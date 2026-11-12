"""Simulated clock so the demo can fast-forward towards deadlines."""
from datetime import datetime, timedelta, timezone

from . import db

IST = timezone(timedelta(hours=5, minutes=30))


def offset_hours() -> float:
    return float(db.get_setting("clock_offset_hours", "0") or 0)


def now() -> datetime:
    return datetime.now(IST) + timedelta(hours=offset_hours())


def iso(dt: datetime) -> str:
    return dt.astimezone(IST).isoformat(timespec="seconds")


def now_iso() -> str:
    return iso(now())


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


def advance(hours: float):
    db.set_setting("clock_offset_hours", offset_hours() + hours)


def hours_until(deadline_iso: str) -> float:
    return round((parse(deadline_iso) - now()).total_seconds() / 3600, 2)


def add_working_days(start: datetime, days: int) -> datetime:
    """Deadlines count working days (Mon-Sat) as UPI dispute windows do (fix #6)."""
    d = start
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() != 6:  # skip Sunday
            added += 1
    return d
