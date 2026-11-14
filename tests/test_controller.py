"""Adversarial integration tests for the dual-loop controller (scripts/controller.py).

By default tests against the built-in zero-dependency scripts/contract.py; if
EXTERNAL_TASKKIT_SCRIPT is set in the environment, also verifies drop-in
interoperability with an external TaskKit contract.py.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "controller.py"
BUILTIN_CONTRACT = REPO_ROOT / "scripts" / "contract.py"


class ControllerTests(unittest.TestCase):
    contract_script = str(BUILTIN_CONTRACT)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dual-loop-ctrl-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Cycle Fixture")
        (self.repo / "code.txt").write_text("initial\n", encoding="utf-8")
        (self.repo / "protected.txt").write_text("protected\n", encoding="utf-8")
        (self.repo / "kernel").mkdir()
        (self.repo / "kernel/core.txt").write_text("immutable kernel\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial fixture")
        self.control = self.base / "control"
        self.authority = self.base / "authority"
        self.policy = self.base / "policy.md"
        self.policy.write_text("Frozen fixture policy.\n", encoding="utf-8")
        self.profile = self.base / "profile.json"
        self.profile.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "fixture",
                    "protected_paths": ["kernel/"],
                    "preflight": {"port": 4100},
                    "docs": [str(self.policy)],
                }
            ),
            encoding="utf-8",
        )
        self.feedback = self.base / "feedback.md"
        self.feedback.write_text("Independent fixture review examined actual test evidence.\n", encoding="utf-8")
        self.call("init", "--authority", str(self.authority))

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)

    def call(self, action: str, *args: str | Path, role: str | None = None, code: int = 0) -> subprocess.CompletedProcess:
        argv = [sys.executable, str(SCRIPT), action, "--root", str(self.control)]
        if role:
            argv += ["--token", str(self.authority / (role + ".key"))]
        argv += [str(a) for a in args]
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, code, f"{action}: {result.stdout}\n{result.stderr}")
        return result

    def contract_call(self, action: str, case: Path, *args: str, code: int = 0) -> subprocess.CompletedProcess:
        argv = [sys.executable, self.contract_script, action, "--root", str(case), *args]
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def case(self, number: int, requirement: str = "required") -> Path:
        root = self.base / f"case-{number}"
        (root / "inputs").mkdir(parents=True)
        (root / "outputs").mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        spec = {
            "schema_version": 1,
            "topic": "cycle-fixture",
            "question": "Verify controlled fixture execution",
            "as_of": today,
            "expected": "inputs/expected.csv",
            "baseline": f"inputs/{today}-baseline-v1.json",
            "run_command": "fixture check",
            "environment": "temporary local test repository",
            "sample_plan": "One fixed fixture requirement",
            "stop_rule": "Stop after one execution",
            "baseline_timing": "prospective",
            "scope": {
                "population": "fixture",
                "denominator_basis": "one known requirement",
                "included": ["fixture"],
                "excluded": ["unrelated paths"],
                "enumeration": "closed",
            },
            "revision": {"reason": "Independent test round", "previous_baseline": None},
        }
        (root / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        with (root / "inputs/expected.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "requirement", "description", "basis", "acceptance"])
            writer.writerow(["fixture", requirement, "Fixture check", "Integration test", "Evidence recorded"])
        self.contract_call("freeze", root)
        return root

    def manifest(
        self,
        number: int = 1,
        *,
        command: list[str] | None = None,
        max_rounds: int = 2,
        requirement: str = "required",
        omit_contract_script: bool = False,
    ) -> tuple[Path, Path]:
        case = self.case(number, requirement)
        data: dict[str, object] = {
            "repo": str(self.repo),
            "case_root": str(case),
            "profile": str(self.profile),
            "allowed_paths": ["code.txt"],
            "protected_paths": ["protected.txt"],
            "checks": [
                {
                    "id": "fixture-check",
                    "argv": command or [sys.executable, "-c", "print('fixture passed')"],
                    "cwd": ".",
                }
            ],
            "max_rounds": max_rounds,
            "policy_files": [str(self.policy)],
        }
        if not omit_contract_script:
            data["taskkit_script"] = self.contract_script
        path = self.base / f"manifest-{number}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path, case

    def issue(self, **kwargs) -> tuple[Path, Path]:
        manifest, case = self.manifest(**kwargs)
        self.call("issue", "--manifest", manifest, role="supervisor")
        return manifest, case

    def report(self, case: Path, status: str = "PASS") -> str:
        spec = json.loads((case / "spec.json").read_text(encoding="utf-8"))
        (case / "outputs/evidence.txt").write_text("Synthetic controller integration fixture.\n", encoding="utf-8")
        obs = {
            "baseline_sha256": hashlib.sha256((case / spec["baseline"]).read_bytes()).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "rows": [
                {
                    "id": "fixture",
                    "status": status,
                    "detail": "Fixed fixture evidence",
                    "evidence_level": "synthetic fixture",
                    "evidence": ["outputs/evidence.txt"],
                }
            ],
        }
        (case / "outputs/observations.json").write_text(json.dumps(obs), encoding="utf-8")
        self.contract_call(
            "compare",
            case,
            "--observations",
            "outputs/observations.json",
            "--out",
            "outputs/fixture-comparison.json",
            code=0 if status == "PASS" else 1,
        )
        return "outputs/fixture-comparison.json"

    def accept(self, report: str, code: int = 0) -> subprocess.CompletedProcess:
        return self.call(
            "review",
            "--verdict",
            "accept",
            "--feedback",
            self.feedback,
            "--report",
            report,
            role="reviewer",
            code=code,
        )

    def reject(self) -> str:
        self.call("review", "--verdict", "reject", "--feedback", self.feedback, role="reviewer")
        return json.loads((self.control / "state.json").read_text(encoding="utf-8"))["rounds"][-1]["review_hash"]

    def test_default_bundled_contract_script_when_omitted(self) -> None:
        _, case = self.issue(omit_contract_script=True)
        (self.repo / "code.txt").write_text("bundled contract edit\n", encoding="utf-8")
        self.call("execute", role="executor")
        self.accept(self.report(case))
        self.call("status")

    def test_roles_and_single_execution(self) -> None:
        manifest, case = self.manifest()
        self.call("issue", "--manifest", manifest, role="executor", code=2)
        self.call("issue", "--manifest", manifest, role="supervisor")
        self.call("execute", role="supervisor", code=2)
        (self.repo / "code.txt").write_text("authorized edit\n", encoding="utf-8")
        self.call("execute", role="executor")
        self.call("execute", role="executor", code=2)
        report = self.report(case)
        self.call("review", "--verdict", "accept", "--feedback", self.feedback, "--report", report, role="executor", code=2)
        self.accept(report)
        self.call("status")
        self.call("execute", role="executor", code=2)

    def test_real_failure_cannot_be_accepted(self) -> None:
        _, case = self.issue(command=[sys.executable, "-c", "raise SystemExit(7)"])
        self.call("execute", role="executor", code=1)
        self.accept(self.report(case), code=2)
        self.call("execute", role="executor", code=2)

    def test_external_reject_authorizes_only_supervisor_recycle(self) -> None:
        self.issue(command=[sys.executable, "-c", "raise SystemExit(1)"])
        self.call("execute", role="executor", code=1)
        new, case = self.manifest(2)
        self.call("recycle", "--manifest", new, "--change-reason", "repair", role="supervisor", code=2)
        review_hash = self.reject()
        self.call("recycle", "--manifest", new, "--from-review", review_hash, "--change-reason", "repair", role="executor", code=2)
        self.call("recycle", "--manifest", new, "--from-review", "incorrect", "--change-reason", "repair", role="supervisor", code=2)
        self.call("recycle", "--manifest", new, "--from-review", review_hash, "--change-reason", "Apply review feedback", role="supervisor")
        (self.repo / "code.txt").write_text("recycled fix\n", encoding="utf-8")
        self.call("execute", role="executor")
        self.accept(self.report(case))
        self.assertTrue((self.control / "rounds/001/execution.json").is_file())
        self.assertTrue((self.control / "rounds/002/review.json").is_file())

    def test_budget_cannot_be_raised(self) -> None:
        self.issue(max_rounds=1)
        self.call("execute", role="executor")
        review_hash = self.reject()
        new, _ = self.manifest(2, max_rounds=3)
        self.call("recycle", "--manifest", new, "--from-review", review_hash, "--change-reason", "attempt raised budget", role="supervisor", code=2)
        data = json.loads(new.read_text(encoding="utf-8"))
        data["max_rounds"] = 1
        new.write_text(json.dumps(data), encoding="utf-8")
        self.call("recycle", "--manifest", new, "--from-review", review_hash, "--change-reason", "budget exhausted", role="supervisor", code=2)

    def test_policy_drift_consumes_attempt_and_requires_review(self) -> None:
        self.issue()
        self.policy.write_text("changed policy after issue\n", encoding="utf-8")
        self.call("execute", role="executor", code=1)
        self.call("execute", role="executor", code=2)
        self.reject()

    def test_frozen_manifest_and_ledger_tampering(self) -> None:
        self.issue()
        path = self.control / "rounds/001/manifest.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.call("status", code=2)
        self.call("execute", role="executor", code=2)

    def test_state_tampering_is_detected(self) -> None:
        state = self.control / "state.json"
        data = json.loads(state.read_text(encoding="utf-8"))
        data["phase"] = "accepted"
        state.write_text(json.dumps(data), encoding="utf-8")
        self.call("status", code=2)

    def test_profile_protects_declared_paths_and_clean_worktree_gate(self) -> None:
        self.profile.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "fixture",
                    "protected_paths": ["kernel/", "protected.txt"],
                    "require_clean_worktree": True,
                }
            ),
            encoding="utf-8",
        )
        # Dirty worktree must be rejected at issue when require_clean_worktree=True
        (self.repo / "code.txt").write_text("uncommitted pre-issue edit\n", encoding="utf-8")
        manifest, _ = self.manifest()
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)
        self.git("checkout", "--", "code.txt")

        self.call("issue", "--manifest", manifest, role="supervisor")
        (self.repo / "kernel/core.txt").write_text("unauthorized edit\n", encoding="utf-8")
        self.call("execute", role="executor", code=1)
        execution = json.loads((self.control / "rounds/001/execution.json").read_text(encoding="utf-8"))
        self.assertIn("kernel/core.txt", execution["error"])

    def test_profile_is_required_validated_and_archived(self) -> None:
        manifest, _ = self.manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data.pop("profile")
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)

        data["profile"] = str(self.profile)
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.profile.write_text(json.dumps({"schema_version": 2, "name": "fixture", "protected_paths": []}), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)

        self.profile.write_text(json.dumps({"schema_version": 1, "name": "fixture", "protected_paths": ["../"]}), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)

        self.profile.write_text(json.dumps({"schema_version": 1, "name": "fixture", "protected_paths": ["kernel/"]}), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor")
        archived = self.control / "rounds/001/policies/001-profile.json"
        self.assertTrue(archived.is_file())
        self.profile.write_text(json.dumps({"schema_version": 1, "name": "changed", "protected_paths": ["kernel/"]}), encoding="utf-8")
        self.call("execute", role="executor", code=1)
        self.reject()
        archived.write_text("{}", encoding="utf-8")
        self.call("status", code=2)

    def test_path_escape_and_symlink_scope_are_rejected(self) -> None:
        manifest, _ = self.manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["checks"][0]["cwd"] = "../"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)
        data["checks"][0]["cwd"] = "."
        (self.repo / "link").symlink_to(self.base)
        data["allowed_paths"] = ["link/"]
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)

    def test_post_execution_edits_and_evidence_tampering_reject_acceptance(self) -> None:
        _, case = self.issue()
        self.call("execute", role="executor")
        report = self.report(case)
        (self.repo / "code.txt").write_text("edit after sealed execution\n", encoding="utf-8")
        self.accept(report, code=2)
        self.git("checkout", "--", "code.txt")
        (case / "outputs/evidence.txt").write_text("tampered\n", encoding="utf-8")
        self.accept(report, code=2)

    def test_interrupted_running_execution_can_only_be_rejected(self) -> None:
        spec = importlib.util.spec_from_file_location("cycle_controller_fixture", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.issue()
        state, previous, seq = module.read_state(self.control)
        state["phase"] = "running"
        module.record(self.control, state, "executor", "execute-start", previous, seq)
        self.call("execute", role="executor", code=2)
        self.call("review", "--verdict", "accept", "--feedback", self.feedback, role="reviewer", code=2)
        self.reject()

    def test_investigate_cannot_be_issued_or_recycled(self) -> None:
        manifest, _ = self.manifest(requirement="investigate")
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)
        state = json.loads((self.control / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(state["rounds"], [])

    def test_check_cannot_mutate_source_and_ignored_builds_allowed(self) -> None:
        manifest, _ = self.manifest(
            command=[sys.executable, "-c", "from pathlib import Path; Path('code.txt').write_text('gate altered')"]
        )
        self.call("issue", "--manifest", manifest, role="supervisor")
        self.call("execute", role="executor", code=1)
        execution = json.loads((self.control / "rounds/001/execution.json").read_text(encoding="utf-8"))
        self.assertIn("candidate changed during check", execution["error"])

    def test_timeout_terminates_descendants_and_offline_replay_works(self) -> None:
        marker, ready = self.base / "late-timeout", self.base / "ready-timeout"
        child = (
            f"import time,signal; from pathlib import Path; "
            f"signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"Path({str(ready)!r}).write_text('ready'); time.sleep(2); Path({str(marker)!r}).write_text('late')"
        )
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(15)"
        manifest, _ = self.manifest(command=[sys.executable, "-c", parent])
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["checks"][0]["timeout"] = 1
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor")
        self.call("execute", role="executor", code=1)
        execution = json.loads((self.control / "rounds/001/execution.json").read_text(encoding="utf-8"))
        self.assertEqual(execution["checks"][0]["exit_code"], 124)
        time.sleep(1.3)
        self.assertFalse(marker.exists())
        rhash = self.reject()

        # Recycle to passing round, accept, then delete original case & policy and verify offline replay
        m2, case2 = self.manifest(2)
        self.call("recycle", "--manifest", m2, "--from-review", rhash, "--change-reason", "Fix timeout", role="supervisor")
        self.git("checkout", "--", ".")
        self.call("execute", role="executor")
        rep = self.report(case2)
        self.accept(rep)
        shutil.rmtree(case2)
        self.policy.unlink()
        self.call("status")
        self.call("status", "--full-replay")

    def test_bytecode_suppression_env_override_and_full_replay_cache(self) -> None:
        # Repository intentionally has NO .gitignore; importing a module in check must not create __pycache__/
        (self.repo / "helper_mod.py").write_text("FLAG = 7\n", encoding="utf-8")
        self.git("add", "helper_mod.py")
        self.git("commit", "-qm", "Add helper module without .gitignore")

        manifest, case = self.manifest(
            command=[
                sys.executable,
                "-c",
                "import os, helper_mod; assert helper_mod.FLAG == 7 and os.environ.get('CUSTOM_CHECK_FLAG') == 'enabled'",
            ]
        )
        data = json.loads(manifest.read_text(encoding="utf-8"))
        # Non-string env values must be rejected at issue
        data["checks"][0]["env"] = {"CUSTOM_CHECK_FLAG": 123}
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor", code=2)

        data["checks"][0]["env"] = {"CUSTOM_CHECK_FLAG": "enabled"}
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.call("issue", "--manifest", manifest, role="supervisor")

        clean_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE")}
        exec_res = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "execute",
                "--root",
                str(self.control),
                "--token",
                str(self.authority / "executor.key"),
            ],
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        self.assertEqual(exec_res.returncode, 0, exec_res.stdout + exec_res.stderr)
        self.assertFalse((self.repo / "__pycache__").exists())

        rep = self.report(case)
        self.accept(rep)
        self.call("status")
        self.assertTrue((self.control / ".verified-cache.json").is_file())
        self.call("status", "--full-replay")

        # Tampering with archived review.json must still be caught immediately even when .verified-cache.json exists
        rev_path = self.control / "rounds/001/review.json"
        rev_path.write_text(rev_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.call("status", code=2)


@unittest.skipUnless(
    os.environ.get("EXTERNAL_TASKKIT_SCRIPT") and Path(os.environ["EXTERNAL_TASKKIT_SCRIPT"]).is_file(),
    "EXTERNAL_TASKKIT_SCRIPT not set",
)
class ExternalTaskKitCompatibilityTests(ControllerTests):
    contract_script = os.environ.get("EXTERNAL_TASKKIT_SCRIPT", "")


if __name__ == "__main__":
    unittest.main()
