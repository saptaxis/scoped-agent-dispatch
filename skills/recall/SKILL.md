---
name: recall
description: >
  Catch up on a project from its durable notes before starting work. Use when
  the user says recall this project, catch me up, what happened here, where did
  we leave off, is there context, or asks you to pick up previous work. Reads
  the newest note first and backtracks only as far as the thread goes. Takes an
  optional project name; otherwise resolves the project from the current
  directory.
---

You are picking up work someone — possibly you, in an earlier session — already
did. The durable record of that is scad's notes store, and this skill reads it.

**Read the least you can get away with.** A project can hold dozens of notes.
Catching up should cost what you actually need, not the whole history. Metadata
is cheap and bodies are not, so decide from metadata which bodies to open.

## Steps

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

For most catch-ups this is the whole job. Stop here unless something below
applies.

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

**4. Say what you found, briefly.**

Two or three lines: where the work stands, what the next step is, and anything
the note flags as untrustworthy or already rejected. Then ask whether to
proceed — do not start work off the back of a note without the human confirming
it is still what they want.

## What to carry forward, specifically

Notes written by the `remember` skill use a fixed shape. Pay attention to:

- **Rejected** items with their reversal conditions. These exist so you do not
  rebuild something already ruled out. Treat a rejection as binding unless its
  stated condition has since become true.
- **`invalidation`** — the circumstance that would make the note wrong. Check it
  before trusting the note. A note written when a file existed is not evidence
  about a world where it does not.
- **Open** items — usually the actual next work.

## What this skill does NOT do

- **Do not read every note.** If you find yourself opening a fourth, stop and
  ask the human what they are after.
- **Do not search.** `scad search --notes` matches `topic` / `title` / `tags` /
  `entities` only — never the body — so it answers "which note mentions X",
  not "what did we decide about X". Use it to *locate*, then read.
- **Do not treat a note as current.** It records what was true when written.
  If it names a file, flag, or command, verify that still exists before
  recommending it.
- **Do not resolve projects yourself.** `scad where` answers that, and the
  project of a note comes from the session that wrote it.
