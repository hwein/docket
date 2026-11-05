"""SessionStart hook (startup, resume, clear, compact).

Marks the session degraded: required reading has not been observed. Prints the
reminder as additional context: state, spec, the docket's point count, the last LOG line.
The degraded flag lifts in pre_tool_use once every required file has been read with Read.
A docket that was sealed but never reached a terminal state is named, so the next
/implement resumes it instead of starting over.
"""
import os
import posixpath
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import SPEC_ID, Paths, load_state, log_event, log_lines, point_counts, read_input, save_state, violations_line

TERMINAL_EVENTS = ("delivered", "blocked", "dissent")


def main():
    hook_input = read_input()
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    required = paths.required_reading()
    state["degraded"] = True
    state["read"] = []
    save_state(paths, session_id, state)
    source = hook_input.get("source", "startup")
    mode = state.get("mode", "none")
    spec_id = state.get("spec", "")
    if mode in ("sealed", "rewrite") and source in ("resume", "compact"):
        log_event(paths, session_id, spec_id, "resumed", f"source={source}")
    lines = [f"[docket] session state: {mode}" + (f", spec {spec_id}" if spec_id else "") + "."]
    if os.path.isfile(paths.config):
        lines.append("[docket] Required reading before any other action, each file completely with Read: "
                     + ", ".join(required) + ". Until then writes, shell commands and agents are refused.")
        lines.append("[docket] Rule of every session here: each message ends with the line `stamp: YYYY-MM-DD HH:MM`, "
                     "the time fetched from the clock in the same turn; the Stop hook refuses the message otherwise. "
                     "When it sends you back, do exactly what it asks: the stamp line alone, correction lines, or the line `[docket] noted`; "
                     "repeat a message only when the hook says it was held back and never shown.")
    else:
        lines.append(f"[docket] No DOCKET.md at {paths.work}. Run /docket:init to set this project up; "
                     "until then the docket hooks only observe.")
    if mode in ("sealed", "rewrite"):
        lines.append(f"[docket] A sealed implementation of {spec_id} is in progress: {points_summary(paths, spec_id)}. "
                     f"Continue from the docket at {paths.docket_file(spec_id) or paths.dockets} "
                     f"and the last lines of {paths.log}.")
        feedback = violations_line(paths, spec_id)
        if feedback:
            lines.append(feedback)
    else:
        open_runs = interrupted_runs(paths)
        if open_runs:
            lines.append("[docket] Sealed runs without a terminal state: " + ", ".join(open_runs)
                         + ". `/implement <id>` resumes one from its docket.")
        stopped = stopped_runs(paths)
        if stopped:
            lines.append("[docket] Runs stopped with open points: " + ", ".join(f"{s} ({n} open)" for s, n in stopped)
                         + ". `/implement <id>` resumes one at its first open point.")
    last = log_lines(paths)[-1:] if os.path.isfile(paths.config) else []
    if last:
        lines.append("[docket] Last LOG line: " + last[0])
    print("\n".join(lines))
    return 0


def points_summary(paths, spec_id):
    docket = paths.docket_file(spec_id)
    if not docket:
        return "no docket yet"
    try:
        with open(docket, encoding="utf-8-sig", errors="ignore") as handle:
            body = handle.read()
    except OSError:
        return "docket unreadable"
    counts = point_counts(body)
    return f"{counts['x']} delivered, {counts['~']} deviations, {counts[' ']} open"


def interrupted_runs(paths):
    """Spec ids with a `sealed` LOG line and no later terminal line."""
    status = {}
    for line in log_lines(paths):
        found = SPEC_ID.search(line)
        if not found:
            continue
        parts = line.split()
        event = parts[2] if len(parts) > 2 else ""
        if event == "sealed":
            status[found.group(0)] = "open"
        elif event in TERMINAL_EVENTS:
            status[found.group(0)] = "closed"
    return sorted(spec for spec, value in status.items() if value == "open")


def stopped_runs(paths):
    """Specs whose last terminal line is `blocked` while their docket still has open `[ ]` points:
    a stop request or a blocker ended the run, the work is resumable."""
    last = {}
    for line in log_lines(paths):
        found = SPEC_ID.search(line)
        parts = line.split()
        if found and len(parts) > 2 and parts[2] in ("sealed",) + TERMINAL_EVENTS:
            last[found.group(0)] = parts[2]
    result = []
    for spec_id, event in sorted(last.items()):
        if event != "blocked":
            continue
        docket = paths.docket_file(spec_id)
        if not docket:
            continue
        try:
            with open(docket, encoding="utf-8-sig", errors="ignore") as handle:
                open_points = point_counts(handle.read())[" "]
        except OSError:
            continue
        if open_points:
            result.append((spec_id, open_points))
    return result


if __name__ == "__main__":
    sys.exit(main())
