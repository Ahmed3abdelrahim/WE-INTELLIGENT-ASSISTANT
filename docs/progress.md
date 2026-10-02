# Progress Log

Running on: WSL2 Ubuntu 24.04 (Windows host), 16 cores, 15 GB RAM, no NVIDIA GPU.
See `docs/decisions.md` for environment-fallback decisions (no Docker daemon available).

---

## Phase 0 — Skeleton, 6 services, download_models.py, smoke_all.py, te.eg probe

**Status: DONE.** Exit check passed for real (native stack — see docs/decisions.md for why
Docker isn't available here).

**Built**
- Full repo skeleton per SPEC.md section 1: `compose.yaml` / `compose.gpu.yaml` / `compose.cloud.yaml`,
  `frontend/` (nginx + placeholder page), `backend/` (FastAPI app: config, schemas, SQLite store,
  LLM/ASR/TTS clients, health + conversation endpoints, Phase 1-4 endpoints stubbed `501`),
  `services/asr` and `services/tts` (FastAPI servers), `config/*`, `Makefile`, `scripts/download_models.py`,
  `scripts/smoke_all.py`.
- `backend/requirements.txt`: fully pinned (105 packages, real `pip freeze`), with `--extra-index-url`
  for the CPU-only torch wheel (see decisions.md).
- `docker compose config` validated for all three compose files (base, `+gpu`, `+cloud`) — real,
  using a user-space Docker CLI/Compose install (no daemon available to actually run containers).

**Commands run / real results**
- `python scripts/download_models.py` (rewritten against ModelScope, not HF — see decisions.md):
  downloaded all 6 required models, ~10200s total on this network, **11.2 GB on disk**:
  - LLM: `Qwen/Qwen3-4B-GGUF` → `qwen3-4b-q4_k_m.gguf`, 2.50 GB
  - ASR: `Systran/faster-whisper-large-v3` (3.1 GB) + `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (1.6 GB)
  - Encoder: `BAAI/bge-m3` (2.3 GB) + `BAAI/bge-reranker-v2-m3` (2.3 GB)
  - TTS: `en_US-lessac-medium` + `ar_JO-kareem-medium` (Piper voices, 63 MB each)
- `scripts/native_up.sh` (no Docker daemon, see decisions.md): started qdrant, llama-server
  (llama.cpp b11323, CPU), ASR/TTS (uvicorn), backend (uvicorn, port **8020** not 8000 — a
  root-owned WSL process already holds 8000, see decisions.md) as host processes.
- `python scripts/smoke_all.py` — **all checks passed**:
  ```
  [OK] qdrant (53 ms): healthz check passed
  [OK] llm_local (613 ms): content='OK'          <- confirmed no <think> in output
  [OK] asr (189 ms): no_speech_prob=1.0 on a synthetic sine tone (not speech, as expected)
  [OK] tts (1855 ms): 56876 bytes of audio/wav
  [OK] backend_health (67 ms): all components "ok"
  [OK] openrouter: SKIPPED (no working key — see decisions.md)
  ```
- Real one-off LLM latency sample (`/v1/chat/completions`, "capital of Egypt" test):
  prompt eval 33.8 tok/s, generation 5.6 tok/s, 1.95s total for a 32-token exchange — on
  16 physical cores (8 threads given to llama-server), CPU only.

**Bugs found and fixed via real testing (not hypothetical)**
- `services/tts/server.py` called a nonexistent `PiperVoice.synthesize_wav`; the real API
  (installed `piper-tts==1.2.0`) is `.synthesize(text, wav_file)`. Fixed.
- `services/asr/server.py` crashed with `ValueError: max() arg is an empty sequence` inside
  faster-whisper's language auto-detection when VAD finds zero speech (e.g. a pure tone/silence).
  Now caught and returned as a confident no-speech result instead of a 500.

**Limitations**
- OpenRouter comparison is unavailable this session — no working key (decisions.md).
- GPU overlay (`compose.gpu.yaml`) is validated with `docker compose config` only; cannot be
  run (no GPU on this machine, and no Docker daemon either).
- `make up`/`make down` (the containerized path) are untested here — only the native
  equivalent (`scripts/native_up.sh` / `native_down.sh`) was actually run.

---

## Phase 1 — crawl, chunking, embed, index, search, answer, citations, store, /chat SSE, conversations

**Status: DONE.** Exit check passed for real against the live native stack.

**Built**
- `backend/app/ingestion/crawl_te.py`: real te.eg crawler (BFS from sitemap + about-te seeds,
  robots.txt-respecting, 1 req/s, strips nav/header/footer/script, table→"header: value" rows).
  Two independent `httpx.Client` sessions (bare-path and `/en/`-path) — found and fixed a real bug
  where a single shared cookie jar caused te.eg's language-preference cookie to "stick" after the
  first `/en/` page, silently flipping bare-path pages to English too.
- `backend/app/ingestion/{loaders,arabic,chunking}.py`: HTML→blocks loader (heading/paragraph/table-row),
  NFKC + garbled-Arabic detection, heading/FAQ/table-aware chunker (~450 bge-m3 tokens, ~60 overlap,
  using the real bge-m3 tokenizer for counts).
- `backend/app/retrieval/{embed,index,search}.py`: BGE-M3 dense+sparse encode, Qdrant collection
  with named vectors + payload indexes, hybrid RRF search with the filter inside each prefetch
  (reranker wired but off by default per spec).
- `backend/app/generation/{prompts,citations,answer,insights}.py` + `backend/app/pipeline.py`:
  full answer pipeline (language resolution, history-aware query rewrite, retrieve, insufficient-evidence
  gate, streamed generation, citation validation, persistence, SSE events).
- `backend/app/api.py`: `/chat` now streams real SSE (`stage`/`token`/`final`/`error`); `/conversations/*`
  insights endpoint wired to a real LLM call.
- `scripts/ingest_website.py`: crawl (if needed) → chunk → embed → upsert, real, no mocks.

**Commands run / real results**
- `python scripts/crawl_te.py`: **106 real pages** (95 Arabic, 11 English — te.eg defaults to
  Arabic on bare/`/ar/` paths; `/en/` is the real English prefix, confirmed by probing, not assumed).
  Saved to `data/website/*.html` + `metadata.jsonl` + `config/te_urls.txt`.
- `python scripts/ingest_website.py`: **322 chunks** embedded (bge-m3, CPU) and upserted into
  Qdrant collection `we_chunks`.
- `pytest tests/test_arabic.py tests/test_citations.py`: **16/16 passed**, including two bugs
  this testing caught and fixed:
  - citation numeric-check was flagging `[S1]`'s own "1" as an unmatched "number in the answer"
    (citation markers weren't stripped before the numeric scan);
  - Arabic-Indic digits (`١١١`) vs Western digits (`111`) weren't recognized as the same number.
- Real `/chat` SSE calls against the live stack (curl, no mocks), one per required language mode:
  - **EN** ("home internet packages?") → `status: answered`, cited `[S4]`, no numeric warning.
    Timings: retrieval 243 ms, llm 83.5 s, total 88.3 s.
  - **Egyptian dialect** ("عايز أعرف باقات الموبايل المتاحة عندكوا") → `answered`, cited
    `[S1][S2][S4]`, no warning. One cosmetic LLM glitch: a single stray Cyrillic word
    ("интернет") appeared mid-Arabic-sentence — noted as a limitation, not blocking.
    Timings: retrieval 712 ms, llm 78.5 s, total 79.3 s.
  - **MSA** ("الشروط والأحكام الخاصة بالتعاقد...") → `answered`, cited `[S1]`, **and the
    validator correctly flagged `numeric_mismatch`**: the model stated a support phone number
    ("١١١") that does not appear anywhere in the retrieved chunk — a genuine model hallucination,
    caught exactly as SPEC.md section 6 intends. Verified by hand against the raw Qdrant payload.
    Timings: retrieval 478 ms, llm 141.4 s, total 141.9 s.
  - **Off-topic** ("What is the recipe for koshari?") → model correctly declined with no
    citations, validator downgraded it to `status: insufficient_evidence` per spec. Timings:
    retrieval 226 ms, llm 78.2 s, total 82.3 s.

**Limitations**
- LLM latency is the dominant cost on this CPU: ~78-142s per answer with a ~2k-token RAG
  context, 4B model, 8 threads. This is a genuine CPU constraint, not a bug — documented here
  honestly rather than tuned away; Phase 5 eval will give p50/p95 numbers across more questions.
- Occasional minor script-mixing artifact from the quantized 4B model (one stray Cyrillic word
  observed in an otherwise-correct Arabic answer).
- `insufficient_evidence` is currently reached in two ways: (a) pre-LLM score-threshold gate,
  tuned provisionally at 0.015 (to be re-tuned against `eval/questions.jsonl` in Phase 5), and
  (b) post-LLM "no valid citation" downgrade. Both exercised for real above.
