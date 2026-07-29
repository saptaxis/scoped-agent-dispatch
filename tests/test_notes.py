"""Tests for the notes store — the one tier that is never rederivable."""

import json
from pathlib import Path

import pytest

from scad.notes import (
    PROJECT_DIR_CAP,
    NOTE_FIELDS,
    append_note,
    encode_cwd,
    note_path,
    notes_root,
    read_note_file,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
    return tmp_path / ".scad"


class TestEncodeCwd:
    """Claude Code's projects/<encoded-cwd> naming.

    Verified against the shipped binary (2.1.220) and against real directory
    names on this machine. The function there is:

        function RA(e){let t=e.replace(/[^a-zA-Z0-9]/g,"-");
                       if(t.length<=200)return t;
                       return `${t.slice(0,200)}-${o0h(e)}`}

    so EVERY non-alphanumeric byte collapses to a dash — not just `/`. That is
    a lossy, one-way mapping: `a.b`, `a_b` and `a-b` all encode to `a-b`, which
    is why nothing here ever decodes.
    """

    def test_slashes_become_dashes_with_a_leading_one(self):
        assert encode_cwd("/Users/vsr/code") == "-Users-vsr-code"

    def test_dots_underscores_and_spaces_also_become_dashes(self):
        # Empirically confirmed: a real session in "/private/tmp/scad enc.test_dir-1"
        # landed in ~/.claude/projects/-private-tmp-scad-enc-test-dir-1
        assert encode_cwd("/private/tmp/scad enc.test_dir-1") == \
            "-private-tmp-scad-enc-test-dir-1"

    def test_existing_hyphens_survive_unchanged(self):
        assert encode_cwd("/Users/vsr/code/scoped-agent-dispatch") == \
            "-Users-vsr-code-scoped-agent-dispatch"

    def test_a_long_path_is_truncated_and_hash_suffixed(self):
        raw = "/" + "/".join(f"segment{i:03d}" for i in range(40))
        out = encode_cwd(raw)
        assert len(out) > PROJECT_DIR_CAP
        head, _, suffix = out.rpartition("-")
        assert head == "-".join(raw.split("/"))[:PROJECT_DIR_CAP].rstrip("-") or \
            head.startswith("-segment000")
        assert suffix.isalnum()

    def test_hash_suffix_matches_claude_codes_string_hash(self):
        # (t << 5) - t + charCode | 0, then Math.abs(...).toString(36)
        raw = "/" + "x" * 260
        out = encode_cwd(raw)
        h = 0
        for ch in raw:
            h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
            if h >= 0x80000000:
                h -= 0x100000000
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        n, expect = abs(h), ""
        while n:
            n, r = divmod(n, 36)
            expect = digits[r] + expect
        assert out.endswith("-" + (expect or "0"))


class TestStoreLayout:
    def test_notes_live_under_scad_home(self, home):
        assert notes_root() == home / "notes"

    def test_path_is_session_keyed_and_agent_sharded(self, home):
        assert note_path("abc-123", "claude") == home / "notes" / "claude" / "abc-123.jsonl"

    def test_no_project_appears_anywhere_in_the_path(self, home):
        # A project is computed, so a project in a durable path orphans the file
        # the moment the definition changes. This is the guard for that.
        p = note_path("abc-123", "claude")
        assert "unfiled" not in str(p) and "scoped-agent-dispatch" not in str(p)


class TestAppendNote:
    def test_writes_one_json_line_and_returns_the_path(self, home):
        p = append_note({"title": "t", "text": "body"}, session_id="S1")
        assert p == home / "notes" / "claude" / "S1.jsonl"
        lines = p.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["title"] == "t"

    def test_every_capture_format_field_is_present(self, home):
        p = append_note({"title": "t"}, session_id="S1")
        rec = json.loads(p.read_text())
        assert set(NOTE_FIELDS) <= set(rec)

    def test_cwd_at_write_is_recorded(self, home):
        # The project must stay rederivable from the note alone once every
        # trace has been pruned. Nothing else in the record carries a location.
        p = append_note({"title": "t"}, session_id="S1", cwd="/Users/vsr/code/orglens")
        assert json.loads(p.read_text())["cwd_at_write"] == "/Users/vsr/code/orglens"

    def test_defaults_fill_ts_and_span(self, home):
        rec = json.loads(append_note({"title": "t"}, session_id="S1").read_text())
        assert rec["span"] == "since-last"
        assert rec["ts"] and rec["ts"][:2] == "20"

    def test_caller_supplied_ts_and_span_win(self, home):
        rec = json.loads(append_note(
            {"title": "t", "ts": "2026-01-01T00:00:00", "span": "the whole plan"},
            session_id="S1").read_text())
        assert rec["ts"] == "2026-01-01T00:00:00"
        assert rec["span"] == "the whole plan"

    def test_appending_never_disturbs_the_earlier_bytes(self, home):
        p = append_note({"title": "first"}, session_id="S1")
        first = p.read_bytes()
        append_note({"title": "second"}, session_id="S1")
        after = p.read_bytes()
        assert after.startswith(first)
        assert [json.loads(l)["title"] for l in after.splitlines()] == ["first", "second"]

    def test_embedded_newlines_stay_on_one_physical_line(self, home):
        p = append_note({"title": "t", "text": "a\nb\nc"}, session_id="S1")
        assert len(p.read_text().splitlines()) == 1
        assert json.loads(p.read_text())["text"] == "a\nb\nc"

    def test_unknown_fields_are_carried_through(self, home):
        # The record shape belongs to capture-format.md, not to this store; a
        # field added there must not need a code change here to survive.
        rec = json.loads(append_note(
            {"title": "t", "confidence": "low"}, session_id="S1").read_text())
        assert rec["confidence"] == "low"

    def test_a_record_that_is_not_an_object_is_refused(self, home):
        with pytest.raises(ValueError):
            append_note([1, 2, 3], session_id="S1")

    def test_an_empty_session_id_is_refused(self, home):
        with pytest.raises(ValueError):
            append_note({"title": "t"}, session_id="")

    def test_a_session_id_that_is_a_path_is_refused(self, home):
        # The id names a file. A traversal here would write outside the store.
        with pytest.raises(ValueError):
            append_note({"title": "t"}, session_id="../../etc/passwd")


class TestReadNoteFile:
    def test_reads_back_in_append_order(self, home):
        append_note({"title": "first"}, session_id="S1")
        append_note({"title": "second"}, session_id="S1")
        assert [r["title"] for r in read_note_file(note_path("S1"))] == ["first", "second"]

    def test_missing_file_reads_as_empty(self, home):
        assert read_note_file(note_path("nope")) == []

    def test_a_malformed_line_is_skipped_not_fatal(self, home):
        p = append_note({"title": "good"}, session_id="S1")
        with p.open("a") as fh:
            fh.write("{not json\n")
        append_note({"title": "also good"}, session_id="S1")
        assert [r["title"] for r in read_note_file(p)] == ["good", "also good"]
