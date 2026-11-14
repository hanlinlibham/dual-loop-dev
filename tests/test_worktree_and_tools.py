"""Tests for worktree isolation, preflight occupancy, audit summary, and unified CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


class WorktreeAndToolingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="dual-loop-wt-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "app-repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "config", "user.email", "wt@example.invalid")
        self.git(self.repo, "config", "user.name", "Worktree Test")
        (self.repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "Initial commit")

    def git(self, repo: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()

    def test_worktree_lifecycle_port_lease_and_trailer_enforced_integration(self) -> None:
        lock_path = self.base / "workspace" / "LOCK"
        res = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "create",
                "--repo",
                str(self.repo),
                "--task-id",
                "feat-01",
                "--port-range",
                "3500-3550",
                "--lock-file",
                str(lock_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        lease = json.loads(res.stdout)
        wt = Path(lease["worktree"])
        self.assertTrue(wt.is_dir())
        self.assertGreaterEqual(lease["port"], 3500)
        self.assertTrue(lock_path.is_file())

        # Preflight detects LOCK occupancy
        pf = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "preflight.py"),
                "--repo",
                str(self.repo),
                "--workspace",
                str(self.base),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(json.loads(pf.stdout)["occupied"])

        # Commit WITHOUT supervised trailer must be rejected by integrate
        (wt / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.git(wt, "add", "app.py")
        self.git(wt, "commit", "-qm", "Unreviewed commit")
        bad_integ = subprocess.run(
            [sys.executable, str(SCRIPTS / "worktree.py"), "integrate", "--repo", str(self.repo), "--worktree", str(wt)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad_integ.returncode, 2)
        self.assertIn("trailer", bad_integ.stderr)

        # Amend with proper supervised trailer -> integrate succeeds
        self.git(wt, "commit", "--amend", "-qm", "Reviewed change\n\nAccepted in supervised round feat-01 (review a1b2c3d4)")
        ok_integ = subprocess.run(
            [sys.executable, str(SCRIPTS / "worktree.py"), "integrate", "--repo", str(self.repo), "--worktree", str(wt)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(json.loads(ok_integ.stdout)["integrated"])
        new_head = self.git(self.repo, "rev-parse", "HEAD")

        # Summary reports 1 commit with supervised trailer
        summ = subprocess.run(
            [sys.executable, str(SCRIPTS / "summary.py"), "--repo", str(self.repo), "--from", new_head, "--to", "HEAD", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        sdata = json.loads(summ.stdout)
        self.assertEqual(sdata["commit_count"], 1)
        self.assertEqual(sdata["supervised_trailer_count"], 1)

        # Cleanup removes worktree, branch, and LOCK
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "cleanup",
                "--repo",
                str(self.repo),
                "--worktree",
                str(wt),
                "--delete-branch",
                "--lock-file",
                str(lock_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertFalse(wt.exists())
        self.assertFalse(lock_path.exists())

    def test_explicit_worktree_path_sibling_lease_and_ledger_verified_integration(self) -> None:
        state_dir = self.base / "manual-state"
        explicit_wt = state_dir / "wt-pkg-01"
        res = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "create",
                "--repo",
                str(self.repo),
                "--task-id",
                "pkg-01",
                "--worktree",
                str(explicit_wt),
                "--port-range",
                "3560-3590",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        lease = json.loads(res.stdout)
        self.assertEqual(Path(lease["worktree"]).resolve(), explicit_wt.resolve())
        sibling_lease = state_dir / "wt-pkg-01.lease.json"
        self.assertTrue(sibling_lease.is_file())

        st = subprocess.run(
            [sys.executable, str(SCRIPTS / "worktree.py"), "status", "--repo", str(self.repo)],
            capture_output=True,
            text=True,
            check=True,
        )
        wt_entries = [w for w in json.loads(st.stdout)["worktrees"] if Path(w["worktree"]).resolve() == explicit_wt.resolve()]
        self.assertEqual(len(wt_entries), 1)
        self.assertEqual(wt_entries[0]["lease"]["task_id"], "pkg-01")

        # Run a genuine supervised round under state_dir/pkg-01/control
        ctrl = state_dir / "pkg-01" / "control"
        auth = state_dir / "pkg-01" / "authority"
        case = state_dir / "pkg-01" / "cases" / "r001"
        meta = state_dir / "meta"
        meta.mkdir(parents=True)
        profile = meta / "profile.json"
        policy = meta / "POLICY.md"
        profile.write_text(
            json.dumps({"schema_version": 1, "name": "app", "protected_paths": [], "require_clean_worktree": True}),
            encoding="utf-8",
        )
        policy.write_text("Policy\n", encoding="utf-8")
        criteria = state_dir / "pkg-01" / "criteria.json"
        criteria.parent.mkdir(parents=True, exist_ok=True)
        criteria.write_text(
            json.dumps(
                [
                    {
                        "id": "val-updated",
                        "requirement": "required",
                        "description": "VALUE is 42",
                        "basis": "Spec",
                        "acceptance": "Check passes",
                    }
                ]
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [sys.executable, str(SCRIPTS / "controller.py"), "init", "--root", str(ctrl), "--authority", str(auth)],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "contract.py"),
                "init-case",
                "--root",
                str(case),
                "--topic",
                "pkg-01",
                "--question",
                "Set VALUE=42",
                "--criteria-json",
                str(criteria),
            ],
            capture_output=True,
            check=True,
        )
        subprocess.run([sys.executable, str(SCRIPTS / "contract.py"), "freeze", "--root", str(case)], capture_output=True, check=True)
        manifest = state_dir / "pkg-01" / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "repo": str(explicit_wt),
                    "case_root": str(case),
                    "profile": str(profile),
                    "policy_files": [str(policy)],
                    "allowed_paths": ["app.py"],
                    "protected_paths": [],
                    "checks": [{"id": "syntax", "argv": [sys.executable, "-m", "py_compile", "app.py"], "cwd": "."}],
                    "max_rounds": 2,
                    "require_clean_worktree": True,
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "controller.py"),
                "issue",
                "--root",
                str(ctrl),
                "--token",
                str(auth / "supervisor.key"),
                "--manifest",
                str(manifest),
            ],
            capture_output=True,
            check=True,
        )
        (explicit_wt / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
        subprocess.run(
            [sys.executable, str(SCRIPTS / "controller.py"), "execute", "--root", str(ctrl), "--token", str(auth / "executor.key")],
            capture_output=True,
            check=True,
        )
        spec = json.loads((case / "spec.json").read_text(encoding="utf-8"))
        from datetime import datetime, timezone
        import hashlib
        b_sha = hashlib.sha256((case / spec["baseline"]).read_bytes()).hexdigest()
        (case / "outputs/ev.txt").write_text("ok\n", encoding="utf-8")
        (case / "outputs/obs.json").write_text(
            json.dumps(
                {
                    "baseline_sha256": b_sha,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "rows": [{"id": "val-updated", "status": "PASS", "detail": "ok", "evidence_level": "unit", "evidence": ["outputs/ev.txt"]}],
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "contract.py"),
                "compare",
                "--root",
                str(case),
                "--observations",
                "outputs/obs.json",
                "--out",
                "outputs/rev-comparison.json",
            ],
            capture_output=True,
            check=True,
        )
        fb = case / "outputs/fb.md"
        fb.write_text("Accepted.\n", encoding="utf-8")
        rev = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "controller.py"),
                "review",
                "--root",
                str(ctrl),
                "--token",
                str(auth / "reviewer.key"),
                "--verdict",
                "accept",
                "--feedback",
                str(fb),
                "--report",
                "outputs/rev-comparison.json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        real_rhash = json.loads(rev.stdout)["review_hash"][:8]

        # 1. Forged review hash trailer must be rejected when --state-dir is provided
        self.git(explicit_wt, "add", "app.py")
        self.git(explicit_wt, "commit", "-qm", "Forged hash\n\nAccepted in supervised round pkg-01-r1 (review deadbeef)")
        forged = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "integrate",
                "--repo",
                str(self.repo),
                "--worktree",
                str(explicit_wt),
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(forged.returncode, 2)
        self.assertIn("not found among accepted rounds", forged.stderr)

        # 2. Real review hash trailer with post-seal tampered commit content must be rejected
        (explicit_wt / "app.py").write_text("VALUE = 999\n", encoding="utf-8")
        self.git(explicit_wt, "add", "app.py")
        self.git(explicit_wt, "commit", "--amend", "-qm", f"Tampered tree\n\nAccepted in supervised round pkg-01-r1 (review {real_rhash})")
        tampered = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "integrate",
                "--repo",
                str(self.repo),
                "--worktree",
                str(explicit_wt),
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(tampered.returncode, 2)
        self.assertIn("candidate.json", tampered.stderr)

        # 3. Restoring exact sealed candidate tree succeeds with ledger_verified=True
        (explicit_wt / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
        self.git(explicit_wt, "add", "app.py")
        self.git(explicit_wt, "commit", "--amend", "-qm", f"Verified change\n\nAccepted in supervised round pkg-01-r1 (review {real_rhash})")
        verified = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "integrate",
                "--repo",
                str(self.repo),
                "--worktree",
                str(explicit_wt),
                "--state-dir",
                str(state_dir),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        vdata = json.loads(verified.stdout)
        self.assertTrue(vdata["integrated"])
        self.assertTrue(vdata["ledger_verified"])

        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "worktree.py"),
                "cleanup",
                "--repo",
                str(self.repo),
                "--worktree",
                str(explicit_wt),
                "--delete-branch",
            ],
            capture_output=True,
            check=True,
        )
        self.assertFalse(sibling_lease.exists())

    def test_unified_cli_dispatcher(self) -> None:
        env = {**dict(subprocess.os.environ), "PYTHONPATH": str(REPO_ROOT)}
        ver = subprocess.run([sys.executable, "-m", "dual_loop.cli", "--version"], capture_output=True, text=True, env=env, check=True)
        self.assertEqual(ver.stdout.strip(), "0.1.1")
        hlp = subprocess.run([sys.executable, "-m", "dual_loop.cli", "controller", "--help"], capture_output=True, text=True, env=env, check=True)
        self.assertIn("recycle", hlp.stdout)

    def test_agent_self_bootstrap_entrypoints(self) -> None:
        for required_doc in ("AGENTS.md", "CLAUDE.md", "SKILL.md", "README.md", "README.en.md"):
            doc_path = REPO_ROOT / required_doc
            self.assertTrue(doc_path.is_file(), f"missing agent entrypoint: {required_doc}")
            self.assertGreater(len(doc_path.read_text(encoding="utf-8").strip()), 100)
        agents_text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("scripts/controller.py", agents_text)
        self.assertIn("scripts/contract.py", agents_text)
        self.assertIn("Accepted in supervised round", agents_text)


if __name__ == "__main__":
    unittest.main()
