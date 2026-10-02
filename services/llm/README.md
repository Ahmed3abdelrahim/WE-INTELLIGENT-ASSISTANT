# llm service

No custom image: `compose.yaml` runs the pinned official llama.cpp server image
(`ghcr.io/ggml-org/llama.cpp:server` CPU / `:server-cuda` GPU) directly, with
`models/llm/` mounted read-only and the GGUF model file loaded by filename.

Flags used (see `compose.yaml` / `compose.gpu.yaml`):
- `--jinja` — enable chat-template (Jinja) rendering, required for Qwen3's template.
- `--ctx-size 4096`
- `--threads <physical cores>` (`LLM_THREADS` in `.env`)
- `--reasoning off` — disables Qwen3's `<think>` block (this llama.cpp build, b11323, has a
  dedicated reasoning on/off/auto flag; this is the "pinned build's equivalent" SPEC.md section 2a allows).
- GPU overlay adds `--n-gpu-layers 99`.

Exposes an OpenAI-compatible `/v1/chat/completions` (streaming) and `/health`.

Model file expected at `models/llm/qwen3-4b-q4_k_m.gguf` (set by `scripts/download_models.py`,
name configurable via `LLM_LOCAL_MODEL`).
