"""Shared helpers for the docket hooks: hook input, paths, session state, LOG, frontmatter.

Every hook fails open: unreadable input or missing files never block Claude Code.
Nothing here prints to stdout; stdout of a hook enters the model's context.
"""
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time

SESSION_ID = re.compile(r"[A-Za-z0-9_-]+")
SPEC_ID = re.compile(r"\bs-[0-9a-f]{8}\b")
CONCEPT_ID = re.compile(r"\bc-[0-9a-f]{8}\b")
MODES = ("none", "concept", "spec", "init", "sealed", "rewrite", "closed")
STATE_VERSION = 1


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
        url = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
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


SAY = {
    "de": {
        "preparing": "Vorbereitung: Spec wird gelesen und gegen den Code geprüft",
        "rewrite": "Spec wird vom Konzept-Agenten umgeschrieben",
        "points": "{x} von {n} Punkten geliefert, {d} abweichend, {o} offen",
        "running": "Lauf läuft",
        "delivered": "geliefert {x}/{n}, Lauf beendet, Sitzung geschlossen",
        "blocked": "blockiert bei {x}/{n}, Lauf beendet; /docket:implement {spec} nimmt ihn wieder auf",
        "dissent": "Dissens bei {x}/{n}, Lauf beendet; die Entscheidung steht in der Nachricht darüber",
        "next": "Nächste Spec im Zustand ready: {spec} ({title}), in einer neuen Sitzung mit /docket:implement {spec}",
        "none_ready": "Keine weitere Spec im Zustand ready",
        "continue": "Weiter in dieser Sitzung mit {spec} ({title}); danach noch {rest}",
        "nothing_more": "nichts weiter",
        "dropped": "Nicht gestartet: {specs}; dafür einen neuen Befehl geben",
    },
    "en": {
        "preparing": "preparing: the spec is read and checked against the code",
        "rewrite": "the spec is being rewritten by the conceptor",
        "points": "{x} of {n} points delivered, {d} deviating, {o} open",
        "running": "run in progress",
        "delivered": "delivered {x}/{n}, run finished, session closed",
        "blocked": "blocked at {x}/{n}, run finished; /docket:implement {spec} resumes it",
        "dissent": "dissent at {x}/{n}, run finished; the decision is in the message above",
        "next": "next spec in state ready: {spec} ({title}), in a new session with /docket:implement {spec}",
        "none_ready": "no further spec in state ready",
        "continue": "continuing in this session with {spec} ({title}); after it {rest}",
        "nothing_more": "nothing more",
        "dropped": "not started: {specs}; give a new command for them",
    },
}


def say(paths, key, **values):
    return SAY[language_code(paths) or "en"][key].format(**values)


def spec_title(paths, spec_id):
    path = paths.spec_file(spec_id)
    return frontmatter(path).get("title", "") if path else ""


def spec_records(paths):
    """Frontmatter of every spec (id, title, state, follows, concept, created), by path."""
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
                                  "state": fields.get("state", "").lower(),
                                  "follows": [s.lower() for s in SPEC_ID.findall(fields.get("follows", ""))],
                                  "concept": (CONCEPT_ID.search(fields.get("concept", "")) or [None])[0]
                                  if CONCEPT_ID.search(fields.get("concept", "")) else "",
                                  "created": fields.get("created", "")})
    return found


def in_order(records):
    """Implementation order: a spec after every spec it follows; ties by created date, then by path."""
    by_id = {r["id"]: r for r in records}
    remaining = sorted(records, key=lambda r: (r["created"], r["id"]))
    ordered, placed = [], set()
    while remaining:
        free = [r for r in remaining if all(f not in by_id or f in placed for f in r["follows"])]
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
    """For a run of the given specs in that order: every `follows:` spec that is neither done nor earlier in the
    run, as (spec, predecessor)."""
    states = {r["id"]: r for r in spec_records(paths)}
    missing, earlier = [], set()
    for spec_id in spec_ids:
        record = states.get(spec_id.lower())
        for predecessor in record["follows"] if record else []:
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


def status_line(paths, state):
    """What the user sees instead of an orchestrator's message in a sealed run: title, points, clock."""
    spec_id = state.get("spec", "")
    counts = docket_counts(paths, spec_id)
    if state.get("mode") == "rewrite":
        progress = say(paths, "rewrite")
    elif counts is None:
        progress = say(paths, "preparing")
    else:
        progress = say(paths, "points", x=counts["x"], n=counts["x"] + counts["~"] + counts[" "], d=counts["~"],
                       o=counts[" "])
    title = spec_title(paths, spec_id) or spec_id
    return f"[docket] {title}: {progress} · {say(paths, 'running')} · {state.get('clock', '')}".rstrip(" ·")


def closing_line(paths, spec_id, kind, queue=(), continued=""):
    """The hook's own line after a terminal message: what happened and what comes next, in the project's language.
    `continued` is the queued spec the session goes on with; `queue` the ids still waiting (or dropped)."""
    counts = docket_counts(paths, spec_id) or {"x": 0, "~": 0, " ": 0}
    title = spec_title(paths, spec_id) or spec_id
    outcome = say(paths, kind, x=counts["x"], n=counts["x"] + counts["~"] + counts[" "], spec=spec_id)
    if continued:
        rest = ", ".join(queue) if queue else say(paths, "nothing_more")
        tail = say(paths, "continue", spec=continued, title=spec_title(paths, continued) or continued, rest=rest)
    elif queue:
        tail = say(paths, "dropped", specs=", ".join(queue))
    else:
        following = [(s, t) for s, t in ready_specs(paths) if s.lower() != spec_id.lower()]
        tail = say(paths, "next", spec=following[0][0], title=following[0][1]) if following else say(paths, "none_ready")
    return f"[docket] {title}: {outcome}. {tail}."


FILE_LINE = re.compile(r"(?<![\w/:])((?:[\w.-]+/)*[\w.-]+\.[a-z]{1,5}):(\d{1,6})\b", re.IGNORECASE)


def missing_citations(text, paths):
    """Every `path/file.ext:line` in the text that does not exist (relative to the repo root or the work dir) or
    has fewer lines, at most six."""
    missing = []
    for match in FILE_LINE.finditer(text):
        rel, line = match.group(1), int(match.group(2))
        candidates = [posixpath.join(paths.root, rel), posixpath.join(paths.work, rel), rel]
        found = next((c for c in candidates if os.path.isfile(c)), None)
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


def load_state(paths, session_id):
    try:
        with open(state_path(paths, session_id), encoding="utf-8-sig") as handle:
            state = json.load(handle)
        if isinstance(state, dict):
            state.setdefault("mode", "none")
            return state
    except (OSError, ValueError):
        pass
    return {"version": STATE_VERSION, "mode": "none", "spec": "", "degraded": False, "read": []}


def save_state(paths, session_id, state):
    """Atomic: SessionStart and UserPromptSubmit can run at the same moment on a session's first prompt;
    two writers on one file left a truncated JSON once, and a state that does not parse reads as mode none."""
    try:
        os.makedirs(paths.state_dir, exist_ok=True)
        target = state_path(paths, session_id)
        temp = f"{target}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, indent=1)
        os.replace(temp, target)
        prune_states(paths.state_dir)
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
    try:
        with open(display_path(paths, session_id), encoding="utf-8-sig") as handle:
            record = json.load(handle)
        if isinstance(record, dict):
            record.setdefault("hidden", [])
            record.setdefault("pending", {})
            record.setdefault("retry", False)
            return record
    except (OSError, ValueError):
        pass
    return {"hidden": [], "pending": {}, "retry": False}


def save_display(paths, session_id, record):
    try:
        os.makedirs(paths.state_dir, exist_ok=True)
        target = display_path(paths, session_id)
        temp = f"{target}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, indent=1)
        os.replace(temp, target)
    except OSError:
        pass


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
            if now - os.path.getmtime(path) > max_age_days * 86400:
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
        return time.mktime(time.strptime(line[:20], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
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

def clock_line(paths, session_id, state=None):
    """Saves the current time in the session state and returns the context line that carries it."""
    state = state if state is not None else load_state(paths, session_id)
    state["clock"] = time.strftime("%Y-%m-%d %H:%M")
    save_state(paths, session_id, state)
    return f"[docket] clock: {state['clock']}"


# --- transcript --------------------------------------------------------------
HARNESS_BLOCK = re.compile(r"<(system-reminder|task-notification|side-sessions-event|cross-session-message)\b[^>]*>.*?</\1>",
                           re.DOTALL)
STOP_FEEDBACK = "Stop hook feedback"


def strip_harness_blocks(prompt):
    return HARNESS_BLOCK.sub("", prompt)


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


def last_assistant_text(transcript_path):
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
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        found = text_of(message.get("content")) if isinstance(message, dict) else None
        if found is not None:
            text = found
    return text


def message_text(hook_input, transcript_key="transcript_path"):
    text = text_of(hook_input.get("last_assistant_message"))
    if text is not None:
        return text
    return last_assistant_text(hook_input.get(transcript_key))


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
        log_event(paths, session_id, doc_id, "judge", reason[:120])
        for tag in judge_tags(reason) or ["unnamed"]:
            record_violation(paths, doc_id, f"judge {tag}")


def answers_judge(transcript_path):
    """True when the newest text of the user's side is the judge's feedback and no request to write a held-back
    message again: the message being displayed is the acknowledgement, and the user has already read the
    message it answers."""
    # Claude Code writes each Stop hook's feedback as an entry of its own, the slower judge last: the whole block of
    # feedback since the last text is read, or stop.py's request for a full rewrite is missed and the rewrite hidden
    # (legacy session 2026-11-05, entries 522 and 523).
    judged = False
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
        if not content.lstrip().startswith(STOP_FEEDBACK):
            break  # a prompt of the user ends the block
        if HELD_BACK in content:
            return False
        judged = judged or JUDGE_PROMPT in content
    return judged


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


def set_context(paths, doc_id):
    """Where refusals are counted: the metrics file of the document the session works on."""
    CONTEXT.update({"paths": paths, "doc_id": doc_id or ""})


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
    """Boxes of a docket: delivered [x], deviations [~], open [ ] (backticked ones ignored)."""
    return {mark: len(re.findall(r"(?<!`)\[" + re.escape(mark) + r"\](?!`)", body)) for mark in ("x", "~", " ")}


def refuse(message):
    print(message, file=sys.stderr)
    if CONTEXT.get("paths") is not None:
        record_violation(CONTEXT["paths"], CONTEXT.get("doc_id", ""), message)
    return 2
