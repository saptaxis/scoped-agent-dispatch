# Scad Interactive Walkthrough

The auto-run demo (`./examples/demo.sh`) covers config, build, start, status, stop, and clean.
This walkthrough covers the interactive steps: attach, Claude interaction, detach, fetch, sync.

## Prerequisites

Run the demo script first, but stop it before it cleans up (set `PAUSE=999` or Ctrl+C after step 5):

```bash
./examples/demo.sh
```

Or start a fresh session:

```bash
scad config add examples/demo.yml   # or your own config
scad build demo
scad session start demo
```

## Attach to Claude

```bash
scad session attach <run-id>
```

You're now inside tmux with Claude running. Try:
- "Add a greet(name) function to hello.py and commit"
- "Update projects/demo/overview.md with what you added and commit to docs repo"

## Detach and reattach

- `Ctrl+b d` — detach (container keeps running)
- `scad session status` — verify it's still running
- `scad session attach <run-id>` — reattach

## Fetch code back to host

After Claude makes commits inside the container:

```bash
scad code fetch <run-id>

# Verify branches appeared in source repos
git -C ~/vsr-tmp/scad-demo/demo-code branch
git -C ~/vsr-tmp/scad-demo/demo-code log main..<branch> --oneline
```

## Sync host changes into container

If you commit on the host and want the container to see it:

```bash
scad code sync <run-id>
```

## Refresh credentials mid-session

If Claude auth is about to expire:

```bash
scad code refresh <run-id>
```

## Claude exit behavior

Inside the container:
1. Exit Claude (type `/exit` or Ctrl+C twice)
2. You drop to a bash prompt (container stays alive)
3. Start Claude again if needed, or `exit` bash to stop the container

## Clean up

```bash
scad session clean <run-id>         # removes container + clones + run dir
scad config remove demo             # unlinks config (source file preserved)
```
