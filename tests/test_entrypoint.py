"""Entrypoint and Dockerfile template rendering tests."""

import pytest
from jinja2 import Environment, PackageLoader


@pytest.fixture
def jinja_env():
    return Environment(loader=PackageLoader("scad", "templates"))


def _render_entrypoint(jinja_env, **overrides):
    """Helper to render entrypoint with sensible defaults."""
    template = jinja_env.get_template("entrypoint.sh.j2")
    defaults = dict(
        repos={"code": {"add_dir": False}},
        workdir_key="code",
        requirements_file=None,
        claude={"dangerously_skip_permissions": True, "additional_flags": None},
        config_name="test",
        context_prompt=None,
    )
    defaults.update(overrides)
    return template.render(**defaults)


class TestEntrypointTemplate:
    def test_no_git_clone(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "git clone" not in result

    def test_no_bundle_creation(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "git bundle" not in result

    def test_renders_workdir(self, jinja_env):
        result = _render_entrypoint(jinja_env, workdir_key="myrepo")
        assert "cd /workspace/myrepo" in result

    def test_renders_pip_sync(self, jinja_env):
        result = _render_entrypoint(jinja_env, requirements_file="requirements.txt")
        assert "pip install" in result
        assert "requirements.txt" in result

    def test_no_pip_sync_without_requirements(self, jinja_env):
        result = _render_entrypoint(jinja_env, requirements_file=None)
        assert "pip install" not in result

    def test_renders_skip_permissions(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "--dangerously-skip-permissions" in result

    def test_renders_add_dir(self, jinja_env):
        result = _render_entrypoint(
            jinja_env,
            repos={
                "code": {"add_dir": False},
                "docs": {"add_dir": True},
            },
        )
        assert "--add-dir /workspace/docs" in result
        assert "--add-dir /workspace/code" not in result

    def test_headless_uses_stream_json(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "--output-format stream-json" in result
        assert "STREAM_LOG" in result
        assert ".stream.jsonl" in result

    def test_interactive_tmux_session(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "tmux new-session -d -s scad" in result
        assert "tmux has-session -t scad" in result

    def test_interactive_context_prompt(self, jinja_env):
        result = _render_entrypoint(
            jinja_env,
            context_prompt="Read /workspace/docs/overview.md for project context",
        )
        assert "tmux new-session -d -s scad" in result
        assert "Read /workspace/docs/overview.md for project context" in result

    def test_interactive_no_context_prompt(self, jinja_env):
        result = _render_entrypoint(jinja_env, context_prompt=None)
        assert 'tmux new-session -d -s scad "$CLAUDE_CMD"' in result

    def test_generates_claude_config_stub(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "hasCompletedOnboarding" in result
        assert "installMethod" in result
        assert ".claude.json" in result

    def test_runs_bootstrap(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "bootstrap-claude.sh" in result

    def test_renders_status_json(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "STATUS_FILE" in result
        assert "exit_code" in result

    def test_set_e_disabled_around_claude(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        set_plus_e = result.find("set +e")
        # Find the actual claude execution (not the CLAUDE_CMD= assignment)
        claude_pos = result.find("$CLAUDE_CMD -p")
        set_minus_e = result.find("set -e", claude_pos)
        status_pos = result.find("STATUS_FILE", set_minus_e)
        assert set_plus_e != -1
        assert set_plus_e < claude_pos
        assert set_minus_e != -1
        assert set_minus_e < status_pos

    def test_log_file_capture_early(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        exec_pos = result.find("exec >")
        claude_pos = result.find("$CLAUDE_CMD")
        assert exec_pos != -1
        assert exec_pos < claude_pos


class TestDockerfileTemplate:
    def test_includes_tmux(self, jinja_env):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image="python:3.11-slim",
            apt_packages=["build-essential"],
            requirements_content=False,
        )
        assert "tmux" in result

    def test_copies_bootstrap_scripts(self, jinja_env):
        template = jinja_env.get_template("Dockerfile.j2")
        result = template.render(
            base_image="python:3.11-slim",
            apt_packages=[],
            requirements_content=False,
        )
        assert "bootstrap-claude.sh" in result
        assert "bootstrap-claude.conf" in result


class TestBootstrapConfTemplate:
    def test_renders_default_plugins(self, jinja_env):
        template = jinja_env.get_template("bootstrap-claude.conf.j2")
        result = template.render(plugins=[
            "superpowers@claude-plugins-official",
            "commit-commands@claude-plugins-official",
            "pyright-lsp@claude-plugins-official",
        ])
        assert "superpowers@claude-plugins-official" in result
        assert "commit-commands@claude-plugins-official" in result
        assert "pyright-lsp@claude-plugins-official" in result

    def test_renders_custom_plugins(self, jinja_env):
        template = jinja_env.get_template("bootstrap-claude.conf.j2")
        result = template.render(plugins=["superpowers@claude-plugins-official"])
        assert "superpowers@claude-plugins-official" in result
        assert "commit-commands" not in result

    def test_renders_empty_plugins(self, jinja_env):
        template = jinja_env.get_template("bootstrap-claude.conf.j2")
        result = template.render(plugins=[])
        assert "PLUGINS=(" in result
