"""Append-only archive of agent trace files.

Retention settings are a postponement, not a solution: they are per-machine and
manual, they belong to the agent vendor rather than to us, and they do not cover
`scad session clean`, which destroys a run's traces outright. This module copies
raw JSONL somewhere nothing deletes it.

Invariants, in order of importance:

1. Nothing here ever deletes, truncates, or shortens an archived file.
2. A copy never runs past the last complete newline — a partial trailing line
   recorded as the archived size welds two records together on the next append.
3. Copy, never move. The live tree is left exactly as found.

Plain filesystem code: no Docker, no scad.container, no scad.vm.
"""

import os
from pathlib import Path

from scad.config import get_scad_home

MARKER_NAME = "DO-NOT-DELETE.md"

MARKER_TEXT = """\
# scad trace archive — do not delete

This directory holds the only surviving copy of agent session traces.

Agents prune their own transcripts (Claude Code defaults to 30 days), and
`scad session clean` destroys a run's traces along with its container. Once
those are gone, the contents of this directory cannot be regenerated from
anything else on this machine.

`scad gc` and `scad session clean` never touch this directory. Neither should you.

Rebuild the session index from here with: scad reindex
"""


def archive_root() -> Path:
    """Where archived traces live. SCAD_ARCHIVE overrides the default."""
    env = os.environ.get("SCAD_ARCHIVE")
    if env:
        return Path(env).expanduser()
    return get_scad_home() / "archive"


def ensure_archive_root() -> Path:
    """Create the archive root and its marker if absent. Never rewrites either."""
    root = archive_root()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER_NAME
    if not marker.exists():
        marker.write_text(MARKER_TEXT)
    return root


def dest_for(src: Path, src_root: Path, label: str) -> Path:
    """Map a source file to its archive path, mirroring the source layout.

    The mirror is what makes re-running a no-op and provenance visible from the
    path alone: archive/claude/projects/<encoded-cwd>/<uuid>.jsonl came from
    ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl.
    """
    try:
        rel = Path(src).relative_to(src_root)
    except ValueError as exc:
        raise ValueError(f"{src} is not under {src_root}") from exc
    return archive_root() / label / rel
