#!/usr/bin/env python3
"""Single-attempt development rounds, externally reviewed and explicitly recycled.

This controller enforces role separation (supervisor / executor / reviewer),
one-attempt execution with immutable candidate sealing, POSIX process-group check
isolation, hash-chained audit ledgers, and self-contained offline-replayable
round archives.

Security boundary: this is a trusted-local workflow guard against accidental role
confusion and silent self-repair, not an OS sandbox against a hostile process
running under the same UID. For untrusted workers, mount control and authority
storage outside the worker container.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


DEFAULT_CONTRACT_SCRIPT = Path(__file__).with_name("contract.py").resolve()


class Rejected(ValueError):
    """A workflow precondition was not satisfied."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Rejected(message)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path: Path | str, value: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def contained(root: Path, value: str, *, directory: bool = False) -> Path:
    """Validate a repo/case-relative path without following a symlink component."""
    require(isinstance(value, str) and bool(value), "path must be nonempty")
    raw = Path(value)
    require(not raw.is_absolute() and ".." not in raw.parts, f"path escapes root: {value}")
    require(not any(p in {".git", "node_modules"} for p in raw.parts), f"reserved path: {value}")
    root_resolved = Path(root).resolve()
    current = root_resolved
    for part in raw.parts:
        current /= part
        require(not current.is_symlink(), f"symlink path is forbidden: {value}")
    resolved = current.resolve()
    require(resolved == root_resolved or root_resolved in resolved.parents, f"path escapes root: {value}")
    if not directory:
        require(resolved != root_resolved, "file path cannot be repository root")
    return resolved


def absolute_regular(value: str) -> Path:
    p = Path(value)
    require(p.is_absolute() and p.is_file(), f"expected absolute existing file: {value}")
    require(not p.is_symlink(), f"symlink file is forbidden: {value}")
    return p.resolve()


def check_no_user_symlink_ancestors(path: Path, label: str = "path") -> None:
    """Forbid symlinks in user-created paths while tolerating OS root aliases like macOS /var -> /private/var."""
    require(not path.is_symlink(), f"{label} cannot be a symlink")
    system_aliases = {Path("/var"), Path("/tmp"), Path("/etc")}
    for parent in path.parents:
        if parent in system_aliases:
            continue
        require(not parent.is_symlink(), f"{label} cannot traverse symlink: {parent}")


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    require(
        result.returncode == 0,
        f"git {' '.join(args[:2])} failed: {result.stderr.decode(errors='replace').strip()}",
    )
    return result.stdout


def snapshot(repo: Path, submodules: bool = True) -> dict[str, dict]:
    """Hash source files only; Git's exclusion machinery avoids ignored build outputs."""
    indexed: dict[str, tuple[str, str]] = {}
    for entry in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if entry:
            info, name = entry.split(b"\t", 1)
            mode, oid, stage = info.decode().split()
            require(stage == "0", "unresolved Git index conflicts")
            indexed[os.fsdecode(name)] = (mode, oid)
    names = {
        os.fsdecode(x)
        for x in git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
        if x
    }
    result: dict[str, dict] = {}
    for name in sorted(names):
        path = repo / name
        mode, oid = indexed.get(name, (None, None))
        if mode == "160000":
            rec: dict[str, object] = {"kind": "gitlink", "index": oid}
            if submodules and (path / ".git").exists():
                rec.update(
                    head=git(path, "rev-parse", "HEAD").decode().strip(),
                    tree=digest(snapshot(path, False)),
                    status=git(path, "status", "--porcelain").decode(),
                )
            else:
                rec["initialized"] = False
            result[name] = rec
        elif path.is_symlink():
            result[name] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            result[name] = {"kind": "file", "sha256": file_hash(path), "mode": stat.S_IMODE(path.stat().st_mode)}
        elif not path.exists():
            result[name] = {"kind": "missing"}
        else:
            raise Rejected(f"unsupported source entry: {name}")
    return result


def head_changed_paths(repo: Path) -> list[str]:
    """List all tracked modifications and non-ignored untracked files relative to Git HEAD."""
    tracked = {os.fsdecode(x) for x in git(repo, "diff", "--name-only", "HEAD", "-z").split(b"\0") if x}
    untracked = {
        os.fsdecode(x)
        for x in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if x
    }
    return sorted(tracked | untracked)


def matches(path: str, patterns: list[str]) -> bool:
    return any(path == p.rstrip("/") or (p.endswith("/") and path.startswith(p)) for p in patterns)


def changes(before: dict, after: dict) -> list[str]:
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def resolve_contract_script(manifest: dict) -> Path:
    raw = manifest.get("contract_script") or manifest.get("taskkit_script")
    if raw is None:
        return absolute_regular(str(DEFAULT_CONTRACT_SCRIPT))
    return absolute_regular(raw)


def archived_contract_script(directory: Path) -> Path:
    for candidate_name in ("contract-engine.py", "taskkit-contract.py"):
        p = directory / candidate_name
        if p.is_file():
            return p
    raise Rejected(f"archived contract script missing in {directory}")


def invoke_contract(script: Path | str, action: str, case: Path | str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), action, "--root", str(case), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def run_check(check: dict, cwd: Path, log) -> dict[str, object]:
    """Keep each check and its ordinary descendants in one disposable process group."""
    process = None
    old_handlers = {}
    cancellation: dict[str, object] = {}
    cleanup_errors: list[dict[str, object]] = []
    stage = "creating"
    outcome: dict[str, object] = {"exit_code": 127}

    def cancel(signum: int, _frame) -> None:
        if not cancellation:
            cancellation.update(signal=signum, stage=stage)

    def cleanup_error(operation: str, exc: BaseException) -> None:
        cleanup_errors.append({"operation": operation, "error": str(exc), "errno": getattr(exc, "errno", None)})

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, cancel)
        deadline = time.monotonic() + check.get("timeout", 300)
        process = subprocess.Popen(
            check["argv"],
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
        )
        stage = "waiting"
        while not cancellation:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome["exit_code"] = 124
                log.write(b"\nController: check timeout; attempt consumed.\n")
                break
            try:
                outcome["exit_code"] = process.wait(timeout=min(remaining, 0.05))
                break
            except subprocess.TimeoutExpired:
                continue
    except OSError as exc:
        outcome["error"] = str(exc)
        log.write((f"Controller: {exc}\n").encode("utf-8"))
    finally:
        stage = "cleanup"
        if process is not None:
            for signum in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, signum)
                except ProcessLookupError:
                    break
                except OSError as exc:
                    cleanup_error("killpg:" + signal.Signals(signum).name, exc)
                if signum == signal.SIGTERM:
                    time.sleep(0.1)
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired) as exc:
                cleanup_error("reap", exc)
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired) as final_exc:
                    cleanup_error("kill-and-reap-parent", final_exc)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)

    if cancellation:
        sig = int(cancellation["signal"])
        outcome.update(
            exit_code=128 + sig,
            cancelled=True,
            cancel_signal=sig,
            cancel_stage=cancellation["stage"],
        )
        log.write(
            (f"\nController: check cancelled by signal {sig} during {cancellation['stage']}; attempt consumed.\n").encode(
                "utf-8"
            )
        )
    if cleanup_errors:
        outcome["cleanup_errors"] = cleanup_errors
        if outcome["exit_code"] == 0:
            outcome["exit_code"] = 125
        log.write(("Controller: process cleanup failed: " + json.dumps(cleanup_errors) + "\n").encode("utf-8"))
    return outcome


@contextmanager
def locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_state(root: Path) -> tuple[dict, str, int]:
    state = load(root / "state.json")
    previous = None
    last = None
    with (root / "events.jsonl").open(encoding="utf-8") as events:
        for index, line in enumerate(events, 1):
            event = json.loads(line)
            sealed = {k: v for k, v in event.items() if k != "hash"}
            require(
                event["seq"] == index and event["previous"] == previous and digest(sealed) == event["hash"],
                "audit event chain changed",
            )
            previous = event["hash"]
            last = event
    require(last is not None and last["state"] == state, "state does not match audit ledger")
    for round_ in state["rounds"]:
        for rel, expected in round_["artifacts"].items():
            require(file_hash(contained(root, rel)) == expected, f"round artifact changed: {rel}")
        directory = root / round_["directory"]
        archived_script = archived_contract_script(directory)
        archived_case = directory / "contract"
        checked = invoke_contract(archived_script, "check", archived_case)
        require(checked.returncode == 0, "archived contract failed verification")
        if (directory / "review.json").is_file():
            report = load(directory / "review.json").get("report")
            if report:
                replayed = invoke_contract(archived_script, "replay", archived_case, "--report", report)
                require(replayed.returncode in (0, 1), "archived review evidence failed replay")
    return state, str(previous), int(last["seq"])


def record(root: Path, state: dict, actor: str, action: str, previous: str | None = None, seq: int = 0) -> None:
    event = {
        "seq": seq + 1,
        "previous": previous,
        "at": now(),
        "actor": actor,
        "action": action,
        "state": state,
    }
    event["hash"] = digest(event)
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp = root / "state.json.tmp"
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, root / "state.json")


def authenticate(state: dict, token_file: Path | None, role: str) -> None:
    require(token_file is not None, f"{role} token is required")
    token = Path(token_file).read_text(encoding="utf-8").strip()
    require(
        secrets.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), state["roles"][role]),
        f"not authorized as {role}",
    )


def remember(root: Path, round_: dict, path: Path) -> None:
    round_["artifacts"][str(path.relative_to(root))] = file_hash(path)


def prepare_round(root: Path, state: dict, manifest_file: Path, reason: str) -> None:
    manifest_file = absolute_regular(str(Path(manifest_file).absolute()))
    manifest = load(manifest_file)
    repo = Path(manifest["repo"])
    require(repo.is_absolute() and repo.is_dir() and not repo.is_symlink(), "repo must be an absolute real directory")
    repo = repo.resolve()
    require(
        Path(git(repo, "rev-parse", "--show-toplevel").decode().strip()).resolve() == repo,
        "repo must be a Git worktree root",
    )
    case = Path(manifest["case_root"])
    require(case.is_absolute() and case.is_dir() and not case.is_symlink(), "case_root must be an absolute real directory")
    case = case.resolve()
    for owned in (root.resolve(), Path(state["authority"]).resolve(), case):
        require(
            owned != repo and repo not in owned.parents,
            "control, authority and contract case directories must be outside executor repo",
        )
    require(str(case) not in [r["case_root"] for r in state["rounds"]], "recycle needs a new independent case root")
    script = resolve_contract_script(manifest)
    require(isinstance(manifest.get("max_rounds"), int) and 1 <= manifest["max_rounds"] <= 20, "max_rounds must be 1..20")
    budget = state.get("max_rounds") or manifest["max_rounds"]
    require(manifest["max_rounds"] <= budget, "round budget cannot be raised")
    budget = min(budget, manifest["max_rounds"])
    require(len(state["rounds"]) < budget, "round budget exhausted")

    for key in ("allowed_paths", "protected_paths"):
        require(isinstance(manifest.get(key), list), f"{key} must be a list")
        for item in manifest[key]:
            require(isinstance(item, str) and bool(item), f"{key} entries must be nonempty strings")
            contained(repo, item, directory=item.endswith("/"))
    require(bool(manifest["allowed_paths"]), "allowed_paths cannot be empty")
    require(isinstance(manifest.get("policy_files"), list) and bool(manifest["policy_files"]), "policy_files cannot be empty")
    require(isinstance(manifest.get("profile"), str) and bool(manifest["profile"]), "manifest profile must be an absolute path")

    profile_file = absolute_regular(manifest["profile"])
    profile = load(profile_file)
    require(isinstance(profile, dict), "profile must be a JSON object")
    require(profile.get("schema_version") == 1, "profile schema_version must be 1")
    require(isinstance(profile.get("name"), str) and bool(profile["name"].strip()), "profile name is required")
    profile_protected = profile.get("protected_paths")
    require(isinstance(profile_protected, list), "profile protected_paths must be a list")
    for item in profile_protected:
        require(isinstance(item, str) and bool(item), "profile protected_paths entries must be nonempty strings")
        contained(repo, item, directory=item.endswith("/"))

    initial_dirty = head_changed_paths(repo)
    if manifest.get("require_clean_worktree") or profile.get("require_clean_worktree"):
        if not state["rounds"]:
            require(not initial_dirty, "worktree has uncommitted changes at issue: " + ", ".join(initial_dirty))
        else:
            protected_all = [*profile_protected, *manifest["protected_paths"]]
            bad_carryover = [
                p for p in initial_dirty if matches(p, protected_all) or not matches(p, manifest["allowed_paths"])
            ]
            require(
                not bad_carryover,
                "recycle worktree has unauthorized uncommitted changes: " + ", ".join(bad_carryover),
            )

    policies: dict[str, str] = {}
    for source in manifest["policy_files"]:
        resolved = str(absolute_regular(source))
        policies[resolved] = file_hash(resolved)
    policies[str(profile_file)] = file_hash(profile_file)

    checks = manifest.get("checks")
    require(isinstance(checks, list) and bool(checks), "checks cannot be empty")
    ids: set[str] = set()
    for check in checks:
        require(
            isinstance(check.get("id"), str) and bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", check["id"])),
            "invalid check id",
        )
        require(check["id"] not in ids, "duplicate check id")
        ids.add(check["id"])
        require(
            isinstance(check.get("argv"), list)
            and bool(check["argv"])
            and all(isinstance(v, str) and bool(v) for v in check["argv"]),
            "check argv must be a nonempty string array",
        )
        cwd = contained(repo, check.get("cwd", "."), directory=True)
        require(cwd.is_dir(), "check cwd does not exist")
        require(
            isinstance(check.get("timeout", 300), int) and 1 <= check.get("timeout", 300) <= 3600,
            "invalid check timeout",
        )

    validated = invoke_contract(script, "check", case)
    require(validated.returncode == 0, "contract is not frozen/valid: " + validated.stderr.strip())
    spec = load(case / "spec.json")
    frozen = {str(case / "spec.json"): file_hash(case / "spec.json")}
    for key in ("expected", "baseline"):
        path = contained(case, spec[key])
        frozen[str(path)] = file_hash(path)
    contract = load(case / spec["baseline"])
    require(
        all(row["requirement"] in ("required", "not_applicable") for row in contract["expected_rows"]),
        "issue requires resolved applicability; investigate is forbidden",
    )
    require(
        all(row["basis"].strip() and row["acceptance"].strip() for row in contract["expected_rows"]),
        "required and not_applicable criteria need a basis and acceptance rule",
    )
    previous_baseline = spec["revision"].get("previous_baseline")
    while previous_baseline:
        path = contained(case, previous_baseline)
        require(str(path) not in frozen, "cyclic contract baseline history")
        frozen[str(path)] = file_hash(path)
        previous_baseline = load(path)["spec"]["revision"].get("previous_baseline")

    number = len(state["rounds"]) + 1
    directory = root / "rounds" / f"{number:03d}"
    require(not directory.exists(), "round directory already exists")
    script_sha = file_hash(script)
    manifest_to_save = {**manifest, "contract_script": str(script)}
    baseline = {
        "head": git(repo, "rev-parse", "HEAD").decode().strip(),
        "initial_head_changed_paths": initial_dirty,
        "source": snapshot(repo),
        "policies": policies,
        "contract": frozen,
        "contract_script": str(script),
        "contract_sha256": script_sha,
        "taskkit_sha256": script_sha,
        "profile": {
            "path": str(profile_file),
            "name": profile["name"],
            "sha256": file_hash(profile_file),
            "protected_paths": profile_protected,
        },
    }
    round_ = {
        "number": number,
        "repo": str(repo),
        "case_root": str(case),
        "profile": profile["name"],
        "reason": reason,
        "directory": str(directory.relative_to(root)),
        "artifacts": {},
    }
    staging = Path(tempfile.mkdtemp(prefix=".issue-", dir=root))
    try:
        for name, value in (("manifest.json", manifest_to_save), ("baseline.json", baseline)):
            write_new(staging / name, value)
        for source, expected in frozen.items():
            target = staging / "contract" / Path(source).relative_to(case)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(source).read_bytes())
            require(file_hash(target) == expected, "contract input changed while archiving")
        for index, (source, expected) in enumerate(policies.items()):
            target = staging / "policies" / f"{index:03d}-{Path(source).name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(source).read_bytes())
            require(file_hash(target) == expected, "policy changed while archiving")
        archived_script = staging / "contract-engine.py"
        archived_script.write_bytes(script.read_bytes())
        require(file_hash(archived_script) == script_sha, "contract script changed while archiving")
        checked = invoke_contract(archived_script, "check", staging / "contract")
        require(checked.returncode == 0, "archived contract is not independently replayable")
        for path in staging.rglob("*"):
            if path.is_file():
                round_["artifacts"][str((directory / path.relative_to(staging)).relative_to(root))] = file_hash(path)
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    state["max_rounds"] = budget
    state["rounds"].append(round_)
    state["phase"] = "issued"


def active_inputs(root: Path, state: dict) -> tuple[dict, Path, dict, dict]:
    round_ = state["rounds"][-1]
    directory = root / round_["directory"]
    return round_, directory, load(directory / "manifest.json"), load(directory / "baseline.json")


def verify_inputs(manifest: dict, baseline: dict) -> None:
    for path, expected in (baseline["policies"] | baseline["contract"]).items():
        require(file_hash(path) == expected, f"frozen input changed: {path}")
    script_path = manifest.get("contract_script") or manifest.get("taskkit_script") or baseline.get("contract_script")
    expected_sha = baseline.get("contract_sha256") or baseline.get("taskkit_sha256")
    require(bool(script_path) and file_hash(script_path) == expected_sha, "contract script changed")
    checked = invoke_contract(script_path, "check", manifest["case_root"])
    require(checked.returncode == 0, "frozen contract failed verification")
    require(git(Path(manifest["repo"]), "rev-parse", "HEAD").decode().strip() == baseline["head"], "executor changed Git HEAD")


def verify_boundary(manifest: dict, baseline: dict, current: dict) -> list[str]:
    touched = changes(baseline["source"], current)
    profile = baseline.get("profile")
    require(profile is not None, "round baseline lacks project profile")
    protected = [*profile["protected_paths"], *manifest["protected_paths"]]
    bad = [p for p in touched if matches(p, protected) or not matches(p, manifest["allowed_paths"])]
    require(not bad, "changes outside writable scope: " + ", ".join(bad))
    for p in touched:
        contained(Path(manifest["repo"]), p)
    return touched


def execute(root: Path, state: dict, previous: str, seq: int) -> int:
    round_, directory, manifest, baseline = active_inputs(root, state)
    state["phase"] = "running"
    record(root, state, "executor", "execute-start", previous, seq)
    result: dict[str, object] = {"started_at": now(), "checks": [], "passed": False}
    candidate = None
    repo = Path(manifest["repo"])
    try:
        verify_inputs(manifest, baseline)
        candidate = snapshot(repo)
        result["changed_paths"] = verify_boundary(manifest, baseline, candidate)
        result["head_changed_paths"] = head_changed_paths(repo)
        candidate_path = directory / "candidate.json"
        write_new(candidate_path, candidate)
        remember(root, round_, candidate_path)
        result["candidate_sha256"] = file_hash(candidate_path)
        checks_log: list[dict] = []
        result["checks"] = checks_log
        for check in manifest["checks"]:
            verify_inputs(manifest, baseline)
            before = snapshot(repo)
            require(
                before == candidate,
                "candidate changed before check " + check["id"] + ": " + ", ".join(changes(candidate, before)),
            )
            path = directory / (check["id"] + ".log")
            with path.open("xb") as log:
                outcome = run_check(check, contained(repo, check.get("cwd", "."), directory=True), log)
            remember(root, round_, path)
            checks_log.append({"id": check["id"], "log": str(path.relative_to(root)), **outcome})
            verify_inputs(manifest, baseline)
            after = snapshot(repo)
            require(
                after == candidate,
                "candidate changed during check " + check["id"] + ": " + ", ".join(changes(candidate, after)),
            )
            if outcome["exit_code"] != 0:
                break
        verify_inputs(manifest, baseline)
        current = snapshot(repo)
        require(current == candidate, "candidate changed after checks")
        result["passed"] = len(checks_log) == len(manifest["checks"]) and all(
            x["exit_code"] == 0 for x in checks_log
        )
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    result["source"] = candidate
    result["observed_source_sha256"] = digest(snapshot(repo))
    result["finished_at"] = now()
    path = directory / "execution.json"
    write_new(path, result)
    remember(root, round_, path)
    _, last, count = read_state(root)
    state["phase"] = "awaiting_review"
    record(root, state, "executor", "execute-finish", last, count)
    return 0 if result["passed"] else 1


def review(root: Path, state: dict, verdict: str, feedback_file: Path, report_file: str | None) -> None:
    round_, directory, manifest, baseline = active_inputs(root, state)
    feedback = absolute_regular(str(Path(feedback_file).absolute()))
    require(bool(feedback.read_text(encoding="utf-8").strip()), "external review feedback must be nonempty")
    archived_script = archived_contract_script(directory)
    report = None
    replayed = None
    if report_file:
        case = Path(manifest["case_root"])
        report_path = contained(case, report_file)
        report = load(report_path)
        replayed = invoke_contract(archived_script, "replay", case, "--report", report_file)
        require(replayed.returncode in (0, 1), "contract report integrity failed: " + replayed.stderr.strip())
    if verdict == "accept":
        require(state["phase"] == "awaiting_review", "unfinished execution cannot be accepted")
        execution = load(directory / "execution.json")
        require(bool(execution["passed"]), "failed execution cannot be accepted")
        verify_inputs(manifest, baseline)
        current = snapshot(Path(manifest["repo"]))
        verify_boundary(manifest, baseline, current)
        require(current == execution["source"], "source changed after execution evidence was sealed")
        require(
            report is not None
            and replayed is not None
            and replayed.returncode == 0
            and report.get("assessment") == "PASS"
            and report.get("required_count", 0) > 0
            and report["required_pass"] == report["required_count"],
            "accept requires a replayed PASS for every required criterion",
        )
    copied = directory / "feedback.md"
    with copied.open("xb") as dest:
        dest.write(feedback.read_bytes())
    remember(root, round_, copied)
    if report is not None and report_file is not None:
        refs = {report_file, report["observations_file"], *report["evidence_fingerprints"].keys()}
        for relative in refs:
            source = contained(Path(manifest["case_root"]), relative)
            target = contained(directory / "review-evidence", relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as dest:
                dest.write(source.read_bytes())
            remember(root, round_, target)
            archived_target = contained(directory / "contract", relative)
            archived_target.parent.mkdir(parents=True, exist_ok=True)
            if archived_target.exists():
                require(archived_target.read_bytes() == source.read_bytes(), "evidence conflicts with frozen contract")
            else:
                with archived_target.open("xb") as dest:
                    dest.write(source.read_bytes())
            remember(root, round_, archived_target)
        archived_replay = invoke_contract(archived_script, "replay", directory / "contract", "--report", report_file)
        require(archived_replay.returncode in (0, 1), "archived review report failed replay")
    review_path = directory / "review.json"
    write_new(
        review_path,
        {
            "verdict": verdict,
            "at": now(),
            "reviewer": "reviewer",
            "feedback_sha256": file_hash(copied),
            "report": report_file,
            "assessment": report.get("assessment") if report else "NOT_RUN",
        },
    )
    remember(root, round_, review_path)
    round_["review_hash"] = file_hash(review_path)
    state["phase"] = "accepted" if verdict == "accept" else "rejected"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "issue", "execute", "review", "recycle", "status"))
    parser.add_argument("--root", type=Path, required=True, help="supervisor control directory outside executor repo")
    parser.add_argument("--authority", type=Path, help="new role-key directory outside control root and repo")
    parser.add_argument("--token", type=Path, help="role key file (supervisor.key / executor.key / reviewer.key)")
    parser.add_argument("--manifest", type=Path, help="round execution manifest JSON")
    parser.add_argument("--verdict", choices=("accept", "reject"))
    parser.add_argument("--feedback", type=Path, help="external review feedback markdown/text file")
    parser.add_argument("--report", help="case-relative comparison report JSON")
    parser.add_argument("--from-review", help="sha256 of the previous rejecting review")
    parser.add_argument("--change-reason", help="explicit reason for recycling into a new round")
    args = parser.parse_args(argv)

    raw_root = args.root if args.root.is_absolute() else (Path.cwd() / args.root)
    check_no_user_symlink_ancestors(raw_root, "control root")
    root = raw_root.resolve()
    with locked(root):
        if args.action == "init":
            require(
                not (root / "state.json").exists() and not (root / "events.jsonl").exists(),
                "controller already initialized",
            )
            require(args.authority is not None, "init requires --authority")
            raw_auth = args.authority if args.authority.is_absolute() else (Path.cwd() / args.authority)
            check_no_user_symlink_ancestors(raw_auth, "authority")
            authority = raw_auth.resolve()
            require(
                authority != root and root not in authority.parents and authority not in root.parents,
                "authority must be separate from control root",
            )
            require(not authority.exists(), "authority directory must be new")
            authority.mkdir(parents=True, mode=0o700)
            roles = {}
            for role in ("supervisor", "executor", "reviewer"):
                value = secrets.token_hex(32)
                fd = os.open(authority / (role + ".key"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(value + "\n")
                roles[role] = hashlib.sha256(value.encode("utf-8")).hexdigest()
            state = {
                "schema_version": 1,
                "phase": "idle",
                "authority": str(authority),
                "roles": roles,
                "rounds": [],
            }
            record(root, state, "supervisor", "init")
        else:
            state, previous, seq = read_state(root)
            if args.action == "status":
                if state["phase"] in ("issued", "awaiting_review"):
                    _, _, manifest, baseline = active_inputs(root, state)
                    verify_inputs(manifest, baseline)
            elif args.action in ("issue", "recycle"):
                authenticate(state, args.token, "supervisor")
                require(args.manifest is not None, "manifest is required")
                if args.action == "issue":
                    require(state["phase"] == "idle", "issue is allowed only for a new controller")
                    reason = "Initial supervisor-issued contract"
                else:
                    require(state["phase"] == "rejected", "recycle needs an external rejecting review")
                    require(
                        args.from_review == state["rounds"][-1]["review_hash"],
                        "recycle must reference the rejecting review hash",
                    )
                    require(
                        bool(args.change_reason and args.change_reason.strip()),
                        "recycle needs an explicit change reason",
                    )
                    reason = args.change_reason
                prepare_round(root, state, args.manifest, reason)
                record(root, state, "supervisor", args.action, previous, seq)
            elif args.action == "execute":
                authenticate(state, args.token, "executor")
                require(state["phase"] == "issued", "execute is allowed exactly once per issued round")
                code = execute(root, state, previous, seq)
                print(
                    json.dumps(
                        {"phase": state["phase"], "round": len(state["rounds"]), "execution_passed": code == 0}
                    )
                )
                return code
            else:
                authenticate(state, args.token, "reviewer")
                require(
                    state["phase"] in ("running", "awaiting_review"),
                    "review needs a submitted or interrupted execution",
                )
                require(
                    bool(args.verdict and args.feedback is not None),
                    "review requires verdict and external feedback",
                )
                review(root, state, args.verdict, args.feedback, args.report)
                record(root, state, "reviewer", "review-" + args.verdict, previous, seq)
        print(
            json.dumps(
                {
                    "phase": state["phase"],
                    "round": len(state["rounds"]),
                    "max_rounds": state.get("max_rounds"),
                    "review_hash": state["rounds"][-1].get("review_hash") if state["rounds"] else None,
                }
            )
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
