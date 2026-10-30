# Physical MCP
 
An API for machines that don't have one. Built Oct 27 to Nov 4, 2026 for the Meta Global AI Developer Hackathon (Indie Developer track).
 
## What it does
Lots of machines in labs and factories only have physical buttons and a small screen. Physical MCP lets an AI operate them anyway:
 
1. You type a job, like "set the heater to turn on at 80 °F"
2. A webcam watches the control panel and SAM 3.1 finds the buttons and the screen
3. Muse Spark plans the steps
4. Muse Spark Open scores each risky step (low / medium / high, plus a one-line reason)
5. Risky steps wait for a human to approve. High-risk ones need a press and hold, not a click
6. A servo finger presses the buttons, one step at a time
7. After each press the camera reads the screen to check it worked, and retries if it didn't
The AI just calls tools like `press` and `read_screen`. The finger is hidden behind the API.

Muse Spark plans, and every action reaches the machine through MCP tools. The approval gate lives inside press. The step runner in `backend/api.py` is the MCP client and talks to the server in `backend/mcp_server.py` (`get_controls`, `press`, `read_screen`, `get_frame`, `stop`) in memory, in the same process, so one process owns the finger. `press` refuses anything that isn't the running step's own control, waits on the approval card for a risky step, and charges the 30-press budget, all before the hardware moves.
 
Our first test device is an Inkbird temperature controller (`inkbird-01`).
 
## Repo layout
| Folder | What's in it | Status |
|---|---|---|
| `web/` | Next.js app: job request, camera view with detected controls, step list, approval card with risk tier and hold-to-approve, action log | Working on mock data and on the backend |
| `backend/` | FastAPI server that speaks the contract: Muse Spark planner, risk scoring, step runner with self-correcting retries | Working, on fake hardware for now |
| `risk/` | Muse Spark Open step scorer (on Baseten), CLI and a small HTTP endpoint | Working, scored fixtures in `risk/out/` |
| `vision/` | SAM 3.1 control detection, screen reading (voted reads), LED on/off checks, calibration | Spike, not tested on real Inkbird frames yet |
| `shared/` | The contract (`types.ts`) and sample jobs both sides build against | v1 |
| `backend/mcp_server.py` | MCP server: the tools every press and read goes through, with the approval gate in `press` | Working, in memory |
| `firmware/`, `backend/serial_hardware.py` | Servo finger (ESP32) and webcam | In progress |
 
## Run it
 
**Web app on mock data** (no keys needed)
```bash
cd web
npm install
npm run dev        # mock runs in the browser
npm run dev:mock   # mock server over real HTTP + WebSocket
```
Open http://localhost:3000. The sample job starts by itself.
 
**Real backend** (Python 3.11+, the MCP SDK needs it. Needs `MODEL_API_KEY` and `BASETEN_API_KEY` in a repo-root `.env`)
```bash
pip install -r backend/requirements.txt
python backend/api.py              # http://127.0.0.1:8000
```
Then in `web/.env.local` set `NEXT_PUBLIC_USE_MOCK=false` and `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000`, and restart `npm run dev`.
 
Want to see a retry? Start the backend with `FAKE_UNDERSHOOT=1` and the first ▲/▼ step comes up one short, so it presses again and rechecks.
 
More detail (mock flags, env vars, where each file lives) is in `web/README.md`.
 
## Safety rules for the approval layer
- If the scorer fails for any reason (no key, timeout, bad reply) the step is marked high. It never falls back to low
- The scorer only adds a tier and a reason. It can't turn approval off for a step that needs it
- Reject stops the job before the risky press happens
## Team
- **Pam** ([@PamAntonio](https://github.com/PamAntonio)): MCP server, servo hardware, Muse Spark orchestration, backend
- **Bhavi** ([@Bhavishaahuja](https://github.com/Bhavishaahuja)): web app, vision (SAM 3.1 control detection, screen reading) and approval layer (risk scoring with Muse Spark Open, hold-to-approve)
## Built during the hackathon
All code in this repo was written during the building period (Oct 27 to Nov 4, 2026). `web/` started from `create-next-app`. Models used: Muse Spark (planning, screen reading), Muse Spark Open on Baseten (risk scoring), SAM 3.1 (control detection).
