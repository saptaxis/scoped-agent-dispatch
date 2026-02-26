"""CLI tests."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, MagicMock

from scad.cli import main


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


@pytest.fixture
def runner():
    return CliRunner()


class TestScadBuild:
    @patch("scad.cli.build_image")
    @patch("scad.cli.load_config")
    def test_build_calls_build_image(self, mock_load, mock_build, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_build.return_value = "scad-test"

        result = runner.invoke(main, ["build", "test"])
        assert result.exit_code == 0
        assert "Image built" in result.output
        mock_build.assert_called_once()

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

    def test_run_requires_branch(self, runner):
        result = runner.invoke(main, ["run", "myconfig"])
        assert result.exit_code != 0
        assert "branch" in result.output.lower()

    @patch("scad.cli.load_config")
    def test_run_config_not_found(self, mock_load, runner):
        mock_load.side_effect = FileNotFoundError("Config 'bad' not found")
        result = runner.invoke(main, ["run", "bad", "--branch", "test"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @patch("scad.cli.run_agent")
    @patch("scad.cli.load_config")
    def test_run_dispatches(self, mock_load, mock_run, runner):
        mock_config = MagicMock()
        mock_config.name = "test"
        mock_load.return_value = mock_config
        mock_run.return_value = "run-id-123"

        result = runner.invoke(
            main, ["run", "test", "--branch", "plan-01", "--prompt", "do stuff"]
        )
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[1]["branch"] == "plan-01"
        assert call_kwargs[1]["prompt"] == "do stuff"
