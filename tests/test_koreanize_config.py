"""kz_config.py + kz_common.py -- the foundation of design §3.2, §3.1, §5.9, §5.10.

The most load-bearing case in this file is `test_stdlib_tier_compiles_on_apple_python`:
§5.9 assertion (2). The suite runs on the ART interpreter, so nothing else here
would ever notice a `match` statement, a PEP-604 annotation evaluated at runtime,
or a 3.10+ builtin written into a stdlib-tier module -- all of which are syntax or
name errors on Apple's 3.9.6 and all of which would ship green under an
import-only AST scan.
"""

import json
import os
import subprocess
import sys

import pytest

import kz_common as kc
import kz_config as kz
import kz_init as ki

WORKSPACE = kc.WORKSPACE_ROOT
PLATFORM_PYTHON = kc.PLATFORM_PYTHON


# ---------------------------------------------------------------------------
# §5.9 -- the two tier assertions, which are the point of the whole tier
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(PLATFORM_PYTHON),
                    reason="Apple's %s is not present" % PLATFORM_PYTHON)
def test_stdlib_tier_compiles_on_apple_python():
    """Assertion (2) of §5.9, as a pytest case rather than only as a CLI selftest.

    An import-only AST scan cannot catch `match`, a PEP-604 `X | Y` annotation
    evaluated at runtime, or a 3.10+ builtin. This runs the real subprocess.
    """
    findings = kc.compile_stdlib_tier()
    assert findings == [], "\n".join(findings)


def test_stdlib_tier_imports_stay_inside_the_allowlist():
    """Assertion (1): the AST scan over the DECLARED subject set.

    The allowlist is closed in the other direction too -- an import of any
    koreanize module outside {kz_common, kz_config, sced_io} fails, which is what
    makes the one-way kz_decide -> kz_config dependency a checked property rather
    than a convention.
    """
    assert kc.scan_stdlib_imports() == []


def test_the_two_tier_checks_police_the_same_files():
    """Two checks disagreeing about WHICH files they police is the shape that let
    AI_STAGE_MAP sit in an unpoliced art-tier module while a policed stdlib-tier
    one imported it at module scope."""
    assert kc.STDLIB_TIER == ("kz_common.py", "kz_config.py", "kz_langpack.py",
                              "kz_verify.py", "sced_io.py")
    declared = {name for name, _path in kc._stdlib_tier_paths()}
    assert declared == set(kc.STDLIB_TIER)
    # Only ABSENCE is pending, and it is never silent.
    assert set(kc.stdlib_tier_pending()) <= declared


def test_kz_init_is_art_tier_and_is_not_policed_as_stdlib():
    """kz_init imports PIL (lazily) and is deliberately NOT in STDLIB_TIER."""
    assert "kz_init.py" not in kc.STDLIB_TIER


def test_kz_config_does_not_import_pil_or_numpy_at_module_level():
    """kz_common/kz_config are importable by BOTH tiers, which is what forces the
    font-identity check to parse the sfnt `name` table with struct."""
    for module in ("kz_common.py", "kz_config.py"):
        source = open(os.path.join(kc.PACKAGE_DIR, module), encoding="utf-8").read()
        assert "\nimport PIL" not in source
        assert "\nfrom PIL" not in source
        assert "\nimport numpy" not in source


# ---------------------------------------------------------------------------
# §3.2 -- the stage universe and the S-id map
# ---------------------------------------------------------------------------

def test_stage_partition_is_22_and_total():
    assert len(kz.STAGES) == len(set(kz.STAGES)) == 22
    assert set(kz.AI_STAGE_MAP) == {"S1", "S2", "S3", "S4", "S5", "S6", "S7"}
    assert len(set(kz.AI_STAGE_MAP.values())) == 7
    assert set(kz.AI_STAGE_MAP.values()) <= set(kz.STAGES)


def test_ai_stage_map_lives_in_kz_config_not_kz_decide():
    """§3.2: kz_config owns the only S-id <-> stage-name mapping in the tool, and
    the dependency is one-way."""
    assert hasattr(kz, "AI_STAGE_MAP")
    source = open(os.path.join(kc.PACKAGE_DIR, "kz_config.py"), encoding="utf-8").read()
    assert "import kz_decide" not in source


def test_predecessor_exempt_is_exactly_revert():
    """K20: without the exemption `revert` refuses at 72 in precisely the
    situation §8.4 invokes it for."""
    assert kz.PREDECESSOR_EXEMPT == frozenset({"revert"})


def test_data_subdirs_covers_all_seven_producers():
    """An exemption named for one of seven would leave the other six unowned."""
    assert set(kz.DATA_SUBDIRS) == {"scenarios", "terms", "text", "layouts",
                                    "icons", "locks", "golden"}


# ---------------------------------------------------------------------------
# scenario.json -- schema, the pin, and the cross-checks
# ---------------------------------------------------------------------------

def make_cfg(**overrides):
    fonts, _counts, _unresolved = ki.resolve_fonts([], {}, WORKSPACE)
    cfg = ki.build_scenario_config(
        slug="s", scenario_name="S", source_dir="d", source_tree_sha256="x",
        pack="Korean - Campaigns", container_guid="g", container_stem="S.aaaaaa",
        arkham_prefixes=["71"], atlases=[], shared_backs=[], fonts=fonts,
        run_dir=".am/koreanize/s", counts={"arkham_ids": 12})
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


def test_a_valid_generated_config_passes():
    assert kz.validate(make_cfg()) is not None


def test_a_missing_top_level_key_is_exit_4():
    cfg = make_cfg()
    del cfg["fonts"]
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg)
    assert excinfo.value.code == kc.EXIT_GUARD


def test_a_hand_edit_after_init_breaks_the_pin():
    """config_sha256 is what stops a hand-edit from silently retargeting the tool."""
    cfg = make_cfg()
    cfg["source_dir"] = "SCED-downloads/decomposed/scenario/Something Else"
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg)
    assert excinfo.value.code == kc.EXIT_GUARD
    assert "config_sha256" in str(excinfo.value)


def test_the_pin_is_a_property_of_content_not_of_key_order():
    """Serialised with sorted keys and a fixed separator, so the digest survives
    whichever writer last touched the file."""
    cfg = make_cfg()
    reordered = dict(reversed(list(cfg.items())))
    assert kz.compute_config_sha256(reordered) == kz.compute_config_sha256(cfg)


def test_the_three_stage_lists_must_partition_stages():
    cfg = make_cfg()
    cfg["ai"]["neutral_stages"] = ["init", "source", "check"]     # drops `erase`
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD


def test_required_stages_must_equal_the_ai_stage_map_values():
    cfg = make_cfg()
    cfg["ai"]["required_stages"] = sorted(set(cfg["ai"]["required_stages"]) - {"audit"})
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD


def test_a_batch_budget_that_cannot_close_is_exit_4():
    """Both inequalities bind max_calls <= 6. The previous table declared 12 for
    S2 against a $40 stage budget -- a ceiling it advertised and could not reach."""
    cfg = make_cfg()
    cfg["ai"]["batch"]["S2"]["max_calls"] = 12
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD


@pytest.mark.parametrize("key", ["timeout_s", "call_budget_s", "stage_wall_clock_s"])
@pytest.mark.parametrize("bad", ["900s", "5m", 0, -1, None, "", 10.5, True])
def test_a_seconds_knob_that_is_not_a_positive_integer_is_exit_4(key, bad):
    """Presence was all `_REQUIRED_AI` proved. A string then reached
    `calls * call_budget` and left a TypeError -- a defect in the tool -- where a
    controlled exit-4 refusal belongs, and a 0 or a negative satisfied every
    inequality silently and was forwarded to the shim as-is. Unlike an
    environment knob, scenario.json is hash-pinned and has an author to tell.
    """
    cfg = make_cfg()
    cfg["ai"][key] = bad
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD
    text = str(excinfo.value)
    assert ("ai.%s" % key) in text and "scenario.json" in text


@pytest.mark.parametrize("key", ["max_budget_usd_per_call", "max_budget_usd_per_stage"])
@pytest.mark.parametrize("bad", ["sixty", 0, -1, None, "", True])
def test_a_usd_knob_that_is_not_a_positive_amount_is_exit_4(key, bad):
    """Money rather than seconds, so a fraction is legal -- but zero, a negative
    and an unparseable string are not an amount to bound a stage by."""
    cfg = make_cfg()
    cfg["ai"][key] = bad
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD
    assert ("ai.%s" % key) in str(excinfo.value)


def test_a_fractional_usd_budget_is_still_accepted():
    """The refusal above is about shape, not about integrality: the USD knobs are
    deliberately not forced through the integer helper."""
    cfg = make_cfg()
    cfg["ai"]["max_budget_usd_per_call"] = 7.5
    cfg["ai"]["max_budget_usd_per_stage"] = 45.0
    assert kz.validate(cfg, check_pin=False) is not None


def test_the_config_refusal_and_the_env_knob_fallback_share_one_predicate():
    """`_positive_int` falls back because an environment knob has no author to
    tell; a pinned config document refuses. Both must agree on what is BAD, so
    the two read the same predicate."""
    for raw in ("45", "900", 780):
        assert kz._is_positive_int(raw) is True
        assert kz._positive_int(raw, 30, "TEST") == int(raw)
    for raw in ("60s", "0", "-1", "", None, "10.5"):
        assert kz._is_positive_int(raw) is False
        assert kz._positive_int(raw, 30, "TEST") == 30


def test_a_timeout_longer_than_the_call_budget_is_exit_4():
    """`call_budget_s` gates a call only BEFORE it starts, so `timeout_s` is the
    bound that actually governs an in-flight call. A scenario shortening
    `call_budget_s` for headroom while leaving `timeout_s` at 780 can have one
    call run the full 780 s, discovered only between batches (§3.7)."""
    cfg = make_cfg()
    cfg["ai"]["call_budget_s"] = 420           # timeout_s stays at 780
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD
    text = str(excinfo.value)
    assert "timeout_s" in text and "call_budget_s" in text
    assert "780" in text and "420" in text


def test_a_timeout_equal_to_the_call_budget_closes():
    """The assertion is `timeout_s <= call_budget_s`; the boundary is legal."""
    cfg = make_cfg()
    cfg["ai"]["timeout_s"] = 420
    cfg["ai"]["call_budget_s"] = 420
    assert kz.validate(cfg, check_pin=False) is not None


def test_a_missing_universe_size_cannot_pass_as_a_declared_null():
    """The carve-out is explicit so an ABSENT universe_size is a finding."""
    cfg = make_cfg()
    del cfg["ai"]["batch"]["S6"]["universe_size"]
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD
    assert "universe_size" in str(excinfo.value)


def test_gate_names_must_be_real_stages():
    cfg = make_cfg()
    cfg["gates"] = {"nosuchstage": "required"}
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD


# ---------------------------------------------------------------------------
# §3.1 -- guard.data_root, the ONE exemption
# ---------------------------------------------------------------------------

def test_data_root_is_evaluated_before_forbidden():
    """guard.forbidden lists "SCED-tools/", so a data_root write evaluated against
    forbidden first would be refused by the rule its exemption carves out."""
    cfg = make_cfg()
    dest = os.path.join(WORKSPACE, cfg["guard"]["data_root"], "scenarios", "s.scenario.json")
    assert kz.check_write_paths(cfg, [dest]) == []


def test_a_data_root_write_outside_the_seven_subdirs_is_refused():
    cfg = make_cfg()
    dest = os.path.join(WORKSPACE, cfg["guard"]["data_root"], "elsewhere", "x.json")
    findings = kz.check_write_paths(cfg, [dest])
    assert findings and "elsewhere" in findings[0]


def test_an_arbitrary_write_under_sced_tools_is_refused():
    cfg = make_cfg()
    dest = os.path.join(WORKSPACE, "SCED-tools", "scripts", "evil.py")
    assert kz.check_write_paths(cfg, [dest]) != []


def test_a_write_into_the_scenario_tree_is_refused():
    """decomposed/scenario/ is a read-only input tier."""
    cfg = make_cfg()
    dest = os.path.join(WORKSPACE, "SCED-downloads", "decomposed", "scenario", "x.json")
    assert kz.check_write_paths(cfg, [dest]) != []


def test_write_data_refuses_an_unknown_subdirectory():
    cfg = make_cfg()
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.write_data(cfg, "nosuchsubdir", "x.json", {})
    assert excinfo.value.code == kc.EXIT_GUARD


def test_write_data_refuses_a_traversing_relpath():
    cfg = make_cfg()
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.write_data(cfg, "scenarios", "../../../etc/passwd", {})
    assert excinfo.value.code == kc.EXIT_GUARD


def test_max_files_written_is_a_hard_cap_with_boundaries(tmp_path):
    """§4.1: 199 / 200 / 201 -- pass / pass / refuse. Exit 4, and "nothing was
    read" is still true because the count is derived from the PLAN."""
    cfg = make_cfg()
    root = cfg["guard"]["write_roots"][0]
    plan = [os.path.join(WORKSPACE, root, "f%03d.json" % i) for i in range(201)]
    kz.assert_write_paths(cfg, plan[:199])
    kz.assert_write_paths(cfg, plan[:200])
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.assert_write_paths(cfg, plan[:201])
    assert excinfo.value.code == kc.EXIT_GUARD


def test_dry_run_destination_must_be_under_the_scratch_root():
    """kz_langpack's outputs are under guard.write_roots, so a SIBLING of one of
    them lands INSIDE the guarded tree and exit 4 could never fire on it."""
    cfg = make_cfg()
    good = os.path.join(WORKSPACE, cfg["run_dir"], "dry-run", "init", "x.json")
    assert kz.assert_dry_run_dest(cfg, "init", good)
    bad = os.path.join(WORKSPACE, "SCED-downloads", "decomposed", "language-pack", "x.json")
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.assert_dry_run_dest(cfg, "init", bad)
    assert excinfo.value.code == kc.EXIT_GUARD


# ---------------------------------------------------------------------------
# §5.10 -- the nightly interlock
# ---------------------------------------------------------------------------

def test_the_union_window_covers_both_local_starts():
    inside_0217, _w = kz.in_schedule_window(now_min=2 * 60 + 17)
    inside_0247, _w = kz.in_schedule_window(now_min=2 * 60 + 47)
    inside_noon, _w = kz.in_schedule_window(now_min=12 * 60)
    assert inside_0217 and inside_0247
    assert not inside_noon


def test_the_owner_line_matches_the_drivers_format():
    """`repo=koreanize` is the operator-legible signature sced-run-now.sh --status
    prints, and is the whole diagnosis of a collision."""
    fields = kz.parse_owner(kz._owner_text())
    assert set(fields) >= {"pid", "repo", "started", "host"}
    assert fields["repo"] == "koreanize"


def test_lock_acquire_and_release_round_trip(tmp_path):
    lock = kz.NightlyLock(lock_dir=str(tmp_path / "daily-sync.lock"), heartbeat_s=0)
    lock.acquire()
    assert lock.held
    assert kz.probe_lock(lock.owner_path)["held"] is True
    ok, state, _msg = lock.release()
    assert ok and state == "present"
    assert kz.probe_lock(lock.owner_path)["held"] is False


def test_acquiring_a_held_lock_is_exit_4(tmp_path):
    lock_dir = str(tmp_path / "daily-sync.lock")
    first = kz.NightlyLock(lock_dir=lock_dir, heartbeat_s=0).acquire()
    try:
        second = kz.NightlyLock(lock_dir=lock_dir, heartbeat_s=0)
        with pytest.raises(kc.KzRefusal) as excinfo:
            second.acquire()
        assert excinfo.value.code == kc.EXIT_GUARD
    finally:
        first.release()


def test_an_ownerless_lock_directory_reads_as_a_takeover_in_progress(tmp_path):
    """NOT a corrupted lock: the driver's stale-takeover rm -f's the owner at
    daily-sync-local.sh:1311 and does not write its own until :1319."""
    lock_dir = tmp_path / "daily-sync.lock"
    lock_dir.mkdir()
    probe = kz.probe_lock(str(lock_dir / "owner"))
    assert probe["held"] is True
    assert probe["owner"] is None
    assert "takeover" in probe["note"]


def test_release_does_not_delete_someone_elses_owner_file(tmp_path):
    """Deleting it would destroy the other side's owner file and leave two writers
    believing they hold the lock."""
    lock_dir = str(tmp_path / "daily-sync.lock")
    lock = kz.NightlyLock(lock_dir=lock_dir, heartbeat_s=0).acquire()
    kc.atomic_write_text(lock.owner_path, kz._owner_text(pid=999999))
    ok, state, msg = lock.release()
    assert not ok and state == "present"
    assert "taken over" in msg
    assert os.path.exists(lock.owner_path)


def test_release_treats_an_absent_owner_as_a_takeover(tmp_path):
    lock_dir = str(tmp_path / "daily-sync.lock")
    lock = kz.NightlyLock(lock_dir=lock_dir, heartbeat_s=0).acquire()
    os.unlink(lock.owner_path)
    ok, state, msg = lock.release()
    assert not ok and state == "absent_owner"
    assert "takeover" in msg


# ---------------------------------------------------------------------------
# Knobs, margins, fonts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("45", 45), ("60s", 30), ("5m", 30), ("0", 30), ("-1", 30), ("", 30), (None, 30),
])
def test_a_bad_knob_collapses_to_the_default_rather_than_to_zero(raw, expected):
    """An unvalidated bad value used to COLLAPSE the bound rather than widen it."""
    assert kz._positive_int(raw, 30, "TEST") == expected


def test_window_margin_bound_holds_in_both_directions():
    ok = {"col_gap": 34, "groups": {"Act/front": {"window_margin_px": 34,
                                                  "calibrated_from": "COL_GAP"}}}
    bad = {"col_gap": 34, "groups": {"Act/front": {"window_margin_px": 40,
                                                   "calibrated_from": 34}}}
    assert kz.check_window_margin(ok) == []
    assert kz.check_window_margin(bad) != []


def test_read_postscript_name_reads_the_body_face():
    """The stdlib struct parse of §5.1 step 8, against the one face this
    workspace actually carries."""
    path = os.path.join(WORKSPACE, "images-ko", "fonts", "HakgyoansimBareondotumB.otf")
    if not os.path.exists(path):
        pytest.skip("images-ko/fonts is not present")
    assert kz.read_postscript_name(path) == "HakgyoansimBareondotumB"


# ---------------------------------------------------------------------------
# §3.6 / §4.2 -- the report envelope and the exit table
# ---------------------------------------------------------------------------

def test_pick_exit_honours_the_precedence_list():
    """26 sits ABOVE 22: the two answer different questions and the human one is
    named first."""
    assert kc.pick_exit([]) == kc.EXIT_OK
    assert kc.pick_exit([kc.EXIT_TOLERANCE, kc.EXIT_CONSENT]) == kc.EXIT_CONSENT
    assert kc.pick_exit([kc.EXIT_TOLERANCE, kc.EXIT_GUARD]) == kc.EXIT_GUARD
    assert kc.pick_exit([kc.EXIT_ARTIFACT, kc.EXIT_COLLISION]) == kc.EXIT_ARTIFACT


def test_the_report_envelope_declares_write_set_and_seed_as_null():
    """Null and not ABSENT: a rollback path cannot read an input the contract does
    not define."""
    report = kc.new_report("init", "s")
    for key in ("write_set", "registration", "seed"):
        assert key in report and report[key] is None


def test_a_dry_run_report_is_never_consumable():
    report = kc.new_report("init", "s", mode="dry-run")
    ok, blocked = kc.compute_consumable(report, cfg=make_cfg())
    assert not ok and "dry-run" in blocked


def test_a_pending_gate_blocks_consumability():
    report = kc.new_report("init", "s", gate={"path": "g.md", "status": "pending"})
    ok, blocked = kc.compute_consumable(report, cfg=make_cfg())
    assert not ok and "gate.status" in blocked


def test_tolerance_and_consent_tables_are_total_and_disjoint():
    """A flag in both tables would mean a human decision recorded as a threshold."""
    assert kc.assert_tables_total() == []
    assert not (set(kc.tolerance_flags()) & set(kc.consent_flags()))


def test_every_pending_row_is_counted_rather_than_skipped():
    """A row that quietly dropped out because its module was missing is precisely
    the coverage nobody would notice was gone."""
    pending = kc.pending_rows()
    assert set(pending) == {"TOLERANCES", "CONSENTS"}
    for _table, rows in pending.items():
        for row in rows:
            assert row.phase in kc.PHASES
            assert row.module
    # Counted and PRINTED -- the count is the whole point.
    assert any("pending" in line for line in kc.pending_summary())


def test_the_two_v0_tolerance_rows_are_the_ones_selftest_must_exercise():
    v0_rows = [r for r in kc.TOLERANCES if r.phase == "v0"]
    assert len(v0_rows) == 2
    flags = {r.flag for r in v0_rows}
    assert "accept-donor-choice" in {f.lstrip("-") for f in flags if f}
    assert any(getattr(r, "hard_cap", False) for r in v0_rows)


# ---------------------------------------------------------------------------
# The CLI selftests, as subprocesses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["kz_common.py", "kz_config.py", "kz_init.py"])
def test_module_selftest_exits_zero(module):
    """§5.9 makes a green selftest the completion predicate of every step."""
    result = subprocess.run(
        [sys.executable, os.path.join(kc.PACKAGE_DIR, module), "--selftest"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")


@pytest.mark.skipif(not os.path.exists(PLATFORM_PYTHON),
                    reason="Apple's %s is not present" % PLATFORM_PYTHON)
@pytest.mark.parametrize("module", ["kz_common.py", "kz_config.py"])
def test_stdlib_tier_selftests_run_on_apple_python(module):
    """The stdlib tier must not merely compile on 3.9.6 -- it must RUN there."""
    result = subprocess.run(
        [PLATFORM_PYTHON, os.path.join(kc.PACKAGE_DIR, module), "--selftest"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")


def test_kz_config_validate_cli_round_trips_a_generated_config(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(make_cfg(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    result = subprocess.run(
        [sys.executable, os.path.join(kc.PACKAGE_DIR, "kz_config.py"),
         "--validate", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")


def test_the_mirror_writer_round_trips_into_data_scenarios():
    """kz_config.write_data() is the ONLY code in the tool that opens a path under
    SCED-tools/ for writing (§3.1).

    Written into the real tier and removed again, because the assertion is about
    the exemption resolving against the live package directory: check_write_paths
    requires data_root to sit inside kc.PACKAGE_DIR, so a temp workspace would
    exercise a different predicate than the one that ships.
    """
    cfg = make_cfg()
    dest = None
    try:
        dest = kz.write_data(cfg, "scenarios", "_pytest-scratch.scenario.json", cfg)
        assert os.path.exists(dest)
        assert os.path.realpath(dest).startswith(os.path.realpath(kc.PACKAGE_DIR))
        with open(dest, encoding="utf-8") as handle:
            assert json.load(handle)["config_sha256"] == cfg["config_sha256"]
    finally:
        if dest and os.path.exists(dest):
            os.unlink(dest)


def test_the_mirror_writer_refuses_a_lock_path_outside_the_tier():
    cfg = make_cfg()
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.write_data(cfg, "locks", "../scenarios/escaped.json", {})
    assert excinfo.value.code == kc.EXIT_GUARD


def test_the_durable_and_receipt_tiers_exist():
    """§3.1's two git-tracked tiers are locations something must be able to create."""
    for subdir in ("scenarios", "locks"):
        path = os.path.join(kc.PACKAGE_DIR, "data", subdir)
        assert os.path.isdir(path), path
