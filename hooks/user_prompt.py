"""UserPromptSubmit hook.

Switches the session mode on /docket:concept, /docket:spec, /docket:implement, /docket:init, /docket:stop.
/docket:implement takes a spec, a list of specs, a concept (its ready specs), --all or nothing (the next
ready spec); several specs are a series: the first is sealed, the rest wait in the state's queue and the
Stop hook seals each after the previous one's docket states DELIVERED (resolve_implement).
Before sealing, the user-only preconditions of every spec of the run are checked (V12); the judge is suspended
for the run (V5).

No prompt is refused: this hook never exits 2. A command that cannot do what it says (a spec that runs elsewhere,
a precondition not met, an order out of sequence) changes nothing and reaches the model with the reason and the
way out; the model tells the user, and outside a run nothing is written or started in that turn (state
`declined`, pre_tool_use). Free text in a sealed run reaches the orchestrator as well: it is kept in the inbox
for the docket and the closing message, the orchestrator's next text is the answer and is shown (state
`answer_due`, message_display), and a run that had stopped goes on with it. Every turn gets a mode reminder as
additional context.
"""
import os
import posixpath
import re
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import command_line as prompt_command
from docket_lib import (CONCEPT_ID, REFUSAL_RULE, RUN_MODES, SPEC_ID, Paths, claim_lock, clock_line, config_values, display_path,
                        load_state,
                        lock_holder, log_event, logged_series, open_predecessors, precondition_result,
                        preconditions, queued_elsewhere, read_input, ready_specs, restore_mode, save_display,
                        save_state, say,
                        series_line, session_live, set_context, set_spec_state, spec_title, strip_harness_blocks,
                        suspend_judge, take_handoff, terminal_base, violations_line)

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
               "Nothing you did not fetch yourself is a fact. You write no text during the run, except the answer to "
               "a message the user writes during it, when the hook says so: a turn that waits "
               "for agents ends without text, and a spec ends in its docket's `## Terminal state` section (`state:`, "
               "`result:`, `open:`, `user:`, `decision:`), then the turn ends without text. The hook then seals the "
               "next spec of the series or hands you the run's one message to send verbatim. When the harness asks "
               "what you are doing, answer in at most five words, no stamp."),
    "rewrite": ("Rewrite mode: only the conceptor agent edits the named spec, and it records the rewrite with date "
                "and reason in the spec."),
    "closing": "The run is over: send the closing message the hook rendered, verbatim, and nothing else.",
    "closed": ("The implementation is closed. Answer only from the docket, shorter when not understood; "
               "propose nothing, start nothing. A new command opens the next mode."),
    "none": "",
}
WATERMARK = ("Every message ends with the line `stamp: YYYY-MM-DD HH:MM`: the time of the latest `[docket] clock:` line "
             "(the hooks give it with every prompt and every tool result; no tool call needed) or fetched from the clock "
             "now, never remembered; the hook refuses the message otherwise. Sent back: do exactly what the hook asks "
             "(the stamp line alone, correction lines, or after a judge verdict an empty answer); repeat a message "
             "only when it says it was held back. When the harness says an answer had no visible output and you have "
             "nothing new for the user, the whole answer is the line `[docket] noted`: it is not shown. "
             + REFUSAL_RULE)
# A prompt is never refused. What a command could not do, and a word of the user in a run, go to the model as an order.
DECLINED = ("Do not carry out the command: nothing is written or started in this turn. Tell the user in one or two "
            "sentences, in everyday words, what stands in the way and what they can do about it, and offer the step you "
            "can take for them.")
DECLINED_IN_RUN = ("Your next text is shown to the user, that one text only: say in one or two sentences, in everyday "
                   "words and without a stamp, what stands in the way and what they can do. Then go on with the run.")
RUN_ANSWER = ("The user wrote during the run; the message is above and in the inbox. Answer it before anything else. "
              "Your next text is shown to the user, that one text only, so it is the answer itself: short, in the user's "
              "language, everyday words, no stamp. A question is answered from the docket and the code. A wish that "
              "changes the work gets its line in the docket's Inbox section as always; what cannot go on without the "
              "user ends the spec in the docket (`state: BLOCKED: <what the user decides>`). Then go on with the run.")
RUN_RESUMED = "The run had stopped without being finished; with the user's message it goes on. "
RUN_RULE = ("In a sealed run you write no text and no stamp: the run's one message is the closing message the hook "
            "renders at its end. A turn that waits for agents ends without text.")


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
    if state.get("refused") or state.get("declined"):
        state["refused"] = False  # a new turn of the user: the next Stop is a first pass
        state.pop("declined", None)  # and what the last command could not do no longer holds anything
        save_state(paths, session_id, state)
    feedback = ""
    # The first line, or the last below a preamble (the pane, a session started by another); a line in between only
    # where the pane recorded exactly this command for a session of its own (the app wraps that session's brief).
    command_line = prompt_command(text) or take_handoff(paths, session_id, text)
    name, rest = parse_command(command_line)
    if not name:
        restore_mode(paths, session_id, state, hook_input.get("transcript_path"))  # a mode the state lost
    if name:
        # The model may expand this one command through the Skill tool; pre_tool_use checks against it.
        state["last_command"] = f"/docket:{name}{rest}".lower().split("\n")[0][:80]
        save_state(paths, session_id, state)
    if name and name != "init" and not os.path.isfile(paths.config):
        # Not set up: no command but init changes anything here. Nothing is sealed, locked, logged or put into an
        # inbox, and no work dir is created.
        print(f"[docket] No DOCKET.md at {paths.work}. Run /docket:init to set this project up; "
              "until then the docket hooks only observe.")
        return 0

    declined = order = ""
    if name == "implement":
        feedback, declined = implement(paths, session_id, state, rest)
    elif name == "stop":
        if state.get("mode") in ("sealed", "rewrite"):
            state["stop_requested"] = True
            save_state(paths, session_id, state)
            log_event(paths, session_id, state.get("spec", ""), "stop-requested")
    elif name == "init":
        state.update({"mode": "init", "spec": "", "init_approved": False})
        save_state(paths, session_id, state)
    elif name in ("status", "register"):
        # read-only: no mode change; a series of this session or an interrupted one is named with its titles (V13)
        mine = (session_id or "")[:8]
        feedback = "\n".join(series_line(paths, series, live=series["session"] == mine)
                             for series in logged_series(paths)[-3:]
                             if series["session"] == mine or "interrupted" in series["status"].values())
    elif state.get("mode") == "init" and GO.match(text):
        # The diff is approved on the user's one word; only now may init write project files.
        state["init_approved"] = True
        save_state(paths, session_id, state)
        log_event(paths, session_id, "", "init-approved")
    elif name in ("concept", "spec"):
        if state.get("mode") in RUN_MODES:
            declined = say(paths, "sealed_on", title=spec_title(paths, state.get("spec", "")) or state.get("spec", ""))
        else:
            state.update({"mode": name, "spec": ""})
            save_state(paths, session_id, state)
    elif state.get("mode") in ("sealed", "rewrite") and not command_line:
        # The user's word in a run reaches the orchestrator and is answered on the screen; the inbox keeps it for the
        # docket and the closing message. A run that had stopped goes on with it: no state is a dead end.
        store_inbox(paths, state.get("spec", ""), text)
        log_event(paths, session_id, state.get("spec", ""), "inbox")
        order = "[docket] " + (RUN_RESUMED if state.get("halted") else "") + RUN_ANSWER
        state.update({"answer_due": True, "halted": False})
        save_state(paths, session_id, state)

    if declined:
        # No prompt is thrown away (docket 2026-11-10: ten ways to refuse a prompt, eight messages of the user that
        # no one read). A command that cannot do what it says reaches the model with the reason and the way out, and
        # the model tells the user; outside a run nothing is written or started in that turn (pre_tool_use).
        reason = re.sub(r"^\[docket\]\s*", "", declined)
        order = f"[docket] The user's command did not take effect: {reason}\n[docket] "
        if state.get("mode") in ("sealed", "rewrite"):
            state["answer_due"] = True
            order += DECLINED_IN_RUN
        else:
            state["declined"] = reason[:200]
            order += DECLINED
        save_state(paths, session_id, state)

    mode = state.get("mode", "none")
    lines = []
    if mode != "none":
        lines.append(f"[docket] Mode {mode}" + (f", spec {state['spec']}" if state.get("spec") else "")
                     + ". " + REMINDERS.get(mode, ""))
        if mode in ("sealed", "rewrite") and state.get("queue"):
            lines.append("[docket] Queued after this spec, in order: " + ", ".join(state["queue"])
                         + ". When this spec's docket states DELIVERED the hook seals the next one in this session and "
                         "tells you; until then it does not exist for you.")
        if mode != "concept":
            lines.append(ambition())  # in concept mode it pushed past the stop (legacy session 2026-11-05)
        language = config_values(paths).get("user_language", "")
        if language:
            lines.append(f"[docket] Every message to the user is written in {language} (DOCKET.md `user_language`); "
                         "the hook refuses a message in another language. Agents are briefed in the language of the spec.")
    if name == "concept" and asks_for_specs(rest) and not declined:
        lines.append(SPEC_REQUEST_LINE)  # with the command that carries the order; it stays in the context
    if mode in RUN_MODES:
        lines.append("[docket] " + RUN_RULE)
        lines.append(clock_line(paths, session_id, state))
    elif mode != "none" or os.path.isfile(paths.config):
        lines.append("[docket] " + WATERMARK)
        lines.append(clock_line(paths, session_id, state))
    if feedback:
        lines.append(feedback)
    if order:
        lines.append(order)  # last: the word that decides this turn
    if state.get("degraded") and os.path.isfile(paths.config):
        missing = [f for f in paths.required_reading() if f not in state.get("read", [])]
        if missing:
            lines.append("[docket] Required reading still open, read completely with Read before anything else: "
                         + ", ".join(missing))
    if lines:
        print("\n".join(lines))
    return 0


def implement(paths, session_id, state, rest):
    """Seals the session on the spec, or the series, of an implement call. Returns (feedback, problem): the refusals
    counted for the spec so far, or why nothing was sealed, with the way out."""
    found, problem = resolve_implement(paths, rest)
    if problem:
        return "", problem
    # Several specs are a series: the first is sealed now, the rest follow in this session, one after another,
    # each with its own docket; the run ends in one message for all of them (docket_lib.render_closing).
    spec_id, queue = found[0], found[1:]
    title = lambda spec: spec_title(paths, spec) or spec
    if state.get("mode") in RUN_MODES and state.get("spec") != spec_id:
        return "", say(paths, "sealed_on", title=title(state.get("spec", "")))
    for candidate in found:
        holder = lock_holder(paths, candidate)
        if holder and holder != session_id and held_by_live_session(paths, holder, candidate):
            return "", say(paths, "sealed_elsewhere", title=title(candidate)) + f" (session {holder[:8]})"
        if queued_elsewhere(paths, candidate, session_id):
            return "", say(paths, "queued_in", title=title(candidate))
    # V12: what only the user can provide (a key, a running server) is checked before the session seals
    # (luradb-web-client 2026-11-06: the test server's key was invalid at sealing, the live test never ran). The
    # commands come from specs the user approved; forbidden_commands keeps its word here too.
    forbidden = config_values(paths).get("forbidden_commands", "")
    for candidate in found:
        for command, expected in preconditions(paths, candidate):
            try:
                if forbidden and re.search(forbidden, command):
                    return "", say(paths, "forbidden_precondition", command=command, title=title(candidate))
            except re.error:
                pass
            ok, got = precondition_result(paths, command, expected)
            if not ok:
                log_event(paths, session_id, candidate, "precondition", f"`{command[:60]}` {got}")
                return "", say(paths, "precondition", title=title(candidate), command=command,
                               expected=expected or "exit 0", got=got)
    # Only the spec at work is locked, exclusively; the ones waiting are named in this session's queue, which
    # other sessions respect while this one runs (V13: a lock on a spec never started outlived a crashed series).
    if claim_lock(paths, spec_id, session_id):
        return "", say(paths, "sealed_elsewhere", title=title(spec_id))
    resume = state.get("mode") in RUN_MODES and state.get("spec") == spec_id and found == [spec_id]
    outcomes, base = {}, terminal_base(paths, spec_id)
    if resume:
        # The resume of the spec at work in this session (the halted line's command): the series stays, and a
        # Terminal state block this run wrote still counts.
        queue, found = list(state.get("queue") or []), list(state.get("series") or found)
        outcomes, base = dict(state.get("outcomes") or {}), int(state.get("terminal_base", 0) or 0)
    state.update({"mode": "sealed", "spec": spec_id, "stop_requested": False, "queue": queue, "series": found,
                  "outcomes": outcomes, "refused": False, "closing": None, "halted": False,
                  "terminal_base": base})
    save_state(paths, session_id, state)
    log_event(paths, session_id, spec_id, "sealed")
    if queue:
        log_event(paths, session_id, spec_id, "queued", ", ".join(queue))
    set_spec_state(paths, spec_id, "in-progress")
    if suspend_judge(paths, session_id):
        log_event(paths, session_id, spec_id, "judge-suspended")
    return violations_line(paths, spec_id), ""


def resolve_implement(paths, rest):
    """The specs of an implement call, in run order, or a refusal.

    `/docket:implement`            the next ready spec whose predecessors (`follows:`) are done
    `/docket:implement c-xxxxxxxx` every ready spec of that concept, in implementation order
    `/docket:implement s-a, s-b`   exactly these, in the given order
    `/docket:implement --all`      every ready spec of the project, in implementation order
    """
    rest = (rest or "").strip()
    ids = list(dict.fromkeys(s.lower() for s in SPEC_ID.findall(rest)))
    concept = CONCEPT_ID.search(rest)
    if ids:
        # A named spec is a spec of this project (docket 2026-11-10: ids pasted from another project sealed a session).
        unknown = [s for s in ids if not paths.spec_file(s)]
        if unknown:
            return [], ("[docket] No spec " + ", ".join(unknown) + " in this project. Run /docket:register to see "
                        "the register.")
        chosen = ids
    elif concept:
        chosen = [s for s, _ in ready_specs(paths, concept.group(0))]
        if not chosen:
            return [], f"[docket] No spec of concept {concept.group(0)} is in state ready. Run /docket:register."
    elif re.search(r"(^|\s)--all\b", rest):
        chosen = [s for s, _ in ready_specs(paths)]
        if not chosen:
            return [], "[docket] No spec is in state ready. Run /docket:register."
    elif not rest:
        runnable = [s for s, _ in ready_specs(paths) if not open_predecessors(paths, [s])]
        if not runnable:
            return [], ("[docket] No ready spec whose predecessors are done. Run /docket:register, or name a spec: "
                        "/docket:implement s-xxxxxxxx.")
        chosen = runnable[:1]
    else:
        return [], ("[docket] /docket:implement takes a spec id (s-xxxxxxxx, several in order), a concept id "
                    "(c-xxxxxxxx: its ready specs), --all (every ready spec) or nothing (the next ready spec). "
                    "Run /docket:register to see the register.")
    missing = open_predecessors(paths, chosen)
    if missing:
        return [], ("[docket] Not in order: " + "; ".join(f"{s} follows {p}, which is not done" for s, p in missing)
                    + ". Run the predecessor first, or name it before its successor.")
    return chosen, ""


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
    """A lock counts only while its session runs on that spec (state sealed, rewrite or closing, touched within the
    hour); the lock of a crashed or finished session is taken over."""
    other = load_state(paths, holder)
    return session_live(paths, holder) and (other.get("spec") == spec_id or spec_id in (other.get("queue") or []))


def store_inbox(paths, spec_id, text):
    try:
        os.makedirs(paths.inbox, exist_ok=True)
        with open(posixpath.join(paths.inbox, f"{spec_id or 'session'}.md"), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')}\n{text}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
