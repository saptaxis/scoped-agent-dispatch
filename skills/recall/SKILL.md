---
name: recall
description: >
  Catch up on a project before starting work — durable notes first, then
  corroborated against the live repo. Use when the user says recall this
  project, catch me up, what happened here, where did we leave off, is there
  context, or asks you to pick up previous work. Reads the newest note, then
  checks git history, planning docs, and the code the note points at. Takes an
  optional project name; otherwise resolves the project from the current
  directory.
---

You are picking up work someone — possibly you, in an earlier session — already
did. Two sources tell you about it, and they are not equals:

- **Notes** say *what was decided and why* — intent, rejections, traps. Nothing
  else records this. But a note is a photograph: true when taken.
- **The repo** says *what is true now* — commits, code, tests, planning docs.
  It cannot tell you why, and its prose files drift.

So: **the note tells you where to look; the repo tells you what still holds.**
Report neither on its own.

**Read the least you can get away with.** Metadata is cheap and bodies are not.
Corroboration is bounded by what the note names — never by the size of the
repo. You are checking a short list of claims, not auditing a codebase.

## Part 1 — Notes

**1. See what exists — metadata only, no bodies.**

```
scad notes ls --project <name> --limit 10
```

If no project was named, `scad where` announces the one for this directory.
Every row gives you `session_id`, `[idx]`, when, `topic`, `relation`, and the
title. That is usually enough to know which note matters.

**2. Read the newest note in full.**

```
scad notes read <session-id> --last
```

**3. Backtrack only while the thread continues.**

The `relation` field on each row is the stopping rule, and it is why you do not
need to read everything:

- **`continue`** — the note before it is the same thread. Worth reading if the
  newest one references it or leaves you short.
- **`shift`** — a new, unrelated topic begins here. **Stop.** Anything older
  belongs to different work.
- **`branch` / `return`** — the note names a `parent` topic. Read that one
  *specifically*, by finding its row in step 1 and reading its index:

```
scad notes read <session-id> --idx <n>
```

Go back one note at a time and re-ask whether you have enough. Reading a third
note should feel like a decision, not a default.

## Part 2 — Corroborate against the world

Do this **every time**, even when the note reads as complete. It is cheap and
it is the half that catches drift. Work down until the note's claims are
grounded; stop when they are.

**4. How stale is the note?** One command, and it sets how hard to look:

```
git log --oneline --since=<note date>
```

Zero commits — trust the note's mechanics and skim the rest. Many commits —
assume its "next steps" list has been partly overtaken, and read the subject
lines before anything else. Also check the working tree is clean and note the
current branch and HEAD against whatever the note recorded.

**5. Check the `invalidation` clause literally.** The note names the
circumstance that would make it wrong. That is a *test*, not a caveat — run it.
If it names a file, `ls` the file. If it names a behaviour, check the behaviour.

**6. Verify what the note calls "next", one item at a time.** This is where
recall earns its cost. For each item the note proposes as the next work, spend
one cheap check on whether it is already built, partly built, or built
elsewhere under another name:

- `<cli> --help` or the equivalent entry point, for anything user-facing
- a targeted `grep` for the function, flag, column, or config key it names
- the file:line the note cites — confirm it still says what the note claims

Items collapse under this routinely: "build X" becomes "X exists but has no
CLI verb", or "needs a design decision" becomes "the data is already on disk,
unread". Both change what to do next.

**7. Read the project's own planning doc if the note names one** — a backlog,
a spec, a roadmap. It is usually the widest view of open work, and it is worth
reading in full where the notes are narrow.

Treat it as **another claim to verify, never as truth**. A canonical file that
ships work faster than it is edited is wrong in the specific direction that
matters: it lists as open things that are done. Date it against `git log`,
and check its concrete rows the way step 6 says.

**8. Resolve conflicts toward the code.** When note, planning doc, and code
disagree, the code wins, then the note (it at least records intent), then the
doc. Say plainly which claims moved and why — a corrected claim is the most
valuable thing recall produces.

## Part 3 — Report

**9. Say what you found, briefly.** Where the work stands, what the next step
is, and anything flagged untrustworthy or already rejected.

**Mark provenance on anything load-bearing** — whether you verified it in code
this session or are relaying a document's claim. The human is deciding what to
build; "the backlog says this is unbuilt" and "I read the source and it is
unbuilt" support very different decisions.

Then ask whether to proceed. Do not start work off the back of a catch-up
without the human confirming it is still what they want.

## What to carry forward, specifically

Notes written by the `remember` skill use a fixed shape. Pay attention to:

- **Rejected** items with their reversal conditions. These exist so you do not
  rebuild something already ruled out. Treat a rejection as binding unless its
  stated condition has since become true — and that condition is checkable, so
  check it rather than assuming either way.
- **`invalidation`** — see step 5. Run it.
- **Open** items — usually the actual next work, and the input to step 6.
- **Measured numbers** (test counts, row counts, timings). These date fastest
  and are the cheapest thing to re-measure. Never repeat one as current
  without re-running it.

## What this skill does NOT do

- **Do not read every note.** If you find yourself opening a fourth, stop and
  ask the human what they are after.
- **Do not skip Part 2 because the note is thorough.** Thoroughness is not
  currency. The most confident note is the one whose staleness costs most.
- **Do not let corroboration become exploration.** You are checking a bounded
  list drawn from the note. Reading files it never mentions means you have
  stopped recalling and started surveying — ask the human instead.
- **Do not search notes for content.** `scad search --notes` matches
  `topic` / `title` / `tags` / `entities` only — never the body — so it answers
  "which note mentions X", not "what did we decide about X". Use it to
  *locate*, then read. Ordinary code search has no such limit; use it freely
  within the bound above.
- **Do not treat any document as current.** Notes, backlogs, handoffs, and
  READMEs all record a past. Only the code and the git history are now.
- **Do not resolve projects yourself.** `scad where` answers that, and the
  project of a note comes from the session that wrote it.
