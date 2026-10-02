"""Tesseract OCR wrapper, ara+eng (SPEC.md section 7)."""
import io

import pytesseract
from PIL import Image


def ocr_image(image: Image.Image, lang: str = "ara+eng") -> str:
    return pytesseract.image_to_string(image, lang=lang)


def ocr_bytes(data: bytes, lang: str = "ara+eng") -> str:
    image = Image.open(io.BytesIO(data))
    return ocr_image(image, lang=lang)
