"""DisputeSaathi agent: Notice -> Understand -> Investigate -> Decide -> Guard -> Act -> Follow up
-> Close & learn.

The AI agent drives each case: it chooses which data to fetch, checks the proof and proposes the
next action. Every proposal passes the policy guardrails (rules.guard) before this module executes
it (submit / accept via the Paytm Dispute API, messages, handovers). Accepting always needs the
merchant's OTP. Offline, or if the agent fails, the fixed rules (rules.decide) take the step.
"""
import json
import threading
import time

import httpx

from . import checklists, clock, db, events, llm, messaging, pdf_pack, rules, security, state, tools
from .checklists import REASONS
from .config import PAYTM_API_KEY, SELF_URL, STEP_DELAY

_locks: dict = {}
_running: set = set()


def _lock(case_id):
    return _locks.setdefault(case_id, threading.RLock())


def _pause():
    time.sleep(STEP_DELAY)


def get_case(case_id):
    return db.q1("SELECT * FROM cases WHERE id=?", (case_id,))


def get_merchant(mid):
    return db.q1("SELECT * FROM merchants WHERE id=?", (mid,))


def _money(v):
    return f"Rs {v:,.0f}"


# ===================================================================== 1. Notice
def open_case(dispute: dict) -> str:
    existing = db.q1("SELECT id FROM cases WHERE dispute_id=?", (dispute["dispute_id"],))
    if existing:
        return existing["id"]
    pay = tools.get_payment(dispute["txn_id"])
    case_id = "C" + dispute["dispute_id"][3:]
    now = clock.now_iso()
    db.insert("cases", dict(id=case_id, dispute_id=dispute["dispute_id"], merchant_id=dispute["merchant_id"],
                            txn_id=dispute["txn_id"], order_id=pay["order_id"] if pay else None, stage="NEW",
                            reason_paytm=dispute["reason_code"], amount=dispute["amount"],
                            deadline_at=dispute["deadline_at"], created_at=now, updated_at=now,
                            evidence={}, injection_flags=[], ai_mode=llm.mode()))
    hrs = clock.hours_until(dispute["deadline_at"])
    events.log(case_id, "system", f"Dispute alert received from Paytm: {dispute['dispute_id']}",
               {"reason_code": dispute["reason_code"], "amount": dispute["amount"],
                "customer_message": dispute["customer_message"]}, actor="paytm")
    events.log(case_id, "system", f"Case opened. Deadline timer started: {hrs:.0f} hours left",
               {"deadline_at": dispute["deadline_at"], "ai_mode": llm.mode()})
    start(case_id)
    return case_id


def start(case_id, fn=None, *args):
    def runner():
        if case_id in _running:
            return
        _running.add(case_id)
        try:
            with _lock(case_id):
                (fn or run_case)(case_id, *args)
        except Exception as e:  # noqa: BLE001
            events.log(case_id, "error", f"Agent error: {e}")
        finally:
            _running.discard(case_id)
    threading.Thread(target=runner, daemon=True).start()


def is_running(case_id):
    return case_id in _running


def run_case(case_id):
    state.transition(case_id, "COLLECTING")
    understand(case_id)
    drive(case_id, "new dispute")


# ===================================================================== 2. Understand
def understand(case_id):
    c = get_case(case_id)
    d = db.q1("SELECT * FROM paytm_disputes WHERE dispute_id=?", (c["dispute_id"],))
    flags = security.scan(d["customer_message"])
    if flags:
        _flag(case_id, "customer complaint", flags)
    _pause()
    res, used = None, "offline"
    if llm.available():
        try:
            res, used = llm.classify(d["customer_message"], d["reason_code"]), "llm"
        except Exception as e:  # noqa: BLE001
            events.log(case_id, "error", f"LLM unavailable, using rule-based reading ({e.__class__.__name__}: {str(e)[:150]})")
    if res is None:
        res = llm.classify_offline(d["customer_message"], d["reason_code"])
    mismatch = res["reason"] != d["reason_code"]
    db.update("cases", "id", case_id, dict(reason=res["reason"], reason_note=res["explanation"]))
    events.log(case_id, "think", f"Understood the complaint: {REASONS[res['reason']]}",
               {"explanation": res["explanation"], "paytm_reason_code": d["reason_code"],
                "matches_paytm_code": not mismatch, "read_by": llm.name() if used == "llm" else "keyword rules"})
    if mismatch:
        events.log(case_id, "security", "AI reading does not match Paytm's reason code",
                   {"ai_reason": res["reason"], "paytm_reason": d["reason_code"]})


# ===================================================================== 3. Collect proof
def _flag(case_id, source, hits):
    c = get_case(case_id)
    flags = c["injection_flags"] or []
    for h in hits:
        if not any(f["text"] == h for f in flags):
            flags.append(dict(source=source, text=h))
            events.log(case_id, "security", f"Hidden instruction found in {source} - ignored and recorded",
                       {"text": h, "effect": "None. Treated as data. The AI cannot pay anyone, accepting is now locked "
                                             "by guardrail G7, and any accept needs the merchant's OTP."})
    db.update("cases", "id", case_id, dict(injection_flags=flags))


def _scan_result(case_id, name, result):
    texts = []
    if name == "get_invoice" and result:
        texts.append(("bill " + result["invoice_id"], result.get("text", "")))
    if name == "get_chats":
        texts += [("customer chat", m["text"]) for m in result or []]
    for src, t in texts:
        hits = security.scan(t)
        if hits:
            _flag(case_id, src, hits)


def _record_tool(case_id, name, arg, result, system):
    c = get_case(case_id)
    ev = c["evidence"] or {}
    key = tools.TOOLS[name][2]
    ev[key] = result
    db.update("cases", "id", case_id, dict(evidence=ev))
    events.log(case_id, "tool", f"{name}({arg}) -> {system}", {"result": tools.summarize(name, result)})
    _scan_result(case_id, name, result)


_HIDE_FROM_LLM = {"customer_phone", "customer_vpa", "address", "merchant_id", "image_path"}


def _for_llm(result):
    """Data minimisation: the AI sees only what it needs (no customer phone, UPI id or address)."""
    if isinstance(result, list):
        return [_for_llm(r) for r in result]
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k not in _HIDE_FROM_LLM and v not in (None, "", [])}
    return result if result is not None else "nothing found"


def _allowed(case, result):
    """Tools may only return this merchant's own records."""
    rows = result if isinstance(result, list) else [result]
    return all((r or {}).get("merchant_id", case["merchant_id"]) == case["merchant_id"] for r in rows)


def collect_offline(case_id):
    """Rule-based plan: run the standard tools for this reason that have not been run yet."""
    c = get_case(case_id)
    ev = c["evidence"] or {}
    for name in tools.PLAN[c["reason"]]:
        if tools.TOOLS[name][2] in ev:
            continue
        _pause()
        arg = c["txn_id"] if tools.TOOLS[name][1] == "txn_id" else c["order_id"]
        key, system, result = tools.run(name, arg)
        _record_tool(case_id, name, arg, result, system)


# ===================================================================== 4-6. Check, decide
def build_facts(c, m, chk):
    limit = m["deadline_rule_limit"]
    return dict(reason=c["reason"], reason_mismatch=c["reason"] != c["reason_paytm"],
                hours_left=clock.hours_until(c["deadline_at"]), amount=c["amount"],
                proof_score=chk["proof_score"], missing_required=len(chk["missing_required"]),
                evidence_requested=bool(c["evidence_requested"]),
                duplicate_confirmed=chk["duplicate_confirmed"], invoice_mismatch=chk["invoice_mismatch"],
                merchant_reachable=bool(m["online"]), trust_level=m["trust_level"],
                auto_send_limit=m["auto_send_limit"], deadline_rule_enabled=bool(m["deadline_rule_enabled"]),
                deadline_rule_limit=limit,
                deadline_rule_applies=bool(m["deadline_rule_enabled"]) and limit is not None and c["amount"] <= limit,
                injection_found=bool(c["injection_flags"]))


def _situation(case_id):
    """Current checklist + facts, and the fixed-rule id as a cheap 'has anything changed?' signal."""
    c = get_case(case_id)
    m = get_merchant(c["merchant_id"])
    chk = checklists.evaluate(c["reason"], c["evidence"] or {})
    db.update("cases", "id", case_id, dict(checklist=chk))
    facts = build_facts(c, m, chk)
    return c, chk, facts, rules.decide(facts)


def _log_checklist(case_id, chk):
    found = sum(1 for i in chk["items"] if i["status"] in ("FOUND", "PARTIAL"))
    events.log(case_id, "check", f"Checklist: {found}/{len(chk['items'])} found, proof strength {chk['proof_score']:.2f}",
               {"items": [f"{i['label']}: {i['status']}" for i in chk["items"]],
                "missing_required": chk["missing_required"]})


def drive(case_id, trigger="new dispute"):
    """Wake the agent for this case. trigger says what happened (new dispute, photo, watcher...)."""
    c = get_case(case_id)
    if c["stage"] in state.FINAL or c["stage"] in ("SUBMITTED", "ESCALATED", "REOPENED"):
        return
    c, chk, facts, rule = _situation(case_id)
    prev = c["decision"] or {}
    if trigger == "watcher" and prev.get("situation") == rule["rule_id"]:
        return  # nothing changed since the last decision: no need to wake the agent
    if llm.available():
        try:
            if _agent_turn(case_id, trigger):
                return
            events.log(case_id, "error", "The agent did not settle on an allowed action - using the fixed rules for this step")
        except Exception as e:  # noqa: BLE001
            events.log(case_id, "error", f"Agent unavailable, using the fixed rules for this step "
                                         f"({e.__class__.__name__}: {str(e)[:150]})")
    _rules_turn(case_id, trigger)


def _agent_context(c, chk, trigger, collected):
    labels = [f"{i['key']} = {i['label']}{' (required)' if i['required'] else ''}" for i in chk["items"]]
    lines = [
        f"Dispute {c['dispute_id']} (case {c['id']}). Transaction id: {c['txn_id']}. Order id: {c['order_id']}. "
        f"Amount: Rs {c['amount']:.0f}. Reason (from the customer's words): {REASONS[c['reason']]}. "
        f"Paytm's reason code: {c['reason_paytm']}. Hours left before the deadline: {clock.hours_until(c['deadline_at']):.0f}.",
        f"What just happened: {trigger}. Case stage: {c['stage']}.",
        f"Proof checklist (key = item): {'; '.join(labels)}.",
    ]
    if collected:
        lines.append(f"Already fetched in earlier steps: {', '.join(collected)}. Do not fetch these again "
                     "unless something changed.")
    pend = c["pending_action"] or {}
    if pend:
        lines.append(f"Currently waiting for the merchant to approve: {pend.get('action')}.")
    if c["evidence_requested"]:
        lines.append("The merchant has already been asked for missing proof.")
    if c["outcome"] == "REOPENED":
        lines.append("The customer re-raised this dispute after our first reply. Prepare the second reply.")
    th = rules.load()["thresholds"]
    lines.append(f"Merchant playbook: a dispute of Rs {th['small_amount']} or less with weak proof (score below "
                 f"{th['weak_proof']}) usually costs the merchant more time than it is worth, so suggesting accept "
                 "(the merchant still approves with OTP) is usually best for them. Fight when the proof is strong. "
                 "Ask the merchant only for required proof that is missing. Hand to Paytm's team when a human "
                 "judgement is needed or a large amount is at risk.")
    lines.append("Your job: decide the best next step for this merchant. Fetch any proof you still need with the "
                 "data tools (call independent tools together in one turn), then call check_proof, then call "
                 "propose_action. If the policy engine blocks your proposal, read why and propose again.")
    return "\n".join(lines)


def _agent_turn(case_id, trigger):
    c = get_case(case_id)
    chk = c["checklist"] or checklists.evaluate(c["reason"], c["evidence"] or {})
    collected = sorted((c["evidence"] or {}).keys())
    events.log(case_id, "think", f"AI agent woke up: {trigger}",
               {"agent": llm.mode(), "already_have": collected or "nothing yet"})
    chosen = {}
    blocked = []

    def on_tool(name, inp):
        cur = get_case(case_id)
        if name == "check_proof":
            _pause()
            _, chk2, facts, _ = _situation(case_id)
            _log_checklist(case_id, chk2)
            return json.dumps(dict(items=[dict(key=i["key"], status=i["status"], required=i["required"], note=i["note"])
                                          for i in chk2["items"]],
                                   proof_score=chk2["proof_score"], missing_required=chk2["missing_required"],
                                   facts=rules._plain(facts)), default=str, separators=(",", ":")), False
        if name == "propose_action":
            return _on_proposal(case_id, inp, chosen, blocked)
        if name not in tools.TOOLS:
            return "Unknown tool.", False
        arg = next(iter(inp.values()), None)
        if not arg:
            return "Error: missing argument.", False
        _, _, key, system, _ = tools.TOOLS[name]
        result = tools.TOOLS[name][0](arg)
        if not _allowed(cur, result):
            events.log(case_id, "security", f"Blocked {name}({arg}): record belongs to another merchant")
            return "Not permitted: record does not belong to this merchant.", False
        _pause()
        _record_tool(case_id, name, arg, result, system)
        text = json.dumps(_for_llm(result), default=str, ensure_ascii=False, separators=(",", ":"))
        return (security.wrap_untrusted(system, text) if name in ("get_invoice", "get_chats") else text), False

    done, notes = llm.run_agent(_agent_context(c, chk, trigger, collected), on_tool)
    if notes:
        events.log(case_id, "think", "Agent notes", {"note": notes[:600]})
    if not done or not chosen:
        return False
    act(case_id, chosen["decision"])
    return True


MAX_BLOCKED = 3


def _complete_checks(case_id):
    """The guardrails must not depend on the agent's diligence: before any proposal is judged, run the
    standard checks for this reason that the agent skipped, so the facts are complete."""
    c = get_case(case_id)
    ev = c["evidence"] or {}
    skipped = [n for n in tools.PLAN[c["reason"]] if tools.TOOLS[n][2] not in ev]
    if not skipped:
        return
    events.log(case_id, "guard", f"Policy pre-check: running {len(skipped)} standard check(s) the agent skipped",
               {"checks": skipped}, actor="policy")
    collect_offline(case_id)
    _log_checklist(case_id, _situation(case_id)[1])


def _on_proposal(case_id, inp, chosen, blocked):
    action = str(inp.get("action", "")).upper()
    reasoning = str(inp.get("reasoning", "")).strip() or "(no reason given)"
    _complete_checks(case_id)
    c, chk, facts, rule = _situation(case_id)
    _pause()
    events.log(case_id, "decide", f"Agent proposes: {ACTION_LABEL.get(action, action)}",
               {"reasoning": reasoning, **({"asks_for": inp.get("items")} if action == "REQUEST_EVIDENCE" else {})},
               actor="ai-agent")
    g = rules.guard(action, facts)
    if not g["allowed"]:
        blocked.append(dict(action=action, guardrail=g["guardrail"], why=g["why"]))
        allowed = rules.allowed_actions(facts)
        events.log(case_id, "guard", f"Policy blocked {ACTION_LABEL.get(action, action)} - {g['guardrail']}",
                   {"why": g["why"], "allowed_now": [ACTION_LABEL[a] for a in allowed]}, actor="policy")
        if len(blocked) >= MAX_BLOCKED:
            return "Blocked again. Too many blocked proposals; the fixed rules will take this step.", True
        return (f"BLOCKED by guardrail {g['guardrail']}: {g['why']} Actions allowed right now: {', '.join(allowed)}. "
                "Propose again."), False
    decision = dict(action=action, why=reasoning, by="agent", agent=llm.mode(), situation=rule["rule_id"],
                    guardrails_checked=g["checked"], blocked=blocked[:], facts=rules._plain(facts))
    note = "No guardrail blocks it"
    if action == "FIGHT":
        fm = rules.fight_mode(facts)
        decision.update(action="FIGHT_AUTO" if fm["mode"] != "NEEDS_APPROVAL" else "FIGHT_NEEDS_APPROVAL",
                        mode=fm["mode"], rule_id=fm["rule_id"], mode_why=fm["why"])
        note += f"; send mode {fm['rule_id']}: {fm['why']}"
    if action == "REQUEST_EVIDENCE":
        wanted = [k for k in inp.get("items") or [] if k in chk["missing_required"]
                  or any(i["key"] == k and i["status"] in ("WEAK", "PARTIAL") for i in chk["items"])]
        decision["items"] = wanted or chk["missing_required"]
    events.log(case_id, "guard", f"Policy allowed {ACTION_LABEL[action]}",
               {"checked": g["checked"] or "no guardrail applies to this action", "note": note}, actor="policy")
    db.update("cases", "id", case_id, dict(decision=decision))
    chosen["decision"] = decision
    return "Allowed. The action is being carried out.", True


def _rules_turn(case_id, trigger):
    """Offline mode / fallback: fixed plan + first-match rules. Still passes the guardrails."""
    c = get_case(case_id)
    if trigger != "watcher":
        if trigger in ("new dispute", "customer re-raised the dispute"):
            labels = [lbl for _, lbl, _, _ in checklists.CHECKLISTS[c["reason"]]]
            events.log(case_id, "think", f"Collecting proof for '{REASONS[c['reason']]}'", {"checklist": labels})
        collect_offline(case_id)
    c, chk, facts, decision = _situation(case_id)
    prev = c["decision"] or {}
    if trigger == "watcher" and prev.get("situation") == decision["rule_id"]:
        return
    if trigger != "watcher":
        _pause()
        _log_checklist(case_id, chk)
    _pause()
    title = f"Rule {decision['rule_id']} -> {decision['action'].replace('_', ' ').title()}"
    if trigger == "watcher":
        title = f"Deadline watcher re-checked ({facts['hours_left']:.0f}h left): " + title
    events.log(case_id, "rule", title, {"why": decision["why"], "facts": decision["facts"],
                                        "rules_checked": decision["rules_checked"]}, actor="rule-engine")
    g = rules.guard(rules.RULE_TO_AGENT[decision["action"]], facts)
    if not g["allowed"]:  # a rules.yaml edit made the two parts disagree: take the safe path
        events.log(case_id, "guard", f"Policy blocked the rule's action - {g['guardrail']}", {"why": g["why"]},
                   actor="policy")
        decision.update(action="ESCALATE", why=f"{decision['why']} (Blocked by {g['guardrail']}: {g['why']})")
    decision.update(by="rules", situation=decision["rule_id"])
    db.update("cases", "id", case_id, dict(decision=decision))
    act(case_id, decision)


ACTION_LABEL = {"FIGHT": "Fight (send proof pack)", "SUGGEST_ACCEPT": "Suggest accepting",
                "REQUEST_EVIDENCE": "Ask merchant for missing proof", "WAIT_FOR_MERCHANT": "Wait for merchant",
                "ESCALATE": "Hand to Paytm's team"}


def evaluate_and_act(case_id, trigger="agent"):
    """Kept for callers outside this module (watcher, API)."""
    drive(case_id, "watcher" if trigger == "watcher" else trigger)


# ===================================================================== actions
def _move(case_id, to):
    try:
        state.transition(case_id, to)
    except state.StageError:
        state.transition(case_id, "COLLECTING")
        state.transition(case_id, to)


def act(case_id, decision):
    a = decision["action"]
    c = get_case(case_id)
    m = get_merchant(c["merchant_id"])
    hrs = clock.hours_until(c["deadline_at"])
    if a == "ESCALATE":
        escalate(case_id, decision["why"])
    elif a == "REQUEST_EVIDENCE":
        keys = decision.get("items") or (get_case(case_id)["checklist"] or {}).get("missing_required") or []
        asks = []
        for key in keys:
            asks.append({"invoice": f"a photo of the bill for order #{c['order_id']} ({_money(c['amount'])})",
                         "delivery": f"the delivery slip or courier receipt for order #{c['order_id']}",
                         "service_usage": f"attendance / session records for booking #{c['order_id']}",
                         "refund": f"the refund reference (RRN) or screenshot for order #{c['order_id']}",
                         }.get(key, key))
        text = (f"DisputeSaathi: A customer disputed {_money(c['amount'])} (case {case_id}). Everything else is ready. "
                f"Please send {' and '.join(asks)}. Time left: {hrs:.0f} hours. Reply here with a photo.")
        db.update("cases", "id", case_id, dict(evidence_requested=1))
        _move(case_id, "AWAITING_MERCHANT")
        messaging.notify(case_id, m, text)
    elif a == "WAIT_FOR_MERCHANT":
        pass
    elif a == "SUGGEST_ACCEPT":
        write_summary(case_id)
        db.update("cases", "id", case_id, dict(pending_action=dict(action="ACCEPT", why=decision["why"])))
        _move(case_id, "AWAITING_APPROVAL")
        messaging.notify(case_id, m, f"DisputeSaathi suggests ACCEPTING the {_money(c['amount'])} dispute (case {case_id}): "
                                     f"{decision['why']} Open Paytm app and confirm with OTP. Nothing happens without you.")
        messaging.new_otp(case_id, m, "ACCEPT")
    elif a == "FIGHT_NEEDS_APPROVAL":
        if c["stage"] == "AWAITING_APPROVAL" and (c["pending_action"] or {}).get("action") == "FIGHT":
            ensure_pack(case_id)
            return
        if c["stage"] == "AWAITING_MERCHANT":
            state.transition(case_id, "COLLECTING")
        ensure_pack(case_id)
        db.update("cases", "id", case_id, dict(pending_action=dict(action="FIGHT", why=decision["why"])))
        _move(case_id, "AWAITING_APPROVAL")
        reached = messaging.notify(case_id, m, f"Proof pack ready for the {_money(c['amount'])} dispute (case {case_id}). "
                                               f"Preview it and tap Fight in the Paytm app (OTP), or reply 1 to fight. "
                                               f"{hrs:.0f}h left.")
        messaging.new_otp(case_id, m, "FIGHT")
        if not reached:
            events.log(case_id, "system", "Waiting: merchant's deadline rule will apply when the deadline gets close")
    elif a == "FIGHT_AUTO":
        if c["stage"] == "AWAITING_MERCHANT":
            state.transition(case_id, "COLLECTING")
        ensure_pack(case_id)
        by = "DEADLINE_RULE" if decision.get("mode") == "DEADLINE_RULE" else "AUTO_SEND"
        submit(case_id, by)


def escalate(case_id, why):
    c = get_case(case_id)
    m = get_merchant(c["merchant_id"])
    ensure_pack(case_id)
    note = write_handover(case_id, why)
    state.transition(case_id, "ESCALATED")
    db.update("cases", "id", case_id, dict(pending_action=None))
    events.log(case_id, "action", "Handed over to Paytm's dispute team with a summary note", {"note": note})
    messaging.send(m["id"], case_id, "APP",
                   f"Case {case_id} ({_money(c['amount'])}) was handed to Paytm's dispute team: {why}",
                   status="DELIVERED" if m["online"] else "QUEUED")


# ===================================================================== writing
def _context(c, m):
    chk = c["checklist"] or checklists.evaluate(c["reason"], c["evidence"] or {})
    found = [f"{i['label']}: {i['note']}" for i in chk["items"] if i["status"] in ("FOUND", "PARTIAL")]
    other = [f"{i['label']}: {i['status']} - {i['note']}" for i in chk["items"] if i["status"] not in ("FOUND", "PARTIAL")]
    return chk, found, other


def write_summary(case_id):
    c = get_case(case_id)
    m = get_merchant(c["merchant_id"])
    chk, found, other = _context(c, m)
    d = db.q1("SELECT customer_message FROM paytm_disputes WHERE dispute_id=?", (c["dispute_id"],))
    second = c["pack_version"] >= 1 and c["stage"] in ("COLLECTING",) and c["outcome"] == "REOPENED"
    text = None
    if llm.available():
        try:
            text = llm.write(
                "Write a factual 4-6 sentence summary for Paytm's dispute reviewer defending this merchant. "
                "Reference proof as E1, E2... in checklist order. No exaggeration. Plain text.\n"
                f"Merchant: {m['business']}. Amount Rs {c['amount']:.0f}. Reason: {REASONS[c['reason']]}.\n"
                + security.wrap_untrusted("customer_complaint", d["customer_message"])
                + "\nChecklist (in order): " + "; ".join(f"E{n + 1} {i['label']} = {i['status']} ({i['note']})"
                                                         for n, i in enumerate(chk["items"]))
                + ("\nThis is a SECOND reply: the customer re-raised the dispute." if second else ""))
        except Exception as e:  # noqa: BLE001
            events.log(case_id, "error", f"LLM summary failed, using template ({e.__class__.__name__}: {str(e)[:150]})")
    if not text:
        refs = "; ".join(f"E{n + 1} {i['label'].lower()} ({i['note']})" for n, i in enumerate(chk["items"])
                         if i["status"] in ("FOUND", "PARTIAL"))
        text = (f"The customer raised a '{REASONS[c['reason']]}' dispute for INR {c['amount']:,.0f} on transaction "
                f"{c['txn_id']} (order {c['order_id']}). {m['business']} responds with the following proof: {refs}. "
                + (f"Not available: {'; '.join(other)}. " if other else "")
                + ("The customer re-raised this dispute; this second reply restates the full evidence. " if second else "")
                + "We request that the dispute be closed in the merchant's favour.")
    db.update("cases", "id", case_id, dict(summary=text))
    events.log(case_id, "think", "Wrote the dispute summary", {"summary": text})
    return text


def write_handover(case_id, why):
    c = get_case(case_id)
    m = get_merchant(c["merchant_id"])
    chk, found, other = _context(c, m)
    hrs = clock.hours_until(c["deadline_at"])
    suggestion = {"UNAUTHORIZED": "Check device / login history for fraud; delivery proof is attached if present.",
                  }.get(c["reason"], "Review the attached pack; fight if the proof below is acceptable to you.")
    if c["reason"] != c["reason_paytm"]:
        suggestion = (f"Confirm the real reason. Customer text reads like '{REASONS[c['reason']]}' but Paytm code is "
                      f"'{REASONS.get(c['reason_paytm'], c['reason_paytm'])}'. Pack was prepared for the customer's text.")
    elif chk.get("invoice_mismatch"):
        suggestion = "Bill amount is lower than the charge; a partial refund of the difference is likely fair."
    elif chk["missing_required"]:
        suggestion = "Required proof could not be collected in time; contact merchant by phone or decide on what exists."
    note = None
    if llm.available():
        try:
            note = llm.write(
                "Write a handover note for a Paytm dispute agent who must act in under a minute. Use exactly these "
                "short labelled lines: Dispute, Reason, Proof found, Proof missing, AI suggests, Why, Time left.\n"
                f"Dispute {c['dispute_id']} / case {case_id}, merchant {m['business']}, Rs {c['amount']:.0f}. "
                f"AI-read reason: {REASONS[c['reason']]}; Paytm code: {c['reason_paytm']}. Escalation rule: {why}. "
                f"Found: {'; '.join(found) or 'none'}. Missing/weak: {'; '.join(other) or 'none'}. "
                f"Suggested direction: {suggestion}. Time left: {hrs:.0f} hours. Plain text, no brackets or lists.",
                max_tokens=2000)
        except Exception as e:  # noqa: BLE001
            events.log(case_id, "error", f"LLM note failed, using template ({e.__class__.__name__}: {str(e)[:150]})")
    if not note:
        note = "\n".join([
            f"Dispute: {c['dispute_id']} (case {case_id}) - {m['business']} - INR {c['amount']:,.0f}",
            f"Reason: {REASONS[c['reason']]} (Paytm code: {c['reason_paytm']})",
            f"Proof found: {'; '.join(found) or 'none'}",
            f"Proof missing: {'; '.join(other) or 'none'}",
            f"AI suggests: {suggestion}",
            f"Why handed over: {why}",
            f"Time left: {hrs:.0f} hours",
        ])
    db.update("cases", "id", case_id, dict(handover_note=note))
    return note


# ===================================================================== 7. Pack
def ensure_pack(case_id):
    c = get_case(case_id)
    if c["pack_path"] and c["summary"]:
        return c["pack_path"]
    if not c["summary"]:
        write_summary(case_id)
        c = get_case(case_id)
    m = get_merchant(c["merchant_id"])
    ev = c["evidence"] or {}
    tl = []
    if ev.get("payment"):
        tl.append((ev["payment"]["created_at"], f"Customer paid INR {ev['payment']['amount']:,.0f} via Paytm UPI"))
    if ev.get("order"):
        tl.append((ev["order"]["created_at"], f"Order {ev['order']['order_id']} created"))
    if ev.get("invoice"):
        tl.append((ev["invoice"]["issued_at"], f"Invoice {ev['invoice']['invoice_id']} issued"))
    for e in (ev.get("delivery") or {}).get("events") or []:
        tl.append((e["ts"], f"Courier: {e['status']} {e.get('note', '')}"))
    for r in ev.get("refunds") or []:
        tl.append((r["processed_at"], f"Refund INR {r['amount']:,.0f} processed, RRN {r['rrn']}"))
    for lg in ev.get("service_logs") or []:
        tl.append((lg["ts"], lg["detail"]))
    d = db.q1("SELECT raised_at FROM paytm_disputes WHERE dispute_id=?", (c["dispute_id"],))
    tl.append((d["raised_at"], "Customer raised dispute"))
    tl.append((clock.now_iso(), "DisputeSaathi prepared this response"))
    tl.sort(key=lambda t: t[0])
    version = (c["pack_version"] or 0) + 1
    path = pdf_pack.build(c, m, ev, c["checklist"] or checklists.evaluate(c["reason"], ev), tl, version)
    db.update("cases", "id", case_id, dict(pack_path=path, pack_version=version))
    if c["stage"] == "COLLECTING":
        state.transition(case_id, "READY")
    _pause()
    events.log(case_id, "action", f"Proof pack PDF created (version {version})", {"file": path.split("\\")[-1].split("/")[-1]})
    return path


# ===================================================================== 8. Send (Paytm Dispute API)
def _paytm(path, body, idem):
    return httpx.post(f"{SELF_URL}/mock-paytm/v1/disputes/{path}", json=body, timeout=20,
                      headers={"x-api-key": PAYTM_API_KEY, "Idempotency-Key": idem})


def submit(case_id, by):
    c = get_case(case_id)
    if c["stage"] == "COLLECTING":
        state.transition(case_id, "READY")
        c = get_case(case_id)
    if c["stage"] not in ("READY", "AWAITING_APPROVAL", "ESCALATED"):
        events.log(case_id, "security", f"Submit blocked: case is in stage {c['stage']}")
        return False
    chk = c["checklist"]
    body = dict(action="DEFEND", summary=c["summary"], documents=[c["pack_path"].replace("\\", "/").split("/")[-1]],
                evidence_index=[f"E{n + 1} {i['label']}: {i['status']}" for n, i in enumerate(chk["items"])],
                submitted_by=by, pack_version=c["pack_version"])
    r = _paytm(f"{c['dispute_id']}/defend", body, f"{case_id}-v{c['pack_version']}")
    if r.status_code == 409:
        events.log(case_id, "security", "Paytm API: already submitted - duplicate send prevented", r.json())
        return False
    r.raise_for_status()
    precheck = dict(score=chk["proof_score"],
                    weak=[i["label"] for i in chk["items"] if i["status"] not in ("FOUND",)],
                    verdict="Strong - approve" if chk["proof_score"] >= 0.8 else "Check flagged items")
    state.transition(case_id, "SUBMITTED")
    db.update("cases", "id", case_id, dict(resolved_by=by, pending_action=None, precheck=precheck))
    label = {"AUTO_SEND": "auto-send (merchant's trust level)", "DEADLINE_RULE": "merchant's pre-set deadline rule",
             "MERCHANT_OTP": "merchant approval with OTP", "MERCHANT_SMS": "merchant SMS reply '1'",
             "PAYTM_OPS": "Paytm dispute team"}.get(by, by)
    events.log(case_id, "action", f"Defence submitted to Paytm Dispute API - by {label}",
               {"paytm_response": r.json(), "idempotency_key": f"{case_id}-v{c['pack_version']}"})
    m = get_merchant(c["merchant_id"])
    messaging.send(m["id"], case_id, "APP" if m["online"] else "SMS",
                   f"Done: defence for case {case_id} ({_money(c['amount'])}) sent to Paytm ({label}). "
                   "We will tell you the result.", status="DELIVERED" if m["online"] else "QUEUED")
    return True


def accept(case_id, by):
    c = get_case(case_id)
    r = _paytm(f"{c['dispute_id']}/accept", dict(action="ACCEPT", submitted_by=by), f"{case_id}-accept")
    if r.status_code == 409:
        events.log(case_id, "security", "Paytm API: already actioned - duplicate prevented", r.json())
        return False
    r.raise_for_status()
    state.transition(case_id, "ACCEPTED")
    db.update("cases", "id", case_id, dict(resolved_by=by, pending_action=None, outcome="ACCEPTED"))
    events.log(case_id, "action", "Dispute accepted via Paytm Dispute API (money returns to the original payer only)",
               {"paytm_response": r.json(), "by": by})
    return True


# ===================================================================== human actions
def approve(case_id, otp):
    c = get_case(case_id)
    pend = c["pending_action"] or {}
    if c["stage"] != "AWAITING_APPROVAL" or not pend:
        return False, "Nothing is waiting for approval"
    ap = db.q1("SELECT * FROM approvals WHERE case_id=? AND action=? AND used=0 ORDER BY id DESC LIMIT 1",
               (case_id, pend["action"]))
    if not ap or ap["otp"] != otp.strip():
        events.log(case_id, "security", "Wrong OTP entered - action blocked", actor="merchant")
        return False, "Wrong OTP"
    db.update("approvals", "id", ap["id"], dict(used=1))
    db.update("cases", "id", case_id, dict(merchant_taps=(c["merchant_taps"] or 0) + 1))
    events.log(case_id, "human", f"Merchant approved {pend['action']} with OTP in Paytm app", actor="merchant")
    ok = submit(case_id, "MERCHANT_OTP") if pend["action"] == "FIGHT" else accept(case_id, "MERCHANT_OTP")
    return ok, "Done"


def decline(case_id):
    c = get_case(case_id)
    pend = c["pending_action"] or {}
    if c["stage"] != "AWAITING_APPROVAL":
        return False
    db.update("cases", "id", case_id, dict(merchant_taps=(c["merchant_taps"] or 0) + 1))
    events.log(case_id, "human", f"Merchant said NO to {pend.get('action')}", actor="merchant")
    why = ("Merchant does not want to accept and prefers to fight, but the rules found the proof weak."
           if pend.get("action") == "ACCEPT" else "Merchant chose not to send the prepared defence.")
    escalate(case_id, why)
    return True


def sms_reply(merchant_id, text):
    m = get_merchant(merchant_id)
    messaging.send(merchant_id, None, "SMS", text, direction="in")
    parts = text.strip().upper().split()
    t = parts[0] if parts else ""
    if len(parts) > 1:  # "1 C88017" targets a specific case
        c = db.q1("SELECT * FROM cases WHERE merchant_id=? AND id=? AND stage='AWAITING_APPROVAL'",
                  (merchant_id, parts[1]))
    else:
        c = db.q1("SELECT * FROM cases WHERE merchant_id=? AND stage='AWAITING_APPROVAL' ORDER BY updated_at DESC",
                  (merchant_id,))
    if not c:
        messaging.send(merchant_id, None, "SMS", "DisputeSaathi: no case is waiting for your reply.")
        return
    pend = (c["pending_action"] or {}).get("action")
    if t == "1" and pend == "FIGHT":
        db.update("cases", "id", c["id"], dict(merchant_taps=(c["merchant_taps"] or 0) + 1))
        events.log(c["id"], "human", "Merchant replied 1 (Fight) by SMS - allowed because fighting moves no money",
                   actor="merchant")
        submit(c["id"], "MERCHANT_SMS")
    elif t == "2" or pend == "ACCEPT":
        messaging.send(merchant_id, c["id"], "SMS",
                       "DisputeSaathi: for your safety, ACCEPT can only be approved with OTP inside the Paytm app.")
        events.log(c["id"], "security", "SMS tried to approve ACCEPT - refused (needs OTP in app)", actor="merchant")
    else:
        messaging.send(merchant_id, c["id"], "SMS", "DisputeSaathi: reply 1 to Fight. Accept needs the Paytm app.")


def receive_upload(merchant_id, path, case_id=None):
    """Merchant sent a photo on WhatsApp. Read it, check it, finish the pack."""
    from . import ocr
    messaging.send(merchant_id, case_id, "WHATSAPP", "[photo]", direction="in", media_path=path)
    c = get_case(case_id) if case_id else db.q1(
        "SELECT * FROM cases WHERE merchant_id=? AND stage='AWAITING_MERCHANT' ORDER BY updated_at DESC", (merchant_id,))
    if not c:
        messaging.send(merchant_id, None, "WHATSAPP", "Thanks! No case is waiting for a photo right now.")
        return None
    start(c["id"], _process_upload, path)
    return c["id"]


def _process_upload(case_id, path):
    from . import ocr
    c = get_case(case_id)
    events.log(case_id, "message", "Merchant sent a photo on WhatsApp", {"file": path.replace("\\", "/").split("/")[-1]},
               actor="merchant")
    _pause()
    out = ocr.read(path)
    events.log(case_id, "tool", f"read_bill_photo -> {out['method']}",
               {"invoice_number": out["invoice_number"], "order_id": out["order_id"], "total": out["total_amount"]})
    hits = security.scan(out.get("full_text", ""))
    if hits:
        _flag(case_id, "uploaded photo", hits)
    ok_order = not out["order_id"] or out["order_id"] == c["order_id"]
    ok_amount = abs((out["total_amount"] or 0) - c["amount"]) <= 1
    if not (ok_order and ok_amount):
        problem = (f"This bill shows Rs {out['total_amount']:.0f}" + (f" for {out['order_id']}" if out["order_id"] else "")
                   + f", but the disputed payment is Rs {c['amount']:.0f} for {c['order_id']}.")
        events.log(case_id, "check", "Photo does not match the disputed payment - asked again", {"problem": problem})
        messaging.send(c["merchant_id"], case_id, "WHATSAPP", f"DisputeSaathi: {problem} Please send the correct bill.")
        return
    inv = dict(invoice_id=out["invoice_number"] or f"PHOTO-{case_id}", order_id=c["order_id"],
               merchant_id=c["merchant_id"], amount=c["amount"], issued_at=clock.now_iso(),
               text=out.get("full_text", ""), image_path=path, source="MERCHANT_PHOTO")
    db.x("DELETE FROM invoices WHERE invoice_id=?", (inv["invoice_id"],))
    db.insert("invoices", inv)
    ev = c["evidence"] or {}
    ev["invoice"] = inv
    db.update("cases", "id", case_id, dict(evidence=ev, summary=None, pack_path=None))
    events.log(case_id, "check", "Bill photo matches order and amount - added as evidence")
    messaging.send(c["merchant_id"], case_id, "WHATSAPP", "DisputeSaathi: got it, the bill matches. Finishing your pack now.")
    state.transition(case_id, "COLLECTING")
    drive(case_id, "merchant sent a bill photo that matches the order and amount (now saved as the invoice)")


def ops_resolve(case_id, action):
    c = get_case(case_id)
    if c["stage"] not in ("ESCALATED", "AWAITING_APPROVAL"):
        return False
    events.log(case_id, "human", f"Paytm dispute team chose to {action.upper()}", actor="paytm-ops")
    if action == "fight":
        ensure_pack(case_id)
        return submit(case_id, "PAYTM_OPS")
    return accept(case_id, "PAYTM_OPS")


# ===================================================================== 9-10. Follow up, close & learn
def record_outcome(case_id, result):
    c = get_case(case_id)
    if c["stage"] != "SUBMITTED":
        return False
    m = get_merchant(c["merchant_id"])
    if result == "REOPENED":
        state.transition(case_id, "REOPENED")
        new_deadline = clock.iso(clock.add_working_days(clock.now(), 7))
        db.update("cases", "id", case_id, dict(outcome="REOPENED", deadline_at=new_deadline, summary=None,
                                               pack_path=None, evidence_requested=0))
        db.update("paytm_disputes", "dispute_id", c["dispute_id"], dict(status="PRE_ARB", deadline_at=new_deadline))
        events.log(case_id, "system", "Customer re-raised the dispute (pre-arbitration). Preparing a second reply.",
                   {"new_deadline": new_deadline}, actor="paytm")

        def second(cid):
            state.transition(cid, "COLLECTING")
            drive(cid, "customer re-raised the dispute")
        start(case_id, second)
        return True
    state.transition(case_id, result)
    db.update("cases", "id", case_id, dict(outcome=result))
    db.update("paytm_disputes", "dispute_id", c["dispute_id"], dict(status=result))
    events.log(case_id, "system", f"Paytm marked the dispute {result}", actor="paytm")
    if result == "WON":
        good = (m["good_cases"] or 0) + 1
        db.update("merchants", "id", m["id"], dict(good_cases=good))
        messaging.send(m["id"], case_id, "APP", f"You WON case {case_id}: {_money(c['amount'])} stays with you.")
        if m["trust_level"] == "ASK_FIRST" and good >= 5:
            messaging.send(m["id"], case_id, "APP", "DisputeSaathi has won 5 cases for you. You can now switch on "
                                                    "auto-send for small cases in Settings.")
    else:
        messaging.send(m["id"], case_id, "APP", f"Case {case_id} was lost. We recorded what was missing to improve.")
    weak = [i["label"] for i in (c["checklist"] or {}).get("items", []) if i["status"] != "FOUND"]
    events.log(case_id, "learn", f"Learning recorded for '{REASONS[c['reason']]}' cases",
               {"result": result, "proof_score": (c["checklist"] or {}).get("proof_score"), "weak_items": weak})
    return True


# ===================================================================== deadline watcher
def watcher_loop(stop_event):
    last_offset = None
    last_full = 0.0
    while not stop_event.is_set():
        try:
            off = clock.offset_hours()
            if off != last_offset or time.time() - last_full > 60:
                last_offset, last_full = off, time.time()
                for c in db.q("SELECT id, stage, deadline_at FROM cases WHERE stage IN "
                              "('AWAITING_MERCHANT','AWAITING_APPROVAL','READY','ESCALATED')"):
                    if is_running(c["id"]):
                        continue
                    if clock.hours_until(c["deadline_at"]) <= 0:
                        with _lock(c["id"]):
                            state.transition(c["id"], "EXPIRED")
                            events.log(c["id"], "error", "Deadline passed without a response - DEADLINE MISSED")
                        continue
                    if c["stage"] != "ESCALATED":
                        start(c["id"], drive, "watcher")
        except Exception as e:  # noqa: BLE001
            print("watcher error", e)
        stop_event.wait(2)
