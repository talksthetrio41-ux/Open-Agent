"""python -m open_agent  — start the GUI server and (on Termux) a Cloudflare tunnel."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time

from open_agent.config import (
    HOST,
    PORT,
    PROJECT_ROOT,
    ensure_access_pin,
    ensure_dirs,
    is_termux,
    load_env,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("OpenAgent")


BANNER = r"""
   ___                     _                    _
  / _ \ _ __   ___ _ __   / \   __ _  ___ _ __ | |_
 | | | | '_ \ / _ \ '_ \ / _ \ / _` |/ _ \ '_ \| __|
 | |_| | |_) |  __/ | | / ___ \ (_| |  __/ | | | |_
  \___/| .__/ \___|_| |_/_/   \_,_|\___|_| |_|\__|
       |_|
  Free agentic coding on Android / Termux via Qwen Chat
"""


def _print_box(url: str, pin: str, local: str) -> None:
    width = max(len(url), 52)
    line = "═" * (width + 2)
    print()
    print(f"  ╔{line}╗")
    print(f"  ║ {'OPEN AGENT IS READY':<{width}} ║")
    print(f"  ╠{line}╣")
    print(f"  ║ Public GUI : {url:<{width - 14}} ║")
    print(f"  ║ Local      : {local:<{width - 14}} ║")
    print(f"  ║ Unlock PIN : {pin:<{width - 14}} ║")
    print(f"  ╚{line}╝")
    print()
    print("  Open the Public GUI URL on this phone (or any browser).")
    print("  Sign in with your Qwen email/password on first visit.")
    print("  Commands in the GUI:  /compact   /clear   /stop")
    print()


def _port_busy(port: int) -> bool:
    import socket as _s

    for family in (_s.AF_INET, _s.AF_INET6):
        try:
            with _s.socket(family, _s.SOCK_STREAM) as sock:
                # No SO_REUSEADDR: a live LISTEN socket must fail the bind.
                sock.bind(("0.0.0.0" if family == _s.AF_INET else "::", port))
        except OSError:
            return True
    return False


def _listener_pids(port: int) -> list[int]:
    """PIDs holding a LISTEN socket on `port`, via /proc (Termux has no lsof)."""
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table, "r", encoding="utf-8") as fh:
                next(fh, None)
                for line in fh:
                    parts = line.split()
                    if len(parts) > 9 and parts[3] == "0A":  # 0A = LISTEN
                        local = parts[1]
                        if int(local.rsplit(":", 1)[1], 16) == port:
                            inodes.add(parts[9])
        except OSError:
            continue
    if not inodes:
        return []
    me = os.getpid()
    pids: list[int] = []
    for pid_dir in os.listdir("/proc"):
        if not pid_dir.isdigit() or int(pid_dir) == me:
            continue
        fd_dir = f"/proc/{pid_dir}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(f"{fd_dir}/{fd}")
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    pids.append(int(pid_dir))
                    break
        except OSError:
            continue
    return pids


def _cmdline_pids() -> list[int]:
    """PIDs whose cmdline looks like a stale Open Agent / uvicorn server.

    Fallback for Android 10+, where SELinux blocks reading /proc/net/tcp so
    _listener_pids() sees nothing.
    """
    me = os.getpid()
    pids: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids
    for pid_dir in entries:
        if not pid_dir.isdigit() or int(pid_dir) == me:
            continue
        try:
            with open(f"/proc/{pid_dir}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "open_agent" in cmd or "uvicorn" in cmd:
            pids.append(int(pid_dir))
    return pids


def _free_port(port: int) -> bool:
    """Kill any stale Open Agent server still bound to `port`. Returns True when free."""
    if not _port_busy(port):
        return True
    pids = _listener_pids(port)
    if not pids:
        # /proc/net/tcp is unreadable on Android 10+; fall back to cmdline scan.
        pids = _cmdline_pids()
    if not pids:
        return False
    print(f"[!] Port {port} is held by stale process(es) {pids} — terminating")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline:
        if not _port_busy(port):
            return True
        time.sleep(0.3)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.5)
    return not _port_busy(port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open Agent — Termux GUI")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-tunnel", action="store_true", help="Do not start cloudflared")
    parser.add_argument("--tunnel", action="store_true", help="Force Cloudflare tunnel even off-Termux")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    os.chdir(str(PROJECT_ROOT))
    load_env()
    ensure_dirs()
    pin = ensure_access_pin()

    print(BANNER)

    # Print the running commit so crash screenshots show whether the
    # device is on stale code.
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if commit:
            print(f"[*] Open Agent commit: {commit}")
    except Exception:
        pass

    if is_termux():
        # Keep the radio up while the tunnel is live
        for lock_cmd in (("termux-wake-lock",),):
            try:
                import shutil as _sh

                if _sh.which(lock_cmd[0]):
                    subprocess.Popen(lock_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print("[*] termux-wake-lock acquired")
                    break
            except Exception:
                pass

    # A previous `oa` instance may still hold the port (Errno 98) — and the
    # tunnel would then serve the STALE server. Reclaim the port first.
    if not _free_port(args.port):
        print(f"[x] Port {args.port} is busy and could not be reclaimed.")
        print(f"    Run:  kill $(ps aux | grep 'open_agent' | grep -v grep | awk '{{print $2}}')")
        print(f"    or start on another port:  ./oa --port {args.port + 1}")
        return 1

    use_tunnel = args.tunnel or (is_termux() and not args.no_tunnel)
    tunnel = None
    public_url = ""

    if use_tunnel:
        try:
            from open_agent.tunnel import CloudflareTunnel

            print("[*] Starting Cloudflare quick tunnel…")
            tunnel = CloudflareTunnel(args.port)
            # Start tunnel in a thread after uvicorn binds — cloudflared retries
            def _later() -> None:
                time.sleep(1.6)
                try:
                    url = tunnel.start()
                    _print_box(url, pin, f"http://127.0.0.1:{args.port}")
                except Exception as exc:
                    logger.error("Tunnel failed: %s", exc)
                    print(f"\n[!] Tunnel failed: {exc}")
                    print(f"[!] Use local URL instead: http://127.0.0.1:{args.port}")
                    print(f"[!] Unlock PIN: {pin}\n")

            threading.Thread(target=_later, daemon=True).start()
        except Exception as exc:
            logger.warning("Could not start tunnel: %s", exc)
            use_tunnel = False

    if not use_tunnel:
        _print_box(f"http://127.0.0.1:{args.port}", pin, f"http://{args.host}:{args.port}")

    def _shutdown(signum, _frame):
        print("\n[*] Shutting down…")
        if tunnel:
            tunnel.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    import uvicorn

    uvicorn.run(
        "open_agent.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    if tunnel:
        tunnel.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
