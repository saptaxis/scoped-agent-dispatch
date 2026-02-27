"""CLI tests."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, MagicMock

import docker
from scad.cli import main, _complete_run_ids, _complete_config_names, _relative_time


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


class TestScadConfigs:
    @patch("scad.cli.get_image_info")
    @patch("scad.cli.list_configs")
    def test_configs_shows_table(self, mock_list, mock_info, runner):
        mock_list.return_value = ["alpha", "beta"]
        mock_info.side_effect = [
            {"tag": "scad-alpha", "created": "2026-02-26T10:00:00Z"},
            None,
        ]
        result = runner.invoke(main, ["configs"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "never" in result.output

    @patch("scad.cli.list_configs")
    def test_configs_empty(self, mock_list, runner):
        mock_list.return_value = []
        result = runner.invoke(main, ["configs"])
        assert result.exit_code == 0
        assert "No configs" in result.output


class TestScadStop:
    @patch("scad.cli.stop_container")
    def test_stop_running(self, mock_stop, runner):
        mock_stop.return_value = True
        result = runner.invoke(main, ["stop", "test-Feb26-1430"])
        assert result.exit_code == 0
        assert "Stopped" in result.output

    @patch("scad.cli.stop_container")
    def test_stop_not_found(self, mock_stop, runner):
        mock_stop.return_value = False
        result = runner.invoke(main, ["stop", "nonexistent"])
        assert result.exit_code != 0
        assert "No running container" in result.output


class TestScadLogs:
    def test_logs_shows_file_content(self, runner, tmp_path):
        with patch("scad.cli.Path.home", return_value=tmp_path):
            # Create the expected directory structure
            logs_dir = tmp_path / ".scad" / "logs"
            logs_dir.mkdir(parents=True)
            (logs_dir / "test-run.log").write_text("line1\nline2\nline3\n")

            result = runner.invoke(main, ["logs", "test-run"])
        assert result.exit_code == 0
        assert "line1" in result.output
        assert "line3" in result.output

    def test_logs_not_found(self, runner, tmp_path):
        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["logs", "nonexistent"])
        assert result.exit_code != 0
        assert "No log file" in result.output

    def test_logs_respects_line_count(self, runner, tmp_path):
        logs_dir = tmp_path / ".scad" / "logs"
        logs_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(200)]
        (logs_dir / "big-run.log").write_text("\n".join(lines))

        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["logs", "big-run", "-n", "5"])
        assert result.exit_code == 0
        assert "line199" in result.output
        assert "line194" not in result.output

    def test_logs_stream_shows_jsonl(self, runner, tmp_path):
        logs_dir = tmp_path / ".scad" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "test-run.stream.jsonl").write_text(
            '{"type":"tool_use","tool":"Edit"}\n'
            '{"type":"tool_result","output":"ok"}\n'
        )

        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["logs", "test-run", "--stream"])
        assert result.exit_code == 0
        assert "tool_use" in result.output
        assert "Edit" in result.output

    def test_logs_stream_not_found(self, runner, tmp_path):
        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["logs", "nonexistent", "--stream"])
        assert result.exit_code != 0
        assert "No stream log" in result.output


class TestScadStatus:
    @patch("scad.cli.list_completed_runs")
    @patch("scad.cli.list_scad_containers")
    def test_status_shows_running_and_completed(self, mock_running, mock_completed, runner):
        mock_running.return_value = [{
            "run_id": "test-Feb26-1430",
            "config": "myconfig",
            "branch": "test",
            "started": "2026-02-26T14:30:00Z",
            "status": "running",
        }]
        mock_completed.return_value = [{
            "run_id": "old-Feb25-0900",
            "config": "myconfig",
            "branch": "old",
            "started": "2026-02-25T09:00:00Z",
            "status": "exited(0)",
        }]
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "test-Feb26-1430" in result.output
        assert "old-Feb25-0900" in result.output

    @patch("scad.cli.list_completed_runs")
    @patch("scad.cli.list_scad_containers")
    def test_status_empty(self, mock_running, mock_completed, runner):
        mock_running.return_value = []
        mock_completed.return_value = []
        result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "No agents" in result.output


class TestScadBuild:
    @patch("scad.cli.build_image")
    @patch("scad.cli.load_config")
    def test_build_calls_build_image(self, mock_load, mock_build, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_build.return_value = iter(["Step 1/5 : FROM python:3.11-slim"])

        result = runner.invoke(main, ["build", "test"])
        assert result.exit_code == 0
        assert "Image built" in result.output
        mock_build.assert_called_once()

    @patch("scad.cli.build_image")
    @patch("scad.cli.load_config")
    def test_build_streams_output(self, mock_load, mock_build, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_build.return_value = iter([
            "Step 1/5 : FROM python:3.11-slim",
            "Step 2/5 : RUN apt-get update",
        ])

        result = runner.invoke(main, ["build", "test"])
        assert result.exit_code == 0
        assert "Step 1/5" in result.output
        assert "Step 2/5" in result.output

    @patch("scad.cli.load_config")
    def test_build_config_not_found(self, mock_load, runner):
        mock_load.side_effect = FileNotFoundError("Config 'bad' not found")
        result = runner.invoke(main, ["build", "bad"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestScadRun:
    def test_run_requires_config(self, runner):
        result = runner.invoke(main, ["run"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "config" in result.output.lower()

    @patch("scad.cli.load_config")
    def test_run_config_not_found(self, mock_load, runner):
        mock_load.side_effect = FileNotFoundError("Config 'bad' not found")
        result = runner.invoke(main, ["run", "bad"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @patch("scad.cli.run_agent")
    @patch("scad.cli.resolve_branch")
    @patch("scad.cli.load_config")
    def test_run_dispatches_headless(self, mock_load, mock_resolve, mock_run, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_resolve.return_value = "plan-22"
        mock_run.return_value = "test-Feb27-1430"

        result = runner.invoke(
            main, ["run", "test", "--branch", "plan-22", "--prompt", "do stuff"]
        )
        assert result.exit_code == 0
        mock_run.assert_called_once()
        assert mock_run.call_args[1]["branch"] == "plan-22"
        assert mock_run.call_args[1]["prompt"] == "do stuff"

    @patch("scad.cli.run_agent")
    @patch("scad.cli.resolve_branch")
    @patch("scad.cli.load_config")
    def test_run_auto_generates_branch(self, mock_load, mock_resolve, mock_run, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_resolve.return_value = "scad-Feb27-1430"
        mock_run.return_value = "test-Feb27-1430"

        result = runner.invoke(main, ["run", "test"])
        assert result.exit_code == 0
        mock_resolve.assert_called_once_with(mock_config, None)


class TestScadAttach:
    @patch("scad.cli._subprocess.run")
    @patch("scad.cli.docker.from_env")
    def test_attach_runs_docker_exec(self, mock_docker, mock_subprocess, runner):
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = MagicMock(exit_code=0)
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client
        mock_subprocess.return_value = MagicMock(returncode=0)

        result = runner.invoke(main, ["attach", "test-Feb27-1430"])
        mock_subprocess.assert_called_once()
        call_args = mock_subprocess.call_args[0][0]
        assert "docker" in call_args
        assert "exec" in call_args
        assert "tmux" in call_args

    @patch("scad.cli.docker.from_env")
    def test_attach_not_found(self, mock_docker, runner):
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["attach", "nonexistent"])
        assert result.exit_code != 0
        assert "No container" in result.output

    @patch("scad.cli.docker.from_env")
    def test_attach_not_running(self, mock_docker, runner):
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["attach", "stopped-run"])
        assert result.exit_code != 0
        assert "not running" in result.output.lower()

    @patch("scad.cli.docker.from_env")
    def test_attach_headless_no_tmux(self, mock_docker, runner):
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = MagicMock(exit_code=1)  # no tmux session
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["attach", "headless-run"])
        assert result.exit_code != 0
        assert "headless" in result.output.lower()


class TestShellCompletion:
    @patch("scad.cli.docker.from_env")
    def test_run_id_completion_from_status_files(self, mock_docker, tmp_path):
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_docker.return_value = mock_client

        logs_dir = tmp_path / ".scad" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "plan-22-Feb26-1430.status.json").write_text("{}")
        (logs_dir / "plan-23-Feb26-1500.status.json").write_text("{}")

        with patch("scad.cli.Path.home", return_value=tmp_path):
            results = _complete_run_ids(None, None, "plan-22")
        completions = [c.value if hasattr(c, "value") else c for c in results]
        assert "plan-22-Feb26-1430" in completions
        assert "plan-23-Feb26-1500" not in completions

    def test_config_name_completion(self, tmp_path):
        with patch("scad.cli.list_configs", return_value=["alpha", "beta"]):
            results = _complete_config_names(None, None, "al")
        completions = [c.value if hasattr(c, "value") else c for c in results]
        assert "alpha" in completions
        assert "beta" not in completions
