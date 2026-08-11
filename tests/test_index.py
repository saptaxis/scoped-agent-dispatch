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

    def test_the_human_name_is_stored(self, tmp_path):
        """A `/rename` reaches the index through the ordinary session upsert."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(name="jul29-session-cli", title="Doing the thing"))
        row = session_row(conn, "S1")
        assert row["name"] == "jul29-session-cli"
        assert row["title"] == "Doing the thing"     # kept apart, both stored

    def test_a_rename_overwrites_an_earlier_name(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(name="first-name"))
        store(conn, rec(name="second-name"), parsed_offset=99)
        assert session_row(conn, "S1")["name"] == "second-name"

    def test_cumulative_counters_add_rather_than_overwrite(self, tmp_path):
        """The unit-level statement of the same rule: a later upsert carries the
        tail's counts, so the column must accumulate. Fields the tail *does*
        know in full (title, outcome) still overwrite — see the test above."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(n_interrupts=3, n_tool_denials=1, n_errors=2))
        store(conn, rec(n_interrupts=0, n_tool_denials=0, n_errors=1),
              parsed_offset=99)

        row = session_row(conn, "S1")
        assert row["n_interrupts"] == 3      # nothing new; the 3 must survive
        assert row["n_tool_denials"] == 1
        assert row["n_errors"] == 3          # 2 seen before + 1 in the tail

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
        # reindex is a function of TWO roots, not one: the archive it parses and
        # the notes store it indexes. Isolating only the archive left this
        # asserting over whatever the developer had typed into /remember.
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
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

    def test_growth_keeps_counts_from_earlier_passes(self, tmp_path, monkeypatch):
        """Interrupts/denials/errors are cumulative over a session's whole life.

        An incremental pass reads only the tail, so its counts describe the tail
        alone. Overwriting the column would erase everything earlier passes saw:
        a session that was interrupted, then grew quietly, would report zero —
        and once raw is pruned that wrong count is the only copy left.
        """
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        interrupted = dict(MAIN[0], interruptedMessageId="m1")
        p = arc_write(arc, "claude/projects/-repo/S1.jsonl", [interrupted])
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        assert session_row(conn, "S1")["n_interrupts"] == 1

        # Grow the session with a clean turn — nothing to count in this tail.
        with p.open("a") as fh:
            fh.write(json.dumps({
                "type": "assistant", "sessionId": "S1",
                "timestamp": "2026-07-28T11:00:00.000Z",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": "more"}]},
            }) + "\n")
        reindex(conn)

        assert session_row(conn, "S1")["n_interrupts"] == 1

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


KIMI_WIRE = [
    {"type": "context.append_message", "time": 1785232800003,
     "message": {"role": "user", "content": [{"type": "text", "text": "do the thing"}],
                 "origin": {"kind": "user"}}},
    {"type": "turn.prompt", "input": [{"type": "text", "text": "do the thing"}],
     "origin": {"kind": "user"}, "time": 1785232800004},
    {"type": "context.append_loop_event", "time": 1785232800005,
     "event": {"type": "content.part", "uuid": "p1",
               "part": {"type": "text", "text": "on it"}}},
]


class TestReindexKimi:
    def _archive(self, arc: Path) -> None:
        arc_write(arc, "kimi/wd_repo_ab/session_K1/agents/main/wire.jsonl", KIMI_WIRE)
        arc_write(arc, "kimi/wd_repo_ab/session_K1/agents/agent-0/wire.jsonl", KIMI_WIRE)
        state = arc / "kimi" / "wd_repo_ab" / "session_K1" / "state-history.jsonl"
        state.write_text(json.dumps({
            "workDir": "/repo", "title": "kimi work",
            "createdAt": "2026-07-28T10:00:00.000Z",
            "updatedAt": "2026-07-28T10:05:00.000Z",
        }) + "\n")

    def test_kimi_files_route_to_the_kimi_reader(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._archive(arc)

        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)

        row = session_row(conn, "K1")
        assert row["agent"] == "kimi"
        assert row["source"] == "kimi-wire"
        assert row["cwd"] == "/repo"
        assert row["title"] == "kimi work"

    def test_main_and_subagent_are_separate_rows(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._archive(arc)

        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)

        rows = list(conn.execute(
            "SELECT id, kind, parent_session_id FROM sessions WHERE agent='kimi'"))
        assert len(rows) == 2
        sub = next(r for r in rows if r["kind"] == "subagent")
        assert sub["parent_session_id"] == "K1"

    def test_user_turns_are_not_doubled_through_the_index(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._archive(arc)

        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)

        n = conn.execute(
            "SELECT count(*) FROM turns WHERE session_id='K1' AND role='user'"
        ).fetchone()[0]
        assert n == 1

    def test_second_pass_is_a_no_op(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._archive(arc)

        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        stats = reindex(conn)
        assert stats["sessions"] == 0
        assert stats["turns"] == 0

    def test_a_kimi_state_history_makes_no_phantom_job_row(self, tmp_path, monkeypatch):
        """It rides the job-state name, but carries no sessionId — so it must add
        nothing rather than forge a row."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._archive(arc)

        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)

        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


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
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))   # see TestReindex
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

    def test_a_transcript_pass_without_a_name_keeps_the_one_job_state_set(self, tmp_path):
        """Two writers share this column: the transcript reader (customTitle) and
        job state. An incremental pass parses only the tail, which usually holds
        no rename at all, so a plain assignment there would blank a real name."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec())
        apply_job_state(conn, job(name="nd-5"))
        store(conn, rec(name=None), parsed_offset=99)
        assert session_row(conn, "S1")["name"] == "nd-5"

    def test_a_job_state_without_a_name_keeps_the_one_the_transcript_read(self, tmp_path):
        """The mirror case. Most job snapshots carry no name; a NULL there means
        "this job was never named", not "forget what the human typed"."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(name="jul29-session-cli"))
        apply_job_state(conn, job(name=None, state="done"))
        row = session_row(conn, "S1")
        assert row["name"] == "jul29-session-cli"
        assert row["harness_state"] == "done"      # the rest still assigns

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


class TestReindexReadsRenames:
    """`/rename` is the commoner source of a human name than job state is —
    17 sessions in the real archive against 5 named jobs."""

    RENAMED = MAIN + [{"type": "custom-title", "sessionId": "S1",
                       "customTitle": "writing-wm-evals-research"}]

    def test_a_renamed_session_is_named_in_the_index(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/projects/-repo/S1.jsonl", self.RENAMED)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        assert session_row(conn, "S1")["name"] == "writing-wm-evals-research"

    def test_a_session_nobody_renamed_stays_unnamed(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        arc_write(arc, "claude/projects/-repo/S1.jsonl", MAIN)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        assert session_row(conn, "S1")["name"] is None

    def test_a_later_pass_over_a_grown_transcript_keeps_the_name(self, tmp_path, monkeypatch):
        """The incremental case: the tail holds no custom-title record, so the
        name has to survive being re-upserted from a partial read."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        p = arc_write(arc, "claude/projects/-repo/S1.jsonl", self.RENAMED)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        with p.open("a") as fh:
            fh.write(json.dumps({
                "type": "assistant", "sessionId": "S1",
                "timestamp": "2026-07-28T11:00:00.000Z", "cwd": "/repo",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": "more"}]}}) + "\n")
        reindex(conn)
        assert session_row(conn, "S1")["name"] == "writing-wm-evals-research"


# --- notes: the tier that is never rederivable --------------------------------

from scad.index import (  # noqa: E402
    SOURCE_NOTE, append_notes, index_notes, search_notes, session_notes,
)
from scad.notes import append_note  # noqa: E402
from scad.readers import read_notes  # noqa: E402


@pytest.fixture
def noted(tmp_path, monkeypatch):
    """A SCAD_HOME with a notes store and an archive, both empty."""
    monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
    monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
    return tmp_path


NOTE = {"topic": "notes-store", "title": "first",
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


class TestNotesSchemaMoved:
    """`kind` and `project` in, stored `relation` out."""

    def test_the_columns_exist_on_a_fresh_index(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(notes)")}
        assert {"kind", "project"} <= cols
        assert "relation" not in cols     # derived per query, never stored

    def test_an_index_built_before_them_gains_them_on_connect(self, tmp_path):
        """ALTER, via _ADDED_COLUMNS. Without it the columns would exist only on
        machines that had never indexed anything."""
        path = tmp_path / "i.sqlite"
        older = sqlite3.connect(path)
        older.execute(
            "CREATE TABLE notes (session_id TEXT NOT NULL, idx INTEGER NOT NULL, "
            "ts INTEGER, topic TEXT, relation TEXT, parent TEXT, title TEXT, "
            "tags TEXT, entities TEXT, note_path TEXT NOT NULL, "
            "PRIMARY KEY (session_id, idx))")
        older.execute("INSERT INTO notes (session_id, idx, topic, relation, note_path) "
                      "VALUES ('OLD', 0, 'a-topic', 'continue', '/n')")
        older.commit()
        older.close()

        conn = connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(notes)")}
        assert {"kind", "project"} <= cols
        row = conn.execute("SELECT kind, project FROM notes").fetchone()
        # ALTER does not backfill, and the notes pass resumes from notes_offset,
        # so an existing row stays NULL until something re-reads its file.
        assert (row["kind"], row["project"]) == (None, None)

    def test_an_unbackfilled_row_still_answers_as_the_default_kind(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="S1"))
        conn.execute("INSERT INTO notes (session_id, idx, ts, topic, note_path) "
                     "VALUES ('S1', 0, 1, 'a-topic', '/n')")
        conn.commit()
        assert session_notes(conn, "S1")[0]["kind"] == "info"

    def test_kind_and_project_are_stored_from_the_record(self, noted):
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        p = append_note({**NOTE, "kind": "bug", "project": "orglens"}, session_id="S1")
        append_notes(conn, "S1", read_notes(p)[0], str(p))
        row = conn.execute("SELECT kind, project FROM notes").fetchone()
        assert (row["kind"], row["project"]) == ("bug", "orglens")


class TestBackfillingTheNewColumns:
    """What actually populates `kind` / `project` on notes indexed before them.

    ALTER does not backfill and the notes pass resumes from `notes_offset`, so a
    row that predates the columns keeps NULL through any number of ordinary
    reindexes. Only re-reading the files fixes it — and the files are truth and
    are never touched, so both routes are safe.
    """

    def _stale(self, noted):
        """An index whose note row is there but predates kind/project."""
        conn = connect(noted / "i.sqlite")
        store(conn, rec(id="S1"))
        p = append_note({**NOTE, "kind": "handoff", "project": "orglens"},
                        session_id="S1")
        index_notes(conn)
        conn.execute("UPDATE notes SET kind = NULL, project = NULL")
        conn.commit()
        return conn, p

    def test_an_ordinary_reindex_does_not_fix_it(self, noted):
        conn, _ = self._stale(noted)
        index_notes(conn)
        row = conn.execute("SELECT kind, project FROM notes").fetchone()
        assert (row["kind"], row["project"]) == (None, None)

    def test_clearing_the_rows_and_the_offset_repopulates_them(self, noted):
        conn, _ = self._stale(noted)
        conn.executescript(
            "DELETE FROM notes; UPDATE sessions SET notes_offset = 0;")
        conn.commit()
        index_notes(conn)
        rows = conn.execute("SELECT kind, project FROM notes").fetchall()
        assert [(r["kind"], r["project"]) for r in rows] == [("handoff", "orglens")]

    def test_a_rebuild_repopulates_them_too(self, noted):
        conn, _ = self._stale(noted)
        arc_write(noted / "arc", "claude/projects/-repo/S1.jsonl", MAIN)
        reindex(conn, rebuild=True)
        row = conn.execute("SELECT kind, project FROM notes").fetchone()
        assert (row["kind"], row["project"]) == ("handoff", "orglens")

    def test_resetting_the_offset_without_clearing_would_duplicate(self, noted):
        """Why the recipe is DELETE *and* reset, not reset alone: idx continues
        from the maximum, so the same records come back as new rows."""
        conn, _ = self._stale(noted)
        conn.execute("UPDATE sessions SET notes_offset = 0")
        conn.commit()
        index_notes(conn)
        assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 2


class TestRelationIsDerived:
    """The rule: parent -> branch; topic seen earlier -> continue; else shift."""

    def _thread(self, tmp_path, records):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="S1"))
        for i, r in enumerate(records):
            conn.execute(
                "INSERT INTO notes (session_id, idx, ts, kind, topic, parent, note_path) "
                "VALUES ('S1', ?, ?, 'info', ?, ?, '/n')",
                (i, i, r.get("topic"), r.get("parent")))
        conn.commit()
        return conn

    def test_a_new_topic_with_no_parent_is_a_shift(self, tmp_path):
        conn = self._thread(tmp_path, [{"topic": "notes-schema"}])
        assert [r["relation"] for r in session_notes(conn, "S1")] == ["shift"]

    def test_a_topic_seen_earlier_in_the_thread_is_a_continue(self, tmp_path):
        conn = self._thread(tmp_path, [{"topic": "notes-schema"},
                                       {"topic": "notes-schema"}])
        assert [r["relation"] for r in session_notes(conn, "S1")] == ["shift", "continue"]

    def test_a_parent_makes_it_a_branch(self, tmp_path):
        conn = self._thread(tmp_path, [{"topic": "notes-schema"},
                                       {"topic": "kind-enum", "parent": "notes-schema"}])
        assert [r["relation"] for r in session_notes(conn, "S1")] == ["shift", "branch"]

    def test_a_parent_outside_this_session_is_still_a_branch(self, tmp_path):
        # `parent` legitimately names a topic in ANOTHER session's note file, so
        # the rule must not require the parent to be resolvable here.
        conn = self._thread(tmp_path, [{"topic": "kind-enum",
                                        "parent": "written-in-some-other-session"}])
        assert [r["relation"] for r in session_notes(conn, "S1")] == ["branch"]

    def test_parent_beats_a_topic_that_also_appeared_earlier(self, tmp_path):
        conn = self._thread(tmp_path, [{"topic": "notes-schema"},
                                       {"topic": "notes-schema", "parent": "elsewhere"}])
        assert session_notes(conn, "S1")[1]["relation"] == "branch"

    def test_a_later_note_never_changes_an_earlier_one(self, tmp_path):
        # Only rows BEFORE this one count, or every first note would turn into a
        # continue as soon as its topic came up again.
        conn = self._thread(tmp_path, [{"topic": "t"}, {"topic": "t"}, {"topic": "t"}])
        assert [r["relation"] for r in session_notes(conn, "S1")] == [
            "shift", "continue", "continue"]

    def test_the_derivation_is_the_same_one_the_file_reader_uses(self, tmp_path):
        # Two implementations of one rule (SQL over rows, Python over records)
        # must not drift. Same thread, same answers.
        from scad.notes import hydrate_notes
        records = [{"topic": "t"}, {"topic": "t"}, {"topic": "u", "parent": "t"},
                   {"topic": "v"}]
        conn = self._thread(tmp_path, records)
        assert [r["relation"] for r in session_notes(conn, "S1")] == \
               [r["relation"] for r in hydrate_notes(records)]


class TestNoteProjectOverridesTheSessions:
    """COALESCE(n.project, s.project) — the cross-capture case."""

    def _cross(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(id="S1"), project="alpha")
        conn.execute("INSERT INTO notes (session_id, idx, ts, kind, topic, project, "
                     "title, tags, entities, note_path) "
                     "VALUES ('S1',0,2,'bug','a-bug-in-beta','beta','filed elsewhere',"
                     "'[]','[]','/n')")
        conn.execute("INSERT INTO notes (session_id, idx, ts, kind, topic, project, "
                     "title, tags, entities, note_path) "
                     "VALUES ('S1',1,1,'info','ordinary',NULL,'stays home',"
                     "'[]','[]','/n')")
        conn.commit()
        return conn

    def test_search_reports_the_notes_project_not_the_sessions(self, tmp_path):
        hits = {h["topic"]: h["project"] for h in search_notes(self._cross(tmp_path), "a")}
        assert hits["a-bug-in-beta"] == "beta"
        assert hits["ordinary"] == "alpha"

    def test_search_matches_the_authored_project_name(self, tmp_path):
        # "beta" appears in no topic, title, tag or entity — only in `project`.
        hits = search_notes(self._cross(tmp_path), "beta")
        assert [h["topic"] for h in hits] == ["a-bug-in-beta"]

    def test_search_does_not_turn_a_projects_name_into_a_listing(self, tmp_path):
        # Matching the RESOLVED project would make every note in alpha a hit for
        # "alpha", which answers a different question than search asks.
        assert search_notes(self._cross(tmp_path), "alpha") == []


class TestReindexArchivesFirst:
    def test_reindex_sweeps_the_archive_before_reading_it(self, tmp_path, monkeypatch):
        """The index reads the archive, so without this a reindex after a day's
        work indexes nothing new and still reports success."""
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        conn = connect(tmp_path / "i.sqlite")
        with patch("scad.index.archive_all") as sweep:
            reindex(conn, archive_first=True)
        sweep.assert_called_once()

    def test_the_library_function_does_not_sweep_by_default(self, tmp_path, monkeypatch):
        """archive_all reads the real ~/.claude whatever SCAD_ARCHIVE says, so a
        default-on sweep would make every reindexing test copy the live corpus."""
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        conn = connect(tmp_path / "i.sqlite")
        with patch("scad.index.archive_all") as sweep:
            reindex(conn)
        sweep.assert_not_called()

    def test_no_archive_skips_the_sweep(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        conn = connect(tmp_path / "i.sqlite")
        with patch("scad.index.archive_all") as sweep:
            reindex(conn, archive_first=False)
        sweep.assert_not_called()

    def test_a_failing_archive_does_not_block_indexing(self, tmp_path, monkeypatch):
        """A full disk should degrade to 'index what we have', not to nothing."""
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        conn = connect(tmp_path / "i.sqlite")
        with patch("scad.index.archive_all", side_effect=OSError("disk full")):
            reindex(conn, archive_first=True)          # must not raise


class TestCwdIsTheSessionsOwnDirectory:
    """A session's cwd is where it was launched, and must never drift.

    Claude Code records `cwd` on EVERY record, so a tool call that runs in
    another directory writes that directory into the trace. The reader takes
    the first cwd in the range it parsed — and an incremental pass parses only
    the tail, which may well begin on one of those foreign records.

    Found on the real corpus: this session's transcript holds 1394 records
    saying `code/scoped-agent-dispatch` and 182 saying `traitful-docs`, and an
    incremental pass had relabelled the whole session `traitful-docs`. Project
    is a computed column derived from cwd, and project is the join key for
    retrieval — so a wandering cwd silently moves a session between projects.
    """

    def test_a_later_pass_cannot_move_a_session_to_another_directory(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(cwd="/repo/real"))
        store(conn, rec(cwd="/somewhere/else"), parsed_offset=99)
        assert session_row(conn, "S1")["cwd"] == "/repo/real"

    def test_a_missing_cwd_is_still_filled_in_later(self, tmp_path):
        """Only drift is refused. A row that never had a cwd must still get one
        — a history.jsonl skeleton has none until a transcript supplies it."""
        conn = connect(tmp_path / "i.sqlite")
        store(conn, rec(cwd=None))
        store(conn, rec(cwd="/repo/real"), parsed_offset=99)
        assert session_row(conn, "S1")["cwd"] == "/repo/real"


CODEX_HEAD = [
    {"timestamp": "2026-07-31T12:22:08.000Z", "type": "session_meta",
     "payload": {"id": "C9", "timestamp": "2026-07-31T12:22:08.000Z", "cwd": "/repo",
                 "originator": "codex-tui", "source": "cli", "cli_version": "0.146.0"}},
    {"timestamp": "2026-07-31T12:22:20.000Z", "type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "review the spec"}]}},
]
CODEX_TAIL = [
    {"timestamp": "2026-07-31T12:40:00.000Z", "type": "response_item",
     "payload": {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "the 40-line review"}]}},
]


class TestASessionThatGrewAfterItWasIndexed:
    """codex writes `session_meta` on the first line and nowhere else, so a
    reader starting from `parsed_offset` sees no identity and returns nothing.

    That made the incremental pass drop every appended turn **silently**: the
    session stayed at its old length, `parsed_offset` never advanced, and each
    later pass re-read and re-dropped the same tail. Only `--rebuild` recovered
    it — contradicting the standing rule that rebuild is for derivation changes
    and incremental handles growth.

    The dropped turn is always the most recent one, which for an answer session
    is the entire payload. And it reads as a session that was interrupted and
    never replied, so the failure argues for a wrong conclusion rather than
    announcing itself.
    """

    def test_a_codex_tail_cannot_identify_itself(self, tmp_path):
        """The reader behaviour the fix has to work around, pinned so a change
        to it is visible rather than silently making the workaround dead code."""
        from scad.readers import read_codex_rollout

        p = tmp_path / "rollout-x.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in CODEX_HEAD + CODEX_TAIL))
        _, _, end = read_codex_rollout(p, 0)
        half = len(json.dumps(CODEX_HEAD[0]) + "\n")
        session, turns, _ = read_codex_rollout(p, half)
        assert session is None and turns == []

    def test_appended_codex_turns_are_indexed_not_dropped(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        rel = "codex/2026/07/31/rollout-2026-07-31T12-22-08-C9.jsonl"
        p = arc_write(arc, rel, CODEX_HEAD)

        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        before = session_row(conn, "C9")["n_turns"]

        with p.open("a") as fh:
            for record in CODEX_TAIL:
                fh.write(json.dumps(record) + "\n")
        stats = reindex(conn)

        row = session_row(conn, "C9")
        assert row["n_turns"] > before, "the appended turn was dropped"
        assert stats["turns"] >= 1
        texts = [r["text"] for r in conn.execute(
            "SELECT text FROM turns WHERE session_id='C9' ORDER BY idx")]
        assert "the 40-line review" in texts

    def test_the_offset_advances_so_a_third_pass_is_quiet(self, tmp_path, monkeypatch):
        """The old failure left `parsed_offset` behind the file forever, so
        every pass re-read the same bytes and re-dropped them."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        rel = "codex/2026/07/31/rollout-2026-07-31T12-22-08-C9.jsonl"
        p = arc_write(arc, rel, CODEX_HEAD)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        with p.open("a") as fh:
            fh.write(json.dumps(CODEX_TAIL[0]) + "\n")
        reindex(conn)
        row = session_row(conn, "C9")
        assert row["parsed_offset"] == p.stat().st_size
        assert reindex(conn)["turns"] == 0

    def test_growth_does_not_duplicate_what_was_already_stored(self, tmp_path, monkeypatch):
        """The fix re-reads the whole file, so the turns already in the index
        must not be appended a second time — `append_turns` numbers by position
        and cannot tell a duplicate from a new turn."""
        arc = tmp_path / "arc"
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        rel = "codex/2026/07/31/rollout-2026-07-31T12-22-08-C9.jsonl"
        p = arc_write(arc, rel, CODEX_HEAD)
        conn = connect(tmp_path / "i.sqlite")
        reindex(conn)
        with p.open("a") as fh:
            fh.write(json.dumps(CODEX_TAIL[0]) + "\n")
        reindex(conn)
        texts = [r["text"] for r in conn.execute(
            "SELECT text FROM turns WHERE session_id='C9' ORDER BY idx")]
        assert texts.count("review the spec") == 1
