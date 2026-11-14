#!/usr/bin/env python3
"""Unattended multi-task DAG queue and daemon runner with automatic Git worktree isolation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time


SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str):
    path = SCRIPTS_DIR / name
    spec = importlib.util.spec_from_file_location(f"dual_loop_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


orchestrator_mod = _load_sibling("orchestrator.py")
worktree_mod = _load_sibling("worktree.py")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_event(state_dir: Path, event_type: str, payload: dict) -> None:
    rec = {"at": now_iso(), "event": event_type, **payload}
    with (state_dir / "roadmap-events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_progress_markdown(state_dir: Path, state: dict) -> Path:
    lines = [
        f"# Roadmap Progress: {state.get('name', 'unnamed')}",
        "",
        f"- **Updated**: `{now_iso()}`",
        f"- **Source Repo**: `{state['repo']}`",
        f"- **Execution Worktree**: `{state['execution_repo']}` (branch `{state.get('branch', 'current')}`)",
        f"- **Isolated Port**: `{state.get('port') or 'n/a'}`",
        "",
        "| Task ID | Title | Tier | Depends On | Status | Rounds | Review Hash | Commit | Note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for tid in state["task_order"]:
        t = state["tasks"][tid]
        deps = ", ".join(t.get("depends_on", [])) or "-"
        rhash = (t.get("review_hash") or "-")[:8]
        commit = (t.get("commit") or "-")[:8]
        note = t.get("blocked_reason") or ""
        lines.append(
            f"| `{tid}` | {t.get('title', tid)} | {t.get('tier', 'L2')} | {deps} | "
            f"**{t['status']}** | {t.get('rounds_used', 0)}/{t.get('max_rounds', 3)} | `{rhash}` | `{commit}` | {note} |"
        )
    out = state_dir / "progress.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def sync_roadmap_state(roadmap: dict, state_dir: Path) -> dict:
    repo = Path(roadmap["repo"]).resolve()
    require(repo.is_dir(), f"roadmap repo does not exist: {repo}")
    require(state_dir != repo and repo not in state_dir.parents, "state_dir must be outside repo")
    state_file = state_dir / "roadmap-state.json"

    if state_file.is_file():
        state = load_json(state_file)
    else:
        name = roadmap.get("name", "roadmap")
        isolate = bool(roadmap.get("isolate_worktree", True))
        if isolate:
            port_range = worktree_mod.parse_port_range(roadmap.get("port_range"))
            wt_dir = Path(roadmap["worktrees_dir"]).resolve() if roadmap.get("worktrees_dir") else (state_dir / "worktrees")
            lease = worktree_mod.create_worktree(
                repo,
                task_id=name,
                base_ref=roadmap.get("base_ref", "HEAD"),
                worktrees_dir=wt_dir,
                port_range=port_range,
                lock_file=state_dir / "LOCK",
                owner=f"roadmap:{name}",
            )
            exec_repo = lease["worktree"]
            branch = lease["branch"]
            port = lease["port"]
        else:
            exec_repo = str(repo)
            branch = None
            port = None

        state = {
            "schema_version": 1,
            "name": name,
            "repo": str(repo),
            "execution_repo": exec_repo,
            "isolated_worktree": isolate,
            "branch": branch,
            "port": port,
            "created_at": now_iso(),
            "task_order": [],
            "tasks": {},
        }

    known_ids = {t["id"] for t in roadmap.get("tasks", [])}
    for task_def in roadmap.get("tasks", []):
        tid = task_def["id"]
        for dep in task_def.get("depends_on", []):
            require(dep in known_ids or dep in state["tasks"], f"task '{tid}' depends on unknown task '{dep}'")
        if tid not in state["tasks"]:
            state["task_order"].append(tid)
            state["tasks"][tid] = {
                "id": tid,
                "title": task_def.get("title", tid),
                "tier": task_def.get("tier", "L2"),
                "depends_on": task_def.get("depends_on", []),
                "max_rounds": int(task_def.get("max_rounds", roadmap.get("default_max_rounds", 3))),
                "status": "pending",
                "rounds_used": 0,
                "review_hash": None,
                "commit": None,
                "blocked_reason": None,
            }
        else:
            state["tasks"][tid]["depends_on"] = task_def.get("depends_on", state["tasks"][tid]["depends_on"])

    save_json(state_file, state)
    write_progress_markdown(state_dir, state)
    return state


def build_single_task_config(roadmap: dict, task_def: dict, state: dict) -> dict:
    repo_root = Path(state["repo"])
    profile_raw = task_def.get("profile") or roadmap["profile"]
    profile_path = Path(profile_raw) if Path(profile_raw).is_absolute() else (repo_root / profile_raw)
    raw_policies = task_def.get("policy_files") or roadmap.get("policy_files", [])
    policy_paths = [str(Path(p) if Path(p).is_absolute() else (repo_root / p)) for p in raw_policies]

    merged = {
        **task_def,
        "repo": state["execution_repo"],
        "profile": str(profile_path.resolve()),
        "policy_files": policy_paths,
        "max_rounds": int(task_def.get("max_rounds", roadmap.get("default_max_rounds", 3))),
        "commit_on_accept": bool(task_def.get("commit_on_accept", True)),
    }
    for backend_key in ("executor_backend", "reviewer_backend"):
        if backend_key not in merged and backend_key in roadmap:
            merged[backend_key] = roadmap[backend_key]
    if "contract_script" not in merged and "contract_script" in roadmap:
        merged["contract_script"] = roadmap["contract_script"]
    return merged


def run_queue_pass(roadmap_file: Path, state_dir: Path, stop_flag: dict[str, bool]) -> dict:
    roadmap = load_json(roadmap_file)
    state = sync_roadmap_state(roadmap, state_dir)
    defs_by_id = {t["id"]: t for t in roadmap.get("tasks", [])}
    continue_independent = bool(roadmap.get("continue_independent_on_failure", False))

    progressed = True
    while progressed and not stop_flag.get("stop", False):
        progressed = False
        for tid in state["task_order"]:
            entry = state["tasks"][tid]
            if entry["status"] != "pending":
                continue
            deps = entry.get("depends_on", [])
            dep_statuses = [state["tasks"][d]["status"] for d in deps]
            if any(s in {"failed", "budget_exhausted", "blocked"} for s in dep_statuses):
                entry["status"] = "blocked"
                entry["blocked_reason"] = "dependency_failed"
                save_json(state_dir / "roadmap-state.json", state)
                write_progress_markdown(state_dir, state)
                progressed = True
                continue
            if all(s == "accepted" for s in dep_statuses):
                task_def = defs_by_id[tid]
                task_cfg = build_single_task_config(roadmap, task_def, state)
                entry["status"] = "running"
                save_json(state_dir / "roadmap-state.json", state)
                write_progress_markdown(state_dir, state)
                append_event(state_dir, "task_start", {"task_id": tid})

                task_state_dir = state_dir / "tasks" / tid
                res = orchestrator_mod.orchestrate_task(task_cfg, state_dir=task_state_dir)
                entry["status"] = res["status"]
                entry["rounds_used"] = res.get("rounds_used", 0)
                entry["review_hash"] = res.get("review_hash")
                entry["commit"] = res.get("commit")
                entry["blocked_reason"] = res.get("blocked_reason")
                save_json(state_dir / "roadmap-state.json", state)
                write_progress_markdown(state_dir, state)
                append_event(state_dir, "task_finish", res)
                progressed = True

                if res["status"] != "accepted" and not continue_independent:
                    return state
                break

    if (
        bool(roadmap.get("auto_integrate", False))
        and state.get("isolated_worktree")
        and state["task_order"]
        and all(state["tasks"][tid]["status"] == "accepted" for tid in state["task_order"])
    ):
        integration = worktree_mod.integrate_worktree(
            Path(state["repo"]),
            worktree=Path(state["execution_repo"]),
            strategy=roadmap.get("integrate_strategy", "ff-only"),
        )
        state["integration"] = integration
        save_json(state_dir / "roadmap-state.json", state)
        append_event(state_dir, "auto_integrated", integration)
    return state


def run_daemon(
    roadmap_file: Path,
    state_dir: Path,
    *,
    poll_interval: float = 10.0,
    max_cycles: int | None = None,
) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "daemon.lock"
    heartbeat_path = state_dir / "daemon.heartbeat.json"
    stop_flag = {"stop": False}

    def handle_stop(signum: int, _frame) -> None:
        stop_flag["stop"] = True
        append_event(state_dir, "daemon_signal", {"signal": signum})

    old_int = signal.signal(signal.SIGINT, handle_stop)
    old_term = signal.signal(signal.SIGTERM, handle_stop)

    lock_handle = lock_path.open("w", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"another roadmap daemon is already holding {lock_path}") from exc
        lock_handle.write(json.dumps({"pid": os.getpid(), "started_at": now_iso()}) + "\n")
        lock_handle.flush()

        cycles = 0
        last_state: dict = {}
        while not stop_flag["stop"]:
            cycles += 1
            save_json(
                heartbeat_path,
                {"pid": os.getpid(), "cycle": cycles, "heartbeat_at": now_iso(), "roadmap": str(roadmap_file)},
            )
            last_state = run_queue_pass(roadmap_file, state_dir, stop_flag)
            if max_cycles is not None and cycles >= max_cycles:
                break
            deadline = time.monotonic() + poll_interval
            while not stop_flag["stop"] and time.monotonic() < deadline:
                time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))

        print(json.dumps(_summarize_state(last_state, state_dir), ensure_ascii=False, indent=2))
        all_ok = bool(last_state.get("task_order")) and all(
            last_state["tasks"][tid]["status"] == "accepted" for tid in last_state["task_order"]
        )
        return 0 if all_ok else 1
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_handle.close()
        if lock_path.is_file():
            lock_path.unlink()


def _summarize_state(state: dict, state_dir: Path) -> dict:
    counts: dict[str, int] = {}
    for tid in state.get("task_order", []):
        st = state["tasks"][tid]["status"]
        counts[st] = counts.get(st, 0) + 1
    return {
        "name": state.get("name"),
        "repo": state.get("repo"),
        "execution_repo": state.get("execution_repo"),
        "branch": state.get("branch"),
        "port": state.get("port"),
        "progress_file": str(state_dir / "progress.md"),
        "counts": counts,
        "tasks": [state["tasks"][tid] for tid in state.get("task_order", [])],
        "integration": state.get("integration"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "daemon", "status"))
    parser.add_argument("--roadmap", type=Path, help="Roadmap JSON specification file (required for run/daemon)")
    parser.add_argument("--state-dir", required=True, type=Path, help="External directory storing state, worktrees, and logs")
    parser.add_argument("--poll-interval", type=float, default=10.0, help="Daemon poll interval in seconds")
    parser.add_argument("--max-cycles", type=int, help="Optional maximum daemon poll cycles before exiting")
    args = parser.parse_args(argv)

    state_dir = args.state_dir.resolve()
    if args.action == "status":
        state_file = state_dir / "roadmap-state.json"
        require(state_file.is_file(), f"roadmap state not found at {state_file}")
        state = load_json(state_file)
        print(json.dumps(_summarize_state(state, state_dir), ensure_ascii=False, indent=2))
        return 0

    require(args.roadmap is not None, f"{args.action} requires --roadmap")
    roadmap_file = args.roadmap.resolve()
    if args.action == "run":
        state = run_queue_pass(roadmap_file, state_dir, {"stop": False})
        summary = _summarize_state(state, state_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        all_accepted = bool(state["task_order"]) and all(
            state["tasks"][tid]["status"] == "accepted" for tid in state["task_order"]
        )
        return 0 if all_accepted else 1

    return run_daemon(
        roadmap_file,
        state_dir,
        poll_interval=args.poll_interval,
        max_cycles=args.max_cycles,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
