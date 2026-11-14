# Closing message

The one message of a run to the user, written by the orchestrator after docket's statement at the end of the
series, from the dockets. Every word in the user's language (DOCKET.md `user_language`), the labels below included;
everyday words. It says whether each spec was implemented as ordered, and what the user has to decide or look at: no
plan, no account of the work, no proof of it. The proof is the docket.

```
<one sentence on the run: how many specs implemented as ordered, with deviations, not implemented; how many items
 wait for the user>                                         (only for a series of two or more specs)
<title>: implemented as ordered.
<title>: implemented with deviations:
- <what differs from the order, in everyday words, and what it means for the user>
<title>: not implemented: <the reason in everyday words>
<title>: not started: <the reason>

For you:
- <a decision or a step only the user can take, done from this line alone> (<title>)
Open:
- <a finding or a proposal, one sentence>. Fault: yes | no | unclear. Recommendation: <do it | drop it | measure
  first: how>. (<title>)

Resume: `/docket:implement <ids>`
Records: `<command>`
```

- Each spec gets the first form that fits, in this order: not started, not implemented (BLOCKED), with deviations, as
  ordered. A deviation is a `[~]` line, a command that was not green, a red run even where a repeat was green, a point
  not checked. A spec without a deviation is its one line.
- Left out, also for a deviation: what the spec ordered and was done, how many commands ran, the check, test counts,
  CI, commits, a count of review findings.
- A red run names the test by its title and says whether its cause is known.
- `For you:` holds every `user:` line, every `[~]` line that waits for the user and every command from
  `forbidden_commands` that did not run. `Open:` holds every review finding the docket keeps as `reported` and every
  `decision:` line, each written out with the fault and the recommendation the docket gives it. Never a count
  instead of the item, never a path to it.
- Across the series: one item from several specs is one line with all their titles; an item a later spec of the
  series settled is left out (its docket and diff show it). `(<title>)` stands only in a series of two or more.
- Not in the message: line numbers, file paths, code, hashes, run ids, spec and docket ids, links, "point n",
  "spec n", role names (implementer, fixer, reviewer) and severity labels. A word that stands in the spec itself may
  stand here (a file name the spec got wrong). Commands stand only where the user runs them: `Resume:`, `Records:`
  and an item under `For you:`.
- A part with nothing in it is left out. `Resume:` lists the specs not started, the one ended `stopped by user` and
  each blocked spec one of them waits for, in series order; `Records:` stands only when docket's statement names one
  or two commands for the records.
