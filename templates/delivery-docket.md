---
id: s-xxxxxxxx
spec: <path of the spec>
created: YYYY-MM-DD HH:MM
session: <session id, first 8>
---

# <title>

Written once as a skeleton before the first implementer runs, then appended only.
A point changes state by flipping its box (`[ ]` → `[x]` delivered with evidence, `[~]` deviation)
and by a new line below; old lines are never edited.

## Points (pre-registered)

| # | Point | Evidence line expected | State |
|---|---|---|---|
| 1 | | `<command> → <expected>` | [ ] |

## Refinement

One line per finding: angle, finding, decision (adopted / rejected with reason / to the user as DISSENT).

- 

## Rewrite

`none`, or: date, reason, spec section changed, by the conceptor.

## Evidence

Appended as points are delivered: `point n: <command> → <result line>`, commit in words, CI state with the pushed hash.

- 

## Deviations

`[~]` points: what differs from the spec, why, and what the concept mode should weigh.

- 

## Reference update

For each file named in the spec: `updated <file>` or `not needed: <reason>`.

- 

## Inbox

Messages the user left during the run and what was done with them at the next checkpoint.

- 

## Terminal state

DOCKET DELIVERED | DOCKET BLOCKED <what only the user can resolve> | DOCKET DISSENT <two views, one sentence each with source>
