# Physical MCP

An API for machines that don't have one. Built for the Meta Global AI Developer Hackathon (Indie Developer track), Oct 27 to Nov 4, 2026.

**Live site:** [physicalmcp.dev](https://physicalmcp.dev), with the 3-minute demo, an interactive look at the rig and a replay you can try

**Demo video:** [watch it here](https://physicalmcp.dev/#video)

## What it does

Lots of machines only have physical buttons and a small screen. Physical MCP lets an AI run them anyway. A webcam watches the panel, servo fingers press the buttons, and every action goes through MCP tools with a human approval gate in front of anything risky.

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

**On the rig, as of Nov 3:** full jobs now work through the app, including "Set T1 to 2 hours and start it" with the hold-to-approve on ST/SP. The rig has one finger per button, with H and ST/SP angles in `backend/devices/timer-01.json`. See [Reliability](#reliability).

The Inkbird temperature controller (`inkbird-01`) is a second profile. It has **not been tested on the rig**: its menu behaviour comes from the manual and every servo angle is still empty. `incubator-01` is a made-up placeholder device that runs on the mock and on FakeHardware, never on the rig.

## Safety rules

All of these live in the backend, not just the web app.

- **Every press goes through `press`.** It refuses anything that isn't the running step's own control, waits on the approval card when the step needs approval, checks the hold, and charges the press budget, all before the hardware moves
- **Press budget:** 30 presses per job. Going over is refused, nothing pressed
- **Hold time is checked inside `press`.** A press has to use the hold the plan was checked with, and nothing over the device's limit goes out. The timer needs a 1 s hold per press and refuses anything over 1.8 s (it auto-repeats at about 2 s)
- **Turning something on or wiping something is always a hold.** A step marked `turns_on_output` (the timer's ST/SP starts the countdown, the Inkbird's heater) or `destructive` (the timer's RESET) needs a press-and-hold approval whatever the risk tier says. The backend refuses a tap for it. The web card also asks for a hold on high-risk and not-yet-scored steps
- **The scorer can only add.** It puts a tier and a reason on a step. It can't turn approval off
- **Scorer failure means high.** No key, a timeout or a bad reply and the step goes out as high, labelled as a fallback. Never low
- **Read confidence.** Vision reads a screen two or three times and votes (the first two reads run at the same time): two reads agree = high, a third read breaks a tie = medium, all three differ (or the read is empty) = low. Every log entry with a screen read shows its level, and so does the approval card
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
| `backend/api.py` | FastAPI server for the web app: planning, scoring, the step runner, approvals, pauses, Stop | Working on FakeHardware and on the rig (see Reliability) |
| `backend/mcp_server.py` | The MCP tools every press and read goes through, with the approval gate in `press` | Working, in memory only (no HTTP transport yet) |
| `backend/planner.py` | Muse Spark planner, plan validation, the timer plan check | Working with the live model |
| `backend/devices/` | Device profiles: buttons, servo angles, holds, notes for the planner | `timer-01` main (H and ST/SP angles set). `inkbird-01` not tested on the rig. `incubator-01` placeholder, no rig |
| `backend/serial_hardware.py`, `firmware/` | ESP32 servo fingers over USB serial, plus the webcam | Working on the bench, see Reliability. Also tested against a fake ESP32 |
| `backend/calibrate.py`, `web/app/calibrate/` | Moves a device's control boxes to a new camera view from 4 clicked corners | Working, unit tested. The boxes on file are still marked placeholder |
| `backend/record_run.py` | Records a run: every event plus every camera frame, into `runs/<job_id>/` | Working, tested against the real HTTP backend |
| `backend/fake_camera.py` | The backend on FakeHardware plus a fake live webcam, to try the camera view with no rig | Working |
| `vision/` | SAM 3.1 control detection, voted screen reads, LED checks, calibration | Reads tested on photos and through the rig camera in the Nov 2 and Nov 3 runs. The boxes on file are marked placeholder (the timer's from a hand-held photo) |
| `risk/` | Muse Spark Open step scorer, CLI and a small HTTP endpoint | Working with the live model. Scored fixtures in `risk/out/` |
| `shared/` | The contract (`types.ts`) and sample jobs both sides build on | v1, with newer fields marked PROPOSED |
| `scripts/dashboard_metrics.py` | Computes every dashboard number from files in this repo and writes `metrics.json`. Anything it can't compute stays empty and says what's missing | Working, unit tested |
| `runs/` | Recorded runs for the dashboard, labelled real or mock in `labels.json` | The Nov 2 and Nov 3 rig runs are in |
| `web/app/results/` | The results page: five tiles to film and every metric underneath, read from `web/public/metrics.json` | Working |
| `site/` | The public site at [physicalmcp.dev](https://physicalmcp.dev): demo video, interactive rig, replays. Plain static files, deployed by hand with Vercel. Built with Muse Code, then changed further with Claude Code. | Live |
| `scripts/readme_cost_table.py` | Writes the README's Cost, Reliability and Speed numbers from `web/public/metrics.json` | Working, unit tested |
| `docs/` | `hardware.md` (how the rig is built, wired and calibrated), `first-loop-checklist.md` (first full run on the rig), `costs.md` (prices and sources) | Current |

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

The fake rig keeps what's on its screen between jobs, like the real timer. After a job presses ST/SP, T1 stays at `01:59` and later T1 jobs get refused, so restart the backend to start clean.

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

The servos run off their own power bank with ground shared with the ESP32. Building, wiring, finding servo angles and troubleshooting: [`docs/hardware.md`](docs/hardware.md). The first full run, step by step: [`docs/first-loop-checklist.md`](docs/first-loop-checklist.md). Firmware: [`firmware/README.md`](firmware/README.md). Web app details and mock flags: [`web/README.md`](web/README.md).

### Dashboard numbers

```bash
.venv/bin/python scripts/dashboard_metrics.py
.venv/bin/python scripts/readme_cost_table.py --write
```

Open `/results` in the web app to see them. The page reads `web/public/metrics.json`, which `scripts/dashboard_metrics.py` writes.

### Tests

The tests need the hardware requirements too (Pillow and OpenCV), but no rig and no API keys.

```bash
.venv/bin/python -m pip install -r backend/requirements.txt -r backend/requirements-hardware.txt
.venv/bin/python -m unittest discover -s backend -p "test_*.py"
.venv/bin/python vision/test_read_screen.py
.venv/bin/python -m unittest scripts/test_dashboard_metrics.py
.venv/bin/python -m unittest scripts/test_readme_cost_table.py
cd web && npx tsc --noEmit
```

On Windows, keep the repo in a short path. Next.js fails with a path length error in very deep folders.

## Reliability

<!-- reliability:start, written by scripts/readme_cost_table.py -->
Presses sent directly from Python, with no AI in the loop:

| Test | Worked | Tried |
|---|---|---|
| H, first rig, 20 presses through Python, 1 s hold (Oct 30) | 20 | 20 |
| ST/SP, rebuilt rig, 10 presses through Python, 1 s hold (Nov 2): start/stop toggles counted on video | 10 | 10 |
| H, rebuilt rig, 20 presses through Python, 1 s hold (Nov 2): T1 read 20:00 00 | 20 | 20 |

Full jobs through the app on the real rig: **7/23 worked** (`real.job_success_rate`).

| Result | Jobs |
|---|---|
| Worked | 7 |
| Stopped by hand | 10 |
| Refused, nothing pressed | 1 |
| Failed, gave up after 3 tries | 3 |
| Failed, hardware error | 2 |
<!-- reliability:end -->

The site says 7/8: the last 8 full jobs on Nov 3, from 19:16 UTC, not counting jobs stopped by hand. The demo video says 6 of 7 because it was made before the last of those runs. Every recorded run is counted in the 7/23 above.

In the Nov 2 runs no H press changed T1: the frames show the H finger landing on the lower edge of the button after the timer was moved. In the Nov 3 runs, full jobs worked. The camera read of T1, the plans, the refusal, the retry and Stop all did their job in the rig runs. The refusal was correct: T1 was already past the time asked for. One hardware error was another program holding the serial port. The other, on Nov 3, was `The read operation timed out`. Reads checked vs. reads right isn't measured yet.

## Speed

Almost all of a job's time is model calls, not presses. From the Nov 2 and Nov 3 rig runs in `runs/`:

<!-- speed:start, written by scripts/readme_cost_table.py -->
| Part | Median | Sample | How it's measured |
|---|---|---|---|
| One press | 1.39 s | 40 presses | step status running to verifying, from step.updated events. Range 1.38 to 1.4 s |
| One screen read (2 or 3 model calls) | 32.48 s | 64 reads | verifying, or the read before, to the next note with a read. Range 7.86 to 99.64 s |
| Reading T1 and planning, together | 56.11 s | 19 jobs | job.created_at to plan.ready ts, includes the pre-plan screen read |
| Risk score, in the background | 9.55 s | 10 scored steps | plan.ready to the first step.updated with risk_tier (upper bound, it's an event ts) |
<!-- speed:end -->

On Nov 2 a read's model calls ran one after another. By Nov 3 the first two ran at the same time and the planner retried at most once, so these medians mix both. So far only the last Nov 3 run has `job.usage`, which records the time of every model call.

## Cost

What the rig costs next to replacing the machine or retrofitting it. Only numbers with a source are here, the rest says so. Every figure, its source page and the date it was checked are in [`docs/costs.md`](docs/costs.md).

<!-- cost:start, written by scripts/readme_cost_table.py -->
**Rig parts:** NEEDS INPUT. Still needs a price for: Jumper wires, Mini breadboard, Olimex BREADBOARD-MINI, Finger materials (arms, hot glue, mounting tape, blocks, base board). a listing URL and a checked date for: Microcontroller (ESP32), Finger materials (arms, hot glue, mounting tape, blocks, base board).

**Rig vs replacing a lab incubator:** not sourced yet (the parts total (cost.hardware_parts_total) has to be REAL first).

Model calls were paid with hackathon credits, so the API figures are cost at list price, not what we spent.

| | Cost |
|---|---|
| API cost per job, at list price | $0.1373 median (screen_read $0.1052, planner $0.0120, scorer $0.0201), from 1 jobs |
| Tokens per job, real runs | 40001 median (planner 4278, screen_read 29649, scorer 6074), from 1 jobs |
| Replace: research lab benchtop incubator | $798.59 to $1,185.00 |
| Replace: legacy manufacturing HMI panel | $1,718.00 |
| Replace: hardware QA touch screen test robot | $12,243.75 to $12,288.75 |
| Replace: building ops temperature controller | $540.52 |
| Replace: greenhouse environment controller | $8,745.00 |
| Replace: commercial fryer | $6,765.00 |
| Replace: warehouse parcel scale | $945.00 |
| Replace: home electric range | $649.00 to $829.00 |
| Retrofit with IoT: a drill press (Bosch Rexroth) | about $2,000 |
| Retrofit with IoT: legacy equipment, typical | a couple thousand dollars, up to $10,000 to $20,000 |
<!-- cost:end -->

## Team

- **Pam** ([@PamAntonio](https://github.com/PamAntonio)): the idea, the first contract (`shared/types.ts` and the first sample jobs, incubator and Inkbird), the first planner and the first device profiles (incubator, Inkbird), the ESP32 firmware, the serial protocol in `backend/serial_hardware.py`, and the rig, including its rebuild, the timer's press angles, the bench reliability numbers and the hardware guide
- **Bhavi** ([@Bhavishaahuja](https://github.com/Bhavishaahuja)): the MCP server, the planner hookup and later planner work (pre-plan reads, the plan check), the backend runner, vision, the risk scorer, the web app, calibration, the live camera, the run recorder, the dashboard metrics and the cost research

## Built during the hackathon

Everything in this repo was written during the hackathon, from Oct 27, except the very first commit: the README was created on Oct 26 (`b06e0dc`, Pam). `web/` started from `create-next-app`.

We used Claude Code as a coding assistant. The Co-Authored-By trailers in the commit history mark some of those commits, not all of them. The site (`site/`) was built with Muse Code, then changed further with Claude Code.
