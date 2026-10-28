"use client";

import type { Control, Step, StepStatus } from "@contract/types";
import { describeAction } from "@/lib/jobReducer";

const statusLabels: Record<StepStatus, string> = {
  pending: "pending",
  awaiting_approval: "needs approval",
  running: "pressing",
  verifying: "checking screen",
  succeeded: "verified",
  failed: "failed",
  skipped: "skipped",
};

const statusIcons: Record<StepStatus, string> = {
  pending: "",
  awaiting_approval: "!",
  running: "",
  verifying: "",
  succeeded: "✓",
  failed: "×",
  skipped: "–",
};

interface Props {
  steps: Step[] | null;
  controls: Control[];
  planning: boolean;
}

export default function StepList({ steps, controls, planning }: Props) {
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
        {steps.map((step) => {
          const retryCount = step.retries ? step.retries.length : 0;
          let chip = statusLabels[step.status];
          if (step.status === "succeeded" && retryCount > 0) chip = "verified after retry";

          return (
            <li key={step.step_id} className={`step step-${step.status}`}>
              <span className="step-dot" aria-hidden>
                {statusIcons[step.status] || step.index}
              </span>
              <div className="step-body">
                <div className="step-top">
                  <span className="step-intent">{step.intent}</span>
                  <span className={`chip chip-${step.status}${retryCount > 0 ? " chip-retried" : ""}`}>{chip}</span>
                </div>
                <div className="step-meta">
                  {describeAction(step, controls)} · expect “{step.expect.text_contains}”
                  {step.requires_approval && <span className="tag-approval"> · approval</span>}
                </div>

                {/* the self-correction moment, keep this visible, judges should see it */}
                {step.retries &&
                  step.retries.map((retry) => (
                    <div key={retry.attempt} className="retry-note">
                      ↻ {retry.problem}
                    </div>
                  ))}

                {step.error && <div className="error-note">{step.error}</div>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
