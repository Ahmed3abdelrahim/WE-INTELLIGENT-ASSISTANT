"""Small-talk shortcut: greetings / "who are you" / thanks / goodbye never go through RAG.

Without this, "مرحبا من انت" was embedded, matched some package page above the retrieval
threshold, and came back as an LLM answer citing [S1] (an unrelated WE Space page) — wasted
LLM time (a minute+ on CPU) and a meaningless citation. A message only counts as small talk
when *every* word in it is a known small-talk phrase or filler, so "hi, what's the price of
Super 100?" still goes to retrieval.
"""
import re

_TASHKEEL_RE = re.compile("[ً-ْٰـ]")  # harakat, superscript alef, tatweel
_PUNCT_RE = re.compile(r"[^\w\s]")
_REPEAT_RE = re.compile(r"(.)\1{2,}")  # "هاااي" / "hiiii" -> one letter


def normalize(text: str) -> str:
    t = _TASHKEEL_RE.sub("", text.lower())
    t = t.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))
    t = _PUNCT_RE.sub(" ", t)
    t = _REPEAT_RE.sub(r"\1", t)
    return " ".join(t.split())


# Phrases are written already normalized (no hamza on alef, ه for ة, ي for ى).
_PHRASES = {
    "greeting": [
        "مرحبا", "مرحبتين", "اهلا", "اهلا وسهلا", "اهلا بيك", "اهلا بك", "اهلين", "هلا",
        "السلام عليكم", "سلام عليكم", "السلام عليكم ورحمه الله", "السلام عليكم ورحمه الله وبركاته",
        "سلام", "صباح الخير", "صباح النور", "مساء الخير", "مساء النور", "هاي", "هلو", "الو",
        "ازيك", "ازيكم", "عامل ايه", "عامله ايه", "اخبارك ايه", "ازاي الحال", "كيف حالك", "كيفك",
        "hi", "hello", "hey", "hiya", "good morning", "good afternoon", "good evening",
        "greetings", "how are you", "how are you doing", "salam", "marhaba",
    ],
    "identity": [
        "من انت", "مين انت", "انت مين", "انتي مين", "من انتم", "مين حضرتك", "انت ايه",
        "ما اسمك", "اسمك ايه", "ماذا تفعل", "بتعمل ايه", "ماذا يمكنك ان تفعل",
        "تقدر تساعدني في ايه", "ممكن تساعدني في ايه", "عرفني بنفسك", "عرف نفسك",
        "who are you", "what are you", "what is your name", "whats your name", "what s your name",
        "what can you do", "what do you do", "introduce yourself", "who r u",
    ],
    "thanks": [
        "شكرا", "شكرا جزيلا", "شكرا ليك", "شكرا لك", "متشكر", "متشكره", "مشكور", "تسلم",
        "تسلم ايدك", "الف شكر", "thanks", "thank you", "thank you so much", "thanks a lot",
        "many thanks", "thx", "ty",
    ],
    "bye": [
        "مع السلامه", "باي", "الي اللقاء", "bye", "goodbye", "bye bye", "see you",
        "see you later",
    ],
}
# Words allowed around the phrases without turning the message into a real question.
_FILLER = {
    "يا", "و", "انا", "حضرتك", "اوكي", "تمام", "طيب", "مساعد", "وي", "we", "bot",
    "assistant", "there", "sir", "ok", "okay", "and", "please", "so", "much", "a", "lot",
    "من", "فضلك", "لو", "سمحت",
}
# Longest first so "السلام عليكم ورحمه الله" wins over "السلام عليكم" / "سلام".
_PATTERNS = sorted(
    ((kind, re.compile(rf"(?<!\S){re.escape(p)}(?!\S)")) for kind, ps in _PHRASES.items() for p in ps),
    key=lambda kp: -len(kp[1].pattern),
)

_REPLIES = {
    "intro": {
        "ar": "أهلاً بك! أنا مساعد WE الذكي من الشركة المصرية للاتصالات. أقدر أساعدك في أسئلة "
              "باقات الإنترنت المنزلي والموبايل، وطرق الشحن والدفع، وخدمة العملاء، وكمان في "
              "المستندات اللي ترفعها. اسألني عن أي حاجة!",
        "en": "Hi! I'm the WE Assistant from Telecom Egypt. I can help with home internet and "
              "mobile plans, recharging and payments, customer service, and any documents you "
              "upload. What would you like to know?",
    },
    "thanks": {
        "ar": "العفو! لو عندك أي سؤال تاني عن خدمات WE أنا موجود.",
        "en": "You're welcome! Ask me anything else about WE services.",
    },
    "bye": {
        "ar": "مع السلامة! سعيد إني قدرت أساعدك.",
        "en": "Goodbye! Glad I could help.",
    },
}


def classify(text: str) -> str | None:
    """Returns "greeting" / "identity" / "thanks" / "bye" when the whole message is small
    talk, else None. Several kinds in one message: identity > greeting > bye > thanks."""
    t = normalize(text)
    if not t or len(t.split()) > 12:
        return None
    found = set()
    for kind, pattern in _PATTERNS:
        t, n = pattern.subn(" ", t)
        if n:
            found.add(kind)
    if not found or any(w not in _FILLER for w in t.split()):
        return None
    for kind in ("identity", "greeting", "bye", "thanks"):
        if kind in found:
            return kind


def reply(kind: str, lang: str) -> str:
    key = "intro" if kind in ("greeting", "identity") else kind
    return _REPLIES[key]["ar" if lang == "ar" else "en"]
