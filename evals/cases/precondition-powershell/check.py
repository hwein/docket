"""precondition-powershell: a series of two independent specs; the first carries a precondition in PowerShell
syntax that holds (config/server.json has cors.enabled true)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from checklib import Run  # noqa: E402


run = Run.from_args()
run.criterion("result", "spec-1-euro", run.accepts('python -c "from kasse.format import euro; print(euro(1234))"',
                                                   "12,34 EUR"))
run.criterion("result", "spec-2-netto", run.accepts('python -c "from kasse.preis import netto; print(netto(1190))"',
                                                    "1000"))
run.criterion("result", "check-green", run.check_green())
if run.plugin:
    run.criterion("docket", "precondition-in-powershell",
                  run.ran("PowerShell", r"ConvertFrom-Json") or run.ran("Bash", r"powershell.*ConvertFrom-Json"))
    docket = run.docket("s-a1000001")
    run.criterion("docket", "spec-1-not-blocked", docket is not None and run.terminal_state(docket) != "BLOCKED")
sys.exit(run.report())
