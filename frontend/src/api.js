async function req(method, url, body, isForm) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else {
      opts.headers["content-type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `Request failed (${r.status})`);
  return data;
}

export const api = {
  health: () => req("GET", "/api/health"),
  advance: (hours) => req("POST", "/api/clock/advance", { hours }),
  reset: () => req("POST", "/api/demo/reset"),
  scenarios: () => req("GET", "/api/scenarios"),
  raise: (id) => req("POST", `/api/scenarios/${id}/raise`),
  cases: (q = "") => req("GET", `/api/cases${q}`),
  caseDetail: (id, after = 0) => req("GET", `/api/cases/${id}?after=${after}`),
  approve: (id, otp) => req("POST", `/api/cases/${id}/approve`, { otp }),
  decline: (id) => req("POST", `/api/cases/${id}/decline`),
  resendOtp: (id) => req("POST", `/api/cases/${id}/resend-otp`),
  merchants: () => req("GET", "/api/merchants"),
  patchMerchant: (id, body) => req("PATCH", `/api/merchants/${id}`, body),
  messages: (id) => req("GET", `/api/merchants/${id}/messages`),
  reply: (id, text) => req("POST", `/api/merchants/${id}/reply`, { text }),
  samples: (id) => req("GET", `/api/merchants/${id}/samples`),
  sendSample: (id, order_id) => req("POST", `/api/merchants/${id}/send-sample`, { order_id }),
  upload: (id, file) => {
    const f = new FormData();
    f.append("file", file);
    return req("POST", `/api/merchants/${id}/upload`, f, true);
  },
  opsQueue: () => req("GET", "/api/ops/queue"),
  opsResolve: (id, action) => req("POST", `/api/ops/cases/${id}/resolve`, { action }),
  opsOutcome: (id, result) => req("POST", `/api/ops/cases/${id}/outcome`, { result }),
  metrics: () => req("GET", "/api/metrics"),
};

export const rupee = (v) =>
  v == null ? "-" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });

export const STAGE_LABEL = {
  NEW: "New",
  COLLECTING: "Collecting proof",
  AWAITING_MERCHANT: "Waiting for merchant",
  READY: "Pack ready",
  AWAITING_APPROVAL: "Needs approval",
  ESCALATED: "With Paytm team",
  SUBMITTED: "Sent to Paytm",
  REOPENED: "Re-raised",
  WON: "Won",
  LOST: "Lost",
  ACCEPTED: "Accepted",
  EXPIRED: "Deadline missed",
};

export const fmtTime = (iso) => {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};
