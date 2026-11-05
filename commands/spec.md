---
description: "Spec mode: turn an agreed concept or request into a spec with measurable done criteria, close user decisions, set it to ready."
argument-hint: "[concept id | spec id | title]"
---

# Spec mode

Argument: `$ARGUMENTS`

You bring one spec to `ready`. Semi-autonomous: everything technical is the team's decision and you take it;
only what users or operators would notice goes to the user, one question at a time, answerable with one word,
with a recommendation.

## Start

1. Required reading is enforced by the hook.
2. Resolve the argument: a concept id (derive the next spec from it), a spec id in `draft` (continue it), or a
   title (new spec). Topic from DOCKET.md; if the topic is new, that is one user question.
3. Create the id: eight hex digits of SHA-256 over `topic|title|<fetched ISO time>`, prefix `s-`.
   File: `specs/<topic>/s-<id>-<slug>.md` from `${CLAUDE_PLUGIN_ROOT}/templates/spec.md`.

## Writing the spec

- Goal in one paragraph: what users or operators get.
- Points: each with a done criterion that a hook or the check script can run. Write the mechanism
  (`<command> → <expected result>`, or `<file> contains <x>`), never the outcome in prose. A point without a
  verifiable criterion does not pass the ready gate.
- Reference update: name every reference file that changes, or `none` with the reason. The spec is not the
  documentation; the reference docs are.
- Technical decisions: your decisions, with the source that led to them (code location, project rule, previous spec).
- Dependencies: `follows:` for rework of an earlier spec; `after: <id>` in the goal paragraph when another spec must
  land first.
- Cite the current state from the reference docs by file; do not restate it.

## User decisions

Collect what users or operators would notice. Ask one question per message, in everyday words, with effect and
recommendation; record the answer with date in the spec. Nothing already decided in DOCKET.md, CONTRIBUTING.md
or an earlier ready spec is asked again.

## Ready gate

Set `state: ready` only when: every point has a verifiable criterion, the reference-update section is filled,
no user decision is open, and the goal cites no hypothetical state. From then on the file is frozen; the hook
enforces it. Report in one sentence: the spec title is ready, and the one thing the user has to decide, if any.
