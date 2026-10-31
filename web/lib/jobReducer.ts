// Applies backend events to a Job and keeps a human-readable action log.
// Pure on purpose (React may call reducers twice in dev).

import type { Action, Control, EventNote, Job, JobEvent, NoteKind, ReadConfidence, Step } from "@contract/types";

export type LogTone = "info" | "good" | "warn" | "bad" | "you";

export interface LogEntry {
  id: string;
  ts: string;
  tone: LogTone;
  text: string; // what happened, e.g. "Verified" or "Plan ready, 6 steps"
  // what the backend said this is (the event's note.kind), so each kind gets its own look.
  // missing = an older backend or a local note, the tone alone decides the look
  kind?: NoteKind;
  // the rest only shows up when the entry is about a step
  stepIndex?: number;
  stepIntent?: string;
  action?: string; // "Press ▲ ×12"
  screenText?: string | null; // what the camera read after
  screenConfidence?: ReadConfidence | null; // how sure that read is, null = no vision vote
  detail?: string; // one extra plain line, e.g. what the plan started from
  beforeFrame?: string | null; // evidence paths, as the backend sent them
  afterFrame?: string | null;
}

// how each note kind reads in the log. routine reads stay quiet so the rest stands out
const KIND_TONE: Record<NoteKind, LogTone> = {
  screen_read: "info",
  retry: "warn",
  read_unclear: "warn",
  resumed: "good",
  rejected: "bad",
  step_skipped: "bad",
  planner_refused: "bad",
};

export interface JobState {
  job: Job | null;
  log: LogEntry[];
}

export type JobMessage =
  | { kind: "reset"; job: Job }
  | { kind: "clear" } // no job (picked another device)
  | { kind: "sync"; job: Job; ts: string } // caught up after a reconnect: new job state, keep the log
  | { kind: "event"; event: JobEvent }
  | { kind: "note"; ts: string; tone: LogTone; text: string };

export const emptyJobState: JobState = { job: null, log: [] };

export function jobReducer(state: JobState, message: JobMessage): JobState {
  if (message.kind === "reset") {
    return { job: message.job, log: [] };
  }

  if (message.kind === "clear") {
    return emptyJobState;
  }

  if (message.kind === "note") {
    return addLog(state, { ts: message.ts, tone: message.tone, text: message.text });
  }

  if (message.kind === "sync") {
    if (!state.job || state.job.job_id !== message.job.job_id) return state;
    return addLog({ ...state, job: message.job }, { ts: message.ts, tone: "info", text: "Reconnected, caught up on missed events" });
  }

  // it's a backend event
  const event = message.event;
  if (!state.job) return state; // event for a job we don't know about yet, ignore
  if (event.job_id !== state.job.job_id) return state; // stale event from an older job

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
    case "job.status": {
      // pause_reason only means something while paused. an older backend sends no reason
      let pauseReason: string | null = null;
      if (event.status === "paused") pauseReason = event.reason ?? job.pause_reason ?? null;
      return { ...job, status: event.status, pause_reason: pauseReason, updated_at: event.ts };
    }

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
      return { ...job, status: event.result.outcome, result: event.result, pause_reason: null, updated_at: event.ts };

    default: {
      // if types.ts grows a new event type, this stops compiling until it's handled above
      const unhandled: never = event;
      console.warn("unhandled job event", unhandled);
      return job;
    }
  }
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

// "Press ▲ ×12", "Hold SET 3.0s", "Wait 5s", "Read Display"
// 1000 -> "1", 1500 -> "1.5": seconds with no trailing ".0", for "1 s" / "1.5 s"
export function formatSeconds(ms: number): string {
  return String(Number((ms / 1000).toFixed(1)));
}

export function describeAction(step: Step, controls: Control[]): string {
  const action = step.action;
  if (action.type === "wait") return `Wait ${action.seconds}s`;

  const control = findControl(controls, action.control_id);
  const label = control ? control.label : action.control_id;
  if (action.type === "read_screen") return `Read ${label}`;

  // same wording as the step list, so a long press doesn't read like a tap
  let text = `Press ${label}`;
  if (action.hold_ms) text = `Hold ${label} ${(action.hold_ms / 1000).toFixed(1)}s`;
  if (action.count > 1) text += ` ×${action.count}`;
  return text;
}

// what the log says while a step is underway: "Pressing", "Reading" or "Waiting"
function describeRunning(step: Step): string {
  if (step.action.type === "read_screen") return "Reading";
  if (step.action.type === "wait") return "Waiting";
  return "Pressing";
}

// what a retry did about it: "Pressed ▲ once more."
export function describeCorrection(correction: Action, controls: Control[]): string {
  if (correction.type === "wait") return `Waited ${correction.seconds}s more.`;
  const control = findControl(controls, correction.control_id);
  const name = control ? control.label : correction.control_id;
  if (correction.type === "read_screen") return `Read ${name} again.`;

  let times = `${correction.count} more times`;
  if (correction.count === 1) times = "once more";
  if (correction.count === 2) times = "twice more";
  return `Pressed ${name} ${times}.`;
}

// ---- helpers ----

function addLog(state: JobState, entry: Omit<LogEntry, "id">): JobState {
  const id = String(state.log.length);
  return { ...state, log: [...state.log, { ...entry, id }] };
}

// an entry straight from the backend's note: its words, its kind, and the read (with its
// confidence) when the note involves one
function fromNote(ts: string, note: EventNote, about: Partial<LogEntry> = {}): Omit<LogEntry, "id"> {
  const entry: Omit<LogEntry, "id"> = {
    ...about,
    ts,
    tone: KIND_TONE[note.kind] ?? "info", // a kind from a newer backend still gets an entry
    kind: note.kind,
    text: note.message,
  };
  if (note.read) {
    entry.screenText = note.read.text;
    entry.screenConfidence = note.read.confidence;
  }
  return entry;
}

function describeEvent(jobBefore: Job, event: JobEvent): Omit<LogEntry, "id"> | null {
  const ts = event.ts;
  const controls = jobBefore.device.controls;

  switch (event.type) {
    case "job.status":
      if (event.note) return fromNote(ts, event.note); // read_unclear, resumed
      if (event.status === "planning") return { ts, tone: "info", text: "Muse Spark is planning the steps" };
      if (event.status === "queued") return { ts, tone: "info", text: "Job queued" };
      if (event.status === "paused") {
        // the backend says why. an older one doesn't, and then all we know is it holds here
        return { ts, tone: "warn", text: event.reason ?? "Job paused, nothing was pressed." };
      }
      return null; // running / awaiting_approval are already covered by step entries

    case "plan.ready": {
      const entry: Omit<LogEntry, "id"> = {
        ts,
        tone: "info",
        text: `Plan ready, ${event.plan.steps.length} steps: ${event.plan.summary}`,
      };
      // what the plan started from: the read (with its badge), or the backend saying it couldn't
      // read the screen and planned from the profile's guess
      if (event.note) {
        entry.detail = event.note.message;
        if (event.note.read) {
          entry.screenText = event.note.read.text;
          entry.screenConfidence = event.note.read.confidence;
        }
      }
      return entry;
    }

    case "step.updated": {
      const step = event.step;
      const about = {
        ts,
        stepIndex: step.index,
        stepIntent: step.intent,
        action: describeAction(step, controls),
      };

      // the backend said what this is: use its words and kind
      if (event.note) {
        const entry = fromNote(ts, event.note, about);
        if (step.status === "succeeded" || step.status === "failed") {
          entry.beforeFrame = step.evidence.before_frame_url;
          entry.afterFrame = step.evidence.after_frame_url;
        }
        if (event.note.kind === "retry" && step.retries && step.retries.length > 0) {
          const latest = step.retries[step.retries.length - 1];
          entry.text = `Retry: ${latest.problem}. ${describeCorrection(latest.correction, controls)}`;
        }
        return entry;
      }

      // no note (an older backend): work out what changed by comparing with what we had
      const previous = findStep(jobBefore, step.step_id);
      const retriesBefore = previous && previous.retries ? previous.retries.length : 0;
      const retriesNow = step.retries ? step.retries.length : 0;
      if (retriesNow > retriesBefore && step.retries) {
        const latest = step.retries[step.retries.length - 1];
        return {
          ...about,
          tone: "warn",
          kind: "retry",
          text: `Retry: ${latest.problem}. ${describeCorrection(latest.correction, controls)}`,
          screenText: latest.screen_text,
          screenConfidence: latest.screen_confidence,
        };
      }

      if (previous && previous.status === step.status) return null; // no real change

      if (step.status === "running") return { ...about, tone: "info", text: describeRunning(step) };
      if (step.status === "verifying") return { ...about, tone: "info", text: "Reading the screen to check" };
      if (step.status === "succeeded") {
        return {
          ...about,
          tone: "good",
          text: retriesNow > 0 ? "Verified after retry" : "Verified",
          screenText: step.evidence.screen_text,
          screenConfidence: step.evidence.screen_confidence,
          beforeFrame: step.evidence.before_frame_url,
          afterFrame: step.evidence.after_frame_url,
        };
      }
      if (step.status === "awaiting_approval") {
        return { ...about, tone: "warn", text: "Needs approval before the finger moves" };
      }
      if (step.status === "failed") {
        return {
          ...about,
          tone: "bad",
          text: `Failed: ${step.error ?? "unknown error"}`,
          screenText: step.evidence.screen_text,
          screenConfidence: step.evidence.screen_confidence,
          beforeFrame: step.evidence.before_frame_url,
          afterFrame: step.evidence.after_frame_url,
        };
      }
      if (step.status === "skipped") {
        return { ...about, tone: "bad", text: step.error ? `Skipped: ${step.error}` : "Skipped" };
      }
      return null;
    }

    case "controls.updated":
      return { ts, tone: "info", text: `Controls re-detected (${event.controls.length} found, v${event.controls_version})` };

    case "frame":
      return null; // way too noisy for the log

    case "job.finished": {
      if (event.note) return fromNote(ts, event.note); // planner_refused
      const tone: LogTone = event.result.outcome === "succeeded" ? "good" : "bad";
      return { ts, tone, text: event.result.message };
    }

    default: {
      const unhandled: never = event; // same compile-time check as applyEvent
      return unhandled;
    }
  }
}

function findStep(job: Job, stepId: string): Step | null {
  if (!job.plan) return null;
  for (const step of job.plan.steps) {
    if (step.step_id === stepId) return step;
  }
  return null;
}
