"use client";

// Everything that happened, newest on top. Step entries show the action, what the
// screen read afterwards (with vision's confidence badge), and before/after frames. Click a
// frame to see it big. Entries the backend labelled (note.kind) each get their own chip, icon and
// color, so a retry, a rejection, a refusal and an unclear read look different at a glance.

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

export default function ActionLog({ log, frameUrl }: Props) {
  const [openFrame, setOpenFrame] = useState<OpenFrame | null>(null);

  // newest first. explicit loop so we don't mutate the reducer's array
  const newestFirst: LogEntry[] = [];
  for (let i = log.length - 1; i >= 0; i--) {
    newestFirst.push(log[i]);
  }

  return (
    <section className="panel log-panel">
      <h2>
        Action log <span className="log-count">{log.length}</span>
      </h2>
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

      <FrameViewer frame={openFrame} onClose={() => setOpenFrame(null)} />
    </section>
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

// Big view of one frame. <dialog> gives us Esc to close and focus handling for free.
function FrameViewer({ frame, onClose }: { frame: OpenFrame | null; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (frame && !dialog.open) dialog.showModal();
    if (!frame && dialog.open) dialog.close();
  }, [frame]);

  return (
    <dialog
      ref={dialogRef}
      className="frame-viewer"
      onClose={onClose}
      onClick={(e) => {
        // clicking the dark backdrop (the dialog itself, not its contents) closes it
        if (e.target === dialogRef.current) onClose();
      }}
    >
      {frame && (
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
      )}
    </dialog>
  );
}
