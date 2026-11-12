import React, { useEffect, useRef, useState } from "react";
import { api, rupee, fmtTime } from "../api.js";
import { usePoll } from "../hooks.js";
import { StagePill, HoursLeft } from "../components/CaseDrawer.jsx";

function fileUrl(p) {
  if (!p) return null;
  const name = p.replace(/\\/g, "/").split("/").pop();
  return `/files/uploads/${name}`;
}

function Settings({ m, onSaved }) {
  const save = async (patch) => {
    try {
      await api.patchMerchant(m.id, patch);
      onSaved();
    } catch (e) {
      alert(e.message);
    }
  };
  return (
    <section className="card settings">
      <h3>Settings (set once when joining)</h3>
      <label>
        <span>Phone has network<div className="small muted">Turn off to simulate no signal</div></span>
        <input type="checkbox" className="toggle" checked={!!m.online} onChange={(e) => save({ online: e.target.checked })} />
      </label>
      <label>
        <span>Sending mode<div className="small muted">Everyone starts with "ask me first"</div></span>
        <select value={m.trust_level} onChange={(e) => save({ trust_level: e.target.value })}>
          <option value="ASK_FIRST">Ask me before sending</option>
          <option value="AUTO">Auto-send small cases</option>
        </select>
      </label>
      <label>
        <span>Auto-send limit</span>
        <input type="number" defaultValue={m.auto_send_limit} key={`a${m.id}${m.auto_send_limit}`}
          onBlur={(e) => save({ auto_send_limit: Number(e.target.value) })} />
      </label>
      <label>
        <span>Deadline rule<div className="small muted">"If I don't reply and all proof is there, fight for me. Never accept without me."</div></span>
        <input type="checkbox" className="toggle" checked={!!m.deadline_rule_enabled}
          onChange={(e) => save({ deadline_rule_enabled: e.target.checked })} />
      </label>
      <label>
        <span>Deadline rule limit<div className="small muted">Above this, Paytm's team takes over</div></span>
        <input type="number" defaultValue={m.deadline_rule_limit} key={`d${m.id}${m.deadline_rule_limit}`}
          onBlur={(e) => save({ deadline_rule_limit: Number(e.target.value) })} />
      </label>
      <div className="small muted" style={{ marginTop: 6 }}>Cases won with DisputeSaathi: <b>{m.good_cases}</b></div>
    </section>
  );
}

function Phone({ m, onOpen, refreshMerchants }) {
  const [msgs] = usePoll(() => api.messages(m.id), 1500, [m.id]);
  const [samples] = usePoll(() => api.samples(m.id), 10000, [m.id]);
  const [text, setText] = useState("");
  const [showSamples, setShowSamples] = useState(false);
  const box = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [msgs?.length]);

  const send = async () => {
    if (!text.trim()) return;
    try {
      await api.reply(m.id, text.trim());
      setText("");
    } catch (e) {
      alert(e.message);
    }
  };
  const sendSample = async (order_id) => {
    setShowSamples(false);
    try {
      const r = await api.sendSample(m.id, order_id);
      if (r.case_id) onOpen(r.case_id);
    } catch (e) {
      alert(e.message);
    }
  };
  const upload = async (f) => {
    if (!f) return;
    try {
      const r = await api.upload(m.id, f);
      if (r.case_id) onOpen(r.case_id);
    } catch (e) {
      alert(e.message);
    }
  };

  return (
    <div className="phone">
      <div className="phone-screen">
        <div className="phone-top">
          <div className="av">DS</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700 }}>DisputeSaathi · {m.business}</div>
            <div className="net">{m.online ? "● online · 4G" : "○ no signal — messages are queued"}</div>
          </div>
        </div>
        <div className="msgs" ref={box}>
          {(msgs || []).length === 0 && <div className="empty small">No messages yet.</div>}
          {(msgs || []).map((x) => (
            <div key={x.id} className={`bubble ${x.direction === "in" ? "in" : ""} ${["FAILED", "NOT_DELIVERED", "NO_ANSWER"].includes(x.status) ? "failed" : ""}`}>
              <div className={`ch ch-${x.channel}`}>{x.channel === "APP" ? "PAYTM APP" : x.channel}</div>
              {x.media_path ? <img src={fileUrl(x.media_path)} alt="bill" /> : x.text}
              <div className="meta">
                {fmtTime(x.ts)} {x.direction === "out" && x.status !== "DELIVERED" ? `· ${x.status.replace("_", " ").toLowerCase()}` : ""}
                {x.case_id && x.direction === "out" && (
                  <> · <a href="#" onClick={(e) => { e.preventDefault(); onOpen(x.case_id); }}>{x.case_id}</a></>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="compose">
          {showSamples && (
            <div className="card small" style={{ padding: 8 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Paper bills in this shop</div>
              {(samples || []).length === 0 && <div className="muted">No paper bills for this merchant.</div>}
              {(samples || []).map((s) => (
                <div key={s.order_id} className="row" style={{ padding: "3px 0" }}>
                  <img src={s.url} alt="" style={{ width: 40, borderRadius: 4 }} />
                  <span>Bill for {s.order_id}</span>
                  <span className="spacer" />
                  <button className="btn btn-green sm" onClick={() => sendSample(s.order_id)}>Send photo</button>
                </div>
              ))}
              <button className="btn btn-light sm" style={{ marginTop: 4 }} onClick={() => fileRef.current?.click()}>
                Upload a photo from this computer…
              </button>
              <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => upload(e.target.files[0])} />
            </div>
          )}
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <button className="btn btn-light sm" title="Send a photo" onClick={() => setShowSamples((v) => !v)}>📷</button>
            <input type="text" value={text} placeholder='SMS reply, e.g. "1" to fight' onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()} />
            <button className="btn btn-green sm" onClick={send}>Send</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function MerchantView({ onOpen }) {
  const [merchants, , refreshMerchants] = usePoll(api.merchants, 4000);
  const [sel, setSel] = useState("M2");
  const m = (merchants || []).find((x) => x.id === sel);
  const [cases] = usePoll(() => api.cases(`?merchant_id=${sel}`), 2000, [sel]);
  const todo = (cases || []).filter((c) => ["AWAITING_APPROVAL", "AWAITING_MERCHANT"].includes(c.stage));

  return (
    <div>
      <div className="chips">
        {(merchants || []).map((x) => (
          <button key={x.id} className={`chip ${x.id === sel ? "active" : ""}`} onClick={() => setSel(x.id)}>
            <span className={`dotsm ${x.online ? "" : "off"}`} />
            {x.business}
          </button>
        ))}
      </div>
      {m && (
        <div className="merchant-layout">
          <div className="grid">
            <section className="card">
              <div className="row">
                <h2>{m.business}</h2>
                <span className="muted">{m.category} · {m.name} · speaks {m.language}</span>
              </div>
              <div className="small muted" style={{ marginTop: 4 }}>
                This is what the merchant sees in the Paytm app. The merchant only says yes or no.
              </div>
            </section>

            <section className="card">
              <h3>Needs your action ({todo.length})</h3>
              {todo.length === 0 && <div className="muted small">Nothing to do. DisputeSaathi is handling everything.</div>}
              {todo.map((c) => (
                <div key={c.id} className="row" style={{ padding: "8px 0", borderBottom: "1px solid #eef2f7" }}>
                  <StagePill stage={c.stage} />
                  <b>{rupee(c.amount)}</b>
                  <span>{c.reason_label}</span>
                  <span className="small muted">
                    {c.stage === "AWAITING_MERCHANT" ? "Send the missing proof on WhatsApp" :
                      c.pending_action?.action === "ACCEPT" ? "AI suggests accepting" : "Preview pack and approve"}
                  </span>
                  <span className="spacer" />
                  <HoursLeft h={c.hours_left} stage={c.stage} />
                  <button className="btn sm" onClick={() => onOpen(c.id)}>Open</button>
                </div>
              ))}
            </section>

            <section className="card">
              <h3>All disputes</h3>
              {(cases || []).length === 0 ? (
                <div className="empty">No disputes yet. Raise one from <b>Pick a case</b>.</div>
              ) : (
                <table className="t">
                  <thead><tr><th>Case</th><th>Reason</th><th>Amount</th><th>Stage</th><th>Deadline</th><th>Handled by</th></tr></thead>
                  <tbody>
                    {cases.map((c) => (
                      <tr key={c.id} className="clickable" onClick={() => onOpen(c.id)}>
                        <td className="mono">{c.id}</td>
                        <td>{c.reason_label || "…"}</td>
                        <td>{rupee(c.amount)}</td>
                        <td><StagePill stage={c.stage} /></td>
                        <td><HoursLeft h={c.hours_left} stage={c.stage} /></td>
                        <td className="small">{c.resolved_by ? c.resolved_by.replace(/_/g, " ").toLowerCase() : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            <Settings m={m} onSaved={refreshMerchants} />
          </div>
          <Phone m={m} onOpen={onOpen} refreshMerchants={refreshMerchants} />
        </div>
      )}
    </div>
  );
}
