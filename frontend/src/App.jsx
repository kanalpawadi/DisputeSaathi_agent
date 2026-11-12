import React, { useState } from "react";
import { api, fmtTime } from "./api.js";
import { usePoll } from "./hooks.js";
import DemoView from "./views/DemoView.jsx";
import MerchantView from "./views/MerchantView.jsx";
import OpsView from "./views/OpsView.jsx";
import ImpactView from "./views/ImpactView.jsx";
import CaseDrawer from "./components/CaseDrawer.jsx";

const TABS = [
  ["demo", "Pick a case"],
  ["merchant", "Merchant app"],
  ["ops", "Paytm team"],
  ["impact", "Impact"],
];

export default function App() {
  const [tab, setTab] = useState("demo");
  const [openCase, setOpenCase] = useState(null);
  const [health, healthErr, refreshHealth] = usePoll(api.health, 3000);
  const [busy, setBusy] = useState(false);

  const advance = async (h) => {
    setBusy(true);
    try {
      await api.advance(h);
      await refreshHealth();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    if (!confirm("Reset all demo data? Every case and message will be cleared.")) return;
    try {
      await api.reset();
      location.reload();
    } catch (e) {
      alert(e.message);
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo" aria-hidden>✓</div>
          <div>
            <div className="brand-name">DisputeSaathi</div>
            <div className="brand-sub">AI teammate for Paytm merchant disputes</div>
          </div>
        </div>
        <nav className="tabs">
          {TABS.map(([k, label]) => (
            <button key={k} className={tab === k ? "tab active" : "tab"} onClick={() => setTab(k)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="controls">
          <span className={health?.llm ? "pill pill-ai" : "pill pill-offline"} title="How the AI is running">
            {healthErr ? "Backend offline" : health ? health.ai_mode : "Connecting…"}
          </span>
          <div className="clock" title="Simulated clock — fast-forward to test deadlines">
            <span className="clock-label">Demo clock</span>
            <span className="clock-time">{health ? fmtTime(health.now) : "--"}</span>
            {health?.offset_hours ? <span className="clock-off">+{health.offset_hours}h</span> : null}
          </div>
          <button className="btn btn-ghost sm" disabled={busy} onClick={() => advance(6)}>+6h</button>
          <button className="btn btn-ghost sm" disabled={busy} onClick={() => advance(24)}>+1 day</button>
          <button className="btn btn-ghost sm" onClick={reset}>Reset</button>
        </div>
      </header>

      <main className="main">
        {tab === "demo" && <DemoView onOpen={setOpenCase} />}
        {tab === "merchant" && <MerchantView onOpen={setOpenCase} />}
        {tab === "ops" && <OpsView onOpen={setOpenCase} />}
        {tab === "impact" && <ImpactView />}
      </main>

      {openCase && <CaseDrawer caseId={openCase} onClose={() => setOpenCase(null)} />}
    </div>
  );
}
