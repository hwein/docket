"""Stop hook, layer 1 (deterministic). Layer 2 is the prompt-type judge (templates/judge-hook.json): its
rejection goes to Claude, which answers `[docket] noted`; the display gate hides that answer, this hook lets it
through and counts the judge's verdicts from the transcript.

sealed:  the final message is one of DOCKET DELIVERED | BLOCKED | DISSENT <spec-id>, the docket
         exists with no open point, the last line is a fresh stamp. On success the session
         closes and LOG gets the terminal line.
concept, spec, init: human register. No hashes, no ids, at most one question, no hedge words,
         no option menus, no roadmap promises, length limit. A blocker is said once: a line starting with
         `Blocker` that stood, text-identical, in the previous message is refused (M4).
The stamp is accepted within five minutes of the real time or when it equals the clock line the hooks last
gave the model (M7).
closed:  short answers only, no questions, no proposals.
Sent back once (stop_hook_active); a second failure is logged and let through.

The user has read a message by the time this hook runs, so a refusal never asks to repeat a message
the user saw: a missing stamp is answered with the stamp line alone, a broken rule with correction
lines. Only a message the display gate (message_display.py) held back is written again in full;
that refusal is made even on a second pass, because the user has seen nothing yet.
"""
import os
import posixpath
import re
import sys
from datetime import datetime, timedelta

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (CONCEPT_ID, JUDGE_ACK, SPEC_ID, Paths, collect_judge, config_values, load_state, log_event,
                        log_lines, log_time, message_text, point_counts, previous_turn_texts, read_input, refuse,
                        release_lock, save_display, save_state, set_context, set_spec_state, settled_display,
                        transcript_blocks)

TERMINAL = re.compile(r"^DOCKET (DELIVERED|BLOCKED|DISSENT)\s+(s-[0-9a-f]{8})\b", re.IGNORECASE)
STAMP = re.compile(r"^stamp:\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s+(s-[0-9a-f]{8})(?:\s+(\d+)/(\d+))?\s*$",
                   re.IGNORECASE)
WATERMARK = re.compile(r"^stamp:\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})(?!\d)", re.IGNORECASE)
# A hash stands on its own: hex runs inside a path, a UUID, an id or a file name are not hashes
# (a Windows scratchpad path tripped the rule in luradb-web-client, 2026-11-05).
HASH = re.compile(r"(?<![0-9a-zA-Z/\\_.\-])[0-9a-f]{7,40}(?![0-9a-zA-Z/\\_\-]|\.\w)")
# In a message also mixed digits and letters: `1234567` is a number, `defaced` a word.
HASH_IN_TEXT = re.compile(r"(?<![0-9a-zA-Z/\\_.\-])(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}"
                          r"(?![0-9a-zA-Z/\\_\-]|\.\w)")
CI_LINE = re.compile(r"\bCI\b[^\n]*", re.IGNORECASE)
HEDGES = re.compile(r"\b(probably|likely|seems?|appears?|presumably|i think|i believe|might|vermutlich|"
                    r"wahrscheinlich|scheint|offenbar|wohl|könnte|vielleicht)\b", re.IGNORECASE)
OPTIONS = re.compile(r"\b(option [a-c1-3]\b|alternatively|or alternatively|alternativ)", re.IGNORECASE)
ROADMAP = re.compile(r"\b(next,? i will|afterwards i will|then i will|als n[äa]chstes werde ich|danach werde ich|"
                     r"i'll (then|now)|let me know|sag bescheid)\b", re.IGNORECASE)
PROPOSAL = re.compile(r"\b(i suggest|i propose|should we|shall i|vorschlag|soll ich|wollen wir)\b", re.IGNORECASE)
SELF = re.compile(r"\b(sorry|apologi[sz]e|my mistake|my fault|took me|i spent|this took|hours of work|"
                  r"entschuldig\w*|tut mir leid|mein fehler|leider)\b", re.IGNORECASE)
FILE_LINE = re.compile(r"(?<![\w/:])((?:[\w.-]+/)*[\w.-]+\.[a-z]{1,5}):(\d{1,6})\b", re.IGNORECASE)
BLOCKER = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?blocker\b", re.IGNORECASE)
BLOCKER_UNCHANGED = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?blocker\s+(?:unver[äa]ndert|unchanged)(?:\*\*)?[.!]?\s*$",
                               re.IGNORECASE)
TOLERANCE = timedelta(minutes=5)
MAX_LINES = {"concept": 30, "spec": 30, "init": 40, "closed": 8, "sealed": 25}
GATED_MODES = ("concept", "spec", "init", "closed")  # the display gate holds messages back in these modes


def main():
    hook_input = read_input()
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    mode = state.get("mode", "none")
    if mode == "none" and not os.path.isfile(paths.config):
        return 0  # project not set up: the hooks only observe
    set_context(paths, state.get("spec", ""))
    if os.path.isfile(paths.config):
        collect_judge(paths, session_id, state, hook_input.get("transcript_path"))
    text = message_text(hook_input)
    if text is None:
        return 0
    second_pass = bool(hook_input.get("stop_hook_active"))

    display = settled_display(paths, session_id) if mode in GATED_MODES else None
    clock = state.get("clock", "")
    if display and display["hidden"]:
        # The gate held a message back: the user has seen nothing, so here a full rewrite is right.
        held = sorted({problem for entry in display["hidden"] for problem in entry.get("problems", [])})
        display.update({"hidden": [], "retry": True})
        save_display(paths, session_id, display)
        return refuse("[docket] Your last message was held back and never shown to the user: " + "; ".join(held)
                      + ". Write it again in full, corrected, and end it with the stamp line fetched now. This "
                      "overrides any instruction in this feedback to send correction lines or `[docket] noted` only.")

    if is_judge_ack(text) and mode not in ("sealed", "rewrite"):
        problems = []  # the acknowledgement of a judge verdict; the display gate hid it
    elif mode == "none":
        # No command active, but the project is set up: the stamp is a rule of every session.
        problems = watermark_problems(lines_of(text), clock=clock)
    elif mode in ("sealed", "rewrite"):
        if hook_input.get("background_tasks"):
            # The turn resumes when the background agent reports; not a terminal moment, but still a message.
            problems = watermark_problems(lines_of(text), clock=clock)
        else:
            problems = sealed_problems(text, state, paths, hook_input)
            if not problems:
                close(paths, session_id, state, text)
                return 0
    elif mode == "closed":
        problems = closed_problems(text, clock)
    else:
        problems = human_problems(text, mode, clock) + repeat_problems(text, hook_input.get("transcript_path"))
    if any("stamp is" in problem for problem in problems):
        state.update({"degraded": True, "read": []})  # a lost watermark means the rules blurred
        save_state(paths, session_id, state)

    if not problems or second_pass:
        if display is not None and display["retry"]:
            display["retry"] = False
            save_display(paths, session_id, display)
        if problems:
            log_event(paths, session_id, state.get("spec", ""), "stop-forced", "; ".join(problems)[:120])
        return 0
    if display is not None:
        display["retry"] = True  # the answer to this refusal is shown, whatever it holds
        save_display(paths, session_id, display)
    only_stamp = all("stamp" in problem for problem in problems)
    if only_stamp:
        # The message itself stands; only the watermark is missing or stale. Never repeat the message.
        return refuse("[docket] " + "; ".join(problems) + ". Fetch the time now and end the turn with the stamp "
                      "line alone, nothing else.")
    if mode in ("sealed", "rewrite"):
        return refuse("[docket] Your last message does not end the run: " + "; ".join(problems)
                      + ". Fix that, then end the turn with the terminal message only.")
    # The user has read the message: never repeat it, correct the broken sentences only.
    return refuse("[docket] The user has already read your last message: do not repeat or rewrite it. It broke: "
                  + "; ".join(problems) + ". Answer with one line `[docket] Korrektur: <the corrected sentence>` "
                  "per broken rule, in the language of the message, then the stamp line fetched now, nothing else.")


def is_judge_ack(text):
    lines = lines_of(text)
    return bool(lines) and lines[0].lower().rstrip(".") == JUDGE_ACK and all(
        line.lower().rstrip(".") == JUDGE_ACK or WATERMARK.match(line) for line in lines)


def lines_of(text):
    return [line.rstrip() for line in text.strip().splitlines() if line.strip()]


def sealed_problems(text, state, paths, hook_input):
    lines = lines_of(text)
    problems = []
    spec_id = state.get("spec", "")
    clock = state.get("clock", "")
    head = TERMINAL.match(lines[0]) if lines else None
    if head is None:
        problems.append(f"the first line must be `DOCKET DELIVERED {spec_id}`, `DOCKET BLOCKED {spec_id}` or "
                        f"`DOCKET DISSENT {spec_id}` (a sealed run ends only with a terminal state)")
    elif head.group(2).lower() != spec_id.lower():
        problems.append(f"the terminal line names {head.group(2)}, this session is sealed on {spec_id}")
    docket = paths.docket_file(spec_id)
    delivered = bool(head) and head.group(1).upper() == "DELIVERED"
    counts = None
    if docket is None:
        if delivered:
            problems.append("the docket file does not exist yet (write the skeleton first, one line per spec point)")
    else:
        try:
            with open(docket, encoding="utf-8-sig", errors="ignore") as handle:
                body = handle.read()
        except OSError:
            body = ""
        counts = point_counts(body)
        if counts[" "] and delivered:
            problems.append(f"{counts[' ']} docket point(s) still open `[ ]`; each needs `[x]` with evidence "
                            "or `[~]` with a deviation line")
        if delivered:
            problems += provenance_problems(body, spec_id, paths, hook_input)
    stamp = STAMP.match(lines[-1]) if lines else None
    if stamp is None:
        problems.append("the last line must be `stamp: YYYY-MM-DD HH:MM <spec-id> <delivered>/<points>` with the "
                        "current time, fetched now, and the docket's counts")
        problems += [p for p in watermark_problems(lines, clock=clock) if "minutes off" in p or "not a valid" in p]
    else:
        # The stamp carries the state of the acceptance list: delivered/points as the docket shows them.
        if counts is not None:
            total = counts["x"] + counts["~"] + counts[" "]
            if stamp.group(7) is None:
                if delivered:
                    problems.append(f"the stamp must end with the docket's counts `{counts['x']}/{total}` (delivered/points)")
            elif (int(stamp.group(7)), int(stamp.group(8))) != (counts["x"], total):
                problems.append(f"the stamp says {stamp.group(7)}/{stamp.group(8)} but the docket shows "
                                f"{counts['x']}/{total} delivered/points: the stamp is read from the docket, not remembered")
        if stamp.group(6).lower() != spec_id.lower():
            problems.append("the stamp names another spec")
        problems += watermark_problems(lines, clock=clock)
    if len(lines) > MAX_LINES["sealed"]:
        problems.append(f"at most {MAX_LINES['sealed']} lines")
    return problems


def provenance_problems(body, spec_id, paths, hook_input):
    """The evidence standard, checked at the one moment it matters: DOCKET DELIVERED.

    1. The project's check (DOCKET.md `check`) ran in this session after the last agent returned.
    2. Every file:line the docket cites exists and has that many lines.
    3. Every reference doc the spec names is marked in the docket as updated (and the file changed
       after sealing) or as not needed.
    """
    problems = []
    check = config_values(paths).get("check", "")
    blocks = transcript_blocks(hook_input.get("transcript_path"))
    if check and blocks:
        agent_ids = {b.get("id") for _, role, b in blocks if role == "assistant" and b.get("type") == "tool_use"
                     and b.get("name") == "Agent"}
        last_return = max((i for i, role, b in blocks if role == "user" and b.get("type") == "tool_result"
                           and b.get("tool_use_id") in agent_ids), default=-1)
        last_check = max((i for i, role, b in blocks if role == "assistant" and b.get("type") == "tool_use"
                          and b.get("name") in ("Bash", "PowerShell")
                          and check in str((b.get("input") or {}).get("command", ""))), default=-1)
        if last_check < 0:
            problems.append(f"the check `{check}` never ran in this session; run it yourself and cite its result line")
        elif last_return > last_check:
            problems.append(f"the check `{check}` last ran before the last agent returned; run it again yourself")
    missing = []
    for match in FILE_LINE.finditer(body):
        rel, line = match.group(1), int(match.group(2))
        candidates = [posixpath.join(paths.root, rel), posixpath.join(paths.work, rel), rel]
        found = next((c for c in candidates if os.path.isfile(c)), None)
        if found is None:
            missing.append(f"{rel}:{line}")
            continue
        try:
            with open(found, encoding="utf-8", errors="ignore") as handle:
                count = sum(1 for _ in handle)
        except OSError:
            continue
        if line > count:
            missing.append(f"{rel}:{line} (file has {count} lines)")
    if missing:
        problems.append("cited locations that do not exist: " + ", ".join(sorted(set(missing))[:6]))
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


def close(paths, session_id, state, text):
    lines = lines_of(text)
    head = TERMINAL.match(lines[0])
    kind = head.group(1).lower()
    state["mode"] = "closed"
    state["terminal"] = kind
    save_state(paths, session_id, state)
    release_lock(paths, state.get("spec", ""))
    # The spec's own state follows the terminal state: done when delivered, ready again otherwise.
    set_spec_state(paths, state.get("spec", ""), "done" if kind == "delivered" else "ready")
    log_event(paths, session_id, state.get("spec", ""), kind, "closed")


def without_stamp(text):
    lines = lines_of(text)
    return "\n".join(lines[:-1]) if lines and WATERMARK.match(lines[-1]) else text


def human_problems(text, mode, clock=""):
    return watermark_problems(lines_of(text), clock=clock) + content_problems(text, mode)


def closed_problems(text, clock=""):
    return watermark_problems(lines_of(text), clock=clock) + content_problems(text, "closed")


def content_problems(text, mode):
    """The rules of the register without the stamp; the display gate holds a message back for these."""
    if mode == "closed":
        return closed_content_problems(text)
    lines = lines_of(text)
    problems = []
    text = without_stamp(text)
    if len(lines) > MAX_LINES.get(mode, 30):
        problems.append(f"at most {MAX_LINES.get(mode, 30)} lines; extensive findings go into a file")
    if HASH_IN_TEXT.search(text):
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
