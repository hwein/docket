# DOCKET

Project configuration and project decisions for the docket workflow.
Framework rules live in the plugin, not here. This file holds only what this project decided.

## Configuration

| Key | Value | Source |
|---|---|---|
| work_dir | `<path>` | /docket:init |
| topics | `<topic1>, <topic2>` | folders under specs/ |
| check | `<first command of the project's check>` `<further commands, as many as the project has>` | <where the project runs them, e.g. the CI workflow> |
| reference_roots | `<docs/, README.md, ...>` (what IS; specs say what SHOULD) | |
| model_ladder | fixer (small exact changes): haiku · implementer round 1: sonnet; complex from round 1 with opus: <the themes of this project that need it, or "none"> · round 2: opus · round 3: the orchestrator's model | |
| third_refiner_angle | `<the project-specific angle, e.g. "operator impact", "game feel", "data migration">` | |
| user_language | `<language of every message to the user, e.g. "Deutsch" or "English"; not the repo's language; docket's own lines follow it>` | question 12 |
| ci | `<how CI is waited for and read, or "none">` | |
| commit_records | `<unset: where the records lie inside another repository, docket commits them at the end of a run (only their folder) and pushes that commit where the branch has an upstream; "no": the closing message names the two commands>` | question 7 |
| push_branch | `<regex the pushed branch must match, e.g. "feature/.*" or "main">` | question 1 |
| commit_regex | `<regex for the first line of a commit message; escape pipes as \|>` | question 2 |
| forbidden_paths | `<comma-separated globs no agent writes, e.g. "secrets/**, .env">` | question 10 |
| forbidden_commands | `<regex of shell commands, e.g. "gh workflow run\|terraform apply": outside a run docket asks the user in a dialog; in a run docket does not run the command, and the closing message names it>` | question 11 |

## Repository behaviour

Answers to the twelve questions, or a pointer to the project file that already answers them
(CONTRIBUTING.md, CLAUDE.md section ...).

1. Branch model:
2. Commit form:
3. Way into the target branch:
4. Who merges:
5. Gate before merge:
6. Release:
7. Storage of specs and dockets:
8. Language of the repo (code, comments, docs, commits):
9. Commenting:
10. Forbidden paths and content:
11. External systems an agent may touch without approval:
12. Language of every message to the user (key `user_language`; the author's language, not the repo's):

## Deviations

Project rules that replace a framework rule ("project wins"), each with its source.

| Framework rule | Project rule | Source |
|---|---|---|

## Audit

Fingerprints of the rules /docket:init has seen, so a re-run shows only what moved.

| File | Rule (first words) | Fingerprint | Decision |
|---|---|---|---|
