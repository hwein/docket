---
description: "Spec mode: turn an agreed concept or request into a spec with measurable done criteria, close user decisions, check it with three refiners and a dry run of its commands, set it to ready."
argument-hint: "[concept id | spec id | title]"
---

# Spec mode

Argument: `$ARGUMENTS`

You bring one spec to `ready`, or each spec of the order in turn when it names several (legacy specs to rework,
for example). Semi-autonomous: everything technical is the team's decision and you take it; only what users or
operators would notice goes to the user, one question at a time, answerable with one word, with a recommendation.
A status flag on an old document (a legacy spec, an index line) points at its new spec, and is set only once that
spec is `ready`.

## Start

1. Resolve the argument: a concept id (derive the next spec from it), a spec id in `draft` (continue it), or a
   title (new spec). Follow-up work from a finished docket the user names by its title or id: read that docket.
   Topic from DOCKET.md; if the topic is new, that is one user question.
2. Create the id: eight hex digits of SHA-256 over `topic|title|<fetched ISO time>`, prefix `s-`.
   File: `specs/<topic>/s-<id>-<slug>.md` from `${CLAUDE_PLUGIN_ROOT}/templates/spec.md`.

## Writing the spec

- Goal in one paragraph: what users or operators get.
- Points: each with a done criterion that docket's run or the project's check commands can run. Write the mechanism
  (`` `<command>` → <expected result> ``, or `<file> contains <x>`), not the outcome in prose: the run checks only a
  command in backticks before the arrow at `state: DELIVERED`.
- What the run can make itself (create a file, install a dependency, start a service of the project) is a point.
  A precondition is only what the user alone can provide (a key, a running server, an account): one line in the
  `Preconditions (user only)` section, in PowerShell or Bash syntax.
- Reference update: name every reference file that changes, or `none` with the reason. The spec is not the
  documentation; the reference docs are.
- Technical decisions: your decisions, with the source that led to them (code location, project rule, previous spec).
- Dependencies: `follows:` for rework of an earlier spec; `after: <id>` in the goal paragraph when another spec must
  land first, once per such spec. docket reads both: the order of implementation, the pane, and the start of a run,
  which does not mark a spec whose predecessor is not done. A dependency written any other way holds nothing up.
- Cite the current state from the reference docs by file; do not restate it.

## User decisions

Collect what users or operators would notice. Ask one question per message, in everyday words, with effect and
recommendation; record the answer with date in the spec. Nothing already decided in DOCKET.md, CONTRIBUTING.md
or an earlier ready spec is asked again.

## The user's word, grants, commits

- An explicit instruction of the user in the chat goes before this text, the spec and AGENTS.md: carry it out and
  note it in the spec. When its result belongs to another command (code: `docket:implement`), invoke that command
  yourself with the Skill tool, because the run's mark and its evidence hang on the command; the user does not type
  it for you. When the user explicitly asks in the chat for something that `forbidden_paths`, `push_branch`,
  `commit_regex` or `forbidden_commands` in DOCKET.md rules out, change that key in DOCKET.md yourself, say so in one
  line and go on: these rules protect the user from the model, not from themselves. Without such a word from the
  user, DOCKET.md holds.
- A command that matches `forbidden_commands` in DOCKET.md runs after the user's click: docket puts it to the user
  as a dialog, one click grants it once, the LOG records `ask` and `grant`. Run the command; asking for it in prose
  only doubles the dialog.
- On `commit`, every change of the session goes in, plus the files the new content cites; changes in the working
  tree that are not the session's are named in one line, never left out silently.

## Ready check

Before `state: ready`, the spec is read three times and its commands run once. Its result is the spec's
`## Ready check` section, in the form of the template.

1. Refiners: spawn three `refiner` agents (`docket:refiner`) together in the background, on your own model (no
   `model`), each in a fresh context with one angle: `feasibility` (the spec against the code), `spec` (the spec against its concept and the reference
   docs DOCKET.md names), and the project's angle, `third_refiner_angle` in DOCKET.md (unset: users and operators).
   Briefing: the angle, the spec path, the concept path. Each finding is one line. While they run, a returning agent
   gets no message: the turn ends without text, and the user gets one message when the last one is back.
2. Dry run, while they run: every precondition and every acceptance command of the spec runs once, in the shell
   its line is written for (PowerShell syntax in the PowerShell tool). An expectation in prose you weigh against the output
   and write down with an excerpt of the output. An acceptance command passes when it starts and either fails for the
   reason its point has yet to make true, or already passes and is meant to keep passing (a guard criterion). A line
   without a command (`<file>` contains `<text>`) is read, not run. A command that writes outside the project, is
   visible to others or runs longer than two minutes is only checked for starting: the tool exists, the syntax is
   the shell's.
3. Findings: fix in the spec only what would stop or falsify the run: a wrong fact, a criterion that cannot be met,
   a command that does not start. A criterion whose part before the arrow is not a command in backticks is such a
   finding in a new spec. Every other finding stays a line in `## Ready check`. Only what the user alone can decide
   goes to the user, one question at a time, with a recommendation (User decisions).

## Setting ready

Set `state: ready` only when: the Ready check section is written and dated, every point has a criterion the dry run
started, the reference-update section is filled, no user decision is open, and the goal cites no hypothetical
state. Report in one sentence: the spec title is ready, and the one thing the user has to decide, if any. A spec
that was `ready` before it had a Ready check runs as it is; the run checks its prose criteria itself.
