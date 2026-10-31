"use client";

// The card that shows up when a step is waiting on a human. It's the hero screen of the demo,
// so everything you need to decide is on it, top to bottom: what's about to be pressed, how
// risky the scorer thinks it is (and which model scored it), what the screen says right now and
// how sure vision is of that read, the camera frame from before the press, then the buttons.
// Risk tier + reason come from the Muse Spark Open scorer (risk/).
// High risk (or not scored at all) = hold-to-approve, low/medium = a normal tap. A step that
// turns something on (step.turns_on_output, the timer's ST/SP) or wipes something
// (step.destructive, RESET on a running timer), both set by the backend, is always a hold,
// whatever the tier: there's no tap button at all then.
// What Reject does (abort / pause / replan) is a setting, see REJECT_BEHAVIOR in lib/config.ts.

import { useEffect, useRef, useState } from "react";
import type { Device, RejectBehavior, Step } from "@contract/types";
import { describeAction, findControl } from "@/lib/jobReducer";
import { RISK_ICONS, type RiskScore } from "@/lib/risk";
import ConfidenceBadge from "./ConfidenceBadge";
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
  pauseReason: string | null; // the backend's Job.pause_reason while paused
  rejectBehavior: RejectBehavior;
  // turns a frame path from the contract into something <img> can load (null = can't)
  frameUrl: (path: string | null) => string | null;
  onDecide: (decision: "approve" | "reject", info: DecisionInfo) => void;
}

export default function ApprovalCard({ step, device, risk, paused, pauseReason, rejectBehavior, frameUrl, onDecide }: Props) {
  // when did this card appear? time_to_decide_ms counts from here, not from page load.
  // a pause puts the same step back in front of you, so that restarts the clock too
  const shownAt = useRef(0);
  const [sent, setSent] = useState(false); // one decision per showing, no double clicks
  useEffect(() => {
    shownAt.current = performance.now();
    setSent(false);
  }, [step.step_id, paused]);

  const target = step.action.type === "wait" ? null : findControl(device.controls, step.action.control_id);

  // high risk has to be held, not tapped. no score at all counts as high (never assume low).
  // turning something on or wiping something is always a hold, the score can't talk it down to a tap
  const turnsOnOutput = step.turns_on_output === true;
  const destructive = step.destructive === true;
  const needsHold = turnsOnOutput || destructive || risk === null || risk.tier === "high";

  // the frame from before the press. the real backend doesn't fill that in yet, so fall back to
  // the camera's latest frame, and say which one it is
  let frameSrc = frameUrl(step.evidence.before_frame_url);
  let frameLabel = "before the press";
  if (!frameSrc) {
    frameSrc = frameUrl(device.camera.latest_frame_url);
    frameLabel = "camera now";
  }

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
    <section className={cardClass} aria-live="assertive" aria-label={`Approval for step ${step.index}`}>
      <div className="approval-head">
        <span className="approval-kicker">
          {paused ? `Paused at step ${step.index}` : `Step ${step.index} is waiting for you`}
        </span>
        {needsHold && <span className="approval-hold-tag">hold to approve</span>}
      </div>

      <h2>{step.intent}</h2>

      {/* what the finger is about to do, biggest thing after the title */}
      <div className="approval-action">
        <span className="approval-action-verb">{describeAction(step, device.controls)}</span>
        {target ? (
          <span className="approval-action-target">
            button <strong>{target.label}</strong> <span className="mono">{target.control_id}</span>
          </span>
        ) : (
          <span className="approval-action-target muted">no button pressed</span>
        )}
      </div>

      <div className="approval-risk">
        {risk ? (
          <>
            <span className={`risk risk-lg risk-${risk.tier}`}>
              <span aria-hidden>{RISK_ICONS[risk.tier]}</span> {risk.tier} risk
            </span>
            <p className="approval-risk-reason">{risk.reason}</p>
            <p className="approval-risk-by">
              {/* the mock's stand-in when the risk scorer can't answer */}
              {risk.scoredBy === "mock score" ? (
                "mock score (risk scorer not reachable)"
              ) : (
                <>
                  scored by <span className="approval-risk-model">{risk.scoredBy}</span>
                </>
              )}
            </p>
          </>
        ) : (
          <>
            <span className="slot-empty">not scored yet</span>
            <p className="approval-risk-by">waiting on the risk scorer</p>
          </>
        )}
      </div>

      <dl className="approval-facts">
        <dt>Screen now</dt>
        <dd className="approval-screen">
          <span className="mono">{step.evidence.screen_text ?? "unknown"}</span>
          <ConfidenceBadge confidence={step.evidence.screen_confidence} />
        </dd>

        <dt>Expect after</dt>
        <dd className="mono">{step.expect.text_contains}</dd>
      </dl>

      {frameSrc && (
        <figure className="approval-frame">
          {/* eslint-disable-next-line @next/next/no-img-element -- frames come from the backend at runtime */}
          <img src={frameSrc} alt={`Camera, ${frameLabel}`} />
          <figcaption>{frameLabel}</figcaption>
        </figure>
      )}

      {step.approval_reason && <p className="approval-planner-note">Planner says: {step.approval_reason}</p>}
      {turnsOnOutput && (
        <p className="approval-planner-note">This step turns something on, so it always takes a press-and-hold.</p>
      )}
      {destructive && (
        <p className="approval-planner-note">This step wipes something that's set or counting, so it always takes a press-and-hold.</p>
      )}

      {paused && (
        <p className="approval-paused-note">
          {pauseReason ?? "You rejected this step, so the job is paused. Nothing was pressed. Approve to carry on, or Stop the job."}
        </p>
      )}

      <div className="approval-actions">
        {!paused && (
          <button type="button" className="btn btn-ghost btn-reject" disabled={sent} onClick={() => decide("reject", "tap")}>
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
