"""Regression tests for the Termux login failure ("All connection attempts failed").

Root causes addressed:
1. Chromium stderr went to a PIPE nobody drained -> the browser blocked once
   the pipe buffer filled, and launch/login died with a bare httpx
   ConnectError ("All connection attempts failed").
2. --single-process was tried first on Android; it often crashes while
   navigating heavy SPAs like chat.qwen.ai.
3. Dead Chromium processes were cached forever; nothing relaunched them.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CDP = (ROOT / "open_agent" / "cdp.py").read_text(encoding="utf-8")
QB = (ROOT / "open_agent" / "qwen_browser.py").read_text(encoding="utf-8")


def test_chromium_stderr_goes_to_log_file_not_pipe():
    assert "stderr=subprocess.PIPE" not in CDP
    assert "chromium.log" in CDP
    assert "stderr=subprocess.STDOUT" in CDP


def test_dead_chromium_error_has_context():
    assert "_dead_error" in CDP
    assert "Chromium is not reachable" in CDP


def test_http_helpers_wrap_connection_errors():
    # /json/list and /json/new must raise CdpError with context, not bare
    # httpx.ConnectError ("All connection attempts failed").
    assert CDP.count("self._dead_error(exc)") >= 2


def test_android_strategy_matrix():
    """Both env variants must be tried: keeping LD_PRELOAD (wrapper exec
    fails with EACCES on some devices when stripped) and stripping it
    (crashpad/zygote re-exec fails with 'CANNOT LINK EXECUTABLE' when kept).
    Single-process+in-process-gpu first to avoid the re-exec entirely."""
    assert "def _attempts" in CDP
    assert '"--headless=new", "--single-process", "--no-zygote", "--in-process-gpu"' in CDP
    assert CDP.count('"strip": False') >= 2
    assert CDP.count('"strip": True') >= 1
    assert "_direct_binary" in CDP


def test_launch_failure_includes_diagnostics():
    assert "def _diagnostics" in CDP
    assert "Diagnostics:" in CDP
    assert "pkg reinstall chromium" in CDP


def test_login_retries_after_relaunch():
    assert "_relaunch" in QB
    login_body = QB.split("async def login", 1)[1].split("async def ", 1)[0]
    assert "for attempt in (1, 2)" in login_body


def test_start_detects_dead_chromium():
    start_body = QB.split("async def start", 1)[1].split("async def ", 1)[0]
    assert "is_alive()" in start_body


def test_ensure_page_retries():
    body = QB.split("async def _ensure_page", 1)[1].split("async def ", 1)[0]
    assert "for attempt in (1, 2)" in body


def test_ld_preload_stripped_on_android():
    """Termux's LD_PRELOAD=libtermux-exec.so breaks Chromium's re-exec of
    /proc/self/exe ('CANNOT LINK EXECUTABLE ... not accessible for the
    namespace'). Stripping must be available as a launch strategy."""
    assert 'env.pop("LD_PRELOAD", None)' in CDP
    assert "strip_ldpreload" in CDP


def test_installer_restores_chromium_exec_bit():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "lost its exec bit" in install
    assert 'chmod 755 "$real_bin"' in install


def test_crashpad_noise_disabled():
    assert "--disable-crash-reporter" in CDP
    assert "--disable-breakpad" in CDP
