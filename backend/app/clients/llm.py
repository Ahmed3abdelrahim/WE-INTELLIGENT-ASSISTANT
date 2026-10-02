"""One OpenAI-compatible client for all LLM providers (SPEC.md section 2a).

Only base_url, API key, model, and a couple of extra params differ by provider.
Thinking is disabled in every mode.
"""
import json
from collections.abc import AsyncIterator

import httpx

from ..config import config


class LLMClient:
    def __init__(self, provider: str | None = None):
        self.provider = provider or config.LLM_PROVIDER
        if self.provider == "openrouter":
            if not config.openrouter_available:
                raise RuntimeError("openrouter_unavailable: OPENROUTER_API_KEY not set")
            self.base_url = config.OPENROUTER_BASE_URL.rstrip("/")
            self.api_key = config.OPENROUTER_API_KEY
            self.model = config.OPENROUTER_MODEL
        else:
            self.base_url = config.LLM_LOCAL_URL.rstrip("/")
            self.api_key = "not-needed"
            self.model = config.LLM_LOCAL_MODEL

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _extra_body(self) -> dict:
        if self.provider == "openrouter":
            # OpenRouter reasoning control: disable "thinking" tokens.
            return {"reasoning": {"enabled": False, "exclude": True}}
        # local llama.cpp server (Qwen3 Jinja template switch)
        return {"chat_template_kwargs": {"enable_thinking": False}}

    async def chat(
        self,
        messages: list[dict],
        max_tokens: int = 384,
        temperature: float = 0.2,
        stream: bool = True,
        timeout: float = 120.0,
    ) -> AsyncIterator[str] | str:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
            **self._extra_body(),
        }
        if stream:
            return self._stream(payload, timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def _stream(self, payload: dict, timeout: float) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                if self.provider == "openrouter":
                    if not config.openrouter_available:
                        return {"status": "unavailable", "reason": "no_api_key"}
                    resp = await client.get(
                        f"{self.base_url}/models", headers=self._headers()
                    )
                else:
                    resp = await client.get(f"{self.base_url.rsplit('/v1', 1)[0]}/health")
                return {"status": "ok" if resp.status_code < 500 else "error", "code": resp.status_code}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "detail": str(e)}
