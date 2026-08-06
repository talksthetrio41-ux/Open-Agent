import asyncio
import json
import logging
import os
from typing import AsyncGenerator, Dict, Any, Optional
from playwright.async_api import async_playwright, Page, BrowserContext, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QwenBrowser")

class QwenBrowserAutomator:
    """
    Playwright browser automation driver for chat.qwen.ai.
    Features:
    - Automatic persistent session state management.
    - Automated auto-login if session cookies expire using QWEN_USERNAME & QWEN_PASSWORD.
    - Real-time DOM streaming.
    """

    def __init__(self, headless: bool = True, user_data_dir: Optional[str] = "./qwen_browser_data", token: Optional[str] = None):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.token = token or os.getenv("QWEN_TOKEN", "")
        self.cookie_str = os.getenv("QWEN_COOKIE", "")
        self.username = os.getenv("QWEN_USERNAME", "")
        self.password = os.getenv("QWEN_PASSWORD", "")
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self.captured_token: Optional[str] = self.token
        self._page: Optional[Page] = None

    def _parse_cookie_string(self, cookie_str: str) -> list:
        cookies = []
        for item in cookie_str.split(";"):
            if "=" in item:
                k, v = item.split("=", 1)
                cookies.append({
                    "name": k.strip(),
                    "value": v.strip(),
                    "domain": ".qwen.ai",
                    "path": "/"
                })
        return cookies

    async def start(self):
        """Launches Playwright Chromium browser instance with cookie injection fallback."""
        if self._playwright:
            return

        self._playwright = await async_playwright().start()
        browser_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"
        ]

        try:
            if self.user_data_dir and os.path.exists(self.user_data_dir):
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=self.headless,
                    args=browser_args,
                    viewport={"width": 1280, "height": 800},
                    timeout=5000
                )
            else:
                browser = await self._playwright.chromium.launch(headless=self.headless, args=browser_args)
                self._context = await browser.new_context(viewport={"width": 1280, "height": 800})
        except Exception as e:
            logger.warning(f"Persistent context locked or failed ({e}). Launching fresh ephemeral context...")
            browser = await self._playwright.chromium.launch(headless=self.headless, args=browser_args)
            self._context = await browser.new_context(viewport={"width": 1280, "height": 800})

        if self.cookie_str:
            try:
                parsed_cookies = self._parse_cookie_string(self.cookie_str)
                if parsed_cookies:
                    await self._context.add_cookies(parsed_cookies)
            except Exception as e:
                logger.warning(f"Failed setting context cookies: {e}")

        self._context.on("request", self._handle_request)
        logger.info("Playwright Chromium browser session initialized.")

    def _handle_request(self, request):
        headers = request.headers
        if "authorization" in headers and not self.captured_token:
            auth = headers["authorization"]
            if auth and auth != "Bearer null":
                self.captured_token = auth
                logger.info(f"Captured Authorization Bearer Token: {auth[:25]}...")

    async def _ensure_logged_in(self, page: Page):
        """Checks if logged out and automatically fills login form if credentials exist."""
        login_btn = await page.query_selector("button:has-text('Log in'), button.header-right-auth-button")
        if login_btn and self.username and self.password:
            logger.info(f"Unauthenticated state detected. Auto-logging in for {self.username}...")
            await page.goto("https://chat.qwen.ai/auth", wait_until="domcontentloaded")
            await asyncio.sleep(2)

            email_input = await page.wait_for_selector("input[placeholder='Enter Your Email'], input[type='email']", timeout=10000)
            pwd_input = await page.wait_for_selector("input[placeholder='Enter Your Password'], input[type='password']", timeout=10000)

            if email_input and pwd_input:
                await email_input.fill(self.username)
                await asyncio.sleep(0.3)
                await pwd_input.fill(self.password)
                await asyncio.sleep(0.3)

                signin_btn = await page.query_selector("button:has-text('Sign in'), button[type='submit']")
                if signin_btn:
                    await signin_btn.click()

                try:
                    await page.wait_for_url("https://chat.qwen.ai/", timeout=15000)
                    logger.info("Auto-login successful!")
                except Exception:
                    await asyncio.sleep(4)

    async def reset_chat(self):
        """Closes active page to start a new chat thread on next prompt."""
        if self._page and not self._page.is_closed():
            try:
                await self._page.close()
            except Exception:
                pass
        self._page = None
        logger.info("Chat thread reset. Next prompt will start a new conversation.")

    async def get_session_info(self, url: str = "https://chat.qwen.ai") -> Dict[str, Any]:
        """Navigates to Qwen web interface and extracts session tokens & cookies."""
        await self.start()
        page = await self._context.new_page()
        logger.info(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await self._ensure_logged_in(page)

        cookies = await self._context.cookies()
        self.captured_cookies = {c["name"]: c["value"] for c in cookies}

        try:
            ls_token = await page.evaluate(
                "() => localStorage.getItem('token') || localStorage.getItem('access_token') || localStorage.getItem('user_token')"
            )
            if ls_token and not self.captured_token:
                self.captured_token = ls_token
        except Exception as e:
            logger.warning(f"Could not read localStorage token: {e}")

        await page.close()

        return {
            "token": self.captured_token,
            "cookies": self.captured_cookies,
            "cookies_header": "; ".join([f"{k}={v}" for k, v in self.captured_cookies.items()])
        }

    async def stream_chat_browser(self, prompt: str, reset_session: bool = False, timeout_sec: int = 90) -> AsyncGenerator[str, None]:
        """
        Sends prompt to Qwen Chat via Playwright browser UI in the current thread and streams response tokens live.
        """
        await self.start()
        
        if reset_session:
            await self.reset_chat()

        is_new_thread = False
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            is_new_thread = True
            logger.info("Starting new chat thread (navigating to https://chat.qwen.ai)...")
            await self._page.goto("https://chat.qwen.ai", wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await self._ensure_logged_in(self._page)

            # 1. Dismiss cookie banner FIRST before any wait_for_selector calls
            try:
                cookie_btn = await self._page.query_selector("button:has-text('Accept all cookies'), .index-module__cookie-confirm-btn___T")
                if cookie_btn:
                    await cookie_btn.click()
                    await asyncio.sleep(1)
            except Exception:
                pass
        else:
            logger.info(f"Continuing existing chat thread at {self._page.url}...")

        page = self._page

        try:
            content_selectors = [
                ".qwen-markdown",
                ".markdown-body"
            ]

            # Count existing response elements prior to sending the prompt
            initial_count = 0
            for selector in content_selectors:
                existing = await page.query_selector_all(selector)
                if existing:
                    initial_count = max(initial_count, len(existing))

            # 2. Focus exact message-input-textarea
            input_selector = "textarea.message-input-textarea, textarea[placeholder*='help' i]"
            textarea = await page.wait_for_selector(input_selector, timeout=15000)
            if not textarea:
                yield "[Error: Could not locate chat input textarea in browser]"
                return

            await textarea.focus()
            await textarea.fill(prompt)
            await asyncio.sleep(0.5)

            # 3. Click Send button
            send_btn = None
            try:
                send_btn = await page.wait_for_selector(
                    "button.send-button, button[aria-label='send' i]",
                    state="visible",
                    timeout=5000
                )
            except Exception:
                pass

            if send_btn:
                try:
                    await send_btn.click(force=True)
                    logger.info("Clicked Send button in Playwright browser UI.")
                except Exception:
                    await page.keyboard.press("Enter")
                    logger.info("Fallback: Pressed Enter via keyboard in Playwright browser UI.")
            else:
                await page.keyboard.press("Enter")
                logger.info("Pressed Enter via keyboard in Playwright browser UI.")

            # Wait for post-click navigation/state update
            await asyncio.sleep(1.5)

            last_text = ""
            no_change_counter = 0

            for _ in range(timeout_sec * 2): # poll every 0.5s
                await asyncio.sleep(0.5)
                try:
                    for selector in content_selectors:
                        elements = await page.query_selector_all(selector)
                        if elements and len(elements) > initial_count:
                            latest_element = elements[-1]
                            current_text = (await latest_element.inner_text()).strip()

                            if current_text and current_text != last_text:
                                if current_text.startswith(last_text):
                                    delta = current_text[len(last_text):]
                                elif len(current_text) > len(last_text) and last_text in current_text:
                                    idx = current_text.find(last_text) + len(last_text)
                                    delta = current_text[idx:]
                                else:
                                    delta = current_text
                                last_text = current_text
                                no_change_counter = 0
                                yield delta
                                break
                            elif current_text and current_text == last_text:
                                no_change_counter += 1
                                break

                    if last_text and no_change_counter >= 6:
                        break
                except Exception as e:
                    logger.debug(f"DOM polling exception (recovering): {e}")
                    continue
        except Exception as e:
            logger.error(f"Error during stream_chat_browser: {e}")
            yield f"[Browser Error: {e}]"

    async def close(self):
        """Closes Playwright browser session."""
        if self._page and not self._page.is_closed():
            try:
                await self._page.close()
            except Exception:
                pass
        self._page = None
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._context = None
        logger.info("Closed Playwright browser session.")
