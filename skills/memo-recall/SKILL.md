---
name: memo-recall
description: >
  Catch up on a project before starting work — durable memos first, then
  corroborated against the live repo. Use when the user says recall this
  project, catch me up, what happened here, where did we leave off, is there
  context, or asks you to pick up previous work. Reads the newest memo, then
  checks git history, planning docs, and the code the memo points at. Takes an
  optional project name; otherwise resolves the project from the current
  directory.
---

You are picking up work someone — possibly you, in an earlier session — already did. Two sources tell you about it, and they are not equals:

- **Memos** say *what was decided and why* — intent, rejections, traps. Nothing else records this. But a memo is a photograph: true when taken.
- **The repo** says *what is true now* — commits, code, tests, planning docs. It cannot tell you why, and its prose files drift.

So: **the memo tells you where to look; the repo tells you what still holds.** Report neither on its own.

**Read the least you can get away with.** Metadata is cheap and bodies are not. Corroboration is bounded by what the memo names — never by the size of the repo. You are checking a short list of claims, not auditing a codebase.

## Part 1 — Memos

**1. Ask for a handoff first.** Someone stopping mid-thread marks the memo they left for you, so this is one command and usually the whole answer:

```
scad memos ls --project <name> --kind handoff --limit 5
```

If it returns nothing, widen to everything — metadata only, no bodies:

```
scad memos ls --project <name> --limit 10
```

If no project was named, `scad where` announces the one for this directory. Every row gives you `session_id`, `[idx]`, when, project, `kind`, `relation`, `topic`, and the title. That is usually enough to know which memo matters. `kind` is what the memo *is* — `info` (the ordinary capture), `handoff`, `bug`, `request`, `verification` — and it filters: `--kind bug` is the list of what is known broken here without reading a single body.

**2. Read the newest memo in full** — the newest handoff if there was one, otherwise the newest memo.

```
scad memos read <session-id> --last
```

**Then read what happened after it.** A memo is written at one moment, and the session may have gone on. Read the turns written after it; when the memo is current there are few or none:

```
scad session read <session-id> --kind text --since <the memo's ts>
```

If you are picking up a session that has no handoff memo at all (for instance, you were started with `scad session launch --from`), read its last turns instead, then its memos, then git:

```
scad session read <session-id> --kind text --last 200
```

**3. Backtrack only while the thread continues.**

The `relation` on each row is the stopping rule, and it is why you do not need to read everything. It is *derived*, not something the writer chose: `parent` set means `branch`, a topic seen earlier in that session means `continue`, and anything else is `shift`.

- **`continue`** — the memo before it is the same thread. Worth reading if the newest one references it or leaves you short.
- **`shift`** — a new, unrelated topic begins here. **Stop.** Anything older belongs to different work.
- **`branch`** — the memo names a `parent` topic. Read that one *specifically*, by finding its row in step 1 and reading its index. The parent may be in **another session's** memos, in which case find it by topic in step 1's listing rather than assuming it is in this file:

```
scad memos read <session-id> --idx <n>
```

Go back one memo at a time and re-ask whether you have enough. Reading a third memo should feel like a decision, not a default.

## Part 2 — Corroborate against the world

Do this **every time**, even when the memo reads as complete. It is cheap and it is the half that catches drift. Work down until the memo's claims are grounded; stop when they are.

**4. How stale is the memo?** One command, and it sets how hard to look:

```
git log --oneline --since=<memo date>
```

Zero commits — trust the memo's mechanics and skim the rest. Many commits — assume its "next steps" list has been partly overtaken, and read the subject lines before anything else. Also check the working tree is clean and note the current branch and HEAD against whatever the memo recorded.

**5. Check the `invalidation` clause literally.** The memo names the circumstance that would make it wrong. That is a *test*, not a caveat — run it. If it names a file, `ls` the file. If it names a behaviour, check the behaviour.

**6. Verify what the memo calls "next", one item at a time.** This is where recall earns its cost. For each item the memo proposes as the next work, spend one cheap check on whether it is already built, partly built, or built elsewhere under another name:

- `<cli> --help` or the equivalent entry point, for anything user-facing
- a targeted `grep` for the function, flag, column, or config key it names
- the file:line the memo cites — confirm it still says what the memo claims

Items collapse under this routinely: "build X" becomes "X exists but has no CLI verb", or "needs a design decision" becomes "the data is already on disk, unread". Both change what to do next.

**7. Read the project's own planning doc if the memo names one** — a backlog, a spec, a roadmap. It is usually the widest view of open work, and it is worth reading in full where the memos are narrow.

Treat it as **another claim to verify, never as truth**. A canonical file that ships work faster than it is edited is wrong in the specific direction that matters: it lists as open things that are done. Date it against `git log`, and check its concrete rows the way step 6 says.

**8. Resolve conflicts toward the code.** When memo, planning doc, and code disagree, the code wins, then the memo (it at least records intent), then the doc. Say plainly which claims moved and why — a corrected claim is the most valuable thing recall produces.

## Part 3 — Report

**9. Say what you found, briefly.** Where the work stands, what the next step is, and anything flagged untrustworthy or already rejected.

**Mark provenance on anything load-bearing** — whether you verified it in code this session or are relaying a document's claim. The human is deciding what to build; "the backlog says this is unbuilt" and "I read the source and it is unbuilt" support very different decisions.

Then ask whether to proceed. Do not start work off the back of a catch-up without the human confirming it is still what they want.

## What to carry forward, specifically

Memos written by the `memo-write` and `memo-handoff` skills use a fixed shape. Pay attention to:

- **Rejected** items with their reversal conditions. These exist so you do not rebuild something already ruled out. Treat a rejection as binding unless its stated condition has since become true — and that condition is checkable, so check it rather than assuming either way.
- **`invalidation`** — see step 5. Run it.
- **Open** items — usually the actual next work, and the input to step 6.
- **Measured numbers** (test counts, row counts, timings). These date fastest and are the cheapest thing to re-measure. Never repeat one as current without re-running it.

## What this skill does NOT do

- **Do not read every memo.** If you find yourself opening a fourth, stop and ask the human what they are after.
- **Do not skip Part 2 because the memo is thorough.** Thoroughness is not currency. The most confident memo is the one whose staleness costs most.
- **Do not let corroboration become exploration.** You are checking a bounded list drawn from the memo. Reading files it never mentions means you have stopped recalling and started surveying — ask the human instead.
- **Do not treat a search hit as the answer.** `scad search --memos` matches the body as well as `topic` / `title` / `tags` / `entities` and a memo's own `project`, but a hit says *which memo* mentions X, not what was decided about X. Use it to *locate*, then read. Ordinary code search has no such limit; use it freely within the bound above.
- **Do not treat any document as current.** Memos, backlogs, handoffs, and READMEs all record a past. Only the code and the git history are now.
- **Do not resolve projects yourself.** `scad where` answers that. A memo's project is the one its session was working in — *unless* the memo names a project itself, which is how work noticed elsewhere gets filed here. So a memo listed under this project may have been written somewhere else entirely; its body says where.
