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

    def test_implement_without_id_and_without_a_ready_spec_is_refused(self):
        self.spec.write_text(f"---\nid: {SPEC}\nstate: draft\n---\n# Login\n", "utf-8")
        code, err, _ = self.prompt("/implement")
        self.assertEqual(code, 2)
        self.assertIn("No ready spec", err)

    def test_implement_with_several_ids_seals_the_first_and_queues_the_rest(self):
        # luradb-web-client 2026-11-06: five ids in one call, the first was sealed, the other four vanished unnamed.
        code, err, out = self.prompt(f"/docket:implement {SPEC},s-aaaa1111 s-bbbb2222")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["spec"], SPEC)
        self.assertEqual(self.state()["queue"], ["s-aaaa1111", "s-bbbb2222"])
        self.assertIn("Queued after this spec, in order: s-aaaa1111, s-bbbb2222", out)
        self.assertIn("queued", self.log())
        for spec in (SPEC, "s-aaaa1111", "s-bbbb2222"):
            self.assertEqual((self.work / "locks" / spec).read_text("utf-8"), SID)

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

    def test_free_text_in_sealed_goes_to_inbox(self):
        self.prompt(f"/implement {SPEC}")
        code, err, _ = self.prompt("please also fix the logo")
        self.assertEqual(code, 2)
        self.assertIn("inbox", err)
        self.assertIn("fix the logo", (self.work / "inbox" / f"{SPEC}.md").read_text("utf-8"))
        self.assertEqual(self.state()["mode"], "sealed")

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

    def test_concept_command_refused_while_sealed(self):
        self.prompt(f"/implement {SPEC}")
        code, err, _ = self.prompt("/concept")
        self.assertEqual(code, 2)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_reminder_names_open_required_reading(self):
        self.set_state(mode="concept", degraded=True)
        code, _, out = self.prompt("hello")
        self.assertEqual(code, 0)
        self.assertIn("Required reading still open", out)


class WriteRights(Project):
    def test_frozen_spec_refused_in_spec_mode(self):
        self.set_state(mode="spec")
        code, err, _ = self.tool("Edit", file_path=str(self.spec))
        self.assertEqual(code, 2)
        self.assertIn("frozen", err)

    def test_draft_spec_writable_in_spec_mode(self):
        self.set_state(mode="spec")
        draft = self.work / "specs" / "core" / "s-0000ffff-draft.md"
        draft.write_text("---\nstate: draft\n---\n", "utf-8")
        code, err, _ = self.tool("Edit", file_path=str(draft))
        self.assertEqual(code, 0, err)

    def test_rewrite_opens_only_the_named_spec(self):
        self.set_state(mode="rewrite", rewrite=SPEC)
        self.assertEqual(self.tool("Edit", file_path=str(self.spec))[0], 0)
        other = self.work / "specs" / "core" / "s-0000ffff-other.md"
        other.write_text("---\nstate: ready\n---\n", "utf-8")
        self.assertEqual(self.tool("Edit", file_path=str(other))[0], 2)

    def test_sealed_never_writes_specs(self):
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = self.tool("Write", file_path=str(self.spec))
        self.assertEqual(code, 2)

    def test_concept_mode_writes_no_specs(self):
        self.set_state(mode="concept")
        draft = self.work / "specs" / "core" / "s-0000ffff-draft.md"
        code, err, _ = self.tool("Write", file_path=str(draft), content="---\nstate: draft\n---\n")
        self.assertEqual(code, 2)
        self.assertIn("/docket:spec", err)
        self.assertIn("end the turn", err)
        self.assertEqual(self.tool("Write", file_path=str(self.work / "concepts" / "core" / "c-0000ffff.md"), content="x")[0], 0)

    def test_model_may_not_invoke_docket_commands_itself(self):
        self.prompt("/concept a done command")
        code, err, _ = self.tool("Skill", skill="docket:spec")
        self.assertEqual(code, 2)
        self.assertIn("user", err)
        self.assertEqual(self.tool("Skill", skill="docket:concept")[0], 0)  # the one the user typed
        self.assertEqual(self.tool("Skill", skill="anthropic-skills:pdf")[0], 0)
        self.prompt("/docket:status")
        self.assertEqual(self.tool("Skill", skill="docket:status")[0], 0)
        self.assertEqual(self.tool("Skill", skill="docket:stop")[0], 2)
        self.assertEqual(self.tool("Skill", skill="docket:concept")[0], 2)

    def test_template_state_comment_is_ignored(self):
        self.set_state(mode="spec")
        self.spec.write_text(f"---\nid: {SPEC}\nstate: ready          # draft | ready | in-progress\n---\n# Login\n", "utf-8")
        code, err, _ = self.tool("Edit", file_path=str(self.spec))
        self.assertEqual(code, 2)
        self.assertIn("frozen", err)

    def test_docket_only_in_sealed_and_only_own(self):
        self.set_state(mode="concept")
        self.assertEqual(self.tool("Write", file_path=str(self.docket))[0], 2)
        self.set_state(mode="sealed", spec="s-99999999")
        self.assertEqual(self.tool("Write", file_path=str(self.docket))[0], 2)
        self.set_state(mode="sealed", spec=SPEC)
        self.assertEqual(self.tool("Write", file_path=str(self.docket))[0], 0)

    def test_existing_docket_refuses_write_allows_edit(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("# Docket\n- [ ] point 1\n", "utf-8")
        self.assertEqual(self.tool("Write", file_path=str(self.docket))[0], 2)
        self.assertEqual(self.tool("Edit", file_path=str(self.docket))[0], 0)
        self.assertTrue((self.root / ".claude" / "docket" / f"snap-{SPEC}.md").exists())

    def test_log_never_written_by_tools(self):
        self.set_state(mode="concept")
        code, err, _ = self.tool("Write", file_path=str(self.work / "LOG.md"))
        self.assertEqual(code, 2)
        self.assertIn("hooks only", err)

    def test_ask_user_refused_when_sealed(self):
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = self.tool("AskUserQuestion", questions=[])
        self.assertEqual(code, 2)
        self.assertIn("sealed", err)

    def test_closed_refuses_writes_and_agents(self):
        self.set_state(mode="closed", spec=SPEC)
        self.assertEqual(self.tool("Write", file_path=str(self.root / "src" / "x.py"))[0], 2)
        self.assertEqual(self.tool("Agent", prompt="x")[0], 2)
        self.assertEqual(self.tool("Read", file_path=str(self.root / "src" / "x.py"))[0], 0)

    def test_degraded_blocks_until_required_reading(self):
        self.set_state(mode="concept", degraded=True)
        self.assertEqual(self.tool("Write", file_path=str(self.root / "a.md"))[0], 2)
        self.assertEqual(self.tool("Bash", command="ls")[0], 2)
        self.assertEqual(self.tool("Read", file_path=str(self.work / "DOCKET.md"))[0], 0)
        self.assertFalse(self.state()["degraded"])
        self.assertEqual(self.tool("Write", file_path=str(self.root / "a.md"))[0], 0)

    def test_done_spec_is_not_a_reference(self):
        done = self.work / "specs" / "core" / "s-0000aaaa-old.md"
        done.write_text("---\nstate: done\n---\n", "utf-8")
        self.set_state(mode="spec")
        code, err, _ = self.tool("Read", file_path=str(done))
        self.assertEqual(code, 2)
        self.assertIn("not a reference", err)
        self.set_state(mode="concept")
        self.assertEqual(self.tool("Read", file_path=str(done))[0], 0)

    def test_registered_check_command_is_allowed(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | hello | `cat hello.txt` → `hello` | [ ] |\n", "utf-8")
        self.assertEqual(self.tool("Bash", command="cat hello.txt")[0], 0)
        self.assertEqual(self.tool("Bash", command="cat other.txt")[0], 2)

    def test_shell_read_redirected(self):
        self.set_state(mode="spec")
        self.assertEqual(self.tool("Bash", command="cat README.md")[0], 2)
        self.assertEqual(self.tool("Bash", command="cat > out.md <<'EOF'\nx\nEOF")[0], 0)
        self.assertEqual(self.tool("Bash", command="git status")[0], 0)


class AppendOnly(Project):
    def post(self, path):
        return run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Edit",
                                             "tool_input": {"file_path": str(path)}}, self.p)

    def test_filling_placeholders_and_inserting_lines_passes(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("# Docket\n\n## Refinement\n\n- \n\n## Evidence\n\n- \n", "utf-8")
        self.tool("Edit", file_path=str(self.docket))
        self.docket.write_text("# Docket\n\n## Refinement\n\n- feasibility: ok\n- spec: ok\n\n## Evidence\n\n- point 1: cat → hello\n", "utf-8")
        self.assertEqual(self.post(self.docket)[0], 0)

    def test_append_passes_rewrite_fails(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("# Docket\n- [ ] point 1\n", "utf-8")
        self.tool("Edit", file_path=str(self.docket))  # takes the snapshot
        self.docket.write_text("# Docket\n- [x] point 1\n- deviation: none\n", "utf-8")
        self.assertEqual(self.post(self.docket)[0], 0)
        self.tool("Edit", file_path=str(self.docket))
        self.docket.write_text("# Docket\n- [x] point 1 (changed wording)\n", "utf-8")
        code, err, _ = self.post(self.docket)
        self.assertEqual(code, 2)
        self.assertIn("append-only", err)
        self.assertIn("docket-rewritten", self.log())


class SubagentReports(Project):
    def report(self, text):
        return run_hook("subagent_stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": text,
                                             "stop_hook_active": False}, self.p)

    def test_short_report_with_unverified_line_passes(self):
        self.assertEqual(self.report("Done: tests 12 passed (cargo test).\nUnverified: none")[0], 0)

    def test_missing_unverified_line_refused(self):
        code, err, _ = self.report("Done: tests passed.")
        self.assertEqual(code, 2)
        self.assertIn("Unverified", err)

    def test_long_report_refused(self):
        text = "\n".join(f"line {i}" for i in range(50)) + "\nUnverified: none"
        self.assertEqual(self.report(text)[0], 2)


class SealedStop(Project):
    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("# Docket\n- [x] point 1: tests 3 passed\n", "utf-8")

    def test_delivered_closes_session_and_logs(self):
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nLogin works, 3 tests, CI green.\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "closed")
        self.assertIn("delivered", self.log())

    def test_plain_message_is_sent_back(self):
        code, err, _ = self.stop("I finished the login. Shall I also do the logout?")
        self.assertEqual(code, 2)
        self.assertIn("terminal state", err)
        self.assertIn("stamp", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_stale_stamp_is_sent_back(self):
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp(-21)}")
        self.assertEqual(code, 2)
        self.assertIn("minutes off", err)

    def test_open_point_blocks_delivered_but_not_blocked(self):
        self.docket.write_text("# Docket\nflip `[ ]` to `[x]`\n| 1 | login | `cargo test` | [ ] |\n", "utf-8")
        self.assertEqual(self.stop(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp(counts='0/1')}")[0], 2)
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nDocker is not running.\nYou must start Docker.\n{stamp(counts='0/1')}")[0], 0)
        self.assertEqual(self.state()["terminal"], "blocked")

    def test_blocked_without_docket_closes(self):
        self.docket.unlink()
        code, err, _ = self.stop(f"DOCKET BLOCKED {SPEC}\nWorking tree not clean.\nYou must commit or stash it.\n{stamp()}")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["terminal"], "blocked")

    def test_background_agent_running_is_not_a_terminal_moment(self):
        code, _, _ = run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": f"waiting for the fixer\n{hstamp()}",
                                          "stop_hook_active": False, "background_tasks": [{"id": "a1"}]}, self.p)
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_second_pass_is_let_through_and_logged(self):
        code, _, _ = self.stop("still not terminal", active=True)
        self.assertEqual(code, 0)
        self.assertIn("stop-forced", self.log())


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

    def test_background_wait_needs_a_stamp_too(self):
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": "waiting for the fixer",
                                            "stop_hook_active": False, "background_tasks": [{"id": "a1"}]}, self.p)
        self.assertEqual(code, 2)
        self.assertIn("stamp", err)
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


class DisplayGate(Project):
    """message_display.py holds a broken message back before the user sees it; stop.py asks for it again."""

    HIDDEN = "​"

    def display(self, delta, final=True, message_id="m1", index=0):
        code, err, out = run_hook("message_display.py", {"session_id": SID, "cwd": self.p, "hook_event_name": "MessageDisplay",
                                                         "turn_id": "t1", "message_id": message_id, "index": index,
                                                         "final": final, "delta": delta}, self.p)
        self.assertEqual(code, 0, err)
        return json.loads(out)["hookSpecificOutput"]["displayContent"] if out.strip() else None

    def record(self):
        path = self.root / ".claude" / "docket" / f"display-{SID}.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else {}

    def test_clean_message_is_shown_whole(self):
        self.set_state(mode="concept")
        text = f"The default stays at 512 (config_reference.md).\n{hstamp()}"
        self.assertEqual(self.display(text), text)
        self.assertEqual(self.stop(text)[0], 0)

    def test_broken_message_is_held_back_and_written_again_once(self):
        self.set_state(mode="init")
        broken = f"It probably works.\n{hstamp()}"
        self.assertEqual(self.display(broken), self.HIDDEN)
        code, err, _ = self.stop(broken)
        self.assertEqual(code, 2)
        self.assertIn("held back and never shown", err)
        self.assertIn("in full", err)
        self.assertNotIn("already read", err)
        self.assertTrue(self.record()["retry"])
        # The answer to the refusal is shown even when it still breaks a rule, and the second pass lets it through.
        self.assertIsNone(self.display(f"It probably works, again.\n{hstamp()}", message_id="m2"))
        self.assertEqual(self.stop(f"It probably works, again.\n{hstamp()}", active=True)[0], 0)
        self.assertFalse(self.record()["retry"])
        self.assertEqual(self.record()["hidden"], [])

    def test_held_back_message_is_asked_for_even_on_a_second_pass(self):
        # A second pass (stop_hook_active) changes nothing: the user still has seen nothing.
        self.set_state(mode="concept")
        broken = f"Fixed in a1b2c3d4e.\n{hstamp()}"
        self.assertEqual(self.display(broken), self.HIDDEN)
        code, err, _ = self.stop(broken, active=True)
        self.assertEqual(code, 2)
        self.assertIn("held back", err)

    def test_message_without_stamp_is_shown_and_corrected_by_lines(self):
        # Text between tool calls has no stamp and no Stop sees it: it is never held back.
        self.set_state(mode="concept")
        broken = "It probably works."
        self.assertEqual(self.display(broken), broken)
        code, err, _ = self.stop(broken)
        self.assertEqual(code, 2)
        self.assertIn("already read", err)
        self.assertIn("[docket] Korrektur:", err)
        self.assertNotIn("Write it again", err)
        self.assertIsNone(self.display(f"[docket] Korrektur: It works (tests 3 passed).\n{hstamp()}", message_id="m2"))

    def test_shown_message_with_a_broken_rule_gets_correction_lines(self):
        # Without the gate (a client that ignores MessageDisplay) the message is on the screen.
        self.set_state(mode="spec")
        code, err, _ = self.stop(f"Keep the default? Or change it?\n{hstamp()}")
        self.assertEqual(code, 2)
        self.assertIn("do not repeat or rewrite it", err)
        self.assertNotIn("held back", err)

    def test_missing_stamp_alone_never_holds_back(self):
        self.set_state(mode="concept")
        text = f"The default stays at 512 (config_reference.md).\n{hstamp(-40)}"
        self.assertEqual(self.display(text), text)
        code, err, _ = self.stop(text)
        self.assertIn("stamp line alone", err)
        self.assertTrue(self.record()["retry"])

    def test_batches_are_held_until_the_last_one(self):
        self.set_state(mode="concept")
        self.assertEqual(self.display("The default stays at 512\n", final=False), self.HIDDEN)
        self.assertIn("m1", self.record()["pending"])
        last = f"(config_reference.md).\n{hstamp()}"
        self.assertEqual(self.display(last, index=1), "The default stays at 512\n" + last)
        self.assertEqual(self.record()["pending"], {})

    def test_mode_none_is_not_gated(self):
        self.assertIsNone(self.display(f"It probably works.\n{hstamp()}"))

    def test_project_without_docket_md_is_not_gated(self):
        (self.work / "DOCKET.md").unlink()
        self.set_state(mode="concept")
        self.assertIsNone(self.display(f"It probably works.\n{hstamp()}"))

    def test_a_new_prompt_settles_the_record(self):
        self.set_state(mode="concept")
        self.display(f"It probably works.\n{hstamp()}")
        self.assertEqual(len(self.record()["hidden"]), 1)
        self.prompt("how are the tests?")
        self.assertEqual(self.record(), {"hidden": [], "pending": {}, "retry": False})

    def test_broken_input_fails_open(self):
        code, _, out = run_hook("message_display.py", {"session_id": SID, "cwd": self.p, "message_id": "../x"}, self.p)
        self.assertEqual((code, out.strip()), (0, ""))


class Rewrite(Project):
    def test_conceptor_spawn_opens_rewrite_and_return_closes_it(self):
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = self.tool("Agent", subagent_type="docket:conceptor", prompt="rewrite section 3")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "rewrite")
        self.assertEqual(self.state()["rewrite"], SPEC)
        self.assertIn("rewrite", self.log())
        self.assertEqual(self.tool("Edit", file_path=str(self.spec))[0], 0)  # the frozen spec is open now
        self.assertEqual(self.tool("AskUserQuestion", questions=[])[0], 2)
        code, _, _ = run_hook("subagent_stop.py", {"session_id": SID, "cwd": self.p, "stop_hook_active": False,
                                                   "last_assistant_message": "Rewrote section 3.\nUnverified: none"}, self.p)
        self.assertEqual(code, 0)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertIn("rewrite-done", self.log())
        self.assertEqual(self.tool("Edit", file_path=str(self.spec))[0], 2)  # frozen again

    def test_other_agents_do_not_open_rewrite(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.tool("Agent", subagent_type="docket:refiner", prompt="angle")
        self.assertEqual(self.state()["mode"], "sealed")


class PointEvents(Project):
    def post(self, path):
        return run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Edit",
                                             "tool_input": {"file_path": str(path)}}, self.p)

    def test_flips_are_logged(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | a | `x` | [ ] |\n| 2 | b | `y` | [ ] |\n", "utf-8")
        self.tool("Edit", file_path=str(self.docket))
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [~] |\n- deviation: b differs\n", "utf-8")
        self.assertEqual(self.post(self.docket)[0], 0)
        self.assertIn("point-delivered", self.log())
        self.assertIn("deviation", self.log())


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

    def test_angle_brackets_in_goal_prose_are_not_placeholders(self):
        self.set_state(mode="spec")
        text = self.spec_text("`python greet.py World` → `hello, World`", "yes (2026-11-05)", "| README.md | Files |")
        self.spec.write_text(text.replace("Users can log in.", "greet.py prints `hello, <name>` for the given <name>."), "utf-8")
        code, err, _ = self.post(self.spec)
        self.assertEqual(code, 0, err)

    def test_template_goal_demotes(self):
        self.set_state(mode="spec")
        text = self.spec_text("`cargo test` → `3 passed`", "yes (2026-11-05)", "| docs/auth.md | login section |")
        for goal in ("", "One paragraph: what users or operators get, and why now."):
            self.spec.write_text(text.replace("Users can log in.", goal), "utf-8")
            code, err, _ = self.post(self.spec)
            self.assertEqual(code, 2, goal)
            self.assertIn("template text", err)
            self.set_state(mode="spec")

    def test_gaps_demote_to_draft(self):
        self.set_state(mode="spec")
        self.spec.write_text(self.spec_text("works", "", ""), "utf-8")
        code, err, _ = self.post(self.spec)
        self.assertEqual(code, 2)
        for fragment in ("criterion", "user decision open", "Reference update"):
            self.assertIn(fragment, err)
        self.assertIn("state: draft", self.spec.read_text("utf-8"))
        self.assertNotIn(" ready ", self.log())


class SpecState(Project):
    def test_spec_state_follows_the_run(self):
        self.spec.write_text(f"---\nid: {SPEC}\nstate: ready          # draft | ready\n---\n# Login\n", "utf-8")
        self.prompt(f"/implement {SPEC}")
        self.assertIn("state: in-progress          # draft | ready", self.spec.read_text("utf-8"))
        self.docket.write_text("- [x] point 1\n", "utf-8")
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nno docker\nYou must start Docker.\n{stamp()}")[0], 0)
        self.assertIn("state: ready", self.spec.read_text("utf-8"))
        self.set_state(mode="sealed", spec=SPEC)
        self.assertEqual(self.stop(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")[0], 0)
        self.assertIn("state: done", self.spec.read_text("utf-8"))


class HookOwnedFiles(Project):
    def test_state_locks_and_violations_are_hook_owned(self):
        self.set_state(mode="spec")
        state_file = self.root / ".claude" / "docket" / f"state-{SID}.json"
        code, err, _ = self.tool("Write", file_path=str(state_file), content="{}")
        self.assertEqual(code, 2)
        self.assertIn("hooks only", err)
        self.assertEqual(self.tool("Edit", file_path=str(self.work / "locks" / SPEC), old_string="a", new_string="b")[0], 2)
        self.assertEqual(self.tool("Write", file_path=str(self.work / "metrics" / f"{SPEC}.violations.json"), content="{}")[0], 2)
        self.set_state(mode="sealed", spec=SPEC)
        self.assertEqual(self.tool("Write", file_path=str(self.work / "metrics" / f"{SPEC}.json"), content="{}")[0], 0)

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
    def test_second_session_is_refused_while_first_is_sealed(self):
        self.prompt(f"/implement {SPEC}")
        self.assertEqual((self.work / "locks" / SPEC).read_text("utf-8"), SID)
        code, err, _ = run_hook("user_prompt.py", {"session_id": "other-session-2", "cwd": self.p,
                                                   "prompt": f"/implement {SPEC}"}, self.p)
        self.assertEqual(code, 2)
        self.assertIn("sealed in session", err)

    def test_stale_lock_is_taken_over_and_released_on_close(self):
        (self.work / "locks").mkdir()
        (self.work / "locks" / SPEC).write_text("dead-session", "utf-8")
        code, _, _ = self.prompt(f"/implement {SPEC}")
        self.assertEqual(code, 0)
        self.docket.write_text("- [x] point 1\n", "utf-8")
        self.stop(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
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
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": False, "transcript_path": str(self.transcript)}, self.p)

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

    def test_cited_location_must_exist(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}})])
        (self.root / "src").mkdir()
        (self.root / "src" / "auth.rs").write_text("a\nb\nc\n", "utf-8")
        self.docket.write_text("- [x] point 1: `cargo test` → 3 passed, see src/auth.rs:2\n", "utf-8")
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")[0], 0)
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("- [x] point 1: see src/auth.rs:40 and src/gone.rs:1\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("src/gone.rs:1", err)
        self.assertIn("src/auth.rs:40", err)

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

    def test_stale_stamp_sets_degraded(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}})])
        self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp(-30)}")
        self.assertTrue(self.state()["degraded"])


class StampCounts(Project):
    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [~] |\n| 3 | c | `z` | [x] |\n", "utf-8")

    def test_counts_must_match_the_docket(self):
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp(counts='3/3')}")
        self.assertEqual(code, 2)
        self.assertIn("2/3", err)
        self.assertEqual(self.stop(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp(counts='2/3')}")[0], 0)

    def test_delivered_without_counts_is_refused_blocked_passes(self):
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nok\nNothing to do.\n{stamp(counts='')}")
        self.assertEqual(code, 2)
        self.assertIn("2/3", err)
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nno docker\nYou must start Docker.\n{stamp(counts='')}")[0], 0)


class Refinement(Project):
    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)

    def implementer(self):
        return self.tool("Agent", subagent_type="docket:implementer", prompt="point 1")

    def test_no_docket_refuses_first_implementer(self):
        code, err, _ = self.implementer()
        self.assertEqual(code, 2)
        self.assertIn("skeleton", err)

    def test_open_refinement_line_refuses_closed_passes(self):
        self.docket.write_text("## Refinement\n\n- feasibility: hello.py does not exist\n- spec: fine → rejected: out of scope\n\n## Evidence\n", "utf-8")
        code, err, _ = self.implementer()
        self.assertEqual(code, 2)
        self.assertIn("hello.py does not exist", err)
        self.docket.write_text("## Refinement\n\n- feasibility: hello.py does not exist → REWRITE by the conceptor\n- spec: fine → rejected\n", "utf-8")
        self.assertEqual(self.implementer()[0], 0)

    def test_fixer_and_refiner_are_not_gated(self):
        self.assertEqual(self.tool("Agent", subagent_type="docket:fixer", prompt="skeleton")[0], 0)
        self.assertEqual(self.tool("Agent", subagent_type="docket:refiner", prompt="angle")[0], 0)


class ProjectRules(Project):
    def setUp(self):
        super().setUp()
        (self.work / "DOCKET.md").write_text(
            "| Key | Value | Source |\n|---|---|---|\n| push_branch | `feature/.*` | |\n"
            "| commit_regex | `(feat\|fix\|docs)(\\(.*\\))?: ` | |\n| forbidden_paths | `secrets/**, .env` | |\n"
            "| forbidden_commands | `gh workflow run\|terraform apply` | |\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC)

    def test_push_to_other_branch_refused(self):
        code, err, _ = self.tool("Bash", command="git push origin main")
        self.assertEqual(code, 2)
        self.assertIn("push_branch", err)
        self.assertEqual(self.tool("Bash", command="git push -u origin feature/login")[0], 0)
        self.assertEqual(self.tool("Bash", command="git push origin HEAD:feature/login")[0], 0)

    def test_commit_message_must_match(self):
        code, err, _ = self.tool("Bash", command='git commit -m "login works"')
        self.assertEqual(code, 2)
        self.assertIn("commit_regex", err)
        self.assertEqual(self.tool("Bash", command='git commit -m "feat(auth): login"')[0], 0)
        self.assertEqual(self.tool("Bash", command="git commit -m \"$(cat <<'EOF'\nfix: typo\n\nbody\nEOF\n)\"")[0], 0)
        self.assertEqual(self.tool("Bash", command="git commit -m \"$(cat <<'EOF'\nwrong\nEOF\n)\"")[0], 2)

    def test_forbidden_paths_and_commands(self):
        code, err, _ = self.tool("Write", file_path=f"{self.p}/secrets/key.pem", content="x")
        self.assertEqual(code, 2)
        self.assertIn("forbidden path", err)
        self.assertEqual(self.tool("Edit", file_path=f"{self.p}/.env", old_string="a", new_string="b")[0], 2)
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/src/main.rs", content="x")[0], 0)
        code, err, _ = self.tool("Bash", command="gh workflow run deploy.yml")
        self.assertEqual(code, 2)
        self.assertIn("forbidden_commands", err)
        self.assertEqual(self.tool("Bash", command="gh pr list")[0], 0)

    def test_rules_inactive_without_config(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n", "utf-8")
        self.assertEqual(self.tool("Bash", command="git push origin main")[0], 0)
        self.assertEqual(self.tool("Bash", command='git commit -m "anything"')[0], 0)


class InitApproval(Project):
    def test_init_writes_only_after_go(self):
        self.prompt("/docket:init")
        self.assertEqual(self.state()["mode"], "init")
        code, err, _ = self.tool("Write", file_path=str(self.work / "DOCKET.md"), content="# DOCKET")
        self.assertEqual(code, 2)
        self.assertIn("approved", err)
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/CONTRIBUTING.md", content="x")[0], 2)
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/notes.md", content="x")[0], 0)
        self.prompt("go")
        self.assertTrue(self.state()["init_approved"])
        self.assertIn("init-approved", self.log())
        self.assertEqual(self.tool("Write", file_path=str(self.work / "DOCKET.md"), content="# DOCKET")[0], 0)
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/CONTRIBUTING.md", content="x")[0], 0)

    def test_other_text_is_not_a_go(self):
        self.prompt("/docket:init")
        self.prompt("go on with the audit, I will decide later")
        self.assertFalse(self.state().get("init_approved"))


class Violations(Project):
    def test_refusals_are_counted_and_fed_back(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.tool("Write", file_path=str(self.work / "LOG.md"), content="x")
        self.tool("Write", file_path=str(self.work / "LOG.md"), content="x")
        self.tool("AskUserQuestion", questions=[])
        counts = json.loads((self.work / "metrics" / f"{SPEC}.violations.json").read_text("utf-8"))
        self.assertEqual(sum(counts.values()), 3)
        self.assertEqual(max(counts.values()), 2)
        self.set_state(mode="none")
        code, err, out = self.prompt(f"/implement {SPEC}")
        self.assertEqual(code, 0, err)
        self.assertIn("Refusals so far", out)
        self.assertIn("(2)", out)
        _, _, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p)
        self.assertIn("Refusals so far", out)

    def test_no_file_without_refusals(self):
        self.set_state(mode="sealed", spec=SPEC)
        self.tool("Read", file_path=f"{self.p}/README.md")
        self.assertFalse((self.work / "metrics").exists())


class JudgeLog(Project):
    """The judge runs at Stop, after the user read the message: its rejection goes to Claude, which answers
    `[docket] noted`; the gate hides that answer, stop.py lets it through and counts the verdict."""

    PROMPT = "[You judge the final message of an assistant before a human reads it. Hook input JSON: $ARGUMENTS ...]"
    TAIL = " The user has read your message and will not see your answer to this; answer with exactly the line `[docket] noted`."
    HIDDEN = "​"

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

    def display(self, delta):
        code, err, out = run_hook("message_display.py", {"session_id": SID, "cwd": self.p, "message_id": "m9",
                                                         "index": 0, "final": True, "delta": delta,
                                                         "transcript_path": str(self.path)}, self.p)
        self.assertEqual(code, 0, err)
        return json.loads(out)["hookSpecificOutput"]["displayContent"] if out.strip() else None

    def stop_with(self, message, active=False):
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": active, "transcript_path": str(self.path)}, self.p)

    def test_acknowledgement_is_hidden_passed_and_the_verdict_counted_once(self):
        self.set_state(mode="concept")
        reason = ('no-source "The transfer set is now the five" | promise "I will report the result when it" | '
                  'no-source "The check is still running"')
        self.add({"type": "user", "timestamp": "2026-11-05T21:57:00Z", "message": {"role": "user", "content": "go on"}},
                 self.said(f"The transfer set is now the five specs.\n{hstamp()}"),
                 self.feedback(reason), self.summary("2026-11-05T21:58:53Z", reason))
        self.assertEqual(self.display("[docket] noted"), self.HIDDEN)
        code, err, _ = self.stop_with("[docket] noted", active=True)
        self.assertEqual(code, 0, err)
        counts = json.loads((self.work / "metrics" / "session.violations.json").read_text("utf-8"))
        self.assertEqual(counts, {"judge-no-source": 2, "judge-promise": 1})
        self.assertEqual(self.log().count(" judge "), 1)
        self.assertNotIn("noted", self.log().split(" judge ")[1])  # the request to acknowledge is not logged
        self.assertNotIn("stop-forced", self.log())
        # Stops after it read only what is new.
        self.stop_with(f"Next answer.\n{hstamp()}")
        self.assertEqual(self.log().count(" judge "), 1)

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

    def test_acknowledgement_is_hidden_in_every_mode_of_a_set_up_project(self):
        self.set_state(mode="none")
        self.add(self.feedback('self "Sorry for the delay"'))
        self.assertEqual(self.display("[docket] noted"), self.HIDDEN)
        self.assertEqual(self.stop_with("[docket] noted", active=True)[0], 0)
        (self.work / "DOCKET.md").unlink()
        self.assertIsNone(self.display("[docket] noted"))

    def test_answer_to_a_held_back_message_is_shown(self):
        self.set_state(mode="concept")
        self.add(self.feedback('promise "I will"', extra="[docket] Your last message was held back and never shown "
                                                         "to the user: no hedge words. Write it again in full.\n"))
        message = f"It works.\n{hstamp()}"
        self.assertEqual(self.display(message), message)

    def test_messages_after_a_user_turn_or_a_tool_call_are_judged_as_usual(self):
        self.set_state(mode="concept")
        self.add(self.feedback('promise "I will"'),
                 {"type": "user", "message": {"role": "user", "content": "und jetzt?"}})
        message = f"It works.\n{hstamp()}"
        self.assertEqual(self.display(message), message)
        self.add(self.feedback('promise "I will"'),
                 {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}})
        self.assertEqual(self.display("[docket] noted"), self.HIDDEN)

    def test_acknowledgement_does_not_end_a_sealed_run(self):
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = self.stop_with("[docket] noted")
        self.assertEqual(code, 2)
        self.assertIn("terminal", err)

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
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": False, "transcript_path": str(self.transcript)}, self.p)

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
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nstopped by user\nNothing to do.\n{stamp(counts='1/3')}")[0], 0)
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

    def test_forbidden_paths_stay_refused(self):
        (self.work / "DOCKET.md").write_text("| forbidden_paths | `secrets/**` | |\n", "utf-8")
        self.set_state(mode="spec")
        self.assertEqual(self.tool("Write", file_path=f"{self.p}/secrets/key.pem", content="x")[0], 2)


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

    def test_concept_mode_writes_outside_the_work_dir_only_with_the_users_click(self):
        self.set_state(mode="concept")
        for target in (f"{self.p}/legacy/specs/022_testserver.md", f"{self.p}/AGENTS.md", f"{self.p}/.claude/CLAUDE.md"):
            code, err, out = self.tool("Edit", file_path=target, old_string="a", new_string="b")
            self.assertEqual(code, 0, err)
            decision = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "ask", target)
            self.assertIn("Concept mode writes concepts", decision["permissionDecisionReason"])
        self.assertIn(" ask ", self.log())
        for target in (str(self.work / "concepts" / "core" / "c-0000ffff.md"), str(self.work / "DOCKET.md"),
                       str(self.tmp / "scratch" / "notes.txt")):
            code, err, out = self.tool("Write", file_path=target, content="x")
            self.assertEqual((code, out.strip()), (0, ""), target)
        self.set_state(mode="spec")
        self.assertEqual(self.tool("Edit", file_path=f"{self.p}/AGENTS.md", old_string="a", new_string="b")[2].strip(), "")
        self.set_state(mode="concept")
        run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read",
                                      "tool_input": {"file_path": f"{self.p}/AGENTS.md"}}, self.p)
        self.assertNotIn(" grant ", self.log())  # a Read is no write
        run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Edit",
                                      "tool_input": {"file_path": f"{self.p}/AGENTS.md"}}, self.p)
        self.assertIn(" grant ", self.log())
        self.assertIn("write AGENTS.md", self.log())
        (self.work / "DOCKET.md").unlink()  # a project not set up: docket only observes
        code, _, out = self.tool("Edit", file_path=f"{self.p}/src/a.py", old_string="a", new_string="b")
        self.assertEqual((code, out.strip()), (0, ""))

    def test_a_named_blocker_ends_the_turn_until_the_user_answers(self):
        self.set_state(mode="concept")
        self.add(self.user("überarbeite die specs"),
                 self.said(f"- **Blocker:** docket specs only through /docket:spec.\n{hstamp()}"), self.stopped(),
                 self.user("<task-notification>workflow completed</task-notification>"))
        for name, tool_input in (("Write", {"file_path": str(self.work / "concepts" / "core" / "c-1.md"), "content": "x"}),
                                 ("Workflow", {"script": "export const meta = {}"}), ("Agent", {"prompt": "draft"})):
            code, err, _ = self.tool_t(name, **tool_input)
            self.assertEqual(code, 2, name)
            self.assertIn("blocker", err)
        self.assertEqual(self.tool_t("Read", file_path=str(self.work / "DOCKET.md"))[0], 0)
        self.add(self.user("ok, dann /docket:spec"))
        self.assertEqual(self.tool_t("Write", file_path=str(self.work / "concepts" / "core" / "c-1.md"), content="x")[0], 0)

    def test_a_skill_text_or_a_compaction_is_no_answer_and_no_blocker_is_no_blocker(self):
        self.set_state(mode="concept")
        concept = str(self.work / "concepts" / "core" / "c-1.md")
        self.add(self.user("weiter"), self.said(f"1. Blocker: the key is missing.\n{hstamp()}"), self.stopped(),
                 {"type": "user", "isMeta": True, "message": {"role": "user", "content": "# Simplify\nReview the code"}},
                 {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "Summary of the session"}})
        self.assertEqual(self.tool_t("Write", file_path=concept, content="x")[0], 2)
        self.add(self.user("ok"), self.said(f"Blocker: keiner.\n{hstamp()}"), self.stopped())
        self.assertEqual(self.tool_t("Write", file_path=concept, content="x")[0], 0)

    def test_a_blocker_in_a_text_between_tool_calls_does_not_end_the_turn(self):
        self.set_state(mode="spec")
        self.add(self.user("schreib die spec"), self.said("Blocker for the live check: no key yet; writing the rest."))
        self.assertEqual(self.tool_t("Write", file_path=str(self.work / "specs" / "core" / "s-0000ffff-x.md"),
                                     content="---\nstate: draft\n---\n")[0], 0)

    def test_no_question_in_the_box_of_a_sealed_or_closed_session(self):
        for mode in ("sealed", "closed"):
            self.set_state(mode=mode, spec=SPEC)
            code, err, _ = self.tool("mcp__ccd_turn__wrap_up", recap="x", ask="Committen?")
            self.assertEqual(code, 2, mode)
            self.assertIn("next-step box", err)
            self.assertEqual(self.tool("mcp__ccd_turn__wrap_up", recap="x")[0], 0, mode)
        self.set_state(mode="concept")
        self.assertEqual(self.tool("mcp__ccd_turn__wrap_up", recap="x", ask="Committen?")[0], 0)

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
        message = (f"- Warum ein Konzept: Der Hook hat geantwortet: „Specs are written in spec mode only“ "
                   f"(pre_tool_use.py).\n{hstamp()}")
        self.add(self.user("was ist das für ein Konzept?"))
        code, err, _ = self.stop_t(message)
        self.assertEqual(code, 2)
        self.assertIn("no hook said it", err)
        self.add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t9", "is_error": True,
             "content": "PreToolUse:Write hook error: [docket] Specs are written in spec mode only: name `/docket:spec`"}]}})
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

    def test_a_rewrite_the_gate_asked_for_is_shown_when_the_judge_feedback_is_a_separate_entry(self):
        self.set_state(mode="concept")
        judge = JudgeLog.PROMPT + ': no-source "Alle fünf Specs tragen"' + JudgeLog.TAIL
        self.add(self.said(f"Alle fünf Specs tragen.\n{hstamp()}"),
                 self.user("Stop hook feedback:\n[docket] Your last message was held back and never shown to the user: "
                           "no spec or concept ids. Write it again in full."),
                 self.user("Stop hook feedback:\n" + judge))
        message = f"Alle fünf Specs tragen (Konzept, Abschnitt Korrekturen).\n{hstamp()}"
        code, err, out = run_hook("message_display.py", {"session_id": SID, "cwd": self.p, "message_id": "m7", "index": 0,
                                                         "final": True, "delta": message,
                                                         "transcript_path": str(self.path)}, self.p)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["displayContent"], message)


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


class Clock(Project):
    """M7: the hooks hand the time to the model with every prompt and tool result; the stamp needs no tool call
    (25 of 174 calls fetched the time on 2026-11-05)."""

    def post(self):
        return run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": "Read",
                                             "tool_input": {"file_path": f"{self.p}/README.md"}}, self.p)

    def test_prompt_and_tool_results_carry_the_clock(self):
        self.set_state(mode="concept")
        _, _, out = self.prompt("how are the tests?")
        clock = self.state()["clock"]
        self.assertRegex(clock, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertIn(f"[docket] clock: {clock}", out)
        code, err, out = self.post()
        self.assertEqual(code, 0, err)
        self.assertIn("[docket] clock: ", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PostToolUse")

    def test_stamp_equal_to_the_given_clock_passes_after_a_long_turn(self):
        when = datetime.now() - timedelta(minutes=40)
        self.set_state(mode="concept", clock=f"{when:%Y-%m-%d %H:%M}")
        code, err, _ = self.stop(f"The default stays at 512 (config_reference.md).\nstamp: {when:%Y-%m-%d %H:%M}")
        self.assertEqual(code, 0, err)
        code, err, _ = self.stop(f"The default stays at 512 (config_reference.md).\n{hstamp(-30)}")
        self.assertEqual(code, 2)
        self.assertIn("minutes off", err)
        self.assertIn("clock", err)

    def test_no_clock_without_docket_md(self):
        (self.work / "DOCKET.md").unlink()
        code, _, out = self.post()
        self.assertEqual((code, out.strip()), (0, ""))


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



class SealedGate(Project):
    """0.2.9: a sealed run speaks to the user once. luradb-web-client 2026-11-06: 25 status lines in the plugin's
    words and three DOCKET DELIVERED reached the user, who asked half an hour later whether the run was done."""

    HIDDEN = DisplayGate.HIDDEN
    display = DisplayGate.display
    record = DisplayGate.record

    def setUp(self):
        super().setUp()
        self.spec.write_text(f"---\nid: {SPEC}\ntitle: Login per Token\nstate: in-progress\n---\n# Login per Token\n", "utf-8")
        self.set_state(mode="sealed", spec=SPEC, clock="2026-11-06 10:00")
        self.docket.write_text("| 1 | login | `cargo test` | [x] |\n| 2 | logout | `cargo test` | [~] |\n", "utf-8")

    def german_config(self):
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| user_language | `Deutsch` | |\n", "utf-8")

    def terminal(self, body="Login per Token: 1 of 2 points delivered, merged to next, run finished.\nNothing to do.",
                 counts="1/2"):
        return f"DOCKET DELIVERED {SPEC}\n{body}\nThe token login works, 3 tests green.\n{stamp(counts=counts)}"

    def test_a_status_message_is_replaced_by_the_hooks_status_line(self):
        shown = self.display("Three refiners running. Meanwhile checking loadEnv myself.")
        self.assertIn("[docket] Login per Token: 1 of 2 points delivered, 1 deviating, 0 open", shown)
        self.assertIn("2026-11-06 10:00", shown)
        self.assertNotIn("refiners", shown)
        self.assertEqual(self.record()["hidden"], [])  # not held back: the Stop hook asks for no rewrite
        # Also after a refusal (retry): the orchestrator's answer to the Stop hook is not for the user either.
        (self.root / ".claude" / "docket" / f"display-{SID}.json").write_text(
            json.dumps({"hidden": [], "pending": {}, "retry": True}), "utf-8")
        self.assertIn("[docket] Login per Token", self.display(f"Fixer running.\n{hstamp()}", message_id="m2"))

    def test_status_line_in_german_before_the_docket_exists(self):
        self.docket.unlink()
        self.german_config()
        shown = self.display("Reading the required files first.")
        self.assertEqual(shown, "[docket] Login per Token: Vorbereitung: Spec wird gelesen und gegen den Code geprüft"
                                " · Lauf läuft · 2026-11-06 10:00")

    def test_a_terminal_message_the_stop_hook_would_refuse_is_held_back(self):
        broken = f"DOCKET DELIVERED {SPEC}\nPoint 1: the fixer flipped the docket row.\n{stamp(counts='1/2')}"
        self.assertEqual(self.display(broken), self.HIDDEN)
        held = self.record()["hidden"][0]["problems"]
        self.assertTrue(any("title `Login per Token`" in p for p in held), held)
        self.assertTrue(any("Du musst" in p for p in held), held)
        code, err, _ = self.stop(broken)
        self.assertEqual(code, 2)
        self.assertIn("held back and never shown", err)
        self.assertEqual(self.state()["mode"], "sealed")

    def test_a_clean_terminal_message_is_shown_and_the_hook_adds_its_closing_line(self):
        text = self.terminal()
        self.assertEqual(self.display(text), text)
        (self.work / "specs" / "core" / "s-aaaa1111-logout.md").write_text(
            "---\nid: s-aaaa1111\ntitle: Logout\nstate: ready\n---\n", "utf-8")
        code, err, out = self.stop(text)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "closed")
        closing = json.loads(out)["systemMessage"]
        self.assertEqual(closing, "[docket] Login per Token: delivered 1/2, run finished, session closed. "
                                  "next spec in state ready: s-aaaa1111 (Logout), in a new session with /docket:implement s-aaaa1111.")

    def test_closing_line_in_german_names_no_next_spec_when_none_is_ready(self):
        self.german_config()
        text = (f"DOCKET BLOCKED {SPEC}\nLogin per Token: 1 von 2 Punkten geliefert, Lauf abgebrochen.\n"
                f"Du musst Docker starten, dann den Lauf erneut beginnen.\n{stamp(counts='1/2')}")
        code, err, out = self.stop(text)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["systemMessage"],
                         f"[docket] Login per Token: blockiert bei 1/2, Lauf beendet; /docket:implement {SPEC} nimmt ihn wieder auf. "
                         "Keine weitere Spec im Zustand ready.")

    def test_terminal_message_about_hooks_or_in_the_wrong_language_is_refused(self):
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nLogin per Token: done.\nNothing to do.\n"
                                 f"Note: the append-only hook objected to those edits.\n{stamp(counts='1/2')}")
        self.assertEqual(code, 2)
        self.assertIn("nothing about hooks", err)
        self.german_config()
        self.set_state(mode="sealed", spec=SPEC)
        code, err, _ = self.stop(self.terminal())
        self.assertEqual(code, 2)
        self.assertIn("not in Deutsch", err)
        self.set_state(mode="sealed", spec=SPEC)
        german = (f"DOCKET DELIVERED {SPEC}\nLogin per Token: 1 von 2 Punkten geliefert, auf next gemerged, Lauf beendet.\n"
                  f"Nichts zu tun.\nDer Login mit Token funktioniert, drei Tests sind grün (`cargo test` → 3 passed).\n"
                  f"{stamp(counts='1/2')}")
        code, err, _ = self.stop(german)
        self.assertEqual(code, 0, err)


class Language(Project):
    """0.2.9: DOCKET.md `user_language` is the language of every message to the user; the gate holds another back."""

    def setUp(self):
        super().setUp()
        (self.work / "DOCKET.md").write_text("# DOCKET\n| Key | Value | Source |\n|---|---|---|\n| user_language | `Deutsch` | |\n", "utf-8")

    def display(self, delta, message_id="m1"):
        code, err, out = run_hook("message_display.py", {"session_id": SID, "cwd": self.p, "message_id": message_id,
                                                         "index": 0, "final": True, "delta": delta}, self.p)
        self.assertEqual(code, 0, err)
        return json.loads(out)["hookSpecificOutput"]["displayContent"] if out.strip() else None

    def test_an_english_message_in_concept_mode_is_held_back_and_a_german_one_shown(self):
        self.set_state(mode="concept")
        english = f"The default stays at 512 and the cache is not touched by this change (config_reference.md).\n{hstamp()}"
        self.assertEqual(self.display(english), DisplayGate.HIDDEN)
        code, err, _ = self.stop(english)
        self.assertEqual(code, 2)
        self.assertIn("not in Deutsch", err)
        german = f"Der Standard bleibt bei 512 und der Cache wird von dieser Änderung nicht berührt (config_reference.md).\n{hstamp()}"
        self.prompt("weiter")
        self.assertEqual(self.display(german, message_id="m2"), german)
        self.assertEqual(self.stop(german)[0], 0)

    def test_code_and_short_texts_are_not_judged_and_the_prompt_names_the_language(self):
        self.set_state(mode="concept")
        code_heavy = f"Der Befehl ist `npm run check && npm test --reporter dot` (package.json).\n{hstamp()}"
        self.assertEqual(self.stop(code_heavy)[0], 0)
        self.assertEqual(self.stop(f"Done.\n{hstamp()}")[0], 0)
        _, _, out = self.prompt("/docket:concept")
        self.assertIn("written in Deutsch", out)
        (self.work / "DOCKET.md").write_text("# DOCKET\n", "utf-8")
        _, _, out = self.prompt("/docket:concept")
        self.assertNotIn("written in", out)


class CitationsAtWrite(Project):
    """0.2.9: a docket line that cites a `file:line` which does not exist undoes the write (luradb-web-client
    2026-11-06: refinement lines cited paths relative to concept/, DELIVERED refused them, the correction broke
    append-only; three DELIVERED attempts)."""

    def post(self, path, tool="Edit"):
        return run_hook("post_tool_use.py", {"session_id": SID, "cwd": self.p, "tool_name": tool,
                                             "tool_input": {"file_path": str(path)}}, self.p)

    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)
        (self.root / "src").mkdir()
        (self.root / "src" / "auth.rs").write_text("a\nb\nc\n", "utf-8")

    def test_a_new_line_with_a_missing_location_undoes_the_edit(self):
        before = "# Docket\n## Refinement\n- feasibility: ok, see src/auth.rs:2\n"
        self.docket.write_text(before, "utf-8")
        self.tool("Edit", file_path=str(self.docket))  # snapshot
        self.docket.write_text(before + "- concept: AGENTS.md:22 says so\n", "utf-8")
        code, err, _ = self.post(self.docket)
        self.assertEqual(code, 2)
        self.assertIn("undone", err)
        self.assertIn("AGENTS.md:22", err)
        self.assertIn("relative to the repo root", err)
        self.assertEqual(self.docket.read_text("utf-8"), before)
        self.assertIn("docket-undone", self.log())
        self.tool("Edit", file_path=str(self.docket))
        self.docket.write_text(before + "- concept: src/auth.rs:3 says so\n", "utf-8")
        self.assertEqual(self.post(self.docket)[0], 0)

    def test_a_skeleton_with_a_missing_location_is_removed(self):
        self.docket.write_text("# Docket\n- feasibility: see src/gone.rs:1\n", "utf-8")
        code, err, _ = self.post(self.docket, tool="Write")
        self.assertEqual(code, 2)
        self.assertFalse(self.docket.exists())

    def test_delivered_checks_the_evidence_sections_only(self):
        # A refinement line written before 0.2.9 may cite a location that is gone: it never forces an in-place edit.
        self.docket.write_text("## Refinement\n- concept: see docs/old.md:3\n## Evidence\n- point 1: `cargo test` → ok, src/auth.rs:2\n"
                               "| 1 | login | x | [x] |\n", "utf-8")
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nLogin works.\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 0, err)
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("## Refinement\n- fine\n## Evidence\n- point 1: see src/auth.rs:9\n| 1 | login | x | [x] |\n", "utf-8")
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nLogin works.\nNothing to do.\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("src/auth.rs:9", err)


class Series(Project):
    """0.2.9: several specs are a series in one session, each with its own docket and terminal message, as the
    author's orchestrate-execution runs them. luradb-web-client 2026-11-06: five ids, the first sealed, four lost."""

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

    def stop_at(self, message, active=False):
        return run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": message,
                                    "stop_hook_active": active}, self.p)

    def terminal(self, spec_id, kind="DELIVERED", title="Login"):
        when = datetime.now()
        return (f"DOCKET {kind} {spec_id}\n{title}: delivered, run finished.\nNothing to do.\n"
                f"stamp: {when:%Y-%m-%d %H:%M} {spec_id} 1/1")

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

    def test_a_successor_before_its_undone_predecessor_is_refused(self):
        code, err, _ = self.prompt("/docket:implement s-aaaa1111")
        self.assertEqual(code, 2)
        self.assertIn(f"s-aaaa1111 follows {SPEC}, which is not done", err)
        self.assertEqual(self.prompt(f"/docket:implement s-aaaa1111, {SPEC}")[0], 2)
        self.assertEqual(self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")[0], 0)

    def test_words_without_an_id_are_refused_with_the_forms(self):
        code, err, _ = self.prompt("/docket:implement the login one")
        self.assertEqual(code, 2)
        self.assertIn("--all", err)

    def test_delivered_seals_the_next_spec_in_this_session_and_blocks_the_stop(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111, s-bbbb2222")
        self.docket.write_text("| 1 | login | x | [x] |\n", "utf-8")
        code, err, out = self.stop_at(self.terminal(SPEC))
        self.assertEqual(code, 0, err)
        answer = json.loads(out)
        self.assertEqual(answer["decision"], "block")
        self.assertIn("sealed on s-aaaa1111", answer["reason"])
        self.assertIn("queued after it: s-bbbb2222", answer["reason"])
        self.assertIn("step 0", answer["reason"])
        self.assertEqual(answer["systemMessage"], "[docket] Login: delivered 1/1, run finished, session closed. "
                                                  "continuing in this session with s-aaaa1111 (Logout); after it s-bbbb2222.")
        state = self.state()
        self.assertEqual((state["mode"], state["spec"], state["queue"]), ("sealed", "s-aaaa1111", ["s-bbbb2222"]))
        log = self.log()
        self.assertIn(f"{SPEC}  delivered", log)
        self.assertIn("s-aaaa1111  sealed", log)
        self.assertIn("state: in-progress", (self.work / "specs" / "core" / "s-aaaa1111-logout.md").read_text("utf-8"))
        self.assertIn("state: done", self.spec.read_text("utf-8"))
        # The next spec's first Stop is a first pass although stop_hook_active is true: a broken message is refused.
        code, err, _ = self.stop_at(f"Reading the spec.\n{hstamp()}", active=True)
        self.assertEqual(code, 2)
        self.assertIn("terminal state", err)
        # A second refusal of the same spec is let through as before.
        self.assertEqual(self.stop_at(f"still reading\n{hstamp()}", active=True)[0], 0)

    def test_blocked_stops_the_series_and_names_the_rest(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        code, err, out = self.stop_at(f"DOCKET BLOCKED {SPEC}\nLogin: Docker is down.\nYou must start Docker.\n{stamp(counts='0/1')}")
        self.assertEqual(code, 0, err)
        answer = json.loads(out)
        self.assertNotIn("decision", answer)
        self.assertIn("not started: s-aaaa1111; give a new command", answer["systemMessage"])
        self.assertEqual((self.state()["mode"], self.state()["queue"]), ("closed", []))
        self.assertIn("queue-dropped", self.log())
        self.assertFalse((self.work / "locks" / "s-aaaa1111").exists())

    def test_stop_request_ends_the_series(self):
        self.prompt(f"/docket:implement {SPEC}, s-aaaa1111")
        self.prompt("/docket:stop")
        self.docket.write_text("| 1 | login | x | [x] |\n", "utf-8")
        code, err, out = self.stop_at(self.terminal(SPEC))
        self.assertEqual(code, 0, err)
        self.assertNotIn("decision", json.loads(out))
        self.assertEqual(self.state()["mode"], "closed")

    def test_a_queued_spec_is_locked_against_other_sessions(self):
        self.prompt(f"/docket:implement {SPEC}, s-bbbb2222")
        code, err, _ = run_hook("user_prompt.py", {"session_id": "other-session-0002", "cwd": self.p,
                                                   "prompt": "/docket:implement s-bbbb2222"}, self.p)
        self.assertEqual(code, 2)
        self.assertIn("is sealed in session", err)


if __name__ == "__main__":
    unittest.main()
