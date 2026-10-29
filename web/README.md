# Physical MCP web app

The human side of Physical MCP: type a job, watch the camera view with detected controls, follow the plan step by step, approve risky steps, and read the action log.

> Built during the Meta Global AI Developer Hackathon, Oct 27 to Nov 4, 2026

Web app and approval layer by Bhavi. Backend, vision, MCP server and servo hardware by Pam.

## Run it on mock data

Both options replay one of Pam's fixtures: `shared/fixtures/inkbirdJobSample.json` for `inkbird-01` (the default device, step 1 waits for approval) or `jobSample.json` for anything else (the step 2 retry, the step 6 approval), so every UI state works before the hardware does. The sample job starts by itself when the page loads. Open http://localhost:3000.

```bash
npm install
npm run dev        # quickest: the mock runs inside the browser
npm run dev:mock   # mock server on :8787 + the app, over real HTTP + WebSocket
```

`dev:mock` is the one to use when touching `lib/backend.ts`, since it runs the same client code the real backend will. Ctrl+C stops both servers.

- `?fail=s4` (in-browser mock) or `MOCK_FAIL_STEP=s4 npm run dev:mock` makes step s4 fail
- `NEXT_PUBLIC_MOCK_INJECT_RETRY` (default `true`) adds a **mock-only** retry on Inkbird s2: the first read is `TS 79.0`, it presses ▲ once more, then reads `TS 80.0`. Pam's Inkbird fixture has no retries yet, this is so the retry UI shows up. Set it to `false` once the real backend sends real retries (in `.env.local` for `npm run dev`, or `NEXT_PUBLIC_MOCK_INJECT_RETRY=false npm run dev:mock`)
- The Inkbird camera frame is a placeholder still, `public/mock/inkbird-01.svg` (2560x1440, controls drawn at the fixture's bboxes so the boxes line up). Not a photo
- Approval steps go to the risk scorer (`python risk/server.py`). If it can't answer (not running, Baseten down or out of credits) the mock uses the fixture's tier, or high when the fixture has none, and the card says "mock score"
- `curl -X POST localhost:8787/debug/drop-sockets` cuts the WebSocket mid-run to watch the app reconnect and catch up
- Reject on the approval card stops the job without pressing START
- Stop retracts the finger (in the mock it just ends the job)

## Connect to Pam's backend

Copy `.env.example` to `.env.local`, flip the one flag, and restart `npm run dev`:

```
NEXT_PUBLIC_USE_MOCK=false
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
NEXT_PUBLIC_DEVICE_ID=inkbird-01
```

The app then uses the endpoints in `shared/types.ts`: `POST /jobs`, `GET /devices/:id`, approval and stop POSTs, and the `/jobs/:id/events` WebSocket (reconnects on drop).

## Where things live

| File | What it does |
|---|---|
| `../shared/types.ts` | Pam's contract, imported directly as `@contract/types` (no copy to keep in sync) |
| `lib/config.ts` | `USE_MOCK` flag, backend URL, device id |
| `lib/backend.ts` | HTTP + WebSocket client (checks every event, reconnects and catches up), picks mock or real |
| `lib/mockBackend.ts` | Fake run built from `shared/fixtures/inkbirdJobSample.json` or `jobSample.json`, picked by device id |
| `mock-server/` | Mock backend over HTTP + WebSocket (`npm run mock-server`), and the `dev:mock` launcher |
| `lib/jobReducer.ts` | Applies events to the job and writes the action log |
| `lib/risk.ts` | `RiskScore` shape only. The Muse Spark Open scorer fills it (Thursday) |
| `lib/mockFrame.ts` | Draws mock camera frames (with the LCD reading) for mock mode |
| `components/CameraView.tsx` | Frame plus bbox overlay (boxes in % of the camera frame, so they scale with it) |
| `components/ApprovalCard.tsx` | Approval card shell: action, target button, empty risk tier/reason slots. Approve/Reject log to the console and send the decision |
| `components/HoldToApprove.tsx` | **Parked**, not rendered. Hold button for high-risk steps |
| `components/StepList.tsx` | Plan with every step state, retries shown inline |
| `components/ActionLog.tsx` | Newest-first log: step, action, screen read, before/after frames (click to enlarge) |

## Not done yet

- Risk tier and reason slots are empty until the Spark Open scorer is wired in (Thursday)
- Hold-to-approve is parked in `HoldToApprove.tsx`, not shown on the card yet
- The mock only acts out `risky_steps`; `every_step` and `plan` modes need backend support
- Rubber-stamp detection (Saturday, on the cut list)
