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
