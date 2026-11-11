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
import shutil
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
# closing: the run is over, the hook has rendered the closing message and waits for it verbatim.
MODES = ("none", "concept", "spec", "init", "sealed", "rewrite", "closing", "closed")
RUN_MODES = ("sealed", "rewrite", "closing")
STATE_VERSION = 1
PLUGIN_ROOT = posixpath.dirname(posixpath.dirname(os.path.abspath(__file__).replace("\\", "/")))


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
        self.inbox = posixpath.join(self.work, "inbox")
        self.log = posixpath.join(self.work, "LOG.md")
        self.config = posixpath.join(self.work, "DOCKET.md")
        self.metrics = posixpath.join(self.work, "metrics")
        self.state_dir = posixpath.join(self.root, ".claude", "docket")

    def required_reading(self):
        files = [self.config]
        for name in ("CONTRIBUTING.md", "AGENTS.md"):
            candidate = posixpath.join(self.root, name)
            if os.path.isfile(candidate):
                files.append(candidate)
        return files

    def spec_file(self, spec_id):
        return find_document(self.specs, spec_id)

    def docket_file(self, spec_id):
        return find_document(self.dockets, spec_id)

    def lock_file(self, spec_id):
        return posixpath.join(self.work, "locks", spec_id)

    def violations_file(self, doc_id):
        return posixpath.join(self.metrics, f"{doc_id or 'session'}.violations.json")


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
    The hooks' own lines to the user (status line, closing line) follow it; the model is told the name."""
    name = config_values(paths).get("user_language", "").lower()
    if not name:
        return ""
    return "de" if name.startswith(("de", "ger")) else "en"


# What the hooks say to the user, and to the model where the user may read along, in DOCKET.md `user_language`.
# luradb-web-client 2026-11-06 (series of four): every spec ended in a receipt of up to 2,500 characters and a
# hook line "run finished" in the middle of the series; the user asked whether it was done. Now a spec ends in its
# docket, and the run ends in one message the hook renders from files and git (render_closing).
SAY = {
    "de": {
        "position": "Spec {i} von {n} · ",
        "preparing": "wird vorab aus drei Blickwinkeln geprüft",
        "rewrite": "die Spec wird nach der Prüfung korrigiert",
        "points": "{x} von {n} Punkten geliefert",
        "deviating": ", {d} abweichend",
        "closing": "der Lauf ist zu Ende, die Abschlussmeldung folgt",
        "handover": ("Spec {i} von {n} ist versiegelt: {title} ({spec}). Beginne sie bei Schritt 0 des Implement-Befehls; "
                     "aus der vorigen Spec ist nichts ein Fakt, was du nicht neu liest. Bis zum Ende des Laufs schreibst "
                     "du keinen Text."),
        "handover_known": (" Dem Nutzer in diesem Lauf schon vorgeschlagen; schreibe es nicht noch einmal als "
                           "`decision:`: {items}"),
        "closing_order": ("Der Lauf ist zu Ende. Lies {path} mit Read und sende den Inhalt als deine einzige Nachricht: "
                          "wörtlich, ohne Zusatz, ohne Codeblock."),
        "closing_again": ("Die Nachricht stimmt nicht mit {path} überein ({problem}) und wurde nicht angezeigt. Sende den "
                          "Inhalt der Datei wörtlich, ohne Zusatz, ohne Codeblock."),
        "not_finished": ("Der Lauf ist nicht fertig: {problem}. Arbeite weiter. Eine Spec endet nur mit ihrem Abschnitt "
                         "„Terminal state“ im Docket, danach endet der Turn ohne Text; ein Turn, in dem Agenten laufen, "
                         "endet ebenfalls ohne Text. `state: BLOCKED` steht nur für das, was der Nutzer am Projekt klären "
                         "muss, nie für einen Mangel in den Zeilen des Dockets: eine Zeile korrigierst du, indem du sie "
                         "unten neu schreibst."),
        "no_terminal": "kein Agent läuft, und der Abschnitt „Terminal state“ des Dockets hat keine Zeile `state:`",
        "docket_message": ("eine Nachricht `DOCKET …` gibt es nicht mehr: das Ende einer Spec steht in ihrem Docket, die "
                           "eine Nachricht des Laufs rendert der Hook am Ende"),
        "halted": ("{title}: Der Lauf hat angehalten, ohne fertig zu sein. `/docket:implement {specs}` setzt ihn fort, "
                   "jede Nachricht von dir auch."),
        "sealed_elsewhere": "„{title}“ läuft in einer anderen Sitzung. Dort beenden oder mit `/docket:stop` anhalten.",
        "queued_in": "„{title}“ wartet in der Reihe einer anderen Sitzung. Die Reihe dort erreicht sie, oder `/docket:stop` dort.",
        "sealed_on": "Diese Sitzung arbeitet an „{title}“. Erst beenden oder `/docket:stop`.",
        "forbidden_precondition": ("Die Vorbedingung `{command}` von „{title}“ steht unter forbidden_commands in "
                                   "DOCKET.md; prüfe sie selbst und nimm sie aus der Spec."),
        "precondition": ("Vorbedingung von „{title}“ nicht erfüllt: `{command}` sollte {expected} liefern ({got}). "
                         "Erst das erledigen, dann den Lauf starten."),
        "head_one": "Lauf beendet: {title} ist umgesetzt",
        "head_done": "Lauf beendet: {d} von {n} Specs umgesetzt",
        # "blockiert" liest ein Mensch als "nichts geht mehr": die Meldung sagt, was der Stand ist und wen er braucht.
        "head_blocked": "Lauf angehalten: „{title}“ braucht dich, {d} von {n} Specs umgesetzt",
        "head_dissent": "Lauf angehalten: bei „{title}“ gibt es Dissens, {d} von {n} Specs umgesetzt",
        "head_stopped": "Lauf auf deinen Wunsch beendet: {d} von {n} Specs umgesetzt",
        "head_past_blocked": "Lauf beendet: {d} von {n} Specs umgesetzt, „{title}“ braucht dich",
        "head_past_dissent": "Lauf beendet: {d} von {n} Specs umgesetzt, bei „{title}“ gibt es Dissens",
        "pushed": "auf `{branch}` gepusht",
        "not_pushed": "{k} Commits auf `{branch}` nicht gepusht",
        "not_pushed_one": "1 Commit auf `{branch}` nicht gepusht",
        "no_upstream": "`{branch}` hat kein Upstream, nichts gepusht",
        "ci_green": "CI grün",
        "ci_red": "CI rot",
        "spec_delivered": "{title}: {x} von {n} Punkten geliefert",
        "spec_blocked": "{title}: angehalten bei {x} von {n} Punkten",
        "spec_blocked_done": "{title}: umgesetzt, {x} von {n} Punkten geliefert, nicht abgeschlossen",
        "spec_dissent": "{title}: Dissens bei {x} von {n} Punkten",
        "spec_not_started": "{title}: nicht begonnen",
        "spec_waits": "{title}: nicht begonnen, wartet auf {on}",
        "spec_after_end": "{title}: nicht begonnen, weil der Lauf bei {on} endete",
        "spec_after_stop": "{title}: nicht begonnen, weil du den Lauf beendet hast",
        "reason": "Grund",
        "nothing": "nichts",
        "must": "Für dich zu tun",
        "must_for": "Für {title}:",
        "must_then": "Danach:",
        "and": "und",
        "resume_continue": "{titles} fortsetzen",
        "resume_start": "{titles} umsetzen lassen",
        "resume": "{what}: `/docket:implement {specs}`",
        "commit_records": "Die Protokolle committen: `{add}`, dann `{commit}`",
        # "Empfehlung: Ich rate dir …" read as a fault of the delivery (luradb-web-client 2026-11-11: four of them in
        # prose, each with a command): a proposal is work after the run, named as that and taken up by its number.
        "repaired": "Außerdem behoben, außerhalb des Auftrags ({k})",
        "proposals": "Vorschläge für später, außerhalb des Auftrags ({k})",
        "proposals_how": "Eine Spec daraus: `/docket:spec` und die Nummer des Vorschlags.",
        "proposal_order": ("Vorschlag {n} des letzten Laufs, aus „{title}“: {what}. Warum: {why}. Warum der Lauf es "
                           "nicht selbst erledigt hat: {outside}. Das ist der Auftrag dieser Spec."),
        "proposal_unknown": "Der letzte Lauf hat keinen Vorschlag {n}.",
        "dissent_line": "Dissens",
        "watch": "Zu beobachten ({k})",
        "note": "Deine Nachricht von {time}",
        "notes_answer": "Zu deinen Nachrichten",
        "note_unhandled": "Deine Nachricht von {time} ist noch nicht bearbeitet",
        "notes_unhandled": "{k} Nachrichten von dir sind noch nicht bearbeitet ({times})",
        "deviates": "„{point}“ weicht von der Spec ab",
        "dirty": "der Arbeitsbaum hat uncommittete Änderungen",
        "log": "Protokolle: `{path}`",
        "delivered": "geliefert",
        "blocked": "angehalten",
        "dissent": "Dissens",
        "interrupted": "unterbrochen",
        "running": "läuft",
        "not_started": "nicht begonnen",
        "series_interrupted": "Unterbrochene Reihe: {items}. `/docket:implement {specs}` setzt sie fort.",
        "series_running": "Reihe: {items}.",
    },
    "en": {
        "position": "spec {i} of {n} · ",
        "preparing": "checked in advance from three angles",
        "rewrite": "the spec is corrected after the check",
        "points": "{x} of {n} points delivered",
        "deviating": ", {d} deviating",
        "closing": "the run is over, the closing message follows",
        "handover": ("Spec {i} of {n} is sealed: {title} ({spec}). Start it at step 0 of the implement command; nothing of "
                     "the previous spec is a fact unless you read it again. Until the run ends you write no text."),
        "handover_known": " Already proposed to the user in this run; do not write it again as a `decision:`: {items}",
        "closing_order": ("The run is over. Read {path} with Read and send its content as your only message: verbatim, "
                          "nothing added, no code block."),
        "closing_again": ("The message does not match {path} ({problem}) and was not shown. Send the file's content "
                          "verbatim, nothing added, no code block."),
        "not_finished": ("The run is not finished: {problem}. Go on working. A spec ends only with its docket's "
                         "`Terminal state` section, then the turn ends without text; a turn while agents run also ends "
                         "without text. `state: BLOCKED` is only for what the user has to resolve in the project, never "
                         "for a fault in the docket's own lines: a line is corrected by writing it again below."),
        "no_terminal": "no agent is running and the docket's `Terminal state` section has no `state:` line",
        "docket_message": ("there is no `DOCKET …` message any more: a spec ends in its docket, and the hook renders the "
                           "run's one message at the end"),
        "halted": ("{title}: the run stopped without being finished. `/docket:implement {specs}` resumes it, and so "
                   "does any message from you."),
        "sealed_elsewhere": "“{title}” runs in another session. Finish it there or stop it with `/docket:stop`.",
        "queued_in": "“{title}” waits in another session's series. That series reaches it, or `/docket:stop` there.",
        "sealed_on": "This session works on “{title}”. Finish it or `/docket:stop` first.",
        "forbidden_precondition": ("The precondition `{command}` of “{title}” matches forbidden_commands in DOCKET.md; "
                                   "check it yourself and take it out of the spec."),
        "precondition": ("Precondition of “{title}” not met: `{command}` should give {expected} ({got}). Settle "
                         "that first, then start the run."),
        "head_one": "Run finished: {title} is implemented",
        "head_done": "Run finished: {d} of {n} specs implemented",
        # A person reads "blocked" as "nothing works any more": the message says where the spec stands and whom it needs.
        "head_blocked": "Run stopped: “{title}” needs you, {d} of {n} specs implemented",
        "head_dissent": "Run stopped: “{title}” has a dissent, {d} of {n} specs implemented",
        "head_stopped": "Run ended at your request: {d} of {n} specs implemented",
        "head_past_blocked": "Run finished: {d} of {n} specs implemented, “{title}” needs you",
        "head_past_dissent": "Run finished: {d} of {n} specs implemented, “{title}” has a dissent",
        "pushed": "pushed to `{branch}`",
        "not_pushed": "{k} commits on `{branch}` not pushed",
        "not_pushed_one": "1 commit on `{branch}` not pushed",
        "no_upstream": "`{branch}` has no upstream, nothing pushed",
        "ci_green": "CI green",
        "ci_red": "CI red",
        "spec_delivered": "{title}: {x} of {n} points delivered",
        "spec_blocked": "{title}: stopped at {x} of {n} points",
        "spec_blocked_done": "{title}: implemented, {x} of {n} points delivered, not closed",
        "spec_dissent": "{title}: dissent at {x} of {n} points",
        "spec_not_started": "{title}: not started",
        "spec_waits": "{title}: not started, waits for {on}",
        "spec_after_end": "{title}: not started, because the run ended at {on}",
        "spec_after_stop": "{title}: not started, because you ended the run",
        "reason": "Reason",
        "nothing": "nothing",
        "must": "For you to do",
        "must_for": "For {title}:",
        "must_then": "Then:",
        "and": "and",
        "resume_continue": "Continue {titles}",
        "resume_start": "have {titles} implemented",
        "resume": "{what}: `/docket:implement {specs}`",
        "commit_records": "Commit the records: `{add}`, then `{commit}`",
        "repaired": "Also repaired, outside the order ({k})",
        "proposals": "Proposals for later, outside the order ({k})",
        "proposals_how": "A spec from one: `/docket:spec` and the proposal's number.",
        "proposal_order": ("Proposal {n} of the last run, from “{title}”: {what}. Why: {why}. Why the run did not do "
                           "it itself: {outside}. This is the order of this spec."),
        "proposal_unknown": "The last run has no proposal {n}.",
        "dissent_line": "Dissent",
        "watch": "To watch ({k})",
        "note": "Your message of {time}",
        "notes_answer": "On your messages",
        "note_unhandled": "Your message of {time} is not handled yet",
        "notes_unhandled": "{k} messages of yours are not handled yet ({times})",
        "deviates": "“{point}” deviates from the spec",
        "dirty": "the working tree has uncommitted changes",
        "log": "Records: `{path}`",
        "delivered": "delivered",
        "blocked": "stopped",
        "dissent": "dissent",
        "interrupted": "interrupted",
        "running": "running",
        "not_started": "not started",
        "series_interrupted": "Interrupted series: {items}. `/docket:implement {specs}` resumes it.",
        "series_running": "Series: {items}.",
    },
}


def say(paths, key, **values):
    return SAY[language_code(paths) or "en"][key].format(**values)


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


def status_line(paths, state):
    """What the user sees live instead of an orchestrator's text in a sealed run: where the series stands, no
    clock (luradb-web-client 2026-11-06: 105 status lines, 30 equal to the one before, 51 changed only the minute;
    the gate now shows a line only when it differs from the last one shown)."""
    spec_id = state.get("spec", "")
    counts = docket_counts(paths, spec_id)
    if state.get("mode") == "closing":
        progress = say(paths, "closing")
    elif state.get("mode") == "rewrite":
        progress = say(paths, "rewrite")
    elif counts is None:
        progress = say(paths, "preparing")
    else:
        progress = say(paths, "points", x=counts["x"], n=counts["x"] + counts["~"] + counts[" "])
        if counts["~"]:
            progress += say(paths, "deviating", d=counts["~"])
    i, n = position(state)
    prefix = say(paths, "position", i=i, n=n) if n > 1 else ""
    if prefix and language_code(paths) != "de":
        prefix = prefix[0].upper() + prefix[1:]
    return f"[docket] {prefix}{spec_title(paths, spec_id) or spec_id}: {progress}"


FILE_LINE = re.compile(r"(?<![\w/:.@])((?:[\w.-]+/)*[\w.-]+\.[a-z]{1,5}):(\d{1,6})\b(?!/)", re.IGNORECASE)
HOST_SUFFIX = re.compile(r"\.(com|net|org|de|io|local|internal|corp|lan|dev|test|app|cloud)$", re.IGNORECASE)


def missing_citations(text, paths):
    """Every `path/file.ext:line` in the text that does not exist (relative to the repo root or the work dir) or
    has fewer lines, at most six."""
    missing = []
    for match in FILE_LINE.finditer(text):
        rel, line = match.group(1), int(match.group(2))
        candidates = [posixpath.join(paths.root, rel), posixpath.join(paths.work, rel), rel]
        found = next((c for c in candidates if os.path.isfile(c)), None)
        if found is None and "/" not in rel and HOST_SUFFIX.search(rel):
            continue  # a host and its port (staging.example.com:8443), not a file
        if found is None:
            missing.append(f"{rel}:{line}")
            continue
        try:
            with open(found, encoding="utf-8", errors="ignore") as handle:
                count = sum(1 for _ in handle)
        except OSError:
            continue
        if line > count:
            missing.append(f"{rel}:{line} (file has {count} lines)")
    return sorted(set(missing))[:6]


# Language check: the message to the user is in the configured language. Function words decide; code spans and
# URLs do not count. "in" and "an" stand in both lists and are left out.
WORDS = {
    "de": re.compile(r"\b(der|die|das|und|ist|nicht|mit|auf|für|ein|eine|den|dem|sind|wird|von|zu|im|bei|nach|"
                     r"noch|auch|wurde|wurden|keine|kein|nur|oder|wie|als|aus|sich|du|musst|nichts|offen|fertig)\b",
                     re.IGNORECASE),
    "en": re.compile(r"\b(the|and|is|not|with|for|a|are|of|to|on|was|were|no|only|or|as|from|you|must|nothing|"
                     r"still|also|now|open|done|it|this|that|by|at)\b", re.IGNORECASE),
}


def language_problem(text, paths):
    """One problem when the text's function words are those of the other language, else []. Short texts
    (fewer than eight words of prose) are not judged."""
    code = language_code(paths)
    if not code:
        return []
    prose = re.sub(r"`[^`\n]*`|https?://\S+|^stamp:.*$|^\[docket\].*$|^DOCKET (DELIVERED|BLOCKED|DISSENT).*$", "",
                   text, flags=re.MULTILINE | re.IGNORECASE)
    if len(prose.split()) < 8:
        return []
    other = "en" if code == "de" else "de"
    mine, theirs = len(WORDS[code].findall(prose)), len(WORDS[other].findall(prose))
    if theirs > mine:
        name = config_values(paths).get("user_language", code)
        return [f"the message is not in {name} (DOCKET.md `user_language`): write it in {name}; only `DOCKET …`, "
                f"`stamp:` and quoted commands keep their form"]
    return []


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
    return {"version": STATE_VERSION, "mode": "none", "spec": "", "degraded": False, "read": []}


def load_state(paths, session_id):
    data, problem = read_json(state_path(paths, session_id))
    if isinstance(data, dict):
        state = State(data)
        state.base = json.loads(json.dumps(data))
        state.setdefault("mode", "none")
        if state["mode"] not in ("none", "init") and not os.path.isfile(paths.config):
            # A project that is not set up has no mode but init: a state that says otherwise is read as none, by
            # every hook, and the file is left as it is (docket 2026-11-10: a session in a repo without DOCKET.md was
            # sealed on another project's specs and swallowed eight of the user's messages).
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


# --- display gate ------------------------------------------------------------
# The MessageDisplay hook holds a message back before the user sees it; the Stop hook then asks for it
# again. Both meet in one record per session: `hidden` (messages held back since the last Stop),
# `retry` (after a refusal every message is shown until a Stop lets one through), `pending`
# (messages whose last batch has not arrived yet).

HIDDEN = "​"  # a zero-width space: an empty displayContent shows the original in the desktop app (exp6)
PENDING_MAX_AGE = 60


def display_path(paths, session_id):
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        session_id = "shared"
    return posixpath.join(paths.state_dir, f"display-{session_id}.json")


def buffer_path(paths, session_id, message_id):
    return display_path(paths, session_id)[:-len(".json")] + f"-{message_id}.txt"


def load_display(paths, session_id):
    record, _ = read_json(display_path(paths, session_id))
    if not isinstance(record, dict):
        record = {}
    record.setdefault("hidden", [])
    record.setdefault("pending", {})
    record.setdefault("retry", False)
    return record


def save_display(paths, session_id, record):
    write_json(display_path(paths, session_id), record)


def settled_display(paths, session_id, wait=3.0):
    """The record once no message is half-displayed: a Stop can arrive while the last batch of a long
    message is still on its way (terminal client); it waits for that batch, at most `wait` seconds."""
    deadline = time.time() + wait
    while True:
        record = load_display(paths, session_id)
        now = time.time()
        pending = {k: v for k, v in record["pending"].items() if isinstance(v, (int, float)) and now - v < PENDING_MAX_AGE}
        if not pending or now >= deadline:
            return record
        time.sleep(0.1)


def prune_states(directory, max_age_days=14):
    now = time.time()
    try:
        for name in os.listdir(directory):
            path = posixpath.join(directory, name)
            if name != "judge-suspended.json" and now - os.path.getmtime(path) > max_age_days * 86400:
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


def log_time(line):
    """The UTC epoch of a LOG line, or None."""
    try:
        return calendar.timegm(time.strptime(line[:20], "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


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


# --- clock (M7) --------------------------------------------------------------
# The hooks hand the time to the model with every prompt and every tool result, so the stamp needs no tool
# call (25 of 174 calls fetched the time on 2026-11-05). stop.py accepts a stamp equal to the last given value.

def clock_line(paths, session_id, state=None, save=True):
    """Saves the current time in the session state and returns the context line that carries it. `save=False` for
    an agent's tool call: only the orchestrator's stamp is checked against the clock, and an agent writes nothing
    into the session state."""
    now = time.strftime("%Y-%m-%d %H:%M")
    if save:
        state = state if state is not None else load_state(paths, session_id)
        state["clock"] = now
        save_state(paths, session_id, state)
    return f"[docket] clock: {now}"


# --- the mode the user typed -------------------------------------------------
# The state file is docket's own record of the mode; the transcript is the harness's record of what the user typed.
# Where the state says none and the user's last mode command says otherwise, the state is wrong (no hook ever sets
# a mode back to none), and the user is not asked to type the command again.
MODE_COMMAND = (r"/(?:docket:(?P<full>concept|spec|init|implement)|(?P<bare>concept|spec|implement))\b"
                )  # /init is Claude Code's
RAN_COMMAND = re.compile(r"<command-name>\s*" + MODE_COMMAND, re.IGNORECASE)  # how the transcript keeps a command
TYPED_COMMAND = re.compile(MODE_COMMAND, re.IGNORECASE)
RESTORABLE_MODES = ("concept", "spec", "init")


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


def typed_mode(transcript_path):
    """The mode of the last mode command the user typed in this session: concept, spec or init; "" for none and
    for implement, whose run (series, queue, outcomes) cannot be rebuilt from a command."""
    try:
        with open(transcript_path, "rb") as handle:
            raw = handle.read().decode("utf-8", errors="ignore")
    except (OSError, TypeError, ValueError):
        return ""
    found = ""
    for line in raw.splitlines():
        if "/docket:" not in line and "/concept" not in line and "/spec" not in line and "/implement" not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not is_user_prompt(entry):
            continue
        content = entry["message"].get("content")
        if isinstance(content, list):
            content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        content = strip_harness_blocks(content or "")
        match = RAN_COMMAND.search(content) or TYPED_COMMAND.match(command_line(content))
        if match:
            found = (match.group("full") or match.group("bare")).lower()
    return found if found in RESTORABLE_MODES else ""


def restore_mode(paths, session_id, state, transcript_path):
    """Puts back a mode the state lost. True when the state now carries the mode the user typed."""
    if state.get("mode", "none") != "none" or not os.path.isfile(paths.config):
        return False
    mode = typed_mode(transcript_path)
    if not mode:
        return False
    state.update({"mode": mode, "spec": ""})
    if mode == "init":
        state["init_approved"] = False
    save_state(paths, session_id, state)
    log_event(paths, session_id, "", "mode-restored", mode)
    return True


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
STOP_FEEDBACK = "Stop hook feedback"


def strip_harness_blocks(prompt):
    """The prompt without what the harness put into it: what is left is what the user typed."""
    text = HARNESS_BLOCK.sub("", prompt)
    if AGENT_MESSAGE.search(prompt):
        text = AGENT_FRAME.sub("", text)
    return text


def is_user_prompt(entry):
    """A prompt the user typed: not a tool result, not the Stop hook's feedback, not a harness notice alone."""
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    if entry.get("isMeta") or entry.get("isCompactSummary"):
        return False  # a skill's text after a Skill call, the summary after a compaction: no user spoke
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        if all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str) or content.lstrip().startswith(STOP_FEEDBACK):
        return False
    return bool(strip_harness_blocks(content).strip())


def previous_turn_texts(transcript_path):
    """The assistant's texts of the previous turn: what stood between the second-last and the last prompt of the
    user. Tool results, harness notices and the Stop hook's feedback do not start a turn."""
    turns = [[]]
    for entry in transcript_entries(transcript_path):
        if entry.get("isSidechain"):
            continue
        if is_user_prompt(entry):
            turns.append([])
        elif entry.get("type") == "assistant":
            message = entry.get("message")
            text = text_of(message.get("content") if isinstance(message, dict) else None)
            if text is not None:
                turns[-1].append(text)
    return turns[-2] if len(turns) >= 2 else []


def current_turn(transcript_path):
    """The main line's entries since the user's last prompt. A harness notice (a finished workflow, a session
    note) continues the turn: no user has spoken."""
    entries = []
    for entry in transcript_entries(transcript_path):
        if entry.get("isSidechain"):
            continue
        if is_user_prompt(entry):
            entries = []
        else:
            entries.append(entry)
    return entries


BLOCKER_LINE = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)?(?:\*\*)?blockers?\b", re.IGNORECASE)
NO_BLOCKER = re.compile(r"blockers?\W*(?:keine?r?|kein|none|nothing|nichts)\b", re.IGNORECASE)


def blocker_since_prompt(transcript_path):
    """The `Blocker` line of a message that ended a turn (a Stop followed it) since the user's last prompt, or "".
    Legacy session 2026-11-05: the message at 21:58 named the blocker, the turn a finished workflow woke at 22:25
    wrote the concept file and the flags anyway."""
    pending = None
    for entry in current_turn(transcript_path):
        if entry.get("type") == "assistant":
            message = entry.get("message")
            text = text_of(message.get("content") if isinstance(message, dict) else None)
            if text is not None:
                pending = text
        elif entry.get("type") == "system" and entry.get("subtype") == "stop_hook_summary" and pending:
            for line in pending.splitlines():
                if BLOCKER_LINE.match(line) and not NO_BLOCKER.search(line):
                    return line.strip()
            pending = None
    return ""


def box_questions(transcript_path):
    """The questions of the next-step box (the desktop app's wrap_up tool) asked since the last Stop: a box the
    user already saw belongs to an earlier message, even when a notification continued the turn."""
    asks = []
    for entry in current_turn(transcript_path):
        if entry.get("type") == "system" and entry.get("subtype") == "stop_hook_summary":
            asks = []
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if entry.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and str(block.get("name", "")).endswith("__wrap_up")):
                ask = (block.get("input") or {}).get("ask")
                if isinstance(ask, str) and ask.strip():
                    asks.append(ask.strip())
    return asks


def hook_outputs(transcript_path):
    """Everything this session heard a hook say, first or second hand: refusals in tool results, Stop hook feedback,
    hook context, and what the user pasted or an agent reported back."""
    said, reporters = [], set()
    for entry in transcript_entries(transcript_path):
        if entry.get("isSidechain"):
            continue
        if entry.get("type") == "attachment":
            attachment = entry.get("attachment")
            if isinstance(attachment, dict) and attachment.get("hookEvent"):
                said.append(str(attachment.get("content") or ""))
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if entry.get("type") == "assistant" and isinstance(content, list):
            reporters.update(b.get("id") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
                             and b.get("name") in ("Agent", "Task", "Workflow"))
            continue
        if entry.get("type") != "user":
            continue
        if isinstance(content, str):
            said.append(content)  # a prompt (pasted text included) or the Stop hook's feedback
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result" and (block.get("is_error") or block.get("tool_use_id") in reporters):
                inner = block.get("content")
                said.append(inner if isinstance(inner, str) else
                            " ".join(b.get("text", "") for b in inner or [] if isinstance(b, dict)))
            elif block.get("type") == "text":
                said.append(block.get("text", ""))
    return " ".join(" ".join(said).split())


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


# --- message text ------------------------------------------------------------

def text_of(content):
    if isinstance(content, list):
        content = "".join(
            block["text"] for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
    return content if isinstance(content, str) and content.strip() else None


def last_assistant_text(transcript_path, unchecked=False):
    """The newest text of the assistant. `unchecked`: only one no Stop has seen yet (no `stop_hook_summary` after
    it); a text an earlier Stop passed is not the message of a later turn that ended without text."""
    try:
        with open(transcript_path, encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
    except (OSError, TypeError):
        return None
    text = None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (unchecked and isinstance(entry, dict) and entry.get("type") == "system"
                and entry.get("subtype") == "stop_hook_summary" and not entry.get("isSidechain")):
            text = None
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        found = text_of(message.get("content")) if isinstance(message, dict) else None
        if found is not None:
            text = found
    return text


def message_text(hook_input, transcript_key="transcript_path", strict=False):
    """The message that ends the turn. `strict`: only `last_assistant_message`; an empty turn stays empty and never
    falls back to an older text of the transcript (a sealed run ends its waits without text). Otherwise the
    transcript's newest text counts while no Stop has seen it: a turn that ends without text (a wait for agents, the
    answer to a judge verdict) is not checked against the message of the turn before, whose stamp has aged."""
    text = text_of(hook_input.get("last_assistant_message"))
    if text is not None or strict:
        return text
    return last_assistant_text(hook_input.get(transcript_key), unchecked=transcript_key == "transcript_path")


# --- judge -------------------------------------------------------------------
# The judge (templates/judge-hook.json) is a prompt hook at Stop: it runs after the user has read the message.
# A rejection goes back to Claude, which answers with an empty turn (experiment 10d: no message is recorded,
# nothing is shown, the verdict stays in the transcript's hook records and is counted at the next Stop).
# Experiment 9: with `impossible: true` the verdict never reaches the transcript, so it could not be counted.
# The older acknowledgement line `[docket] noted` is still accepted and hidden live (not durably: experiment 10).
JUDGE_PROMPT = "[You judge the final message"  # how Claude Code quotes the judge's prompt in front of its reason
JUDGE_ACK = "[docket] noted"
JUDGE_TAGS = ("no-source", "questions", "options", "manual-task", "conditional", "promise", "self", "identifier",
              "is-not-should")
HELD_BACK = "held back and never shown"  # stop.py's request for a full rewrite: that answer is always shown
TAIL_BYTES = 262144


def judge_verdicts(transcript_path, since=""):
    """(reasons, last): the judge's reasons recorded after the ISO timestamp `since` (stop_hook_summary,
    hookErrors "[<prompt>]: <reason>"), and the timestamp of the last summary read."""
    reasons, last = [], ""
    for entry in transcript_entries(transcript_path):
        if entry.get("type") != "system" or entry.get("subtype") != "stop_hook_summary" or entry.get("isSidechain"):
            continue
        stamp = str(entry.get("timestamp") or "")
        if not stamp or stamp <= since:
            continue
        last = max(last, stamp)
        for error in entry.get("hookErrors") or []:
            error = str(error)
            if error.startswith(JUDGE_PROMPT) and "]: " in error:
                reason = " ".join(error.split("]: ", 1)[1].split())
                reasons.append(re.split(r"\s*The user has (?:already )?read", reason)[0].strip())
    return reasons, last


def judge_tags(reason):
    """The rule tags of a verdict: the first word of each ` | `-separated entry; otherwise any tag named."""
    tags = []
    for entry in reason.split("|"):
        words = entry.strip().split()
        tag = words[0].strip("`\"':,.").lower() if words else ""
        if tag in JUDGE_TAGS:
            tags.append(tag)
    if not tags:  # free text (the judge before the tags): the rules as the prompt words them
        lowered = reason.lower()
        tags = [tag for tag, pattern in JUDGE_PHRASES if re.search(pattern, lowered)]
    return tags


JUDGE_PHRASES = (("no-source", r"without (a )?source|no source|not a result with (a )?source"),
                 ("questions", r"more than one question"), ("options", r"options? without"),
                 ("manual-task", r"manual task"), ("conditional", r"conditional"), ("promise", r"promise"),
                 ("self", r"apolog|justification|effort|time spent"), ("identifier", r"identifier"),
                 ("is-not-should", r"what is\b|currently behaves"))


def collect_judge(paths, session_id, state, transcript_path):
    """Counts and logs the judge's verdicts since the last call; state["judge_seen"] marks how far."""
    reasons, last = judge_verdicts(transcript_path, state.get("judge_seen", ""))
    if not last:
        return
    state["judge_seen"] = last
    save_state(paths, session_id, state)
    doc_id = state.get("spec", "")
    for reason in reasons:
        if state.get("mode") in RUN_MODES:
            # The judge has no place in a sealed run (framework/ENTSCHEIDUNGEN.md, Stop-Hook): where it could not be
            # suspended (a tracked settings file), its verdict is recorded and counts as nothing.
            log_event(paths, session_id, doc_id, "judge-ignored", reason[:120])
            continue
        log_event(paths, session_id, doc_id, "judge", reason[:120])
        for tag in judge_tags(reason) or ["unnamed"]:
            record_violation(paths, doc_id, f"judge {tag}")


NUDGE = "[Your previous response had no visible output"  # the harness after a turn that ended without text


def answers_judge(transcript_path):
    """Truthy when the newest text of the user's side is the judge's feedback and no request to write a held-back
    message again: the message being displayed is the acknowledgement, and the user has already read the
    message it answers. "direct": it answers the verdict itself. "nudged": the verdict was answered with an empty
    turn, as asked, and the harness then demanded a visible answer (Claude Code Desktop 2.1.321, luradb-web-client
    2026-11-10: six of six verdicts ended in a line `Blocker unverändert` or a bare stamp on the user's screen)."""
    # Claude Code writes each Stop hook's feedback as an entry of its own, the slower judge last: the whole block of
    # feedback since the last text is read, or stop.py's request for a full rewrite is missed and the rewrite hidden
    # (legacy session 2026-11-05, entries 522 and 523).
    judged, nudged = False, False
    for entry in reversed(transcript_entries(transcript_path, tail=True)):
        if entry.get("isSidechain"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if entry.get("type") == "assistant":
            if judged and text_of(content) is not None:
                break  # the message the feedback answers
            continue
        if entry.get("type") != "user":
            continue
        if isinstance(content, list):
            if all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue  # the answer's own tool calls
            content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            continue
        if content.lstrip().startswith(NUDGE) and not judged:
            nudged = True
            continue
        if not content.lstrip().startswith(STOP_FEEDBACK):
            break  # a prompt of the user ends the block
        if HELD_BACK in content:
            return ""
        judged = judged or JUDGE_PROMPT in content
    return ("nudged" if nudged else "direct") if judged else ""


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


CONTEXT = {}
# The user's side of a refusal (luradb-web-client 2026-11-10: a session whose mode was lost told the user about modes,
# hook texts and the judge, and sent them to type their command again). Said with every prompt and session start of a
# conversation, and with every refused tool call of one.
REFUSAL_RULE = ("A refusal by a hook is yours to resolve. The user reads nothing about it: no hook, no refusal text, "
                "no session state, no judge. If only the user can clear it, say in one sentence what they decide or "
                "type.")


def set_context(paths, doc_id):
    """Where refusals are counted: the metrics file of the document the session works on."""
    CONTEXT.update({"paths": paths, "doc_id": doc_id or ""})


def in_conversation(flag=True):
    """The orchestrator's next message goes to the user (no run, no agent): a refusal ends with REFUSAL_RULE."""
    CONTEXT["suffix"] = REFUSAL_RULE if flag else ""


def rule_key(message):
    words = re.sub(r"^\[docket\]\s*", "", message).lower()
    words = re.sub(r"[^a-z0-9 ]+", " ", words).split()
    return "-".join(words[:5]) or "unnamed"


def violations(paths, doc_id):
    try:
        with open(paths.violations_file(doc_id), encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_violation(paths, doc_id, message):
    """Measurement with feedback: every refusal is counted per rule in metrics/<id>.violations.json
    and shown again at the next /implement of that spec and at session start."""
    counts = violations(paths, doc_id)
    key = rule_key(message)
    counts[key] = int(counts.get(key, 0)) + 1
    try:
        os.makedirs(paths.metrics, exist_ok=True)
        with open(paths.violations_file(doc_id), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(counts, handle, indent=1, sort_keys=True)
    except (OSError, TypeError):
        pass


def violations_line(paths, doc_id):
    counts = violations(paths, doc_id)
    if not counts:
        return ""
    top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    return "[docket] Refusals so far for " + (doc_id or "this session") + ": " + ", ".join(
        f"{key} ({n})" for key, n in top) + ". Each one is a rule you broke; do not repeat it."


def point_counts(body):
    """Boxes of a docket: delivered [x], deviations [~], open [ ] (backticked ones ignored); only those of the
    points section when the docket has one."""
    match = re.search(r"^##\s+Points\b.*?$(.*?)(?=^##\s|\Z)", body or "", re.MULTILINE | re.DOTALL | re.IGNORECASE)
    body = match.group(1) if match else (body or "")
    return {mark: len(re.findall(r"(?<!`)\[" + re.escape(mark) + r"\](?!`)", body)) for mark in ("x", "~", " ")}


def refuse(message):
    print(message + (" " + CONTEXT["suffix"] if CONTEXT.get("suffix") else ""), file=sys.stderr)
    if CONTEXT.get("paths") is not None:
        record_violation(CONTEXT["paths"], CONTEXT.get("doc_id", ""), message)
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


def has_section(body, title):
    return bool(re.search(r"^##\s+" + re.escape(title) + r"\b", body or "", re.MULTILINE | re.IGNORECASE))


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


def deviation_points(body):
    """The names of the `[~]` points in the docket's points table."""
    names = []
    for line in (body or "").splitlines():
        if line.strip().startswith("|") and "[~]" in line:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[1]:
                names.append(cells[1])
    return names


DECISION_WORDS = re.compile(r"\b(adopted|rejected|rewrite|rewritten|dissent|moot)\b", re.IGNORECASE)
REVIEW_DECISION = re.compile(r"\b(fixed|rejected|deferred|no findings|behoben|abgelehnt|zurückgestellt|keine befunde)\b",
                             re.IGNORECASE)


def refinement_problem(paths, spec_id):
    """Every refinement line carries a decision, in every `## Refinement…` section (luradb-web-client 2026-11-06:
    lines added under a second Refinement heading were never checked)."""
    docket = paths.docket_file(spec_id)
    if docket is None:
        return "[docket] No docket yet: write the skeleton (one row per spec point) before the first implementer."
    body = read_text(docket)
    lines = bullets(section_text(body, "Refinement"))
    # The docket is append-only: a line written without its decision cannot be completed in place, so it is closed
    # by the line written again below with the decision (without this the first implementer was refused for good).
    flat = lambda line: " ".join(line.lower().split())
    decided = [flat(line) for line in lines if DECISION_WORDS.search(line)]
    open_lines = [line for line in lines if not DECISION_WORDS.search(line)
                  and not any(later.startswith(flat(line)) for later in decided)]
    if open_lines:
        return ("[docket] Refinement lines without a decision (adopted / rejected / rewrite / dissent): "
                + " | ".join(line[:70] for line in open_lines[:3]) + ". Close them before the first implementer: "
                "write each line again below, complete with its decision; the later line counts.")
    return ""


REVIEW_ANGLE = re.compile(r"^[`*]*(code|spec|recheck)\b", re.IGNORECASE)
ROUND_ONE = ("code", "spec")
SEVERITIES = ("bug", "suspect", "unmet", "rule", "test", "docs", "scope", "cleanup")
MUST_FIX = ("bug", "unmet", "rule")
NO_FINDINGS = re.compile(r"no findings|keine befunde", re.IGNORECASE)
DEFERRED = re.compile(r"\b(deferred|zurückgestellt)\b", re.IGNORECASE)
OBSERVE = ("observe", "beobacht")


def review_cells(line):
    return [cell.strip().strip("`*").strip().lower() for cell in line.split("|")]


def review_line(line):
    """A Review line `<angle> | <finding> | <source> | <severity> | <decision>` as (angle, severity, deferred); a
    part the line does not carry is empty."""
    cells = review_cells(line)
    angle = REVIEW_ANGLE.match(line.strip())
    severity = next((cell for cell in cells[1:] if cell in SEVERITIES), "")
    return (angle.group(1).lower() if angle else "", severity, bool(DEFERRED.search(cells[-1])))


def observed(line):
    """The late exit as a line's decision: a cell, not the first, that starts with `observe`. A Terminal state
    line (`watch: …`) decides nothing, it reports."""
    if TERMINAL_LINE.match(line.strip()):
        return False
    return any(cell.startswith(OBSERVE) for cell in review_cells(line)[1:])


WATCH_PARTS = ("seen", "suspected", "impact", "signal")


DECISION_PARTS = ("why", "outside")


def line_parts(value, names):
    """A Terminal state line of several parts, `<subject> | <name>: … | <name>: …`. A pipe inside a part stays in
    that part."""
    cells = [cell.strip() for cell in value.split("|")]
    parts = dict.fromkeys(("subject",) + tuple(names), "")
    parts["subject"], last = cells[0], "subject"
    for cell in cells[1:]:
        match = re.match(r"[`*]*(" + "|".join(names) + r")[`*]*\s*:\s*(.*)$", cell, re.IGNORECASE | re.DOTALL)
        if match:
            last = match.group(1).lower()
            parts[last] = match.group(2).strip()
        else:
            parts[last] += " | " + cell
    return parts


def watch_parts(value):
    """A `watch:` line as the user gets it, the whole case in one piece so that a spec can be cut from it:
    `<what it is about> | seen: … | suspected: … | impact: … | signal: …`."""
    return line_parts(value, WATCH_PARTS)


def decision_parts(value):
    """A `decision:` line, a proposal for work after the run: `<what to do, and where> | why: <what the user gains or
    risks> | outside: <why the run could not do it itself>`. The message shows the first two. The third is what
    keeps a run from handing back what it could have done itself (luradb-web-client 2026-11-11: four pieces of
    advice, two of them a line in a file the run had just written and a fault it had measured itself)."""
    return line_parts(value, DECISION_PARTS)


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


def review_problem(paths, spec_id):
    """V14: the whole diff of the spec is reviewed before the push, from two angles (`code`, `spec`); each finding
    is a docket line with its severity and its decision, an angle without findings the line `<angle> | no findings`.
    A bug, an unmet point and a broken rule are fixed or refuted, never deferred into a delivery."""
    body = read_text(paths.docket_file(spec_id))
    form = ("each finding is a line `<angle> | <finding> | <source> | <severity> | <decision>` (fixed / rejected with "
            "its source / deferred), an angle without findings the line `<angle> | no findings`")
    if not has_section(body, "Review"):
        return ("[docket] The docket has no `## Review` section yet: two reviewer agents (angles `code` and `spec`) "
                "read the whole diff before the push; " + form + ".")
    lines = review_lines(body)
    if not lines:
        return "[docket] The docket's Review section is empty: " + form + "."
    undecided = [line for line in lines if not REVIEW_DECISION.search(line) and not observed(line)]
    if undecided:
        return ("[docket] Review lines without a decision (fixed / rejected / deferred): "
                + " | ".join(line[:70] for line in undecided[:3]) + ".")
    parsed = [(line, *review_line(line)) for line in lines]
    missing = [angle for angle in ROUND_ONE if angle not in {angle for _, angle, _, _ in parsed}]
    if missing:
        return ("[docket] The Review section has no line of the angle " + " and ".join(f"`{a}`" for a in missing)
                + ": round one is two reviewers, the whole diff as code and the delivery against the spec; " + form + ".")
    bare = [line for line, angle, severity, _ in parsed if angle and not severity and not NO_FINDINGS.search(line)]
    if bare:
        return ("[docket] Review lines without a severity (" + " / ".join(SEVERITIES) + "), as the reviewer gave it: "
                + " | ".join(line[:70] for line in bare[:3]) + ". Write the line again below, complete: of two "
                "lines with the same angle and finding the later one counts.")
    kept = [line for line, _, severity, deferred in parsed if severity in MUST_FIX and deferred]
    if kept:
        return ("[docket] A finding of severity bug, unmet or rule is not deferred into a delivery: "
                + " | ".join(line[:70] for line in kept[:3]) + ". Fix it or reject it with the source that refutes it, "
                "and write the line again below with that decision; or end the spec with "
                "`state: BLOCKED: <the finding in everyday words>`.")
    early = [line for line, _, severity, deferred in parsed if severity == "suspect" and deferred]
    if early:
        return ("[docket] A suspect is not deferred, it is examined: " + " | ".join(line[:70] for line in early[:3])
                + ". A diagnoser triages it: shown or explained it is a `bug`, refuted it is rejected with its "
                "source, and only when the means are spent its line ends `… | suspect | cause: … | repro: … | "
                "impact: … | observe: <signal>`. Write the line again below with that decision.")
    stray = [line for line, _, severity, _ in parsed if observed(line) and severity != "suspect"]
    if stray:
        return ("[docket] Only a `suspect` is observed: " + " | ".join(line[:70] for line in stray[:3])
                + ". What a reviewer showed is fixed or rejected; what is neither shown nor explained is a `suspect`.")
    return ""


# --- the review is run, the acceptance is run (V15, V16) ------------------------
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


def review_run_problem(paths, spec_id, transcript_path):
    """V15: the review is run, not written. Once an implementer or fixer wrote in this session, a reviewer agent
    must have started after the last of them returned, and returned itself: round one on the whole diff, a later
    round on what was written since."""
    runs = agent_runs(transcript_path, sealed_stamp(paths, spec_id))
    written = last_written(runs)
    if written < 0 or any("reviewer" in kind and launch > written and back != STILL_RUNNING
                          for kind, launch, back, _ in runs):
        return ""
    return ("[docket] No reviewer has returned since the last implementer or fixer wrote: round one is two reviewer "
            "agents (angles `code` and `spec`) on the whole diff, a later round one of angle `recheck` on what was "
            "written since. The review is run, not written; no agent changes the project after it.")


# --- triage: the cause before the next attempt (V18, V19) -----------------------
TRIAGE_FIELDS = ("cause", "repro", "impact")
FIELD_END = r"(?=\|\s*[`*]*(?:cause|repro|impact|fix|deviation|observe|beobacht)|$)"


def triage_lines(body):
    """{subject: line} of the docket's Triage section, a subject being the line's first cell (`point 3`); of two
    lines for one subject the later one counts."""
    latest = {}
    for line in bullets(section_text(body, "Triage")):
        subject = " ".join(review_cells(line)[0].split())
        latest.pop(subject, None)
        latest[subject] = line
    return latest


def triage_field(line, name):
    """The text of a line's `name: …` field, up to the next field."""
    match = re.search(r"\b" + name + r"\s*:\s*(.*?)\s*" + FIELD_END, line, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def open_point(body):
    """The number of the first point still open `[ ]`: the one the run works on."""
    match = re.search(r"^##\s+Points\b.*?$(.*?)(?=^##\s|\Z)", body or "", re.MULTILINE | re.DOTALL | re.IGNORECASE)
    for line in (match.group(1) if match else (body or "")).splitlines():
        line = line.strip()
        if line.startswith("|") and re.search(r"(?<!`)\[ \](?!`)", line):
            return line.strip("|").split("|")[0].strip()
    return ""


def rework_problem(paths, spec_id, transcript_path):
    """V18: a failure gets a second attempt only after it was examined, and no third attempt at a symptom. Counted
    are the implementers that returned since the last point was closed; a point that passed is flipped before the
    next one starts, so more than one of them means rework."""
    body = read_text(paths.docket_file(spec_id))
    point = open_point(body)
    if not point:
        return ""
    since = max((line[:20] for line in log_lines(paths, spec_id)
                 if any(f" {event} " in line for event in ("sealed", "point-delivered", "deviation"))), default="")
    rounds = len([1 for kind, _, back, _ in agent_runs(transcript_path, since)
                  if "implementer" in kind and back != STILL_RUNNING])
    if not rounds:
        return ""
    line = triage_lines(body).get(f"point {point}".lower(), "")
    if not line or not triage_field(line, "cause"):
        return (f"[docket] An implementer has returned for point {point} and the point is still open: a second attempt "
                "comes after the failure was examined. Spawn a diagnoser with what the gate showed and write its "
                f"answer into the docket's Triage section: `point {point} | cause: … | repro: … | impact: … | fix`. "
                "(A point that passed is flipped to `[x]` before the next one starts.)")
    if rounds >= 2 and not FILE_LINE.search(triage_field(line, "cause")):
        return (f"[docket] Point {point} failed twice and its triage names no cause at a `file:line`: a third attempt "
                "at a symptom is not made. Have it examined again (the later Triage line counts), or close the point "
                "as `[~]` with its deviation line; a failure that is not shown reliably ends in `observe`.")
    return ""


def observe_problems(line, paths, spec_id, transcript_path):
    """V19: `observe` (a possible fault that needs watching) is the late exit. The line carries cause, repro and
    impact; the orchestrator ran the repro command three times itself and it did not fail all three times; a second
    diagnoser challenged the first one's answer."""
    problems = [f"it has no `{name}:`" for name in TRIAGE_FIELDS if not triage_field(line, name)]
    entries = transcript_entries(transcript_path)
    if not entries:
        return problems
    since = sealed_stamp(paths, spec_id)
    command = re.search(r"\brepro\s*:\s*`([^`]+)`", line, re.IGNORECASE)
    if command:
        failed = failed_calls(entries)
        runs = [block.get("id") in failed for _, block, stamp in tool_calls(entries, ("Bash", "PowerShell"))
                if command.group(1).strip() in str((block.get("input") or {}).get("command", ""))
                and not (since and stamp[:19] < since[:19])]
        if len(runs) < 3:
            problems.append(f"you ran its repro command `{command.group(1).strip()}` {len(runs)} of three times "
                            "yourself (one call each)")
        elif all(runs[-3:]):
            problems.append("its repro command failed in each of the last three runs: shown three of three is a "
                            "fault, not a thing to watch, so it is `fix` with its cause or `deviation`")
    examined = [1 for kind, _, back, _ in agent_runs(transcript_path, since)
                if "diagnoser" in kind and back != STILL_RUNNING]
    if len(examined) < 2:
        problems.append("no second diagnoser has returned: one examines the failure, one in mode `challenge` tries "
                        "to refute that answer")
    return problems


def point_commands(body, paths):
    """(point, command) for every delivered point `[x]` whose pre-registered acceptance line is
    `<command> → <expected>`. A line that names a file and what it must contain has no command."""
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
        command = parts[0].strip().strip("`").strip()
        if (len(parts) < 2 or not command or command.startswith("<")
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
            problems.append(f"the acceptance command of point {point} (`{command}`) never ran in this session; run it yourself")
        elif runs[-1][0] < written:
            problems.append(f"the acceptance command of point {point} (`{command}`) last ran before the last "
                            "implementer or fixer returned; run it again yourself")
        elif runs[-1][1]:
            problems.append(f"the acceptance command of point {point} (`{command}`) failed in its last run: the point "
                            "is not delivered, record it as a review finding `unmet`")
    return problems


def section_stats(body):
    refinement = bullets(section_text(body, "Refinement"))
    review = review_lines(body)
    findings = [line for line in review if not NO_FINDINGS.search(line)]
    return {"refinement": len([line for line in refinement if DECISION_WORDS.search(line)]),
            "adopted": len([line for line in refinement if re.search(r"\badopted\b|übernommen", line, re.IGNORECASE)]),
            "review": len(findings),
            "fixed": len([line for line in findings if re.search(r"\bfixed\b|behoben", line, re.IGNORECASE)]),
            "reviewed": bool(review)}


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
    """A session counts as running while its state says sealed, rewrite or closing and was touched within the hour:
    every tool call of the session and of its agents writes the clock into it. The LOG names a session by its first
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


# --- the judge in a sealed run (V5) ---------------------------------------------
JUDGE_MARK = "You judge the final message"


def judge_record_path(paths):
    return posixpath.join(paths.state_dir, "judge-suspended.json")


def settings_files(paths):
    return [posixpath.join(paths.root, ".claude", name) for name in ("settings.json", "settings.local.json")]


def git_keeps(paths, path):
    """Whether a commit could carry the file: tracked, or untracked and not ignored (`git add -A` would take it).
    Outside a repository, or ignored, it is the user's alone."""
    try:
        code = subprocess.run(["git", "-C", paths.root, "check-ignore", "-q", path], capture_output=True,
                              timeout=10).returncode
    except (OSError, subprocess.SubprocessError):
        return False
    return code == 1


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
        with open(judge_record_path(paths), encoding="utf-8") as handle:
            record = json.load(handle)
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def suspend_judge(paths, session_id):
    """A sealed run has no judge (ENTSCHEIDUNGEN.md, Stop-Hook; luradb-web-client 2026-11-06: 24 verdicts on
    three-line waits the judge's own exceptions let through). Claude Code picks up hook edits in the settings files
    during a session; the judge entry is moved into a record and put back when the run ends. A settings file that
    git tracks is left alone: its change would end up in a commit. True when the judge is suspended now."""
    record = load_judge_record(paths)
    if record and record.get("entries"):
        sessions = set(record.get("sessions") or [])
        sessions.add(session_id or "")
        record["sessions"] = sorted(sessions)
        _save_json(judge_record_path(paths), record)
        return True
    entries, changes = [], []
    for path in settings_files(paths):
        if not os.path.isfile(path) or git_keeps(paths, path):
            continue
        settings, raw = load_settings(path)
        hooks = settings.get("hooks") if isinstance(settings, dict) else None
        stop = hooks.get("Stop") if isinstance(hooks, dict) else None
        if not isinstance(stop, list):
            continue
        kept, removed = [], []
        for group in stop:
            members = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(members, list) or not any(is_judge(hook) for hook in members):
                kept.append(group)
                continue
            removed += [hook for hook in members if is_judge(hook)]
            rest = [hook for hook in members if not is_judge(hook)]
            if rest:
                kept.append(dict(group, hooks=rest))
        if not removed:
            continue
        if kept:
            hooks["Stop"] = kept
        else:
            del hooks["Stop"]
        entries += [{"file": path, "hook": hook} for hook in removed]
        changes.append((path, settings, raw))
    if not entries:
        return False
    # The record first: a judge taken out is never without a way back.
    if not _save_json(judge_record_path(paths), {"sessions": [session_id or ""], "entries": entries}):
        return False
    for path, settings, raw in changes:
        try:
            save_settings(path, settings, raw)
        except OSError:
            pass
    return True


def resume_judge(paths, session_id, force=False):
    """Puts the suspended judge back once no sealed run needs it away. True when it was put back."""
    record = load_judge_record(paths)
    if not record:
        return False
    # A session that no longer runs needs the judge away no more (a crashed run would keep it out for good).
    sessions = [s for s in (record.get("sessions") or []) if s != (session_id or "") and session_live(paths, s)]
    if sessions and not force:
        record["sessions"] = sessions
        _save_json(judge_record_path(paths), record)
        return False
    left = []
    for entry in record.get("entries") or []:
        path, hook = entry.get("file"), entry.get("hook")
        if not path or not is_judge(hook):
            continue
        settings, raw = load_settings(path) if os.path.isfile(path) else ({}, "")
        if not isinstance(settings, dict) or not isinstance(settings.setdefault("hooks", {}), dict):
            left.append(entry)  # unreadable now: kept in the record for the next try
            continue
        stop = settings["hooks"].setdefault("Stop", [])
        if not isinstance(stop, list):
            left.append(entry)
            continue
        if not any(isinstance(g, dict) and hook in (g.get("hooks") or []) for g in stop):
            stop.append({"hooks": [hook]})
            try:
                os.makedirs(posixpath.dirname(path), exist_ok=True)
                save_settings(path, settings, raw)
            except OSError:
                left.append(entry)
    if left:
        _save_json(judge_record_path(paths), {"sessions": [], "entries": left})
        return False
    try:
        os.remove(judge_record_path(paths))
    except OSError:
        pass
    return True


def _save_json(path, data):
    try:
        os.makedirs(posixpath.dirname(path), exist_ok=True)
        write_atomically(path, json.dumps(data, indent=1, ensure_ascii=False))
        return True
    except OSError:
        return False


# --- the closing message (V1) ----------------------------------------------------
STAMP_LINE = re.compile(r"^stamp:\s*\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}", re.IGNORECASE)


def normalized_lines(text):
    """A message as compared with the rendered closing message: whitespace, bold markers, code fences and the stamp
    line do not count."""
    lines = []
    for line in (text or "").splitlines():
        line = " ".join(line.replace("**", "").split())
        if line and not line.startswith("```") and not STAMP_LINE.match(line):
            lines.append(line)
    return lines


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


def ci_state(body):
    """The state word of the docket's last `CI: <state> <hash>` line."""
    words = re.findall(r"\bCI:\s*\**`?([A-Za-zäöüÄÖÜ]+)", body or "")
    if not words:
        return ""
    word = words[-1].lower()
    if word in ("red", "rot", "failure", "failed", "fail", "rejected"):
        return "red"
    if word in ("green", "grün", "success", "passed", "ok", "pass"):
        return "green"
    return ""


def inbox_notes(paths, spec_id, since=""):
    """The times of the user's messages left in the inbox for the spec, those since `since` (local
    "YYYY-MM-DD HH:MM", the inbox's own clock) when given."""
    found = re.findall(r"^##\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})",
                       read_text(posixpath.join(paths.inbox, f"{spec_id}.md")), re.MULTILINE)
    return [moment for day, moment in found if f"{day} {moment:0>5}" >= since]


def sealed_local(paths, spec_id):
    """The local time of the spec's last sealing, as the inbox writes its headers."""
    stamps = [log_time(line) for line in log_lines(paths, spec_id) if " sealed " in line]
    stamps = [s for s in stamps if s]
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(max(stamps))) if stamps else ""


def spec_facts(paths, spec_id):
    body = read_text(paths.docket_file(spec_id))
    counts = point_counts(body) if body else {"x": 0, "~": 0, " ": 0}
    points = counts["x"] + counts["~"] + counts[" "]
    facts = {"id": spec_id, "title": spec_title(paths, spec_id) or spec_id, "body": body,
             "x": counts["x"], "n": points, "complete": points > 0 and not counts[" "],
             "deviations": deviation_points(body),
             "ci": ci_state(body), "notes": inbox_notes(paths, spec_id, sealed_local(paths, spec_id)),
             "inbox": bullets(section_text(body, "Inbox"))}
    facts.update(terminal_section(paths, spec_id))
    facts.update(section_stats(body))
    return facts


# What the closing message shows of a Terminal state is never cut: a line longer than this is refused when the
# block is written (stop.py), where the orchestrator can still shorten it. luradb-web-client 2026-11-10: six lines
# of the closing message ended in "…", among them the one instruction the user had to follow.
TERMINAL_LIMITS = {"result": 200, "reason": 300, "user": 300, "decision": 100, "why": 200, "repaired": 160,
                   "watch": 120}


def _sentence(text):
    """A docket line as a sentence of the message: one line, the first letter upper case, no closing period."""
    text = " ".join(str(text).split()).rstrip(" .;")
    return text[:1].upper() + text[1:] if text else ""


def _short(text, limit=60):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:(") + " …"


def _end(text):
    return text if text.endswith(("…", "?", "!")) else text + "."


def _unique(items):
    """Each sentence once, in order; the same sentence in other spacing or case is the same sentence (experiment
    12: the same decision stood twice, once per spec)."""
    seen, out = set(), []
    for item in items:
        sentence = _sentence(item)
        if sentence and sentence.lower() not in seen:
            seen.add(sentence.lower())
            out.append(sentence)
    return out


def _names(paths, titles):
    """Titles in quotes as a list in a sentence: „A“, „B“ und „C“."""
    names = [quoted(paths, title) for title in titles]
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + f" {say(paths, 'and')} " + names[-1]


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


def resumable(paths, facts, outcomes):
    """The specs `/docket:implement` takes up again, in series order: the ones that stopped and the ones not
    started, each only when its predecessors are done or come before it in that same call."""
    chosen = []
    for f in facts:
        kind = outcomes.get(f["id"], "")
        state = frontmatter(paths.spec_file(f["id"]) or "").get("state", "").lower()
        if kind == "blocked" or (not kind and state not in ("done", "superseded")):
            chosen.append(f)
    while chosen:
        waiting = {spec for spec, _ in open_predecessors(paths, [f["id"] for f in chosen])}
        if not waiting:
            break
        chosen = [f for f in chosen if f["id"] not in waiting]
    return chosen


def run_proposals(paths, facts):
    """The proposals of a run in the order of its specs, each once: what to do, why, why the run did not do it,
    and the title of the spec it came from. A `decision:` line in the form of 0.5.10 and before (one sentence)
    stands as what to do."""
    proposals = []
    for f in facts:
        for item in f["decision"]:
            parts = decision_parts(item)
            what = _sentence(parts["subject"])
            if what and what.lower() not in {p["what"].lower() for p in proposals}:
                proposals.append({"what": what, "why": _sentence(parts["why"]), "outside": _sentence(parts["outside"]),
                                  "title": f["title"]})
    return proposals


def proposals_path(paths):
    return posixpath.join(paths.state_dir, "proposals.json")


def save_proposals(paths, state):
    """The proposals of the run that just ended, whole, for `/docket:spec <number>`: a finished docket is no
    reference outside concept mode, so the spec session gets the proposal from here (user_prompt.py)."""
    facts = [spec_facts(paths, spec_id) for spec_id in series_of(state)]
    try:
        os.makedirs(paths.state_dir, exist_ok=True)
        write_json(proposals_path(paths), {"version": 1, "proposals": run_proposals(paths, facts)})
    except OSError:
        pass


def proposal(paths, number):
    """Proposal `number` (from 1) of the last run, or None."""
    data, _ = read_json(proposals_path(paths))
    found = data.get("proposals") if isinstance(data, dict) else None
    if isinstance(found, list) and 1 <= number <= len(found) and isinstance(found[number - 1], dict):
        return found[number - 1]
    return None


def render_closing(paths, state):
    """The one message of a run, rendered from the dockets, each spec's outcome and git, in blocks with a blank line
    between them: one line for the run (what is implemented, pushed, CI); one line per spec by its title with its
    result, the points that deviate and, where it did not start, the reason; what the user does, as numbered steps,
    or that there is nothing to do; what the run repaired outside the order; the proposals for work after the run,
    numbered, each by what to do and why; each thing to watch by what it is about; what came of the user's
    messages; where the dockets are. What is merely on record (`open:`) stays in the dockets and is not mentioned.

    luradb-web-client 2026-11-11 (two specs, both delivered): every line after the second spec hung in that spec's
    list item, four pieces of advice stood as sentences of up to 400 characters, each with the same bare command,
    one matter stood three times, and the last line counted "15 open points" the user had to ask for: five of them
    the advice again, three of them notes, five of them things only a question would have brought to light.

    luradb-web-client 2026-11-10 (four specs, three of them pushed): 29 lines, twelve of them the same sentence,
    six cut with "…", "CI green" above a red run, a spec whose code was pushed called "blocked", a command of bare
    ids, and the instruction not to implement a spec again next to the command that does. So: nothing is cut here
    (TERMINAL_LIMITS), a sentence stands once, an id stands only inside a command whose line names the titles, and
    the CI word is the run's latest."""
    series = series_of(state)
    outcomes = state.get("outcomes") or {}
    facts = [spec_facts(paths, spec_id) for spec_id in series]
    kind = lambda f: outcomes.get(f["id"], "")
    many = len(series) > 1
    git = repo_facts(paths)
    pushed = bool(git) and bool(git["upstream"]) and git["ahead"] == 0 and not git["dirty"]
    # Implemented is what the project has: a spec that ended without DELIVERED with every point done and its work
    # pushed is implemented, and the message says so.
    shipped = lambda f: kind(f) == "blocked" and f["complete"] and pushed
    delivered = [f for f in facts if kind(f) == "delivered"]
    done = [f for f in facts if kind(f) == "delivered" or shipped(f)]
    closed = [f for f in facts if kind(f)]
    ended = next((f for f in facts if kind(f) in ("blocked", "dissent")), None)
    stopped = bool(state.get("stop_requested")) and len(delivered) < len(series)
    if stopped:
        head = say(paths, "head_stopped", d=len(done), n=len(series))
    elif ended is not None:
        # The series went on past the spec that ended so: the run was not stopped by it.
        went_on = any(kind(f) for f in facts[facts.index(ended) + 1:])
        head = say(paths, ("head_past_" if went_on else "head_") + kind(ended), title=ended["title"],
                   d=len(done), n=len(series))
    elif not many and delivered:
        head = say(paths, "head_one", title=quoted(paths, delivered[0]["title"]))
    else:
        head = say(paths, "head_done", d=len(done), n=len(series))
    parts = []
    if git and done:
        if not git["upstream"]:
            parts.append(say(paths, "no_upstream", branch=git["branch"]))
        elif git["ahead"]:
            parts.append(say(paths, "not_pushed_one" if git["ahead"] == 1 else "not_pushed", k=git["ahead"],
                             branch=git["branch"]))
        else:
            parts.append(say(paths, "pushed", branch=git["branch"]))
    elif git and git["ahead"]:
        parts.append(say(paths, "not_pushed_one" if git["ahead"] == 1 else "not_pushed", k=git["ahead"],
                         branch=git["branch"]))
    # The pushes of a run follow one another on one branch: the CI word of the last spec that has one is the state
    # of what is pushed now, whatever state that spec ended in.
    ci = [f["ci"] for f in closed if f["ci"]]
    if ci:
        parts.append(say(paths, "ci_red" if ci[-1] == "red" else "ci_green"))
    if git.get("dirty"):
        parts.append(say(paths, "dirty"))
    lines = [head + (", " + ", ".join(parts) if parts else "") + "."]

    last = closed[-1] if closed else None
    for f in facts:
        if kind(f):
            key = "spec_blocked_done" if shipped(f) else "spec_" + kind(f)
            line = say(paths, key, title=f["title"], x=f["x"], n=f["n"])
            if f["result"]:
                line += ". " + _sentence(f["result"])
            # A point that deviates is named, not counted behind a number the user has to ask for.
            for point in f["deviations"]:
                line += ". " + say(paths, "deviates", point=_short(point))
            if kind(f) == "blocked" and f["reason"] and f["user"] and not stopped:
                line += f". {say(paths, 'reason')}: " + _sentence(f["reason"])
        else:
            # A spec that did not start says why (luradb-web-client 2026-11-10: "nicht begonnen." and nothing else).
            waits = [quoted(paths, spec_title(paths, p) or p) for _, p in open_predecessors(paths, [f["id"]])]
            if waits:
                line = say(paths, "spec_waits", title=f["title"], on=", ".join(waits))
            elif stopped:
                line = say(paths, "spec_after_stop", title=f["title"])
            elif last is not None and kind(last) in ("blocked", "dissent") and facts.index(f) > facts.index(last):
                line = say(paths, "spec_after_end", title=f["title"], on=quoted(paths, last["title"]))
            else:
                line = say(paths, "spec_not_started", title=f["title"])
        lines.append(_end(f"- {line}"))

    # What the user does: the `user:` lines of each spec, and where a stopped spec has none, what it stopped at.
    groups = []
    for f in facts:
        steps = list(f["user"])
        if kind(f) == "blocked" and f["reason"] and not f["user"] and not stopped:
            steps.append(f["reason"])
        groups.append((f["title"], _unique(steps)))
    counts = Counter(step.lower() for _, steps in groups for step in steps)
    general = _unique(step for _, steps in groups for step in steps if counts[step.lower()] > 1)
    groups = [(title, [step for step in steps if counts[step.lower()] == 1]) for title, steps in groups]
    groups = [(title, steps) for title, steps in groups if steps]
    again = resumable(paths, facts, outcomes) if len(delivered) < len(series) else []
    what = []
    for key, chosen in (("resume_continue", [f for f in again if kind(f)]),
                        ("resume_start", [f for f in again if not kind(f)])):
        if chosen:
            what.append(say(paths, key, titles=_names(paths, [f["title"] for f in chosen])))
    if what:
        general.append(_sentence(say(paths, "resume", what=f" {say(paths, 'and')} ".join(what),
                                     specs=", ".join(f["id"] for f in again))))
    uncommitted = work_dir_dirty(paths)
    commands = records_commands(paths) if uncommitted else None
    if commands:
        general.append(say(paths, "commit_records", add=commands[0], commit=commands[1]))

    proposals = run_proposals(paths, facts)
    repaired = _unique(item for f in facts for item in f["repaired"])
    dissent = []
    for f in facts:
        if kind(f) == "dissent":
            dissent += [(f["title"] + ": " if many else "") + item
                        for item in _unique(([f["reason"]] if f["reason"] else []) + f["dissent"])]
    watch = _unique(watch_parts(item)["subject"] for f in facts for item in f["watch"])

    bold = lambda label: f"**{label}:**"
    blocks = [lines[:1], lines[1:]]
    steps = sum(len(found) for _, found in groups) + len(general)
    if steps == 1 and not (many and groups):
        blocks.append([_end(f"{bold(say(paths, 'must'))} {(groups[0][1] if groups else general)[0]}")])
    elif steps:
        # One list; in a series a line between its parts says which spec a step belongs to.
        blocks.append([bold(say(paths, "must"))])
        number, block = 0, []
        for title, found in groups:
            if many:
                blocks += [block, [say(paths, "must_for", title=quoted(paths, title))]]
                block = []
            for step in found:
                number += 1
                block.append(_end(f"{number}. {step}"))
        if many and groups and general:
            blocks += [block, [say(paths, "must_then")]]
            block = []
        for step in general:
            number += 1
            block.append(_end(f"{number}. {step}"))
        blocks.append(block)
    elif not (dissent or git.get("dirty")):
        # Said, not left to be concluded from what is missing.
        blocks.append([f"{bold(say(paths, 'must'))} {say(paths, 'nothing')}."])

    def listed(key, items):
        label = bold(say(paths, key, k=len(items)))
        return [_end(f"{label} {items[0]}")] if len(items) == 1 else [label] + [_end(f"- {item}") for item in items]

    if repaired:
        blocks.append(listed("repaired", repaired))
    if proposals:
        # Numbered also when there is one: the number is what `/docket:spec` takes.
        blocks.append([bold(say(paths, "proposals", k=len(proposals)))]
                      + [_end(f"{n}. {p['what']}" + (f" – {p['why']}" if p["why"] else ""))
                         for n, p in enumerate(proposals, 1)])
        blocks.append([say(paths, "proposals_how")])
    blocks += [[_end(f"{say(paths, 'dissent_line')}: {item}")] for item in dissent]
    if watch:
        blocks.append(listed("watch", watch))
    for f in facts:
        # The user's messages of this run, each with the Inbox line the orchestrator wrote for it. Where the docket
        # holds fewer lines than there were messages, a line may stand for several: it is shown without a time, and
        # no message is called unhandled on a guess.
        notes = f["notes"]
        answers = f["inbox"][-len(notes):] if notes else []
        if notes and len(answers) == len(notes):
            blocks += [[_end(say(paths, "note", time=moment) + ": " + _sentence(answer))]
                       for moment, answer in zip(notes, answers)]
        elif answers:
            blocks += [[_end(say(paths, "notes_answer") + ": " + sentence)] for sentence in _unique(answers)]
        elif len(notes) == 1:
            blocks.append([_end(say(paths, "note_unhandled", time=notes[0]))])
        elif notes:
            blocks.append([_end(say(paths, "notes_unhandled", k=len(notes), times=", ".join(notes)))])
    # The dockets carry the rest in full (the `open:` lines, the `watch:` lines whole, the evidence): the message
    # says where they are and counts nothing the user would have to ask for.
    blocks.append([say(paths, "log", path=relative_to_root(paths, paths.dockets) + "/"),
                   "stamp: " + time.strftime("%Y-%m-%d %H:%M")])
    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"


def closing_path(paths, session_id):
    if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        session_id = "shared"
    return posixpath.join(paths.state_dir, f"closing-{session_id}.md")


# --- metrics (V6) ------------------------------------------------------------------

def write_metrics(paths, spec_id, transcript_path, kind):
    """metrics/<spec>.json, written by the hook when the spec ends: points, refinement and review counts, agents by
    kind since sealing, implementer and reviewer models, inbox notes. The orchestrator no longer spends a fixer on
    it."""
    body = read_text(paths.docket_file(spec_id))
    sealed = sealed_stamp(paths, spec_id)
    agents, models, reviewers = Counter(), [], []
    for kind_of, _, _, model in agent_runs(transcript_path, sealed):
        agents[kind_of] += 1
        if kind_of == "implementer":
            models.append(model or "inherit")
        elif kind_of == "reviewer":
            reviewers.append(model or "inherit")
    stats = section_stats(body)
    counts = point_counts(body) if body else {"x": 0, "~": 0, " ": 0}
    data = {"spec": spec_id, "title": spec_title(paths, spec_id), "outcome": kind, "sealed": sealed,
            "closed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "points": {"delivered": counts["x"], "deviations": counts["~"], "open": counts[" "]},
            "refinement": {"decided": stats["refinement"], "adopted": stats["adopted"]},
            "review": {"findings": stats["review"], "fixed": stats["fixed"]},
            "agents": dict(agents), "implementer_models": models, "reviewer_models": reviewers,
            "inbox_notes": len(inbox_notes(paths, spec_id))}
    path = posixpath.join(paths.metrics, f"{spec_id}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            old = json.load(handle)
        if isinstance(old, dict):
            old.update(data)
            data = old
    except (OSError, ValueError):
        pass
    _save_json(path, data)


# --- user-only preconditions (V12) --------------------------------------------------
PRECONDITION = re.compile(r"`([^`\n]+)`\s*(?:→|->)\s*(.+?)\s*$")


def preconditions(paths, spec_id):
    """`command` → expected lines of the spec's `## Preconditions` section: what only the user can provide (a key, a
    running server), checked before the session seals."""
    found = []
    for line in bullets(section_text(read_text(paths.spec_file(spec_id)), "Preconditions")):
        match = PRECONDITION.search(line)
        if match and not match.group(1).strip().startswith("<"):  # the template's placeholder is no command
            found.append((match.group(1).strip(), match.group(2).strip().strip("`").strip()))
    return found


def precondition_result(paths, command, expected, timeout=20):
    """(ok, what came back): ok when the command exits 0 and its output contains the expected text (`exit 0` or
    nothing: the exit code alone). The output goes to a temporary file, not a pipe, so a process the command leaves
    behind cannot hold the hook; on timeout the whole process tree is killed. What came back names the exit code or
    that the text was missing, never the output: a precondition may print a key."""
    import tempfile
    bash = shutil.which("bash")
    with tempfile.TemporaryFile() as sink:
        try:
            process = subprocess.Popen([bash, "-c", command] if bash else command, shell=not bash, cwd=paths.root,
                                       stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       start_new_session=os.name != "nt")
        except (OSError, ValueError) as error:
            return False, type(error).__name__
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, timeout=10)
            else:
                try:
                    os.killpg(process.pid, 9)
                except OSError:
                    process.kill()
            return False, f"timeout {timeout} s"
        sink.seek(0)
        output = sink.read(65536).decode("utf-8", errors="replace")
    if code != 0:
        return False, f"exit {code}"
    if expected.lower() not in ("", "exit 0") and expected not in output:
        return False, "exit 0, without that text"
    return True, "exit 0"


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
        return "[docket] " + say(paths, "series_interrupted", items=items, specs=", ".join(rest))
    return "[docket] " + say(paths, "series_running", items=items)
