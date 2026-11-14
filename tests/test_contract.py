"""Unit tests for the built-in zero-dependency contract engine (scripts/contract.py)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CONTRACT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "contract.py"


class ContractEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dual-loop-contract-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def run_contract(self, *args: str, code: int = 0) -> subprocess.CompletedProcess:
        res = subprocess.run(
            [sys.executable, str(CONTRACT_SCRIPT), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, code, f"args={args}\nstdout={res.stdout}\nstderr={res.stderr}")
        return res

    def test_init_freeze_check_compare_replay_and_tamper_detection(self) -> None:
        case = self.base / "case-01"
        criteria_file = self.base / "criteria.json"
        criteria_file.write_text(
            json.dumps(
                [
                    {
                        "id": "api-contract",
                        "requirement": "required",
                        "description": "Endpoint returns expected payload",
                        "basis": "Service contract",
                        "acceptance": "Status 200 and schema valid",
                    },
                    {
                        "id": "mobile-layout",
                        "requirement": "not_applicable",
                        "description": "Mobile viewport check",
                        "basis": "Backend-only change",
                        "acceptance": "No client files modified",
                    },
                ]
            ),
            encoding="utf-8",
        )
        self.run_contract(
            "init-case",
            "--root",
            str(case),
            "--topic",
            "backend-check",
            "--question",
            "Verify backend endpoint",
            "--criteria-json",
            str(criteria_file),
        )
        frozen = json.loads(self.run_contract("freeze", "--root", str(case)).stdout)
        self.assertEqual(frozen["expected_items"], 2)
        # Re-freezing the same baseline must fail
        self.run_contract("freeze", "--root", str(case), code=2)

        checked = json.loads(self.run_contract("check", "--root", str(case)).stdout)
        self.assertEqual(checked["integrity"], "PASS")

        baseline_sha = frozen["sha256"]
        (case / "outputs/ev.txt").write_text("HTTP 200 OK verified\n", encoding="utf-8")
        obs = {
            "baseline_sha256": baseline_sha,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "rows": [
                {
                    "id": "api-contract",
                    "status": "PASS",
                    "detail": "Verified HTTP 200 payload",
                    "evidence_level": "integration-test",
                    "evidence": ["outputs/ev.txt"],
                }
            ],
        }
        (case / "outputs/obs.json").write_text(json.dumps(obs), encoding="utf-8")
        comp = json.loads(
            self.run_contract(
                "compare",
                "--root",
                str(case),
                "--observations",
                "outputs/obs.json",
                "--out",
                "outputs/run1-comparison.json",
            ).stdout
        )
        self.assertEqual(comp["assessment"], "PASS")
        self.assertEqual(comp["required_pass"], 1)

        replayed = json.loads(
            self.run_contract("replay", "--root", str(case), "--report", "outputs/run1-comparison.json").stdout
        )
        self.assertEqual(replayed["integrity"], "PASS")

        # Tampering with evidence invalidates replay
        (case / "outputs/ev.txt").write_text("altered evidence\n", encoding="utf-8")
        self.run_contract("replay", "--root", str(case), "--report", "outputs/run1-comparison.json", code=2)

    def test_baseline_revision_chaining_and_fail_assessment(self) -> None:
        case = self.base / "case-chain"
        criteria_file = self.base / "c.json"
        criteria_file.write_text(
            json.dumps(
                [
                    {
                        "id": "item-1",
                        "requirement": "required",
                        "description": "Item 1",
                        "basis": "Spec",
                        "acceptance": "Pass",
                    }
                ]
            ),
            encoding="utf-8",
        )
        self.run_contract(
            "init-case",
            "--root",
            str(case),
            "--topic",
            "chain-topic",
            "--question",
            "Test chaining",
            "--criteria-json",
            str(criteria_file),
        )
        f1 = json.loads(self.run_contract("freeze", "--root", str(case)).stdout)

        # Create v2 referencing v1
        spec = json.loads((case / "spec.json").read_text(encoding="utf-8"))
        old_bl = spec["baseline"]
        spec["baseline"] = old_bl.replace("-v1.json", "-v2.json")
        spec["revision"] = {"reason": "Refined scope", "previous_baseline": old_bl}
        (case / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        f2 = json.loads(self.run_contract("freeze", "--root", str(case)).stdout)
        self.assertNotEqual(f1["sha256"], f2["sha256"])

        (case / "outputs/fail.txt").write_text("Assertion failed\n", encoding="utf-8")
        obs = {
            "baseline_sha256": hashlib.sha256((case / spec["baseline"]).read_bytes()).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "rows": [
                {
                    "id": "item-1",
                    "status": "FAIL",
                    "detail": "Failed check",
                    "evidence_level": "unit",
                    "evidence": ["outputs/fail.txt"],
                }
            ],
        }
        (case / "outputs/obs.json").write_text(json.dumps(obs), encoding="utf-8")
        res = self.run_contract(
            "compare",
            "--root",
            str(case),
            "--observations",
            "outputs/obs.json",
            "--out",
            "outputs/fail-comparison.json",
            code=1,
        )
        self.assertEqual(json.loads(res.stdout)["assessment"], "FAIL")


if __name__ == "__main__":
    unittest.main()
