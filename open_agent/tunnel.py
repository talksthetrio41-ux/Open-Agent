"""Launch a Cloudflare quick tunnel and capture the public HTTPS URL."""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from open_agent.config import RUNTIME_DIR, find_cloudflared

logger = logging.getLogger("Tunnel")

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


class CloudflareTunnel:
    def __init__(self, local_port: int):
        self.local_port = local_port
        self.proc: Optional[subprocess.Popen] = None
        self.url: str = ""
        self._buf: list[str] = []

    def start(self, timeout: float = 45.0) -> str:
        binary = find_cloudflared()
        if not binary:
            raise RuntimeError(
                "cloudflared not found. On Termux: pkg install cloudflared "
                "(enable tur-repo first). On desktop: install cloudflared."
            )
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        log_path = RUNTIME_DIR / "cloudflared.log"
        cmd = [
            binary,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{self.local_port}",
            "--no-autoupdate",
        ]
        logger.info("Starting tunnel: %s", " ".join(cmd))
        log_file = open(log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def _pump() -> None:
            assert self.proc and self.proc.stdout
            for line in self.proc.stdout:
                self._buf.append(line)
                try:
                    log_file.write(line)
                    log_file.flush()
                except Exception:
                    pass
                match = URL_RE.search(line)
                if match and not self.url:
                    self.url = match.group(0)
                    logger.info("Tunnel URL: %s", self.url)

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()

        deadline = time.time() + timeout
        while time.time() < deadline and not self.url:
            if self.proc.poll() is not None:
                tail = "".join(self._buf[-20:])
                raise RuntimeError(f"cloudflared exited early:\n{tail}")
            time.sleep(0.2)

        if not self.url:
            # Last chance: read the log file
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
                match = URL_RE.search(text)
                if match:
                    self.url = match.group(0)
            except Exception:
                pass
        if not self.url:
            raise RuntimeError(
                "Timed out waiting for trycloudflare.com URL. "
                f"See {log_path}"
            )
        return self.url

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


def which_or_hint() -> str:
    path = find_cloudflared()
    if path:
        return path
    extra = shutil.which("cloudflared")
    return extra or ""
