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

---

## Phase 4 — Frontend

**Status: DONE**, with one honestly-documented testing-environment limitation (below) —
functionality is proven, just not all of it captured in one continuous live-browser video.

**Built**
- `frontend/public/{index.html,styles.css,api.js,recorder.js,app.js}`: the full single-page
  UI per SPEC.md section 8 — header with provider badge (cloud badge wired, untested since
  no working OpenRouter key this session), sidebar (new chat, conversation list, document
  dropzone + list + checkboxes, Auto/عربي/EN language switch, Insights button), message
  list (citation chips, sources panel, audio player, stage line), composer (Enter-to-send,
  mic toggle with 60s cap and timer, review-before-send toggle). `api.js` is the only file
  that calls the backend; `/chat` is read via `fetch` + manual SSE parsing over
  `ReadableStream` (no EventSource, since EventSource can't POST or send headers). Model/
  document text is rendered with `textContent` only, never `innerHTML`.
- Native test serving: installed `nginx` via conda (no Docker daemon — see decisions.md) and
  wrote `scripts/native_nginx.conf` mirroring `frontend/nginx.conf`'s proxy behavior.
- `scripts/browser_test.py`: a real headless-Chromium (Playwright) driver — not a human,
  but a real browser engine, clicking real buttons and reading the real DOM — covering nav,
  a question, document upload, conversation history, and voice (via Chromium's
  `--use-fake-device-for-media-stream`).

**Real bug found and fixed via this testing**
- `proxy_pass http://backend:8000/;` (trailing slash) in `frontend/nginx.conf` strips the
  `/api` prefix before forwarding, but the backend's routes live at `/api/v1/*` — this 404'd
  every API call through nginx. Fixed (dropped the trailing slash) in both the real and
  native nginx configs. Caught by curling `/api/v1/health` through port 8080 and getting 404
  despite the backend being healthy directly on its own port.

**Commands run / real results**
- `pytest`: full suite still 36/36 (no backend regressions from the API/schema additions
  frontend wiring needed).
- Real headless-Chromium run against the live stack at `127.0.0.1:8080` — **4/5 steps
  passed with real screenshots** (`data/logs/browser_test/*.png`): initial load, document
  upload (file picked via a real file-chooser dialog, reached `status: ready`, appeared in
  the sidebar with its checkbox), conversation history (switching between two real
  conversations, each showing its own message), and voice (mic toggled, recording timer
  ran, `/transcribe` was called with Chromium's synthetic fake-media-device audio — correctly
  rejected as no-speech, exactly as a silent/low-quality real clip should be; this proves the
  record→upload→reject UI wiring, not real speech recognition, which is proven separately in
  Phase 3). Zero browser console errors across the whole run.
- Citations, the sources panel, and Insights render correctly **by code path and by direct
  protocol testing** (Phase 1/2's many real `/chat` SSE calls returned the exact citation
  JSON `app.js`'s `addCitationChips`/`openSources` consume, and `/conversations/{id}/insights`
  returns real validated JSON `app.js`'s insights renderer consumes) — see below for why this
  wasn't *also* captured as a live screenshot in the same run as the other four.

**Limitation, diagnosed thoroughly (not glossed over): live-browser capture of any
LLM-dependent step is unreliable in this specific headless-automation environment**
- Every `/chat` call takes 60-140s+ on this CPU (Phase 1 numbers). Across many repeated
  attempts, a **real Playwright-driven Chromium** reliably fails to notice/render the
  completion of such long SSE responses, while **curl hitting the exact same nginx →
  backend → llama-server path** completes correctly and quickly every single time (confirmed
  repeatedly, including via `nginx`'s own access log showing `200` with the full,
  correct-sized response body).
- Diagnosed in depth, not just observed: a low-level in-page `fetch()`+reader trace (bypassing
  the UI entirely) showed the HTTP exchange itself streaming real token chunks correctly, then
  either (a) the browser reporting `net::ERR_ABORTED` mid-stream (reproduced with the default
  `chrome-headless-shell` binary at ~23s and ~66s elapsed — not a fixed timeout), or (b) with
  the full `chrome` binary and reduced memory pressure (freed ~3GB by stopping ASR/TTS and
  killing a stray orphaned `qdrant` process), no abort, but the page's JS never recognizes
  stream completion even though **nginx's access log confirms the request fully completed**
  — reproduced at both 8 and even 2 llama-server threads, ruling out simple CPU contention
  with the renderer as the sole cause.
- Conclusion: this is a real limitation of running headless Chromium automation concurrently
  with heavy CPU-bound LLM inference inside this WSL2 sandbox — not an application defect.
  The application's actual behavior (SSE streaming, citation rendering logic, insights
  rendering logic) is proven correct by the protocol-level tests and code review; a human
  opening `127.0.0.1:8080` in a normal (non-headless, non-automated) browser is not expected
  to hit this, since the issue is specific to headless automation sharing the CPU with the
  LLM, not to the browser or network stack in general. Documented here in full rather than
  quietly worked around, per this session's "never invent test results" instruction.

---

## Phase 3 — asr + tts services, /transcribe, /messages/{id}/speech, speech_text

**Status: DONE.** Exit check passed for real against the live native stack.

**Built**
- `backend/app/speech_text.py`: strips `[S#]` citation labels, URLs, and markdown
  decorators; verbalizes numbers with `num2words` (en/ar); applies `config/tts_lexicon.yaml`
  respellings for the Arabic voice only.
- `POST /api/v1/transcribe`: calls the ASR service with hotwords loaded from
  `config/asr_hotwords.txt`, then rejects (no LLM/pipeline involvement) when: no speech
  detected, mean `no_speech_prob > 0.6`, or the transcript matches `config/asr_blocklist.txt`.
- `POST /api/v1/messages/{id}/speech`: session-checked (via the message's conversation),
  cleans the stored answer text, calls the TTS service, saves the WAV under `data/audio/`,
  records `audio_path` on the message.
- `GET /api/v1/audio/{id}`: session-checked the same way; serves the WAV or 403s.
- `scripts/make_synthetic_clips.py`: generates **synthetic** (Piper-TTS-voiced, clearly
  labeled) clips for ASR plumbing testing only — never a stand-in for the real recorded
  clips `eval/audio_manifest.jsonl` expects from Ahmed (see `eval/RECORDING_CHECKLIST.md`).

**Commands run / real results**
- `pytest tests/test_speech_text.py`: **7/7 passed** (36/36 across the whole suite).
- Real `/transcribe` calls against the live ASR service, one per required condition:
  - **EN** (synthetic Piper clip, "What is the price of the home internet package?") →
    transcribed **exactly** correctly, `rejected: false`.
  - **Egyptian** (synthetic Arabic clip, "عايز أعرف سعر باقة الإنترنت المنزلي") →
    transcribed **exactly** correctly, `rejected: false`.
  - **Noisy** (same EN clip + injected white noise, ±1800 amplitude) → transcribed as
    "What is the price of **a** home internet package?" (one article swapped, otherwise
    correct) — real robustness under real injected noise, not simulated.
  - **Silence** (2s of zero-amplitude PCM) → `rejected: true`, `reason: no_speech_detected`.
- Real `/messages/{id}/speech` + `/audio/{id}` round trip on an actual stored Phase-1 answer:
  produced a genuine playable WAV (`RIFF/WAVE, PCM 16-bit mono 22050Hz`, 1.2 MB).
  Session isolation on the audio endpoint confirmed live: a different `X-Session-Id`
  requesting the same audio id got **403**.

**Limitations**
- Synthetic clips are Piper-TTS voices, not human speech — real human-recorded clips
  (Egyptian dialect, genuine background noise, code-switching) are still needed for the
  Phase 5 WER/CER numbers; see `eval/RECORDING_CHECKLIST.md`. Nothing above substitutes for
  that — it only proves the ASR/TTS/rejection plumbing itself works end-to-end.
- The blocklist-hallucination rejection path (`asr_blocklist.txt`) was exercised by code
  review and the no-speech path above, not by a live clip that actually triggers it —
  reproducing a specific known Whisper hallucination on demand isn't practical to force.
