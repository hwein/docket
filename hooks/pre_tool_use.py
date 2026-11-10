"""PreToolUse hook, all tools.

Write rights by state x path (roles are write rights, not sessions):
- specs: writable in spec mode while not Ready or Done; afterwards only in rewrite mode for that spec
- dockets: only in sealed mode, only the session's own spec; Write creates the skeleton once, Edit appends
- concepts and DOCKET.md: concept/init modes
- LOG.md: never by a tool (hooks only)
Done specs and dockets are not a reference source: Read/Grep on them outside concept mode is refused.
Sealed: AskUserQuestion and the next-step box refused; an agent's description names its place in the series in
everyday words (V8); a fixer never gets the docket or the metrics (V6, the orchestrator keeps the docket, the hook
writes the metrics); the plugin's own source is not read (V10); a push waits for a decided review (V14) that
reviewer agents ran after the last implementer or fixer (V15); a reviewer never gets the docket (V15); an
implementer, refiner, reviewer, diagnoser or conceptor is spawned with its model (V17); a point's second
implementer waits for the failure's Triage line, its third for a cause named there at a file:line (V18).
Closing: only the rendered closing message is sent; the box may ask only the decision it names. Closed: writes,
agents and box questions refused. Degraded: writes, shell and agents refused until the required reading has been observed. Shell read
commands are redirected to Read.
Concept and spec: after a message that named a `Blocker` and ended the turn, writes, agents and workflows are
refused until the user's next prompt. Concept: a write in the repo outside the work dir is put to the user (`ask`).
forbidden_commands (DOCKET.md) are not refused but put to the user: permissionDecision `ask` shows the command,
one click grants it once (LOG `ask`, then `grant` from post_tool_use when it ran). In a sealed run, which has no
user, they stay refused. forbidden_paths stay refused everywhere.
"""
import json
import os
import posixpath
import re
import shutil
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (PLUGIN_ROOT, RUN_MODES, Paths, absolute, blocker_since_prompt, closing_path, config_values,
                        frontmatter, language_code, load_state, log_event, position, read_input, read_text,
                        refinement_problem, refuse, review_problem, review_run_problem, rework_problem, save_state,
                        set_context, under)

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
INIT_FILES = {"docket.md", "contributing.md", "claude.md", "agents.md", ".gitignore", "settings.json", "settings.local.json"}
# V8: the agent's description is what the user reads when it finishes; plugin words mean nothing to the user
# (luradb-web-client 2026-11-06: 80 completions, 59 names in the plugin's words, none naming its spec).
PLUGIN_WORDS = re.compile(r"\b(refiners?|fixers?|conceptors?|implementers?|diagnosers?|orchestrators?|flip\w*|dockets?|"
                          r"skeletons?)\b",
                          re.IGNORECASE)
# V17: the model is a decision of the orchestrator per spawn, by the point's tier and the role, never an inheritance
# from its own. The fixer's model is fixed in its definition.
MODEL_ROLES = ("implementer", "refiner", "reviewer", "conceptor", "diagnoser")
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

    # Modes are switched by the user's command, never by the model invoking the command itself.
    skill = str(tool_input.get("skill", "")).lower()
    if tool == "Skill" and skill.startswith("docket:"):
        typed = str(state.get("last_command", "")).split()
        if not typed or typed[0] != "/" + skill:
            return refuse("[docket] The /docket: commands are the user's commands: they switch the mode "
                          "only when the user types them. Say which command comes next and end the turn.")

    if mode in RUN_MODES and tool == "AskUserQuestion":
        return refuse("[docket] The spec is sealed: no questions. Take the default, write a 'deviation' line "
                      "into the docket, continue. What only the user can decide goes into the Terminal state "
                      "(`decision:` or `state: BLOCKED: …`) and reaches the user in the closing message.")

    # The desktop app's next-step box asks too, and no Stop hook sees its question.
    if tool.endswith("__wrap_up"):
        asks = bool(str(tool_input.get("ask") or "").strip())
        if mode in ("sealed", "rewrite"):
            return refuse("[docket] A sealed run shows nothing until its closing message: no next-step box, no recap. "
                          "End the turn without text.")
        if mode == "closing":
            head = (read_text(closing_path(paths, session_id)).splitlines() or [""])[0]
            recap = " ".join(str(tool_input.get("recap") or "").split())
            if head and not recap.startswith(" ".join(head.split())[:60]):
                return refuse(f"[docket] The recap of the closing turn begins with the closing message's first line: `{head}`.")
            if asks and not closing_decision(paths, session_id):
                return refuse("[docket] The closing message asks no decision: send the recap alone, no question.")
        if mode == "closed" and asks:
            return refuse("[docket] The implementation is closed: no questions, not in the next-step box either. "
                          "Send the recap alone.")

    # A blocker ends the turn: nothing is written or started until the user answers.
    if mode in ("concept", "spec") and configured and tool in WRITE_TOOLS | {"Agent", "Workflow"}:
        blocker = blocker_since_prompt(hook_input.get("transcript_path"))
        if blocker:
            return refuse(f"[docket] Your message named a blocker (`{blocker[:80]}`) and ended the turn. Nothing is "
                          "written or started until the user answers; report only what is new.")

    if mode == "closed" and tool in WRITE_TOOLS | {"Agent"}:
        return refuse("[docket] The implementation is closed. Answer from the docket; a new command opens the next mode.")
    if mode == "closing" and tool in WRITE_TOOLS | {"Agent", "Workflow", "Bash", "PowerShell"}:
        return refuse("[docket] The run is over: read the closing message with Read and send it verbatim, nothing else.")

    subagent = bool(hook_input.get("agent_id"))  # a tool call of an agent, not of the orchestrator
    if tool.endswith("__update_plan") and mode in RUN_MODES:
        problem = plan_problem(paths, tool_input)
        if problem:
            return refuse(problem)
    if tool in ("Agent", "Task") and mode in ("sealed", "rewrite") and not subagent:
        problem = agent_problem(paths, state, tool_input)
        if problem:
            return refuse(problem)

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
        problem = (refinement_problem(paths, state.get("spec", ""))
                   or (not subagent and rework_problem(paths, state.get("spec", ""), hook_input.get("transcript_path"))))
        if problem:
            return refuse(problem)

    if tool in ("Bash", "PowerShell"):
        command = tool_input.get("command")
        if isinstance(command, str):
            forbidden = forbidden_command(command, config)
            if forbidden and mode in ("sealed", "rewrite"):
                return refuse(forbidden + " A sealed run has no user: end the spec with `state: BLOCKED: <the command, for the user to run>`.")
            if forbidden:
                log_event(paths, session_id, state.get("spec", ""), "ask", " ".join(command.split())[:100])
                return ask(forbidden + " Allowing it here grants it once; the LOG records the grant.")
            problem = project_rule_problem(command, config, paths)
            if problem:
                return refuse(problem)
            if mode in ("sealed", "rewrite") and GIT_PUSH.search(command) and not subagent:
                problem = (review_problem(paths, state.get("spec", ""))
                           or review_run_problem(paths, state.get("spec", ""), hook_input.get("transcript_path")))
                if problem:
                    return refuse(problem + " The push waits for it.")
        if isinstance(command, str) and registered_check(command, paths, state):
            return 0
        if configured and isinstance(command, str) and SHELL_READ.search(command) and "<<" not in command:
            return refuse("[docket] Read files with the Read tool, search with Grep or Glob, not with shell commands.")
        return 0

    if path is None:
        return 0

    if tool in READ_TOOLS and mode in RUN_MODES and hook_source_path(path):
        # V10: the run is checked by the hooks, not studied through them (luradb-web-client 2026-11-06: the
        # orchestrator read stop.py to find what the check accepts).
        return refuse("[docket] The plugin's own files are not part of the work. What a hook wants is in its message; "
                      "the rules are in the implement command.")
    if tool in READ_TOOLS and mode != "concept":
        if path.lower() == paths.log.lower():
            return 0
        for folder in (paths.specs, paths.dockets):
            if under(path, folder) and os.path.isfile(path):
                if finished_document(paths, path, folder):
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

    if under(path, paths.state_dir) or under(path, posixpath.join(paths.work, "locks")) or under(path, paths.metrics):
        return refuse("[docket] Session state, locks and metrics are written by the hooks only; "
                      "a mode changes through the user's command, never by editing the state file.")

    forbidden = forbidden_path(path, config.get("forbidden_paths", ""), paths)
    if forbidden:
        return refuse(f"[docket] {forbidden} is a forbidden path per DOCKET.md (forbidden_paths); nothing is written there.")

    # Concept mode writes concepts and DOCKET.md. Anything else in the repo (legacy specs, AGENTS.md, CLAUDE.md)
    # goes to the user as a dialog, one click per write (legacy session 2026-11-05: flags and rule files changed
    # on a box answer about something else).
    if mode == "concept" and configured and under(path, paths.root) and not under(path, paths.work):
        relative = path[len(paths.root):].lstrip("/")
        log_event(paths, session_id, "", "ask", "write " + relative[:90])
        return ask(f"[docket] Concept mode writes concepts and DOCKET.md; {relative} lies outside. Allowing it "
                   "grants this one write.")

    if mode == "init" and not state.get("init_approved") and posixpath.basename(path).lower() in INIT_FILES:
        return refuse("[docket] Nothing is written before the diff is approved: show the diff and wait for the user's go.")

    if path.lower() == paths.config.lower():
        if mode in ("concept", "init", "none"):
            return 0
        return refuse("[docket] DOCKET.md changes only in concept mode or through /docket:init.")

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


def agent_problem(paths, state, tool_input):
    """V8: the description names the place in the series and the task in everyday words; V6: a fixer changes project
    files only, the orchestrator keeps the docket and the hook writes the metrics; V15: a reviewer is briefed
    without the docket; V17: the spawn names its model."""
    description = str(tool_input.get("description") or "")
    kind = str(tool_input.get("subagent_type") or "").lower()
    i, n = position(state)
    german = language_code(paths) == "de"
    prefix = (f"Spec {i} von {n}: " if german else f"Spec {i} of {n}: ") if n > 1 else ""
    example = prefix + ("Prüfung gegen den Code" if german else "check against the code")
    if (prefix and not description.startswith(prefix)) or PLUGIN_WORDS.search(description):
        return ("[docket] The agent's description is what the user reads when it finishes: "
                + (f"start it with `{prefix}` and " if prefix else "")
                + "name the task in everyday words, without the plugin's words (refiner, fixer, implementer, docket "
                f"…). Example: `{example}`.")
    prompt = str(tool_input.get("prompt") or "")
    if "fixer" in kind and re.search(r"\bdockets[/\\]|\bmetrics[/\\]|\.violations\.json", prompt, re.IGNORECASE):
        return ("[docket] The docket is yours: write it yourself (skeleton with Write, then Edit: boxes, evidence, "
                "Terminal state); the hook writes the metrics. A fixer changes project files only.")
    if "reviewer" in kind and re.search(r"\bdockets[/\\]", prompt, re.IGNORECASE):
        return ("[docket] A reviewer reads the order and the delivery, not your account of them: brief it with its "
                "angle, the spec path and the range to read, without the docket.")
    if any(role in kind for role in MODEL_ROLES) and not str(tool_input.get("model") or "").strip():
        return ("[docket] This agent has no `model`: an implementer runs on the model of its point's tier, one tier "
                "higher per rework round; refiners, reviewers, the diagnoser and the conceptor on the model DOCKET.md "
                "`model_ladder` gives their role. Spawn it again with that model.")
    return ""


def hook_source_path(path):
    """The hooks of this plugin or of any installed docket version; its templates, commands and agents stay readable
    (the orchestrator writes the docket from the template)."""
    return under(path, PLUGIN_ROOT + "/hooks") or bool(
        re.search(r"/plugins/cache/docket[^/]*/docket/[^/]+/hooks(/|$)", path.replace("\\", "/"), re.IGNORECASE))


def finished_document(paths, path, folder):
    """A spec in state done or superseded, or the docket of such a spec (a docket has no state of its own)."""
    if folder == paths.specs:
        return frontmatter(path).get("state", "").lower() in FINISHED_STATES
    found = re.match(r"(s-[0-9a-f]{8})", posixpath.basename(path).lower())
    spec = paths.spec_file(found.group(1)) if found else None
    return bool(spec) and frontmatter(spec).get("state", "").lower() in FINISHED_STATES


def plan_problem(paths, tool_input):
    """The desktop app's task list is on the user's screen during a run: its items speak the user's words, not the
    plugin's (experiment 12: "Docket-Skelett schreiben", "Terminal-Zustand")."""
    items = tool_input.get("items") if isinstance(tool_input.get("items"), list) else []
    words = [str(item.get("content", "")) for item in items if isinstance(item, dict)]
    bad = [w for w in words if PLUGIN_WORDS.search(w) or re.search(r"\bterminal[- ]?(state|zustand)\b|\bhooks?\b", w, re.I)]
    if bad:
        return ("[docket] The task list is on the user's screen: name each step in everyday words, without the plugin's "
                f"words (docket, fixer, refiner, hook, terminal state). Refused: `{bad[0][:60]}`.")
    return ""


def closing_decision(paths, session_id):
    """Whether the rendered closing message asks a decision (its `Entscheidung:` / `Decision:` line)."""
    text = read_text(closing_path(paths, session_id))
    return bool(re.search(r"^(Entscheidung|Decision):", text, re.MULTILINE))


def ask(reason):
    """permissionDecision `ask`: Claude Code shows the user the command and the reason; one click runs it once.
    Not a violation: the rule says the command needs the user, and this is the way the user is asked."""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                             "permissionDecisionReason": reason}}))
    return 0


def forbidden_command(command, config):
    """forbidden_commands in DOCKET.md: a regex of commands that run only with the user's click."""
    forbidden = config.get("forbidden_commands", "")
    if forbidden:
        try:
            if re.search(forbidden, command):
                return f"[docket] This command matches forbidden_commands in DOCKET.md (`{forbidden}`); it runs only with the user's click."
        except re.error:
            pass
    return ""


def project_rule_problem(command, config, paths):
    """CONTRIBUTING rules made deterministic through DOCKET.md: push_branch (regex the pushed branch must
    match), commit_regex (the first line of every commit message)."""
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
    if mode == "spec":
        return 0
    if mode == "sealed":
        return refuse("[docket] Sealed sessions never write specs; deviations go into the docket.")
    if mode == "concept":
        return refuse("[docket] Specs are written in spec mode only: name `/docket:spec` with the whole order and end "
                      "the turn. No draft, concept file or status flag stands in for a spec.")
    return refuse("[docket] Specs are written in spec mode only (/spec).")


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
