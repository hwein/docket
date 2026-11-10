"""Stop hook, layer 1 (deterministic). Layer 2 is the optional prompt-type judge (templates/judge-hook.json), which
is suspended during a sealed run; outside it, its rejection goes to Claude, which answers with an empty turn, and
this hook counts the verdicts from the transcript at the next Stop.

sealed, rewrite (a run): no text reaches the user. A Stop while agents run (background_tasks, or an agent whose
         notification has not reached the model) is a wait and passes unchecked. Otherwise the spec must have ended
         in its docket's `## Terminal state` section (`state:` DELIVERED | BLOCKED | DISSENT, `result:`, `open:`,
         `user:`, `decision:`); DELIVERED needs the evidence (check after the last agent, citations, reference docs,
         CI hash, refinement decided, review decided). An accepted end closes the spec, writes its metrics and either
         seals the next spec of the series (a short order on stderr) or starts the closing.
closing: the hook rendered the run's one message from the dockets and git (docket_lib.render_closing) into a file;
         the orchestrator sends it verbatim. On a match the session closes and the judge returns; after three
         misses the gate shows the rendered text itself and the session closes.
concept, spec, init: human register. No hashes, no ids, at most one question, no hedge words, no option menus,
         no roadmap promises, length limit. A blocker is said once (M4).
closed:  short answers only, no questions, no proposals.
Every message outside a run ends with a fresh stamp: within five minutes of the real time or equal to the clock
line the hooks last gave the model (M7).

A refusal is sent back once: a second Stop right after this hook's own refusal (state `refused`, cleared by any
tool call) is let through and logged; in a run it tells the user in one line that the run stopped.
The user has read a message by the time this hook runs, so a refusal never asks to repeat a message the user saw;
only a message the display gate (message_display.py) held back is written again in full.
"""
import json
import os
import posixpath
import re
import sys
import time
from datetime import datetime, timedelta

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (FILE_LINE, JUDGE_ACK, Paths, acceptance_problems, agent_returns, box_questions,
                        check_commands, claim_lock, review_run_problem,
                        closing_path, collect_judge, frontmatter,
                        config_values, hook_outputs, language_problem, load_state, log_event, log_lines, log_time,
                        message_text, missing_citations, normalized_lines, notice, observed, point_counts,
                        review_lines, triage_lines, watch_parts, WATCH_PARTS,
                        position, previous_turn_texts, read_input, read_text, refinement_problem, refuse,
                        release_lock, render_closing, resume_judge, review_problem, save_display, save_state, say,
                        section_text, session_live, lock_holder, set_context, set_spec_state, settled_display,
                        spec_title, take_lock, terminal_base, terminal_section, text_of, transcript_blocks,
                        waiting, write_atomically, write_metrics)

TERMINAL = re.compile(r"^DOCKET (DELIVERED|BLOCKED|DISSENT)\b", re.IGNORECASE)
WATERMARK = re.compile(r"^stamp:\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})(?!\d)", re.IGNORECASE)
# A hash stands on its own: hex runs inside a path, a UUID, an id or a file name are not hashes
# (a Windows scratchpad path tripped the rule in luradb-web-client, 2026-11-05).
HASH = re.compile(r"(?<![0-9a-zA-Z/\\_.\-])[0-9a-f]{7,40}(?![0-9a-zA-Z/\\_\-]|\.\w)")
# In a message also mixed digits and letters: `1234567` is a number, `defaced` a word.
HASH_IN_TEXT = re.compile(r"(?<![0-9a-zA-Z/\\_.\-])(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}"
                          r"(?![0-9a-zA-Z/\\_\-]|\.\w)")
# An id stands on its own as well: the id inside a file name or a link target is part of a path, the title is in
# the text (legacy session 2026-11-05: a link to the concept file was held back and the link was lost).
ID_IN_TEXT = re.compile(r"(?<![0-9a-zA-Z/\\_.\-])[cs]-[0-9a-f]{8}(?![0-9a-zA-Z_\-]|\.\w)")
# A quoted hook answer: the quote in a line that names a hook.
QUOTE = re.compile(r"[„“\"]([^„“”\"\n]{12,240})[“”\"]")
HOOK_WORD = re.compile(r"\bhooks?\b", re.IGNORECASE)
CI_LINE = re.compile(r"\bCI\b[^\n]*", re.IGNORECASE)
HEDGES = re.compile(r"\b(probably|likely|seems?|appears?|presumably|i think|i believe|might|vermutlich|"
                    r"wahrscheinlich|scheint|offenbar|wohl|könnte|vielleicht)\b", re.IGNORECASE)
OPTIONS = re.compile(r"\b(option [a-c1-3]\b|alternatively|or alternatively|alternativ)", re.IGNORECASE)
ROADMAP = re.compile(r"\b(next,? i will|afterwards i will|then i will|als n[äa]chstes werde ich|danach werde ich|"
                     r"i'll (then|now)|let me know|sag bescheid)\b", re.IGNORECASE)
PROPOSAL = re.compile(r"\b(i suggest|i propose|should we|shall i|vorschlag|soll ich|wollen wir)\b", re.IGNORECASE)
SELF = re.compile(r"\b(sorry|apologi[sz]e|my mistake|my fault|took me|i spent|this took|hours of work|"
                  r"entschuldig\w*|tut mir leid|mein fehler|leider)\b", re.IGNORECASE)
# Sections of the docket whose citations are checked at DELIVERED; refinement lines were checked when written.
EVIDENCE_SECTIONS = ("Evidence", "Deviations", "Reference update")
BLOCKER =re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?blocker\b", re.IGNORECASE)
BLOCKER_UNCHANGED = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?blocker\s+(?:unver[äa]ndert|unchanged)(?:\*\*)?[.!]?\s*$",
                               re.IGNORECASE)
TOLERANCE = timedelta(minutes=5)
MAX_LINES = {"concept": 30, "spec": 30, "init": 40, "closed": 8}
CLOSING_TRIES = 3
# The display gate holds messages back in these modes; in a run it replaces every text with the hook's status
# line, and in closing it shows only the rendered closing message.
GATED_MODES = ("concept", "spec", "init", "closed", "sealed", "rewrite", "closing")


def main():
    hook_input = read_input()
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    if getattr(state, "unreadable", False):
        return 0  # the state file is closed to this hook: no mode is known, nothing is checked or counted
    mode = state.get("mode", "none")
    if mode == "none" and not os.path.isfile(paths.config):
        return 0  # project not set up: the hooks only observe
    set_context(paths, state.get("spec", ""))
    if os.path.isfile(paths.config):
        collect_judge(paths, session_id, state, hook_input.get("transcript_path"))
    if mode in ("sealed", "rewrite"):
        return run_stop(paths, session_id, state, hook_input)
    if mode == "closing":
        return closing_stop(paths, session_id, state, hook_input)
    # After a judge verdict Claude answers with an empty turn: in closed mode that turn ends the closing message's
    # turn and is no message of its own.
    text = message_text(hook_input, strict=(mode == "closed"))
    if text is None:
        return 0
    # stop_hook_active stays true for the rest of a turn any Stop hook once blocked; only this hook's own refusal
    # makes the next Stop a second pass (luradb-web-client 2026-11-06: the judge's verdicts used up stop.py's one
    # refusal).
    second_pass = bool(hook_input.get("stop_hook_active")) and bool(state.get("refused"))

    display = settled_display(paths, session_id) if mode in GATED_MODES else None
    clock = state.get("clock", "")
    if display and display["hidden"]:
        # The gate held a message back: the user has seen nothing, so here a full rewrite is right.
        held = sorted({problem for entry in display["hidden"] for problem in entry.get("problems", [])})
        display.update({"hidden": [], "retry": True})
        save_display(paths, session_id, display)
        mark_refused(paths, session_id, state)
        return refuse("[docket] Your last message was held back and never shown to the user: " + "; ".join(held)
                      + ". Write it again in full, corrected, and end it with the stamp line fetched now. This "
                      "overrides any instruction in this feedback to send correction lines, `[docket] noted` or an "
                      "empty answer.")

    if is_judge_ack(text):
        problems = []  # the acknowledgement of a judge verdict; the display gate hid it
    elif mode == "none":
        # No command active, but the project is set up: the stamp is a rule of every session.
        problems = watermark_problems(lines_of(text), clock=clock)
    elif mode == "closed":
        problems = closed_problems(text, clock, paths)
    else:
        transcript = hook_input.get("transcript_path")
        problems = (human_problems(text, mode, clock, paths) + repeat_problems(text, transcript)
                    + box_problems(text, transcript) + hook_quote_problems(text, transcript))
    if any("stamp is" in problem for problem in problems):
        state.update({"degraded": True, "read": []})  # a lost watermark means the rules blurred
        save_state(paths, session_id, state)

    if not problems or second_pass:
        if display is not None and display["retry"]:
            display["retry"] = False
            save_display(paths, session_id, display)
        if state.get("refused"):
            state["refused"] = False
            save_state(paths, session_id, state)
        if problems:
            log_event(paths, session_id, state.get("spec", ""), "stop-forced", "; ".join(problems)[:120])
        return 0
    if display is not None:
        display["retry"] = True  # the answer to this refusal is shown, whatever it holds
        save_display(paths, session_id, display)
    mark_refused(paths, session_id, state)
    only_stamp = all("stamp" in problem for problem in problems)
    if only_stamp:
        # The message itself stands; only the watermark is missing or stale. Never repeat the message.
        return refuse("[docket] " + "; ".join(problems) + ". Fetch the time now and end the turn with the stamp "
                      "line alone, nothing else.")
    # The user has read the message: never repeat it, correct the broken sentences only.
    return refuse("[docket] The user has already read your last message: do not repeat or rewrite it. It broke: "
                  + "; ".join(problems) + ". Answer with one line `[docket] Korrektur: <the corrected sentence>` "
                  "per broken rule, in the language of the message, then the stamp line fetched now, nothing else.")


def mark_refused(paths, session_id, state):
    state["refused"] = True
    save_state(paths, session_id, state)


# --- a run ---------------------------------------------------------------------

def run_stop(paths, session_id, state, hook_input):
    """A Stop in a sealed run. Waiting for agents ends a turn without text and is not checked (V4); otherwise the
    spec must have ended in its docket (V3)."""
    transcript = hook_input.get("transcript_path")
    spec_id = state.get("spec", "")
    terminal = terminal_section(paths, spec_id, skip=int(state.get("terminal_base", 0) or 0))
    if waiting(hook_input, transcript, bool(terminal["kind"])):
        state["waiting"] = time.time()  # a run that waits for its agents stays live for other sessions
        save_state(paths, session_id, state)
        return 0
    text = text_of(hook_input.get("last_assistant_message"))
    problems = []
    if text and TERMINAL.match(lines_of(text)[0]):
        problems.append(say(paths, "docket_message"))
    if terminal["kind"]:
        problems += terminal_problems(terminal, spec_id, paths, hook_input)
        if not problems:
            return end_spec(paths, session_id, state, terminal["kind"], transcript)
    elif not problems:
        problems.append(say(paths, "no_terminal"))
    if bool(hook_input.get("stop_hook_active")) and state.get("refused"):
        # The second Stop right after this hook's refusal, with no tool call between: the run has stopped.
        state.update({"refused": False, "halted": True})
        save_state(paths, session_id, state)
        log_event(paths, session_id, spec_id, "stop-forced", "; ".join(problems)[:120])
        title = spec_title(paths, spec_id) or spec_id
        rest = ", ".join([spec_id] + list(state.get("queue") or []))
        print(json.dumps({"systemMessage": "[docket] " + say(paths, "halted", title=title, specs=rest)},
                         ensure_ascii=True))
        return 0
    mark_refused(paths, session_id, state)
    return refuse("[docket] " + say(paths, "not_finished", problem="; ".join(problems)))


def _cut(text, limit=50):
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def terminal_problems(terminal, spec_id, paths, hook_input):
    """The end of a spec as the docket states it. Its lines go into the run's closing message, so they are in the
    user's language and carry no ids or hashes; DELIVERED needs the evidence."""
    kind = terminal["kind"]
    problems = []
    if not terminal["result"]:
        problems.append("the Terminal state needs a `result:` line: what works now, one sentence in everyday words")
    if kind == "blocked" and not (terminal["reason"] or terminal["user"]):
        problems.append("BLOCKED names what only the user can resolve: `state: BLOCKED: <that>` or a `user:` line")
    if kind == "dissent" and not (terminal["reason"] or terminal["dissent"]):
        problems.append("DISSENT names both positions: `dissent:` lines, one sentence each with its source")
    if any("/docket:" not in line for line in terminal["decision"]):
        problems.append("a `decision:` line asks one question with its recommendation and names the command that "
                        "carries it out (`/docket:spec`, `/docket:concept`)")
    body = read_text(paths.docket_file(spec_id))
    watched = [line for line in list(triage_lines(body).values()) + review_lines(body) if observed(line)]
    if watched and not terminal["watch"]:
        problems.append(f"the docket sets {len(watched)} possible fault(s) to `observe` and the Terminal state has no "
                        "`watch:` line: say for each what was seen and the signal that would show it again")
    for value in terminal["watch"]:
        parts = watch_parts(value)
        missing = [key for key in WATCH_PARTS if not parts[key]]
        if missing or not parts["subject"]:
            problems.append("a `watch:` line is the whole case for the user, who cuts a spec from it and has nothing "
                            "else at hand: `watch: <what it is about> | seen: <what happened, where, how often> | "
                            "suspected: <the cause you suspect and what speaks for it> | impact: <who notices what, in "
                            "which case> | signal: <what to look for, and where>`; `" + _cut(value) + "` has no "
                            + ", ".join(f"`{key}:`" for key in missing or ["subject"]))
    prose = "\n".join([terminal["result"], terminal["reason"]] + terminal["open"] + terminal["watch"]
                      + terminal["user"] + terminal["decision"] + terminal["dissent"])
    if HASH_IN_TEXT.search(prose):
        problems.append("no commit hashes in the Terminal state lines: they are read by the user; the hash stays in "
                        "the Evidence section")
    if ID_IN_TEXT.search(prose):
        problems.append("no spec or concept ids in the Terminal state lines: name the title")
    problems += [p.replace("the message is", "the Terminal state is") for p in language_problem(prose, paths)]
    if kind != "delivered":
        return problems
    docket = paths.docket_file(spec_id)
    if docket is None:
        return problems + ["the docket file does not exist"]
    body = read_text(docket)
    open_points = point_counts(body)[" "]
    if open_points:
        problems.append(f"{open_points} docket point(s) still open `[ ]`; each needs `[x]` with evidence or `[~]` "
                        "with a deviation line")
    problems += provenance_problems(body, spec_id, paths, hook_input)
    for problem in (refinement_problem(paths, spec_id), review_problem(paths, spec_id),
                    review_run_problem(paths, spec_id, hook_input.get("transcript_path"))):
        if problem:
            problems.append(problem.replace("[docket] ", "", 1))
    return problems


def end_spec(paths, session_id, state, kind, transcript):
    """The spec is closed: LOG, spec state, lock, metrics. After DELIVERED the series goes on with its next spec,
    sealed here and announced to the model in one short order; otherwise, or when the series is done, the closing
    begins."""
    spec_id = state.get("spec", "")
    release_lock(paths, spec_id)
    set_spec_state(paths, spec_id, "done" if kind == "delivered" else "ready")
    write_metrics(paths, spec_id, transcript, kind)  # before the LOG line: its commit carries the metrics
    log_event(paths, session_id, spec_id, kind, "spec closed")
    state.setdefault("outcomes", {})[spec_id] = kind
    queue = [s for s in (state.get("queue") or []) if s]
    if kind == "delivered" and not state.get("stop_requested"):
        while queue:
            following = queue.pop(0)
            if (frontmatter(paths.spec_file(following) or "").get("state", "").lower() not in ("ready", "in-progress")
                    or claim_lock(paths, following, session_id)):
                continue  # sealed or finished elsewhere meanwhile: named as not started at the end
            state.update({"mode": "sealed", "spec": following, "queue": queue, "refused": False,
                          "terminal_base": terminal_base(paths, following)})
            save_state(paths, session_id, state)
            log_event(paths, session_id, following, "sealed", "from queue")
            set_spec_state(paths, following, "in-progress")
            i, n = position(state)
            order = say(paths, "handover", i=i, n=n, title=spec_title(paths, following) or following, spec=following)
            return notice("[docket] " + order)
    if queue:
        log_event(paths, session_id, spec_id, "queue-dropped", ", ".join(queue))
    state["queue"] = []
    return start_closing(paths, session_id, state)


def start_closing(paths, session_id, state):
    log_event(paths, session_id, state.get("spec", ""), "closing")  # first: a work dir of its own is committed
    text = render_closing(paths, state)
    path = closing_path(paths, session_id)
    try:
        os.makedirs(paths.state_dir, exist_ok=True)
        write_atomically(path, text)
    except OSError:
        pass
    state.update({"mode": "closing", "closing": {"file": path, "attempts": 0}, "refused": False,
                  "clock": text.rstrip().splitlines()[-1][len("stamp: "):]})
    save_state(paths, session_id, state)
    return notice("[docket] " + say(paths, "closing_order", path=path))


def closing_stop(paths, session_id, state, hook_input):
    """The run's one message: the rendered text, verbatim. A miss is asked for again (the gate showed nothing);
    after three the gate showed the rendered text itself and the session closes anyway."""
    info = state.get("closing") or {}
    path = info.get("file") or closing_path(paths, session_id)
    rendered = read_text(path)
    display = settled_display(paths, session_id)
    if display["hidden"] or display["retry"]:
        display.update({"hidden": [], "retry": False})
        save_display(paths, session_id, display)
    text = text_of(hook_input.get("last_assistant_message"))
    if text is not None and normalized_lines(text) == normalized_lines(rendered):
        return finish_run(paths, session_id, state, forced=False)
    attempts = int(info.get("attempts", 0)) + 1
    if attempts >= CLOSING_TRIES:
        log_event(paths, session_id, state.get("spec", ""), "stop-forced", "closing message sent by the gate")
        if text is None and rendered:
            # No text the gate could replace: the user gets the rendered message from the hook.
            print(json.dumps({"systemMessage": rendered.rstrip()}, ensure_ascii=True))
        return finish_run(paths, session_id, state, forced=True)
    info.update({"file": path, "attempts": attempts})
    state["closing"] = info
    save_state(paths, session_id, state)
    return refuse("[docket] " + say(paths, "closing_again", path=path, problem=closing_difference(text, rendered)))


def closing_difference(text, rendered):
    if text is None:
        return "no message"
    sent, wanted = normalized_lines(text), normalized_lines(rendered)
    for index, line in enumerate(wanted):
        if index >= len(sent) or sent[index] != line:
            return f"line {index + 1} should read `{line[:60]}`"
    return f"{len(sent) - len(wanted)} line(s) too many"


def finish_run(paths, session_id, state, forced):
    outcomes = state.get("outcomes") or {}
    state.update({"mode": "closed", "terminal": list(outcomes.values())[-1] if outcomes else "", "refused": False})
    save_state(paths, session_id, state)
    log_event(paths, session_id, state.get("spec", ""), "run-closed", "forced" if forced else "")
    if resume_judge(paths, session_id):
        log_event(paths, session_id, state.get("spec", ""), "judge-resumed")
    return 0


def is_judge_ack(text):
    lines = lines_of(text)
    return bool(lines) and lines[0].lower().rstrip(".") == JUDGE_ACK and all(
        line.lower().rstrip(".") == JUDGE_ACK or WATERMARK.match(line) for line in lines)


def is_stub(text):
    """A message with nothing in it for the user: only acknowledgement lines, `Blocker unverändert` and a stamp. It is
    what an answer the harness forced after an empty turn consists of; the display gate never shows it."""
    lines = lines_of(text)
    return bool(lines) and all(line.lower().rstrip(".") == JUDGE_ACK or WATERMARK.match(line)
                               or BLOCKER_UNCHANGED.match(line) for line in lines)


def lines_of(text):
    return [line.rstrip() for line in text.strip().splitlines() if line.strip()]


def provenance_problems(body, spec_id, paths, hook_input):
    """The evidence standard, checked at the one moment it matters: a Terminal state DELIVERED.

    1. Every command of the project's check (DOCKET.md `check`, as many as the project has) ran in this session
       after the last agent returned, alone or together with others, and its last run did not fail.
    2. Every file:line the docket cites exists and has that many lines.
    3. Every reference doc the spec names is marked in the docket as updated (and the file changed
       after sealing) or as not needed.
    4. The command of every delivered point's acceptance line ran after the last implementer or fixer returned
       and did not fail in its last run (V16).
    """
    problems = []
    blocks = transcript_blocks(hook_input.get("transcript_path"))
    commands = check_commands(paths)
    if commands and blocks:
        last_return = max(agent_returns(hook_input.get("transcript_path")).values(), default=-1)
        failed = {b.get("tool_use_id") for _, role, b in blocks
                  if role == "user" and b.get("type") == "tool_result" and b.get("is_error")}
        shell = [(i, b) for i, role, b in blocks if role == "assistant" and b.get("type") == "tool_use"
                 and b.get("name") in ("Bash", "PowerShell")]
        for command in commands:
            # One call per command or one call for several: what counts is the last call that contains it.
            runs = [(i, b) for i, b in shell if command in str((b.get("input") or {}).get("command", ""))]
            if not runs:
                problems.append(f"the check `{command}` never ran in this session; run it yourself")
            elif last_return > runs[-1][0]:
                problems.append(f"the check `{command}` last ran before the last agent returned; run it again yourself")
            elif runs[-1][1].get("id") in failed:
                problems.append(f"the check `{command}` failed in its last run; the point is not delivered")
    problems += acceptance_problems(paths, spec_id, body, hook_input.get("transcript_path"))
    # Citations are checked when a docket line is written (post_tool_use, the write is undone otherwise); here only
    # the evidence sections, so an old refinement line never forces an in-place edit of an append-only docket
    # (luradb-web-client 2026-11-06: three DELIVERED attempts, seven append-only violations).
    evidence = "\n".join(section_text(body, title) for title in EVIDENCE_SECTIONS)
    missing = missing_citations(evidence if evidence.strip() else body, paths)
    if missing:
        problems.append("cited locations that do not exist: " + ", ".join(missing))
    problems += reference_problems(body, spec_id, paths)
    problems += ci_problems(body, paths, blocks)
    return problems


def ci_problems(body, paths, blocks):
    """A CI state belongs to a pushed hash: every hash on a CI line of the docket is reachable from HEAD,
    and a `git push` ran in this session. With `ci` configured in DOCKET.md, DELIVERED needs such a line."""
    import subprocess
    problems = []
    hashes = []
    for line in CI_LINE.findall(body):
        hashes += HASH.findall(line)
    ci = config_values(paths).get("ci", "")
    if ci and ci.lower() != "none" and not hashes:
        problems.append("DOCKET.md configures CI: the docket needs a `CI: <state> <pushed hash>` line per delivered point")
    if not hashes:
        return problems
    pushed = any(role == "assistant" and b.get("type") == "tool_use" and b.get("name") in ("Bash", "PowerShell")
                 and re.search(r"\bgit\s+push\b", str((b.get("input") or {}).get("command", ""))) for _, role, b in blocks)
    if blocks and not pushed:
        problems.append("the docket cites a CI hash but no `git push` ran in this session")
    for found in sorted(set(hashes)):
        try:
            result = subprocess.run(["git", "-C", paths.root, "merge-base", "--is-ancestor", found, "HEAD"],
                                    capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            problems.append(f"CI hash {found} is not reachable from HEAD: the CI state belongs to another commit")
    return problems


def reference_problems(body, spec_id, paths):
    spec = paths.spec_file(spec_id)
    if not spec:
        return []
    try:
        with open(spec, encoding="utf-8-sig", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return []
    match = re.search(r"^##\s+Reference update\b.*?$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    files = []
    for line in match.group(1).splitlines():
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")] if line.strip().startswith("|") else []
        if len(cells) >= 2 and cells[0] and cells[0].lower() not in ("file", "none") and not re.fullmatch(r":?-+:?", cells[0]):
            files.append(cells[0])
    sealed_at = max((log_time(line) or 0 for line in log_lines(paths, spec_id) if " sealed " in line), default=0)
    problems = []
    lowered = body.lower()
    for name in files:
        key = name.lower()
        updated = re.search(r"updated\s+`?" + re.escape(key), lowered)
        not_needed = re.search(r"not needed[^\n]*" + re.escape(key), lowered) or re.search(
            re.escape(key) + r"[^\n]*not needed", lowered)
        if not updated and not not_needed:
            problems.append(f"reference doc {name} named in the spec is neither `updated` nor `not needed: reason` in the docket")
            continue
        if updated:
            path = next((c for c in (posixpath.join(paths.root, name), name) if os.path.isfile(c)), None)
            if path is None:
                problems.append(f"reference doc {name} is marked updated but does not exist")
            elif sealed_at and os.path.getmtime(path) < sealed_at:
                problems.append(f"reference doc {name} is marked updated but has not changed since sealing")
    return problems


def watermark_problems(lines, require_spec=None, clock=""):
    """Every message in every mode ends with a stamp from the clock: `stamp: YYYY-MM-DD HH:MM` (sealed runs add
    the spec id and the docket counts). Accepted within TOLERANCE of the real time, or equal to the clock line
    the hooks last gave the model (a long turn without a tool call). A stale stamp means the rules blurred."""
    problems = []
    stamp = WATERMARK.match(lines[-1]) if lines else None
    if stamp is None:
        problems.append("the last line must be `stamp: YYYY-MM-DD HH:MM` with the current time (the latest "
                        "`[docket] clock:` line, or fetched now)"
                        + (f" (sealed: `stamp: YYYY-MM-DD HH:MM {require_spec} <delivered>/<points>`)" if require_spec else ""))
        return problems
    try:
        when = datetime(*(int(stamp.group(i)) for i in range(1, 6)))
        deviation = abs(datetime.now() - when)
        if deviation > TOLERANCE and when != clock_time(clock):
            problems.append(f"the stamp is {round(deviation.total_seconds() / 60)} minutes off the real time: "
                            "take the latest `[docket] clock:` line or fetch the time, do not remember it; "
                            "then check whether other rules blurred too")
    except ValueError:
        problems.append("the stamp is not a valid date and time")
    return problems


def clock_time(clock):
    try:
        return datetime.strptime(clock, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def blocker_key(line):
    return " ".join(re.sub(r"^\s*(?:[-*]\s+)?", "", line).replace("**", "").lower().split())


def repeat_problems(text, transcript_path):
    """A blocker is said once (M4). On 2026-11-05 the same blocker stood in seven messages in a row and the
    user read the seventh as a refusal. A `Blocker` line that stood, text-identical, in the previous message
    is refused; afterwards only new facts or the one line `Blocker unverändert`."""
    current = [line for line in lines_of(without_stamp(text)) if BLOCKER.match(line) and not BLOCKER_UNCHANGED.match(line)]
    if not current:
        return []
    previous = {blocker_key(line) for earlier in previous_turn_texts(transcript_path)
                for line in lines_of(earlier) if BLOCKER.match(line)}
    repeated = [line for line in current if blocker_key(line) in previous]
    if not repeated:
        return []
    return [f"the blocker `{blocker_key(repeated[0])[:50]}` already stood in your previous message: say it once; "
            "after that only new facts or the one line `Blocker unverändert`"]


def prose_of(text):
    """The text without URLs and code: a ? there asks nothing."""
    return re.sub(r"`[^`\n]*`|https?://\S+", "", text)


def box_problems(text, transcript_path):
    """The next-step box of the desktop app asks a question the judge never sees: it counts toward the one
    question. Legacy session 2026-11-05: the box asked about the test server, the text opened a second approval."""
    asks = box_questions(transcript_path)
    if not asks:
        return []
    if len(asks) > 1 or "?" in prose_of(without_stamp(text)):
        return [f"at most one question: the next-step box already asks `{asks[-1][:60]}`; the text asks nothing more"]
    return []


def hook_source():
    """The text of docket's own hooks, whitespace-normalised: what a hook can say."""
    here = posixpath.dirname(os.path.abspath(__file__).replace("\\", "/"))
    parts = []
    for name in sorted(os.listdir(here)):
        if name.endswith(".py") and not name.startswith("test_"):
            try:
                with open(posixpath.join(here, name), encoding="utf-8", errors="ignore") as handle:
                    parts.append(handle.read())
            except OSError:
                pass
    return " ".join(" ".join(parts).split())


def hook_quote_problems(text, transcript_path):
    """A hook answer is quoted only when a hook gave it in this session. Legacy session 2026-11-05: the model read
    the refusal text in the hook's source with Grep and told the user twice that the hook had answered so."""
    # A line that cites file and line names the source it read: that is the rule, not a breach of it.
    quotes = [" ".join(q.split()) for line in lines_of(without_stamp(text))
              if HOOK_WORD.search(line) and not FILE_LINE.search(line) for q in QUOTE.findall(line)]
    quotes = [q for q in quotes if len(q.split()) >= 3]
    if not quotes:
        return []
    source = hook_source()
    claimed = [q for q in quotes if q in source]
    if not claimed:
        return []
    heard = hook_outputs(transcript_path)
    missing = [q for q in claimed if q not in heard]
    if not missing:
        return []
    return [f"`{missing[0][:50]}` is text from the hook's source, but no hook said it in this session: "
            "cite the file and line you read, not the hook"]


def without_stamp(text):
    lines = lines_of(text)
    return "\n".join(lines[:-1]) if lines and WATERMARK.match(lines[-1]) else text


def human_problems(text, mode, clock="", paths=None):
    return watermark_problems(lines_of(text), clock=clock) + content_problems(text, mode, paths)


def closed_problems(text, clock="", paths=None):
    return watermark_problems(lines_of(text), clock=clock) + content_problems(text, "closed", paths)


def content_problems(text, mode, paths=None):
    """The rules of the register without the stamp; the display gate holds a message back for these."""
    if mode == "closed":
        return closed_content_problems(text) + (language_problem(without_stamp(text), paths) if paths else [])
    lines = lines_of(text)
    problems = []
    text = without_stamp(text)
    if paths is not None:
        problems += language_problem(text, paths)
    if len(lines) > MAX_LINES.get(mode, 30):
        problems.append(f"at most {MAX_LINES.get(mode, 30)} lines; extensive findings go into a file")
    if HASH_IN_TEXT.search(text):
        problems.append("no commit hashes: say in words what changed")
    if ID_IN_TEXT.search(text):
        problems.append("no spec or concept ids: name the title")
    if prose_of(text).count("?") > 1:
        problems.append("at most one question, with a recommendation")
    hedges = sorted({m.group(0).lower() for m in HEDGES.finditer(text)})
    if hedges:
        problems.append("no hedge words (" + ", ".join(hedges) + "): a sentence without a source is not written")
    if OPTIONS.search(text):
        problems.append("no option menus: give the recommendation")
    if ROADMAP.search(text):
        problems.append("no promises about what you will do next")
    if SELF.search(text):
        problems.append("no sentences about yourself, your effort or apologies: name the error once, correct it, go on")
    return problems


def closed_content_problems(text):
    lines = lines_of(text)
    problems = []
    text = without_stamp(text)
    if len(lines) > MAX_LINES["closed"]:
        problems.append(f"closed state: at most {MAX_LINES['closed']} lines, answer from the docket only")
    if "?" in text:
        problems.append("closed state: no questions")
    if PROPOSAL.search(text) or ROADMAP.search(text):
        problems.append("closed state: no proposals and no announcements of work")
    return problems


if __name__ == "__main__":
    sys.exit(main())
