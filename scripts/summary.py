#!/usr/bin/env python3
"""Generate deterministic completion-report metrics from Git history and supervised trailers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


TRAILER_PREFIX = "Accepted in supervised round"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Git repository path")
    parser.add_argument("--from", dest="start", required=True, help="First commit of the task (inclusive) or base..head base")
    parser.add_argument("--to", default="HEAD", help="End revision (default: HEAD)")
    parser.add_argument("--path", default="", help="Optional path filter")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    try:
        git(repo, "rev-parse", "--verify", f"{args.start}^")
        base_ref = f"{args.start}^"
        span = f"{args.start}^..{args.to}"
    except subprocess.CalledProcessError:
        base_ref = args.start
        span = f"{args.start}..{args.to}"

    extra = ["--", args.path] if args.path else []
    raw_commits = git(repo, "log", "--reverse", "--format=%h %s", span, *extra)
    commits = [line for line in raw_commits.splitlines() if line.strip()]
    accepted = [
        c for c in commits if TRAILER_PREFIX in git(repo, "log", "-1", "--format=%B", c.split()[0])
    ]
    diffstat = git(repo, "diff", "--shortstat", base_ref, args.to, *extra)
    try:
        upstream = git(repo, "rev-list", "--left-right", "--count", f"{args.to}...@{{u}}")
    except subprocess.CalledProcessError:
        upstream = "no upstream for the current branch"

    payload = {
        "span": span,
        "path_filter": args.path or None,
        "commit_count": len(commits),
        "supervised_trailer_count": len(accepted),
        "commits": commits,
        "diffstat": diffstat,
        "ahead_behind_upstream": upstream,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        suffix = f" for {args.path}" if args.path else ""
        print(f"commits in {span}{suffix}: {len(commits)}")
        print(f"  with supervised-round trailer: {len(accepted)}")
        for line in commits:
            print("  " + line)
        print("diffstat:", diffstat or "0 files changed")
        print("ahead/behind upstream:", upstream)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: {exc.stderr.strip() or exc}", file=sys.stderr)
        sys.exit(2)
