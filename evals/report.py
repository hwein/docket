"""Report of the comparison test, read from the result files only.

    python evals/report.py --stand <s> [--stand <s>]... [--case <name>]... [--min-pass <n>] [--not-below <stand>]...
                           [--table]

One line per case and stand: case, stand, `result p/v`, `docket p/v`, valid runs, loaded plugin with version. A run
passes a class when every criterion of that class passes. Expected per case: three valid runs, or six where a case
missed its threshold and got three more (5 of 6 meets "3 of 3", 4 of 6 meets "2 of 3"). `--min-pass` holds for both
classes; `--not-below` compares `result` with the other stand and ends with `equal with <stand>: <cases>`. Exit 1
when runs are missing, invalid or stale, or a threshold is missed. `--table` prints the Markdown table of every case
of the named stands instead.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CASES, NOT_MEASURED, STANDS, cases_for, plugin_state, result_path  # noqa: E402

CLASSES = ("result", "docket")
MAX_RUNS = 6


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


class Cell:
    """The runs of one case in one stand."""

    def __init__(self, stand, case, state):
        self.stand, self.case = stand, case
        self.records = [r for r in (load(result_path(stand, case, n)) for n in range(1, MAX_RUNS + 1)) if r]
        self.invalid = [r for r in self.records if not r.get("valid")]
        self.stale = [r for r in self.records if r.get("valid") and stand == "rework" and r.get("plugin_state") != state]
        self.valid = [r for r in self.records if r.get("valid") and r not in self.stale]

    def passes(self, cls):
        """(runs passing the class, valid runs), or None where the class has no criterion in this stand."""
        runs = [[c for c in r.get("criteria", []) if c.get("class") == cls] for r in self.valid]
        if not any(runs):
            return None
        return sum(1 for crit in runs if crit and all(c.get("pass") for c in crit)), len(self.valid)

    def text(self, cls):
        found = self.passes(cls)
        return "-" if found is None else f"{found[0]}/{found[1]}"

    def plugin(self):
        names = {f"{p.get('name')} {p.get('version')}" for r in self.valid for p in r.get("plugins") or []}
        return ", ".join(sorted(names)) or "none"

    def mean(self, key):
        values = [r.get(key) for r in self.valid if isinstance(r.get(key), (int, float))]
        return sum(values) / len(values) if values else None

    def problems(self, min_pass):
        found = []
        count = len(self.valid)
        if count not in (3, 6):
            found.append(f"{count} valid runs, expected 3 or 6")
        if self.invalid:
            found.append("invalid: " + ", ".join(f"run {r.get('run')} ({r.get('reason', '')[:80]})" for r in self.invalid))
        if self.stale:
            found.append("stale: " + ", ".join(f"run {r.get('run')}" for r in self.stale))
        if min_pass is not None and count:
            need = min_pass if count <= 3 else min_pass + (count - 3) - 1
            for cls in CLASSES:
                result = self.passes(cls)
                if result is not None and result[0] < need:
                    found.append(f"{cls} {result[0]}/{result[1]} below {need}")
        return found


def rate(cell):
    found = cell.passes("result")
    return None if not found or not found[1] else found[0] / found[1]


def table(stands, cases, cells):
    head = "| Fall | " + " | ".join(f"{s}: result | {s}: docket | {s}: Läufe" for s in stands) + " |"
    rule = "|---|" + "---|---|---|" * len(stands)
    lines, notes = [head, rule], []
    for case in cases:
        row = [case]
        for stand in stands:
            if (case, stand) in NOT_MEASURED:
                notes.append(f"({len(notes) + 1}) {case}, {stand}: {NOT_MEASURED[(case, stand)]}.")
                row += [f"nicht gemessen ({len(notes)})", "-", "-"]
                continue
            cell = cells[(stand, case)]
            turns, ms = cell.mean("num_turns"), cell.mean("duration_ms")
            runs = str(len(cell.valid))
            if turns is not None and ms is not None:
                runs += f" (Ø {turns:.0f} Züge, {ms / 60000:.0f} min)"
            docket = "nur result" if stand == "none" else cell.text("docket")
            row += [cell.text("result"), docket, runs]
        lines.append("| " + " | ".join(row) + " |")
    plugins = []
    for stand in stands:
        loaded = {cells[(stand, c)].plugin() for c in cases if (stand, c) in cells}
        plugins.append(f"{stand}: {', '.join(sorted(loaded)) or 'none'}")
    return "\n".join(lines + [""] + notes + ["", "Geladenes Plugin: " + "; ".join(plugins) + "."]) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stand", action="append", choices=STANDS, required=True)
    parser.add_argument("--case", action="append", default=[], choices=CASES)
    parser.add_argument("--min-pass", type=int)
    parser.add_argument("--not-below", action="append", default=[], choices=STANDS)
    parser.add_argument("--table", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # umlauts and LF, also on Windows
    stands = list(dict.fromkeys(args.stand))
    states = {s: plugin_state(s) for s in set(stands) | set(args.not_below)}
    cells = {(s, c): Cell(s, c, states[s]) for s in set(stands) | set(args.not_below) for c in cases_for(s)}
    failed = False
    if args.table:
        cases = [c for c in CASES if not args.case or c in args.case]
        sys.stdout.write(table(stands, cases, cells))
        for stand in stands:
            for case in cases_for(stand):
                if case in cases and cells[(stand, case)].problems(None):
                    failed = True
        return 1 if failed else 0
    for stand in stands:
        for case in [c for c in cases_for(stand) if not args.case or c in args.case]:
            cell = cells[(stand, case)]
            problems = cell.problems(args.min_pass)
            print(f"{case}  {stand}  result {cell.text('result')}  docket {cell.text('docket')}  "
                  f"valid {len(cell.valid)}  plugin {cell.plugin()}" + ("  | " + "; ".join(problems) if problems else ""))
            failed = failed or bool(problems)
        for other in args.not_below:
            equal, skipped = [], []
            for case in [c for c in cases_for(stand) if not args.case or c in args.case]:
                if (other, case) not in cells:
                    skipped.append(case)
                    continue
                ours, theirs = rate(cells[(stand, case)]), rate(cells[(other, case)])
                if ours is None or theirs is None:
                    print(f"{case}  {stand}  not comparable with {other}: no valid runs")
                    failed = True
                elif ours < theirs:
                    print(f"{case}  {stand}  result below {other}: {cells[(stand, case)].text('result')} against "
                          f"{cells[(other, case)].text('result')}")
                    failed = True
                elif ours == theirs:
                    equal.append(case)
            if skipped:
                print(f"not measured by {other}, skipped: {', '.join(skipped)}")
            print(f"equal with {other}: {', '.join(equal) or '-'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
