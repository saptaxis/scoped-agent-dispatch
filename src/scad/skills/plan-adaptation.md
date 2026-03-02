---
name: scad-plan-adapt
description: Use when you need to adapt an implementation plan for execution in a scad container. Rewrites host paths to container paths, adds environment context. Trigger phrases - "make this plan scad-aware", "adapt this plan for scad", "prepare plan for container execution", "scad-adapt this plan".
---

# Scad Plan Adaptation

## Overview

Rewrite an implementation plan written for local development to work inside a scad Docker container. Translates host paths to container paths and adds environment context.

## When to Use

- Before dispatching a plan for execution via `scad session start --prompt`
- When a plan references host-specific paths (~/Dropbox/code/..., /home/user/...)
- When preparing a plan for headless or interactive-with-prompt execution

## Process

### Step 1: Get scad environment info

Run this command to get the container environment mapping:

```bash
scad config info <config-name>
```

This outputs:
- Repo path mappings: host path → /workspace/<key>
- Mount points
- Python/venv location (/opt/venv)
- Claude configuration

### Step 2: Read the plan file

Read the implementation plan that needs adaptation.

### Step 3: Build path mapping

From the `scad config info` output, build a mapping:

| Host path | Container path |
|-----------|---------------|
| (repo path from config) | /workspace/(key) |
| (mount host path) | (mount container path) |

### Step 4: Rewrite the plan

Apply these transformations:

1. **Path replacement:** Replace all host repo paths with /workspace/<key> equivalents
   - Both resolved paths (/home/user/Dropbox/code/project) and tilde paths (~/Dropbox/code/project)
   - File references in "Files:" sections
   - Commands that reference paths (cd, pytest, git add, etc.)

2. **Environment header:** Add or update the plan header with:
   ```
   > **Execution environment:** scad container (config: <name>)
   > **Repos:** <key>: /workspace/<key> (workdir|add-dir, rw|ro)
   > **Python:** /opt/venv (auto-activated)
   > **Working directory:** /workspace/<workdir-key>
   ```

3. **Command adjustments:**
   - Remove `source .venv/bin/activate` or similar (venv auto-activated by entrypoint)
   - Remove `cd ~/path/to/project` at start (working dir is set by entrypoint)
   - Keep `cd` within the workspace (e.g., `cd /workspace/docs`)
   - Keep all git, pytest, pip commands as-is (they work the same)

4. **Things to NOT change:**
   - Relative paths (tests/test_foo.py, src/scad/cli.py) — these are relative to working dir and work as-is
   - Import statements
   - Code content inside code blocks
   - Test assertions
   - Commit messages

### Step 5: Verify

After rewriting, scan for any remaining host-specific paths that were missed. Common patterns:
- `/home/<user>/`
- `~/Dropbox/` or `~/code/`
- Absolute paths that don't start with `/workspace/` or `/opt/`

### Step 6: Write back

Write the adapted plan back to the same file (or offer to write to a new file with `-scad` suffix).

## Key Facts About Scad Containers

- **User:** `scad` (non-root)
- **Working directory:** `/workspace/<workdir-key>` (set by entrypoint)
- **Venv:** `/opt/venv` (auto-activated by entrypoint, no need to source)
- **Repos:** Mounted at `/workspace/<key>` (rw for worktree repos, ro otherwise)
- **Claude:** Pre-configured with permissions, plugins, and settings
- **Git:** Configured with host's gitconfig, on a branch named `scad-{config}-{tag}-{MonDD}-{HHMM}`
- **Timezone:** Matches host (IANA timezone)
