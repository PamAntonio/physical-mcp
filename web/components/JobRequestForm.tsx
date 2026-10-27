"use client";

import { useState } from "react";
import type { ApprovalMode } from "@/lib/types";

interface Props {
  busy: boolean;
  onSubmit: (request: string, approvalMode: ApprovalMode) => void;
}

export default function JobRequestForm({ busy, onSubmit }: Props) {
  const [request, setRequest] = useState("Set the incubator to 37 °C and run it for 2 hours");
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
