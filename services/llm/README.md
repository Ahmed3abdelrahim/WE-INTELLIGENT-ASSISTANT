# llm service

No custom image: `compose.yaml` runs the official llama.cpp server image
(`ghcr.io/ggml-org/llama.cpp:server` CPU / `:server-cuda` GPU) with `models/llm/` mounted
read-only. Natively, `scripts/native_up.sh` starts the same `llama-server` binary with the same
flags.

Flags used (see `compose.yaml` / `compose.gpu.yaml` / `scripts/native_up.sh`):
- `--jinja` — chat-template rendering, required for Qwen3's template.
- `--parallel 4 --ctx-size 16384` — 4 slots × 4096 tokens. The slots share one KV pool, so the
  pool is sized per slot; a fixed 4096 made simultaneous users fail with "Context size has been
  exceeded" (`LLM_PARALLEL`, `LLM_CTX_PER_SLOT` / `LLM_CTX_TOTAL`).
- `--flash-attn on --cache-type-k q8_0 --cache-type-v q8_0` — 8-bit KV cache, half the memory of
  f16 with negligible quality loss (`LLM_KV_TYPE`).
- `--cache-ram 1024` — caps the host-RAM prompt cache, which otherwise grows to 8 GiB
  (`LLM_CACHE_RAM`).
- `--threads <physical cores>` (`LLM_THREADS`).
- `--reasoning off` — disables Qwen3's `<think>` block.
- GPU: `--n-gpu-layers 99` (`LLM_NGL` natively). Measured on an RTX 3080: 3.96 GB VRAM.

Exposes an OpenAI-compatible `/v1/chat/completions` (streaming) and `/health`.

Model file expected at `models/llm/qwen3-4b-q4_k_m.gguf` (`scripts/download_models.py`; name
configurable via `LLM_LOCAL_MODEL`).
