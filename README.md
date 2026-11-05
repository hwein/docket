# docket

Spec in, docket out. A Claude Code plugin that separates thinking from building and makes the building verifiable.

A **delivery docket** is the slip that travels with a shipment and proves what was delivered. Here, every spec gets
exactly one docket: a pre-registered list of what counts as delivered, filled in with evidence, append-only, written
by the team and read by you.

## What it does

- **Three modes, one state machine.** `/concept` talks with you (one idea per message, one question at most).
  `/spec` turns the agreed WHAT into a spec with done criteria a script can check. `/implement <id>` seals the spec
  and runs autonomously: refinement from three angles, a pre-registered docket, implementer agents per point,
  checks the orchestrator runs itself, and exactly one final message. After that the session is `closed` and
  answers only from the docket.
- **Freedom in the how, duty to leave a trail.** Hooks check provenance, form and state, never content. Specs freeze
  at `ready` (a gate checks every point has a verifiable criterion and no user decision is open); dockets are
  append-only; the LOG is written by hooks only (sealed, rewrite, every point delivered or deviated, delivered /
  blocked / dissent), so it cannot be made up; in the default work dir every LOG line is a git commit. The first
  implementer runs only once the docket is pre-registered and every refinement finding has a decision. `DOCKET
  DELIVERED` passes only when the project's check ran after the last agent returned, every cited `file:line`
  exists, every reference doc the spec names is updated or accounted for, every CI hash is reachable from HEAD
  and was pushed, and the stamp carries the docket's delivered/points count. Project rules from `DOCKET.md`
  (`push_branch`, `commit_regex`, `forbidden_paths`, `forbidden_commands`) are enforced on every shell command
  and write. Every refusal is counted per rule and shown again at the next run of that spec. Finished specs are
  not a reference source: the docs say what IS, specs say what SHOULD.
- **Two registers.** Reports to the orchestrator are dense and carry an `Unverified:` line. Messages to you are
  short and plain: a deterministic parser plus a small judge model (installed per project by `/docket init`) reject hashes, ids, hedges, option menus,
  promises and manual tasks assigned to you, and the message is rewritten before you see it.
- **Your pace, not the model's.** During a sealed run your free text goes to an inbox the orchestrator reads
  between points; `/docket stop` ends the run cleanly. No questions reach you until the docket is written.
- **`/docket init` interviews the repo first, you second.** It gathers evidence for eleven questions about how
  the repo is worked on (branches, commits, merges, gates, releases, storage, language, commenting, forbidden
  content, external systems), asks you only where the repo is silent or contradicts itself, and writes `DOCKET.md`
  plus, if you wish, CONTRIBUTING.md. Nothing is written before your `go`; the hook enforces that. Re-running it
  shows only what moved.

## Install

Requires Python 3 on PATH (`python` or `python3`). Hooks are standard-library only.

```bash
claude plugin marketplace add /path/to/docket
claude plugin install docket@docket-local
```

Then, in a project: `/docket init`.

## Where things live

- The plugin: this repository, nothing is copied into your project.
- Your project: `.claude/docket/` (session state, gitignored) and, only if the work dir is not the default,
  `.claude/docket.json` pointing to it.
- The work dir (`DOCKET.md`, `concepts/`, `specs/`, `dockets/`, `LOG.md`, `inbox/`, `metrics/`): by default
  `~/.docket/<repo key>/` with its own git history, or `docket/` inside the repo, or a second repo. `/docket init`
  asks.

## Layout

```
commands/   concept, spec, implement, docket (register, init, stop, status)
agents/     conceptor, refiner, implementer, fixer
hooks/      hooks.json + Python hooks (state, write rights, append-only, stop grammar, judge prompt), tests
templates/  DOCKET.md, concept.md, spec.md, delivery-docket.md, check.py, judge-hook.json
```

Run the hook tests with `python hooks/test_hooks.py -v`.

## Status

0.1.2. The hooks are tested (77 cases); `/implement` has run end to end in a toy project; `/docket init`,
`/concept` and `/spec` are specifications of their interviews, not yet verified in a real project.
