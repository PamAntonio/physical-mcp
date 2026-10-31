// Fake backend that replays one of Pam's fixtures as a stream of JobEvents, the same ones
// the real WebSocket sends (job.status, controls.updated, plan.ready, step.updated, frame,
// job.finished). inkbird-01 replays shared/fixtures/inkbirdJobSample.json, timer-01
// timerJobSample.json, any other device replays jobSample.json. Retries in the fixture get replayed, approval steps wait for you.
// Lets us build every UI state before Pam's server and the finger are ready.
// Timings are made up, roughly what a servo press + screen read might take.

import type {
  ApprovalDecision,
  BBox,
  CreateJobRequest,
  Device,
  EventNote,
  Job,
  JobEvent,
  JobStatus,
  NoteKind,
  Plan,
  ReadConfidence,
  Retry,
  ScreenRead,
  Step,
} from "@contract/types";
import type { JobBackend, JobStreamHandlers } from "./backend";
import { applyEvent, findControl } from "./jobReducer";
import { mockFrameUrl } from "./mockFrame";
import { scorerCouldNotScore, type StepRisk } from "./riskScorer";
import sampleJson from "@contract/fixtures/jobSample.json";
import inkbirdJson from "@contract/fixtures/inkbirdJobSample.json";
import timerJson from "@contract/fixtures/timerJobSample.json";

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
  // how sure the mock's reads are. null = no vision vote, so no badge (the fixtures for the
  // Inkbird and the incubator have none, and the mock doesn't make one up)
  confidence: ReadConfidence | null;
  // the screen the planner reads first (the timer's plan_reads), or null to skip that read
  planningScreen: string | null;
  // one read comes back low after this step's press: the job pauses until Resume, then the
  // re-read is clear. Shows the backend's read_unclear pause with no backend
  lowRead?: { stepId: string; screenText: string };
  // what the backend's planner would refuse on this rig, or null to plan it
  refuse?: (request: string) => string | null;
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
    confidence: null,
    planningScreen: null,
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
    confidence: null,
    planningScreen: null,
  },
};

// what screen_t1 reads after each step of the T1 2-hour job (the same HH:MM SS the backend's
// vision returns). s2's first H misses, the fixture's retry replays that
const TIMER_SCREENS: Record<string, string> = {
  s1: "01:00 00",
  s2: "02:00 00",
  s3: "01:59 58",
};

// the rig only has fingers on H and ST/SP (backend/devices/timer-01.json), so like the real
// planner the mock refuses anything that needs another channel or another time button
function refuseOnTimer(request: string): string | null {
  if (/\bT\s*[234]\b/i.test(request)) {
    return "Can't do this on this rig: only T1 can be used (no finger on T1 to T4 to pick another channel). Nothing was pressed.";
  }
  if (/\b(minute|minutes|min|second|seconds|sec)\b/i.test(request)) {
    return "Can't do this on this rig: only whole hours can be set (H is the only time button with a finger). Nothing was pressed.";
  }
  return null;
}

const TIMER: { job: Job; script: MockScript } = {
  job: timerJson as unknown as Job,
  script: {
    firstScreen: "00:00 00",
    doneMessage: "Job done. T1 is counting down from 2 hours.",
    screenAfter: (step) => TIMER_SCREENS[step.step_id] ?? "01:59 58",
    frameUrl: "/mock/timer-01.svg",
    injectedRetries: {},
    confidence: "high",
    planningScreen: "screen_t1",
    // the check after the first H: three reads disagreed, so vision says low and the job waits
    lowRead: { stepId: "s1", screenText: "01:08 00" },
    refuse: refuseOnTimer,
  },
};

function fixtureFor(deviceId: string) {
  if (deviceId === "inkbird-01") return INKBIRD;
  if (deviceId === "timer-01") return TIMER;
  return INCUBATOR;
}

// a camera frame for this device. The incubator gets one drawn with the screen text and a ring
// where the finger is about to press; the Inkbird still is the same every time
function frameFor(script: MockScript, screenText: string | null, pressBox?: BBox): string {
  return script.frameUrl ?? mockFrameUrl(screenText, pressBox);
}

// when the risk scorer can't answer in mock mode (no Baseten credits, scorer not running), use the
// fixture's tier if it has one, else high, and say it's a mock score. Never low by default
// A fixture that carries a real score the scorer gave earlier (the timer's ST/SP) says which
// model gave it, marked as recorded so nobody takes it for a live call
function mockScore(step: Step, sampleStep: Step): StepRisk {
  let scoredBy = "mock score";
  if (sampleStep.risk_tier && sampleStep.risk_scored_by && sampleStep.risk_scored_by !== "fixture") {
    scoredBy = `${sampleStep.risk_scored_by} (recorded score, scorer not running)`;
  }
  return {
    risk_tier: sampleStep.risk_tier ?? "high",
    risk_reason: sampleStep.risk_reason ?? step.approval_reason ?? "Needs approval in the plan.",
    risk_scored_by: scoredBy,
  };
}

class StoppedError extends Error {}

// an EventNote like backend/api.py's make_note
function note(kind: NoteKind, message: string, read?: ScreenRead): EventNote {
  const made: EventNote = { kind, message };
  if (read) made.read = { ...read };
  return made;
}

// "screen_t1 reads '01:00 00' (high)", the backend's describe_read
function describeRead(read: ScreenRead): string {
  return `${read.control_id} reads '${read.text}' (${read.confidence ?? "no vision vote"})`;
}

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
  private waitingForResume: ((decidedBy: string) => void) | null = null;
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
    job.pause_reason = null;
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

  async resume(_jobId: string, decidedBy: string) {
    if (this.waitingForResume) {
      const resolve = this.waitingForResume;
      this.waitingForResume = null;
      resolve(decidedBy);
    }
  }

  async stop(_jobId: string) {
    this.stopped = true;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers = [];
    this.waitingForApproval = null;
    this.waitingForResume = null;

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
  // Sends the same notes, confidences and pauses as backend/api.py, so every log kind, the
  // confidence badges and the read_unclear pause all show up with no backend running.

  private async run() {
    const sample = this.fixture.job;
    const script = this.fixture.script;
    await this.sleep(400);
    this.emitStatus("planning");

    // the planner's first read (the timer's plan_reads: screen_t1)
    let planningRead: ScreenRead | null = null;
    if (script.planningScreen) {
      await this.sleep(500);
      planningRead = { control_id: script.planningScreen, text: script.firstScreen, confidence: script.confidence };
    }
    await this.sleep(800);

    // the real planner refuses what the rig can't do. nothing gets planned or pressed
    const refusal = script.refuse && this.job ? script.refuse(this.job.request) : null;
    if (refusal) {
      this.emit({
        type: "job.finished",
        job_id: this.jobId(),
        ts: now(),
        result: { outcome: "failed", message: refusal, final_screen_text: null },
        note: note("planner_refused", refusal),
      });
      return;
    }

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
        evidence: { before_frame_url: null, after_frame_url: null, screen_text: null, screen_confidence: null },
        error: null,
        retries: undefined,
      });
    }
    const plan: Plan = { planner: sample.plan!.planner, summary: sample.plan!.summary, steps };
    let planNote: EventNote | undefined;
    if (planningRead) planNote = note("screen_read", `Planned from ${describeRead(planningRead)}`, planningRead);
    this.emit({ type: "plan.ready", job_id: this.jobId(), ts: now(), plan: clone(plan), note: planNote });
    this.emitStatus("running");

    let lastScreenText = script.firstScreen;
    let lastConfidence: ReadConfidence | null = planningRead ? planningRead.confidence : null;
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      const sampleStep = sample.plan!.steps[i];
      const keepGoing = await this.runStep(step, sampleStep, lastScreenText, lastConfidence);
      if (!keepGoing) return;
      lastScreenText = step.evidence.screen_text ?? lastScreenText;
      lastConfidence = step.evidence.screen_confidence ?? null;
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
  private async runStep(
    step: Step,
    sampleStep: Step,
    screenBefore: string,
    confidenceBefore: ReadConfidence | null,
  ): Promise<boolean> {
    this.activeStep = step;
    // "before" frame: the screen as it is now, with a ring where the finger is about to press
    let pressBox: BBox | undefined;
    if (step.action.type !== "wait") {
      const control = findControl(this.job ? this.job.device.controls : [], step.action.control_id);
      if (control && control.kind !== "screen") pressBox = control.bbox;
    }
    const script = this.fixture.script;
    const screenId = step.expect.control_id;
    step.evidence.before_frame_url = frameFor(script, screenBefore, pressBox);
    // what the approval card shows: the last read of this screen, with how sure it was
    step.evidence.screen_text = screenBefore;
    step.evidence.screen_confidence = confidenceBefore;

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
        // the rejection is one entry, what happens to the step after it is another
        let rejected = `Step ${step.index} rejected by ${decision.decided_by ?? "you"} (${behavior})`;
        if (decision.note) rejected += `: ${decision.note}`;
        this.emitStep(step, note("rejected", rejected));

        if (behavior === "abort") {
          step.status = "skipped";
          step.error = decision.note ?? "Rejected at approval";
          step.finished_at = now();
          this.emitStep(step, note("step_skipped", `Step ${step.index} skipped, the finger never moved`));
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
        this.emitStatus(
          "paused",
          `Step ${step.index} was rejected, holding here. Nothing was pressed. Approve it to carry on, or Stop.`,
        );
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

    // a read vision isn't sure of: pause, nothing pressed, until Resume. then read it again
    if (script.lowRead && script.lowRead.stepId === step.step_id) {
      const unclear: ScreenRead = { control_id: screenId, text: script.lowRead.screenText, confidence: "low" };
      const reason =
        `Couldn't read ${screenId} clearly (step ${step.index}): low confidence, read '${unclear.text}'. ` +
        "Nothing was pressed. Check the camera and the screen, then resume to read it again.";
      step.evidence.screen_text = unclear.text;
      step.evidence.screen_confidence = "low";
      this.emitStep(step);
      this.emitStatus("paused", reason, note("read_unclear", reason, unclear));
      const decidedBy = await this.waitForResume();
      this.emitStatus("running", undefined, note("resumed", `Resumed by ${decidedBy}, reading ${screenId} again`));
      await this.sleep(900);
    }

    // replay the sample's retries so the self-correction moment shows up.
    // no retries in the fixture? use the mock-only injected ones, if that's switched on
    let sampleRetries = sampleStep.retries ?? [];
    if (sampleRetries.length === 0 && this.injectRetry) sampleRetries = script.injectedRetries[step.step_id] ?? [];
    for (const retry of sampleRetries) {
      step.retries = [...(step.retries ?? []), retry];
      step.evidence.screen_text = retry.screen_text;
      step.evidence.screen_confidence = retry.screen_confidence ?? script.confidence;
      step.status = "running";
      step.attempts += 1;
      const read: ScreenRead = { control_id: screenId, text: retry.screen_text, confidence: step.evidence.screen_confidence };
      this.emitStep(step, note("retry", `Retry: ${retry.problem}`, read));
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
    step.evidence.screen_confidence = script.confidence;
    step.evidence.after_frame_url = frameFor(script, step.evidence.screen_text);
    const read: ScreenRead = { control_id: screenId, text: step.evidence.screen_text, confidence: script.confidence };
    this.emitStep(step, note("screen_read", `Verified: ${describeRead(read)}`, read));
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

  private waitForResume(): Promise<string> {
    return new Promise((resolve) => {
      this.waitingForResume = resolve;
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
    this.waitingForResume = null;
    this.running = false;
    this.stopped = false;
  }

  private jobId() {
    return this.job ? this.job.job_id : "job_mock";
  }

  // reason: only for "paused", why (the backend's job.status reason). note: what it is for the log
  private emitStatus(status: JobStatus, reason?: string, eventNote?: EventNote) {
    const event: JobEvent = { type: "job.status", job_id: this.jobId(), ts: now(), status };
    if (reason !== undefined) event.reason = reason;
    if (eventNote !== undefined) event.note = eventNote;
    this.emit(event);
  }

  private emitStep(step: Step, eventNote?: EventNote) {
    const event: JobEvent = { type: "step.updated", job_id: this.jobId(), ts: now(), step: clone(step) };
    if (eventNote !== undefined) event.note = eventNote;
    this.emit(event);
  }

  private emit(event: JobEvent) {
    if (this.job) this.job = applyEvent(this.job, event);
    for (const listener of this.listeners) listener(event);
  }
}
