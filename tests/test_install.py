"""Tests for install.sh bootstrap script.

These tests verify the script's logic by testing individual functions.
The script is bash, so we test by running it with --dry-run or by testing
the Python helpers it calls — clearing any leftover Claude Code plugin
registration, and the transcript-retention prompt.
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

    def test_no_plugin_flag_still_works_as_an_alias_for_no_skills(self, tmp_path):
        """`--no-plugin` named the mechanism that used to ship scad's skills.

        That mechanism is gone, but the flag is still accepted and still means
        "do not install skills" — a rename inside scad must not break a
        scripted install that was passing it.
        """
        script = Path(__file__).parent.parent / "install.sh"
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(script), "--dry-run", "--no-plugin"],
            capture_output=True, text=True, env=env, timeout=30
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "skipping skill installation" in result.stdout.lower()

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


class TestDeregistrationIsTheExactInverse:
    """Deregistration must take a registered config back exactly.

    scad no longer registers a plugin — skills ship through ~/.agents/skills
    and ~/.claude/skills instead — but every machine installed before that
    switch still carries the registration, and both install (as a migration)
    and uninstall call this to clear it. Leaving
    `extraKnownMarketplaces["scad"]` behind points a marketplace at a directory
    that may no longer exist, and the user gets errors from a tool they removed.
    """

    # What the old `register_claude_plugin` wrote, as a literal fixture: the
    # user's own settings with scad's marketplace and qualified enabled key
    # appended to the containers that already existed. Deregistration is
    # exercised against this rather than against a live registration call, and
    # the two dicts are spelled out in full so a drift between them fails here
    # rather than being silently constructed away.
    USER_SETTINGS = {
        "cleanupPeriodDays": 3650,
        "model": "opus[1m]",
        "attribution": {"commit": "", "pr": ""},
        "enabledPlugins": {"humanizer@humanizer": True},
        "extraKnownMarketplaces": {
            "humanizer": {"source": {"source": "github", "repo": "blader/humanizer"}}
        },
        "env": {"CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1"},
    }

    def _registered(self, repo):
        return {
            "cleanupPeriodDays": 3650,
            "model": "opus[1m]",
            "attribution": {"commit": "", "pr": ""},
            "enabledPlugins": {"humanizer@humanizer": True, "scad@scad": True},
            "extraKnownMarketplaces": {
                "humanizer": {"source": {"source": "github", "repo": "blader/humanizer"}},
                "scad": {"source": {"source": "directory", "path": str(repo)}},
            },
            "env": {"CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN": "1"},
        }

    def _home(self, tmp_path, settings):
        home = tmp_path / ".claude"
        (home / "plugins").mkdir(parents=True)
        (home / "settings.json").write_text(json.dumps(settings, indent=4) + "\n")
        return home

    def _settings(self, home):
        return json.loads((home / "settings.json").read_text())

    def test_a_registered_config_comes_back_byte_identical(self, tmp_path):
        # The cleanest proof deregistration is exact: anything registration
        # added and deregistration forgets shows up here as a byte difference.
        from scad.install import deregister_claude_plugin

        repo = tmp_path / "repo"
        home = self._home(tmp_path, self._registered(repo))
        expected = json.dumps(self.USER_SETTINGS, indent=4) + "\n"
        assert (home / "settings.json").read_text() != expected  # fixture is registered

        deregister_claude_plugin(home, use_cli=False)
        assert (home / "settings.json").read_text() == expected

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


class TestTranscriptRetention:
    def _home(self, tmp_path, settings=None):
        home = tmp_path / ".claude"
        home.mkdir(parents=True, exist_ok=True)
        if settings is not None:
            (home / "settings.json").write_text(json.dumps(settings, indent=4) + "\n")
        return home

    def test_an_existing_value_is_never_touched(self, tmp_path):
        """A present key is a decision — including a short one. Someone who chose
        60 days meant 60, and raising it is as much an override as lowering it."""
        from scad.install import set_transcript_retention
        for existing in (7, 30, 60, 9999):
            home = self._home(tmp_path / str(existing), {"cleanupPeriodDays": existing})
            assert set_transcript_retention(home, ask=lambda _: True) == "kept"
            got = json.loads((home / "settings.json").read_text())
            assert got["cleanupPeriodDays"] == existing

    def test_sets_only_when_unset_and_consented(self, tmp_path):
        from scad.install import RETENTION_DAYS, set_transcript_retention
        home = self._home(tmp_path, {"model": "opus"})
        assert set_transcript_retention(home, ask=lambda _: True) == "set"
        got = json.loads((home / "settings.json").read_text())
        assert got["cleanupPeriodDays"] == RETENTION_DAYS
        assert got["model"] == "opus"

    def test_declining_changes_nothing(self, tmp_path):
        from scad.install import set_transcript_retention
        home = self._home(tmp_path, {"model": "opus"})
        before = (home / "settings.json").read_text()
        assert set_transcript_retention(home, ask=lambda _: False) == "declined"
        assert (home / "settings.json").read_text() == before

    def test_never_guesses_when_nobody_can_be_asked(self, tmp_path, monkeypatch):
        """A scripted install must not silently rewrite the harness's config."""
        from scad.install import set_transcript_retention
        monkeypatch.setattr("scad.install.sys.stdin.isatty", lambda: False)
        home = self._home(tmp_path, {})
        assert set_transcript_retention(home) == "skipped"
        assert "cleanupPeriodDays" not in json.loads((home / "settings.json").read_text())

    def test_assume_yes_skips_the_prompt(self, tmp_path):
        from scad.install import set_transcript_retention
        home = self._home(tmp_path, {})
        def explode(_):
            raise AssertionError("must not prompt under --yes")
        assert set_transcript_retention(home, assume_yes=True, ask=explode) == "set"

    def test_the_prompt_states_the_disk_cost_and_what_it_edits(self, tmp_path):
        from scad.install import set_transcript_retention
        seen = {}
        def capture(text):
            seen["text"] = text
            return False
        set_transcript_retention(self._home(tmp_path, {}), ask=capture)
        assert "settings.json" in seen["text"]
        assert "MB" in seen["text"]
        assert "3650" in seen["text"]

    def test_creates_settings_when_absent(self, tmp_path):
        from scad.install import set_transcript_retention
        home = self._home(tmp_path)
        assert set_transcript_retention(home, ask=lambda _: True) == "set"
        assert (home / "settings.json").is_file()

    def test_every_other_key_survives(self, tmp_path):
        from scad.install import set_transcript_retention
        original = {"model": "opus[1m]", "env": {"X": "1"},
                    "enabledPlugins": {"a@b": True}, "permissions": {"allow": ["Read"]}}
        home = self._home(tmp_path, dict(original))
        set_transcript_retention(home, ask=lambda _: True)
        got = json.loads((home / "settings.json").read_text())
        for k, v in original.items():
            assert got[k] == v

    def test_unreadable_settings_reports_failure_not_a_crash(self, tmp_path):
        from scad.install import set_transcript_retention
        home = self._home(tmp_path)
        (home / "settings.json").write_text("{ not json")
        assert set_transcript_retention(home, ask=lambda _: True) == "failed"

    def test_a_declined_answer_is_remembered_by_doing_nothing(self, tmp_path):
        """Declining leaves the key absent, so a later install asks again rather
        than treating silence as consent."""
        from scad.install import set_transcript_retention
        home = self._home(tmp_path, {})
        set_transcript_retention(home, ask=lambda _: False)
        assert set_transcript_retention(home, ask=lambda _: True) == "set"


REPO = Path(__file__).parent.parent
SHIPPED_SKILLS = tuple(sorted(p.name for p in (REPO / "skills").iterdir() if p.is_dir()))
# A PATH with no npx (and no `claude`) on it, so the fallback tests exercise the
# fallback on every machine rather than only on ones without Node installed.
BARE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _skills_helpers() -> str:
    """The skill install/removal helper section of the live install.sh.

    Extracted and sourced rather than reimplemented, so an edit that regresses
    the real script fails these tests. Same trick as
    TestDescribeDockerCheckFailure, scaled to a section: Step 5 sits behind a
    venv build and a pip install, which no unit test should be paying for.
    """
    content = (REPO / "install.sh").read_text()
    section = content.split("# --- Skills: where they go", 1)[1]
    section = section.split("# --- Uninstall flow ---", 1)[0]
    assert "install_skills()" in section and "remove_installed_skills()" in section
    return "# --- Skills: where they go" + section


def _run_helper(home: Path, command: str, env_extra=None, path=BARE_PATH):
    """Source the helper section against a throwaway HOME and run `command`."""
    helpers = home / "_install-helpers.sh"
    helpers.parent.mkdir(parents=True, exist_ok=True)
    helpers.write_text(_skills_helpers())
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = path
    env["VENV_DIR"] = str(home / "venv")
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", "-c",
         'set -euo pipefail; VENV_DIR="${VENV_DIR}"; source "$1"; shift; eval "$@"',
         "install-sh-test", str(helpers), command],
        capture_output=True, text=True, env=env, timeout=60,
    )


class TestSkillInstallation:
    """Step 5: getting scad's skills in front of every agent on the machine."""

    def test_npx_is_genuinely_absent_from_the_fallback_path(self):
        """Guard for the tests below: they must exercise the fallback because
        npx is missing, not because this machine happens to lack Node."""
        import shutil
        assert shutil.which("npx", path=BARE_PATH) is None

    def test_no_skills_flag_skips_installation_entirely(self, tmp_path):
        """--no-skills must not create the shared skill directories at all."""
        env = os.environ.copy()
        env["HOME"] = str(tmp_path)
        env["SCAD_INSTALL_VENV"] = str(tmp_path / "venv")
        result = subprocess.run(
            [str(REPO / "install.sh"), "--dry-run", "--no-skills"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Skipping skill installation (--no-skills)" in result.stdout
        assert not (tmp_path / ".agents").exists()
        assert not (tmp_path / ".claude" / "skills").exists()

    def test_no_skills_guards_the_whole_of_step_5(self):
        """The skip is structural, not a message: nothing in Step 5 — not the
        install, not the plugin deregistration it depends on — may run when
        --no-skills was passed."""
        content = (REPO / "install.sh").read_text()
        step_5 = content.split("# --- Step 5:", 1)[1].split("\n# ---", 1)[0]
        skip_arm, run_arm = step_5.split("\nelse\n", 1)
        assert "install_skills" not in skip_arm, \
            f"--no-skills arm must not install skills:\n{skip_arm}"
        assert "install_skills" in run_arm

    def test_fallback_links_into_both_agent_directories(self, tmp_path):
        """~/.agents/skills reaches Codex and Kimi; Claude reads only
        ~/.claude/skills. Missing either leaves an agent without scad."""
        result = _run_helper(tmp_path, f'install_skills "{REPO}"')
        assert result.returncode == 0, result.stdout + result.stderr
        assert "npx not found" in result.stdout

        for target in (tmp_path / ".agents/skills", tmp_path / ".claude/skills"):
            installed = sorted(p.name for p in target.iterdir())
            assert installed == list(SHIPPED_SKILLS), f"{target}: {installed}"

    def test_fallback_installs_symlinks_not_copies(self, tmp_path):
        """Symlinks are why editing a SKILL.md in the checkout takes effect at
        once; a copy would need a reinstall after every edit."""
        result = _run_helper(tmp_path, f'install_skills "{REPO}"')
        assert result.returncode == 0, result.stdout + result.stderr

        for target in (tmp_path / ".agents/skills", tmp_path / ".claude/skills"):
            for name in SHIPPED_SKILLS:
                entry = target / name
                assert entry.is_symlink(), f"{entry} is not a symlink"
                assert Path(os.path.realpath(entry)) == \
                    Path(os.path.realpath(REPO / "skills" / name))

    def test_deregistration_happens_before_skills_are_installed(self, tmp_path):
        """Ordering is the point. Plugin skills and ~/.claude/skills entries
        stack rather than override, so installing while the old plugin is still
        registered offers every scad skill twice. The plugin must be gone
        first."""
        import sys

        claude = tmp_path / ".claude"
        claude.mkdir(parents=True)
        (claude / "settings.json").write_text(json.dumps({
            "enabledPlugins": {"scad@scad": True, "other-plugin": True},
        }))

        # A stand-in for the venv's python: it records whether any skill was
        # already installed at the moment deregistration ran, then does the
        # real deregistration.
        witness = tmp_path / "witness"
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        stub = venv_bin / "python"
        stub.write_text(
            "#!/bin/bash\n"
            f'if [ -e "$HOME/.claude/skills/scad" ] || [ -e "$HOME/.agents/skills/scad" ]; then\n'
            f'  echo present > "{witness}"\n'
            "else\n"
            f'  echo absent > "{witness}"\n'
            "fi\n"
            f'exec "{sys.executable}" "$@"\n'
        )
        stub.chmod(0o755)

        result = _run_helper(tmp_path, f'install_skills "{REPO}"')
        assert result.returncode == 0, result.stdout + result.stderr

        assert witness.read_text().strip() == "absent", \
            "skills were already installed when deregistration ran — wrong order"
        # And both halves actually happened.
        settings = json.loads((claude / "settings.json").read_text())
        assert "scad@scad" not in settings.get("enabledPlugins", {})
        assert settings["enabledPlugins"]["other-plugin"] is True
        assert (tmp_path / ".claude/skills/scad").is_symlink()


class TestUninstallSkillRemoval:
    """--uninstall must remove scad's skills from the shared skill directories
    and nothing else.

    ~/.agents/skills and ~/.claude/skills are flat and global: every skill on
    the machine, from every source, lands in the same two directories. scad
    ships a skill called `remember` — about as generic a name as exists — so
    deleting by name alone means uninstalling scad can take a stranger's work
    with it.
    """

    def _uninstall(self, home: Path):
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["SCAD_INSTALL_VENV"] = str(home / "venv")
        return subprocess.run(
            [str(REPO / "install.sh"), "--uninstall"],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def _link_ours(self, home: Path):
        """Reproduce what the symlink fallback installs."""
        for target in (home / ".agents/skills", home / ".claude/skills"):
            target.mkdir(parents=True, exist_ok=True)
            for name in SHIPPED_SKILLS:
                (target / name).symlink_to(REPO / "skills" / name)

    def _copy_ours(self, home: Path):
        """Reproduce what the skills CLI installs — it copies, not links."""
        import shutil
        for target in (home / ".agents/skills", home / ".claude/skills"):
            target.mkdir(parents=True, exist_ok=True)
            for name in SHIPPED_SKILLS:
                shutil.copytree(REPO / "skills" / name, target / name)

    def test_uninstall_removes_our_linked_skills_from_both_directories(self, tmp_path):
        self._link_ours(tmp_path)

        result = self._uninstall(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        for target in (tmp_path / ".agents/skills", tmp_path / ".claude/skills"):
            for name in SHIPPED_SKILLS:
                entry = target / name
                assert not entry.exists() and not entry.is_symlink(), f"{entry} survived"
        assert f"Removed {2 * len(SHIPPED_SKILLS)} installed skill(s)" in result.stdout

    def test_uninstall_removes_our_skills_when_they_were_copied(self, tmp_path):
        """The skills CLI copies rather than links, so a copy of a skill we ship
        — byte-identical SKILL.md — is still ours to remove."""
        self._copy_ours(tmp_path)

        result = self._uninstall(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        for target in (tmp_path / ".agents/skills", tmp_path / ".claude/skills"):
            for name in SHIPPED_SKILLS:
                assert not (target / name).exists(), f"{target / name} survived"

    def test_uninstall_does_not_remove_a_same_named_foreign_skill(self, tmp_path):
        """Someone else's `remember` must survive scad's uninstall.

        Regression: removal matched on directory name only, so any third-party
        skill that happened to share a name with one of ours was deleted.
        """
        foreign = tmp_path / ".agents/skills/remember"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text(
            "---\nname: remember\n---\nSomebody else's remember skill.\n"
        )

        result = self._uninstall(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert foreign.is_dir(), "uninstall deleted a foreign skill that shared a name"
        assert "Somebody else's" in (foreign / "SKILL.md").read_text()
        assert "Left remember" in result.stdout, \
            f"uninstall should report what it left behind:\n{result.stdout}"

    def test_uninstall_leaves_a_foreign_skill_with_an_unrelated_name(self, tmp_path):
        """The shared directories are not scad's to tidy."""
        self._link_ours(tmp_path)
        stranger = tmp_path / ".claude/skills/somebody-elses-skill"
        stranger.mkdir(parents=True)
        (stranger / "SKILL.md").write_text("---\nname: somebody-elses-skill\n---\n")

        result = self._uninstall(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (stranger / "SKILL.md").is_file()
        # Untouched names are not reported either — nothing to say about them.
        assert "somebody-elses-skill" not in result.stdout

    def test_uninstall_leaves_a_same_named_link_that_points_elsewhere(self, tmp_path):
        """A `scad` skill symlinked in from another checkout resolves outside
        this repo's skills/, so it is not ours to delete."""
        other = tmp_path / "someone-else/skills/scad"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("---\nname: scad\n---\nNot our scad.\n")
        target = tmp_path / ".agents/skills"
        target.mkdir(parents=True)
        (target / "scad").symlink_to(other)

        result = self._uninstall(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert (target / "scad").is_symlink()
        assert (other / "SKILL.md").read_text().endswith("Not our scad.\n")
        assert "Left scad in" in result.stdout

    def test_uninstall_leaves_an_empty_shared_directory_only_if_it_emptied_it(self, tmp_path):
        """A directory scad emptied is scad's to clean up; one still holding
        somebody else's skill stays."""
        self._link_ours(tmp_path)
        keeper = tmp_path / ".claude/skills/somebody-elses-skill"
        keeper.mkdir(parents=True)

        result = self._uninstall(tmp_path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert not (tmp_path / ".agents/skills").exists()
        assert (tmp_path / ".claude/skills").is_dir()

    def test_removal_removes_nothing_when_it_cannot_resolve_our_skills(self, tmp_path):
        """If the source path cannot be canonicalised there is nothing to match
        against, and "inside our skills/" would collapse to "any absolute path"
        — every link in a shared directory. Refuse rather than guess."""
        target = tmp_path / ".agents/skills"
        target.mkdir(parents=True)
        (target / "remember").symlink_to(REPO / "skills" / "remember")

        result = _run_helper(
            tmp_path,
            f'realpath_of() {{ echo ""; }}; remove_installed_skills "{REPO}"',
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert (target / "remember").is_symlink()
        assert "Skipped skill removal" in result.stdout
