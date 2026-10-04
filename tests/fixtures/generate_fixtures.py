#!/usr/bin/env python3
"""Generates the Phase 2 test fixtures (SPEC.md section 9 exit check): text PDF, Arabic
PDF, scanned PDF, DOCX with a table, TXT, HTML, PNG. Real files, not placeholders —
PyMuPDF/python-docx/Pillow write actual parseable documents; the "scanned" PDF embeds a
rendered image with no text layer so the OCR path is genuinely exercised.
"""
import os

import arabic_reshaper
import pymupdf
from bidi.algorithm import get_display
from docx import Document
from PIL import Image, ImageDraw, ImageFont, features

FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))


def find_font(candidates):
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


LATIN_FONT = find_font(
    [
        "/home/ahmed/miniforge3/envs/we/fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    + [os.path.join(r, f) for r, _, fs in os.walk("/home/ahmed/miniforge3/envs/we") for f in fs if f == "DejaVuSans.ttf"]
)
ARABIC_FONT = find_font([os.path.join(FIXTURES_DIR, "NotoSansArabic-Regular.ttf")])


def make_text_pdf():
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        "WE Home Internet Packages\n\n"
        "The Pro package costs 299 EGP per month and offers 100 Mbps download speed.\n\n"
        "Frequently Asked Questions\n\n"
        "What is the contract length?\nThe standard contract length is 12 months.\n"
    )
    page.insert_text((72, 72), text, fontsize=12, fontname="helv")
    out = os.path.join(FIXTURES_DIR, "text_pdf.pdf")
    doc.save(out)
    doc.close()
    print("wrote", out)


# Pillow built with libraqm shapes and orders RTL text itself; pre-applying reshaper + bidi on
# top of that reversed every line twice, so the fixture showed backwards, unjoined Arabic and
# OCR (correctly) read it backwards. Only do it manually when raqm isn't available.
_RAQM = features.check("raqm")


def _shape(text):
    return text if _RAQM else get_display(arabic_reshaper.reshape(text))


def _draw_shaped_arabic(draw, xy, text, font, fill="black"):
    if _RAQM:
        draw.text(xy, text, fill=fill, font=font, direction="rtl", language="ar")
    else:
        draw.text(xy, _shape(text), fill=fill, font=font)


def make_arabic_pdf():
    # PyMuPDF's text-insertion APIs (insert_text and insert_htmlbox, tested) don't
    # correctly shape/reorder Arabic (RTL + letter joining) — the "clean" text layer they
    # produce is actually scrambled, which isn't representative of a real Arabic PDF.
    # Rendering properly-shaped Arabic (arabic_reshaper + python-bidi, the standard tools
    # for this) to an image and embedding it with no text layer is both more reliable and
    # a more realistic fixture: many real-world Arabic PDFs are scans, so this exercises
    # the real OCR fallback path end-to-end, in Arabic.
    lines = [
        "باقات إنترنت المنزل من وي",
        "تبلغ تكلفة باقة برو 299 جنيه شهريًا بسرعة تحميل 100 ميجابت.",
        "الأسئلة الشائعة",
        "ما هي مدة العقد؟",
        "مدة العقد القياسية 12 شهرًا.",
    ]
    img = Image.new("RGB", (1240, 700), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(ARABIC_FONT, 36)
    y = 50
    for line in lines:
        bbox = draw.textbbox((0, 0), _shape(line), font=font, **({"direction": "rtl", "language": "ar"} if _RAQM else {}))
        text_width = bbox[2] - bbox[0]
        _draw_shaped_arabic(draw, (1190 - text_width, y), line, font)
        y += 110
    img_path = os.path.join(FIXTURES_DIR, "_arabic_source.png")
    img.save(img_path)

    doc = pymupdf.open()
    page = doc.new_page(width=1240, height=700)
    page.insert_image(pymupdf.Rect(0, 0, 1240, 700), filename=img_path)
    out = os.path.join(FIXTURES_DIR, "arabic_pdf.pdf")
    doc.save(out)
    doc.close()
    os.remove(img_path)
    print("wrote", out)


def make_scanned_pdf():
    # Render English text to an image, then embed ONLY the image (no text layer) so the
    # loader's garbled/empty-text check must trigger and route through real Tesseract OCR.
    img = Image.new("RGB", (1240, 400), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(LATIN_FONT, 32) if LATIN_FONT else ImageFont.load_default()
    draw.text((40, 40), "WE Mobile Control Package", fill="black", font=font)
    draw.text((40, 100), "Monthly price is 199 EGP including 20GB data.", fill="black", font=font)
    draw.text((40, 160), "Contract length: 12 months.", fill="black", font=font)
    img_path = os.path.join(FIXTURES_DIR, "_scanned_source.png")
    img.save(img_path)

    doc = pymupdf.open()
    page = doc.new_page(width=1240, height=400)
    page.insert_image(pymupdf.Rect(0, 0, 1240, 400), filename=img_path)
    out = os.path.join(FIXTURES_DIR, "scanned_pdf.pdf")
    doc.save(out)
    doc.close()
    os.remove(img_path)
    print("wrote", out)


def make_docx_with_table():
    doc = Document()
    doc.add_heading("WE Mobile Packages", level=1)
    doc.add_paragraph("This document summarizes available mobile packages and pricing.")
    doc.add_heading("Pricing Table", level=2)
    table = doc.add_table(rows=1, cols=3)
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Package", "Data", "Price"
    rows = [("Basic", "10GB", "99 EGP"), ("Plus", "25GB", "149 EGP"), ("Premium", "50GB", "249 EGP")]
    for pkg, data, price in rows:
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = pkg, data, price
    doc.add_heading("Terms", level=2)
    doc.add_paragraph("All prices include VAT and are valid for a 12-month contract.")
    out = os.path.join(FIXTURES_DIR, "mobile_packages.docx")
    doc.save(out)
    print("wrote", out)


def make_txt():
    out = os.path.join(FIXTURES_DIR, "support_hours.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(
            "WE Customer Support Hours\n\n"
            "Our call center is available 24/7 at 111.\n"
            "Live chat support is available from 9 AM to 9 PM daily.\n"
        )
    print("wrote", out)


def make_html():
    out = os.path.join(FIXTURES_DIR, "roaming_faq.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(
            "<html><head><title>Roaming FAQ</title></head><body>"
            "<nav>Skip this nav</nav>"
            "<h1>International Roaming</h1>"
            "<p>Roaming is available in 150 countries worldwide.</p>"
            "<h2>How much does roaming cost per day?</h2>"
            "<p>The daily roaming bundle costs 150 EGP and includes 500MB of data.</p>"
            "<footer>Skip this footer</footer>"
            "</body></html>"
        )
    print("wrote", out)


def make_png():
    img = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(LATIN_FONT, 28) if LATIN_FONT else ImageFont.load_default()
    draw.text((30, 30), "WE Store Locations", fill="black", font=font)
    draw.text((30, 90), "Visit any WE store nationwide for device upgrades.", fill="black", font=font)
    out = os.path.join(FIXTURES_DIR, "store_locations.png")
    img.save(out)
    print("wrote", out)


if __name__ == "__main__":
    make_text_pdf()
    make_arabic_pdf()
    make_scanned_pdf()
    make_docx_with_table()
    make_txt()
    make_html()
    make_png()
