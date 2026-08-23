"""kz_decide.py -- the manifest schema (design §3.7) and the ten rules (§4.4).

WHAT THIS SUITE IS FOR
    `--replay` is what makes the deterministic half of the AI path testable at
    all: it copies a recorded run into a fresh directory and re-runs merge +
    decide there with no `claude`, no credential, no network and no cost. Every
    case below goes through that door or calls a function directly. Nothing here
    may invoke the ask shim, read a token, or touch the network.

THE FIXTURES ARE COMMITTED STATIC ARTIFACTS, NOT GENERATED AT TEST TIME
    `tests/fixtures/koreanize/ai/S6/<class>/` holds the twelve recorded-envelope
    classes of §4.4. They are written by `kz_decide.build_reference_run`, which is
    deliberately both the fixture generator AND the module's negative selftest --
    so a schema change that would invalidate them cannot pass `--selftest`. This
    suite reads the COMMITTED directories rather than regenerating them, because a
    suite that regenerates its own inputs from the code under test can only ever
    prove that code self-consistent. `test_committed_fixtures_match_the_generator`
    is the one case that closes that loop, and it compares rather than overwrites.

THE RULE -> FIXTURE MAP IS A LITERAL DICT, NOT A DIRECTORY WALK
    §4.4 requires it explicitly. A map discovered by walking the fixture tree
    would grow silently as classes are added and shrink silently as they are
    deleted -- exactly the two failures the meta-test exists to catch.

NEVER MUTATE A COMMITTED FIXTURE
    Every case that needs a damaged run replays into `tmp_path` first and damages
    the COPY. `replay()` itself refuses when source and destination are the same
    path, which is the second half of the same property.
"""

import filecmp
import json
import os
import subprocess
import sys

import pytest

import kz_common as kc
import kz_decide as kd

# The in-file path convention of `test_sced_schedule.py:21-23`, which conftest.py
# adds to rather than replaces. `tests/` is a package, so conftest is not
# importable by bare name.
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
S6_FIXTURES = os.path.join(TESTS_DIR, "fixtures", "koreanize", "ai", "S6")
DECIDE_PY = os.path.join(kc.PACKAGE_DIR, "kz_decide.py")


# ---------------------------------------------------------------------------
# §4.4 -- the literal rule -> fixture map and the table it drives
# ---------------------------------------------------------------------------
#
# Rule 4 maps to TWO classes: `medium-on-pixels/` for clause 1 (medium confidence
# is permitted only where the ruling does not reach shipped pixels) and
# `provenance-mismatch/` for clause 2 (a ruling's declared provenance must match
# its verdict). One fixture cannot exercise both clauses and leave neither
# unproven.

RULE_FIXTURES = {
    1: ("coverage-gap",),
    2: ("abstain",),
    3: ("low",),
    4: ("medium-on-pixels", "provenance-mismatch"),
    5: ("novel-not-high",),
    6: ("permission-denied",),
    7: ("binding-drift",),
    8: ("write-outside-set",),
    9: ("empty-value",),
    10: ("identity-drift",),
}

# §4.4's two-tier aggregation, restated as data so the table below cannot quietly
# disagree with it: rules 1, 6, 7 and 9 are the 66 tier; 2, 3, 4, 5, 8 and 10 the
# 11 tier. There is no 67 tier here -- 67 belongs to the artifact verifier.
MANIFEST_INVALID_RULES = (1, 6, 7, 9)
POLICY_RULES = (2, 3, 4, 5, 8, 10)


def _expected_exit(rule):
    return kc.EXIT_AI_MANIFEST if rule in MANIFEST_INVALID_RULES else kc.EXIT_POLICY


# (fixture, expected process exit, expected failing rule). `good/` carries no
# failing rule and is the only row expecting 0.
DECIDE_TABLE = (
    [("good", kc.EXIT_OK, None)]
    + [(name, _expected_exit(rule), rule)
       for rule, names in sorted(RULE_FIXTURES.items()) for name in names]
)


def replay_and_decide(tmp_path, fixture, name=None):
    """Run the real CLI over a COMMITTED fixture into a fresh directory.

    The subprocess is the point: it asserts the PROCESS exit code §4.4 specifies
    rather than only the return value of `decide()`, and it exercises the
    documented invocation shape. `--replay` copies the recorded run into
    `--run-dir`; it never decides in place.
    """
    run_dir = os.path.join(str(tmp_path), name or fixture)
    proc = subprocess.run(
        [sys.executable, DECIDE_PY,
         "--replay", os.path.join(S6_FIXTURES, fixture),
         "--run-dir", run_dir, "--quiet"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    decide_path = os.path.join(run_dir, "decide.json")
    assert os.path.exists(decide_path), (
        "no decide.json was written for %s\nstdout: %s\nstderr: %s"
        % (fixture, proc.stdout, proc.stderr))
    with open(decide_path, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    return proc.returncode, report, run_dir


def rule_entry(report, number):
    matches = [r for r in report["rules"] if r["rule"] == number]
    assert len(matches) == 1, "rule %d appears %d time(s)" % (number, len(matches))
    return matches[0]


def failing_rules(report):
    return [r["rule"] for r in report["rules"] if r["status"] == "fail"]


def _batch_manifest(fixture_dir, index):
    path = os.path.join(fixture_dir, "batch-%02d" % index, "out", "manifest.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _relative_files(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(found)


@pytest.mark.parametrize("fixture,expected_exit,expected_failing_rule", DECIDE_TABLE)
def test_recorded_fixture_decides_as_specified(tmp_path, fixture, expected_exit,
                                               expected_failing_rule):
    """Both halves of §4.4's contract: the process exit code AND the named
    `rules[]` entry carrying `"status": "fail"`.

    Asserting only the exit code would pass a fixture that failed the RIGHT tier
    for the WRONG rule -- the shape a single `medium` fixture had before §4.4
    split rule 4 and rule 5 apart.
    """
    code, report, _run_dir = replay_and_decide(tmp_path, fixture)
    assert code == expected_exit, (
        "%s: exit %d, expected %d (%s)"
        % (fixture, code, expected_exit, report["reason"]))
    assert report["exit_code"] == code, "decide.json disagrees with the process"

    if expected_failing_rule is None:
        assert report["outcome"] == "proceed"
        assert failing_rules(report) == []
        assert report["covered"] == report["universe"]
        return

    entry = rule_entry(report, expected_failing_rule)
    assert entry["status"] == "fail", (
        "%s: rule %d (%s) did not fail; the failures were %s"
        % (fixture, expected_failing_rule, entry["name"], failing_rules(report)))
    assert entry["detail"], "a failing rule must say what failed"
    assert entry["exit_on_fail"] == expected_exit
    assert entry["name"] in report["reason"]


def test_rule_fixture_map_is_total_and_every_fixture_exists():
    """The meta-test §4.4 requires, in BOTH directions.

    Totality over `range(1, 11)` is what stops a rule added later from shipping
    untested. The on-disk existence check is what stops a fixture deleted later
    from passing unnoticed -- without it the parametrized table above would simply
    collect fewer cases and still report green.
    """
    assert sorted(RULE_FIXTURES) == list(range(1, 11))
    assert set(MANIFEST_INVALID_RULES) | set(POLICY_RULES) == set(range(1, 11))
    assert set(MANIFEST_INVALID_RULES) & set(POLICY_RULES) == set()

    for rule, names in sorted(RULE_FIXTURES.items()):
        assert names, "rule %d names no fixture" % rule
        for name in names:
            path = os.path.join(S6_FIXTURES, name)
            assert os.path.isdir(path), (
                "rule %d names fixture %r, which is not on disk at %s"
                % (rule, name, path))
            assert os.path.exists(os.path.join(path, "inputs.json"))
            assert kd.batch_dirs(path), "%s has no batch-NN/ directory" % name

    # `good/` is the twelfth class and is named by no rule, so it needs its own
    # existence assertion or its deletion would show up as nothing at all.
    assert os.path.isdir(os.path.join(S6_FIXTURES, "good"))


def test_the_classes_on_disk_are_exactly_the_twelve_declared():
    """A class present on disk but named by nothing is as much a defect as a
    missing one: it is a recorded run nobody decides about."""
    declared = {"good"} | {name for names in RULE_FIXTURES.values() for name in names}
    on_disk = {name for name in os.listdir(S6_FIXTURES)
               if os.path.isdir(os.path.join(S6_FIXTURES, name))}
    assert on_disk == declared
    assert len(declared) == 12


def test_the_map_agrees_with_the_modules_own_fault_table():
    """`kz_decide.FAULT_RULE` drives `--selftest`; the dict above drives pytest.
    Two tables stating one fact have to be checked against each other, or the CLI
    selftest and this suite can disagree about which rule a class targets --
    which is how a rule ends up covered in neither."""
    assert set(kd.FAULT_RULE) == set(kd.FAULTS)
    assert len(kd.FAULTS) == 11
    for name, (rule, exit_code) in sorted(kd.FAULT_RULE.items()):
        assert name in RULE_FIXTURES[rule], (
            "%s: the module maps it to rule %d, this suite does not" % (name, rule))
        assert exit_code == _expected_exit(rule)


def test_committed_fixtures_match_the_generator(tmp_path):
    """The committed tree is what `build_reference_run` writes, byte for byte.

    This is the ONE case that regenerates, and it does so into `tmp_path` and
    COMPARES rather than overwriting. It is what makes "the fixtures are static
    committed artifacts" and "the generator is also the negative selftest" one
    statement rather than two that can drift apart silently.
    """
    for name in sorted({"good"} | set(kd.FAULTS)):
        fault = None if name == "good" else name
        fresh = os.path.join(str(tmp_path), name)
        kd.build_reference_run(fresh, fault=fault)
        committed = os.path.join(S6_FIXTURES, name)

        fresh_files = _relative_files(fresh)
        assert fresh_files == _relative_files(committed), (
            "%s: the committed file set differs from the generator's" % name)
        for rel in fresh_files:
            assert filecmp.cmp(os.path.join(fresh, rel), os.path.join(committed, rel),
                               shallow=False), (
                "%s/%s differs from what build_reference_run writes -- regenerate "
                "the fixture tree or revert the schema change" % (name, rel))


def test_replay_refuses_to_decide_in_place(tmp_path):
    """The property that keeps the committed fixtures immutable: `--replay` copies
    a recorded run, and a source equal to the destination is a usage refusal."""
    fixture = os.path.join(S6_FIXTURES, "good")
    with pytest.raises(kc.KzRefusal) as excinfo:
        kd.replay(fixture, fixture)
    assert excinfo.value.code == kc.EXIT_USAGE
    assert "never decides in place" in str(excinfo.value)


def test_deciding_a_replayed_run_writes_nothing_into_the_fixture(tmp_path):
    """Hermeticity, asserted rather than assumed: the committed tree's file set and
    digests are unchanged after a full replay + decide."""
    fixture = os.path.join(S6_FIXTURES, "good")
    before = dict((rel, kc.sha256_file(os.path.join(fixture, rel)))
                  for rel in _relative_files(fixture))
    replay_and_decide(tmp_path, "good")
    after = dict((rel, kc.sha256_file(os.path.join(fixture, rel)))
                 for rel in _relative_files(fixture))
    assert after == before


# ---------------------------------------------------------------------------
# Rule 5 versus rule 4 -- the separation that is the whole point of the class
# ---------------------------------------------------------------------------

def test_novel_not_high_trips_rule_five_and_not_rule_four(tmp_path):
    """`novel-not-high/` is a novel verdict (S6's `tolerance`) at `medium` with
    `reaches_pixels: false`.

    Without the `reaches_pixels: false` half, one `medium` fixture makes rule 4
    and rule 5 both red and NEITHER is actually tested: whichever of the two was
    broken, the fixture would still fail. Asserting rule 4 PASSES here is the
    assertion that carries the separation.
    """
    code, report, _run_dir = replay_and_decide(tmp_path, "novel-not-high")
    assert code == kc.EXIT_POLICY
    assert rule_entry(report, 5)["status"] == "fail"
    assert rule_entry(report, 4)["status"] == "pass", (
        "novel-not-high must not also trip rule 4, or neither rule is tested")
    assert failing_rules(report) == [5]

    # And the fixture really is the shape the reasoning above depends on.
    manifest = _batch_manifest(os.path.join(S6_FIXTURES, "novel-not-high"), 1)
    offending = [r for r in manifest["rulings"] if r["confidence"] == "medium"]
    assert offending, "the fixture carries no medium ruling at all"
    for ruling in offending:
        assert ruling["verdict"] in kd.NOVEL_VERDICTS["S6"]
        assert ruling["reaches_pixels"] is False


def test_medium_on_pixels_trips_rule_four_and_not_rule_five(tmp_path):
    """The mirror image, which is what makes the pair a partition rather than an
    overlap: a NON-novel verdict at `medium` that does reach shipped pixels."""
    code, report, _run_dir = replay_and_decide(tmp_path, "medium-on-pixels")
    assert code == kc.EXIT_POLICY
    assert rule_entry(report, 4)["status"] == "fail"
    assert rule_entry(report, 5)["status"] == "pass"
    assert any("reaches pixels" in line for line in rule_entry(report, 4)["detail"])

    manifest = _batch_manifest(os.path.join(S6_FIXTURES, "medium-on-pixels"), 1)
    offending = [r for r in manifest["rulings"] if r["confidence"] == "medium"]
    assert offending
    for ruling in offending:
        assert ruling["reaches_pixels"] is True
        assert ruling["verdict"] not in kd.NOVEL_VERDICTS["S6"]


def test_provenance_mismatch_is_rule_four_clause_two(tmp_path):
    """Clause 2 fires on the provenance table, not on the confidence: the fixture
    is `high` throughout, so a rule 4 failure here can only be the evidence half."""
    code, report, _run_dir = replay_and_decide(tmp_path, "provenance-mismatch")
    assert code == kc.EXIT_POLICY
    entry = rule_entry(report, 4)
    assert entry["status"] == "fail"
    assert not any("medium confidence" in line for line in entry["detail"])
    assert any("must cite" in line or "may not cite" in line
               for line in entry["detail"]), entry["detail"]

    manifest = _batch_manifest(os.path.join(S6_FIXTURES, "provenance-mismatch"), 1)
    assert all(r["confidence"] == "high" for r in manifest["rulings"])


# ---------------------------------------------------------------------------
# §3.7 -- the schema has exactly one owner
# ---------------------------------------------------------------------------

def _emit_schema(sid):
    proc = subprocess.run([sys.executable, DECIDE_PY, "--emit-schema", "--stage", sid],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)
    assert proc.returncode == kc.EXIT_OK, proc.stderr
    return proc.stdout


@pytest.mark.parametrize("sid", ["S1", "S2", "S3", "S4", "S5", "S6", "S7"])
def test_emit_schema_is_the_literal_the_module_enforces(sid):
    """The "one owner" property of `ai-overlap-decide.py`: `--emit-schema --stage
    <Sn>` emits the same bytes the module re-validates against, "so the schema can
    never drift between what the model was told and what is enforced."

    All seven stages, because a per-stage specialization is precisely where a
    second literal would be introduced.
    """
    emitted = _emit_schema(sid)
    owned = json.dumps(kd.stage_schema(sid), ensure_ascii=False, indent=2) + "\n"
    assert emitted == owned
    assert json.loads(emitted)["properties"]["stage"]["const"] == sid


def test_every_fixture_carries_the_emitted_schema_byte_for_byte():
    """The recorded runs are S6, so S6 is where "what the model was told" exists as
    bytes on disk and can be compared with what `--emit-schema` prints.

    Both the stage-level copy and every per-batch copy are checked: the agent
    reads the per-batch one, and a bundle shipping a stale copy in either place is
    the drift this property forbids.
    """
    emitted = _emit_schema("S6")
    checked = 0
    for name in sorted(os.listdir(S6_FIXTURES)):
        fixture = os.path.join(S6_FIXTURES, name)
        if not os.path.isdir(fixture):
            continue
        paths = [os.path.join(fixture, "S6.schema.json")]
        paths += [os.path.join(batch, "S6.schema.json")
                  for _index, batch in kd.batch_dirs(fixture)]
        for path in paths:
            assert os.path.exists(path), path
            with open(path, "r", encoding="utf-8") as fh:
                assert fh.read() == emitted, "%s is not the emitted schema" % path
            checked += 1
    assert checked == 12 * 3, "expected 12 classes x (1 stage + 2 batch) copies"


def test_emit_schema_refuses_without_a_stage():
    proc = subprocess.run([sys.executable, DECIDE_PY, "--emit-schema"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)
    assert proc.returncode == kc.EXIT_USAGE
    assert "--stage" in proc.stderr


def test_every_stage_schema_pins_its_own_verdict_enum():
    """The universe == batch unit identity is per-stage, and so is the verdict
    enum; a schema that carried another stage's enum would validate a manifest the
    rules then judge by a different table."""
    for sid, verdicts in sorted(kd.VERDICTS.items()):
        schema = kd.stage_schema(sid)
        ruling = schema["properties"]["rulings"]["items"]
        assert tuple(ruling["properties"]["verdict"]["enum"]) == verdicts
        assert set(kd.NOVEL_VERDICTS[sid]) <= set(verdicts)


# ---------------------------------------------------------------------------
# §4.4 -- the multi-turn fallback is not optional
# ---------------------------------------------------------------------------

def test_a_prose_envelope_falls_back_to_out_manifest_and_decides_identically(tmp_path):
    """`ai-overlap-decide.py:761-765`: a multi-turn session that ends on a tool
    error can produce prose instead of JSON, and `--json-schema` was only ever
    validated on single-turn read-only runs. The agent is therefore REQUIRED to
    also write `out/manifest.json`, giving two independent paths.

    "Decides identically" is asserted on the RULES, not merely on the exit code: a
    fallback that reached 0 by skipping validation would satisfy an exit-only
    assertion. `manifest_source` proves the second path was the one taken, and the
    batch-02 half proves the two paths coexist within one run.
    """
    _code, baseline, _dir = replay_and_decide(tmp_path, "good", name="baseline")

    run_dir = os.path.join(str(tmp_path), "prose")
    kd.replay(os.path.join(S6_FIXTURES, "good"), run_dir)
    envelope_path = os.path.join(run_dir, "batch-01", "claude-envelope.json")
    with open(envelope_path, "r", encoding="utf-8") as fh:
        envelope = json.load(fh)
    envelope["result"] = ("I hit a tool error before I could answer; the manifest "
                          "is written to out/manifest.json as the prompt requires.")
    kc.atomic_write_json(envelope_path, envelope)

    report, code = kd.decide(run_dir)
    assert code == kc.EXIT_OK, report["reason"]
    assert report["batches"][0]["manifest_source"] == "out-manifest"
    assert report["batches"][1]["manifest_source"] == "envelope"
    assert report["batches"][0]["fallback_reason"], (
        "the report must record WHY the second path was taken")
    assert ([(r["rule"], r["status"]) for r in report["rules"]]
            == [(r["rule"], r["status"]) for r in baseline["rules"]])

    # The merged RULINGS are identical -- the same bytes reach every downstream
    # stage either way. The merged manifest's own digest is deliberately NOT
    # asserted equal: it records `manifest_source` per batch, so the two runs
    # differ there exactly as they should.
    with open(os.path.join(run_dir, "manifest.merged.json"), "r",
              encoding="utf-8") as fh:
        fallback_merged = json.load(fh)
    with open(os.path.join(str(tmp_path), "baseline", "manifest.merged.json"), "r",
              encoding="utf-8") as fh:
        baseline_merged = json.load(fh)
    assert fallback_merged["rulings"] == baseline_merged["rulings"]
    assert fallback_merged["ruling_batch"] == baseline_merged["ruling_batch"]


def test_the_fallback_validates_a_bad_manifest_exactly_as_the_envelope_path_does(
        tmp_path):
    """"Both paths validate identically" is the load-bearing half: a fallback that
    accepted what the envelope path rejects would be a way around every rule.

    The same fault -- an `abstain` -- is planted once behind a prose envelope and
    once in the envelope itself, and the two runs must reach the same rule.
    """
    outcomes = {}
    for label, prose in (("envelope", False), ("out-manifest", True)):
        run_dir = os.path.join(str(tmp_path), label)
        kd.replay(os.path.join(S6_FIXTURES, "abstain"), run_dir)
        if prose:
            path = os.path.join(run_dir, "batch-01", "claude-envelope.json")
            with open(path, "r", encoding="utf-8") as fh:
                envelope = json.load(fh)
            envelope["result"] = "sorry, I ran out of turns"
            kc.atomic_write_json(path, envelope)
        report, code = kd.decide(run_dir)
        assert report["batches"][0]["manifest_source"] == label
        outcomes[label] = (code, failing_rules(report))
    assert outcomes["envelope"] == outcomes["out-manifest"] == (kc.EXIT_POLICY, [2])


def test_a_prose_envelope_with_no_second_path_is_rule_six(tmp_path):
    """The fallback is a second path, not an excuse: when neither path yields a
    manifest the run is an INVALID manifest at 66, not a quiet pass."""
    run_dir = os.path.join(str(tmp_path), "no-path")
    kd.replay(os.path.join(S6_FIXTURES, "good"), run_dir)
    envelope_path = os.path.join(run_dir, "batch-01", "claude-envelope.json")
    with open(envelope_path, "r", encoding="utf-8") as fh:
        envelope = json.load(fh)
    envelope["result"] = "I could not complete the task."
    kc.atomic_write_json(envelope_path, envelope)
    os.remove(os.path.join(run_dir, "batch-01", "out", "manifest.json"))

    report, code = kd.decide(run_dir)
    assert code == kc.EXIT_AI_MANIFEST
    assert rule_entry(report, 6)["status"] == "fail"
    assert any("neither the envelope nor the required second path" in line
               for line in rule_entry(report, 6)["detail"])


# ---------------------------------------------------------------------------
# §3.7 -- the cross-batch merge
# ---------------------------------------------------------------------------

def test_a_unit_id_in_two_batches_is_rule_one_at_66_never_last_write_wins(tmp_path):
    """§3.7: "A `unit_id` appearing in two batches is a rule 1 failure (66), not a
    silent last-write-wins."

    The duplicate is planted with a DIFFERENT verdict, so a last-write-wins
    implementation would not merely be quiet -- it would ship the second ruling.
    The assertion is therefore both that the run refuses and that the merged
    manifest kept the first, so the failure cannot be argued to be cosmetic.
    """
    run_dir = os.path.join(str(tmp_path), "duplicate")
    kd.replay(os.path.join(S6_FIXTURES, "good"), run_dir)

    first_unit = _batch_manifest(run_dir, 1)["rulings"][0]["unit_id"]
    path = os.path.join(run_dir, "batch-02", "out", "manifest.json")
    with open(path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["rulings"][0]["unit_id"] = first_unit
    manifest["rulings"][0]["value"]["owner_stage"] = "somewhere-else"
    kc.atomic_write_json(path, manifest)
    # The envelope carries its own copy of the manifest; remove it so the required
    # second path is the one read, rather than re-signing a recorded envelope.
    os.remove(os.path.join(run_dir, "batch-02", "claude-envelope.json"))

    report, code = kd.decide(run_dir)
    assert code == kc.EXIT_AI_MANIFEST
    assert 1 in failing_rules(report)
    assert any("never last-write-wins" in line
               for line in rule_entry(report, 1)["detail"])

    with open(os.path.join(run_dir, "manifest.merged.json"), "r",
              encoding="utf-8") as fh:
        merged = json.load(fh)
    survivors = [r for r in merged["rulings"] if r["unit_id"] == first_unit]
    assert len(survivors) == 1
    assert survivors[0]["value"]["owner_stage"] != "somewhere-else"


def test_the_merge_is_the_union_in_universe_order(tmp_path):
    """The merged manifest is what every downstream stage consumes, so its ORDER
    and its coverage are part of the contract, not an implementation detail."""
    _code, report, run_dir = replay_and_decide(tmp_path, "good")
    with open(os.path.join(run_dir, "inputs.json"), "r", encoding="utf-8") as fh:
        universe = json.load(fh)["universe"]
    with open(os.path.join(run_dir, "manifest.merged.json"), "r",
              encoding="utf-8") as fh:
        merged = json.load(fh)

    assert [r["unit_id"] for r in merged["rulings"]] == universe
    assert merged["universe"] == len(universe) == report["universe"]
    assert merged["batch_of"] == 2 and len(merged["batches"]) == 2
    # Two batches really did contribute, or the cross-batch merge is untested.
    assert set(merged["ruling_batch"].values()) == {1, 2}
    assert merged["stage"] == "S6" and merged["stage_name"] == "triage"


def test_the_recorded_run_is_multi_batch_so_the_merge_is_exercised():
    """A single-batch fixture would make every union rule and the merge itself
    vacuous. The reference run splits three findings at two per call."""
    for name in sorted(os.listdir(S6_FIXTURES)):
        fixture = os.path.join(S6_FIXTURES, name)
        if not os.path.isdir(fixture):
            continue
        assert len(kd.batch_dirs(fixture)) == 2, "%s is not multi-batch" % name


def test_coverage_gap_is_a_missing_unit_not_a_missing_batch(tmp_path):
    """Rule 1 is about UNITS, and this class must isolate it.

    Both batches, both envelopes and a non-empty `rulings[]` everywhere: one unit
    of batch-01 simply goes unruled. Emptying a whole batch instead would also
    trip rule 6 (`minItems: 1`), and then a broken rule 1 would still fail this
    fixture at the same exit code -- the conflation §4.4 splits rule 4 and rule 5
    apart to avoid.
    """
    fixture = os.path.join(S6_FIXTURES, "coverage-gap")
    assert len(kd.batch_dirs(fixture)) == 2
    for index in (1, 2):
        assert os.path.exists(os.path.join(fixture, "batch-%02d" % index,
                                           "claude-envelope.json"))
        assert _batch_manifest(fixture, index)["rulings"], (
            "batch-%02d is empty, which trips rule 6 as well as rule 1" % index)

    code, report, _run_dir = replay_and_decide(tmp_path, "coverage-gap")
    assert code == kc.EXIT_AI_MANIFEST
    assert failing_rules(report) == [1]
    assert report["covered"] < report["universe"]
    assert any("no ruling in any batch" in line
               for line in rule_entry(report, 1)["detail"])


# ---------------------------------------------------------------------------
# Aggregation and the precondition boundary
# ---------------------------------------------------------------------------

def test_aggregation_is_two_tier_and_66_outranks_11():
    """`ai-overlap-decide.py:799-805`: any 66-rule failing => 66; else any failure
    => 11; else 0. A run failing BOTH tiers must report 66, or an operator is sent
    to the symptom instead of the cause."""
    both = [{"rule": 2, "name": "no_abstain", "status": "fail",
             "exit_on_fail": kc.EXIT_POLICY, "detail": [], "scope": "batch"},
            {"rule": 7, "name": "run_binding", "status": "fail",
             "exit_on_fail": kc.EXIT_AI_MANIFEST, "detail": [], "scope": "union"}]
    assert kd.aggregate(both)[:2] == ("invalid", kc.EXIT_AI_MANIFEST)
    assert kd.aggregate(both[:1])[:2] == ("stop", kc.EXIT_POLICY)
    assert kd.aggregate([])[:2] == ("proceed", kc.EXIT_OK)


def test_the_decision_is_only_ever_0_11_or_66(tmp_path):
    """§4.4: "`kz_decide.py` owns 66 and 11 and nothing else." 67 belongs to the
    artifact verifier, which evaluates a rule this module does not."""
    seen = set()
    for fixture, _expected, _rule in DECIDE_TABLE:
        code, _report, _run_dir = replay_and_decide(tmp_path, fixture)
        seen.add(code)
    assert seen <= {kc.EXIT_OK, kc.EXIT_POLICY, kc.EXIT_AI_MANIFEST}
    assert seen == {kc.EXIT_OK, kc.EXIT_POLICY, kc.EXIT_AI_MANIFEST}
    for record in kd.evaluate_rules(*_loaded(tmp_path, "good"))[0]:
        assert record["exit_on_fail"] in (kc.EXIT_POLICY, kc.EXIT_AI_MANIFEST)


def _loaded(tmp_path, fixture):
    run_dir = os.path.join(str(tmp_path), "loaded-%s" % fixture)
    kd.replay(os.path.join(S6_FIXTURES, fixture), run_dir)
    with open(os.path.join(run_dir, "inputs.json"), "r", encoding="utf-8") as fh:
        inputs = json.load(fh)
    batches = [kd.load_batch(index, path, inputs["stage"])
               for index, path in kd.batch_dirs(run_dir)]
    return inputs, batches


def test_a_run_that_is_not_there_is_a_precondition_not_a_decision(tmp_path):
    """The split §4.4 draws: "is there a run to decide about" is 13, so the
    DECISION itself can never leave {0, 11, 66}. A manifest that is present but
    unparseable is not a precondition -- it is rule 6 at 66."""
    empty = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty)
    with pytest.raises(kc.KzRefusal) as excinfo:
        kd.decide(empty)
    assert excinfo.value.code == kc.EXIT_PRECONDITION

    no_batches = os.path.join(str(tmp_path), "no-batches")
    os.makedirs(no_batches)
    kc.atomic_write_json(os.path.join(no_batches, "inputs.json"),
                         {"stage": "S6", "universe": []})
    with pytest.raises(kc.KzRefusal) as excinfo:
        kd.decide(no_batches)
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "batch-NN" in str(excinfo.value)


def test_a_present_but_unparseable_manifest_is_rule_six_at_66(tmp_path):
    """The other side of the same split, which is what keeps 13 from swallowing an
    invalid manifest and reporting it as a missing input."""
    run_dir = os.path.join(str(tmp_path), "garbage")
    kd.replay(os.path.join(S6_FIXTURES, "good"), run_dir)
    os.remove(os.path.join(run_dir, "batch-01", "claude-envelope.json"))
    kc.atomic_write_text(os.path.join(run_dir, "batch-01", "out", "manifest.json"),
                         "{ this is not json\n")

    report, code = kd.decide(run_dir)
    assert code == kc.EXIT_AI_MANIFEST
    assert rule_entry(report, 6)["status"] == "fail"


# ---------------------------------------------------------------------------
# The suite's own hermeticity
# ---------------------------------------------------------------------------

def test_no_case_here_can_reach_claude_or_a_credential():
    """§4.4's whole reason for `--replay`: no `claude`, no credential, no network,
    no cost.

    Asserted over this file's own AST rather than over its text, because a text
    scan for the forbidden names contains those names and can never pass. Two
    properties: this module imports nothing that can open a socket, and every
    subprocess it starts is `kz_decide.py` itself.
    """
    import ast

    with open(os.path.abspath(__file__), "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    forbidden_modules = {"urllib", "http", "socket", "requests", "ssl", "ftplib",
                         "kz_ask", "kz_triage"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported & forbidden_modules == set(), imported & forbidden_modules

    launches = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "run"
                and isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
            continue
        launches += 1
        argv = node.args[0]
        assert isinstance(argv, (ast.List, ast.Tuple)), (
            "a subprocess argv built dynamically cannot be audited here")
        names = [n.id for n in ast.walk(argv) if isinstance(n, ast.Name)]
        assert "DECIDE_PY" in names, (
            "this suite may only ever launch kz_decide.py; argv names %s" % names)
    assert launches >= 3, "the subprocess audit found nothing to audit"
