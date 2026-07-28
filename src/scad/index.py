"""The session index — SQLite over normalized records.

Knows nothing about JSONL: readers produce records, this stores and queries them.

Durability note: while raw traces survive in the archive, this file is a cache and
`scad reindex` can rebuild it. Once raw is pruned, `turns` becomes the only copy of
that content and this file joins the backup set. That is why reindex never deletes
and `--rebuild` refuses when any raw is missing.
"""

import sqlite3
from pathlib import Path

from scad.config import get_scad_home
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
