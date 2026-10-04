"""On-demand conversation insights, one LLM call -> validated JSON (SPEC.md section 6)."""
import json
import re

from ..clients.llm import LLMClient
from ..ingestion.arabic import ARABIC_RE, detect_lang
from . import smalltalk
from .prompts import build_insights_messages

# Egyptian-colloquial words that MSA doesn't use, written in smalltalk.normalize() form.
EGYPTIAN_MARKERS = [
    "عايز", "عايزه", "عاوز", "عاوزه", "ازاي", "ايه", "ده", "دي", "دول", "مش", "كده", "بتاع",
    "بتاعت", "بتاعي", "فين", "ليه", "امتي", "دلوقتي", "ازيك", "قولي", "قوللي", "ابعتلي",
    "عشان", "علشان", "اوي", "لسه", "بقي", "معلش", "ازيكم", "بكام",
]
EGYPTIAN_MARKERS_RE = re.compile(r"(?<!\S)(?:" + "|".join(EGYPTIAN_MARKERS) + r")(?!\S)")

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


def _customer_text(transcript: str) -> str:
    """The user's turns only, from the "role: text" transcript the API builds."""
    parts, role = [], None
    for line in transcript.splitlines():
        for r in ("user", "assistant"):
            if line.startswith(f"{r}: "):
                role, line = r, line[len(r) + 2:]
                break
        if role == "user":
            parts.append(line)
    return "\n".join(parts)


def _check_dialect(llm_dialect, customer_text: str):
    """The LLM labelled "مرحبا من انت" as Egyptian just because the prompt says WE Telecom
    *Egypt*. Ground the label in the customer's actual words instead: Egyptian only with
    Egyptian marker words, MSA for marker-free Arabic, null when there's no Arabic at all.
    Other dialects the LLM names (Gulf, Levantine, ...) are kept as-is."""
    if not ARABIC_RE.search(customer_text):
        return None
    if EGYPTIAN_MARKERS_RE.search(smalltalk.normalize(customer_text)):
        return "Egyptian"
    if llm_dialect is None or str(llm_dialect).strip().lower() in ("egyptian", "egyptian arabic", "msa", ""):
        return "MSA"
    return llm_dialect


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
    out = _coerce(parsed)
    customer = _customer_text(transcript)
    # The LLM returns "ar" / "Arabic" / "MSA" interchangeably here; use the pipeline's own rule.
    out["language"] = detect_lang(customer) if customer.strip() else out["language"]
    out["dialect"] = _check_dialect(out["dialect"], customer)
    return out
