"""Audit log: every step, rule and action is saved with time and reason."""
from datetime import datetime

from . import clock, db, security


def log(case_id, kind, title, detail=None, actor="agent"):
    db.insert("events", dict(case_id=case_id, ts=datetime.now().isoformat(timespec="seconds"),
                             sim_ts=clock.now_iso(), kind=kind, title=security.mask_str(title),
                             detail=security.mask(detail) if detail is not None else None, actor=actor))


def for_case(case_id, after=0):
    return db.q("SELECT * FROM events WHERE case_id=? AND id>? ORDER BY id", (case_id, after))
