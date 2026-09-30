"""Path aliases: where a recorded directory is now, after it moved.

`project` is derived from a session's recorded cwd by resolving it on today's
filesystem, so when the directory moves the evidence is gone, and a
`reindex --rebuild` turns a correct label into `unfiled`. The recorded path
cannot be corrected at the source: it is stamped on every transcript record.
So the correction is an authored file, `~/.scad/aliases`, of `old -> new`
prefix rules, consulted only when the recorded directory no longer exists.

It lives outside the index because `--rebuild` wipes every table, and a
correction stored there would be lost to the operation it exists to survive.

Depends on nothing in scad but `get_scad_home`: project resolution, the export
and resume all ask this module the one question, so they cannot disagree.
"""

import functools
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from scad.config import get_scad_home

ALIAS_FILE = "aliases"
SEPARATOR = " -> "

# `#` starts a comment at the start of a line or after whitespace, so a path
# that happens to contain one (`/a/x#1`) keeps it.
_COMMENT = re.compile(r"(^|\s)#.*$")


@dataclass(frozen=True)
class Rule:
    old: Path            # normalised
    new: Path            # normalised
    old_written: str     # the side as the line had it, for display
    new_written: str
    line: int            # 1-based


def alias_path() -> Path:
    return get_scad_home() / ALIAS_FILE


def normalise(path) -> Path:
    """Resolve the deepest existing ancestor through symlinks, keep the rest.

    `realpath` in its default mode already does exactly that: it follows every
    leading component that exists and appends the missing remainder as is. Not
    `Path.resolve()`, which raises on a symlink loop, and this runs over
    recorded paths from sessions long gone.
    """
    return Path(os.path.realpath(os.path.expanduser(os.fspath(path))))


def _warn(source: str, line: int, reason: str) -> None:
    # stderr only: `session ls --json` triggers a load, and its stdout is data.
    print(f"[scad] {source}:{line}: skipped: {reason}", file=sys.stderr)


def parse(text: str, source: str) -> list[Rule]:
    """The rules in `text`. A bad line is skipped with a warning, never fatal:
    the file is hand-edited, and one typo must not cost a whole reindex."""
    out: list[Rule] = []
    seen: dict[Path, int] = {}
    for n, raw in enumerate(text.splitlines(), start=1):
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        parts = line.split(SEPARATOR)
        if len(parts) != 2:
            _warn(source, n, "no ' -> ' separator" if len(parts) < 2
                  else "more than one ' -> '")
            continue
        old_w, new_w = (p.strip() for p in parts)
        bad = next((side for side, text_ in (("old", old_w), ("new", new_w))
                    if not text_ or not os.path.isabs(os.path.expanduser(text_))), None)
        if bad:
            _warn(source, n, f"{bad} side is not an absolute path")
            continue
        old, new = normalise(old_w), normalise(new_w)
        if old == new:
            _warn(source, n, "maps a path to itself")
            continue
        if old in seen:
            _warn(source, n, f"same old path as line {seen[old]}")
            continue
        seen[old] = n
        out.append(Rule(old=old, new=new, old_written=old_w, new_written=new_w, line=n))
    return out


@functools.lru_cache(maxsize=None)
def _load(path: Path) -> tuple[Rule, ...]:
    """Read once per process: a reindex resolves thousands of rows. Keyed on
    the path, so a different SCAD_HOME is a different read."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except (OSError, UnicodeDecodeError) as e:
        print(f"[scad] {path}: not read: {e}", file=sys.stderr)
        return ()
    return tuple(parse(text, str(path)))


def rules() -> tuple[Rule, ...]:
    return _load(alias_path())


def reset() -> None:
    _load.cache_clear()


def rule_for(path: Path) -> Rule | None:
    """The rule with the longest old side that `path` is at or under.
    Component-wise: a rule for /a/foo never matches /a/foobar."""
    hits = [r for r in rules() if path == r.old or path.is_relative_to(r.old)]
    return max(hits, key=lambda r: len(r.old.parts), default=None)


def _apply(path: Path) -> tuple[Path, Rule] | None:
    # The rule's new side has to exist, not the whole translated path: a
    # session in a since-deleted worktree still walks up to the new root. And
    # no fallback to a shorter rule; a nested rule speaks for its subtree.
    rule = rule_for(path)
    if rule is None or not rule.new.exists():
        return None
    return rule.new / path.relative_to(rule.old), rule


def alias_for(path: Path) -> Path | None:
    hit = _apply(path)
    return hit[0] if hit else None


def locate(cwd) -> tuple[Path, Rule | None]:
    """Where a recorded directory is now, and the rule that said so.

    A path that exists is answered by the filesystem before any rule is read,
    so no rule can redirect a live session.
    """
    here = normalise(cwd)
    if here.exists():
        return here, None
    hit = _apply(here)
    return hit if hit else (here, None)


def current_cwd(cwd: str | None) -> str | None:
    # `Path("")` is `.`, which would become the process's own directory.
    if not cwd:
        return cwd
    return str(locate(cwd)[0])


def status(rule: Rule) -> str:
    """ok: the case the rule is for. stale: the old side still exists, so the
    rule is inert. broken: the new side is missing, so it cannot help."""
    if rule.old.exists():
        return "stale"
    if not rule.new.exists():
        return "broken"
    return "ok"
