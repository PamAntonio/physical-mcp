"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import type { ApprovalDecision, ApprovalMode, Control, Device, RejectBehavior, Step } from "@contract/types";
import { createBackend, type ConnectionState, type JobBackend } from "@/lib/backend";
import { sampleDevice, sampleJob } from "@/lib/mockBackend";
import { AUTO_START_SAMPLE, DEVICE_ID, DEVICES, OPERATOR_NAME, REJECT_BEHAVIOR } from "@/lib/config";
import { emptyJobState, findControl, findCurrentStep, jobReducer } from "@/lib/jobReducer";
import { riskFromStep } from "@/lib/risk";
import CameraView from "@/components/CameraView";
import StepList from "@/components/StepList";
import ApprovalCard, { type DecisionInfo } from "@/components/ApprovalCard";
import ActionLog from "@/components/ActionLog";
import JobRequestForm from "@/components/JobRequestForm";

const REJECT_LOG_SUFFIX: Record<RejectBehavior, string> = {
  abort: ", stopping the job",
  pause: ", job paused",
  replan: ", replan requested",
};

// what the log calls a step: the button's label ("START"), or the intent if no button
function stepTargetName(step: Step, controls: Control[]): string {
  if (step.action.type === "press") {
    const control = findControl(controls, step.action.control_id);
    if (control) return control.label;
  }
  return `step ${step.index} (${step.intent})`;
}

export default function Home() {
  const backendRef = useRef<JobBackend | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const [state, dispatch] = useReducer(jobReducer, emptyJobState);
  const [deviceId, setDeviceId] = useState(DEVICE_ID); // picked next to the request box
  const [idleDevice, setIdleDevice] = useState<Device>(sampleDevice(DEVICE_ID));
  const pickedRef = useRef(DEVICE_ID); // so a slow getDevice for the old pick can't win
  const [isMock, setIsMock] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null); // clicked in the step list
  const [connection, setConnection] = useState<ConnectionState | null>(null);

  // one place that wires a job's event stream into the reducer
  function listenTo(backend: JobBackend, jobId: string) {
    unsubscribeRef.current = backend.subscribe(jobId, {
      onEvent: (event) => dispatch({ kind: "event", event }),
      onSync: (job) => dispatch({ kind: "sync", job, ts: new Date().toISOString() }),
      onConnection: setConnection,
    });
  }

  // backend reads window.location, so make it on the client only
  useEffect(() => {
    let cancelled = false; // dev mode runs this effect twice, don't let the first run leak a mock job
    const backend = createBackend();
    backendRef.current = backend;
    setIsMock(backend.isMock);
    backend
      .getDevice(DEVICE_ID)
      .then((device) => {
        if (!cancelled && pickedRef.current === DEVICE_ID) setIdleDevice(device);
      })
      .catch((err) => setError(`Couldn't load the device: ${err.message}`));

    // on mock data, kick off the sample job right away so the page opens on a live run
    if (AUTO_START_SAMPLE) {
      const sample = sampleJob(DEVICE_ID);
      backend
        .createJob({ device_id: sample.device.device_id, request: sample.request, approval_mode: sample.approval_mode })
        .then((newJob) => {
          if (cancelled) return;
          dispatch({ kind: "reset", job: newJob });
          setSelectedStepId(null);
          listenTo(backend, newJob.job_id);
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
      const newJob = await backend.createJob({ device_id: deviceId, request, approval_mode: approvalMode });
      dispatch({ kind: "reset", job: newJob });
      setSelectedStepId(null);
      listenTo(backend, newJob.job_id);
    } catch (err) {
      setError(`Couldn't start the job: ${(err as Error).message}`);
    }
  }

  // switch devices: drop the old (finished) job and show the new device's panel
  function pickDevice(newDeviceId: string) {
    const backend = backendRef.current;
    if (busy || newDeviceId === deviceId) return;
    if (unsubscribeRef.current) unsubscribeRef.current();
    unsubscribeRef.current = null;
    pickedRef.current = newDeviceId;
    setDeviceId(newDeviceId);
    dispatch({ kind: "clear" });
    setSelectedStepId(null);
    setError(null);
    setIdleDevice(sampleDevice(newDeviceId)); // something to show until the backend answers
    if (!backend) return;
    backend
      .getDevice(newDeviceId)
      .then((device) => {
        if (pickedRef.current === newDeviceId) setIdleDevice(device);
      })
      .catch((err) => setError(`Couldn't load the device: ${err.message}`));
  }

  async function handleDecision(decision: "approve" | "reject", info: DecisionInfo) {
    const backend = backendRef.current;
    if (!backend || !job || !currentStep) return;
    const decidedAt = new Date().toISOString();

    // "You approved START (hold, 2.1s)" / "You rejected START (tap, 1.4s), job paused"
    const seconds = (info.timeToDecideMs / 1000).toFixed(1);
    const what = stepTargetName(currentStep, device.controls);
    let text = `You ${decision === "approve" ? "approved" : "rejected"} ${what} (${info.approvalType}, ${seconds}s)`;
    if (decision === "reject") text += REJECT_LOG_SUFFIX[REJECT_BEHAVIOR];
    dispatch({ kind: "note", ts: decidedAt, tone: "you", text });

    const payload: ApprovalDecision = {
      decision,
      decided_by: OPERATOR_NAME,
      decided_at: decidedAt,
      approval_type: info.approvalType,
      time_to_decide_ms: info.timeToDecideMs,
    };
    if (decision === "reject") payload.on_reject = REJECT_BEHAVIOR;
    try {
      await backend.decide(job.job_id, currentStep.step_id, payload);
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
          {!isMock && connection && connection !== "closed" && (
            <span className={`badge-conn badge-conn-${connection}`}>
              {connection === "live" ? "live" : `${connection}…`}
            </span>
          )}
        </div>
        <div className="topbar-right">
          {job && <span className={`status status-${job.status}`}>{job.status.replace("_", " ")}</span>}
          <button className="btn btn-stop" onClick={stopJob} disabled={!busy}>
            Stop
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      {/* the device's sample request: the 80 °F heater job for inkbird-01, T1 for 2 hours for
          timer-01, the incubator one otherwise. keyed on the device so the text swaps with it */}
      <JobRequestForm
        key={deviceId}
        busy={busy}
        devices={DEVICES}
        deviceId={deviceId}
        onDeviceChange={pickDevice}
        defaultRequest={sampleJob(deviceId).request}
        onSubmit={startJob}
      />

      <div className="layout">
        <div className="col-main">
          <CameraView
            device={device}
            currentStep={currentStep}
            frameSrc={frameSrc}
            live={!finished}
            screenOverlay={isMock ? screenText : null}
            selectedControlId={selectedControlId}
          />
        </div>
        <div className="col-side">
          {currentStep && currentStep.status === "awaiting_approval" && (
            <ApprovalCard
              key={currentStep.step_id}
              step={currentStep}
              device={device}
              risk={riskFromStep(currentStep)}
              paused={job !== null && job.status === "paused"}
              rejectBehavior={REJECT_BEHAVIOR}
              onDecide={handleDecision}
            />
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
