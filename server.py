import json
import os
import time
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qwen_web_client import QwenWebClient
from qwen_browser import QwenBrowserAutomator

browser_automator: Optional[QwenBrowserAutomator] = None

async def get_browser_automator(headless: bool = True) -> QwenBrowserAutomator:
    global browser_automator
    if browser_automator is None:
        token = os.getenv("QWEN_TOKEN", "")
        browser_automator = QwenBrowserAutomator(headless=headless, token=token)
        await browser_automator.start()
    return browser_automator


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    global browser_automator
    if browser_automator:
        await browser_automator.close()

app = FastAPI(
    title="Qwen Web Chat Streaming Gateway",
    description="Bridge server for streaming flagship Qwen models from chat.qwen.ai web interface via Playwright & HTTP",
    version="1.2.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatStreamRequest(BaseModel):
    prompt: str
    model: Optional[str] = "qwen-max"
    token: Optional[str] = None
    cookie: Optional[str] = None
    temperature: Optional[float] = 0.7
    use_playwright: Optional[bool] = True

class Message(BaseModel):
    role: str
    content: str

class OpenAIChatRequest(BaseModel):
    model: Optional[str] = "qwen-max"
    messages: List[Message]
    stream: Optional[bool] = True
    temperature: Optional[float] = 0.7
    use_playwright: Optional[bool] = True


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Qwen Web Chat Streaming API",
        "default_mode": "Playwright Browser Automation (Aliyun WAF Bypassed)",
        "endpoints": {
            "stream_direct": "POST /chat/stream",
            "stream_playwright": "POST /chat/playwright-stream",
            "openai_compat": "POST /v1/chat/completions",
            "token_helper": "GET /auth/token-helper"
        }
    }


@app.post("/chat/playwright-stream")
async def chat_playwright_stream(req: ChatStreamRequest):
    """
    Executes prompt directly using Playwright Chromium browser UI and streams SSE text back.
    """
    automator = await get_browser_automator(headless=True)

    async def event_generator():
        try:
            async for chunk in automator.stream_chat_browser(prompt=req.prompt):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest, authorization: Optional[str] = Header(None)):
    """
    Direct prompt streaming endpoint. Uses Playwright by default to bypass Aliyun Cloud WAF.
    """
    if req.use_playwright:
        return await chat_playwright_stream(req)

    token = req.token or (authorization.replace("Bearer ", "") if authorization else None) or os.getenv("QWEN_TOKEN")
    cookie = req.cookie or os.getenv("QWEN_COOKIE")

    client = QwenWebClient(token=token, cookies={"cookie_str": cookie} if cookie else None)
    messages = [{"role": "user", "content": req.prompt}]

    async def event_generator():
        try:
            async for chunk in client.stream_chat(messages=messages, model=req.model, temperature=req.temperature):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/v1/chat/completions")
async def openai_chat_completions(req: OpenAIChatRequest, authorization: Optional[str] = Header(None)):
    """
    OpenAI-compatible chat completions endpoint. Supports SSE streaming via Playwright.
    """
    if req.use_playwright and req.messages:
        latest_prompt = req.messages[-1].content
        automator = await get_browser_automator(headless=True)
        chat_id = f"chatcmpl-{int(time.time() * 1000)}"
        created_time = int(time.time())

        async def pw_openai_stream():
            async for chunk in automator.stream_chat_browser(prompt=latest_prompt):
                response_obj = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": req.model,
                    "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(response_obj)}\n\n"

            yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created_time, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(pw_openai_stream(), media_type="text/event-stream")

    token = (authorization.replace("Bearer ", "") if authorization else None) or os.getenv("QWEN_TOKEN")
    client = QwenWebClient(token=token)
    messages = [msg.model_dump() for msg in req.messages]
    chat_id = f"chatcmpl-{int(time.time() * 1000)}"
    created_time = int(time.time())

    if req.stream:
        async def openai_stream():
            try:
                async for chunk in client.stream_chat(messages=messages, model=req.model, temperature=req.temperature):
                    response_obj = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": req.model,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(response_obj)}\n\n"

                final_obj = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": req.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                }
                yield f"data: {json.dumps(final_obj)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                err_obj = {"error": {"message": str(e), "type": "server_error"}}
                yield f"data: {json.dumps(err_obj)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(openai_stream(), media_type="text/event-stream")
    else:
        full_text = ""
        try:
            async for chunk in client.stream_chat(messages=messages, model=req.model, temperature=req.temperature):
                full_text += chunk
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {
            "id": chat_id,
            "object": "chat.completion",
            "created": created_time,
            "model": req.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": full_text}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": sum(len(m["content"].split()) for m in messages),
                "completion_tokens": len(full_text.split()),
                "total_tokens": sum(len(m["content"].split()) for m in messages) + len(full_text.split())
            }
        }


@app.get("/auth/token-helper")
async def extract_token_via_browser():
    automator = QwenBrowserAutomator(headless=True)
    try:
        session_info = await automator.get_session_info("https://chat.qwen.ai")
        await automator.close()
        return session_info
    except Exception as e:
        await automator.close()
        raise HTTPException(status_code=500, detail=f"Failed extracting browser session: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
