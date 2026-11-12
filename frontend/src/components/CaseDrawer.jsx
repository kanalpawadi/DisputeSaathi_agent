import React, { useEffect, useRef, useState } from "react";
import { api, rupee, fmtTime, STAGE_LABEL } from "../api.js";

const ICON = {
  system: "⚙", think: "AI", tool: "⇄", check: "✓", rule: "§", action: "▶",
  message: "✉", security: "!", human: "☺", learn: "★", error: "×", decide: "◆", guard: "⛨",
};
const KIND_LABEL = {
  system: "System", think: "AI thinking", tool: "Tool call", check: "Checklist", rule: "Rule engine",
  action: "Action", message: "Message", security: "Security", human: "Human", learn: "Learning", error: "Alert",
  decide: "Agent decision", guard: "Policy guardrail",
};
const STEPS = ["Alert", "Collect proof", "Pack ready", "Decide / approve", "Sent", "Result"];
const STAGE_STEP = {
  NEW: 0, COLLECTING: 1, AWAITING_MERCHANT: 1, REOPENED: 1, READY: 2, AWAITING_APPROVAL: 3, ESCALATED: 3,
  SUBMITTED: 4, ACCEPTED: 5, WON: 5, LOST: 5, EXPIRED: 5,
};
const ACTION_TEXT = {
  ESCALATE: "Hand to Paytm's team", REQUEST_EVIDENCE: "Ask merchant for missing proof",
  WAIT_FOR_MERCHANT: "Wait for merchant", SUGGEST_ACCEPT: "Suggest accepting (merchant must say yes)",
  FIGHT_AUTO: "Fight automatically", FIGHT_NEEDS_APPROVAL: "Fight after merchant approves",
  FIGHT: "Fight (send proof pack)",
};

function Detail({ d }) {
  if (d == null) return null;
  if (typeof d !== "object") return <div className="ev-detail">{String(d)}</div>;
  const entries = Object.entries(d).filter(([k]) => k !== "facts" && k !== "rules_checked" && k !== "checked");
  if (!entries.length) return null;
  return (
    <div className="ev-detail">
      {entries.map(([k, v]) => (
        <div key={k}>
          <b>{k.replace(/_/g, " ")}:</b>{" "}
          {Array.isArray(v) ? (v.length ? v.join(" · ") : "none") : typeof v === "object" && v ? JSON.stringify(v) : String(v)}
        </div>
      ))}
      {d.rules_checked && (
        <div className="muted small">Rules checked in order: {d.rules_checked.join(" → ")}</div>
      )}
      {d.checked && (
        <div className="muted small">
          Guardrails checked: {Array.isArray(d.checked) ? d.checked.join(" · ") : d.checked}
        </div>
      )}
    </div>
  );
}

export function StagePill({ stage }) {
  return <span className={`pill st st-${stage}`}>{STAGE_LABEL[stage] || stage}</span>;
}

export function HoursLeft({ h, stage }) {
  if (["WON", "LOST", "ACCEPTED", "EXPIRED", "SUBMITTED"].includes(stage)) return <span className="muted">—</span>;
  const cls = h < 24 ? "err" : h < 48 ? "" : "muted";
  return <span className={cls} style={{ fontWeight: 600 }}>{h < 0 ? "overdue" : `${Math.floor(h)}h left`}</span>;
}

export default function CaseDrawer({ caseId, onClose }) {
  const [c, setC] = useState(null);
  const [evs, setEvs] = useState([]);
  const [msgs, setMsgs] = useState([]);
  const [otp, setOtp] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const lastId = useRef(0);
  const traceRef = useRef(null);

  useEffect(() => {
    lastId.current = 0;
    setEvs([]);
    let alive = true;
    let timer;
    const tick = async () => {
      try {
        const r = await api.caseDetail(caseId, lastId.current);
        if (!alive) return;
        setC(r.case);
        setMsgs(r.messages);
        if (r.events.length) {
          lastId.current = r.events[r.events.length - 1].id;
          setEvs((old) => [...old, ...r.events]);
        }
      } catch (e) {
        /* case may not exist yet right after raising - keep polling */
      }
      timer = setTimeout(tick, 900);
    };
    tick();
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      alive = false;
      clearTimeout(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [caseId]);

  useEffect(() => {
    if (traceRef.current) traceRef.current.scrollTop = traceRef.current.scrollHeight;
  }, [evs.length]);

  const run = async (fn) => {
    setBusy(true);
    setErr("");
    try {
      await fn();
      setOtp("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const step = c ? STAGE_STEP[c.stage] ?? 0 : 0;
  const chk = c?.checklist;
  const dec = c?.decision;
  const pend = c?.pending_action;

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer">
        <div className="drawer-head">
          <div className="row">
            <h2>Case {caseId}</h2>
            {c && <StagePill stage={c.stage} />}
            {c?.running && <span className="pill pill-ai">AI working…</span>}
            <span className="spacer" />
            {c?.has_pack && (
              <a className="btn btn-light sm" href={`/api/cases/${caseId}/pack`} target="_blank" rel="noreferrer">
                View proof pack (PDF v{c.pack_version})
              </a>
            )}
            <button className="close" onClick={onClose} aria-label="Close">×</button>
          </div>
          {c ? (
            <div className="kv">
              <div><span>Merchant</span><b>{c.merchant_name}</b></div>
              <div><span>Amount</span><b>{rupee(c.amount)}</b></div>
              <div><span>Paytm dispute</span><b>{c.dispute_id}</b></div>
              <div><span>Order / txn</span><b className="mono">{c.order_id} · {c.txn_id}</b></div>
              <div><span>Deadline</span><b>{fmtTime(c.deadline_at)} · <HoursLeft h={c.hours_left} stage={c.stage} /></b></div>
            </div>
          ) : (
            <div className="muted">Waiting for Paytm's alert to arrive…</div>
          )}
          <div className="stepper">
            {STEPS.map((s, i) => (
              <React.Fragment key={s}>
                {i > 0 && <div className={`step-line ${i <= step ? "done" : ""}`} />}
                <div className={`step ${i < step ? "done" : i === step ? "current" : ""}`}>
                  <div className="dot">{i < step ? "✓" : i + 1}</div>
                  {s}
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>

        <div className="drawer-body">
          <section className="card">
            <div className="row" style={{ marginBottom: 8 }}>
              <h3 style={{ margin: 0 }}>AI teammate — live steps</h3>
              <span className="muted small">{evs.length} steps recorded · every step is saved with time and reason</span>
            </div>
            <div className="trace" ref={traceRef}>
              {evs.map((e) => (
                <div key={e.id} className={`ev k-${e.kind}`}>
                  <div className="ev-icon" title={KIND_LABEL[e.kind]}>{ICON[e.kind] || "•"}</div>
                  <div>
                    <div className="ev-title">{e.title}</div>
                    <div className="ev-meta">
                      {KIND_LABEL[e.kind]} · {e.actor} · {fmtTime(e.sim_ts)}
                    </div>
                    <Detail d={e.detail} />
                  </div>
                </div>
              ))}
              {(c?.running || !c) && (
                <div className="typing"><i /><i /><i /></div>
              )}
            </div>
          </section>

          <div className="grid" style={{ alignContent: "start" }}>
            {c && (
              <section className="card">
                <h3>What the customer said</h3>
                <div className="complaint">"{c.customer_message}"</div>
                {c.reason && (
                  <div style={{ marginTop: 10 }}>
                    <div><b>AI reading:</b> {c.reason_label}</div>
                    <div className="muted small">{c.reason_note}</div>
                    <div className="small" style={{ marginTop: 4 }}>
                      Paytm reason code: <span className="mono">{c.reason_paytm}</span>{" "}
                      {c.reason !== c.reason_paytm ? <span className="pill st st-LOST">does not match</span> : <span className="pill st st-WON">matches</span>}
                    </div>
                  </div>
                )}
              </section>
            )}

            {pend && c.stage === "AWAITING_APPROVAL" && (
              <section className="card approve-box">
                <h3>Merchant approval needed: {pend.action === "ACCEPT" ? "ACCEPT the dispute" : "FIGHT (send proof pack)"}</h3>
                <div className="small" style={{ marginBottom: 8 }}>{pend.why}</div>
                <div className="small muted" style={{ marginBottom: 8 }}>
                  OTP was sent by SMS to the merchant's phone (see <b>Merchant app</b> tab). Approval happens only inside the Paytm app.
                </div>
                <div className="row">
                  <input className="otp-input" maxLength={6} value={otp} placeholder="••••••"
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} />
                  <button className={pend.action === "ACCEPT" ? "btn btn-red" : "btn btn-green"} disabled={busy || otp.length !== 6}
                    onClick={() => run(() => api.approve(caseId, otp))}>
                    {pend.action === "ACCEPT" ? "Yes, accept" : "Yes, fight"}
                  </button>
                  <button className="btn btn-light" disabled={busy} onClick={() => run(() => api.decline(caseId))}>No</button>
                  <button className="btn btn-light sm" disabled={busy} onClick={() => run(() => api.resendOtp(caseId))}>Resend OTP</button>
                </div>
                {err && <div className="err" style={{ marginTop: 6 }}>{err}</div>}
              </section>
            )}

            {c?.stage === "ESCALATED" && (
              <section className="card handover">
                <h3>Handover note for Paytm's team</h3>
                <div className="note">{c.handover_note}</div>
                <div className="row" style={{ marginTop: 10 }}>
                  <span className="small muted">Paytm team decides:</span>
                  <button className="btn btn-green sm" disabled={busy} onClick={() => run(() => api.opsResolve(caseId, "fight"))}>Fight with this pack</button>
                  <button className="btn btn-red sm" disabled={busy} onClick={() => run(() => api.opsResolve(caseId, "accept"))}>Accept</button>
                </div>
                {err && <div className="err">{err}</div>}
              </section>
            )}

            {c?.stage === "SUBMITTED" && (
              <section className="card">
                <h3>Sent to Paytm — waiting for the result</h3>
                {c.precheck && (
                  <div className="small" style={{ marginBottom: 8 }}>
                    AI pre-check for Paytm's reviewer: <b>{c.precheck.verdict}</b> (proof {c.precheck.score.toFixed(2)})
                    {c.precheck.weak?.length ? <> · check: {c.precheck.weak.join(", ")}</> : null}
                  </div>
                )}
                <div className="row">
                  <span className="small muted">Simulate Paytm's decision:</span>
                  <button className="btn btn-green sm" disabled={busy} onClick={() => run(() => api.opsOutcome(caseId, "WON"))}>Won</button>
                  <button className="btn btn-red sm" disabled={busy} onClick={() => run(() => api.opsOutcome(caseId, "LOST"))}>Lost</button>
                  <button className="btn btn-light sm" disabled={busy} onClick={() => run(() => api.opsOutcome(caseId, "REOPENED"))}>Customer re-raised</button>
                </div>
                {err && <div className="err">{err}</div>}
              </section>
            )}

            {dec && (
              <section className="card decision">
                <div className="row" style={{ marginBottom: 6 }}>
                  <h3 style={{ margin: 0 }}>Decision</h3>
                  <span className={`rule-id ${dec.by === "agent" ? "by-agent" : ""}`}>
                    {dec.by === "agent" ? "AI agent" : dec.rule_id}
                  </span>
                  {dec.by === "agent" && dec.rule_id && <span className="rule-id">{dec.rule_id}</span>}
                </div>
                <div style={{ fontWeight: 700, color: "var(--navy)" }}>{ACTION_TEXT[dec.action] || dec.action}</div>
                <div className="small" style={{ marginTop: 4 }}>{dec.why}</div>
                {dec.mode_why && <div className="small muted" style={{ marginTop: 4 }}>Send mode: {dec.mode_why}</div>}
                {dec.blocked?.length > 0 && (
                  <div className="small" style={{ marginTop: 6 }}>
                    {dec.blocked.map((b, i) => (
                      <div key={i} className="blocked">
                        Blocked first: {ACTION_TEXT[b.action] || b.action} · <b>{b.guardrail}</b> — {b.why}
                      </div>
                    ))}
                  </div>
                )}
                <div className="small muted" style={{ marginTop: 6 }}>
                  {dec.by === "agent"
                    ? `Chosen by the AI agent (${dec.agent}) and checked against the policy guardrails in rules.yaml.`
                    : "Decided by the fixed rules (rules.yaml) because the AI was offline or failed. First matching rule wins."}
                </div>
                {c.resolved_by && <div className="small" style={{ marginTop: 6 }}>Resolved by: <b>{c.resolved_by.replace(/_/g, " ")}</b></div>}
              </section>
            )}

            {chk && (
              <section className="card">
                <div className="row">
                  <h3 style={{ margin: 0 }}>Proof checklist</h3>
                  <span className="spacer" />
                  <b>{Math.round(chk.proof_score * 100)}%</b>
                </div>
                <div className="meter"><div style={{ width: `${chk.proof_score * 100}%` }} /></div>
                <div style={{ marginTop: 8 }}>
                  {chk.items.map((it, i) => (
                    <div className="chk-row" key={it.key}>
                      <div className={`chk-icon c-${it.status}`}>
                        {it.status === "FOUND" ? "✓" : it.status === "MISSING" ? "–" : it.status === "CONTRADICTS" ? "✕" : "~"}
                      </div>
                      <div>
                        <div><b>E{i + 1}</b> {it.label} {it.required && <span className="tag">required</span>}</div>
                        <div className="muted small">{it.note}</div>
                      </div>
                      <span className={`pill c-${it.status}`}>{it.status}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {c?.injection_flags?.length > 0 && (
              <section className="card">
                <h3>Security: hidden instructions ignored</h3>
                {c.injection_flags.map((f, i) => (
                  <div className="flag" key={i}>
                    <b>{f.source}:</b> "{f.text}"
                  </div>
                ))}
                <div className="small muted" style={{ marginTop: 6 }}>
                  Treated as data only. The AI has no tool that can accept, refund or pay anyone.
                </div>
              </section>
            )}

            {c?.summary && (
              <section className="card">
                <h3>Summary written for Paytm's reviewer</h3>
                <div className="small">{c.summary}</div>
              </section>
            )}

            {msgs.length > 0 && (
              <section className="card">
                <h3>Messages for this case</h3>
                {msgs.map((m) => (
                  <div key={m.id} className="small" style={{ padding: "4px 0", borderBottom: "1px solid #eef2f7" }}>
                    <span className={`ch-${m.channel}`} style={{ fontWeight: 700 }}>{m.channel}</span>{" "}
                    <span className="muted">{m.direction === "in" ? "from merchant" : `· ${m.status}`}</span> — {m.text}
                  </div>
                ))}
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
