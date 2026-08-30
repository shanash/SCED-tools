#!/usr/bin/python3
"""arknights `renumber` -- id assignment (design section 5.2). Owns exit 83.

Every source id is DISCARDED and every card is reassigned into the dash-free
8-character `akn<pack><ordinal>` namespace. That is what turns the 232 collisions
with real SCED cards (55.9% of 415 source ids: 01001 = Roland Banks, 01020 =
Machete) from dormant into eliminated, and it takes the two genuine internal
duplicates with it.

THE ORDER OF THE FIVE STEPS IS LOAD-BEARING, and each step exists for a measured
reason:

  1. ROLE OVERRIDES FIRST, so role resolution never reads a field the repair
     table is about to correct. Zima.json/0 says `type: Asset` because its
     GMNotes is a byte-identical copy of another card's.
  2. GROUP INTO CARDS, NOT OBJECTS. Nine of the eleven duplicate source ids are
     the same card printed 2-4 times, and a per-OBJECT assignment breaks all nine
     silently. The grouping key is (pack, base id, is-minicard).
  3. BIND MINICARDS. A minicard's base is its own id minus "-m" -- 86 of the 92.
     The six with no GMNotes at all take their investigator's, and if their file
     does not hold exactly one investigator the stage refuses.
  4. ASSIGN ORDINALS, E-rows first. Injectivity is asserted over DISTINCT BASE
     STRINGS within a pack, which is the domain the design measured: an
     investigator and its own minicard share a base and are not a collision, two
     different cards landing on one ordinal are.
  5. COMPOSE AND ASSERT -- the regex, the dash-free property getMiniId depends
     on, global uniqueness, the investigator/minicard bijection, and that no
     group lost or gained an object.
"""

import argparse
import collections
import json
import os
import re
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

import akn_common as kc  # noqa: E402
import akn_config as kz  # noqa: E402

STAGE = "renumber"

TRAILING_DIGITS = re.compile(r"([0-9]+)$")
ROLE_INVESTIGATOR = "investigator"
ROLE_MINICARD = "minicard"
ROLE_CARD = "card"
ROLE_CONTAINER = "container"


def base_of(source_id):
    """The source id with a trailing -m removed. The id STRING is discarded by
    the scheme; only this derived base and its trailing digits survive."""
    return source_id[:-2] if source_id.endswith("-m") else source_id


def ordinal_of(base):
    """int(trailing digits of base) mod 1000, or None when there are none."""
    match = TRAILING_DIGITS.search(base or "")
    return None if not match else int(match.group(1)) % 1000


class Group(object):
    """One CARD. Many-to-one on objects is the normal case, not the exception."""

    __slots__ = ("key", "pack", "role", "base", "objects", "ordinal", "status",
                 "override", "inherits_from")

    def __init__(self, key, pack, role, base):
        self.key = key
        self.pack = pack
        self.role = role
        self.base = base
        self.objects = []
        self.ordinal = None
        self.status = None
        self.override = None
        self.inherits_from = None

    @property
    def is_minicard(self):
        return self.role == ROLE_MINICARD


def resolve_roles(cfg, records):
    """Step 1 and the role half of step 2. Returns {(pack,file,path): role}."""
    overrides = {}
    for row in cfg["role_overrides"]:
        key = (row["pack"], row["file"], row["path"])
        overrides[key] = row

    roles, unresolved, unmatched = {}, [], dict(overrides)
    for rec in records:
        key = (rec["pack"], rec["file"], rec["path"])
        if key in overrides:
            roles[key] = overrides[key]["role"]
            unmatched.pop(key, None)
            continue
        if rec["name"] == "Deck":
            roles[key] = ROLE_CONTAINER
            continue
        source_type = rec["source_type"]
        tags = rec["tags"] or []
        if source_type == "Investigator":
            roles[key] = ROLE_INVESTIGATOR
        elif source_type == "Minicard":
            roles[key] = ROLE_MINICARD
        elif rec["md_state"] != "ok" and "Minicard" in tags:
            roles[key] = ROLE_MINICARD
        elif rec["md_state"] == "ok":
            roles[key] = ROLE_CARD
        else:
            unresolved.append("%s %s %s (%s, tags=%r, no GMNotes)"
                              % (rec["pack"], rec["file"], rec["path"],
                                 rec["name"], rec["tags"]))
    if unresolved:
        kc.refuse(kc.EXIT_RENUMBER,
                  "%d object(s) have no resolvable role" % len(unresolved),
                  "; ".join(unresolved[:8]) + " -- each needs a role_override row")
    if unmatched:
        kc.refuse(kc.EXIT_RENUMBER,
                  "%d role_override row(s) match no object" % len(unmatched),
                  "; ".join("%s -> %s %s %s" % (r["id"], r["pack"], r["file"],
                                                r["path"])
                            for r in unmatched.values()))
    return roles


def exception_index(cfg, records):
    """Resolve every E-row assignment to its objects. Refuses in BOTH directions.

    Returns (by_object, by_pack_id, rows) where by_object maps a
    (pack,file,path) key to (row_id, ordinal) and by_pack_id maps
    (pack, source_id) to the same.
    """
    by_key = collections.defaultdict(list)
    by_pack_id = collections.defaultdict(list)
    for rec in records:
        by_key[(rec["pack"], rec["file"], rec["path"])].append(rec)
        if rec["source_id"]:
            by_pack_id[(rec["pack"], rec["source_id"])].append(rec)

    object_rows, id_rows, unmatched = {}, {}, []
    for row in cfg["exceptions"]:
        for item in row["assign"]:
            if "source_id" in item:
                hits = by_pack_id.get((row["pack"], item["source_id"]), [])
                if not hits:
                    unmatched.append("%s: source_id %r in pack %s matches no object"
                                     % (row["id"], item["source_id"], row["pack"]))
                    continue
                id_rows[(row["pack"], item["source_id"])] = (row["id"],
                                                             item["ordinal"])
            else:
                key = (row["pack"], item["file"], item["path"])
                hits = by_key.get(key, [])
                if len(hits) != 1:
                    unmatched.append("%s: %s %s in pack %s matches %d objects"
                                     % (row["id"], item["file"], item["path"],
                                        row["pack"], len(hits)))
                    continue
                object_rows[key] = (row["id"], item["ordinal"])
    if unmatched:
        kc.refuse(kc.EXIT_RENUMBER,
                  "%d exception assignment(s) match no object" % len(unmatched),
                  "; ".join(unmatched[:8]))
    return object_rows, id_rows


def build_groups(cfg, records, roles, object_rows, id_rows):
    """Steps 2 and 3. Returns an ordered {key: Group}."""
    groups = collections.OrderedDict()

    def group_for(key, pack, role, base):
        if key not in groups:
            groups[key] = Group(key, pack, role, base)
        return groups[key]

    # First pass: everything that can be keyed from its own fields.
    pending_minicards = []
    for rec in records:
        okey = (rec["pack"], rec["file"], rec["path"])
        role = roles[okey]
        if role == ROLE_CONTAINER:
            continue
        exc = object_rows.get(okey)
        if exc is not None:
            # An E-row naming an object by {file,path} SPLITS it into its own
            # group. That is the whole of E2: without the split, Zima and
            # 네온의 사진 share the base 06006 and would receive one id.
            group = group_for(("E", exc[0]) + okey, rec["pack"], role,
                              base_of(rec["source_id"]) if rec["source_id"] else None)
            group.ordinal, group.override = exc[1], exc[0]
            group.status = "overridden"
            group.objects.append(rec)
            continue
        if rec["source_id"]:
            base = base_of(rec["source_id"])
            prefix = "mini" if role == ROLE_MINICARD else "card"
            group_for((prefix, rec["pack"], base), rec["pack"], role,
                      base).objects.append(rec)
            continue
        if role == ROLE_MINICARD:
            pending_minicards.append(rec)
            continue
        kc.refuse(kc.EXIT_RENUMBER,
                  "%s %s %s has no id and no exception row to supply one"
                  % (rec["pack"], rec["file"], rec["path"]),
                  "a config-supplied key is required where the source has none")

    # Second pass: the minicards with no GMNotes at all. Their base is the single
    # investigator in the same source file; a file with anything but exactly one
    # is exit 83 rather than a guess. (Mlynar.json has two, but both its minicards
    # carry GMNotes, so it never reaches this branch.)
    for rec in pending_minicards:
        siblings = [g for g in groups.values()
                    if g.role == ROLE_INVESTIGATOR
                    and any(o["file"] == rec["file"] and o["pack"] == rec["pack"]
                            for o in g.objects)]
        if len(siblings) != 1:
            kc.refuse(kc.EXIT_RENUMBER,
                      "%s %s %s is a minicard with no id and its file holds %d "
                      "investigators" % (rec["pack"], rec["file"], rec["path"],
                                         len(siblings)),
                      "the base can only be derived when there is exactly one")
        inv = siblings[0]
        group = group_for(("mini-of",) + inv.key, rec["pack"], ROLE_MINICARD,
                          inv.base)
        group.inherits_from = inv.key
        group.objects.append(rec)

    # An id-selector E-row assigns a whole group rather than splitting one.
    for group in groups.values():
        if group.ordinal is not None:
            continue
        for rec in group.objects:
            hit = id_rows.get((rec["pack"], rec["source_id"]))
            if hit:
                group.ordinal, group.override = hit[1], hit[0]
                group.status = "overridden"
                break
    return groups


def assign_ordinals(cfg, groups):
    """Step 4. E-rows are already in place; derive the rest, then inherit."""
    for group in groups.values():
        if group.ordinal is not None:
            continue
        if group.inherits_from is not None:
            continue  # resolved below, once its investigator has an ordinal
        derived = ordinal_of(group.base)
        if derived is None:
            kc.refuse(kc.EXIT_RENUMBER,
                      "no ordinal can be derived for %s" % (group.key,),
                      "base %r has no trailing digits and no exception row"
                      % group.base)
        group.ordinal, group.status = derived, "carried"
    for group in groups.values():
        if group.inherits_from is None:
            continue
        group.ordinal = groups[group.inherits_from].ordinal
        group.status = "absent"

    # Whole-pack exception tables must be exactly that: every group in the pack
    # assigned. A row that has silently stopped covering its pack is how an
    # explicit table decays back into the derived rule it was written to replace.
    for row in cfg["exceptions"]:
        if not row.get("whole_pack"):
            continue
        loose = [g.key for g in groups.values()
                 if g.pack == row["pack"] and g.override != row["id"]
                 and g.inherits_from is None]
        if loose:
            kc.refuse(kc.EXIT_RENUMBER,
                      "%s declares whole_pack but %d group(s) in pack %s are not "
                      "assigned by it" % (row["id"], len(loose), row["pack"]),
                      "; ".join(str(k) for k in loose[:8]))

    # Injectivity, over DISTINCT BASE STRINGS within a pack (design section 3.1).
    # An investigator and its own minicard share a base and are not a collision.
    by_slot = collections.defaultdict(dict)
    for group in groups.values():
        if group.base is None:
            continue
        by_slot[(group.pack, group.ordinal)].setdefault(group.base, []).append(group)
    collisions = []
    for (pack, ordinal), bases in sorted(by_slot.items()):
        if len(bases) < 2:
            continue
        if any(g.override for gs in bases.values() for g in gs):
            continue
        collisions.append(
            "pack %s ordinal %03d is claimed by %s"
            % (pack, ordinal,
               ", ".join("%s (%s)" % (b, ", ".join(
                   "%s%s" % (o["file"], o["path"]) for g in gs for o in g.objects))
                   for b, gs in sorted(bases.items()))))
    if collisions:
        kc.refuse(kc.EXIT_RENUMBER,
                  "%d ordinal collision(s) with no exception row" % len(collisions),
                  "; ".join(collisions[:6]))
    return by_slot


def compose(cfg, groups):
    """Step 5. Compose the ids, then assert every property the scheme rests on."""
    scheme = cfg["id_scheme"]
    pattern = re.compile(scheme["regex"])
    digits = scheme["ordinal_digits"]
    new_ids = {}
    for group in groups.values():
        base_id = "%s%s%0*d" % (scheme["prefix"], group.pack, digits, group.ordinal)
        new_ids[group.key] = (base_id + scheme["minicard_suffix"]
                              if group.is_minicard else base_id)

    findings = []
    seen = {}
    for key, new_id in sorted(new_ids.items(), key=lambda kv: kv[1]):
        stem = (new_id[:-len(scheme["minicard_suffix"])]
                if new_id.endswith(scheme["minicard_suffix"]) else new_id)
        if not pattern.match(stem):
            findings.append("%s does not match %s" % (new_id, scheme["regex"]))
        if len(stem) != 8 or "-" in stem:
            findings.append("%s: the base must be 8 dash-free characters, so "
                            "getMiniId takes its first branch" % new_id)
        if kc.mini_id_branch(stem) != 1:
            findings.append("%s takes getMiniId branch %d, not 1"
                            % (new_id, kc.mini_id_branch(stem)))
        if new_id in seen:
            findings.append("%s is assigned to both %s and %s"
                            % (new_id, seen[new_id], key))
        seen[new_id] = key
    if findings:
        kc.refuse(kc.EXIT_RENUMBER, "%d composed id(s) violate the scheme"
                  % len(findings), "; ".join(findings[:8]))

    investigators = set(new_ids[g.key] for g in groups.values()
                        if g.role == ROLE_INVESTIGATOR)
    minicards = set(new_ids[g.key] for g in groups.values() if g.is_minicard)
    expected = set(kc.get_mini_id(i) for i in investigators)
    orphan_minis = sorted(minicards - expected)
    missing_minis = sorted(expected - minicards)
    if orphan_minis or missing_minis:
        kc.refuse(kc.EXIT_RENUMBER,
                  "the investigator/minicard bijection is broken",
                  "minicards with no investigator: %s; investigators with no "
                  "minicard: %s" % (orphan_minis[:8] or "none",
                                    missing_minis[:8] or "none"))
    return new_ids


def build_idmap(cfg, inventory, groups, new_ids, workspace=None):
    packs = kz.pack_by_code(cfg)
    ids = collections.OrderedDict()
    for key, new_id in sorted(new_ids.items(), key=lambda kv: kv[1]):
        group = groups[key]
        source_ids = sorted(set(o["source_id"] for o in group.objects
                                if o["source_id"]))
        nickname = ""
        for obj in group.objects:
            if obj["nickname"]:
                nickname = obj["nickname"]
                break
        entry = collections.OrderedDict([
            ("pack", group.pack),
            ("pack_folder", packs[group.pack]["folder"]),
            ("ordinal", group.ordinal),
            ("role", group.role),
            ("nickname", nickname),
        ])
        if group.is_minicard:
            entry["base"] = new_id[:-len(cfg["id_scheme"]["minicard_suffix"])]
        entry["source_ids"] = source_ids
        entry["source_id_status"] = group.status
        if group.override:
            entry["override"] = group.override
        entry["objects"] = [collections.OrderedDict([
            ("file", o["file"]), ("path", o["path"]), ("guid", o["guid"]),
            ("name", o["name"])]) for o in group.objects]
        ids[new_id] = entry

    roles = collections.Counter(g.role for g in groups.values())
    mapped = sum(len(g.objects) for g in groups.values())
    counts = collections.OrderedDict([
        ("objects", inventory["counts"]["objects_with_metadata"]),
        ("objects_mapped", mapped),
        ("new_ids", len(new_ids)),
        ("investigators", roles.get(ROLE_INVESTIGATOR, 0)),
        ("minicards", roles.get(ROLE_MINICARD, 0)),
        ("other", roles.get(ROLE_CARD, 0)),
        ("many_to_one_groups", sum(1 for g in groups.values()
                                   if len(g.objects) > 1)),
        ("declared_remainders", cfg["declared_remainders"]),
    ])
    return collections.OrderedDict([
        ("schema", 1),
        ("generated_by", "akn_renumber.py"),
        ("config_sha256", kz.compute_config_sha256(cfg)),
        ("source_tree_sha256", inventory["source_tree_sha256"]),
        ("counts", counts),
        ("ids", ids),
    ])


def idmap_tsv(idmap):
    lines = ["new_id\told_id\tpack\trole\tnickname\tfile\tpath"]
    for new_id, entry in idmap["ids"].items():
        old = ",".join(entry["source_ids"]) or "-"
        for obj in entry["objects"]:
            lines.append("\t".join([new_id, old, entry["pack"], entry["role"],
                                    entry["nickname"], obj["file"], obj["path"]]))
    return "\n".join(lines) + "\n"


def run_renumber(cfg, run_dir, mode="build", workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    kz.assert_predecessors(run_dir, STAGE, workspace)
    inv_path = os.path.join(run_dir, "inventory.json")
    if not os.path.exists(inv_path):
        kc.refuse(kc.EXIT_PRECONDITION, "inventory.json is missing", inv_path)
    inventory = json.loads(kc.read_text(inv_path))
    if inventory["config_sha256"] != kz.compute_config_sha256(cfg):
        kc.refuse(kc.EXIT_DRIFT,
                  "the config moved since `scan` ran",
                  "re-run `arknights.sh scan`; the exception and repair tables "
                  "select objects the inventory was built without")
    records = inventory["objects"]

    roles = resolve_roles(cfg, records)
    object_rows, id_rows = exception_index(cfg, records)
    groups = build_groups(cfg, records, roles, object_rows, id_rows)
    assign_ordinals(cfg, groups)
    new_ids = compose(cfg, groups)
    idmap = build_idmap(cfg, inventory, groups, new_ids, workspace)

    checks = []
    oracles = cfg["oracles"]
    counts = idmap["counts"]
    for cid, name, got, want in (
            ("N1", "new id count", counts["new_ids"], oracles["new_ids"]),
            ("N2", "investigators", counts["investigators"], oracles["investigators"]),
            ("N3", "minicards", counts["minicards"], oracles["minicards"]),
            ("N4", "other cards", counts["other"], oracles["other_ids"])):
        checks.append(kc.check(
            cid, name, [] if got == want else
            ["%d, the design's oracle is %d" % (got, want)],
            exit_on_fail=kc.EXIT_RENUMBER, note="%d" % got))

    # Every object that carries metadata, plus the seven that carry none, must be
    # in exactly one group. A group that lost an object is a card that lost a
    # printing, and nothing downstream would notice.
    mapped = collections.Counter()
    for group in groups.values():
        for obj in group.objects:
            mapped[(obj["pack"], obj["file"], obj["path"])] += 1
    expect = set((r["pack"], r["file"], r["path"]) for r in records
                 if roles[(r["pack"], r["file"], r["path"])] != ROLE_CONTAINER)
    checks.append(kc.check(
        "N5", "object coverage",
        ["%s is in %d groups" % (k, v) for k, v in sorted(mapped.items()) if v != 1]
        + ["%s is in no group" % (k,) for k in sorted(expect - set(mapped))],
        exit_on_fail=kc.EXIT_RENUMBER, subject_size=len(expect)))

    # The nine benign multi-printings, named individually: a count alone cannot
    # distinguish "a02011's four objects share one id" from "some four objects do".
    benign_detail = []
    for row in cfg["many_to_one"]["benign_groups"]:
        hits = [nid for nid, entry in idmap["ids"].items()
                if entry["pack"] == row["pack"]
                and row["source_id"] in entry["source_ids"]]
        if len(hits) != 1:
            benign_detail.append("%s in pack %s maps to %d new ids (%s)"
                                 % (row["source_id"], row["pack"], len(hits), hits))
        elif len(idmap["ids"][hits[0]]["objects"]) != row["objects"]:
            benign_detail.append("%s carries %d objects, config says %d"
                                 % (hits[0],
                                    len(idmap["ids"][hits[0]]["objects"]),
                                    row["objects"]))
    checks.append(kc.check("N6", "benign many-to-one groups", benign_detail,
                           exit_on_fail=kc.EXIT_RENUMBER,
                           subject_size=len(cfg["many_to_one"]["benign_groups"])))

    split_detail = []
    for row in cfg["many_to_one"]["declared_splits"]:
        hits = sorted(nid for nid, entry in idmap["ids"].items()
                      if row["source_id"] in entry["source_ids"])
        if len(hits) < 2:
            split_detail.append("%s was declared a split but maps to %s"
                                % (row["source_id"], hits))
    checks.append(kc.check("N7", "declared splits", split_detail,
                           exit_on_fail=kc.EXIT_RENUMBER,
                           subject_size=len(cfg["many_to_one"]["declared_splits"])))

    run_map = os.path.join(run_dir, "idmap.json")
    tsv_path = os.path.join(run_dir, "idmap.tsv")
    triggered = [c["exit_on_fail"] for c in checks if c["status"] == "fail"]
    if mode == "build" and not triggered:
        kz.write_guarded(cfg, run_map, idmap, workspace=workspace)
        kz.write_guarded(cfg, tsv_path, idmap_tsv(idmap), workspace=workspace)
        # The committed audit artifact. Same bytes as the run copy, so V1 can
        # assert byte-identity and catch a hand-edit of either.
        kz.write_guarded(cfg, kz.IDMAP_PATH, idmap, workspace=workspace)

    report = kc.new_report(
        STAGE, os.path.basename(run_dir), mode=mode, counts=dict(counts),
        checks=checks,
        binding=kc.build_binding([cfg["_path"], inv_path, run_map, kz.IDMAP_PATH]),
        results={"idmap": os.path.relpath(run_map, workspace),
                 "committed_idmap": os.path.relpath(kz.IDMAP_PATH, workspace)})
    report["write_set"] = [os.path.relpath(p, workspace)
                           for p in (run_map, tsv_path, kz.IDMAP_PATH)]
    kc.finalize_report(report, triggered=triggered)
    return report, idmap


# ---------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------

FAULTS = ("ordinal", "grouping", "collision", "bijection", "minicard-base")


def _rec(pack, filename, path, name="CardCustom", tags=None, md=None, nickname=""):
    return {"pack": pack, "file": filename, "path": path, "name": name,
            "nickname": nickname, "description": "", "tags": tags,
            "guid": "%s%s" % (filename[:3].lower(), path.replace("/", "")),
            "md_state": "ok" if md else "absent", "md": md,
            "source_id": (md or {}).get("id"), "source_type": (md or {}).get("type"),
            "parent_deck": None, "custom_deck": [], "card_id": None,
            "deck_ids": None, "children": []}


def selftest(fault=None, verbose=True):
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))
    cfg = kz.load_config()

    def fires(label, code, fn):
        try:
            fn()
        except kc.AknRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d (%s)"
                                % (label, code, exc.code, exc.message))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    def pipeline(records, exceptions=None, overrides=None):
        local = json.loads(json.dumps({k: v for k, v in cfg.items()
                                       if not k.startswith("_")}))
        local["_path"] = cfg["_path"]
        local["exceptions"] = exceptions if exceptions is not None else []
        local["role_overrides"] = overrides if overrides is not None else []
        roles = resolve_roles(local, records)
        obj_rows, id_rows = exception_index(local, records)
        groups = build_groups(local, records, roles, obj_rows, id_rows)
        assign_ordinals(local, groups)
        return groups, compose(local, groups)

    if "ordinal" in wanted:
        for base, want in (("00005", 5), ("a02011", 11), ("rep00001", 1),
                           ("r01019", 19), ("14027", 27)):
            if ordinal_of(base) != want:
                findings.append("ordinal(%r) = %r, expected %d"
                                % (base, ordinal_of(base), want))
        if ordinal_of("nodigits") is not None:
            findings.append("ordinal of a digitless base is not None")
        if base_of("06005-m") != "06005" or base_of("06005") != "06005":
            findings.append("base_of does not strip exactly one -m")

    if "grouping" in wanted:
        # Four objects, one id, one card -- the a02011 shape.
        recs = [_rec("a2", "X.json", "/%d" % i, md={"id": "a02011", "type": "Event"})
                for i in range(4)]
        recs.append(_rec("a2", "X.json", "/9", md={"id": "a02001",
                                                   "type": "Investigator"}))
        recs.append(_rec("a2", "X.json", "/10", md={"id": "a02001-m",
                                                    "type": "Minicard"}))
        groups, new_ids = pipeline(recs)
        multi = [g for g in groups.values() if len(g.objects) == 4]
        if len(multi) != 1 or new_ids[multi[0].key] != "akna2011":
            findings.append("grouping: the four-object card did not collapse to "
                            "one id (%s)" % sorted(new_ids.values()))
        if sorted(new_ids.values()) != ["akna2001", "akna2001-m", "akna2011"]:
            findings.append("grouping: %s" % sorted(new_ids.values()))

    if "collision" in wanted:
        # Two DIFFERENT bases landing on one ordinal, with no E-row.
        recs = [_rec("00", "A.json", "/0", md={"id": "00005", "type": "Investigator"}),
                _rec("00", "A.json", "/1", md={"id": "00005-m", "type": "Minicard"}),
                _rec("00", "B.json", "/0", md={"id": "10005", "type": "Asset"})]
        fires("collision (no E-row)", kc.EXIT_RENUMBER, lambda: pipeline(recs))
        # E1's exact shape: an investigator whose own id is the defect (00005) and
        # a minicard whose base (01005) supplies the real ordinal. Two distinct
        # base strings on one ordinal, reconciled by an E-row, composing to a pair
        # that differs only by -m.
        e1 = [_rec("01", "S.json", "/0", md={"id": "00005", "type": "Investigator"}),
              _rec("01", "S.json", "/2", md={"id": "01005", "type": "Minicard"})]
        exc = [{"id": "Et", "pack": "01", "whole_pack": False, "evidence": "test",
                "assign": [{"file": "S.json", "path": "/0", "ordinal": 5}]}]
        _groups, new_ids = pipeline(e1, exceptions=exc)
        if sorted(new_ids.values()) != ["akn01005", "akn01005-m"]:
            findings.append("collision: the E1 shape did not compose as expected "
                            "(%s)" % sorted(new_ids.values()))
        # And the same pair WITHOUT its E-row is a refusal, so the row is doing work.
        fires("collision (E1 shape, row removed)", kc.EXIT_RENUMBER,
              lambda: pipeline(e1))
        # An investigator and its own minicard share a base: NOT a collision.
        pair = [_rec("00", "A.json", "/0", md={"id": "00001", "type": "Investigator"}),
                _rec("00", "A.json", "/1", md={"id": "00001-m", "type": "Minicard"})]
        groups, new_ids = pipeline(pair)
        if sorted(new_ids.values()) != ["akn00001", "akn00001-m"]:
            findings.append("collision: a false positive on an investigator and "
                            "its own minicard")

    if "bijection" in wanted:
        orphan = [_rec("00", "A.json", "/0", md={"id": "00001",
                                                 "type": "Investigator"}),
                  _rec("00", "A.json", "/1", md={"id": "00002-m",
                                                 "type": "Minicard"})]
        fires("bijection (orphan minicard)", kc.EXIT_RENUMBER,
              lambda: pipeline(orphan))
        lonely = [_rec("00", "A.json", "/0", md={"id": "00001",
                                                 "type": "Investigator"})]
        fires("bijection (investigator with no minicard)", kc.EXIT_RENUMBER,
              lambda: pipeline(lonely))

    if "minicard-base" in wanted:
        # The pack-3 six: a Minicard-tagged object with no GMNotes takes its
        # file's single investigator.
        recs = [_rec("03", "C.json", "/0", md={"id": "03004",
                                               "type": "Investigator"},
                     nickname="케오베"),
                _rec("03", "C.json", "/2", tags=["Minicard"])]
        _groups, new_ids = pipeline(recs)
        if sorted(new_ids.values()) != ["akn03004", "akn03004-m"]:
            findings.append("minicard-base: %s" % sorted(new_ids.values()))
        two = [_rec("03", "D.json", "/0", md={"id": "03001", "type": "Investigator"}),
               _rec("03", "D.json", "/1", md={"id": "03002", "type": "Investigator"}),
               _rec("03", "D.json", "/2", tags=["Minicard"]),
               _rec("03", "D.json", "/3", md={"id": "03001-m", "type": "Minicard"}),
               _rec("03", "D.json", "/4", md={"id": "03002-m", "type": "Minicard"})]
        fires("minicard-base (two investigators in the file)", kc.EXIT_RENUMBER,
              lambda: pipeline(two))

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_renumber.py",
        description="arknights `renumber` -- role resolution, card grouping, "
                    "ordinal assignment and the committed id map.")
    parser.add_argument("--run-dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    kc.check_invocation_guards()

    if args.selftest is not None:
        fault = args.selftest or None
        print("akn_renumber --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_VERIFY
        print("  ok: the ordinal rule, many-to-one grouping, collisions in both "
              "directions (a bare one refuses, an E-row-covered one composes, an "
              "investigator plus its own minicard is not one), the bijection, and "
              "the GMNotes-less minicard's base")
        return kc.EXIT_OK

    if not args.run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    cfg = kz.load_config()
    mode = "dry-run" if args.dry_run else "build"
    report, idmap = run_renumber(cfg, args.run_dir, mode=mode)
    if mode == "build":
        kc.write_report(report, args.run_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    print("arknights renumber -- %s" % report["run_id"])
    counts = report["counts"]
    print("  new ids     : %d (%d investigators + %d minicards + %d other)"
          % (counts["new_ids"], counts["investigators"], counts["minicards"],
             counts["other"]))
    print("  objects     : %d mapped, of which %d carry metadata"
          % (counts["objects_mapped"], counts["objects"]))
    print("  many-to-one : %d group(s) with more than one object"
          % counts["many_to_one_groups"])
    print("  remainders  : %d declared" % counts["declared_remainders"])
    for entry in report["checks"]:
        print("  %-4s %-26s %s" % (entry["id"], entry["name"], entry["status"]))
        for line in entry["detail"][:5]:
            print("       - %s" % line)
    print("  hand-check  : %d row(s) with source_id_status != carried"
          % sum(1 for e in idmap["ids"].values()
                if e["source_id_status"] != "carried"))
    print("  verdict     : %s (exit %d, consumable %s)"
          % (report["verdict"], report["exit_code"], report["consumable"]))
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.AknRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
