"""Tests for model usage per job (job["usage"]): the planner's and the scorer's token counts.
No API key, no network: the model replies are fakes shaped like what api.meta.ai and Baseten
really send back (usage.prompt_tokens / usage.completion_tokens). Run from the repo root:
    python -m unittest backend/test_model_usage.py -v
"""

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.pop("HARDWARE", None)  # always the fake here

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import api  # noqa: E402
import planner  # noqa: E402
from test_mcp_server import REQUEST, FakeRequest, RecordingHardware, fixture_plan  # noqa: E402

scorer = api.scorer


def fake_reply(model, prompt_tokens, completion_tokens, content="{}", with_usage=True):
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                            total_tokens=prompt_tokens + completion_tokens)
    message = SimpleNamespace(content=content)
    return SimpleNamespace(model=model, choices=[SimpleNamespace(message=message)],
                           usage=usage if with_usage else None)


class FakeClient:
    """Enough of the OpenAI client: chat.completions.create hands back the next reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        return self.replies.pop(0)


class PlannerUsageTests(unittest.TestCase):
    def tearDown(self):
        planner.usage_log.calls = None

    def test_counts_a_reply_when_asked(self):
        calls = []
        planner.usage_log.calls = calls
        client = FakeClient([fake_reply("muse-spark-1.3", 812, 1430, content='{"steps": []}')])
        self.assertEqual(planner.call_model(client, [{"role": "user", "content": "x"}]), '{"steps": []}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["kind"], "planner")
        self.assertEqual(calls[0]["model"], "muse-spark-1.3")
        self.assertEqual(calls[0]["prompt_tokens"], 812)
        self.assertEqual(calls[0]["completion_tokens"], 1430)
        self.assertEqual(sorted(calls[0]), ["completion_tokens", "kind", "model", "prompt_tokens", "seconds"])
        self.assertGreaterEqual(calls[0]["seconds"], 0)

    def test_off_unless_asked(self):
        client = FakeClient([fake_reply("muse-spark-1.3", 812, 1430)])
        planner.call_model(client, [{"role": "user", "content": "x"}])  # nothing set up, nothing breaks
        self.assertIsNone(getattr(planner.usage_log, "calls", None))

    def test_reply_without_usage_adds_nothing(self):
        calls = []
        planner.usage_log.calls = calls
        planner.call_model(FakeClient([fake_reply("muse-spark-1.3", 1, 1, with_usage=False)]), [])
        self.assertEqual(calls, [])


class ScorerUsageTests(unittest.TestCase):
    def setUp(self):
        self.saved = (scorer.get_client, scorer.structured_outputs_rejected)
        scorer.structured_outputs_rejected = False

    def tearDown(self):
        scorer.get_client, scorer.structured_outputs_rejected = self.saved

    def test_score_carries_its_usage(self):
        reply = '{"risk_tier": "medium", "risk_reason": "turns the heater on"}'
        client = FakeClient([fake_reply("meta-models/muse-spark-open-1026", 1560, 240, content=reply)])
        scorer.get_client = lambda: client
        job = fixture_job()
        score, raw = scorer.score_step_with_raw(job, job["plan"]["steps"][0])
        self.assertEqual(score["risk_tier"], "medium")
        self.assertEqual(len(raw["usage"]), 1)
        self.assertEqual(raw["usage"][0]["kind"], "scorer")
        self.assertEqual(raw["usage"][0]["model"], "meta-models/muse-spark-open-1026")
        self.assertEqual(raw["usage"][0]["prompt_tokens"], 1560)
        self.assertEqual(raw["usage"][0]["completion_tokens"], 240)

    def test_failed_call_has_no_usage(self):
        def broken():
            raise RuntimeError("no key")

        scorer.get_client = broken
        job = fixture_job()
        score, raw = scorer.score_step_with_raw(job, job["plan"]["steps"][0])
        self.assertEqual(score["risk_tier"], "high")
        self.assertEqual(raw["usage"], [])


def fixture_job():
    return {"job_id": "job_test", "request": REQUEST, "device": api.load_device_with_frame("inkbird-01"),
            "plan": fixture_plan(), "status": "planning"}


class VisionReadHardware(RecordingHardware):
    """FakeHardware whose reads come back with usage, the way serial_hardware passes on
    vision's vote: two model calls per read (they agreed)."""

    def __init__(self):
        super().__init__()
        self.reads_seen = 0

    def read_screen_detail(self, control_id):
        detail = super().read_screen_detail(control_id)
        self.reads_seen += 1
        detail["usage"] = []
        for _ in range(2):
            detail["usage"].append({"kind": "screen_read", "model": "muse-spark-1.3", "prompt_tokens": 200,
                                    "completion_tokens": 700, "seconds": 2.5})
        return detail


class PlannerClientTests(unittest.TestCase):
    def test_planner_client_retries_once_at_most(self):
        made_with = {}

        class Stop(Exception):
            pass

        def fake_openai(**kwargs):
            made_with.update(kwargs)
            return object()

        def stop_here(client, messages):
            raise Stop()

        self.addCleanup(setattr, api, "OpenAI", api.OpenAI)
        self.addCleanup(setattr, planner, "call_model", planner.call_model)
        self.addCleanup(os.environ.__setitem__, "MODEL_API_KEY", os.environ.get("MODEL_API_KEY", ""))
        api.OpenAI = fake_openai
        planner.call_model = stop_here
        os.environ["MODEL_API_KEY"] = "test-key"

        with self.assertRaises(Stop):
            api.make_plan("timer-01", "Set T1 to 2 hours and start it", [])
        self.assertEqual(made_with["max_retries"], 1)
        self.assertEqual(made_with["timeout"], api.PLANNER_TIMEOUT_SECONDS)


class JobUsageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = (api.hardware, api.make_plan, scorer.score_step_with_raw)
        api.hardware = RecordingHardware()
        api.runs.clear()
        api.current_run = None

        # a stand-in planner that "gets a reply" the way call_model does, then plans
        def make_plan(device_id, request, screen_readings=None):
            planner.record_usage(fake_reply("muse-spark-1.3", 900, 1500), 4.2)
            return fixture_plan()

        def score(job, step):
            time.sleep(0.05)
            raw = {"usage": [{"kind": "scorer", "model": "meta-models/muse-spark-open-1026", "prompt_tokens": 1600,
                              "completion_tokens": 200, "seconds": 1.8}]}
            return {"risk_tier": "high", "risk_reason": "test score", "risk_scored_by": "test"}, raw

        api.make_plan = make_plan
        scorer.score_step_with_raw = score

    async def asyncTearDown(self):
        for run in list(api.runs.values()):
            if not api.is_finished(run):
                await api.stop_job_now(run, "test over")
        api.hardware, api.make_plan, scorer.score_step_with_raw = self.saved

    async def wait_until(self, check, seconds=10):
        deadline = time.monotonic() + seconds
        while not check():
            if time.monotonic() > deadline:
                self.fail("timed out waiting")
            await asyncio.sleep(0.01)

    async def test_job_collects_planner_and_scorer_calls(self):
        job = await api.create_job(FakeRequest({"device_id": "inkbird-01", "request": REQUEST}))
        self.assertEqual(job["usage"], [])
        run = api.runs[job["job_id"]]
        await self.wait_until(lambda: run.waiting_step_id == "s1" and run.job["plan"]["steps"][0].get("risk_tier"))

        kinds = []
        for call in run.job["usage"]:
            kinds.append(call["kind"])
        # one planner reply, then one score for the one gated step (s1). FakeHardware's reads
        # make no model call, so no screen_read entries
        self.assertEqual(kinds, ["planner", "scorer"])
        self.assertEqual(run.job["usage"][0], {"kind": "planner", "model": "muse-spark-1.3", "prompt_tokens": 900,
                                               "completion_tokens": 1500, "seconds": 4.2})
        self.assertIsNone(getattr(planner.usage_log, "calls", None), "the worker thread's list is put away after")

    async def test_screen_reads_count_too(self):
        api.hardware = VisionReadHardware()
        job = await api.create_job(FakeRequest({"device_id": "inkbird-01", "request": REQUEST}))
        run = api.runs[job["job_id"]]
        await self.wait_until(lambda: run.waiting_step_id == "s1")
        await api.decide(run.job["job_id"], "s1", FakeRequest({"decision": "approve", "approval_type": "hold"}))
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])

        kinds = {"planner": 0, "scorer": 0, "screen_read": 0}
        for call in run.job["usage"]:
            kinds[call["kind"]] += 1
        for event in run.events:
            if event.get("note") and "read" in event["note"]:
                # the read itself doesn't carry usage, only the job does
                self.assertNotIn("usage", event["note"]["read"])
        reads = api.hardware.reads_seen
        self.assertGreater(reads, 0)
        self.assertEqual(kinds, {"planner": 1, "scorer": 1, "screen_read": 2 * reads})

    async def test_refused_plan_still_counts(self):
        def refusing(device_id, request, screen_readings=None):
            planner.record_usage(fake_reply("muse-spark-1.3", 700, 300), 3.0)
            raise api.PlanRefused("T2 can't be picked on this rig")

        api.make_plan = refusing
        job = await api.create_job(FakeRequest({"device_id": "inkbird-01", "request": REQUEST}))
        run = api.runs[job["job_id"]]
        await self.wait_until(lambda: api.is_finished(run))
        self.assertEqual(run.job["status"], "failed")
        self.assertEqual(len(run.job["usage"]), 1)
        self.assertEqual(run.job["usage"][0]["completion_tokens"], 300)


if __name__ == "__main__":
    unittest.main()
