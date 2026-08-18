# Open Agent — Android / Termux coding agent (Qwen Chat)

## Overview

Open Agent turns **free unlimited chats** on [chat.qwen.ai](https://chat.qwen.ai) into a real coding agent on a phone.

It logs into Qwen with **system Chromium** (Chrome DevTools Protocol on Android; Playwright on desktop), streams the model’s replies from the page DOM, runs commands on the **Android/Termux device as the sandbox**, and treats a **GitHub repo** as the file system (user PAT + `owner/name`).

There is **no web-search tool**. Qwen already browses; the harness only runs shell + GitHub file tools.

This is **not** a Cloudflare Pages / Hono app. The GUI is a local FastAPI server. On Termux it is published with a **Cloudflare quick tunnel** (`trycloudflare.com`) and the URL + unlock PIN are printed in the terminal.

---

## One-command install (Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/talksthetrio41-ux/Open-Agent/main/install.sh | bash
```

That script:

1. Enables `x11-repo` then `pkg install` git, python, **system Chromium**, `termux-api`
2. Enables `tur-repo` then `pkg install cloudflared`
3. Clones this repo to `~/open-agent`
4. Creates `.venv` and installs **`requirements-termux.txt`** as **wheels only** (`--only-binary=:all:`). Pins `pydantic<2` so Termux never compiles Rust `pydantic-core`. It **never** `pip install playwright` — PyPI has no `aarch64-linux-android` wheel
5. Writes `.env` with `OPEN_AGENT_BROWSER=cdp`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, and `CHROMIUM_PATH` pointing at Termux Chromium (`$PREFIX/bin/chromium` or `$PREFIX/lib/chromium/chromium`)
6. Starts `python -m open_agent`, which drives Chromium over **CDP** (`open_agent/cdp.py`)
7. Prints the **public HTTPS URL** and a **4-digit PIN**

Open the URL on the phone, unlock with the PIN, then enter Qwen email/password in the GUI.

Manual launch later:

```bash
~/open-agent/oa
```

---

## Architecture

| Piece | Role |
|---|---|
| `install.sh` | One-shot Termux bootstrap + auto-start (Playwright-free on Android) |
| `requirements-termux.txt` | Android pip deps — **no Playwright** |
| `requirements.txt` | Desktop pip deps (includes Playwright) |
| `open_agent/__main__.py` | Starts FastAPI + cloudflared, prints URL/PIN |
| `open_agent/server.py` | GUI + JSON/SSE API (Secure cookie on HTTPS / `X-Forwarded-Proto`) |
| `public/` | Mobile-first GUI (login, chat, Compact, Clear, Settings) |
| `open_agent/cdp.py` | Chrome DevTools Protocol client; duck-types Playwright Page/Element |
| `open_agent/qwen_browser.py` | Qwen driver — CDP on Android, Playwright on desktop (lazy import) |
| `open_agent/config.py` | `is_android()`, `prefer_cdp()`, Chromium / cloudflared paths |
| `open_agent/agent.py` | Agentic loop: prompt → stream → tool → feed output back |
| `open_agent/harness.py` | Parse ` ```bash ` / ` ```github ` blocks; run them |
| `open_agent/github_fs.py` | Clone / read / write / delete / commit+push |
| `open_agent/prompts.py` | System prompt, compact prompt, resume preamble |
| `workspace/` | Local clone of the user’s GitHub repo (sandbox cwd) |

---

## Tools the model may emit

Exactly **one** tool block per turn.

### Shell (Android sandbox)

````
```bash
ls -la
```
````

Runs in `workspace/` with the full environment (so `GITHUB_TOKEN`, etc. are visible). Output is truncated at 8k chars and pasted back into the Qwen thread.

### GitHub files

````
```github write path/to/file.py
print("hi")
```

```github read path/to/file.py
```

```github ls src/
```

```github delete old.txt
```

```github commit short message
```
````

---

## GUI commands

| Control | Effect |
|---|---|
| **Compact** or `/compact` | Ask Qwen to summarize the thread (`<COMPACT_READY>`), close the chat, open a **new** chat, paste the handoff |
| **Clear** or `/clear` | Drop local history and start a new Qwen thread (no summary) |
| `/stop` | Cancel the current loop |
| Settings | Reconnect Qwen; link GitHub PAT + `owner/name` |

On first visit the GUI asks for **Qwen email + password**. Session cookies live in `qwen_browser_data/` so later launches skip login.

---

## Critical Playwright / Termux / CDP gotchas (do not regress)

- **Never** `pip install playwright` on Termux. PyPI has **no** `aarch64-linux-android` wheel (`from versions: none`). Use `requirements-termux.txt` + `open_agent/cdp.py`.
- **Never** `pip install pydantic>=2` on Termux. `pydantic-core` is Rust and has **no** Android wheel; pip hangs at `Installing build dependencies` (screenshot: `pydantic_core-*.tar.gz`). Pin `pydantic<2` + `fastapi<0.126` and always `pip install --only-binary=:all:`.
- **Never** `import playwright` at module top-level in `qwen_browser.py`. Android must be able to import the package without Playwright installed.
- Default backend is CDP on Android (`prefer_cdp()`). Force with `OPEN_AGENT_BROWSER=cdp` or `=playwright`.
- **Never** `context.set_extra_http_headers({"Authorization": ...})`. It breaks Qwen’s CDN and websockets (unstyled page, dead input).
- Send button: `button.send-button, button[aria-label='send' i]`. Do **not** click the parent `.message-input-right-button-send` wrapper (voice input).
- Stream only `.qwen-markdown, .markdown-body`. Ignore “Skip” / “Thinking completed”.
- `extract_bash_command` must normalize `\xa0` and strip DOM line numbers (`1\n2\n3…`) plus duplicate `bash`/`sh` headers.
- Always `env=os.environ.copy()` for subprocesses.
- **Never** pipe Chromium's stderr (`stderr=subprocess.PIPE`) without draining it — the browser deadlocks once the buffer fills. Log to `.runtime/chromium.log` instead (also used for crash diagnostics).
- On Android, try `--headless=new --no-zygote` **before** `--single-process`: single-process Chromium often crashes *while navigating* heavy SPAs (chat.qwen.ai), surfacing as a bare httpx "All connection attempts failed" at login.
- CDP HTTP helpers must raise `CdpError` with context (port, process state, log tail) — never leak raw httpx `ConnectError` to the GUI.
- `qwen_browser.login()` / `_ensure_page()` must relaunch Chromium and retry once when the browser process died; `start()` must detect a dead process instead of reusing a cached context.
- Termux Chromium lives in `x11-repo`. `cloudflared` lives in `tur-repo`. Enable those repos *before* installing the packages.
- Chromium binary may be `$PREFIX/lib/chromium/chromium`, not only `$PREFIX/bin/chromium`.
- The public tunnel is HTTPS. `/api/unlock` must set `Secure` on the PIN cookie when `X-Forwarded-Proto: https` so mobile Chrome keeps the session.
- `install.sh` must only re-exec bash via `$0` when `$0` is a real file (safe for `curl | bash`).
- `install.sh` and `oa` must update code with `fetch --depth 1` + `reset --hard origin/$BRANCH` — never `pull --ff-only … || true`, which silently leaves stale code on the device (this is how the top-level `import playwright` crash survived after its fix). `.env` and `qwen_browser_data/` are untracked, so the reset is safe. `OA_NO_UPDATE=1` opts out.
- `install.sh` self-heals: if `open_agent/qwen_browser.py` has a top-level playwright import after checkout, force a fresh clone.

---

## Environment (`.env`)

```env
QWEN_USERNAME=
QWEN_PASSWORD=
QWEN_TOKEN=
QWEN_COOKIE=
GITHUB_TOKEN=
GITHUB_REPO=owner/name
HOST=0.0.0.0
PORT=8765
OPEN_AGENT_PIN=
CHROMIUM_PATH=
OPEN_AGENT_BROWSER=cdp
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
```

`.env` is gitignored. PIN is auto-generated on first launch if empty.

On Termux, `install.sh` upserts `OPEN_AGENT_BROWSER=cdp`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, and `CHROMIUM_PATH`. Desktop can leave `OPEN_AGENT_BROWSER` empty (Playwright if installed, else CDP).

---

## Local desktop dev (no Termux)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m open_agent --no-tunnel
```

GUI: http://127.0.0.1:8765  (PIN printed in the terminal)

```bash
python -m pytest tests -q
```

---

## What this project is not

- Not a paid API gateway
- Not a Cloudflare Pages site (in-app Preview / `gsk hosted deploy` do not apply)
- No built-in web-search tool (the model already has it)
- No long-running Node server
