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

    def test_unified_cli_dispatcher(self) -> None:
        env = {**dict(subprocess.os.environ), "PYTHONPATH": str(REPO_ROOT)}
        ver = subprocess.run([sys.executable, "-m", "dual_loop.cli", "--version"], capture_output=True, text=True, env=env, check=True)
        self.assertEqual(ver.stdout.strip(), "0.1.0")
        hlp = subprocess.run([sys.executable, "-m", "dual_loop.cli", "controller", "--help"], capture_output=True, text=True, env=env, check=True)
        self.assertIn("recycle", hlp.stdout)


if __name__ == "__main__":
    unittest.main()
