# Open Agent

Free agentic coding on **Android / Termux**. It logs into [chat.qwen.ai](https://chat.qwen.ai) with **system Chromium** (Chrome DevTools Protocol — Playwright has no Android wheels), runs commands on the phone, and keeps files in **your GitHub repo**.

This is **not** a Cloudflare Pages site. The GUI is a local FastAPI server. Termux publishes it with a Cloudflare **quick tunnel** and prints the URL.

## One-command install (Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/talksthetrio41-ux/Open-Agent/main/install.sh | bash
```

The script enables `x11-repo` / `tur-repo`, installs git, Python, Chromium, and `cloudflared`, clones this repo to `~/open-agent`, installs **`requirements-termux.txt`** as wheels only (never Playwright, never compiles Rust `pydantic-core`), then launches the agent. Termux prints something like:

```
Public GUI : https://xxxx.trycloudflare.com
Unlock PIN : 4821
```

1. Open the URL on the phone.
2. Enter the PIN.
3. Sign in with your **Qwen email + password** (first visit).
4. Optional: Settings → paste a GitHub PAT + `owner/name` so file edits live in your repo.

Later launches:

```bash
~/open-agent/oa
```

## What it does

| Need | How |
|---|---|
| Model | Free Qwen Chat via system Chromium + CDP (no paid API key) |
| Run code | Shell on this Android device (` ```bash `) |
| Edit files | GitHub workspace (` ```github write/read/ls/delete/commit `) |
| Search the web | Built into Qwen — no extra tool |
| GUI | Mobile web UI over a Cloudflare tunnel |
| Compact | Summarize the thread, start a **new** Qwen chat, paste the handoff |
| Clear | Drop history and open a new Qwen chat |

## GUI commands

- **Compact** or `/compact` — prompt the model to compact history, reset the Qwen thread, seed the summary
- **Clear** or `/clear` — new chat, no summary
- `/stop` — cancel the current loop

## Desktop / Linux (no Termux)

```bash
git clone https://github.com/talksthetrio41-ux/Open-Agent.git
cd Open-Agent
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m open_agent --no-tunnel
```

Open http://127.0.0.1:8765 and use the PIN printed in the terminal.

```bash
python -m pytest tests -q
```

## Project layout

```
install.sh                 one-shot Termux bootstrap (no Playwright on Android)
oa                         launcher
requirements-termux.txt    Android pip deps (no Playwright)
requirements.txt           desktop pip deps (includes Playwright)
open_agent/                Python package
  __main__.py              server + tunnel + PIN banner
  server.py                FastAPI GUI / SSE API
  agent.py                 agentic loop
  harness.py               bash + github tool runner
  github_fs.py             clone / edit / commit / push
  cdp.py                   Chrome DevTools Protocol client (Termux)
  qwen_browser.py          Qwen driver (CDP on Android, Playwright on desktop)
  config.py                prefer_cdp / Chromium paths
  prompts.py               system + compact prompts
public/                    mobile GUI
workspace/                 local clone of the linked GitHub repo
```

## Security

- `.env` and `qwen_browser_data/` are gitignored (password, token, cookies).
- The public tunnel is gated by a PIN printed only in Termux.
- Never commit `GITHUB_TOKEN` or Qwen passwords.

## Status

- [x] One-command Termux install + Cloudflare tunnel URL
- [x] Mobile GUI with Qwen login, Compact, Clear, GitHub settings
- [x] Android sandbox shell + GitHub file tools
- [x] System / compact / resume prompts
- [x] CDP backend so Termux never needs Playwright
- [ ] Optional extra free providers (DeepSeek web, etc.)
- [ ] Termux:Widget / notification controls

See `AGENTS.md` for CDP / Playwright gotchas and the tool protocol.
