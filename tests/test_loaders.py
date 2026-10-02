import os

from app.ingestion.loaders import detect_doc_type, load_docx, load_html, load_image, load_pdf, load_txt

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


def test_detect_doc_type_matches_extension_and_magic():
    assert detect_doc_type("text_pdf.pdf", _read("text_pdf.pdf")) == "pdf"
    assert detect_doc_type("mobile_packages.docx", _read("mobile_packages.docx")) == "docx"
    assert detect_doc_type("store_locations.png", _read("store_locations.png")) == "png"
    assert detect_doc_type("support_hours.txt", _read("support_hours.txt")) == "txt"
    assert detect_doc_type("roaming_faq.html", _read("roaming_faq.html")) == "html"


def test_detect_doc_type_rejects_mismatched_extension():
    # a PNG's real magic bytes renamed with a .pdf extension must not be accepted as a PDF
    assert detect_doc_type("fake.pdf", _read("store_locations.png")) is None


def test_text_pdf_extracts_without_ocr():
    title, blocks = load_pdf(_read("text_pdf.pdf"))
    assert any("299 EGP" in b["text"] for b in blocks)
    assert all(b["ocr"] is False for b in blocks)
    assert all(b["page"] == 1 for b in blocks)


def test_scanned_pdf_triggers_ocr_and_recovers_text():
    title, blocks = load_pdf(_read("scanned_pdf.pdf"))
    assert blocks, "OCR should have produced at least one block"
    assert all(b["ocr"] is True for b in blocks)
    joined = " ".join(b["text"] for b in blocks)
    assert "199" in joined
    assert "12" in joined


def test_arabic_pdf_triggers_ocr():
    # Rendered as an image (see tests/fixtures/generate_fixtures.py) so it must OCR, not
    # extract a text layer. Tesseract's Arabic accuracy on synthetic renders is imperfect
    # (documented limitation), so this only asserts OCR ran and recovered the numbers,
    # not perfect letter-for-letter Arabic text.
    title, blocks = load_pdf(_read("arabic_pdf.pdf"))
    assert blocks
    assert all(b["ocr"] is True for b in blocks)
    joined = " ".join(b["text"] for b in blocks)
    assert "299" in joined
    assert "100" in joined


def test_docx_table_becomes_header_value_rows():
    title, blocks = load_docx(_read("mobile_packages.docx"))
    headings = [b["text"] for b in blocks if b["type"] == "heading"]
    assert "WE Mobile Packages" in headings
    table_rows = [b["text"] for b in blocks if b["type"] == "table_row"]
    assert any("Package: Basic" in r and "Price: 99 EGP" in r for r in table_rows)


def test_txt_decodes_utf8():
    text = load_txt(_read("support_hours.txt"))
    assert "111" in text


def test_txt_falls_back_to_cp1256():
    data = "مرحبا بكم".encode("cp1256")
    text = load_txt(data)
    assert text == "مرحبا بكم"


def test_html_strips_nav_and_footer():
    title, blocks = load_html(load_txt(_read("roaming_faq.html")))
    assert title == "Roaming FAQ"
    texts = [b["text"] for b in blocks]
    assert not any("Skip this" in t for t in texts)
    assert any("150 countries" in t for t in texts)


def test_png_ocr_extracts_text():
    title, blocks = load_image(_read("store_locations.png"))
    assert all(b["ocr"] is True for b in blocks)
    assert any("Store Locations" in b["text"] for b in blocks)
