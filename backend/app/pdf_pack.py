"""Evidence pack PDF: summary, timeline, checklist and each proof clearly labelled."""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .checklists import REASONS
from .config import PACK_DIR

NAVY = colors.HexColor("#002E6E")
CYAN = colors.HexColor("#00BAF2")


def _esc(s):
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(case: dict, merchant: dict, evidence: dict, checklist: dict, timeline: list, version: int) -> str:
    path = Path(PACK_DIR) / f"{case['id']}_v{version}.pdf"
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=NAVY, fontSize=18, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=NAVY, fontSize=12, spaceBefore=10)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=8, textColor=colors.grey)

    def table(rows, widths, header=True):
        t = Table([[Paragraph(_esc(c), body) for c in r] for r in rows], colWidths=widths, repeatRows=1 if header else 0)
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C8D3E6")),
                 ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F7FD"))]
        t.setStyle(TableStyle(style))
        return t

    pay = evidence.get("payment") or {}
    story = [
        Paragraph("Dispute Response - Evidence Pack", h1),
        Paragraph(f"Prepared by DisputeSaathi for {_esc(merchant['business'])} &nbsp;|&nbsp; Version {version}"
                  + (" (second reply)" if version > 1 else ""), small),
        Spacer(1, 6),
        table([["Case", case["id"], "Paytm dispute", case["dispute_id"]],
               ["Transaction", case["txn_id"], "Order", case["order_id"]],
               ["Amount", f"INR {case['amount']:,.2f}", "Reason", REASONS.get(case["reason"], case["reason"])],
               ["Payment date", (pay.get("created_at") or "")[:16], "Response due", case["deadline_at"][:16]]],
              [30 * mm, 55 * mm, 30 * mm, 55 * mm], header=False),
        Paragraph("Summary", h2),
        Paragraph(_esc(case.get("summary") or ""), body),
        Paragraph("Proof checklist", h2),
        table([["#", "Proof", "Status", "Detail"]] +
              [[f"E{i + 1}", it["label"], it["status"], it["note"]] for i, it in enumerate(checklist["items"])],
              [10 * mm, 55 * mm, 25 * mm, 80 * mm]),
        Paragraph(f"Proof strength (computed by checklist rules): {checklist['proof_score']:.2f} / 1.00", small),
        Paragraph("Timeline", h2),
        table([["When", "What happened"]] + [[t[0][:16].replace("T", " "), t[1]] for t in timeline],
              [40 * mm, 130 * mm]),
        Paragraph("Evidence details", h2),
    ]
    order = evidence.get("order")
    if order:
        story.append(Paragraph(f"<b>Order {order['order_id']}</b>: {_esc(', '.join(order['items']))} - INR "
                               f"{order['amount']:,.2f}, created {order['created_at'][:16]}", body))
    inv = evidence.get("invoice")
    if inv:
        story.append(Paragraph(f"<b>Invoice {inv['invoice_id']}</b> (source: {inv.get('source')}):", body))
        story.append(Paragraph(_esc(inv.get("text", "")).replace("\n", "<br/>"), small))
        if inv.get("image_path") and Path(inv["image_path"]).exists():
            story.append(Image(inv["image_path"], width=70 * mm, height=59 * mm))
    d = evidence.get("delivery")
    if d:
        story.append(Paragraph(f"<b>Delivery {d['awb']}</b> via {_esc(d['courier'])}: {d['status']}, proof "
                               f"{d.get('pod') or 'none'}, at {(d.get('delivered_at') or '-')[:16]}", body))
        for e in d.get("events") or []:
            story.append(Paragraph(f"&nbsp;&nbsp;{e['ts'][:16]} - {e['status']} {_esc(e.get('note', ''))}", small))
    for r in evidence.get("refunds") or []:
        story.append(Paragraph(f"<b>Refund {r['refund_id']}</b>: INR {r['amount']:,.2f} {r['status']} on "
                               f"{r['processed_at'][:16]}, bank RRN {r['rrn']}", body))
    for p in evidence.get("related_payments") or []:
        story.append(Paragraph(f"<b>Payment {p['txn_id']}</b> for order {p['order_id']}: INR {p['amount']:,.2f} "
                               f"at {p['created_at'][:16]}", body))
    logs = evidence.get("service_logs") or []
    if logs:
        story.append(Paragraph("<b>Service usage logs</b>:", body))
        for lg in logs:
            story.append(Paragraph(f"&nbsp;&nbsp;{lg['ts'][:16]} - {_esc(lg['detail'])}", small))
    chats = evidence.get("chats") or []
    if chats:
        story.append(Paragraph("<b>Customer conversation</b>:", body))
        for c in chats:
            story.append(Paragraph(f"&nbsp;&nbsp;{c['ts'][:16]} {c['sender']}: {_esc(c['text'])}", small))
    flags = case.get("injection_flags") or []
    if flags:
        story += [Paragraph("Security note", h2),
                  Paragraph(f"{len(flags)} instruction-like text(s) were found inside the evidence and treated as "
                            "data only. They had no effect on this response.", small)]
    story += [Spacer(1, 10), Paragraph("Customer personal details are limited to what this dispute needs. "
                                       "Generated automatically; every step is in the DisputeSaathi audit log.", small)]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=15 * mm, bottomMargin=15 * mm, title=f"Dispute {case['dispute_id']}").build(story)
    return str(path)
