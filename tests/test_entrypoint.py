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

    def test_interactive_context_prompt(self, jinja_env):
        result = _render_entrypoint(
            jinja_env,
            context_prompt="Read /workspace/docs/overview.md for project context",
        )
        assert "tmux new-session -d -s scad" in result
        assert "Read /workspace/docs/overview.md for project context" in result

    def test_interactive_no_context_prompt(self, jinja_env):
        result = _render_entrypoint(jinja_env, context_prompt=None)
        assert 'tmux new-session -d -s scad "$CLAUDE_CMD; exec bash"' in result

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
        assert set_plus_e != -1
        assert set_plus_e < claude_pos
        # Status writing is handled by EXIT trap with set -e
        assert "trap write_status EXIT" in result
        assert "STATUS_FILE" in result

    def test_log_file_capture_early(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        exec_pos = result.find("exec >")
        claude_pos = result.find("$CLAUDE_CMD")
        assert exec_pos != -1
        assert exec_pos < claude_pos

    def test_pretrusts_workdir(self, jinja_env):
        """Entrypoint .claude.json includes hasTrustDialogAccepted for workdir."""
        result = _render_entrypoint(jinja_env)
        assert "hasTrustDialogAccepted" in result
        assert "/workspace/" in result

    def test_disables_coauthored_by(self, jinja_env):
        """Entrypoint sets coAuthoredBy to false in settings.json."""
        result = _render_entrypoint(jinja_env)
        assert "coAuthoredBy" in result

    def test_claude_exit_drops_to_bash(self, jinja_env):
        """Interactive mode: Claude exit drops to bash, not container exit."""
        result = _render_entrypoint(jinja_env)
        assert "exec bash" in result

    def test_sleep_infinity(self, jinja_env):
        """Container stays alive via sleep infinity, not tmux wait loop."""
        result = _render_entrypoint(jinja_env)
        assert "sleep infinity" in result
        assert "while tmux has-session" not in result

    def test_credentials_copied_if_mounted(self, jinja_env):
        """Credentials always re-copied from staging path (handles /login refresh)."""
        result = _render_entrypoint(jinja_env)
        assert "/mnt/host-claude-credentials.json" in result
        assert ".credentials.json" in result

    def test_seeds_settings_json(self, jinja_env):
        """Entrypoint creates settings.json if not present."""
        result = _render_entrypoint(jinja_env)
        assert "settings.json" in result

    def test_bypass_permissions_when_skip_enabled(self, jinja_env):
        """Entrypoint sets bypassPermissions when dangerously_skip_permissions is true."""
        result = _render_entrypoint(
            jinja_env,
            claude={"dangerously_skip_permissions": True, "additional_flags": None},
        )
        assert "bypassPermissions" in result
        assert "skipDangerousModePermissionPrompt" in result

    def test_no_bypass_permissions_when_skip_disabled(self, jinja_env):
        """Entrypoint does NOT set bypassPermissions when dangerously_skip_permissions is false."""
        result = _render_entrypoint(
            jinja_env,
            claude={"dangerously_skip_permissions": False, "additional_flags": None},
        )
        assert "bypassPermissions" not in result

    def test_deny_rules_always_present(self, jinja_env):
        """Entrypoint always adds deny rules regardless of permissions mode."""
        result = _render_entrypoint(jinja_env)
        assert "deny" in result
        assert "rm -rf" in result

    def test_pretooluse_hooks(self, jinja_env):
        """Entrypoint adds PreToolUse safety hooks."""
        result = _render_entrypoint(jinja_env)
        assert "PreToolUse" in result

    def test_cleanup_period(self, jinja_env):
        """Entrypoint sets cleanupPeriodDays to prevent auto-cleanup."""
        result = _render_entrypoint(jinja_env)
        assert "cleanupPeriodDays" in result

    def test_statusline_hook(self, jinja_env):
        """Entrypoint configures statusline hook in settings.json."""
        result = _render_entrypoint(jinja_env)
        assert "Notification" in result or "statusline" in result

    def test_statusline_hook_command(self, jinja_env):
        """Statusline hook points to the statusline script."""
        result = _render_entrypoint(jinja_env)
        assert "statusline.sh" in result

    def test_git_delta_config(self, jinja_env):
        """Entrypoint configures git to use delta as pager."""
        result = _render_entrypoint(jinja_env)
        assert "core.pager" in result or "delta" in result


class TestDockerfileTemplate:
    def _make_config(self):
        return {
            "base_image": "python:3.11-slim",
            "apt_packages": ["build-essential"],
            "requirements_content": False,
        }

    def _render_dockerfile(self, config):
        env = Environment(loader=PackageLoader("scad", "templates"))
        template = env.get_template("Dockerfile.j2")
        return template.render(**config)

    def test_includes_tmux(self, jinja_env):
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        assert "tmux" in rendered

    def test_copies_bootstrap_scripts(self, jinja_env):
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        assert "bootstrap-claude.sh" in rendered
        assert "bootstrap-claude.conf" in rendered

    def test_path_before_claude_install(self):
        """PATH is set before Claude install to suppress warning."""
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        path_pos = rendered.index('PATH="/home/scad/.local/bin')
        install_pos = rendered.index("claude.ai/install.sh")
        assert path_pos < install_pos

    def test_includes_ohmyzsh(self):
        """Dockerfile installs oh-my-zsh."""
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        assert "ohmyzsh" in rendered

    def test_sets_term(self):
        """Dockerfile sets TERM=xterm-256color."""
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        assert "xterm-256color" in rendered

    def test_copies_tmux_conf(self):
        """Dockerfile copies .tmux.conf."""
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        assert ".tmux.conf" in rendered

    def test_includes_jq(self):
        """Dockerfile installs jq for statusline script."""
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        assert "jq" in rendered

    def test_installs_delta(self):
        """Dockerfile installs git-delta."""
        config = self._make_config()
        rendered = self._render_dockerfile(config)
        assert "delta" in rendered


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
