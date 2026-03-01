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

## Code style

- Python 3.11+, type hints
- pytest for tests, click for CLI
- TDD: write failing test first
