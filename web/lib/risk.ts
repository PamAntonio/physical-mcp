// PLACEHOLDER risk scoring. Thursday's job is to replace this with the
// Muse Spark Open scorer (tier + one-line reason per step, in context).
// Until then it's a fixed rule: risky control or requires_approval = high.

import type { Device, Step } from "./types";
import { findControl } from "./jobReducer";

export type RiskTier = "low" | "medium" | "high";

export interface RiskScore {
  tier: RiskTier;
  reason: string;
  scoredBy: string;
}

export function placeholderRisk(step: Step, device: Device): RiskScore {
  const scoredBy = "fixed rules (Spark Open scorer not wired yet)";
  const controlId = step.action.type === "wait" ? null : step.action.control_id;
  const control = controlId ? findControl(device.controls, controlId) : null;

  if (control && control.risky) {
    return { tier: "high", reason: step.approval_reason ?? "Changes the machine's physical state", scoredBy };
  }
  if (step.requires_approval) {
    return { tier: "medium", reason: step.approval_reason ?? "Marked as needing approval by the planner", scoredBy };
  }
  return { tier: "low", reason: "Only changes a setting on the screen", scoredBy };
}
