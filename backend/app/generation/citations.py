"""Citation validation (SPEC.md section 6, step 7)."""
import re

CITATION_RE = re.compile(r"\[S(\d+)\]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")

# Arabic-Indic (U+0660-0669) and Extended Arabic-Indic/Persian (U+06F0-06F9) digits ->
# ASCII, so "١١١" and "111" compare equal (found via real Arabic-answer testing: a true
# number match was otherwise flagged as a numeric_warning false positive).
_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"
)


def _normalize_digits(text: str) -> str:
    return text.translate(_DIGIT_TRANSLATION)


def extract_cited_labels(text: str) -> set[str]:
    return {f"S{m}" for m in CITATION_RE.findall(text)}


def validate(answer_text: str, sources: list[dict]) -> dict:
    """sources: list of {"label","title","url","filename","page","section","text"}.

    Returns {"text": cleaned answer (unknown [S#] labels dropped), "citations": [...],
    "has_valid_citations": bool, "numeric_warning": bool}.
    """
    label_map = {s["label"]: s for s in sources}
    cited = extract_cited_labels(answer_text)
    valid_labels = cited & label_map.keys()
    unknown_labels = cited - label_map.keys()

    cleaned = answer_text
    for lbl in unknown_labels:
        cleaned = cleaned.replace(f"[{lbl}]", "")

    # Strip citation markers before scanning for numbers — otherwise the "1" in "[S1]"
    # gets counted as a number in the answer and falsely flagged as unmatched.
    text_without_markers = _normalize_digits(CITATION_RE.sub("", cleaned))
    cited_text_blob = _normalize_digits(" ".join(label_map[lbl]["text"] for lbl in valid_labels))
    numbers_in_answer = set(NUMBER_RE.findall(text_without_markers))
    numbers_in_sources = set(NUMBER_RE.findall(cited_text_blob))
    unmatched = numbers_in_answer - numbers_in_sources

    citations = []
    for lbl in sorted(valid_labels, key=lambda l: int(l[1:])):
        s = label_map[lbl]
        citations.append(
            {
                "label": lbl,
                "title": s["title"],
                "url": s.get("url"),
                "filename": s.get("filename"),
                "page": s.get("page"),
                "section": s.get("section"),
                "excerpt": s["text"][:300],
            }
        )

    return {
        "text": cleaned.strip(),
        "citations": citations,
        "has_valid_citations": len(valid_labels) > 0,
        "numeric_warning": bool(unmatched),
    }
