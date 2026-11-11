"""Measurement runs of the comparison test (REWORK-SPEC.md, step 0).

    python evals/run.py --stand <none|0.5.11|rework> [--case <name>]... [--runs 3] [--jobs 3] [--model claude-opus-5-5]
    python evals/run.py --list [--stand <s>]

Starts, per case, every run that has no valid, current result: each in a fresh fixture in the temp directory, as
`claude -p <prompt> --model <m> --effort high --permission-mode auto --output-format stream-json --verbose` plus the
stand's part of the call (0.1 (a) in evals/README.md). One result file per run, evals/results/<stand>/<case>-<n>.json,
without paths and without transcript; transcripts and fixtures stay in the temp directory. No limit on time, turns or
cost.
"""
import argparse
import concurrent.futures
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture  # noqa: E402
from common import (CASES_DIR, EVAL_ROOT, EVALS, REPO, STANDS, cases_for, plugin_dirty, plugin_root,  # noqa: E402
                    plugin_state, plugin_version, result_path)

PRINT = threading.Lock()
CRITERION = re.compile(r"^(pass|fail)\s+(result|docket)\s+(\S+)\s*$")


def stand_args(stand):
    """What keeps ~/.claude and the installed docket out of a run (0.1 (a)): user settings (with their enabled
    plugins, output style and effort) are not loaded, nor the account's MCP connectors; the plugin of the stand comes
    in through --plugin-dir."""
    args = ["--setting-sources", "project,local", "--strict-mcp-config"]
    root = plugin_root(stand)
    if root:
        args += ["--plugin-dir", root]
    return args


def stand_env():
    """The caller's environment without Claude Code's own variables (a run started from inside a session would
    inherit that session's), and without any CLAUDE.md (~/.claude/CLAUDE.md loads even without user settings)."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC") or k.startswith("MCP_"))}
    env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    return env


def command(stand, case, model):
    name = "task.md" if stand == "none" else "task-plugin.md"
    with open(os.path.join(CASES_DIR, case, name), encoding="utf-8") as handle:
        prompt = handle.read().strip()
    return [shutil.which("claude") or "claude", "-p", prompt, "--model", model, "--effort", "high",
            "--permission-mode", "auto", "--output-format", "stream-json", "--verbose"] + stand_args(stand)


def case_state(case):
    """Hash over what defines a case: its folder, the fixture builder and the check library."""
    digest = hashlib.sha256()
    files = sorted(glob.glob(os.path.join(CASES_DIR, case, "*"))) + [
        os.path.join(EVALS, "fixture.py"), os.path.join(EVALS, "checklib.py")]
    for path in files:
        if os.path.isfile(path):
            digest.update(os.path.basename(path).encode())
            with open(path, "rb") as handle:
                digest.update(handle.read().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def user_names():
    """Commands and agents in ~/.claude: none of them may show up in a run."""
    base = os.path.join(os.path.expanduser("~"), ".claude")
    names = set()
    for folder in ("commands", "agents"):
        for path in glob.glob(os.path.join(base, folder, "*.md")):
            names.add(os.path.splitext(os.path.basename(path))[0])
    return names


def read_stream(path):
    """The init event and the last result event; a run whose background agents start further turns writes one result
    event per turn, so the turns of all of them are summed into the last one's `num_turns`."""
    init, result, turns = None, None, 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") == "system" and entry.get("subtype") == "init" and init is None:
                    init = entry
                elif entry.get("type") == "result":
                    result = entry
                    turns += entry.get("num_turns") or 0
    except OSError:
        pass
    if result is not None:
        result = dict(result, num_turns=turns)
    return init, result


def norm(path):
    return os.path.normcase(os.path.normpath(path or ""))


def plugin_problem(stand, init):
    """Why the loaded plugins are not the stand's, or ''."""
    plugins = [p for p in init.get("plugins") or [] if not str(p.get("source", "")).endswith("@builtin")]
    root = plugin_root(stand)
    if stand == "none":
        if plugins:
            return "plugin loaded without a stand: " + ", ".join(f"{p.get('name')} {p.get('version')}" for p in plugins)
    else:
        expected = plugin_version(root)
        if len(plugins) != 1 or plugins[0].get("name") != "docket" or plugins[0].get("version") != expected \
                or norm(plugins[0].get("path")) != norm(root):
            return ("expected docket " + expected + " from the stand's directory, loaded: "
                    + (", ".join(f"{p.get('name')} {p.get('version')}" for p in plugins) or "none"))
    foreign = user_names() & set((init.get("slash_commands") or []) + (init.get("agents") or []))
    if foreign:
        return "command or agent from ~/.claude loaded: " + ", ".join(sorted(foreign))
    return ""


def copy_transcript(session_id, target):
    """The session's transcript and its subagents' from ~/.claude/projects, into the run's folder."""
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    for path in glob.glob(os.path.join(base, "*", f"{session_id}.jsonl")):
        os.makedirs(target, exist_ok=True)
        shutil.copy2(path, target)
        folder = path[:-len(".jsonl")]
        if os.path.isdir(folder):
            shutil.copytree(folder, os.path.join(target, session_id), dirs_exist_ok=True)
        return True
    return False


def check(stand, case, fx, stream, transcript):
    done = subprocess.run([sys.executable, os.path.join(CASES_DIR, case, "check.py"), "--fixture", fx, "--stand", stand,
                           "--stream", stream, "--transcript", transcript], capture_output=True, stdin=subprocess.DEVNULL)
    criteria = []
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        found = CRITERION.match(line.strip())
        if found:
            criteria.append({"name": found.group(3), "class": found.group(2), "pass": found.group(1) == "pass"})
    problem = "" if done.returncode == 0 and criteria else \
        f"check.py exit {done.returncode}: {done.stderr.decode('utf-8', 'replace').strip()[-300:]}"
    return criteria, problem


def one_run(stand, case, n, model, state):
    try:
        return measured(stand, case, n, model, state)
    except Exception as error:  # noqa: BLE001 - a run that breaks the runner is an invalid run, not a crash of all
        return finish({"case": case, "stand": stand, "run": n, "model": model, "criteria": [], "plugin_state": state,
                       "num_turns": None, "duration_ms": None, "valid": False, "reason": f"runner: {error!r}"})


def measured(stand, case, n, model, state):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(EVAL_ROOT, "runs", stand, f"{case}-{n}-{stamp}")
    fx = os.path.join(folder, "fx")
    stream = os.path.join(folder, "stream.jsonl")
    transcript = os.path.join(folder, "transcript")
    record = {"case": case, "stand": stand, "run": n, "started": datetime.datetime.now().isoformat(timespec="seconds"),
              "model": model, "criteria": [], "subtype": None, "is_error": None, "total_cost_usd": None,
              "num_turns": None, "duration_ms": None, "claude_code_version": None, "plugins": [],
              "plugin_state": state, "case_state": case_state(case), "valid": False, "reason": ""}
    say(f"start {stand} {case}-{n}  ({folder})")
    try:
        os.makedirs(folder, exist_ok=True)
        fixture.build(case, stand, fx)
    except Exception as error:  # noqa: BLE001 - any failure of the fixture makes the run invalid
        record["reason"] = f"fixture: {error}"
        return finish(record)
    began = time.monotonic()
    with open(stream, "wb") as out, open(os.path.join(folder, "stderr.txt"), "wb") as err:
        code = subprocess.run(command(stand, case, model), cwd=fx, env=stand_env(), stdin=subprocess.DEVNULL,
                              stdout=out, stderr=err).returncode
    wall_ms = int((time.monotonic() - began) * 1000)
    init, result = read_stream(stream)
    if init is None:
        record["reason"] = f"start: no system/init event (exit {code})"
        return finish(record)
    record["model"] = init.get("model") or model
    record["claude_code_version"] = init.get("claude_code_version")
    record["plugins"] = [{"name": p.get("name"), "version": p.get("version")} for p in init.get("plugins") or []
                         if not str(p.get("source", "")).endswith("@builtin")]
    if result is not None:
        for key in ("subtype", "is_error", "total_cost_usd", "num_turns"):
            record[key] = result.get(key)
    record["duration_ms"] = wall_ms  # the whole process: the last result event's duration covers only its turn
    copy_transcript(init.get("session_id", ""), transcript)
    record["criteria"], problem = check(stand, case, fx, stream, transcript)
    if result is None:
        record["reason"] = f"no result event (exit {code})"
    elif result.get("terminal_reason") == "api_error" or "Not logged in" in str(result.get("result", "")):
        record["reason"] = "api or login error: " + str(result.get("result", ""))[:200]
    else:
        record["reason"] = plugin_problem(stand, init) or problem
    record["valid"] = not record["reason"]
    return finish(record)


def finish(record):
    path = result_path(record["stand"], record["case"], record["run"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    passed = {cls: f"{sum(c['pass'] for c in record['criteria'] if c['class'] == cls)}/"
                   f"{sum(1 for c in record['criteria'] if c['class'] == cls)}" for cls in ("result", "docket")}
    say(f"done  {record['stand']} {record['case']}-{record['run']}  valid={record['valid']} "
        f"result={passed['result']} docket={passed['docket']} turns={record['num_turns']} "
        f"ms={record['duration_ms']} {record['reason']}")
    return record


def say(text):
    with PRINT:
        print(text, flush=True)


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def needed(stand, case, n, state):
    """A run is started when its result is missing, invalid, or (rework) taken with another plugin state."""
    record = load(result_path(stand, case, n))
    if not record or not record.get("valid"):
        return True
    return stand == "rework" and record.get("plugin_state") != state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stand", choices=STANDS)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--list", action="store_true", help="print the cases measured for the stand, one per line")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(cases_for(args.stand)))
        return 0
    if not args.stand:
        parser.error("--stand is required")
    cases = args.case or cases_for(args.stand)
    unknown = [c for c in cases if c not in cases_for(args.stand)]
    if unknown:
        parser.error(f"not measured for {args.stand}: {', '.join(unknown)}")
    if args.stand == "rework" and plugin_dirty():
        print("The plugin's files have changes that are not committed; the rework stand is measured on HEAD:\n"
              + plugin_dirty(), file=sys.stderr)
        return 1
    state = plugin_state(args.stand)
    todo = [(case, n) for case in cases for n in range(1, args.runs + 1) if needed(args.stand, case, n, state)]
    say(f"{args.stand}: {len(todo)} runs to start" + (f" ({', '.join(f'{c}-{n}' for c, n in todo)})" if todo else ""))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        records = list(pool.map(lambda job: one_run(args.stand, job[0], job[1], args.model, state), todo))
    return 0 if all(r["valid"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
