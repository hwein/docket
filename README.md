<p>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img alt="docket" src="assets/logo-light.svg">
  </picture>
</p>


Spec in, docket out. A Claude Code plugin that separates thinking from building and makes the building verifiable.

A **delivery docket** is the slip that travels with a shipment and proves what was delivered. Here, every spec
gets one docket: a list of what counts as done, registered before the first code, filled with evidence, written by
the team and read by you.

## What it is for

Long, autonomous implementation runs. You think with the model until the WHAT is agreed, write it down as a
spec, then hand it over: one session runs for hours, spawns its own agents, verifies its own work and ends with
one message. docket exists so that such a run can be trusted without watching it: the run checks its own work
against commands written down before the first code, the docket shows what was done with evidence you can check,
and hooks hold only the rules that have no exception (your project rules and the gate before delivered).

It is not a chat assistant for quick edits. The modes before the run are short; the run itself is long.

## Install

Requires Python 3 on PATH. Hooks use the standard library only.

```bash
claude plugin marketplace add hwein/docket
claude plugin install docket@docket
```

Then, in a project: `/docket:init`.

Update (restart Claude Code afterwards):

```bash
claude plugin marketplace update docket
claude plugin update docket@docket
```

Claude Code keeps its own copy of each installed version, so a session never runs a half-updated plugin. Every
push to this repository runs the hook tests on Linux and Windows (`.github/workflows/test.yml`); the badge on the
latest commit shows whether they passed.

The plugin ships an output style, `docket:docket`: the result first, at most one question with a recommendation,
titles instead of ids, one message when the agents are back. It is your choice (`/config`); docket does not set it.

## Workflow

1. `/docket:init`, once per project: audits the repo, asks only what it cannot answer, writes `DOCKET.md`.
2. `/docket:concept`: agree the WHAT, one idea at a time.
3. `/docket:spec`: points with done criteria a command checks. Three refiner agents read the spec and its commands
   run once before it is `ready`.
4. `/docket:implement <id>`: the run. A docket with one acceptance line per point before the first code,
   implementer agents, checks the orchestrator runs itself, a review, push. Nothing asks you until it is over.
5. One closing message: what is delivered, the commands checked with their results, what deviated, what is yours
   to do. Deviations go back to step 2.

## Commands

| Command | What happens |
|---|---|
| `/docket:init` | Sets the project up and writes `DOCKET.md` after your `go`. |
| `/docket:concept` | Thinks with you until the WHAT is agreed. |
| `/docket:spec` | Turns it into a spec with checkable done criteria, then `ready`. |
| `/docket:implement` | Runs the next ready spec, a spec id, a list of ids, a concept id or `--all`, in order. |
| `/docket:register` | Concepts, specs, dockets, last LOG lines. |
| `/docket:status` | One line: mode, spec, open points, last LOG line. |
| `/docket:stop` | Ends a run at the next checkpoint. |

## What you can rely on

- Delivered means: no point open, and the project's check and every acceptance command ran after the last agent
  and passed.
- docket enforces five things: `forbidden_paths`, `push_branch`, `commit_regex`, `forbidden_commands` in a run,
  and the gate before delivered. Details: [HOOKS.md](HOOKS.md).
- Every command the closing message lists as checked was run by the orchestrator, with the result it saw.

The pane beside the chat shows specs, their state and the run at work; its buttons run the same commands.

The work dir (`DOCKET.md`, `specs/`, `dockets/`, `LOG.md`) lives in `~/.docket/<repo key>/` by default;
`/docket:init` offers `docket/` inside the repo instead. docket adds no code to your repository.

## Status

0.6.2, early development. No warranty: use docket only where you can verify the result.
