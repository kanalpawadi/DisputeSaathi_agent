"""Read bill photos: Claude vision -> Tesseract -> known demo sample -> give up (human check)."""
import hashlib
import mimetypes
import re

from . import db, llm


def _parse(text: str):
    inv = re.search(r"(INV[-\w]+)", text)
    order = re.search(r"(ORD\d+[A-Z]?)", text)
    total = re.search(r"TOTAL[^\d]*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    return dict(invoice_number=inv.group(1) if inv else "", order_id=order.group(1) if order else "",
                total_amount=float(total.group(1).replace(",", "")) if total else 0.0, full_text=text)


def read(path: str):
    data = open(path, "rb").read()
    media = mimetypes.guess_type(path)[0] or "image/png"
    if llm.can_read_images() and media.startswith("image/"):
        try:
            out = llm.read_bill(data, media)
            out["method"] = f"{llm.name()} vision"
            return out
        except Exception as e:  # noqa: BLE001
            err = str(e)
    else:
        err = None
    try:
        import pytesseract
        from PIL import Image
        text = pytesseract.image_to_string(Image.open(path))
        if text.strip():
            return {**_parse(text), "method": "Tesseract OCR"}
    except Exception:  # noqa: BLE001
        pass
    sha = hashlib.sha256(data).hexdigest()
    known = db.q1("SELECT text FROM sample_bills WHERE sha256=?", (sha,))
    if known:
        return {**_parse(known["text"]), "method": "Offline demo OCR (known sample)"}
    return dict(invoice_number="", order_id="", total_amount=0.0, full_text="",
                method="Unreadable" + (f" ({err})" if err else ""))
