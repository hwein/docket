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
   CHANGELOG, version field); storage of specs, dockets and concepts (existing spec folders, external repos in
   CLAUDE.md, the project's concept documents, see below);
   language of the repo (code, comments, README, issues); commenting (comment lines per code line per language: sparse < 5 %,
   normal, verbose > 15 %; existing section markers); forbidden paths and content (.gitignore, guard hooks,
   CLAUDE.md); external systems an agent may touch (permissions in settings, CLAUDE.md rules); language of every
   message to the user, in every mode, which is a question of its own because it is not the language of the repo
   (an English repo, a German author); its finding is the language the user writes
   in this session.
   The concept documents of question 7 are what the project already decided or plans: ADR folders (`docs/adr`,
   `doc/adr`, `adr/`, `decisions/`, `docs/decisions`), RFCs (`rfcs/`, `docs/rfc*`), design docs (`docs/design`,
   `design/`), `PRD*`, `ROADMAP*`, files and folders whose name contains concept, konzept, proposal or plan, spec
   folders without docket frontmatter, and references to a concept repository in CLAUDE.md or AGENTS.md. For each
   document: path, title, last commit (`git log -1 --format=%cs -- <path>`), number of authors
   (`git shortlog -s HEAD -- <path>`, one line per author), and its class (step 2) with the source that decides it.
   Also: existing CLAUDE.md, AGENTS.md, CONTRIBUTING.md, hooks, commands, skills, permissions; the commands that
   make up the project's own check (CI workflow, package scripts, Makefile, CONTRIBUTING), as many as it has. For
   every existing hook, command or skill, say what it does in one line and
   whether docket does the same: docket stops a write into `forbidden_paths`, a push outside `push_branch` and a
   commit against `commit_regex`, and runs a command from `forbidden_commands` only after the user's click (in a
   run, not at all). A duplicate gets one card, replace (project hook disabled in the diff) or keep (both run).
2. Compare findings with each other and with the framework rules. Three classes: unambiguous (one finding, no
   objection), ambiguous or contradictory, silent.
   Each concept document also gets one of three document classes, each with its source (a status line in the
   document, its folder, its text): it describes what IS, then it goes into `reference_roots`; it is an open SHOULD,
   not yet implemented, then it becomes a docket reference concept (step 4); it records a decision already taken
   (an ADR), then it goes into `reference_roots`, because a taken decision is part of what IS. A document whose
   source does not settle its class is ambiguous.
   `concept_roots`, where new concepts are written after init, follows the concept folders found: none, then it
   stays empty and nothing is asked; exactly one with a commit in the last 12 months
   (`git log -1 --format=%cs -- <folder>`), then it is unambiguous; one without such a commit, then it is ambiguous
   with the docket work dir (empty `concept_roots`) as recommendation, because a folder nobody writes in is no
   place for new concepts; several, then it is ambiguous with the folder of the most recent commit as
   recommendation.
3. Interview: unambiguous findings are listed once ("adopted, source") and confirmed in one go at the end.
   Ambiguous and silent questions get one card each (AskUserQuestion): both findings with source, the effect in
   one sentence, the recommendation first, answerable with one word. A card never asks two things and never gives
   the user a task. Questions 8 and 12 are told together: the finding on the repo's language is followed at once
   by the language of the messages to the user (a card when the user's own language differs from the repo's or is
   not evident), never separated by other questions.
   Concept documents: each unambiguous one stands once in that list (path, class, topic, source), and so does an
   adopted concept folder (with its last commit date and number of authors); each ambiguous document gets one card
   (its class, recommendation first, answerable with one word), and an ambiguous `concept_roots` gets one card by
   the folder rule of step 2.
4. Diff: `DOCKET.md` in the work dir (configuration, repository behaviour or pointers to where the project already
   answers, deviations, audit fingerprints), `.gitignore` lines, the permission rule `Edit(~/.docket/**)` in user
   settings when the work dir is the home default, and the pointer `.claude/docket.json` in the repo root with
   `{"work_dir": "<path relative to the repo root>"}` when the work dir is neither the home default nor `docket/`
   in the repo root, because the hooks and the pane find only those two without it: a work dir in a second
   repo without the pointer reads as not set up. Existing CLAUDE.md, AGENTS.md or CONTRIBUTING.md receive the
   decisions where they already treat the topic, otherwise as a marked section; no existing sentence changes before
   the user decided the contradiction. Missing files of those three are created only on explicit wish.
   The `check` row lists those commands, each in backticks, in the order the project runs them, with their source
   (`npm run check`, `npm test` from `.github/workflows/ci.yml:19-20`); docket adds no file of its own to the repo.
   For the concept documents the diff holds: per open SHOULD a reference concept `concepts/<topic>/c-<id>-<slug>.md`
   in the work dir from `${CLAUDE_PLUGIN_ROOT}/templates/concept.md`, with `state: agreed` and `source:` the
   document's path relative to the repo root (or absolute), Intent, Principles and constraints and Shape left empty
   because the source holds them (id: `c-` plus eight hex digits from SHA-256 over topic, title and creation time);
   the additions to `reference_roots`; the row `concept_roots` (each folder with its format, e.g.
   `docs/rfcs/ (RFC, template docs/rfcs/0000-template.md)`, or empty); and one Audit row per concept folder whose
   fingerprint is the first 8 hex digits of SHA-256 over the sorted repo-relative names of the files in it (POSIX
   slashes, one per line), so a re-run reports the documents added since. init links the project's concept
   documents by reference and never changes, moves or converts one: they stay the project's files and the source
   of their content, so none of them is in the diff.
   When the work dir is the home default, docket keeps it under its own git history
   (`git init` on the first LOG line); for `docket/` inside the repo or a second repo, the diff says which, and that docket commits its records there at
   the end of a run and pushes that commit (`commit_records: no` leaves both to the user).
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
5. Show the diff and ask for the go. Write DOCKET.md, the pointer, the reference concepts, CONTRIBUTING.md, CLAUDE.md,
   AGENTS.md, .gitignore and settings files only after the user answered with the one word `go` (or ok, yes, ja),
   because they are the project's files (see above). Then write. Re-runs show only findings whose fingerprint moved.
6. After the write, ask docket where it looks: `python "${CLAUDE_PLUGIN_ROOT}/hooks/docket_ui.py" overview --root
   <repo root>`. `"initialized": true` with `"work"` equal to the work dir of the diff says the hooks and the pane
   find DOCKET.md. Any other answer is a blocker: report the command, its answer and the folder docket looks in
   instead, because a project docket does not find gets no pane, no state line and no run.
7. Last, when every file is written and the document situation is final: check which context paths git ignores,
   because Claude Code's Grep searched from the repo root skips every path `.gitignore` excludes, while Read, Glob
   and a Grep whose `path` lies inside that folder find it. Context paths: the work dir, every `reference_roots` and
   `concept_roots` entry, CLAUDE.md, AGENTS.md, `.claude/CLAUDE.md`, CONTRIBUTING.md
   (`git check-ignore -v <path>`). A work dir outside the repo is no context path here: no search from the repo root
   reaches it. None ignored: nothing is said. Otherwise read an existing `.rgignore` or `.ignore` in the repo root
   and propose, as one card with the recommendation first, the missing lines of a `.rgignore` in the repo root: one
   line `!<folder>/` per ignored folder and `!<file>` per ignored file, never `<folder>/**`, which does not lift the
   exclusion; and the line `.rgignore` in `.gitignore`, so git keeps ignoring what it ignored. Write both only after
   the user's go. Then Grep from the repo root, per line, for a word that occurs only under that path and report each
   line as found or not found; a line not found is reported with its command and result.
