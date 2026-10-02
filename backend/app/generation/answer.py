"""LLM-calling helpers used by pipeline.answer (SPEC.md section 6)."""
from collections.abc import AsyncIterator

from ..clients.llm import LLMClient
from ..config import config
from .prompts import build_messages, build_rewrite_messages


async def rewrite_standalone_query(history: list[dict], query: str, provider: str | None = None) -> str:
    client = LLMClient(provider)
    messages = build_rewrite_messages(history, query)
    result = await client.chat(messages, max_tokens=128, temperature=0.0, stream=False)
    return result.strip()


async def stream_answer(
    lang: str, history: list[dict], query: str, sources: list[dict], provider: str | None = None
) -> AsyncIterator[str]:
    client = LLMClient(provider)
    messages = build_messages(lang, history, query, sources)
    async for token in await client.chat(
        messages, max_tokens=config.MAX_ANSWER_TOKENS, temperature=0.2, stream=True
    ):
        yield token
