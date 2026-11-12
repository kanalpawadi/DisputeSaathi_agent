"""DisputeSaathi API (FastAPI)."""
import hmac
import json
import shutil
import threading
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, clock, db, events, llm, mock_paytm, seed
from .checklists import REASONS
from .config import DB_PATH, SAMPLE_DIR, UPLOAD_DIR

app = FastAPI(title="DisputeSaathi", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(mock_paytm.router)
app.mount("/files/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/files/samples", StaticFiles(directory=SAMPLE_DIR), name="samples")

_stop = threading.Event()


@app.on_event("startup")
def startup():
    fresh = not Path(DB_PATH).exists()
    db.init_db()
    if fresh or not db.q1("SELECT 1 AS x FROM merchants"):
        seed.seed()
    threading.Thread(target=agent.watcher_loop, args=(_stop,), daemon=True).start()


@app.on_event("shutdown")
def shutdown():
    _stop.set()


# ------------------------------------------------------------------ helpers
def _case_view(c):
    if not c:
        return None
    m = agent.get_merchant(c["merchant_id"])
    d = db.q1("SELECT customer_message, raised_at FROM paytm_disputes WHERE dispute_id=?", (c["dispute_id"],))
    c = dict(c)
    c.pop("evidence", None)
    c.update(merchant_name=m["business"], customer_message=d["customer_message"], raised_at=d["raised_at"],
             hours_left=clock.hours_until(c["deadline_at"]), reason_label=REASONS.get(c["reason"] or "", None),
             running=agent.is_running(c["id"]), has_pack=bool(c["pack_path"]))
    return c


# ------------------------------------------------------------------ system
@app.get("/api/health")
def health():
    return {"ok": True, "ai_mode": llm.mode(), "llm": llm.available(), "now": clock.now_iso(),
            "offset_hours": clock.offset_hours()}


@app.get("/api/clock")
def get_clock():
    return {"now": clock.now_iso(), "offset_hours": clock.offset_hours()}


class Advance(BaseModel):
    hours: float


@app.post("/api/clock/advance")
def advance(a: Advance):
    if a.hours <= 0 or a.hours > 24 * 10:
        raise HTTPException(400, "hours must be between 0 and 240")
    clock.advance(a.hours)
    return get_clock()


@app.post("/api/demo/reset")
def reset():
    if any(agent.is_running(c["id"]) for c in db.q("SELECT id FROM cases")):
        raise HTTPException(409, "An agent is still working; try again in a few seconds")
    seed.seed(reset=True)
    for f in UPLOAD_DIR.glob("*"):
        f.unlink(missing_ok=True)
    return {"ok": True}


# ------------------------------------------------------------------ scenarios (demo control)
@app.get("/api/scenarios")
def scenarios():
    rows = db.q("SELECT d.*, m.business FROM paytm_disputes d JOIN merchants m ON m.id=d.merchant_id "
                "WHERE d.scenario_order IS NOT NULL ORDER BY d.scenario_order")
    cases = {c["dispute_id"]: c for c in db.q("SELECT id, dispute_id, stage FROM cases")}
    out = []
    for r in rows:
        c = cases.get(r["dispute_id"])
        out.append(dict(dispute_id=r["dispute_id"], n=r["scenario_order"], title=r["scenario_title"],
                        expect=r["scenario_expect"], featured=bool(r["featured"]), merchant=r["business"],
                        merchant_id=r["merchant_id"], amount=r["amount"], reason_code=r["reason_code"],
                        message=r["customer_message"], case_id=c["id"] if c else None,
                        stage=c["stage"] if c else None))
    return out


@app.post("/api/scenarios/{dispute_id}/raise")
def raise_scenario(dispute_id: str):
    d = mock_paytm.raise_dispute(dispute_id)
    return {"dispute_id": dispute_id, "case_id": "C" + dispute_id[3:], "status": d["status"]}


# ------------------------------------------------------------------ webhook from Paytm
@app.post("/api/webhooks/paytm-dispute")
async def paytm_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("x-paytm-signature", "")
    if not hmac.compare_digest(sig, mock_paytm.sign(body)):
        raise HTTPException(401, "bad signature")
    payload = json.loads(body)
    case_id = agent.open_case(payload["dispute"])
    return {"received": True, "case_id": case_id}


# ------------------------------------------------------------------ cases
@app.get("/api/cases")
def list_cases(merchant_id: str | None = None, stage: str | None = None):
    sql, args = "SELECT * FROM cases WHERE 1=1", []
    if merchant_id:
        sql, args = sql + " AND merchant_id=?", [*args, merchant_id]
    if stage:
        sql, args = sql + " AND stage IN (%s)" % ",".join("?" * len(stage.split(","))), [*args, *stage.split(",")]
    return [_case_view(c) for c in db.q(sql + " ORDER BY created_at DESC", args)]


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, after: int = 0):
    c = agent.get_case(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    return dict(case=_case_view(c), events=events.for_case(case_id, after),
                messages=db.q("SELECT * FROM messages WHERE case_id=? ORDER BY id", (case_id,)))


@app.get("/api/cases/{case_id}/pack")
def case_pack(case_id: str):
    c = agent.get_case(case_id)
    if not c or not c["pack_path"] or not Path(c["pack_path"]).exists():
        raise HTTPException(404, "pack not ready")
    return FileResponse(c["pack_path"], media_type="application/pdf", filename=Path(c["pack_path"]).name,
                        content_disposition_type="inline")


class OtpIn(BaseModel):
    otp: str


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, body: OtpIn):
    ok, msg = agent.approve(case_id, body.otp)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True}


@app.post("/api/cases/{case_id}/decline")
def decline(case_id: str):
    if not agent.decline(case_id):
        raise HTTPException(400, "nothing to decline")
    return {"ok": True}


@app.post("/api/cases/{case_id}/resend-otp")
def resend_otp(case_id: str):
    c = agent.get_case(case_id)
    pend = (c or {}).get("pending_action")
    if not pend:
        raise HTTPException(400, "nothing waiting")
    from . import messaging
    messaging.new_otp(case_id, agent.get_merchant(c["merchant_id"]), pend["action"])
    return {"ok": True}


# ------------------------------------------------------------------ merchants
@app.get("/api/merchants")
def merchants():
    return db.q("SELECT * FROM merchants ORDER BY id")


class MerchantPatch(BaseModel):
    trust_level: str | None = None
    auto_send_limit: float | None = None
    deadline_rule_enabled: bool | None = None
    deadline_rule_limit: float | None = None
    online: bool | None = None


@app.patch("/api/merchants/{mid}")
def patch_merchant(mid: str, p: MerchantPatch):
    fields = {k: (int(v) if isinstance(v, bool) else v) for k, v in p.model_dump().items() if v is not None}
    if "trust_level" in fields and fields["trust_level"] not in ("AUTO", "ASK_FIRST"):
        raise HTTPException(400, "bad trust level")
    db.update("merchants", "id", mid, fields)
    if fields.get("online") == 1:  # back online: queued SMS delivered, short note of what happened
        db.x("UPDATE messages SET status='DELIVERED' WHERE merchant_id=? AND status='QUEUED'", (mid,))
        from . import messaging
        done = db.q("SELECT id, stage FROM cases WHERE merchant_id=? AND resolved_by='DEADLINE_RULE'", (mid,))
        if done:
            messaging.send(mid, None, "APP", "Welcome back. While you were offline DisputeSaathi sent "
                                             f"{len(done)} defence(s) under your deadline rule: "
                                             + ", ".join(f"{d['id']} ({d['stage']})" for d in done))
    return db.q1("SELECT * FROM merchants WHERE id=?", (mid,))


@app.get("/api/merchants/{mid}/messages")
def merchant_messages(mid: str):
    return db.q("SELECT * FROM messages WHERE merchant_id=? ORDER BY id", (mid,))


class Reply(BaseModel):
    text: str


@app.post("/api/merchants/{mid}/reply")
def merchant_reply(mid: str, r: Reply):
    agent.sms_reply(mid, r.text)
    return {"ok": True}


@app.post("/api/merchants/{mid}/upload")
def merchant_upload(mid: str, file: UploadFile = File(...), case_id: str | None = Form(None)):
    ext = Path(file.filename or "photo.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(400, "send a photo (png/jpg/webp)")
    dest = UPLOAD_DIR / f"{mid}_{uuid.uuid4().hex[:8]}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"case_id": agent.receive_upload(mid, str(dest), case_id)}


@app.get("/api/merchants/{mid}/samples")
def merchant_samples(mid: str):
    return [dict(order_id=s["order_id"], url=f"/files/samples/{Path(s['path']).name}")
            for s in db.q("SELECT * FROM sample_bills WHERE merchant_id=?", (mid,))]


class SampleIn(BaseModel):
    order_id: str


@app.post("/api/merchants/{mid}/send-sample")
def send_sample(mid: str, s: SampleIn):
    row = db.q1("SELECT * FROM sample_bills WHERE merchant_id=? AND order_id=?", (mid, s.order_id))
    if not row:
        raise HTTPException(404, "no sample")
    dest = UPLOAD_DIR / f"{mid}_{uuid.uuid4().hex[:8]}.png"
    shutil.copy(row["path"], dest)
    return {"case_id": agent.receive_upload(mid, str(dest))}


# ------------------------------------------------------------------ Paytm ops
@app.get("/api/ops/queue")
def ops_queue():
    return dict(escalated=[_case_view(c) for c in db.q("SELECT * FROM cases WHERE stage='ESCALATED' ORDER BY deadline_at")],
                submitted=[_case_view(c) for c in db.q("SELECT * FROM cases WHERE stage='SUBMITTED' ORDER BY updated_at DESC")],
                closed=[_case_view(c) for c in db.q("SELECT * FROM cases WHERE stage IN ('WON','LOST','ACCEPTED','EXPIRED') "
                                                    "ORDER BY closed_at DESC")])


class Resolve(BaseModel):
    action: str


@app.post("/api/ops/cases/{case_id}/resolve")
def ops_resolve(case_id: str, r: Resolve):
    if r.action not in ("fight", "accept"):
        raise HTTPException(400, "action must be fight or accept")
    if not agent.ops_resolve(case_id, r.action):
        raise HTTPException(400, "case is not waiting for the Paytm team")
    return {"ok": True}


class Outcome(BaseModel):
    result: str


@app.post("/api/ops/cases/{case_id}/outcome")
def ops_outcome(case_id: str, o: Outcome):
    if o.result not in ("WON", "LOST", "REOPENED"):
        raise HTTPException(400, "result must be WON, LOST or REOPENED")
    if not agent.record_outcome(case_id, o.result):
        raise HTTPException(400, "case is not submitted")
    return {"ok": True}


# ------------------------------------------------------------------ metrics
@app.get("/api/metrics")
def metrics():
    cases = db.q("SELECT * FROM cases")
    stages = Counter(c["stage"] for c in cases)
    by = Counter(c["resolved_by"] for c in cases if c["resolved_by"])
    won = [c for c in cases if c["stage"] == "WON"]
    lost = [c for c in cases if c["stage"] == "LOST"]
    ready_secs = []
    for c in cases:
        first = db.q1("SELECT ts FROM events WHERE case_id=? ORDER BY id LIMIT 1", (c["id"],))
        done = db.q1("SELECT ts FROM events WHERE case_id=? AND (kind='rule' OR (kind='guard' AND title LIKE 'Policy allowed%')) ORDER BY id LIMIT 1", (c["id"],))
        if first and done:
            ready_secs.append((datetime.fromisoformat(done["ts"]) - datetime.fromisoformat(first["ts"])).total_seconds())
    learning = {}
    for c in cases:
        if c["stage"] in ("WON", "LOST") and c["reason"]:
            l = learning.setdefault(REASONS[c["reason"]], {"won": 0, "lost": 0})
            l["won" if c["stage"] == "WON" else "lost"] += 1
    flags = sum(len(c["injection_flags"] or []) for c in cases)
    taps = sum(c["merchant_taps"] or 0 for c in cases)
    return dict(
        total=len(cases), stages=stages, resolved_by=by,
        deadlines_missed=stages.get("EXPIRED", 0),
        no_human=by.get("AUTO_SEND", 0) + by.get("DEADLINE_RULE", 0),
        one_tap=by.get("MERCHANT_OTP", 0) + by.get("MERCHANT_SMS", 0),
        to_paytm_team=stages.get("ESCALATED", 0) + by.get("PAYTM_OPS", 0),
        accepted=stages.get("ACCEPTED", 0), won=len(won), lost=len(lost),
        win_rate=round(len(won) / (len(won) + len(lost)), 2) if (won or lost) else None,
        money_protected=sum(c["amount"] for c in won),
        money_defended=sum(c["amount"] for c in cases if c["stage"] in ("SUBMITTED", "WON", "LOST")),
        avg_seconds_to_decision=round(sum(ready_secs) / len(ready_secs), 1) if ready_secs else None,
        merchant_taps=taps, merchant_minutes=taps * 1, baseline_minutes=len(cases) * 120,
        injections_blocked=flags, learning=learning)
