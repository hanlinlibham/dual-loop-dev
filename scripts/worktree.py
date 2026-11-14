#!/usr/bin/env python3
"""Isolated Git worktree, TCP port lease, LOCK file, and trailer-checked integration manager."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


TRAILER_RE = re.compile(
    r"^Accepted in supervised round (?P<round_label>\S+) \(review (?P<review_prefix>[0-9a-f]{8,64})\)\s*$",
    re.MULTILINE,
)


def _load_controller_module():
    ctrl_path = Path(__file__).with_name("controller.py").resolve()
    spec = importlib.util.spec_from_file_location("dual_loop_wt_ctrl", ctrl_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ctrl_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    require(res.returncode == 0, f"git {' '.join(args[:2])} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def find_free_port(start: int, end: int, reserved: set[int] | None = None) -> int:
    reserved = reserved or set()
    require(1024 <= start <= end <= 65535, "port range must be within 1024..65535")
    for port in range(start, end + 1):
        if port in reserved:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise ValueError(f"no free TCP port available in {start}..{end}")


def parse_port_range(spec: str | None) -> tuple[int, int] | None:
    if not spec:
        return None
    m = re.fullmatch(r"(\d+)-(\d+)", spec.strip())
    require(m is not None, "port range must be START-END (e.g. 3200-3299)")
    return int(m.group(1)), int(m.group(2))


def create_worktree(
    repo: Path,
    *,
    task_id: str,
    base_ref: str = "HEAD",
    worktree: Path | None = None,
    worktrees_dir: Path | None = None,
    branch_prefix: str = "supervised/",
    port_range: tuple[int, int] | None = None,
    lock_file: Path | None = None,
    owner: str = "dual-loop",
) -> dict[str, object]:
    repo = repo.resolve()
    require(bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", task_id)), "invalid task_id")
    toplevel = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    require(toplevel == repo, "repo must be a Git worktree root")

    if worktree is not None:
        wt_path = worktree.resolve()
        require(
            wt_path != repo and repo not in wt_path.parents and wt_path not in repo.parents,
            "worktree must be outside repo",
        )
        require(not wt_path.exists(), f"worktree path already exists: {wt_path}")
        wt_parent = wt_path.parent
        wt_parent.mkdir(parents=True, exist_ok=True)
    else:
        wt_parent = (worktrees_dir or (repo.parent / ".dual-loop-worktrees")).resolve()
        require(wt_parent != repo and repo not in wt_parent.parents, "worktrees_dir must be outside repo")
        wt_parent.mkdir(parents=True, exist_ok=True)
        wt_path = wt_parent / f"{repo.name}-{task_id}"
        require(not wt_path.exists(), f"worktree path already exists: {wt_path}")

    branch = f"{branch_prefix}{task_id}"
    base_head = git(repo, "rev-parse", base_ref)
    existing_branches = git(repo, "branch", "--list", branch)
    if existing_branches:
        git(repo, "worktree", "add", str(wt_path), branch)
    else:
        git(repo, "worktree", "add", "-b", branch, str(wt_path), base_head)

    port = None
    if port_range is not None:
        reserved: set[int] = set()
        for meta in wt_parent.glob("*.lease.json"):
            try:
                p = json.loads(meta.read_text(encoding="utf-8")).get("port")
                if isinstance(p, int):
                    reserved.add(p)
            except Exception:
                pass
        port = find_free_port(port_range[0], port_range[1], reserved)

    lease = {
        "schema_version": 1,
        "task_id": task_id,
        "owner": owner,
        "pid": os.getpid(),
        "source_repo": str(repo),
        "worktree": str(wt_path),
        "branch": branch,
        "base_head": base_head,
        "port": port,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    lease_file = wt_parent / f"{wt_path.name}.lease.json"
    lease_file.write_text(json.dumps(lease, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if lock_file is not None:
        lock_file = lock_file.resolve()
        require(repo not in lock_file.parents, "lock_file must be outside repo")
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(json.dumps(lease, ensure_ascii=False) + "\n", encoding="utf-8")
        lease["lock_file"] = str(lock_file)
    return lease


def list_worktrees(repo: Path, worktrees_dir: Path | None = None) -> dict[str, object]:
    repo = repo.resolve()
    raw = git(repo, "worktree", "list", "--porcelain")
    entries: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["worktree"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        else:
            current[line] = True
    if current:
        entries.append(current)

    wt_parent = (worktrees_dir or (repo.parent / ".dual-loop-worktrees")).resolve()
    leases = {}
    if wt_parent.is_dir():
        for lf in sorted(wt_parent.glob("*.lease.json")):
            try:
                data = json.loads(lf.read_text(encoding="utf-8"))
                leases[data.get("worktree", "")] = data
            except Exception:
                continue
    for item in entries:
        wt = str(item.get("worktree", ""))
        if wt in leases:
            item["lease"] = leases[wt]
        elif wt:
            sibling_lease = Path(wt).parent / f"{Path(wt).name}.lease.json"
            if sibling_lease.is_file():
                try:
                    item["lease"] = json.loads(sibling_lease.read_text(encoding="utf-8"))
                except Exception:
                    pass
    return {"repo": str(repo), "worktrees": entries}


def _discover_control_roots(state_dir: Path) -> list[Path]:
    state_dir = state_dir.resolve()
    candidates: list[Path] = []
    seen: set[Path] = set()

    def _add(ctrl: Path) -> None:
        resolved = ctrl.resolve()
        if resolved in seen:
            return
        if (resolved / "state.json").is_file() and (resolved / "events.jsonl").is_file():
            seen.add(resolved)
            candidates.append(resolved)

    _add(state_dir)
    _add(state_dir / "control")
    for ctrl in sorted(state_dir.glob("*/control")):
        if not (ctrl.parent / ".git").exists():
            _add(ctrl)
    tasks_dir = state_dir / "tasks"
    if tasks_dir.is_dir():
        for ctrl in sorted(tasks_dir.glob("*/control")):
            _add(ctrl)
    require(bool(candidates), f"no supervised control ledger found under {state_dir}")
    return candidates


def _collect_verified_acceptances(state_dir: Path) -> list[dict[str, object]]:
    ctrl_mod = _load_controller_module()
    accepted: list[dict[str, object]] = []
    for ctrl_root in _discover_control_roots(state_dir):
        state, _, _ = ctrl_mod.read_state(ctrl_root)
        for round_ in state.get("rounds", []):
            rhash = round_.get("review_hash")
            if not rhash:
                continue
            rdir = ctrl_root / round_["directory"]
            rev_file = rdir / "review.json"
            exec_file = rdir / "execution.json"
            cand_file = rdir / "candidate.json"
            if not (rev_file.is_file() and exec_file.is_file() and cand_file.is_file()):
                continue
            rev_data = json.loads(rev_file.read_text(encoding="utf-8"))
            exec_data = json.loads(exec_file.read_text(encoding="utf-8"))
            if rev_data.get("verdict") == "accept" and exec_data.get("passed") is True:
                accepted.append(
                    {
                        "control_root": str(ctrl_root),
                        "round": round_["number"],
                        "review_hash": str(rhash),
                        "candidate": json.loads(cand_file.read_text(encoding="utf-8")),
                    }
                )
    return accepted


def _verify_commit_matches_candidate(worktree: Path, rev: str, candidate: dict[str, dict]) -> None:
    raw_tree = subprocess.run(
        ["git", "-C", str(worktree), "ls-tree", "-r", "-z", rev],
        capture_output=True,
        check=True,
    ).stdout
    tree_entries: dict[str, tuple[str, str, str]] = {}
    for entry in raw_tree.split(b"\0"):
        if not entry:
            continue
        header, raw_path = entry.split(b"\t", 1)
        mode_str, obj_type, oid = header.decode("ascii").split()
        tree_entries[os.fsdecode(raw_path)] = (mode_str, obj_type, oid)

    expected = {p: meta for p, meta in candidate.items() if meta.get("kind") != "missing"}
    extra = sorted(set(tree_entries) - set(expected))
    missing = sorted(set(expected) - set(tree_entries))
    require(
        not extra and not missing,
        f"commit {rev[:8]} tree paths diverge from sealed candidate.json (extra={extra}, missing={missing})",
    )

    for path, meta in expected.items():
        mode_str, _obj_type, oid = tree_entries[path]
        kind = meta.get("kind")
        if kind == "file":
            require(mode_str in ("100644", "100755"), f"unexpected git mode {mode_str} on {path}")
            require(
                (mode_str == "100755") == bool(int(meta["mode"]) & 0o111),
                f"commit {rev[:8]} executable bit mismatch on {path}",
            )
            blob = subprocess.run(
                ["git", "-C", str(worktree), "cat-file", "blob", oid],
                capture_output=True,
                check=True,
            ).stdout
            require(
                hashlib.sha256(blob).hexdigest() == meta["sha256"],
                f"commit {rev[:8]} file content hash mismatch against sealed candidate.json on {path}",
            )
        elif kind == "symlink":
            require(mode_str == "120000", f"commit {rev[:8]} expected symlink mode on {path}")
            blob = subprocess.run(
                ["git", "-C", str(worktree), "cat-file", "blob", oid],
                capture_output=True,
                check=True,
            ).stdout
            require(os.fsdecode(blob) == meta["target"], f"commit {rev[:8]} symlink target mismatch on {path}")
        elif kind == "gitlink":
            require(mode_str == "160000" and oid == meta["index"], f"commit {rev[:8]} gitlink mismatch on {path}")


def integrate_worktree(
    repo: Path,
    *,
    worktree: Path,
    strategy: str = "ff-only",
    allow_unreviewed: bool = False,
    state_dir: Path | None = None,
) -> dict[str, object]:
    repo = repo.resolve()
    worktree = worktree.resolve()
    require(worktree.is_dir(), f"worktree does not exist: {worktree}")
    require(not git(repo, "status", "--porcelain"), "target repository has uncommitted changes; refuse integration")
    require(not git(worktree, "status", "--porcelain"), "isolated worktree has uncommitted changes; refuse integration")

    target_head = git(repo, "rev-parse", "HEAD")
    wt_head = git(worktree, "rev-parse", "HEAD")
    if target_head == wt_head:
        return {"integrated": False, "reason": "no new commits to integrate", "head": target_head}

    revs = [r for r in git(worktree, "rev-list", "--reverse", f"{target_head}..{wt_head}").splitlines() if r.strip()]
    require(bool(revs), "worktree does not descend from target HEAD; rebase or inspect manually")

    if not allow_unreviewed:
        verified_acceptances = _collect_verified_acceptances(state_dir) if state_dir is not None else None
        for rev in revs:
            body = git(worktree, "log", "-1", "--format=%B", rev)
            m = TRAILER_RE.search(body)
            require(
                m is not None,
                f"commit {rev[:8]} lacks 'Accepted in supervised round ... (review ...)' trailer",
            )
            if verified_acceptances is not None:
                prefix = m.group("review_prefix")
                matches = [rec for rec in verified_acceptances if str(rec["review_hash"]).startswith(prefix)]
                require(
                    bool(matches),
                    f"commit {rev[:8]} review hash '{prefix}' not found among accepted rounds in {state_dir}",
                )
                last_err: str | None = None
                matched_tree = False
                for rec in matches:
                    try:
                        _verify_commit_matches_candidate(worktree, rev, rec["candidate"])
                        matched_tree = True
                        break
                    except ValueError as exc:
                        last_err = str(exc)
                require(matched_tree, last_err or f"commit {rev[:8]} does not match sealed candidate.json")

    if strategy == "ff-only":
        git(repo, "merge", "--ff-only", wt_head)
    else:
        git(repo, "cherry-pick", *revs)

    new_head = git(repo, "rev-parse", "HEAD")
    return {
        "integrated": True,
        "commits": revs,
        "previous_head": target_head,
        "new_head": new_head,
        "ledger_verified": bool(state_dir is not None and not allow_unreviewed),
    }


def cleanup_worktree(
    repo: Path,
    *,
    worktree: Path,
    delete_branch: bool = False,
    lock_file: Path | None = None,
    force: bool = False,
) -> dict[str, object]:
    repo = repo.resolve()
    worktree = worktree.resolve()
    branch = None
    if worktree.is_dir():
        if not force:
            require(not git(worktree, "status", "--porcelain"), "worktree has uncommitted changes; pass --force to discard")
        branch_ref = git(worktree, "symbolic-ref", "-q", "--short", "HEAD") if not force else ""
        branch = branch_ref or None
        args = ["worktree", "remove"] + (["--force"] if force else []) + [str(worktree)]
        git(repo, *args)

    lease_candidate = worktree.parent / f"{worktree.name}.lease.json"
    if lease_candidate.is_file():
        if branch is None:
            try:
                branch = json.loads(lease_candidate.read_text(encoding="utf-8")).get("branch")
            except Exception:
                pass
        lease_candidate.unlink()

    if lock_file and lock_file.is_file():
        lock_file.unlink()

    deleted_branch = False
    if delete_branch and branch:
        flag = "-D" if force else "-d"
        git(repo, "branch", flag, branch)
        deleted_branch = True

    return {"removed_worktree": str(worktree), "deleted_branch": branch if deleted_branch else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "status", "integrate", "cleanup"))
    parser.add_argument("--repo", required=True, type=Path, help="Primary Git repository root")
    parser.add_argument("--task-id", help="Task identifier for create")
    parser.add_argument("--base-ref", default="HEAD", help="Base Git revision for create")
    parser.add_argument("--worktrees-dir", type=Path, help="External parent directory holding isolated worktrees")
    parser.add_argument("--branch-prefix", default="supervised/", help="Branch prefix for isolated worktrees")
    parser.add_argument("--port-range", help="Optional TCP port range START-END (e.g. 3200-3299)")
    parser.add_argument("--lock-file", type=Path, help="Optional external LOCK file path")
    parser.add_argument("--owner", default="dual-loop", help="Lease owner label")
    parser.add_argument("--worktree", type=Path, help="Target isolated worktree path (for create/integrate/cleanup)")
    parser.add_argument("--state-dir", type=Path, help="Optional control/orchestrator/roadmap state dir to cryptographically verify commit trees against sealed candidate.json on integrate")
    parser.add_argument("--strategy", choices=("ff-only", "cherry-pick"), default="ff-only")
    parser.add_argument("--allow-unreviewed", action="store_true", help="Allow integrating commits without review trailer")
    parser.add_argument("--delete-branch", action="store_true", help="Delete worktree branch on cleanup")
    parser.add_argument("--force", action="store_true", help="Force removal of dirty/unmerged worktree")
    args = parser.parse_args(argv)

    if args.action == "create":
        require(bool(args.task_id), "create requires --task-id")
        out = create_worktree(
            args.repo,
            task_id=args.task_id,
            base_ref=args.base_ref,
            worktree=args.worktree,
            worktrees_dir=args.worktrees_dir,
            branch_prefix=args.branch_prefix,
            port_range=parse_port_range(args.port_range),
            lock_file=args.lock_file,
            owner=args.owner,
        )
    elif args.action == "status":
        out = list_worktrees(args.repo, args.worktrees_dir)
    elif args.action == "integrate":
        require(args.worktree is not None, "integrate requires --worktree")
        out = integrate_worktree(
            args.repo,
            worktree=args.worktree,
            strategy=args.strategy,
            allow_unreviewed=args.allow_unreviewed,
            state_dir=args.state_dir,
        )
    else:
        require(args.worktree is not None, "cleanup requires --worktree")
        out = cleanup_worktree(
            args.repo,
            worktree=args.worktree,
            delete_branch=args.delete_branch,
            lock_file=args.lock_file,
            force=args.force,
        )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
