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

## DOCKET DELIVERED

The Stop hook accepts `DOCKET DELIVERED` only if every command of the project's check (as many as the project has,
listed in `DOCKET.md`) ran after the last agent returned and none failed in its last run; every `file:line` cited in
the docket's Evidence, Deviations and Reference update sections exists; every reference doc the spec names is marked
`updated` (and changed since sealing) or `not needed`; with `ci` configured, a `CI: <state> <hash>` line names a
hash reachable from HEAD after a push in this session. Otherwise the run goes on until it holds.

## Project rules

Branch (`push_branch`), commit form (`commit_regex`), `forbidden_paths` and `forbidden_commands` from `DOCKET.md`
apply to every command. A forbidden command is not refused but put to the user as a permission dialog: one click
runs it once, and the LOG records the grant. A sealed run has nobody to ask, so there it stays refused and is named
in `DOCKET BLOCKED`.

## Messages

- Every message in every session of a project with a `DOCKET.md` ends with a time stamp; the hooks hand the time to
  the model with every prompt and tool result, so no tool call is spent on it.
- Every message to the user is in the language `DOCKET.md` sets (`user_language`, question 12 of `/docket:init`:
  the author's language, not the repo's); the check counts function words, code spans and URLs excluded.
- No hashes, ids or hedge words; at most one question, with a recommendation; no option menus, no promises, no
  self-talk. The question in the desktop app's next-step box counts toward the one question.
- A blocker is said once: the same `Blocker` line twice in a row is refused. A blocker ends the turn: until the user
  answers, nothing is written and no agent or workflow starts.
- A quoted hook answer must have come from a hook in this session.
- The display gate (MessageDisplay hook) holds a message that breaks a rule back before the user sees it; the Stop
  hook asks for it again in full. In a sealed run the gate replaces every message that is not the terminal one
  with the hook's status line, and shows a terminal message only when the Stop hook will accept it.
- The terminal message of a run: line 2 names the spec by its title, one line starts with `Du musst` / `You must`
  or `Nichts zu tun` / `Nothing to do`, no line is about hooks, the last line is the stamp with the docket's counts.
  The hook then adds a closing line of its own: delivered how many of how many, run finished, and the next spec
  (of the series, or in state `ready`).

## Modes

Each mode keeps to its work: concept mode writes concepts and `DOCKET.md`, any other write in the repo comes to the
user as a permission dialog, and an order for specs given there is answered with `/docket:spec`. A sealed run asks
no question, not in the text and not in the next-step box; free text typed during it goes to an inbox the
orchestrator reads between points.

## The judge

The optional judge (a prompt hook, per project, opt-in at `/docket:init`) checks the form of a message after the
user has read it: one question at most, no option menus, no manual tasks for the user beyond the steps of a blocker,
no promises, no self-talk. Whether a sentence names its source is a rule of the prose, not of the judge. Its verdicts
go to Claude and the log, never to the screen: Claude answers them with silence.

## Footprint

docket adds no code to the repository. The judge entry `/docket:init` writes into `.claude/settings.json` follows
the plugin: every session start brings it to the installed version, so an update needs no second init; a judge the
project changed stays as it is.
