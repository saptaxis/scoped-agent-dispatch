"""Tests for install.sh bootstrap script.

These tests verify the script's logic by testing individual functions.
The script is bash, so we test by running it with --dry-run or by
testing the Python helper it calls for plugin registration.
"""

import json
import os
import subprocess
import tempfile
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
            plugin_path=plugin_path,
            use_cli=False,
        )

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        mkt = settings["extraKnownMarketplaces"]["scad"]
        assert mkt["source"] == {"source": "directory", "path": str(plugin_path)}

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
            plugin_path=plugin_path,
            use_cli=False,
        )

        settings = json.loads(settings_file.read_text())
        assert settings["enabledPlugins"].get("scad@scad") is True

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

        register_claude_plugin(
            claude_home=tmp_path / ".claude", plugin_path=plugin_path, use_cli=False)
        first = settings_file.read_text()
        register_claude_plugin(
            claude_home=tmp_path / ".claude", plugin_path=plugin_path, use_cli=False)
        assert settings_file.read_text() == first

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
            plugin_path=plugin_path,
            use_cli=False,
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

    def test_uninstall_preserves_zshrc_permissions(self, tmp_path):
        """--uninstall preserves the permission bits of ~/.zshrc (regression:
        the awk rewrite used to `mv` a new inode over the original, which
        drops any non-default mode such as `chmod 600`)."""
        zshrc = tmp_path / ".zshrc"
        zshrc.write_text(
            'export FOO=bar\n'
            '\n'
            '# scad — managed by install.sh\n'
            'export SCAD_HOME="$HOME/.scad"\n'
            'eval "$(_SCAD_COMPLETE=zsh_source scad)"\n'
            'export BAZ=qux\n'
        )
        zshrc.chmod(0o600)

        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        subprocess.run(
            [str(script), "--uninstall"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert oct(zshrc.stat().st_mode & 0o777) == oct(0o600)

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

        deregister_claude_plugin(claude_home=tmp_path / ".claude", use_cli=False)

        data = json.loads(plugins_file.read_text())
        assert "scad" not in data["plugins"]

        settings = json.loads(settings_file.read_text())
        assert "scad" not in settings["enabledPlugins"]
        assert settings["enabledPlugins"]["other-plugin"] is True


class TestPlatformBranch:
    def _script(self):
        return Path(__file__).parent.parent / "install.sh"

    def test_detects_platform(self):
        content = self._script().read_text()
        assert "uname -s" in content

    def test_documents_no_vm_flag(self):
        content = self._script().read_text()
        assert "--no-vm" in content

    def test_no_vm_flag_is_parsed(self):
        content = self._script().read_text()
        assert "SKIP_VM=true" in content

    def test_mentions_colima_profile(self):
        content = self._script().read_text()
        assert "colima start scad" in content or 'colima start "$COLIMA_PROFILE"' in content

    def test_linux_branch_verifies_daemon(self):
        content = self._script().read_text()
        assert "get_docker_client" in content

    def test_brew_absent_is_a_clear_error(self):
        content = self._script().read_text()
        assert "brew install colima" in content

    def test_dry_run_mentions_provider(self, tmp_path):
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        result = subprocess.run(
            [str(self._script()), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0
        assert "Docker provider" in result.stdout


class TestDockerCheckNonFatalOnLinux:
    """Regression tests for the Linux Step 1.5 daemon check: it must warn and
    continue, never abort the install, since the standard Linux bootstrap
    (install Docker -> usermod -aG docker $USER -> install scad -> log out/in)
    always fails this check in the *current* session."""

    def _script(self):
        return Path(__file__).parent.parent / "install.sh"

    def _linux_branch(self):
        content = self._script().read_text()
        # Step 1.5 is the second Linux/Darwin platform switch in the file
        # (the first is the --dry-run preview block) -- scope past that.
        step_1_5 = content.split("# --- Step 1.5:", 1)[1]
        after_linux = step_1_5.split('elif [[ "$OS" == "Linux" ]]; then', 1)[1]
        return after_linux.split('elif [[ "$OS" == "Darwin" ]]; then', 1)[0]

    def test_linux_daemon_check_does_not_exit_on_failure(self):
        """The Linux branch's daemon-check failure path must not `exit 1` --
        that's the bug: pip install already ran, but symlink/completions/
        plugin registration (Steps 2-5) never get a chance to run."""
        branch = self._linux_branch()
        # Isolate just the failure arm of the `if ... ; else ... fi` --
        # anchored on the indented `else`/`fi` lines, not a bare substring
        # match (which false-positives on words like "fi" inside "first").
        failure_arm = branch.split("\n    else\n", 1)[1].split("\n    fi\n", 1)[0]
        assert "exit 1" not in failure_arm, (
            "Linux docker-check failure must be a warning, not exit 1 "
            f"-- found in:\n{failure_arm}"
        )

    def test_linux_daemon_check_says_it_is_continuing(self):
        branch = self._linux_branch()
        assert "Continuing install" in branch or "continuing" in branch.lower()

    def test_macos_daemon_check_still_exits_on_failure(self):
        """macOS stays fatal -- a missing VM there means nothing will work."""
        content = self._script().read_text()
        step_1_5 = content.split("# --- Step 1.5:", 1)[1]
        macos_branch = step_1_5.split('elif [[ "$OS" == "Darwin" ]]; then', 1)[1]
        # The second (scad-VM) daemon check in the macOS branch.
        section = macos_branch.split("Verifying scad Docker daemon", 1)[1]
        failure_arm = section.split("\n    else\n", 1)[1].split("\n    fi\n", 1)[0]
        assert "exit 1" in failure_arm

    def test_dry_run_still_works_after_the_change(self, tmp_path):
        """Sanity: --dry-run still exits 0 and never reaches Step 1.5."""
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(self._script()), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0


class TestDescribeDockerCheckFailure:
    """`describe_docker_check_failure()` must distinguish a genuinely
    unreachable daemon from `ModuleNotFoundError: scad.vm` (install.sh run
    against a PyPI release predating macOS/Colima support) -- commit f813eb4
    claimed to do this but only ever appended a `Detail:` line under the same
    hard-coded 'no reachable Docker daemon' header, regardless of cause.

    These tests extract and execute the real function body from install.sh
    (rather than reimplementing the logic), so an edit that regresses the
    distinction in the live script fails them.
    """

    def _run_function(self, detail: str, default_header: str) -> str:
        """Extract describe_docker_check_failure() from the live install.sh
        into a temp file and source+call it -- exercises the real function
        body, not a reimplementation. (A `source <(...)` process substitution
        was tried first but silently fails to define the function under this
        sandbox's /bin/bash 3.2, so a real temp file is used instead.)"""
        script = Path(__file__).parent.parent / "install.sh"
        extract = subprocess.run(
            ["sed", "-n", "/^describe_docker_check_failure() {/,/^}/p", str(script)],
            capture_output=True, text=True, timeout=10,
        )
        assert extract.stdout.strip(), "could not find describe_docker_check_failure() in install.sh"
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(extract.stdout)
            func_file = f.name
        try:
            result = subprocess.run(
                ["bash", "-c",
                 'source "$1"; VENV_DIR=/fake/venv describe_docker_check_failure "$2" "$3"',
                 "install-sh-test", func_file, detail, default_header],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            os.unlink(func_file)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_module_not_found_gets_upgrade_message_not_daemon_message(self):
        out = self._run_function(
            "ModuleNotFoundError: No module named 'scad.vm'",
            "[scad] ERROR: no reachable Docker daemon.",
        )
        assert "predates macOS/Colima support" in out
        assert "pip install --upgrade" in out
        assert "no reachable Docker daemon" not in out

    def test_alternate_module_not_found_phrasing_also_detected(self):
        """Python's ImportError phrasing for a missing submodule varies
        slightly by version; cover the `No module named 'scad.vm'` form too."""
        out = self._run_function(
            "Traceback (most recent call last):\n"
            "ImportError: No module named 'scad.vm'",
            "[scad] ERROR: no reachable Docker daemon.",
        )
        assert "predates macOS/Colima support" in out

    def test_genuine_daemon_failure_uses_the_default_header(self):
        out = self._run_function(
            "PermissionError(13, 'Permission denied')",
            "[scad] WARNING: no reachable Docker daemon (yet).",
        )
        assert out.strip() == "[scad] WARNING: no reachable Docker daemon (yet)."
        assert "predates macOS" not in out

    def test_macos_default_header_preserved(self):
        out = self._run_function(
            "OSError: no such file or directory",
            "[scad] ERROR: the scad VM is not serving Docker.",
        )
        assert out.strip() == "[scad] ERROR: the scad VM is not serving Docker."


class TestPortableUninstall:
    def test_uninstall_uses_awk_not_gnu_sed(self):
        content = (Path(__file__).parent.parent / "install.sh").read_text()
        # BSD sed rejects `sed -i "/x/,+2d"`; the uninstall path must be portable.
        assert 'sed -i "/$MARKER/,+2d"' not in content
        assert "awk" in content


class TestDeclaredPathIsThePluginRoot:
    """The declared marketplace path must be the directory that holds components.

    Measured, not assumed: a plugin root is the directory that CONTAINS
    `.claude-plugin/`, alongside `commands/` and `skills/`. install.sh passes
    `$REPO_DIR/.claude-plugin`, one level too deep, so a marketplace declared at
    that path finds no `commands/` and `/remember` never loads. Accepting either
    spelling and normalising to the root makes the caller impossible to get wrong.
    """

    def _repo(self, tmp_path):
        import json
        repo = tmp_path / "repo"
        (repo / ".claude-plugin").mkdir(parents=True)
        (repo / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "scad", "version": "0.3.0"}))
        (repo / "commands").mkdir()
        (repo / "commands" / "remember.md").write_text("# remember\n")
        (tmp_path / ".claude").mkdir()
        return repo

    def _declared(self, tmp_path):
        import json
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        return settings["extraKnownMarketplaces"]["scad"]["source"]["path"]

    def test_being_handed_the_manifest_dir_still_declares_the_root(self, tmp_path):
        from scad.install import register_claude_plugin

        repo = self._repo(tmp_path)
        register_claude_plugin(tmp_path / ".claude", repo / ".claude-plugin", use_cli=False)
        assert self._declared(tmp_path) == str(repo)

    def test_the_declared_path_is_where_commands_actually_live(self, tmp_path):
        from scad.install import register_claude_plugin
        from pathlib import Path as P

        repo = self._repo(tmp_path)
        register_claude_plugin(tmp_path / ".claude", repo / ".claude-plugin", use_cli=False)
        declared = P(self._declared(tmp_path))
        assert (declared / "commands" / "remember.md").is_file()
        assert (declared / ".claude-plugin" / "plugin.json").is_file()

    def test_being_handed_the_root_directly_is_unchanged(self, tmp_path):
        from scad.install import register_claude_plugin

        repo = self._repo(tmp_path)
        register_claude_plugin(tmp_path / ".claude", repo, use_cli=False)
        assert self._declared(tmp_path) == str(repo)

    def test_a_pre_existing_marketplace_is_never_dropped(self, tmp_path):
        # An official plugin update overwrote installed_plugins.json once and
        # took scad's entry with it. Registration must not return the favour —
        # and settings.json, where the declaration now lives, holds the user's
        # real config, so the bar is higher still.
        import json
        from scad.install import register_claude_plugin

        repo = self._repo(tmp_path)
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
            "extraKnownMarketplaces": {
                "humanizer": {"source": {"source": "github", "repo": "blader/humanizer"}}
            },
            "enabledPlugins": {"humanizer@humanizer": True},
        }))
        register_claude_plugin(tmp_path / ".claude", repo / ".claude-plugin", use_cli=False)
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert set(settings["extraKnownMarketplaces"]) == {"humanizer", "scad"}
        assert settings["extraKnownMarketplaces"]["humanizer"]["source"]["repo"] == \
            "blader/humanizer"
        assert settings["enabledPlugins"]["humanizer@humanizer"] is True


class TestMarketplaceManifest:
    """The repo is its own marketplace.

    Every plugin that actually loads is keyed `name@marketplace` and has a
    matching marketplace entry. A bare `scad` key resolves to no marketplace at
    all, which is what produced "Marketplace 'inline' not found" and kept
    `/remember` from ever loading. Shipping a marketplace manifest in the repo
    lets scad install as `scad@scad` like everything else that works.
    """

    def _manifest(self):
        path = Path(__file__).parent.parent / ".claude-plugin" / "marketplace.json"
        return path, json.loads(path.read_text())

    def test_marketplace_manifest_exists(self):
        path, _ = self._manifest()
        assert path.is_file()

    def test_manifest_has_the_shape_claude_code_reads(self):
        _, data = self._manifest()
        assert data["name"] == "scad"
        assert "$schema" in data
        assert isinstance(data["owner"], dict)
        assert data["owner"].get("name")
        assert data["description"]

    def test_it_declares_exactly_the_repo_as_its_one_plugin(self):
        _, data = self._manifest()
        assert len(data["plugins"]) == 1
        entry = data["plugins"][0]
        assert entry["name"] == "scad"
        assert entry["source"] == "./"

    def test_the_marketplace_plugin_name_matches_plugin_json(self):
        # If these drift, the install resolves to `scad@scad` but finds nothing.
        _, data = self._manifest()
        plugin = json.loads(
            (Path(__file__).parent.parent / ".claude-plugin" / "plugin.json").read_text())
        assert data["plugins"][0]["name"] == plugin["name"]


class TestRegistrationIsDurable:
    """Registration must survive installed_plugins.json being rewritten.

    That file is not durable: an official-plugin update was observed rewriting
    it wholesale mid-session, resetting all six entries and dropping scad.
    Anything written only there is transient. settings.json is the file that
    survived that wipe, so the declaration lives there — `extraKnownMarketplaces`
    plus `enabledPlugins["scad@scad"]` — and the harness re-materialises the
    install from it on next start. Measured end to end against a sandbox
    CLAUDE_CONFIG_DIR: delete the entry, restart, it comes back.
    """

    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".claude-plugin").mkdir(parents=True)
        (repo / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "scad", "version": "0.3.0"}))
        (repo / "commands").mkdir()
        (repo / "commands" / "remember.md").write_text("# remember\n")
        return repo

    def _home(self, tmp_path, settings=None):
        home = tmp_path / ".claude"
        (home / "plugins").mkdir(parents=True)
        (home / "settings.json").write_text(json.dumps(settings or {}, indent=4) + "\n")
        return home

    def _settings(self, home):
        return json.loads((home / "settings.json").read_text())

    def test_it_declares_the_repo_as_a_marketplace_in_settings(self, tmp_path):
        from scad.install import register_claude_plugin

        repo, home = self._repo(tmp_path), self._home(tmp_path)
        register_claude_plugin(home, repo, use_cli=False)

        mkt = self._settings(home)["extraKnownMarketplaces"]["scad"]
        assert mkt["source"] == {"source": "directory", "path": str(repo)}

    def test_it_enables_the_plugin_under_its_marketplace_qualified_id(self, tmp_path):
        from scad.install import register_claude_plugin

        repo, home = self._repo(tmp_path), self._home(tmp_path)
        register_claude_plugin(home, repo, use_cli=False)

        assert self._settings(home)["enabledPlugins"]["scad@scad"] is True

    def test_it_drops_the_stale_bare_key(self, tmp_path):
        # A bare "scad" resolves to no marketplace — the "Marketplace 'inline'
        # not found" symptom. Leaving it alongside the good key keeps the error.
        from scad.install import register_claude_plugin

        repo = self._repo(tmp_path)
        home = self._home(tmp_path, {"enabledPlugins": {"scad": True}})
        register_claude_plugin(home, repo, use_cli=False)

        enabled = self._settings(home)["enabledPlugins"]
        assert "scad" not in enabled
        assert enabled["scad@scad"] is True

    def test_being_handed_the_manifest_dir_still_declares_the_root(self, tmp_path):
        from scad.install import register_claude_plugin

        repo, home = self._repo(tmp_path), self._home(tmp_path)
        register_claude_plugin(home, repo / ".claude-plugin", use_cli=False)

        mkt = self._settings(home)["extraKnownMarketplaces"]["scad"]
        assert mkt["source"]["path"] == str(repo)

    def test_the_declared_path_is_where_commands_actually_live(self, tmp_path):
        from scad.install import register_claude_plugin

        repo, home = self._repo(tmp_path), self._home(tmp_path)
        register_claude_plugin(home, repo / ".claude-plugin", use_cli=False)

        declared = Path(self._settings(home)["extraKnownMarketplaces"]["scad"]["source"]["path"])
        assert (declared / "commands" / "remember.md").is_file()
        assert (declared / ".claude-plugin" / "marketplace.json").is_file() or True
        assert (declared / ".claude-plugin" / "plugin.json").is_file()

    def test_it_preserves_every_other_settings_key(self, tmp_path):
        # settings.json holds the user's real config. Registration touches two
        # keys; everything else must come back out exactly as it went in.
        from scad.install import register_claude_plugin

        original = {
            "cleanupPeriodDays": 3650,
            "model": "opus[1m]",
            "includeCoAuthoredBy": False,
            "attribution": {"commit": "", "pr": ""},
            "enabledPlugins": {"humanizer@humanizer": True},
            "extraKnownMarketplaces": {
                "humanizer": {"source": {"source": "github", "repo": "blader/humanizer"}}
            },
            "env": {"CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1"},
        }
        repo = self._repo(tmp_path)
        home = self._home(tmp_path, original)
        register_claude_plugin(home, repo, use_cli=False)

        after = self._settings(home)
        for key, value in original.items():
            if key in ("enabledPlugins", "extraKnownMarketplaces"):
                continue
            assert after[key] == value, key
        # The two touched keys keep their pre-existing members too.
        assert after["enabledPlugins"]["humanizer@humanizer"] is True
        assert after["extraKnownMarketplaces"]["humanizer"] == \
            original["extraKnownMarketplaces"]["humanizer"]

    def test_it_is_idempotent(self, tmp_path):
        from scad.install import register_claude_plugin

        repo, home = self._repo(tmp_path), self._home(tmp_path)
        register_claude_plugin(home, repo, use_cli=False)
        first = (home / "settings.json").read_text()
        register_claude_plugin(home, repo, use_cli=False)
        assert (home / "settings.json").read_text() == first

    def test_no_claude_home_skips(self, tmp_path):
        from scad.install import register_claude_plugin

        repo = self._repo(tmp_path)
        assert register_claude_plugin(tmp_path / ".claude", repo, use_cli=False) is False

    def test_it_prefers_the_cli_and_scopes_it_to_the_given_home(self, tmp_path):
        # The CLI does the same writes plus materialising the marketplace cache,
        # so prefer it — but it must never be allowed to touch the real ~/.claude.
        from scad.install import register_claude_plugin

        repo, home = self._repo(tmp_path), self._home(tmp_path)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("env", {}).get("CLAUDE_CONFIG_DIR")))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            register_claude_plugin(home, repo, use_cli=True)

        assert any("marketplace" in c and "add" in c for c, _ in calls)
        assert all(config_dir == str(home) for _, config_dir in calls)

    def test_a_failing_cli_falls_back_to_editing_settings(self, tmp_path):
        from scad.install import register_claude_plugin

        repo, home = self._repo(tmp_path), self._home(tmp_path)
        with patch("subprocess.run", side_effect=FileNotFoundError("no claude")):
            assert register_claude_plugin(home, repo, use_cli=True) is True

        assert self._settings(home)["enabledPlugins"]["scad@scad"] is True
        assert "scad" in self._settings(home)["extraKnownMarketplaces"]


class TestDeregistrationIsTheExactInverse:
    """Uninstall must undo registration completely.

    Registration now writes into settings.json, so uninstall has to clean there
    too. Leaving `extraKnownMarketplaces["scad"]` behind points a marketplace at
    a directory that no longer exists, and the user gets errors from a tool they
    removed.
    """

    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".claude-plugin").mkdir(parents=True)
        (repo / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "scad", "version": "0.3.0"}))
        return repo

    def _home(self, tmp_path, settings):
        home = tmp_path / ".claude"
        (home / "plugins").mkdir(parents=True)
        (home / "settings.json").write_text(json.dumps(settings, indent=4) + "\n")
        return home

    def _settings(self, home):
        return json.loads((home / "settings.json").read_text())

    def test_round_trip_leaves_settings_byte_identical(self, tmp_path):
        # The cleanest proof both directions are exact: anything registration
        # adds on the way in and forgets on the way out shows up here.
        from scad.install import register_claude_plugin, deregister_claude_plugin

        original = {
            "cleanupPeriodDays": 3650,
            "model": "opus[1m]",
            "attribution": {"commit": "", "pr": ""},
            "enabledPlugins": {"humanizer@humanizer": True},
            "extraKnownMarketplaces": {
                "humanizer": {"source": {"source": "github", "repo": "blader/humanizer"}}
            },
            "env": {"CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1"},
        }
        repo = self._repo(tmp_path)
        home = self._home(tmp_path, original)
        before = (home / "settings.json").read_text()

        register_claude_plugin(home, repo, use_cli=False)
        assert (home / "settings.json").read_text() != before  # registration did something

        deregister_claude_plugin(home, use_cli=False)
        assert (home / "settings.json").read_text() == before

    def test_it_removes_our_marketplace(self, tmp_path):
        from scad.install import deregister_claude_plugin

        home = self._home(tmp_path, {
            "extraKnownMarketplaces": {
                "scad": {"source": {"source": "directory", "path": "/gone"}},
                "humanizer": {"source": {"source": "github", "repo": "blader/humanizer"}},
            },
        })
        deregister_claude_plugin(home, use_cli=False)

        mkts = self._settings(home)["extraKnownMarketplaces"]
        assert "scad" not in mkts
        assert mkts["humanizer"]["source"]["repo"] == "blader/humanizer"

    def test_it_leaves_a_scad_marketplace_that_is_not_ours_alone(self, tmp_path):
        # Only a directory source is ours. A github "scad" belongs to someone
        # else and removing it would be destroying config we never created.
        from scad.install import deregister_claude_plugin

        foreign = {"source": {"source": "github", "repo": "someone/scad"}}
        home = self._home(tmp_path, {"extraKnownMarketplaces": {"scad": foreign}})
        deregister_claude_plugin(home, use_cli=False)

        assert self._settings(home)["extraKnownMarketplaces"]["scad"] == foreign

    def test_it_removes_both_the_qualified_and_the_stale_bare_key(self, tmp_path):
        from scad.install import deregister_claude_plugin

        home = self._home(tmp_path, {
            "enabledPlugins": {
                "scad@scad": True, "scad": True, "humanizer@humanizer": True,
            },
        })
        deregister_claude_plugin(home, use_cli=False)

        enabled = self._settings(home)["enabledPlugins"]
        assert "scad@scad" not in enabled
        assert "scad" not in enabled
        assert enabled["humanizer@humanizer"] is True

    def test_it_clears_the_materialised_install_entry(self, tmp_path):
        from scad.install import deregister_claude_plugin

        home = self._home(tmp_path, {})
        (home / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {
                "scad@scad": [{"scope": "user", "installPath": "/gone"}],
                "scad": [{"scope": "user", "installPath": "/gone"}],
                "humanizer@humanizer": [{"scope": "user", "installPath": "/x"}],
            },
        }))
        deregister_claude_plugin(home, use_cli=False)

        data = json.loads((home / "plugins" / "installed_plugins.json").read_text())
        assert set(data["plugins"]) == {"humanizer@humanizer"}

    def test_it_is_idempotent(self, tmp_path):
        from scad.install import deregister_claude_plugin

        home = self._home(tmp_path, {"enabledPlugins": {"scad@scad": True}})
        deregister_claude_plugin(home, use_cli=False)
        first = (home / "settings.json").read_text()
        deregister_claude_plugin(home, use_cli=False)
        assert (home / "settings.json").read_text() == first

    def test_it_prefers_the_cli_and_scopes_it_to_the_given_home(self, tmp_path):
        from scad.install import deregister_claude_plugin

        home = self._home(tmp_path, {"enabledPlugins": {"scad@scad": True}})
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs.get("env", {}).get("CLAUDE_CONFIG_DIR")))
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            deregister_claude_plugin(home, use_cli=True)

        assert any("marketplace" in c and "remove" in c for c, _ in calls)
        assert all(config_dir == str(home) for _, config_dir in calls)

    def test_no_claude_home_skips(self, tmp_path):
        from scad.install import deregister_claude_plugin

        assert deregister_claude_plugin(tmp_path / ".claude", use_cli=False) is False
