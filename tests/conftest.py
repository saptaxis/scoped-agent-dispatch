"""Suite-wide fixtures.

The one thing here is git isolation, and it exists because the suite was not
hermetic: dozens of tests build fixture repos with `git init` followed by
`git commit`, which git refuses outright when it cannot resolve an identity.
On a machine with no global `user.email` — a fresh checkout, CI, or the
deep-history machine — 17 tests failed for a reason that had nothing to do
with the code under test.

Environment variables are not a workaround. `GIT_AUTHOR_EMAIL` lets a commit
succeed, but `git config --get user.email` still reports nothing, and that is
the lookup `container._git_identity()` actually performs when propagating
identity into a clone. The fix has to put the identity somewhere git's config
machinery will read.

So the suite points `GIT_CONFIG_GLOBAL` at a file it owns. That cuts both ways
deliberately: it supplies an identity where none exists, and it also ignores
the developer's real one, so the suite behaves identically everywhere. System
config is neutralized for the same reason.

Tests that need to exercise the *absence* of an identity patch
`scad.container._git_identity` (see `TestGitIdentityPropagation`), so they are
unaffected by what is set here.
"""

import os
import tempfile
from pathlib import Path

import pytest

from scad.memos import SESSION_ID_ENV

# Written into the throwaway global config. `.invalid` is reserved by RFC 2606
# and can never resolve, so a leaked commit is traceable to the test suite.
GIT_TEST_NAME = "Scad Test Suite"
GIT_TEST_EMAIL = "tests@scad.invalid"

_git_config_dir: tempfile.TemporaryDirectory | None = None

# Restored in pytest_unconfigure so an in-process pytest run (or a plugin that
# shells out afterwards) does not inherit the isolation.
_saved_env: dict[str, str | None] = {}


def pytest_configure(config):
    """Redirect git's global and system config before any test runs.

    A `pytest_configure` hook is early enough: it fires before collection, and
    every fixture repo is built inside a test or fixture, all of which inherit
    `os.environ` through `subprocess`.
    """
    global _git_config_dir

    _git_config_dir = tempfile.TemporaryDirectory(prefix="scad-tests-gitconfig-")
    config_file = Path(_git_config_dir.name) / "gitconfig"
    config_file.write_text(
        "[user]\n"
        f"\tname = {GIT_TEST_NAME}\n"
        f"\temail = {GIT_TEST_EMAIL}\n"
        # A fixture repo must never inherit a signing requirement — there is no
        # key in a test environment, and every commit would fail.
        "[commit]\n"
        "\tgpgsign = false\n"
    )

    # scad's own home and archive, for the same reason: a test that sets
    # neither read the developer's real launch records and memo store, and
    # passed or failed by what happened to be on the machine. A test that
    # wants its own sets it with monkeypatch, which overrides this.
    scad_home = Path(_git_config_dir.name) / "scad-home"
    for key, value in (
        ("GIT_CONFIG_GLOBAL", str(config_file)),
        ("GIT_CONFIG_SYSTEM", os.devnull),
        ("SCAD_HOME", str(scad_home)),
        ("SCAD_ARCHIVE", str(scad_home / "archive")),
    ):
        _saved_env[key] = os.environ.get(key)
        os.environ[key] = value


@pytest.fixture(autouse=True)
def _no_inherited_session_ids(monkeypatch):
    """Unset every agent's session variable for the duration of each test.

    The suite is frequently run *from inside* one of the agents it models, and
    a real `CLAUDE_CODE_SESSION_ID` in the ambient environment would silently
    satisfy `current_session_id()` — so the transcript-scanning tests would
    stop testing the scan without ever going red. Tests that want a variable
    set say so with `monkeypatch.setenv`, which overrides this.
    """
    for var in SESSION_ID_ENV.values():
        monkeypatch.delenv(var, raising=False)


def pytest_addoption(parser):
    parser.addoption("--run-vm", action="store_true", default=False,
                     help="Also run tests marked `vm`, which use the real scad VM.")


def pytest_collection_modifyitems(config, items):
    """The real VM is its own suite, run on purpose: `pytest --run-vm -m vm`.

    It restarts the VM and stops every container in it, so it is never part of
    an ordinary run.
    """
    if config.getoption("--run-vm"):
        return
    skip = pytest.mark.skip(reason="uses the real scad VM; run with --run-vm")
    for item in items:
        if "vm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_real_vm(request, monkeypatch):
    """No test may run colima or reach the Docker daemon, unless it is marked
    `vm` (and so runs only with --run-vm). A test that needs a fake patches
    `scad.vm._colima` or the Docker client itself, which overrides this.

    Without it, a test that left `reconcile_vm_mounts` unmocked restarted the
    developer's real scad VM on every run once SCAD_HOME pointed outside
    $HOME, and wrote each run's temporary directory into its colima.yaml.
    """
    if request.node.get_closest_marker("vm"):
        return

    def refuse(*args):
        raise AssertionError(f"a test tried to run the real `colima {' '.join(args)}`")
    monkeypatch.setattr("scad.vm._colima", refuse)

    # And no test may reach the real Docker daemon: the same unmocked path
    # listed the developer's running scad containers and restarted them after
    # the VM came back. Tests that want a client patch these themselves.
    def no_daemon(*args, **kwargs):
        raise AssertionError("a test tried to reach the real Docker daemon")
    monkeypatch.setattr("docker.DockerClient", no_daemon)
    monkeypatch.setattr("docker.from_env", no_daemon)


@pytest.fixture(autouse=True)
def _fresh_aliases():
    """The alias rules are read once per process and cached on the file's path.

    Without a reset, a rule set loaded under one test's SCAD_HOME, or loaded
    before a test moved a directory, would still be answering in the next.
    """
    from scad import aliases

    aliases.reset()
    yield
    aliases.reset()


def pytest_unconfigure(config):
    global _git_config_dir

    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _saved_env.clear()

    if _git_config_dir is not None:
        _git_config_dir.cleanup()
        _git_config_dir = None
