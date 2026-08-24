"""pytest configuration for the SCED-tools suites -- the runner contract of design §5.9.

WHICH INTERPRETER RUNS THIS SUITE
    The ART interpreter (Homebrew python3, PIL 12, numpy 2.4), because the
    art-tier suites need real pixels. Stdlib-tier (3.9.6) compatibility is NOT
    covered by running the suite twice; it is covered solely by the
    `/usr/bin/python3 -m py_compile` subprocess check, which is itself a pytest
    case in `test_koreanize_config.py`.

WHY THIS FILE EXISTS AT ALL
    Before koreanize the repository had no conftest.py, pytest.ini, pyproject.toml,
    setup.cfg or tox.ini anywhere under SCED-tools/, and every suite did its own
    sys.path work in-file (test_sced_schedule.py:21-23's TESTS_DIR / SCRIPT_DIR /
    FIXTURE_DIR pattern). Nine koreanize modules across two interpreter tiers made
    that untenable. This file ADDS to that convention rather than replacing it, so
    the older suites keep working unchanged.

NO CI RUNS THIS SUITE, AND THAT IS DELIBERATE
    SCED-tools/ has no .github/ directory. The art tier is pinned to this
    machine's Homebrew triple by the golden fixture and the stdlib tier to Apple's
    /usr/bin/python3 3.9.6, which exists only on macOS -- a hosted runner
    reproduces neither, so it would report green on a suite whose two most
    load-bearing checks it had not performed. `koreanize.sh selftest` is the
    compensating control.
"""

import json
import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(TESTS_DIR)
SCRIPTS_DIR = os.path.join(TOOLS_DIR, "scripts")
KOREANIZE_DIR = os.path.join(SCRIPTS_DIR, "koreanize")
WORKSPACE_ROOT = os.path.dirname(TOOLS_DIR)
FIXTURE_DIR = os.path.join(TESTS_DIR, "fixtures")

# Matching the in-file convention rather than replacing it.
for _path in (SCRIPTS_DIR, KOREANIZE_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GOLDEN_ROOT_ENV = "KOREANIZE_GOLDEN_ROOT"
DEFAULT_GOLDEN_ROOT = os.path.expanduser("~/SCED-golden")
GOLDEN_MANIFEST_DIR = os.path.join(KOREANIZE_DIR, "data", "golden")


def pytest_configure(config):
    """Register `needs_golden`.

    An UNREGISTERED mark is a PytestUnknownMarkWarning on every collection, and
    under -W error or a future --strict-markers default it is a collection ERROR
    -- in the one suite whose entire purpose is to skip cleanly.
    """
    config.addinivalue_line(
        "markers",
        "needs_golden: requires the golden corpus declared by "
        "data/golden/<fixture>.manifest.json")
    # `slow` is registered for the same reason and under the same rule: it marks
    # the corpus SMOKE runs, which walk the live SCED-downloads tree the nightly
    # force-pushes nightly. They are not the coverage -- the synthetic cases
    # beside them are -- so `-m "not slow"` is a supported way to run the suite.
    config.addinivalue_line(
        "markers",
        "slow: walks the live SCED-downloads tree; a smoke run, not coverage")


def _read_env_file():
    """~/.config/koreanize/env, best-effort. kz_config owns the strict reader; this
    is only here so the skip predicate can find KOREANIZE_GOLDEN_ROOT."""
    path = os.path.expanduser("~/.config/koreanize/env")
    values = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _sep, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def golden_root():
    env = _read_env_file()
    root = env.get(GOLDEN_ROOT_ENV) or os.environ.get(GOLDEN_ROOT_ENV)
    return os.path.expanduser(root) if root else DEFAULT_GOLDEN_ROOT


def _tool_triple():
    triple = {"python": "%d.%d.%d" % sys.version_info[:3], "pil": None, "numpy": None}
    try:
        import PIL
        triple["pil"] = PIL.__version__
    except ImportError:
        pass
    try:
        import numpy
        triple["numpy"] = numpy.__version__
    except ImportError:
        pass
    return triple


def _declared_inputs(manifest):
    """Every declared golden input as {path, sha256}, flattened across groups.

    §3.1's golden-inputs tier is `delivered/` plus the three decoded English
    atlases, and §5.8's seed adds `init`'s three data files -- so the manifest
    groups them (`inputs{atlases_en, delivered, init}`) rather than emitting one
    flat `files[]`. Anything carrying a `path` counts, which keeps a later group
    from being silently excluded the way an unlisted `init` once was.
    """
    entries = []
    inputs = manifest.get("inputs")
    if isinstance(inputs, dict):
        for _group, value in sorted(inputs.items()):
            if isinstance(value, list):
                entries.extend(e for e in value
                               if isinstance(e, dict) and e.get("path"))
    elif isinstance(inputs, list):
        entries.extend(e for e in inputs if isinstance(e, dict) and e.get("path"))
    # The older flat spelling, still honoured so a hand-written fixture works.
    for entry in manifest.get("files") or []:
        if isinstance(entry, dict) and entry.get("path"):
            entries.append(entry)
    return entries


def require_golden(fixture="midwinter"):
    """The THREE-WAY predicate, because two of the three outcomes are not skips.

      inputs ABSENT              -> skip  ("golden corpus not present at <root>")
      inputs present but DRIFTED -> FAIL, naming the first offending path
      inputs intact, INTERPRETER
        does not match tool{}    -> skip  (the pytest side of exit 75)

    §3.1 refuses drift at exit 13 precisely because it is a restore job, and a
    suite that skipped it would be quieter about the same fact than the tool it
    tests. A single "corpus not present" predicate collapses all three and reports
    the drifted case as a skip -- exactly what §5.8's 13-versus-75 split exists to
    prevent. Never a collection error in any of the three.
    """
    manifest_path = os.path.join(GOLDEN_MANIFEST_DIR, "%s.manifest.json" % fixture)
    if not os.path.exists(manifest_path):
        pytest.skip("golden manifest not present at %s" % manifest_path)

    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    root = os.path.join(golden_root(), fixture)
    files = _declared_inputs(manifest)

    # AN EMPTY DECLARED SET IS A MANIFEST DEFECT, NOT A CLEAN CORPUS.
    #
    # This guard exists because its absence made the whole predicate below dead
    # code. The reader was `manifest.get("files")`, but the manifest §6 step 8
    # actually writes declares its inputs under `inputs{atlases_en, delivered,
    # init}` -- so `files` was absent, the list was empty, `missing` was empty,
    # the drift loop never executed, and every one of the three outcomes this
    # function exists to separate was unreachable. The case marked
    # `needs_golden` then RAN, against a corpus nothing had checked.
    #
    # Refusing an empty set is what makes the rest of this function evidence.
    if not files:
        raise AssertionError(
            "%s declares no input files -- the golden predicate cannot check "
            "anything. Expected `inputs{}` groups of {path, sha256} entries "
            "(design §3.1)." % manifest_path)

    if not os.path.isdir(root):
        pytest.skip("golden corpus not present at %s" % root)

    # Absent -> skip. Checked over every declared file first, so a partially
    # restored corpus reads as absent rather than as drift.
    missing = [f["path"] for f in files
               if not os.path.exists(os.path.join(root, f["path"]))]
    if missing:
        pytest.skip("golden corpus not present at %s (%d of %d files missing)"
                    % (root, len(missing), len(files)))

    # Present but drifted -> FAIL, naming the FIRST offending path.
    import hashlib
    for entry in files:
        path = os.path.join(root, entry["path"])
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry.get("sha256"):
            raise AssertionError(
                "golden input drifted from the manifest: %s (sha256 %s, recorded %s). "
                "This is a restore job, not a skip -- see design §3.1."
                % (path, digest.hexdigest()[:16], (entry.get("sha256") or "")[:16]))

    # Intact but the interpreter moved -> skip, the pytest side of exit 75.
    recorded = manifest.get("tool") or {}
    found = _tool_triple()
    drifted = [k for k in ("python", "pil", "numpy")
               if recorded.get(k) and recorded.get(k) != found.get(k)]
    if drifted:
        pytest.skip("art chain untested on this interpreter: %s"
                    % ", ".join("%s %s != recorded %s"
                                % (k, found.get(k), recorded.get(k)) for k in drifted))
    return root


@pytest.fixture
def golden(request):
    """Resolve the golden corpus root, applying the three-way predicate."""
    marker = request.node.get_closest_marker("needs_golden")
    fixture = (marker.args[0] if marker and marker.args else "midwinter")
    return require_golden(fixture)


@pytest.fixture(scope="session")
def workspace_root():
    return WORKSPACE_ROOT


@pytest.fixture(scope="session")
def scenario_root():
    """The live SCED-downloads scenario tree, or None when it is not checked out.

    The corpus figures §1.3 pins need a tree the nightly force-pushes nightly and
    are therefore a SMOKE run, not the coverage.
    """
    path = os.path.join(WORKSPACE_ROOT, "SCED-downloads", "decomposed", "scenario")
    return path if os.path.isdir(path) else None
