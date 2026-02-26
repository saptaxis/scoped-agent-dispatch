"""Template rendering tests."""

import pytest
from jinja2 import Environment, PackageLoader

from scad.config import ScadConfig


@pytest.fixture
def jinja_env():
    return Environment(loader=PackageLoader("scad", "templates"))


@pytest.fixture
def sample_config():
    return ScadConfig(
        name="test",
        repos={"code": {"path": "/tmp/fake", "workdir": True}},
        apt_packages=["build-essential", "ffmpeg"],
        python={"version": "3.11", "requirements": "requirements.txt"},
    )


class TestDockerfileTemplate:
    def test_renders_base_image(self, jinja_env, sample_config):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image=sample_config.base_image,
            apt_packages=sample_config.apt_packages,
            requirements_content=True,
        )
        assert "FROM python:3.11-slim" in result

    def test_includes_apt_packages(self, jinja_env, sample_config):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image=sample_config.base_image,
            apt_packages=sample_config.apt_packages,
            requirements_content=True,
        )
        assert "build-essential" in result
        assert "ffmpeg" in result

    def test_includes_requirements_install(self, jinja_env, sample_config):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image=sample_config.base_image,
            apt_packages=sample_config.apt_packages,
            requirements_content=True,
        )
        assert "requirements.txt" in result
        assert "pip install" in result

    def test_skips_requirements_when_none(self, jinja_env, sample_config):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image=sample_config.base_image,
            apt_packages=[],
            requirements_content=False,
        )
        assert "COPY requirements.txt" not in result

    def test_includes_claude_install(self, jinja_env, sample_config):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image=sample_config.base_image,
            apt_packages=[],
            requirements_content=False,
        )
        assert "claude" in result.lower() or "claude.ai/install" in result

    def test_has_entrypoint(self, jinja_env, sample_config):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image=sample_config.base_image,
            apt_packages=[],
            requirements_content=False,
        )
        assert "ENTRYPOINT" in result
