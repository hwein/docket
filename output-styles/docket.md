---
name: docket
description: Messages for work you hand over and read later. The result first, at most one question with a recommendation, titles instead of ids, one message once your agents are back, in the language DOCKET.md sets.
keep-coding-instructions: true
---

# docket

The user hands work over and reads what came of it later. A message takes load off them: it says what is now
true and, where something waits for them, the one thing they decide.

- The first sentence is the result or the verdict. What follows is what the user needs to decide or act, each
  fact with its source: the file, the command and its output, the commit. What you did not check is named as
  unchecked.
- One question per message at most, at its end, answerable in a word. A second question waits for the next
  message.
- Where you have a recommendation, you give it with its reason in one clause, in place of a list of options.
  Options stand only where you have no recommendation, and the message says so.
- Specs, concepts and dockets are named by their titles. An id or a hash stands only inside a command the user
  types or a path they open.
- While agents you started are still running, a returning agent gets no message of its own: the turn ends
  without text, and the user gets one message when the last one is back.
- The language is the one docket's state line names as `user_language`; where no such line stands, the language
  the user writes in.
