"""Autonomous agent loop: Qwen chat ↔ local tools."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from open_agent.config import DEFAULT_COMMAND_TIMEOUT, MAX_AGENT_STEPS, get_env
from open_agent.harness import AgentHarness
from open_agent.prompts import COMPACT_PROMPT, RESUME_PREAMBLE, SYSTEM_PROMPT, WELCOME_HINT
from open_agent.qwen_browser import QwenBrowserAutomator
from open_agent.session import AgentSession

logger = logging.getLogger("OpenAgent")

FEEDBACK = (
    "Tool result ({kind}):\n```\n{output}\n```\n"
    "Inspect this output and continue. Emit exactly one tool block, "
    "or finish with <DONE>."
)


class OpenAgent:
    def __init__(self) -> None:
        self.session = AgentSession()
        self.harness = AgentHarness()
        self.automator = QwenBrowserAutomator(headless=True)
        self._run_lock = asyncio.Lock()
        self._cancel = asyncio.Event()

    def public_state(self) -> Dict[str, Any]:
        gh = self.harness.fs.status()
        state = self.session.snapshot()
        state.update(
            {
                "github_linked": gh.linked,
                "github_repo": gh.repo,
                "github_branch": gh.branch,
                "github_dirty": gh.dirty,
                "workspace": gh.workspace,
                "welcome": WELCOME_HINT,
                "has_qwen_creds": bool(get_env("QWEN_USERNAME") and get_env("QWEN_PASSWORD")),
            }
        )
        return state

    async def connect_qwen(self, username: str, password: str) -> Dict[str, str]:
        self.session.status = "connecting"
        self.session.status_detail = "Signing in to chat.qwen.ai…"
        try:
            result = await self.automator.login(username, password)
            ok = result.get("ok") == "true"
            self.session.qwen_connected = ok
            self.session.qwen_username = username
            self.session.status = "idle" if ok else "error"
            self.session.status_detail = (
                "Connected to Qwen." if ok else "Login did not complete. Check email/password or captcha."
            )
            self.session.last_error = "" if ok else self.session.status_detail
            return result
        except Exception as exc:
            self.session.status = "error"
            self.session.status_detail = str(exc)
            self.session.last_error = str(exc)
            self.session.qwen_connected = False
            raise

    async def ensure_browser(self) -> None:
        await self.automator.start()
        if get_env("QWEN_USERNAME"):
            self.session.qwen_username = get_env("QWEN_USERNAME")
            self.session.qwen_connected = True

    async def clear(self) -> None:
        self._cancel.set()
        await self.automator.reset_chat()
        self.session.clear()
        self.session.add("system", "Chat cleared. New Qwen thread started.", kind="system")

    async def compact(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Ask the model to summarize, then reset the Qwen thread and seed the summary."""
        if self.session.busy:
            yield {"type": "error", "text": "Agent is busy. Wait or Clear first."}
            return
        if not self.session.messages:
            yield {"type": "error", "text": "Nothing to compact yet."}
            return

        async with self._run_lock:
            self.session.busy = True
            self.session.status = "compacting"
            self.session.status_detail = "Asking Qwen to compact history…"
            yield {"type": "status", "state": self.public_state()}
            try:
                summary = ""
                async for chunk in self.automator.stream_chat_browser(COMPACT_PROMPT, timeout_sec=120):
                    summary += chunk
                    yield {"type": "delta", "role": "assistant", "text": chunk, "kind": "compact"}
                summary = summary.strip()
                self.session.compact_summary = summary
                self.session.add("assistant", summary, kind="compact")
                yield {"type": "compacted", "text": summary}

                await self.automator.reset_chat()
                seed = RESUME_PREAMBLE + summary
                # Seed the new Qwen thread so the next user turn has context
                async for _chunk in self.automator.stream_chat_browser(seed, timeout_sec=90):
                    pass
                self.session.messages.clear()
                self.session.add("system", "History compacted into a fresh Qwen chat.", kind="system")
                self.session.add("assistant", summary, kind="compact")
                self.session.step = 0
                self.session.status = "idle"
                self.session.status_detail = "Compact complete. Continuing in a new chat."
                yield {"type": "status", "state": self.public_state()}
                yield {"type": "done", "reason": "compact"}
            except Exception as exc:
                logger.exception("compact failed")
                self.session.status = "error"
                self.session.last_error = str(exc)
                yield {"type": "error", "text": str(exc)}
            finally:
                self.session.busy = False

    async def run_user_task(self, task: str, max_steps: Optional[int] = None) -> AsyncGenerator[Dict[str, Any], None]:
        if self.session.busy:
            yield {"type": "error", "text": "Agent is already running."}
            return
        task = (task or "").strip()
        if not task:
            yield {"type": "error", "text": "Empty message."}
            return

        async with self._run_lock:
            self._cancel.clear()
            self.session.busy = True
            self.session.max_steps = max_steps or MAX_AGENT_STEPS
            self.session.add("user", task)
            yield {"type": "user", "text": task}
            try:
                await self.ensure_browser()
                is_first = self.session.step == 0 and len(
                    [m for m in self.session.messages if m.role == "user"]
                ) == 1
                if is_first:
                    prompt = f"{SYSTEM_PROMPT}\n\n### Task to solve:\n{task}"
                else:
                    prompt = task
                async for event in self._loop(prompt):
                    yield event
            except Exception as exc:
                logger.exception("agent run failed")
                self.session.status = "error"
                self.session.last_error = str(exc)
                self.session.add("system", f"Error: {exc}", kind="system")
                yield {"type": "error", "text": str(exc)}
            finally:
                self.session.busy = False
                if self.session.status == "running":
                    self.session.status = "idle"
                    self.session.status_detail = "Idle."
                yield {"type": "status", "state": self.public_state()}

    async def _loop(self, first_prompt: str) -> AsyncGenerator[Dict[str, Any], None]:
        current = first_prompt
        while self.session.step < self.session.max_steps:
            if self._cancel.is_set():
                self.session.status = "idle"
                self.session.status_detail = "Cancelled."
                yield {"type": "done", "reason": "cancelled"}
                return

            self.session.step += 1
            self.session.status = "running"
            self.session.status_detail = f"Qwen thinking — step {self.session.step}/{self.session.max_steps}"
            yield {"type": "status", "state": self.public_state()}
            yield {"type": "step", "step": self.session.step}

            full = ""
            async for chunk in self.automator.stream_chat_browser(current, timeout_sec=180):
                if self._cancel.is_set():
                    break
                full += chunk
                yield {"type": "delta", "role": "assistant", "text": chunk}

            full = full.strip()
            if full:
                self.session.add("assistant", full)
                yield {"type": "assistant_done", "text": full}

            if self.harness.is_done(full):
                self.session.status = "idle"
                self.session.status_detail = "Task complete."
                yield {"type": "done", "reason": "done"}
                return

            result = self.harness.run_tool(full, timeout=DEFAULT_COMMAND_TIMEOUT)
            if result:
                yield {
                    "type": "tool",
                    "kind": result.kind,
                    "command": result.command,
                    "output": result.output,
                    "ok": result.ok,
                }
                self.session.add(
                    "system",
                    result.output,
                    kind="tool",
                    tool=result.kind,
                    command=result.command,
                    ok=result.ok,
                )
                current = FEEDBACK.format(kind=result.kind, output=result.output)
                continue

            # No tool and not done — stop and wait for the user
            self.session.status = "idle"
            self.session.status_detail = "Waiting for you."
            yield {"type": "done", "reason": "awaiting_user"}
            return

        self.session.status = "idle"
        self.session.status_detail = "Hit max steps. Compact or continue."
        yield {"type": "done", "reason": "max_steps"}
