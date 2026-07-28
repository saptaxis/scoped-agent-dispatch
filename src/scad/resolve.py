"""Generic target resolution — the precedence engine.

scad owns the engine; consumers own the config. This module must not import
anything out of the scad.* package: it has no domain knowledge, which is what
lets external consumers (e.g. interior-viz) use it with their own markers and
targets. (The isolation guard test string-searches this file, so the prose here
deliberately avoids writing an import-like phrase.)

The engine is read-only and total: it never writes to the filesystem, never
raises, and never exits. "Nothing resolved" is a returned value, so the engine
can be mapped over thousands of recorded paths during a reindex.
"""

import sys
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


def _ancestors(start: Path) -> list[Path]:
    """The directory itself and every parent, nearest first.

    Tolerates a path that does not exist — this engine is mapped over recorded
    cwds from old sessions, where the directory may be long gone.
    """
    here = Path(start).expanduser()
    try:
        here = here.resolve()
    except OSError:
        here = here.absolute()
    return [here, *here.parents]


GIT = ".git"


def _git_root(d: Path) -> Path | None:
    """Return the repository root if `d` holds a .git, else None.

    A worktree's .git is a FILE containing `gitdir: <path>` pointing at
    <repo>/.git/worktrees/<name>. We chase that pointer so a session run from a
    worktree attributes to the repository it belongs to, not to the worktree —
    the worktree name survives in the session's raw cwd either way, so this
    direction loses no information while the reverse would destroy grouping.

    Pure Python, no subprocess: this must work on recorded paths where shelling
    out to git is not viable.
    """
    dot_git = d / GIT

    if dot_git.is_dir():
        return d

    if dot_git.is_file():
        try:
            text = dot_git.read_text().strip()
        except OSError:
            return None
        if not text.startswith("gitdir:"):
            return None
        pointer = Path(text.split(":", 1)[1].strip()).expanduser()
        if not pointer.is_absolute():
            pointer = (d / pointer).resolve()
        # pointer == <repo>/.git/worktrees/<name>; the repo is three levels up.
        if pointer.parent.name == "worktrees" and pointer.parent.parent.name == GIT:
            return pointer.parent.parent.parent
        return None

    return None


def resolve(
    cfg: ResolveConfig,
    start: Path | None = None,
    *,
    explicit: Path | None = None,
    interactive: bool = True,
) -> Resolution:
    """Resolve a target directory by fixed precedence.

    explicit -> markers (walk-up) -> git-root -> ask -> unresolved

    `start` is a parameter rather than an ambient cwd read, so the function is
    pure with respect to its inputs and can be mapped over recorded paths. The
    cwd default is taken once, here at the boundary.
    """
    tried: list[str] = []

    if explicit is not None:
        tried.append(EXPLICIT)
        return Resolution(
            path=Path(explicit).expanduser().resolve(),
            matched_by=EXPLICIT,
            tried=tuple(tried),
        )

    start = Path.cwd() if start is None else start
    dirs = _ancestors(start)

    if cfg.markers:
        for name in cfg.markers:
            tried.append(marker(name))
        for d in dirs:
            for name in cfg.markers:
                if (d / name).exists():
                    return Resolution(
                        path=d, matched_by=marker(name), tried=tuple(tried)
                    )

    if cfg.use_git_root:
        tried.append(marker(GIT))
        for d in dirs:
            root = _git_root(d)
            if root is not None:
                return Resolution(
                    path=root, matched_by=marker(GIT), tried=tuple(tried)
                )

    if cfg.allow_ask:
        if not interactive or not sys.stdin.isatty():
            tried.append("ask (skipped: no tty)" if interactive else "ask (disabled)")
        else:
            tried.append(ASK)
            hint = cfg.markers[0] if cfg.markers else None
            suffix = f" (or create {hint} at the root)" if hint else ""
            reply = input(f"No target found. Enter the directory{suffix}: ").strip()
            if reply:
                answer = Path(reply).expanduser()
                try:
                    answer = answer.resolve()
                except OSError:
                    pass
                if answer.is_dir():
                    return Resolution(
                        path=answer, matched_by=ASK, tried=tuple(tried)
                    )

    return Resolution(path=None, matched_by=UNRESOLVED, tried=tuple(tried))


def announce(value: object, res: Resolution, label: str) -> None:
    """Print one line to stderr saying what was resolved and how.

    Announce is the consumer's job because only the consumer knows the semantic
    label and value ("using project: foo", not a raw path). It goes to stderr so
    stdout stays a clean machine-readable channel.
    """
    print(f"using {label}: {value} (via {res.matched_by})", file=sys.stderr)


def require(res: Resolution) -> Path:
    """Return the resolved path, or exit 1 with what was tried.

    This is the single renderer for `tried`; the CLI calls it too, so there is
    exactly one failure message in the system. Exit code is 1 (not 2) because
    Click reserves 2 for UsageError — unresolved and "you called it wrong" must
    stay distinguishable by exit code.
    """
    if res.path is not None:
        return res.path
    print("scad resolve: no target.", file=sys.stderr)
    print(f"  tried: {', '.join(res.tried) or '(nothing configured)'}", file=sys.stderr)
    print(
        "  options: pass an explicit path, pass --start DIR, "
        "or create a marker file at the root.",
        file=sys.stderr,
    )
    raise SystemExit(1)
