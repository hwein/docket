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


# --- transcript --------------------------------------------------------------

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
