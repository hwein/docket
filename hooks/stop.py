"""Stop hook, layer 1 (deterministic). Layer 2 is the prompt-type judge in hooks.json.

sealed:  the final message is one of DOCKET DELIVERED | BLOCKED | DISSENT <spec-id>, the docket
         exists with no open point, the last line is a fresh stamp. On success the session
         closes and LOG gets the terminal line.
concept, spec, init: human register. No hashes, no ids, at most one question, no hedge words,
         no option menus, no roadmap promises, length limit.
closed:  short answers only, no questions, no proposals.
Sent back once (stop_hook_active); a second failure is logged and let through.
"""
import os
import posixpath
import re
import sys
from datetime import datetime, timedelta

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import CONCEPT_ID, SPEC_ID, Paths, load_state, log_event, message_text, read_input, refuse, save_state

TERMINAL = re.compile(r"^DOCKET (DELIVERED|BLOCKED|DISSENT)\s+(s-[0-9a-f]{8})\b", re.IGNORECASE)
STAMP = re.compile(r"^stamp:\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s+(s-[0-9a-f]{8})\s*$", re.IGNORECASE)
HASH = re.compile(r"(?<![0-9a-zA-Z/])[0-9a-f]{7,40}(?![0-9a-zA-Z])")
HEDGES = re.compile(r"\b(probably|likely|seems?|appears?|presumably|i think|i believe|might|vermutlich|"
                    r"wahrscheinlich|scheint|offenbar|wohl|könnte|vielleicht)\b", re.IGNORECASE)
OPTIONS = re.compile(r"\b(option [a-c1-3]\b|alternatively|or alternatively|alternativ)", re.IGNORECASE)
ROADMAP = re.compile(r"\b(next,? i will|afterwards i will|then i will|als n[äa]chstes werde ich|danach werde ich|"
                     r"i'll (then|now)|let me know|sag bescheid)\b", re.IGNORECASE)
PROPOSAL = re.compile(r"\b(i suggest|i propose|should we|shall i|vorschlag|soll ich|wollen wir)\b", re.IGNORECASE)
TOLERANCE = timedelta(minutes=5)
MAX_LINES = {"concept": 30, "spec": 30, "init": 40, "closed": 8, "sealed": 25}


def main():
    hook_input = read_input()
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    mode = state.get("mode", "none")
    if mode == "none":
        return 0
    text = message_text(hook_input)
    if text is None:
        return 0
    second_pass = bool(hook_input.get("stop_hook_active"))

    if mode == "sealed":
        if hook_input.get("background_tasks"):
            return 0  # the turn resumes when the background agent reports; not a terminal moment
        problems = sealed_problems(text, state, paths)
        if not problems:
            close(paths, session_id, state, text)
            return 0
    elif mode == "closed":
        problems = closed_problems(text)
    else:
        problems = human_problems(text, mode)

    if not problems:
        return 0
    if second_pass:
        log_event(paths, session_id, state.get("spec", ""), "stop-forced", "; ".join(problems)[:120])
        return 0
    return refuse("[docket] Rewrite your last message before it is delivered: " + "; ".join(problems)
                  + ". Then end the turn with the rewritten message only.")


def lines_of(text):
    return [line.rstrip() for line in text.strip().splitlines() if line.strip()]


def sealed_problems(text, state, paths):
    lines = lines_of(text)
    problems = []
    spec_id = state.get("spec", "")
    head = TERMINAL.match(lines[0]) if lines else None
    if head is None:
        problems.append(f"the first line must be `DOCKET DELIVERED {spec_id}`, `DOCKET BLOCKED {spec_id}` or "
                        f"`DOCKET DISSENT {spec_id}` (a sealed run ends only with a terminal state)")
    elif head.group(2).lower() != spec_id.lower():
        problems.append(f"the terminal line names {head.group(2)}, this session is sealed on {spec_id}")
    docket = paths.docket_file(spec_id)
    delivered = bool(head) and head.group(1).upper() == "DELIVERED"
    if docket is None:
        if delivered:
            problems.append("the docket file does not exist yet (write the skeleton first, one line per spec point)")
    else:
        try:
            with open(docket, encoding="utf-8-sig", errors="ignore") as handle:
                body = handle.read()
        except OSError:
            body = ""
        open_points = len(re.findall(r"(?<!`)\[ \](?!`)", body))
        if open_points and head and head.group(1).upper() == "DELIVERED":
            problems.append(f"{open_points} docket point(s) still open `[ ]`; each needs `[x]` with evidence "
                            "or `[~]` with a deviation line")
    stamp = STAMP.match(lines[-1]) if lines else None
    if stamp is None:
        problems.append("the last line must be `stamp: YYYY-MM-DD HH:MM <spec-id>` with the current time, fetched now")
    else:
        try:
            when = datetime(*(int(stamp.group(i)) for i in range(1, 6)))
            deviation = abs(datetime.now() - when)
            if deviation > TOLERANCE:
                problems.append(f"the stamp is {round(deviation.total_seconds() / 60)} minutes off the real time: "
                                "fetch the time, do not remember it; then check whether other rules blurred too")
        except ValueError:
            problems.append("the stamp is not a valid date and time")
        if stamp.group(6).lower() != spec_id.lower():
            problems.append("the stamp names another spec")
    if len(lines) > MAX_LINES["sealed"]:
        problems.append(f"at most {MAX_LINES['sealed']} lines")
    return problems


def close(paths, session_id, state, text):
    lines = lines_of(text)
    head = TERMINAL.match(lines[0])
    kind = head.group(1).lower()
    state["mode"] = "closed"
    state["terminal"] = kind
    save_state(paths, session_id, state)
    log_event(paths, session_id, state.get("spec", ""), kind, "closed")


def human_problems(text, mode):
    lines = lines_of(text)
    problems = []
    if len(lines) > MAX_LINES.get(mode, 30):
        problems.append(f"at most {MAX_LINES.get(mode, 30)} lines; extensive findings go into a file")
    if HASH.search(text):
        problems.append("no commit hashes: say in words what changed")
    if SPEC_ID.search(text) or CONCEPT_ID.search(text):
        problems.append("no spec or concept ids: name the title")
    if text.count("?") > 1:
        problems.append("at most one question, with a recommendation")
    hedges = sorted({m.group(0).lower() for m in HEDGES.finditer(text)})
    if hedges:
        problems.append("no hedge words (" + ", ".join(hedges) + "): a sentence without a source is not written")
    if OPTIONS.search(text):
        problems.append("no option menus: give the recommendation")
    if ROADMAP.search(text):
        problems.append("no promises about what you will do next")
    return problems


def closed_problems(text):
    lines = lines_of(text)
    problems = []
    if len(lines) > MAX_LINES["closed"]:
        problems.append(f"closed state: at most {MAX_LINES['closed']} lines, answer from the docket only")
    if "?" in text:
        problems.append("closed state: no questions")
    if PROPOSAL.search(text) or ROADMAP.search(text):
        problems.append("closed state: no proposals and no announcements of work")
    return problems


if __name__ == "__main__":
    sys.exit(main())
