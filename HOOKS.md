# docket's hooks

Five hooks in `hooks/hooks.json`, Python with the standard library only. They refuse exactly five things: a write
into `forbidden_paths`, a push to a branch outside `push_branch`, a commit whose message does not match
`commit_regex`, a command from `forbidden_commands` in a run (all four in PreToolUse), and `state: DELIVERED` while
the gate finds a gap (Stop). Outside a run, a command from `forbidden_commands` goes to the user as a permission
dialog; that is a question, not a refusal. Every other text a hook gives is a statement of state, never an order.

Every refusal and the dialog begin with an identifier: `[docket rule: <key>]` (PreToolUse, the key of DOCKET.md) or
`[docket gate]` (Stop). A turn the Stop hook continues with a statement begins with `[docket state]`; a state line in
the context begins with `[docket]`. Every text stands in one table in `hooks/docket_lib.py` (`SAY`), in English and
German after `user_language`.

The hooks read DOCKET.md at every call and make no exception, for the orchestrator and its agents alike. Who wants
a project rule changed changes its key in DOCKET.md: when the user asks for it explicitly in the chat, the session
does that itself and says so in one line (`commands/spec.md`, `commands/concept.md`, `skills/implement/SKILL.md`).

A run is a session with the run mark (`mode: sealed` in its state file). The mark is set by `/docket:implement`,
typed or called by the model as the skill `docket:implement`, and taken off by the Stop hook at the end of the series.

## SessionStart

Matcher `startup|resume|clear|compact`. Refuses nothing. At every start, also in a project without DOCKET.md: the
judge entry of docket 0.5 leaves `.claude/settings.json` and `.claude/settings.local.json` (known by the digests of
its eight versions or by the mark in a changed copy; a file git tracks included), and the removed entry is appended
to `.claude/docket/judge-removed.json` before a settings file changes; files of 0.5 nobody reads any more go once
they are an hour old. In a project with DOCKET.md, state lines as context: the session's mode, the work dir with the
path of DOCKET.md and `user_language`, the run line of a marked run (spec i of n, its docket, its points), else the
interrupted series, the runs without a Terminal state, the runs stopped with open points, and the last LOG line. A
marked run that goes on after `resume` or `compact` gets the LOG line `resumed`.

## UserPromptSubmit

No matcher. Refuses no prompt. `/docket:implement` marks the run (`mark_run`): run mark, lock of the first spec,
the series in the state, LOG `sealed` and `queued`, the spec's state `in-progress`. It takes a spec, a list of specs,
a concept (its ready specs), `--all` or nothing (the next ready spec). It marks nothing for an unknown spec, no ready
spec, a predecessor not done, a spec another live session holds or has queued, or another spec at work in this
session, and says why in a state line. `/docket:stop` in a run records `stop_requested` and LOG `stop-requested`.
`/docket:status` and `/docket:register` get the series of this session and the interrupted ones. Every typed
`/docket:` command gets the work dir line; in a project without DOCKET.md, the line that it is not set up. Every
prompt in a run gets the run line. Every prompt clears the Stop hook's fields `refused` and `halted`.

## PreToolUse

Matcher `Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell|Skill`, only in a project with DOCKET.md.

- A write tool on a path matching a glob of `forbidden_paths`: refused, `[docket rule: forbidden_paths]`.
- A `git push` to a branch `push_branch` does not match: refused, `[docket rule: push_branch]`.
- A `git commit` whose message (`-m`, `--message` or a heredoc) does not match `commit_regex`: refused,
  `[docket rule: commit_regex]`.
- A shell command matching `forbidden_commands`: in a run refused, `[docket rule: forbidden_commands]`, which says
  as a fact that the command is on that list and did not run; what the run does next stands in the run's text.
  Outside a run the user is asked (`permissionDecision: ask`, LOG `ask`); one click runs it once.
- The Skill call `docket:implement`: the second trigger of `mark_run`, a side effect; the call goes on and the run
  line, or why no run was marked, reaches the model as context.

Each refusal names the key, its value and the path of DOCKET.md. An invalid regex matches nothing.

## PostToolUse

No matcher. Refuses nothing, says nothing to the model. Side effects: in a run, a write on a docket that turns more
points `[x]` or `[~]` than the LOG has counted gets the LOG line `point-delivered` or `deviation`; a spec written
with `state: ready` gets LOG `ready` once; a command that matched `forbidden_commands` and ran gets LOG `grant`. The
life sign: in a run, every tool call (orchestrator and agents) renews the state file's time, and the orchestrator's
next tool call clears `refused`. Without the run mark it writes no state.

## Stop

No matcher; acts only in a run. A Stop while agents run, or a background shell before this run wrote its Terminal
state, is a wait and passes; its time stays in the state, so the run stays live for other sessions. Then the docket
of the spec at work:

- `state: DELIVERED` passes the gate: no point `[ ]`, and every `check` command of DOCKET.md and the acceptance
  command of every `[x]` point (the part before the arrow in backticks) ran after the last agent returned and did
  not fail, read from the transcript; nothing is run. A gap is refused once, `[docket gate]`, with the facts.
- `state: BLOCKED: <reason>`, and any other terminal state of a 0.5 docket, closes the spec unchecked.
- No Terminal state: the turn goes on once with `[docket state]` (the spec, k of m points open). A block the docket
  held when the run began counts only when it says DELIVERED or the run changed its `state:` line in place.
- A second Stop right after either, without a tool call between, passes: LOG `stop-forced`, one line to the user
  with where the run stands and the command that resumes it.

A closed spec: lock released, the spec's state `done` (delivered) or back to `ready`, `metrics/<spec>.json`, LOG
`delivered` or `blocked`. The series goes on with its next spec (`[docket state] Spec i of n is running …`): after
DELIVERED; after BLOCKED when the spec left the project as it found it or its work is pushed; a spec whose
predecessor is open is passed over (LOG `passed-over`). Otherwise, or after `stop_requested`, or after the last spec,
the series ends: run mark `none`, LOG `run-closed`, `commit_records` where DOCKET.md sets it, and once a
`[docket state]` statement with the count of every outcome and why each unstarted spec did not start. The model
then writes the closing message; the Stop after it passes.

## The session state

One file per session, `.claude/docket/state-<session>.json`: the mode (`none` or `sealed`; a mode of 0.5 reads as
`none`), the spec at work, the series. Hooks run at once (a message that starts many agents runs many hooks in the
same second), so a hook writes under a lock file and only the fields it changed. A read that fails while the file
exists is tried again and never counts as "no state". A run counts as live while its state file was touched within
the hour or it waits for its agents; a lock of a dead run is taken over. LOG.md is written by the hooks; in a work dir
with its own git history every LOG line is a commit.

## The pane

`hooks/hooks.json` names one hooks module, `hooks/mod/register.tsx`: the pane, the line under the prompt and, where
docket is not set up, the line above it. It reads no docket file's meaning itself: `hooks/docket_ui.py overview`
answers register, runs and LOG as JSON with the functions the hooks use, so both read one project the same way.
Every open spec shows its position in implementation order (after every `follows:` and `after:` predecessor, then
by age). A button runs the command as typed (`/docket:implement …`, `/docket:stop`, `/docket:init`), in this session
or, in Claude Code Desktop, in a session of its own whose brief ends with the command; the pane records it first
(`docket_ui.py handoff`), and the started session takes it once within fifteen minutes. `docket_ui.py supersede`
writes `state: superseded` with its LOG line; `docket_ui.py stop` records the stop request for a run of another
session. The pane reads again after a prompt, at the end of a turn, at most every ten seconds while tools run and
every fifteen while another session runs a spec. Tests: `claude plugin test .` (the pane, Python side mocked) and
`python hooks/test_hooks.py` (hooks and `docket_ui.py`).

## Footprint

docket adds no code to a repository. In a project it writes `.claude/docket/` (session state, ignored by git) and,
after `/docket:init`, its work dir.
