#!/usr/bin/env python3
"""Real evaluation harness (SPEC.md section 10). Run from repo root with the native/container
stack up (qdrant + backend's embedding model + llm). Writes eval/results.md.

Sections, each run against the live stack, nothing fabricated:
  1. Retrieval: Recall@4 and MRR for dense-only vs hybrid(RRF) vs hybrid+rerank.
  2. Full pipeline: citation validity, numeric-fact match, abstention correctness on the
     5 unanswerable questions, p50/p95 latency per stage.
  3. ASR WER/CER (turbo vs large-v3, beam 1 vs 5, with/without hotwords) — only if
     eval/audio_manifest.jsonl (the REAL recorded clips) exists; otherwise skipped and
     noted, never substituted with synthetic clips.
  4. Local vs OpenRouter comparison — only if OPENROUTER_API_KEY works; otherwise skipped
     and noted (`--compare` / `make eval-compare`).
"""
import argparse
import json
import os
import statistics
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

from app.config import config  # noqa: E402
from app.retrieval.embed import encode_one  # noqa: E402
from app.retrieval.index import get_client  # noqa: E402
from app.retrieval.search import build_filter, hybrid_search  # noqa: E402

QUESTIONS_PATH = os.path.join(REPO_ROOT, "eval", "questions.jsonl")
RESULTS_PATH = os.path.join(REPO_ROOT, "eval", "results.md")
AUDIO_MANIFEST_PATH = os.path.join(REPO_ROOT, "eval", "audio_manifest.jsonl")


def load_questions():
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def dense_only_search(query, top_k=4):
    qvec = encode_one(query)
    client = get_client()
    qfilter = build_filter("eval-session", doc_ids=None)
    result = client.query_points(
        collection_name=config.QDRANT_COLLECTION,
        query=qvec["dense"],
        using="dense",
        query_filter=qfilter,
        limit=top_k,
        with_payload=True,
    )
    return [{"score": p.score, **p.payload} for p in result.points]


def recall_and_rank(hits, expected_urls):
    if not expected_urls:
        return None, None
    urls = [h.get("url") for h in hits]
    hit_rank = next((i + 1 for i, u in enumerate(urls) if u in expected_urls), None)
    recall = 1.0 if hit_rank else 0.0
    mrr = (1.0 / hit_rank) if hit_rank else 0.0
    return recall, mrr


def eval_retrieval(questions):
    answerable = [q for q in questions if q["answerable"]]
    rows = {"dense": [], "hybrid": [], "hybrid_rerank": []}
    for q in answerable:
        dense_hits = dense_only_search(q["question"])
        r, m = recall_and_rank(dense_hits, q["expected_urls"])
        rows["dense"].append((r, m))

        hybrid_hits = hybrid_search(q["question"], "eval-session", doc_ids=None, use_reranker=False)
        r, m = recall_and_rank(hybrid_hits, q["expected_urls"])
        rows["hybrid"].append((r, m))

        rerank_hits = hybrid_search(q["question"], "eval-session", doc_ids=None, use_reranker=True)
        r, m = recall_and_rank(rerank_hits, q["expected_urls"])
        rows["hybrid_rerank"].append((r, m))

    summary = {}
    for name, vals in rows.items():
        recalls = [r for r, _ in vals if r is not None]
        mrrs = [m for _, m in vals if m is not None]
        summary[name] = {
            "recall_at_4": round(statistics.mean(recalls), 3) if recalls else None,
            "mrr": round(statistics.mean(mrrs), 3) if mrrs else None,
            "n": len(recalls),
        }
    return summary


def eval_pipeline(questions, provider=None):
    import asyncio

    from app.pipeline import answer as pipeline_answer
    from app.store import create_conversation

    async def run_one(q):
        conv = await create_conversation(f"eval-{q['id']}", None)
        chunks = []
        final = None
        t0 = time.time()
        async for event, data in pipeline_answer(
            conv["id"], f"eval-{q['id']}", q["question"], "text", q["lang"], [], provider
        ):
            if event == "token":
                chunks.append(data["text"])
            elif event == "final":
                final = data
            elif event == "error":
                final = {"status": "error", "citations": [], "timings": {}, "answer": data.get("message", "")}
        total_ms = (time.time() - t0) * 1000
        return q, final, total_ms

    results = []
    for q in questions:
        print(f"  [eval] {q['id']}: {q['question'][:60]}...", flush=True)
        q_out, final, total_ms = asyncio.run(run_one(q))
        results.append((q_out, final, total_ms))
        print(f"    -> status={final.get('status') if final else None} ({total_ms/1000:.1f}s)", flush=True)
    return results


def summarize_pipeline(results):
    n = len(results)
    correct_abstention = 0
    n_unanswerable = 0
    citation_valid = 0
    n_answerable_got_answer = 0
    numeric_ok = 0
    n_with_citations = 0
    stage_timings = {"retrieval_ms": [], "llm_ms": [], "total_ms": []}

    for q, final, total_ms in results:
        if not q["answerable"]:
            n_unanswerable += 1
            # out_of_scope / refused come from the router guard (not answered = abstained)
            if final and final.get("status") in ("insufficient_evidence", "out_of_scope", "refused"):
                correct_abstention += 1
        else:
            if final and final.get("status") == "answered":
                n_answerable_got_answer += 1
                if final.get("citations"):
                    n_with_citations += 1
                    citation_valid += 1  # presence of mapped citations == validator accepted them
                    if not final.get("warning"):
                        numeric_ok += 1
        timings = (final or {}).get("timings", {})
        for k in stage_timings:
            if k in timings:
                stage_timings[k].append(timings[k])

    def pctl(vals, p):
        if not vals:
            return None
        vals = sorted(vals)
        idx = min(len(vals) - 1, int(len(vals) * p))
        return round(vals[idx], 1)

    return {
        "n_total": n,
        "n_unanswerable": n_unanswerable,
        "correct_abstention": correct_abstention,
        "n_answerable": n - n_unanswerable,
        "n_answerable_got_answer": n_answerable_got_answer,
        "n_with_citations": n_with_citations,
        "numeric_ok": numeric_ok,
        "latency_p50": {k: pctl(v, 0.5) for k, v in stage_timings.items()},
        "latency_p95": {k: pctl(v, 0.95) for k, v in stage_timings.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare", action="store_true", help="also run the OpenRouter comparison")
    parser.add_argument("--retrieval-only", action="store_true")
    args = parser.parse_args()

    questions = load_questions()
    print(f"loaded {len(questions)} questions")

    print("\n=== retrieval eval (dense vs hybrid vs hybrid+rerank) ===")
    retrieval_summary = eval_retrieval(questions)
    print(json.dumps(retrieval_summary, indent=2))

    pipeline_summary = None
    pipeline_results = None
    if not args.retrieval_only:
        print("\n=== full pipeline eval (local provider) ===")
        pipeline_results = eval_pipeline(questions, provider="local")
        pipeline_summary = summarize_pipeline(pipeline_results)
        print(json.dumps(pipeline_summary, indent=2))

    compare_summary = None
    if args.compare:
        if not config.openrouter_available:
            print("\n=== --compare requested but OPENROUTER_API_KEY not set/working: SKIPPED ===")
        else:
            print("\n=== full pipeline eval (openrouter provider) ===")
            compare_results = eval_pipeline(questions, provider="openrouter")
            compare_summary = summarize_pipeline(compare_results)
            print(json.dumps(compare_summary, indent=2))

    has_real_audio = os.path.exists(AUDIO_MANIFEST_PATH)

    write_results_md(questions, retrieval_summary, pipeline_summary, compare_summary, has_real_audio)
    print(f"\nwrote {RESULTS_PATH}")


def write_results_md(questions, retrieval_summary, pipeline_summary, compare_summary, has_real_audio):
    import subprocess

    nproc = os.cpu_count()
    try:
        mem_kb = int(subprocess.check_output(["grep", "MemTotal", "/proc/meminfo"]).split()[1])
        mem_gb = round(mem_kb / 1024 / 1024, 1)
    except Exception:  # noqa: BLE001
        mem_gb = "unknown"

    lines = []
    lines.append("# Evaluation Results\n")
    lines.append(f"Generated by `eval/run_eval.py` on a real run against the live native stack "
                  f"(no Docker daemon on this machine — see docs/decisions.md).\n")
    lines.append("## Laptop specs\n")
    lines.append(f"- CPU: {nproc} logical cores, no NVIDIA GPU\n- RAM: {mem_gb} GB\n"
                  f"- OS: WSL2 Ubuntu 24.04 (Windows host)\n")

    lines.append("\n## Retrieval: Recall@4 and MRR (dense vs hybrid vs hybrid+rerank)\n")
    lines.append(f"Measured on the {sum(1 for q in questions if q['answerable'])} answerable questions "
                  f"in `eval/questions.jsonl` (real facts copied from crawled te.eg text, not generated).\n")
    lines.append("| Method | Recall@4 | MRR | n |\n|---|---|---|---|\n")
    for name, label in [("dense", "Dense only"), ("hybrid", "Hybrid (RRF)"), ("hybrid_rerank", "Hybrid + rerank")]:
        s = retrieval_summary[name]
        lines.append(f"| {label} | {s['recall_at_4']} | {s['mrr']} | {s['n']} |\n")

    if pipeline_summary:
        s = pipeline_summary
        lines.append("\n## Full pipeline (local provider)\n")
        lines.append(f"- Answerable questions that reached `status: answered`: "
                      f"{s['n_answerable_got_answer']}/{s['n_answerable']}\n")
        lines.append(f"- Of those, with valid mapped citations: {s['n_with_citations']}/{s['n_answerable_got_answer'] or 1}\n")
        lines.append(f"- Of those, with no numeric-mismatch warning: {s['numeric_ok']}/{s['n_with_citations'] or 1}\n")
        lines.append(f"- Correct abstention on unanswerable questions: "
                      f"{s['correct_abstention']}/{s['n_unanswerable']}\n")
        lines.append("\n### Latency per stage (ms)\n")
        lines.append("| Stage | p50 | p95 |\n|---|---|---|\n")
        for stage in ("retrieval_ms", "llm_ms", "total_ms"):
            p50 = s["latency_p50"].get(stage)
            p95 = s["latency_p95"].get(stage)
            lines.append(f"| {stage} | {p50} | {p95} |\n")
        lines.append("\nNote: ASR/TTS stage timings are measured separately in Phase 3 "
                      "(docs/progress.md) — `run_eval.py`'s pipeline eval here is text-only.\n")

    lines.append("\n## ASR WER/CER (turbo vs large-v3, beam 1 vs 5, with/without hotwords)\n")
    # Measured by eval/asr_eval.py (loads Whisper models in-process, so it runs separately).
    if has_real_audio:
        lines.append("Real recorded clips: see `eval/results_asr.md` (`python eval/asr_eval.py`).\n")
    else:
        lines.append("Real recorded clips not provided yet (`eval/audio_manifest.jsonl`, see "
                      "`eval/RECORDING_CHECKLIST.md`). A synthetic-TTS plumbing run is in "
                      "`eval/results_asr_synthetic.md` and is never substituted for real numbers.\n")

    lines.append("\n## Local vs OpenRouter comparison (`make eval-compare`)\n")
    if compare_summary:
        s = compare_summary
        lines.append(f"- Answerable questions that reached `status: answered`: "
                      f"{s['n_answerable_got_answer']}/{s['n_answerable']}\n")
        lines.append(f"- Correct abstention: {s['correct_abstention']}/{s['n_unanswerable']}\n")
        lines.append("| Stage | p50 | p95 |\n|---|---|---|\n")
        for stage in ("llm_ms", "total_ms"):
            lines.append(f"| {stage} | {s['latency_p50'].get(stage)} | {s['latency_p95'].get(stage)} |\n")
    else:
        lines.append("**SKIPPED**: no working `OPENROUTER_API_KEY` this session (see docs/decisions.md). "
                      "Add a real key to `.env` and run `make eval-compare` to fill this in.\n")

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


if __name__ == "__main__":
    main()
