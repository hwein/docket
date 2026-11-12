# Sessions in this repo

Analysing a session (a transcript, judge verdicts, a violations file, hook output of another session) is
done by a subagent (Explore or general-purpose) that returns findings with entry numbers. The main context
never holds the judge prompt or another session's rule text verbatim: 83 % rule text in the context made a
session argue in the judge's terms where no judge ran.

Editing the hooks, commands and templates is development, not analysis: read those files directly.

# Release

This machine installs docket from GitHub like every user (`claude plugin marketplace add hwein/docket`), never from
this working tree. An edit here reaches no session until it is committed, pushed, green in CI
(`.github/workflows/test.yml`, Linux and Windows) and installed with `claude plugin marketplace update docket`
and `claude plugin update docket@docket` (restart needed).

docket adds no code file to a user's repository.

# Evals

`evals/` is the project's comparison test, not `claude plugin eval`: `python evals/run.py --stand <stand>` runs each
case headless (`claude -p`) in a fresh fixture repository on the committed plugin files, and `python evals/report.py`
reads the result files under `evals/results/`.
