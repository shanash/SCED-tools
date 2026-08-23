#!/usr/bin/python3
"""koreanize's langpack writer -- the SOLE writer into SCED-downloads (design §5.3).

INTERPRETER TIER: stdlib (`#!/usr/bin/python3`, Apple's 3.9.6 xcode_select shim).
This module is in `kz_common.STDLIB_TIER`, so it may import only the stdlib
allowlist plus `kz_common`, `kz_config` and `sced_io` -- enforced twice, by the
AST scan and by a `/usr/bin/python3 -m py_compile` subprocess (§5.9). In
particular it may NOT import `kz_source`, which is art tier; it reads that
stage's report off disk instead, through `resolution_of()` below.

SUBMODES

    scaffold   create the override files, carrying the ENGLISH urls        v0
    reuse      overwrite them with donor art, per §3.5                     v0
    objtext    write Nickname/Description from the adopted donor           v0
    register   create the scenario container and append it to the pack     v0
    revert     the inverse stage -- first class, and exempt from the DAG   v0
    repoint    substitute atlas urls, under the byte-level gate            v1

WHAT IS SCOPED, AND WHY THE SCOPING IS THE POINT

v0 has no manufacture path, so all four writing submodes cover the REUSE SUBSET
-- the objects `source` decided `reuse` -- and DECLARE the remainder rather than
covering it (§1.2). Unscoped, `scaffold` would plan one override per object in
the population (298 over the eight Challenge Scenarios) while `reuse` writes only
the 226 it has donor art for; the other 72 would be override files carrying the
ENGLISH FaceURL, sitting inside a container the mod advertises as Korean, in a
set `register`'s scoped acceptance is scoped precisely out of seeing. That is
N-9's own defect class -- "11 unregistered files were dropped silently with rc 0"
-- reproduced by the fix for a different finding. Scoped, those 72 objects get no
override file at all and TTS resolves them from the English base object exactly
as it does today: the pack is smaller and honest rather than larger and wrong.

A `scaffold` plan whose length exceeds `counts.reuse` is refused, and
`test_koreanize_langpack.py` carries that as a negative.

THREE PROPERTIES THAT ARE NOT OPTIONAL

1.  THE PATH GUARD IS RE-ASSERTED INTERNALLY, BEFORE ANY READ. `guard.forbidden`
    lists "SCED-tools/", and this module's absolute refusal-before-read is the
    property §3.1 preserves by putting the `data_root` exemption in `kz_config`
    instead of here. The lock receipt is therefore HANDED to `kz_config`'s mirror
    writer; this module never opens a path under `SCED-tools/`.

2.  `sort_keys` IS A PROPERTY OF THE MAPPING, NOT A FAVOUR ASKED OF THE WRITER.
    `sced_io.py:99` serialises with `json.dump(data, fh, indent=2,
    ensure_ascii=False)` and passes NO `sort_keys`. That coincides with the A1
    byte convention for `repoint`, which mutates an existing mapping in place and
    inherits its key order, and DIVERGES for `scaffold` and `reuse`, which build
    a mapping from scratch and would otherwise emit construction order -- bytes
    that change the day a constructor is reordered, in files whose whole
    verification story is byte comparison. Every object is therefore built
    through `_sorted()` and asserted key-sorted before the batch is handed over.

3.  AN INTENTIONS LOG IS WRITTEN BEFORE PHASE 2, BECAUSE `write_set[]` CANNOT
    DESCRIBE A HALF-COMMITTED BATCH. `atomic_write_json_batch` stages every temp
    file in phase 1 and renames them one at a time in phase 2; a crash BETWEEN
    two renames leaves some destinations replaced and some not, and `post_sha256`
    is re-read after the batch returns -- which a crash means it never does. So
    the plan is persisted to `<run_dir>/revert/<stage>/intentions.json` with
    `committed: false` before phase 2, and the report's `write_set[]` is that
    same array with `committed: true` and `post_sha256` re-read from disk.
    `revert` reads whichever it finds (§8.4 row A).
"""

import argparse
import collections
import copy
import json
import os
import shutil
import sys

import kz_common as kc
import kz_config as kz

SCHEMA_VERSION = "1.1.0"

#: Every submode is a STAGE in its own right (§4.1's stage->module->submode
#: table), so each gets its own `<stage>.json` report, its own gate row and its
#: own entry in the AI partition. All six are AI-FORBIDDEN, and declaring that
#: here in the module body is what makes the prohibition mechanized rather than
#: documented: a scenario.json that does not list one of them in
#: ai.forbidden_stages refuses at exit 4 the moment it is bound, and
#: `kz_common.write_report` refuses a build report carrying an `ai` block.
SUBMODES = ("scaffold", "reuse", "objtext", "register", "revert", "repoint")
V1_SUBMODES = frozenset({"repoint"})

for _submode in SUBMODES:
    kc.declare_ai(_submode, required=False)

#: The four §5.3 A1 scaffolder rules that are expressible as a key list. The
#: fifth -- "containers not overridden" -- is a rule about WHICH objects get a
#: file at all and is implemented by the population, not by a key filter.
DROPPED_KEYS = ("Tags", "LuaScript", "LuaScriptState", "LuaScriptState_path",
                "GMNotes_path", "ContainedObjects", "ContainedObjects_order",
                "ContainedObjects_path", "AttachedDecals", "States",
                "HideWhenFaceDown", "SidewaysCard", "Memo", "Locked",
                "Grid", "Snap", "IgnoreFoW", "MeasureMovement", "Autoraise",
                "Sticky", "Tooltip", "GridProjection", "Value")
TRANSFORM_KEYS = ("scaleX", "scaleY", "scaleZ")

#: The presentation fields the scenario container hands to its langpack copy.
#: Everything else -- Description, GMNotes, LuaScript, LuaScriptState_path -- is
#: deliberately absent: "containers not overridden" (§5.3) means the container is
#: present STRUCTURALLY, to hold the children, and carries no translated text.
#: Measured against the shipped Midwinter Korean container, whose keys are
#: exactly this set plus the two ContainedObjects_* fields.
CONTAINER_KEYS = ("AttachedDecals", "ColorDiffuse", "CustomMesh", "GUID",
                  "Hands", "HideWhenFaceDown", "Name", "Nickname", "Transform")

LANGPACK_ROOT = "SCED-downloads/decomposed/language-pack"
DOWNLOADS_ROOT = "SCED-downloads"


# ---------------------------------------------------------------------------
# 1. Small readers -- this module reads its inputs, it does not import them
# ---------------------------------------------------------------------------


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolution_of(report):
    """The per-object decisions from a `source` stage report.

    Duplicated from `kz_source.resolution_of` ON PURPOSE and not imported:
    `kz_source` is art tier and this module is stdlib tier, so importing it fails
    the §5.9 AST scan. Three lines is the right price for the tier boundary; the
    contract it duplicates is one key path and is stated in both docstrings.
    """
    return ((report or {}).get("results") or {}).get("resolution") or []


def load_source(run_dir):
    """(report, resolution[]) with the report asserted CONSUMABLE.

    Exit 13 and not 72: 72 is the DISPATCHER's refusal, taken before any stage
    process started. Reaching this line means a stage ran far enough to read its
    input, which is a different fact and sends the operator to the named file.
    """
    path = kc.report_path(run_dir, "source", "build")
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "source.json is missing",
                  "run `koreanize source` first; %s" % path)
    report = read_json(path)
    if not report.get("consumable"):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the `source` report is not consumable",
                  "%s: %s" % (path, report.get("consumable_blocked_by")))
    return report, resolution_of(report)


def reuse_subset(resolution):
    """The objects `source` decided `reuse`, in `card-text-en.json` order."""
    return [entry for entry in resolution if entry.get("decision") == "reuse"]


def assert_key_sorted(data, what="an override"):
    """§5.3's literal predicate: `json.dumps(obj, sort_keys=True) ==
    json.dumps(obj)` on the built object, before the batch is handed over.

    Kept SEPARATE from `_sorted` deliberately. Fused, the assertion runs only on
    output `sorted_mapping` just produced and is therefore unfalsifiable -- it
    could be deleted without any test noticing, which is the shape of a check
    that is really a comment. Split, it is a predicate over any mapping and
    `test_koreanize_langpack.py` can hand it one in construction order.
    """
    if json.dumps(data, sort_keys=True, ensure_ascii=False) != \
            json.dumps(data, ensure_ascii=False):
        kc.refuse(kc.EXIT_ARTIFACT,
                  "%s was handed over in construction order" % what,
                  "sced_io passes no sort_keys, so the A1 byte convention has to "
                  "be a property of the mapping (§5.3)")
    return data


def _sorted(data):
    """Recursively key-sorted, then asserted so. See property 2 above."""
    return assert_key_sorted(kc.sorted_mapping(data))


# ---------------------------------------------------------------------------
# 2. Destinations
# ---------------------------------------------------------------------------


def object_stem(object_id):
    """The langpack filename stem for a walk-relative scenario object id.

    The Korean container is FLAT -- the shipped Midwinter one is 64 files and not
    one subdirectory -- so a `Deck`-parented object in the scenario tree has an
    ordinary per-object override here, never a container-level one (§5.2 step 5).
    """
    return object_id.replace("\\", "/").rsplit("/", 1)[-1]


def container_dir(cfg, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    return os.path.join(workspace, LANGPACK_ROOT, cfg["pack"],
                        cfg["pack_container"], cfg["container_stem"])


def container_json(cfg, workspace=None):
    return container_dir(cfg, workspace) + ".json"


def pack_container_json(cfg, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    return os.path.join(workspace, LANGPACK_ROOT, cfg["pack"],
                        "%s.json" % cfg["pack_container"])


def object_path(cfg, object_id, workspace=None):
    return os.path.join(container_dir(cfg, workspace),
                        "%s.json" % object_stem(object_id))


# ---------------------------------------------------------------------------
# 3. The A1 override builder (§5.3, reconstructed from the SPEC)
# ---------------------------------------------------------------------------
#
# The A1 scaffolder no longer exists anywhere -- it survived only in an ephemeral
# session scratchpad and a full-tree search finds nothing. This is therefore a
# reconstruction from `.am/midwinter-gala-korean/design.md`'s Block A1 record,
# whose rules are "derived by measuring Carnevale (40 files), Barkham (57) and
# the existing Korean - Player Cards": containers not overridden; GMNotes inlined
# and reduced to {"id": ...}; Tags dropped; Transform scale-only; Description
# omitted when absent.


def build_override(entry, source_obj):
    """One override file's content, from the ENGLISH source object.

    `scaffold` writes the English urls; `reuse` overwrites them with the donor's
    (§1.2). Both branches consume this, and `repoint`'s byte-level substitution
    gate can only substitute urls into files that already exist.
    """
    out = collections.OrderedDict()
    for key, value in source_obj.items():
        if key in DROPPED_KEYS:
            continue
        out[key] = copy.deepcopy(value)

    # Transform is scale-only.
    transform = source_obj.get("Transform") or {}
    out["Transform"] = dict((k, transform[k]) for k in TRANSFORM_KEYS
                            if k in transform)

    # GMNotes inlined and reduced to {"id": ...}. Written with the A1 byte
    # convention so the STRING itself is stable, not merely the mapping around it.
    arkham_id = entry.get("arkham_id")
    if arkham_id:
        out["GMNotes"] = json.dumps({"id": arkham_id}, indent=2,
                                    ensure_ascii=False)
    else:
        out.pop("GMNotes", None)

    # Description omitted when absent -- never written as "" or null.
    if not source_obj.get("Description"):
        out.pop("Description", None)

    out["GUID"] = entry.get("guid") or source_obj.get("GUID")
    return _sorted(out)


def adopt_donor(override, entry, cfg):
    """§3.5's donor-adoption rule, asserted per object.

    THE HAZARD NO PRECEDENT IN THIS REPOSITORY CAN SHOW. Carnevale's Korean
    override and its English source carry the same CardID 4000 and the same 10x7
    grid -- that is SAME-ATLAS harvest, where only FaceURL moves. Cross-scenario
    reuse is not that case: a donor's Korean atlas has the DONOR's grid and the
    DONOR's cell, and TTS resolves a face as CustomDeck[deck_key].FaceURL at cell
    CardID % 100. Retaining the target's English CardID while installing the
    donor's FaceURL yields a valid-LOOKING file that renders the WRONG CARD, and
    every prior Korean pack is same-atlas harvest, so nothing in the tree
    exhibits the distinction.
    """
    donor = entry.get("donor")
    if not donor:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "object %s is decided `reuse` with no donor recorded"
                  % entry.get("object_id"))
    out = collections.OrderedDict(override)
    deck_key = str(donor["deck_key"])
    out["CardID"] = donor["card_id"]

    deck = collections.OrderedDict([
        ("BackIsHidden", donor.get("back_is_hidden", True)),
        ("BackURL", donor.get("back_url")),
        ("FaceURL", donor.get("face_url")),
        ("NumHeight", donor.get("num_height")),
        ("NumWidth", donor.get("num_width")),
        ("Type", donor.get("type", 0)),
    ])

    # A shared back keeps its ENGLISH generic: only the backs this scenario owns
    # become Korean, and the policy that says so is data, not a constant here.
    for shared in cfg.get("shared_backs") or []:
        if shared.get("policy") != "never_rewrite":
            continue
        if shared.get("url") and shared["url"] == (override.get("CustomDeck") or {}).get(
                next(iter(override.get("CustomDeck") or {"x": {}})), {}).get("BackURL"):
            deck["BackURL"] = shared["url"]
            break

    out["CustomDeck"] = {deck_key: deck}
    out = _sorted(out)
    assert_adoption(out, entry, donor)
    return out


def assert_adoption(override, entry, donor):
    """The four §3.5 assertions, stated as a list so each names its own failure."""
    findings = []
    deck_key = str(donor["deck_key"])
    card_id = override.get("CardID")
    if not isinstance(card_id, int):
        findings.append("CardID is %r, not an integer" % card_id)
    else:
        if str(card_id // 100) != deck_key:
            findings.append("CardID // 100 is %s, donor.deck_key is %s"
                            % (card_id // 100, deck_key))
        if card_id % 100 != donor.get("cell"):
            findings.append("CardID %% 100 is %s, donor.cell is %s"
                            % (card_id % 100, donor.get("cell")))
    decks = override.get("CustomDeck") or {}
    if list(decks.keys()) != [deck_key]:
        findings.append("CustomDeck has keys %s, expected exactly [%r]"
                        % (list(decks.keys()), deck_key))
    else:
        deck = decks[deck_key]
        for field, want in (("NumWidth", donor.get("num_width")),
                            ("NumHeight", donor.get("num_height")),
                            ("FaceURL", donor.get("face_url"))):
            if deck.get(field) != want:
                findings.append("CustomDeck[%s].%s is %r, donor's is %r"
                                % (deck_key, field, deck.get(field), want))
    # The object identity is the TARGET's; only the art is the donor's.
    if entry.get("guid") and override.get("GUID") != entry["guid"]:
        findings.append("GUID is %r, the target's is %r"
                        % (override.get("GUID"), entry["guid"]))
    if findings:
        kc.refuse(kc.EXIT_RULE_A,
                  "donor adoption failed for %s" % entry.get("object_id"),
                  "; ".join(findings))
    return True


def apply_objtext(override, entry):
    """`objtext`: Nickname and Description, from the donor override `reuse`
    adopted (§3.5 adopts the donor's whole card identity, TEXT INCLUDED).

    Verbatim from the donor, and NOT translated here: the shipped Korean packs
    keep object text in English and carry the Korean in the art, because Nickname
    and Description drive search and tooltips rather than the rendered card. An
    object whose text this stage invented would disagree with the very donor
    whose art it is displaying.

    Returns (override, wrote) -- `wrote` False when the donor carried no Nickname
    at all, which the O1 check reports by object id rather than skipping.
    """
    donor = entry.get("donor") or {}
    nickname = donor.get("nickname")
    if not nickname:
        return override, False
    out = collections.OrderedDict(override)
    out["Nickname"] = nickname
    description = donor.get("description")
    if description:
        out["Description"] = description
    else:
        # Omitted when absent -- never "" and never null (§5.3).
        out.pop("Description", None)
    return _sorted(out), True


# ---------------------------------------------------------------------------
# 4. The plan
# ---------------------------------------------------------------------------


class PlanEntry(object):
    """One destination.

    `path` is ALWAYS the real destination and is what the path guard and the hard
    cap are evaluated over; `write_to` is where THIS invocation actually writes,
    which is `path` on the live path and the rebased scratch destination on the
    rehearsal path. Keeping them apart is not tidiness: a rehearsal that
    evaluated the guard over its own scratch destinations would be checking the
    scratch root's membership of `guard.write_roots` -- which it is not a member
    of -- so the guard would fire on every rehearsal and never on the thing it
    exists to stop. The two assertions are correspondingly different:
    `assert_write_paths` over `path`, `assert_dry_run_dest` over `write_to`.
    """

    __slots__ = ("path", "write_to", "action", "pre_sha256", "snapshot",
                 "plan_sha256", "data")

    def __init__(self, path, data, snapshot_root, write_to=None):
        self.path = path
        self.write_to = write_to or path
        self.data = data
        exists = os.path.exists(path)
        self.action = "modify" if exists else "create"
        self.pre_sha256 = kc.sha256_file(path) if exists else None
        self.plan_sha256 = kc.sha256_bytes(serialize(data))
        self.snapshot = os.path.join(snapshot_root, os.path.basename(path)) \
            if exists else None

    def record(self, workspace, committed=False, post_sha256=None):
        return collections.OrderedDict([
            ("path", os.path.relpath(self.path, workspace)),
            ("action", self.action),
            ("pre_sha256", self.pre_sha256),
            ("post_sha256", post_sha256),
            ("committed", committed),
            ("plan_sha256", self.plan_sha256),
            ("snapshot", (os.path.relpath(self.snapshot, workspace)
                          if self.snapshot else None)),
        ])


def serialize(data):
    """The A1 byte convention, and the bytes `sced_io` will actually write.

    Delegated to `kz_common.json_bytes` so the convention has ONE owner: a copy
    that drifted from the writer would make every `plan_sha256` wrong, and
    `revert` compares a created file against exactly that value before deleting
    it.
    """
    return kc.json_bytes(data)


class Plan(object):
    def __init__(self, stage, cfg, entries, run_dir, workspace):
        self.stage = stage
        self.cfg = cfg
        self.entries = entries
        self.run_dir = run_dir
        self.workspace = workspace

    @property
    def paths(self):
        return [e.path for e in self.entries]

    def roots(self):
        return sorted({os.path.dirname(os.path.relpath(e.path, self.workspace))
                       for e in self.entries})

    def document(self):
        return collections.OrderedDict([
            ("schema_version", SCHEMA_VERSION),
            ("stage", self.stage),
            ("slug", self.cfg["slug"]),
            ("generated_by", "koreanize %s" % self.stage),
            ("generated_at", kc.utc_now()),
            ("count", len(self.entries)),
            ("roots", self.roots()),
            ("entries", [collections.OrderedDict([
                ("path", os.path.relpath(e.path, self.workspace)),
                ("action", e.action),
                ("pre_sha256", e.pre_sha256),
                ("plan_sha256", e.plan_sha256),
            ]) for e in self.entries]),
        ])


def plan_path(run_dir, stage):
    return os.path.join(run_dir, "%s.plan.json" % stage)


def snapshot_root(run_dir, stage):
    return os.path.join(run_dir, "revert", stage)


def build_plan(stage, cfg, run_dir, workspace=None, live_root=None):
    """The planned write set for one submode. Reads; writes nothing.

    `live_root` rebases every destination onto a scratch root, which is what
    `--dry-run` uses: §4.1 pins that root to `<run_dir>/dry-run/<stage>/` rather
    than to a SIBLING of the output directory, because this module's outputs are
    under `guard.write_roots` and a sibling of one of them lands INSIDE the
    guarded tree -- where the guard, which refuses writes OUTSIDE the roots,
    could never fire on it.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    snaps = snapshot_root(run_dir, stage)

    if stage == "register":
        pairs = _plan_register(cfg, run_dir, workspace)
    else:
        pairs = _plan_objects(stage, cfg, run_dir, workspace)

    entries = []
    for path, data in pairs:
        write_to = path
        if live_root:
            write_to = os.path.join(live_root, os.path.relpath(path, workspace))
        entries.append(PlanEntry(path, data, snaps, write_to=write_to))
    return Plan(stage, cfg, entries, run_dir, workspace)


def _english_objects(run_dir):
    card_text = read_json(os.path.join(run_dir, "card-text-en.json"))
    return dict((o["object_id"], o) for o in card_text.get("objects") or [])


def _source_object_json(cfg, obj, workspace):
    return read_json(os.path.join(workspace, obj["source_file"]))


def _plan_objects(stage, cfg, run_dir, workspace):
    """scaffold / reuse / objtext / repoint -- one file per reuse-subset object."""
    _report, resolution = load_source(run_dir)
    subset = reuse_subset(resolution)
    objects = _english_objects(run_dir)

    pairs = []
    for entry in subset:
        obj = objects.get(entry["object_id"])
        if obj is None:
            kc.refuse(kc.EXIT_DRIFT,
                      "source.json names an object card-text-en.json does not",
                      entry["object_id"])
        source_obj = _source_object_json(cfg, obj, workspace)
        override = build_override(entry, source_obj)
        if stage in ("reuse", "objtext", "repoint"):
            override = adopt_donor(override, entry, cfg)
        if stage == "objtext":
            override, _wrote = apply_objtext(override, entry)
        pairs.append((object_path(cfg, entry["object_id"], workspace), override))

    # The scoping IS the predicate (§1.3): a plan longer than counts.reuse means
    # the population leaked in, and the 72 English-FaceURL overrides that would
    # produce sit inside a container advertised as Korean, in a set `register`'s
    # scoped acceptance cannot see.
    declared = (_report.get("counts") or {}).get("reuse")
    if declared is not None and len(pairs) > declared:
        kc.refuse(kc.EXIT_ARTIFACT,
                  "the %s plan of %d exceeds counts.reuse %d"
                  % (stage, len(pairs), declared),
                  "v0 plans the REUSE SUBSET, not the population (§1.2)")
    return pairs


def _plan_register(cfg, run_dir, workspace):
    """The two files `register` writes, and why there are two.

    §1.3's `register` row is "set equality between the container directory and
    `ContainedObjects_order`, in BOTH directions, over the objects `objtext`
    wrote" -- so the scenario container object has to exist for that row to have
    a subject at all, and `scaffold`'s plan is pinned at the 226 object files
    (§1.3's `scaffold` and `reuse` rows), which leaves the container to this
    stage. The pack container is the second, and is what §3.6's `registration`
    block records.
    """
    _report, resolution = load_source(run_dir)
    subset = reuse_subset(resolution)
    stems = [object_stem(e["object_id"]) for e in subset]

    scenario_container = os.path.join(
        workspace, cfg["source_dir"], "%s.json" % cfg["container_stem"])
    base = read_json(scenario_container) if os.path.exists(scenario_container) else {}
    container = collections.OrderedDict()
    for key in CONTAINER_KEYS:
        if key in base:
            container[key] = copy.deepcopy(base[key])
    container["ContainedObjects_order"] = stems
    container["ContainedObjects_path"] = cfg["container_stem"]

    pack_path = pack_container_json(cfg, workspace)
    pack = read_json(pack_path) if os.path.exists(pack_path) else {}
    order = list(pack.get("ContainedObjects_order") or [])
    if cfg["container_stem"] not in order:
        order.append(cfg["container_stem"])
    pack = collections.OrderedDict(pack)
    pack["ContainedObjects_order"] = order

    return [(container_json(cfg, workspace), _sorted(container)),
            (pack_path, _sorted(pack))]


# ---------------------------------------------------------------------------
# 5. --live derivation and the banner
# ---------------------------------------------------------------------------


def requires_live(cfg, paths, workspace=None):
    """DERIVED from the planned write set, never matched against a submode list.

    A submode added later inherits the banner without anyone remembering to add
    it. The derivation is stated over `guard.write_roots` rather than over
    "outside <run_dir>" because `guard.data_root` is also outside <run_dir> and
    is deliberately NOT banner-bearing (§3.1): a blast-radius prompt whose radius
    is a git-tracked JSON receipt trains the operator to type LIVE without
    reading it.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    roots = cfg["guard"]["write_roots"]
    for path in paths:
        rel = os.path.relpath(os.path.realpath(str(path)),
                              os.path.realpath(workspace)).replace(os.sep, "/")
        for root in roots:
            if rel.startswith(root) or rel == root.rstrip("/"):
                return True
    return False


def banner(plan):
    lines = ["", "=" * 72,
             "  LIVE WRITE -- koreanize %s -- %s" % (plan.stage, plan.cfg["slug"]),
             "=" * 72,
             "  files planned : %d  (cap %d)"
             % (len(plan.entries), plan.cfg["guard"]["max_files_written"]),
             "  create/modify : %d / %d"
             % (sum(1 for e in plan.entries if e.action == "create"),
                sum(1 for e in plan.entries if e.action == "modify")),
             "  roots         :"]
    for root in plan.roots():
        lines.append("      %s" % root)
    lines.append("  snapshots     : %s"
                 % os.path.relpath(snapshot_root(plan.run_dir, plan.stage),
                                   plan.workspace))
    lines.append("=" * 72)
    return "\n".join(lines)


def revert_banner(stage, source, entries, workspace=None):
    """`revert`'s OWN banner, and it is deliberately not the write-set one.

    §8.4 makes `revert` a first-class stage -- `--live`, its own banner, exit 24 --
    and the generic banner is the wrong shape for it twice over: it is built from
    a `<stage>.plan.json` that `revert` does not have, and it describes files
    about to be CREATED when the blast radius here is files about to be deleted
    and restored. An operator reading "23 files planned, 23 create" before a
    deletion is being told the opposite of what is about to happen.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    creates = [e for e in entries if e.get("action") == "create"]
    modifies = [e for e in entries if e.get("action") == "modify"]
    lines = ["", "=" * 72,
             "  LIVE REVERT -- koreanize revert --stage %s" % stage,
             "=" * 72,
             "  record        : %s" % source,
             "  will DELETE   : %d file(s) created by that stage" % len(creates),
             "  will RESTORE  : %d file(s) from their pre-write snapshots"
             % len(modifies),
             "  roots         :"]
    for root in sorted({os.path.dirname(e["path"]) for e in entries
                        if isinstance(e.get("path"), str)}):
        lines.append("      %s" % root)
    lines.append("=" * 72)
    return "\n".join(lines)


def confirm_revert(stage, source, entries, assume_yes=False, workspace=None,
                   stream=None):
    stream = stream or sys.stderr
    stream.write(revert_banner(stage, source, entries, workspace) + "\n")
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        kc.refuse(kc.EXIT_USAGE,
                  "a live revert needs a tty to confirm, or --yes")
    stream.write("Type LIVE to proceed: ")
    stream.flush()
    return sys.stdin.readline().strip() == "LIVE"


def confirm_live(plan, assume_yes=False, stream=None):
    """Returns True to proceed. The typed-LIVE prompt, matching sced-run-now.sh.

    `koreanize.sh` prints its own banner and passes --yes, so this is the path a
    DIRECT module invocation takes. Exit 73 is the dispatcher's "declined at the
    banner" code and is reused verbatim here so an operator reads one code for
    one fact regardless of which entry point produced it.
    """
    stream = stream or sys.stderr
    stream.write(banner(plan) + "\n")
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        kc.refuse(kc.EXIT_USAGE,
                  "--live needs a tty to confirm, or --yes",
                  "koreanize.sh prints the banner itself and passes --yes")
    stream.write("Type LIVE to proceed: ")
    stream.flush()
    return sys.stdin.readline().strip() == "LIVE"


# ---------------------------------------------------------------------------
# 6. The commit -- snapshot, intentions, batch, re-read
# ---------------------------------------------------------------------------


def take_snapshots(plan):
    """Byte-for-byte copies of every planned destination that already exists.

    This is what makes `revert` an INVERSE rather than a guess (§8.4).
    """
    root = snapshot_root(plan.run_dir, plan.stage)
    if not os.path.isdir(root):
        os.makedirs(root)
    for entry in plan.entries:
        if entry.snapshot:
            shutil.copyfile(entry.path, entry.snapshot)
    return root


def write_intentions(plan):
    """The pre-phase-2 log. See property 3 in the module docstring."""
    path = os.path.join(snapshot_root(plan.run_dir, plan.stage), "intentions.json")
    kc.atomic_write_json(path, collections.OrderedDict([
        ("schema_version", SCHEMA_VERSION),
        ("stage", plan.stage),
        ("slug", plan.cfg["slug"]),
        ("generated_at", kc.utc_now()),
        ("entries", [e.record(plan.workspace, committed=False)
                     for e in plan.entries]),
    ]))
    return path


def commit(plan):
    """Phase 1 + phase 2, then re-read every destination FROM DISK."""
    kc.atomic_write_json_batch(dict((e.path, e.data) for e in plan.entries))
    write_set = []
    for entry in plan.entries:
        post = kc.sha256_file(entry.path) if os.path.exists(entry.path) else None
        if post != entry.plan_sha256:
            kc.refuse(kc.EXIT_ARTIFACT,
                      "a committed file does not match the bytes it was planned "
                      "with", "%s: on disk %s, planned %s"
                      % (entry.path, (post or "<absent>")[:16],
                         entry.plan_sha256[:16]))
        write_set.append(entry.record(plan.workspace, committed=True,
                                      post_sha256=post))
    return write_set


def assert_plan_matches(plan, persisted):
    """The two-process comparison §1.3's `scaffold` row denies being a tautology.

    "the executed write_set[] equals the plan the --live banner printed" is a
    statement one process makes about itself unless the plan is an ARTIFACT. So
    the rehearsal writes `<run_dir>/<stage>.plan.json`, the live invocation
    carries its sha256 in `binding{}` and asserts equality against it -- same
    paths, same ORDER, same length.
    """
    want = persisted.get("entries") or []
    got = plan.document()["entries"]
    if len(want) != len(got):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the persisted plan has %d entries, this run planned %d"
                  % (len(want), len(got)),
                  "re-run the rehearsal; the plan is stale")
    for index, (a, b) in enumerate(zip(want, got)):
        if a.get("path") != b["path"]:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the persisted plan diverges at entry %d" % index,
                      "%s != %s" % (a.get("path"), b["path"]))
        if a.get("plan_sha256") != b["plan_sha256"]:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the planned bytes for %s have moved since the banner"
                      % b["path"],
                      "%s != %s" % ((a.get("plan_sha256") or "")[:16],
                                    b["plan_sha256"][:16]))
    return True


# ---------------------------------------------------------------------------
# 7. The nightly interlock's two records -- the base, and the stamp (§5.10)
# ---------------------------------------------------------------------------


def base_sha(workspace=None):
    """The `origin/korean` sha this write set was planned against.

    §5.10 HAZARD A, and it is the one that actually bites. The nightly
    FORCE-PUSHES `korean` on every successful night; the rebase happens in a
    detached scratch worktree, so uncommitted koreanize writes are invisible to
    it and survive untouched. What moves is the BASE. Nothing is lost -- the plan
    is simply stale -- so re-running is right and `revert` is WRONG, which is why
    this is recorded and reported rather than made a refusal. A PROBE would close
    nothing: it is a TOCTOU race, and nothing stops a write that begins at 02:16
    from still running when the driver takes the lock at 02:17.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    downloads = os.path.join(workspace, DOWNLOADS_ROOT)
    try:
        return kc.git_stdout("rev-parse", ["origin/korean"], cwd=downloads).strip()
    except (kc.KzRefusal, OSError):
        return None


def stamp_path(slug, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    return os.path.join(workspace, ".local-sync", "run",
                        "koreanize-%s.stamp" % slug)


def write_stamp(cfg, plan, workspace=None):
    """The corroboration a SILENT NIGHT is diagnosed from.

    If koreanize holds the workspace lock when the driver fires, the driver hits
    a BARE `exit 3` at daily-sync-local.sh:1314 -- before `trap cleanup EXIT` is
    installed at :1318 and before the log tee at :1321. `finish()` never runs, so
    no state is written, no Discord message of any grade is sent, and no log file
    is created; and `3` is not in the dispatch allow-list at :585-589, which is
    default-deny, so no failover fires either. The night is structurally
    invisible to a runbook indexed by Discord grade.

    The entry point that finds it is `sced-run-now.sh --status`, which prints
    `lock : held (<first line of owner>)` -- and that line is
    `pid=... repo=koreanize started=... host=...`, which is the whole diagnosis.
    This file is what turns that into "which stage, which roots, how many files".
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    path = stamp_path(cfg["slug"], workspace)
    kc.atomic_write_json(path, collections.OrderedDict([
        ("stage", plan.stage),
        ("submode", plan.stage),
        ("pid", os.getpid()),
        ("started_at", kc.utc_now()),
        ("write_roots", plan.roots()),
        ("planned_files", len(plan.entries)),
        ("base_sha", base_sha(workspace)),
    ]))
    return path


def remove_stamp(cfg, workspace=None):
    """Removed under the SAME compare-and-delete rule as the lock: a stamp this
    process did not write is another process's diagnosis, not litter."""
    path = stamp_path(cfg["slug"], workspace)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if json.load(handle).get("pid") != os.getpid():
                return False
        os.unlink(path)
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 8. register's set equality (the N-9 assertion)
# ---------------------------------------------------------------------------


def register_set_equality(cfg, stems, workspace=None):
    """Both directions, over the objects `objtext` wrote.

    "`ContainedObjects_order` is the manifest, not the directory -- 11
    unregistered files were silently dropped with rc 0." One direction catches
    the file nobody registered; the other catches the order entry with no file.
    """
    directory = container_dir(cfg, workspace)
    on_disk = set()
    if os.path.isdir(directory):
        on_disk = {name[:-5] for name in os.listdir(directory)
                   if name.endswith(".json")}
    declared = set(stems)
    unregistered = sorted(on_disk - declared)
    orphaned = sorted(declared - on_disk)
    return unregistered, orphaned


# ---------------------------------------------------------------------------
# 9. The byte-level substitution gate (`repoint`, v1 -- built here per §6 step 6)
# ---------------------------------------------------------------------------


def substitution_gate(paths, substitutions, workspace=None):
    """Each written file must equal the original bytes with ONLY the url
    substrings substituted, then `git diff --numstat` must show every row `1 1`.

    `diff --numstat` is correct HERE AND ONLY HERE, because `repoint` mutates
    files that already exist in a TRACKED container -- the `1 1` shape IS the
    assertion, and it is meaningful precisely because git can see the
    before-bytes. The tracked check is not decoration: an untracked path would
    satisfy the `1 1` rule VACUOUSLY by contributing no row at all, which is the
    §8.4 step 4 defect one submode over.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    downloads = os.path.join(workspace, DOWNLOADS_ROOT)
    rels = [os.path.relpath(p, downloads) for p in paths]
    findings = []

    proc = kc.git("ls-files", ["--error-unmatch"] + rels, cwd=downloads)
    if proc.returncode != 0:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "a repoint destination is not tracked by git",
                  proc.stderr.strip() or "git ls-files --error-unmatch failed")

    numstat = kc.git_stdout("diff", ["--numstat", "--"] + rels, cwd=downloads)
    seen = set()
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        seen.add(path)
        if (added, removed) != ("1", "1"):
            findings.append("%s changed %s/%s lines, expected 1/1"
                            % (path, added, removed))
    for rel in rels:
        if rel not in seen:
            findings.append("%s produced no diff row at all" % rel)
    for path, (old, new) in (substitutions or {}).items():
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        if old in text:
            findings.append("%s still carries the old url" % path)
        if new not in text:
            findings.append("%s does not carry the new url" % path)
    return findings


# ---------------------------------------------------------------------------
# 10. revert (§8.4) -- the inverse stage
# ---------------------------------------------------------------------------


def revert_inputs(run_dir, stage, cfg=None, workspace=None):
    """(entries, source) from whichever of the three records exists.

    §1.2 exempts `revert` from the universal predecessor rule and HAS to: its
    predecessor is by construction the stage that just FAILED, and a failed stage
    is never `consumable`, so applying the rule makes `revert` refuse at the
    dispatcher's exit 72 in exactly the situation §8.4 invokes it for. Its
    precondition is instead the EXISTENCE OF SOMETHING TO UNDO, and absent all
    three there is genuinely nothing to invert -- exit 13 naming the directory it
    looked in, which is a different fact from 72 and sends the operator
    somewhere different.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    root = snapshot_root(run_dir, stage)

    report_file = kc.report_path(run_dir, stage, "build")
    if os.path.exists(report_file):
        report = read_json(report_file)
        if report.get("write_set"):
            return report["write_set"], report_file

    intentions = os.path.join(root, "intentions.json")
    if os.path.exists(intentions):
        return read_json(intentions).get("entries") or [], intentions

    if cfg:
        lock = os.path.join(workspace, cfg["guard"]["data_root"], "locks",
                            "%s.lock.json" % cfg["slug"])
        if os.path.exists(lock):
            stages = (read_json(lock).get("stages") or {}).get(stage) or {}
            if stages.get("write_set"):
                return stages["write_set"], lock

    kc.refuse(kc.EXIT_PRECONDITION,
              "nothing to revert for stage %r" % stage,
              "looked in %s, %s and data/locks/" % (report_file, intentions))


def contained(relpath, workspace):
    """Resolve a RECORDED relpath under the workspace, or return None.

    `os.path.join` DISCARDS its first argument when the second is absolute, so
    joining a recorded path to the workspace is not containment -- the realpath
    comparison is what makes it one. This is the same rule
    `kz_init.contained_path()` applies to `GMNotes_path`, applied here for the
    same reason one level later: a revert record is plain JSON on disk that
    nothing signs, so its `path` is no more trusted than a mod-authored one.
    """
    if not isinstance(relpath, str) or not relpath or "\x00" in relpath:
        return None
    real_ws = os.path.realpath(workspace)
    candidate = os.path.realpath(os.path.join(real_ws, relpath))
    if candidate != real_ws and not candidate.startswith(real_ws + os.sep):
        return None
    return candidate


def revert(run_dir, stage, cfg, workspace=None, apply_changes=True):
    """Restore a `modify`, delete a `create`, refuse anything else at 24.

    `committed: false` means "this path MAY OR MAY NOT have been written", which
    is the state a crash between two phase-2 renames leaves and the state
    `write_set[]` alone cannot express. A `create` is deleted ONLY when the
    file's sha256 equals its `plan_sha256`: anything else is a file this run did
    not write, and deleting it would be the guess `revert` exists not to make.

    THE PATH GUARD IS RE-ASSERTED HERE, AND THE OMISSION WAS A REAL DEFECT.
    Every forward write in this module runs `kz.assert_write_paths` over its real
    destinations before the first byte; `revert` did not, and it is the one entry
    point whose destinations come from a FILE ON DISK rather than from a plan it
    just computed. `revert_inputs()` reads `intentions.json`, a stage report, or
    the lock receipt -- all plain JSON, none signed -- and `os.path.join`
    discards the workspace when handed an absolute path, so an
    `entry["path"]` of `/etc/passwd` resolved there verbatim. Measured before the
    fix: a hand-written `intentions.json` naming an absolute path outside the
    workspace, with `plan_sha256` set to that file's real digest, made `revert`
    delete it and report no finding at all.

    The sha256 rule alone cannot close that, and understanding why is the point:
    it authenticates the record against the DISK STATE ("does this file match
    what the JSON claims"), never the record against its own PROVENANCE ("did
    this run write it"). Both halves are needed, so the guard runs first and a
    path outside it is a finding rather than an action.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    entries, source = revert_inputs(run_dir, stage, cfg, workspace)
    restored, deleted, skipped, findings = [], [], [], []

    # Pass 1: containment and the write-root guard, over EVERY recorded
    # destination, before a single byte is restored or removed. A record that
    # fails is dropped from the work list and reported -- never acted on.
    safe = []
    for entry in entries:
        path = contained(entry.get("path"), workspace)
        if path is None:
            findings.append("%r is not a workspace-relative path this run could "
                            "have written" % (entry.get("path"),))
            continue
        snapshot = entry.get("snapshot")
        snap_path = contained(snapshot, workspace) if snapshot else None
        if snapshot and snap_path is None:
            findings.append("%s: the snapshot %r escapes the workspace"
                            % (entry["path"], snapshot))
            continue
        # A snapshot is not merely "some file in the workspace": §5.3 writes them
        # to <run_dir>/revert/<stage>/, and `revert` copies their bytes OVER a
        # destination the guard just approved. Containment alone would let a
        # record name any readable file in the tree as the thing to restore --
        # the destination would be legitimate and the CONTENT arbitrary, which is
        # the same overwrite primitive one level down.
        if snap_path is not None:
            snap_root = os.path.realpath(snapshot_root(run_dir, stage))
            if not snap_path.startswith(snap_root + os.sep):
                findings.append("%s: the snapshot %r is outside %s"
                                % (entry["path"], snapshot,
                                   os.path.relpath(snap_root, workspace)))
                continue
        if cfg:
            bad = kz.check_write_paths(cfg, [path], workspace=workspace)
            if bad:
                findings.append("%s: outside the path guard -- %s"
                                % (entry["path"], "; ".join(bad)))
                continue
        safe.append((entry, path, snap_path))

    for entry, path, snap_path in safe:
        action = entry.get("action")
        snapshot = entry.get("snapshot")
        present = os.path.exists(path)

        if action == "modify":
            if not snapshot:
                findings.append("%s: a modify with no snapshot" % entry["path"])
                continue
            # snap_path came from pass 1 already contained; re-joining the raw
            # value here would undo exactly the check that was just made.
            if not os.path.exists(snap_path):
                findings.append("%s: the snapshot %s is gone"
                                % (entry["path"], snapshot))
                continue
            if apply_changes:
                shutil.copyfile(snap_path, path)
                if kc.sha256_file(path) != entry.get("pre_sha256"):
                    findings.append("%s: restored bytes do not match pre_sha256"
                                    % entry["path"])
                    continue
            restored.append(entry["path"])
        elif action == "create":
            if not present:
                skipped.append(entry["path"])
                continue
            digest = kc.sha256_file(path)
            if digest != entry.get("plan_sha256"):
                findings.append(
                    "%s: on disk %s but this run planned %s -- not deleting a "
                    "file it did not write"
                    % (entry["path"], digest[:16],
                       (entry.get("plan_sha256") or "<none>")[:16]))
                continue
            if apply_changes:
                os.unlink(path)
            deleted.append(entry["path"])
        else:
            findings.append("%s: unclassifiable action %r"
                            % (entry["path"], action))

    pruned = []
    if apply_changes and deleted:
        pruned = _prune_empty_dirs(cfg, deleted, workspace)

    return {"source": os.path.relpath(source, workspace),
            "restored": restored, "deleted": deleted, "skipped": skipped,
            "pruned": pruned, "findings": findings}


def _prune_empty_dirs(cfg, deleted, workspace):
    """Remove a directory `revert` just emptied, and nothing else.

    `git status --porcelain` is empty either way -- git does not track empty
    directories -- so this is not what makes the §1.3 acceptance predicate hold.
    It matters for the NEXT run: `register_set_equality` lists the container
    directory, and an emptied-but-present one reads as a container that exists
    and has no children, which is a different diagnosis from one that was never
    created.

    The rule is deliberately narrow and self-limiting. Only a parent of a path
    this revert actually deleted is a candidate; it must resolve under
    `guard.write_roots`; and the removal is `os.rmdir`, which FAILS on a
    non-empty directory rather than recursing. So a directory holding anything
    at all -- including a file this run did not write and correctly refused to
    delete -- survives untouched. That is the same restraint as the created-file
    rule: `revert` inverts what it recorded and guesses at nothing.
    """
    roots = [os.path.realpath(os.path.join(workspace, r))
             for r in (cfg or {}).get("guard", {}).get("write_roots", [])]
    if not roots:
        return []
    candidates = sorted({os.path.dirname(os.path.join(workspace, rel))
                         for rel in deleted}, key=len, reverse=True)
    pruned = []
    for directory in candidates:
        real = os.path.realpath(directory)
        # INSIDE a write root, and nothing else. An earlier form also admitted a
        # directory that was an ANCESTOR of a write root, which is reachable
        # rather than theoretical: guard.write_roots contains a FILE root
        # (`.../<pack_container>/<stem>.json`), so after `revert --stage
        # register` its parent -- the whole pack container directory -- became a
        # prune candidate. `os.rmdir` failing on a non-empty directory is what
        # kept that inert, which is a property of the data and not of the rule.
        if not any(real == root.rstrip(os.sep)
                   or real.startswith(root.rstrip(os.sep) + os.sep)
                   for root in roots):
            continue
        try:
            os.rmdir(real)
            pruned.append(os.path.relpath(real, workspace))
        except OSError:
            pass   # not empty, or not ours: leave it exactly as it is
    return pruned


def git_clean_over(paths, workspace=None):
    """§1.3's `revert` acceptance predicate: `git status --porcelain` returns NO
    LINES over the write set. Exercised as an acceptance RUN on a throwaway slug,
    not only reached as an error path."""
    workspace = workspace or kc.WORKSPACE_ROOT
    downloads = os.path.join(workspace, DOWNLOADS_ROOT)
    if not paths:
        return []
    rels = [os.path.relpath(os.path.join(workspace, p), downloads) for p in paths]
    out = kc.git_stdout("status", ["--porcelain", "--"] + rels, cwd=downloads)
    return [line for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 11. The stage
# ---------------------------------------------------------------------------


class _NoLock(object):
    """The interlock, off. `interlock=False` exists for ONE caller -- a test
    sandbox, whose writes are inside its own tmp_path and can collide with
    nothing -- and it is a named parameter rather than an env var precisely so
    that no operator invocation can reach it.
    """

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def run_langpack(stage, run_dir, live=False, assume_yes=False, dry_run=False,
                 workspace=None, mirror=True, confirm=True, interlock=True):
    """Returns (report, plan). Rehearsal is the DEFAULT for every submode."""
    workspace = workspace or kc.WORKSPACE_ROOT
    if stage not in SUBMODES:
        kc.refuse(kc.EXIT_USAGE, "unknown submode %r" % stage,
                  "the submodes are %s" % list(SUBMODES))
    kc.check_invocation_guards([run_dir])

    cfg = kz.load_scenario(os.path.join(run_dir, "scenario.json"))
    if stage in V1_SUBMODES:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%r is a v1 submode and its producer does not exist yet" % stage,
                  "v0 ships scaffold / reuse / objtext / register / revert")

    if live and dry_run:
        kc.refuse(kc.EXIT_USAGE, "--live and --dry-run are contradictory",
                  "rehearsal is the default; --live is what makes it a build")

    # Rehearsal is the DEFAULT for every submode, matching sced-run-now.sh. So
    # forgetting --live produces a rehearsal rather than a refusal, which is the
    # behaviour §4.1 asks for; the derivation below is what decides whether the
    # BANNER is required once a build is actually requested.
    mode = "build" if live else "dry-run"
    live_root = None
    if mode == "dry-run":
        live_root = kz.assert_dry_run_dest(
            cfg, stage, os.path.join(run_dir, "dry-run", stage), workspace)

    plan = build_plan(stage, cfg, run_dir, workspace, live_root=live_root)

    # The guard and the hard cap, BEFORE the first byte, and over the REAL
    # destinations on both paths. Exit 4 and "nothing was read" is still true,
    # because the count is derived from the plan and not from the writes.
    kz.assert_write_paths(cfg, plan.paths, workspace)
    if mode != "build":
        for entry in plan.entries:
            kz.assert_dry_run_dest(cfg, stage, entry.write_to, workspace)

    needs_banner = requires_live(cfg, plan.paths, workspace)

    triggered, checks = [], []
    write_set = None
    registration = None
    counts = collections.OrderedDict([
        ("planned", len(plan.entries)),
        ("created", sum(1 for e in plan.entries if e.action == "create")),
        ("modified", sum(1 for e in plan.entries if e.action == "modify")),
    ])

    _sreport, resolution = load_source(run_dir)
    scounts = _sreport.get("counts") or {}
    subset = reuse_subset(resolution)
    counts["reuse"] = scounts.get("reuse", len(subset))
    if stage == "objtext":
        # §1.3's predicate is "every object whose source.json decision is `reuse`
        # has a Nickname WRITTEN" -- not "has a Korean one". The distinction is
        # measured, not pedantic: the shipped Korean packs keep object text in
        # ENGLISH and carry the Korean in the ART (`AcridMiasma.83c3ec.json`'s
        # Nickname is "Acrid Miasma"; the donor for 01159 gives "Swarm of Rats"),
        # because Nickname and Description drive search and tooltips rather than
        # the rendered card. §3.5 adopts the donor's whole card identity, text
        # included, so mirroring the donor verbatim IS the rule -- and a check
        # asserting Hangul here would fail on every correct run.
        missing = [e["object_id"] for e in subset
                   if not (e.get("donor") or {}).get("nickname")]
        counts["objects_with_text"] = len(subset) - len(missing)
        # §3.4: v0 has no manufacture path, so the declared remainder is exactly
        # the objects `source` could not resolve to a donor. Reported, never
        # silently skipped -- that is what makes v0's coverage DECLARED (§1.2).
        counts["objects_without_korean_text"] = (
            scounts.get("manufacture", 0) + scounts.get("defer", 0)
            + scounts.get("unresolved", 0))
        checks.append({"id": "O1", "name": "nickname_written",
                       "status": "pass" if not missing else "fail",
                       "exit_on_fail": kc.EXIT_RULE_A,
                       "detail": missing[:20]})
        if missing and mode == "build":
            triggered.append(kc.EXIT_RULE_A)

    if mode == "build":
        persisted_path = plan_path(run_dir, stage)
        if not os.path.exists(persisted_path):
            kc.refuse(kc.EXIT_PRECONDITION,
                      "a --live %s has no %s.plan.json to bind against"
                      % (stage, stage),
                      "run the rehearsal first; the banner is built from it")
        assert_plan_matches(plan, read_json(persisted_path))
        # The banner is DERIVED, never matched against a submode list: a submode
        # added later whose plan lands under guard.write_roots inherits it, and
        # one whose plan does not (a data_root receipt, say) is not put behind a
        # typed LIVE for a git-tracked JSON file (§3.1, §4.1).
        if confirm and needs_banner and not confirm_live(plan, assume_yes):
            kc.refuse(kc.EXIT_DISPATCH_LIVE_DECLINED,
                      "--live declined at the banner")

        # §5.10, in this order and not another. The window refusal comes first
        # because it costs nothing; then the lock, taken AFTER the banner has
        # been answered and immediately before the first write. Acquiring across
        # the banner is what hands the lock to the driver on a timer: it declares
        # a lock stale when `started` is older than MAX_RUN_SECONDS (3600 s), and
        # an operator who steps away for an hour with a typed-LIVE prompt open
        # would be exactly that.
        if interlock:
            kz.assert_outside_window()
            lock = kz.NightlyLock()
        else:
            lock = _NoLock()

        with lock:
            if interlock:
                write_stamp(cfg, plan, workspace)
            try:
                take_snapshots(plan)
                write_intentions(plan)
                write_set = commit(plan)
            finally:
                if interlock:
                    remove_stamp(cfg, workspace)
    else:
        kc.atomic_write_json(plan_path(run_dir, stage), plan.document())
        for entry in plan.entries:
            kc.atomic_write_json(entry.write_to, entry.data)

    if stage == "register":
        stems = [object_stem(e["object_id"]) for e in subset]
        unregistered, orphaned = register_set_equality(cfg, stems, workspace)
        ok = not unregistered and not orphaned
        checks.append({"id": "R1", "name": "set_equality_both_directions",
                       "status": "pass" if ok else "fail",
                       "exit_on_fail": kc.EXIT_RULE_A,
                       "detail": (["unregistered: %s" % u for u in unregistered[:20]]
                                  + ["orphaned order entry: %s" % o
                                     for o in orphaned[:20]])})
        if not ok and mode == "build":
            triggered.append(kc.EXIT_RULE_A)
        registration = {"added": [cfg["container_stem"]], "removed": []}
        counts["container_children"] = len(stems)

    checks.append({"id": "L1", "name": "plan_within_cap", "status": "pass",
                   "exit_on_fail": kc.EXIT_GUARD,
                   "detail": ["%d planned against guard.max_files_written %d"
                              % (len(plan.entries),
                                 cfg["guard"]["max_files_written"])]})
    checks.append({"id": "L2", "name": "scoped_to_reuse_subset", "status": "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": ["%d planned against counts.reuse %s"
                              % (len(plan.entries), counts["reuse"])]})

    binding = kc.build_binding(
        [os.path.join(run_dir, "scenario.json"),
         kc.report_path(run_dir, "source", "build")])
    if mode == "build":
        binding["%s.plan.json" % stage] = kc.sha256_file(plan_path(run_dir, stage))
    # §5.10 hazard A: --status compares this to the live ref and prints the
    # base-moved line when they differ. Recorded on BOTH paths, so a rehearsal's
    # plan carries the base it was planned against too.
    sha = base_sha(workspace)
    if sha:
        binding["SCED-downloads@origin/korean"] = sha

    report = kc.new_report(
        stage, cfg["slug"], mode=mode, counts=dict(counts), checks=checks,
        binding=binding,
        freshness=kc.build_freshness([os.path.join(run_dir, "scenario.json")]),
        results={"roots": plan.roots(),
                 "plan": os.path.relpath(plan_path(run_dir, stage), workspace)})
    report["write_set"] = write_set
    report["registration"] = registration
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None

    kc.write_report(report, run_dir, cfg=cfg)
    if mode == "build" and mirror:
        mirror_lock(cfg, run_dir, report, workspace)
    return report, plan


def mirror_lock(cfg, run_dir, report, workspace=None):
    """HANDED to kz_config's mirror writer -- this module never opens a path
    under SCED-tools/ (§3.1). Paths and hashes only; the receipt carries no bytes.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    relpath = "%s.lock.json" % cfg["slug"]
    existing = os.path.join(workspace, cfg["guard"]["data_root"], "locks", relpath)
    lock = read_json(existing) if os.path.exists(existing) else collections.OrderedDict()
    lock.setdefault("schema_version", SCHEMA_VERSION)
    lock["slug"] = cfg["slug"]
    lock["generated_at"] = kc.utc_now()
    stages = lock.setdefault("stages", {})
    stages[report["stage"]] = collections.OrderedDict([
        ("verdict", report["verdict"]),
        ("exit_code", report["exit_code"]),
        ("consumable", report["consumable"]),
        ("gate", report["gate"]),
        ("binding", report["binding"]),
        ("write_set", report["write_set"]),
        ("registration", report["registration"]),
    ])
    return kz.write_data(cfg, "locks", relpath, _sorted(lock), workspace=workspace)


# ---------------------------------------------------------------------------
# 12. --selftest
# ---------------------------------------------------------------------------

FAULTS = ("adoption", "sort-keys", "cap", "scoping", "set-equality",
          "revert-create", "live-derivation")


def _cfg(max_files=200):
    return {
        "slug": "selftest", "scenario_name": "Selftest",
        "pack": "Korean - Campaigns", "pack_container": "Korean-Campaigns.KoreanC",
        "container_stem": "Selftest.aaaaaa", "shared_backs": [],
        "guard": {
            "write_roots": [
                "%s/Korean - Campaigns/Korean-Campaigns.KoreanC/Selftest.aaaaaa/"
                % LANGPACK_ROOT,
                "%s/Korean - Campaigns/Korean-Campaigns.KoreanC/Selftest.aaaaaa.json"
                % LANGPACK_ROOT,
                "%s/Korean - Campaigns/Korean-Campaigns.KoreanC.json" % LANGPACK_ROOT,
            ],
            "forbidden": ["SCED/", ".git/", "SCED-tools/"],
            "data_root": "SCED-tools/scripts/koreanize/data/",
            "max_files_written": max_files,
        },
    }


def _entry(card_id=4000, deck_key="40", cell=0, num_width=10, num_height=7):
    return {"object_id": "Deck.a/Card.aaa111", "guid": "aaa111",
            "arkham_id": "82022", "decision": "reuse",
            "donor": {"pack": "Korean - Campaigns", "card_id": card_id,
                      "deck_key": deck_key, "cell": cell,
                      "num_width": num_width, "num_height": num_height,
                      "face_url": "KO", "back_url": "B", "back_is_hidden": True,
                      "type": 0, "nickname": "한국어", "description": "설명"}}


def selftest(fault=None, verbose=True):
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    def fires(label, code, fn):
        try:
            fn()
        except kc.KzRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d"
                                % (label, code, exc.code))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    source_obj = {"CardID": 917500, "GUID": "aaa111", "Name": "Card",
                  "Nickname": "Abbess", "Tags": ["x"],
                  "CustomDeck": {"9175": {"FaceURL": "EN", "BackURL": "B",
                                          "NumWidth": 8, "NumHeight": 5}},
                  "Transform": {"posX": 1, "scaleX": 1, "scaleY": 1, "scaleZ": 1}}

    if "adoption" in wanted:
        entry = _entry()
        override = build_override(entry, source_obj)
        if "Tags" in override:
            findings.append("adoption: Tags were not dropped")
        if set(override["Transform"]) != {"scaleX", "scaleY", "scaleZ"}:
            findings.append("adoption: Transform is not scale-only")
        if json.loads(override["GMNotes"]) != {"id": "82022"}:
            findings.append("adoption: GMNotes was not reduced to {id}")
        if "Description" in override:
            findings.append("adoption: an absent Description was written")

        adopted = adopt_donor(override, entry, _cfg())
        if adopted["CardID"] != 4000 or list(adopted["CustomDeck"]) != ["40"]:
            findings.append("adoption: the donor's CardID/deck_key were not adopted")
        if adopted["GUID"] != "aaa111":
            findings.append("adoption: the object identity is not the target's")

        # THE hazard: the target's English CardID retained beside the donor's
        # FaceURL renders the wrong card, and no precedent in the tree shows it.
        bad = collections.OrderedDict(adopted)
        bad["CardID"] = 917500
        fires("adoption (English CardID retained)", kc.EXIT_RULE_A,
              lambda: assert_adoption(bad, entry, entry["donor"]))
        bad2 = collections.OrderedDict(adopted)
        bad2["CustomDeck"] = {"40": dict(adopted["CustomDeck"]["40"],
                                         NumWidth=8)}
        fires("adoption (donor grid not adopted)", kc.EXIT_RULE_A,
              lambda: assert_adoption(bad2, entry, entry["donor"]))
        bad3 = collections.OrderedDict(adopted)
        bad3["GUID"] = "bbb222"
        fires("adoption (donor GUID installed)", kc.EXIT_RULE_A,
              lambda: assert_adoption(bad3, entry, entry["donor"]))

        with_text, wrote = apply_objtext(adopted, entry)
        if not wrote or with_text["Nickname"] != "한국어":
            findings.append("objtext: the donor's Korean Nickname was not adopted")
        _o, wrote = apply_objtext(adopted, {"donor": {"nickname": None}})
        if wrote:
            findings.append("objtext: a donor with no Korean text reported a write")

    if "sort-keys" in wanted:
        if list(_sorted({"b": 1, "a": {"d": 1, "c": 2}}).keys()) != ["a", "b"]:
            findings.append("sort-keys: the mapping was not key-sorted")
        if serialize({"a": 1}) != b'{\n  "a": 1\n}\n':
            findings.append("sort-keys: the A1 byte convention does not hold")

    if "cap" in wanted:
        cfg = _cfg(max_files=200)
        root = os.path.join(kc.WORKSPACE_ROOT, LANGPACK_ROOT,
                            "Korean - Campaigns", "Korean-Campaigns.KoreanC",
                            "Selftest.aaaaaa")
        for count, should_pass in ((199, True), (200, True), (201, False)):
            paths = [os.path.join(root, "o%d.json" % i) for i in range(count)]
            try:
                kz.assert_write_paths(cfg, paths)
                if not should_pass:
                    findings.append("cap: %d planned files did not refuse" % count)
            except kc.KzRefusal as exc:
                if should_pass:
                    findings.append("cap: %d planned files refused (exit %d)"
                                    % (count, exc.code))
                elif exc.code != kc.EXIT_GUARD:
                    findings.append("cap: expected exit 4, got %d" % exc.code)

    if "scoping" in wanted:
        # A path outside guard.write_roots is exit 4 BEFORE any read. This is the
        # property that makes this module's refusal absolute and is why the lock
        # receipt is handed to kz_config rather than written here.
        fires("scoping (a write under SCED-tools/)", kc.EXIT_GUARD,
              lambda: kz.assert_write_paths(
                  _cfg(), [os.path.join(kc.WORKSPACE_ROOT, "SCED-tools",
                                        "scripts", "x.json")]))
        fires("scoping (a write into the scenario tree)", kc.EXIT_GUARD,
              lambda: kz.assert_write_paths(
                  _cfg(), [os.path.join(kc.WORKSPACE_ROOT, DOWNLOADS_ROOT,
                                        "decomposed", "scenario", "x.json")]))

    if "set-equality" in wanted:
        cfg = _cfg()
        unregistered, orphaned = register_set_equality(cfg, ["A.1", "B.2"])
        if orphaned != ["A.1", "B.2"]:
            findings.append("set-equality: an order entry with no file was not "
                            "reported as orphaned (got %r)" % (orphaned,))
        if unregistered:
            findings.append("set-equality: reported %r unregistered against an "
                            "absent directory" % (unregistered,))

    if "revert-create" in wanted:
        # The case `git diff --numstat` could not express: a `create` left behind.
        # It is deleted ONLY when its sha256 matches the bytes this run planned.
        import tempfile
        tmp = tempfile.mkdtemp(prefix="kz-langpack-selftest.")
        try:
            run_dir = os.path.join(tmp, "run")
            target = os.path.join(tmp, "made.json")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
            digest = kc.sha256_file(target)
            root = snapshot_root(run_dir, "reuse")
            os.makedirs(root)
            kc.atomic_write_json(os.path.join(root, "intentions.json"), {
                "entries": [{"path": os.path.relpath(target, tmp),
                             "action": "create", "pre_sha256": None,
                             "snapshot": None, "plan_sha256": digest,
                             "committed": False}]})
            result = revert(run_dir, "reuse", None, workspace=tmp)
            if result["deleted"] != [os.path.relpath(target, tmp)]:
                findings.append("revert-create: the created file was not deleted")
            if os.path.exists(target):
                findings.append("revert-create: the created file survived")

            # And a file whose bytes are NOT the ones this run planned is left
            # alone with a finding, never deleted on a guess.
            with open(target, "w", encoding="utf-8") as handle:
                handle.write('{"edited": true}\n')
            result = revert(run_dir, "reuse", None, workspace=tmp)
            if result["deleted"] or not result["findings"]:
                findings.append("revert-create: a foreign file was deleted")
            if not os.path.exists(target):
                findings.append("revert-create: a foreign file was removed")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        fires("revert (nothing to invert)", kc.EXIT_PRECONDITION,
              lambda: revert_inputs("/nonexistent/run", "reuse", None))

    if "live-derivation" in wanted:
        cfg = _cfg()
        inside = os.path.join(kc.WORKSPACE_ROOT, LANGPACK_ROOT,
                              "Korean - Campaigns", "Korean-Campaigns.KoreanC",
                              "Selftest.aaaaaa", "x.json")
        outside = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", "x.json")
        data_root = os.path.join(kc.WORKSPACE_ROOT, "SCED-tools", "scripts",
                                 "koreanize", "data", "locks", "x.json")
        if not requires_live(cfg, [inside]):
            findings.append("live-derivation: a write root did not require --live")
        if requires_live(cfg, [outside]):
            findings.append("live-derivation: a <run_dir> write required --live")
        # data_root is deliberately NOT banner-bearing: a blast-radius prompt
        # whose radius is a git-tracked JSON receipt trains the operator to type
        # LIVE without reading it (§3.1).
        if requires_live(cfg, [data_root]):
            findings.append("live-derivation: guard.data_root required --live")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 13. CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_langpack.py",
        description="koreanize's langpack writer -- the sole writer into "
                    "SCED-downloads (design §5.3).")
    parser.add_argument("submode", nargs="?", choices=list(SUBMODES))
    parser.add_argument("--slug")
    parser.add_argument("--run-dir")
    parser.add_argument("--stage", help="`revert` only -- which report to invert")
    parser.add_argument("--live", action="store_true",
                        help="write for real; prints a blast-radius banner and "
                             "requires typing LIVE unless --yes")
    parser.add_argument("--yes", action="store_true",
                        help="skip the typed LIVE confirmation; the banner is "
                             "still printed")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--no-mirror", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_langpack --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_ARTIFACT
        print("  ok: donor adoption and its three negatives, the A1 byte "
              "convention, 199/200/201, the path guard, register's set equality, "
              "revert's created-file rule, and the --live derivation")
        return kc.EXIT_OK

    if not args.submode or (not args.run_dir and not args.slug):
        parser.print_help()
        return kc.EXIT_USAGE
    run_dir = args.run_dir or os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize",
                                           args.slug)

    if args.submode == "revert":
        kc.check_invocation_guards([run_dir])
        cfg = kz.load_scenario(os.path.join(run_dir, "scenario.json"))
        stage = args.stage
        if not stage:
            kc.refuse(kc.EXIT_USAGE, "`revert` needs --stage NAME",
                      "which report to invert (§8.4)")
        entries, source = revert_inputs(run_dir, stage, cfg)
        if args.live:
            # `revert` mutates SCED-downloads exactly as the forward submodes do,
            # so it takes the SAME two refusals before touching anything. Leaving
            # them off made the one stage invoked *during an incident* the one
            # stage that could collide with the 02:17 driver -- and §4.1's rule
            # is stated over "every stage that writes outside <run_dir>", which
            # revert plainly does.
            if not confirm_revert(stage, source, entries, assume_yes=args.yes):
                kc.refuse(kc.EXIT_DISPATCH_LIVE_DECLINED,
                          "live revert declined at the banner")
            kz.assert_outside_window()
            lock = kz.NightlyLock()
        else:
            lock = _NoLock()
        with lock:
            result = revert(run_dir, stage, cfg, apply_changes=args.live)
        dirty = git_clean_over([e["path"] for e in entries])
        code = kc.EXIT_OK
        if result["findings"] or dirty:
            code = kc.EXIT_REVERT_INCOMPLETE
        if args.json_only:
            print(json.dumps(dict(result, dirty=dirty, exit_code=code),
                             ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("koreanize revert --stage %s -- %s" % (stage, result["source"]))
            print("  restored        : %d" % len(result["restored"]))
            print("  deleted         : %d" % len(result["deleted"]))
            print("  already absent  : %d" % len(result["skipped"]))
            for directory in result.get("pruned") or []:
                print("  pruned empty dir: %s" % directory)
            for line in result["findings"]:
                print("  ! %s" % line)
            for line in dirty:
                print("  ! still modified: %s" % line)
            print("  exit            : %d" % code)
        return code

    report, plan = run_langpack(
        args.submode, run_dir, live=args.live, assume_yes=args.yes,
        dry_run=args.dry_run, mirror=not args.no_mirror)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    print("koreanize %s -- %s (%s)" % (args.submode, report["slug"], report["mode"]))
    print("  planned         : %d  (%d create, %d modify)"
          % (report["counts"]["planned"], report["counts"]["created"],
             report["counts"]["modified"]))
    for root in plan.roots():
        print("  root            : %s" % root)
    if report["write_set"] is not None:
        print("  written         : %d" % len(report["write_set"]))
    if report["registration"] is not None:
        print("  registered      : %s" % report["registration"]["added"])
    for check in report["checks"]:
        print("  %-28s %s" % (check["name"], check["status"]))
    print("  verdict         : %s (exit %d, consumable %s)"
          % (report["verdict"], report["exit_code"], report["consumable"]))
    if report["mode"] != "build":
        print("  plan            : %s" % plan_path(run_dir, args.submode))
        print("  NOTE            : rehearsal. Re-run with --live to write.")
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
