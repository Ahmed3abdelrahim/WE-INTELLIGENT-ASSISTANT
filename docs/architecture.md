# Architecture

## Services (per `compose.yaml`; the same processes run natively via `scripts/native_up.sh`)

```
                 ┌──────────────┐
  browser ──────▶│   frontend    │  nginx: static files (/) + reverse proxy (/api/)
                 │  (nginx)      │  only service with a published host port (8080)
                 └──────┬────────┘
                        │ internal network (internal: true)
                 ┌──────▼────────┐
                 │   backend     │  FastAPI: guardrails, routing, RAG, ingestion, OCR,
                 │  (FastAPI)    │  embeddings + reranker (bge-m3 / bge-reranker-v2-m3), SQLite
                 └──┬───┬───┬────┘
                    │   │   │
        ┌───────────┘   │   └───────────┐
        ▼               ▼               ▼
  ┌──────────┐   ┌──────────┐    ┌──────────┐
  │   llm    │   │   asr    │    │   tts    │
  │(llama.cpp│   │(faster-  │    │ (Piper)  │
  │ server)  │   │ whisper) │    │          │
  └──────────┘   └──────────┘    └──────────┘
                        │
                        ▼
                  ┌──────────┐
                  │  qdrant  │  vector DB: named vectors dense + sparse
                  └──────────┘
```

All inter-service traffic stays on the `internal` network (no egress). Only `frontend` joins
the default network, to publish its port; under `compose.cloud.yaml` the backend also gets
egress to `openrouter.ai`.

Both verification environments (CPU laptop, RTX 3080 GPU container) ran these services as
native host processes on the same ports, because neither had a usable Docker daemon.

**llama-server concurrency**: `--parallel 4` slots with a pool of 4 × 4096 tokens (`q8_0` KV
cache, flash attention). Slots share one KV pool, so the pool must be sized per slot; with the
old fixed `--ctx-size 4096`, two simultaneous RAG questions exhausted it.

## Request flow: a text or voice question

1. **Frontend** (`app.js`) posts to `/api/v1/chat`, or for voice first to `/api/v1/transcribe`.
   `api.js` is the only file that talks to the backend; the SSE stream is read with `fetch` +
   `ReadableStream` and parsed per the SSE spec (CRLF, LF or CR line endings).
2. **Transcription** (`/transcribe`): faster-whisper with telecom hotwords; silence and known
   Whisper hallucinations are rejected; if the transcript is mostly the hotword prompt read
   back, it is transcribed again without hotwords.
3. **Pipeline** (`backend/app/pipeline.py`), cheapest checks first:
   1. Resolve the answer language (UI setting, or Arabic-script ratio).
   2. **Small talk** (`generation/smalltalk.py`): greetings, "who are you", thanks, goodbye →
      fixed reply, no retrieval, no LLM. Only when the whole message is small talk.
   3. **Injection pre-filter** (`generation/guard.py`): regex for phrasing aimed at the
      assistant's own instructions (EN + AR) → refusal, no LLM call.
   4. **Router** (`generation/answer.py: route_query`): one short LLM call returns
      `{type: question | smalltalk | off_topic | injection, query}`. History is passed as quoted
      text, never as chat turns. The query is standalone, typo-free, dialect turned into MSA,
      same language as the question. Unusable router output falls back to "question with the
      original text" — the router can never block a real question by misbehaving.
   5. **Retrieval** (`retrieval/search.py`): bge-m3 dense + sparse prefetch (top 20 each) fused
      with RRF in Qdrant, filter `source_type=official OR (session_id=S AND doc_id IN selected)`
      applied inside each prefetch (cross-session leakage is structurally impossible), then
      reranked by bge-reranker-v2-m3 to the final 4.
   6. **Evidence gate**: the top reranker score must reach `min_rerank_score` (0.02; off-topic
      queries score ~0.000-0.002). Without the reranker, the older RRF threshold applies.
   7. **Answer**: system prompt + sources in `<source label="S1" origin="official te.eg">`
      blocks (uploads tagged `origin="uploaded by the user"`, tags inside source text defanged),
      then the standalone question, then reminders at the end: the answer language, and — if
      official and uploaded sources are both present — "official first, then what the document
      says". Context capped at ~2k tokens; no chat history.
   8. **Output checks**: wrong language or foreign script → one temperature-0 regeneration;
      system-prompt fingerprint in the answer → refusal; citation validation drops unknown
      `[S#]` labels and flags numbers not present in the cited sources (list numbering and
      Arabic-Indic digits handled).
   9. Persist the message with per-stage timings to SQLite; emit the SSE `final` event.
4. **Spoken reply**: `/api/v1/messages/{id}/speech` cleans the stored answer (`speech_text.py`:
   strip citations/markdown/URLs, verbalise numbers, respell brand names for the Arabic voice)
   and calls TTS. A TTS failure never removes the text.

## Ingestion

- **Website** (`ingestion/crawl_te.py`, `scripts/ingest_website.py`): robots.txt-respecting BFS
  crawl of te.eg at 1 request/s with separate sessions for Arabic and English paths.
- **Uploads** (`api.py /documents`, `ingestion/loaders.py`): extension + magic-byte check,
  size/page limits, per-type loader (PDF via PyMuPDF with garbled-Arabic detection → 300 DPI
  render → Tesseract `ara+eng` OCR; DOCX in body order; TXT with cp1256 fallback; HTML via
  BeautifulSoup; images via OCR). Sentences addressing the assistant's instructions are
  stripped. The original is saved as `data/uploads/<uuid>.<type>` after a successful ingest.
- **Tables** (HTML): `colspan`/`rowspan` expanded into a grid; leading header rows combined per
  column; output one line per row, e.g. `Fixed Internet Bundle: Super 250 GBs, WE Gold & Fixed
  Internet Upgrade Fees (in EGP): 260 = 135; 525 = 135; 775 = Free`.
- **Chunking** (`ingestion/chunking.py`): ~450 bge-m3 tokens with ~60 overlap; FAQ question +
  answer and each table kept together when they fit; anything larger split on row, then
  sentence, then word boundaries; section and page carried to every chunk.

## Frontend

Static files served by nginx: `index.html`, `styles.css` (theme tokens, logical properties for
RTL), `i18n.js` (English/Arabic strings), `icons.js` (inline SVG icons), `api.js`, `recorder.js`
(MediaRecorder; keeps the mic stream warm for 60 s after a recording), `app.js`. Model and
document text is only ever inserted as text nodes. Settings (interface language, theme, answer
language) are stored per browser.

## Data

- **Qdrant** `we_chunks`: named vectors `dense` (1024-d, cosine) + `sparse`, payload-indexed on
  `source_type` / `session_id` / `doc_id`.
- **SQLite** (WAL): `conversations` (title falls back to the first question; rename/delete are
  session-scoped), `messages` (citations and timings as JSON), `documents`.
- **Files**: `data/uploads/` (originals), `data/audio/` (spoken replies, deleted with their
  conversation), `data/logs/`.

See `SPEC.md` for the original spec.
