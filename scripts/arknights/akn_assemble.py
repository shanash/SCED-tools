#!/usr/bin/python3
"""arknights `assemble` -- build the bag (design section 5.4). Owns exit 85.

THE DECK PROHIBITION LIVES HERE, and it is the one rule in this module that is
not merely mechanical. Every Deck is copied WHOLE -- ContainedObjects, DeckIDs,
CustomDeck and GUID unchanged; only GMNotes ids and URL hosts inside it are
rewritten. The reason is measured: index 2664 alone appears with SEVEN different
FaceURL/BackURL pairs across the corpus and 73 of 328 CardIDs are reused, so
merging two such cards into one Deck silently swaps their images. Card objects
keep their own CustomDeck and a TTS Bag does not merge its children's CustomDeck
maps -- which is exactly why the prohibition is scoped to Deck and to nothing
else.

The eight steps, in order, are design section 5.4's. Two are worth stating here
because they look like conveniences and are not:

  * DEEP-COPY, NEVER A SHARED REFERENCE. A multi-printed card is the same card
    in two objects; a shared reference would make the second printing's id
    rewrite overwrite the first's object.
  * DERIVED GUIDS ARE DETERMINISTIC -- sha256("arknights:v1:" + pack)[:6],
    incremented on collision against the 531 source GUIDs and against each other
    -- so re-running on unchanged inputs produces a byte-identical file and the
    committed artifact diffs cleanly instead of churning 23 GUIDs a run.
"""

import argparse
import collections
import copy
import hashlib
import json
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

import akn_common as kc  # noqa: E402
import akn_config as kz  # noqa: E402

STAGE = "assemble"

#: Copied verbatim from the template pack. AttachedDecals carries the shared
#: "dunwich_back" decal every fan box uses.
TEMPLATE_KEYS = ("CustomMesh", "AttachedDecals", "ColorDiffuse", "Transform",
                 "HideWhenFaceDown")


def load_pack_trees(cfg, root):
    """{(pack, file): [top-level objects]}, deep-copied out of the source bytes.

    json.loads already returns fresh objects, so this IS the deep copy: nothing
    downstream ever shares a node with anything else.
    """
    trees = collections.OrderedDict()
    packs_dir = kz.packs_root(cfg, root)
    on_disk = dict((kc.nfc(name), name) for name in os.listdir(packs_dir)
                   if os.path.isdir(os.path.join(packs_dir, name)))
    for row in sorted(cfg["packs"], key=lambda r: r["code"]):
        folder = on_disk[kc.nfc(row["folder"])]
        pack_dir = os.path.join(packs_dir, folder)
        for filename in sorted(name for name in os.listdir(pack_dir)
                               if kz.is_pack_file(name)):
            rel = os.path.join(cfg["guard"]["packs_subdir"], folder, filename)
            doc = kz.read_tts_save(cfg, rel, root=root)
            trees[(row["code"], filename)] = doc["ObjectStates"]
    return trees


def node_at(states, path):
    """The object at an ObjectStates path like "/1/0", or None."""
    indices = [int(part) for part in path.strip("/").split("/")]
    node, siblings = None, states
    for index in indices:
        if index >= len(siblings):
            return None
        node = siblings[index]
        siblings = node.get("ContainedObjects") or []
    return node


def walk_nodes(states):
    """Yield (path, node) depth-first, in the same order akn_scan walks."""
    stack = [("/%d" % i, node) for i, node in reversed(list(enumerate(states)))]
    while stack:
        path, node = stack.pop()
        yield path, node
        kids = node.get("ContainedObjects") or []
        for index in reversed(range(len(kids))):
            stack.append(("%s/%d" % (path, index), kids[index]))


def apply_patches(trees, repairs):
    """Step 2. Returns the number of fields written."""
    written = 0
    for patch in repairs["patches"]:
        states = trees[(patch["pack"], patch["file"])]
        node = node_at(states, patch["path"])
        if node is None or node.get("GUID") != patch["guid"]:
            kc.refuse(kc.EXIT_ASSEMBLE,
                      "repair %s: %s %s no longer resolves to GUID %s"
                      % (patch["row_id"], patch["file"], patch["path"],
                         patch["guid"]),
                      "the source moved between `repair` and `assemble`")
        for field in patch["fields"]:
            value = field["after"]
            if field["field"] == "GMNotes":
                value = json.dumps(value, ensure_ascii=False, indent=2)
            node[field["field"]] = value
            written += 1
    return written


def rewrite_ids(trees, idmap):
    """Step 3. Only GMNotes.id is touched; no other field, and no CardID.

    CardID and CustomDeck keys are TTS ATLAS COORDINATES, not card ids, and the
    corpus carries zero signatures / alternate_ids / deckbuilding / deckOptions /
    TtsZoopGuid fields -- so `id` is the only place an id appears and there are no
    cross-references to chase.
    """
    by_object = {}
    for new_id, entry in idmap["ids"].items():
        for obj in entry["objects"]:
            by_object[(entry["pack"], obj["file"], obj["path"])] = new_id

    rewritten, missed = 0, []
    for (pack, filename), states in trees.items():
        for path, node in walk_nodes(states):
            raw = node.get("GMNotes")
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            md = json.loads(raw)
            new_id = by_object.get((pack, filename, path))
            if new_id is None:
                missed.append("%s %s %s carries GMNotes but no new id"
                              % (pack, filename, path))
                continue
            md["id"] = new_id
            # Re-serialised uniformly for every object, not only the patched ones.
            # The source mixes \r\n and \n inside these strings; TTS parses GMNotes
            # as JSON and nothing reads its whitespace, and a uniform shape makes
            # the committed artifact diffable where the source formatting does not.
            node["GMNotes"] = json.dumps(md, ensure_ascii=False, indent=2)
            rewritten += 1
    if missed:
        kc.refuse(kc.EXIT_ASSEMBLE,
                  "%d object(s) carry GMNotes that `renumber` never mapped"
                  % len(missed), "; ".join(missed[:8]))
    return rewritten


def normalise_urls(node, before, after, counter):
    """Step 4. Every string leaf, exact-prefix, path preserved."""
    if isinstance(node, dict):
        for key in list(node):
            node[key] = normalise_urls(node[key], before, after, counter)
        return node
    if isinstance(node, list):
        return [normalise_urls(item, before, after, counter) for item in node]
    if isinstance(node, str) and node.startswith(before):
        counter[0] += 1
        return after + node[len(before):]
    return node


def derive_guid(seed, key, taken):
    """sha256(seed + key)[:6], incremented on collision. Deterministic."""
    guid = hashlib.sha256((seed + key).encode("utf-8")).hexdigest()[:6]
    while guid in taken:
        guid = "%06x" % ((int(guid, 16) + 1) % (16 ** 6))
    taken.add(guid)
    return guid


def ml_layout(cfg, guids):
    """Step 7. A deterministic grid; `pos` spacing measured from the template."""
    grid = cfg["bag"]["ml_grid"]
    cells = grid["columns"] * grid["rows"]
    if len(guids) > cells:
        kc.refuse(kc.EXIT_ASSEMBLE,
                  "%d sub-bags do not fit a %dx%d grid"
                  % (len(guids), grid["columns"], grid["rows"]),
                  "widen bag.ml_grid in the config")
    origin, step = grid["origin"], grid["step"]
    layout = collections.OrderedDict()
    for index, guid in enumerate(guids):
        column, row = index % grid["columns"], index // grid["columns"]
        layout[guid] = collections.OrderedDict([
            ("lock", grid["lock"]),
            ("pos", collections.OrderedDict([
                ("x", round(origin["x"] + column * step, 4)),
                ("y", origin["y"]),
                ("z", round(origin["z"] + row * step, 4))])),
            ("rot", collections.OrderedDict([
                ("x", grid["rot"]["x"]), ("y", grid["rot"]["y"]),
                ("z", grid["rot"]["z"])])),
        ])
    return layout


def build_bag(cfg, trees, inventory, workspace=None):
    """Steps 5 to 7. Returns (bag, sub_guids, url_count)."""
    workspace = workspace or kc.WORKSPACE_ROOT
    template_path = os.path.join(workspace, cfg["bag"]["template_source"])
    template = json.loads(kc.read_text(template_path))
    missing = [key for key in TEMPLATE_KEYS if key not in template]
    if missing:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the template pack is missing %s" % missing, template_path)

    taken = set(record["guid"] for record in inventory["objects"])
    seed = cfg["bag"]["guid_seed"]
    sub_guids = collections.OrderedDict()
    sub_bags = []
    for row in sorted(cfg["packs"], key=lambda r: r["code"]):
        contained = []
        for (pack, filename), states in trees.items():
            if pack != row["code"]:
                continue
            contained.extend(states)
        guid = derive_guid(seed, row["code"], taken)
        sub_guids[row["code"]] = guid
        sub_bags.append(collections.OrderedDict(sorted({
            "Name": "Bag",
            "Nickname": row["nickname"],
            "GUID": guid,
            "ColorDiffuse": cfg["bag"]["sub_bag"]["color_diffuse"],
            "Transform": cfg["bag"]["sub_bag"]["transform"],
            "HideWhenFaceDown": False,
            "ContainedObjects": contained,
        }.items())))

    top_guid = derive_guid(seed, cfg["bag"]["top_guid_key"], taken)
    layout = ml_layout(cfg, [sub_guids[row["code"]]
                             for row in sorted(cfg["packs"],
                                               key=lambda r: r["code"])])
    bag = {
        "Name": "Custom_Model_Bag",
        "Nickname": cfg["bag"]["nickname"],
        "GUID": top_guid,
        "LuaScript": kc.memory_bag_luascript(
            workspace, cfg["bag"].get("memory_bag_updater")),
        "LuaScriptState": json.dumps({"ml": layout}, separators=(",", ":")),
        "ContainedObjects": sub_bags,
    }
    for key in TEMPLATE_KEYS:
        bag[key] = copy.deepcopy(template[key])
    bag["Transform"]["rotY"] = cfg["bag"]["transform_rot_y"]

    counter = [0]
    norm = cfg["url_normalisation"]
    bag = normalise_urls(bag, norm["from"], norm["to"], counter)
    return collections.OrderedDict(sorted(bag.items())), sub_guids, counter[0]


def collect_output(bag):
    """Every object in the built bag, by GUID, with its Deck shape."""
    found = collections.OrderedDict()

    def visit(node, parent_deck):
        found[node.get("GUID")] = {
            "name": node.get("Name"),
            "tags": node.get("Tags"),
            "gmnotes": node.get("GMNotes"),
            "parent_deck": parent_deck,
            "children": [k.get("GUID") for k in (node.get("ContainedObjects") or [])],
            "deck_ids": node.get("DeckIDs"),
            "custom_deck": sorted((node.get("CustomDeck") or {}).keys()),
        }
        inner = node.get("GUID") if node.get("Name") == "Deck" else parent_deck
        for kid in (node.get("ContainedObjects") or []):
            visit(kid, inner)

    visit(bag, None)
    return found


def run_assemble(cfg, run_dir, mode="build", workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    kz.assert_predecessors(run_dir, STAGE, workspace)
    paths = dict((name, os.path.join(run_dir, name + ".json"))
                 for name in ("inventory", "idmap", "repairs"))
    for name, path in sorted(paths.items()):
        if not os.path.exists(path):
            kc.refuse(kc.EXIT_PRECONDITION, "%s.json is missing" % name, path)
    inventory = json.loads(kc.read_text(paths["inventory"]))
    idmap = json.loads(kc.read_text(paths["idmap"]))
    repairs = json.loads(kc.read_text(paths["repairs"]))

    root = kz.source_root(cfg, workspace=workspace)
    digest, _files, _bytes = kc.sha256_tree(root)
    if digest != inventory["source_tree_sha256"]:
        kc.refuse(kc.EXIT_DRIFT,
                  "docs/Arknights/ changed since `scan` hashed it",
                  "re-run `arknights.sh scan`; every artifact downstream of it "
                  "describes a tree that is no longer there")

    trees = load_pack_trees(cfg, root)
    patched = apply_patches(trees, repairs)
    rewritten = rewrite_ids(trees, idmap)
    bag, sub_guids, urls = build_bag(cfg, trees, inventory, workspace)

    text = json.dumps(bag, indent=2, ensure_ascii=False) + "\n"
    output = collect_output(bag)
    checks = build_checks(cfg, inventory, idmap, repairs, bag, output, sub_guids,
                          urls, text)

    out_path = os.path.join(run_dir, "arknights.json")
    triggered = [c["exit_on_fail"] for c in checks if c["status"] == "fail"]
    if mode == "build" and not triggered:
        kz.write_guarded(cfg, out_path, text, workspace=workspace)

    counts = collections.OrderedDict([
        ("objects_in", inventory["counts"]["objects"]),
        ("objects_out", len(output) - 1 - len(sub_guids)),
        ("containers", len(sub_guids) + 1),
        ("guids", len(output)),
        ("sub_bags", len(sub_guids)),
        ("decks", sum(1 for v in output.values() if v["name"] == "Deck")),
        ("patched_fields", patched),
        ("ids_rewritten", rewritten),
        ("urls_normalised", urls),
        ("bytes", len(text.encode("utf-8"))),
    ])
    report = kc.new_report(
        STAGE, os.path.basename(run_dir), mode=mode, counts=dict(counts),
        checks=checks,
        binding=kc.build_binding([cfg["_path"]] + sorted(paths.values())
                                 + [out_path]),
        results={"output": os.path.relpath(out_path, workspace),
                 "top_guid": bag["GUID"],
                 "sub_bag_guids": dict(sub_guids)})
    report["write_set"] = [os.path.relpath(out_path, workspace)]
    kc.finalize_report(report, triggered=triggered)
    return report, text


def build_checks(cfg, inventory, idmap, repairs, bag, output, sub_guids, urls,
                 text):
    """The assembly rules, all exit 85."""
    checks = []
    source = dict((r["guid"], r) for r in inventory["objects"])

    checks.append(kc.check(
        "A1", "every source object survived",
        ["%s %s %s (%s) is not in the output"
         % (r["pack"], r["file"], r["path"], r["guid"])
         for r in inventory["objects"] if r["guid"] not in output],
        exit_on_fail=kc.EXIT_ASSEMBLE, subject_size=len(source)))

    # THE DECK PROHIBITION. The child GUID SEQUENCE, not the set: a reordering
    # inside a Deck is a different deck, and a set comparison cannot see one.
    deck_detail = []
    for record in inventory["objects"]:
        if record["name"] != "Deck":
            continue
        built = output.get(record["guid"])
        if built is None:
            continue  # A1 already names it
        if built["children"] != record["children"]:
            deck_detail.append("Deck %s children moved: %s -> %s"
                               % (record["guid"], record["children"],
                                  built["children"]))
        if built["deck_ids"] != record["deck_ids"]:
            deck_detail.append("Deck %s DeckIDs changed" % record["guid"])
        if built["custom_deck"] != record["custom_deck"]:
            deck_detail.append("Deck %s CustomDeck keys changed" % record["guid"])
    decks_in = sum(1 for r in inventory["objects"] if r["name"] == "Deck")
    decks_out = sum(1 for v in output.values() if v["name"] == "Deck")
    if decks_in != decks_out:
        deck_detail.append("%d Decks in, %d out" % (decks_in, decks_out))
    checks.append(kc.check("A2", "Deck integrity", deck_detail,
                           exit_on_fail=kc.EXIT_ASSEMBLE, subject_size=decks_in))

    # No object may cross a file boundary into another file's Deck.
    cross = []
    parent_of = dict((r["guid"], r["parent_deck"]) for r in inventory["objects"])
    for guid, built in output.items():
        if guid not in parent_of:
            continue
        if built["parent_deck"] != parent_of[guid]:
            cross.append("%s moved from Deck %s to %s"
                         % (guid, parent_of[guid], built["parent_deck"]))
    checks.append(kc.check("A3", "no object changed Deck", cross,
                           exit_on_fail=kc.EXIT_ASSEMBLE, subject_size=len(parent_of)))

    expected_guids = len(inventory["objects"]) + len(sub_guids) + 1
    checks.append(kc.check(
        "A4", "GUID uniqueness",
        [] if len(output) == expected_guids
        else ["%d distinct GUIDs, expected %d source + %d container = %d"
              % (len(output), len(inventory["objects"]), len(sub_guids) + 1,
                 expected_guids)],
        exit_on_fail=kc.EXIT_ASSEMBLE, subject_size=len(output)))

    survivors = text.count(cfg["url_normalisation"]["from"])
    checks.append(kc.check(
        "A5", "url normalisation",
        (["%d cloud-3 reference(s) survived" % survivors] if survivors else [])
        + ([] if urls == cfg["url_normalisation"]["expected"]
           else ["normalised %d, `scan` recorded %d"
                 % (urls, cfg["url_normalisation"]["expected"])]),
        exit_on_fail=kc.EXIT_ASSEMBLE, note="%d normalised, %d survivors"
        % (urls, survivors)))

    # Every object that carries GMNotes carries a NEW id and no source id.
    old_ids = set()
    for record in inventory["objects"]:
        if record["source_id"]:
            old_ids.add(record["source_id"])
    id_detail = []
    for guid, built in output.items():
        if not built["gmnotes"]:
            continue
        md = json.loads(built["gmnotes"])
        if md.get("id") in old_ids:
            id_detail.append("%s still carries the source id %r" % (guid, md["id"]))
        if md.get("id") not in idmap["ids"]:
            id_detail.append("%s carries %r, which is not in the id map"
                             % (guid, md.get("id")))
    checks.append(kc.check("A6", "ids rewritten", id_detail,
                           exit_on_fail=kc.EXIT_ASSEMBLE))

    # Every repair patch is visible in the output.
    patch_detail = []
    for patch in repairs["patches"]:
        built = output.get(patch["guid"])
        if built is None:
            patch_detail.append("%s: %s is not in the output"
                                % (patch["row_id"], patch["guid"]))
            continue
        for field in patch["fields"]:
            if field["field"] == "Tags" and built["tags"] != field["after"]:
                patch_detail.append("%s: %s Tags are %r, expected %r"
                                    % (patch["row_id"], patch["guid"],
                                       built["tags"], field["after"]))
            if field["field"] == "GMNotes":
                md = json.loads(built["gmnotes"] or "{}")
                want = dict(field["after"])
                want["id"] = md.get("id")  # the id map is authoritative here
                if md != want:
                    patch_detail.append("%s: %s GMNotes are %r, expected %r"
                                        % (patch["row_id"], patch["guid"], md, want))
    checks.append(kc.check("A7", "repairs applied", patch_detail,
                           exit_on_fail=kc.EXIT_ASSEMBLE,
                           subject_size=len(repairs["patches"])))

    layout = json.loads(bag["LuaScriptState"])["ml"]
    checks.append(kc.check(
        "A8", "TTS shape and the ml layout",
        ([] if bag["Name"] == "Custom_Model_Bag" else
         ["root Name is %r" % bag["Name"]])
        + ([] if bag["Nickname"] == cfg["bag"]["nickname"] else
           ["root Nickname is %r" % bag["Nickname"]])
        + ([] if len(bag["ContainedObjects"]) == len(cfg["packs"]) else
           ["%d sub-bags, expected %d" % (len(bag["ContainedObjects"]),
                                          len(cfg["packs"]))])
        + ["sub-bag %d is Name %r, not Bag" % (i, sub.get("Name"))
           for i, sub in enumerate(bag["ContainedObjects"])
           if sub.get("Name") != "Bag"]
        + ["a sub-bag carries %s, which a plain Bag must not" % key
           for sub in bag["ContainedObjects"]
           for key in ("LuaScript", "LuaScriptState") if key in sub]
        + ([] if set(layout) == set(sub_guids.values()) else
           ["the ml key set does not equal the sub-bag GUIDs"]),
        exit_on_fail=kc.EXIT_ASSEMBLE, subject_size=len(bag["ContainedObjects"])))
    return checks


# ---------------------------------------------------------------------------
# --selftest
# ---------------------------------------------------------------------------

FAULTS = ("guid", "layout", "urls", "walk", "memorybag")


def selftest(fault=None, verbose=True):
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))
    cfg = kz.load_config()

    if "guid" in wanted:
        first = derive_guid("arknights:v1:", "00", set())
        again = derive_guid("arknights:v1:", "00", set())
        if first != again:
            findings.append("guid: not deterministic (%s vs %s)" % (first, again))
        if len(first) != 6 or any(c not in "0123456789abcdef" for c in first):
            findings.append("guid: %r is not 6 lowercase hex characters" % first)
        bumped = derive_guid("arknights:v1:", "00", set([first]))
        if bumped == first or int(bumped, 16) != (int(first, 16) + 1) % (16 ** 6):
            findings.append("guid: a collision did not increment (%s -> %s)"
                            % (first, bumped))
        # 22 packs plus the top bag must all be distinct in one pass.
        taken = set()
        minted = [derive_guid("arknights:v1:", row["code"], taken)
                  for row in cfg["packs"]] + [derive_guid("arknights:v1:", "top",
                                                          taken)]
        if len(set(minted)) != 23:
            findings.append("guid: the 23 containers are not distinct")

    if "layout" in wanted:
        guids = ["%06x" % i for i in range(22)]
        layout = ml_layout(cfg, guids)
        if list(layout) != guids:
            findings.append("layout: key order does not follow the sub-bag order")
        positions = set((v["pos"]["x"], v["pos"]["z"]) for v in layout.values())
        if len(positions) != 22:
            findings.append("layout: %d distinct cells for 22 sub-bags"
                            % len(positions))
        for entry in layout.values():
            if entry["rot"]["y"] != cfg["bag"]["ml_grid"]["rot"]["y"]:
                findings.append("layout: rot.y is not the configured value")
                break
        try:
            ml_layout(cfg, ["%06x" % i for i in range(99)])
        except kc.AknRefusal as exc:
            if exc.code != kc.EXIT_ASSEMBLE:
                findings.append("layout: an overfull grid gave exit %d" % exc.code)
        else:
            findings.append("layout: an overfull grid was not refused")

    if "urls" in wanted:
        norm = cfg["url_normalisation"]
        counter = [0]
        node = {"a": norm["from"] + "ugc/1/A/",
                "b": [norm["to"] + "ugc/2/B/", {"c": norm["from"] + "ugc/3/C/"}],
                "d": "http://cloud-3.steamusercontent.example/nope"}
        out = normalise_urls(node, norm["from"], norm["to"], counter)
        if counter[0] != 2:
            findings.append("urls: counted %d, expected 2" % counter[0])
        if out["a"] != norm["to"] + "ugc/1/A/":
            findings.append("urls: the path was not preserved (%r)" % out["a"])
        if out["b"][0] != norm["to"] + "ugc/2/B/":
            findings.append("urls: an already-akamaihd reference was disturbed")
        if out["d"] != node["d"]:
            findings.append("urls: a near-miss host was rewritten -- the match "
                            "must be an exact prefix")

    if "walk" in wanted:
        states = [{"GUID": "a", "ContainedObjects": [
            {"GUID": "b"}, {"GUID": "c", "ContainedObjects": [{"GUID": "d"}]}]},
            {"GUID": "e"}]
        seen = [(p, n["GUID"]) for p, n in walk_nodes(states)]
        if seen != [("/0", "a"), ("/0/0", "b"), ("/0/1", "c"), ("/0/1/0", "d"),
                    ("/1", "e")]:
            findings.append("walk: depth-first order or paths are wrong (%s)" % seen)
        if node_at(states, "/0/1/0")["GUID"] != "d":
            findings.append("walk: node_at does not resolve a nested path")

    if "memorybag" in wanted:
        script = kc.memory_bag_luascript()
        updater = kc.load_module_by_path(
            "akn_mb_check", os.path.join(kc.WORKSPACE_ROOT,
                                         cfg["bag"]["memory_bag_updater"]))
        if updater.SEARCH_TEXT not in script and updater.SEARCH_TEXT_2 not in script:
            findings.append("memorybag: the built script carries neither marker "
                            "the updater searches for, so the updater would not "
                            "recognise our file as a memory bag")
        if 'require("MemoryBag")' not in script:
            findings.append("memorybag: the bundle does not require MemoryBag")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_assemble.py",
        description="arknights `assemble` -- apply, nest, and build the bag.")
    parser.add_argument("--run-dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    kc.check_invocation_guards()

    if args.selftest is not None:
        fault = args.selftest or None
        print("akn_assemble --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_VERIFY
        print("  ok: deterministic GUID derivation and its collision bump, the ml "
              "grid and its capacity refusal, exact-prefix url normalisation, the "
              "depth-first walk and path resolution, and the MemoryBag construction")
        return kc.EXIT_OK

    if not args.run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    cfg = kz.load_config()
    mode = "dry-run" if args.dry_run else "build"
    report, _text = run_assemble(cfg, args.run_dir, mode=mode)
    if mode == "build":
        kc.write_report(report, args.run_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    counts = report["counts"]
    print("arknights assemble -- %s" % report["run_id"])
    print("  output      : %s  %.1f KB"
          % (report["results"]["output"], counts["bytes"] / 1024.0))
    print("  objects     : %d source in %d sub-bags + 1 top bag = %d GUIDs"
          % (counts["objects_out"], counts["sub_bags"], counts["guids"]))
    print("  decks       : %d, copied whole" % counts["decks"])
    print("  rewrites    : %d ids, %d repair fields, %d urls"
          % (counts["ids_rewritten"], counts["patched_fields"],
             counts["urls_normalised"]))
    print("  top GUID    : %s" % report["results"]["top_guid"])
    for entry in report["checks"]:
        print("  %-4s %-28s %s" % (entry["id"], entry["name"], entry["status"]))
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
