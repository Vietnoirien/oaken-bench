# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Which of these exist on the repo today

Only `wontfix` does. The repo currently carries GitHub's stock set --
`accessibility`, `bug`, `documentation`, `duplicate`, `enhancement`,
`good first issue`, `help wanted`, `invalid`, `question`, `wontfix` -- so the four
triage-specific labels have to be created before `/triage` can apply them:

```bash
gh label create needs-triage    --description "Maintainer needs to evaluate this issue" --color FBCA04
gh label create needs-info      --description "Waiting on reporter for more information" --color D4C5F9
gh label create ready-for-agent --description "Fully specified, ready for an AFK agent" --color 0E8A16
gh label create ready-for-human --description "Requires human implementation"           --color 1D76DB
```

`bug` and `enhancement` are orthogonal to these five and stay as they are: issues
#1-#8 are `enhancement`, #9-#12 are `bug`, and a triage label sits alongside rather
than replacing that.
