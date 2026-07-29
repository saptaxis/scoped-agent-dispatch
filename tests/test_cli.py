"""CLI tests."""

import json
import os

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
        result = runner.invoke(main, ["reindex"])
        assert result.exit_code == 0
        assert "sessions" in result.output

    def test_session_ls_lists_the_row(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "ls"])
        assert result.exit_code == 0
        assert "S1" in result.output

    def test_session_show_includes_turn_count(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "show", "S1"])
        assert result.exit_code == 0
        assert "turns" in result.output.lower()

    def test_session_show_unknown_id_exits_nonzero(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "show", "NOPE"])
        assert result.exit_code != 0

    def test_project_ls_groups(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["project", "ls"])
        assert result.exit_code == 0

    def test_session_ls_json_is_parseable(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "ls", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload[0]["id"] == "S1"

    def test_since_and_until_bound_the_window(self, runner, tmp_path, monkeypatch):
        """'What was I doing in March' is the query this exists for."""
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])

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
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "ls", "--outcome", "awaiting-user", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)[0]["id"] == "S1"

    def test_tool_result_last_is_a_filterable_outcome(self, runner, tmp_path, monkeypatch):
        """The majority outcome on a real index — unfilterable is unusable."""
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
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
        runner.invoke(main, ["reindex"])

        result = runner.invoke(main, ["session", "ls"])
        assert result.exit_code == 0
        assert "nd-5" in result.output

        shown = runner.invoke(main, ["session", "show", "S1"])
        assert "nd-5" in shown.output
        assert "blocked" in shown.output
        assert "drop the bioRxiv PDF" in shown.output

    def test_session_ls_falls_back_to_the_id_when_unnamed(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "ls"])
        assert "S1" in result.output

    def test_grade_filter_separates_skeletons(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
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
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "read", "S1"])
        assert result.exit_code == 0
        assert "the resolver returns a directory" in result.output

    def test_read_can_exclude_reasoning(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "read", "S1", "--kind", "text"])
        assert "weighing markers" not in result.output
        assert "the resolver returns a directory" in result.output

    def test_read_unknown_session_exits_nonzero(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        assert runner.invoke(main, ["session", "read", "NOPE"]).exit_code != 0

    def test_search_finds_the_session(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["search", "resolver"])
        assert result.exit_code == 0
        assert "S1" in result.output

    def test_search_with_no_hits_says_so(self, runner, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        runner.invoke(main, ["reindex"])
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
    TRACE_VERBS = ("ls", "show", "read", "note", "notes")

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
        runner.invoke(main, ["reindex"])
        result = runner.invoke(main, ["session", "show", "S1"])
        assert result.exit_code == 0
        assert "notes" in result.output

    def test_reindex_reports_the_note_count(self, runner, tmp_path, monkeypatch):
        # Drift must be visible: a pass that indexed a note and said nothing
        # would leave "did /remember work?" answerable only by opening sqlite.
        self._home(tmp_path, monkeypatch)
        runner.invoke(main, ["session", "note", "--session", "S1"],
                      input=json.dumps(self.NOTE))
        result = runner.invoke(main, ["reindex"])
        assert "notes: 1" in result.output


class TestRememberCommandIsAThinCaller:
    """`/remember` produces the record; `scad session note` decides where it goes.

    The split is the point. The old command reimplemented project resolution in
    prose — basename of the git root, fall back to the cwd — which meant every
    other agent that wanted to capture had to reimplement it again, and a change
    to what a project means would have had to be made in two languages.
    """

    @property
    def text(self):
        return (Path(__file__).resolve().parent.parent / "commands" / "remember.md").read_text()

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
