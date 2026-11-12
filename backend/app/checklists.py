"""Proof checklist per dispute reason. Scoring is done by code, never by the AI (fix #5)."""

REASONS = {
    "ITEM_NOT_RECEIVED": "Item not received",
    "SERVICE_NOT_PROVIDED": "Service not given",
    "DUPLICATE_PAYMENT": "Charged twice",
    "INCORRECT_AMOUNT": "Wrong amount",
    "REFUND_NOT_PROCESSED": "Already refunded / refund not received",
    "UNAUTHORIZED": "\"I didn't make this payment\"",
}

# key, label, required, weight
CHECKLISTS = {
    "ITEM_NOT_RECEIVED": [
        ("payment", "Payment record from Paytm", True, 0.10),
        ("order", "Order record", True, 0.15),
        ("invoice", "Bill / invoice matching the amount", True, 0.20),
        ("delivery", "Delivery proof (status + OTP/signature)", True, 0.40),
        ("chat", "Customer conversation", False, 0.15),
    ],
    "SERVICE_NOT_PROVIDED": [
        ("payment", "Payment record from Paytm", True, 0.10),
        ("order", "Booking / subscription record", True, 0.15),
        ("invoice", "Bill / invoice matching the amount", True, 0.20),
        ("service_usage", "Proof the service was used (check-ins, logs)", True, 0.45),
        ("chat", "Customer conversation", False, 0.10),
    ],
    "DUPLICATE_PAYMENT": [
        ("payment", "Payment record from Paytm", True, 0.15),
        ("order", "Order record", True, 0.15),
        ("invoice", "Bill / invoice", True, 0.20),
        ("separate_orders", "Each payment maps to its own order", True, 0.50),
    ],
    "INCORRECT_AMOUNT": [
        ("payment", "Payment record from Paytm", True, 0.15),
        ("order", "Order record with price breakup", True, 0.25),
        ("invoice", "Bill showing the charged amount", True, 0.45),
        ("delivery", "Delivery record", False, 0.15),
    ],
    "REFUND_NOT_PROCESSED": [
        ("payment", "Payment record from Paytm", True, 0.15),
        ("order", "Order record", True, 0.10),
        ("refund", "Refund record with bank reference (RRN)", True, 0.60),
        ("delivery", "Return / delivery record", False, 0.15),
    ],
    "UNAUTHORIZED": [
        ("payment", "Payment record from Paytm", True, 0.10),
        ("order", "Order record", True, 0.20),
        ("invoice", "Bill / invoice", False, 0.20),
        ("delivery", "Delivery proof to customer's address", False, 0.30),
        ("chat", "Customer conversation", False, 0.20),
    ],
}

POD_QUALITY = {"OTP_VERIFIED": 1.0, "SIGNATURE": 0.8, "RETURN_SCAN": 0.8}


def evaluate(reason: str, ev: dict) -> dict:
    """ev = evidence collected by the agent. Returns checklist, score and derived facts."""
    pay = ev.get("payment") or {}
    amount = pay.get("amount")
    items, facts = [], {"duplicate_confirmed": False, "invoice_mismatch": False}

    def status_for(key):
        if key == "payment":
            return ("FOUND", 1.0, f"{pay.get('txn_id')} - {pay.get('status')}") if pay.get("status") == "SUCCESS" \
                else ("MISSING", 0, "No successful payment found")
        if key == "order":
            o = ev.get("order")
            return ("FOUND", 1.0, f"{o['order_id']} - {', '.join(o['items'])}") if o else ("MISSING", 0, "No order record")
        if key == "invoice":
            inv = ev.get("invoice")
            if not inv:
                return "MISSING", 0, "No bill in billing system"
            if amount is not None and abs(inv["amount"] - amount) > 1:
                facts["invoice_mismatch"] = True
                return "CONTRADICTS", 0, f"Bill shows Rs {inv['amount']:.0f} but payment is Rs {amount:.0f}"
            src = " (from merchant photo)" if inv.get("source") == "MERCHANT_PHOTO" else ""
            return "FOUND", 1.0, f"{inv['invoice_id']} - Rs {inv['amount']:.0f}{src}"
        if key == "delivery":
            d = ev.get("delivery")
            if not d:
                return "MISSING", 0, "No delivery record"
            if d["status"] in ("DELIVERED", "RETURNED") and d.get("pod") in POD_QUALITY:
                q = POD_QUALITY[d["pod"]]
                return ("FOUND" if q == 1 else "PARTIAL"), q, f"{d['courier']} {d['awb']}: {d['status']} ({d['pod']})"
            return "WEAK", 0.25, f"{d['courier']}: {d['status']} - no proof of delivery"
        if key == "chat":
            c = ev.get("chats") or []
            return ("FOUND", 1.0, f"{len(c)} messages") if c else ("MISSING", 0, "No conversation on record")
        if key == "service_usage":
            logs = ev.get("service_logs") or []
            return ("FOUND", 1.0, f"{len(logs)} check-ins recorded") if logs else ("MISSING", 0, "No usage logs")
        if key == "refund":
            ok = [r for r in ev.get("refunds") or [] if r["status"] == "SUCCESS" and r.get("rrn")]
            return ("FOUND", 1.0, f"Refund {ok[0]['refund_id']} Rs {ok[0]['amount']:.0f}, RRN {ok[0]['rrn']}") \
                if ok else ("MISSING", 0, "No processed refund found")
        if key == "separate_orders":
            if "related_payments" not in ev:
                return "MISSING", 0, "Other payments by this customer not checked yet"
            related = ev.get("related_payments") or []
            same_order = [p for p in related if p["order_id"] == pay.get("order_id")]
            if len(same_order) > 1:
                facts["duplicate_confirmed"] = True
                return "CONTRADICTS", 0, f"{len(same_order)} payments for the same order {pay.get('order_id')}"
            others = [p for p in related if p["order_id"] != pay.get("order_id")]
            if others:
                return "FOUND", 1.0, f"Other payment {others[0]['txn_id']} is for a different order {others[0]['order_id']}"
            return "FOUND", 1.0, "Only one payment exists for this order"
        return "MISSING", 0, ""

    score = 0.0
    missing_required = []
    for key, label, required, weight in CHECKLISTS[reason]:
        st, q, note = status_for(key)
        score += weight * q
        if required and st == "MISSING":
            missing_required.append(key)
        items.append(dict(key=key, label=label, required=required, weight=weight, status=st, note=note))
    return dict(items=items, proof_score=round(score, 2), missing_required=missing_required, **facts)
