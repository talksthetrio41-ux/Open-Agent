"""Regression test: Errno 98 (address already in use) on restart.

A crashed/killed `oa` left a server process holding port 8765, so the next
launch died with '[Errno 98] address already in use' — and the Cloudflare
tunnel would have served the STALE instance. __main__ must reclaim the port
before starting uvicorn and before the tunnel thread fires.
"""

import multiprocessing
import socket
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_SRC = (ROOT / "open_agent" / "__main__.py").read_text(encoding="utf-8")


def _hold_port(port: int) -> None:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.listen(1)
    time.sleep(30)


def test_free_port_kills_stale_listener():
    from open_agent.__main__ import _free_port, _listener_pids, _port_busy

    port = 18766
    proc = multiprocessing.Process(target=_hold_port, args=(port,))
    proc.start()
    try:
        deadline = time.time() + 5
        while not _port_busy(port) and time.time() < deadline:
            time.sleep(0.1)
        assert _port_busy(port)
        assert proc.pid in _listener_pids(port)
        assert _free_port(port) is True
        assert not _port_busy(port)
    finally:
        proc.terminate()


def test_port_reclaimed_before_tunnel_and_uvicorn():
    free_idx = MAIN_SRC.find("_free_port(args.port)")
    tunnel_idx = MAIN_SRC.find("use_tunnel =")
    uvicorn_idx = MAIN_SRC.find("uvicorn.run(")
    assert free_idx != -1
    assert free_idx < tunnel_idx < uvicorn_idx


def test_no_reuseaddr_in_busy_probe():
    probe = MAIN_SRC.split("def _port_busy", 1)[1].split("def ", 1)[0]
    assert "setsockopt" not in probe
