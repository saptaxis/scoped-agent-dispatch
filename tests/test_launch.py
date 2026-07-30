"""Tests for the launch record."""

import json

import pytest

from scad.launch import launches_root, read_record, record_path, write_record


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
    return tmp_path / ".scad"


RECORD = {
    "agent": "codex",
    "session_id": "019fb23c-6ce5-7580-b1b0-5a69839a946b",
    "cwd": "/Users/vsr/Desktop/scad-handoff-demo",
    "tmux": "scad-cx-1430:0.0",
    "started": "2026-07-30T14:30:00Z",
    "resume": "cd /Users/vsr/Desktop/scad-handoff-demo && codex resume 019fb23c",
    "provenance": "tui-native",
}


class TestWhereRecordsLive:
    def test_one_file_per_session_under_scad_home(self, _home):
        assert record_path("S1") == _home / "launches" / "S1.json"

    def test_the_root_is_not_the_archive(self, _home):
        """`reindex --rebuild` drops and rebuilds the index from the archive.

        A launch record is an authored fact about an event — nothing can
        recompute it — so it lives beside notes, not inside the archive.
        """
        assert launches_root() == _home / "launches"


class TestRoundTrip:
    def test_what_was_written_is_what_is_read(self):
        write_record(RECORD)
        assert read_record(RECORD["session_id"]) == RECORD

    def test_the_file_is_json_a_human_can_read(self):
        path = write_record(RECORD)
        assert json.loads(path.read_text())["provenance"] == "tui-native"
        assert path.read_text().endswith("\n")

    def test_an_unknown_session_has_no_record(self):
        assert read_record("never-launched") is None

    def test_a_record_with_no_session_id_is_refused(self):
        with pytest.raises(ValueError):
            write_record({k: v for k, v in RECORD.items() if k != "session_id"})


class TestDegradingRatherThanRaising:
    """Reading a record is on the resume path, which must still work when the
    record is unreadable — the index alone is enough to resume (§5)."""

    def test_a_corrupt_record_reads_as_absent(self, _home):
        path = record_path("S1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert read_record("S1") is None

    def test_a_record_that_is_not_an_object_reads_as_absent(self, _home):
        path = record_path("S1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('["a list"]')
        assert read_record("S1") is None

    def test_a_session_id_that_is_not_a_filename_cannot_escape_the_store(self):
        """An id arrives from the command line, so it names a path component
        only if we insist that it does."""
        assert read_record("../../etc/passwd") is None
        with pytest.raises(ValueError):
            write_record({**RECORD, "session_id": "../escape"})
