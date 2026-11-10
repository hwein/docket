"""What the docket pane shows and does, as JSON on stdout. Called by the plugin's hooks module (hooks/mod), never
by Claude Code's hook events and never by the model.

    python docket_ui.py overview  --root <project root> [--session <id>]
    python docket_ui.py supersede --root <project root> --spec s-xxxxxxxx --by s-yyyyyyyy [--session <id>]
    python docket_ui.py stop      --root <project root> --session <id>
    python docket_ui.py handoff   --root <project root> --session <id> --command "/docket:implement s-xxxxxxxx"

The pane reads nothing by itself: the register, the runs and the LOG come from the same functions the hooks use, so
both read one project the same way. A state the pane sets is written here, with its LOG line, as a hook would.
"""
import argparse
import json
import os
import posixpath
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docket_lib import (CONCEPT_ID, RUN_MODES, SPEC_ID, Paths, docket_counts, frontmatter, in_order, language_code,  # noqa: E402
                        load_state, lock_holder, log_event, log_lines, norm, other_sessions, position, queued_elsewhere, read_text,
                        save_handoff, save_state, section_text, session_live, set_spec_state, spec_records, spec_title,
                        terminal_section, work_dir_dirty)

SPEC_STATES = ("draft", "ready", "in-progress", "done", "superseded")
OPEN_STATES = ("draft", "ready", "in-progress")
LOG_TAIL = 200


def open_decisions(path, title):
    """Rows of a decisions table (`| Question | Recommendation | Answer (date) |`) whose answer cell is empty."""
    count = 0
    for line in section_text(read_text(path), title).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0] or set(cells[0]) <= set("-: ") or cells[0].lower() == "question":
            continue
        if not cells[2]:
            count += 1
    return count


def concept_records(paths):
    """Frontmatter of every concept (id, title, topic, state) and its file, by path."""
    found = []
    if not os.path.isdir(paths.concepts):
        return found
    for topic in sorted(os.listdir(paths.concepts)):
        folder = posixpath.join(paths.concepts, topic)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = posixpath.join(folder, name)
            fields = frontmatter(path) if name.lower().endswith(".md") else {}
            if CONCEPT_ID.fullmatch(fields.get("id", "")):
                found.append({"id": fields["id"].lower(), "title": fields.get("title", ""),
                              "topic": fields.get("topic", "") or topic, "state": fields.get("state", "").lower(),
                              "path": path, "decisions": open_decisions(path, "Open decisions")})
    return found


def superseded_by(paths):
    """{spec: the spec that replaced it}, from the LOG's `superseded` lines (`by=s-…`); the last line counts."""
    found = {}
    for line in log_lines(paths):
        parts = line.split()
        if len(parts) > 2 and parts[2] == "superseded":
            match = re.search(r"\bby=(s-[0-9a-f]{8})\b", line)
            if match:
                found[parts[1].lower()] = match.group(1)
    return found


def runs(paths, session_id):
    """Every session that runs a spec right now: this one first."""
    found = []
    for other in ([session_id] if session_id else []) + other_sessions(paths, session_id or ""):
        state = load_state(paths, other)
        if state.get("mode") not in RUN_MODES or not session_live(paths, other):
            continue
        spec_id = state.get("spec", "")
        counts = docket_counts(paths, spec_id)
        i, n = position(state)
        found.append({"session": other, "mine": other == session_id, "mode": state.get("mode"), "spec": spec_id,
                      "title": spec_title(paths, spec_id) or spec_id, "i": i, "n": n,
                      "queue": list(state.get("queue") or []), "stop_requested": bool(state.get("stop_requested")),
                      "points": None if counts is None else {"done": counts["x"], "deviating": counts["~"],
                                                             "open": counts[" "]}})
    return found


def overview(paths, session_id):
    if not os.path.isfile(paths.config):
        return {"ok": True, "initialized": False, "root": paths.root, "work": paths.work}
    records = spec_records(paths)
    by_id = {r["id"]: r for r in records}
    replaced = superseded_by(paths)
    # Where a spec stands in implementation order: a selection of several runs as a series in that order.
    order = {r["id"]: index for index, r in enumerate(in_order(records))}
    live = runs(paths, session_id)
    running = {run["spec"]: run["session"] for run in live}
    specs = []
    for record in records:
        spec_id = record["id"]
        counts = docket_counts(paths, spec_id)
        terminal = terminal_section(paths, spec_id)
        specs.append({
            "id": spec_id, "title": record["title"] or spec_id, "topic": record["topic"], "state": record["state"],
            "concept": record["concept"] or "", "follows": record["follows"], "created": record["created"],
            "path": record["path"], "docket": paths.docket_file(spec_id) or "", "order": order[spec_id],
            "points": None if counts is None else {"done": counts["x"], "deviating": counts["~"], "open": counts[" "]},
            "result": {"kind": terminal["kind"], "text": terminal["result"] or terminal["reason"]} if terminal["kind"] else None,
            "decisions": open_decisions(record["path"], "User decisions") if record["state"] == "draft" else 0,
            "waits_for": [f for f in record["follows"]
                          if f in by_id and by_id[f]["state"] not in ("done", "superseded")],
            "running": running.get(spec_id, ""),
            "superseded_by": replaced.get(spec_id, "") if record["state"] == "superseded" else "",
        })
    counts = {state: sum(1 for s in specs if s["state"] == state) for state in SPEC_STATES}
    counts["open"] = sum(counts[state] for state in OPEN_STATES)
    counts["total"] = len(specs)
    return {"ok": True, "initialized": True, "root": paths.root, "work": paths.work,
            "language": language_code(paths) or "en", "config": paths.config, "log_path": paths.log,
            "counts": counts, "concepts": concept_records(paths), "specs": specs, "runs": live,
            "log": log_lines(paths)[-LOG_TAIL:], "dirty": work_dir_dirty(paths)}


def supersede(paths, session_id, spec_id, by_id):
    """`state: superseded` for a spec another spec replaces. A hook-side write: the model never sets this state, and
    a spec that runs, or waits in a running series, is left alone."""
    spec_id, by_id = (spec_id or "").lower(), (by_id or "").lower()
    records = {r["id"]: r for r in spec_records(paths)}
    if spec_id not in records:
        return {"ok": False, "error": "unknown-spec", "spec": spec_id}
    if by_id not in records or by_id == spec_id:
        return {"ok": False, "error": "unknown-successor", "spec": by_id}
    if records[by_id]["state"] == "superseded":
        return {"ok": False, "error": "successor-superseded", "spec": by_id}
    state = records[spec_id]["state"]
    if state == "superseded":
        return {"ok": False, "error": "already-superseded", "spec": spec_id}
    holder = lock_holder(paths, spec_id)
    if state == "in-progress" and holder and session_live(paths, holder):
        return {"ok": False, "error": "running", "spec": spec_id}
    if queued_elsewhere(paths, spec_id, ""):
        return {"ok": False, "error": "queued", "spec": spec_id}
    set_spec_state(paths, spec_id, "superseded")
    if frontmatter(records[spec_id]["path"]).get("state", "").lower() != "superseded":
        return {"ok": False, "error": "not-written", "spec": spec_id}
    log_event(paths, session_id, spec_id, "superseded", f"by={by_id}")
    return {"ok": True, "spec": spec_id, "by": by_id}


def stop(paths, session_id):
    """What `/docket:stop` records for a session: its series ends with the spec at work. The pane uses it for a run
    of another session, which no prompt of this one reaches."""
    state = load_state(paths, session_id)
    if state.get("mode") not in ("sealed", "rewrite") or not os.path.isfile(
            posixpath.join(paths.state_dir, f"state-{session_id}.json")):
        return {"ok": False, "error": "not-running", "session": session_id}
    state["stop_requested"] = True
    save_state(paths, session_id, state)
    log_event(paths, session_id, state.get("spec", ""), "stop-requested")
    return {"ok": True, "session": session_id, "spec": state.get("spec", "")}


def handoff(paths, session_id, command):
    """Records the command the pane hands to a session of its own: the app wraps that session's brief, and the
    prompt hook takes the command from this record where it is no longer the brief's first or last line."""
    if not os.path.isfile(paths.config):
        return {"ok": False, "error": "not-initialized"}
    if not save_handoff(paths, session_id, command):
        return {"ok": False, "error": "usage"}
    return {"ok": True, "command": command}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="docket_ui")
    parser.add_argument("action", choices=("overview", "supersede", "stop", "handoff"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--session", default="")
    parser.add_argument("--spec", default="")
    parser.add_argument("--by", default="")
    parser.add_argument("--command", default="")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print(json.dumps({"ok": False, "error": "usage"}))
        return 2
    # The project is the one the pane names, whatever directory or environment this process inherited.
    root = norm(os.path.abspath(args.root)).rstrip("/")
    os.environ["CLAUDE_PROJECT_DIR"] = root
    paths = Paths({"cwd": root})
    try:
        if args.action == "overview":
            result = overview(paths, args.session)
        elif args.action == "supersede":
            if not (SPEC_ID.fullmatch(args.spec.lower()) and SPEC_ID.fullmatch(args.by.lower())):
                result = {"ok": False, "error": "usage"}
            else:
                result = supersede(paths, args.session, args.spec, args.by)
        elif args.action == "handoff":
            result = handoff(paths, args.session, args.command)
        else:
            result = stop(paths, args.session) if args.session else {"ok": False, "error": "usage"}
    except Exception as error:  # the pane shows the reason; a traceback on stdout would not parse
        result = {"ok": False, "error": "failed", "detail": f"{type(error).__name__}: {error}"[:300]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
