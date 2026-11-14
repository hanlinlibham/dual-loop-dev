#!/usr/bin/env python3
"""Self-contained end-to-end demonstration of dual-loop-dev.

Runs a 2-task DAG roadmap inside an automatically isolated Git worktree:
  - Task 1 ('sanitize-labels'):
      * Round 1: Executor passes unit tests, but the cold Reviewer runs an
        adversarial probe, catches an empty-label bug on numeric strings, and
        rejects with ATTRIBUTION: code.
      * Round 2: Orchestrator recycles using the sealed review_hash; Executor
        fixes the edge case; fresh Reviewer verifies and accepts -> committed
        with supervised-round trailer.
  - Task 2 ('batch-export', depends_on=['sanitize-labels']):
      * Round 1: Implemented, verified, accepted, and committed.
  - Finally integrates the isolated worktree back into main (verifying review
    trailers), prints the Git audit summary, and cleans up the worktree.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


MOCK_AGENT_CODE = """\
import hashlib, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

role = os.environ["DUAL_LOOP_ROLE"]
repo = Path(os.environ["DUAL_LOOP_REPO"])
control = Path(os.environ["DUAL_LOOP_CONTROL_ROOT"])
case = Path(os.environ["DUAL_LOOP_CASE_ROOT"])
token = Path(os.environ["DUAL_LOOP_TOKEN_FILE"])
round_num = int(os.environ["DUAL_LOOP_ROUND"])
contract_py = os.environ["DUAL_LOOP_CONTRACT_SCRIPT"]
controller_py = os.environ["DUAL_LOOP_CONTROLLER_SCRIPT"]
baseline_file = Path(os.environ["DUAL_LOOP_BASELINE_FILE"])
brief = Path(os.environ["DUAL_LOOP_BRIEF_FILE"]).read_text(encoding="utf-8")

is_task_1 = "`sanitize-labels`" in brief

if role == "executor":
    if is_task_1:
        if round_num == 1:
            # Subtle bug: strips digits and leaves empty string instead of placeholder!
            (repo / "src/sanitizer.py").write_text(
                "def sanitize_label(label: str) -> str:\\n"
                "    cleaned = ''.join(ch for ch in label.strip() if not ch.isdigit())\\n"
                "    return cleaned.strip()\\n",
                encoding="utf-8",
            )
        else:
            # Round 2 fix guided by independent reviewer feedback
            (repo / "src/sanitizer.py").write_text(
                "def sanitize_label(label: str) -> str:\\n"
                "    raw = label.strip()\\n"
                "    if not raw:\\n"
                "        raise ValueError('empty label is forbidden')\\n"
                "    if any(ch.isdigit() for ch in raw):\\n"
                "        return 'Series-Placeholder'\\n"
                "    return raw\\n",
                encoding="utf-8",
            )
    else:
        (repo / "src/sanitizer.py").write_text(
            (repo / "src/sanitizer.py").read_text(encoding="utf-8")
            + "\\ndef sanitize_batch(labels: list[str]) -> list[str]:\\n"
            + "    return [sanitize_label(item) for item in labels]\\n",
            encoding="utf-8",
        )
        (repo / "tests/test_sanitizer.py").write_text(
            (repo / "tests/test_sanitizer.py").read_text(encoding="utf-8")
            + "\\n    def test_batch(self):\\n"
            + "        from src.sanitizer import sanitize_batch\\n"
            + "        self.assertEqual(sanitize_batch(['Alpha', 'Q4-2026']), ['Alpha', 'Series-Placeholder'])\\n",
            encoding="utf-8",
        )
    subprocess.run([sys.executable, controller_py, "execute", "--root", str(control), "--token", str(token)], check=False)

elif role == "reviewer":
    # Independent adversarial probe in process memory (never mutating repo)
    probe = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from src.sanitizer import sanitize_label; out = sanitize_label('2026'); assert out and len(out) > 0, 'empty output on numeric label'", str(repo)],
        capture_output=True,
        text=True,
    )
    ev_rel = f"outputs/evidence-r{round_num}.txt"
    (case / ev_rel).write_text(f"Adversarial probe exit={probe.returncode}\\nstderr={probe.stderr}\\n", encoding="utf-8")

    if probe.returncode != 0:
        fb = case / f"outputs/feedback-r{round_num}.md"
        fb.write_text(
            "ATTRIBUTION: code\\n\\n"
            "Unit tests passed, but independent probe `sanitize_label('2026')` returned an empty string. "
            "Numeric labels must retain a non-empty placeholder and empty inputs must raise ValueError.\\n",
            encoding="utf-8",
        )
        subprocess.run(
            [sys.executable, controller_py, "review", "--root", str(control), "--token", str(token), "--verdict", "reject", "--feedback", str(fb)],
            check=True,
        )
    else:
        b_sha = hashlib.sha256(baseline_file.read_bytes()).hexdigest()
        spec = json.loads((case / "spec.json").read_text(encoding="utf-8"))
        import csv
        with (case / spec["expected"]).open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        obs = {
            "baseline_sha256": b_sha,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "rows": [
                {
                    "id": r["id"],
                    "status": "PASS",
                    "detail": "Verified diff scope, unit check receipt, and adversarial numeric-label probe",
                    "evidence_level": "independent-probe",
                    "evidence": [ev_rel],
                }
                for r in rows if r["requirement"] == "required"
            ],
        }
        obs_rel = f"outputs/observations-r{round_num}.json"
        rep_rel = f"outputs/review-r{round_num}-comparison.json"
        (case / obs_rel).write_text(json.dumps(obs, indent=2), encoding="utf-8")
        subprocess.run(
            [sys.executable, contract_py, "compare", "--root", str(case), "--observations", obs_rel, "--out", rep_rel],
            check=True,
        )
        fb = case / f"outputs/feedback-r{round_num}.md"
        fb.write_text("Independent cold review passed all checks and adversarial counterexamples.\\n", encoding="utf-8")
        subprocess.run(
            [
                sys.executable, controller_py, "review",
                "--root", str(control), "--token", str(token),
                "--verdict", "accept", "--feedback", str(fb), "--report", rep_rel,
            ],
            check=True,
        )
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dual-loop-demo-") as tmp:
        base = Path(tmp).resolve()
        repo = base / "sample-service"
        state_dir = base / "roadmap-state"
        mock_agent = base / "mock_agent.py"
        mock_agent.write_text(MOCK_AGENT_CODE, encoding="utf-8")

        # 1. Seed a clean sample Git repository
        (repo / "core").mkdir(parents=True)
        (repo / "src").mkdir(parents=True)
        (repo / "tests").mkdir(parents=True)
        (repo / "core/kernel.py").write_text("KERNEL_VERSION = '1.0.0'\n", encoding="utf-8")
        (repo / "src/__init__.py").write_text("", encoding="utf-8")
        (repo / "src/sanitizer.py").write_text(
            "def sanitize_label(label: str) -> str:\n    return label\n",
            encoding="utf-8",
        )
        (repo / "tests/test_sanitizer.py").write_text(
            "import unittest\nfrom src.sanitizer import sanitize_label\n\n"
            "class SanitizerTests(unittest.TestCase):\n"
            "    def test_plain_text(self):\n"
            "        self.assertEqual(sanitize_label('Revenue'), 'Revenue')\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n",
            encoding="utf-8",
        )
        (repo / "POLICY.md").write_text("# Engineering Policy\nNever modify core/.\n", encoding="utf-8")
        (repo / "profile.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "sample-service",
                    "protected_paths": ["core/", "POLICY.md", "profile.json"],
                    "require_clean_worktree": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        git(repo, "init", "-q")
        git(repo, "config", "user.email", "demo@example.invalid")
        git(repo, "config", "user.name", "Dual-Loop Demo")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "Initial repository seed")
        initial_head = git(repo, "rev-parse", "HEAD")

        # 2. Define 2-task DAG roadmap
        roadmap = {
            "name": "demo-roadmap",
            "repo": str(repo),
            "profile": "profile.json",
            "policy_files": ["POLICY.md"],
            "isolate_worktree": True,
            "port_range": "3400-3450",
            "default_max_rounds": 3,
            "executor_backend": {"kind": "command", "argv": [sys.executable, str(mock_agent)]},
            "reviewer_backend": {"kind": "command", "argv": [sys.executable, str(mock_agent)]},
            "tasks": [
                {
                    "id": "sanitize-labels",
                    "title": "Sanitize numeric series labels safely",
                    "tier": "L3",
                    "allowed_paths": ["src/sanitizer.py", "tests/test_sanitizer.py"],
                    "protected_paths": [],
                    "checks": [
                        {"id": "unit-tests", "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests"], "cwd": "."}
                    ],
                    "criteria": [
                        {
                            "id": "safe-placeholder",
                            "requirement": "required",
                            "description": "Numeric labels become non-empty placeholder and plain labels stay intact",
                            "basis": "Data sanitization spec",
                            "acceptance": "Unit tests and adversarial numeric-string probe pass",
                        }
                    ],
                    "commit_message": "Sanitize numeric labels without producing empty names",
                },
                {
                    "id": "batch-export",
                    "title": "Add batch label sanitization helper",
                    "tier": "L2",
                    "depends_on": ["sanitize-labels"],
                    "allowed_paths": ["src/sanitizer.py", "tests/test_sanitizer.py"],
                    "protected_paths": [],
                    "checks": [
                        {"id": "unit-tests", "argv": [sys.executable, "-m", "unittest", "discover", "-s", "tests"], "cwd": "."}
                    ],
                    "criteria": [
                        {
                            "id": "batch-helper",
                            "requirement": "required",
                            "description": "sanitize_batch processes a sequence of labels consistently",
                            "basis": "Batch API spec",
                            "acceptance": "Batch unit test and cold review pass",
                        }
                    ],
                    "commit_message": "Add sanitize_batch helper and unit coverage",
                },
            ],
        }
        roadmap_file = base / "roadmap.json"
        roadmap_file.write_text(json.dumps(roadmap, indent=2) + "\n", encoding="utf-8")

        print("==> 1. Running 2-task DAG roadmap in an isolated Git worktree...")
        res = subprocess.run(
            [sys.executable, str(SCRIPTS / "roadmap.py"), "run", "--roadmap", str(roadmap_file), "--state-dir", str(state_dir)],
            capture_output=True,
            text=True,
            check=True,
        )
        summary = json.loads(res.stdout)
        print(json.dumps(summary, indent=2))
        assert summary["counts"] == {"accepted": 2}, summary
        assert summary["tasks"][0]["rounds_used"] == 2, "Task 1 should take 2 rounds (reject -> recycle -> accept)"
        assert summary["tasks"][1]["rounds_used"] == 1, "Task 2 should pass in 1 round"

        # Primary checkout was untouched while isolated worktree advanced!
        assert git(repo, "rev-parse", "HEAD") == initial_head

        print("\n==> 2. Integrating reviewed commits from isolated worktree into primary branch...")
        wt_path = summary["execution_repo"]
        integ = subprocess.run(
            [sys.executable, str(SCRIPTS / "worktree.py"), "integrate", "--repo", str(repo), "--worktree", wt_path],
            capture_output=True,
            text=True,
            check=True,
        )
        print(integ.stdout.strip())

        print("\n==> 3. Generating deterministic Git audit summary...")
        first_task_commit = summary["tasks"][0]["commit"]
        audit = subprocess.run(
            [sys.executable, str(SCRIPTS / "summary.py"), "--repo", str(repo), "--from", first_task_commit, "--to", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        print(audit.stdout.strip())

        print("\n==> 4. Cleaning up isolated worktree and verifying archived round replay...")
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "cleanup",
                "--repo",
                str(repo),
                "--worktree",
                wt_path,
                "--delete-branch",
                "--lock-file",
                str(state_dir / "LOCK"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        status_check = subprocess.run(
            [sys.executable, str(SCRIPTS / "controller.py"), "status", "--root", str(state_dir / "tasks/sanitize-labels/control")],
            capture_output=True,
            text=True,
            check=True,
        )
        print("Archived controller verification:", status_check.stdout.strip())
        print("\nDEMO PASSED SUCCESSFULLY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
