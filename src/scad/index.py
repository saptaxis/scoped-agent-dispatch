"""The session index — SQLite over normalized records.

Knows nothing about JSONL: readers produce records, this stores and queries them.

Durability note: while raw traces survive in the archive, this file is a cache and
`scad reindex` can rebuild it. Once raw is pruned, `turns` becomes the only copy of
that content and this file joins the backup set. That is why reindex never deletes
and `--rebuild` refuses when any raw is missing.
"""

import collections
import platform
import sqlite3
from pathlib import Path

import click

from scad.archive import archive_root
from scad.config import get_scad_home
from scad.project import resolve_project
from scad.readers import read_claude_any, read_claude_history, read_codex_rollout
from scad.records import GRADE_FULL, SessionRecord, TurnRecord

SCHEMA_VERSION = 1
EXTRACTOR_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id                TEXT PRIMARY KEY,
  kind              TEXT NOT NULL,
  parent_session_id TEXT,
  agent_id          TEXT,
  workflow_id       TEXT,
  agent             TEXT NOT NULL,
  machine           TEXT NOT NULL,
  scad_run_id       TEXT,
  cwd               TEXT,
  project           TEXT,
  title             TEXT,
  git_branch        TEXT,
  started           INTEGER,
  ended             INTEGER,
  n_turns           INTEGER NOT NULL DEFAULT 0,
  grade             TEXT NOT NULL,
  source            TEXT NOT NULL,
  outcome           TEXT,               -- derived terminal state; see readers
  last_stop_reason  TEXT,               -- end_turn | tool_use | stop_sequence
  n_interrupts      INTEGER NOT NULL DEFAULT 0,
  n_tool_denials    INTEGER NOT NULL DEFAULT 0,
  n_errors          INTEGER NOT NULL DEFAULT 0,
  archive_path      TEXT,
  source_mtime      INTEGER,
  source_size       INTEGER,
  parsed_offset     INTEGER NOT NULL DEFAULT 0,
  notes_offset      INTEGER NOT NULL DEFAULT 0,
  raw_present       INTEGER NOT NULL DEFAULT 1,
  extractor_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS turns (
  session_id TEXT NOT NULL,
  idx        INTEGER NOT NULL,
  ts         INTEGER,
  role       TEXT,
  kind       TEXT,
  tool_name  TEXT,
  text       TEXT,
  truncated  INTEGER NOT NULL DEFAULT 0,
  raw_offset INTEGER,
  PRIMARY KEY (session_id, idx)
);

CREATE TABLE IF NOT EXISTS notes (
  session_id TEXT NOT NULL,
  idx        INTEGER NOT NULL,
  ts         INTEGER,
  topic      TEXT,
  relation   TEXT,
  parent     TEXT,
  title      TEXT,
  tags       TEXT,
  entities   TEXT,
  note_path  TEXT NOT NULL,
  PRIMARY KEY (session_id, idx)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started);
CREATE INDEX IF NOT EXISTS idx_sessions_agent   ON sessions(agent);
CREATE INDEX IF NOT EXISTS idx_sessions_machine ON sessions(machine);
CREATE INDEX IF NOT EXISTS idx_sessions_parent  ON sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_kind    ON sessions(kind);
CREATE INDEX IF NOT EXISTS idx_turns_kind       ON turns(kind);
CREATE INDEX IF NOT EXISTS idx_sessions_outcome ON sessions(outcome);
"""


def index_path() -> Path:
    return get_scad_home() / "index.sqlite"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the index, creating schema if absent."""
    target = Path(path) if path else index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def upsert_session(
    conn, session: SessionRecord, *, machine: str, project: str,
    archive_path: str, source_size: int, source_mtime: int, parsed_offset: int,
    scad_run_id: str | None = None,
) -> None:
    """Insert or update a session row.

    Grade only ever moves skeleton -> full. Roots are scanned in arbitrary order,
    so a history.jsonl pass must never downgrade a row a transcript already filled.
    """
    conn.execute(
        """
        INSERT INTO sessions (
            id, kind, parent_session_id, agent_id, workflow_id, agent, machine,
            scad_run_id, cwd, project, title, git_branch, started, ended,
            grade, source, outcome, last_stop_reason, n_interrupts,
            n_tool_denials, n_errors, archive_path, source_mtime, source_size,
            parsed_offset, raw_present, extractor_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(id) DO UPDATE SET
            cwd              = COALESCE(excluded.cwd, sessions.cwd),
            project          = excluded.project,
            title            = COALESCE(excluded.title, sessions.title),
            git_branch       = COALESCE(excluded.git_branch, sessions.git_branch),
            started          = MIN(COALESCE(sessions.started, excluded.started), excluded.started),
            ended            = MAX(COALESCE(sessions.ended, excluded.ended), excluded.ended),
            grade            = CASE WHEN sessions.grade = ? THEN ? ELSE excluded.grade END,
            source           = CASE WHEN sessions.grade = ? THEN sessions.source ELSE excluded.source END,
            outcome          = excluded.outcome,
            last_stop_reason = excluded.last_stop_reason,
            n_interrupts     = excluded.n_interrupts,
            n_tool_denials   = excluded.n_tool_denials,
            n_errors         = excluded.n_errors,
            archive_path     = excluded.archive_path,
            source_mtime     = excluded.source_mtime,
            source_size      = excluded.source_size,
            parsed_offset    = excluded.parsed_offset,
            raw_present      = 1
        """,
        (
            session.id, session.kind, session.parent_session_id, session.agent_id,
            session.workflow_id, session.agent, machine, scad_run_id, session.cwd,
            project, session.title, session.git_branch, session.started, session.ended,
            session.grade, session.source, session.outcome, session.last_stop_reason,
            session.n_interrupts, session.n_tool_denials, session.n_errors,
            archive_path, source_mtime, source_size,
            parsed_offset, EXTRACTOR_VERSION,
            GRADE_FULL, GRADE_FULL, GRADE_FULL,
        ),
    )
    conn.commit()


def append_turns(conn, session_id: str, turns: list[TurnRecord]) -> int:
    """Append turns, continuing idx from whatever is already stored.

    Never rewrites an existing row: a session whose raw has since been pruned
    keeps the turns extracted when raw was present.
    """
    if not turns:
        return 0
    start = conn.execute(
        "SELECT COALESCE(MAX(idx) + 1, 0) FROM turns WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO turns "
        "(session_id, idx, ts, role, kind, tool_name, text, truncated, raw_offset) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (session_id, start + i, t.ts, t.role, t.kind, t.tool_name,
             t.text, 1 if t.truncated else 0, t.raw_offset)
            for i, t in enumerate(turns)
        ],
    )
    conn.execute(
        "UPDATE sessions SET n_turns = (SELECT count(*) FROM turns WHERE session_id = ?) "
        "WHERE id = ?",
        (session_id, session_id),
    )
    conn.commit()
    return len(turns)


def session_row(conn, session_id: str):
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def _run_id_for(rel_parts: tuple) -> str | None:
    """archive/runs/<run-id>/... -> the run id; anything else -> None."""
    return rel_parts[1] if len(rel_parts) > 1 and rel_parts[0] == "runs" else None


def _peek_session_id(conn, path: Path) -> str | None:
    """The row id a file maps to, without parsing it.

    Identity is path-derived for Claude (see readers.identity_from_path); codex
    rollouts key on session_meta, so fall back to the archive path already stored.
    """
    from scad.readers import identity_from_path

    ident = identity_from_path(path)
    if ident["kind"] != "main":
        return ident["id"]
    row = conn.execute(
        "SELECT id FROM sessions WHERE archive_path = ?", (str(path),)).fetchone()
    return row["id"] if row else ident["id"]


def reindex(conn=None, *, rebuild: bool = False, force: bool = False) -> dict[str, int]:
    """Scan the archive into the index.

    Incremental by default: a file whose size already equals the session's
    parsed_offset is skipped without being opened. Never deletes; `rebuild`
    is the only destructive path and it refuses when any session's raw is gone,
    because those turns cannot be re-derived from anything.
    """
    conn = conn or connect()
    root = archive_root()
    stats = collections.Counter()
    machine = platform.node()

    if rebuild:
        missing = conn.execute(
            "SELECT count(*) FROM sessions WHERE raw_present = 0").fetchone()[0]
        if missing and not force:
            raise click.ClickException(
                f"{missing} session(s) have no raw left in the archive; --rebuild would "
                "destroy the only copy of their turns. Re-run with --force to override."
            )
        conn.executescript("DELETE FROM turns; DELETE FROM sessions;")
        conn.commit()

    if not root.is_dir():
        return stats

    for path in sorted(root.rglob("*.jsonl")):
        rel = path.relative_to(root).parts
        scad_run_id = _run_id_for(rel)
        stat = path.stat()

        if path.name == "history.jsonl":
            sessions, end = read_claude_history(path)
            for s in sessions:
                if session_row(conn, s.id) is not None:
                    continue                     # a transcript already told us more
                upsert_session(
                    conn, s, machine=machine,
                    project=resolve_project(s.cwd, scad_run_id=scad_run_id),
                    archive_path=str(path), source_size=stat.st_size,
                    source_mtime=int(stat.st_mtime), parsed_offset=0,
                    scad_run_id=scad_run_id,
                )
                stats["sessions"] += 1
            continue

        reader = read_codex_rollout if rel[0] == "codex" else read_claude_any

        # Incremental: resolve the row this file maps to without parsing it, and
        # skip entirely when nothing has been appended since the last pass.
        probe_id = _peek_session_id(conn, path)
        row = session_row(conn, probe_id) if probe_id else None
        if row is not None and row["parsed_offset"] >= stat.st_size:
            continue
        start = row["parsed_offset"] if row is not None else 0

        try:
            session, turns, end = reader(path, start)
        except Exception as exc:                 # a file we cannot read must not stop the scan
            click.echo(f"[scad] skipped {path.name}: {exc}")
            stats["skipped_files"] += 1
            continue

        if session is None:
            stats["skipped_lines"] += 1
            continue

        existed = session_row(conn, session.id) is not None
        upsert_session(
            conn, session, machine=machine,
            project=resolve_project(session.cwd, scad_run_id=scad_run_id),
            archive_path=str(path), source_size=stat.st_size,
            source_mtime=int(stat.st_mtime), parsed_offset=end,
            scad_run_id=scad_run_id,
        )
        if not existed:
            stats["sessions"] += 1
        stats["turns"] += append_turns(conn, session.id, turns)
        stats["files"] += 1

    # A Counter, not a plain dict: callers ask for counts that a quiet pass never
    # incremented ("how many sessions?" after a no-op scan), and 0 is the honest
    # answer there rather than a KeyError.
    return stats
