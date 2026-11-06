# Sessions in this repo

Analysing a session (a transcript, judge verdicts, a violations file, hook output of another session) is
done by a subagent (Explore or general-purpose) that returns findings with entry numbers. The main context
never holds the judge prompt or another session's rule text verbatim: 83 % rule text in the context made a
session argue in the judge's terms where no judge ran.

Editing the hooks, commands and templates is development, not analysis: read those files directly.

# Release

This machine installs docket from GitHub like every user (`claude plugin marketplace add hwein/docket`), never from
this working tree. An edit here reaches no session until it is committed, pushed, green in CI
(`.github/workflows/test.yml`, Linux and Windows) and installed with `claude plugin marketplace update docket-local`
and `claude plugin update docket@docket-local` (restart needed).

A change to the code part of `templates/check.py` or to `templates/judge-hook.json` appends its SHA-256 to
`CHECK_VERSIONS` or `JUDGE_VERSIONS` in `hooks/session_start.py` (a test fails otherwise): session start keeps
every project's copy current.
