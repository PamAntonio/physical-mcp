"use client";

// Everything that happened, newest on top. Step entries show the action, what the
// screen read afterwards (with vision's confidence badge), and before/after frames. Click a
// frame to see it big. Entries the backend labelled (note.kind) each get their own chip, icon and
// color, so a retry, a rejection, a refusal and an unclear read look different at a glance.
//
// It lives in a drawer opened from the top bar: a slide-over from the right on desktop, a bottom
// sheet on a phone. Not modal on purpose: it opens below the top bar and STOP has to keep
// working while it's open, so nothing here may make the rest of the page inert.

import { useEffect, useRef, useState } from "react";
import type { NoteKind } from "@contract/types";
import type { LogEntry } from "@/lib/jobReducer";
import ConfidenceBadge from "./ConfidenceBadge";

// shape as well as color, so they still differ in a grey video frame
const KIND_CHIPS: Record<NoteKind, { icon: string; label: string }> = {
  screen_read: { icon: "◦", label: "read" },
  retry: { icon: "↻", label: "retry" },
  read_unclear: { icon: "?", label: "unclear read" },
  resumed: { icon: "▶", label: "resumed" },
  rejected: { icon: "✕", label: "rejected" },
  step_skipped: { icon: "⤼", label: "skipped" },
  planner_refused: { icon: "⊘", label: "refused" },
};

interface Props {
  open: boolean;
  onClose: () => void;
  log: LogEntry[];
  frameUrl: (path: string | null) => string | null;
}

interface OpenFrame {
  src: string;
  caption: string;
}

function clockTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

export default function ActionLog({ open, onClose, log, frameUrl }: Props) {
  const [openFrame, setOpenFrame] = useState<OpenFrame | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Esc closes the big frame first, then the drawer
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (openFrame) setOpenFrame(null);
      else onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, openFrame, onClose]);

  // focus the X on open, hand focus back to whatever opened it (the top bar button) on close
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => {
      setOpenFrame(null);
      opener?.focus();
    };
  }, [open]);

  // newest first. explicit loop so we don't mutate the reducer's array
  const newestFirst: LogEntry[] = [];
  for (let i = log.length - 1; i >= 0; i--) {
    newestFirst.push(log[i]);
  }

  return (
    <>
      {/* clicking anywhere outside the drawer (below the top bar) closes it */}
      <div className={open ? "log-backdrop log-backdrop-open" : "log-backdrop"} onClick={onClose} aria-hidden />
      <section
        id="action-log"
        className={open ? "log-drawer log-drawer-open" : "log-drawer"}
        role="dialog"
        aria-modal="false"
        aria-labelledby="action-log-title"
        inert={!open}
      >
        <div className="log-drawer-head">
          <h2 id="action-log-title">
            Action log <span className="log-count">{log.length}</span>
          </h2>
          <button type="button" ref={closeRef} className="btn btn-small log-close" onClick={onClose} aria-label="Close the action log">
            ✕
          </button>
        </div>
        <div className="log-drawer-body">
          {log.length === 0 && <p className="muted">Nothing yet.</p>}

          <ol className="log" reversed>
            {newestFirst.map((entry) => {
              const before = frameUrl(entry.beforeFrame ?? null);
              const after = frameUrl(entry.afterFrame ?? null);
              const stepName = entry.stepIndex !== undefined ? `Step ${entry.stepIndex}` : null;

              return (
                <li key={entry.id} className={`log-entry log-${entry.tone}${entry.kind ? ` log-kind-${entry.kind}` : ""}`}>
                  <time dateTime={entry.ts}>{clockTime(entry.ts)}</time>

                  <span className="log-step" title={entry.stepIntent}>
                    {stepName ?? (entry.tone === "you" ? "You" : "Job")}
                  </span>

                  <div className="log-main">
                    <div className="log-text">
                      {entry.kind && KIND_CHIPS[entry.kind] && (
                        <span className={`log-kind log-kind-chip-${entry.kind}`}>
                          <span aria-hidden>{KIND_CHIPS[entry.kind].icon}</span> {KIND_CHIPS[entry.kind].label}
                        </span>
                      )}
                      {entry.text}
                    </div>
                    {(entry.action || entry.screenText || entry.detail) && (
                      <div className="log-detail">
                        {entry.detail && <span>{entry.detail}</span>}
                        {entry.action && <span>{entry.action}</span>}
                        {entry.screenText && (
                          <span className="log-read">
                            screen read <span className="log-screen">“{entry.screenText}”</span>
                            <ConfidenceBadge confidence={entry.screenConfidence} />
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  {(before || after) && (
                    <div className="log-frames">
                      {before && (
                        <Thumb
                          src={before}
                          label="before"
                          onOpen={() => setOpenFrame({ src: before, caption: `${stepName ?? ""} · before the press` })}
                        />
                      )}
                      {after && (
                        <Thumb
                          src={after}
                          label="after"
                          onOpen={() =>
                            setOpenFrame({
                              src: after,
                              caption: `${stepName ?? ""} · after${entry.screenText ? `, screen read “${entry.screenText}”` : ""}`,
                            })
                          }
                        />
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      </section>

      <FrameViewer frame={openFrame} onClose={() => setOpenFrame(null)} />
    </>
  );
}

function Thumb({ src, label, onOpen }: { src: string; label: string; onOpen: () => void }) {
  return (
    <button type="button" className="log-thumb" onClick={onOpen} aria-label={`Open the ${label} frame`}>
      {/* eslint-disable-next-line @next/next/no-img-element -- frames come from the backend at runtime */}
      <img src={src} alt="" />
      <span>{label}</span>
    </button>
  );
}

// Big view of one frame, over the drawer and under the top bar. Not a modal <dialog>: showModal()
// would make STOP inert while it's open. Esc is handled by the drawer above.
function FrameViewer({ frame, onClose }: { frame: OpenFrame | null; onClose: () => void }) {
  if (!frame) return null;
  return (
    <div
      className="frame-viewer"
      role="dialog"
      aria-label={frame.caption}
      onClick={(e) => {
        // clicking the dark backdrop (not the frame itself) closes it
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <figure>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={frame.src} alt={frame.caption} />
        <figcaption>
          <span>{frame.caption}</span>
          <button type="button" className="btn btn-small" onClick={onClose} autoFocus>
            Close
          </button>
        </figcaption>
      </figure>
    </div>
  );
}
