"use client";

// Press-and-hold approve button for high-risk steps, so they can't be rubber-stamped with
// one tap. Hold for holdMs (1.5s) and it fires; the fill shows how far along you are.
// Let go early and it resets to empty. Works with mouse, touch/pen and keyboard (hold Space or Enter).

import { useEffect, useRef, useState } from "react";

const DEFAULT_HOLD_MS = 1500;

interface Props {
  holdMs?: number;
  onComplete: () => void;
}

export default function HoldToApprove({ holdMs = DEFAULT_HOLD_MS, onComplete }: Props) {
  const [progress, setProgress] = useState(0);
  const [letGoEarly, setLetGoEarly] = useState(false); // show a hint after a short press
  const startedAt = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const done = useRef(false); // fire once, even if the hold keeps going

  function tick() {
    if (startedAt.current === null) return;
    const elapsed = performance.now() - startedAt.current;
    const fraction = Math.min(elapsed / holdMs, 1);
    setProgress(fraction);
    if (fraction >= 1) {
      startedAt.current = null;
      done.current = true;
      onComplete();
      return;
    }
    frame.current = requestAnimationFrame(tick);
  }

  function begin() {
    if (done.current || startedAt.current !== null) return;
    setLetGoEarly(false);
    startedAt.current = performance.now();
    frame.current = requestAnimationFrame(tick);
  }

  // let go (or slid off, or lost focus) before the fill was full: back to empty
  function release() {
    if (done.current || startedAt.current === null) return;
    startedAt.current = null;
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    setProgress(0);
    setLetGoEarly(true);
  }

  // stop the animation if the card disappears mid-hold
  useEffect(() => {
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, []);

  const seconds = (holdMs / 1000).toFixed(1);
  let label = `Hold ${seconds}s to approve`;
  if (progress > 0 && progress < 1) label = "Keep holding…";
  if (progress >= 1) label = "Approved";

  return (
    <div className="hold-wrap">
      <button
        type="button"
        className="btn btn-hold"
        style={{ ["--progress" as string]: progress }}
        aria-label={`Approve: press and hold for ${seconds} seconds`}
        onPointerDown={(e) => {
          if (e.button !== 0) return; // left click / touch / pen only
          // keep getting pointer events for this press, so a finger wobble doesn't count as letting go
          e.currentTarget.setPointerCapture(e.pointerId);
          begin();
        }}
        onPointerUp={release}
        onPointerCancel={release}
        onLostPointerCapture={release}
        onContextMenu={(e) => e.preventDefault()} // long-press on touch would open a menu
        onKeyDown={(e) => {
          if (e.key !== " " && e.key !== "Enter") return;
          e.preventDefault(); // no click, no page scroll
          if (!e.repeat) begin();
        }}
        onKeyUp={(e) => {
          if (e.key === " " || e.key === "Enter") release();
        }}
        onBlur={release}
      >
        <span className="btn-hold-text">{label}</span>
        {/* the fill carries its own dark copy of the label, so text stays readable on both sides of the edge */}
        <span className="btn-hold-fill" aria-hidden>
          {label}
        </span>
      </button>
      <span className="hold-hint" aria-live="polite">
        {letGoEarly ? `Let go too early. Hold for the full ${seconds}s.` : ""}
      </span>
    </div>
  );
}
