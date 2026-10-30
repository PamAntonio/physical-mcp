"use client";

// Click a device's 4 panel corners on a camera frame, see where the control boxes land, save.
// The math and the files live in backend/calibrate.py, which runs a little API on ?api=
// (default http://127.0.0.1:8765). Start it first: python backend/calibrate.py <frame> <device>

import { useEffect, useRef, useState } from "react";
import type { BBox } from "@contract/types";

type Point = [number, number];

interface Session {
  device_id: string;
  mode: "reference" | "calibrate";
  frame_name: string;
  frame_url: string;
  frame_size: [number, number];
  corner_names: string[];
  current_boxes: Record<string, BBox>;
  current_frame_size: [number, number] | null;
}

interface Preview {
  boxes: Record<string, BBox>;
  panel: Point[];
}

interface Saved {
  saved: string;
  backup: string | null;
  picture: string | null;
}

const DEFAULT_API = "http://127.0.0.1:8765";

export default function CalibratePage() {
  const [api, setApi] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [corners, setCorners] = useState<Point[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [saved, setSaved] = useState<Saved | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const frameRef = useRef<HTMLDivElement | null>(null);

  // ?api= comes from the url, so read it on the client
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("api");
    const base = (fromUrl || DEFAULT_API).replace(/\/$/, "");
    setApi(base);
    fetch(`${base}/session`)
      .then((response) => response.json())
      .then((data: Session) => setSession(data))
      .catch(() =>
        setError(`Can't reach calibrate.py at ${base}. Start it first: python backend/calibrate.py <frame> <device>`),
      );
  }, []);

  async function post(path: string, points: Point[]) {
    const response = await fetch(`${api}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ corners: points }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  // the 4th click asks for the new boxes right away
  async function showPreview(points: Point[]) {
    setBusy(true);
    try {
      setPreview((await post("/preview", points)) as Preview);
      setError(null);
    } catch (err) {
      setPreview(null);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function handleClick(event: React.MouseEvent<HTMLDivElement>) {
    if (!session || !frameRef.current || corners.length >= 4 || saved) return;
    // page pixels -> frame pixels
    const rect = frameRef.current.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * session.frame_size[0];
    const y = ((event.clientY - rect.top) / rect.height) * session.frame_size[1];
    const next: Point[] = [...corners, [Math.round(x), Math.round(y)]];
    setCorners(next);
    if (next.length === 4) showPreview(next);
  }

  function undo() {
    setCorners(corners.slice(0, -1));
    setPreview(null);
    setError(null);
  }

  function redo() {
    setCorners([]);
    setPreview(null);
    setError(null);
  }

  async function save() {
    setBusy(true);
    try {
      setSaved((await post("/save", corners)) as Saved);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!session) {
    return (
      <main className="app">
        <h1 style={{ fontSize: 20 }}>Calibrate</h1>
        <p>{error || "Loading…"}</p>
      </main>
    );
  }

  const [frameWidth, frameHeight] = session.frame_size;
  const nextCorner = corners.length < 4 ? session.corner_names[corners.length] : null;
  // labels and dots in frame pixels, sized so they read the same at any frame size
  const textSize = frameWidth / 70;
  const dotSize = frameWidth / 220;
  const sameSizeAsCurrent =
    session.current_frame_size !== null &&
    session.current_frame_size[0] === frameWidth &&
    session.current_frame_size[1] === frameHeight;

  let instruction: string;
  if (saved) instruction = "Saved.";
  else if (nextCorner) instruction = `Click the panel's ${nextCorner} corner (${corners.length + 1} of 4).`;
  else if (busy) instruction = "Working out the boxes…";
  else if (preview) instruction = "Check the green boxes. Save if they sit on the controls, Redo if not.";
  else instruction = "Those corners didn't work, Redo.";

  return (
    <main className="app">
      <header className="topbar">
        <div className="brand">
          Calibrate · {session.device_id}
          <span className="badge-mock">{session.mode === "reference" ? "making the reference" : "new camera view"}</span>
        </div>
        <div className="topbar-right" style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-small" onClick={undo} disabled={corners.length === 0 || !!saved || busy}>
            Undo
          </button>
          <button className="btn btn-small" onClick={redo} disabled={corners.length === 0 || !!saved || busy}>
            Redo
          </button>
          <button className="btn btn-small" onClick={save} disabled={!preview || !!saved || busy}>
            Save
          </button>
        </div>
      </header>

      <p style={{ margin: "12px 0" }}>
        <strong>{instruction}</strong>{" "}
        <span style={{ opacity: 0.7 }}>
          {session.frame_name} · {frameWidth}x{frameHeight}
          {session.mode === "reference"
            ? " · click the corners on the frame the current boxes were made on"
            : " · grey dashed = the boxes before"}
        </span>
      </p>
      {error && <div className="error-banner">{error}</div>}
      {saved && (
        <div className="panel" style={{ marginBottom: 12 }}>
          Saved {saved.saved}
          {saved.backup && <> · old file backed up to {saved.backup}</>}
          {saved.picture && <> · picture at {saved.picture}</>}. You can close this tab.
        </div>
      )}

      <div
        ref={frameRef}
        onClick={handleClick}
        style={{
          position: "relative",
          width: "100%",
          aspectRatio: `${frameWidth} / ${frameHeight}`,
          cursor: corners.length < 4 && !saved ? "crosshair" : "default",
          background: "#000",
          userSelect: "none",
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- the frame comes from calibrate.py at runtime */}
        <img
          src={session.frame_url}
          alt={`Frame ${session.frame_name}`}
          draggable={false}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}
        />
        <svg
          viewBox={`0 0 ${frameWidth} ${frameHeight}`}
          preserveAspectRatio="none"
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
        >
          {/* the boxes as they are now, faint, for comparison */}
          {session.mode === "calibrate" &&
            sameSizeAsCurrent &&
            Object.entries(session.current_boxes).map(([controlId, [x, y, w, h]]) => (
              <rect
                key={`old-${controlId}`}
                x={x}
                y={y}
                width={w}
                height={h}
                fill="none"
                stroke="#9aa3ab"
                strokeDasharray="6 4"
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
              />
            ))}

          {/* the panel outline as clicked so far */}
          {corners.length > 1 && (
            <polyline
              points={(corners.length === 4 ? [...corners, corners[0]] : corners).map((point) => point.join(",")).join(" ")}
              fill="none"
              stroke="#5cc8ff"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          )}
          {corners.map(([x, y], index) => (
            <g key={`corner-${index}`}>
              <circle cx={x} cy={y} r={dotSize} fill="#5cc8ff" />
              <text x={x + dotSize * 1.5} y={y - dotSize * 1.5} fill="#5cc8ff" fontSize={textSize}>
                {session.corner_names[index]}
              </text>
            </g>
          ))}

          {/* where the boxes land */}
          {preview &&
            Object.entries(preview.boxes).map(([controlId, [x, y, w, h]]) => (
              <g key={`new-${controlId}`}>
                <rect
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  fill="rgba(109,255,158,0.12)"
                  stroke="#6dff9e"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                />
                <text x={x} y={y + h + textSize} fill="#6dff9e" fontSize={textSize * 0.8}>
                  {controlId}
                </text>
              </g>
            ))}
        </svg>
      </div>
    </main>
  );
}
