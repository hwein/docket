"""UserPromptSubmit hook.

Switches the session mode on /docket:concept, /docket:spec, /docket:implement <id>, /docket:init,
/docket:stop.
In sealed mode, free text does not reach the model: it is stored in the inbox and the
prompt is blocked (exit 2 shows the notice to the user). Every turn gets a two-line
mode reminder as additional context.
"""
import os
import posixpath
import re
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import (SPEC_ID, Paths, clock_line, config_values, display_path, load_state, lock_holder, log_event,
                        read_input, refuse, save_display, save_state, set_context, set_spec_state, strip_harness_blocks,
                        take_lock, violations_line)

COMMAND = re.compile(r"^/(?P<namespace>docket:)?(?P<name>[a-z]+)\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
GO = re.compile(r"^(go|ok|okay|yes|ja|los|approved|freigabe|freigegeben|do it|write it)\b[.!]?\s*$", re.IGNORECASE)
COMMANDS = ("concept", "spec", "implement", "init", "stop", "status", "register")
# Claude Code has no built-in of these names, so they also count without the namespace; /init and /status do not.
BARE = ("concept", "spec", "implement")

REMINDERS = {
    "concept": ("You are the thinking partner, not the executor. One idea per message, one question at most; "
                "a recommendation, never a list of options. Specs and concepts say what SHOULD be; the reference "
                "docs say what IS."),
    "spec": ("Turn the agreed concept into a spec with measurable done criteria and a reference-update section. "
             "User questions carry a recommendation and are answerable with one word; everything technical you decide."),
    "init": ("Audit first, ask second: evidence from the repo with its source for every question; cards only where "
             "the repo is silent or contradicts itself. Write nothing before the diff is approved."),
    "sealed": ("The spec is sealed. No questions to the user; defaults plus a 'deviation' line in the docket. "
               "Nothing you did not fetch yourself is a fact. The user sees none of your messages during the run "
               "(the hook shows a status line instead), only the terminal message: DOCKET DELIVERED, DOCKET BLOCKED "
               "or DOCKET DISSENT, line 2 the spec's title and the result in one sentence, line 3 what only the user "
               "must do (`Du musst …` / `You must …`) or `Nichts zu tun` / `Nothing to do`, then one line per point "
               "named by its content, nothing about hooks, last line `stamp: YYYY-MM-DD HH:MM <spec-id> "
               "<delivered>/<points>` with the docket's current counts."),
    "rewrite": ("Rewrite mode: only the conceptor agent edits the named spec, and it records the rewrite with date "
                "and reason in the spec."),
    "closed": ("The implementation is closed. Answer only from the docket, shorter when not understood; "
               "propose nothing, start nothing. A new command opens the next mode."),
    "none": "",
}
WATERMARK = ("Every message ends with the line `stamp: YYYY-MM-DD HH:MM`: the time of the latest `[docket] clock:` line "
             "(the hooks give it with every prompt and every tool result; no tool call needed) or fetched from the clock "
             "now, never remembered; the hook refuses the message otherwise. Sent back: do exactly what the hook asks "
             "(the stamp line alone, correction lines, or after a judge verdict an empty answer); repeat a message "
             "only when it says it was held back.")


# A concept command whose order is to write or rework specs (legacy session 2026-11-05: "überarbeite die specs in
# docket-specs" in /docket:concept became a concept file and flags pointing at it).
SPECS = re.compile(r"\bspecs?\b|spezifikation", re.IGNORECASE)
WRITE_ORDER = re.compile(r"\b(?:überarbeit|umschreib|schreib|erstell|formulier|neu\s*fass|anleg|rewrite|write|draft|"
                         r"create|turn\b[^.\n]{0,40}\binto)\w*", re.IGNORECASE)
SPEC_REQUEST_LINE = ("[docket] This order asks for specs: in concept mode the answer is the command `/docket:spec` "
                     "with the whole order, then the end of the turn. Nothing is drafted, put into a concept file or "
                     "flagged for it.")


def asks_for_specs(rest):
    """An order, not a question: "Welche Specs sollten wir schreiben?" is a concept question."""
    rest = (rest or "").strip()
    return bool(rest and not rest.endswith("?") and SPECS.search(rest) and WRITE_ORDER.search(rest))


def ambition():
    return ("[docket] You lead this team. Be ambitious. You are responsible for the docket being true, "
            "not for being finished.")


def main():
    hook_input = read_input()
    prompt = hook_input.get("prompt")
    if not isinstance(prompt, str):
        return 0
    paths = Paths(hook_input)
    session_id = hook_input.get("session_id")
    state = load_state(paths, session_id)
    set_context(paths, state.get("spec", ""))
    text = strip_harness_blocks(prompt).strip()
    if not text:
        return 0  # only harness notifications (task results, session notes): not user text
    # A new turn of the user: what the display gate held back or let through in the last one is settled.
    if os.path.isfile(display_path(paths, session_id)):
        save_display(paths, session_id, {"hidden": [], "pending": {}, "retry": False})
    feedback = ""
    # A command may sit on its own line below a preamble (sessions started by another session).
    command_line = next((line.strip() for line in text.splitlines() if line.strip().startswith("/")), "")
    name, rest = parse_command(command_line)
    if name:
        # The model may expand this one command through the Skill tool; pre_tool_use checks against it.
        state["last_command"] = f"/docket:{name}{rest}".lower().split("\n")[0][:80]
        save_state(paths, session_id, state)

    if name == "implement":
        found = list(dict.fromkeys(SPEC_ID.findall(rest)))
        if not found:
            return refuse("[docket] /docket:implement needs a spec id (s-xxxxxxxx). Run /docket:register to see "
                          "the register.")
        if len(found) > 1:
            # luradb-web-client 2026-11-06: five ids in one call, the first was sealed, four vanished without a word.
            return refuse(f"[docket] /docket:implement takes one spec per run; given: {', '.join(found)}. "
                          f"Start with `/docket:implement {found[0]}`; the next one gets a run of its own in a new "
                          "session once this one is closed.")
        spec_id = found[0]
        if state.get("mode") in ("sealed", "rewrite") and state.get("spec") != spec_id:
            return refuse(f"[docket] This session is sealed on {state.get('spec')}. Finish it or run /docket:stop.")
        holder = lock_holder(paths, spec_id)
        if holder and holder != session_id and held_by_live_session(paths, holder, spec_id):
            return refuse(f"[docket] {spec_id} is sealed in session {holder[:8]}. Finish or /docket:stop it there; "
                          f"the lock is {paths.lock_file(spec_id)}.")
        take_lock(paths, spec_id, session_id)
        state.update({"mode": "sealed", "spec": spec_id, "stop_requested": False})
        save_state(paths, session_id, state)
        log_event(paths, session_id, spec_id, "sealed")
        set_spec_state(paths, spec_id, "in-progress")
        feedback = violations_line(paths, spec_id)
    elif name == "stop":
        if state.get("mode") in ("sealed", "rewrite"):
            state["stop_requested"] = True
            save_state(paths, session_id, state)
            log_event(paths, session_id, state.get("spec", ""), "stop-requested")
    elif name == "init":
        state.update({"mode": "init", "spec": "", "init_approved": False})
        save_state(paths, session_id, state)
    elif name in ("status", "register"):
        pass  # read-only: no mode change
    elif state.get("mode") == "init" and GO.match(text):
        # The diff is approved on the user's one word; only now may init write project files.
        state["init_approved"] = True
        save_state(paths, session_id, state)
        log_event(paths, session_id, "", "init-approved")
    elif name in ("concept", "spec"):
        if state.get("mode") in ("sealed", "rewrite"):
            return refuse(f"[docket] Sealed on {state.get('spec')}: finish it or run /docket:stop first.")
        state.update({"mode": name, "spec": ""})
        save_state(paths, session_id, state)
    elif state.get("mode") in ("sealed", "rewrite") and not command_line:
        store_inbox(paths, state.get("spec", ""), text)
        log_event(paths, session_id, state.get("spec", ""), "inbox")
        return refuse("[docket] Noted for the next checkpoint; the orchestrator reads the inbox between "
                      "points. Use /docket:stop to end the run.")

    mode = state.get("mode", "none")
    lines = []
    if name and name != "init" and mode == "none" and not os.path.isfile(paths.config):
        lines.append(f"[docket] No DOCKET.md at {paths.work}. Run /docket:init to set this project up; "
                     "until then the docket hooks only observe.")
    if mode != "none":
        lines.append(f"[docket] Mode {mode}" + (f", spec {state['spec']}" if state.get("spec") else "")
                     + ". " + REMINDERS.get(mode, ""))
        if mode != "concept":
            lines.append(ambition())  # in concept mode it pushed past the stop (legacy session 2026-11-05)
        language = config_values(paths).get("language", "")
        if language:
            lines.append(f"[docket] Every message to the user is written in {language} (DOCKET.md `language`); "
                         "the hook refuses a message in another language. Agents are briefed in the language of the spec.")
    if name == "concept" and asks_for_specs(rest):
        lines.append(SPEC_REQUEST_LINE)  # with the command that carries the order; it stays in the context
    if mode != "none" or os.path.isfile(paths.config):
        lines.append("[docket] " + WATERMARK)
        lines.append(clock_line(paths, session_id, state))
    if feedback:
        lines.append(feedback)
    if state.get("degraded") and os.path.isfile(paths.config):
        missing = [f for f in paths.required_reading() if f not in state.get("read", [])]
        if missing:
            lines.append("[docket] Required reading still open, read completely with Read before anything else: "
                         + ", ".join(missing))
    if lines:
        print("\n".join(lines))
    return 0


def parse_command(line):
    """(name, rest) of a docket command typed as /docket:<name>, or ("", "") for anything else."""
    found = COMMAND.match(line)
    if not found:
        return "", ""
    name = found.group("name").lower()
    if name not in COMMANDS or not (found.group("namespace") or name in BARE):
        return "", ""
    return name, found.group("rest")


def held_by_live_session(paths, holder, spec_id):
    """A lock counts only while its session's state still says sealed on that spec; stale locks are taken over."""
    other = load_state(paths, holder)
    return other.get("mode") in ("sealed", "rewrite") and other.get("spec") == spec_id


def store_inbox(paths, spec_id, text):
    try:
        os.makedirs(paths.inbox, exist_ok=True)
        with open(posixpath.join(paths.inbox, f"{spec_id or 'session'}.md"), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')}\n{text}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
