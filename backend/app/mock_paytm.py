"""Mock Paytm dispute system, shaped after Paytm's public dispute flow:
list / fetch / accept / defend (with documents), plus a webhook alert to the merchant's
integration. Idempotency-Key makes double-sending impossible on the API side too."""
import hashlib
import hmac
import json
import threading
import uuid
from datetime import timedelta

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from . import clock, db
from .config import PAYTM_API_KEY, SELF_URL, WEBHOOK_SECRET

router = APIRouter(prefix="/mock-paytm", tags=["mock-paytm"])


def _auth(key):
    if key != PAYTM_API_KEY:
        raise HTTPException(401, "invalid api key")


def _get(dispute_id):
    d = db.q1("SELECT * FROM paytm_disputes WHERE dispute_id=?", (dispute_id,))
    if not d:
        raise HTTPException(404, "dispute not found")
    return d


def sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _public(d):
    return {k: d[k] for k in ("dispute_id", "txn_id", "merchant_id", "reason_code", "customer_message", "amount",
                              "raised_at", "deadline_at", "status")}


@router.get("/v1/disputes")
def list_disputes(merchant_id: str, x_api_key: str = Header(None)):
    _auth(x_api_key)
    return [_public(d) for d in db.q("SELECT * FROM paytm_disputes WHERE merchant_id=? AND status!='NOT_RAISED'",
                                     (merchant_id,))]


@router.get("/v1/disputes/{dispute_id}")
def fetch_dispute(dispute_id: str, x_api_key: str = Header(None)):
    _auth(x_api_key)
    return _public(_get(dispute_id))


@router.post("/v1/disputes/{dispute_id}/defend")
async def defend(dispute_id: str, request: Request, x_api_key: str = Header(None),
                 idempotency_key: str = Header(None)):
    _auth(x_api_key)
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header required")
    d = _get(dispute_id)
    body = await request.json()
    if d["idempotency_key"] == idempotency_key:
        return {"status": d["status"], "dispute_id": dispute_id, "replayed": True}
    if d["status"] not in ("OPEN", "PRE_ARB"):
        raise HTTPException(409, f"dispute already {d['status']}")
    ref = "PTMD" + uuid.uuid4().hex[:10].upper()
    db.update("paytm_disputes", "dispute_id", dispute_id,
              dict(status="DEFENDED", submission={**body, "reference": ref, "at": clock.now_iso()},
                   idempotency_key=idempotency_key))
    return {"status": "DEFENDED", "dispute_id": dispute_id, "reference": ref,
            "message": "Documents received. Paytm dispute team will review."}


@router.post("/v1/disputes/{dispute_id}/accept")
async def accept(dispute_id: str, request: Request, x_api_key: str = Header(None),
                 idempotency_key: str = Header(None)):
    _auth(x_api_key)
    d = _get(dispute_id)
    if d["idempotency_key"] == idempotency_key and d["status"] == "ACCEPTED":
        return {"status": "ACCEPTED", "dispute_id": dispute_id, "replayed": True}
    if d["status"] not in ("OPEN", "PRE_ARB"):
        raise HTTPException(409, f"dispute already {d['status']}")
    db.update("paytm_disputes", "dispute_id", dispute_id, dict(status="ACCEPTED", idempotency_key=idempotency_key))
    return {"status": "ACCEPTED", "dispute_id": dispute_id,
            "message": "Amount will be returned to the original payment source."}


def raise_dispute(dispute_id: str):
    """Simulate a customer raising the dispute with their bank -> Paytm alerts the merchant."""
    d = _get(dispute_id)
    if d["status"] != "NOT_RAISED":
        return d
    now = clock.now()
    deadline = (now + timedelta(hours=d["deadline_hours"])) if d["deadline_hours"] \
        else clock.add_working_days(now, 7)
    db.update("paytm_disputes", "dispute_id", dispute_id,
              dict(status="OPEN", raised_at=clock.iso(now), deadline_at=clock.iso(deadline)))
    d = _get(dispute_id)
    payload = json.dumps({"event": "DISPUTE_CREATED", "dispute": _public(d)}).encode()

    def fire():
        httpx.post(f"{SELF_URL}/api/webhooks/paytm-dispute", content=payload, timeout=20,
                   headers={"content-type": "application/json", "x-paytm-signature": sign(payload)})
    threading.Thread(target=fire, daemon=True).start()
    return d
