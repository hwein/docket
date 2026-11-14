---
id: c-xxxxxxxx
topic: <topic>
title: <title>
state: draft          # draft | agreed | superseded
source:               # empty, or the project's concept document: path from the repo root (../ allowed) or absolute
created: YYYY-MM-DD
---

# <Title>

A concept says what SHOULD be and why, for a feature too large for one spec. It is not documentation:
the current state is cited from the reference docs. Specs derived from it carry `concept: c-xxxxxxxx`.
With `source` set, the project's own document holds the content and this file only links it: Intent, Principles
and constraints, Shape stay empty; the file keeps the state, the open decisions and the specs derived. A change of
the content is written into the source, in its format. The state stands here, never in the source, so the source
keeps the project's format.

## Intent

## Principles and constraints

## Shape (what the user or operator will notice)

## Open decisions (user)

| Question | Recommendation | Answer (date) |
|---|---|---|

## Specs derived

| Spec | Title | State |
|---|---|---|
