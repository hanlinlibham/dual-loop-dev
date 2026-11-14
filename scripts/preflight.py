#!/usr/bin/env python3
"""Read-only occupancy check before Git writes, worktree integration, or shared-port restarts."""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


CONTROLLER_SCRIPT = Path(__file__).with_name("controller.py").resolve()


def run_cmd(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=20, check=False)
        return res.stdout.strip()
    except Exception as exc:
        return f"<error: {exc}>"


def _load_controller_module(controller: Path):
    spec = importlib.util.spec_from_file_location("dual_loop_preflight_ctrl", controller)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_active_rounds(workspace: Path, controller: Path) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[Path] = set()
    ctrl_mod = _load_controller_module(controller)
    for state_file in sorted(workspace.rglob("state.json")):
        parts = state_file.parts
        if any(p in {".git", "node_modules", ".venv", "venv"} for p in parts):
            continue
        control_root = state_file.parent.resolve()
        if control_root in seen or not (control_root / "events.jsonl").is_file():
            continue
        seen.add(control_root)
        try:
            if ctrl_mod is not None:
                state, _, _ = ctrl_mod.read_state(control_root)
                phase = state.get("phase", "unknown")
            else:
                out = run_cmd([sys.executable, str(controller), "status", "--root", str(control_root)])
                phase = json.loads(out).get("phase", "unknown")
        except Exception:
            phase = "unknown"
        if phase in {"issued", "running", "awaiting_review", "unknown"}:
            found.append({"root": str(control_root), "phase": phase})
    return found


def find_locks(workspace: Path) -> list[dict[str, str]]:
    locks: list[dict[str, str]] = []
    for name in ("LOCK", "daemon.lock"):
        for lock_path in sorted(workspace.rglob(name)):
            if any(p in {".git", "node_modules", ".venv", "venv"} for p in lock_path.parts):
                continue
            if lock_path.is_file():
                try:
                    content = lock_path.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    content = "<unreadable>"
                locks.append({"path": str(lock_path), "content": content[:300]})
    return locks


def recent_agent_sessions(minutes: int) -> list[dict[str, str]]:
    cutoff = time.time() - minutes * 60
    patterns = [
        os.path.expanduser("~/.claude/projects/*/*.jsonl"),
        os.path.expanduser("~/.cursor/projects/*/agent-transcripts/*/*.jsonl"),
        os.path.expanduser("~/.codex/sessions/**/*.jsonl"),
        os.path.expanduser("~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl"),
    ]
    hits: list[tuple[float, str]] = []
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= cutoff:
                hits.append((mtime, path))
    hits.sort(reverse=True)
    return [
        {"modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m)), "path": p}
        for m, p in hits[:20]
    ]


def check_port(port: int | None) -> dict[str, object]:
    if port is None:
        return {"port": None, "in_use": False, "detail": "not configured"}
    in_use = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        if sock.connect_ex(("127.0.0.1", int(port))) == 0:
            in_use = True
    lsof_out = run_cmd(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    if lsof_out and not lsof_out.startswith("<error:"):
        in_use = True
    return {"port": int(port), "in_use": in_use, "detail": lsof_out or ("listening" if in_use else "free")}


def resolve_profile_port(repo: Path, profile_arg: Path | None, port_arg: int | None) -> int | None:
    if port_arg is not None:
        return port_arg
    candidates = [profile_arg] if profile_arg else [
        repo / "profile.json",
        repo / "docs" / "development" / "profile.json",
        repo / ".dual-loop" / "profile.json",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                port = data.get("preflight", {}).get("port")
                if port is not None:
                    return int(port)
            except Exception:
                continue
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Git repository path")
    parser.add_argument("--workspace", type=Path, help="Workspace directory to scan for active rounds and LOCKs")
    parser.add_argument("--profile", type=Path, help="Optional project profile.json")
    parser.add_argument("--port", type=int, help="Optional TCP port override to check")
    parser.add_argument("--minutes", type=int, default=30, help="Lookback window for active agent transcripts")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    workspace = (args.workspace or repo.parent).resolve()
    port = resolve_profile_port(repo, args.profile, args.port)

    rounds = find_active_rounds(workspace, CONTROLLER_SCRIPT)
    locks = find_locks(workspace)
    sessions = recent_agent_sessions(args.minutes)
    port_info = check_port(port)
    git_status = run_cmd(["git", "status", "-sb"], cwd=repo)
    git_reflog = run_cmd(["git", "reflog", "-5", "--date=format:%m-%d %H:%M"], cwd=repo)
    git_worktrees = run_cmd(["git", "worktree", "list"], cwd=repo)

    occupied = bool(rounds or locks)
    report = {
        "occupied": occupied,
        "verdict": (
            "OCCUPIED - coordinate or use an isolated worktree/port"
            if occupied
            else "CLEAR - no active round or lock found; check recent agent sessions before mutating shared branch"
        ),
        "active_rounds": rounds,
        "locks": locks,
        "recent_sessions": sessions,
        "port": port_info,
        "git": {
            "status": git_status,
            "reflog": git_reflog,
            "worktrees": git_worktrees,
        },
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("== active rounds ==")
        for r in rounds or [{"phase": "none", "root": ""}]:
            print(f"  {r['phase']:<16} {r['root']}")
        print(f"== agent sessions written in last {args.minutes} min ==")
        for s in sessions or [{"modified_at": "", "path": "none"}]:
            print(f"  {s['modified_at']} {s['path']}".rstrip())
        print("== LOCK files ==")
        for lk in locks or [{"path": "none", "content": ""}]:
            suffix = f": {lk['content']}" if lk["content"] else ""
            print(f"  {lk['path']}{suffix}")
        print("== git ==")
        for block in (git_status, git_reflog, git_worktrees):
            if block:
                print("  " + block.replace("\n", "\n  "))
        if port is not None:
            print(f"== port {port} ==")
            print("  " + str(port_info["detail"]).replace("\n", "\n  "))
        print("\nVERDICT:", report["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
