"""UserPromptSubmit hook.

Switches the session mode on /concept, /spec, /implement <id>, /docket init, /docket stop.
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
from docket_lib import (SPEC_ID, Paths, load_state, lock_holder, log_event, read_input, refuse, save_state,
                        set_context, take_lock, violations_line)

IMPLEMENT = re.compile(r"^/implement\b(?P<rest>.*)$", re.DOTALL)
GO = re.compile(r"^(go|ok|okay|yes|ja|los|approved|freigabe|freigegeben|do it|write it)\b[.!]?\s*$", re.IGNORECASE)
COMMANDS = {
    "/concept": "concept",
    "/spec": "spec",
    "/docket init": "init",
}

REMINDERS = {
    "concept": ("You are the thinking partner, not the executor. One idea per message, one question at most; "
                "a recommendation, never a list of options. Specs and concepts say what SHOULD be; the reference "
                "docs say what IS."),
    "spec": ("Turn the agreed concept into a spec with measurable done criteria and a reference-update section. "
             "User questions carry a recommendation and are answerable with one word; everything technical you decide."),
    "init": ("Audit first, ask second: evidence from the repo with its source for every question; cards only where "
             "the repo is silent or contradicts itself. Write nothing before the diff is approved."),
    "sealed": ("The spec is sealed. No questions to the user; defaults plus a 'deviation' line in the docket. "
               "Nothing you did not fetch yourself is a fact. The turn ends with exactly one message: "
               "DOCKET DELIVERED, DOCKET BLOCKED or DOCKET DISSENT, last line `stamp: YYYY-MM-DD HH:MM <spec-id> "
               "<delivered>/<points>` with the docket's current counts."),
    "rewrite": ("Rewrite mode: only the conceptor agent edits the named spec, and it records the rewrite with date "
                "and reason in the spec."),
    "closed": ("The implementation is closed. Answer only from the docket, shorter when not understood; "
               "propose nothing, start nothing. A new command opens the next mode."),
    "none": "",
}


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
    feedback = ""
    # A command may sit on its own line below a preamble (sessions started by another session).
    command_line = next((line.strip() for line in text.splitlines() if line.strip().startswith("/")), "")
    lowered = command_line.lower()
    if lowered.startswith(("/concept", "/spec", "/implement", "/docket")):
        # The model may expand this one command through the Skill tool; pre_tool_use checks against it.
        state["last_command"] = lowered.split("\n")[0][:80]
        save_state(paths, session_id, state)

    implement = IMPLEMENT.match(command_line)
    if implement:
        found = SPEC_ID.search(implement.group("rest"))
        if not found:
            return refuse("[docket] /implement needs a spec id (s-xxxxxxxx). Run /docket to see the register.")
        spec_id = found.group(0)
        if state.get("mode") in ("sealed", "rewrite") and state.get("spec") != spec_id:
            return refuse(f"[docket] This session is sealed on {state.get('spec')}. Finish it or run /docket stop.")
        holder = lock_holder(paths, spec_id)
        if holder and holder != session_id and held_by_live_session(paths, holder, spec_id):
            return refuse(f"[docket] {spec_id} is sealed in session {holder[:8]}. Finish or /docket stop it there; "
                          f"the lock is {paths.lock_file(spec_id)}.")
        take_lock(paths, spec_id, session_id)
        state.update({"mode": "sealed", "spec": spec_id, "stop_requested": False})
        save_state(paths, session_id, state)
        log_event(paths, session_id, spec_id, "sealed")
        feedback = violations_line(paths, spec_id)
    elif lowered.startswith("/docket stop"):
        if state.get("mode") in ("sealed", "rewrite"):
            state["stop_requested"] = True
            save_state(paths, session_id, state)
            log_event(paths, session_id, state.get("spec", ""), "stop-requested")
    elif lowered.startswith("/docket"):
        for command, mode in COMMANDS.items():
            if lowered.startswith(command):
                state.update({"mode": mode, "spec": "", "init_approved": False})
                save_state(paths, session_id, state)
    elif state.get("mode") == "init" and GO.match(text):
        # The diff is approved on the user's one word; only now may init write project files.
        state["init_approved"] = True
        save_state(paths, session_id, state)
        log_event(paths, session_id, "", "init-approved")
    else:
        for command, mode in COMMANDS.items():
            if lowered.startswith(command):
                if state.get("mode") in ("sealed", "rewrite"):
                    return refuse(f"[docket] Sealed on {state.get('spec')}: finish it or run /docket stop first.")
                state.update({"mode": mode, "spec": ""})
                save_state(paths, session_id, state)
                break
        else:
            if state.get("mode") in ("sealed", "rewrite") and not command_line:
                store_inbox(paths, state.get("spec", ""), text)
                log_event(paths, session_id, state.get("spec", ""), "inbox")
                return refuse("[docket] Noted for the next checkpoint; the orchestrator reads the inbox between "
                              "points. Use /docket stop to end the run.")

    mode = state.get("mode", "none")
    lines = []
    if mode != "none":
        lines.append(f"[docket] Mode {mode}" + (f", spec {state['spec']}" if state.get("spec") else "")
                     + ". " + REMINDERS.get(mode, ""))
        lines.append(ambition())
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


def held_by_live_session(paths, holder, spec_id):
    """A lock counts only while its session's state still says sealed on that spec; stale locks are taken over."""
    other = load_state(paths, holder)
    return other.get("mode") in ("sealed", "rewrite") and other.get("spec") == spec_id


HARNESS_BLOCK = re.compile(r"<(system-reminder|task-notification|side-sessions-event|cross-session-message)\b[^>]*>.*?</\1>",
                           re.DOTALL)


def strip_harness_blocks(prompt):
    return HARNESS_BLOCK.sub("", prompt)


def store_inbox(paths, spec_id, text):
    try:
        os.makedirs(paths.inbox, exist_ok=True)
        with open(posixpath.join(paths.inbox, f"{spec_id or 'session'}.md"), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')}\n{text}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
