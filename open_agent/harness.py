"""Parse tool blocks and execute them on the Android/Termux sandbox."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from open_agent.config import (
    DEFAULT_COMMAND_TIMEOUT,
    MAX_OUTPUT_CHARS,
    WORKSPACE_DIR,
    get_env,
)
from open_agent.github_fs import GitHubFS, parse_github_block

logger = logging.getLogger("AgentHarness")

BASH_FENCE = re.compile(r"```(?:bash|sh|shell|zsh|termux)?\n(.*?)```", re.DOTALL | re.IGNORECASE)
UI_BASH = re.compile(r"(?:^|\n)(?:bash|sh|shell)\n((?:\d+\n)+)(.*)", re.DOTALL | re.IGNORECASE)


@dataclass
class ToolResult:
    kind: str
    command: str
    output: str
    ok: bool = True


class AgentHarness:
    def __init__(self, sandbox_dir: Optional[str] = None):
        self.sandbox_dir = os.path.abspath(sandbox_dir or str(WORKSPACE_DIR))
        os.makedirs(self.sandbox_dir, exist_ok=True)
        self.fs = GitHubFS()

    def extract_bash_command(self, text: str) -> Optional[str]:
        if not text:
            return None
        text = text.replace("\xa0", " ")
        match = BASH_FENCE.search(text)
        if match:
            return self._clean_code(match.group(1))
        match_ui = UI_BASH.search(text)
        if match_ui:
            return self._clean_code(match_ui.group(2))
        return None

    @staticmethod
    def _clean_code(raw: str) -> str:
        lines = []
        for line in (raw or "").splitlines():
            stripped = line.strip()
            if stripped.isdigit() or stripped.lower() in ("bash", "sh", "shell", "zsh", "termux"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def is_done(self, text: str) -> bool:
        return bool(text) and "<DONE>" in text

    def is_compact_ready(self, text: str) -> bool:
        return bool(text) and "<COMPACT_READY>" in text

    def first_tool(self, text: str) -> Optional[ToolResult]:
        """Prefer a github block if present, otherwise the first bash block."""
        gh = parse_github_block(text or "")
        bash = self.extract_bash_command(text or "")
        if gh and bash:
            gh_pos = (text or "").lower().find("```github")
            bash_pos = (text or "").find("```")
            if gh_pos != -1 and (bash_pos == -1 or gh_pos <= bash_pos):
                return ToolResult(kind="github", command=f"{gh[0]} {gh[1]}".strip(), output="")
            return ToolResult(kind="bash", command=bash, output="")
        if gh:
            return ToolResult(kind="github", command=f"{gh[0]} {gh[1]}".strip(), output="")
        if bash:
            return ToolResult(kind="bash", command=bash, output="")
        return None

    def run_tool(self, text: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[ToolResult]:
        gh = parse_github_block(text or "")
        bash = self.extract_bash_command(text or "")
        if gh and bash:
            gh_pos = (text or "").lower().find("```github")
            bash_pos = (text or "").find("```")
            if gh_pos != -1 and (bash_pos == -1 or gh_pos <= bash_pos):
                return self.execute_github(*gh)
            return self.execute_command(bash, timeout=timeout)
        if gh:
            return self.execute_github(*gh)
        if bash:
            return self.execute_command(bash, timeout=timeout)
        return None

    def execute_github(self, action: str, path: str, body: str) -> ToolResult:
        action = (action or "write").lower()
        try:
            if action == "write":
                out = self.fs.write_file(path, body)
            elif action == "delete":
                out = self.fs.delete_file(path)
            elif action == "read":
                out = self.fs.read_file(path)
            elif action == "ls":
                out = self.fs.list_tree(path)
            elif action == "commit":
                message = (path or body or "Open Agent update").strip()
                out = self.fs.commit_and_push(message)
            else:
                return ToolResult(kind="github", command=f"{action} {path}", output=f"Unknown github action: {action}", ok=False)
            return ToolResult(kind="github", command=f"{action} {path}".strip(), output=out, ok=True)
        except Exception as exc:
            logger.exception("github tool failed")
            return ToolResult(kind="github", command=f"{action} {path}".strip(), output=f"[GitHub Error: {exc}]", ok=False)

    def execute_command(self, command: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> ToolResult:
        os.makedirs(self.sandbox_dir, exist_ok=True)
        logger.info("Executing in %s:\n%s", self.sandbox_dir, command)
        env = os.environ.copy()
        token = get_env("GITHUB_TOKEN")
        if token:
            env.setdefault("GITHUB_TOKEN", token)
            env.setdefault("GH_TOKEN", token)
        try:
            process = subprocess.run(
                command,
                shell=True,
                cwd=self.sandbox_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = process.stdout or ""
            if process.stderr:
                output += ("\n" if output else "") + "[stderr]\n" + process.stderr
            if process.returncode != 0:
                output += f"\n[Exit Code: {process.returncode}]"
            if not output.strip():
                output = "(Command executed successfully with no output)"
            output = self._truncate(output)
            return ToolResult(kind="bash", command=command, output=output, ok=process.returncode == 0)
        except subprocess.TimeoutExpired:
            return ToolResult(
                kind="bash",
                command=command,
                output=f"[Execution Error: Command timed out after {timeout} seconds]",
                ok=False,
            )
        except Exception as exc:
            return ToolResult(kind="bash", command=command, output=f"[Execution Error: {exc}]", ok=False)

    @staticmethod
    def _truncate(output: str) -> str:
        if len(output) <= MAX_OUTPUT_CHARS:
            return output
        keep = MAX_OUTPUT_CHARS // 2
        return output[:keep] + "\n\n... [Output Truncated] ...\n\n" + output[-keep:]
