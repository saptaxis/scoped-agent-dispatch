"""Claude config module tests."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestGetHostTimezone:
    def test_reads_etc_timezone(self, tmp_path):
        """Reads IANA timezone from /etc/timezone."""
        from scad.claude_config import get_host_timezone

        with patch("scad.claude_config.Path") as mock_path_cls:
            mock_etc_tz = MagicMock()
            mock_etc_tz.exists.return_value = True
            mock_etc_tz.read_text.return_value = "Asia/Kolkata\n"

            def path_side_effect(arg):
                if arg == "/etc/timezone":
                    return mock_etc_tz
                return Path(arg)

            mock_path_cls.side_effect = path_side_effect
            assert get_host_timezone() == "Asia/Kolkata"

    def test_reads_localtime_symlink(self, tmp_path):
        """Falls back to /etc/localtime symlink target."""
        from scad.claude_config import get_host_timezone

        with patch("scad.claude_config.Path") as mock_path_cls:
            mock_etc_tz = MagicMock()
            mock_etc_tz.exists.return_value = False

            mock_localtime = MagicMock()
            mock_localtime.exists.return_value = True
            mock_localtime.is_symlink.return_value = True
            mock_localtime.resolve.return_value = Path(
                "/usr/share/zoneinfo/America/New_York"
            )

            def path_side_effect(arg):
                if arg == "/etc/timezone":
                    return mock_etc_tz
                if arg == "/etc/localtime":
                    return mock_localtime
                return Path(arg)

            mock_path_cls.side_effect = path_side_effect
            assert get_host_timezone() == "America/New_York"

    def test_falls_back_to_utc(self):
        """Returns UTC when no timezone info available."""
        from scad.claude_config import get_host_timezone

        with patch("scad.claude_config.Path") as mock_path_cls:
            mock_missing = MagicMock()
            mock_missing.exists.return_value = False
            mock_path_cls.side_effect = lambda arg: mock_missing

            assert get_host_timezone() == "UTC"


from scad.config import ScadConfig


class TestRenderClaudeJson:
    @pytest.fixture
    def sample_config(self):
        return ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
        )

    def test_sets_onboarding_complete(self, sample_config):
        from scad.claude_config import render_claude_json
        result = render_claude_json(sample_config)
        assert result["hasCompletedOnboarding"] is True

    def test_sets_effort_callout_dismissed(self, sample_config):
        from scad.claude_config import render_claude_json
        result = render_claude_json(sample_config)
        assert result["effortCalloutDismissed"] is True

    def test_sets_install_method(self, sample_config):
        from scad.claude_config import render_claude_json
        result = render_claude_json(sample_config)
        assert result["installMethod"] == "native"

    def test_trusts_workdir(self, sample_config):
        from scad.claude_config import render_claude_json
        result = render_claude_json(sample_config)
        projects = result["projects"]
        assert "/workspace/code" in projects
        assert projects["/workspace/code"]["hasTrustDialogAccepted"] is True

    def test_no_include_coauthored_by(self, sample_config):
        """render_claude_json does NOT include the broken includeCoAuthoredBy key."""
        from scad.claude_config import render_claude_json
        result = render_claude_json(sample_config)
        assert "includeCoAuthoredBy" not in result


class TestRenderSettingsJson:
    @pytest.fixture
    def sample_config(self):
        return ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
        )

    @pytest.fixture
    def skip_perms_config(self):
        return ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"dangerously_skip_permissions": True},
        )

    def test_sets_cleanup_period(self, sample_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(sample_config)
        assert result["cleanupPeriodDays"] == 365

    def test_sets_attribution_empty(self, sample_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(sample_config)
        assert result["attribution"] == {"commit": "", "pr": ""}

    def test_sets_deny_rules(self, sample_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(sample_config)
        deny = result["permissions"]["deny"]
        assert "Bash(rm -rf /)" in deny
        assert "Bash(git reset --hard*)" in deny

    def test_sets_pretooluse_hooks(self, sample_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(sample_config)
        hooks = result["hooks"]["PreToolUse"]
        assert len(hooks) == 1
        assert hooks[0]["matcher"] == "Bash"

    def test_sets_statusline_hook(self, sample_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(sample_config)
        notification = result["hooks"]["Notification"]
        assert len(notification) == 1
        assert "statusline" in notification[0]["matcher"]

    def test_bypass_permissions_when_enabled(self, skip_perms_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(skip_perms_config)
        assert result["permissions"]["defaultMode"] == "bypassPermissions"
        assert result["skipDangerousModePermissionPrompt"] is True

    def test_no_bypass_permissions_when_disabled(self, sample_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(sample_config)
        assert "defaultMode" not in result.get("permissions", {})
        assert "skipDangerousModePermissionPrompt" not in result

    def test_preseeds_enabled_plugins(self, sample_config):
        from scad.claude_config import render_settings_json
        result = render_settings_json(sample_config)
        plugins = result["enabledPlugins"]
        assert plugins["superpowers@claude-plugins-official"] is True
        assert plugins["commit-commands@claude-plugins-official"] is True
        assert plugins["pyright-lsp@claude-plugins-official"] is True

    def test_preseeds_custom_plugins(self):
        from scad.claude_config import render_settings_json
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"plugins": ["superpowers@claude-plugins-official"]},
        )
        result = render_settings_json(config)
        plugins = result["enabledPlugins"]
        assert "superpowers@claude-plugins-official" in plugins
        assert "commit-commands@claude-plugins-official" not in plugins
