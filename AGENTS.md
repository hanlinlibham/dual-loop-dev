# AGENTS.md — AI Agent Self-Bootstrap & Execution Guide (`dual-loop-dev`)

> **If a user pointed you to `https://github.com/hanlinlibham/dual-loop-dev` (or cloned this repository) and asked you to apply it to a codebase, read Sections 1–5 below. Everything you need to run the framework on the first try—without `pip install` or third-party packages—is in this file.**
>
> **若用户让你读取本仓库并用这套双层循环框架去开发/优化某个目标代码库，请直接按下方第 1–5 节执行；无需 `pip install`，克隆后仅凭 Python 3.10+ 标准库与 Git 即可直接运行。若你要修改本框架自身源码，见第 6 节。**

---

## 1. Mode Router / 先确认你的任务场景

- **Scenario A — Apply `dual-loop-dev` to a target repository (使用本框架推进目标仓库开发)**  
  Follow **Sections 2 → 3 → 4 → 5** below. Detailed tier/review methodology lives in [`SKILL.md`](SKILL.md), [`references/workflow-tiers.md`](references/workflow-tiers.md), and [`references/brief-templates.md`](references/brief-templates.md).
- **Scenario B — Maintain or extend `dual-loop-dev` itself (开发维护本框架自身)**  
  Jump to **Section 6**.

---

## 2. Zero-Install Cloud Bootstrap / 云端与新环境免安装挂载

`dual-loop-dev` uses **only the Python 3.10+ standard library and Git**. Do **NOT** clone it inside the target repository worktree (keeping framework files and state outside the target repo prevents polluting Git snapshots).

```bash
# 1. Clone dual-loop-dev outside your target repository
export DL="${DUAL_LOOP_HOME:-/tmp/dual-loop-dev}"
if [ ! -d "$DL/.git" ]; then
  git clone --depth 1 https://github.com/hanlinlibham/dual-loop-dev.git "$DL"
fi

# 2. Point TARGET_REPO to the codebase you are improving, and STATE outside TARGET_REPO
export TARGET_REPO="$(pwd)"
export STATE="/tmp/dual-loop-state/$(basename "$TARGET_REPO")"
mkdir -p "$STATE"
```

### Critical Path & Environment Rules (必守铁律，违反会直接报错拦截)
1. **External State Only**: `--root` (`control`), `--authority` (`authority`), and `case_root` **MUST** reside outside `TARGET_REPO` (never inside `TARGET_REPO` or any subdirectory of it).
2. **Absolute Paths in `manifest.json`**: `repo`, `case_root`, `profile`, and every entry in `policy_files` inside `manifest.json` **MUST** be absolute paths (`/...`). `allowed_paths` and `protected_paths` are `TARGET_REPO`-relative paths (use trailing `/` for directories, e.g. `"src/core/"`).
3. **Ignore Build/Bytecode Caches**: `controller.py` hashes `git ls-files --cached --others --exclude-standard` before and after every check. Before issuing Round 1, ensure `.gitignore` in `TARGET_REPO` ignores generated artifacts (`__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `node_modules/`, `dist/`, `.DS_Store`), or pass `-B` / `PYTHONDONTWRITEBYTECODE=1` to Python checks so checks never create un-ignored files.
4. **Create `profile.json` & Policy Anchor**: If `TARGET_REPO/profile.json` does not exist yet, create and commit it (or place `profile.json` and a baseline `POLICY.md` in `$STATE/meta/` with absolute paths in `manifest.json` if the repository must remain untouched before Round 1).

---

## 3. Choosing the Execution Mode for Your Runtime / 按当前 Agent 环境选择运行模式

| Your Runtime Capabilities | Recommended Mode | How to Run |
|---|---|---|
| **Can spawn subagents** (Claude Code `Task`/`Agent`, Cursor subagents, Codex multi-agent, Antigravity `invoke_subagent`) | **Mode A: Supervisor + Subagents** | You act as `supervisor`. Copy only `executor.key` to `$STATE/worker-tokens/` for an **Executor** subagent, then spawn a **fresh-context Cold Reviewer** subagent with only `reviewer.key`. See Section 4. |
| **Has `claude` or `codex` CLI in shell** (Cloud VM / night-run container) | **Mode B: Automated Orchestrator / Roadmap Daemon** | Write `task.json` or `roadmap.json` (see [`schemas/roadmap.schema.json`](schemas/roadmap.schema.json)) and run `python3 "$DL/scripts/orchestrator.py"` or `python3 "$DL/scripts/roadmap.py" run`. |
| **Single-session cloud sandbox** (Bash + Python + Git only; no subagent tool or nested CLI) | **Mode C: Single-Session Enforced Gate** | Still run the full `worktree.py` -> `contract.py` -> `controller.py` state machine to enforce path allowlists, single-attempt sealing, `/tmp` mutation probes, and review trailers. Perform the cold diff audit and `/tmp` counterexample probe explicitly before `review`, and set `"evidence_level": "single-session-verified"` in `observations.json`. |

---

## 4. Exact Step-by-Step CLI & JSON Recipe / 零猜测标准执行流与最小合法 JSON

### Step 1 — Read-Only Occupancy Preflight & Baseline Diagnosis
```bash
python3 "$DL/scripts/preflight.py" --repo "$TARGET_REPO" --workspace "$(dirname "$TARGET_REPO")"
```
Inspect architecture, run baseline tests/benchmarks read-only, and decompose the user's goal into 2–5 small staged packages (`L1` $\le 3$ files no behavior change; `L2` feature/bugfix; `L3` cross-module refactor/protocol change).

### Step 2 — Create an Isolated Git Worktree (Never Edit Primary Checkout Directly)
```bash
python3 "$DL/scripts/worktree.py" create \
  --repo "$TARGET_REPO" \
  --task-id "pkg-01" \
  --worktree "$STATE/wt-pkg-01" \
  --port-range 3200-3299 \
  --lock-file "$STATE/LOCK"
export WORK_REPO="$STATE/wt-pkg-01"
```

Ensure `$STATE/meta/profile.json` and `$STATE/meta/POLICY.md` exist (or use existing files in `$WORK_REPO`):
```json
{
  "schema_version": 1,
  "name": "target-service",
  "protected_paths": [".gitignore", "migrations/"],
  "require_clean_worktree": true
}
```

### Step 3 — Initialize Controller, Freeze Contract, and `issue` Round 1 (Supervisor)
```bash
export CTRL="$STATE/pkg-01/control"
export AUTH="$STATE/pkg-01/authority"
export CASE="$STATE/pkg-01/cases/r001"

python3 "$DL/scripts/controller.py" init --root "$CTRL" --authority "$AUTH"
```

Write `$STATE/pkg-01/criteria.json` (every row `requirement` MUST be `"required"` or `"not_applicable"`—`"investigate"` is rejected at `issue`):
```json
[
  {
    "id": "behavior-verified",
    "requirement": "required",
    "description": "Target behavior and edge cases work without regressions",
    "basis": "Repository specification and baseline diagnosis",
    "acceptance": "Automated checks exit 0 and cold /tmp counterexample probe fails when guard is removed"
  }
]
```

Freeze the prospective contract:
```bash
python3 "$DL/scripts/contract.py" init-case \
  --root "$CASE" \
  --topic "pkg-01" \
  --question "Deliver package 01 safely" \
  --criteria-json "$STATE/pkg-01/criteria.json"
python3 "$DL/scripts/contract.py" freeze --root "$CASE"
```

Write `$STATE/pkg-01/manifest-r001.json` (**all external file/dir references must be absolute paths**):
```json
{
  "repo": "/abs/path/to/wt-pkg-01",
  "case_root": "/abs/path/to/cases/r001",
  "profile": "/abs/path/to/profile.json",
  "policy_files": ["/abs/path/to/POLICY.md"],
  "allowed_paths": ["src/module.py", "tests/test_module.py"],
  "protected_paths": [],
  "checks": [
    {
      "id": "unit-tests",
      "argv": ["python3", "-B", "-m", "unittest", "discover", "-s", "tests"],
      "cwd": ".",
      "timeout": 300
    }
  ],
  "max_rounds": 3,
  "require_clean_worktree": true
}
```

Issue Round 1:
```bash
python3 "$DL/scripts/controller.py" issue \
  --root "$CTRL" \
  --token "$AUTH/supervisor.key" \
  --manifest "$STATE/pkg-01/manifest-r001.json"
```

### Step 4 — Inner Loop: Single-Attempt Implementation & `execute` (Executor)
Copy **only** `executor.key` to a scoped worker token file (`install -m 600 "$AUTH/executor.key" "$STATE/worker-tokens/r001-executor.key"`).
- Edit **only** `allowed_paths` inside `$WORK_REPO`.
- Do **NOT** run `git commit`, `git reset`, `git checkout`, `git stash`, or modify `$CTRL` / `$CASE`.
- Run local non-mutating self-checks, then run **`execute` at most once**:
```bash
python3 "$DL/scripts/controller.py" execute \
  --root "$CTRL" \
  --token "$STATE/worker-tokens/r001-executor.key"
```
Stop immediately after `execute` (whether exit code is `0` or `1`). Never modify any file in `$WORK_REPO` after `execute` seals `candidate.json`.

### Step 5 — Outer Loop: Cold Adversarial Review & Verdict (Reviewer)
Use a **fresh-context** Reviewer (copying only `reviewer.key` to `$STATE/worker-tokens/r001-reviewer.key`):
1. Do **NOT** modify any file in `$WORK_REPO`.
2. Inspect `git -C "$WORK_REPO" diff HEAD`, `git -C "$WORK_REPO" status --porcelain`, and `$CTRL/rounds/001/execution.json` + check `.log` files.
3. Copy relevant files to `/tmp` and run a **counterexample / mutation probe** (remove the new guard or feed adversarial inputs in `/tmp` to confirm tests genuinely catch regressions).
4. Write evidence under `$CASE/outputs/review-evidence.md`. Compute the SHA-256 of the frozen baseline file (`BASELINE_SHA=$(python3 -c 'import hashlib,json,pathlib; c=pathlib.Path("'"$CASE"'"); s=json.loads((c/"spec.json").read_text()); print(hashlib.sha256((c/s["baseline"]).read_bytes()).hexdigest())')`), generate the current UTC timestamp (`NOW_UTC=$(python3 -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())')` — **must be $\ge$ `frozen_at`**), and write `$CASE/outputs/observations.json`:
```json
{
  "baseline_sha256": "<64-hex-sha256-of-frozen-baseline-file>",
  "observed_at": "<current-utc-iso8601-timestamp-after-freeze>",
  "rows": [
    {
      "id": "behavior-verified",
      "status": "PASS",
      "detail": "Diff confined to allowed_paths; unit tests passed; /tmp mutation probe confirmed failure without guard",
      "evidence_level": "independent-review",
      "evidence": ["outputs/review-evidence.md"]
    }
  ]
}
```
5. Reconcile observations and submit verdict (`--report` is **relative to `$CASE`**):
```bash
python3 "$DL/scripts/contract.py" compare \
  --root "$CASE" \
  --observations "outputs/observations.json" \
  --out "outputs/review-comparison.json"

# To ACCEPT (requires execution.json passed==true and all required rows PASS):
python3 "$DL/scripts/controller.py" review \
  --root "$CTRL" \
  --token "$STATE/worker-tokens/r001-reviewer.key" \
  --verdict accept \
  --feedback "$CASE/outputs/feedback.md" \
  --report "outputs/review-comparison.json"

# Or to REJECT (include ATTRIBUTION: code | contract | environment in feedback.md):
python3 "$DL/scripts/controller.py" review \
  --root "$CTRL" \
  --token "$STATE/worker-tokens/r001-reviewer.key" \
  --verdict reject \
  --feedback "$CASE/outputs/feedback.md"
```

### Step 6 — Recycle on Rejection OR Trailer Commit & Integration on Acceptance (Supervisor)
- **If `rejected`**: Read `$CTRL/rounds/001/feedback.md` and `review_hash` from `controller.py status --root "$CTRL"`:
  - `ATTRIBUTION: code`: Keep acceptance standards unchanged, initialize a **new** case directory `$STATE/pkg-01/cases/r002` (chaining `previous_baseline` if revising), freeze it, and run:
    ```bash
    python3 "$DL/scripts/controller.py" recycle \
      --root "$CTRL" \
      --token "$AUTH/supervisor.key" \
      --manifest "$STATE/pkg-01/manifest-r002.json" \
      --from-review "$REVIEW_HASH" \
      --change-reason "Round 2 fix for code defect identified in review"
    ```
  - `ATTRIBUTION: contract`: Keep candidate code untouched byte-for-byte; fix only contract/allowlist in `r002` and `recycle`.
  - Stop and report to the user if `max_rounds` is exhausted.
- **If `accepted`**: Commit inside `$WORK_REPO` with the mandatory audit trailer (`worktree.py integrate` rejects commits lacking this trailer!):
  ```bash
  REVIEW_HASH8=$(python3 "$DL/scripts/controller.py" status --root "$CTRL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["review_hash"][:8])')
  git -C "$WORK_REPO" add -- <allowed_changed_files>
  git -C "$WORK_REPO" commit -m "$(printf 'Complete pkg-01\n\nAccepted in supervised round pkg-01-r1 (review %s)' "$REVIEW_HASH8")"
  ```
- Repeat Steps 3–6 for subsequent staged packages in `$WORK_REPO`. Once all packages pass:
  ```bash
  # Verify every commit carries a valid supervised-round trailer + matches sealed candidate.json, then fast-forward merge
  python3 "$DL/scripts/worktree.py" integrate --repo "$TARGET_REPO" --worktree "$WORK_REPO" --state-dir "$STATE"
  python3 "$DL/scripts/summary.py" --repo "$TARGET_REPO" --from <first_new_commit> --to HEAD
  python3 "$DL/scripts/worktree.py" cleanup --repo "$TARGET_REPO" --worktree "$WORK_REPO" --delete-branch --lock-file "$STATE/LOCK"
  ```
  Do **NOT** run `git push` or production deployment unless the user explicitly requested it.

---

## 5. Automated Multi-Task DAG Alternative (`roadmap.py`)

When running in an environment with `claude`, `codex`, or a custom worker script (`"kind": "command"`), you can skip manual per-round wiring by generating a `roadmap.json` validated against [`schemas/roadmap.schema.json`](schemas/roadmap.schema.json) and running:

```bash
python3 "$DL/scripts/roadmap.py" run --roadmap "$STATE/roadmap.json" --state-dir "$STATE/run"
```
See [`examples/minimal-project/run_demo.py`](examples/minimal-project/run_demo.py) for a complete, runnable 2-task reference implementation.

---

## 6. Contributing to `dual-loop-dev` Itself

If your task is modifying this repository (`dual-loop-dev`):
1. **Zero external dependencies**: Use only the Python 3.10+ standard library and `git`.
2. **Preserve core invariants**: Role token separation, single `execute` per round, pre/post-check SHA-256 `candidate.json` verification, process-group timeout cleanup, hash-chained `events.jsonl`, and offline-replayable `rounds/NNN/` archives.
3. **Required verification before finishing**:
   ```bash
   env -u PYTHONPYCACHEPREFIX -u PYTHONDONTWRITEBYTECODE python3 -m unittest discover -s tests -p "test_*.py" -v
   env -u PYTHONPYCACHEPREFIX -u PYTHONDONTWRITEBYTECODE python3 examples/minimal-project/run_demo.py
   ```
