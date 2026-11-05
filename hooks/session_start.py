"""SessionStart hook (startup, resume, clear, compact).

Marks the session degraded: required reading has not been observed. Prints the
reminder as additional context. The degraded flag lifts in pre_tool_use once every
required file has been read with Read.
"""
import os
import posixpath
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import Paths, load_state, log_event, read_input, save_state


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
    if state.get("mode") == "sealed" and source in ("resume", "compact"):
        log_event(paths, session_id, state.get("spec", ""), "resumed", f"source={source}")
    lines = [f"[docket] session state: {state.get('mode', 'none')}"
             + (f", spec {state['spec']}" if state.get("spec") else "") + "."]
    if os.path.isfile(paths.config):
        lines.append("[docket] Required reading before any other action, each file completely with Read: "
                     + ", ".join(required) + ". Until then writes, shell commands and agents are refused.")
    else:
        lines.append(f"[docket] No DOCKET.md at {paths.work}. Run /docket init to set this project up; "
                     "until then the docket hooks only observe.")
    if state.get("mode") == "sealed":
        lines.append(f"[docket] A sealed implementation of {state.get('spec')} is in progress; "
                     f"continue it from the docket at {paths.docket_file(state.get('spec')) or paths.dockets} "
                     f"and the last lines of {paths.log}.")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
