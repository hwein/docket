"""Shared helpers for the docket hooks: hook input, paths, session state, LOG, frontmatter.

Every hook fails open: unreadable input or missing files never block Claude Code.
Nothing here prints to stdout; stdout of a hook enters the model's context.
"""
import calendar
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
from collections import Counter

# Claude Code reads a hook's stdout and stderr as UTF-8; Python on Windows writes the locale's code page (cp1252),
# which turned „…“ and umlauts of the German notices into replacement characters.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SESSION_ID = re.compile(r"[A-Za-z0-9_-]+")
SPEC_ID = re.compile(r"\bs-[0-9a-f]{8}\b")
CONCEPT_ID = re.compile(r"\bc-[0-9a-f]{8}\b")
# The modes of the session state (REWORK-SPEC 1d.7): `sealed` is the run mark, `none` everything else. A state file of
# 0.5 with `rewrite`, `closing`, `closed`, `concept`, `spec` or `init` is read as `none` (load_state).
RUN_MODES = ("sealed",)
STATE_VERSION = 2


def read_input():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def norm(path):
    return str(path).replace("\\", "/")


def working_directory(hook_input):
    return norm(hook_input.get("cwd") or os.getcwd())


def project_root(hook_input):
    # cwd follows cd and worktrees; CLAUDE_PROJECT_DIR stays where the session began.
    return norm(os.environ.get("CLAUDE_PROJECT_DIR") or working_directory(hook_input)).rstrip("/")


def absolute(file_path, base):
    path = norm(file_path)
    if not (path.startswith("/") or re.match(r"[A-Za-z]:/", path)):
        path = base.rstrip("/") + "/" + path
    return posixpath.normpath(path)


def under(path, directory):
    path, directory = norm(path).lower(), norm(directory).lower().rstrip("/")
    return path == directory or path.startswith(directory + "/")


def home():
    return norm(os.path.expanduser("~"))


def remote_key(root):
    try:
        url = (subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=5).stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        url = ""
    if url:
        key = re.sub(r"^[a-z]+://", "", url.lower())
        key = re.sub(r"^git@", "", key)
        key = re.sub(r"\.git$", "", key)
        return re.sub(r"[^a-z0-9]+", "-", key).strip("-")
    return "local-" + hashlib.sha1(root.lower().encode("utf-8")).hexdigest()[:12]


def work_dir(root):
    """Where specs, dockets, LOG and DOCKET.md live.

    Order: .claude/docket.json {"work_dir": ...}, then <root>/docket if it holds a DOCKET.md,
    then ~/.docket/<key>/ (the no-footprint default).
    """
    pointer = posixpath.join(root, ".claude", "docket.json")
    try:
        with open(pointer, encoding="utf-8-sig") as handle:
            configured = json.load(handle).get("work_dir")
        if isinstance(configured, str) and configured:
            return absolute(os.path.expanduser(configured), root)
    except (OSError, ValueError, AttributeError):
        pass
    local = posixpath.join(root, "docket")
    if os.path.isfile(posixpath.join(local, "DOCKET.md")):
        return local
    return posixpath.join(home(), ".docket", remote_key(root))


class Paths:
    def __init__(self, hook_input):
        self.cwd = working_directory(hook_input)
        self.root = project_root(hook_input)
        self.work = work_dir(self.root)
        self.specs = posixpath.join(self.work, "specs")
        self.dockets = posixpath.join(self.work, "dockets")
        self.concepts = posixpath.join(self.work, "concepts")
        self.log = posixpath.join(self.work, "LOG.md")
        self.config = posixpath.join(self.work, "DOCKET.md")
        self.metrics = posixpath.join(self.work, "metrics")
        self.state_dir = posixpath.join(self.root, ".claude", "docket")

    def spec_file(self, spec_id):
        return find_document(self.specs, spec_id)

    def docket_file(self, spec_id):
        return find_document(self.dockets, spec_id)

    def lock_file(self, spec_id):
        return posixpath.join(self.work, "locks", spec_id)


def config_values(paths):
    """The Configuration table of DOCKET.md as {key: value}; backticks stripped."""
    values = {}
    try:
        with open(paths.config, encoding="utf-8-sig", errors="ignore") as handle:
            for line in handle:
                # Cells split on unescaped pipes; `\|` inside a value (a regex alternative) is a literal pipe.
                cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
                if len(cells) >= 2 and cells[0] and not cells[0].startswith("-") and cells[0].lower() != "key":
                    value = cells[1].strip("`").strip()
                    if value.startswith("<"):
                        value = ""  # template placeholder, not configured
                    values.setdefault(cells[0].lower(), value)
    except OSError:
        pass
    return values


def check_commands(paths):
    """The project's check from DOCKET.md: every command in backticks in the `check` row, as many as the project
    has (`npm run check`, `npm test`); a row without backticks is one command. Placeholders count as none."""
    try:
        with open(paths.config, encoding="utf-8-sig", errors="ignore") as handle:
            for line in handle:
                cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
                if len(cells) >= 2 and cells[0].lower() == "check":
                    commands = re.findall(r"`([^`]+)`", cells[1]) or ([cells[1]] if cells[1] else [])
                    return [c.strip() for c in commands if c.strip() and not c.strip().startswith("<")]
    except OSError:
        pass
    return []


def language_code(paths):
    """The language of every message to the user, DOCKET.md `user_language`: "de" or "en" ("" when unset).
    The hooks' texts (SAY) follow it; the work dir line names it."""
    name = config_values(paths).get("user_language", "").lower()
    if not name:
        return ""
    return "de" if name.startswith(("de", "ger")) else "en"


# Every text a hook gives to the model or the user, in DOCKET.md `user_language` (REWORK-SPEC 1e.2): a statement of
# what holds now, no order and no rule; no hook prints a text of its own (test HookTexts).
SAY = {
    "de": {
        # The identifiers a hook's text begins with (marked); the same in both languages.
        "mark_line": "[docket]",
        "mark_gate": "[docket gate]",
        "mark_state": "[docket state]",
        # The Stop hook (REWORK-SPEC 1d): the gate before DELIVERED and the state statements of a run, as facts.
        "gate": "Das Docket von {title} ({spec}) sagt DELIVERED; {problems}.",
        "gate_open": "{k} Punkt(e) stehen noch offen `[ ]`",
        "gate_check_missing": "der Check `{command}` lief in dieser Sitzung nicht",
        "gate_check_before": "der Check `{command}` lief zuletzt vor der Rückkehr des letzten Agenten",
        "gate_check_failed": "der Check `{command}` scheiterte in seinem letzten Lauf",
        "gate_point_missing": "der Akzeptanzbefehl von Punkt {point} (`{command}`) lief in dieser Sitzung nicht",
        "gate_point_before": ("der Akzeptanzbefehl von Punkt {point} (`{command}`) lief zuletzt vor der Rückkehr des "
                              "letzten Umsetzers oder Fixers"),
        "gate_point_failed": "der Akzeptanzbefehl von Punkt {point} (`{command}`) scheiterte in seinem letzten Lauf",
        "no_terminal": "Das Docket von {title} ({spec}) hat keinen Terminal state; {open} von {total} Punkten sind offen.",
        "no_docket": "{title} ({spec}) hat noch kein Docket.",
        "halted": ("{title}: Der Lauf hat angehalten ({points}). `/docket:implement {specs}` setzt ihn fort, "
                   "jede Nachricht von dir auch."),
        "series_end": "Die Reihe ist zu Ende: {d} geliefert, {b} blockiert, {k} nicht begonnen{rest}.",
        "series_records": " Die Protokolle sind nicht committet: `{add}`, dann `{commit}`.",
        "unstarted_waits": "{title} wartet auf {on}",
        "unstarted_ended": "der Lauf endete bei {on} vor {title}",
        "unstarted_stopped": "{title} nach /docket:stop",
        "unstarted_state": "{title} steht im Zustand {state}",
        "unstarted_held": "{title} hält eine andere Sitzung",
        # The run mark (mark_run) and the state lines of user_prompt.py and session_start.py: facts, no order.
        "run_line": "Spec {i} von {n} läuft: {title} ({spec}); ihr Docket liegt unter {docket} ({points}).",
        "run_after": " Danach: {queue}.",
        "points_summary": "{x} geliefert, {d} abweichend, {o} offen",
        "no_docket_yet": "noch kein Docket",
        "docket_unreadable": "Docket nicht lesbar",
        "no_run": "Kein Lauf markiert: {reason}",
        "run_unknown": "keine Spec {ids} in diesem Projekt.",
        "run_no_ready_concept": "keine Spec des Konzepts {concept} ist im Zustand ready.",
        "run_no_ready": "keine Spec ist im Zustand ready.",
        "run_no_runnable": "keine ready-Spec hat alle Vorgänger erledigt.",
        "run_forms": ("/docket:implement nimmt eine Spec-Id (s-xxxxxxxx, mehrere in Reihenfolge), eine Konzept-Id "
                      "(c-xxxxxxxx: seine ready-Specs), --all (jede ready-Spec) oder nichts (die nächste ready-Spec)."),
        "run_order": "{pairs} (`follows:` oder `after:` in der Spec).",
        "run_order_pair": "{spec} braucht zuerst {predecessor}, und das ist nicht erledigt",
        "run_held": "„{title}“ läuft in einer anderen Sitzung (Sitzung {session}).",
        "run_queued": "„{title}“ wartet in der Reihe einer anderen Sitzung (Sitzung {session}).",
        "run_other": "diese Sitzung arbeitet an „{title}“.",
        "not_set_up": ("Unter {work} gibt es kein DOCKET.md: Das Projekt ist für docket nicht eingerichtet, "
                       "`/docket:init` richtet es ein; bis dahin beobachten die docket-Hooks nur."),
        "work_dir": "Arbeitsordner: {work}; DOCKET.md: {config}; user_language: {language}.",
        "session_state": "Sitzungszustand: {mode}{spec}.",
        "runs_open": ("Versiegelte Läufe ohne Terminal state: {ids}. `/docket:implement <id>` nimmt einen aus seinem "
                      "Docket wieder auf."),
        "runs_stopped": ("Angehaltene Läufe mit offenen Punkten: {items}. `/docket:implement <id>` nimmt einen beim "
                         "ersten offenen Punkt wieder auf."),
        "run_stopped_item": "{spec} ({n} offen)",
        "last_log": "Letzte LOG-Zeile: {line}",
        "judge_removed": ("Der Judge-Eintrag aus docket 0.5 ist aus {files} entfernt; der entfernte Eintrag steht in "
                          "{record}."),
        "delivered": "geliefert",
        "blocked": "angehalten",
        "dissent": "Dissens",
        "interrupted": "unterbrochen",
        "running": "läuft",
        "not_started": "nicht begonnen",
        "series_interrupted": "Unterbrochene Reihe: {items}. `/docket:implement {specs}` setzt sie fort.",
        "series_running": "Reihe: {items}.",
        # The project rules of DOCKET.md (pre_tool_use.py): a fact with key, value and file, no instruction.
        "rule_forbidden_paths": "[docket rule: forbidden_paths] {path} trifft forbidden_paths `{value}` in DOCKET.md "
                                "({config}); es wurde nichts geschrieben.",
        "rule_push_branch": "[docket rule: push_branch] Der Push geht auf `{branch}`; push_branch in DOCKET.md ({config}) "
                            "ist `{value}`. Der Push lief nicht.",
        "rule_commit_regex": "[docket rule: commit_regex] Die Commit-Nachricht `{message}` passt nicht zu commit_regex "
                             "`{value}` in DOCKET.md ({config}). Der Commit lief nicht.",
        "rule_forbidden_commands": "[docket rule: forbidden_commands] Der Befehl steht auf der Liste forbidden_commands "
                                   "`{value}` in DOCKET.md ({config}) und wurde nicht ausgeführt.",
        "rule_forbidden_commands_ask": "[docket rule: forbidden_commands] Der Befehl steht auf der Liste "
                                       "forbidden_commands `{value}` in DOCKET.md ({config}). Hier erlaubt, läuft er "
                                       "einmal; das LOG hält die Freigabe fest.",
    },
    "en": {
        "mark_line": "[docket]",
        "mark_gate": "[docket gate]",
        "mark_state": "[docket state]",
        "gate": "The docket of {title} ({spec}) says DELIVERED; {problems}.",
        "gate_open": "{k} point(s) still open `[ ]`",
        "gate_check_missing": "the check `{command}` did not run in this session",
        "gate_check_before": "the check `{command}` last ran before the last agent returned",
        "gate_check_failed": "the check `{command}` failed in its last run",
        "gate_point_missing": "the acceptance command of point {point} (`{command}`) did not run in this session",
        "gate_point_before": ("the acceptance command of point {point} (`{command}`) last ran before the last implementer "
                              "or fixer returned"),
        "gate_point_failed": "the acceptance command of point {point} (`{command}`) failed in its last run",
        "no_terminal": "The docket of {title} ({spec}) has no Terminal state; {open} of {total} points are open.",
        "no_docket": "{title} ({spec}) has no docket yet.",
        "halted": ("{title}: the run stopped ({points}). `/docket:implement {specs}` resumes it, and so does any "
                   "message from you."),
        "series_end": "The series is over: {d} delivered, {b} blocked, {k} not started{rest}.",
        "series_records": " The records are not committed: `{add}`, then `{commit}`.",
        "unstarted_waits": "{title} waits for {on}",
        "unstarted_ended": "the run ended at {on} before {title}",
        "unstarted_stopped": "{title} after /docket:stop",
        "unstarted_state": "{title} is in state {state}",
        "unstarted_held": "{title} is held by another session",
        "run_line": "Spec {i} of {n} is running: {title} ({spec}); its docket is at {docket} ({points}).",
        "run_after": " After it: {queue}.",
        "points_summary": "{x} delivered, {d} deviations, {o} open",
        "no_docket_yet": "no docket yet",
        "docket_unreadable": "docket unreadable",
        "no_run": "No run marked: {reason}",
        "run_unknown": "no spec {ids} in this project.",
        "run_no_ready_concept": "no spec of concept {concept} is in state ready.",
        "run_no_ready": "no spec is in state ready.",
        "run_no_runnable": "no ready spec has all its predecessors done.",
        "run_forms": ("/docket:implement takes a spec id (s-xxxxxxxx, several in order), a concept id (c-xxxxxxxx: its "
                      "ready specs), --all (every ready spec) or nothing (the next ready spec)."),
        "run_order": "{pairs} (`follows:` or `after:` in the spec).",
        "run_order_pair": "{spec} needs {predecessor} first, which is not done",
        "run_held": "“{title}” runs in another session (session {session}).",
        "run_queued": "“{title}” waits in another session's series (session {session}).",
        "run_other": "this session works on “{title}”.",
        "not_set_up": ("There is no DOCKET.md at {work}: the project is not set up for docket, and `/docket:init` sets "
                       "it up; until then the docket hooks only observe."),
        "work_dir": "This project's work dir: {work}; DOCKET.md: {config}; user_language: {language}.",
        "session_state": "Session state: {mode}{spec}.",
        "runs_open": ("Sealed runs without a terminal state: {ids}. `/docket:implement <id>` resumes one from its "
                      "docket."),
        "runs_stopped": ("Runs stopped with open points: {items}. `/docket:implement <id>` resumes one at its first "
                         "open point."),
        "run_stopped_item": "{spec} ({n} open)",
        "last_log": "Last LOG line: {line}",
        "judge_removed": "The judge entry of docket 0.5 was removed from {files}; the removed entry is in {record}.",
        "delivered": "delivered",
        "blocked": "stopped",
        "dissent": "dissent",
        "interrupted": "interrupted",
        "running": "running",
        "not_started": "not started",
        "series_interrupted": "Interrupted series: {items}. `/docket:implement {specs}` resumes it.",
        "series_running": "Series: {items}.",
        "rule_forbidden_paths": "[docket rule: forbidden_paths] {path} matches forbidden_paths `{value}` in DOCKET.md "
                                "({config}); nothing was written.",
        "rule_push_branch": "[docket rule: push_branch] The push goes to `{branch}`; push_branch in DOCKET.md ({config}) "
                            "is `{value}`. The push did not run.",
        "rule_commit_regex": "[docket rule: commit_regex] The commit message `{message}` does not match commit_regex "
                             "`{value}` in DOCKET.md ({config}). The commit did not run.",
        "rule_forbidden_commands": "[docket rule: forbidden_commands] The command is on the list forbidden_commands "
                                   "`{value}` in DOCKET.md ({config}) and was not run.",
        "rule_forbidden_commands_ask": "[docket rule: forbidden_commands] The command is on the list forbidden_commands "
                                       "`{value}` in DOCKET.md ({config}). Allowed here, it runs once; the LOG records "
                                       "the grant.",
    },
}


def say(paths, key, **values):
    return SAY[language_code(paths) or "en"][key].format(**values)


def marked(paths, mark, text):
    """A hook's text behind its identifier (Festlegung „Kennung der Rückmeldungen“): `line` [docket], `gate` [docket
    gate], `state` [docket state]; the refusals of the project rules carry `[docket rule: <key>]` in their entry."""
    return say(paths, "mark_" + mark) + " " + text


def quoted(paths, title):
    return f"„{title}“" if language_code(paths) == "de" else f"“{title}”"


def spec_title(paths, spec_id):
    path = paths.spec_file(spec_id)
    return frontmatter(path).get("title", "") if path else ""


# `after: s-xxxxxxxx` in a spec's goal: that spec lands first. Several ids may share one `after:`.
AFTER = re.compile(r"\bafter:((?:[\s`*,]*\bs-[0-9a-f]{8}\b)+)", re.IGNORECASE)


def after_ids(path, own=""):
    """The specs a spec names as `after: <id>` in its Goal section (commands/spec.md: another spec must land
    first). Only there: a Rewrite section quotes an `after:` it removed (luradb-web-client 2026-11-10)."""
    goal = section_text(read_text(path), "Goal")
    found = [s.lower() for run in AFTER.findall(goal) for s in SPEC_ID.findall(run)]
    return [s for s in dict.fromkeys(found) if s != own]


def spec_records(paths):
    """Frontmatter of every spec (id, title, topic, state, follows, concept, created) and its file, by path, with
    the specs it names as `after:` and its predecessors: both kinds, `follows:` and `after:`."""
    found = []
    if not os.path.isdir(paths.specs):
        return found
    for topic in sorted(os.listdir(paths.specs)):
        folder = posixpath.join(paths.specs, topic)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.lower().endswith(".md"):
                fields = frontmatter(posixpath.join(folder, name))
                if SPEC_ID.fullmatch(fields.get("id", "")):
                    found.append({"id": fields["id"].lower(), "title": fields.get("title", ""),
                                  "topic": fields.get("topic", "") or topic, "path": posixpath.join(folder, name),
                                  "state": fields.get("state", "").lower(),
                                  "follows": [s.lower() for s in SPEC_ID.findall(fields.get("follows", ""))],
                                  "concept": (CONCEPT_ID.search(fields.get("concept", "")) or [None])[0]
                                  if CONCEPT_ID.search(fields.get("concept", "")) else "",
                                  "created": fields.get("created", "")})
                    record = found[-1]
                    record["after"] = after_ids(record["path"], record["id"])
                    record["predecessors"] = list(dict.fromkeys(record["follows"] + record["after"]))
    return found


def in_order(records):
    """Implementation order: a spec after every predecessor (`follows:` and `after:`); ties by created date, then
    by path."""
    by_id = {r["id"]: r for r in records}
    remaining = sorted(records, key=lambda r: (r["created"], r["id"]))
    ordered, placed = [], set()
    while remaining:
        free = [r for r in remaining if all(f not in by_id or f in placed for f in r["predecessors"])]
        pick = (free or remaining)[0]  # the oldest free spec; no free one is a cycle: go on in date order
        ordered.append(pick)
        placed.add(pick["id"])
        remaining = [r for r in remaining if r["id"] != pick["id"]]
    return ordered


def ready_specs(paths, concept=""):
    """(id, title) of every spec in state ready, in implementation order; only one concept's when given."""
    records = [r for r in spec_records(paths) if r["state"] == "ready"
               and (not concept or r["concept"].lower() == concept.lower())]
    return [(r["id"], r["title"]) for r in in_order(records)]


def open_predecessors(paths, spec_ids):
    """For a run of the given specs in that order: every predecessor (`follows:` or `after:`) that is neither done
    nor earlier in the run, as (spec, predecessor). luradb-web-client 2026-11-10: a spec whose goal said `after:`
    two undone specs stood first in the pane's order, was sealed in a series and blocked at 0 points."""
    states = {r["id"]: r for r in spec_records(paths)}
    missing, earlier = [], set()
    for spec_id in spec_ids:
        record = states.get(spec_id.lower())
        for predecessor in record["predecessors"] if record else []:
            before = states.get(predecessor)
            if predecessor not in earlier and before is not None and before["state"] not in ("done", "superseded"):
                missing.append((spec_id, predecessor))
        earlier.add(spec_id.lower())
    return missing


def docket_counts(paths, spec_id):
    docket = paths.docket_file(spec_id)
    if not docket:
        return None
    try:
        with open(docket, encoding="utf-8-sig", errors="ignore") as handle:
            return point_counts(handle.read())
    except OSError:
        return None


def series_of(state):
    """The specs of this session's run in order: the series named at sealing, else the spec and its queue."""
    series = [s for s in (state.get("series") or []) if s]
    if not series:
        series = [s for s in [state.get("spec", "")] + list(state.get("queue") or []) if s]
    return series


def position(state):
    series = series_of(state)
    spec_id = state.get("spec", "")
    return (series.index(spec_id) + 1 if spec_id in series else 1), max(len(series), 1)


def find_document(directory, doc_id):
    """The one file under <directory>/<topic>/ whose name starts with the id."""
    if not doc_id or not os.path.isdir(directory):
        return None
    for topic in sorted(os.listdir(directory)):
        folder = posixpath.join(directory, topic)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.lower().startswith(doc_id.lower()) and name.lower().endswith(".md"):
                return posixpath.join(folder, name)
    return None


def frontmatter(path):
    """Minimal YAML frontmatter: `key: value` lines between the first two `---` lines."""
    fields = {}
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as handle:
            first = handle.readline().strip()
            if first != "---":
                return fields
            for line in handle:
                if line.strip() == "---":
                    break
                key, sep, value = line.partition(":")
                if sep:
                    value = re.sub(r"\s+#\s.*$", "", value)  # the template's `# draft | ready | ...` comment
                    fields[key.strip().lower()] = value.strip().strip("\"'")
    except OSError:
        pass
    return fields


def set_spec_state(paths, spec_id, new_state):
    """The spec's frontmatter state follows the run: in-progress on sealing, done on delivery, ready again
    after blocked or dissent. Written by hooks only; the comment behind the value stays."""
    path = paths.spec_file(spec_id)
    if not path:
        return
    try:
        with open(path, encoding="utf-8-sig") as handle:
            text = handle.read()
        text, n = re.subn(r"^(state:\s*)\S+", lambda m: m.group(1) + new_state, text, count=1, flags=re.MULTILINE)
        if n:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
    except OSError:
        pass


# --- session state -----------------------------------------------------------

def state_path(paths, session_id):
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        session_id = "shared"
    return posixpath.join(paths.state_dir, f"state-{session_id}.json")


# One state file per session, and many hooks at once: a message that starts 14 agents runs 14 PostToolUse hooks in
# the same second, every tool call of every agent runs two more. Each hook used to read the whole state, change a
# field and write the whole state back, and a read that failed on Windows (the file open in another process, or
# just being replaced) counted as "no state": mode none went back to disk and a spec session lost its mode
# (luradb-web-client 2026-11-10: 15 conceptors worked 10-25 minutes, every Write was refused).
# So: a read is retried and never answered with a default while the file exists; a save takes a lock, reads the
# file again and writes back only the fields this hook changed.
READ_WAIT = 2.0
INVALID_WAIT = 0.3  # a replace is atomic: a file that does not parse was not written by these functions
REPLACE_WAIT = 2.0
LOCK_WAIT = 3.0
LOCK_STALE = 10.0
_LOCKS_HELD = set()


def read_json(path, wait=READ_WAIT):
    """(data, problem) of a JSON file other processes replace: problem is "" (read), "missing" (no such file),
    "invalid" (not JSON after `wait` seconds) or "unreadable" (could not be opened in that time)."""
    start = time.time()
    while True:
        try:
            with open(path, encoding="utf-8-sig") as handle:
                return json.load(handle), ""
        except FileNotFoundError:
            return None, "missing"
        except ValueError:
            problem = "invalid"
        except OSError:
            problem = "unreadable"
        if time.time() - start >= (min(wait, INVALID_WAIT) if problem == "invalid" else wait):
            return None, problem
        time.sleep(0.01)


def write_json(path, data, wait=REPLACE_WAIT):
    """Atomic. On Windows the replace fails while another process has the file open: it is tried again for `wait`
    seconds, and the temporary file never stays behind. True when the file was written."""
    temp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(posixpath.dirname(path), exist_ok=True)
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=1)
        deadline = time.time() + wait
        while True:
            try:
                os.replace(temp, path)
                return True
            except OSError:
                if time.time() >= deadline:
                    return False
                time.sleep(0.01)
    except OSError:
        return False
    finally:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass


class file_lock:
    """`with file_lock(path):` one writer at a time on `path`, across processes: a lock file created exclusively.
    A lock older than LOCK_STALE seconds belongs to a hook that was killed and is taken over. After LOCK_WAIT
    seconds the block runs without the lock: a hook never hangs on it. Re-entrant within one process."""

    def __init__(self, path):
        self.path, self.lock, self.held = path, path + ".lock", False

    def __enter__(self):
        if self.path in _LOCKS_HELD:
            return self
        deadline = time.time() + LOCK_WAIT
        while True:
            try:
                os.close(os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                self.held = True
                _LOCKS_HELD.add(self.path)
                return self
            except FileNotFoundError:
                try:
                    os.makedirs(posixpath.dirname(self.lock), exist_ok=True)
                except OSError:
                    return self
            except OSError:  # held by another hook (FileExistsError; PermissionError while Windows deletes it)
                if not os.path.exists(self.lock):
                    return self  # not held: the folder takes no file, and waiting changes nothing
                try:
                    if time.time() - os.path.getmtime(self.lock) > LOCK_STALE:
                        os.remove(self.lock)
                        continue
                except OSError:
                    pass
            if time.time() >= deadline:
                return self
            time.sleep(0.004)

    def __exit__(self, *exc):
        if self.held:
            self.held = False
            _LOCKS_HELD.discard(self.path)
            deadline = time.time() + 1.0
            while True:
                try:
                    os.remove(self.lock)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    if time.time() >= deadline:
                        break
                    time.sleep(0.004)
        return False


class State(dict):
    """The session state as one hook read it. `base` is what the file held then: save_state writes back only what
    differs from it. `unreadable`: the file exists and could not be read; such a state is the default, and nothing of
    it is a fact about the session."""
    base = None
    unreadable = False


def default_state():
    return {"version": STATE_VERSION, "mode": "none", "spec": "", }


def load_state(paths, session_id):
    data, problem = read_json(state_path(paths, session_id))
    if isinstance(data, dict):
        state = State(data)
        state.base = json.loads(json.dumps(data))
        state.setdefault("mode", "none")
        if state["mode"] != "none" and (state["mode"] not in RUN_MODES or not os.path.isfile(paths.config)):
            # A mode of 0.5 (rewrite, closing, closed, concept, spec, init) is none, and so is the run mark in a
            # project that is not set up (docket 2026-11-10: a session in a repo without DOCKET.md was sealed on
            # another project's specs and swallowed eight of the user's messages). Every hook reads it so; the file
            # is left as it is until a hook changes the mode.
            state["mode"] = state.base["mode"] = "none"
        return state
    # No file, one that is not a state, or one that could not be opened: the default is this hook's guess, and only
    # what the hook changes on top of it is written. Another hook may write the real state before this one saves
    # (SessionStart and the first prompt's hook run at the same moment).
    state = State(default_state())
    state.base = default_state()
    state.unreadable = problem == "unreadable"
    return state


def save_state(paths, session_id, state):
    """Writes the fields this hook changed since it loaded the state into the file as it is now, under the session's
    lock: two hooks that change different fields both keep their change, and a hook that read an older state (or
    none) cannot write it over a newer one. A plain dict replaces the file."""
    target = state_path(paths, session_id)
    base = getattr(state, "base", None)
    with file_lock(target):
        if base is None:
            merged = dict(state)
        else:
            current, problem = read_json(target)
            if problem == "unreadable":
                return  # the file is there and closed to us: nothing we know is worth more than what it holds
            if isinstance(current, dict):
                merged = current
                for key in set(state) | set(base):
                    if key not in state:
                        merged.pop(key, None)
                    elif key not in base or state[key] != base[key]:
                        merged[key] = state[key]
            else:
                merged = dict(state)  # no file, or one that is no state: what this hook knows is all there is
        if write_json(target, merged) and isinstance(state, State):
            state.base = json.loads(json.dumps(dict(state)))
            state.unreadable = False
    prune_states(paths.state_dir)


def touch_state(paths, session_id):
    """The state file's time without its content: an agent's tool call shows that the session lives (session_live)
    and changes nothing in it."""
    try:
        os.utime(state_path(paths, session_id))
    except OSError:
        pass


KEPT_RECORDS = ("judge-suspended.json", "judge-removed.json")  # the way back for a judge entry taken out


def prune_states(directory, max_age_days=14):
    now = time.time()
    try:
        for name in os.listdir(directory):
            path = posixpath.join(directory, name)
            if name not in KEPT_RECORDS and now - os.path.getmtime(path) > max_age_days * 86400:
                os.remove(path)
    except OSError:
        pass


def prune_leftovers(directory, max_age_minutes=60):
    """Files of 0.5 that lost their writer and reader (display records, docket snapshots, rendered closing messages)
    and temporary files a
    killed hook left behind, once older than an hour: session_start runs it, so they go also where state is seldom
    saved."""
    now = time.time()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not (name.startswith(("display-", "snap-", "closing-")) or name.endswith(".tmp")):
            continue
        path = posixpath.join(directory, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > max_age_minutes * 60:
                os.remove(path)
        except OSError:
            pass


# --- LOG ---------------------------------------------------------------------

def log_event(paths, session_id, doc_id, event, note=""):
    """Append one line to LOG.md. Only hooks write here; the model never does."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    short = (session_id or "")[:8] or "-"
    line = f"{stamp}  {doc_id or '-':<11} {event:<18} session={short}"
    if note:
        line += f"  {note}"
    try:
        os.makedirs(paths.work, exist_ok=True)
        with open(paths.log, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    commit_work_dir(paths, f"{event} {doc_id or ''}".strip())


def log_lines(paths, doc_id=None):
    """LOG lines, oldest first, optionally only those of one document."""
    try:
        with open(paths.log, encoding="utf-8-sig", errors="ignore") as handle:
            lines = [line.rstrip("\n") for line in handle if line.strip()]
    except OSError:
        return []
    if doc_id:
        lines = [line for line in lines if f"  {doc_id} " in line or f"  {doc_id}\t" in line]
    return lines


def log_has(paths, doc_id, event):
    return any(f" {event} " in line + " " for line in log_lines(paths, doc_id))


def commit_work_dir(paths, message):
    """The work dir keeps its own history: every LOG line is a commit when the work dir is a git
    repository of its own (the home default always is; a work dir inside the project is not)."""
    work = paths.work
    try:
        if under(work, posixpath.join(home(), ".docket")) and not os.path.isdir(posixpath.join(work, ".git")):
            subprocess.run(["git", "-C", work, "init", "-q"], capture_output=True, timeout=15)
        if not os.path.isdir(posixpath.join(work, ".git")):
            return
        subprocess.run(["git", "-C", work, "add", "-A"], capture_output=True, timeout=15)
        subprocess.run(["git", "-C", work, "-c", "user.name=docket", "-c", "user.email=docket@localhost",
                        "commit", "-q", "-m", message], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


# --- a command the user typed -------------------------------------------------
MODE_COMMAND = (r"/(?:docket:(?P<full>concept|spec|init|implement)|(?P<bare>concept|spec|implement))\b"
                )  # /init is Claude Code's
TYPED_COMMAND = re.compile(MODE_COMMAND, re.IGNORECASE)


def command_line(text):
    """The line of a prompt that can be a command: its first line (typed), or its last (the pane and the brief of a
    session started by another put the command below a preamble). A line that starts with a slash anywhere else is
    text - a paste, a quoted transcript, a request that names a command (docket 2026-11-10: a pasted
    `/docket:implement` line sealed the session that was to find out why another one was sealed; luradb-web-client
    2026-11-10: the pane's request to start a session for a command sealed the session it asked)."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return next((line for line in lines[:1] + lines[-1:] if line.startswith("/")), "")


# --- a command the pane hands to a session of its own -----------------------------
# The app wraps the brief of a session it starts: a heading above it, the background, and below it lines of its own
# (luradb-web-client 2026-11-10: "Shared notes folder for this group of sessions (...)" followed the command, the
# command was neither the first nor the last line, the session stayed unsealed, called the command as a skill, was
# refused and ended on "type the command yourself"). The pane therefore records the command before it asks for the
# session; the session whose prompt carries exactly that line takes it, once. A line in a paste matches no record.
HANDOFF_SECONDS = 900


def handoff_path(paths):
    return posixpath.join(paths.state_dir, "handoff.json")


def handoff_records(paths):
    data, _ = read_json(handoff_path(paths))
    now = time.time()
    return [r for r in (data if isinstance(data, list) else [])
            if isinstance(r, dict) and isinstance(r.get("command"), str)
            and now - HANDOFF_SECONDS <= float(r.get("at") or 0) <= now + 60]


def handoff_line(command):
    """The command as one line, spaces folded: what the pane records and what a prompt's line is compared with."""
    return " ".join((command or "").strip().strip("`").split())


def save_handoff(paths, session_id, command):
    """Records a mode command the pane hands to another session. True when it was written."""
    line = handoff_line(command)
    if not TYPED_COMMAND.match(line):
        return False
    records = [r for r in handoff_records(paths) if r["command"] != line]
    records.append({"command": line, "from": session_id or "", "at": int(time.time())})
    return write_json(handoff_path(paths), records)


def take_handoff(paths, session_id, text):
    """The handed-over command a prompt carries on a line of its own, or "". Taken once, and never by the session
    that recorded it: its own request names the command inside a sentence."""
    records = handoff_records(paths)
    if not records:
        return ""
    lines = {handoff_line(line) for line in (text or "").splitlines()}
    for record in records:
        if record["command"] in lines and record.get("from") != (session_id or ""):
            write_json(handoff_path(paths), [r for r in records if r is not record])
            return record["command"]
    return ""


# --- transcript --------------------------------------------------------------
HARNESS_BLOCK = re.compile(r"<(system-reminder|task-notification|side-sessions-event|cross-session-message|"
                           r"agent-message)\b[^>]*>.*?</\1>", re.DOTALL)
# An agent's report handed back to the session arrives in the user role, framed by a line before the block and a
# paragraph after it (luradb-web-client 2026-11-10: 14 reports of the run's own agents were booked as messages of
# the user, answered on the screen and listed in the closing message as "your message ... not handled yet").
AGENT_MESSAGE = re.compile(r"<agent-message\b")
AGENT_FRAME = re.compile(r"^[ \t]*(?:Another Claude session sent a message:[ \t]*|"
                         r"That [\"“]other Claude session[\"”] is an agent working inside this same session\b.*)$",
                         re.MULTILINE)


def strip_harness_blocks(prompt):
    """The prompt without what the harness put into it: what is left is what the user typed."""
    text = HARNESS_BLOCK.sub("", prompt)
    if AGENT_MESSAGE.search(prompt):
        text = AGENT_FRAME.sub("", text)
    return text


def transcript_blocks(transcript_path):
    """(index, role, block) for every content block of the main line of a transcript, in order."""
    try:
        with open(transcript_path, encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
    except (OSError, TypeError):
        return []
    blocks = []
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") not in ("assistant", "user") or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    blocks.append((index, entry["type"], block))
    return blocks


def lock_holder(paths, spec_id):
    try:
        with open(paths.lock_file(spec_id), encoding="utf-8-sig") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def take_lock(paths, spec_id, session_id):
    try:
        os.makedirs(posixpath.dirname(paths.lock_file(spec_id)), exist_ok=True)
        with open(paths.lock_file(spec_id), "w", encoding="utf-8") as handle:
            handle.write(session_id or "")
    except OSError:
        pass


def claim_lock(paths, spec_id, session_id):
    """The lock, created exclusively; a lock of this session or of a session no longer running is taken over.
    Returns the other live holder, or "" when this session holds it now (review 2026-11-06: two sessions could seal
    one spec while the first ran its preconditions)."""
    path = paths.lock_file(spec_id)
    try:
        os.makedirs(posixpath.dirname(path), exist_ok=True)
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, (session_id or "").encode("utf-8"))
        os.close(handle)
        return ""
    except FileExistsError:
        holder = lock_holder(paths, spec_id)
        if holder and holder != session_id and session_live(paths, holder):
            other = load_state(paths, holder)
            if other.get("spec") == spec_id or spec_id in (other.get("queue") or []):
                return holder
        take_lock(paths, spec_id, session_id)
        return ""
    except OSError:
        return ""


def release_lock(paths, spec_id):
    try:
        os.remove(paths.lock_file(spec_id))
    except OSError:
        pass


# --- transcript entries ------------------------------------------------------
TAIL_BYTES = 262144


def transcript_entries(transcript_path, tail=False):
    try:
        with open(transcript_path, "rb") as handle:
            if tail:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - TAIL_BYTES))
            raw = handle.read().decode("utf-8", errors="ignore")
    except (OSError, TypeError, ValueError):
        return []
    entries = []
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # also the first, cut line of a tail
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def point_counts(body):
    """Boxes of a docket: delivered [x], deviations [~], open [ ] (backticked ones ignored); only those of the
    points section when the docket has one."""
    match = re.search(r"^##\s+Points\b.*?$(.*?)(?=^##\s|\Z)", body or "", re.MULTILINE | re.DOTALL | re.IGNORECASE)
    body = match.group(1) if match else (body or "")
    return {mark: len(re.findall(r"(?<!`)\[" + re.escape(mark) + r"\](?!`)", body)) for mark in ("x", "~", " ")}


def refuse(message):
    """A refusal: exit 2 with the message on stderr, nothing counted, nothing added."""
    print(message, file=sys.stderr)
    return 2


def notice(message):
    """A hook's word to the model (and, where the client shows it, to the user) that is no refusal: exit 2 with the
    message on stderr, nothing counted."""
    print(message, file=sys.stderr)
    return 2


def write_atomically(path, text, bom=False):
    temporary = f"{path}.docket-{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def read_text(path):
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as handle:
            return handle.read()
    except (OSError, TypeError):
        return ""


def section_text(body, title):
    """The text of every `## <title>…` section of a markdown body, joined (a docket may carry a section twice when
    lines were added below an older copy)."""
    pattern = r"^##\s+" + re.escape(title) + r"\b.*?$(.*?)(?=^##\s|\Z)"
    return "\n".join(m.group(1) for m in re.finditer(pattern, body or "", re.MULTILINE | re.DOTALL | re.IGNORECASE))


def bullets(block):
    """The filled bullet lines of a section: placeholders (`- `) and prose without a bullet do not count."""
    return [line.strip()[1:].strip() for line in (block or "").splitlines()
            if re.match(r"\s*[-*]\s+\S", line)]


# --- end of a spec: the docket's Terminal state section (V3) ---------------------
# The orchestrator ends a spec by writing lines into its docket, not by a message: `state:` DELIVERED / BLOCKED /
# DISSENT, `result:` one sentence, `open:` and `user:` and `decision:` as many as there are, `dissent:` for DISSENT.
TERMINAL_LINE = re.compile(r"^[`*]*(state|result|open|watch|user|decision|repaired|dissent)[`*]*\s*:[`*]*\s*(.*?)\s*$",
                           re.IGNORECASE)
TERMINAL_STATE = re.compile(r"^[`*]*(DELIVERED|BLOCKED|DISSENT)[`*]*(?!\w)[\s.:;,\-–—(]*(.*?)[\s.)]*$", re.IGNORECASE)
# "none", "keine Schritte nötig", "nothing to do": a line that says there is nothing (experiment 12: "Du musst: keine
# Schritte nötig" stood twice in the closing message).
NONE_VALUE = re.compile(r"^[\s`*]*(?:-|–|—|n/a|(?:none|nothing|no|keine?|kein|nichts)\b[^.;:]{0,30})[\s`*.]*$", re.IGNORECASE)


def terminal_blocks(paths, spec_id):
    """Every block of the docket's Terminal state section, in order: a block starts at a valid `state:` line. A line
    may carry a bullet, bold or backticks around the key (`- **state:** DELIVERED`, `` `state: DELIVERED` ``)."""
    docket = paths.docket_file(spec_id)
    section = section_text(read_text(docket), "Terminal state") if docket else ""
    blocks = []
    for raw in section.splitlines():
        line = re.sub(r"^\s*[-*]\s+", "", raw.strip())
        if line.count("`") == 2 and line.startswith("`") and line.endswith("`"):
            line = line[1:-1]
        match = TERMINAL_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == "state":
            state = TERMINAL_STATE.match(value)
            if state:
                blocks.append({"kind": state.group(1).lower(), "reason": (state.group(2) or "").strip().strip("`"),
                               "result": "", "open": [], "watch": [], "user": [], "decision": [],
                               "repaired": [], "dissent": []})
            continue
        if not blocks or not value:
            continue
        if key == "result":
            blocks[-1]["result"] = value  # "Nothing was built." is a result
        elif not NONE_VALUE.match(value):
            blocks[-1][key].append(value)
    return blocks


def terminal_section(paths, spec_id, skip=0):
    """The last block counts, from its `state:` line on: the docket is append-only, so a wrong block is corrected by
    writing it again below. `skip`: blocks an earlier run left (review 2026-11-06: a resumed spec was closed at once
    from its old BLOCKED block)."""
    blocks = terminal_blocks(paths, spec_id)[skip:]
    if blocks:
        return blocks[-1]
    return {"kind": "", "reason": "", "result": "", "open": [], "watch": [], "user": [], "decision": [],
            "repaired": [], "dissent": []}


def terminal_base(paths, spec_id):
    return len(terminal_blocks(paths, spec_id))


REVIEW_ANGLE = re.compile(r"^[`*]*(code|spec|recheck)\b", re.IGNORECASE)
NO_FINDINGS = re.compile(r"no findings|keine befunde", re.IGNORECASE)


def review_cells(line):
    return [cell.strip().strip("`*").strip().lower() for cell in line.split("|")]


def review_lines(body):
    """The Review lines that count: of two lines with the same angle and finding the later one. The docket is
    append-only, so a line is corrected by writing it again below."""
    latest = {}
    for line in bullets(section_text(body, "Review")):
        cells = review_cells(line)
        angle = REVIEW_ANGLE.match(line.strip())
        key = (angle.group(1).lower(), cells[1]) if angle and len(cells) > 1 else line
        latest.pop(key, None)
        latest[key] = line
    return list(latest.values())


# --- the acceptance commands of the delivered points (V16) ---------------------
WRITING_AGENTS = ("implementer", "fixer")  # every change to the project goes to one of them
ARROW = re.compile(r"\s*→\s*|\s+->\s+")
STILL_RUNNING = 10 ** 9


def sealed_stamp(paths, spec_id):
    """The LOG stamp of the spec's last sealing: what the transcript holds before it belongs to an earlier spec."""
    return max((line[:20] for line in log_lines(paths, spec_id) if " sealed " in line), default="")


def failed_calls(entries):
    return {block.get("tool_use_id") for entry in entries if entry.get("type") == "user"
            for block in ((entry.get("message") or {}).get("content") or [])
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error")}


def tool_calls(entries, names):
    """(entry index, block) for the main line's calls of the named tools, in order."""
    calls = []
    for index, entry in enumerate(entries):
        if entry.get("isSidechain") or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        for block in (message.get("content") if isinstance(message, dict) else None) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in names:
                calls.append((index, block, str(entry.get("timestamp", ""))))
    return calls


def agent_runs(transcript_path, since=""):
    """The main line's agents since the sealing, in order: (kind, launch index, return index, model). A spawn a hook
    refused or that failed is none; an agent that has not returned yet carries STILL_RUNNING."""
    entries = transcript_entries(transcript_path)
    returns, failed = agent_returns(transcript_path), failed_calls(entries)
    runs = []
    for index, block, stamp in tool_calls(entries, ("Agent", "Task")):
        if block.get("id") in failed or (since and stamp[:19] < since[:19]):
            continue
        given = block.get("input") or {}
        kind = str(given.get("subagent_type") or "general").split(":")[-1].lower()
        runs.append((kind, index, returns.get(block.get("id"), STILL_RUNNING), str(given.get("model") or "")))
    return runs


def last_written(runs):
    """The return index of the last agent that changed the project, -1 when none ran."""
    return max((back for kind, _, back, _ in runs if any(name in kind for name in WRITING_AGENTS)), default=-1)


def point_commands(body, paths):
    """(point, command) for every delivered point `[x]` whose pre-registered acceptance line is
    `<command> → <expected>`: only what stands in backticks before the arrow is a command (REWORK-SPEC 1d.2); a
    criterion in prose, and a file with what it contains, the orchestrator checks itself."""
    match = re.search(r"^##\s+Points\b.*?$(.*?)(?=^##\s|\Z)", body or "", re.MULTILINE | re.DOTALL | re.IGNORECASE)
    found = []
    for line in (match.group(1) if match else (body or "")).splitlines():
        line = line.strip()
        if not line.startswith("|") or not re.search(r"(?<!`)\[x\](?!`)", line):
            continue
        cells = re.split(r"(?<!\\)\|", line.strip("|"))
        if len(cells) < 4:
            continue
        parts = ARROW.split("|".join(cells[2:-1]).replace("\\|", "|").strip(), 1)
        before = parts[0].strip()
        command = before.strip("`").strip()
        if (len(parts) < 2 or not before.startswith("`") or "`" in command or not command or command.startswith("<")
                or re.match(r"[`\s]*(contains|enthält)\b", parts[1], re.IGNORECASE)
                or os.path.exists(posixpath.join(paths.root, command))):
            continue
        found.append((cells[0].strip(), command))
    return found


def acceptance_problems(paths, spec_id, body, transcript_path):
    """V16: a delivered point's own command ran in this session after the last implementer or fixer returned, and
    did not fail in its last run. The project's check says the whole still holds, this says the point does."""
    entries = transcript_entries(transcript_path)
    if not entries:
        return []
    failed = failed_calls(entries)
    shell = [(index, str((block.get("input") or {}).get("command", "")), block.get("id") in failed)
             for index, block, _ in tool_calls(entries, ("Bash", "PowerShell"))]
    written = last_written(agent_runs(transcript_path, sealed_stamp(paths, spec_id)))
    problems = []
    for point, command in point_commands(body, paths):
        runs = [(index, broke) for index, text, broke in shell if command in text]
        if not runs:
            problems.append(say(paths, "gate_point_missing", point=point, command=command))
        elif runs[-1][0] < written:
            problems.append(say(paths, "gate_point_before", point=point, command=command))
        elif runs[-1][1]:
            problems.append(say(paths, "gate_point_failed", point=point, command=command))
    return problems


def review_stats(body):
    """The findings of the docket's Review section and how many of them were fixed (metrics)."""
    findings = [line for line in review_lines(body) if not NO_FINDINGS.search(line)]
    return {"review": len(findings),
            "fixed": len([line for line in findings if re.search(r"\bfixed\b|behoben", line, re.IGNORECASE)])}


# --- waiting for agents (V4) ---------------------------------------------------
LAUNCHED = re.compile(r"(?:Async agent launched successfully|Workflow launched in background)", re.IGNORECASE)
NOTIFIED = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")
PENDING_MAX_HOURS = 3


def iso_age_seconds(stamp):
    try:
        return time.time() - calendar.timegm(time.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return 0


def pending_agents(transcript_path, queued_only=False):
    """Background agents and workflows launched in the main line whose notification the model has not seen yet.
    A notification arrives either as a user entry (between turns) or as a queued_command attachment (mid-turn,
    seen by the model with its next assistant entry); a queued one the model has not answered yet still counts as
    waiting, the turn resumes with it (luradb-web-client 2026-11-06: three waits were refused because
    background_tasks was already empty). `queued_only`: only those, for a client that reports background_tasks."""
    calls, launched, delivered, queued = {}, {}, set(), set()
    for entry in transcript_entries(transcript_path):
        if entry.get("isSidechain"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if entry.get("type") == "attachment":
            attachment = entry.get("attachment") or {}
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                prompt = attachment.get("prompt")
                queued.update(NOTIFIED.findall(prompt if isinstance(prompt, str) else json.dumps(prompt)))
            continue
        if entry.get("type") == "assistant":
            delivered |= queued  # the model has seen what was queued before this entry
            queued = set()
        if entry.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task", "Workflow"):
                    calls[block.get("id")] = entry.get("timestamp", "")
        elif entry.get("type") == "user":
            if isinstance(content, str):
                delivered.update(NOTIFIED.findall(content))
                continue
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    delivered.update(NOTIFIED.findall(block.get("text", "")))
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    inner = block.get("content")
                    inner = inner if isinstance(inner, str) else " ".join(
                        b.get("text", "") for b in inner or [] if isinstance(b, dict))
                    if LAUNCHED.match((inner or "").lstrip()):
                        launched[block.get("tool_use_id")] = calls[block.get("tool_use_id")]
    if queued_only:
        return {tool_id for tool_id in queued if tool_id in launched}
    return {tool_id for tool_id, stamp in launched.items()
            if tool_id not in delivered and iso_age_seconds(stamp) < PENDING_MAX_HOURS * 3600}


AGENT_TASKS = ("subagent", "workflow", "agent")


def agent_returns(transcript_path):
    """{tool_use_id: line index of its return} for the main line's agents: the tool result of a foreground agent, the
    notification of a background one (its tool result only says it was launched)."""
    returns, launched = {}, set()
    for index, entry in enumerate(transcript_entries(transcript_path)):
        if entry.get("isSidechain"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if entry.get("type") == "assistant":
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task"):
                    returns.setdefault(block.get("id"), -1)
            continue
        texts = []
        if entry.get("type") == "attachment" and isinstance(entry.get("attachment"), dict):
            prompt = entry["attachment"].get("prompt")
            texts.append(prompt if isinstance(prompt, str) else "")
        elif entry.get("type") == "user":
            if isinstance(content, str):
                texts.append(content)
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in returns:
                    inner = block.get("content")
                    inner = inner if isinstance(inner, str) else " ".join(
                        b.get("text", "") for b in inner or [] if isinstance(b, dict))
                    if LAUNCHED.match((inner or "").lstrip()):
                        launched.add(block.get("tool_use_id"))
                    else:
                        returns[block.get("tool_use_id")] = index
        for text in texts:
            for tool_id in NOTIFIED.findall(text):
                if tool_id in launched:
                    returns[tool_id] = index
    return {k: v for k, v in returns.items() if v >= 0}


def waiting(hook_input, transcript_path, terminal_written):
    """A Stop that waits for work in the background: no check, no text needed (V4). Claude Code reports that work in
    `background_tasks`; once the spec's Terminal state is written, only agents count, not a dev server or a monitor
    left running (review 2026-11-06: a background shell turned the spec end into an endless wait). A notification
    queued but not yet seen by the model also waits. A client without `background_tasks` falls back to the
    transcript."""
    if "background_tasks" not in hook_input:
        return bool(pending_agents(transcript_path))
    tasks = [t for t in hook_input.get("background_tasks") or [] if t]
    if terminal_written:
        tasks = [t for t in tasks if not isinstance(t, dict) or str(t.get("type", "")).lower() in AGENT_TASKS]
    return bool(tasks) or bool(pending_agents(transcript_path, queued_only=True))


# --- live sessions (V13) -------------------------------------------------------
LIVE_MINUTES = 60  # without a tool call and without a recorded wait


def session_live(paths, session_id):
    """A session counts as running while its state carries the run mark and was touched within the hour: every tool
    call of the session and of its agents renews the file's time (post_tool_use). The LOG names a session by its first
    eight characters; such a prefix is matched against the state files."""
    if session_id and not os.path.isfile(state_path(paths, session_id)):
        session_id = next((s for s in other_sessions(paths, "") if s.startswith(session_id)), "")
    if not session_id:
        return False
    state = load_state(paths, session_id)
    if state.get("mode") not in RUN_MODES:
        return False
    if time.time() - float(state.get("waiting") or 0) < PENDING_MAX_HOURS * 3600:
        return True  # it ended a turn to wait for its agents (stop.py records the moment)
    try:
        return time.time() - os.path.getmtime(state_path(paths, session_id)) < LIVE_MINUTES * 60
    except OSError:
        return False


def other_sessions(paths, session_id):
    try:
        names = os.listdir(paths.state_dir)
    except OSError:
        return []
    found = []
    for name in names:
        match = re.fullmatch(r"state-([A-Za-z0-9_-]+)\.json", name)
        if match and match.group(1) != session_id:
            found.append(match.group(1))
    return found


def queued_elsewhere(paths, spec_id, session_id):
    """The live session whose series still has this spec ahead of it, or ""."""
    for other in other_sessions(paths, session_id):
        state = load_state(paths, other)
        if spec_id in (state.get("queue") or []) and session_live(paths, other):
            return other
    return ""


# --- the judge entry of 0.5 in a project's settings (REWORK-SPEC 1c.6) ----------
JUDGE_MARK = "You judge the final message"


def judge_record_path(paths):
    return posixpath.join(paths.state_dir, "judge-suspended.json")


def settings_files(paths):
    return [posixpath.join(paths.root, ".claude", name) for name in ("settings.json", "settings.local.json")]


def is_judge(hook):
    return isinstance(hook, dict) and hook.get("type") == "prompt" and JUDGE_MARK in str(hook.get("prompt", ""))


def load_settings(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, ""
    try:
        settings = json.loads(raw.lstrip("﻿")) if raw.strip() else {}
    except ValueError:
        return None, raw
    return (settings if isinstance(settings, dict) else None), raw


def save_settings(path, settings, raw):
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    write_atomically(path, text.replace("\n", "\r\n") if "\r\n" in raw else text, bom=raw.startswith("﻿"))


def load_judge_record(paths):
    try:
        with open(judge_record_path(paths), encoding="utf-8-sig") as handle:
            record = json.load(handle)
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def _save_json(path, data):
    try:
        os.makedirs(posixpath.dirname(path), exist_ok=True)
        write_atomically(path, json.dumps(data, indent=1, ensure_ascii=False))
        return True
    except OSError:
        return False


# --- git ------------------------------------------------------------------------

def git_out(root, *args):
    try:
        result = subprocess.run(["git", "-C", root] + list(args), capture_output=True, encoding="utf-8",
                                errors="replace", timeout=10)
        return result.returncode, (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def relative_to_root(paths, path):
    if under(path, paths.root):
        return norm(path)[len(paths.root):].lstrip("/") or "."
    homedir = home()
    return "~" + norm(path)[len(homedir):] if under(path, homedir) else norm(path)


def repo_facts(paths):
    """Branch, upstream, commits not pushed and whether the working tree is clean, from git; {} outside a repo."""
    code, top = git_out(paths.root, "rev-parse", "--show-toplevel")
    if code != 0 or not top:
        return {}
    code, branch = git_out(paths.root, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or not branch:
        return {}
    code, upstream = git_out(paths.root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    ahead = None
    if code == 0 and upstream:
        code, out = git_out(paths.root, "rev-list", "--count", "@{u}..HEAD")
        ahead = int(out) if code == 0 and out.isdigit() else None
    else:
        upstream = ""
    code, status = git_out(paths.root, "status", "--porcelain", *project_pathspec(paths))
    return {"branch": branch, "upstream": upstream, "ahead": ahead, "dirty": code == 0 and bool(status)}


def project_pathspec(paths):
    """The project without docket's own folders (the work dir and the state dir, where they lie inside it)."""
    spec = ["--", "."]
    for own in (paths.work, paths.state_dir):
        if under(own, paths.root):
            spec.append(":(exclude)" + relative_to_root(paths, own))
    return spec


def project_head(paths):
    code, out = git_out(paths.root, "rev-parse", "HEAD")
    return out if code == 0 else ""


def untouched_since(paths, head):
    """Whether the project is as it was at commit `head`: nothing committed since and nothing uncommitted, outside
    docket's own folders. What cannot be told (no git, no head) counts as touched."""
    if not head:
        return False
    spec = project_pathspec(paths)
    if git_out(paths.root, "diff", "--quiet", head, "HEAD", *spec)[0] != 0:
        return False
    code, status = git_out(paths.root, "status", "--porcelain", *spec)
    return code == 0 and not status


def work_dir_dirty(paths):
    code, status = git_out(paths.work, "status", "--porcelain", "--", ".")
    return code == 0 and bool(status)


def records_commands(paths):
    """The two commands that commit docket's records where the work dir lies inside a repository that is not its
    own (the project's, or a concept repository beside it), as the user types them at the project root; None where
    the work dir keeps its own history or is in no repository."""
    if os.path.isdir(posixpath.join(paths.work, ".git")):
        return None
    code, top = git_out(paths.work, "rev-parse", "--show-toplevel")
    prefix_code, prefix = git_out(paths.work, "rev-parse", "--show-prefix")
    if code != 0 or not top or prefix_code != 0:
        return None
    folder = prefix.strip().rstrip("/") or "."
    where = relative_to_root(paths, norm(top).rstrip("/"))
    git = "git" if where == "." else f"git -C {where}"
    return f"{git} add {folder}", f'{git} commit -m "docket: records" -- {folder}'


def commit_records(paths):
    """With `commit_records` set in DOCKET.md the hook commits the records itself at the end of a run, in the
    repository they lie in and only their folder; it never pushes."""
    if config_values(paths).get("commit_records", "").lower() not in ("yes", "true", "ja", "on"):
        return
    commands = records_commands(paths)
    if not commands or not work_dir_dirty(paths):
        return
    folder = commands[0].rsplit(" ", 1)[-1]
    code, top = git_out(paths.work, "rev-parse", "--show-toplevel")
    if code != 0 or not top:
        return
    try:
        subprocess.run(["git", "-C", top, "add", "--", folder], capture_output=True, timeout=15)
        subprocess.run(["git", "-C", top, "commit", "-q", "-m", "docket: records", "--", folder],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


# --- metrics (V6) ------------------------------------------------------------------

def write_metrics(paths, spec_id, transcript_path, kind):
    """metrics/<spec>.json, written by the hook when the spec ends: points, review counts, agents by kind since
    sealing, implementer and reviewer models; no refusal, refinement or judge counts (REWORK-SPEC 1d.1)."""
    body = read_text(paths.docket_file(spec_id))
    sealed = sealed_stamp(paths, spec_id)
    agents, models, reviewers = Counter(), [], []
    for kind_of, _, _, model in agent_runs(transcript_path, sealed):
        agents[kind_of] += 1
        if kind_of == "implementer":
            models.append(model or "inherit")
        elif kind_of == "reviewer":
            reviewers.append(model or "inherit")
    stats = review_stats(body)
    counts = point_counts(body) if body else {"x": 0, "~": 0, " ": 0}
    data = {"spec": spec_id, "title": spec_title(paths, spec_id), "outcome": kind, "sealed": sealed,
            "closed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "points": {"delivered": counts["x"], "deviations": counts["~"], "open": counts[" "]},
            "review": {"findings": stats["review"], "fixed": stats["fixed"]},
            "agents": dict(agents), "implementer_models": models, "reviewer_models": reviewers}
    path = posixpath.join(paths.metrics, f"{spec_id}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            old = json.load(handle)
        if isinstance(old, dict):
            old.update(data)
            data = {key: value for key, value in old.items() if key not in ("refinement", "inbox_notes")}
    except (OSError, ValueError):
        pass
    _save_json(path, data)


# --- the run mark ------------------------------------------------------------------
# One function with two triggers (REWORK-SPEC 1c.1): the typed `/docket:implement` (user_prompt.py) and the model's
# Skill call `docket:implement` (pre_tool_use.py). It runs no precondition: the run runs them as its first step per
# spec, `/docket:spec` in the ready check.
def run_specs(paths, rest):
    """(the specs of an implement call in run order, "") or ([], why none).

    `/docket:implement`            the next ready spec whose predecessors (`follows:`, `after:`) are done
    `/docket:implement c-xxxxxxxx` every ready spec of that concept, in implementation order
    `/docket:implement s-a, s-b`   exactly these, in the given order
    `/docket:implement --all`      every ready spec of the project, in implementation order
    """
    rest = (rest or "").strip()
    ids = list(dict.fromkeys(s.lower() for s in SPEC_ID.findall(rest)))
    concept = CONCEPT_ID.search(rest)
    if ids:
        # A named spec is a spec of this project (docket 2026-11-10: ids pasted from another project sealed a session).
        unknown = [s for s in ids if not paths.spec_file(s)]
        if unknown:
            return [], say(paths, "run_unknown", ids=", ".join(unknown))
        chosen = ids
    elif concept:
        chosen = [s for s, _ in ready_specs(paths, concept.group(0))]
        if not chosen:
            return [], say(paths, "run_no_ready_concept", concept=concept.group(0))
    elif re.search(r"(^|\s)--all\b", rest):
        chosen = [s for s, _ in ready_specs(paths)]
        if not chosen:
            return [], say(paths, "run_no_ready")
    elif not rest:
        chosen = [s for s, _ in ready_specs(paths) if not open_predecessors(paths, [s])][:1]
        if not chosen:
            return [], say(paths, "run_no_runnable")
    else:
        return [], say(paths, "run_forms")
    missing = open_predecessors(paths, chosen)
    if missing:
        pairs = "; ".join(say(paths, "run_order_pair", spec=s, predecessor=p) for s, p in missing)
        return [], say(paths, "run_order", pairs=pairs)
    return chosen, ""


def held_by_live_session(paths, holder, spec_id):
    """A lock counts only while its session runs on that spec or has it in its queue (session_live); the lock of a
    crashed or finished session is taken over."""
    other = load_state(paths, holder)
    return session_live(paths, holder) and (other.get("spec") == spec_id or spec_id in (other.get("queue") or []))


def points_summary(paths, spec_id):
    docket = paths.docket_file(spec_id)
    if not docket:
        return say(paths, "no_docket_yet")
    try:
        with open(docket, encoding="utf-8-sig", errors="ignore") as handle:
            counts = point_counts(handle.read())
    except OSError:
        return say(paths, "docket_unreadable")
    return say(paths, "points_summary", x=counts["x"], d=counts["~"], o=counts[" "])


def run_line(paths, state, mark="line"):
    """The state line of a marked run: which spec of the series runs, where its docket lies, its points, the rest.
    `mark`: `state` ([docket state]) where the Stop hook goes on with the next spec of the series."""
    spec_id = state.get("spec", "")
    i, n = position(state)
    line = marked(paths, mark, say(paths, "run_line", i=i, n=n, title=spec_title(paths, spec_id) or spec_id,
                                   spec=spec_id, docket=paths.docket_file(spec_id) or paths.dockets,
                                   points=points_summary(paths, spec_id)))
    if state.get("queue"):
        line += say(paths, "run_after", queue=", ".join(state["queue"]))
    return line


def work_dir_line(paths):
    """Where this project's docket files are: the work dir, DOCKET.md and the language of the messages."""
    return marked(paths, "line", say(paths, "work_dir", work=paths.work, config=paths.config,
                                     language=config_values(paths).get("user_language", "") or "-"))


def mark_run(paths, session_id, rest, state=None):
    """Marks the run of an implement call. (marked, state line): marked when this call set the run mark; the line is
    the run line, or "no run marked" with the reason.

    A session already marked on the same first spec stays as it is (no LOG line). Nothing is marked for an unknown
    spec, no ready spec, a predecessor not done, a spec another live session holds or has queued, or another spec at
    work in this session; no tool is held afterwards. Otherwise: the lock of the first spec, the run mark and the
    series in the state, LOG `sealed` and `queued`, the spec's state in-progress."""
    state = state if state is not None else load_state(paths, session_id)
    no_run = lambda reason: (False, marked(paths, "line", say(paths, "no_run", reason=reason)))
    found, reason = run_specs(paths, rest)
    if reason:
        return no_run(reason)
    spec_id, queue = found[0], found[1:]
    title = lambda spec: spec_title(paths, spec) or spec
    if state.get("mode") in RUN_MODES and state.get("spec") == spec_id:
        return False, run_line(paths, state)
    if state.get("mode") in RUN_MODES and state.get("spec") and state.get("spec") != spec_id:
        return no_run(say(paths, "run_other", title=title(state["spec"])))
    for candidate in found:
        holder = lock_holder(paths, candidate)
        if holder and holder != session_id and held_by_live_session(paths, holder, candidate):
            return no_run(say(paths, "run_held", title=title(candidate), session=holder[:8]))
        other = queued_elsewhere(paths, candidate, session_id)
        if other:
            return no_run(say(paths, "run_queued", title=title(candidate), session=other[:8]))
    # Only the spec at work is locked, exclusively; the ones waiting are named in this session's queue, which
    # other sessions respect while this one runs (V13: a lock on a spec never started outlived a crashed series).
    holder = claim_lock(paths, spec_id, session_id)
    if holder:
        return no_run(say(paths, "run_held", title=title(spec_id), session=holder[:8]))
    # sealed_head: the project's commit when the spec was marked; a spec that ends BLOCKED with the project still
    # there lets the series go on (stop.end_spec).
    state.update({"mode": "sealed", "spec": spec_id, "stop_requested": False, "queue": queue, "series": found,
                  "outcomes": {}, "skipped": {}, "refused": False, "halted": False,
                  "terminal_base": terminal_base(paths, spec_id), "sealed_head": project_head(paths)})
    save_state(paths, session_id, state)
    log_event(paths, session_id, spec_id, "sealed")
    if queue:
        log_event(paths, session_id, spec_id, "queued", ", ".join(queue))
    set_spec_state(paths, spec_id, "in-progress")
    return True, run_line(paths, state)


# --- series in the LOG (V13) --------------------------------------------------------

def logged_series(paths):
    """Every series the LOG knows (a `sealed` line and its `queued` line): the ids in order, the session, and each
    spec's state since the series began: delivered, blocked, dissent, interrupted or not_started."""
    lines = log_lines(paths)
    found = []
    for index, line in enumerate(lines):
        parts = line.split()
        if len(parts) < 4 or parts[2] != "queued":
            continue
        session = (re.search(r"session=(\S+)", line) or [None, ""])[1]
        ids = [parts[1]] + SPEC_ID.findall(line.split("session=", 1)[-1])
        status = {}
        for later in lines[index + 1:]:
            bits = later.split()
            if len(bits) > 2 and bits[1] in ids:
                if bits[2] == "sealed":
                    status[bits[1]] = "interrupted"
                elif bits[2] in ("delivered", "blocked", "dissent"):
                    status[bits[1]] = bits[2]
        status.setdefault(ids[0], "interrupted")
        found.append({"ids": ids, "session": session, "status": {i: status.get(i, "not_started") for i in ids}})
    return found


def series_line(paths, series, live=False):
    """One line for a series: each spec by title with its state. A live series runs; another one with a spec sealed
    and never ended was interrupted, and the line names the command that resumes it."""
    status = {i: ("running" if live and s == "interrupted" else s) for i, s in series["status"].items()}
    items = ", ".join(f"{quoted(paths, spec_title(paths, i) or i)} {say(paths, status[i])}" for i in series["ids"])
    rest = [i for i in series["ids"] if status[i] in ("interrupted", "not_started")]
    if rest and "interrupted" in status.values():
        return marked(paths, "line", say(paths, "series_interrupted", items=items, specs=", ".join(rest)))
    return marked(paths, "line", say(paths, "series_running", items=items))
