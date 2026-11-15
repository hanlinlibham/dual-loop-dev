"""One reviewer-authored observation set produces the existing audit artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import sys
import unittest

import test_controller


class ReviewSubmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_controller.ControllerTests(methodName="runTest")
        self.fixture.contract_script = os.environ.get("EXTERNAL_TASKKIT_SCRIPT", self.fixture.contract_script)
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def candidate(self, *, failed: bool = False):
        f = self.fixture
        command = [sys.executable, "-c", "raise SystemExit(1)"] if failed else None
        _, case = f.issue(command=command)
        (f.repo / "code.txt").write_text("candidate\n", encoding="utf-8")
        f.call("execute", role="executor", code=1 if failed else 0)
        return case

    def observations(self, case, *, status="PASS", review=None, name="observations"):
        spec = json.loads((case / "spec.json").read_text(encoding="utf-8"))
        (case / "outputs/evidence.txt").write_text("Synthetic review integration evidence.\n", encoding="utf-8")
        observation = {
            "baseline_sha256": hashlib.sha256((case / spec["baseline"]).read_bytes()).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "rows": [{
                "id": "fixture", "status": status, "detail": "Fixture checked",
                "evidence_level": "synthetic fixture", "evidence": ["outputs/evidence.txt"],
            }],
            "review": review or {"summary": "Independent fixture review", "blockers": [], "non_blocking": []},
        }
        relative = f"outputs/{name}.json"
        (case / relative).write_text(json.dumps(observation), encoding="utf-8")
        return relative

    def test_single_submission_generates_feedback_and_replayable_comparison(self):
        f = self.fixture
        case = self.candidate()
        obs = self.observations(case)
        f.call("review", "--verdict", "accept", "--observations", obs, role="reviewer")
        state = json.loads((f.control / "state.json").read_text())
        self.assertEqual(state["phase"], "accepted")
        round_dir = f.control / "rounds/001"
        review = json.loads((round_dir / "review.json").read_text())
        self.assertEqual(review["assessment"], "PASS")
        self.assertIn("Independent fixture review", (round_dir / "feedback.md").read_text())
        self.assertEqual(json.loads((case / obs).read_text())["rows"][0]["status"], "PASS")
        f.call("status", "--full-replay")

    def test_rejection_records_explicit_probe_only_authorization(self):
        f = self.fixture
        case = self.candidate(failed=True)
        obs = self.observations(case, status="FAIL", review={
            "summary": "Probe uses the wrong fixture field; no product defects remain.",
            "attribution": "contract", "verification_only": True,
            "blockers": [], "non_blocking": ["Optional wording improvement"],
        })
        f.call("review", "--verdict", "reject", "--observations", obs, role="reviewer")
        feedback = (f.control / "rounds/001/feedback.md").read_text()
        self.assertIn("\nATTRIBUTION: contract\n", feedback)
        self.assertIn("\nVERIFICATION_ONLY: true\n", feedback)
        f.call("status", "--full-replay")

    def test_failed_evidence_and_code_blockers_cannot_be_auto_accepted(self):
        f = self.fixture
        case = self.candidate()
        obs = self.observations(case, status="FAIL", name="failed")
        f.call("review", "--verdict", "accept", "--observations", obs, role="reviewer", code=2)
        blocked = self.observations(case, name="blocked", review={
            "summary": "Found a data-loss defect", "blockers": ["Shared component deleted"], "non_blocking": [],
        })
        f.call("review", "--verdict", "accept", "--observations", blocked, role="reviewer", code=2)
        self.assertEqual(json.loads((f.control / "state.json").read_text())["phase"], "awaiting_review")

    def test_verification_only_rejects_mixed_failures_and_missing_attribution(self):
        f = self.fixture
        case = self.candidate(failed=True)
        for index, review in enumerate([
            {"summary": "Probe and implementation both fail", "attribution": "contract", "verification_only": True,
             "blockers": ["Broken shared relationship"], "non_blocking": []},
            {"summary": "Unknown failure", "blockers": [], "non_blocking": []},
            {"summary": "Implementation failure", "attribution": "code", "verification_only": True,
             "blockers": [], "non_blocking": []},
        ]):
            obs = self.observations(case, status="FAIL", review=review, name=f"invalid-{index}")
            f.call("review", "--verdict", "reject", "--observations", obs, role="reviewer", code=2)
        self.assertFalse((f.control / "rounds/001/review.json").exists())

    def test_executor_cannot_submit_and_legacy_flags_cannot_be_mixed(self):
        f = self.fixture
        case = self.candidate()
        obs = self.observations(case)
        f.call("review", "--verdict", "accept", "--observations", obs, role="executor", code=2)
        f.call("review", "--verdict", "accept", "--observations", obs,
               "--feedback", f.feedback, role="reviewer", code=2)
        self.assertFalse((f.control / "rounds/001/review.json").exists())


if __name__ == "__main__":
    unittest.main()
