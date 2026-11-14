# CLAUDE.md

Read [`AGENTS.md`](AGENTS.md) first, then [`SKILL.md`](SKILL.md).

- **If asked to apply `dual-loop-dev` to another codebase**: Follow Sections 1–5 of [`AGENTS.md`](AGENTS.md) (clone outside the target repo, run `preflight.py`, isolate in a Git worktree via `worktree.py`, freeze contracts via `contract.py`, enforce single-attempt `execute` and fresh-context cold `review` via `controller.py` or `orchestrator.py` / `roadmap.py`, and integrate commits with `Accepted in supervised round <case> (review <hash8>)` trailers).
- **If modifying `dual-loop-dev` itself**: Keep pure Python 3.10+ stdlib + Git (zero third-party dependencies) and run both `env -u PYTHONPYCACHEPREFIX python3 -m unittest discover -s tests -p "test_*.py" -v` and `python3 examples/minimal-project/run_demo.py`.
