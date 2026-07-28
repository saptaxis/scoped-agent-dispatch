"""Tests for the append-only trace archive."""

import os
from pathlib import Path

import pytest

from scad.archive import (
    MARKER_NAME,
    ArchiveResult,
    archive_all,
    archive_file,
    archive_root,
    archive_run,
    archive_tree,
    dest_for,
    ensure_archive_root,
    summarize,
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


def write(p: Path, data: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


LINES = b'{"a":1}\n{"a":2}\n{"a":3}\n'


class TestCopyCreate:
    def test_first_copy_creates_dest(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES)
        dest = tmp_path / "arc" / "src.jsonl"
        res = archive_file(src, dest)
        assert res.action == "created"
        assert dest.read_bytes() == LINES
        assert res.copied == len(LINES)

    def test_partial_trailing_line_is_not_copied(self, tmp_path):
        """The rule that prevents welding two records together on the next append."""
        src = write(tmp_path / "src.jsonl", LINES + b'{"a":4')
        dest = tmp_path / "arc" / "src.jsonl"
        res = archive_file(src, dest)
        assert dest.read_bytes() == LINES          # complete lines only
        assert res.copied == len(LINES)

    def test_file_with_no_newline_at_all_copies_nothing(self, tmp_path):
        src = write(tmp_path / "src.jsonl", b'{"a":1')
        dest = tmp_path / "arc" / "src.jsonl"
        res = archive_file(src, dest)
        assert res.action == "skipped"
        assert not dest.exists()

    def test_empty_source_copies_nothing(self, tmp_path):
        src = write(tmp_path / "src.jsonl", b"")
        dest = tmp_path / "arc" / "src.jsonl"
        assert archive_file(src, dest).action == "skipped"


class TestCopySkip:
    def test_unchanged_file_is_skipped(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES)
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        res = archive_file(src, dest)
        assert res.action == "skipped"
        assert res.copied == 0

    def test_incomplete_line_still_incomplete_is_skipped(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES + b'{"a":4')
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        res = archive_file(src, dest)
        assert res.action == "skipped"
        assert dest.read_bytes() == LINES


class TestCopyAppend:
    def test_growth_appends_only_the_tail(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES)
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)

        more = b'{"a":4}\n{"a":5}\n'
        with src.open("ab") as fh:
            fh.write(more)

        res = archive_file(src, dest)
        assert res.action == "appended"
        assert res.copied == len(more)
        assert dest.read_bytes() == LINES + more

    def test_partial_line_completes_on_the_next_run(self, tmp_path):
        """A session written mid-copy: the split record arrives whole, once."""
        src = write(tmp_path / "src.jsonl", LINES + b'{"a":4')
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        assert dest.read_bytes() == LINES

        with src.open("ab") as fh:
            fh.write(b'}\n')

        archive_file(src, dest)
        assert dest.read_bytes() == LINES + b'{"a":4}\n'

    def test_append_survives_a_file_larger_than_the_prefix_window(self, tmp_path):
        big = b"".join(b'{"n":%d}\n' % i for i in range(4000))   # > 8 KiB
        src = write(tmp_path / "src.jsonl", big)
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        with src.open("ab") as fh:
            fh.write(b'{"n":"tail"}\n')
        res = archive_file(src, dest)
        assert res.action == "appended"
        assert dest.read_bytes() == big + b'{"n":"tail"}\n'


class TestCopyFork:
    def test_rewritten_source_never_overwrites_the_archive(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES)
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        original = dest.read_bytes()

        # Same path, entirely different (longer) content — a rewrite, not an append.
        write(src, b'{"z":9}\n{"z":8}\n{"z":7}\n{"z":6}\n')
        res = archive_file(src, dest)

        assert res.action == "forked"
        assert dest.read_bytes() == original          # untouched
        siblings = [p for p in dest.parent.iterdir() if p != dest]
        assert len(siblings) == 1
        assert siblings[0].read_bytes() == src.read_bytes()

    def test_truncated_source_never_shortens_the_archive(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES)
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        original = dest.read_bytes()

        write(src, b'{"a":1}\n')                       # rotated / truncated
        res = archive_file(src, dest)

        assert res.action == "forked"
        assert dest.read_bytes() == original
        assert len(original) > src.stat().st_size

    def test_fork_sidecar_is_named_by_source_mtime(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES)
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        write(src, b'{"z":9}\n' * 8)
        archive_file(src, dest)
        mtime = int(src.stat().st_mtime)
        assert (dest.parent / f"src.{mtime}.jsonl").is_file()

    def test_forking_twice_is_idempotent(self, tmp_path):
        src = write(tmp_path / "src.jsonl", LINES)
        dest = tmp_path / "arc" / "src.jsonl"
        archive_file(src, dest)
        write(src, b'{"z":9}\n' * 8)
        archive_file(src, dest)
        before = sorted(p.name for p in dest.parent.iterdir())
        archive_file(src, dest)
        assert sorted(p.name for p in dest.parent.iterdir()) == before


class TestSweeps:
    def test_tree_copies_every_jsonl_and_mirrors_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        root = tmp_path / ".claude"
        write(root / "history.jsonl", LINES)
        write(root / "projects" / "-workspace-foo" / "abc.jsonl", LINES)
        write(root / "notes.txt", b"not jsonl")

        results = archive_tree(root, "claude")

        arc = tmp_path / "arc" / "claude"
        assert (arc / "history.jsonl").read_bytes() == LINES
        assert (arc / "projects" / "-workspace-foo" / "abc.jsonl").read_bytes() == LINES
        assert not (arc / "notes.txt").exists()
        assert summarize(results)["created"] == 2

    def test_second_sweep_copies_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        root = tmp_path / ".claude"
        write(root / "history.jsonl", LINES)
        archive_tree(root, "claude")
        results = archive_tree(root, "claude")
        assert summarize(results) == {"skipped": 1}

    def test_missing_root_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        assert archive_tree(tmp_path / "absent", "codex") == []

    def test_run_is_labelled_by_run_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        run = tmp_path / ".scad" / "runs" / "demo-Jul28-1200" / "claude"
        write(run / "history.jsonl", LINES)
        write(run / "projects" / "-workspace-foo" / "abc.jsonl", LINES)

        archive_run("demo-Jul28-1200")

        arc = tmp_path / "arc" / "runs" / "demo-Jul28-1200"
        assert (arc / "history.jsonl").read_bytes() == LINES
        assert (arc / "projects" / "-workspace-foo" / "abc.jsonl").read_bytes() == LINES

    def test_run_with_no_claude_dir_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SCAD_HOME", str(tmp_path / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))
        (tmp_path / ".scad" / "runs" / "empty").mkdir(parents=True)
        assert archive_run("empty") == []

    def test_all_covers_the_three_root_kinds(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setenv("SCAD_HOME", str(home / ".scad"))
        monkeypatch.setenv("SCAD_ARCHIVE", str(tmp_path / "arc"))

        write(home / ".claude" / "history.jsonl", LINES)
        write(home / ".codex" / "sessions" / "2026" / "07" / "28" / "roll.jsonl", LINES)
        write(home / ".scad" / "runs" / "r1" / "claude" / "history.jsonl", LINES)

        archive_all()

        arc = tmp_path / "arc"
        assert (arc / "claude" / "history.jsonl").is_file()
        assert (arc / "codex" / "2026" / "07" / "28" / "roll.jsonl").is_file()
        assert (arc / "runs" / "r1" / "history.jsonl").is_file()
