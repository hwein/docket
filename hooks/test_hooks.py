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
from datetime import datetime, timedelta
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


def hstamp(minutes_off=0):
    when = datetime.now() + timedelta(minutes=minutes_off)
    return f"stamp: {when:%Y-%m-%d %H:%M}"


def stamp(minutes_off=0, counts="1/1"):
    when = datetime.now() + timedelta(minutes=minutes_off)
    return f"stamp: {when:%Y-%m-%d %H:%M} {SPEC}" + (f" {counts}" if counts else "")


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
        base = {"version": 1, "mode": "none", "spec": "", "degraded": False, "read": []}
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

    def end_spec(self, kind="DELIVERED", result="Login works, three tests are green.", lines=(), spec_id=SPEC,
                 review=True):
        """0.3.0 (V3): a spec ends in its docket's Terminal state section; DELIVERED also needs a decided review."""
        docket = self.work / "dockets" / "core" / f"{spec_id}.md"
        body = docket.read_text("utf-8") if docket.exists() else "# Docket\n"
        if review and "## Review" not in body:
            body += "\n## Review\n\n- code | no findings\n- spec | no findings\n"
        body += "\n## Terminal state\n\n" + f"- state: {kind}\n" + (f"- result: {result}\n" if result else "")
        docket.write_text(body + "".join(f"- {line}\n" for line in lines), "utf-8")

    def finish(self, message, **extra):
        """A 0.2.9 terminal message as a 0.3.0 end: its kind goes into the docket's Terminal state and the Stop has no
        text. 0 when the spec ended (the hook moved on to the closing), else the hook's answer."""
        kind = message.split()[1] if message.startswith("DOCKET ") else "DELIVERED"
        if kind == "DELIVERED":
            self.end_spec()
        else:
            self.end_spec("BLOCKED: start Docker" if kind == "BLOCKED" else kind, review=False,
                          lines=["dissent: a, per src/a.rs", "dissent: b, per src/b.rs"] if kind == "DISSENT" else ())
        code, err, out = self.run_stop(**extra)
        if code == 2 and self.state().get("mode") == "closing":
            return 0, err, out
        return code, err, out

    def closing(self):
        path = self.root / ".claude" / "docket" / f"closing-{SID}.md"
        return path.read_text("utf-8") if path.exists() else ""

    def closing_lines(self):
        """The closing message without the blank lines between its blocks."""
        return [line for line in self.closing().splitlines() if line]


class SessionStart(Project):
    def test_marks_degraded_and_names_required_reading(self):
        code, err, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p)
        self.assertEqual(code, 0, err)
        self.assertIn("Required reading", out)
        self.assertIn("DOCKET.md", out)
        self.assertTrue(self.state()["degraded"])

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
        self.assertIn("Mode sealed", out)

    def test_implement_without_id_and_without_a_ready_spec_seals_nothing_and_says_why(self):
        self.spec.write_text(f"---\nid: {SPEC}\nstate: draft\n---\n# Login\n", "utf-8")
        code, err, out = self.prompt("/implement")
        self.assertEqual(code, 0, err)
        self.assertIn("The user's command did not take effect: No ready spec", out)
        self.assertIn("Tell the user", out)
        self.assertEqual(self.state()["mode"], "none")

    def test_implement_with_several_ids_seals_the_first_and_queues_the_rest(self):
        # luradb-web-client 2026-11-06: five ids in one call, the first was sealed, the other four vanished unnamed.
        for spec_id in ("s-aaaa1111", "s-bbbb2222"):
            (self.work / "specs" / "core" / f"{spec_id}.md").write_text(f"---\nid: {spec_id}\nstate: ready\n---\n", "utf-8")
        code, err, out = self.prompt(f"/docket:implement {SPEC},s-aaaa1111 s-bbbb2222")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["spec"], SPEC)
        self.assertEqual(self.state()["queue"], ["s-aaaa1111", "s-bbbb2222"])
        self.assertIn("Queued after this spec, in order: s-aaaa1111, s-bbbb2222", out)
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
        self.assertNotIn("Mode sealed", out)
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
        self.assertIn("Mode sealed", out)
        code, err, _ = self.tool("Skill", skill="docket:implement", args=SPEC)  # the model may expand it
        self.assertEqual(code, 0, err)
        # Taken once: the record is gone, and the same text seals no second session.
        self.assertEqual(json.loads((self.root / ".claude" / "docket" / "handoff.json").read_text("utf-8")), [])

    def test_the_same_brief_without_a_record_of_the_pane_is_text(self):
        code, err, out = self.prompt(self.APP_BRIEF.format(command=f"/docket:implement {SPEC}"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertNotIn("Mode sealed", out)

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
        self.assertIn("did not take effect: No spec s-35903eb4, s-55d9034e in this project", out)
        self.assertEqual(self.state().get("mode", "none"), "none")
        self.assertFalse((self.work / "locks").exists())
        self.assertNotIn("sealed", self.log())

    def test_namespaced_commands_are_the_same_commands(self):
        code, _, _ = self.prompt(f"/docket:implement {SPEC}")
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertEqual(self.state()["last_command"], f"/docket:implement {SPEC}")
        self.assertEqual(self.tool("Skill", skill="docket:implement")[0], 0)
        self.set_state(mode="none")
        self.prompt("/docket:concept the next step")
        self.assertEqual(self.state()["mode"], "concept")
        self.prompt("/docket:init")
        self.assertEqual(self.state()["mode"], "init")

    def test_builtin_init_and_status_are_not_docket_commands(self):
        self.prompt("/init")
        self.assertNotEqual(self.state().get("mode"), "init")
        self.prompt(f"/docket:implement {SPEC}")
        code, _, _ = self.prompt("/status")
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertEqual(self.state()["last_command"], f"/docket:implement {SPEC}")
        self.prompt("/stop")
        self.assertFalse(self.state().get("stop_requested"))

    def test_concept_and_spec_modes(self):
        self.prompt("/concept")
        self.assertEqual(self.state()["mode"], "concept")
        self.prompt("/spec")
        self.assertEqual(self.state()["mode"], "spec")

    def test_free_text_in_a_run_reaches_the_orchestrator_and_is_kept_in_the_inbox(self):
        # docket 2026-11-10: eight messages of the user went to an inbox and none reached the model.
        self.prompt(f"/implement {SPEC}")
        code, err, out = self.prompt("please also fix the logo")
        self.assertEqual((code, err.strip()), (0, ""))
        self.assertIn("The user wrote during the run", out)
        self.assertIn("Your next text is shown to the user", out)
        self.assertIn("fix the logo", (self.work / "inbox" / f"{SPEC}.md").read_text("utf-8"))
        self.assertEqual((self.state()["mode"], self.state()["answer_due"]), ("sealed", True))

    def test_a_message_of_the_user_takes_a_stopped_run_up_again(self):
        self.set_state(mode="sealed", spec=SPEC, halted=True)
        code, err, out = self.prompt("what happened?")
        self.assertEqual((code, err.strip()), (0, ""))
        self.assertIn("The run had stopped without being finished; with the user's message it goes on.", out)
        self.assertEqual((self.state()["halted"], self.state()["answer_due"]), (False, True))
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

    def test_a_command_that_did_not_take_effect_starts_nothing_in_its_turn(self):
        self.spec.write_text(f"---\nid: {SPEC}\nstate: draft\n---\n# Login\n", "utf-8")
        self.prompt("/docket:implement")
        self.assertIn("No ready spec", self.state()["declined"])
        self.prompt("then set it to ready")  # the user's next prompt clears it
        self.assertNotIn("declined", self.state())

    def test_harness_notifications_pass_through_when_sealed(self):
        self.prompt(f"/implement {SPEC}")
        code, _, _ = self.prompt("<task-notification>\n<task-id>x</task-id>\n</task-notification>")
        self.assertEqual(code, 0)
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

    def test_a_concept_command_in_a_run_changes_nothing_and_is_answered(self):
        self.prompt(f"/implement {SPEC}")
        code, err, out = self.prompt("/concept")
        self.assertEqual(code, 0, err)
        self.assertIn("The user's command did not take effect: This session works on", out)
        self.assertEqual((self.state()["mode"], self.state()["answer_due"]), ("sealed", True))
        self.assertNotIn("declined", self.state())  # the run goes on in that turn

    def test_reminder_names_open_required_reading(self):
        self.set_state(mode="concept", degraded=True)
        code, _, out = self.prompt("hello")
        self.assertEqual(code, 0)
        self.assertIn("Required reading still open", out)


class SealedStop(Project):
    """0.3.0: a spec ends in its docket, the run in one message the hook renders (luradb-web-client 2026-11-06:
    four receipts of up to 2,500 characters, 76 bare stamps, no message about the series as a whole)."""

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

    def test_delivered_closes_the_spec_and_the_closing_message_closes_the_run(self):
        self.end_spec()
        code, err, out = self.run_stop()
        self.assertEqual(code, 2, err)  # the order to send the closing message, no refusal
        self.assertEqual(out.strip(), "")
        self.assertIn(f"closing-{SID}.md", err)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertIn(f"{SPEC}  delivered", self.log())
        self.assertFalse((self.work / "metrics" / f"{SPEC}.violations.json").exists())
        text = self.closing()
        # One line for the run, one per spec, then only what the user acts on, where the dockets are, the stamp.
        self.assertEqual(self.closing_lines()[:3], [
            "Run finished: “Login” is implemented.",
            "- Login: 1 of 1 points delivered. Login works, three tests are green.",
            "**For you to do:** nothing."])
        self.assertEqual(len(self.closing_lines()), 5)
        self.assertTrue(self.closing_lines()[3].startswith("Records: `"))
        # Blocks with a blank line between them: what follows the specs does not hang in the last spec's list item
        # (luradb-web-client 2026-11-11).
        self.assertIn("three tests are green.\n\n**For you to do:** nothing.\n\nRecords: `", text)
        self.assertNotIn(SPEC, text)
        self.assertTrue(text.splitlines()[-1].startswith("stamp: "))
        code, err, _ = self.run_stop(text, active=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "closed")
        self.assertIn("run-closed", self.log())

    def test_a_text_without_the_terminal_state_is_sent_back(self):
        code, err, _ = self.run_stop("I finished the login. Shall I also do the logout?")
        self.assertEqual(code, 2)
        self.assertIn("Terminal state", err)
        self.assertNotIn("stamp", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_a_docket_message_is_no_end(self):
        self.end_spec()
        code, err, _ = self.run_stop(f"DOCKET DELIVERED {SPEC}\nLogin works.\nNothing to do.")
        self.assertEqual(code, 2)
        self.assertIn("no `DOCKET …` message", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_open_point_blocks_delivered_but_not_blocked(self):
        self.docket.write_text("# Docket\n| 1 | login | `cargo test` | [ ] |\n", "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("still open", err)
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.", review=False)
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("closing", {SPEC: "blocked"}))
        text = self.closing()
        self.assertIn("Run stopped: “Login” needs you, 0 of 1 specs implemented.", text)
        self.assertIn("- Login: stopped at 0 of 1 points. Login is not built yet.", text)
        self.assertIn("**For you to do:**\n\n1. Start Docker.\n2. Continue “Login”: `/docket:implement s-0123abcd`.", text)
        self.assertNotIn("blocked", text)

    def test_blocked_with_a_docket_of_its_terminal_state_alone(self):
        self.docket.unlink()
        self.end_spec("BLOCKED: the working tree is not clean, commit or stash it", result="Nothing was built.",
                      review=False)
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["outcomes"], {SPEC: "blocked"})

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
        self.assertIn("Terminal state", err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertNotIn("stop-forced", self.log())

    def test_an_empty_turn_never_judges_an_older_text(self):
        older = {"type": "assistant", "message": {"content": [{"type": "text", "text": f"DOCKET DELIVERED {SPEC}\nok"}]}}
        code, err, _ = self.run_stop("", transcript_path=self.entries(older))
        self.assertEqual(code, 2)
        self.assertNotIn("DOCKET …", err)

    def test_a_second_stop_right_after_the_refusal_tells_the_user_the_run_stopped(self):
        self.assertEqual(self.run_stop()[0], 2)
        code, err, out = self.run_stop(active=True)
        self.assertEqual(code, 0, err)
        self.assertIn("Login: the run stopped without being finished. `/docket:implement s-0123abcd` resumes it, and "
                      "so does any message from you.", json.loads(out)["systemMessage"])
        self.assertIn("stop-forced", self.log())
        self.assertEqual(self.state()["mode"], "sealed")

    def test_another_hooks_block_is_no_second_pass(self):
        # stop_hook_active comes from any Stop hook's block; only this hook's own refusal makes a second pass.
        code, err, _ = self.run_stop(active=True)
        self.assertEqual(code, 2)
        self.assertNotIn("stop-forced", self.log())

    def test_a_tool_call_after_the_refusal_makes_the_next_stop_a_first_pass(self):
        self.assertEqual(self.run_stop()[0], 2)
        run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read",
                                      "tool_input": {"file_path": "x"}}, self.p)
        self.assertEqual(self.run_stop(active=True)[0], 2)
        self.assertNotIn("stop-forced", self.log())


class HumanStop(Project):
    def test_hashes_ids_hedges_options_rejected(self):
        self.set_state(mode="concept")
        code, err, _ = self.stop(f"Fixed in a1b2c3d4e. It probably works. Option A: keep it. Option B: drop it. See {SPEC}.\n{hstamp()}")
        self.assertEqual(code, 2)
        for fragment in ("hashes", "ids", "hedge", "option"):
            self.assertIn(fragment, err)

    def test_hex_inside_paths_and_ids_is_not_a_hash(self):
        # The diff message in luradb-web-client was sent back for a Windows scratchpad path (2026-11-05).
        self.set_state(mode="init")
        path = (r"C:\Users\heiko\AppData\Local\Temp\claude\257f88f23acc5a93"
                r"\ddeb9c46-306b-47e4-8033-820e5e176cee\scratchpad\final\docket-init.diff")
        for text in (f"Diff: {path}", "Port 1234567 stays open.", "The file was effaced.", "See 469f289a.txt."):
            code, err, _ = self.stop(f"{text}\n{hstamp()}")
            self.assertEqual(code, 0, f"{text}: {err}")
        for text in ("Fixed in a1b2c3d4e.", "Merged (469f289) today.", "Merged `469f289`."):
            code, err, _ = self.stop(f"{text}\n{hstamp()}")
            self.assertEqual(code, 2, text)
            self.assertIn("hashes", err)

    def test_two_questions_rejected(self):
        self.set_state(mode="spec")
        self.assertEqual(self.stop(f"Keep the default? Or change it?\n{hstamp()}")[0], 2)

    def test_clean_message_passes(self):
        self.set_state(mode="concept")
        code, err, _ = self.stop(f"The default stays at 512 (config_reference.md). Keep it?\n{hstamp()}")
        self.assertEqual(code, 0, err)

    def test_closed_rejects_proposals(self):
        self.set_state(mode="closed", spec=SPEC)
        self.assertEqual(self.stop(f"The fix is in. I suggest we also remove the switch.\n{hstamp()}")[0], 2)
        self.assertEqual(self.stop(f"The fix is in, delivered as written in the docket.\n{hstamp()}")[0], 0)

    def test_every_message_needs_a_fresh_stamp(self):
        self.set_state(mode="concept")
        code, err, _ = self.stop("The default stays at 512 (config_reference.md).")
        self.assertEqual(code, 2)
        self.assertIn("stamp", err)
        code, err, _ = self.stop(f"The default stays at 512 (config_reference.md).\n{hstamp(-40)}")
        self.assertEqual(code, 2)
        self.assertIn("minutes off", err)
        self.assertTrue(self.state()["degraded"])
        self.set_state(mode="closed", spec=SPEC)
        self.assertEqual(self.stop("Delivered as written in the docket.")[0], 2)
        self.assertEqual(self.stop(f"Delivered as written in the docket.\n{hstamp()}")[0], 0)

    def test_a_wait_in_a_sealed_run_needs_no_text_and_no_stamp(self):
        # V4 (luradb-web-client 2026-11-06: 76 of 128 texts were a bare stamp for a wait).
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": "",
                                            "stop_hook_active": False, "background_tasks": [{"id": "a1"}]}, self.p)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_none_mode_needs_only_the_stamp_once_the_project_is_set_up(self):
        code, err, _ = self.stop("anything goes here? really? a1b2c3d4e")
        self.assertEqual(code, 2)
        self.assertIn("stamp line alone", err)
        self.assertNotIn("hashes", err)
        self.assertEqual(self.stop(f"anything goes here? really? a1b2c3d4e\n{hstamp()}")[0], 0)

    def test_none_mode_is_silent_without_docket_md(self):
        (self.work / "DOCKET.md").unlink()
        self.assertEqual(self.stop("anything goes here? really? a1b2c3d4e")[0], 0)

    def test_missing_stamp_asks_for_the_stamp_line_alone(self):
        self.set_state(mode="concept")
        code, err, _ = self.stop("The default stays at 512 (config_reference.md).")
        self.assertEqual(code, 2)
        self.assertIn("stamp line alone", err)
        self.assertNotIn("Rewrite your last message", err)

    def test_rule_is_announced_at_start_and_on_every_prompt(self):
        _, _, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p)
        self.assertIn("stamp line alone", out)
        _, _, out = self.prompt("how are the tests?")
        self.assertIn("stamp:", out)


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
        self.prompt("/concept")
        folder = self.root / ".claude" / "docket"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [f"state-{SID}.json"])
        self.assertEqual(self.state()["mode"], "concept")


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
        self.assertIn("runs in another session", out)
        self.assertIn(f"(session {SID[:8]})", out)
        self.assertIn("what they can do", out)
        other = json.loads((self.root / ".claude" / "docket" / "state-other-session-2.json").read_text("utf-8"))
        self.assertEqual(other["mode"], "none")
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)

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
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 0, err)

    def test_check_before_last_agent_return_is_refused(self):
        self.write_transcript([
            ("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}}),
            ("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"prompt": "x"}}),
            ("user", {"type": "tool_result", "tool_use_id": "a1", "content": "done"}),
        ])
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("before the last agent returned", err)

    def test_check_never_run_is_refused_but_blocked_passes(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}})])
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")[0], 2)
        self.assertEqual(self.stop_t(f"DOCKET BLOCKED {SPEC}\nno docker\nYou must start Docker.\n{stamp()}")[0], 0)

    def test_a_cited_location_is_not_checked_at_the_end(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}})])
        (self.root / "src").mkdir()
        (self.root / "src" / "auth.rs").write_text("a\nb\nc\n", "utf-8")
        self.docket.write_text("- [x] point 1: `cargo test` → 3 passed, see src/auth.rs:2\n", "utf-8")
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")[0], 0)
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("- [x] point 1: see src/auth.rs:40 and src/gone.rs:1\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 0, err)  # a cited location is not checked at the end

    def test_reference_update_must_be_accounted_for(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}})])
        self.spec.write_text(f"---\nid: {SPEC}\nstate: ready\n---\n## Reference update\n\n| File | Change |\n|---|---|\n"
                             "| docs/auth.md | login |\n| README.md | none |\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("docs/auth.md", err)
        self.docket.write_text("- [x] point 1\n- updated docs/auth.md\n- README.md: not needed, unchanged\n", "utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "auth.md").write_text("login\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 0, err)

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
        return self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")

    def test_a_check_of_several_commands_passes_alone_or_together(self):
        self.assertEqual(self.several(("npm run check && npm test", False))[0], 0)
        self.assertEqual(self.several(("npm run check", False), ("npm test", False))[0], 0)

    def test_every_command_of_the_check_must_run_and_none_may_fail(self):
        code, err, _ = self.several(("npm run check", False))
        self.assertEqual(code, 2)
        self.assertIn("`npm test` never ran", err)
        code, err, _ = self.several(("npm run check", False), ("npm test", True))
        self.assertEqual(code, 2)
        self.assertIn("`npm test` failed", err)
        self.assertEqual(self.several(("npm test", True), ("npm run check && npm test", False))[0], 0)

    def test_placeholder_check_is_not_a_check(self):
        (self.work / "DOCKET.md").write_text("| check | `<command that exits 0>` | |\n", "utf-8")
        self.write_transcript([("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}})])
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")[0], 0)


class ClosingCounts(Project):
    """The closing message counts the docket's boxes itself and names every deviation (replaces F19: the stamp's
    counts are gone with the terminal message)."""

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | token refresh | `y` | [~] |\n| 3 | c | `z` | [x] |\n"
                               "## Refinement\n- feasibility: a → adopted\n- spec: c → rejected: out of scope\n", "utf-8")

    def test_counts_and_deviations_come_from_the_docket(self):
        self.end_spec(lines=["open: the refresh needs the server's 0.6 API"])
        self.assertEqual(self.run_stop()[0], 2)
        text = self.closing()
        # A point that deviates is named in its spec's line.
        self.assertIn("- Login: 2 of 3 points delivered. Login works, three tests are green. “token refresh” deviates "
                      "from the spec.", text)
        # A note for the record stays in the docket: the message neither shows nor counts it (luradb-web-client
        # 2026-11-11: "15 open points: ask here for them").
        self.assertTrue(text.splitlines()[-2].endswith("/work/dockets/`"), text)
        self.assertNotIn("open point", text)
        self.assertNotIn("ask here", text)
        self.assertNotIn("The refresh needs", text)
        self.assertNotIn("Checked", text)


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


class JudgeLog(Project):
    """The judge runs at Stop, after the user read the message: its rejection goes to Claude, which answers
    `[docket] noted`; stop.py lets it through and counts the verdict."""

    PROMPT = "[You judge the final message of an assistant before a human reads it. Hook input JSON: $ARGUMENTS ...]"
    TAIL = " The user has read your message and will not see your answer to this; answer with exactly the line `[docket] noted`."

    def setUp(self):
        super().setUp()
        self.path = self.tmp / "transcript.jsonl"

    def add(self, *entries):
        with self.path.open("a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def summary(self, at, reason=None):
        errors = [f"{self.PROMPT}: {reason}{self.TAIL}"] if reason else []
        return {"type": "system", "subtype": "stop_hook_summary", "timestamp": at, "hookErrors": errors}

    def feedback(self, reason, extra=""):
        text = f"Stop hook feedback:\n{extra}{self.PROMPT}: {reason}{self.TAIL}"
        return {"type": "user", "timestamp": "2026-11-05T21:58:53Z", "message": {"role": "user", "content": text}}

    def said(self, text):
        return {"type": "assistant", "timestamp": "2026-11-05T21:58:44Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    def stop_with(self, message, active=False):
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": active, "transcript_path": str(self.path)}, self.p)

    def test_empty_answer_after_a_verdict_passes_and_the_verdict_is_counted(self):
        # Experiment 10d: after the verdict Claude records no message; the second Stop carries no
        # last_assistant_message and the transcript's last text is the judged message.
        self.set_state(mode="concept")
        reason = 'promise "Morgen schreibe ich dir ein Gedicht"'
        self.add(self.said(f"Der Mond kreist still um unsere Erde.\n{hstamp()}"), self.feedback(reason),
                 self.summary("2026-11-05T21:58:53Z", reason),
                 {"type": "user", "message": {"role": "user", "content": "[Your previous response had no visible output.]"}})
        code, err, _ = run_hook("stop.py", {"session_id": SID, "cwd": self.p, "stop_hook_active": True,
                                            "transcript_path": str(self.path)}, self.p)
        self.assertEqual(code, 0, err)
        counts = json.loads((self.work / "metrics" / "session.violations.json").read_text("utf-8"))
        self.assertEqual(counts, {"judge-promise": 1})
        self.assertEqual(self.log().count(" judge "), 1)

    def test_acknowledgement_does_not_end_a_sealed_run(self):
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = self.stop_with("[docket] noted")
        self.assertEqual(code, 2)
        self.assertIn("Terminal state", err)

    def test_older_judge_verdicts_are_counted_too(self):
        # luradb-web-client, 2026-11-05: the judge before the acknowledgement asked for correction lines.
        self.set_state(mode="concept")
        self.add({"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-11-05T21:58:53Z",
                  "hookErrors": [f'{self.PROMPT}: Sentence "I will report the result" is a promise about the '
                                 "assistant's next action. The user has already read the message: do not repeat it."]})
        self.stop_with(f"Fine.\n{hstamp()}")
        counts = json.loads((self.work / "metrics" / "session.violations.json").read_text("utf-8"))
        self.assertEqual(counts, {"judge-promise": 1})

    def test_template_asks_for_the_acknowledgement(self):
        template = json.loads((HOOKS.parent / "templates" / "judge-hook.json").read_text("utf-8"))
        prompt = template["hooks"]["Stop"][0]["hooks"][0]["prompt"]
        self.assertIn("empty answer", prompt)  # experiment 10d: an empty turn leaves nothing on screen, the verdict stays
        self.assertIn("stop_hook_active is true", prompt)  # the second Stop after the empty answer goes through
        self.assertNotIn("`[docket] noted`", prompt)  # experiment 10: visible when the session is opened later
        self.assertNotIn("impossible", prompt)  # experiment 9: with it the verdict never reaches the transcript
        self.assertNotIn("Korrektur", prompt)
        self.assertNotIn("]: ", prompt)  # the reason is what follows the quoted prompt's first "]: "
        # M2/M3 (2026-11-05): the blocker's one step is not a manual task; no-source and identifier are not
        # decidable without the tool calls and made the model repeat every command on screen.
        self.assertIn("not manual tasks", prompt)
        # 0.2.9: a blocker names every step only the user can take (luradb-web-client 2026-11-06: URL, key and
        # one test run were three sentences, the judge called two of them manual tasks).
        self.assertIn("every step only the user can take", prompt)
        self.assertNotIn("no-source", prompt)
        self.assertNotIn("identifier", prompt)


class CiHash(Project):
    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        (self.root / "a.txt").write_text("a\n", "utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one"], check=True)
        self.head = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.transcript = self.tmp / "transcript.jsonl"

    def stop_t(self, message, pushed=True):
        blocks = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "p1", "name": "Bash",
                   "input": {"command": "git push origin feature/x" if pushed else "git status"}}]}}]
        self.transcript.write_text("\n".join(json.dumps(b) for b in blocks) + "\n", "utf-8")
        return self.finish(message, transcript_path=str(self.transcript))

    def test_ci_hash_must_be_reachable_and_pushed(self):
        self.docket.write_text(f"- [x] point 1: ok. CI: green {self.head[:10]}\n", "utf-8")
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")[0], 0)
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("- [x] point 1: ok. CI: green 0123456789abcdef0123456789abcdef01234567\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("not reachable", err)
        self.docket.write_text(f"- [x] point 1: ok. CI: green {self.head[:10]}\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}", pushed=False)
        self.assertEqual(code, 2)
        self.assertIn("git push", err)

    def test_configured_ci_needs_a_hash_line(self):
        (self.work / "DOCKET.md").write_text("| Key | Value | Source |\n|---|---|---|\n| ci | `gh run watch` | |\n", "utf-8")
        self.docket.write_text("- [x] point 1: ok. CI: none\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("pushed hash", err)


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


class SelfTalk(Project):
    def test_apologies_and_effort_rejected(self):
        self.set_state(mode="concept")
        code, err, _ = self.stop(f"Sorry, this took me two hours.\n{hstamp()}")
        self.assertEqual(code, 2)
        self.assertIn("yourself", err)


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


class LegacyConcept(Project):
    """The concept session of 2026-11-05 (framework/ANALYSE-LEGACY-CONCEPT-SITZUNG.md): asked in /docket:concept to
    rewrite five legacy specs, it never tried a spec write, wrote a concept file, flagged the legacy specs to it,
    edited three rule files on a box answer about something else, and said a hook had answered what it had only
    read in the hook's source."""

    def setUp(self):
        super().setUp()
        self.path = self.tmp / "transcript.jsonl"

    def add(self, *entries):
        with self.path.open("a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def user(self, content):
        return {"type": "user", "message": {"role": "user", "content": content}}

    def said(self, text):
        return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    def box(self, ask):
        return {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__ccd_turn__wrap_up", "input": {"recap": "x", "ask": ask}}]}}

    def stopped(self):
        return {"type": "system", "subtype": "stop_hook_summary", "hookErrors": []}

    def tool_t(self, name, **tool_input):
        return run_hook("pre_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": name, "tool_input": tool_input,
                                            "transcript_path": str(self.path)}, self.p)

    def stop_t(self, message):
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": False, "transcript_path": str(self.path)}, self.p)

    def test_a_spec_order_in_concept_mode_gets_the_handover_line_and_no_ambition(self):
        _, _, out = self.prompt("/docket:concept schaue dir die legacy-specs an, nimm dir die nächsten 5 specs und "
                                "überarbeite die specs in docket-specs.")
        self.assertIn("`/docket:spec`", out)
        self.assertNotIn("Be ambitious", out)
        _, _, out = self.prompt("Die nächsten Specs laut einer Alt-Aufstellung wären diese gewesen")
        self.assertNotIn("`/docket:spec`", out)  # said once, with the command; it stays in the context
        _, _, out = self.prompt("/docket:concept welche spec ist die nächste?")
        self.assertNotIn("`/docket:spec`", out)
        _, _, out = self.prompt("/docket:concept Welche Specs sollten wir als nächstes schreiben?")
        self.assertNotIn("`/docket:spec`", out)  # a question to think about, not an order
        _, _, out = self.prompt(f"/docket:implement {SPEC}")
        self.assertIn("Be ambitious", out)

    def test_the_box_question_counts_toward_the_one_question(self):
        self.set_state(mode="concept")
        self.add(self.user("weiter"), self.box("Dürfen docket-Läufe den Testserver per REST ansprechen?"))
        code, err, _ = self.stop_t(f"Empfehlung zur Frage im Kasten: ja. Soll ich auch CHANGELOG.md bereinigen?\n{hstamp()}")
        self.assertEqual(code, 2)
        self.assertIn("next-step box", err)
        self.assertEqual(self.stop_t(f"Empfehlung zur Frage im Kasten: ja (DOCKET.md Frage 11).\n{hstamp()}")[0], 0)
        self.assertEqual(self.stop_t(f"Quelle: https://example.org/x?y=1 und `a?b`.\n{hstamp()}")[0], 0)

    def test_a_box_before_the_last_stop_belongs_to_an_earlier_message(self):
        self.set_state(mode="concept")
        self.add(self.user("weiter"), self.box("Committen?"), self.said(f"Fertig.\n{hstamp()}"), self.stopped(),
                 self.user("<task-notification>done</task-notification>"), self.box("Pushen?"))
        self.assertEqual(self.stop_t(f"Der Lauf ist durch (git log).\n{hstamp()}")[0], 0)

    def test_a_hook_answer_is_quoted_only_when_a_hook_gave_it(self):
        self.set_state(mode="concept")
        message = (f"- Warum ein Konzept: Der Hook hat geantwortet: „The push did not run“ "
                   f"(pre_tool_use.py).\n{hstamp()}")
        self.add(self.user("was ist das für ein Konzept?"))
        code, err, _ = self.stop_t(message)
        self.assertEqual(code, 2)
        self.assertIn("no hook said it", err)
        self.add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t9", "is_error": True,
             "content": "PreToolUse:Bash hook error: [docket rule: push_branch] The push goes to `main`; "
                        "push_branch in DOCKET.md is `next`. The push did not run."}]}})
        self.assertEqual(self.stop_t(message)[0], 0)
        # A quote from elsewhere, or without a hook in the line, is not checked.
        self.assertEqual(self.stop_t(f"Der Nutzer schrieb „nimm die nächsten fünf Specs“.\n{hstamp()}")[0], 0)

    def test_a_hook_text_read_in_a_file_or_pasted_by_the_user_may_be_quoted(self):
        self.set_state(mode="concept")
        self.add(self.user("was ist das für ein Konzept?"))
        self.assertEqual(self.stop_t("Der Hook-Text lautet „Specs are written in spec mode only“ "
                                     f"(pre_tool_use.py:310).\n{hstamp()}")[0], 0)
        self.add(self.user("Der Hook schrieb mir: Specs are written in spec mode only: name it"))
        self.assertEqual(self.stop_t(f"Der Hook sagte „Specs are written in spec mode only“.\n{hstamp()}")[0], 0)

    def test_an_id_inside_a_link_target_is_part_of_a_path(self):
        self.set_state(mode="concept")
        link = "[Legacy-Transfer 1](concept/docket/concepts/general/c-35290626-legacy-transfer-1.md)"
        self.assertEqual(self.stop(f"Die Entwürfe stehen in {link}.\n{hstamp()}")[0], 0)
        code, err, _ = self.stop(f"Die Entwürfe stehen in c-35290626.\n{hstamp()}")
        self.assertEqual(code, 2)
        self.assertIn("ids", err)


class RepeatedBlocker(Project):
    """M4: a blocker is said once. On 2026-11-05 the same blocker stood in seven messages in a row and the user
    read the seventh as a refusal."""

    def setUp(self):
        super().setUp()
        self.set_state(mode="spec")
        self.path = self.tmp / "transcript.jsonl"

    def turn(self, user, *assistant):
        entries = [{"type": "user", "message": {"role": "user", "content": user}}]
        entries += [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}
                    for t in assistant]
        with self.path.open("a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")

    def stop_t(self, message):
        self.turn("bitte pushen", message)  # the current turn
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": False, "transcript_path": str(self.path)}, self.p)

    def test_the_same_blocker_line_again_is_refused(self):
        self.turn("alles pushen", f"Blocker: the push of the concept repo needs your click.\n{hstamp()}")
        code, err, _ = self.stop_t("Pushed the client repo (git push).\n"
                                   f"- **Blocker:** the push of the concept repo needs your click.\n{hstamp()}")
        self.assertEqual(code, 2)
        self.assertIn("say it once", err)

    def test_new_facts_or_blocker_unchanged_pass(self):
        self.turn("alles pushen", f"Blocker: the push of the concept repo needs your click.\n{hstamp()}")
        self.assertEqual(self.stop_t(f"Blocker unverändert.\n{hstamp()}")[0], 0)
        self.assertEqual(self.stop_t(f"Blocker: GitHub rejected the push (remote: fatal error).\n{hstamp()}")[0], 0)

    def test_tool_results_and_hook_feedback_do_not_end_a_turn(self):
        self.turn("alles pushen", f"Blocker: the push needs your click.\n{hstamp()}")
        self.turn("Stop hook feedback:\n[docket] the last line must be a stamp", hstamp())  # same turn, sent back
        self.turn([{"type": "tool_result", "content": "x"}], "looked again")
        code, err, _ = self.stop_t(f"Blocker: the push needs your click.\n{hstamp()}")
        self.assertEqual(code, 2, err)
        self.assertIn("say it once", err)

    def test_a_blocker_two_turns_ago_is_not_the_previous_message(self):
        self.turn("alles pushen", f"Blocker: the push needs your click.\n{hstamp()}")
        self.turn("ok", f"Pushed (git push).\n{hstamp()}")
        self.assertEqual(self.stop_t(f"Blocker: the push needs your click.\n{hstamp()}")[0], 0)


class CopySync(Project):
    """The judge entry /docket:init wrote into the project follows the plugin at every session start; nobody runs
    init again (2026-11-06: the judge in luradb-web-client was five versions behind)."""

    TEMPLATE = json.loads((HOOKS.parent / "templates" / "judge-hook.json").read_text("utf-8"))["hooks"]["Stop"][0]["hooks"][0]

    def setUp(self):
        super().setUp()

    def start(self):
        return run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p)

    def settings(self, hooks):
        (self.root / ".claude" / "settings.json").write_text(json.dumps({"hooks": hooks, "model": "x"}), "utf-8")

    def in_process(self, function, **patched):
        sys.path.insert(0, str(HOOKS))
        import session_start
        saved = {name: getattr(session_start, name) for name in patched}
        saved_env = os.environ.get("CLAUDE_PROJECT_DIR")
        for name, value in patched.items():
            setattr(session_start, name, value)
        os.environ["CLAUDE_PROJECT_DIR"] = self.p
        try:
            return getattr(session_start, function)(session_start.Paths({"cwd": self.p}))
        finally:
            for name, value in saved.items():
                setattr(session_start, name, value)
            if saved_env is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = saved_env

    def test_an_old_judge_is_replaced_and_other_settings_stay(self):
        sys.path.insert(0, str(HOOKS))
        import session_start
        old = {"type": "prompt", "model": "claude-haiku-5-5", "timeout": 30, "prompt": "You judge the final message, old"}
        other = {"type": "command", "command": "node no-root-docs.mjs"}
        self.settings({"Stop": [{"hooks": [old]}], "PreToolUse": [{"matcher": "Bash", "hooks": [other]}]})
        versions = session_start.JUDGE_VERSIONS + (session_start.judge_digest(old),)
        self.assertTrue(self.in_process("sync_judge", JUDGE_VERSIONS=versions))
        data = json.loads((self.root / ".claude" / "settings.json").read_text("utf-8"))
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0], self.TEMPLATE)
        self.assertEqual(data["hooks"]["PreToolUse"][0]["hooks"][0], other)
        self.assertEqual(data["model"], "x")
        self.assertFalse(self.in_process("sync_judge", JUDGE_VERSIONS=versions))  # nothing left to do
        self.assertEqual(list((self.root / ".claude").glob("*.tmp")), [])

    def test_a_judge_the_project_changed_stays_and_odd_settings_do_not_crash(self):
        custom = dict(self.TEMPLATE, model="claude-sonnet-5-5", timeout=60)
        self.settings({"Stop": [{"hooks": [custom]}]})
        before = (self.root / ".claude" / "settings.json").read_text("utf-8")
        code, err, out = self.start()
        self.assertEqual(code, 0, err)
        self.assertEqual((self.root / ".claude" / "settings.json").read_text("utf-8"), before)
        self.settings({"Stop": None})
        code, err, out = self.start()
        self.assertEqual(code, 0, err)
        self.assertIn("session state", out)

    def test_the_template_judge_is_a_known_version(self):
        sys.path.insert(0, str(HOOKS))
        import session_start
        self.assertIn(session_start.judge_digest(self.TEMPLATE), session_start.JUDGE_VERSIONS,
                      "templates/judge-hook.json changed: append its hash to JUDGE_VERSIONS in session_start.py")

    def test_a_project_without_the_judge_stays_without(self):
        self.settings({"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]})
        before = (self.root / ".claude" / "settings.json").read_text("utf-8")
        self.start()
        self.assertEqual((self.root / ".claude" / "settings.json").read_text("utf-8"), before)
        self.assertNotIn("judge-updated", self.log())


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
        self.assertIn("Queued after this spec, in order: s-aaaa1111", out)

    def test_all_runs_every_ready_spec_in_implementation_order(self):
        code, err, _ = self.prompt("/docket:implement --all")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.state()["spec"], self.state()["queue"]), (SPEC, ["s-aaaa1111", "s-bbbb2222"]))

    def test_a_successor_before_its_undone_predecessor_is_not_sealed(self):
        code, err, out = self.prompt("/docket:implement s-aaaa1111")
        self.assertEqual(code, 0, err)
        self.assertIn(f"s-aaaa1111 needs {SPEC} first, which is not done", out)
        self.assertEqual(self.state()["mode"], "none")
        self.prompt(f"/docket:implement s-aaaa1111, {SPEC}")
        self.assertEqual(self.state()["mode"], "none")
        self.assertEqual(self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")[0], 0)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_words_without_an_id_seal_nothing_and_name_the_forms(self):
        code, err, out = self.prompt("/docket:implement the login one")
        self.assertEqual(code, 0, err)
        self.assertIn("--all", out)
        self.assertEqual(self.state()["mode"], "none")

    def test_delivered_seals_the_next_spec_and_hands_over_on_stderr(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111, s-bbbb2222")
        self.deliver(SPEC, "Login")
        code, err, out = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(), "")  # no JSON, no systemMessage: nothing for the user
        self.assertIn("Spec 2 of 3 is sealed: Logout (s-aaaa1111)", err)
        self.assertIn("step 0", err)
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"], state["series"]),
                         ("sealed", "s-aaaa1111", ["s-bbbb2222"], [SPEC, "s-aaaa1111", "s-bbbb2222"]))
        log = self.log()
        self.assertIn(f"{SPEC}  delivered", log)
        self.assertIn("s-aaaa1111  sealed", log)
        self.assertIn("state: in-progress", (self.work / "specs" / "core" / "s-aaaa1111-logout.md").read_text("utf-8"))
        self.assertIn("state: done", self.spec.read_text("utf-8"))
        self.assertFalse((self.work / "metrics" / f"{SPEC}.violations.json").exists())  # the order is no refusal
        metrics = json.loads((self.work / "metrics" / f"{SPEC}.json").read_text("utf-8"))
        self.assertEqual((metrics["outcome"], metrics["points"]["delivered"]), ("delivered", 1))
        # The next spec's first Stop is a first pass although stop_hook_active is true.
        code, err, _ = self.run_stop(active=True)
        self.assertEqual(code, 2)
        self.assertIn("Terminal state", err)
        code, err, out = self.run_stop(active=True)
        self.assertEqual(code, 0, err)
        self.assertIn("Logout: the run stopped", json.loads(out)["systemMessage"])

    def test_a_series_ends_in_one_message_naming_every_spec(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.deliver(SPEC, "Login")
        self.assertEqual(self.run_stop()[0], 2)
        self.deliver("s-aaaa1111", "Logout")
        code, err, _ = self.run_stop(active=True)
        self.assertEqual(code, 2)
        self.assertIn("closing", err)
        text = self.closing()
        self.assertEqual(self.closing_lines()[:4], [
            "Run finished: 2 of 2 specs implemented.",
            "- Login: 1 of 1 points delivered. Login works.",
            "- Logout: 1 of 1 points delivered. Logout works.",
            "**For you to do:** nothing."])
        self.assertNotRegex(text, r"\bs-[0-9a-f]{8}\b")
        self.assertEqual(self.run_stop(text, active=True)[0], 0)
        self.assertEqual(self.state()["mode"], "closed")
        self.assertEqual(self.log().count(" run-closed "), 1)

    def test_blocked_stops_the_series_and_names_the_rest(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.assertFalse((self.work / "locks" / "s-aaaa1111").exists())  # only the spec at work is locked
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.", review=False)
        code, err, out = self.run_stop()
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(), "")
        text = self.closing()
        self.assertIn("Run stopped: “Login” needs you, 0 of 2 specs implemented.", text)
        self.assertIn("- Logout: not started, waits for “Login”.", text)
        self.assertIn("**For you to do:**\n\nFor “Login”:\n\n1. Start Docker.\n\nThen:\n\n2. Continue “Login” and have "
                      "“Logout” implemented: `/docket:implement s-0123abcd, s-aaaa1111`.", text)
        self.assertEqual((self.state()["mode"], self.state()["queue"]), ("closing", []))
        self.assertIn("queue-dropped", self.log())

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
        self.assertEqual(self.state()["mode"], "none")  # Profile is not done; an id of no spec holds nothing up
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
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.", review=False)
        code, err, out = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("Spec 3 of 3 is sealed: Profile (s-bbbb2222)", err)  # Logout follows Login: passed over
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"]), ("sealed", "s-bbbb2222", []))
        self.assertIn("s-aaaa1111  passed-over", self.log())
        self.assertIn("waits for s-0123abcd", self.log())
        self.deliver("s-bbbb2222", "Profile")
        self.assertEqual(self.run_stop(active=True)[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertEqual(self.closing_lines()[:9], [
            "Run finished: 1 of 3 specs implemented, “Login” needs you, `master` has no upstream, nothing pushed."
            .replace("master", subprocess.run(["git", "-C", str(self.root), "rev-parse", "--abbrev-ref", "HEAD"],
                                              capture_output=True, text=True).stdout.strip()),
            "- Login: stopped at 0 of 0 points. Login is not built yet.",
            "- Logout: not started, waits for “Login”.",
            "- Profile: 1 of 1 points delivered. Profile works.",
            "**For you to do:**",
            "For “Login”:",
            "1. Start Docker.",
            "Then:",
            "2. Continue “Login” and have “Logout” implemented: `/docket:implement s-0123abcd, s-aaaa1111`."])

    def test_blocked_with_work_left_in_the_project_ends_the_series(self):
        # The next spec's push would carry what the blocked one left behind.
        git = self.repo()
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        (self.root / "half.txt").write_text("half\n", "utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "half a login")
        self.end_spec("BLOCKED: the review found a fault in the login", result="Login is half built.", review=False)
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual((self.state()["mode"], self.state()["queue"]), ("closing", []))
        self.assertIn("- Profile: not started, because the run ended at “Login”.", self.closing())
        self.assertIn("queue-dropped", self.log())

    def test_what_the_user_acts_on_is_not_cut(self):
        # luradb-web-client 2026-11-10: the decision line lost its recommendation and its command at 200 characters.
        self.prompt(f"/docket:implement {SPEC}")
        what = "Zuerst den Contract neu pinnen, dann die Query-Keys als Konstanten, danach die Tabellen-Anlage"
        why = ("Die Tabellen-Anlage baut auf beiden auf und kann ohne sie an keinem Punkt beginnen, und die Query-Keys "
               "ändern sich mit dem Contract noch einmal")
        self.assertGreater(len(what) + len(why), 200)
        self.end_spec("BLOCKED: start Docker", result="Login is not built yet.", review=False,
                      lines=[f"decision: {what} | why: {why} | outside: Die Reihenfolge der Specs legst du fest"])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertIn(f"1. {what} – {why}.", self.closing())

    def test_stop_request_ends_the_series(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.prompt("/docket:stop")
        self.deliver(SPEC, "Login")
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertIn("Run ended at your request: 1 of 2 specs implemented.", self.closing())

    def test_a_queued_spec_waits_for_its_series(self):
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        code, err, out = run_hook("user_prompt.py", {"session_id": "other-session-0002", "cwd": self.p,
                                                     "prompt": "/docket:implement s-bbbb2222"}, self.p)
        self.assertEqual(code, 0, err)
        self.assertIn("waits in another session's series", out)
        self.assertFalse((self.work / "locks" / "s-bbbb2222").exists())

    def test_the_closing_message_says_whether_the_work_is_pushed(self):
        git = lambda *args, cwd=self.root: subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c",
                                                           "user.email=t@t"] + list(args), capture_output=True, check=True)
        remote = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        git("init", "-q")
        (self.root / ".gitignore").write_text(".claude/\n", "utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "one")
        git("remote", "add", "origin", str(remote))
        git("push", "-q", "-u", "origin", "HEAD")
        branch = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        self.prompt(f"/docket:implement {SPEC}")
        self.deliver(SPEC, "Login")
        self.run_stop()
        self.assertEqual(self.closing().splitlines()[0], f"Run finished: “Login” is implemented, pushed to `{branch}`.")
        (self.root / "b.txt").write_text("b\n", "utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "two")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC], outcomes={})
        self.run_stop()
        self.assertEqual(self.closing().splitlines()[0],
                         f"Run finished: “Login” is implemented, 1 commit on `{branch}` not pushed.")


class JudgeSuspension(Project):
    """V5: the judge has no place in a sealed run (ENTSCHEIDUNGEN.md, Stop-Hook). luradb-web-client 2026-11-06: 24
    verdicts on waits of at most three lines, which the judge's own exceptions should have let through."""

    def setUp(self):
        super().setUp()
        template = json.loads((HOOKS.parent / "templates" / "judge-hook.json").read_text("utf-8"))
        self.judge = template["hooks"]["Stop"][0]["hooks"][0]
        self.other = {"type": "command", "command": "echo hi"}
        self.settings = self.root / ".claude" / "settings.json"
        self.settings.write_text(json.dumps({"permissions": {"allow": []},
                                             "hooks": {"Stop": [{"hooks": [self.other, self.judge]}]}}, indent=2), "utf-8")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.record = self.root / ".claude" / "docket" / "judge-suspended.json"

    def stop_hooks(self):
        return [hook for group in json.loads(self.settings.read_text("utf-8"))["hooks"]["Stop"] for hook in group["hooks"]]

    def test_the_judge_is_away_during_the_run_and_back_after_it(self):
        self.prompt(f"/implement {SPEC}")
        self.assertEqual(self.stop_hooks(), [self.other])
        self.assertEqual(json.loads(self.settings.read_text("utf-8"))["permissions"], {"allow": []})
        self.assertTrue(self.record.exists())
        self.assertIn("judge-suspended", self.log())
        self.docket.write_text("| 1 | login | `x` | [x] |\n", "utf-8")
        self.end_spec()
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.run_stop(self.closing(), active=True)[0], 0)
        self.assertEqual(sorted(map(json.dumps, self.stop_hooks())), sorted(map(json.dumps, [self.other, self.judge])))
        self.assertFalse(self.record.exists())
        self.assertIn("judge-resumed", self.log())

    def test_a_tracked_settings_file_stays_and_the_verdicts_in_a_run_count_nothing(self):
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", ".claude/settings.json"], check=True)
        self.prompt(f"/implement {SPEC}")
        self.assertEqual(self.stop_hooks(), [self.other, self.judge])
        self.assertFalse(self.record.exists())
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(json.dumps({"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-11-06T12:00:00Z",
                                          "hookErrors": [JudgeLog.PROMPT + ': promise "I will report"' + JudgeLog.TAIL]}) + "\n",
                              "utf-8")
        self.run_stop(background_tasks=[{"id": "a1"}], transcript_path=str(transcript))
        self.assertIn("judge-ignored", self.log())
        self.assertFalse((self.work / "metrics" / f"{SPEC}.violations.json").exists())

    def test_a_judge_a_crashed_run_left_away_comes_back_at_the_next_start(self):
        self.prompt(f"/implement {SPEC}")
        state_file = self.root / ".claude" / "docket" / f"state-{SID}.json"
        old = time.time() - 7200
        os.utime(state_file, (old, old))
        run_hook("session_start.py", {"session_id": "fresh-session-2", "cwd": self.p, "source": "startup"}, self.p)
        self.assertIn(self.judge, self.stop_hooks())
        self.assertFalse(self.record.exists())


class ReviewGate(Project):
    """V14: one reviewer reads the whole diff before the push; every finding is decided in the docket."""

    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | login | `x` | [x] |\n", "utf-8")

    def test_delivered_needs_the_review(self):
        self.end_spec(review=False)
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("## Review", err)

    def add(self, *lines):
        self.docket.write_text(self.docket.read_text("utf-8") + "".join(line + "\n" for line in lines), "utf-8")

    def test_a_bug_an_unmet_point_and_a_broken_rule_are_never_deferred(self):
        self.docket.write_text("| 1 | login | `x` | [x] |\n## Review\n\n- code | the token never expires | src/auth.rs:40 | "
                               "bug | deferred\n- spec | no findings\n", "utf-8")
        self.add("- code | the token never expires | src/auth.rs:40 | bug | fixed, expires after an hour")
        self.end_spec("BLOCKED: the token never expires", result="Login works, the token stays valid.", review=False)
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["outcomes"], {SPEC: "blocked"})

    def transcript(self, *agents):
        """Each agent as (kind, returned), in order, then the push is tried."""
        entries = []
        for n, (kind, returned) in enumerate(agents):
            entries.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"a{n}", "name": "Agent", "input": {"subagent_type": f"docket:{kind}"}}]}})
            if returned:
                entries.append({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"a{n}", "content": "done", "is_error": returned == "refused"}]}})
        path = self.tmp / "review.jsonl"
        path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", "utf-8")
        return str(path)

    def test_a_fix_after_the_review_is_reviewed_again(self):
        """V15: round two reads what the fix wrote."""
        self.end_spec()
        code, err, _ = self.run_stop(transcript_path=self.transcript(("implementer", True), ("reviewer", True),
                                                                     ("implementer", True)), background_tasks=[])
        self.assertEqual(code, 2)
        self.assertIn("No reviewer has returned", err)
        self.assertEqual(self.state()["mode"], "sealed")


class Triage(Project):
    """V18, V19: against edge-case hunting. A failure gets its second attempt after it was examined and no third
    attempt at a symptom; `observe` (a possible fault that needs watching) is the late exit, never the early one."""

    POINTS = ("## Points\n\n| # | Point | Evidence line expected | State |\n|---|---|---|---|\n"
              "| 1 | login | `cargo test login → 3 passed` | [x] |\n| 2 | refresh | `cargo test refresh → 1 passed` | [ ] |\n"
              "\n## Refinement\n\n- feasibility: fine → adopted\n")
    FLAKY = ("- point 2 | cause: unknown after src/auth.rs and three runs | repro: `cargo test refresh` → 1 of 3 | "
             "impact: the suite, one run in three | observe: a red refresh test in CI")

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: in-progress\n---\n# Login\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        (self.root / "src").mkdir()
        (self.root / "src" / "auth.rs").write_text("a\nb\nc\n", "utf-8")
        self.docket.write_text(self.POINTS, "utf-8")

    def test_what_is_observed_reaches_the_user_as_a_line_to_watch(self):
        self.docket.write_text("| 1 | login | `x` | [x] |\n## Triage\n\n" + self.FLAKY.replace("point 2", "point 1") + "\n",
                               "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("no `watch:` line", err)
        self.assertEqual(self.state()["mode"], "sealed")
        # The user cuts a spec from the line and has nothing else at hand: a line that is not the whole case is refused.
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.end_spec(lines=["watch: the refresh test failed once in three runs; a red refresh test in CI shows it again"])
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("has no `seen:`, `suspected:`, `impact:`, `signal:`", err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.end_spec(lines=[
            "watch: the refresh test fails now and then | seen: `cargo test refresh | tail` was red in one of three "
            "runs on this branch, green on the two others | suspected: the expiry in src/auth.rs is read before the "
            "refresh has set it, when both land in the same second | impact: a user is logged out once, right after "
            "an hour | signal: a red refresh test in CI, or a 401 right after a refresh in the access log"])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        # The whole case stays in the docket's Terminal state; the closing message says what it is about.
        lines = self.closing_lines()
        self.assertEqual(lines[2:4], ["**For you to do:** nothing.",
                                      "**To watch (1):** The refresh test fails now and then."])
        self.assertTrue(lines[-2].endswith("/work/dockets/`"), lines)
        self.assertNotIn("Seen:", self.closing())


class AcceptanceCommands(Project):
    """V16: a delivered point's own command ran after the last implementer or fixer and did not fail. The Stop hook
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
        code, err, _ = self.deliver("implementer", "cargo test login", "reviewer")
        self.assertEqual(code, 2)
        self.assertIn("point 2 (`cargo test | grep ok`) never ran", err)
        self.assertNotIn("point 1", err)
        self.assertNotIn("point 3", err)  # a file and what it must contain: no command
        self.assertNotIn("point 4", err)  # a deviation delivers nothing
        self.assertEqual(self.state()["mode"], "sealed")
        code, err, _ = self.deliver("cargo test login", "implementer", "cargo test | grep ok", "reviewer")
        self.assertEqual(code, 2)
        self.assertIn("point 1 (`cargo test login`) last ran before the last implementer or fixer returned", err)
        code, err, _ = self.deliver("implementer", "reviewer", ("cargo test login", "failed"), "cargo test | grep ok")
        self.assertEqual(code, 2)
        self.assertIn("point 1 (`cargo test login`) failed in its last run", err)
        self.deliver("implementer", ("cargo test login", "failed"), "reviewer", "cd app && cargo test login",
                     "cargo test | grep ok")
        self.assertEqual(self.state()["mode"], "closing")


class InboxNotice(Project):
    """V11: a note typed during the run is no violation, and the closing message says what came of it
    (luradb-web-client 2026-11-06: an English notice, counted as a breach). Since 0.5.4 the note also reaches the
    orchestrator, which answers it on the screen."""

    def test_the_note_is_uncounted_and_answered_at_the_end(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| user_language | `Deutsch` | |\n", "utf-8")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}")
        code, err, out = self.prompt("bitte auch das Logo prüfen")
        self.assertEqual((code, err.strip()), (0, ""))
        self.assertIn("written in Deutsch", out)
        self.assertFalse((self.work / "metrics" / f"{SPEC}.violations.json").exists())
        self.docket.write_text("| 1 | login | `x` | [x] |\n## Inbox\n\n- Logo geprüft, es bleibt unverändert.\n", "utf-8")
        self.end_spec(result="Der Login funktioniert, drei Tests sind grün.")
        self.run_stop()
        self.assertRegex(self.closing(), r"Deine Nachricht von \d{1,2}:\d{2}: Logo geprüft, es bleibt unverändert\.")


class Preconditions(Project):
    """V12: what only the user can provide is checked before the session seals (luradb-web-client 2026-11-06: the
    test server's key was invalid at sealing; the live test of the first spec never ran)."""

    def write(self, *lines):
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n\n## Preconditions (user only)\n\n"
                             + "".join(f"- {line}\n" for line in lines), "utf-8")

    def test_a_failing_precondition_keeps_the_session_unsealed(self):
        self.write("`echo ready` → ready", "`exit 3` → ok")
        code, err, out = self.prompt(f"/implement {SPEC}")
        self.assertEqual(code, 0, err)
        self.assertIn("Precondition of “Login” not met: `exit 3`", out)
        self.assertIn("(exit 3)", out)
        self.assertNotEqual(self.state().get("mode"), "sealed")
        self.assertFalse((self.work / "metrics" / f"{SPEC}.violations.json").exists())
        self.write("`echo ready` → ready")
        self.assertEqual(self.prompt(f"/implement {SPEC}")[0], 0)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_the_template_has_no_precondition(self):
        template = (HOOKS.parent / "templates" / "spec.md").read_text("utf-8")
        self.spec.write_text(template.replace("id: s-xxxxxxxx", f"id: {SPEC}").replace("state: draft", "state: ready"), "utf-8")
        self.assertEqual(self.prompt(f"/implement {SPEC}")[0], 0)


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
        self.assertEqual(self.state()["mode"], "closing")

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
        self.assertEqual(self.state()["mode"], "closing")

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
        self.assertEqual(self.state()["mode"], "closing")
        text = self.closing()
        self.assertIn("- Login: 1 of 1 points delivered. Login works.", text)
        self.assertIn("**For you to do:** nothing.", text)

    def test_a_block_an_earlier_run_left_does_not_end_the_resumed_spec(self):
        self.end_spec("BLOCKED: start Docker", result="Nothing built.", review=False)
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("Terminal state", err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.end_spec()
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["outcomes"], {SPEC: "delivered"})

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
        self.end_spec("BLOCKED: stopped by user", result="Half of the login is built.", review=False)
        self.run_stop()
        text = self.closing()
        self.assertTrue(text.startswith("Run ended at your request: 0 of 2 specs implemented."), text)
        self.assertNotIn("stopped by user", text)
        self.assertIn("- Logout: not started, because you ended the run.", text)
        self.assertIn(f"**For you to do:** Continue “Login” and have “Logout” implemented: `/docket:implement {SPEC}, "
                      "s-aaaa1111`.", text)

    def test_the_closing_message_reaches_the_user_when_the_last_try_had_no_text(self):
        self.end_spec()
        self.run_stop()
        self.assertEqual(self.run_stop(active=True)[0], 2)
        self.assertEqual(self.run_stop(active=True)[0], 2)
        code, err, out = self.run_stop(active=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["systemMessage"], self.closing().rstrip())
        self.assertEqual(self.state()["mode"], "closed")

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
        self.assertEqual(self.state()["mode"], "closing")
        self.assertIn("Login: 1 of 1 points delivered", self.closing())

    def test_an_untracked_settings_file_git_would_commit_keeps_its_judge(self):
        template = json.loads((HOOKS.parent / "templates" / "judge-hook.json").read_text("utf-8"))
        settings = self.root / ".claude" / "settings.json"
        settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": template["hooks"]["Stop"][0]["hooks"]}]}}), "utf-8")
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        self.set_state(mode="none")
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login\nstate: ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}")
        self.assertIn("You judge", settings.read_text("utf-8"))
        (self.root / ".gitignore").write_text(".claude/\n", "utf-8")
        self.set_state(mode="none")
        self.prompt(f"/implement {SPEC}")
        self.assertNotIn("You judge", settings.read_text("utf-8"))

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

    def test_shared_items_once_none_values_dropped_cut_lines_without_a_period(self):
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC, "s-aaaa1111"], queue=["s-aaaa1111"])
        for spec_id, title in ((SPEC, "Login"), ("s-aaaa1111", "Logout")):
            (self.work / "specs" / "core" / f"{spec_id}-{title.lower()}.md").write_text(
                f"---\nid: {spec_id}\ntitle: {title}\nstate: ready\n---\n", "utf-8")
        for spec_id, title in ((SPEC, "Login"), ("s-aaaa1111", "Logout")):
            (self.work / "dockets" / "core" / f"{spec_id}.md").write_text("| 1 | x | `x` | [x] |\n", "utf-8")
            self.end_spec(spec_id=spec_id, result=f"{title} works.", lines=[
                "user: keine Schritte nötig.",
                "decision: Fehleingaben in einer eigenen Spec festlegen | why: Sie sind heute offen | outside: Die "
                "Spec nennt sie nicht, und was eine Fehleingabe zeigt, entscheidest du",
                "open: " + "lange Zeile " * 30])
            self.assertEqual(self.run_stop(active=spec_id != SPEC)[0], 2)  # the handover, then the closing order
        text = self.closing()
        self.assertNotIn("keine Schritte", text)
        self.assertIn("**For you to do:** nothing.", text)
        # The same proposal of both specs stands once.
        self.assertIn("**Proposals for later, outside the order (1):**\n1. Fehleingaben in einer eigenen Spec "
                      "festlegen – Sie sind heute offen.\n", text)
        self.assertEqual(text.count("Fehleingaben"), 1)
        self.assertNotIn("….", text)
        self.assertNotIn("lange Zeile", text)  # a note for the record: neither shown nor counted
        self.assertNotIn("open point", text)

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
              "    lost += state.get('mode') != 'spec'\n"
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
        self.set_state(mode="spec", read=["a", "b"])
        state_dir = f"{self.p}/.claude/docket"
        workers = [subprocess.Popen([sys.executable, "-c", self.WORKER, str(HOOKS), state_dir, SID, "25",
                                     str(self.work / "DOCKET.md")],
                                    stdout=subprocess.PIPE) for _ in range(15)]
        lost = sum(int(worker.communicate()[0].decode() or 1) for worker in workers)
        self.assertEqual(lost, 0, "a hook read the default state while the file existed")
        self.assertEqual((self.state()["mode"], self.state()["read"]), ("spec", ["a", "b"]))
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
        newer.update({"mode": "spec", "last_command": "/docket:spec"})
        d.save_state(paths, SID, newer)  # the command's hook
        older["clock"] = "2026-11-10 12:53"
        d.save_state(paths, SID, older)
        self.assertEqual((self.state()["mode"], self.state()["last_command"], self.state()["clock"]),
                         ("spec", "/docket:spec", "2026-11-10 12:53"))

    def test_a_hook_that_found_no_state_does_not_undo_the_first_command(self):
        # SessionStart and the hook of the session's first prompt run at the same moment.
        d, paths = self.lib()
        start = d.load_state(paths, SID)  # no file yet
        prompt = d.load_state(paths, SID)
        prompt.update({"mode": "spec"})
        d.save_state(paths, SID, prompt)
        start.update({"degraded": True, "read": []})
        d.save_state(paths, SID, start)
        self.assertEqual(self.state(), {"version": 1, "mode": "spec", "spec": "", "degraded": True, "read": []})

    def test_a_state_that_could_not_be_read_is_never_written_back(self):
        d, paths = self.lib()
        self.set_state(mode="spec", read=["a"])
        read_json = d.read_json
        try:
            d.read_json = lambda path, wait=0: (None, "unreadable")
            blind = d.load_state(paths, SID)
            self.assertTrue(blind.unreadable)
            blind["clock"] = "2026-11-10 12:53"
            d.save_state(paths, SID, blind)  # the file is still closed to this hook: nothing is written
            self.assertEqual(self.state(), {"version": 1, "mode": "spec", "spec": "", "degraded": False, "read": ["a"]})
        finally:
            d.read_json = read_json
        d.save_state(paths, SID, blind)  # readable again: only what the hook changed goes in
        self.assertEqual((self.state()["mode"], self.state()["read"], self.state()["clock"]),
                         ("spec", ["a"], "2026-11-10 12:53"))

    def test_a_file_held_open_by_another_process_is_read_after_a_wait(self):
        d, paths = self.lib()
        self.set_state(mode="spec")
        calls = []
        opened = open

        def flaky(path, *args, **kwargs):
            if str(path).endswith(f"state-{SID}.json") and len(calls) < 3:
                calls.append(path)
                raise PermissionError(13, "The process cannot access the file")
            return opened(path, *args, **kwargs)

        d.open = flaky  # the module's own name for open: only docket_lib sees it
        try:
            self.assertEqual(d.load_state(paths, SID)["mode"], "spec")
        finally:
            del d.open
        self.assertEqual(len(calls), 3)

    def test_an_agents_tool_call_writes_nothing_into_the_state(self):
        for mode in ("spec", "sealed"):
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
        self.assertFalse((self.work / "inbox" / f"{SPEC}.md").exists())
        self.assertFalse(self.state().get("answer_due"))
        self.assertNotIn(" inbox ", self.log())
        # What the user typed beside it is still theirs.
        self.prompt(self.AGENT + "\n\nplease also fix the logo")
        self.assertEqual((self.work / "inbox" / f"{SPEC}.md").read_text("utf-8").count("\n## "), 1)
        self.assertIn("fix the logo", (self.work / "inbox" / f"{SPEC}.md").read_text("utf-8"))

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

    def test_a_fault_of_form_is_sent_back_twice_and_then_let_pass(self):
        self.end_spec("BLOCKED", result="Login is half built.", review=False)  # names nothing the user can resolve
        for _ in range(2):
            code, err, _ = self.run_stop()
            self.assertEqual(code, 2)
            self.assertIn("BLOCKED names what only the user can resolve", err)
            self.assertIn("never for a fault in the docket's own lines", err)
            self.assertEqual(self.state()["mode"], "sealed")
            self.worked()
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual((self.state()["mode"], self.state()["outcomes"]), ("closing", {SPEC: "blocked"}))
        self.assertIn(" waived ", self.log())

    def test_a_turn_that_ends_again_right_after_the_refusal_ends_the_spec(self):
        self.end_spec("BLOCKED", result="Login is half built.", review=False)
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertEqual(self.run_stop(active=True)[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertIn(" waived ", self.log())

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

    def test_a_line_too_long_for_the_message_is_sent_back_and_never_cut(self):
        long_result = "Login works " + "and works " * 30
        self.end_spec(result=long_result)
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("a `result:` line has at most 200 characters", err)
        self.worked()
        step = "open the settings of the server and " * 9 + "restart it"
        self.end_spec(result="Login works.", lines=["user: " + step])
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("a `user:` line has at most 300 characters", err)
        # Sent back three times unchanged, the line stands in the message whole.
        for _ in range(2):
            self.worked()
            self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertIn(step[0].upper() + step[1:] + ".", self.closing())
        self.assertNotIn("…", self.closing())

    PROPOSAL = ("decision: Handle wrong input at the login | why: It accepts an empty password today | outside: The "
                "spec orders the happy path only, and what a wrong input shows is a decision of yours")

    def test_a_proposal_says_why_and_what_kept_the_run_from_doing_it(self):
        # luradb-web-client 2026-11-11: four pieces of advice as sentences, each with the same bare command; two of
        # them the run could have done itself, and nothing asked why it had not.
        self.end_spec(lines=["decision: I advise you to have wrong input handled in a spec of its own, because the "
                             "login accepts an empty password today. Command: /docket:spec"])
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("a proposal for work after the run and says why the run did not do that work itself", err)
        self.assertIn("has no `why:`, `outside:`", err)
        self.worked()
        self.end_spec(lines=[self.PROPOSAL])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        text = self.closing()
        self.assertIn("**For you to do:** nothing.\n\n**Proposals for later, outside the order (1):**\n"
                      "1. Handle wrong input at the login – It accepts an empty password today.\n\n"
                      "A spec from one: `/docket:spec` and the proposal's number.\n", text)
        self.assertNotIn("a decision of yours", text)  # what kept the run from it stays in the docket
        self.assertNotIn("I advise", text)

    def test_a_proposal_in_the_form_of_before_never_holds_a_spec(self):
        old = ("decision: I advise you to have wrong input handled in a spec of its own, because the login accepts an "
               "empty password today. Command: /docket:spec")
        self.end_spec(lines=[old])
        for _ in range(2):
            self.assertEqual(self.run_stop()[0], 2)
            self.assertEqual(self.state()["mode"], "sealed")
            self.worked()
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertIn(" waived ", self.log())
        self.assertIn("1. " + old[len("decision: "):] + ".", self.closing())

    def test_a_proposal_is_taken_up_by_its_number(self):
        self.end_spec(lines=[self.PROPOSAL])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.run_stop(self.closing(), active=True)[0], 0)
        self.assertEqual(self.state()["mode"], "closed")
        code, err, out = self.prompt("/docket:spec 1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "spec")
        # The spec session gets the proposal whole: the finished docket is no reference there.
        self.assertIn("[docket] Proposal 1 of the last run, from “Login”: Handle wrong input at the login. Why: It "
                      "accepts an empty password today. Why the run did not do it itself: The spec orders the happy "
                      "path only, and what a wrong input shows is a decision of yours.", out)
        self.assertIn("The last run has no proposal 4.", self.prompt("/docket:spec 4")[2])
        self.assertNotIn("Proposal", self.prompt("/docket:spec Wrong input at the login")[2])  # a title, as before

    def test_a_deferred_finding_reaches_the_user_by_name(self):
        # luradb-web-client 2026-11-11: three review findings deferred "for the user to decide" stood behind a count.
        self.docket.write_text(self.docket.read_text("utf-8") + "\n## Review\n\n- code | the error line of the last "
                               "save can come back | src/admin.ts:3 | test | deferred\n- spec | no findings\n", "utf-8")
        self.end_spec(lines=["open: the error line of the last save can come back"])
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("the Review section defers 1 finding(s) and the Terminal state has 0 `decision:` or `user:` "
                      "line(s)", err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.worked()
        self.end_spec(lines=["decision: Clear the error line of the last save when the next one starts | why: An old "
                             "error can stand beside a save that went through | outside: Three rounds were spent on "
                             "the review, and this finding came in the third"])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertIn("1. Clear the error line of the last save when the next one starts – An old error can stand "
                      "beside a save that went through.", self.closing())

    def test_what_the_run_repaired_outside_the_order_stands_in_the_message(self):
        self.end_spec(lines=["repaired: The command that fetches the types ends with exit code 1 on a wrong name now",
                             "repaired: The change list no longer holds the line the new one contradicts"])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        self.assertEqual(self.closing_lines()[2:6], [
            "**For you to do:** nothing.",
            "**Also repaired, outside the order (2):**",
            "- The command that fetches the types ends with exit code 1 on a wrong name now.",
            "- The change list no longer holds the line the new one contradicts."])
        self.worked()
        self.set_state(mode="sealed", spec=SPEC, series=[SPEC])
        self.end_spec(lines=["repaired: " + "the command that fetches the types " * 6])
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("a `repaired:` line has at most 160 characters", err)

    def test_a_red_delivery_says_what_made_it_red(self):
        self.docket.write_text(self.docket.read_text("utf-8") + "\n## Evidence\n\n- point 1: ok. CI: red\n", "utf-8")
        self.end_spec()
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("so the delivery is red: repair what made it red in this run", err)
        self.worked()
        self.end_spec(lines=["decision: Repair the two clock tests | why: One of them turned this CI run red and will do "
                             "so again | outside: Three rounds were spent on them and the cause is not found"])
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.closing().splitlines()[0], "Run finished: “Login” is implemented, CI red.")

    def test_messages_no_line_answers_are_one_line(self):
        for text in ("first", "second", "third"):
            self.prompt(text)
        self.end_spec()
        self.run_stop()
        self.assertEqual(self.state()["mode"], "closing")
        text = self.closing()
        self.assertRegex(text, r"\n3 messages of yours are not handled yet \(\d{1,2}:\d{2}, \d{1,2}:\d{2}, \d{1,2}:\d{2}\)\.\n")
        self.assertEqual(text.count("not handled"), 1)

    def test_a_line_that_answers_several_messages_stands_once_and_calls_none_unhandled(self):
        for text in ("first", "second", "third"):
            self.prompt(text)
        self.docket.write_text(self.docket.read_text("utf-8") + "\n## Inbox\n\n- All three notes were about the logo; "
                               "it stays as it is.\n", "utf-8")
        self.end_spec()
        self.run_stop()
        text = self.closing()
        self.assertEqual(text.count("On your messages: All three notes were about the logo; it stays as it is."), 1)
        self.assertNotIn("not handled", text)

    def test_a_closed_session_may_list_what_is_open(self):
        self.set_state(mode="closed", spec=SPEC)
        answer = "\n".join(f"- open point {n}" for n in range(1, 15)) + "\n" + hstamp()
        self.assertEqual(self.stop(answer)[0], 0)


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

    def test_uncommitted_records_are_a_step_with_its_commands(self):
        self.end_spec()
        self.assertEqual(self.run_stop()[0], 2)
        self.assertIn('**For you to do:** Commit the records: `git -C concept add docket`, then `git -C concept commit '
                      '-m "docket: records" -- docket`.', self.closing())
        self.assertNotIn("nothing.", self.closing())

    def test_the_hook_commits_them_itself_where_the_project_says_so(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n"
                                             "| commit_records | `yes` | |\n", "utf-8")
        self.end_spec()
        self.assertEqual(self.run_stop()[0], 2)
        self.assertNotIn("Commit the records", self.closing())
        self.assertIn("**For you to do:** nothing.", self.closing())
        self.assertIn("docket: records", self.git("log", "--oneline"))
        self.assertEqual(self.run_stop(self.closing(), active=True)[0], 0)
        self.assertEqual(self.git("status", "--porcelain").strip(), "")


class ReplayOfTheTenth(Project):
    """The run of luradb-web-client 2026-11-10 (four specs, three of them pushed, the third ended BLOCKED over its own
    record, the fourth never started): its closing message had 29 lines, twelve of them the same sentence, six cut
    with "…", "CI grün" above a red run and a command of bare ids."""

    A, B, C, D = "s-aaaa1111", "s-bbbb2222", "s-cccc3333", "s-dddd4444"
    TITLES = {A: "Contract-Re-Pin", B: "Explorer-Query-Keys", C: "Tabellen-Anlage aus Datei", D: "Rechte-Bestand anzeigen"}
    WATCH = ("watch: Zwei Tests vergleichen eine Restzeit mit der echten Uhr und schlagen deshalb vereinzelt fehl | "
             "seen: der Test der Backup-Karte war in einem von drei Läufen rot | suspected: die Uhr springt zwischen "
             "Berechnung und Prüfung um | impact: ein roter Lauf ohne Fehler im Produkt | signal: ein roter Lauf mit "
             "einer Restzeit, die um eine Einheit abweicht")
    TESTS = ("decision: Die zwei Tests der Backup-Karte und des KV-Browsers von der echten Uhr lösen | why: Sie "
             "schlagen vereinzelt fehl, obwohl nichts kaputt ist | outside: Drei Runden haben die Ursache nicht "
             "gefunden")
    SWAGGER = ("decision: Den veralteten Swagger-Zusatz in der Änderungsliste berichtigen | why: Er nennt eine "
               "Adresse, die es nicht mehr gibt | outside: Die Zeile steht in einem veröffentlichten Release, das "
               "änderst nur du")

    def setUp(self):
        super().setUp()
        self.git = lambda *args: subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c",
                                                 "user.email=t@t"] + list(args), capture_output=True, check=True,
                                                text=True).stdout.strip()
        remote = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git("init", "-q")
        (self.root / ".gitignore").write_text(".claude/\n", "utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "-u", "origin", "HEAD")
        self.branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n"
                                             "| user_language | `Deutsch` | |\n", "utf-8")
        for spec_id, title in self.TITLES.items():
            state = "ready" if spec_id == self.D else "in-progress"
            (self.work / "specs" / "core" / f"{spec_id}.md").write_text(
                f"---\nid: {spec_id}\ntitle: {title}\nstate: {state}\n---\n# {title}\n", "utf-8")
        self.close(self.A, "Der Client arbeitet jetzt gegen die neue Fassung des Servers.", [self.SWAGGER],
                   evidence="- Punkt 1: in Ordnung. CI: green\n")
        self.close(self.B, "Die Schlüssel der Abfragen stehen jetzt an einer Stelle im Code.", [self.WATCH, self.TESTS],
                   evidence="- Punkt 1: in Ordnung. CI: green\n")
        self.set_state(mode="sealed", spec=self.C, series=[self.A, self.B, self.C, self.D], queue=[self.D],
                       outcomes={self.A: "delivered", self.B: "delivered"}, sealed_head="0" * 40)

    def close(self, spec_id, result, lines=(), kind="DELIVERED", evidence=""):
        (self.work / "dockets" / "core" / f"{spec_id}.md").write_text(
            "# Docket\n| 1 | x | `x` | [x] |\n" + ("\n## Evidence\n\n" + evidence if evidence else ""), "utf-8")
        self.end_spec(kind, result=result, lines=lines, spec_id=spec_id)

    def checked(self, text):
        lines = [line for line in text.splitlines() if line]  # the blocks' lines, without the blank lines between
        self.assertEqual(len(lines), len(set(lines)), text)  # no sentence twice
        self.assertNotIn("…", text)
        self.assertNotIn("blockiert", text)
        for line in lines:
            if "s-" in line and any(spec_id in line for spec_id in self.TITLES):
                self.assertIn("`/docket:implement ", line)  # an id only inside a command
                self.assertIn("„", line)  # whose line names the titles
        return lines

    def test_as_the_run_went_the_message_says_what_happened(self):
        self.close(self.C, "Im Explorer lässt sich jetzt aus einer Datei eine Tabelle anlegen.", kind=(
            "BLOCKED: Der Abschluss dieser Spec ist offen, weil eine alte Zeile ihres Protokolls zwei Zeilennummern "
            "einer Datei nennt, die inzwischen kürzer ist"), lines=[
            "user: Den roten Lauf auf GitHub unter Actions neu starten",
            "user: In der Datei des Protokolls die beiden veralteten Zeilennummern entfernen",
            self.WATCH, self.TESTS], evidence="- Punkt 1: in Ordnung, siehe src/shell/domains.ts:99. CI: red\n")
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        # Its work is pushed: the series goes on, and the next spec is told what the run already advised.
        self.assertIn("Spec 4 von 4 ist versiegelt: Rechte-Bestand anzeigen", err)
        self.assertIn("schon vorgeschlagen", err)
        self.assertIn("Swagger-Zusatz", err)
        self.assertEqual(self.state()["spec"], self.D)
        self.close(self.D, "Die Rechte einer Rolle stehen jetzt in einer Liste.")
        self.assertEqual(self.run_stop(active=True)[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        lines = self.checked(self.closing())
        self.assertEqual(lines[0], "Lauf beendet: 4 von 4 Specs umgesetzt, „Tabellen-Anlage aus Datei“ braucht dich, "
                                   f"auf `{self.branch}` gepusht, CI rot.")
        self.assertTrue(lines[3].startswith("- Tabellen-Anlage aus Datei: umgesetzt, 1 von 1 Punkten geliefert, nicht "
                                            "abgeschlossen. Im Explorer lässt sich jetzt aus einer Datei eine Tabelle "
                                            "anlegen. Grund: Der Abschluss dieser Spec ist offen"), lines[3])
        self.assertEqual(lines[5:11], [
            "**Für dich zu tun:**",
            "Für „Tabellen-Anlage aus Datei“:",
            "1. Den roten Lauf auf GitHub unter Actions neu starten.",
            "2. In der Datei des Protokolls die beiden veralteten Zeilennummern entfernen.",
            "Danach:",
            "3. „Tabellen-Anlage aus Datei“ fortsetzen: `/docket:implement s-cccc3333`."])
        # The same proposal of two specs, and the same thing to watch, stand once.
        self.assertEqual(lines[11:16], [
            "**Vorschläge für später, außerhalb des Auftrags (2):**",
            "1. Den veralteten Swagger-Zusatz in der Änderungsliste berichtigen – Er nennt eine Adresse, die es nicht "
            "mehr gibt.",
            "2. Die zwei Tests der Backup-Karte und des KV-Browsers von der echten Uhr lösen – Sie schlagen vereinzelt "
            "fehl, obwohl nichts kaputt ist.",
            "Eine Spec daraus: `/docket:spec` und die Nummer des Vorschlags.",
            "**Zu beobachten (1):** Zwei Tests vergleichen eine Restzeit mit der echten Uhr und schlagen deshalb "
            "vereinzelt fehl."])
        self.assertEqual(len(lines), 18)

    def test_as_the_run_goes_now_it_is_delivered_in_thirteen_lines(self):
        # The stale citation holds nothing, the red run was started again and is green.
        self.close(self.C, "Im Explorer lässt sich jetzt aus einer Datei eine Tabelle anlegen.",
                   evidence="- Punkt 1: in Ordnung, siehe src/shell/domains.ts:99. CI: red\n- CI: green\n")
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["outcomes"][self.C], "delivered")
        self.close(self.D, "Die Rechte einer Rolle stehen jetzt in einer Liste.")
        self.assertEqual(self.run_stop(active=True)[0], 2)
        lines = self.checked(self.closing())
        self.assertEqual(lines[0], f"Lauf beendet: 4 von 4 Specs umgesetzt, auf `{self.branch}` gepusht, CI grün.")
        self.assertLessEqual(len(lines), 13)
        self.assertEqual(lines[5], "**Für dich zu tun:** nichts.")
        self.assertNotRegex(self.closing(), r"\bs-[0-9a-f]{8}\b")

    def test_a_spec_that_did_not_start_says_why_and_is_named_by_its_title(self):
        (self.root / "half.txt").write_text("half\n", "utf-8")  # work left in the project: the series ends here
        self.close(self.C, "Die Tabellen-Anlage ist zur Hälfte gebaut.", kind="BLOCKED: Docker starten")
        self.assertEqual(self.run_stop()[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        text = self.closing()
        self.checked(text)
        self.assertIn("- Tabellen-Anlage aus Datei: angehalten bei 1 von 1 Punkten. Die Tabellen-Anlage ist zur Hälfte "
                      "gebaut.", text)
        self.assertIn("- Rechte-Bestand anzeigen: nicht begonnen, weil der Lauf bei „Tabellen-Anlage aus Datei“ "
                      "endete.", text)
        self.assertIn("„Tabellen-Anlage aus Datei“ fortsetzen und „Rechte-Bestand anzeigen“ umsetzen lassen: "
                      "`/docket:implement s-cccc3333, s-dddd4444`.", text)


class ReplayOfTheEleventh(Project):
    """The run of luradb-web-client 2026-11-11 (two specs, both delivered, pushed, CI green). Its closing message:
    every line after the second spec hung in that spec's list item; four pieces of advice as sentences, each with
    the same bare command, two of them work the run had caused or measured itself (a line of the change list its own
    new line contradicted, the exit code of a script it had just run); one matter three times; "15 offene Punkte:
    frag hier danach", five of them the advice again and five of them things only a question would have brought up.
    As the run goes now: what it could settle it settled and says so, one proposal is left with what kept the run
    from it, and nothing is counted."""

    A, B = "s-aaaa1111", "s-bbbb2222"
    TITLES = {A: "Rechte-Bestand anzeigen, Domain-Validierung tragen", B: "What's-new-Dialog für Server-Releases"}

    def setUp(self):
        super().setUp()
        self.git = lambda *args: subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c",
                                                 "user.email=t@t"] + list(args), capture_output=True, check=True,
                                                text=True).stdout.strip()
        remote = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git("init", "-q")
        (self.root / ".gitignore").write_text(".claude/\n", "utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one")
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "-u", "origin", "HEAD")
        self.branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n"
                                             "| user_language | `Deutsch` | |\n", "utf-8")
        for spec_id, title in self.TITLES.items():
            state = "in-progress" if spec_id == self.A else "ready"
            (self.work / "specs" / "core" / f"{spec_id}.md").write_text(
                f"---\nid: {spec_id}\ntitle: {title}\nstate: {state}\n---\n# {title}\n", "utf-8")
        self.set_state(mode="sealed", spec=self.A, series=[self.A, self.B], queue=[self.B], sealed_head="0" * 40)

    def close(self, spec_id, result, lines):
        (self.work / "dockets" / "core" / f"{spec_id}.md").write_text(
            "# Docket\n| 1 | x | `x` | [x] |\n\n## Evidence\n\n- Punkt 1: in Ordnung. CI: green\n", "utf-8")
        self.end_spec(result=result, lines=lines, spec_id=spec_id)

    def test_the_message_of_the_run_as_it_goes_now(self):
        self.close(self.A, "Admins sehen in der Rechte-Karte die gespeicherten Rechte und vergeben sie für die ganze "
                           "Domäne oder einzeln; Fehler zeigen den Wortlaut des Servers.", [
            "repaired: In der Änderungsliste steht die Zeile nicht mehr, der die neue Zeile widerspricht",
            "decision: Die Rechte-Karte für viele Domänen und schmale Fenster neu ordnen | why: Ab vier Domänen kürzen "
            "die Chips in schmalen Fenstern ihren Text ab, und die Spaltenköpfe stehen versetzt | outside: Die Spec "
            "schließt eine neue Spaltenlogik aus, und wie die Karte dann aussieht, entscheidest du",
            "open: Der Text des Servers steht ohne Statusnummer in der Fehlerzeile, wie die Spec es ordnet",
            "open: Der erste Lauf auf GitHub war rot, wurde repariert und ist grün"])
        code, err, _ = self.run_stop()
        self.assertEqual(code, 2)
        self.assertIn("Spec 2 von 2 ist versiegelt: What's-new-Dialog für Server-Releases", err)
        self.close(self.B, "Ein Klick auf das Server-Label der Statusleiste öffnet einen Dialog mit den Änderungen "
                           "aller Versionen des Servers.", [
            "repaired: Der Befehl, der die Typen holt, endet bei einem falschen Namen jetzt mit Exit-Code 1",
            "watch: Ein Test eines anderen Bereichs läuft unter Last manchmal in sein Zeitlimit | seen: der Testlauf "
            "war in einem von vier Läufen rot, als ein zweiter Lauf gleichzeitig lief | suspected: die Tests haben "
            "kein eigenes Zeitlimit und teilen sich alle Kerne | impact: ein roter Lauf ohne Fehler im Produkt | "
            "signal: ein roter Lauf, in dem ein Test eines anderen Bereichs sein Zeitlimit erreicht",
            "open: Das Server-Label bricht bei 1024 Pixeln um wie vor dem Lauf"])
        self.assertEqual(self.run_stop(active=True)[0], 2)
        self.assertEqual(self.state()["mode"], "closing")
        text = self.closing()
        head, _, rest = text.rpartition("\nProtokolle: `")
        self.assertRegex(rest, r"^[^`\n]*/work/dockets/`\nstamp: \d{4}-\d{2}-\d{2} \d{2}:\d{2}\n$")
        self.assertEqual(head, "\n".join([
            f"Lauf beendet: 2 von 2 Specs umgesetzt, auf `{self.branch}` gepusht, CI grün.",
            "",
            "- Rechte-Bestand anzeigen, Domain-Validierung tragen: 1 von 1 Punkten geliefert. Admins sehen in der "
            "Rechte-Karte die gespeicherten Rechte und vergeben sie für die ganze Domäne oder einzeln; Fehler zeigen "
            "den Wortlaut des Servers.",
            "- What's-new-Dialog für Server-Releases: 1 von 1 Punkten geliefert. Ein Klick auf das Server-Label der "
            "Statusleiste öffnet einen Dialog mit den Änderungen aller Versionen des Servers.",
            "",
            "**Für dich zu tun:** nichts.",
            "",
            "**Außerdem behoben, außerhalb des Auftrags (2):**",
            "- In der Änderungsliste steht die Zeile nicht mehr, der die neue Zeile widerspricht.",
            "- Der Befehl, der die Typen holt, endet bei einem falschen Namen jetzt mit Exit-Code 1.",
            "",
            "**Vorschläge für später, außerhalb des Auftrags (1):**",
            "1. Die Rechte-Karte für viele Domänen und schmale Fenster neu ordnen – Ab vier Domänen kürzen die Chips "
            "in schmalen Fenstern ihren Text ab, und die Spaltenköpfe stehen versetzt.",
            "",
            "Eine Spec daraus: `/docket:spec` und die Nummer des Vorschlags.",
            "",
            "**Zu beobachten (1):** Ein Test eines anderen Bereichs läuft unter Last manchmal in sein Zeitlimit.",
            ""]))
        for gone in ("offene", "frag hier", "Ich rate dir", "Befehl:", "Empfehlung", "Statusnummer", "1024"):
            self.assertNotIn(gone, text)
        # The message is sent as rendered.
        self.assertEqual(self.run_stop(text, active=True)[0], 0)
        self.assertEqual(self.state()["mode"], "closed")
        self.assertIn("Vorschlag 1 des letzten Laufs, aus „Rechte-Bestand anzeigen, Domain-Validierung tragen“: Die "
                      "Rechte-Karte für viele Domänen und schmale Fenster neu ordnen.", self.prompt("/docket:spec 1")[2])


if __name__ == "__main__":
    unittest.main()
