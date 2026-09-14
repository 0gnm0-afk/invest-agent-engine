import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from invest_agent.trading.runner import PipelineRunner
from invest_agent.trading.store import instance_lock

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "morning_fixture.json"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture.json"
        self.fixture.write_bytes(EXAMPLE.read_bytes())
        self.instance = self.root / "instance"
        self.runner = PipelineRunner(self.instance)

    def run_fixture(self, **kwargs):
        return self.runner.run(self.fixture, "2026-09-11", ["KR", "US"], **kwargs)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "invest_agent.cli", *args, "--instance", str(self.instance)], capture_output=True, text=True, encoding="utf-8", check=False)

    def test_checkpoint_resume_freezes_input_and_deduplicates(self):
        stopped = self.run_fixture(stop_after="snapshot")
        self.assertEqual(stopped["state"], "interrupted")
        self.fixture.unlink()  # resume must not reopen the original fixture
        completed = self.runner.resume(stopped["run_id"])
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(len(completed["steps"]), 3)
        again = self.runner.resume(stopped["run_id"])
        self.assertEqual(again["steps"], completed["steps"])
        report = next(self.instance.glob("reports/*/*/report.md")).read_text(encoding="utf-8")
        self.assertIn("합성 테스트 데이터", report)
        self.assertIn("SYNTH_KR", report)
        self.assertIn("SYNTH_US", report)
        self.assertIn("2026-09-10", report)
        self.assertEqual(len(list(self.instance.glob("reports/*/*/report.html"))), 1)

    def test_identical_run_reuses_id_changed_input_creates_new_run(self):
        first = self.run_fixture()
        repeated = self.run_fixture()
        self.assertEqual(first["run_id"], repeated["run_id"])
        self.assertEqual(len(repeated["steps"]), 3)
        data = json.loads(self.fixture.read_text())
        data["observations"][0]["volume"] += 1
        self.fixture.write_text(json.dumps(data))
        changed = self.run_fixture()
        self.assertNotEqual(first["run_id"], changed["run_id"])
        self.assertEqual(len(self.runner.status()["runs"]), 2)

    def test_failed_report_retries_only_failed_stage(self):
        with patch("invest_agent.trading.runner.render_report", side_effect=OSError("test")):
            failed = self.run_fixture()
        self.assertEqual(failed["state"], "failed")
        completed = self.runner.resume(failed["run_id"])
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual([(s["step_key"], s["attempt_no"]) for s in completed["steps"]], [("snapshot", 1), ("summary", 1), ("report", 1), ("report", 2)])

    def test_hard_process_exit_recovers_inflight_attempt(self):
        # Exit inside the report step after writing a file but before its DB commit.
        code = '''
import os, sys
from pathlib import Path
from invest_agent.trading.runner import PipelineRunner
original = PipelineRunner._step
def crash(self, step, *args):
    result = original(self, step, *args)
    if step == "report": os._exit(73)
    return result
PipelineRunner._step = crash
PipelineRunner(Path(sys.argv[1])).run(Path(sys.argv[2]), "2026-09-11", ["KR", "US"])
'''
        child = subprocess.run([sys.executable, "-c", code, str(self.instance), str(self.fixture)], capture_output=True, check=False)
        self.assertEqual(child.returncode, 73, child.stderr)
        with closing(sqlite3.connect(self.instance / "state.sqlite3", isolation_level=None)) as db:
            run_id, state = db.execute("SELECT run_id,state FROM runs").fetchone()
            self.assertEqual(state, "running")
        recovered = self.runner.status(run_id)
        self.assertEqual(recovered["state"], "interrupted")
        result = self.runner.resume(run_id)
        self.assertEqual(result["state"], "succeeded")
        reports = [s for s in result["steps"] if s["step_key"] == "report"]
        self.assertEqual([s["state"] for s in reports], ["interrupted", "succeeded"])
        with closing(sqlite3.connect(self.instance / "state.sqlite3", isolation_level=None)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 3)
        self.assertEqual(len(list(self.instance.glob("reports/*/*/report.md"))), 1)

    def test_live_writer_is_not_recovered_by_status(self):
        stopped = self.run_fixture(stop_after="snapshot")
        with closing(sqlite3.connect(self.instance / "state.sqlite3", isolation_level=None)) as db:
            db.execute("UPDATE runs SET state='running'")
        with instance_lock(self.instance):
            probe = self.cli("status", stopped["run_id"])
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(json.loads(probe.stdout)["state"], "running")
            blocked = self.cli("resume", stopped["run_id"])
            self.assertEqual(blocked.returncode, 1)
            self.assertIn("BusyError", blocked.stderr)
        self.assertEqual(self.runner.resume(stopped["run_id"])["state"], "succeeded")

    def test_corrupted_output_is_not_silently_reused(self):
        first = self.run_fixture()
        next(self.instance.glob("reports/*/*/report.md")).write_text("corrupt")
        self.assertEqual(self.runner.resume(first["run_id"])["state"], "failed")

    def test_invalid_input_does_not_create_run(self):
        raw = json.loads(self.fixture.read_text())
        raw["observations"][0]["complete"] = False
        self.fixture.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            self.run_fixture()
        self.assertFalse((self.instance / "state.sqlite3").exists())

    def test_cli_exit_codes_and_real_fresh_process_resume(self):
        stopped = self.cli("run", "--fixture", str(self.fixture), "--report-date", "2026-09-11", "--stop-after", "summary")
        self.assertEqual(stopped.returncode, 2, stopped.stderr)
        run_id = json.loads(stopped.stdout)["run_id"]
        resumed = self.cli("resume", run_id)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["state"], "succeeded")
        missing = self.cli("resume", "unknown")
        self.assertEqual(missing.returncode, 1)

    def test_changed_code_requires_explicit_new_run(self):
        stopped = self.run_fixture(stop_after="snapshot")
        with patch('invest_agent.trading.runner.code_version', return_value='new'), self.assertRaisesRegex(ValueError, 'Engine changed'):
            self.runner.resume(stopped["run_id"])


if __name__ == "__main__":
    unittest.main()
