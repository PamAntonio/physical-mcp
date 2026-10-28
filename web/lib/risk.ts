// Risk scoring shape. The Muse Spark Open scorer (risk/) puts risk_tier, risk_reason and
// risk_scored_by on the step (proposed contract fields, see shared/types.ts).
// null = this step hasn't been scored (e.g. Pam's backend doesn't send them yet).

import type { RiskTier, Step } from "@contract/types";

export type { RiskTier };

export interface RiskScore {
  tier: RiskTier;
  reason: string;
  scoredBy: string; // e.g. the scorer's model id
}

// each tier gets a shape as well as a color, so they don't only differ by hue
export const RISK_ICONS: Record<RiskTier, string> = {
  low: "●",
  medium: "▲",
  high: "■",
};

export function riskFromStep(step: Step): RiskScore | null {
  if (!step.risk_tier) return null;
  return {
    tier: step.risk_tier,
    reason: step.risk_reason ?? "",
    scoredBy: step.risk_scored_by ?? "unknown",
  };
}
