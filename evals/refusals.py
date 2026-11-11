"""Counts the blocking feedbacks of docket's hooks in a session transcript that begin with none of the three
identifiers (REWORK-SPEC, Festlegung „Kennung der Rückmeldungen“): `[docket rule: <key>]`, `[docket gate]`,
`[docket state]`. 0 is the criterion "no refusal outside the five".

    python evals/refusals.py <transcript.jsonl>

A docket hook is known by its command: `hooks/<name>.py` of the plugin. Blocking feedbacks are read where Claude Code
writes them into the transcript:
- PreToolUse and PostToolUse: the tool result with `is_error`, `<Event>:<Tool> hook error: [<command>]: <text>`;
  an attachment `hook_blocking_error` (`blockingError`, `command`) the same way;
- Stop: the entry `system`/`stop_hook_summary`, one string `[<command>]: <text>` per hook in `hookErrors`; only where a
  transcript has no such entry, the meta prompt `Stop hook feedback:` that repeats it.
A hook's success, its additional context and a summary without errors are no feedback. Prints one line per docket
hook with its count, then the count as the last line; no feedback text.
"""
import json
import re
import sys

DOCKET_HOOK = re.compile(r"hooks[/\\](pre_tool_use|post_tool_use|stop|subagent_stop|message_display|user_prompt|"
                         r"session_start)\.py\b")
IDENTIFIERS = ("[docket rule: ", "[docket gate]", "[docket state]")
TOOL_ERROR = re.compile(r"^(?:Error: )?(PreToolUse|PostToolUse)(?::[^\s]*)? hook error: \[(.*?)\]: (.*)$", re.DOTALL)
STOP_FEEDBACK = "Stop hook feedback:"


def split_feedback(text):
    """(command, text) of a feedback `[<command>]: <text>`; the command holds no `]: `."""
    match = re.match(r"^\s*\[(.*?)\]: (.*)$", text or "", re.DOTALL)
    return (match.group(1), match.group(2)) if match else ("", text or "")


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def feedbacks(entries):
    """(command, text) of every blocking feedback of any hook, in order."""
    found, summaries, meta = [], [], []
    for entry in entries:
        kind = entry.get("type")
        if kind == "system" and entry.get("subtype") == "stop_hook_summary":
            summaries += [split_feedback(e) for e in entry.get("hookErrors") or [] if isinstance(e, str)]
            continue
        attachment = entry.get("attachment")
        if kind == "attachment" and isinstance(attachment, dict) and attachment.get("type") == "hook_blocking_error":
            error = attachment.get("blockingError")
            if isinstance(error, dict):
                found.append((str(error.get("command") or attachment.get("command") or ""),
                              str(error.get("blockingError") or "")))
            else:
                found.append((str(attachment.get("command") or ""), str(error or "")))
            continue
        message = entry.get("message")
        if kind != "user" or not isinstance(message, dict):
            continue
        content = message.get("content")
        if entry.get("isMeta") and content_text(content).startswith(STOP_FEEDBACK):
            meta.append(split_feedback(content_text(content)[len(STOP_FEEDBACK):].lstrip("\n")))
            continue
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                match = TOOL_ERROR.match(content_text(block.get("content")))
                if match:
                    found.append((match.group(2), match.group(3)))
    return found + (summaries or meta)


def read_entries(path):
    entries = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def main(argv):
    if len(argv) != 2:
        print("usage: python evals/refusals.py <transcript.jsonl>", file=sys.stderr)
        return 2
    counts = {}
    for command, text in feedbacks(read_entries(argv[1])):
        hook = DOCKET_HOOK.search(command)
        if hook and not text.lstrip().startswith(IDENTIFIERS):
            counts[hook.group(1)] = counts.get(hook.group(1), 0) + 1
    for name in sorted(counts):
        print(f"{name} {counts[name]}")
    print(sum(counts.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
