"""Tests for the generic resolver engine."""

from pathlib import Path

import pytest

from scad.resolve import (
    ASK,
    EXPLICIT,
    UNRESOLVED,
    ResolveConfig,
    Resolution,
    marker,
)


class TestTypes:
    def test_config_defaults_to_every_tier_disabled(self):
        cfg = ResolveConfig()
        assert cfg.markers == ()
        assert cfg.use_git_root is False
        assert cfg.allow_ask is False

    def test_config_is_frozen(self):
        cfg = ResolveConfig(markers=("design.yaml",))
        with pytest.raises(Exception):
            cfg.markers = ()

    def test_resolution_is_frozen(self):
        res = Resolution(path=Path("/tmp"), matched_by=EXPLICIT, tried=(EXPLICIT,))
        with pytest.raises(Exception):
            res.path = None

    def test_vocabulary_values_are_the_public_contract(self):
        assert EXPLICIT == "explicit"
        assert ASK == "ask"
        assert UNRESOLVED == "unresolved"

    def test_marker_builder_encodes_the_filename(self):
        assert marker("design.yaml") == "marker:design.yaml"
        assert marker(".git") == "marker:.git"


from scad.resolve import resolve


class TestExplicitTier:
    def test_explicit_wins_and_is_recorded(self, tmp_path):
        res = resolve(ResolveConfig(), explicit=tmp_path)
        assert res.path == tmp_path
        assert res.matched_by == EXPLICIT
        assert res.tried == (EXPLICIT,)

    def test_explicit_is_expanded_and_resolved(self, tmp_path):
        nested = tmp_path / "a" / ".." / "a"
        (tmp_path / "a").mkdir()
        res = resolve(ResolveConfig(), explicit=nested)
        assert res.path == (tmp_path / "a").resolve()

    def test_explicit_beats_a_marker_in_cwd(self, tmp_path):
        (tmp_path / "design.yaml").touch()
        other = tmp_path / "other"
        other.mkdir()
        res = resolve(
            ResolveConfig(markers=("design.yaml",)), start=tmp_path, explicit=other
        )
        assert res.path == other
        assert res.matched_by == EXPLICIT

    def test_nothing_configured_resolves_to_nothing(self, tmp_path):
        res = resolve(ResolveConfig(), start=tmp_path)
        assert res.path is None
        assert res.matched_by == UNRESOLVED

    def test_never_guesses_a_default(self, tmp_path):
        """No '.' or '~' fallback — an unconfigured resolve returns None, not cwd."""
        res = resolve(ResolveConfig(), start=tmp_path)
        assert res.path is None
