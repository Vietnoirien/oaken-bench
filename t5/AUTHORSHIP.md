# T5 contract authorship

Issue #39, contract `t5/1`, completed 2026-09-25.

The initial simulator and baseline drafts came from the stopped Claude
worktree `agent-a5d12b1018efc0cf0`, branch `issue-39-t5-contract`, at
`fd4e90d`. Its ten untracked TypeScript/module files were recovered. That
commit merges `issue-37-refengine` at `56a5820` and
`issue-36-tier-namespaces` at `2db6f62`.

Codex, GPT-6, task `01a0d7fe-c4bb-75b0-990c-c17667872cfb`, completed the
contract, simulator corrections, launcher, tests and visible measurements.
Work was developed in `/tmp/oaken-issue-39-codex` to avoid concurrent checkout
changes, then delivered as `t5/` files for the supervisor to isolate and commit.

Both the original Claude draft author and this Codex task are contract
authors. Neither may author the T5 held-out seeds, snapshot pool or oracle.
Assign issue #40 to a separate context that has not inspected held-out tests.
Freeze and verify `t5/FROZEN.sha256` before that work begins.

This Codex task did not read or run the held-out suite. It executed the sealed
reference engine in disposable workspaces against visible tests, without
printing or copying its source into deliverable files. The reference engine's
own author remains subject to the separate exclusion recorded in CANARY.md.
