# Recorded runs

One folder per job, written by `backend/record_run.py`. The dashboard numbers
(`scripts/dashboard_metrics.py`) come from these files, so commit them:

- `<job_id>/job.json`: the finished job
- `<job_id>/events.json`: every event the WebSocket sent, in order
- `<job_id>/frames.json`: when each camera frame was taken

The frames themselves (`<job_id>/frames/`) stay local, `.gitignore` keeps them out.

## Label every run

`labels.json` says which runs happened on what. Add each job id once it's recorded:

```json
{
  "job_1a2b3c4d": "real",
  "job_9f8e7d6c": "mock"
}
```

- `"real"`: the ESP32 rig (`HARDWARE=serial`)
- `"mock"`: FakeHardware

Always label a run filmed with `FAKE_CONFIDENCE`. FakeHardware normally reads with no
confidence, and the script uses that to guess "mock" for unlabelled runs. `FAKE_CONFIDENCE`
fakes the confidences, so an unlabelled one gets counted as real. A label always wins
over the guess.

How to record a run: `docs/first-loop-checklist.md`, "Recording runs for the dashboard".
