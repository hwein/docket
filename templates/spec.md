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
Once `ready`, this file is frozen: findings become deviation lines in the docket or a new spec;
a rewrite is a visible event (section "Rewrite") by the conceptor in rewrite mode.

## Goal (WHAT)

One paragraph: what users or operators get, and why now.

## Out of scope

- 

## Points

Each point has a done criterion that a hook or the project's check commands can verify: a command and its expected
result, or a file and what it must contain. A criterion describes the mechanism of the check, never the
outcome in prose.

| # | Point | Done criterion (command → expected) |
|---|---|---|
| 1 | | |

## Preconditions (user only)

What only the user can provide before the run (a key, a running server, an account), one line each:
`<command>` → `<expected output>`. `/docket:implement` runs them before it seals the session and names the first
one that fails. `none` when there is nothing.

- none

## Reference update

Which reference docs change because of this spec (file → what), or `none` with the reason.
The docket records whether each was updated.

| File | Change |
|---|---|

## Technical decisions (team)

Filled by the team during spec and refinement; the user does not decide these.

- 

## User decisions

Questions that users or operators would notice (behaviour, compatibility, defaults, data migration,
platform requirements, scope and order). Each answerable with one word, each with a recommendation.
Closed before `ready`.

| Question | Recommendation | Answer (date) |
|---|---|---|

## Rewrite

Only in rewrite mode, by the conceptor: date, reason, what changed.
