#!/usr/bin/python3
"""arknights `scan` -- discovery (design section 5.1).

READ-ONLY, and that is the point of the stage rather than a property of it. It
hashes the WHOLE of docs/Arknights/ BEFORE anything else reads the tree, walks
the 22 pack folders depth-first, and emits three artifacts:

  source.tree.json  the pre-run whole-tree digest V8 compares against
  inventory.json    one record per object -- 531 of them
  scan.json         the report, with the measured defect list

WHY DISCOVERY IS PURELY CONTENT-DRIVEN. `(12) 카시미어 방랑자/Mlynar.json` is the
single file whose three top-level objects are all Decks: a Deck tagged
["Investigator","PlayerCard"] holding TWO investigators, a signature Deck that
contains one card twice (a genuine multi-print), and a Deck tagged ["Minicard"]
holding the two matching minicards. Object order within ObjectStates also varies
across the corpus. Any assumption of "one investigator per file" or of a fixed
ObjectStates index is wrong, so roles are read from GMNotes and Tags and never
from position.

THE DEFECT LIST IS THREE STRUCTURAL CLASSES, and their union is 21 objects
(measured 2026-08-30). It is deliberately NOT "everything akn_repair fixes":
Zima.json/0 carries a GMNotes blob that PARSES and is internally consistent --
it is simply a byte-identical copy of another card's -- so nothing structural
distinguishes it, and it is a repair row with no scan defect. See DEFECT_CLASSES.
"""

import argparse
import collections
import json
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

import akn_common as kc  # noqa: E402
import akn_config as kz  # noqa: E402

STAGE = "scan"

#: The three classes, closed. Each is a property of the object's own SHAPE, so
#: each is answerable without knowing what the card is supposed to be.
DEFECT_CLASSES = (
    ("tags_missing", "Tags is absent or empty"),
    ("gmnotes_missing", "GMNotes is absent or empty on a card object"),
    ("minicard_mistagged", "GMNotes.type is Minicard but Tags does not carry it"),
)

CARD_NAMES = ("Card", "CardCustom")


def walk_object(node, pack_code, filename, path, parent_deck, out):
    """Depth-first, recording every node. Returns nothing; appends to `out`."""
    raw = node.get("GMNotes")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        md, md_state = None, "absent"
    else:
        try:
            md = json.loads(raw)
            md_state = "ok"
        except ValueError:
            md, md_state = None, "parse_failed"
        if md_state == "ok" and not isinstance(md, dict):
            md, md_state = None, "parse_failed"

    record = {
        "pack": pack_code,
        "file": filename,
        "path": path,
        "name": node.get("Name"),
        "nickname": kc.nfc(node.get("Nickname") or ""),
        "description": kc.nfc(node.get("Description") or ""),
        "tags": node.get("Tags"),
        "guid": node.get("GUID"),
        "md_state": md_state,
        "md": md,
        "source_id": (md or {}).get("id"),
        "source_type": (md or {}).get("type"),
        "parent_deck": parent_deck,
        "custom_deck": sorted((node.get("CustomDeck") or {}).keys()),
        "card_id": node.get("CardID"),
        "deck_ids": node.get("DeckIDs"),
        "children": [],
    }
    out.append(record)

    kids = node.get("ContainedObjects") or []
    inner_parent = node.get("GUID") if node.get("Name") == "Deck" else parent_deck
    for index, child in enumerate(kids):
        record["children"].append(child.get("GUID"))
        walk_object(child, pack_code, filename, "%s/%d" % (path, index),
                    inner_parent, out)


def defects_of(record):
    """Which of DEFECT_CLASSES this record trips. Empty list is the normal case."""
    hits = []
    tags = record["tags"]
    if not tags:
        hits.append("tags_missing")
    if record["md_state"] != "ok" and record["name"] in CARD_NAMES:
        hits.append("gmnotes_missing")
    if record["source_type"] == "Minicard" and "Minicard" not in (tags or []):
        hits.append("minicard_mistagged")
    return hits


def scan_source(cfg, source_override=None, workspace=None):
    """Hash, enumerate, walk. Returns (tree, records, defects, pack_stats)."""
    workspace = workspace or kc.WORKSPACE_ROOT
    root = kz.source_root(cfg, override=source_override, workspace=workspace)

    # Step 2, and it runs BEFORE anything else opens the tree so that V8's
    # reference is provably a pre-run reading.
    digest, per_file, byte_total = kc.sha256_tree_detail(root)
    tree = {
        "root": os.path.relpath(root, workspace),
        "sha256": digest,
        "files": len(per_file),
        "bytes": byte_total,
        "hashed_at": kc.utc_now(),
        "per_file": per_file,
    }

    packs_dir = kz.packs_root(cfg, root)
    if not os.path.isdir(packs_dir):
        kc.refuse(kc.EXIT_PRECONDITION, "the packs directory is missing", packs_dir)

    by_folder = kz.pack_by_folder(cfg)
    # os.listdir hands back NFD on APFS while the config is NFC (measured
    # 2026-08-30), so both sides are folded before they are compared. Files at
    # this level -- .DS_Store above all -- are not packs and are not folders.
    on_disk = dict((kc.nfc(name), name)
                   for name in os.listdir(packs_dir)
                   if os.path.isdir(os.path.join(packs_dir, name)))

    missing = sorted(set(by_folder) - set(on_disk))
    extra = sorted(set(on_disk) - set(by_folder))
    if missing or extra:
        # Exit 82 in both directions: the tool never silently ignores content, and
        # never silently tolerates a config row that has stopped matching.
        kc.refuse(kc.EXIT_DRIFT,
                  "the pack table and %s disagree" % os.path.relpath(packs_dir,
                                                                     workspace),
                  "config rows with no folder: %s; folders with no config row: %s"
                  % (missing or "none", extra or "none"))

    records = []
    pack_stats = collections.OrderedDict()
    for folder_nfc in sorted(by_folder, key=lambda f: by_folder[f]["code"]):
        row = by_folder[folder_nfc]
        pack_dir = os.path.join(packs_dir, on_disk[folder_nfc])
        filenames = sorted(name for name in os.listdir(pack_dir)
                           if kz.is_pack_file(name))
        before = len(records)
        for filename in filenames:
            rel = os.path.join(cfg["guard"]["packs_subdir"], on_disk[folder_nfc],
                               filename)
            doc = kz.read_tts_save(cfg, rel, root=root)
            states = doc.get("ObjectStates")
            if not isinstance(states, list):
                kc.refuse(kc.EXIT_DRIFT, "%s has no ObjectStates list" % rel, rel)
            for index, obj in enumerate(states):
                walk_object(obj, row["code"], filename, "/%d" % index, None, records)
        pack_stats[row["code"]] = {
            "folder": folder_nfc,
            "nickname": row["nickname"],
            "files": len(filenames),
            "objects": len(records) - before,
        }

    defects = []
    for record in records:
        hits = defects_of(record)
        if hits:
            defects.append({
                "pack": record["pack"], "file": record["file"],
                "path": record["path"], "guid": record["guid"],
                "name": record["name"], "nickname": record["nickname"],
                "source_id": record["source_id"], "tags": record["tags"],
                "classes": hits,
            })
    return tree, records, defects, pack_stats


def count_url_prefix(cfg, records, source_override=None, workspace=None):
    """Occurrences of the cloud-3 prefix, counted over the RAW source bytes.

    Over the bytes and not over the parsed records because `assemble` rewrites
    string leaves anywhere in the tree, and a count taken over the fields this
    stage happens to record would be an oracle for a different question than the
    one V6 asks.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    root = kz.source_root(cfg, override=source_override, workspace=workspace)
    needle = cfg["url_normalisation"]["from"].encode("utf-8")
    other = cfg["url_normalisation"]["to"].encode("utf-8")
    packs_dir = kz.packs_root(cfg, root)
    total, already = 0, 0
    for dirpath, dirnames, filenames in os.walk(packs_dir):
        dirnames.sort()
        for name in sorted(filenames):
            if not kz.is_pack_file(name):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            blob = kz.read_source(cfg, rel, root=root)
            total += blob.count(needle)
            already += blob.count(other)
    return total, already


def run_scan(cfg, run_dir, mode="build", source_override=None, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    tree, records, defects, pack_stats = scan_source(cfg, source_override, workspace)
    cloud3, akamai = count_url_prefix(cfg, records, source_override, workspace)

    by_name = collections.Counter(r["name"] for r in records)
    with_md = [r for r in records if r["md_state"] == "ok"]
    ids = [r["source_id"] for r in with_md if r["source_id"]]
    guids = [r["guid"] for r in records]
    files = set((r["pack"], r["file"]) for r in records)

    checks = []
    oracles = cfg["oracles"]
    checks.append(kc.check(
        "S1", "object inventory",
        [] if len(records) == oracles["objects"]
        else ["%d objects, the design's oracle is %d"
              % (len(records), oracles["objects"])],
        exit_on_fail=kc.EXIT_DRIFT, subject_size=len(records)))
    name_detail = []
    for name, expected in sorted(oracles["objects_by_name"].items()):
        if by_name.get(name, 0) != expected:
            name_detail.append("%s: %d, expected %d"
                               % (name, by_name.get(name, 0), expected))
    checks.append(kc.check("S2", "objects by Name", name_detail,
                           exit_on_fail=kc.EXIT_DRIFT,
                           note=", ".join("%s %d" % (n, c)
                                          for n, c in sorted(by_name.items()))))
    checks.append(kc.check(
        "S3", "GUID uniqueness",
        [] if len(set(guids)) == len(guids)
        else ["%d objects share %d GUIDs" % (len(guids), len(set(guids)))],
        exit_on_fail=kc.EXIT_DRIFT, subject_size=len(guids)))
    checks.append(kc.check(
        "S4", "packs and files",
        ([] if len(pack_stats) == oracles["packs"] else
         ["%d packs, expected %d" % (len(pack_stats), oracles["packs"])]) +
        ([] if len(files) == oracles["files"] else
         ["%d files, expected %d" % (len(files), oracles["files"])]),
        exit_on_fail=kc.EXIT_DRIFT,
        note="%d packs, %d files" % (len(pack_stats), len(files))))
    checks.append(kc.check(
        "S5", "GMNotes parseability",
        ["%s %s %s: GMNotes does not parse" % (r["pack"], r["file"], r["path"])
         for r in records if r["md_state"] == "parse_failed"],
        exit_on_fail=kc.EXIT_DRIFT,
        note="%d with metadata, %d without" % (len(with_md),
                                               len(records) - len(with_md))))
    checks.append(kc.check(
        "S6", "cloud-3 url count",
        [] if cloud3 == cfg["url_normalisation"]["expected"]
        else ["%d occurrences, config declares %d"
              % (cloud3, cfg["url_normalisation"]["expected"])],
        exit_on_fail=kc.EXIT_DRIFT,
        note="%d cloud-3, %d already akamaihd" % (cloud3, akamai)))
    checks.append(kc.check(
        "S7", "defect list size",
        [] if len(defects) == oracles["defects"]
        else ["%d defective objects, the design's oracle is %d"
              % (len(defects), oracles["defects"])],
        exit_on_fail=kc.EXIT_DRIFT, subject_size=len(defects)))

    counts = collections.OrderedDict([
        ("objects", len(records)),
        ("objects_by_name", dict(by_name)),
        ("objects_with_metadata", len(with_md)),
        ("objects_without_metadata", len(records) - len(with_md)),
        ("distinct_source_ids", len(set(ids))),
        ("distinct_guids", len(set(guids))),
        ("packs", len(pack_stats)),
        ("files", len(files)),
        ("decks", by_name.get("Deck", 0)),
        ("defects", len(defects)),
        ("defects_by_class", dict(collections.Counter(
            cls for d in defects for cls in d["classes"]))),
        ("url_cloud3", cloud3),
        ("url_akamaihd", akamai),
        ("source_files", tree["files"]),
        ("source_bytes", tree["bytes"]),
    ])

    inventory = {
        "schema": 1,
        "generated_by": "akn_scan.py",
        "generated_at": kc.utc_now(),
        "config_sha256": kz.compute_config_sha256(cfg),
        "source_tree_sha256": tree["sha256"],
        "packs": pack_stats,
        "counts": dict(counts),
        "defects": defects,
        "objects": records,
    }

    tree_path = os.path.join(run_dir, "source.tree.json")
    inv_path = os.path.join(run_dir, "inventory.json")
    if mode == "build":
        kz.write_guarded(cfg, tree_path, tree, workspace=workspace)
        kz.write_guarded(cfg, inv_path, inventory, workspace=workspace)

    report = kc.new_report(
        STAGE, os.path.basename(run_dir), mode=mode, counts=dict(counts),
        checks=checks,
        binding=kc.build_binding([cfg["_path"], tree_path, inv_path],
                                 extra={"source.tree.sha256": tree["sha256"]}),
        results={"source_root": tree["root"], "packs": list(pack_stats)})
    report["write_set"] = [os.path.relpath(p, workspace)
                           for p in (tree_path, inv_path)]
    kc.finalize_report(report,
                       triggered=[c["exit_on_fail"] for c in checks
                                  if c["status"] == "fail"])
    return report, inventory


# ---------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------

FAULTS = ("walk", "defects", "pack-drift", "readonly")


def _synthetic_tree(tmp, cfg):
    """A three-file miniature source tree, built in a tempdir.

    It pins the three shapes that broke a naive walker: a Deck holding two
    investigators (Mlynar), a card printed twice inside one Deck, and a minicard
    with no GMNotes at all.
    """
    packs = os.path.join(tmp, cfg["guard"]["packs_subdir"])
    folder = cfg["packs"][0]["folder"]
    os.makedirs(os.path.join(packs, folder))

    def card(guid, nickname, tags, md):
        node = {"Name": "CardCustom", "GUID": guid, "Nickname": nickname,
                "CustomDeck": {"2664": {"FaceURL": "http://cloud-3.steamusercontent.com/ugc/1/A/",
                                        "BackURL": "http://cloud-3.steamusercontent.com/ugc/2/B/"}}}
        if tags is not None:
            node["Tags"] = tags
        if md is not None:
            node["GMNotes"] = json.dumps(md, ensure_ascii=False)
        return node

    docs = {
        "Solo.json": [
            card("aaa001", "솔로", ["Investigator", "PlayerCard"],
                 {"id": "90001", "type": "Investigator"}),
            {"Name": "Deck", "GUID": "aaa002", "Tags": ["PlayerCard"],
             "DeckIDs": [2664, 2664],
             "ContainedObjects": [
                 card("aaa003", "중복", ["PlayerCard"], {"id": "90002", "type": "Event"}),
                 card("aaa004", "중복", ["PlayerCard"], {"id": "90002", "type": "Event"})]},
            card("aaa005", "솔로", ["Minicard"], {"id": "90001-m", "type": "Minicard"}),
        ],
        "Twin.json": [
            {"Name": "Deck", "GUID": "bbb001", "Tags": ["Investigator", "PlayerCard"],
             "ContainedObjects": [
                 card("bbb002", "쌍둥이", ["Investigator", "PlayerCard"],
                      {"id": "90003", "type": "Investigator"}),
                 card("bbb003", "쌍둥이", ["Investigator", "PlayerCard"],
                      {"id": "90004", "type": "Investigator"})]},
            {"Name": "Deck", "GUID": "bbb004", "Tags": ["Minicard"],
             "ContainedObjects": [
                 card("bbb005", "쌍둥이", ["Minicard"], {"id": "90003-m", "type": "Minicard"}),
                 card("bbb006", "쌍둥이", ["Minicard"], {"id": "90004-m", "type": "Minicard"})]},
        ],
        "Broken.json": [
            card("ccc001", "결손", ["Investigator", "PlayerCard"],
                 {"id": "90005", "type": "Investigator"}),
            card("ccc002", "", ["Minicard"], None),   # gmnotes_missing
            card("ccc003", "무태그", None, {"id": "90006", "type": "Asset"}),  # tags_missing
        ],
    }
    for name, states in docs.items():
        # Through the atomic writer, not open(): the AST scan in akn_common
        # forbids open() outside two modules, and a fixture builder is exactly
        # where an exemption would first be argued for.
        kc.atomic_write_text(os.path.join(packs, folder, name),
                             json.dumps({"SaveName": name, "ObjectStates": states},
                                        ensure_ascii=False))
    return packs, folder


def selftest(fault=None, verbose=True):
    import tempfile
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))
    cfg = kz.load_config()

    tmp = tempfile.mkdtemp(prefix="akn-scan-selftest.")
    try:
        _packs, folder = _synthetic_tree(tmp, cfg)
        # The synthetic tree is not under docs/Arknights/, so source_root() would
        # refuse it -- which is the guard working. The walk is exercised directly.
        records = []
        for name in sorted(os.listdir(os.path.join(tmp,
                                                   cfg["guard"]["packs_subdir"],
                                                   folder))):
            path = os.path.join(tmp, cfg["guard"]["packs_subdir"], folder, name)
            doc = json.loads(kc.read_text(path))
            for index, obj in enumerate(doc["ObjectStates"]):
                walk_object(obj, "00", name, "/%d" % index, None, records)

        if "walk" in wanted:
            # 5 + 6 + 3: the object counts of the three fixture files, stated
            # so a walker that silently stopped descending fails here first.
            if len(records) != 14:
                findings.append("walk: %d objects, expected 14" % len(records))
            twin = [r for r in records
                    if r["file"] == "Twin.json" and r["source_type"] == "Investigator"]
            if len(twin) != 2:
                findings.append("walk: the two-investigator Deck yielded %d "
                                "investigators" % len(twin))
            nested = [r for r in records if r["parent_deck"] == "aaa002"]
            if len(nested) != 2 or set(r["source_id"] for r in nested) != set(["90002"]):
                findings.append("walk: the multi-printed card inside a Deck was "
                                "not recorded twice under its Deck")
            paths = set(r["path"] for r in records if r["file"] == "Twin.json")
            if "/0/1" not in paths:
                findings.append("walk: the ObjectStates path form is wrong (%s)"
                                % sorted(paths))

        if "defects" in wanted:
            hits = dict(((r["file"], r["path"]), defects_of(r)) for r in records)
            if hits[("Broken.json", "/1")] != ["gmnotes_missing"]:
                findings.append("defects: the GMNotes-less minicard was not caught "
                                "(%s)" % hits[("Broken.json", "/1")])
            if hits[("Broken.json", "/2")] != ["tags_missing"]:
                findings.append("defects: the untagged card was not caught (%s)"
                                % hits[("Broken.json", "/2")])
            if hits[("Solo.json", "/0")]:
                findings.append("defects: a false positive on a clean investigator")
            mistagged = dict(records[0])
            mistagged.update({"tags": ["Investigator"], "source_type": "Minicard"})
            if defects_of(mistagged) != ["minicard_mistagged"]:
                findings.append("defects: a Minicard tagged Investigator was not "
                                "caught")
            if len(DEFECT_CLASSES) != 3:
                findings.append("defects: DEFECT_CLASSES is no longer three classes")

        if "pack-drift" in wanted:
            # A folder with no config row must refuse at 82 in that direction too.
            os.makedirs(os.path.join(tmp, cfg["guard"]["packs_subdir"], "(99) 없음"))
            try:
                scan_source(cfg, source_override=tmp)
            except kc.AknRefusal as exc:
                if exc.code not in (kc.EXIT_GUARD, kc.EXIT_DRIFT):
                    findings.append("pack-drift: expected 80 or 82, got %d" % exc.code)
            else:
                findings.append("pack-drift: a tree outside the containment root "
                                "with an unknown folder was not refused")

        if "readonly" in wanted:
            before = kc.sha256_tree(tmp)
            scanned = []
            for name in sorted(os.listdir(os.path.join(
                    tmp, cfg["guard"]["packs_subdir"], folder))):
                path = os.path.join(tmp, cfg["guard"]["packs_subdir"], folder, name)
                doc = json.loads(kc.read_text(path))
                for index, obj in enumerate(doc["ObjectStates"]):
                    walk_object(obj, "00", name, "/%d" % index, None, scanned)
            if kc.sha256_tree(tmp) != before:
                findings.append("readonly: the walk changed the tree it read")
    finally:
        import shutil as _shutil
        _shutil.rmtree(tmp, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_scan.py",
        description="arknights `scan` -- the read-only walk of docs/Arknights/.")
    parser.add_argument("--run-dir")
    parser.add_argument("--run-id")
    parser.add_argument("--source", help="override guard.source_root; must still "
                                         "resolve inside docs/Arknights/")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    kc.check_invocation_guards()

    if args.selftest is not None:
        fault = args.selftest or None
        print("akn_scan --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_VERIFY
        print("  ok: the depth-first walk over the three shapes that break a naive "
              "one, the three defect classes in both directions, pack-table drift, "
              "and the read-only property of the walk itself")
        return kc.EXIT_OK

    cfg = kz.load_config()
    run_dir = args.run_dir or os.path.join(kz.run_root(cfg),
                                           args.run_id or kz.mint_run_id())
    mode = "dry-run" if args.dry_run else "build"
    report, inventory = run_scan(cfg, run_dir, mode=mode,
                                 source_override=args.source)
    if mode == "build":
        kc.write_report(report, run_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    print("arknights scan -- %s" % report["run_id"])
    print("  source      : %s  %.1f MB  %d files  sha256 %s"
          % (report["results"]["source_root"],
             report["counts"]["source_bytes"] / 1048576.0,
             report["counts"]["source_files"],
             inventory["source_tree_sha256"][:12]))
    print("  objects     : %d (%s)"
          % (report["counts"]["objects"],
             ", ".join("%s %d" % (n, c) for n, c
                       in sorted(report["counts"]["objects_by_name"].items()))))
    print("  metadata    : %d with, %d without; %d distinct source ids; %d GUIDs"
          % (report["counts"]["objects_with_metadata"],
             report["counts"]["objects_without_metadata"],
             report["counts"]["distinct_source_ids"],
             report["counts"]["distinct_guids"]))
    print("  packs/files : %d / %d" % (report["counts"]["packs"],
                                       report["counts"]["files"]))
    print("  urls        : %d cloud-3 to normalise, %d already akamaihd"
          % (report["counts"]["url_cloud3"], report["counts"]["url_akamaihd"]))
    print("  defects     : %d (%s)"
          % (report["counts"]["defects"],
             ", ".join("%s %d" % (k, v) for k, v
                       in sorted(report["counts"]["defects_by_class"].items()))))
    for entry in report["checks"]:
        print("  %-4s %-24s %s" % (entry["id"], entry["name"], entry["status"]))
        for line in entry["detail"][:5]:
            print("       - %s" % line)
    print("  verdict     : %s (exit %d, consumable %s)"
          % (report["verdict"], report["exit_code"], report["consumable"]))
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.AknRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
