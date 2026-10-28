"use client";

// The card that shows up when a step is waiting on a human.
// Risk tier + reason come from the Muse Spark Open scorer (risk/), shown big at the top
// so they're the first thing you read. Approve/Reject log to the console and send the decision.

import { useEffect, useRef } from "react";
import type { Device, Step } from "@contract/types";
import { describeAction, findControl } from "@/lib/jobReducer";
import { RISK_ICONS, type RiskScore } from "@/lib/risk";

export interface DecisionInfo {
  approvalType: "tap" | "hold";
  timeToDecideMs: number;
}

interface Props {
  step: Step;
  device: Device;
  risk: RiskScore | null; // null = not scored yet
  onDecide: (decision: "approve" | "reject", info: DecisionInfo) => void;
}

export default function ApprovalCard({ step, device, risk, onDecide }: Props) {
  // when did this card appear? we want time_to_decide_ms for rubber-stamp detection later
  const shownAt = useRef(0);
  useEffect(() => {
    shownAt.current = performance.now();
  }, [step.step_id]);

  const target = step.action.type === "wait" ? null : findControl(device.controls, step.action.control_id);

  // TODO(hold-to-approve): when risk.tier === "high", swap the Approve button for
  // <HoldToApprove> (components/HoldToApprove.tsx, parked) and send approval_type "hold".
  const needsHold = risk !== null && risk.tier === "high";

  function decide(decision: "approve" | "reject") {
    const timeToDecideMs = Math.round(performance.now() - shownAt.current);
    const info: DecisionInfo = { approvalType: "tap", timeToDecideMs };
    console.log("[approval]", decision, { step_id: step.step_id, intent: step.intent, ...info, risk });
    onDecide(decision, info);
  }

  let cardClass = "approval";
  if (risk) cardClass += ` approval-${risk.tier}`;

  return (
    <section className={cardClass} aria-live="assertive">
      <div className="approval-head">
        <span className="approval-kicker">Step {step.index} is waiting for you</span>
      </div>

      <h2>{step.intent}</h2>

      <div className="approval-risk">
        {risk ? (
          <>
            <span className={`risk risk-lg risk-${risk.tier}`}>
              <span aria-hidden>{RISK_ICONS[risk.tier]}</span> {risk.tier} risk
            </span>
            <p className="approval-risk-reason">{risk.reason}</p>
            <p className="approval-risk-by">scored by {risk.scoredBy}</p>
          </>
        ) : (
          <>
            <span className="slot-empty">not scored yet</span>
            <p className="approval-risk-by">waiting on the risk scorer</p>
          </>
        )}
      </div>

      <dl className="approval-facts">
        <dt>Action</dt>
        <dd>{describeAction(step, device.controls)}</dd>

        <dt>Button</dt>
        <dd>
          {target ? (
            <>
              <strong>{target.label}</strong> <span className="muted mono">{target.control_id}</span>
              <span className="muted"> · {Math.round(target.confidence * 100)}% sure it's this one</span>
            </>
          ) : (
            <span className="muted">none (no button pressed)</span>
          )}
        </dd>

        <dt>Screen now</dt>
        <dd className="mono">{step.evidence.screen_text ?? "unknown"}</dd>

        <dt>Expect after</dt>
        <dd className="mono">{step.expect.text_contains}</dd>
      </dl>

      {step.approval_reason && <p className="approval-planner-note">Planner says: {step.approval_reason}</p>}

      <div className="approval-actions">
        <button type="button" className="btn btn-ghost" onClick={() => decide("reject")}>
          Reject
        </button>
        {/* hold-to-approve goes here for high-risk steps, see TODO above */}
        <button type="button" className="btn btn-approve" onClick={() => decide("approve")}>
          Approve
        </button>
      </div>
      {needsHold && <p className="approval-hold-note">High risk: hold-to-approve will go here (not built yet).</p>}
    </section>
  );
}
