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


def stamp(minutes_off=0):
    when = datetime.now() + timedelta(minutes=minutes_off)
    return f"stamp: {when:%Y-%m-%d %H:%M} {SPEC}"


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
        self.assertEqual(self.stop(f"DOCKET DELIVERED {SPEC}\nok\n{stamp()}")[0], 2)
        self.assertEqual(self.stop(f"DOCKET BLOCKED {SPEC}\nDocker is not running.\n{stamp()}")[0], 0)
        self.assertEqual(self.state()["terminal"], "blocked")

    def test_blocked_without_docket_closes(self):
        self.docket.unlink()
        code, err, _ = self.stop(f"DOCKET BLOCKED {SPEC}\nWorking tree not clean.\n{stamp()}")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state()["terminal"], "blocked")

    def test_background_agent_running_is_not_a_terminal_moment(self):
        code, _, _ = run_hook("stop.py", {"session_id": SID, "cwd": self.p, "last_assistant_message": "waiting for the fixer",
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
        code, err, _ = self.stop(f"Fixed in a1b2c3d4e. It probably works. Option A: keep it. Option B: drop it. See {SPEC}.")
        self.assertEqual(code, 2)
        for fragment in ("hashes", "ids", "hedge", "option"):
            self.assertIn(fragment, err)

    def test_two_questions_rejected(self):
        self.set_state(mode="spec")
        self.assertEqual(self.stop("Keep the default? Or change it?")[0], 2)

    def test_clean_message_passes(self):
        self.set_state(mode="concept")
        code, err, _ = self.stop("The default stays at 512 (config_reference.md). Keep it?")
        self.assertEqual(code, 0, err)

    def test_closed_rejects_proposals(self):
        self.set_state(mode="closed", spec=SPEC)
        self.assertEqual(self.stop("The fix is in. I suggest we also remove the switch.")[0], 2)
        self.assertEqual(self.stop("The fix is in, delivered as written in the docket.")[0], 0)

    def test_none_mode_is_silent(self):
        self.assertEqual(self.stop("anything goes here? really? a1b2c3d4e")[0], 0)


if __name__ == "__main__":
    unittest.main()
