# CLAUDE.md

Read [AGENTS.md](AGENTS.md) for the single command/configuration entry point and [SKILL.md](SKILL.md) for workflow rules.

- When applying the framework, keep state outside the target repository, use targeted development checks, submit once with execute, then obtain independent review of the diff, sealed receipts and relevant risks.
- First review is cold; one scoped follow-up by the same reviewer is allowed when the native host supports it. Changed goals/scope or public-contract/concurrency/security assumptions require fresh review. A single session must not claim independent acceptance.
- Pure probe/fixture repairs may use a linked verification round without another LLM executor. It still runs all checks, consumes budget and keeps the rejected parent record.
- When maintaining this repository, preserve the core invariants, use no external dependencies, and run the tests and demo listed in AGENTS.md.
