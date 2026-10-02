#!/usr/bin/env python3
"""Phase 0 exit check: one real call per service, no mocks.

Hits whatever stack is currently up (container or native — same ports either way,
127.0.0.1 by default) and records per-call latency. Writes a timing line per
component to data/logs/timings.jsonl and prints a pass/fail summary.
"""
import io
import json
import os
import sys
import time
import wave
import struct
import math

import httpx

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8020")
LLM_URL = os.environ.get("LLM_LOCAL_URL", "http://127.0.0.1:8080/v1")
ASR_URL = os.environ.get("ASR_URL", "http://127.0.0.1:8001")
TTS_URL = os.environ.get("TTS_URL", "http://127.0.0.1:8002")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3-4b")

LOG_PATH = os.path.join(REPO_ROOT, "data", "logs", "timings.jsonl")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

results = []


def log_timing(stage, ms, extra=None):
    entry = {"stage": stage, "ms": round(ms, 1), "ts": time.time(), **(extra or {})}
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def check(name, fn):
    t0 = time.time()
    try:
        detail = fn()
        ms = (time.time() - t0) * 1000
        results.append((name, True, detail, ms))
        log_timing(f"smoke_{name}", ms)
        print(f"[OK]   {name} ({ms:.0f} ms): {detail}")
    except Exception as e:  # noqa: BLE001
        ms = (time.time() - t0) * 1000
        results.append((name, False, str(e), ms))
        print(f"[FAIL] {name} ({ms:.0f} ms): {e}")


def make_sine_wav(seconds=2.0, freq=440.0, sr=16000) -> bytes:
    n = int(seconds * sr)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            val = int(3000 * math.sin(2 * math.pi * freq * i / sr))
            frames += struct.pack("<h", val)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def smoke_llm():
    payload = {
        "model": os.environ.get("LLM_LOCAL_MODEL", "qwen3-4b-q4_k_m"),
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 32,
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    resp = httpx.post(f"{LLM_URL}/chat/completions", json=payload, timeout=60.0)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    if "<think>" in content.lower():
        raise AssertionError(f"found <think> in output: {content!r}")
    return f"content={content!r}"


def smoke_asr():
    wav_bytes = make_sine_wav()
    files = {"file": ("tone.wav", wav_bytes)}
    resp = httpx.post(f"{ASR_URL}/transcribe", files=files, data={"beam_size": "1"}, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()
    return f"text={data.get('text')!r} no_speech_prob={data.get('no_speech_prob')} (synthetic sine tone, not speech)"


def smoke_tts():
    resp = httpx.post(f"{TTS_URL}/synthesize", json={"text": "Hello from WE Assistant.", "lang": "en"}, timeout=60.0)
    resp.raise_for_status()
    if resp.headers.get("content-type", "").split(";")[0] != "audio/wav" and not resp.content[:4] == b"RIFF":
        raise AssertionError("response is not a WAV file")
    return f"{len(resp.content)} bytes of audio/wav"


def smoke_qdrant():
    resp = httpx.get(f"{QDRANT_URL}/healthz", timeout=10.0)
    resp.raise_for_status()
    return resp.text.strip()


def smoke_backend():
    resp = httpx.get(f"{BACKEND_URL}/health", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def smoke_openrouter():
    if not OPENROUTER_API_KEY:
        return "SKIPPED: OPENROUTER_API_KEY not set"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 16,
        "reasoning": {"enabled": False, "exclude": True},
    }
    resp = httpx.post(f"{OPENROUTER_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=60.0)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    if "<think>" in content.lower():
        raise AssertionError(f"found <think> in output: {content!r}")
    return f"content={content!r}"


def main():
    check("qdrant", smoke_qdrant)
    check("llm_local", smoke_llm)
    check("asr", smoke_asr)
    check("tts", smoke_tts)
    check("backend_health", smoke_backend)
    check("openrouter", smoke_openrouter)

    print("\n--- summary ---")
    failed = [r for r in results if not r[1] and not (isinstance(r[2], str) and r[2].startswith("SKIPPED"))]
    for name, ok, detail, ms in results:
        status = "SKIP" if (isinstance(detail, str) and detail.startswith("SKIPPED")) else ("PASS" if ok else "FAIL")
        print(f"  {name}: {status}")
    if failed:
        print(f"\n{len(failed)} check(s) failed")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
