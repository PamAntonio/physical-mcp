"use client";

// Shown while the job is paused on a read vision wasn't sure of (the backend's read_unclear
// pause). Says why in plain words (Job.pause_reason), shows the newest camera frame so it's
// obvious what the camera was looking at, and Resume asks the backend to read the screen again
// (POST /jobs/:id/resume). Nothing gets pressed while this is up.
// A step rejected with "pause" isn't this: that one resumes from its approval card.

import { useEffect, useState } from "react";
import type { Step } from "@contract/types";
import ConfidenceBadge from "./ConfidenceBadge";

interface Props {
  reason: string | null;
  step: Step | null; // the step whose check paused, or null if it paused before planning
  frameSrc: string | null;
  onResume: () => Promise<void>;
}

export default function PausePanel({ reason, step, frameSrc, onResume }: Props) {
  const [sending, setSending] = useState(false);
  // a new pause (the re-read was unclear too) gives you the button back
  useEffect(() => {
    setSending(false);
  }, [reason]);

  async function resume() {
    if (sending) return;
    setSending(true);
    try {
      await onResume();
    } catch {
      setSending(false); // the page shows the error, let them try again
    }
  }

  const where = step ? `step ${step.index}, ${step.intent}` : "before planning";

  return (
    <section className="pause-panel" aria-live="assertive" aria-label="Job paused">
      <div className="pause-head">
        <span className="pause-icon" aria-hidden>
          ?
        </span>
        <span className="pause-kicker">Paused, nothing pressed · {where}</span>
      </div>
      <p className="pause-reason">{reason ?? "The job is paused. Nothing was pressed."}</p>

      {step && step.evidence.screen_text !== null && (
        <p className="pause-read">
          vision read <span className="mono pause-read-text">“{step.evidence.screen_text}”</span>
          <ConfidenceBadge confidence={step.evidence.screen_confidence} size="lg" />
        </p>
      )}

      {frameSrc ? (
        <figure className="pause-frame">
          {/* eslint-disable-next-line @next/next/no-img-element -- frames come from the backend at runtime */}
          <img src={frameSrc} alt="What the camera sees right now" />
          <figcaption>what the camera sees right now</figcaption>
        </figure>
      ) : (
        <p className="muted">No camera frame yet.</p>
      )}

      <div className="pause-actions">
        <button type="button" className="btn btn-resume" disabled={sending} onClick={resume}>
          {sending ? "Reading the screen again…" : "Resume: read the screen again"}
        </button>
      </div>
    </section>
  );
}
