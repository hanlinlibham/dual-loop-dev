# Dual-Loop Dev

[中文](README.md) · [Agent execution guide](AGENTS.md) · [Skill](SKILL.md) · [Technical overview](docs/OVERVIEW.en.md) · [Engineering cases](references/failure-casebook.md)

dual-loop-dev provides a supervised delivery loop for AI coding tasks: freeze the objective and scope, submit a candidate, obtain an independent evidence-based verdict, and address blocking findings within a fixed round budget.

It addresses shifting acceptance criteria, self-review blind spots, unnecessary code rewrites caused by broken validation tools, and shared-worktree conflicts. Automated tests supply evidence; the framework does not guarantee steady improvement or eventual success.

## Default workflow

```text
One task → frozen contract → targeted development checks → execute and seal
                                                              ↓
            bounded, focused repair ← independent review → accept
```

- **Run formal checks once; review for additional information.** The executor uses targeted checks during development. The controller runs the formal frozen list. The reviewer checks valid sealed receipts and reruns relevant checks only when evidence, environment or a new risk warrants it.
- **Verify actual risks independently.** Exercise important UI actions in the real host, use boundary inputs for data/file operations, and compare model-facing return values with tool instructions. Mutation probes target important guards without a fixed quota.
- **Maintain one review result.** Observations contain actual findings and evidence. `review --observations` generates compatible feedback/comparison artifacts; it does not fill in PASS.
- **Allow one focused follow-up.** The first review is cold. A native host may reuse that reviewer once for a scoped fix. Changed objectives/scope, new public-contract/concurrency/security assumptions, repeated failure or a revealed missed defect require a fresh reviewer. CLI calls still start new processes.
- **Skip the coding agent for pure validation repairs.** An independently confirmed probe/fixture defect can use a linked verification round with the same entire candidate and acceptance target. All checks run again, independent review remains required, and the round consumes the existing budget. The rejected parent stays intact. There is no cross-round check cache.

## Enforced mechanics

Role-specific commands, writable/protected paths, one execute per round, source hashes before and after checks, process-group cleanup on timeout, a round budget that cannot be silently increased, hash-chained events, and self-contained offline replay.

Worktree tools provide repository isolation and port leases. Ledger-verified integration checks that committed source matches an accepted candidate. Review quality still depends on judgment: intact evidence files and PASS fields alone cannot establish correct behavior. Hosts without an independent reviewer must leave the candidate awaiting review.

## Get started

Python 3.10+ and Git are sufficient. No third-party packages are required:

```sh
git clone https://github.com/hanlinlibham/dual-loop-dev.git
cd dual-loop-dev
python3 examples/minimal-project/run_demo.py
python3 -m unittest discover -s tests -p "test_*.py" -v
```

The demo uses simulated agents to exercise rejection, recycling, acceptance and integration. It is not a controlled evaluation of real-model convergence or token savings.

Run scripts directly, or install the CLI:

```sh
pip install -e .
dual-loop --help
```

To use the repository as a Skill, install it in your host's skill directory. Keep the framework and control/authority/case state outside the target worktree.

A concise prompt for a host with independent subagents or CLI workers:

> Read dual-loop-dev's AGENTS.md and SKILL.md and apply the supported mode to my objective. Maintain one task definition and verify its actual risks. Preserve independent acceptance, sealed candidates and the round budget while avoiding routine duplicate full-suite runs and duplicate reports. Complete the authorized scope and report measured results and costs.

**[AGENTS.md](AGENTS.md)** is the single source for task/profile examples, native-host and CLI commands, observations, verification-round conditions, commits and integration. Use the orchestrator for one task or the roadmap runner for dependent packages. Backends are claude, codex or a custom command.

## Scope, compatibility and limits

Use the framework for complex features, cross-module changes, state/concurrency work and unattended queues. Read-only questions and low-risk micro-edits do not need it. Choose L1/L2/L3 by actual impact; file counts and Markdown extensions do not establish low risk.

The legacy `review --feedback ... --report ...` interface, configurations without new fields, and external [TaskKit](https://github.com/abuttoncc/agent-taskkit) interfaces remain supported. Old archives are not rewritten. Upgrade workflows between tasks rather than during an active round.

Report actual agent calls, checks and elapsed time. Without genuine backend usage, tokens are unknown; do not claim an unmeasured savings percentage. max_rounds bounds retries rather than guaranteeing success.

This is a trusted-local workflow guard, not an OS sandbox against hostile code under the same UID. See [architecture and boundaries](references/architecture.md), [risk-based verification](references/workflow-tiers.md), and [rule proposal guidance](references/rule-evolution.md).

[MIT License](LICENSE)
