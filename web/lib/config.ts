// The one switch between the mock and Pam's real backend.
// Set NEXT_PUBLIC_USE_MOCK=false in .env.local to talk to the real thing.
// (Next inlines NEXT_PUBLIC_* at build time, so restart `npm run dev` after changing it.)

import type { RejectBehavior } from "@contract/types";

export const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK !== "false"; // mock unless told otherwise

export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

// the Python risk scorer (risk/server.py). only the in-browser mock calls it from here,
// the mock server reads RISK_SCORER_URL instead
export const RISK_SCORER_URL = process.env.NEXT_PUBLIC_RISK_SCORER_URL || "http://127.0.0.1:8091";

// what Reject does. Not agreed with Pam yet, so it's a setting: abort | pause | replan.
// Sent with every reject as on_reject. Bad or missing value -> pause.
export const REJECT_BEHAVIOR: RejectBehavior = pickRejectBehavior(process.env.NEXT_PUBLIC_REJECT_BEHAVIOR);

// who's approving, sent as decided_by. No logins at the hackathon, so it's just a name.
export const OPERATOR_NAME = process.env.NEXT_PUBLIC_OPERATOR_NAME || "operator";

export const DEVICE_ID = process.env.NEXT_PUBLIC_DEVICE_ID || "incubator-01";

// start the sample job as soon as the page loads. always on for the in-browser mock,
// `npm run dev:mock` turns it on for the mock server too
export const AUTO_START_SAMPLE = USE_MOCK || process.env.NEXT_PUBLIC_AUTO_START === "true";

function pickRejectBehavior(value: string | undefined): RejectBehavior {
  if (value === "abort" || value === "pause" || value === "replan") return value;
  return "pause";
}
