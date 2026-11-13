# Closing message

The one message of a run to the user, written by the orchestrator after docket's statement at the end of the
series, from the dockets. In the user's language (DOCKET.md `user_language`), everyday words, no plan or account of
the work. One block per spec of the series, in its order; a spec that did not start gets its line from docket's
statement. Leave out a part that has nothing in it.

```
<title of the spec>: <delivered | blocked: the reason | not started: the reason>
<what works now, one sentence (the docket's `result:` line)>
Checked: <n> acceptance commands, `<check command>`, CI: green   (one line for all that is green)
- `<command>` → <its result>                     (a line of its own only for a command that is not green)
Deviations:
- point <n>: <what differs from the spec and why>   (each `[~]` line)
Unchecked:
- <what the run did not check, and why>
For you:
- <each thing only you can decide or do: what, and where>
- `<command>` was not run: it is on the list forbidden_commands in DOCKET.md   (one line per such command)
Left open: <n> review findings, <m> proposals, in <path of the docket>

<the next spec's block>

Resume: `/docket:implement <ids>`   (the specs not started, the one ended `stopped by user` and each blocked spec
                                     one of them waits for, in series order)
Records: `<command>`                (only when docket's statement names one or two for the records)
```

The first line of each block says the result. The message is short: a delivered spec without deviations is its
first two lines and the `Checked:` line. Each thing stands once. A review finding that was not fixed and a
`decision:` proposal are counted in `Left open:`, not listed: the docket holds each with its source. `For you:`
holds only what the user alone can decide or do, each done from that line alone; without such a thing the part is
left out. Every command counted under `Checked:` the orchestrator ran itself, with the result it saw.
