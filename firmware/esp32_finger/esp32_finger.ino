// Physical MCP: servo finger firmware (ESP32 + MG90S)
//
// Setup (Arduino IDE 2):
//   1. Boards Manager: install "esp32" by Espressif. Board: "ESP32 Dev Module".
//   2. Library Manager: install "ESP32Servo".
//   3. Serial Monitor: 115200 baud, line ending "Newline".
//
// Wiring (servo 0 today): orange -> GPIO 18, red -> VIN/5V, brown -> GND.
// Servos 1-3 are on GPIO 19, 21, 22 for later (they need an external 5V supply
// with its ground tied to the ESP32 GND).
//
// Commands, one per line:
//   PING                         -> PONG
//   ANGLE <servo> <deg>          move one servo (use this to find the press angle)
//   CAL <servo> <rest> <press>   set rest/press angles (kept until reboot)
//   PRESS <servo> <count> [ms]   press <count> times, holding each press [ms]
//                                (default 250 ms). Any new line on serial aborts it, even
//                                mid-hold: the finger lifts at once. Reply: OK PRESS s done=x/y
//   REST                         all servos to rest (backend stop() sends this)
//
// The Python side is backend/serial_hardware.py; it speaks exactly these commands.
//
// Inkbird ITC-308: SET needs a 3-second hold, e.g.  PRESS 0 1 3500
// If the board prints READY again while the servo moves, it rebooted from low power:
// move the servo's red wire to an external 5V supply and share ground.

#include <ESP32Servo.h>

const int NUM_SERVOS = 4;
const int SERVO_PINS[NUM_SERVOS] = {18, 19, 21, 22};
int restAngle[NUM_SERVOS]  = {90, 90, 90, 90};
int pressAngle[NUM_SERVOS] = {65, 65, 65, 65};  // placeholder: calibrate with ANGLE, then CAL

const int DEFAULT_HOLD_MS = 250;
const int RELEASE_MS = 350;  // time to lift off the button between presses

Servo servos[NUM_SERVOS];

bool validServo(int s) { return s >= 0 && s < NUM_SERVOS; }

// Wait up to ms, but return true early if a new command arrives (so stop is quick
// even during the Inkbird's 3-second SET hold).
bool waitOrAbort(int ms) {
  unsigned long start = millis();
  while (millis() - start < (unsigned long)ms) {
    if (Serial.available()) return true;
    delay(5);
  }
  return false;
}

void restAll() {
  for (int i = 0; i < NUM_SERVOS; i++) servos[i].write(restAngle[i]);
}

void setup() {
  Serial.begin(115200);
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  for (int i = 0; i < NUM_SERVOS; i++) {
    servos[i].setPeriodHertz(50);
    servos[i].attach(SERVO_PINS[i], 500, 2400);
  }
  restAll();
  Serial.println("READY");
}

void loop() {
  if (!Serial.available()) return;

  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  char cmd[8] = {0};
  int a = -1, b = -1, c = -1;
  int n = sscanf(line.c_str(), "%7s %d %d %d", cmd, &a, &b, &c);
  String name = String(cmd);
  name.toUpperCase();

  if (name == "PING") {
    Serial.println("PONG");

  } else if (name == "REST") {
    restAll();
    Serial.println("OK REST");

  } else if (name == "ANGLE" && n >= 3 && validServo(a)) {
    int deg = constrain(b, 0, 180);
    servos[a].write(deg);
    Serial.printf("OK ANGLE %d %d\n", a, deg);

  } else if (name == "CAL" && n >= 4 && validServo(a)) {
    restAngle[a] = constrain(b, 0, 180);
    pressAngle[a] = constrain(c, 0, 180);
    servos[a].write(restAngle[a]);
    Serial.printf("OK CAL %d rest=%d press=%d\n", a, restAngle[a], pressAngle[a]);

  } else if (name == "PRESS" && n >= 3 && validServo(a)) {
    int count = constrain(b, 1, 30);
    int hold = (n >= 4) ? constrain(c, 50, 5000) : DEFAULT_HOLD_MS;
    int done = 0;
    for (int i = 0; i < count; i++) {
      servos[a].write(pressAngle[a]);
      bool aborted = waitOrAbort(hold);
      servos[a].write(restAngle[a]);  // always lift, even when aborted
      if (aborted) break;             // an interrupted press does not count as done
      done++;
      if (waitOrAbort(RELEASE_MS)) break;
    }
    Serial.printf("OK PRESS %d done=%d/%d hold=%d\n", a, done, count, hold);

  } else {
    Serial.println("ERR use: PING | ANGLE s deg | CAL s rest press | PRESS s count [ms] | REST");
  }
}
