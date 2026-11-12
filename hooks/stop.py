"""Stop hook (REWORK-SPEC 1d). It acts only in a session with the run mark (`mode: sealed`) of a project with
DOCKET.md; every other Stop passes without a word.

In a run:
- A Stop while agents run (background_tasks, or an agent whose notification has not reached the model) is a wait and
  passes; its time stays in the state (`waiting`), so the run stays live for other sessions (session_live). Until
  this run wrote its Terminal state, or while a command of the gate runs in the background, a background shell is a
  wait too; an unchanged DELIVERED of an earlier run counts as written, also at the Stop right after the gate refused
  it, which halts.
- The spec's docket has a Terminal state (the last `state:` block of its `## Terminal state` section; an unchanged
  BLOCKED or DISSENT block an earlier run left does not count, a DELIVERED and a block whose `state:` line changed in
  place do). BLOCKED, and DISSENT from a docket of 0.5, close the spec unchecked. DELIVERED passes
  the gate first: no point `[ ]`, and every command of DOCKET.md `check` and the acceptance command of every `[x]`
  point ran after the last agent returned and did not fail; a run in the background counts with the exit code of its
  notification (read from the transcript, nothing is run). A gap is the
  one refusal of this hook: `[docket gate]` with the facts.
- No Terminal state: the turn goes on once with a `[docket state]` statement (the spec, k of m points open).
- The second Stop right after either, with no tool call between (`stop_hook_active` and `refused`), passes: LOG
  `stop-forced`, field `halted`, one line to the user (systemMessage) with where the run stands and the command that
  resumes it. post_tool_use clears `refused` at the orchestrator's next tool call, user_prompt at every prompt.
- A closed spec: lock, spec state, metrics, LOG (end_spec). The series goes on with its next spec (`[docket state]
  Spec i of n is running …`) or ends: run mark `none`, LOG `run-closed`, commit_records, and once a `[docket state]`
  statement with the count of every outcome. The Stop after it runs without the run mark and passes.
"""
import json
import os
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (RUN_MODES, Paths, claim_lock, commit_records, frontmatter, gate_commands, load_state, log_event,
                        marked, notice, open_predecessors, point_counts, points_summary, project_head, quoted, read_input,
                        read_text, records_commands, refuse, release_lock, repo_facts, run_line, save_state, say,
                        set_spec_state, spec_title, terminal_mark, terminal_section, untouched_since, waiting,
                        work_dir_dirty, write_metrics)


def main():
    hook_input = read_input()
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    if getattr(state, "unreadable", False):
        return 0  # the state file is closed to this hook: no run is known
    if state.get("mode") not in RUN_MODES or not os.path.isfile(paths.config):
        return 0
    return run_stop(paths, session_id, state, hook_input)


def run_stop(paths, session_id, state, hook_input):
    transcript = hook_input.get("transcript_path")
    spec_id = state.get("spec", "")
    terminal = terminal_section(paths, spec_id, state)
    problems, running = gate_problems(paths, spec_id, transcript) if terminal["kind"] == "delivered" else ([], False)
    # stop_hook_active stays true for the rest of a turn any Stop hook once blocked; only this hook's own word makes
    # the next Stop a second pass.
    second = bool(hook_input.get("stop_hook_active")) and bool(state.get("refused"))
    # An earlier run's unchanged DELIVERED counts as written, so a background shell (a dev server) is no wait: a gap
    # of the gate reaches the model as `[docket gate]`, and the Stop right after that refusal halts (Festlegung
    # „Zweiter Stop nach dem Prüf-Tor“). While a command of the gate still runs in the background, a background shell
    # is a wait again: its notification starts the next turn, whose Stop reads the gate with its result.
    if waiting(hook_input, transcript, bool(terminal["kind"]) and not running):
        state["waiting"] = time.time()
        save_state(paths, session_id, state)
        return 0
    title = spec_title(paths, spec_id) or spec_id
    if terminal["kind"]:
        if not problems:
            return end_spec(paths, session_id, state, terminal["kind"], transcript)
        if second:
            return halt(paths, session_id, state, "; ".join(problems))
        mark_refused(paths, session_id, state)
        return refuse(marked(paths, "gate", say(paths, "gate", title=title, spec=spec_id,
                                                problems="; ".join(problems))))
    docket = paths.docket_file(spec_id)
    if docket:
        counts = point_counts(read_text(docket))
        old = terminal_section(paths, spec_id)["kind"]  # an earlier run's block, unchanged in this run
        statement = say(paths, "no_terminal_old" if old else "no_terminal", title=title, spec=spec_id,
                        kind=old.upper(), open=counts[" "], total=sum(counts.values()))
    else:
        statement = say(paths, "no_docket", title=title, spec=spec_id)
    if second:
        return halt(paths, session_id, state, statement)
    mark_refused(paths, session_id, state)
    return notice(marked(paths, "state", statement))


def mark_refused(paths, session_id, state):
    state["refused"] = True
    save_state(paths, session_id, state)


def halt(paths, session_id, state, why):
    """The second Stop right after the hook's own word: the turn ends, the spec stays marked and in progress."""
    spec_id = state.get("spec", "")
    state.update({"refused": False, "halted": True})
    save_state(paths, session_id, state)
    log_event(paths, session_id, spec_id, "stop-forced", why[:120])
    line = say(paths, "halted", title=spec_title(paths, spec_id) or spec_id, points=points_summary(paths, spec_id),
               specs=", ".join([spec_id] + list(state.get("queue") or [])))
    print(json.dumps({"systemMessage": marked(paths, "line", line)}, ensure_ascii=True))
    return 0


def gate_problems(paths, spec_id, transcript):
    """The gate before DELIVERED (Festlegung „Umfang des Prüf-Tors“): open points, the project's check, the acceptance
    commands of the delivered points (docket_lib.gate_commands). Without a readable transcript only the open points
    count; without `check` in DOCKET.md (a placeholder included) only the acceptance commands and the open points.
    Returns (problems, running): running while a command of the gate still runs in the background."""
    body = read_text(paths.docket_file(spec_id))
    open_points = point_counts(body)[" "]
    problems, running = gate_commands(paths, spec_id, body, transcript)
    return ([say(paths, "gate_open", k=open_points)] if open_points else []) + problems, running


def end_spec(paths, session_id, state, kind, transcript):
    """The spec is closed: lock, spec state, metrics, LOG. The series goes on with its next spec after DELIVERED, and
    after BLOCKED or DISSENT when the spec left the project as it found it or all its work is pushed and the tree is
    clean (luradb-web-client 2026-11-10: a spec blocked at 0 points ended a series of three); a spec that left
    commits or changes behind ends the series, since the next spec's push would carry them. A queued spec whose
    predecessor is not done is passed over. /docket:stop ends the series after the spec at work."""
    spec_id = state.get("spec", "")
    release_lock(paths, spec_id)
    set_spec_state(paths, spec_id, "done" if kind == "delivered" else "ready")
    write_metrics(paths, spec_id, transcript, kind)  # before the LOG line: its commit carries the metrics
    log_event(paths, session_id, spec_id, kind, "spec closed")
    state.setdefault("outcomes", {})[spec_id] = kind
    queue = [s for s in (state.get("queue") or []) if s]
    skipped = dict(state.get("skipped") or {})
    git = repo_facts(paths)
    settled = bool(git) and bool(git["upstream"]) and git["ahead"] == 0 and not git["dirty"]
    goes_on = kind == "delivered" or settled or untouched_since(paths, state.get("sealed_head", ""))
    if goes_on and not state.get("stop_requested"):
        while queue:
            following = queue.pop(0)
            found = frontmatter(paths.spec_file(following) or "").get("state", "").lower()
            if found not in ("ready", "in-progress"):
                skipped[following] = ["state", found or "-"]  # finished elsewhere meanwhile
                continue
            waits = open_predecessors(paths, [following])
            if waits:
                log_event(paths, session_id, following, "passed-over", "waits for " + ", ".join(p for _, p in waits))
                skipped[following] = ["waits", [p for _, p in waits]]
                continue
            if claim_lock(paths, following, session_id):
                skipped[following] = ["held", ""]  # marked elsewhere meanwhile
                continue
            state.update({"mode": "sealed", "spec": following, "queue": queue, "refused": False, "skipped": skipped,
                          "sealed_head": project_head(paths), **terminal_mark(paths, following)})
            save_state(paths, session_id, state)
            log_event(paths, session_id, following, "sealed", "from queue")
            set_spec_state(paths, following, "in-progress")
            return notice(run_line(paths, state, mark="state"))
    if queue:
        log_event(paths, session_id, spec_id, "queue-dropped", ", ".join(queue))
    state["skipped"] = skipped
    return end_series(paths, session_id, state, spec_id)


def end_series(paths, session_id, state, last):
    """The series is over, in order or early: the run mark goes, LOG `run-closed`, the records are committed where
    DOCKET.md `commit_records` says so, and the turn goes on once with the outcome of every spec. The closing message
    is the model's, from the template of the implement command."""
    outcomes = state.get("outcomes") or {}
    series = list(state.get("series") or []) or list(outcomes)
    skipped = state.get("skipped") or {}
    stopped = bool(state.get("stop_requested"))
    state.update({"mode": "none", "spec": "", "queue": [], "refused": False})
    save_state(paths, session_id, state)
    log_event(paths, session_id, last, "run-closed")
    commit_records(paths)
    unstarted = [spec_id for spec_id in series if spec_id not in outcomes]
    reasons = [unstarted_reason(paths, spec_id, skipped.get(spec_id), stopped, last) for spec_id in unstarted]
    text = say(paths, "series_end", d=sum(1 for kind in outcomes.values() if kind == "delivered"),
               b=sum(1 for kind in outcomes.values() if kind != "delivered"), k=len(unstarted),
               rest=f" ({'; '.join(reasons)})" if reasons else "")
    commands = records_commands(paths) if work_dir_dirty(paths) else None
    if commands:
        text += say(paths, "series_records", add=commands[0], commit=commands[1])
    return notice(marked(paths, "state", text))


def unstarted_reason(paths, spec_id, skipped, stopped, last):
    name = lambda spec: quoted(paths, spec_title(paths, spec) or spec)
    kind, value = (list(skipped) + ["", ""])[:2] if skipped else ("", "")
    if kind == "state":
        return say(paths, "unstarted_state", title=name(spec_id), state=value)
    if kind == "held":
        return say(paths, "unstarted_held", title=name(spec_id))
    if kind != "waits" and stopped:
        return say(paths, "unstarted_stopped", title=name(spec_id))
    waits = value if kind == "waits" else [p for _, p in open_predecessors(paths, [spec_id])]
    if waits:
        return say(paths, "unstarted_waits", title=name(spec_id), on=", ".join(name(p) for p in waits))
    return say(paths, "unstarted_ended", title=name(spec_id), on=name(last))


if __name__ == "__main__":
    sys.exit(main())
