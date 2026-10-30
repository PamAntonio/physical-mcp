# First full loop on the rig

For Pam, Mac, tonight. Run everything from the repo root unless it says `cd web`. Copy the
blocks as they are.

## 1. Setup

```bash
git checkout main && git pull
python3 --version                 # needs 3.11 or newer. if not: brew install python@3.12, then use python3.12 below
python3 -m venv .venv             # already have one? it's fine to run again
.venv/bin/python -m pip install -r backend/requirements.txt -r backend/requirements-hardware.txt
ls /dev/cu.*                      # the ESP32 is the cu.usbserial-... (or cu.SLAB_USBtoUART / cu.wchusbserial...) one
```

Add these to the repo-root `.env` (fill in your own values, never commit this file):

```bash
HARDWARE=serial
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

**Servo angles first.** Every angle in `backend/devices/inkbird-01.json` is still `null`, so
every press fails until they're filled in. Find them with `angle` (see `firmware/README.md`),
then write `servo` + `rest_deg` + `press_deg` (or the `pan_deg` / `tilt_*` set) for `btn_set`,
`btn_up`, `btn_down`.

```bash
.venv/bin/python backend/serial_hardware.py ping                # expect: PONG: the ESP32 is answering
.venv/bin/python backend/serial_hardware.py angle 0 90          # step down until the button just clicks
.venv/bin/python backend/serial_hardware.py frame               # saves web/public/frames/inkbird-01-latest.jpg
open web/public/frames/inkbird-01-latest.jpg                    # is it the Inkbird, sharp, whole panel in view?
.venv/bin/python backend/serial_hardware.py press btn_set --hold 3000   # PV should show TS (set-point menu)
# don't touch anything for 10 s: the menu closes on its own without saving
```

**Two frames for Bhavi (the vision boxes).** The Inkbird's boxes in
`vision/calibration/inkbird-01.boxes.json` are still the placeholder drawing's, so screen
reads look in the wrong place, and they can't be fixed from your side. Take two frames and send
them to Bhavi:

```bash
# 1. webcam pointed straight at the Inkbird, whole panel in view
.venv/bin/python backend/serial_hardware.py frame --out ~/Desktop/inkbird-straight.jpg
# 2. webcam back on the rig mount, exactly where it stays tonight, HEAT and COOL both dark
.venv/bin/python backend/serial_hardware.py frame --out ~/Desktop/inkbird-rig.jpg
```

Bhavi makes the boxes and the LED-off baselines from these (the baselines need the right boxes,
so they come from the rig frame too) and pushes them. **Don't move the camera after frame 2.**
When Bhavi says it's pushed:

```bash
git pull
.venv/bin/python backend/serial_hardware.py read screen_pv      # should print what PV shows, e.g. 72.0
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
NEXT_PUBLIC_USE_MOCK=false NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev
# open http://localhost:3000: "live" badge top left, camera view moving
```

```bash
# terminal 3: recorder. start it BEFORE Run job, it stops by itself when the job ends
.venv/bin/python backend/record_run.py
# writes runs/<job_id>/: events.json, job.json, frames/
```

## 4. The job

Type `Set the heater to turn on at 80 F`, hit **Run job**. What should happen:

- [ ] The plan shows up in about 10 s, something like: enter menu, up to 80, save, check HEAT
- [ ] Step 1 (hold SET, the one that turns the heater on) stops at an approval card. It's
      always **press and hold 1.5 s**, whatever the risk tier says: anything that turns
      something on is a hold. A tap won't do it
- [ ] The finger doesn't move until you approve
- [ ] The camera view keeps moving during every press, including the 3 s SET holds
- [ ] After each press the screen gets read and the step turns green ("verified")
- [ ] The action log fills in with each press and screen read
- [ ] HEAT lights up and the lamp on the heating outlet turns on. Job says "succeeded"

## 5. If a step fails

1. Hit **STOP** (top right). The finger lifts right away, even mid-hold.
2. Send Bhavi:

```bash
zip -r run-for-bhavi.zip runs/ web/public/frames/inkbird-01-latest.jpg web/public/frames/inkbird-01-read.jpg \
  backend/devices/inkbird-01.json vision/calibration/inkbird-01.boxes.json
```

   plus everything terminal 1 (`api.py`) printed since Run job (select all, copy), and a
   screenshot of the web page. `inkbird-01-read.jpg` is the last frame vision read, which is
   usually the answer when a "verify" fails.

## 6. Fallback

If presses miss 19 of 20: demo the full loop on fake hardware, and film the presses that do
work as B-roll.

```bash
# Ctrl+C terminal 1, then one of these. HARDWARE=fake wins over the .env line
HARDWARE=fake .venv/bin/python backend/api.py              # the normal mock still in the camera view
HARDWARE=fake .venv/bin/python backend/fake_camera.py      # a fake live camera instead (counter ticks)
```

The web app stays as it is. For B-roll, film single presses from the bench:
`.venv/bin/python backend/serial_hardware.py press btn_up` (stop the backend first, it holds the port).

## If you see X

| You see | Do this |
|---|---|
| `No ESP32 serial port found (ports seen: ...)` | Close Serial Monitor. `ls /dev/cu.*`, put the right one in `SERIAL_PORT`. Nothing listed: another USB cable (charge-only ones are common), or the CP2102 / CH340 driver |
| `ESP32 rebooted mid-command (it printed READY again)`, or `READY` mid-press | Brownout. Power the servo from an external 5V supply, GND shared with the ESP32 |
| `press refused` / `Planning failed` mentioning `hold_ms` (e.g. `needs a hold of 3000 ms but hold_ms is ...`) | Make the hold match in `backend/devices/inkbird-01.json`: `btn_set.hold_ms` and `timed_menu.hold_ms` (same number), restart `api.py` |
| `Can't open camera 0`, or the frame shows your face | `CAMERA_INDEX=1` (then 2) in `.env`, `source` it again, re-run `frame`. Also System Settings > Privacy & Security > Camera: allow Terminal |
| `'btn_set' has no servo position yet` | Fill in its angles in `inkbird-01.json` (section 2) |
| A step fails with `Expected 'TS' on screen_pv, read '...'` | Open `web/public/frames/inkbird-01-read.jpg`. Did the camera move since the rig frame? Send it to Bhavi, who fixes the boxes |
