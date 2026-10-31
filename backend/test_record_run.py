"""Test for record_run.py: a whole job on FakeHardware (with fake_camera.py's fake webcam),
over real HTTP, recorded into a temp folder. No rig, no API key. Needs Pillow for the frames.
Run from the repo root:
    python -m unittest backend/test_record_run.py -v
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

os.environ.pop("HARDWARE", None)  # always the fake here

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import uvicorn  # noqa: E402

import api  # noqa: E402
import fake_camera  # noqa: E402
import record_run  # noqa: E402
from test_mcp_server import fixture_plan, stub_score  # noqa: E402


class QuickFakeCamera(fake_camera.FakeCameraHardware):
    """The fake webcam rig, with SET's 3 s holds cut to 1 s so the test doesn't drag."""

    def press(self, control_id, count, hold_ms=None):
        super().press(control_id, count, hold_ms / 3 if hold_ms else None)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def post_json(url, body):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


class RecordRunTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.frames_dir = Path(self.folder.name) / "frames"
        self.runs_dir = Path(self.folder.name) / "runs"
        self.saved = (fake_camera.FRAMES_DIR, api.hardware, api.make_plan, api.scorer.score_step_with_raw, api.LIVE_FRAMES)
        fake_camera.FRAMES_DIR = self.frames_dir  # the fake webcam writes into the temp folder
        api.hardware = QuickFakeCamera("inkbird-01")
        api.make_plan = lambda device_id, request, screen_readings=None: fixture_plan()
        api.scorer.score_step_with_raw = stub_score
        api.LIVE_FRAMES = True  # the frame loop, like HARDWARE=serial
        api.runs.clear()
        api.current_run = None

        # the real backend over HTTP, on a spare port, in a thread
        self.backend = f"http://127.0.0.1:{free_port()}"
        port = int(self.backend.rsplit(":", 1)[1])
        self.server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
        self.server_thread = threading.Thread(target=self.server.run, daemon=True)
        self.server_thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                self.fail("backend didn't start")
            time.sleep(0.05)

    def tearDown(self):
        # stop any job still going (test_lists_jobs never finishes its own), so nothing is
        # mid-read on the fake camera when the temp folder goes
        for run in list(api.runs.values()):
            if not api.is_finished(run):
                post_json(f"{self.backend}/jobs/{run.job['job_id']}/stop", {})
        self.server.should_exit = True
        self.server_thread.join(timeout=10)
        # and let a frame write that already started finish, Windows won't delete a folder mid-write
        api.hardware_pool.submit(lambda: None).result(timeout=10)
        api.camera_pool.submit(lambda: None).result(timeout=10)
        fake_camera.FRAMES_DIR, api.hardware, api.make_plan, api.scorer.score_step_with_raw, api.LIVE_FRAMES = self.saved
        api.runs.clear()
        api.current_run = None
        self.folder.cleanup()

    def wait_for_status(self, job_id, wanted, seconds=20):
        deadline = time.monotonic() + seconds
        while True:
            job = record_run.get_json(self.backend, f"/jobs/{job_id}")
            if job["status"] in wanted:
                return job
            if time.monotonic() > deadline:
                self.fail(f"job stuck at {job['status']}, wanted {wanted}")
            time.sleep(0.1)

    def test_records_a_whole_job(self):
        # start the recorder first, like Pam will: it waits for the next job
        recorded = {}

        def run_recorder():
            recorded["folder"] = record_run.record(self.backend, out_dir=self.runs_dir,
                                                   frames_dir=self.frames_dir, give_up_after=20)

        recorder = threading.Thread(target=run_recorder)
        recorder.start()
        time.sleep(0.5)

        job = post_json(f"{self.backend}/jobs", {"device_id": "inkbird-01", "request": "Set the heater to turn on at 80 F"})
        job_id = job["job_id"]
        self.wait_for_status(job_id, ("awaiting_approval",))
        time.sleep(1.2)  # a couple of frames while the card is up
        post_json(f"{self.backend}/jobs/{job_id}/steps/s1/approval", {"decision": "approve", "approval_type": "hold"})
        self.wait_for_status(job_id, ("succeeded", "failed", "stopped"))
        recorder.join(timeout=20)
        self.assertFalse(recorder.is_alive(), "the recorder should stop by itself when the job finishes")

        folder = recorded["folder"]
        self.assertEqual(folder, self.runs_dir / job_id)

        # the events: everything the WebSocket sent, from the very first one
        events = json.loads((folder / "events.json").read_text(encoding="utf-8"))
        kinds = []
        for event in events:
            kinds.append(event["type"])
            self.assertEqual(event["job_id"], job_id)
        self.assertEqual(kinds[0], "job.status")
        self.assertEqual(events[0]["status"], "planning")
        self.assertIn("plan.ready", kinds)
        self.assertEqual(kinds[-1], "job.finished")
        self.assertEqual(events[-1]["result"]["outcome"], "succeeded")
        self.assertEqual(events, record_run.get_json(self.backend, f"/jobs/{job_id}/history"))

        # the finished job
        saved_job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_job["status"], "succeeded")
        self.assertEqual(len(saved_job["plan"]["steps"]), 4)

        # the frames: one per new camera frame, each a real file, in time order inside the job
        frames = json.loads((folder / "frames.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(frames), 5, f"only {len(frames)} frames for a ~4 s job")
        names = []
        for frame in frames:
            path = folder / frame["file"]
            self.assertTrue(path.is_file(), frame)
            self.assertGreater(path.stat().st_size, 1000)
            names.append(frame["file"])
        self.assertEqual(len(set(names)), len(names), "no frame saved twice")
        times = []
        for frame in frames:
            times.append(frame["ts"])
        self.assertEqual(times, sorted(times))
        # same format as the events' ts, so the two line up on one clock
        self.assertRegex(times[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertRegex(events[0]["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")

    def test_lists_jobs(self):
        self.assertEqual(record_run.get_json(self.backend, "/jobs"), [])
        job = post_json(f"{self.backend}/jobs", {"device_id": "inkbird-01", "request": "Set the heater to turn on at 80 F"})
        listed = record_run.get_json(self.backend, "/jobs")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["job_id"], job["job_id"])
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{self.backend}/jobs/job_nope/history", timeout=5)


if __name__ == "__main__":
    unittest.main()
