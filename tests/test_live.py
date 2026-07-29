"""Tests for live process discovery."""

import subprocess
from unittest.mock import patch

from scad.live import TmuxPane, is_agent_command, tmux_panes

SAMPLE = """main:0.0|/Users/vsr|htop
main:1.2|/Users/vsr/code/orgdeck|2.1.219
main:2.0|/Users/vsr/code/docs|zsh
main:3.0|/Users/vsr/code/scad|2.1.205
work:0.1|/Users/vsr/code/nd|codex
"""


def fake_run(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestAgentCommand:
    def test_claude_panes_show_a_version_string_not_a_name(self):
        """Claude Code appears in tmux as its version, e.g. 2.1.219."""
        assert is_agent_command("2.1.219") is True
        assert is_agent_command("2.1.205") is True

    def test_codex_shows_its_name(self):
        assert is_agent_command("codex") is True

    def test_shells_and_tools_are_not_agents(self):
        for cmd in ("zsh", "bash", "htop", "python3.12", "vim", ""):
            assert is_agent_command(cmd) is False

    def test_a_version_needs_at_least_two_dots(self):
        assert is_agent_command("2.1") is False
        assert is_agent_command("12") is False


class TestTmuxPanes:
    def test_parses_the_format_output(self):
        with patch("scad.live.subprocess.run", return_value=fake_run(SAMPLE)):
            panes = tmux_panes()
        assert len(panes) == 5
        assert panes[1] == TmuxPane(target="main:1.2", path="/Users/vsr/code/orgdeck", command="2.1.219")

    def test_no_tmux_server_yields_empty(self):
        """`tmux list-panes` exits non-zero when no server is running."""
        with patch("scad.live.subprocess.run", return_value=fake_run("no server running", 1)):
            assert tmux_panes() == []

    def test_tmux_not_installed_yields_empty(self):
        with patch("scad.live.subprocess.run", side_effect=FileNotFoundError):
            assert tmux_panes() == []

    def test_timeout_yields_empty(self):
        with patch("scad.live.subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
            assert tmux_panes() == []

    def test_malformed_lines_are_skipped_not_fatal(self):
        with patch("scad.live.subprocess.run", return_value=fake_run("garbage\nmain:1.2|/p|zsh\n")):
            panes = tmux_panes()
        assert [p.target for p in panes] == ["main:1.2"]

    def test_paths_with_pipes_do_not_break_parsing(self):
        """Split from the left on a fixed field count, not naively."""
        with patch("scad.live.subprocess.run", return_value=fake_run("main:0.0|/we|rd|zsh\n")):
            panes = tmux_panes()
        assert panes[0].command == "zsh"
        assert panes[0].path == "/we|rd"
