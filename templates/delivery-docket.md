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

## Tiers

One line per point, written with the skeleton: `point <n>: simple / standard / complex, <reason>`. The tier picks
the implementer's model.

- 

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

## Triage

One line per failure that gets a second attempt, written before that attempt from the diagnoser's answer:
`point <n> | cause: <file:line, or unknown after …> | repro: <command> → <n> of <m> | impact: <who notices what, when> | <decision>`
with the decision fix / deviation / `observe: <the signal that would show it again>`. `observe` stands only after
three runs of the repro command and a second diagnoser's challenge.

- 

## Review

One line per finding of the reviewers, who read the whole diff before the push from two angles (`code`, `spec`)
and every fix after it (`recheck`): `<angle> | <finding> | <source> | <severity> | <decision>` with the decision
fixed / rejected with the source that refutes it / deferred; an angle without findings is `<angle> | no findings`.
A `bug`, `unmet` or `rule` finding is never deferred into a delivery. A `suspect` is triaged: it becomes a `bug`,
is rejected, or ends `… | suspect | cause: … | repro: … | impact: … | observe: <signal>`.

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
`watch:` each possible fault set to observe, the whole case in one line the user can cut a spec from
(`watch: <what it is about> | seen: … | suspected: … | impact: … | signal: …`); `user:` each step only the user can
take; `decision:` a question with its recommendation and the command that
carries it out; `dissent:` each position, one sentence with its source. The run's closing message is rendered from
these lines.

- 
