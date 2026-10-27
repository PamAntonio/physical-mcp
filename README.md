# Physical MCP

An API for machines that don't have one. Built Oct 27 to Nov 4, 2026 for the Meta Global AI Developer Hackathon (Indie Developer track).

## What it does
Lots of machines in labs and factories only have physical buttons and a small screen. Physical MCP lets an AI operate them anyway:

1. You type a job, like "set the incubator to 37 °C and run it for 2 hours"
2. A webcam watches the control panel and SAM 3.1 finds the buttons and the screen
3. Muse Spark plans the steps
4. A servo finger presses the buttons, one step at a time
5. After each press the camera reads the screen to check it worked, and retries if it didn't
6. Risky steps (like START) wait for a human to approve before the finger moves

The AI just calls tools like `press` and `read_screen` over MCP. The finger is hidden behind the API.

## Repo layout
| Folder | What's in it | Status |
|---|---|---|
| `web/` | Web app: job request, camera view with detected controls, plan, approval cards, action log | Working on mock data |
| TBD | MCP server, vision, servo control, planner | In progress |

## Run the web app
cd web
npm install
npm run dev

Open localhost:3000. With no backend set it runs on mock data. See `web/README.md` for connecting to the real backend.

## Team
- **Pam** ([@PamAntonio](https://github.com/PamAntonio)): MCP server, vision (SAM 3.1), servo hardware, Muse Spark orchestration
- **Bhavi** ([@Bhavishaahuja](https://github.com/Bhavishaahuja)): web app and approval layer (risk scoring with Muse Spark Open, hold-to-approve)

## Built during the hackathon
All code in this repo was written during the building period (Oct 27 to Nov 4, 2026). Starter templates are noted where used (`web/` started from `create-next-app`).
