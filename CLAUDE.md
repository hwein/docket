# Sessions in this repo

Analysing a session (a transcript, judge verdicts, a violations file, hook output of another session) is
done by a subagent (Explore or general-purpose) that returns findings with entry numbers. The main context
never holds the judge prompt or another session's rule text verbatim: 83 % rule text in the context made a
session argue in the judge's terms where no judge ran.

Editing the hooks, commands and templates is development, not analysis: read those files directly.
