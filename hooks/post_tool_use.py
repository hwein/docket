"""PostToolUse hook, every tool, also registered for PostToolUseFailure: Claude Code runs PostToolUse only after a
call that succeeded, a failed one (a shell command with an exit code other than 0) runs PostToolUseFailure (hooks docs;
none of 59 failed Bash/PowerShell calls in the eval transcripts carries a PostToolUse attachment). It refuses nothing
and undoes nothing.

The one line it gives the model, as additionalContext: in a project that is not set up, a DOCKET.md written where
docket does not look (config_elsewhere).

What it keeps, as side effects:
- LOG `point-delivered` and `deviation`: in a session with the run mark, a write on a docket counts the file's
  `[x]` and `[~]`; what exceeds the `count=` sum of the spec's LOG lines of that kind becomes one LOG line. The spec id
  comes from the file name.
- LOG `ready`: a spec written with `state: ready`, once per spec.
- LOG `preconditions`: a spec written with the verdict of a precondition check (`checked: <date> met` or
  `not met`), once per verdict.
- LOG `grant`: a shell command that matched forbidden_commands ran, so the user allowed it in the dialog (M1).
- The life sign: in a session with the run mark, a tool call of the orchestrator clears the Stop hook's refusal
  marker `refused` where it is set (the next Stop is a first pass again), else it renews the state file's time; an
  agent's tool call only renews the time. Without the run mark the hook writes nothing into the state.
"""
import json
import os
import posixpath
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (RUN_MODES, SPEC_ID, Paths, absolute, config_values, frontmatter, load_state, log_event,
                        log_has, log_lines, marked, precondition_state, read_input, save_state, say, touch_state,
                        under)

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
CONFIG_NAME = "DOCKET.md"
POINT_EVENTS = {"x": "point-delivered", "~": "deviation"}


def main():
    hook_input = read_input()
    tool = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    configured = os.path.isfile(paths.config)
    state = load_state(paths, session_id) if configured else {}
    in_run = state.get("mode") in RUN_MODES
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if tool in WRITE_TOOLS and isinstance(file_path, str) and file_path:
        path = absolute(file_path, paths.cwd)
        if not configured and hook_input.get("hook_event_name") != "PostToolUseFailure":
            config_elsewhere(path, paths)
        if under(path, paths.dockets):
            if in_run:
                log_points(path, paths, session_id)
        elif under(path, paths.specs):
            log_ready(path, paths, session_id)
            log_preconditions(path, paths, session_id)
    elif tool in ("Bash", "PowerShell"):
        log_grant(tool_input, paths, session_id)
    if in_run:
        live(hook_input, paths, session_id, state)
    return 0


def config_elsewhere(path, paths):
    """A DOCKET.md written where docket does not look, in a project that is not set up: the line names both places
    and the pointer file that makes the written folder the work dir (/docket:init wrote DOCKET.md into a second
    repo and no pointer, and neither a hook nor the pane saw the project as set up)."""
    if posixpath.basename(path) != CONFIG_NAME or not os.path.isfile(path):
        return
    folder = posixpath.dirname(path)
    relative = folder[len(paths.root):].lstrip("/") or "." if under(folder, paths.root) else folder
    line = marked(paths, "line", say(paths, "config_elsewhere", written=path, config=paths.config, folder=folder,
                                     pointer=json.dumps({"work_dir": relative})))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": line}}))


def live(hook_input, paths, session_id, state):
    """The life sign of a run (session_live). An agent writes nothing into the session state: 15 agents at work were
    15 writers on one file, and the mode fell out of it (luradb-web-client 2026-11-10)."""
    if not hook_input.get("agent_id") and state.get("refused"):
        state["refused"] = False  # work went on after a refusal: the next Stop is a first pass again
        save_state(paths, session_id, state)
    else:
        touch_state(paths, session_id)


def log_grant(tool_input, paths, session_id):
    """A command that matches forbidden_commands ran: pre_tool_use asked, the user allowed it (M1)."""
    command = tool_input.get("command")
    if not isinstance(command, str) or not os.path.isfile(paths.config):
        return
    forbidden = config_values(paths).get("forbidden_commands", "")
    try:
        if forbidden and re.search(forbidden, command):
            state = load_state(paths, session_id)
            log_event(paths, session_id, state.get("spec", ""), "grant", " ".join(command.split())[:100])
    except re.error:
        pass


# --- dockets -----------------------------------------------------------------

def count_marks(text):
    return {mark: len(re.findall(r"(?<!`)\[" + re.escape(mark) + r"\](?!`)", text)) for mark in ("x", "~")}


def logged_marks(paths, spec_id):
    """The `count=` sums of the spec's LOG lines point-delivered and deviation: the points the LOG already knows."""
    logged = {mark: 0 for mark in POINT_EVENTS}
    for line in log_lines(paths, spec_id):
        fields = line.split()
        if len(fields) < 3 or fields[1] != spec_id:
            continue
        for mark, event in POINT_EVENTS.items():
            count = re.search(r"\bcount=(\d+)", line)
            if fields[2] == event and count:
                logged[mark] += int(count.group(1))
    return logged


def log_points(path, paths, session_id):
    """Each point the LOG does not know yet is counted in one LOG line per kind: point-delivered for [x], deviation
    for [~]."""
    match = SPEC_ID.search(posixpath.basename(path))
    if not match:
        return
    spec_id = match.group(0)
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as handle:
            now = count_marks(handle.read())
    except OSError:
        return
    logged = logged_marks(paths, spec_id)
    for mark, event in POINT_EVENTS.items():
        new = now[mark] - logged[mark]
        if new > 0:
            log_event(paths, session_id, spec_id, event, f"count={new}")


# --- specs -------------------------------------------------------------------

def log_ready(path, paths, session_id):
    """A spec written with `state: ready`: LOG `ready`, once per spec."""
    fields = frontmatter(path)
    spec_id = fields.get("id", "")
    if fields.get("state", "").lower() == "ready" and spec_id and not log_has(paths, spec_id, "ready"):
        log_event(paths, session_id, spec_id, "ready")


def log_preconditions(path, paths, session_id):
    """A spec written with a verdict of a precondition check (`checked: <date> met` or `not met`): LOG
    `preconditions` with the verdict and its date, once per verdict."""
    spec_id = frontmatter(path).get("id", "")
    check = precondition_state(path)
    if not spec_id or check["met"] is None:
        return
    note = f"{'met' if check['met'] else 'unmet'} checked={check['checked']}"
    logged = [line for line in log_lines(paths, spec_id) if line.split()[2:3] == ["preconditions"]]
    if not logged or not logged[-1].endswith(note):
        log_event(paths, session_id, spec_id, "preconditions", note)


if __name__ == "__main__":
    sys.exit(main())
