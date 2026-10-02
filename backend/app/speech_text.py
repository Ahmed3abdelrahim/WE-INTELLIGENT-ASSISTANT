"""Text cleanup before TTS (SPEC.md section 7): strip [S#]/markdown/URLs, verbalize
numbers with num2words, respell English brand names for the Arabic voice."""
import re

import yaml
from num2words import num2words

from .config import CONFIG_DIR

CITATION_RE = re.compile(r"\[S\d+\]")
URL_RE = re.compile(r"https?://\S+")
MARKDOWN_RE = re.compile(r"[*_`#]+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

_lexicon = None


def _get_lexicon() -> dict:
    global _lexicon
    if _lexicon is None:
        with open(CONFIG_DIR / "tts_lexicon.yaml", encoding="utf-8") as f:
            _lexicon = yaml.safe_load(f) or {}
    return _lexicon


def _verbalize_numbers(text: str, lang: str) -> str:
    num2words_lang = "ar" if lang == "ar" else "en"

    def repl(m: re.Match) -> str:
        value = m.group(0)
        try:
            num = float(value) if "." in value else int(value)
            return num2words(num, lang=num2words_lang)
        except (ValueError, NotImplementedError):
            return value

    return NUMBER_RE.sub(repl, text)


def _apply_lexicon(text: str, lang: str) -> str:
    lexicon = _get_lexicon().get(lang, {})
    for term, replacement in lexicon.items():
        text = re.sub(rf"\b{re.escape(term)}\b", replacement, text, flags=re.IGNORECASE)
    return text


def clean_for_tts(text: str, lang: str = "en") -> str:
    text = CITATION_RE.sub("", text)
    text = URL_RE.sub("", text)
    text = MARKDOWN_RE.sub("", text)
    text = _verbalize_numbers(text, lang)
    if lang == "ar":
        text = _apply_lexicon(text, lang)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def voice_for_lang(lang: str) -> str:
    return "ar" if lang == "ar" else "en"
