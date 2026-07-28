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
