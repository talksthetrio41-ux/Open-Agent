"""In-memory agent session: history, status, compact/clear."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _now() -> float:
    return time.time()


@dataclass
class ChatMessage:
    role: str
    content: str
    ts: float = field(default_factory=_now)
    kind: str = "text"  # text | tool | system
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "ts": self.ts,
            "kind": self.kind,
            "meta": self.meta,
        }


class AgentSession:
    def __init__(self) -> None:
        self.messages: List[ChatMessage] = []
        self.status: str = "idle"  # idle | connecting | running | compacting | error
        self.status_detail: str = ""
        self.step: int = 0
        self.max_steps: int = 20
        self.busy: bool = False
        self.last_error: str = ""
        self.qwen_connected: bool = False
        self.qwen_username: str = ""
        self.compact_summary: str = ""
        self.created_at: float = _now()

    def add(self, role: str, content: str, kind: str = "text", **meta: Any) -> ChatMessage:
        msg = ChatMessage(role=role, content=content, kind=kind, meta=meta)
        self.messages.append(msg)
        return msg

    def history(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self.messages]

    def clear(self) -> None:
        self.messages.clear()
        self.step = 0
        self.status = "idle"
        self.status_detail = "Chat cleared."
        self.last_error = ""
        self.compact_summary = ""
        self.busy = False

    def snapshot(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "status_detail": self.status_detail,
            "step": self.step,
            "max_steps": self.max_steps,
            "busy": self.busy,
            "last_error": self.last_error,
            "qwen_connected": self.qwen_connected,
            "qwen_username": self.qwen_username,
            "message_count": len(self.messages),
            "has_compact": bool(self.compact_summary),
        }
