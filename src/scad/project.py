"""scad's own project resolution — the first internal consumer of the resolver engine.

The engine (scad.resolve) knows precedence and nothing else. This module supplies
the config and the key derivation: what counts as a project root, and what its
name is. Keeping that here is what lets an external consumer use the same engine
with no scad vocabulary in it.

`project` is never stored as identity — it is recomputed on every index pass, so
redefining it means editing this file and running `scad reindex`, with no files moved.
"""

from dataclasses import dataclass
from pathlib import Path

from scad.aliases import Rule, locate
from scad.resolve import UNRESOLVED, ResolveConfig, Resolution, resolve

UNFILED = "unfiled"

# A scad config marks a project root explicitly; otherwise a git repo is one.
SCAD_PROJECT = ResolveConfig(
    markers=("scad.yml", ".scad-project"),
    use_git_root=True,
    allow_ask=False,      # reindex is headless over thousands of rows
)


def _config_for_run(run_id: str):
    """Load the scad config a run was dispatched from, or None.

    Imported lazily: scad.container pulls in docker, and this module is on the
    hot path of an index pass that must not need a Docker daemon.
    """
    try:
        from scad.container import config_name_for_run
        from scad.config import load_config

        name = config_name_for_run(run_id)
        return load_config(name) if name else None
    except Exception:
        return None


def _project_from_config(cfg) -> str:
    """A container cwd (/workspace/foo) never exists on the host, so resolve the
    config's primary repo instead. Multi-repo configs without a workdir repo fall
    back to the config name rather than guessing which repo the work belonged to.
    """
    for repo in cfg.repos.values():
        if getattr(repo, "workdir", False):
            res = resolve(SCAD_PROJECT, start=Path(repo.resolved_path))
            if res.path is not None:
                return res.path.name
    return cfg.name or UNFILED


@dataclass(frozen=True)
class ProjectResolution(Resolution):
    """The engine's answer, plus the alias rule that fired, if one did.

    A subclass rather than a field on `Resolution`: the engine is shared with
    consumers outside scad and must not know scad's vocabulary.
    """

    via_aliases: tuple[Rule, ...] = ()   # the rules that applied, in order

    @property
    def via_alias(self) -> Rule | None:
        """The first rule that applied, or None."""
        return self.via_aliases[0] if self.via_aliases else None


def project_resolution(cwd) -> tuple[str, ProjectResolution]:
    """A directory's project key, with the evidence that produced it.

    `project` is the retrieval join key, so a wrong one is worse than a missing
    one — and the answer alone gives a human nothing to check. The engine
    already returns `matched_by` and `tried`; this stops throwing them away.

    Same rule as `resolve_project`, and it is the same code path, so what
    `scad where` explains cannot drift from what the index recorded.
    """
    if cwd is None:
        return UNFILED, ProjectResolution(path=None, matched_by=UNRESOLVED)
    # A gone directory is translated through the alias file before the marker
    # walk, which from a missing directory climbs into any marked ancestor and
    # would answer with the ancestor's name. A directory that exists is never
    # translated, so no rule can redirect a live session.
    start, hops = locate(cwd)
    res = resolve(SCAD_PROJECT, start=start, interactive=False)
    return (UNFILED if res.path is None else res.path.name), ProjectResolution(
        path=res.path, matched_by=res.matched_by, tried=res.tried, via_aliases=hops)


def resolve_project(cwd, scad_run_id: str | None = None) -> str:
    """Resolve a recorded cwd to a project key. Never raises, never prompts."""
    if scad_run_id:
        cfg = _config_for_run(scad_run_id)
        return _project_from_config(cfg) if cfg is not None else UNFILED

    return project_resolution(cwd)[0]
