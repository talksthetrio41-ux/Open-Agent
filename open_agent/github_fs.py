"""GitHub-backed workspace: clone, read/write/delete, commit, push."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote

from open_agent.config import WORKSPACE_DIR, get_env, write_env

logger = logging.getLogger("GitHubFS")

SAFE_PATH = re.compile(r"^(?!\.\.)[A-Za-z0-9._/@+\-]+$")
MAX_FILE_BYTES = 1_500_000


@dataclass
class GitHubStatus:
    linked: bool
    repo: str
    branch: str
    workspace: str
    dirty: bool
    last_error: str = ""


class GitHubFS:
    def __init__(self, workspace: Path | None = None):
        self.workspace = Path(workspace or WORKSPACE_DIR)
        self.workspace.mkdir(parents=True, exist_ok=True)

    @property
    def token(self) -> str:
        return get_env("GITHUB_TOKEN")

    @property
    def repo(self) -> str:
        return get_env("GITHUB_REPO").strip().lstrip("/")

    def status(self) -> GitHubStatus:
        repo = self.repo
        linked = bool(self.token and repo and (self.workspace / ".git").exists())
        branch = ""
        dirty = False
        err = ""
        if (self.workspace / ".git").exists():
            branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout.strip()
            dirty_out = self._git(["status", "--porcelain"], check=False).stdout.strip()
            dirty = bool(dirty_out)
        elif repo and not self.token:
            err = "GitHub token missing"
        return GitHubStatus(
            linked=linked,
            repo=repo,
            branch=branch or "main",
            workspace=str(self.workspace),
            dirty=dirty,
            last_error=err,
        )

    def configure(self, token: str, repo: str) -> str:
        repo = (repo or "").strip().lstrip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if repo.startswith("https://github.com/"):
            repo = repo[len("https://github.com/") :]
        if repo.startswith("github.com/"):
            repo = repo[len("github.com/") :]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
            raise ValueError("Repo must look like owner/name")
        if not token or len(token) < 8:
            raise ValueError("A GitHub personal access token is required")

        write_env("GITHUB_TOKEN", token)
        write_env("GITHUB_REPO", repo)
        return self.sync(force_clone=True)

    def _authed_url(self) -> str:
        token = quote(self.token, safe="")
        return f"https://x-access-token:{token}@github.com/{self.repo}.git"

    def _public_url(self) -> str:
        return f"https://github.com/{self.repo}.git"

    def _git(self, args: List[str], check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GIT_AUTHOR_NAME", "Open Agent")
        env.setdefault("GIT_AUTHOR_EMAIL", "open-agent@local")
        env.setdefault("GIT_COMMITTER_NAME", "Open Agent")
        env.setdefault("GIT_COMMITTER_EMAIL", "open-agent@local")
        return subprocess.run(
            ["git", *args],
            cwd=str(self.workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def sync(self, force_clone: bool = False) -> str:
        if not self.token or not self.repo:
            return "GitHub is not configured. Add a token and owner/repo in Settings."

        url = self._authed_url()
        git_dir = self.workspace / ".git"

        if force_clone and git_dir.exists():
            # Keep local untracked files if possible; reset remote
            remote = self._git(["remote", "get-url", "origin"], check=False).stdout.strip()
            if remote:
                self._git(["remote", "set-url", "origin", url], check=False)

        if not git_dir.exists() or force_clone and not self._same_repo():
            if self.workspace.exists():
                # Don't wipe unrelated user files outside a git clone of this repo
                if git_dir.exists() and not self._same_repo():
                    shutil.rmtree(self.workspace)
                    self.workspace.mkdir(parents=True, exist_ok=True)
            try:
                if not (self.workspace / ".git").exists():
                    # Clone into workspace (may already have leftover files)
                    if any(self.workspace.iterdir()):
                        tmp = self.workspace.parent / ".workspace_clone_tmp"
                        if tmp.exists():
                            shutil.rmtree(tmp)
                        subprocess.run(
                            ["git", "clone", "--depth", "1", url, str(tmp)],
                            capture_output=True,
                            text=True,
                            timeout=180,
                            check=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                        )
                        for child in tmp.iterdir():
                            dest = self.workspace / child.name
                            if dest.exists():
                                if dest.is_dir():
                                    shutil.rmtree(dest)
                                else:
                                    dest.unlink()
                            shutil.move(str(child), str(dest))
                        shutil.rmtree(tmp, ignore_errors=True)
                    else:
                        subprocess.run(
                            ["git", "clone", "--depth", "1", url, str(self.workspace)],
                            capture_output=True,
                            text=True,
                            timeout=180,
                            check=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                        )
                self._git(["remote", "set-url", "origin", url], check=False)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(
                    f"git clone failed: {(exc.stderr or exc.stdout or str(exc))[-800:]}"
                ) from exc

        self._git(["remote", "set-url", "origin", url], check=False)
        pull = self._git(["pull", "--ff-only", "origin"], check=False)
        msg = f"Linked {self.repo} → {self.workspace}"
        if pull.returncode != 0:
            msg += f"\n(pull note: {(pull.stderr or pull.stdout).strip()[:400]})"
        return msg

    def _same_repo(self) -> bool:
        remote = self._git(["remote", "get-url", "origin"], check=False).stdout.strip()
        if not remote:
            return False
        cleaned = remote.replace("x-access-token:", "").split("@")[-1]
        return self.repo.lower() in cleaned.lower()

    def _resolve(self, rel: str) -> Path:
        rel = (rel or "").strip().lstrip("/")
        if not rel or rel in (".", "./"):
            return self.workspace
        if rel.startswith("..") or Path(rel).is_absolute():
            raise ValueError("Path escapes the workspace")
        # Allow common source characters; still block traversal
        candidate = (self.workspace / rel).resolve()
        workspace = self.workspace.resolve()
        if workspace != candidate and workspace not in candidate.parents:
            raise ValueError("Path escapes the workspace")
        return candidate

    def list_tree(self, rel: str = "", limit: int = 400) -> str:
        root = self._resolve(rel)
        if not root.exists():
            return f"(path not found: {rel or '.'})"
        if root.is_file():
            return f"{rel}  ({root.stat().st_size} bytes)"
        lines: List[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in sorted(dirnames)
                if d not in {".git", "__pycache__", "node_modules", ".venv", "qwen_browser_data"}
            ]
            rel_dir = os.path.relpath(dirpath, self.workspace)
            if rel_dir == ".":
                rel_dir = ""
            for name in sorted(filenames):
                if name.endswith((".pyc", ".png", ".jpg")) and name not in ("favicon.png",):
                    continue
                path = Path(dirpath) / name
                shown = f"{rel_dir}/{name}" if rel_dir else name
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{shown}  ({size} bytes)")
                if len(lines) >= limit:
                    lines.append("… truncated …")
                    return "\n".join(lines)
        return "\n".join(lines) if lines else "(empty directory)"

    def read_file(self, rel: str) -> str:
        path = self._resolve(rel)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(rel)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File too large to read ({path.stat().st_size} bytes)")
        return path.read_text(encoding="utf-8", errors="replace")

    def write_file(self, rel: str, content: str) -> str:
        path = self._resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") or content == "" else content + "\n", encoding="utf-8")
        return f"Wrote {rel} ({path.stat().st_size} bytes)"

    def delete_file(self, rel: str) -> str:
        path = self._resolve(rel)
        if not path.exists():
            return f"(already gone: {rel})"
        if path.is_dir():
            shutil.rmtree(path)
            return f"Deleted directory {rel}"
        path.unlink()
        return f"Deleted {rel}"

    def commit_and_push(self, message: str) -> str:
        if not self.token or not self.repo:
            raise RuntimeError("GitHub is not configured")
        if not (self.workspace / ".git").exists():
            self.sync(force_clone=True)

        self._git(["add", "-A"], check=False)
        status = self._git(["status", "--porcelain"], check=False).stdout.strip()
        if not status:
            return "Nothing to commit."

        msg = (message or "Open Agent update").strip()[:180]
        commit = self._git(["commit", "-m", msg], check=False)
        if commit.returncode != 0:
            combined = (commit.stdout or "") + (commit.stderr or "")
            if "nothing to commit" in combined.lower():
                return "Nothing to commit."
            raise RuntimeError(combined[-800:] or "git commit failed")

        self._git(["remote", "set-url", "origin", self._authed_url()], check=False)
        push = self._git(["push", "-u", "origin", "HEAD"], check=False, timeout=180)
        out = (commit.stdout or "") + "\n" + (push.stdout or push.stderr or "")
        if push.returncode != 0:
            raise RuntimeError(out[-1200:] or "git push failed")
        return out.strip() or f"Pushed to {self.repo}: {msg}"


def parse_github_block(text: str) -> Optional[Tuple[str, str, str]]:
    """Return (action, path, body) if a ```github block is present."""
    if not text:
        return None
    text = text.replace("\xa0", " ")
    match = re.search(
        r"```github(?:\s+(write|delete|read|ls|list|commit))?(?:[ \t]+([^\n`]+))?\n(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    action = (match.group(1) or "write").strip().lower()
    if action == "list":
        action = "ls"
    path = (match.group(2) or "").strip()
    body = match.group(3) or ""
    # Strip accidental language / line-number artifacts
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.isdigit() or stripped in ("github", "text"):
            continue
        lines.append(line)
    return action, path, "\n".join(lines)
