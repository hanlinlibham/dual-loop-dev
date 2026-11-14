#!/usr/bin/env python3
"""Isolated Git worktree, TCP port lease, LOCK file, and trailer-checked integration manager."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


TRAILER_RE = re.compile(r"^Accepted in supervised round .+ \(review [0-9a-f]{8,64}\)\s*$", re.MULTILINE)


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
    lease_file = wt_parent / f"{repo.name}-{task_id}.lease.json"
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
    return {"repo": str(repo), "worktrees": entries}


def integrate_worktree(
    repo: Path,
    *,
    worktree: Path,
    strategy: str = "ff-only",
    allow_unreviewed: bool = False,
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
        for rev in revs:
            body = git(worktree, "log", "-1", "--format=%B", rev)
            require(
                bool(TRAILER_RE.search(body)),
                f"commit {rev[:8]} lacks 'Accepted in supervised round ... (review ...)' trailer",
            )

    if strategy == "ff-only":
        git(repo, "merge", "--ff-only", wt_head)
    else:
        git(repo, "cherry-pick", *revs)

    new_head = git(repo, "rev-parse", "HEAD")
    return {"integrated": True, "commits": revs, "previous_head": target_head, "new_head": new_head}


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
    parser.add_argument("--worktrees-dir", type=Path, help="External directory holding isolated worktrees")
    parser.add_argument("--branch-prefix", default="supervised/", help="Branch prefix for isolated worktrees")
    parser.add_argument("--port-range", help="Optional TCP port range START-END (e.g. 3200-3299)")
    parser.add_argument("--lock-file", type=Path, help="Optional external LOCK file path")
    parser.add_argument("--owner", default="dual-loop", help="Lease owner label")
    parser.add_argument("--worktree", type=Path, help="Target isolated worktree path (for integrate/cleanup)")
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
