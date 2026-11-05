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
from docket_lib import (Paths, absolute, config_values, frontmatter, load_state, log_event, read_input, refuse,
                        save_state, set_context, under)

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
INIT_FILES = {"docket.md", "contributing.md", "claude.md", "agents.md", ".gitignore", "settings.json", "settings.local.json"}
DECISION = re.compile(r"\b(adopted|rejected|rewrite|rewritten|dissent|moot)\b", re.IGNORECASE)
GIT_PUSH = re.compile(r"\bgit\s+push\b([^|;&\n]*)")
GIT_COMMIT = re.compile(r"\bgit\s+commit\b[^|;&\n]*?(?:-m|--message)[=\s]+(?:\"([^\"]*)\"|'([^']*)'|(\S+))")
HEREDOC_MESSAGE = re.compile(r"\bgit\s+commit\b[^\n]*<<\s*['\"]?(\w+)['\"]?\)?\s*\n([^\n]*)")
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
    set_context(paths, state.get("spec", ""))
    config = config_values(paths) if configured else {}

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

    if mode in ("sealed", "rewrite") and tool == "AskUserQuestion":
        return refuse("[docket] The spec is sealed: no questions. Take the default, write a 'deviation' line "
                      "into the docket, continue. Only DOCKET BLOCKED or DOCKET DISSENT reach the user.")

    if mode == "closed" and tool in WRITE_TOOLS | {"Agent"}:
        return refuse("[docket] The implementation is closed. Answer from the docket; a new command opens the next mode.")

    # Rewrite is a visible event: spawning the conceptor in a sealed run opens that one spec for it.
    if tool == "Agent" and "conceptor" in str(tool_input.get("subagent_type", "")).lower():
        if mode == "sealed":
            state.update({"mode": "rewrite", "rewrite": state.get("spec", "")})
            save_state(paths, session_id, state)
            log_event(paths, session_id, state.get("spec", ""), "rewrite", "conceptor spawned")
            return 0
        if mode == "rewrite":
            return refuse("[docket] A rewrite is already running; wait for the conceptor to return.")

    # The first implementer runs only after pre-registration and a closed refinement.
    if tool == "Agent" and mode == "sealed" and "implementer" in str(tool_input.get("subagent_type", "")).lower():
        problem = refinement_problem(paths, state.get("spec", ""))
        if problem:
            return refuse(problem)

    if tool in ("Bash", "PowerShell"):
        command = tool_input.get("command")
        if isinstance(command, str):
            problem = project_rule_problem(command, config, paths)
            if problem:
                return refuse(problem)
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

    forbidden = forbidden_path(path, config.get("forbidden_paths", ""), paths)
    if forbidden:
        return refuse(f"[docket] {forbidden} is a forbidden path per DOCKET.md (forbidden_paths); nothing is written there.")

    if mode == "init" and not state.get("init_approved") and posixpath.basename(path).lower() in INIT_FILES:
        return refuse("[docket] Nothing is written before the diff is approved: show the diff and wait for the user's go.")

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


def refinement_problem(paths, spec_id):
    """Before the first implementer: the docket skeleton exists and every refinement line carries a decision."""
    docket = paths.docket_file(spec_id)
    if docket is None:
        return "[docket] No docket yet: a fixer writes the skeleton (one row per spec point) before the first implementer."
    try:
        with open(docket, encoding="utf-8-sig", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return ""
    match = re.search(r"^##\s+Refinement\b.*?$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    open_lines = [line.strip() for line in match.group(1).splitlines()
                  if re.match(r"\s*[-*]\s+\S", line) and not DECISION.search(line)]
    if open_lines:
        return ("[docket] Refinement lines without a decision (adopted / rejected / rewrite / dissent): "
                + " | ".join(line[:70] for line in open_lines[:3]) + ". Close them before the first implementer.")
    return ""


def project_rule_problem(command, config, paths):
    """CONTRIBUTING rules made deterministic through DOCKET.md: forbidden_commands (regex), push_branch
    (regex the pushed branch must match), commit_regex (the first line of every commit message)."""
    forbidden = config.get("forbidden_commands", "")
    if forbidden:
        try:
            if re.search(forbidden, command):
                return f"[docket] This command matches forbidden_commands in DOCKET.md (`{forbidden}`); it needs the user."
        except re.error:
            pass
    push_branch = config.get("push_branch", "")
    push = GIT_PUSH.search(command)
    if push_branch and push:
        branch = pushed_branch(push.group(1), paths)
        try:
            if branch and not re.fullmatch(push_branch, branch):
                return f"[docket] Push goes to `{branch}`; DOCKET.md allows push_branch `{push_branch}` only."
        except re.error:
            pass
    commit_regex = config.get("commit_regex", "")
    if commit_regex and re.search(r"\bgit\s+commit\b", command):
        message = commit_message(command)
        try:
            if message is not None and not re.match(commit_regex, message):
                return f"[docket] Commit message `{message[:60]}` does not match commit_regex `{commit_regex}` from DOCKET.md."
        except re.error:
            pass
    return ""


def pushed_branch(args, paths):
    words = [word for word in args.split() if not word.startswith("-")]
    if len(words) >= 2:
        return words[1].split(":")[-1].replace("refs/heads/", "")
    try:
        import subprocess
        return subprocess.run(["git", "-C", paths.root, "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def commit_message(command):
    heredoc = HEREDOC_MESSAGE.search(command)
    if heredoc:
        return heredoc.group(2).strip()
    match = GIT_COMMIT.search(command)
    if match:
        return next(group for group in match.groups() if group is not None).splitlines()[0].strip()
    return None


def forbidden_path(path, patterns, paths):
    """forbidden_paths in DOCKET.md: comma-separated globs relative to the repo root."""
    if not patterns:
        return ""
    import fnmatch
    relative = path[len(paths.root) + 1:] if under(path, paths.root) else path
    for pattern in [p.strip().strip("`") for p in patterns.split(",") if p.strip()]:
        if fnmatch.fnmatch(relative.lower(), pattern.lower()) or fnmatch.fnmatch(relative.lower(), pattern.lower().rstrip("/") + "/*"):
            return relative
    return ""


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
    if mode not in ("sealed", "rewrite"):
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
