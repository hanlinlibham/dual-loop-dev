"""Integration tests for automated subagent orchestrator and unattended roadmap daemon."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


AGENT_FIXTURE = """\
import csv, hashlib, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

mode = sys.argv[1]
role = os.environ["DUAL_LOOP_ROLE"]
repo = Path(os.environ["DUAL_LOOP_REPO"])
control = Path(os.environ["DUAL_LOOP_CONTROL_ROOT"])
case = Path(os.environ["DUAL_LOOP_CASE_ROOT"])
token = Path(os.environ["DUAL_LOOP_TOKEN_FILE"])
round_num = int(os.environ["DUAL_LOOP_ROUND"])
contract_py = os.environ["DUAL_LOOP_CONTRACT_SCRIPT"]
controller_py = os.environ["DUAL_LOOP_CONTROLLER_SCRIPT"]
baseline_file = Path(os.environ["DUAL_LOOP_BASELINE_FILE"])
marker_dir = Path(sys.argv[2])

# Verify worker never receives authority dir directly
assert token.parent.name == "worker-tokens", f"unexpected token parent: {token}"
assert not (token.parent / "supervisor.key").exists()

if role == "executor":
    (repo / "feature.py").write_text(f"ROUND = {round_num}\\n", encoding="utf-8")
    if mode != "forgot_execute":
        subprocess.run([sys.executable, controller_py, "execute", "--root", str(control), "--token", str(token)], check=False)

elif role == "reviewer":
    if mode == "quota_retry_once":
        flag = marker_dir / "reviewer_interrupted_once"
        if not flag.exists():
            flag.write_text("1", encoding="utf-8")
            raise SystemExit(1)  # Simulate quota exhaustion before verdict

    if mode == "contract_block":
        fb = case / "outputs/fb.md"
        fb.write_text("ATTRIBUTION: contract\\nAllowlist missing test file.\\n", encoding="utf-8")
        subprocess.run([sys.executable, controller_py, "review", "--root", str(control), "--token", str(token), "--verdict", "reject", "--feedback", str(fb)], check=True)
        raise SystemExit(0)

    if mode == "always_reject":
        fb = case / f"outputs/fb-{round_num}.md"
        fb.write_text("ATTRIBUTION: code\\nStill broken.\\n", encoding="utf-8")
        subprocess.run([sys.executable, controller_py, "review", "--root", str(control), "--token", str(token), "--verdict", "reject", "--feedback", str(fb)], check=True)
        raise SystemExit(0)

    # Accept path
    ev = "outputs/ev.txt"
    (case / ev).write_text("verified\\n", encoding="utf-8")
    b_sha = hashlib.sha256(baseline_file.read_bytes()).hexdigest()
    spec = json.loads((case / "spec.json").read_text(encoding="utf-8"))
    with (case / spec["expected"]).open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    obs = {
        "baseline_sha256": b_sha,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "rows": [{"id": r["id"], "status": "PASS", "detail": "ok", "evidence_level": "unit", "evidence": [ev]} for r in rows if r["requirement"] == "required"],
    }
    (case / "outputs/obs.json").write_text(json.dumps(obs), encoding="utf-8")
    subprocess.run([sys.executable, contract_py, "compare", "--root", str(case), "--observations", "outputs/obs.json", "--out", "outputs/rev-comparison.json"], check=True)
    fb = case / "outputs/fb.md"
    fb.write_text("Accepted.\\n", encoding="utf-8")
    subprocess.run([sys.executable, controller_py, "review", "--root", str(control), "--token", str(token), "--verdict", "accept", "--feedback", str(fb), "--report", "outputs/rev-comparison.json"], check=True)
"""


class OrchestratorAndRoadmapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dual-loop-orch-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.agent_script = self.base / "agent_fixture.py"
        self.agent_script.write_text(AGENT_FIXTURE, encoding="utf-8")
        self.markers = self.base / "markers"
        self.markers.mkdir()

        self.repo = self.base / "service"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "orch@example.invalid")
        self.git("config", "user.name", "Orch Fixture")
        (self.repo / "feature.py").write_text("ROUND = 0\n", encoding="utf-8")
        (self.repo / "POLICY.md").write_text("Policy v1\n", encoding="utf-8")
        (self.repo / "profile.json").write_text(
            json.dumps({"schema_version": 1, "name": "svc", "protected_paths": ["POLICY.md", "profile.json"]}),
            encoding="utf-8",
        )
        self.git("add", ".")
        self.git("commit", "-qm", "Seed")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def make_task(self, task_id: str, mode: str, max_rounds: int = 2) -> dict:
        backend = {"kind": "command", "argv": [sys.executable, str(self.agent_script), mode, str(self.markers)]}
        return {
            "id": task_id,
            "title": f"Task {task_id}",
            "repo": str(self.repo),
            "profile": str(self.repo / "profile.json"),
            "policy_files": [str(self.repo / "POLICY.md")],
            "allowed_paths": ["feature.py"],
            "protected_paths": [],
            "checks": [{"id": "syntax", "argv": [sys.executable, "-m", "py_compile", "feature.py"], "cwd": "."}],
            "criteria": [
                {
                    "id": "compiles",
                    "requirement": "required",
                    "description": "Compiles cleanly",
                    "basis": "Spec",
                    "acceptance": "py_compile passes",
                }
            ],
            "max_rounds": max_rounds,
            "executor_backend": backend,
            "reviewer_backend": backend,
        }

    def test_auto_submit_when_executor_forgets_execute(self) -> None:
        cfg = self.base / "t1.json"
        cfg.write_text(json.dumps(self.make_task("auto-sub", "forgot_execute")), encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "orchestrator.py"), "--task-config", str(cfg), "--state-dir", str(self.base / "s1")],
            capture_output=True,
            text=True,
            check=True,
        )
        out = json.loads(res.stdout)
        self.assertEqual(out["status"], "accepted")
        self.assertIn("Accepted in supervised round auto-sub-r1", self.git("log", "-1", "--format=%B"))

    def test_reviewer_quota_interruption_is_safely_retried(self) -> None:
        cfg = self.base / "t2.json"
        cfg.write_text(json.dumps(self.make_task("quota-retry", "quota_retry_once")), encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "orchestrator.py"), "--task-config", str(cfg), "--state-dir", str(self.base / "s2")],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(res.stdout)["status"], "accepted")
        self.assertTrue((self.markers / "reviewer_interrupted_once").is_file())

    def test_contract_attribution_halts_without_wasting_budget(self) -> None:
        cfg = self.base / "t3.json"
        cfg.write_text(json.dumps(self.make_task("contract-err", "contract_block", max_rounds=3)), encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "orchestrator.py"), "--task-config", str(cfg), "--state-dir", str(self.base / "s3")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 1)
        out = json.loads(res.stdout)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["blocked_reason"], "contract_revision_required")
        self.assertEqual(out["rounds_used"], 1)

    def test_daemon_mode_and_dependency_blocking(self) -> None:
        roadmap = {
            "name": "nightly-queue",
            "repo": str(self.repo),
            "profile": "profile.json",
            "policy_files": ["POLICY.md"],
            "isolate_worktree": True,
            "tasks": [
                {
                    **self.make_task("step-1-fail", "always_reject", max_rounds=1),
                },
                {
                    **self.make_task("step-2-dep", "forgot_execute", max_rounds=1),
                    "depends_on": ["step-1-fail"],
                },
            ],
        }
        rm_file = self.base / "roadmap.json"
        rm_file.write_text(json.dumps(roadmap), encoding="utf-8")
        state_dir = self.base / "rm-state"
        res = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "roadmap.py"),
                "daemon",
                "--roadmap",
                str(rm_file),
                "--state-dir",
                str(state_dir),
                "--poll-interval",
                "0.05",
                "--max-cycles",
                "2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 1)
        data = json.loads(res.stdout)
        self.assertEqual(data["tasks"][0]["status"], "budget_exhausted")
        self.assertEqual(data["tasks"][1]["status"], "blocked")
        self.assertEqual(data["tasks"][1]["blocked_reason"], "dependency_failed")
        self.assertTrue((state_dir / "progress.md").is_file())
        self.assertTrue((state_dir / "daemon.heartbeat.json").is_file())
        # Cleanup isolated worktree created by test
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "cleanup",
                "--repo",
                str(self.repo),
                "--worktree",
                data["execution_repo"],
                "--force",
                "--delete-branch",
                "--lock-file",
                str(state_dir / "LOCK"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
