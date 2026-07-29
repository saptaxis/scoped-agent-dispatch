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


# --- --current: which session's trace is being written in this cwd right now ---

import os as _os  # noqa: E402

from scad.notes import (  # noqa: E402
    LIVE_WINDOW_S,
    SESSION_ID_ENV,
    AmbiguousSession,
    NoSessionFound,
    NoteTargetError,
    current_session_id,
)


def _transcript(projects: Path, cwd: str, session_id: str, *, age_s: float = 0.0,
                encoded: str | None = None) -> Path:
    from scad.notes import encode_cwd
    d = projects / (encoded or encode_cwd(cwd))
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text(json.dumps({"sessionId": session_id, "cwd": cwd, "type": "user"}) + "\n")
    now = p.stat().st_mtime
    _os.utime(p, (now - age_s, now - age_s))
    return p


class TestCurrentSession:
    def test_takes_the_newest_transcript_in_the_encoded_directory(self, tmp_path):
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "older", age_s=4000)
        _transcript(projects, "/w/proj", "newest", age_s=0)
        assert current_session_id("/w/proj", projects_root=projects) == "newest"

    def test_history_in_the_same_directory_does_not_make_it_ambiguous(self, tmp_path):
        # The point of the recency window: a busy project accumulates dozens of
        # dead transcripts, and none of them is a live session.
        projects = tmp_path / "projects"
        for i in range(6):
            _transcript(projects, "/w/proj", f"dead{i}", age_s=LIVE_WINDOW_S + 60 + i)
        _transcript(projects, "/w/proj", "live", age_s=1)
        assert current_session_id("/w/proj", projects_root=projects) == "live"

    def test_two_live_sessions_in_one_cwd_refuse_to_guess(self, tmp_path):
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "alpha", age_s=1)
        _transcript(projects, "/w/proj", "beta", age_s=2)
        with pytest.raises(AmbiguousSession) as exc:
            current_session_id("/w/proj", projects_root=projects)
        assert "alpha" in str(exc.value) and "beta" in str(exc.value)
        assert "--session" in str(exc.value)

    def test_subagent_transcripts_are_not_candidates(self, tmp_path):
        # A subagent file's sessionId is its PARENT's, and its stem is an agentId.
        # Taking one as the current session would key the note onto the wrong row.
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "main", age_s=30)
        sub = projects / encode_cwd("/w/proj") / "main" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-xyz.jsonl").write_text("{}\n")
        assert current_session_id("/w/proj", projects_root=projects) == "main"

    def test_nothing_at_all_raises_rather_than_inventing_an_id(self, tmp_path):
        with pytest.raises(NoSessionFound):
            current_session_id("/w/proj", projects_root=tmp_path / "projects")

    def test_cwd_is_realpathed_before_encoding(self, tmp_path):
        # /tmp is a symlink to /private/tmp on macOS, and Claude Code encodes the
        # resolved path — an unresolved cwd would look at a directory that has
        # never existed.
        projects = tmp_path / "projects"
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        _transcript(projects, str(real.resolve()), "S1")
        assert current_session_id(str(link), projects_root=projects) == "S1"

    def test_falls_back_to_scanning_cwd_fields_when_the_directory_is_absent(self, tmp_path):
        # A future Claude Code could change the naming and this would silently
        # find nothing; the records themselves carry the cwd, so scan for it.
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "S1", encoded="some-other-naming-scheme")
        assert current_session_id("/w/proj", projects_root=projects) == "S1"

    def test_the_fallback_scan_also_takes_the_newest(self, tmp_path):
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "old", age_s=9000, encoded="other-a")
        _transcript(projects, "/w/proj", "new", age_s=0, encoded="other-b")
        assert current_session_id("/w/proj", projects_root=projects) == "new"

    def test_defaults_to_the_process_cwd(self, tmp_path, monkeypatch):
        projects = tmp_path / "projects"
        work = tmp_path / "work"
        work.mkdir()
        _transcript(projects, str(work.resolve()), "S1")
        monkeypatch.chdir(work)
        assert current_session_id(projects_root=projects) == "S1"


class TestCurrentSessionFromEnv:
    """The agent tells us its own session id; ask it before searching for it.

    Each harness exports the id of the session it is running, so `--current`
    is a lookup rather than a guess. The one hazard is inheritance: those
    variables are ordinary environment variables and a codex launched from a
    Claude session carries `CLAUDE_CODE_SESSION_ID` with it. An agent may
    therefore only ever read its OWN variable.
    """

    def test_claude_returns_the_exported_id_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-claude-id")
        assert current_session_id(
            "/w/proj", projects_root=tmp_path / "nothing-here") == "env-claude-id"

    def test_claude_does_not_touch_the_filesystem_when_the_var_is_set(
            self, tmp_path, monkeypatch):
        # Not just "the same answer" — the scan must not run at all, or a
        # project with two live transcripts would still be ambiguous.
        import scad.notes as notes_mod
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "on-disk-a", age_s=1)
        _transcript(projects, "/w/proj", "on-disk-b", age_s=2)
        monkeypatch.setattr(notes_mod, "_transcripts_in",
                            lambda d: pytest.fail("scanned the filesystem"))
        monkeypatch.setattr(notes_mod, "_scan_for_cwd",
                            lambda r, c: pytest.fail("scanned the filesystem"))
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-claude-id")
        assert current_session_id("/w/proj", projects_root=projects) == "env-claude-id"

    def test_claude_falls_back_to_the_cwd_scan_when_the_var_is_absent(self, tmp_path):
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "from-the-scan")
        assert current_session_id("/w/proj", projects_root=projects) == "from-the-scan"

    def test_a_blank_variable_counts_as_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "   ")
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "from-the-scan")
        assert current_session_id("/w/proj", projects_root=projects) == "from-the-scan"

    def test_codex_reads_its_own_variable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODEX_THREAD_ID", "codex-thread-1")
        assert current_session_id(
            "/w/proj", agent="codex", projects_root=tmp_path) == "codex-thread-1"

    def test_codex_never_answers_with_an_inherited_claude_id(self, tmp_path, monkeypatch):
        # The inheritance trap. `codex` launched from a Claude session inherits
        # CLAUDE_CODE_SESSION_ID; answering with it would file the note into
        # the codex shard under a Claude session's id, and nothing downstream
        # could tell.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-claude-one")
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "the-claude-one")
        with pytest.raises(NoSessionFound) as exc:
            current_session_id("/w/proj", agent="codex", projects_root=projects)
        assert "the-claude-one" not in str(exc.value)
        assert "CODEX_THREAD_ID" in str(exc.value)
        assert "--session" in str(exc.value)

    def test_a_non_claude_agent_never_scans_claudes_transcripts(self, tmp_path, monkeypatch):
        # There is no fallback for codex, and claude's directory is not one.
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "a-claude-session")
        with pytest.raises(NoSessionFound) as exc:
            current_session_id("/w/proj", agent="codex", projects_root=projects)
        assert "a-claude-session" not in str(exc.value)

    def test_an_agent_with_no_known_variable_refuses_clearly(self, tmp_path, monkeypatch):
        # kimi exports nothing. Say so and name the flag rather than guessing.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-claude-one")
        projects = tmp_path / "projects"
        _transcript(projects, "/w/proj", "a-claude-session")
        with pytest.raises(NoSessionFound) as exc:
            current_session_id("/w/proj", agent="kimi", projects_root=projects)
        assert "kimi" in str(exc.value)
        assert "the-claude-one" not in str(exc.value)
        assert "--session" in str(exc.value)

    @pytest.mark.parametrize("bogus", ["../../etc/passwd", "a/b", "..", "a\\b"])
    def test_an_id_that_is_not_a_path_component_is_refused(self, tmp_path, monkeypatch, bogus):
        # The value becomes a filename under ~/.scad/notes. A traversal in the
        # environment must not become a write outside the store.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", bogus)
        with pytest.raises(NoteTargetError) as exc:
            current_session_id("/w/proj", projects_root=tmp_path / "projects")
        assert "CLAUDE_CODE_SESSION_ID" in str(exc.value)

    def test_the_map_is_one_named_constant(self):
        assert SESSION_ID_ENV["claude"] == "CLAUDE_CODE_SESSION_ID"
        assert SESSION_ID_ENV["codex"] == "CODEX_THREAD_ID"
