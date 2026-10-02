"""End-to-end tests against a running stack (SPEC.md section 10). Not run by default —
`pytest.ini` excludes `@pytest.mark.real`. Run explicitly with:
    pytest tests/test_api_e2e.py -m real -v
against a live backend (native: http://127.0.0.1:8020, container: http://127.0.0.1:8080/api).
"""
import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8020/api/v1")
TIMEOUT = 200.0

pytestmark = pytest.mark.real


def _session_headers():
    return {"X-Session-Id": str(uuid.uuid4())}


def test_health():
    resp = httpx.get(f"{BASE_URL}/health", timeout=10.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "llm" in body["components"]


def test_conversation_lifecycle():
    headers = _session_headers()
    resp = httpx.post(f"{BASE_URL}/conversations", json={"title": None}, headers=headers, timeout=10.0)
    assert resp.status_code == 200
    conv = resp.json()
    assert "id" in conv

    resp = httpx.get(f"{BASE_URL}/conversations", headers=headers, timeout=10.0)
    assert resp.status_code == 200
    assert any(c["id"] == conv["id"] for c in resp.json())

    resp = httpx.get(f"{BASE_URL}/conversations/{conv['id']}/messages", headers=headers, timeout=10.0)
    assert resp.status_code == 200
    assert resp.json() == []


def test_session_isolation_on_conversations():
    headers_a = _session_headers()
    resp = httpx.post(f"{BASE_URL}/conversations", json={"title": None}, headers=headers_a, timeout=10.0)
    conv = resp.json()

    headers_b = _session_headers()
    resp = httpx.get(f"{BASE_URL}/conversations/{conv['id']}/messages", headers=headers_b, timeout=10.0)
    assert resp.status_code == 404


def test_chat_off_topic_question_streams_sse():
    headers = _session_headers()
    conv = httpx.post(f"{BASE_URL}/conversations", json={"title": None}, headers=headers, timeout=10.0).json()

    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat",
        json={
            "conversation_id": conv["id"],
            "text": "What is the recipe for koshari?",
            "input_mode": "text",
            "lang": "en",
            "doc_ids": [],
        },
        headers=headers,
        timeout=TIMEOUT,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert "event: stage" in body
    assert "event: final" in body
    assert '"status":' in body or '"status": ' in body


def test_documents_upload_and_session_isolation():
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    headers_a = _session_headers()
    with open(os.path.join(fixtures_dir, "support_hours.txt"), "rb") as f:
        resp = httpx.post(
            f"{BASE_URL}/documents",
            files={"file": ("support_hours.txt", f, "text/plain")},
            headers=headers_a,
            timeout=60.0,
        )
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["status"] == "ready"

    resp = httpx.get(f"{BASE_URL}/documents", headers=headers_a, timeout=10.0)
    assert any(d["id"] == doc["id"] for d in resp.json())

    headers_b = _session_headers()
    resp = httpx.get(f"{BASE_URL}/documents", headers=headers_b, timeout=10.0)
    assert all(d["id"] != doc["id"] for d in resp.json())


def test_transcribe_rejects_silence():
    import struct
    import wave
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<16000h", *([0] * 16000)))

    resp = httpx.post(
        f"{BASE_URL}/transcribe",
        files={"file": ("silence.wav", buf.getvalue())},
        timeout=30.0,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rejected"] is True
