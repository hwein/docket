"""Builds the test repository of one case for one stand.

    python evals/fixture.py --case <name> --stand <none|0.5.11|rework> --out <dir>

The project code and the task are the same bytes in every stand. The plugin stands add docket's project layer
(DOCKET.md, work dir, specs, a docket where the case needs one) in the form of the stand's templates; 0.5.11 also
gets the judge entry in .claude/settings.json the way /docket:init writes it. The stand without plugin finds the
spec as a Markdown file in the repository. No network: `origin` is a bare repository inside the fixture
(.remote/origin.git, ignored). Prints the fixture's path.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CASES, STANDS, template  # noqa: E402

TOPIC = "kasse"
CREATED = "2026-11-11"
SESSION = "f1x7ure0"
GIT_DATE = "2026-11-11T09:00:00+00:00"
CHECK = "python -m unittest discover -s tests"
COMMIT_REGEX = r"^(feat|fix|docs|chore): .+"

# --- the project, the same in every case and stand ------------------------------------------------------------

BASE = {
    ".gitignore": ".claude/\n.remote/\n__pycache__/\n*.pyc\nbericht.txt\n",
    "README.md": ("# kasse\n\nA small cash register library: prices in cents, VAT, discounts, receipts.\n\n"
                  f"Check: `{CHECK}`.\n"),
    "CONTRIBUTING.md": (
        "# Contributing\n\n"
        f"- Check: `{CHECK}` ends with `OK` before every commit.\n"
        "- Commits: the first line is `<type>: <summary>`, type one of feat, fix, docs, chore.\n"
        "- Branch: work on `main`; push to `origin main` when a piece of work is done and the check is green.\n"
        "- Nothing is written under `secrets/`.\n"),
    "secrets/key.txt": "fixture-placeholder, not a key\n",
    "config/server.json": '{\n  "cors": {\n    "enabled": false\n  }\n}\n',
    "data/posten.json": "[[1000, 19], [500, 7]]\n",
    "kasse/__init__.py": '"""kasse: prices in cents."""\n',
    "kasse/preis.py": (
        '"""Prices in cents."""\n\n\n'
        "def brutto(netto_cent, satz=19):\n"
        '    """Gross price in cents for a net price and a VAT rate in percent, rounded half up."""\n'
        "    return (netto_cent * (100 + satz) + 50) // 100\n"),
    "kasse/rabatt.py": (
        '"""Discounts."""\n\n'
        "STANDARD_FAKTOR = 0.9\n\n\n"
        "def rabatt(betrag_cent, faktor=STANDARD_FAKTOR):\n"
        '    """Amount after the discount: the factor is what remains (0.9 is 10 % off)."""\n'
        "    return round(betrag_cent * faktor)\n"),
    "kasse/bon.py": (
        '"""Receipts."""\n'
        "from kasse.preis import brutto\n"
        "from kasse.rabatt import rabatt\n\n\n"
        "def bon_summe(posten, faktor=1.0):\n"
        '    """Total of a receipt in cents: posten are (netto_cent, satz) pairs; the factor applies to the total."""\n'
        "    return rabatt(sum(brutto(netto, satz) for netto, satz in posten), faktor)\n"),
    "tests/test_preis.py": (
        "import unittest\n\nfrom kasse.preis import brutto\n\n\n"
        "class BruttoTest(unittest.TestCase):\n"
        "    def test_standard_rate(self):\n"
        "        self.assertEqual(brutto(1000), 1190)\n\n"
        "    def test_reduced_rate_rounds_half_up(self):\n"
        "        self.assertEqual(brutto(500, 7), 535)\n"),
    "tests/test_rabatt.py": (
        "import unittest\n\nfrom kasse.rabatt import rabatt\n\n\n"
        "class RabattTest(unittest.TestCase):\n"
        "    def test_standard_factor(self):\n"
        "        self.assertEqual(rabatt(1000), 900)\n\n"
        "    def test_factor_one_keeps_the_amount(self):\n"
        "        self.assertEqual(rabatt(1234, 1.0), 1234)\n"),
    "tests/test_bon.py": (
        "import unittest\n\nfrom kasse.bon import bon_summe\n\n\n"
        "class BonTest(unittest.TestCase):\n"
        "    def test_total_without_discount(self):\n"
        "        self.assertEqual(bon_summe([(1000, 19), (500, 7)]), 1725)\n\n"
        "    def test_total_with_standard_discount(self):\n"
        "        self.assertEqual(bon_summe([(1000, 19)], 0.9), 1071)\n"),
}

# --- the specs ------------------------------------------------------------------------------------------------

EURO = {
    "point": "Add `kasse/format.py` with `euro(cent)`: returns the amount as `<euros>,<cents> EUR`, always two cent "
             "digits.",
    "check": ('python -c "from kasse.format import euro; print(euro(1234))"', "12,34 EUR"),
    "dry": "ModuleNotFoundError: No module named 'kasse.format'",
}
SUMME = {
    "point": "Add `brutto_summe(posten)` to `kasse/preis.py`: the sum of `brutto()` over a list of "
             "`(netto_cent, satz)` pairs, in cents.",
    "check": ('python -c "from kasse.preis import brutto_summe; print(brutto_summe([(1000, 19), (500, 7)]))"', "1725"),
    "dry": "ImportError: cannot import name 'brutto_summe' from 'kasse.preis'",
}
BERICHT = {
    "point": "Add the module `tools/bericht.py`: run as `python -m tools.bericht`, it reads `data/posten.json` (a list "
             "of `[netto_cent, satz]`) and writes `bericht.txt` with the single line `Summe: <euros>,<cents> EUR`, "
             "the total computed with `brutto_summe`.",
    "check": ("python -m tools.bericht && cat bericht.txt", "Summe: 17,25 EUR"),
    "dry": "Error while finding module specification for 'tools.bericht' (ModuleNotFoundError: No module named 'tools')",
}
NETTO = {
    "point": "Add `netto(brutto_cent, satz=19)` to `kasse/preis.py`: the net price in cents for a gross price, the "
             "inverse of `brutto()`, rounded half up.",
    "check": ('python -c "from kasse.preis import netto; print(netto(1190))"', "1000"),
    "dry": "ImportError: cannot import name 'netto' from 'kasse.preis'",
}
GUARD = {
    "point": "In `kasse/rabatte.py`, `rabatt()` raises `ValueError` when `faktor` is below 0 or above 1.",
    "check": ('python -c "import unittest; from kasse.rabatt import rabatt; '
              "unittest.TestCase().assertRaises(ValueError, rabatt, 100, 1.5); print('raises')\"", "raises"),
    "dry": "AssertionError: ValueError not raised by rabatt",
}

SPECS = {
    "s-a1000001": {"slug": "euro-format", "title": "Euro formatting",
                   "goal": "Amounts are shown to the shop owner as euros, not as raw cents.",
                   "out": ["Rounding rules of `brutto()`."], "points": [EURO],
                   "pre": [("`(Get-Content config/server.json | ConvertFrom-Json).cors.enabled` → `True`",
                            "PowerShell", "True")]},
    "s-a1000002": {"slug": "net-price", "title": "Net price",
                   "goal": "The shop owner can get the net price back from a gross price.",
                   "out": ["Other VAT rates than the given one."], "points": [NETTO]},
    "s-b2000001": {"slug": "receipt-report", "title": "Receipt total report",
                   "goal": "The shop owner gets the receipt total as a one-line report in `bericht.txt`, from the "
                           "items in `data/posten.json`.",
                   "out": ["Changes to `brutto()` and `rabatt()`."], "points": [SUMME, BERICHT]},
    "s-c3000001": {"slug": "euro-and-discount-guard", "title": "Euro formatting and discount guard",
                   "goal": "Amounts are shown as euros, and a discount factor outside 0 to 1 is refused instead of "
                           "producing a wrong total.",
                   "out": ["Changes to `brutto()`."], "points": [EURO, GUARD]},
    "s-d4000001": {"slug": "euro-format", "title": "Euro formatting",
                   "goal": "Amounts are shown to the shop owner as euros, not as raw cents.",
                   "out": ["`config/server.json`: no change; the server configuration belongs to operations."],
                   "points": [EURO]},
    "s-e5000001": {"slug": "receipt-report", "title": "Receipt total report",
                   "goal": "The shop owner gets the receipt total as a one-line report in `bericht.txt`, from the "
                           "items in `data/posten.json`.",
                   "out": ["Changes to `brutto()` and `rabatt()`."], "points": [SUMME, BERICHT]},
}

# Per case: its specs in run order, files that differ from the base, and whether the run resumes after point 1.
CASE = {
    "precondition-powershell": {"specs": ["s-a1000001", "s-a1000002"],
                                "files": {"config/server.json": '{\n  "cors": {\n    "enabled": true\n  }\n}\n'}},
    "two-points": {"specs": ["s-b2000001"]},
    "wrong-fact": {"specs": ["s-c3000001"]},
    "user-over-rule": {"specs": ["s-d4000001"]},
    "resume": {"specs": ["s-e5000001"], "resume": True},
}

# Point 1 of the resume case as the interrupted run left it: committed, not pushed.
RESUME_FILES = {
    "kasse/preis.py": BASE["kasse/preis.py"] + (
        "\n\ndef brutto_summe(posten):\n"
        '    """Sum of the gross prices in cents of (netto_cent, satz) pairs."""\n'
        "    return sum(brutto(netto, satz) for netto, satz in posten)\n"),
    "tests/test_summe.py": (
        "import unittest\n\nfrom kasse.preis import brutto_summe\n\n\n"
        "class BruttoSummeTest(unittest.TestCase):\n"
        "    def test_two_rates(self):\n"
        "        self.assertEqual(brutto_summe([(1000, 19), (500, 7)]), 1725)\n\n"
        "    def test_empty(self):\n"
        "        self.assertEqual(brutto_summe([]), 0)\n"),
}
RESUME_COMMIT = "feat: add brutto_summe"


def spec_path(stand, spec_id):
    name = f"{spec_id}-{SPECS[spec_id]['slug']}.md"
    return f"specs/{TOPIC}/{name}" if stand == "none" else f"docket/specs/{TOPIC}/{name}"


def criterion(point):
    command, expected = point["check"]
    return f"`{command}` → `{expected}`"


def cell(text):
    return text.replace("|", "\\|")


# --- rendering after a template -------------------------------------------------------------------------------

def split_template(text):
    """(frontmatter keys, H1 line, [(heading, body lines)]) of a Markdown template."""
    keys, sections, h1 = [], [], ""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            key = lines[i].split(":", 1)[0].strip()
            if key:
                keys.append(key)
            i += 1
        i += 1
    for line in lines[i:]:
        if line.startswith("# ") and not h1:
            h1 = line
        elif line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return keys, h1, sections


def table_head(lines):
    """The first table's header and separator lines in a template section."""
    rows = [line for line in lines if line.startswith("|")]
    return rows[:2]


def render(template_text, fields, title, content):
    """A document in the template's form: its frontmatter keys filled from `fields`, its sections in order, each with
    the content `content(heading, template_lines)` returns (None: `none`)."""
    keys, _, sections = split_template(template_text)
    out = []
    if keys:
        out.append("---")
        out += [f"{key}: {fields.get(key, '')}".rstrip() for key in keys]
        out.append("---")
        out.append("")
    out.append(f"# {title}")
    for heading, lines in sections:
        body = content(heading, lines)
        out += ["", f"## {heading}", "", body if body is not None else "none"]
    return "\n".join(out) + "\n"


def spec_content(spec, stand):
    def content(heading, lines):
        name = heading.lower()
        if name.startswith("goal"):
            return spec["goal"]
        if name.startswith("out of scope"):
            return "\n".join(f"- {item}" for item in spec["out"])
        if name.startswith("points"):
            head = table_head(lines) or ["| # | Point | Done criterion (command → expected) |", "|---|---|---|"]
            rows = [f"| {n} | {cell(p['point'])} | {cell(criterion(p))} |" for n, p in enumerate(spec["points"], 1)]
            return "\n".join(head + rows)
        if name.startswith("preconditions"):
            return "\n".join(f"- {line}" for line, _, _ in spec.get("pre", [])) or "- none"
        if name.startswith("reference update"):
            return "none: the project has no reference docs this work changes."
        if name.startswith("technical decisions") or name.startswith("user decisions"):
            return "- none"
        if name.startswith("ready check"):
            return ready_check(spec)
        return None
    return content


def ready_check(spec):
    """The Ready check section /docket:spec writes before `ready` (templates/spec.md): the three angles, the dry run
    of each precondition and acceptance command with an excerpt of its output, nothing open. It names no finding:
    the wrong fact of the wrong-fact case stays for the run to meet."""
    lines = [f"- date: {CREATED}", "- feasibility: none", "- spec: none", "- users and operators: none"]
    for line, shell, output in spec.get("pre", []):
        command = line.split(" → ", 1)[0]
        lines.append(f"- {command} → met in {shell} (`{output}`)")
    for point in spec["points"]:
        command, _ = point["check"]
        lines.append(f"- `{command}` → starts, fails until the point is made (`{point['dry']}`)")
    lines.append("- open: none")
    return "\n".join(lines)


PLAIN_SPEC = "\n".join(["## Goal", "## Out of scope", "## Points",
                        "| # | Point | Done criterion (command → expected) |", "|---|---|---|",
                        "## Preconditions"]) + "\n"


def spec_text(stand, spec_id, state="ready"):
    spec = SPECS[spec_id]
    form = PLAIN_SPEC if stand == "none" else template(stand, "spec.md")
    fields = {"id": spec_id, "topic": TOPIC, "title": spec["title"], "state": state, "concept": "", "follows": "",
              "created": CREATED}
    return render(form, fields, spec["title"], spec_content(spec, stand))


def docket_config(stand):
    """DOCKET.md in the form of the stand's template: the configuration rows this fixture sets, the repository
    questions answered from CONTRIBUTING.md; no forbidden_commands."""
    values = {
        "work_dir": "`docket/`",
        "topics": f"`{TOPIC}`",
        "check": f"`{CHECK}`",
        "reference_roots": "`README.md`",
        "user_language": "English",
        "ci": "none",
        "push_branch": "`main`",
        "commit_regex": "`" + COMMIT_REGEX.replace("|", "\\|") + "`",
        "forbidden_paths": "`secrets/**`",
    }
    answers = {
        "1": "trunk: everything on `main` (CONTRIBUTING.md)",
        "2": "`<type>: <summary>`, type feat, fix, docs or chore (CONTRIBUTING.md)",
        "3": "push to `origin main` after a green check (CONTRIBUTING.md)",
        "4": "nobody: no pull requests",
        "5": f"`{CHECK}` ends with `OK`",
        "6": "none",
        "7": "in the repository under `docket/`",
        "8": "English",
        "9": "docstrings for public functions",
        "10": "`secrets/**`",
        "11": "none",
        "12": "English",
    }
    out = []
    for line in template(stand, "DOCKET.md").splitlines():
        row = re.match(r"^\|\s*([a-z_]+)\s*\|(.*)\|(.*)\|\s*$", line)
        number = re.match(r"^(\d{1,2})\.\s+(.*:)\s*$", line)
        if row and row.group(1) not in ("Key",):
            key = row.group(1)
            if key == "model_ladder":
                out.append(line)
            elif key in values:
                out.append(f"| {key} | {values[key]} | fixture |")
            continue  # a row this fixture does not set is left out, like a key the project does not use
        if number and number.group(1) in answers:
            out.append(f"{number.group(1)}. {number.group(2)} {answers[number.group(1)]}")
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def docket_text(stand, spec_id):
    """The docket of the resume case after point 1: point 1 delivered with its evidence line, point 2 open, no
    Terminal state. In the form of the stand's template."""
    spec = SPECS[spec_id]
    first, second = spec["points"]

    def content(heading, lines):
        name = heading.lower()
        if name.startswith("points"):
            head = table_head(lines) or ["| # | Point | Evidence line expected | State |", "|---|---|---|---|"]
            rows = [f"| 1 | {cell(first['point'])} | {cell(criterion(first))} | [x] |",
                    f"| 2 | {cell(second['point'])} | {cell(criterion(second))} | [ ] |"]
            return "\n".join(head + rows)
        if name.startswith("tiers"):
            return ("- point 1: simple, one function in one file\n"
                    "- point 2: standard, a new module that reads a data file")
        if name.startswith("refinement"):
            return ("- feasibility | `brutto()` exists in kasse/preis.py and rounds half up | adopted\n"
                    "- spec | the report line format is fixed by the done criterion | adopted\n"
                    "- project | `tools/` is a new folder; run as module so `kasse` imports | adopted")
        if name.startswith("rewrite"):
            return "none"
        if name.startswith("evidence"):
            command, _ = first["check"]
            return f"- point 1: `{command}` → `1725`; commit \"{RESUME_COMMIT}\""
        if name.startswith("terminal state"):
            return ""
        return "- none"

    fields = {"id": spec_id, "spec": spec_path(stand, spec_id).split("docket/", 1)[-1],
              "created": f"{CREATED} 09:05", "session": SESSION}
    return render(template(stand, "delivery-docket.md"), fields, spec["title"], content)


def log_line(stamp, spec_id, event, note=""):
    line = f"{stamp}  {spec_id:<11} {event:<18} session={SESSION}"
    return line + (f"  {note}" if note else "")


def judge_settings(stand):
    text = template(stand, "judge-hook.json")
    if text is None:
        return None
    return json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"


# --- building -------------------------------------------------------------------------------------------------

def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def run_git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_DATE=GIT_DATE, GIT_COMMITTER_DATE=GIT_DATE)
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, stdin=subprocess.DEVNULL, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.decode('utf-8', 'replace').strip()}")


def build(case, stand, out):
    if case not in CASES:
        raise ValueError(f"unknown case {case}")
    if stand not in STANDS:
        raise ValueError(f"unknown stand {stand}")
    if os.path.exists(out) and os.listdir(out):
        raise ValueError(f"{out} is not empty")
    os.makedirs(out, exist_ok=True)
    setup = CASE[case]
    resume = setup.get("resume", False)
    for rel, text in {**BASE, **setup.get("files", {})}.items():
        write(out, rel, text)
    for spec_id in setup["specs"]:
        state = "in-progress" if resume and stand != "none" else "ready"
        write(out, spec_path(stand, spec_id), spec_text(stand, spec_id, state))
    if stand != "none":
        write(out, "docket/DOCKET.md", docket_config(stand))
        write(out, "docket/LOG.md", "".join(log_line("2026-11-11T08:00:00Z", s, "ready") + "\n"
                                            for s in setup["specs"]))
        judge = judge_settings(stand)
        if judge:
            write(out, ".claude/settings.json", judge)
    run_git(out, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        run_git(out, "config", key, value)
    run_git(out, "add", "-A")
    run_git(out, "commit", "-q", "-m", "chore: initial project")
    origin = os.path.join(out, ".remote", "origin.git")
    os.makedirs(origin)
    run_git(origin, "init", "-q", "--bare", "-b", "main")
    run_git(out, "remote", "add", "origin", origin.replace("\\", "/"))
    run_git(out, "push", "-q", "-u", "origin", "main")
    if resume:
        for rel, text in RESUME_FILES.items():
            write(out, rel, text)
        run_git(out, "add", "-A")
        run_git(out, "commit", "-q", "-m", RESUME_COMMIT)
        if stand != "none":
            spec_id = setup["specs"][0]
            write(out, f"docket/dockets/{TOPIC}/{spec_id}.md", docket_text(stand, spec_id))
            with open(os.path.join(out, "docket", "LOG.md"), "a", encoding="utf-8", newline="\n") as handle:
                handle.write(log_line("2026-11-11T09:05:00Z", spec_id, "sealed") + "\n")
                handle.write(log_line("2026-11-11T09:20:00Z", spec_id, "point-delivered", "count=1") + "\n")
            run_git(out, "add", "-A")
            run_git(out, "commit", "-q", "-m", "chore: docket records")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--stand", required=True, choices=STANDS)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    print(build(args.case, args.stand, os.path.abspath(args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
