"use client";

import { useEffect, useRef } from "react";
import type { LogEntry } from "@/lib/jobReducer";

interface Props {
  log: LogEntry[];
  frameUrl: (path: string | null) => string | null;
}

function clockTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

export default function ActionLog({ log, frameUrl }: Props) {
  const bottom = useRef<HTMLDivElement>(null);

  // keep the newest entry in view
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [log.length]);

  return (
    <section className="panel log-panel">
      <h2>Action log</h2>
      {log.length === 0 && <p className="muted">Nothing yet.</p>}
      <ul className="log">
        {log.map((entry) => {
          const before = entry.step ? frameUrl(entry.step.evidence.before_frame_url) : null;
          const after = entry.step ? frameUrl(entry.step.evidence.after_frame_url) : null;
          return (
            <li key={entry.id} className={`log-entry log-${entry.tone}`}>
              <time>{clockTime(entry.ts)}</time>
              <div>
                <div>{entry.text}</div>
                {(before || after) && (
                  <div className="evidence">
                    {before && (
                      <figure>
                        <img src={before} alt="before the press" />
                        <figcaption>before</figcaption>
                      </figure>
                    )}
                    {after && (
                      <figure>
                        <img src={after} alt="after the press" />
                        <figcaption>after</figcaption>
                      </figure>
                    )}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div ref={bottom} />
    </section>
  );
}
