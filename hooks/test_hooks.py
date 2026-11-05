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

    def test_without_docket_md_only_observes(self):
        (self.work / "DOCKET.md").unlink()
        code, err, out = run_hook("session_start.py", {"session_id": SID, "cwd": self.p, "source": "startup"}, self.p)
        self.assertEqual(code, 0)
        self.assertIn("/docket init", out)


class ModeSwitch(Project):
    def test_implement_seals_and_logs(self):
        code, err, out = self.prompt(f"/implement {SPEC}")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["mode"], "sealed")
        self.assertEqual(self.state()["spec"], SPEC)
        self.assertIn("sealed", self.log())
        self.assertIn("Mode sealed", out)

    def test_implement_without_id_is_refused(self):
        code, err, _ = self.prompt("/implement")
        self.assertEqual(code, 2)
        self.assertIn("spec id", err)

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
        code, _, _ = self.prompt("/docket stop")
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
        self.assertIn("/spec", err)
        self.assertEqual(self.tool("Write", file_path=str(self.work / "concepts" / "core" / "c-0000ffff.md"), content="x")[0], 0)

    def test_model_may_not_invoke_docket_commands_itself(self):
        self.prompt("/concept a done command")
        code, err, _ = self.tool("Skill", skill="docket:spec")
        self.assertEqual(code, 2)
        self.assertIn("user", err)
        self.assertEqual(self.tool("Skill", skill="docket:concept")[0], 0)  # the one the user typed
        self.assertEqual(self.tool("Skill", skill="anthropic-skills:pdf")[0], 0)
        self.prompt("/docket status")
        self.assertEqual(self.tool("Skill", skill="docket:docket")[0], 0)
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
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nLogin works, 3 tests, CI green.\n{stamp()}")
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
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp(-21)}")
        self.assertEqual(code, 2)
        self.assertIn("minutes off", err)

    def test_open_point_blocks_delivered_but_not_blocked(self):
        self.docket.write_text("# Docket\nflip `[ ]` to `[x]`\n| 1 | login | `cargo test` | [ ] |\n", "utf-8")
        self.assertEqual(self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp(counts='0/1')}")[0], 2)
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nDocker is not running.\n{stamp(counts='0/1')}")[0], 0)
        self.assertEqual(self.state()["terminal"], "blocked")

    def test_blocked_without_docket_closes(self):
        self.docket.unlink()
        code, err, _ = self.stop(f"DOCKET BLOCKED {SPEC}\nWorking tree not clean.\n{stamp()}")
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

    def test_none_mode_is_silent(self):
        self.assertEqual(self.stop("anything goes here? really? a1b2c3d4e")[0], 0)


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
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nno docker\n{stamp()}")[0], 0)
        self.assertIn("state: ready", self.spec.read_text("utf-8"))
        self.set_state(mode="sealed", spec=SPEC)
        self.assertEqual(self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")[0], 0)
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
        self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
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
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
        self.assertEqual(code, 0, err)

    def test_check_before_last_agent_return_is_refused(self):
        self.write_transcript([
            ("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}}),
            ("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"prompt": "x"}}),
            ("user", {"type": "tool_result", "tool_use_id": "a1", "content": "done"}),
        ])
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("before the last agent returned", err)

    def test_check_never_run_is_refused_but_blocked_passes(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}})])
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")[0], 2)
        self.assertEqual(self.stop_t(f"DOCKET BLOCKED {SPEC}\nno docker\n{stamp()}")[0], 0)

    def test_cited_location_must_exist(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}})])
        (self.root / "src").mkdir()
        (self.root / "src" / "auth.rs").write_text("a\nb\nc\n", "utf-8")
        self.docket.write_text("- [x] point 1: `cargo test` → 3 passed, see src/auth.rs:2\n", "utf-8")
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")[0], 0)
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("- [x] point 1: see src/auth.rs:40 and src/gone.rs:1\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("src/gone.rs:1", err)
        self.assertIn("src/auth.rs:40", err)

    def test_reference_update_must_be_accounted_for(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}})])
        self.spec.write_text(f"---\nid: {SPEC}\nstate: ready\n---\n## Reference update\n\n| File | Change |\n|---|---|\n"
                             "| docs/auth.md | login |\n| README.md | none |\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("docs/auth.md", err)
        self.docket.write_text("- [x] point 1\n- updated docs/auth.md\n- README.md: not needed, unchanged\n", "utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "auth.md").write_text("login\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
        self.assertEqual(code, 0, err)

    def test_placeholder_check_is_not_a_check(self):
        (self.work / "DOCKET.md").write_text("| check | `<command that exits 0>` | |\n", "utf-8")
        self.write_transcript([("assistant", {"type": "tool_use", "id": "a1", "name": "Agent", "input": {}})])
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")[0], 0)

    def test_stale_stamp_sets_degraded(self):
        self.write_transcript([("assistant", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cargo test"}})])
        self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp(-30)}")
        self.assertTrue(self.state()["degraded"])


class StampCounts(Project):
    def setUp(self):
        super().setUp()
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("| 1 | a | `x` | [x] |\n| 2 | b | `y` | [~] |\n| 3 | c | `z` | [x] |\n", "utf-8")

    def test_counts_must_match_the_docket(self):
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp(counts='3/3')}")
        self.assertEqual(code, 2)
        self.assertIn("2/3", err)
        self.assertEqual(self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp(counts='2/3')}")[0], 0)

    def test_delivered_without_counts_is_refused_blocked_passes(self):
        code, err, _ = self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp(counts='')}")
        self.assertEqual(code, 2)
        self.assertIn("2/3", err)
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nno docker\n{stamp(counts='')}")[0], 0)


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
        self.prompt("/docket init")
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
        self.prompt("/docket init")
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
        self.assertEqual(self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")[0], 0)
        self.set_state(mode="sealed", spec=SPEC)
        self.docket.write_text("- [x] point 1: ok. CI: green 0123456789abcdef0123456789abcdef01234567\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
        self.assertEqual(code, 2)
        self.assertIn("not reachable", err)
        self.docket.write_text(f"- [x] point 1: ok. CI: green {self.head[:10]}\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}", pushed=False)
        self.assertEqual(code, 2)
        self.assertIn("git push", err)

    def test_configured_ci_needs_a_hash_line(self):
        (self.work / "DOCKET.md").write_text("| Key | Value | Source |\n|---|---|---|\n| ci | `gh run watch` | |\n", "utf-8")
        self.docket.write_text("- [x] point 1: ok. CI: none\n", "utf-8")
        code, err, _ = self.stop_t(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")
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
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nstopped by user\n{stamp(counts='1/3')}")[0], 0)
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


if __name__ == "__main__":
    unittest.main()
