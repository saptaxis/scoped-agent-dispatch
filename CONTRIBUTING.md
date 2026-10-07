# Contributing

## Setup

```bash
git clone https://github.com/saptaxis/scoped-agent-dispatch
cd scoped-agent-dispatch
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```

The suite never touches the machine it runs on: `SCAD_HOME` and `SCAD_ARCHIVE` point at a temporary directory, and any test that runs `colima` or reaches the Docker daemon fails. A test that needs the real scad VM is marked `@pytest.mark.vm` and is skipped unless asked for. It restarts the VM and stops every container in it, so run it on purpose:

```bash
pytest --run-vm -m vm
```

## Code style

- Python 3.11+, type hints
- pytest for tests, click for CLI
- TDD: write failing test first

## Releasing

`main` and `refs/tags/v*` are protected by rulesets with no bypass, so a release goes through a branch and a PR, and a published tag never moves.

1. Bump the version in **both** `pyproject.toml` and `src/scad/__init__.py`. They have drifted for four months once; nothing checks them but you.
2. In `CHANGELOG.md`, rename `[Unreleased]` to `[x.y.z] — YYYY-MM-DD` and open a new empty `[Unreleased]` above it.
3. Commit as `release: x.y.z`, open the PR, merge it.
4. On the merged `main`: `git tag -a vx.y.z -m "x.y.z" && git push origin vx.y.z`. The tag goes where the changelog says the features are.
