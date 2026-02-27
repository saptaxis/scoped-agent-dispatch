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

    def test_bundle_uses_rev_list_not_pipe(self, jinja_env):
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        # Must use rev-list (no SIGPIPE risk), not git log | head | grep
        assert "git rev-list --count" in result
        assert "head -1 | grep" not in result

    def test_log_file_created_early(self, jinja_env):
        """Log file capture starts before repo cloning, not just at Claude."""
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        exec_pos = result.find("exec >")
        clone_pos = result.find("git clone")
        assert exec_pos != -1, "exec redirect not found"
        assert exec_pos < clone_pos, "exec redirect must appear before git clone"

    def test_headless_uses_stream_json(self, jinja_env):
        """Headless mode uses --output-format stream-json to a separate log."""
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "--output-format stream-json" in result
        assert "STREAM_LOG" in result
        assert ".stream.jsonl" in result
        # Should NOT use script -qfc or pipe to tee for headless
        assert "script -qfc" not in result
        assert '| tee "$LOG_FILE"' not in result

    def test_generates_claude_config_stub(self, jinja_env):
        """Entrypoint generates minimal .claude.json for onboarding skip."""
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        assert "hasCompletedOnboarding" in result
        assert "installMethod" in result
        assert ".claude.json" in result
        # Stub must be generated before claude command runs
        stub_pos = result.find("hasCompletedOnboarding")
        claude_pos = result.find("--output-format stream-json")
        assert stub_pos < claude_pos, "config stub must be generated before Claude runs"

    def test_set_e_disabled_around_claude(self, jinja_env):
        """set -e is disabled before Claude runs so non-zero exit doesn't
        skip bundle creation and status file writing."""
        template = jinja_env.get_template("entrypoint.sh.j2")
        result = template.render(
            repos={"code": {"branch_from": "main"}},
            workdir_key="code",
            requirements_file=None,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
            config_name="test",
        )
        set_plus_e_pos = result.find("set +e")
        claude_pos = result.find("--output-format stream-json")
        set_minus_e_pos = result.find("set -e", claude_pos)
        bundle_pos = result.find("git bundle create")
        assert set_plus_e_pos != -1, "set +e not found"
        assert set_plus_e_pos < claude_pos, "set +e must come before claude command"
        assert set_minus_e_pos != -1, "set -e not restored after claude"
        assert set_minus_e_pos < bundle_pos, "set -e must be restored before bundling"
