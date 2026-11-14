"""two-points: one spec with two points, the second building on the first."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from checklib import Run  # noqa: E402


run = Run.from_args()
run.criterion("result", "point-1-accepted", run.accepts(
    'python -c "from kasse.preis import brutto_summe; print(brutto_summe([(1000, 19), (500, 7)]))"', "1725"))
run.criterion("result", "point-2-accepted", run.accepts("python -m tools.bericht", "")
              and "Summe: 17,25 EUR" in (run.file("bericht.txt") or ""))
run.criterion("result", "check-green", run.check_green())
run.criterion("result", "pushed-to-main", run.origin_has("main", "tools/bericht.py"))
if run.plugin:
    docket = run.docket("s-b2000001")
    run.criterion("docket", "delivered", run.terminal_state(docket) == "DELIVERED")
    run.criterion("docket", "evidence-per-point", bool(docket) and run.evidence_for(docket, 1)
                  and run.evidence_for(docket, 2))
sys.exit(run.report())
