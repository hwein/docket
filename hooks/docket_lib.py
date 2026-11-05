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
                    fields[key.strip().lower()] = value.strip().strip("\"'")
    except OSError:
        pass
    return fields


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
    try:
        os.makedirs(paths.state_dir, exist_ok=True)
        with open(state_path(paths, session_id), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, indent=1)
        prune_states(paths.state_dir)
    except OSError:
        pass


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


def refuse(message):
    print(message, file=sys.stderr)
    return 2
