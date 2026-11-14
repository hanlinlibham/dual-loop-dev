# Dual-Loop Dev (`dual-loop-dev`)

[中文主文档](README.md) · [Architecture & Boundaries](references/architecture.md) · [Workflow Tiers](references/workflow-tiers.md) · [Failure Casebook](references/failure-casebook.md)

**A dual-loop supervised automated development framework and universal Agent Skill for AI coding agents.**

Single-loop coding agents ("edit -> run tests -> weaken assertions or hack around failures -> self-declare success") break down on multi-module refactors and long-horizon roadmaps. `dual-loop-dev` splits automated software engineering into two strictly separated loops and three distinct role identities—powered only by the **Python 3.10+ standard library and Git** (zero third-party dependencies):

1. **Inner Execution Loop (`executor`)**: Receives a frozen contract and an allowlist of writable paths, implements the change in one pass, and runs `execute` **at most once**. Failed checks never authorize silent self-repair or retries.
2. **Outer Governance Loop (`supervisor` + cold `reviewer`)**: Freezes contracts and baselines, dispatches a fresh independent reviewer to cold-read diffs and run `/tmp` mutation probes, attributes rejections (`code` vs. `contract` vs. `environment`), recycles within a monotonically non-increasing round budget, and orchestrates multi-task DAG roadmaps inside isolated Git worktrees.

---

## Key Capabilities

- **Cryptographically Separated Local Role Tokens**: `supervisor.key`, `executor.key`, and `reviewer.key` (`0600`) prevent role confusion, self-approval, and unauthorized retries.
- **Immutable Candidate Sealing & Process-Group Check Isolation**: Before check #1 runs, `controller.py` seals a SHA-256 source snapshot (`candidate.json`) and re-verifies it before and after *every* check—validation gates can never silently mutate or auto-format source code. Checks run in isolated POSIX process groups (`start_new_session=True`) so timeouts (`124`) and `SIGINT`/`SIGTERM` cleanly reap descendant processes.
- **Tamper-Evident Hash Ledger & Offline Replay**: Every transition is recorded in a SHA-256 hash chain (`events.jsonl`), and each `rounds/NNN/` archive stores self-contained copies of contracts, policies, `profile.json`, logs, review evidence, and the contract engine script—verifiable offline even if original inputs are later removed.
- **Built-in Zero-Dependency Contract Engine + TaskKit Compatibility**: Ships with `scripts/contract.py` (`init-case / freeze / check / compare / replay`) out of the box while remaining drop-in compatible with external [TaskKit](https://github.com/abuttoncc/agent-taskkit) (`taskkit_script`).
- **Automated Subagent Orchestrator & Unattended Roadmap Daemon**:
  - `orchestrator.py` dispatches `claude`, `codex`, or any custom command backend with scoped per-round worker tokens, auto-submits unexecuted candidates if an executor forgets to call `execute`, safely retries interrupted read-only reviewers, and routes `code / contract / environment` rejections.
  - `roadmap.py` runs multi-task DAG queues (`run` or `daemon` mode with `flock` PID lock and heartbeats) inside isolated Git worktrees + leased TCP ports, maintaining a live `progress.md` board and trailer-verified branch integration.
- **Universal Agent Skill (`SKILL.md`)**: Ready to drop into `~/.agents/skills/dual-loop-dev` or `~/.claude/skills/dual-loop-dev` for Claude Code, Cursor, Codex, and Antigravity.

---

## 30-Second Quickstart

Run the self-contained 2-task DAG demo (no LLM API keys required—simulates a Round 1 cold-review adversarial rejection despite passing unit tests, automatic Round 2 recycle & acceptance, and trailer-checked Git worktree integration):

```bash
git clone https://github.com/hanlinlibham/dual-loop-dev.git
cd dual-loop-dev

# Run the end-to-end demo
python3 examples/minimal-project/run_demo.py

# Run the full adversarial unit & integration test suite
python3 -m unittest discover -s tests -p "test_*.py" -v
```

---

## Installation

### As a CLI Tool

```bash
pip install -e .
dual-loop --help
```

### As an Agent Skill

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/hanlinlibham/dual-loop-dev.git ~/.agents/skills/dual-loop-dev
```

---

## Usage Modes

1. **Unattended Multi-Task Roadmap Runner / Daemon**:
   ```bash
   dual-loop roadmap run --roadmap roadmap.json --state-dir ../roadmap-state
   dual-loop roadmap daemon --roadmap roadmap.json --state-dir ../roadmap-state --poll-interval 15
   dual-loop worktree integrate --repo . --worktree <isolated_worktree_path>
   ```
2. **Single-Task Automated Outer-Loop Orchestrator**:
   ```bash
   dual-loop orchestrate --task-config task.json --state-dir ../task-state
   ```
3. **Granular State-Machine CLI** (`dual-loop controller init | issue | execute | review | recycle | status` + `dual-loop contract`).

---

## Security Boundary

`dual-loop-dev` is a **trusted-local workflow guard**, not an OS sandbox against hostile code running under the same user UID. A malicious script running as the same OS user can bypass the CLI or inspect parent directories. For untrusted workers, keep `control/`, `authority/`, and policies on the host outside the worker container/VM, and mount only a read-only brief and scoped writable worktree inside the worker.

---

## License

[MIT](LICENSE)
