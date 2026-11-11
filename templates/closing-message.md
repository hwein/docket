# Closing message

The one message of a run to the user, written by the orchestrator after docket's statement at the end of the
series, from the dockets. In the user's language (DOCKET.md `user_language`), everyday words, no plan or account of
the work. One block per spec of the series, in its order; a spec that did not start gets its line from docket's
statement. Leave out a part that has nothing in it.

```
<title of the spec>: <delivered | blocked: the reason | not started: the reason>
<what works now, one sentence (the docket's `result:` line)>
Checked:
- `<acceptance command>` → <its result>        (one line per point, from the docket's Evidence)
- `<check command>` → <its result>
- CI: <state>                                  (when DOCKET.md sets `ci`)
Deviations:
- point <n>: <what differs from the spec and why>   (each `[~]` line)
- <review finding not fixed, with its severity>
Unchecked:
- <what the run did not check, and why>
For you:
- <each thing only you can decide or do: what, and where>
- `<command>` was not run: it is on the list forbidden_commands in DOCKET.md   (one line per such command)
- Proposal: <what to do, and why>                (each `decision:` line)

<the next spec's block>

Records: `<first command>`, then `<second command>`   (only when docket's statement names them)
```

The first line of each block says the result. Every line under `Checked:` is a command the orchestrator ran
itself, with the result it saw. Whatever stands under `For you:` can be done from that line alone.
