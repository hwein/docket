"""SessionStart hook (startup, resume, clear, compact).

State lines for the session as additional context, in a project with DOCKET.md: the session's state, the work dir
with DOCKET.md and user_language, the run line of a marked run (spec i of n, its docket, its points), else the
interrupted series, the runs without a terminal state and the runs stopped with open points, and the last LOG line.
A marked run that goes on after resume or compact gets the LOG line `resumed`.

At every start, also in a project without DOCKET.md: the judge entry of docket 0.5 leaves the project's settings
(remove_judge), and files of 0.5 nobody reads any more go (docket_lib.prune_leftovers).
"""
import hashlib
import json
import os
import posixpath
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (RUN_MODES, SPEC_ID, Paths, _save_json, is_judge, judge_record_path, load_judge_record,
                        load_settings, load_state, log_event, log_lines, logged_series, marked, point_counts,
                        prune_leftovers, read_input, run_line, save_settings, say, series_line, session_live,
                        settings_files, work_dir_line)

TERMINAL_EVENTS = ("delivered", "blocked", "dissent")
# SHA-256 of the judge entry (JSON, sorted keys) in every version of templates/judge-hook.json up to 0.5.11: an
# unchanged copy is known by its digest, a changed one by the mark in its prompt (docket_lib.is_judge).
JUDGE_VERSIONS = (
    "5319c732aade46e8a8ce727da0274ca1e9fcae1967933bee4cd0d25b1715552e",  # 87691e5
    "66e52126396b9438efb7e6c803836f1bee02fd254a0b81e569039b520e5d9001",  # 7cd023d
    "e37f7abb6c3e4e740f0b333451fb477b7951d33be4e76301a70470d6e7d0a370",  # ede9558
    "4cd58dcd2ad3ca02922a83aff66b6c0e17401be61d338778b6fdec2ec2669a24",  # 2ebd179
    "a3e15f7bb88c5d429ba60ef5c715b39706813296dc543287f6c51ee25dc58bb2",  # 964a32e
    "cc495faee6749643631443339542108ada60ff6d505b41d8eb7db180ed5e3817",  # 2087323
    "f14e0d464e7013031488bbbc9221448b13f33c1590225e65108c948ca6be44b0",  # 0.2.9: a blocker names every step of the user
    "1837459e90f76e13c1fb9c5102498c8c104a1fc714a991ae6b07c4334dce35a0",  # 0.5.1: a /docket: command is no manual task
)
REMOVED_RECORD = "judge-removed.json"


def main():
    hook_input = read_input()
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    lines = remove_judge(paths)
    prune_leftovers(paths.state_dir)
    if not os.path.isfile(paths.config):
        if lines:
            print("\n".join(lines))
        return 0  # project not set up: user_prompt names /docket:init when a docket command is typed
    state = load_state(paths, session_id)
    source = hook_input.get("source", "startup")
    mode = state.get("mode", "none")
    spec_id = state.get("spec", "")
    if mode in RUN_MODES and source in ("resume", "compact"):
        log_event(paths, session_id, spec_id, "resumed", f"source={source}")
    lines = [marked(paths, "line", say(paths, "session_state", mode=mode, spec=f", {spec_id}" if spec_id else "")),
             work_dir_line(paths)] + lines
    if mode in RUN_MODES:
        lines.append(run_line(paths, state))
    else:
        in_series = set()
        for series in logged_series(paths):
            if "interrupted" in series["status"].values() and not session_live(paths, series["session"]):
                lines.append(series_line(paths, series))
            in_series.update(series["ids"])
        open_runs = [spec for spec in interrupted_runs(paths) if spec not in in_series]
        if open_runs:
            lines.append(marked(paths, "line", say(paths, "runs_open", ids=", ".join(open_runs))))
        stopped = stopped_runs(paths)
        if stopped:
            lines.append(marked(paths, "line", say(paths, "runs_stopped", items=", ".join(
                say(paths, "run_stopped_item", spec=s, n=n) for s, n in stopped))))
    last = log_lines(paths)[-1:]
    if last:
        lines.append(marked(paths, "line", say(paths, "last_log", line=last[0])))
    print("\n".join(lines))
    return 0


def judge_digest(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def docket_judge(hook):
    return isinstance(hook, dict) and (is_judge(hook) or judge_digest(hook) in JUDGE_VERSIONS)


def remove_judge(paths):
    """0.6 has no judge (REWORK-SPEC 1c.6). Every Stop hook entry of docket's judge leaves .claude/settings.json and
    .claude/settings.local.json, a changed copy and a file git tracks included; a group left without hooks and an
    empty Stop go with it, everything else stays (BOM and line ends too). The entries of a judge-suspended.json go the
    same way, and that file is deleted. The record first: every removed entry, with its file, is appended to
    .claude/docket/judge-removed.json before a settings file changes. Returns the state line, once: a second start finds
    nothing and says nothing."""
    removed, changes, sources = [], [], []
    for path in settings_files(paths):
        if not os.path.isfile(path):
            continue
        settings, raw = load_settings(path)
        hooks = settings.get("hooks") if isinstance(settings, dict) else None
        stop = hooks.get("Stop") if isinstance(hooks, dict) else None
        if not isinstance(stop, list):
            continue
        kept, found = [], []
        for group in stop:
            members = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(members, list) or not any(docket_judge(hook) for hook in members):
                kept.append(group)
                continue
            found += [hook for hook in members if docket_judge(hook)]
            rest = [hook for hook in members if not docket_judge(hook)]
            if rest:
                kept.append(dict(group, hooks=rest))
        if not found:
            continue
        if kept:
            hooks["Stop"] = kept
        else:
            del hooks["Stop"]
        name = ".claude/" + posixpath.basename(path)
        removed += [{"file": name, "hook": hook} for hook in found]
        changes.append((path, settings, raw))
        sources.append(name)
    suspended = judge_record_path(paths)
    record = load_judge_record(paths)  # None when absent or unreadable now: an unreadable one stays for the next start
    if isinstance(record, dict):
        for entry in record.get("entries") or []:
            if isinstance(entry, dict) and entry.get("hook"):
                removed.append({"file": str(entry.get("file") or ""), "hook": entry["hook"],
                                "from": ".claude/docket/judge-suspended.json"})
        sources.append(".claude/docket/judge-suspended.json")
    if not sources:
        return []
    target = posixpath.join(paths.state_dir, REMOVED_RECORD)
    if removed:
        entries = []
        if os.path.isfile(target):
            try:
                with open(target, encoding="utf-8-sig") as handle:
                    entries = json.load(handle).get("entries") or []
            except (OSError, ValueError, AttributeError):
                return []  # a record that cannot be read is never written over: nothing is removed
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not _save_json(target, {"entries": list(entries) + [dict(entry, removed=when) for entry in removed]}):
            return []
    for path, settings, raw in changes:
        try:
            save_settings(path, settings, raw)
        except OSError:
            pass
    if isinstance(record, dict):
        try:
            os.remove(suspended)
        except OSError:
            pass
    return [marked(paths, "line", say(paths, "judge_removed", files=", ".join(sources),
                                      record=".claude/docket/" + REMOVED_RECORD))]


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
