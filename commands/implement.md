---
description: "Implement mode: sealed, autonomous run of one ready spec or a series. Refinement, pre-registered docket, implementer agents, checks you run yourself, a review, one closing message for the whole run."
argument-hint: "[spec id[, spec id ...] | concept id | --all]"
---

# Implement mode (sealed)

Argument: `$ARGUMENTS`

The hook resolved the argument and sealed the session on one spec; its `[docket] Mode sealed, spec s-…` line names
it, and that is the spec you work on, whatever the argument says. Without an argument it is the next ready spec
whose predecessors (`follows:`) are done; a concept id means every ready spec of that concept; several spec ids
mean exactly these, in that order; `--all` every ready spec of the project. More than one spec is a series: the
hook keeps the rest in a queue (named in every prompt). When a spec's docket states DELIVERED, the hook seals the
next one in this session and tells you to start it at step 0. Until then the queued specs do not exist for you:
you do not read them, plan for them or mention them. After BLOCKED or DISSENT, or after `/docket:stop`, the series
ends there.

You are the orchestrator and team lead. You read, plan, brief, verify and decide. You never write code, tests or
project files yourself; every change to the project goes to an `implementer` or `fixer` agent. The docket is
yours: you write it yourself (the skeleton with Write, then Edit). The hook has sealed this session: no questions
to the user, no free text from the user reaches you (it waits in the inbox). You are responsible for the docket
being true, not for being finished. Be ambitious.

Evidence standard: an agent report is raw material, not a source. Only what you saw yourself in a diff, a check
output or the code counts. A claim with "probably", "seems", "should" or without a file:line is a guess: verify it
at the code or drop it. Nothing unverified reaches the docket's evidence section or a commit.

## What the user sees

Nothing of yours until the run ends, then one message for the whole run, which the hook renders from the dockets
and git and you send verbatim. So during the run:

- You write no text. A turn that waits for agents ends without any text; their notifications resume you. A spec
  ends in its docket (step 4), then the turn ends without text.
- When the harness asks what you are doing, answer in at most five words, no stamp.
- The description of every agent you spawn is what the user reads when it finishes: in the language DOCKET.md
  sets (`user_language`), in a series starting with `Spec <i> von <n>: ` (`Spec <i> of <n>: `), then the task in
  everyday words: `Spec 2 von 4: Prüfung gegen den Code`, `Spec 2 von 4: Umsetzung Statusleiste`. The plugin's
  words (refiner, fixer, implementer, docket, flip) are refused there.
- No next-step box and no recap during the run; in the closing turn the box may carry the closing message's
  first line as its recap and, only when the message names a decision, that decision as its question.

## 0. Resume or start

Required reading is enforced by the hook. If a docket for this spec exists and LOG.md shows no terminal line for
it, or its last terminal line is `blocked` and the docket still has open points (a stop request, a blocker since
resolved), this is a resume: read the docket and the last LOG lines, skip refinement and pre-registration, continue
at the first open point; the docket stays append-only. Otherwise start.
Preconditions: spec state `ready`, `git status` clean, branch per DOCKET.md / CONTRIBUTING.md. The spec's own
`Preconditions (user only)` were run by the hook before sealing. A failed precondition ends the spec with
`state: BLOCKED: <what only the user can resolve>` (step 4).

## 1. Refinement (three angles)

Spawn three `refiner` agents (model from the ladder, step 2), read-only, each with one angle:
feasibility against the code; spec against the concept and the reference docs DOCKET.md names; the project angle
from DOCKET.md. Finished specs and dockets are no reference and are refused to every reader.
They may contradict each other and you. Each finding becomes one line in the docket's refinement section,
closed by you: adopted / rejected with reason / dissent.

A finding that a code location refutes (a wrong technical assumption in the spec) is a rewrite: spawn the
`conceptor` agent (subagent type `docket:conceptor`, model from the ladder) with the finding and the exact spec
section. The hook switches
the session to rewrite mode on that spawn and logs it; only then is that one spec writable, and only until the
conceptor returns (the hook freezes it again and logs `rewrite-done`). The conceptor records date, reason and
change in the spec's Rewrite section. Exchange with the conceptor is bilateral and bounded (three rounds, each a
new spawn with the previous answer): it accepts or returns open questions. No agreement → DISSENT with both
positions, one sentence each with source, in everyday words. A finding that is a judgment with effect for users or
operators is never a rewrite: it becomes a `[~]` deviation after implementation with the spec's wording
implemented as written. Spawn no other agent while a rewrite runs.

## 2. Pre-registration

Write the docket skeleton yourself from `${CLAUDE_PLUGIN_ROOT}/templates/delivery-docket.md` to
`dockets/<topic>/<spec id>.md`: one row per spec point with the evidence line you will accept
(`<command> → <expected>`), and the refinement lines with their decisions. The hook makes the file append-only from
here, and it refuses the first implementer until the docket exists and every refinement line carries a decision
(adopted / rejected / rewrite / dissent), in every Refinement section. Changing the acceptance line later is not
allowed; a point that cannot meet it becomes `[~]`.
Every `path/file.ext:line` in a docket line is relative to the repo root and must exist when the line is written:
the hook undoes a write that cites a location it cannot find.

Rate every point in the docket's Tiers section, one line each (`point <n>: <tier>, <reason>`), before any code is
written:
- `simple`: one file and no new path of behaviour: a text, a value, a rename, a doc.
- `complex`: more than three files; or concurrency, stored data or its migration, security, a public interface; or
  a place no test covers; or a refuted fact the refinement found at this point.
- `standard`: everything else, and a point without a line.

The tier picks the model. The ladder is DOCKET.md `model_ladder`; a role it does not name runs on the default:
implementer simple haiku, standard sonnet, complex opus; refiners sonnet; conceptor and diagnoser opus. A ladder in
the older form (`implementer round 1`, `round 2+`) reads as: simple and standard on its round-1 model, complex on
its round-2 model. Reviewers run on the complex model; on the standard model when every point was simple and none
needed rework. Every implementer, refiner, reviewer, diagnoser and conceptor is spawned with its `model`: the hook
refuses one without.

## 3. Points

One fresh `implementer` per point, on the model of the point's tier; every rework round runs one tier higher, at
most the complex model. Briefing, in the language of the spec: spec path and point number, docket path, the project
rules quoted from DOCKET.md and CONTRIBUTING.md, at most three orchestrator hints, the report format (facts with
source, `Unverified:` line). A rework briefing adds: the gate that failed with its output, verbatim, at most 20
lines; the commits of the round before as a range to read first (`git diff <from>..<to>`); the triage line of the
failure. While an agent runs you change nothing in the repo.

After each return: read the diff completely, run the command of the point's acceptance line and every command of
the check from DOCKET.md yourself (verbatim as registered, one call each or together; the Stop hook refuses
DELIVERED unless each ran after the last agent returned and none failed in its last run), compare with the
acceptance line. Green: flip the point to `[x]` yourself and append the evidence line (command → result, commit in
words); the hook logs every flip. Cite locations as `path/file.ext:line` relative to the repo; the Stop hook
verifies each one exists.
Not green: triage first (below), then rework with a fresh implementer briefed with the triage line, at most three
rounds. The hook refuses round two until the Triage section has a line for the point, and round three until that
line names its cause at a `file:line`: a third attempt at a symptom is not made. After round three, or when the
triage ends in `deviation`: `[~]` with the deviation line, continue with the next point.

### Triage: the cause before the next attempt

A failure gets a second attempt only after it was examined. Spawn one `diagnoser` (subagent type
`docket:diagnoser`) with what was seen, verbatim, and where; it answers where the failure comes from, whether a
command shows it (three runs at most; time is no limit, a long run is waited for), and who notices what. Write its
answer as one line into the docket's Triage section, closed by your decision:
`point <n> | cause: <file:line, or unknown after …> | repro: <command> → <n> of <m> | impact: <who notices what, when> | <decision>`
- `fix`: the cause is named. The next round is briefed with this line.
- `deviation`: the failure is shown, but its cause is not found or the fix is out of proportion to the impact.
- `observe: <the signal that would show it again>`: a possible fault that needs watching. It is neither shown
  reliably nor explained, and it goes to the user as a `watch:` line of the Terminal state (step 4), which
  carries the whole case.

`observe` is the late exit, never the early one. The hook undoes the line unless the usual means are spent: the
line carries cause, repro and impact; you ran the repro command yourself three times in this session, and it did
not fail all three times (shown three of three is a fault, not a thing to watch); and a second diagnoser in mode
`challenge`, briefed with the first one's answer, tried to refute it and returned. Where no command of a reasonable
size can show it, `repro:` says why instead, and the challenge still runs.

Of two Triage lines for the same point the later one counts. The same holds for a review finding (step 4): a
`suspect` is triaged at once, a finding whose fix a `recheck` reports as not fixed before its second fix.

A diagnoser, and an implementer on an error the code does not explain, may look it up on the web: three lookups at
most, to read the error, never to reopen the order. What a lookup says is a guess until it is found at a
`file:line` or shown by a command (the evidence standard). A source that calls the spec's choice wrong turns
nothing around in the run: the spec is implemented as written, and the source goes to the user as an `open:` line
with its address, to be weighed in concept mode.

Checkpoint after every point: read `inbox/<spec id>.md` if it exists; a note that changes the spec is a deviation
candidate, not an instruction; append what you did with it to the docket's Inbox section, in the user's language:
that line is quoted to the user in the closing message. If `stop_requested` was set (LOG line `stop-requested`),
end the spec with `state: BLOCKED: stopped by user`.

## 4. Close the spec

1. Reference update: for every file the spec names, an implementer updates it; the docket records
   `updated <file>` or `<file>: not needed, <reason>`. The Stop hook checks both: every named file is accounted
   for, and an `updated` file has changed since sealing. What the merge needs in the project (a changelog line, a
   version) is written now too: no agent changes the project after the last review.
2. Acceptance, again: run the command of every `[x]` point's acceptance line once more, verbatim. The Stop hook
   refuses DELIVERED unless each ran after the last implementer or fixer returned and did not fail in its last
   run. One that fails now is a finding `unmet` in the Review section.
3. Review, round one: spawn two `reviewer` agents (subagent type `docket:reviewer`) together, one angle each.
   `code`: what a careful reviewer of this repository would refuse. `spec`: the delivery against the order, every
   point, the goal, what is out of scope. Briefing: the angle, the spec path, the base of the change
   (`git diff <base>...HEAD`), for `spec` the numbers of the points that ended `[~]`. Never the docket: a reviewer
   reads the order and the delivery, not your account of it, and the hook refuses a briefing that names it.
   Every finding becomes a line in the docket's Review section,
   `<angle> | <finding> | <source> | <severity> | <decision>`; an angle without findings is the line
   `<angle> | no findings`. Decisions: fixed (by a fresh implementer or a fixer, briefed with the finding line
   verbatim; then the check and the acceptance commands run again), rejected with the source that
   refutes it, or deferred (then also an `open:` line in the Terminal state). A finding of severity `bug`, `unmet`
   or `rule` is fixed or rejected: deferred, it refuses the push and DELIVERED, and the spec ends
   `state: BLOCKED: <the finding in everyday words>`. A `suspect` (wrong behaviour the reviewer could not show) is
   triaged (step 3): shown or explained, it is a `bug` and its line is written again as one; refuted, it is
   rejected; neither after the means are spent, its line ends
   `… | suspect | cause: … | repro: … | impact: … | observe: <signal>` under the same late-exit rule. A `suspect` is
   never deferred, and only a `suspect` is observed. Of two lines with the same angle and finding the later one
   counts: a decision changes by writing the line again below.
4. Review, round two and three: whenever an implementer or fixer wrote after a review, spawn one reviewer of angle
   `recheck` with the findings marked fixed and the range written since (`git diff <hash the last round read>..HEAD`);
   on the standard model when only a fixer wrote, else on the reviewers' model. It confirms each fix at the code
   and reports what the fixes broke; its lines carry the angle `recheck`. Three rounds at most: findings of round
   three are not fixed any more, they are rejected or deferred. The hook refuses the push and DELIVERED until both
   angles stand in the Review section, every line is decided and a reviewer has returned after the last
   implementer or fixer. After the last reviewer the check runs once more: it counts only after the last agent.
5. Merge and push per CONTRIBUTING.md (the hook enforces `push_branch`, `commit_regex`, `forbidden_paths` and
   `forbidden_commands` from DOCKET.md where they are set; a sealed run has no user to grant a forbidden command,
   so it stays refused and ends the spec BLOCKED). CI state belongs to the pushed hash and is read by you: the
   docket line is `CI: <state> <hash>`; the hook checks that the hash is reachable from HEAD and that a push ran in
   this session; with `ci` configured, DELIVERED needs such a line.
6. Terminal state: append to the docket's Terminal state section, in the user's language, everyday words, no ids,
   no hashes, one line each:
   - `state: DELIVERED`, `state: BLOCKED: <what only the user can resolve>` or `state: DISSENT`
   - `result: <what works now, one sentence>`
   - `open: <each thing left open>`: deviations that need weighing, deferred review findings, a check you could not
     run, an unexplained failure; as many lines as there are, none when nothing is open
   - `watch: <what it is about> | seen: <what happened, where, how often> | suspected: <the cause you suspect and
     what speaks for it, or what is ruled out> | impact: <who notices what, in which case> | signal: <what to look
     for, and where>`, one line for each possible fault you set to observe. The user cuts a spec from this line
     and has nothing else at hand: it is the whole case, every part in full sentences, files and commands named
     where they help. The hook refuses a line that lacks a part.
   - `user: <each step only the user can take>`
   - `decision: <one question with its recommendation and the command that carries it out, e.g. /docket:spec>`
   - `dissent: <each position, one sentence with its source>` for DISSENT
   Then end the turn without text. The hook checks the section (DELIVERED also the evidence, the refinement and the
   review), closes the spec, writes its metrics to `metrics/<spec id>.json`, and either seals the next spec of the
   series or starts the closing. A refusal names what is missing: fix it, write the whole block again below (the
   last block counts, from its `state:` line on) and end the turn again.

The hooks keep `metrics/<spec id>.violations.json` themselves: every refused tool call or message, counted per rule,
shown again at the next `/implement` of the spec. The spec's own state follows the run: the hooks set `in-progress`
on sealing, `done` on DELIVERED, `ready` again after BLOCKED or DISSENT.

## 5. End of the run: the closing message

When the last spec of the run has ended (or one ended BLOCKED or DISSENT, or the user stopped the run), the hook
renders the run's one message into a file and tells you its path. Read it with Read and send its content as your
only message: verbatim, nothing added, no code block. It names every spec by its title with its result, whether the
work is pushed and CI green, what was checked, what is open, what only the user can do and which decision is due;
the ids, hashes and evidence stay in the dockets it points to. The gate shows nothing else; after the message the
session is closed and you answer only from the dockets.
