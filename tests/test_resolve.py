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
