# How the hooks enforce it

Hooks check provenance, form and state, never content. This is the mechanics behind the three README sections
"What you can rely on", "When docket interrupts you" and "What you see".

## Frozen specs, append-only dockets, hook-written log

- A spec in state `ready` is refused to every write; only the conceptor agent in rewrite mode may edit it, and the
  hook logs `rewrite` and `rewrite-done`.
- The docket is append-only: after every edit each previous line must still be there, in order; a checkbox flip
  `[ ]` to `[x]` or `[~]` and filling a placeholder are the only in-place changes.
- A docket line that cites a `file:line` which does not exist is undone at once (the snapshot is restored, a
  skeleton removed), so no citation has to be corrected in place later.
- `LOG.md` is written by hooks only: sealed, point-delivered, deviation, delivered, blocked, dissent, grants,
  judge verdicts.

## The end of a spec

A spec ends in its docket, not in a message: the orchestrator writes the `Terminal state` section (`state:`
DELIVERED, BLOCKED or DISSENT, `result:`, `open:`, `watch:`, `user:`, `decision:`, `dissent:`), in the user's language, without
ids or hashes; the last block counts. The Stop hook accepts DELIVERED only if every command of the project's check (as
many as the project has, listed in `DOCKET.md`) ran after the last agent returned and none failed in its last run;
the command of every delivered point's acceptance line (`<command> → <expected>`) ran after the last implementer or
fixer returned and did not fail in its last run; every `file:line` cited in the docket's Evidence, Deviations and
Reference update sections exists; every reference doc the spec names is marked `updated` (and changed since
sealing) or `not needed`; every refinement line and every review line carries a decision, and the review holds
(below); with `ci` configured, a `CI: <state> <hash>` line names a hash reachable from HEAD
after a push in this session. Otherwise the run goes on until it holds. An accepted end closes the spec, writes its
metrics (`metrics/<id>.json`) and seals the next spec of the series, or starts the closing. The series goes on
after DELIVERED, and after BLOCKED or DISSENT when the project is as it was at the spec's sealing (`sealed_head`,
`untouched_since`: no commit and no uncommitted change outside docket's own folders); a spec that left work behind
ends the series, since the next spec's push would carry it. A queued spec whose predecessor is not done is passed
over (LOG `passed-over`) and named in the closing message with what it waits for.

## The closing message

When the last spec of a run has ended, or the series ended at a BLOCKED or DISSENT spec, or the user stopped the
run, the hook renders the run's one message from the dockets, the LOG and git: one line for the run (whether the
work is pushed and CI green), one line per spec by its title with its points and result (a spec not started with
what it waits for), then only what the user acts on: what only the user can do, which decision is due and with
which command (neither cut below 400 characters), what came of the user's notes. The last line names where the
dockets are and counts what they hold beyond that: the `open:` lines and deviations, and the `watch:` lines of the
Terminal state. The orchestrator sends it verbatim; the display gate shows nothing else, and after three misses it
shows the rendered text itself. The session is closed afterwards.

## Project rules

Branch (`push_branch`), commit form (`commit_regex`), `forbidden_paths` and `forbidden_commands` from `DOCKET.md`
apply to every command. A forbidden command is not refused but put to the user as a permission dialog: one click
runs it once, and the LOG records the grant. A sealed run has nobody to ask, so there it stays refused and is named
as BLOCKED in the spec's docket and in the closing message.

## Messages

- Every message in every session of a project with a `DOCKET.md` ends with a time stamp; the hooks hand the time to
  the model with every prompt and tool result, so no tool call is spent on it.
- In concept and spec mode a returning agent gets no message while others still run: the user gets one message when
  the last one is back.
- Every message to the user is in the language `DOCKET.md` sets (`user_language`, question 12 of `/docket:init`:
  the author's language, not the repo's); the check counts function words, code spans and URLs excluded.
- No hashes, ids or hedge words; at most one question, with a recommendation; no option menus, no promises, no
  self-talk. The question in the desktop app's next-step box counts toward the one question.
- A blocker is said once: the same `Blocker` line twice in a row is refused. A blocker ends the turn: until the user
  answers, nothing is written and no agent or workflow starts.
- A quoted hook answer must have come from a hook in this session.
- A refusal by a hook is Claude's to resolve, and the user reads nothing about it: no hook, no refusal text, no
  session state, no judge; where only the user can clear it, one sentence says what they decide or type. The rule
  comes with every prompt and session start outside a run and with every refused tool call of the conversation.
- The display gate (MessageDisplay hook) holds a message that breaks a rule back before the user sees it; the Stop
  hook asks for it again in full.
- A sealed run writes no text. A turn that waits for agents ends without text: the Stop hook recognises the wait by
  Claude Code's `background_tasks` or by an agent notification queued and not yet seen by the model, and checks
  nothing; once the spec's Terminal state is written, only agents still running hold it, not a dev server. Whatever
  text the orchestrator still writes is replaced live by the hook's status line (spec i of n, title, points
  delivered), and only when that line differs from the last one shown.
- The description of an agent in a run is what the user reads when it finishes: in a series it starts with
  `Spec i of n: `, then the task in everyday words; the plugin's words are refused there.
- The Stop hook refuses a message only once: a second Stop right after its own refusal, with no tool call between,
  is let through and logged; in a run the user is told in one line that the run stopped and how to resume it.

## Modes

Each mode keeps to its work: concept mode writes concepts and `DOCKET.md`, any other write in the repo comes to the
user as a permission dialog, and an order for specs given there is answered with `/docket:spec`. A sealed run asks
no question, not in the text and not in the next-step box.

No prompt of the user is refused: `user_prompt.py` never exits 2. Free text typed during a run reaches the
orchestrator, whose next text is the answer and is shown (`answer_due`, the one text of a run the display gate
lets through); the inbox keeps the message for the docket and the closing message, and a run that had stopped
goes on with it. A command that cannot do what it says (a spec that runs or waits in another session, a
precondition not met, specs out of order, a spec the project does not have, a mode command during a run) changes
nothing and reaches the model with the reason and the way out; the model tells the user in one or two sentences,
and outside a run nothing is written or started in that turn (`declined`, `pre_tool_use.py`).

In a sealed run the orchestrator keeps the docket itself; a fixer gets no docket or metrics path. The hooks' own
source is not read. An implementer, refiner, reviewer, diagnoser or conceptor is spawned with its `model`; a spawn without
one is refused, so the model is a decision per tier and role and never the orchestrator's own by default.

The review gates the push and DELIVERED alike:

- The Review section carries a line of each angle of round one, `code` and `spec` (a finding or
  `<angle> | no findings`), and every line a decision.
- A finding line that names its angle names its severity. A finding of severity `bug`, `unmet` or `rule` that is
  `deferred` is refused: it is fixed, rejected with its source, or the spec ends BLOCKED.
- Of two lines with the same angle and finding the later one counts, so a line is corrected by writing it again.
- The review is run, not written: once an implementer or fixer wrote in the session, a reviewer agent must have
  started after the last of them returned, and returned itself. A fix after a review therefore needs a reviewer
  again; a spawn a hook refused is none.
- A reviewer's briefing that names the docket is refused: it judges the order and the delivery, not the
  orchestrator's account of them.
- A `suspect` (wrong behaviour the reviewer could not show) that is `deferred` is refused, and so is `observe` on
  any other severity.

Against chasing a symptom, and against waving a fault away:

- While a point is open, the implementers that returned since the last point was closed are its rounds. A second
  implementer is refused until the docket's Triage section has a line for that point with its `cause:`; a third
  until that cause names a `file:line`. Of two Triage lines for a point the later one counts.
- A new docket line whose decision is `observe` is undone at once, like a wrong citation, unless it carries
  `cause:`, `repro:` and `impact:`, the orchestrator ran the backticked repro command three times in the session
  and it did not fail all three times, and two diagnoser agents have returned (the examination and its
  challenge). A `repro:` without a command (nothing of a reasonable size shows the fault) skips the runs, not the
  challenge. No rule counts time.
- A spec whose docket sets something to `observe` ends only with a `watch:` line in its Terminal state, and a
  `watch:` line only with all its parts: what it is about, `seen:`, `suspected:`, `impact:`, `signal:`. The closing
  message shows each as a block of its own, so that the user can copy it into `/docket:spec` as it stands.

Before the session seals, the user-only preconditions of every spec of
the run (`Preconditions (user only)`: `command` → expected output) are run; the first that fails is named and
nothing is sealed; a precondition that matches `forbidden_commands` is not run, and its output never reaches the LOG.
Only the spec at work is locked, exclusively; a session counts as running while its state was touched within the hour
or it waits for its agents, so a lock of a crashed run is taken over and the next session start names an interrupted series with the
titles and state of its specs.

## The judge

The optional judge (a prompt hook, per project, opt-in at `/docket:init`) checks the form of a message after the
user has read it: one question at most, no option menus, no manual tasks for the user beyond the steps of a blocker,
no promises, no self-talk. Whether a sentence names its source is a rule of the prose, not of the judge. Its verdicts
go to Claude and the log, never to the screen: Claude answers them with silence. Where the harness answers that
silence with a demand for visible output, Claude writes the line `[docket] noted`, and the display gate hides it, as
it hides a bare stamp or `Blocker unverändert` after a verdict. A sentence that names a `/docket:` command for the
user to type is no manual task.

## The session state

One file per session in `.claude/docket/` holds the mode, the spec at work and the clock. Many hooks run at once (a
message that starts 14 agents runs 14 hooks in the same second), so a hook never writes the state as it read it:
under a lock file it reads the file again and writes back only the fields it changed. A read that fails while the
file exists is tried again and never counts as "no state". An agent's tool calls write nothing into it; in a run
they only renew the file's time, which is what tells other sessions that the run lives.

No hook sets a mode back to none. Where the state says none and the last mode command the user typed in this
session was `/docket:concept`, `/docket:spec` or `/docket:init`, the mode is put back from the transcript (LOG
`mode-restored`): at the user's next prompt, when an agent is started, and before a spec or concept is written. A
run (`/docket:implement`) is not rebuilt this way.

A sealed run has no judge: at sealing its entry leaves `.claude/settings.json` (or `settings.local.json`) for a
record in `.claude/docket/` (written first), and it comes back when the run ends; a session start puts it back when
the run that took it is no longer running. A settings file a commit could carry (tracked, or not ignored) is left
alone; there a verdict in a run is logged as `judge-ignored` and counts nothing.

## The pane

`hooks/hooks.json` names one hooks module beside the hook commands, `hooks/mod/register.tsx`. It draws the pane, the
line under the prompt and, where docket is not set up, the line above it. It reads no docket file's meaning by
itself: `hooks/docket_ui.py overview` answers the register, the runs and the LOG as JSON, built with the functions
the hooks use (`spec_records`, `docket_counts`, `terminal_section`, `session_live`), so the pane and the hooks read
one project the same way. A spec's or a docket's text is shown as the file holds it.

Every open spec carries its position in the recommended order of implementation in front of its state: a spec after
every predecessor (`follows:` in its header, `after: <id>` in its Goal section), then by age (`in_order`), counted
over the whole project, since a series runs across concepts
in that order. A concept's open specs are listed by that position, its finished ones below.

What a button does:

- Implement, stop, set up: the command itself (`/docket:implement s-… | c-…`, `/docket:stop`, `/docket:init`), run
  as if typed, so `user_prompt.py` resolves, checks and seals exactly as for a typed command. Ticked specs go as
  `/docket:implement s-a, s-b` in implementation order (`in_order`, the order `overview` numbers them in); a tick
  whose predecessor is neither done nor ticked offers no run, since the hook would refuse it. In a session of its
  own: Claude Code Desktop's session tool is called with a brief that ends with the command on a line of its own.
  Where the call starts none, the request is put into the empty prompt box for the person to send, in a sentence
  that names the command and carries it on no line of its own: the app starts a session on a turn the person began
  and refuses it on a prompt the plugin submits. Where no box takes it, or a draft stands in it, the plugin submits
  that request, which has Claude propose the session for one click where the app starts none. `user_prompt.py`
  reads a command on the first line of a prompt or on its last (`docket_lib.command_line`), never on a line in
  between: pasted text and a request that names a command are text. One exception: the app wraps the brief of a
  session it starts (a heading and the background above, a line about a notes folder below), so the pane records
  the command first (`docket_ui.py handoff --command …`, `.claude/docket/handoff.json`), and the session whose
  prompt carries exactly that command on a line of its own takes it (`take_handoff`): once, within fifteen
  minutes, never the session that recorded it. In a project without DOCKET.md no command but `/docket:init` changes anything, a state that names another
  mode is read as none (`load_state`), and a spec named in `/docket:implement` is a spec of this project.
- Superseded: `docket_ui.py supersede --spec … --by …` writes `state: superseded` and the LOG line
  `superseded … by=s-…`. It refuses a spec that runs or waits in a running series, an unknown or superseded
  successor, and a spec already superseded. The model still never sets this state.
- Stop for a run of another session: `docket_ui.py stop --session …` records what `/docket:stop` records there
  (`stop_requested`, LOG `stop-requested`): that series ends with the spec at work.

Claude Code Desktop starts the process of a new session in a placeholder folder (`claude-code-spare` in the app's
data) before the first prompt and moves it to the project with that prompt (`relocated` in the transcript). The
pane therefore reads the project again when the session start hooks run (`classic.SessionStart`), when a surface
attaches and at a turn's start, wherever the session's root is no longer the one of the last read, and opens on
the first read that finds a DOCKET.md. Before the first prompt the process does not know the project.

The pane reads again after a prompt, at the end of a turn, at most every ten seconds while tools run, every fifteen
while another session runs a spec, and on `Refresh`. Its tests run against the engine's own test kit
(`claude plugin test .`), with the Python side mocked; `python hooks/test_hooks.py` covers `docket_ui.py`.

## Footprint

docket adds no code to the repository. The judge entry `/docket:init` writes into `.claude/settings.json` follows
the plugin: every session start brings it to the installed version, so an update needs no second init; a judge the
project changed stays as it is.
