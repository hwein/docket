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
- In a run (sealed, rewrite) every text of the orchestrator is replaced by the hook's status line (where the
  series stands, in the project's language), and only when that line differs from the one shown last; else the
  text is hidden (luradb-web-client 2026-11-06: 105 status lines, 81 of them without news).
- In closing only the closing message the hook rendered is shown; another text is hidden and the Stop hook asks
  for the rendered one; at the third try the gate shows the rendered text itself.
- The judge's verdict is answered with an empty turn (templates/judge-hook.json), so there is nothing to hide.
  Where the harness then demands a visible answer (Claude Code Desktop: "no visible output"), that answer is the
  line `[docket] noted` and is hidden, as is a bare stamp or `Blocker unverändert` after a verdict
  (luradb-web-client 2026-11-10: six such lines reached the user). The desktop app renders the transcript's
  original text when a session is opened later (experiment 10), so hiding is not durable and the empty answer
  stays the rule. Not when stop.py asked in the same feedback for a held-back message in full: that answer is shown.
- Fails open: any error shows the original text.
Nothing is printed except the hook's JSON answer.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docket_lib import (HIDDEN, SESSION_ID, Paths, answers_judge, buffer_path, closing_path, load_display, load_state,
                        normalized_lines, read_input, read_text, save_display, status_line)
from stop import CLOSING_TRIES, GATED_MODES, WATERMARK, box_problems, content_problems, is_judge_ack, is_stub, lines_of


def show(text):
    # ASCII escapes in the JSON: safe whatever encoding the client assumes (docket_lib sets UTF-8 for the streams).
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": text}}))
    return 0


def main():
    hook_input = read_input()
    message_id = str(hook_input.get("message_id") or "")
    if not SESSION_ID.fullmatch(message_id):
        return 0
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    mode = state.get("mode", "none")
    # The acknowledgement of a judge verdict: the user has read the message it answers. Not in closing, where the
    # closing branch alone decides (review 2026-11-06: a verdict next to the closing order hid the closing message).
    if mode != "closing" and os.path.isfile(paths.config):
        answered = answers_judge(hook_input.get("transcript_path"))
        if answered == "direct":
            return show(HIDDEN)
        # An answer the harness forced after a turn that rightly ended without text (a judge verdict, a wait for
        # agents): the acknowledgement line is never shown, and after a verdict neither is a bare stamp or
        # `Blocker unverändert`. Only a whole message is judged so: one batch, final.
        whole = hook_input.get("delta") or ""
        # Not in a run: there every text becomes the status line below.
        if (mode not in ("sealed", "rewrite") and hook_input.get("final")
                and not os.path.isfile(buffer_path(paths, session_id, message_id))
                and (is_judge_ack(whole) or (answered == "nudged" and is_stub(whole)))):
            return show(HIDDEN)
    if mode not in GATED_MODES or not os.path.isfile(paths.config):
        return 0
    record = load_display(paths, session_id)
    sealed = mode in ("sealed", "rewrite", "closing")
    if record.get("retry") and not sealed:
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
    if mode in ("sealed", "rewrite"):
        # A run speaks to the user once, at its end. Its texts (answers to the harness, notes between tool calls) are
        # replaced by the status line when it carries news, else hidden.
        line = status_line(paths, state)
        if record.get("last_status") == line:
            save_display(paths, session_id, record)
            return show(HIDDEN)
        record["last_status"] = line
        save_display(paths, session_id, record)
        return show(line)
    if mode == "closing":
        info = state.get("closing") or {}
        rendered = read_text(info.get("file") or closing_path(paths, session_id))
        if record.get("closing_shown"):
            save_display(paths, session_id, record)
            return show(HIDDEN)  # the user has the closing message already: once
        if rendered and normalized_lines(text) == normalized_lines(rendered):
            record["closing_shown"] = True
            save_display(paths, session_id, record)
            return show(text)
        if rendered and int(info.get("attempts", 0)) >= CLOSING_TRIES - 1:
            record["closing_shown"] = True
            save_display(paths, session_id, record)
            return show(rendered.rstrip())  # the last try: the user gets the rendered message
        save_display(paths, session_id, record)
        return show(HIDDEN)
    problems = (content_problems(text, mode, paths) + box_problems(text, hook_input.get("transcript_path"))
                if lines and WATERMARK.match(lines[-1]) else [])
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
