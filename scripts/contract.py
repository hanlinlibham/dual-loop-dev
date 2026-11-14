#!/usr/bin/env python3
"""Built-in zero-dependency evidence contract engine (TaskKit v0.5 protocol compatible).

Supports freezing an acceptance contract (spec.json + inputs/expected.csv),
checking contract integrity, reconciling external reviewer observations into a
sealed comparison report, and replaying archived reports offline.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


EXPECTED_FIELDS = {"id", "requirement", "description", "basis", "acceptance"}
REQUIREMENTS = {"required", "investigate", "not_applicable"}
RESULTS = {"PASS", "FAIL", "PARTIAL", "SKIP", "NOT_RUN"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_path(base: Path, relative: str) -> Path:
    require(nonempty(relative), "path must be a nonempty relative string")
    raw = Path(relative)
    require(not raw.is_absolute() and ".." not in raw.parts, f"path escapes its root: {relative}")
    base_resolved = base.resolve()
    current = base_resolved
    for part in raw.parts:
        current /= part
        require(not current.is_symlink(), f"symlink path is forbidden: {relative}")
    path = current.resolve()
    require(base_resolved in path.parents, f"path escapes its root: {relative}")
    rel_parts = path.relative_to(base_resolved).parts
    require(
        not any(part.startswith(".env") or part in {".git", ".venv", "__pycache__"} for part in rel_parts),
        f"excluded path: {relative}",
    )
    return path


def fingerprint(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def write_new(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_timestamp(value: object) -> datetime:
    require(nonempty(value), "timestamp must be nonempty")
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp must include a timezone")
    return result


def read_spec(project: Path) -> tuple[dict, list[dict[str, str]]]:
    spec_file = project / "spec.json"
    require(spec_file.is_file(), "spec.json does not exist")
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    require(isinstance(spec, dict) and spec.get("schema_version") == 1, "spec schema_version must be 1")
    for field in (
        "topic",
        "question",
        "as_of",
        "expected",
        "baseline",
        "run_command",
        "environment",
        "sample_plan",
        "stop_rule",
    ):
        require(nonempty(spec.get(field)), f"spec needs {field}")
    require(bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", spec["topic"])), "topic must be kebab-case")
    datetime.strptime(spec["as_of"], "%Y-%m-%d")
    require(spec.get("baseline_timing") in {"prospective", "retrospective"}, "invalid baseline_timing")

    scope = spec.get("scope", {})
    require(isinstance(scope, dict), "scope must be an object")
    for field in ("population", "denominator_basis"):
        require(nonempty(scope.get(field)), f"scope needs {field}")
    for field in ("included", "excluded"):
        require(
            isinstance(scope.get(field), list) and all(nonempty(x) for x in scope[field]),
            f"scope.{field} must be a list of strings",
        )
    require(bool(scope["included"]), "scope.included cannot be empty")
    require(scope.get("enumeration") in {"closed", "bounded", "open"}, "invalid enumeration")

    revision = spec.get("revision", {})
    require(isinstance(revision, dict) and nonempty(revision.get("reason")), "revision needs a reason")
    require("previous_baseline" in revision, "revision must declare previous_baseline (null for initial)")
    require(
        revision["previous_baseline"] is None or nonempty(revision["previous_baseline"]),
        "previous_baseline must be null or a nonempty path",
    )
    if revision.get("event") is not None:
        require(safe_path(project, revision["event"]).is_file(), "revision.event must exist within project")

    expected_path = safe_path(project, spec["expected"])
    baseline_path = safe_path(project, spec["baseline"])
    require(
        expected_path.parent == project / "inputs" and baseline_path.parent == project / "inputs",
        "expected and baseline must be files in project inputs/",
    )
    require(expected_path != baseline_path, "expected and baseline paths must differ")
    require(
        bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}-baseline-[a-zA-Z0-9-]+\.json", baseline_path.name)),
        "baseline filename must be YYYY-MM-DD-baseline-VERSION.json",
    )
    require(expected_path.is_file(), f"expected CSV missing: {spec['expected']}")
    with expected_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        require(EXPECTED_FIELDS <= set(reader.fieldnames or []), "expected CSV lacks required columns")
        require(len(reader.fieldnames or []) == len(set(reader.fieldnames or [])), "duplicate expected CSV columns")
        rows = list(reader)
    require(bool(rows), "expected CSV must contain at least one item")
    for row in rows:
        require(all(nonempty(row.get(k)) for k in EXPECTED_FIELDS), "expected item has empty fields")
        require(row["requirement"] in REQUIREMENTS, f"invalid requirement for {row['id']}")
        require(row["id"] == row["id"].strip() and not any(c in row["id"] for c in "\r\n"), "invalid item ID")
        require(None not in row, "expected CSV has surplus values")
    require(len({r["id"] for r in rows}) == len(rows), "duplicate expected IDs")
    require(any(r["requirement"] != "not_applicable" for r in rows), "expected list has no applicable items")
    return spec, rows


def init_case(
    project: Path,
    *,
    topic: str,
    question: str,
    criteria: list[dict[str, str]],
    version: str = "v1",
    reason: str = "Initial supervised contract",
    included: list[str] | None = None,
    excluded: list[str] | None = None,
    run_command: str = "dual-loop controller execute",
    environment: str = "isolated git worktree",
    stop_rule: str = "Stop after single execution attempt and submit to independent review",
) -> dict[str, object]:
    require(not (project / "spec.json").exists(), "case spec.json already exists; never overwrite")
    require(bool(criteria), "criteria cannot be empty")
    project.mkdir(parents=True, exist_ok=True)
    (project / "inputs").mkdir(exist_ok=True)
    (project / "outputs").mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    baseline_rel = f"inputs/{today}-baseline-{version}.json"
    spec = {
        "schema_version": 1,
        "topic": topic,
        "question": question,
        "as_of": today,
        "expected": "inputs/expected.csv",
        "baseline": baseline_rel,
        "run_command": run_command,
        "environment": environment,
        "sample_plan": f"{len(criteria)} explicit acceptance criteria",
        "stop_rule": stop_rule,
        "baseline_timing": "prospective",
        "scope": {
            "population": topic,
            "denominator_basis": "explicitly enumerated contract rows",
            "included": included or [topic],
            "excluded": excluded or ["out-of-scope repository paths"],
            "enumeration": "closed",
        },
        "revision": {
            "reason": reason,
            "previous_baseline": None,
        },
    }
    write_new(project / "spec.json", spec)
    expected_file = project / "inputs" / "expected.csv"
    with expected_file.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "requirement", "description", "basis", "acceptance"])
        writer.writeheader()
        for item in criteria:
            writer.writerow(
                {
                    "id": item["id"],
                    "requirement": item.get("requirement", "required"),
                    "description": item["description"],
                    "basis": item.get("basis", "Task specification"),
                    "acceptance": item["acceptance"],
                }
            )
    return {"case_root": str(project.resolve()), "spec": "spec.json", "expected": "inputs/expected.csv"}


def freeze_contract(project: Path) -> dict[str, object]:
    project = project.resolve()
    spec, rows = read_spec(project)
    target = safe_path(project, spec["baseline"])
    require(not target.exists(), "baseline already exists; create a new revision, never overwrite")
    previous = spec["revision"]["previous_baseline"]
    old_rows: dict[str, dict[str, str]] = {}
    previous_hash = None
    existing = list((project / "inputs").glob("*-baseline-*.json"))
    if not previous:
        require(not existing, "existing baseline found; new revisions must reference previous_baseline")
    else:
        prior = safe_path(project, previous)
        require(prior.is_file(), f"previous_baseline missing: {previous}")
        require(prior != target, "previous_baseline cannot be the new baseline")
        old = json.loads(prior.read_text(encoding="utf-8"))
        require(old["spec"]["topic"] == spec["topic"], "previous baseline belongs to a different topic")
        old_rows = {r["id"]: r for r in old["expected_rows"]}
        previous_hash = fingerprint(prior)["sha256"]
    new_rows = {r["id"]: r for r in rows}
    changes = {
        "added_ids": sorted(new_rows.keys() - old_rows.keys()),
        "removed_ids": sorted(old_rows.keys() - new_rows.keys()),
        "changed_ids": sorted(k for k in new_rows.keys() & old_rows.keys() if new_rows[k] != old_rows[k]),
    }
    baseline = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "spec_sha256": fingerprint(project / "spec.json")["sha256"],
        "expected_sha256": fingerprint(safe_path(project, spec["expected"]))["sha256"],
        "spec": spec,
        "expected_rows": rows,
        "previous_baseline_sha256": previous_hash,
        "changes": changes,
    }
    write_new(target, baseline)
    return {
        "baseline": str(target.relative_to(project)),
        **fingerprint(target),
        "baseline_timing": spec["baseline_timing"],
        "expected_items": len(rows),
        "changes": changes,
    }


def load_contract(project: Path) -> tuple[dict, list[dict[str, str]], dict, Path]:
    project = project.resolve()
    spec, rows = read_spec(project)
    path = safe_path(project, spec["baseline"])
    require(path.is_file(), f"frozen baseline missing: {spec['baseline']}")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    require(baseline.get("schema_version") == 1, "invalid baseline version")
    require(baseline["spec_sha256"] == fingerprint(project / "spec.json")["sha256"], "spec changed after freeze")
    require(
        baseline["expected_sha256"] == fingerprint(safe_path(project, spec["expected"]))["sha256"],
        "expected list changed after freeze",
    )
    require(baseline["spec"] == spec and baseline["expected_rows"] == rows, "baseline snapshot is inconsistent")
    parse_timestamp(baseline["created_at"])
    if spec["revision"]["previous_baseline"]:
        prior = safe_path(project, spec["revision"]["previous_baseline"])
        require(fingerprint(prior)["sha256"] == baseline["previous_baseline_sha256"], "previous baseline changed")
    return spec, rows, baseline, path


def compare_observations(project: Path, observed_rel: str) -> dict[str, object]:
    project = project.resolve()
    observed_path = safe_path(project, observed_rel)
    require(observed_path.is_file(), f"observations file missing: {observed_rel}")
    spec, expected, baseline, baseline_path = load_contract(project)
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    require(isinstance(observed, dict), "observations must be an object")
    baseline_hash = fingerprint(baseline_path)["sha256"]
    require(observed.get("baseline_sha256") == baseline_hash, "observations reference a different baseline")
    observed_time = parse_timestamp(observed.get("observed_at"))
    if spec["baseline_timing"] == "prospective":
        require(observed_time >= parse_timestamp(baseline["created_at"]), "observations precede prospective baseline")

    records = observed.get("rows")
    require(isinstance(records, list), "observations.rows must be a list")
    by_id: dict[str, list[dict]] = {}
    evidence: dict[str, dict[str, object]] = {}
    for row in records:
        require(isinstance(row, dict) and nonempty(row.get("id")), "observation needs an ID")
        require(row.get("status") in RESULTS, f"invalid status for {row['id']}")
        require(nonempty(row.get("detail")), f"observation needs detail: {row['id']}")
        require(nonempty(row.get("evidence_level")), f"observation needs evidence_level: {row['id']}")
        refs = row.get("evidence")
        require(isinstance(refs, list) and all(nonempty(x) for x in refs), "evidence must be a list of local paths")
        require(row["status"] == "NOT_RUN" or bool(refs), f"executed observation lacks evidence: {row['id']}")
        for ref in refs:
            if ref not in evidence:
                ev_path = safe_path(project, ref)
                require(ev_path.is_file(), f"evidence file missing: {ref}")
                evidence[ref] = fingerprint(ev_path)
        by_id.setdefault(row["id"], []).append(row)

    expected_ids = {r["id"] for r in expected}
    unexpected = sorted(by_id.keys() - expected_ids)
    duplicates = sorted(k for k, values in by_id.items() if len(values) > 1)
    missing = sorted(r["id"] for r in expected if r["requirement"] != "not_applicable" and r["id"] not in by_id)

    results = []
    for row in expected:
        matches = by_id.get(row["id"], [])
        state = "DUPLICATE" if len(matches) > 1 else matches[0]["status"] if matches else "NOT_RUN"
        if row["requirement"] == "not_applicable":
            state = (
                "NOT_APPLICABLE"
                if not matches or all(x["status"] == "SKIP" for x in matches)
                else "UNEXPECTED_EXECUTION"
            )
        results.append({**row, "status": state, "observations": matches})

    required = [r for r in results if r["requirement"] == "required"]
    unknown = [r["id"] for r in results if r["requirement"] == "investigate" and r["status"] != "PASS"]
    invalid_na = [r["id"] for r in results if r["status"] == "UNEXPECTED_EXECUTION"]
    assessment = (
        "FAIL"
        if duplicates or any(r["status"] == "FAIL" for r in required)
        else (
            "PARTIAL"
            if unexpected or unknown or invalid_na or any(r["status"] != "PASS" for r in required)
            else "PASS"
        )
    )
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "topic": spec["topic"],
        "baseline_sha256": baseline_hash,
        "baseline_timing": spec["baseline_timing"],
        "scope": spec["scope"],
        "observations_file": str(observed_path.resolve().relative_to(project)),
        "observations_fingerprint": fingerprint(observed_path),
        "observed_at": observed["observed_at"],
        "assessment": assessment,
        "expected_count": len(expected),
        "observation_count": len(records),
        "unique_observation_ids": len(by_id),
        "required_count": len(required),
        "required_pass": sum(r["status"] == "PASS" for r in required),
        "investigate_count": sum(r["requirement"] == "investigate" for r in results),
        "not_applicable_count": sum(r["requirement"] == "not_applicable" for r in results),
        "status_counts": dict(Counter(r["status"] for r in results)),
        "missing_ids": missing,
        "duplicate_ids": duplicates,
        "unexpected_ids": unexpected,
        "unresolved_ids": unknown,
        "unexpected_execution_ids": invalid_na,
        "claim_limit": "Only the frozen list and stated evidence levels were assessed; this is not proof of universal completeness.",
        "results": results,
        "evidence_fingerprints": evidence,
    }


def replay(project: Path, report_rel: str) -> dict[str, object]:
    project = project.resolve()
    report_file = safe_path(project, report_rel)
    require(report_file.is_file(), f"report file missing: {report_rel}")
    saved = json.loads(report_file.read_text(encoding="utf-8"))
    actual = compare_observations(project, saved["observations_file"])
    strip_ts = lambda val: {k: v for k, v in val.items() if k != "created_at"}
    require(strip_ts(saved) == strip_ts(actual), "saved report or its evidence changed; preserve it and create a new report")
    return {"integrity": "PASS", "assessment": saved["assessment"], "claim_limit": saved["claim_limit"]}


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        sys.argv = [os.fsencode(arg).decode("utf-8") for arg in sys.argv]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init-case", "freeze", "compare", "check", "replay"))
    parser.add_argument("--root", required=True, type=Path, help="case directory; all evidence paths are relative to it")
    parser.add_argument("--observations", help="case-relative observations JSON")
    parser.add_argument("--out", help="new case-relative outputs/*-comparison.json")
    parser.add_argument("--report", help="case-relative report for replay")
    parser.add_argument("--topic", help="kebab-case topic name (for init-case)")
    parser.add_argument("--question", help="task objective question (for init-case)")
    parser.add_argument("--criteria-json", type=Path, help="JSON array of criteria objects (for init-case)")
    parser.add_argument("--version", default="v1", help="baseline version suffix (for init-case)")
    parser.add_argument("--reason", default="Initial supervised contract", help="revision reason (for init-case)")
    args = parser.parse_args(argv)

    if args.action == "init-case":
        require(bool(args.topic and args.question and args.criteria_json), "init-case needs --topic, --question, and --criteria-json")
        criteria = json.loads(args.criteria_json.read_text(encoding="utf-8"))
        require(isinstance(criteria, list), "--criteria-json must contain a JSON array")
        result = init_case(
            args.root,
            topic=args.topic,
            question=args.question,
            criteria=criteria,
            version=args.version,
            reason=args.reason,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    project = args.root.resolve()
    require(project.is_dir(), "task directory does not exist")
    if args.action == "freeze":
        require(not any((args.observations, args.out, args.report)), "freeze does not accept observations/out/report")
        result = freeze_contract(project)
    elif args.action == "compare":
        require(bool(args.observations and args.out and not args.report), "compare needs --observations and --out")
        out = safe_path(project, args.out)
        require(
            project / "outputs" in out.parents and out.name.endswith("-comparison.json"),
            "report must be a new outputs/*-comparison.json",
        )
        require(not out.exists(), "report already exists; use a new name")
        result = compare_observations(project, args.observations)
        write_new(out, result)
    elif args.action == "replay":
        require(bool(args.report and not args.observations and not args.out), "replay needs only --report")
        result = replay(project, args.report)
    else:
        require(not any((args.observations, args.out, args.report)), "check does not accept observations/out/report")
        _, rows, _, path = load_contract(project)
        result = {
            "integrity": "PASS",
            "baseline": str(path.relative_to(project)),
            "expected_items": len(rows),
            "claim_limit": "Contract integrity only; no task outcome has been verified.",
        }
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"results", "evidence_fingerprints", "scope", "changes"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if result.get("assessment") in {"FAIL", "PARTIAL"} else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
