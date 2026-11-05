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
from docket_lib import SPEC_ID, Paths, load_state, log_event, log_lines, read_input, save_state

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
    else:
        lines.append(f"[docket] No DOCKET.md at {paths.work}. Run /docket init to set this project up; "
                     "until then the docket hooks only observe.")
    if mode in ("sealed", "rewrite"):
        lines.append(f"[docket] A sealed implementation of {spec_id} is in progress: {points_summary(paths, spec_id)}. "
                     f"Continue from the docket at {paths.docket_file(spec_id) or paths.dockets} "
                     f"and the last lines of {paths.log}.")
    else:
        open_runs = interrupted_runs(paths)
        if open_runs:
            lines.append("[docket] Sealed runs without a terminal state: " + ", ".join(open_runs)
                         + ". `/implement <id>` resumes one from its docket.")
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
    counts = {mark: len(re.findall(r"(?<!`)\[" + re.escape(mark) + r"\](?!`)", body)) for mark in ("x", "~", " ")}
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


if __name__ == "__main__":
    sys.exit(main())
