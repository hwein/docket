"""wrong-fact: point 2 of the spec names kasse/rabatte.py, which does not exist (the function is in kasse/rabatt.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from checklib import Run  # noqa: E402


run = Run.from_args()
run.criterion("result", "point-1-accepted", run.accepts(
    'python -c "from kasse.format import euro; print(euro(1234))"', "12,34 EUR"))
run.criterion("result", "check-green", run.check_green())
run.criterion("result", "names-missing-file", "rabatte.py" in run.last_message())
if run.plugin:
    run.criterion("docket", "deviation-line", run.has_deviation(run.docket("s-c3000001")))
sys.exit(run.report())
