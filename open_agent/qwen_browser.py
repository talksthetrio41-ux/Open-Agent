"""Playwright driver for chat.qwen.ai — Termux/Android aware."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncGenerator, Dict, Optional

from playwright.async_api import BrowserContext, Page, async_playwright

from open_agent.config import BROWSER_DATA_DIR, find_chromium, get_env, write_env

logger = logging.getLogger("QwenBrowser")

CHAT_URL = "https://chat.qwen.ai"
AUTH_URL = "https://chat.qwen.ai/auth"

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--mute-audio",
    "--no-first-run",
]


class QwenBrowserAutomator:
    """
    Persistent Chromium session against chat.qwen.ai.

    Gotchas (do not regress):
    - NEVER set_extra_http_headers({"Authorization": ...}) — breaks Qwen CDN/WS.
    - Send with button.send-button / aria-label=send, not the parent wrapper.
    - Stream only .qwen-markdown / .markdown-body (ignore Skip / Thinking buttons).
    """

    def __init__(
        self,
        headless: bool = True,
        user_data_dir: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.headless = headless
        self.user_data_dir = user_data_dir or str(BROWSER_DATA_DIR)
        self.token = token or get_env("QWEN_TOKEN")
        self.cookie_str = get_env("QWEN_COOKIE")
        self.username = get_env("QWEN_USERNAME")
        self.password = get_env("QWEN_PASSWORD")
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self.captured_token: Optional[str] = self.token
        self.captured_cookies: Dict[str, str] = {}
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()

    def set_credentials(self, username: str, password: str) -> None:
        self.username = (username or "").strip()
        self.password = password or ""
        write_env("QWEN_USERNAME", self.username)
        write_env("QWEN_PASSWORD", self.password)

    def _parse_cookie_string(self, cookie_str: str) -> list:
        cookies = []
        for item in (cookie_str or "").split(";"):
            if "=" in item:
                k, v = item.split("=", 1)
                cookies.append(
                    {
                        "name": k.strip(),
                        "value": v.strip(),
                        "domain": ".qwen.ai",
                        "path": "/",
                    }
                )
        return cookies

    def _launch_kwargs(self) -> dict:
        kwargs: dict = {
            "headless": self.headless,
            "args": list(BROWSER_ARGS),
            "viewport": {"width": 1280, "height": 800},
            "ignore_https_errors": True,
        }
        exe = find_chromium()
        if exe:
            kwargs["executable_path"] = exe
            logger.info("Using system Chromium: %s", exe)
        return kwargs

    async def start(self) -> None:
        if self._playwright:
            return
        os.makedirs(self.user_data_dir, exist_ok=True)
        self._playwright = await async_playwright().start()
        launch = self._launch_kwargs()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                timeout=20_000,
                **launch,
            )
        except Exception as exc:
            logger.warning("Persistent context failed (%s). Ephemeral fallback.", exc)
            browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=launch["args"],
                executable_path=launch.get("executable_path"),
            )
            self._context = await browser.new_context(
                viewport=launch["viewport"],
                ignore_https_errors=True,
            )

        if self.cookie_str:
            try:
                parsed = self._parse_cookie_string(self.cookie_str)
                if parsed:
                    await self._context.add_cookies(parsed)
            except Exception as exc:
                logger.warning("Failed setting cookies: %s", exc)

        self._context.on("request", self._handle_request)
        logger.info("Playwright Chromium session ready.")

    def _handle_request(self, request) -> None:
        headers = request.headers
        if "authorization" in headers and not self.captured_token:
            auth = headers["authorization"]
            if auth and auth != "Bearer null":
                self.captured_token = auth
                logger.info("Captured Authorization token %s…", auth[:24])

    async def _persist_session(self, page: Page) -> None:
        try:
            cookies = await self._context.cookies()
            self.captured_cookies = {c["name"]: c["value"] for c in cookies}
            header = "; ".join(f"{k}={v}" for k, v in self.captured_cookies.items())
            if header:
                write_env("QWEN_COOKIE", header)
        except Exception:
            pass
        try:
            ls_token = await page.evaluate(
                "() => localStorage.getItem('token') || localStorage.getItem('access_token') || localStorage.getItem('user_token')"
            )
            if ls_token:
                self.captured_token = ls_token
        except Exception:
            pass
        if self.captured_token:
            write_env("QWEN_TOKEN", self.captured_token)

    async def is_logged_in(self, page: Optional[Page] = None) -> bool:
        target = page or self._page
        if target is None or target.is_closed():
            return False
        try:
            url = target.url or ""
            if "/auth" in url:
                return False
            login_btn = await target.query_selector(
                "button:has-text('Log in'), button.header-right-auth-button"
            )
            if login_btn:
                return False
            textarea = await target.query_selector(
                "textarea.message-input-textarea, textarea[placeholder*='help' i]"
            )
            return textarea is not None
        except Exception:
            return False

    async def _ensure_logged_in(self, page: Page) -> bool:
        login_btn = await page.query_selector(
            "button:has-text('Log in'), button.header-right-auth-button"
        )
        on_auth = "/auth" in (page.url or "")
        if not login_btn and not on_auth:
            await self._persist_session(page)
            return True
        if not (self.username and self.password):
            logger.warning("Logged out and no Qwen credentials stored.")
            return False

        logger.info("Auto-login for %s", self.username)
        await page.goto(AUTH_URL, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        try:
            email_input = await page.wait_for_selector(
                "input[placeholder='Enter Your Email'], input[placeholder*='Email' i], input[type='email']",
                timeout=12_000,
            )
            pwd_input = await page.wait_for_selector(
                "input[placeholder='Enter Your Password'], input[placeholder*='Password' i], input[type='password']",
                timeout=12_000,
            )
        except Exception as exc:
            logger.error("Login form not found: %s", exc)
            return False

        await email_input.fill(self.username)
        await asyncio.sleep(0.25)
        await pwd_input.fill(self.password)
        await asyncio.sleep(0.25)
        signin = await page.query_selector("button:has-text('Sign in'), button[type='submit']")
        if signin:
            await signin.click()
        else:
            await pwd_input.press("Enter")
        try:
            await page.wait_for_url("https://chat.qwen.ai/**", timeout=20_000)
        except Exception:
            await asyncio.sleep(4)
        ok = await self.is_logged_in(page)
        if ok:
            await self._persist_session(page)
            logger.info("Auto-login successful.")
        return ok

    async def login(self, username: str, password: str) -> Dict[str, str]:
        self.set_credentials(username, password)
        await self.start()
        page = await self._context.new_page()
        try:
            await page.goto(AUTH_URL, wait_until="domcontentloaded", timeout=45_000)
            await asyncio.sleep(1.5)
            ok = await self._ensure_logged_in(page)
            await self._persist_session(page)
            return {
                "ok": "true" if ok or self.captured_token else "false",
                "token": (self.captured_token or "")[:24],
                "username": self.username,
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def reset_chat(self) -> None:
        if self._page and not self._page.is_closed():
            try:
                await self._page.close()
            except Exception:
                pass
        self._page = None
        logger.info("Chat thread reset.")

    async def _dismiss_cookie(self, page: Page) -> None:
        try:
            btn = await page.query_selector(
                "button:has-text('Accept all cookies'), .index-module__cookie-confirm-btn___T"
            )
            if btn:
                await btn.click()
                await asyncio.sleep(0.6)
        except Exception:
            pass

    async def _ensure_page(self) -> Page:
        await self.start()
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            logger.info("Opening %s", CHAT_URL)
            await self._page.goto(CHAT_URL, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await self._ensure_logged_in(self._page)
            await self._dismiss_cookie(self._page)
        return self._page

    async def stream_chat_browser(
        self,
        prompt: str,
        reset_session: bool = False,
        timeout_sec: int = 180,
    ) -> AsyncGenerator[str, None]:
        async with self._lock:
            async for chunk in self._stream_unlocked(prompt, reset_session, timeout_sec):
                yield chunk

    async def _stream_unlocked(
        self, prompt: str, reset_session: bool, timeout_sec: int
    ) -> AsyncGenerator[str, None]:
        if reset_session:
            await self.reset_chat()
        page = await self._ensure_page()
        content_selectors = [".qwen-markdown", ".markdown-body"]

        initial_count = 0
        for selector in content_selectors:
            existing = await page.query_selector_all(selector)
            if existing:
                initial_count = max(initial_count, len(existing))

        input_selector = "textarea.message-input-textarea, textarea[placeholder*='help' i]"
        try:
            textarea = await page.wait_for_selector(input_selector, timeout=20_000)
        except Exception:
            # Session may have expired mid-run
            logged = await self._ensure_logged_in(page)
            if not logged:
                yield "[Error: Not logged in to Qwen. Open Settings and connect your account.]"
                return
            try:
                textarea = await page.wait_for_selector(input_selector, timeout=15_000)
            except Exception:
                yield "[Error: Could not locate chat input textarea in browser]"
                return

        await textarea.focus()
        # fill() is faster and more reliable than type() for long system prompts
        await textarea.fill(prompt)
        await asyncio.sleep(0.4)

        send_btn = None
        try:
            send_btn = await page.wait_for_selector(
                "button.send-button, button[aria-label='send' i]",
                state="visible",
                timeout=5_000,
            )
        except Exception:
            pass

        if send_btn:
            try:
                await send_btn.click(force=True)
                logger.info("Clicked Send.")
            except Exception:
                await page.keyboard.press("Enter")
        else:
            await page.keyboard.press("Enter")

        await asyncio.sleep(1.2)

        last_text = ""
        no_change = 0
        polls = max(timeout_sec * 2, 20)
        for _ in range(polls):
            await asyncio.sleep(0.5)
            try:
                for selector in content_selectors:
                    elements = await page.query_selector_all(selector)
                    if elements and len(elements) > initial_count:
                        latest = elements[-1]
                        current = (await latest.inner_text()).strip()
                        if current and current != last_text:
                            if current.startswith(last_text):
                                delta = current[len(last_text) :]
                            elif last_text and last_text in current and len(current) > len(last_text):
                                idx = current.find(last_text) + len(last_text)
                                delta = current[idx:]
                            else:
                                delta = current if not last_text else current[len(last_text) :]
                            last_text = current
                            no_change = 0
                            if delta:
                                yield delta
                            break
                        if current and current == last_text:
                            no_change += 1
                            break
                if last_text and no_change >= 8:
                    break
            except Exception as exc:
                logger.debug("DOM poll recover: %s", exc)
                continue

    async def close(self) -> None:
        if self._page and not self._page.is_closed():
            try:
                await self._page.close()
            except Exception:
                pass
        self._page = None
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._context = None
        logger.info("Closed Playwright session.")
