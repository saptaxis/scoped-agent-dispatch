"""Tests for the alias file: old-prefix -> new-prefix rules that let a recorded
cwd whose directory has moved still be answered for.

Every path is built under `tmp_path`, which pytest has already resolved, so a
symlink in a test is one the test made.
"""

import os
from pathlib import Path

import pytest

from scad import aliases
from scad.aliases import (
    alias_for, current_cwd, locate, normalise, parse, rules, status,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A SCAD_HOME of the test's own, so the real ~/.scad/aliases is never read."""
    h = tmp_path / ".scad"
    h.mkdir()
    monkeypatch.setenv("SCAD_HOME", str(h))
    return h


def write_rules(home: Path, text: str) -> None:
    (home / "aliases").write_text(text)
    aliases.reset()


def one(text: str):
    got = parse(text, "aliases")
    assert len(got) == 1, got
    return got[0]


class TestParse:
    def test_a_rule_round_trips(self, tmp_path):
        r = one(f"{tmp_path}/a/x -> {tmp_path}/b/x\n")
        assert r.old == tmp_path / "a" / "x"
        assert r.new == tmp_path / "b" / "x"
        assert r.old_written == f"{tmp_path}/a/x"
        assert r.line == 1

    def test_comments_and_blank_lines_are_ignored(self, tmp_path, capsys):
        text = (f"# a directory that moved\n"
                f"\n"
                f"   \n"
                f"{tmp_path}/a -> {tmp_path}/b  # 2026-10\n")
        r = one(text)
        assert r.new == tmp_path / "b"
        assert r.line == 4
        assert capsys.readouterr().err == ""

    def test_a_path_with_spaces_keeps_them(self, tmp_path):
        r = one(f"{tmp_path}/my dir -> {tmp_path}/new dir  # moved\n")
        assert r.old == tmp_path / "my dir"
        assert r.new == tmp_path / "new dir"

    def test_a_hash_inside_a_path_is_not_a_comment(self, tmp_path):
        r = one(f"{tmp_path}/x#1 -> {tmp_path}/y\n")
        assert r.old == tmp_path / "x#1"

    @pytest.mark.parametrize("bad", [
        "{t}/a {t}/b",           # no separator
        "{t}/a->{t}/b",          # the spaces are part of the separator
        "{t}/a -> {t}/b -> {t}/c",
        "{t}/a -> ",
        " -> {t}/b",
        "relative/a -> {t}/b",
        "{t}/a -> relative/b",
    ])
    def test_a_bad_line_is_skipped_with_a_warning_and_the_rest_loads(
            self, tmp_path, capsys, bad):
        text = bad.format(t=tmp_path) + f"\n{tmp_path}/ok -> {tmp_path}/fine\n"
        got = parse(text, "aliases")
        assert [r.old for r in got] == [tmp_path / "ok"]
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err.count("\n") == 1
        assert "aliases:1: skipped:" in out.err

    def test_a_tilde_is_expanded(self):
        r = one("~/x -> ~/y\n")
        assert r.old == Path(os.path.realpath(Path.home() / "x"))

    def test_a_rule_mapping_a_path_to_itself_is_skipped(self, tmp_path, capsys):
        assert parse(f"{tmp_path}/a -> {tmp_path}/a/\n", "aliases") == []
        assert "itself" in capsys.readouterr().err

    def test_two_spellings_of_one_old_path_keep_the_first(self, tmp_path, capsys):
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        text = (f"{real}/gone -> {tmp_path}/one\n"
                f"{tmp_path}/link/gone -> {tmp_path}/two\n")
        got = parse(text, "aliases")
        assert [r.new for r in got] == [tmp_path / "one"]
        assert "same old path as line 1" in capsys.readouterr().err

    def test_a_trailing_slash_makes_no_difference(self, tmp_path):
        assert one(f"{tmp_path}/a/ -> {tmp_path}/b/\n").old == tmp_path / "a"


class TestLoad:
    def test_no_file_is_no_rules_and_no_output(self, home, capsys):
        assert rules() == ()
        assert capsys.readouterr() == ("", "")

    def test_the_file_is_read_once_until_reset(self, home, tmp_path):
        write_rules(home, f"{tmp_path}/a -> {tmp_path}/b\n")
        assert len(rules()) == 1
        (home / "aliases").write_text("")
        assert len(rules()) == 1
        aliases.reset()
        assert rules() == ()

    def test_scad_home_is_respected(self, home, tmp_path, monkeypatch):
        write_rules(home, f"{tmp_path}/a -> {tmp_path}/b\n")
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv("SCAD_HOME", str(other))
        assert rules() == ()

    def test_an_unreadable_file_warns_once_and_gives_no_rules(self, home, capsys):
        (home / "aliases").write_bytes(b"\xff\xfe /a -> /b\n")
        aliases.reset()
        assert rules() == ()
        assert capsys.readouterr().err.count("\n") == 1


class TestNormalise:
    def test_an_existing_symlink_gives_its_target(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        assert normalise(tmp_path / "link") == real

    def test_a_gone_path_under_a_symlink_keeps_its_tail(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        assert normalise(tmp_path / "link" / "gone" / "x") == real / "gone" / "x"

    def test_the_real_spelling_of_a_gone_path_is_unchanged(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        assert normalise(real / "gone" / "x") == real / "gone" / "x"

    def test_dotdot_after_a_missing_component_collapses(self, tmp_path):
        assert normalise(f"{tmp_path}/gone/../x") == tmp_path / "x"

    def test_a_dangling_symlink_is_followed(self, tmp_path):
        (tmp_path / "link").symlink_to(tmp_path / "missing")
        assert normalise(tmp_path / "link" / "x") == tmp_path / "missing" / "x"

    def test_a_symlink_loop_does_not_raise(self, tmp_path):
        (tmp_path / "a").symlink_to(tmp_path / "b")
        (tmp_path / "b").symlink_to(tmp_path / "a")
        assert isinstance(normalise(tmp_path / "a" / "x"), Path)


class TestAliasFor:
    def _rules(self, home, text):
        write_rules(home, text)

    def test_no_rules_is_none(self, home, tmp_path):
        assert alias_for(tmp_path / "old") is None

    def test_no_match_is_none(self, home, tmp_path):
        (tmp_path / "new").mkdir()
        self._rules(home, f"{tmp_path}/old -> {tmp_path}/new\n")
        assert alias_for(tmp_path / "elsewhere") is None

    def test_matching_is_by_component_not_by_string(self, home, tmp_path):
        (tmp_path / "new").mkdir()
        self._rules(home, f"{tmp_path}/foo -> {tmp_path}/new\n")
        assert alias_for(tmp_path / "foobar") is None

    def test_the_old_path_itself_translates(self, home, tmp_path):
        (tmp_path / "new").mkdir()
        self._rules(home, f"{tmp_path}/old -> {tmp_path}/new\n")
        assert alias_for(tmp_path / "old") == tmp_path / "new"

    def test_the_longest_rule_wins(self, home, tmp_path):
        (tmp_path / "broad").mkdir()
        (tmp_path / "narrow").mkdir()
        self._rules(home, f"{tmp_path}/a -> {tmp_path}/broad\n"
                          f"{tmp_path}/a/b -> {tmp_path}/narrow\n")
        assert alias_for(tmp_path / "a" / "b" / "c") == tmp_path / "narrow" / "c"
        assert alias_for(tmp_path / "a" / "d") == tmp_path / "broad" / "d"

    def test_no_fallback_when_the_longest_rules_new_side_is_gone(self, home, tmp_path):
        (tmp_path / "broad").mkdir()
        self._rules(home, f"{tmp_path}/a -> {tmp_path}/broad\n"
                          f"{tmp_path}/a/b -> {tmp_path}/missing\n")
        assert alias_for(tmp_path / "a" / "b" / "c") is None

    def test_a_tail_missing_under_the_new_side_still_translates(self, home, tmp_path):
        """A deleted worktree under the moved tree: the walk-up from the
        translated path still finds the new root."""
        (tmp_path / "new").mkdir()
        self._rules(home, f"{tmp_path}/old -> {tmp_path}/new\n")
        assert alias_for(tmp_path / "old" / "wt") == tmp_path / "new" / "wt"

    def test_rules_do_not_chain(self, home, tmp_path):
        (tmp_path / "c").mkdir()
        self._rules(home, f"{tmp_path}/a -> {tmp_path}/b\n"
                          f"{tmp_path}/b -> {tmp_path}/c\n")
        assert alias_for(tmp_path / "a" / "x") is None

    @pytest.mark.parametrize("rule_in, recorded_in", [("real", "link"), ("link", "real")])
    def test_either_spelling_matches_the_other(self, home, tmp_path, rule_in, recorded_in):
        """The shape measured on ribosome: ~/Dropbox is a symlink to
        ~/Library/CloudStorage/Dropbox, and rows hold both spellings."""
        real = tmp_path / "Library" / "CloudStorage" / "Dropbox"
        (real / "code" / "docs").mkdir(parents=True)
        link = tmp_path / "Dropbox"
        link.symlink_to(real)
        spell = {"real": real, "link": link}
        self._rules(home, f"{spell[rule_in]}/tc/docs -> {spell[rule_in]}/code/docs\n")
        got = alias_for(normalise(spell[recorded_in] / "tc" / "docs" / "sub"))
        assert got == real / "code" / "docs" / "sub"


class TestLocate:
    def test_an_existing_path_never_reads_the_file(self, home, tmp_path, monkeypatch):
        def boom(path):
            raise AssertionError("read the alias file for a live path")
        monkeypatch.setattr(aliases, "_load", boom)
        assert locate(tmp_path) == (tmp_path, None)

    def test_a_gone_path_with_a_rule_is_translated(self, home, tmp_path):
        (tmp_path / "new").mkdir()
        write_rules(home, f"{tmp_path}/old -> {tmp_path}/new\n")
        where, rule = locate(str(tmp_path / "old" / "x"))
        assert where == tmp_path / "new" / "x"
        assert rule.line == 1

    def test_a_gone_path_with_no_rule_comes_back_normalised(self, home, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "link").symlink_to(real)
        assert locate(tmp_path / "link" / "gone") == (real / "gone", None)

    def test_none_and_empty_pass_through_current_cwd(self, home):
        assert current_cwd(None) is None
        assert current_cwd("") == ""

    def test_a_container_cwd_is_unchanged(self, home):
        assert current_cwd("/workspace/x") == "/workspace/x"


class TestStatus:
    def test_ok_stale_broken(self, home, tmp_path):
        (tmp_path / "new").mkdir()
        (tmp_path / "here").mkdir()
        write_rules(home, f"{tmp_path}/gone -> {tmp_path}/new\n"
                          f"{tmp_path}/here -> {tmp_path}/new2\n"
                          f"{tmp_path}/gone2 -> {tmp_path}/missing\n")
        assert [status(r) for r in rules()] == ["ok", "stale", "broken"]

    def test_stale_wins_over_broken(self, home, tmp_path):
        (tmp_path / "here").mkdir()
        write_rules(home, f"{tmp_path}/here -> {tmp_path}/missing\n")
        assert status(rules()[0]) == "stale"
