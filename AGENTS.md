# AGENTS.md — Apply or Maintain dual-loop-dev

Read this file for the command/configuration entry point. [SKILL.md](SKILL.md) defines the shared workflow; [review briefs](references/brief-templates.md) and [risk tiers](references/workflow-tiers.md) add details when needed. 使用本框架时以本文件为命令入口，不重复维护多份任务定义。

## 1. Choose the mode

- **Native subagents:** the host is supervisor, an executor implements, and an independent reviewer judges. See Sections 2–5.
- **CLI workers:** use the single-task orchestrator or roadmap runner in Section 3. Supported backends are `claude`, `codex`, and custom `command`.
- **No independent reviewer available:** seal the candidate and leave it awaiting review. A single session switching role tokens must not claim independent acceptance.
- **Maintaining this framework:** follow Section 6.

First review is cold and independent of the author. A native host may reuse that reviewer once for a scoped fix it requested. Changed objectives/scope, new public-contract/concurrency/security assumptions, repeated failure, or evidence of a missed defect require a fresh cold reviewer. CLI calls remain separate processes; a focused follow-up brief does not resume a reviewer session.

## 2. Prepare an isolated workspace

Requires Python 3.10+ standard library and Git, with no package install. Clone the framework outside the target worktree:

```sh
git clone https://github.com/hanlinlibham/dual-loop-dev.git /tmp/dual-loop-dev
export DL=/tmp/dual-loop-dev
export TARGET_REPO=/absolute/path/to/target-repository
export STATE=/absolute/path/outside-target/task-state
python3 "$DL/scripts/preflight.py" --repo "$TARGET_REPO" --workspace "$(dirname "$TARGET_REPO")"
python3 "$DL/scripts/worktree.py" create --repo "$TARGET_REPO" \
  --task-id task-01 --worktree "$STATE/worktree" --port-range 3200-3299 --lock-file "$STATE/LOCK"
export WORK_REPO="$STATE/worktree"
```

Use existing architecture and current test evidence; fill gaps with relevant baseline checks. Split work only where the goal needs distinct bounded packages. Choose risk by behavior, not file count or extension.

Control, authority and case directories must be outside the target worktree. In manifests, `repo`, `case_root`, `profile`, and `policy_files` use absolute paths; writable/protected paths are repository-relative, with a trailing slash for directories. Ensure generated artifacts are ignored before issuing a round. Checks must not format or mutate tracked source.

Create a profile and a policy anchor outside the worktree, or use existing repository files. A minimal profile is:

```json
{
  "schema_version": 1,
  "name": "target-service",
  "protected_paths": [".gitignore", "migrations/"],
  "require_clean_worktree": true
}
```

## 3. Maintain one task definition

Store the objective, scope, criteria, checks and risk notes once in `task.json`; derive the contract, manifest and briefs. Replace the example paths and criteria with the actual task:

```json
{
  "id": "task-01",
  "title": "Deliver the specified behavior",
  "repo": "/absolute/path/to/task-state/worktree",
  "profile": "/absolute/path/to/profile.json",
  "policy_files": ["/absolute/path/to/POLICY.md"],
  "allowed_paths": ["src/module.py", "tests/test_module.py"],
  "protected_paths": [],
  "tier": "L2",
  "brief": "State the requested behavior, relevant risks and stopping condition.",
  "criteria": [
    {
      "id": "behavior-verified",
      "requirement": "required",
      "description": "Requested behavior and its relevant boundary cases work",
      "basis": "The task specification",
      "acceptance": "Checks pass and independent evidence covers the stated risks"
    }
  ],
  "checks": [
    {
      "id": "unit-tests",
      "argv": ["python3", "-B", "-m", "unittest", "discover", "-s", "tests"],
      "cwd": ".",
      "timeout": 300
    }
  ],
  "max_rounds": 3,
  "executor_backend": {"kind": "codex"},
  "reviewer_backend": {"kind": "codex"}
}
```

Every criterion must be `required` or justified `not_applicable`; unresolved `investigate` cannot be issued. Optional `probe_preflight_checks` validate existing staged probe syntax/imports and fixture shape before the first issue and after contract repair. They run before creating the new case, so use staged source paths, not future case paths. They must not evaluate the business behavior still being implemented. A failed preflight preserves the previous round and consumes no new round; its logs remain under `probe-preflight/attempt-NNN/`.

**CLI mode:** the orchestrator generates round artifacts from this task:

```sh
python3 "$DL/scripts/orchestrator.py" --task-config "$STATE/task.json" --state-dir "$STATE/run"
```

For dependent packages, use [roadmap.schema.json](schemas/roadmap.schema.json):

```sh
python3 "$DL/scripts/roadmap.py" run --roadmap "$STATE/roadmap.json" --state-dir "$STATE/run"
python3 "$DL/scripts/roadmap.py" daemon --roadmap "$STATE/roadmap.json" --state-dir "$STATE/run" --poll-interval 15
```

**Native-host mode:** derive `criteria.json` from task.criteria and a manifest containing repo, case_root, profile, policy_files, allowed_paths, protected_paths, checks and max_rounds. These are generated artifacts, not separately maintained specifications. Use [manifest.schema.json](schemas/manifest.schema.json) and [profile.schema.json](schemas/profile.schema.json).

```sh
export CTRL="$STATE/control"
export AUTH="$STATE/authority"
export CASE="$STATE/cases/r001"
python3 "$DL/scripts/controller.py" init --root "$CTRL" --authority "$AUTH"
python3 "$DL/scripts/contract.py" init-case --root "$CASE" --topic task-01 \
  --question "Deliver the specified behavior" --criteria-json "$STATE/criteria.json"
python3 "$DL/scripts/contract.py" freeze --root "$CASE"
python3 "$DL/scripts/controller.py" issue --root "$CTRL" --token "$AUTH/supervisor.key" \
  --manifest "$STATE/manifest-r001.json"
```

## 4. Execute once and review once

Give the executor only a scoped copy of its role key. Create the worker-token directory first, use restrictive permissions, and remove copies after the worker returns.

The executor edits only allowed paths and runs targeted checks during development. It does not need to pre-run the full formal checklist. Submit the candidate exactly once:

```sh
python3 "$DL/scripts/controller.py" execute --root "$CTRL" --token "$EXECUTOR_TOKEN"
```

The controller runs the frozen checks. Whether execution succeeds or fails, stop editing the candidate. The executor only adds design decisions, uncertainties and uncovered boundaries; receipts already contain paths, exits and check counts.

The independent reviewer reads the diff, verifies scope and sealed candidate/check receipts, and performs risk-specific verification. Valid receipts do not require a routine second full run. Missing evidence, unclear environment, inconsistent candidate, unstable checks or a new risk justify rerunning relevant checks. Mutation probes run on a temporary copy and target important guards, with no quota.

Write one `outputs/observations.json` with the original rows/evidence and a `review` object:

```json
{
  "summary": "<actual review conclusion>",
  "attribution": "code",
  "verification_only": false,
  "blockers": ["<criterion, trigger, impact and required follow-up>"],
  "non_blocking": []
}
```

The attribution is required when rejecting; values are `code`, `contract`, or `environment`. See the [full observations example](references/brief-templates.md#observations主结果). Fill PASS/FAIL/NOT_RUN from actual evidence; never prefill PASS. The blockers list records unresolved code/product defects. For a pure probe/fixture failure, put the failure and its evidence in summary, leave product blockers empty, and use contract attribution with verification_only=true. Non-blocking suggestions do not delay acceptance.

```sh
python3 "$DL/scripts/controller.py" review --root "$CTRL" --token "$REVIEWER_TOKEN" \
  --verdict accept --observations outputs/observations.json
```

Use `--verdict reject` for a failed or incomplete review. The command generates compatible feedback and comparison artifacts; it does not decide the truth of observations. Acceptance still requires successful execution, unchanged candidate, every required criterion passing, and no blockers.

**Compatibility:** manual `contract.py compare` followed by `review --feedback <absolute-file> --report <case-relative-path>` remains supported, including external TaskKit. Existing archives and running tasks need no migration; switch workflow versions between tasks.

## 5. Rejection, verification-only rounds and completion

For code defects, fix only blocking findings without weakening acceptance. For contract defects, distinguish changed requirements from a broken probe. For environment defects, restore isolation/environment before continuing. A new case and rejecting review hash are required:

```sh
python3 "$DL/scripts/controller.py" recycle --root "$CTRL" --token "$AUTH/supervisor.key" \
  --manifest "$STATE/manifest-r002.json" --from-review "$REVIEW_HASH" \
  --change-reason "Address the identified blocking issue"
```

A **verification-only round** is allowed only when the immediately preceding rejected review explicitly records `ATTRIBUTION: contract` and `VERIFICATION_ONLY: true`, with no code blockers. The entire candidate, HEAD, objective, criteria and scope remain unchanged; retain the complete ordered check-ID list. Profile, writable/protected paths and governance policy remain fixed. Revised case-local probe policies must be frozen at the corresponding path in the new case. Fix the probe/fixture so it still measures the original requirement, supported by positive and negative cases. Do not remove failed business assertions or include optional source cleanup.

Add to the new manifest:

```json
{
  "round_kind": "verification",
  "parent_round": 1,
  "expected_candidate_sha256": "<SHA-256 of parent rounds/001/candidate.json file>"
}
```

CLI contract repair uses the optional task `contract_repair_command`; without it, the orchestrator stops with contract_revision_required. The command receives `{task_config}`, `{feedback_file}`, `{out_patch}`, `{round}` and `{case_root}` placeholders and writes a JSON patch. The case_root is the new case that the orchestrator will create; do not create it from the repair command. Preserve candidate source and HEAD.

To repair a frozen case-local probe, stage the corrected file outside both the worktree and new case. Return `case_inputs` as a mapping from new case-relative names to absolute staged files, and point the patched policy_files/check argv at the new case paths:

```json
{
  "case_inputs": {
    "inputs/probe.py": "/absolute/path/to/staged/corrected-probe.py"
  }
}
```

The orchestrator copies these inputs after creating the case and before issue. It rejects path escape, symlinks and overwriting generated files. External governance policies remain locked; do not modify a state/meta policy in place. For eligible repairs the orchestrator creates the linked verification manifest and skips the LLM executor.

A native supervisor likewise dispatches the controller's single execute with an executor-role token, without a coding agent. The full frozen check list runs again; there is **no cross-round check cache**. Parent candidate/HEAD checks at issue, execute and acceptance reject drift. A new audit round consumes the existing max_rounds budget; the old rejection remains intact. Requirement changes or mixed code/probe failures use a normal development round.

After independent acceptance, create a local commit with the final trailer:

```text
Accepted in supervised round <case> (review <review_hash_8>)
```

Integrate and report from actual records:

```sh
python3 "$DL/scripts/worktree.py" integrate --repo "$TARGET_REPO" --worktree "$WORK_REPO" --state-dir "$STATE"
python3 "$DL/scripts/summary.py" --repo "$TARGET_REPO" --from <first_new_commit> --to HEAD
```

Report actual agent calls, checks and elapsed time where recorded. Token usage remains unknown unless supplied by the backend; do not turn prompt size or demo results into a savings claim. Respect the existing round budget and user authorization for push/deployment.

## 6. Maintaining this framework

- Write the cleanup plan before edits; preserve behavior with relevant regression tests.
- Use only Python 3.10+ standard library and Git. Keep changes small and compatible.
- Preserve role checks, single execute per audit round, candidate sealing, process-group cleanup, hash chains and offline replay. Do not rewrite old archives.
- Run the full tests and demo before completion:

```sh
env -u PYTHONPYCACHEPREFIX -u PYTHONDONTWRITEBYTECODE python3 -m unittest discover -s tests -p "test_*.py" -v
env -u PYTHONPYCACHEPREFIX -u PYTHONDONTWRITEBYTECODE python3 examples/minimal-project/run_demo.py
```

The demo uses simulated agents to test orchestration. It does not establish real-model review quality or token efficiency. The controller is a [trusted-local workflow guard](references/architecture.md), not an OS sandbox.
