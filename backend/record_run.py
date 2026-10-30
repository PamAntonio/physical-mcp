#!/usr/bin/env python3
"""Record a run: the job's events plus every camera frame shown while it ran, into runs/<job_id>/.

    python backend/record_run.py                     # wait for the next job, record it to the end
    python backend/record_run.py --job job_1a2b3c4d  # that job (frames only from now on)
    python backend/record_run.py --backend http://localhost:8000 --out runs

Start it BEFORE hitting Run job: the camera frame file gets overwritten every 0.5 s, so frames
can only be saved while they happen. It stops on its own when the job finishes (succeeded,
failed or stopped).

What it writes, so a real run can stand in for the mock sample in the web app later:
    runs/<job_id>/events.json     every JobEvent in order, exactly what the WebSocket sent
    runs/<job_id>/job.json        the finished Job (GET /jobs/<id>)
    runs/<job_id>/frames/0001.jpg each new camera frame while the job ran
    runs/<job_id>/frames.json     [{"file", "ts"}]: when each frame was taken, same clock as the events

Frames come from web/public/frames/<device_id>-latest.jpg, the file the real rig (and
backend/fake_camera.py) keep writing, so run this on the same computer as the backend.
On plain FakeHardware there's no camera, so no frames, just the events.
"""

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAMES_DIR = REPO_ROOT / "web" / "public" / "frames"
RUNS_DIR = REPO_ROOT / "runs"
FINISHED = ("succeeded", "failed", "stopped")
FRAME_CHECK_SECONDS = 0.1  # how often to look for a new frame (just a file stat)
JOB_CHECK_SECONDS = 0.5  # how often to ask the backend how the job's doing


def get_json(backend, path):
    try:
        with urllib.request.urlopen(backend.rstrip("/") + path, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as err:
        raise SystemExit(f"Can't reach the backend at {backend} ({err}). Is backend/api.py running?")


def iso_time(seconds):
    """Same format as the events' ts, so frames and events line up."""
    moment = datetime.fromtimestamp(seconds, timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def wait_for_next_job(backend, give_up_after=None):
    """The id of the first job created after we started looking."""
    already = set()
    for job in get_json(backend, "/jobs"):
        already.add(job["job_id"])
    print("Waiting for the next job... (hit Run job in the web app)")
    started = time.monotonic()
    while True:
        for job in get_json(backend, "/jobs"):
            if job["job_id"] not in already:
                return job["job_id"]
        if give_up_after is not None and time.monotonic() - started > give_up_after:
            raise SystemExit("No new job showed up, gave up.")
        time.sleep(JOB_CHECK_SECONDS)


class FrameSaver:
    """Copies the latest frame file every time it changes."""

    def __init__(self, source, folder):
        self.source = Path(source)
        self.folder = Path(folder)
        self.saved = []  # [{"file", "ts"}]
        self.last_seen = None

    def check(self):
        try:
            info = self.source.stat()
        except FileNotFoundError:
            return  # no camera (yet)
        seen = (info.st_mtime_ns, info.st_size)
        if seen == self.last_seen:
            return
        name = f"{len(self.saved) + 1:04d}{self.source.suffix}"
        try:
            # the writer swaps whole files in (os.replace), so this is an old or a new frame, never half
            shutil.copyfile(self.source, self.folder / name)
        except OSError:
            return  # caught it mid-swap, it'll still be new next time
        self.last_seen = seen
        self.saved.append({"file": f"frames/{name}", "ts": iso_time(info.st_mtime)})


def record(backend, job_id=None, out_dir=RUNS_DIR, frames_dir=FRAMES_DIR, give_up_after=None):
    """Record one job until it finishes. Returns the folder it wrote."""
    if job_id is None:
        job_id = wait_for_next_job(backend, give_up_after)
    job = get_json(backend, f"/jobs/{job_id}")
    device_id = job["device"]["device_id"]

    folder = Path(out_dir) / job_id
    (folder / "frames").mkdir(parents=True, exist_ok=True)
    frames = FrameSaver(Path(frames_dir) / f"{device_id}-latest.jpg", folder / "frames")
    print(f"Recording {job_id} ({device_id}): \"{job['request']}\" -> {folder}")

    last_job_check = 0.0
    while True:
        frames.check()
        if time.monotonic() - last_job_check >= JOB_CHECK_SECONDS:
            last_job_check = time.monotonic()
            job = get_json(backend, f"/jobs/{job_id}")
            if job["status"] in FINISHED:
                break
        time.sleep(FRAME_CHECK_SECONDS)
    frames.check()  # the very last frame

    events = get_json(backend, f"/jobs/{job_id}/history")
    (folder / "events.json").write_text(json.dumps(events, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "frames.json").write_text(json.dumps(frames.saved, indent=2) + "\n", encoding="utf-8")

    outcome = (job.get("result") or {}).get("outcome", job["status"])
    print(f"Done: {outcome}. {len(events)} events, {len(frames.saved)} frames in {folder}")
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record a job's events and camera frames into runs/<job_id>/")
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--job", help="record this job id instead of waiting for the next one")
    parser.add_argument("--out", type=Path, default=RUNS_DIR)
    parser.add_argument("--frames", type=Path, default=FRAMES_DIR, help="where the backend writes camera frames")
    args = parser.parse_args(argv)
    try:
        record(args.backend, job_id=args.job, out_dir=args.out, frames_dir=args.frames)
    except KeyboardInterrupt:
        print("\nStopped recording, nothing written for this job.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
