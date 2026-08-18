"""CDP / Termux backend tests — no live Chromium required."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import open_agent.config as config
from open_agent.cdp import _HELPER_JS, ANDROID_CHROME_ARGS, is_android


ROOT = Path(__file__).resolve().parents[1]


def test_qwen_browser_has_no_toplevel_playwright_import():
    src = (ROOT / "open_agent" / "qwen_browser.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Only module-level statements — Playwright is allowed inside _start_playwright.
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("playwright"), alias.name
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("playwright"), node.module
    before_lazy, _, after_lazy = src.partition("async def _start_playwright")
    assert "from playwright" not in before_lazy
    assert "import playwright" not in before_lazy
    assert "from playwright.async_api" in after_lazy


def test_qwen_browser_imports_without_playwright():
    import open_agent.qwen_browser as qb

    importlib.reload(qb)
    assert hasattr(qb.QwenBrowserAutomator, "_start_cdp")
    assert hasattr(qb.QwenBrowserAutomator, "_start_playwright")
    automator = qb.QwenBrowserAutomator()
    assert automator._context is None


def test_prefer_cdp_forced(monkeypatch):
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setenv("OPEN_AGENT_BROWSER", "cdp")
    assert config.prefer_cdp() is True
    monkeypatch.setenv("OPEN_AGENT_BROWSER", "devtools")
    assert config.prefer_cdp() is True
    monkeypatch.setenv("OPEN_AGENT_BROWSER", "playwright")
    assert config.prefer_cdp() is False


def test_prefer_cdp_on_android(monkeypatch):
    monkeypatch.delenv("OPEN_AGENT_BROWSER", raising=False)
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    assert config.is_android() is True
    assert config.prefer_cdp() is True
    assert is_android() is True


def test_prefer_cdp_without_playwright(monkeypatch):
    monkeypatch.delenv("OPEN_AGENT_BROWSER", raising=False)
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.delenv("PREFIX", raising=False)
    monkeypatch.setattr(config, "is_android", lambda: False)
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("simulated missing playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert config.prefer_cdp() is True


def test_helper_js_supports_playwright_selectors():
    assert ":has-text" in _HELPER_JS
    assert "__oaQueryAll" in _HELPER_JS
    assert "ciAttrs" in _HELPER_JS
    assert "__oaVisible" in _HELPER_JS
    # Sanity: the helper is valid JS (Function constructor).
    assert "window.__oaQueryAll" in _HELPER_JS
    assert "querySelectorAll" in _HELPER_JS


def test_helper_js_parses_in_node():
    """If node is installed, compile the injected helper so syntax errors fail CI."""
    import os
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        return

    script = "new Function(process.env.HELPER); console.log('SYNTAX_OK');\n"
    env = os.environ.copy()
    env["HELPER"] = _HELPER_JS
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    result = subprocess.run(
        [node, path], capture_output=True, text=True, env=env, timeout=20
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "SYNTAX_OK" in result.stdout


def test_android_chrome_flags_include_single_process():
    joined = " ".join(ANDROID_CHROME_ARGS)
    assert "--remote-allow-origins=*" in joined
    assert "--renderer-process-limit=1" in joined


def test_install_sh_never_pips_playwright_on_termux():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "requirements-termux.txt" in text
    assert "OPEN_AGENT_BROWSER" in text
    assert "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD" in text
    assert "x11-repo" in text
    assert "tur-repo" in text
    assert "pip install -r requirements-termux.txt" in text.replace('"$REQ_FILE"', "requirements-termux.txt") or (
        'REQ_FILE="requirements-termux.txt"' in text and "pip install -r \"$REQ_FILE\"" in text
    )
    # The Termux Python-deps branch must never install the desktop file.
    start = text.find("# CRITICAL: never pip-install playwright")
    assert start != -1
    termux_pip = text[start : text.find("else", start)]
    assert "requirements.txt" not in termux_pip
    assert "playwright install" not in termux_pip


def test_termux_requirements_exclude_playwright():
    lines = (ROOT / "requirements-termux.txt").read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("#") or not stripped:
            continue
        assert "playwright" not in stripped
    text = "\n".join(lines)
    assert "websockets" in text
    desktop = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "playwright" in desktop


def test_chromium_search_includes_termux_lib_path():
    src = inspect.getsource(config.find_chromium)
    assert "lib/chromium/chromium" in src
