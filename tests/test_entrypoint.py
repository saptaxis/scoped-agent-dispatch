"""Entrypoint template rendering tests."""

import pytest
from jinja2 import Environment, PackageLoader


@pytest.fixture
def jinja_env():
    return Environment(loader=PackageLoader("scad", "templates"))


class TestEntrypointTemplate:
    def test_renders_repo_cloning(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}, "docs": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file="requirements.txt",
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "git clone /mnt/repos/code" in result
        assert "git clone /mnt/repos/docs" in result

    def test_renders_branch_checkout(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert 'git checkout -b "$BRANCH"' in result

    def test_renders_workdir(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "cd /workspace/code" in result

    def test_renders_pip_sync(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file="requirements.txt",
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "pip install" in result
        assert "requirements.txt" in result

    def test_renders_skip_permissions(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "--dangerously-skip-permissions" in result

    def test_renders_bundle_creation(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "git bundle create" in result

    def test_renders_status_json(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "STATUS_FILE" in result
        assert "exit_code" in result

    def test_renders_add_dir(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={
                "code": {"branch_from": "main", "add_dir": False},
                "docs": {"branch_from": "main", "add_dir": True},
            },
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "--add-dir /workspace/docs" in result
