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

    def test_an_id_with_no_session_becomes_a_skeleton_row(self, tmp_path):
        """The harness outlives transcripts. nd-3 has no transcript and no
        history.jsonl line anywhere — its state.json is the only record it ran,
        so without this the one session that HAS a human name is unfindable."""
        conn = connect(tmp_path / "i.sqlite")
        assert apply_job_state(conn, job(session_id="GHOST", name="nd-3",
                                         state="failed", cwd="/repo/nd",
                                         created_at=1000, updated_at=2000)) is True
        row = session_row(conn, "GHOST")
        assert row["grade"] == GRADE_SKELETON
        assert row["source"] == "claude-jobstate"
        assert row["kind"] == KIND_MAIN
        assert row["agent"] == "claude"
        assert row["name"] == "nd-3"
        assert row["harness_state"] == "failed"
        assert row["cwd"] == "/repo/nd"

    def test_an_inserted_row_is_datable_so_it_sorts_into_session_ls(self, tmp_path):
        """`session ls` orders by started DESC with a limit; a NULL start would
        sink the row below every dated session and defeat the whole point."""
        conn = connect(tmp_path / "i.sqlite")
        apply_job_state(conn, job(session_id="GHOST", created_at=1000, updated_at=2000))
        row = session_row(conn, "GHOST")
        assert row["started"] == 1000
        assert row["ended"] == 2000

    def test_an_inserted_row_resolves_a_project_from_its_cwd(self, tmp_path):
        repo = tmp_path / "nd"
        (repo / ".git").mkdir(parents=True)
        conn = connect(tmp_path / "i.sqlite")
        apply_job_state(conn, job(session_id="GHOST", cwd=str(repo)))
        assert session_row(conn, "GHOST")["project"] == "nd"

    def test_inserting_never_clobbers_a_real_row(self, tmp_path):
        """Insert only when the UPDATE matched nothing — a transcript row keeps
        its grade, source, and turns."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(title="real"))
        assert apply_job_state(conn, job(name="nd-5")) is True
        row = session_row(conn, "S1")
        assert row["grade"] == GRADE_FULL
        assert row["source"] == "claude-transcript"
        assert row["title"] == "real"
        assert row["name"] == "nd-5"
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1

    def test_a_newer_snapshot_clears_a_resolved_need(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        apply_job_state(conn, job())
        apply_job_state(conn, job(state="done", needs=None, detail=None))
        row = session_row(conn, "S1")
        assert row["harness_state"] == "done"
        assert row["needs"] is None


class TestOutcomeCoverage:
    """`outcome` after tool-result-last: what is total, and what is honestly not.

    The spec claims outcome becomes total. It does not, for two reasons that have
    nothing to do with this branch, and both are pinned here so the gap is a
    documented fact rather than a surprise at query time:

      1. `read_codex_rollout` never calls `derive_outcome` at all — 107 of 1458
         rows on the real index. That is a separate reader, not a missing branch.
      2. A transcript with no `message` records at all (a job that died before
         the model spoke) has an empty tail, so there is nothing to derive.
    """

    TOOL_RESULT_LAST = [
        {"type": "assistant", "sessionId": "S1", "timestamp": "2026-07-28T10:00:00.000Z",
         "cwd": "/repo", "message": {"role": "assistant", "stop_reason": "tool_use",
                                     "content": [{"type": "tool_use", "name": "Bash",
                                                  "input": {}}]}},
        {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:01.000Z",
         "cwd": "/repo", "message": {"role": "user",
                                     "content": [{"type": "tool_result", "content": "ok"}]}},
    ]
    NO_MESSAGES = [{"type": "mode", "sessionId": "S2", "mode": "default"},
                   {"type": "permission-mode", "sessionId": "S2", "permissionMode": "plan"}]

    def _index(self, tmp_path, monkeypatch, files):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        for rel, records in files:
            arc_write(arc, rel, records)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        return conn

    def test_claude_transcripts_with_turns_all_get_an_outcome(self, tmp_path, monkeypatch):
        conn = self._index(tmp_path, monkeypatch, [
            ("claude/projects/-repo/S1.jsonl", self.TOOL_RESULT_LAST),
            ("claude/projects/-repo/S1/subagents/agent-sub1.jsonl", self.TOOL_RESULT_LAST),
            ("claude/projects/-repo/S3.jsonl",
             [{**r, "sessionId": "S3"} for r in MAIN]),
        ])
        nulls = conn.execute(
            "SELECT count(*) FROM sessions WHERE grade = 'full' AND n_turns > 0 "
            "AND agent = 'claude' AND outcome IS NULL").fetchone()[0]
        assert nulls == 0
        assert session_row(conn, "S1")["outcome"] == "tool-result-last"
        assert session_row(conn, "sub1")["outcome"] == "tool-result-last"

    def test_a_transcript_with_no_messages_keeps_a_null_outcome(self, tmp_path, monkeypatch):
        """There is genuinely nothing to derive; a fabricated label would be worse."""
        conn = self._index(tmp_path, monkeypatch,
                           [("claude/projects/-repo/S2.jsonl", self.NO_MESSAGES)])
        row = session_row(conn, "S2")
        assert row["n_turns"] == 0
        assert row["outcome"] is None


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

    def test_state_history_is_never_read_by_the_transcript_reader(self, tmp_path, monkeypatch):
        """state.json carries a sessionId and a cwd — read as a transcript it would
        forge a `grade='full'` row claiming a trace that does not exist. The
        job-state pass may create a row; it must be an honest skeleton."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/jobs/0829ef1a/state-history.jsonl", self.STATE)
        conn = connect(tmp_path / "i.sqlite")
        stats = reindex(conn)
        assert stats["files"] == 0                   # no file went to a reader
        row = session_row(conn, "S1")
        assert row["grade"] == GRADE_SKELETON
        assert row["source"] == "claude-jobstate"
        assert row["n_turns"] == 0

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

    def test_a_job_with_no_surviving_trace_still_gets_a_row(self, tmp_path, monkeypatch):
        """The real nd-3: state-history.jsonl and nothing else, anywhere."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/jobs/j/state-history.jsonl",
                  [{"sessionId": "NOSUCH", "name": "nd-9", "state": "failed",
                    "cwd": "/repo/nd", "createdAt": "2026-07-08T14:28:25.019Z",
                    "updatedAt": "2026-07-12T19:48:48.238Z"}])
        conn = connect(tmp_path / "i.sqlite")
        stats = reindex(conn)
        assert stats["named"] == 1
        row = session_row(conn, "NOSUCH")
        assert row["name"] == "nd-9"
        assert row["grade"] == GRADE_SKELETON
        assert row["source"] == "claude-jobstate"
        assert row["started"] is not None


# --- notes: the tier that is never rederivable --------------------------------

from scad.index import SOURCE_NOTE, append_notes, index_notes, session_notes  # noqa: E402
from scad.notes import append_note  # noqa: E402
from scad.readers import read_notes  # noqa: E402


@pytest.fixture
def noted(tmp_path, monkeypatch):
    """A SCAD_HOME with a notes store and an archive, both empty."""
    monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
    monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
    return tmp_path


NOTE = {"topic": "notes-store", "relation": "continue", "title": "first",
        "text": "body", "tags": ["notes", "jsonl"], "entities": ["session-index.md"],
        "cwd_at_write": "/repo"}


class TestAppendNotes:
    def test_rows_land_with_the_note_path(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        p = append_note(NOTE, session_id="S1")
        n = append_notes(conn, "S1", read_notes(p)[0], str(p))
        assert n == 1
        row = conn.execute("SELECT * FROM notes").fetchone()
        assert row["session_id"] == "S1"
        assert row["idx"] == 0
        assert row["note_path"] == str(p)

    def test_tags_and_entities_are_stored_as_json_arrays(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        p = append_note(NOTE, session_id="S1")
        append_notes(conn, "S1", read_notes(p)[0], str(p))
        row = conn.execute("SELECT tags, entities FROM notes").fetchone()
        assert json.loads(row["tags"]) == ["notes", "jsonl"]
        assert json.loads(row["entities"]) == ["session-index.md"]

    def test_idx_continues_and_existing_rows_are_untouched(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        p = append_note(NOTE, session_id="S1")
        append_notes(conn, "S1", read_notes(p)[0], str(p))
        end = p.stat().st_size
        append_note({**NOTE, "title": "second"}, session_id="S1")
        append_notes(conn, "S1", read_notes(p, end)[0], str(p))
        rows = conn.execute("SELECT idx, title FROM notes ORDER BY idx").fetchall()
        assert [(r["idx"], r["title"]) for r in rows] == [(0, "first"), (1, "second")]


class TestIndexNotes:
    def test_a_note_becomes_a_row_and_advances_notes_offset(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        p = append_note(NOTE, session_id="S1")
        stats = index_notes(conn)
        assert stats["notes"] == 1
        assert session_row(conn, "S1")["notes_offset"] == p.stat().st_size

    def test_a_second_pass_with_no_new_note_writes_nothing(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        append_note(NOTE, session_id="S1")
        index_notes(conn)
        assert index_notes(conn)["notes"] == 0
        assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 1

    def test_a_note_appended_between_two_passes_produces_exactly_one_new_row(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        append_note(NOTE, session_id="S1")
        index_notes(conn)
        append_note({**NOTE, "title": "second"}, session_id="S1")
        assert index_notes(conn)["notes"] == 1
        assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 2

    def test_the_agent_shard_names_the_agent_column(self, noted):
        conn = connect(noted / "i.sqlite")
        append_note(NOTE, session_id="X1", agent="codex")
        index_notes(conn)
        assert session_row(conn, "X1")["agent"] == "codex"

    def test_a_note_for_an_unindexed_session_creates_a_skeleton_row(self, noted):
        # DECISION: yes, it creates a row. Notes outlive traces by design — the
        # spec says one can arrive when the transcript no longer exists — and a
        # tier that is never rederivable must never be the tier that is
        # unfindable. Same standing history.jsonl and job state already have.
        conn = connect(noted / "i.sqlite")
        with patch("scad.index.resolve_project", return_value="repo"):
            append_note(NOTE, session_id="GHOST")
            assert index_notes(conn)["notes"] == 1
        row = session_row(conn, "GHOST")
        assert row["grade"] == "skeleton"
        assert row["source"] == SOURCE_NOTE
        assert row["kind"] == "main"

    def test_that_row_resolves_its_project_from_cwd_at_write(self, noted):
        # The whole reason cwd_at_write exists: nothing else here knows where
        # this happened once the trace is gone.
        conn = connect(noted / "i.sqlite")
        append_note(NOTE, session_id="GHOST")
        with patch("scad.index.resolve_project", return_value="from-the-note") as rp:
            index_notes(conn)
        assert rp.call_args[0][0] == "/repo"
        assert session_row(conn, "GHOST")["project"] == "from-the-note"

    def test_an_existing_full_row_is_never_downgraded_by_its_note(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1", title="real session"))
        append_note(NOTE, session_id="S1")
        index_notes(conn)
        row = session_row(conn, "S1")
        assert row["grade"] == GRADE_FULL
        assert row["source"] == "claude-transcript"
        assert row["title"] == "real session"

    def test_a_note_arriving_after_the_trace_was_pruned_still_indexes(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        conn.execute("UPDATE sessions SET raw_present = 0 WHERE id = 'S1'")
        conn.commit()
        append_note(NOTE, session_id="S1")
        assert index_notes(conn)["notes"] == 1
        assert session_row(conn, "S1")["raw_present"] == 0

    def test_an_empty_store_is_not_an_error(self, noted):
        conn = connect(noted / "i.sqlite")
        assert index_notes(conn)["notes"] == 0

    def test_reindex_runs_the_notes_pass(self, noted):
        conn = connect(noted / "i.sqlite")
        arc_write(noted / "arc", "claude/projects/-repo/S1.jsonl", MAIN)
        append_note(NOTE, session_id="S1")
        stats = reindex(conn)
        assert stats["notes"] == 1
        assert conn.execute(
            "SELECT count(*) FROM notes n JOIN sessions s ON s.id = n.session_id "
            "WHERE s.id = 'S1'").fetchone()[0] == 1

    def test_rebuild_reindexes_notes_from_the_files(self, noted):
        # The files are truth and are never deleted, so a rebuild of the notes
        # index is always safe — unlike turns, which is what --rebuild guards.
        conn = connect(noted / "i.sqlite")
        arc_write(noted / "arc", "claude/projects/-repo/S1.jsonl", MAIN)
        append_note(NOTE, session_id="S1")
        reindex(conn)
        reindex(conn, rebuild=True)
        assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 1

    def test_session_notes_reads_back_in_order(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        append_note(NOTE, session_id="S1")
        append_note({**NOTE, "title": "second"}, session_id="S1")
        index_notes(conn)
        assert [r["title"] for r in session_notes(conn, "S1")] == ["first", "second"]
