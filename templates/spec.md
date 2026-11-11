---
id: s-xxxxxxxx
topic: <topic>
title: <title>
state: draft          # draft | ready | in-progress | done | superseded
concept: <c-xxxxxxxx or empty>
follows: <s-xxxxxxxx of the spec this one reworks, or empty>
created: YYYY-MM-DD
---

# <Title>

A spec says what SHOULD be. The current state is cited from the reference docs, never restated here.
What the run finds against a ready spec becomes a deviation line in its docket or a new spec.

## Goal (WHAT)

One paragraph: what users or operators get, and why now.

## Out of scope

- 

## Points

Each point has a done criterion that the run or the project's check commands can verify: a command in backticks
and its expected result, or a file and what it must contain. A criterion describes the mechanism of the check,
never the outcome in prose.

| # | Point | Done criterion (command → expected) |
|---|---|---|
| 1 | | |

## Preconditions (user only)

What only the user can provide before the run (a key, a running server, an account), one line each:
`<command>` → `<expected output>`. A line in PowerShell syntax runs in PowerShell, any other line in Bash.
`/docket:spec` runs each line once in its Ready check; the run runs them as the first step of the spec, and a spec
whose precondition is not met ends there. What the run can make itself is a point, not a precondition.
`none` when there is nothing.

- none

## Reference update

Which reference docs change because of this spec (file → what), or `none` with the reason.
The docket records whether each was updated.

| File | Change |
|---|---|

## Technical decisions (team)

Filled by the team while writing the spec and in the Ready check; the user does not decide these.

- 

## User decisions

Questions that users or operators would notice (behaviour, compatibility, defaults, data migration,
platform requirements, scope and order). Each answerable with one word, each with a recommendation.
Closed before `ready`.

| Question | Recommendation | Answer (date) |
|---|---|---|

## Ready check

Written by `/docket:spec` before `state: ready`: one line per refiner angle with its findings, one line per
precondition and acceptance command with the result of its dry run and an excerpt of the output, the findings
that stay open, the date.

- date: YYYY-MM-DD
- feasibility: <findings, or none>
- spec: <findings, or none>
- <third_refiner_angle>: <findings, or none>
- `<command>` → <result of the dry run> (<output excerpt>)
- open: <a finding that stays open, or none>
