// Risk scoring shape. Thursday's job: fill this from the Muse Spark Open scorer
// (tier + one-line reason per step, in context). Until then the approval card gets null
// and shows empty slots.

export type RiskTier = "low" | "medium" | "high";

export interface RiskScore {
  tier: RiskTier;
  reason: string;
  scoredBy: string; // e.g. the scorer's model id
}
