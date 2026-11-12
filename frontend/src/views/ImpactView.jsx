import React, { useState } from "react";
import { api, rupee } from "../api.js";
import { usePoll } from "../hooks.js";

function Tile({ v, l, s, good }) {
  return (
    <div className={`card tile ${good ? "good" : ""}`}>
      <div className="l">{l}</div>
      <div className="v">{v}</div>
      {s && <div className="s">{s}</div>}
    </div>
  );
}

function Calculator() {
  const [x, setX] = useState({ disputes: 40, lostShare: 35, winBack: 50, avg: 1800, handling: 300, ourCost: 15 });
  const set = (k) => (e) => setX({ ...x, [k]: Number(e.target.value) });
  const recovered = x.disputes * (x.lostShare / 100) * (x.winBack / 100) * x.avg;
  const handlingSaved = x.disputes * Math.max(0, x.handling - x.ourCost);
  const fields = [
    ["disputes", "Disputes per month"],
    ["lostShare", "% lost today (missed deadline / weak proof)"],
    ["winBack", "% of those we can win back"],
    ["avg", "Average dispute amount (₹)"],
    ["handling", "Handling cost per dispute today (₹)"],
    ["ourCost", "DisputeSaathi cost per dispute (₹)"],
  ];
  return (
    <section className="card calc">
      <h3>Savings estimate (honest version)</h3>
      <div className="small muted" style={{ marginBottom: 8 }}>
        Money saved = disputes × share lost today × share we can win back × average amount. Plus handling time saved.
        Replace these with Paytm's real numbers; the goals are to be tested in a pilot, not claimed as fact.
      </div>
      {fields.map(([k, l]) => (
        <label key={k}>
          <span>{l}</span>
          <input type="number" value={x[k]} onChange={set(k)} />
        </label>
      ))}
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 10 }}>
        <Tile l="Money recovered / month" v={rupee(recovered)} good />
        <Tile l="Handling cost saved / month" v={rupee(handlingSaved)} good />
      </div>
    </section>
  );
}

export default function ImpactView() {
  const [m, err] = usePoll(api.metrics, 3000);
  if (err) return <div className="card err">{err}</div>;
  if (!m) return <div className="card empty">Loading…</div>;
  const decided = m.won + m.lost;
  return (
    <div>
      <div className="section-head">
        <h2>Impact</h2>
        <span className="muted">Live numbers from this demo session.</span>
      </div>
      <div className="grid tiles">
        <Tile l="Disputes handled" v={m.total} s={`${m.no_human} with no human · ${m.one_tap} with one tap`} />
        <Tile l="Deadlines missed" v={m.deadlines_missed} good={m.deadlines_missed === 0} s="Goal: close to zero" />
        <Tile l="Money protected (won)" v={rupee(m.money_protected)} good s={`${rupee(m.money_defended)} defended in total`} />
        <Tile l="Win rate (decided cases)" v={m.win_rate == null ? "—" : `${Math.round(m.win_rate * 100)}%`} s={`${m.won} won · ${m.lost} lost`} />
        <Tile l="Merchant time" v={`${m.merchant_minutes} min`} s={`vs ~${Math.round(m.baseline_minutes / 60)} h by hand (est. 2 h per dispute)`} good />
        <Tile l="Avg. time to a decision" v={m.avg_seconds_to_decision == null ? "—" : `${m.avg_seconds_to_decision}s`} s="Alert → proof collected → action approved by policy" />
        <Tile l="Sent to Paytm's team" v={m.to_paytm_team} s="Each with a handover note" />
        <Tile l="Hidden tricks caught" v={m.injections_blocked} s="Recorded; no effect on actions" />
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <section className="card">
          <h3>Who resolved each case</h3>
          {Object.keys(m.resolved_by).length === 0 && <div className="muted small">No cases resolved yet.</div>}
          {Object.entries(m.resolved_by).map(([k, v]) => (
            <div key={k} className="row" style={{ padding: "4px 0" }}>
              <span style={{ width: 160 }}>{k.replace(/_/g, " ").toLowerCase()}</span>
              <div className="bar" style={{ flex: 1 }}><div className="w" style={{ width: `${(v / m.total) * 100}%`, background: "var(--cyan)" }} /></div>
              <b>{v}</b>
            </div>
          ))}
          <h3 style={{ marginTop: 16 }}>Close and learn: what works per reason</h3>
          {Object.keys(m.learning).length === 0 && <div className="muted small">Mark cases won/lost on the Paytm team screen to see learning.</div>}
          {Object.entries(m.learning).map(([k, v]) => (
            <div key={k} style={{ padding: "4px 0" }}>
              <div className="row small"><b>{k}</b><span className="spacer" />{v.won} won · {v.lost} lost</div>
              <div className="bar">
                <div className="w" style={{ width: `${(v.won / (v.won + v.lost)) * 100}%` }} />
                <div className="lo" style={{ width: `${(v.lost / (v.won + v.lost)) * 100}%` }} />
              </div>
            </div>
          ))}
          {decided > 0 && <div className="small muted" style={{ marginTop: 6 }}>Proof lists are tuned from these results.</div>}
        </section>
        <Calculator />
      </div>
    </div>
  );
}
