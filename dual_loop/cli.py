#!/usr/bin/env python3
"""Unified CLI dispatcher for dual-loop-dev."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from dual_loop import __version__


SUBCOMMANDS = {
    "contract": ("contract.py", "Freeze, check, compare, and replay evidence contracts"),
    "controller": ("controller.py", "Run single-attempt round state machine (init/issue/execute/review/recycle/status)"),
    "worktree": ("worktree.py", "Manage isolated Git worktrees, port leases, locks, and trailer-checked integration"),
    "orchestrate": ("orchestrator.py", "Automate outer-loop executor + cold reviewer dispatch for one task"),
    "roadmap": ("roadmap.py", "Run unattended multi-task DAG queue or daemon with isolated worktrees"),
    "preflight": ("preflight.py", "Read-only occupancy check for active rounds, locks, sessions, and ports"),
    "summary": ("summary.py", "Generate deterministic Git audit summary from supervised-round trailers"),
}


def _locate_scripts_dir() -> Path:
    repo_scripts = Path(__file__).resolve().parent.parent / "scripts"
    if repo_scripts.is_dir():
        return repo_scripts
    shared_scripts = Path(sys.prefix) / "share" / "dual-loop-dev" / "scripts"
    if shared_scripts.is_dir():
        return shared_scripts
    raise FileNotFoundError("Could not locate dual-loop-dev scripts directory")


def _load_script_module(script_name: str):
    path = _locate_scripts_dir() / script_name
    spec = importlib.util.spec_from_file_location(f"dual_loop_script_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _print_help() -> None:
    lines = [
        f"dual-loop {__version__} - Dual-loop supervised automated development framework",
        "",
        "Usage:",
        "  dual-loop <command> [args...]",
        "  dual-loop --version | --help",
        "",
        "Commands:",
    ]
    for name, (_, desc) in SUBCOMMANDS.items():
        lines.append(f"  {name:<14} {desc}")
    lines.append("")
    lines.append("Run 'dual-loop <command> --help' for command-specific arguments.")
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        _print_help()
        return 0
    if args[0] in ("-V", "--version", "version"):
        print(__version__)
        return 0
    cmd, rest = args[0], args[1:]
    if cmd not in SUBCOMMANDS:
        print(f"ERROR: unknown command '{cmd}'. Run 'dual-loop --help' for available commands.", file=sys.stderr)
        return 2
    script_file, _ = SUBCOMMANDS[cmd]
    module = _load_script_module(script_file)
    old_argv = sys.argv
    try:
        sys.argv = [f"dual-loop {cmd}", *rest]
        try:
            rc = module.main(rest)
        except TypeError:
            rc = module.main()
        return int(rc or 0)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else (1 if exc.code else 0)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    sys.exit(main())
