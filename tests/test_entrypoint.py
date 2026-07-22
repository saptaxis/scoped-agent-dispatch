"""Entrypoint and Dockerfile template rendering tests."""

import os
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
from jinja2 import Environment, PackageLoader


@pytest.fixture
def jinja_env():
    return Environment(loader=PackageLoader("scad", "templates"))


def _render_entrypoint(jinja_env, **overrides):
    """Helper to render entrypoint with sensible defaults."""
    template = jinja_env.get_template("entrypoint.sh.j2")
    defaults = dict(
        workdir_key="code",
        requirements_file=None,
        config_name="test",
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

    def test_editable_install(self, jinja_env):
        """Entrypoint runs pip install -e . when python_editable=True."""
        result = _render_entrypoint(jinja_env, python_editable=True)
        assert "pip install" in result
        assert "-e ." in result

    def test_no_editable_install_by_default(self, jinja_env):
        """Entrypoint does NOT run pip install -e . by default."""
        result = _render_entrypoint(jinja_env)
        assert "-e ." not in result

    def test_generates_claude_config_stub(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "seed-claude.json" in result
        assert ".claude.json" in result

    def test_runs_bootstrap(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "bootstrap-claude.sh" in result

    def test_renders_status_json(self, jinja_env):
        result = _render_entrypoint(jinja_env)
        assert "STATUS_FILE" in result
        assert "exit_code" in result

    def test_log_file_capture_early(self, jinja_env):
        """Log capture starts before any setup steps."""
        result = _render_entrypoint(jinja_env)
        exec_pos = result.find("exec >")
        setup_pos = result.find("seed-claude.json")
        assert exec_pos != -1
        assert exec_pos < setup_pos

    def test_pretrusts_workdir(self, jinja_env):
        """Entrypoint seeds .claude.json from seed file (trust logic in claude_config)."""
        result = _render_entrypoint(jinja_env)
        assert "seed-claude.json" in result
        assert ".claude.json" in result

    def test_no_include_coauthored_by_in_entrypoint(self, jinja_env):
        """Entrypoint does NOT contain the broken includeCoAuthoredBy setting."""
        result = _render_entrypoint(jinja_env)
        assert "includeCoAuthoredBy" not in result

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
        """Entrypoint seeds settings.json from rendered seed file."""
        result = _render_entrypoint(jinja_env)
        assert "settings.json" in result
        assert "seed-settings.json" in result

    def test_seeds_from_json_files(self, jinja_env):
        """Entrypoint uses seed JSON files instead of inline Python config."""
        result = _render_entrypoint(jinja_env)
        assert "seed-claude.json" in result
        assert "seed-settings.json" in result

    def test_git_delta_config(self, jinja_env):
        """Entrypoint configures git to use delta as pager."""
        result = _render_entrypoint(jinja_env)
        assert "core.pager" in result or "delta" in result


class TestSSHKeyStagingResilience:
    """Regression tests for fix 7: the entrypoint's SSH key staging must not
    kill the container when ~/.ssh contains something `cp -r` can't handle.
    `cp -r /mnt/host-ssh/. "$HOME/.ssh/"` was unguarded under `set -euo
    pipefail`; `cp -r` fails on sockets and dangling symlinks. A user with
    `ControlPath ~/.ssh/cm-%r@%h:%p` (very common) has live control-master
    sockets in ~/.ssh -> `cp` errors -> entrypoint exits -> container dies
    immediately, while `session start` had already printed "Container
    started" and returned success -- the failure only surfaces later as a
    tmux timeout.

    These tests extract the real SSH-staging block from the rendered
    template and execute it in a real bash subprocess (substituting a temp
    directory for the hardcoded /mnt/host-ssh, since that path only exists
    inside the container), so a regression in the actual shell logic is
    caught, not just a string search over the template source.
    """

    def _extract_and_run(self, host_ssh_dir: Path, home_dir: Path) -> subprocess.CompletedProcess:
        env = Environment(loader=PackageLoader("scad", "templates"))
        template = env.get_template("entrypoint.sh.j2")
        rendered = template.render(
            config_name="test", workdir_key="code", requirements_file=None,
        )
        start = rendered.index("# Copy staged SSH keys")
        # The block ends at the first `fi` on its own line after `start`.
        end = rendered.index("\nfi\n", start) + len("\nfi\n")
        block = rendered[start:end]
        assert "/mnt/host-ssh" in block, "SSH staging block not found as expected"
        block = block.replace("/mnt/host-ssh", str(host_ssh_dir))

        script = f"#!/bin/bash\nset -euo pipefail\nHOME={home_dir}\n{block}\n"
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(script)
            script_path = f.name
        try:
            return subprocess.run(
                ["bash", script_path], capture_output=True, text=True, timeout=10,
            )
        finally:
            os.unlink(script_path)

    def _bind_socket(self, path: Path) -> socket.socket:
        """AF_UNIX bind paths are limited to ~104 bytes on macOS/BSD -- well
        under pytest's deeply-nested tmp_path. Bind via a relative path after
        chdir'ing into the target directory to stay under that limit
        regardless of how long tmp_path itself is."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cwd = os.getcwd()
        os.chdir(path.parent)
        try:
            sock.bind(path.name)
        finally:
            os.chdir(cwd)
        return sock

    def test_survives_a_control_master_socket(self, tmp_path):
        """The exact live-reported scenario: a ControlMaster socket sitting
        directly in ~/.ssh must not crash the copy."""
        host_ssh = tmp_path / "host-ssh"
        host_ssh.mkdir()
        (host_ssh / "id_rsa").write_text("fake-private-key")
        (host_ssh / "id_rsa.pub").write_text("fake-public-key")
        sock_path = host_ssh / "cm-user@host_22"
        sock = self._bind_socket(sock_path)
        assert sock_path.is_socket()

        home = tmp_path / "home"
        home.mkdir()

        result = self._extract_and_run(host_ssh, home)
        sock.close()

        assert result.returncode == 0, (
            f"SSH staging must not fail on a socket -- stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )

    def test_still_copies_real_keys_alongside_the_socket(self, tmp_path):
        host_ssh = tmp_path / "host-ssh"
        host_ssh.mkdir()
        (host_ssh / "id_rsa").write_text("fake-private-key")
        (host_ssh / "id_rsa.pub").write_text("fake-public-key")
        (host_ssh / "config").write_text("Host *\n  ForwardAgent yes\n")
        sock_path = host_ssh / "cm-user@host_22"
        sock = self._bind_socket(sock_path)

        home = tmp_path / "home"
        home.mkdir()

        result = self._extract_and_run(host_ssh, home)
        sock.close()

        assert result.returncode == 0
        ssh_dir = home / ".ssh"
        assert (ssh_dir / "id_rsa").read_text() == "fake-private-key"
        assert (ssh_dir / "id_rsa.pub").read_text() == "fake-public-key"
        assert (ssh_dir / "config").exists()
        assert not (ssh_dir / "cm-user@host_22").exists(), (
            "the socket itself must not be copied into the container"
        )

    def test_keys_still_land_with_correct_permissions(self, tmp_path):
        """The permission guarantee (0700 dir / 0600 files) must survive
        even with a socket present."""
        host_ssh = tmp_path / "host-ssh"
        host_ssh.mkdir()
        (host_ssh / "id_rsa").write_text("fake-private-key")
        sock = self._bind_socket(host_ssh / "cm-user@host_22")

        home = tmp_path / "home"
        home.mkdir()

        result = self._extract_and_run(host_ssh, home)
        sock.close()

        assert result.returncode == 0
        ssh_dir = home / ".ssh"
        assert oct(ssh_dir.stat().st_mode)[-3:] == "700"
        assert oct((ssh_dir / "id_rsa").stat().st_mode)[-3:] == "600"

    def test_survives_a_dangling_symlink(self, tmp_path):
        """A dangling symlink (e.g. from a password manager's SSH agent
        integration that isn't running) must not crash the copy either."""
        host_ssh = tmp_path / "host-ssh"
        host_ssh.mkdir()
        (host_ssh / "id_rsa").write_text("fake-private-key")
        (host_ssh / "dangling").symlink_to(host_ssh / "does-not-exist")

        home = tmp_path / "home"
        home.mkdir()

        result = self._extract_and_run(host_ssh, home)

        assert result.returncode == 0, (
            f"SSH staging must not fail on a dangling symlink -- "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert (home / ".ssh" / "id_rsa").read_text() == "fake-private-key"

    def test_no_host_ssh_dir_is_a_noop(self, tmp_path):
        """Sanity: when nothing is mounted at /mnt/host-ssh, the block must
        not run at all (guarded by the `[ -d ... ]` check)."""
        host_ssh = tmp_path / "does-not-exist"
        home = tmp_path / "home"
        home.mkdir()

        result = self._extract_and_run(host_ssh, home)

        assert result.returncode == 0
        assert not (home / ".ssh").exists()


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


@pytest.fixture
def rendered_entrypoint(jinja_env):
    """Render entrypoint with minimal template variables for simplified template."""
    template = jinja_env.get_template("entrypoint.sh.j2")
    return template.render(
        config_name="test",
        workdir_key="code",
        requirements_file=None,
    )


class TestSimplifiedEntrypoint:
    """Tests for setup-only entrypoint (no Claude launch)."""

    def test_no_mode_branching(self, rendered_entrypoint):
        """Entrypoint should not branch on HEADLESS or PROMPT."""
        assert 'if [ -n "$HEADLESS" ]' not in rendered_entrypoint
        assert 'if [ -n "$PROMPT" ]' not in rendered_entrypoint
        assert "AGENT_PROMPT" not in rendered_entrypoint

    def test_ends_with_sleep_infinity(self, rendered_entrypoint):
        """Entrypoint should end with sleep infinity."""
        lines = rendered_entrypoint.strip().split("\n")
        assert lines[-1].strip() == "sleep infinity"

    def test_no_claude_command(self, rendered_entrypoint):
        """Entrypoint should not build or run claude command."""
        assert "CLAUDE_CMD" not in rendered_entrypoint
        assert "claude -p" not in rendered_entrypoint

    def test_creates_default_tmux_session(self, rendered_entrypoint):
        """Entrypoint should start a default tmux session for interactive work."""
        assert 'tmux new-session -d -s scad' in rendered_entrypoint

    def test_logs_session_ready(self, rendered_entrypoint):
        """Entrypoint should log session-ready event."""
        assert "session-ready" in rendered_entrypoint

    def test_still_seeds_config(self, rendered_entrypoint):
        """Setup steps remain: seed claude.json, settings.json, git, creds, plugins."""
        assert "seed-claude.json" in rendered_entrypoint
        assert "seed-settings.json" in rendered_entrypoint
        assert "host-gitconfig" in rendered_entrypoint
        assert "host-claude-credentials" in rendered_entrypoint
        assert "bootstrap-claude.sh" in rendered_entrypoint

    def test_still_activates_venv(self, rendered_entrypoint):
        """Venv activation and pip install remain."""
        assert "/opt/venv/bin/activate" in rendered_entrypoint


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
