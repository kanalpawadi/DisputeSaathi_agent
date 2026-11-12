import React, { useState } from "react";
import { api, rupee } from "../api.js";
import { usePoll } from "../hooks.js";
import { StagePill } from "../components/CaseDrawer.jsx";

const REASON = {
  ITEM_NOT_RECEIVED: "Item not received", SERVICE_NOT_PROVIDED: "Service not given",
  DUPLICATE_PAYMENT: "Charged twice", INCORRECT_AMOUNT: "Wrong amount",
  REFUND_NOT_PROCESSED: "Refund not received", UNAUTHORIZED: "Didn't make payment",
};

export default function DemoView({ onOpen }) {
  const [list, err, refresh] = usePoll(api.scenarios, 2500);
  const [onlyFeatured, setOnlyFeatured] = useState(false);
  const [raising, setRaising] = useState(null);

  const raise = async (s) => {
    setRaising(s.dispute_id);
    try {
      const r = await api.raise(s.dispute_id);
      onOpen(r.case_id);
      refresh();
    } catch (e) {
      alert(e.message);
    } finally {
      setRaising(null);
    }
  };

  const shown = (list || []).filter((s) => !onlyFeatured || s.featured);

  return (
    <div>
      <div className="hero">
        <div className="card">
          <h1>Pick any dispute. Watch the AI teammate handle it.</h1>
          <div className="muted">
            Each card is a real-looking dispute on a fake Paytm system. Raising it sends a signed webhook to
            DisputeSaathi. An AI agent investigates the case, pulls proof from the merchant's systems and decides
            the next step: fight, accept, ask for proof, or hand over to a human. Policy guardrails check every
            step before it runs. Nothing is pre-scripted.
          </div>
          <div className="flow">
            {["Notice", "Understand", "Collect proof", "Check list", "Ask only what's missing", "Agent decides", "Guardrails check",
              "Proof pack", "Send", "Follow up", "Learn"].map((s) => <span key={s}>{s}</span>)}
          </div>
        </div>
        <div className="card">
          <h3>Safety built in</h3>
          <div className="small" style={{ display: "grid", gap: 6 }}>
            <div>🔒 The AI can't send money. It can only fight or accept, and accepting needs the merchant's OTP.</div>
            <div>⛨ The agent decides, but every action passes the guardrails in rules.yaml first. Blocked actions are shown with the reason.</div>
            <div>⏱ Deadline watcher: use <b>+6h / +1 day</b> in the top bar to fast-forward.</div>
            <div>🛡 Text hidden in bills or chats is treated as data and recorded.</div>
          </div>
        </div>
      </div>

      <div className="section-head">
        <h2>20 dispute scenarios</h2>
        <span className="muted">Highlighted cards are the main demo cases.</span>
        <span className="spacer" />
        <label className="row small">
          <input type="checkbox" className="toggle" checked={onlyFeatured} onChange={(e) => setOnlyFeatured(e.target.checked)} />
          Main demo cases only
        </label>
      </div>
      {err && <div className="card err">Can't reach the backend: {err}. Start it on port 8000.</div>}
      <div className="grid scen-grid">
        {shown.map((s) => (
          <div key={s.dispute_id} className={`card scen ${s.featured ? "featured" : ""}`}>
            {s.featured && <span className="star">MAIN DEMO</span>}
            <div className="scen-n">#{s.n} · {s.merchant}</div>
            <div className="scen-title">{s.title}</div>
            <div className="row">
              <b>{rupee(s.amount)}</b>
              <span className="tag">{REASON[s.reason_code]}</span>
            </div>
            <div className="scen-msg">"{s.message}"</div>
            <div className="small muted">
              Expected path: <span className={`tag tag-${s.expect.split(" ")[0]}`}>{s.expect}</span>
            </div>
            <div className="scen-foot">
              {s.case_id ? (
                <>
                  <StagePill stage={s.stage} />
                  <span className="spacer" />
                  <button className="btn btn-light sm" onClick={() => onOpen(s.case_id)}>Open case</button>
                </>
              ) : (
                <button className="btn btn-cyan sm" disabled={raising === s.dispute_id} onClick={() => raise(s)}>
                  {raising === s.dispute_id ? "Raising…" : "Raise this dispute"}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
