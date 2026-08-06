# Qwen Web Chat Streaming Client & Gateway

This project provides a complete solution to stream flagship Qwen model responses from the **Qwen Web Chat platform** (`chat.qwen.ai`) directly to your terminal or custom platform via Playwright browser automation (bypassing Aliyun Cloud WAF).

## Features

- **Default Playwright Automation**: Bypasses Aliyun Cloud WAF automatically in both CLI and Server modes.
- **Terminal Login Helper (`login.py`)**: Prompts for your Qwen email & password in the terminal, logs into `chat.qwen.ai`, captures the session token, and automatically updates `.env`.
- **FastAPI SSE Gateway (`server.py`)**: Gateway API providing `/chat/stream`, `/chat/playwright-stream`, and OpenAI-compatible `/v1/chat/completions`.
- **Interactive CLI (`cli.py`)**: Real-time terminal chat interface.

---

## Quick Setup & Usage

### 1. Extract Session Token via Terminal Login
Run `login.py` in your terminal to enter your Qwen credentials and save your session token:
```bash
python login.py
```
*(You will be prompted for your Qwen Email and Password. The script will output your session token and automatically save `QWEN_TOKEN` in `.env`)*

If your account requires a Captcha or 2FA verification code, run in headful browser mode:
```bash
python login.py --headful
```

### 2. Run Interactive Terminal CLI
Stream responses directly in your terminal:
```bash
python cli.py
```

### 3. Run FastAPI Streaming Gateway Server
Launch the local streaming server on port 8000:
```bash
python server.py
```

Stream responses from any application:
```bash
curl -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a 3-line poem about space."}'
```
