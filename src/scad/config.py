"""Config loading and validation."""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, model_validator


class RepoConfig(BaseModel):
    path: str
    workdir: bool = False
    branch_from: str = "main"
    add_dir: bool = False


class MountConfig(BaseModel):
    host: str
    container: str


class PythonConfig(BaseModel):
    version: str = "3.11"
    requirements: Optional[str] = None


class ClaudeConfig(BaseModel):
    dangerously_skip_permissions: bool = False
    additional_flags: Optional[str] = None


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


def load_config(name: str, config_dir: Optional[Path] = None) -> ScadConfig:
    """Load a config from ~/.scad/templates/<name>.yml."""
    if config_dir is None:
        config_dir = Path.home() / ".scad"
    config_path = config_dir / "templates" / f"{name}.yml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config '{name}' not found at {config_path}")
    raw = yaml.safe_load(config_path.read_text())
    return ScadConfig(**raw)
