#!/usr/bin/env bash
# Open Agent — one-shot Termux installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/talksthetrio41-ux/Open-Agent/main/install.sh | bash
# or, from a clone:
#   bash install.sh
#
# Playwright has NO wheels for Android (aarch64-linux-android). This
# script must never `pip install playwright` inside Termux.
#
# pydantic 2.x has NO Android wheel either. pip then downloads
# pydantic_core-*.tar.gz and hangs at "Installing build dependencies"
# (needs Rust/maturin). Always install Termux deps as wheels only.

set -euo pipefail

# Re-exec under bash only when this is a real file (not `curl | sh`).
if [ -z "${BASH_VERSION:-}" ] && command -v bash >/dev/null 2>&1 && [ -f "$0" ]; then
  exec bash "$0" "$@"
fi

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

merge_env_kv() {
  # merge_env_kv KEY VALUE  — upsert into .env without duplicating
  local key="$1"
  local value="$2"
  local env_file="${3:-.env}"
  touch "$env_file"
  if grep -q "^${key}=" "$env_file" 2>/dev/null; then
    # portable in-place replace
    local tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" 'BEGIN{done=0} $0 ~ "^"k"=" {print k"="v; done=1; next} {print} END{if(!done) print k"="v}' \
      "$env_file" > "$tmp"
    mv "$tmp" "$env_file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

c_info "Open Agent installer"
echo "    target : $INSTALL_DIR"
echo "    source : $REPO_URL"

if is_termux; then
  c_info "Termux detected — installing packages (pkg)"
  export DEBIAN_FRONTEND=noninteractive
  pkg update -y || true

  # Core tools first. Do NOT install rust just to compile Playwright /
  # pydantic-core — neither can be built on a phone in any useful way.
  # python-ensurepip-wheels makes `python -m venv` ship pip.
  pkg install -y git python python-pip python-ensurepip-wheels \
    which curl wget libffi openssl termux-api || \
    pkg install -y git python python-pip which curl wget

  # Chromium lives in x11-repo. Enable it *before* asking for chromium.
  if ! need_cmd chromium && ! need_cmd chromium-browser; then
    c_info "Enabling x11-repo for Chromium"
    pkg install -y x11-repo || true
    pkg install -y chromium || c_warn "Install chromium later: pkg install x11-repo && pkg install chromium"
  fi

  if ! need_cmd cloudflared; then
    c_warn "cloudflared missing — enabling tur-repo and retrying"
    pkg install -y tur-repo || true
    pkg install -y cloudflared || c_warn "Install cloudflared later: pkg install tur-repo && pkg install cloudflared"
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

fresh_clone() {
  c_info "Cloning Open Agent (fresh)"
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
}

if [ -d "$INSTALL_DIR/.git" ]; then
  c_info "Updating existing clone"
  # Do NOT `|| true` this away: a failed/ff-only pull on a shallow clone
  # silently leaves STALE code in place (that is how the old top-level
  # `import playwright` crash survived on devices). Hard-reset to the
  # fetched branch instead; .env / qwen_browser_data are untracked and
  # survive the reset. If anything fails, wipe and re-clone.
  if git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH" && \
     git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"; then
    c_ok "Code updated to latest $BRANCH"
  else
    c_warn "git update failed — forcing a fresh clone"
    fresh_clone
  fi
else
  fresh_clone
fi

cd "$INSTALL_DIR"

# Self-heal guard: the checked-out code must not import Playwright at
# module top level (crash on Termux, where Playwright can never exist).
if [ -f open_agent/qwen_browser.py ] && \
   grep -qE "^(from|import)[[:space:]]+playwright" open_agent/qwen_browser.py; then
  c_warn "Stale code detected (top-level playwright import) — forcing fresh clone"
  fresh_clone
  cd "$INSTALL_DIR"
  if grep -qE "^(from|import)[[:space:]]+playwright" open_agent/qwen_browser.py; then
    c_err "Repository still ships a top-level playwright import. Report this bug."
    exit 1
  fi
fi
c_ok "Running commit: $(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"

c_info "Python virtualenv + dependencies"
# Isolated venv. Do NOT use --system-site-packages: Termux's system
# pydantic/httpx (when present) can fight the pinned wheels below.
# Drop a leftover venv from a previous hung pydantic-core compile.
if is_termux && [ -d .venv ]; then
  c_info "Recreating virtualenv (clears any half-built pydantic-core)"
  rm -rf .venv
fi
if ! "$PYTHON_BIN" -m venv .venv; then
  c_warn "python -m venv failed — retrying without bundled pip"
  "$PYTHON_BIN" -m venv --without-pip .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
if ! command -v pip >/dev/null 2>&1; then
  c_warn "venv has no pip — bootstrapping with ensurepip"
  python -m ensurepip --upgrade --default-pip || true
fi
if ! command -v pip >/dev/null 2>&1; then
  c_err "pip is not available inside the venv. Install python-pip and retry."
  exit 1
fi
# Keep pip itself on a wheel. setuptools/wheel are not needed on Termux
# because we never compile anything (see --only-binary below).
if is_termux; then
  pip install --upgrade --only-binary=:all: pip || pip install --upgrade pip
else
  pip install --upgrade pip
fi

if is_termux; then
  # CRITICAL: never pip-install playwright on Android.
  # PyPI has no aarch64-linux-android wheel (from versions: none).
  #
  # CRITICAL: never let pip compile pydantic-core / maturin. pydantic 2.x
  # has no Android wheel; pip downloads pydantic_core-*.tar.gz and hangs
  # at "Installing build dependencies" (needs a Rust toolchain that
  # Termux phones cannot realistically build). requirements-termux.txt
  # pins pydantic 1.x + FastAPI <0.126, both py3-none-any wheels.
  export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
  export PIP_ONLY_BINARY=":all:"
  REQ_FILE="requirements-termux.txt"
  PIP_TERMUX=(pip install --only-binary=:all: --prefer-binary --no-compile)
  if [ ! -f "$REQ_FILE" ]; then
    c_warn "$REQ_FILE missing — installing a Playwright-free, no-compile subset"
    "${PIP_TERMUX[@]}" \
      'httpx>=0.27.0,<1.0.0' \
      'fastapi>=0.110.0,<0.126.0' \
      'uvicorn>=0.28.0,<0.35.0' \
      'pydantic>=1.10.13,<2.0.0' \
      'sse-starlette>=2.0.0,<2.3.0' \
      'python-dotenv>=1.0.0,<2.0.0' \
      'aiofiles>=23.2.1,<25.0.0' \
      'websockets>=12.0,<16.0.0' \
      'pytest>=8.0.0,<10.0.0'
  else
    c_info "Installing Termux wheels only (never compile pydantic-core)"
    "${PIP_TERMUX[@]}" -r "$REQ_FILE"
  fi
  # Prove we did not pull the Rust extension. Fail loud if we did.
  if python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pydantic_core') is None else 1)"; then
    c_ok "pydantic is the pure-Python 1.x wheel (no pydantic-core / Rust)"
  else
    c_err "pydantic-core got installed — Termux cannot compile that. Re-run with a fresh venv."
    exit 1
  fi

  CHROME_PATH="$(command -v chromium-browser || command -v chromium || true)"
  PREFIX_DIR="${PREFIX:-/data/data/com.termux/files/usr}"
  if [ -z "${CHROME_PATH:-}" ]; then
    for candidate in \
      "$PREFIX_DIR/bin/chromium-browser" \
      "$PREFIX_DIR/bin/chromium" \
      "$PREFIX_DIR/lib/chromium/chromium"; do
      if [ -x "$candidate" ]; then
        CHROME_PATH="$candidate"
        break
      fi
    done
  fi
  # A partial pkg upgrade can leave the real binary without its exec bit
  # (wrapper then dies with exit 126 'Permission denied'). Self-heal.
  for real_bin in "$PREFIX_DIR/lib/chromium/chrome" "$PREFIX_DIR/lib/chromium/chromium"; do
    if [ -f "$real_bin" ] && [ ! -x "$real_bin" ]; then
      c_warn "$real_bin lost its exec bit — restoring"
      chmod 755 "$real_bin" || c_warn "chmod failed — run: pkg reinstall chromium"
    fi
  done
  if [ -n "${CHROME_PATH:-}" ]; then
    c_ok "Using system Chromium: $CHROME_PATH"
  else
    c_warn "Chromium binary not found. GUI login will fail until: pkg install x11-repo && pkg install chromium"
  fi
else
  c_info "Installing desktop Python deps (includes Playwright)"
  pip install -r requirements.txt
  c_info "Installing Playwright Chromium (desktop)"
  python -m playwright install chromium || c_warn "playwright install chromium failed — set CHROMIUM_PATH"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env || true
fi

if is_termux; then
  merge_env_kv PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD 1 .env
  merge_env_kv OPEN_AGENT_BROWSER cdp .env
  if [ -n "${CHROME_PATH:-}" ]; then
    merge_env_kv CHROMIUM_PATH "$CHROME_PATH" .env
  fi
fi
chmod 600 .env || true

# Convenience launcher
cat > "$INSTALL_DIR/oa" << 'LAUNCH'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# Best-effort self-update so bug fixes actually reach the device.
# Skip with OA_NO_UPDATE=1 (offline / hacked locally).
if [ -z "${OA_NO_UPDATE:-}" ] && [ -d .git ] && command -v git >/dev/null 2>&1; then
  BRANCH="${OPEN_AGENT_BRANCH:-main}"
  if git fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1 && \
     git reset --hard "origin/$BRANCH" >/dev/null 2>&1; then
    echo "[*] Code is up to date ($(git rev-parse --short HEAD 2>/dev/null))"
  else
    echo "[!] Update check failed (offline?) — running local copy"
  fi
fi
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi
# Prefer python from the venv; fall back to python3
if command -v python >/dev/null 2>&1; then
  exec python -m open_agent "$@"
fi
exec python3 -m open_agent "$@"
LAUNCH
chmod +x "$INSTALL_DIR/oa"

# Global `oa` command so it works from any directory, not just $INSTALL_DIR
if is_termux && [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ]; then
  cat > "$PREFIX/bin/oa" << EOF
#!$PREFIX/bin/bash
exec "$INSTALL_DIR/oa" "\$@"
EOF
  chmod +x "$PREFIX/bin/oa" || true
  c_ok "Global command installed: oa (works from any directory)"
fi

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
if is_termux; then
  echo "  Android uses system Chromium over CDP (Playwright is not installed)."
  echo
fi

# Auto-start unless SKIP_START=1
if [ "${SKIP_START:-0}" != "1" ]; then
  exec "$INSTALL_DIR/oa"
fi
