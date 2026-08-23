"""kz_ask.py -- the batch machinery of design §3.7, entirely offline.

WHY THIS MODULE EXISTS
    The batch machinery had no test module of any kind, and that is where both
    the round-1 and the round-2 CRITICAL review findings landed (§6 step 3). A
    stage is one or more `claude` invocations, never an unstated number, and
    every property that makes that safe -- the partition, the ceiling, the
    universe_size equality with its `null` carve-out, the between-batches stop at
    exit 25, the resume from the first missing batch, and the cross-batch merge --
    is checkable with no `claude`, no credential, no network and no cost.

HOW THE OFFLINE PATH IS BUILT
    `run_bundle(..., invoke=False)` walks the batches, re-uses what is complete
    and reports the first missing batch as `resume_from`. `seed_from_replay()`
    copies a RECORDED run's invocation results (envelope, observed.json, out/)
    into a freshly planned bundle -- the bundle half is always re-planned, never
    copied, so a plan that no longer matches the recording shows up as a digest
    mismatch instead of being papered over.

    Every case here additionally replaces `invoke_shim` with a function that
    fails the test if it is ever called. A test that merely happens not to reach
    the shim is not the same claim as a test that cannot.

THE CONFIG IS BUILT HERE, NOT BORROWED FROM THE MODULE'S OWN SELFTEST
    `analyze.md` §3 item 6: a second verifier that shares the first's helper can
    only reproduce its bugs. The scenario-shaped dict below is written out in this
    file so that a change to `kz_ask._selftest_cfg` cannot silently change what
    this suite asserts.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

import kz_ask as ka
import kz_common as kc
import kz_decide as kd

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
S6_FIXTURES = os.path.join(TESTS_DIR, "fixtures", "koreanize", "ai", "S6")
RECORDED = os.path.join(S6_FIXTURES, "good")


# ---------------------------------------------------------------------------
# Fixtures: a scenario-shaped config and the recorded run's own inputs
# ---------------------------------------------------------------------------

def make_cfg(slug, scenario_sha256, max_units=2, max_calls=3, universe_size=None,
             stage_usd=60.0, call_usd=10.0, clock=5400):
    """Only what `build_bundle` reads. Deliberately not a generated scenario.json:
    this suite exercises the splitter, not `kz_config`.

    `slug` and `scenario_sha256` come from the RECORDING, because rule 7 binds a
    manifest to both and a config that invented its own would make every replay
    fail as run-binding drift rather than as the property under test.
    """
    return {
        "slug": slug,
        "run_dir": ".am/koreanize/%s" % slug,
        "config_sha256": scenario_sha256,
        "ai": {
            "model": "claude-opus-5", "fallback_model": "claude-opus-4-8",
            "effort": "high", "timeout_s": 780, "call_budget_s": 900,
            "stage_wall_clock_s": clock,
            "max_budget_usd_per_call": call_usd,
            "max_budget_usd_per_stage": stage_usd,
            "batch": {"S6": {"unit": "finding",
                             "max_units_per_call": max_units,
                             "max_calls": max_calls,
                             "universe_size": universe_size}},
        },
    }


@pytest.fixture(scope="module")
def recorded():
    """The committed recorded run's stage-level inputs. Read-only: no case in this
    file may write inside the fixture tree."""
    with open(os.path.join(RECORDED, "inputs.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def material():
    """The two copied material files every batch of the recording carries."""
    return {name: os.path.join(RECORDED, "batch-01", "material", name)
            for name in ("gate-typeset.json", "lock.json")}


@pytest.fixture(autouse=True)
def never_invoke_claude(monkeypatch):
    """No test may reach the ask shim. Asserted by replacing it, so a case that
    started invoking would fail loudly rather than hang on a credential prompt."""
    def forbidden(*args, **kwargs):
        raise AssertionError("invoke_shim was called: this suite must stay offline")
    monkeypatch.setattr(ka, "invoke_shim", forbidden)


def build(tmp_path, cfg, recorded, material, name, universe=None):
    return ka.build_bundle(cfg, "S6",
                           universe if universe is not None else recorded["universe"],
                           recorded["units"],
                           identity=recorded["identity"], material=material,
                           ask_root=os.path.join(str(tmp_path), name),
                           stamp="00000000-000000")


def batch_doc(ask_dir, index, *parts):
    path = os.path.join(ask_dir, "batch-%02d" % index, *parts)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 1. The splitter -- total, disjoint, bounded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [1, 2, 3, 7, 9, 58, 80])
@pytest.mark.parametrize("bound", [1, 2, 3, 8, 96])
def test_the_partition_is_total_disjoint_and_bounded(size, bound):
    """A partition, in all three senses at once. The sizes include War of the
    Outer Gods' 58-card S2 universe and The Blob's 80, which are the two §3.7
    names as the reason batching exists at all."""
    universe = ["u%03d" % i for i in range(size)]
    groups = ka.plan_batches(universe, bound)

    flat = [unit for group in groups for unit in group]
    assert flat == universe, "the partition is not total, or reorders the universe"
    assert len(set(flat)) == len(flat), "the batches are not disjoint"
    assert all(0 < len(group) <= bound for group in groups)
    assert len(groups) == (size + bound - 1) // bound


def test_an_empty_universe_produces_no_batches():
    """The splitter itself is total on the empty list; refusing an empty universe
    is `assert_universe`'s job, one layer up, where the stage can be named."""
    assert ka.plan_batches([], 4) == []


@pytest.mark.parametrize("bad", [0, -1, None, "4", 2.0, [2]])
def test_a_non_positive_bound_is_a_precondition(bad):
    """A bound that is not a positive integer refuses rather than producing an
    unbounded or empty partition.

    `True` is deliberately NOT in this table: `isinstance(True, int)` is true in
    Python, so a bool reads as 1 here exactly as it does in `kz_config`'s own
    `max_units_per_call` check. Asserting a refusal here and not there would make
    the two layers disagree about the same value.
    """
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.plan_batches(["a", "b"], bad)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_batch_of_and_batch_numbers_are_what_the_splitter_decided(
        tmp_path, recorded, material):
    """`batch_of` is part of the run binding (rule 7 checks it against every
    manifest), so it must be the splitter's answer and not a declared constant."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"], max_units=2)
    ask_dir = build(tmp_path, cfg, recorded, material, "ask")

    with open(os.path.join(ask_dir, "inputs.json"), "r", encoding="utf-8") as fh:
        stage_inputs = json.load(fh)
    assert stage_inputs["batch_of"] == 2 == len(kd.batch_dirs(ask_dir))

    seen = []
    for index, _path in kd.batch_dirs(ask_dir):
        doc = batch_doc(ask_dir, index, "inputs.json")
        assert doc["batch"] == index
        assert doc["batch_of"] == 2
        seen += doc["universe"]
    assert seen == recorded["universe"], "the bundles do not partition the universe"


def test_one_batch_per_invocation_and_each_carries_its_own_material(
        tmp_path, recorded, material):
    """Each batch is a self-contained invocation directory: prompt, policy, the
    stage schema and the copied material. `material_sha256` is a STAGE-level fact,
    so it is identical in every batch -- material that differed per batch would
    give one file two digests and rule 7 could never hold."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "ask")

    digests = []
    for index, path in kd.batch_dirs(ask_dir):
        for name in ("inputs.json", "prompt.md", "policy.md", "S6.schema.json"):
            assert os.path.exists(os.path.join(path, name)), name
        for name in material:
            assert os.path.exists(os.path.join(path, "material", name))
        digests.append(batch_doc(ask_dir, index, "inputs.json")["material_sha256"])
    assert digests[0] == digests[1]
    assert set(digests[0]) == {"material/gate-typeset.json", "material/lock.json"}


def test_a_batch_bundle_is_a_pure_function_of_the_plan(tmp_path, recorded, material):
    """The resume path binds a completed batch by `sha256(batch-NN/inputs.json)`,
    so a batch bundle must carry no timestamp and no run-local value. Re-planning
    that moved the digest would make every resume look like input drift."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "ask")
    before = [kc.sha256_file(os.path.join(path, "inputs.json"))
              for _index, path in kd.batch_dirs(ask_dir)]

    build(tmp_path, cfg, recorded, material, "ask")
    after = [kc.sha256_file(os.path.join(path, "inputs.json"))
             for _index, path in kd.batch_dirs(ask_dir)]
    assert after == before

    # The STAGE-level inputs may move (they carry `generated_at`); the per-batch
    # bundles may not. Stating both is what pins which of the two is the binding.
    doc = batch_doc(ask_dir, 1, "inputs.json")
    assert "generated_at" not in doc


def test_the_bundle_shape_has_one_owner(tmp_path, recorded, material):
    """§3.7's "one owner": `kz_ask.batch_inputs` IS `kz_decide.batch_inputs`,
    because rule 7 binds a manifest to the bundle's digest and two implementations
    of one format is exactly the drift that property exists to prevent."""
    assert ka.batch_inputs is kd.batch_inputs


# ---------------------------------------------------------------------------
# 2. The ceiling, and the universe_size equality with its null carve-out
# ---------------------------------------------------------------------------

def test_the_ceiling_is_re_asserted_and_names_stage_size_and_ceiling():
    """`kz_config` refuses a mis-declared `universe_size` at load (exit 4); this is
    defence in depth for the stages whose universe is not knowable until
    invocation. The refusal must read as "this scenario is too big for the
    configured budget" rather than as a timeout, so all three numbers are named.
    """
    entry = {"max_units_per_call": 2, "max_calls": 3, "universe_size": None}
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.assert_universe("S6", entry, ["u%d" % i for i in range(7)])
    refusal = excinfo.value
    assert refusal.code == kc.EXIT_PRECONDITION
    text = str(refusal)
    assert "S6" in text and "triage" in text
    assert "7" in text and "6" in text
    assert "max_units_per_call" in text and "max_calls" in text


def test_a_universe_exactly_at_the_ceiling_is_allowed():
    """The boundary is inclusive: `max_units_per_call * max_calls` is the declared
    ceiling, not one below it."""
    entry = {"max_units_per_call": 2, "max_calls": 3, "universe_size": None}
    assert ka.assert_universe("S6", entry, ["a", "b", "c", "d", "e", "f"]) == 6


def test_an_empty_universe_never_reaches_an_invocation():
    entry = {"max_units_per_call": 2, "max_calls": 3, "universe_size": None}
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.assert_universe("S6", entry, [])
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_universe_size_equality_is_asserted_when_it_is_declared():
    """"a corpus that changed under a pinned config is exit 13 on the input, not a
    silently short universe" (§3.7)."""
    entry = {"max_units_per_call": 2, "max_calls": 3, "universe_size": 5}
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.assert_universe("S6", entry, ["a", "b", "c", "d"])
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "universe_size" in str(excinfo.value)

    assert ka.assert_universe("S6", entry, ["a", "b", "c", "d", "e"]) == 6


def test_the_null_carve_out_holds_for_a_gate_invoked_stage():
    """BOTH conjuncts of §3.7's invariant carry the `null` carve-out, not just the
    ceiling one. `universe_size: null` means "not knowable until invocation" and is
    what S6 and S7 declare, because both are gate-invoked and their universe is
    the failing gate's `checks[].detail[]`.

    A set-equality against `null` is unsatisfiable, so a one-sided form would
    refuse EVERY S6 bundle at exit 13 on the way to its first invocation. The
    carve-out is therefore asserted positively -- at several universe sizes, so a
    version that accepted only one length would still fail here.
    """
    entry = {"max_units_per_call": 2, "max_calls": 3, "universe_size": None}
    for size in (1, 2, 3, 5, 6):
        assert ka.assert_universe("S6", entry, ["u%d" % i for i in range(size)]) == 6


def test_an_absent_universe_size_is_not_the_same_as_a_null_one():
    """`null` is a declaration; a missing key is an unanswered question. Conflating
    them would let a stage that simply forgot the field inherit the carve-out."""
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.assert_universe("S6", {"max_units_per_call": 2, "max_calls": 3}, ["a"])
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "null" in str(excinfo.value)


def test_the_carve_out_holds_through_a_real_s6_bundle_build(
        tmp_path, recorded, material):
    """The end-to-end half of the same property: the recorded S6 run declares
    `universe_size: null`, and building its bundle must succeed."""
    assert recorded["universe_size"] is None
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"], universe_size=None)
    ask_dir = build(tmp_path, cfg, recorded, material, "ask")
    assert len(kd.batch_dirs(ask_dir)) == 2


def test_the_ceiling_refusal_fires_at_bundle_build_before_anything_is_written(
        tmp_path, recorded, material):
    """Exit 13 "before any invocation" has to mean before any directory, too, or a
    refused stage leaves a half-built bundle a later resume would trust."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"],
                   max_units=1, max_calls=2)
    ask_root = os.path.join(str(tmp_path), "over-ceiling")
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.build_bundle(cfg, "S6", recorded["universe"], recorded["units"],
                        identity=recorded["identity"], material=material,
                        ask_root=ask_root, stamp="00000000-000000")
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert not os.path.exists(ask_root)


def test_an_unknown_stage_id_is_a_usage_refusal(tmp_path, recorded, material):
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.build_bundle(cfg, "S9", recorded["universe"], recorded["units"],
                        material=material,
                        ask_root=os.path.join(str(tmp_path), "x"), stamp="s")
    assert excinfo.value.code == kc.EXIT_USAGE


# ---------------------------------------------------------------------------
# 3. The between-batches budget stop -- exit 25, and 25 is not 65
# ---------------------------------------------------------------------------

def test_budget_finding_is_forward_looking_on_cost():
    """The next invocation may cost up to `max_budget_usd_per_call`, so stopping
    only once the cap is already exceeded would exceed it by construction."""
    budget = {"stage_wall_clock_s": 100, "max_budget_usd_per_stage": 60,
              "max_budget_usd_per_call": 10}
    assert ka.budget_finding(budget, 40.0, 10) is None
    assert ka.budget_finding(budget, 50.1, 10) is not None
    assert "max_budget_usd_per_stage" in ka.budget_finding(budget, 55.0, 10)
    assert "stage_wall_clock_s" in ka.budget_finding(budget, 0.0, 101)
    assert ka.budget_finding(budget, 0.0, 100) is None


def test_the_stage_stops_between_batches_at_25_leaving_n_of_m_on_disk(
        tmp_path, recorded, material):
    """§3.7: "A stage that exhausts either stops at a batch boundary and exits 25 --
    'stage stopped between batches; N of M complete'."

    The completed batches must still be on disk, because that is what the resume
    path re-uses; a stop that cleaned up would turn 25 into a dead end.
    """
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"], stage_usd=1.0)
    ask_dir = build(tmp_path, cfg, recorded, material, "broke")
    ka.seed_from_replay(ask_dir, RECORDED)

    result = ka.run_bundle(ask_dir, invoke=False)
    assert result["exit_code"] == kc.EXIT_AI_BUDGET
    assert result["stopped_by"] and "max_budget_usd_per_stage" in result["stopped_by"]
    assert result["resume_from"] == 2
    assert result["complete"] is False

    completed = [b for b in result["batches"] if b["complete"]]
    assert len(completed) == 1, "N of M: exactly batch-01 completed"
    assert result["batch_of"] == 2
    assert os.path.exists(os.path.join(ask_dir, "batch-01", "claude-envelope.json"))
    assert os.path.exists(os.path.join(ask_dir, "batch-01", "out", "manifest.json"))


def test_the_wall_clock_half_of_the_stop_is_also_25(tmp_path, recorded, material):
    """Both knobs `budget_check` runs between batches reach the same code, or an
    operator would have to know which one fired to know where to look."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"], clock=0)
    ask_dir = build(tmp_path, cfg, recorded, material, "slow")
    ka.seed_from_replay(ask_dir, RECORDED)

    result = ka.run_bundle(ask_dir, invoke=False, started=0)
    assert result["exit_code"] == kc.EXIT_AI_BUDGET
    assert "stage_wall_clock_s" in result["stopped_by"]
    assert result["resume_from"] == 2


def test_the_process_itself_exits_25_not_just_the_result(tmp_path, recorded, material):
    """25 is an OPERATOR-facing contract -- `--status` reads it, the runbook indexes
    it, and `koreanize.sh` passes it through verbatim. A result dict carrying 25
    while the process exited 0 would satisfy every in-process assertion above and
    still be wrong at the only boundary that matters.

    `--replay` implies `--no-invoke`, so this subprocess reaches no `claude` and
    needs no credential.
    """
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"], stage_usd=1.0)
    ask_dir = build(tmp_path, cfg, recorded, material, "cli-25")
    proc = subprocess.run(
        [sys.executable, os.path.join(kc.PACKAGE_DIR, "kz_ask.py"),
         "--ask-dir", ask_dir, "--replay", RECORDED, "--json-only"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    assert proc.returncode == kc.EXIT_AI_BUDGET, proc.stderr
    result = json.loads(proc.stdout)
    assert result["resume_from"] == 2 and result["invoked"] == 0

    roomy = build(tmp_path, make_cfg(recorded["slug"], recorded["scenario_sha256"]),
                  recorded, material, "cli-0")
    proc = subprocess.run(
        [sys.executable, os.path.join(kc.PACKAGE_DIR, "kz_ask.py"),
         "--ask-dir", roomy, "--replay", RECORDED, "--json-only"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    assert proc.returncode == kc.EXIT_OK, proc.stderr
    assert json.loads(proc.stdout)["complete"] is True


def test_25_is_not_65_and_the_two_mean_different_things():
    """"65 is scoped to a single `claude` invocation failing, and telling an
    operator 'claude unavailable' when the truth is 'the stage ran out of its own
    budget' sends them to the wrong file" (§3.7)."""
    assert kc.EXIT_AI_BUDGET == 25
    assert kc.EXIT_AI_UNAVAILABLE == 65
    assert kc.EXIT_AI_BUDGET != kc.EXIT_AI_UNAVAILABLE
    assert kc.EXIT_MEANING[kc.EXIT_AI_BUDGET] != kc.EXIT_MEANING[kc.EXIT_AI_UNAVAILABLE]
    # And the budget stop reports 25 through the RESULT, never as a shim rc.
    assert kc.EXIT_AI_UNAVAILABLE in ka.SHIM_PASSTHROUGH
    assert kc.EXIT_AI_BUDGET not in ka.SHIM_PASSTHROUGH


def test_a_stage_with_room_left_does_not_stop(tmp_path, recorded, material):
    """The negative of the stop: with the default budget the same recorded run
    walks to completion, so the exit-25 case above is testing the budget and not
    some other refusal."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "roomy")
    ka.seed_from_replay(ask_dir, RECORDED)

    result = ka.run_bundle(ask_dir, invoke=False)
    assert result["exit_code"] == kc.EXIT_OK
    assert result["complete"] is True
    assert result["resume_from"] is None
    assert result["stopped_by"] is None
    assert result["reused"] == 2 and result["invoked"] == 0


# ---------------------------------------------------------------------------
# 4. Resume -- from the first missing batch, bound by inputs_sha256
# ---------------------------------------------------------------------------

def test_an_unrecorded_bundle_resumes_at_batch_one(tmp_path, recorded, material):
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "fresh")
    result = ka.run_bundle(ask_dir, invoke=False)
    assert result["resume_from"] == 1
    assert result["complete"] is False
    assert result["reused"] == 0


def test_resume_starts_at_the_first_missing_batch_and_re_uses_the_rest(
        tmp_path, recorded, material):
    """§3.7: re-running against the same `ai/<stage>/<stamp>/` re-uses every
    completed `batch-NN/` and starts at the first missing one.

    "First missing" rather than "last completed": the two differ the moment a
    middle batch is absent, and only the first is safe.
    """
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "resume")
    for name in ("claude-envelope.json", "observed.json"):
        shutil.copy2(os.path.join(RECORDED, "batch-01", name),
                     os.path.join(ask_dir, "batch-01", name))
    shutil.copytree(os.path.join(RECORDED, "batch-01", "out"),
                    os.path.join(ask_dir, "batch-01", "out"), dirs_exist_ok=True)

    result = ka.run_bundle(ask_dir, invoke=False)
    assert result["resume_from"] == 2
    assert result["reused"] == 1
    assert result["batches"][0]["complete"] is True
    assert result["batches"][0]["reused"] is True
    assert result["batches"][0]["session_id"]


def test_resume_never_trusts_a_batch_on_its_filename(tmp_path, recorded, material):
    """The negative the design names explicitly: "each is bound by `inputs_sha256`
    and revalidated, never trusted on filename".

    A completed batch whose recorded inputs no longer hash to the PLAN's digest
    must not be re-used. Hashing the file on disk instead of the derived plan
    makes that comparison a tautology, so this case is checked through the walk
    (`run_bundle`) and not only through the predicate (`batch_state`).
    """
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "tamper")
    ka.seed_from_replay(ask_dir, RECORDED)
    assert ka.run_bundle(ask_dir, invoke=False)["complete"] is True

    tampered = os.path.join(ask_dir, "batch-01", "inputs.json")
    doc = batch_doc(ask_dir, 1, "inputs.json")
    doc["universe"] = list(reversed(doc["universe"]))
    kc.atomic_write_json(tampered, doc)

    state = ka.batch_state(ask_dir, 1, kc.sha256_file(tampered))
    assert state["complete"] is True, "the predicate must accept its own digest"

    result = ka.run_bundle(ask_dir, invoke=False)
    assert result["resume_from"] == 1, (
        "a batch whose inputs.json moved was silently re-used")
    assert result["reused"] == 0
    assert "digest" in (result["batches"][0]["reason"] or "")


@pytest.mark.parametrize("damage,expected", [
    ("no-envelope", "no claude-envelope.json"),
    ("bad-json", "not valid JSON"),
    ("no-session", "no session_id"),
])
def test_a_batch_is_complete_only_on_a_parseable_envelope_with_a_session_id(
        tmp_path, recorded, material, damage, expected):
    """The three ways a recorded batch can be present and still not be a completed
    invocation. Each must be re-invoked rather than re-used, and each must say
    which one it was."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, damage)
    ka.seed_from_replay(ask_dir, RECORDED)
    envelope_path = os.path.join(ask_dir, "batch-01", "claude-envelope.json")

    if damage == "no-envelope":
        os.remove(envelope_path)
    elif damage == "bad-json":
        kc.atomic_write_text(envelope_path, "{ truncated\n")
    else:
        doc = batch_doc(ask_dir, 1, "claude-envelope.json")
        doc["session_id"] = ""
        kc.atomic_write_json(envelope_path, doc)

    result = ka.run_bundle(ask_dir, invoke=False)
    assert result["resume_from"] == 1
    assert expected in result["batches"][0]["reason"]


def test_resume_is_idempotent_once_every_batch_is_recorded(
        tmp_path, recorded, material):
    """Re-planning into a directory that is already complete must not re-invoke,
    move a digest, or remove a recorded result."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "idem")
    ka.seed_from_replay(ask_dir, RECORDED)
    before = {rel: kc.sha256_file(os.path.join(ask_dir, rel))
              for rel in _relative_files(ask_dir)}

    for _ in range(2):
        build(tmp_path, cfg, recorded, material, "idem")
        result = ka.run_bundle(ask_dir, invoke=False)
        assert result["complete"] and result["invoked"] == 0

    after = {rel: kc.sha256_file(os.path.join(ask_dir, rel))
             for rel in _relative_files(ask_dir)}
    assert set(after) == set(before), "re-planning removed or added a file"
    moved = [rel for rel in sorted(before) if before[rel] != after[rel]]
    # Only the STAGE-level inputs are allowed to move at all, and only because
    # they carry `generated_at`. Every per-batch bundle and every recorded result
    # must be byte-identical, or the resume binding is not stable.
    assert set(moved) <= {"inputs.json"}, moved


def _relative_files(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(found)


def test_seed_from_replay_never_copies_the_bundle_half(tmp_path, recorded, material):
    """"The bundle half is re-planned by `build_bundle`, never copied -- so a
    replay that resumes is resuming against a freshly derived plan, and a plan
    that no longer matches the recording shows up as a digest mismatch rather than
    being papered over."
    """
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "seed")
    planned = kc.sha256_file(os.path.join(ask_dir, "batch-01", "inputs.json"))
    copied = ka.seed_from_replay(ask_dir, RECORDED)

    assert kc.sha256_file(os.path.join(ask_dir, "batch-01", "inputs.json")) == planned
    assert not any(name.endswith("inputs.json") for name in copied)
    assert any(name.endswith("claude-envelope.json") for name in copied)
    assert any(name.endswith("out/") for name in copied)


def test_a_bundle_with_no_stage_inputs_is_a_precondition(tmp_path):
    empty = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty)
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.run_bundle(empty, invoke=False)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


# ---------------------------------------------------------------------------
# 5. The cross-batch merge
# ---------------------------------------------------------------------------

def test_a_replayed_bundle_merges_across_batches_and_decides_proceed(
        tmp_path, recorded, material):
    """The whole offline path end to end: plan, seed from the recording, walk with
    no invocation, decide, and read `manifest.merged.json`.

    The merge is the reason rule 1 is satisfiable at all: no single invocation
    holds the whole universe, so the union has to be assembled before coverage can
    be judged.
    """
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "merge")
    ka.seed_from_replay(ask_dir, RECORDED)
    assert ka.run_bundle(ask_dir, invoke=False)["complete"] is True

    report, code = kd.decide(ask_dir)
    assert code == kc.EXIT_OK, report["reason"]

    with open(os.path.join(ask_dir, "manifest.merged.json"), "r",
              encoding="utf-8") as fh:
        merged = json.load(fh)
    assert [r["unit_id"] for r in merged["rulings"]] == recorded["universe"]
    assert merged["universe"] == len(recorded["universe"])
    assert merged["batch_of"] == 2 and len(merged["batches"]) == 2
    assert set(merged["ruling_batch"].values()) == {1, 2}, (
        "both batches must contribute, or the cross-batch merge is untested")
    assert merged["slug"] == recorded["slug"]
    assert merged["scenario_sha256"] == recorded["scenario_sha256"]


def test_the_merged_manifest_binds_to_the_plan_this_run_built(
        tmp_path, recorded, material):
    """Rule 7 evaluates over the union, so a replay that resumed against a
    different plan must not merge clean. The digests recorded in the merge are the
    freshly planned bundles', not the recording's file names."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "bound")
    ka.seed_from_replay(ask_dir, RECORDED)
    report, code = kd.decide(ask_dir)
    assert code == kc.EXIT_OK

    with open(os.path.join(ask_dir, "manifest.merged.json"), "r",
              encoding="utf-8") as fh:
        merged = json.load(fh)
    for entry in merged["batches"]:
        on_disk = kc.sha256_file(os.path.join(ask_dir, "batch-%02d" % entry["batch"],
                                              "inputs.json"))
        assert entry["inputs_sha256"] == on_disk


def test_a_plan_that_moved_under_a_recording_fails_the_run_binding(
        tmp_path, recorded, material):
    """The point of re-planning rather than copying: seeding a recording into a
    bundle built from a DIFFERENT universe order gives batches whose manifests no
    longer bind, and that is rule 7 at 66 -- never a quiet pass."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    shuffled = list(reversed(recorded["universe"]))
    ask_dir = build(tmp_path, cfg, recorded, material, "moved", universe=shuffled)
    ka.seed_from_replay(ask_dir, RECORDED)

    report, code = kd.decide(ask_dir)
    assert code == kc.EXIT_AI_MANIFEST
    failed = [r["rule"] for r in report["rules"] if r["status"] == "fail"]
    assert 7 in failed


# ---------------------------------------------------------------------------
# 6. The suite's own offline guarantee
# ---------------------------------------------------------------------------

def test_the_shim_is_never_invoked_on_the_offline_path(tmp_path, recorded, material):
    """`invoke=False` is the offline path, and the autouse fixture has already
    replaced `invoke_shim` with a function that fails the test if called. This case
    exercises every branch of the walk -- reuse, resume and the budget stop -- under
    that replacement, so the guarantee is demonstrated rather than asserted."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    ask_dir = build(tmp_path, cfg, recorded, material, "offline")
    ka.run_bundle(ask_dir, invoke=False)
    ka.seed_from_replay(ask_dir, RECORDED)
    ka.run_bundle(ask_dir, invoke=False)

    broke = build(tmp_path, make_cfg(recorded["slug"], recorded["scenario_sha256"],
                                     stage_usd=1.0), recorded, material, "offline2")
    ka.seed_from_replay(broke, RECORDED)
    assert ka.run_bundle(broke, invoke=False)["exit_code"] == kc.EXIT_AI_BUDGET


def test_the_committed_recording_is_untouched_by_this_suite():
    """Nothing here may write inside the fixture tree: it is a committed static
    artifact and `seed_from_replay` reads from it in every case above."""
    for _index, path in kd.batch_dirs(RECORDED):
        for name in ("inputs.json", "claude-envelope.json", "observed.json"):
            assert os.path.exists(os.path.join(path, name))
    assert not os.path.exists(os.path.join(RECORDED, "decide.json"))
    assert not os.path.exists(os.path.join(RECORDED, "manifest.merged.json"))


# ---------------------------------------------------------------------------
# 7. The invocation: the budget knobs that actually reach the shim's argv
# ---------------------------------------------------------------------------

# Captured at import, BEFORE the autouse fixture replaces the module attribute.
# These cases are about the argv the REAL `invoke_shim` builds; the suite's
# offline guarantee is kept by replacing `subprocess.run` instead, so nothing is
# executed -- no `claude`, no credential, no network.
REAL_INVOKE_SHIM = ka.invoke_shim


def capture_argv(monkeypatch, tmp_path, budget, name="argv"):
    """Run the real `invoke_shim` with `subprocess.run` replaced; return the argv."""
    root = os.path.join(str(tmp_path), name)
    batch_dir = os.path.join(root, "batch-01")
    os.makedirs(batch_dir)
    shim = os.path.join(root, "kz-ask-claude.sh")
    with open(shim, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")

    seen = {}

    class _Proc(object):
        returncode = 0

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(ka.subprocess, "run", fake_run)
    rc = REAL_INVOKE_SHIM(root, batch_dir, "S6", budget,
                          run_dir=root, workspace=root, shim=shim)
    assert rc == 0
    return seen["cmd"]


def test_the_call_budget_reaches_the_shim(tmp_path, monkeypatch, recorded, material):
    """`call_budget_s` is the per-batch bound the shim's own `budget_check`
    enforces. `kz_config` proves `max_calls * call_budget_s <= stage_wall_clock_s`
    at load, so a scenario that shortens it to leave headroom under a tight stage
    wall clock has that choice enforced only if the flag is actually passed --
    otherwise the shim silently measures against its own 900 s default. The value
    here is deliberately NOT 900, so a dropped flag cannot look like a pass."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    cfg["ai"]["call_budget_s"] = 420
    # `timeout_s` comes down with it: the per-call wall clock has to fit inside
    # the pre-call gate, and `build_bundle` refuses at exit 13 when it does not.
    cfg["ai"]["timeout_s"] = 300
    ask_dir = build(tmp_path, cfg, recorded, material, "callbudget")
    with open(os.path.join(ask_dir, "inputs.json"), "r", encoding="utf-8") as fh:
        budget = json.load(fh)["budget"]
    assert budget["call_budget_s"] == 420

    cmd = capture_argv(monkeypatch, tmp_path, budget)
    assert "--call-budget" in cmd
    assert cmd[cmd.index("--call-budget") + 1] == str(budget["call_budget_s"])


def test_an_absent_call_budget_omits_the_flag(tmp_path, monkeypatch):
    """The `if value is not None` guard. A budget declaring no per-call bound
    leaves the shim on its own default rather than passing an empty value, which
    the shim's `shift 2` would consume as the next flag."""
    cmd = capture_argv(monkeypatch, tmp_path, {"model": "claude-opus-5"}, "absent")
    assert "--call-budget" not in cmd

    cmd = capture_argv(monkeypatch, tmp_path,
                       {"model": "claude-opus-5", "call_budget_s": None}, "none")
    assert "--call-budget" not in cmd


def test_every_forwarded_flag_is_one_the_shim_parses():
    """The cross-module half: a flag `invoke_shim` sends that the shim does not
    parse is an unknown argument (exit 2), and a knob the shim parses that nothing
    sends is silently the shim's default -- which is the defect this section
    exists for."""
    with open(ka.SHIM, "r", encoding="utf-8") as fh:
        shim_text = fh.read()
    assert dict(ka.SHIM_BUDGET_FLAGS)["call_budget_s"] == "--call-budget"
    for _key, flag in ka.SHIM_BUDGET_FLAGS:
        assert ("%s)" % flag) in shim_text, "%s parses no %s" % (ka.SHIM, flag)


# ---------------------------------------------------------------------------
# 8. The per-call clock must fit inside the pre-call gate
# ---------------------------------------------------------------------------

def test_a_timeout_longer_than_the_call_budget_is_a_precondition():
    """`call_budget_s` is checked BEFORE a call and never inside one, so a
    `timeout_s` longer than it lets a single invocation run past the per-batch
    budget and `max_calls * call_budget_s <= stage_wall_clock_s` stops being a
    runtime guarantee -- the overrun surfaces only between batches, at exit 25.
    """
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.assert_call_bounds("triage", {"timeout_s": 780, "call_budget_s": 420})
    refusal = excinfo.value
    assert refusal.code == kc.EXIT_PRECONDITION
    text = str(refusal)
    assert "triage" in text and "780" in text and "420" in text


def test_a_timeout_equal_to_the_call_budget_is_allowed():
    """The boundary is inclusive: the assertion is `timeout_s <= call_budget_s`."""
    ka.assert_call_bounds("triage", {"timeout_s": 420, "call_budget_s": 420})
    ka.assert_call_bounds("triage", {"timeout_s": 780, "call_budget_s": 900})


@pytest.mark.parametrize("ai", [
    {"timeout_s": "780s", "call_budget_s": 900},
    {"timeout_s": 780, "call_budget_s": "900"},
    {"timeout_s": 0, "call_budget_s": 900},
    {"timeout_s": 780, "call_budget_s": -1},
    {"timeout_s": None, "call_budget_s": 900},
    {"call_budget_s": 900},
])
def test_a_malformed_per_call_knob_is_a_precondition_not_a_typeerror(ai):
    """A knob that is merely present bounds nothing. The refusal has to be the
    controlled exit 13, never a `TypeError` out of the comparison."""
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.assert_call_bounds("triage", ai)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_the_bound_is_re_asserted_at_bundle_build_before_anything_is_written(
        tmp_path, recorded, material):
    """Defence in depth, in the §3.7 shape: `kz_config` refuses at load (exit 4),
    `kz_ask` re-asserts before any invocation (exit 13). The bundle directory must
    not exist afterwards -- the refusal comes before the first write."""
    cfg = make_cfg(recorded["slug"], recorded["scenario_sha256"])
    cfg["ai"]["call_budget_s"] = 420           # timeout_s stays at 780
    ask_root = os.path.join(str(tmp_path), "overbudget")
    with pytest.raises(kc.KzRefusal) as excinfo:
        ka.build_bundle(cfg, "S6", recorded["universe"], recorded["units"],
                        identity=recorded["identity"], material=material,
                        ask_root=ask_root, stamp="00000000-000000")
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "timeout_s" in str(excinfo.value)
    assert not os.path.exists(ask_root)


# ---------------------------------------------------------------------------
# 9. The shim's own knobs -- the guard fails CLOSED
# ---------------------------------------------------------------------------
#
# `set -e` is suspended inside an `if` condition, so a `-gt` against a
# non-integer raises an arithmetic error that reads as FALSE: the per-call budget
# guard would pass silently for the rest of the run rather than stop the batch.
# These cases run the real shim, which refuses on the knob's SHAPE before it
# reads a bundle, so nothing here reaches `claude`, a credential or the network.

def run_shim(*args):
    env = dict(os.environ)
    # A machine-local env file would be sourced and could carry its own knobs.
    env["KOREANIZE_ENV_FILE"] = os.path.join(os.sep, "nonexistent", "koreanize-env")
    return subprocess.run(["bash", ka.SHIM] + list(args),
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


@pytest.mark.parametrize("flag,name", [("--call-budget", "KOREANIZE_AI_CALL_BUDGET"),
                                       ("--timeout", "KOREANIZE_AI_TIMEOUT")])
@pytest.mark.parametrize("bad", ["900s", "0", "-1", "abc", "10.5", "9 00"])
def test_a_bad_seconds_knob_refuses_before_any_work(flag, name, bad):
    proc = run_shim(flag, bad, "--ask-dir", os.sep + "nonexistent-ask-dir",
                    "--stage", "S6")
    assert proc.returncode == kc.EXIT_USAGE
    combined = proc.stdout + proc.stderr
    assert flag in combined and name in combined
    assert "not a positive integer of seconds" in combined


@pytest.mark.parametrize("flag", ["--call-budget", "--timeout"])
def test_a_well_formed_seconds_knob_gets_past_the_shape_check(flag):
    """The negative control: with a legal value the shim reaches its ordinary
    argument validation instead, so the refusal above is the knob's and not an
    artefact of the rest of the command line."""
    proc = run_shim(flag, "600")
    combined = proc.stdout + proc.stderr
    assert "not a positive integer of seconds" not in combined
    assert "ASK_DIR" in combined


def test_the_budget_guard_validates_the_shape_before_it_compares():
    """The guard is re-checked inside `budget_check` and not only at startup: it
    is the only thing between a batch and an unbounded run, and a comparison that
    errors reads as "under budget"."""
    with open(ka.SHIM, "r", encoding="utf-8") as fh:
        shim_text = fh.read()
    body = shim_text.split("budget_check() {", 1)[1].split("\n}", 1)[0]
    guard = body.index('"${AI_CALL_BUDGET}" =~ ^[1-9][0-9]*$')
    compare = body.index('"${e}" -gt "${AI_CALL_BUDGET}"')
    assert guard < compare
