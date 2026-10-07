"""The real VM is not part of the regular suite.

A test that needs it is marked `vm` and runs only with `--run-vm`; every other
test is refused colima and the Docker daemon (see conftest). Without the
refusal, one unmocked call restarted the developer's VM on every run.
"""

pytest_plugins = ["pytester"]

CONFTEST = (__import__("pathlib").Path(__file__).parent / "conftest.py").read_text()

MARKED = '''
import pytest

@pytest.mark.vm
def test_needs_the_vm():
    import scad.vm
    assert scad.vm._colima.__name__ != "refuse"
'''

UNMARKED = '''
import pytest, scad.vm

def test_is_refused():
    with pytest.raises(AssertionError, match="real `colima"):
        scad.vm._colima("status")
'''


def test_a_vm_test_is_skipped_by_default(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_marked=MARKED)
    pytester.runpytest_subprocess().assert_outcomes(skipped=1)


def test_run_vm_runs_it_without_the_refusal(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_marked=MARKED)
    pytester.runpytest_subprocess("--run-vm").assert_outcomes(passed=1)


def test_an_unmarked_test_is_refused_colima_even_with_run_vm(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_unmarked=UNMARKED)
    pytester.runpytest_subprocess("--run-vm").assert_outcomes(passed=1)
