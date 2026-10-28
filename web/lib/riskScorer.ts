// Calls the Python risk scorer (risk/server.py, POST /score) for one step.
// Works from Node (the mock server) and the browser (the in-browser mock), both have fetch.
//
// Same safety rule as the scorer itself: if it isn't running, times out, or sends back
// junk, the step is "high" with "couldn't score". Never low, never empty.

import type { Job, RiskTier, Step } from "@contract/types";

export const DEFAULT_RISK_SCORER_URL = "http://127.0.0.1:8091";
// a bit over the scorer's own 30s model timeout, so its fallback usually answers first.
// Baseten cold starts can take ~45s, those land on the fallback (high), which is the safe side
const TIMEOUT_MS = 35_000;
const TIERS: RiskTier[] = ["low", "medium", "high"];

export interface StepRisk {
  risk_tier: RiskTier;
  risk_reason: string;
  risk_scored_by: string;
}

export function couldNotScore(): StepRisk {
  return {
    risk_tier: "high",
    risk_reason: "Couldn't score this step, treating it as high risk.",
    risk_scored_by: "fallback (scorer unreachable)",
  };
}

export async function scoreStep(scorerUrl: string, job: Job, step: Step): Promise<StepRisk> {
  try {
    const response = await fetch(`${scorerUrl}/score`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job, step_id: step.step_id }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) return couldNotScore();

    const body = await response.json();
    if (!TIERS.includes(body.risk_tier)) return couldNotScore();
    if (typeof body.risk_reason !== "string" || !body.risk_reason.trim()) return couldNotScore();
    return {
      risk_tier: body.risk_tier,
      risk_reason: body.risk_reason,
      risk_scored_by: typeof body.risk_scored_by === "string" ? body.risk_scored_by : "unknown",
    };
  } catch {
    // not running, refused, timed out, bad JSON...
    return couldNotScore();
  }
}
