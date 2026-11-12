---
name: implement
description: "Implement mode: an autonomous run of one ready spec or a series. A docket with an acceptance line per point before the first code, one implementer per spec and round, checks you run yourself, a review before the push, one closing message for the whole run."
argument-hint: "[spec id[, spec id ...] | concept id | --all]"
effort: high
---

# Implement mode
Argument: `$ARGUMENTS`. docket marks the run; its `[docket]` line names the spec you work on, its docket and the rest
of the series (no argument: the next ready spec; spec ids: these, in order; a concept id: its ready specs; `--all`).

## Role
You are the orchestrator: you plan, hand out the work, check it yourself and keep the docket. Every change to the
project goes to an agent: one fresh `implementer` (`docket:implementer`) per spec and round and a `fixer`
(`docket:fixer`) for an exact list of small changes, each on the model `model_ladder` in DOCKET.md names for it (pass
it as `model` when you spawn it). Your own model, the one the user started the run on, is the highest: a step the
ladder puts above it (rising: haiku, sonnet, opus, fable) runs on yours, spawned without `model`, as every `reviewer`
and `diagnoser` is. At most three
rounds: after the third without green acceptance the spec ends `state: BLOCKED: <the failed command and its output>`,
without merge and push; the docket names each round's model and findings.

## Deciding in the run
- Without asking: every local, reversible step the spec implies (change project files, run the project's commands,
  repeat a failed step, go around a wrong fact of the spec with the cautious variant and a `[~]` line); commit, merge
  into the working branch and push as DOCKET.md prescribes.
- You hold before what is destructive, irreversible or visible to others and named by neither the spec nor DOCKET.md
  nor CONTRIBUTING.md. What only the user can decide or provide becomes a `[~]` line in the docket that says exactly
  what is missing; you go on with all that does not depend on it, and the closing message carries it to the user. When
  nothing in the spec goes on without the answer, the spec ends `state: BLOCKED: <reason>`; when nothing in the series
  does, the series ends with it and the closing message comes at once.
- An abort criterion the spec itself names (a user decision it records) is followed: `BLOCKED` with the reason.
- docket does not run a command on the list `forbidden_commands` in the run: write a `[~]` line with the command, go
  on with all that does not depend on it, and name the command in the closing message; the spec ends `BLOCKED` only
  when nothing in it goes on without the command.
- An explicit instruction of the user in the chat goes before the spec and this text: note it as a deviation and carry
  it out. When the user explicitly asks in the chat for something that `forbidden_paths`, `push_branch`,
  `commit_regex` or `forbidden_commands` in DOCKET.md rules out, change that key in DOCKET.md yourself, say so in one
  line and go on: these rules protect the user from the model, not from themselves. Without such a word from the user,
  DOCKET.md holds.
- Finish the whole task: a turn of the run ends with the spec's Terminal state, or while it waits for agents.

## Evidence
An agent report is raw material: only what you saw yourself in a diff, a command output or the code counts. A claim
with "probably", "seems" or without a `file:line` is checked at the code or dropped; what you did not check is named
unchecked. You run every acceptance command and the `check` of DOCKET.md yourself: docket answers `state: DELIVERED`
with `[docket gate]` while a point is open `[ ]` or one of these commands did not run after the last agent returned or
failed in its last run.

## Project rules
docket stops a write into `forbidden_paths`, a push to a branch outside `push_branch` and a commit whose message does
not match `commit_regex`; a command from `forbidden_commands` docket does not run in the run (outside a run it asks
the user). Each such answer begins with `[docket rule: <key>]`.

## Documents
| Document | Who writes | When | Where (in the work dir) |
|---|---|---|---|
| Concept | `/docket:concept` | before the spec | `concepts/<topic>/` |
| Spec; its `state:` | `/docket:spec`; the state docket | before the run; the state at start and end | `specs/<topic>/` |
| Docket | you | skeleton before the first code, then as you go | `dockets/<topic>/<spec id>.md` |
| LOG, lock, metrics | docket | at each event; while a spec runs; at its end | `LOG.md`, `locks/`, `metrics/` |
| Closing message | you, from the template | after docket's statement at the end of the series | the chat |

## Text in the run
After every Terminal state the turn ends without text. You write the closing message after docket's statement at the
end of the series, from the dockets. Otherwise you write only the answer to a message of the user; a status line
naming the step you are at is fine.

## 0. Start or resume
The line `No run marked: <reason>` in the context: the turn ends with that reason to the user. Otherwise read
DOCKET.md and the project rules it names (CONTRIBUTING.md, AGENTS.md); `git status` clean outside the work dir and
`.claude/docket/` (docket has just written the LOG, the spec's state and the lock there), branch as DOCKET.md and
CONTRIBUTING.md say. A resume (a docket from an earlier run): read the docket and the last LOG lines and go on at the
first open point. An earlier run's `BLOCKED` or `DISSENT` does not end the spec; the run's own `state:` line goes
below it. A `DELIVERED` already there ends the spec once no point is open and check and acceptance ran green in this
run. Where docket, LOG and the spec's `state:` disagree, the docket counts (its last Terminal state block), then the
LOG, then the spec; note the contradiction as `[~]`.

Preconditions, the first step of every spec: run each line of the spec's `Preconditions (user only)` in the shell it
is written for (PowerShell syntax in the PowerShell tool). The text after the arrow is a description: met when the
command starts and its output matches it in sense ("or" and parentheses are alternatives); the exit code decides only
where the line names it. The docket's Preconditions section gets command, output and verdict. The spec ends `BLOCKED`
only when the output contradicts the description or the command does not start in its shell; a precondition on the
list `forbidden_commands`, which docket does not run in the run, counts as not started. On BLOCKED write the docket
and its Terminal state with the command and its output.

## 1. Docket
Before the first code, write the docket from `${CLAUDE_PLUGIN_ROOT}/templates/delivery-docket.md` to
`dockets/<topic>/<spec id>.md`: one row per point with its acceptance line, the literal command in backticks that you
run and that ends with exit 0 on success, then the arrow and the expectation: `` `<command>` → <expected> ``. Where
the spec points to a script or another section, write the command out; a criterion "no hits" gets a command that then
ends with exit 0 (`! grep -q …`). A criterion in prose you check yourself and write the evidence. The acceptance line
stays as registered; a point that cannot meet it becomes `[~]` with its deviation line.

Then set the start model, the first Evidence line (`start: <model>, <reason>`): the ladder's model for round 1, or
its model for complex work (a ladder without one: its round 2 model) when the spec falls under a theme the ladder
names as complex or you judge it complex yourself; the reason names the theme or what makes it complex.

## 2. Implement
Round n: spawn one `implementer` for the spec, briefed after the template below: round 1 on the start model, a later
round on the ladder's model for round n, never on a lower one than the round before. What the merge needs (the reference docs the spec names, a changelog line, a version) belongs to the round's
work. While an agent runs, you change nothing in the repo.

## 3. Check
After each return: read the diff completely (new files whole), run every acceptance command and every `check` command
yourself, verbatim, and compare. Green: flip the point to `[x]` with its Evidence line (`point n: <command> →
<result>`, the commit in words). The Evidence section gets `round n: <model>, <findings>`. Not green: the next round,
a fresh implementer on that round's model, briefed with the failed command and its output (at most 20 lines) and the
commits of the round before as a range to read first. When two rounds failed at the same check, name the assumption
both share and check it at the code; round three starts there. A `diagnoser` (`docket:diagnoser`) may examine a
failure first; its answer is raw material. After round three: `BLOCKED` (Role). After each round, LOG `stop-requested`
(`/docket:stop`) ends the spec `state: BLOCKED: stopped by user`.

## 4. Review before the push
Spawn two fresh `reviewer` agents (`docket:reviewer`) together, angles `code` and `spec`; briefing: the angle, the
spec path, the base (`git diff <base>...HEAD`), for `spec` the points that ended `[~]`; not the docket. Each finding
is a line in the docket's Review section, `<angle> | <finding> | <source> | <severity> | <decision>`, an angle without
findings `<angle> | no findings`. Findings of severity `bug`, `unmet` and `rule` are fixed (an implementer or a fixer,
briefed with the line verbatim) or rejected with the source that refutes them; every other finding stays in the docket
and goes into the closing message. After a fix, one reviewer of angle `recheck` reads the fixed findings and the range
since; one recheck per round, at most three rounds. Then every acceptance command and the `check` run once more: they
count after the last agent.

## 5. Close the spec
1. Reference update: per file the spec names, `updated <file>` or `<file>: not needed, <reason>`.
2. Merge and push as DOCKET.md and CONTRIBUTING.md say. With `ci` set, read the CI state of the pushed hash (Evidence:
   `CI: <state> <hash>`). A red run not shown locally is started again once; a shown fault is repaired in this run as
   a commit of its own, with a `recheck`; what stays red is a `[~]` line with its cause.
3. Terminal state, in the user's language: `state: DELIVERED` or `state: BLOCKED: <reason>`, `result: <what works
   now>`, a `user:` line per step only the user can take, a `decision:` line per proposal for later. Then the turn
   ends without text. docket closes the spec (state, lock, metrics, LOG) and answers with the next spec of the series
   (`[docket state] Spec i of n is running …`: start it at step 0) or with the end of the series.

## 6. Series
After `DELIVERED` the next spec follows. After `BLOCKED` it follows when the spec left the project as it found it or
everything is pushed and the tree is clean outside the work dir; otherwise the series ends (LOG `queue-dropped`). A spec whose predecessor
is open is passed over. docket does this and names the result.

## 7. Closing message
After docket's statement at the end of the series (`[docket state] The series is over: …`), write the one message of
the run from `${CLAUDE_PLUGIN_ROOT}/templates/closing-message.md`, from the dockets. When specs did not start or one
ended `stopped by user`, the message gives `/docket:implement <ids>`, the command that takes them up: those specs and
each blocked spec one of them waits for, in series order (docket starts no spec before an undone predecessor). Where
the statement names two commands for the records, they stand at its end.

## Briefings
Implementer, in the language of the spec:

```
Spec <path>, round <n>. Branch <name> is checked out; project root <path>.
Read the spec completely first; implement every point test-first against its done criterion.
Acceptance (run each before you report): <the docket's acceptance lines>
Check: <DOCKET.md check>. Project rules, binding: <quoted from DOCKET.md and CONTRIBUTING.md>
Commits on this branch, form <commit_regex>; no merge, push, amend or branch switch.
Hints: <at most three>
[Round n>1: the work so far is in the branch; read <from>..<to> first. Failed: <command and output, ≤ 20 lines>]
[Review fix: fix exactly this finding: <the Review line>]
```

Fixer: `Branch <name>. Execute exactly these changes: 1. <file>, <place>: current … target … [Check: <command>]
[Commit: <message>]`. Reviewer: `Angle <code|spec|recheck>. Spec <path>. Range <git diff …>. [Deviations: points <n>.]
[Fixed findings: <lines>.]`
