"""Config loading and validation tests."""

import pytest
import yaml
from pathlib import Path
from scad.config import ScadConfig, load_config, list_configs, SCAD_DEFAULT_PLUGINS


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory with a sample config."""
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    return tmp_path


@pytest.fixture
def sample_config_dict():
    """Minimal valid config as a Python dict."""
    return {
        "name": "test-project",
        "repos": {
            "code": {
                "path": "/tmp/fake-repo",
                "workdir": True,
                "branch_from": "main",
            }
        },
    }


@pytest.fixture
def sample_config_file(tmp_config_dir, sample_config_dict):
    """Write a sample config YAML file and return its path."""
    config_path = tmp_config_dir / "templates" / "test-project.yml"
    config_path.write_text(yaml.dump(sample_config_dict))
    return config_path


class TestScadConfig:
    def test_minimal_valid_config(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        assert config.name == "test-project"
        assert config.repos["code"].path == "/tmp/fake-repo"
        assert config.repos["code"].workdir is True

    def test_defaults(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        assert config.base_image == "python:3.11-slim"
        assert config.apt_packages == []
        assert config.mounts == []
        assert config.claude.dangerously_skip_permissions is False

    def test_workdir_key(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        assert config.workdir_key == "code"

    def test_no_workdir_raises(self):
        with pytest.raises(Exception):
            ScadConfig(
                name="bad",
                repos={"code": {"path": "/tmp/fake"}},
            )

    def test_multiple_workdir_raises(self):
        with pytest.raises(Exception):
            ScadConfig(
                name="bad",
                repos={
                    "a": {"path": "/tmp/a", "workdir": True},
                    "b": {"path": "/tmp/b", "workdir": True},
                },
            )

    def test_repo_defaults(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        repo = config.repos["code"]
        assert repo.branch_from == "main"
        assert repo.add_dir is False

    def test_with_mounts(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            mounts=[{"host": "/data", "container": "/mnt/data"}],
        )
        assert len(config.mounts) == 1
        assert config.mounts[0].host == "/data"

    def test_with_python_config(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            python={"version": "3.11", "requirements": "requirements.txt"},
        )
        assert config.python.version == "3.11"
        assert config.python.requirements == "requirements.txt"

    def test_with_claude_config(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"dangerously_skip_permissions": True, "additional_flags": "--verbose"},
        )
        assert config.claude.dangerously_skip_permissions is True
        assert config.claude.additional_flags == "--verbose"

    def test_with_apt_packages(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            apt_packages=["build-essential", "ffmpeg"],
        )
        assert config.apt_packages == ["build-essential", "ffmpeg"]

    def test_claude_md_default_is_none(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        assert config.claude.claude_md is None

    def test_claude_md_custom_path(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"claude_md": "~/custom/CLAUDE.md"},
        )
        assert config.claude.claude_md == "~/custom/CLAUDE.md"

    def test_claude_md_disabled(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"claude_md": False},
        )
        assert config.claude.claude_md is False

    def test_worktree_default_true(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        assert config.repos["code"].worktree is True

    def test_focus_default_none(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        assert config.repos["code"].focus is None

    def test_focus_with_value(self):
        config = ScadConfig(
            name="test",
            repos={
                "docs": {
                    "path": "/tmp/docs",
                    "workdir": True,
                    "focus": "docs/projects/lwg",
                }
            },
        )
        assert config.repos["docs"].focus == "docs/projects/lwg"

    def test_resolved_path(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake-repo", "workdir": True}},
        )
        assert config.repos["code"].resolved_path == Path("/tmp/fake-repo")

    def test_plugins_default(self, sample_config_dict):
        config = ScadConfig(**sample_config_dict)
        assert config.claude.plugins == [
            "superpowers@claude-plugins-official",
            "commit-commands@claude-plugins-official",
            "pyright-lsp@claude-plugins-official",
        ]

    def test_plugins_override(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"plugins": ["superpowers@claude-plugins-official"]},
        )
        assert config.claude.plugins == ["superpowers@claude-plugins-official"]

    def test_plugins_empty(self):
        config = ScadConfig(
            name="test",
            repos={"code": {"path": "/tmp/fake", "workdir": True}},
            claude={"plugins": []},
        )
        assert config.claude.plugins == []


class TestLoadConfig:
    def test_load_from_file(self, tmp_config_dir, sample_config_file):
        config = load_config("test-project", config_dir=tmp_config_dir)
        assert config.name == "test-project"

    def test_config_not_found(self, tmp_config_dir):
        with pytest.raises(FileNotFoundError):
            load_config("nonexistent", config_dir=tmp_config_dir)

    def test_invalid_yaml(self, tmp_config_dir):
        bad_file = tmp_config_dir / "templates" / "bad.yml"
        bad_file.write_text("name: [invalid\n")
        with pytest.raises(Exception):
            load_config("bad", config_dir=tmp_config_dir)


class TestListConfigs:
    def test_lists_yml_files(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        (templates / "alpha.yml").write_text("name: alpha\nrepos:\n  code:\n    path: /x\n    workdir: true")
        (templates / "beta.yml").write_text("name: beta\nrepos:\n  code:\n    path: /x\n    workdir: true")
        (templates / "readme.txt").write_text("not a config")
        result = list_configs(config_dir=tmp_path)
        assert result == ["alpha", "beta"]

    def test_empty_dir(self, tmp_path):
        templates = tmp_path / "templates"
        templates.mkdir()
        result = list_configs(config_dir=tmp_path)
        assert result == []

    def test_missing_dir(self, tmp_path):
        result = list_configs(config_dir=tmp_path / "nonexistent")
        assert result == []
