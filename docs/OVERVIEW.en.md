# dual-loop-dev: supervised delivery with bounded repair

[Repository](https://github.com/hanlinlibham/dual-loop-dev) · [English README](../README.en.md) · [Execution guide](../AGENTS.md) · [中文](../README.md)

dual-loop-dev combines a Python standard-library controller with an agent workflow. Its purpose is to keep a coding task's objective, candidate and acceptance evidence identifiable while allowing bounded repair after independent review.

## What the two loops do

The executor implements within declared paths, performs targeted development checks, and calls execute once. The controller seals the source and runs the frozen checks. The outer loop verifies the diff and receipts, adds evidence for relevant risks, and accepts or rejects the candidate.

A rejection records the violated criterion, trigger, impact and required follow-up. The supervisor distinguishes code, contract and environment problems before another round. This can prevent rewriting valid code to satisfy a broken probe.

Tests are one input to this process. There is no objective-function optimizer or guarantee that every round improves the result. The stopping conditions are acceptance, an unresolved blocker, or budget exhaustion.

## Reduce repeated work while preserving judgment

- Maintain one task definition and one observations result. Generate manifests, briefs, feedback and comparisons instead of hand-writing equivalent documents.
- Let execute run the formal frozen checks. An independent reviewer verifies sealed receipts and reruns relevant checks only for evidence gaps, unclear environments, unstable checks, candidate mismatch or new risks.
- Verify what the change can break: real-host UI actions, file/data invariants, model-facing contracts, or concurrency boundaries. Mutation probes test important guards without a quota.
- Use a cold first review. A native host may allow one focused follow-up by the original reviewer; changed objectives/scope or public-contract/concurrency/security assumptions, repeated failure, or a discovered earlier miss require a fresh reviewer.
- CLI workers remain separate processes. Focused follow-up context does not provide cross-backend session resumption. A host without an independent reviewer cannot claim independent acceptance.

## Linked verification rounds

A pure probe or fixture defect may use a verification round only after independent confirmation: the parent feedback identifies contract attribution and verification-only eligibility, there are no code blockers, and the complete candidate, HEAD, objective, criteria and scope remain unchanged.

The new manifest links its parent and the SHA-256 of the parent's candidate.json file. The orchestrator skips the LLM executor; the controller still executes the entire frozen checklist once and requires independent review. Candidate/HEAD drift at issue, execution or acceptance rejects this path. The round consumes the existing budget and leaves the previous rejection intact.

This release does not cache checks across rounds. Changed requirements and mixed code/probe failures use normal development rounds. A repaired probe must still test the original requirement; deleting failed assertions is not a validation repair.

## Enforced mechanics and their limits

| Mechanism | What it establishes |
|---|---|
| Role-specific command authentication | Which authorized workflow role invoked an operation |
| Frozen contract and policy hashes | Issued inputs were not silently altered |
| Candidate sealing and boundary checks | Checks and acceptance refer to the same allowed source tree |
| Single execute and max_rounds | Attempts and retry limits remain explicit |
| Process-group cleanup | Timed-out checks do not leave ordinary descendant processes running |
| Hash chains and archived contract engines | Historical evidence can be checked and replayed offline |
| Worktree and ledger-verified integration | Concurrent work is isolated and merged source matches an accepted candidate |

These controls cannot prove that two role tokens were held by independent agents or that a review's evidence supports its conclusion. Reviewers remain responsible for the semantic judgment. The observations shortcut generates artifacts; it never supplies PASS on the reviewer's behalf.

The framework is a trusted-local workflow guard, not an OS sandbox. Untrusted workers need an external container/VM boundary with control, authority and policies kept outside their writable environment. Details are in [architecture.md](../references/architecture.md).

## Use and evaluation

Start with [AGENTS.md](../AGENTS.md), the shared command/configuration entry point for native subagents, single-task CLI orchestration and dependent roadmap queues. Python 3.10+ and Git are sufficient. Legacy feedback/report submissions and external TaskKit interfaces remain supported, and old archives are preserved.

The test suite and simulated-agent demo verify state-machine and orchestration behavior. They do not measure real-model review quality, convergence success, or token savings. Runtime reports should count actual agent calls, checks and elapsed time; token usage is unknown unless supplied by the backend.

Rule-evolution guidance is a documented proposal/review process, not an automatic learning engine. See [rule-evolution.md](../references/rule-evolution.md).
