---
description: Capture what was just worked out — an intent-driven summary appended to this session's capture log (JSONL).
argument-hint: "[optional angle/guidance]"
---

You are running the `/remember` capture command **inside the current session**. You already hold the conversation context — **use it directly. Do NOT re-read the transcript, re-open files, or re-ingest logs.** This must be cheap: one in-context generation plus one append.

Optional guidance from the user (may be empty): **$ARGUMENTS**

## What to capture

Summarize what has been worked out **since the last capture** in this session. If guidance was given above, use it as the *angle* — the human supplies the angle, you hold the material.

## Discipline — anti-inflation (important)

- Present tentative thinking as tentative. Do **not** launder exploration into conclusions.
- A decision **not** taken (something evaluated and rejected) is a first-class capture — record it *with the condition that would reverse it*.
- Scale structure to content: a small span is a Frame plus a line or two; a substantive one uses the narrative sections below.

## Steps

**1. Resolve the store.**
- `project` = basename of the current git repo root (fall back to the cwd's basename).
- `session file` = the newest `~/.capture/<project>/*.jsonl` that was modified within the last 4 hours (treat as the same session and append to it). If none exists, create `~/.capture/<project>/<YYYY-MM-DD>-<short-slug>.jsonl`. Create the directory if missing.
- Read the **last record** in that file (if any) to set `relation`/`parent` relative to it.

**2. Compose ONE JSON record** (single line) with these fields:
- `ts` — current local ISO8601 timestamp
- `span` — `"since-last"` (or a short phrase if guidance narrowed it)
- `topic` — the semantic subject as a short kebab label; **reuse an existing project / plan / spec / file name** if this span is about a known entity
- `relation` — one of: `continue` (same topic as the last record) · `shift` (new, unrelated) · `branch` (spins off an earlier topic) · `return` (resumes an earlier topic)
- `parent` — the earlier topic, **only** on `branch` / `return` (omit otherwise)
- `title` — one line
- `text` — the narrative as markdown. Use **Frame** / **Concluded** / **Rejected** (each with an invalidation condition) / **Open**, adaptively — omit sections that don't apply
- `tags` — a **dense** list of keywords, jargon, concepts, and decisions named in this span. This is the search index — be generous; include domain terms, tool/flag names, file names, and concepts
- `entities` — canonical named things (projects, plans, specs, files, tools)
- `sessions` — refs into the raw archive: this Claude session and any other engine threads used this span, e.g. `["claude:<session-id>", "codex:<thread-id>"]` (include what you know; omit ids you don't)
- `invalidation` — (optional) the condition that would revise this capture

**3. Append** the record as a single line to the session file. The `text` field is markdown — JSON-escape its newlines (`\n`) so the whole record stays one physical line. Use a tool call that **appends** (e.g. shell `>>`) — never rewrite or reformat existing lines.

**4. Confirm briefly:** the file path, the `topic`, the `relation` (and `parent` if any), and the number of tags. Do **not** print the full record.

Reference: capture format spec at `traitful-docs/docs/projects/traitful-workflow-ecosystem/specs/capture-format.md`.

<!-- Interim: storage (resolve store + append) is inline here. It migrates to a
     public `scad remember` contract (CLI + capture-format.md) that any agent —
     Claude Code, codex, pi, private operators — calls. This command then thins
     to a caller: produce the record (judgment), pipe it to `scad remember`. -->

