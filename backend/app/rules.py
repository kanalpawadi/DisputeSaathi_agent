"""Deterministic policy engine (rules.yaml).

- guard(action, facts): checks an action the AI agent proposed against the guardrails.
- fight_mode(facts): decides whether an allowed FIGHT is sent automatically or needs the merchant's OTP.
- decide(facts): the fixed first-match rule list, used offline and as the fallback when the agent fails.
"""
import yaml

from .config import RULES_FILE

OPS = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "lt": lambda a, b: a is not None and a < b, "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b, "gte": lambda a, b: a is not None and a >= b,
    "is_true": lambda a, b: bool(a) is True, "is_false": lambda a, b: bool(a) is False,
}

AGENT_ACTIONS = ["FIGHT", "SUGGEST_ACCEPT", "REQUEST_EVIDENCE", "WAIT_FOR_MERCHANT", "ESCALATE"]


def load():
    with open(RULES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve(value, facts, thresholds):
    if isinstance(value, str) and value.startswith("$"):
        return thresholds[value[1:]]
    if isinstance(value, str) and value.startswith("@"):
        return facts.get(value[1:])
    return value


def _matches(conds, facts, th):
    for cond in conds:
        op = next(k for k in cond if k != "fact")
        if not OPS[op](facts.get(cond["fact"]), _resolve(cond[op], facts, th)):
            return False
    return True


def _plain(facts):
    return {k: v for k, v in facts.items() if not isinstance(v, (dict, list))}


def guard(action: str, facts: dict) -> dict:
    """Is the agent allowed to take this action now? Returns allowed, the blocking guardrail and why."""
    cfg = load()
    th = cfg["thresholds"]
    if action not in AGENT_ACTIONS:
        return dict(allowed=False, guardrail="UNKNOWN_ACTION", why=f"'{action}' is not an action the agent can take.",
                    checked=[])
    checked = []
    for g in cfg.get("guardrails", []):
        if action not in g["block"]:
            continue
        checked.append(g["id"])
        if _matches(g["when"], facts, th):
            return dict(allowed=False, guardrail=g["id"], why=g["why"], checked=checked)
    return dict(allowed=True, guardrail=None, why="No guardrail blocks this action.", checked=checked)


def allowed_actions(facts: dict) -> list:
    return [a for a in AGENT_ACTIONS if guard(a, facts)["allowed"]]


def fight_mode(facts: dict) -> dict:
    cfg = load()
    th = cfg["thresholds"]
    for m in cfg["fight_modes"]:
        if _matches(m["when"], facts, th):
            return dict(rule_id=m["id"], mode=m["mode"], why=m["why"])
    raise RuntimeError("fight_modes in rules.yaml must end with a catch-all")


def decide(facts: dict) -> dict:
    cfg = load()
    th = cfg["thresholds"]
    checked = []
    for rule in cfg["rules"]:
        checked.append(rule["id"])
        if _matches(rule["when"], facts, th):
            return dict(rule_id=rule["id"], action=rule["action"], mode=rule.get("mode"),
                        why=rule["why"], rules_checked=checked, thresholds=th, facts=_plain(facts))
    raise RuntimeError("No rule matched; rules.yaml must end with a catch-all rule")


# Offline rule actions expressed as agent actions (so the same executor runs both).
RULE_TO_AGENT = {"ESCALATE": "ESCALATE", "REQUEST_EVIDENCE": "REQUEST_EVIDENCE", "WAIT_FOR_MERCHANT": "WAIT_FOR_MERCHANT",
                 "SUGGEST_ACCEPT": "SUGGEST_ACCEPT", "FIGHT_AUTO": "FIGHT", "FIGHT_NEEDS_APPROVAL": "FIGHT"}
