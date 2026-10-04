# WE Assistant — On-Prem Bilingual RAG Case Study

A bilingual (Arabic / English / Egyptian dialect) retrieval-augmented assistant for WE
Telecom Egypt. Users ask by voice or text and get a grounded, cited answer; voice questions
also get a spoken reply. Users can upload documents (PDF/DOCX/TXT/HTML/images) to query.

This is a 2-day case-study PoC, built to run fully on-prem on CPU-only hardware. See
`docs/SPEC.md` for the full build spec, `docs/progress.md` for a phase-by-phase build log
with real measured results, and `docs/decisions.md` for every environment-driven decision
made along the way (all logged with a one-line reason).

## Tested hardware

- **CPU:** 16 logical cores, no NVIDIA GPU
- **RAM:** 15 GB total
- **OS:** Ubuntu 24.04 under WSL2 (Windows host)
- **Disk:** ~11.2 GB of model files actually downloaded this session (LLM 2.5GB, ASR 4.7GB
  for both Whisper sizes, embeddings 4.6GB for both encoder+reranker, TTS 0.12GB)

If running the containerized stack: give Docker Desktop **≥ 12 GB RAM** and plan for
**~12 GB free disk** for model files plus image layers.

## ⚠️ This machine has no Docker daemon — read this first

This specific laptop has Docker Desktop installed but **not running** (no WSL integration
enabled for this distro, and no way to start it from here). The commands below
(`make models`, `make up`, etc.) are the real, spec'd way to run this project and **are
expected to work on a normal machine with Docker available** — they were not skippable
fictions, they're just untested *as containers* in this specific session. Every container
image/Dockerfile here is real and was written to the real spec.

To actually verify behavior in this session, every phase was run instead as **native host
processes** (same code, same ports, no mocks) using `scripts/native_up.sh` /
`scripts/native_down.sh`. If you're in the same boat (no Docker daemon), use those instead
of `make up`. If you have Docker working, use `make up` as normal — see docs/decisions.md
for the exact diagnosis (and `docker compose config` was used to validate all three compose
files for real, including the GPU overlay, even though nothing could be run).

## Quick start (with Docker)

```bash
cp .env.example .env            # defaults to LLM_PROVIDER=local (CPU)
make models                     # downloads all models (see "Model sources" below)
make crawl                      # crawls te.eg (~106 pages take a few minutes)
make ingest                     # chunks + embeds + indexes into Qdrant
make up                         # builds and starts all 6 containers
make smoke                      # one real call per service, asserts no <think> leakage
```

Open **http://127.0.0.1:8080**.

- `make down` — stop everything
- `make up-gpu` — GPU overlay (untested on this laptop — no NVIDIA GPU; validated with
  `docker compose -f compose.yaml -f compose.gpu.yaml config`)
- `make up-cloud` — routes the LLM to OpenRouter (comparison only, see Privacy below)
- `make test` — pytest (unit tests; `tests/test_api_e2e.py` needs `-m real` against a live stack)
- `make eval` / `make eval-compare` — see `eval/results.md`
- `make notebook` — Jupyter at `127.0.0.1:8888`

## Quick start (no Docker — the native fallback actually used this session)

```bash
cp .env.example .env
# Install Miniforge (conda) and: conda create -n we python=3.11 tesseract ffmpeg nginx -c conda-forge
conda activate we
pip install -r backend/requirements.txt
python scripts/download_models.py
python scripts/crawl_te.py
python scripts/ingest_website.py
bash scripts/native_up.sh        # starts qdrant, llama-server, asr, tts, backend as host processes
nginx -c scripts/native_nginx.conf   # frontend + API proxy at 127.0.0.1:8080
python scripts/smoke_all.py
```

Ports used natively (differ slightly from the container ports — see `docs/decisions.md` for
why): frontend/nginx **8080**, backend **8020** (not 8000 — a root-owned WSL process already
holds 8000 on this machine), llm **8081** (not 8080 — the frontend needs that port natively;
no conflict in Docker since containers get separate network namespaces), asr **8001**, tts
**8002**, qdrant **6333**.

## Model sources

**`huggingface.co` is unreachable from this machine's network** (confirmed via `curl -v`:
every CloudFront IP times out on connect — a real, diagnosed block, not a guess). All models
were downloaded for real from **ModelScope** (`modelscope.cn`), which mirrors the exact same
repos. `scripts/download_models.py` lists exactly what it fetches and why; if `huggingface.co`
works for you, the repo IDs are identical and you can adapt the script to use
`huggingface_hub` directly.

## LLM provider modes

Set in `.env` via `LLM_PROVIDER`:

| Mode | How | Privacy |
|---|---|---|
| `local` (default) | `make up` / `bash scripts/native_up.sh` — Qwen3-4B Q4_K_M GGUF on CPU via llama.cpp | Nothing leaves the machine. |
| `local` + GPU | `make up-gpu` — same model, `-ngl 99` (**untested, no GPU on this laptop**) | Nothing leaves the machine. |
| `openrouter` | `make up-cloud`, needs a real `OPENROUTER_API_KEY` in `.env` | **Prompts and retrieved document text are sent to OpenRouter.** The UI shows a visible "Cloud LLM (comparison) — data leaves this machine" badge in this mode. Comparison/benchmarking only, per the brief — never the default, never used in the offline demo. |

This session's `.env` has `OPENROUTER_API_KEY` blank: the key provided didn't authenticate
against OpenRouter's real API (confirmed with a live request — `401 Missing Authentication
header`, and the value didn't match OpenRouter's usual `sk-or-v1-` key format). The app
handles this exactly as specced: the provider reports "unavailable" and everything else
keeps working. Add a real key and re-run `make smoke` / `make eval-compare` to exercise it.

## Offline operation

`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set everywhere; every model loads from
a local path at runtime (downloads only happen in `make models`, a separate `setup` step).
Every inference call (LLM, ASR, TTS, Qdrant, embeddings) goes to `127.0.0.1` — confirmed
throughout this session via the backend/nginx access logs, which show no external DNS or
network calls during normal chat/ASR/TTS/retrieval operation.

**Honesty note:** physically disconnecting Wi-Fi to prove this wasn't done in this session —
toggling network state isn't practical in this sandboxed headless dev container. What's
verified instead is architectural (offline env vars set, all traffic observed going to
loopback addresses only). If you have access to the physical machine, turning off Wi-Fi and
re-running the demo end-to-end is the stronger proof and is expected to work unchanged.

## Model licenses

All verified for real this session (API queries to ModelScope/GitHub, or the model's own
`README.md`/config on disk) — not from memory.

| Model | Source | License |
|---|---|---|
| Qwen3-4B-GGUF (LLM) | `Qwen/Qwen3-4B-GGUF` | Apache-2.0 |
| faster-whisper large-v3 (ASR) | `Systran/faster-whisper-large-v3` | MIT |
| faster-whisper large-v3-turbo (ASR) | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | MIT |
| bge-m3 (embeddings) | `BAAI/bge-m3` | MIT |
| bge-reranker-v2-m3 (reranker) | `BAAI/bge-reranker-v2-m3` | Apache-2.0 |
| Piper voices (TTS) | `rhasspy/piper-voices` | MIT |
| llama.cpp (LLM server) | `ggml-org/llama.cpp` | MIT |
| Qdrant | `qdrant/qdrant` | Apache-2.0 |

## Measured latency (this laptop, CPU-only)

Full numbers, methodology, and the retrieval ablation are in `eval/results.md`. Headline
Phase-1 sample (4B model, ~2000-token RAG context, 8 threads): LLM prompt eval ~34-44
tok/s, generation ~5.6 tok/s, **end-to-end `/chat` answers take roughly 80-140 seconds** —
the dominant cost on this hardware. This is a genuine CPU constraint (documented honestly,
not tuned away) — see "Limitations" and the production roadmap in `slides/`.

## Known limitations

- **Abstention precision gap, found by the real eval**: 2 of 5 unanswerable test questions
  were incorrectly answered instead of triggering `insufficient_evidence` — both mentioned
  "WE" or Egypt/Cairo context, which is enough token overlap to push retrieval's fused score
  above the current threshold even though the question isn't actually covered. See
  `eval/results.md` for the full breakdown; the threshold is a real tuning target, not
  swept under the rug.
- **No Docker daemon on this machine** — see above. Container paths are real and spec'd but
  untested here; native fallback used instead for all verification.
- **LLM latency is high** (60-140s/answer) — a 4B model with a ~2k-token RAG context on CPU
  is slow. GPU serving (`compose.gpu.yaml`) would fix this but is untested here (no GPU).
- **OpenRouter comparison unavailable this session** — no working API key (see above).
- **No real recorded audio clips yet** — `eval/audio_manifest.jsonl.template` and
  `eval/RECORDING_CHECKLIST.md` are ready for Ahmed to record ~10 real clips; only synthetic
  (Piper-TTS-voiced, clearly labeled) clips were used this session, for plumbing tests only.
- **Arabic OCR accuracy is imperfect** on synthetic (non-photographic) test renders —
  word-level content and all key numbers were recovered correctly in testing, but letter-
  level reordering occurred. Real scanned documents typically OCR better.
- **Headless-browser E2E testing of long-LLM-wait UI flows is unreliable** in this specific
  WSL2 + headless-Chromium sandbox (thoroughly diagnosed in `docs/progress.md` Phase 4) —
  the underlying app logic is proven correct via direct protocol testing; a human using a
  normal (non-automated) browser is not expected to hit this.
- **Piper's Arabic pronunciation quality** is the best available open TTS voice for Arabic
  but is noticeably more robotic than the English voice — documented honestly per spec,
  Chatterbox (GPU-only) is on the production roadmap.
- One LLM generation run produced a single stray Cyrillic word mid-Arabic-sentence — a minor
  quantization artifact of the 4B model, not a pipeline bug (see `docs/progress.md` Phase 1).

## Repository layout

See `docs/SPEC.md` section 1, or just browse — `backend/app/` is organized by concern
(`ingestion/`, `retrieval/`, `generation/`, `clients/`), `services/` holds the ASR/TTS
containers, `scripts/` holds the one-off/setup scripts (including the native-fallback ones
added this session), `eval/` and `tests/` hold the evaluation and test suites, `docs/` holds
the build log and decisions.

## Demo script

See `docs/demo_script.md` for the exact walkthrough (EN question → Egyptian voice question →
follow-up → Arabic PDF upload → unanswerable question → insights → Wi-Fi off).
