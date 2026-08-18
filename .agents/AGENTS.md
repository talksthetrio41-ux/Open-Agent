# Open Agent — Android / Termux coding agent (Qwen Chat)

See the repository-root `AGENTS.md` for the full architecture, tool protocol, and CDP / Playwright gotchas.

Summary for agents working in this repo:

- This is a **Termux/Android Python agent**, not a Cloudflare Pages / Hono app.
- Free model access is via **system Chromium on chat.qwen.ai**.
  - Android/Termux: `open_agent/cdp.py` (Chrome DevTools Protocol).
  - Desktop: Playwright, lazy-imported from `open_agent/qwen_browser.py`.
- **Never** `pip install playwright` on Termux. PyPI has no `aarch64-linux-android` wheel.
- **Never** `import playwright` at module top-level. `qwen_browser.py` must import without Playwright installed.
- Shell commands run on the **phone** (`open_agent/harness.py`).
- File create/edit/delete/commit uses the user's **GitHub repo** (`open_agent/github_fs.py`).
- GUI lives in `public/` and is served by `open_agent/server.py`.
- One-command install is `install.sh`; it must print the Cloudflare tunnel URL + PIN and use `requirements-termux.txt` on Android.
- Do **not** inject `Authorization` headers into the browser context.
- Send button selector: `button.send-button, button[aria-label='send' i]`.
- Stream only `.qwen-markdown, .markdown-body`.
- Normalize `\xa0` and strip DOM line numbers when parsing bash blocks.
- Always pass `env=os.environ.copy()` to subprocesses.
- Termux Chromium is in `x11-repo`; `cloudflared` is in `tur-repo`.
