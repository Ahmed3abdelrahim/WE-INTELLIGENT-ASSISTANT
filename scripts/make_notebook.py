#!/usr/bin/env python3
"""Generates notebooks/walkthrough.ipynb (SPEC.md section 11): imports backend modules and
calls the running services directly — no duplicated logic. Run `python scripts/make_notebook.py`
to (re)generate, then execute top-to-bottom against a live stack to verify."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
cells = []


def md(text):
    cells.append(nbf.v4.new_markdown_cell(text))


def code(text):
    cells.append(nbf.v4.new_code_cell(text))


md("""# WE Assistant — Walkthrough

Calls the **real running services** (native stack on this build machine — see
`docs/decisions.md` for why there's no Docker daemon here; ports below match
`scripts/native_up.sh`). Imports backend modules directly rather than re-implementing any
logic. ASR clip is **synthetic** (Piper-TTS-voiced) — see `eval/RECORDING_CHECKLIST.md` for
the real-clip recording plan; this notebook only proves the plumbing.""")

code("""import os, sys, asyncio, json
from pathlib import Path

REPO_ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(REPO_ROOT / "backend"))

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("LLM_LOCAL_URL", "http://127.0.0.1:8081/v1")
os.environ.setdefault("LLM_LOCAL_MODEL", "qwen3-4b-q4_k_m")
os.environ.setdefault("ASR_URL", "http://127.0.0.1:8001")
os.environ.setdefault("TTS_URL", "http://127.0.0.1:8002")
os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:6333")
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
os.environ.setdefault("DATA_DIR", str(REPO_ROOT / "data"))
os.environ.setdefault("MODELS_ENCODER_DIR", str(REPO_ROOT / "models" / "encoder"))

print("repo root:", REPO_ROOT)""")

md("## 1. ASR on a clip\n\nReal `faster-whisper` inference via the backend's own ASR client, on a synthetic (Piper-voiced) clip.")

code("""from app.clients.asr import ASRClient

clip_path = REPO_ROOT / "eval" / "audio" / "synthetic_en_clean.wav"
if not clip_path.exists():
    print("Run `python scripts/make_synthetic_clips.py` first (needs the TTS service up).")
else:
    audio_bytes = clip_path.read_bytes()
    result = await ASRClient().transcribe(audio_bytes, filename=clip_path.name)
    print(json.dumps(result, indent=2, ensure_ascii=False))""")

md("## 2. Document ingestion\n\nReal loaders → chunker → embedder → Qdrant upsert, using the actual backend modules (same code path `/api/v1/documents` uses).")

code("""from app.ingestion.loaders import load_docx
from app.ingestion.chunking import chunk_blocks
from app.retrieval.embed import encode
from app.retrieval.index import upsert_chunks

doc_path = REPO_ROOT / "tests" / "fixtures" / "mobile_packages.docx"
title, blocks = load_docx(doc_path.read_bytes())
chunks = chunk_blocks(blocks)
print(f"{len(chunks)} chunks from {doc_path.name}")

embeddings = encode([c["text"] for c in chunks])
demo_session = "notebook-demo-session"
payloads = [
    {
        "chunk_id": f"notebook-demo_{i}",
        "source_type": "upload",
        "url": None,
        "title": title or doc_path.name,
        "lang": "en",
        "doc_id": "notebook-demo-doc",
        "filename": doc_path.name,
        "page": c.get("page"),
        "section": c.get("section"),
        "session_id": demo_session,
        "ocr": c.get("ocr", False),
        "text": c["text"],
    }
    for i, c in enumerate(chunks)
]
n = upsert_chunks(payloads, embeddings["dense"], embeddings["sparse"])
print(f"upserted {n} chunks into Qdrant under session_id={demo_session!r}")""")

md("## 3. Hybrid retrieval scores\n\nReal RRF-fused dense+sparse search, scoped to the session + doc we just ingested, alongside official te.eg content.")

code("""from app.retrieval.search import hybrid_search

question = "What mobile data packages are available and what do they cost?"
hits = hybrid_search(question, demo_session, doc_ids=["notebook-demo-doc"])
for h in hits:
    print(f"score={h['score']:.4f}  [{h.get('source_type')}]  {h.get('title')}")
    print("  ", h["text"][:160].replace("\\n", " "))""")

md("## 4. Prompt construction\n\nThe exact system+user prompt the LLM receives — sources are numbered `[S#]` and the model is told they're data, not instructions.")

code("""from app.pipeline import hits_to_sources, resolve_lang
from app.generation.prompts import build_messages

lang = resolve_lang("auto", question)
sources = hits_to_sources(hits)
messages = build_messages(lang, [], question, sources)
for m in messages:
    print(f"--- {m['role']} ---")
    print(m["content"][:800])
    print()""")

md("## 5. Answer with citation validation\n\nThe real end-to-end pipeline (same function `/api/v1/chat` calls): retrieve → generate (streamed) → validate citations → persist.")

code("""from app.pipeline import answer as pipeline_answer
from app.store import create_conversation

conv = await create_conversation(demo_session, None)
final = None
async for event, data in pipeline_answer(conv["id"], demo_session, question, "text", "en", ["notebook-demo-doc"]):
    if event == "final":
        final = data

print("status:", final["status"])
print("answer:", final["answer"])
print("citations:", json.dumps(final["citations"], indent=2, ensure_ascii=False))
print("timings (ms):", final["timings"])""")

md("## 6. Text-to-speech\n\nThe same cleanup (`speech_text.py`) and TTS call `/messages/{id}/speech` uses, played back inline.")

code("""from app.speech_text import clean_for_tts, voice_for_lang
from app.clients.tts import TTSClient
from IPython.display import Audio

cleaned = clean_for_tts(final["answer"], final["lang"])
print("cleaned for TTS:", cleaned)

wav_bytes = await TTSClient().synthesize(cleaned, voice_for_lang(final["lang"]))
out_path = REPO_ROOT / "data" / "audio" / "notebook_demo.wav"
out_path.write_bytes(wav_bytes)
Audio(str(out_path))""")

md("## 7. Evaluation summary\n\nReal numbers from `eval/run_eval.py` — see `eval/results.md` for the full write-up.")

code("""results_path = REPO_ROOT / "eval" / "results.md"
from IPython.display import Markdown
Markdown(results_path.read_text(encoding="utf-8"))""")

nb["cells"] = cells
out_path = "notebooks/walkthrough.ipynb"
with open(out_path, "w", encoding="utf-8") as f:
    nbf.write(nb, f)
print(f"wrote {out_path}")
