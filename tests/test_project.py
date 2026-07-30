"""Tests for scad's own project resolution — the first internal consumer of the
Phase-0 resolver engine."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from scad.project import UNFILED, resolve_project


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


class TestHostSessions:
    def test_git_repo_resolves_to_its_basename(self, tmp_path):
        repo = git_repo(tmp_path / "scoped-agent-dispatch")
        assert resolve_project(repo) == "scoped-agent-dispatch"

    def test_resolves_from_a_subdirectory(self, tmp_path):
        repo = git_repo(tmp_path / "myproj")
        deep = repo / "src" / "pkg"
        deep.mkdir(parents=True)
        assert resolve_project(deep) == "myproj"

    def test_scad_config_marker_beats_git_root(self, tmp_path):
        repo = git_repo(tmp_path / "outer")
        inner = repo / "sub"
        inner.mkdir()
        (inner / "scad.yml").write_text("name: inner\n")
        assert resolve_project(inner) == "sub"

    def test_no_repo_and_no_marker_is_unfiled(self, tmp_path):
        loose = tmp_path / "just" / "files"
        loose.mkdir(parents=True)
        assert resolve_project(loose) == UNFILED

    def test_nonexistent_cwd_is_unfiled_not_an_error(self, tmp_path):
        """v2.0 resolves recorded cwds; many directories are long gone."""
        assert resolve_project(tmp_path / "gone" / "away") == UNFILED

    def test_none_cwd_is_unfiled(self):
        assert resolve_project(None) == UNFILED

    def test_never_prompts(self, tmp_path):
        """Reindex is headless over thousands of rows."""
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            assert resolve_project(tmp_path) == UNFILED


class TestContainerSessions:
    def test_workspace_cwd_resolves_via_the_run_config(self, tmp_path):
        """A container cwd never exists on the host, so the filesystem cannot
        answer. The run id is the durable input that can."""
        repo = git_repo(tmp_path / "scoped-agent-dispatch")

        class FakeRepo:
            resolved_path = repo
            workdir = True

        class FakeConfig:
            name = "scad"
            repos = {"scoped-agent-dispatch": FakeRepo()}

        with patch("scad.project._config_for_run", return_value=FakeConfig()):
            got = resolve_project("/workspace/scoped-agent-dispatch", scad_run_id="scad-x-Jul28")
        assert got == "scoped-agent-dispatch"

    def test_falls_back_to_config_name_when_no_primary_repo(self, tmp_path):
        class FakeRepo:
            resolved_path = tmp_path / "nowhere"
            workdir = False

        class FakeConfig:
            name = "mixed-config"
            repos = {"a": FakeRepo()}

        with patch("scad.project._config_for_run", return_value=FakeConfig()):
            got = resolve_project("/workspace/a", scad_run_id="mixed-Jul28")
        assert got == "mixed-config"

    def test_unknown_run_is_unfiled(self):
        with patch("scad.project._config_for_run", return_value=None):
            assert resolve_project("/workspace/x", scad_run_id="ghost") == UNFILED


class TestResolutionEvidence:
    """`resolve_project` throws away `matched_by` and `tried`.

    `project` is the retrieval join key, so a wrong one is worse than a missing
    one — and a bare answer gives a human nothing to check it against.
    """

    def test_the_key_comes_with_the_tier_that_answered(self, tmp_path):
        from scad.project import project_resolution

        repo = git_repo(tmp_path / "myproj")
        name, res = project_resolution(repo)
        assert name == "myproj"
        assert res.matched_by == "marker:.git"

    def test_a_marker_says_which_marker(self, tmp_path):
        from scad.project import project_resolution

        (tmp_path / "scad.yml").write_text("name: x\n")
        assert project_resolution(tmp_path)[1].matched_by == "marker:scad.yml"

    def test_unfiled_carries_everything_that_was_tried(self, tmp_path):
        from scad.project import project_resolution

        loose = tmp_path / "just" / "files"
        loose.mkdir(parents=True)
        name, res = project_resolution(loose)
        assert name == UNFILED
        assert res.path is None
        assert res.tried == ("marker:scad.yml", "marker:.scad-project", "marker:.git")

    def test_a_cwd_that_is_not_a_directory_is_still_answered(self, tmp_path):
        from scad.project import project_resolution

        assert project_resolution(None)[0] == UNFILED
        assert project_resolution(tmp_path / "gone")[0] == UNFILED

    def test_resolve_project_and_the_detail_never_disagree(self, tmp_path):
        """One rule, two callers: the indexer's answer and the one `scad where`
        explains have to be the same answer."""
        from scad.project import project_resolution

        repo = git_repo(tmp_path / "same")
        assert resolve_project(repo) == project_resolution(repo)[0]
