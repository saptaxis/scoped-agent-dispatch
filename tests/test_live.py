"""Tests for live process discovery."""

import subprocess
from unittest.mock import patch

from scad.live import TmuxPane, is_agent_command, tmux_panes

# session:window.pane | window_name | path | command
SAMPLE = """main:0.0|shell|/Users/vsr|htop
main:1.2|services|/Users/vsr/code/orgdeck|2.1.219
main:2.0|docs|/Users/vsr/code/docs|zsh
main:3.0|scad|/Users/vsr/code/scad|2.1.205
work:0.1|nd|/Users/vsr/code/nd|codex
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
        assert panes[1] == TmuxPane(target="main:1.2", path="/Users/vsr/code/orgdeck",
                                    command="2.1.219", window="services")

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
        with patch("scad.live.subprocess.run", return_value=fake_run("garbage\nmain:1.2|w|/p|zsh\n")):
            panes = tmux_panes()
        assert [p.target for p in panes] == ["main:1.2"]

    def test_paths_with_pipes_do_not_break_parsing(self):
        """Split from the left on a fixed field count, not naively."""
        with patch("scad.live.subprocess.run", return_value=fake_run("main:0.0|win|/we|rd|zsh\n")):
            panes = tmux_panes()
        assert panes[0].command == "zsh"
        assert panes[0].path == "/we|rd"
        assert panes[0].window == "win"


from scad.live import running_run_ids


class TestRunningContainers:
    def test_strips_the_scad_prefix(self):
        out = "scad-demo-Jul28-1200\nscad-other-Jul29-0900\n"
        with patch("scad.live.subprocess.run", return_value=fake_run(out)):
            assert running_run_ids() == {"demo-Jul28-1200", "other-Jul29-0900"}

    def test_ignores_containers_that_are_not_scad(self):
        with patch("scad.live.subprocess.run", return_value=fake_run("postgres\nscad-x-Jul1-0000\n")):
            assert running_run_ids() == {"x-Jul1-0000"}

    def test_docker_missing_yields_empty(self):
        with patch("scad.live.subprocess.run", side_effect=FileNotFoundError):
            assert running_run_ids() == set()

    def test_docker_error_yields_empty(self):
        with patch("scad.live.subprocess.run", return_value=fake_run("cannot connect", 1)):
            assert running_run_ids() == set()

    def test_timeout_yields_empty(self):
        with patch("scad.live.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 5)):
            assert running_run_ids() == set()

    def test_uses_scads_docker_env_not_the_ambient_one(self):
        """On macOS the bare docker CLI reaches the wrong daemon and finds nothing —
        indistinguishable from 'no containers running'. Verified on this machine."""
        with patch("scad.live.subprocess.run", return_value=fake_run("")) as run:
            with patch("scad.live._docker_env", return_value={"DOCKER_HOST": "unix:///x.sock"}):
                running_run_ids()
        assert run.call_args.kwargs["env"] == {"DOCKER_HOST": "unix:///x.sock"}

    def test_a_broken_vm_module_does_not_break_discovery(self):
        with patch("scad.live._docker_env", side_effect=Exception("boom")):
            with patch("scad.live.subprocess.run", return_value=fake_run("")):
                try:
                    running_run_ids()
                except Exception:
                    raise AssertionError("discovery must never raise")


class TestAgentPanes:
    def test_lists_only_agent_panes_across_every_tmux_session(self):
        """`list-panes -a` spans all sessions, so main, main2 and the rest are
        covered without enumerating them."""
        from scad.live import agent_panes
        out = ("main:1.0|services|/a|2.1.215\n"
               "main:2.0|docs|/b|zsh\n"
               "main2:0.1|other|/c|codex\n")
        with patch("scad.live.subprocess.run", return_value=fake_run(out)):
            panes = agent_panes()
        assert [p.target for p in panes] == ["main:1.0", "main2:0.1"]

    def test_several_agents_in_one_window_all_appear(self):
        """main:3 really holds three on this machine — two claude and a codex."""
        from scad.live import agent_panes
        out = ("main:3.0|scad|/repo|2.1.205\n"
               "main:3.1|scad|/repo|codex\n"
               "main:3.2|scad|/repo|2.1.220\n")
        with patch("scad.live.subprocess.run", return_value=fake_run(out)):
            assert len(agent_panes()) == 3

    def test_session_name_is_derived_from_the_target(self):
        assert TmuxPane("main2:3.1", "/p", "codex").session == "main2"


import json
import time
from pathlib import Path

from scad.live import ClaudeSession, claude_live_sessions

# Every test here points the reader at a tmp_path. Nothing in this file may
# read the real ~/.claude/sessions: the suite runs from inside one of the very
# agents it models, so a real registry would make these tests pass for reasons
# that have nothing to do with the code.

START = 1785307502.0  # a fixed process start, in epoch seconds


def ctime_utc(epoch: float) -> str:
    """`procStart` as Claude writes it: ctime format, rendered in UTC.

    Measured on this machine: `procStart` read as UTC equals the kernel's
    process start time to the second for all 12 live entries.
    """
    return time.asctime(time.gmtime(epoch))


def write_entry(directory: Path, stem: int, **overrides) -> dict:
    """One <pid>.json exactly as Claude Code writes it.

    `stem` names the file and, unless overridden, the pid inside it — the two
    are separable so a test can write a file whose name and contents disagree.
    """
    record = {
        "pid": stem,
        "sessionId": f"sid-{stem}",
        "cwd": "/Users/vsr/code/scad",
        "startedAt": 1785307504101,
        "procStart": ctime_utc(START),
        "version": "2.1.220",
        "peerProtocol": 1,
        "kind": "interactive",
        "entrypoint": "cli",
        "name": f"name-{stem}",
        "status": "idle",
        "updatedAt": 1785307545649,
    }
    record.update(overrides)
    for key in [k for k, v in record.items() if v is None]:
        del record[key]
    (directory / f"{stem}.json").write_text(json.dumps(record))
    return record


def alive(*pids, starts=None):
    """Patch both liveness checks: every pid given is running, and started at START."""
    starts = {p: START for p in pids} if starts is None else starts
    return (
        patch("scad.live.os.kill", side_effect=lambda p, s: None if p in pids else _dead(p)),
        patch("scad.live._process_start_times", return_value=starts),
    )


def _dead(pid):
    raise ProcessLookupError(pid)


class TestClaudeLiveSessions:
    def test_reads_one_record_per_live_registry_entry(self, tmp_path):
        write_entry(tmp_path, 30036, name="jul25-resolver-session-cli",
                    status="waiting", waitingFor="permission prompt")
        kill, starts = alive(30036)
        with kill, starts:
            sessions = claude_live_sessions(tmp_path)
        assert sessions == [ClaudeSession(
            session_id="sid-30036", pid=30036, cwd="/Users/vsr/code/scad",
            name="jul25-resolver-session-cli", status="waiting",
            waiting_for="permission prompt", started_at=1785307504101,
            kind="interactive", entrypoint="cli", version="2.1.220",
        )]

    def test_waiting_for_is_empty_when_the_session_is_not_blocked(self, tmp_path):
        write_entry(tmp_path, 64757, status="busy")
        kill, starts = alive(64757)
        with kill, starts:
            assert claude_live_sessions(tmp_path)[0].waiting_for == ""

    def test_a_registry_file_alone_does_not_prove_the_process_lives(self, tmp_path):
        """The file outlives the process; only a signal-0 kill settles it."""
        write_entry(tmp_path, 111)
        write_entry(tmp_path, 222)
        kill, starts = alive(222)
        with kill, starts:
            assert [s.pid for s in claude_live_sessions(tmp_path)] == [222]

    def test_a_pid_owned_by_another_user_counts_as_alive(self, tmp_path):
        """EPERM means the process exists — we just may not signal it."""
        write_entry(tmp_path, 333)
        with patch("scad.live.os.kill", side_effect=PermissionError):
            with patch("scad.live._process_start_times", return_value={333: START}):
                assert [s.pid for s in claude_live_sessions(tmp_path)] == [333]


class TestPidReuseGuard:
    def test_a_recycled_pid_is_not_the_session_the_file_names(self, tmp_path):
        """Same pid, different process: its start time cannot match procStart."""
        write_entry(tmp_path, 30036)
        kill, starts = alive(30036, starts={30036: START + 900})
        with kill, starts:
            assert claude_live_sessions(tmp_path) == []

    def test_a_second_of_skew_is_tolerated(self, tmp_path):
        """ps truncates to the second; a rounding disagreement is not reuse."""
        write_entry(tmp_path, 30036)
        kill, starts = alive(30036, starts={30036: START + 1})
        with kill, starts:
            assert len(claude_live_sessions(tmp_path)) == 1

    def test_an_unverifiable_process_is_dropped_not_assumed(self, tmp_path):
        """No start time for the pid means we cannot know, so we do not claim."""
        write_entry(tmp_path, 30036)
        kill, starts = alive(30036, starts={})
        with kill, starts:
            assert claude_live_sessions(tmp_path) == []

    def test_an_entry_without_proc_start_cannot_be_verified(self, tmp_path):
        write_entry(tmp_path, 30036, procStart=None)
        kill, starts = alive(30036)
        with kill, starts:
            assert claude_live_sessions(tmp_path) == []

    def test_started_at_is_not_used_as_the_guard(self, tmp_path):
        """Measured: startedAt trails procStart by up to 838s (session start, not
        process start), so it would reject live sessions if used as the check."""
        write_entry(tmp_path, 30036, startedAt=int((START + 838) * 1000))
        kill, starts = alive(30036)
        with kill, starts:
            assert len(claude_live_sessions(tmp_path)) == 1

    def test_a_file_whose_name_disagrees_with_its_pid_is_dropped(self, tmp_path):
        """The filename is the registry key; a mismatch means we cannot trust it."""
        (tmp_path / "999.json").write_text(json.dumps({
            "pid": 30036, "sessionId": "s", "procStart": ctime_utc(START)}))
        kill, starts = alive(30036, 999)
        with kill, starts:
            assert claude_live_sessions(tmp_path) == []


class TestDegradesToEmpty:
    def test_missing_directory_yields_empty(self, tmp_path):
        assert claude_live_sessions(tmp_path / "nope") == []

    def test_one_bad_file_does_not_lose_the_others(self, tmp_path):
        (tmp_path / "1.json").write_text("{not json")
        write_entry(tmp_path, 30036)
        write_entry(tmp_path, 64757)
        kill, starts = alive(1, 30036, 64757)
        with kill, starts:
            assert len(claude_live_sessions(tmp_path)) == 2

    def test_an_unreadable_entry_is_skipped(self, tmp_path):
        (tmp_path / "1.json").mkdir()  # read_text raises IsADirectoryError
        write_entry(tmp_path, 30036)
        kill, starts = alive(1, 30036)
        with kill, starts:
            assert [s.pid for s in claude_live_sessions(tmp_path)] == [30036]

    def test_entries_missing_the_identifying_keys_are_skipped(self, tmp_path):
        write_entry(tmp_path, 10, sessionId=None)
        write_entry(tmp_path, 11, pid=None)
        write_entry(tmp_path, 12, pid="not-a-pid")
        write_entry(tmp_path, 30036)
        kill, starts = alive(10, 11, 12, 30036)
        with kill, starts:
            assert [s.pid for s in claude_live_sessions(tmp_path)] == [30036]

    def test_json_that_is_not_an_object_is_skipped(self, tmp_path):
        (tmp_path / "1.json").write_text("[1, 2, 3]")
        kill, starts = alive(1)
        with kill, starts:
            assert claude_live_sessions(tmp_path) == []

    def test_non_json_files_are_ignored(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        assert claude_live_sessions(tmp_path) == []

    def test_no_ps_means_nothing_can_be_verified(self, tmp_path):
        """Without a start time for any pid the whole result degrades to empty —
        the same as today's behaviour, never a wrong 'open'."""
        write_entry(tmp_path, 30036)
        with patch("scad.live.os.kill", return_value=None):
            with patch("scad.live.subprocess.run", side_effect=FileNotFoundError):
                assert claude_live_sessions(tmp_path) == []

    def test_the_default_registry_is_under_the_users_claude_directory(self):
        from scad.live import _sessions_dir
        assert _sessions_dir() == Path.home() / ".claude" / "sessions"


class TestProcessStartTimes:
    def test_parses_the_ps_listing(self):
        from scad.live import _process_start_times
        out = " 8453 Thu Jul  9 17:45:33 2026\n30036 Wed Jul 29 12:15:02 2026\n"
        with patch("scad.live.subprocess.run", return_value=fake_run(out)):
            starts = _process_start_times([8453, 30036])
        assert set(starts) == {8453, 30036}
        assert starts[8453] == time.mktime(time.strptime("Thu Jul  9 17:45:33 2026"))

    def test_ps_reports_local_time_and_the_registry_reports_utc(self):
        """Both are normalised to epoch seconds, so the comparison is
        timezone-free even though the two sources disagree on rendering."""
        from scad.live import _process_start_times
        local = time.asctime(time.localtime(START))
        with patch("scad.live.subprocess.run", return_value=fake_run(f"7 {local}\n")):
            assert _process_start_times([7])[7] == START

    def test_dead_pids_that_ps_omits_are_simply_absent(self):
        from scad.live import _process_start_times
        with patch("scad.live.subprocess.run", return_value=fake_run("", 1)):
            assert _process_start_times([1, 2]) == {}

    def test_garbage_lines_are_skipped(self):
        from scad.live import _process_start_times
        out = "oops\n30036 Wed Jul 29 12:15:02 2026\n42 not a date\n"
        with patch("scad.live.subprocess.run", return_value=fake_run(out)):
            assert list(_process_start_times([30036])) == [30036]

    def test_no_pids_means_no_subprocess_at_all(self):
        from scad.live import _process_start_times
        with patch("scad.live.subprocess.run") as run:
            assert _process_start_times([]) == {}
        run.assert_not_called()

    def test_a_ps_timeout_yields_empty(self):
        from scad.live import _process_start_times
        with patch("scad.live.subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            assert _process_start_times([1]) == {}
