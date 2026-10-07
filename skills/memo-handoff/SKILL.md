---
name: memo-handoff
description: >
  Write a handoff memo: where the work stands, what to trust, what is open and
  what comes next, checked against the repo before it is written, so a fresh
  session can pick the work up cold. Use when the user says write a handoff,
  handoff memo, hand this off, we are stopping here, or wrap up for the next
  session. Takes an optional scope: empty for the whole context of the work,
  brief or end for the latest phase only, or any other text as the focus.
---

You are writing a **handoff memo**: one scad memo of kind `handoff`, filed against this session, for whoever resumes the work, probably a later you with no memory of this conversation. `/memo-recall` looks for it first.

Unlike an ordinary memo, a handoff makes claims about **the state of things now** (the branch, what is committed, what passes), and those must be checked, not remembered. So unlike `/memo-write`, this skill reads the repo before it writes.

## 1. Scope, from the arguments

- **Empty**: the whole context of the work to date.
- **`brief`, `short`, `end` or `recent`**: the most recent phase only; keep it tight.
- **Anything else**: the focus. Weight the memo towards it, and still give a short status and next steps.

## 2. Check before you write

For each repository the work touched:

```
git status -sb
git log --oneline -15
```

Note the branch, HEAD, uncommitted or untracked files, and whether anything is unpushed. If tests are part of what you are about to claim, run them, or say they were not run. Open the files and counts you are going to describe, and quote them rather than approximating.

If an earlier handoff exists for this work (`scad memos ls --project <name> --kind handoff --limit 3`), read the newest with `scad memos read <session-id> --last`, so this one continues it rather than repeating it, and name it as `parent`.

## 3. The text

Markdown, in these sections, each scaled to the work; cut what does not apply:

- **Frame**: what the session set out to do, in a line or two, and where the goal turned if it did.
- **Where things stand**: repo, branch, HEAD, what is committed and pushed, what is not, test status as measured in step 2.
- **What to trust, and what not**: verified, assumed, unfinished, kept apart. This is the most valuable section; do not soften it.
- **Concluded** and **Rejected**: decisions with their reasons; each rejection with the condition that would reverse it.
- **Open**: questions still to decide, and whose they are.
- **Next steps**: concrete and ordered, the cheapest one that unblocks the most first.

Real numbers, paths, commands and commit hashes; absolute dates.

## 4. The record

```
{
  "kind": "handoff",
  "topic": "<short kebab subject; reuse the project, spec or plan name>",
  "parent": "<the earlier handoff's topic, if this continues one>",
  "title": "<one line: where it stands and what is next; not the word HANDOFF>",
  "text": "<the sections above>",
  "tags": ["<dense: every subject, tool, flag, file and decision named>"],
  "entities": ["<repos, specs, files, tools>"],
  "sessions": ["claude:<this session's id>"],
  "invalidation": "<what would make this memo wrong: a commit, a merge, a decision>"
}
```

Omit `parent` when there is none. `ts` and `cwd_at_write` are filled in for you. `tags` and `entities` are what `scad memos ls --about` matches, so be generous. Set `project` only when the handoff is for another project's work.

## 5. Write it, against your own session

```
cat > /tmp/memo-handoff.json <<'EOF'
{ ...the record... }
EOF
scad session memo --current --agent <you> < /tmp/memo-handoff.json
```

**Name yourself in `--agent`**: `claude`, `codex`, and so on. `--current` reads only the session id your own harness exports. A session you launched is not yours: never pass its id, and never let `--current` fall through to another agent's variable. If `--current` cannot resolve your session (kimi exports no id), re-run with `--session <id>` naming this session.

Then repeat the one line `scad` printed, and stop. Do not print the record back.

## What this skill does NOT do

- It does not write a document into the repo. A handoff is a memo, so `/memo-recall` and `scad memos ls --kind handoff` find it.
- It does not commit, push or tidy anything up. It records the state as it is, including what is unfinished.
