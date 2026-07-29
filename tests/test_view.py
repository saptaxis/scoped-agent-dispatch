"""Tests for the session viewer."""

from scad.live import TmuxPane
from scad.view import Reentry, reentry_for


def row(**kw) -> dict:
    base = {"id": "S1", "agent": "claude", "cwd": "/Users/vsr/code/scad",
            "scad_run_id": None, "kind": "main"}
    base.update(kw)
    return base


class TestReentry:
    def test_live_tmux_pane_wins(self):
        panes = [TmuxPane("main:1.2", "/Users/vsr/code/scad", "2.1.219")]
        r = reentry_for(row(), panes, set())
        assert r.kind == "tmux"
        assert r.command == "tmux select-window -t main:1.2"

    def test_a_pane_in_the_same_cwd_that_is_not_an_agent_is_ignored(self):
        """Three panes share a directory on the real machine; only agent panes count."""
        panes = [TmuxPane("main:2.0", "/Users/vsr/code/scad", "zsh")]
        r = reentry_for(row(), panes, set())
        assert r.kind == "resume"

    def test_several_agent_panes_list_all_candidates(self):
        panes = [
            TmuxPane("main:1.2", "/Users/vsr/code/scad", "2.1.219"),
            TmuxPane("main:3.0", "/Users/vsr/code/scad", "2.1.205"),
        ]
        r = reentry_for(row(), panes, set())
        assert r.kind == "tmux"
        assert "main:1.2" in r.command
        assert "main:3.0" in r.note
        assert "ambiguous" in r.note.lower()

    def test_running_container_beats_resume(self):
        r = reentry_for(row(scad_run_id="demo-Jul28-1200"), [], {"demo-Jul28-1200"})
        assert r.kind == "container"
        assert r.command == "scad run attach demo-Jul28-1200"

    def test_dead_container_falls_back_to_resume(self):
        r = reentry_for(row(scad_run_id="old-Mar01-1200"), [], set())
        assert r.kind == "resume"

    def test_closed_claude_session_resumes_by_uuid(self):
        r = reentry_for(row(), [], set())
        assert r.kind == "resume"
        assert r.command == "cd /Users/vsr/code/scad && claude --resume S1"

    def test_closed_codex_session_uses_codex_resume(self):
        r = reentry_for(row(agent="codex", id="C9"), [], set())
        assert r.command == "cd /Users/vsr/code/scad && codex resume C9"

    def test_a_cwd_that_no_longer_exists_still_produces_a_command(self):
        """Recorded cwds outlive their directories; the command is still the best hint."""
        r = reentry_for(row(cwd="/gone/away"), [], set())
        assert "cd /gone/away" in r.command

    def test_missing_cwd_resumes_without_a_cd(self):
        r = reentry_for(row(cwd=None), [], set())
        assert r.command == "claude --resume S1"

    def test_subagent_has_no_reentry(self):
        """You cannot resume a subagent — you resume its parent."""
        r = reentry_for(row(kind="subagent"), [], set())
        assert r.kind == "none"
        assert r.command == ""


import time

from scad.index import append_turns, connect, upsert_session
from scad.records import KIND_MAIN, SessionRecord, TurnRecord
from scad.view import gather


def _store(conn, sid, outcome, ended_days_ago=0, cwd="/repo", agent="claude", kind=KIND_MAIN):
    ended = int((time.time() - ended_days_ago * 86400) * 1000)
    rec = SessionRecord(id=sid, kind=kind, agent=agent, source="claude-transcript",
                        cwd=cwd, started=ended - 1000, ended=ended, outcome=outcome)
    upsert_session(conn, rec, machine="mac", project="proj", archive_path=f"/arc/{sid}.jsonl",
                   source_size=1, source_mtime=1, parsed_offset=1)
    return rec


class TestGather:
    def test_waiting_holds_only_sessions_awaiting_input(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user")
        _store(conn, "Q1", "awaiting-question")
        _store(conn, "D1", "tool-result-last")
        data = gather(conn, [], set())
        assert {r["id"] for r in data["waiting"]} == {"W1", "Q1"}

    def test_questions_sort_before_plain_waiting(self, tmp_path):
        """An explicit question is a stronger claim on your attention."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user", ended_days_ago=5)
        _store(conn, "Q1", "awaiting-question", ended_days_ago=1)
        assert [r["id"] for r in gather(conn, [], set())["waiting"]] == ["Q1", "W1"]

    def test_oldest_first_within_a_group_so_nothing_rots(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "NEW", "awaiting-user", ended_days_ago=1)
        _store(conn, "OLD", "awaiting-user", ended_days_ago=6)
        assert [r["id"] for r in gather(conn, [], set())["waiting"]] == ["OLD", "NEW"]

    def test_window_excludes_ancient_sessions(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "OLD", "awaiting-user", ended_days_ago=90)
        assert gather(conn, [], set(), days=14)["waiting"] == []

    def test_subagents_never_appear_in_waiting(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "SUB", "awaiting-user", kind="subagent")
        assert gather(conn, [], set())["waiting"] == []

    def test_live_lists_sessions_with_a_matching_pane(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "L1", "tool-result-last", cwd="/repo")
        panes = [TmuxPane("main:1.0", "/repo", "2.1.219")]
        data = gather(conn, panes, set())
        assert [r["id"] for r in data["live"]] == ["L1"]

    def test_every_row_carries_a_reentry(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user")
        r = gather(conn, [], set())["waiting"][0]
        assert r["reentry"]["kind"] == "resume"
        assert "claude --resume W1" in r["reentry"]["command"]

    def test_waiting_rows_carry_the_last_turn_text(self, tmp_path):
        """So you can remember where the conversation left off."""
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "W1", "awaiting-user")
        append_turns(conn, "W1", [
            TurnRecord(ts=1, role="assistant", kind="text", text="first"),
            TurnRecord(ts=2, role="assistant", kind="text", text="the last thing said"),
        ])
        assert gather(conn, [], set())["waiting"][0]["last_text"] == "the last thing said"

    def test_all_holds_every_session(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite")
        _store(conn, "A", "awaiting-user")
        _store(conn, "B", "tool-result-last", kind="subagent")
        assert len(gather(conn, [], set())["all"]) == 2
