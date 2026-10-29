// Mock backend server. Speaks the same contract as Pam's real backend (shared/types.ts):
//
//   POST /jobs                                  -> Job
//   GET  /jobs/:job_id                          -> Job (current state)
//   POST /jobs/:job_id/steps/:step_id/approval  -> Job
//   POST /jobs/:job_id/stop                     -> Job
//   GET  /devices/:device_id                    -> Device
//   WS   /jobs/:job_id/events                   -> stream of JobEvent
//
// The run itself is lib/mockBackend.ts, the same replay the in-browser mock uses,
// so the app's real HTTP + WebSocket client gets exercised against mock data.
// One job at a time, like the real rig (one finger).
//
//   npm run mock-server              # just this, on :8787
//   MOCK_FAIL_STEP=s4 npm run mock-server   # make step s4 fail
//   curl -X POST localhost:8787/debug/drop-sockets   # cut the stream, watch the app reconnect
//
// Steps that need approval get scored by the Python risk scorer first (python risk/server.py,
// POST /score on :8091, or set RISK_SCORER_URL). If it isn't running they go out as
// "high, couldn't score", so the approval card never has an empty risk slot.

import http from "node:http";
import { WebSocketServer, type WebSocket } from "ws";
import type { ApprovalDecision, CreateJobRequest, Job, JobEvent, Step } from "@contract/types";
import { MockBackend, sampleDevice } from "../lib/mockBackend";
import { DEFAULT_RISK_SCORER_URL, scoreStep } from "../lib/riskScorer";

export const MOCK_PORT = Number(process.env.MOCK_PORT || 8787);
const failStepId = process.env.MOCK_FAIL_STEP || undefined;
const riskScorerUrl = process.env.RISK_SCORER_URL || DEFAULT_RISK_SCORER_URL;

async function scoreWithLog(job: Job, step: Step) {
  log(`scoring ${step.step_id} via ${riskScorerUrl}/score`);
  const risk = await scoreStep(riskScorerUrl, job, step);
  log(`${step.step_id} risk: ${risk.risk_tier} "${risk.risk_reason}" (${risk.risk_scored_by})`);
  return risk;
}

const mock = new MockBackend({ failStepId, scoreStep: scoreWithLog });
let currentJobId: string | null = null;
let sockets = new Set<WebSocket>();
let streamStarted = false;

// ---- little http helpers ----

function sendJson(response: http.ServerResponse, status: number, body: unknown) {
  response.writeHead(status, {
    "Content-Type": "application/json",
    // the app runs on another port (3000), so let the browser call us
    "Access-Control-Allow-Origin": "*",
  });
  response.end(JSON.stringify(body));
}

function sendError(response: http.ServerResponse, status: number, message: string) {
  sendJson(response, status, { error: message });
}

async function readJson(request: http.IncomingMessage): Promise<unknown> {
  let text = "";
  for await (const chunk of request) text += chunk;
  if (!text) return {};
  return JSON.parse(text);
}

function log(message: string) {
  console.log(`[mock-server] ${message}`);
}

// ---- the WebSocket side ----

function broadcast(event: JobEvent) {
  const message = JSON.stringify(event);
  for (const socket of sockets) {
    if (socket.readyState === socket.OPEN) socket.send(message);
  }
}

function closeAllSockets() {
  for (const socket of sockets) socket.close(1000, "new job started");
  sockets = new Set();
}

// ---- routes ----

async function handle(request: http.IncomingMessage, response: http.ServerResponse) {
  const method = request.method ?? "GET";
  const path = (request.url ?? "/").split("?")[0];
  const parts = path.split("/").filter((part) => part !== "");

  if (method === "OPTIONS") {
    // CORS preflight for the JSON POSTs
    response.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    });
    response.end();
    return;
  }

  log(`${method} ${path}`);

  // GET /devices/:device_id
  if (method === "GET" && parts.length === 2 && parts[0] === "devices") {
    const device = sampleDevice(parts[1]);
    if (parts[1] !== device.device_id) return sendError(response, 404, `no device ${parts[1]} (mock has inkbird-01 and ${device.device_id})`);
    return sendJson(response, 200, device);
  }

  // POST /jobs
  if (method === "POST" && parts.length === 1 && parts[0] === "jobs") {
    const body = (await readJson(request)) as Partial<CreateJobRequest>;
    if (typeof body.device_id !== "string" || typeof body.request !== "string" || !body.request.trim()) {
      return sendError(response, 400, "need device_id and request");
    }
    // a new job replaces the old one, so drop anyone still watching the old stream
    closeAllSockets();
    streamStarted = false;
    const job = await mock.createJob(body as CreateJobRequest);
    currentJobId = job.job_id;
    log(`job ${job.job_id} created: "${job.request}"`);
    return sendJson(response, 201, job);
  }

  // POST /debug/drop-sockets: not in the contract. Cuts every live stream, to test
  // (or demo) the app reconnecting and catching up
  if (method === "POST" && path === "/debug/drop-sockets") {
    const count = sockets.size;
    for (const socket of sockets) socket.terminate();
    return sendJson(response, 200, { dropped: count });
  }

  // everything else is /jobs/:job_id/...
  if (parts[0] !== "jobs" || parts.length < 2) return sendError(response, 404, `no route for ${method} ${path}`);
  const jobId = parts[1];
  if (jobId !== currentJobId) return sendError(response, 404, `no job ${jobId}`);

  // GET /jobs/:job_id
  if (method === "GET" && parts.length === 2) {
    return sendJson(response, 200, await mock.getJob(jobId));
  }

  // POST /jobs/:job_id/steps/:step_id/approval
  if (method === "POST" && parts.length === 5 && parts[2] === "steps" && parts[4] === "approval") {
    const body = (await readJson(request)) as Partial<ApprovalDecision>;
    if (body.decision !== "approve" && body.decision !== "reject") {
      return sendError(response, 400, 'decision must be "approve" or "reject"');
    }
    log(
      `step ${parts[3]}: ${body.decision}${body.decision === "reject" ? ` -> ${body.on_reject ?? "pause"}` : ""}` +
        ` (${body.approval_type ?? "?"}, ${body.time_to_decide_ms ?? "?"}ms, by ${body.decided_by ?? "?"} at ${body.decided_at ?? "?"})`,
    );
    if (body.decision === "reject" && body.on_reject === "replan") {
      log("replan requested (no replanner in the mock yet, holding like a pause)");
    }
    await mock.decide(jobId, parts[3], body as ApprovalDecision);
    return sendJson(response, 200, await mock.getJob(jobId));
  }

  // POST /jobs/:job_id/stop
  if (method === "POST" && parts.length === 3 && parts[2] === "stop") {
    await mock.stop(jobId);
    return sendJson(response, 200, await mock.getJob(jobId));
  }

  return sendError(response, 404, `no route for ${method} ${path}`);
}

const server = http.createServer((request, response) => {
  handle(request, response).catch((err) => {
    log(`error: ${err instanceof Error ? err.message : String(err)}`);
    if (!response.headersSent) sendError(response, err instanceof SyntaxError ? 400 : 500, String(err));
  });
});

// WS /jobs/:job_id/events
const wss = new WebSocketServer({ noServer: true });
server.on("upgrade", (request, socket, head) => {
  const match = (request.url ?? "").match(/^\/jobs\/([^/?]+)\/events/);
  if (!match || match[1] !== currentJobId) {
    socket.write("HTTP/1.1 404 Not Found\r\n\r\n");
    socket.destroy();
    return;
  }
  const jobId = match[1];

  wss.handleUpgrade(request, socket, head, (client) => {
    sockets.add(client);
    log(`ws connected to ${jobId} (${sockets.size} watching)`);
    client.on("close", () => {
      sockets.delete(client);
    });

    // the fake run starts when the first watcher shows up, like the in-browser mock
    if (!streamStarted) {
      streamStarted = true;
      mock.subscribe(jobId, { onEvent: broadcast });
    }
  });
});

server.listen(MOCK_PORT, () => {
  log(`listening on http://localhost:${MOCK_PORT}${failStepId ? ` (step ${failStepId} will fail)` : ""}`);
});
