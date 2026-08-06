# Qwen Web Chat Reverse-Engineered Driver & API Server

## Overview
This repository provides a reverse-engineered client, headless browser automation layer, interactive CLI, and FastAPI server for **Qwen Chat (`chat.qwen.ai`)**, providing free access to flagship models (e.g. `qwen-max`, `qwen-plus`, `qwen-turbo`) without needing paid API keys.

---

## Key Components & Architecture

1. **`qwen_browser.py` (`QwenBrowserAutomator`)**:
   - Primary Playwright driver for browser-based UI automation on `chat.qwen.ai`.
   - **Persistent Context**: Uses `./qwen_browser_data` to persist session cookies and avoid re-authenticating on every run.
   - **Auto Re-authentication (`_ensure_logged_in`)**: Automatically detects if the session is logged out and auto-fills credentials (`QWEN_USERNAME`, `QWEN_PASSWORD`) at `https://chat.qwen.ai/auth`.
   - **Single-Thread Conversation Continuity**: Maintains an active browser tab (`self._page`) across calls so multi-turn chats retain context on `https://chat.qwen.ai/c/<chat_id>`.
   - **DOM Streaming**: Filters for `.qwen-markdown, .markdown-body` elements, accurately calculating delta updates while ignoring UI buttons ("Skip", "Thinking completed").

2. **`login.py`**:
   - Terminal utility to authenticate with `chat.qwen.ai`.
   - Captures and stores the JWT authorization token and session cookies in `.env` and `./qwen_browser_data`.

3. **`cli.py`**:
   - Interactive command-line chat interface streaming live responses.
   - Commands:
     - `/new` or `reset`: Clears session history and opens a new chat thread.
     - `exit` or `quit`: Exits the interactive shell.
   - Flags: `--api-mode` (use raw HTTP SSE client instead of Playwright), `--headful` (show visible browser window).

4. **`qwen_web_client.py` (`QwenWebClient`)**:
   - Lightweight direct HTTP SSE client interacting with `chat.qwen.ai/api/...` endpoints using Bearer token authentication.

5. **`server.py`**:
   - FastAPI server exposing OpenAI-compatible REST API endpoints (`/v1/chat/completions`) and native endpoints (`/chat/stream`).

6. **`run_verified_tests.py` / `run_full_tests.py`**:
   - End-to-end verification scripts for testing token extraction, single-turn, and multi-turn Playwright streaming.

---

## Environment Setup (`.env`)

Create a `.env` file from `.env.example`:

```env
QWEN_USERNAME="your_email@domain.com"
QWEN_PASSWORD="your_password"

# Optional token & cookie fallbacks
QWEN_TOKEN="your_jwt_token_here"
QWEN_COOKIE="your_cookies_here"

PORT=8000
HOST="0.0.0.0"
```

---

## Getting Started

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **Login & Save Session**:
   ```bash
   python login.py
   ```

3. **Run Interactive CLI**:
   ```bash
   python cli.py
   ```

4. **Run Server**:
   ```bash
   python server.py
   ```

---

## Critical Implementation Notes & Gotchas

- **Header Injection Warning**: DO NOT use `context.set_extra_http_headers({"Authorization": ...})` globally in Playwright. It causes Qwen's CDN resources and websockets to fail with CORS/Unauthorized errors, leaving the page unstyled and input disabled.
- **Send Button Selector**: The Send button selector is `"button.send-button, button[aria-label='send' i]"`. Do not click the parent `.message-input-right-button-send` wrapper directly as it can trigger voice input if input state isn't registered.
- **Stream Scoping**: Response polling must target `.qwen-markdown, .markdown-body` to avoid capturing UI buttons like `"Skip"`.
