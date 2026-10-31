# First full loop on the rig

For Pam, Mac. The device is the kitchen timer (`timer-01`): two fixed fingers, servo 0 on
**H** and servo 1 on **ST/SP**, no pan. Every press is a **1 s hold** (a 250 ms tap doesn't
click, 2 s makes it auto-repeat, and the backend refuses anything over 1.8 s). Run everything
from the repo root unless it says `cd web`. Copy the blocks as they are.

## 1. Setup

```bash
git checkout main && git pull
python3 --version                 # needs 3.11 or newer. if not: brew install python@3.12, then use python3.12 below
python3 -m venv .venv             # already have one? it's fine to run again
```

**Intel Mac only** (Apple menu > About This Mac says "Intel"): pin cryptography first, then
the requirements:

```bash
.venv/bin/python -m pip install "cryptography<49"     # Intel Mac only, before the line below
.venv/bin/python -m pip install -r backend/requirements.txt -r backend/requirements-hardware.txt
ls /dev/cu.*                      # the ESP32 is the cu.usbserial-... (or cu.SLAB_USBtoUART / cu.wchusbserial...) one
```

**Power:** both servos run off their own power bank, not the ESP32's 5V pin. Wire the power
bank's ground to the ESP32's GND too (shared ground), or the servos jitter and the board
browns out and reboots mid-press.

Add these to the repo-root `.env` (fill in your own values, never commit this file):

```bash
HARDWARE=serial
DEVICE_ID=timer-01
SERIAL_PORT=/dev/cu.usbserial-XXXX
CAMERA_INDEX=0
MODEL_API_KEY=your-model-api-key
BASETEN_API_KEY=your-baseten-key
```

**Close Arduino's Serial Monitor.** Only one program can hold the port.

The bench commands below don't read `.env` on their own, so load it into the terminal first:

```bash
set -a; source .env; set +a
```

## 2. Bench checks, in this order

**Servo angles.** In `backend/devices/timer-01.json`, `btn_h` is done (servo 0, rest 150,
press 50). `btn_stsp` (servo 1) still has `rest_deg` and `press_deg` as `null`, so pressing
ST/SP fails until they're filled in. Find them with `angle` (see `firmware/README.md`), write
them into `btn_stsp`, and don't add `pan_deg` / `tilt_*` anywhere: the timer has no pan.

The bench `press` command doesn't read the profile's hold, so **always pass `--hold 1000`**
(without it, it taps for 250 ms and nothing clicks):

```bash
.venv/bin/python backend/serial_hardware.py ping                          # expect: PONG: the ESP32 is answering
.venv/bin/python backend/serial_hardware.py angle 1 120                   # ST/SP finger: step until it just clicks
.venv/bin/python backend/serial_hardware.py frame                         # saves web/public/frames/timer-01-latest.jpg
open web/public/frames/timer-01-latest.jpg                                # the whole timer in view, all 4 rows sharp?
.venv/bin/python backend/serial_hardware.py press btn_h --hold 1000       # T1 should go up by 1 hour
.venv/bin/python backend/serial_hardware.py press btn_stsp --hold 1000    # T1 starts counting down
.venv/bin/python backend/serial_hardware.py press btn_stsp --hold 1000    # and stops again
```

Then power-cycle the timer so T1 is the picked channel and every row reads `00:00 00`. The
rig can't press T1 to T4, so the planner always works on T1.

**Frames for Bhavi (the vision boxes).** If the camera moved since the timer's boxes in
`vision/calibration/timer-01.boxes.json` were made, take one frame from the rig mount,
exactly where it stays, and send it to Bhavi:

```bash
.venv/bin/python backend/serial_hardware.py frame --out ~/Desktop/timer-rig.jpg
```

**Don't move the camera after that.** When Bhavi says the boxes are pushed (or if they're
already right):

```bash
git pull
.venv/bin/python backend/serial_hardware.py --device timer-01 read screen_t1   # should print 00:00 00
```

(Don't run `read_screen.py --calibrate` after the pull: it offers SAM's guesses for every box,
and Enter would write them over Bhavi's.)

## 3. Start everything (3 terminals)

```bash
# terminal 1: backend
.venv/bin/python backend/api.py
# expect: [api] live camera: saving a frame every 0.5 s
```

```bash
# terminal 2: web app
cd web && npm install
NEXT_PUBLIC_USE_MOCK=false NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 NEXT_PUBLIC_DEVICE_ID=timer-01 npm run dev
# open http://localhost:3000: "live" badge top left, camera view moving
```

```bash
# terminal 3: recorder. start it BEFORE Run job, it stops by itself when the job ends
.venv/bin/python backend/record_run.py
# writes runs/<job_id>/: events.json, job.json, frames/
```

## 4. The job

The page opens on the kitchen timer (or pick it next to the request box). Type
`Set T1 to 2 hours and start it`, hit **Run job**. What should happen:

- [ ] Before the plan, terminal 1 prints four `before planning, screen_t1 reads '00:00 00' ...`
      lines (one per row): the planner starts from what the camera sees, not a fixed guess
- [ ] The plan shows up, about 10 s after those reads: H, H, then ST/SP
- [ ] Each H press is a 1 s hold, and T1 reads `01:00 00`, then `02:00 00` (each step turns
      green, "verified")
- [ ] ST/SP stops at an approval card. The finger doesn't move until you approve
- [ ] After ST/SP, T1 reads `01:59 ...` (counting down). Job says "succeeded"
- [ ] The camera view keeps moving during every press

Try it once more with T1 already showing `01:00 00`: power-cycle the timer, press H once by
hand (hold it about 1 s), then run the same job. The plan should be **one** H, then ST/SP.

And one it can't do: `Set T2 to 5 minutes`. It should end "Can't do this on this rig: ..."
with nothing pressed (no finger on T2 or the minute buttons).

## 5. If a step fails

1. Hit **STOP** (top right). The finger lifts right away, even mid-hold.
2. Send Bhavi:

```bash
zip -r run-for-bhavi.zip runs/ web/public/frames/timer-01-latest.jpg web/public/frames/timer-01-read.jpg \
  backend/devices/timer-01.json vision/calibration/timer-01.boxes.json
```

   plus everything terminal 1 (`api.py`) printed since Run job (select all, copy), and a
   screenshot of the web page. `timer-01-read.jpg` is the last frame vision read, which is
   usually the answer when a "verify" fails.

## 6. Fallback

If presses miss: demo the full loop on fake hardware, and film the presses that do work as
B-roll.

```bash
# Ctrl+C terminal 1, then one of these. HARDWARE=fake wins over the .env line
HARDWARE=fake .venv/bin/python backend/api.py              # the normal mock still in the camera view
HARDWARE=fake .venv/bin/python backend/fake_camera.py      # a fake live camera instead (counter ticks)
```

The web app stays as it is. For B-roll, film single presses from the bench:
`.venv/bin/python backend/serial_hardware.py press btn_h --hold 1000` (stop the backend first,
it holds the port).

## If you see X

| You see | Do this |
|---|---|
| `pip install` fails building `cryptography` (Intel Mac) | `.venv/bin/python -m pip install "cryptography<49"` first, then the requirements line again |
| `No ESP32 serial port found (ports seen: ...)` | Close Serial Monitor. `ls /dev/cu.*`, put the right one in `SERIAL_PORT`. Nothing listed: another USB cable (charge-only ones are common), or the CP2102 / CH340 driver |
| `ESP32 rebooted mid-command (it printed READY again)`, or `READY` mid-press | Brownout. Servos on their own power bank, its ground wired to the ESP32's GND |
| The button doesn't click on a press | Bench: did you pass `--hold 1000`? In a job every press is already 1 s (`hold_ms` in `timer-01.json`). Still nothing: the press angle is short, step `press_deg` further |
| A number goes up by 2 or more per press | Auto-repeat, the hold is too long. Lower `hold_ms` in `timer-01.json` (top level, or on that one button), never above 1800 |
| `hold_ms ... is over this device's 1800 ms limit` | Something asked for a longer hold. Keep `hold_ms` at 1000 in `timer-01.json` |
| `'btn_stsp' has a servo but no rest_deg/press_deg` | Fill in ST/SP's angles in `timer-01.json` (section 2) |
| `Can't do this on this rig: ...` | Expected for anything needing a button with no finger (T2 to T4, M, S, 10M, 5M, 10S, RESET). Only whole hours on T1 |
| `Can't open camera 0`, or the frame shows your face | `CAMERA_INDEX=1` (then 2) in `.env`, `source` it again, re-run `frame`. Also System Settings > Privacy & Security > Camera: allow Terminal |
| `before planning, couldn't read screen_t1` | The camera read failed, so the planner used the profile's guess (`00:00 00`). Check the frame, then the boxes (send to Bhavi) |
| A step fails with `Expected '01:00 00' on screen_t1, read '...'` | Open `web/public/frames/timer-01-read.jpg`. Did H click? Did the camera move? Send it to Bhavi, who fixes the boxes |
