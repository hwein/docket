---
id: s-xxxxxxxx
spec: <path of the spec>
created: YYYY-MM-DD HH:MM
session: <session id, first 8>
---

# <title>

Written by the orchestrator: the points with their acceptance lines before the first code, the rest as the run
goes. A point changes state by its box: `[ ]` open, `[x]` delivered with its evidence, `[~]` deviation.

## Points

The acceptance line is the literal command in backticks, run by the orchestrator, exit 0 on success, then the arrow
and what it shows. A criterion in prose: the orchestrator checks it and writes the evidence.

| # | Point | Acceptance line | State |
|---|---|---|---|
| 1 | | `<command>` → <expected> | [ ] |

## Preconditions

One line per precondition of the spec, run as the first step: `<command>` → <output> → met / not met.

- 

## Evidence

`start: <model>, <reason>` before the first code; `point n: <command> → <result line>`, the commit in words;
`round n: <model>, <findings>` for each round; `CI: <state> <hash>` after the push.

- 

## Deviations

`[~]` lines: what differs from the spec, or what only the user can decide or provide, and why; a command from
`forbidden_commands` that was not run; an instruction of the user in the chat. A red run of a check or acceptance
command, also one a repeat turned green: the test's title, its file, expected and received, the command and its start
time, whether the cause is known, and the docket of an earlier spec (by its title) where the same test stood.

- 

## Review

One line per finding: `<angle> | <finding> | <source> | <severity> | <decision>`, the angle `code`, `spec` or
`recheck`, the decision fixed or rejected with its source (`bug`, `unmet`, `rule`), or
`reported, fault: yes|no|unclear, <recommendation>` (every other severity; the recommendation do it, drop it or
measure first: how); an angle without findings is `<angle> | no findings`.

- 

## Reference update

For each file named in the spec: `updated <file>` or `<file>: not needed, <reason>`.

- 

## Terminal state

Written when the spec ends, in the user's language: `state: DELIVERED` or `state: BLOCKED: <reason>` (after three
rounds: the failed command and its output); `result:` what works now, one sentence; `user:` each step only the user
can take; `decision:` each proposal for later, with why and a recommendation (do it, drop it, measure first: how).
A resumed spec gets its new `state:` line below the old block.

- 
