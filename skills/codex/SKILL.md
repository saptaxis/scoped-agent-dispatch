---
name: codex
description: >
  Use when the user wants a second, independent AI agent (Codex) — for
  adversarial/peer code review, challenging a design, a debugging second
  opinion, a persistent consult companion, or generating/editing images —
  or when the user mentions codex, "ask codex", peer review, a second
  opinion, or image generation. Not for Codex itself — this skill is how
  another agent reaches Codex, and Codex consulting Codex is an echo.
---

# Codex — Peer Agent via CLI

**Announce at start:** "I'm using the codex skill to consult Codex as a peer agent."

Codex is a **separate coding agent** with its own model, run through the `codex` CLI. Use it as an independent peer — a reviewer, critic, debugger, or image generator — not as a copy of this Claude session. This skill calls `codex` directly; it has **no dependency on scad** (it just happens to ship in scad's plugin bundle).

## Model

- **`codex exec`** runs Codex non-interactively and returns when done. **Default sandbox is read-only** — Codex cannot modify files unless you pass `-s workspace-write`.
- **Threads persist.** Every `exec` creates a session with a `thread_id`. Resume it with `codex exec resume <thread_id>` to keep context across turns.
- **Independent perspective.** Frame prompts to *challenge*, not confirm. The value is a second opinion, not an echo.

## When to Use

- Adversarial or peer review of code / a design / a plan
- A debugging second opinion (independent root-cause)
- A persistent consult companion you feed curated context to
- Generating or editing images (Codex has built-in image generation)

## When NOT to Use

- **You are Codex.** This skill exists so that a *different* agent can get an
  independent opinion. Codex consulting Codex is the same model with the same
  weights answering its own question — an echo, not a second perspective, and
  it costs a turn and a sub-run to learn nothing. Do the work yourself.
  (Observed 2026-08-12: a Codex session launched as a peer reviewer read this
  skill, matched it, and spawned `codex exec` against itself.)
- Routine edits you can do directly — don't outsource your own work
- Dumping the whole conversation at Codex — curate instead (see below)
- Anything needing writes, unless the user explicitly delegates (`-s workspace-write`)

## Invocation

```bash
# One-shot (read-only by default) — capture thread_id for follow-ups
codex exec -C <dir> --json -o /tmp/codex.txt "<prompt>"

# Follow-up on the same thread (keeps prior context)
codex exec resume <thread_id> -C <dir> -o /tmp/codex.txt "<prompt>"

# Attach files/images — pipe the prompt via stdin, put -i LAST (see gotcha)
printf '%s' "<prompt referring to Image 1, Image 2>" | codex exec -C <dir> -i a.png -i b.jpg

# Delegated write (ONLY when the user explicitly asks Codex to change files)
codex exec -C <dir> -s workspace-write "<task>"
```

- Get `<thread_id>` from the `--json` stream (the `thread_id` field on `thread.started`). Reuse it to resume.
- `-C <dir>` roots Codex at a directory (defaults to cwd). `-o <file>` captures Codex's final message.
- Default sandbox is read-only; pass `-s workspace-write` **only** for an explicit delegated change.

> **Gotcha — `-i/--image` is variadic (`<FILE>...`).** A positional prompt placed *after* `-i` is eaten as another filename, and Codex then blocks waiting on stdin ("No prompt provided via stdin"). When attaching images, **pass the prompt via stdin** (`printf '%s' "$PROMPT" | codex exec … -i a.png`) or put the prompt *before* `-i`.

### Sandbox & writable paths (verified)

- **Default sandbox is read-only** — Codex cannot write until you pass `-s workspace-write`.
- With `-s workspace-write`, Codex can write **only** to the **working dir** (`-C`, or the launch cwd if `-C` is omitted) **+ the system temp dir**. Writes anywhere else (e.g. home) are **blocked**.
- **No `-C` → working dir = wherever you launch Codex.** In scripts, always pass `-C` so writes are predictable.
- Reads are not restricted the same way — `-i` inputs can live *outside* the writable root and still be read.
- To land output elsewhere: set `-C` to a parent containing the target, add writable roots via config, or (cleanest) let your own wrapper move the file — **your script is not sandboxed.** The raw generation is always at `~/.codex/generated_images/<thread_id>/` to harvest from.

## Profiles

One skill, several intents. The profile picks the prompt shape, the sandbox, and whether files are attached.

| Profile | Sandbox | Prompt intent |
|---------|---------|---------------|
| **review** (default) | read-only | "Adversarially review this. Strongest objection, concrete risks, simpler alternative, a test that would catch the issue. Do not edit files." |
| **debug** | read-only | "Independently find the root cause. Inspect the repo. Give evidence and the smallest reproduction." |
| **companion** | read-only | Persistent consult: inject a curated dossier first, then ask follow-ups by `resume`ing the thread. Keep it skeptical and concrete. |
| **implement** | workspace-write | Explicit delegated change only. "Keep the diff minimal, preserve public APIs, run tests, summarize what changed." Warn the user Codex may edit files. |
| **image** | workspace-write | Generate/edit images with `-i` inputs. "Use your image_generation tool (not code). Save to <path>." |

## Curated context, not a transcript

The point of a peer agent is an *independent* look. Send Codex a compact dossier — goal, current hypothesis, the specific files/diff, the exact question — not the whole Claude conversation. Frame it to challenge:

> "Challenge this. Find simpler alternatives. Identify hidden assumptions. Point to concrete risks."

## Image generation (verified: codex-cli 0.142.5)

- `image_generation` is a **stable, default-on** feature — no `--enable` needed.
- Attach reference/edit-target images with repeated `-i <file>` (ordered; refer to them as "Image 1…N").
- Generated files land at **`~/.codex/generated_images/<thread_id>/ig_*.png`**. With `-s workspace-write`, Codex also copies to any path you instruct — so ask it to save into your target folder.
- Native output size is 1024×1536 (portrait) and other aspect ratios; it honors material/lighting/subject steering.
- **Conditional edits work** (verified): pass the edit-target as `-i` and instruct "preserve everything, change only X" — Codex returns the same scene pixel-faithfully with only the requested change. This is edit-target editing, not just text-to-image.

## Rules

| Rule | Why |
|------|-----|
| Read-only unless explicitly delegated | Codex must not touch files during review/debug/consult. Only `implement`/`image` use `-s workspace-write`. |
| Always announce writes | Before an `implement`/`image` run, tell the user Codex may edit files. |
| Resume, don't restart | For a multi-turn consult or a staged pipeline, `resume <thread_id>` — a fresh `exec` loses the accumulated context. |
| Curate, never dump | Send a focused dossier, not the transcript. |
| Report Codex's answer faithfully | Relay findings as Codex's independent view; note where you agree or disagree, don't launder them as your own. |
