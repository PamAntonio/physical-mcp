// How the web app talks to Pam's backend: plain HTTP + a WebSocket (see shared/types.ts).
// USE_MOCK in config.ts picks the mock or the real one; nothing else in the app cares which.

import type { ApprovalDecision, CreateJobRequest, Device, Job, JobEvent } from "@contract/types";
import { BACKEND_URL, MOCK_INJECT_RETRY, RISK_SCORER_URL, USE_MOCK } from "./config";
import { MockBackend } from "./mockBackend";
import { scoreStep } from "./riskScorer";

export type ConnectionState = "connecting" | "live" | "reconnecting" | "closed";

export interface JobStreamHandlers {
  onEvent: (event: JobEvent) => void;
  // full job from GET /jobs/:id after a reconnect, so events missed while offline aren't lost
  onSync?: (job: Job) => void;
  onConnection?: (state: ConnectionState) => void;
}

export interface JobBackend {
  isMock: boolean;
  getDevice(deviceId: string): Promise<Device>;
  createJob(request: CreateJobRequest): Promise<Job>;
  getJob(jobId: string): Promise<Job>;
  subscribe(jobId: string, handlers: JobStreamHandlers): () => void;
  decide(jobId: string, stepId: string, decision: ApprovalDecision): Promise<void>;
  stop(jobId: string): Promise<void>;
  // frame urls in the contract are paths like /frames/..., this turns them into something <img> can load
  frameUrl(path: string | null): string | null;
}

// every JobEvent type in the contract. Record<> makes TS complain if one is added and not listed here
const knownEventTypes: Record<JobEvent["type"], true> = {
  "job.status": true,
  "plan.ready": true,
  "step.updated": true,
  "controls.updated": true,
  frame: true,
  "job.finished": true,
};

// Basic sanity check on a message off the socket before it goes near the reducer.
// Returns null (and says why) for anything that isn't an event for this job.
export function readJobEvent(raw: unknown, jobId: string): JobEvent | null {
  if (typeof raw !== "object" || raw === null) {
    console.warn("job event isn't an object, ignoring", raw);
    return null;
  }
  const message = raw as { type?: unknown; job_id?: unknown; ts?: unknown };
  if (typeof message.type !== "string" || !(message.type in knownEventTypes)) {
    console.warn(`unknown job event type "${String(message.type)}", ignoring`, raw);
    return null;
  }
  if (message.job_id !== jobId) {
    console.warn(`event for job ${String(message.job_id)} on the ${jobId} stream, ignoring`);
    return null;
  }
  if (typeof message.ts !== "string") {
    console.warn("job event has no ts, ignoring", raw);
    return null;
  }
  return raw as JobEvent;
}

export class HttpBackend implements JobBackend {
  isMock = false;
  private baseUrl: string;

  constructor(baseUrl: string) {
    // drop a trailing slash so we can just glue paths on
    this.baseUrl = baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
  }

  private async send<T>(path: string, method: string = "GET", body?: unknown): Promise<T> {
    const response = await fetch(this.baseUrl + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`${method} ${path} failed with ${response.status}`);
    }
    const text = await response.text();
    return (text ? JSON.parse(text) : null) as T;
  }

  getDevice(deviceId: string) {
    return this.send<Device>(`/devices/${deviceId}`);
  }

  createJob(request: CreateJobRequest) {
    return this.send<Job>("/jobs", "POST", request);
  }

  getJob(jobId: string) {
    return this.send<Job>(`/jobs/${jobId}`);
  }

  async decide(jobId: string, stepId: string, decision: ApprovalDecision) {
    await this.send<Job>(`/jobs/${jobId}/steps/${stepId}/approval`, "POST", decision);
  }

  async stop(jobId: string) {
    await this.send<unknown>(`/jobs/${jobId}/stop`, "POST");
  }

  subscribe(jobId: string, handlers: JobStreamHandlers) {
    const wsUrl = this.baseUrl.replace(/^http/, "ws") + `/jobs/${jobId}/events`;
    let socket: WebSocket | null = null;
    let closedByUs = false;
    let jobFinished = false; // once the job is over there's nothing left to stream
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let retryDelay = 1000;
    let everConnected = false;

    // while we're catching up after a reconnect, hold events so the older GET result
    // can't overwrite something newer
    let syncing = false;
    let heldEvents: JobEvent[] = [];

    const setConnection = (state: ConnectionState) => {
      if (handlers.onConnection) handlers.onConnection(state);
    };

    const deliver = (event: JobEvent) => {
      if (syncing) {
        heldEvents.push(event);
        return;
      }
      handlers.onEvent(event);
      if (event.type === "job.finished") {
        jobFinished = true;
        if (socket) socket.close();
      }
    };

    const catchUp = async () => {
      if (!handlers.onSync) return;
      syncing = true;
      try {
        const job = await this.getJob(jobId);
        if (!closedByUs) handlers.onSync(job);
      } catch (err) {
        console.warn("couldn't catch up after reconnecting", err);
      }
      syncing = false;
      const held = heldEvents;
      heldEvents = [];
      for (const event of held) deliver(event);
    };

    const connect = () => {
      setConnection(everConnected ? "reconnecting" : "connecting");
      socket = new WebSocket(wsUrl);

      socket.onopen = () => {
        retryDelay = 1000;
        setConnection("live");
        if (everConnected) catchUp(); // we were offline for a bit, grab the full job
        everConnected = true;
      };

      socket.onmessage = (message) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(message.data);
        } catch (err) {
          console.warn("couldn't parse job event", err, message.data);
          return;
        }
        const event = readJobEvent(parsed, jobId);
        if (event) deliver(event);
      };

      socket.onclose = () => {
        if (closedByUs || jobFinished) {
          setConnection("closed");
          return;
        }
        // wifi blips happen at demos, so keep trying, backing off up to 8s
        setConnection("reconnecting");
        retryTimer = setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 8000);
      };
    };
    connect();

    return () => {
      closedByUs = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (socket) socket.close();
    };
  }

  frameUrl(path: string | null) {
    if (!path) return null;
    // full urls (and data: urls from the mock server) are ready to use as is.
    // /mock/... is a placeholder still the mock server points at, and /frames/... is the rig's
    // latest webcam frame (backend/serial_hardware.py writes it into web/public/frames). Both are
    // served by this app from web/public, not by the backend
    if (path.startsWith("http") || path.startsWith("data:") || path.startsWith("/mock/") || path.startsWith("/frames/")) {
      return path;
    }
    return this.baseUrl + path;
  }
}

export function createBackend(): JobBackend {
  if (!USE_MOCK) return new HttpBackend(BACKEND_URL);

  // mock mode. ?fail=s4 makes step s4 fail so we can see the failed state
  let failStepId: string | undefined;
  if (typeof window !== "undefined") {
    const params = new URLSearchParams(window.location.search);
    failStepId = params.get("fail") ?? undefined;
  }
  // approval steps get scored by the Python risk scorer, or fall back to a "mock score"
  return new MockBackend({
    failStepId,
    scoreStep: (job, step) => scoreStep(RISK_SCORER_URL, job, step),
    injectRetry: MOCK_INJECT_RETRY,
  });
}
