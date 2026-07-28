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


class TestMarkerTier:
    def test_marker_in_start_dir(self, tmp_path):
        (tmp_path / "design.yaml").touch()
        res = resolve(ResolveConfig(markers=("design.yaml",)), start=tmp_path)
        assert res.path == tmp_path
        assert res.matched_by == marker("design.yaml")

    def test_walks_up_to_find_the_marker(self, tmp_path):
        (tmp_path / "design.yaml").touch()
        deep = tmp_path / "units" / "wardrobe" / "versions"
        deep.mkdir(parents=True)
        res = resolve(ResolveConfig(markers=("design.yaml",)), start=deep)
        assert res.path == tmp_path

    def test_nearest_ancestor_wins_over_a_farther_one(self, tmp_path):
        """A nested project beats its enclosing one — this is why the walk is dir-major."""
        (tmp_path / "design.yaml").touch()
        inner = tmp_path / "inner"
        inner.mkdir()
        (inner / "design.yaml").touch()
        res = resolve(ResolveConfig(markers=("design.yaml",)), start=inner)
        assert res.path == inner

    def test_marker_order_breaks_ties_within_one_directory(self, tmp_path):
        (tmp_path / "design.yaml").touch()
        (tmp_path / ".viz-root").touch()
        cfg = ResolveConfig(markers=(".viz-root", "design.yaml"))
        res = resolve(cfg, start=tmp_path)
        assert res.matched_by == marker(".viz-root")

    def test_a_nearer_second_marker_beats_a_farther_first_marker(self, tmp_path):
        """Distance dominates order: dir-major, not marker-major."""
        (tmp_path / ".viz-root").touch()
        inner = tmp_path / "inner"
        inner.mkdir()
        (inner / "design.yaml").touch()
        cfg = ResolveConfig(markers=(".viz-root", "design.yaml"))
        res = resolve(cfg, start=inner)
        assert res.path == inner
        assert res.matched_by == marker("design.yaml")

    def test_no_marker_anywhere_is_unresolved(self, tmp_path):
        res = resolve(ResolveConfig(markers=("design.yaml",)), start=tmp_path)
        assert res.path is None
        assert res.matched_by == UNRESOLVED
        assert marker("design.yaml") in res.tried

    def test_nonexistent_start_does_not_raise(self, tmp_path):
        """v2.0 maps this over recorded cwds; many no longer exist."""
        res = resolve(
            ResolveConfig(markers=("design.yaml",)), start=tmp_path / "gone" / "away"
        )
        assert res.path is None
        assert res.matched_by == UNRESOLVED
