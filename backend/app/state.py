"""Case stages. Fixed transitions make double-sending impossible (fix #4)."""
from . import clock, db

FINAL = {"WON", "LOST", "ACCEPTED", "EXPIRED"}

TRANSITIONS = {
    "NEW": {"COLLECTING"},
    "COLLECTING": {"AWAITING_MERCHANT", "READY", "AWAITING_APPROVAL", "ESCALATED"},
    "AWAITING_MERCHANT": {"COLLECTING", "ESCALATED"},
    "READY": {"AWAITING_APPROVAL", "SUBMITTED", "ESCALATED"},
    "AWAITING_APPROVAL": {"SUBMITTED", "ACCEPTED", "ESCALATED", "COLLECTING", "AWAITING_APPROVAL"},
    "ESCALATED": {"SUBMITTED", "ACCEPTED", "COLLECTING"},
    "SUBMITTED": {"WON", "LOST", "REOPENED"},
    "REOPENED": {"COLLECTING"},
}


class StageError(Exception):
    pass


def transition(case_id: str, to: str):
    c = db.q1("SELECT stage FROM cases WHERE id=?", (case_id,))
    cur = c["stage"]
    if cur == to and to != "AWAITING_APPROVAL":
        return cur
    allowed = TRANSITIONS.get(cur, set()) | ({"EXPIRED"} if cur not in FINAL else set())
    if to not in allowed:
        raise StageError(f"Case {case_id}: {cur} -> {to} is not allowed")
    fields = dict(stage=to, updated_at=clock.now_iso())
    if to == "READY":
        fields["ready_at"] = clock.now_iso()
    if to in FINAL:
        fields["closed_at"] = clock.now_iso()
    db.update("cases", "id", case_id, fields)
    return to
