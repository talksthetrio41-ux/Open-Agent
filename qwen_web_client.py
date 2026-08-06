import json
import os
import sys
import time
from typing import AsyncGenerator, List, Dict, Any, Optional
import httpx

DEFAULT_BASE_URL = "https://chat.qwen.ai"
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

class QwenWebClient:
    """
    Asynchronous API Client for Qwen Web Chat interface (chat.qwen.ai) using v2 API protocol.
    Directly streams SSE completion responses using session tokens / cookies.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        cookies: Optional[Dict[str, str]] = None,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 60.0
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token or os.getenv("QWEN_TOKEN", "")
        self.cookie_str = os.getenv("QWEN_COOKIE", "")
        self.user_agent = user_agent
        self.timeout = timeout

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/event-stream, application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }
        if self.token:
            headers["Authorization"] = self.token if self.token.startswith("Bearer ") else f"Bearer {self.token}"
        if self.cookie_str:
            headers["Cookie"] = self.cookie_str
        return headers

    async def create_new_chat(self, client: httpx.AsyncClient, model: str = "qwen3.8-max") -> str:
        """Calls POST /api/v2/chats/new to create a session chat_id."""
        url = f"{self.base_url}/api/v2/chats/new"
        payload = {
            "chatId": "",
            "models": [model],
            "project_id": "",
            "timestamp": int(time.time() * 1000),
            "chat_type": "t2t",
            "chat_mode": "normal"
        }
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if "chatId" in data:
                    return data["chatId"]
                elif "id" in data:
                    return data["id"]
                elif "data" in data and "chatId" in data["data"]:
                    return data["data"]["chatId"]
        except Exception:
            pass
        # Fallback generated UUID
        import uuid
        return str(uuid.uuid4())

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "qwen3.8-max",
        temperature: float = 0.7,
        chat_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        Streams response text chunks from Qwen Web Chat API v2.
        """
        headers = self._get_headers()
        async with httpx.AsyncClient(headers=headers, timeout=self.timeout, follow_redirects=True) as client:
            if not chat_id:
                chat_id = await self.create_new_chat(client, model=model)

            url = f"{self.base_url}/api/v2/chat/completions?chat_id={chat_id}"
            
            # Format message list
            prompt_content = messages[-1]["content"] if messages else ""

            payload = {
                "stream": True,
                "version": "2.1",
                "incremental_output": True,
                "chatId": chat_id,
                "parentId": "",
                "chat_id": chat_id,
                "chat_mode": "normal",
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt_content,
                        "contentType": "text"
                    }
                ]
            }

            req = client.build_request("POST", url, json=payload)
            res = await client.send(req, stream=True)

            if res.status_code != 200:
                body_peek = await res.aread()
                yield f"[HTTP {res.status_code} Error: {body_peek.decode('utf-8', errors='ignore')[:150]}]"
                return

            async for line in res.aiter_lines():
                if not line:
                    continue
                line = line.strip()
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data_json = json.loads(data_str)
                        delta_content = ""
                        if "choices" in data_json and len(data_json["choices"]) > 0:
                            choice = data_json["choices"][0]
                            if "delta" in choice and "content" in choice["delta"]:
                                delta_content = choice["delta"]["content"] or ""
                            elif "text" in choice:
                                delta_content = choice["text"] or ""
                        elif "content" in data_json:
                            delta_content = data_json["content"] or ""
                        elif "text" in data_json:
                            delta_content = data_json["text"] or ""

                        if delta_content:
                            yield delta_content
                    except json.JSONDecodeError:
                        yield data_str
