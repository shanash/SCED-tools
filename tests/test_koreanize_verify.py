"""`kz_verify` -- C1-C11 and the TTSMM build gate (design §2 item 37a, §5.8).

THE SHAPE IS `verify-both-packs-negative.py`'s: "Eleven defects staged on
throwaway copies", scored on WHICH CHECK FIRED rather than on the exit code,
because one defect legitimately trips several. An override deleted is C1's
absence and C10's count and C2's other direction all at once; asserting an exit
code would let a suite pass while the check it named stayed silent.

Every negative below therefore names the check it must fire and asserts that
check by id. `_fired(...)` is the whole scoring rule.

"C1-C11 all pass" is a v0 ACCEPTANCE PREDICATE, so the table's totality is
checked here too: a check quietly dropped would make the predicate easier to
satisfy every time it was evaluated, which is the opposite of what an acceptance
predicate is for.
"""

import json
import os

import pytest

import kz_common as kc
import kz_langpack as kl
import kz_verify as kv

from tests.koreanize_sandbox import KO_FACE, Sandbox, default_sandbox


# ---------------------------------------------------------------------------
# Fixtures and the scoring rule
# ---------------------------------------------------------------------------


@pytest.fixture
def registered(tmp_path):
    """A sandbox driven all the way through `register`, live."""
    box = default_sandbox(tmp_path)
    box.chain()
    return box


def subject_of(box):
    return kv.Subject(box.run_dir, box.cfg, box.root)


def results(box):
    return dict((check["id"], check) for check in kv.run_checks(subject_of(box)))


def _fired(box, *check_ids):
    """Assert exactly the named checks fail. Scored on WHICH check, never on an
    exit code -- one staged defect legitimately trips several, and an exit-code
    assertion lets the suite pass while the check it named stays silent."""
    scored = results(box)
    failed = {cid for cid, check in scored.items() if check["status"] == "fail"}
    for cid in check_ids:
        assert cid in failed, (
            "%s did not fire; failing checks were %s"
            % (cid, sorted(failed) or "none"))
    return scored


def override_path(box, stem):
    return os.path.join(kl.container_dir(box.cfg, box.root), "%s.json" % stem)


def edit_override(box, stem, mutate):
    path = override_path(box, stem)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    mutate(data)
    kc.atomic_write_json(path, data)
    return path


FIRST = "Card01001.111111"
SECOND = "Card01002.222222"


# ---------------------------------------------------------------------------
# 0. The table itself
# ---------------------------------------------------------------------------


def test_the_check_table_is_exactly_c1_through_c11():
    assert kv.CHECK_IDS == ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8",
                            "C9", "C10", "C11")
    assert len(kv.CHECKS) == 11


def test_run_checks_refuses_if_the_table_stops_being_c1_to_c11(registered,
                                                               monkeypatch):
    monkeypatch.setattr(kv, "CHECKS", kv.CHECKS[:-1])
    with pytest.raises(kc.KzRefusal) as excinfo:
        kv.run_checks(subject_of(registered))
    assert excinfo.value.code == kc.EXIT_ARTIFACT


def test_a_correctly_written_pack_passes_all_eleven(registered):
    scored = results(registered)
    failed = [cid for cid, check in scored.items() if check["status"] == "fail"]
    assert failed == [], [scored[cid]["detail"] for cid in failed]


# ---------------------------------------------------------------------------
# C1 -- inventory, English residue, strays
# ---------------------------------------------------------------------------


def test_c1_fires_on_an_override_left_on_the_english_atlas(registered):
    """The first defect the record's own C3 could not see: it selected by URL,
    so a drifted url silently left the SELECTED SET and the check passed by
    looking at fewer things. Selection here is by IDENTITY."""
    english = registered.cfg  # noqa: F841 -- read below from card-text-en.json
    card_text = json.load(open(os.path.join(registered.run_dir,
                                            "card-text-en.json"),
                               encoding="utf-8"))
    en_face = card_text["objects"][0]["atlas_id"]
    edit_override(registered, FIRST,
                  lambda d: d["CustomDeck"]["2317"].update({"FaceURL": en_face}))
    scored = _fired(registered, "C1")
    assert "ENGLISH atlas" in " ".join(scored["C1"]["detail"])


def test_c1_fires_on_a_deleted_override(registered):
    os.unlink(override_path(registered, FIRST))
    scored = _fired(registered, "C1", "C10")
    assert "no override was written" in " ".join(scored["C1"]["detail"])


def test_c1_fires_on_a_stray_outside_the_reuse_subset(registered):
    """The 72 objects with no donor must have NO override file at all. An
    English FaceURL inside a container the mod advertises as Korean is the silent
    defect the v0 scoping exists to prevent, and it sits in a set `register`'s
    scoped acceptance is scoped precisely out of seeing (§1.2)."""
    kc.atomic_write_json(override_path(registered, "Card01003.333333"),
                         {"GUID": "333333", "CardID": 10003,
                          "CustomDeck": {"100": {"FaceURL": "x"}}})
    _fired(registered, "C1", "C2", "C10")


# ---------------------------------------------------------------------------
# C2 -- the id set, in both directions
# ---------------------------------------------------------------------------


def test_c2_fires_on_unparseable_gmnotes(registered):
    """An object whose GMNotes went unparseable surfaces as a MISSING ID rather
    than passing unnoticed, which is the whole reason C1 selects by identity."""
    edit_override(registered, FIRST, lambda d: d.update({"GMNotes": "{not json"}))
    scored = _fired(registered, "C2")
    assert "unparseable" in " ".join(scored["C2"]["detail"])


def test_c2_fires_when_a_written_id_disagrees_with_card_text_en(registered):
    edit_override(registered, FIRST,
                  lambda d: d.update({"GMNotes": json.dumps({"id": "99999"})}))
    _fired(registered, "C2")


def test_c2_expands_an_id_carried_by_two_objects_to_both(tmp_path):
    """71033 -> 4a2568, ccce29. OBJECT counts, never id counts -- that expansion
    is exactly what an id-keyed comparison silently collapses."""
    box = Sandbox(tmp_path).build(
        cards=[("4a2568", "71033", 10001), ("ccce29", "71033", 10002)],
        donors={"71033": ("d11111", 231701, "Donor")})
    box.init()
    box.source()
    box.chain()
    subject = subject_of(box)
    assert len(subject.subset) == 2, "both objects must be in scope"
    assert len(subject.written) == 2
    scored = dict((c["id"], c) for c in kv.run_checks(subject))
    assert scored["C2"]["status"] == "pass"


# ---------------------------------------------------------------------------
# C3 -- bytes, not normalised strings
# ---------------------------------------------------------------------------


def test_c3_fires_on_a_url_drifting_by_case(registered):
    """BYTES, not normalised strings: a case-drifted url is a different object in
    R2 and TTS will 404 on it, but every "same url?" check written with .lower()
    says they match."""
    edit_override(
        registered, FIRST,
        lambda d: d["CustomDeck"]["2317"].update({"FaceURL": KO_FACE.upper()}))
    _fired(registered, "C3")


def test_c3_fires_when_one_id_lands_on_two_atlases(tmp_path):
    box = Sandbox(tmp_path).build(
        cards=[("4a2568", "71033", 10001), ("ccce29", "71033", 10002)],
        donors={"71033": ("d11111", 231701, "Donor")})
    box.init()
    box.source()
    box.chain()
    edit_override(box, "Card71033.ccce29",
                  lambda d: d["CustomDeck"]["2317"].update({"FaceURL": "other"}))
    _fired(box, "C3")


# ---------------------------------------------------------------------------
# C4 / C5 -- the arithmetic and the measured grid
# ---------------------------------------------------------------------------


def test_c4_fires_on_a_card_id_off_by_one(registered):
    edit_override(registered, FIRST, lambda d: d.update({"CardID": 231801}))
    _fired(registered, "C4")


def test_c4_fires_on_a_cell_outside_the_sheets_capacity(registered):
    """cell must be INSIDE the sheet's capacity: a 10x7 sheet holds 70 cells, so
    cell 99 addresses a sprite that does not exist and TTS renders the atlas's
    top-left corner instead of failing."""
    edit_override(registered, FIRST, lambda d: d.update({"CardID": 231799}))
    _fired(registered, "C4")


def test_c5_fires_on_a_num_width_of_thirteen(registered):
    edit_override(registered, FIRST,
                  lambda d: d["CustomDeck"]["2317"].update({"NumWidth": 13}))
    _fired(registered, "C5")


def test_c5_fires_when_the_grid_disagrees_with_the_donors_measured_one(
        registered):
    """The measured grid is `init`'s, NEVER a constant table."""
    edit_override(registered, FIRST,
                  lambda d: d["CustomDeck"]["2317"].update({"NumHeight": 5}))
    _fired(registered, "C5")


# ---------------------------------------------------------------------------
# C6 -- GUIDs, scoped to the target set
# ---------------------------------------------------------------------------


def test_c6_fires_on_a_rewritten_guid(registered):
    edit_override(registered, FIRST, lambda d: d.update({"GUID": "999999"}))
    _fired(registered, "C6")


def test_c6_is_scoped_to_the_target_set_and_not_pack_wide(registered):
    """Both packs carry INHERITED duplicate-GUID groups -- 47 of 167 in
    Campaigns, 4 of 12 in Player Cards -- that koreanize did not write and cannot
    fix, so a pack-wide assertion fails on somebody else's data forever. The
    donor container in this sandbox is exactly such a neighbour."""
    donor_dir = os.path.join(registered.langpack, "Korean - Campaigns",
                             "Korean-Campaigns.KoreanC", "SomeOtherScenario.bbbbbb")
    kc.atomic_write_json(os.path.join(donor_dir, "Clash.111111.json"),
                         {"GUID": "111111", "CardID": 1})
    scored = results(registered)
    assert scored["C6"]["status"] == "pass", \
        "a GUID collision OUTSIDE the target set must not fail C6"


# ---------------------------------------------------------------------------
# C7 -- the one class git status cannot show on a case-insensitive volume
# ---------------------------------------------------------------------------


def test_c7_is_a_pure_function_over_paths():
    """Which is what makes it exercisable on a case-insensitive volume at all --
    the two files cannot both exist here, so the check is over the NAMES."""
    assert kv.case_fold_collisions(["A/x.json", "a/x.json"]) == \
        [["A/x.json", "a/x.json"]]
    assert kv.case_fold_collisions(["A/x.json", "A/y.json"]) == []


def test_c7_fires_on_a_synthetic_case_only_pair(registered, monkeypatch):
    subject = subject_of(registered)
    subject.written["card01001.111111"] = subject.written[FIRST]
    check = kv.c7_case_collisions(subject)
    assert check["status"] == "fail"
    assert check["exit_on_fail"] == kc.EXIT_COLLISION


# ---------------------------------------------------------------------------
# C8 -- against the PRE-WRITE state
# ---------------------------------------------------------------------------


def test_c8_compares_against_the_snapshot_and_says_how_many_it_had(registered):
    scored = results(registered)
    assert scored["C8"]["status"] == "pass"
    # `reuse` and `objtext` both modify what `scaffold` created, so there are
    # snapshots to compare -- and the note says how many, because a check with an
    # empty subject that reports `pass` is indistinguishable from one that ran.
    assert "had a pre-write snapshot" in scored["C8"]["note"]
    assert scored["C8"]["note"].split()[0] != "0"


def test_c8_fires_when_a_guid_moved_between_the_snapshot_and_the_file(
        registered):
    edit_override(registered, FIRST, lambda d: d.update({"GUID": "999999"}))
    _fired(registered, "C8", "C6")


# ---------------------------------------------------------------------------
# C9 -- shared backs keep their English generics
# ---------------------------------------------------------------------------


def test_c9_passes_when_the_shared_back_is_carried_through(registered):
    """`init` derives shared_backs[] from the measured BackURL occurrences and
    marks every one never_rewrite, so this sandbox HAS one -- and `reuse` adopts
    the donor's back, which for a same-back donor is the English generic."""
    assert [b for b in registered.cfg["shared_backs"]
            if b["policy"] == "never_rewrite"]
    scored = results(registered)
    assert scored["C9"]["status"] == "pass"


def test_c9_reports_when_a_scenario_declares_no_never_rewrite_back(registered):
    """A check with an empty subject says so rather than reporting a bare pass."""
    subject = subject_of(registered)
    subject.cfg = dict(subject.cfg, shared_backs=[])
    check = kv.c9_shared_backs(subject)
    assert check["status"] == "pass"
    assert "declares no never_rewrite back" in check["note"]


def test_c9_fires_when_a_shared_english_back_is_repointed(registered):
    from tests.koreanize_sandbox import BACK
    subject = subject_of(registered)
    subject.cfg = dict(subject.cfg)
    subject.cfg["shared_backs"] = [{"url": BACK, "policy": "never_rewrite"}]
    edit_override(registered, FIRST,
                  lambda d: d["CustomDeck"]["2317"].update({"BackURL": "KO-BACK"}))
    subject = subject_of(registered)
    subject.cfg = dict(subject.cfg)
    subject.cfg["shared_backs"] = [{"url": BACK, "policy": "never_rewrite"}]
    check = kv.c9_shared_backs(subject)
    assert check["status"] == "fail"
    assert "shared English back was rewritten" in " ".join(check["detail"])


# ---------------------------------------------------------------------------
# C10 -- N-10's spawn-path asymmetry
# ---------------------------------------------------------------------------


def test_c10_reconciles_three_counts_and_not_two(registered):
    """The manifest and the directory can AGREE while both disagree with what
    the resolution says should be there, which is why it is three."""
    scored = results(registered)
    assert scored["C10"]["status"] == "pass"


def test_c10_fires_when_the_manifest_and_the_directory_disagree(registered):
    container = json.load(open(registered.container_json, encoding="utf-8"))
    container["ContainedObjects_order"].append("Ghost.000000")
    kc.atomic_write_json(registered.container_json, container)
    scored = _fired(registered, "C10")
    assert "disagree" in " ".join(scored["C10"]["detail"])


def test_c10_fires_when_the_container_is_missing_entirely(registered):
    os.unlink(registered.container_json)
    scored = _fired(registered, "C10")
    assert "does not exist" in " ".join(scored["C10"]["detail"])


# ---------------------------------------------------------------------------
# C11 -- the load-bearing Lua pin
# ---------------------------------------------------------------------------


def test_c11_fires_when_the_scenario_nickname_is_translated(tmp_path):
    """`SCED/src/mythos/MythosArea.ttslua:209` selects the scenario card by the
    literal English string "Scenario", so translating it does not mistranslate a
    card -- it breaks the mod. The rule existed in the design with no verifier
    named."""
    box = Sandbox(tmp_path)
    box.build(cards=[("111111", "01001", 10001)],
              donors={"01001": ("d11111", 231701, "시나리오")})
    # The ENGLISH object's Nickname is what selects the check's subject.
    path = os.path.join(box.scenario_dir, "TestScenario.aaaaaa",
                        "Card01001.111111.json")
    data = json.load(open(path, encoding="utf-8"))
    data["Nickname"] = kv.SCENARIO_NICKNAME
    kc.atomic_write_json(path, data)
    box.init()
    box.source()
    box.chain()
    _fired(box, "C11")


def test_c11_passes_when_the_pin_survives(tmp_path):
    box = Sandbox(tmp_path)
    box.build(cards=[("111111", "01001", 10001)],
              donors={"01001": ("d11111", 231701, kv.SCENARIO_NICKNAME)})
    path = os.path.join(box.scenario_dir, "TestScenario.aaaaaa",
                        "Card01001.111111.json")
    data = json.load(open(path, encoding="utf-8"))
    data["Nickname"] = kv.SCENARIO_NICKNAME
    kc.atomic_write_json(path, data)
    box.init()
    box.source()
    box.chain()
    scored = results(box)
    assert scored["C11"]["status"] == "pass"


def test_c11_ignores_objects_whose_english_nickname_is_not_the_literal(
        registered):
    scored = results(registered)
    assert scored["C11"]["status"] == "pass"
    assert scored["C11"]["detail"] == []


# ---------------------------------------------------------------------------
# The TTSMM build gate -- its FOUR declared properties
# ---------------------------------------------------------------------------
#
# "the TTSMM build gate rc 0" as a phrase names no binary, asserts no checksum,
# sets no bound and defines no failure code -- against a driver that does all
# four for that same executable. Each property gets a case, and the sha256 one
# gets the deliberately-wrong negative §2 item 37a asks for.


def test_property_one_the_gate_names_a_path():
    assert kv.resolve_ttsmm(env={TTSMM: "/tmp/x"} if False else {}) \
        .endswith("TTSModManager-Darwin")
    assert kv.resolve_ttsmm(env={kv.TTSMM_ENV: "/opt/custom/TTSMM"}) == \
        "/opt/custom/TTSMM"


TTSMM = kv.TTSMM_ENV


def test_property_one_negative_an_absent_binary_is_thirteen(tmp_path):
    with pytest.raises(kc.KzRefusal) as excinfo:
        kv.build_gate(str(tmp_path), env={TTSMM: "/nonexistent/TTSMM"}, run=False)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_the_gate_may_not_borrow_the_nightlys_scratch_copy(tmp_path):
    """daily-sync-local.sh:1749 copies the binary into the nightly's DISPOSABLE
    worktree, P0b refuses any koreanize run that resolves a path there, and the
    tree is deleted when the driver finishes -- a gate pointed at it is a gate
    that vanishes."""
    scratch = os.path.join(kc.WORKSPACE_ROOT, ".local-sync", "scratch",
                           "SCED", "TTSModManager-Darwin")
    with pytest.raises(kc.KzRefusal) as excinfo:
        kv.build_gate(str(tmp_path), env={TTSMM: scratch}, run=False)
    assert excinfo.value.code == kc.EXIT_USAGE


def _real_ttsmm():
    path = kv.resolve_ttsmm(env={})
    if not os.path.exists(path):
        pytest.skip("TTSModManager-Darwin is not present at %s" % path)
    return path


def test_property_two_the_sha256_is_asserted_before_every_invocation(tmp_path):
    path = _real_ttsmm()
    gate = kv.build_gate(str(tmp_path),
                         env={TTSMM: path, kv.TTSMM_SHA_ENV: kc.sha256_file(path)},
                         run=False)
    assert gate["status"] == "pass"
    assert gate["sha256"] == kc.sha256_file(path)


def test_property_two_negative_a_wrong_sha256_is_a_named_refusal(tmp_path):
    """The deliberately-wrong sha256. TTSModManager-Darwin is adhoc/linker-signed
    with Identifier=a.out and no TeamIdentifier, so macOS makes it its OWN TCC
    responsible_path and any Full Disk Access grant is keyed to path + cdhash.
    This refusal is what turns a grant-invalidating replacement into a NAMED
    FAILURE instead of a six-hour wedge."""
    path = _real_ttsmm()
    with pytest.raises(kc.KzRefusal) as excinfo:
        kv.build_gate(str(tmp_path), env={TTSMM: path,
                                          kv.TTSMM_SHA_ENV: "0" * 64}, run=False)
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "sha256" in str(excinfo.value)
    assert "grant" in str(excinfo.value), \
        "the message must say WHY a moved digest matters here"


def test_property_three_the_wall_clock_has_a_default_and_is_overridable():
    assert kv.ttsmm_timeout(env={}) == kv.TTSMM_TIMEOUT_DEFAULT == 1800
    assert kv.ttsmm_timeout(env={kv.TTSMM_TIMEOUT_ENV: "60"}) == 60
    # Plain integer seconds only. An unparseable value must fall back to the
    # default rather than COLLAPSE the bound -- the failure mode CLAUDE.md
    # records for the nightly's own knobs.
    for bad in ("60s", "5m", "0", "abc", "-1", ""):
        assert kv.ttsmm_timeout(env={kv.TTSMM_TIMEOUT_ENV: bad}) == \
            kv.TTSMM_TIMEOUT_DEFAULT, bad


def test_property_four_a_timeout_is_reported_as_a_wedge_not_a_broken_build(
        tmp_path, monkeypatch):
    """A real build failure takes seconds and puts a traceback in the log; a
    wedge leaves the log ending at the last thing that DID work. The two need
    different first moves, so the gate says which it saw."""
    import subprocess

    path = _real_ttsmm()

    class Wedged(object):
        pid = 4242
        returncode = None

        def communicate(self, timeout=None):
            if timeout != 5:
                raise subprocess.TimeoutExpired(cmd="ttsmm", timeout=timeout)
            return (b"", None)

    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: Wedged())
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: None)
    gate = kv.build_gate(str(tmp_path),
                         env={TTSMM: path,
                              kv.TTSMM_SHA_ENV: kc.sha256_file(path),
                              kv.TTSMM_TIMEOUT_ENV: "1"})
    assert gate["status"] == "fail"
    assert gate["rc"] == 143
    joined = " ".join(gate["detail"])
    assert "wedge" in joined and "TCC" in joined


def test_a_non_zero_rc_is_sixty_seven_the_artifact_code(tmp_path, monkeypatch):
    """A mod that will not build is a PRODUCED ARTIFACT that failed structural
    verification, which is what 67 means."""
    import subprocess

    path = _real_ttsmm()

    class Proc(object):
        pid = 4242
        returncode = 2

        def communicate(self, timeout=None):
            return (b"boom", None)

    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: Proc())
    gate = kv.build_gate(str(tmp_path),
                         env={TTSMM: path, kv.TTSMM_SHA_ENV: kc.sha256_file(path)})
    assert gate["status"] == "fail"
    assert gate["exit_on_fail"] == kc.EXIT_ARTIFACT


def test_no_build_asserts_the_first_two_properties_and_stops(tmp_path):
    path = _real_ttsmm()
    gate = kv.build_gate(str(tmp_path),
                         env={TTSMM: path, kv.TTSMM_SHA_ENV: kc.sha256_file(path)},
                         run=False)
    assert gate["status"] == "pass"
    assert gate["rc"] is None
    assert "the build itself was not run" in gate["note"]


# ---------------------------------------------------------------------------
# The stage, end to end
# ---------------------------------------------------------------------------


def _verify(box, **kwargs):
    """`run_verify` with the build gate pointed at the REAL binary.

    A sandbox workspace has no TTSModManager-Darwin, and `--no-build` still
    asserts the gate's first two properties (path, sha256) on purpose -- that is
    what makes `--no-build` a diagnostic rather than a way to skip the gate. So
    the tests supply the path rather than the module relaxing the assertion.
    """
    path = _real_ttsmm()
    return kv.run_verify(box.run_dir, workspace=box.root, build=False,
                         env={TTSMM: path,
                              kv.TTSMM_SHA_ENV: kc.sha256_file(path)}, **kwargs)


def test_verify_refuses_before_register_has_run(tmp_path):
    """verify's predecessor is register (§1.2), and this is the STAGE's own 13 --
    the dispatcher's 72 is a different fact reported by a different actor."""
    box = default_sandbox(tmp_path)
    with pytest.raises(kc.KzRefusal) as excinfo:
        _verify(box)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_verify_passes_over_the_reuse_subset_and_declares_the_remainder(
        registered):
    report = _verify(registered)
    assert report["verdict"] == "PASS"
    assert report["exit_code"] == 0
    assert report["counts"]["checks_failed"] == 0
    assert report["counts"]["verified"] == 2
    assert report["counts"]["objects"] == 3
    # The scoping IS the claim: v0 covers the objects `source` decided `reuse`
    # and DECLARES the rest rather than covering it (§1.2).
    assert report["counts"]["objects_without_korean_text"] == 1
    assert "reuse subset (2 of 3 objects)" == report["results"]["scope"]


def test_verify_is_ai_forbidden_and_carries_no_ai_block(registered):
    report = _verify(registered)
    assert report["ai"] is None
    assert kc.AI_CONTRACT["verify"] is False


def test_a_failed_check_makes_the_report_non_consumable(registered):
    edit_override(registered, FIRST, lambda d: d.update({"GUID": "999999"}))
    report = _verify(registered)
    assert report["verdict"] == "FAIL"
    assert report["consumable"] is False
    assert report["counts"]["checks_failed"] >= 1


def test_module_selftest_is_clean():
    assert kv.selftest(verbose=False) == []


@pytest.mark.parametrize("fault", kv.FAULTS)
def test_each_declared_fault_is_exercised(fault):
    assert kv.selftest(fault, verbose=False) == []


def test_an_unknown_fault_is_a_usage_refusal():
    with pytest.raises(kc.KzRefusal) as excinfo:
        kv.selftest("no-such-fault", verbose=False)
    assert excinfo.value.code == kc.EXIT_USAGE


# ---------------------------------------------------------------------------
# The build gate's two hardened properties
# ---------------------------------------------------------------------------


def test_the_sha256_pin_is_mandatory_not_optional(tmp_path):
    """An OPTIONAL integrity check on a binary this function then EXECUTES is not
    a weaker guarantee, it is none: the operator who most needs it is exactly the
    one who never set the variable. The nightly's own copy of this same binary is
    asserted unconditionally."""
    path = _real_ttsmm()
    with pytest.raises(kc.KzRefusal) as excinfo:
        kv.build_gate(str(tmp_path), env={TTSMM: path}, run=False)
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    message = str(excinfo.value)
    assert kv.TTSMM_SHA_ENV in message
    # The refusal must name the digest to record, so it is one copy-paste to
    # satisfy rather than an obstacle an operator routes around.
    assert kc.sha256_file(path) in message


def test_the_build_runs_in_its_own_process_group(tmp_path, monkeypatch):
    """`with_timeout` kills only its DIRECT child, and on 2026-08-18 TTSMM was a
    GRANDCHILD: it survived the SIGTERM, held an unanswered TCC prompt for
    another 2h16m, blocked the other repo's agent and kept the scratch worktree
    unremovable. Python's default subprocess timeout reproduces that exactly."""
    import subprocess
    path = _real_ttsmm()
    seen = {}

    class Proc(object):
        pid = 4242
        returncode = 0

        def communicate(self, timeout=None):
            seen["timeout"] = timeout
            return (b"", None)

    def popen(argv, **kwargs):
        seen.update(kwargs)
        return Proc()

    monkeypatch.setattr(subprocess, "Popen", popen)
    kv.build_gate(str(tmp_path), env={TTSMM: path,
                                      kv.TTSMM_SHA_ENV: kc.sha256_file(path)})
    assert seen["start_new_session"] is True, \
        "without its own session the timeout cannot reach a grandchild"
    assert seen["timeout"] == kv.TTSMM_TIMEOUT_DEFAULT


def test_a_timeout_kills_the_whole_group(tmp_path, monkeypatch):
    import subprocess
    path = _real_ttsmm()
    killed = {}

    class Proc(object):
        pid = 4242
        returncode = None

        def communicate(self, timeout=None):
            if timeout != 5:          # the reap after the kill
                raise subprocess.TimeoutExpired(cmd="ttsmm", timeout=timeout)
            return (b"", None)

    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: Proc())
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg",
                        lambda pgid, sig: killed.update({"pgid": pgid, "sig": sig}))
    gate = kv.build_gate(str(tmp_path), env={TTSMM: path,
                                             kv.TTSMM_SHA_ENV: kc.sha256_file(path)})
    assert killed["pgid"] == 4242
    assert gate["rc"] == 143
    assert "wedge" in " ".join(gate["detail"])


# ---------------------------------------------------------------------------
# The scope guard — "C1-C11 all pass" must never pass on an empty subject
# ---------------------------------------------------------------------------


def _shrink_resolution(box, keep):
    path = kc.report_path(box.run_dir, "source", "build")
    doc = json.load(open(path, encoding="utf-8"))
    doc["results"]["resolution"] = [
        e for e in doc["results"]["resolution"] if e.get("decision") != "reuse"
    ][:0] + [e for e in doc["results"]["resolution"]
             if e.get("decision") == "reuse"][:keep]
    kc.atomic_write_json(path, doc)


def test_an_empty_reuse_subset_refuses_instead_of_passing_eleven_checks(
        registered):
    """Nine of C1-C11 iterate the reuse subset, so an EMPTY subset made all nine
    report `pass` over nothing -- and "C1-C11 all pass" is the acceptance
    predicate §1.3 gates `register` and `verify` on. That is N-9's defect class
    exactly: a green result whose subject was empty."""
    _shrink_resolution(registered, 0)
    with pytest.raises(kc.KzRefusal) as excinfo:
        _verify(registered)
    assert excinfo.value.code == kc.EXIT_DRIFT
    assert "counts.reuse" in str(excinfo.value)


def test_a_partial_reuse_subset_refuses_too(registered):
    """Not just zero: any disagreement between counts.reuse and the resolution[]
    actually loaded means the two inputs disagree about what is being verified."""
    _shrink_resolution(registered, 1)
    with pytest.raises(kc.KzRefusal) as excinfo:
        _verify(registered)
    assert excinfo.value.code == kc.EXIT_DRIFT


def test_every_check_reports_how_many_objects_were_in_scope(registered):
    """The mechanical half: a reader can tell "verified 2" from "verified 0"
    without cross-referencing counts. C8 and C9 already said so in prose."""
    for check in results(registered).values():
        assert check.get("note"), "%s reports no subject size" % check["id"]
    scored = results(registered)
    assert "2 object(s) in scope" in scored["C2"]["note"]


# ---------------------------------------------------------------------------
# C1's cross-scope clause
# ---------------------------------------------------------------------------


def test_the_cross_scope_clause_has_no_subject_in_v0_and_says_so(registered):
    """Evaluated over every url the overrides carry, this clause fires on the
    DONORS -- correctly by its own logic and wrongly by intent, because `reuse`
    ADOPTS a donor's atlas and sharing it is the entire mechanism (§3.5)."""
    subject = subject_of(registered)
    assert subject.owned_urls() == set()
    assert subject.foreign_overrides(), "the donors are there to be seen"
    check = kv.c1_inventory(subject)
    assert check["status"] == "pass"
    assert "no subject until `upload`" in check["note"]


def test_the_cross_scope_clause_fires_on_an_owned_atlas(registered):
    """N-7's cross-pack duplication: this scenario's OWN art on an object that is
    not ours, which leaves no trace in the container listing at all."""
    donor_dir = os.path.join(registered.langpack, "Korean - Campaigns",
                             "Korean-Campaigns.KoreanC", "SomeOtherScenario.bbbbbb")
    owned = "https://example.invalid/ko/OURS.png"
    kc.atomic_write_json(os.path.join(donor_dir, "Thief.abcdef.json"),
                         {"GUID": "abcdef", "CardID": 100,
                          "CustomDeck": {"1": {"FaceURL": owned, "BackURL": "b",
                                               "NumWidth": 1, "NumHeight": 1}}})
    kc.atomic_write_json(os.path.join(registered.run_dir, "atlas-urls.json"),
                         {"urls": {"front": owned}})
    subject = subject_of(registered)
    assert subject.owned_urls() == {owned}
    check = kv.c1_inventory(subject)
    assert check["status"] == "fail"
    assert "OWN atlas urls" in " ".join(check["detail"])


def test_foreign_overrides_excludes_this_scenarios_own_container(registered):
    subject = subject_of(registered)
    stems = {stem for stem, _obj in subject.foreign_overrides()}
    assert not (stems & set(subject.written)), \
        "this scenario's own overrides are not foreign to it"
