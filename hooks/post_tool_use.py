"""PostToolUse hook, every tool.

Every tool result carries the clock line `[docket] clock: YYYY-MM-DD HH:MM` as additional context once the
project is set up: the stamp needs no tool call (M7). In a sealed run an agent's spawn adds the rule for the wait:
no text (V4). Every tool call clears the Stop hook's refusal marker: the next Stop is a first pass again (V9). A shell command that matched forbidden_commands and ran
was allowed by the user in the dialog: LOG `grant` (M1). So was a concept-mode write outside the work dir.

Dockets are append-only: after an edit every previous line (snapshot taken in pre_tool_use) must
still be there, in order. A violation is reported to the model with exit 2 and logged; the hook
cannot undo it, the model must restore the lines. New lines that cite a `file:line` which does not
exist undo the write (the snapshot is restored, a skeleton removed): a citation is checked at the one
moment the line may still change. So does a new line whose decision is `observe` before the means were
spent (V19: cause, repro and impact named, the repro command run three times by the orchestrator and not
failed all three, a second diagnoser returned). Point flips `[ ]` -> `[x]` / `[~]` become LOG
events (point-delivered, deviation): the LOG knows every point, written by the hook only.

Specs: the ready gate. A spec whose state became `ready` must have a verifiable criterion for
every point, no open user decision and a filled reference-update section; otherwise the hook sets
the state back to `draft` (so the file stays writable) and sends the gaps back. A passed gate is
logged once as `ready`.
"""
import json
import os
import posixpath
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (Paths, absolute, bullets, clock_line, config_values, frontmatter, load_state, log_event,
                        log_has, missing_citations, observe_problems, observed, read_input, refuse, set_context, under)

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
CRITERION = re.compile(r"(→|->|\bcontains\b|\bexits?\b|\bprints?\b|\bequals?\b|==)", re.IGNORECASE)


def main():
    hook_input = read_input()
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    result = 0
    # Since PostToolUse runs for every tool (M7), the file checks run only after a write: a Read is no edit.
    if hook_input.get("tool_name") in WRITE_TOOLS and isinstance(file_path, str) and file_path:
        path = absolute(file_path, paths.cwd)
        if under(path, paths.dockets):
            result = docket_check(path, paths, session_id, hook_input.get("transcript_path"))
        elif under(path, paths.specs):
            result = ready_gate(path, paths, session_id)
        elif (under(path, paths.root) and not under(path, paths.work) and os.path.isfile(paths.config)
              and load_state(paths, session_id).get("mode") == "concept"):
            # pre_tool_use asked; the write ran, so the user allowed it in the dialog.
            log_event(paths, session_id, "", "grant", "write " + path[len(paths.root):].lstrip("/")[:90])
    elif hook_input.get("tool_name") in ("Bash", "PowerShell"):
        log_grant(tool_input, paths, session_id)
    if result:
        return result
    if os.path.isfile(paths.config):
        state = load_state(paths, session_id)
        if not hook_input.get("agent_id"):
            state["refused"] = False  # work went on after a refusal: the next Stop is a first pass again
        context = clock_line(paths, session_id, state)
        if (state.get("mode") in ("sealed", "rewrite") and hook_input.get("tool_name") in ("Agent", "Task", "Workflow")
                and not hook_input.get("agent_id")):
            # V4: the rule at the moment it applies (luradb-web-client 2026-11-06: 76 of 128 texts were a bare stamp
            # after a spawn; the prompt hook that states the rule ran twice in three hours).
            context += (" · While agents run, end the turn without any text; their notifications resume you. If the "
                        "harness asks what you are doing, answer in at most five words, no stamp.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))
    return 0


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

def docket_check(path, paths, session_id, transcript_path=None):
    state = load_state(paths, session_id)
    spec_id = state.get("spec", "")
    set_context(paths, spec_id)
    snap = posixpath.join(paths.state_dir, f"snap-{spec_id}.md")
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as handle:
            after = handle.read()
    except OSError:
        return 0
    if not os.path.isfile(snap):
        before = None  # the skeleton: no earlier version
    else:
        try:
            with open(snap, encoding="utf-8-sig", errors="ignore") as handle:
                before = handle.read().rstrip()
        except OSError:
            return 0
    if before is not None and not (after.startswith(before) or before_lines_kept(before, after)):
        log_event(paths, session_id, spec_id, "docket-rewritten", "append-only violated")
        return refuse("[docket] The docket is append-only. Restore every previous line unchanged and add your "
                      "change as new lines below; a point changes state by a new line, never by editing the old one.")
    missing = missing_citations("\n".join(new_lines(before or "", after)), paths)
    if missing:
        # Checked now, at the one moment a line may still change: once written, the docket is append-only, and a
        # citation found wrong at DELIVERED forced an in-place edit against that rule (luradb-web-client 2026-11-06).
        undo_write(path, snap, before)
        log_event(paths, session_id, spec_id, "docket-undone", "cited locations missing: " + ", ".join(missing)[:90])
        return refuse("[docket] The write was undone, the docket is as before: it cited locations that do not exist: "
                      + ", ".join(missing) + ". Cite `path/file.ext:line` relative to the repo root, as it is on "
                      "disk, then write the lines again.")
    for line in bullets("\n".join(new_lines(before or "", after))):
        # V19: `observe` is the late exit; like a citation it is checked while the line may still change.
        early = observe_problems(line, paths, spec_id, transcript_path) if observed(line) else []
        if early:
            undo_write(path, snap, before)
            log_event(paths, session_id, spec_id, "docket-undone", "observe before the means were spent")
            return refuse("[docket] The write was undone, the docket is as before: `observe` is the late exit, and for `"
                          + line[:60] + "` " + "; ".join(early) + ". Spend the means first, then write the line again; "
                          "a failure that is shown is `fix` or `deviation`, a finding that is refuted is rejected.")
    log_points(paths, session_id, spec_id, before or "", after)
    return 0


def new_lines(before, after):
    old = set(before.splitlines())
    return [line for line in after.splitlines() if line not in old]


def undo_write(path, snap, before):
    """The docket as it was before the write: the snapshot's text, or no file for the skeleton."""
    try:
        if before is None:
            os.remove(path)
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(before + "\n")
    except OSError:
        pass


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


def count_marks(text):
    return {mark: len(re.findall(r"(?<!`)\[" + re.escape(mark) + r"\](?!`)", text)) for mark in ("x", "~")}


def log_points(paths, session_id, spec_id, before, after):
    """Each newly flipped point is one LOG line: point-delivered for [x], deviation for [~]."""
    was, now = count_marks(before), count_marks(after)
    delivered = now["x"] - was["x"]
    deviated = now["~"] - was["~"]
    if delivered > 0:
        log_event(paths, session_id, spec_id, "point-delivered", f"count={delivered}")
    if deviated > 0:
        log_event(paths, session_id, spec_id, "deviation", f"count={deviated}")


# --- specs: the ready gate ---------------------------------------------------

def ready_gate(path, paths, session_id):
    fields = frontmatter(path)
    spec_id = fields.get("id", "")
    if fields.get("state", "").lower() != "ready":
        return 0
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return 0
    problems = ready_problems(text)
    if problems:
        demote(path, text)
        return refuse("[docket] Not ready, state set back to draft: " + "; ".join(problems)
                      + ". Fix these, then set `state: ready` again.")
    if spec_id and not log_has(paths, spec_id, "ready"):
        log_event(paths, session_id, spec_id, "ready")
    return 0


def section(text, title):
    match = re.search(r"^##\s+" + re.escape(title) + r"\b.*?$(.*?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else ""


def table_rows(block):
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell) or not any(cells):
            continue
        rows.append(cells)
    return rows


TEMPLATE_GOAL = "One paragraph: what users or operators get, and why now."


def ready_problems(text):
    problems = []
    points = [row for row in table_rows(section(text, "Points")) if row and row[0].lower() != "#"]
    if not points:
        problems.append("no point in the Points table")
    for row in points:
        criterion = row[2] if len(row) > 2 else ""
        if not criterion or not CRITERION.search(criterion):
            problems.append(f"point {row[0]} has no verifiable criterion (`<command> → <expected>` or `<file> contains <x>`)")
    for row in table_rows(section(text, "User decisions")):
        if row[0].lower() in ("question", "") or len(row) < 3:
            continue
        if not row[2].strip():
            problems.append(f"user decision open: {row[0][:60]}")
    reference = section(text, "Reference update")
    rows = [row for row in table_rows(reference) if row and row[0].lower() != "file"]
    if not rows and not re.search(r"\bnone\b", reference, re.IGNORECASE):
        problems.append("Reference update section empty: name the reference docs that change, or `none` with the reason")
    goal = section(text, "Goal")
    if not goal.strip() or TEMPLATE_GOAL in goal or re.search(r"^#\s*<Title>", text, re.MULTILINE):
        problems.append("Goal still holds the template text")
    return problems


def demote(path, text):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(re.sub(r"^(state:\s*)ready\b", r"\1draft", text, count=1, flags=re.MULTILINE | re.IGNORECASE))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
