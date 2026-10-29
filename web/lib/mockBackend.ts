// Fake backend that replays one of Pam's fixtures as a stream of JobEvents, the same ones
// the real WebSocket sends (job.status, controls.updated, plan.ready, step.updated, frame,
// job.finished). inkbird-01 replays shared/fixtures/inkbirdJobSample.json, any other device
// replays jobSample.json. Retries in the fixture get replayed, approval steps wait for you.
// Lets us build every UI state before Pam's server and the finger are ready.
// Timings are made up, roughly what a servo press + screen read might take.

import type { ApprovalDecision, BBox, CreateJobRequest, Device, Job, JobEvent, JobStatus, Plan, Retry, Step } from "@contract/types";
import type { JobBackend, JobStreamHandlers } from "./backend";
import { applyEvent, findControl } from "./jobReducer";
import { mockFrameUrl } from "./mockFrame";
import { scorerCouldNotScore, type StepRisk } from "./riskScorer";
import sampleJson from "@contract/fixtures/jobSample.json";
import inkbirdJson from "@contract/fixtures/inkbirdJobSample.json";

// the fixtures only have the screen text Pam wrote down, so the mock fills in the rest
type MockScript = {
  firstScreen: string;
  doneMessage: string;
  screenAfter: (step: Step) => string; // for steps the fixture has no screen text for
  // a still from web/public/mock/, or null to draw the incubator with mockFrameUrl
  frameUrl: string | null;
  // MOCK ONLY: retries the fixture doesn't have yet, so the retry UI shows up on this device.
  // Behind NEXT_PUBLIC_MOCK_INJECT_RETRY, turn it off once Pam's backend sends real retries
  injectedRetries: Record<string, Retry[]>;
};

const INKBIRD_SCREENS: Record<string, string> = {
  s1: "TS 75.0",
  s2: "TS 80.0",
  s3: "PV 72.0 SV 80.0",
  s4: "PV 72.0 SV 80.0 HEAT",
};

const INCUBATOR: { job: Job; script: MockScript } = {
  job: sampleJson as unknown as Job,
  script: {
    firstScreen: "SET TEMP 25.0",
    doneMessage: "Job done. Incubator is running at 37.0 for 2 hours.",
    screenAfter: (step) => `${step.expect.text_contains} 37.0 02:00`, // only START hits this, good enough for a mock
    frameUrl: null,
    injectedRetries: {},
  },
};

const INKBIRD: { job: Job; script: MockScript } = {
  job: inkbirdJson as unknown as Job,
  script: {
    firstScreen: "PV 72.0 SV 75.0",
    doneMessage: "Job done. SV is 80.0 °F and the HEAT light is on.",
    screenAfter: (step) => INKBIRD_SCREENS[step.step_id] ?? "PV 72.0 SV 80.0",
    // served by Next from web/public, same origin as the app
    frameUrl: "/mock/inkbird-01.svg",
    injectedRetries: {
      // s2 presses ▲ five times, one doesn't register, so the first read is one short
      s2: [
        {
          attempt: 1,
          screen_text: "TS 79.0",
          problem: "expected TS 80.0, read TS 79.0",
          correction: { type: "press", control_id: "btn_up", count: 1 },
        },
      ],
    },
  },
};

function fixtureFor(deviceId: string) {
  return deviceId === "inkbird-01" ? INKBIRD : INCUBATOR;
}

// a camera frame for this device. The incubator gets one drawn with the screen text and a ring
// where the finger is about to press; the Inkbird still is the same every time
function frameFor(script: MockScript, screenText: string | null, pressBox?: BBox): string {
  return script.frameUrl ?? mockFrameUrl(screenText, pressBox);
}

// when the risk scorer can't answer in mock mode (no Baseten credits, scorer not running), use the
// fixture's tier if it has one, else high, and say it's a mock score. Never low by default
function mockScore(step: Step, sampleStep: Step): StepRisk {
  return {
    risk_tier: sampleStep.risk_tier ?? "high",
    risk_reason: sampleStep.risk_reason ?? step.approval_reason ?? "Needs approval in the plan.",
    risk_scored_by: "mock score",
  };
}

class StoppedError extends Error {}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function now() {
  return new Date().toISOString();
}

export function sampleDevice(deviceId: string): Device {
  const fixture = fixtureFor(deviceId);
  const device = clone(fixture.job.device);
  device.camera.latest_frame_url = frameFor(fixture.script, null); // blank LCD, the camera view paints the text
  return device;
}

export function sampleJob(deviceId: string): Job {
  return clone(fixtureFor(deviceId).job);
}

export class MockBackend implements JobBackend {
  isMock = true;

  private listeners: Array<(event: JobEvent) => void> = [];
  private timers: ReturnType<typeof setTimeout>[] = [];
  private stopped = false;
  private running = false;
  private waitingForApproval: ((decision: ApprovalDecision) => void) | null = null;
  private job: Job | null = null; // kept up to date with every event we send, so getJob is current
  private fixture = INCUBATOR; // picked by device_id in createJob
  private activeStep: Step | null = null;
  private failStepId: string | null;
  private scoreStep: ((job: Job, step: Step) => Promise<StepRisk>) | null;
  private injectRetry: boolean;

  // scoreStep: called right before a step goes to awaiting_approval, its tier + reason
  // get attached to the step. Leave it out and steps go out unscored.
  // injectRetry: replay the mock-only retries in MockScript.injectedRetries (default on)
  constructor(
    options: { failStepId?: string; scoreStep?: (job: Job, step: Step) => Promise<StepRisk>; injectRetry?: boolean } = {},
  ) {
    this.failStepId = options.failStepId ?? null;
    this.scoreStep = options.scoreStep ?? null;
    this.injectRetry = options.injectRetry ?? true;
  }

  async getDevice(deviceId: string): Promise<Device> {
    return sampleDevice(deviceId);
  }

  async createJob(request: CreateJobRequest): Promise<Job> {
    this.reset();
    this.fixture = fixtureFor(request.device_id);
    const sample = this.fixture.job;
    // start from the fixture, rewound to "just submitted": no plan yet, nothing pressed
    const job: Job = clone(sample);
    job.job_id = "job_mock_" + Math.random().toString(36).slice(2, 8);
    job.created_at = now();
    job.updated_at = now();
    job.request = request.request;
    job.approval_mode = request.approval_mode ?? sample.approval_mode;
    job.status = "queued";
    job.plan = null;
    job.result = null;
    job.device = sampleDevice(request.device_id); // swaps in the mock frame
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
    // mock frames are data: urls we drew ourselves or stills in web/public/mock/.
    // the sample's /frames/... paths don't exist, so skip those
    if (path && (path.startsWith("data:") || path.startsWith("/mock/"))) return path;
    return null;
  }

  // ---- the fake run ----

  private async run() {
    const sample = this.fixture.job;
    const script = this.fixture.script;
    await this.sleep(400);
    this.emitStatus("planning");
    await this.sleep(800);

    // pretend SAM just re-scanned the panel and found the same buttons
    const controls = sampleDevice(sample.device.device_id).controls;
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

    let lastScreenText = script.firstScreen;
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
        message: script.doneMessage,
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
    const script = this.fixture.script;
    step.evidence.before_frame_url = frameFor(script, screenBefore, pressBox);
    step.evidence.screen_text = screenBefore;

    if (step.requires_approval) {
      // score it before anyone sees the approval card. this only adds tier + reason,
      // the step stays gated whatever the scorer says
      if (this.scoreStep && this.job) {
        const jobIdBefore = this.jobId();
        let risk = await this.scoreStep(clone(this.job), clone(step));
        if (scorerCouldNotScore(risk)) risk = mockScore(step, sampleStep);
        // stopped, or a new job started while we were waiting on the scorer
        if (this.stopped || this.jobId() !== jobIdBefore) throw new StoppedError();
        step.risk_tier = risk.risk_tier;
        step.risk_reason = risk.risk_reason;
        step.risk_scored_by = risk.risk_scored_by;
      }

      step.status = "awaiting_approval";
      this.emitStep(step);
      this.emitStatus("awaiting_approval");

      // keep asking until it's approved or aborted. a pause just holds here
      let decision = await this.waitForDecision();
      while (decision.decision === "reject") {
        const behavior = decision.on_reject ?? "pause"; // contract default

        if (behavior === "abort") {
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

        // "pause", and "replan" for now (no replanner in the mock, so it holds like a pause).
        // the step stays awaiting_approval: approving it later resumes, Stop ends the job.
        // (the mock server logs "replan requested", the action log shows it too)
        this.emitStatus("paused");
        decision = await this.waitForDecision();
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
      step.evidence.after_frame_url = frameFor(script, screenBefore); // nothing changed, that's the problem
      this.emitStep(step);
      this.emit({
        type: "job.finished",
        job_id: this.jobId(),
        ts: now(),
        result: { outcome: "failed", message: `Job failed at step ${step.index}.`, final_screen_text: screenBefore },
      });
      return false;
    }

    // replay the sample's retries so the self-correction moment shows up.
    // no retries in the fixture? use the mock-only injected ones, if that's switched on
    let sampleRetries = sampleStep.retries ?? [];
    if (sampleRetries.length === 0 && this.injectRetry) sampleRetries = script.injectedRetries[step.step_id] ?? [];
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
      step.evidence.screen_text = this.fixture.script.screenAfter(step);
    }
    step.evidence.after_frame_url = frameFor(script, step.evidence.screen_text);
    this.emitStep(step);
    this.emit({ type: "frame", job_id: this.jobId(), ts: now(), frame_url: frameFor(script, null) });
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
