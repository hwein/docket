---
description: "Ends a sealed run at the next checkpoint; the run's closing message says what was done and how to resume."
---

# /docket:stop

In a sealed run: the hook records the request. The orchestrator ends the spec at the next checkpoint in its docket
(`state: BLOCKED: stopped by user` in the Terminal state section) and ends the turn without text; the hook then
renders the run's closing message, which names what was delivered and the command that resumes the rest. Outside a
sealed run: say in one sentence that nothing runs.

## Every message outside a sealed run

Ends with the line `stamp: YYYY-MM-DD HH:MM`, the time of the latest `[docket] clock:` line the hooks give with
every prompt and tool result, or fetched from the clock at the moment of writing; the hook refuses a message
without it or with a stale one. When the hook sends you back for the stamp, answer with the stamp line alone, never
repeat the message.
