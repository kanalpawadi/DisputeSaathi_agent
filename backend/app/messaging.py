"""Merchant messaging (mock WhatsApp / SMS / app push / voice) with a fallback ladder
for weak networks. WhatsApp and SMS carry alerts and photos; they can confirm FIGHT
(a safe, no-money action) but ACCEPT always needs the in-app OTP (fix #3)."""
import random

from . import clock, db, events


def send(merchant_id, case_id, channel, text, direction="out", media_path=None, status="DELIVERED"):
    return db.insert("messages", dict(merchant_id=merchant_id, case_id=case_id, channel=channel,
                                      direction=direction, text=text, media_path=media_path,
                                      status=status, ts=clock.now_iso()))


def notify(case_id, merchant, text):
    """Try app -> WhatsApp -> SMS -> voice. Returns True if the merchant was reached."""
    mid = merchant["id"]
    if merchant["online"]:
        send(mid, case_id, "APP", text)
        send(mid, case_id, "WHATSAPP", text)
        events.log(case_id, "message", "Merchant notified on Paytm app + WhatsApp", {"text": text})
        return True
    ladder = [("APP", "FAILED", "App push failed - phone has no data connection"),
              ("WHATSAPP", "NOT_DELIVERED", "WhatsApp not delivered (single tick)"),
              ("SMS", "QUEUED", "SMS queued on 2G: reply 1 = Fight. (Accept needs OTP in app)"),
              ("VOICE", "NO_ANSWER", f"Automatic {merchant['language']} voice call: no answer")]
    for ch, st, note in ladder:
        send(mid, case_id, ch, text if ch != "VOICE" else f"[IVR call] {text}", status=st)
        events.log(case_id, "message", f"{ch}: {note}", None)
    events.log(case_id, "system", "Merchant unreachable on all channels",
               {"next": "Deadline watcher will apply the merchant's pre-set rule, or hand to Paytm's team"})
    return False


def new_otp(case_id, merchant, action):
    otp = f"{random.randint(0, 999999):06d}"
    db.insert("approvals", dict(case_id=case_id, action=action, otp=otp, created_at=clock.now_iso(), used=0))
    send(merchant["id"], case_id, "SMS",
         f"PAYTM: {otp} is your OTP to {action} dispute case {case_id} in the Paytm app. Never share it.",
         status="DELIVERED" if merchant["online"] else "QUEUED")
    return otp
