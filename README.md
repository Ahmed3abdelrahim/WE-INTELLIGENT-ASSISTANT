# WE Assistant — On-Prem Bilingual RAG Case Study

A bilingual (Arabic / English / Egyptian dialect) retrieval-augmented assistant for WE
Telecom Egypt. Users ask by voice or text and get a grounded, cited answer; voice questions
also get a spoken reply. Users can upload documents (PDF/DOCX/TXT/HTML/images) and ask about
them. Everything runs on-prem: no model call leaves the machine in the default mode.

It runs on CPU only (the original 2-day build target) and on an NVIDIA GPU, where answers
take ~1-2 seconds instead of ~1-2 minutes. See `SPEC.md` for the build spec,
`docs/FINAL_REPORT.md` for the summary, `docs/progress.md` for the phase-by-phase build log
with real measured results, and `docs/decisions.md` for every non-obvious decision.

## What it does

- **Grounded answers with citations** from 106 crawled te.eg pages (Arabic + English) and the
  user's own uploads: hybrid dense+sparse retrieval (bge-m3), reranking (bge-reranker-v2-m3),
  answers from Qwen3-4B with numbered source badges and a per-document Sources list.
- **Guardrails**: greetings/thanks answered instantly without RAG; an LLM router refuses
  off-topic questions ("capital of Egypt") and prompt-injection attempts, and rewrites typos and
  Egyptian dialect into a clean search query; nothing is answered without a relevant source
  (reranker evidence gate); numbers not found in the cited sources are flagged; uploaded files
  are sanitised and can never be presented as official WE policy.
- **Voice**: faster-whisper ASR with telecom hotwords (and a guard against Whisper echoing the
  hotword prompt), Piper TTS spoken replies in Arabic and English.
- **Documents**: PDF (text and scanned, OCR in Arabic + English), DOCX with tables, TXT, HTML,
  images; private to the browser session that uploaded them.
- **UI**: English or Arabic interface (full right-to-left), light/dark theme, topic quick
  access, history grouped by day with search/rename/delete, conversation insights.

## Tested hardware

| | CPU baseline (original build) | GPU (verified later) |
|---|---|---|
| Machine | Laptop, Ubuntu 24.04 under WSL2 | Vast.ai container, Ubuntu 24.04 |
| CPU / RAM | 16 logical cores, 15 GB | 16 logical cores, 30 GB |
| GPU | none | NVIDIA RTX 3080, 10 GB (driver 595, CUDA 12.8) |
| Runtime used | native host processes (no Docker daemon) | native host processes (no Docker in the container) |

Model files: ~8 GB for the deployed set (LLM 2.4 GB, ASR turbo 1.6 GB, encoder + reranker
4.4 GB, TTS 0.12 GB), plus 3 GB if you also download Whisper large-v3 for the ASR comparison.

## Quick start — native, CPU (how the original build was verified)

```bash
cp .env.example .env
# Python 3.11 env (conda or venv) with tesseract (ara+eng), ffmpeg, nginx installed
pip install -r backend/requirements.txt -r services/asr/requirements.txt -r services/tts/requirements.txt
python scripts/download_models.py     # ModelScope mirror (huggingface.co was blocked there)
python scripts/crawl_te.py            # ~106 pages, 1 request/second
python scripts/ingest_website.py      # chunk + embed + index into Qdrant
bash scripts/native_up.sh             # qdrant, llama-server, asr, tts, backend
nginx -c scripts/native_nginx.conf    # frontend + API proxy at 127.0.0.1:8080
python scripts/smoke_all.py
```

`native_up.sh` reads `STORE` (where the qdrant/llama.cpp binaries live), `LLAMA_DIR` and an
already-activated venv; the defaults match the original laptop.

## Quick start — native, NVIDIA GPU

Same steps, with a CUDA build of llama.cpp (`cmake -B build -DGGML_CUDA=ON
-DCMAKE_CUDA_ARCHITECTURES=86` for RTX 30xx, `120` for RTX 50xx), the CUDA torch wheel instead
of the CPU one pinned in `backend/requirements.txt`, and the GPU switches:

```bash
STORE=/path/to/store LLAMA_DIR=/path/to/llama.cpp/build/bin \
LLM_NGL=99 ASR_DEVICE=cuda ASR_COMPUTE_TYPE=float16 EMBED_DEVICE=cuda RERANKER_ENABLED=true \
bash scripts/native_up.sh
```

| Switch | Default | Meaning |
|---|---|---|
| `LLM_NGL` | `0` | llama.cpp layers on the GPU (`99` = all) |
| `LLM_PARALLEL` / `LLM_CTX_PER_SLOT` | `4` / `4096` | simultaneous LLM requests and tokens each (the pool is their product) |
| `LLM_KV_TYPE` | `q8_0` | KV-cache precision (`f16` if VRAM allows) |
| `LLM_CACHE_RAM` | `1024` | llama-server host-RAM prompt cache cap, MiB |
| `ASR_DEVICE` / `ASR_COMPUTE_TYPE` | `cpu` / `int8` | `cuda` / `float16` on GPU |
| `EMBED_DEVICE` | `cpu` | `cuda` runs bge-m3 + reranker on the GPU in fp16 |
| `RERANKER_ENABLED` | `false` | reranking + the evidence gate; cheap on GPU, slower on CPU |

GPU memory, measured per component on the RTX 3080 (`scripts/gpu_vram_report.py`):

| Component | Loaded | Peak |
|---|---|---|
| LLM (Qwen3-4B Q4_K_M, 4 slots × 4096, q8_0 KV) | 3.96 GB | 3.96 GB |
| ASR (Whisper large-v3-turbo fp16) | 2.3 GB | 2.5 GB |
| Embedder (bge-m3 fp16) | 1.4 GB | 1.9 GB |
| Reranker (bge-reranker-v2-m3 fp16) | +0.55 GB | +0.9 GB |
| **Whole app** | | **9.2 GB** |
| App + an eval run alongside | | 12.1 GB |

A 10 GB card runs the app; re-ingesting the website or running the eval needs the backend
stopped first (two embedders don't fit). A 24 GB card (RTX 3090/4090) runs everything at once.

## Quick start — Docker

```bash
cp .env.example .env
make models && make crawl && make ingest
make up          # CPU;  make up-gpu  for the GPU overlay;  make up-cloud  for OpenRouter
make smoke
```

Open **http://127.0.0.1:8080**. The compose files carry the same LLM settings as the native
path. They were validated with `docker compose config` but **never run as containers** (no
Docker daemon on the laptop, no Docker inside the GPU container). In Docker the backend image
uses CPU-only torch, so embeddings/reranking stay on CPU even under `make up-gpu`.

## Testing

| Command | What it covers |
|---|---|
| `make test` | 101 unit tests (loaders, tables, chunking, citations, guardrails, small talk, insights, history, speech text) |
| `pytest tests -m real` | 6 end-to-end tests against a live stack |
| `python scripts/smoke_all.py` | one real call per service |
| `python scripts/heavy_test.py` | 49 system checks: every upload type, voice round trip, adversarial guardrail set, session isolation, input edge cases, 8 concurrent users, insights |
| `python eval/run_eval.py` | retrieval ablation + full pipeline on 25 questions (`eval/results.md` CPU, `eval/results_gpu.md` GPU) |
| `python eval/asr_eval.py` | ASR WER/CER: turbo vs large-v3, beam 1/5, hotwords on/off |
| `python scripts/gpu_leak_test.py` | GPU memory / RSS / file-descriptor soak test |
| `python scripts/gpu_vram_report.py` | per-component GPU memory |

## Measured results

| | CPU laptop (`eval/results.md`) | RTX 3080 (`eval/results_gpu.md`) |
|---|---|---|
| Answerable questions answered, with valid citations | 19/20 | 20/20 |
| Unanswerable questions correctly refused | 2/5 | 5/5 |
| Retrieval Recall@4 (hybrid + rerank) | 0.95 | 0.95 |
| `/chat` latency p50 / p95 | 66.5 s / 100.7 s | 1.2 s / 1.9 s |
| 8 simultaneous users (p50 / p95) | — | 5.8 s / 7.7 s, 32/32 answered |

The GPU run includes the guardrails added after the CPU run (router, reranker gate); the CPU
numbers are the original baseline and were not re-measured. GPU soak test: memory flat after
warm-up, no leaks. ASR on synthetic TTS clips (`eval/results_asr_synthetic.md`, plumbing check
only): 13.1% WER with the deployed setting, English and MSA transcribed exactly; real recorded
clips are still to be collected (`eval/RECORDING_CHECKLIST.md`).

## LLM provider modes

| Mode | How | Privacy |
|---|---|---|
| `local` (default) | Qwen3-4B Q4_K_M via llama.cpp, CPU or GPU | Nothing leaves the machine. |
| `openrouter` | `make up-cloud` with a real `OPENROUTER_API_KEY` | **Prompts and retrieved document text are sent to OpenRouter.** The UI shows a "Cloud LLM (comparison) — data leaves this machine" badge. Comparison only, never the default. |

The OpenRouter key provided during the build did not authenticate (real `401`, not the
`sk-or-v1-` format), so the cloud path reports "unavailable" and everything else keeps working.

## Offline operation

`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set everywhere and every model loads from a
local path; every inference call goes to `127.0.0.1` (confirmed in the backend/nginx logs).
Physically disconnecting the network was not possible in the sandboxed build environments;
turning Wi-Fi off and re-running the demo on the real machine is the stronger proof.

## Model sources and licenses

Models were downloaded from ModelScope on the laptop (huggingface.co was blocked there) and
from huggingface.co on the GPU machine; the repo ids are identical.

| Model | Source | License |
|---|---|---|
| Qwen3-4B-GGUF (LLM) | `Qwen/Qwen3-4B-GGUF` | Apache-2.0 |
| faster-whisper large-v3-turbo (ASR, deployed) | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | MIT |
| faster-whisper large-v3 (ASR, comparison) | `Systran/faster-whisper-large-v3` | MIT |
| bge-m3 (embeddings) | `BAAI/bge-m3` | MIT |
| bge-reranker-v2-m3 (reranker) | `BAAI/bge-reranker-v2-m3` | Apache-2.0 |
| Piper voices (TTS) | `rhasspy/piper-voices` | MIT |
| llama.cpp (LLM server) | `ggml-org/llama.cpp` | MIT |
| Qdrant | `qdrant/qdrant` | Apache-2.0 |

## Known limitations

- **Containers never run** — Docker paths are written and config-validated only (see above).
- **No real recorded audio yet** — ASR accuracy is measured on synthetic TTS speech only; the
  Arabic TTS voice is Jordanian, so Egyptian-dialect accuracy needs real clips.
- **CPU latency** — ~1-2 minutes per answer on the laptop. The router added one short LLM call
  per question after the CPU baseline; its CPU cost has not been re-measured.
- **10 GB GPUs are tight** — see the memory table; maintenance jobs need the backend stopped.
- **Conflicts with uploads are only flagged when both sources are retrieved** — if no official
  te.eg passage is in the top 4, the answer uses the document, attributed to the document.
- **Piper's Arabic voice** is more robotic than the English one; a better Arabic/Egyptian voice
  is on the roadmap.
- **Small-model quirks** — the 4B model occasionally adds its own unit conversions (caught by the
  numeric warning) and needed explicit language reminders; a larger model (e.g. Qwen3-8B on a
  24 GB GPU, ~6 GB estimated) is the natural next step.

## Repository layout

`backend/app/` by concern (`ingestion/`, `retrieval/`, `generation/` incl. `guard.py` and
`smalltalk.py`, `clients/`), `services/` the ASR/TTS services, `frontend/public/` the UI
(`app.js`, `i18n.js`, `icons.js`), `scripts/` setup and test tools, `eval/` evaluation,
`tests/` unit + live tests, `docs/` report, architecture, decisions and build log.

## Demo

See `docs/demo_script.md`.
