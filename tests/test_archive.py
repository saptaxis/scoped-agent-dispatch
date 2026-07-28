"""Tests for the append-only trace archive."""

import os
from pathlib import Path

import pytest

from scad.archive import (
    MARKER_NAME,
    archive_root,
    dest_for,
    ensure_archive_root,
)


class TestArchiveRoot:
    def test_defaults_inside_scad_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.delenv("SCAD_ARCHIVE", raising=False)
        assert archive_root() == tmp_path / ".scad" / "archive"

    def test_scad_archive_env_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "elsewhere"))
        assert archive_root() == tmp_path / "elsewhere"

    def test_ensure_creates_root_and_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        root = ensure_archive_root()
        assert root.is_dir()
        marker = root / MARKER_NAME
        assert marker.is_file()
        text = marker.read_text()
        assert "cannot be regenerated" in text

    def test_ensure_is_idempotent_and_does_not_rewrite_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        root = ensure_archive_root()
        (root / MARKER_NAME).write_text("edited by hand\n")
        ensure_archive_root()
        assert (root / MARKER_NAME).read_text() == "edited by hand\n"


class TestDestMapping:
    def test_mirrors_layout_under_label(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        src_root = tmp_path / "home" / ".claude"
        src = src_root / "projects" / "-workspace-foo" / "abc.jsonl"
        assert dest_for(src, src_root, "claude") == (
            tmp_path / "arc" / "claude" / "projects" / "-workspace-foo" / "abc.jsonl"
        )

    def test_file_directly_in_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        src_root = tmp_path / ".claude"
        assert dest_for(src_root / "history.jsonl", src_root, "claude") == (
            tmp_path / "arc" / "claude" / "history.jsonl"
        )

    def test_source_outside_root_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        with pytest.raises(ValueError):
            dest_for(tmp_path / "other" / "x.jsonl", tmp_path / "root", "claude")
