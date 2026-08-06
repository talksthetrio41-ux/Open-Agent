import argparse
import asyncio
import getpass
import os
import sys
from typing import Dict, Any, Optional
from dotenv import set_key
from playwright.async_api import async_playwright

ENV_PATH = "/kaggle/working/workspace/.env"
USER_DATA_DIR = "/kaggle/working/workspace/qwen_browser_data"

class QwenAuthenticator:
    def __init__(self, headful: bool = False):
        self.headful = headful
        self.captured_token: Optional[str] = None
        self.captured_cookies: Dict[str, str] = {}

    async def login_with_credentials(self, username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        """
        Launches Playwright, inputs credentials into Qwen login page, submits form,
        waits for full authentication redirect, and persists session cookies into qwen_browser_data.
        """
        async with async_playwright() as p:
            print("\n[1/4] Launching Playwright browser...")
            browser_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]

            context = await p.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                headless=not self.headful,
                args=browser_args,
                viewport={"width": 1280, "height": 800}
            )

            page = await context.new_page()

            # Network listener to capture Bearer authorization header automatically
            def on_request(request):
                auth = request.headers.get("authorization")
                if auth and auth != "Bearer null" and not self.captured_token:
                    self.captured_token = auth
                    print(f"\n[+] Captured Authorization Token from network intercept!")

            page.on("request", on_request)

            print("[2/4] Navigating to https://chat.qwen.ai/auth ...")
            await page.goto("https://chat.qwen.ai/auth", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2)

            if username and password:
                print(f"[3/4] Submitting credentials for: {username} ...")

                email_input = await page.wait_for_selector(
                    "input[placeholder='Enter Your Email'], input[placeholder*='Email' i], input[type='email']",
                    timeout=10000
                )
                pwd_input = await page.wait_for_selector(
                    "input[placeholder='Enter Your Password'], input[placeholder*='Password' i], input[type='password']",
                    timeout=10000
                )

                if email_input and pwd_input:
                    await email_input.fill(username)
                    await asyncio.sleep(0.3)
                    await pwd_input.fill(password)
                    await asyncio.sleep(0.3)

                    signin_btn = await page.query_selector("button:has-text('Sign in'), button[type='submit']")
                    if signin_btn:
                        await signin_btn.click()
                        print("Clicked 'Sign in' button. Waiting for login & session redirect...")
                    else:
                        await pwd_input.press("Enter")
                        print("Pressed Enter to submit. Waiting for login & session redirect...")

                    # Wait for redirect from /auth to main chat app
                    try:
                        await page.wait_for_url("https://chat.qwen.ai/", timeout=15000)
                        print("[+] Successfully redirected to main chat page!")
                    except Exception:
                        await asyncio.sleep(5)

            print("\n[4/4] Finalizing session state & token capture...")
            for _ in range(10):
                await asyncio.sleep(1)
                try:
                    ls_token = await page.evaluate(
                        "() => localStorage.getItem('token') || localStorage.getItem('access_token') || localStorage.getItem('user_token')"
                    )
                    if ls_token:
                        self.captured_token = ls_token
                        break
                except Exception:
                    pass
                if self.captured_token:
                    break

            cookies = await context.cookies()
            self.captured_cookies = {c["name"]: c["value"] for c in cookies}
            cookie_header = "; ".join([f"{k}={v}" for k, v in self.captured_cookies.items()])

            await context.close()

            return {
                "token": self.captured_token,
                "cookies_header": cookie_header,
                "cookies_dict": self.captured_cookies
            }


def update_env_file(token: str, cookie_header: str, username: Optional[str] = None, password: Optional[str] = None):
    """Writes token, cookies, and credentials into .env file."""
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w") as f:
            f.write("# Qwen Web Chat Credentials\n")

    if token:
        set_key(ENV_PATH, "QWEN_TOKEN", token)
    if cookie_header:
        set_key(ENV_PATH, "QWEN_COOKIE", cookie_header)
    if username:
        set_key(ENV_PATH, "QWEN_USERNAME", username)
    if password:
        set_key(ENV_PATH, "QWEN_PASSWORD", password)

    print(f"\n[+] Automatically updated credentials in {ENV_PATH}")


async def main():
    parser = argparse.ArgumentParser(description="Qwen Web Chat Terminal Login Helper")
    parser.add_argument("--username", "-u", type=str, help="Qwen account email/username")
    parser.add_argument("--headful", action="store_true", help="Launch visible browser window for interactive login")
    args = parser.parse_args()

    print("=" * 65)
    print("        Qwen Studio / Web Chat Terminal Login Helper")
    print("=" * 65)

    username = args.username
    password = None

    if not username:
        username = input("Enter your Qwen Account Email: ").strip()

    if username:
        password = getpass.getpass("Enter your Qwen Password: ").strip()

    if not username or not password:
        print("[Error]: Email and Password are required to log in.")
        sys.exit(1)

    auth = QwenAuthenticator(headful=args.headful)
    res = await auth.login_with_credentials(username=username, password=password)

    token = res.get("token")
    cookie = res.get("cookies_header")

    print("\n" + "=" * 65)
    if token:
        print("LOGIN SUCCESSFUL!")
        print("=" * 65)
        print(f"\nYour Session Token:\n{token}\n")
        if cookie:
            print(f"Cookie Header:\n{cookie[:100]}...\n")
        update_env_file(token, cookie, username, password)
    else:
        print("COULD NOT CAPTURE TOKEN AUTOMATICALLY")
        print("=" * 65)
        print("Tip: If Qwen required a Captcha or 2FA OTP code, run with --headful flag:")
        print("  python login.py --headful")


if __name__ == "__main__":
    asyncio.run(main())
