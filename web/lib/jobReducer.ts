// Applies backend events to a Job and keeps a human-readable action log.
// Pure on purpose (React may call reducers twice in dev).

import type { Control, Job, JobEvent, Step } from "./types";

export type LogTone = "info" | "good" | "warn" | "bad" | "you";

export interface LogEntry {
  id: string;
  ts: string;
  tone: LogTone;
  text: string;
  step?: Step; // attached when the entry is about a finished step, so we can show evidence
}

export interface JobState {
  job: Job | null;
  log: LogEntry[];
}

export type JobMessage =
  | { kind: "reset"; job: Job }
  | { kind: "event"; event: JobEvent }
  | { kind: "note"; ts: string; tone: LogTone; text: string };

export const emptyJobState: JobState = { job: null, log: [] };

export function jobReducer(state: JobState, message: JobMessage): JobState {
  if (message.kind === "reset") {
    return { job: message.job, log: [] };
  }

  if (message.kind === "note") {
    return addLog(state, { ts: message.ts, tone: message.tone, text: message.text });
  }

  // it's a backend event
  const event = message.event;
  if (!state.job) return state; // event for a job we don't know about yet, ignore

  const nextJob = applyEvent(state.job, event);
  const entry = describeEvent(state.job, event);

  let nextState: JobState = { ...state, job: nextJob };
  if (entry) {
    nextState = addLog(nextState, entry);
  }
  return nextState;
}

export function applyEvent(job: Job, event: JobEvent): Job {
  switch (event.type) {
    case "job.status":
      return { ...job, status: event.status, updated_at: event.ts };

    case "plan.ready":
      return { ...job, plan: event.plan, updated_at: event.ts };

    case "step.updated": {
      if (!job.plan) return job; // a step before the plan exists shouldn't happen
      const newSteps: Step[] = [];
      let found = false;
      for (const step of job.plan.steps) {
        if (step.step_id === event.step.step_id) {
          newSteps.push(event.step);
          found = true;
        } else {
          newSteps.push(step);
        }
      }
      if (!found) newSteps.push(event.step); // planner added a step mid-run
      return { ...job, plan: { ...job.plan, steps: newSteps }, updated_at: event.ts };
    }

    case "controls.updated":
      return {
        ...job,
        device: { ...job.device, controls_version: event.controls_version, controls: event.controls },
      };

    case "frame":
      return {
        ...job,
        device: { ...job.device, camera: { ...job.device.camera, latest_frame_url: event.frame_url } },
      };

    case "job.finished":
      return { ...job, status: event.result.outcome, result: event.result, updated_at: event.ts };
  }
  return job;
}

// The step the machine is busy with right now (or waiting on), if any
export function findCurrentStep(job: Job | null): Step | null {
  if (!job || !job.plan) return null;
  // once the job is over nothing is "current", even if the backend forgot to update a step
  if (job.status === "succeeded" || job.status === "failed" || job.status === "stopped") return null;
  for (const step of job.plan.steps) {
    if (step.status === "running" || step.status === "verifying" || step.status === "awaiting_approval") {
      return step;
    }
  }
  return null;
}

export function findControl(controls: Control[], controlId: string): Control | null {
  for (const control of controls) {
    if (control.control_id === controlId) return control;
  }
  return null;
}

// "Press ▲ ×12", "Wait 5s", "Read Display"
export function describeAction(step: Step, controls: Control[]): string {
  const action = step.action;
  if (action.type === "wait") return `Wait ${action.seconds}s`;

  const control = findControl(controls, action.control_id);
  const label = control ? control.label : action.control_id;
  if (action.type === "read_screen") return `Read ${label}`;
  if (action.count > 1) return `Press ${label} ×${action.count}`;
  return `Press ${label}`;
}

// ---- helpers ----

function addLog(state: JobState, entry: Omit<LogEntry, "id">): JobState {
  const id = String(state.log.length);
  return { ...state, log: [...state.log, { ...entry, id }] };
}

function describeEvent(jobBefore: Job, event: JobEvent): Omit<LogEntry, "id"> | null {
  const ts = event.ts;
  const controls = jobBefore.device.controls;

  switch (event.type) {
    case "job.status":
      if (event.status === "planning") return { ts, tone: "info", text: "Muse Spark is planning the steps" };
      if (event.status === "queued") return { ts, tone: "info", text: "Job queued" };
      return null; // running / awaiting_approval are already covered by step entries

    case "plan.ready":
      return {
        ts,
        tone: "info",
        text: `Plan ready, ${event.plan.steps.length} steps: ${event.plan.summary}`,
      };

    case "step.updated": {
      const step = event.step;
      const prefix = `Step ${step.index}`;
      const actionText = describeAction(step, controls);

      // was this a retry? compare with what we had before
      const previous = findStep(jobBefore, step.step_id);
      const retriesBefore = previous && previous.retries ? previous.retries.length : 0;
      const retriesNow = step.retries ? step.retries.length : 0;
      if (retriesNow > retriesBefore && step.retries) {
        const latest = step.retries[step.retries.length - 1];
        const fakeStep = { ...step, action: latest.correction };
        return {
          ts,
          tone: "warn",
          text: `${prefix} self-corrected: ${latest.problem}. ${describeAction(fakeStep, controls)}`,
        };
      }

      if (previous && previous.status === step.status) return null; // no real change

      if (step.status === "running") return { ts, tone: "info", text: `${prefix}: ${actionText}` };
      if (step.status === "verifying") return { ts, tone: "info", text: `${prefix}: reading the screen to check` };
      if (step.status === "succeeded") {
        const screen = step.evidence.screen_text ? `, screen reads "${step.evidence.screen_text}"` : "";
        return { ts, tone: "good", text: `${prefix} verified${screen}`, step };
      }
      if (step.status === "awaiting_approval") {
        return { ts, tone: "warn", text: `${prefix} needs approval before the finger moves: ${actionText}` };
      }
      if (step.status === "failed") {
        return { ts, tone: "bad", text: `${prefix} failed: ${step.error ?? "unknown error"}`, step };
      }
      if (step.status === "skipped") return { ts, tone: "bad", text: `${prefix} skipped` };
      return null;
    }

    case "controls.updated":
      return { ts, tone: "info", text: `Controls re-detected (${event.controls.length} found, v${event.controls_version})` };

    case "frame":
      return null; // way too noisy for the log

    case "job.finished": {
      const tone: LogTone = event.result.outcome === "succeeded" ? "good" : "bad";
      return { ts, tone, text: event.result.message };
    }
  }
  return null;
}

function findStep(job: Job, stepId: string): Step | null {
  if (!job.plan) return null;
  for (const step of job.plan.steps) {
    if (step.step_id === stepId) return step;
  }
  return null;
}
