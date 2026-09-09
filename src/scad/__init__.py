"""scoped-agent-dispatch — dispatch Claude Code agents in isolated Docker containers."""

from scad.resolve import (
    ASK,
    EXPLICIT,
    UNRESOLVED,
    ResolveConfig,
    Resolution,
    announce,
    marker,
    require,
    resolve,
)

__version__ = "0.4.0"

__all__ = [
    "ASK",
    "EXPLICIT",
    "UNRESOLVED",
    "ResolveConfig",
    "Resolution",
    "announce",
    "marker",
    "require",
    "resolve",
    "__version__",
]
