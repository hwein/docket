"""MessageDisplay hook: the display gate. Checks a message before the user sees it.

In the dialogue modes (concept, spec, init, closed) a message that ends with a stamp line and breaks a
rule of the human register is held back: the screen shows nothing, the Stop hook sends Claude back to
write it again, and only the corrected message reaches the user. A Stop hook alone cannot do this:
it runs after the message is on the screen, so a rewrite shows the message twice (luradb-web-client,
2026-11-05).

- Only the rules of the human register hold a message back (stop.content_problems). A missing or
  stale stamp never does: the message stands, the Stop hook asks for the stamp line alone.
- A message without a stamp line is shown: it may be text between tool calls, which no Stop sees.
- After a refusal every message is shown until a Stop lets one through (record `retry`), so the
  answer to a refusal is never held back and nothing is lost.
- The terminal client streams a message in batches; each batch is held until the last one, then the
  whole message is shown at once. Claude Code Desktop and the SDK send one batch per message.
- The judge's verdict is answered with an empty turn (templates/judge-hook.json), so there is nothing to hide.
  An older acknowledgement line `[docket] noted` is still hidden live; the desktop app renders the transcript's
  original text when a session is opened later (experiment 10), so hiding is not durable and the empty answer is
  the rule. Not when stop.py asked in the same feedback for a held-back message in full: that answer is shown.
- Fails open: any error shows the original text.
Nothing is printed except the hook's JSON answer.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docket_lib import (HIDDEN, SESSION_ID, Paths, answers_judge, buffer_path, load_display, load_state, read_input,
                        save_display)
from stop import GATED_MODES, WATERMARK, content_problems, lines_of


def show(text):
    # ASCII escapes: stdout of a hook on Windows is cp1252, which cannot carry the zero-width space or emoji.
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": text}}))
    return 0


def main():
    hook_input = read_input()
    message_id = str(hook_input.get("message_id") or "")
    if not SESSION_ID.fullmatch(message_id):
        return 0
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    if os.path.isfile(paths.config) and answers_judge(hook_input.get("transcript_path")):
        return show(HIDDEN)  # the acknowledgement of a judge verdict: the user has read the message it answers
    mode = load_state(paths, session_id).get("mode", "none")
    if mode not in GATED_MODES or not os.path.isfile(paths.config):
        return 0
    record = load_display(paths, session_id)
    if record.get("retry"):
        return 0
    final = bool(hook_input.get("final"))
    buffer = buffer_path(paths, session_id, message_id)
    try:
        os.makedirs(paths.state_dir, exist_ok=True)
        with open(buffer, "a", encoding="utf-8", newline="") as handle:
            handle.write(hook_input.get("delta") or "")
    except OSError:
        return 0
    if not final:
        if message_id not in record["pending"]:
            record["pending"][message_id] = time.time()
            save_display(paths, session_id, record)
        return show(HIDDEN)
    try:
        with open(buffer, encoding="utf-8") as handle:
            text = handle.read()
        os.remove(buffer)
    except OSError:
        return 0
    record = load_display(paths, session_id)
    record["pending"].pop(message_id, None)
    lines = lines_of(text)
    problems = content_problems(text, mode) if lines and WATERMARK.match(lines[-1]) else []
    if problems:
        record["hidden"].append({"at": time.time(), "problems": problems})
        save_display(paths, session_id, record)
        return show(HIDDEN)
    save_display(paths, session_id, record)
    return show(text)  # the whole message: earlier batches were held back


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # fail open: the original text is shown
        sys.exit(0)
