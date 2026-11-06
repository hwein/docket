---
description: "The docket register: every concept, spec and docket with title, topic, state and dependencies, plus the last LOG lines."
---

# /docket:register

Render the register from the files, never from memory: every concept, spec and docket under the work dir with
title, topic, state and dependencies (`concept:`, `follows:`, `after`), plus the last ten LOG lines in words.
Group by topic. Dependencies give a rough order, nothing more: never present the register as a roadmap and make
no promises about what comes next. Ids are shown in the table only.

## Every message

Ends with the line `stamp: YYYY-MM-DD HH:MM`, the time of the latest `[docket] clock:` line the hooks give with
every prompt and tool result, or fetched from the clock at the moment of writing, in every
session of a project with a DOCKET.md, command or not; the hook refuses a message without it or with a stale one.
When the hook sends you back for the stamp, answer with the stamp line alone, never repeat the message.
