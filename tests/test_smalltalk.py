"""Small-talk shortcut and insights dialect grounding (found via GPU GUI testing: "مرحبا من انت"
went through RAG and cited an unrelated package page, and insights labelled it Egyptian)."""
import pytest

from app.generation import smalltalk
from app.generation.insights import _check_dialect, _customer_text


@pytest.mark.parametrize(
    "text,kind",
    [
        ("مرحبا", "greeting"),
        ("مرحبا من انت", "identity"),
        ("مرحباً، مَن أنت؟", "identity"),  # hamza + tashkeel + Arabic punctuation
        ("السلام عليكم ورحمة الله وبركاته", "greeting"),
        ("ازيك يا وي", "greeting"),
        ("انت مين", "identity"),
        ("هاااي", "greeting"),
        ("Hello!", "greeting"),
        ("hi there, who are you?", "identity"),
        ("شكرا جزيلا", "thanks"),
        ("thank you so much", "thanks"),
        ("مع السلامة", "bye"),
        ("thanks, bye", "bye"),
    ],
)
def test_smalltalk_detected(text, kind):
    assert smalltalk.classify(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        "ابخريني عن رقم خدنهع الملاء",
        "مرحبا، عايز اعرف رقم خدمة العملاء",
        "hi, what's the price of Super 100?",
        "شكرا، وايه هي باقات الانترنت؟",
        "What internet packages does WE offer?",
        "",
    ],
)
def test_real_questions_are_not_smalltalk(text):
    assert smalltalk.classify(text) is None


def test_reply_matches_language():
    assert "WE" in smalltalk.reply("identity", "en") and smalltalk.reply("identity", "en").isascii()
    assert "مساعد" in smalltalk.reply("greeting", "ar")


def test_customer_text_only_keeps_user_turns():
    transcript = "user: مرحبا من انت\nassistant: أنا مساعد WE، عايز تعرف إيه؟\nuser: شكرا"
    assert _customer_text(transcript) == "مرحبا من انت\nشكرا"


@pytest.mark.parametrize(
    "llm_says,customer,expected",
    [
        ("Egyptian", "مرحبا من انت", "MSA"),  # the original bug
        (None, "قولي رقم خدمه العملا", "Egyptian"),
        ("MSA", "عايز اعرف الباقات بكام", "Egyptian"),
        ("Egyptian", "hello who are you", None),
        ("Gulf", "ابي اعرف الباقات", "Gulf"),  # other dialects the LLM names are kept
    ],
)
def test_dialect_grounded_in_customer_words(llm_says, customer, expected):
    assert _check_dialect(llm_says, customer) == expected
