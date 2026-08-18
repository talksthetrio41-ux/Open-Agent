"""Minimal async Chrome DevTools client.

Playwright publishes no wheels for Android/Termux (`aarch64-linux-android`),
and its driver is a glibc binary that cannot run on Bionic. On those hosts we
drive the *system* Chromium package over CDP instead.

The types here duck-type the Playwright Page / Element / Context surface that
`qwen_browser.py` already uses, including Playwright-only selectors such as
`:has-text('…')` and `[attr='val' i]`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger("OpenAgentCDP")

# Injected into every document before we query the DOM.
_HELPER_JS = r"""
(() => {
  if (window.__oaReady) return true;
  window.__oaStore = window.__oaStore || {};
  window.__oaSeq = window.__oaSeq || 1;
  function splitList(s) {
    const parts = [];
    let buf = "", q = null;
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (q) { if (c === q) q = null; buf += c; }
      else if (c === '"' || c === "'") { q = c; buf += c; }
      else if (c === ",") { if (buf.trim()) parts.push(buf.trim()); buf = ""; }
      else buf += c;
    }
    if (buf.trim()) parts.push(buf.trim());
    return parts;
  }
  function queryPart(part) {
    const hasText = part.match(/^(.*?):has-text\((['"])(.*)\2\)\s*$/i);
    let css = part;
    let text = null;
    if (hasText) {
      css = (hasText[1] || "*").trim() || "*";
      text = hasText[3];
    }
    const ciAttrs = [];
    css = css.replace(
      /\[([*\w:-]+)\s*([*^$|~]?=)\s*(['"])(.*?)\3\s+i\]/gi,
      (_, name, op, _q, val) => {
        ciAttrs.push({ name, op, val: String(val).toLowerCase() });
        return "";
      }
    );
    css = css.replace(/\s+/g, " ").trim() || "*";
    let els;
    try { els = Array.from(document.querySelectorAll(css)); }
    catch (_e) { els = []; }
    if (ciAttrs.length) {
      els = els.filter((el) => ciAttrs.every((a) => {
        const v = (el.getAttribute(a.name) || "").toLowerCase();
        if (a.op === "=") return v === a.val;
        if (a.op === "*=") return v.includes(a.val);
        if (a.op === "^=") return v.startsWith(a.val);
        if (a.op === "$=") return v.endsWith(a.val);
        if (a.op === "~=") return v.split(/\s+/).includes(a.val);
        if (a.op === "|=") return v === a.val || v.startsWith(a.val + "-");
        return true;
      }));
    }
    if (text != null) {
      els = els.filter((el) => (el.innerText || el.textContent || "").includes(text));
    }
    return els;
  }
  window.__oaQueryAll = (selector) => {
    const out = [];
    for (const p of splitList(String(selector || ""))) out.push(...queryPart(p));
    return out;
  };
  window.__oaVisible = (el) => {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  window.__oaHold = (el) => {
    const id = String(window.__oaSeq++);
    window.__oaStore[id] = el;
    return id;
  };
  window.__oaReady = true;
  return true;
})()
"""

ANDROID_CHROME_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-translate",
    "--disable-features=Translate,BackForwardCache,MediaRouter,PaintHolding",
    "--disable-software-rasterizer",
    "--renderer-process-limit=1",
    "--remote-allow-origins=*",
    # crashpad cannot read /sys cpufreq on Android and spams the log
    "--disable-crash-reporter",
    "--disable-breakpad",
    "--noerrdialogs",
]


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def is_android() -> bool:
    if os.environ.get("TERMUX_VERSION"):
        return True
    prefix = os.environ.get("PREFIX", "")
    if "com.termux" in prefix:
        return True
    return os.path.exists("/data/data/com.termux") or os.path.exists("/system/build.prop")


class CdpError(RuntimeError):
    pass


class CdpConnection:
    """One WebSocket attached to a single target (page)."""

    def __init__(self, ws) -> None:
        self._ws = ws
        self._next_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._events: List[Callable[[str, dict], None]] = []
        self._reader = asyncio.create_task(self._read_loop())

    def on_event(self, cb: Callable[[str, dict], None]) -> None:
        self._events.append(cb)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if "id" in msg:
                    fut = self._pending.pop(int(msg["id"]), None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(CdpError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                else:
                    method = msg.get("method") or ""
                    params = msg.get("params") or {}
                    for cb in list(self._events):
                        try:
                            cb(method, params)
                        except Exception:
                            logger.debug("CDP event handler failed", exc_info=True)
        except Exception:
            logger.debug("CDP reader stopped", exc_info=True)
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(CdpError("CDP connection closed"))
            self._pending.clear()

    async def send(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        self._next_id += 1
        msg_id = self._next_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        await self._ws.send(json.dumps(payload))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def close(self) -> None:
        self._reader.cancel()
        try:
            await self._ws.close()
        except Exception:
            pass


class CdpElement:
    def __init__(self, page: "CdpPage", handle: str):
        self._page = page
        self._handle = handle

    async def _eval(self, js: str, arg: Any = None) -> Any:
        return await self._page._eval_handle(self._handle, js, arg)

    async def fill(self, text: str) -> None:
        await self.focus()
        await self._eval(
            """(el, value) => {
                el.focus();
                const proto = el.tagName === 'TEXTAREA'
                  ? window.HTMLTextAreaElement.prototype
                  : window.HTMLInputElement.prototype;
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            text,
        )

    async def click(self, force: bool = False) -> None:
        await self._eval(
            """(el, force) => {
                el.scrollIntoView({ block: 'center', inline: 'center' });
                if (force) {
                  const r = el.getBoundingClientRect();
                  const x = r.left + r.width / 2, y = r.top + r.height / 2;
                  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
                  }
                } else {
                  el.click();
                }
            }""",
            force,
        )

    async def press(self, key: str) -> None:
        await self.focus()
        await self._page.keyboard.press(key)

    async def focus(self) -> None:
        await self._eval("(el) => { el.focus(); }")

    async def inner_text(self) -> str:
        val = await self._eval("(el) => el.innerText || el.textContent || ''")
        return str(val or "")


class CdpKeyboard:
    def __init__(self, page: "CdpPage"):
        self._page = page

    async def press(self, key: str) -> None:
        mapping = {
            "Enter": ("Enter", "\r", 13),
            "Tab": ("Tab", "\t", 9),
            "Escape": ("Escape", "\u001b", 27),
            "Backspace": ("Backspace", "\u0008", 8),
        }
        name, text, code = mapping.get(key, (key, key, 0))
        params = {"type": "keyDown", "key": name}
        if text:
            params["text"] = text
        if code:
            params["windowsVirtualKeyCode"] = code
            params["nativeVirtualKeyCode"] = code
        try:
            await self._page._send("Input.dispatchKeyEvent", params)
            params["type"] = "keyUp"
            params.pop("text", None)
            await self._page._send("Input.dispatchKeyEvent", params)
        except Exception:
            await self._page.evaluate(
                """(key) => {
                    const el = document.activeElement || document.body;
                    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
                    if (key === 'Enter') {
                      el.dispatchEvent(new KeyboardEvent('keypress', { key, bubbles: true }));
                    }
                    el.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }));
                }""",
                name,
            )


class CdpPage:
    def __init__(self, chrome: "CdpChrome", ws_url: str, target_id: str):
        self._chrome = chrome
        self._ws_url = ws_url
        self._target_id = target_id
        self._conn: Optional[CdpConnection] = None
        self._closed = False
        self.keyboard = CdpKeyboard(self)
        self._url = "about:blank"

    @property
    def url(self) -> str:
        return self._url

    def is_closed(self) -> bool:
        return self._closed

    async def _ensure(self) -> CdpConnection:
        if self._conn is None:
            try:
                import websockets
            except ImportError as exc:
                raise CdpError(
                    "The 'websockets' package is required on Termux/Android. "
                    "Re-run install.sh or: pip install websockets"
                ) from exc
            ws = await websockets.connect(self._ws_url, max_size=None, ping_interval=20)
            conn = CdpConnection(ws)
            conn.on_event(self._on_event)
            self._conn = conn
            for domain in ("Page", "Runtime", "Network", "Input"):
                try:
                    await conn.send(f"{domain}.enable", timeout=10)
                except Exception as exc:
                    logger.debug("enable %s: %s", domain, exc)
            await self._inject_helper()
        return self._conn

    def _on_event(self, method: str, params: dict) -> None:
        if method in ("Page.frameNavigated", "Page.navigatedWithinDocument"):
            frame = params.get("frame") or {}
            if frame.get("url"):
                self._url = frame["url"]
        if method == "Network.requestWillBeSent":
            req = params.get("request") or {}
            headers = {str(k).lower(): str(v) for k, v in (req.get("headers") or {}).items()}
            self._chrome._emit_request(headers)

    async def _send(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        conn = await self._ensure()
        return await conn.send(method, params, timeout=timeout)

    async def _inject_helper(self) -> None:
        try:
            await self._send("Runtime.evaluate", {"expression": _HELPER_JS, "returnByValue": True})
        except Exception:
            logger.debug("helper inject failed", exc_info=True)

    async def _evaluate_raw(self, expression: str, await_promise: bool = True) -> Any:
        await self._inject_helper()
        result = await self._send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
            },
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = detail.get("text") or ""
            exc = (detail.get("exception") or {}).get("description") or text
            raise CdpError(exc or "Runtime.evaluate failed")
        return (result.get("result") or {}).get("value")

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        src = (expression or "").strip()
        if src.startswith("()") or "=>" in src:
            if arg is None:
                expr = f"({src})()"
            else:
                expr = f"({src})({json.dumps(arg)})"
        else:
            expr = src
        return await self._evaluate_raw(expr)

    async def _eval_handle(self, handle: str, fn: str, arg: Any = None) -> Any:
        payload = json.dumps(arg)
        expr = f"""(() => {{
            const el = window.__oaStore[{json.dumps(handle)}];
            if (!el) throw new Error('stale element');
            return ({fn})(el, {payload});
        }})()"""
        return await self._evaluate_raw(expr)

    async def _refresh_url(self) -> None:
        try:
            self._url = str(await self._evaluate_raw("location.href") or self._url)
        except Exception:
            pass

    async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 45_000) -> None:
        await self._ensure()
        await self._send("Page.navigate", {"url": url}, timeout=max(timeout / 1000, 5))
        deadline = asyncio.get_event_loop().time() + (timeout / 1000)
        while asyncio.get_event_loop().time() < deadline:
            try:
                state = await self._evaluate_raw("document.readyState")
                await self._refresh_url()
                if wait_until in ("domcontentloaded", "commit") and state in ("interactive", "complete"):
                    await self._inject_helper()
                    return
                if wait_until == "load" and state == "complete":
                    await self._inject_helper()
                    return
            except Exception:
                pass
            await asyncio.sleep(0.25)
        await self._inject_helper()
        await self._refresh_url()

    async def wait_for_url(self, pattern: str, timeout: int = 20_000) -> None:
        import fnmatch

        glob = pattern.replace("**", "*")
        deadline = asyncio.get_event_loop().time() + (timeout / 1000)
        while asyncio.get_event_loop().time() < deadline:
            await self._refresh_url()
            if fnmatch.fnmatch(self._url, glob) or self._url.startswith(pattern.rstrip("*")):
                return
            await asyncio.sleep(0.25)
        raise CdpError(f"Timeout waiting for URL {pattern} (last={self._url})")

    async def query_selector_all(self, selector: str) -> List[CdpElement]:
        handles = await self._evaluate_raw(
            f"""(() => {{
                const els = window.__oaQueryAll({json.dumps(selector)});
                return els.map((el) => window.__oaHold(el));
            }})()"""
        )
        if not handles:
            return []
        return [CdpElement(self, str(h)) for h in handles]

    async def query_selector(self, selector: str) -> Optional[CdpElement]:
        found = await self.query_selector_all(selector)
        return found[0] if found else None

    async def wait_for_selector(
        self,
        selector: str,
        timeout: int = 20_000,
        state: str = "attached",
    ) -> CdpElement:
        deadline = asyncio.get_event_loop().time() + (timeout / 1000)
        last_err = "not found"
        while asyncio.get_event_loop().time() < deadline:
            try:
                handles = await self._evaluate_raw(
                    f"""(() => {{
                        const els = window.__oaQueryAll({json.dumps(selector)});
                        const wantVisible = {json.dumps(state == "visible")};
                        const picked = [];
                        for (const el of els) {{
                            if (wantVisible && !window.__oaVisible(el)) continue;
                            picked.push(window.__oaHold(el));
                        }}
                        return picked;
                    }})()"""
                )
                if handles:
                    return CdpElement(self, str(handles[0]))
            except Exception as exc:
                last_err = str(exc)
            await asyncio.sleep(0.2)
        raise CdpError(f"Timeout waiting for selector {selector!r} ({last_err})")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._conn:
                await self._conn.close()
        except Exception:
            pass
        self._conn = None
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get(f"{self._chrome.http_base}/json/close/{self._target_id}")
        except Exception:
            pass


class CdpChrome:
    """A Chromium instance launched with --remote-debugging-port, plus Context-like API."""

    def __init__(
        self,
        executable: str,
        user_data_dir: str,
        headless: bool = True,
        extra_args: Optional[List[str]] = None,
    ):
        self.executable = executable
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.extra_args = list(extra_args or [])
        self.port = 0
        self.proc: Optional[subprocess.Popen] = None
        self.http_base = ""
        self._request_handlers: List[Callable] = []
        self._default_page: Optional[CdpPage] = None
        log_dir = Path(self.user_data_dir).parent / ".runtime"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log_dir = Path(self.user_data_dir)
        self.log_path = log_dir / "chromium.log"

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _log_tail(self, limit: int = 600) -> str:
        try:
            data = self.log_path.read_bytes()[-4000:]
            return data.decode("utf-8", "replace").strip()[-limit:]
        except OSError:
            return ""

    def _dead_error(self, exc: Exception) -> "CdpError":
        state = (
            f"process exited with code {self.proc.returncode}"
            if self.proc is not None and self.proc.poll() is not None
            else "process state unknown"
        )
        tail = self._log_tail()
        hint = f" Last Chromium log: {tail}" if tail else ""
        return CdpError(
            f"Chromium is not reachable at {self.http_base or '127.0.0.1'} ({state}).{hint} "
            f"Full log: {self.log_path}. Root cause detail: {exc}"
        )

    def on(self, event: str, handler: Callable) -> None:
        if event == "request":
            self._request_handlers.append(handler)

    def _emit_request(self, headers: Dict[str, str]) -> None:
        class _Req:
            def __init__(self, hdrs):
                self.headers = hdrs

        req = _Req(headers)
        for handler in list(self._request_handlers):
            try:
                handler(req)
            except Exception:
                logger.debug("request handler failed", exc_info=True)

    async def add_cookies(self, cookies: list) -> None:
        page = await self._any_page()
        formatted = []
        for c in cookies:
            item = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain") or ".qwen.ai",
                "path": c.get("path") or "/",
            }
            formatted.append(item)
        if formatted:
            await page._send("Network.setCookies", {"cookies": formatted})

    async def cookies(self) -> list:
        page = await self._any_page()
        result = await page._send("Network.getAllCookies")
        return result.get("cookies") or []

    async def _any_page(self) -> CdpPage:
        if self._default_page and not self._default_page.is_closed():
            return self._default_page
        page = await self.new_page()
        self._default_page = page
        return page

    async def launch(self, timeout: float = 40.0) -> None:
        os.makedirs(self.user_data_dir, exist_ok=True)
        self.port = free_port()
        self.http_base = f"http://127.0.0.1:{self.port}"
        attempts = self._attempts()
        last_err: Optional[Exception] = None
        for attempt in attempts:
            try:
                await self._spawn(
                    attempt["args"],
                    timeout=timeout,
                    executable=attempt.get("exe"),
                    strip_ldpreload=bool(attempt.get("strip")),
                )
                return
            except Exception as exc:
                last_err = exc
                logger.warning("Chromium launch attempt failed: %s", exc)
                self._kill()
        raise CdpError(
            f"Could not start Chromium at {self.executable}. "
            f"Last error: {last_err}. Diagnostics: {self._diagnostics()}. "
            f"If the binary lost its exec bit, run: pkg reinstall chromium "
            f"(pkg install x11-repo first if needed). Full log: {self.log_path}"
        )

    def _direct_binary(self) -> Optional[str]:
        """The real ELF behind the chromium-browser wrapper script."""
        try:
            parent = Path(self.executable).resolve().parent
        except OSError:
            parent = Path(self.executable).parent
        prefix = parent.parent
        for name in ("chrome", "chromium", "chrome_headless_shell"):
            candidate = prefix / "lib" / "chromium" / name
            if candidate.exists():
                return str(candidate)
        return None

    def _attempts(self) -> List[Dict[str, Any]]:
        """(executable, args, strip LD_PRELOAD) strategies, best first.

        Android needs a matrix because of two opposing constraints observed
        in the field:
        - With LD_PRELOAD=libtermux-exec.so kept, the wrapper's exec of the
          real binary works, but Chromium's crashpad/zygote re-exec of
          /proc/self/exe dies with 'CANNOT LINK EXECUTABLE ... not
          accessible for the namespace'. Hence --disable-crash-reporter and
          single-process/no-zygote first.
        - With LD_PRELOAD stripped, some devices instead fail at the wrapper
          exec with EACCES (126). So each flag set is tried both ways.
        """
        base = list(ANDROID_CHROME_ARGS if is_android() else [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--mute-audio",
            "--no-first-run",
            "--remote-allow-origins=*",
        ])
        base += [
            f"--user-data-dir={self.user_data_dir}",
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
        ]
        base += self.extra_args

        if self.headless:
            if is_android():
                flag_sets = [
                    ["--headless=new", "--single-process", "--no-zygote", "--in-process-gpu"],
                    ["--headless=new", "--no-zygote"],
                    ["--headless", "--single-process", "--no-zygote"],
                    ["--headless=new"],
                    ["--headless"],
                ]
            else:
                flag_sets = [["--headless=new"], ["--headless"]]
        else:
            flag_sets = [["--single-process", "--no-zygote"]] if is_android() else []
            flag_sets.append([])

        attempts: List[Dict[str, Any]] = []
        for flags in flag_sets:
            attempts.append({"exe": self.executable, "args": base + flags, "strip": False})
        if is_android():
            direct = self._direct_binary()
            if direct and direct != self.executable:
                for flags in flag_sets:
                    attempts.append({"exe": direct, "args": base + flags, "strip": False})
            for flags in flag_sets:
                attempts.append({"exe": self.executable, "args": base + flags, "strip": True})
        return attempts

    def _diagnostics(self) -> str:
        lines = []
        paths = {self.executable}
        direct = self._direct_binary()
        if direct:
            paths.add(direct)
        for path in sorted(paths):
            try:
                st = os.stat(path)
                lines.append(f"{path}: mode {oct(st.st_mode & 0o777)}")
            except OSError as exc:
                lines.append(f"{path}: {exc}")
        try:
            with open("/sys/fs/selinux/enforce", "r", encoding="utf-8") as fh:
                lines.append(f"SELinux enforce={fh.read().strip()}")
        except OSError:
            pass
        return " | ".join(lines)

    async def _spawn(self, args: List[str], timeout: float, executable: Optional[str] = None, strip_ldpreload: bool = False) -> None:
        exe = executable or self.executable
        cmd = [exe, *args, "about:blank"]
        logger.info("Launching Chromium: %s %s", exe, "strip-LD_PRELOAD" if strip_ldpreload else "")
        env = os.environ.copy()
        # Headless Chromium on Termux still probes DISPLAY; a dummy value avoids a hard abort.
        env.setdefault("DISPLAY", ":0")
        if strip_ldpreload:
            # Termux globally sets LD_PRELOAD=$PREFIX/lib/libtermux-exec.so.
            # Chromium re-execs /proc/self/exe inside the Android runtime
            # linker namespace, where Termux libs are NOT accessible:
            #   CANNOT LINK EXECUTABLE "/proc/self/exe": library
            #   ".../libtermux-exec.so" ... is not accessible for the namespace
            env.pop("LD_PRELOAD", None)
        # Chromium is very chatty on stderr. A PIPE nobody drains deadlocks the
        # browser once the buffer fills (launch then "times out" for no reason).
        # Log to a file instead — it also makes remote diagnosis possible.
        log_file = open(self.log_path, "ab", buffering=0)
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )
        finally:
            # Popen dup()s the fd into the child; our copy can go.
            log_file.close()
        deadline = asyncio.get_event_loop().time() + timeout
        last = ""
        async with httpx.AsyncClient(timeout=2.0) as client:
            while asyncio.get_event_loop().time() < deadline:
                if self.proc.poll() is not None:
                    err = self._log_tail(800)
                    raise CdpError(f"Chromium exited ({self.proc.returncode}): {err or 'no output'}")
                try:
                    resp = await client.get(f"{self.http_base}/json/version")
                    if resp.status_code == 200:
                        logger.info("CDP ready on port %s", self.port)
                        return
                    last = f"HTTP {resp.status_code}"
                except Exception as exc:
                    last = str(exc)
                await asyncio.sleep(0.35)
        raise CdpError(f"Timed out waiting for DevTools on {self.http_base} ({last})")

    async def _targets(self) -> list:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.http_base}/json/list")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            raise self._dead_error(exc) from exc

    async def new_page(self) -> CdpPage:
        # Reuse the first about:blank tab to save RAM on phones.
        try:
            targets = await self._targets()
        except CdpError:
            raise
        except Exception:
            targets = []
        reusable = None
        for t in targets:
            if t.get("type") == "page" and (t.get("url") or "").startswith("about:blank"):
                if not any(
                    p and not p.is_closed() and p._target_id == t.get("id")
                    for p in (self._default_page,)
                    if p
                ):
                    reusable = t
                    break
        if reusable and reusable.get("webSocketDebuggerUrl"):
            page = CdpPage(self, reusable["webSocketDebuggerUrl"], reusable.get("id") or "")
            await page._ensure()
            return page
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.put(f"{self.http_base}/json/new?{quote('about:blank', safe='')}")
                if resp.status_code >= 400:
                    resp = await client.get(f"{self.http_base}/json/new?{quote('about:blank', safe='')}")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise self._dead_error(exc) from exc
        ws = data.get("webSocketDebuggerUrl")
        if not ws:
            raise CdpError(f"Chromium did not return a page websocket: {data}")
        page = CdpPage(self, ws, data.get("id") or "")
        await page._ensure()
        return page

    def _kill(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=4)
            except Exception:
                self.proc.kill()
        except Exception:
            pass
        self.proc = None

    async def close(self) -> None:
        if self._default_page:
            try:
                await self._default_page.close()
            except Exception:
                pass
            self._default_page = None
        self._kill()
