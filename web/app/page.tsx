"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import type { ApprovalMode, Device } from "@contract/types";
import { createBackend, type JobBackend } from "@/lib/backend";
import { sampleDevice, sampleJob } from "@/lib/mockBackend";
import { DEVICE_ID } from "@/lib/config";
import { emptyJobState, findCurrentStep, jobReducer } from "@/lib/jobReducer";
import CameraView from "@/components/CameraView";
import StepList from "@/components/StepList";
import ApprovalCard, { type DecisionInfo } from "@/components/ApprovalCard";
import ActionLog from "@/components/ActionLog";
import JobRequestForm from "@/components/JobRequestForm";

export default function Home() {
  const backendRef = useRef<JobBackend | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const [state, dispatch] = useReducer(jobReducer, emptyJobState);
  const [idleDevice, setIdleDevice] = useState<Device>(sampleDevice());
  const [isMock, setIsMock] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null); // clicked in the step list

  // backend reads window.location, so make it on the client only
  useEffect(() => {
    let cancelled = false; // dev mode runs this effect twice, don't let the first run leak a mock job
    const backend = createBackend();
    backendRef.current = backend;
    setIsMock(backend.isMock);
    backend
      .getDevice(DEVICE_ID)
      .then((device) => {
        if (!cancelled) setIdleDevice(device);
      })
      .catch((err) => setError(`Couldn't load the device: ${err.message}`));

    // in mock mode, kick off the sample job right away so the page opens on a live run
    if (backend.isMock) {
      const sample = sampleJob();
      backend
        .createJob({ device_id: sample.device.device_id, request: sample.request, approval_mode: sample.approval_mode })
        .then((newJob) => {
          if (cancelled) return;
          dispatch({ kind: "reset", job: newJob });
          setSelectedStepId(null);
          unsubscribeRef.current = backend.subscribe(newJob.job_id, (event) => dispatch({ kind: "event", event }));
        });
    }

    return () => {
      cancelled = true;
      if (unsubscribeRef.current) unsubscribeRef.current();
      unsubscribeRef.current = null;
    };
  }, []);

  const job = state.job;
  const device = job ? job.device : idleDevice;
  const currentStep = findCurrentStep(job);

  // the control of whichever step was clicked, so the camera can point at it
  let selectedControlId: string | null = null;
  if (job && job.plan && selectedStepId) {
    for (const step of job.plan.steps) {
      if (step.step_id === selectedStepId && step.action.type !== "wait") {
        selectedControlId = step.action.control_id;
      }
    }
  }
  const finished = job !== null && (job.status === "succeeded" || job.status === "failed" || job.status === "stopped");
  const busy = job !== null && !finished;

  // what the screen says right now: current step's reading, else the last one we got
  let screenText: string | null = null;
  if (job && job.plan) {
    for (const step of job.plan.steps) {
      if (step.evidence.screen_text) screenText = step.evidence.screen_text;
      if (step === currentStep) break;
    }
  }

  async function startJob(request: string, approvalMode: ApprovalMode) {
    const backend = backendRef.current;
    if (!backend) return;
    setError(null);
    if (unsubscribeRef.current) unsubscribeRef.current();
    try {
      const newJob = await backend.createJob({ device_id: DEVICE_ID, request, approval_mode: approvalMode });
      dispatch({ kind: "reset", job: newJob });
      setSelectedStepId(null);
      unsubscribeRef.current = backend.subscribe(newJob.job_id, (event) => dispatch({ kind: "event", event }));
    } catch (err) {
      setError(`Couldn't start the job: ${(err as Error).message}`);
    }
  }

  async function handleDecision(decision: "approve" | "reject", info: DecisionInfo) {
    const backend = backendRef.current;
    if (!backend || !job || !currentStep) return;
    const seconds = (info.timeToDecideMs / 1000).toFixed(1);
    const how = info.approvalType === "hold" ? "held to approve" : decision === "approve" ? "approved" : "rejected";
    dispatch({
      kind: "note",
      ts: new Date().toISOString(),
      tone: "you",
      text: `You ${how} step ${currentStep.index} after ${seconds}s`,
    });
    try {
      await backend.decide(job.job_id, currentStep.step_id, {
        decision,
        approval_type: info.approvalType, // contract v1 fields
        time_to_decide_ms: info.timeToDecideMs,
      });
    } catch (err) {
      setError(`Couldn't send your decision: ${(err as Error).message}`);
    }
  }

  async function stopJob() {
    const backend = backendRef.current;
    if (!backend || !job) return;
    try {
      await backend.stop(job.job_id);
    } catch (err) {
      setError(`Stop failed, pull the power: ${(err as Error).message}`);
    }
  }

  const frameSrc = backendRef.current ? backendRef.current.frameUrl(device.camera.latest_frame_url) : null;

  return (
    <main className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          Physical MCP
          {isMock && <span className="badge-mock">mock backend</span>}
        </div>
        <div className="topbar-right">
          {job && <span className={`status status-${job.status}`}>{job.status.replace("_", " ")}</span>}
          <button className="btn btn-stop" onClick={stopJob} disabled={!busy}>
            Stop
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <JobRequestForm busy={busy} onSubmit={startJob} />

      <div className="layout">
        <div className="col-main">
          <CameraView
            device={device}
            currentStep={currentStep}
            frameSrc={frameSrc}
            screenOverlay={isMock ? screenText : null}
            selectedControlId={selectedControlId}
          />
        </div>
        <div className="col-side">
          {currentStep && currentStep.status === "awaiting_approval" && (
            <ApprovalCard key={currentStep.step_id} step={currentStep} device={device} onDecide={handleDecision} />
          )}
          {job && job.result && (
            <section className={`panel result result-${job.result.outcome}`}>
              <h2>{job.result.outcome}</h2>
              <p>{job.result.message}</p>
            </section>
          )}
          <StepList
            steps={job && job.plan ? job.plan.steps : null}
            controls={device.controls}
            planning={job !== null && job.status === "planning"}
            selectedStepId={selectedStepId}
            onSelect={setSelectedStepId}
          />
        </div>
      </div>

      {/* full width along the bottom */}
      <div className="log-row">
        <ActionLog log={state.log} frameUrl={(path) => (backendRef.current ? backendRef.current.frameUrl(path) : null)} />
      </div>
    </main>
  );
}
