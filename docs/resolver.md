# The resolver engine

scad owns the resolution *engine*; each consumer owns its *config*. The engine
knows about precedence, walk-up, and git; it knows nothing about projects,
apartments, or sessions.

## Python

```python
from scad import ResolveConfig, resolve, require, announce

VIZ = ResolveConfig(markers=(".viz-root", "design.yaml"), allow_ask=True)

def resolve_root(explicit=None):
    res = resolve(VIZ, explicit=explicit)
    root = require(res)          # exits 1 with what was tried, if unresolved
    announce(root, res, "root")  # one line to stderr — never resolve silently
    return root
```

Every entry point starts with `root = resolve_root(args.root)`. That one line is
the whole guarantee: the path is computed on every call, never recalled from a
model's context.

## CLI

```bash
scad resolve --marker design.yaml --marker .viz-root --ask --label root
scad resolve --marker design.yaml --json      # {path, matched_by, tried}
```

Path on stdout, announce on stderr, exit `0` resolved / `1` unresolved / `2` usage.

## Precedence

`explicit -> markers (walk-up, nearest ancestor wins) -> git-root -> ask -> unresolved`

## matched_by (stable public contract)

| Value | Meaning |
|---|---|
| `explicit` | the caller passed the target |
| `marker:<file>` | walk-up found `<file>` |
| `marker:.git` | walk-up found a repo; a worktree resolves to its **repository** |
| `ask` | answered interactively |
| `unresolved` | nothing matched; `path` is `None` |

## What the engine will not do

- Write anything. Phase 0 is read-only; the interactive answer is not persisted.
- Guess. There is no `.` or `~` fallback — unresolved is returned, never assumed.
- Raise. `resolve()` is total, so it can be mapped over thousands of recorded paths.
- Derive a name. It returns a directory; turning that into a key is the consumer's job.
