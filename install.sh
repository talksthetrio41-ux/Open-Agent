#!/data/data/com.termux/files/usr/bin/bash
# Open Agent — one-shot Termux installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/talksthetrio41-ux/Open-Agent/main/install.sh | bash
# or, from a clone:
#   bash install.sh

set -euo pipefail

REPO_URL="${OPEN_AGENT_REPO:-https://github.com/talksthetrio41-ux/Open-Agent.git}"
INSTALL_DIR="${OPEN_AGENT_HOME:-$HOME/open-agent}"
BRANCH="${OPEN_AGENT_BRANCH:-main}"

c_info() { printf '\n\033[1;36m[*]\033[0m %s\n' "$*"; }
c_ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
c_warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
c_err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*"; }

is_termux() {
  [ -n "${TERMUX_VERSION:-}" ] || [ -d /data/data/com.termux/files/usr ]
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

c_info "Open Agent installer"
echo "    target : $INSTALL_DIR"
echo "    source : $REPO_URL"

if is_termux; then
  c_info "Termux detected — installing packages (pkg)"
  # Prevent interactive prompts
  export DEBIAN_FRONTEND=noninteractive
  pkg update -y || true
  pkg install -y git python python-pip clang make pkg-config \
    libffi openssl rust binutils which curl wget \
    chromium cloudflared termux-api 2>/dev/null \
    || pkg install -y git python python-pip clang make pkg-config \
         libffi openssl which curl wget

  if ! need_cmd cloudflared; then
    c_warn "cloudflared missing — enabling tur-repo and retrying"
    pkg install -y tur-repo || true
    pkg install -y cloudflared || c_warn "Install cloudflared later: pkg install cloudflared"
  fi

  if ! need_cmd chromium && ! need_cmd chromium-browser; then
    c_warn "Chromium missing — trying x11-repo"
    pkg install -y x11-repo || true
    pkg install -y chromium || c_warn "Install chromium later: pkg install chromium"
  fi
else
  c_warn "Not running inside Termux. Will install Python deps only."
  if ! need_cmd python3 && ! need_cmd python; then
    c_err "python3 is required"
    exit 1
  fi
  if ! need_cmd git; then
    c_err "git is required"
    exit 1
  fi
fi

PYTHON_BIN="$(command -v python3 || command -v python)"

if [ -d "$INSTALL_DIR/.git" ]; then
  c_info "Updating existing clone"
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH" || true
  git -C "$INSTALL_DIR" checkout "$BRANCH" || true
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
else
  c_info "Cloning Open Agent"
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

c_info "Python virtualenv + dependencies"
"$PYTHON_BIN" -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --upgrade pip wheel setuptools
pip install -r requirements.txt

# Playwright's bundled Chromium is huge on Android and often fails.
# Prefer the system Chromium we just installed; still try Playwright browsers on desktop.
if is_termux; then
  export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
  CHROME_PATH="$(command -v chromium-browser || command -v chromium || true)"
  if [ -n "${CHROME_PATH:-}" ]; then
    echo "CHROMIUM_PATH=$CHROME_PATH" >> .env.partial
    echo "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1" >> .env.partial
    c_ok "Using system Chromium: $CHROME_PATH"
  fi
else
  c_info "Installing Playwright Chromium (desktop)"
  python -m playwright install chromium || c_warn "playwright install chromium failed — set CHROMIUM_PATH"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env || true
fi
if [ -f .env.partial ]; then
  # merge chromium path if not already set
  if ! grep -q '^CHROMIUM_PATH=' .env 2>/dev/null; then
    cat .env.partial >> .env
  fi
  rm -f .env.partial
fi

# Convenience launcher
cat > "$INSTALL_DIR/oa" << 'LAUNCH'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi
exec python -m open_agent "$@"
LAUNCH
chmod +x "$INSTALL_DIR/oa"

# Termux widget / home shortcut helper
if is_termux && [ -d "$HOME/.shortcuts" ]; then
  cat > "$HOME/.shortcuts/Open-Agent" << EOF
#!/data/data/com.termux/files/usr/bin/bash
cd "$INSTALL_DIR" && exec ./oa
EOF
  chmod +x "$HOME/.shortcuts/Open-Agent" || true
fi

c_ok "Install complete"
echo
echo "  Launch with:"
echo "      $INSTALL_DIR/oa"
echo
echo "  The GUI URL and unlock PIN will print here after the tunnel starts."
echo

# Auto-start unless SKIP_START=1
if [ "${SKIP_START:-0}" != "1" ]; then
  exec "$INSTALL_DIR/oa"
fi
