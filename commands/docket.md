---
description: "The docket register, project setup and run control: /docket (register), /docket init (audit and interview), /docket stop (end a sealed run), /docket status."
argument-hint: "[init | stop | status]"
---

# /docket

Argument: `$ARGUMENTS`

## No argument: the register

Render the register from the files, never from memory: every concept, spec and docket under the work dir with
title, topic, state and dependencies (`concept:`, `follows:`, `after`), plus the last ten LOG lines in words.
Group by topic. Dependencies give a rough order, nothing more: never present the register as a roadmap and make
no promises about what comes next. Ids are shown in the table only.

## `init`: audit and interview

Runs any time, never required. The repo is the first interview partner, the user the second: the user decides
only where the repo is silent or contradicts itself. Nothing is written before the diff is approved.

1. Findings, with a source for each (file, command, number), for the eleven questions:
   branch model (`git branch -a`, default branch, branch protection); commit form (last 200 messages: convention,
   language, ticket numbers); way into the target branch (merged PRs vs direct commits, squash vs merge commits);
   who merges; gate before merge (workflows on pull_request, check scripts); release (tags, release workflow,
   CHANGELOG, version field); storage of specs and dockets (existing spec folders, external repos in CLAUDE.md);
   language (comments, README, issues); commenting (comment lines per code line per language: sparse < 5 %,
   normal, verbose > 15 %; existing section markers); forbidden paths and content (.gitignore, guard hooks,
   CLAUDE.md); external systems an agent may touch (permissions in settings, CLAUDE.md rules).
   Also: existing CLAUDE.md, AGENTS.md, CONTRIBUTING.md, hooks, commands, skills, permissions; a check script with
   exit code and one result line. For every existing hook, command or skill, say what it does in one line and
   whether the plugin does the same (write guard on specs, timestamp check, report limit, read redirect,
   required reading, mode switch): a duplicate gets one card, replace (project hook disabled in the diff) or keep
   (both run).
2. Compare findings with each other and with the framework rules. Three classes: unambiguous (one finding, no
   objection), ambiguous or contradictory, silent.
3. Interview: unambiguous findings are listed once ("adopted, source") and confirmed in one go at the end.
   Ambiguous and silent questions get one card each (AskUserQuestion): both findings with source, the effect in
   one sentence, the recommendation first, answerable with one word. A card never asks two things and never gives
   the user a task.
4. Diff: `DOCKET.md` in the work dir (configuration, repository behaviour or pointers to where the project already
   answers, deviations, audit fingerprints), `.gitignore` lines, the permission rule `Edit(~/.docket/**)` in user
   settings when the work dir is the home default, and the judge hook from `templates/judge-hook.json` into the
   project's `.claude/settings.json` (the judge is a per-project opt-in, so it never runs in projects that did not ask). Existing CLAUDE.md, AGENTS.md or CONTRIBUTING.md receive the
   decisions where they already treat the topic, otherwise as a marked section; no existing sentence changes before
   the user decided the contradiction. Missing files of those three are created only on explicit wish.
   Without a check script that exits non-zero on failure and prints one result line, the diff adds
   `${CLAUDE_PLUGIN_ROOT}/templates/check.py` as `scripts/check.py` with the project's commands filled in, and
   registers it as `check`. When the work dir is the home default, the hooks keep it under its own git history
   (`git init` on the first LOG line); for `docket/` inside the repo or a second repo, the diff says which.
   New repo: `git init`, remote (existing / create via `gh repo create` / none), all eleven questions.
   Questions 1, 2, 10 and 11 also become the configuration keys `push_branch`, `commit_regex`, `forbidden_paths`
   and `forbidden_commands` (regexes and globs the hooks enforce); a pipe inside a regex is written `\|`.
5. Show the diff and ask for the go. The hook refuses every write to DOCKET.md, CONTRIBUTING.md, CLAUDE.md,
   AGENTS.md, .gitignore and settings files until the user answers with the one word `go` (or ok, yes, ja); that
   answer is logged as `init-approved`. Then write, one LOG line. Re-runs show only findings whose fingerprint moved.

## `stop`

In a sealed run: the hook records the request; the orchestrator ends at the next checkpoint with
`DOCKET BLOCKED <id>` and the reason "stopped by user". Outside a sealed run: say in one sentence that nothing runs.

## `status`

One line: mode, spec, open points of the current docket, last LOG line.

## Every message

Ends with the line `stamp: YYYY-MM-DD HH:MM`, the time fetched from the clock at the moment of writing, in every
session of a project with a DOCKET.md, command or not; the hook refuses a message without it or with a stale one.
When the hook sends you back for the stamp, answer with the stamp line alone, never repeat the message.
