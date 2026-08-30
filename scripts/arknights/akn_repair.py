#!/usr/bin/python3
"""arknights `repair` -- the patch set (design section 5.3). Owns exit 84.

PURELY CONFIG-DRIVEN. This module contains no card names, no ids and no Korean
strings: every one of them is a row in data/arknights.packs.json, and the two
implemented rules are the only shapes a row may take.

IT EMITS; IT DOES NOT APPLY. `assemble` applies. That separation is what makes
repairs.json reviewable as a diff before anything is built -- 22 objects with a
before and an after each, rather than a 600 KB output file to eyeball.

BOTH DIRECTIONS ARE ENFORCED, ALWAYS. An object in scan's defect list with no
matching row is exit 84 naming the object; a row matching no object is exit 84
naming the row. The second direction is the one that rots silently: a repair
suite whose rows have stopped matching still reports green, and every run after
that is unprotected.

THE DEFECT LIST AND THE ROW SET ARE NOT THE SAME SIZE, and that is by design.
scan sees 21 objects through three structural classes; the table repairs 22. The
extra is R-f -- Zima.json/0, whose GMNotes is a byte-identical copy of another
card's blob. It parses, it is internally consistent, and no property of its own
shape distinguishes it, so it can only be named by a config row. The rule is
therefore "every defect is covered", not "every row covers a defect".
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

STAGE = "repair"


def object_index(records):
    return dict(((r["pack"], r["file"], r["path"]), r) for r in records)


def idmap_by_object(idmap):
    """{(pack,file,path): (new_id, entry)} -- what `renumber` decided, per object."""
    out = {}
    for new_id, entry in idmap["ids"].items():
        for obj in entry["objects"]:
            out[(entry["pack"], obj["file"], obj["path"])] = (new_id, entry)
    return out


def investigator_in_file(idmap, pack, filename):
    """The single investigator card in one source file, or None when not exactly one."""
    hits = []
    for new_id, entry in idmap["ids"].items():
        if entry["pack"] != pack or entry["role"] != "investigator":
            continue
        if any(obj["file"] == filename for obj in entry["objects"]):
            hits.append((new_id, entry))
    return hits[0] if len(hits) == 1 else None


def before_of(record, field):
    """The pre-patch value, with `absent` and `[]` kept distinct.

    Tags is *absent* on some objects and *empty* on others; both are repaired the
    same way, but recording them the same way would lose the only evidence of
    which shape the source actually had.
    """
    if field == "Tags":
        return {"state": "absent" if record["tags"] is None else "present",
                "value": record["tags"]}
    if field == "GMNotes":
        return {"state": record["md_state"], "value": record["md"]}
    return {"state": "present", "value": record.get(field.lower())}


def patch_for(cfg, row, target, record, idmap, by_object):
    """The (field -> after) mapping one target receives. Refuses at 84."""
    fields = target.get("fields", row.get("fields")) or {}
    after = collections.OrderedDict()

    if row["rule"] == "minicard_from_investigator":
        inv = investigator_in_file(idmap, target["pack"], target["file"])
        if inv is None:
            kc.refuse(kc.EXIT_REPAIR,
                      "%s: %s %s holds no single investigator to derive from"
                      % (row["id"], target["pack"], target["file"]),
                      "the rule authors GMNotes {id: <investigator>-m, type: "
                      "Minicard} and copies the Nickname, so exactly one is required")
        inv_id, inv_entry = inv
        after["GMNotes"] = collections.OrderedDict([
            ("id", kc.get_mini_id(inv_id)), ("type", "Minicard")])
        after["Nickname"] = inv_entry["nickname"]

    for field, value in fields.items():
        after[field] = value

    # A GMNotes blob transcribed into the config carries the id the design wrote
    # down. Keeping it is what lets an auditor read the row against the design
    # verbatim; ASSERTING it is what stops the two from drifting apart silently,
    # because `assemble` rewrites the id from the map either way.
    if isinstance(after.get("GMNotes"), dict) and after["GMNotes"].get("id"):
        hit = by_object.get((target["pack"], target["file"], target["path"]))
        if hit is None:
            kc.refuse(kc.EXIT_REPAIR,
                      "%s: %s %s carries no entry in the id map"
                      % (row["id"], target["file"], target["path"]),
                      "every repaired object must have been renumbered")
        expected = hit[0]
        if after["GMNotes"]["id"] != expected:
            kc.refuse(kc.EXIT_REPAIR,
                      "%s: the config's GMNotes id %r disagrees with the id map's %r"
                      % (row["id"], after["GMNotes"]["id"], expected),
                      "%s %s. Transcribe the design's blob verbatim and let this "
                      "assertion catch a renumbering that has moved under it."
                      % (target["file"], target["path"]))
    return after


def build_repairs(cfg, inventory, idmap):
    """Resolve every row, in both directions. Returns (rows, patches)."""
    records = inventory["objects"]
    by_key = object_index(records)
    by_object = idmap_by_object(idmap)

    patches, rows, unmatched, covered = [], [], [], set()
    for row in cfg["repairs"]:
        row_targets = []
        for target in row["targets"]:
            key = (target["pack"], target["file"], target["path"])
            record = by_key.get(key)
            if record is None:
                unmatched.append("%s -> %s %s %s matches no object"
                                 % (row["id"], target["pack"], target["file"],
                                    target["path"]))
                continue
            after = patch_for(cfg, row, target, record, idmap, by_object)
            entry = collections.OrderedDict([
                ("row_id", row["id"]),
                ("rule", row["rule"]),
                ("pack", target["pack"]),
                ("file", target["file"]),
                ("path", target["path"]),
                ("guid", record["guid"]),
                ("nickname", record["nickname"]),
                ("fields", [collections.OrderedDict([
                    ("field", field),
                    ("before", before_of(record, field)),
                    ("after", value)]) for field, value in after.items()]),
                ("evidence", target.get("why") or row["evidence"]),
            ])
            patches.append(entry)
            row_targets.append(entry)
            covered.add(key)
        rows.append(collections.OrderedDict([
            ("id", row["id"]), ("rule", row["rule"]),
            ("defect_class", row.get("defect_class")),
            ("targets", len(row["targets"])),
            ("resolved", len(row_targets)),
            ("status", "pass" if len(row_targets) == len(row["targets"])
             else "fail"),
        ]))
    if unmatched:
        kc.refuse(kc.EXIT_REPAIR,
                  "%d repair target(s) match no object" % len(unmatched),
                  "; ".join(unmatched[:8]) + " -- a row that has stopped matching "
                  "is how a repair suite rots")

    orphans = [d for d in inventory["defects"]
               if (d["pack"], d["file"], d["path"]) not in covered]
    if orphans:
        kc.refuse(kc.EXIT_REPAIR,
                  "%d defective object(s) have no repair row" % len(orphans),
                  "; ".join("%s %s %s (%s)" % (d["pack"], d["file"], d["path"],
                                               ",".join(d["classes"]))
                            for d in orphans[:8]))
    return rows, patches


def run_repair(cfg, run_dir, mode="build", workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    kz.assert_predecessors(run_dir, STAGE, workspace)
    inv_path = os.path.join(run_dir, "inventory.json")
    map_path = os.path.join(run_dir, "idmap.json")
    for path in (inv_path, map_path):
        if not os.path.exists(path):
            kc.refuse(kc.EXIT_PRECONDITION, "%s is missing" % os.path.basename(path),
                      path)
    inventory = json.loads(kc.read_text(inv_path))
    idmap = json.loads(kc.read_text(map_path))

    rows, patches = build_repairs(cfg, inventory, idmap)

    checks = []
    checks.append(kc.check(
        "P1", "every row resolved",
        ["%s: %d of %d targets" % (r["id"], r["resolved"], r["targets"])
         for r in rows if r["status"] != "pass"],
        exit_on_fail=kc.EXIT_REPAIR, subject_size=len(rows)))
    covered = set((p["pack"], p["file"], p["path"]) for p in patches)
    checks.append(kc.check(
        "P2", "every defect covered",
        ["%s %s %s" % (d["pack"], d["file"], d["path"])
         for d in inventory["defects"]
         if (d["pack"], d["file"], d["path"]) not in covered],
        exit_on_fail=kc.EXIT_REPAIR, subject_size=len(inventory["defects"])))
    checks.append(kc.check(
        "P3", "no object patched twice",
        ["%s %s %s appears in %d rows" % (k[0], k[1], k[2], n)
         for k, n in sorted(collections.Counter(
             (p["pack"], p["file"], p["path"]) for p in patches).items()) if n > 1],
        exit_on_fail=kc.EXIT_REPAIR, subject_size=len(patches)))
    # THE ROLE TABLE AND THE REPAIRED METADATA MUST AGREE, on both Tags and
    # GMNotes.type. This is what protects the one row the defect list cannot:
    # R-f's object is a defect only because its (parsing, self-consistent) blob
    # belongs to another card, so P2 cannot see it -- but `renumber` has already
    # ruled the object an investigator via a role_override, and an investigator
    # whose GMNotes still says `type: Asset` is a contradiction this can see.
    # Delete R-f and P4 fails naming Zima.json/0; delete O1 as well and
    # `renumber` refuses first. Evaluated here rather than only in verify so a
    # row that patched the wrong field never reaches the assembled bag.
    role_detail = []
    patch_by_key = dict(((p["pack"], p["file"], p["path"]), p) for p in patches)
    records_by_key = object_index(inventory["objects"])
    for entry in idmap["ids"].values():
        want = {"investigator": "Investigator", "minicard": "Minicard"}.get(
            entry["role"])
        if not want:
            continue
        for obj in entry["objects"]:
            key = (entry["pack"], obj["file"], obj["path"])
            record = records_by_key[key]
            tags = list(record["tags"] or [])
            md = record["md"]
            patch = patch_by_key.get(key)
            for field in (patch or {}).get("fields", []):
                if field["field"] == "Tags":
                    tags = list(field["after"])
                elif field["field"] == "GMNotes":
                    md = field["after"]
            if want not in tags:
                role_detail.append("%s %s %s is a %s but ends up tagged %r"
                                   % (key[0], key[1], key[2], entry["role"], tags))
            if (md or {}).get("type") != want:
                role_detail.append("%s %s %s is a %s but its GMNotes ends up "
                                   "type %r" % (key[0], key[1], key[2],
                                                entry["role"], (md or {}).get("type")))
    checks.append(kc.check("P4", "roles agree with tags and GMNotes.type",
                           role_detail, exit_on_fail=kc.EXIT_REPAIR))

    counts = collections.OrderedDict([
        ("rows", len(rows)),
        ("patched_objects", len(patches)),
        ("patched_fields", sum(len(p["fields"]) for p in patches)),
        ("defects", len(inventory["defects"])),
        ("rows_beyond_the_defect_list",
         sum(1 for p in patches
             if (p["pack"], p["file"], p["path"])
             not in set((d["pack"], d["file"], d["path"])
                        for d in inventory["defects"]))),
    ])

    repairs = collections.OrderedDict([
        ("schema", 1),
        ("generated_by", "akn_repair.py"),
        ("config_sha256", kz.compute_config_sha256(cfg)),
        ("counts", dict(counts)),
        ("rows", rows),
        ("patches", patches),
    ])

    out_path = os.path.join(run_dir, "repairs.json")
    triggered = [c["exit_on_fail"] for c in checks if c["status"] == "fail"]
    if mode == "build" and not triggered:
        kz.write_guarded(cfg, out_path, repairs, workspace=workspace)

    report = kc.new_report(
        STAGE, os.path.basename(run_dir), mode=mode, counts=dict(counts),
        checks=checks,
        binding=kc.build_binding([cfg["_path"], inv_path, map_path, out_path]),
        results={"repairs": os.path.relpath(out_path, workspace)})
    report["write_set"] = [os.path.relpath(out_path, workspace)]
    kc.finalize_report(report, triggered=triggered)
    return report, repairs


# ---------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------

FAULTS = ("orphan-object", "orphan-row", "minicard-rule", "id-disagreement",
          "before-shape")


def _mini_fixture():
    inventory = {
        "counts": {"objects_with_metadata": 2},
        "defects": [{"pack": "03", "file": "C.json", "path": "/2", "guid": "g2",
                     "name": "CardCustom", "nickname": "", "source_id": None,
                     "tags": ["Minicard"], "classes": ["gmnotes_missing"]}],
        "objects": [
            {"pack": "03", "file": "C.json", "path": "/0", "name": "CardCustom",
             "nickname": "케오베", "description": "", "tags": ["Investigator",
                                                             "PlayerCard"],
             "guid": "g0", "md_state": "ok",
             "md": {"id": "03004", "type": "Investigator"},
             "source_id": "03004", "source_type": "Investigator"},
            {"pack": "03", "file": "C.json", "path": "/2", "name": "CardCustom",
             "nickname": "", "description": "", "tags": ["Minicard"], "guid": "g2",
             "md_state": "absent", "md": None, "source_id": None,
             "source_type": None},
        ],
    }
    idmap = {"ids": {
        "akn03004": {"pack": "03", "role": "investigator", "nickname": "케오베",
                     "objects": [{"file": "C.json", "path": "/0", "guid": "g0",
                                  "name": "CardCustom"}]},
        "akn03004-m": {"pack": "03", "role": "minicard", "nickname": "",
                       "objects": [{"file": "C.json", "path": "/2", "guid": "g2",
                                    "name": "CardCustom"}]},
    }}
    return inventory, idmap


def _cfg_with(cfg, repairs):
    local = json.loads(json.dumps({k: v for k, v in cfg.items()
                                   if not k.startswith("_")}))
    local["_path"] = cfg["_path"]
    local["repairs"] = repairs
    return local


def selftest(fault=None, verbose=True):
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))
    cfg = kz.load_config()
    inventory, idmap = _mini_fixture()

    def fires(label, code, fn):
        try:
            fn()
        except kc.AknRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d (%s)"
                                % (label, code, exc.code, exc.message))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    good = [{"id": "R-t", "rule": "minicard_from_investigator",
             "defect_class": "gmnotes_missing", "fields": {"Tags": ["Minicard"]},
             "evidence": "test",
             "targets": [{"pack": "03", "file": "C.json", "path": "/2"}]}]

    if "orphan-object" in wanted:
        fires("a defect with no row", kc.EXIT_REPAIR,
              lambda: build_repairs(_cfg_with(cfg, []), inventory, idmap))

    if "orphan-row" in wanted:
        bogus = good + [{"id": "R-x", "rule": "set_fields", "defect_class": None,
                         "fields": {"Tags": ["PlayerCard"]}, "evidence": "test",
                         "targets": [{"pack": "03", "file": "Nope.json",
                                      "path": "/9"}]}]
        fires("a row with no object", kc.EXIT_REPAIR,
              lambda: build_repairs(_cfg_with(cfg, bogus), inventory, idmap))

    if "minicard-rule" in wanted:
        _rows, patches = build_repairs(_cfg_with(cfg, good), inventory, idmap)
        after = dict((f["field"], f["after"]) for f in patches[0]["fields"])
        if after.get("GMNotes") != {"id": "akn03004-m", "type": "Minicard"}:
            findings.append("minicard-rule: authored %r" % (after.get("GMNotes"),))
        if after.get("Nickname") != "케오베":
            findings.append("minicard-rule: Nickname %r, expected the "
                            "investigator's" % after.get("Nickname"))
        if after.get("Tags") != ["Minicard"]:
            findings.append("minicard-rule: Tags %r" % (after.get("Tags"),))
        # Two investigators in the file makes the derivation ambiguous.
        two = json.loads(json.dumps(idmap))
        two["ids"]["akn03009"] = {"pack": "03", "role": "investigator",
                                  "nickname": "다른",
                                  "objects": [{"file": "C.json", "path": "/1",
                                               "guid": "g1", "name": "CardCustom"}]}
        fires("minicard-rule (two investigators)", kc.EXIT_REPAIR,
              lambda: build_repairs(_cfg_with(cfg, good), inventory, two))

    if "id-disagreement" in wanted:
        drifted = [{"id": "R-y", "rule": "set_fields", "defect_class": None,
                    "evidence": "test",
                    "fields": {"GMNotes": {"id": "akn99999", "type": "Minicard"}},
                    "targets": [{"pack": "03", "file": "C.json", "path": "/2"}]}]
        fires("a config GMNotes id the map disagrees with", kc.EXIT_REPAIR,
              lambda: build_repairs(_cfg_with(cfg, drifted), inventory, idmap))

    if "before-shape" in wanted:
        absent = {"tags": None, "md_state": "absent", "md": None}
        empty = {"tags": [], "md_state": "absent", "md": None}
        if before_of(absent, "Tags")["state"] != "absent":
            findings.append("before-shape: an absent Tags was not recorded as absent")
        if before_of(empty, "Tags")["state"] != "present":
            findings.append("before-shape: an empty Tags was recorded as absent -- "
                            "the two shapes must stay distinguishable")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_repair.py",
        description="arknights `repair` -- the config-driven patch set. Emits; "
                    "never applies.")
    parser.add_argument("--run-dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    kc.check_invocation_guards()

    if args.selftest is not None:
        fault = args.selftest or None
        print("akn_repair --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_VERIFY
        print("  ok: both resolution directions refuse at 84, the "
              "minicard_from_investigator rule and its ambiguity guard, the "
              "config-vs-idmap id assertion, and the absent/empty Tags distinction")
        return kc.EXIT_OK

    if not args.run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    cfg = kz.load_config()
    mode = "dry-run" if args.dry_run else "build"
    report, repairs = run_repair(cfg, args.run_dir, mode=mode)
    if mode == "build":
        kc.write_report(report, args.run_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    print("arknights repair -- %s" % report["run_id"])
    print("  rows        : %d covering %d object(s), %d field patch(es)"
          % (report["counts"]["rows"], report["counts"]["patched_objects"],
             report["counts"]["patched_fields"]))
    print("  defects     : %d, all covered; %d row(s) beyond the defect list"
          % (report["counts"]["defects"],
             report["counts"]["rows_beyond_the_defect_list"]))
    for row in repairs["rows"]:
        print("    %-4s %-28s %d/%d target(s)  %s"
              % (row["id"], row["rule"], row["resolved"], row["targets"],
                 row["status"]))
    for entry in report["checks"]:
        print("  %-4s %-32s %s" % (entry["id"], entry["name"], entry["status"]))
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
