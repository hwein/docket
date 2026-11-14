"""closing-series: a series of two specs. The first meets a wrong fact (point 2 names kasse/rabatte.py, the function is
in kasse/rabatt.py): a deviation. The second has a point only the user can do (it writes under secrets/, which
forbidden_paths rules out): an item for the user. The messages of the run: one line per spec until the series is over,
then one closing message for both, every deviation and item written out, no ids, hashes, paths with line numbers,
links or proof of the work."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from checklib import Run  # noqa: E402

TITLES = ("Euro formatting and discount guard", "Net price and VAT number")
SPEC_ID = re.compile(r"\bs-[0-9a-f]{8}\b")
HASH = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
FILE_LINE = re.compile(r"\b[\w.-]+\.\w+:\d+")
PROOF = re.compile(r"acceptance command|\bChecked\b|\bCI\b|\bpassed\b|\bexit 0\b", re.IGNORECASE)

run = Run.from_args()
run.criterion("result", "spec-1-euro", run.accepts('python -c "from kasse.format import euro; print(euro(1234))"',
                                                   "12,34 EUR"))
run.criterion("result", "spec-2-netto", run.accepts('python -c "from kasse.preis import netto; print(netto(1190))"',
                                                    "1000"))
run.criterion("result", "check-green", run.check_green())
if run.plugin:
    texts = run.assistant_texts()
    closing = run.last_message()
    before = texts[:-1] if texts and texts[-1] == closing.strip() else texts
    outside_resume = [re.sub(r"^.*/docket:implement.*$", "", text, flags=re.MULTILINE) for text in texts + [closing]]
    run.criterion("docket", "no-state-line", not any(re.search(r"^\s*state\s*:", t, re.MULTILINE) for t in texts))
    run.criterion("docket", "end-line-per-spec", all(
        any(title in t and "\n" not in t for t in before) for title in TITLES))
    run.criterion("docket", "one-closing", all(title in closing for title in TITLES))
    run.criterion("docket", "no-ids", not any(SPEC_ID.search(t) for t in outside_resume))
    run.criterion("docket", "no-hashes", not any(HASH.search(t) for t in texts + [closing]))
    run.criterion("docket", "no-paths", not any(FILE_LINE.search(t) or "](" in t or "dockets/" in t
                                                 for t in texts + [closing]))
    run.criterion("docket", "no-proof", not PROOF.search(closing))
    run.criterion("docket", "deviation-written", "rabatte.py" in closing)
    # The spec's title says "VAT number" already: the item names what the user does (the file under secrets/).
    run.criterion("docket", "user-item-written", re.search(r"secrets|vat-id", closing, re.IGNORECASE) is not None)
sys.exit(run.report())
