"""What run.py, report.py, fixture.py and the cases' check.py share: the cases, the stands, where a stand's plugin
lives and how its state is fingerprinted."""
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile

EVALS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVALS)
CASES_DIR = os.path.join(EVALS, "cases")
RESULTS_DIR = os.path.join(EVALS, "results")

CASES = ("precondition-powershell", "two-points", "wrong-fact", "user-over-rule", "resume",
         "closing-series")
STANDS = ("none", "0.5.11", "rework")
BASE_COMMIT = "c7cf2b7"  # docket 0.5.11
PLUGIN_PATHS = ("hooks", "commands", "agents", "templates", "output-styles", "skills", ".claude-plugin")
# A case a stand does not measure, and why (REWORK-SPEC.md 0.3).
NOT_MEASURED = {
    ("resume", "0.5.11"): ("a hand-built 0.5.11 docket does not pass the gates at DELIVERED (refinement lines with "
                           "decision, review, provenance, hooks/stop.py:324-328); the state comes only from a real run"),
}
EVAL_ROOT = os.path.join(tempfile.gettempdir(), "docket-evals")


def cases_for(stand=None):
    """The cases a stand is measured on; every case without a stand."""
    return [c for c in CASES if stand is None or (c, stand) not in NOT_MEASURED]


def git(*args, cwd=REPO, binary=False):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def export_dir():
    return os.path.join(EVAL_ROOT, f"plugin-0.5.11-{BASE_COMMIT}")


def ensure_export():
    """docket 0.5.11 as `git archive` of the base commit, outside the working tree; made where it is missing."""
    target = export_dir()
    if os.path.isfile(os.path.join(target, ".claude-plugin", "plugin.json")):
        return target
    data = git("archive", "--format=tar", BASE_COMMIT, binary=True)
    partial = target + ".partial"
    os.makedirs(partial, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(partial, filter="data")
    os.replace(partial, target)
    return target


def plugin_root(stand):
    """The directory passed as --plugin-dir, None for the stand without plugin."""
    if stand == "none":
        return None
    if stand == "0.5.11":
        return ensure_export()
    if stand == "rework":
        return REPO
    raise ValueError(f"unknown stand {stand}")


def plugin_state(stand):
    """Hash over the plugin's files as git sees them: `git ls-files -s` of the working tree for rework,
    `git ls-tree -r` of the base commit for 0.5.11, empty for none."""
    if stand == "none":
        return ""
    if stand == "0.5.11":
        listing = git("ls-tree", "-r", BASE_COMMIT, "--", *PLUGIN_PATHS)
    else:
        listing = git("ls-files", "-s", "--", *PLUGIN_PATHS)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def plugin_dirty():
    """`git status --short` of the plugin's paths in the working tree."""
    return git("status", "--short", "--", *PLUGIN_PATHS).strip()


def plugin_version(root):
    with open(os.path.join(root, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
        return json.load(handle)["version"]


def template(stand, name):
    """A template file of the stand: from the base commit for 0.5.11, from the working tree for rework."""
    if stand == "0.5.11":
        try:
            return git("show", f"{BASE_COMMIT}:templates/{name}")
        except RuntimeError:
            return None
    path = os.path.join(REPO, "templates", name)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def result_path(stand, case, n):
    return os.path.join(RESULTS_DIR, stand, f"{case}-{n}.json")
