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
