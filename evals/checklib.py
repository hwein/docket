"""What the cases' check.py scripts read: the fixture after the run (repository, docket, LOG), the stream-json output
of `claude -p` and the session transcripts (main session and subagents).

A check prints one line per criterion: `pass|fail <class> <name>`, the class `result` (repository, check, artifact,
last message) or `docket` (docket and LOG).
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
TERMINAL = re.compile(r"^[\s\-*`]*state[`*]*\s*:[\s`*]*(DELIVERED|BLOCKED|DISSENT)\b", re.IGNORECASE | re.MULTILINE)
DEVIATION_MARK = re.compile(r"(?<!`)\[~\](?!`)")


class Run:
    def __init__(self, fixture, stand, stream=None, transcript=None):
        self.fixture = fixture
        self.stand = stand
        self.stream = stream
        self.transcript = transcript
        self.lines = []

    @classmethod
    def from_args(cls, argv=None):
        parser = argparse.ArgumentParser()
        parser.add_argument("--fixture", required=True)
        parser.add_argument("--stand", required=True)
        parser.add_argument("--stream", help="stream-json output of claude -p")
        parser.add_argument("--transcript", help="folder with the session's transcript files (.jsonl)")
        args = parser.parse_args(argv)
        return cls(args.fixture, args.stand, args.stream, args.transcript)

    # --- output ---------------------------------------------------------------------------------------------
    def criterion(self, cls, name, ok):
        self.lines.append(f"{'pass' if ok else 'fail'} {cls} {name}")

    def report(self):
        print("\n".join(self.lines))
        return 0

    @property
    def plugin(self):
        return self.stand != "none"

    # --- repository -----------------------------------------------------------------------------------------
    def sh(self, command, timeout=120):
        """(exit code, stdout and stderr) of a shell command in the fixture, through bash like the docket hooks."""
        bash = shutil.which("bash")
        try:
            done = subprocess.run([bash, "-c", command] if bash else command, shell=not bash, cwd=self.fixture,
                                  capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return 1, str(error)
        return done.returncode, (done.stdout + done.stderr).decode("utf-8", "replace")

    def accepts(self, command, expected):
        code, output = self.sh(command)
        return code == 0 and expected in output

    def check_green(self):
        code, output = self.sh("python -m unittest discover -s tests")
        return code == 0 and re.search(r"^OK\b", output, re.MULTILINE) is not None

    def git(self, *args, git_dir=None):
        command = ["git"] + (["--git-dir", git_dir] if git_dir else []) + list(args)
        done = subprocess.run(command, cwd=self.fixture, capture_output=True, stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout.decode("utf-8", "replace")

    def origin_has(self, branch, path):
        code, _ = self.git("cat-file", "-e", f"{branch}:{path}",
                           git_dir=os.path.join(self.fixture, ".remote", "origin.git"))
        return code == 0

    def file(self, rel):
        try:
            with open(os.path.join(self.fixture, *rel.split("/")), encoding="utf-8-sig", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    # --- docket ---------------------------------------------------------------------------------------------
    def docket(self, spec_id):
        found = sorted(glob.glob(os.path.join(self.fixture, "docket", "dockets", "*", f"{spec_id}*.md")))
        return self.file(os.path.relpath(found[0], self.fixture).replace("\\", "/")) if found else None

    @staticmethod
    def terminal_state(docket):
        """The last `state:` of the docket's Terminal state lines, upper case, or ''."""
        found = TERMINAL.findall(docket or "")
        return found[-1].upper() if found else ""

    @staticmethod
    def section(docket, title):
        parts = re.findall(r"^##\s+" + re.escape(title) + r"\b.*?$(.*?)(?=^##\s|\Z)", docket or "",
                           re.MULTILINE | re.DOTALL | re.IGNORECASE)
        return "\n".join(parts)

    def evidence_for(self, docket, point):
        return re.search(rf"\bpoint\s*{point}\b", self.section(docket, "Evidence"), re.IGNORECASE) is not None

    @staticmethod
    def has_deviation(docket):
        return bool(DEVIATION_MARK.search(docket or ""))

    # --- transcript -----------------------------------------------------------------------------------------
    def _entries(self):
        files = [self.stream] if self.stream else []
        if self.transcript and os.path.isdir(self.transcript):
            files += sorted(glob.glob(os.path.join(self.transcript, "**", "*.jsonl"), recursive=True))
        for path in files:
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            entry = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(entry, dict):
                            yield entry
            except OSError:
                continue

    def tool_uses(self):
        """Every tool call of the session and its subagents: [(name, input)], each call once."""
        seen, calls = set(), []
        for entry in self._entries():
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if entry.get("type") != "assistant" or not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    key = block.get("id") or json.dumps(block, sort_keys=True)
                    if key not in seen:
                        seen.add(key)
                        calls.append((block.get("name", ""), block.get("input") or {}))
        return calls

    def last_message(self):
        """The text of the run's last answer: the result event of the stream, else the last assistant text."""
        result, text = None, ""
        if self.stream:
            try:
                with open(self.stream, encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            entry = json.loads(line)
                        except ValueError:
                            continue
                        if entry.get("type") == "result" and isinstance(entry.get("result"), str):
                            result = entry["result"]
                        elif entry.get("type") == "assistant" and not entry.get("parent_tool_use_id"):
                            blocks = (entry.get("message") or {}).get("content") or []
                            texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
                            if any(t.strip() for t in texts):
                                text = "\n".join(texts)
            except OSError:
                pass
        return result if result and result.strip() else text

    def untitled_texts(self, titles):
        """The texts before the closing message that are not one line `<title>: <step>` with a title of the series
        (2026-11-14: 12 of 46 such texts in six runs told the next step without the title, before a tool call)."""
        texts, closing = self.assistant_texts(), self.last_message().strip()
        before = texts[:-1] if texts and texts[-1] == closing else texts
        return [t for t in before if "\n" in t or not any(t.startswith(title + ":") for title in titles)]

    def assistant_texts(self):
        """The texts of the main session's answers in order, from the stream: one entry per assistant message with
        text, subagents left out."""
        texts = []
        if not self.stream:
            return texts
        try:
            with open(self.stream, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("type") != "assistant" or entry.get("parent_tool_use_id"):
                        continue
                    blocks = (entry.get("message") or {}).get("content") or []
                    text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                    if text.strip():
                        texts.append(text.strip())
        except OSError:
            pass
        return texts

    def edited(self, *paths):
        """Whether an Edit or Write call of the session or an agent targeted one of the paths (relative)."""
        wanted = [p.replace("\\", "/").lower() for p in paths]
        for name, data in self.tool_uses():
            if name not in EDIT_TOOLS:
                continue
            target = str(data.get("file_path") or data.get("notebook_path") or "").replace("\\", "/").lower()
            if any(target == p or target.endswith("/" + p) for p in wanted):
                return True
        return False

    def ran(self, tool, pattern):
        """Whether a call of the tool had an input command matching the pattern."""
        return any(name == tool and re.search(pattern, str(data.get("command", "")), re.IGNORECASE)
                   for name, data in self.tool_uses())
