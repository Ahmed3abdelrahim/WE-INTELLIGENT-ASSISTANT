#!/usr/bin/env python3
"""GPU soak / leak test against a running native stack (scripts/native_up.sh).

Runs the same mix of inputs repeatedly through every GPU-backed path and records,
after each round: total GPU memory used (nvidia-smi), and per service process host
RSS, open file descriptors and thread count, plus leftover temp files.

Caching allocators (torch, CTranslate2, llama.cpp's KV cache) legitimately grow during
the first rounds while they see each input size for the first time, then must plateau.
So the verdict compares the end of the run with the end of warmup: anything still
climbing after warmup by more than the tolerance is reported as a leak.

Usage: python scripts/gpu_leak_test.py [--rounds 12] [--warmup 3]
"""
import argparse
import asyncio
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import psutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIDFILE = os.path.join(REPO_ROOT, "data", "logs", "native_pids.txt")
BACKEND = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8020/api/v1")
LLM = os.environ.get("LLM_LOCAL_URL", "http://127.0.0.1:8081/v1")
ASR = os.environ.get("ASR_URL", "http://127.0.0.1:8001")
TTS = os.environ.get("TTS_URL", "http://127.0.0.1:8002")

# Tolerances for growth after warmup.
GPU_TOL_MB = 64
RSS_TOL_MB = 150
FD_TOL = 10

LLM_PROMPTS = [
    "Reply with exactly: OK",
    "Summarise in two sentences what a fibre internet plan is.",
    "اكتب جملة واحدة عن خدمة العملاء.",
    "List five common questions telecom customers ask, one per line. " * 8,
]
SPEECH = [
    ("en", "What are the prices of the home internet packages?"),
    ("en", "How can I recharge my mobile balance and check my remaining quota, "
           "and is there a way to transfer credit to another number?"),
    ("ar", "ما هي أسعار باقات الإنترنت المنزلي؟"),
    ("ar", "كيف يمكنني شحن رصيد الموبايل ومعرفة الباقة المتبقية؟"),
]
RAG_QUESTIONS = [
    "What internet packages does WE offer?",
    "ما هي باقات الانترنت المنزلي المتاحة؟",
    "How do I contact WE customer service?",
    "What is the capital of France?",  # off-topic: exercises the abstention path
]


def gpu_used_mb() -> int:
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True
    )
    return int(out.strip().splitlines()[0])


def service_procs() -> dict[str, psutil.Process]:
    procs = {}
    with open(PIDFILE) as f:
        for line in f:
            name, pid = line.strip().split(":")
            try:
                procs[name] = psutil.Process(int(pid))
            except psutil.NoSuchProcess:
                print(f"WARNING: {name} (pid {pid}) is not running")
    return procs


def proc_stats(procs) -> dict:
    stats = {}
    for name, p in procs.items():
        try:
            family = [p] + p.children(recursive=True)
            stats[name] = {
                "rss_mb": round(sum(q.memory_info().rss for q in family) / 2**20, 1),
                "fds": sum(q.num_fds() for q in family),
                "threads": sum(q.num_threads() for q in family),
            }
        except psutil.NoSuchProcess:
            stats[name] = {"rss_mb": None, "fds": None, "threads": None, "dead": True}
    return stats


def tmp_file_count() -> int:
    return len(glob.glob(os.path.join(tempfile.gettempdir(), "tmp*")))


async def llm_call(c: httpx.AsyncClient, prompt: str):
    r = await c.post(f"{LLM}/chat/completions", json={
        "model": "x", "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 128, "temperature": 0.0, "chat_template_kwargs": {"enable_thinking": False},
    })
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    assert "<think>" not in text.lower(), f"<think> leaked into LLM output: {text[:80]!r}"


async def asr_call(c: httpx.AsyncClient, wav: bytes, lang: str | None):
    data = {"beam_size": "1"}
    if lang:
        data["language"] = lang
    r = await c.post(f"{ASR}/transcribe", files={"file": ("q.wav", wav)}, data=data)
    r.raise_for_status()
    return r.json()


async def rag_call(c: httpx.AsyncClient, conv_id: str, sid: str, question: str):
    headers = {"X-Session-Id": sid}
    answer, final = [], None
    async with c.stream("POST", f"{BACKEND}/chat", headers=headers,
                        json={"conversation_id": conv_id, "text": question}) as r:
        r.raise_for_status()
        event = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event:
                data = json.loads(line.split(":", 1)[1])
                if event == "token":
                    answer.append(data["text"])
                elif event == "final":
                    final = data
                elif event == "error":
                    raise RuntimeError(f"/chat error: {data}")
    text = "".join(answer) or (final or {}).get("text", "")
    assert "<think>" not in text.lower(), f"<think> leaked into /chat answer: {text[:80]!r}"
    assert final is not None, "/chat stream ended without a final event"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--warmup", type=int, default=3)
    args = ap.parse_args()

    procs = service_procs()
    async with httpx.AsyncClient(timeout=300.0) as c:
        # Real speech clips for ASR: synthesise them once with the TTS service.
        clips = []
        for lang, text in SPEECH:
            r = await c.post(f"{TTS}/synthesize", json={"text": text, "lang": lang})
            r.raise_for_status()
            clips.append((lang, text, r.content))
        silence = bytes(r.content[:44]) + b"\x00" * 32000  # header of a real wav + 1s of silence

        sid = str(uuid.uuid4())
        r = await c.post(f"{BACKEND}/conversations", headers={"X-Session-Id": sid}, json={"title": "leak test"})
        r.raise_for_status()
        conv_id = r.json()["id"]

        # Sanity check that ASR actually understands the TTS speech (not just "doesn't crash").
        for lang, text, wav in clips:
            out = await asr_call(c, wav, None)
            print(f"[asr check] {lang}: sent {text[:40]!r} -> got {out['text'][:40]!r} (lang={out['language']})")

        async def workload_llm():
            for p in LLM_PROMPTS:
                await llm_call(c, p)

        async def workload_asr():
            for lang, _, wav in clips:
                await asr_call(c, wav, lang)
            await asr_call(c, silence, None)

        async def workload_rag():
            for q in RAG_QUESTIONS:
                # fresh conversation each time so history doesn't grow the prompt round over round
                r = await c.post(f"{BACKEND}/conversations", headers={"X-Session-Id": sid}, json={})
                await rag_call(c, r.json()["id"], sid, q)

        async def workload_mixed():
            await asyncio.gather(workload_llm(), workload_asr(), workload_rag())

        workloads = [("llm", workload_llm), ("asr", workload_asr), ("rag", workload_rag), ("mixed", workload_mixed)]
        history = []
        print(f"\nbaseline: gpu={gpu_used_mb()} MB  tmp files={tmp_file_count()}")
        for rnd in range(1, args.rounds + 1):
            t0 = time.time()
            for name, fn in workloads:
                await fn()
            snap = {"round": rnd, "gpu_mb": gpu_used_mb(), "tmp_files": tmp_file_count(),
                    "procs": proc_stats(procs), "s": round(time.time() - t0, 1)}
            history.append(snap)
            ps = "  ".join(f"{n}:{v['rss_mb']}MB/{v['fds']}fd" for n, v in snap["procs"].items())
            print(f"round {rnd:2d} ({snap['s']:5.1f}s)  gpu={snap['gpu_mb']} MB  tmp={snap['tmp_files']}  {ps}")

    # Verdict: compare last round with the end of warmup.
    base, last = history[args.warmup - 1], history[-1]
    problems = []
    dg = last["gpu_mb"] - base["gpu_mb"]
    print(f"\nGPU memory after warmup: {base['gpu_mb']} -> {last['gpu_mb']} MB ({dg:+d} MB)")
    if dg > GPU_TOL_MB:
        problems.append(f"GPU memory grew {dg} MB after warmup")
    if last["tmp_files"] > base["tmp_files"]:
        problems.append(f"temp files grew {base['tmp_files']} -> {last['tmp_files']}")
    for name in last["procs"]:
        b, l = base["procs"][name], last["procs"][name]
        if l.get("dead"):
            problems.append(f"{name} died during the test")
            continue
        drss, dfd, dthr = l["rss_mb"] - b["rss_mb"], l["fds"] - b["fds"], l["threads"] - b["threads"]
        print(f"{name:8s} rss {b['rss_mb']:8.1f} -> {l['rss_mb']:8.1f} MB ({drss:+.1f})  "
              f"fds {b['fds']} -> {l['fds']} ({dfd:+d})  threads {b['threads']} -> {l['threads']} ({dthr:+d})")
        if drss > RSS_TOL_MB:
            problems.append(f"{name} host RSS grew {drss:.0f} MB after warmup")
        if dfd > FD_TOL:
            problems.append(f"{name} open file descriptors grew by {dfd}")

    out_path = os.path.join(REPO_ROOT, "data", "logs", "gpu_leak_test.json")
    with open(out_path, "w") as f:
        json.dump(history, f, indent=1)
    print(f"\nper-round data: {out_path}")
    if problems:
        print("LEAK SUSPECTED:\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    print("NO LEAKS: GPU memory, host RSS, fds and temp files all plateaued after warmup")


if __name__ == "__main__":
    asyncio.run(main())
