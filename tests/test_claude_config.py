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
