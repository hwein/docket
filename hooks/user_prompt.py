"""UserPromptSubmit hook.

Says, as state lines, what the prompt set off, and nothing else:
- `/docket:implement` marks the run (docket_lib.mark_run): run mark, lock of the first spec, series, LOG `sealed` and
  `queued`, the spec's state in-progress; or the line why no run was marked. It takes a spec, a list of specs, a
  concept (its ready specs), --all or nothing (the next ready spec).
- `/docket:stop` in a run records the stop request; the Stop hook ends the series after the spec at work.
- `/docket:status` and `/docket:register` name the series of this session and interrupted ones.
- every typed `/docket:` command in a project with DOCKET.md gets the line with the work dir, DOCKET.md and
  user_language; in a project without DOCKET.md, the line that it is not set up.
- every prompt in a session with the run mark gets the run line: spec i of n, its docket, its points.
Without a command and without the run mark there is no output. Every prompt of the user clears `refused` and
`halted`: the next Stop is a first pass. No prompt is refused: this hook never exits 2.
"""
import os
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import command_line as prompt_command
from docket_lib import (MARKED, RUN_MODES, Paths, load_state, log_event, logged_series, mark_run, read_input, run_line,
                        save_state, say, series_line, strip_harness_blocks, take_handoff, work_dir_line)

COMMAND = re.compile(r"^/(?P<namespace>docket:)?(?P<name>[a-z]+)\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
COMMANDS = ("concept", "spec", "implement", "init", "stop", "status", "register")
# Claude Code has no built-in of these names, so they also count without the namespace; /init and /status do not.
BARE = ("concept", "spec", "implement")


def main():
    hook_input = read_input()
    prompt = hook_input.get("prompt")
    if not isinstance(prompt, str):
        return 0
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    text = strip_harness_blocks(prompt).strip()
    if not text:
        return 0  # only harness notifications (task results, session notes): not user text
    if state.get("refused") or state.get("halted"):
        state.update({"refused": False, "halted": False})  # a new turn of the user: the next Stop is a first pass
        save_state(paths, session_id, state)
    # The first line, or the last below a preamble (the pane, a session started by another); a line in between only
    # where the pane recorded exactly this command for a session of its own (the app wraps that session's brief).
    name, rest = parse_command(prompt_command(text) or take_handoff(paths, session_id, text))
    if not os.path.isfile(paths.config):
        # Not set up: nothing is marked, locked or logged, and no work dir is created.
        if name and name != "init":
            print("[docket] " + say(paths, "not_set_up", work=paths.work))
        return 0

    lines = [work_dir_line(paths)] if name else []
    if name == "implement":
        lines.append(mark_run(paths, session_id, rest, state)[1])
    else:
        if name == "stop" and state.get("mode") in RUN_MODES:
            state["stop_requested"] = True
            save_state(paths, session_id, state)
            log_event(paths, session_id, state.get("spec", ""), "stop-requested")
        if state.get("mode") in MARKED:
            lines.append(run_line(paths, state))
        if name in ("status", "register"):
            # a series of this session or an interrupted one, with its titles (V13)
            mine = (session_id or "")[:8]
            lines += [series_line(paths, series, live=series["session"] == mine)
                      for series in logged_series(paths)[-3:]
                      if series["session"] == mine or "interrupted" in series["status"].values()]
    if lines:
        print("\n".join(lines))
    return 0


def parse_command(line):
    """(name, rest) of a docket command typed as /docket:<name>, or ("", "") for anything else."""
    found = COMMAND.match(line)
    if not found:
        return "", ""
    name = found.group("name").lower()
    if name not in COMMANDS or not (found.group("namespace") or name in BARE):
        return "", ""
    return name, found.group("rest")


if __name__ == "__main__":
    sys.exit(main())
