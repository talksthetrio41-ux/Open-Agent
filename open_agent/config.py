"""Runtime paths, environment, and Termux-aware settings."""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, set_key

# Project root = parent of the open_agent package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BROWSER_DATA_DIR = PROJECT_ROOT / "qwen_browser_data"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
ENV_PATH = PROJECT_ROOT / ".env"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8765"))

DEFAULT_COMMAND_TIMEOUT = 180
MAX_OUTPUT_CHARS = 8000
MAX_AGENT_STEPS = 20


def is_termux() -> bool:
    if os.environ.get("TERMUX_VERSION"):
        return True
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    return Path("/data/data/com.termux").exists()


def ensure_dirs() -> None:
    for path in (DATA_DIR, BROWSER_DATA_DIR, WORKSPACE_DIR, RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_env() -> None:
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=str(ENV_PATH), override=True)
    else:
        example = PROJECT_ROOT / ".env.example"
        if example.exists():
            shutil.copy(example, ENV_PATH)
            load_dotenv(dotenv_path=str(ENV_PATH), override=True)


def write_env(key: str, value: str) -> None:
    if not ENV_PATH.exists():
        ENV_PATH.write_text("# Open Agent\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass
    set_key(str(ENV_PATH), key, value or "")
    os.environ[key] = value or ""


def get_env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def find_chromium() -> Optional[str]:
    """Prefer an explicit path, then Termux/system Chromium, else Playwright's bundled one."""
    explicit = get_env("CHROMIUM_PATH")
    if explicit and Path(explicit).exists():
        return explicit

    candidates = [
        "chromium-browser",
        "chromium",
        "google-chrome",
        "google-chrome-stable",
        "chrome",
    ]
    termux_prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    extra_paths = [
        f"{termux_prefix}/bin/chromium-browser",
        f"{termux_prefix}/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in extra_paths:
        if Path(path).exists():
            return path
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_cloudflared() -> Optional[str]:
    explicit = get_env("CLOUDFLARED_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("cloudflared")
    if found:
        return found
    termux_prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    for path in (
        f"{termux_prefix}/bin/cloudflared",
        str(PROJECT_ROOT / "bin" / "cloudflared"),
        "/usr/local/bin/cloudflared",
        "/usr/bin/cloudflared",
    ):
        if Path(path).exists():
            return path
    return None


def ensure_access_pin() -> str:
    pin = get_env("OPEN_AGENT_PIN")
    if pin:
        return pin
    pin = f"{secrets.randbelow(10000):04d}"
    write_env("OPEN_AGENT_PIN", pin)
    return pin


def masked(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "…" + ("*" * 4)
