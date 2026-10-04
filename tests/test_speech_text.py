from app.speech_text import clean_for_tts, echoes_hotwords, voice_for_lang


def test_citation_labels_stripped():
    out = clean_for_tts("The price is 299 EGP [S1] per month [S2].", "en")
    assert "[S1]" not in out
    assert "[S2]" not in out


def test_urls_stripped():
    out = clean_for_tts("See https://te.eg/personal/home for details.", "en")
    assert "https://" not in out


def test_markdown_stripped():
    out = clean_for_tts("**Bold** and _italic_ and `code` and # heading", "en")
    assert "*" not in out and "_" not in out and "`" not in out and "#" not in out


def test_numbers_verbalized_english():
    out = clean_for_tts("It costs 299 EGP.", "en")
    assert "299" not in out
    assert "two hundred" in out.lower()


def test_numbers_verbalized_arabic():
    out = clean_for_tts("تكلف 12 جنيه.", "ar")
    assert "12" not in out
    assert "اثنا عشر" in out


def test_lexicon_applied_for_arabic_only():
    out_ar = clean_for_tts("WE Wi-Fi package", "ar")
    assert "وي" in out_ar
    assert "واي فاي" in out_ar

    out_en = clean_for_tts("WE Wi-Fi package", "en")
    assert "WE" in out_en  # lexicon (Arabic respellings) must not apply to English


def test_voice_for_lang():
    assert voice_for_lang("ar") == "ar"
    assert voice_for_lang("en") == "en"
    assert voice_for_lang("auto") == "en"


HOTWORDS = "WE, WE Telecom Egypt, تيداتا, إنترنت المنزل, فاتورة, باقة, إعادة شحن, واي فاي"


def test_hotword_prompt_echo_detected():
    # seen live: clipped audio came back as the hotword list itself
    assert echoes_hotwords("ما إنترنت المنزل, فاتورة, باقة, إنترنت المنزل, فاتورة, باقة", HOTWORDS)
    assert echoes_hotwords("WE Telecom Egypt, باقة، فاتورة.", HOTWORDS)


def test_real_questions_mentioning_products_are_not_echoes():
    assert not echoes_hotwords("عايز أعرف سعر باقة الإنترنت المنزلي", HOTWORDS)
    assert not echoes_hotwords("What is the price of the WE Air package?", HOTWORDS)
    assert not echoes_hotwords("باقة", HOTWORDS)  # a one-word answer is not an echo


def test_echo_with_filler_fragments_detected():
    # second variant seen live: prompt words mixed with fragments
    assert echoes_hotwords("فاتورة. ما إنترنت المنزل, فلما إنترنت المنزل, فلما إنترنت", HOTWORDS)


def test_number_glued_to_unit_is_spoken_separately():
    out = clean_for_tts("Basic offers 10GB for 99 EGP.", "en")
    assert "tenGB" not in out and "ten GB" in out
