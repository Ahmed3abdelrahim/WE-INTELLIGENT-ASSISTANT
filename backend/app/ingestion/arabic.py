"""Arabic text helpers shared by ingestion and chunking (SPEC.md section 7)."""
import re
import unicodedata

# Arabic Presentation Forms-A (U+FB50-FDFF) and Presentation Forms-B (U+FE70-FEFF)
PRESENTATION_FORMS_RE = re.compile("[ﭐ-﷿ﹰ-﻿]")
# Arabic block (U+0600-06FF) + Arabic Supplement (U+0750-077F)
ARABIC_RE = re.compile("[؀-ۿݐ-ݿ]")

# Reversed-common-word heuristic: when a page's text was extracted in visual (reversed)
# order, common short Arabic function words come out backwards.
_REVERSED_WORDS = ["يف", "نم", "ىلع"]
_NORMAL_WORDS = ["في", "من", "على"]


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def arabic_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic_count = sum(1 for c in letters if ARABIC_RE.match(c))
    return arabic_count / len(letters)


def detect_lang(text: str, threshold: float = 0.3) -> str:
    return "ar" if arabic_ratio(text) > threshold else "en"


def is_garbled(text: str) -> bool:
    """SPEC.md section 7: a page counts as garbled if empty, presentation-forms-dominated,
    or reversed common words outnumber normal ones."""
    stripped = text.strip()
    if not stripped:
        return True
    presentation_count = len(PRESENTATION_FORMS_RE.findall(text))
    normal_arabic_count = len(ARABIC_RE.findall(text))
    if presentation_count > normal_arabic_count:
        return True
    reversed_count = sum(text.count(w) for w in _REVERSED_WORDS)
    normal_count = sum(text.count(w) for w in _NORMAL_WORDS)
    if reversed_count > normal_count and reversed_count > 0:
        return True
    return False
