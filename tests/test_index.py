"""Tests for the session index."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from scad.index import SCHEMA_VERSION, connect, index_path


class TestSchema:
    def test_index_lives_under_scad_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        assert index_path() == tmp_path / ".scad" / "index.sqlite"

    def test_connect_creates_tables(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"sessions", "turns", "notes", "meta"} <= names

    def test_sessions_columns(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert {
            "id", "kind", "parent_session_id", "agent_id", "workflow_id",
            "agent", "machine", "scad_run_id", "cwd", "project", "title",
            "git_branch", "started", "ended", "n_turns", "grade", "source",
            "archive_path", "source_mtime", "source_size", "parsed_offset",
            "notes_offset", "raw_present", "extractor_version",
            "outcome", "last_stop_reason", "n_interrupts", "n_tool_denials", "n_errors",
        } <= cols

    def test_turns_columns_and_key(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        assert {"session_id", "idx", "ts", "role", "kind", "tool_name",
                "text", "truncated", "raw_offset"} <= cols

    def test_connect_is_idempotent(self, tmp_path):
        p = tmp_path / "i.sqlite"
        connect(p).close()
        conn = connect(p)
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'"
                            ).fetchone()[0] == str(SCHEMA_VERSION)

    def test_parent_index_exists_for_tree_queries(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert any("parent" in n for n in idx)


class TestModuleBoundaries:
    """Dependencies point one way: records <- readers, records <- index.

    A readers -> index edge would make the readers untestable without SQLite and
    invite the index's storage concerns back into parsing.
    """

    def test_records_imports_nothing_from_scad(self):
        source = Path("src/scad/records.py").read_text()
        assert "scad." not in source

    def test_readers_never_imports_the_index(self):
        source = Path("src/scad/readers.py").read_text()
        assert "scad.index" not in source
        assert "sqlite" not in source.lower()


from scad.index import append_turns, session_row, upsert_session  # noqa: E402
from scad.records import (  # noqa: E402
    GRADE_FULL, GRADE_SKELETON, KIND_MAIN, SessionRecord, TurnRecord,
)


def rec(**kw) -> SessionRecord:
    base = dict(id="S1", kind=KIND_MAIN, agent="claude", source="claude-transcript")
    base.update(kw)
    return SessionRecord(**base)


def store(conn, session, **kw):
    opts = dict(machine="mac", project="proj", archive_path="/arc/S1.jsonl",
                source_size=10, source_mtime=1, parsed_offset=10)
    opts.update(kw)
    upsert_session(conn, session, **opts)


class TestUpsert:
    def test_insert_then_read_back(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(cwd="/repo", title="T", started=1, ended=2))
        row = session_row(conn, "S1")
        assert row["project"] == "proj"
        assert row["machine"] == "mac"
        assert row["title"] == "T"
        assert row["raw_present"] == 1

    def test_second_upsert_updates_in_place(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(title="first", ended=2))
        store(conn, rec(title="second", ended=9), parsed_offset=99)
        assert session_row(conn, "S1")["title"] == "second"
        assert session_row(conn, "S1")["parsed_offset"] == 99
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1

    def test_skeleton_upgrades_to_full(self, tmp_path):
        """history.jsonl is read first; a transcript upgrades the row in place."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(source="claude-history", grade=GRADE_SKELETON, title="prompt"))
        assert session_row(conn, "S1")["grade"] == GRADE_SKELETON
        store(conn, rec(source="claude-transcript", grade=GRADE_FULL, title="aiTitle"))
        row = session_row(conn, "S1")
        assert row["grade"] == GRADE_FULL
        assert row["source"] == "claude-transcript"
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1

    def test_full_is_never_downgraded_to_skeleton(self, tmp_path):
        """Roots are read in arbitrary order; history must not clobber a real read."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(source="claude-transcript", grade=GRADE_FULL))
        store(conn, rec(source="claude-history", grade=GRADE_SKELETON))
        assert session_row(conn, "S1")["grade"] == GRADE_FULL

    def test_subagent_and_parent_are_separate_rows(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="PARENT"))
        store(conn, rec(id="a0778b", kind="subagent", parent_session_id="PARENT",
                        agent_id="a0778b", source="claude-subagent"))
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
        kids = conn.execute(
            "SELECT id FROM sessions WHERE parent_session_id=?", ("PARENT",)).fetchall()
        assert [k["id"] for k in kids] == ["a0778b"]

    def test_outcome_round_trips(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(outcome="awaiting-question", last_stop_reason="tool_use",
                        n_interrupts=2))
        row = session_row(conn, "S1")
        assert row["outcome"] == "awaiting-question"
        assert row["last_stop_reason"] == "tool_use"
        assert row["n_interrupts"] == 2


class TestAppendTurns:
    def test_turns_get_sequential_idx(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        n = append_turns(conn, "S1", [
            TurnRecord(ts=1, role="user", kind="text", text="a"),
            TurnRecord(ts=2, role="assistant", kind="text", text="b"),
        ])
        assert n == 2
        rows = conn.execute("SELECT idx, text FROM turns ORDER BY idx").fetchall()
        assert [(r["idx"], r["text"]) for r in rows] == [(0, "a"), (1, "b")]

    def test_appending_continues_numbering_and_keeps_existing_rows(self, tmp_path):
        """The property that makes reindex safe after raw is pruned."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        append_turns(conn, "S1", [TurnRecord(ts=1, role="user", kind="text", text="a")])
        append_turns(conn, "S1", [TurnRecord(ts=2, role="assistant", kind="text", text="b")])
        rows = conn.execute("SELECT idx, text FROM turns ORDER BY idx").fetchall()
        assert [(r["idx"], r["text"]) for r in rows] == [(0, "a"), (1, "b")]

    def test_n_turns_tracks_the_count(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        append_turns(conn, "S1", [TurnRecord(ts=1, role="user", kind="text", text="a")])
        assert session_row(conn, "S1")["n_turns"] == 1

    def test_truncated_flag_round_trips(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        append_turns(conn, "S1", [TurnRecord(
            ts=1, role="user", kind="tool_result", text="x", truncated=True, raw_offset=42)])
        row = conn.execute("SELECT truncated, raw_offset FROM turns").fetchone()
        assert row["truncated"] == 1
        assert row["raw_offset"] == 42


import json  # noqa: E402

import click  # noqa: E402

from scad.index import reindex  # noqa: E402


def arc_write(root: Path, rel: str, records: list[dict]) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    return p


MAIN = [{"type": "assistant", "sessionId": "S1", "timestamp": "2026-07-28T10:00:00.000Z",
         "cwd": "/repo", "message": {"role": "assistant",
                                     "content": [{"type": "text", "text": "hello"}]}}]
SUB = [{"type": "assistant", "sessionId": "S1", "agentId": "sub1", "isSidechain": True,
        "timestamp": "2026-07-28T10:01:00.000Z", "cwd": "/repo",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "sub"}]}}]


class TestReindex:
    def test_indexes_main_and_subagents_as_separate_rows(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        arc_write(arc, "claude/projects/-repo/S1/subagents/agent-sub1.jsonl", SUB)

        conn = connect(tmp_path / "i.sqlite")
        stats = reindex(conn)

        ids = {r["id"] for r in conn.execute("SELECT id FROM sessions")}
        assert ids == {"S1", "sub1"}
        assert stats["sessions"] == 2
        kid = session_row(conn, "sub1")
        assert kid["parent_session_id"] == "S1"
        assert kid["kind"] == "subagent"

    def test_second_pass_is_a_no_op(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        stats = reindex(conn)
        assert stats["sessions"] == 0
        assert stats["turns"] == 0

    def test_growth_appends_without_rewriting(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        p = arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        with p.open("a") as fh:
            fh.write(json.dumps({
                "type": "assistant", "sessionId": "S1",
                "timestamp": "2026-07-28T11:00:00.000Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "more"}]},
            }) + "\n")
        reindex(conn)
        rows = conn.execute("SELECT idx, text FROM turns ORDER BY idx").fetchall()
        assert [r["text"] for r in rows] == ["hello", "more"]

    def test_project_is_computed(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        conn = connect(tmp_path / "i.sqlite")
        with patch("scad.index.resolve_project", return_value="my-proj"):
            reindex(conn)
        assert session_row(conn, "S1")["project"] == "my-proj"

    def test_run_dir_rows_get_scad_run_id(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "runs/demo-Jul28/projects/-workspace-x/S9.jsonl", [{
            "type": "assistant", "sessionId": "S9", "timestamp": "2026-07-28T10:00:00.000Z",
            "cwd": "/workspace/x",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "in a box"}]},
        }])
        conn = connect(tmp_path / "i.sqlite")
        with patch("scad.index.resolve_project", return_value="x"):
            reindex(conn)
        assert session_row(conn, "S9")["scad_run_id"] == "demo-Jul28"

    def test_malformed_file_is_counted_not_fatal(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        (arc / "claude" / "projects" / "-repo").mkdir(parents=True)
        (arc / "claude" / "projects" / "-repo" / "bad.jsonl").write_text("not json at all\n")
        arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        conn = connect(tmp_path / "i.sqlite")
        stats = reindex(conn)
        assert stats["sessions"] == 1
        assert stats["skipped_lines"] >= 1


class TestRebuildSafety:
    def test_rebuild_refuses_when_raw_is_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="GONE"))
        conn.execute("UPDATE sessions SET raw_present = 0 WHERE id = 'GONE'")
        conn.commit()
        with pytest.raises(click.ClickException) as exc:
            reindex(conn, rebuild=True)
        assert "raw" in str(exc.value).lower()

    def test_force_overrides_the_refusal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="GONE"))
        conn.execute("UPDATE sessions SET raw_present = 0 WHERE id = 'GONE'")
        conn.commit()
        reindex(conn, rebuild=True, force=True)
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0

    def test_plain_reindex_never_deletes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="ORPHAN"))
        reindex(conn)
        assert session_row(conn, "ORPHAN") is not None


from scad.index import ensure_fts, search_turns  # noqa: E402


class TestSearch:
    def _seed(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="S1"), project="alpha")
        store(conn, rec(id="S2"), project="beta")
        append_turns(conn, "S1", [
            TurnRecord(ts=1, role="assistant", kind="text",
                       text="the resolver engine returns a directory"),
            TurnRecord(ts=2, role="assistant", kind="thinking",
                       text="considering whether markers beat git-root"),
        ])
        append_turns(conn, "S2", [
            TurnRecord(ts=3, role="assistant", kind="text",
                       text="archive copy semantics and newline boundaries"),
        ])
        return conn

    def test_finds_a_turn_by_word(self, tmp_path):
        conn = self._seed(tmp_path)
        ensure_fts(conn)
        hits = search_turns(conn, "resolver")
        assert [h["session_id"] for h in hits] == ["S1"]
        assert "resolver" in hits[0]["text"]

    def test_filters_by_project(self, tmp_path):
        conn = self._seed(tmp_path)
        ensure_fts(conn)
        assert search_turns(conn, "archive", project="alpha") == []
        assert len(search_turns(conn, "archive", project="beta")) == 1

    def test_filters_by_turn_kind(self, tmp_path):
        """Search only reasoning, or only what was said."""
        conn = self._seed(tmp_path)
        ensure_fts(conn)
        assert len(search_turns(conn, "markers", kind="thinking")) == 1
        assert search_turns(conn, "markers", kind="text") == []

    def test_no_match_is_empty_not_an_error(self, tmp_path):
        conn = self._seed(tmp_path)
        ensure_fts(conn)
        assert search_turns(conn, "nonexistentterm") == []

    def test_ensure_fts_is_idempotent(self, tmp_path):
        conn = self._seed(tmp_path)
        ensure_fts(conn)
        ensure_fts(conn)
        assert len(search_turns(conn, "resolver")) == 1   # not duplicated

    def test_fts_is_rebuilt_from_existing_rows(self, tmp_path):
        """FTS is derived — it must be addable without re-reading the archive."""
        conn = self._seed(tmp_path)
        ensure_fts(conn)
        conn.execute("DROP TABLE turns_fts")
        conn.commit()
        ensure_fts(conn)
        assert len(search_turns(conn, "resolver")) == 1

    def test_quotes_in_a_query_do_not_break_it(self, tmp_path):
        conn = self._seed(tmp_path)
        ensure_fts(conn)
        assert search_turns(conn, 'resolver "engine"') != []


from scad.index import apply_job_state  # noqa: E402
from scad.records import JobStateRecord  # noqa: E402


def job(**kw) -> JobStateRecord:
    base = dict(session_id="S1", name="nd-5", state="blocked",
                needs="drop the bioRxiv PDF to ~/Downloads/",
                detail="workflow salvaged (28/29 results)")
    base.update(kw)
    return JobStateRecord(**base)


class TestJobStateColumns:
    def test_columns_exist(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert {"name", "harness_state", "needs", "needs_detail"} <= cols

    def test_added_to_an_index_that_predates_them(self, tmp_path):
        """CREATE TABLE IF NOT EXISTS is a no-op on an existing file, so without a
        migration these columns would only ever appear on a fresh machine."""
        from scad.index import _SCHEMA

        added = ("name", "harness_state", "needs", "needs_detail")
        before = "\n".join(
            line for line in _SCHEMA.splitlines()
            if not any(line.strip().startswith(f"{c} ") for c in added)
        )
        p = tmp_path / "old.sqlite"
        old = sqlite3.connect(p)
        old.executescript(before)
        old.execute("INSERT INTO sessions (id, kind, agent, machine, grade, source, "
                    "parsed_offset) VALUES ('KEEP','main','claude','mac','full',"
                    "'claude-transcript',0)")
        old.commit()
        stale = {r[1] for r in old.execute("PRAGMA table_info(sessions)")}
        old.close()
        assert not set(added) & stale                # the fixture really is older

        conn = connect(p)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert set(added) <= cols
        assert session_row(conn, "KEEP") is not None      # migration never drops rows


class TestApplyJobState:
    def test_updates_the_matching_session(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        assert apply_job_state(conn, job()) is True
        row = session_row(conn, "S1")
        assert row["name"] == "nd-5"
        assert row["harness_state"] == "blocked"
        assert row["needs"].startswith("drop the bioRxiv")
        assert row["needs_detail"].startswith("workflow salvaged")

    def test_an_id_with_no_session_is_ignored(self, tmp_path):
        """The harness outlives transcripts; a name with nothing to hang it on is
        not an error and must not create a phantom row."""
        conn = connect(tmp_path / "i.sqlite")
        assert apply_job_state(conn, job(session_id="GHOST")) is False
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0

    def test_a_newer_snapshot_clears_a_resolved_need(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        apply_job_state(conn, job())
        apply_job_state(conn, job(state="done", needs=None, detail=None))
        row = session_row(conn, "S1")
        assert row["harness_state"] == "done"
        assert row["needs"] is None


class TestReindexJoinsJobState:
    STATE = [{"name": "nd-5", "sessionId": "S1", "state": "blocked",
              "needs": "drop the bioRxiv PDF", "detail": "workflow salvaged",
              "updatedAt": "2026-07-28T17:56:43.450Z"}]

    def test_session_gets_its_human_name(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        arc_write(arc, "claude/jobs/0829ef1a/state-history.jsonl", self.STATE)

        conn = connect(tmp_path / "i.sqlite")
        stats = reindex(conn)

        row = session_row(conn, "S1")
        assert row["name"] == "nd-5"
        assert row["harness_state"] == "blocked"
        assert row["needs"] == "drop the bioRxiv PDF"
        assert stats["named"] == 1

    def test_state_history_is_never_indexed_as_a_session(self, tmp_path, monkeypatch):
        """state.json carries a sessionId and a cwd — read as a transcript it would
        forge a turnless row over the real one."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/jobs/0829ef1a/state-history.jsonl", self.STATE)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0

    def test_job_state_read_before_its_session_still_lands(self, tmp_path, monkeypatch):
        """'jobs' sorts before 'projects', so the row does not exist yet when the
        job file is read. This is why the join is a second pass."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/jobs/0829ef1a/state-history.jsonl", self.STATE)
        arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        assert session_row(conn, "S1")["name"] == "nd-5"

    def test_unknown_session_id_is_skipped_quietly(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/jobs/j/state-history.jsonl",
                  [{"sessionId": "NOSUCH", "name": "nd-9"}])
        conn = connect(tmp_path / "i.sqlite")
        stats = reindex(conn)
        assert stats["named"] == 0
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
