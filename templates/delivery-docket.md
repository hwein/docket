---
id: s-xxxxxxxx
spec: <path of the spec>
created: YYYY-MM-DD HH:MM
session: <session id, first 8>
---

# <title>

Written once as a skeleton by the orchestrator before the first implementer runs, then appended only.
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

## Review

One line per finding of the reviewer, who read the whole diff before the push: the finding, its source and the
decision (fixed / rejected with reason / deferred); or the one line `no findings`.

- 

## Reference update

For each file named in the spec: `updated <file>` or `<file>: not needed, <reason>`.

- 

## Inbox

Messages the user left during the run and what was done with them at the next checkpoint.

- 

## Terminal state

Written by the orchestrator when the spec ends; then the turn ends without text. One line each, in the user's
language and everyday words, no ids, no hashes: `state:` DELIVERED, BLOCKED: what only the user can resolve, or
DISSENT; `result:` what works now, one sentence; `open:` each thing left open (as many lines as there are);
`user:` each step only the user can take; `decision:` a question with its recommendation and the command that
carries it out; `dissent:` each position, one sentence with its source. The run's closing message is rendered from
these lines.

- 
