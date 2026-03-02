"""Tests for composite workflow commands — dispatch, harvest, finish."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from click.testing import CliRunner
import pytest

from scad.cli import main


class TestDispatch:
    """Tests for scad dispatch — start + inject composite."""

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_headless_wait_default(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """dispatch defaults to headless + wait."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-test-Mar03-1200"
        mock_inject.return_value = ("demo-test-Mar03-1200-job-001", 0)

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test", "--prompt", "Do the thing",
        ])
        assert result.exit_code == 0
        mock_inject.assert_called_once()
        _, kwargs = mock_inject.call_args
        assert kwargs["wait"] is True
        assert kwargs["headless"] is True

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_no_wait(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """dispatch --no-wait dispatches without blocking."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-test-Mar03-1200"
        mock_inject.return_value = "demo-test-Mar03-1200-job-001"

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test", "--no-wait",
            "--prompt", "Do the thing",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_inject.call_args
        assert kwargs.get("wait") is not True

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_fetch_implies_wait(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """dispatch --fetch forces --wait."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-test-Mar03-1200"
        mock_inject.return_value = ("demo-test-Mar03-1200-job-001", 0)

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test", "--fetch",
            "--prompt", "Do the thing",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_inject.call_args
        assert kwargs["wait"] is True

    def test_dispatch_fetch_no_wait_errors(self):
        """dispatch --fetch --no-wait is an error."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test",
            "--fetch", "--no-wait",
            "--prompt", "Do the thing",
        ])
        assert result.exit_code != 0

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=False)
    @patch("scad.cli.build_image")
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_builds_if_no_image(
        self, mock_load, mock_inject, mock_run_agent, mock_build, mock_img, mock_auth
    ):
        """dispatch builds image if not already built."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_build.return_value = iter(["Step 1/5"])
        mock_run_agent.return_value = "demo-test-Mar03-1200"
        mock_inject.return_value = ("demo-test-Mar03-1200-job-001", 0)

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test", "--prompt", "Task",
        ])
        assert result.exit_code == 0
        mock_build.assert_called_once()


class TestHarvest:
    """Tests for scad harvest — fetch + diff composite."""

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli.diff_from_source")
    @patch("scad.cli._config_for_run")
    def test_harvest_fetches_and_diffs(
        self, mock_config, mock_diff, mock_fetch, mock_validate
    ):
        """harvest runs fetch then diff."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_config.return_value = config
        mock_fetch.return_value = [{"repo": "code", "branch": "scad-test", "source": "/tmp/code"}]
        mock_diff.return_value = {"code": "+new line"}

        runner = CliRunner()
        result = runner.invoke(main, ["harvest", "test-run"])
        assert result.exit_code == 0
        mock_fetch.assert_called_once()
        mock_diff.assert_called_once()
        assert "Fetched" in result.output
        assert "+new line" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli.diff_from_source")
    @patch("scad.cli._config_for_run")
    def test_harvest_no_changes(
        self, mock_config, mock_diff, mock_fetch, mock_validate
    ):
        """harvest with no changes shows message."""
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_fetch.return_value = []
        mock_diff.return_value = {}

        runner = CliRunner()
        result = runner.invoke(main, ["harvest", "test-run"])
        assert result.exit_code == 0
