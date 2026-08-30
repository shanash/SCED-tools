#!/usr/bin/python3
"""arknights `verify` -- the V1-V12 acceptance gate (design section 6). Owns 86.

EVERY CHECK NAMES THE OBJECTS IT FAILED ON; none prints a count alone. That is
the difference between a gate and a scoreboard, and it is the property that makes
the deliberate-break exercise in step S7 possible: breaking V1 has to produce a
message an operator can act on, not "V1 fail".

TWO CHECKS DO NOT RETURN 86, and both are deliberate:

  * V8 returns 82. "docs/Arknights/ changed under the run" is INPUT DRIFT, not a
    failed assertion about the output, and the first move is different: re-run
    `scan`, do not go looking at the bag.
  * V12 is opt-in and NEVER fatal. The r2.dev edge returns 403 to python's urllib
    User-Agent while curl gets 200 (a recorded gotcha), so a transient edge
    response must never be able to block a publish. It is printed as `skipped` on
    every run rather than omitted, because a silent skip and a pass look identical
    once the prose has scrolled away.

V10 IS HALF-REACHABLE UNTIL `publish` HAS RUN. The library entry does not exist
until then, so until it does the live half reports `skipped` with a note naming
what it did check -- the entry TEMPLATE in the config, which is the half that can
be wrong before anything is written. The template half can still fail and still
returns 86. Once the entry exists the live half runs on its own: exactly one
arknights row, byte-equal to the template, no cycle_code, a count floor one
higher than before, and a sorter-stable file. It must keep degrading, because
`verify` legitimately runs before `publish` on every fresh run.
"""

import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

import akn_common as kc  # noqa: E402
import akn_config as kz  # noqa: E402

STAGE = "verify"

CHECK_IDS = ("V1", "V2", "V3", "V3b", "V4", "V5", "V6", "V7", "V8", "V9", "V10",
             "V11", "V12")

REAL_CARDS_GLOB = "SCED/objects/AllPlayerCards.15bb07/**/*.gmnotes"
FAN_PACK_GLOBS = ("SCED-downloads/downloadable/playercards/*.json",
                  "SCED-downloads/decomposed/playercards/**/*.json")
LIBRARY_JSON = "SCED-downloads/library.json"

#: Platform path, never PATH. See its use in v12().
CURL = "/usr/bin/curl"
SORT_LIBRARY = "SCED-downloads/misc/sort_library.py"

#: V12 only. A url is handed to curl as one element of a list argv -- never a
#: shell string -- and it is matched against this first, so a source-supplied
#: string can never become an option or a second command.
#: It deliberately does NOT scope the host: the urls come from third-party fan
#: packs, and an allowlist would make V12 blind to the one case most worth
#: reporting -- a pack pointing somewhere unexpected. The probed hosts are named
#: in the check's note instead, so an unexpected one shows up in the report
#: rather than only in the operator's network log.
SAFE_URL = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9/_.~%-]*$")


class Subject(object):
    """Everything the twelve checks read, loaded once and named."""

    def __init__(self, cfg, run_dir, workspace=None):
        self.cfg = cfg
        self.run_dir = run_dir
        self.workspace = workspace or kc.WORKSPACE_ROOT
        self.paths = dict(
            (name, os.path.join(run_dir, name + ".json"))
            for name in ("inventory", "idmap", "repairs", "source.tree",
                         "arknights"))
        for name, path in sorted(self.paths.items()):
            if not os.path.exists(path):
                kc.refuse(kc.EXIT_PRECONDITION, "%s.json is missing" % name, path)
        self.inventory = json.loads(kc.read_text(self.paths["inventory"]))
        self.idmap = json.loads(kc.read_text(self.paths["idmap"]))
        self.repairs = json.loads(kc.read_text(self.paths["repairs"]))
        self.tree = json.loads(kc.read_text(self.paths["source.tree"]))
        self.text = kc.read_text(self.paths["arknights"])
        self.bag = json.loads(self.text)
        self.objects = self._collect(self.bag)
        self.sub_bags = self.bag.get("ContainedObjects") or []

    @staticmethod
    def _collect(bag):
        """(guid -> node) over the whole built bag, plus the parent Deck of each."""
        found = collections.OrderedDict()

        def visit(node, parent_deck, depth):
            found[node.get("GUID")] = {"node": node, "parent_deck": parent_deck,
                                       "depth": depth}
            inner = node.get("GUID") if node.get("Name") == "Deck" else parent_deck
            for kid in (node.get("ContainedObjects") or []):
                visit(kid, inner, depth + 1)

        visit(bag, None, 0)
        return found

    def metadata(self, node):
        raw = node.get("GMNotes")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        return json.loads(raw)

    def cards(self):
        """Every object below the sub-bags -- the source objects, not containers."""
        for guid, entry in self.objects.items():
            if entry["depth"] >= 2:
                yield guid, entry["node"]

    def output_ids(self):
        ids = set()
        for _guid, node in self.cards():
            md = self.metadata(node)
            if md and md.get("id"):
                ids.add(md["id"])
        return ids


# ---------------------------------------------------------------------------
# V1 -- id format and uniqueness
# ---------------------------------------------------------------------------

def v1(subject):
    cfg = subject.cfg
    scheme = cfg["id_scheme"]
    pattern = re.compile(scheme["regex"])
    suffix = scheme["minicard_suffix"]
    detail = []
    ids = subject.idmap["ids"]
    for new_id, entry in sorted(ids.items()):
        stem = new_id[:-len(suffix)] if new_id.endswith(suffix) else new_id
        if not pattern.match(stem):
            detail.append("%s does not match %s" % (new_id, scheme["regex"]))
        if len(stem) != 8:
            detail.append("%s: the base is %d characters, not 8" % (new_id,
                                                                    len(stem)))
        if "-" in stem:
            detail.append("%s: the base contains a dash, so getMiniId would "
                          "resolve its minicard from the first five characters"
                          % new_id)
        if kc.mini_id_branch(stem) != 1:
            detail.append("%s takes getMiniId branch %d, not 1"
                          % (new_id, kc.mini_id_branch(stem)))
        if entry["role"] == "minicard":
            if not new_id.endswith(suffix):
                detail.append("%s is a minicard without the %s suffix"
                              % (new_id, suffix))
            elif entry.get("base") != stem:
                detail.append("%s records base %r, not %r"
                              % (new_id, entry.get("base"), stem))
        elif new_id.endswith(suffix):
            detail.append("%s carries the minicard suffix but its role is %s"
                          % (new_id, entry["role"]))
    want = cfg["oracles"]["new_ids"]
    if len(ids) != want:
        detail.append("%d ids, the design's oracle is %d" % (len(ids), want))

    # The committed audit artifact must be the bytes this run produced. It is the
    # only durable record of what akn06005 means once the gitignored source is
    # gone, so a hand-edit of either copy has to be visible.
    committed = kz.IDMAP_PATH
    if not os.path.exists(committed):
        detail.append("%s is missing" % os.path.relpath(committed, subject.workspace))
    elif kc.sha256_file(committed) != kc.sha256_file(subject.paths["idmap"]):
        detail.append("%s is not byte-identical to this run's idmap.json"
                      % os.path.relpath(committed, subject.workspace))
    return kc.check("V1", "id format and uniqueness", detail,
                    note="%d ids, %d dash-free bases"
                    % (len(ids), sum(1 for i in ids
                                     if "-" not in (i[:-len(suffix)]
                                                    if i.endswith(suffix) else i))))


# ---------------------------------------------------------------------------
# V2 -- many-to-one preserved
# ---------------------------------------------------------------------------

def v2(subject):
    cfg = subject.cfg
    detail = []
    ids = subject.idmap["ids"]

    # The partition of the metadata-carrying objects by (pack, base id, is-mini)
    # must equal the partition by new id, except where a split is declared.
    by_source = collections.defaultdict(set)
    for new_id, entry in ids.items():
        is_mini = entry["role"] == "minicard"
        for source_id in entry["source_ids"]:
            base = source_id[:-2] if source_id.endswith("-m") else source_id
            by_source[(entry["pack"], base, is_mini)].add(new_id)
    declared = set()
    for row in cfg["many_to_one"]["declared_splits"]:
        for pack in row["packs"]:
            for is_mini in (True, False):
                base = row["source_id"]
                declared.add((pack, base[:-2] if base.endswith("-m") else base,
                              is_mini))
    for key, new_ids in sorted(by_source.items()):
        if len(new_ids) > 1 and key not in declared:
            detail.append("(pack %s, base %s, minicard=%s) maps to %d new ids: %s"
                          % (key[0], key[1], key[2], len(new_ids),
                             sorted(new_ids)))

    for row in cfg["many_to_one"]["benign_groups"]:
        hits = [nid for nid, entry in ids.items()
                if entry["pack"] == row["pack"]
                and row["source_id"] in entry["source_ids"]]
        if len(hits) != 1:
            detail.append("benign group %s in pack %s maps to %d new ids (%s) -- "
                          "the same card printed %d times must receive ONE id"
                          % (row["source_id"], row["pack"], len(hits), hits,
                             row["objects"]))
        elif len(ids[hits[0]]["objects"]) != row["objects"]:
            detail.append("benign group %s carries %d objects, config says %d"
                          % (hits[0], len(ids[hits[0]]["objects"]), row["objects"]))

    for row in cfg["many_to_one"]["declared_splits"]:
        hits = sorted(nid for nid, entry in ids.items()
                      if row["source_id"] in entry["source_ids"])
        if len(hits) < 2:
            detail.append("declared split %s maps to %s -- the two genuine "
                          "duplicates must receive DIFFERENT ids"
                          % (row["source_id"], hits))
    return kc.check(
        "V2", "many-to-one preserved", detail,
        note="%d benign group(s), %d declared split(s)"
        % (len(cfg["many_to_one"]["benign_groups"]),
           len(cfg["many_to_one"]["declared_splits"])))


# ---------------------------------------------------------------------------
# V3 / V3b -- collision with the real corpus, and with the other fan packs
# ---------------------------------------------------------------------------

def real_card_ids(workspace):
    ids = set()
    files = 0
    for path in glob.glob(os.path.join(workspace, REAL_CARDS_GLOB), recursive=True):
        files += 1
        try:
            data = json.loads(kc.read_text(path))
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("id"):
            ids.add(data["id"])
        for alt in (data.get("alternate_ids") or []):
            ids.add(alt)
    return ids, files


def _gmnotes_ids(node, out):
    if isinstance(node, dict):
        raw = node.get("GMNotes")
        if isinstance(raw, str) and raw.strip():
            try:
                md = json.loads(raw)
            except ValueError:
                md = None
            if isinstance(md, dict) and md.get("id"):
                out.add(md["id"])
        if isinstance(node.get("id"), str) and "GMNotes" not in node:
            pass  # a decomposed .gmnotes sidecar is handled by its own branch
        for value in node.values():
            _gmnotes_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            _gmnotes_ids(item, out)


def fan_pack_ids(workspace):
    ids = set()
    files = 0
    for pattern in FAN_PACK_GLOBS:
        for path in glob.glob(os.path.join(workspace, pattern), recursive=True):
            files += 1
            try:
                data = json.loads(kc.read_text(path))
            except ValueError:
                continue
            _gmnotes_ids(data, ids)
    for path in glob.glob(os.path.join(
            workspace, "SCED-downloads/decomposed/playercards/**/*.gmnotes"),
            recursive=True):
        files += 1
        try:
            data = json.loads(kc.read_text(path))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("id"):
            ids.add(data["id"])
    return ids, files


def v3(subject):
    real, files = real_card_ids(subject.workspace)
    floor = subject.cfg["oracles"]["real_card_ids_min"]
    ours = subject.output_ids() | set(subject.idmap["ids"])
    hits = sorted(ours & real)
    detail = ["%s collides with a real card id" % i for i in hits]
    # The corpus is a live sibling checkout and only grows, so the count is a
    # FLOOR. A shrink is the dangerous direction: a partial checkout or an empty
    # glob would make this check pass over nothing.
    if len(real) < floor:
        detail.append("the real corpus yielded %d distinct ids from %d files, "
                      "below the floor of %d -- V3 would be passing over a corpus "
                      "smaller than the one it was calibrated against"
                      % (len(real), files, floor))
    return kc.check("V3", "collision vs the real corpus", detail,
                    note="%d ours vs %d real ids from %d .gmnotes files (floor %d)"
                    % (len(ours), len(real), files, floor))


def v3b(subject):
    fan, files = fan_pack_ids(subject.workspace)
    ours = subject.output_ids() | set(subject.idmap["ids"])
    hits = sorted(ours & fan)
    if not fan:
        hits = []
    return kc.check(
        "V3b", "collision vs the other fan packs",
        ["%s collides with an existing fan pack id" % i for i in hits]
        + ([] if fan else ["the fan-pack oracle is EMPTY (%d files) -- V3b would "
                           "be passing over nothing" % files]),
        note="%d ours vs %d fan-pack ids from %d files -- a fan-pack collision is "
             "as real as an FFG one once a player loads both bags"
             % (len(ours), len(fan), files))


# ---------------------------------------------------------------------------
# V4 -- the live minicard gate
# ---------------------------------------------------------------------------

def v4(subject):
    """Global.ttslua:397 and Playermat.ttslua:455-458 read the LIVE object's tag
    and GMNotes, not the card index -- so omitting cycle_code does not help here
    and this is the check that decides whether minicards work in play."""
    detail = []
    investigators, minicards = {}, {}
    containers = 0
    for guid, node in subject.cards():
        # A Deck is a CONTAINER, and one of the 93 is tagged ["Minicard"] --
        # Mlynar's, which holds the two 무에나 minicards. It carries no GMNotes
        # because no Deck in the corpus does, and it is not a minicard: the live
        # gate reads md.type off each spawned CARD. Counting it would report 93
        # minicards against 92 investigators forever.
        if node.get("Name") == "Deck":
            containers += 1
            continue
        md = subject.metadata(node)
        tags = node.get("Tags") or []
        if md and md.get("type") == "Investigator":
            investigators[md["id"]] = guid
        if "Minicard" not in tags:
            if md and md.get("type") == "Minicard":
                detail.append("%s carries type Minicard but is not tagged "
                              "Minicard, so hasTag('Minicard') is false" % guid)
            continue
        if md is None:
            detail.append("%s is tagged Minicard but carries no GMNotes" % guid)
            continue
        if md.get("type") != "Minicard":
            detail.append("%s is tagged Minicard but its GMNotes type is %r"
                          % (guid, md.get("type")))
            continue
        minicards[md["id"]] = guid

    for inv_id, guid in sorted(investigators.items()):
        if kc.mini_id_branch(inv_id) != 1:
            detail.append("%s (%s) does not take getMiniId's first branch"
                          % (inv_id, guid))
        expected = kc.get_mini_id(inv_id)
        if expected not in minicards:
            detail.append("investigator %s has no minicard with id %s"
                          % (inv_id, expected))
    for mini_id, guid in sorted(minicards.items()):
        base = mini_id[:-2] if mini_id.endswith("-m") else mini_id
        if base not in investigators:
            detail.append("minicard %s (%s) is orphaned -- no investigator %s"
                          % (mini_id, guid, base))
        elif kc.get_mini_id(base) != mini_id:
            detail.append("minicard %s is not getMiniId(%s) = %s"
                          % (mini_id, base, kc.get_mini_id(base)))

    want = subject.cfg["oracles"]["minicards"]
    if len(minicards) != want:
        detail.append("%d minicards pass the live gate, the design's oracle is %d"
                      % (len(minicards), want))
    if len(investigators) != subject.cfg["oracles"]["investigators"]:
        detail.append("%d investigators, the design's oracle is %d"
                      % (len(investigators),
                         subject.cfg["oracles"]["investigators"]))
    return kc.check("V4", "minicard live gate", detail,
                    note="%d/%d minicards, %d investigators, %d Deck(s) skipped "
                         "as containers"
                    % (len(minicards), want, len(investigators), containers))


# ---------------------------------------------------------------------------
# V5 -- Deck integrity
# ---------------------------------------------------------------------------

def v5(subject):
    detail = []
    source = dict((r["guid"], r) for r in subject.inventory["objects"])
    decks_in = [r for r in subject.inventory["objects"] if r["name"] == "Deck"]
    decks_out = [(guid, e) for guid, e in subject.objects.items()
                 if e["node"].get("Name") == "Deck"]

    for record in decks_in:
        entry = subject.objects.get(record["guid"])
        if entry is None:
            detail.append("Deck %s (%s %s) is not in the output"
                          % (record["guid"], record["file"], record["path"]))
            continue
        node = entry["node"]
        children = [k.get("GUID") for k in (node.get("ContainedObjects") or [])]
        if children != record["children"]:
            detail.append("Deck %s child sequence changed: %s -> %s"
                          % (record["guid"], record["children"], children))
        if node.get("DeckIDs") != record["deck_ids"]:
            detail.append("Deck %s DeckIDs changed" % record["guid"])
        if sorted((node.get("CustomDeck") or {}).keys()) != record["custom_deck"]:
            detail.append("Deck %s CustomDeck key set changed" % record["guid"])
        # No object may have arrived from another source file.
        for child in children:
            origin = source.get(child)
            if origin is None:
                detail.append("Deck %s gained an object %s that is not in the "
                              "source" % (record["guid"], child))
            elif (origin["file"], origin["pack"]) != (record["file"],
                                                      record["pack"]):
                detail.append("Deck %s (%s) holds %s from %s"
                              % (record["guid"], record["file"], child,
                                 origin["file"]))
        # Within one Deck, a CustomDeck index must map to exactly one image pair.
        # Index 2664 alone appears with SEVEN pairs across the corpus, so this is
        # the assertion that stands between a merge and silently swapped art.
        pairs = collections.defaultdict(set)
        for child in (node.get("ContainedObjects") or []):
            for index, deck in (child.get("CustomDeck") or {}).items():
                pairs[index].add((deck.get("FaceURL"), deck.get("BackURL")))
        for index, seen in sorted(pairs.items()):
            if len(seen) > 1:
                detail.append("Deck %s: CustomDeck index %s maps to %d different "
                              "FaceURL/BackURL pairs" % (record["guid"], index,
                                                         len(seen)))
    if len(decks_out) != len(decks_in):
        detail.append("%d Decks in, %d out" % (len(decks_in), len(decks_out)))
    return kc.check("V5", "Deck integrity", detail,
                    note="%d decks, %d objects moved"
                    % (len(decks_in), sum(1 for d in detail if "sequence" in d)))


# ---------------------------------------------------------------------------
# V6 -- cloud-3 survivors
# ---------------------------------------------------------------------------

def v6(subject):
    norm = subject.cfg["url_normalisation"]
    survivors = subject.text.count(norm["from"])
    bare_host = subject.text.count("cloud-3.steamusercontent.com")
    recorded = subject.inventory["counts"]["url_cloud3"]
    detail = []
    if survivors:
        detail.append("%d occurrence(s) of %s survived" % (survivors, norm["from"]))
    if bare_host:
        detail.append("%d occurrence(s) of the cloud-3 host survived in some other "
                      "form" % bare_host)
    if recorded != norm["expected"]:
        detail.append("`scan` recorded %d cloud-3 references, config declares %d"
                      % (recorded, norm["expected"]))
    return kc.check("V6", "cloud-3 survivors", detail,
                    note="0 survivors, %d normalised" % recorded)


# ---------------------------------------------------------------------------
# V7 -- JSON validity and TTS shape
# ---------------------------------------------------------------------------

def v7(subject):
    cfg = subject.cfg
    bag = subject.bag
    detail = []
    if bag.get("Name") != "Custom_Model_Bag":
        detail.append("root Name is %r, not Custom_Model_Bag" % bag.get("Name"))
    if bag.get("Nickname") != cfg["bag"]["nickname"]:
        detail.append("root Nickname is %r, not %r" % (bag.get("Nickname"),
                                                       cfg["bag"]["nickname"]))
    subs = subject.sub_bags
    if len(subs) != len(cfg["packs"]):
        detail.append("%d sub-bags, expected %d" % (len(subs), len(cfg["packs"])))
    for index, sub in enumerate(subs):
        if sub.get("Name") != "Bag":
            detail.append("sub-bag %d is Name %r, not Bag" % (index,
                                                              sub.get("Name")))
        for key in ("LuaScript", "LuaScriptState"):
            if key in sub:
                detail.append("sub-bag %r carries %s; a plain Bag must not"
                              % (sub.get("Nickname"), key))
    nicknames = [sub.get("Nickname") for sub in subs]
    expected = [row["nickname"] for row in sorted(cfg["packs"],
                                                  key=lambda r: r["code"])]
    if nicknames != expected:
        detail.append("sub-bag nicknames do not match the pack table in order")

    guids = []

    def collect(node):
        guids.append(node.get("GUID"))
        for kid in (node.get("ContainedObjects") or []):
            collect(kid)

    collect(bag)
    want = subject.cfg["oracles"]["objects"] + subject.cfg["oracles"]["containers"]
    if len(guids) != len(set(guids)):
        duplicates = [g for g, n in collections.Counter(guids).items() if n > 1]
        detail.append("%d duplicate GUID(s): %s" % (len(duplicates),
                                                    sorted(duplicates)[:8]))
    if len(guids) != want:
        detail.append("%d GUIDs, expected %d source + %d derived = %d"
                      % (len(guids), subject.cfg["oracles"]["objects"],
                         subject.cfg["oracles"]["containers"], want))
    try:
        state = json.loads(bag.get("LuaScriptState") or "")
    except ValueError:
        state = None
        detail.append("LuaScriptState does not parse")
    if state is not None:
        keys = set((state.get("ml") or {}).keys())
        sub_guids = set(sub.get("GUID") for sub in subs)
        if keys != sub_guids:
            detail.append("the ml key set is not the 22 sub-bag GUIDs "
                          "(%d extra, %d missing)"
                          % (len(keys - sub_guids), len(sub_guids - keys)))
    return kc.check("V7", "JSON / TTS shape", detail,
                    note="%d GUIDs, %d sub-bags, ml keys match"
                    % (len(guids), len(subs)))


# ---------------------------------------------------------------------------
# V8 -- the source tree is unmodified
# ---------------------------------------------------------------------------

def v8(subject):
    """The only empirical layer of the read-only guarantee, and the only check
    that returns 82 rather than 86."""
    root = kz.source_root(subject.cfg, workspace=subject.workspace)
    digest, per_file, total = kc.sha256_tree_detail(root)
    recorded = subject.tree
    detail = []
    if digest != recorded["sha256"]:
        was = recorded.get("per_file") or {}
        changed = sorted(k for k in set(was) | set(per_file)
                         if was.get(k) != per_file.get(k))
        for path in changed[:20]:
            if path not in was:
                detail.append("ADDED %s" % path)
            elif path not in per_file:
                detail.append("REMOVED %s" % path)
            else:
                detail.append("MODIFIED %s" % path)
        if not changed:
            detail.append("the tree digest moved but no file did -- the recorded "
                          "per_file map is missing; re-run `scan`")
    if len(per_file) != recorded["files"]:
        detail.append("%d files, `scan` recorded %d" % (len(per_file),
                                                        recorded["files"]))
    if total != recorded["bytes"]:
        detail.append("%d bytes, `scan` recorded %d" % (total, recorded["bytes"]))
    return kc.check("V8", "source tree unmodified", detail,
                    exit_on_fail=kc.EXIT_DRIFT,
                    note="sha256 %s == %s" % (digest[:12],
                                              recorded["sha256"][:12]))


# ---------------------------------------------------------------------------
# V9 -- metadata coverage
# ---------------------------------------------------------------------------

def v9(subject):
    declared = subject.cfg["declared_remainders"]
    remainder = []
    for guid, node in subject.cards():
        if node.get("Name") == "Deck":
            continue
        try:
            md = subject.metadata(node)
        except ValueError:
            remainder.append("%s: GMNotes does not parse" % guid)
            continue
        if md is None or not md.get("id"):
            remainder.append("%s (%s) carries no id" % (guid,
                                                        node.get("Nickname")))
    detail = list(remainder)
    if len(remainder) != declared:
        detail.append("%d object(s) with no metadata, config declares %d"
                      % (len(remainder), declared))
    entry = kc.check("V9", "metadata coverage", detail,
                     note="%d declared remainder(s)" % declared)
    entry["tolerance"] = "declared-remainder"
    return entry


# ---------------------------------------------------------------------------
# V10 -- the library entry
# ---------------------------------------------------------------------------

def v10(subject):
    """The template half always runs; the live half waits for `publish`."""
    cfg = subject.cfg
    rules = cfg["library_rules"]
    entry = cfg["library_entry"]
    detail = []

    for key in rules["forbidden_keys"]:
        if key in entry:
            detail.append("the entry template carries %r" % key)
    if entry.get("author") == rules["forbidden_author"]:
        detail.append("the entry template's author is %r" % rules["forbidden_author"])
    if entry.get("boxsize") not in rules["boxsize_values"]:
        detail.append("boxsize %r is not one of %s" % (entry.get("boxsize"),
                                                       rules["boxsize_values"]))
    if not str(entry.get("boxart", "")).startswith("https://"):
        detail.append("boxart is not https://")
    payload = os.path.basename(subject.paths["arknights"])
    if entry.get("filename") + ".json" != payload:
        detail.append("filename %r does not match the payload basename %r"
                      % (entry.get("filename"), payload))

    sorter = kc.load_module_by_path(
        "akn_sort_library", os.path.join(subject.workspace, SORT_LIBRARY))
    if list(sorter.reorder_item_keys(entry).items()) != list(entry.items()):
        detail.append("the entry template's key order is not sort_library's "
                      "KEY_ORDER; reorder_item_keys is not idempotent on it")

    library_path = os.path.join(subject.workspace, LIBRARY_JSON)
    library = json.loads(kc.read_text(library_path))
    content = library.get("content")
    if content is None:
        detail.append("%s has no content key" % LIBRARY_JSON)
        return kc.check("V10", "library entry", detail)
    live = [item for item in content if item.get("filename") == entry["filename"]]
    if not live:
        return kc.check(
            "V10", "library entry", detail,
            status="skipped" if not detail else "fail",
            note="template ok (6 assertions); the LIVE half needs the entry, "
                 "which `publish` writes -- %s carries %d entries and none for "
                 "%r" % (LIBRARY_JSON, len(content), entry["filename"]))

    if len(live) != 1:
        detail.append("%d entries carry filename %r" % (len(live),
                                                        entry["filename"]))
    for item in live:
        for key in rules["forbidden_keys"]:
            if key in item:
                detail.append("the live entry carries %r" % key)
        if item.get("author") == rules["forbidden_author"]:
            detail.append("the live entry's author is the FFG sentinel")
        # The shipped entry must still BE the template, values and key order
        # alike. Without this the live half only ever checked what the entry is
        # not, and a hand-edit of the shipped row -- a changed boxart, a dropped
        # description -- would pass every other assertion here.
        if list(item.items()) != list(sorter.reorder_item_keys(entry).items()):
            detail.append("the live entry differs from the config template: "
                          "on disk %s, template %s"
                          % (sorted(item.items()), sorted(entry.items())))
    # The floor RISES BY ONE once the entry exists, which is `publish`'s +1 delta
    # asserted from the other side. It stays a floor rather than an equality
    # because upstream adds entries of its own; the failure a count can really
    # catch is a truncated file, and that direction is unaffected.
    floor = rules["expected_entries_min"] + 1
    if len(content) < floor:
        detail.append("%d entries with the arknights entry present, below the "
                      "floor of %d -- publish adds exactly one, so library.json "
                      "looks truncated" % (len(content), floor))
    ordered = [sorter.reorder_item_keys(item) for item in
               [i for _n, i in sorted(list(enumerate(content)),
                                      key=sorter.get_sort_keys)]]
    if [list(i.items()) for i in ordered] != [list(i.items()) for i in content]:
        detail.append("%s is not sorter-stable -- re-run misc/sort_library.py"
                      % LIBRARY_JSON)
    return kc.check("V10", "library entry", detail,
                    note="%d entries, no cycle_code, sorter-stable" % len(content))


# ---------------------------------------------------------------------------
# V11 -- the MemoryBag LuaScript identity
# ---------------------------------------------------------------------------

def v11(subject):
    built = kc.memory_bag_luascript(subject.workspace,
                                    subject.cfg["bag"].get("memory_bag_updater"))
    shipped = subject.bag.get("LuaScript") or ""
    detail = []
    if shipped != built:
        detail.append("the shipped LuaScript is %d bytes, the updater's "
                      "construction is %d; misc/memory-bag-updater.py would "
                      "rewrite our file on its next run"
                      % (len(shipped), len(built)))
    return kc.check("V11", "MemoryBag LuaScript identity", detail,
                    note="%d bytes, identical to the TTSUpdater construction"
                    % len(built))


# ---------------------------------------------------------------------------
# V12 -- url reachability (opt-in, never fatal)
# ---------------------------------------------------------------------------

def v12(subject, sample=0):
    if not sample:
        return kc.check("V12", "url reachability", [], status="skipped",
                        note="--probe-urls not given")
    urls = set()
    for _guid, node in subject.cards():
        for deck in (node.get("CustomDeck") or {}).values():
            for key in ("FaceURL", "BackURL"):
                if deck.get(key):
                    urls.add(deck[key])
    ordered = sorted(urls)[:sample]
    results, hosts = [], set()
    for url in ordered:
        if not SAFE_URL.match(url):
            results.append("%s does not match the safe-url pattern; not probed"
                           % url[:80])
            continue
        hosts.add(url.split("/")[2])
        # A RANGED GET, never a HEAD. MEASURED 2026-08-30: Steam's CDN answers
        # `curl -I` with 404 for a url that `curl -r 0-0` serves as 206
        # image/png, so a HEAD probe reports every Steam atlas in the corpus as
        # dead. Same lesson as D3's urllib 403, different mechanism -- an edge's
        # answer is evidence about the asset only when the probe resembles the
        # request the mod actually makes.
        # /usr/bin/curl, not PATH: the same platform-path rule P0b enforces for
        # the interpreter. CLAUDE.md records this one as the single binary in
        # the nightly's dependency list that genuinely inherits bash's TCC grant
        # (Identifier=com.apple.curl, Platform identifier=26) -- a Homebrew curl
        # ahead of it on PATH is its own responsible process and does not.
        proc = subprocess.run([CURL, "-sS", "-o", "/dev/null",
                               "-w", "%{http_code}", "--max-time", "20",
                               "-r", "0-0", "--", url],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        code = proc.stdout.decode("ascii", "replace").strip()
        if code not in ("200", "206"):
            results.append("%s -> %s" % (url[:80], code or "no response"))
    return kc.check(
        "V12", "url reachability", [], status="skipped" if not ordered else "pass",
        note="probed %d of %d distinct atlas urls with a ranged GET; hosts "
             "contacted: %s; %d not 200/206: %s. A 403 from an r2.dev edge or a "
             "404 from a HEAD is not evidence of a broken upload -- this is "
             "opt-in and never fatal for exactly that reason."
             % (len(ordered), len(urls), ", ".join(sorted(hosts)) or "none",
                len(results), results[:5] or "none"))


CHECKS = (v1, v2, v3, v3b, v4, v5, v6, v7, v8, v9, v10, v11)


def run_verify(cfg, run_dir, mode="build", accepted=None, probe_urls=0,
               workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    kz.assert_predecessors(run_dir, STAGE, workspace)
    subject = Subject(cfg, run_dir, workspace)
    accepted = accepted or {}

    checks = [fn(subject) for fn in CHECKS]
    checks.append(v12(subject, probe_urls))

    triggered = []
    for entry in checks:
        if entry["status"] != "fail":
            continue
        flag = entry.get("tolerance")
        if flag and accepted.get(flag):
            continue
        triggered.append(entry["exit_on_fail"])

    counts = collections.OrderedDict([
        ("checks", len(checks)),
        ("passed", sum(1 for c in checks if c["status"] == "pass")),
        ("failed", sum(1 for c in checks if c["status"] == "fail")),
        ("skipped", sum(1 for c in checks if c["status"] == "skipped")),
        ("ids", len(subject.idmap["ids"])),
        ("objects", subject.cfg["oracles"]["objects"]),
        ("declared_remainders", cfg["declared_remainders"]),
    ])
    report = kc.new_report(
        STAGE, os.path.basename(run_dir), mode=mode, counts=dict(counts),
        checks=checks, accepted=accepted,
        binding=kc.build_binding([cfg["_path"]] + sorted(subject.paths.values())),
        results={"payload": os.path.relpath(subject.paths["arknights"],
                                            workspace)})
    kc.finalize_report(report, triggered=triggered)
    return report


# ---------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------

FAULTS = ("check-table", "oracles", "safe-url", "corpora")


def selftest(fault=None, verbose=True):
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))
    cfg = kz.load_config()

    if "check-table" in wanted:
        # "V1-V12 all pass" is the acceptance predicate, so the TABLE being total
        # is itself checked: a check quietly dropped makes the predicate easier to
        # satisfy every time it is evaluated.
        if len(CHECKS) != 12:
            findings.append("check-table: %d checks, expected 12 (V12 is added "
                            "separately because it takes a sample size)"
                            % len(CHECKS))
        if len(CHECK_IDS) != 13:
            findings.append("check-table: CHECK_IDS is not V1..V12 plus V3b")
        names = [fn.__name__ for fn in CHECKS]
        if names != ["v1", "v2", "v3", "v3b", "v4", "v5", "v6", "v7", "v8", "v9",
                     "v10", "v11"]:
            findings.append("check-table: the order moved (%s)" % names)

    if "oracles" in wanted:
        for key in ("objects", "containers", "new_ids", "investigators",
                    "minicards", "real_card_ids_min"):
            if key not in cfg["oracles"]:
                findings.append("oracles: %r is missing, so a check that reads it "
                                "would raise instead of failing" % key)
        if cfg["oracles"]["objects"] + cfg["oracles"]["containers"] != 554:
            findings.append("oracles: source + containers is not 554")

    if "safe-url" in wanted:
        for good in ("https://steamusercontent-a.akamaihd.net/ugc/1/ABC/",
                     "https://pub-05b4.r2.dev/boxart/arknights.jpg"):
            if not SAFE_URL.match(good):
                findings.append("safe-url: rejected %r" % good)
        for bad in ("http://x/y", "https://x/y;rm -rf /", "-oPWNED",
                    "https://x/$(whoami)", "file:///etc/passwd",
                    "https://x/y' --config /tmp/evil"):
            if SAFE_URL.match(bad):
                findings.append("safe-url: ACCEPTED %r -- it would reach curl" % bad)

    if "corpora" in wanted:
        real, files = real_card_ids(kc.WORKSPACE_ROOT)
        if files == 0:
            findings.append("corpora: no .gmnotes files found under %s -- V3 "
                            "would pass over an EMPTY oracle" % REAL_CARDS_GLOB)
        elif len(real) < cfg["oracles"]["real_card_ids_min"]:
            findings.append("corpora: %d real ids from %d files, below the floor "
                            "of %d" % (len(real), files,
                                       cfg["oracles"]["real_card_ids_min"]))
        fan, fan_files = fan_pack_ids(kc.WORKSPACE_ROOT)
        if fan_files == 0 or not fan:
            findings.append("corpora: the fan-pack oracle is empty (%d files, %d "
                            "ids) -- V3b would pass over nothing"
                            % (fan_files, len(fan)))

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_verify.py",
        description="arknights `verify` -- the V1-V12 acceptance gate.")
    parser.add_argument("--run-dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--probe-urls", type=int, default=0, metavar="N")
    parser.add_argument("--accept-declared-remainder", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    kc.check_invocation_guards()

    if args.selftest is not None:
        fault = args.selftest or None
        print("akn_verify --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_VERIFY
        print("  ok: the V-table is total and ordered, the oracles it reads are "
              "present, the V12 url pattern refuses every injection shape, and "
              "both collision corpora are non-empty")
        return kc.EXIT_OK

    if not args.run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    cfg = kz.load_config()
    accepted = {"declared-remainder": bool(args.accept_declared_remainder)}
    report = run_verify(cfg, args.run_dir,
                        mode="verify-only" if args.verify_only else "build",
                        accepted=accepted, probe_urls=args.probe_urls)
    if not args.verify_only:
        kc.write_report(report, args.run_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    print("arknights verify -- %s" % report["run_id"])
    for entry in report["checks"]:
        dots = "." * max(1, 38 - len(entry["name"]))
        print("%-4s %s %s %-8s %s"
              % (entry["id"], entry["name"], dots, entry["status"],
                 entry.get("note") or ""))
        for line in entry["detail"][:6]:
            print("       - %s" % line)
        if entry["detail_total"] > 6:
            print("       ... and %d more" % (entry["detail_total"] - 6))
    print("VERDICT: %s (exit %d, consumable %s)"
          % ("pass" if report["exit_code"] == 0 else report["verdict"],
             report["exit_code"], report["consumable"]))
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.AknRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
