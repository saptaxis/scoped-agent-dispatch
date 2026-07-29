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

    for key, value in (
        ("GIT_CONFIG_GLOBAL", str(config_file)),
        ("GIT_CONFIG_SYSTEM", os.devnull),
    ):
        _saved_env[key] = os.environ.get(key)
        os.environ[key] = value


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
