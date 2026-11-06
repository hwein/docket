---
description: "Ends a sealed run at the next checkpoint with DOCKET BLOCKED, reason \"stopped by user\"."
---

# /docket:stop

In a sealed run: the hook records the request; the orchestrator ends at the next checkpoint with
`DOCKET BLOCKED <id>` and the reason "stopped by user". Outside a sealed run: say in one sentence that nothing runs.

## Every message

Ends with the line `stamp: YYYY-MM-DD HH:MM`, the time of the latest `[docket] clock:` line the hooks give with
every prompt and tool result, or fetched from the clock at the moment of writing, in every
session of a project with a DOCKET.md, command or not; the hook refuses a message without it or with a stale one.
When the hook sends you back for the stamp, answer with the stamp line alone, never repeat the message.
