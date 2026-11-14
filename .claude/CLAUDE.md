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

# Tests

CI (`.github/workflows/test.yml`, Linux and Windows) runs `python hooks/test_hooks.py` (every hook as a process),
`claude plugin validate .`, `claude plugin test` (the mod) and the install path (`claude plugin marketplace add`,
`claude plugin install`); none of them needs a login or a model.

`evals/` holds cases for `claude plugin eval` only. On native Windows it runs a case without a shell grant and without
a scaffold script, in an empty folder (measured 2026-11-14, Claude Code 2.1.322).
