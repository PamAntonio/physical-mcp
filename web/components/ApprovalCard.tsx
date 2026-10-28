"use client";

// The card that shows up when a step is waiting on a human.
// Risk tier + reason come from the Muse Spark Open scorer (risk/), shown big at the top
// so they're the first thing you read.
// High risk (or not scored at all) = hold-to-approve, low/medium = a normal tap.
// What Reject does (abort / pause / replan) is a setting, see REJECT_BEHAVIOR in lib/config.ts.

import { useEffect, useRef, useState } from "react";
import type { Device, RejectBehavior, Step } from "@contract/types";
import { describeAction, findControl } from "@/lib/jobReducer";
import { RISK_ICONS, type RiskScore } from "@/lib/risk";
import HoldToApprove from "./HoldToApprove";

const REJECT_LABELS: Record<RejectBehavior, string> = {
  abort: "Reject & stop job",
  pause: "Reject & pause",
  replan: "Reject & replan",
};

export interface DecisionInfo {
  approvalType: "tap" | "hold";
  timeToDecideMs: number;
}

interface Props {
  step: Step;
  device: Device;
  risk: RiskScore | null; // null = not scored yet
  paused: boolean; // you already rejected this step with "pause", the job is holding here
  rejectBehavior: RejectBehavior;
  onDecide: (decision: "approve" | "reject", info: DecisionInfo) => void;
}

export default function ApprovalCard({ step, device, risk, paused, rejectBehavior, onDecide }: Props) {
  // when did this card appear? time_to_decide_ms counts from here, not from page load.
  // a pause puts the same step back in front of you, so that restarts the clock too
  const shownAt = useRef(0);
  const [sent, setSent] = useState(false); // one decision per showing, no double clicks
  useEffect(() => {
    shownAt.current = performance.now();
    setSent(false);
  }, [step.step_id, paused]);

  const target = step.action.type === "wait" ? null : findControl(device.controls, step.action.control_id);

  // high risk has to be held, not tapped. no score at all counts as high (never assume low)
  const needsHold = risk === null || risk.tier === "high";

  function decide(decision: "approve" | "reject", approvalType: "tap" | "hold") {
    if (sent) return;
    setSent(true);
    const timeToDecideMs = Math.round(performance.now() - shownAt.current);
    const info: DecisionInfo = { approvalType, timeToDecideMs };
    console.log("[approval]", decision, { step_id: step.step_id, intent: step.intent, ...info, risk });
    onDecide(decision, info);
  }

  let cardClass = "approval";
  if (risk) cardClass += ` approval-${risk.tier}`;

  return (
    <section className={cardClass} aria-live="assertive">
      <div className="approval-head">
        <span className="approval-kicker">
          {paused ? `Paused at step ${step.index}` : `Step ${step.index} is waiting for you`}
        </span>
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

      {paused && (
        <p className="approval-paused-note">
          You rejected this step, so the job is paused. Nothing was pressed. Approve to carry on, or Stop the job.
        </p>
      )}

      <div className="approval-actions">
        {!paused && (
          <button type="button" className="btn btn-ghost" disabled={sent} onClick={() => decide("reject", "tap")}>
            {REJECT_LABELS[rejectBehavior]}
          </button>
        )}
        {needsHold ? (
          // keyed on paused so a pause gives you a fresh button (no leftover "let go too early")
          <HoldToApprove key={paused ? "paused" : "open"} onComplete={() => decide("approve", "hold")} />
        ) : (
          <button type="button" className="btn btn-approve" disabled={sent} onClick={() => decide("approve", "tap")}>
            Approve
          </button>
        )}
      </div>
    </section>
  );
}
