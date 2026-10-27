"use client";

// The card that shows up when a step is waiting on a human.
// High-risk steps need a press-and-hold so they can't be rubber-stamped with one tap.

import { useEffect, useRef, useState } from "react";
import type { Device, Step } from "@/lib/types";
import { describeAction, findControl } from "@/lib/jobReducer";
import { placeholderRisk } from "@/lib/risk";

const HOLD_MS = 1500;

export interface DecisionInfo {
  approvalType: "tap" | "hold";
  timeToDecideMs: number;
}

interface Props {
  step: Step;
  device: Device;
  onDecide: (decision: "approve" | "reject", info: DecisionInfo) => void;
}

export default function ApprovalCard({ step, device, onDecide }: Props) {
  // when did this card appear? we want time_to_decide_ms for rubber-stamp detection later
  const shownAt = useRef(0);
  useEffect(() => {
    shownAt.current = performance.now();
  }, [step.step_id]);

  const risk = placeholderRisk(step, device);
  const needsHold = risk.tier === "high";
  const target = step.action.type === "wait" ? null : findControl(device.controls, step.action.control_id);

  function decide(decision: "approve" | "reject", approvalType: "tap" | "hold") {
    const timeToDecideMs = Math.round(performance.now() - shownAt.current);
    onDecide(decision, { approvalType, timeToDecideMs });
  }

  return (
    <section className={`approval approval-${risk.tier}`} aria-live="assertive">
      <div className="approval-head">
        <span className="approval-kicker">Step {step.index} is waiting for you</span>
        <span className={`risk risk-${risk.tier}`}>{risk.tier} risk</span>
      </div>

      <h2>{step.intent}</h2>

      <dl className="approval-facts">
        <dt>Action</dt>
        <dd>{describeAction(step, device.controls)}</dd>
        <dt>Button</dt>
        <dd>
          {target ? target.label : "none"}
          {target && <span className="muted"> ({Math.round(target.confidence * 100)}% sure it's this one)</span>}
        </dd>
        <dt>Why</dt>
        <dd>{risk.reason}</dd>
        <dt>Screen now</dt>
        <dd className="mono">{step.evidence.screen_text ?? "unknown"}</dd>
        <dt>Expect after</dt>
        <dd className="mono">{step.expect.text_contains}</dd>
      </dl>

      <p className="scored-by">Risk scored by {risk.scoredBy}</p>

      <div className="approval-actions">
        <button className="btn btn-ghost" onClick={() => decide("reject", "tap")}>
          Reject
        </button>
        {needsHold ? (
          <HoldButton holdMs={HOLD_MS} onComplete={() => decide("approve", "hold")} />
        ) : (
          <button className="btn btn-approve" onClick={() => decide("approve", "tap")}>
            Approve
          </button>
        )}
      </div>
    </section>
  );
}

function HoldButton({ holdMs, onComplete }: { holdMs: number; onComplete: () => void }) {
  const [progress, setProgress] = useState(0);
  const startedAt = useRef<number | null>(null);
  const frame = useRef<number | null>(null);

  function tick() {
    if (startedAt.current === null) return;
    const elapsed = performance.now() - startedAt.current;
    const fraction = Math.min(elapsed / holdMs, 1);
    setProgress(fraction);
    if (fraction >= 1) {
      startedAt.current = null;
      onComplete();
      return;
    }
    frame.current = requestAnimationFrame(tick);
  }

  function begin() {
    if (startedAt.current !== null) return;
    startedAt.current = performance.now();
    frame.current = requestAnimationFrame(tick);
  }

  function cancel() {
    startedAt.current = null;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    setProgress(0);
  }

  // stop the animation if the card disappears mid-hold
  useEffect(() => {
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, []);

  return (
    <button
      className="btn btn-hold"
      style={{ ["--progress" as string]: progress }}
      onPointerDown={begin}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => {
        if ((e.key === " " || e.key === "Enter") && !e.repeat) {
          e.preventDefault();
          begin();
        }
      }}
      onKeyUp={cancel}
    >
      <span className="btn-hold-fill" aria-hidden />
      <span className="btn-hold-text">{progress > 0 ? "Keep holding…" : "Hold to approve"}</span>
    </button>
  );
}
