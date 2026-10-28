"use client";

// The plan, one row per step. Each status gets its own look (dot shape + color + chip),
// and retries get a big obvious block because the self-correction is the demo moment.
//
// Note: "retried" isn't a StepStatus in the contract. A retry just adds to step.retries
// while the step stays running/verifying, then ends succeeded or failed. So we work it out
// from retries.length.

import type { Action, Control, Expectation, Retry, Step, StepStatus } from "@contract/types";
import { describeCorrection, findControl } from "@/lib/jobReducer";

const DEFAULT_MAX_ATTEMPTS = 3; // contract says max_attempts defaults to 3

interface Props {
  steps: Step[] | null;
  controls: Control[];
  planning: boolean;
  selectedStepId: string | null;
  onSelect: (stepId: string | null) => void;
}

export default function StepList({ steps, controls, planning, selectedStepId, onSelect }: Props) {
  if (!steps) {
    return (
      <section className="panel">
        <h2>Plan</h2>
        <p className="muted">{planning ? "Muse Spark is planning…" : "Send a job to get a plan."}</p>
      </section>
    );
  }

  return (
    <section className="panel">
      <h2>Plan</h2>
      <ol className="steps">
        {steps.map((step) => (
          <StepRow
            key={step.step_id}
            step={step}
            controls={controls}
            selected={step.step_id === selectedStepId}
            onClick={() => onSelect(step.step_id === selectedStepId ? null : step.step_id)}
          />
        ))}
      </ol>
      <p className="steps-hint">Click a step to see its control on the camera.</p>
    </section>
  );
}

interface RowProps {
  step: Step;
  controls: Control[];
  selected: boolean;
  onClick: () => void;
}

function StepRow({ step, controls, selected, onClick }: RowProps) {
  const retries = step.retries ?? [];
  const wasRetried = retries.length > 0;
  const look = pickLook(step.status, wasRetried);
  const maxAttempts = step.max_attempts ?? DEFAULT_MAX_ATTEMPTS;

  let className = `step step-${look.key}`;
  if (selected) className += " step-selected";

  return (
    <li className={className}>
      <button type="button" className="step-button" aria-pressed={selected} onClick={onClick}>
        <span className="step-dot" aria-hidden>
          {look.icon || step.index}
        </span>

        <span className="step-body">
          <span className="step-top">
            <span className="step-intent">
              <span className="step-num">{step.index}.</span> {step.intent}
            </span>
            <span className={`chip chip-${look.key}`}>{look.label}</span>
          </span>

          <span className="step-fields">
            <span className="step-field">
              <span className="step-field-name">do</span>
              <span className="step-field-value">{describeAction(step.action, controls)}</span>
            </span>
            <span className="step-field">
              <span className="step-field-name">expect</span>
              <span className="step-field-value">{describeExpect(step.expect, controls)}</span>
            </span>
            <span className="step-field">
              <span className="step-field-name">attempts</span>
              <span className={step.attempts > 1 ? "step-field-value step-attempts-warn" : "step-field-value"}>
                {step.attempts}/{maxAttempts}
              </span>
            </span>
          </span>

          {step.requires_approval && step.status !== "awaiting_approval" && (
            <span className="step-approval-note">
              needs a human OK{step.approval_reason ? `: ${step.approval_reason}` : ""}
            </span>
          )}
          {step.status === "awaiting_approval" && (
            <span className="step-approval-note step-approval-now">
              Waiting for you{step.approval_reason ? `: ${step.approval_reason}` : ""}
            </span>
          )}

          {/* the self-correction moment, keep this loud */}
          {wasRetried && (
            <span className="retry-block">
              {retries.map((retry) => (
                <span key={retry.attempt} className="retry-line">
                  <span className="retry-icon" aria-hidden>
                    ↻
                  </span>
                  <span>
                    <strong>Retried.</strong> {describeRetry(retry, step.expect, controls)}
                    <span className="retry-problem">{retry.problem}</span>
                  </span>
                </span>
              ))}
            </span>
          )}

          {step.error && <span className="error-note">{step.error}</span>}
        </span>
      </button>
    </li>
  );
}

// ---- how each status looks ----

interface Look {
  key: string; // css suffix: step-<key>, chip-<key>
  label: string;
  icon: string; // empty = show the step number
}

function pickLook(status: StepStatus, wasRetried: boolean): Look {
  if (status === "pending") return { key: "pending", label: "pending", icon: "" };
  if (status === "running") {
    if (wasRetried) return { key: "retrying", label: "retrying", icon: "" };
    return { key: "running", label: "pressing", icon: "" };
  }
  if (status === "verifying") {
    if (wasRetried) return { key: "retrying", label: "re-checking screen", icon: "" };
    return { key: "running", label: "checking screen", icon: "" };
  }
  if (status === "succeeded") {
    if (wasRetried) return { key: "retried", label: "verified after retry", icon: "↻" };
    return { key: "succeeded", label: "verified", icon: "✓" };
  }
  if (status === "awaiting_approval") return { key: "awaiting", label: "needs approval", icon: "!" };
  if (status === "failed") return { key: "failed", label: wasRetried ? "failed after retries" : "failed", icon: "×" };
  return { key: "skipped", label: "skipped", icon: "–" };
}

// ---- text helpers ----

function controlName(controlId: string, controls: Control[]): string {
  const control = findControl(controls, controlId);
  return control ? control.label : controlId;
}

// "press ▲ ×12", "hold POWER 2.0s", "wait 5s", "read_screen Display"
function describeAction(action: Action, controls: Control[]): string {
  if (action.type === "wait") return `wait ${action.seconds}s`;
  const name = controlName(action.control_id, controls);
  if (action.type === "read_screen") return `read_screen ${name}`;

  let text = `press ${name}`;
  if (action.hold_ms) text = `hold ${name} ${(action.hold_ms / 1000).toFixed(1)}s`;
  if (action.count > 1) text += ` ×${action.count}`;
  return text;
}

// "Display shows “37.0”" or "RUN light on"
function describeExpect(expect: Expectation, controls: Control[]): string {
  const name = controlName(expect.control_id, controls);
  if (expect.indicator_on !== undefined) return `${name} light ${expect.indicator_on ? "on" : "off"}`;
  return `${name} shows “${expect.text_contains}”`;
}

// "Attempt 1: screen said “SET TEMP 36.0”, wanted “37.0”. Pressed ▲ once more."
function describeRetry(retry: Retry, expect: Expectation, controls: Control[]): string {
  const saw = `Attempt ${retry.attempt}: screen said “${retry.screen_text}”, wanted “${expect.text_contains}”.`;
  return `${saw} ${describeCorrection(retry.correction, controls)}`;
}
