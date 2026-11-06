"""SessionStart hook (startup, resume, clear, compact).

Marks the session degraded: required reading has not been observed. Prints the
reminder as additional context: state, spec, the docket's point count, the last LOG line.
In a project without DOCKET.md it prints nothing: the hooks only observe there.
The degraded flag lifts in pre_tool_use once every required file has been read with Read.
A docket that was sealed but never reached a terminal state is named, so the next
/implement resumes it instead of starting over.
"""
import hashlib
import json
import os
import posixpath
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (SPEC_ID, Paths, absolute, config_values, load_state, log_event, log_lines, point_counts,
                        read_input, save_state, under, violations_line)

TERMINAL_EVENTS = ("delivered", "blocked", "dissent")
PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_MARKER = "The project's check: one command, one result line"
# SHA-256 of every version of the code after the COMMANDS block in templates/check.py (LF line ends). A project copy
# whose code matches one of them is docket's own and follows the template; code the project changed stays as it is.
# A change to the template's code appends its hash here (test_the_template_code_is_a_known_version).
CHECK_VERSIONS = (
    "6256f39287468bc17631bbd1fc9d68a7ecc9229ada13cdb6c0a1e62287c7d3f3",  # 7cd023d .. 2087323
    "09cf57b14dc638eff0579cd207f65927e5a5ee9221842ad7be64783ff572653b",  # 0.2.7: marker line below COMMANDS
)
# SHA-256 of the judge entry (JSON, sorted keys) in every version of templates/judge-hook.json. A change to the
# template appends its hash here (test_the_template_judge_is_a_known_version).
JUDGE_VERSIONS = (
    "5319c732aade46e8a8ce727da0274ca1e9fcae1967933bee4cd0d25b1715552e",  # 87691e5
    "66e52126396b9438efb7e6c803836f1bee02fd254a0b81e569039b520e5d9001",  # 7cd023d
    "e37f7abb6c3e4e740f0b333451fb477b7951d33be4e76301a70470d6e7d0a370",  # ede9558
    "4cd58dcd2ad3ca02922a83aff66b6c0e17401be61d338778b6fdec2ec2669a24",  # 2ebd179
    "a3e15f7bb88c5d429ba60ef5c715b39706813296dc543287f6c51ee25dc58bb2",  # 964a32e
    "cc495faee6749643631443339542108ada60ff6d505b41d8eb7db180ed5e3817",  # 2087323
)


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
    if mode == "none" and not os.path.isfile(paths.config):
        return 0  # project not set up: nothing to say; user_prompt names /docket:init when a docket command is typed
    lines = [f"[docket] session state: {mode}" + (f", spec {spec_id}" if spec_id else "") + "."]
    if os.path.isfile(paths.config):
        lines += sync_copies(paths, session_id)
        lines.append("[docket] Required reading before any other action, each file completely with Read: "
                     + ", ".join(required) + ". Until then writes, shell commands and agents are refused.")
        lines.append("[docket] Rule of every session here: each message ends with the line `stamp: YYYY-MM-DD HH:MM`, "
                     "the time of the latest `[docket] clock:` line the hooks give with every prompt and tool result, "
                     "or fetched from the clock in the same turn; the Stop hook refuses the message otherwise. "
                     "When it sends you back, do exactly what it asks: the stamp line alone, correction lines, or after a judge "
                     "verdict an empty answer; "
                     "repeat a message only when the hook says it was held back and never shown.")
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


def sync_copies(paths, session_id):
    """/docket:init copies two files into the project: the judge hook into .claude/settings.json and the check
    script. A plugin update reaches them here, at every session start, so no project runs an old copy and nobody
    has to run init again (2026-11-06: the judge in luradb-web-client was five template versions behind)."""
    notes = []
    if sync_judge(paths):
        log_event(paths, session_id, "", "judge-updated", "from templates/judge-hook.json")
        notes.append("[docket] The judge in .claude/settings.json now follows the plugin's version.")
    check = sync_check(paths)
    if check:
        log_event(paths, session_id, "", "check-updated", check)
        notes.append(f"[docket] The code of {check} now follows the plugin's version; its commands stayed.")
    return notes


def write_atomically(path, text, bom=False):
    temporary = f"{path}.docket-{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def judge_digest(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def sync_judge(paths):
    """The project's judge entry becomes the template's while it is an unchanged copy of an earlier template; a
    judge the project changed (model, timeout, prompt) stays, and so does a project without one (opt-in)."""
    settings_path = posixpath.join(paths.root, ".claude", "settings.json")
    try:
        with open(posixpath.join(PLUGIN, "templates", "judge-hook.json"), encoding="utf-8") as handle:
            template = json.load(handle)["hooks"]["Stop"][0]["hooks"][0]
        with open(settings_path, "rb") as handle:
            raw = handle.read().decode("utf-8")
        settings = json.loads(raw.lstrip("﻿"))
    except (OSError, ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
        return False
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    stop = hooks.get("Stop") if isinstance(hooks, dict) else None
    changed = False
    for group in stop if isinstance(stop, list) else []:
        entries = group.get("hooks") if isinstance(group, dict) else None
        for index, hook in enumerate(entries if isinstance(entries, list) else []):
            if (isinstance(hook, dict) and hook != template and judge_digest(hook) in JUDGE_VERSIONS):
                entries[index] = dict(template)
                changed = True
    if changed:
        text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        try:
            write_atomically(settings_path, text.replace("\n", "\r\n") if "\r\n" in raw else text,
                             bom=raw.startswith("﻿"))
        except OSError:
            return False
    return changed


def split_check(text):
    """(head with the COMMANDS block, code after it) or None."""
    start = text.find("\nCOMMANDS = [")
    end = text.find("\n]\n", start) if start >= 0 else -1
    if end < 0:
        return None
    return text[:end + 3], text[end + 3:]


def sync_check(paths):
    """The check script docket copied keeps its head and commands; its code follows the template while it is an
    unchanged copy of an earlier template. Returns the script's path relative to the root, or ""."""
    found = re.search(r"([^\s\"'`]+\.py)\b", config_values(paths).get("check", ""))
    if not found:
        return ""
    path = absolute(found.group(1), paths.root)
    if not under(path, paths.root):
        return ""  # a script outside the project is not docket's copy
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
        with open(posixpath.join(PLUGIN, "templates", "check.py"), encoding="utf-8") as handle:
            template = handle.read().replace("\r\n", "\n")
    except OSError:
        return ""
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    project, current = split_check(text), split_check(template)
    if CHECK_MARKER not in text or not project or not current or project[1] == current[1]:
        return ""
    if hashlib.sha256(project[1].encode("utf-8")).hexdigest() not in CHECK_VERSIONS:
        return ""  # the project changed the code: it stays
    updated = project[0] + current[1]
    try:
        write_atomically(path, updated.replace("\n", "\r\n") if crlf else updated)
    except OSError:
        return ""
    return path[len(paths.root):].lstrip("/")


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
