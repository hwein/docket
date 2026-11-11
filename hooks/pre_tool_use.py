"""PreToolUse hook: the project rules of DOCKET.md, for writes and shell commands.

Four rules, each read from DOCKET.md at every call, each for the orchestrator and its agents alike:
- forbidden_paths: a write tool on a path that matches one of the globs is refused.
- push_branch: a `git push` to a branch the regex does not match is refused.
- commit_regex: a `git commit` whose message (in the command) does not match is refused.
- forbidden_commands: a matching shell command is refused in a run (the session carries the run mark, mode sealed);
  outside a run it goes to the user as a permission dialog (permissionDecision `ask`, LOG `ask`, then `grant` from
  post_tool_use when it ran).
Every refusal and the dialog's text begin with `[docket rule: <key>]` and name the key, its value and DOCKET.md.

The model's Skill call `docket:implement` is the second trigger of the run mark (docket_lib.mark_run, the first is the
typed command in user_prompt): a side effect, never a refusal. The call goes on (exit 0), and the state line, the run
line or why no run was marked, goes to the model as additionalContext.
"""
import json
import os
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (RUN_MODES, Paths, absolute, config_values, load_state, log_event, mark_run, read_input, refuse,
                        say, under)

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
GIT_PUSH = re.compile(r"\bgit\s+push\b([^|;&\n]*)")
GIT_COMMIT = re.compile(r"\bgit\s+commit\b[^|;&\n]*?(?:-m|--message)[=\s]+(?:\"([^\"]*)\"|'([^']*)'|(\S+))")
HEREDOC_MESSAGE = re.compile(r"\bgit\s+commit\b[^\n]*<<\s*['\"]?(\w+)['\"]?\)?\s*\n([^\n]*)")


def main():
    hook_input = read_input()
    tool = hook_input.get("tool_name")
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    if tool not in WRITE_TOOLS and tool not in SHELL_TOOLS and tool != "Skill":
        return 0
    paths = Paths(hook_input)
    if not os.path.isfile(paths.config):
        return 0

    if tool == "Skill":
        if tool_input.get("skill") != "docket:implement":
            return 0
        args = tool_input.get("args")
        _, line = mark_run(paths, hook_input.get("session_id"), args if isinstance(args, str) else "")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": line}}))
        return 0

    config = config_values(paths)

    if tool in SHELL_TOOLS:
        command = tool_input.get("command")
        if not isinstance(command, str):
            return 0
        problem = project_rule_problem(command, config, paths)
        if problem:
            return refuse(problem)
        forbidden = config.get("forbidden_commands", "")
        if forbidden_command(command, forbidden):
            session_id = hook_input.get("session_id")
            state = load_state(paths, session_id)
            values = {"value": forbidden, "config": paths.config}
            if state.get("mode") in RUN_MODES:
                return refuse(say(paths, "rule_forbidden_commands", **values))
            log_event(paths, session_id, state.get("spec", ""), "ask", " ".join(command.split())[:100])
            return ask(say(paths, "rule_forbidden_commands_ask", **values))
        return 0

    file_path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    if not isinstance(file_path, str) or not file_path:
        return 0
    path = absolute(file_path, paths.cwd)
    patterns = config.get("forbidden_paths", "")
    forbidden = forbidden_path(path, patterns, paths)
    if forbidden:
        return refuse(say(paths, "rule_forbidden_paths", path=forbidden, value=patterns, config=paths.config))
    return 0


def ask(reason):
    """permissionDecision `ask`: Claude Code shows the user the command and the reason; one click runs it once."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                             "permissionDecisionReason": reason}}))
    return 0


def forbidden_command(command, forbidden):
    """forbidden_commands in DOCKET.md: a regex of commands; an invalid regex matches nothing."""
    if not forbidden:
        return False
    try:
        return bool(re.search(forbidden, command))
    except re.error:
        return False


def project_rule_problem(command, config, paths):
    """push_branch (regex the pushed branch must match) and commit_regex (the first line of every commit message)."""
    push_branch = config.get("push_branch", "")
    push = GIT_PUSH.search(command)
    if push_branch and push:
        branch = pushed_branch(push.group(1), paths)
        try:
            if branch and not re.fullmatch(push_branch, branch):
                return say(paths, "rule_push_branch", branch=branch, value=push_branch, config=paths.config)
        except re.error:
            pass
    commit_regex = config.get("commit_regex", "")
    if commit_regex and re.search(r"\bgit\s+commit\b", command):
        message = commit_message(command)
        try:
            if message is not None and not re.match(commit_regex, message):
                return say(paths, "rule_commit_regex", message=message[:60], value=commit_regex, config=paths.config)
        except re.error:
            pass
    return ""


def pushed_branch(args, paths):
    words = [word for word in args.split() if not word.startswith("-")]
    if len(words) >= 2:
        return words[1].split(":")[-1].replace("refs/heads/", "")
    import subprocess
    try:
        return (subprocess.run(["git", "-C", paths.root, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=5).stdout or "").strip()
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


if __name__ == "__main__":
    sys.exit(main())
