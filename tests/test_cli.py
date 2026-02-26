"""CLI tests."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, MagicMock

from scad.cli import main


@pytest.fixture
def runner():
    return CliRunner()


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
