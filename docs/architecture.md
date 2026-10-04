# Architecture

## Containers (per `compose.yaml`)

```
                 ┌──────────────┐
  browser ──────▶│   frontend    │  nginx: static files (/) + reverse proxy (/api/)
                 │  (nginx)      │  only container with a published host port (8080)
                 └──────┬────────┘
                        │ internal network (internal: true)
                 ┌──────▼────────┐
                 │   backend     │  FastAPI: orchestration, RAG, ingestion, OCR,
                 │  (FastAPI)    │  embeddings (BAAI/bge-m3 + reranker), SQLite
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
                  │  qdrant  │  vector DB: named vectors dense+sparse
                  └──────────┘
```

All inter-service traffic stays on the `internal` Docker network (`internal: true` — no
default-route egress). Only `frontend` also joins the default network, which it needs
purely to publish its port to the host. Under `compose.cloud.yaml`, `backend` additionally
joins the default network for egress to `openrouter.ai` when `LLM_PROVIDER=openrouter`.

**On this build machine**, no Docker daemon was available, so every phase was instead
verified with the same backend/service code running as native host processes
(`scripts/native_up.sh`) on the same ports the containers would use — see
`docs/decisions.md` for the full diagnosis. The architecture above is what's actually
specified and built (every Dockerfile and compose file is real); only the *runtime* used
for verification differed.

## Request flow: a text/voice question

1. **Frontend** (`app.js`) posts to `/api/v1/chat` (text) or first to `/api/v1/transcribe`
   (voice) then `/api/v1/chat` with the transcript. `api.js` is the only file that talks to
   the backend; the SSE response is read manually via `fetch` + `ReadableStream` (not
   `EventSource`, which can't POST or send custom headers).
2. **Backend pipeline** (`backend/app/pipeline.py`, orchestrating `backend/app/{ingestion,
   retrieval,generation}/`):
   - Resolve language (UI choice, or Arabic-script-ratio heuristic on the text).
   - Load last 4 turns of history; if this isn't the first turn, one LLM call rewrites the
     question as a standalone query (preserving numbers/entities/negation).
   - Encode the (rewritten) query with `BAAI/bge-m3` (dense 1024-d + sparse lexical
     weights), hybrid-search Qdrant: dense top-20 + sparse top-20 prefetch, fused with RRF,
     filter (`source_type=official OR (session_id=S AND doc_id IN selected)`) applied
     *inside each prefetch* so cross-session leakage is structurally impossible, not just
     filtered after the fact.
   - If the top fused score is below a tuned threshold, return `insufficient_evidence`
     **without calling the LLM** (see `eval/results.md` for how the threshold was chosen).
   - Otherwise build a numbered source context (`[S1] title | url p.N` + text, capped
     ~2k tokens) and stream a grounded answer from the LLM (local llama.cpp or OpenRouter,
     one client in `backend/app/clients/llm.py`, provider only changes base URL/key/model).
   - Validate the raw answer (`backend/app/generation/citations.py`): drop unknown `[S#]`
     labels, flag numbers in the answer that don't appear in any cited source (with
     Arabic-Indic digit normalization), downgrade to `insufficient_evidence` if a factual
     answer ends up with zero valid citations.
   - Persist the message + per-stage timings to SQLite, emit the SSE `final` event.
3. **Voice replies**: the frontend separately calls `/api/v1/messages/{id}/speech`, which
   cleans the stored answer text (`speech_text.py`: strip citations/markdown/URLs, verbalize
   numbers with `num2words`, respell English brand names for the Arabic voice via
   `config/tts_lexicon.yaml`) and calls the TTS service. A TTS failure never removes the
   already-shown text.

## Ingestion

- **Website** (`backend/app/ingestion/crawl_te.py` + `scripts/ingest_website.py`): BFS crawl
  of te.eg from sitemap/about-te seeds, robots.txt-respecting, 1 req/s, two independent
  `httpx.Client` sessions (bare-path vs `/en/`-path — found during testing that a single
  shared cookie jar let te.eg's language-preference cookie "stick" after the first `/en/`
  page, silently flipping later bare-path pages to English too).
- **Uploads** (`backend/app/api.py` `/documents` + `backend/app/ingestion/loaders.py`):
  extension+magic-byte validated, size/page-count limited, then dispatched to a per-type
  loader (PDF via PyMuPDF with per-page garbled-Arabic detection → 300 DPI render → Tesseract
  OCR fallback; DOCX walking `document.element.body` in order; TXT with cp1256 fallback;
  HTML via BeautifulSoup; images via direct OCR).
- **Chunking** (`backend/app/ingestion/chunking.py`): heading/paragraph-aware, ~450 bge-m3
  tokens with ~60 overlap, keeping each FAQ question with its answer and each table's rows
  together as one atomic unit; page/OCR flags propagate through to the final chunk for
  accurate citations.

## Data

- **Qdrant** collection `we_chunks`: named vectors `dense` (1024-d, cosine) + `sparse`
  (lexical weights), payload-indexed on `source_type`/`session_id`/`doc_id`.
- **SQLite** (WAL): `conversations`, `messages` (with JSON citations/timings columns),
  `documents` (per-session upload status).

See `docs/SPEC.md` for the complete original spec this was built against.
