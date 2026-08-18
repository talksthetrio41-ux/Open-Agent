"""Optional terminal chat (no GUI). Prefer `python -m open_agent` on Termux."""

from __future__ import annotations

import argparse
import asyncio
import sys

from open_agent.agent import OpenAgent
from open_agent.config import ensure_dirs, load_env


async def _run(task: str | None) -> int:
    load_env()
    ensure_dirs()
    agent = OpenAgent()
    if not task:
        task = input("Task: ").strip()
    if not task:
        print("Empty task.")
        return 1
    async for event in agent.run_user_task(task):
        kind = event.get("type")
        if kind == "delta":
            print(event.get("text", ""), end="", flush=True)
        elif kind == "tool":
            print(f"\n\n[tool:{event.get('kind')}] {event.get('command')}\n{event.get('output')}\n")
        elif kind == "error":
            print(f"\n[error] {event.get('text')}")
        elif kind == "done":
            print(f"\n[{event.get('reason')}]")
    await agent.automator.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Agent CLI")
    parser.add_argument("--task", "-t", help="Task to run")
    args = parser.parse_args()
    return asyncio.run(_run(args.task))


if __name__ == "__main__":
    sys.exit(main())
