"""LLM-calling helpers used by pipeline.answer (SPEC.md section 6)."""
import json
import re
from collections.abc import AsyncIterator

from ..clients.llm import LLMClient
from ..config import config
from ..ingestion.arabic import detect_lang
from .prompts import build_messages, build_router_messages

ROUTE_TYPES = {"question", "smalltalk", "off_topic", "injection"}
_JSON_RE = re.compile(r"\{.*\}", re.S)
# Letters outside Latin / Arabic: the 4B router occasionally emits Cyrillic or CJK mid-word
# ("يمكن للроутер").
_FOREIGN_SCRIPT_RE = re.compile(r"[^\W\d_a-zA-Z\u00C0-\u024F\u0600-\u06FF\u0750-\u077F]")


def parse_route(raw: str, original: str) -> dict:
    """Validate the router's JSON. Anything unusable falls back to treating the message as a
    plain question with the original text — the router must never be able to block a real
    question by misbehaving, only the reranker gate can reject for lack of evidence."""
    fallback = {"type": "question", "query": original}
    m = _JSON_RE.search(raw or "")
    if not m:
        return fallback
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return fallback
    kind = data.get("type")
    query = data.get("query")
    if kind not in ROUTE_TYPES:
        return fallback
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > 3 * len(original) + 200  # an essay/answer instead of a query
        or detect_lang(query) != detect_lang(original)  # translated despite the prompt
        or _FOREIGN_SCRIPT_RE.search(query)
    ):
        query = original
    return {"type": kind, "query": query.strip()}


async def route_query(
    history: list[dict], query: str, has_documents: bool, provider: str | None = None
) -> dict:
    client = LLMClient(provider)
    messages = build_router_messages(history, query, has_documents)
    raw = await client.chat(messages, max_tokens=160, temperature=0.0, stream=False)
    return parse_route(raw, query)


async def stream_answer(
    lang: str, query: str, sources: list[dict], provider: str | None = None
) -> AsyncIterator[str]:
    client = LLMClient(provider)
    messages = build_messages(lang, query, sources)
    async for token in await client.chat(
        messages, max_tokens=config.MAX_ANSWER_TOKENS, temperature=0.2, stream=True
    ):
        yield token


async def regenerate_answer(lang: str, query: str, sources: list[dict], provider: str | None = None) -> str:
    """One deterministic (temperature 0) retry, used when the streamed answer came back in the
    wrong language or with stray foreign script."""
    client = LLMClient(provider)
    messages = build_messages(lang, query, sources)
    return await client.chat(messages, max_tokens=config.MAX_ANSWER_TOKENS, temperature=0.0, stream=False)
