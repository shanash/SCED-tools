#!/usr/bin/python3
"""arknights: the gates. Two tiers, and the second one skips LOUDLY.

RUN IT WITH THE PLATFORM INTERPRETER, either way:

    /usr/bin/python3 SCED-tools/scripts/arknights/tests/test_arknights_gates.py
    arknights.sh selftest                       # which is what runs it

pytest is installed on this machine's Homebrew 3.14 only, and this package is
pinned to Apple's /usr/bin/python3 -- so the file carries its OWN runner and does
not depend on pytest being importable. It stays pytest-compatible (plain
`test_*` functions, `skip` via an exception) for anyone who has it, but nothing
here requires it.

TIER 1, SYNTHETIC, always runs. A three-file miniature source tree in a tempdir,
pinning the three shapes that break a naive converter -- a Deck holding two
investigators, a card printed twice, and a minicard with no GMNotes -- plus the
package-wide grep gates that no single module's --selftest owns.

TIER 2, CORPUS, needs docs/Arknights/ and the sibling checkouts. It SKIPS LOUDLY
with a printed line rather than passing quietly: a suite that reports green on
checks it did not perform is worse than one that reports nothing, because it
reads as coverage.
"""

import ast
import collections
import json
import os
import re
import sys
import tempfile

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

import akn_common as kc  # noqa: E402
import akn_config as kz  # noqa: E402
import akn_scan as scan  # noqa: E402
import akn_renumber as renumber  # noqa: E402
import akn_repair as repair  # noqa: E402
import akn_assemble as assemble  # noqa: E402
import akn_verify as verify  # noqa: E402
import akn_publish as publish  # noqa: E402

WORKSPACE = kc.WORKSPACE_ROOT
DISPATCHER = os.path.join(PACKAGE_DIR, "arknights.sh")


class Skip(Exception):
    """Raised by a tier-2 test whose corpus is absent. Printed, never swallowed."""


def _skip(reason):
    raise Skip(reason)


def _cfg():
    return kz.load_config()


def _local(cfg, **overrides):
    local = json.loads(json.dumps({k: v for k, v in cfg.items()
                                   if not k.startswith("_")}))
    local["_path"] = cfg["_path"]
    local.update(overrides)
    return local


# ---------------------------------------------------------------------------
# Tier 1a -- the package-wide grep gates
# ---------------------------------------------------------------------------

MUTATING_VERBS = ("add", "commit", "checkout", "clean", "reset", "fetch", "push",
                  "stash", "rebase", "merge", "cherry-pick")


def test_no_mutating_git_verb_anywhere():
    """Every git invocation goes through the GIT_READONLY wrapper, and no module
    names a mutating verb except as text it prints or as prose."""
    findings = kc.scan_git_calls()
    assert not findings, findings
    # The allowlist itself must stay read-only. A verb added to it is the one
    # edit that would make the wrapper agree with a mutating call.
    for verb in MUTATING_VERBS:
        assert verb not in kc.GIT_READONLY, \
            "GIT_READONLY has grown the mutating verb %r" % verb
    assert kc.GIT_READONLY == frozenset(["rev-parse", "diff", "status", "ls-files"])


def test_no_write_mode_open_in_the_package():
    """Layer 3 of the read-only guarantee (design section 5.1)."""
    findings = kc.scan_open_modes()
    assert not findings, findings


def test_package_imports_are_stdlib_or_allowlisted():
    """Stdlib, plus the three names in PACKAGE_IMPORTS -- akn_common, akn_config
    and sced_io, the workspace's shared atomic writer. Named here because the
    old name said `stdlib_only`, which the sced_io import has made untrue."""
    findings = kc.scan_imports()
    assert not findings, findings


def test_package_compiles_on_the_platform_interpreter():
    findings = kc.compile_package()
    assert not findings, findings


def test_every_module_is_built_and_selftests():
    """A module missing from the dispatcher's selftest loop has a --selftest
    nothing ever runs -- which is how a check comes to exist and never fire."""
    assert kc.modules_pending() == [], kc.modules_pending()
    text = kc.read_text(DISPATCHER)
    for name in kc.PACKAGE_MODULES:
        stem = name[:-3]
        assert stem in text, "%s is not in arknights.sh's selftest loop" % stem


def test_exit_band_is_disjoint_from_every_other_table():
    """{80..89} against the union of the workspace's documented tables."""
    ours = set(kc.EXIT_MEANING) - set([0, 1, 2])
    assert ours == set(range(80, 90)), sorted(ours)
    # koreanize's stage codes and dispatcher band, the driver's table,
    # sced-run-now.sh's, and the wrapper's -- read from the sources, not restated.
    others = set()
    for rel in ("SCED-tools/scripts/daily-sync-local.sh",
                "SCED-tools/scripts/sced-run-now.sh"):
        path = os.path.join(WORKSPACE, rel)
        if not os.path.exists(path):
            continue
        for match in re.finditer(r"^#\s{0,3}(\d{1,3})\s{2}", kc.read_text(path),
                                 re.M):
            others.add(int(match.group(1)))
    kzc = os.path.join(WORKSPACE, "SCED-tools/scripts/koreanize/kz_common.py")
    if os.path.exists(kzc):
        for match in re.finditer(r"^\s{4}(\d{1,3}):\s*\"", kc.read_text(kzc), re.M):
            others.add(int(match.group(1)))
    assert others, "no other exit table could be read -- this check would be vacuous"
    assert not (ours & others), sorted(ours & others)


def test_dispatcher_knows_exactly_the_config_stages():
    text = kc.read_text(DISPATCHER)
    block = text.split("stage_module() {", 1)[1].split("}", 1)[0]
    named = set(re.findall(r"^\s{4}([a-z]+)\)", block, re.M))
    assert named == set(kz.STAGES), (sorted(named), sorted(kz.STAGES))


def test_accept_flag_family_is_closed():
    flags = kc.all_flags()
    assert flags == set(["--accept-declared-remainder"]), sorted(flags)
    for flag in flags:
        assert kc.row_by_flag(flag) is not None
    assert not kc.assert_tables_total()


def test_config_validates_and_its_pin_is_stable():
    cfg = _cfg()
    first = kz.compute_config_sha256(cfg)
    assert first == kz.compute_config_sha256(_cfg())
    assert len(cfg["packs"]) == 22
    assert cfg["declared_remainders"] == 0
    assert "cycle_code" not in cfg["library_entry"]
    assert cfg["library_entry"]["author"] != "Fantasy Flight Games"


def test_get_mini_id_matches_the_lua():
    assert kc.get_mini_id("akn06005") == "akn06005-m"
    assert kc.get_mini_id("akn06-005") == "akn06-m"       # the trap the scheme avoids
    assert kc.get_mini_id("a" * 16) == "a" * 16 + "-m"
    assert kc.mini_id_branch("akn06005") == 1


def test_the_committed_idmap_is_present_and_agrees_with_the_config():
    path = kz.IDMAP_PATH
    assert os.path.exists(path), "%s is the durable audit record and is missing" % path
    idmap = json.loads(kc.read_text(path))
    cfg = _cfg()
    counts = idmap["counts"]
    for key, oracle in (("new_ids", "new_ids"), ("investigators", "investigators"),
                        ("minicards", "minicards"), ("other", "other_ids")):
        assert counts[key] == cfg["oracles"][oracle], \
            "%s: %d vs oracle %d" % (key, counts[key], cfg["oracles"][oracle])
    assert counts["declared_remainders"] == cfg["declared_remainders"]
    suffix = cfg["id_scheme"]["minicard_suffix"]
    pattern = re.compile(cfg["id_scheme"]["regex"])
    for new_id in idmap["ids"]:
        stem = new_id[:-len(suffix)] if new_id.endswith(suffix) else new_id
        assert pattern.match(stem) and len(stem) == 8 and "-" not in stem, new_id


# ---------------------------------------------------------------------------
# Tier 1b -- the synthetic corpus
# ---------------------------------------------------------------------------

def _card(guid, nickname, tags, md, index="2664", face="A"):
    node = {
        "Name": "CardCustom", "GUID": guid, "Nickname": nickname,
        "CardID": int(index) * 100,
        "CustomDeck": {index: {
            "FaceURL": "http://cloud-3.steamusercontent.com/ugc/%s/FACE/" % face,
            "BackURL": "http://cloud-3.steamusercontent.com/ugc/%s/BACK/" % face}},
    }
    if tags is not None:
        node["Tags"] = tags
    if md is not None:
        node["GMNotes"] = json.dumps(md, ensure_ascii=False)
    return node


def _synthetic_states():
    """The three shapes that break a naive converter, in one pack.

    Twin.json's investigator Deck holds two investigators (the Mlynar shape).
    Solo.json's signature Deck prints one card twice (a genuine multi-print) and
    its two children reuse CustomDeck index 2664 with DIFFERENT art, which is the
    shape that breaks silently the moment two such cards land in one Deck.
    Broken.json's minicard carries no GMNotes at all (the pack-3 shape).
    """
    return collections.OrderedDict([
        ("Solo.json", [
            _card("aaa001", "솔로", ["Investigator", "PlayerCard"],
                  {"id": "90001", "type": "Investigator"}),
            {"Name": "Deck", "GUID": "aaa002", "Tags": ["PlayerCard"],
             "DeckIDs": [266400, 266400],
             "CustomDeck": {"2664": {"FaceURL": "x", "BackURL": "y"}},
             "ContainedObjects": [
                 _card("aaa003", "중복", ["PlayerCard"],
                       {"id": "90002", "type": "Event"}, face="A"),
                 _card("aaa004", "중복", ["PlayerCard"],
                       {"id": "90002", "type": "Event"}, face="A")]},
            _card("aaa005", "솔로", ["Minicard"],
                  {"id": "90001-m", "type": "Minicard"}),
        ]),
        ("Twin.json", [
            {"Name": "Deck", "GUID": "bbb001",
             "Tags": ["Investigator", "PlayerCard"], "ContainedObjects": [
                 _card("bbb002", "쌍둥이", ["Investigator", "PlayerCard"],
                       {"id": "90003", "type": "Investigator"}),
                 _card("bbb003", "쌍둥이", ["Investigator", "PlayerCard"],
                       {"id": "90004", "type": "Investigator"})]},
            {"Name": "Deck", "GUID": "bbb004", "Tags": ["Minicard"],
             "ContainedObjects": [
                 _card("bbb005", "쌍둥이", ["Minicard"],
                       {"id": "90003-m", "type": "Minicard"}),
                 _card("bbb006", "쌍둥이", ["Minicard"],
                       {"id": "90004-m", "type": "Minicard"})]},
        ]),
        ("Broken.json", [
            _card("ccc001", "결손", ["Investigator", "PlayerCard"],
                  {"id": "90005", "type": "Investigator"}),
            _card("ccc002", "", ["Minicard"], None),
        ]),
    ])


def _synthetic_records():
    records = []
    for filename, states in _synthetic_states().items():
        for index, node in enumerate(states):
            scan.walk_object(node, "00", filename, "/%d" % index, None, records)
    return records


def test_synthetic_walk_and_defects():
    records = _synthetic_records()
    assert len(records) == 13, len(records)
    twin = [r for r in records if r["file"] == "Twin.json"
            and r["source_type"] == "Investigator"]
    assert len(twin) == 2, "the two-investigator Deck was flattened or missed"
    dupes = [r for r in records if r["source_id"] == "90002"]
    assert len(dupes) == 2 and all(r["parent_deck"] == "aaa002" for r in dupes)
    defects = [r for r in records if scan.defects_of(r)]
    assert len(defects) == 1 and defects[0]["path"] == "/1", \
        [(d["file"], d["path"], scan.defects_of(d)) for d in defects]
    assert scan.defects_of(defects[0]) == ["gmnotes_missing"]


def test_synthetic_renumber_collapses_a_multi_print_and_keeps_the_twins_apart():
    cfg = _local(_cfg(), exceptions=[], role_overrides=[])
    records = _synthetic_records()
    roles = renumber.resolve_roles(cfg, records)
    obj_rows, id_rows = renumber.exception_index(cfg, records)
    groups = renumber.build_groups(cfg, records, roles, obj_rows, id_rows)
    renumber.assign_ordinals(cfg, groups)
    new_ids = renumber.compose(cfg, groups)

    # Nine cards from thirteen objects: the twice-printed Event (90002) collapses
    # to one id and takes no -m, and the twins keep separate ids AND separate
    # minicards despite sharing a Deck.
    assert sorted(new_ids.values()) == [
        "akn00001", "akn00001-m", "akn00002",
        "akn00003", "akn00003-m", "akn00004", "akn00004-m",
        "akn00005", "akn00005-m",
    ], sorted(new_ids.values())
    multi = [g for g in groups.values() if len(g.objects) == 2
             and g.role == renumber.ROLE_CARD]
    assert len(multi) == 1 and new_ids[multi[0].key] == "akn00002", \
        "the card printed twice did not collapse to one id"
    # The GMNotes-less minicard took its file's single investigator.
    derived = [g for g in groups.values() if g.inherits_from is not None]
    assert len(derived) == 1 and new_ids[derived[0].key] == "akn00005-m"


def test_synthetic_repair_and_assemble_preserve_every_deck():
    cfg = _cfg()
    records = _synthetic_records()
    local = _local(cfg, exceptions=[], role_overrides=[], repairs=[
        {"id": "R-s", "rule": "minicard_from_investigator",
         "defect_class": "gmnotes_missing", "fields": {"Tags": ["Minicard"]},
         "evidence": "synthetic", "targets": [
             {"pack": "00", "file": "Broken.json", "path": "/1"}]}])
    roles = renumber.resolve_roles(local, records)
    obj_rows, id_rows = renumber.exception_index(local, records)
    groups = renumber.build_groups(local, records, roles, obj_rows, id_rows)
    renumber.assign_ordinals(local, groups)
    new_ids = renumber.compose(local, groups)
    inventory = {"counts": {"objects": len(records),
                            "objects_with_metadata":
                                sum(1 for r in records if r["md_state"] == "ok")},
                 "defects": [dict(r, classes=scan.defects_of(r)) for r in records
                             if scan.defects_of(r)],
                 "objects": records, "source_tree_sha256": "0" * 64}
    idmap = renumber.build_idmap(local, inventory, groups, new_ids)
    _rows, patches = repair.build_repairs(local, inventory, idmap)
    assert len(patches) == 1

    trees = collections.OrderedDict(
        ((("00", name), states) for name, states in _synthetic_states().items()))
    assemble.apply_patches(trees, {"patches": patches})
    assemble.rewrite_ids(trees, idmap)
    counter = [0]
    norm = local["url_normalisation"]
    for key in trees:
        trees[key] = assemble.normalise_urls(trees[key], norm["from"], norm["to"],
                                             counter)
    assert counter[0] == 20, counter[0]

    flat = []
    for states in trees.values():
        flat.extend(states)
    bag = {"Name": "Custom_Model_Bag", "GUID": "top000",
           "ContainedObjects": [{"Name": "Bag", "GUID": "sub000",
                                 "ContainedObjects": flat}]}
    built = assemble.collect_output(bag)

    # Every Deck kept its exact child sequence and its DeckIDs.
    for record in records:
        if record["name"] != "Deck":
            continue
        assert built[record["guid"]]["children"] == record["children"], record["guid"]
        assert built[record["guid"]]["deck_ids"] == record["deck_ids"]
    # No source id survives and no cloud-3 url does.
    text = json.dumps(bag, ensure_ascii=False)
    assert norm["from"] not in text
    for record in records:
        if record["source_id"]:
            assert '"id": "%s"' % record["source_id"] not in text, record["source_id"]


def test_v5_catches_two_cards_with_the_same_customdeck_index_in_one_deck():
    """The prohibition's teeth. Index 2664 appears with SEVEN different image
    pairs across the real corpus, so a Deck holding two of them silently swaps
    art -- and nothing but this check can see it."""
    deck = {"Name": "Deck", "GUID": "ddd001", "Tags": ["PlayerCard"],
            "ContainedObjects": [
                _card("ddd002", "A", ["PlayerCard"],
                      {"id": "akn00010", "type": "Event"}, face="A"),
                _card("ddd003", "B", ["PlayerCard"],
                      {"id": "akn00011", "type": "Event"}, face="B")]}
    pairs = collections.defaultdict(set)
    for child in deck["ContainedObjects"]:
        for index, entry in child["CustomDeck"].items():
            pairs[index].add((entry["FaceURL"], entry["BackURL"]))
    conflicting = [i for i, seen in pairs.items() if len(seen) > 1]
    assert conflicting == ["2664"], \
        "the fixture no longer expresses the conflict V5 exists to catch"


def test_v12_url_pattern_refuses_every_injection_shape():
    for good in ("https://steamusercontent-a.akamaihd.net/ugc/1/ABCDEF/",
                 "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/boxart/a.jpg"):
        assert verify.SAFE_URL.match(good), good
    for bad in ("http://x/y", "https://x/y;whoami", "-oPWNED", "file:///etc/passwd",
                "https://x/$(id)", "https://x/y' --config /tmp/evil",
                "https://x/y\nhttps://z/", "https://x/`id`"):
        assert not verify.SAFE_URL.match(bad), bad


def test_run_id_and_source_overrides_refuse_traversal():
    cfg = _cfg()
    for bad in ("../../SCED/objects", "latest", "; rm -rf /",
                "20260830T000000Z/../.."):
        try:
            kz.resolve_run_dir(cfg, run_id=bad)
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_DISPATCH_REFUSED, (bad, exc.code)
        else:
            raise AssertionError("run id %r was accepted" % bad)
    for bad in (os.path.join(WORKSPACE, "SCED-downloads"),
                os.path.join(WORKSPACE, "docs", "Arknights", "..", "..", "SCED")):
        try:
            kz.source_root(cfg, override=bad)
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_GUARD, (bad, exc.code)
        else:
            raise AssertionError("--source %r was accepted" % bad)


def test_the_write_guard_refuses_every_path_outside_its_roots():
    cfg = _cfg()
    for bad in ("docs/Arknights/x.json", "SCED-downloads/library.json",
                "SCED-downloads/downloadable/playercards/arknights.json",
                "SCED/objects/AllPlayerCards.15bb07/x.json",
                "SCED-tools/scripts/koreanize/kz_common.py",
                ".local-sync/run/daily-sync.lock/owner"):
        findings = kz.check_write_paths(cfg, [os.path.join(WORKSPACE, bad)])
        assert findings, "%s was NOT refused by the write guard" % bad
    with tempfile.TemporaryDirectory() as tmp:
        assert kz.check_write_paths(cfg, [os.path.join(tmp, "x.json")]), \
            "a path outside the workspace was not refused"


def test_a_pack_folder_file_that_is_not_a_tts_save_is_drift_not_a_defect():
    cfg = _cfg()
    # The AppleDouble sidecar is caught by the filter rather than by the parse:
    # macOS writes `._Name.json` on a non-native copy of the corpus, it ends in
    # .json, and its resource-fork bytes are not UTF-8.
    for name, expected in (("Specter.json", True), ("._Specter.json", False),
                           (".DS_Store", False), ("notes.txt", False),
                           ("._.json", False), ("Zima.JSON", False)):
        assert kz.is_pack_file(name) is expected, name

    with tempfile.TemporaryDirectory() as tmp:
        kc.atomic_write_text(os.path.join(tmp, "good.json"),
                             json.dumps({"ObjectStates": []}))
        assert kz.read_tts_save(cfg, "good.json", root=tmp) == {"ObjectStates": []}
        # Anything else under the pack folders -- a truncated save, a note, an
        # export -- is exit 82 naming the path, never exit 1 as a tool defect.
        kc.atomic_write_text(os.path.join(tmp, "truncated.json"),
                             '{"ObjectStates": [')
        try:
            kz.read_tts_save(cfg, "truncated.json", root=tmp)
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_DRIFT, exc.code
            assert "truncated.json" in str(exc), str(exc)
        else:
            raise AssertionError("a file that is not a TTS save was accepted")


def test_the_nightly_window_is_the_union_and_is_not_hardcoded():
    win = kz.schedule_window()
    assert win["source"].endswith("sync-schedule.json")
    assert win["start_from"] and win["end_from"]
    inside, _ = kz.in_schedule_window(now_min=2 * 60 + 30)
    assert inside, "02:30 must be inside the nightly window"
    inside, _ = kz.in_schedule_window(now_min=12 * 60)
    assert not inside, "12:00 must be outside"


def test_the_lock_owner_line_matches_the_drivers_format():
    fields = kz.parse_owner(kz.owner_text(pid=1234, started=99))
    assert fields["repo"] == "arknights"
    assert sorted(fields) == ["host", "pid", "repo", "started"]


# ---------------------------------------------------------------------------
# Tier 1c -- `publish`, the only stage that writes outside <run_dir>
# ---------------------------------------------------------------------------

def test_publish_targets_are_a_closed_pair_the_write_guard_also_refuses():
    """The pair is publish's ONLY exemption, and it has to be an exemption rather
    than a hole: every target must still be refused by guard.write_roots, so no
    other stage can reach it."""
    cfg = _cfg()
    pair = publish.publish_targets(cfg)
    assert pair == ("SCED-downloads/downloadable/playercards/arknights.json",
                    "SCED-downloads/library.json"), pair
    for rel in pair:
        assert kz.check_write_paths(cfg, [os.path.join(WORKSPACE, rel)]), \
            "%s is not refused by the ordinary write guard" % rel
    for bad in ("SCED-downloads/library.json.bak",
                "SCED-downloads/downloadable/playercards/arknights.json.tmp",
                "SCED-downloads/downloadable/playercards/../../../SCED/x.json",
                "docs/Arknights/TTS용 파일/x.json",
                ".local-sync/run/daily-sync.lock/owner"):
        try:
            publish.assert_publish_targets(cfg, [bad])
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_GUARD, (bad, exc.code)
        else:
            raise AssertionError("publish accepted the target %r" % bad)


def test_publish_plan_binding_refuses_a_plan_that_moved():
    """Exit 81 on a different length, a different order, or different bytes --
    the three ways an approved write set turns into a different one."""
    class _Plan(object):
        def document(self):
            return {"entries": [{"path": "a", "plan_sha256": "x"},
                                {"path": "b", "plan_sha256": "y"}]}
    plan = _Plan()
    assert publish.assert_plan_matches(plan, plan.document())
    for broken in ({"entries": [{"path": "a", "plan_sha256": "x"}]},
                   {"entries": [{"path": "b", "plan_sha256": "y"},
                                {"path": "a", "plan_sha256": "x"}]},
                   {"entries": [{"path": "a", "plan_sha256": "x"},
                                {"path": "b", "plan_sha256": "MOVED"}]}):
        try:
            publish.assert_plan_matches(plan, broken)
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_PRECONDITION, exc.code
        else:
            raise AssertionError("a moved plan was accepted: %r" % broken)


def test_publish_refuses_inside_the_nightly_window_before_the_banner():
    """The window refusal has to land BEFORE the prompt, or an operator types
    LIVE into a run that was always going to refuse."""
    try:
        publish.probe_before_banner(now_min=2 * 60 + 30)
    except kc.AknRefusal as exc:
        assert exc.code == kc.EXIT_GUARD, exc.code
        assert "window" in exc.message, exc.message
    else:
        raise AssertionError("02:30 was not refused")


def test_publish_refuses_while_the_nightly_lock_is_held():
    """Against a lock in a tempdir, never the real one: a suite that took the
    workspace-wide lock would make every concurrent run exit 3."""
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "daily-sync.lock")
        os.makedirs(lock_dir)
        kc.atomic_write_text(os.path.join(lock_dir, "owner"),
                             "pid=999999 repo=SCED-downloads started=1 host=t\n")
        try:
            kz.NightlyLock(lock_dir=lock_dir).acquire()
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_GUARD, exc.code
            assert "repo=SCED-downloads" in (exc.detail or ""), exc.detail
        else:
            raise AssertionError("a held lock was acquired anyway")


def test_publish_lock_round_trip_writes_the_drivers_owner_format():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "daily-sync.lock")
        lock = kz.NightlyLock(lock_dir=lock_dir)
        with lock:
            fields = kz.parse_owner(kc.read_text(os.path.join(lock_dir, "owner")))
            assert fields["repo"] == "arknights", fields
            assert fields["pid"] == str(os.getpid()), fields
            assert sorted(fields) == ["host", "pid", "repo", "started"], fields
        assert lock.released_ok, lock.release_message
        assert not os.path.exists(lock_dir), "release left the lock directory"


def test_publish_library_plan_is_plus_one_and_sorter_stable():
    """The +1 delta is DERIVED from the file, and a re-publish is 0 -- which is
    the case a hardcoded 194 would get wrong the second time it ran."""
    cfg = _cfg()
    sorter = publish.load_sorter(WORKSPACE)
    entry = dict(cfg["library_entry"])
    base = {"content": [{"name": "Zzz", "type": "playercards",
                         "author": "Someone", "filename": "zzz"}]}
    doc, replaced = publish.planned_library(sorter, base, entry)
    assert not replaced and len(doc["content"]) == len(base["content"]) + 1
    again, replaced2 = publish.planned_library(sorter, doc, entry)
    assert replaced2 and len(again["content"]) == len(doc["content"])
    assert kc.json_bytes(again) == kc.json_bytes(doc), "not sorter-stable"
    ours = [i for i in doc["content"] if i.get("filename") == "arknights"][0]
    assert "cycle_code" not in ours and "scenariocount" not in ours
    assert list(ours) == [k for k in sorter.KEY_ORDER if k in ours], list(ours)


def test_publish_commit_command_is_text_and_names_only_the_two_paths():
    """It is PRINTED, never run. kc.scan_git_calls() proves no module builds a
    git argv outside the wrapper; this proves the text an operator will paste
    touches nothing else."""
    cfg = _cfg()
    text = publish.commit_command(cfg)
    assert "downloadable/playercards/arknights.json" in text
    assert "library.json" in text
    for path in ("decomposed/", "objects/", "docs/", "-A", "--all"):
        assert path not in text.split("commit")[0], path


def _temp_publish_workspace(tmp, payload_text):
    """A whole workspace in a tempdir: the run directory the plan is built from,
    a library.json, and a COPY of sort_library.py -- whose JSON_FILE resolves
    from its own __file__, so the copy sorts the copy and the real
    SCED-downloads/library.json is unreachable from this test by construction."""
    cfg = _cfg()
    run_dir = os.path.join(tmp, cfg["run_root"], "20260830T000000Z")
    kc.atomic_write_text(os.path.join(run_dir, "arknights.json"), payload_text)
    library = {"content": [
        {"name": "Night of the Zealot", "type": "campaign",
         "author": "Fantasy Flight Games", "decomposed": True,
         "filename": "night_of_the_zealot"},
        {"name": "Zzz", "type": "playercards", "author": "Someone",
         "decomposed": False, "filename": "zzz"}]}
    kc.atomic_write_json(os.path.join(tmp, "SCED-downloads", "library.json"),
                         library)
    kc.atomic_write_text(
        os.path.join(tmp, "SCED-downloads", "misc", "sort_library.py"),
        kc.read_text(os.path.join(WORKSPACE, publish.SORT_LIBRARY_REL)))
    return cfg, run_dir, library


def test_publish_write_then_restore_round_trips_in_a_temp_workspace():
    """The live write path, exercised where it can do no harm.

    S9 -- the real publish -- is the operator's, so without this the snapshot,
    the two writes, the imported sorter and the restore would ship having never
    run once.

    It also carries the claim commit_writes used to make at RUNTIME by calling
    sort_json_file() on the published file: that our in-memory application of
    get_sort_keys/reorder_item_keys equals what the file-level function puts on
    disk. That call was a truncate-then-rewrite of a published file and is gone;
    the claim is asserted here instead, against a temp workspace where the
    sorter's in-place write can do no harm."""
    payload_text = '{\n  "Name": "Custom_Model_Bag"\n}\n'
    with tempfile.TemporaryDirectory() as tmp:
        cfg, run_dir, before = _temp_publish_workspace(tmp, payload_text)
        library_path = os.path.join(tmp, "SCED-downloads", "library.json")
        before_sha = kc.sha256_file(library_path)

        plan = publish.PublishPlan(cfg, run_dir, workspace=tmp)
        assert plan.delta == 1 and not plan.replaces_existing
        publish.take_snapshots(cfg, plan)
        written = publish.commit_writes(cfg, plan)

        payload_detail, library_detail = publish.post_write_checks(plan, written)
        assert not payload_detail and not library_detail, (payload_detail,
                                                           library_detail)
        after = json.loads(kc.read_text(library_path))
        assert len(after["content"]) == len(before["content"]) + 1
        ours = [i for i in after["content"] if i.get("filename") == "arknights"]
        assert len(ours) == 1 and "cycle_code" not in ours[0], ours
        # The FFG entry keeps its original position; ours sorts among the rest.
        assert after["content"][0]["filename"] == "night_of_the_zealot"
        payload = os.path.join(tmp, publish.payload_rel(cfg))
        assert kc.read_text(payload) == payload_text

        # The file-level sorter, run on what we wrote, must change nothing.
        # This is the assertion that replaces the removed runtime call, so it is
        # made through the REAL sort_json_file() and not through our own copy of
        # its key functions -- otherwise it would only be testing planned_library
        # against itself.
        after_sha = kc.sha256_file(library_path)
        publish.load_sorter(tmp).sort_json_file()
        assert kc.sha256_file(library_path) == after_sha, \
            "misc/sort_library.py disagrees with planned_library about the " \
            "canonical order -- the in-memory sort is no longer equivalent"

        restored, detail = publish.run_restore(cfg, run_dir, assume_yes=True,
                                               workspace=tmp, confirm=False)
        assert not detail, detail
        assert sorted(restored) == sorted(publish.publish_targets(cfg)), restored
        assert kc.sha256_file(library_path) == before_sha, \
            "library.json was not restored byte-for-byte"
        assert not os.path.exists(payload), \
            "the created payload was not deleted by --restore"


def test_p6_bands_80_before_a_write_and_81_after_one():
    """The exit code and the report have to agree about whether files exist.

    A dirty lock release can only happen once both targets are on disk, and 80
    contracts `nothing was written` -- so a fixed 80 told the operator not to run
    --restore in the one case where --restore is the move. akn_config's
    NightlyLock.__exit__ already assumed this split: it reports instead of
    raising precisely so the caller can pick the band.
    """
    assert publish.interlock_exit([]) == kc.EXIT_GUARD
    assert publish.interlock_exit(["SCED-downloads/library.json"]) == \
        kc.EXIT_PRECONDITION
    # Through pick_exit, because 80 outranks 81 in EXIT_PRECEDENCE -- a P6 still
    # banded 80 would mask every 81 in the same report.
    post = kc.check("P6", "nightly interlock", ["lock was taken over"],
                    exit_on_fail=publish.interlock_exit(["x"]))
    assert post["status"] == "fail"
    assert kc.pick_exit([post["exit_on_fail"]]) == kc.EXIT_PRECONDITION

    # AST-scoped, for the reason its sibling below states: everything above
    # exercises the HELPER, so reverting the call site to a fixed kc.EXIT_GUARD
    # would leave all of it green and silently restore the contract break.
    tree = ast.parse(kc.read_bytes(os.path.join(PACKAGE_DIR, "akn_publish.py")))
    body = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_publish"]
    assert len(body) == 1, "run_publish moved or was renamed"
    banded = [n for n in ast.walk(body[0])
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "check"
              and n.args and getattr(n.args[0], "s", None) == "P6"]
    assert len(banded) == 1, "run_publish no longer raises exactly one P6 check"
    kwargs = {k.arg: k.value for k in banded[0].keywords}
    assert isinstance(kwargs.get("exit_on_fail"), ast.Call) and \
        getattr(kwargs["exit_on_fail"].func, "id", None) == "interlock_exit", \
        "run_publish's P6 stopped banding through interlock_exit"


def test_the_restore_read_refuses_a_hardlink_and_a_symlink_at_the_derived_name():
    """assert_restore_record checks a PATH; the loop then re-opens the NAME.

    Between the two sits confirm_live's stdin.readline(), so the window is human
    time rather than a race -- and a hard link needs no timing at all, because
    islink() is false for one and realpath() reports it as this path. Both
    channels are planted for real here and driven through the reader the loop
    uses, since asserting the path checks' own return values is what let this
    reach a third verification round.
    """
    with tempfile.TemporaryDirectory() as tmp:
        secret = os.path.join(tmp, "secret.env")
        kc.atomic_write_text(secret, "SCED_SYNC_DISCORD_WEBHOOK=xxx\n")
        snap = os.path.join(tmp, "SCED-downloads__library.json")

        os.link(secret, snap)
        # Non-vacuity: both path-shaped predicates PASS on the hard link.
        assert not os.path.islink(snap)
        assert os.path.realpath(snap).startswith(os.path.realpath(tmp) + os.sep)
        try:
            kc.read_text_nofollow(snap, kc.EXIT_PRECONDITION)
            raise AssertionError("a hardlinked snapshot was read")
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_PRECONDITION, exc.code

        os.unlink(snap)
        os.symlink(secret, snap)
        try:
            kc.read_text_nofollow(snap, kc.EXIT_PRECONDITION)
            raise AssertionError("a symlinked snapshot was read")
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_PRECONDITION, exc.code

        # A real snapshot -- one link, not a link -- still reads.
        os.unlink(snap)
        kc.atomic_write_text(snap, "{}\n")
        assert kc.read_text_nofollow(snap, kc.EXIT_PRECONDITION) == "{}\n"

    # AST-scoped, and the half that actually stops a regression: everything above
    # exercises the READER, so reverting the loop to kc.read_text would leave all
    # of it green. Assert the call site instead of trusting it.
    tree = ast.parse(kc.read_bytes(os.path.join(PACKAGE_DIR, "akn_publish.py")))
    body = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_restore"]
    assert len(body) == 1, "run_restore moved or was renamed"
    # Scoped to the argument, not to the function. manifest.json used to be
    # carved out here as "legitimately a plain read, validated before anything
    # is written" -- true against a link at the FINAL component, false against a
    # hijacked prefix, since the manifest is what chooses the entries the loop
    # restores. Both reads are asserted now.
    def first_arg(node):
        return getattr(node.args[0], "id", None) if node.args else None

    reads = collections.defaultdict(set)
    for node in ast.walk(body[0]):
        if isinstance(node, ast.Call):
            reads[getattr(node.func, "attr", None)].add(first_arg(node))
    assert "snapshot" in reads["read_text_nofollow"], \
        "run_restore stopped reading the snapshot through the no-follow reader"
    assert "snapshot" not in reads["read_text"], \
        "run_restore reads a snapshot through a plain open() again"
    assert "manifest_path" in reads["read_text_nofollow"], \
        "run_restore stopped reading the manifest through the no-follow reader"
    assert "manifest_path" not in reads["read_text"], \
        "run_restore reads the restore manifest through a plain open() again"


def test_publish_restore_takes_the_lock_and_stamps_like_a_live_write():
    """--restore writes the two PUBLISHED paths, so it takes the lock and leaves
    the stamp `publish --live` does.

    The nightly window is deliberately NOT asserted here: run_restore only warns
    about it, because refusing an unplanned recovery for up to 2h25m is a worse
    failure than overlapping a nightly whose lock this process holds. Two of the
    three interlocks are enforced and that is the design; asserting the third
    would encode the opposite decision.
    """
    payload_text = '{\n  "Name": "Custom_Model_Bag"\n}\n'
    with tempfile.TemporaryDirectory() as tmp:
        cfg, run_dir, _ = _temp_publish_workspace(tmp, payload_text)
        plan = publish.PublishPlan(cfg, run_dir, workspace=tmp)
        publish.take_snapshots(cfg, plan)
        publish.commit_writes(cfg, plan)

        # The lock must be scoped to the workspace, or this gate takes the real
        # one and every concurrent run exits 3.
        lock_dir = publish.workspace_lock(tmp).lock_dir
        assert lock_dir.startswith(os.path.realpath(tmp)) \
            or lock_dir.startswith(tmp), lock_dir

        os.makedirs(lock_dir)
        kc.atomic_write_text(os.path.join(lock_dir, "owner"),
                             "pid=999999 repo=SCED-downloads started=1 host=t\n")
        try:
            publish.run_restore(cfg, run_dir, assume_yes=True, workspace=tmp,
                                confirm=False)
        except kc.AknRefusal as exc:
            assert exc.code == kc.EXIT_GUARD, exc.code
            assert "repo=SCED-downloads" in (exc.detail or ""), exc.detail
        else:
            raise AssertionError("--restore ran while the nightly lock was held")
        os.unlink(os.path.join(lock_dir, "owner"))
        os.rmdir(lock_dir)

        # base=None explicitly: it is base_sha()'s own answer for a workspace
        # with no SCED-downloads checkout, and the parameter is required so the
        # in-lock git spawn cannot come back as a default.
        stamp = publish.write_restore_stamp(
            run_dir, [{"path": rel} for rel in publish.publish_targets(cfg)],
            tmp, None)
        record = json.loads(kc.read_text(stamp))
        assert record["stage"].endswith("--restore"), record
        assert record["pid"] == os.getpid(), record
        assert sorted(record["write_paths"]) == sorted(publish.publish_targets(cfg))
        assert publish.remove_stamp(tmp), "the stamp was not compare-and-deleted"

        restored, detail = publish.run_restore(cfg, run_dir, assume_yes=True,
                                               workspace=tmp, confirm=False)
        assert not detail, detail
        assert sorted(restored) == sorted(publish.publish_targets(cfg)), restored
        assert not os.path.exists(lock_dir), "the restore did not release the lock"
        assert not os.path.exists(publish.stamp_path(tmp)), \
            "the restore left its stamp behind"


def test_publish_restore_refuses_to_delete_a_payload_someone_else_edited():
    """A `create` is deleted ONLY when its bytes are still the ones this run
    wrote. Anything else is somebody's later edit, and guessing is not an option
    open to a recovery tool."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg, run_dir, _before = _temp_publish_workspace(tmp, '{"a": 1}\n')
        plan = publish.PublishPlan(cfg, run_dir, workspace=tmp)
        publish.take_snapshots(cfg, plan)
        publish.commit_writes(cfg, plan)
        payload = os.path.join(tmp, publish.payload_rel(cfg))
        kc.atomic_write_text(payload, '{"a": 2}\n')
        restored, detail = publish.run_restore(cfg, run_dir, assume_yes=True,
                                               workspace=tmp, confirm=False)
        assert os.path.exists(payload), "an edited payload was deleted anyway"
        assert any("not deleting" in d for d in detail), detail
        assert publish.payload_rel(cfg) not in restored, restored


class _V10Subject(object):
    """The three fields v10 reads. A full Subject needs a built run; V10's live
    half needs only a workspace with a library.json and the sorter in it."""

    def __init__(self, cfg, workspace):
        self.cfg = cfg
        self.workspace = workspace
        self.paths = {"arknights": os.path.join(workspace, "arknights.json")}


def _v10_workspace(tmp, content):
    downloads = os.path.join(tmp, "SCED-downloads")
    kc.atomic_write_json(os.path.join(downloads, "library.json"),
                         {"content": content})
    kc.atomic_write_text(
        os.path.join(downloads, "misc", "sort_library.py"),
        kc.read_text(os.path.join(WORKSPACE, verify.SORT_LIBRARY)))
    return _V10Subject(_cfg(), tmp)


def _v10_sorted(cfg, extra):
    sorter = publish.load_sorter(WORKSPACE)
    base = json.loads(kc.read_text(os.path.join(WORKSPACE, verify.LIBRARY_JSON)))
    doc, _replaced = publish.planned_library(sorter, base, dict(extra))
    return doc["content"]


def test_v10_degrades_to_the_template_half_before_publish_and_runs_after():
    """The half that cannot run yet must report `skipped`, and the half that can
    must actually fail on a wrong entry -- a check never seen to fail is not a
    check."""
    cfg = _cfg()
    entry = dict(cfg["library_entry"])
    base = json.loads(kc.read_text(os.path.join(WORKSPACE, verify.LIBRARY_JSON)))
    pre = len(base["content"])

    with tempfile.TemporaryDirectory() as tmp:
        result = verify.v10(_v10_workspace(tmp, base["content"]))
        assert result["status"] == "skipped", result
        assert "publish" in result["note"], result["note"]

    with tempfile.TemporaryDirectory() as tmp:
        content = _v10_sorted(cfg, entry)
        assert len(content) == pre + 1, (len(content), pre)
        result = verify.v10(_v10_workspace(tmp, content))
        assert result["status"] == "pass", result["detail"]

    # cycle_code is the safety mechanism; V10 is the check that would notice.
    with tempfile.TemporaryDirectory() as tmp:
        tampered = dict(entry)
        tampered["cycle_code"] = "aknx"
        result = verify.v10(_v10_workspace(tmp, _v10_sorted(cfg, tampered)))
        assert result["status"] == "fail", result
        assert any("cycle_code" in d for d in result["detail"]), result["detail"]

    # a hand-edited shipped entry, and a truncated file
    with tempfile.TemporaryDirectory() as tmp:
        edited = dict(entry)
        edited["boxart"] = "https://example.invalid/other.jpg"
        result = verify.v10(_v10_workspace(tmp, _v10_sorted(cfg, edited)))
        assert result["status"] == "fail", result
    # Truncated but still sorter-stable and still carrying our entry, so the
    # only thing left for V10 to notice is the count.
    with tempfile.TemporaryDirectory() as tmp:
        sorter = publish.load_sorter(WORKSPACE)
        others = [i for i in base["content"] if i.get("filename") != "arknights"]
        short, _r = publish.planned_library(sorter, {"content": others[:20]},
                                            dict(entry))
        result = verify.v10(_v10_workspace(tmp, short["content"]))
        assert result["status"] == "fail", result
        assert any("truncated" in d for d in result["detail"]), result["detail"]


# ---------------------------------------------------------------------------
# Tier 2 -- the corpus. Skips LOUDLY.
# ---------------------------------------------------------------------------

def _newest_run(cfg):
    run_dir = kz.resolve_run_dir(cfg)
    if run_dir is None or not os.path.isdir(run_dir):
        _skip("no run directory under %s -- run `arknights.sh scan`"
              % cfg["run_root"])
    return run_dir


def test_corpus_source_tree_is_present_and_matches_the_pack_table():
    cfg = _cfg()
    try:
        root = kz.source_root(cfg)
    except kc.AknRefusal as exc:
        _skip("docs/Arknights/ is absent (%s)" % exc.message)
    packs = kz.packs_root(cfg, root)
    on_disk = set(kc.nfc(n) for n in os.listdir(packs)
                  if os.path.isdir(os.path.join(packs, n)))
    assert on_disk == set(kz.pack_by_folder(cfg)), \
        sorted(on_disk ^ set(kz.pack_by_folder(cfg)))


def test_corpus_scan_counts_match_the_design_oracles():
    cfg = _cfg()
    run_dir = _newest_run(cfg)
    path = os.path.join(run_dir, "inventory.json")
    if not os.path.exists(path):
        _skip("no inventory.json in %s" % run_dir)
    counts = json.loads(kc.read_text(path))["counts"]
    oracles = cfg["oracles"]
    for key, oracle in (("objects", "objects"),
                        ("objects_with_metadata", "objects_with_metadata"),
                        ("distinct_source_ids", "distinct_source_ids"),
                        ("distinct_guids", "distinct_guids"),
                        ("packs", "packs"), ("files", "files"),
                        ("defects", "defects")):
        assert counts[key] == oracles[oracle], \
            "%s: %d vs oracle %d" % (key, counts[key], oracles[oracle])
    assert counts["url_cloud3"] == cfg["url_normalisation"]["expected"]


def test_corpus_verify_passes_end_to_end():
    cfg = _cfg()
    run_dir = _newest_run(cfg)
    for name in ("inventory", "idmap", "repairs", "source.tree", "arknights"):
        if not os.path.exists(os.path.join(run_dir, name + ".json")):
            _skip("%s.json is not in %s -- run the chain to `assemble`"
                  % (name, run_dir))
    report = verify.run_verify(cfg, run_dir, mode="verify-only")
    failed = [c["id"] for c in report["checks"] if c["status"] == "fail"]
    assert report["exit_code"] == 0, (failed, report["checks"])
    skipped = [c["id"] for c in report["checks"] if c["status"] == "skipped"]
    # V10's live half and V12 are the only legitimate skips in this pipeline.
    assert set(skipped) <= set(["V10", "V12"]), skipped
    # Totality, because the skip assertion above is satisfied by a MISSING check
    # too: run_verify appends V12 separately from the CHECKS tuple, so deleting
    # that line kept both len(CHECKS) == 12 and len(CHECK_IDS) == 13 true.
    assert [c["id"] for c in report["checks"]] == list(verify.CHECK_IDS), \
        "the run does not carry every check in CHECK_IDS, in order"


def test_corpus_collision_oracles_are_not_empty():
    """A collision check whose oracle is empty passes over nothing."""
    real, files = verify.real_card_ids(WORKSPACE)
    if not files:
        _skip("no .gmnotes files under %s -- the SCED checkout is absent"
              % verify.REAL_CARDS_GLOB)
    assert len(real) >= _cfg()["oracles"]["real_card_ids_min"], len(real)
    fan, fan_files = verify.fan_pack_ids(WORKSPACE)
    if not fan_files:
        _skip("no fan packs found -- the SCED-downloads checkout is absent")
    assert fan, "the fan-pack oracle is empty"


def test_corpus_output_is_byte_stable():
    """Derived GUIDs are deterministic, so a rebuild on unchanged inputs must
    produce the same bytes -- otherwise the committed artifact churns 23 GUIDs
    every run and its diffs become unreadable."""
    cfg = _cfg()
    run_dir = _newest_run(cfg)
    built = os.path.join(run_dir, "arknights.json")
    if not os.path.exists(built):
        _skip("no arknights.json in %s -- run `arknights.sh assemble`" % run_dir)
    before = kc.sha256_file(built)
    report, text = assemble.run_assemble(cfg, run_dir, mode="dry-run")
    assert report["exit_code"] == 0, [c for c in report["checks"]
                                      if c["status"] == "fail"]
    assert kc.sha256_bytes(text.encode("utf-8")) == before, \
        "a rebuild produced different bytes"


def test_corpus_publish_rehearsal_touches_nothing_in_sced_downloads():
    """The rehearsal is the default, and this is the property that makes the
    default safe. Asserted against git rather than against the code's intent."""
    cfg = _cfg()
    run_dir = _newest_run(cfg)
    if not os.path.exists(os.path.join(run_dir, "arknights.json")):
        _skip("no arknights.json in %s -- run `arknights.sh assemble`" % run_dir)
    downloads = os.path.join(WORKSPACE, publish.DOWNLOADS_ROOT)
    before = kc.git_stdout("status", ["--porcelain"], cwd=downloads)
    report, plan = publish.run_publish(cfg, run_dir, live=False, confirm=False)
    assert report["exit_code"] == 0, [c for c in report["checks"]
                                      if c["status"] == "fail"]
    assert plan.delta == 1, plan.delta
    after = kc.git_stdout("status", ["--porcelain"], cwd=downloads)
    assert before == after, "the rehearsal changed SCED-downloads:\n%s" % after
    for rel in report["write_set"]:
        assert rel.startswith(cfg["run_root"]), rel
    assert os.path.exists(publish.plan_path(run_dir)), "no plan was persisted"


SLOW = ("test_corpus_source_tree_is_present_and_matches_the_pack_table",
        "test_corpus_scan_counts_match_the_design_oracles",
        "test_corpus_verify_passes_end_to_end",
        "test_corpus_collision_oracles_are_not_empty",
        "test_corpus_output_is_byte_stable",
        "test_corpus_publish_rehearsal_touches_nothing_in_sced_downloads")


def main(argv=None):
    """The runner. pytest is not on the platform interpreter, so this is it."""
    argv = list(argv if argv is not None else sys.argv[1:])
    only = argv[0] if argv else None
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    if only:
        names = [n for n in names if only in n]
    passed, failed, skipped = 0, [], []
    for name in names:
        tier = "corpus" if name in SLOW else "synthetic"
        try:
            globals()[name]()
        except Skip as exc:
            skipped.append((name, str(exc)))
            print("SKIP %-8s %s\n       %s" % (tier, name, exc))
        except AssertionError as exc:
            failed.append((name, exc))
            print("FAIL %-8s %s\n       %s" % (tier, name, str(exc)[:400]))
        except kc.AknRefusal as exc:
            failed.append((name, exc))
            print("FAIL %-8s %s\n       %s" % (tier, name, str(exc)[:400]))
        else:
            passed += 1
            print("ok   %-8s %s" % (tier, name))
    print("\n%d passed, %d failed, %d skipped" % (passed, len(failed),
                                                  len(skipped)))
    if skipped:
        # LOUDLY. A suite reporting green on checks it did not perform reads as
        # coverage, which is worse than reporting nothing.
        print("SKIPPED CHECKS WERE NOT PERFORMED:")
        for name, reason in skipped:
            print("  %s -- %s" % (name, reason))
    return 1 if failed else 0


if __name__ == "__main__":
    kc.check_invocation_guards()
    sys.exit(main())


def test_the_restore_read_refuses_a_symlink_at_an_intermediate_component():
    """The sibling of the FIFO, and the class all four earlier fixes missed.

    O_NOFOLLOW, islink() and st_nlink are FINAL-component properties, and a
    realpath containment test RESOLVES its own prefix rather than refusing it --
    so <run_dir>/publish as a symlink satisfies every one of them at once, with
    an ordinary single-link regular file at the leaf. Planted for real, and the
    leaf predicates are asserted to still pass, so the day the prefix check is
    reverted this fault is proving something rather than agreeing with itself.
    """
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = os.path.join(tmp, "run", "20260830T000000Z")
        evil = os.path.join(tmp, "evil")
        os.makedirs(os.path.join(run_dir, "publish"))
        os.makedirs(evil)
        os.symlink(evil, os.path.join(run_dir, "publish", "pre"))
        forged = os.path.join(evil, "SCED-downloads__library.json")
        kc.atomic_write_text(forged, '{"FORGED": true}\n')

        # Non-vacuity: every predicate rounds 2-5 added PASSES on this plant.
        contain = os.path.realpath(publish.snapshot_root(run_dir)) + os.sep
        assert not os.path.islink(forged)
        assert os.path.realpath(forged).startswith(contain)
        assert os.lstat(forged).st_nlink == 1
        assert "FORGED" in kc.read_text_nofollow(forged, kc.EXIT_PRECONDITION)

        for subject in (run_dir, os.path.join(tmp, "run", "20260830T111111Z")):
            if subject != run_dir:
                # The run directory is a component too, not just what is inside it.
                os.symlink(evil, subject)
            try:
                publish.snapshot_root_checked(subject)
                raise AssertionError("a hijacked snapshot prefix was accepted")
            except kc.AknRefusal as exc:
                assert exc.code == kc.EXIT_GUARD, exc.code

        # A real tree still resolves, to the anchored path callers must join on.
        os.unlink(os.path.join(run_dir, "publish", "pre"))
        os.makedirs(publish.snapshot_root(run_dir))
        assert publish.snapshot_root_checked(run_dir) == \
            os.path.realpath(publish.snapshot_root(run_dir))

    # Everything above exercises the HELPER, so reverting the three call sites
    # to snapshot_root() would leave all of it green. Assert them by AST.
    tree = ast.parse(kc.read_bytes(os.path.join(PACKAGE_DIR, "akn_publish.py")))
    for name in ("run_restore", "assert_restore_record", "take_snapshots"):
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name]
        assert len(fn) == 1, "%s moved or was renamed" % name
        called = set()
        for node in ast.walk(fn[0]):
            if isinstance(node, ast.Call):
                called.add(getattr(node.func, "id", None))
        assert "snapshot_root_checked" in called, \
            "%s stopped resolving the snapshot root through the prefix check" % name
        assert "snapshot_root" not in called, \
            "%s derives the snapshot root without the prefix check again" % name


def test_a_failed_write_leaves_the_stamp_the_silent_night_row_reads():
    """remove_stamp() must not run from a finally.

    commit_writes() is two publish_write() calls with nothing spanning them, so
    an OSError on the second leaves a partial write -- and under a finally it
    also deleted .local-sync/run/arknights.stamp, the one artifact the Stall
    runbook's silent-night row reads. A SIGKILL preserved the stamp while an
    ordinary disk-full erased it, which is backwards.

    Two halves, because neither is enough alone: the behavioural half pins the
    partial write and that the stamp survives the sequence, and the AST half is
    what catches a revert -- run_publish's live path needs the whole stage chain
    and cannot be driven against a temp workspace.
    """
    payload_text = '{\n  "Name": "Custom_Model_Bag"\n}\n'
    with tempfile.TemporaryDirectory() as tmp:
        cfg, run_dir, _ = _temp_publish_workspace(tmp, payload_text)
        plan = publish.PublishPlan(cfg, run_dir, workspace=tmp)
        stamp = publish.write_stamp(plan)

        original, seen = publish.publish_write, []

        def failing(cfg_, rel, text, workspace):
            seen.append(rel)
            if len(seen) == 2:
                raise OSError(28, "No space left on device")
            return original(cfg_, rel, text, workspace)

        publish.publish_write = failing
        try:
            publish.commit_writes(cfg, plan)
            raise AssertionError("the injected OSError did not propagate")
        except OSError:
            pass
        finally:
            publish.publish_write = original

        assert len(seen) == 2, seen
        assert os.path.exists(stamp), \
            "the stamp did not survive a failed write sequence"
        assert publish.remove_stamp(tmp), "the stamp was not compare-and-deleted"

    tree = ast.parse(kc.read_bytes(os.path.join(PACKAGE_DIR, "akn_publish.py")))
    for name in ("run_publish", "run_restore"):
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == name]
        assert len(fn) == 1, "%s moved or was renamed" % name
        for node in ast.walk(fn[0]):
            if not isinstance(node, ast.Try):
                continue
            for stmt in ast.walk(ast.Module(body=node.finalbody, type_ignores=[])
                                 if hasattr(ast, "Module") else node):
                if isinstance(stmt, ast.Call) and \
                        getattr(stmt.func, "id", None) == "remove_stamp":
                    raise AssertionError(
                        "%s deletes the stamp from a finally again" % name)
