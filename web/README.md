# Physical MCP web app

The human side of Physical MCP: type a job, watch the camera view with detected controls, follow the plan step by step, approve risky steps, and read the action log.

Built during the Meta Global AI Developer Hackathon (Oct 27 to Nov 4, 2026). Web app and approval layer by Bhavi. Backend, vision, MCP server and servo hardware by Pam.

## Run it

```bash
npm install
npm run dev
```

Open http://localhost:3000. By default it runs on a **mock backend** that replays Pam's `shared/fixtures/jobSample.json` as live events, so every UI state works before the hardware does. The sample job starts by itself when the page loads.

- `?fail=s4` makes step s4 fail, to see the failed state
- Reject on the approval card stops the job without pressing START
- Stop retracts the finger (in the mock it just ends the job)

## Connect to Pam's backend

Copy `.env.example` to `.env.local`, flip the one flag, and restart `npm run dev`:

```
NEXT_PUBLIC_USE_MOCK=false
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
NEXT_PUBLIC_DEVICE_ID=incubator-01
```

The app then uses the endpoints in `shared/types.ts`: `POST /jobs`, `GET /devices/:id`, approval and stop POSTs, and the `/jobs/:id/events` WebSocket (reconnects on drop).

## Where things live

| File | What it does |
|---|---|
| `../shared/types.ts` | Pam's contract, imported directly as `@contract/types` (no copy to keep in sync) |
| `lib/config.ts` | `USE_MOCK` flag, backend URL, device id |
| `lib/backend.ts` | HTTP + WebSocket client, picks mock or real |
| `lib/mockBackend.ts` | Fake run built from `shared/fixtures/jobSample.json` |
| `lib/jobReducer.ts` | Applies events to the job and writes the action log |
| `lib/risk.ts` | `RiskScore` shape only. The Muse Spark Open scorer fills it (Thursday) |
| `lib/mockFrame.ts` | Draws mock camera frames (with the LCD reading) for mock mode |
| `components/CameraView.tsx` | Frame plus bbox overlay (SVG viewBox = camera pixels) |
| `components/ApprovalCard.tsx` | Approval card shell: action, target button, empty risk tier/reason slots. Approve/Reject log to the console and send the decision |
| `components/HoldToApprove.tsx` | **Parked**, not rendered. Hold button for high-risk steps |
| `components/StepList.tsx` | Plan with every step state, retries shown inline |
| `components/ActionLog.tsx` | Newest-first log: step, action, screen read, before/after frames (click to enlarge) |

## Not done yet

- Risk tier and reason slots are empty until the Spark Open scorer is wired in (Thursday)
- Hold-to-approve is parked in `HoldToApprove.tsx`, not shown on the card yet
- The mock only acts out `risky_steps`; `every_step` and `plan` modes need backend support
- Rubber-stamp detection (Saturday, on the cut list)
