"""On-demand conversation insights, one LLM call -> validated JSON (SPEC.md section 6)."""
import json

from ..clients.llm import LLMClient
from .prompts import build_insights_messages

REQUIRED_FIELDS = {
    "intent": str,
    "products": list,
    "language": str,
    "sentiment": str,
    "resolved": bool,
    "needs_escalation": bool,
    "summary": str,
}


def _coerce(raw: dict) -> dict:
    out = {}
    for field, typ in REQUIRED_FIELDS.items():
        val = raw.get(field)
        if typ is list and not isinstance(val, list):
            val = [val] if val else []
        if typ is bool and not isinstance(val, bool):
            val = bool(val)
        if typ is str and val is None:
            val = ""
        out[field] = val
    out["dialect"] = raw.get("dialect")
    return out


async def compute_insights(transcript: str, provider: str | None = None) -> dict:
    client = LLMClient(provider)
    messages = build_insights_messages(transcript)
    raw_text = await client.chat(messages, max_tokens=400, temperature=0.0, stream=False)
    try:
        start = raw_text.index("{")
        end = raw_text.rindex("}") + 1
        parsed = json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        parsed = {}
    return _coerce(parsed)
