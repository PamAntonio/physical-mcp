// Fake backend that replays Pam's shared/fixtures/jobSample.json as a stream of JobEvents,
// the same ones the real WebSocket sends (job.status, controls.updated, plan.ready,
// step.updated, frame, job.finished). Step 2 replays the sample's retry, step 6 waits for you.
// Lets us build every UI state before Pam's server and the finger are ready.
// Timings are made up, roughly what a servo press + screen read might take.

import type { ApprovalDecision, BBox, CreateJobRequest, Device, Job, JobEvent, JobStatus, Plan, Step } from "@contract/types";
import type { JobBackend, JobStreamHandlers } from "./backend";
import { applyEvent, findControl } from "./jobReducer";
import { mockFrameUrl } from "./mockFrame";
import sampleJson from "@contract/fixtures/jobSample.json";

const sample = sampleJson as unknown as Job;

// stand-in camera frame with a blank LCD (the camera view paints the live screen text on top)
const MOCK_FRAME_URL = mockFrameUrl(null);

class StoppedError extends Error {}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function now() {
  return new Date().toISOString();
}

export function sampleDevice(): Device {
  const device = clone(sample.device);
  device.camera.latest_frame_url = MOCK_FRAME_URL;
  return device;
}

export function sampleJob(): Job {
  return clone(sample);
}

export class MockBackend implements JobBackend {
  isMock = true;

  private listeners: Array<(event: JobEvent) => void> = [];
  private timers: ReturnType<typeof setTimeout>[] = [];
  private stopped = false;
  private running = false;
  private waitingForApproval: ((decision: ApprovalDecision) => void) | null = null;
  private job: Job | null = null; // kept up to date with every event we send, so getJob is current
  private activeStep: Step | null = null;
  private failStepId: string | null;

  constructor(options: { failStepId?: string } = {}) {
    this.failStepId = options.failStepId ?? null;
  }

  async getDevice(): Promise<Device> {
    return sampleDevice();
  }

  async createJob(request: CreateJobRequest): Promise<Job> {
    this.reset();
    // start from job_sample.json, rewound to "just submitted": no plan yet, nothing pressed
    const job: Job = clone(sample);
    job.job_id = "job_mock_" + Math.random().toString(36).slice(2, 8);
    job.created_at = now();
    job.updated_at = now();
    job.request = request.request;
    job.approval_mode = request.approval_mode ?? sample.approval_mode;
    job.status = "queued";
    job.plan = null;
    job.result = null;
    job.device = sampleDevice(); // swaps in the mock frame
    this.job = job;
    return clone(job);
  }

  async getJob(_jobId: string): Promise<Job> {
    if (!this.job) throw new Error("no mock job yet");
    return clone(this.job);
  }

  subscribe(_jobId: string, handlers: JobStreamHandlers) {
    const onEvent = handlers.onEvent;
    this.listeners.push(onEvent);
    if (handlers.onConnection) handlers.onConnection("live"); // nothing to connect to in the browser
    if (!this.running) {
      this.running = true;
      // start the fake run once someone is listening
      this.run().catch((err) => {
        if (!(err instanceof StoppedError)) console.error("mock run crashed", err);
      });
    }
    return () => {
      const kept: Array<(event: JobEvent) => void> = [];
      for (const listener of this.listeners) {
        if (listener !== onEvent) kept.push(listener);
      }
      this.listeners = kept;
    };
  }

  async decide(_jobId: string, _stepId: string, decision: ApprovalDecision) {
    if (this.waitingForApproval) {
      const resolve = this.waitingForApproval;
      this.waitingForApproval = null;
      resolve(decision);
    }
  }

  async stop(_jobId: string) {
    this.stopped = true;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers = [];
    this.waitingForApproval = null;

    // whatever step was mid-press gets marked, so the UI doesn't spin forever
    const step = this.activeStep;
    if (step && (step.status === "running" || step.status === "verifying" || step.status === "awaiting_approval")) {
      step.status = "skipped";
      step.error = "Stopped mid-step";
      step.finished_at = now();
      this.emitStep(step);
    }
    this.activeStep = null;

    this.emit({
      type: "job.finished",
      job_id: this.jobId(),
      ts: now(),
      result: { outcome: "stopped", message: "Stopped by you. Finger retracted.", final_screen_text: null },
    });
  }

  frameUrl(path: string | null) {
    // mock frames are data: urls we drew ourselves. the sample's /frames/... paths don't exist, so skip those
    if (path && path.startsWith("data:")) return path;
    return null;
  }

  // ---- the fake run ----

  private async run() {
    await this.sleep(400);
    this.emitStatus("planning");
    await this.sleep(800);

    // pretend SAM just re-scanned the panel and found the same buttons
    const controls = sampleDevice().controls;
    this.emit({
      type: "controls.updated",
      job_id: this.jobId(),
      ts: now(),
      controls_version: sample.device.controls_version + 1,
      controls,
    });
    await this.sleep(800);

    // fresh copies of the sample steps, everything back to pending
    const steps: Step[] = [];
    for (const sampleStep of sample.plan!.steps) {
      steps.push({
        ...clone(sampleStep),
        status: "pending",
        attempts: 0,
        started_at: null,
        finished_at: null,
        evidence: { before_frame_url: null, after_frame_url: null, screen_text: null },
        error: null,
        retries: undefined,
      });
    }
    const plan: Plan = { planner: sample.plan!.planner, summary: sample.plan!.summary, steps };
    this.emit({ type: "plan.ready", job_id: this.jobId(), ts: now(), plan: clone(plan) });
    this.emitStatus("running");

    let lastScreenText = "SET TEMP 25.0";
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      const sampleStep = sample.plan!.steps[i];
      const keepGoing = await this.runStep(step, sampleStep, lastScreenText);
      if (!keepGoing) return;
      lastScreenText = step.evidence.screen_text ?? lastScreenText;
    }

    this.emit({
      type: "job.finished",
      job_id: this.jobId(),
      ts: now(),
      result: {
        outcome: "succeeded",
        message: "Job done. Incubator is running at 37.0 for 2 hours.",
        final_screen_text: lastScreenText,
      },
    });
  }

  // returns false if the job should stop here
  private async runStep(step: Step, sampleStep: Step, screenBefore: string): Promise<boolean> {
    this.activeStep = step;
    // "before" frame: the screen as it is now, with a ring where the finger is about to press
    let pressBox: BBox | undefined;
    if (step.action.type !== "wait") {
      const control = findControl(this.job ? this.job.device.controls : [], step.action.control_id);
      if (control && control.kind !== "screen") pressBox = control.bbox;
    }
    step.evidence.before_frame_url = mockFrameUrl(screenBefore, pressBox);
    step.evidence.screen_text = screenBefore;

    if (step.requires_approval) {
      step.status = "awaiting_approval";
      this.emitStep(step);
      this.emitStatus("awaiting_approval");

      const decision = await this.waitForDecision();
      if (decision.decision === "reject") {
        step.status = "skipped";
        step.error = decision.note ?? "Rejected at approval";
        step.finished_at = now();
        this.emitStep(step);
        this.emit({
          type: "job.finished",
          job_id: this.jobId(),
          ts: now(),
          result: {
            outcome: "stopped",
            message: `Stopped: step ${step.index} was rejected, the finger never moved.`,
            final_screen_text: screenBefore,
          },
        });
        return false;
      }
      this.emitStatus("running");
    }

    // press
    step.status = "running";
    step.attempts = 1;
    step.started_at = now();
    this.emitStep(step);
    const pressCount = step.action.type === "press" ? step.action.count : 1;
    await this.sleep(500 + pressCount * 180);

    // verify
    step.status = "verifying";
    this.emitStep(step);
    await this.sleep(700);

    if (this.failStepId === step.step_id) {
      step.status = "failed";
      step.error = "Screen didn't change after the press. The finger may have missed the button.";
      step.finished_at = now();
      step.evidence.after_frame_url = mockFrameUrl(screenBefore); // nothing changed, that's the problem
      this.emitStep(step);
      this.emit({
        type: "job.finished",
        job_id: this.jobId(),
        ts: now(),
        result: { outcome: "failed", message: `Job failed at step ${step.index}.`, final_screen_text: screenBefore },
      });
      return false;
    }

    // replay the sample's retries so the self-correction moment shows up
    const sampleRetries = sampleStep.retries ?? [];
    for (const retry of sampleRetries) {
      step.retries = [...(step.retries ?? []), retry];
      step.evidence.screen_text = retry.screen_text;
      step.status = "running";
      step.attempts += 1;
      this.emitStep(step);
      await this.sleep(900);

      step.status = "verifying";
      this.emitStep(step);
      await this.sleep(700);
    }

    // done, use the sample evidence when it has some
    step.status = "succeeded";
    step.finished_at = now();
    if (sampleStep.status === "succeeded" && sampleStep.evidence.screen_text) {
      step.evidence.screen_text = sampleStep.evidence.screen_text;
    } else {
      step.evidence.screen_text = `${step.expect.text_contains} 37.0 02:00`; // only START hits this, good enough for a mock
    }
    step.evidence.after_frame_url = mockFrameUrl(step.evidence.screen_text);
    this.emitStep(step);
    this.emit({ type: "frame", job_id: this.jobId(), ts: now(), frame_url: MOCK_FRAME_URL });
    await this.sleep(300);
    return true;
  }

  // ---- little helpers ----

  private waitForDecision(): Promise<ApprovalDecision> {
    return new Promise((resolve) => {
      this.waitingForApproval = resolve;
    });
  }

  private async sleep(ms: number) {
    await new Promise<void>((resolve) => {
      this.timers.push(setTimeout(resolve, ms));
    });
    if (this.stopped) throw new StoppedError();
  }

  private reset() {
    this.stopped = true; // kills any old run that's mid-sleep
    for (const timer of this.timers) clearTimeout(timer);
    this.timers = [];
    this.listeners = [];
    this.waitingForApproval = null;
    this.running = false;
    this.stopped = false;
  }

  private jobId() {
    return this.job ? this.job.job_id : "job_mock";
  }

  private emitStatus(status: JobStatus) {
    this.emit({ type: "job.status", job_id: this.jobId(), ts: now(), status });
  }

  private emitStep(step: Step) {
    this.emit({ type: "step.updated", job_id: this.jobId(), ts: now(), step: clone(step) });
  }

  private emit(event: JobEvent) {
    if (this.job) this.job = applyEvent(this.job, event);
    for (const listener of this.listeners) listener(event);
  }
}
