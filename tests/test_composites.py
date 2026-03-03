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
    def test_dispatch_defaults_to_interactive(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """dispatch without flags defaults to interactive (not headless)."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-test-Mar03-1200"
        mock_inject.return_value = "demo-test-Mar03-1200-job-001"

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test", "--prompt", "Do the thing",
        ])
        assert result.exit_code == 0
        mock_inject.assert_called_once()
        _, kwargs = mock_inject.call_args
        assert kwargs["headless"] is False
        assert kwargs["wait"] is False

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_headless_flag(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """dispatch --headless enables headless + wait."""
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
            "--headless",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_inject.call_args
        assert kwargs["headless"] is True
        assert kwargs["wait"] is True

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_headless_no_wait(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """dispatch --headless --no-wait dispatches headless without blocking."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-test-Mar03-1200"
        mock_inject.return_value = "demo-test-Mar03-1200-job-001"

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test", "--headless", "--no-wait",
            "--prompt", "Do the thing",
        ])
        assert result.exit_code == 0
        _, kwargs = mock_inject.call_args
        assert kwargs["headless"] is True
        assert kwargs.get("wait") is not True

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_fetch_implies_wait(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """dispatch --fetch forces --wait and --headless."""
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
        assert kwargs["headless"] is True

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
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_plan_generates_prompt(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth, tmp_path
    ):
        """dispatch --plan reads file and generates execution prompt."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-test-Mar03-1200"
        mock_inject.return_value = "demo-test-Mar03-1200-job-001"

        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# My Plan\n\n### Task 1: Do stuff\n")

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test", "--plan", str(plan_file),
        ])
        assert result.exit_code == 0
        mock_inject.assert_called_once()
        _, kwargs = mock_inject.call_args
        prompt = kwargs["prompt"]
        assert "executing-plans" in prompt
        assert str(plan_file) in prompt or "My Plan" in prompt

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    def test_dispatch_plan_and_prompt_mutually_exclusive(
        self, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth, tmp_path
    ):
        """dispatch --plan and --prompt cannot be used together."""
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# Plan\n")

        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test",
            "--plan", str(plan_file), "--prompt", "also this",
        ])
        assert result.exit_code != 0

    def test_dispatch_plan_file_not_found(self):
        """dispatch --plan with missing file errors."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "dispatch", "demo", "--tag", "test",
            "--plan", "/nonexistent/plan.md",
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
        mock_inject.return_value = "demo-test-Mar03-1200-job-001"

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
    @patch("scad.cli.log_from_source")
    @patch("scad.cli._config_for_run")
    def test_harvest_fetches_and_logs(
        self, mock_config, mock_log, mock_fetch, mock_validate
    ):
        """harvest runs fetch then shows git log."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_config.return_value = config
        mock_fetch.return_value = [{"repo": "code", "branch": "scad-test", "source": "/tmp/code"}]
        mock_log.return_value = {"code": "abc1234 first commit"}

        runner = CliRunner()
        result = runner.invoke(main, ["harvest", "test-run"])
        assert result.exit_code == 0
        mock_fetch.assert_called_once()
        mock_log.assert_called_once()
        assert "Fetched" in result.output

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli.log_from_source")
    @patch("scad.cli._config_for_run")
    def test_harvest_no_changes(
        self, mock_config, mock_log, mock_fetch, mock_validate
    ):
        """harvest with no changes shows message."""
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_fetch.return_value = []
        mock_log.return_value = {}

        runner = CliRunner()
        result = runner.invoke(main, ["harvest", "test-run"])
        assert result.exit_code == 0

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli.log_from_source")
    @patch("scad.cli._config_for_run")
    def test_harvest_shows_log_not_diff(
        self, mock_config, mock_log, mock_fetch, mock_validate
    ):
        """harvest shows git log --oneline by default."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_config.return_value = config
        mock_fetch.return_value = [{"repo": "code", "branch": "scad-test", "source": "/tmp/code"}]
        mock_log.return_value = {"code": "abc1234 first commit\ndef5678 second commit"}

        runner = CliRunner()
        result = runner.invoke(main, ["harvest", "test-run"])
        assert result.exit_code == 0
        assert "abc1234" in result.output
        mock_log.assert_called_once()

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli.diff_from_source")
    @patch("scad.cli._config_for_run")
    def test_harvest_diff_flag_shows_diff(
        self, mock_config, mock_diff, mock_fetch, mock_validate
    ):
        """harvest --diff shows full diff output."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_config.return_value = config
        mock_fetch.return_value = [{"repo": "code", "branch": "scad-test", "source": "/tmp/code"}]
        mock_diff.return_value = {"code": "+new line\n-old line"}

        runner = CliRunner()
        result = runner.invoke(main, ["harvest", "test-run", "--diff"])
        assert result.exit_code == 0
        assert "+new line" in result.output
        mock_diff.assert_called_once()


class TestFinish:
    """Tests for scad finish — fetch + clean composite."""

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli.diff_from_source")
    @patch("scad.cli.clean_run")
    @patch("scad.cli._config_for_run")
    def test_finish_fetches_then_cleans(
        self, mock_config, mock_clean, mock_diff, mock_fetch, mock_validate
    ):
        """finish fetches before cleaning."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_config.return_value = config
        mock_fetch.return_value = [{"repo": "code", "branch": "b", "source": "/tmp"}]
        mock_diff.return_value = {}

        runner = CliRunner()
        result = runner.invoke(main, ["finish", "test-run"])
        assert result.exit_code == 0
        mock_fetch.assert_called_once()
        mock_clean.assert_called_once()

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.clean_run")
    @patch("scad.cli._config_for_run")
    def test_finish_no_fetch_skips_fetch(
        self, mock_config, mock_clean, mock_validate
    ):
        """finish --no-fetch skips fetching."""
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )

        runner = CliRunner()
        result = runner.invoke(main, ["finish", "test-run", "--no-fetch"])
        assert result.exit_code == 0
        mock_clean.assert_called_once()

    @patch("scad.cli.validate_run_id")
    @patch("scad.cli.fetch_to_host")
    @patch("scad.cli.diff_from_source")
    @patch("scad.cli._config_for_run")
    def test_finish_keep_session_skips_clean(
        self, mock_config, mock_diff, mock_fetch, mock_validate
    ):
        """finish --keep-session fetches but doesn't clean."""
        from scad.config import ScadConfig, RepoConfig
        mock_config.return_value = ScadConfig(
            name="test", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_fetch.return_value = []
        mock_diff.return_value = {}

        runner = CliRunner()
        result = runner.invoke(main, ["finish", "test-run", "--keep-session"])
        assert result.exit_code == 0


class TestBatch:
    """Tests for scad batch — parallel headless jobs."""

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    @patch("scad.cli.parse_prompt_file")
    def test_batch_runs_all_prompts(
        self, mock_parse, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """batch runs inject_job for each prompt in the file."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-batch-Mar03-1200"
        mock_parse.return_value = ["Prompt A", "Prompt B", "Prompt C"]
        mock_inject.return_value = ("demo-batch-Mar03-1200-job-001", 0)

        runner = CliRunner()
        result = runner.invoke(main, [
            "batch", "demo", "--tag", "batch", "--prompt-file", "/tmp/prompts.txt",
        ])
        assert result.exit_code == 0
        assert mock_inject.call_count == 3

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    @patch("scad.cli.parse_prompt_file")
    def test_batch_reports_summary(
        self, mock_parse, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """batch prints pass/fail summary."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-batch-Mar03-1200"
        mock_parse.return_value = ["Prompt A", "Prompt B"]
        # First succeeds, second fails
        mock_inject.side_effect = [
            ("demo-batch-Mar03-1200-job-001", 0),
            ("demo-batch-Mar03-1200-job-002", 1),
        ]

        runner = CliRunner()
        result = runner.invoke(main, [
            "batch", "demo", "--tag", "batch", "--prompt-file", "/tmp/prompts.txt",
        ])
        assert result.exit_code == 0
        assert "1 passed" in result.output
        assert "1 failed" in result.output

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    @patch("scad.cli.parse_prompt_file")
    def test_batch_fail_fast_stops_early(
        self, mock_parse, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """batch --fail-fast cancels remaining jobs on first failure."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-batch-Mar03-1200"
        mock_parse.return_value = ["A", "B", "C", "D"]
        # First fails — with --fail-fast and --parallel 1 the rest shouldn't run
        mock_inject.side_effect = [
            ("job-001", 1),
            ("job-002", 0),
            ("job-003", 0),
            ("job-004", 0),
        ]

        runner = CliRunner()
        result = runner.invoke(main, [
            "batch", "demo", "--tag", "batch",
            "--prompt-file", "/tmp/prompts.txt",
            "--fail-fast", "--parallel", "1",
        ])
        assert result.exit_code == 0
        # With parallel=1 and fail-fast, should stop after first failure
        assert mock_inject.call_count < 4

    def test_batch_requires_prompt_file(self):
        """batch without --prompt-file errors."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "batch", "demo", "--tag", "test",
        ])
        assert result.exit_code != 0

    @patch("scad.cli.check_claude_auth", return_value=(True, 8.0))
    @patch("scad.cli.image_exists", return_value=True)
    @patch("scad.cli.run_agent")
    @patch("scad.cli.inject_job")
    @patch("scad.cli.load_config")
    @patch("scad.cli.parse_prompt_file")
    def test_batch_parallel_flag(
        self, mock_parse, mock_load, mock_inject, mock_run_agent, mock_img, mock_auth
    ):
        """batch --parallel N limits concurrency."""
        from scad.config import ScadConfig, RepoConfig
        config = ScadConfig(
            name="demo", repos={"code": RepoConfig(path="/tmp/code", workdir=True)}
        )
        mock_load.return_value = config
        mock_run_agent.return_value = "demo-batch-Mar03-1200"
        mock_parse.return_value = ["A", "B"]
        mock_inject.return_value = ("job-001", 0)

        runner = CliRunner()
        result = runner.invoke(main, [
            "batch", "demo", "--tag", "batch",
            "--prompt-file", "/tmp/prompts.txt",
            "--parallel", "2",
        ])
        assert result.exit_code == 0
