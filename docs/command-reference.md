# scad — Command Reference

A use-case-oriented guide to every `scad` command: what it does, and **when / under what conditions** you'd reach for it. For a flat one-line listing see the `## CLI` section of the [README](../README.md); for the agent-facing workflow skill see [`skills/scad/SKILL.md`](../skills/scad/SKILL.md).

---

## Mental model (read this first)

- **Config** — a YAML file (`~/.scad/configs/<name>.yml`) describing a project: which repos, Python version, mounts, Claude settings. Registered once, reused forever.
- **Image** — a Docker image built from a config. Built once, cached, rebuilt only when the config or deps change.
- **Session** — a running container: repos cloned in, venv ready, credentials + plugins seeded. **Nothing runs until you inject work.** A session is identified by a **run-id** like `myproj-feat1-Jul23-1104`.
- **Job** — a Claude process injected into a session. Interactive (a tmux window you can attach to) or headless (`claude -p`, fire-and-forget). One session can hold many jobs.
- **Code flow** — repos are *cloned* into the container on a scad branch; your host repos are untouched until you explicitly **fetch** the branches back.

Lifecycle in one line: **`config → build → run start → inject → (work) → fetch → clean`.** The composites (`dispatch`, `harvest`, `finish`) bundle these steps.

---

## "Which command do I actually want?"

| You want to… | Run |
|---|---|
| Do a task in a fresh isolated container, one shot | `scad dispatch <config> --tag t --prompt "..."` |
| …and sit at the keyboard with Claude | add `--attach` |
| …and just get results, no interaction | add `--headless` (blocks) or `--headless --no-wait` (returns immediately) |
| …and pull the code back automatically when done | add `--fetch` |
| Run an implementation plan | `scad dispatch <config> --tag t --plan plan.md` |
| Run N independent tasks in parallel | `scad batch <config> --tag t --prompt-file prompts.txt` |
| Add more work to a session that's already running | `scad run inject <run-id> --prompt "..."` |
| See what's running right now | `scad run ls` |
| See what one session/job did | `scad run info <run-id>` / `scad run logs <run-id> --job <id>` |
| Get the code back onto my host | `scad code fetch <run-id>` (or `scad harvest`) |
| Save work and tear the session down | `scad finish <run-id>` |
| Free up memory when done for the day (macOS) | `scad vm stop` |

If you're new, start with `dispatch` — it does build + start + inject in one command.

---

## Prerequisites (what must be true before commands work)

| Condition | Linux | macOS |
|---|---|---|
| Docker daemon | Native `dockerd` must be running | scad's Colima VM — **auto-started** by `build`/`run start`/`dispatch`/`batch`. You rarely run `scad vm` by hand. |
| Claude auth | Logged into Claude Code (`claude /login`) | Same — scad reads credentials from the **macOS Keychain** automatically |
| First run | `install.sh` sets up venv + symlink + completions | `install.sh` additionally installs Colima and creates the `scad` VM |

scad warns when Claude credentials are within ~1 hour of expiring. Use `scad run refresh <run-id>` to push fresh credentials into a long-running session without restarting it.

---

## Composites — the everyday commands

These bundle the lifecycle. Reach for these first; drop to `session`/`code` sub-commands only when you need finer control.

| Command | Does | When / conditions |
|---|---|---|
| `scad dispatch <config> --tag <t> --prompt "..."` | build (if needed) + start session + inject, **interactive** | The default way to start work. Interactive unless you say otherwise. |
| `scad dispatch ... --attach` | …then drop you into the tmux session | You want to watch/steer Claude live |
| `scad dispatch ... --headless` | …inject headless and **block** until done | "Do this and give me the result." Non-interactive. |
| `scad dispatch ... --headless --no-wait` | …inject headless and return immediately | "Start this, I'll check later." |
| `scad dispatch ... --fetch` | headless + wait + **auto-fetch branches to host** | One-shot task where you want the code back on the host automatically. Implies `--headless --wait`. |
| `scad dispatch ... --plan plan.md` | generate an execution prompt from a plan file, then dispatch | Running a written implementation plan |
| `scad dispatch ... --tail` | stream Claude's tool activity during the wait | You want live progress on a headless job |
| `scad dispatch ... --no-build` | skip the image-build check | Image is known-current and you want to save the check |
| `scad batch <config> --tag <t> --prompt-file f.txt` | run each `---`-delimited prompt as a **parallel headless job** | N independent tasks at once. `--parallel N` (default 3), `--fail-fast` to stop queuing on first failure. |
| `scad harvest <run-id>` | fetch branches back to host + show `git log` summary | "What did Claude produce?" `--diff` for the full diff, `--merge` to fast-forward-merge into source. |
| `scad finish <run-id>` | fetch branches + **clean** the session (safe teardown) | Done with a session and want the work saved. `--merge` to also merge, `--keep-session` to fetch without destroying, `--force` if still running, `--no-fetch` to skip fetching (risk losing work). |

---

## `scad run` — the container run lifecycle

Use these when you want step-by-step control instead of a composite.

| Command | Does | When / conditions |
|---|---|---|
| `run start <config> --tag <t>` | build (if needed) + start container, **no work injected** | You want the environment up before deciding what to run. Auto-starts the VM on macOS. `--prompt` to inject immediately, `--headless` (needs `--prompt`), `--branch` to name the branch, `--rebuild` to force a fresh image. |
| `run inject <run-id> --prompt "..."` | inject a new Claude process, **interactive** | Add work to a running session. `--headless` for fire-and-forget, `--wait` to block (headless only), `--tail` to stream during wait, `--branch` to switch branch first. |
| `run send <run-id> "text"` | type into a running **interactive** Claude | Reply to Claude mid-conversation without attaching |
| `run attach <run-id>` | attach to the session's tmux | Watch/drive an interactive job. Detaching returns you to the host shell; the container keeps running. |
| `run jobs <run-id>` | list the run's jobs with their status | "What has this session run?" |
| `run logs <run-id>` | read the entrypoint/agent log | Setup and lifecycle output. `--job <id>` for one job, `-s/--stream` for Claude's tool activity, `-f/--follow` to tail, `-n` for line count. |
| `run info <run-id>` | single-session dashboard: clones, Claude session IDs, event log | Drill into one session |
| `run refresh <run-id>` | push fresh credentials into the container | Long-running session whose Claude auth is nearing expiry (scad warns) |
| `run stop <run-id>` | stop the container, **preserve** clones/state | Pause a session; restartable state stays on disk. `--all [--yes]` for every running session. |
| `run clean <run-id>` | **destroy** container + clones + run data | Point of no return. Fetch first (`harvest`) or use `finish` which fetches automatically. `--all [--yes] [--force]`. |

> `scad run ls` (top-level) is the fleet view — every running session with jobs nested. `scad session` is now the trace index only (`ls`, `show`, `read`).

---

## `scad code` — git flow between host and container

The container works on cloned copies. These move code between the clone and your real host repos.

| Command | Does | When / conditions |
|---|---|---|
| `code fetch <run-id>` | fetch the session's branches back to your host repos | Get Claude's work onto the host (without tearing down). `harvest`/`finish` call this for you. |
| `code sync <run-id>` | pull host repo changes **into** the clones | You changed the host repo and want the running session to see it. `--checkout <branch>`, `--no-update-main`. |
| `code diff <run-id>` | diff clones vs source repos | See uncommitted/unfetched changes before fetching |
| `code branch <run-id> <name>` | create + switch a branch in all clone repos | Reorganize work onto a new branch inside the session |
| `code add <run-id> --path <dir> --name <name>` | add a host directory to the session workspace | Bring in extra data/repo mid-session. `--clone` to git-clone instead of symlink. **macOS:** a path outside `$HOME` isn't VM-visible and can't be hot-added — scad warns and offers a restart; `--restart-vm` accepts it (this stops running sessions, then scad restarts the target). |
| `code remove <run-id> --name <name>` | remove a directory from the workspace | Undo a `code add` |

---

## `scad config` — project definitions

| Command | Does | When / conditions |
|---|---|---|
| `config list` | list registered configs | "What projects do I have?" |
| `config new <name> [--edit]` | scaffold a new config from a commented template | Starting a new project |
| `config add <path>` | register an external YAML (symlinks into `~/.scad/configs/`) | Keep the config in your repo and register it |
| `config remove <name>` | unregister (removes the link, not your source file) | Stop tracking a project |
| `config view <name>` | print the config YAML | Quick look |
| `config edit <name>` | open in `$EDITOR` | Change repos, mounts, deps |
| `config info <name>` | structured environment summary (repos, image, mounts) | Verify what a config will produce before building |

---

## `scad build` / `scad run ls` / `scad gc`

| Command | Does | When / conditions |
|---|---|---|
| `build <config>` | build/rebuild the Docker image | After creating or changing a config, or changing deps. Composites build automatically; run this to pre-build or force a rebuild. `-v` verbose, `--no-cache` to bust the layer cache. Auto-starts the VM on macOS. |
| `run ls` | list running sessions with their jobs (the fleet view) | Day-to-day "what's running?" `--all` for full history (incl. cleaned), `--cost` to add token cost (slow). |
| `run ls <config>` | cross-session overview for one project | Aggregate view of a project's sessions. `--cost` for cost. |
| `gc` | find orphaned containers/run-dirs/images (**dry-run**) | Housekeeping after crashes or manual docker meddling. `--force` to actually clean. |

---

## `scad vm` — the macOS Docker VM (escape hatch, rarely needed)

On macOS scad runs containers in a dedicated Colima VM it owns (profile `scad`, isolated from any other Docker). **It's auto-managed:** `build`, `run start`, `dispatch`, and `batch` start it if it's down. You only touch `scad vm` for the cases below. On Linux there is no VM — `vm status`/`vm info` report the native daemon; `start`/`stop`/`delete` exit with a macOS-only message.

| Command | Does | When / conditions |
|---|---|---|
| `vm status` | is scad's Docker daemon reachable? | Quick health check |
| `vm info` | sizing, socket path, and which extra mounts are exposed | Inspect the VM |
| `vm stop` | stop the VM (containers preserved, not running) | **The one you'll use** — reclaim ~4 GiB RAM when done. Nothing auto-stops it. |
| `vm start` | start / create the VM | Pre-warm before a session so the first command isn't slow. Rarely needed (auto-start covers it). |
| `vm delete [--yes]` | destroy the VM and everything in it | Reset, or apply new sizing (`vm delete && vm start`). Prompts unless `--yes`. |

**Sizing** lives in `~/.scad/settings.yml` (`colima.cpu` 2, `colima.memory` 4 GiB, `colima.disk` 60 GiB, `vm_type` vz, `mount_type` virtiofs) and applies at VM creation. To resize: edit it, then `scad vm delete && scad vm start`.

---

## Gotchas (conditions that trip people up)

- **`--wait` is headless-only.** Interactive jobs live in tmux and can't block. Use `--headless --wait` (or just `--wait`, which implies headless).
- **`--fetch` implies `--wait`.** You can't fetch results from unfinished work.
- **`run clean` is destructive, no undo.** Fetch first (`harvest`) or use `finish`, which fetches automatically.
- **Detaching tmux drops you to the host shell.** Expected — the container keeps running. Reattach with `run attach`.
- **Credentials expire.** scad warns when within ~1h. `run refresh <run-id>` pushes fresh credentials without restarting.
- **macOS, non-`$HOME` mounts:** paths outside `$HOME` (external drives, `/Volumes/…`, `/data`) aren't visible to the VM by default. At `run start` scad reconciles them and restarts the VM **only when the set changed**. `code add` of such a path mid-session can't hot-add — it warns and offers a restart.
- **macOS, `gpu: true`:** unsupported (no GPU passthrough into the Lima VM) — it errors clearly. GPU stays Linux-only.
- **First command after boot/`vm stop` is slower** on macOS — it pays a few seconds to start the VM. Everything after is instant until you stop it.
