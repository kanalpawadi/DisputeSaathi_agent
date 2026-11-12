import React, { useState } from "react";
import { api, rupee, fmtTime } from "../api.js";
import { usePoll } from "../hooks.js";
import { StagePill, HoursLeft } from "../components/CaseDrawer.jsx";

export default function OpsView({ onOpen }) {
  const [q, err, refresh] = usePoll(api.opsQueue, 2000);
  const [busy, setBusy] = useState(null);

  const act = async (id, fn) => {
    setBusy(id);
    try {
      await fn();
      refresh();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <div className="section-head">
        <h2>Paytm dispute team</h2>
        <span className="muted">Only cases that need a human come here, each with a short note. Every pack is pre-checked by the AI.</span>
      </div>
      {err && <div className="card err">{err}</div>}
      <div className="ops-layout">
        <section>
          <h3>Handovers waiting for a human ({q?.escalated.length ?? 0})</h3>
          <div className="grid">
            {q?.escalated.length === 0 && <div className="card empty">No handovers. The AI is handling everything else.</div>}
            {q?.escalated.map((c) => (
              <div key={c.id} className="card handover">
                <div className="row">
                  <b className="mono">{c.id}</b>
                  <span>{c.merchant_name}</span>
                  <b>{rupee(c.amount)}</b>
                  <span className="spacer" />
                  <HoursLeft h={c.hours_left} stage={c.stage} />
                </div>
                <div className="note" style={{ marginTop: 8 }}>{c.handover_note}</div>
                <div className="row" style={{ marginTop: 10 }}>
                  <button className="btn btn-green sm" disabled={busy === c.id} onClick={() => act(c.id, () => api.opsResolve(c.id, "fight"))}>Fight with prepared pack</button>
                  <button className="btn btn-red sm" disabled={busy === c.id} onClick={() => act(c.id, () => api.opsResolve(c.id, "accept"))}>Accept</button>
                  {c.has_pack && <a className="btn btn-light sm" href={`/api/cases/${c.id}/pack`} target="_blank" rel="noreferrer">Pack PDF</a>}
                  <button className="btn btn-light sm" onClick={() => onOpen(c.id)}>Full history</button>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="grid">
          <div className="card">
            <h3>Packs received — AI pre-checked ({q?.submitted.length ?? 0})</h3>
            {q?.submitted.length === 0 && <div className="muted small">Nothing submitted yet.</div>}
            {q?.submitted.length > 0 && (
              <table className="t">
                <thead><tr><th>Case</th><th>Amount</th><th>Pre-check</th><th>Decide</th></tr></thead>
                <tbody>
                  {q.submitted.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <a href="#" className="mono" onClick={(e) => { e.preventDefault(); onOpen(c.id); }}>{c.id}</a>
                        <div className="small muted">{c.merchant_name}</div>
                        <div className="small muted">by {c.resolved_by?.replace(/_/g, " ").toLowerCase()}</div>
                      </td>
                      <td>{rupee(c.amount)}</td>
                      <td className="small">
                        <b style={{ color: c.precheck?.score >= 0.8 ? "var(--green)" : "var(--amber)" }}>{c.precheck?.verdict}</b>
                        <div className="muted">proof {c.precheck?.score?.toFixed(2)}</div>
                        {c.precheck?.weak?.length > 0 && <div className="muted">check: {c.precheck.weak.join(", ")}</div>}
                        <a href={`/api/cases/${c.id}/pack`} target="_blank" rel="noreferrer">open pack</a>
                      </td>
                      <td>
                        <div className="grid" style={{ gap: 4 }}>
                          <button className="btn btn-green sm" disabled={busy === c.id} onClick={() => act(c.id, () => api.opsOutcome(c.id, "WON"))}>Merchant wins</button>
                          <button className="btn btn-red sm" disabled={busy === c.id} onClick={() => act(c.id, () => api.opsOutcome(c.id, "LOST"))}>Customer wins</button>
                          <button className="btn btn-light sm" disabled={busy === c.id} onClick={() => act(c.id, () => api.opsOutcome(c.id, "REOPENED"))}>Customer re-raised</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="card">
            <h3>Closed</h3>
            {q?.closed.length === 0 && <div className="muted small">No closed cases yet.</div>}
            {q?.closed.map((c) => (
              <div key={c.id} className="row small" style={{ padding: "5px 0", borderBottom: "1px solid #eef2f7" }}>
                <a href="#" className="mono" onClick={(e) => { e.preventDefault(); onOpen(c.id); }}>{c.id}</a>
                <span>{c.merchant_name}</span>
                <span>{rupee(c.amount)}</span>
                <span className="spacer" />
                <StagePill stage={c.stage} />
                <span className="muted">{fmtTime(c.closed_at)}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
