"""Config loading and validation."""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, model_validator

SCAD_DIR = Path.home() / ".scad"
CONFIG_DIR = SCAD_DIR / "configs"

CONFIG_TEMPLATE = """\
# scad config: {name}
# Edit this file, then run: scad build {name}

name: {name}

repos:
  # At least one repo must have workdir: true
  code:
    path: ~/path/to/your/repo
    workdir: true
    # add_dir: false    # add to Claude context with --add-dir
    # worktree: true    # create local clone (false = direct mount)
    # focus: docs/      # subdir for context prompt

# mounts:              # additional host paths to mount
#   - host: ~/data
#     container: /data

# apt_packages: []     # extra system packages

python:
  version: "3.11"
  # requirements: requirements.txt   # relative to workdir repo

claude:
  dangerously_skip_permissions: true
  # additional_flags: ""
  # claude_md: ~/CLAUDE.md   # null=auto, false=disabled, string=path
  # plugins:                 # defaults: superpowers, commit-commands, pyright-lsp
"""


class RepoConfig(BaseModel):
    path: str
    workdir: bool = False
    add_dir: bool = False
    worktree: bool = True
    focus: Optional[str] = None

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser().resolve()


class MountConfig(BaseModel):
    host: str
    container: str


class PythonConfig(BaseModel):
    version: str = "3.11"
    requirements: Optional[str] = None


SCAD_DEFAULT_PLUGINS = [
    "superpowers@claude-plugins-official",
    "commit-commands@claude-plugins-official",
    "pyright-lsp@claude-plugins-official",
]


class ClaudeConfig(BaseModel):
    dangerously_skip_permissions: bool = False
    additional_flags: Optional[str] = None
    claude_md: str | bool | None = None  # None=auto ~/CLAUDE.md, False=disabled, str=custom path
    plugins: list[str] = SCAD_DEFAULT_PLUGINS.copy()


class ScadConfig(BaseModel):
    name: str
    repos: dict[str, RepoConfig]
    mounts: list[MountConfig] = []
    apt_packages: list[str] = []
    python: PythonConfig = PythonConfig()
    claude: ClaudeConfig = ClaudeConfig()

    @property
    def base_image(self) -> str:
        return f"python:{self.python.version}-slim"

    @property
    def workdir_key(self) -> str:
        workdirs = [k for k, v in self.repos.items() if v.workdir]
        if len(workdirs) != 1:
            raise ValueError(
                f"Exactly one repo must have workdir=True, found {len(workdirs)}"
            )
        return workdirs[0]

    @model_validator(mode="after")
    def validate_workdir(self):
        workdirs = [k for k, v in self.repos.items() if v.workdir]
        if len(workdirs) != 1:
            raise ValueError(
                f"Exactly one repo must have workdir=True, found {len(workdirs)}"
            )
        return self


def _ensure_config_dir() -> None:
    """Migrate ~/.scad/templates/ to ~/.scad/configs/ if needed."""
    templates_dir = SCAD_DIR / "templates"
    if not CONFIG_DIR.exists() and templates_dir.exists():
        templates_dir.rename(CONFIG_DIR)


def list_configs(config_dir: Optional[Path] = None) -> list[str]:
    """List available config names from ~/.scad/configs/."""
    if config_dir is not None:
        # Legacy: caller passes parent dir, configs live in templates/ subdir
        templates_dir = config_dir / "templates"
        if not templates_dir.exists():
            return []
        return sorted(p.stem for p in templates_dir.glob("*.yml"))
    _ensure_config_dir()
    if not CONFIG_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIG_DIR.glob("*.yml"))


def load_config(name: str, config_dir: Optional[Path] = None) -> ScadConfig:
    """Load a config from ~/.scad/configs/<name>.yml."""
    if config_dir is not None:
        # Legacy: caller passes parent dir, configs live in templates/ subdir
        config_path = config_dir / "templates" / f"{name}.yml"
    else:
        _ensure_config_dir()
        config_path = CONFIG_DIR / f"{name}.yml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config '{name}' not found at {config_path}")
    raw = yaml.safe_load(config_path.read_text())
    return ScadConfig(**raw)
