"""Container management tests."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from scad.config import ScadConfig
from scad.container import render_build_context, generate_run_id


@pytest.fixture
def sample_config():
    return ScadConfig(
        name="test",
        repos={"code": {"path": "/tmp/fake", "workdir": True, "branch_from": "main"}},
        python={"version": "3.11", "requirements": "requirements.txt"},
        apt_packages=["build-essential"],
    )


class TestRenderBuildContext:
    def test_creates_dockerfile(self, sample_config, tmp_path):
        render_build_context(sample_config, tmp_path)
        dockerfile = tmp_path / "Dockerfile"
        assert dockerfile.exists()
        content = dockerfile.read_text()
        assert "FROM python:3.11-slim" in content

    def test_creates_entrypoint(self, sample_config, tmp_path):
        render_build_context(sample_config, tmp_path)
        entrypoint = tmp_path / "entrypoint.sh"
        assert entrypoint.exists()
        content = entrypoint.read_text()
        assert "git clone" in content

    def test_copies_requirements(self, sample_config, tmp_path):
        # Create a fake requirements.txt in a fake repo
        fake_repo = Path(sample_config.repos["code"].path)
        fake_repo.mkdir(parents=True, exist_ok=True)
        (fake_repo / "requirements.txt").write_text("numpy\n")

        render_build_context(sample_config, tmp_path)
        req_file = tmp_path / "requirements.txt"
        assert req_file.exists()
        assert "numpy" in req_file.read_text()

    def test_no_requirements_file(self, tmp_path):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake2", "workdir": True}},
        )
        render_build_context(config, tmp_path)
        assert not (tmp_path / "requirements.txt").exists()


class TestGenerateRunId:
    def test_format(self):
        run_id = generate_run_id("plan-22")
        # Should be like plan-22-Feb26-1430
        assert run_id.startswith("plan-22-")
        parts = run_id.split("-")
        # branch-parts-MonDD-HHMM
        assert len(parts) >= 3
        # Last two parts are the date and time
        assert len(parts[-1]) == 4  # HHMM
        assert len(parts[-2]) == 5  # MonDD like Feb26

    def test_contains_branch(self):
        run_id = generate_run_id("my-feature")
        assert "my-feature" in run_id
