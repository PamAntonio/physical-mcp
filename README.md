# Physical MCP

An API for machines that don't have one. Built for the Meta Global AI Developer Hackathon (Indie Developer track), Oct 27 to Nov 4, 2026.

**Demo video:** TODO (link)

## What it does

Lots of machines only have physical buttons and a small screen. Physical MCP lets an AI run them anyway. A webcam watches the panel, a servo finger presses the buttons, and every action goes through MCP tools with a human approval gate in front of anything risky.

The main machine is a 4-channel kitchen timer (`timer-01`). Example job:

> Set T1 to 2 hours and start it

What happens:

1. The camera reads T1 before planning (Muse Spark reads the LCD)
2. Muse Spark plans the steps: press H, press H, press ST/SP
3. The plan gets checked in code against what T1 shows (see the safety rules)
4. Muse Spark Open scores each step that needs approval: low / medium / high, plus a one-line reason
5. H presses run on their own. ST/SP stops at an approval card and waits for a press-and-hold
6. After every press the camera reads T1 again to check it worked. A missed press gets pressed again
7. If vision isn't sure what it read, the job pauses until someone hits Resume

The AI only ever calls tools: `get_controls`, `press`, `read_screen`, `get_frame`, `stop` (`backend/mcp_server.py`). The step runner in `backend/api.py` is the MCP client, in the same process, so one process owns the finger.

On the rig right now: the H finger works (Pam found its angles on Oct 30). ST/SP's angles are still to be found, so a real run stops at ST/SP until they're in `backend/devices/timer-01.json`.

The Inkbird temperature controller (`inkbird-01`) is a second profile. It has **not been tested on the rig**: its menu behaviour comes from the manual and every servo angle is still empty. `incubator-01` is a made-up demo device for the mock only.

## Safety rules

All of these live in the backend, not just the web app.

- **Every press goes through `press`.** It refuses anything that isn't the running step's own control, waits on the approval card when the step needs approval, checks the hold, and charges the press budget, all before the hardware moves
- **Press budget:** 30 presses per job. Going over is refused, nothing pressed
- **Hold time is checked inside `press`.** A press has to use the hold the plan was checked with, and nothing over the device's limit goes out. The timer needs a 1 s hold per press and refuses anything over 1.8 s (it auto-repeats at about 2 s)
- **Turning something on or wiping something is always a hold.** A step marked `turns_on_output` (the timer's ST/SP starts the countdown, the Inkbird's heater) or `destructive` (the timer's RESET) needs a press-and-hold approval whatever the risk tier says. The backend refuses a tap for it. The web card also asks for a hold on high-risk and not-yet-scored steps
- **The scorer can only add.** It puts a tier and a reason on a step. It can't turn approval off
- **Scorer failure means high.** No key, a timeout or a bad reply and the step goes out as high, labelled as a fallback. Never low
- **Read confidence.** Vision reads a screen two or three times and votes: two reads agree = high, a third read breaks a tie = medium, all three differ (or the read is empty) = low. Every log entry with a screen read shows its level, and so does the approval card
- **A low read pauses the job.** Before planning, after a press, on a re-read: nothing gets pressed or verified on it. The job pauses with the reason. Resume reads the screen again first (and pauses again if that's low too). Stop ends it
- **Missed presses get retried, carefully.** On the timer, if T1 still shows exactly what it showed before an H press, on two reads in a row, H gets pressed again. Never on a step that needs approval (pressing ST/SP twice would stop what it started). On the Inkbird, a ▲/▼ step that lands off by a few presses gets corrected, at most 5 presses
- **The plan check refuses impossible requests.** For the timer, the plan is simulated from the real read of T1: each H adds exactly 1 hour, ST/SP starts the countdown. If a step's expected screen doesn't match, or the time set isn't what was asked, the plan is refused with a plain reason and nothing is pressed. Example: T1 shows 01:59 00 and you ask for 2 hours. H only adds hours and there's no finger on RESET, so it says so and asks for a reset by hand. No real read of T1 before planning means no plan either
- **Buttons with no finger are never planned.** On the timer that's everything except H and ST/SP
- **Stop** lifts the finger right away, even mid-hold, and ends the job
- **One job at a time.** A new job stops the one before it

**Reject** sends `on_reject`, a setting in the web app (`NEXT_PUBLIC_REJECT_BEHAVIOR`, default `pause`):
- `pause`: the job pauses at that step, nothing pressed. The step keeps waiting. Approve it later to carry on, or Stop
- `abort`: the step is marked skipped and the job ends as stopped. The finger never moved
- `replan`: there's no replanner yet, so it holds like `pause`

## Models used

| What | Model | Where |
|---|---|---|
| Planning | `muse-spark-1.3` (Meta Model API) | `backend/planner.py` |
| Reading screens | `muse-spark-1.3` (Meta Model API) | `vision/read_screen.py` |
| Finding buttons and screens | `sam-3.1` (Meta Model API) | `vision/detect_controls.py` |
| Risk scoring | `meta-models/muse-spark-open-1026` (Muse Spark Open on Baseten) | `risk/scorer.py` |

LED indicators are a pixel check, no model.

## Repo layout

| Path | What's in it | Status |
|---|---|---|
| `web/` | Next.js app: job request, camera view with control boxes, step list, approval card (risk, scorer, screen read with confidence, hold-to-approve), pause panel with Resume, action log | Working on mock data and on the backend. Checked in a browser at desktop and 390 px |
| `backend/api.py` | FastAPI server for the web app: planning, scoring, the step runner, approvals, pauses, Stop | Working on FakeHardware. On the rig a job stops at ST/SP until its angles are in |
| `backend/mcp_server.py` | The MCP tools every press and read goes through, with the approval gate in `press` | Working, in memory only (no HTTP transport yet) |
| `backend/planner.py` | Muse Spark planner, plan validation, the timer plan check | Working with the live model |
| `backend/devices/` | Device profiles: buttons, servo angles, holds, notes for the planner | `timer-01` main (H angles set, ST/SP pending). `inkbird-01` not tested on the rig. `incubator-01` mock only |
| `backend/serial_hardware.py`, `firmware/` | ESP32 servo finger over USB serial, plus the webcam | H angles found on the rig. Tested against a fake ESP32 |
| `backend/calibrate.py` | Moves a device's control boxes to a new camera view from 4 clicked corners | Working, unit tested. The boxes on file are still placeholders |
| `backend/record_run.py` | Records a run: every event plus every camera frame, into `runs/<job_id>/` | Working, tested against the real HTTP backend |
| `backend/fake_camera.py` | The backend on FakeHardware plus a fake live webcam, to try the camera view with no rig | Working |
| `vision/` | SAM 3.1 control detection, voted screen reads, LED checks, calibration | Reads tested on photos. Boxes are placeholders (the timer's from a hand-held photo). Not tested on the rig camera yet |
| `risk/` | Muse Spark Open step scorer, CLI and a small HTTP endpoint | Working with the live model. Scored fixtures in `risk/out/` |
| `shared/` | The contract (`types.ts`) and sample jobs both sides build on | v1, with newer fields marked PROPOSED |
| `docs/` | `first-loop-checklist.md` (first full run on the rig, timer) and UI screenshots | Current |

## Run it

Needs Python 3.11 or newer (the MCP SDK does) and Node. Keys go in a repo-root `.env`: `MODEL_API_KEY` (planner and screen reads) and `BASETEN_API_KEY` (risk scorer). The mock web app needs no keys.

### Web app on mock data

macOS / Linux:
```bash
cd web
npm install
NEXT_PUBLIC_DEVICE_ID=timer-01 npm run dev      # mock runs in the browser
NEXT_PUBLIC_DEVICE_ID=timer-01 npm run dev:mock # mock server over real HTTP + WebSocket
```

Windows Command Prompt:
```bat
cd web
npm install
set NEXT_PUBLIC_DEVICE_ID=timer-01
npm run dev
```

Open http://localhost:3000. The sample job starts by itself, pauses once on an unclear read (hit Resume), retries one missed H, then waits on ST/SP.

### Backend on FakeHardware (no rig)

macOS / Linux:
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python backend/api.py                  # http://127.0.0.1:8000
```

Windows Command Prompt:
```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
.venv\Scripts\python backend\api.py
```

Then run the web app against it (another terminal):
```bash
cd web
NEXT_PUBLIC_USE_MOCK=false NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 NEXT_PUBLIC_DEVICE_ID=timer-01 npm run dev
```
```bat
cd web
set NEXT_PUBLIC_USE_MOCK=false
set NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
set NEXT_PUBLIC_DEVICE_ID=timer-01
npm run dev
```

FakeHardware flags, to show the interesting parts with no rig. The low read and the missed press each happen once per backend start, so restart the backend before every take.

| Flag | What it does |
|---|---|
| `FAKE_CONFIDENCE=high` (or `medium`, `low`) | Every read reports that confidence. Default: none (the fake has no vision vote) |
| `FAKE_LOW_READ=2` | Only the 2nd read comes back low. On the timer that's the check after the first H, so the job pauses there |
| `FAKE_MISS_PRESS=btn_h` | The 2nd H press doesn't register, so the backend presses it again |
| `FAKE_SCREENS='{"screen_t1": "01:59 00"}'` | T1 starts at 01:59 00 instead of 00:00 00, so "Set T1 to 2 hours" gets refused |

Pause, resume and retry in one job:
```bash
FAKE_CONFIDENCE=high FAKE_LOW_READ=2 FAKE_MISS_PRESS=btn_h .venv/bin/python backend/api.py
```
```bat
set FAKE_CONFIDENCE=high
set FAKE_LOW_READ=2
set FAKE_MISS_PRESS=btn_h
.venv\Scripts\python backend\api.py
```

The refusal from 01:59 00:
```bash
FAKE_CONFIDENCE=high FAKE_SCREENS='{"screen_t1": "01:59 00"}' .venv/bin/python backend/api.py
```
```bat
set FAKE_CONFIDENCE=high
set "FAKE_SCREENS={"screen_t1": "01:59 00"}"
.venv\Scripts\python backend\api.py
```

### On the rig

Add the hardware requirements, then set `HARDWARE=serial` and the device in `.env`. On an **Intel Mac**, pin cryptography first:

```bash
.venv/bin/python -m pip install "cryptography<49"     # Intel Mac only, before the line below
.venv/bin/python -m pip install -r backend/requirements.txt -r backend/requirements-hardware.txt
```
```bat
.venv\Scripts\python -m pip install -r backend\requirements.txt -r backend\requirements-hardware.txt
```

`.env`:
```
HARDWARE=serial
DEVICE_ID=timer-01
SERIAL_PORT=/dev/cu.usbserial-XXXX
CAMERA_INDEX=0
```
(On Windows the port is something like `COM3`.)

The servos run off their own power bank with ground shared with the ESP32. Step by step, including finding servo angles and the bench checks: [`docs/first-loop-checklist.md`](docs/first-loop-checklist.md). Firmware: [`firmware/README.md`](firmware/README.md). Web app details and mock flags: [`web/README.md`](web/README.md).

### Tests

```bash
.venv/bin/python -m unittest discover -s backend -p "test_*.py"
.venv/bin/python vision/test_read_screen.py
cd web && npx tsc --noEmit
```

## Reliability

TODO: presses tried vs. presses that registered on the rig, reads checked vs. reads right. No numbers until they're measured.

## Cost

TODO: cost per job (planner, screen reads, scorer) compared with the alternative. No numbers until they're measured.

## Team

- **Pam** ([@PamAntonio](https://github.com/PamAntonio)): hardware. The servos, the ESP32 firmware, the serial protocol in `backend/serial_hardware.py`, and the rig, including finding the timer's press angles
- **Bhavi** ([@Bhavishaahuja](https://github.com/Bhavishaahuja)): the MCP server, the planner hookup, the backend, vision, the risk scorer, the web app, calibration, the live camera, and the plan check

<!-- TODO: git shows Pam also started the shared contract (shared/types.ts, sample jobs), the first backend/planner.py and the incubator + Inkbird profiles on Oct 27. Decide how to credit that. -->

## Built during the hackathon

Everything in this repo was written during the hackathon, from Oct 27, except the very first commit: the README was created on Oct 26 (`b06e0dc`, Pam). `web/` started from `create-next-app`.
