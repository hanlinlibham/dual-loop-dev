"""Integration tests for automated subagent orchestrator and unattended roadmap daemon."""

from __future__ import annotations

import json
import importlib.util
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
    with (marker_dir / "executor-calls").open("a") as f:
        f.write(str(round_num) + "\\n")
    (repo / "feature.py").write_text(f"ROUND = {round_num}\\n", encoding="utf-8")
    if mode == "out_of_scope_r1" and round_num == 1:
        (repo / "stray.txt").write_text("unauthorized round 1 edit\\n", encoding="utf-8")
    if mode == "contract_with_extra_file" and round_num == 1:
        (repo / "new_test.py").write_text("assert True\\n", encoding="utf-8")
    if mode != "forgot_execute":
        subprocess.run([sys.executable, controller_py, "execute", "--root", str(control), "--token", str(token)], check=False)

elif role == "reviewer":
    if mode in ("verification", "mixed_contract") and round_num == 1:
        fb = case / "outputs/fb.md"
        flag = "true" if mode == "verification" else "false"
        fb.write_text("ATTRIBUTION: contract\\nVERIFICATION_ONLY: " + flag + "\\nProbe assumption is wrong.\\n", encoding="utf-8")
        subprocess.run([sys.executable, controller_py, "review", "--root", str(control), "--token", str(token), "--verdict", "reject", "--feedback", str(fb)], check=True)
        raise SystemExit(0)

    if mode == "quota_retry_once":
        flag = marker_dir / "reviewer_interrupted_once"
        if not flag.exists():
            flag.write_text("1", encoding="utf-8")
            raise SystemExit(1)  # Simulate quota exhaustion before verdict

    if mode in ("contract_block", "contract_with_extra_file"):
        fb = case / "outputs/fb.md"
        fb.write_text("ATTRIBUTION: contract\\nAllowlist missing test file.\\n", encoding="utf-8")
        subprocess.run([sys.executable, controller_py, "review", "--root", str(control), "--token", str(token), "--verdict", "reject", "--feedback", str(fb)], check=True)
        raise SystemExit(0)

    if mode == "out_of_scope_r1" and round_num == 1:
        fb = case / "outputs/fb-1.md"
        fb.write_text("ATTRIBUTION: code\\nRemove unauthorized stray.txt and keep changes inside feature.py.\\n", encoding="utf-8")
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
        (self.repo / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
        (self.repo / "feature.py").write_text("ROUND = 0\n", encoding="utf-8")
        (self.repo / "POLICY.md").write_text("Policy v1\n", encoding="utf-8")
        (self.repo / "profile.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "svc",
                    "protected_paths": [".gitignore", "POLICY.md", "profile.json"],
                    "require_clean_worktree": True,
                }
            ),
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

    def repaired_probe_task(self, *, tamper: bool = False, mode: str = "verification", max_rounds: int = 2) -> dict:
        task = self.make_task("probe-repair", mode, max_rounds=max_rounds)
        marker = self.markers / "checks"
        task["checks"] = [
            {"id": "probe", "argv": [sys.executable, "-c", "raise SystemExit(1)"]},
            {"id": "tail", "argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]},
        ]
        repair = self.base / "repair.py"
        repair.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "task=json.loads(Path(sys.argv[1]).read_text())\n"
            "task['checks'][0]['argv']=[sys.executable,'-c',\"print('corrected probe')\"]\n"
            + (f"Path({str(self.repo / 'stray.txt')!r}).write_text('repair tampered with source')\n" if tamper else "")
            + "Path(sys.argv[2]).write_text(json.dumps({'checks':task['checks']}))\n",
            encoding="utf-8",
        )
        task["contract_repair_command"] = [sys.executable, str(repair), "{task_config}", "{out_patch}"]
        return task

    def run_task(self, task: dict, state: str = "verification-state") -> subprocess.CompletedProcess:
        cfg = self.base / (state + ".json")
        cfg.write_text(json.dumps(task), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "orchestrator.py"), "--task-config", str(cfg), "--state-dir", str(self.base / state)],
            capture_output=True, text=True, check=False,
        )

    def test_verification_repair_skips_executor_and_runs_all_checks(self) -> None:
        res = self.run_task(self.repaired_probe_task())
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1"])
        self.assertEqual((self.repo / "feature.py").read_text(), "ROUND = 1\n")
        self.assertTrue((self.markers / "checks").is_file())
        rounds = self.base / "verification-state/control/rounds"
        self.assertEqual(json.loads((rounds / "001/review.json").read_text())["verdict"], "reject")
        self.assertEqual(json.loads((rounds / "002/manifest.json").read_text())["round_kind"], "verification")
        checks = json.loads((rounds / "002/execution.json").read_text())["checks"]
        self.assertEqual([c["id"] for c in checks], ["probe", "tail"])
        self.assertEqual(out["metrics"]["agent_calls"], {"executor": 1, "reviewer": 2})
        self.assertEqual(out["metrics"]["round_kinds"], {"development": 1, "verification": 1})
        self.assertEqual(out["metrics"]["checks_executed"], 3)
        self.assertIsNone(out["metrics"]["token_usage"])

    def test_verification_repair_copies_case_local_probe_without_changing_parent(self) -> None:
        task = self.repaired_probe_task()
        state = self.base / "case-probe-state"
        first_probe = state / "cases/round-001/inputs/probe.py"
        bad_probe = self.base / "bad-probe.py"
        bad_probe.write_text("raise SystemExit(1)\n", encoding="utf-8")
        fixed_probe = self.base / "fixed-probe.py"
        fixed_probe.write_text("print('corrected case-local probe')\n", encoding="utf-8")
        task["case_inputs"] = {"inputs/probe.py": str(bad_probe)}
        task["policy_files"].append(str(first_probe))
        task["checks"][0]["argv"] = [sys.executable, "-B", str(first_probe)]
        repair = self.base / "repair-case-probe.py"
        repair.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "task=json.loads(Path(sys.argv[1]).read_text())\n"
            "case=Path(sys.argv[3])\nassert not case.exists()\n"
            "probe=case/'inputs/probe.py'\n"
            "task['checks'][0]['argv']=[sys.executable,'-B',str(probe)]\n"
            "task['policy_files'][-1]=str(probe)\n"
            f"inputs={{'inputs/probe.py':{str(fixed_probe)!r}}}\n"
            "Path(sys.argv[2]).write_text(json.dumps({'checks':task['checks'],'policy_files':task['policy_files'],'case_inputs':inputs}))\n",
            encoding="utf-8",
        )
        task["contract_repair_command"] = [sys.executable, str(repair), "{task_config}", "{out_patch}", "{case_root}"]
        res = self.run_task(task, "case-probe-state")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1"])
        self.assertEqual(first_probe.read_bytes(), bad_probe.read_bytes())
        second_probe = state / "cases/round-002/inputs/probe.py"
        self.assertEqual(second_probe.read_bytes(), fixed_probe.read_bytes())
        archive = state / "control/rounds/002/policies/001-probe.py"
        self.assertEqual(archive.read_bytes(), fixed_probe.read_bytes())
        self.assertEqual(json.loads(res.stdout)["metrics"]["checks_executed"], 3)

    def test_case_inputs_reject_escape_symlinks_and_generated_file_overwrite(self) -> None:
        spec = importlib.util.spec_from_file_location("orchestrator_case_inputs_test", SCRIPTS / "orchestrator.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        case = self.base / "case-inputs"
        (case / "inputs").mkdir(parents=True)
        generated = case / "inputs/expected.csv"
        generated.write_text("preserve generated contract", encoding="utf-8")
        source = self.base / "source.py"
        source.write_text("print('ok')", encoding="utf-8")
        symlink_source = self.base / "source-link.py"
        symlink_source.symlink_to(source)
        (case / "inputs/link").symlink_to(self.base, target_is_directory=True)
        for destination, origin in [
            ("../escape.py", str(source)),
            (str(self.base / "absolute.py"), str(source)),
            ("outputs/probe.py", str(source)),
            ("inputs/expected.csv", str(source)),
            ("inputs/link/escaped.py", str(source)),
            ("inputs/probe.py", str(symlink_source)),
            ("inputs/probe.py", "relative.py"),
        ]:
            with self.subTest(destination=destination, origin=origin), self.assertRaises((ValueError, OSError)):
                module.copy_case_inputs(case, {destination: origin})
        self.assertEqual(generated.read_text(), "preserve generated contract")

    def test_repaired_probe_preflight_failure_keeps_parent_round_and_prior_logs(self) -> None:
        task = self.repaired_probe_task()
        task["probe_preflight_checks"] = [
            {"id": "syntax", "argv": [sys.executable, "-c", "print('initial syntax valid')"]}
        ]
        repair = self.base / "repair.py"
        repair.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "task=json.loads(Path(sys.argv[1]).read_text())\n"
            "task['checks'][0]['argv']=[sys.executable,'-c',\"print('fixed probe')\"]\n"
            "preflight=[{'id':'syntax','argv':[sys.executable,'-c',\"compile('for', '<fixed-probe>', 'exec')\"]}]\n"
            "Path(sys.argv[2]).write_text(json.dumps({'checks':task['checks'],'probe_preflight_checks':preflight}))\n",
            encoding="utf-8",
        )
        res = self.run_task(task)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout)["blocked_reason"], "probe_preflight_failed")
        state_dir = self.base / "verification-state"
        state = json.loads((state_dir / "control/state.json").read_text())
        self.assertEqual(state["phase"], "rejected")
        self.assertEqual(len(state["rounds"]), 1)
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1"])
        logs = sorted((state_dir / "probe-preflight").glob("attempt-*/result.json"))
        self.assertEqual(len(logs), 2)
        self.assertEqual(json.loads(logs[0].read_text())["checks"][0]["exit_code"], 0)
        self.assertNotEqual(json.loads(logs[1].read_text())["checks"][0]["exit_code"], 0)
        self.assertFalse((state_dir / "cases/round-002").exists())
        repair.write_text(
            repair.read_text().replace("compile('for', '<fixed-probe>', 'exec')", "print('repaired syntax valid')"),
            encoding="utf-8",
        )
        resumed = self.run_task(task)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1"])
        self.assertEqual(len(list((state_dir / "probe-preflight").glob("attempt-*/result.json"))), 3)

    def test_verification_repair_does_not_hide_source_tampering(self) -> None:
        res = self.run_task(self.repaired_probe_task(tamper=True))
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertTrue((self.repo / "stray.txt").is_file())
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1"])
        state = json.loads((self.base / "verification-state/control/state.json").read_text())
        self.assertEqual(len(state["rounds"]), 1)
        self.assertFalse((self.base / "verification-state/reverted-out-of-scope").exists())

    def test_verification_repair_keeps_budget_and_mixed_failures_use_executor(self) -> None:
        res = self.run_task(self.repaired_probe_task(max_rounds=1), "budget-state")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout)["status"], "budget_exhausted")
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1"])
        self.git("checkout", "--", "feature.py")
        (self.markers / "executor-calls").unlink()
        mixed = self.run_task(self.repaired_probe_task(mode="mixed_contract"), "mixed-state")
        self.assertEqual(mixed.returncode, 0, mixed.stdout + mixed.stderr)
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1", "2"])

    def test_resume_can_supply_missing_repair_command_without_recoding(self) -> None:
        task = self.repaired_probe_task()
        repair = task.pop("contract_repair_command")
        blocked = self.run_task(task)
        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertEqual(json.loads(blocked.stdout)["blocked_reason"], "contract_revision_required")
        task["contract_repair_command"] = repair
        resumed = self.run_task(task)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual((self.markers / "executor-calls").read_text().splitlines(), ["1"])
        self.assertEqual(json.loads(resumed.stdout)["metrics"]["agent_calls"], {"executor": 1, "reviewer": 2})

    def test_probe_preflight_failure_does_not_consume_round_or_call_agents(self) -> None:
        task = self.make_task("bad-probe", "forgot_execute")
        task["probe_preflight_checks"] = [
            {"id": "probe-syntax", "argv": [sys.executable, "-c", "compile('for', '<probe>', 'exec')"]}
        ]
        res = self.run_task(task, "preflight-state")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        result = json.loads(res.stdout)
        self.assertEqual(result["blocked_reason"], "probe_preflight_failed")
        self.assertEqual(result["rounds_used"], 0)
        self.assertFalse((self.markers / "executor-calls").exists())

    def test_probe_preflight_source_mutation_stops_before_issue(self) -> None:
        task = self.make_task("bad-preflight", "forgot_execute")
        task["probe_preflight_checks"] = [
            {"id": "mutating-probe", "argv": [sys.executable, "-c", "from pathlib import Path; Path('feature.py').write_text('changed')"]}
        ]
        res = self.run_task(task, "preflight-mutation-state")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout)["rounds_used"], 0)
        self.assertEqual((self.repo / "feature.py").read_text(), "changed")
        self.assertFalse((self.markers / "executor-calls").exists())

    def test_out_of_scope_reverted_on_code_attribution_but_preserved_on_contract(self) -> None:
        # 1. ATTRIBUTION: code -> stray.txt archived under reverted-out-of-scope/before-r002 and removed so Round 2 passes
        cfg_code = self.base / "t_oos.json"
        state_oos = self.base / "s_oos"
        cfg_code.write_text(json.dumps(self.make_task("oos-fix", "out_of_scope_r1", max_rounds=2)), encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "orchestrator.py"), "--task-config", str(cfg_code), "--state-dir", str(state_oos)],
            capture_output=True,
            text=True,
            check=True,
        )
        out = json.loads(res.stdout)
        self.assertEqual(out["status"], "accepted")
        self.assertEqual(out["rounds_used"], 2)
        self.assertFalse((self.repo / "stray.txt").exists())
        self.assertTrue((state_oos / "reverted-out-of-scope/before-r002/files/stray.txt").is_file())

        # 2. ATTRIBUTION: contract -> candidate's new_test.py MUST NOT be wiped!
        cfg_contract = self.base / "t_contract_extra.json"
        state_ce = self.base / "s_ce"
        cfg_contract.write_text(json.dumps(self.make_task("ce-keep", "contract_with_extra_file", max_rounds=3)), encoding="utf-8")
        res_ce = subprocess.run(
            [sys.executable, str(SCRIPTS / "orchestrator.py"), "--task-config", str(cfg_contract), "--state-dir", str(state_ce)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_ce.returncode, 1)
        self.assertEqual(json.loads(res_ce.stdout)["blocked_reason"], "contract_revision_required")
        self.assertTrue((self.repo / "new_test.py").is_file())
        (self.repo / "new_test.py").unlink()
        self.git("checkout", "--", ".")

    def test_continue_independent_on_failure_archives_dirty_worktree_and_auto_integrates(self) -> None:
        roadmap = {
            "name": "independent-queue",
            "repo": str(self.repo),
            "profile": "profile.json",
            "policy_files": ["POLICY.md"],
            "isolate_worktree": True,
            "continue_independent_on_failure": True,
            "auto_integrate": True,
            "tasks": [
                {
                    **self.make_task("step-1-dirty-fail", "always_reject", max_rounds=1),
                },
                {
                    **self.make_task("step-2-independent", "forgot_execute", max_rounds=1),
                    "depends_on": [],
                },
            ],
        }
        rm_file = self.base / "roadmap-indep.json"
        rm_file.write_text(json.dumps(roadmap), encoding="utf-8")
        state_dir = self.base / "rm-indep-state"
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "roadmap.py"), "run", "--roadmap", str(rm_file), "--state-dir", str(state_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["tasks"][0]["status"], "budget_exhausted")
        self.assertEqual(data["tasks"][1]["status"], "accepted")
        self.assertTrue((state_dir / "tasks/step-1-dirty-fail/uncommitted-snapshot/files/feature.py").is_file())
        # Partial failure must NOT auto-integrate automatically
        self.assertIsNone(data["integration"])

        # Explicit integration with --state-dir verifies step-2's accepted ledger & candidate tree
        integ = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "integrate",
                "--repo",
                str(self.repo),
                "--worktree",
                data["execution_repo"],
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(json.loads(integ.stdout)["ledger_verified"])
        self.assertIn("Accepted in supervised round step-2-independent-r1", self.git("log", "-1", "--format=%B"))

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

        # All-green roadmap with auto_integrate=True automatically verifies ledger & integrates
        (self.repo / "feature.py").write_text("ROUND = 0\n", encoding="utf-8")
        self.git("add", "feature.py")
        self.git("commit", "-qm", "Reset feature.py before all-green test")
        all_green_rm = {
            "name": "all-green-queue",
            "repo": str(self.repo),
            "profile": "profile.json",
            "policy_files": ["POLICY.md"],
            "isolate_worktree": True,
            "auto_integrate": True,
            "tasks": [self.make_task("step-3-green", "forgot_execute", max_rounds=1)],
        }
        rm_green_file = self.base / "roadmap-green.json"
        rm_green_file.write_text(json.dumps(all_green_rm), encoding="utf-8")
        green_state = self.base / "rm-green-state"
        res_green = subprocess.run(
            [sys.executable, str(SCRIPTS / "roadmap.py"), "run", "--roadmap", str(rm_green_file), "--state-dir", str(green_state)],
            capture_output=True,
            text=True,
            check=True,
        )
        gdata = json.loads(res_green.stdout)
        self.assertTrue(gdata["integration"]["integrated"])
        self.assertTrue(gdata["integration"]["ledger_verified"])
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "cleanup",
                "--repo",
                str(self.repo),
                "--worktree",
                gdata["execution_repo"],
                "--force",
                "--delete-branch",
                "--lock-file",
                str(green_state / "LOCK"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_roadmap_cycle_detection(self) -> None:
        roadmap = {
            "name": "cyclic-queue",
            "repo": str(self.repo),
            "profile": "profile.json",
            "policy_files": ["POLICY.md"],
            "isolate_worktree": False,
            "tasks": [
                {**self.make_task("task-a", "forgot_execute", max_rounds=1), "depends_on": ["task-b"]},
                {**self.make_task("task-b", "forgot_execute", max_rounds=1), "depends_on": ["task-a"]},
            ],
        }
        rm_file = self.base / "roadmap-cycle.json"
        rm_file.write_text(json.dumps(roadmap), encoding="utf-8")
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "roadmap.py"), "run", "--roadmap", str(rm_file), "--state-dir", str(self.base / "rm-cycle-state")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("dependency cycle detected", res.stderr)

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
