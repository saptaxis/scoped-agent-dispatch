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

import collections
import os
from dataclasses import dataclass
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


PREFIX_CHECK_BYTES = 8192
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ArchiveResult:
    action: str          # created | appended | skipped | forked
    src: Path
    dest: Path
    copied: int = 0


def _last_newline_end(path: Path, start: int, end: int) -> int:
    """Offset just past the final b'\\n' in [start, end), else `start`.

    Copying past this point is the one way to corrupt the archive permanently.
    """
    if end <= start:
        return start
    with path.open("rb") as fh:
        pos = end
        while pos > start:
            step = min(_CHUNK, pos - start)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step)
            idx = buf.rfind(b"\n")
            if idx != -1:
                return pos + idx + 1
    return start


def _read_at(path: Path, offset: int, size: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(offset)
        return fh.read(size)


def _copy_range(src: Path, dest: Path, start: int, end: int) -> int:
    """Append src[start:end] to dest. Only ever extends dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with src.open("rb") as rfh, dest.open("ab") as wfh:
        rfh.seek(start)
        remaining = end - start
        while remaining > 0:
            buf = rfh.read(min(_CHUNK, remaining))
            if not buf:
                break
            wfh.write(buf)
            written += len(buf)
            remaining -= len(buf)
    return written


def _prefix_matches(src: Path, dest: Path, have: int) -> bool:
    """Is `dest` still a prefix of `src`?

    Size alone cannot tell "appended" from "rewritten to something larger", and
    appending onto a rewritten file produces silent garbage. Checking the window
    immediately before the append point catches that cheaply.
    """
    window = min(PREFIX_CHECK_BYTES, have)
    start = have - window
    return _read_at(src, start, window) == _read_at(dest, start, window)


def _fork(src: Path, dest: Path, have: int) -> ArchiveResult:
    """Source is no longer an extension of what we hold: rewrite or rotation.

    Keep the existing archive untouched and put the new content beside it. This
    should be rare; if it stops being rare, the append assumption is wrong and
    the design needs revisiting rather than the data being quietly replaced.
    """
    mtime = int(src.stat().st_mtime)
    sidecar = dest.with_name(f"{dest.stem}.{mtime}{dest.suffix}")
    src_size = src.stat().st_size
    end = _last_newline_end(src, 0, src_size)

    if sidecar.exists() and sidecar.stat().st_size >= end:
        return ArchiveResult("forked", src, sidecar, 0)

    if sidecar.exists():
        sidecar.unlink()   # partial sidecar from an interrupted run; dest is untouched

    copied = _copy_range(src, sidecar, 0, end)
    return ArchiveResult("forked", src, sidecar, copied)


def archive_file(src: Path, dest: Path) -> ArchiveResult:
    """Copy new complete lines of `src` into `dest`. Never shortens `dest`."""
    src_size = src.stat().st_size
    have = dest.stat().st_size if dest.exists() else 0

    if have > src_size:
        return _fork(src, dest, have)

    if have == src_size:
        return ArchiveResult("skipped", src, dest, 0)

    if have and not _prefix_matches(src, dest, have):
        return _fork(src, dest, have)

    end = _last_newline_end(src, have, src_size)
    if end <= have:
        return ArchiveResult("skipped", src, dest, 0)

    copied = _copy_range(src, dest, have, end)
    return ArchiveResult("created" if have == 0 else "appended", src, dest, copied)


def archive_tree(src_root: Path, label: str) -> list[ArchiveResult]:
    """Archive every *.jsonl under `src_root` into archive/<label>/…

    A missing root is normal, not an error — not every machine runs codex.
    """
    src_root = Path(src_root)
    if not src_root.is_dir():
        return []
    ensure_archive_root()
    results = []
    for src in sorted(src_root.rglob("*.jsonl")):
        if not src.is_file():
            continue
        results.append(archive_file(src, dest_for(src, src_root, label)))
    return results


def archive_run(run_id: str) -> list[ArchiveResult]:
    """Archive one scad run's traces, labelled by run id.

    A run directory is an ordinary ~/.claude — transcripts plus its own
    history.jsonl — so the same tree sweep applies.
    """
    run_claude = get_scad_home() / "runs" / run_id / "claude"
    return archive_tree(run_claude, f"runs/{run_id}")


def archive_all() -> list[ArchiveResult]:
    """Sweep every root: host Claude, host codex, and each scad run."""
    home = Path.home()
    results = archive_tree(home / ".claude", "claude")
    results += archive_tree(home / ".codex" / "sessions", "codex")
    runs = get_scad_home() / "runs"
    if runs.is_dir():
        for entry in sorted(runs.iterdir()):
            if entry.is_dir():
                results += archive_run(entry.name)
    return results


def summarize(results: list[ArchiveResult]) -> dict[str, int]:
    """Count results by action, for reporting."""
    return dict(collections.Counter(r.action for r in results))
