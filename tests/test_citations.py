from app.generation.citations import validate

SOURCES = [
    {
        "label": "S1",
        "title": "Home Internet Packages",
        "url": "https://te.eg/personal/home",
        "filename": None,
        "page": None,
        "section": "Pricing",
        "text": "The Pro package costs 299 EGP per month and offers 100 Mbps.",
    },
    {
        "label": "S2",
        "title": "FAQs",
        "url": "https://te.eg/about-te/faq",
        "filename": None,
        "page": None,
        "section": "Billing",
        "text": "Bills are issued on the 1st of every month.",
    },
]


def test_valid_citation_is_kept():
    result = validate("The Pro package costs 299 EGP per month [S1].", SOURCES)
    assert "[S1]" in result["text"]
    assert result["has_valid_citations"] is True
    assert len(result["citations"]) == 1
    assert result["citations"][0]["label"] == "S1"
    assert result["numeric_warning"] is False


def test_unknown_label_is_dropped():
    result = validate("The price is 299 EGP [S1][S9].", SOURCES)
    assert "[S9]" not in result["text"]
    assert "[S1]" in result["text"]
    assert result["has_valid_citations"] is True


def test_numeric_mismatch_flagged():
    result = validate("The Pro package costs 350 EGP per month [S1].", SOURCES)
    assert result["numeric_warning"] is True


def test_numeric_match_across_multiple_sources():
    result = validate("Bills come monthly, on the 1st [S2], for 299 EGP [S1].", SOURCES)
    assert result["numeric_warning"] is False


def test_no_citation_at_all():
    result = validate("I'm not sure about that.", SOURCES)
    assert result["has_valid_citations"] is False
    assert result["citations"] == []


def test_arabic_indic_digits_match_western_digits():
    # Found via real end-to-end testing: an answer written with Eastern Arabic-Indic
    # numerals (١٠٠) must match the same number in a source written with Western
    # digits (100), or every Arabic answer gets a spurious numeric_mismatch warning.
    result = validate("حصل على ٢٩٩ جنيه شهريًا [S1].", SOURCES)
    assert result["numeric_warning"] is False


def test_hallucinated_number_still_flagged():
    # A number with no counterpart anywhere in the cited source(s) must still be caught.
    result = validate("The price is 999 EGP [S1].", SOURCES)
    assert result["numeric_warning"] is True


def test_list_numbering_is_not_a_fact_to_verify():
    """A 9-step answer was flagged numeric_warning because "8" and "9" (its own list
    numbering) don't appear in the sources."""
    sources = [{"label": "S1", "title": "kb", "url": None, "filename": "kb.docx", "page": None,
                "section": None, "text": "Choose the invoice type, then add the customer."}]
    answer = "\n".join(f"{i}. step [S1]." for i in range(1, 10))
    assert validate(answer, sources)["numeric_warning"] is False
    # a real unverified number inside a list item is still caught
    assert validate("1. It costs 450 EGP [S1].", sources)["numeric_warning"] is True
