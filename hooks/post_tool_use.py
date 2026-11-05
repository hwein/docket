"""PostToolUse hook, matcher Edit|Write|MultiEdit.

Dockets are append-only: after an edit the previous content (snapshot taken in pre_tool_use)
must still be a prefix of the new content. A violation is reported to the model with exit 2
and logged; the hook cannot undo it, the model must restore the lines.
"""
import os
import posixpath
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import Paths, absolute, load_state, log_event, read_input, refuse, under


def main():
    hook_input = read_input()
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return 0
    paths = Paths(hook_input)
    path = absolute(file_path, paths.cwd)
    if not under(path, paths.dockets):
        return 0
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    spec_id = state.get("spec", "")
    snap = posixpath.join(paths.state_dir, f"snap-{spec_id}.md")
    if not os.path.isfile(snap):
        return 0
    try:
        with open(snap, encoding="utf-8-sig", errors="ignore") as handle:
            before = handle.read().rstrip()
        with open(path, encoding="utf-8-sig", errors="ignore") as handle:
            after = handle.read()
    except OSError:
        return 0
    if after.startswith(before) or before_lines_kept(before, after):
        return 0
    log_event(paths, session_id, spec_id, "docket-rewritten", "append-only violated")
    return refuse("[docket] The docket is append-only. Restore every previous line unchanged and add your "
                  "change as new lines below; a point changes state by a new line, never by editing the old one.")


def placeholder(line):
    """Template lines that are meant to be filled: blank lines and empty bullets."""
    return line.strip() in ("", "-", "*")


def before_lines_kept(before, after):
    """Every previous line is still there, in order; new lines may be inserted anywhere.

    Allowed in-place changes: a checkbox flip `[ ]` -> `[x]` or `[~]`, and filling a placeholder line.
    """
    old = [line for line in before.splitlines() if not placeholder(line)]
    new = after.splitlines()
    position = 0
    for a in old:
        while position < len(new):
            b = new[position]
            position += 1
            if a == b or a.replace("[ ]", "[x]") == b or a.replace("[ ]", "[~]") == b:
                break
        else:
            return False
    return True


if __name__ == "__main__":
    sys.exit(main())
