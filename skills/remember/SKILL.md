---
name: remember
description: >
  Capture what this session worked out and could not be recovered from the
  repo — findings, decisions and their rejected alternatives, where the goal
  turned, and what is still uncertain. Appends one record to this session's
  note file, classified by kind (info, handoff, bug, request, verification) and
  optionally filed against another project. Use when the user says remember,
  capture this, note this down, or wants the current span written to the
  durable notes store. Takes an optional angle; with none, select for what the
  diff and the docs cannot say.
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
- **One record per capture, and `kind` has five values on purpose.** If a span seems to want three records, it wants one record with better `tags`.
- **The one exception is a different destination, not a different subject.** If the span produced a `bug` or a `request` **about another project**, that is a second record with its own `kind` and its own `project` — see `project` below. This is not the rule above being bent: those two records have different *homes*, and the second one is the only way the other project ever hears about it. Splitting because a span was long is inflation; splitting because a finding belongs somewhere else is filing. Keep the main note whole and let the cross-filed one be short — a few lines and dense `tags` is enough.

## Making the note findable

Search is the only way anyone reaches this note later, and it matches **`topic` / `title` / `tags` / `entities` / an authored `project`** — **never the body**. A subject that appears only in `text` is, for retrieval purposes, not in the note at all. Two consequences, and they are the ones most often got wrong:

- **`topic` is a short freeform subject label**, not a summary. One subject. A compound topic joined by "plus", ";" or "and" — `notes-schema-plus-search-fix` — is a **smell**: it means the span actually had several subjects and you are trying to name them all in a field that holds one.
- **When the span had several subjects, every one of them goes in `tags`**, even though only one of them gets to be `topic`. Pick the subject the note is *most* about as `topic`, then make sure the others are searchable. Losing the second subject is the common failure, and it is silent — nothing tells you later that the note you needed existed under a name you did not search for.

`tags` should be **dense**: domain terms, jargon, tool and flag names, file names, decisions, the names of the things you were wrong about. This is the index; be generous.

## Steps

**1. Compose ONE JSON record** with these fields:

- `kind` — one of five, default `info` if none clearly fits:
  - `info` — the ordinary capture: findings, decisions, turns, corrections
  - `handoff` — written to be picked up: where the work stands, what is next, what to trust. Use it when the session is *ending mid-thread* and someone (probably you) resumes cold. Do **not** put "HANDOFF" in the title — that is what this field is for, and `scad notes ls --kind handoff` is how it gets found.
  - `bug` — something observed broken, whether or not it was fixed here
  - `request` — a feature or change wanted, filed rather than built
  - `verification` — a claim checked against reality; say what you checked and what came back
- `topic` — the subject, as a short kebab label; **reuse an existing project / plan / spec / file name** if this span is about a known entity. See *Making the note findable* above.
- `parent` — the earlier topic this one hangs off, when it hangs off one. Set it when this span **spun out of** a different topic — including one from an earlier session, whose notes are a different file. Omit it otherwise. It is the one piece of thread structure nothing can compute for you: how this note relates to the ones before it (`continue` / `shift` / `branch`) is worked out at read time from `parent` and the topics already in the thread, so **do not** write a `relation` field.
- `project` — **omit this**. The normal case is that the note belongs to the project this session is working in, which is resolved for you. Set it **only** for cross-capture: you are working in project A and this note is *about* project B — a bug you noticed in B, a feature you want in B. Then `project: "B"` files it where someone looking for B will find it. An unknown name warns and still writes, so a typo is visible but costs nothing. **It holds one project, so it answers "where does this belong", not "what does this mention"** — a note that merely *discusses* B alongside A keeps its own project and names B in `tags` and `entities` instead. **When a span genuinely produced work for another project, write the second record** (see the exception under *Discipline* above); burying a request for B inside a note filed under A is how it gets lost, and that is a measured failure, not a hypothetical one.
- `title` — one line
- `text` — the narrative as markdown. Use **Frame** / **Concluded** / **Rejected** (each with an invalidation condition) / **Open**, adaptively — omit sections that don't apply
- `tags` — a **dense** list of keywords, jargon, concepts, and decisions named in this span. This is the search index — see above; be generous
- `entities` — canonical named things (projects, plans, specs, files, tools)
- `sessions` — refs into the raw archive: this session and any other engine threads used this span, e.g. `["claude:<session-id>", "codex:<thread-id>"]` (include what you know; omit ids you don't)
- `invalidation` — (optional) the condition that would revise this capture

`ts` and `cwd_at_write` are filled in for you. Do not set them.

**2. Pipe it to the store.** Write the record to a temp file and pipe it in — a heredoc keeps the markdown readable and avoids shell-quoting the `text` field:

```
cat > /tmp/remember.json <<'EOF'
{ ...the record... }
EOF
scad session note --current --agent <you> < /tmp/remember.json
```

**Name yourself in `--agent`** — `claude`, `codex`, and so on. `--current` reads the session id your harness exports (`CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`), and it reads **only** the one belonging to the agent you name. That matters because these variables are inherited: an agent launched from another agent's session carries the parent's id in its environment, and a note filed against the parent is filed against the wrong conversation. Naming yourself is what prevents it. Omitting the flag assumes `claude`.

If `--current` reports it cannot resolve your session — your harness exports no id, as kimi does not — re-run with `--session <id>` naming this session. Do not guess an id, and do not let it fall through to another agent's.

**3. Confirm briefly:** repeat the one line `scad` printed. Do **not** print the full record — you just wrote it; echoing it back doubles its cost in context for no information. If it warned that the project name is unknown, say so — the note was written either way, and the human is the one who can tell a new project from a typo.

Reading the previous note first is **optional and usually unnecessary**: nothing in the record now asks you to place this span against the last one. If you want it anyway, `scad session notes <this-session-id> --json` is the cheap way.

## What this skill does NOT do

Everything about *where the note goes* belongs to `scad session note`, not here:

- Do not resolve a project. It is computed from `cwd`, and `project` in the record is an override for cross-capture only — `scad where` announces the computed one if you want to know.
- Do not pick or create a file. The store is `~/.scad/notes/<agent>/<session-uuid>.jsonl`, one file per session, and appending is the only write.
- Do not append with `>>` yourself. The CLI validates the record, fills the defaults, and is the same contract codex and pi call.

References (in the traitful-docs repo, not shipped with this skill): the `capture-format` spec for the record; the `session-index` spec, §Notes, for the addressing.
