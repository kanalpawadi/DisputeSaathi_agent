# DisputeSaathi

An AI teammate for Paytm merchants. When a customer disputes a payment, an AI agent investigates the
case, collects proof from the merchant's systems, and decides what to do: fight with a proof pack,
suggest accepting, ask the merchant for what is missing, or hand the case to a human. Policy
guardrails check every action before it runs, and accepting always needs the merchant's OTP.

Paytm Build for India AI Hackathon, Track 3: Autonomous AI Teammates.

## Run it

You need Python 3.10+ and Node 18+. Every Python package lives in a virtual environment.

**1. Backend (FastAPI, port 8000)**

```bash
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

On macOS/Linux use `.venv/bin/python` instead of `.venv\Scripts\python.exe`.

**2. Frontend (React + Vite, port 5173)**

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

### AI mode

Put keys in `backend/.env` (copy `backend/.env.example`; git ignores `.env`). `DS_LLM_PROVIDER` can be
`auto` (Claude if its key is set, else Groq, else offline), `claude`, `groq` or `offline`.

- **Groq (free tier, tested):** set `GROQ_API_KEY`. The work is split across models because free-tier
  limits are per model (about 8,000 tokens a minute each): `openai/gpt-oss-120b` runs the agent loop,
  `openai/gpt-oss-20b` reads complaints and writes notes, and `qwen/qwen3.8-27b` reads bill photos.
  Calls go out one at a time and wait for a rate-limit reset (up to 20 s) before falling back.
  Raising cases one after another is fine; raising many at once can still hit the limit.
- **Claude:** set `ANTHROPIC_API_KEY`. The default model is `claude-opus-5-5`; change it with `DS_LLM_MODEL`.
- **Offline (no key, or `DS_OFFLINE=1`):** a fixed plan and first-match rules do the same steps, so the
  demo never depends on the internet.

With a key, the agent decides each step and the guardrails in `rules.yaml` can block it (it then
chooses again, up to 3 tries). If the agent fails or keeps getting blocked, the fixed rules take that
step. Because the agent decides, it can take a different (still allowed) path from the "Expected path"
on a card, for example handing a weak ₹150 case to Paytm's team instead of suggesting accept.

Other settings: `DS_STEP_DELAY` (seconds between agent steps on the live screen, default 0.7).

## Demo script (about 5 minutes)

1. **Pick a case → #1** "Item not received". Watch the live steps: alert, AI reading, tool calls,
   checklist, "Agent proposes: Fight", "Policy allowed" (send mode R11 auto-send), proof pack PDF,
   auto-sent to Paytm.
2. **#3** ₹150 with weak proof. The AI suggests accepting. In **Merchant app → Priya's Handloom**,
   read the OTP from the phone and approve.
3. **#4** Bill missing. The AI asks on WhatsApp. On the phone, tap 📷 → *Send photo*. The AI reads the
   bill, checks the order and amount, and finishes the pack.
4. **#5** "I didn't make this payment". Handed to **Paytm team** with a one-minute note.
5. **#6** Merchant offline. The approval request fails on every channel. Press **+1 day**: the merchant's
   deadline rule fights for them. Turn the network back on to see the "welcome back" note.
6. **#7** A bill with hidden text telling the AI to accept and refund. It is flagged and recorded; the
   agent ignores it, and guardrail G7 locks accepting for the case even if the agent were fooled.
7. **Paytm team:** mark results (won / lost / customer re-raised → second reply). End on **Impact**.

## How it is built

```
Paytm (mock) --signed webhook--> FastAPI --> AI agent --(read-only tools)--> Paytm payments,
                                     |   ^                                     orders, billing,
                          propose_action  | blocked: why + allowed actions     courier, refunds,
                                     v   |                                     chats, service logs
                             Policy guardrails (rules.yaml)
                                     | allowed
             +-----------+-----------+-----------+---------------+
             v           v           v           v               v
        Ask merchant  Proof pack  Submit via   Accept (OTP)   Hand to Paytm
        (WhatsApp/    PDF         Paytm API    only           team + note
         SMS/voice)               (idempotent)
```

| Part | File |
|---|---|
| Policy: guardrails, fight send modes, offline rules | `backend/app/rules.yaml`, `rules.py` |
| Proof checklists and scoring (by code, not the AI) | `backend/app/checklists.py` |
| Case stages (no double sending) | `backend/app/state.py` |
| Agent: notice → understand → investigate → decide → guard → act → learn | `backend/app/agent.py` |
| Fixed tool list: read-only data tools, `check_proof`, `propose_action` | `backend/app/tools.py` |
| Agent loop (Claude or Groq) and offline fallback | `backend/app/llm.py`, `ocr.py` |
| Hidden-instruction detection and personal-data masking | `backend/app/security.py` |
| Mock Paytm Dispute API and webhook | `backend/app/mock_paytm.py` |
| WhatsApp / SMS / voice fallback ladder, OTP | `backend/app/messaging.py` |
| Proof-pack PDF | `backend/app/pdf_pack.py` |
| 20 seeded scenarios | `backend/app/seed.py` |
| React screens | `frontend/src/views`, `frontend/src/components/CaseDrawer.jsx` |

## Safety rules

- The AI agent decides, but it can only *propose* actions. Each proposal is checked against the
  guardrails in `rules.yaml`; a blocked proposal is logged with the reason. The AI has no tool that
  moves money.
- Before a proposal is judged, any standard check the agent skipped is run, so the guardrails never
  depend on the agent's diligence.
- Whether a fight is sent automatically (auto-send, deadline rule) or needs the merchant's OTP is
  decided by `fight_modes` in `rules.yaml`, not by the agent.
- Fraud claims, reason-code mismatches, and big or strong-proof cases can never be suggested for accept
  by the agent.
- Accepting always needs the merchant's OTP in the app (or the Paytm team). SMS can only confirm
  **fight**, which moves no money.
- The merchant's deadline rule can only fight, never accept, and has an amount limit. Above the limit,
  Paytm's team takes over.
- Proof missing with less than 24 hours left: handed to Paytm's team.
- Every step is logged with time and reason. Customer phone numbers and UPI IDs are masked in logs.
