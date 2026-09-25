# T5 oracle authorship

Issue #40 was authored by Codex GPT-6 in this task, on branch
`codex/issue-40-t5-oracle`, from `origin/main` at
`a75f268533da866d2cd3517166928c7a7c8014f2` on 2026-09-25.

This task is separate from issue #39's Claude draft and Codex contract task
recorded in `t5/AUTHORSHIP.md`, and from issue #37's reference-engine author
recorded in `CANARY.md`. I did not read or unlock the T2 held-out suite.
The reference engine ran only through the existing `t5/bin/t5-sim` launcher.

I chose the 48 held-out uint32 seeds with `crypto.randomBytes`, rejecting
values in the visible 1–100 range. I captured each baseline snapshot from
seat 0 of a same-baseline match, selecting day 3 or the last available day.
No outcomes were inspected before choosing seeds or snapshots. The plaintext
pool and canary were removed after sealing.
