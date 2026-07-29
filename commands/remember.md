---
description: Capture what was just worked out — an intent-driven summary appended to this session's note file.
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

**1. Read the last note** (cheap, optional but preferred) to set `relation`/`parent` relative to it:

```
scad session notes <this-session-id> --json
```

If you do not know the session id, skip this and use `relation: "shift"` — a wrong `continue` is worse than an honest `shift`.

**2. Compose ONE JSON record** with these fields:

- `span` — `"since-last"` (or a short phrase if guidance narrowed it)
- `topic` — the semantic subject as a short kebab label; **reuse an existing project / plan / spec / file name** if this span is about a known entity
- `relation` — one of: `continue` (same topic as the last record) · `shift` (new, unrelated) · `branch` (spins off an earlier topic) · `return` (resumes an earlier topic)
- `parent` — the earlier topic, **only** on `branch` / `return` (omit otherwise)
- `title` — one line
- `text` — the narrative as markdown. Use **Frame** / **Concluded** / **Rejected** (each with an invalidation condition) / **Open**, adaptively — omit sections that don't apply
- `tags` — a **dense** list of keywords, jargon, concepts, and decisions named in this span. This is the search index — be generous; include domain terms, tool/flag names, file names, and concepts
- `entities` — canonical named things (projects, plans, specs, files, tools)
- `sessions` — refs into the raw archive: this session and any other engine threads used this span, e.g. `["claude:<session-id>", "codex:<thread-id>"]` (include what you know; omit ids you don't)
- `invalidation` — (optional) the condition that would revise this capture

`ts` and `cwd_at_write` are filled in for you. Do not set them.

**3. Pipe it to the store.** Write the record to a temp file and pipe it in — a heredoc keeps the markdown readable and avoids shell-quoting the `text` field:

```
cat > /tmp/remember.json <<'EOF'
{ ...the record... }
EOF
scad session note --current < /tmp/remember.json
```

`--current` resolves the session whose trace is being written in this cwd. If it reports that several sessions are live, re-run with `--session <id>` naming this one.

**4. Confirm briefly:** repeat the one line `scad` printed. Do **not** print the full record — you just wrote it; echoing it back doubles its cost in context for no information.

## What this command does NOT do

Everything about *where the note goes* belongs to `scad session note`, not here:

- Do not resolve a project. `project` is computed from `cwd` and is never part of a durable path — `scad where` announces it if you want to know.
- Do not pick or create a file. The store is `~/.scad/notes/<agent>/<session-uuid>.jsonl`, one file per session, and appending is the only write.
- Do not append with `>>` yourself. The CLI validates the record, fills the defaults, and is the same contract codex and pi call.

References: [`capture-format.md`](../../../traitful-docs/docs/projects/traitful-workflow-ecosystem/specs/capture-format.md) for the record; [`session-index.md`](../../../traitful-docs/docs/projects/scoped-agent-dispatch/specs/session-index.md) §Notes for the addressing.
