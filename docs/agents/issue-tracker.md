# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues on
[Vietnoirien/oaken-bench](https://github.com/Vietnoirien/oaken-bench). Use the `gh` CLI
for all operations.

## Read this first: bare `gh issue view` does not work here

```
$ gh issue view 9
GraphQL: Projects (classic) is being deprecated in favor of the new Projects
experience, see: https://github.blog/changelog/2024-05-23-sunset-notice-projects-classic/.
(repository.issue.projectCards)
```

The human-readable `gh issue view` asks for `projectCards` to render its output, and
GitHub no longer answers that field. This box ships `gh 2.46.0` from Debian; newer `gh`
releases dropped the field. Upgrading `gh` fixes it properly -- until then, work around
it.

**Exactly what breaks.** Measured, not assumed:

| command | works |
|---|---|
| `gh issue view 9` | **no** |
| `gh issue view 9 --comments` | **no** |
| `gh issue view 9 --json body` | yes |
| `gh issue list` | yes |
| `gh issue list --json number,title,body,labels,comments` | yes |
| `gh api repos/{owner}/{repo}/issues/9` | yes |
| `gh issue create` / `comment` / `edit` / `close` | yes |

So it is only the *rendered* single-issue view. Adding `--json` takes a different code
path and is fine, and every list, write and `gh api` call is unaffected.

**Use either of these to read one issue:**

```bash
# simplest: the same command with --json
gh issue view 9 --json title,body,labels,comments --jq '.title + "\n\n" + .body'

# or the REST API
gh api repos/{owner}/{repo}/issues/9 --jq '.title + "\n\n" + .body'
gh api repos/{owner}/{repo}/issues/9/comments --jq '.[].body'
```

Note that the REST issues endpoint returns pull requests alongside issues, so filter
with `select(.pull_request | not)` when listing through it.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --json title,body,labels,comments`. The
  bare `gh issue view <number>` fails on this box -- see above.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters. This works as written.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve with `gh pr view 42` and fall back to `gh issue view 42 --json title,body`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --json title,body,labels,comments` -- with `--json`, not
without it.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, _not_ the `#number` or `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.

## House style for issue bodies

Existing issues (#1-#12) set the convention, and new ones should match it:

- Lead with **what is wrong or missing**, not with the proposed fix.
- Back the claim with **measured evidence** -- a table of real numbers, a quoted code
  fragment, a named run under `results/`. An issue that only describes the code is
  weaker than one that shows what the code produced.
- Say what **cannot be recovered**. Several issues turn on data that no longer exists
  (see #7); being explicit about that is what makes the sequencing arguments legible.
- Put a **`## Caveat`** or equivalent at the end when a fix would change the meaning of
  an already-published field or number. This repo publishes `results/*/score.json` and
  `results/*/events-summary.json`, and silently redefining a field in them is worse than
  leaving it wrong and documented.

Before filing anything about `harnessMetrics`, read `EVENTS.md` -- it documents both
harness event schemas and already records three known-wrong fields (#9, #10, #11).
