---
name: scad-plan-functional-test
description: >
  Use when a plan has been executed and the user wants to confirm features
  actually landed — after a scad run completes, after merging a feature
  branch, or when the user says "test the plan", "did everything land",
  "functional test", "check the build", "harvest and test", or asks what a
  plan execution actually produced.
---

# Plan Functional Test

Functionally test that an implementation plan's tasks actually landed. Not unit tests — exercise the real CLI, imports, configs, and behavior described in the plan.

**Announce at start:** "I'm using the plan functional test skill to check what landed."

## When NOT to Use

- Code review — **use superpowers:requesting-code-review**
- Writing unit tests — **use superpowers:test-driven-development**
- Executing the plan itself — **use superpowers:executing-plans**
- Merging/finishing a branch — **use superpowers:finishing-a-development-branch**

## Quick Reference

| Step | What | Key action |
|------|------|------------|
| 1 | Identify inputs | Locate plan doc + code source |
| 2 | Get code ready | Harvest (if scad), checkout, fresh venv, install |
| 3 | Extract tasks | Read plan, list each task's expected behavior |
| 4 | Test each task | Run real commands, check real output |
| 5 | Run test suite | Catch regressions |
| 6 | Report | Pass/partial/fail per task |

## The Process

### 1. Identify inputs

You need two things:
- **The plan document** — a markdown file with numbered tasks describing what was built
- **The code** — a branch, a scad run to harvest, or already-checked-out code

Ask the user if either is unclear. Common situations:

| Situation | What to do |
|-----------|------------|
| scad run just finished | `scad harvest <run-id>` to fetch branches, then checkout |
| Branch already on host | Checkout the branch |
| Already checked out | Skip straight to install |
| Plan location unknown | Check the project's docs/plans/ directory |

### 2. Get the code ready

**If harvesting from scad:**
```bash
scad harvest <run-id>
git checkout <branch-name>
```

**Create a fresh venv** — not the dev venv. A clean install catches missing dependencies, broken imports, and packaging issues that an editable install hides.

```bash
python3 -m venv <temp-dir>/<project>-verify-venv
source <temp-dir>/<project>-verify-venv/bin/activate
pip install .
```

### 3. Read the plan and extract tasks

Read the plan document. Pull out every numbered task with:
- Task number and title
- What it should do (acceptance criteria / expected behavior)
- Key commands, flags, config fields, or outputs to verify

### 4. Test each task

For each task, exercise the actual feature the way a user would:

- **CLI commands** — run them, check output and exit codes
- **Config fields** — create a config that uses them, verify they're recognized
- **New files** — check they exist with expected content
- **Behavior changes** — trigger the behavior, confirm the change
- **Error handling** — try the failure case if the plan specifies one

Don't just grep for a string or check if a function exists. Actually invoke the feature and observe the result.

Record each task:
- **PASS** — works as specified
- **PARTIAL** — partially works, note what's missing
- **FAIL** — doesn't work or doesn't exist

### 5. Run existing tests

After functional verification, run the project's test suite:

```bash
pytest    # or the project's test command
```

### 6. Report

```
## Functional Test: Plan <N> — <title>

Branch: <branch-name>
Tests: <N> passing

| Task | Status | Notes |
|------|--------|-------|
| 1. <title> | PASS | |
| 2. <title> | PARTIAL | <what's missing> |
| 3. <title> | PASS | |
```

Flag anything that needs follow-up — version mismatches, partial implementations, missing docs.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Checking if code exists instead of running it | Actually invoke the command/feature and check output |
| Using the dev venv | Fresh venv catches packaging issues |
| Skipping tasks that "obviously" work | Test every task — obvious ones break too |
| Not running the test suite | Functional tests don't catch regressions |
| Auto-deleting the verify venv | Ask the user — they may want to explore |
