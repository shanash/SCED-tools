"""`kz_langpack` -- the sole writer into SCED-downloads (design §2 item 36, §5.3).

WHY THIS SUITE BUILDS A WHOLE MINIATURE WORKSPACE

Every other koreanize suite can test its subject with dicts, because every other
module's output is a dict. This one's output is FILES IN A GIT REPOSITORY, and
the properties that matter are properties of the filesystem after the fact:
snapshots taken before the write, an intentions log persisted before phase 2, a
`create` that `revert` deletes only when its bytes are the ones this run planned,
and a set equality between a directory listing and a manifest. None of those is
observable from a plan.

So `sandbox` below assembles a real workspace -- a scenario tree, a Korean pack
with donors, a run directory -- and drives the actual v0 chain
(`init` -> `source` -> `scaffold` -> `reuse` -> `objtext` -> `register`) through
it with `--live`. That is the only way to exercise the live path at all: the real
one writes into SCED-downloads, which no test may do.

THE ONE THING THE SANDBOX CANNOT COVER is `kz_config.write_data`'s `data_root`
mirror, because `data_root` is asserted to resolve inside the REAL koreanize
package directory and a sandbox workspace puts it outside. `mirror=False` is
therefore passed throughout and the lock receipt's shape is covered separately,
against a stubbed writer.
"""

import copy
import json
import os

import pytest

import kz_common as kc
import kz_config as kz
import kz_langpack as kl

from tests.koreanize_sandbox import (BACK, KO_FACE, STEM, Sandbox,
                                     default_sandbox)


@pytest.fixture
def sandbox(tmp_path):
    return default_sandbox(tmp_path)


# ---------------------------------------------------------------------------
# 1. The A1 override convention (§5.3, reconstructed from the SPEC)
# ---------------------------------------------------------------------------


def test_scaffold_applies_every_a1_rule(sandbox):
    sandbox.run("scaffold")
    plan = kl.build_plan("scaffold", sandbox.cfg, sandbox.run_dir, sandbox.root)
    override = plan.entries[0].data

    assert "Tags" not in override, "Tags dropped"
    assert set(override["Transform"]) == {"scaleX", "scaleY", "scaleZ"}, \
        "Transform scale-only"
    assert json.loads(override["GMNotes"]) == {"id": "01001"}, \
        "GMNotes inlined and reduced to {id}"
    assert "LuaScript" not in override
    assert override["GUID"] == "111111"
    # Description omitted when ABSENT -- present here because the source has one.
    assert override["Description"] == "Creature."


def test_description_is_omitted_when_absent_never_written_empty():
    source = {"Name": "Card", "GUID": "111111", "CardID": 10001,
              "Nickname": "x", "Description": "",
              "Transform": {"scaleX": 1, "scaleY": 1, "scaleZ": 1}}
    override = kl.build_override({"arkham_id": "01001", "guid": "111111"}, source)
    assert "Description" not in override


def test_the_bytes_are_the_a1_convention(sandbox):
    sandbox.run("scaffold")
    plan = kl.build_plan("scaffold", sandbox.cfg, sandbox.run_dir, sandbox.root)
    raw = kl.serialize(plan.entries[0].data).decode("utf-8")
    assert raw.endswith("\n")
    assert raw.startswith("{\n  ")
    # sced_io passes NO sort_keys, so the ordering has to be a property of the
    # mapping. Round-tripping through json.loads and re-dumping with sort_keys
    # must be byte-identical.
    assert raw == json.dumps(json.loads(raw), indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n"


def test_a_mapping_in_construction_order_is_refused():
    """The property is ASSERTED on the built object, not hoped for.

    `sced_io.py:99` passes no sort_keys, so key order is whatever the caller
    built. That coincides with the A1 convention for `repoint`, which inherits an
    existing mapping's order, and diverges for `scaffold` and `reuse`, which
    build from scratch -- bytes that would change the day a constructor is
    reordered, in files whose whole verification story is byte comparison.
    """
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.assert_key_sorted({"b": 1, "a": 2})
    assert excinfo.value.code == kc.EXIT_ARTIFACT
    # Nested too: CustomDeck is a mapping inside a mapping.
    with pytest.raises(kc.KzRefusal):
        kl.assert_key_sorted({"a": {"z": 1, "y": 2}})
    assert kl.assert_key_sorted(kl._sorted({"b": 1, "a": {"z": 1, "y": 2}}))


def test_object_stem_flattens_a_deck_parented_object():
    """The Korean container is FLAT -- the shipped Midwinter one is 64 files and
    not one subdirectory."""
    assert kl.object_stem("Deck.ddd111/Card01003.333333") == "Card01003.333333"
    assert kl.object_stem("Card01001.111111") == "Card01001.111111"


# ---------------------------------------------------------------------------
# 2. Donor adoption (§3.5) -- the hazard no precedent in the tree can show
# ---------------------------------------------------------------------------


def test_reuse_adopts_the_donors_card_id_and_whole_custom_deck(sandbox):
    sandbox.run("reuse")
    plan = kl.build_plan("reuse", sandbox.cfg, sandbox.run_dir, sandbox.root)
    override = plan.entries[0].data

    assert override["CardID"] == 231701
    assert list(override["CustomDeck"]) == ["2317"]
    deck = override["CustomDeck"]["2317"]
    assert deck["FaceURL"] == KO_FACE
    assert (deck["NumWidth"], deck["NumHeight"]) == (10, 7)
    # The object identity is the TARGET's; only the art is the donor's.
    assert override["GUID"] == "111111"


@pytest.mark.parametrize("mutate,why", [
    (lambda o: o.update({"CardID": 10001}),
     "the target's English CardID retained beside the donor's FaceURL"),
    (lambda o: o["CustomDeck"]["2317"].update({"NumWidth": 7}),
     "the donor's grid not adopted"),
    (lambda o: o.update({"GUID": "d11111"}),
     "the donor's GUID installed over the target's"),
    (lambda o: o.update({"CustomDeck": dict(o["CustomDeck"], **{"99": {}})}),
     "a second CustomDeck key"),
])
def test_donor_adoption_refuses_each_way_it_can_be_got_wrong(sandbox, mutate, why):
    """Retaining the target's English CardID while installing the donor's FaceURL
    yields a valid-LOOKING file that RENDERS THE WRONG CARD, and every prior
    Korean pack is same-atlas harvest, so nothing in this repository exhibits the
    distinction."""
    plan = kl.build_plan("reuse", sandbox.cfg, sandbox.run_dir, sandbox.root)
    override = copy.deepcopy(plan.entries[0].data)
    _report, resolution = kl.load_source(sandbox.run_dir)
    entry = kl.reuse_subset(resolution)[0]

    mutate(override)
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.assert_adoption(override, entry, entry["donor"])
    assert excinfo.value.code == kc.EXIT_RULE_A, why


def test_a_shared_back_keeps_its_english_generic(sandbox):
    """Only the backs this scenario OWNS become Korean."""
    cfg = dict(sandbox.cfg)
    cfg["shared_backs"] = [{"url": BACK, "occurrences_pack_wide": 814,
                            "policy": "never_rewrite"}]
    _report, resolution = kl.load_source(sandbox.run_dir)
    entry = kl.reuse_subset(resolution)[0]
    objects = kl._english_objects(sandbox.run_dir)
    source_obj = kl._source_object_json(
        cfg, objects[entry["object_id"]], sandbox.root)
    override = kl.adopt_donor(kl.build_override(entry, source_obj), entry, cfg)
    assert override["CustomDeck"]["2317"]["BackURL"] == BACK


# ---------------------------------------------------------------------------
# 3. Scoping -- the reuse subset, and the negative that pins it
# ---------------------------------------------------------------------------


def test_every_writing_submode_is_scoped_to_the_reuse_subset(sandbox):
    """3 objects in the population, 2 with donors. Unscoped, the third would be
    an override carrying the ENGLISH FaceURL inside a container the mod
    advertises as Korean, in a set `register`'s scoped acceptance cannot see."""
    source = json.load(open(kc.report_path(sandbox.run_dir, "source", "build"),
                            encoding="utf-8"))
    assert source["counts"]["objects"] == 3
    assert source["counts"]["reuse"] == 2

    for stage in ("scaffold", "reuse", "objtext"):
        plan = kl.build_plan(stage, sandbox.cfg, sandbox.run_dir, sandbox.root)
        assert len(plan.entries) == 2, stage
        assert all("Card01003" not in e.path for e in plan.entries), stage


def test_a_plan_longer_than_counts_reuse_refuses(sandbox, monkeypatch):
    """The scoping IS the predicate (§1.3): a longer plan means the population
    leaked in."""
    real = kl.reuse_subset
    monkeypatch.setattr(kl, "reuse_subset",
                        lambda resolution: real(resolution) + real(resolution))
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.build_plan("scaffold", sandbox.cfg, sandbox.run_dir, sandbox.root)
    assert excinfo.value.code == kc.EXIT_ARTIFACT


def test_a_deck_parented_object_with_a_donor_is_in_the_plan(tmp_path):
    """§5.2 step 5 deleted the `parent_kind == "Deck"` -> `defer` predicate, and
    this is the langpack half of the same regression: the flattened stem has to
    reach a destination."""
    box = Sandbox(tmp_path).build(
        cards=[("333333", "01003", 10003)],
        donors={"01003": ("d33333", 231703, "Donor Three")},
        deck_parented=("01003",))
    box.init()
    box.source()
    plan = kl.build_plan("scaffold", box.cfg, box.run_dir, box.root)
    assert len(plan.entries) == 1
    assert plan.entries[0].path.endswith("/%s/Card01003.333333.json" % STEM)


# ---------------------------------------------------------------------------
# 4. The guard, the cap, and the --live derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count,refuses", [(199, False), (200, False), (201, True)])
def test_the_max_files_written_boundary(sandbox, count, refuses):
    """A hard cap with NO acceptance path (TOLERANCES, hard_cap: true), and exit
    4 -- "nothing was read" is still true, because the count is derived from the
    plan."""
    paths = [os.path.join(kl.container_dir(sandbox.cfg, sandbox.root),
                          "o%d.json" % i) for i in range(count)]
    if refuses:
        with pytest.raises(kc.KzRefusal) as excinfo:
            kz.assert_write_paths(sandbox.cfg, paths, workspace=sandbox.root)
        assert excinfo.value.code == kc.EXIT_GUARD
    else:
        kz.assert_write_paths(sandbox.cfg, paths, workspace=sandbox.root)


def test_a_guard_refusal_over_two_hundred_paths_does_not_emit_one_huge_line(
        sandbox):
    """The cause is the same for all of them, and an uncapped join scrolls the
    first line -- the one that says what is wrong -- off the terminal."""
    paths = [os.path.join(sandbox.root, "SCED", "src", "o%d.json" % i)
             for i in range(150)]
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.assert_write_paths(sandbox.cfg, paths, workspace=sandbox.root)
    assert "and 138 more" in str(excinfo.value)


def test_live_is_required_and_is_derived_not_matched(sandbox):
    """A submode added later inherits the banner without anyone remembering."""
    inside = os.path.join(kl.container_dir(sandbox.cfg, sandbox.root), "x.json")
    outside = os.path.join(sandbox.run_dir, "x.json")
    data_root = os.path.join(sandbox.root, sandbox.cfg["guard"]["data_root"],
                             "locks", "x.json")
    assert kl.requires_live(sandbox.cfg, [inside], sandbox.root) is True
    assert kl.requires_live(sandbox.cfg, [outside], sandbox.root) is False
    # data_root is deliberately NOT banner-bearing: a blast-radius prompt whose
    # radius is a git-tracked JSON receipt trains the operator to type LIVE
    # without reading it (§3.1).
    assert kl.requires_live(sandbox.cfg, [data_root], sandbox.root) is False


def test_forgetting_live_produces_a_rehearsal_not_a_refusal(sandbox):
    """§4.1: "Rehearsal is the default for all seven." An operator who forgets
    the flag gets a rehearsal and a NOTE, which is the safe outcome; a refusal
    would train them to add --live reflexively."""
    report, _plan = kl.run_langpack("scaffold", sandbox.run_dir, live=False,
                                    workspace=sandbox.root, mirror=False,
                                    confirm=False)
    assert report["mode"] == "dry-run"
    assert not os.path.exists(kl.container_dir(sandbox.cfg, sandbox.root))


def test_live_and_dry_run_together_are_a_usage_refusal(sandbox):
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.run_langpack("scaffold", sandbox.run_dir, live=True, dry_run=True,
                        workspace=sandbox.root, mirror=False, confirm=False)
    assert excinfo.value.code == kc.EXIT_USAGE


def test_the_banner_is_required_on_a_live_write_and_a_decline_is_seventy_three(
        sandbox, monkeypatch):
    sandbox.run("scaffold")
    monkeypatch.setattr(kl, "confirm_live", lambda plan, assume_yes=False,
                        stream=None: False)
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.run_langpack("scaffold", sandbox.run_dir, live=True,
                        workspace=sandbox.root, mirror=False, confirm=True)
    assert excinfo.value.code == kc.EXIT_DISPATCH_LIVE_DECLINED
    assert not os.path.exists(kl.container_dir(sandbox.cfg, sandbox.root))


def test_rehearsal_is_the_default_and_writes_only_into_the_scratch_root(sandbox):
    report, plan = sandbox.run("scaffold")
    assert report["mode"] == "dry-run"
    assert not os.path.exists(kl.container_dir(sandbox.cfg, sandbox.root))
    scratch = os.path.join(sandbox.run_dir, "dry-run", "scaffold")
    assert os.path.isdir(scratch)
    for entry in plan.entries:
        assert entry.write_to.startswith(scratch + os.sep)
        assert os.path.exists(entry.write_to)


# ---------------------------------------------------------------------------
# 5. The plan handoff -- the two-process comparison
# ---------------------------------------------------------------------------


def test_a_live_invocation_with_no_persisted_plan_refuses_at_thirteen(sandbox):
    with pytest.raises(kc.KzRefusal) as excinfo:
        sandbox.run("scaffold", live=True)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_a_live_invocation_whose_plan_has_moved_refuses_at_thirteen(sandbox):
    sandbox.run("scaffold")
    path = kl.plan_path(sandbox.run_dir, "scaffold")
    doc = json.load(open(path, encoding="utf-8"))
    doc["entries"][0]["plan_sha256"] = "0" * 64
    kc.atomic_write_json(path, doc)
    with pytest.raises(kc.KzRefusal) as excinfo:
        sandbox.run("scaffold", live=True)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_a_plan_that_lost_an_entry_refuses(sandbox):
    sandbox.run("scaffold")
    path = kl.plan_path(sandbox.run_dir, "scaffold")
    doc = json.load(open(path, encoding="utf-8"))
    doc["entries"] = doc["entries"][:1]
    kc.atomic_write_json(path, doc)
    with pytest.raises(kc.KzRefusal) as excinfo:
        sandbox.run("scaffold", live=True)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_the_executed_write_set_equals_the_plan_the_banner_printed(sandbox):
    """§1.3's `scaffold` row, and it is NOT tautological, because the plan is an
    ARTIFACT: the rehearsal and the live invocation are two processes and the
    comparison spans them."""
    sandbox.run("scaffold")
    persisted = json.load(open(kl.plan_path(sandbox.run_dir, "scaffold"),
                               encoding="utf-8"))
    report, _plan = sandbox.run("scaffold", live=True)

    # Same paths, same ORDER, same length.
    assert [e["path"] for e in report["write_set"]] == \
        [e["path"] for e in persisted["entries"]]
    assert report["binding"]["scaffold.plan.json"]


# ---------------------------------------------------------------------------
# 6. The live commit -- snapshots, intentions, post_sha256 from disk
# ---------------------------------------------------------------------------


def test_the_live_chain_writes_the_reuse_subset_and_registers_it(sandbox):
    reports = sandbox.chain()

    assert reports["scaffold"]["exit_code"] == 0
    assert len(reports["reuse"]["write_set"]) == 2
    assert reports["reuse"]["counts"]["reuse"] == 2

    on_disk = sorted(os.listdir(kl.container_dir(sandbox.cfg, sandbox.root)))
    assert on_disk == ["Card01001.111111.json", "Card01002.222222.json"]

    # register creates the scenario container and appends it to the pack.
    container = json.load(open(sandbox.container_json, encoding="utf-8"))
    assert sorted(container["ContainedObjects_order"]) == \
        ["Card01001.111111", "Card01002.222222"]
    assert container["ContainedObjects_path"] == STEM
    # "containers not overridden": the container is present STRUCTURALLY and
    # carries no translated text.
    assert "Description" not in container
    assert "LuaScript" not in container

    pack = json.load(open(sandbox.pack_json, encoding="utf-8"))
    assert STEM in pack["ContainedObjects_order"]
    assert reports["register"]["registration"] == {"added": [STEM], "removed": []}


def test_every_write_set_entry_carries_a_post_sha256_re_read_from_disk(sandbox):
    sandbox.run("scaffold")
    report, _plan = sandbox.run("scaffold", live=True)
    for entry in report["write_set"]:
        path = os.path.join(sandbox.root, entry["path"])
        assert entry["committed"] is True
        assert entry["post_sha256"] == kc.sha256_file(path)
        assert entry["action"] == "create"
        assert entry["pre_sha256"] is None


def test_a_second_pass_records_modify_with_a_snapshot(sandbox):
    sandbox.chain(("scaffold",))
    sandbox.run("reuse")
    report, _plan = sandbox.run("reuse", live=True)
    for entry in report["write_set"]:
        assert entry["action"] == "modify"
        assert entry["pre_sha256"]
        snapshot = os.path.join(sandbox.root, entry["snapshot"])
        assert os.path.exists(snapshot)
        assert kc.sha256_file(snapshot) == entry["pre_sha256"]


def test_the_intentions_log_is_written_before_phase_two(sandbox):
    """`write_set[]` cannot describe a half-committed batch: `post_sha256` is
    re-read after the batch RETURNS, and a crash means it never does."""
    sandbox.run("scaffold")
    sandbox.run("scaffold", live=True)
    path = os.path.join(kl.snapshot_root(sandbox.run_dir, "scaffold"),
                        "intentions.json")
    assert os.path.exists(path)
    doc = json.load(open(path, encoding="utf-8"))
    assert doc["entries"]
    for entry in doc["entries"]:
        assert entry["committed"] is False
        assert entry["plan_sha256"]


def test_a_crash_between_the_two_phases_leaves_a_revertible_record(sandbox,
                                                                   monkeypatch):
    """§8.4 row A: the narrow window in which some destinations are replaced and
    some are not. The intentions log is what makes it revertible at all."""
    sandbox.run("scaffold")

    boom = {"count": 0}
    real = kc.atomic_write_json_batch

    def half(items):
        boom["count"] += 1
        raise OSError("simulated crash between two phase-2 renames")

    monkeypatch.setattr(kc, "atomic_write_json_batch", half)
    with pytest.raises(OSError):
        sandbox.run("scaffold", live=True)
    monkeypatch.setattr(kc, "atomic_write_json_batch", real)

    path = os.path.join(kl.snapshot_root(sandbox.run_dir, "scaffold"),
                        "intentions.json")
    assert os.path.exists(path), "the log must precede the commit, not follow it"
    entries, source = kl.revert_inputs(sandbox.run_dir, "scaffold")
    assert source == path
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# 7. revert -- the inverse, including the case git diff --numstat cannot express
# ---------------------------------------------------------------------------


def test_revert_deletes_a_created_file(sandbox):
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    assert len(os.listdir(directory)) == 2

    result = kl.revert(sandbox.run_dir, "scaffold", sandbox.cfg,
                       workspace=sandbox.root)
    assert len(result["deleted"]) == 2
    assert result["findings"] == []
    # The directory goes too, because revert emptied it -- see
    # test_revert_prunes_a_directory_it_emptied for why that matters.
    assert not os.path.exists(directory)


def test_revert_refuses_to_delete_a_file_it_did_not_write(sandbox):
    """The case `git diff --numstat` could not express (§8.4 step 4): a `create`
    is deleted ONLY when the file's sha256 equals its plan_sha256. Anything else
    is a guess, and `revert` exists not to make one."""
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    victim = os.path.join(directory, sorted(os.listdir(directory))[0])
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write('{"edited by a human": true}\n')

    result = kl.revert(sandbox.run_dir, "scaffold", sandbox.cfg,
                       workspace=sandbox.root)
    assert len(result["deleted"]) == 1
    assert len(result["findings"]) == 1
    assert "not deleting a file it did not write" in result["findings"][0]
    assert os.path.exists(victim)


def test_revert_restores_a_modified_file_byte_for_byte(sandbox):
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    before = dict((name, kc.sha256_file(os.path.join(directory, name)))
                  for name in os.listdir(directory))

    sandbox.chain(("reuse",))
    after = dict((name, kc.sha256_file(os.path.join(directory, name)))
                 for name in os.listdir(directory))
    assert after != before, "reuse must actually change the bytes"

    result = kl.revert(sandbox.run_dir, "reuse", sandbox.cfg,
                       workspace=sandbox.root)
    assert len(result["restored"]) == 2
    assert result["findings"] == []
    restored = dict((name, kc.sha256_file(os.path.join(directory, name)))
                    for name in os.listdir(directory))
    assert restored == before


def test_revert_with_nothing_to_invert_is_thirteen_not_seventy_two(sandbox):
    """§1.2 exempts `revert` from the predecessor rule and HAS to: its
    predecessor is by construction the stage that just failed. A `revert` with
    nothing to undo is exit 13 naming the directory it looked in, which is a
    different fact from 72 and sends the operator somewhere different."""
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.revert_inputs(sandbox.run_dir, "reuse", sandbox.cfg,
                         workspace=sandbox.root)
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "nothing to revert" in str(excinfo.value)


def test_revert_reads_the_lock_receipt_when_the_run_dir_is_gone(sandbox,
                                                                tmp_path):
    """§8.4: `write_set[]` goes in the RECEIPT tier precisely so a lost <run_dir>
    does not make an in-flight repository write unrevertible."""
    sandbox.chain(("scaffold",))
    report = json.load(open(kc.report_path(sandbox.run_dir, "scaffold", "build"),
                            encoding="utf-8"))
    lock_dir = os.path.join(sandbox.root, sandbox.cfg["guard"]["data_root"],
                            "locks")
    os.makedirs(lock_dir)
    kc.atomic_write_json(
        os.path.join(lock_dir, "%s.lock.json" % sandbox.cfg["slug"]),
        {"slug": sandbox.cfg["slug"],
         "stages": {"scaffold": {"write_set": report["write_set"]}}})

    gone = os.path.join(str(tmp_path), "no-such-run-dir")
    entries, source = kl.revert_inputs(gone, "scaffold", sandbox.cfg,
                                       workspace=sandbox.root)
    assert source.endswith(".lock.json")
    assert len(entries) == 2


def test_revert_is_exempt_and_the_exemption_set_is_exactly_revert():
    assert kz.PREDECESSOR_EXEMPT == frozenset({"revert"})


# ---------------------------------------------------------------------------
# 8. register's set equality, in BOTH directions (the N-9 assertion)
# ---------------------------------------------------------------------------


def test_register_set_equality_passes_on_a_complete_container(sandbox):
    sandbox.chain(("scaffold", "reuse", "objtext"))
    report, _plan = sandbox.run("register")
    check = [c for c in report["checks"] if c["id"] == "R1"][0]
    assert check["status"] == "pass"


def test_register_reports_an_unregistered_file(sandbox):
    """"ContainedObjects_order is the manifest, not the directory -- 11
    unregistered files were dropped silently with rc 0.\""""
    sandbox.chain(("scaffold",))
    stray = os.path.join(kl.container_dir(sandbox.cfg, sandbox.root),
                         "Stray.999999.json")
    with open(stray, "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    unregistered, orphaned = kl.register_set_equality(
        sandbox.cfg, ["Card01001.111111", "Card01002.222222"], sandbox.root)
    assert unregistered == ["Stray.999999"]
    assert orphaned == []


def test_register_reports_an_orphan_order_entry(sandbox):
    sandbox.chain(("scaffold",))
    unregistered, orphaned = kl.register_set_equality(
        sandbox.cfg, ["Card01001.111111", "Card01002.222222", "Ghost.000000"],
        sandbox.root)
    assert unregistered == []
    assert orphaned == ["Ghost.000000"]


def test_register_fails_the_build_when_the_two_sets_disagree(sandbox):
    sandbox.chain(("scaffold", "reuse", "objtext"))
    stray = os.path.join(kl.container_dir(sandbox.cfg, sandbox.root),
                         "Stray.999999.json")
    with open(stray, "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    sandbox.run("register")
    report, _plan = sandbox.run("register", live=True)
    assert report["exit_code"] == kc.EXIT_RULE_A
    assert report["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# 9. objtext over the reuse subset, with the declared remainder
# ---------------------------------------------------------------------------


def test_objtext_writes_a_nickname_for_every_reuse_object(sandbox):
    report, _plan = sandbox.run("objtext")
    check = [c for c in report["checks"] if c["id"] == "O1"][0]
    assert check["status"] == "pass"
    assert report["counts"]["objects_with_text"] == 2


def test_objtext_declares_the_remainder_rather_than_covering_it(sandbox):
    """§1.2: the objects `source` decided `manufacture` are REPORTED, never
    silently skipped. That is what makes v0's partial coverage declared."""
    report, _plan = sandbox.run("objtext")
    source = json.load(open(kc.report_path(sandbox.run_dir, "source", "build"),
                            encoding="utf-8"))
    counts = source["counts"]
    assert report["counts"]["objects_without_korean_text"] == (
        counts["manufacture"] + counts["defer"] + counts["unresolved"])
    assert report["counts"]["objects_without_korean_text"] == 1


def test_objtext_adopts_the_donors_text_verbatim(sandbox):
    """The shipped Korean packs keep object text in English and carry the Korean
    in the ART, so mirroring the donor IS the rule -- a check asserting Hangul
    here would fail on every correct run."""
    plan = kl.build_plan("objtext", sandbox.cfg, sandbox.run_dir, sandbox.root)
    nicknames = sorted(e.data["Nickname"] for e in plan.entries)
    assert nicknames == ["Donor One", "Donor Two"]


def test_a_donor_with_no_nickname_is_named_not_skipped(sandbox, monkeypatch):
    real = kl.reuse_subset

    def stripped(resolution):
        out = copy.deepcopy(real(resolution))
        out[0]["donor"]["nickname"] = None
        return out

    monkeypatch.setattr(kl, "reuse_subset", stripped)
    report, _plan = sandbox.run("objtext")
    check = [c for c in report["checks"] if c["id"] == "O1"][0]
    assert check["status"] == "fail"
    assert len(check["detail"]) == 1


# ---------------------------------------------------------------------------
# 10. The lock receipt is HANDED to kz_config, never written here
# ---------------------------------------------------------------------------


def test_the_lock_mirror_goes_through_kz_config(sandbox, monkeypatch):
    """`guard.forbidden` lists "SCED-tools/" and this module's refusal-before-read
    is the property §3.1 preserves by putting the exemption in kz_config."""
    seen = {}

    def fake_write_data(cfg, subdir, relpath, data, workspace=None):
        seen.update({"subdir": subdir, "relpath": relpath, "data": data})
        return "/dev/null"

    monkeypatch.setattr(kz, "write_data", fake_write_data)
    sandbox.chain(("scaffold",))
    report = json.load(open(kc.report_path(sandbox.run_dir, "scaffold", "build"),
                            encoding="utf-8"))
    kl.mirror_lock(sandbox.cfg, sandbox.run_dir, report, sandbox.root)

    assert seen["subdir"] == "locks"
    assert seen["relpath"] == "%s.lock.json" % sandbox.cfg["slug"]
    entry = seen["data"]["stages"]["scaffold"]
    # Paths and hashes only -- the receipt carries no bytes.
    assert entry["write_set"]
    assert all(set(e) <= {"path", "action", "pre_sha256", "post_sha256",
                          "committed", "plan_sha256", "snapshot"}
               for e in entry["write_set"])
    assert "data" not in json.dumps(entry)


def test_a_write_under_sced_tools_is_refused_before_any_read(sandbox):
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.assert_write_paths(
            sandbox.cfg,
            [os.path.join(sandbox.root, "SCED-tools", "scripts", "x.json")],
            workspace=sandbox.root)
    assert excinfo.value.code == kc.EXIT_GUARD


def test_a_write_into_the_scenario_tree_is_refused(sandbox):
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.assert_write_paths(
            sandbox.cfg,
            [os.path.join(sandbox.scenario_dir, "x.json")],
            workspace=sandbox.root)
    assert excinfo.value.code == kc.EXIT_GUARD


# ---------------------------------------------------------------------------
# 11. v1 submodes, the AI partition, and --selftest
# ---------------------------------------------------------------------------


def test_repoint_is_a_v1_submode_and_says_so(sandbox):
    with pytest.raises(kc.KzRefusal) as excinfo:
        sandbox.run("repoint")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_every_submode_is_declared_ai_forbidden():
    for submode in kl.SUBMODES:
        assert kc.AI_CONTRACT[submode] is False


def test_a_langpack_report_carrying_an_ai_block_cannot_be_written(sandbox):
    report = kc.new_report("reuse", "test-scenario", mode="build",
                           ai={"used": True, "stage_id": "S6"})
    with pytest.raises(kc.KzRefusal) as excinfo:
        kc.assert_ai_contract(report, cfg=sandbox.cfg)
    assert excinfo.value.code == kc.EXIT_GUARD


def test_module_selftest_is_clean():
    assert kl.selftest(verbose=False) == []


@pytest.mark.parametrize("fault", kl.FAULTS)
def test_each_declared_fault_is_exercised(fault):
    assert kl.selftest(fault, verbose=False) == []


def test_an_unknown_submode_is_a_usage_refusal(sandbox):
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.run_langpack("nonsense", sandbox.run_dir, workspace=sandbox.root)
    assert excinfo.value.code == kc.EXIT_USAGE


def test_a_non_consumable_source_report_stops_the_stage(sandbox):
    path = kc.report_path(sandbox.run_dir, "source", "build")
    doc = json.load(open(path, encoding="utf-8"))
    doc["consumable"] = False
    doc["consumable_blocked_by"] = "exit_code == 13"
    kc.atomic_write_json(path, doc)
    with pytest.raises(kc.KzRefusal) as excinfo:
        sandbox.run("scaffold")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


# ---------------------------------------------------------------------------
# 12. The nightly interlock (§5.10)
# ---------------------------------------------------------------------------


def test_the_planned_base_is_recorded_in_the_binding(sandbox):
    """§5.10 hazard A. A PROBE closes nothing -- it is a TOCTOU race, and nothing
    stops a write that begins at 02:16 from still running when the driver takes
    the lock at 02:17. Recording the base is what lets `--status` say "re-plan;
    do NOT revert" instead of leaving the operator to guess."""
    # A sandbox workspace is not a git checkout, so base_sha() there is None and
    # the key is correctly ABSENT -- the binding never carries a field it could
    # not measure. The recording itself is exercised with the resolver stubbed.
    assert kl.base_sha(sandbox.root) is None
    report, _plan = sandbox.run("scaffold")
    assert "SCED-downloads@origin/korean" not in report["binding"]

    import kz_langpack
    real = kz_langpack.base_sha
    kz_langpack.base_sha = lambda workspace=None: "a" * 40
    try:
        report, _plan = sandbox.run("scaffold")
    finally:
        kz_langpack.base_sha = real
    assert report["binding"]["SCED-downloads@origin/korean"] == "a" * 40


def test_base_sha_reads_origin_korean_through_the_git_allowlist():
    """`rev-parse` is one of the four verbs §1.4 admits, and this is one of the
    four requirements in the design that actually run git."""
    sha = kl.base_sha()
    if sha is None:
        pytest.skip("SCED-downloads has no origin/korean in this checkout")
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
    assert "rev-parse" in kc.GIT_READONLY


def test_the_stamp_names_the_stage_the_roots_and_the_base(sandbox, tmp_path):
    """The corroboration a SILENT NIGHT is diagnosed from: if koreanize holds the
    lock when the driver fires, the driver hits a BARE `exit 3` before its own
    trap is installed -- no Discord of any grade, no log, no last-run.json, and
    `3` is not in the default-deny dispatch allow-list either."""
    sandbox.run("scaffold")
    plan = kl.build_plan("scaffold", sandbox.cfg, sandbox.run_dir, sandbox.root)
    path = kl.write_stamp(sandbox.cfg, plan, sandbox.root)
    doc = json.load(open(path, encoding="utf-8"))
    assert doc["stage"] == "scaffold"
    assert doc["pid"] == os.getpid()
    assert doc["planned_files"] == 2
    assert doc["write_roots"]
    assert kl.remove_stamp(sandbox.cfg, sandbox.root) is True
    assert not os.path.exists(path)


def test_a_stamp_another_process_wrote_is_never_removed(sandbox):
    """Removed under the SAME compare-and-delete rule as the lock: a stamp this
    process did not write is another process's diagnosis, not litter."""
    path = kl.stamp_path(sandbox.cfg["slug"], sandbox.root)
    kc.atomic_write_json(path, {"stage": "reuse", "pid": os.getpid() + 999999})
    assert kl.remove_stamp(sandbox.cfg, sandbox.root) is False
    assert os.path.exists(path)


def test_the_interlock_is_on_by_default_and_off_only_by_a_named_parameter():
    """`interlock=False` is a named parameter and NOT an env var or a flag,
    precisely so that no operator invocation can reach it."""
    import inspect
    signature = inspect.signature(kl.run_langpack)
    assert signature.parameters["interlock"].default is True
    with open(os.path.join(os.path.dirname(kl.__file__), "kz_langpack.py"),
              encoding="utf-8") as handle:
        body = handle.read()
    assert "--interlock" not in body, "the interlock must have no CLI flag"
    assert "KOREANIZE_INTERLOCK" not in body, "and no env override"


def test_the_schedule_window_is_the_union_over_every_repo():
    """A window derived from ONE repo's bounds refuses at the wrong times,
    because the lock is workspace-wide -- its purpose is to stop the 02:17 run
    from overlapping the 02:47 one."""
    _inside, window = kz.in_schedule_window()
    assert window["start"] and window["end"]
    assert window["start_from"] and window["end_from"]
    # Never hardcoded: everything downstream renders from sync-schedule.json.
    assert window["source"].endswith("sync-schedule.json")


def test_revert_prunes_a_directory_it_emptied(sandbox):
    """`git status --porcelain` is empty either way -- git does not track empty
    directories -- so this is not what makes the acceptance predicate hold. It
    matters for the NEXT run: `register_set_equality` lists the container
    directory, and an emptied-but-present one reads as a container that exists
    and has no children, which is a different diagnosis from one never created.
    """
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    assert os.path.isdir(directory)
    result = kl.revert(sandbox.run_dir, "scaffold", sandbox.cfg,
                       workspace=sandbox.root)
    assert result["pruned"]
    assert not os.path.exists(directory)


def test_revert_leaves_a_directory_that_still_holds_anything(sandbox):
    """`os.rmdir` FAILS on a non-empty directory rather than recursing, so a
    directory holding a file this run did not write -- including one revert
    correctly refused to delete -- survives untouched. Same restraint as the
    created-file rule."""
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    stranger = os.path.join(directory, "NotOurs.aaaaaa.json")
    with open(stranger, "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    result = kl.revert(sandbox.run_dir, "scaffold", sandbox.cfg,
                       workspace=sandbox.root)
    assert result["pruned"] == []
    assert os.path.exists(stranger)


def test_revert_never_prunes_outside_the_write_roots(sandbox, tmp_path):
    """Only a parent of a path this revert actually deleted is a candidate, and
    it must resolve under guard.write_roots."""
    outside = os.path.join(str(tmp_path), "elsewhere")
    os.makedirs(outside)
    pruned = kl._prune_empty_dirs(sandbox.cfg,
                                  [os.path.relpath(os.path.join(outside, "x.json"),
                                                   sandbox.root)],
                                  sandbox.root)
    assert pruned == []
    assert os.path.isdir(outside)


# ---------------------------------------------------------------------------
# 13. revert's path guard — the verification FAIL, and its three vectors
# ---------------------------------------------------------------------------
#
# `revert` is the one entry point whose destinations come from a FILE ON DISK
# rather than from a plan it just computed, and it was the one write path in the
# module that never re-asserted the guard. Measured before the fix: a
# hand-written intentions.json naming an absolute path outside the workspace,
# with plan_sha256 set to that file's real digest, made revert DELETE it and
# report no finding at all.


def _stage_record(box, stage, path_value, plan_sha256, action="create",
                  snapshot=None):
    root = kl.snapshot_root(box.run_dir, stage)
    if not os.path.isdir(root):
        os.makedirs(root)
    kc.atomic_write_json(os.path.join(root, "intentions.json"), {
        "entries": [{"path": path_value, "action": action, "pre_sha256": None,
                     "snapshot": snapshot, "plan_sha256": plan_sha256,
                     "committed": False}]})


@pytest.mark.parametrize("label,make_path", [
    ("absolute", lambda box, victim: victim),
    ("traversal", lambda box, victim: os.path.join(
        "..", os.path.relpath(victim, os.path.dirname(box.root)))),
])
def test_revert_refuses_a_recorded_path_outside_the_workspace(sandbox, tmp_path,
                                                              label, make_path):
    """`os.path.join` DISCARDS the workspace when handed an absolute path, so
    joining a recorded path to it is not containment -- the realpath comparison
    is what makes it one."""
    victim = os.path.join(str(tmp_path), "IMPORTANT.txt")
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write("a file koreanize never wrote\n")
    digest = kc.sha256_file(victim)

    _stage_record(sandbox, "reuse", make_path(sandbox, victim), digest)
    result = kl.revert(sandbox.run_dir, "reuse", sandbox.cfg,
                       workspace=sandbox.root)

    assert result["deleted"] == [], "%s escaped the workspace" % label
    assert result["findings"], "the refusal must be REPORTED, not silent"
    assert os.path.exists(victim)


def test_revert_refuses_a_recorded_path_inside_the_workspace_but_off_root(
        sandbox):
    """Containment alone is not enough: `SCED/` is inside the workspace and is
    in guard.forbidden. The guard has to run too."""
    victim = os.path.join(sandbox.root, "SCED", "src", "thing.json")
    os.makedirs(os.path.dirname(victim))
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    _stage_record(sandbox, "reuse", os.path.relpath(victim, sandbox.root),
                  kc.sha256_file(victim))
    result = kl.revert(sandbox.run_dir, "reuse", sandbox.cfg,
                       workspace=sandbox.root)
    assert result["deleted"] == []
    assert "outside the path guard" in " ".join(result["findings"])
    assert os.path.exists(victim)


def test_revert_refuses_a_snapshot_that_escapes_the_workspace(sandbox, tmp_path):
    """The `modify` branch's arbitrary-overwrite half: `shutil.copyfile(snapshot,
    path)` with a record-chosen snapshot.

    Note the payload here is INSIDE the workspace (the sandbox root is tmp_path),
    which is the point: containment alone lets a record name any readable file in
    the tree as the thing to restore, so the destination is legitimate and the
    CONTENT is arbitrary. The snapshot has to be pinned to <run_dir>/revert/.
    """
    outside = os.path.join(str(tmp_path), "payload.json")
    with open(outside, "w", encoding="utf-8") as handle:
        handle.write('{"payload": true}\n')
    target = os.path.join(kl.container_dir(sandbox.cfg, sandbox.root), "x.json")
    os.makedirs(os.path.dirname(target))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    _stage_record(sandbox, "reuse", os.path.relpath(target, sandbox.root),
                  None, action="modify", snapshot=outside)
    result = kl.revert(sandbox.run_dir, "reuse", sandbox.cfg,
                       workspace=sandbox.root)
    assert result["restored"] == []
    assert "is outside" in " ".join(result["findings"])
    with open(target, encoding="utf-8") as handle:
        assert "payload" not in handle.read()


def test_the_sha256_rule_alone_cannot_close_the_hole(sandbox, tmp_path):
    """Why BOTH halves are needed. plan_sha256 authenticates the record against
    the DISK STATE ("does this file match what the JSON claims"), never the
    record against its own PROVENANCE ("did this run write it"). Here the digest
    matches perfectly and the path guard is the only thing that refuses."""
    victim = os.path.join(str(tmp_path), "IMPORTANT.txt")
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write("contents\n")
    _stage_record(sandbox, "reuse", victim, kc.sha256_file(victim))
    result = kl.revert(sandbox.run_dir, "reuse", sandbox.cfg,
                       workspace=sandbox.root)
    assert result["deleted"] == []
    assert os.path.exists(victim)


def test_contained_rejects_absolute_traversal_and_nul():
    ws = "/tmp/ws"
    assert kl.contained("a/b.json", ws) == os.path.realpath("/tmp/ws/a/b.json")
    assert kl.contained("/etc/passwd", ws) is None
    assert kl.contained("../outside/x", ws) is None
    assert kl.contained("a\x00b", ws) is None
    assert kl.contained("", ws) is None
    assert kl.contained(None, ws) is None


def test_a_valid_record_still_reverts_after_the_guard(sandbox):
    """The guard must not break the legitimate path it was added in front of."""
    sandbox.chain(("scaffold",))
    result = kl.revert(sandbox.run_dir, "scaffold", sandbox.cfg,
                       workspace=sandbox.root)
    assert len(result["deleted"]) == 2
    assert result["findings"] == []


# ---------------------------------------------------------------------------
# 14. revert --live takes the same two refusals as a forward write
# ---------------------------------------------------------------------------


def test_revert_has_its_own_banner_and_it_describes_a_deletion(sandbox):
    """§8.4 makes revert first-class: --live, ITS OWN banner, exit 24. The
    write-set banner is the wrong shape twice -- revert has no plan.json, and it
    would say "23 create" before a deletion."""
    sandbox.chain(("scaffold",))
    entries, source = kl.revert_inputs(sandbox.run_dir, "scaffold", sandbox.cfg,
                                       sandbox.root)
    text = kl.revert_banner("scaffold", source, entries, sandbox.root)
    assert "LIVE REVERT" in text
    assert "will DELETE   : 2" in text
    assert "will RESTORE  : 0" in text
    assert "create" not in text.split("will DELETE")[0]


def test_a_declined_revert_banner_is_seventy_three_and_writes_nothing(sandbox,
                                                                      monkeypatch):
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    monkeypatch.setattr(kl, "confirm_revert", lambda *a, **k: False)
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.main(["revert", "--run-dir", sandbox.run_dir, "--stage", "scaffold",
                 "--live"])
    assert excinfo.value.code == kc.EXIT_DISPATCH_LIVE_DECLINED
    assert len(os.listdir(directory)) == 2, "nothing may be deleted on a decline"


def test_a_live_revert_refuses_inside_the_nightly_window(sandbox, monkeypatch):
    """revert mutates SCED-downloads exactly as the forward submodes do, so it
    takes the same two refusals. Leaving them off made the one stage invoked
    DURING AN INCIDENT the one stage that could collide with the 02:17 driver."""
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    monkeypatch.setattr(kl, "confirm_revert", lambda *a, **k: True)

    def inside_window():
        kc.refuse(kc.EXIT_GUARD, "inside the nightly schedule window")

    monkeypatch.setattr(kz, "assert_outside_window", inside_window)
    with pytest.raises(kc.KzRefusal) as excinfo:
        kl.main(["revert", "--run-dir", sandbox.run_dir, "--stage", "scaffold",
                 "--live", "--yes"])
    assert excinfo.value.code == kc.EXIT_GUARD
    assert len(os.listdir(directory)) == 2, "nothing may be deleted on a refusal"


def test_a_dry_revert_takes_no_lock_and_deletes_nothing(sandbox):
    """Rehearsal is the default here too: without --live, revert reports."""
    sandbox.chain(("scaffold",))
    directory = kl.container_dir(sandbox.cfg, sandbox.root)
    rc = kl.main(["revert", "--run-dir", sandbox.run_dir, "--stage", "scaffold"])
    assert rc == kc.EXIT_OK
    assert len(os.listdir(directory)) == 2
