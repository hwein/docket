#!/usr/bin/env python3
"""The project's check: one command, one result line, exit code 0 only when everything passed.

/docket init copies this file into the project (e.g. scripts/check.py) and fills COMMANDS.
The orchestrator runs it after every implementer return; the Stop hook refuses DOCKET DELIVERED
unless it ran after the last agent returned. Keep the result line short: it is quoted as evidence.
"""
import subprocess
import sys

# Fill in: label, command (run through the shell), in the order they should run.
COMMANDS = [
    # ("tests", "cargo test"),
    # ("lint", "cargo clippy -- -D warnings"),
]


def main():
    if not COMMANDS:
        print("check: no commands configured (edit COMMANDS in this file)")
        return 1
    failed = []
    for label, command in COMMANDS:
        result = subprocess.run(command, shell=True)
        if result.returncode != 0:
            failed.append(f"{label} exit {result.returncode}")
    if failed:
        print("check: FAILED " + ", ".join(failed))
        return 1
    print(f"check: {len(COMMANDS)} passed (" + ", ".join(label for label, _ in COMMANDS) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
