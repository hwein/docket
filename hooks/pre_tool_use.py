"""PreToolUse hook, all tools.

Write rights by state x path (roles are write rights, not sessions):
- specs: writable in concept/spec/init while not Ready or Done; afterwards only in rewrite mode for that spec
- dockets: only in sealed mode, only the session's own spec; Write creates the skeleton once, Edit appends
- concepts and DOCKET.md: concept/init modes
- LOG.md: never by a tool (hooks only)
Done specs and dockets are not a reference source: Read/Grep on them outside concept mode is refused.
Sealed: AskUserQuestion refused. Closed: writes and agents refused. Degraded: writes, shell and agents
refused until the required reading has been observed. Shell read commands are redirected to Read.
"""
import os
import posixpath
import re
import shutil
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import Paths, absolute, frontmatter, load_state, read_input, refuse, save_state, under

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}
SHELL_READ = re.compile(r"(?:^|[;&|(]\s*)(?:cat|head|tail|less|more|bat|type|Get-Content|gc)\s+(?!>)(?:-\S+\s+)*[\"']?[^\s|;&>]+\.(?:md|py|rs|ts|js|json|toml|yml|yaml|txt)", re.IGNORECASE)
FROZEN_STATES = {"ready", "in-progress", "done", "superseded"}
FINISHED_STATES = {"done", "superseded"}


def main():
    hook_input = read_input()
    tool = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    if not isinstance(tool, str):
        return 0
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    mode = state.get("mode", "none")
    configured = os.path.isfile(paths.config)

    file_path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    path = absolute(file_path, paths.cwd) if isinstance(file_path, str) and file_path else None

    # Required reading lifts the degraded flag.
    if tool == "Read" and path and state.get("degraded") and configured:
        required = {p.lower(): p for p in paths.required_reading()}
        if path.lower() in required:
            read = set(state.get("read", []))
            read.add(required[path.lower()])
            state["read"] = sorted(read)
            if all(p in read for p in required.values()):
                state["degraded"] = False
            save_state(paths, session_id, state)
        return 0

    if state.get("degraded") and configured and tool in WRITE_TOOLS | {"Bash", "Agent", "PowerShell"}:
        missing = [p for p in paths.required_reading() if p not in state.get("read", [])]
        return refuse("[docket] Required reading first, completely with Read: " + ", ".join(missing))

    if mode == "sealed" and tool == "AskUserQuestion":
        return refuse("[docket] The spec is sealed: no questions. Take the default, write a 'deviation' line "
                      "into the docket, continue. Only DOCKET BLOCKED or DOCKET DISSENT reach the user.")

    if mode == "closed" and tool in WRITE_TOOLS | {"Agent"}:
        return refuse("[docket] The implementation is closed. Answer from the docket; a new command opens the next mode.")

    if tool in ("Bash", "PowerShell"):
        command = tool_input.get("command")
        if isinstance(command, str) and registered_check(command, paths, state):
            return 0
        if configured and isinstance(command, str) and SHELL_READ.search(command) and "<<" not in command:
            return refuse("[docket] Read files with the Read tool, search with Grep or Glob, not with shell commands.")
        return 0

    if path is None:
        return 0

    if tool in READ_TOOLS and mode != "concept":
        if path.lower() == paths.log.lower():
            return 0
        for folder in (paths.specs, paths.dockets):
            if under(path, folder) and os.path.isfile(path):
                if frontmatter(path).get("state", "").lower() in FINISHED_STATES:
                    return refuse("[docket] Finished specs and dockets are closed work orders, not a reference. "
                                  "Current behaviour comes from the reference docs named in DOCKET.md, then from code.")
        if tool == "Grep" and (under(paths.specs, path) or under(paths.dockets, path)) and mode != "concept":
            if path.lower() in (paths.specs.lower(), paths.dockets.lower(), paths.work.lower()):
                return refuse("[docket] Do not search the spec folder outside concept mode; open the one spec you work on.")
        return 0

    if tool not in WRITE_TOOLS:
        return 0

    if path.lower() == paths.log.lower():
        return refuse("[docket] LOG.md is written by the hooks only.")

    if path.lower() == paths.config.lower():
        if mode in ("concept", "init", "none"):
            return 0
        return refuse("[docket] DOCKET.md changes only in concept mode or through /docket init.")

    if under(path, paths.concepts):
        if mode in ("concept", "init"):
            return 0
        return refuse("[docket] Concepts are written in concept mode only.")

    if under(path, paths.specs):
        return spec_rule(path, mode, state, tool)

    if under(path, paths.dockets):
        return docket_rule(path, mode, state, tool, paths)

    return 0


def registered_check(command, paths, state):
    """A command pre-registered in the docket's points table (`<command>` → ...) or the spec's done
    criteria, or the check from DOCKET.md, is allowed verbatim: evidence must be fetchable."""
    wanted = command.strip()
    if not wanted:
        return False
    sources = [paths.config]
    if state.get("spec"):
        sources += [paths.docket_file(state["spec"]), paths.spec_file(state["spec"])]
    for source in sources:
        if not source or not os.path.isfile(source):
            continue
        try:
            with open(source, encoding="utf-8-sig", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            continue
        for registered in re.findall(r"`([^`\n]+)`", text):
            if registered.strip() == wanted:
                return True
    return False


def spec_rule(path, mode, state, tool):
    exists = os.path.isfile(path)
    doc_state = frontmatter(path).get("state", "").lower() if exists else ""
    name = posixpath.basename(path)
    if mode == "rewrite":
        if state.get("rewrite") and name.lower().startswith(state["rewrite"].lower()):
            return 0
        return refuse(f"[docket] Rewrite mode opens only {state.get('rewrite')}, not {name}.")
    if doc_state in FROZEN_STATES:
        return refuse(f"[docket] {name} is {doc_state} and frozen. A finding becomes a deviation line in the docket "
                      "or a new spec; a rewrite is a visible event through the conceptor in rewrite mode.")
    if mode in ("concept", "spec", "init"):
        return 0
    if mode == "sealed":
        return refuse("[docket] Sealed sessions never write specs; deviations go into the docket.")
    return 0


def docket_rule(path, mode, state, tool, paths):
    name = posixpath.basename(path)
    if mode != "sealed":
        return refuse("[docket] Dockets are written during a sealed implementation only.")
    spec_id = state.get("spec", "")
    if not name.lower().startswith(spec_id.lower()):
        return refuse(f"[docket] This session may only write the docket of {spec_id}.")
    exists = os.path.isfile(path)
    if tool == "Write" and exists:
        return refuse("[docket] The docket exists and is append-only: use Edit to add lines, never rewrite it.")
    if exists:
        snapshot(path, paths, spec_id)
    return 0


def snapshot(path, paths, spec_id):
    try:
        os.makedirs(paths.state_dir, exist_ok=True)
        shutil.copyfile(path, posixpath.join(paths.state_dir, f"snap-{spec_id}.md"))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
