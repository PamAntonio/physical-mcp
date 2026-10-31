"use client";

import { useState } from "react";
import type { ApprovalMode } from "@contract/types";

interface Props {
  busy: boolean;
  devices: { id: string; name: string }[];
  deviceId: string;
  onDeviceChange: (deviceId: string) => void;
  defaultRequest: string; // starting text, matches the device (e.g. the Inkbird's 80 °F job)
  onSubmit: (request: string, approvalMode: ApprovalMode) => void;
}

export default function JobRequestForm({ busy, devices, deviceId, onDeviceChange, defaultRequest, onSubmit }: Props) {
  const [request, setRequest] = useState(defaultRequest);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>("risky_steps");

  return (
    <form
      className="request"
      onSubmit={(e) => {
        e.preventDefault();
        if (request.trim()) onSubmit(request.trim(), approvalMode);
      }}
    >
      <label htmlFor="job-request">What should the machine do?</label>
      <div className="request-row">
        {/* one finger, one job: no switching devices while a job is running */}
        <select aria-label="Device" value={deviceId} onChange={(e) => onDeviceChange(e.target.value)} disabled={busy}>
          {devices.map((device) => (
            <option key={device.id} value={device.id}>
              {device.name}
            </option>
          ))}
        </select>
        <input
          id="job-request"
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          disabled={busy}
          autoComplete="off"
        />
        <select
          aria-label="Approval mode"
          value={approvalMode}
          onChange={(e) => setApprovalMode(e.target.value as ApprovalMode)}
          disabled={busy}
        >
          <option value="risky_steps">Ask on risky steps</option>
          <option value="every_step">Ask on every step</option>
          <option value="plan">Approve the plan once</option>
        </select>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          Run job
        </button>
      </div>
    </form>
  );
}
