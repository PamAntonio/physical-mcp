# Servo finger (ESP32 + MG90S)

The finger that presses the Inkbird's buttons. `esp32_finger/esp32_finger.ino` runs on the
ESP32; `backend/serial_hardware.py` drives it over USB and reads the panel with the webcam.

## Wiring

| Servo wire | ESP32 pin |
|---|---|
| Orange (signal) | GPIO 18 (servo 0). Servos 1-3: GPIO 19, 21, 22 |
| Red (power) | VIN / 5V for one servo. Two or more: external 5V supply |
| Brown (ground) | GND, shared with the external supply if you use one |

Nothing is wired to the Inkbird. The finger only presses its front buttons.

## Flash it

1. Arduino IDE 2, Boards Manager: install **esp32** by Espressif, pick **ESP32 Dev Module**.
2. Library Manager: install **ESP32Servo**.
3. Open `esp32_finger/esp32_finger.ino`, upload, open Serial Monitor at 115200, line ending
   **Newline**. Type `PING`, expect `PONG`.

If the Mac shows no port: try another USB cable (charge-only cables are common), then the
USB driver for the chip printed near the board's USB port (CP2102 or CH340).

## Find the press angle

In Serial Monitor: `ANGLE 0 90`, then `ANGLE 0 85`, `ANGLE 0 80`... until the button just
clicks. Go 3-5 degrees past that, no more (pushing harder stalls the servo). Then save it
for the session with `CAL 0 <rest> <press>` and test with `PRESS 0 1 3500` (SET needs a
3-second hold). Write the angles into `backend/devices/inkbird-01.json`:

```json
"btn_set": { "reachable": true, "servo": 0, "rest_deg": 90, "press_deg": 72, "hold_ms": 3000 }
```

(or `pan_deg` / `tilt_up_deg` / `tilt_press_deg` if the finger rides a pan/tilt bracket).

## Bench test from Python

```bash
pip install -r backend/requirements.txt -r backend/requirements-hardware.txt
python backend/serial_hardware.py ping
python backend/serial_hardware.py press btn_set --hold 3500
python backend/serial_hardware.py frame          # saves web/public/frames/inkbird-01-latest.jpg
python -m unittest backend/test_serial_hardware.py -v   # no board needed
```

Close Arduino's Serial Monitor first: only one program can hold the port.

## Run the backend on the real rig

Add `HARDWARE=serial` to the repo-root `.env` (optionally `SERIAL_PORT=/dev/cu.usbserial-...`,
`CAMERA_INDEX=0`) and start `backend/api.py` as usual. Without `HARDWARE=serial` it stays on
the fake hardware.

## Safety

`POST /jobs/:id/stop` calls `stop()`, which sends `REST`. The firmware aborts a running press
on any new line, even mid-hold, and lifts the finger. If the board prints `READY` while the
servo moves, it rebooted from low power: move the servo to an external 5V supply.
