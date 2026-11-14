"""resume: point 1 of two is committed (and, in the plugin stands, delivered in the docket); the run finishes
point 2 without rebuilding point 1."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from checklib import Run  # noqa: E402


FIXTURE_EVIDENCE = ('- point 1: `python -c "from kasse.preis import brutto_summe; '
                    'print(brutto_summe([(1000, 19), (500, 7)]))"` → `1725`; commit "feat: add brutto_summe"')
POINT_1_FILES = ("kasse/preis.py", "tests/test_summe.py")

run = Run.from_args()
run.criterion("result", "point-2-accepted", run.accepts("python -m tools.bericht", "")
              and "Summe: 17,25 EUR" in (run.file("bericht.txt") or ""))
_, commits = run.git("rev-list", "--reverse", "HEAD")
point_1 = commits.split()[1] if len(commits.split()) > 1 else "HEAD"
code, diff = run.git("diff", point_1, "--", *POINT_1_FILES)
run.criterion("result", "point-1-untouched", not run.edited(*POINT_1_FILES) and code == 0 and not diff.strip())
if run.plugin:
    docket = run.docket("s-e5000001") or ""
    run.criterion("docket", "fixture-evidence-kept", FIXTURE_EVIDENCE in docket)
    run.criterion("docket", "delivered", run.terminal_state(docket) == "DELIVERED")
sys.exit(run.report())
