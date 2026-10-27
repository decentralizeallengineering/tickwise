"""Small tests on temporary repos, files and snapshots. Run: python3 -m unittest discover -s tests"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tickwise import core


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.env = {**os.environ, "TICKWISE_STATE": str(self.d / "state.json"), "TICKWISE_CONFIG": str(self.d / "none.json"),
                    "TICKWISE_SNAPSHOT": str(self.d / "snap.json"), "PYTHONPATH": str(ROOT)}
        self.env.pop("TICKWISE_OFF", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, "-m", "tickwise", *args], check=False, env=self.env, input=stdin,
                              capture_output=True, text=True, timeout=60)

    def nxt(self, *args):
        r = self.run_cli("next", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(r.stdout)


class Tests(Base):
    def test_no_change_streak_doubles_up_to_ceiling(self):
        got = [self.nxt("--floor", "100", "--ceiling", "700") for _ in range(5)]
        self.assertEqual(got, [100, 200, 400, 700, 700])

    def test_commit_and_ledger_change_shorten_toward_floor(self):
        repo, ledger = self.d / "repo", self.d / "ledger.md"
        repo.mkdir()
        ledger.write_text("a\n")
        g = ["git", "-C", str(repo), "-c", "user.name=Ann Example", "-c", "user.email=ann@example.invalid"]
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "one"], check=True)
        a = ["--floor", "100", "--ceiling", "800", "--repo", str(repo), "--file", str(ledger)]
        self.assertEqual([self.nxt(*a) for _ in range(4)], [100, 200, 400, 800])
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "two"], check=True)
        self.assertEqual(self.nxt(*a), 400)       # new commit: halved
        ledger.write_text("a changed\n")
        os.utime(ledger, (time.time() + 5, time.time() + 5))
        self.assertEqual(self.nxt(*a), 200)       # changed ledger: halved again
        self.assertEqual(self.nxt(*a), 100)       # quiet again: the streak restarts at the floor

    def test_high_usage_forces_an_hour_from_args_or_snapshot(self):
        self.assertEqual(self.nxt("--floor", "60", "--ceiling", "600", "--usage", "92", "--hours-left", "2"), 3600)
        self.assertEqual(self.nxt("--floor", "60", "--ceiling", "600", "--usage", "10", "--hours-left", "4"), 120)
        now = time.time()
        (self.d / "snap.json").write_text(json.dumps({"rate_limits": {"five_hour": {"used_percentage": 85, "resets_at": now + 3600}}}))
        self.assertEqual(self.nxt("--floor", "60", "--ceiling", "600"), 3600)
        # burn rate: 50% used after 3 of 5 hours is on pace; 50% after 1 hour projects past 100%
        self.assertIsNone(core.high_usage(50, 2.0, core.DEFAULTS))
        self.assertIn("projects", core.high_usage(30, 4.0, core.DEFAULTS))

    def test_digest_reports_changed_then_nochange(self):
        f = self.d / "watched.txt"
        f.write_text("x\n")
        cmd = ["--cmd", "cat", str(f)]
        out = [self.run_cli("digest", *cmd).stdout.strip()]
        out.append(self.run_cli("digest", *cmd).stdout.strip())
        f.write_text("y\n")
        out.append(self.run_cli("digest", *cmd).stdout.strip())
        out.append(self.run_cli("digest", "--cmd", "false").stdout.strip())
        self.assertEqual(out, ["CHANGED", "NOCHANGE", "CHANGED", "CHANGED"])

    def test_guard_raises_wakeup_and_refuses_fast_cron(self):
        hook = lambda name, ti: self.run_cli("guard", stdin=json.dumps({"tool_name": name, "tool_input": ti}))
        r = hook("ScheduleWakeup", {"delaySeconds": 60, "prompt": "tick"})
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["updatedInput"]["delaySeconds"], 900)
        self.assertEqual(out["updatedInput"]["prompt"], "tick")
        self.assertEqual(hook("ScheduleWakeup", {"delaySeconds": 7200}).stdout, "")
        self.assertEqual(hook("ScheduleWakeup", {"stop": True}).stdout, "")
        deny = json.loads(hook("CronCreate", {"cron": "*/5 * * * *"}).stdout)["hookSpecificOutput"]
        self.assertEqual(deny["permissionDecision"], "deny")
        self.assertEqual(hook("CronCreate", {"cron": "7,37 * * * *"}).stdout, "")   # 30 min gap is above 900 s
        self.assertEqual(hook("CronCreate", {"cron": "0 */2 * * *"}).stdout, "")


if __name__ == "__main__":
    unittest.main()
