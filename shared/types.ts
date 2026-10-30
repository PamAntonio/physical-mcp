// Physical MCP: rough contract between the backend and the web app.
// Draft v1, 2026-10-27. Field names may change; job.sample.json matches these types.
// Draft v1 adds (all optional, nothing existing breaks): Control.reachable,
// press hold_ms, Step.max_attempts, Job.press_budget,
// ApprovalDecision.approval_type / time_to_decide_ms, Control kind "indicator",
// and Expectation.indicator_on.
// Proposed (not locked with Pam yet): Step.risk_tier / risk_reason / risk_scored_by,
// JobStatus "paused", ApprovalDecision.decided_by / decided_at / on_reject.
//
// The web app never speaks MCP. Muse Spark makes one JSON plan, and the step runner in
// backend/api.py is the MCP client: every action reaches the machine through the tools in
// backend/mcp_server.py (get_controls, press, read_screen, get_frame, stop), connected in
// memory in the same process. press is the approval gate: it waits for approval and
// charges the press budget before the finger moves.
// The web app talks to the backend over plain HTTP + a WebSocket:
//
//   POST /jobs                         body: CreateJobRequest  -> Job
//   GET  /jobs/:job_id                                         -> Job
//   POST /jobs/:job_id/steps/:step_id/approval  body: ApprovalDecision -> Job
//   POST /jobs/:job_id/stop            immediate stop: servo retracts, job -> "stopped"
//   GET  /devices/:device_id                                   -> Device (with controls)
//   WS   /jobs/:job_id/events          stream of JobEvent
//   GET  /jobs                         -> [{ job_id, created_at, status, request }]  (backend/record_run.py)
//   GET  /jobs/:job_id/history         -> JobEvent[], everything sent so far (backend/record_run.py)

export type JobStatus =
  | "queued"
  | "planning"
  | "running"
  | "awaiting_approval"
  // PROPOSED, not locked with Pam yet: a step was rejected with on_reject "pause" (or "replan").
  // Nothing gets pressed. The step stays awaiting_approval: approving it resumes, Stop ends the job.
  | "paused"
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

// PROPOSED, not locked with Pam yet: risk score from the Muse Spark Open scorer (risk/).
export type RiskTier = "low" | "medium" | "high";

export interface Step {
  step_id: string;
  index: number;
  intent: string; // human-readable, shown in the UI
  action: Action;
  expect: Expectation;
  requires_approval: boolean;
  approval_reason?: string;
  // PROPOSED, not locked with Pam yet. Filled in before a step is shown for approval.
  // The scorer only adds these; it never changes requires_approval.
  risk_tier?: RiskTier;
  risk_reason?: string; // one short line, readable in 2 seconds
  risk_scored_by?: string; // the scorer's model id, or "fallback (...)" if it couldn't score
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

// PROPOSED, not locked with Pam yet: what a reject does.
// "abort": skip the step and end the job. "pause": hold here, nothing pressed, approve later to
// resume. "replan": ask the planner for a new plan (mock only logs it for now, then pauses).
export type RejectBehavior = "abort" | "pause" | "replan";

export interface ApprovalDecision {
  decision: "approve" | "reject";
  note?: string;
  approval_type?: "tap" | "hold"; // how the approval was given: tap or hold-to-approve
  time_to_decide_ms?: number; // how long the human took to decide, in ms, from when the card appeared
  // PROPOSED, not locked with Pam yet:
  decided_by?: string; // who decided, e.g. the operator name from the web app's config
  decided_at?: string; // ISO 8601, when the decision was made
  on_reject?: RejectBehavior; // only meaningful with decision "reject", backend default "pause"
}

// WebSocket events. Each carries enough to update the UI without refetching.
export type JobEvent =
  | { type: "job.status"; job_id: string; ts: string; status: JobStatus }
  | { type: "plan.ready"; job_id: string; ts: string; plan: Plan }
  | { type: "step.updated"; job_id: string; ts: string; step: Step }
  | { type: "controls.updated"; job_id: string; ts: string; controls_version: number; controls: Control[] }
  | { type: "frame"; job_id: string; ts: string; frame_url: string } // throttle to ~2 fps
  | { type: "job.finished"; job_id: string; ts: string; result: JobResult };
