"""The pytest home for `kz_common.TOLERANCES` and `kz_common.CONSENTS` (design §2 item 40a).

WHY THIS FILE LANDS IN v1 AND NOT EARLIER
    It can only be TOTAL over every row once `kz_typeset.py`, `kz_erase.py` and
    `kz_recompose.py` exist. The two v0 rows (`donor-choice` on `kz_triage.py`,
    `max-files-written` on `kz_langpack.py`) were exercised from §6 steps 3 and 6,
    and the five v1/v1.x rows were the counted `pending` set until part 4 landed
    their owning modules (§4.1's phase axis). Before this file, the `typeset`,
    `erase` and `recompose` rows had no test of any kind.

WHY THE ROWS CANNOT BE EXERCISED IN-PROCESS
    `kz_common` is STDLIB TIER (§5.9): it runs on Apple's /usr/bin/python3 3.9.6
    and may not import PIL or numpy. Four of the seven tolerance rows are owned by
    art-tier modules whose faults ARE pixels, so `kz_common` cannot plant them --
    it cannot import the thing that would raise. Each row is therefore driven as a
    SUBPROCESS against its own declared owning module, on that module's own
    interpreter tier, which is also the only way the stdlib/art split is honoured
    rather than asserted.

WHAT A ROW IS AND WHY EACH FIELD IS CHECKED
    A TOLERANCES/CONSENTS row is a promise that a named predicate fires on a named
    fault with a named exit code, in a named module, at a named phase. Every one of
    those is load-bearing, and `assert_tables_total()` already checks the fields are
    PRESENT. What it cannot check is that the promise is KEPT -- that the fault
    named in `selftest_fault` actually exists in the module and actually fires.
    That is this file's job.
"""

import os
import subprocess
import sys

import pytest

import kz_common as kc

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(TESTS_DIR)
PACKAGE_DIR = os.path.join(TOOLS_DIR, "scripts", "koreanize")

#: The stdlib-tier interpreter, per §5.9. A stdlib-tier module MUST be driven on
#: 3.9.6 rather than on the art interpreter, because running it on 3.14 would pass
#: while telling us nothing about the tier it actually ships to.
STDLIB_PY = "/usr/bin/python3"

#: Row name -> `--selftest` fault key, for the ONE row where they differ.
#:
#: Six of the seven tolerance rows and the single consent row name their fault
#: exactly as the row is named, so `--selftest <row.name>` is the convention. The
#: `max-files-written` row breaks it: `kz_langpack.py` calls that fault `cap`.
#: The divergence is recorded here as a NAMED exception rather than absorbed by a
#: tolerant lookup, and `test_the_override_table_is_minimal` fails the moment a
#: second row needs one -- so the convention stays checkable instead of decaying
#: into "look it up somehow".
ROW_FAULT_OVERRIDES = {"max-files-written": "cap"}

ALL_ROWS = tuple(kc.TOLERANCES) + tuple(kc.CONSENTS)


def _fault_for(row):
    return ROW_FAULT_OVERRIDES.get(row.name, row.name)


def _interpreter_for(row):
    return STDLIB_PY if row.module in kc.STDLIB_TIER else sys.executable


def _run_selftest(row, fault):
    return subprocess.run(
        [_interpreter_for(row), os.path.join(PACKAGE_DIR, row.module),
         "--selftest", fault],
        capture_output=True, text=True)


def _row_id(row):
    return "%s:%s" % (row.module.replace("kz_", "").replace(".py", ""), row.name)


# ---------------------------------------------------------------------------
# 1. Every row's named fault fires
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row", ALL_ROWS, ids=[_row_id(r) for r in ALL_ROWS])
def test_row_named_fault_fires(row):
    """The row's promise, kept: the owning module runs the named fault green.

    A module's `--selftest <fault>` exits 0 when the fault FIRED as the row
    declares -- the fault is planted by the selftest and the predicate is required
    to catch it. So a non-zero rc here means either the predicate did not fire on
    the fault it was written for, or the fault is gone.
    """
    result = _run_selftest(row, _fault_for(row))
    assert result.returncode == 0, (
        "%s --selftest %s exited %d\nstdout:\n%s\nstderr:\n%s"
        % (row.module, _fault_for(row), result.returncode,
           result.stdout, result.stderr))


# ---------------------------------------------------------------------------
# 2. Anti-vacuity -- the fault name is DISPATCHED, not ignored
# ---------------------------------------------------------------------------

_OWNING_MODULES = sorted({r.module for r in ALL_ROWS})


@pytest.mark.parametrize("module", _OWNING_MODULES)
def test_an_unknown_fault_is_refused(module):
    """Without this, section 1 proves nothing.

    If `--selftest <fault>` ignored its argument and ran the whole suite, every
    case above would pass on a fault name that does not exist -- including a row
    whose predicate had been deleted. Requiring an unknown name to FAIL is what
    makes the parametrized pass in section 1 evidence about that specific fault.

    This is the same rule `atlas-prompt/build-package.py:456-459` states for the
    faults themselves: "A check that cannot fail is a defect in this project's
    history, not a nicety."
    """
    row = next(r for r in ALL_ROWS if r.module == module)
    result = _run_selftest(row, "__no_such_fault__")
    assert result.returncode != 0, (
        "%s --selftest __no_such_fault__ exited 0; the fault argument is not "
        "being dispatched, so every other case in this file is vacuous" % module)


# ---------------------------------------------------------------------------
# 3. The totality and disjointness assertions (§4.1)
# ---------------------------------------------------------------------------

def test_tables_are_total():
    """Every row carries a phase, an owning module, an exit code and a named fault,
    and exactly the `hard_cap` rows lack a flag -- so the absence is a declaration
    rather than an omission."""
    assert kc.assert_tables_total() == []


def test_tables_are_disjoint():
    """A flag in both tables would mean a human DECISION recorded as a THRESHOLD.
    Exit 22 says "a measurement exceeded a bound, decide whether to accept it";
    exit 26 says "nothing is wrong; a person must authorize this"."""
    assert not (kc.tolerance_flags() & kc.consent_flags())


def test_every_owning_module_exists():
    """v1's completion predicate for the phase axis: with part 4 landed, no row's
    owning module is missing, so `pending_rows()` is empty in both tables.

    `test_koreanize_config.py::test_every_pending_row_is_counted_rather_than_skipped`
    holds the other direction -- that the COUNTING mechanism still works once this
    is true -- so neither test can decay into asserting the project's current state.
    """
    for row in ALL_ROWS:
        assert os.path.exists(os.path.join(PACKAGE_DIR, row.module)), row.module
    pending = kc.pending_rows()
    assert pending["TOLERANCES"] == [] and pending["CONSENTS"] == []


# ---------------------------------------------------------------------------
# 4. The override table stays minimal
# ---------------------------------------------------------------------------

def test_the_override_table_is_minimal():
    """Every override must be needed, and every row not overridden must resolve by
    its own name. A stale entry would silently redirect a row to a fault that is no
    longer the one it names."""
    assert set(ROW_FAULT_OVERRIDES) <= {r.name for r in ALL_ROWS}
    for name, fault in ROW_FAULT_OVERRIDES.items():
        row = next(r for r in ALL_ROWS if r.name == name)
        assert _run_selftest(row, name).returncode != 0, (
            "%s now accepts --selftest %s; drop the override" % (row.module, name))
        assert _run_selftest(row, fault).returncode == 0
