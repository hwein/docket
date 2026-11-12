---
description: "Ends a run at the next checkpoint; the run's closing message says what was done and how to resume."
---

# /docket:stop

In a run: docket records the request (`stop_requested`, LOG `stop-requested`), and the series ends with the spec at
work. End that spec at its next checkpoint, after the round at work, with `state: BLOCKED: stopped by user` in its
docket's Terminal state; the closing message then names what was delivered and the command that resumes the rest.
Outside a run: say in one sentence that nothing runs.
