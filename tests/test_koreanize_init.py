"""kz_init.py -- the population rule of design §3.3 and the stage of §5.1.

The corpus rows at the bottom are a SMOKE run: they need the live SCED-downloads
tree the nightly force-pushes nightly, so they skip when it is absent. The
coverage is the synthetic cases above them, and three of those exist precisely
because NEITHER reference corpus can exhibit the fault they pin:

  - clause 2 (an excluded kind carrying a GMNotes.id) has zero violations over
    all 16 target scenarios, so it fires on nothing that exists;
  - a `Card` with no GMNotes id -- both corpora report objects_without_arkham_id 0;
  - a case-only path collision, which a case-insensitive volume cannot show.
"""

import collections
import json
import os
import subprocess
import sys

import pytest

import kz_common as kc
import kz_init as ki

WORKSPACE = kc.WORKSPACE_ROOT


# ---------------------------------------------------------------------------
# A synthetic decomposed scenario tree, so the walk is testable with no corpus
# ---------------------------------------------------------------------------

def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def card(guid, arkham_id=None, card_id=None, deck=None, nickname=None, kind="Card",
         inline_id=True):
    obj = {"Name": kind, "GUID": guid, "Nickname": nickname or guid}
    if card_id is not None:
        obj["CardID"] = card_id
    if deck is not None:
        obj["CustomDeck"] = deck
    if arkham_id and inline_id:
        obj["GMNotes"] = json.dumps({"id": arkham_id, "type": "Card"}, indent=2)
    return obj


GRID_8x5 = {"9175": {"NumWidth": 8, "NumHeight": 5,
                     "FaceURL": "https://example/face-8x5", "BackURL": "https://example/back"}}


def build_tree(tmp_path, children, container_kind="Bag", stem="Scenario.aaaaaa"):
    """Lay out <scenario_dir>/<stem>.json + <stem>/ with `children` inside it.

    `children` is a list of (name, object, [grandchildren]) triples; a triple with
    grandchildren becomes a container with its own ContainedObjects_order.
    """
    scenario_dir = str(tmp_path / "A Scenario")
    root_dir = os.path.join(scenario_dir, stem)

    def emit(dirpath, entries):
        order = []
        for name, obj, kids in entries:
            order.append(name)
            if kids:
                # A container carries its OWN ContainedObjects_order -- the walk
                # reads the manifest, not the directory, so a container written
                # without one has no children as far as kz_init is concerned.
                child_order = emit(os.path.join(dirpath, name), kids)
                obj = dict(obj, ContainedObjects_order=child_order,
                           ContainedObjects_path=name)
            write_json(os.path.join(dirpath, name + ".json"), obj)
        return order

    order = emit(root_dir, children)
    write_json(root_dir + ".json",
               {"Name": container_kind, "GUID": "root00", "Nickname": "root",
                "ContainedObjects_order": order})
    return scenario_dir, root_dir


# ---------------------------------------------------------------------------
# Clause 1 -- kind, including CardCustom
# ---------------------------------------------------------------------------

def test_clause1_cardcustom_is_a_population_kind():
    """Excluding CardCustom dropped 83 real cards silently across the 16 targets,
    concentrated in the eight Challenge Scenarios that are v0's whole set."""
    assert "CardCustom" in ki.POPULATION_KINDS
    assert set(ki.POPULATION_KINDS) == {"Card", "Custom_PDF", "CardCustom"}


def test_clause1_excluded_kinds_are_tallied_and_never_loop(tmp_path):
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
        ("Tile.bbb001", {"Name": "Custom_Tile", "GUID": "bbb001"}, None),
        ("Note.ccc001", {"Name": "Notecard", "GUID": "ccc001"}, None),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    assert walk.objects_reached == 1
    assert dict(walk.excluded_by_kind) == {"Custom_Tile": 1, "Notecard": 1}


# ---------------------------------------------------------------------------
# Clause 4 -- file identity, applied BEFORE clause 3
# ---------------------------------------------------------------------------

def test_clause4_a_name_repeated_in_one_order_list_is_visited_once(tmp_path):
    """N copies of one card in a deck are one card, not N.

    Counting visits instead yields Midwinter 89/64/25 and WotOG 79/62/17 for
    reached / objects / twice -- reproducing neither pinned row.
    """
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
    ])
    # Rewrite the order list so the same name appears sixteen times.
    root_json = root_dir + ".json"
    with open(root_json, encoding="utf-8") as handle:
        root = json.load(handle)
    root["ContainedObjects_order"] = ["Card.aaa001"] * 16
    write_json(root_json, root)

    walk = ki.walk_population(root_dir, scenario_dir)
    assert walk.objects_reached == 1
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    assert len(objects) == 1
    # reached_via is a SET of distinct paths: one name sixteen times is one path.
    assert objects[0]["reached_via"] == ["Card.aaa001"]


def test_clause4_precedes_clause3_reached_twice_counts_containers(tmp_path):
    """objects_reached_twice counts objects reached through two CONTAINERS.

    That is a different fact about a different thing from a name listed twice in
    one order list, and it is the fact Midwinter's 9 and WotOG's 0 are pinned on.
    """
    shared = card("4c24f5", "71040", 918023,
                  {"9180": {"NumWidth": 6, "NumHeight": 5,
                            "FaceURL": "https://example/f6", "BackURL": "https://example/b"}})
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Factions.aaa", {"Name": "Bag", "GUID": "aaa"}, [("WilliamBain.4c24f5", shared, None)]),
        ("Rivals.bbb", {"Name": "Bag", "GUID": "bbb"}, [("WilliamBain.4c24f5", shared, None)]),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    assert walk.objects_reached == 2          # two distinct walk-relative paths

    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    assert len(objects) == 1                  # one object, merged on the composite key
    assert len(objects[0]["reached_via"]) == 2

    counts = ki.build_counts(walk, objects, {})
    assert counts["objects_reached"] == 2
    assert counts["objects"] == 1
    assert counts["objects_reached_twice"] == 1


# ---------------------------------------------------------------------------
# Clause 3 -- the composite key, and the GUID it must NOT key on
# ---------------------------------------------------------------------------

def test_clause3_one_guid_carrying_four_cards_admits_all_four(tmp_path):
    """`Challenge Scenario - By the Book`'s GUID ab3719 in miniature.

    All four have Korean donors. Under GUID keying the first is admitted and the
    other three fold into its reached_via[] -- three donors never looked up, three
    overrides never written, three cards left English inside a pack that
    advertises itself as Korean -- and NOTHING reports it.
    """
    entries = []
    for i, arkham_id in enumerate(("01141", "50044", "01179", "01172")):
        entries.append(("Card%d.ab3719" % i,
                        card("ab3719", arkham_id, 917500 + i, GRID_8x5), None))
    scenario_dir, root_dir = build_tree(tmp_path, entries)

    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    assert len(objects) == 4
    assert {o["arkham_id"] for o in objects} == {"01141", "50044", "01179", "01172"}

    counts = ki.build_counts(walk, objects, {})
    # Sum(len-1) -- the only reading that satisfies both By the Book's pinned 3
    # and Midwinter's pinned 0 (K22).
    assert counts["guid_collisions_distinct_id"] == 3
    assert counts["arkham_ids"] == 4


def test_guid_collisions_uses_sum_len_minus_one(tmp_path):
    """K22: the prose definition yields 4 and 2 where the document pins 3 and 1.

    A group of size 2 is the one size at which the two readings coincide, which is
    why the synthetic case alone could never have caught it -- so this asserts the
    three-member group, where they diverge.
    """
    walk = ki.WalkResult()
    for i, arkham_id in enumerate(("01141", "50044", "01179")):
        rel = "Card%d.ab3719" % i
        walk.reached[rel] = {
            "walkrel": rel, "kind": "Card", "parent_kind": "Deck",
            "json": "/nonexistent/%s.json" % rel,
            "obj": card("ab3719", arkham_id, 917500 + i, GRID_8x5)}
    objects = ki.build_objects(walk, "/nonexistent", WORKSPACE)
    counts = ki.build_counts(walk, objects, {})
    assert len(objects) == 3
    assert counts["guid_collisions_distinct_id"] == 2   # 3 - 1, not 3


def test_clause3_merges_only_when_all_three_agree(tmp_path):
    """A different CardID under the same GUID and id is a different entry."""
    walk = ki.WalkResult()
    for i, card_id in enumerate((917500, 917501)):
        rel = "Card%d.aaa001" % i
        walk.reached[rel] = {
            "walkrel": rel, "kind": "Card", "parent_kind": None,
            "json": "/nonexistent/%s.json" % rel,
            "obj": card("aaa001", "71001", card_id, GRID_8x5)}
    objects = ki.build_objects(walk, "/nonexistent", WORKSPACE)
    assert len(objects) == 2


# ---------------------------------------------------------------------------
# Clause 2 -- excluded kinds may not carry ids (exit 14)
# ---------------------------------------------------------------------------

def test_clause2_excluded_kind_carrying_an_id_is_exit_14(tmp_path):
    """The only clause not measurable against today's corpora: zero violations
    over all 16 target scenarios, so it exists entirely for the NEXT kind."""
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
        ("Weird.ddd001", {"Name": "Custom_Token", "GUID": "ddd001",
                          "GMNotes": json.dumps({"id": "71099"})}, None),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.scan_excluded_ids(walk, scenario_dir)
    assert excinfo.value.code == kc.EXIT_DRIFT
    assert "Custom_Token" in str(excinfo.value)
    assert "71099" in str(excinfo.value)


def test_clause2_passes_when_no_excluded_node_carries_an_id(tmp_path):
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
        ("Tile.bbb001", {"Name": "Custom_Tile", "GUID": "bbb001"}, None),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    assert ki.scan_excluded_ids(walk, scenario_dir) == 0


# ---------------------------------------------------------------------------
# Exit 62 -- the case-fold scan, and the half that was REMOVED
# ---------------------------------------------------------------------------

def test_case_fold_collision_is_exit_62():
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.scan_case_fold([{"object_id": "Deck.aaa/Card.ab3719"},
                           {"object_id": "Deck.aaa/card.ab3719"}])
    assert excinfo.value.code == kc.EXIT_COLLISION


def test_case_fold_scan_does_not_fire_on_a_duplicate_guid():
    """62's duplicate-GUID half is removed, on both grounds that scoped it.

    It was unreachable -- evaluated over an objects[] that GUID keying made
    GUID-unique by construction -- and it is now wrong in principle, because
    clause 3 ADMITS a repeated GUID carrying a different identity. Midwinter's
    two Bags sharing GUID af62ff are the measured reason.
    """
    assert ki.scan_case_fold([
        {"object_id": "Rivals.0cbe7e/RivalTheSilverTwilightLodge.af62ff"},
        {"object_id": "Rivals.0cbe7e/RivalTheSyndicate.af62ff"},
    ]) == 0


def test_case_fold_scan_ignores_one_object_reached_twice():
    """Run AFTER both de-duplications, so an object reached through two
    containers is never mistaken for two colliding objects."""
    assert ki.scan_case_fold([{"object_id": "Factions.a/WilliamBain.4c24f5"}]) == 0


# ---------------------------------------------------------------------------
# GMNotes -- BOTH storage forms (§5.1 step 3)
# ---------------------------------------------------------------------------

def test_gmnotes_inline_form_is_read(tmp_path):
    obj = {"GMNotes": '{\n  "id": "71006",\n  "type": "Act"\n}'}
    assert ki.read_gmnotes_id(obj, "x.json", str(tmp_path)) == "71006"


def test_gmnotes_sidecar_form_is_read(tmp_path):
    scenario_dir = str(tmp_path)
    rel = "Root.aaa/Guests.1e04d0/ArchibaldHudson.6cd7ec.gmnotes"
    write_json(os.path.join(scenario_dir, rel), {"id": "71013", "type": "Card"})
    obj = {"GMNotes_path": rel}
    assert ki.read_gmnotes_id(obj, "x.json", scenario_dir) == "71013"


def test_reading_only_the_sidecar_would_lose_the_inline_ids(tmp_path):
    """Reading only the sidecar takes Midwinter's id count from 63 to 29, and it
    fails QUIETLY -- as a smaller counts.arkham_ids, not as an error. 71006 is one
    of the ids it loses, and one of the two faces the v1.x assertion is pinned on."""
    inline_only = {"GMNotes": json.dumps({"id": "71006"})}
    assert inline_only.get("GMNotes_path") is None
    assert ki.read_gmnotes_id(inline_only, "x.json", str(tmp_path)) == "71006"


def test_gmnotes_two_forms_that_disagree_is_exit_13(tmp_path):
    scenario_dir = str(tmp_path)
    rel = "sidecar.gmnotes"
    write_json(os.path.join(scenario_dir, rel), {"id": "71013"})
    obj = {"GMNotes_path": rel, "GMNotes": json.dumps({"id": "71099"})}
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.read_gmnotes_id(obj, "x.json", scenario_dir)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_gmnotes_two_forms_that_agree_is_accepted(tmp_path):
    scenario_dir = str(tmp_path)
    rel = "sidecar.gmnotes"
    write_json(os.path.join(scenario_dir, rel), {"id": "71013"})
    obj = {"GMNotes_path": rel, "GMNotes": json.dumps({"id": "71013"})}
    assert ki.read_gmnotes_id(obj, "x.json", scenario_dir) == "71013"


def test_an_object_with_no_gmnotes_is_not_a_parse_failure(tmp_path):
    """§5.2 step 6's defer branch. Neither reference corpus exercises it -- both
    report objects_without_arkham_id 0 -- so it is covered synthetically."""
    assert ki.read_gmnotes_id({}, "x.json", str(tmp_path)) is None
    assert ki.read_gmnotes_id({"GMNotes": "just some prose"}, "x.json", str(tmp_path)) is None


def test_a_population_card_with_no_id_stays_in_the_population(tmp_path):
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
        ("Card.aaa002", card("aaa002", None, 917501, GRID_8x5), None),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    counts = ki.build_counts(walk, objects, {})
    assert counts["objects"] == 2
    assert counts["objects_without_arkham_id"] == 1
    assert counts["arkham_ids"] == 1


# ---------------------------------------------------------------------------
# Geometry -- CONDITIONAL on CustomDeck (§5.1 step 3, K7)
# ---------------------------------------------------------------------------

def test_a_custom_pdf_guide_with_no_customdeck_does_not_refuse():
    """Applied unconditionally the assertion is unsatisfiable for the guide, so
    init refuses at exit 13 on BOTH reference corpora -- at the first stage of the
    first phase, on the only data v0 is proven against."""
    result = ki.check_geometry({"Name": "Custom_PDF", "GUID": "ed8f81"}, "guide.json")
    assert result["cell"] is None and result["deck_key"] is None
    assert result["num_width"] is None


def test_half_a_pair_is_still_exit_13():
    """The pair is what the assertion is about; half of it is a malformed card
    rather than a PDF."""
    for obj in ({"CardID": 917500}, {"CustomDeck": GRID_8x5}):
        with pytest.raises(kc.KzRefusal) as excinfo:
            ki.check_geometry(obj, "half.json")
        assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_geometry_derives_row_col_from_the_real_grid():
    """918020 on the 6x5 sheet is row 3, col 2 -- §3.3's own worked example."""
    grid = {"9180": {"NumWidth": 6, "NumHeight": 5, "FaceURL": "f", "BackURL": "b"}}
    result = ki.check_geometry({"CardID": 918020, "CustomDeck": grid}, "ok.json")
    assert (result["deck_key"], result["cell"]) == ("9180", 20)
    assert (result["row"], result["col"]) == (3, 2)
    assert result["cell"] == result["row"] * result["num_width"] + result["col"]


def test_a_cell_outside_the_declared_grid_is_exit_13():
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.check_geometry({"CardID": 917599, "CustomDeck": GRID_8x5}, "overflow.json")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_a_customdeck_without_the_cardids_deck_key_is_exit_13():
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.check_geometry({"CardID": 999900, "CustomDeck": GRID_8x5}, "mismatch.json")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


# ---------------------------------------------------------------------------
# parent_kind is REPORTED and decides nothing (§5.2 step 5)
# ---------------------------------------------------------------------------

def test_parent_kind_is_recorded_but_never_filters(tmp_path):
    """The unconditional `parent_kind == "Deck"` -> defer predicate is DELETED.

    Applied over the eight Challenge Scenarios it takes counts.arkham_ids_resolved
    from 222 to 61. It does not shave v0's value, it removes it.
    """
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("ActDeck.ddd", {"Name": "Deck", "GUID": "ddd"},
         [("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None)]),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    assert len(objects) == 1
    assert objects[0]["parent_kind"] == "Deck"
    counts = ki.build_counts(walk, objects, {})
    assert counts["objects_inside_deck"] == 1
    assert counts["arkham_ids"] == 1          # reported, not deferred


# ---------------------------------------------------------------------------
# Guide ids, packs, and the counters
# ---------------------------------------------------------------------------

def test_guide_ids_are_counted_separately_from_arkham_ids(tmp_path):
    """§3.4's counts.arkham_ids excludes CG* -- that is the K1 correction."""
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
        ("Guide.ed8f81", card("ed8f81", "CG71", None, None, kind="Custom_PDF"), None),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    counts = ki.build_counts(walk, objects, {})
    assert counts["objects"] == 2
    assert counts["arkham_ids"] == 1
    assert counts["guide_ids"] == 1


def test_a_guide_id_is_never_assigned_a_pack():
    """CG71 does have a Korean - Campaigns override, so looking it up would file
    the guide as a Campaigns object and move §3.3's pinned 47."""
    objects = [{"arkham_id": "CG71"}, {"arkham_id": "71001"}]
    index = {"CG71": {"Korean - Campaigns"}, "71001": {"Korean - Campaigns"}}
    by_pack = ki.assign_packs(objects, index)
    assert objects[0]["pack"] is None
    assert objects[1]["pack"] == "Korean - Campaigns"
    assert by_pack == {ki.UNASSIGNED: 1, "Korean - Campaigns": 1}


def test_player_cards_wins_when_an_id_is_in_both_packs():
    """16 Midwinter ids are in both packs; Player Cards is what makes the pinned
    objects_by_pack 47 / 16 / 1 come out."""
    objects = [{"arkham_id": "71040"}]
    index = {"71040": {"Korean - Campaigns", "Korean - Player Cards"}}
    ki.assign_packs(objects, index)
    assert objects[0]["pack"] == "Korean - Player Cards"


def test_ids_carried_by_two_objects_counts_ids_not_objects(tmp_path):
    """The recorded defect: 63 Card overrides carry 62 ids (71033 at 4a2568 and
    ccce29), and every arkham_id-driven loop silently drops one."""
    walk = ki.WalkResult()
    for guid in ("4a2568", "ccce29"):
        rel = "Factions.a/SilverTwilightLodge.%s" % guid
        walk.reached[rel] = {"walkrel": rel, "kind": "Card", "parent_kind": "Bag",
                             "json": "/nonexistent/%s.json" % rel,
                             "obj": card(guid, "71033", 918020,
                                         {"9180": {"NumWidth": 6, "NumHeight": 5,
                                                   "FaceURL": "f", "BackURL": "b"}})}
    objects = ki.build_objects(walk, "/nonexistent", WORKSPACE)
    counts = ki.build_counts(walk, objects, {})
    assert counts["objects"] == 2
    assert counts["arkham_ids"] == 1
    assert counts["ids_carried_by_two_objects"] == 1
    assert counts["guid_collisions_distinct_id"] == 0


def test_excluded_by_kind_carrying_id_is_zero_in_every_report_that_exists(tmp_path):
    """Any other value is exit 14, so the counter can only ever be 0 on disk."""
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
        ("Tile.bbb001", {"Name": "Custom_Tile", "GUID": "bbb001"}, None),
    ])
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    counts = ki.build_counts(walk, objects, {},
                             excluded_with_id=ki.scan_excluded_ids(walk, scenario_dir))
    assert counts["objects_excluded_by_kind_carrying_id"] == 0


# ---------------------------------------------------------------------------
# The walk reads the MANIFEST, not the directory
# ---------------------------------------------------------------------------

def test_an_unregistered_file_is_not_reached(tmp_path):
    """"ContainedObjects_order is the manifest, not the directory -- 11
    unregistered files were silently dropped with rc 0." """
    scenario_dir, root_dir = build_tree(tmp_path, [
        ("Card.aaa001", card("aaa001", "71001", 917500, GRID_8x5), None),
    ])
    # A file on disk that no order list names.
    write_json(os.path.join(root_dir, "Card.zzz999.json"),
               card("zzz999", "71099", 917501, GRID_8x5))
    walk = ki.walk_population(root_dir, scenario_dir)
    assert walk.objects_reached == 1


# ---------------------------------------------------------------------------
# Fonts, slug, prefixes
# ---------------------------------------------------------------------------

def test_a_font_role_naming_the_wrong_face_is_exit_13(tmp_path):
    """The mismatch §5.1 step 8 refuses on, naming the ROLE."""
    fake = tmp_path / "NotTheBodyFace.ttf"
    fake.write_bytes(b"\x00\x01\x00\x00" + b"\x00" * 64)
    env = {"KOREANIZE_FONT_BODY": str(fake)}
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.resolve_fonts(["images-ko/fonts/"], env, WORKSPACE)
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "body" in str(excinfo.value)


def test_a_font_role_naming_a_missing_file_is_exit_13():
    env = {"KOREANIZE_FONT_BODY": "/nonexistent/NoSuchFont.otf"}
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.resolve_fonts([], env, WORKSPACE)
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_an_absent_font_role_is_recorded_rather_than_refused():
    """v0 typesets nothing, and this workspace carries only the body face.
    Refusing an absent title face at stage 0 would make every v0 run unreachable."""
    fonts, counts, unresolved = ki.resolve_fonts([], {}, WORKSPACE)
    assert set(fonts) >= {"title", "body", "icons", "search_roots"}
    assert counts["fonts_resolved"] + counts["fonts_unresolved"] == 3
    for role in unresolved:
        assert fonts[role]["sha256"] is None


def test_derive_slug_matches_the_designs_own_example():
    assert ki.derive_slug("War of the Outer Gods") == "war-of-the-outer-gods"
    assert ki.derive_slug("Challenge Scenario - By the Book") == \
        "challenge-scenario-by-the-book"


def test_arkham_prefixes_exclude_guide_ids():
    objects = [{"arkham_id": "71001"}, {"arkham_id": "71040"},
               {"arkham_id": "CG71"}, {"arkham_id": None}]
    assert ki.derive_arkham_prefixes(objects) == ["71"]


# ---------------------------------------------------------------------------
# Scenario resolution -- exit 2 on 0 or >1 (§5.1 step 1)
# ---------------------------------------------------------------------------

def test_an_unmatched_scenario_name_is_exit_2(scenario_root):
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.resolve_scenario("no such scenario anywhere", WORKSPACE)
    assert excinfo.value.code == kc.EXIT_USAGE


def test_an_ambiguous_scenario_name_is_exit_2(scenario_root):
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.resolve_scenario("Challenge Scenario", WORKSPACE)
    assert excinfo.value.code == kc.EXIT_USAGE


def test_an_exact_name_beats_a_substring_match(scenario_root):
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    scenario_dir, root_dir, root_json = ki.resolve_scenario("The Midwinter Gala", WORKSPACE)
    assert os.path.basename(scenario_dir) == "The Midwinter Gala"
    assert os.path.isdir(root_dir) and os.path.exists(root_json)


# ---------------------------------------------------------------------------
# init is a NEUTRAL stage
# ---------------------------------------------------------------------------

def test_init_declares_no_ai_contract():
    """§4.1 puts init in ai.neutral_stages. _check_contracts only knows the
    required/forbidden dichotomy, so a neutral stage declaring either half would
    refuse against its own generated config."""
    assert "init" not in kc.AI_CONTRACT


def test_the_generated_config_declares_init_neutral():
    counts = {"arkham_ids": 12}
    fonts, _c, _u = ki.resolve_fonts([], {}, WORKSPACE)
    cfg = ki.build_scenario_config(
        slug="s", scenario_name="S", source_dir="d", source_tree_sha256="x",
        pack="Korean - Campaigns", container_guid="g", container_stem="S.aaaaaa",
        arkham_prefixes=["71"], atlases=[], shared_backs=[], fonts=fonts,
        run_dir=".am/koreanize/s", counts=counts)
    assert "init" in cfg["ai"]["neutral_stages"]
    assert "init" not in cfg["ai"]["required_stages"]
    assert "init" not in cfg["ai"]["forbidden_stages"]


def test_the_generated_config_validates_and_is_pinned():
    """kz_config recomputes config_sha256 and refuses at exit 4 on mismatch, so a
    constructor that forgot to re-pin cannot ship."""
    import kz_config as kz
    counts = {"arkham_ids": 12}
    fonts, _c, _u = ki.resolve_fonts([], {}, WORKSPACE)
    cfg = ki.build_scenario_config(
        slug="s", scenario_name="S", source_dir="d", source_tree_sha256="x",
        pack="Korean - Campaigns", container_guid="g", container_stem="S.aaaaaa",
        arkham_prefixes=["71"], atlases=[], shared_backs=[], fonts=fonts,
        run_dir=".am/koreanize/s", counts=counts)
    assert kz.validate(cfg) is cfg
    assert cfg["config_sha256"] == kz.compute_config_sha256(cfg)

    cfg["slug"] = "tampered"
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg)
    assert excinfo.value.code == kc.EXIT_GUARD


def test_universe_size_over_the_ceiling_refuses_at_exit_4():
    """§3.2: kz_config refuses unless universe_size <= max_units_per_call *
    max_calls. S2's ceiling is 16 * 6 = 96."""
    import kz_config as kz
    fonts, _c, _u = ki.resolve_fonts([], {}, WORKSPACE)
    cfg = ki.build_scenario_config(
        slug="s", scenario_name="S", source_dir="d", source_tree_sha256="x",
        pack="Korean - Campaigns", container_guid="g", container_stem="S.aaaaaa",
        arkham_prefixes=["71"], atlases=[], shared_backs=[], fonts=fonts,
        run_dir=".am/koreanize/s", counts={"arkham_ids": 97})
    with pytest.raises(kc.KzRefusal) as excinfo:
        kz.validate(cfg, check_pin=False)
    assert excinfo.value.code == kc.EXIT_GUARD
    assert "universe_size" in str(excinfo.value)


# ---------------------------------------------------------------------------
# --selftest, as a subprocess -- the CLI contract §5.9 makes the step predicate
# ---------------------------------------------------------------------------

def test_module_selftest_exits_zero():
    result = subprocess.run(
        [sys.executable, os.path.join(kc.PACKAGE_DIR, "kz_init.py"), "--selftest"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Corpus smoke rows -- §1.3 and §3.3's pinned figures
# ---------------------------------------------------------------------------

CORPUS_ROWS = [
    # (scenario, objects_reached, objects, twice, arkham_ids, guide_ids,
    #  guid_collisions_distinct_id, excluded_sum)
    ("The Midwinter Gala", 73, 64, 9, 62, 1, 0, 23),
    ("War of the Outer Gods", 62, 62, 0, 58, 1, 0, 11),
    ("Challenge Scenario - By the Book", 37, 37, 0, 36, 1, 3, 8),
]


@pytest.mark.parametrize(
    "name,reached,objects_n,twice,arkham_ids,guide_ids,collisions,excluded",
    CORPUS_ROWS)
def test_corpus_pinned_counts(scenario_root, name, reached, objects_n, twice,
                              arkham_ids, guide_ids, collisions, excluded):
    """The rows §1.3 and §3.3 pin. A SMOKE run: it needs the live SCED-downloads
    tree, so it skips when that is absent."""
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")

    scenario_dir, root_dir, _root_json = ki.resolve_scenario(name, WORKSPACE)
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    counts = ki.build_counts(walk, objects, {},
                             excluded_with_id=ki.scan_excluded_ids(walk, scenario_dir),
                             case_fold_collisions=ki.scan_case_fold(objects))

    assert counts["objects_reached"] == reached
    assert counts["objects"] == objects_n
    assert counts["objects_reached_twice"] == twice
    assert counts["arkham_ids"] == arkham_ids
    assert counts["guide_ids"] == guide_ids
    assert counts["guid_collisions_distinct_id"] == collisions
    assert sum(counts["objects_excluded_by_kind"].values()) == excluded
    assert counts["objects_excluded_by_kind_carrying_id"] == 0
    assert counts["objects_case_folded_collisions"] == 0


def test_midwinter_pack_split_and_deck_counters(scenario_root):
    """§3.3's objects_by_pack {Campaigns: 47, Player Cards: 16, unassigned: 1},
    its objects_inside_deck 40 and its ids_carried_by_two_objects 1."""
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    langpack = os.path.join(WORKSPACE, "SCED-downloads", "decomposed", "language-pack")
    if not os.path.isdir(langpack):
        pytest.skip("SCED-downloads language-pack tree is not checked out")

    scenario_dir, root_dir, _root_json = ki.resolve_scenario("The Midwinter Gala", WORKSPACE)
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    by_pack = ki.assign_packs(objects, ki.build_korean_pack_index(WORKSPACE))
    counts = ki.build_counts(walk, objects, by_pack)

    assert counts["objects_by_pack"] == {"Korean - Campaigns": 47,
                                         "Korean - Player Cards": 16,
                                         "unassigned": 1}
    assert counts["objects_inside_deck"] == 40
    assert counts["ids_carried_by_two_objects"] == 1
    assert counts["objects_without_arkham_id"] == 0


def test_midwinter_excluded_kinds_break_down_as_pinned(scenario_root):
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    scenario_dir, root_dir, _root_json = ki.resolve_scenario("The Midwinter Gala", WORKSPACE)
    walk = ki.walk_population(root_dir, scenario_dir)
    assert dict(walk.excluded_by_kind) == {"Deck": 8, "Bag": 12, "Custom_Model_Bag": 1,
                                           "Notecard": 1, "Custom_Tile": 1}


def test_midwinter_reached_twice_objects_carry_two_paths(scenario_root):
    """The nine are objects reached through two containers each -- and the walk
    reconciliation 73 -> 64 is exactly those nine."""
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    scenario_dir, root_dir, _root_json = ki.resolve_scenario("The Midwinter Gala", WORKSPACE)
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    twice = [o for o in objects if len(o["reached_via"]) > 1]
    assert len(twice) == 9
    for entry in twice:
        assert len(set(entry["reached_via"])) == len(entry["reached_via"])


def test_by_the_book_guid_ab3719_carries_four_distinct_cards(scenario_root):
    """The measured case that deletes GUID keying: 37 objects over 34 GUIDs."""
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    scenario_dir, root_dir, _root_json = ki.resolve_scenario(
        "Challenge Scenario - By the Book", WORKSPACE)
    walk = ki.walk_population(root_dir, scenario_dir)
    objects = ki.build_objects(walk, scenario_dir, WORKSPACE)
    shared = [o for o in objects if o["guid"] == "ab3719"]
    assert len(shared) == 4
    assert {o["arkham_id"] for o in shared} == {"01141", "50044", "01179", "01172"}
    assert len({o["guid"] for o in objects}) == 34


# ---------------------------------------------------------------------------
# Untrusted-input containment (verify round 1)
#
# Every path fragment and URL below is authored by whoever published the mod.
# The operator trusts that tree enough to load it into Tabletop Simulator, which
# is a statement about its game content and not about its filesystem behaviour.
# ---------------------------------------------------------------------------

def test_an_absolute_gmnotes_path_cannot_escape_the_scenario_dir(tmp_path):
    """os.path.join DISCARDS the root entirely when the tail is absolute, so the
    join alone is not containment."""
    outside = tmp_path / "outside.gmnotes"
    write_json(str(outside), {"id": "99999"})
    scenario_dir = str(tmp_path / "scenario")
    os.makedirs(scenario_dir, exist_ok=True)
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.read_gmnotes_id({"GMNotes_path": str(outside)}, "x.json", scenario_dir)
    assert excinfo.value.code == kc.EXIT_DRIFT


def test_a_traversing_gmnotes_path_cannot_escape_the_scenario_dir(tmp_path):
    outside = tmp_path / "secret.gmnotes"
    write_json(str(outside), {"id": "99999"})
    scenario_dir = str(tmp_path / "scenario")
    os.makedirs(scenario_dir, exist_ok=True)
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.read_gmnotes_id({"GMNotes_path": "../secret.gmnotes"}, "x.json", scenario_dir)
    assert excinfo.value.code == kc.EXIT_DRIFT


def test_a_legitimate_gmnotes_path_still_resolves(tmp_path):
    scenario_dir = str(tmp_path / "scenario")
    rel = "Root.aaa/Card.bbb.gmnotes"
    write_json(os.path.join(scenario_dir, rel), {"id": "71013"})
    assert ki.read_gmnotes_id({"GMNotes_path": rel}, "x.json", scenario_dir) == "71013"


def test_an_unreadable_gmnotes_sidecar_is_not_a_crash(tmp_path):
    """Only ValueError used to be caught, so a directory at the sidecar path
    raised IsADirectoryError straight out of the walk."""
    scenario_dir = str(tmp_path / "scenario")
    os.makedirs(os.path.join(scenario_dir, "Root.aaa", "Card.bbb.gmnotes"),
                exist_ok=True)
    obj = {"GMNotes_path": "Root.aaa/Card.bbb.gmnotes"}
    # A directory is not a file, so it is simply not a sidecar -- no id, no crash.
    assert ki.read_gmnotes_id(obj, "x.json", scenario_dir) is None


@pytest.mark.parametrize("name", ["..", ".", "a/b", "a\\b", "", "x\x00y"])
def test_a_non_segment_order_entry_is_refused(tmp_path, name):
    """An entry of `..` walks the descent out of the tree; one containing a
    separator addresses a file the manifest does not name."""
    assert not ki.is_safe_segment(name)
    scenario_dir = str(tmp_path / "A Scenario")
    root_dir = os.path.join(scenario_dir, "Scenario.aaaaaa")
    write_json(root_dir + ".json",
               {"Name": "Bag", "GUID": "root00", "ContainedObjects_order": [name]})
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.walk_population(root_dir, scenario_dir)
    assert excinfo.value.code == kc.EXIT_DRIFT


def test_a_legitimate_order_entry_is_a_safe_segment():
    for name in ("Card.aaa001", "TheMidwinterGala-ScenarioGuide.ed8f81",
                 "ArsèneRenard.2d493a"):
        assert ki.is_safe_segment(name)


def test_a_symlink_cycle_in_the_container_tree_is_refused(tmp_path):
    """Without a cycle guard this recurses to RecursionError -- a crash rather
    than a diagnosis, on input the tool does not control."""
    scenario_dir = str(tmp_path / "A Scenario")
    root_dir = os.path.join(scenario_dir, "Scenario.aaaaaa")
    os.makedirs(root_dir, exist_ok=True)
    write_json(root_dir + ".json",
               {"Name": "Bag", "GUID": "root00",
                "ContainedObjects_order": ["Loop.bbb"]})
    write_json(os.path.join(root_dir, "Loop.bbb.json"),
               {"Name": "Bag", "GUID": "bbb",
                "ContainedObjects_order": ["Loop.bbb"]})
    try:
        os.symlink(root_dir, os.path.join(root_dir, "Loop.bbb"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.walk_population(root_dir, scenario_dir)
    assert excinfo.value.code == kc.EXIT_DRIFT


@pytest.mark.parametrize("url,ok", [
    ("https://steamusercontent-a.akamaihd.net/ugc/123/ABC/", True),
    ("http://example.com/atlas.png", True),
    ("file:///Users/someone/.ssh/id_rsa", False),
    ("ftp://example.com/atlas.png", False),
    ("http://169.254.169.254/latest/meta-data/", False),
    ("http://127.0.0.1:8080/atlas.png", False),
    ("http://10.0.0.5/atlas.png", False),
    ("http://[::1]/atlas.png", False),
    ("http://localhost:8080/atlas.png", False),
    ("http://LocalHost./atlas.png", False),
    ("http://api.localhost/atlas.png", False),
    ("", False),
    ("https://", False),
])
def test_only_routable_http_atlas_targets_are_fetched(url, ok):
    """urllib supports file:// and ftp:// out of the box, so an unvalidated
    urlopen on an attacker-authored FaceURL reads local files -- and the result
    would be hashed, stored, and named in a config that gets mirrored into git."""
    assert (ki._check_fetch_target(url) is None) is ok


def test_a_hostile_atlas_url_is_reported_not_fatal(tmp_path):
    """`init` reports on an unmeasurable atlas rather than failing, so a hostile
    URL must not be able to halt the walk."""
    faces = collections.OrderedDict([
        ("file:///etc/passwd", {"url": "file:///etc/passwd", "side": "face",
                                "grid": {"num_width": 8, "num_height": 5},
                                "cells": {0}, "packs": set()}),
    ])
    inventory, counts = ki.measure_atlases(faces, str(tmp_path), download=True)
    assert counts["atlases_fetched"] == 0
    assert inventory[0]["pixels"] is None
    assert inventory[0]["atlas_id"]          # still stably named


def test_a_negative_card_id_is_refused():
    """Floor division makes -1 // 100 == -1 and -1 % 100 == 99, so an unchecked
    negative CardID yields a plausible deck_key and cell and passes every
    downstream assertion."""
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.check_geometry({"CardID": -50, "CustomDeck": GRID_8x5}, "negative.json")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_a_boolean_card_id_is_refused():
    """bool is an int subclass, so True would otherwise be CardID 1."""
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.check_geometry({"CardID": True, "CustomDeck": GRID_8x5}, "bool.json")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


# ---------------------------------------------------------------------------
# emit() ordering and --verify-only (verify round 1)
# ---------------------------------------------------------------------------

def test_the_git_tracked_mirror_is_written_last():
    """Each write is atomic but the SET is not transactional, and the only
    partial state that outlives <run_dir> is the mirror. Writing it last means a
    mirror on disk implies a complete, reported run."""
    source = open(os.path.join(kc.PACKAGE_DIR, "kz_init.py"), encoding="utf-8").read()
    body = source.split("\ndef emit(", 1)[1].split("\ndef ", 1)[0]
    assert body.index("write_report") < body.index("write_data")


def test_verify_only_refuses_when_there_is_nothing_to_verify(tmp_path):
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.verify_only(str(tmp_path))
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_verify_only_round_trips_a_real_run(scenario_root, tmp_path):
    """§4.1's uniform option: re-assert an existing output, write
    init.verify.json, never the marker."""
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    run_dir = str(tmp_path / "run")
    report, artifacts = ki.run_init(scenario="The Midwinter Gala",
                                    pack="Korean - Campaigns", run_dir=run_dir,
                                    download_atlases=False)
    ki.emit(report, artifacts, run_dir, mirror=False)

    verify, path = ki.verify_only(run_dir)
    assert verify["mode"] == "verify-only"
    assert verify["exit_code"] == kc.EXIT_OK, [c for c in verify["checks"]
                                               if c["status"] != "pass"]
    assert path.endswith("init.verify.json")
    assert os.path.exists(path)
    # Never the marker: the build report is untouched.
    assert os.path.exists(os.path.join(run_dir, "init.json"))


def test_verify_only_detects_a_tampered_index(scenario_root, tmp_path):
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    run_dir = str(tmp_path / "run")
    report, artifacts = ki.run_init(scenario="The Midwinter Gala",
                                    pack="Korean - Campaigns", run_dir=run_dir,
                                    download_atlases=False)
    ki.emit(report, artifacts, run_dir, mirror=False)

    index_path = os.path.join(run_dir, "card-text-en.json")
    index = json.load(open(index_path, encoding="utf-8"))
    index["counts"]["objects"] = 999
    write_json(index_path, index)

    verify, _path = ki.verify_only(run_dir)
    assert verify["exit_code"] != kc.EXIT_OK
    failed = {c["name"] for c in verify["checks"] if c["status"] != "pass"}
    assert "count:objects" in failed


# ---------------------------------------------------------------------------
# The pack-index binding (verify round 1)
# ---------------------------------------------------------------------------

def test_the_pack_index_cache_returns_the_same_answer(scenario_root):
    """A stale or corrupt cache entry must cost a rebuild, never a wrong answer."""
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    langpack = os.path.join(WORKSPACE, "SCED-downloads", "decomposed", "language-pack")
    if not os.path.isdir(langpack):
        pytest.skip("SCED-downloads language-pack tree is not checked out")

    uncached = ki.build_korean_pack_index(WORKSPACE, use_cache=False)
    cached = ki.build_korean_pack_index(WORKSPACE, use_cache=True)
    again = ki.build_korean_pack_index(WORKSPACE, use_cache=True)
    assert uncached == cached == again


def test_a_corrupt_pack_index_cache_falls_back_to_a_rebuild(scenario_root):
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    langpack = os.path.join(WORKSPACE, "SCED-downloads", "decomposed", "language-pack")
    if not os.path.isdir(langpack):
        pytest.skip("SCED-downloads language-pack tree is not checked out")

    digest = ki._pack_tree_digest(WORKSPACE)
    path = ki.pack_index_cache_path(WORKSPACE, digest)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")
    try:
        assert ki.build_korean_pack_index(WORKSPACE, use_cache=True)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_the_fetch_leaves_no_temp_file_behind_on_failure(tmp_path):
    """The rejected-redirect and over-cap returns used to leak a temp, and a
    caller that reconstructs the name to delete it is a coupling waiting to rot."""
    assets = str(tmp_path / "assets")
    assert ki._download_to_temp("http://127.0.0.1:9/nope", assets, 1) is None
    leftovers = [f for f in os.listdir(assets)] if os.path.isdir(assets) else []
    assert leftovers == [], leftovers


@pytest.mark.parametrize("broken", ["scenario.json", "card-text-en.json"])
def test_verify_only_reports_malformed_json_rather_than_crashing(scenario_root,
                                                                 tmp_path, broken):
    """--verify-only's whole promise is that a broken input is a FINDING, not a
    crash. The tampered-index case covers still-valid JSON with a wrong value;
    this covers syntactically invalid JSON, which used to raise a bare
    JSONDecodeError out of the one unguarded read_json() in the module."""
    if scenario_root is None:
        pytest.skip("SCED-downloads scenario tree is not checked out")
    run_dir = str(tmp_path / "run")
    report, artifacts = ki.run_init(scenario="The Midwinter Gala",
                                    pack="Korean - Campaigns", run_dir=run_dir,
                                    download_atlases=False)
    ki.emit(report, artifacts, run_dir, mirror=False)

    with open(os.path.join(run_dir, broken), "w", encoding="utf-8") as handle:
        handle.write("{ this is not json")

    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.verify_only(run_dir)
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "not valid JSON" in str(excinfo.value)


# ---------------------------------------------------------------------------
# P0a / P0b are a per-MODULE invariant, not a per-entry-point one (§5.9)
# ---------------------------------------------------------------------------

def test_verify_only_evaluates_the_invocation_guards(tmp_path, monkeypatch):
    """`verify_only` WRITES (init.verify.json), so it must evaluate P0a/P0b like
    every other entry point. It reached its write with neither evaluated."""
    scratch = tmp_path / ".local-sync" / "scratch" / "SCED" / "run"
    os.makedirs(str(scratch), exist_ok=True)
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.verify_only(str(scratch))
    # Exit 2, P0b -- and it fires BEFORE the "nothing to verify" precondition,
    # which is the proof the guard runs first rather than after the reads.
    assert excinfo.value.code == kc.EXIT_USAGE
    assert "scratch" in str(excinfo.value)


def test_run_init_refuses_an_explicit_run_dir_inside_the_scratch_tree(tmp_path):
    """P0b's subject is "cwd or any declared write root", and this stage's write
    root is <run_dir>. An explicit --run-dir into the nightly's scratch tree must
    refuse BEFORE anything is read -- the driver deletes that tree on completion
    and the writes would vanish with no diagnostic."""
    scratch = tmp_path / ".local-sync" / "scratch" / "SCED-downloads" / "run"
    os.makedirs(str(scratch), exist_ok=True)
    with pytest.raises(kc.KzRefusal) as excinfo:
        ki.run_init(scenario="The Midwinter Gala", pack="Korean - Campaigns",
                    run_dir=str(scratch), download_atlases=False)
    assert excinfo.value.code == kc.EXIT_USAGE


def test_both_entry_points_evaluate_p0b_over_the_run_dir():
    """The two entry points must agree on P0b's path set; they evaluated it over
    different ones while one of them passed no paths at all."""
    import ast
    source = open(os.path.join(kc.PACKAGE_DIR, "kz_init.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    guarded = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in ("run_init", "verify_only"):
            continue
        guarded[node.name] = [
            call.func.attr for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr in ("check_invocation_guards", "check_p0b")]
    assert "check_invocation_guards" in guarded["run_init"]
    assert "check_p0b" in guarded["run_init"]          # the derived run_dir
    assert "check_invocation_guards" in guarded["verify_only"]


# ---------------------------------------------------------------------------
# The pack-index cache has an eviction policy (verify round 3)
# ---------------------------------------------------------------------------

def test_the_pack_index_cache_evicts_old_digests(tmp_path):
    """Every langpack edit moves the digest and mints a new filename, so without
    eviction this directory grows once per pack change, forever."""
    cache_dir = str(tmp_path / "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    made = []
    for i in range(6):
        path = os.path.join(cache_dir, "korean-pack-index.%016d.json" % i)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        os.utime(path, (1000 + i, 1000 + i))
        made.append(path)
    ki._evict_pack_index_cache(cache_dir, keep=3)
    left = sorted(os.listdir(cache_dir))
    assert len(left) == 3
    # The NEWEST three survive.
    assert left == sorted(os.path.basename(p) for p in made[-3:])


def test_eviction_is_a_no_op_below_the_threshold(tmp_path):
    cache_dir = str(tmp_path / "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    for i in range(2):
        with open(os.path.join(cache_dir, "korean-pack-index.%d.json" % i),
                  "w", encoding="utf-8") as handle:
            handle.write("{}")
    ki._evict_pack_index_cache(cache_dir, keep=3)
    assert len(os.listdir(cache_dir)) == 2


def test_eviction_ignores_files_it_does_not_own(tmp_path):
    cache_dir = str(tmp_path / "_cache")
    os.makedirs(cache_dir, exist_ok=True)
    for i in range(5):
        with open(os.path.join(cache_dir, "korean-pack-index.%d.json" % i),
                  "w", encoding="utf-8") as handle:
            handle.write("{}")
    keep_me = os.path.join(cache_dir, "something-else.json")
    with open(keep_me, "w", encoding="utf-8") as handle:
        handle.write("{}")
    ki._evict_pack_index_cache(cache_dir, keep=2)
    assert os.path.exists(keep_me)


def test_the_fetch_returns_its_digest_rather_than_forcing_a_rehash():
    """The destination filename already encodes the digest, so re-deriving it with
    sha256_file re-read the whole 49-79 MB file for nothing."""
    import ast
    source = open(os.path.join(kc.PACKAGE_DIR, "kz_init.py"), encoding="utf-8").read()
    body = source.split("\ndef measure_atlases(", 1)[1].split("\ndef ", 1)[0]
    assert "sha256_file(local)" not in body
    fetch = source.split("\ndef _fetch_atlas(", 1)[1].split("\ndef ", 1)[0]
    assert "return dest, digest" in fetch
