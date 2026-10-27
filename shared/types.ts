// Physical MCP: rough contract between the backend and the web app.
// Draft v1, 2026-10-27. Field names may change; job.sample.json matches these types.
// Draft v1 adds (all optional, nothing existing breaks): Control.reachable,
// press hold_ms, Step.max_attempts, Job.press_budget,
// ApprovalDecision.approval_type / time_to_decide_ms, Control kind "indicator",
// and Expectation.indicator_on.
//
// The web app never speaks MCP. MCP is how the planner (Muse Spark) drives the
// machine through backend tools such as get_controls, press, read_screen.
// The web app talks to the backend over plain HTTP + a WebSocket:
//
//   POST /jobs                         body: CreateJobRequest  -> Job
//   GET  /jobs/:job_id                                         -> Job
//   POST /jobs/:job_id/steps/:step_id/approval  body: ApprovalDecision -> Job
//   POST /jobs/:job_id/stop            immediate stop: servo retracts, job -> "stopped"
//   GET  /devices/:device_id                                   -> Device (with controls)
//   WS   /jobs/:job_id/events          stream of JobEvent

export type JobStatus =
  | "queued"
  | "planning"
  | "running"
  | "awaiting_approval"
  | "succeeded"
  | "failed"
  | "stopped";

export type StepStatus =
  | "pending"
  | "awaiting_approval"
  | "running"
  | "verifying" // pressed; reading the screen to check the result
  | "succeeded"
  | "failed"
  | "skipped"; // e.g. rejected at approval

// "risky_steps": pause only on steps marked requires_approval (default)
// "every_step":  pause before every step
// "plan":        approve the whole plan once, then run
export type ApprovalMode = "risky_steps" | "every_step" | "plan";

// Pixel box in the camera frame: [x, y, width, height], origin top-left.
export type BBox = [number, number, number, number];

export interface Control {
  control_id: string;
  label: string; // text read from the panel, or a symbol like "▲"
  kind: "button" | "screen" | "knob" | "switch" | "indicator"; // "indicator": a status light
  bbox: BBox; // from SAM 3.1 segmentation
  confidence: number; // 0..1
  risky?: boolean; // pressing it changes the machine's state (start, eject, ...)
  reachable?: boolean; // false = the finger can't press it on this rig
}

export interface Camera {
  camera_id: string;
  frame_width: number;
  frame_height: number;
  latest_frame_url: string;
}

export interface Device {
  device_id: string;
  name: string;
  camera: Camera;
  controls_version: number; // bumps when controls are re-detected
  controls: Control[];
}

// Keep the action vocabulary tiny for the hackathon.
export type Action =
  | { type: "press"; control_id: string; count: number; hold_ms?: number } // hold_ms: long press, e.g. power buttons
  | { type: "wait"; seconds: number }
  | { type: "read_screen"; control_id: string };

export interface Expectation {
  control_id: string; // usually the screen
  text_contains: string;
  indicator_on?: boolean; // true = expect this indicator light to be on instead of screen text
}

export interface Evidence {
  before_frame_url: string | null;
  after_frame_url: string | null;
  screen_text: string | null; // what the vision model read after the step
}

export interface Retry {
  attempt: number;
  screen_text: string;
  problem: string;
  correction: Action;
}

export interface Step {
  step_id: string;
  index: number;
  intent: string; // human-readable, shown in the UI
  action: Action;
  expect: Expectation;
  requires_approval: boolean;
  approval_reason?: string;
  status: StepStatus;
  attempts: number;
  max_attempts?: number; // max tries before the step fails (default 3)
  started_at: string | null; // ISO 8601
  finished_at: string | null;
  evidence: Evidence;
  error: string | null;
  retries?: Retry[];
}

export interface Plan {
  planner: string;
  summary: string;
  steps: Step[];
}

export interface JobResult {
  outcome: "succeeded" | "failed" | "stopped";
  message: string;
  final_screen_text: string | null;
}

export interface Job {
  job_id: string;
  created_at: string;
  updated_at: string;
  request: string; // the typed job request
  approval_mode: ApprovalMode;
  press_budget?: number; // max presses per job (default 30)
  status: JobStatus;
  device: Device;
  plan: Plan | null; // null while status is "queued" or "planning"
  result: JobResult | null;
}

export interface CreateJobRequest {
  device_id: string;
  request: string;
  approval_mode?: ApprovalMode;
}

export interface ApprovalDecision {
  decision: "approve" | "reject";
  note?: string;
  approval_type?: "tap" | "hold"; // how the approval was given: tap or hold-to-approve
  time_to_decide_ms?: number; // how long the human took to decide, in ms
}

// WebSocket events. Each carries enough to update the UI without refetching.
export type JobEvent =
  | { type: "job.status"; job_id: string; ts: string; status: JobStatus }
  | { type: "plan.ready"; job_id: string; ts: string; plan: Plan }
  | { type: "step.updated"; job_id: string; ts: string; step: Step }
  | { type: "controls.updated"; job_id: string; ts: string; controls_version: number; controls: Control[] }
  | { type: "frame"; job_id: string; ts: string; frame_url: string } // throttle to ~2 fps
  | { type: "job.finished"; job_id: string; ts: string; result: JobResult };
