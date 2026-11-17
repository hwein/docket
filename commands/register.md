---
description: "The docket register: every concept, spec and docket with title, topic, state and dependencies, plus the last LOG lines."
---

# /docket:register

Render the register from the files, never from memory: every concept, spec and docket under the work dir with
title, topic, state and dependencies (`concept:`, `follows:`, `after`), per concept its `source:` where set, plus the
last ten LOG lines in words. Group by topic. A spec whose Preconditions section ends in `checked: … not met` is
shown as waiting for that precondition, and a spec that comes after it as waiting for that spec. Dependencies say
which spec comes before which, nothing more: state no order for specs no dependency ties, never present the
register as a roadmap and make no promises about what comes next. Ids are shown in the table only.

After the concepts, a list "not registered", when `concept_roots` in DOCKET.md is set: the files ending in `.md`,
`.mdx`, `.rst` or `.adoc` under those folders (an entry's part in parentheses is its format, not a path), searched
recursively without hidden folders and without docket's work dir, leaving out names that start with `README` or
`INDEX` or contain `template` (case ignored), whose file no concept's `source:` names and no `reference_roots` entry
names, itself or a folder at or below the concept folder it lies in (`docs/` over `docs/rfcs/` names none of them);
each with its path relative to the repository root. The pane counts the same files. `/docket:concept` takes them
in. With `concept_roots` empty, or nothing unregistered, the list is left out.
