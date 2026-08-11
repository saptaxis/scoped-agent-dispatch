"""The session index — SQLite over normalized records.

Knows nothing about JSONL: readers produce records, this stores and queries them.

Durability note: while raw traces survive in the archive, this file is a cache and
`scad reindex` can rebuild it. Once raw is pruned, `turns` becomes the only copy of
that content and this file joins the backup set. That is why reindex never deletes
and `--rebuild` refuses when any raw is missing.
"""

import collections
import json
import platform
import sqlite3
from pathlib import Path

import click

from scad.archive import STATE_HISTORY_NAME, archive_all, archive_root
from scad.config import get_scad_home
from scad.notes import DEFAULT_KIND, notes_root
from scad.project import resolve_project
from scad.readers import (
    read_claude_any,
    read_claude_history,
    read_codex_rollout,
    read_job_state,
    read_kimi_wire,
    read_notes,
)
from scad.records import (
    GRADE_FULL,
    GRADE_SKELETON,
    KIND_MAIN,
    JobStateRecord,
    NoteRecord,
    SessionRecord,
    TurnRecord,
)

SCHEMA_VERSION = 2
EXTRACTOR_VERSION = 1

# A real `source` value, alongside claude-transcript / claude-subagent /
# claude-history / codex-rollout / kimi-wire: some sessions exist only as job
# state.
SOURCE_JOBSTATE = "claude-jobstate"

# ...and some exist only as a note. See index_notes for why that is a row.
SOURCE_NOTE = "scad-note"

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
  -- `name` is the human's own label, and it has TWO writers: the transcript
  -- reader (the `custom-title` record `/rename` appends — 17 sessions in the
  -- real archive) and the harness's jobs/<short>/state.json (5 named jobs).
  -- Whichever writes non-NULL last wins; neither may blank the other's, which
  -- is why both paths COALESCE. Never derived from `title` — see records.py.
  name              TEXT,
  -- The rest come from the harness's state.json alone. `harness_state` is
  -- structural, observed of a live process. `needs` and `needs_detail` are
  -- MODEL-WRITTEN PROSE — the notes tier, self-report, not trace evidence. Do
  -- not read them as measurements.
  harness_state     TEXT,
  needs             TEXT,
  needs_detail      TEXT,
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

-- No `relation` column: it is derived per query from `parent` and the topics
-- already in the thread (NOTE_RELATION_SQL). Older indexes still carry the
-- column with its authored values in it — nothing reads it, and dropping it
-- would be the one destructive act this file otherwise refuses.
CREATE TABLE IF NOT EXISTS notes (
  session_id TEXT NOT NULL,
  idx        INTEGER NOT NULL,
  ts         INTEGER,
  kind       TEXT,
  topic      TEXT,
  parent     TEXT,
  project    TEXT,               -- authored override; NULL = the session's project
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


# Columns added after the first schema shipped. CREATE TABLE IF NOT EXISTS is a
# no-op on an index that already exists, so without this they would appear only
# on machines that had never indexed anything.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "sessions": {
        "name": "TEXT", "harness_state": "TEXT", "needs": "TEXT", "needs_detail": "TEXT",
    },
    # ALTER TABLE ADD COLUMN does not backfill, and the notes pass resumes from
    # `notes_offset` — so on an index that already has rows, these stay NULL
    # through any number of ordinary reindexes. Only re-reading the note files
    # fills them: `scad reindex --rebuild`, or `DELETE FROM notes; UPDATE
    # sessions SET notes_offset = 0;` followed by `scad reindex`. Both halves of
    # that second recipe are needed — resetting the offset alone re-appends the
    # same records under fresh idx values. Meanwhile queries read `kind` through
    # COALESCE(kind,'info'), so an un-backfilled row answers as the default
    # rather than as nothing.
    "notes": {"kind": "TEXT", "project": "TEXT"},
}


def _migrate(conn) -> None:
    """Add declared columns a live table is missing. Only ever adds."""
    for table, columns in _ADDED_COLUMNS.items():
        present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not present:
            continue                      # table does not exist yet; _SCHEMA made it
        for column, decl in columns.items():
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the index, creating schema if absent."""
    target = Path(path) if path else index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
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
            scad_run_id, cwd, project, title, name, git_branch, started, ended,
            grade, source, outcome, last_stop_reason, n_interrupts,
            n_tool_denials, n_errors, archive_path, source_mtime, source_size,
            parsed_offset, raw_present, extractor_version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(id) DO UPDATE SET
            -- First cwd wins, and later passes may only FILL it, never move it.
            -- Claude Code stamps `cwd` on every record, so a tool call made in
            -- another directory writes that directory into the trace. An
            -- incremental pass parses only the tail, so it can legitimately
            -- begin on one of those foreign records and would otherwise
            -- relabel the whole session. Observed: 1394 records saying
            -- `code/scoped-agent-dispatch` against 182 saying `traitful-docs`,
            -- and the session had moved to the latter. `project` is computed
            -- from `cwd` and is the join key for retrieval, so a wandering cwd
            -- silently moves a session between projects.
            cwd              = COALESCE(sessions.cwd, excluded.cwd),
            -- Pinned the SAME way, and this is the other half of the fix above.
            -- `project` is derived from the cwd of the record a pass happened to
            -- parse, so assigning it plainly let the label keep wandering while
            -- `cwd` no longer did — leaving the two columns of one row
            -- contradicting each other. Observed on the very session the comment
            -- above cites: cwd `.../scoped-agent-dispatch`, project
            -- `traitful-docs`, and `resolve_project(stored cwd)` disagreeing with
            -- both. Because `project` is the retrieval join key, that session's
            -- notes fell out of its own project's listing.
            --
            -- Safe to pin: `scad_run_id` is derived from the archive path and is
            -- identical on every pass, so no later pass knows better. The one
            -- thing it gives up is a marker dropped mid-session taking effect on
            -- the next append — and re-attribution already required `--rebuild`,
            -- since the pass is mtime-based and never re-reads unchanged files.
            project          = COALESCE(sessions.project, excluded.project),
            title            = COALESCE(excluded.title, sessions.title),
            -- COALESCE, not assignment. Two writers share this column (see the
            -- schema), and an incremental pass parses only the tail — which
            -- rarely contains the rename — so assigning would blank a name on
            -- the next quiet append. A new rename is non-NULL and still wins.
            name             = COALESCE(excluded.name, sessions.name),
            git_branch       = COALESCE(excluded.git_branch, sessions.git_branch),
            started          = MIN(COALESCE(sessions.started, excluded.started), excluded.started),
            ended            = MAX(COALESCE(sessions.ended, excluded.ended), excluded.ended),
            grade            = CASE WHEN sessions.grade = ? THEN ? ELSE excluded.grade END,
            source           = CASE WHEN sessions.grade = ? THEN sessions.source ELSE excluded.source END,
            outcome          = excluded.outcome,
            last_stop_reason = excluded.last_stop_reason,
            -- Cumulative over the session's life, so these ADD. An incremental
            -- pass parses only the tail (from parsed_offset), so `excluded`
            -- carries the tail's counts alone; overwriting would erase every
            -- interrupt and error the earlier passes saw. A session that was
            -- interrupted and then grew quietly would read as clean, and once
            -- raw is pruned that wrong count is the only copy left.
            n_interrupts     = sessions.n_interrupts + excluded.n_interrupts,
            n_tool_denials   = sessions.n_tool_denials + excluded.n_tool_denials,
            n_errors         = sessions.n_errors + excluded.n_errors,
            archive_path     = excluded.archive_path,
            source_mtime     = excluded.source_mtime,
            source_size      = excluded.source_size,
            parsed_offset    = excluded.parsed_offset,
            raw_present      = 1
        """,
        (
            session.id, session.kind, session.parent_session_id, session.agent_id,
            session.workflow_id, session.agent, machine, scad_run_id, session.cwd,
            project, session.title, session.name, session.git_branch,
            session.started, session.ended,
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


def apply_job_state(conn, job: JobStateRecord) -> bool:
    """Attach one harness job state to a session, creating the row if need be.

    Assignment, not COALESCE, for the state fields: the newest snapshot is
    authoritative for its session, so a job that has since unblocked must be able
    to clear `needs` rather than keep advertising a resolved one.

    `name` is the exception, because it is the one column this pass does not own.
    The transcript reader writes it too, from `/rename`, and most job snapshots
    carry no name at all — a NULL here means "this job was never named", not
    "forget what the human typed". So the name only ever moves to another
    non-NULL value, whichever writer supplies it last.

    An id with no session INSERTS rather than being ignored. The harness outlives
    transcripts: nd-3 has no transcript and no history.jsonl line on the live
    roots or anywhere in the archive, so its state.json is the only surviving
    record that it ever ran. That is exactly the argument this index already
    accepts for history.jsonl, so job state gets the same standing — otherwise a
    session that HAS a human name is one of the few that is unfindable.

    The inserted row is `grade='skeleton'` (no turns were ever extracted) and
    `raw_present=1` (its source, state-history.jsonl, IS in the archive and the
    row is fully re-derivable from it — a 0 here would make `--rebuild` refuse
    forever over a row that has nothing to lose).
    """
    cursor = conn.execute(
        "UPDATE sessions SET name = COALESCE(?, name), harness_state = ?, "
        "needs = ?, needs_detail = ? WHERE id = ?",
        (job.name, job.state, job.needs, job.detail, job.session_id),
    )
    if cursor.rowcount == 0:
        conn.execute(
            """
            INSERT INTO sessions (
                id, kind, agent, machine, cwd, project, started, ended,
                grade, source, name, harness_state, needs, needs_detail,
                parsed_offset, raw_present, extractor_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,?)
            """,
            (
                job.session_id, KIND_MAIN, "claude", platform.node(), job.cwd,
                resolve_project(job.cwd), job.created_at, job.updated_at,
                GRADE_SKELETON, SOURCE_JOBSTATE, job.name, job.state, job.needs,
                job.detail, EXTRACTOR_VERSION,
            ),
        )
    conn.commit()
    return True


# --- the two notes columns that are computed, not stored ---
#
# Both expect the `notes` table to be aliased `n`, and the project one expects
# `sessions` aliased `s`. They live here rather than inline so the CLI, the
# search and the view page cannot drift into three different answers to the
# same question.

# `relation` in SQL, and the same rule as notes.derived_relation. The EXISTS
# looks at the whole session's rows rather than at insert order, so an
# incremental pass that appends one note gets the same answer a rebuild does.
NOTE_RELATION_SQL = (
    "CASE WHEN COALESCE(n.parent, '') <> '' THEN 'branch' "
    "     WHEN EXISTS (SELECT 1 FROM notes prior "
    "                  WHERE prior.session_id = n.session_id AND prior.idx < n.idx "
    "                    AND prior.topic IS NOT NULL AND prior.topic = n.topic) "
    "     THEN 'continue' "
    "     ELSE 'shift' END AS relation"
)

# A note's project is the writing session's project *unless* the note names one.
# That is the cross-capture case: a session working in project A files a bug
# against project B, and before this it was filed under A because the project
# only ever arrived through this JOIN. Nothing is lost by letting the note win —
# `cwd_at_write` still records where it was actually written.
NOTE_PROJECT_RESOLVED = "COALESCE(n.project, s.project)"
NOTE_PROJECT_SQL = f"{NOTE_PROJECT_RESOLVED} AS project"

# An index row written before `kind` existed reads as the default, not as NULL.
NOTE_KIND_SQL = f"COALESCE(n.kind, '{DEFAULT_KIND}')"


def append_notes(
    conn, session_id: str, notes: list[NoteRecord], note_path: str
) -> int:
    """Append note rows, continuing idx from whatever is already stored.

    The same append-only shape as `append_turns`, and for a stronger reason:
    turns can be re-extracted while raw survives, but a note is self-report with
    no second copy anywhere. Rewriting a row here would be the one destructive
    act in the whole index.
    """
    if not notes:
        return 0
    start = conn.execute(
        "SELECT COALESCE(MAX(idx) + 1, 0) FROM notes WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO notes "
        "(session_id, idx, ts, kind, topic, parent, project, title, tags, entities, "
        " note_path) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (session_id, start + i, n.ts, n.kind, n.topic, n.parent, n.project, n.title,
             json.dumps(list(n.tags)), json.dumps(list(n.entities)), note_path)
            for i, n in enumerate(notes)
        ],
    )
    conn.commit()
    return len(notes)


def _ensure_note_session(conn, session_id: str, agent: str, cwd: str | None) -> None:
    """Make sure a note has a session row to hang off, without disturbing one.

    A note can arrive for a session the index has never seen: the transcript may
    have been pruned before the first scan, or may never have been archived.
    The note is still stored on disk either way — that is the file-is-truth rule
    and it is not negotiable — and the question is only whether it also gets a
    row.

    It does. The reasoning is the one this index already accepted twice, for
    history.jsonl and then for job state: a source that outlives transcripts
    earns a row, because otherwise real work is invisible. Notes outlive
    transcripts by construction — the spec's own words are that a note "can
    arrive when the transcript no longer exists" — and this is the tier that
    can never be re-derived from anything. The tier that is least replaceable
    must not be the tier that is unfindable.

    So: `grade='skeleton'` (no turns were ever extracted), `source='scad-note'`,
    `cwd` from the note's own `cwd_at_write` so `project` still resolves, and
    `raw_present=1` — its source file exists, is never pruned, and the row is
    wholly re-derivable from it, so a 0 here would make `--rebuild` refuse
    forever over a row with nothing to lose.
    """
    if session_row(conn, session_id) is not None:
        return                            # never downgrade a row a transcript filled
    conn.execute(
        """
        INSERT INTO sessions (
            id, kind, agent, machine, cwd, project, grade, source,
            parsed_offset, raw_present, extractor_version
        ) VALUES (?,?,?,?,?,?,?,?,0,1,?)
        """,
        (session_id, KIND_MAIN, agent, platform.node(), cwd,
         resolve_project(cwd), GRADE_SKELETON, SOURCE_NOTE, EXTRACTOR_VERSION),
    )
    conn.commit()


def index_notes(conn) -> collections.Counter:
    """Scan `~/.scad/notes/<agent>/*.jsonl` into the `notes` table.

    `notes_offset` is `parsed_offset` on a different file, and works identically:
    a note file whose size already equals the offset is skipped without being
    opened, and a resumed read appends rows whose idx continues from the maximum.

    This pass deliberately does NOT read the archive. Every other source obeys
    "nothing enters the index that is not in the archive first", because the
    archive is what makes those rows rebuildable. The notes store is already the
    durable home of its own content — copying it into the archive would make a
    second copy of the one thing that has no original elsewhere, and leave two
    files to keep honest instead of one.
    """
    stats = collections.Counter()
    root = notes_root()
    if not root.is_dir():
        return stats

    for shard in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(shard.glob("*.jsonl")):
            stats += index_note_file(conn, path, shard.name)

    return stats


def index_note_file(conn, path: Path, agent: str) -> collections.Counter:
    """Index one note file from wherever its offset left off.

    Split out of the pass above so the WRITE path can index the record it just
    appended without a second implementation of the offset rule. That rule is
    the whole reason this is shared: reading rows in without advancing
    `notes_offset` makes the next pass re-append the same records under fresh
    `idx` values, which is a duplicate that looks like a real second note.
    """
    stats = collections.Counter()
    session_id = path.stem
    row = session_row(conn, session_id)
    size = path.stat().st_size
    if row is not None and row["notes_offset"] >= size:
        return stats

    start = row["notes_offset"] if row is not None else 0
    try:
        notes, end = read_notes(path, start)
    except Exception as exc:          # a note we cannot read must not stop the pass
        click.echo(f"[scad] skipped note {path.name}: {exc}")
        stats["skipped_files"] += 1
        return stats

    if row is None:
        cwd = next((n.cwd_at_write for n in notes if n.cwd_at_write), None)
        _ensure_note_session(conn, session_id, agent, cwd)

    stats["notes"] += append_notes(conn, session_id, notes, str(path))
    conn.execute("UPDATE sessions SET notes_offset = ? WHERE id = ?", (end, session_id))
    conn.commit()
    return stats


def session_row(conn, session_id: str):
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def _run_id_for(rel_parts: tuple) -> str | None:
    """archive/runs/<run-id>/... -> the run id; anything else -> None."""
    return rel_parts[1] if len(rel_parts) > 1 and rel_parts[0] == "runs" else None


def _peek_session_id(conn, path: Path, *, kimi: bool = False) -> str | None:
    """The row id a file maps to, without parsing it.

    Identity is path-derived for Claude (see readers.identity_from_path) and for
    kimi (readers.kimi_identity_from_path); codex rollouts key on session_meta,
    so fall back to the archive path already stored.
    """
    from scad.readers import identity_from_path, kimi_identity_from_path

    ident = kimi_identity_from_path(path) if kimi else identity_from_path(path)
    if ident["kind"] != "main":
        return ident["id"]
    row = conn.execute(
        "SELECT id FROM sessions WHERE archive_path = ?", (str(path),)).fetchone()
    return row["id"] if row else ident["id"]


def reindex(conn=None, *, rebuild: bool = False, force: bool = False,
            archive_first: bool = False) -> dict[str, int]:
    """Scan the archive into the index.

    Incremental by default: a file whose size already equals the session's
    parsed_offset is skipped without being opened. Never deletes; `rebuild`
    is the only destructive path and it refuses when any session's raw is gone,
    because those turns cannot be re-derived from anything.
    """
    conn = conn or connect()

    # Archive before reading. The index reads the ARCHIVE, never the live trace
    # dirs, so without this a reindex after a day's work quietly indexes nothing
    # new and still reports success — the worst kind of failure. This is the
    # spec's invariant: nothing enters the index that is not archived first.
    #
    # Defaults OFF here and ON at the CLI. archive_all() sweeps the real ~/.claude
    # regardless of SCAD_ARCHIVE, so a default of True would make every test that
    # reindexes copy the whole live corpus into its tmpdir. The user-facing
    # command is where the sweep belongs; the library function stays pure.
    if archive_first:
        try:
            archive_all()
        except Exception as exc:            # a full disk must not block indexing
            click.echo(f"[scad] Warning: archive step failed: {exc}")

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
        # Notes rows go too, and safely: the note FILES are truth, are never
        # deleted, and index_notes below reads every one of them back from
        # offset 0. Leaving them would strand rows whose session no longer
        # exists and break the idx continuation on the next append.
        conn.executescript("DELETE FROM notes; DELETE FROM turns; DELETE FROM sessions;")
        conn.commit()

    if not root.is_dir():
        stats.update(index_notes(conn))   # a note does not need an archive to exist
        return stats

    job_states: list[JobStateRecord] = []

    for path in sorted(root.rglob("*.jsonl")):
        rel = path.relative_to(root).parts
        scad_run_id = _run_id_for(rel)
        stat = path.stat()

        if path.name == STATE_HISTORY_NAME:
            # Held back for a second pass: job state is keyed on sessionId, and
            # the row it names may not exist yet. Must also never reach the
            # transcript reader, which would see the snapshot's own sessionId and
            # cwd and forge a turnless row on top of the real session.
            job_states.extend(read_job_state(path)[0])
            continue

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

        # The archive mirrors each root under its own label, so the label is what
        # says which format a file is in.
        kimi = rel[0] == "kimi"
        if rel[0] == "codex":
            reader = read_codex_rollout
        elif kimi:
            reader = read_kimi_wire
        else:
            reader = read_claude_any

        # Incremental: resolve the row this file maps to without parsing it, and
        # skip entirely when nothing has been appended since the last pass.
        probe_id = _peek_session_id(conn, path, kimi=kimi)
        row = session_row(conn, probe_id) if probe_id else None
        if row is not None and row["parsed_offset"] >= stat.st_size:
            continue
        start = row["parsed_offset"] if row is not None else 0

        try:
            session, turns, end = reader(path, start)
            if session is None and start > 0:
                # A format whose identity lives only at the head cannot be read
                # from the middle. codex writes `session_meta` on line 1 and
                # nowhere else, so a tail parse returns nothing at all — and
                # this branch used to fall through to `skipped_lines`, dropping
                # every appended turn AND leaving `parsed_offset` behind the
                # file, so each later pass re-read and re-dropped the same tail.
                # Only `--rebuild` recovered it, against the standing rule that
                # rebuild is for derivation changes and incremental handles
                # growth. Silent, and it argued for a wrong conclusion: the
                # transcript ended on an unanswered user turn, which reads
                # exactly like a session that was interrupted.
                #
                # So re-read whole and keep only what is new. `raw_offset` is
                # the offset of the record that produced each turn, which makes
                # "new" a fact about the file rather than a guess — necessary
                # because `append_turns` numbers by position and cannot tell a
                # duplicate from a fresh turn.
                session, turns, end = reader(path, 0)
                turns = [t for t in turns if (t.raw_offset or 0) >= start]
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

    # Second pass, once every row the scan will create exists.
    for job in job_states:
        if apply_job_state(conn, job):
            stats["named"] += 1

    # Last, for the same reason: a note only inserts a session row when the scan
    # has already had its chance to produce a better one.
    stats.update(index_notes(conn))

    # A Counter, not a plain dict: callers ask for counts that a quiet pass never
    # incremented ("how many sessions?" after a no-op scan), and 0 is the honest
    # answer there rather than a KeyError.
    return stats


_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts
USING fts5(text, content='turns', content_rowid='rowid');
"""


def ensure_fts(conn) -> None:
    """Create and populate the full-text index over turns.text.

    FTS is derived from rows that already exist, so it can be added, dropped, or
    rebuilt at any time without re-reading the archive. It is an external-content
    table: the text lives once, in `turns`.

    Two traps in that arrangement, both of which silently return zero hits:

    1. `SELECT count(*) FROM turns_fts` does NOT count the index — an
       external-content table answers it from the content table, so it reports
       `turns`'s row count whether or not a single term has been indexed. The
       staleness probe is therefore the row count recorded in `meta` at the last
       build, plus whether the virtual table existed at all before this call.
    2. External content does not stay in sync on its own. `reindex` appends to
       `turns` without touching the index, so a count mismatch means a rebuild is
       owed. Rebuilding costs a pass over `turns`, which is why it is gated on the
       count rather than done on every search.
    """
    existed = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns_fts'"
    ).fetchone() is not None
    conn.executescript(_FTS_SCHEMA)

    rows = conn.execute("SELECT count(*) FROM turns").fetchone()[0]
    stored = conn.execute("SELECT value FROM meta WHERE key='fts_rows'").fetchone()
    indexed = int(stored[0]) if stored else -1

    if not existed or indexed != rows:
        conn.execute("INSERT INTO turns_fts(turns_fts) VALUES('rebuild')")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('fts_rows', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(rows),),
        )
    conn.commit()


def _fts_query(raw: str) -> str:
    """Quote user input as FTS5 phrase tokens.

    FTS5 treats bare punctuation as syntax; a stray quote or hyphen in a search
    term would otherwise raise sqlite3.OperationalError instead of finding nothing.
    """
    words = [w.replace('"', "") for w in raw.split()]
    return " ".join(f'"{w}"' for w in words if w)


def session_notes(conn, session_id: str) -> list[dict]:
    """A session's notes, oldest first — the order they were written."""
    rows = conn.execute(
        f"SELECT n.idx, n.ts, {NOTE_KIND_SQL} AS kind, n.topic, {NOTE_RELATION_SQL}, "
        f"       n.parent, n.project, n.title, n.tags, n.entities, n.note_path "
        f"FROM notes n WHERE n.session_id = ? ORDER BY n.idx",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_notes(conn, query: str, *, limit: int = 20) -> list[dict]:
    """Search notes by topic, title, tags, entities and the note's own project.

    Deliberately not FTS. Notes are the authored tier and there are few of them;
    a LIKE over five short columns is exact enough and needs no second index to
    keep in sync with `turns_fts`. If the note count ever reaches the thousands
    this becomes an FTS table over `notes.title`.

    `n.project` is matched, not the resolved one: matching the JOINed project
    would make every note in a project a hit for that project's name, which
    turns a search for a subject into a listing. The authored value is a
    deliberate label and is worth finding by.
    """
    like = f"%{query.lower()}%"
    rows = conn.execute(
        f"SELECT n.session_id, n.idx, n.ts, {NOTE_KIND_SQL} AS kind, n.topic, "
        f"       {NOTE_RELATION_SQL}, n.parent, n.title, n.tags, n.entities, "
        f"       n.note_path, {NOTE_PROJECT_SQL}, s.name, s.agent "
        f"FROM notes n LEFT JOIN sessions s ON s.id = n.session_id "
        f"WHERE lower(COALESCE(n.topic,'')) LIKE ? OR lower(COALESCE(n.title,'')) LIKE ? "
        f"   OR lower(COALESCE(n.tags,'')) LIKE ? OR lower(COALESCE(n.entities,'')) LIKE ? "
        f"   OR lower(COALESCE(n.project,'')) LIKE ? "
        f"ORDER BY n.ts DESC LIMIT ?",
        (like, like, like, like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def known_projects(conn) -> set[str]:
    """Every project name the index has seen, from sessions and from notes.

    The write path uses this to tell a caller its `project` looks unfamiliar.
    Sessions alone would be the wrong set twice over: `scad project ls` counts
    sessions, so a real project nobody has opened a session in yet is missing
    from it, and a note that already named a project is itself evidence the
    name is in use.
    """
    rows = conn.execute(
        "SELECT DISTINCT project FROM sessions WHERE COALESCE(project,'') <> '' "
        "UNION "
        "SELECT DISTINCT project FROM notes WHERE COALESCE(project,'') <> ''"
    ).fetchall()
    return {r[0] for r in rows}


def search_turns(conn, query: str, *, project=None, kind=None, limit: int = 20):
    """Full-text search over turns, newest first, with the session joined in."""
    ensure_fts(conn)
    match = _fts_query(query)
    if not match:
        return []

    where, params = ["turns_fts MATCH ?"], [match]
    if project:
        where.append("s.project = ?")
        params.append(project)
    if kind:
        where.append("t.kind = ?")
        params.append(kind)

    return conn.execute(
        f"""
        SELECT t.session_id, t.idx, t.kind, t.role, t.ts, t.text,
               s.project, s.agent, s.title
        FROM turns_fts
        JOIN turns t ON t.rowid = turns_fts.rowid
        JOIN sessions s ON s.id = t.session_id
        WHERE {' AND '.join(where)}
        ORDER BY t.ts DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()


def session_turns(conn, session_id: str, *, kind=None, role=None, limit=None):
    """Read a session's turns in order."""
    where, params = ["session_id = ?"], [session_id]
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if role:
        where.append("role = ?")
        params.append(role)
    sql = (f"SELECT idx, ts, role, kind, tool_name, text, truncated FROM turns "
           f"WHERE {' AND '.join(where)} ORDER BY idx")
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()
