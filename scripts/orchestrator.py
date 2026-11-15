#!/usr/bin/env python3
"""Automated outer-loop supervisor for a single task across budgeted rounds.

Coordinates contract freezing, role-isolated subagent dispatch (claude / codex /
custom command adapter), single-attempt execution sealing, cold independent
review with read-only retry on quota/interruption, rejection attribution
(code vs. contract vs. environment), recycle chaining, and trailer-stamped local
Git commits.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_CONTROLLER = SCRIPTS_DIR / "controller.py"
DEFAULT_CONTRACT = SCRIPTS_DIR / "contract.py"
ATTRIBUTION_RE = re.compile(r"ATTRIBUTION\s*:\s*(code|contract|environment)", re.IGNORECASE)
VERIFICATION_ONLY_RE = re.compile(r"^VERIFICATION_ONLY:\s*true\s*$", re.IGNORECASE | re.MULTILINE)


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


def run_cli(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    require(res.returncode == 0, f"git {' '.join(args[:2])} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def prepare_case_directory(
    case_root: Path,
    *,
    topic: str,
    question: str,
    criteria: list[dict[str, str]],
    round_num: int,
    reason: str,
    prior_case_root: Path | None,
    contract_script: Path,
) -> Path:
    require(not case_root.exists(), f"case directory already exists: {case_root}")
    (case_root / "inputs").mkdir(parents=True)
    (case_root / "outputs").mkdir(parents=True)

    previous_baseline_rel = None
    if prior_case_root is not None:
        prior_spec = load_json(prior_case_root / "spec.json")
        for prior_bl in sorted((prior_case_root / "inputs").glob("*-baseline-*.json")):
            shutil.copy2(prior_bl, case_root / "inputs" / prior_bl.name)
        previous_baseline_rel = prior_spec["baseline"]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    baseline_rel = f"inputs/{today}-baseline-r{round_num}.json"
    spec = {
        "schema_version": 1,
        "topic": topic,
        "question": question,
        "as_of": today,
        "expected": "inputs/expected.csv",
        "baseline": baseline_rel,
        "run_command": "dual-loop controller execute",
        "environment": "supervised git worktree",
        "sample_plan": f"{len(criteria)} acceptance criteria (round {round_num})",
        "stop_rule": "Single execute attempt per round; independent review decides accept/reject",
        "baseline_timing": "prospective",
        "scope": {
            "population": topic,
            "denominator_basis": "frozen contract criteria",
            "included": [topic],
            "excluded": ["unlisted paths and post-commit deployment"],
            "enumeration": "closed",
        },
        "revision": {
            "reason": reason,
            "previous_baseline": previous_baseline_rel,
        },
    }
    (case_root / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (case_root / "inputs" / "expected.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "requirement", "description", "basis", "acceptance"])
        writer.writeheader()
        for row in criteria:
            writer.writerow(
                {
                    "id": row["id"],
                    "requirement": row.get("requirement", "required"),
                    "description": row["description"],
                    "basis": row.get("basis", "Task specification"),
                    "acceptance": row["acceptance"],
                }
            )
    frozen = run_cli(contract_script, "freeze", "--root", str(case_root))
    require(frozen.returncode == 0, f"contract freeze failed: {frozen.stderr.strip() or frozen.stdout.strip()}")
    return case_root / baseline_rel


def render_executor_brief(
    task: dict,
    *,
    round_num: int,
    repo: Path,
    head: str,
    control_root: Path,
    case_root: Path,
    token_file: Path,
    controller_script: Path,
    prior_feedback: str | None,
) -> str:
    allowed = ", ".join(f"`{p}`" for p in task["allowed_paths"])
    protected = ", ".join(f"`{p}`" for p in task.get("protected_paths", [])) or "(see profile.json)"
    checks_lines = "\n".join(
        f"- `{c['id']}` (cwd `{c.get('cwd', '.')}`): `{' '.join(c['argv'])}`" for c in task["checks"]
    )
    criteria_lines = "\n".join(
        f"- `{r['id']}` [{r.get('requirement', 'required')}]: {r['description']} — Acceptance: {r['acceptance']}"
        for r in task["criteria"]
    )
    feedback_section = ""
    if prior_feedback:
        feedback_section = (
            f"\n## Prior Round Independent Review Feedback (Round {round_num - 1} Rejected)\n"
            f"Address the root cause below without relaxing any acceptance criterion:\n\n"
            f"```markdown\n{prior_feedback.strip()}\n```\n"
        )
    return f"""You are the **executor** (inner loop) for supervised task `{task['id']}` (Round {round_num} of {task.get('max_rounds', 3)}, Tier {task.get('tier', 'L2')}).
Implement the change, use targeted checks while debugging, invoke `execute` **at most once**, and stop.

## Hard Role Boundaries
- Repository worktree: `{repo}` (HEAD `{head}`).
- Writable paths ONLY: {allowed}. Modifying any other tracked/untracked source file fails the round.
- Protected paths (never touch): {protected}.
- Forbidden Git mutations: do NOT run `git commit`, `git reset`, `git checkout`, `git stash`, `git merge`, `git rebase`, or `git push`. Keep `HEAD` at `{head}`.
- Do NOT read or search for supervisor/reviewer tokens, and do NOT modify files under `{control_root}` or `{case_root}`.

## Task Objective: {task.get('title', task['id'])}
{task.get('brief', '')}
{feedback_section}
## Frozen Acceptance Criteria (`{case_root}/inputs/expected.csv`)
{criteria_lines}

## Frozen Checks (executed in order upon submission)
{checks_lines}

## Single Submission Command
Use targeted non-mutating checks during development. Do not pre-run the entire frozen suite just to repeat it at submission; `execute` runs that suite once. When ready, run **exactly once**:
```sh
python3 "{controller_script}" execute --root "{control_root}" --token "{token_file}"
```
Whether `execute` exits `0` or `1`, **stop immediately afterwards** and do NOT edit any source file after running `execute`.

## Final Note
Only report design choices, doubts, or uncovered edge cases that the receipt cannot supply. Changed paths, exit codes and check counts are already recorded; do not rewrite them in another report. Fix blocking feedback only; leave optional polish outside this candidate.
"""


def render_reviewer_brief(
    task: dict,
    *,
    round_num: int,
    repo: Path,
    head: str,
    control_root: Path,
    case_root: Path,
    baseline_file: Path,
    execution_summary: dict,
    token_file: Path,
    controller_script: Path,
    contract_script: Path,
    prior_feedback: str | None = None,
) -> str:
    baseline_sha = load_json(control_root / f"rounds/{round_num:03d}/baseline.json")["contract"][str(baseline_file)]
    criteria_lines = "\n".join(
        f"- `{r['id']}` [{r.get('requirement', 'required')}]: {r['description']} — Acceptance: {r['acceptance']}"
        for r in task["criteria"]
    )
    followup = ""
    if prior_feedback:
        followup = f"""\n## Previous Verdict
This is a new CLI reviewer process, not a resumed reviewer session. Focus on the previous blocking findings, the patch and related regressions; expand to cold review if scope, acceptance or critical assumptions changed. Do not turn optional polish into a repair round.
{prior_feedback.strip()}
"""
    return f"""You are the **independent reviewer** for task `{task['id']}` (Round {round_num}, Tier {task.get('tier', 'L2')}).
You did NOT write this candidate. Verify from source, sealed receipts and independent probes, not executor self-reports.

## Boundaries and Inputs
- Repository: `{repo}`; baseline HEAD `{head}`. Compare `git diff {head}` and `git status -sb` with allowed paths `{task['allowed_paths']}`.
- Do not edit or commit the candidate. Do not run execute/recycle or access other role keys.
- Counterexample/mutation tests run on a copy in `/tmp`, never inside the candidate.
- Run `python3 "{controller_script}" status --root "{control_root}"` to validate sealed receipts. Inspect logs in `{control_root}/rounds/{round_num:03d}/`.
- Execution receipt:
```json
{json.dumps({k: v for k, v in execution_summary.items() if k != 'source'}, ensure_ascii=False, indent=2)}
```

## Frozen Criteria
{criteria_lines}
{followup}
## Review Protocol
1. Check the complete diff and sealed evidence. Do not repeat the full frozen suite by default. Rerun specific checks for missing/invalid evidence, environment uncertainty, instability or a newly found risk; record why.
2. Independently verify relevant risks: real host clicks/viewports for UI; shared-part and archive invariants for data; actual outputs vs tool instructions for model-visible contracts. Probe important guards with meaningful counterexamples; do not meet an arbitrary mutation quota. Evidence must match this candidate.
3. Write evidence under `{case_root}/outputs/` and one `outputs/observations-r{round_num}.json`. The review object supplies the summary, unresolved code/product blockers and optional notes; comparison and feedback are generated from it:
```json
{{
  "baseline_sha256": "{baseline_sha}",
  "observed_at": "<ISO-8601 UTC timestamp>",
  "review": {{
    "summary": "<verdict rationale and any validation-tool defect>",
    "attribution": "code | contract | environment",
    "verification_only": false,
    "blockers": [],
    "non_blocking": []
  }},
  "rows": [
    {{
      "id": "<criterion-id>",
      "status": "PASS | FAIL | PARTIAL | SKIP",
      "detail": "<concise verification finding>",
      "evidence_level": "independent-review",
      "evidence": ["outputs/<evidence-file>"]
    }}
  ]
}}
```
4. Each blocker identifies the criterion, reproduction, impact and required recheck. Optional notes do not block acceptance. `verification_only: true` requires attribution=contract, unchanged business acceptance, no code/product blockers and an unchanged candidate. Put the probe/fixture error in summary; mixed failures are never verification-only. Check that a corrected probe still enforces the original requirement.
5. Submit your independent verdict in one command (accept requires execution passed and all required rows PASS; no PASS rows are invented):
```sh
python3 "{controller_script}" review --root "{control_root}" --token "{token_file}" --verdict <accept|reject> --observations "outputs/observations-r{round_num}.json"
```
The existing explicit --feedback/--report path remains supported.
"""


def invoke_agent_backend(
    backend: dict,
    *,
    role: str,
    brief_file: Path,
    log_file: Path,
    cwd: Path,
    placeholders: dict[str, str],
) -> int:
    kind = backend.get("kind", "claude")
    timeout = int(backend.get("timeout", 1800))
    extra_args = [str(x) for x in backend.get("extra_args", [])]
    model = backend.get("model")
    pass_stdin = True

    if kind == "claude":
        argv = ["claude", "-p", "--dangerously-skip-permissions"]
        if model:
            argv += ["--model", str(model)]
        argv += extra_args
    elif kind == "codex":
        argv = ["codex", "exec", "--full-auto"]
        if model:
            argv += ["-m", str(model)]
        argv += [*extra_args, "-"]
    elif kind == "command":
        raw_argv = backend.get("argv")
        require(isinstance(raw_argv, list) and bool(raw_argv), "command backend requires nonempty 'argv' list")
        argv = [str(item).format(**placeholders) for item in raw_argv]
        pass_stdin = bool(backend.get("pass_stdin", False))
    else:
        raise ValueError(f"unsupported backend kind: {kind}")

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("DUAL_LOOP_")
    }
    env.update(
        {
            "DUAL_LOOP_ROLE": role,
            "DUAL_LOOP_REPO": placeholders["repo"],
            "DUAL_LOOP_CONTROL_ROOT": placeholders["control_root"],
            "DUAL_LOOP_CASE_ROOT": placeholders["case_root"],
            "DUAL_LOOP_TOKEN_FILE": placeholders["token_file"],
            "DUAL_LOOP_ROUND": placeholders["round"],
            "DUAL_LOOP_BRIEF_FILE": str(brief_file),
            "DUAL_LOOP_CONTRACT_SCRIPT": placeholders["contract_script"],
            "DUAL_LOOP_CONTROLLER_SCRIPT": placeholders["controller_script"],
            "DUAL_LOOP_BASELINE_FILE": placeholders.get("baseline_file", ""),
        }
    )

    brief = brief_file.read_text(encoding="utf-8")
    brief_bytes = brief.encode("utf-8") if pass_stdin else None
    log_file.parent.mkdir(parents=True, exist_ok=True)
    original_log = log_file
    retry = 1
    while log_file.exists():
        retry += 1
        log_file = original_log.with_name(f"{original_log.stem}-resume{retry}{original_log.suffix}")
    call = {
        "role": role,
        "round": int(placeholders["round"]),
        "attempt": int(placeholders.get("attempt", "1")),
        "backend": kind,
        "started_at": now_iso(),
        "prompt_chars": len(brief),
        "returncode": None,
        "token_usage": None,
    }
    call_file = log_file.with_suffix(".call.json")
    save_json(call_file, call)
    started = time.monotonic()
    try:
        with log_file.open("xb") as out:
            proc = subprocess.run(
                argv, cwd=cwd, input=brief_bytes, stdout=out, stderr=subprocess.STDOUT,
                env=env, timeout=timeout, check=False,
            )
        call["returncode"] = proc.returncode
        return proc.returncode
    finally:
        call["finished_at"] = now_iso()
        call["duration_seconds"] = round(time.monotonic() - started, 3)
        save_json(call_file, call)


def finish_result(state_dir: Path, summary: dict) -> dict:
    calls = [load_json(path) for path in (state_dir / "agent-logs").glob("*.call.json")]
    kinds = {"development": 0, "verification": 0}
    checks = 0
    for directory in (state_dir / "control/rounds").glob("[0-9]*"):
        manifest = load_json(directory / "manifest.json")
        kinds[manifest.get("round_kind", "development")] += 1
        receipt = directory / "execution.json"
        if receipt.is_file():
            checks += len(load_json(receipt).get("checks", []))
    summary["metrics"] = {
        "agent_calls": {role: sum(c["role"] == role for c in calls) for role in ("executor", "reviewer")},
        "agent_duration_seconds": round(sum(c.get("duration_seconds", 0) for c in calls), 3),
        "prompt_chars": sum(c["prompt_chars"] for c in calls),
        "checks_executed": checks,
        "checks_reused": 0,
        "round_kinds": kinds,
        "token_usage": None,
    }
    save_json(state_dir / "orchestrator-result.json", summary)
    return summary


def controller_helpers():
    spec = importlib.util.spec_from_file_location("dual_loop_preflight_checks", DEFAULT_CONTROLLER)
    require(spec is not None and spec.loader is not None, "cannot load check runner")
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    return controller


def copy_case_inputs(case_root: Path, inputs: dict) -> None:
    """Copy staged probe/fixture revisions into a new case without overwrites."""
    require(isinstance(inputs, dict), "case_inputs must map case-relative inputs to absolute source files")
    if not inputs:
        return
    controller = controller_helpers()
    copies = []
    destinations = set()
    for relative, source in inputs.items():
        require(isinstance(relative, str) and bool(relative), "case input destination must be a string")
        parts = Path(relative).parts
        require(len(parts) >= 2 and parts[0] == "inputs", "case input destination must be under inputs/")
        target = controller.contained(case_root, relative)
        require(not target.exists() and target not in destinations, "case input cannot overwrite existing/generated files")
        require(isinstance(source, str) and bool(source), "case input source must be an absolute file")
        controller.check_no_user_symlink_ancestors(Path(source), "case input source")
        original = controller.absolute_regular(source)
        copies.append((original, target))
        destinations.add(target)
    for original, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        with original.open("rb") as src, target.open("xb") as dest:
            shutil.copyfileobj(src, dest)
        shutil.copymode(original, target, follow_symlinks=False)


def run_probe_preflight(task: dict, repo: Path, state_dir: Path) -> bool:
    """Check existing staged probes/fixtures before case creation or round issue."""
    checks = task.get("probe_preflight_checks", [])
    require(isinstance(checks, list), "probe_preflight_checks must be a list")
    if not checks:
        return True
    controller = controller_helpers()
    original = controller.snapshot(repo)
    head = git(repo, "rev-parse", "HEAD")
    results = []
    log_root = state_dir / "probe-preflight"
    log_root.mkdir(exist_ok=True)
    attempt = 1
    while (log_root / f"attempt-{attempt:03d}").exists():
        attempt += 1
    log_dir = log_root / f"attempt-{attempt:03d}"
    log_dir.mkdir()
    for index, check in enumerate(checks):
        require(isinstance(check, dict), "probe preflight check must be an object")
        require(isinstance(check.get("id"), str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", check["id"]), "invalid probe preflight id")
        require(isinstance(check.get("argv"), list) and bool(check["argv"]) and all(isinstance(x, str) and x for x in check["argv"]), "probe preflight argv must be a nonempty string array")
        require(isinstance(check.get("timeout", 300), int) and 1 <= check.get("timeout", 300) <= 3600, "invalid probe preflight timeout")
        cwd = controller.contained(repo, check.get("cwd", "."), directory=True)
        with (log_dir / f"{index:03d}-{check['id']}.log").open("wb") as log:
            outcome = controller.run_check(check, cwd, log)
        intact = controller.snapshot(repo) == original and git(repo, "rev-parse", "HEAD") == head
        results.append({"id": check["id"], **outcome, "source_unchanged": intact})
        save_json(log_dir / "result.json", {"checks": results})
        if outcome["exit_code"] != 0 or not intact:
            return False
    return True


def issue_scoped_worker_token(authority_dir: Path, worker_tokens_dir: Path, role: str, label: str) -> Path:
    worker_tokens_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(worker_tokens_dir, 0o700)
    target = worker_tokens_dir / f"{label}-{role}.key"
    if target.exists():
        target.unlink()
    secret = (authority_dir / f"{role}.key").read_text(encoding="utf-8")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret)
    return target


def _matches_scope(path: str, patterns: list[str]) -> bool:
    return any(path == p.rstrip("/") or (p.endswith("/") and path.startswith(p)) for p in patterns)


def _worktree_dirty_paths(repo: Path) -> list[str]:
    tracked = {
        os.fsdecode(x)
        for x in subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", "HEAD", "-z"],
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        if x
    }
    untracked = {
        os.fsdecode(x)
        for x in subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        if x
    }
    return sorted(tracked | untracked)


def archive_and_revert_unauthorized_changes(
    repo: Path,
    *,
    profile_path: Path,
    allowed_paths: list[str],
    protected_paths: list[str],
    archive_dir: Path,
) -> list[str]:
    profile_data = load_json(profile_path)
    protected_all = [*profile_data.get("protected_paths", []), *protected_paths]
    dirty = _worktree_dirty_paths(repo)
    unauthorized = [
        p for p in dirty if _matches_scope(p, protected_all) or not _matches_scope(p, allowed_paths)
    ]
    if not unauthorized:
        return []

    archive_dir.mkdir(parents=True, exist_ok=True)
    save_json(
        archive_dir / "reverted.json",
        {"reverted_at": now_iso(), "paths": unauthorized},
    )
    diff_proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "HEAD", "--binary", "--", *unauthorized],
        capture_output=True,
        check=False,
    )
    (archive_dir / "unauthorized.patch").write_bytes(diff_proc.stdout)
    for rel in unauthorized:
        src = repo / rel
        dst = archive_dir / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            os.symlink(os.readlink(src), dst)
        elif src.is_file():
            shutil.copy2(src, dst)

    for rel in unauthorized:
        in_head = (
            subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{rel}"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        if in_head:
            git(repo, "checkout", "HEAD", "--", rel)
        else:
            subprocess.run(
                ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel],
                capture_output=True,
                check=False,
            )
            target = repo / rel
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            parent = target.parent
            while parent != repo and repo in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    return unauthorized


def parse_rejection_attribution(feedback_path: Path) -> str:
    if not feedback_path.is_file():
        return "code"
    text = feedback_path.read_text(encoding="utf-8", errors="replace")
    match = ATTRIBUTION_RE.search(text)
    return match.group(1).lower() if match else "code"


def commit_accepted_round(repo: Path, task: dict, changed_paths: list[str], round_num: int, review_hash: str) -> str | None:
    if not changed_paths:
        status = git(repo, "status", "--porcelain")
        if not status:
            return git(repo, "rev-parse", "HEAD")
    for rel in changed_paths:
        full = repo / rel
        if full.exists() or full.is_symlink():
            git(repo, "add", "--", rel)
        else:
            git(repo, "rm", "--cached", "--ignore-unmatch", "--", rel)
    staged = git(repo, "diff", "--cached", "--name-only")
    if not staged:
        return git(repo, "rev-parse", "HEAD")
    title = task.get("commit_message") or task.get("title") or f"Complete {task['id']}"
    trailer = f"Accepted in supervised round {task['id']}-r{round_num} (review {review_hash[:8]})"
    message = f"{title}\n\n{trailer}\n"
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def orchestrate_task(
    task_orig: dict,
    *,
    state_dir: Path,
    controller_script: Path = DEFAULT_CONTROLLER,
    default_contract_script: Path = DEFAULT_CONTRACT,
) -> dict[str, object]:
    task = json.loads(json.dumps(task_orig))
    task_id = task["id"]
    topic = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-") or "supervised-task"
    repo = Path(task["repo"]).resolve()
    state_dir = state_dir.resolve()
    require(state_dir != repo and repo not in state_dir.parents, "state_dir must be outside executor repo")
    state_dir.mkdir(parents=True, exist_ok=True)

    control_root = state_dir / "control"
    authority_dir = state_dir / "authority"
    cases_dir = state_dir / "cases"
    briefs_dir = state_dir / "briefs"
    logs_dir = state_dir / "agent-logs"
    manifests_dir = state_dir / "manifests"
    worker_tokens_dir = state_dir / "worker-tokens"

    contract_script = Path(task["contract_script"]).resolve() if task.get("contract_script") else default_contract_script
    max_rounds = int(task.get("max_rounds", 3))
    max_reviewer_retries = int(task.get("max_reviewer_retries", 2))
    auto_submit = bool(task.get("auto_submit_unexecuted", True))
    commit_on_accept = bool(task.get("commit_on_accept", True))

    if not (control_root / "state.json").is_file():
        init_res = run_cli(controller_script, "init", "--root", str(control_root), "--authority", str(authority_dir))
        require(init_res.returncode == 0, f"controller init failed: {init_res.stderr.strip()}")

    ctrl_state = load_json(control_root / "state.json")
    if ctrl_state["rounds"] and (state_dir / "active-task.json").is_file():
        task = load_json(state_dir / "active-task.json")
        require(task["id"] == task_id and Path(task["repo"]).resolve() == repo, "saved task identity differs")
        # Keep frozen/repaired task inputs on resume, but allow an operator to
        # supply a missing repair command or replace an unavailable backend.
        for key in ("executor_backend", "reviewer_backend", "contract_repair_command"):
            if key in task_orig:
                task[key] = task_orig[key]
    if not ctrl_state["rounds"] and not run_probe_preflight(task, repo, state_dir):
        return finish_result(state_dir, {
            "task_id": task_id, "status": "blocked", "blocked_reason": "probe_preflight_failed",
            "rounds_used": 0, "finished_at": now_iso(),
        })
    prior_case_root: Path | None = None
    prior_feedback: str | None = None
    if ctrl_state["rounds"]:
        last_round = ctrl_state["rounds"][-1]
        prior_case_root = Path(last_round["case_root"])
        fb = control_root / last_round["directory"] / "feedback.md"
        if fb.is_file():
            prior_feedback = fb.read_text(encoding="utf-8", errors="replace")

    while True:
        ctrl_state = load_json(control_root / "state.json")
        phase = ctrl_state["phase"]
        current_round_count = len(ctrl_state["rounds"])

        if phase == "accepted":
            review_hash = ctrl_state["rounds"][-1]["review_hash"]
            round_dir_acc = control_root / ctrl_state["rounds"][-1]["directory"]
            exec_data = load_json(round_dir_acc / "execution.json")
            manifest_acc = load_json(round_dir_acc / "manifest.json")
            commit_sha = None
            if commit_on_accept:
                all_candidates = sorted(
                    set(exec_data.get("changed_paths", []))
                    | {
                        p
                        for p in exec_data.get("head_changed_paths", [])
                        if any(
                            p == a.rstrip("/") or (a.endswith("/") and p.startswith(a))
                            for a in manifest_acc.get("allowed_paths", [])
                        )
                    }
                )
                commit_sha = commit_accepted_round(
                    repo, task, all_candidates, current_round_count, review_hash
                )
            summary = {
                "task_id": task_id,
                "status": "accepted",
                "rounds_used": current_round_count,
                "max_rounds": max_rounds,
                "review_hash": review_hash,
                "commit": commit_sha,
                "finished_at": now_iso(),
            }
            return finish_result(state_dir, summary)

        if phase in ("idle", "rejected"):
            verification_manifest = {}
            next_round = current_round_count + 1
            case_root = cases_dir / f"round-{next_round:03d}"
            if next_round > max_rounds:
                summary = {
                    "task_id": task_id,
                    "status": "budget_exhausted",
                    "rounds_used": current_round_count,
                    "max_rounds": max_rounds,
                    "review_hash": ctrl_state["rounds"][-1].get("review_hash") if ctrl_state["rounds"] else None,
                    "finished_at": now_iso(),
                }
                return finish_result(state_dir, summary)

            if phase == "rejected":
                last_dir = control_root / ctrl_state["rounds"][-1]["directory"]
                attribution = parse_rejection_attribution(last_dir / "feedback.md")
                if attribution == "environment":
                    summary = {
                        "task_id": task_id,
                        "status": "blocked",
                        "blocked_reason": "environment_drift",
                        "rounds_used": current_round_count,
                        "review_hash": ctrl_state["rounds"][-1]["review_hash"],
                        "finished_at": now_iso(),
                    }
                    return finish_result(state_dir, summary)
                if attribution == "contract":
                    repair_cmd = task.get("contract_repair_command")
                    if not repair_cmd:
                        summary = {
                            "task_id": task_id,
                            "status": "blocked",
                            "blocked_reason": "contract_revision_required",
                            "rounds_used": current_round_count,
                            "review_hash": ctrl_state["rounds"][-1]["review_hash"],
                            "finished_at": now_iso(),
                        }
                        return finish_result(state_dir, summary)
                    feedback = (last_dir / "feedback.md").read_text(encoding="utf-8")
                    verification_only = bool(VERIFICATION_ONLY_RE.search(feedback))
                    controller = controller_helpers()
                    before_repair = controller.snapshot(repo)
                    before_head = git(repo, "rev-parse", "HEAD")
                    patch_file = state_dir / f"contract-patch-r{next_round}.json"
                    rc = subprocess.run(
                        [
                            str(x).format(
                                task_config=str(state_dir / "active-task.json"),
                                feedback_file=str(last_dir / "feedback.md"),
                                out_patch=str(patch_file),
                                round=str(next_round),
                                case_root=str(case_root),
                            )
                            for x in repair_cmd
                        ],
                        check=False,
                    ).returncode
                    require(rc == 0 and patch_file.is_file(), "contract_repair_command failed to produce patch JSON")
                    require(
                        controller.snapshot(repo) == before_repair and git(repo, "rev-parse", "HEAD") == before_head,
                        "contract repair changed the candidate or HEAD; preserve it for inspection",
                    )
                    patch_data = load_json(patch_file)
                    for k in ("allowed_paths", "protected_paths", "checks", "criteria", "policy_files", "case_inputs", "probe_preflight_checks"):
                        if k in patch_data:
                            task[k] = patch_data[k]
                    if not run_probe_preflight(task, repo, state_dir):
                        save_json(state_dir / "active-task.json", task)
                        return finish_result(state_dir, {
                            "task_id": task_id, "status": "blocked", "blocked_reason": "probe_preflight_failed",
                            "rounds_used": current_round_count, "review_hash": ctrl_state["rounds"][-1]["review_hash"],
                            "finished_at": now_iso(),
                        })
                    if verification_only:
                        parent_candidate = last_dir / "candidate.json"
                        require(parent_candidate.is_file(), "verification repair needs a sealed parent candidate")
                        verification_manifest = {
                            "round_kind": "verification",
                            "parent_round": current_round_count,
                            "expected_candidate_sha256": hashlib.sha256(parent_candidate.read_bytes()).hexdigest(),
                        }

                if not verification_manifest:
                    archive_and_revert_unauthorized_changes(
                        repo,
                        profile_path=Path(task["profile"]).resolve(),
                        allowed_paths=task["allowed_paths"],
                        protected_paths=task.get("protected_paths", []),
                        archive_dir=state_dir / "reverted-out-of-scope" / f"before-r{next_round:03d}",
                    )

            save_json(state_dir / "active-task.json", task)
            reason = (
                "Initial supervised contract"
                if phase == "idle"
                else f"Recycle round {next_round} from rejecting review {ctrl_state['rounds'][-1]['review_hash'][:12]}"
            )
            prepare_case_directory(
                case_root,
                topic=topic,
                question=task.get("title", task_id),
                criteria=task["criteria"],
                round_num=next_round,
                reason=reason,
                prior_case_root=prior_case_root,
                contract_script=contract_script,
            )
            copy_case_inputs(case_root, task.get("case_inputs", {}))
            manifest = {
                "repo": str(repo),
                "case_root": str(case_root),
                "contract_script": str(contract_script),
                "profile": str(Path(task["profile"]).resolve()),
                "policy_files": [str(Path(p).resolve()) for p in task["policy_files"]],
                "allowed_paths": task["allowed_paths"],
                "protected_paths": task.get("protected_paths", []),
                "checks": task["checks"],
                "max_rounds": max_rounds,
                **verification_manifest,
            }
            if "require_clean_worktree" in task:
                manifest["require_clean_worktree"] = bool(task["require_clean_worktree"])
            manifest_path = manifests_dir / f"manifest-{next_round:03d}.json"
            save_json(manifest_path, manifest)

            sup_key = authority_dir / "supervisor.key"
            if phase == "idle":
                iss = run_cli(
                    controller_script,
                    "issue",
                    "--root",
                    str(control_root),
                    "--token",
                    str(sup_key),
                    "--manifest",
                    str(manifest_path),
                )
            else:
                prev_hash = ctrl_state["rounds"][-1]["review_hash"]
                iss = run_cli(
                    controller_script,
                    "recycle",
                    "--root",
                    str(control_root),
                    "--token",
                    str(sup_key),
                    "--manifest",
                    str(manifest_path),
                    "--from-review",
                    prev_hash,
                    "--change-reason",
                    reason,
                )
            require(iss.returncode == 0, f"issue/recycle failed: {iss.stderr.strip() or iss.stdout.strip()}")
            prior_case_root = case_root
            ctrl_state = load_json(control_root / "state.json")
            phase = ctrl_state["phase"]
            current_round_count = len(ctrl_state["rounds"])

        round_info = ctrl_state["rounds"][-1]
        round_num = round_info["number"]
        round_dir = control_root / round_info["directory"]
        case_root = Path(round_info["case_root"])
        baseline_data = load_json(round_dir / "baseline.json")
        head = baseline_data["head"]
        spec_data = load_json(case_root / "spec.json")
        baseline_file = case_root / spec_data["baseline"]

        if phase == "issued":
            exec_token = issue_scoped_worker_token(authority_dir, worker_tokens_dir, "executor", f"r{round_num:03d}")
            try:
                if load_json(round_dir / "manifest.json").get("round_kind") == "verification":
                    submitted = run_cli(
                        controller_script, "execute", "--root", str(control_root), "--token", str(exec_token),
                    )
                    require(submitted.returncode in (0, 1), f"verification execute failed: {submitted.stderr.strip()}")
                else:
                    brief_text = render_executor_brief(
                        task,
                        round_num=round_num,
                        repo=repo,
                        head=head,
                        control_root=control_root,
                        case_root=case_root,
                        token_file=exec_token,
                        controller_script=controller_script,
                        prior_feedback=prior_feedback,
                    )
                    brief_file = briefs_dir / f"round-{round_num:03d}-executor.md"
                    brief_file.parent.mkdir(parents=True, exist_ok=True)
                    brief_file.write_text(brief_text, encoding="utf-8")
                    placeholders = {
                        "role": "executor",
                        "repo": str(repo),
                        "control_root": str(control_root),
                        "case_root": str(case_root),
                        "token_file": str(exec_token),
                        "round": str(round_num),
                        "brief_file": str(brief_file),
                        "contract_script": str(contract_script),
                        "controller_script": str(controller_script),
                        "baseline_file": str(baseline_file),
                    }
                    invoke_agent_backend(
                        task.get("executor_backend", {"kind": "claude"}),
                        role="executor",
                        brief_file=brief_file,
                        log_file=logs_dir / f"round-{round_num:03d}-executor.log",
                        cwd=repo,
                        placeholders=placeholders,
                    )
                    post_state = load_json(control_root / "state.json")
                    if post_state["phase"] == "issued":
                        if auto_submit:
                            run_cli(
                                controller_script,
                                "execute",
                                "--root",
                                str(control_root),
                                "--token",
                                str(exec_token),
                            )
                        else:
                            summary = {
                                "task_id": task_id,
                                "status": "blocked",
                                "blocked_reason": "executor_did_not_submit",
                                "rounds_used": round_num,
                                "finished_at": now_iso(),
                            }
                            return finish_result(state_dir, summary)
            finally:
                if exec_token.exists():
                    exec_token.unlink()

        ctrl_state = load_json(control_root / "state.json")
        phase = ctrl_state["phase"]
        if phase in ("running", "awaiting_review"):
            exec_json_path = round_dir / "execution.json"
            exec_summary = load_json(exec_json_path) if exec_json_path.is_file() else {"phase": phase}
            reviewed = False
            for attempt in range(1, max_reviewer_retries + 2):
                rev_token = issue_scoped_worker_token(
                    authority_dir, worker_tokens_dir, "reviewer", f"r{round_num:03d}-a{attempt}"
                )
                try:
                    rev_brief = render_reviewer_brief(
                        task,
                        round_num=round_num,
                        repo=repo,
                        head=head,
                        control_root=control_root,
                        case_root=case_root,
                        baseline_file=baseline_file,
                        execution_summary=exec_summary,
                        token_file=rev_token,
                        controller_script=controller_script,
                        contract_script=contract_script,
                        prior_feedback=prior_feedback,
                    )
                    rev_brief_file = briefs_dir / f"round-{round_num:03d}-reviewer-a{attempt}.md"
                    rev_brief_file.parent.mkdir(parents=True, exist_ok=True)
                    rev_brief_file.write_text(rev_brief, encoding="utf-8")
                    placeholders = {
                        "role": "reviewer",
                        "attempt": str(attempt),
                        "repo": str(repo),
                        "control_root": str(control_root),
                        "case_root": str(case_root),
                        "token_file": str(rev_token),
                        "round": str(round_num),
                        "brief_file": str(rev_brief_file),
                        "contract_script": str(contract_script),
                        "controller_script": str(controller_script),
                        "baseline_file": str(baseline_file),
                    }
                    invoke_agent_backend(
                        task.get("reviewer_backend", task.get("executor_backend", {"kind": "claude"})),
                        role="reviewer",
                        brief_file=rev_brief_file,
                        log_file=logs_dir / f"round-{round_num:03d}-reviewer-a{attempt}.log",
                        cwd=repo,
                        placeholders=placeholders,
                    )
                finally:
                    if rev_token.exists():
                        rev_token.unlink()

                after_rev = load_json(control_root / "state.json")
                if after_rev["phase"] in ("accepted", "rejected"):
                    reviewed = True
                    fb_path = round_dir / "feedback.md"
                    prior_feedback = fb_path.read_text(encoding="utf-8", errors="replace") if fb_path.is_file() else ""
                    break

            if not reviewed:
                summary = {
                    "task_id": task_id,
                    "status": "blocked",
                    "blocked_reason": "reviewer_did_not_verdict",
                    "rounds_used": round_num,
                    "finished_at": now_iso(),
                }
                return finish_result(state_dir, summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-config", required=True, type=Path, help="Task specification JSON file")
    parser.add_argument("--state-dir", required=True, type=Path, help="External directory for control, keys, cases, and logs")
    args = parser.parse_args(argv)

    task = load_json(args.task_config.resolve())
    summary = orchestrate_task(task, state_dir=args.state_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "accepted" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
