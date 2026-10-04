"""Heading/paragraph-aware chunker, ~450 bge-m3 tokens with ~60 overlap (SPEC.md section 7).

Keeps each FAQ question with its answer and each run of table rows together as one
atomic unit, then packs atomic units into token-budgeted chunks with overlap.
"""
import re

from ..config import SETTINGS, config

_tokenizer = None


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained(str(config.MODELS_ENCODER_DIR / "bge-m3"))
    return _tokenizer


def count_tokens(text: str) -> int:
    try:
        return len(_get_tokenizer().encode(text, add_special_tokens=False))
    except Exception:  # noqa: BLE001 — unit tests without model files on disk
        return max(1, len(text.split()))


def _group_atomic_units(blocks: list[dict]) -> list[dict]:
    """Merge heading+immediately-following-paragraph (FAQ-style) and runs of table_row
    blocks into single atomic units that chunking must never split apart."""
    units = []
    i = 0
    current_section = None
    while i < len(blocks):
        b = blocks[i]
        page = b.get("page")
        ocr = b.get("ocr", False)
        if b["type"] == "heading":
            current_section = b["text"]
            # FAQ-style: a heading phrased as a question, immediately followed by one
            # paragraph, is kept together with it.
            if (
                b["text"].rstrip().endswith(("?", "؟"))
                and i + 1 < len(blocks)
                and blocks[i + 1]["type"] == "paragraph"
            ):
                text = b["text"] + "\n" + blocks[i + 1]["text"]
                units.append({"text": text, "section": current_section, "page": page, "ocr": ocr})
                i += 2
                continue
            units.append(
                {"text": b["text"], "section": current_section, "is_heading": True, "page": page, "ocr": ocr}
            )
            i += 1
            continue
        if b["type"] == "table_row":
            rows = [b["text"]]
            i += 1
            while i < len(blocks) and blocks[i]["type"] == "table_row":
                rows.append(blocks[i]["text"])
                i += 1
            units.append({"text": "\n".join(rows), "section": current_section, "page": page, "ocr": ocr})
            continue
        # paragraph
        units.append({"text": b["text"], "section": current_section, "page": page, "ocr": ocr})
        i += 1
    return units


_SENTENCE_RE = re.compile(r"(?<=[.!?؟。])\s+")


def _split_oversized(unit: dict, target_tokens: int) -> list[dict]:
    """Atomic units are never split while packing, so one long table (or paragraph) used to
    become a single chunk far over budget (largest te.eg chunk: ~825 words). Split such a unit
    at line boundaries (table rows are self-contained "header: value" lines), then sentences,
    then words as a last resort; every piece keeps the unit's section/page."""
    if count_tokens(unit["text"]) <= target_tokens:
        return [unit]
    text = unit["text"]
    if "\n" in text.strip():
        parts = [p for p in text.split("\n") if p.strip()]
        joiner = "\n"
    else:
        parts = [p for p in _SENTENCE_RE.split(text) if p.strip()]
        joiner = " "
    if len(parts) == 1:  # one huge sentence: fall back to words
        parts, joiner = text.split(), " "

    pieces, current, current_tokens = [], [], 0
    for part in parts:
        part_tokens = count_tokens(part)
        if part_tokens > target_tokens:  # a single line/sentence over budget: split it further
            if current:
                pieces.append(joiner.join(current))
                current, current_tokens = [], 0
            pieces.extend(p["text"] for p in _split_oversized({**unit, "text": part}, target_tokens))
            continue
        if current and current_tokens + part_tokens > target_tokens:
            pieces.append(joiner.join(current))
            current, current_tokens = [], 0
        current.append(part)
        current_tokens += part_tokens
    if current:
        pieces.append(joiner.join(current))
    return [{**unit, "text": p} for p in pieces]


def chunk_blocks(blocks: list[dict], target_tokens: int | None = None, overlap_tokens: int | None = None) -> list[dict]:
    """Returns a list of {"text": str, "section": str|None} chunks."""
    if target_tokens is None:
        target_tokens = SETTINGS["chunking"]["target_tokens"]
    if overlap_tokens is None:
        overlap_tokens = SETTINGS["chunking"]["overlap_tokens"]

    units = [piece for unit in _group_atomic_units(blocks) for piece in _split_oversized(unit, target_tokens)]
    chunks: list[dict] = []
    current_texts: list[str] = []
    current_tokens = 0
    current_section = None
    current_page = None
    current_ocr = False

    def flush():
        nonlocal current_texts, current_tokens, current_section, current_page, current_ocr
        if current_texts:
            chunks.append(
                {
                    "text": "\n\n".join(current_texts),
                    "section": current_section,
                    "page": current_page,
                    "ocr": current_ocr,
                }
            )
        current_texts = []
        current_tokens = 0
        current_page = None
        current_ocr = False

    for unit in units:
        unit_tokens = count_tokens(unit["text"])
        if current_texts and current_tokens + unit_tokens > target_tokens:
            flush()
            # overlap: carry the tail of the previous chunk forward
            if chunks and overlap_tokens > 0:
                tail_words = chunks[-1]["text"].split()
                carried = []
                carried_tokens = 0
                for w in reversed(tail_words):
                    t = count_tokens(w)
                    if carried_tokens + t > overlap_tokens:
                        break
                    carried.insert(0, w)
                    carried_tokens += t
                if carried:
                    current_texts = [" ".join(carried)]
                    current_tokens = carried_tokens
        current_section = unit.get("section") or current_section
        if current_page is None:
            current_page = unit.get("page")
        current_ocr = current_ocr or unit.get("ocr", False)
        current_texts.append(unit["text"])
        current_tokens += unit_tokens

    flush()
    return [c for c in chunks if c["text"].strip()]
