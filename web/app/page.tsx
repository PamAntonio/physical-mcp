"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import type { ApprovalMode, Device } from "@/lib/types";
import { createBackend, type JobBackend } from "@/lib/backend";
import { sampleDevice } from "@/lib/mockBackend";
import { emptyJobState, findCurrentStep, jobReducer } from "@/lib/jobReducer";
import CameraView from "@/components/CameraView";
import StepList from "@/components/StepList";
import ApprovalCard, { type DecisionInfo } from "@/components/ApprovalCard";
import ActionLog from "@/components/ActionLog";
import JobRequestForm from "@/components/JobRequestForm";

const DEVICE_ID = process.env.NEXT_PUBLIC_DEVICE_ID ?? "incubator-01";

export default function Home() {
  const backendRef = useRef<JobBackend | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const [state, dispatch] = useReducer(jobReducer, emptyJobState);
  const [idleDevice, setIdleDevice] = useState<Device>(sampleDevice());
  const [isMock, setIsMock] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // backend reads window.location, so make it on the client only
  useEffect(() => {
    const backend = createBackend();
    backendRef.current = backend;
    setIsMock(backend.isMock);
    backend
      .getDevice(DEVICE_ID)
      .then(setIdleDevice)
      .catch((err) => setError(`Couldn't load the device: ${err.message}`));
    return () => {
      if (unsubscribeRef.current) unsubscribeRef.current();
    };
  }, []);

  const job = state.job;
  const device = job ? job.device : idleDevice;
  const currentStep = findCurrentStep(job);
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
      // TODO: send approval_type + time_to_decide_ms once Pam adds them to ApprovalDecision
      await backend.decide(job.job_id, currentStep.step_id, { decision });
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
          <CameraView device={device} currentStep={currentStep} frameSrc={frameSrc} screenText={screenText} />
          <ActionLog log={state.log} frameUrl={(path) => (backendRef.current ? backendRef.current.frameUrl(path) : null)} />
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
          />
        </div>
      </div>
    </main>
  );
}
