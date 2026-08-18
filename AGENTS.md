# Open Agent — Android / Termux coding agent (Qwen Chat)

## Overview

Open Agent turns **free unlimited chats** on [chat.qwen.ai](https://chat.qwen.ai) into a real coding agent on a phone.

It logs into Qwen with **Chromium** (Playwright), streams the model’s replies from the page DOM, runs commands on the **Android/Termux device as the sandbox**, and treats a **GitHub repo** as the file system (user PAT + `owner/name`).

There is **no web-search tool**. Qwen already browses; the harness only runs shell + GitHub file tools.

This is **not** a Cloudflare Pages / Hono app. The GUI is a local FastAPI server. On Termux it is published with a **Cloudflare quick tunnel** (`trycloudflare.com`) and the URL + unlock PIN are printed in the terminal.

---

## One-command install (Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/talksthetrio41-ux/Open-Agent/main/install.sh | bash
```

That script:

1. `pkg install` git, python, chromium, cloudflared, build deps
2. Clones this repo to `~/open-agent`
3. Creates `.venv` and installs `requirements.txt`
4. Points Playwright at **system Chromium** (Playwright’s bundled browser is too heavy / broken on Android)
5. Starts `python -m open_agent`
6. Prints the **public HTTPS URL** and a **4-digit PIN**

Open the URL on the phone, unlock with the PIN, then enter Qwen email/password in the GUI.

Manual launch later:

```bash
~/open-agent/oa
```

---

## Architecture

| Piece | Role |
|---|---|
| `install.sh` | One-shot Termux bootstrap + auto-start |
| `open_agent/__main__.py` | Starts FastAPI + cloudflared, prints URL/PIN |
| `open_agent/server.py` | GUI + JSON/SSE API |
| `public/` | Mobile-first GUI (login, chat, Compact, Clear, Settings) |
| `open_agent/qwen_browser.py` | Playwright driver for `chat.qwen.ai` |
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

## Critical Playwright gotchas (do not regress)

- **Never** `context.set_extra_http_headers({"Authorization": ...})`. It breaks Qwen’s CDN and websockets (unstyled page, dead input).
- Send button: `button.send-button, button[aria-label='send' i]`. Do **not** click the parent `.message-input-right-button-send` wrapper (voice input).
- Stream only `.qwen-markdown, .markdown-body`. Ignore “Skip” / “Thinking completed”.
- `extract_bash_command` must normalize `\xa0` and strip DOM line numbers (`1\n2\n3…`) plus duplicate `bash`/`sh` headers.
- Always `env=os.environ.copy()` for subprocesses.

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
```

`.env` is gitignored. PIN is auto-generated on first launch if empty.

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
