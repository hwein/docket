"""PostToolUse hook, matcher Edit|Write|MultiEdit.

Dockets are append-only: after an edit every previous line (snapshot taken in pre_tool_use) must
still be there, in order. A violation is reported to the model with exit 2 and logged; the hook
cannot undo it, the model must restore the lines. Point flips `[ ]` -> `[x]` / `[~]` become LOG
events (point-delivered, deviation): the LOG knows every point, written by the hook only.

Specs: the ready gate. A spec whose state became `ready` must have a verifiable criterion for
every point, no open user decision and a filled reference-update section; otherwise the hook sets
the state back to `draft` (so the file stays writable) and sends the gaps back. A passed gate is
logged once as `ready`.
"""
import os
import posixpath
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (Paths, absolute, frontmatter, load_state, log_event, log_has, read_input, refuse,
                        under)

CRITERION = re.compile(r"(→|->|\bcontains\b|\bexits?\b|\bprints?\b|\bequals?\b|==)", re.IGNORECASE)


def main():
    hook_input = read_input()
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return 0
    paths = Paths(hook_input)
    path = absolute(file_path, paths.cwd)
    session_id = hook_input.get("session_id")
    if under(path, paths.dockets):
        return docket_check(path, paths, session_id)
    if under(path, paths.specs):
        return ready_gate(path, paths, session_id)
    return 0


# --- dockets -----------------------------------------------------------------

def docket_check(path, paths, session_id):
    state = load_state(paths, session_id)
    spec_id = state.get("spec", "")
    snap = posixpath.join(paths.state_dir, f"snap-{spec_id}.md")
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as handle:
            after = handle.read()
    except OSError:
        return 0
    if not os.path.isfile(snap):
        log_points(paths, session_id, spec_id, "", after)
        return 0
    try:
        with open(snap, encoding="utf-8-sig", errors="ignore") as handle:
            before = handle.read().rstrip()
    except OSError:
        return 0
    if after.startswith(before) or before_lines_kept(before, after):
        log_points(paths, session_id, spec_id, before, after)
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
    if re.search(r"<[a-z][^>]*>", section(text, "Goal")):
        problems.append("Goal still holds template placeholders")
    return problems


def demote(path, text):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(re.sub(r"^(state:\s*)ready\b", r"\1draft", text, count=1, flags=re.MULTILINE | re.IGNORECASE))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
