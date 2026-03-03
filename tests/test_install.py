"""Tests for install.sh bootstrap script.

These tests verify the script's logic by testing individual functions.
The script is bash, so we test by running it with --dry-run or by
testing the Python helper it calls for plugin registration.
"""

import json
import os
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestInstallScript:
    """Tests for install.sh behavior."""

    def test_script_is_executable(self):
        """install.sh has executable permission."""
        script = Path(__file__).parent.parent / "install.sh"
        assert script.exists(), "install.sh not found at repo root"
        assert os.access(script, os.X_OK), "install.sh is not executable"

    def test_script_has_bash_shebang(self):
        """install.sh starts with #!/bin/bash or #!/usr/bin/env bash."""
        script = Path(__file__).parent.parent / "install.sh"
        first_line = script.read_text().split("\n")[0]
        assert first_line in ("#!/bin/bash", "#!/usr/bin/env bash"), \
            f"Expected bash shebang, got: {first_line}"

    def test_script_sets_euo_pipefail(self):
        """install.sh uses strict mode."""
        script = Path(__file__).parent.parent / "install.sh"
        content = script.read_text()
        assert "set -euo pipefail" in content, "Missing strict mode"

    def test_dry_run_makes_no_changes(self, tmp_path):
        """--dry-run prints what would happen without doing it."""
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(script), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert result.returncode == 0
        assert "DRY RUN" in result.stdout or "dry run" in result.stdout.lower()
        # No venv should be created
        assert not (tmp_path / "venv").exists()

    def test_default_scad_home_is_dot_scad(self, tmp_path):
        """Default SCAD_HOME is ~/.scad."""
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(script), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert f"{tmp_path}/.scad" in result.stdout

    def test_custom_home_flag(self, tmp_path):
        """--home sets custom SCAD_HOME."""
        script = Path(__file__).parent.parent / "install.sh"
        custom_home = tmp_path / "my-scad"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(script), "--dry-run", "--home", str(custom_home)],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert str(custom_home) in result.stdout

    def test_no_claude_skips_plugin(self, tmp_path):
        """When claude is not on PATH, plugin registration is skipped."""
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        # Remove claude from PATH by setting a minimal PATH
        env["PATH"] = str(tmp_path / "bin")
        (tmp_path / "bin").mkdir(parents=True)
        result = subprocess.run(
            [str(script), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert "not found" in result.stdout.lower() or "skipping plugin" in result.stdout.lower()

    def test_no_zshrc_skips_completions(self, tmp_path):
        """When no .zshrc exists, completions are skipped."""
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        # Don't create .zshrc
        result = subprocess.run(
            [str(script), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert "skipping" in result.stdout.lower() or "no .zshrc" in result.stdout.lower()

    def test_no_plugin_flag_skips_even_with_claude(self, tmp_path):
        """--no-plugin skips plugin registration even if claude is available."""
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(script), "--dry-run", "--no-plugin"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert "skipping plugin" in result.stdout.lower()

    def test_no_completions_flag_skips(self, tmp_path):
        """--no-completions skips shell completion setup."""
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        (tmp_path / ".zshrc").write_text("# existing config\n")
        result = subprocess.run(
            [str(script), "--dry-run", "--no-completions"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert "skipping" in result.stdout.lower()


class TestPluginRegistration:
    """Tests for Claude Code plugin registration helper."""

    def test_register_creates_entry(self, tmp_path):
        """register_plugin adds scad to installed_plugins.json."""
        from scad.install import register_claude_plugin

        plugins_dir = tmp_path / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True)
        plugins_file = plugins_dir / "installed_plugins.json"
        plugins_file.write_text(json.dumps({"version": 2, "plugins": {}}))

        plugin_path = tmp_path / "scad-plugin"
        plugin_path.mkdir()
        (plugin_path / "plugin.json").write_text(json.dumps({
            "name": "scad", "version": "0.2.0",
            "description": "Scad plugin"
        }))

        register_claude_plugin(
            claude_home=tmp_path / ".claude",
            plugin_path=plugin_path
        )

        data = json.loads(plugins_file.read_text())
        assert "scad" in data["plugins"]
        entry = data["plugins"]["scad"][0]
        assert entry["installPath"] == str(plugin_path)
        assert entry["scope"] == "user"

    def test_register_updates_settings(self, tmp_path):
        """register_plugin adds scad to enabledPlugins in settings.json."""
        from scad.install import register_claude_plugin

        plugins_dir = tmp_path / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True)
        plugins_file = plugins_dir / "installed_plugins.json"
        plugins_file.write_text(json.dumps({"version": 2, "plugins": {}}))

        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {}}))

        plugin_path = tmp_path / "scad-plugin"
        plugin_path.mkdir()
        (plugin_path / "plugin.json").write_text(json.dumps({
            "name": "scad", "version": "0.2.0",
            "description": "Scad plugin"
        }))

        register_claude_plugin(
            claude_home=tmp_path / ".claude",
            plugin_path=plugin_path
        )

        settings = json.loads(settings_file.read_text())
        assert settings["enabledPlugins"].get("scad") is True

    def test_register_idempotent(self, tmp_path):
        """Running register_plugin twice doesn't duplicate entries."""
        from scad.install import register_claude_plugin

        plugins_dir = tmp_path / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True)
        plugins_file = plugins_dir / "installed_plugins.json"
        plugins_file.write_text(json.dumps({"version": 2, "plugins": {}}))

        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(json.dumps({"enabledPlugins": {}}))

        plugin_path = tmp_path / "scad-plugin"
        plugin_path.mkdir()
        (plugin_path / "plugin.json").write_text(json.dumps({
            "name": "scad", "version": "0.2.0",
            "description": "Scad plugin"
        }))

        register_claude_plugin(claude_home=tmp_path / ".claude", plugin_path=plugin_path)
        register_claude_plugin(claude_home=tmp_path / ".claude", plugin_path=plugin_path)

        data = json.loads(plugins_file.read_text())
        assert len(data["plugins"]["scad"]) == 1

    def test_register_no_claude_home_skips(self, tmp_path):
        """If ~/.claude doesn't exist, registration is skipped gracefully."""
        from scad.install import register_claude_plugin

        plugin_path = tmp_path / "scad-plugin"
        plugin_path.mkdir()
        (plugin_path / "plugin.json").write_text(json.dumps({
            "name": "scad", "version": "0.2.0",
            "description": "Scad plugin"
        }))

        # Should not raise
        result = register_claude_plugin(
            claude_home=tmp_path / ".claude",
            plugin_path=plugin_path
        )
        assert result is False


class TestUninstall:
    """Tests for --uninstall behavior."""

    def test_uninstall_dry_run(self, tmp_path):
        """--uninstall --dry-run shows what would be removed."""
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(script), "--uninstall", "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert result.returncode == 0
        assert "uninstall" in result.stdout.lower()

    def test_uninstall_removes_symlink(self, tmp_path):
        """--uninstall removes the scad symlink from ~/.local/bin."""
        local_bin = tmp_path / ".local" / "bin"
        local_bin.mkdir(parents=True)
        scad_link = local_bin / "scad"
        scad_link.symlink_to("/fake/path/scad")

        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        subprocess.run(
            [str(script), "--uninstall"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert not scad_link.exists()

    def test_uninstall_removes_venv(self, tmp_path):
        """--uninstall removes the scad venv."""
        venv_dir = tmp_path / "venv"
        venv_dir.mkdir(parents=True)
        (venv_dir / "bin").mkdir()
        (venv_dir / "bin" / "scad").touch()

        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(venv_dir)
        subprocess.run(
            [str(script), "--uninstall"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert not venv_dir.exists()

    def test_uninstall_removes_zshrc_lines(self, tmp_path):
        """--uninstall removes the managed lines from ~/.zshrc."""
        zshrc = tmp_path / ".zshrc"
        zshrc.write_text(
            'export FOO=bar\n'
            '\n'
            '# scad — managed by install.sh\n'
            'export SCAD_HOME="$HOME/.scad"\n'
            'eval "$(_SCAD_COMPLETE=zsh_source scad)"\n'
            'export BAZ=qux\n'
        )

        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        subprocess.run(
            [str(script), "--uninstall"],
            capture_output=True, text=True, env=env, timeout=30
        )
        content = zshrc.read_text()
        assert "scad" not in content
        assert "FOO=bar" in content
        assert "BAZ=qux" in content

    def test_uninstall_preserves_scad_home(self, tmp_path):
        """--uninstall does NOT remove $SCAD_HOME (user data)."""
        scad_home = tmp_path / ".scad"
        scad_home.mkdir()
        (scad_home / "configs").mkdir()
        (scad_home / "configs" / "myconfig.yml").write_text("name: test")

        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        subprocess.run(
            [str(script), "--uninstall"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert scad_home.exists()
        assert (scad_home / "configs" / "myconfig.yml").exists()

    def test_uninstall_deregisters_plugin(self, tmp_path):
        """--uninstall removes scad from Claude's installed_plugins.json."""
        from scad.install import deregister_claude_plugin

        plugins_dir = tmp_path / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True)
        plugins_file = plugins_dir / "installed_plugins.json"
        plugins_file.write_text(json.dumps({
            "version": 2,
            "plugins": {"scad": [{"scope": "user", "installPath": "/fake"}]}
        }))

        settings_file = tmp_path / ".claude" / "settings.json"
        settings_file.write_text(json.dumps({
            "enabledPlugins": {"scad": True, "other-plugin": True}
        }))

        deregister_claude_plugin(claude_home=tmp_path / ".claude")

        data = json.loads(plugins_file.read_text())
        assert "scad" not in data["plugins"]

        settings = json.loads(settings_file.read_text())
        assert "scad" not in settings["enabledPlugins"]
        assert settings["enabledPlugins"]["other-plugin"] is True
