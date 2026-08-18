from fastapi.testclient import TestClient

from open_agent.config import ensure_access_pin
from open_agent.server import app


def test_health_and_index():
    client = TestClient(app)
    assert client.get("/api/health").json()["ok"] is True
    page = client.get("/")
    assert page.status_code == 200
    assert "Open Agent" in page.text
    assert "/static/app.js" in page.text


def test_pin_gate_and_unlock():
    client = TestClient(app)
    locked = client.get("/api/state")
    assert locked.status_code == 401
    pin = ensure_access_pin()
    bad = client.post("/api/unlock", json={"pin": "00000000-wrong"})
    assert bad.status_code == 403
    ok = client.post("/api/unlock", json={"pin": pin})
    assert ok.status_code == 200
    state = client.get("/api/state")
    assert state.status_code == 200
    body = state.json()
    assert "github_linked" in body
    assert "messages" in body


def test_unlock_sets_secure_cookie_behind_https_proxy():
    client = TestClient(app)
    pin = ensure_access_pin()
    ok = client.post(
        "/api/unlock",
        json={"pin": pin},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert ok.status_code == 200
    set_cookie = ",".join(ok.headers.get_list("set-cookie")).lower()
    assert "oa_session=" in set_cookie
    assert "secure" in set_cookie
