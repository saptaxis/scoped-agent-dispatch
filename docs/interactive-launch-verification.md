# Manual verification — `scad session launch` / `scad session resume`

**This is the layer the test suite cannot reach, and it is not automated on
purpose: every run costs a model call and leaves a real session in the corpus.**
Run it by hand when the launch routes change — new agent version, changed flags,
a rewritten gate screen.

Everything else is covered by `tests/test_launch.py`, which drives a **stub**
agent over a private tmux socket. The stub can prove that the pane is a pty,
that the gates are answered by label, and that the record round-trips. It cannot
prove that a real `codex resume <id>` restores a real conversation. That is what
this page is for.

The one thing to hold onto: **the test suite must never do any of this.** A
suite that launches agents costs money per run and fills the index with junk.

---

## Before you start

```bash
mkdir -p ~/Desktop/scad-launch-demo && cd ~/Desktop/scad-launch-demo
git init -q && git commit -q --allow-empty -m init
scad where            # expect: myproj via marker:.git — not `unfiled`
tmux -V               # required; a launch without a pty is refused
```

The planted phrase is how you prove the resumed session is *the same
conversation* rather than a new one that happens to start cleanly. Use a
distinctive one; `PURPLE-OTTER-42` is what the original walk used.

---

## 1. Claude — the id is minted before launch

```bash
scad session launch --agent claude --cwd ~/Desktop/scad-launch-demo \
  --prompt "Remember the phrase PURPLE-OTTER-42. Reply with exactly: ok."
```

- [ ] The command returns immediately and leaves your terminal alone.
- [ ] `provenance` reads `minted`.
- [ ] The transcript is at exactly
      `~/.claude/projects/<encoded-cwd>/<the printed id>.jsonl`.
- [ ] **Picker visibility.** In the pane, `/resume` lists this session. If it
      does not, the pty is not doing its job — check `claude --debug` for
      `Session <id> filtered from /resume: entrypoint=sdk-cli`.
- [ ] Close the pane, then `scad session resume <id>` and ask what the phrase
      was. It answers `PURPLE-OTTER-42`.

## 2. kimi — the id is read back and confirmed

```bash
scad session launch --agent kimi --cwd ~/Desktop/scad-launch-demo \
  --prompt "Remember the phrase PURPLE-OTTER-42. Reply with exactly: ok."
```

- [ ] The id resolves inside the 15s window (it appeared "immediately" on the
      original walk — a slow one is worth reporting, not tuning away).
- [ ] The printed id is the **bare uuid**, matching what `scad session ls`
      shows, while the resume command carries `kimi --session session_<uuid>`.
      Both halves matter: the index strips the prefix and the CLI requires it.
- [ ] The matching line in `~/.kimi-code/session_index.jsonl` has a `workDir`
      equal to the launch directory.
- [ ] Close the pane, resume, ask for the phrase.

## 3. codex — the id comes from the first turn

Do this one **in a directory codex has never seen**, so the trust gate is live.
That is the whole point of the case.

```bash
scad session launch --agent codex --cwd ~/Desktop/scad-launch-demo
```

- [ ] The gates clear without your help, and **without a `curl | sh` running**.
      Watch for it: option 1 on the update gate installs a new codex.
- [ ] Turn 1 is the priming prompt and the reply is exactly `ready.`
- [ ] **No note was written.** `scad notes ls --limit 5` shows nothing new.
      `scad`, `remember` and `recall` are live trigger words inside the session
      the moment turn 1 names scad, and a fired `remember` writes junk into the
      one tier nothing can re-derive. This check is the reason
      `Take no action and read nothing` is in the prompt.
- [ ] `provenance` reads `tui-native`, and `codex resume` (no flags) lists this
      thread in its default picker. An `exec`-born thread would not appear —
      that is why codex is not primed with `codex exec`.
- [ ] Type the phrase into the live session, close the pane, resume, ask for it.

## 4. The failure modes, deliberately provoked

- [ ] **No tmux.** `PATH=/usr/bin scad session launch --agent claude` refuses
      and names tmux. It must not fall back: a non-pty launch produces the
      `sdk-cli` stamp, which looks fine and hides the session.
- [ ] **Unfiled directory.** Launch from `/tmp/somewhere-loose`. It warns, names
      the `.scad-project` fix, and **launches anyway**.
- [ ] **Resume while live.** With the pane still open, `scad session resume <id>`
      attaches to that pane rather than starting a second process.
- [ ] **Resume after the pane is gone.** Kill the pane; the same command execs
      the agent with the cwd set.

## 5. Read-back

- [ ] `scad session read <id>` shows the turns **after** an index pass
      (`scad reindex`), not before. Interactive read-back is eventually
      consistent; headless output is immediate. Confirm the asymmetry rather
      than assume it away.
- [ ] `scad view` puts the session under the expected project, with the resume
      command on the row.

---

## Cleaning up

```bash
tmux kill-session -t scad-cl-<HHMM>     # per launch, or leave them running
rm -rf ~/Desktop/scad-launch-demo
```

The launch records under `~/.scad/launches/` are authored facts about events
and are not swept by anything. Delete them by hand if the demo sessions are not
worth keeping.
