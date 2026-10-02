from app.ingestion.arabic import arabic_ratio, detect_lang, is_garbled, normalize


def test_detect_lang_english():
    assert detect_lang("What is the price of the home internet package?") == "en"


def test_detect_lang_arabic():
    assert detect_lang("ما هو سعر باقة إنترنت المنزل؟") == "ar"


def test_detect_lang_threshold():
    # mostly English with a couple of Arabic words should stay "en" at the 0.3 threshold
    assert detect_lang("The package is called وي and costs 200 EGP per month total") == "en"


def test_arabic_ratio_ignores_digits_and_punctuation():
    assert arabic_ratio("123!؟.,") == 0.0


def test_is_garbled_empty():
    assert is_garbled("") is True
    assert is_garbled("   ") is True


def test_is_garbled_normal_arabic_is_not_garbled():
    assert is_garbled("مرحبا بكم في موقع وي للاتصالات، نقدم لكم أفضل الباقات في مصر") is False


def test_is_garbled_reversed_common_words():
    # "يف" / "نم" / "ىلع" are "في"/"من"/"على" reversed — a sign of visual-order extraction
    assert is_garbled("انا يف المنزل نم الصباح ىلع الساعة العاشرة") is True


def test_is_garbled_presentation_forms_dominated():
    # NFKC-normalizing presentation-forms text collapses it to normal Arabic, so garbled
    # detection must run on the RAW (pre-normalize) text to catch this case.
    presentation_form_heavy = "ﹰﹱﹲﹳﹴﹶﹷ" * 5
    assert is_garbled(presentation_form_heavy) is True


def test_normalize_nfkc():
    assert normalize("ﷲ") == normalize(normalize("ﷲ"))  # idempotent
    assert len(normalize("ﷲ")) >= 1
