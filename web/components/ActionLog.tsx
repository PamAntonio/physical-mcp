"use client";

// Everything that happened, newest on top. Step entries show the action, what the
// screen read afterwards, and before/after frames. Click a frame to see it big.

import { useEffect, useRef, useState } from "react";
import type { LogEntry } from "@/lib/jobReducer";

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
            <li key={entry.id} className={`log-entry log-${entry.tone}`}>
              <time dateTime={entry.ts}>{clockTime(entry.ts)}</time>

              <span className="log-step" title={entry.stepIntent}>
                {stepName ?? (entry.tone === "you" ? "You" : "Job")}
              </span>

              <div className="log-main">
                <div className="log-text">{entry.text}</div>
                {(entry.action || entry.screenText) && (
                  <div className="log-detail">
                    {entry.action && <span>{entry.action}</span>}
                    {entry.screenText && (
                      <span>
                        screen read <span className="log-screen">“{entry.screenText}”</span>
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
