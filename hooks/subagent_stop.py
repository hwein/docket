"""SubagentStop hook: the agent register.

A report to the orchestrator is dense and structured: at most MAX_LINES lines and a line
starting with `Unverified:` (what the agent asserts without a source; `Unverified: none` is fine).
Sent back once.
"""
import posixpath
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from docket_lib import message_text, read_input, refuse

MAX_LINES = 40


def main():
    hook_input = read_input()
    if hook_input.get("stop_hook_active"):
        return 0
    text = message_text(hook_input, "agent_transcript_path")
    if text is None:
        return 0
    lines = [line for line in text.strip().splitlines() if line.strip()]
    problems = []
    if len(lines) > MAX_LINES:
        problems.append(f"shorten the report from {len(lines)} to at most {MAX_LINES} lines")
    if not any(line.lower().startswith("unverified:") for line in lines):
        problems.append("add a line `Unverified: ...` naming every claim you did not check yourself "
                        "(file:line or command output count as checked), or `Unverified: none`")
    if not problems:
        return 0
    return refuse("[docket] Report format: " + "; ".join(problems) + ". Facts with their source, "
                  "deviations, open points; no narrative.")


if __name__ == "__main__":
    sys.exit(main())
