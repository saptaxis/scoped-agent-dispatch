"""CLI tests."""

import json
import os
import re

import pytest
import click
from click.testing import CliRunner
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock, call

import docker
from scad.cli import main, _complete_run_ids, _complete_config_names, _relative_time, get_all_sessions, get_project_status, get_session_usage
from scad.vm import VMUnsupported


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def offline_view(monkeypatch):
    """Keep `scad view` off the developer's real Claude state.

    Two paths reach outside the tmp dirs and neither can be redirected by
    SCAD_HOME, because both are Claude Code's own directories: the live session
    registry under `~/.claude/sessions`, and the archive sweep that `view` now
    runs by default. Tests that care about the sweep patch `run_reindex`
    themselves; this stops everyone else from touching it.
    """
    monkeypatch.setattr("scad.view.claude_live_sessions", lambda *a, **k: [])
    monkeypatch.setattr("scad.cli.run_reindex", lambda *a, **k: {})


class TestRelativeTime:
    def test_just_now(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        assert _relative_time(now) == "just now"

    def test_minutes_ago(self):
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        assert "min ago" in _relative_time(past)

    def test_garbage_input(self):
        result = _relative_time("not-a-date")
        assert result == "not-a-date"

    def test_empty_string(self):
        assert _relative_time("") == "?"

    def test_none_input(self):
        assert _relative_time(None) == "?"

    def test_future_timestamp(self):
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = _relative_time(future)
        assert result == "just now"  # max(0, ...) clamps to 0


class TestCodeFetch:
    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli._config_for_run")
    def test_fetch_shows_results(self, mock_config, mock_fetch, mock_validate, runner):
        mock_config.return_value = MagicMock()
        mock_fetch.return_value = [{"repo": "code", "branch": "feat", "source": "/src"}]
        result = runner.invoke(main, ["code", "fetch", "test-run"])
        assert result.exit_code == 0
        assert "Fetched" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli._config_for_run")
    def test_fetch_nothing(self, mock_config, mock_fetch, mock_validate, runner):
        mock_config.return_value = MagicMock()
        mock_fetch.return_value = []
        result = runner.invoke(main, ["code", "fetch", "test-run"])
        assert result.exit_code == 0
        assert "Nothing to fetch" in result.output

    @patch("scad.cli._config_for_run")
    def test_fetch_not_found(self, mock_config, runner):
        mock_config.side_effect = click.ClickException("Cannot determine config")
        result = runner.invoke(main, ["code", "fetch", "nonexistent"])
        assert result.exit_code != 0


class TestCodeSync:
    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.sync_from_host")
    @patch("scad.cli._config_for_run")
    def test_sync_shows_results(self, mock_config, mock_sync, mock_validate, runner):
        mock_config.return_value = MagicMock()
        mock_sync.return_value = [{"repo": "code", "source": "/src", "main_updated": True}]
        result = runner.invoke(main, ["code", "sync", "test-run"])
        assert result.exit_code == 0
        assert "Synced" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.sync_from_host")
    @patch("scad.cli._config_for_run")
    def test_sync_nothing(self, mock_config, mock_sync, mock_validate, runner):
        mock_config.return_value = MagicMock()
        mock_sync.return_value = []
        result = runner.invoke(main, ["code", "sync", "test-run"])
        assert result.exit_code == 0
        assert "Nothing to sync" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.sync_from_host")
    @patch("scad.cli._config_for_run")
    def test_sync_with_checkout(self, mock_config, mock_sync, mock_validate, runner):
        mock_config.return_value = MagicMock()
        mock_sync.return_value = [{"repo": "code", "source": "/src", "main_updated": True}]
        result = runner.invoke(main, ["code", "sync", "test-run", "--checkout", "main"])
        assert result.exit_code == 0
        mock_sync.assert_called_once()
        _, kwargs = mock_sync.call_args
        assert kwargs.get("checkout") == "main"

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.sync_from_host")
    @patch("scad.cli._config_for_run")
    def test_sync_no_update_main(self, mock_config, mock_sync, mock_validate, runner):
        mock_config.return_value = MagicMock()
        mock_sync.return_value = [{"repo": "code", "source": "/src", "main_updated": None}]
        result = runner.invoke(main, ["code", "sync", "test-run", "--no-update-main"])
        assert result.exit_code == 0
        mock_sync.assert_called_once()
        _, kwargs = mock_sync.call_args
        assert kwargs.get("update_main") is False


class TestSessionStop:
    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.stop_container")
    def test_stop_running(self, mock_stop, mock_validate, runner):
        mock_stop.return_value = True
        result = runner.invoke(main, ["session", "stop", "test-Feb26-1430"])
        assert result.exit_code == 0
        assert "Stopped" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.stop_container")
    def test_stop_not_found(self, mock_stop, mock_validate, runner):
        mock_stop.return_value = False
        result = runner.invoke(main, ["session", "stop", "nonexistent"])
        assert result.exit_code != 0
        assert "No running container" in result.output


class TestSessionLogs:
    @patch("scad.cli.validate_run_id")
    def test_logs_shows_file_content(self, mock_validate, runner, tmp_path):
        scad_dir = tmp_path / ".scad"
        logs_dir = scad_dir / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "test-run.log").write_text("line1\nline2\nline3\n")

        with patch("scad.cli.SCAD_DIR", scad_dir):
            result = runner.invoke(main, ["session", "logs", "test-run"])
        assert result.exit_code == 0
        assert "line1" in result.output
        assert "line3" in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_not_found(self, mock_validate, runner, tmp_path):
        scad_dir = tmp_path / ".scad"
        with patch("scad.cli.SCAD_DIR", scad_dir):
            result = runner.invoke(main, ["session", "logs", "nonexistent"])
        assert result.exit_code != 0
        assert "No log file" in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_respects_line_count(self, mock_validate, runner, tmp_path):
        scad_dir = tmp_path / ".scad"
        logs_dir = scad_dir / "logs"
        logs_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(200)]
        (logs_dir / "big-run.log").write_text("\n".join(lines))

        with patch("scad.cli.SCAD_DIR", scad_dir):
            result = runner.invoke(main, ["session", "logs", "big-run", "-n", "5"])
        assert result.exit_code == 0
        assert "line199" in result.output
        assert "line194" not in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_stream_shows_jsonl(self, mock_validate, runner, tmp_path):
        scad_dir = tmp_path / ".scad"
        logs_dir = scad_dir / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "test-run.stream.jsonl").write_text(
            '{"type":"tool_use","tool":"Edit"}\n'
            '{"type":"tool_result","output":"ok"}\n'
        )

        with patch("scad.cli.SCAD_DIR", scad_dir):
            result = runner.invoke(main, ["session", "logs", "test-run", "--stream"])
        assert result.exit_code == 0
        assert "tool_use" in result.output
        assert "Edit" in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_stream_not_found(self, mock_validate, runner, tmp_path):
        scad_dir = tmp_path / ".scad"
        with patch("scad.cli.SCAD_DIR", scad_dir):
            result = runner.invoke(main, ["session", "logs", "nonexistent", "--stream"])
        assert result.exit_code != 0
        assert "No stream log" in result.output


class TestSessionLogsHumanReadable:
    """Tests for session logs --job human-readable output."""

    @patch("scad.cli.validate_run_id")
    def test_job_logs_show_tool_activity(self, mock_validate, tmp_path):
        """session logs --job shows condensed tool activity."""
        import json
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        records = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/workspace/code/main.py"}}
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/workspace/code/main.py"}}
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/ -v"}}
            ]}},
            {"type": "result", "is_error": False, "result": "All done."},
        ]
        stream_file = logs_dir / "test-run-job-001.stream.jsonl"
        stream_file.write_text("\n".join(json.dumps(r) for r in records))

        runner = CliRunner()
        with patch("scad.cli.SCAD_DIR", tmp_path):
            result = runner.invoke(main, ["session", "logs", "test-run", "--job", "test-run-job-001"])

        assert result.exit_code == 0
        assert "Reading" in result.output
        assert "Editing" in result.output
        assert "pytest" in result.output
        assert "All done" in result.output

    @patch("scad.cli.validate_run_id")
    def test_job_logs_still_running(self, mock_validate, tmp_path):
        """session logs --job with no result record shows 'still running'."""
        import json
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        records = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/workspace/code/main.py"}}
            ]}},
        ]
        stream_file = logs_dir / "test-run-job-001.stream.jsonl"
        stream_file.write_text("\n".join(json.dumps(r) for r in records))

        runner = CliRunner()
        with patch("scad.cli.SCAD_DIR", tmp_path):
            result = runner.invoke(main, ["session", "logs", "test-run", "--job", "test-run-job-001"])

        assert "Reading" in result.output
        assert "still running" in result.output.lower() or "no result" in result.output.lower()


class TestSessionStatus:
    @patch("scad.cli.list_scad_containers")
    @patch("scad.cli.get_recently_crashed")
    def test_status_shows_running(self, mock_crashed, mock_running, runner):
        mock_running.return_value = [{
            "run_id": "test-Feb26-1430",
            "config": "myconfig",
            "branch": "test",
            "started": "2026-02-26T14:30:00Z",
            "container": "running",
            "clones": "yes",
        }]
        mock_crashed.return_value = []
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "test-Feb26-1430" in result.output
        assert "running" in result.output

    @patch("scad.cli.list_scad_containers")
    @patch("scad.cli.get_recently_crashed")
    def test_status_empty(self, mock_crashed, mock_running, runner):
        mock_running.return_value = []
        mock_crashed.return_value = []
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "No running runs" in result.output

    @patch("scad.cli.get_all_sessions")
    def test_status_all_shows_history(self, mock_all, runner):
        mock_all.return_value = [
            {
                "run_id": "test-Feb28-1400",
                "config": "demo",
                "branch": "scad-Feb28-1400",
                "started": "2026-02-28T14:00:00Z",
                "container": "running",
                "clones": "yes",
            },
            {
                "run_id": "old-Feb27-0900",
                "config": "demo",
                "branch": "scad-Feb27-0900",
                "started": "2026-02-27T09:00:00Z",
                "container": "stopped",
                "clones": "yes",
            },
        ]
        result = runner.invoke(main, ["status", "--all"])
        assert result.exit_code == 0
        assert "test-Feb28-1400" in result.output
        assert "old-Feb27-0900" in result.output
        assert "running" in result.output
        assert "stopped" in result.output


class TestScadBuild:
    @patch("scad.cli.ensure_vm_running")
    @patch("scad.cli.build_image")
    @patch("scad.cli.load_config")
    def test_build_shows_step_progress(self, mock_load, mock_build, _ensure_vm, runner):
        """Quiet build shows Step N/M lines."""
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_build.return_value = iter([
            "Step 1/5 : FROM python:3.11-slim",
            "----> abc123",
            "Step 2/5 : RUN apt-get update",
            "----> def456",
        ])

        result = runner.invoke(main, ["build", "test"])
        assert result.exit_code == 0
        assert "Step 1/5" in result.output
        assert "Step 2/5" in result.output
        assert "abc123" not in result.output  # non-Step lines hidden

    @patch("scad.cli.ensure_vm_running")
    @patch("scad.cli.build_image")
    @patch("scad.cli.load_config")
    def test_build_verbose_shows_everything(self, mock_load, mock_build, _ensure_vm, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_build.return_value = iter([
            "Step 1/5 : FROM python:3.11-slim",
            "----> abc123",
        ])

        result = runner.invoke(main, ["build", "test", "-v"])
        assert result.exit_code == 0
        assert "Step 1/5" in result.output
        assert "abc123" in result.output

    @patch("scad.cli.load_config")
    def test_build_config_not_found(self, mock_load, runner):
        mock_load.side_effect = FileNotFoundError("Config 'bad' not found")
        result = runner.invoke(main, ["build", "bad"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestSessionStart:
    def test_start_requires_config(self, runner):
        result = runner.invoke(main, ["session", "start"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "config" in result.output.lower()

    @patch("scad.cli.load_config")
    def test_start_config_not_found(self, mock_load, runner):
        mock_load.side_effect = FileNotFoundError("Config 'bad' not found")
        result = runner.invoke(main, ["session", "start", "bad", "--tag", "test"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @patch("scad.cli.run_agent")
    @patch("scad.cli.resolve_branch")
    @patch("scad.cli.load_config")
    def test_start_dispatches_headless(self, mock_load, mock_resolve, mock_run, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_resolve.return_value = "plan-22"
        mock_run.return_value = "test-plan07-Feb27-1430"

        result = runner.invoke(
            main, ["session", "start", "test", "--tag", "plan07", "--branch", "plan-22", "--prompt", "do stuff"]
        )
        assert result.exit_code == 0
        mock_run.assert_called_once()
        assert mock_run.call_args[1]["branch"] == "plan-22"
        assert mock_run.call_args[1]["tag"] == "plan07"
        assert mock_run.call_args[1]["prompt"] == "do stuff"

    def test_start_requires_tag(self, runner):
        """session start errors without --tag."""
        result = runner.invoke(main, ["session", "start", "test"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "tag" in result.output.lower()

    @patch("scad.cli.run_agent")
    @patch("scad.cli.resolve_branch")
    @patch("scad.cli.load_config")
    def test_start_auto_generates_branch(self, mock_load, mock_resolve, mock_run, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_resolve.return_value = "scad-test-Feb27-1430"
        mock_run.return_value = "test-test-Feb27-1430"

        result = runner.invoke(main, ["session", "start", "test", "--tag", "test"])
        assert result.exit_code == 0
        mock_resolve.assert_called_once_with(mock_config, None, "test")

    @patch("scad.cli.log_event")
    @patch("scad.cli.run_agent")
    @patch("scad.cli.resolve_branch")
    @patch("scad.cli.load_config")
    def test_headless_requires_prompt(self, mock_load, mock_resolve, mock_run, mock_log, runner):
        """--headless without --prompt should error."""
        mock_load.return_value = MagicMock(name="test")
        result = runner.invoke(main, ["session", "start", "test", "--tag", "t1", "--headless"])
        assert result.exit_code != 0

    @patch("scad.cli.log_event")
    @patch("scad.cli.run_agent")
    @patch("scad.cli.resolve_branch")
    @patch("scad.cli.load_config")
    def test_prompt_without_headless_is_interactive(self, mock_load, mock_resolve, mock_run, mock_log, runner):
        """--prompt without --headless passes headless=False."""
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_resolve.return_value = "scad-test-t1-Mar02-1400"
        mock_run.return_value = "test-t1-Mar02-1400"
        runner.invoke(main, ["session", "start", "test", "--tag", "t1", "--prompt", "do stuff"])
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs.get("headless") is False


class TestRunAgentInjectIntegration:
    """Test that run_agent() uses inject_job() when prompt is given."""

    @patch("scad.cli.ensure_vm_running")
    @patch("scad.cli.ensure_gpu_supported")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.run_container")
    @patch("scad.cli.create_clones")
    @patch("scad.cli.image_exists")
    @patch("scad.cli.check_claude_auth")
    @patch("scad.cli.generate_run_id")
    @patch("time.sleep")
    def test_prompt_triggers_inject(self, mock_sleep, mock_gen_id, mock_auth, mock_img,
                                     mock_clones, mock_run_container, mock_inject,
                                     _ensure_gpu, _ensure_vm):
        """run_agent with prompt calls inject_job after container start."""
        from scad.cli import run_agent
        mock_auth.return_value = (True, 10.0)
        mock_gen_id.return_value = "test-t1-Mar02-1400"
        mock_img.return_value = True
        mock_clones.return_value = {"code": Path("/tmp/ws/code")}
        mock_run_container.return_value = "abc123def456"
        mock_inject.return_value = "test-t1-Mar02-1400-job-001"

        config = MagicMock()
        config.name = "test"
        config.workdir_key = "code"
        config.repos = {"code": MagicMock(add_dir=False)}
        config.claude.dangerously_skip_permissions = True
        config.claude.additional_flags = "--verbose"

        run_agent(config, branch="feat", tag="t1", prompt="do stuff", headless=True)

        # run_container should NOT receive prompt or headless
        _, rc_kwargs = mock_run_container.call_args
        assert "prompt" not in rc_kwargs
        assert "headless" not in rc_kwargs

        # inject_job should be called with correct args
        mock_inject.assert_called_once()
        _, ij_kwargs = mock_inject.call_args
        assert ij_kwargs["run_id"] == "test-t1-Mar02-1400"
        assert ij_kwargs["prompt"] == "do stuff"
        assert ij_kwargs["headless"] is True
        assert ij_kwargs["workdir_key"] == "code"
        assert ij_kwargs["dangerously_skip_permissions"] is True
        assert ij_kwargs["additional_flags"] == "--verbose"

    @patch("scad.cli.ensure_vm_running")
    @patch("scad.cli.ensure_gpu_supported")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.run_container")
    @patch("scad.cli.create_clones")
    @patch("scad.cli.image_exists")
    @patch("scad.cli.check_claude_auth")
    @patch("scad.cli.generate_run_id")
    def test_no_prompt_skips_inject(self, mock_gen_id, mock_auth, mock_img,
                                     mock_clones, mock_run_container, mock_inject,
                                     _ensure_gpu, _ensure_vm):
        """run_agent without prompt does not call inject_job."""
        from scad.cli import run_agent
        mock_auth.return_value = (True, 10.0)
        mock_gen_id.return_value = "test-t1-Mar02-1400"
        mock_img.return_value = True
        mock_clones.return_value = {"code": Path("/tmp/ws/code")}
        mock_run_container.return_value = "abc123def456"

        config = MagicMock()
        config.name = "test"
        config.workdir_key = "code"
        config.repos = {"code": MagicMock(add_dir=False)}

        run_agent(config, branch="feat", tag="t1")

        mock_inject.assert_not_called()

    @patch("scad.cli.ensure_vm_running")
    @patch("scad.cli.ensure_gpu_supported")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.run_container")
    @patch("scad.cli.create_clones")
    @patch("scad.cli.image_exists")
    @patch("scad.cli.check_claude_auth")
    @patch("scad.cli.generate_run_id")
    @patch("time.sleep")
    def test_prompt_builds_add_dirs(self, mock_sleep, mock_gen_id, mock_auth, mock_img,
                                     mock_clones, mock_run_container, mock_inject,
                                     _ensure_gpu, _ensure_vm):
        """run_agent passes add_dirs from repos with add_dir=True."""
        from scad.cli import run_agent
        mock_auth.return_value = (True, 10.0)
        mock_gen_id.return_value = "test-t1-Mar02-1400"
        mock_img.return_value = True
        mock_clones.return_value = {"code": Path("/tmp/ws/code"), "docs": Path("/tmp/ws/docs")}
        mock_run_container.return_value = "abc123def456"
        mock_inject.return_value = "test-t1-Mar02-1400-job-001"

        config = MagicMock()
        config.name = "test"
        config.workdir_key = "code"
        config.repos = {
            "code": MagicMock(add_dir=False),
            "docs": MagicMock(add_dir=True),
        }
        config.claude.dangerously_skip_permissions = False
        config.claude.additional_flags = None

        run_agent(config, branch="feat", tag="t1", prompt="work on docs")

        _, ij_kwargs = mock_inject.call_args
        assert ij_kwargs["add_dirs"] == ["docs"]

    @patch("scad.cli.ensure_vm_running")
    @patch("scad.cli.ensure_gpu_supported")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.run_container")
    @patch("scad.cli.create_clones")
    @patch("scad.cli.image_exists")
    @patch("scad.cli.check_claude_auth")
    @patch("scad.cli.generate_run_id")
    @patch("time.sleep")
    def test_prompt_sleeps_before_inject(self, mock_sleep, mock_gen_id, mock_auth, mock_img,
                                          mock_clones, mock_run_container, mock_inject,
                                          _ensure_gpu, _ensure_vm):
        """run_agent sleeps briefly before injecting to let entrypoint set up."""
        from scad.cli import run_agent
        mock_auth.return_value = (True, 10.0)
        mock_gen_id.return_value = "test-t1-Mar02-1400"
        mock_img.return_value = True
        mock_clones.return_value = {"code": Path("/tmp/ws/code")}
        mock_run_container.return_value = "abc123def456"
        mock_inject.return_value = "test-t1-Mar02-1400-job-001"

        config = MagicMock()
        config.name = "test"
        config.workdir_key = "code"
        config.repos = {"code": MagicMock(add_dir=False)}
        config.claude.dangerously_skip_permissions = False
        config.claude.additional_flags = None

        run_agent(config, branch="feat", tag="t1", prompt="hello")

        mock_sleep.assert_called_once_with(1)


class TestSessionAttach:
    @patch("scad.cli.validate_run_id")
    @patch("scad.cli._subprocess.run")
    @patch("scad.cli.get_docker_client")
    def test_attach_runs_docker_exec(self, mock_docker, mock_subprocess, mock_validate, runner):
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = MagicMock(exit_code=0)
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = runner.invoke(main, ["session", "attach", "test-Feb27-1430"])
        mock_subprocess.assert_called_once()
        call_args = mock_subprocess.call_args[0][0]
        assert "docker" in call_args
        assert "exec" in call_args
        assert "tmux" in call_args

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.get_docker_client")
    def test_attach_not_found(self, mock_docker, mock_validate, runner):
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["session", "attach", "nonexistent"])
        assert result.exit_code != 0
        assert "No container" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.get_docker_client")
    def test_attach_not_running(self, mock_docker, mock_validate, runner):
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["session", "attach", "stopped-run"])
        assert result.exit_code != 0
        assert "not running" in result.output.lower()

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.get_docker_client")
    def test_attach_headless_no_tmux(self, mock_docker, mock_validate, runner):
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = MagicMock(exit_code=1)  # no tmux session
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["session", "attach", "headless-run"])
        assert result.exit_code != 0
        assert "headless" in result.output.lower()


class TestSessionClean:
    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.clean_run")
    def test_clean_removes_run(self, mock_clean, mock_validate, runner, tmp_path):
        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["session", "clean", "test-run"])

        assert result.exit_code == 0
        assert "Cleaned" in result.output
        mock_clean.assert_called_once_with("test-run")

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.clean_run")
    def test_clean_nonexistent_is_ok(self, mock_clean, mock_validate, runner, tmp_path):
        # clean_run is a no-op if nothing exists, so clean always succeeds
        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["session", "clean", "nonexistent"])

        assert result.exit_code == 0
        mock_clean.assert_called_once_with("nonexistent")


class TestConfigList:
    @patch("scad.cli.get_image_info")
    @patch("scad.cli.list_configs")
    def test_config_list_shows_table(self, mock_list, mock_info, runner):
        mock_list.return_value = ["alpha", "beta"]
        mock_info.side_effect = [
            {"tag": "scad-alpha", "created": "2026-02-26T10:00:00Z"},
            None,
        ]
        result = runner.invoke(main, ["config", "list"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "never" in result.output

    @patch("scad.cli.list_configs")
    def test_config_list_empty(self, mock_list, runner):
        mock_list.return_value = []
        result = runner.invoke(main, ["config", "list"])
        assert result.exit_code == 0
        assert "No configs" in result.output


class TestScadConfig:
    def test_config_view(self, runner, tmp_path, monkeypatch):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "demo.yml").write_text("name: demo\nrepos:\n  code:\n    path: /tmp\n    workdir: true\n")
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)
        result = runner.invoke(main, ["config", "view", "demo"])
        assert result.exit_code == 0
        assert "name: demo" in result.output

    def test_config_view_not_found(self, runner, tmp_path, monkeypatch):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)
        result = runner.invoke(main, ["config", "view", "nope"])
        assert result.exit_code != 0

    @patch("scad.cli.subprocess.run")
    def test_config_edit_calls_editor(self, mock_run, runner, tmp_path, monkeypatch):
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "demo.yml").write_text("name: demo\n")
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)
        monkeypatch.setenv("EDITOR", "nano")
        result = runner.invoke(main, ["config", "edit", "demo"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "nano" in call_args


class TestConfigAdd:
    def test_add_creates_symlink(self, runner, tmp_path, monkeypatch):
        """config add creates a symlink in configs dir."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        ext_config = tmp_path / "project" / "scad.yml"
        ext_config.parent.mkdir()
        ext_config.write_text(
            "name: myproject\nrepos:\n  code:\n    path: /tmp/code\n    workdir: true\n"
            "python:\n  version: '3.11'\nclaude:\n  dangerously_skip_permissions: true\n"
        )

        result = runner.invoke(main, ["config", "add", str(ext_config)])
        assert result.exit_code == 0
        assert "Registered" in result.output

        link = config_dir / "myproject.yml"
        assert link.is_symlink()
        assert link.resolve() == ext_config.resolve()

    def test_add_rejects_duplicate_name(self, runner, tmp_path, monkeypatch):
        """config add errors if a config with that name already exists."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "taken.yml").write_text("name: taken\n")
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        ext_config = tmp_path / "other" / "scad.yml"
        ext_config.parent.mkdir()
        ext_config.write_text(
            "name: taken\nrepos:\n  code:\n    path: /tmp/code\n    workdir: true\n"
            "python:\n  version: '3.11'\nclaude:\n  dangerously_skip_permissions: true\n"
        )

        result = runner.invoke(main, ["config", "add", str(ext_config)])
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_add_same_target_is_noop(self, runner, tmp_path, monkeypatch):
        """config add with same target is idempotent."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        ext_config = tmp_path / "project" / "scad.yml"
        ext_config.parent.mkdir()
        ext_config.write_text(
            "name: myproject\nrepos:\n  code:\n    path: /tmp/code\n    workdir: true\n"
            "python:\n  version: '3.11'\nclaude:\n  dangerously_skip_permissions: true\n"
        )

        # First add
        runner.invoke(main, ["config", "add", str(ext_config)])
        # Second add — same target, should be fine
        result = runner.invoke(main, ["config", "add", str(ext_config)])
        assert result.exit_code == 0
        assert "Already registered" in result.output

    def test_add_validates_yaml(self, runner, tmp_path, monkeypatch):
        """config add rejects invalid config YAML."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        bad_config = tmp_path / "bad.yml"
        bad_config.write_text("not: valid: scad: config\n")

        result = runner.invoke(main, ["config", "add", str(bad_config)])
        assert result.exit_code != 0


class TestConfigRemove:
    def test_remove_deletes_symlink(self, runner, tmp_path, monkeypatch):
        """config remove removes the symlink."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        ext_config = tmp_path / "project" / "scad.yml"
        ext_config.parent.mkdir()
        ext_config.write_text("name: myproject\n")

        link = config_dir / "myproject.yml"
        link.symlink_to(ext_config.resolve())

        result = runner.invoke(main, ["config", "remove", "myproject"])
        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not link.exists()
        # Original file still exists
        assert ext_config.exists()

    def test_remove_nonexistent(self, runner, tmp_path, monkeypatch):
        """config remove errors for unknown config."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        result = runner.invoke(main, ["config", "remove", "nope"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestShellCompletion:
    def test_run_id_completion_from_runs(self, tmp_path):
        scad_dir = tmp_path / ".scad"
        runs_dir = scad_dir / "runs"
        runs_dir.mkdir(parents=True)
        (runs_dir / "demo-Feb28-1400").mkdir()
        (runs_dir / "scad-Feb28-0900").mkdir()

        with patch("scad.cli.SCAD_DIR", scad_dir):
            results = _complete_run_ids(None, None, "demo")
        completions = [c.value if hasattr(c, "value") else c for c in results]
        assert "demo-Feb28-1400" in completions
        assert "scad-Feb28-0900" not in completions

    def test_run_id_completion_empty(self, tmp_path):
        scad_dir = tmp_path / ".scad"
        with patch("scad.cli.SCAD_DIR", scad_dir):
            results = _complete_run_ids(None, None, "")
        assert results == []

    def test_config_name_completion(self, tmp_path):
        with patch("scad.cli.list_configs", return_value=["alpha", "beta"]):
            results = _complete_config_names(None, None, "al")
        completions = [c.value if hasattr(c, "value") else c for c in results]
        assert "alpha" in completions
        assert "beta" not in completions


class TestSessionInfo:
    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.get_session_info")
    def test_info_shows_dashboard(self, mock_info, mock_validate, runner):
        mock_info.return_value = {
            "run_id": "demo-Feb28-1400",
            "config": "demo",
            "branch": "scad-Feb28-1400",
            "container": "running",
            "clones_path": "~/.scad/runs/demo-Feb28-1400/worktrees/",
            "clones": ["demo-code", "demo-docs"],
            "claude_sessions": [{"id": "abc12345", "modified": "2026-02-28 14:00"}],
            "events": [
                "2026-02-28T14:00 start config=demo branch=scad-Feb28-1400",
                "2026-02-28T14:30 fetch demo-code → /src",
            ],
        }
        result = runner.invoke(main, ["session", "info", "demo-Feb28-1400"])
        assert result.exit_code == 0
        assert "demo-Feb28-1400" in result.output
        assert "demo" in result.output
        assert "running" in result.output
        assert "abc12345" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.get_session_usage")
    @patch("scad.cli.get_session_info")
    def test_info_shows_cost_when_nonzero(self, mock_info, mock_usage, mock_validate, runner):
        mock_info.return_value = {
            "run_id": "demo-Feb28-1400",
            "config": "demo",
            "branch": "scad-Feb28-1400",
            "container": "running",
            "clones_path": None,
            "clones": [],
            "claude_sessions": [],
            "events": [],
        }
        mock_usage.return_value = {
            "total_cost": 2.34,
            "total_input_tokens": 12450,
            "total_output_tokens": 8200,
            "total_turns": 47,
        }
        result = runner.invoke(main, ["session", "info", "demo-Feb28-1400"])
        assert result.exit_code == 0
        assert "$2.34" in result.output
        assert "12,450 input" in result.output
        assert "Usage:" in result.output

    @patch("scad.cli.get_session_info")
    def test_info_not_found(self, mock_info, runner):
        mock_info.side_effect = FileNotFoundError("No session found for bad-id")
        result = runner.invoke(main, ["session", "info", "bad-id"])
        assert result.exit_code != 0
        assert "No session found" in result.output


class TestEventLogging:
    @patch("scad.cli.log_event")
    @patch("scad.cli.run_agent")
    @patch("scad.cli.resolve_branch")
    @patch("scad.cli.load_config")
    def test_start_logs_event(self, mock_load, mock_resolve, mock_run, mock_log, runner):
        """session start logs a start event."""
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_resolve.return_value = "scad-plan07-Feb28-1400"
        mock_run.return_value = "test-plan07-Feb28-1400"

        runner.invoke(main, ["session", "start", "test", "--tag", "plan07"])
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == "test-plan07-Feb28-1400"  # run_id
        assert call_args[0][1] == "start"  # verb
        assert "config=test" in call_args[0][2]
        assert "branch=scad-plan07-Feb28-1400" in call_args[0][2]

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.log_event")
    @patch("scad.cli.stop_container")
    def test_stop_logs_event(self, mock_stop, mock_log, mock_validate, runner):
        """session stop logs a stop event."""
        mock_stop.return_value = True
        runner.invoke(main, ["session", "stop", "test-run"])
        mock_log.assert_called_once_with("test-run", "stop")

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.log_event")
    @patch("scad.cli._subprocess.run")
    @patch("scad.cli.get_docker_client")
    def test_attach_logs_event(self, mock_docker, mock_subprocess, mock_log, mock_validate, runner):
        """session attach logs an attach event."""
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = MagicMock(exit_code=0)
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client
        mock_subprocess.return_value = MagicMock(returncode=0)

        runner.invoke(main, ["session", "attach", "test-run"])
        mock_log.assert_called_once_with("test-run", "attach")


class TestSessionRefresh:
    """Tests for session refresh (moved from code refresh)."""

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.refresh_credentials")
    def test_refresh_under_session(self, mock_refresh, mock_validate, runner):
        """session refresh pushes fresh credentials."""
        mock_refresh.return_value = 7.5
        result = runner.invoke(main, ["session", "refresh", "test-run"])
        assert result.exit_code == 0
        assert "Credentials refreshed" in result.output
        mock_refresh.assert_called_once_with("test-run")

    def test_code_refresh_no_longer_exists(self, runner):
        """code refresh should no longer be a valid command."""
        result = runner.invoke(main, ["code", "refresh", "test-run"])
        assert result.exit_code != 0

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.refresh_credentials")
    def test_refresh_shows_time_remaining(self, mock_refresh, mock_validate, runner):
        mock_refresh.return_value = 4.5
        result = runner.invoke(main, ["session", "refresh", "test-run"])
        assert result.exit_code == 0
        assert "4h 30m" in result.output or "refreshed" in result.output.lower()

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.refresh_credentials")
    def test_refresh_expired(self, mock_refresh, mock_validate, runner):
        mock_refresh.side_effect = click.ClickException("Credentials expired")
        result = runner.invoke(main, ["session", "refresh", "test-run"])
        assert result.exit_code != 0
        assert "expired" in result.output.lower()


class TestConfigNew:
    def test_new_creates_template(self, runner, tmp_path, monkeypatch):
        """config new creates a YAML template file."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        result = runner.invoke(main, ["config", "new", "demo"])
        assert result.exit_code == 0
        config_file = config_dir / "demo.yml"
        assert config_file.exists()
        content = config_file.read_text()
        assert "name: demo" in content
        assert "workdir: true" in content
        assert str(config_file) in result.output

    def test_new_rejects_existing(self, runner, tmp_path, monkeypatch):
        """config new errors if config already exists."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        (config_dir / "demo.yml").write_text("name: demo\n")
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)

        result = runner.invoke(main, ["config", "new", "demo"])
        assert result.exit_code != 0
        assert "already exists" in result.output

    @patch("scad.cli.subprocess.run")
    def test_new_edit_flag(self, mock_run, runner, tmp_path, monkeypatch):
        """config new --edit opens in $EDITOR."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()
        monkeypatch.setattr("scad.config.CONFIG_DIR", config_dir)
        monkeypatch.setattr("scad.cli.CONFIG_DIR", config_dir)
        monkeypatch.setenv("EDITOR", "nano")

        result = runner.invoke(main, ["config", "new", "demo", "--edit"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        assert "nano" in mock_run.call_args[0][0]


class TestConfigInfo:
    def test_config_info_shows_repos(self, runner, tmp_path, monkeypatch):
        """config info shows repo path mappings."""
        monkeypatch.setattr("scad.config.CONFIG_DIR", tmp_path)
        config_file = tmp_path / "demo.yml"
        config_file.write_text("""
name: demo
repos:
  code:
    path: /home/user/code/demo
    workdir: true
  docs:
    path: /home/user/docs
    add_dir: true
python:
  version: "3.11"
claude:
  dangerously_skip_permissions: true
""")
        result = runner.invoke(main, ["config", "info", "demo"])
        assert result.exit_code == 0
        assert "demo" in result.output
        assert "/workspace/code" in result.output
        assert "/workspace/docs" in result.output
        assert "workdir" in result.output.lower()
        assert "add-dir" in result.output.lower()

    def test_config_info_shows_python(self, runner, tmp_path, monkeypatch):
        """config info shows Python/venv info."""
        monkeypatch.setattr("scad.config.CONFIG_DIR", tmp_path)
        config_file = tmp_path / "demo.yml"
        config_file.write_text("""
name: demo
repos:
  code:
    path: /home/user/code/demo
    workdir: true
python:
  version: "3.11"
  requirements: requirements.txt
""")
        result = runner.invoke(main, ["config", "info", "demo"])
        assert result.exit_code == 0
        assert "3.11" in result.output
        assert "/opt/venv" in result.output

    def test_config_info_not_found(self, runner, tmp_path, monkeypatch):
        """config info with invalid name shows error."""
        monkeypatch.setattr("scad.config.CONFIG_DIR", tmp_path)
        result = runner.invoke(main, ["config", "info", "nonexistent"])
        assert result.exit_code != 0


class TestProjectStatus:
    @patch("scad.cli.get_project_status")
    def test_shows_project_overview(self, mock_status, runner):
        mock_status.return_value = {
            "config": "demo",
            "total_sessions": 2,
            "running": 1,
            "stopped": 1,
            "cleaned": 0,
            "last_active": "2026-03-01T14:00",
            "total_cost": 3.84,
            "sessions": [
                {"run_id": "demo-plan07-Mar01-1400", "branch": "scad-plan07-Mar01-1400",
                 "started": "2026-03-01T14:00", "container": "running", "cost": 2.34,
                 "usage": None},
                {"run_id": "demo-bugfix-Mar01-0900", "branch": "scad-bugfix-Mar01-0900",
                 "started": "2026-03-01T09:00", "container": "stopped", "cost": 1.50,
                 "usage": None},
            ],
        }
        result = runner.invoke(main, ["status", "demo", "--cost"])
        assert result.exit_code == 0
        assert "demo" in result.output
        assert "2 " in result.output  # total sessions
        assert "$3.84" in result.output

    @patch("scad.cli.get_project_status")
    def test_no_cost_by_default(self, mock_status, runner):
        mock_status.return_value = {
            "config": "demo",
            "total_sessions": 1,
            "running": 0,
            "stopped": 1,
            "cleaned": 0,
            "last_active": "2026-03-01T14:00",
            "total_cost": 0,
            "sessions": [
                {"run_id": "demo-test", "config": "demo", "branch": "b",
                 "started": "2026-03-01T14:00", "container": "stopped", "cost": 0,
                 "usage": None},
            ],
        }
        result = runner.invoke(main, ["status", "demo"])
        assert result.exit_code == 0
        assert "$" not in result.output  # no cost column without --cost flag


class TestRunIdValidation:
    """Commands with run-id arguments validate before acting."""

    def test_clean_invalid_run_id(self, runner, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", Path("/nonexistent"))
        monkeypatch.setattr("scad.container._container_exists", lambda rid: False)
        result = runner.invoke(main, ["session", "clean", "fake-run-id"])
        assert result.exit_code != 0
        assert "No session found" in result.output

    def test_stop_invalid_run_id(self, runner, monkeypatch):
        monkeypatch.setattr("scad.container.RUNS_DIR", Path("/nonexistent"))
        monkeypatch.setattr("scad.container._container_exists", lambda rid: False)
        result = runner.invoke(main, ["session", "stop", "fake-run-id"])
        assert result.exit_code != 0
        assert "No session found" in result.output


class TestBulkOperations:
    """--all and --config flags for stop and clean."""

    def test_clean_all(self, runner, monkeypatch):
        sessions = [
            {"run_id": "demo-a-Mar01-1400", "config": "demo", "container": "stopped"},
            {"run_id": "demo-b-Mar01-1500", "config": "demo", "container": "stopped"},
        ]
        monkeypatch.setattr("scad.cli.get_all_sessions", lambda: sessions)
        cleaned = []
        monkeypatch.setattr("scad.cli.clean_run", lambda rid: cleaned.append(rid))
        monkeypatch.setattr("scad.cli.validate_run_id", lambda rid: None)

        result = runner.invoke(main, ["session", "clean", "--all", "--yes"])
        assert result.exit_code == 0
        assert len(cleaned) == 2

    def test_clean_by_config(self, runner, monkeypatch):
        sessions = [
            {"run_id": "demo-a-Mar01-1400", "config": "demo", "container": "stopped"},
            {"run_id": "scad-b-Mar01-1500", "config": "scad", "container": "stopped"},
        ]
        monkeypatch.setattr("scad.cli.get_all_sessions", lambda: sessions)
        cleaned = []
        monkeypatch.setattr("scad.cli.clean_run", lambda rid: cleaned.append(rid))
        monkeypatch.setattr("scad.cli.validate_run_id", lambda rid: None)

        result = runner.invoke(main, ["session", "clean", "--config", "demo", "--yes"])
        assert result.exit_code == 0
        assert cleaned == ["demo-a-Mar01-1400"]

    def test_clean_all_skips_running_without_force(self, runner, monkeypatch):
        sessions = [
            {"run_id": "demo-a-Mar01-1400", "config": "demo", "container": "running"},
        ]
        monkeypatch.setattr("scad.cli.get_all_sessions", lambda: sessions)
        cleaned = []
        monkeypatch.setattr("scad.cli.clean_run", lambda rid: cleaned.append(rid))

        result = runner.invoke(main, ["session", "clean", "--all", "--yes"])
        assert result.exit_code == 0
        assert len(cleaned) == 0  # skipped running

    def test_stop_all(self, runner, monkeypatch):
        sessions = [
            {"run_id": "demo-a-Mar01-1400", "config": "demo", "container": "running"},
            {"run_id": "demo-b-Mar01-1500", "config": "demo", "container": "running"},
        ]
        monkeypatch.setattr("scad.cli.get_all_sessions", lambda: sessions)
        stopped = []
        monkeypatch.setattr("scad.cli.stop_container", lambda rid: stopped.append(rid) or True)
        monkeypatch.setattr("scad.cli.validate_run_id", lambda rid: None)
        monkeypatch.setattr("scad.cli.log_event", lambda *a, **kw: None)

        result = runner.invoke(main, ["session", "stop", "--all", "--yes"])
        assert result.exit_code == 0
        assert len(stopped) == 2

    def test_requires_confirmation_without_yes(self, runner, monkeypatch):
        sessions = [{"run_id": "demo-a", "config": "demo", "container": "stopped"}]
        monkeypatch.setattr("scad.cli.get_all_sessions", lambda: sessions)
        monkeypatch.setattr("scad.cli.clean_run", lambda rid: None)

        result = runner.invoke(main, ["session", "clean", "--all"], input="n\n")
        assert result.exit_code == 0 or "Aborted" in result.output

    def test_run_id_and_all_mutually_exclusive(self, runner):
        result = runner.invoke(main, ["session", "clean", "some-id", "--all"])
        assert result.exit_code != 0


class TestGcCommand:
    """scad gc command."""

    def test_gc_dry_run(self, runner, monkeypatch):
        monkeypatch.setattr("scad.cli.gc", lambda force: {"orphaned_containers": [], "dead_run_dirs": [], "unused_images": []})
        result = runner.invoke(main, ["gc"])
        assert result.exit_code == 0
        assert "dry run" in result.output.lower() or "nothing" in result.output.lower()

    def test_gc_force(self, runner, monkeypatch):
        monkeypatch.setattr("scad.cli.gc", lambda force: {"orphaned_containers": [], "dead_run_dirs": [], "unused_images": []})
        result = runner.invoke(main, ["gc", "--force"])
        assert result.exit_code == 0


class TestUsageDisplay:
    """session info shows tokens, project status has --cost opt-in."""

    def test_session_info_shows_tokens(self, runner, monkeypatch):
        info = {
            "run_id": "demo-test-Mar01-1400", "config": "demo",
            "branch": "scad-demo-test-Mar01-1400", "container": "running",
            "events": [], "clones": [], "claude_sessions": [],
            "clones_path": None,
        }
        usage = {"total_input_tokens": 5000, "total_output_tokens": 3000,
                 "total_turns": 10, "total_cost": 0}
        monkeypatch.setattr("scad.cli.get_session_info", lambda rid: info)
        monkeypatch.setattr("scad.cli.get_session_usage", lambda rid: usage)
        monkeypatch.setattr("scad.cli.validate_run_id", lambda rid: None)

        result = runner.invoke(main, ["session", "info", "demo-test-Mar01-1400"])
        assert "5,000 input" in result.output or "5000 input" in result.output
        assert "$" not in result.output  # no cost when 0

    def test_project_status_no_cost_by_default(self, runner, monkeypatch):
        status = {
            "config": "demo", "total_sessions": 1, "running": 0,
            "stopped": 1, "cleaned": 0, "last_active": "2026-03-01T14:00",
            "total_cost": 0, "sessions": [
                {"run_id": "demo-test", "config": "demo", "branch": "b",
                 "started": "2026-03-01T14:00", "container": "stopped", "cost": 0,
                 "usage": None}
            ],
        }
        monkeypatch.setattr("scad.cli.get_project_status", lambda name, **kw: status)

        result = runner.invoke(main, ["status", "demo"])
        assert result.exit_code == 0


class TestCrashDetection:
    """Tests for crash detection in session status and start."""

    @patch("scad.cli.list_scad_containers")
    @patch("scad.cli.get_recently_crashed")
    def test_status_shows_crashed(self, mock_crashed, mock_running):
        """scad status shows recently crashed sessions."""
        mock_running.return_value = []
        mock_crashed.return_value = [
            {"run_id": "demo-test-Mar03-1200", "exit_code": 1, "finished": "2m ago"}
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["status"])
        assert "crashed" in result.output.lower() or "exit" in result.output.lower()


class TestTopLevelStatus:
    """Tests for top-level scad status command."""

    @patch("scad.cli.list_scad_containers")
    @patch("scad.cli.get_recently_crashed")
    def test_status_no_args_lists_sessions(self, mock_crashed, mock_containers, runner):
        """scad status with no args lists running sessions."""
        mock_containers.return_value = [
            {"run_id": "demo-test-Mar03-1200", "config": "demo",
             "branch": "scad-demo-test-Mar03-1200", "started": "2026-03-03T12:00:00+00:00"},
        ]
        mock_crashed.return_value = []
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "demo-test-Mar03-1200" in result.output

    @patch("scad.cli.list_scad_containers")
    @patch("scad.cli.get_recently_crashed")
    def test_status_no_args_all_flag(self, mock_crashed, mock_containers, runner):
        """scad status --all shows full history."""
        mock_containers.return_value = []
        mock_crashed.return_value = []
        with patch("scad.cli.get_all_sessions") as mock_all:
            mock_all.return_value = []
            result = runner.invoke(main, ["status", "--all"])
        assert result.exit_code == 0

    @patch("scad.cli.get_project_status")
    def test_status_with_config_arg_shows_project(self, mock_status, runner):
        """scad status <config> shows project overview."""
        mock_status.return_value = {
            "config": "demo",
            "total_sessions": 1,
            "running": 1, "stopped": 0, "cleaned": 0,
            "last_active": "2026-03-03T12:00",
            "total_cost": 0,
            "sessions": [
                {"run_id": "demo-test", "branch": "b",
                 "started": "2026-03-03T12:00", "container": "running",
                 "cost": 0, "usage": None},
            ],
        }
        result = runner.invoke(main, ["status", "demo"])
        assert result.exit_code == 0
        assert "demo" in result.output
        assert "Sessions:" in result.output

    @patch("scad.cli.get_project_status")
    def test_status_with_config_cost_flag(self, mock_status, runner):
        """scad status <config> --cost shows cost data."""
        mock_status.return_value = {
            "config": "demo",
            "total_sessions": 1,
            "running": 0, "stopped": 1, "cleaned": 0,
            "last_active": "2026-03-03T12:00",
            "total_cost": 3.84,
            "sessions": [
                {"run_id": "demo-test", "branch": "b",
                 "started": "2026-03-03T12:00", "container": "stopped",
                 "cost": 3.84, "usage": None},
            ],
        }
        result = runner.invoke(main, ["status", "demo", "--cost"])
        assert result.exit_code == 0
        assert "$3.84" in result.output


class TestVMGroup:
    @patch("scad.cli.vm_start")
    @patch("scad.cli.is_macos", return_value=True)
    def test_start_calls_vm_start(self, _mac, mock_start, runner):
        result = runner.invoke(main, ["vm", "start"])
        assert result.exit_code == 0
        mock_start.assert_called_once_with()

    @patch("scad.cli.vm_stop")
    @patch("scad.cli.is_macos", return_value=True)
    def test_stop_calls_vm_stop(self, _mac, mock_stop, runner):
        result = runner.invoke(main, ["vm", "stop"])
        assert result.exit_code == 0
        mock_stop.assert_called_once_with()

    @patch("scad.cli.vm_delete")
    @patch("scad.cli.is_macos", return_value=True)
    def test_delete_requires_confirmation(self, _mac, mock_delete, runner):
        result = runner.invoke(main, ["vm", "delete"], input="n\n")
        mock_delete.assert_not_called()
        assert result.exit_code != 0

    @patch("scad.cli.vm_delete")
    @patch("scad.cli.is_macos", return_value=True)
    def test_delete_yes_skips_prompt(self, _mac, mock_delete, runner):
        result = runner.invoke(main, ["vm", "delete", "--yes"])
        assert result.exit_code == 0
        mock_delete.assert_called_once_with()

    @patch("scad.cli.vm_state", return_value="running")
    @patch("scad.cli.is_macos", return_value=True)
    def test_status_reports_state(self, _mac, _state, runner):
        result = runner.invoke(main, ["vm", "status"])
        assert result.exit_code == 0
        assert "running" in result.output

    @patch("scad.cli.get_docker_client")
    @patch("scad.cli.is_macos", return_value=False)
    def test_status_on_linux_reports_native_docker(self, _mac, _client, runner):
        result = runner.invoke(main, ["vm", "status"])
        assert result.exit_code == 0
        assert "native Docker" in result.output

    @patch("scad.vm.is_macos", return_value=False)
    def test_start_on_linux_errors(self, _mac, runner):
        result = runner.invoke(main, ["vm", "start"])
        assert result.exit_code == 2
        assert "macOS-only" in result.output

    @patch("scad.cli.vm_info")
    @patch("scad.cli.is_macos", return_value=True)
    def test_info_prints_mounts(self, _mac, mock_info, runner):
        mock_info.return_value = {
            "profile": "scad", "state": "running",
            "socket": "/Users/t/.colima/scad/docker.sock",
            "cpu": 2, "memory_gib": 4, "disk_gib": 60,
            "vm_type": "vz", "mount_type": "virtiofs",
            "mounts": ["/Volumes/data"],
        }
        result = runner.invoke(main, ["vm", "info"])
        assert result.exit_code == 0
        assert "/Volumes/data" in result.output
        assert "virtiofs" in result.output


class TestLazyVMStart:
    @patch("scad.cli.prune_old_images")
    @patch("scad.cli.get_docker_client")
    @patch("scad.cli.build_image", return_value=iter([]))
    @patch("scad.cli.load_config")
    @patch("scad.cli.ensure_vm_running")
    def test_build_ensures_vm(self, mock_ensure, mock_load, _b, _c, _p, runner):
        from scad.config import ScadConfig
        mock_load.return_value = ScadConfig(
            name="t", repos={"code": {"path": "/tmp/x", "workdir": True}}
        )
        runner.invoke(main, ["build", "t"])
        mock_ensure.assert_called_once_with()

    @patch("scad.cli.run_container", return_value="cid123456789")
    @patch("scad.cli.create_clones", return_value={})
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.check_claude_auth", return_value=(True, 10.0))
    @patch("scad.cli.ensure_vm_running")
    @patch("scad.cli.reconcile_vm_mounts", return_value=False)
    @patch("scad.cli.ensure_gpu_supported")
    def test_run_agent_guards_then_ensures(self, mock_gpu, mock_reconcile, mock_ensure, *_rest):
        from scad.cli import run_agent
        from scad.config import ScadConfig

        # Attach both mocks to a shared manager so we can assert relative
        # call order (a regression swapping the two calls should fail this).
        manager = Mock()
        manager.attach_mock(mock_gpu, "ensure_gpu_supported")
        manager.attach_mock(mock_ensure, "ensure_vm_running")

        config = ScadConfig(
            name="t", repos={"code": {"path": "/tmp/x", "workdir": True}}
        )
        run_agent(config, branch="b", tag="tg")
        mock_gpu.assert_called_once_with(config)
        mock_ensure.assert_called_once_with()
        mock_reconcile.assert_called_once_with(config)

        expected_order = [call.ensure_gpu_supported(config), call.ensure_vm_running()]
        assert manager.mock_calls == expected_order


class TestCodeAddVMVisibility:
    @patch("scad.cli.workspace_name_taken", return_value=False)
    @patch("scad.cli.workspace_add")
    @patch("scad.cli.path_visible_in_vm", return_value=True)
    @patch("scad.cli.validate_run_id")
    def test_visible_path_adds_silently(self, _v, _vis, mock_add, _taken, runner):
        result = runner.invoke(
            main, ["code", "add", "run-1", "--path", "/Users/t/x", "--name", "x"]
        )
        assert result.exit_code == 0
        assert "restart" not in result.output.lower()
        mock_add.assert_called_once()

    @patch("scad.cli.workspace_name_taken", return_value=False)
    @patch("scad.cli.workspace_add")
    @patch("scad.cli.path_visible_in_vm", return_value=False)
    @patch("scad.cli.validate_run_id")
    def test_invisible_path_warns_and_skips_by_default(
        self, _v, _vis, mock_add, _taken, runner
    ):
        result = runner.invoke(
            main, ["code", "add", "run-1", "--path", "/Volumes/d", "--name", "d"],
            input="n\n",
        )
        assert result.exit_code != 0
        assert "not visible" in result.output.lower()
        mock_add.assert_not_called()

    @patch("scad.cli.get_docker_client")
    @patch("scad.cli.vm_start")
    @patch("scad.cli.vm_stop")
    @patch("scad.cli.vm_state", return_value="running")
    @patch("scad.cli.read_vm_mounts", return_value=["/srv/other"])
    @patch("scad.cli.mount_root", return_value=Path("/Volumes/d"))
    @patch("scad.cli.workspace_add")
    @patch("scad.cli.path_visible_in_vm", return_value=False)
    @patch("scad.cli.workspace_name_taken", return_value=False)
    @patch("scad.cli.validate_run_id")
    def test_restart_vm_flag_adds_mount_then_adds_path(
        self, _v, _taken, _vis, mock_add, _root, _read, _state,
        mock_stop, mock_start, mock_client, runner
    ):
        # read_vm_mounts is seeded with a pre-existing, unrelated mount so this
        # test can tell a preserved union from a regressed "just the new path"
        # set — Colima replaces its mount list wholesale on start, so dropping
        # the union would silently unmount every other session's paths.
        result = runner.invoke(
            main,
            ["code", "add", "run-1", "--path", "/Volumes/d", "--name", "d",
             "--restart-vm"],
        )
        assert result.exit_code == 0
        mock_stop.assert_called_once_with()
        mock_start.assert_called_once_with(mounts=sorted(["/srv/other", "/Volumes/d"]))
        mock_add.assert_called_once()
        mock_client.return_value.containers.get.assert_called_once_with("scad-run-1")


class TestCodeAddOrderingAndErrorHandling:
    """Regression tests for fix 5: code_add must validate the cheap,
    non-destructive thing (name collision) before doing anything destructive
    (VM restart, which stops every running scad session), and every VM call
    site here must handle VMUnsupported the same way every other VM call
    site in the CLI does."""

    @patch("scad.cli.vm_start")
    @patch("scad.cli.vm_stop")
    @patch("scad.cli.workspace_add")
    @patch("scad.cli.path_visible_in_vm", return_value=False)
    @patch("scad.cli.workspace_name_taken", return_value=True)
    @patch("scad.cli.validate_run_id")
    def test_name_collision_checked_before_any_vm_restart(
        self, _v, _taken, _vis, mock_add, mock_stop, mock_start, runner
    ):
        """A `--name` that already exists must fail before vm_stop/vm_start
        are ever called -- the old ordering restarted the VM (killing every
        running session) for a doomed FileExistsError."""
        result = runner.invoke(
            main,
            ["code", "add", "run-1", "--path", "/Volumes/d", "--name", "d"],
        )
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()
        mock_stop.assert_not_called()
        mock_start.assert_not_called()
        mock_add.assert_not_called()

    @patch("scad.cli.vm_start")
    @patch("scad.cli.vm_stop")
    @patch("scad.cli.workspace_add")
    @patch("scad.cli.path_visible_in_vm", return_value=True)
    @patch("scad.cli.workspace_name_taken", return_value=True)
    @patch("scad.cli.validate_run_id")
    def test_name_collision_checked_even_when_path_is_visible(
        self, _v, _taken, _vis, mock_add, mock_stop, mock_start, runner
    ):
        """The collision check must run unconditionally, not just on the
        VM-restart branch."""
        result = runner.invoke(
            main,
            ["code", "add", "run-1", "--path", "/Users/t/x", "--name", "x"],
        )
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()
        mock_add.assert_not_called()

    @patch("scad.cli.vm_stop")
    @patch("scad.cli.vm_start", side_effect=VMUnsupported("colima is not installed"))
    @patch("scad.cli.workspace_add")
    @patch("scad.cli.path_visible_in_vm", return_value=False)
    @patch("scad.cli.workspace_name_taken", return_value=False)
    @patch("scad.cli.validate_run_id")
    def test_vm_unsupported_prints_scad_prefixed_error_and_exits_2(
        self, _v, _taken, _vis, mock_add, mock_start, mock_stop, runner
    ):
        """A missing colima must surface the same way every other VM call
        site does (`[scad] ...`, exit 2) -- not a bare click `Error:` with
        exit 1."""
        result = runner.invoke(
            main,
            ["code", "add", "run-1", "--path", "/Volumes/d", "--name", "d",
             "--restart-vm"],
        )
        assert result.exit_code == 2
        assert "[scad]" in result.output
        assert "colima is not installed" in result.output
        mock_add.assert_not_called()


class TestResolveCommand:
    @pytest.fixture
    def runner(self):
        import inspect

        from click.testing import CliRunner

        if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
            return CliRunner(mix_stderr=False)
        return CliRunner()

    def test_resolves_via_marker_and_prints_the_path_on_stdout(self, runner, tmp_path):
        (tmp_path / "design.yaml").touch()
        result = runner.invoke(
            main, ["resolve", "--marker", "design.yaml", "--start", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == str(tmp_path)

    def test_explicit_argument_wins(self, runner, tmp_path):
        result = runner.invoke(main, ["resolve", str(tmp_path)])
        assert result.exit_code == 0
        assert result.stdout.strip() == str(tmp_path)

    def test_unresolved_exits_1_and_prints_what_was_tried(self, runner, tmp_path):
        result = runner.invoke(
            main, ["resolve", "--marker", "design.yaml", "--start", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "marker:design.yaml" in result.stderr
        assert "options:" in result.stderr

    def test_bad_flag_exits_2_not_1(self, runner):
        """Usage error and 'no target here' must be distinguishable by exit code.

        2 is Click's own UsageError code; 1 is ours for unresolved.
        """
        result = runner.invoke(main, ["resolve", "--nonsense"])
        assert result.exit_code == 2

    def test_json_carries_path_matched_by_and_tried(self, runner, tmp_path):
        (tmp_path / ".git").mkdir()
        result = runner.invoke(
            main, ["resolve", "--git-root", "--start", str(tmp_path), "--json"]
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["path"] == str(tmp_path)
        assert payload["matched_by"] == "marker:.git"
        assert "marker:.git" in payload["tried"]

    def test_json_on_failure_still_emits_a_document(self, runner, tmp_path):
        result = runner.invoke(
            main, ["resolve", "--marker", "design.yaml", "--start", str(tmp_path), "--json"]
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["path"] is None
        assert payload["matched_by"] == "unresolved"

    def test_help_documents_the_matched_by_vocabulary(self, runner):
        """--help is the entire agent-facing documentation surface for Phase 0."""
        result = runner.invoke(main, ["resolve", "--help"])
        assert result.exit_code == 0
        for token in ("explicit", "marker:<file>", "ask", "unresolved"):
            assert token in result.output


class TestArchiveCommand:
    def test_reports_a_summary(self, runner, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "history.jsonl").write_bytes(b'{"a":1}\n')
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))

        result = runner.invoke(main, ["archive"])

        assert result.exit_code == 0
        assert "created" in result.output
        assert (tmp_path / "arc" / "claude" / "history.jsonl").is_file()

    def test_json_output_is_parseable(self, runner, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "history.jsonl").write_bytes(b'{"a":1}\n')
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))

        result = runner.invoke(main, ["archive", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["counts"]["created"] == 1
        assert payload["archive_root"].endswith("arc")

    def test_run_flag_archives_only_that_run(self, runner, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        run = home / ".scad" / "runs" / "r1" / "claude"
        run.mkdir(parents=True)
        (run / "history.jsonl").write_bytes(b'{"a":1}\n')

        result = runner.invoke(main, ["archive", "--run", "r1"])

        assert result.exit_code == 0
        assert (tmp_path / "arc" / "runs" / "r1" / "history.jsonl").is_file()


class TestIndexCommands:
    def _seed(self, tmp_path, monkeypatch):
        import json as _json
        arc = tmp_path / "arc"
        (arc / "claude" / "projects" / "-repo").mkdir(parents=True)
        (arc / "claude" / "projects" / "-repo" / "S1.jsonl").write_text(_json.dumps({
            "type": "assistant", "sessionId": "S1", "timestamp": "2026-07-28T10:00:00.000Z",
            "cwd": "/repo", "message": {"role": "assistant",
                                        "content": [{"type": "text", "text": "hello"}]},
        }) + "\n")
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))

    def test_reindex_reports_counts(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        result = runner.invoke(main, ["reindex", "--no-archive"])
        assert result.exit_code == 0
        assert "sessions" in result.output

    def test_session_ls_lists_the_row(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "ls"])
        assert result.exit_code == 0
        assert "S1" in result.output

    def test_session_show_includes_turn_count(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "show", "S1"])
        assert result.exit_code == 0
        assert "turns" in result.output.lower()

    def test_session_show_unknown_id_exits_nonzero(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "show", "NOPE"])
        assert result.exit_code != 0

    def test_project_ls_groups(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["project", "ls"])
        assert result.exit_code == 0

    def test_session_ls_json_is_parseable(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "ls", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["id"] == "S1"

    def test_since_and_until_bound_the_window(self, runner, tmp_path, monkeypatch):
        """'What was I doing in March' is the query this exists for."""
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])

        inside = runner.invoke(main, ["session", "ls", "--since", "2026-07-01", "--json"])
        assert json.loads(inside.stdout)[0]["id"] == "S1"

        after = runner.invoke(main, ["session", "ls", "--since", "2026-08-01", "--json"])
        assert json.loads(after.stdout) == []

        before = runner.invoke(main, ["session", "ls", "--until", "2026-07-01", "--json"])
        assert json.loads(before.stdout) == []

    def test_bad_date_is_a_clear_error(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "ls", "--since", "last-tuesday"])
        assert result.exit_code != 0
        assert "YYYY-MM-DD" in result.output

    def test_outcome_filter_finds_sessions_awaiting_input(self, runner, tmp_path, monkeypatch):
        """The query this whole feature exists for: what is waiting on me?"""
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "ls", "--outcome", "awaiting-user", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)[0]["id"] == "S1"

    def test_tool_result_last_is_a_filterable_outcome(self, runner, tmp_path, monkeypatch):
        """The majority outcome on a real index — unfilterable is unusable."""
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "ls", "--outcome", "tool-result-last"])
        assert result.exit_code == 0, result.output

    def test_session_ls_shows_the_human_name_over_a_uuid(self, runner, tmp_path, monkeypatch):
        """nd-5 is how a person refers to the session; the uuid prefix is not."""
        self._seed(tmp_path, monkeypatch)
        (Path(os.environ["SCAD_ARCHIVE"]) / "claude" / "jobs" / "j").mkdir(parents=True)
        (Path(os.environ["SCAD_ARCHIVE"]) / "claude" / "jobs" / "j" /
         "state-history.jsonl").write_text(json.dumps({
             "sessionId": "S1", "name": "nd-5", "state": "blocked",
             "needs": "drop the bioRxiv PDF", "detail": "workflow salvaged",
             "updatedAt": "2026-07-28T17:56:43.450Z"}) + "\n")
        runner.invoke(main, ["reindex", "--no-archive"])

        result = runner.invoke(main, ["session", "ls"])
        assert result.exit_code == 0
        assert "nd-5" in result.output

        shown = runner.invoke(main, ["session", "show", "S1"])
        assert "nd-5" in shown.output
        assert "blocked" in shown.output
        assert "drop the bioRxiv PDF" in shown.output

    def test_session_ls_falls_back_to_the_id_when_unnamed(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "ls"])
        assert "S1" in result.output

    def test_session_ls_shows_a_renamed_session_by_its_name(self, runner, tmp_path, monkeypatch):
        """`/rename` is the commoner way a session acquires a human name."""
        self._seed(tmp_path, monkeypatch)
        p = (Path(os.environ["SCAD_ARCHIVE"]) / "claude" / "projects" / "-repo" / "S1.jsonl")
        with p.open("a") as fh:
            fh.write(json.dumps({"type": "custom-title", "sessionId": "S1",
                                 "customTitle": "jul29-session-cli"}) + "\n")
        runner.invoke(main, ["reindex", "--no-archive"])

        result = runner.invoke(main, ["session", "ls"])
        assert result.exit_code == 0
        line = next(ln for ln in result.output.splitlines() if "S1" in ln)
        assert re.match(r"^S1\s+jul29-session-cli\s", line), line

    def test_session_ls_leaves_the_name_blank_when_nobody_renamed(self, runner, tmp_path,
                                                                  monkeypatch):
        """The id is always shown; the name column is EMPTY rather than filled
        with derived text. Showing an agent's own title there is what made a
        session read as "/rename writing-wm-evals-research"."""
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        from scad.index import connect as index_connect

        conn = index_connect()
        conn.execute("UPDATE sessions SET title = 'Doing the thing' WHERE id = 'S1'")
        conn.commit()

        result = runner.invoke(main, ["session", "ls"])
        line = next(ln for ln in result.output.splitlines() if "S1" in ln)
        assert re.match(r"^S1\s+2026-", line), line       # id, then blank, then the date

    def test_grade_filter_separates_skeletons(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "ls", "--grade", "skeleton", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []      # this fixture has a real transcript


class TestReadAndSearch:
    def _seed(self, tmp_path, monkeypatch):
        import json as _json
        arc = tmp_path / "arc"
        (arc / "claude" / "projects" / "-repo").mkdir(parents=True)
        (arc / "claude" / "projects" / "-repo" / "S1.jsonl").write_text("".join(
            _json.dumps(r) + "\n" for r in [
                {"type": "user", "sessionId": "S1", "timestamp": "2026-07-28T10:00:00.000Z",
                 "cwd": "/repo", "message": {"role": "user", "content": "find the resolver"}},
                {"type": "assistant", "sessionId": "S1", "timestamp": "2026-07-28T10:00:05.000Z",
                 "message": {"role": "assistant", "content": [
                     {"type": "thinking", "thinking": "weighing markers against git-root"},
                     {"type": "text", "text": "the resolver returns a directory"},
                 ]}},
            ]))
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))

    def test_read_prints_turn_text(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "read", "S1"])
        assert result.exit_code == 0
        assert "the resolver returns a directory" in result.output

    def test_read_can_exclude_reasoning(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "read", "S1", "--kind", "text"])
        assert "weighing markers" not in result.output
        assert "the resolver returns a directory" in result.output

    def test_read_unknown_session_exits_nonzero(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        assert runner.invoke(main, ["session", "read", "NOPE"]).exit_code != 0

    def test_search_finds_the_session(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["search", "resolver"])
        assert result.exit_code == 0
        assert "S1" in result.output

    def test_search_with_no_hits_says_so(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["search", "zzzznomatch"])
        assert result.exit_code == 0
        assert "no match" in result.output.lower()


class TestRunSessionSplit:
    """v2.1 — the container verbs move to `scad run`; `session` means traces.

    Three levels the code already models: a RUN is the container, a JOB is one
    agent process inside it, a SESSION is that agent's trace. Only the command
    group was misnamed, which is what made this mechanical.
    """

    CONTAINER_VERBS = ("start", "stop", "clean", "attach", "info",
                       "inject", "jobs", "logs", "send", "refresh")
    # `note` and `notes` belong here rather than under `run`: they are keyed on
    # a session uuid, not a run id, and a note can outlive every container that
    # ever existed.
    TRACE_VERBS = ("ls", "show", "read", "launch", "resume", "note", "notes")

    def test_container_verbs_live_under_run(self, runner):
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        for verb in self.CONTAINER_VERBS:
            assert f"  {verb}" in result.output, f"{verb} missing from `scad run --help`"

    def test_run_ls_replaces_status(self, runner):
        result = runner.invoke(main, ["run", "--help"])
        assert "  ls" in result.output

    def test_session_help_offers_only_the_trace_verbs(self, runner):
        """The whole point of the rename: `scad session` stops being ambiguous."""
        result = runner.invoke(main, ["session", "--help"])
        assert result.exit_code == 0
        listed = {name for name in main.commands["session"].commands
                  if not main.commands["session"].commands[name].hidden}
        assert listed == set(self.TRACE_VERBS)
        for verb in self.CONTAINER_VERBS:
            assert f"  {verb} " not in result.output

    def test_old_paths_are_hidden_aliases_not_deletions(self, runner):
        """Muscle memory keeps working; --help stops teaching the old shape.

        Not a deprecation cycle — an advertised alias would preserve exactly the
        ambiguity the rename removes.
        """
        session_group = main.commands["session"]
        for verb in self.CONTAINER_VERBS:
            assert verb in session_group.commands, f"scad session {verb} was deleted"
            assert session_group.commands[verb].hidden is True

    def test_the_alias_and_the_new_path_are_the_same_command(self, runner):
        run_group = main.commands["run"]
        session_group = main.commands["session"]
        for verb in self.CONTAINER_VERBS:
            assert (session_group.commands[verb].callback
                    is run_group.commands[verb].callback)

    def test_status_still_works_and_is_hidden(self, runner):
        assert main.commands["status"].hidden is True
        assert main.commands["status"].callback is main.commands["run"].commands["ls"].callback
        top = runner.invoke(main, ["--help"])
        assert "  status" not in top.output
        assert "  run " in top.output

    @patch("scad.cli.list_scad_containers")
    @patch("scad.cli.get_recently_crashed")
    def test_run_ls_lists_runs(self, mock_crashed, mock_containers, runner):
        mock_containers.return_value = [
            {"run_id": "demo-test-Mar03-1200", "config": "demo",
             "branch": "scad-demo-test-Mar03-1200", "started": "2026-03-03T12:00:00+00:00"},
        ]
        mock_crashed.return_value = []
        result = runner.invoke(main, ["run", "ls"])
        assert result.exit_code == 0
        assert "demo-test-Mar03-1200" in result.output

    @patch("scad.cli.get_session_usage", return_value=None)
    @patch("scad.cli.get_session_info")
    def test_session_info_alias_still_resolves_a_run_id(self, mock_info, _usage, runner):
        mock_info.return_value = {"run_id": "demo-test-Mar03-1200", "config": "demo",
                                  "branch": "b", "container": "running",
                                  "clones_path": None, "clones": [],
                                  "claude_sessions": [], "events": []}
        old = runner.invoke(main, ["session", "info", "demo-test-Mar03-1200"])
        new = runner.invoke(main, ["run", "info", "demo-test-Mar03-1200"])
        assert old.exit_code == 0, old.output
        assert old.output == new.output


class TestRenameLeftNoStaleDocs:
    """~95 references across 9 files. skills/scad/SKILL.md is the critical one:
    it teaches agents the commands, so a stale copy makes every scad-skill agent
    call dead verbs."""

    VERBS = ("start", "stop", "clean", "attach", "info",
             "inject", "jobs", "logs", "send", "refresh")

    def _sources(self):
        root = Path(__file__).resolve().parent.parent
        for pattern in ("*.md", "docs/*.md", "skills/**/*.md", "examples/*",
                        "commands/*.md"):
            for p in root.glob(pattern):
                if p.is_file() and p.name != "CHANGELOG.md":
                    yield p

    def test_no_doc_teaches_a_container_verb_under_session(self):
        stale = []
        for path in self._sources():
            text = path.read_text(errors="ignore")
            for verb in self.VERBS:
                if f"scad session {verb}" in text:
                    stale.append(f"{path.name}: scad session {verb}")
        assert stale == []

    def test_no_doc_teaches_scad_status(self):
        stale = [p.name for p in self._sources()
                 if "scad status" in p.read_text(errors="ignore")]
        assert stale == []


class TestSessionNote:
    """`scad session note` — the write CLI for the one tier with no second copy."""

    NOTE = {"topic": "notes-store", "relation": "continue", "title": "built it",
            "text": "**Frame**\nmulti\nline", "tags": ["notes", "jsonl"],
            "entities": ["session-index.md"]}

    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        return tmp_path / ".scad"

    def _projects(self, tmp_path, monkeypatch, cwd, session_id="S1"):
        """A fake ~/.claude/projects holding one live transcript for `cwd`."""
        from scad.notes import encode_cwd
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        d = home / ".claude" / "projects" / encode_cwd(str(Path(cwd).resolve()))
        d.mkdir(parents=True)
        (d / f"{session_id}.jsonl").write_text(
            json.dumps({"sessionId": session_id, "cwd": str(Path(cwd).resolve())}) + "\n")
        return home

    def test_explicit_session_appends_the_record(self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps(self.NOTE))
        assert result.exit_code == 0, result.output
        path = scad_home / "notes" / "claude" / "S1.jsonl"
        assert json.loads(path.read_text())["title"] == "built it"

    def test_the_file_lands_agent_sharded_and_session_keyed(self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "X9", "--agent", "codex"],
                      input=json.dumps(self.NOTE))
        assert (scad_home / "notes" / "codex" / "X9.jsonl").is_file()

    def test_a_second_note_appends_leaving_the_first_byte_identical(
            self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        path = scad_home / "notes" / "claude" / "S1.jsonl"
        first = path.read_bytes()
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps({**self.NOTE, "title": "and again"}))
        assert path.read_bytes().startswith(first)
        assert len(path.read_text().splitlines()) == 2

    def test_current_resolves_the_live_session_in_this_cwd(self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        work = tmp_path / "work"
        work.mkdir()
        self._projects(tmp_path, monkeypatch, work, session_id="LIVE")
        monkeypatch.chdir(work)
        result = runner.invoke(main, ["session", "note", "--current"],
                               input=json.dumps(self.NOTE))
        assert result.exit_code == 0, result.output
        assert (scad_home / "notes" / "claude" / "LIVE.jsonl").is_file()

    def test_current_records_the_cwd_it_resolved_from(self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        work = tmp_path / "work"
        work.mkdir()
        self._projects(tmp_path, monkeypatch, work, session_id="LIVE")
        monkeypatch.chdir(work)
        runner.invoke(main, ["session", "note", "--current"], input=json.dumps(self.NOTE))
        rec = json.loads((scad_home / "notes" / "claude" / "LIVE.jsonl").read_text())
        assert rec["cwd_at_write"] == str(work.resolve())

    def test_two_live_sessions_refuse_and_name_the_flag(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        work = tmp_path / "work"
        work.mkdir()
        home = self._projects(tmp_path, monkeypatch, work, session_id="alpha")
        from scad.notes import encode_cwd
        d = home / ".claude" / "projects" / encode_cwd(str(work.resolve()))
        (d / "beta.jsonl").write_text(json.dumps({"cwd": str(work.resolve())}) + "\n")
        monkeypatch.chdir(work)
        result = runner.invoke(main, ["session", "note", "--current"],
                               input=json.dumps(self.NOTE))
        assert result.exit_code != 0
        assert "--session" in result.output

    def test_no_session_at_all_is_a_clear_error_not_a_traceback(
            self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        result = runner.invoke(main, ["session", "note", "--current"],
                               input=json.dumps(self.NOTE))
        assert result.exit_code != 0
        assert "--session" in result.output

    def test_current_takes_the_session_id_the_agent_exported(
            self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "EXPORTED")
        result = runner.invoke(main, ["session", "note", "--current"],
                               input=json.dumps(self.NOTE))
        assert result.exit_code == 0, result.output
        assert (scad_home / "notes" / "claude" / "EXPORTED.jsonl").is_file()

    def test_current_resolves_against_the_agent_that_was_asked_for(
            self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        monkeypatch.setenv("CODEX_THREAD_ID", "CX7")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "CLAUDE-PARENT")
        result = runner.invoke(main, ["session", "note", "--current", "--agent", "codex"],
                               input=json.dumps(self.NOTE))
        assert result.exit_code == 0, result.output
        assert (scad_home / "notes" / "codex" / "CX7.jsonl").is_file()
        assert not (scad_home / "notes" / "codex" / "CLAUDE-PARENT.jsonl").exists()

    def test_current_for_codex_refuses_rather_than_using_an_inherited_claude_id(
            self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        work = tmp_path / "work"
        work.mkdir()
        self._projects(tmp_path, monkeypatch, work, session_id="CLAUDE-PARENT")
        monkeypatch.chdir(work)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "CLAUDE-PARENT")
        result = runner.invoke(main, ["session", "note", "--current", "--agent", "codex"],
                               input=json.dumps(self.NOTE))
        assert result.exit_code != 0
        assert "CODEX_THREAD_ID" in result.output
        assert not (scad_home / "notes" / "codex").exists()

    def test_neither_target_is_refused(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note"], input=json.dumps(self.NOTE))
        assert result.exit_code != 0

    def test_malformed_stdin_is_refused_without_writing_anything(
            self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input="not json at all")
        assert result.exit_code != 0
        assert not (scad_home / "notes" / "claude" / "S1.jsonl").exists()

    def test_it_confirms_briefly_without_echoing_the_record(
            self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps(self.NOTE))
        assert "notes-store" in result.output          # the topic
        assert "**Frame**" not in result.output        # not the whole text


class TestSessionNotes:
    NOTE = {"topic": "t", "relation": "continue", "title": "first", "tags": ["a"]}

    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        return tmp_path / ".scad"

    def test_reads_back_newest_last(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps({**self.NOTE, "title": "second"}))
        result = runner.invoke(main, ["session", "notes", "S1"])
        assert result.exit_code == 0
        assert result.output.index("first") < result.output.index("second")

    def test_json_round_trips_the_records_as_written(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        result = runner.invoke(main, ["session", "notes", "S1", "--json"])
        payload = json.loads(result.stdout)
        assert payload[0]["title"] == "first"
        assert payload[0]["cwd_at_write"]

    def test_it_reads_the_file_not_the_index(self, runner, tmp_path, monkeypatch):
        # The file is truth. A note must be readable before any reindex has run,
        # and after a rebuild has dropped every row.
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        result = runner.invoke(main, ["session", "notes", "S1"])
        assert result.exit_code == 0 and "first" in result.output

    def test_a_session_with_no_notes_says_so(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "notes", "NOPE"])
        assert result.exit_code == 0
        assert "no notes" in result.output.lower()

    def test_session_show_counts_the_notes(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "show", "S1"])
        assert result.exit_code == 0
        assert "notes" in result.output

    def test_session_show_lists_each_note_kind(self, runner, tmp_path, monkeypatch):
        # A handoff among a session's notes is the one you want first, and the
        # listing used to show only topic and title -- so nothing said which.
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps({**self.NOTE, "kind": "handoff",
                                        "topic": "where-this-stands"}))
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "show", "S1"])
        assert result.exit_code == 0
        assert "handoff" in result.output

    def test_notes_read_shows_a_readable_stamp(self, runner, tmp_path, monkeypatch):
        # The raw ISO string is in the file; a reader wants to know how old it
        # is without doing the subtraction.
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        result = runner.invoke(main, ["notes", "read", "S1", "--last"])
        assert result.exit_code == 0
        assert "ago)" in result.output

    def test_reindex_reports_notes_it_had_not_already_seen(
            self, runner, tmp_path, monkeypatch):
        # Drift must be visible: a pass that indexed a note and said nothing
        # would leave "did /remember work?" answerable only by opening sqlite.
        #
        # The note is placed on disk directly rather than through `session note`,
        # because that verb now indexes as it writes — so a note it wrote is
        # legitimately NOT news to the next pass. What must still be reported is
        # a note that arrived some other way: another machine, a restored
        # backup, a shard synced in.
        home = self._home(tmp_path, monkeypatch)
        shard = home / "notes" / "claude"
        shard.mkdir(parents=True)
        (shard / "S9.jsonl").write_text(
            json.dumps({**self.NOTE, "ts": "2026-08-05T10:00:00+05:30"}) + "\n")
        result = runner.invoke(main, ["reindex", "--no-archive"])
        assert "notes: 1" in result.output

    def test_a_note_written_here_is_not_news_to_the_next_pass(
            self, runner, tmp_path, monkeypatch):
        # The other half of the same rule: indexing at write must advance the
        # offset, so the pass has nothing left to do.
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        result = runner.invoke(main, ["reindex", "--no-archive"])
        assert "notes: 1" not in result.output


class TestRememberSkillIsAThinCaller:
    """`/remember` produces the record; `scad session note` decides where it goes.

    The split is the point. The old command reimplemented project resolution in
    prose — basename of the git root, fall back to the cwd — which meant every
    other agent that wanted to capture had to reimplement it again, and a change
    to what a project means would have had to be made in two languages.

    It is a SKILL now, not a Claude Code command. A command reached exactly one
    harness; the shared convention reaches every agent, which is the whole point
    of a capture verb that codex and pi are also supposed to call.
    """

    ROOT = Path(__file__).resolve().parent.parent

    @property
    def text(self):
        return (self.ROOT / "skills" / "remember" / "SKILL.md").read_text()

    def test_the_claude_only_command_is_gone(self):
        # Leaving it would re-create the duplication the migration removes: the
        # same verb offered twice, from a plugin and from the skills directory.
        assert not (self.ROOT / "commands" / "remember.md").exists()

    def test_its_name_preserves_the_slash_verb(self):
        # A skill's frontmatter `name` IS its invocation — `/remember` in Claude,
        # `$remember` in codex. Renaming the directory or the field changes what
        # the human types, so both are pinned.
        assert (self.ROOT / "skills" / "remember" / "SKILL.md").exists()
        assert re.search(r"^name:\s*remember\s*$", self.text, re.M)

    def test_it_carries_no_command_only_syntax(self):
        # $ARGUMENTS and argument-hint are slash-command features with no meaning
        # in a SKILL.md; left in place they would read as literal text to every
        # agent that is not Claude Code.
        assert "$ARGUMENTS" not in self.text
        assert "argument-hint" not in self.text

    def test_it_has_no_path_that_breaks_once_installed(self):
        # Skills are COPIED into ~/.agents/skills, so a relative path escaping
        # the repo resolves to nothing there — and fails silently, as a dead
        # markdown link rather than an error.
        assert "../" not in self.text

    def test_it_pipes_to_the_cli(self):
        assert "scad session note --current" in self.text

    def test_the_dead_store_path_is_gone(self):
        # ~/.capture/<project>/ was never created on any machine, and a project
        # in a durable path is what session-state-cli.md forbids.
        assert "~/.capture" not in self.text

    def test_it_no_longer_resolves_a_project_in_prose(self):
        lowered = self.text.lower()
        assert "basename of the current git repo" not in lowered
        assert "git repo root (fall back" not in lowered

    def test_it_does_not_tell_the_agent_to_append_by_hand(self):
        # The old step 3 said "use a tool call that appends (e.g. shell >>)".
        # Asserting the absence of ">>" would be wrong — the file now names it
        # in order to forbid it.
        assert "that **appends**" not in self.text
        assert "Do not append with" in self.text

    def test_the_anti_inflation_discipline_survives(self):
        # The valuable part: everything else here is mechanism, this is judgment.
        assert "anti-inflation" in self.text.lower()
        assert "tentative" in self.text
        assert "reverse it" in self.text

    def test_the_record_fields_still_match_the_capture_format(self):
        from scad.notes import NOTE_FIELDS
        authored = set(NOTE_FIELDS) - {"ts", "cwd_at_write"}   # filled by the CLI
        for field in authored:
            assert f"`{field}`" in self.text, f"{field} undocumented in /remember"

    def test_the_fields_the_cli_fills_are_marked_as_not_the_callers_job(self):
        assert "Do not set them." in self.text


@pytest.mark.usefixtures("offline_view")
class TestViewCommand:
    def test_writes_a_page_and_reports_the_path(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        result = runner.invoke(main, ["view", "--no-open"])
        assert result.exit_code == 0
        out = tmp_path / ".scad" / "view.html"
        assert out.is_file()
        assert "</html>" in out.read_text()
        assert str(out) in result.output

    def test_honours_an_explicit_output_path(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        target = tmp_path / "elsewhere.html"
        result = runner.invoke(main, ["view", "--no-open", "--output", str(target)])
        assert result.exit_code == 0
        assert target.is_file()

    def test_no_open_does_not_launch_a_browser(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        with patch("scad.cli.webbrowser.open") as opener:
            runner.invoke(main, ["view", "--no-open"])
        opener.assert_not_called()


class TestReindexSweepIsolation:
    def test_reindex_sweeps_by_default_but_can_be_opted_out(self, runner, tmp_path, monkeypatch):
        """The sweep reads the real ~/.claude — SCAD_HOME cannot redirect it — so
        the CLI defaults it on for users and tests pass --no-archive."""
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        with patch("scad.index.archive_all") as sweep:
            runner.invoke(main, ["reindex"])
        sweep.assert_called_once()

        with patch("scad.index.archive_all") as sweep:
            runner.invoke(main, ["reindex", "--no-archive"])
        sweep.assert_not_called()


@pytest.mark.usefixtures("offline_view")
class TestViewRefresh:
    """`scad view` archives and indexes before rendering, by default.

    It shipped the other way round — read-only, with `--refresh` as the opt-in
    exception — because the viewer spec states the read-only contract three
    times. Two things overturned that. Nothing else refreshes the index (the
    launchd timer was declined), so opt-in meant stale whenever you forgot. And
    the failure path already existed: a refresh that fails warns and renders
    the existing index rather than withholding the page, which was the
    strongest argument against defaulting it.

    `--no-refresh` preserves the pure reader for anyone who wants it.
    """

    def test_the_default_refreshes(self, runner, tmp_path, monkeypatch):
        """Nothing else refreshes the index — the timer was declined — so an
        opt-in refresh means a stale page every time you forget the flag."""
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        with patch("scad.cli.run_reindex") as ri:
            result = runner.invoke(main, ["view", "--no-open"])
        ri.assert_called_once()
        assert result.exit_code == 0

    def test_no_refresh_keeps_the_pure_reader(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        with patch("scad.cli.run_reindex") as ri:
            result = runner.invoke(main, ["view", "--no-open", "--no-refresh"])
        ri.assert_not_called()
        assert result.exit_code == 0

    def test_the_old_refresh_flag_is_still_accepted(self, runner, tmp_path, monkeypatch):
        """Muscle memory and scripts should not break — it just does nothing."""
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        with patch("scad.cli.run_reindex") as ri:
            result = runner.invoke(main, ["view", "--no-open", "--refresh"])
        assert result.exit_code == 0
        ri.assert_called_once()

    def test_only_one_way_is_documented(self, runner):
        """Two documented flags for one decision is one too many."""
        help_text = runner.invoke(main, ["view", "--help"]).output
        assert "--no-refresh" in help_text
        assert "--refresh" not in help_text.replace("--no-refresh", "")

    def test_refresh_runs_an_incremental_pass_before_rendering(
        self, runner, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        with patch("scad.cli.run_reindex") as ri:
            result = runner.invoke(main, ["view", "--no-open"])
        ri.assert_called_once()
        # Incremental, and sweeping: archiving is how new work enters the index
        # at all, so a refresh that skipped it would render the same stale page.
        assert ri.call_args.kwargs.get("archive_first") is True
        assert ri.call_args.kwargs.get("rebuild", False) is False
        assert result.exit_code == 0

    def test_a_failed_refresh_still_renders(self, runner, tmp_path, monkeypatch):
        """A refresh is a convenience. If archiving or indexing fails, the page
        the user asked for must still appear — showing stale data beats showing
        nothing, provided the failure is said out loud."""
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        with patch("scad.cli.run_reindex", side_effect=OSError("disk full")):
            result = runner.invoke(main, ["view", "--no-open"])
        assert result.exit_code == 0
        assert "disk full" in result.output
        assert str(tmp_path / ".scad") in result.output


class TestProjectShow:
    """It has to agree with the page about the same project."""

    def _seed(self, tmp_path, monkeypatch):
        arc = tmp_path / "arc"
        (arc / "claude" / "projects" / "-repo").mkdir(parents=True)
        (arc / "claude" / "projects" / "-repo" / "S1.jsonl").write_text(json.dumps({
            "type": "assistant", "sessionId": "S1", "timestamp": "2026-07-28T10:00:00.000Z",
            "cwd": "/repo", "message": {"role": "assistant",
                                        "content": [{"type": "text", "text": "hello"}]},
        }) + "\n")
        monkeypatch.setenv("SCAD_ARCHIVE", str(arc))
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        return arc

    def _indexed(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex", "--no-archive"])
        from scad.index import connect as index_connect

        from scad.index import upsert_session
        from scad.records import SessionRecord

        conn = index_connect()
        conn.execute("UPDATE sessions SET project = 'alpha' WHERE id = 'S1'")
        upsert_session(conn, SessionRecord(
            id="SUB1", kind="subagent", agent="claude", source="claude-transcript",
            cwd="/repo", started=1000, ended=2000, parent_session_id="S1"),
            machine="mac", project="alpha", archive_path="/arc/SUB1.jsonl",
            source_size=1, source_mtime=1, parsed_offset=1)
        conn.commit()
        return conn

    def test_subagents_are_excluded_exactly_as_the_viewer_excludes_them(
        self, runner, tmp_path, monkeypatch
    ):
        """A subagent is triggered BY an agent and cannot be resumed. Listing it
        here while the page hides it made the two disagree about one project."""
        self._indexed(runner, tmp_path, monkeypatch)
        result = runner.invoke(main, ["project", "show", "alpha"])
        assert result.exit_code == 0, result.output
        assert "S1" in result.output
        assert "SUB1" not in result.output

    def test_the_name_column_is_shown(self, runner, tmp_path, monkeypatch):
        """`session ls` shows it; the same session in `project show` did not."""
        conn = self._indexed(runner, tmp_path, monkeypatch)
        conn.execute("UPDATE sessions SET name = 'jul29-viewer' WHERE id = 'S1'")
        conn.commit()
        result = runner.invoke(main, ["project", "show", "alpha"])
        line = next(ln for ln in result.output.splitlines() if "S1" in ln)
        assert re.match(r"^S1\s+jul29-viewer\s", line), line

    def test_an_unnamed_session_leaves_the_column_blank_never_the_title(
        self, runner, tmp_path, monkeypatch
    ):
        conn = self._indexed(runner, tmp_path, monkeypatch)
        conn.execute("UPDATE sessions SET title = 'Doing the thing' WHERE id = 'S1'")
        conn.commit()
        result = runner.invoke(main, ["project", "show", "alpha"])
        line = next(ln for ln in result.output.splitlines() if "S1" in ln)
        assert re.match(r"^S1\s+2026-", line), line     # id, blank name, then the date
        assert "Doing the thing" in line               # still there, as the title

    def test_a_project_with_nothing_you_started_is_an_error_not_a_blank(
        self, runner, tmp_path, monkeypatch
    ):
        conn = self._indexed(runner, tmp_path, monkeypatch)
        conn.execute("UPDATE sessions SET project = 'beta' WHERE id = 'S1'")
        conn.commit()
        result = runner.invoke(main, ["project", "show", "alpha"])
        assert result.exit_code != 0
        assert "alpha" in result.output


class TestNotesForHandoff:
    """`scad notes` — the minimum a fresh session needs to pick up work.

    This exists to replace the handoff document. A note is written at the end of
    a session; the next session finds it and reads it. Two verbs only: find
    which notes exist for a project, and read one in full. Anything more is the
    browse CLI, which is separately backlogged.
    """

    def _index(self, tmp_path, monkeypatch):
        from scad.index import connect
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        conn = connect(tmp_path / ".scad" / "index.sqlite")
        for sid, proj, name, ended in (
            ("S1", "scad", "jul29-cli", 100),
            ("S2", "other", None, 90),
        ):
            conn.execute(
                "INSERT INTO sessions (id, kind, agent, machine, grade, source, "
                "project, name, ended) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, "main", "claude", "m", "full", "claude-transcript", proj, name, ended))
        conn.execute("INSERT INTO notes (session_id, idx, ts, topic, title, note_path) "
                     "VALUES ('S1',0,200,'registry','What the registry solved','/n')")
        conn.execute("INSERT INTO notes (session_id, idx, ts, topic, title, note_path) "
                     "VALUES ('S2',0,150,'other-thing','Unrelated','/n')")
        conn.commit()
        conn.close()

    def test_ls_scoped_to_a_project_shows_only_that_project(self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--project", "scad"])
        assert result.exit_code == 0
        assert "What the registry solved" in result.output
        assert "Unrelated" not in result.output

    def test_ls_gives_the_session_id_to_read_with(self, runner, tmp_path, monkeypatch):
        # The listing is only useful if it hands over the key to the next command.
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--project", "scad"])
        assert "S1" in result.output

    def test_ls_json_is_machine_readable(self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--json"])
        rows = json.loads(result.output)
        assert {r["session_id"] for r in rows} == {"S1", "S2"}
        assert rows[0]["ts"] >= rows[-1]["ts"]      # newest first

    def test_ls_says_so_when_a_project_has_none(self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--project", "nothing-here"])
        assert result.exit_code == 0
        assert "No notes" in result.output

    def test_read_prints_the_full_record(self, runner, tmp_path, monkeypatch):
        """Reads the FILE, not the index — the index holds no body text at all."""
        self._index(tmp_path, monkeypatch)
        from scad.notes import append_note
        append_note({"topic": "registry", "title": "What the registry solved",
                     "text": "THE FULL BODY GOES HERE"}, session_id="S1", agent="claude")
        result = runner.invoke(main, ["notes", "read", "S1"])
        assert "THE FULL BODY GOES HERE" in result.output


class TestNotesCrossCapture:
    """A note may be filed against a project other than its session's.

    Before this the project only ever arrived through the sessions JOIN, so a
    bug noticed while working elsewhere was filed where nobody would look.
    """

    def _index(self, tmp_path, monkeypatch):
        from scad.index import connect
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        conn = connect(tmp_path / ".scad" / "index.sqlite")
        conn.execute(
            "INSERT INTO sessions (id, kind, agent, machine, grade, source, project) "
            "VALUES ('S1','main','claude','m','full','claude-transcript','alpha')")
        conn.execute(
            "INSERT INTO notes (session_id, idx, ts, kind, topic, project, title, "
            "tags, entities, note_path) VALUES "
            "('S1',0,300,'bug','beta-crash','beta','BETA IS BROKEN','[]','[]','/n')")
        conn.execute(
            "INSERT INTO notes (session_id, idx, ts, kind, topic, project, title, "
            "tags, entities, note_path) VALUES "
            "('S1',1,200,'info','alpha-work',NULL,'ORDINARY ALPHA NOTE','[]','[]','/n')")
        conn.commit()
        conn.close()

    def test_a_note_filed_against_another_project_lists_under_it(
            self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--project", "beta"])
        assert result.exit_code == 0, result.output
        assert "BETA IS BROKEN" in result.output
        assert "ORDINARY ALPHA NOTE" not in result.output

    def test_it_no_longer_lists_under_the_session_that_wrote_it(
            self, runner, tmp_path, monkeypatch):
        # The override is an override, not an addition: two projects claiming
        # one note is the ambiguity the field exists to remove.
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--project", "alpha"])
        assert "ORDINARY ALPHA NOTE" in result.output
        assert "BETA IS BROKEN" not in result.output

    def test_a_note_with_no_project_still_follows_its_session(
            self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        rows = json.loads(runner.invoke(
            main, ["notes", "ls", "--project", "alpha", "--json"]).output)
        assert [r["project"] for r in rows] == ["alpha"]

    def test_search_finds_the_cross_captured_note_under_its_own_project(
            self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["search", "beta", "--notes"])
        assert result.exit_code == 0, result.output
        assert "BETA IS BROKEN" in result.output


class TestNotesKindFilter:
    def _index(self, tmp_path, monkeypatch):
        from scad.index import connect
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        conn = connect(tmp_path / ".scad" / "index.sqlite")
        conn.execute(
            "INSERT INTO sessions (id, kind, agent, machine, grade, source, project) "
            "VALUES ('S1','main','claude','m','full','claude-transcript','alpha')")
        for idx, (kind, title) in enumerate((
                ("handoff", "WHERE WE STOPPED"), ("bug", "SOMETHING BROKE"),
                ("info", "AN ORDINARY NOTE"))):
            conn.execute(
                "INSERT INTO notes (session_id, idx, ts, kind, topic, title, tags, "
                "entities, note_path) VALUES ('S1',?,?,?,'t',?,'[]','[]','/n')",
                (idx, 100 - idx, kind, title))
        # Written before `kind` existed: NULL in the column, `info` by default.
        conn.execute(
            "INSERT INTO notes (session_id, idx, ts, topic, title, tags, entities, "
            "note_path) VALUES ('S1',9,50,'t','A LEGACY NOTE','[]','[]','/n')")
        conn.commit()
        conn.close()

    def test_kind_handoff_is_the_catch_up_query(self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--kind", "handoff"])
        assert result.exit_code == 0, result.output
        assert "WHERE WE STOPPED" in result.output
        assert "SOMETHING BROKE" not in result.output
        assert "AN ORDINARY NOTE" not in result.output

    def test_kind_combines_with_project(self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(
            main, ["notes", "ls", "--project", "alpha", "--kind", "bug"])
        assert "SOMETHING BROKE" in result.output
        assert "WHERE WE STOPPED" not in result.output

    def test_a_note_written_before_kind_existed_answers_as_info(
            self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--kind", "info"])
        assert "A LEGACY NOTE" in result.output
        assert "AN ORDINARY NOTE" in result.output

    def test_a_kind_outside_the_five_is_refused_by_the_flag(
            self, runner, tmp_path, monkeypatch):
        self._index(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "ls", "--kind", "decision"])
        assert result.exit_code != 0
        assert "decision" in result.output


class TestSessionNoteValidatesKindAndProject:
    """The write path: refuse a bad `kind`, never refuse the note over a project."""

    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        return tmp_path / ".scad"

    def _known(self, tmp_path, project="alpha"):
        from scad.index import connect
        conn = connect(tmp_path / ".scad" / "index.sqlite")
        conn.execute(
            "INSERT INTO sessions (id, kind, agent, machine, grade, source, project) "
            "VALUES ('OTHER','main','claude','m','full','claude-transcript',?)",
            (project,))
        conn.commit()
        conn.close()

    def _note(self, scad_home, session="S1"):
        return scad_home / "notes" / "claude" / f"{session}.jsonl"

    def test_an_unknown_kind_is_refused_and_nothing_is_written(
            self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps({"kind": "decision", "title": "t"}))
        assert result.exit_code != 0
        assert "decision" in result.output
        assert not self._note(scad_home).exists()

    def test_each_of_the_five_kinds_is_accepted(self, runner, tmp_path, monkeypatch):
        from scad.notes import KINDS
        scad_home = self._home(tmp_path, monkeypatch)
        for kind in KINDS:
            result = runner.invoke(main, ["session", "note", "--session", kind],
                                   input=json.dumps({"kind": kind, "title": "t"}))
            assert result.exit_code == 0, result.output
            assert json.loads(self._note(scad_home, kind).read_text())["kind"] == kind

    def test_omitting_kind_is_fine_and_lands_as_info(self, runner, tmp_path, monkeypatch):
        scad_home = self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps({"title": "t"}))
        assert result.exit_code == 0, result.output
        assert json.loads(self._note(scad_home).read_text())["kind"] == "info"

    def test_the_confirmation_line_shows_the_kind(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps({"kind": "handoff", "topic": "x",
                                                 "title": "t", "text": "**Frame**"}))
        assert "handoff" in result.output
        assert "**Frame**" not in result.output      # still confirms, does not echo

    def test_an_unknown_project_warns_and_the_note_is_still_written(
            self, runner, tmp_path, monkeypatch):
        # A note is authored data with no second copy. `scad project ls` counts
        # sessions, so a real new project is missing from it — refusing here
        # would lose notes over a name the index has merely not met yet.
        scad_home = self._home(tmp_path, monkeypatch)
        self._known(tmp_path, project="alpha")
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps({"title": "t", "project": "nowhere"}))
        assert result.exit_code == 0, result.output
        assert "nowhere" in result.output
        assert "warning" in result.output.lower()
        assert json.loads(self._note(scad_home).read_text())["project"] == "nowhere"

    def test_a_known_project_does_not_warn(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        self._known(tmp_path, project="alpha")
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps({"title": "t", "project": "alpha"}))
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()

    def test_a_project_only_another_note_has_used_counts_as_known(
            self, runner, tmp_path, monkeypatch):
        # The first cross-capture into a new project warns; the second should
        # not, or the warning becomes noise on deliberate, established use.
        self._home(tmp_path, monkeypatch)
        self._known(tmp_path, project="alpha")
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps({"title": "t", "project": "beta"}))
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["session", "note", "--session", "S2"],
                               input=json.dumps({"title": "t", "project": "beta"}))
        assert "warning" not in result.output.lower()

    def test_omitting_project_never_warns(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        result = runner.invoke(main, ["session", "note", "--session", "S1"],
                               input=json.dumps({"title": "t"}))
        assert "warning" not in result.output.lower()


class TestNotesReadDerivesRelation:
    """`relation` is not in the file, so reading must compute it."""

    def _store(self, tmp_path, monkeypatch, records):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        from scad.notes import append_note
        for r in records:
            append_note(r, session_id="S1", agent="claude")

    def test_the_json_output_carries_a_relation_the_file_does_not(
            self, runner, tmp_path, monkeypatch):
        self._store(tmp_path, monkeypatch, [
            {"topic": "a", "title": "one"}, {"topic": "a", "title": "two"},
            {"topic": "b", "parent": "a", "title": "three"}])
        raw = (tmp_path / ".scad" / "notes" / "claude" / "S1.jsonl").read_text()
        assert "relation" not in raw

        rows = json.loads(runner.invoke(main, ["session", "notes", "S1", "--json"]).output)
        assert [r["relation"] for r in rows] == ["shift", "continue", "branch"]

    def test_reading_one_note_still_places_it_in_its_thread(
            self, runner, tmp_path, monkeypatch):
        self._store(tmp_path, monkeypatch, [
            {"topic": "a", "title": "one"}, {"topic": "a", "title": "two"}])
        row = json.loads(runner.invoke(
            main, ["notes", "read", "S1", "--last", "--json"]).output)
        assert row["relation"] == "continue"


class TestNotesReadIsProgressive:
    """`notes read` must be able to fetch ONE note, not a session's whole history.

    Recall is progressive: read the newest note, and backtrack only while the
    metadata says the thread continues. Printing every note of a session defeats
    that — the cost of catching up should scale with how much you actually need,
    not with how long the session was.
    """

    def _store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        from scad.notes import append_note
        for i, (topic, body) in enumerate((
            ("older", "OLDEST BODY"), ("middle", "MIDDLE BODY"), ("newest", "NEWEST BODY"))):
            append_note({"topic": topic, "title": f"t{i}", "text": body},
                        session_id="S1", agent="claude")

    def test_last_reads_only_the_newest(self, runner, tmp_path, monkeypatch):
        self._store(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "read", "S1", "--last"])
        assert "NEWEST BODY" in result.output
        assert "MIDDLE BODY" not in result.output
        assert "OLDEST BODY" not in result.output

    def test_idx_reads_exactly_that_one(self, runner, tmp_path, monkeypatch):
        self._store(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "read", "S1", "--idx", "1"])
        assert "MIDDLE BODY" in result.output
        assert "NEWEST BODY" not in result.output

    def test_no_flag_still_reads_everything(self, runner, tmp_path, monkeypatch):
        # The whole-session read stays the default; progressive is opt-in.
        self._store(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "read", "S1"])
        for body in ("OLDEST BODY", "MIDDLE BODY", "NEWEST BODY"):
            assert body in result.output

    def test_an_idx_that_does_not_exist_says_so(self, runner, tmp_path, monkeypatch):
        self._store(tmp_path, monkeypatch)
        result = runner.invoke(main, ["notes", "read", "S1", "--idx", "99"])
        assert result.exit_code == 0
        assert "no note" in result.output.lower()


class TestSessionResume:
    """`scad session resume <id>` — back into a conversation, by id.

    Off the index, so it covers every session on the machine rather than the
    ones scad launched. A launch record only sharpens it.
    """

    def _seed(self, tmp_path, monkeypatch, session_id="S1", agent="claude",
              cwd="/repo", kind=None):
        import time as _time
        from scad.index import connect
        from scad.records import KIND_MAIN, SessionRecord

        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        monkeypatch.setattr("scad.cli.tmux_panes", lambda *a, **k: [])
        monkeypatch.setattr("scad.cli.claude_live_sessions", lambda *a, **k: [])
        now = int(_time.time() * 1000)
        rec = SessionRecord(id=session_id, kind=kind or KIND_MAIN, agent=agent,
                            source=f"{agent}-transcript", cwd=cwd,
                            started=now - 1000, ended=now, outcome="awaiting-user")
        conn = connect()
        from scad.index import upsert_session
        upsert_session(conn, rec, machine="mac", project="proj",
                       archive_path=f"/arc/{session_id}.jsonl", source_size=1,
                       source_mtime=1, parsed_offset=1)
        conn.commit()

    def _exec_spy(self, monkeypatch):
        calls = []
        monkeypatch.setattr("scad.cli._exec", lambda argv: calls.append(argv))
        return calls

    def test_print_emits_the_command_and_runs_nothing(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        calls = self._exec_spy(monkeypatch)
        result = runner.invoke(main, ["session", "resume", "S1", "--print"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == "cd /repo && claude --resume S1"
        assert calls == []

    def test_a_codex_session_prints_codex_resume(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch, session_id="C1", agent="codex")
        self._exec_spy(monkeypatch)
        result = runner.invoke(main, ["session", "resume", "C1", "--print"])
        assert result.output.strip() == "cd /repo && codex resume C1"

    def test_a_kimi_session_gets_its_prefix_back(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch, session_id="abc-123", agent="kimi")
        self._exec_spy(monkeypatch)
        result = runner.invoke(main, ["session", "resume", "abc-123", "--print"])
        assert result.output.strip() == "cd /repo && kimi --session session_abc-123"

    def test_a_closed_session_execs_the_agent_with_the_cwd_set(
            self, runner, tmp_path, monkeypatch):
        """`os.execvp`, not a shell: the caller lands in the session with no
        wrapper process left behind holding a pipe open."""
        repo = tmp_path / "repo"
        repo.mkdir()
        self._seed(tmp_path, monkeypatch, cwd=str(repo))
        calls = self._exec_spy(monkeypatch)
        chdirs = []
        monkeypatch.setattr("scad.cli.os.chdir", chdirs.append)

        result = runner.invoke(main, ["session", "resume", "S1"])

        assert result.exit_code == 0, result.output
        assert calls == [["claude", "--resume", "S1"]]
        assert chdirs == [str(repo)]

    def test_a_cwd_that_is_gone_still_resumes(self, runner, tmp_path, monkeypatch):
        """Recorded cwds outlive their directories; the conversation does not
        stop existing because the folder was moved."""
        self._seed(tmp_path, monkeypatch, cwd="/gone/away")
        calls = self._exec_spy(monkeypatch)
        monkeypatch.setattr("scad.cli.os.chdir", lambda p: None)
        result = runner.invoke(main, ["session", "resume", "S1"])
        assert result.exit_code == 0, result.output
        assert calls == [["claude", "--resume", "S1"]]
        assert "gone/away" in result.output

    def test_an_unknown_session_says_so(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        self._exec_spy(monkeypatch)
        result = runner.invoke(main, ["session", "resume", "NOPE"])
        assert result.exit_code != 0
        assert "NOPE" in result.output

    def test_a_subagent_cannot_be_resumed(self, runner, tmp_path, monkeypatch):
        from scad.records import KIND_SUBAGENT

        self._seed(tmp_path, monkeypatch, session_id="S1:agent-0", kind=KIND_SUBAGENT)
        self._exec_spy(monkeypatch)
        result = runner.invoke(main, ["session", "resume", "S1:agent-0"])
        assert result.exit_code != 0
        assert "subagent" in result.output.lower()


class TestResumeIsLiveFirst:
    """Attach to a session that is open; never start a second process on it."""

    def _record(self, tmp_path, monkeypatch, target="main:3.1", **kw):
        from scad.launch import write_record

        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        monkeypatch.setattr("scad.cli.claude_live_sessions", lambda *a, **k: [])
        record = {"agent": "claude", "session_id": "S1", "cwd": "/repo",
                  "tmux": target, "started": "2026-07-30T14:30:00Z",
                  "resume": "cd /repo && claude --resume S1",
                  "provenance": "minted"}
        record.update(kw)
        write_record(record)

    def _panes(self, monkeypatch, *panes):
        from scad.live import TmuxPane

        monkeypatch.setattr("scad.cli.tmux_panes", lambda *a, **k: list(panes))

    def test_a_live_recorded_pane_is_attached_to(self, runner, tmp_path, monkeypatch):
        from scad.live import TmuxPane

        self._record(tmp_path, monkeypatch)
        self._panes(monkeypatch, TmuxPane("main:3.1", "/repo", "2.1.219"))
        calls = []
        monkeypatch.setattr("scad.cli._exec", lambda argv: calls.append(argv))

        result = runner.invoke(main, ["session", "resume", "S1"])

        assert result.exit_code == 0, result.output
        assert calls and calls[0][0] == "tmux"
        assert "main:3.1" in calls[0]
        assert "main:3.1" in result.output

    def test_a_recorded_pane_that_is_gone_falls_back_to_resuming(
            self, runner, tmp_path, monkeypatch):
        self._record(tmp_path, monkeypatch)
        self._panes(monkeypatch)
        calls = []
        monkeypatch.setattr("scad.cli._exec", lambda argv: calls.append(argv))
        monkeypatch.setattr("scad.cli.os.chdir", lambda p: None)

        result = runner.invoke(main, ["session", "resume", "S1"])

        assert result.exit_code == 0, result.output
        assert calls == [["claude", "--resume", "S1"]]

    def test_a_pane_that_now_holds_a_shell_is_not_attached_to(
            self, runner, tmp_path, monkeypatch):
        from scad.live import TmuxPane

        self._record(tmp_path, monkeypatch)
        self._panes(monkeypatch, TmuxPane("main:3.1", "/repo", "zsh"))
        calls = []
        monkeypatch.setattr("scad.cli._exec", lambda argv: calls.append(argv))
        monkeypatch.setattr("scad.cli.os.chdir", lambda p: None)

        runner.invoke(main, ["session", "resume", "S1"])

        assert calls == [["claude", "--resume", "S1"]]

    def test_print_still_prints_the_resume_command_for_a_live_session(
            self, runner, tmp_path, monkeypatch):
        """--print is the viewer's clipboard payload, which is never a tmux
        target — the pane is gone tomorrow and the command is not."""
        from scad.live import TmuxPane

        self._record(tmp_path, monkeypatch)
        self._panes(monkeypatch, TmuxPane("main:3.1", "/repo", "2.1.219"))
        result = runner.invoke(main, ["session", "resume", "S1", "--print"])
        assert result.output.strip() == "cd /repo && claude --resume S1"

    def test_a_session_the_registry_proves_is_running_is_not_started_twice(
            self, runner, tmp_path, monkeypatch):
        """The registry names the session exactly but cannot name its pane, so
        there is nowhere to attach — and resuming anyway would put a second
        process on a live session id."""
        from scad.live import ClaudeSession

        self._record(tmp_path, monkeypatch)
        self._panes(monkeypatch)
        monkeypatch.setattr("scad.cli.claude_live_sessions",
                            lambda *a, **k: [ClaudeSession("S1", 4242, cwd="/repo")])
        calls = []
        monkeypatch.setattr("scad.cli._exec", lambda argv: calls.append(argv))

        result = runner.invoke(main, ["session", "resume", "S1"])

        assert result.exit_code != 0
        assert calls == []
        assert "4242" in result.output

    def test_a_launched_session_resumes_before_it_has_been_indexed(
            self, runner, tmp_path, monkeypatch):
        """Read-back is eventually consistent behind an index pass; getting
        back into the session you just launched must not be."""
        self._record(tmp_path, monkeypatch, agent="codex", cwd="/repo")
        self._panes(monkeypatch)
        result = runner.invoke(main, ["session", "resume", "S1", "--print"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == "cd /repo && codex resume S1"


class TestWhereDiagnostics:
    """`scad where` used to echo one line and discard the evidence.

    An interactive session is indexed whatever launches it, so attribution is
    the only thing that silently goes wrong — and `project` is the retrieval
    join key. A human has to be able to see how the answer was reached.
    """

    def _repo(self, tmp_path):
        import subprocess as sp

        repo = tmp_path / "myproj"
        repo.mkdir()
        sp.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        return repo

    def test_it_names_the_tier_that_answered(self, runner, tmp_path):
        result = runner.invoke(main, ["where", "--start", str(self._repo(tmp_path))])
        assert result.exit_code == 0, result.output
        assert "myproj" in result.output
        assert "marker:.git" in result.output

    def test_it_lists_what_was_tried(self, runner, tmp_path):
        result = runner.invoke(main, ["where", "--start", str(self._repo(tmp_path))])
        assert "marker:scad.yml" in result.output
        assert "marker:.scad-project" in result.output

    def test_unfiled_is_stated_as_a_failure_not_as_an_answer(self, runner, tmp_path):
        loose = tmp_path / "loose"
        loose.mkdir()
        result = runner.invoke(main, ["where", "--start", str(loose)])
        assert result.exit_code == 0, result.output
        assert "unfiled" in result.output
        assert "nothing here marks a project" in result.output

    def test_unfiled_names_the_fix(self, runner, tmp_path):
        loose = tmp_path / "loose"
        loose.mkdir()
        result = runner.invoke(main, ["where", "--start", str(loose)])
        assert ".scad-project" in result.output
        assert str(loose) in result.output

    def test_unfiled_says_a_marker_does_not_refile_the_past(self, runner, tmp_path):
        """`project` is a computed column and the incremental pass is
        mtime-based, so it never recomputes for unchanged sessions."""
        loose = tmp_path / "loose"
        loose.mkdir()
        result = runner.invoke(main, ["where", "--start", str(loose)])
        assert "reindex --rebuild" in result.output

    def test_a_filed_directory_is_not_lectured_about_markers(self, runner, tmp_path):
        result = runner.invoke(main, ["where", "--start", str(self._repo(tmp_path))])
        assert "reindex --rebuild" not in result.output


class TestAttributionSkill:
    """A session is indexed whatever launches it; only its `project` can go
    quietly wrong. The skill is how that gets checked from wherever you are."""

    ROOT = Path(__file__).resolve().parent.parent
    DIR = ROOT / "skills" / "attribution"

    @property
    def text(self):
        return (self.DIR / "SKILL.md").read_text()

    def test_it_ships_as_a_skill_so_every_agent_gets_it(self):
        """Skills install into ~/.agents/skills and ~/.claude/skills alike. A
        Claude-only command would leave codex and kimi filing sessions blind."""
        assert (self.DIR / "SKILL.md").exists()
        assert re.search(r"^name:\s*attribution\s*$", self.text, re.M)

    def test_it_asks_the_cli_rather_than_resolving_in_prose(self):
        """The precedence lives in `project.py`. Restating it here is how the
        two get to disagree about the same directory."""
        assert "scad where" in self.text
        assert "basename of the git" not in self.text.lower()

    def test_it_offers_the_marker_by_name(self):
        assert ".scad-project" in self.text
        assert "scad.yml" in self.text

    def test_it_states_that_a_marker_does_not_refile_the_past(self):
        assert "reindex --rebuild" in self.text

    def test_it_covers_resolving_somewhere_unwanted_not_only_unfiled(self):
        """The worse failure of the two: a wrong project is a wrong join key,
        and it looks like a result."""
        assert "unfiled" in self.text.lower()
        assert re.search(r"wrong project|not the project|somewhere you do not want",
                         self.text, re.I)

    def test_it_does_not_drop_a_marker_without_asking(self):
        assert re.search(r"ask|offer|confirm", self.text, re.I)

    def test_it_carries_no_command_only_syntax(self):
        assert "$ARGUMENTS" not in self.text
        assert "argument-hint" not in self.text

    def test_it_has_no_path_that_breaks_once_installed(self):
        assert "../" not in self.text


class TestSessionLaunch:
    """`scad session launch` — hand work to an agent, and be able to find the
    conversation afterwards.

    The launcher itself is exercised against a stub in test_launch.py. What is
    checked here is the command around it: what it says, what it refuses, and
    that it never leaves the human without the two strings that matter.
    """

    RECORD = {"agent": "codex", "session_id": "CX1", "cwd": "/repo",
              "tmux": "scad-cx-1430:0.0", "started": "2026-07-30T14:30:00Z",
              "resume": "cd /repo && codex resume CX1", "provenance": "tui-native"}

    def _fake(self, monkeypatch, record=None, **overrides):
        calls = []

        def fake_launch(agent, cwd, **kw):
            calls.append({"agent": agent, "cwd": str(cwd), **kw})
            return {**(record or self.RECORD), **overrides}

        monkeypatch.setattr("scad.cli.launch_agent", fake_launch)
        monkeypatch.setattr("scad.cli._exec", lambda argv: calls.append(argv))
        return calls

    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))

    def test_json_emits_the_record_and_nothing_to_parse(
            self, runner, tmp_path, monkeypatch):
        """The id is a contract. A consumer must not regex the human lines for
        it, or a cosmetic change to an echo becomes a silent break elsewhere."""
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch)
        # The WHOLE of stdout must parse. Taking the last line would pass
        # while a preamble sat in front of the record -- which is exactly what
        # shipped, and what this assertion is written to catch.
        r = CliRunner().invoke(
            main, ["session", "launch", "--agent", "codex",
                   "--cwd", str(tmp_path), "--json"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.stdout)
        assert payload["session_id"] == "CX1"
        assert payload["resume"] == "cd /repo && codex resume CX1"

    def test_json_keeps_the_human_lines_off_stdout(
            self, runner, tmp_path, monkeypatch):
        """An unfiled cwd prints a warning and a fix. Neither may land on the
        data channel: stdout is the contract, stderr is for people."""
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch)
        unfiled = tmp_path / "no-marker-here"
        unfiled.mkdir()
        r = CliRunner().invoke(
            main, ["session", "launch", "--agent", "codex",
                   "--cwd", str(unfiled), "--json"])
        assert json.loads(r.stdout)["session_id"] == "CX1"
        assert "[scad]" not in r.stdout

    def test_json_still_fails_loudly_when_no_id_was_resolved(
            self, runner, tmp_path, monkeypatch):
        # A caller reading session_id off stdout must not also have to decide
        # what a missing one means -- the exit status has to say it.
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch, session_id=None, problem="no rollout appeared")
        result = runner.invoke(main, ["session", "launch", "--agent", "codex",
                                      "--cwd", str(tmp_path), "--json"])
        assert result.exit_code != 0

    def test_launching_puts_the_session_in_the_index_immediately(
            self, runner, tmp_path, monkeypatch):
        """`session show` used to deny a session scad had just started."""
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch)
        runner.invoke(main, ["session", "launch", "--agent", "codex",
                             "--cwd", str(tmp_path)])
        shown = runner.invoke(main, ["session", "show", "CX1"])
        assert shown.exit_code == 0, shown.output
        assert "codex" in shown.output

    def test_it_hands_back_the_resume_command(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch)
        result = runner.invoke(main, ["session", "launch", "--agent", "codex",
                                      "--cwd", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "cd /repo && codex resume CX1" in result.output

    def test_it_names_the_pane_it_launched_into(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch)
        result = runner.invoke(main, ["session", "launch", "--agent", "codex",
                                      "--cwd", str(tmp_path)])
        assert "scad-cx-1430:0.0" in result.output

    def test_it_says_how_the_session_was_born(self, runner, tmp_path, monkeypatch):
        """Provenance predicts whether the agent's own picker will show it, and
        a human reading this should not have to re-derive that."""
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch)
        result = runner.invoke(main, ["session", "launch", "--agent", "codex",
                                      "--cwd", str(tmp_path)])
        assert "tui-native" in result.output

    def test_it_is_detached_by_default(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        calls = self._fake(monkeypatch)
        runner.invoke(main, ["session", "launch", "--agent", "codex",
                             "--cwd", str(tmp_path)])
        assert not any(isinstance(c, list) for c in calls)

    def test_attach_takes_you_into_the_pane(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        calls = self._fake(monkeypatch)
        runner.invoke(main, ["session", "launch", "--agent", "codex",
                             "--cwd", str(tmp_path), "--attach"])
        argv = [c for c in calls if isinstance(c, list)]
        assert argv and argv[0][0] == "tmux"
        assert "scad-cx-1430:0.0" in argv[0]

    def test_the_prompt_is_passed_through(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        calls = self._fake(monkeypatch)
        runner.invoke(main, ["session", "launch", "--agent", "codex",
                             "--cwd", str(tmp_path), "--prompt", "port the parser"])
        assert calls[0]["prompt"] == "port the parser"

    def test_the_cwd_defaults_to_where_you_are(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        calls = self._fake(monkeypatch)
        with runner.isolated_filesystem(temp_dir=tmp_path) as here:
            runner.invoke(main, ["session", "launch", "--agent", "kimi"])
            assert Path(calls[0]["cwd"]).resolve() == Path(here).resolve()

    def test_only_the_three_families_are_accepted(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch)
        result = runner.invoke(main, ["session", "launch", "--agent", "pi",
                                      "--cwd", str(tmp_path)])
        assert result.exit_code != 0

    def test_a_refused_launch_says_why(self, runner, tmp_path, monkeypatch):
        from scad.launch import LaunchError

        self._home(tmp_path, monkeypatch)

        def boom(*a, **k):
            raise LaunchError("tmux is required and is not usable here.")

        monkeypatch.setattr("scad.cli.launch_agent", boom)
        result = runner.invoke(main, ["session", "launch", "--agent", "claude",
                                      "--cwd", str(tmp_path)])
        assert result.exit_code != 0
        assert "tmux" in result.output

    def test_an_unresolved_id_is_not_reported_as_success(self, runner, tmp_path,
                                                         monkeypatch):
        """The pane is live and the session is real — only its id is unknown.
        Both halves have to be said, and the exit code cannot claim a findable
        session was produced."""
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch, session_id=None, provenance="unresolved",
                   resume="", problem="kimi wrote no new index line")
        result = runner.invoke(main, ["session", "launch", "--agent", "kimi",
                                      "--cwd", str(tmp_path)])
        assert result.exit_code != 0
        assert "kimi wrote no new index line" in result.output
        assert "scad-cx-1430:0.0" in result.output          # the pane is still yours

    def test_ambiguous_candidates_are_both_shown(self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        self._fake(monkeypatch, session_id=None, provenance="unresolved", resume="",
                   problem="two new index lines", candidates=["aaa", "bbb"])
        result = runner.invoke(main, ["session", "launch", "--agent", "kimi",
                                      "--cwd", str(tmp_path)])
        assert "aaa" in result.output and "bbb" in result.output


class TestLaunchChecksAttribution:
    """`project` is the retrieval join key, and a session launched into an
    unfiled directory is one you will not find by project later."""

    def _fake(self, monkeypatch):
        monkeypatch.setattr("scad.cli.launch_agent",
                            lambda agent, cwd, **kw: dict(TestSessionLaunch.RECORD))
        monkeypatch.setattr("scad.cli._exec", lambda argv: None)

    def test_an_unfiled_target_is_warned_about(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._fake(monkeypatch)
        loose = tmp_path / "loose"
        loose.mkdir()
        result = runner.invoke(main, ["session", "launch", "--agent", "kimi",
                                      "--cwd", str(loose)])
        assert "unfiled" in result.output
        assert ".scad-project" in result.output

    def test_the_warning_does_not_block_the_launch(self, runner, tmp_path, monkeypatch):
        """The session is still valid; it will just be hard to find later."""
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._fake(monkeypatch)
        loose = tmp_path / "loose"
        loose.mkdir()
        result = runner.invoke(main, ["session", "launch", "--agent", "kimi",
                                      "--cwd", str(loose)])
        assert result.exit_code == 0, result.output
        assert "codex resume CX1" in result.output

    def test_a_filed_target_is_not_warned_about(self, runner, tmp_path, monkeypatch):
        import subprocess as sp

        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        self._fake(monkeypatch)
        repo = tmp_path / "myproj"
        repo.mkdir()
        sp.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        result = runner.invoke(main, ["session", "launch", "--agent", "kimi",
                                      "--cwd", str(repo)])
        assert "unfiled" not in result.output
        assert "myproj" in result.output


class TestTimestampFormatting:
    """The columns are epoch MILLISECONDS and nothing in the schema says so."""

    def test_session_span_is_rendered_from_milliseconds(self):
        from scad.cli import _fmt_ms, _fmt_span
        start = 1785676230299          # a real value out of the index
        end = start + (90 * 60 * 1000)  # ninety minutes later
        assert _fmt_ms(start).startswith("20")   # a year, not 1970
        assert _fmt_span(start, end) == "1h 30m"

    def test_missing_or_reversed_ends_render_as_nothing(self):
        from scad.cli import _fmt_ms, _fmt_span
        assert _fmt_ms(None) is None
        assert _fmt_span(None, 5) is None
        assert _fmt_span(1785676230299, 1785676230000) is None

    def test_multi_day_span_does_not_report_hundreds_of_hours(self):
        from scad.cli import _fmt_span
        start = 1785676230299
        assert _fmt_span(start, start + (50 * 3600 * 1000)) == "2d 2h"

    def test_note_stamp_carries_how_long_ago(self):
        from datetime import datetime, timedelta
        from scad.cli import _fmt_ago
        recent = (datetime.now().astimezone() - timedelta(hours=3)).isoformat()
        assert "3h ago" in _fmt_ago(recent)

    def test_unparseable_note_stamp_is_passed_through(self):
        from scad.cli import _fmt_ago
        assert _fmt_ago("not-a-date") == "not-a-date"
        assert _fmt_ago(None) == "?"


class TestNoteWriteIndexesImmediately:
    """A note nothing can find is a note that was not really captured."""

    NOTE = {"topic": "t", "title": "first", "tags": ["a"]}

    def test_a_written_note_is_listable_without_a_reindex(
            self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        result = runner.invoke(main, ["notes", "ls"])
        assert result.exit_code == 0
        assert "S1" in result.output

    def test_write_then_reindex_does_not_duplicate(self, runner, tmp_path, monkeypatch):
        # Indexing at write must ALSO advance notes_offset -- otherwise the next
        # pass re-appends the same record under a fresh idx, a duplicate that
        # reads as a real second note.
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        runner.invoke(main, ["reindex", "--no-archive"])
        result = runner.invoke(main, ["notes", "ls", "--json"])
        rows = json.loads(result.output)
        assert len([r for r in rows if r["session_id"] == "S1"]) == 1


class TestNoteReadFindsTheRightShard:
    """The listing does not make you type the agent; reading must not either."""

    NOTE = {"topic": "t", "title": "from another agent", "tags": ["a"]}

    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))

    def test_read_falls_back_to_the_shard_that_has_it(
            self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "K1", "--agent", "kimi"],
                      input=json.dumps(self.NOTE))
        # No --agent: the default shard is claude and the note is under kimi.
        result = runner.invoke(main, ["notes", "read", "K1"])
        assert result.exit_code == 0
        assert "from another agent" in result.output

    def test_named_agent_still_wins_when_it_has_the_file(
            self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "D1", "--agent", "kimi"],
                      input=json.dumps({**self.NOTE, "title": "kimi one"}))
        runner.invoke(main, ["session", "note", "--session", "D1", "--agent", "claude"],
                      input=json.dumps({**self.NOTE, "title": "claude one"}))
        result = runner.invoke(main, ["notes", "read", "D1", "--agent", "kimi"])
        assert "kimi one" in result.output
        assert "claude one" not in result.output

    def test_listing_shows_which_agent_wrote_each_note(
            self, runner, tmp_path, monkeypatch):
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "K2", "--agent", "kimi"],
                      input=json.dumps(self.NOTE))
        result = runner.invoke(main, ["notes", "ls"])
        assert "kimi" in result.output


class TestLaunchRecordsTheSessionItStarted:
    """Record the job when you start it — the index should not need a sweep."""

    def test_a_launched_session_is_in_the_index_without_a_reindex(self, tmp_path):
        from scad.index import connect, ensure_launched_session, session_row
        conn = connect(tmp_path / "i.sqlite")
        assert ensure_launched_session(conn, "L1", "codex", "/repo") is True
        row = session_row(conn, "L1")
        assert row is not None
        assert row["agent"] == "codex"
        assert row["grade"] == "skeleton"      # no turns exist yet, and that is honest

    def test_the_archive_pass_upgrades_that_row_rather_than_adding_one(self, tmp_path):
        # The whole point: sessions.id is the primary key and the pass upserts
        # ON CONFLICT(id), so a launch-seeded row and the later parse are one row.
        from scad.index import connect, ensure_launched_session, session_row, upsert_session
        from scad.records import SessionRecord, GRADE_FULL
        conn = connect(tmp_path / "i.sqlite")
        ensure_launched_session(conn, "L1", "codex", "/repo")
        upsert_session(
            conn,
            SessionRecord(id="L1", kind="main", agent="codex", source="codex-rollout",
                          cwd="/repo", grade=GRADE_FULL),
            machine="m", project="proj", archive_path="/arc/L1.jsonl",
            source_size=10, source_mtime=1, parsed_offset=10,
        )
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        row = session_row(conn, "L1")
        assert row["grade"] == "full"

    def test_it_never_overwrites_a_row_a_transcript_already_filled(self, tmp_path):
        from scad.index import connect, ensure_launched_session, session_row, upsert_session
        from scad.records import SessionRecord, GRADE_FULL
        conn = connect(tmp_path / "i.sqlite")
        upsert_session(
            conn,
            SessionRecord(id="L1", kind="main", agent="codex", source="codex-rollout",
                          cwd="/real", grade=GRADE_FULL),
            machine="m", project="proj", archive_path="/arc/L1.jsonl",
            source_size=10, source_mtime=1, parsed_offset=10,
        )
        assert ensure_launched_session(conn, "L1", "codex", "/elsewhere") is False
        assert session_row(conn, "L1")["cwd"] == "/real"
