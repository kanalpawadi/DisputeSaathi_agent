"""Prompt-injection detection and PII masking.

Text from bills, chats, customer messages and uploads is DATA. We scan it, record any
instruction-like content, and pass it to the LLM only inside <untrusted_data> tags.
Even if the LLM were fooled, it has no tool that moves money or submits anything.
"""
import re

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above)? ?(rules|instructions|prompts?)",
    r"disregard (all |the )?(rules|instructions)",
    r"system (note|prompt|message|override)",
    r"(note|message|instruction)s? to (the )?(ai|agent|assistant|model)",
    r"you are now", r"act as (an? )?(admin|system)",
    r"accept (this|the) dispute", r"approve (this|the) (refund|dispute)",
    r"(refund|transfer|send) (rs\.?|inr|₹)? ?\d+ to", r"upi id",
]
_rx = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


def scan(text: str):
    if not text:
        return []
    spans = [(m.start(), m.end()) for m in _rx.finditer(text)]
    if not spans:
        return []
    start = max(0, spans[0][0] - 40)
    end = min(len(text), spans[-1][1] + 60)
    return [text[start:end].replace("\n", " ").strip()]  # one flag per text, covering all matches


def wrap_untrusted(label: str, text: str) -> str:
    return f'<untrusted_data source="{label}">\n{text}\n</untrusted_data>'


_PHONE = re.compile(r"(?<!\d)(\+?91)?[6-9]\d{5}(\d{4})(?!\d)")
_VPA = re.compile(r"\b([a-zA-Z0-9._-])[a-zA-Z0-9._-]*@([a-zA-Z]+)\b")
_EMAIL = re.compile(r"\b([a-zA-Z0-9])[a-zA-Z0-9._%+-]*@([a-zA-Z0-9.-]+\.[a-z]{2,})\b")


def mask_str(s: str) -> str:
    s = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", s)
    s = _VPA.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", s)
    s = _PHONE.sub(lambda m: f"******{m.group(2)}", s)
    return s


def mask(obj):
    """Mask customer personal details before writing to the audit log (DPDP)."""
    if isinstance(obj, str):
        return mask_str(obj)
    if isinstance(obj, list):
        return [mask(v) for v in obj]
    if isinstance(obj, dict):
        return {k: mask(v) for k, v in obj.items()}
    return obj
