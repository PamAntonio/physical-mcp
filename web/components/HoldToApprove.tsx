"use client";

// PARKED, not used anywhere yet. Press-and-hold approve button for high-risk steps,
// so they can't be rubber-stamped with one tap. Moved out of ApprovalCard so the card
// can stay a shell until risk tiers come from the scorer. See the TODO in ApprovalCard.

import { useEffect, useRef, useState } from "react";

const DEFAULT_HOLD_MS = 1500;

interface Props {
  holdMs?: number;
  onComplete: () => void;
}

export default function HoldToApprove({ holdMs = DEFAULT_HOLD_MS, onComplete }: Props) {
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
      type="button"
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
