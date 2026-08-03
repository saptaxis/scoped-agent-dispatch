---
name: remember
description: >
  Capture what this session worked out and could not be recovered from the
  repo — findings, decisions and their rejected alternatives, where the goal
  turned, and what is still uncertain. Appends one record to this session's
  note file. Use when the user says remember, capture this, note this down, or
  wants the current span written to the durable notes store. Takes an optional
  angle; with none, select for what the diff and the docs cannot say.
---

You are running the capture skill **inside the current session**. You already hold the conversation context — **use it directly. Do NOT re-read the transcript, re-open files, or re-ingest logs.** This must be cheap: one in-context generation plus one append.

Optional guidance from the user arrives as this skill's arguments. Treat it as the *angle*, never as the whole brief — the human supplies the angle, you hold the material. Vague guidance ("something useful") is the same as none.

## What to capture

Cover the span **since the last capture** in this session. But a note is not a work log, and the selection matters more than the coverage.

**The test: would this be lost?** The repo already records what changed — git has the diff, the code says what it now does, the docs say what it is for. A note holds what none of them can:

- **Findings that cost something to learn.** A measurement, a trap, a thing that contradicted the documentation or your own expectation. Include the number or the observation, not just the conclusion.
- **Why a decision went the way it did** — and especially **what was rejected**, with the condition that would reverse it. A rejection is a first-class capture; it is the thing that stops the same idea being rebuilt in three weeks.
- **Where the conversation turned.** A goal that changed mid-session, a premise that collapsed, an ask that turned out to be a different ask. The turn is often the most useful line in the note, and it exists nowhere else.
- **Corrections.** Where something believed earlier *in this session* turned out wrong. Say what was believed, what it actually is, and what caught it.
- **What is still uncertain**, marked as uncertain.

**Leave out what is recoverable.** A list of files touched, a restatement of what a function now does, a paraphrase of a spec, or a step-by-step of the work in the order it happened. If someone could get it by reading the diff or the doc, it is costing context for nothing.

**Nothing qualifying is a valid outcome.** A span that was mechanical — a rename, a green test run, applying a decision already recorded — deserves a Frame and two lines, or an honest "nothing here that the diff does not already say". An inflated note is worse than a short one, because it teaches the reader that notes are noise.

## Discipline — anti-inflation (important)

- Present tentative thinking as tentative. Do **not** launder exploration into conclusions.
- Attribute honestly. "Measured X" and "assumed X" are different claims, and only one of them survives contact with a future session.
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
scad session note --current --agent <you> < /tmp/remember.json
```

**Name yourself in `--agent`** — `claude`, `codex`, and so on. `--current` reads the session id your harness exports (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`), and it reads **only** the one belonging to the agent you name. That matters because these variables are inherited: an agent launched from another agent's session carries the parent's id in its environment, and a note filed against the parent is filed against the wrong conversation. Naming yourself is what prevents it. Omitting the flag assumes `claude`.

If `--current` reports it cannot resolve your session — your harness exports no id, as kimi does not — re-run with `--session <id>` naming this session. Do not guess an id, and do not let it fall through to another agent's.

**4. Confirm briefly:** repeat the one line `scad` printed. Do **not** print the full record — you just wrote it; echoing it back doubles its cost in context for no information.

## What this skill does NOT do

Everything about *where the note goes* belongs to `scad session note`, not here:

- Do not resolve a project. `project` is computed from `cwd` and is never part of a durable path — `scad where` announces it if you want to know.
- Do not pick or create a file. The store is `~/.scad/notes/<agent>/<session-uuid>.jsonl`, one file per session, and appending is the only write.
- Do not append with `>>` yourself. The CLI validates the record, fills the defaults, and is the same contract codex and pi call.

References (in the traitful-docs repo, not shipped with this skill): the `capture-format` spec for the record; the `session-index` spec, §Notes, for the addressing.
