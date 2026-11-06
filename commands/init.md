---
description: "Project setup: audits the repo, interviews you only where it is silent, writes DOCKET.md after your go."
---

# /docket:init

Runs any time, never required. The repo is the first interview partner, the user the second: the user decides
only where the repo is silent or contradicts itself. Nothing is written before the diff is approved.

1. Findings, with a source for each (file, command, number), for the twelve questions:
   branch model (`git branch -a`, default branch, branch protection); commit form (last 200 messages: convention,
   language, ticket numbers); way into the target branch (merged PRs vs direct commits, squash vs merge commits);
   who merges; gate before merge (workflows on pull_request, check scripts); release (tags, release workflow,
   CHANGELOG, version field); storage of specs and dockets (existing spec folders, external repos in CLAUDE.md);
   language of the repo (code, comments, README, issues); commenting (comment lines per code line per language: sparse < 5 %,
   normal, verbose > 15 %; existing section markers); forbidden paths and content (.gitignore, guard hooks,
   CLAUDE.md); external systems an agent may touch (permissions in settings, CLAUDE.md rules); language of every
   message to the user, in every mode, which is a question of its own because it is not the language of the repo
   (an English repo, a German author: luradb-web-client 2026-11-06) and is answered from the language the user
   writes in this session.
   Also: existing CLAUDE.md, AGENTS.md, CONTRIBUTING.md, hooks, commands, skills, permissions; the commands that
   make up the project's own check (CI workflow, package scripts, Makefile, CONTRIBUTING), as many as it has. For
   every existing hook, command or skill, say what it does in one line and
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
   project's `.claude/settings.json` (the judge is a per-project opt-in, so it never runs in projects that did not ask;
   once there, session start keeps the entry equal to the plugin's template, so a plugin update reaches it). Existing CLAUDE.md, AGENTS.md or CONTRIBUTING.md receive the
   decisions where they already treat the topic, otherwise as a marked section; no existing sentence changes before
   the user decided the contradiction. Missing files of those three are created only on explicit wish.
   The `check` row lists those commands, each in backticks, in the order the project runs them, with their source
   (`npm run check`, `npm test` from `.github/workflows/ci.yml:19-20`); docket adds no file of its own to the repo.
   When the work dir is the home default, the hooks keep it under its own git history
   (`git init` on the first LOG line); for `docket/` inside the repo or a second repo, the diff says which.
   New repo: `git init`, remote (existing / create via `gh repo create` / none), all twelve questions.
   Questions 1, 2, 10 and 11 also become the configuration keys `push_branch`, `commit_regex`, `forbidden_paths`
   and `forbidden_commands` (regexes and globs the hooks enforce); a pipe inside a regex is written `\|`.
   Question 12 becomes `language`: the language of every message to the user in every mode; the hooks write
   their own lines to the user (status line of a sealed run, closing line) in it and refuse a message in another
   language.
5. Show the diff and ask for the go. The hook refuses every write to DOCKET.md, CONTRIBUTING.md, CLAUDE.md,
   AGENTS.md, .gitignore and settings files until the user answers with the one word `go` (or ok, yes, ja); that
   answer is logged as `init-approved`. Then write, one LOG line. Re-runs show only findings whose fingerprint moved.

## Every message

Ends with the line `stamp: YYYY-MM-DD HH:MM`, the time of the latest `[docket] clock:` line the hooks give with
every prompt and tool result, or fetched from the clock at the moment of writing, in every
session of a project with a DOCKET.md, command or not; the hook refuses a message without it or with a stale one.
When the hook sends you back for the stamp, answer with the stamp line alone, never repeat the message.
