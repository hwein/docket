# docket

Spec in, docket out. A Claude Code plugin that separates thinking from building and makes the building verifiable.

A **delivery docket** is the slip that travels with a shipment and proves what was delivered. Here, every spec
gets one docket: a pre-registered list of what counts as done, filled with evidence, append-only, written by the
team and read by you.

## Commands

Typed with the plugin's namespace, as Claude Code lists them: `/docket:concept`, `/docket:docket init`.

| Command | Mode | What happens |
|---|---|---|
| `/docket:docket init` | init | Audits the repo, asks you only where it is silent, writes `DOCKET.md` after your `go`. |
| `/docket:concept` | concept | Thinks with you. One idea per message, one question at most. |
| `/docket:spec` | spec | Turns the agreed WHAT into a spec with checkable done criteria, then `ready`. |
| `/docket:implement <id>` | sealed | Runs the spec autonomously and ends with exactly one message. |
| `/docket:docket` | | The register: concepts, specs, dockets, last LOG lines. |
| `/docket:docket status` | | One line: mode, spec, open points, last LOG line. |
| `/docket:docket stop` | | Ends a sealed run at the next checkpoint. |

## How a sealed run works

1. Three refiner agents read the spec against the code, the concept and the project rules.
2. A docket skeleton is pre-registered: one line per point with the evidence that will count.
3. One implementer agent per point. The orchestrator reads every diff and runs the project check itself.
4. Every delivered point gets its evidence line; what differs from the spec becomes a deviation line.
5. The run ends with `DOCKET DELIVERED`, `DOCKET BLOCKED` or `DOCKET DISSENT`. The session is then closed.

While it runs, your messages go to an inbox the orchestrator reads between points.

## What the hooks enforce

Hooks check provenance, form and state, never content.

- Specs freeze at `ready`; the docket is append-only; `LOG.md` is written by hooks only.
- `DOCKET DELIVERED` passes only if the check ran after the last agent returned, every cited `file:line`
  exists, and every reference doc the spec names is updated or accounted for.
- Project rules from `DOCKET.md` (branch, commit form, forbidden paths and commands) apply to every command.
- Every message in every session of the project ends with a time stamp fetched from the clock. No hashes, ids
  or hedges reach you.

## Install

Requires Python 3 on PATH. Hooks use the standard library only.

```bash
claude plugin marketplace add /path/to/docket
claude plugin install docket@docket-local
```

Then, in a project: `/docket:docket init`.

## Where things live

- Plugin: this repository. Nothing is copied into your project.
- Project: `.claude/docket/` for session state (gitignored).
- Work dir with `DOCKET.md`, `specs/`, `dockets/`, `LOG.md`: `~/.docket/<repo key>/` by default, with its own
  git history. `/docket init` offers `docket/` inside the repo instead.

## Layout

```
commands/   concept, spec, implement, docket
agents/     conceptor, refiner, implementer, fixer
hooks/      hooks.json, Python hooks, tests
templates/  DOCKET.md, concept.md, spec.md, delivery-docket.md, check.py, judge-hook.json
```

Tests: `python hooks/test_hooks.py`.

## Status

0.1.2. Every command has run end to end in a toy project; not yet used on a real codebase.
