---
name: attribution
description: >
  Check which project scad files this directory's sessions under, and fix it if
  the answer is wrong. Use when the user says why is this unfiled, which project
  am I in, where do my sessions go, mark this as a project, my sessions are not
  showing up under the right project, or before starting work in a directory
  that is new. Runs scad where, explains how it resolved, and offers to drop a
  .scad-project marker when the answer is unfiled or is not the project the
  human wants.
---

Every agent session on this machine is indexed, whatever started it. So nothing
is ever *lost* — but `project` is the key everything is retrieved by, and it is
the one field that can be quietly wrong. A session filed under the wrong project
looks exactly like a result until someone goes looking for it.

Two failures, and the second is worse:

- **`unfiled`** — nothing here marks a project. The session lands in the bucket
  every unattributed session on the machine shares, findable only by id.
- **The wrong project** — resolution walked *up* past what you meant and
  answered with an enclosing repo. This one reports like a success.

## 1. Ask

```
scad where
```

Add `--start DIR` to ask about somewhere else. The answer comes from scad's own
resolver, which is also what the indexer ran — so this is the attribution the
sessions actually get, not a re-derivation of it.

It prints the project, what was `tried`, and which tier `matched by`. The
precedence is fixed: `scad.yml` → `.scad-project` → the git root, each walking
up from the directory. Read the resolved path in the `matched by` line, not
just the name: that is the directory scad considers the root, and a name that
looks right can come from a root that is not.

## 2. Say how it resolved

Report it in one or two lines. What matters to the human is the tier and the
root, e.g. *"unfiled — no marker anywhere above this directory, and it is not
in a git repo"*, or *"filed under `orglens` via the git root at
`~/code/orglens`, which is three levels up — sessions here group with all of
orglens."*

Stop here if the answer is right. This is the common case.

## 3. Offer the marker — do not drop it unasked

When the answer is `unfiled`, or when it resolved somewhere the human does not
want, the fix is one empty file at the directory that *should* be the root:

```
touch <root>/.scad-project
```

`scad.yml` marks a root too, and takes precedence — use it when the directory
already has one, and `.scad-project` otherwise. An empty `.scad-project` is
enough; nothing reads its contents.

Always confirm the *directory* before creating anything. The marker names a
root, and the root decides how everything under it groups — put it too high and
unrelated work merges, too low and one project splits in two. Ask which the
human wants when it is not obvious, then re-run `scad where` to show the new
answer.

## 4. Say what a marker does not do

**A marker files future sessions, not past ones.** `project` is a computed
column and the incremental index pass is keyed on file mtime, so it is never
recomputed for a session whose trace has not changed. Re-filing what is already
indexed takes the full pass:

```
scad reindex --rebuild
```

That is a real cost on a large index and it is the human's call, so name it and
let them choose. Say it every time you drop a marker — otherwise they check the
viewer, see the old grouping, and conclude the marker did not work.

## What this skill does NOT do

- **Do not resolve a project yourself.** No basename guessing, no walking up
  for `.git` by hand. `scad where` is the answer; anything you compute in
  parallel is a second rule that can disagree with the index.
- **Do not create a marker without being asked.** It changes how every future
  session in that tree is grouped.
- **Do not run `scad reindex --rebuild` on your own.** Offer it. It rebuilds
  the whole index.
- **Do not treat `unfiled` as broken.** The sessions are indexed and readable
  by id; only their grouping is missing.
