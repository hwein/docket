---
description: "Runs the preconditions of ready specs outside a run and records per spec whether they are met. A spec whose last check says not met starts no run, and neither does a spec that comes after it."
---

# /docket:precheck

A precondition is what only the user can provide (a key, a running server, an account). Whether it holds changes
without docket: this command asks the world again and writes down what it found.

Which specs: the spec ids of the arguments; without arguments every spec in state `ready` whose
`Preconditions (user only)` section names a command. A spec whose section says `none` has nothing to check: say so
in one line and write nothing. In a session with the run mark this command checks nothing: the run runs the
preconditions of its specs itself.

Per spec:

1. Run each line of the section in the shell it is written for (PowerShell syntax in the PowerShell tool, any other
   line in Bash). The text behind the command is a description: met when the command starts and its output matches
   it in sense ("or" and parentheses are alternatives); the exit code decides only where the line names it. A
   command on `forbidden_commands` runs after the user's click (docket puts it to the user as a dialog); a dialog
   the user declines leaves that line unchecked, and the spec keeps the verdict it had.
2. Write the verdict as the last bullet of that section, in place of an older verdict, and change nothing else in
   the spec:
   - `- checked: YYYY-MM-DD met` when every line is met;
   - `- checked: YYYY-MM-DD not met: <the command, and in a few words what its output said instead>` otherwise,
     with the first line that is not met.

docket reads that bullet: the pane shows it at the spec, `/docket:implement` marks no run for a spec whose last
verdict is `not met` or that comes after such a spec (`follows:`, `after:`), and the LOG gets the line
`preconditions`. `met` promises nothing: the run runs the preconditions again as its first step.

Then one message in the user's language, per spec by title, no ids: met, or not met with the command, an excerpt of
its output and what the user would have to provide. Fixing what is missing is not part of this command.
