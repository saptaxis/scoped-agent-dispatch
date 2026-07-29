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
