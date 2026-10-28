"use client";

// Camera frame with the detected controls drawn as boxes on top.
// bbox is [x, y, width, height] in camera pixels (1280x720 for the incubator), so each box
// is placed as a % of the frame size. The frame and the boxes scale together at any width,
// while labels keep a normal font size so they're still readable on a phone.

import { useEffect, useState } from "react";
import type { Control, Device, Step } from "@contract/types";

interface Props {
  device: Device;
  currentStep: Step | null;
  frameSrc: string | null;
  // mock frames are a still image, so we paint what the screen "says" on top. null = don't
  screenOverlay: string | null;
  selectedControlId: string | null; // picked in the step list
}

export default function CameraView({ device, currentStep, frameSrc, screenOverlay, selectedControlId }: Props) {
  const [showBoxes, setShowBoxes] = useState(true);
  const [frameBroken, setFrameBroken] = useState(false);

  // new frame url, give it another chance to load
  useEffect(() => {
    setFrameBroken(false);
  }, [frameSrc]);

  const frameWidth = device.camera.frame_width;
  const frameHeight = device.camera.frame_height;
  const hasFrame = frameSrc !== null && !frameBroken;

  // which control is the current step about to press (or read)?
  let targetId: string | null = null;
  if (currentStep && currentStep.action.type !== "wait") {
    targetId = currentStep.action.control_id;
  }
  const waitingForApproval = currentStep !== null && currentStep.status === "awaiting_approval";

  let targetLabel: string | null = null;
  for (const control of device.controls) {
    if (control.control_id === targetId) targetLabel = control.label;
  }

  return (
    <figure className="camera">
      <div className="camera-bar">
        <span className="camera-title">
          Camera · {device.camera.camera_id}
          {targetLabel && (
            <span className={waitingForApproval ? "camera-target camera-target-waiting" : "camera-target"}>
              {waitingForApproval ? `waiting on ${targetLabel}` : `→ ${targetLabel}`}
            </span>
          )}
        </span>
        <button
          type="button"
          className="btn btn-small"
          aria-pressed={showBoxes}
          onClick={() => setShowBoxes(!showBoxes)}
        >
          {showBoxes ? "Hide boxes" : "Show boxes"}
        </button>
      </div>

      {/* aspect-ratio comes from the camera, so the overlay lines up even before the image loads */}
      <div className="camera-frame" style={{ aspectRatio: `${frameWidth} / ${frameHeight}` }}>
        {hasFrame ? (
          // eslint-disable-next-line @next/next/no-img-element -- frames come from the backend at runtime
          <img
            src={frameSrc!}
            alt={`Camera view of ${device.name}`}
            className="camera-img"
            onError={() => setFrameBroken(true)}
          />
        ) : (
          <div className="camera-empty">No camera frame yet</div>
        )}

        {showBoxes && (
          <div className="camera-overlay" aria-hidden="true">
            {device.controls.map((control) => (
              <ControlBox
                key={control.control_id}
                control={control}
                frameWidth={frameWidth}
                frameHeight={frameHeight}
                isTarget={control.control_id === targetId}
                isSelected={control.control_id === selectedControlId}
                waitingForApproval={waitingForApproval}
                screenOverlay={screenOverlay}
                labelBelow={moreRoomBelow(control, device.controls, frameHeight)}
              />
            ))}
          </div>
        )}
      </div>

      <figcaption>
        <span>{device.name}</span>
        <span>
          {hasFrame ? "" : "no frame · "}
          {device.controls.length} controls · v{device.controls_version}
        </span>
      </figcaption>
    </figure>
  );
}

interface BoxProps {
  control: Control;
  frameWidth: number;
  frameHeight: number;
  isTarget: boolean;
  isSelected: boolean;
  waitingForApproval: boolean;
  screenOverlay: string | null;
  labelBelow: boolean;
}

// Labels go above the box by default. But if a neighbour sits right on top of it (bottom row of
// buttons) and there's more space underneath, put the label below so it doesn't cover that row.
function moreRoomBelow(control: Control, allControls: Control[], frameHeight: number): boolean {
  const [x, y, w, h] = control.bbox;
  let gapAbove = y; // distance to the top of the frame, unless something closer is above
  let gapBelow = frameHeight - (y + h);

  for (const other of allControls) {
    if (other.control_id === control.control_id) continue;
    const [otherX, otherY, otherW, otherH] = other.bbox;
    const overlapsSideways = otherX < x + w && otherX + otherW > x;
    if (!overlapsSideways) continue;

    if (otherY + otherH <= y) {
      const gap = y - (otherY + otherH);
      if (gap < gapAbove) gapAbove = gap;
    } else if (otherY >= y + h) {
      const gap = otherY - (y + h);
      if (gap < gapBelow) gapBelow = gap;
    }
  }
  // ~20 camera px is about where a label stops fitting between two rows
  const crampedAbove = gapAbove < 20;
  return crampedAbove && gapBelow > gapAbove;
}

function ControlBox({
  control,
  frameWidth,
  frameHeight,
  isTarget,
  isSelected,
  waitingForApproval,
  screenOverlay,
  labelBelow,
}: BoxProps) {
  const [x, y, w, h] = control.bbox;

  // camera pixels -> % of the frame, so the box stretches with the image
  const style = {
    left: `${(x / frameWidth) * 100}%`,
    top: `${(y / frameHeight) * 100}%`,
    width: `${(w / frameWidth) * 100}%`,
    height: `${(h / frameHeight) * 100}%`,
  };

  let className = "ctl";
  if (control.kind === "screen") className += " ctl-screen";
  if (control.risky) className += " ctl-risky";
  if (control.reachable === false) className += " ctl-unreachable";
  if (isTarget) {
    // amber while it waits for a human, blue while the finger is on it
    className += waitingForApproval ? " ctl-waiting" : " ctl-target";
  }
  if (isSelected) className += " ctl-selected";

  const confidence = Math.round(control.confidence * 100);

  return (
    <div className={className} style={style}>
      <span className={labelBelow ? "ctl-tag ctl-tag-below" : "ctl-tag"}>
        {control.label}
        <span className="ctl-conf"> {confidence}%</span>
      </span>
      {control.kind === "screen" && screenOverlay && <span className="ctl-screen-text">{screenOverlay}</span>}
    </div>
  );
}
