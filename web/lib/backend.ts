// How the web app talks to Pam's backend: plain HTTP + a WebSocket (see types.ts).
// If NEXT_PUBLIC_BACKEND_URL isn't set we fall back to the mock, so the UI works today.

import type { ApprovalDecision, CreateJobRequest, Device, Job, JobEvent } from "./types";
import { MockBackend } from "./mockBackend";

export interface JobBackend {
  isMock: boolean;
  getDevice(deviceId: string): Promise<Device>;
  createJob(request: CreateJobRequest): Promise<Job>;
  subscribe(jobId: string, onEvent: (event: JobEvent) => void): () => void;
  decide(jobId: string, stepId: string, decision: ApprovalDecision): Promise<void>;
  stop(jobId: string): Promise<void>;
  // frame urls in the contract are paths like /frames/..., this turns them into something <img> can load
  frameUrl(path: string | null): string | null;
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

  async decide(jobId: string, stepId: string, decision: ApprovalDecision) {
    await this.send<Job>(`/jobs/${jobId}/steps/${stepId}/approval`, "POST", decision);
  }

  async stop(jobId: string) {
    await this.send<unknown>(`/jobs/${jobId}/stop`, "POST");
  }

  subscribe(jobId: string, onEvent: (event: JobEvent) => void) {
    const wsUrl = this.baseUrl.replace(/^http/, "ws") + `/jobs/${jobId}/events`;
    let socket: WebSocket | null = null;
    let closedByUs = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      socket = new WebSocket(wsUrl);
      socket.onmessage = (message) => {
        try {
          onEvent(JSON.parse(message.data) as JobEvent);
        } catch (err) {
          console.warn("couldn't parse job event", err, message.data);
        }
      };
      socket.onclose = () => {
        // wifi blips happen at demos, so just reconnect after a second
        if (!closedByUs) retryTimer = setTimeout(connect, 1000);
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
    if (path.startsWith("http")) return path;
    return this.baseUrl + path;
  }
}

export function createBackend(): JobBackend {
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (backendUrl) return new HttpBackend(backendUrl);

  // mock mode. ?fail=s4 makes step s4 fail so we can see the failed state
  let failStepId: string | undefined;
  if (typeof window !== "undefined") {
    const params = new URLSearchParams(window.location.search);
    failStepId = params.get("fail") ?? undefined;
  }
  return new MockBackend({ failStepId });
}
