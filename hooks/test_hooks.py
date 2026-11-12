"""Tests of the docket hooks. Each case runs a hook as a subprocess with JSON on stdin and
checks exit code, stderr and stdout, exactly what Claude Code sees.

Run: python hooks/test_hooks.py -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
SID = "test-session-0001"
SPEC = "s-0123abcd"


def run_hook(script, payload, project_dir):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["CLAUDE_PROJECT_DIR"] = project_dir
    result = subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload).encode("utf-8"),
                            capture_output=True, env=env)
    return result.returncode, result.stderr.decode("utf-8", "replace"), result.stdout.decode("utf-8", "replace")


# The judge entry of templates/judge-hook.json at c7cf2b7 (0.5.11), the file 1c.7 removes: test datum of
# JudgeCleanup. Its digest is the last of session_start.JUDGE_VERSIONS.
JUDGE_C7CF2B7 = {"type": 'prompt', "model": 'claude-haiku-5-5', "timeout": 30,
                 "prompt": (
              'You judge the final message of an assistant before a human reads it. Hook input JSON (the messag'
              'e is in last_assistant_message): $ARGUMENTS\n\nReply {"ok": true} when ANY of these hold: stop_hoo'
              'k_active is true; the message begins with DOCKET DELIVERED, DOCKET BLOCKED or DOCKET DISSENT; th'
              'e message is at most 3 lines, not counting a final `stamp:` line; the message answers a question'
              ' of the user with Ja, Nein, Yes or No in its first word and has at most 6 lines; the message con'
              'tains a line starting with [docket].\n\nOtherwise check every sentence. Allowed sentence kinds: (1'
              ') a result (whether it names its source is not yours to judge: the tool calls that produced it a'
              're not in your input); (2) one question to the user that is answerable with one word and comes w'
              'ith a recommendation; (3) a blocker only the user can resolve, which may name every step only th'
              'e user can take (a value to enter, a command to run once); those are not manual tasks. A sentenc'
              'e that names a command starting with /docket: for the user to type is never a manual task and ne'
              'ver a reason to reject. Reject the message when it contains: more than one question; a list of o'
              'ptions without a recommendation; a plan step that gives the user a manual task (toggle, click, c'
              'heck, run) instead of a decision, other than the one step of a blocker; a conditional plan step '
              '("if X still happens, then Y"); a promise about what the assistant will do next; an apology, a j'
              'ustification of its own behaviour, or a statement about effort or time spent; in a message that '
              'presents or quotes a spec or concept section, a sentence that describes how the system currently'
              ' behaves (what IS) instead of what it should do, unless it cites a reference doc by file name (s'
              'pecs say what SHOULD be, the reference docs say what IS).\n\nWhen rejecting, reply {"ok": false, "'
              'reason": "<one entry per rejected sentence, entries separated by ` | `; each entry is a rule tag'
              ', then up to 8 quoted words of the sentence. Rule tags: questions, options, manual-task, conditi'
              'onal, promise, self, is-not-should. End the reason with these three sentences: The user has read'
              ' your message and will not see your answer to this; end your turn with an empty answer: no text '
              'at all, no stamp, no tool call. If another hook asks in this same feedback for specific lines, w'
              'rite exactly those lines and nothing else. If the harness then says your response had no visible'
              ' output, answer with the one line [docket] noted and nothing else.>"}. Reply with JSON only.')}


class Project(unittest.TestCase):
    """A temp project with an explicit work dir; every test starts from a clean state."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="docket-test-")).resolve()
        self.root = self.tmp / "repo"
        self.work = self.tmp / "work"
        (self.root / ".claude").mkdir(parents=True)
        (self.root / ".claude" / "docket.json").write_text(json.dumps({"work_dir": str(self.work)}), "utf-8")
        for folder in ("specs/core", "dockets/core", "concepts/core"):
            (self.work / folder).mkdir(parents=True)
        (self.work / "DOCKET.md").write_text("# DOCKET\n", "utf-8")
        self.spec = self.work / "specs" / "core" / f"{SPEC}-login.md"
        self.spec.write_text(f"---\nid: {SPEC}\nstate: ready\n---\n# Login\n", "utf-8")
        self.docket = self.work / "dockets" / "core" / f"{SPEC}.md"
        self.p = str(self.root).replace("\\", "/")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def state(self):
        path = self.root / ".claude" / "docket" / f"state-{SID}.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else {}

    def set_state(self, **fields):
        base = {"version": 2, "mode": "none", "spec": ""}
        base.update(fields)
        (self.root / ".claude" / "docket").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "docket" / f"state-{SID}.json").write_text(json.dumps(base), "utf-8")

    def log(self):
        path = self.work / "LOG.md"
        return path.read_text("utf-8") if path.exists() else ""

    def prompt(self, text):
        return run_hook("user_prompt.py", {"session_id": SID, "cwd": self.p, "prompt": text}, self.p)

    def tool(self, name, **tool_input):
        return run_hook("pre_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": name,
                                            "tool_input": tool_input}, self.p)

    def stop(self, message, active=False):
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": active}, self.p)

    def run_stop(self, message="", active=False, **extra):
        payload = {"session_id": SID, "cwd": self.p, "last_assistant_message": message, "stop_hook_active": active}
        payload.update(extra)
        return run_hook("stop.py", payload, self.p)

    def end_spec(self, kind="DELIVERED", result="Login works, three tests are green.", lines=(), spec_id=SPEC):
        """A spec ends in its docket's Terminal state section (V3); a review section is no gate any more (1d)."""
        docket = self.work / "dockets" / "core" / f"{spec_id}.md"
        body = docket.read_text("utf-8") if docket.exists() else "# Docket\n"
        body += "\n## Terminal state\n\n" + f"- state: {kind}\n" + (f"- result: {result}\n" if result else "")
        docket.write_text(body + "".join(f"- {line}\n" for line in lines), "utf-8")

    def finish(self, message, **extra):
        """A 0.2.9 terminal message as an end in the docket: its kind goes into the Terminal state and the Stop has no
        text. 0 when the series ended (exit 2 with the run mark gone, 1d.6), else the hook's answer."""
        kind = message.split()[1] if message.startswith("DOCKET ") else "DELIVERED"
        if kind == "DELIVERED":
            self.end_spec()
        else:
            self.end_spec("BLOCKED: start Docker" if kind == "BLOCKED" else kind,
                          lines=["dissent: a, per src/a.rs", "dissent: b, per src/b.rs"] if kind == "DISSENT" else ())
        code, err, out = self.run_stop(**extra)
        if code == 2 and self.state().get("mode") == "none":
            return 0, err, out
        return code, err, out


class SessionStart(Project):
    def test_every_start_names_the_work_dir_and_writes_no_state(self):
        # 1c.5: the work dir, DOCKET.md and user_language at every source, and no state file.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n"
                                             "| user_language | `Deutsch` | |\n", "utf-8")
        config = str(self.work / "DOCKET.md").replace("\\", "/")
        for source in ("startup", "resume", "clear", "compact"):
            code, err, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": source}, self.p)
            self.assertEqual(code, 0, err)
            self.assertIn(config, out, source)
            self.assertIn("user_language: Deutsch", out)
            self.assertNotIn("Required reading", out)
            self.assertNotIn("stamp", out)
        self.assertFalse((self.root / ".claude" / "docket" / f"state-{SID}.json").exists())

    def test_the_work_dir_after_compact_and_at_status(self):
        config = str(self.work / "DOCKET.md").replace("\\", "/")
        code, err, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "compact"}, self.p)
        self.assertEqual(code, 0, err)
        self.assertIn(config, out)
        code, err, out = self.prompt("/docket:status")
        self.assertEqual(code, 0, err)
        self.assertIn(config, out)
        self.assertEqual(self.prompt("hello"), (0, "", ""))  # no command and no run mark: nothing

    def test_without_docket_md_prints_nothing(self):
        (self.work / "DOCKET.md").unlink()
        code, err, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")

    def test_without_docket_md_a_docket_command_names_init(self):
        (self.work / "DOCKET.md").unlink()
        code, err, out = self.prompt("/docket:status")
        self.assertEqual(code, 0, err)
        self.assertIn("/docket:init", out)
        code, err, out = self.prompt("hello")
        self.assertEqual(code, 0, err)
        self.assertNotIn("[docket]", out)


class ModeSwitch(Project):
    def test_implement_seals_and_logs(self):
        code, err, out = self.prompt(f"/implement {SPEC}")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertEqual(self.state()["spec"], SPEC)
        self.assertIn("sealed", self.log())
        self.assertIn(f"Spec 1 of 1 is running: {SPEC} ({SPEC})", out)
        self.assertIn(str(self.work / "DOCKET.md").replace("\\", "/"), out)

    def test_implement_without_id_and_without_a_ready_spec_seals_nothing_and_says_why(self):
        self.spec.write_text(f"---\nid: {SPEC}\nstate: draft\n---\n# Login\n", "utf-8")
        code, err, out = self.prompt("/implement")
        self.assertEqual(code, 0, err)
        self.assertIn("No run marked: no ready spec has all its predecessors done.", out)
        self.assertEqual(self.state().get("mode", "none"), "none")

    def test_implement_with_several_ids_seals_the_first_and_queues_the_rest(self):
        # luradb-web-client 2026-11-06: five ids in one call, the first was sealed, the other four vanished unnamed.
        for spec_id in ("s-aaaa1111", "s-bbbb2222"):
            (self.work / "specs" / "core" / f"{spec_id}.md").write_text(f"---\nid: {spec_id}\nstate: ready\n---\n", "utf-8")
        code, err, out = self.prompt(f"/docket:implement {SPEC},s-aaaa1111 s-bbbb2222")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["spec"], SPEC)
        self.assertEqual(self.state()["queue"], ["s-aaaa1111", "s-bbbb2222"])
        self.assertIn("Spec 1 of 3 is running", out)
        self.assertIn("After it: s-aaaa1111, s-bbbb2222.", out)
        self.assertIn("queued", self.log())
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)  # only the spec at work
        self.assertFalse((self.work / "locks" / "s-aaaa1111").exists())
        self.assertEqual(self.state()["series"], [SPEC, "s-aaaa1111", "s-bbbb2222"])

    def test_a_command_inside_pasted_text_is_text(self):
        # docket 2026-11-10: the complaint about a sealed session carried that session's transcript, command included.
        code, err, out = self.prompt(f"docket blocks me, look:\n\n> Aus der docket-Pane:\n/docket:implement {SPEC}\n"
                                     "[docket] Mode sealed\n\nWhat is going on?")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertNotIn("is running", out)
        self.assertFalse((self.work / "locks").exists())

    def test_the_panes_request_for_a_session_seals_nothing_here(self):
        # luradb-web-client 2026-11-10: the request carried the command on a line of its own, the hook sealed the
        # asking session on it, and the session started for the run found the spec taken.
        strings = (HOOKS / "mod" / "strings.ts").read_text("utf-8")
        asks = [line for line in strings.splitlines() if "start_session, initiation" in line]
        self.assertEqual(len(asks), 2, "askSession in English and German")
        for line in asks:
            text = line.strip().rstrip(",")[1:-1].replace("\\n", "\n").replace("\\`", "`").replace(
                "${command}", f"/docket:implement {SPEC}")
            self.assertIn(f"`/docket:implement {SPEC}`", text)
            code, err, _ = self.prompt(text)
            self.assertEqual(code, 0, err)
            self.assertEqual(self.state().get("mode", "none"), "none", text)

    def test_a_command_below_a_preamble_seals(self):
        for text in (f"Aus der docket-Pane, per Knopf von mir:\n/docket:implement {SPEC}",
                     "Background (from the session that started this one): started from the docket pane.\n\n"
                     f"Run this command exactly as written, as your first action:\n\n/docket:implement {SPEC}\n"):
            self.set_state(mode="none")
            shutil.rmtree(self.work / "locks", ignore_errors=True)
            code, err, _ = self.prompt(text)
            self.assertEqual(code, 0, err)
            self.assertEqual(self.state()["mode"], "sealed", text)

    # The brief of a session the app starts, as the session reads it (luradb-web-client 2026-11-10, session
    # 5482c916, entry 5): the app's heading and background above the task, a line of its own below the command.
    APP_BRIEF = ("Background (from the session that started this one):\nThis session was started from the docket "
                 "pane of the main session.\n\nTask:\nRun this command exactly as written, as your first action:\n\n"
                 "{command}\n\nShared notes folder for this group of sessions (create it when you first write to "
                 "it): C:/Users/x/side-session-notes/abc\n")

    def hand_over(self, command, session="pane-session-0002"):
        result = subprocess.run([sys.executable, str(HOOKS / "docket_ui.py"), "handoff", "--root", self.p,
                                 "--session", session, "--command", command], capture_output=True,
                                env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
        return json.loads(result.stdout.decode("utf-8"))

    def test_a_command_the_pane_handed_over_seals_inside_the_apps_brief(self):
        # The command was neither the first nor the last line, the session stayed unsealed, called the command as a
        # skill, was refused and ended on "type the command yourself".
        command = f"/docket:implement {SPEC}"
        self.assertTrue(self.hand_over(command)["ok"])
        code, err, out = self.prompt(self.APP_BRIEF.format(command=command))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertEqual(self.state()["spec"], SPEC)
        self.assertIn("is running", out)
        code, err, _ = self.tool("Skill", skill="docket:implement", args=SPEC)  # the model may expand it
        self.assertEqual(code, 0, err)
        self.assertEqual(self.log().count(" sealed "), 1)
        # Taken once: the record is gone, and the same text seals no second session.
        self.assertEqual(json.loads((self.root / ".claude" / "docket" / "handoff.json").read_text("utf-8")), [])

    def test_the_same_brief_without_a_record_of_the_pane_is_text(self):
        code, err, out = self.prompt(self.APP_BRIEF.format(command=f"/docket:implement {SPEC}"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertNotIn("is running", out)

    def test_a_handed_over_command_is_not_taken_by_the_session_that_recorded_it_nor_as_another_command(self):
        command = f"/docket:implement {SPEC}"
        self.assertTrue(self.hand_over(command, session=SID)["ok"])
        code, err, _ = self.prompt(f"look at this:\n{command}\nwhy does it not start?")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertTrue(self.hand_over(command)["ok"])  # now from the pane's session
        code, err, _ = self.prompt("look at this:\n/docket:implement s-aaaa1111\nwhy does it not start?")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertFalse(self.hand_over("rm -rf /")["ok"])  # only a mode command is recorded

    def test_a_project_not_set_up_is_never_sealed(self):
        # docket 2026-11-10: a complaint with a pasted `/docket:implement` line sealed a session in a repo without
        # DOCKET.md; eight messages of the user went to an inbox and none reached the model.
        (self.work / "DOCKET.md").unlink()
        code, err, out = self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.assertEqual(code, 0, err)
        self.assertIn("/docket:init", out)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertFalse((self.work / "locks").exists())
        self.assertEqual(self.log(), "")
        for command in ("/docket:concept an idea", "/docket:spec the order", "/docket:stop"):
            self.assertEqual(self.prompt(command)[0], 0)
            self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertEqual(self.prompt("why does this block?")[0], 0)
        self.assertFalse((self.work / "inbox").exists())

    def test_a_run_mode_in_the_state_binds_no_hook_without_docket_md(self):
        # The session of 2026-11-10 as it was left: sealed and halted in a project that is not set up.
        (self.work / "DOCKET.md").unlink()
        self.set_state(mode="sealed", spec=SPEC, halted=True, queue=["s-aaaa1111"])
        code, err, out = self.prompt("why does this block?")
        self.assertEqual((code, out.strip()), (0, ""), err)
        self.assertFalse((self.work / "inbox").exists())
        self.assertEqual(self.tool("mcp__ccd_turn__wrap_up", recap="The cause is found.", ask="Fix it?")[0], 0)
        self.assertEqual(self.tool("AskUserQuestion", questions=[])[0], 0)
        self.assertEqual(self.run_stop("The cause is the pasted command.")[0], 0)
        self.assertEqual(self.log(), "")

    def test_implement_with_a_spec_this_project_does_not_have_seals_nothing(self):
        code, err, out = self.prompt(f"/docket:implement {SPEC}, s-35903eb4, s-55d9034e")
        self.assertEqual(code, 0, err)
        self.assertIn("No run marked: no spec s-35903eb4, s-55d9034e in this project.", out)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertFalse((self.work / "locks").exists())
        self.assertNotIn("sealed", self.log())

    def test_namespaced_commands_are_the_same_commands(self):
        code, _, _ = self.prompt(f"/docket:implement {SPEC}")
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertNotIn("last_command", self.state())
        self.assertEqual(self.tool("Skill", skill="docket:implement")[0], 0)
        self.set_state(mode="none")
        for command in ("/docket:concept the next step", "/concept", "/docket:spec", "/spec", "/docket:init"):
            code, err, out = self.prompt(command)
            self.assertEqual(code, 0, err)
            self.assertIn("work dir: ", out, command)
            self.assertEqual(self.state()["mode"], "none", command)  # 1c.2: no mode for concept, spec, init

    def test_builtin_init_and_status_are_not_docket_commands(self):
        self.prompt("/init")
        self.assertNotEqual(self.state().get("mode"), "init")
        self.prompt(f"/docket:implement {SPEC}")
        code, _, _ = self.prompt("/status")
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["mode"], "sealed")
        self.prompt("/stop")
        self.assertFalse(self.state().get("stop_requested"))

    def test_free_text_in_a_run_gets_the_run_line_and_nothing_else(self):
        # 1c.2: no inbox, no order to answer first; the message reaches the model as it is.
        self.prompt(f"/implement {SPEC}")
        code, err, out = self.prompt("please also fix the logo")
        self.assertEqual((code, err.strip()), (0, ""))
        self.assertEqual(out.strip().splitlines(), [l for l in out.strip().splitlines() if "is running" in l])
        self.assertFalse((self.work / "inbox").exists())
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertNotIn("answer_due", self.state())

    def test_a_message_of_the_user_takes_a_stopped_run_up_again(self):
        self.set_state(mode="sealed", spec=SPEC, halted=True, refused=True)
        code, err, out = self.prompt("what happened?")
        self.assertEqual((code, err.strip()), (0, ""))
        self.assertIn("is running", out)
        self.assertEqual((self.state()["halted"], self.state()["refused"]), (False, False))
        self.assertEqual(self.run_stop("It stopped at the second point.")[0], 2)  # the run is checked again

    def test_no_prompt_is_ever_refused(self):
        # The rule of 0.5.4: whatever the state and whatever the prompt, user_prompt.py lets it through.
        prompts = ("hello", "/implement", f"/implement {SPEC}", "/implement s-ffffffff", "/implement the login one",
                   "/concept", "/spec", "/docket:stop", "/docket:status", "/docket:init", "go")
        for mode in ("none", "concept", "spec", "init", "sealed", "rewrite", "closing", "closed"):
            for halted in (False, True):
                for prompt in prompts:
                    self.set_state(mode=mode, spec=SPEC if mode in ("sealed", "rewrite", "closing", "closed") else "",
                                   halted=halted)
                    code, err, _ = self.prompt(prompt)
                    self.assertEqual((code, err.strip()), (0, ""), (mode, halted, prompt))

    def test_a_command_that_did_not_take_effect_holds_nothing(self):
        self.spec.write_text(f"---\nid: {SPEC}\nstate: draft\n---\n# Login\n", "utf-8")
        self.prompt("/docket:implement")
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/a.txt", content="x"), (0, "", ""))

    def test_harness_notifications_pass_through_when_sealed(self):
        self.prompt(f"/implement {SPEC}")
        code, _, out = self.prompt("<task-notification>\n<task-id>x</task-id>\n</task-notification>")
        self.assertEqual((code, out), (0, ""))
        self.assertFalse((self.work / "inbox").exists())

    def test_command_behind_system_reminder_still_seals(self):
        code, _, _ = self.prompt(f"<system-reminder>\nLinked sessions now: x\n</system-reminder>\n/implement {SPEC}")
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_docket_stop_sets_flag(self):
        self.prompt(f"/implement {SPEC}")
        code, _, _ = self.prompt("/docket:stop")
        self.assertEqual(code, 0)
        self.assertTrue(self.state()["stop_requested"])
        self.assertIn("stop-requested", self.log())

    def test_a_concept_command_in_a_run_changes_nothing(self):
        self.prompt(f"/implement {SPEC}")
        code, err, out = self.prompt("/concept")
        self.assertEqual(code, 0, err)
        self.assertIn("is running", out)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertNotIn("answer_due", self.state())

    def test_the_same_spec_again_changes_nothing_and_logs_nothing(self):
        self.prompt(f"/implement {SPEC}")
        log, state = self.log(), self.state()
        code, err, out = self.prompt(f"/docket:implement {SPEC}")
        self.assertEqual(code, 0, err)
        self.assertIn("is running", out)
        self.assertNotIn("No run marked", out)
        self.assertEqual((self.log(), self.state()), (log, state))

    def test_another_spec_in_a_run_is_not_marked(self):
        (self.work / "specs" / "core" / "s-aaaa1111.md").write_text("---\nid: s-aaaa1111\nstate: ready\n---\n", "utf-8")
        self.prompt(f"/implement {SPEC}")
        code, err, out = self.prompt("/docket:implement s-aaaa1111")
        self.assertEqual(code, 0, err)
        self.assertIn(f"No run marked: this session works on “{SPEC}”.", out)
        self.assertEqual(self.state()["spec"], SPEC)

    # 1c.4: the Skill call `docket:implement` is the second trigger of the run mark.
    def skill(self, args, session=SID):
        code, err, out = run_hook("pre_tool_use.py", {"session_id": session, "cwd": self.p, "tool_name": "Skill",
                                                      "tool_input": {"skill": "docket:implement", "args": args}}, self.p)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)["hookSpecificOutput"]

    def test_the_skill_call_marks_like_the_typed_command(self):
        for spec_id in ("s-aaaa1111", "s-bbbb2222"):
            (self.work / "specs" / "core" / f"{spec_id}.md").write_text(f"---\nid: {spec_id}\nstate: ready\n---\n", "utf-8")
        output = self.skill(f"{SPEC}, s-aaaa1111 s-bbbb2222")
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", output)
        self.assertIn("Spec 1 of 3 is running", output["additionalContext"])
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"], state["series"]),
                         ("sealed", SPEC, ["s-aaaa1111", "s-bbbb2222"], [SPEC, "s-aaaa1111", "s-bbbb2222"]))
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)
        self.assertIn("state: in-progress", self.spec.read_text("utf-8"))
        log = self.log()
        self.assertEqual((log.count(" sealed "), log.count(" queued ")), (1, 1))
        # The same call a second time changes nothing.
        self.assertIn("Spec 1 of 3 is running", self.skill(f"{SPEC}, s-aaaa1111 s-bbbb2222")["additionalContext"])
        self.assertEqual((self.log(), self.state()), (log, state))

    def test_the_skill_call_with_a_foreign_lock_marks_nothing(self):
        self.prompt(f"/implement {SPEC}")
        output = self.skill(SPEC, session="other-session-5")
        self.assertIn(f"No run marked: “{SPEC}” runs in another session (session {SID[:8]}).",
                      output["additionalContext"])
        self.assertFalse((self.root / ".claude" / "docket" / "state-other-session-5.json").exists())
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)

    def test_another_skill_and_a_project_without_docket_md_mark_nothing(self):
        code, err, out = self.tool("Skill", skill="docket:status")
        self.assertEqual((code, err, out), (0, "", ""))
        (self.work / "DOCKET.md").unlink()
        self.assertEqual(self.tool("Skill", skill="docket:implement", args=SPEC), (0, "", ""))
        self.assertEqual(self.state().get("mode", "none"), "none")


class SealedStop(Project):
    """1d: in a run the Stop hook refuses only a DELIVERED the gate does not carry; a turn that ends without a Terminal
    state goes on once with a state statement; the series ends with the run mark gone and one statement."""

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [x] |\n", "utf-8")

    def entries(self, *entries):
        path = self.tmp / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", "utf-8")
        return str(path)

    def spawn(self):
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return [{"type": "assistant", "timestamp": now,
                 "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                          "content": "Async agent launched successfully.\nagentId: a1"}]}}]

    NOTE = "<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n</task-notification>"

    def open_point(self):
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [ ] |\n", "utf-8")
        self.end_spec()

    def test_delivered_closes_the_spec_and_the_series_ends_with_one_statement(self):
        self.end_spec()
        code, err, out = self.run_stop()
        self.assertEqual(code, 2, err)  # the turn goes on once: the model writes the closing message
        self.assertEqual(out.strip(), "")
        self.assertTrue(err.startswith("[docket state] The series is over: 1 delivered, 0 blocked, 0 not started."), err)
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"]), ("none", "", []))
        self.assertIn(f"{SPEC}  delivered", self.log())
        self.assertIn("run-closed", self.log())
        self.assertFalse((self.work / "locks" / SPEC).exists())
        self.assertFalse((self.root / ".claude" / "docket" / f"closing-{SID}.md").exists())  # nothing rendered
        # The Stop after it runs without the run mark and passes.
        self.assertEqual(self.run_stop("Run finished: Login works.", active=True), (0, "", ""))
        self.assertEqual(self.log().count(" run-closed "), 1)

    def test_a_turn_without_the_terminal_state_goes_on_once_with_a_state_statement(self):
        code, err, _ = self.run_stop("I finished the login. Shall I also do the logout?")
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "[docket state] The docket of Login (s-0123abcd) has no Terminal state; 0 of 1 "
                                      "points are open.")
        self.assertEqual((self.state()["mode"], self.state()["refused"]), ("sealed", True))
        self.docket.unlink()
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        code, err, _ = self.run_stop()
        self.assertEqual((code, err.strip()), (2, "[docket state] Login (s-0123abcd) has no docket yet."))

    def test_open_point_blocks_delivered_but_not_blocked(self):
        self.open_point()
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket gate] The docket of Login (s-0123abcd) says DELIVERED; 1 point(s) still "
                                       "open `[ ]`"), err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket state] The series is over: 0 delivered, 1 blocked, 0 not started."), err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "blocked"}))
        self.assertIn("state: ready", self.spec.read_text("utf-8"))

    def test_blocked_with_a_docket_of_its_terminal_state_alone(self):
        self.docket.unlink()
        self.end_spec("BLOCKED: the working tree is not clean, commit or stash it", result="Nothing was built.")
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["outcomes"], {SPEC: "blocked"})

    def test_a_dissent_of_an_older_docket_closes_like_blocked(self):
        self.end_spec("DISSENT", lines=["dissent: a, per src/a.rs"])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "dissent"}))

    def test_waits_pass_without_text_or_stamp(self):
        code, err, _ = self.run_stop("", background_tasks=[{"id": "a1"}])
        self.assertEqual(code, 0, err)
        # The agent's notification has not reached the model: still a wait, also when background_tasks is empty.
        self.assertEqual(self.run_stop(transcript_path=self.entries(*self.spawn()))[0], 0)
        queued = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": self.NOTE}}
        self.assertEqual(self.run_stop(transcript_path=self.entries(*self.spawn(), queued))[0], 0)
        delivered = {"type": "user", "message": {"role": "user", "content": self.NOTE}}
        code, err, _ = self.run_stop(transcript_path=self.entries(*self.spawn(), delivered))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket state]"), err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertNotIn("stop-forced", self.log())

    def test_a_second_stop_right_after_the_refusal_tells_the_user_the_run_stopped(self):
        self.assertEqual(self.run_stop()[0], 2)
        code, err, out = self.run_stop(active=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["systemMessage"],
                         "[docket] Login: the run stopped (1 delivered, 0 deviations, 0 open). "
                         "`/docket:implement s-0123abcd` resumes it, and so does any message from you.")
        self.assertIn("stop-forced", self.log())
        self.assertEqual((self.state()["mode"], self.state()["halted"], self.state()["refused"]), ("sealed", True, False))

    def test_a_second_stop_right_after_the_gate_lets_the_turn_end(self):
        self.open_point()
        self.assertEqual(self.run_stop()[0], 2)
        code, err, out = self.run_stop(active=True)
        self.assertEqual(code, 0, err)
        self.assertIn("`/docket:implement s-0123abcd` resumes it", json.loads(out)["systemMessage"])
        self.assertIn("stop-forced", self.log())
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertIn("state: in-progress", self.spec.read_text("utf-8"))

    def test_another_hooks_block_is_no_second_pass(self):
        # stop_hook_active comes from any Stop hook's block; only this hook's own refusal makes a second pass.
        code, err, _ = self.run_stop(active=True)
        self.assertEqual(code, 2)
        self.assertNotIn("stop-forced", self.log())

    def test_a_tool_call_after_the_refusal_makes_the_next_stop_a_first_pass(self):
        for setup in (lambda: None, self.open_point):  # the state statement, then the gate
            self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
            setup()
            self.assertEqual(self.run_stop()[0], 2)
            run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read",
                                          "tool_input": {"file_path": "x"}}, self.p)
            self.assertEqual(self.run_stop(active=True)[0], 2)
            self.assertNotIn("stop-forced", self.log())

    def test_a_closing_state_reads_as_none(self):
        # 1d.7: a state file of 0.5 in a mode that is gone is none for every hook.
        sys.path.insert(0, str(HOOKS))
        import docket_lib
        paths = type("P", (), {"state_dir": f"{self.p}/.claude/docket", "config": str(self.work / "DOCKET.md")})()
        for mode in ("rewrite", "closing", "closed", "concept", "spec", "init"):
            self.set_state(mode=mode, spec=SPEC, series=[SPEC], closing={"file": "x", "attempts": 1})
            self.assertEqual(docket_lib.load_state(paths, SID)["mode"], "none", mode)
            self.assertEqual(self.run_stop("Some text."), (0, "", ""), mode)
        self.assertEqual((docket_lib.RUN_MODES, docket_lib.STATE_VERSION), (("sealed",), 2))

    def test_outside_a_run_every_stop_passes(self):
        self.set_state(mode="none")
        self.assertEqual(self.run_stop("anything goes here? really? a1b2c3d4e"), (0, "", ""))
        (self.work / "DOCKET.md").unlink()
        self.set_state(mode="sealed", spec=SPEC)
        self.assertEqual(self.run_stop("anything"), (0, "", ""))


class PointEvents(Project):
    """1b.3: in a session with the run mark, the marks of a docket the LOG does not know yet become LOG lines."""

    def post(self, path, **extra):
        payload = {"session_id": SID, "cwd": self.p, "tool_name": "Edit", "tool_input": {"file_path": str(path)}}
        payload.update(extra)
        return run_hook("post_tool_use.py", payload, self.p)

    def events(self, spec_id=SPEC):
        """(event, note) of the spec's LOG lines point-delivered and deviation."""
        lines = [line.split() for line in self.log().splitlines()]
        return [(fields[2], fields[-1]) for fields in lines
                if len(fields) > 3 and fields[1] == spec_id and fields[2] in ("point-delivered", "deviation")]

    def test_flips_are_logged(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | a | `x` | [ ] |\n| 2 | b | `y` | [ ] |\n", "utf-8")
        self.assertEqual(self.post(self.docket), (0, "", ""))
        self.assertEqual(self.events(), [])
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [~] |\n- deviation: b differs\n", "utf-8")
        self.assertEqual(self.post(self.docket), (0, "", ""))
        self.assertEqual(self.events(), [("point-delivered", "count=1"), ("deviation", "count=1")])

    def test_a_second_write_without_a_new_mark_logs_nothing(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [ ] |\n", "utf-8")
        self.post(self.docket)
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [ ] |\n- evidence: `x` → ok\n", "utf-8")
        self.post(self.docket)
        self.assertEqual(self.events(), [("point-delivered", "count=1")])
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [x] |\n- evidence: `x` → ok\n", "utf-8")
        self.post(self.docket)
        self.assertEqual(self.events(), [("point-delivered", "count=1"), ("point-delivered", "count=1")])

    def test_a_point_of_an_earlier_session_with_its_log_line_logs_nothing(self):
        (self.work / "LOG.md").write_text(
            f"2026-11-10T10:00:00Z  {SPEC:<11} {'point-delivered':<18} session=earlier1  count=1\n", "utf-8")
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [ ] |\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC)
        self.post(self.docket)
        self.assertEqual(self.events(), [("point-delivered", "count=1")])

    def test_the_spec_comes_from_the_file_name_and_only_a_run_logs(self):
        other = self.work / "dockets" / "core" / "s-89abcdef.md"
        other.write_text("| 1 | a | `x` | [x] |\n", "utf-8")
        self.set_state(mode="none", spec="")
        self.post(other)
        self.assertEqual(self.events("s-89abcdef"), [])
        self.set_state(mode="sealed", spec=SPEC)
        self.post(other)
        self.assertEqual(self.events("s-89abcdef"), [("point-delivered", "count=1")])
        self.assertEqual(self.events(), [])


class ReadyGate(Project):
    def post(self, path):
        return run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Edit",
                                             "tool_input": {"file_path": str(path)}}, self.p)

    def spec_text(self, criterion, answer, reference):
        return (f"---\nid: {SPEC}\nstate: ready\n---\n# Login\n\n## Goal (WHAT)\n\nUsers can log in.\n\n"
                f"## Points\n\n| # | Point | Done criterion |\n|---|---|---|\n| 1 | login | {criterion} |\n\n"
                f"## Reference update\n\n| File | Change |\n|---|---|\n{reference}\n\n"
                f"## User decisions\n\n| Question | Recommendation | Answer (date) |\n|---|---|---|\n| Keep 512? | yes | {answer} |\n")

    def test_complete_spec_passes_and_logs_ready_once(self):
        self.set_state(mode="spec")
        self.spec.write_text(self.spec_text("`cargo test` → `3 passed`", "yes (2026-11-05)", "| docs/auth.md | login section |"), "utf-8")
        code, err, _ = self.post(self.spec)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.log().count(" ready "), 1)
        self.post(self.spec)
        self.assertEqual(self.log().count(" ready "), 1)

    def test_a_spec_with_gaps_stays_ready(self):
        # 1b.2: no check and no demotion; the ready check lives in /docket:spec.
        self.set_state(mode="spec")
        self.spec.write_text(self.spec_text("works", "", ""), "utf-8")
        self.assertEqual(self.post(self.spec), (0, "", ""))
        self.assertIn("state: ready", self.spec.read_text("utf-8"))
        self.assertEqual(self.log().count(" ready "), 1)


class SpecState(Project):
    def test_spec_state_follows_the_run(self):
        self.spec.write_text(f"---\nid: {SPEC}\nstate: ready          # draft | ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}")
        self.assertIn("state: in-progress          # draft | ready", self.spec.read_text("utf-8"))
        self.docket.write_text("- [x] point 1\n", "utf-8")
        self.assertEqual(self.finish(f"DOCKET BLOCKED {SPEC}")[0], 0)
        self.assertIn("state: ready", self.spec.read_text("utf-8"))
        self.set_state(mode="sealed", spec=SPEC)
        self.assertEqual(self.finish(f"DOCKET DELIVERED {SPEC}")[0], 0)
        self.assertIn("state: done", self.spec.read_text("utf-8"))


class HookOwnedFiles(Project):

    def test_save_state_is_atomic_and_leaves_no_temp_files(self):
        self.prompt(f"/implement {SPEC}")
        folder = self.root / ".claude" / "docket"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [f"state-{SID}.json"])
        self.assertEqual(self.state()["mode"], "sealed")


class WorkDirGit(Project):
    def test_log_lines_become_commits_when_work_dir_is_a_repo(self):
        subprocess.run(["git", "-C", str(self.work), "init", "-q"], check=True)
        self.prompt(f"/implement {SPEC}")
        out = subprocess.run(["git", "-C", str(self.work), "log", "--oneline"], capture_output=True, text=True).stdout
        self.assertIn(f"sealed {SPEC}", out)

    def test_no_commits_without_a_repo(self):
        self.prompt(f"/implement {SPEC}")
        self.assertFalse((self.work / ".git").exists())


class Locks(Project):
    def test_a_second_session_is_not_sealed_while_the_first_runs_and_learns_why(self):
        self.prompt(f"/implement {SPEC}")
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)
        code, err, out = run_hook("user_prompt.py", {"session_id": "other-session-2", "cwd": self.p,
                                                     "prompt": f"/implement {SPEC}"}, self.p)
        self.assertEqual(code, 0, err)
        self.assertIn(f"No run marked: “{SPEC}” runs in another session (session {SID[:8]}).", out)
        state = self.root / ".claude" / "docket" / "state-other-session-2.json"
        self.assertFalse(state.exists() and json.loads(state.read_text("utf-8")).get("mode") != "none")
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)
        # No tool is held afterwards.
        for name, tool_input in (("Write", {"file_path": f"{self.p}/a.txt", "content": "x"}),
                                 ("Bash", {"command": "git status"}), ("Agent", {"prompt": "x"})):
            self.assertEqual(run_hook("pre_tool_use.py", {"session_id": "other-session-2", "cwd": self.p,
                                                          "tool_name": name, "tool_input": tool_input}, self.p),
                             (0, "", ""), name)

    def test_stale_lock_is_taken_over_and_released_on_close(self):
        (self.work / "locks").mkdir()
        (self.work / "locks" / SPEC).write_text("dead-session", "utf-8")
        code, _, _ = self.prompt(f"/implement {SPEC}")
        self.assertEqual(code, 0)
        self.docket.write_text("- [x] point 1\n", "utf-8")
        self.assertEqual(self.finish(f"DOCKET DELIVERED {SPEC}")[0], 0)
        self.assertFalse((self.work / "locks" / SPEC).exists())


class Provenance(Project):
    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.docket.write_text("- [x] point 1: `cargo test` → 3 passed\n", "utf-8")
        self.transcript = self.tmp / "transcript.jsonl"

    def write_transcript(self, blocks):
        lines = []
        for role, block in blocks:
            lines.append(json.dumps({"type": role, "message": {"content": [block]}}))
        self.transcript.write_text("\n".join(lines) + "\n", "utf-8")

    def stop_t(self, message):
        return self.finish(message, transcript_path=str(self.transcript))

    def test_check_after_last_agent_return_passes(self):
        self.write_transcript([
            ("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"prompt": "x"}}),
            ("user", {"type": "tool_result", "tool_use_id": "a1", "content": "done"}),
            ("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}}),
        ])
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.")
        self.assertEqual(code, 0, err)

    def test_check_before_last_agent_return_is_refused(self):
        self.write_transcript([
            ("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}}),
            ("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"prompt": "x"}}),
            ("user", {"type": "tool_result", "tool_use_id": "a1", "content": "done"}),
        ])
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.")
        self.assertEqual(code, 2)
        self.assertIn("before the last agent returned", err)

    def test_check_never_run_is_refused_but_blocked_passes(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}})])
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.")[0], 2)
        self.assertEqual(self.stop_t(f"DOCKET BLOCKED {SPEC}\nno docker\nYou must start Docker.")[0], 0)

    def several(self, *commands):
        (self.work / "DOCKET.md").write_text("| Key | Value | Source |\n|---|---|---|\n"
                                             "| check | `npm run check` `npm test` | ci.yml |\n", "utf-8")
        blocks = [("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}}),
                  ("user", {"type": "tool_result", "tool_use_id": "a1", "content": "done"})]
        for n, (command, failed) in enumerate(commands):
            blocks.append(("assistant", {"type": "tool_use", "id": f"b{n}", "name": "Bash", "input": {"command": command}}))
            blocks.append(("user", {"type": "tool_result", "tool_use_id": f"b{n}", "is_error": failed,
                                    "content": "Exit code 1" if failed else "ok"}))
        self.write_transcript(blocks)
        self.set_state(mode="sealed", spec=SPEC)
        return self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.")

    def test_a_check_of_several_commands_passes_alone_or_together(self):
        self.assertEqual(self.several(("npm run check && npm test", False))[0], 0)
        self.assertEqual(self.several(("npm run check", False), ("npm test", False))[0], 0)

    def test_every_command_of_the_check_must_run_and_none_may_fail(self):
        code, err, _ = self.several(("npm run check", False))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket gate] "), err)
        self.assertIn("the check `npm test` did not run in this session", err)
        code, err, _ = self.several(("npm run check", False), ("npm test", True))
        self.assertEqual(code, 2)
        self.assertIn("`npm test` failed", err)
        self.assertEqual(self.several(("npm test", True), ("npm run check && npm test", False))[0], 0)

    def test_placeholder_check_is_not_a_check(self):
        (self.work / "DOCKET.md").write_text("| check | `<command that exits 0>` | |\n", "utf-8")
        self.write_transcript([("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}})])
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.")[0], 0)


class ProjectRules(Project):
    def setUp(self):
        super().setUp()
        (self.work / "DOCKET.md").write_text(
            "| Key | Value | Source |\n|---|---|---|\n| push_branch | `feature/.*` | |\n"
            "| commit_regex | `(feat\|fix\|docs)(\\(.*\\))?: ` | |\n| forbidden_paths | `secrets/**, .env` | |\n"
            "| forbidden_commands | `gh workflow run\|terraform apply` | |\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC)

    def assertRule(self, err, key, value):
        """A refusal begins with its key and names the key's value and DOCKET.md as facts."""
        self.assertTrue(err.startswith(f"[docket rule: {key}]"), err)
        self.assertIn(value, err)
        self.assertIn("DOCKET.md", err)

    def test_push_to_other_branch_refused(self):
        code, err, _ = self.tool("Bash", command="git push origin main")
        self.assertEqual(code, 2)
        self.assertRule(err, "push_branch", "feature/.*")
        self.assertEqual(self.tool("Bash", command="git push -u origin feature/login")[0], 0)
        self.assertEqual(self.tool("Bash", command="git push origin HEAD:feature/login")[0], 0)

    def test_commit_message_must_match(self):
        code, err, _ = self.tool("Bash", command='git commit -m "login works"')
        self.assertEqual(code, 2)
        self.assertRule(err, "commit_regex", "(feat|fix|docs)")
        self.assertEqual(self.tool("Bash", command='git commit -m "feat(auth): login"')[0], 0)
        self.assertEqual(self.tool("Bash", command="git commit -m \"$(cat <<'EOF'\nfix: typo\n\nbody\nEOF\n)\"")[0], 0)
        self.assertEqual(self.tool("Bash", command="git commit -m \"$(cat <<'EOF'\nwrong\nEOF\n)\"")[0], 2)

    def test_forbidden_paths_and_commands(self):
        code, err, _ = self.tool("Write", file_path=f"{self.p}/secrets/key.pem", content="x")
        self.assertEqual(code, 2)
        self.assertRule(err, "forbidden_paths", "secrets/**, .env")
        self.assertEqual(self.tool("Edit", file_path=f"{self.p}/.env", old_string="a", new_string="b")[0], 2)
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/src/main.rs", content="x")[0], 0)
        code, err, _ = self.tool("Bash", command="gh workflow run deploy.yml")
        self.assertEqual(code, 2)
        self.assertIn("forbidden_commands", err)
        self.assertRule(err, "forbidden_commands", "gh workflow run|terraform apply")
        self.assertEqual(self.tool("Bash", command="gh pr list")[0], 0)

    def test_a_push_rule_goes_before_a_forbidden_command(self):
        code, err, out = self.tool("Bash", command="git push origin main && terraform apply")
        self.assertEqual((code, out.strip()), (2, ""))
        self.assertRule(err, "push_branch", "feature/.*")
        self.set_state(mode="none")  # outside a run too: a refusal, no dialog
        code, err, out = self.tool("Bash", command="git push origin main && terraform apply")
        self.assertEqual((code, out.strip()), (2, ""))
        self.assertRule(err, "push_branch", "feature/.*")

    def test_rules_inactive_without_config(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n", "utf-8")
        self.assertEqual(self.tool("Bash", command="git push origin main")[0], 0)
        self.assertEqual(self.tool("Bash", command='git commit -m "anything"')[0], 0)


class StartSummary(Project):
    def test_sealed_start_names_points_and_last_log_line(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("- [x] a\n- [~] b\n- [ ] c\n", "utf-8")
        (self.work / "LOG.md").write_text(f"2026-11-05T10:00:00Z  {SPEC}  sealed             session=abc\n", "utf-8")
        code, _, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "resume"}, self.p)
        self.assertEqual(code, 0)
        self.assertIn("1 delivered, 1 deviations, 1 open", out)
        self.assertIn("Last LOG line", out)

    def test_stopped_run_with_open_points_is_resumable(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [ ] |\n| 3 | c | `z` | [ ] |\n", "utf-8")
        self.assertEqual(self.finish(f"DOCKET BLOCKED {SPEC}")[0], 0)
        _, _, out = run_hook("session_start.py", {"session_id": "other-session-9", "cwd": self.p, "source": "startup"}, self.p)
        self.assertIn("stopped with open points", out)
        self.assertIn(f"{SPEC} (2 open)", out)
        self.assertNotIn("without a terminal state", out)

    def test_interrupted_run_is_named_in_a_new_session(self):
        (self.work / "LOG.md").write_text(f"2026-11-05T10:00:00Z  {SPEC}  sealed             session=abc\n", "utf-8")
        _, _, out = run_hook("session_start.py", {"session_id": "fresh-1", "cwd": self.p, "source": "startup"}, self.p)
        self.assertIn(f"without a terminal state: {SPEC}", out)


class Grants(Project):
    """M1: a forbidden command is put to the user (permissionDecision ask) instead of refused. On 2026-11-05 the
    hook said 'it needs the user' and had no way to take the user's yes."""

    def setUp(self):
        super().setUp()
        (self.work / "DOCKET.md").write_text("| Key | Value | Source |\n|---|---|---|\n"
                                             "| forbidden_commands | `gh workflow run\\|git -C concept push` | |\n", "utf-8")

    def post(self, command):
        return run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Bash",
                                             "tool_input": {"command": command}}, self.p)

    def test_forbidden_command_asks_the_user_and_its_run_is_a_grant(self):
        self.set_state(mode="spec")
        code, err, out = self.tool("Bash", command="git -C concept push")
        self.assertEqual(code, 0, err)
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "ask")
        self.assertIn("forbidden_commands", decision["permissionDecisionReason"])
        self.assertTrue(decision["permissionDecisionReason"].startswith("[docket rule: forbidden_commands]"))
        self.assertIn(" ask ", self.log())
        self.assertFalse((self.work / "metrics").exists())  # asking is not a violation
        self.assertEqual(self.post("git -C concept push")[0], 0)
        self.assertIn(" grant ", self.log())
        self.assertIn("git -C concept push", self.log())
        self.post("git status")
        self.assertEqual(self.log().count(" grant "), 1)

    def test_sealed_run_has_no_user_and_still_refuses(self):
        self.set_state(mode="sealed", spec=SPEC)
        code, err, out = self.tool("Bash", command="gh workflow run deploy.yml")
        self.assertEqual(code, 2)
        self.assertIn("forbidden_commands", err)
        self.assertEqual(out.strip(), "")
        self.assertTrue(err.startswith("[docket rule: forbidden_commands]"), err)
        self.assertNotIn("BLOCKED", err)
        self.assertNotIn("A sealed run has no user", err)

    def test_forbidden_paths_stay_refused(self):
        (self.work / "DOCKET.md").write_text("| forbidden_paths | `secrets/**` | |\n", "utf-8")
        self.set_state(mode="spec")
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/secrets/key.pem", content="x")[0], 2)


class NoRefusalOutsideTheRules(Project):
    """Grundsatz 1 of the 0.6 rework: with an empty rule set in DOCKET.md, no tool call is refused or put to the
    user, in a run or outside one."""

    def calls(self):
        return (("Write", {"file_path": f"{self.p}/src/a.py", "content": "x"}),
                ("Edit", {"file_path": f"{self.p}/src/a.py", "old_string": "a", "new_string": "b"}),
                ("Agent", {"subagent_type": "docket:implementer", "description": "docket refiner",
                           "prompt": "dockets/core/x.md"}),
                ("AskUserQuestion", {"questions": [{"question": "Which one?"}]}),
                ("Skill", {"skill": "docket:implement"}),
                ("Bash", {"command": "cat README.md && git push origin main && git commit -m 'anything'"}),
                # what 0.5 refused: a spec, a docket, the LOG and the session state written by a tool
                ("Write", {"file_path": str(self.spec), "content": "x"}),
                ("Write", {"file_path": str(self.docket), "content": "x"}),
                ("Edit", {"file_path": str(self.work / "LOG.md"), "old_string": "a", "new_string": "b"}),
                ("Write", {"file_path": str(self.root / ".claude" / "docket" / f"state-{SID}.json"), "content": "{}"}))

    def test_no_call_is_refused_without_rules(self):
        (self.work / "DOCKET.md").write_text("| Key | Value | Source |\n|---|---|---|\n| check | `pytest` | |\n", "utf-8")
        for state in ({"mode": "none"}, {"mode": "sealed", "spec": SPEC, "series": [SPEC]}):
            self.set_state(**state)
            for name, tool_input in self.calls():
                code, err, out = self.tool(name, **tool_input)
                self.assertEqual((code, err), (0, ""), (state["mode"], name, tool_input))
                self.assertNotIn("permissionDecision", out, (state["mode"], name))


class HookTexts(Project):
    """REWORK-SPEC 1e.2 and 1e.3: every text a hook gives comes from the table SAY in docket_lib.py, states a fact and
    gives no order; evals/refusals.py counts the blocking feedbacks of docket's hooks without an identifier."""

    HOOK_FILES = ("pre_tool_use.py", "post_tool_use.py", "user_prompt.py", "session_start.py", "stop.py")
    RULE_WORDS = r"(?i)\b(never|must|always|do not|don't|nie|niemals|muss|musst|immer|darfst)\b"
    ORDERS = ("Continue", "Start", "Read", "Send", "Answer", "Write", "Work", "Run", "Beginne", "Lies", "Sende",
              "Arbeite", "Schreibe", "Antworte", "Führe")
    PROTOCOL = ("PreToolUse", "ask")  # values of the hook output's JSON fields, no text
    LETTER = r"[A-Za-zÄÖÜäöüß]"

    @staticmethod
    def table():
        sys.path.insert(0, str(HOOKS))
        import docket_lib
        return docket_lib.SAY

    def test_no_entry_is_a_rule_or_an_order(self):
        import re
        for language, entries in self.table().items():
            self.assertEqual(set(entries), set(self.table()["en"]), language)
            for key, text in entries.items():
                self.assertIsNone(re.search(self.RULE_WORDS, text), (language, key, text))
                body = re.sub(r"^\[docket[^\]]*\]\s*", "", text.strip())
                for sentence in re.split(r"(?<=[.!?;:])\s+", body):
                    first = (re.findall(r"[\wÄÖÜäöüß]+", sentence) or [""])[0]
                    self.assertNotIn(first, self.ORDERS, (language, key, sentence))

    def test_no_hook_prints_a_text_of_its_own(self):
        """Static: what a hook hands to print, refuse, notice or ask holds no string literal with a letter (a JSON
        field name and the protocol's own values aside), and no file but the table spells an identifier."""
        import ast
        for name in self.HOOK_FILES + ("docket_lib.py",):
            tree = ast.parse((HOOKS / name).read_text("utf-8"))
            say = next((n for n in tree.body if isinstance(n, ast.Assign)
                        and getattr(n.targets[0], "id", "") == "SAY"), None)
            in_table = {id(node) for node in ast.walk(say)} if say else set()
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                          and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and "[docket" in node.value
                        and id(node) not in in_table and id(node) not in docstrings):
                    self.fail(f"{name}:{node.lineno}: an identifier outside the table")
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if called not in ("print", "refuse", "notice", "ask", "write") or name == "docket_lib.py" and called == "write":
                    continue
                keys = {id(k) for sub in ast.walk(node) if isinstance(sub, ast.Dict) for k in sub.keys if k is not None}
                # the names of table entries: say(paths, "<key>"), marked(paths, "<mark>", ...), mark="<mark>"
                keys |= {id(sub.args[1]) for sub in ast.walk(node) if isinstance(sub, ast.Call)
                         and getattr(sub.func, "id", "") in ("say", "marked") and len(sub.args) > 1}
                keys |= {id(k.value) for sub in ast.walk(node) if isinstance(sub, ast.Call)
                         for k in sub.keywords if k.arg == "mark"}
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Constant) and isinstance(sub.value, str) and id(sub) not in keys
                            and sub.value not in self.PROTOCOL):
                        self.assertNotRegex(sub.value, self.LETTER, f"{name}:{sub.lineno}")

    def texts(self, code, err, out):
        """What the model or the user reads of a hook's answer."""
        found = [err] if code == 2 else []
        try:
            data = json.loads(out)
        except ValueError:
            return found + ([out] if out.strip() else [])
        specific = data.get("hookSpecificOutput") or {}
        return found + [t for t in (data.get("systemMessage"), specific.get("additionalContext"),
                                    specific.get("permissionDecisionReason")) if t]

    def residue(self, text, language):
        """The text with every table entry taken out, its values with it: letters left over are a hook's own."""
        import re
        for template in sorted(self.table()[language].values(), key=len, reverse=True):
            parts = re.split(r"\{\w+\}", template)
            pattern = "".join(re.escape(part) + ("[^\n]*?" if i < len(parts) - 2 else "[^\n]*" if i == len(parts) - 2
                                                 else "") for i, part in enumerate(parts))
            text = re.sub(pattern, " ", text)
        return text

    def answers(self):
        """One hook answer of every kind a hook gives: state lines, the four rules and the dialog, the Skill trigger,
        the gate, the statements of the Stop hook, the line to the user after the second Stop, the series' end."""
        other = "s-0fedcba9"
        (self.work / "specs" / "core" / f"{other}-logout.md").write_text(
            f"---\nid: {other}\nstate: ready\ntitle: Logout\n---\n# Logout\n", "utf-8")
        found = [run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p),
                 self.prompt("/docket:status"), self.prompt("/docket:implement s-99999999"),
                 self.tool("Bash", command="gh workflow run deploy.yml"),
                 self.prompt(f"/docket:implement {SPEC} {other}"), self.prompt("hello"),
                 self.tool("Bash", command="gh workflow run deploy.yml"),
                 self.tool("Bash", command="git push origin main"),
                 self.tool("Bash", command='git commit -m "login works"'),
                 self.tool("Write", file_path=f"{self.p}/secrets/key.pem", content="x"),
                 self.tool("Skill", skill="docket:implement", args=SPEC),
                 run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "compact"}, self.p),
                 self.run_stop(), self.run_stop(active=True)]
        self.docket.write_text("## Points\n\n| # | Point | Acceptance | |\n|---|---|---|---|\n"
                               "| 1 | login | `pytest` → passed | [x] |\n| 2 | logout | `pytest` → passed | [ ] |\n", "utf-8")
        found += [self.run_stop()]
        self.end_spec()
        found += [self.run_stop(), self.run_stop(active=True)]
        self.end_spec("BLOCKED: no database")
        found += [self.run_stop(), run_hook("session_start.py", {"session_id": "other-session", "cwd": self.p,
                                                                "source": "startup"}, self.p)]
        return found

    def test_every_text_a_hook_gives_is_made_of_table_entries(self):
        for language, setting in (("en", "English"), ("de", "Deutsch")):
            self.tearDown()
            self.setUp()
            (self.work / "DOCKET.md").write_text(
                "| Key | Value | Source |\n|---|---|---|\n| push_branch | `feature/.*` | |\n"
                "| commit_regex | `feat: ` | |\n| forbidden_paths | `secrets/**` | |\n"
                f"| forbidden_commands | `gh workflow run` | |\n| user_language | `{setting}` | |\n"
                "| check | `pytest` | |\n", "utf-8")
            texts = [t for answer in self.answers() for t in self.texts(*answer)]
            marks = [t.lstrip()[:15] for t in texts]
            for mark in ("[docket rule: f", "[docket rule: p", "[docket rule: c", "[docket gate] ", "[docket state] "):
                self.assertTrue(any(m.startswith(mark) for m in marks), (language, mark, marks))
            for text in texts:
                self.assertNotRegex(self.residue(text, language), self.LETTER, (language, text))
            # and a hook text of its own would be found
            self.assertRegex(self.residue("[docket] Read the docket first.", language), self.LETTER)

    def test_refusals_counts_the_feedback_of_a_docket_hook_without_an_identifier(self):
        hook = "s=\"${CLAUDE_PLUGIN_ROOT}/hooks/%s.py\"; [ -f \"$s\" ] || exit 0; p=$(command -v python); \"$p\" \"$s\""
        error = lambda text: {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": f"PreToolUse:Bash hook error: [{hook % 'pre_tool_use'}]: {text}"}]}}
        entries = [
            error("[docket rule: push_branch] The push goes to `main`; push_branch in DOCKET.md is `feature/.*`."),
            {"type": "system", "subtype": "stop_hook_summary",
             "hookInfos": [{"command": hook % "stop"}, {"command": "lint.sh"}],
             "hookErrors": [f"[{hook % 'stop'}]: [docket state] The docket of Login (s-0123abcd) has no Terminal "
                            "state; 1 of 2 points are open.",
                            "[/home/u/lint.sh]: The linter found 3 problems."]},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": (
                f"Stop hook feedback:\n[{hook % 'stop'}]: [docket state] The docket of Login (s-0123abcd) has no "
                "Terminal state; 1 of 2 points are open.")}},
            error("[docket] Writes in this mode are refused."),
            {"type": "attachment", "attachment": {"type": "hook_success", "command": hook % "post_tool_use",
                                                  "exitCode": 0, "stdout": "no identifier, no block"}},
        ]
        transcript = self.tmp / "session.jsonl"
        transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n", "utf-8")
        result = subprocess.run([sys.executable, str(HOOKS.parent / "evals" / "refusals.py"), str(transcript)],
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "1", result.stdout)
        self.assertNotIn("refused", result.stdout)  # counts, no text of the feedback


class JudgeCleanup(Project):
    """1c.6: the session start takes docket's judge entry out of the project's settings, once, and keeps it in
    .claude/docket/judge-removed.json."""

    def setUp(self):
        super().setUp()
        self.claude = self.root / ".claude"
        self.removed = self.claude / "docket" / "judge-removed.json"
        self.other = {"type": "command", "command": "node no-root-docs.mjs"}

    def start(self):
        code, err, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p)
        self.assertEqual(code, 0, err)
        return out

    def write(self, name, data, bom=False, crlf=False):
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        (self.claude / name).write_bytes(("﻿" if bom else "").encode("utf-8")
                                          + (text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))

    def read(self, name):
        return json.loads((self.claude / name).read_bytes().decode("utf-8").lstrip("﻿"))

    def record(self):
        return json.loads(self.removed.read_text("utf-8"))["entries"]

    def test_an_unchanged_copy_goes_and_everything_else_stays(self):
        self.write("settings.json", {"model": "x", "hooks": {"Stop": [{"hooks": [JUDGE_C7CF2B7]}],
                                                             "PreToolUse": [{"matcher": "Bash", "hooks": [self.other]}]}},
                   bom=True, crlf=True)
        out = self.start()
        self.assertIn("The judge entry of docket 0.5 was removed from .claude/settings.json; the removed entry is in "
                      ".claude/docket/judge-removed.json.", out)
        self.assertEqual(self.read("settings.json"),
                         {"model": "x", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [self.other]}]}})
        raw = (self.claude / "settings.json").read_bytes()
        self.assertTrue(raw.startswith("﻿".encode("utf-8")))
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))
        self.assertEqual([(e["file"], e["hook"]) for e in self.record()], [(".claude/settings.json", JUDGE_C7CF2B7)])
        self.assertEqual(list(self.claude.glob("*.tmp")), [])

    def test_a_changed_copy_is_known_by_its_mark(self):
        changed = dict(JUDGE_C7CF2B7, model="claude-sonnet-5-5", timeout=60,
                       prompt=JUDGE_C7CF2B7["prompt"].replace("before a human reads it", "before anyone reads it"))
        self.write("settings.json", {"hooks": {"Stop": [{"hooks": [changed]}]}})
        self.assertIn("removed from .claude/settings.json", self.start())
        self.assertEqual(self.read("settings.json"), {"hooks": {}})
        self.assertEqual(self.record()[0]["hook"], changed)

    def test_a_second_hook_of_the_stop_group_stays(self):
        second = {"type": "command", "command": "echo hi"}
        self.write("settings.json", {"hooks": {"Stop": [{"hooks": [second, JUDGE_C7CF2B7]}]}})
        self.start()
        self.assertEqual(self.read("settings.json"), {"hooks": {"Stop": [{"hooks": [second]}]}})

    def test_the_local_settings_and_a_file_git_tracks(self):
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        self.write("settings.json", {"hooks": {"Stop": [{"hooks": [JUDGE_C7CF2B7]}]}})
        subprocess.run(["git", "-C", str(self.root), "add", ".claude/settings.json"], check=True)
        self.write("settings.local.json", {"hooks": {"Stop": [{"hooks": [JUDGE_C7CF2B7]}]}})
        self.assertIn("removed from .claude/settings.json, .claude/settings.local.json;", self.start())
        self.assertEqual((self.read("settings.json"), self.read("settings.local.json")), ({"hooks": {}}, {"hooks": {}}))
        self.assertEqual([e["file"] for e in self.record()], [".claude/settings.json", ".claude/settings.local.json"])

    def test_a_suspended_judge_goes_to_the_record_and_its_file_is_deleted(self):
        suspended = self.claude / "docket" / "judge-suspended.json"
        suspended.parent.mkdir(parents=True, exist_ok=True)
        entry = {"file": f"{self.p}/.claude/settings.json", "hook": JUDGE_C7CF2B7}
        suspended.write_text(json.dumps({"sessions": ["dead-session"], "entries": [entry]}), "utf-8")
        self.assertIn("removed from .claude/docket/judge-suspended.json;", self.start())
        self.assertFalse(suspended.exists())
        self.assertFalse((self.claude / "settings.json").exists())  # not written back
        self.assertEqual([(e["file"], e["hook"]) for e in self.record()], [(entry["file"], JUDGE_C7CF2B7)])

    def test_a_second_start_changes_nothing_and_says_nothing(self):
        self.write("settings.json", {"hooks": {"Stop": [{"hooks": [JUDGE_C7CF2B7]}]}})
        self.assertIn("judge entry", self.start())
        before = ((self.claude / "settings.json").read_bytes(), self.removed.read_bytes())
        self.assertNotIn("judge entry", self.start())
        self.assertEqual(((self.claude / "settings.json").read_bytes(), self.removed.read_bytes()), before)

    def test_old_files_of_0_5_go_and_the_record_stays(self):
        folder = self.claude / "docket"
        folder.mkdir(parents=True, exist_ok=True)
        old = time.time() - 2 * 3600
        names = ("display-a.json", "display-a-m1.txt", "snap-s-0123abcd.md", f"state-{SID}.json.123.tmp",
                 "judge-removed.json", "state-other.json")
        for name in names:
            (folder / name).write_text("{}", "utf-8")
            os.utime(folder / name, (old, old))
        (folder / "snap-new.md").write_text("x", "utf-8")  # younger than an hour
        self.start()
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         sorted(["judge-removed.json", "state-other.json", "snap-new.md"]))
        os.utime(folder / "judge-removed.json", (time.time() - 30 * 86400,) * 2)
        sys.path.insert(0, str(HOOKS))
        import docket_lib
        docket_lib.prune_states(str(folder))
        self.assertTrue((folder / "judge-removed.json").exists())  # prune_states keeps the way back


class Series(Project):
    """Several specs are a series in one session, each with its own docket; the run ends in one closing message
    for all of them. luradb-web-client 2026-11-06: five ids, the first sealed, four lost (0.2.8); four delivered,
    four receipts, no word about the series (0.2.9)."""

    def spec_file(self, spec_id, title, state="ready", follows="", concept="", created="2026-11-01"):
        path = self.work / "specs" / "core" / f"{spec_id}-{title.lower()}.md"
        path.write_text(f"---\nid: {spec_id}\ntitle: {title}\nstate: {state}\nconcept: {concept}\nfollows: {follows}\n"
                        f"created: {created}\n---\n# {title}\n", "utf-8")
        return path

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\nconcept: c-11112222\nfollows:\ncreated: 2026-11-01\n---\n# Login\n", "utf-8")
        self.spec_file("s-aaaa1111", "Logout", follows=SPEC, concept="c-11112222", created="2026-11-02")
        self.spec_file("s-bbbb2222", "Profile", concept="c-33334444", created="2026-11-03")
        self.spec_file("s-cccc3333", "Done", state="done", created="2026-10-01")

    def deliver(self, spec_id, title):
        (self.work / "dockets" / "core" / f"{spec_id}.md").write_text("| 1 | x | `x` | [x] |\n", "utf-8")
        self.end_spec(spec_id=spec_id, result=f"{title} works.")

    def test_no_argument_picks_the_next_ready_spec_whose_predecessors_are_done(self):
        code, err, out = self.prompt("/docket:implement")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["spec"], SPEC)  # Logout follows Login; Profile is younger
        self.assertEqual(self.state()["queue"], [])

    def test_a_concept_runs_its_ready_specs_in_order(self):
        code, err, out = self.prompt("/docket:implement c-11112222")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.state()["spec"], self.state()["queue"]), (SPEC, ["s-aaaa1111"]))
        self.assertIn("Spec 1 of 2 is running: Login (s-0123abcd)", out)
        self.assertIn("After it: s-aaaa1111.", out)

    def test_all_runs_every_ready_spec_in_implementation_order(self):
        code, err, _ = self.prompt("/docket:implement --all")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.state()["spec"], self.state()["queue"]), (SPEC, ["s-aaaa1111", "s-bbbb2222"]))

    def test_a_successor_before_its_undone_predecessor_is_not_sealed(self):
        code, err, out = self.prompt("/docket:implement s-aaaa1111")
        self.assertEqual(code, 0, err)
        self.assertIn(f"s-aaaa1111 needs {SPEC} first, which is not done", out)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.prompt(f"/docket:implement s-aaaa1111, {SPEC}")
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertEqual(self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")[0], 0)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_words_without_an_id_seal_nothing_and_name_the_forms(self):
        code, err, out = self.prompt("/docket:implement the login one")
        self.assertEqual(code, 0, err)
        self.assertIn("No run marked: /docket:implement takes a spec id", out)
        self.assertIn("--all", out)
        self.assertEqual(self.state().get("mode", "none"), "none")

    def test_a_series_with_a_precondition_is_marked_and_the_precondition_does_not_run(self):
        # 1c.1: the run mark runs no precondition; the run runs it as its first step per spec.
        marker = (self.tmp / "precondition-ran").as_posix()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\nconcept: c-11112222\nfollows:\n"
                             f"created: 2026-11-01\n---\n# Login\n\n## Preconditions (user only)\n\n"
                             f"- `touch {marker}; exit 2` → ok\n", "utf-8")
        code, err, out = self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        self.assertEqual(code, 0, err)
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"]), ("sealed", SPEC, ["s-bbbb2222"]))
        self.assertTrue((self.work / "locks" / SPEC).exists())
        self.assertFalse((self.work / "locks" / "s-bbbb2222").exists())
        self.assertFalse(os.path.exists(marker))
        self.assertNotIn("precondition", self.log())

    def test_delivered_seals_the_next_spec_and_hands_over_on_stderr(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111, s-bbbb2222")
        self.deliver(SPEC, "Login")
        code, err, out = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(), "")  # no JSON, no systemMessage: nothing for the user
        # 1d.5: a state statement, no order.
        self.assertTrue(err.startswith("[docket state] Spec 2 of 3 is running: Logout (s-aaaa1111); its docket is at "),
                        err)
        self.assertIn("After it: s-bbbb2222.", err)
        self.assertNotIn("step 0", err)
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"], state["series"]),
                         ("sealed", "s-aaaa1111", ["s-bbbb2222"], [SPEC, "s-aaaa1111", "s-bbbb2222"]))
        log = self.log()
        self.assertIn(f"{SPEC}  delivered", log)
        self.assertIn("s-aaaa1111  sealed", log)
        self.assertNotIn("run-closed", log)
        self.assertIn("state: in-progress", (self.work / "specs" / "core" / "s-aaaa1111-logout.md").read_text("utf-8"))
        self.assertIn("state: done", self.spec.read_text("utf-8"))
        metrics = json.loads((self.work / "metrics" / f"{SPEC}.json").read_text("utf-8"))
        self.assertEqual((metrics["outcome"], metrics["points"]["delivered"]), ("delivered", 1))
        self.assertNotIn("refinement", metrics)
        # The next spec's first Stop is a first pass although stop_hook_active is true.
        code, err, _ = self.run_stop(active=True)
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "[docket state] Logout (s-aaaa1111) has no docket yet.")
        code, err, out = self.run_stop(active=True)
        self.assertEqual(code, 0, err)
        self.assertIn("Logout: the run stopped", json.loads(out)["systemMessage"])

    def test_a_series_ends_with_one_statement(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.deliver(SPEC, "Login")
        self.assertEqual(self.run_stop()[0], 2)
        self.deliver("s-aaaa1111", "Logout")
        code, err, out = self.run_stop(active=True)
        self.assertEqual((code, out.strip()), (2, ""))
        self.assertEqual(err.strip(), "[docket state] The series is over: 2 delivered, 0 blocked, 0 not started.")
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"]), ("none", "", []))
        self.assertEqual(state["outcomes"], {SPEC: "delivered", "s-aaaa1111": "delivered"})
        self.assertFalse((self.work / "locks" / "s-aaaa1111").exists())
        self.assertEqual(self.log().count(" run-closed "), 1)
        # Exactly one continuation: the closing message's Stop passes.
        self.assertEqual(self.run_stop("Run finished: two specs.", active=True), (0, "", ""))
        self.assertEqual(self.log().count(" run-closed "), 1)

    def test_blocked_stops_the_series_and_names_the_rest(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.assertFalse((self.work / "locks" / "s-aaaa1111").exists())  # only the spec at work is locked
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.")
        code, err, out = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(), "")
        self.assertEqual(err.strip(), "[docket state] The series is over: 0 delivered, 1 blocked, 1 not started "
                                      "(“Logout” waits for “Login”).")
        self.assertEqual((self.state()["mode"], self.state()["queue"]), ("none", []))
        self.assertIn("queue-dropped", self.log())
        self.assertIn("run-closed", self.log())

    def test_the_resume_line_names_the_blocked_spec_before_the_one_that_waits_for_it(self):
        # Review of b0cb330: the closing message's resume line named only the specs not started; mark_run refuses a
        # spec whose predecessor ended blocked unless the predecessor runs first.
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.")
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "none")
        code, err, out = self.prompt("/docket:implement s-aaaa1111")
        self.assertIn("No run marked: ", out)
        self.assertIn("s-aaaa1111 needs s-0123abcd first", out)
        self.assertEqual(self.state()["mode"], "none")
        code, err, out = self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.assertEqual(code, 0, err)
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"]), ("sealed", SPEC, ["s-aaaa1111"]))

    def repo(self):
        """The project as a git repository with one commit; docket's state dir is ignored."""
        git = lambda *args: subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t"]
                                           + list(args), capture_output=True, check=True)
        git("init", "-q")
        (self.root / ".gitignore").write_text(".claude/\n", "utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "one")
        return git

    def test_after_in_the_goal_is_a_predecessor_like_follows(self):
        # luradb-web-client 2026-11-10: `after:` stood in the goal, the order and the start check read `follows:` only.
        self.spec_file("s-dddd4444", "Import", created="2026-10-15").write_text(
            "---\nid: s-dddd4444\ntitle: Import\nstate: ready\nfollows:\ncreated: 2026-10-15\n---\n# Import\n\n"
            "## Goal (WHAT)\n\nUsers import a file. `after: s-bbbb2222` (the profile) and `after: s-eeee5555`.\n\n"
            "## Rewrite\n\n- 2026-11-01: Goal (`after: s-aaaa1111` replaced by independence).\n", "utf-8")
        self.assertEqual(self.prompt("/docket:implement s-dddd4444")[0], 0)
        self.assertEqual(self.state().get("mode", "none"), "none")  # Profile is not done; an id of no spec holds nothing up
        code, err, out = self.prompt("/docket:implement s-dddd4444, s-bbbb2222")
        self.assertIn("s-dddd4444 needs s-bbbb2222 first, which is not done", out)
        self.assertNotIn("s-aaaa1111", out)  # the Rewrite section quotes what it removed
        self.assertNotIn("s-eeee5555", out)
        self.assertEqual(self.prompt("/docket:implement --all")[0], 0)  # the oldest spec, yet after Profile
        self.assertEqual((self.state()["spec"], self.state()["queue"]), (SPEC, ["s-aaaa1111", "s-bbbb2222", "s-dddd4444"]))

    def test_blocked_with_the_project_untouched_lets_the_series_go_on(self):
        # luradb-web-client 2026-11-10: the second of three specs blocked at 0 points and the third never ran.
        self.repo()
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111, s-bbbb2222")
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.")
        code, err, out = self.run_stop()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket state] Spec 3 of 3 is running: Profile (s-bbbb2222)"), err)  # Logout follows Login: passed over
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"]), ("sealed", "s-bbbb2222", []))
        self.assertIn("s-aaaa1111  passed-over", self.log())
        self.assertIn("waits for s-0123abcd", self.log())
        self.deliver("s-bbbb2222", "Profile")
        code, err, _ = self.run_stop(active=True)
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "[docket state] The series is over: 1 delivered, 1 blocked, 1 not started "
                                      "(“Logout” waits for “Login”).")
        self.assertEqual(self.state()["mode"], "none")

    def test_blocked_with_its_work_pushed_lets_the_series_go_on(self):
        # luradb-web-client 2026-11-10: the third spec's work was pushed and the tree clean; the fourth never started.
        git = self.repo()
        remote = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        git("remote", "add", "origin", str(remote))
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n"
                                             "| user_language | `Deutsch` | |\n", "utf-8")
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        (self.root / "half.txt").write_text("half\n", "utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "half a login")
        git("push", "-q", "-u", "origin", "HEAD")
        self.end_spec("BLOCKED: Docker starten", result="Login ist zur Hälfte gebaut.")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket state] Spec 2 von 2 läuft: Profile (s-bbbb2222)"), err)
        self.deliver("s-bbbb2222", "Profile")
        code, err, _ = self.run_stop(active=True)
        self.assertEqual(err.strip(), "[docket state] Die Reihe ist zu Ende: 1 geliefert, 1 blockiert, 0 nicht begonnen.")

    def test_blocked_with_work_left_in_the_project_ends_the_series(self):
        # The next spec's push would carry what the blocked one left behind.
        git = self.repo()
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        (self.root / "half.txt").write_text("half\n", "utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "half a login")
        self.end_spec("BLOCKED: the review found a fault in the login", result="Login is half built.")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual((self.state()["mode"], self.state()["queue"]), ("none", []))
        self.assertIn("1 not started (the run ended at “Login” before “Profile”).", err)
        self.assertIn("queue-dropped", self.log())

    def test_stop_request_ends_the_series(self):
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        self.prompt("/docket:stop")
        self.deliver(SPEC, "Login")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(self.state()["mode"], "none")
        self.assertEqual(err.strip(), "[docket state] The series is over: 1 delivered, 0 blocked, 1 not started "
                                      "(“Profile” after /docket:stop).")

    def test_a_spec_finished_elsewhere_is_named_with_its_state(self):
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        self.spec_file("s-bbbb2222", "Profile", state="done", concept="c-33334444", created="2026-11-03")
        self.deliver(SPEC, "Login")
        code, err, _ = self.run_stop()
        self.assertEqual(err.strip(), "[docket state] The series is over: 1 delivered, 0 blocked, 1 not started "
                                      "(“Profile” is in state done).")

    def test_a_queued_spec_waits_for_its_series(self):
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        code, err, out = run_hook("user_prompt.py", {"session_id": "other-session-0002", "cwd": self.p,
                                                     "prompt": "/docket:implement s-bbbb2222"}, self.p)
        self.assertEqual(code, 0, err)
        self.assertIn("waits in another session's series", out)
        self.assertFalse((self.work / "locks" / "s-bbbb2222").exists())


class AcceptanceCommands(Project):
    """V16: a delivered point's own command ran after the last agent returned and did not fail. The Stop hook
    checked the project's check only; whether the point's criterion ran was the orchestrator's word."""

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        (self.root / "docs").mkdir()
        (self.root / "docs" / "auth.md").write_text("login\n", "utf-8")
        self.docket.write_text("## Points\n\n| # | Point | Evidence line expected | State |\n|---|---|---|---|\n"
                               "| 1 | login | `cargo test login → 3 passed` | [x] |\n"
                               "| 2 | piped | `cargo test \\| grep ok` → `ok` | [x] |\n"
                               "| 3 | doc | `docs/auth.md → contains login` | [x] |\n"
                               "| 4 | refresh | `cargo test refresh → 1 passed` | [~] |\n", "utf-8")

    def deliver(self, *steps):
        """Steps in order: a shell command, (command, "failed") or an agent kind."""
        entries = []
        for n, step in enumerate(steps):
            command, failed = step if isinstance(step, tuple) else (step, "")
            agent = command in ("implementer", "fixer", "reviewer")
            given = {"subagent_type": f"docket:{command}"} if agent else {"command": command}
            entries.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{n}", "name": "Agent" if agent else "Bash", "input": given}]}})
            entries.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{n}", "content": "x", "is_error": bool(failed)}]}})
        path = self.tmp / "acceptance.jsonl"
        path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        if "## Terminal state" not in self.docket.read_text("utf-8"):
            self.end_spec()
        return self.run_stop(transcript_path=str(path), background_tasks=[])

    def test_every_delivered_points_command_ran_after_the_last_change(self):
        code, err, _ = self.deliver("implementer", "reviewer", "cargo test login")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket gate] "), err)
        self.assertIn("point 2 (`cargo test | grep ok`) did not run in this session", err)
        self.assertNotIn("point 1", err)
        self.assertNotIn("point 3", err)  # a file and what it must contain: no command
        self.assertNotIn("point 4", err)  # a deviation delivers nothing
        self.assertEqual(self.state()["mode"], "sealed")
        code, err, _ = self.deliver("cargo test login", "implementer", "cargo test | grep ok", "reviewer")
        self.assertEqual(code, 2)
        self.assertIn("point 1 (`cargo test login`) last ran before the last agent returned", err)
        code, err, _ = self.deliver("implementer", "reviewer", ("cargo test login", "failed"), "cargo test | grep ok")
        self.assertEqual(code, 2)
        self.assertIn("point 1 (`cargo test login`) failed in its last run", err)
        code, err, _ = self.deliver("implementer", ("cargo test login", "failed"), "reviewer",
                                    "cd app && cargo test login", "cargo test | grep ok")
        self.assertTrue(err.startswith("[docket state] The series is over: 1 delivered"), err)
        self.assertEqual(self.state()["mode"], "none")

    def test_an_acceptance_command_before_the_reviewers_return_is_refused(self):
        # Review of 0d3bfb9 (D): the gate counted only implementer and fixer; the Festlegung „Umfang des Prüf-Tors“
        # says after the last agent return.
        code, err, _ = self.deliver("implementer", "cargo test login", "cargo test | grep ok", "reviewer")
        self.assertEqual(code, 2, err)
        self.assertIn("point 1 (`cargo test login`) last ran before the last agent returned", err)
        self.assertIn("point 2 (`cargo test | grep ok`) last ran before the last agent returned", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_prose_before_the_arrow_does_not_hold_delivered(self):
        # 1d.2: only what stands in backticks before the arrow is an acceptance command; prose the orchestrator checks.
        self.docket.write_text("## Points\n\n| # | Point | Evidence line expected | State |\n|---|---|---|---|\n"
                               "| 1 | page | the login page shows the user's name → yes | [x] |\n"
                               "| 2 | mixed | run `cargo test login` → 3 passed | [x] |\n"
                               "| 3 | login | `cargo test login` → 3 passed | [x] |\n", "utf-8")
        code, err, _ = self.deliver("implementer")
        self.assertEqual(code, 2)
        self.assertNotIn("point 1", err)
        self.assertNotIn("point 2", err)
        self.assertIn("point 3 (`cargo test login`) did not run in this session", err)
        code, err, _ = self.deliver("implementer", "cargo test login")
        self.assertEqual((code, self.state()["mode"]), (2, "none"), err)
        self.assertTrue(err.startswith("[docket state] The series is over: 1 delivered"), err)


class SeriesOnAbort(Project):
    """V13: an interrupted series is named with its titles and the state of each spec; a lock of a session gone
    quiet is taken over."""

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        for spec_id, title in (("s-aaaa1111", "Logout"), ("s-bbbb2222", "Profile")):
            (self.work / "specs" / "core" / f"{spec_id}-{title.lower()}.md").write_text(
                f"---\nid: {spec_id}\ntitle: {title}\nstate: ready\n---\n", "utf-8")

    def test_an_interrupted_series_is_named_with_its_titles(self):
        (self.work / "LOG.md").write_text(
            f"2026-11-05T10:00:00Z  {SPEC}  sealed             session=deadbeef\n"
            f"2026-11-05T10:00:01Z  {SPEC}  queued             session=deadbeef  s-aaaa1111, s-bbbb2222\n"
            f"2026-11-05T10:30:00Z  {SPEC}  delivered          session=deadbeef  spec closed\n"
            "2026-11-05T10:30:01Z  s-aaaa1111  sealed             session=deadbeef  from queue\n", "utf-8")
        _, _, out = run_hook("session_start.py", {"session_id": "fresh-session-3", "cwd": self.p, "source": "startup"}, self.p)
        self.assertIn("Interrupted series: “Login” delivered, “Logout” interrupted, “Profile” not started. "
                      "`/docket:implement s-aaaa1111, s-bbbb2222` resumes it.", out)
        self.assertNotIn("without a terminal state", out)

    def test_status_names_the_series_of_this_session(self):
        self.prompt(f"/implement {SPEC}, s-aaaa1111")
        _, _, out = self.prompt("/docket:status")
        self.assertIn("Series: “Login” running, “Logout” not started.", out)

    def test_a_lock_of_a_session_gone_quiet_is_taken_over(self):
        self.prompt(f"/implement {SPEC}")
        _, _, out = run_hook("user_prompt.py", {"session_id": "other-session-7", "cwd": self.p,
                                                "prompt": f"/implement {SPEC}"}, self.p)
        self.assertIn("runs in another session", out)
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)
        old = time.time() - 7200
        os.utime(self.root / ".claude" / "docket" / f"state-{SID}.json", (old, old))
        code, err, _ = run_hook("user_prompt.py", {"session_id": "other-session-7", "cwd": self.p,
                                                   "prompt": f"/implement {SPEC}"}, self.p)
        self.assertEqual(code, 0, err)
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), "other-session-7")


class ReviewFindings(Project):
    """The adversarial review of 0.3.0 (2026-11-06), one test per confirmed defect."""

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [x] |\n", "utf-8")
        self.path = self.tmp / "t.jsonl"

    def entries(self, *entries):
        self.path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", "utf-8")
        return str(self.path)

    def spawn(self, tool_id="toolu_1"):
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return [{"type": "assistant", "timestamp": now,
                 "message": {"content": [{"type": "tool_use", "id": tool_id, "name": "Agent", "input": {}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                          "content": "Async agent launched successfully.\nagentId: a1"}]}}]

    def queued(self, tool_id="toolu_1"):
        return {"type": "attachment", "attachment": {"type": "queued_command",
                "prompt": f"<task-notification>\n<tool-use-id>{tool_id}</tool-use-id>\n</task-notification>"}}

    def said(self, text="ok"):
        return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}

    def test_a_notification_the_model_saw_mid_turn_is_no_wait(self):
        # Live in experiment 12: the spec's end was waved through as a wait, the run stood still.
        self.end_spec()
        path = self.entries(*self.spawn(), self.queued(), self.said())
        code, err, _ = self.run_stop(transcript_path=path, background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertEqual(self.state()["mode"], "none")

    def test_a_queued_notification_not_yet_seen_still_waits(self):
        self.end_spec()
        path = self.entries(*self.spawn(), self.queued())
        self.assertEqual(self.run_stop(transcript_path=path, background_tasks=[])[0], 0)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_a_background_shell_does_not_hold_a_written_end(self):
        shell = [{"id": "b1", "type": "shell", "status": "running", "command": "npm run dev"}]
        self.assertEqual(self.run_stop(background_tasks=shell)[0], 0)  # no end written yet: a wait
        self.end_spec()
        self.assertEqual(self.run_stop(background_tasks=shell)[0], 2)
        self.assertEqual(self.state()["mode"], "none")

    def test_an_agent_still_running_holds_a_written_end(self):
        self.end_spec()
        agent = [{"id": "a1", "type": "subagent", "status": "running"}]
        self.assertEqual(self.run_stop(background_tasks=agent)[0], 0)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_the_terminal_lines_may_carry_bold_backticks_and_dashes(self):
        self.docket.write_text(self.docket.read_text("utf-8") + "\n## Review\n\n- code | no findings\n"
                               "- spec | no findings\n\n## Terminal state\n\n"
                               "- **state:** `DELIVERED`.\n- `result: Login works.`\n- open: none\n- user: keine\n", "utf-8")
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))

    def test_a_block_an_earlier_run_left_does_not_end_the_resumed_spec(self):
        self.end_spec("BLOCKED: start Docker", result="Nothing built.")
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("has no Terminal state", err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.end_spec()
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["outcomes"], {SPEC: "delivered"})

    def resume(self, point, terminal):
        """A docket an earlier run left with one point and a Terminal state, the spec marked again by a typed prompt."""
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.docket.write_text(f"# Docket\n| 1 | login | `cargo test` | {point} |\n\n## Terminal state\n\n"
                               f"- state: {terminal}\n", "utf-8")
        self.prompt(f"/docket:implement {SPEC}")
        self.assertEqual((self.state()["mode"], self.state()["terminal_base"]), ("sealed", 1))

    def test_a_block_rewritten_in_place_ends_the_resumed_spec(self):
        # Review 2026-11-12, as replayed: `[ ]` and BLOCKED, marked again, rewritten in place to `[x]` and DELIVERED.
        # Before the fix the Stop said "has no Terminal state; 0 of 1 points are open", the second Stop halted, and
        # the spec stayed in progress.
        self.resume("[ ]", "BLOCKED: three rounds")
        self.docket.write_text(f"# Docket\n| 1 | login | `cargo test` | [x] |\n\n## Terminal state\n\n"
                               "- state: DELIVERED\n", "utf-8")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket state] The series is over: 1 delivered, 0 blocked"), err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))
        self.assertIn("state: done", self.spec.read_text("utf-8"))
        self.assertIn(" delivered ", self.log())
        self.assertNotIn("stop-forced", self.log())

    def test_a_blocked_block_changed_in_place_closes_the_resumed_spec_as_blocked(self):
        self.resume("[ ]", "BLOCKED: three rounds")
        self.docket.write_text(self.docket.read_text("utf-8").replace("three rounds", "start Docker"), "utf-8")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2, err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "blocked"}))

    def test_a_delivered_block_from_a_halted_run_passes_the_gate_at_the_stop(self):
        self.resume("[x]", "DELIVERED")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2, err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))

    def test_a_delivered_block_from_a_halted_run_meets_the_gate(self):
        self.resume("[ ]", "DELIVERED")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket gate] The docket of Login (s-0123abcd) says DELIVERED; 1 point(s) "
                                       "still open"), err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_an_unchanged_blocked_or_dissent_block_does_not_close_the_resumed_spec(self):
        for kind in ("BLOCKED: three rounds", "DISSENT"):
            self.resume("[ ]", kind)
            code, err, _ = self.run_stop()
            self.assertEqual(code, 2, err)
            self.assertEqual(err.strip(), f"[docket state] The docket of Login (s-0123abcd) has no Terminal state from "
                                          f"this run: `state: {kind.split(':')[0]}` stood there when the run began; "
                                          "1 of 1 points are open.")
            self.assertEqual((self.state()["mode"], self.state()["spec"]), ("sealed", SPEC))
            self.assertNotIn("state: done", self.spec.read_text("utf-8"))
            self.end_spec()  # a new block below the old one
            self.docket.write_text(self.docket.read_text("utf-8").replace("[ ]", "[x]"), "utf-8")
            self.assertEqual(self.run_stop()[0], 2)
            self.assertEqual(self.state()["outcomes"], {SPEC: "delivered"})

    def test_only_a_changed_state_line_closes_the_resumed_spec(self):
        # Review of b0cb330 (Q3): an `open:` line deleted under the old BLOCKED closed the spec as blocked.
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [ ] |\n| 2 | logout | `cargo test` | [ ] |\n\n"
                               "## Terminal state\n\n- state: BLOCKED: three rounds\n"
                               "- open: point 1, cargo test fails\n", "utf-8")
        self.prompt(f"/docket:implement {SPEC}")
        self.docket.write_text(self.docket.read_text("utf-8").replace("| 1 | login | `cargo test` | [ ]",
                               "| 1 | login | `cargo test` | [x]").replace("- open: point 1, cargo test fails\n", ""),
                               "utf-8")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket state] The docket of Login (s-0123abcd) has no Terminal state from "
                                       "this run: `state: BLOCKED`"), err)
        self.assertIn("1 of 2 points are open", err)
        self.assertEqual((self.state()["mode"], self.state()["spec"]), ("sealed", SPEC))
        self.assertNotIn(" blocked ", self.log())

    def test_a_state_line_that_does_not_parse_leaves_the_old_block_unchanged(self):
        # Review of b0cb330 (P1): `State: ✅ DELIVERED` did not parse, its `result:` line hung on the old BLOCKED.
        self.resume("[ ]", "BLOCKED: three rounds")
        self.docket.write_text(self.docket.read_text("utf-8").replace("[ ]", "[x]")
                               + "- State: ✅ DELIVERED\n- result: Login works.\n", "utf-8")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket state] The docket of Login (s-0123abcd) has no Terminal state from "
                                       "this run"), err)
        self.assertEqual((self.state()["mode"], self.state().get("outcomes")), ("sealed", {}))
        self.assertIn("state: in-progress", self.spec.read_text("utf-8"))

    def test_a_background_shell_holds_the_stop_after_the_gate_refused_a_delivered_block_from_a_halted_run(self):
        # Review of b0cb330 (P2): a running build got the gate and the second Stop halted. Since the recheck of
        # 5c9abe1 (F2) the gap reaches the model first; the Stop right after it waits for the build instead of halting.
        self.resume("[ ]", "DELIVERED")
        shell = [{"id": "b1", "type": "shell", "status": "running", "command": "cargo build"}]
        code, err, _ = self.run_stop(background_tasks=shell)
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket gate] The docket of Login (s-0123abcd) says DELIVERED"), err)
        self.assertEqual(self.run_stop(active=True, background_tasks=shell), (0, "", ""))
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertNotIn("stop-forced", self.log())
        code, err, _ = self.run_stop(background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket gate] The docket of Login (s-0123abcd) says DELIVERED"), err)

    def test_resuming_the_spec_at_work_keeps_the_series(self):
        (self.work / "specs" / "core" / "s-aaaa1111-logout.md").write_text(
            "---\nid: s-aaaa1111\ntitle: Logout\nstate: ready\n---\n", "utf-8")
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}, s-aaaa1111")
        self.assertEqual(self.run_stop()[0], 2)
        code, _, out = self.run_stop(active=True)  # halted
        self.assertIn(f"`/docket:implement {SPEC}, s-aaaa1111` resumes it", json.loads(out)["systemMessage"])
        self.prompt(f"/implement {SPEC}")
        self.assertEqual((self.state()["series"], self.state()["queue"]), ([SPEC, "s-aaaa1111"], ["s-aaaa1111"]))

    def test_a_stopped_series_is_reported_as_stopped_with_the_stopped_spec_to_resume(self):
        (self.work / "specs" / "core" / "s-aaaa1111-logout.md").write_text(
            "---\nid: s-aaaa1111\ntitle: Logout\nstate: ready\n---\n", "utf-8")
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}, s-aaaa1111")
        self.prompt("/docket:stop")
        self.end_spec("BLOCKED: stopped by user", result="Half of the login is built.")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "[docket state] The series is over: 0 delivered, 1 blocked, 1 not started "
                                      "(“Logout” after /docket:stop).")
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "blocked"}))
        self.assertIn("state: ready", self.spec.read_text("utf-8"))

    def test_a_check_counts_only_after_a_background_agent_returned(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        check = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b1", "name": "Bash",
                                                               "input": {"command": "cargo test"}}]}}
        note = {"type": "user", "message": {"content": "<task-notification>\n<tool-use-id>toolu_1</tool-use-id>\n</task-notification>"}}
        code, err, _ = self.run_stop(transcript_path=self.entries(*self.spawn(), check, note), background_tasks=[])
        self.assertEqual(code, 2)
        self.assertIn("before the last agent returned", err)
        code, err, _ = self.run_stop(transcript_path=self.entries(*self.spawn(), note, check), background_tasks=[])
        self.assertEqual(code, 2)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))

    HAND_BACK = {"type": "user", "message": {"content": 'Another Claude session sent a message:\n<agent-message from="a1">'
                                                        "\n[Subagent hand-back] Review: no findings.\n</agent-message>"}}
    CHECK = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b1", "name": "Bash",
                                                           "input": {"command": "cargo test"}}]}}

    def test_a_repeated_notification_after_the_check_does_not_move_the_agents_return(self):
        # The agent's report reached the model as a hand-back, the check ran, then the same notification was written
        # again as a queued_command: the agent returned before the check.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop(transcript_path=self.entries(*self.spawn(), self.HAND_BACK, self.CHECK,
                                                                  self.queued(), self.said()), background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertNotIn("before the last agent returned", err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))

    def test_a_check_before_the_agents_hand_back_is_still_refused(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop(transcript_path=self.entries(*self.spawn(), self.CHECK, self.HAND_BACK,
                                                                  self.queued(), self.said()), background_tasks=[])
        self.assertEqual(code, 2)
        self.assertIn("before the last agent returned", err)

    def test_after_a_send_message_the_agents_next_result_is_a_new_return(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        resume = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "m1", "name": "SendMessage",
                                                                "input": {"to": "a1", "message": "check once more"}}]}}
        code, err, _ = self.run_stop(transcript_path=self.entries(*self.spawn(), self.HAND_BACK, self.CHECK, resume,
                                                                  self.NEW_HAND_BACK), background_tasks=[])
        self.assertEqual(code, 2)
        self.assertIn("before the last agent returned", err)

    NEW_HAND_BACK = {"type": "user", "message": {"content": 'Another Claude session sent a message:\n<agent-message '
                                                            'from="a1">\n[Subagent hand-back] Review: one finding, fixed.'
                                                            "\n</agent-message>"}}
    RESUME = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "m1", "name": "SendMessage",
                                                            "input": {"to": "a1", "message": "check once more"}}]}}

    def test_the_notification_after_the_hand_back_of_the_same_stop_does_not_move_the_return(self):
        # wrong-fact-2 2026-11-12, real format: the reviewer's report came as a hand-back, the check ran, then the
        # notification as a queued_command whose result only points to the hand-back; the gate refused the check.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        spawn = [{"type": "assistant", "timestamp": now, "message": {"content": [
                     {"type": "tool_use", "id": "toolu_01S1V", "name": "Agent", "input": {"run_in_background": True}}]}},
                 {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_01S1V", "content": [
                     {"type": "text", "text": "Async agent launched successfully.\nagentId: aca2cb2e (internal ID)"}]}]}}]
        hand_back = {"type": "user", "isMeta": True, "message": {"role": "user", "content":
                     'Another Claude session sent a message:\n<agent-message from="aca2cb2e">\n[Subagent hand-back] '
                     "The report follows:\n  no findings\n  Unverified: none\n</agent-message>\n\nThat \"other Claude "
                     "session\" is an agent working inside this same session."}}
        notification = {"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "task-notification",
                        "prompt": "<task-notification>\n<task-id>aca2cb2e</task-id>\n<tool-use-id>toolu_01S1V</tool-use-id>"
                                  "\n<status>completed</status>\n<summary>Agent \"Review spec angle\" finished</summary>\n"
                                  "<result>This agent's report was delivered to you as a message from \"aca2cb2e\" (its "
                                  "SubagentHandback call). Read it there; it is not repeated here.\n</result>\n"
                                  "</task-notification>"}}
        code, err, _ = self.run_stop(transcript_path=self.entries(*spawn, self.said(), hand_back, self.CHECK,
                                                                  notification, self.said()), background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertNotIn("before the last agent returned", err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))

    def test_a_resumed_foreground_agents_new_result_moves_its_return(self):
        # Review of 0d3bfb9 (B): only a background agent got a new return after a SendMessage.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        spawn = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent",
                                                               "input": {}}]}},
                 {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                           "content": "Review: no findings.\nagentId: a1"}]}}]
        code, err, _ = self.run_stop(transcript_path=self.entries(*spawn, self.CHECK, self.RESUME, self.NEW_HAND_BACK,
                                                                  self.said()), background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertIn("before the last agent returned", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_a_stale_repeat_after_a_send_message_leaves_the_return_to_the_new_result(self):
        # Review of 0d3bfb9 (C): the old notification, repeated after the SendMessage, took the agent's return.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop(transcript_path=self.entries(*self.spawn(), self.HAND_BACK, self.RESUME,
                                                                  self.queued(), self.said(), self.CHECK,
                                                                  self.NEW_HAND_BACK, self.said()), background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertIn("before the last agent returned", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def returns(self, path):
        sys.path.insert(0, str(HOOKS))
        import docket_lib
        return docket_lib.agent_returns(path)

    def test_a_resumed_agents_report_equal_to_its_first_moves_its_return(self):
        # Recheck of 5c9abe1 (F1): the texts seen survived the SendMessage, the resumed agent's same report was dropped
        # and its return stayed at the first stop, before the check.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        spawn = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent",
                                                               "input": {}}]}},
                 {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                           "content": "1. done\nUnverified: none\nagentId: a1"}]}}]
        same = {"type": "user", "message": {"content": 'Another Claude session sent a message:\n<agent-message from="a1">'
                                                       "\n[Subagent hand-back] 1. done\n  Unverified: none\n</agent-message>"}}
        path = self.entries(*spawn, self.CHECK, self.RESUME, same, self.said())
        self.assertEqual(self.returns(path), {"toolu_1": 4})
        code, err, _ = self.run_stop(transcript_path=path, background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertIn("before the last agent returned", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_a_refused_spawn_is_no_return(self):
        # Recheck of 5c9abe1 (F3): an Agent call a hook refused counted as a return, the check before it was refused.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.end_spec()
        refused = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_2", "name": "Agent",
                                                                 "input": {"subagent_type": "docket:fixer"}}]}},
                   {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_2",
                                                             "is_error": True,
                                                             "content": "PreToolUse:Agent hook error: [docket] no"}]}}]
        path = self.entries(self.CHECK, *refused, self.said())
        self.assertEqual(self.returns(path), {})
        code, err, _ = self.run_stop(transcript_path=path, background_tasks=[])
        self.assertEqual(code, 2, err)
        self.assertNotIn("before the last agent returned", err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))

    def test_a_dev_server_lets_the_gap_of_a_delivered_block_from_a_halted_run_reach_the_model(self):
        # Review of 0d3bfb9 (A): a background shell that never ends (a dev server for an e2e acceptance command) was a
        # wait at every Stop while the old DELIVERED counted as not written. Recheck of 5c9abe1 (F2): with the check
        # missing it still waited silently, the orchestrator never learned the gap and the run idled.
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| check | `cargo test` | |\n", "utf-8")
        self.resume("[x]", "DELIVERED")
        server = [{"id": "b1", "type": "shell", "status": "running", "command": "npm run dev"}]
        code, err, _ = self.run_stop(transcript_path=self.entries(self.said()), background_tasks=server)
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket gate] The docket of Login (s-0123abcd) says DELIVERED"), err)
        self.assertIn("cargo test", err)
        self.assertEqual(self.state()["mode"], "sealed")
        code, err, _ = self.run_stop(transcript_path=self.entries(self.CHECK, self.said()), background_tasks=server)
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("[docket state] The series is over: 1 delivered"), err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "delivered"}))

    def test_the_lock_is_exclusive_and_a_live_holder_keeps_it(self):
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.assertEqual(self.prompt(f"/implement {SPEC}")[0], 0)
        code, err, out = run_hook("user_prompt.py", {"session_id": "other-session-8", "cwd": self.p,
                                                     "prompt": f"/implement {SPEC}"}, self.p)
        self.assertEqual(code, 0, err)
        self.assertIn("runs in another session", out)
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)


class ExperimentTwelve(Project):
    """Experiment 12 (2026-11-06): the first closing message of a real series said "Du musst: keine Schritte nötig"
    twice, the same decision twice and ended cut lines with "….". """

    def test_metrics_count_agents_that_started(self):
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        (self.work / "LOG.md").write_text(f"2026-11-06T10:00:00Z  {SPEC}  sealed             session=test-ses\n", "utf-8")
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        call = lambda i: {"type": "assistant", "timestamp": now, "message": {"content": [
            {"type": "tool_use", "id": f"f{i}", "name": "Agent", "input": {"subagent_type": "docket:fixer"}}]}}
        refused = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "f1", "is_error": True,
                                                            "content": "[docket] The docket is yours"}]}}
        path = self.tmp / "t.jsonl"
        back = lambda tool_id: {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": "done"}]}}
        review = {"type": "assistant", "timestamp": now, "message": {"content": [
            {"type": "tool_use", "id": "r1", "name": "Agent", "input": {"subagent_type": "docket:reviewer", "model": "opus"}}]}}
        path.write_text("\n".join(json.dumps(e) for e in (call(1), refused, call(2), back("f2"), review, back("r1")))
                        + "\n", "utf-8")
        self.docket.write_text("| 1 | x | `x` | [x] |\n", "utf-8")
        self.end_spec()
        self.run_stop(transcript_path=str(path), background_tasks=[])
        metrics = json.loads((self.work / "metrics" / f"{SPEC}.json").read_text("utf-8"))
        self.assertEqual(metrics["agents"], {"fixer": 1, "reviewer": 1})
        self.assertEqual(metrics["reviewer_models"], ["opus"])


class PaneData(Project):
    """hooks/docket_ui.py: what the pane shows and sets, read and written with the hooks' own functions."""

    def ui(self, action, *args, session=SID):
        result = subprocess.run([sys.executable, str(HOOKS / "docket_ui.py"), action, "--root", self.p,
                                 "--session", session, *args], capture_output=True,
                                env={k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"})
        return result.returncode, json.loads(result.stdout.decode("utf-8"))

    def spec_file(self, spec_id, title, state="ready", follows="", concept="", body=""):
        path = self.work / "specs" / "core" / f"{spec_id}-{title.lower()}.md"
        path.write_text(f"---\nid: {spec_id}\ntitle: {title}\nstate: {state}          # draft | ready\n"
                        f"concept: {concept}\nfollows: {follows}\ncreated: 2026-11-02\n---\n# {title}\n{body}", "utf-8")
        return path

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\nconcept: c-11112222\nfollows:\n"
                             "created: 2026-11-01\n---\n# Login\n", "utf-8")
        (self.work / "concepts" / "core" / "c-11112222-accounts.md").write_text(
            "---\nid: c-11112222\ntopic: core\ntitle: Accounts\nstate: agreed\n---\n# Accounts\n\n## Open decisions (user)\n\n"
            "| Question | Recommendation | Answer (date) |\n|---|---|---|\n| SSO? | no | |\n| MFA? | yes | yes (2026-11-01) |\n",
            "utf-8")
        self.spec_file("s-aaaa1111", "Logout", follows=SPEC, concept="c-11112222")
        self.spec_file("s-bbbb2222", "Profile", state="draft",
                       body="\n## User decisions\n\n| Question | Recommendation | Answer (date) |\n|---|---|---|\n"
                            "| Avatar? | yes | |\n\n## Rewrite\n")
        self.spec_file("s-cccc3333", "Signup", state="done")

    def test_overview_counts_the_states_and_names_what_holds_a_spec(self):
        (self.work / "dockets" / "core" / "s-cccc3333.md").write_text(
            "## Points\n| 1 | a | `a` | [x] |\n| 2 | b | `b` | [~] |\n\n## Terminal state\n\n- state: DELIVERED\n"
            "- result: Signup works.\n", "utf-8")
        with open(self.work / "LOG.md", "a", encoding="utf-8", newline="\n") as log:
            log.write("2026-11-06T11:30:00Z  s-cccc3333  delivered          session=abcd1234  spec closed\n")
        code, data = self.ui("overview")
        self.assertEqual(code, 0, data)
        self.assertTrue(data["initialized"])
        self.assertEqual(data["counts"], {"draft": 1, "ready": 2, "in-progress": 0, "done": 1, "superseded": 0,
                                          "open": 3, "total": 4})
        specs = {s["id"]: s for s in data["specs"]}
        self.assertEqual(specs["s-aaaa1111"]["waits_for"], [SPEC])
        self.assertLess(specs[SPEC]["order"], specs["s-aaaa1111"]["order"])  # Logout follows Login
        # `after:` in the goal holds a spec back in the pane too: its place in the order, and what it waits for.
        self.spec_file("s-dddd4444", "Import", body="\n## Goal (WHAT)\n\nUsers import a file. `after: s-aaaa1111`.\n")
        (self.work / "specs" / "core" / "s-dddd4444-import.md").write_text(
            (self.work / "specs" / "core" / "s-dddd4444-import.md").read_text("utf-8")
            .replace("created: 2026-11-02", "created: 2026-10-01"), "utf-8")
        later = {s["id"]: s for s in self.ui("overview")[1]["specs"]}
        self.assertEqual(later["s-dddd4444"]["waits_for"], ["s-aaaa1111"])
        self.assertLess(later["s-aaaa1111"]["order"], later["s-dddd4444"]["order"])  # the oldest spec, yet after Logout
        self.assertEqual(sorted(s["order"] for s in data["specs"]), [0, 1, 2, 3])
        self.assertEqual(specs["s-aaaa1111"]["concept"], "c-11112222")
        self.assertEqual(specs["s-bbbb2222"]["concept"], "")
        self.assertEqual(specs["s-bbbb2222"]["decisions"], 1)
        self.assertEqual(specs["s-cccc3333"]["result"], {"kind": "delivered", "text": "Signup works."})
        self.assertEqual(specs["s-cccc3333"]["points"], {"done": 1, "deviating": 1, "open": 0})
        # When a finished spec was closed, from its LOG line: the pane lists the one closed last first.
        self.assertEqual(specs["s-cccc3333"]["closed"], "2026-11-06T11:30:00Z")
        self.assertEqual(specs[SPEC]["closed"], "")
        self.assertTrue(specs[SPEC]["path"].endswith(f"{SPEC}-login.md"))
        self.assertEqual([(c["id"], c["title"], c["state"], c["decisions"]) for c in data["concepts"]],
                         [("c-11112222", "Accounts", "agreed", 1)])
        self.assertEqual(data["runs"], [])

    def test_overview_of_a_project_without_docket_md_says_so(self):
        (self.work / "DOCKET.md").unlink()
        code, data = self.ui("overview")
        self.assertEqual((code, data["initialized"]), (0, False))
        self.assertNotIn("specs", data)

    def test_overview_names_the_run_of_this_session_and_its_place_in_the_series(self):
        self.assertEqual(self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")[0], 0)
        self.docket.write_text("## Points\n| 1 | a | `a` | [x] |\n| 2 | b | `b` | [ ] |\n", "utf-8")
        _, data = self.ui("overview")
        self.assertEqual(len(data["runs"]), 1)
        run = data["runs"][0]
        self.assertEqual((run["mine"], run["spec"], run["title"], run["i"], run["n"]), (True, SPEC, "Login", 1, 2))
        self.assertEqual(run["points"], {"done": 1, "deviating": 0, "open": 1})
        self.assertEqual({s["id"]: s["running"] for s in data["specs"]}[SPEC], SID)
        self.assertEqual(data["counts"]["in-progress"], 1)
        self.assertTrue(any(" sealed " in line for line in data["log"]))

    def test_supersede_writes_the_state_keeps_the_comment_and_logs_the_successor(self):
        code, data = self.ui("supersede", "--spec", "s-bbbb2222", "--by", "s-aaaa1111")
        self.assertEqual((code, data["ok"]), (0, True), data)
        text = (self.work / "specs" / "core" / "s-bbbb2222-profile.md").read_text("utf-8")
        self.assertIn("state: superseded          # draft | ready", text)
        self.assertRegex(self.log(), r"s-bbbb2222\s+superseded\s+session=test-ses\s+by=s-aaaa1111")
        _, after = self.ui("overview")
        self.assertEqual({s["id"]: s["superseded_by"] for s in after["specs"]}["s-bbbb2222"], "s-aaaa1111")
        self.assertEqual(after["counts"]["superseded"], 1)

    def test_supersede_refuses_a_running_spec_an_unknown_successor_and_itself(self):
        self.assertEqual(self.ui("supersede", "--spec", SPEC, "--by", SPEC)[1]["error"], "unknown-successor")
        self.assertEqual(self.ui("supersede", "--spec", SPEC, "--by", "s-dddd4444")[1]["error"], "unknown-successor")
        self.assertEqual(self.ui("supersede", "--spec", "s-dddd4444", "--by", SPEC)[1]["error"], "unknown-spec")
        self.assertEqual(self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")[0], 0)
        code, data = self.ui("supersede", "--spec", SPEC, "--by", "s-cccc3333", session="other-session")
        self.assertEqual((code, data["error"]), (1, "running"))
        self.assertEqual(self.ui("supersede", "--spec", "s-aaaa1111", "--by", "s-cccc3333",
                                 session="other-session")[1]["error"], "queued")
        self.assertIn("state: in-progress", self.spec.read_text("utf-8"))

    def test_stop_records_the_request_for_a_running_session_only(self):
        self.assertEqual(self.ui("stop")[1]["error"], "not-running")
        self.assertEqual(self.prompt(f"/docket:implement {SPEC}")[0], 0)
        code, data = self.ui("stop")
        self.assertEqual((code, data["ok"], data["spec"]), (0, True, SPEC))
        self.assertTrue(self.state()["stop_requested"])
        self.assertIn("stop-requested", self.log())
        self.assertTrue(self.ui("overview")[1]["runs"][0]["stop_requested"])


class SessionStateRace(Project):
    """One state file, many hooks at once. luradb-web-client 2026-11-10: a message started 14 agents, their hooks
    wrote the state in the same second, a read that failed on Windows counted as mode none and went back to disk; the
    spec session had lost its mode, and 15 agents' Writes were refused after 10-25 minutes of work."""

    WORKER = ("import sys, types\n"
              "sys.path.insert(0, sys.argv[1])\n"
              "import docket_lib as d\n"
              "paths = types.SimpleNamespace(state_dir=sys.argv[2], config=sys.argv[5])\n"
              "lost = 0\n"
              "for n in range(int(sys.argv[4])):\n"
              "    state = d.load_state(paths, sys.argv[3])\n"
              "    lost += state.get('mode') != 'sealed'\n"
              "    state['refused'] = bool(n % 2)\n"
              "    d.save_state(paths, sys.argv[3], state)\n"
              "print(lost)\n")

    def lib(self):
        sys.path.insert(0, str(HOOKS))
        import docket_lib
        return docket_lib, type("P", (), {"state_dir": f"{self.p}/.claude/docket",
                                          "config": str(self.work / "DOCKET.md")})()

    def leftovers(self):
        return sorted(f.name for f in (self.root / ".claude" / "docket").iterdir()
                      if f.name.endswith((".tmp", ".lock")))

    def test_fifteen_writers_at_once_keep_the_mode(self):
        self.set_state(mode="sealed", read=["a", "b"])
        state_dir = f"{self.p}/.claude/docket"
        workers = [subprocess.Popen([sys.executable, "-c", self.WORKER, str(HOOKS), state_dir, SID, "25",
                                     str(self.work / "DOCKET.md")],
                                    stdout=subprocess.PIPE) for _ in range(15)]
        lost = sum(int(worker.communicate()[0].decode() or 1) for worker in workers)
        self.assertEqual(lost, 0, "a hook read the default state while the file existed")
        self.assertEqual((self.state()["mode"], self.state()["read"]), ("sealed", ["a", "b"]))
        self.assertEqual(self.leftovers(), [])

    def test_hooks_of_one_message_keep_the_mode(self):
        # The hooks as Claude Code starts them: one process per tool call, all in the same moment. Each one finds the
        # refusal marker set and clears it.
        payload = json.dumps({"session_id": SID, "cwd": self.p, "tool_name": "Agent",
                              "tool_input": {"description": "draft"}}).encode("utf-8")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        env["CLAUDE_PROJECT_DIR"] = self.p
        for _ in range(3):
            self.set_state(mode="sealed", spec=SPEC, refused=True)
            hooks = [subprocess.Popen([sys.executable, str(HOOKS / "post_tool_use.py")], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) for _ in range(14)]
            for hook in hooks:
                out = hook.communicate(payload)[0].decode("utf-8")
                self.assertEqual(out, "")
            self.assertEqual((self.state()["mode"], self.state()["spec"], self.state()["refused"]),
                             ("sealed", SPEC, False))
        self.assertEqual(self.leftovers(), [])

    def test_a_hook_with_an_older_state_does_not_undo_a_newer_mode(self):
        d, paths = self.lib()
        self.set_state(mode="none")
        older = d.load_state(paths, SID)  # a hook that started before the user's command
        newer = d.load_state(paths, SID)
        newer.update({"mode": "sealed", "last_command": "/docket:implement"})
        d.save_state(paths, SID, newer)  # the command's hook
        older["clock"] = "2026-11-10 12:53"
        d.save_state(paths, SID, older)
        self.assertEqual((self.state()["mode"], self.state()["last_command"], self.state()["clock"]),
                         ("sealed", "/docket:implement", "2026-11-10 12:53"))

    def test_a_hook_that_found_no_state_does_not_undo_the_first_command(self):
        # SessionStart and the hook of the session's first prompt run at the same moment.
        d, paths = self.lib()
        start = d.load_state(paths, SID)  # no file yet
        prompt = d.load_state(paths, SID)
        prompt.update({"mode": "sealed"})
        d.save_state(paths, SID, prompt)
        start.update({"stop_requested": True})
        d.save_state(paths, SID, start)
        self.assertEqual(self.state(), {"version": 2, "mode": "sealed", "spec": "", "stop_requested": True})

    def test_a_state_that_could_not_be_read_is_never_written_back(self):
        d, paths = self.lib()
        self.set_state(mode="sealed", queue=["a"])
        read_json = d.read_json
        try:
            d.read_json = lambda path, wait=0: (None, "unreadable")
            blind = d.load_state(paths, SID)
            self.assertTrue(blind.unreadable)
            blind["clock"] = "2026-11-10 12:53"
            d.save_state(paths, SID, blind)  # the file is still closed to this hook: nothing is written
            self.assertEqual(self.state(), {"version": 2, "mode": "sealed", "spec": "", "queue": ["a"]})
        finally:
            d.read_json = read_json
        d.save_state(paths, SID, blind)  # readable again: only what the hook changed goes in
        self.assertEqual((self.state()["mode"], self.state()["queue"], self.state()["clock"]),
                         ("sealed", ["a"], "2026-11-10 12:53"))

    def test_a_file_held_open_by_another_process_is_read_after_a_wait(self):
        d, paths = self.lib()
        self.set_state(mode="sealed")
        calls = []
        opened = open

        def flaky(path, *args, **kwargs):
            if str(path).endswith(f"state-{SID}.json") and len(calls) < 3:
                calls.append(path)
                raise PermissionError(13, "The process cannot access the file")
            return opened(path, *args, **kwargs)

        d.open = flaky  # the module's own name for open: only docket_lib sees it
        try:
            self.assertEqual(d.load_state(paths, SID)["mode"], "sealed")
        finally:
            del d.open
        self.assertEqual(len(calls), 3)

    def test_an_agents_tool_call_writes_nothing_into_the_state(self):
        for mode in ("none", "sealed"):
            self.set_state(mode=mode, spec=SPEC, clock="2026-11-10 12:00", refused=True)
            before = (self.root / ".claude" / "docket" / f"state-{SID}.json").read_text("utf-8")
            code, err, out = run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read",
                                                           "agent_id": "a1", "tool_input": {"file_path": "x.md"}}, self.p)
            self.assertEqual((code, err, out), (0, "", ""))
            self.assertEqual((self.root / ".claude" / "docket" / f"state-{SID}.json").read_text("utf-8"), before)

    def test_an_agents_tool_call_keeps_a_run_live(self):
        self.set_state(mode="sealed", spec=SPEC)
        path = self.root / ".claude" / "docket" / f"state-{SID}.json"
        os.utime(path, (time.time() - 7200, time.time() - 7200))
        run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read", "agent_id": "a1",
                                      "tool_input": {"file_path": "x.md"}}, self.p)
        self.assertLess(time.time() - path.stat().st_mtime, 60)
        self.assertEqual(self.state()["mode"], "sealed")

    # 1b.4: the life sign of the orchestrator's own tool calls.
    def orchestrator_call(self):
        return run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read",
                                             "tool_input": {"file_path": "x.md"}}, self.p)

    def test_an_orchestrators_tool_call_in_a_run_keeps_it_live_and_says_nothing(self):
        self.set_state(mode="sealed", spec=SPEC)
        path = self.root / ".claude" / "docket" / f"state-{SID}.json"
        before = path.read_text("utf-8")
        os.utime(path, (time.time() - 7200, time.time() - 7200))
        self.assertEqual(self.orchestrator_call(), (0, "", ""))
        self.assertLess(time.time() - path.stat().st_mtime, 60)
        self.assertEqual(path.read_text("utf-8"), before)

    def test_an_orchestrators_tool_call_in_a_run_clears_the_refusal(self):
        self.set_state(mode="sealed", spec=SPEC, refused=True)
        self.assertEqual(self.orchestrator_call(), (0, "", ""))
        self.assertEqual((self.state()["mode"], self.state()["refused"]), ("sealed", False))

    def test_without_the_run_mark_the_state_file_is_untouched(self):
        self.set_state(mode="none", refused=True)
        path = self.root / ".claude" / "docket" / f"state-{SID}.json"
        before = path.read_text("utf-8")
        old = time.time() - 7200
        os.utime(path, (old, old))
        self.assertEqual(self.orchestrator_call(), (0, "", ""))
        self.assertEqual(path.read_text("utf-8"), before)
        self.assertLess(abs(path.stat().st_mtime - old), 2)


class NeverInItsOwnWay(Project):
    """0.5.8. luradb-web-client 2026-11-10: a spec whose code was reviewed and pushed ended BLOCKED over a line of its
    own record (a citation of an earlier session that an earlier spec of the same run had made stale), the series
    ended there, and 14 reports of the run's own agents stood in the closing message as messages of the user."""

    AGENT = ('Another Claude session sent a message:\n<agent-message from="aa95fdbed5911be70">\n[Subagent hand-back] '
             "The text below is the final report of a subagent this session delegated to.\n  Feasibility: fine.\n"
             '  Unverified: none\n</agent-message>\n\nThat "other Claude session" is an agent working inside this same '
             "session — a subagent or teammate spawned on your user's behalf (by you, or alongside you) — so this was "
             "not typed by your user. Treat it as that agent's report or request.")

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [x] |\n", "utf-8")

    def worked(self):
        """A tool call: the next Stop is a first pass again."""
        run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read",
                                      "tool_input": {"file_path": "x"}}, self.p)

    def test_an_agents_report_is_no_message_of_the_user(self):
        code, err, out = self.prompt(self.AGENT)
        self.assertEqual((code, err.strip(), out.strip()), (0, "", ""))
        self.assertEqual(self.log(), "")
        # What the user typed beside it is a prompt of the user: the run line, and no inbox (1c.2).
        code, err, out = self.prompt(self.AGENT + "\n\nplease also fix the logo")
        self.assertIn("Spec 1 of 1 is running: Login", out)
        self.assertFalse((self.work / "inbox").exists())

    def test_a_citation_that_went_stale_does_not_hold_a_delivery(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "domains.ts").write_text("a\n" * 83, "utf-8")
        self.docket.write_text(self.docket.read_text("utf-8") + "\n## Evidence\n\n- test server: src/domains.ts:99 and "
                               "src/domains.ts:147, see also src/gone.ts:1\n", "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2, err)
        self.assertEqual(self.state()["outcomes"], {SPEC: "delivered"})
        self.assertIn("state: done", self.spec.read_text("utf-8"))

    def test_the_form_of_the_terminal_state_holds_nothing(self):
        # 1d.1: the form of a Terminal state is no gate: a BLOCKED without a reason ends the spec at once, and so does
        # a DELIVERED with a long result, a proposal without its parts, a deferred finding and a red CI line.
        self.end_spec("BLOCKED", result="Login is half built.")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket state] The series is over: 0 delivered, 1 blocked"), err)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("none", {SPEC: "blocked"}))
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC], terminal_base=1)
        self.docket.write_text(self.docket.read_text("utf-8") + "\n## Review\n\n- code | an old error line | "
                               "src/admin.ts:3 | test | deferred\n\n## Evidence\n\n- point 1: ok. CI: red\n", "utf-8")
        self.end_spec(result="Login works " + "and works " * 30, lines=[
            "decision: I advise you to have wrong input handled, because the login accepts an empty password. "
            "Command: /docket:spec", "repaired: " + "the command that fetches the types " * 6])
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("[docket state] The series is over: 1 delivered"), err)
        self.assertNotIn(" waived ", self.log())

    def test_the_evidence_of_a_delivery_is_never_let_pass(self):
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [ ] |\n", "utf-8")
        self.end_spec()
        for _ in range(4):
            code, err, _ = self.run_stop()
            self.assertEqual(code, 2)
            self.assertIn("still open", err)
            self.assertEqual(self.state()["mode"], "sealed")
            self.worked()
        self.assertNotIn(" waived ", self.log())


class RecordsInAnotherRepository(Project):
    """luradb-web-client 2026-11-10: the dockets lay in a concept repository beside the code, the closing message said
    "(nicht committet)" and named no step."""

    def setUp(self):
        super().setUp()
        concept = self.root / "concept"
        shutil.copytree(self.work, concept / "docket")
        self.work = concept / "docket"
        (self.root / ".claude" / "docket.json").write_text(json.dumps({"work_dir": str(self.work)}), "utf-8")
        self.spec = self.work / "specs" / "core" / f"{SPEC}-login.md"
        self.docket = self.work / "dockets" / "core" / f"{SPEC}.md"
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.git = lambda *args: subprocess.run(["git", "-C", str(concept)] + list(args), capture_output=True,
                                                check=True, text=True).stdout
        self.git("init", "-q")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@t")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [x] |\n", "utf-8")

    def test_uncommitted_records_are_named_with_their_commands_at_the_end_of_the_series(self):
        self.end_spec()
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), '[docket state] The series is over: 1 delivered, 0 blocked, 0 not started. The '
                                      'records are not committed: `git -C concept add docket`, then `git -C concept '
                                      'commit -m "docket: records" -- docket`.')
        self.assertNotIn("docket: records", self.git("log", "--oneline"))

    def test_the_hook_commits_them_itself_where_the_project_says_so(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n"
                                             "| commit_records | `yes` | |\n", "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), "[docket state] The series is over: 1 delivered, 0 blocked, 0 not started.")
        self.assertEqual(self.git("log", "--oneline").count("docket: records"), 1)  # once, after the last LOG line
        self.assertIn("run-closed", self.git("show", "HEAD:docket/LOG.md"))
        self.assertEqual(self.run_stop("Run finished.", active=True), (0, "", ""))
        self.assertEqual(self.git("status", "--porcelain").strip(), "")

    def test_a_handover_commits_nothing(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n"
                                             "| commit_records | `yes` | |\n", "utf-8")
        (self.work / "specs" / "core" / "s-aaaa1111-logout.md").write_text(
            "---\nid: s-aaaa1111\ntitle: Logout\nstate: ready\n---\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC, "s-aaaa1111"], queue=["s-aaaa1111"])
        self.end_spec()
        code, err, _ = self.run_stop()
        self.assertTrue(err.startswith("[docket state] Spec 2 of 2 is running: Logout"), err)
        self.assertNotIn("docket: records", self.git("log", "--oneline"))


if __name__ == "__main__":
    unittest.main()
