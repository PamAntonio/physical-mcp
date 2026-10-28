"use client";

// Camera view with the detected controls drawn as boxes on top.
// The SVG uses the camera's pixel size as its viewBox, so bbox values
// from SAM 3.1 line up without any scaling math.
// No real frame yet (mock mode)? We draw a fake panel from the boxes instead.

import { useState } from "react";
import type { Device, Step } from "@contract/types";

interface Props {
  device: Device;
  currentStep: Step | null;
  frameSrc: string | null;
  screenText: string | null;
}

export default function CameraView({ device, currentStep, frameSrc, screenText }: Props) {
  const [frameBroken, setFrameBroken] = useState(false);
  const { frame_width: width, frame_height: height } = device.camera;
  const showRealFrame = frameSrc !== null && !frameBroken;

  const targetId = currentStep && currentStep.action.type !== "wait" ? currentStep.action.control_id : null;
  const waiting = currentStep !== null && currentStep.status === "awaiting_approval";

  // box around all the controls, so the fake panel has an edge
  let minX = width;
  let minY = height;
  let maxX = 0;
  let maxY = 0;
  for (const control of device.controls) {
    const [x, y, w, h] = control.bbox;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x + w > maxX) maxX = x + w;
    if (y + h > maxY) maxY = y + h;
  }
  const pad = 40;

  // real frame: show the whole thing. mock: zoom in on the panel but keep the camera's aspect ratio
  let viewBox = `0 0 ${width} ${height}`;
  if (!showRealFrame && device.controls.length > 0) {
    const aspect = width / height;
    let boxW = (maxX - minX + pad * 2) * 1.3;
    let boxH = (maxY - minY + pad * 2) * 1.3;
    if (boxW / boxH < aspect) boxW = boxH * aspect;
    else boxH = boxW / aspect;
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    viewBox = `${centerX - boxW / 2} ${centerY - boxH / 2} ${boxW} ${boxH}`;
  }

  return (
    <figure className={showRealFrame ? "camera camera-live" : "camera camera-mock"}>
      <svg viewBox={viewBox} role="img" aria-label={`Camera view of ${device.name}`}>
        {showRealFrame ? (
          <image href={frameSrc!} x={0} y={0} width={width} height={height} onError={() => setFrameBroken(true)} />
        ) : (
          <>
            <rect x={-width} y={-height} width={width * 3} height={height * 3} className="cam-bg" />
            <rect
              x={minX - pad}
              y={minY - pad}
              width={maxX - minX + pad * 2}
              height={maxY - minY + pad * 2}
              rx={18}
              className="cam-panel"
            />
          </>
        )}

        {device.controls.map((control) => {
          const [x, y, w, h] = control.bbox;
          const isTarget = control.control_id === targetId;
          let className = "ctl";
          if (control.kind === "screen") className += " ctl-screen";
          if (control.risky) className += " ctl-risky";
          if (isTarget) className += waiting ? " ctl-waiting" : " ctl-target";

          return (
            <g key={control.control_id}>
              <rect x={x} y={y} width={w} height={h} rx={control.kind === "screen" ? 6 : 8} className={className} />

              {/* in mock mode draw the label inside, like a real panel */}
              {!showRealFrame && control.kind === "screen" && (
                <text x={x + w / 2} y={y + h / 2} className="ctl-screen-text">
                  {screenText ?? "----"}
                </text>
              )}
              {!showRealFrame && control.kind !== "screen" && (
                <text x={x + w / 2} y={y + h / 2} className="ctl-label">
                  {control.label}
                </text>
              )}

              {/* little tag with confidence above each box */}
              <text x={x} y={y - 6} className="ctl-tag">
                {control.kind === "screen" ? "screen" : control.label} {Math.round(control.confidence * 100)}%
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption>
        <span>{device.name}</span>
        <span>
          {showRealFrame ? "live frame" : "mock frame, drawn from detected controls"} · {device.controls.length} controls
          · v{device.controls_version}
        </span>
      </figcaption>
    </figure>
  );
}
