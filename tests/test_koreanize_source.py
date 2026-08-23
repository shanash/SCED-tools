"""`source` -- the 3-way art-source selector (design §2 item 34b, §5.2, §3.4).

WHAT THIS SUITE IS FOR, AND WHAT IT DELIBERATELY IS NOT

The pinned corpus figures -- 222 of 274 ids over the eight Challenge Scenarios,
20 / 17 same-URL rejections over the 16 targets -- need the live SCED-downloads
tree that the nightly force-pushes every night. They are therefore a SMOKE run
(`scenario_root`-gated, skipped when the tree is absent), not this suite's
coverage. The coverage is the synthetic cases below, every one of which is a
fault a corpus either cannot exhibit or exhibits only by accident:

  * a `Deck`-parented object WITH a donor resolving to `reuse` -- the case that
    pins the deleted `parent_kind == "Deck"` -> `defer` predicate DELETED.
    Re-introducing it takes the eight scenarios from 222 resolved ids to 61
    (§5.2 step 5), and no corpus assertion catches that, because a corpus with
    the predicate re-introduced simply reports a smaller number.
  * the two same-URL counters measured on a corpus where they DIFFER. Over the
    eight Challenge Scenarios both are 0 and over War of the Outer Gods both are
    5, so neither can distinguish an object count from an id count; only
    Machinations Through Time (6 objects / 3 ids) can, and it is one scenario out
    of 16 (§3.4).
  * an object with no `arkham_id` -> `defer`. NEITHER reference corpus exercises
    this branch: Midwinter and War of the Outer Gods both report
    `objects_without_arkham_id: 0`.
"""

import json
import os

import pytest

import kz_common as kc
import kz_source as ks
import kz_triage as kt


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

EN = "https://example.invalid/english/atlas.png"
KO1 = "https://example.invalid/korean/one.png"
KO2 = "https://example.invalid/korean/two.png"


def obj(object_id, arkham_id, atlas_id=EN, parent_kind=None, guid="aaa111",
        card_id=100):
    return {"object_id": object_id, "arkham_id": arkham_id, "guid": guid,
            "atlas_id": atlas_id, "parent_kind": parent_kind, "kind": "Card",
            "card_id": card_id, "deck_key": str(card_id // 100),
            "cell": card_id % 100, "num_width": 10, "num_height": 7}


def donor(pack="Korean - Campaigns", face_url=KO1, num_width=10, num_height=7,
          card_id=4000):
    return {"pack": pack, "container": "Container.1", "file": "Container.1/x.json",
            "guid": "bbb222", "card_id": card_id, "deck_key": str(card_id // 100),
            "cell": card_id % 100, "num_width": num_width,
            "num_height": num_height, "face_url": face_url, "back_url": "BACK",
            "back_is_hidden": True, "type": 0, "nickname": None,
            "description": None}


def counts_for(objects, index):
    res = ks.select(objects, index)
    return res, ks.build_counts(res, objects)


# ---------------------------------------------------------------------------
# 1. The three predicates -- 0 / 1 / >= 2 donors
# ---------------------------------------------------------------------------


def test_zero_donors_resolves_to_manufacture():
    res, counts = counts_for([obj("A.1", "82022")], {})
    assert res.entries[0]["decision"] == "manufacture"
    assert res.entries[0]["reason"] == "no donor with Korean art"
    assert counts["manufacture"] == 1 and counts["reuse"] == 0


def test_one_donor_resolves_to_reuse_with_no_ai():
    res, counts = counts_for([obj("A.1", "82022")], {"82022": [donor()]})
    entry = res.entries[0]
    assert entry["decision"] == "reuse"
    assert entry["confidence"] == "high"
    assert entry["source"] == "id_exact"
    assert entry["ai"] is False
    assert entry["donor"]["face_url"] == KO1
    # The donor is annotated against THIS object's English atlas, so the hard
    # filter's own verdict travels with the record §3.5 will adopt from.
    assert entry["donor"]["english_face_url"] == EN
    assert entry["donor"]["face_url_differs_from_english"] is True
    assert counts["reuse"] == 1 and counts["escalations"] == 0


def test_two_donors_escalate_and_are_not_silently_picked():
    index = {"01129": [donor(pack="Korean - Player Cards", face_url=KO1,
                             num_width=1, num_height=1),
                       donor(pack="Korean - Campaigns", face_url=KO2)]}
    res, counts = counts_for([obj("A.1", "01129")], index)
    assert res.entries[0]["decision"] == "unresolved"
    assert res.entries[0]["ai"] is True
    assert res.entries[0]["ai_stage"] == kt.SID
    assert "donor" not in res.entries[0]
    assert counts["escalations"] == 1
    assert counts["escalations_differing_grid"] == 1
    assert counts["reuse"] == 0


def test_two_donors_at_the_same_grid_still_escalate_but_are_not_a_tolerance():
    """`>= 2 donors OR a grid conflict` -- the escalation is on the count, and
    the TOLERANCE is on the grid. Only the second needs --accept-donor-choice."""
    index = {"82022": [donor(face_url=KO1), donor(pack="Korean - Player Cards",
                                                  face_url=KO2)]}
    res, counts = counts_for([obj("A.1", "82022")], index)
    assert counts["escalations"] == 1
    assert counts["escalations_differing_grid"] == 0


# ---------------------------------------------------------------------------
# 2. The Korean-art predicate and its TWO counters (§3.4)
# ---------------------------------------------------------------------------


def test_same_url_donor_is_rejected_and_resolves_to_manufacture():
    """The mirror image of R-E's expensive mistake: a donor whose FaceURL equals
    the English source's is a TEXT-ONLY override and buys no art."""
    res, counts = counts_for([obj("A.1", "82022")], {"82022": [donor(face_url=EN)]})
    assert res.entries[0]["decision"] == "manufacture"
    assert "text-only" in res.entries[0]["reason"]
    assert counts["donor_rejected_same_url_objects"] == 1
    assert counts["donor_rejected_same_url_ids"] == 1
    # The overlap rule: a rejection is a REASON counter, not an outcome, so the
    # object is counted in both places and the totality identity still holds.
    ks.assert_totality(counts)


def test_the_two_same_url_counters_are_allowed_to_differ():
    """Machinations Through Time contributes 6 objects for 3 ids. The v1 WotOG
    acceptance row cannot catch the confusion, because there both are 5."""
    objects = [obj("A.1", "82022"), obj("A.2", "82022", guid="ccc333"),
               obj("A.3", "82023")]
    index = {"82022": [donor(face_url=EN)], "82023": [donor(face_url=EN)]}
    _res, counts = counts_for(objects, index)
    assert counts["donor_rejected_same_url_objects"] == 3
    assert counts["donor_rejected_same_url_ids"] == 2


def test_a_donor_is_judged_against_its_own_objects_english_atlas():
    """Two objects of one id may sit on different English atlases, so the
    rejection is per object rather than against a global URL set."""
    objects = [obj("A.1", "82022", atlas_id=KO1),
               obj("A.2", "82022", atlas_id=EN, guid="ccc333")]
    _res, counts = counts_for(objects, {"82022": [donor(face_url=KO1)]})
    assert counts["donor_rejected_same_url_objects"] == 1
    assert counts["reuse"] == 1


# ---------------------------------------------------------------------------
# 3. The DELETED Deck predicate (§5.2 step 5)
# ---------------------------------------------------------------------------


def test_deck_parented_object_with_a_donor_resolves_to_reuse():
    """THE regression this whole module exists to keep deleted.

    `parent_kind == "Deck"` -> `defer`, applied over the eight Challenge
    Scenarios, takes counts.arkham_ids_resolved from 222 to 61. 40 of 40
    Deck-parented Midwinter objects carry an override whose FaceURL is genuinely
    Korean, and the Midwinter Korean container is FLAT -- 64 files, not one
    subdirectory -- so a Deck-parented object in the scenario tree has an
    ordinary per-object override in the langpack, never a container-level one.
    """
    res, counts = counts_for([obj("Deck.a/Card.1", "82037", parent_kind="Deck")],
                             {"82037": [donor()]})
    assert res.entries[0]["decision"] == "reuse"
    assert res.entries[0]["parent_kind"] == "Deck"
    assert counts["defer"] == 0


def test_parent_kind_is_reported_and_never_a_decision_input():
    """A scenario's Deck shape is a real and useful fact; it is reported."""
    objects = [obj("Deck.a/Card.1", "82037", parent_kind="Deck"),
               obj("Card.2", "82038", parent_kind="Bag"),
               obj("Deck.b/Card.3", "82039", parent_kind="Deck")]
    index = dict((o["arkham_id"], [donor()]) for o in objects)
    res, counts = counts_for(objects, index)
    assert counts["objects_inside_deck"] == 2
    assert [e["decision"] for e in res.entries] == ["reuse"] * 3


# ---------------------------------------------------------------------------
# 4. defer and out_of_scope
# ---------------------------------------------------------------------------


def test_object_with_no_arkham_id_defers():
    """Neither reference corpus exercises this branch, which is why it is here
    as a synthetic case rather than as a corpus assertion (§5.2 step 6)."""
    res, counts = counts_for([obj("A.1", None)], {})
    assert res.entries[0]["decision"] == "defer"
    assert res.entries[0]["reason"] == "no GMNotes id"
    assert counts["defer"] == 1


def test_campaign_guide_id_is_out_of_scope_before_any_donor_lookup():
    """CG71 HAS a Korean - Campaigns override, so a lookup-first implementation
    resolves the scenario guide to `reuse` and ships a PDF as a card."""
    index = {"CG71": [donor()]}
    res, counts = counts_for([obj("Guide.1", "CG71")], index)
    assert res.entries[0]["decision"] == "out_of_scope"
    assert "donor" not in res.entries[0]
    assert counts["out_of_scope"] == 1
    assert counts["reuse"] == 0


# ---------------------------------------------------------------------------
# 5. The totality identity (§3.4), asserted and not reported
# ---------------------------------------------------------------------------


def test_totality_identity_holds_over_a_mixed_population():
    objects = [obj("A.1", "82022"), obj("A.2", "82023"), obj("A.3", None),
               obj("Guide.1", "CG71"),
               obj("A.4", "01129")]
    index = {"82022": [donor()],
             "01129": [donor(face_url=KO1), donor(face_url=KO2, num_width=1,
                                                  num_height=1)]}
    _res, counts = counts_for(objects, index)
    assert (counts["reuse"], counts["manufacture"], counts["defer"],
            counts["unresolved"], counts["out_of_scope"]) == (1, 1, 1, 1, 1)
    assert counts["objects"] == 5
    ks.assert_totality(counts)


def test_totality_identity_refuses_at_67_when_it_does_not_hold():
    with pytest.raises(kc.KzRefusal) as excinfo:
        ks.assert_totality({"reuse": 1, "manufacture": 0, "defer": 0,
                            "unresolved": 0, "out_of_scope": 0, "objects": 2})
    assert excinfo.value.code == kc.EXIT_ARTIFACT


def test_arkham_ids_resolved_is_an_id_count_not_an_object_count():
    """The project's headline figure is an id figure, and 222 of 274 names THIS
    field. Two objects carrying one id resolve to two reuses and ONE id."""
    objects = [obj("A.1", "82022"), obj("A.2", "82022", guid="ccc333")]
    _res, counts = counts_for(objects, {"82022": [donor()]})
    assert counts["reuse"] == 2
    assert counts["arkham_ids_resolved"] == 1


# ---------------------------------------------------------------------------
# 6. The S6 hand-off -- the escalation payload and the ruling contract
# ---------------------------------------------------------------------------


def test_escalation_detail_labels_every_candidate_and_carries_identity():
    donors = [donor(face_url=KO1, num_width=1, num_height=1),
              donor(face_url=KO2)]
    detail = ks.escalation_detail(obj("A.1", "01129"), donors)
    assert detail["kind"] == "donor_choice"
    assert detail["differing_grid"] is True
    assert detail["donor_keys"] == ["D0", "D1"]
    assert "D0" in detail["detail"] and "D1" in detail["detail"]
    assert detail["arkham_id"] == "01129"


def test_the_escalation_gate_is_a_universe_kz_triage_accepts():
    """The two modules meet HERE, and the unit id is keyed on the object rather
    than on the arkham id -- two objects can carry one id (71033 -> 4a2568,
    ccce29) and universe_from_gate refuses a duplicate unit id at exit 13."""
    objects = [obj("A.1", "71033", guid="4a2568"),
               obj("A.2", "71033", guid="ccce29")]
    donors = [donor(face_url=KO1, num_width=1, num_height=1), donor(face_url=KO2)]
    gate = ks.escalation_gate([ks.escalation_detail(o, donors) for o in objects])
    universe, units, identity = kt.universe_from_gate(gate)
    assert universe == [ks.unit_id_for(objects[0]), ks.unit_id_for(objects[1])]
    assert units[universe[0]]["kind"] == "donor_choice"
    assert identity[universe[0]]["arkham_id"] == "71033"
    assert identity[universe[0]]["guid"] == "4a2568"


def test_the_donor_choice_tolerance_fires_only_on_a_differing_grid():
    """§4.1's TOLERANCES row is `two donors at equal confidence and DIFFERENT
    grids`, exit 22. Two donors at one grid is an escalation and not a tolerance."""
    same = ks.escalation_gate([ks.escalation_detail(
        obj("A.1", "82022"), [donor(face_url=KO1), donor(face_url=KO2)])])
    _u, units, _i = kt.universe_from_gate(same)
    assert kt.donor_choice_check(units, {}, False)["status"] == "pass"

    differing = ks.escalation_gate([ks.escalation_detail(
        obj("A.1", "01129"),
        [donor(face_url=KO1, num_width=1, num_height=1), donor(face_url=KO2)])])
    _u, units, _i = kt.universe_from_gate(differing)
    fired = kt.donor_choice_check(units, {}, False)
    assert fired["status"] == "fail"
    assert fired["exit_on_fail"] == kc.EXIT_TOLERANCE
    assert kt.donor_choice_check(units, {}, True)["status"] == "pass"


@pytest.mark.parametrize("action,expected", [
    ("adopt D1, the 10x7 donor", 1),
    ("D0 is the only one at the target grid", 0),
    ("use D0 or D1", None),           # two tokens is not a choice
    ("pick the wider sheet", None),   # no token at all
    ("adopt D7", None),               # out of range
])
def test_parse_donor_choice_requires_exactly_one_named_donor(action, expected):
    ruling = {"verdict": "tolerance", "confidence": "high",
              "value": {"owner_stage": "source", "suggested_action": action}}
    assert ks.parse_donor_choice(ruling, 2)[0] == expected


@pytest.mark.parametrize("ruling", [
    {"verdict": "abstain", "confidence": "high",
     "value": {"suggested_action": "adopt D1"}},
    {"verdict": "tolerance", "confidence": "medium",
     "value": {"suggested_action": "adopt D1"}},
    {"verdict": "tolerance", "confidence": "low",
     "value": {"suggested_action": "adopt D1"}},
    None,
])
def test_parse_donor_choice_refuses_anything_but_a_high_confidence_choice(ruling):
    """§5.2 step 4: a HIGH-confidence choice with a rationale, or exit 11. An
    `abstain` is the model saying it could not tell, and must never become a
    pick."""
    assert ks.parse_donor_choice(ruling, 2)[0] is None


def test_a_high_confidence_ruling_is_folded_back_onto_the_chosen_donor():
    objects = [obj("A.1", "01129")]
    index = {"01129": [donor(face_url=KO1, num_width=1, num_height=1),
                       donor(face_url=KO2)]}
    res = ks.select(objects, index)
    applied = ks.apply_rulings(res, {"results": {"rulings": [{
        "unit_id": ks.unit_id_for(objects[0]),
        "verdict": "tolerance", "confidence": "high",
        "rationale": "the 10x7 sheet is the one the scenario ships",
        "value": {"owner_stage": "source", "suggested_action": "adopt D1"}}]}})
    assert applied == 1
    entry = res.entries[0]
    assert entry["decision"] == "reuse"
    assert entry["source"] == "triage"
    assert entry["donor"]["face_url"] == KO2
    assert entry["rationale"]
    assert "reason" not in entry


def test_an_unparseable_ruling_leaves_the_object_unresolved():
    objects = [obj("A.1", "01129")]
    index = {"01129": [donor(face_url=KO1, num_width=1, num_height=1),
                       donor(face_url=KO2)]}
    res = ks.select(objects, index)
    applied = ks.apply_rulings(res, {"results": {"rulings": [{
        "unit_id": ks.unit_id_for(objects[0]), "verdict": "abstain",
        "confidence": "high", "value": {"suggested_action": "adopt D0"}}]}})
    assert applied == 0
    assert res.entries[0]["decision"] == "unresolved"
    assert "named no single donor" in res.entries[0]["reason"]


def test_a_missing_ruling_leaves_the_object_unresolved():
    objects = [obj("A.1", "01129")]
    index = {"01129": [donor(face_url=KO1), donor(face_url=KO2, num_width=1,
                                                  num_height=1)]}
    res = ks.select(objects, index)
    assert ks.apply_rulings(res, {"results": {"rulings": []}}) == 0
    assert res.entries[0]["decision"] == "unresolved"


# ---------------------------------------------------------------------------
# 7. The donor index
# ---------------------------------------------------------------------------


def test_donor_record_skips_an_override_with_no_atlas():
    """An override with no CustomDeck has no atlas at all. It is skipped here
    rather than admitted and rejected later, because donor_rejected_same_url_* is
    a URL-EQUALITY counter and this is not that fault."""
    assert ks._donor_record("p", "c", "f.json", {"GUID": "a", "CardID": 1}) is None
    assert ks._donor_record("p", "c", "f.json",
                            {"GUID": "a", "CustomDeck": {"1": {"FaceURL": "u"}}}) is None
    rec = ks._donor_record("p", "c", "f.json", {
        "GUID": "abc123", "CardID": 3725,
        "CustomDeck": {"37": {"FaceURL": KO1, "BackURL": "b",
                              "NumWidth": 10, "NumHeight": 7}}})
    assert (rec["deck_key"], rec["cell"], rec["num_width"]) == ("37", 25, 10)


def test_index_document_is_sorted_and_counted():
    doc = ks.index_document({"b": [donor()], "a": [donor(), donor()]})
    assert list(doc["donors"].keys()) == ["a", "b"]
    assert doc["counts"] == {"ids": 2, "donors": 3}


# ---------------------------------------------------------------------------
# 8. resolution_of -- the one accessor (see the module docstring)
# ---------------------------------------------------------------------------


def test_resolution_of_reads_the_declared_results_section():
    report = {"results": {"resolution": [{"object_id": "A.1", "decision": "reuse"}]}}
    assert ks.decisions_by_object(report) == {"A.1": "reuse"}


def test_resolution_of_a_precondition_report_is_empty_not_an_error():
    """The house rule nulls `results` on the PRECONDITION path, and the correct
    reading of that is `nothing was adjudicated`, never a crash downstream."""
    assert ks.resolution_of({"results": None}) == []
    assert ks.resolution_of({}) == []
    assert ks.resolution_of(None) == []


# ---------------------------------------------------------------------------
# 9. --selftest, as a pytest case
# ---------------------------------------------------------------------------


def test_module_selftest_is_clean():
    assert ks.selftest(verbose=False) == []


@pytest.mark.parametrize("fault", ks.FAULTS)
def test_each_declared_fault_is_exercised(fault):
    assert ks.selftest(fault, verbose=False) == []


def test_an_unknown_fault_is_a_usage_refusal():
    with pytest.raises(kc.KzRefusal) as excinfo:
        ks.selftest("no-such-fault", verbose=False)
    assert excinfo.value.code == kc.EXIT_USAGE


# ---------------------------------------------------------------------------
# 10. The corpus smoke run -- §1.3's pinned figures
# ---------------------------------------------------------------------------
#
# These need the live SCED-downloads tree the nightly force-pushes nightly, so
# they are a SMOKE run and not the coverage. They are still worth having: every
# one of §1.3's `source` acceptance rows is checked here, and a silent drift in
# the langpack would otherwise only surface at a live `reuse`.

EIGHT = ["Challenge Scenario - All or Nothing",
         "Challenge Scenario - Bad Blood",
         "Challenge Scenario - By the Book",
         "Challenge Scenario - Enthralling Encore",
         "Challenge Scenario - Laid to Rest",
         "Challenge Scenario - Read or Die",
         "Challenge Scenario - Red Tide Rising",
         "Challenge Scenario - Relics of the Past"]


def _run_corpus(tmp_path, scenario_root, names):
    import kz_init as ki
    totals = {}
    for name in names:
        if not os.path.isdir(os.path.join(scenario_root, name)):
            pytest.skip("scenario %r is not in this checkout" % name)
        run_dir = os.path.join(str(tmp_path), name.replace(" ", "_"))
        report, artifacts = ki.run_init(
            scenario=name, pack="Korean - Campaigns", run_dir=run_dir,
            download_atlases=False)
        ki.emit(report, artifacts, run_dir, mirror=False)
        srep, sart, _t = ks.run_source(run_dir, escalate=False)
        ks.emit(srep, sart, run_dir, cfg=artifacts["scenario.json"])
        for key, value in srep["counts"].items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return totals


@pytest.mark.slow
def test_eight_challenge_scenarios_reproduce_the_v0_acceptance_block(
        tmp_path, scenario_root):
    """§1.3's v0 `source` rows, summed per scenario (a SUM, never a union: over
    the same eight the union is 251 / 222, a denominator 30 lower for no stated
    reason)."""
    if scenario_root is None:
        pytest.skip("SCED-downloads/decomposed/scenario is not checked out")
    t = _run_corpus(tmp_path, scenario_root, EIGHT)

    assert t["objects"] == 298
    assert t["arkham_ids"] == 274
    assert t["guide_ids"] == 7
    # out_of_scope is the campaign-guide branch and EQUALS sum(guide_ids); that
    # equality is the K1 reconciliation, 274 + 7 = 281.
    assert t["out_of_scope"] == t["guide_ids"] == 7
    # The escalation is on v0's CRITICAL PATH: 16 of the 226 reuse objects, 14 of
    # them at differing grids, each --accept-donor-choice or exit 22.
    assert t["escalations"] == 16
    assert t["escalations_differing_grid"] == 14
    # With every escalation resolved, reuse is 226 objects carrying 222 ids.
    assert t["reuse"] + t["escalations"] == 226
    assert t["arkham_ids_resolved"] + t["escalations"] == 222
    # The totality identity, over the sum.
    assert (t["reuse"] + t["manufacture"] + t["defer"] + t["unresolved"]
            + t["out_of_scope"]) == t["objects"]


@pytest.mark.slow
def test_sixteen_targets_reproduce_the_two_same_url_counters(
        tmp_path, scenario_root):
    """20 objects / 17 ids -- both pinned, because they are NOT equal and §3.4
    forbids reporting one as the other. Neither the eight (0 / 0) nor War of the
    Outer Gods (5 / 5) can distinguish them."""
    if scenario_root is None:
        pytest.skip("SCED-downloads/decomposed/scenario is not checked out")
    names = EIGHT + ["Cosmic Pantheon", "Film Fatale",
                     "Machinations Through Time", "The Blob that Ate Everything",
                     "The Grand Oak Hotel", "The Symphony of Erich Zann",
                     "The Woods of the Black Goat", "War of the Outer Gods"]
    t = _run_corpus(tmp_path, scenario_root, names)
    assert t["objects"] == 860
    assert t["arkham_ids"] == 775
    assert t["guide_ids"] == 12
    assert t["out_of_scope"] == 12
    assert t["donor_rejected_same_url_objects"] == 20
    assert t["donor_rejected_same_url_ids"] == 17


@pytest.mark.slow
def test_war_of_the_outer_gods_resolves_no_reuse_at_all(tmp_path, scenario_root):
    """§1.3's v1 row: all five of WotOG's id hits in the Korean packs are
    text-only, so counts.reuse is 0 and the same-URL counters coincide at 5."""
    if scenario_root is None:
        pytest.skip("SCED-downloads/decomposed/scenario is not checked out")
    t = _run_corpus(tmp_path, scenario_root, ["War of the Outer Gods"])
    assert t["objects"] == 62
    assert t["reuse"] == 0
    assert t["manufacture"] > 0
    assert t["donor_rejected_same_url_objects"] == 5
    assert t["donor_rejected_same_url_ids"] == 5


@pytest.mark.slow
def test_a_no_escalate_run_is_never_consumable(tmp_path, scenario_root):
    """--no-escalate is not a degradation path. `By the Book` has 9 escalations,
    so the D2 check fails at 13 and nothing downstream can read the resolution."""
    if scenario_root is None:
        pytest.skip("SCED-downloads/decomposed/scenario is not checked out")
    import kz_init as ki
    name = "Challenge Scenario - By the Book"
    if not os.path.isdir(os.path.join(scenario_root, name)):
        pytest.skip("scenario %r is not in this checkout" % name)
    run_dir = os.path.join(str(tmp_path), "bythebook")
    report, artifacts = ki.run_init(scenario=name, pack="Korean - Campaigns",
                                    run_dir=run_dir, download_atlases=False)
    ki.emit(report, artifacts, run_dir, mirror=False)
    srep, sart, _t = ks.run_source(run_dir, escalate=False)
    path = ks.emit(srep, sart, run_dir, cfg=artifacts["scenario.json"])[-1]

    assert srep["exit_code"] == kc.EXIT_PRECONDITION
    assert srep["verdict"] == "PRECONDITION"
    assert srep["consumable"] is False
    assert srep["results"] is None
    # The unadjudicated objects are named in the check detail, which is then the
    # ONLY record of which they were.
    d2 = [c for c in srep["checks"] if c["id"] == "D2"][0]
    assert d2["status"] == "fail" and len(d2["detail"]) > 1
    # And the report landed on the path §3.4 names, with the index beside it.
    with open(path, "r", encoding="utf-8") as handle:
        on_disk = json.load(handle)
    assert os.path.basename(path) == "source.json"
    assert on_disk["stage"] == "source"
    assert os.path.exists(os.path.join(run_dir, "korean-pack-index.json"))


# ---------------------------------------------------------------------------
# 11. The pack tree digest — §5.2 step 1's binding
# ---------------------------------------------------------------------------


def test_the_donor_index_is_cached_on_the_pack_tree_digest(tmp_path, monkeypatch):
    """§5.2 step 1: "Bind by a tree digest so it is rebuilt when the packs move."
    `kz_init.build_korean_pack_index` implements the pattern for its lighter
    membership-only walk; this one does strictly more per file (a donor record
    with geometry) over ~6,008 ids and 2,138+ files, so it is the one that most
    needed it."""
    calls = {"n": 0}
    real = ks.ki._pack_tree_digest
    monkeypatch.setattr(ks.ki, "_pack_tree_digest",
                        lambda ws: (calls.__setitem__("n", calls["n"] + 1),
                                    real(ws))[1])
    first = ks.build_donor_index()
    second = ks.build_donor_index()
    assert calls["n"] == 2, "the digest is consulted on every call"
    assert first == second
    assert os.path.exists(ks.donor_index_cache_path(
        kc.WORKSPACE_ROOT, ks.pack_tree_digest()))


def test_a_corrupt_cache_entry_costs_a_rebuild_never_a_wrong_answer(tmp_path,
                                                                    monkeypatch):
    digest = ks.pack_tree_digest()
    path = ks.donor_index_cache_path(kc.WORKSPACE_ROOT, digest)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    index = ks.build_donor_index()
    assert index, "a bad cache entry must fall through to a rebuild"


def test_the_index_is_bound_by_the_pack_input_not_by_its_own_output(tmp_path,
                                                                    scenario_root):
    """The distinction is the operator-visible half of the requirement. A binding
    taken over the emitted korean-pack-index.json is compared by `--status`
    against the very file `source` just wrote, so it can never go stale and
    "rebuilt when the packs move" becomes undetectable."""
    if scenario_root is None:
        pytest.skip("SCED-downloads/decomposed/scenario is not checked out")
    import kz_init as ki
    name = "Challenge Scenario - All or Nothing"
    run_dir = os.path.join(str(tmp_path), "run")
    report, artifacts = ki.run_init(scenario=name, pack="Korean - Campaigns",
                                    run_dir=run_dir, download_atlases=False)
    ki.emit(report, artifacts, run_dir, mirror=False)
    srep, sart, _t = ks.run_source(run_dir, escalate=False)
    ks.emit(srep, sart, run_dir, cfg=artifacts["scenario.json"])

    assert srep["binding"]["korean-packs@tree"] == ks.pack_tree_digest()
    # And the drift check reads it back.
    vreport, _path = ks.verify_only(run_dir)
    d5 = [c for c in vreport["checks"] if c["id"] == "D5"][0]
    assert d5["status"] == "pass"


def test_verify_only_reports_pack_drift_at_fourteen(tmp_path, scenario_root):
    """A moved digest does not mean the resolution is WRONG -- it means it was
    computed against a langpack that has since changed. Exit 14 (input drift),
    not 13: the input exists and is readable, it moved."""
    if scenario_root is None:
        pytest.skip("SCED-downloads/decomposed/scenario is not checked out")
    import kz_init as ki
    run_dir = os.path.join(str(tmp_path), "run")
    report, artifacts = ki.run_init(scenario="Challenge Scenario - All or Nothing",
                                    pack="Korean - Campaigns", run_dir=run_dir,
                                    download_atlases=False)
    ki.emit(report, artifacts, run_dir, mirror=False)
    srep, sart, _t = ks.run_source(run_dir, escalate=False)
    ks.emit(srep, sart, run_dir, cfg=artifacts["scenario.json"])

    path = kc.report_path(run_dir, "source", "build")
    doc = json.load(open(path, encoding="utf-8"))
    doc["binding"]["korean-packs@tree"] = "0" * 64
    kc.atomic_write_json(path, doc)

    vreport, _p = ks.verify_only(run_dir)
    d5 = [c for c in vreport["checks"] if c["id"] == "D5"][0]
    assert d5["status"] == "fail"
    assert d5["exit_on_fail"] == kc.EXIT_DRIFT
    assert vreport["exit_code"] == kc.EXIT_DRIFT


def test_the_memo_is_keyed_on_the_digest_so_it_cannot_serve_a_stale_index():
    """Correct by construction: if the packs move the key moves and the entry is
    rebuilt. A memo keyed on the workspace alone would be a genuine footgun --
    this one cannot serve an index for a pack state it did not measure."""
    digest = ks.pack_tree_digest()
    first = ks.build_donor_index(digest=digest)
    assert ks.build_donor_index(digest=digest) is first, "same digest, same object"
    other = ks.build_donor_index(digest="f" * 64)
    assert other is not first, "a different digest must not hit the memo"


def test_an_uncached_build_never_consults_the_memo():
    digest = ks.pack_tree_digest()
    ks.build_donor_index(digest=digest)
    assert ks.build_donor_index(use_cache=False) is not ks.build_donor_index(
        digest=digest)


def test_the_memo_is_bounded():
    """One live entry per distinct pack state is the normal case, but a process
    that walks several workspaces would hold one per workspace per state, each
    ~4 MB of donor records. A miss costs a rebuild, never a wrong answer."""
    digest = ks.pack_tree_digest()
    ks.build_donor_index(digest=digest)
    for n in range(ks._INDEX_MEMO_MAX + 3):
        ks.build_donor_index(digest="%064d" % n)
    assert len(ks._INDEX_MEMO) <= ks._INDEX_MEMO_MAX
