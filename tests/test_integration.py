"""Integration tests — requires Docker daemon running."""

import json
import subprocess
import pytest
from pathlib import Path

from scad.config import ScadConfig
from scad.container import render_build_context, generate_run_id


@pytest.fixture
def integration_repo(tmp_path):
    """Create a real git repo to use as a source."""
    repo = tmp_path / "source-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, capture_output=True
    )
    (repo / "hello.txt").write_text("hello world\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
    return repo


@pytest.fixture
def integration_config(integration_repo):
    return ScadConfig(
        name="integration-test",
        repos={
            "code": {
                "path": str(integration_repo),
                "workdir": True,
            }
        },
    )


class TestBuildContext:
    def test_full_render(self, integration_config, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        render_build_context(integration_config, build_dir)

        assert (build_dir / "Dockerfile").exists()
        assert (build_dir / "entrypoint.sh").exists()
        assert (build_dir / "bootstrap-claude.sh").exists()
        assert (build_dir / "bootstrap-claude.conf").exists()

        dockerfile = (build_dir / "Dockerfile").read_text()
        assert "FROM python:3.11-slim" in dockerfile
        assert "ENTRYPOINT" in dockerfile

        entrypoint = (build_dir / "entrypoint.sh").read_text()
        assert "cd /workspace/code" in entrypoint
        assert "git clone" not in entrypoint
        assert "git bundle" not in entrypoint
        assert "tmux" in entrypoint


class TestRunIdGeneration:
    def test_uniqueness(self):
        """Run IDs generated at different times should contain config name."""
        rid = generate_run_id("lwg")
        assert "lwg" in rid

    def test_format_readable(self):
        rid = generate_run_id("lwg")
        # Should contain month abbreviation
        months = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        assert any(m in rid for m in months)
