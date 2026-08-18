"""Back-compat CLI login. The GUI collects Qwen credentials on first visit."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from open_agent.config import ensure_dirs, load_env
from open_agent.qwen_browser import QwenBrowserAutomator


async def main() -> int:
    load_env()
    ensure_dirs()
    parser = argparse.ArgumentParser(description="Sign in to chat.qwen.ai and save the session")
    parser.add_argument("--username", "-u")
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()

    user = args.username or input("Qwen email: ").strip()
    password = getpass.getpass("Qwen password: ")
    if not user or not password:
        print("Email and password required.")
        return 1

    automator = QwenBrowserAutomator(headless=not args.headful)
    try:
        result = await automator.login(user, password)
    finally:
        await automator.close()

    if result.get("ok") == "true":
        print("Login saved. Launch the GUI with: python -m open_agent")
        return 0
    print("Login did not finish. Try --headful if a captcha appeared.")
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
