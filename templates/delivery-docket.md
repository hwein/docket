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

One line per finding: angle, finding, decision (adopted / rejected with reason / to the user as DISSENT). A line
written without its decision is closed by writing it again below, complete.

- 

## Rewrite

`none`, or: date, reason, spec section changed, by the conceptor.

## Evidence

Appended as points are delivered: `point n: <command> → <result line>`, commit in words, CI state with the pushed hash.

- 

## Deviations

`[~]` points: what differs from the spec, why, and what the concept mode should weigh. And what the run repaired
beyond the spec: `outside the order: <what was repaired, where, why>`.

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
A finding is fixed while a round is left; `deferred` is for round three and for a fix that changes what the spec
orders, and each deferred finding is a `decision:` line of the Terminal state.
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
language and everyday words, no ids, no hashes: `state:` DELIVERED, BLOCKED: what only the user can resolve in the
project (never a fault in this docket's own lines), or DISSENT; `result:` what works now, one sentence;
`repaired:` each thing the run repaired outside the order, one sentence; `user:` each step only the user can take,
doable from the line alone; `decision:` each proposal for work after the run (`decision: <what to do, and where,
a short phrase> | why: <what the user gains or risks> | outside: <what kept the run from doing it itself>`);
`watch:` each possible fault set to observe, the whole case in one line the user can cut a spec from (`watch:
<what it is about, a short sentence without a term to look up> | seen: … | suspected: … | impact: … | signal: …`);
`dissent:` each position, one sentence with its source; `open:` a note for the record, what was seen and left as
it is and why nobody has to act on it. The user gets back only what the run could not do: what it can settle it
settles before this section is written, and one matter stands once. The run's closing message is rendered from
these lines and shows them uncut, except the `open:` notes, which it neither shows nor counts: `result:` has at
most 200 characters, `user:` 300, `repaired:` 160, the first part of `decision:` 100 and its `why:` 200.

- 
