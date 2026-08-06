# Qwen Web Chat Reverse-Engineered Driver & Autonomous Agentic Harness

## Overview
This repository provides a reverse-engineered client, headless browser automation layer, interactive CLI, FastAPI server, and an **Autonomous Agentic Coding Harness** for **Qwen Chat (`chat.qwen.ai`)**, providing free access to flagship models (e.g. `qwen-max`, `qwen-plus`, `qwen-turbo`) without needing paid API keys.

---

## Key Components & Architecture

1. **`harness.py` (`AgentHarness`)**:
   - Manages the agentic system prompt, parses bash code blocks (supporting both standard markdown and Qwen UI DOM rendered formats with `\xa0` space normalization), and executes commands locally inside a `./sandbox/` directory using Python `subprocess`.

2. **`agent_cli.py`**:
   - Autonomous agent entrypoint. Asks for a coding task, drives `QwenBrowserAutomator`, executes extracted commands via `harness.py`, feeds `stdout/stderr` back into the chat loop, and stops when `<DONE>` is detected.

3. **`qwen_browser.py` (`QwenBrowserAutomator`)**:
   - Primary Playwright driver for browser-based UI automation on `chat.qwen.ai`.
   - **Persistent Context**: Uses `./qwen_browser_data` to persist session cookies and avoid re-authenticating on every run.
   - **Auto Re-authentication (`_ensure_logged_in`)**: Automatically detects if the session is logged out and auto-fills credentials (`QWEN_USERNAME`, `QWEN_PASSWORD`) at `https://chat.qwen.ai/auth`.
   - **Single-Thread Conversation Continuity**: Maintains an active browser tab (`self._page`) across calls so multi-turn chats retain context on `https://chat.qwen.ai/c/<chat_id>`.
   - **DOM Streaming**: Filters for `.qwen-markdown, .markdown-body` elements, accurately calculating delta updates while ignoring UI buttons ("Skip", "Thinking completed").

4. **`login.py`**:
   - Terminal utility to authenticate with `chat.qwen.ai`.
   - Captures and stores the JWT authorization token and session cookies in `.env` and `./qwen_browser_data`.

5. **`cli.py`**:
   - Interactive command-line chat interface streaming live responses.

6. **`qwen_web_client.py` (`QwenWebClient`)**:
   - Lightweight direct HTTP SSE client interacting with `chat.qwen.ai/api/...` endpoints using Bearer token authentication.

7. **`server.py`**:
   - FastAPI server exposing OpenAI-compatible REST API endpoints (`/v1/chat/completions`) and native endpoints (`/chat/stream`).

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

3. **Run Autonomous Agent**:
   ```bash
   python agent_cli.py --task "Create a Python script that calculates primes and run it"
   ```

4. **Run Interactive Chat CLI**:
   ```bash
   python cli.py
   ```

5. **Run Server**:
   ```bash
   python server.py
   ```

---

## Critical Implementation Notes & Gotchas

- **Header Injection Warning**: DO NOT use `context.set_extra_http_headers({"Authorization": ...})` globally in Playwright. It causes Qwen's CDN resources and websockets to fail with CORS/Unauthorized errors, leaving the page unstyled and input disabled.
- **Send Button Selector**: The Send button selector is `"button.send-button, button[aria-label='send' i]"`. Do not click the parent `.message-input-right-button-send` wrapper directly as it can trigger voice input if input state isn't registered.
- **Stream Scoping**: Response polling must target `.qwen-markdown, .markdown-body` to avoid capturing UI buttons like `"Skip"`.
- **Qwen UI Code Block Parsing**: `extract_bash_command` in `harness.py` normalizes non-breaking spaces `\xa0` to `" "` and strips Playwright DOM rendered line numbers (`1\n2\n3...`) and duplicate `bash`/`sh` headers.
- **Subprocess Environment Pass-Through**: Always pass `env=os.environ.copy()` to `subprocess.run` to ensure CLI tools (`git`, `pip`, `npm`, `kaggle`, `huggingface-cli`) have access to `.env` tokens.
