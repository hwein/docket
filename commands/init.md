---
description: "Project setup: audits the repo, interviews you only where it is silent, writes DOCKET.md after your go."
---

# /docket:init

Runs any time, never required. The repo is the first interview partner, the user the second: the user decides
only where the repo is silent or contradicts itself. Nothing is written before the diff is approved, because the
diff changes files the project owns.

1. Findings, with a source for each (file, command, number), for the twelve questions:
   branch model (`git branch -a`, default branch, branch protection); commit form (last 200 messages: convention,
   language, ticket numbers); way into the target branch (merged PRs vs direct commits, squash vs merge commits);
   who merges; gate before merge (workflows on pull_request, check scripts); release (tags, release workflow,
   CHANGELOG, version field); storage of specs and dockets (existing spec folders, external repos in CLAUDE.md);
   language of the repo (code, comments, README, issues); commenting (comment lines per code line per language: sparse < 5 %,
   normal, verbose > 15 %; existing section markers); forbidden paths and content (.gitignore, guard hooks,
   CLAUDE.md); external systems an agent may touch (permissions in settings, CLAUDE.md rules); language of every
   message to the user, in every mode, which is a question of its own because it is not the language of the repo
   (an English repo, a German author: luradb-web-client 2026-11-06); its finding is the language the user writes
   in this session.
   Also: existing CLAUDE.md, AGENTS.md, CONTRIBUTING.md, hooks, commands, skills, permissions; the commands that
   make up the project's own check (CI workflow, package scripts, Makefile, CONTRIBUTING), as many as it has. For
   every existing hook, command or skill, say what it does in one line and
   whether docket does the same: docket stops a write into `forbidden_paths`, a push outside `push_branch` and a
   commit against `commit_regex`, and runs a command from `forbidden_commands` only after the user's click (in a
   run, not at all). A duplicate gets one card, replace (project hook disabled in the diff) or keep (both run).
2. Compare findings with each other and with the framework rules. Three classes: unambiguous (one finding, no
   objection), ambiguous or contradictory, silent.
3. Interview: unambiguous findings are listed once ("adopted, source") and confirmed in one go at the end.
   Ambiguous and silent questions get one card each (AskUserQuestion): both findings with source, the effect in
   one sentence, the recommendation first, answerable with one word. A card never asks two things and never gives
   the user a task. Questions 8 and 12 are told together: the finding on the repo's language is followed at once
   by the language of the messages to the user (a card when the user's own language differs from the repo's or is
   not evident), never separated by other questions.
4. Diff: `DOCKET.md` in the work dir (configuration, repository behaviour or pointers to where the project already
   answers, deviations, audit fingerprints), `.gitignore` lines, the permission rule `Edit(~/.docket/**)` in user
   settings when the work dir is the home default. Existing CLAUDE.md, AGENTS.md or CONTRIBUTING.md receive the
   decisions where they already treat the topic, otherwise as a marked section; no existing sentence changes before
   the user decided the contradiction. Missing files of those three are created only on explicit wish.
   The `check` row lists those commands, each in backticks, in the order the project runs them, with their source
   (`npm run check`, `npm test` from `.github/workflows/ci.yml:19-20`); docket adds no file of its own to the repo.
   When the work dir is the home default, docket keeps it under its own git history
   (`git init` on the first LOG line); for `docket/` inside the repo or a second repo, the diff says which.
   New repo: `git init`, remote (existing / create via `gh repo create` / none), all twelve questions.
   Questions 1, 2, 10 and 11 also become the configuration keys `push_branch`, `commit_regex`, `forbidden_paths`
   and `forbidden_commands` (regexes and globs docket enforces); a pipe inside a regex is written `\|`.
   Question 12 becomes `user_language`: the language of every message to the user in every mode; docket writes its
   own lines (the state lines, the gate) in it.
   `model_ladder` is one card with its default first: fixer haiku; implementer round 1 sonnet, round 2 opus, round 3
   the orchestrator's model (the one the user starts a run on; no agent runs above it). The themes that need opus
   from round 1 are a finding where the project names them (with its source), otherwise a card of their own with
   "none" first. The answer is the row as the template writes it.
   The plugin ships the output style `docket:docket` (result first, one question with a recommendation, titles
   instead of ids, one message when the agents are back); name it once as a choice the user makes in `/config`,
   because a style docket set would override the one the user chose. The diff does not set it.
5. Show the diff and ask for the go. Write DOCKET.md, CONTRIBUTING.md, CLAUDE.md, AGENTS.md, .gitignore and settings
   files only after the user answered with the one word `go` (or ok, yes, ja), because they are the project's files
   (see above). Then write. Re-runs show only findings whose fingerprint moved.
