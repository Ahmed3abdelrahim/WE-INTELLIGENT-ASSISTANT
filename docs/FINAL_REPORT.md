# Final Report — WE Assistant PoC

Top-level summary. `docs/progress.md` has the phase-by-phase log with real commands and output,
`docs/decisions.md` a one-line reason for every non-obvious decision, `docs/architecture.md`
the design, and `eval/results.md` (CPU) / `eval/results_gpu.md` (GPU) the evaluation write-ups.

## How to run it

See `README.md`: native CPU, native GPU (with the switch table and a GPU memory table) and
Docker. The two environments actually used for verification were a CPU-only laptop (the
original build) and an RTX 3080 10 GB GPU container; both ran the services as native host
processes because neither had a usable Docker daemon.

## What works (verified, not claimed)

- **RAG pipeline** — EN, MSA and Egyptian-dialect questions get cited answers from 106 real
  te.eg pages (360 chunks). GPU eval: 20/20 answerable questions answered with valid
  citations, 5/5 unanswerable ones refused.
- **Guardrails** — small talk answered without RAG; off-topic questions and prompt-injection
  attempts refused (regex pre-filter + LLM router); typos and dialect rewritten into a clean
  query; reranker evidence gate; numbers not in the cited sources flagged; system-prompt leaks
  withheld; uploaded documents sanitised and labelled as non-official, with te.eg preferred on
  conflicts. Adversarial set in the heavy test: 10/10 off-topic, 7/7 injection, 6/6 small talk,
  10/10 tricky-but-legitimate questions not blocked.
- **Documents** — PDF (text layer and scanned/OCR, Arabic and English), DOCX with tables, TXT,
  HTML and images ingest and are answerable; originals saved under `data/uploads/`; long tables
  split on row boundaries; te.eg price tables with merged header cells parsed correctly.
- **Session isolation** — another session cannot read, rename, delete, chat in, fetch audio
  from, or retrieve from your conversations and documents (8 live checks).
- **Speech** — ASR (EN and AR, silence rejected, hotword-echo guard) and TTS replies;
  synthetic-speech WER 13.1% with the deployed setting (real clips pending).
- **Frontend** — streamed answers with lists and source badges, grouped sources, English/Arabic
  interface (RTL), light/dark theme, topic quick access, history (grouped, search, rename,
  delete), insights, voice with clear microphone-permission guidance. Verified in headless
  Chrome.
- **Robustness** — 8 simultaneous users: 32/32 answered (p50 5.8 s); GPU soak test flat (no
  memory, RSS or file-descriptor growth); invalid input rejected with 422.
- **Tests** — 101 unit + 6 live end-to-end tests, and 49/49 heavy system checks.

## Tested vs. untested

| Area | Status |
|---|---|
| Local LLM on CPU | Tested on the laptop (original baseline) |
| Local LLM on GPU (native) | **Tested** on RTX 3080: eval, heavy test, soak, VRAM report |
| Docker containers (`make up`, `make up-gpu`) | **Not run** — no Docker daemon on either machine; config-validated only. In Docker, embeddings stay on CPU (CPU torch image). |
| OpenRouter cloud path | Not tested — the provided key was rejected (`401`) |
| ASR accuracy on real speech | **Pending** — tooling ready (`eval/asr_eval.py`); only synthetic TTS clips measured |
| Guardrail latency on CPU | Not re-measured (router adds one short LLM call; ~0.2 s on GPU) |
| Physical offline (Wi-Fi off) | Architecturally verified (all calls to 127.0.0.1), not physically toggled |

## Measured results

| | CPU laptop | RTX 3080 |
|---|---|---|
| Answerable answered with citations | 19/20 | 20/20 |
| Unanswerable correctly refused | 2/5 | 5/5 |
| Retrieval Recall@4 / MRR (hybrid + rerank) | 0.95 / 0.825 | 0.95 / 0.825 |
| `/chat` p50 / p95 | 66.5 s / 100.7 s | 1.2 s / 1.9 s |

The GPU column includes the guardrails added after the CPU run; the CPU column is the original
baseline. GPU memory: 9.2 GB for the whole app at peak (component table in `README.md`).

## Problems found by testing, and fixed

Each is logged with its diagnosis in `docs/decisions.md`:

- Context overflow on long retrieved contexts (CPU phase) — token budget now enforced.
- Answers never rendered in the browser — the SSE parser ignored CRLF-delimited events.
- Answers lagging one turn behind — chat history was given to the 4B model as chat turns.
- Off-topic questions answered from general knowledge; the RRF score threshold rejected
  almost nothing (rank-1 hits always score ~1/61) — router + reranker evidence gate.
- English questions answered in Arabic (8/20) — language rule repeated at the end of the prompt.
- Simultaneous users failing with "Context size has been exceeded" — llama-server's slots
  shared one 4096-token pool; now sized per slot.
- Price tables garbled ("col3: 775") by merged header cells; oversized table chunks.
- Whisper occasionally "transcribing" its own hotword prompt — detected and re-transcribed.
- Numeric warning fired on the answer's own list numbering; insights called MSA "Egyptian".
- The Arabic OCR test fixture was drawn reversed (bidi applied twice) — fixed and the test now
  checks the Arabic words, not just the numbers.

## Next steps

1. **Record ~10 real clips** (`eval/RECORDING_CHECKLIST.md`) and run `python eval/asr_eval.py`.
2. **Run the containers once** on a machine with Docker (`make up`, `make up-gpu`).
3. **Move to a 24 GB GPU** (RTX 3090): runs the app plus eval/maintenance concurrently; try
   Qwen3-8B against the same eval.
4. **Measure the guardrails on CPU** and tune if the router call is too slow there.
5. **Add a real OpenRouter key** and run `make eval-compare`.
6. Production roadmap: auto-restarting services, a CUDA backend image, a better Arabic voice,
   monitoring, SSO.

## Repository state

Commits per phase (0-6, the CPU build) followed by the GPU bring-up, SSE fix, table parser,
citation fix, insights, guardrails, history API, frontend redesign and decision-log commits,
then the hardening work described above. `git log --oneline` lists them.
