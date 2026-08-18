# Open Agent — Android / Termux coding agent (Qwen Chat)

See the repository-root `AGENTS.md` for the full architecture, tool protocol, and Playwright gotchas.

Summary for agents working in this repo:

- This is a **Termux/Android Python agent**, not a Cloudflare Pages / Hono app.
- Free model access is via **Chromium on chat.qwen.ai** (`open_agent/qwen_browser.py`).
- Shell commands run on the **phone** (`open_agent/harness.py`).
- File create/edit/delete/commit uses the user's **GitHub repo** (`open_agent/github_fs.py`).
- GUI lives in `public/` and is served by `open_agent/server.py`.
- One-command install is `install.sh`; it must print the Cloudflare tunnel URL + PIN.
- Do **not** inject `Authorization` headers into the Playwright context.
- Send button selector: `button.send-button, button[aria-label='send' i]`.
- Stream only `.qwen-markdown, .markdown-body`.
- Normalize `\xa0` and strip DOM line numbers when parsing bash blocks.
- Always pass `env=os.environ.copy()` to subprocesses.
