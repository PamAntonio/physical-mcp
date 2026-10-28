#!/usr/bin/env python3
"""Tiny HTTP endpoint for the risk scorer. Standard library only.

    python risk/server.py              # listens on http://127.0.0.1:8091
    python risk/server.py --port 9000

POST /score   body: {"job": <Job>, "step_id": "s6"}
  -> 200 {"step_id": "s6", "requires_approval": true,
          "risk_tier": "high", "risk_reason": "...", "risk_scored_by": "meta-models/..."}

requires_approval is echoed straight from the step. The scorer can't change it.
If scoring fails the answer is still 200 with tier "high" (the safe default).
Bad requests (no job, unknown step_id) get a 400/404.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import scorer

MAX_BODY_BYTES = 2_000_000


class ScoreHandler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/score":
            self.send_json(404, {"error": "not found, try POST /score"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(400, {"error": "body missing or too big"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self.send_json(400, {"error": "body isn't JSON"})
            return

        job = body.get("job") if isinstance(body, dict) else None
        step_id = body.get("step_id") if isinstance(body, dict) else None
        if not isinstance(job, dict) or not step_id:
            self.send_json(400, {"error": "need {job, step_id}"})
            return

        step = scorer.find_step(job, step_id)
        if step is None:
            self.send_json(404, {"error": f"no step {step_id} in this job's plan"})
            return

        score, raw = scorer.score_step_with_raw(job, step)
        if raw.get("error"):
            self.log_message("scoring %s failed, sent high: %s", step_id, raw["error"])

        result = {"step_id": step_id, "requires_approval": bool(step.get("requires_approval"))}
        result.update(score)
        self.send_json(200, result)


def main():
    parser = argparse.ArgumentParser(description="Risk scorer HTTP endpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ScoreHandler)
    print(f"risk scorer on http://{args.host}:{args.port}  (POST /score)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
