"""Generic target resolution — the precedence engine.

scad owns the engine; consumers own the config. This module must not import
anything from scad.*: it has no domain knowledge, which is what lets external
consumers (e.g. interior-viz) use it with their own markers and targets.

The engine is read-only and total: it never writes to the filesystem, never
raises, and never exits. "Nothing resolved" is a returned value, so the engine
can be mapped over thousands of recorded paths during a reindex.
"""

from dataclasses import dataclass, field
from pathlib import Path

# --- the matched_by vocabulary -------------------------------------------
# PUBLIC CONTRACT. Agents string-compare these. Renaming one is a breaking
# change. Documented in `scad resolve --help`.

EXPLICIT = "explicit"
ASK = "ask"
UNRESOLVED = "unresolved"


def marker(name: str) -> str:
    """Build the matched_by value for a marker hit, e.g. 'marker:design.yaml'."""
    return f"marker:{name}"


@dataclass(frozen=True)
class ResolveConfig:
    """A consumer's resolution instance — declarative data, no behaviour.

    markers:      walk-up sentinel filenames; () skips the tier.
    use_git_root: walk up for .git (a directory in a clone, a file in a worktree).
    allow_ask:    may prompt when nothing was discovered.
    """

    markers: tuple[str, ...] = ()
    use_git_root: bool = False
    allow_ask: bool = False


@dataclass(frozen=True)
class Resolution:
    """The engine's answer.

    path:       the resolved directory, or None when nothing matched.
    matched_by: which tier answered — see the vocabulary above.
    tried:      tiers attempted, in order, for the failure message.
    """

    path: Path | None
    matched_by: str
    tried: tuple[str, ...] = field(default=())
