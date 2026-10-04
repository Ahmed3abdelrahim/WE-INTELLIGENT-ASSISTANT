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
NUMBER_UNIT_RE = re.compile(r"(\d)([A-Za-z\u0600-\u06FF])")

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

    # "10GB" -> "10 GB" first, otherwise the voice reads "tenGB" as one word.
    text = NUMBER_UNIT_RE.sub(r"\1 \2", text)
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


def echoes_hotwords(transcript: str, hotwords: str) -> bool:
    """True when an ASR transcript looks like the hotword prompt read back ("فاتورة. ما إنترنت
    المنزل, فلما إنترنت المنزل ..."). faster-whisper feeds hotwords to the decoder as a prompt
    and, on unclear or clipped audio, sometimes emits that prompt (with filler fragments)
    instead of the speech. Trigger: 2+ hotword phrases making up at least 40% of the words.
    False positives only cost one re-transcription without hotwords."""
    from .generation.smalltalk import normalize  # same Arabic/punctuation normalisation

    text = f" {normalize(transcript)} "
    total = len(text.split())
    if total == 0:
        return False
    phrases = sorted({normalize(p) for p in hotwords.split(",") if p.strip()}, key=len, reverse=True)
    hits = hotword_words = 0
    for phrase in phrases:
        needle = f" {phrase} "
        while needle in text:
            text = text.replace(needle, " ", 1)
            hits += 1
            hotword_words += len(phrase.split())
    return hits >= 2 and hotword_words / total >= 0.4
