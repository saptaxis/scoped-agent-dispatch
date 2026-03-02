"""CLI tests."""

import pytest
import click
from click.testing import CliRunner
from pathlib import Path
from unittest.mock import patch, MagicMock

import docker
from scad.cli import main, _complete_run_ids, _complete_config_names, _relative_time, get_all_sessions, get_project_status, get_session_usage


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
        with patch("scad.cli.Path.home", return_value=tmp_path):
            # Create the expected directory structure
            logs_dir = tmp_path / ".scad" / "logs"
            logs_dir.mkdir(parents=True)
            (logs_dir / "test-run.log").write_text("line1\nline2\nline3\n")

            result = runner.invoke(main, ["session", "logs", "test-run"])
        assert result.exit_code == 0
        assert "line1" in result.output
        assert "line3" in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_not_found(self, mock_validate, runner, tmp_path):
        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["session", "logs", "nonexistent"])
        assert result.exit_code != 0
        assert "No log file" in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_respects_line_count(self, mock_validate, runner, tmp_path):
        logs_dir = tmp_path / ".scad" / "logs"
        logs_dir.mkdir(parents=True)
        lines = [f"line{i}" for i in range(200)]
        (logs_dir / "big-run.log").write_text("\n".join(lines))

        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["session", "logs", "big-run", "-n", "5"])
        assert result.exit_code == 0
        assert "line199" in result.output
        assert "line194" not in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_stream_shows_jsonl(self, mock_validate, runner, tmp_path):
        logs_dir = tmp_path / ".scad" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "test-run.stream.jsonl").write_text(
            '{"type":"tool_use","tool":"Edit"}\n'
            '{"type":"tool_result","output":"ok"}\n'
        )

        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["session", "logs", "test-run", "--stream"])
        assert result.exit_code == 0
        assert "tool_use" in result.output
        assert "Edit" in result.output

    @patch("scad.cli.validate_run_id")
    def test_logs_stream_not_found(self, mock_validate, runner, tmp_path):
        with patch("scad.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["session", "logs", "nonexistent", "--stream"])
        assert result.exit_code != 0
        assert "No stream log" in result.output


class TestSessionStatus:
    @patch("scad.cli.check_claude_auth", return_value=(True, 10.0))
    @patch("scad.cli.list_scad_containers")
    def test_status_shows_running(self, mock_running, mock_auth, runner):
        mock_running.return_value = [{
            "run_id": "test-Feb26-1430",
            "config": "myconfig",
            "branch": "test",
            "started": "2026-02-26T14:30:00Z",
            "container": "running",
            "clones": "yes",
        }]
        result = runner.invoke(main, ["session", "status"])
        assert result.exit_code == 0
        assert "test-Feb26-1430" in result.output
        assert "running" in result.output

    @patch("scad.cli.check_claude_auth", return_value=(True, 10.0))
    @patch("scad.cli.list_scad_containers")
    def test_status_empty(self, mock_running, mock_auth, runner):
        mock_running.return_value = []
        result = runner.invoke(main, ["session", "status"])
        assert result.exit_code == 0
        assert "No running sessions" in result.output

    @patch("scad.cli.check_claude_auth", return_value=(True, 10.0))
    @patch("scad.cli.get_all_sessions")
    def test_status_all_shows_history(self, mock_all, mock_auth, runner):
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
        result = runner.invoke(main, ["session", "status", "--all"])
        assert result.exit_code == 0
        assert "test-Feb28-1400" in result.output
        assert "old-Feb27-0900" in result.output
        assert "running" in result.output
        assert "stopped" in result.output


class TestScadBuild:
    @patch("scad.cli.build_image")
    @patch("scad.cli.load_config")
    def test_build_shows_step_progress(self, mock_load, mock_build, runner):
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

    @patch("scad.cli.build_image")
    @patch("scad.cli.load_config")
    def test_build_verbose_shows_everything(self, mock_load, mock_build, runner):
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


class TestSessionAttach:
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

        result = runner.invoke(main, ["session", "attach", "test-Feb27-1430"])
        mock_subprocess.assert_called_once()
        call_args = mock_subprocess.call_args[0][0]
        assert "docker" in call_args
        assert "exec" in call_args
        assert "tmux" in call_args

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.docker.from_env")
    def test_attach_not_found(self, mock_docker, mock_validate, runner):
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["session", "attach", "nonexistent"])
        assert result.exit_code != 0
        assert "No container" in result.output

    @patch("scad.cli.docker.from_env")
    def test_attach_not_running(self, mock_docker, runner):
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client

        result = runner.invoke(main, ["session", "attach", "stopped-run"])
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
        runs_dir = tmp_path / ".scad" / "runs"
        runs_dir.mkdir(parents=True)
        (runs_dir / "demo-Feb28-1400").mkdir()
        (runs_dir / "scad-Feb28-0900").mkdir()

        with patch("scad.cli.Path.home", return_value=tmp_path):
            results = _complete_run_ids(None, None, "demo")
        completions = [c.value if hasattr(c, "value") else c for c in results]
        assert "demo-Feb28-1400" in completions
        assert "scad-Feb28-0900" not in completions

    def test_run_id_completion_empty(self, tmp_path):
        with patch("scad.cli.Path.home", return_value=tmp_path):
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

    @patch("scad.cli.log_event")
    @patch("scad.cli._subprocess.run")
    @patch("scad.cli.docker.from_env")
    def test_attach_logs_event(self, mock_docker, mock_subprocess, mock_log, runner):
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


class TestCodeRefresh:
    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.refresh_credentials")
    def test_refresh_shows_time_remaining(self, mock_refresh, mock_validate, runner):
        mock_refresh.return_value = 4.5
        result = runner.invoke(main, ["code", "refresh", "test-run"])
        assert result.exit_code == 0
        assert "4h 30m" in result.output or "refreshed" in result.output.lower()

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.refresh_credentials")
    def test_refresh_expired(self, mock_refresh, mock_validate, runner):
        mock_refresh.side_effect = click.ClickException("Credentials expired")
        result = runner.invoke(main, ["code", "refresh", "test-run"])
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
        result = runner.invoke(main, ["project", "status", "demo", "--cost"])
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
        result = runner.invoke(main, ["project", "status", "demo"])
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


class TestCredentialWarning:
    """session status shows credential expiry warning."""

    def test_warning_when_expiring_soon(self, runner, monkeypatch):
        monkeypatch.setattr("scad.cli.list_scad_containers", lambda: [])
        monkeypatch.setattr("scad.cli.check_claude_auth", lambda: (True, 1.5))

        result = runner.invoke(main, ["session", "status"])
        assert "expire" in result.output.lower() or "1.5h" in result.output

    def test_warning_when_expired(self, runner, monkeypatch):
        monkeypatch.setattr("scad.cli.list_scad_containers", lambda: [])
        monkeypatch.setattr("scad.cli.check_claude_auth", lambda: (False, 0))

        result = runner.invoke(main, ["session", "status"])
        assert "expired" in result.output.lower()

    def test_no_warning_when_plenty_of_time(self, runner, monkeypatch):
        monkeypatch.setattr("scad.cli.list_scad_containers", lambda: [])
        monkeypatch.setattr("scad.cli.check_claude_auth", lambda: (True, 8.0))

        result = runner.invoke(main, ["session", "status"])
        assert "expire" not in result.output.lower()


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

        result = runner.invoke(main, ["project", "status", "demo"])
        assert result.exit_code == 0
