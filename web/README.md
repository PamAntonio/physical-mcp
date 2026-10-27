# Physical MCP web app

The human side of Physical MCP: type a job, watch the camera view with detected controls, follow the plan step by step, approve risky steps, and read the action log.

Built during the Meta Global AI Developer Hackathon (Oct 27 to Nov 4, 2026). Web app and approval layer by Bhavi. Backend, vision, MCP server and servo hardware by Pam.

## Run it

```bash
npm install
npm run dev
```

Open http://localhost:3000. With no backend configured it runs on a **mock backend** that replays `mock/job_sample.json` as live events, so every UI state works before the hardware does.

- `?fail=s4` makes step s4 fail, to see the failed state
- Reject on the approval card stops the job without pressing START
- Stop retracts the finger (in the mock it just ends the job)

## Connect to Pam's backend

Copy `.env.example` to `.env.local` and set:

```
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
NEXT_PUBLIC_DEVICE_ID=incubator-01
```

The app then uses the endpoints in `lib/types.ts`: `POST /jobs`, `GET /devices/:id`, approval and stop POSTs, and the `/jobs/:id/events` WebSocket (reconnects on drop).

## Where things live

| File | What it does |
|---|---|
| `lib/types.ts` | Pam's contract, copied as is. Update it when the contract changes |
| `lib/backend.ts` | HTTP + WebSocket client, picks mock or real |
| `lib/mockBackend.ts` | Fake run built from `job_sample.json` |
| `lib/jobReducer.ts` | Applies events to the job and writes the action log |
| `lib/risk.ts` | **Placeholder** risk rules. Swap for the Muse Spark Open scorer |
| `components/CameraView.tsx` | Frame plus bbox overlay (SVG viewBox = camera pixels) |
| `components/ApprovalCard.tsx` | Approval card, hold-to-approve for high risk, measures time to decide |
| `components/StepList.tsx` | Plan with every step state, retries shown inline |
| `components/ActionLog.tsx` | Timestamped log with before/after frames |

## Not done yet

- Risk tier and reason come from fixed rules, not the Spark Open scorer (Thursday)
- `approval_type` and `time_to_decide_ms` are measured but not sent, waiting on contract fields
- The mock only acts out `risky_steps`; `every_step` and `plan` modes need backend support
- Rubber-stamp detection (Saturday, on the cut list)
