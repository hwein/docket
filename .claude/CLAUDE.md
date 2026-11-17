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

# Commits

A commit message is `type(scope): statement`: lower case, imperative, at most 72 characters. Types: feat, fix, docs,
test, refactor, chore, ci, perf, build. The scope is the part of docket the commit changes (init, spec, implement,
pane, hooks). Example: `fix(init): write the work dir pointer and check that docket finds it`.

A body only where the subject does not carry the reason: at most four lines that say why, or a reference
(`Refs #20`). No list of files, no test count, no step numbers, no account of the session that wrote the commit, and
a version number only in a release commit: the diff and the tests hold the rest. Measured 2026-11-17 over the last
100 commits here: subject median 118 characters, 78 over 72, the longest 1815.

# Other projects

This repository is public. No commit message and no file names another project of the author, a path in it or what
happened in a session there: a case is written without its origin (`a work dir in a second repo`, never the project
it happened in). A commit that named one was pushed on 2026-11-17 and replaced with a force push; GitHub went on
serving the replaced commit by its SHA.

# Tests

CI (`.github/workflows/test.yml`, Linux and Windows) runs `python hooks/test_hooks.py` (every hook as a process),
`claude plugin validate .`, `claude plugin test` (the mod) and the install path (`claude plugin marketplace add`,
`claude plugin install`); none of them needs a login or a model.

`evals/` holds cases for `claude plugin eval` only. On native Windows it runs a case without a shell grant and without
a scaffold script, in an empty folder (measured 2026-11-14, Claude Code 2.1.322).
