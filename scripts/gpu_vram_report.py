#!/usr/bin/env python3
"""Per-component GPU memory report: what each model needs on its own, idle and at peak.

Measures, one component at a time, from nvidia-smi's per-process numbers (what really
counts against the card, CUDA context included):
  * llm      - the running llama-server (KV cache is pre-allocated, so idle == peak)
  * asr      - the running ASR service, peak while transcribing a ~60 s clip
  * embedder - bge-m3 loaded in THIS process, peak while encoding an ingestion batch
  * reranker - bge-reranker-v2-m3 added in THIS process, peak while reranking 20 passages
The embedder/reranker numbers equal what the backend uses, and again what an in-process
eval run adds on top of a running backend.

Run with the backend STOPPED (its own embedder/reranker would double-count and may not
leave room):  EMBED_DEVICE=cuda python scripts/gpu_vram_report.py
"""
import io
import os
import subprocess
import sys
import threading
import time
import wave

import httpx

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
PIDFILE = os.path.join(REPO_ROOT, "data", "logs", "native_pids.txt")
ASR = os.environ.get("ASR_URL", "http://127.0.0.1:8001")
TTS = os.environ.get("TTS_URL", "http://127.0.0.1:8002")


def per_pid_mib() -> dict[int, int]:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], text=True
    )
    return {int(p): int(m) for p, m in (line.split(", ") for line in out.strip().splitlines() if line)}


def total_mib() -> tuple[int, int]:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], text=True
    )
    used, total = out.strip().split(", ")
    return int(used), int(total)


class PeakSampler:
    """Polls nvidia-smi every 50 ms for one pid's memory while a block runs."""

    def __init__(self, pid: int):
        self.pid, self.peak, self._stop = pid, 0, threading.Event()

    def __enter__(self):
        def loop():
            while not self._stop.is_set():
                self.peak = max(self.peak, per_pid_mib().get(self.pid, 0))
                time.sleep(0.05)
        self._t = threading.Thread(target=loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        self.peak = max(self.peak, per_pid_mib().get(self.pid, 0))


def service_pids() -> dict[str, int]:
    with open(PIDFILE) as f:
        return {n: int(p) for n, p in (line.strip().split(":") for line in f if line.strip())}


def long_speech_wav(seconds: float = 60.0) -> bytes:
    sentence = ("WE Telecom Egypt offers home internet, mobile and WE Air packages. "
                "Customers can recharge their balance and subscribe through the My WE application. ")
    r = httpx.post(f"{TTS}/synthesize", json={"text": sentence * 3, "lang": "en"}, timeout=120)
    r.raise_for_status()
    with wave.open(io.BytesIO(r.content)) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    reps = max(1, int(seconds * params.framerate * params.sampwidth / max(1, len(frames))) + 1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(frames * reps)
    return buf.getvalue()


def main():
    rows = []
    pids = service_pids()
    smi = per_pid_mib()

    llm = smi.get(pids.get("llm"), 0)
    rows.append(("llm (llama-server, Qwen3-4B Q4_K_M, ctx 4096, -ngl 99)", llm, llm))

    asr_pid = pids.get("asr")
    asr_idle = smi.get(asr_pid, 0)
    wav = long_speech_wav(60)
    with PeakSampler(asr_pid) as s:
        r = httpx.post(f"{ASR}/transcribe", files={"file": ("long.wav", wav)}, data={"beam_size": "5"}, timeout=300)
        r.raise_for_status()
    rows.append((f"asr (faster-whisper large-v3-turbo fp16, {r.json()['duration_s']:.0f}s clip, beam 5)", asr_idle, s.peak))

    me = os.getpid()
    mem = lambda: per_pid_mib().get(me, 0)  # noqa: E731
    from app.config import config
    from app.retrieval import embed

    assert config.EMBED_DEVICE.startswith("cuda"), "run with EMBED_DEVICE=cuda"
    # FlagEmbedding moves a model to the GPU on first use, so warm up before reading "loaded".
    embed.encode(["warm up"])
    emb_loaded = mem()
    chunk = "باقات WE Air هي باقات إنترنت هوائي. WE Air packages include 20 GB for 150 EGP. " * 30  # ~450 tokens
    with PeakSampler(me) as s:
        embed.encode([chunk] * 32)  # one ingestion batch of full-size chunks
    emb_peak = s.peak
    rows.append(("embedder (bge-m3 fp16, batch of 32 x ~450-token chunks)", emb_loaded, emb_peak))

    after_emb = mem()  # torch keeps its cache: this is what the process now holds
    embed.rerank("warm up", ["warm up"])
    rr_loaded = mem() - after_emb
    with PeakSampler(me) as s:
        embed.rerank("How much is WE Air 150?", [chunk] * 20)
    rows.append(("reranker (bge-reranker-v2-m3 fp16, 20 passages) [on top of embedder]", rr_loaded, s.peak - after_emb))
    backend_peak = max(emb_peak, s.peak)  # one process holds both models + torch's cache

    used, total = total_mib()
    print(f"\nGPU: {total} MiB total, {used} MiB in use right now\n")
    print(f"{'component':74s} {'loaded MiB':>10s} {'peak MiB':>9s}")
    for name, idle, peak in rows:
        print(f"{name:74s} {idle:10d} {peak:9d}")
    app_peak = rows[0][2] + rows[1][2] + backend_peak
    print(f"\n{'backend process at peak (embedder + reranker + torch cache)':74s} {'':10s} {backend_peak:9d}")
    print(f"{'whole app stack at peak (llm + asr + backend)':74s} {'':10s} {app_peak:9d}")
    print(f"{'+ eval running alongside (a second embedder + reranker in-process)':74s} {'':10s} {app_peak + backend_peak:9d}")


if __name__ == "__main__":
    main()
