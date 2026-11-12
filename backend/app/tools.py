"""The FIXED list of tools the AI agent may use. The data tools are read-only connectors to Paytm and
merchant systems. The agent's only way to act is propose_action, which agent.py checks against the
policy guardrails (rules.yaml) before executing; no tool sends money directly, and accepting always
needs the merchant's OTP."""
from datetime import timedelta

from . import clock, db


def get_payment(txn_id: str):
    return db.q1("SELECT * FROM paytm_payments WHERE txn_id=?", (txn_id,))


def get_related_payments(txn_id: str):
    """Payments by the same customer to the same merchant, same amount, within 48h."""
    p = get_payment(txn_id)
    if not p:
        return []
    t = clock.parse(p["created_at"])
    rows = db.q("SELECT * FROM paytm_payments WHERE merchant_id=? AND customer_vpa=? AND amount=? AND status='SUCCESS'",
                (p["merchant_id"], p["customer_vpa"], p["amount"]))
    return [r for r in rows if abs(clock.parse(r["created_at"]) - t) <= timedelta(hours=48)]


def get_order(order_id: str):
    return db.q1("SELECT * FROM orders WHERE order_id=?", (order_id,))


def get_invoice(order_id: str):
    return db.q1("SELECT * FROM invoices WHERE order_id=? ORDER BY issued_at DESC", (order_id,))


def get_delivery(order_id: str):
    return db.q1("SELECT * FROM deliveries WHERE order_id=?", (order_id,))


def get_refunds(txn_id: str):
    return db.q("SELECT * FROM refunds WHERE txn_id=?", (txn_id,))


def get_chats(order_id: str):
    return db.q("SELECT sender, text, ts FROM chats WHERE order_id=? ORDER BY ts", (order_id,))


def get_service_logs(order_id: str):
    return db.q("SELECT kind, detail, ts FROM service_logs WHERE order_id=? ORDER BY ts", (order_id,))


# name -> (function, argument name, evidence key, system label, description)
TOOLS = {
    "get_payment": (get_payment, "txn_id", "payment", "Paytm Payments",
                    "Fetch the Paytm payment record for a transaction id."),
    "get_related_payments": (get_related_payments, "txn_id", "related_payments", "Paytm Payments",
                             "Find other payments from the same customer, same amount, within 48 hours. Use for 'charged twice' disputes."),
    "get_order": (get_order, "order_id", "order", "Merchant order system",
                  "Fetch the merchant's order record (items, amount, address)."),
    "get_invoice": (get_invoice, "order_id", "invoice", "Merchant billing",
                    "Fetch the bill / invoice for an order, if one exists in the billing system."),
    "get_delivery": (get_delivery, "order_id", "delivery", "Courier tracking",
                     "Fetch courier tracking and proof of delivery for an order."),
    "get_refunds": (get_refunds, "txn_id", "refunds", "Paytm Refunds",
                    "Fetch refunds already processed for a transaction."),
    "get_chats": (get_chats, "order_id", "chats", "Merchant chat inbox",
                  "Fetch the conversation between merchant and customer about an order."),
    "get_service_logs": (get_service_logs, "order_id", "service_logs", "Service / attendance system",
                         "Fetch service usage logs (gym check-ins, sessions attended) for a booking."),
}


def schemas():
    return [dict(name=name, description=desc, strict=True,
                 input_schema={"type": "object", "properties": {arg: {"type": "string"}},
                               "required": [arg], "additionalProperties": False})
            for name, (_, arg, _, _, desc) in TOOLS.items()]


EVIDENCE_KEYS = ["payment", "order", "invoice", "delivery", "chat", "service_usage", "refund", "separate_orders"]


def agent_schemas():
    """Read tools + the two agent tools. propose_action is the ONLY way the agent can act, and every
    call goes through the policy guardrails (rules.guard) before agent.py executes anything."""
    out = schemas()
    out.append(dict(name="check_proof", strict=True,
                    description="Score the proof gathered so far against this dispute's checklist (computed by "
                                "code, not by you). Returns each item's status, the proof score, what required "
                                "proof is missing, and the case facts the policy engine uses.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False}))
    out.append(dict(name="propose_action", strict=True,
                    description="Propose the next step for this case. The policy engine checks it: if a guardrail "
                                "blocks it you get the reason and must choose again. Actions: "
                                "FIGHT = send the proof pack to defend the merchant (the engine decides whether it "
                                "auto-sends or needs the merchant's OTP); "
                                "SUGGEST_ACCEPT = suggest the merchant gives up this dispute (merchant must approve "
                                "with OTP; never automatic); "
                                "REQUEST_EVIDENCE = ask the merchant for missing proof (list it in 'items'); "
                                "WAIT_FOR_MERCHANT = keep waiting for proof already asked for; "
                                "ESCALATE = hand to Paytm's human dispute team with a note.",
                    input_schema={"type": "object", "additionalProperties": False,
                                  "required": ["action", "reasoning", "items"],
                                  "properties": {
                                      "action": {"type": "string", "enum": ["FIGHT", "SUGGEST_ACCEPT", "REQUEST_EVIDENCE",
                                                                            "WAIT_FOR_MERCHANT", "ESCALATE"]},
                                      "reasoning": {"type": "string",
                                                    "description": "One or two plain sentences: why this step, based on the proof."},
                                      "items": {"type": "array", "items": {"type": "string", "enum": EVIDENCE_KEYS},
                                                "description": "For REQUEST_EVIDENCE: the checklist keys to ask for. Otherwise []."},
                                  }}))
    return out


def run(name, arg_value):
    fn, _, key, system, _ = TOOLS[name]
    return key, system, fn(arg_value)


def summarize(name, result):
    if result is None or result == []:
        return "nothing found"
    if name == "get_payment":
        return f"Rs {result['amount']:.0f} via {result['method']}, {result['status']}"
    if name == "get_related_payments":
        return f"{len(result)} matching payment(s): " + ", ".join(f"{p['txn_id']}->{p['order_id']}" for p in result)
    if name == "get_order":
        return f"{', '.join(result['items'])} - Rs {result['amount']:.0f}"
    if name == "get_invoice":
        return f"{result['invoice_id']} for Rs {result['amount']:.0f}"
    if name == "get_delivery":
        return f"{result['courier']} {result['status']} (proof: {result.get('pod') or 'none'})"
    if name == "get_refunds":
        return ", ".join(f"{r['refund_id']} Rs {r['amount']:.0f} {r['status']} RRN {r['rrn']}" for r in result)
    if name in ("get_chats", "get_service_logs"):
        return f"{len(result)} record(s)"
    return "ok"


# Which tools a careful human would check for each reason (used by offline planner).
PLAN = {
    "ITEM_NOT_RECEIVED": ["get_payment", "get_order", "get_invoice", "get_delivery", "get_chats"],
    "SERVICE_NOT_PROVIDED": ["get_payment", "get_order", "get_invoice", "get_service_logs", "get_chats"],
    "DUPLICATE_PAYMENT": ["get_payment", "get_related_payments", "get_order", "get_invoice"],
    "INCORRECT_AMOUNT": ["get_payment", "get_order", "get_invoice", "get_delivery"],
    "REFUND_NOT_PROCESSED": ["get_payment", "get_order", "get_refunds", "get_delivery", "get_chats"],
    "UNAUTHORIZED": ["get_payment", "get_order", "get_invoice", "get_delivery", "get_chats"],
}
