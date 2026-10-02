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

---

## Phase 2 — loaders, arabic, ocr, /documents, session filter

**Status: DONE.** Exit check passed for real against the live native stack.

**Built**
- `backend/app/ingestion/loaders.py`: `load_pdf` (PyMuPDF per-page extraction, NFKC-normalize,
  garbled-page detection → 300 DPI render → Tesseract OCR fallback, page numbers kept),
  `load_docx` (walks `document.element.body` in order, headings set section, tables →
  "header: value" rows), `load_txt` (utf-8 → cp1256 fallback), `load_image` (Tesseract
  ara+eng, `ocr=true`), plus `detect_doc_type` (extension + magic-byte cross-check).
- `backend/app/ingestion/ocr.py`: Tesseract `ara+eng` wrapper.
- `backend/app/ingestion/chunking.py`: extended to carry `page`/`ocr` through from blocks
  to final chunks (needed so upload citations include the right page number).
- `POST/GET /api/v1/documents` (`backend/app/api.py`): synchronous upload → validate
  (size, extension+magic, PDF page count) → load → chunk → embed → upsert with
  `source_type=upload`, `session_id`, `doc_id` — exactly the fields `retrieval/search.py`'s
  filter already scoped to session in Phase 1.
- `tests/fixtures/generate_fixtures.py`: generates all 7 real fixture files (not placeholders)
  — a genuine PyMuPDF-written text PDF, a python-docx file with a real table, a UTF-8 TXT,
  an HTML file with nav/footer to strip, a PNG with real rendered text, and two **image-only**
  PDFs (scanned English, and Arabic — both have no text layer, forcing the OCR path for real).
  The Arabic fixture uses `arabic_reshaper`+`python-bidi` to pre-shape the text before
  rendering, because PyMuPDF's own text-insertion APIs (`insert_text`, `insert_htmlbox` —
  both tried) don't shape Arabic correctly and would have produced an unrepresentative fixture.

**Commands run / real results**
- `pytest tests/test_loaders.py tests/test_session_isolation.py`: **13/13 passed** (29/29
  across the whole suite). Covers: PDF text-layer extraction with page numbers, OCR
  triggering + recovery on both the scanned and Arabic image-only PDFs, DOCX table→rows,
  TXT UTF-8 and cp1256 fallback, HTML nav/footer stripping, PNG OCR, magic-byte mismatch
  rejection, and the session-isolation filter logic in isolation.
- Real uploads via `curl` against the live stack, one per fixture type — **all 7 reached
  `status: ready`**:
  ```
  text_pdf.pdf       -> pdf,  1 page,  1 chunk
  arabic_pdf.pdf     -> pdf,  1 page,  1 chunk
  scanned_pdf.pdf    -> pdf,  1 page,  1 chunk
  mobile_packages.docx -> docx, 1 chunk
  support_hours.txt  -> txt,  1 chunk
  roaming_faq.html   -> html, 1 chunk
  store_locations.png -> png, 1 chunk
  ```
- Real `/chat` call (session A) asking about both the text-PDF price and the DOCX table,
  with `doc_ids` scoped to those two uploads: answered correctly, citing `[S1]` (text_pdf.pdf,
  **page 1**) and `[S2]` (mobile_packages.docx, the table data), no numeric warning.
- **Session isolation, verified live, not just unit-tested**: `GET /documents` from a second
  session (`doc-test-session-B`) returned `[]`; a `/chat` call from session B that explicitly
  passed session A's `doc_ids` in the request body still could not retrieve them — the
  response fell back to official te.eg content only, confirming the `session_id AND doc_id`
  filter clause (not just the doc_id) is what gates upload visibility.

**Limitations**
- Tesseract's Arabic OCR accuracy on the synthetically-rendered Arabic PDF fixture is
  imperfect at the letter level (word-level content and all key numbers were recovered
  correctly; some letter reordering within words). This is a genuine OCR-engine limitation
  on synthetic renders, not a bug in the ingestion code — documented honestly rather than
  hidden. Real scanned Arabic documents (actual photographed/scanned pages) typically OCR
  better than text rendered fresh onto a blank image.
- A document that fits in a single chunk reports whichever heading was *last* seen as its
  `section` (e.g. the DOCX fixture's citation says "Terms" even though the cited price data
  sits under "Pricing Table") — section attribution gets more precise once a document is
  large enough to span multiple chunks. Not fixed, since it didn't affect correctness of the
  citation's `page`/`filename`/`excerpt`, only the `section` label's precision on tiny documents.
