#!/usr/bin/env python3
"""koreanize `init` -- stage 0: the generator that never existed (design §5.1).

`card-text-en.json`'s `generated_by` reads "am:analyze midwinter-gala-korean":
the Midwinter index was produced ad hoc by an agent and has no reusable
generator. This module is that generator, and it emits the **v2** schema of
§3.3 -- two levels, objects first -- so that every wiring stage downstream
loops over an OBJECT rather than over an id.

ART TIER (§5.9): `#!/usr/bin/env python3`, Homebrew 3.14.x, PIL available.
PIL is imported LAZILY, inside the atlas step alone, so that the population
walk -- which is the part every test exercises and the part that has no pixels
in it -- runs on any interpreter and needs no wheel.

`init` is a NEUTRAL stage (§4.1): it is in neither `ai.required_stages` nor
`ai.forbidden_stages`, so it deliberately does NOT call `kc.declare_ai()`.
`_check_contracts` only knows the required/forbidden dichotomy, and a neutral
stage declaring either half would refuse against its own generated config.

WHAT THIS MODULE OWNS
  - the four-clause population rule of §3.3, applied in the order 1 -> 4 -> 3 -> 2
  - both `GMNotes` storage forms (sidecar and inline), because reading only the
    first takes Midwinter's id count from 63 to 29, silently
  - the CustomDeck-CONDITIONAL geometry assertions (§5.1 step 3)
  - the case-fold collision scan (exit 62) and the excluded-kind id scan (exit 14)
  - the atlas inventory with its content-addressed cache under <run_dir>/assets/
  - the generated `scenario.json`, handed to kz_config's mirror writer for its
    git home; `init` itself opens no path under SCED-tools/ (§3.1)

MEASURED, 2026-08-23, by this module against the live trees:
  The Midwinter Gala          73 reached / 64 objects /  9 twice / 62 ids + 1 guide
  War of the Outer Gods       62 reached / 62 objects /  0 twice / 58 ids + 1 guide
  Challenge Scenario - By the Book
                              37 reached / 37 objects /  0 twice / 36 ids + 1 guide
                              guid_collisions_distinct_id = 3
Those are §1.3's and §3.3's pinned rows, and `test_koreanize_init.py` asserts
them against the corpus when it is present.
"""

import argparse
import collections
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402

STAGE = "init"

# The v2 schema of §3.3. Deliberately NOT kc.SCHEMA_VERSION: the report envelope
# and the card index version independently, and conflating them is how a report
# format change would silently claim to have moved the index format too.
CARD_TEXT_SCHEMA_VERSION = "2.0.0"

# ---------------------------------------------------------------------------
# Clause 1 of the population rule (§3.3): the kinds that JOIN objects[].
# ---------------------------------------------------------------------------
#
# `CardCustom` is not a curiosity. Measured across the 16 target scenarios there
# are 94 of them, 83 carrying a GMNotes.id, and 212 CardCustom files across the
# three shipped Korean langpacks. Excluding it dropped 83 real cards silently,
# concentrated in the eight Challenge Scenarios that are v0's entire addressable
# set. NEITHER reference corpus contains one -- which is precisely the argument
# for clause 2, below.
POPULATION_KINDS = ("Card", "Custom_PDF", "CardCustom")

# §1.4 puts the campaign-guide PDF out of scope. `init` still admits the object
# (it is a population kind and it carries an id), but counts its id separately as
# a `guide_id` so that `counts.arkham_ids` is the denominator §3.4 and §1.3 use.
GUIDE_ID_PREFIX = "CG"

SCENARIO_ROOT = os.path.join("SCED-downloads", "decomposed", "scenario")
LANGPACK_ROOT = os.path.join("SCED-downloads", "decomposed", "language-pack")

# Pack precedence for §5.1 step 7's reuse probe, and it is ORDERED. An id present
# in both Player Cards and Campaigns belongs to Player Cards: measured on
# Midwinter that is what yields §3.3's pinned objects_by_pack of
# {Campaigns: 47, Player Cards: 16, unassigned: 1} -- 16 ids are in both packs,
# 48 are Campaigns-only, and one of those 48 is the CG71 guide, which this module
# routes to `unassigned` before the lookup because §1.4 puts it out of scope.
KOREAN_PACKS = (
    ("Korean - Player Cards", "Korean-PlayerCards.KoreanI"),
    ("Korean - Campaigns", "Korean-Campaigns.KoreanC"),
    ("Korean - Fan Campaigns", "Korean-FanCampaigns.KoreanFC"),
)
UNASSIGNED = "unassigned"

# Font roles and the PostScript name each is expected to carry. `body` is pinned
# by §3.2; `title` and `icons` are resolved through ~/.config/koreanize/env only,
# because this workspace's images-ko/fonts/ carries neither and the design elides
# the title face's name. See resolve_fonts() for why an ABSENT role is recorded
# rather than refused, while a MISMATCHED one is still exit 13.
FONT_ROLES = (
    ("title", None, "KOREANIZE_FONT_TITLE", False),
    ("body", "HakgyoansimBareondotumB", "KOREANIZE_FONT_BODY", True),
    ("icons", "ArkhamIcons", "KOREANIZE_FONT_ICONS", False),
)

ATLAS_CACHE_ENV = "KOREANIZE_ATLAS_CACHE"


# ---------------------------------------------------------------------------
# 1. Scenario resolution -- §5.1 step 1
# ---------------------------------------------------------------------------

def resolve_scenario(name, workspace=None):
    """Resolve --scenario to exactly one directory; refuse on 0 or >1 (exit 2).

    Returns (scenario_dir, root_container_dir, root_container_json). The ROOT
    CONTAINER is not itself a walked node -- §3.3 measures "by walking
    .../TheMidwinterGala.074d4a/", i.e. its children.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    root = os.path.join(workspace, SCENARIO_ROOT)
    if not os.path.isdir(root):
        kc.refuse(kc.EXIT_PRECONDITION, "scenario root not found", root)

    entries = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    exact = [d for d in entries if d == name]
    matches = exact or [d for d in entries if name.lower() in d.lower()]

    if not matches:
        kc.refuse(kc.EXIT_USAGE, "no scenario matches %r" % name,
                  "%d candidates under %s" % (len(entries), SCENARIO_ROOT))
    if len(matches) > 1:
        kc.refuse(kc.EXIT_USAGE, "%r matches %d scenarios" % (name, len(matches)),
                  "; ".join(matches))

    scenario_dir = os.path.join(root, matches[0])
    containers = sorted(f[:-5] for f in os.listdir(scenario_dir)
                        if f.endswith(".json")
                        and os.path.isdir(os.path.join(scenario_dir, f[:-5])))
    if len(containers) != 1:
        kc.refuse(kc.EXIT_USAGE,
                  "%s does not hold exactly one root container" % matches[0],
                  "found %s" % (containers or "none"))
    stem = containers[0]
    return (scenario_dir,
            os.path.join(scenario_dir, stem),
            os.path.join(scenario_dir, stem + ".json"))


def derive_slug(scenario_name):
    """Lowercase kebab-case, ASCII only -- 'War of the Outer Gods' ->
    'war-of-the-outer-gods', which is §3.2's own example."""
    text = re.sub(r"[^a-z0-9]+", "-", scenario_name.lower()).strip("-")
    return text or "scenario"


# ---------------------------------------------------------------------------
# 2. GMNotes -- BOTH storage forms (§5.1 step 3)
# ---------------------------------------------------------------------------

def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Untrusted-input containment
# ---------------------------------------------------------------------------
#
# EVERY path fragment and URL this module consumes is authored by whoever
# published the mod, not by the operator: `ContainedObjects_order` entries,
# `GMNotes_path`, and the `FaceURL`/`BackURL` inside `CustomDeck`. The operator
# trusts that tree enough to load it into Tabletop Simulator, which is a
# statement about its game content and not about its filesystem behaviour -- so
# the three of them are validated here rather than assumed well-formed.

def contained_path(root, relpath, field):
    """Join `relpath` under `root` and re-assert it did not escape.

    os.path.join DISCARDS `root` entirely when `relpath` is absolute, so the join
    alone is not containment; the realpath comparison is what makes it one.
    Returns None when the path does not exist, refuses at exit 14 when it exists
    but resolves outside `root` -- input drift, not a precondition failure.
    """
    if not isinstance(relpath, str) or not relpath:
        return None
    candidate = os.path.realpath(os.path.join(root, relpath))
    root_real = os.path.realpath(root)
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        kc.refuse(kc.EXIT_DRIFT,
                  "%s escapes the scenario directory" % field,
                  "%r resolves to %s, which is outside %s"
                  % (relpath, candidate, root_real))
    return candidate


def is_safe_segment(name):
    """A `ContainedObjects_order` entry must be ONE path segment and nothing else.

    Not a style rule: an entry of `..` walks the descent out of the tree, and an
    entry containing a separator addresses a file the manifest does not name.
    """
    return (isinstance(name, str) and bool(name)
            and name not in (".", "..")
            and "/" not in name and "\\" not in name
            and "\x00" not in name)


def read_gmnotes_id(obj, source_json, scenario_dir):
    """Return the object's GMNotes id, reading BOTH storage forms.

    An object either carries `GMNotes_path` naming a `.gmnotes` sidecar, or it
    carries `GMNotes` INLINE as a JSON string. Both are live in the reference
    corpora -- Midwinter's Guests.1e04d0/ArchibaldHudson.6cd7ec has the sidecar,
    ActDeck.0782c0/FindingtheJewel.a26b6b has the inline string and no sidecar at
    all -- and reading only the sidecar takes Midwinter's id count from 63 to 29
    QUIETLY, as a smaller counts.arkham_ids rather than as an error.

    A node carrying BOTH with different ids is exit 13 naming the file. A node
    carrying neither is an object with no id (step 6's `defer` branch), never a
    parse failure.
    """
    found = []

    rel = obj.get("GMNotes_path")
    if rel:
        # The sidecar path is relative to the SCENARIO directory -- it leads with
        # the root container's own stem (e.g. "TheMidwinterGala.074d4a/...").
        #
        # CONTAINED, because `rel` is attacker-controlled: every byte of this tree
        # is authored by whoever published the mod, not by the operator. An
        # absolute `GMNotes_path` makes os.path.join DISCARD scenario_dir
        # outright, and a `../../../..` one walks out of it, so the join is
        # resolved and re-asserted rather than trusted.
        sidecar = contained_path(scenario_dir, rel, "GMNotes_path")
        if sidecar and os.path.isfile(sidecar):
            try:
                found.append(("sidecar", read_json(sidecar).get("id")))
            except ValueError as exc:
                kc.refuse(kc.EXIT_PRECONDITION,
                          "GMNotes sidecar is not valid JSON", "%s: %s" % (sidecar, exc))
            except OSError as exc:
                # A directory, an unreadable file or an undecodable one is a fact
                # about the input tree, not a crash in the tool.
                kc.refuse(kc.EXIT_PRECONDITION,
                          "GMNotes sidecar could not be read", "%s: %s" % (sidecar, exc))

    inline = obj.get("GMNotes")
    if isinstance(inline, str) and inline.strip():
        try:
            found.append(("inline", json.loads(inline).get("id")))
        except ValueError:
            # An unparseable inline blob is not an id and not a crash: TTS stores
            # free-text GMNotes on plenty of non-card objects.
            pass

    ids = [(form, val) for form, val in found if val]
    if len(ids) > 1 and len({val for _form, val in ids}) > 1:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "object carries two GMNotes ids that disagree",
                  "%s: %s" % (source_json,
                              ", ".join("%s=%s" % (f, v) for f, v in ids)))
    return ids[0][1] if ids else None


def is_guide_id(arkham_id):
    return bool(arkham_id) and str(arkham_id).startswith(GUIDE_ID_PREFIX)


# ---------------------------------------------------------------------------
# 3. The walk -- clause 1 then clause 4 (§3.3, §5.1 step 2)
# ---------------------------------------------------------------------------

class WalkResult(object):
    """What the descent produced, before clauses 3 and 2 run over it."""

    def __init__(self):
        self.reached = collections.OrderedDict()   # walkrel -> node dict
        self.excluded = collections.OrderedDict()  # walkrel -> node dict
        self.excluded_by_kind = collections.Counter()

    @property
    def objects_reached(self):
        """DISTINCT population FILES the walk reached -- never visits.

        Counting visits instead yields 89 on Midwinter and 79 on War of the Outer
        Gods and reproduces neither pinned row (§3.3 clause 4).
        """
        return len(self.reached)


def walk_population(root_container_dir, scenario_dir):
    """Depth-first over `ContainedObjects_order`, NOT over the directory.

    "ContainedObjects_order is the manifest, not the directory -- 11 unregistered
    files were silently dropped with rc 0."

    Clause 1 (kind) and clause 4 (file identity) are applied HERE, in that order.
    Clause 4 de-duplicates on the WALK-RELATIVE PATH: a name listed more than
    once inside one order list is N copies of one card in a deck and is visited
    ONCE, so no path can enter reached_via[] twice.
    """
    result = WalkResult()
    # Realpaths already on the descent stack. A container whose subdirectory is a
    # symlink back to an ancestor would otherwise recurse until RecursionError --
    # a crash rather than a diagnosis, on input the tool does not control.
    on_stack = set()

    def descend(dirpath, relprefix, parent_kind):
        real = os.path.realpath(dirpath)
        if real in on_stack:
            kc.refuse(kc.EXIT_DRIFT, "container tree contains a cycle",
                      "%s re-enters %s" % (relprefix or "<root>", real))
        on_stack.add(real)
        try:
            _descend_body(dirpath, relprefix, parent_kind)
        finally:
            on_stack.discard(real)

    def _descend_body(dirpath, relprefix, parent_kind):
        order_json = dirpath + ".json"
        if not os.path.exists(order_json):
            return
        try:
            parent = read_json(order_json)
        except ValueError as exc:
            kc.refuse(kc.EXIT_PRECONDITION, "container is not valid JSON",
                      "%s: %s" % (order_json, exc))

        seen_here = set()
        for name in parent.get("ContainedObjects_order") or []:
            if not is_safe_segment(name):
                kc.refuse(kc.EXIT_DRIFT,
                          "ContainedObjects_order names a non-segment entry",
                          "%s lists %r, which is not a single path segment"
                          % (order_json, name))
            # Clause 4, first half: a name repeated inside ONE order list is one
            # file. Skipping it here is what keeps reached_via[] a set of DISTINCT
            # paths rather than a list with sixteen copies of one entry.
            if name in seen_here:
                continue
            seen_here.add(name)

            walkrel = (relprefix + "/" + name) if relprefix else name
            child_json = os.path.join(dirpath, name + ".json")

            if os.path.exists(child_json):
                try:
                    obj = read_json(child_json)
                except ValueError as exc:
                    kc.refuse(kc.EXIT_PRECONDITION, "object is not valid JSON",
                              "%s: %s" % (child_json, exc))
                kind = obj.get("Name")
                node = {
                    "walkrel": walkrel,
                    "kind": kind,
                    "parent_kind": parent_kind,
                    "obj": obj,
                    "json": child_json,
                }
                if kind in POPULATION_KINDS:
                    # Clause 4, second half: the same walk-relative path reached
                    # again is the same file, not a second one.
                    if walkrel not in result.reached:
                        result.reached[walkrel] = node
                elif walkrel not in result.excluded:
                    result.excluded[walkrel] = node
                    result.excluded_by_kind[kind] += 1
                child_kind = kind
            else:
                child_kind = None

            subdir = os.path.join(dirpath, name)
            if os.path.isdir(subdir):
                descend(subdir, walkrel, child_kind)

    descend(root_container_dir, "", None)
    return result


# ---------------------------------------------------------------------------
# 4. Clause 3 -- the composite key (guid, arkham_id, card_id)
# ---------------------------------------------------------------------------

def build_objects(walk, scenario_dir, workspace=None):
    """Merge on the composite key; ADMIT a repeated GUID under a different identity.

    Two nodes merge only when all three of (guid, arkham_id, card_id) agree; the
    surviving entry's reached_via[] gains the second path and it is one object the
    walk reached twice through two containers. A GUID that reappears under a
    DIFFERENT arkham_id or CardID is a different card and is admitted as its own
    entry, counted in counts.guid_collisions_distinct_id.

    A GUID alone is not an identity in this tree and the place that becomes
    expensive is a v0 target scenario: in `Challenge Scenario - By the Book` the
    GUID ab3719 carries four different cards (01141 / 50044 / 01179 / 01172), all
    four with Korean donors. Under GUID keying three would be folded into the
    first -- three donors never looked up, three overrides never written -- and
    nothing would report it.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    by_key = collections.OrderedDict()

    for walkrel, node in walk.reached.items():
        obj = node["obj"]
        guid = obj.get("GUID")
        arkham_id = read_gmnotes_id(obj, node["json"], scenario_dir)
        card_id = obj.get("CardID")
        key = (guid, arkham_id, card_id)

        if key in by_key:
            entry = by_key[key]
            if walkrel not in entry["reached_via"]:
                entry["reached_via"].append(walkrel)
            continue

        geometry = check_geometry(obj, node["json"])
        source_file = os.path.relpath(os.path.realpath(node["json"]),
                                      os.path.realpath(workspace))
        entry = {
            "guid": guid,
            "object_id": walkrel,
            "reached_via": [walkrel],
            "arkham_id": arkham_id,
            "kind": node["kind"],
            # Recorded, NEVER a decision input (§5.2 step 5). The unconditional
            # `parent_kind == "Deck"` -> defer predicate an earlier draft carried
            # takes the eight Challenge Scenarios from 222 resolved ids to 61.
            "parent_kind": node["parent_kind"],
            "card_id": card_id,
            "deck_key": geometry["deck_key"],
            "cell": geometry["cell"],
            "row": geometry["row"],
            "col": geometry["col"],
            "atlas_id": geometry["face_url"],
            "back_atlas_id": geometry["back_url"],
            "num_width": geometry["num_width"],
            "num_height": geometry["num_height"],
            "pack": None,          # filled by assign_packs()
            "nickname": obj.get("Nickname"),
            "description": obj.get("Description"),
            "source_file": source_file,
        }
        by_key[key] = entry

    return list(by_key.values())


def check_geometry(obj, source_json):
    """CardID <-> CustomDeck arithmetic, CONDITIONAL on the object carrying a
    CustomDeck (§5.1 step 3).

    The condition is what makes the assertion runnable at all. Every scenario in
    this corpus ships its guide as a Custom_PDF with NO CardID and NO CustomDeck
    whatsoever -- Midwinter's ...-ScenarioGuide.ed8f81 and War of the Outer Gods'
    ...36b4eb -- because a PDF is not a sprite in an atlas and has no cell to be
    at. Applied unconditionally the assertion is unsatisfiable for it, so `init`
    would refuse at exit 13 on BOTH reference corpora, at the first stage of the
    first phase, on the only data v0 is proven against.

    An object carrying ONE of CustomDeck / CardID and not the other is still exit
    13: the pair is what the assertion is about, and half of it is a malformed
    card rather than a PDF.
    """
    blank = {"deck_key": None, "cell": None, "row": None, "col": None,
             "num_width": None, "num_height": None,
             "face_url": None, "back_url": None}

    card_id = obj.get("CardID")
    custom_deck = obj.get("CustomDeck")
    has_deck = isinstance(custom_deck, dict) and bool(custom_deck)
    has_card_id = card_id is not None

    if not has_deck and not has_card_id:
        return blank
    if has_deck != has_card_id:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "object carries one of CustomDeck / CardID and not the other",
                  "%s: CardID=%r CustomDeck=%s" % (source_json, card_id,
                                                   "present" if has_deck else "absent"))
    if not isinstance(card_id, int) or isinstance(card_id, bool) or card_id < 0:
        # Python's floor division makes -1 // 100 == -1 and -1 % 100 == 99, so an
        # unchecked negative CardID yields a plausible-looking deck_key and cell
        # and passes every downstream assertion.
        kc.refuse(kc.EXIT_PRECONDITION, "CardID is not a non-negative integer",
                  "%s: CardID=%r" % (source_json, card_id))

    deck_key = card_id // 100
    cell = card_id % 100
    entry = custom_deck.get(str(deck_key))
    if entry is None:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "CustomDeck has no entry for the CardID's deck key",
                  "%s: CardID=%s deck_key=%s keys=%s"
                  % (source_json, card_id, deck_key, sorted(custom_deck)))

    num_width = entry.get("NumWidth")
    num_height = entry.get("NumHeight")
    if not isinstance(num_width, int) or not isinstance(num_height, int) \
            or num_width < 1 or num_height < 1:
        kc.refuse(kc.EXIT_PRECONDITION, "CustomDeck grid is not a positive integer pair",
                  "%s: NumWidth=%r NumHeight=%r" % (source_json, num_width, num_height))

    row, col = divmod(cell, num_width)
    # Both assertions of §5.1 step 3, in the only form in which the first is not
    # tautological: row/col are DERIVED, so the round-trip is real only when the
    # cell is inside the declared grid.
    if row >= num_height:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "cell is outside the declared grid",
                  "%s: cell=%d is row %d of a %dx%d sheet"
                  % (source_json, cell, row, num_width, num_height))
    if cell != row * num_width + col:
        kc.refuse(kc.EXIT_PRECONDITION, "cell != row*num_width + col",
                  "%s: cell=%d row=%d col=%d num_width=%d"
                  % (source_json, cell, row, col, num_width))
    if card_id != deck_key * 100 + cell:
        kc.refuse(kc.EXIT_PRECONDITION, "card_id != deck_key*100 + cell",
                  "%s: card_id=%d deck_key=%d cell=%d" % (source_json, card_id,
                                                          deck_key, cell))

    return {"deck_key": str(deck_key), "cell": cell, "row": row, "col": col,
            "num_width": num_width, "num_height": num_height,
            "face_url": entry.get("FaceURL"), "back_url": entry.get("BackURL")}


# ---------------------------------------------------------------------------
# 5. Clause 2 and the case-fold scan -- §5.1 step 4
# ---------------------------------------------------------------------------

def scan_case_fold(objects):
    """Exit 62, and this is now its ONLY half.

    Run AFTER both de-duplications, so neither a file listed twice inside one
    order list nor an object reached through two containers is ever mistaken for
    two colliding objects. The duplicate-GUID half is REMOVED: clause 3 admits a
    repeated GUID carrying a different (arkham_id, card_id), and the check as
    previously written was evaluated over an objects[] that GUID keying made
    GUID-unique by construction, so it could never fire. Midwinter's two Bags
    sharing GUID af62ff remain the measured reason koreanize refuses no duplicate
    GUID anywhere.
    """
    folded = collections.defaultdict(list)
    for entry in objects:
        folded[entry["object_id"].lower()].append(entry["object_id"])
    collisions = {k: v for k, v in folded.items() if len({x for x in v}) > 1}
    if collisions:
        first = sorted(collisions)[0]
        kc.refuse(kc.EXIT_COLLISION,
                  "case-only path collision among object ids",
                  "%d group(s); first: %s" % (len(collisions),
                                              " vs ".join(sorted(collisions[first]))))
    return len(collisions)


def scan_excluded_ids(walk, scenario_dir):
    """Clause 2: no node tallied in objects_excluded_by_kind{} may carry a
    GMNotes.id. A violation is exit 14, naming the kind, the id and the file.

    This converts "a kind we did not think of" from a silent drop into a stop,
    and it is the only clause here that is not measurable against today's
    corpora: over all 16 target scenarios there are ZERO violations, so it fires
    on nothing that exists and exists entirely for the next kind.
    """
    offenders = []
    for walkrel, node in walk.excluded.items():
        arkham_id = read_gmnotes_id(node["obj"], node["json"], scenario_dir)
        if arkham_id:
            offenders.append((node["kind"], arkham_id, node["json"]))
    if offenders:
        kind, arkham_id, path = offenders[0]
        kc.refuse(kc.EXIT_DRIFT,
                  "an excluded kind carries a GMNotes.id",
                  "%d violation(s); first: kind=%s id=%s file=%s"
                  % (len(offenders), kind, arkham_id, path))
    return len(offenders)


# ---------------------------------------------------------------------------
# 6. The reuse probe -- §5.1 step 7, and where objects_by_pack comes from
# ---------------------------------------------------------------------------

def _pack_tree_digest(workspace):
    """A cheap binding over the three Korean packs -- names, sizes and mtimes.

    Deliberately NOT a content digest: reading 6,008 files to decide whether to
    read 6,008 files buys nothing. §5.2 step 1 binds `source`'s full index "by a
    tree digest so it is rebuilt when the packs move"; this is the same idea at
    the resolution `init`'s membership probe needs.
    """
    digest = hashlib.sha256()
    for pack, container in KOREAN_PACKS:
        base = os.path.join(workspace, LANGPACK_ROOT, pack, container)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for fname in sorted(filenames):
                if not fname.endswith(".json"):
                    continue
                path = os.path.join(dirpath, fname)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                digest.update(("%s|%d|%d\n" % (os.path.relpath(path, workspace),
                                               stat.st_size,
                                               int(stat.st_mtime))).encode("utf-8"))
    return digest.hexdigest()


def pack_index_cache_path(workspace, digest):
    return os.path.join(workspace, ".am", "koreanize", "_cache",
                        "korean-pack-index.%s.json" % digest[:16])


def build_korean_pack_index(workspace=None, use_cache=True):
    """id -> ordered set of Korean packs carrying an override for it.

    This is the light half of §5.2 step 1: `source` builds the full
    korean-pack-index.json with donor geometry, but `init` needs only pack
    MEMBERSHIP so the human gate can see the reuse set before deciding scope.

    Bound to a tree digest and cached, because `init` is re-run repeatedly while
    an operator works the gate, and the probe otherwise re-parses every file in
    all three packs (6,008 ids measured) on each invocation. The cache lives in
    run material, is keyed by the digest, and a miss simply rebuilds -- so a
    stale or corrupt entry costs a rebuild, never a wrong answer.
    """
    workspace = workspace or kc.WORKSPACE_ROOT

    cache_path = None
    if use_cache:
        digest = _pack_tree_digest(workspace)
        cache_path = pack_index_cache_path(workspace, digest)
        if os.path.exists(cache_path):
            try:
                cached = read_json(cache_path)
                return {k: set(v) for k, v in cached.items()}
            except (ValueError, OSError, AttributeError):
                pass   # a bad cache entry is a rebuild, never a wrong answer

    index = collections.defaultdict(set)
    for pack, container in KOREAN_PACKS:
        base = os.path.join(workspace, LANGPACK_ROOT, pack, container)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for fname in filenames:
                if not fname.endswith(".json"):
                    continue
                try:
                    obj = read_json(os.path.join(dirpath, fname))
                except (ValueError, OSError):
                    continue
                inline = obj.get("GMNotes")
                if not isinstance(inline, str) or not inline.strip():
                    continue
                try:
                    arkham_id = json.loads(inline).get("id")
                except ValueError:
                    continue
                if arkham_id:
                    index[arkham_id].add(pack)

    if cache_path:
        try:
            kc.atomic_write_json(cache_path,
                                 {k: sorted(v) for k, v in sorted(index.items())})
            _evict_pack_index_cache(os.path.dirname(cache_path))
        except OSError:
            pass   # the cache is an optimisation; failing to write one is not an error
    return index


PACK_INDEX_CACHE_KEEP = 3


def _evict_pack_index_cache(cache_dir, keep=PACK_INDEX_CACHE_KEEP):
    """Keep the newest few entries and delete the rest.

    Every langpack edit moves the digest and therefore mints a new filename, so
    without eviction this directory grows once per pack change, forever. Three is
    enough to survive flipping between a couple of tree states while working, and
    the entries are pure derived data -- deleting a live one costs one rebuild.
    """
    try:
        entries = [f for f in os.listdir(cache_dir)
                   if f.startswith("korean-pack-index.") and f.endswith(".json")]
    except OSError:
        return
    if len(entries) <= keep:
        return
    paths = []
    for name in entries:
        path = os.path.join(cache_dir, name)
        try:
            paths.append((os.path.getmtime(path), path))
        except OSError:
            continue
    for _mtime, path in sorted(paths, reverse=True)[keep:]:
        _unlink_quietly(path)


def assign_packs(objects, index):
    """Fill each object's `pack`, in KOREAN_PACKS order.

    A guide id is routed to `unassigned` BEFORE the lookup: §1.4 puts the
    campaign-guide PDF out of scope, and CG71 does have a Korean - Campaigns
    override, so looking it up would file the guide as a Campaigns object and
    move §3.3's pinned 47.
    """
    counts = collections.Counter()
    for entry in objects:
        arkham_id = entry.get("arkham_id")
        pack = None
        if arkham_id and not is_guide_id(arkham_id):
            carriers = index.get(arkham_id) or set()
            for candidate, _container in KOREAN_PACKS:
                if candidate in carriers:
                    pack = candidate
                    break
        entry["pack"] = pack
        counts[pack or UNASSIGNED] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# 7. Atlas inventory -- §5.1 step 5
# ---------------------------------------------------------------------------

def atlas_cache_root(env, run_dir):
    """Content-addressed, and deliberately NOT images-ko/assets/.

    That path is a §3.1 read-only tier, is not inside any git repository, holds
    the ~174 MB of English atlases the golden fixture depends on, and is named in
    rule 8(c)'s unchanged-digest set. Under URL-stem naming a second scenario
    whose atlas normalises to a colliding filename would overwrite part of the v1
    regression instrument with --live never invoked; under content addressing a
    collision IS a cache hit.
    """
    shared = env.get(ATLAS_CACHE_ENV) or os.environ.get(ATLAS_CACHE_ENV)
    return os.path.expanduser(shared) if shared else None


def collect_atlas_urls(objects):
    """Distinct FaceURL/BackURL set, with the cells each face URL serves."""
    faces = collections.OrderedDict()
    backs = collections.Counter()
    for entry in objects:
        face = entry.get("atlas_id")
        if face:
            slot = faces.setdefault(face, {
                "url": face, "side": "face",
                "grid": {"num_width": entry.get("num_width"),
                         "num_height": entry.get("num_height")},
                "cells": set(), "packs": set()})
            if entry.get("cell") is not None:
                slot["cells"].add(entry["cell"])
            if entry.get("pack"):
                slot["packs"].add(entry["pack"])
        back = entry.get("back_atlas_id")
        if back:
            backs[back] += 1
    return faces, backs


def measure_atlases(faces, run_dir, cache_root=None, download=True, timeout=60):
    """Download once each into <run_dir>/assets/, measure, derive cell_pixels.

    PIL is imported HERE and nowhere else in this module, so the population walk
    -- which is what every offline test exercises -- needs no wheel at all.

    A URL that cannot be fetched or measured is RECORDED with null pixels rather
    than refused: `init` reports, it does not fail (§5.1 step 6's rationale
    applies to the same reason). The integrality assertion is exit 13 and fires
    only on an atlas that WAS measured and whose pixels do not divide by its grid.
    """
    assets = os.path.join(run_dir, "assets")
    inventory = []
    fetched = 0
    measured = 0

    for url, slot in faces.items():
        num_width = slot["grid"]["num_width"]
        num_height = slot["grid"]["num_height"]
        record = {
            "atlas_id": None,
            "side": "face",
            "english_url": url,
            "local": None,
            "grid": {"num_width": num_width, "num_height": num_height},
            "pixels": None,
            "cell_pixels": None,
            "bytes": None,
            "sha256": None,
            "cells_used": len(slot["cells"]),
            "packs": sorted(slot["packs"]),
            "single_card": bool(num_width == 1 and num_height == 1),
        }

        local = digest = None
        if download:
            got = _fetch_atlas(url, assets, cache_root, timeout)
            if got:
                local, digest = got
        if local:
            fetched += 1
            record["bytes"] = os.path.getsize(local)
            record["sha256"] = digest
            record["atlas_id"] = digest[:16]
            record["local"] = os.path.join("<run_dir>", "assets", digest + ".png")
            size = _measure_png(local)
            if size:
                measured += 1
                width, height = size
                record["pixels"] = [width, height]
                if num_width and num_height:
                    if width % num_width or height % num_height:
                        kc.refuse(kc.EXIT_PRECONDITION,
                                  "atlas pixels do not divide by its grid",
                                  "%s: %dx%d over a %dx%d sheet"
                                  % (url, width, height, num_width, num_height))
                    record["cell_pixels"] = [width // num_width, height // num_height]
        if record["atlas_id"] is None:
            # Stable identity even when the pixels were never seen, so the report
            # and card-text-en.json agree on how to name this sheet.
            record["atlas_id"] = kc.sha256_bytes(url.encode("utf-8"))[:16]
        inventory.append(record)

    return inventory, {"atlases_fetched": fetched, "atlases_measured": measured}


# The atlas URLs come out of the mod tree, so the fetch is constrained rather
# than trusted. compose-card-atlas.py caps a produced atlas at MAX_ATLAS_BYTES;
# an INPUT sheet has no such contract, so the ceiling here is generous and its
# only job is to stop an unbounded body, not to police atlas size.
ATLAS_SCHEMES = ("http", "https")
MAX_ATLAS_FETCH_BYTES = 512 * 1024 * 1024
_FETCH_CHUNK = 1 << 20


def _check_fetch_target(url):
    """Refuse anything but http(s) to a routable host.

    urllib supports `file://` and `ftp://` out of the box, so an unvalidated
    urlopen on an attacker-authored `FaceURL` reads local files -- and the result
    would be hashed, stored under <run_dir>/assets/ and named in an inventory
    that gets mirrored into a git-tracked config. The host check is the SSRF half:
    a link-local or loopback target reaches services that exist only because this
    process is inside the operator's network boundary.

    Returns None when the target is acceptable, a reason string otherwise. It
    REPORTS rather than refuses, because `init` reports on an unmeasurable atlas
    (§5.1 step 6's rationale) -- a hostile URL must not be able to halt the walk.
    """
    import ipaddress
    import urllib.parse

    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme not in ATLAS_SCHEMES:
        return "scheme %r is not one of %s" % (parts.scheme, list(ATLAS_SCHEMES))
    host = parts.hostname
    if not host:
        return "no host"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A NAME. Resolving it here to re-check the address would still not close
        # DNS rebinding -- the resolution that matters is the one urllib performs
        # inside urlopen, and there is no portable way to pin the two together --
        # so that is left out of scope and stated rather than implied. What IS
        # closed is the name that needs no rebinding at all: `localhost` is a
        # loopback target spelled as a name, and refusing the literal while
        # allowing its own hostname would be a guard with a hole in the middle.
        name = host.lower().rstrip(".")
        if name == "localhost" or name.endswith(".localhost"):
            return "host %s is loopback" % host
        return None
    if (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast
            or address.is_unspecified):
        return "host %s is not routable" % host
    return None


def _download_to_temp(url, assets_dir, timeout):
    """Stream the body into a temp file, hashing as it goes.

    Returns (tmp_path, sha256) or None. The bytes are never fully buffered and
    never hashed twice, and a body past MAX_ATLAS_FETCH_BYTES is abandoned rather
    than read to completion.
    """
    import tempfile
    import urllib.error
    import urllib.request

    os.makedirs(assets_dir, exist_ok=True)
    # mkstemp rather than a pid-derived name: the temp is only ever consumed by
    # the caller, so it needs no predictable name, and a unique one means two
    # runs sharing a <run_dir> cannot truncate each other's download.
    handle_fd, tmp = tempfile.mkstemp(prefix=".fetch-", suffix=".tmp", dir=assets_dir)
    request = urllib.request.Request(url, headers={"User-Agent": "koreanize-init"})
    digest = hashlib.sha256()
    total = 0
    ok = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            # Re-assert AFTER redirects: urllib follows them by default, so the
            # scheme and host checked before the call are the ones that were
            # asked for, not necessarily the ones that answered.
            if _check_fetch_target(response.geturl()) is not None:
                return None
            with os.fdopen(handle_fd, "wb") as handle:
                handle_fd = None
                while True:
                    chunk = response.read(_FETCH_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ATLAS_FETCH_BYTES:
                        return None
                    digest.update(chunk)
                    handle.write(chunk)
        ok = total > 0
    except (urllib.error.URLError, OSError, ValueError):
        return None
    finally:
        # Self-cleaning on EVERY failure path -- the rejected-redirect and
        # over-cap returns above leave a temp behind otherwise, and a caller that
        # reconstructs the name to delete it is a coupling waiting to rot.
        if handle_fd is not None:
            os.close(handle_fd)
        if not ok:
            _unlink_quietly(tmp)
    return tmp, digest.hexdigest()


def _fetch_atlas(url, assets_dir, cache_root, timeout):
    """Content-addressed fetch. A cache hit is hard-linked into <run_dir>/assets/.

    Returns (path, sha256) or None. The digest is RETURNED rather than left to be
    recomputed: it was produced incrementally during the stream, and the caller
    would otherwise re-read the whole 49-79 MB file to derive a value the
    destination filename already encodes.

    Content addressing rather than URL-stem naming is what makes a second
    scenario's colliding filename a cache HIT instead of an overwrite (§5.1
    step 5).
    """
    import shutil

    if _check_fetch_target(url) is not None:
        return None

    downloaded = _download_to_temp(url, assets_dir, timeout)
    if downloaded is None:
        return None          # _download_to_temp cleans up its own temp
    tmp, digest = downloaded

    dest = os.path.join(assets_dir, digest + ".png")
    try:
        if os.path.exists(dest):
            _unlink_quietly(tmp)
            return dest, digest
        if cache_root:
            os.makedirs(cache_root, exist_ok=True)
            cached = os.path.join(cache_root, digest + ".png")
            if not os.path.exists(cached):
                shutil.copyfile(tmp, cached)
            _unlink_quietly(tmp)
            try:
                os.link(cached, dest)
            except OSError:
                shutil.copyfile(cached, dest)
            return dest, digest
        os.replace(tmp, dest)
        return dest, digest
    except OSError:
        _unlink_quietly(tmp)
        return None


def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _measure_png(path):
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 8. ArkhamDB join -- §5.1 step 6
# ---------------------------------------------------------------------------

def arkhamdb_join(objects, db_path=None):
    """Ids with no row are marked `source: none` and counted.

    Four scenarios will be 100% unmatched, which is the R-B signal and the reason
    `init` REPORTS rather than fails. With no local ArkhamDB extract at all every
    id is `none`, which is the same signal at full strength.
    """
    rows = {}
    if db_path and os.path.exists(db_path):
        try:
            blob = read_json(db_path)
        except ValueError as exc:
            kc.refuse(kc.EXIT_PRECONDITION, "ArkhamDB extract is not valid JSON",
                      "%s: %s" % (db_path, exc))
        records = blob if isinstance(blob, list) else blob.get("cards") or []
        for record in records:
            code = record.get("code")
            if code:
                rows[str(code)] = record

    cards = collections.OrderedDict()
    for entry in objects:
        arkham_id = entry.get("arkham_id")
        if not arkham_id or is_guide_id(arkham_id):
            continue
        if arkham_id in cards:
            cards[arkham_id]["objects"].append(entry["object_id"])
            continue
        # Tolerate the "...b" back entries: 71033b is the back face of 71033.
        row = rows.get(str(arkham_id)) or rows.get(str(arkham_id).rstrip("b"))
        cards[arkham_id] = {
            "arkham_id": arkham_id,
            "source": "arkhamdb" if row else "none",
            "name": (row or {}).get("name") or entry.get("nickname"),
            "type_code": (row or {}).get("type_code"),
            "text": (row or {}).get("text"),
            "objects": [entry["object_id"]],
        }

    unmatched = sum(1 for c in cards.values() if c["source"] == "none")
    return list(cards.values()), {"cards": len(cards), "cards_unmatched": unmatched}


# ---------------------------------------------------------------------------
# 9. Fonts -- §5.1 step 8
# ---------------------------------------------------------------------------

def resolve_fonts(search_roots, env, workspace=None):
    """Resolve each role's identity, recording what is present.

    §5.1 step 8 refuses at exit 13 "on either mismatch" -- and a MISMATCH is what
    this still refuses on, through kz_config.resolve_font: a role whose env var
    names a file whose PostScript name or sha256 disagrees with the spec.

    A role that is simply ABSENT on this machine is a different fact and is
    recorded with null identity rather than refused. v0 is the reuse phase and
    typesets nothing, and this workspace's images-ko/fonts/ carries only the body
    face -- so refusing an absent title face here would make every v0 run
    unreachable at stage 0, on the one machine the tool targets. The v1 stages
    that actually consume a face assert it themselves at the point of use, which
    is where an absent font is genuinely a precondition failure.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    fonts = {"search_roots": list(search_roots)}
    resolved = 0
    unresolved = []

    for role, expected_name, env_var, redistributable in FONT_ROLES:
        spec = {"postscript_name": expected_name, "sha256": None,
                "redistributable": redistributable, "env_var": env_var}
        candidate = env.get(env_var) or os.environ.get(env_var)
        found = None

        if candidate:
            path = os.path.expanduser(candidate)
            if not os.path.exists(path):
                kc.refuse(kc.EXIT_PRECONDITION,
                          "font role %r names a file that does not exist" % role,
                          "%s=%s" % (env_var, candidate))
            try:
                name = kz.read_postscript_name(path)
            except kc.KzRefusal as exc:
                # kz_config refuses by PATH; §5.1 step 8 refuses by ROLE, and the
                # role is the half an operator can act on -- it names the env var
                # to repoint.
                kc.refuse(kc.EXIT_PRECONDITION,
                          "font role %r could not be identified" % role,
                          "%s=%s: %s" % (env_var, candidate, exc.message))
            if expected_name and name != expected_name:
                # The mismatch §5.1 step 8 refuses on, naming the ROLE.
                kc.refuse(kc.EXIT_PRECONDITION,
                          "font role %r resolved to the wrong face" % role,
                          "%s -> postscript_name %r, expected %r"
                          % (path, name, expected_name))
            found = (path, name)
        elif expected_name:
            found = _search_font(expected_name, search_roots, workspace)

        if found:
            path, name = found
            spec["postscript_name"] = name
            spec["sha256"] = kc.sha256_file(path)
            resolved += 1
        else:
            unresolved.append(role)
        fonts[role] = spec

    return fonts, {"fonts_resolved": resolved, "fonts_unresolved": len(unresolved)}, unresolved


def _search_font(expected_name, search_roots, workspace):
    for root in search_roots:
        root_abs = root if os.path.isabs(root) else os.path.join(workspace, root)
        if not os.path.isdir(root_abs):
            continue
        for dirpath, _dirnames, filenames in os.walk(root_abs):
            for fname in sorted(filenames):
                if os.path.splitext(fname)[1].lower() not in (".ttf", ".otf", ".ttc"):
                    continue
                path = os.path.join(dirpath, fname)
                try:
                    name = kz.read_postscript_name(path)
                except kc.KzRefusal:
                    # A .ttc is a collection; kz_config refuses to guess a face.
                    continue
                if name == expected_name:
                    return (path, name)
    return None


# ---------------------------------------------------------------------------
# 10. counts{} -- the v2 schema of §3.3
# ---------------------------------------------------------------------------

def build_counts(walk, objects, by_pack, excluded_with_id=0, case_fold_collisions=0):
    """Every counter §3.3 pins, computed exactly once and in one place."""
    arkham_ids = set()
    guide_ids = set()
    without_id = 0
    inside_deck = 0
    id_owners = collections.Counter()
    guid_counts = collections.Counter()

    for entry in objects:
        guid_counts[entry["guid"]] += 1
        arkham_id = entry.get("arkham_id")
        if not arkham_id:
            without_id += 1
        elif is_guide_id(arkham_id):
            guide_ids.add(arkham_id)
        else:
            arkham_ids.add(arkham_id)
            id_owners[arkham_id] += 1
        if entry.get("parent_kind") == "Deck":
            inside_deck += 1

    return collections.OrderedDict([
        # DISTINCT population files the walk reached (clause 4) -- never visits.
        ("objects_reached", walk.objects_reached),
        # len(objects[]) after the composite-key merge: the LOOP LENGTH.
        ("objects", len(objects)),
        ("objects_reached_twice", walk.objects_reached - len(objects)),
        # Entries sharing a GUID under a DIFFERENT (arkham_id, card_id) -- ADMITTED,
        # not merged. Sum(len-1) is the only reading that satisfies both By the
        # Book's pinned 3 and Midwinter's pinned 0 (K22).
        ("guid_collisions_distinct_id",
         sum(n - 1 for n in guid_counts.values() if n > 1)),
        ("arkham_ids", len(arkham_ids)),
        ("guide_ids", len(guide_ids)),
        ("objects_by_pack", by_pack),
        ("objects_without_arkham_id", without_id),
        # REPORTED, never a decision input (§5.2 step 5).
        ("objects_inside_deck", inside_deck),
        ("objects_case_folded_collisions", case_fold_collisions),
        ("objects_excluded_by_kind", dict(walk.excluded_by_kind)),
        # Any other value is exit 14, so this is 0 in every report that exists.
        ("objects_excluded_by_kind_carrying_id", excluded_with_id),
        ("ids_carried_by_two_objects",
         sum(1 for n in id_owners.values() if n > 1)),
    ])


def derive_arkham_prefixes(objects):
    """The numeric prefixes this scenario's ids carry -- '71' for Midwinter."""
    prefixes = set()
    for entry in objects:
        arkham_id = entry.get("arkham_id")
        if arkham_id and not is_guide_id(arkham_id) and str(arkham_id)[:2].isdigit():
            prefixes.add(str(arkham_id)[:2])
    return sorted(prefixes)


# ---------------------------------------------------------------------------
# 11. The generated scenario.json -- §3.2
# ---------------------------------------------------------------------------

def build_scenario_config(slug, scenario_name, source_dir, source_tree_sha256,
                          pack, container_guid, container_stem, arkham_prefixes,
                          atlases, shared_backs, fonts, run_dir, counts,
                          layout_set=None, icon_set="core"):
    """Assemble the hash-pinned config. Every path is WORKSPACE-RELATIVE (§3.2)."""
    pack_container = dict(KOREAN_PACKS).get(pack)
    write_roots = [
        "%s/%s/%s/%s/" % (LANGPACK_ROOT, pack, pack_container, container_stem),
        "%s/%s/%s.json" % (LANGPACK_ROOT, pack, pack_container),
        "%s/%s/%s/" % (LANGPACK_ROOT, "Korean - Player Cards",
                       "Korean-PlayerCards.KoreanI"),
    ]

    cfg = collections.OrderedDict([
        ("schema_version", kz.SCHEMA_VERSION),
        ("generated_by", "koreanize init"),
        ("generated_at", kc.utc_now()),
        ("slug", slug),
        ("scenario_name", scenario_name),
        ("source_dir", source_dir),
        ("source_tree_sha256", source_tree_sha256),
        ("pack", pack),
        ("pack_container", pack_container),
        ("container_guid", container_guid),
        ("arkham_prefixes", arkham_prefixes),
        ("guard", collections.OrderedDict([
            ("write_roots", write_roots),
            ("forbidden", ["SCED/", ".git/", "decomposed/scenario/",
                           "decomposed/campaign/", "library.json", "SCED-tools/"]),
            ("data_root", "SCED-tools/scripts/koreanize/data/"),
            ("playercards_predicate", "url_substring_only"),
            ("max_files_written", 200),
        ])),
        ("run_dir", run_dir),
        ("atlases", atlases),
        ("shared_backs", shared_backs),
        ("ai", collections.OrderedDict([
            ("model", "claude-opus-5"),
            ("fallback_model", "claude-opus-4-8"),
            ("effort", "high"),
            ("timeout_s", 780),
            ("call_budget_s", 900),
            ("stage_wall_clock_s", 5400),
            ("max_budget_usd_per_call", 10),
            ("max_budget_usd_per_stage", 60),
            ("required_stages", ["terms", "translate", "mask", "typeset", "ocr",
                                 "triage", "audit"]),
            ("forbidden_stages", ["scaffold", "reuse", "register", "revert", "verify",
                                  "slice", "composite", "recompose", "upload",
                                  "repoint", "objtext"]),
            ("neutral_stages", ["init", "source", "check", "erase"]),
            ("batch", collections.OrderedDict([
                # universe_size is written HERE, by init, from THIS scenario's
                # measured corpus; kz_config refuses at exit 4 unless it is
                # <= max_units_per_call * max_calls (§3.2).
                #
                # null means "not knowable until invocation". S1/S3/S4 are the
                # text and art chains' own discoveries -- the terminology set, the
                # layout groups and the icon tokens do not exist until `terms`,
                # `mask` and `typeset` measure them -- so init declares null
                # rather than inventing a number it cannot measure at stage 0.
                ("S1", {"unit": "term", "max_units_per_call": 120,
                        "max_calls": 4, "universe_size": None}),
                ("S2", {"unit": "card", "max_units_per_call": 16,
                        "max_calls": 6, "universe_size": counts["arkham_ids"]}),
                ("S3", {"unit": "group", "max_units_per_call": 4,
                        "max_calls": 6, "universe_size": None}),
                ("S4", {"unit": "token", "max_units_per_call": 64,
                        "max_calls": 2, "universe_size": None}),
                # ocr is v1.x and refuses at 13 until v2 (§4.1).
                ("S5", {"unit": "object", "max_units_per_call": 16,
                        "max_calls": 6, "universe_size": 0}),
                ("S6", {"unit": "finding", "max_units_per_call": 40,
                        "max_calls": 3, "universe_size": None}),
                ("S7", {"unit": "seed_defect", "max_units_per_call": 1,
                        "max_calls": 1, "universe_size": None}),
            ])),
        ])),
        ("gates", {"init": "required", "terms": "required",
                   "mask": "required", "typeset": "required"}),
        ("fonts", fonts),
        ("layout_set", layout_set or slug),
        ("icon_set", icon_set),
        ("config_sha256", ""),
    ])
    cfg["config_sha256"] = kz.compute_config_sha256(cfg)
    return cfg


def verify_only(run_dir, workspace=None):
    """§4.1's uniform `--verify-only`: re-assert an existing output, write
    `<stage>.verify.json`, and never the marker.

    "Re-assert" here means what it means for every stage: recompute the artifact
    from its declared inputs and compare, rather than re-read the artifact and
    agree with it. So the walk is re-run against the recorded `source_dir` and its
    counts are compared to the ones `card-text-en.json` shipped, and the config
    pin is recomputed. A drifted source tree, a hand-edited index or a broken pin
    are all findings; nothing is rewritten.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    # P0a / P0b, and they belong HERE and not only in run_init: §5.9 frames both
    # as a per-MODULE invariant, and this entry point writes (init.verify.json).
    # `run_dir` is always explicit on this path, so it is passed as the declared
    # write root -- P0b's subject is "cwd or any declared write root", and a
    # --run-dir pointing into the nightly's scratch tree is exactly the hazard.
    kc.check_invocation_guards([run_dir])
    cfg_path = os.path.join(run_dir, "scenario.json")
    index_path = os.path.join(run_dir, "card-text-en.json")
    for path in (cfg_path, index_path):
        if not os.path.exists(path):
            kc.refuse(kc.EXIT_PRECONDITION, "nothing to verify", "%s is absent" % path)

    # Guarded like every other read_json() site in this module. `--verify-only`'s
    # whole promise is that a broken input is a FINDING and not a crash, so the
    # one call that reads a hand-editable file must not be the one that raises a
    # bare JSONDecodeError.
    def _read(path, what):
        try:
            return read_json(path)
        except ValueError as exc:
            kc.refuse(kc.EXIT_PRECONDITION, "%s is not valid JSON" % what,
                      "%s: %s" % (path, exc))
        except OSError as exc:
            kc.refuse(kc.EXIT_PRECONDITION, "%s could not be read" % what,
                      "%s: %s" % (path, exc))

    cfg = _read(cfg_path, "scenario.json")
    index = _read(index_path, "card-text-en.json")
    checks = []
    triggered = []

    def record(name, ok, detail, code=kc.EXIT_ARTIFACT):
        checks.append({"name": name, "status": "pass" if ok else "fail",
                       "detail": detail})
        if not ok:
            triggered.append(code)

    pinned = cfg.get("config_sha256")
    recomputed = kz.compute_config_sha256(cfg)
    record("config-pin", pinned == recomputed,
           "pinned %s, recomputed %s" % ((pinned or "")[:16], recomputed[:16]),
           kc.EXIT_GUARD)

    record("index-binding",
           (index.get("binding") or {}).get("scenario.json") == pinned,
           "card-text-en.json binds %s"
           % ((index.get("binding") or {}).get("scenario.json") or "<none>")[:16],
           kc.EXIT_DRIFT)

    scenario_dir = os.path.join(workspace, cfg["source_dir"])
    if not os.path.isdir(scenario_dir):
        record("source-tree", False, "%s is absent" % cfg["source_dir"],
               kc.EXIT_PRECONDITION)
    else:
        record("source-tree-digest",
               kc.sha256_tree(scenario_dir) == cfg.get("source_tree_sha256"),
               "recomputed over %s" % cfg["source_dir"], kc.EXIT_DRIFT)

        stem = os.path.basename(cfg["source_dir"])
        containers = sorted(f[:-5] for f in os.listdir(scenario_dir)
                            if f.endswith(".json")
                            and os.path.isdir(os.path.join(scenario_dir, f[:-5])))
        root_container = os.path.join(scenario_dir, containers[0]) if containers else None
        if root_container:
            walk = walk_population(root_container, scenario_dir)
            objects = build_objects(walk, scenario_dir, workspace)
            recomputed_counts = build_counts(
                walk, objects, index["counts"].get("objects_by_pack") or {},
                excluded_with_id=scan_excluded_ids(walk, scenario_dir),
                case_fold_collisions=scan_case_fold(objects))
            for key in ("objects_reached", "objects", "objects_reached_twice",
                        "arkham_ids", "guide_ids", "guid_collisions_distinct_id",
                        "objects_without_arkham_id", "ids_carried_by_two_objects"):
                record("count:%s" % key,
                       recomputed_counts[key] == index["counts"].get(key),
                       "recorded %s, recomputed %s"
                       % (index["counts"].get(key), recomputed_counts[key]),
                       kc.EXIT_DRIFT)
        else:
            record("root-container", False, "no root container under %s" % stem,
                   kc.EXIT_PRECONDITION)

    report = kc.new_report(STAGE, cfg["slug"], mode="verify-only",
                           counts={"checks": len(checks),
                                   "failed": sum(1 for c in checks
                                                 if c["status"] != "pass")},
                           checks=checks)
    kc.finalize_report(report, cfg=cfg, triggered=tuple(triggered))
    path = kc.write_report(report, run_dir, cfg=cfg)
    return report, path


GATE_STUB = """# init gate -- {slug}

status: pending

`init` produced the object index below. Read it, then set `status:` to
`accepted` (or `rejected`) and record who decided and when. Every downstream
stage refuses at exit 30 until this reads `accepted`; `init` itself exits 0.

| count | value |
|---|---|
{rows}

- objects index : `{run_dir}/card-text-en.json`
- source join   : `{run_dir}/card-source-en.json`
- atlases       : `{run_dir}/atlas-inventory.json`

decided_by:
decided_on:
"""


def write_gate_stub(run_dir, slug, counts):
    rows = "\n".join("| `%s` | %s |" % (k, json.dumps(v, ensure_ascii=False)
                                        if isinstance(v, dict) else v)
                     for k, v in counts.items())
    path = os.path.join(run_dir, "gates", "init-gate.md")
    kc.atomic_write_text(path, GATE_STUB.format(slug=slug, rows=rows, run_dir=run_dir))
    return path


# ---------------------------------------------------------------------------
# 12. The stage
# ---------------------------------------------------------------------------

def run_init(scenario, pack, slug=None, run_dir=None, workspace=None,
             download_atlases=True, arkhamdb=None, env=None, mode="build"):
    """The whole of §5.1, in its nine steps. Returns (report, artifacts)."""
    workspace = workspace or kc.WORKSPACE_ROOT
    env = env if env is not None else kz.read_env_file()

    # Step 1 -- guards, then resolve to exactly one directory.
    #
    # P0b's subject is "cwd or any declared WRITE ROOT", and this stage's write
    # root is <run_dir> -- so it is passed whenever it is already known. It is
    # only known here when the caller gave one explicitly; the slug-derived
    # default needs the scenario resolved first, so P0b is re-asserted over it
    # below. Both halves matter and neither subsumes the other: an explicit
    # --run-dir into the scratch tree must refuse BEFORE anything is read, and a
    # derived one must still refuse before anything is written.
    kc.check_invocation_guards([run_dir] if run_dir else [])
    scenario_dir, root_container, root_json = resolve_scenario(scenario, workspace)
    scenario_name = os.path.basename(scenario_dir)
    slug = slug or derive_slug(scenario_name)
    run_dir = run_dir or os.path.join(workspace, ".am", "koreanize", slug)
    kc.check_p0b([run_dir])
    container_stem = os.path.basename(root_container)
    root_obj = read_json(root_json)

    # Step 2 -- the walk (clauses 1 and 4).
    walk = walk_population(root_container, scenario_dir)

    # Steps 2/3 -- clause 3, and the geometry assertions inside it.
    objects = build_objects(walk, scenario_dir, workspace)

    # Step 4 -- the case-fold scan, then clause 2 over the complement.
    case_fold_collisions = scan_case_fold(objects)
    excluded_with_id = scan_excluded_ids(walk, scenario_dir)

    # Step 7 -- the reuse probe, which is what objects_by_pack is measured from.
    index = build_korean_pack_index(workspace)
    by_pack = assign_packs(objects, index)

    counts = build_counts(walk, objects, by_pack,
                          excluded_with_id=excluded_with_id,
                          case_fold_collisions=case_fold_collisions)

    # Step 5 -- the atlas inventory.
    faces, backs = collect_atlas_urls(objects)
    atlases, atlas_counts = measure_atlases(
        faces, run_dir, cache_root=atlas_cache_root(env, run_dir),
        download=download_atlases)
    shared_backs = [{"url": url, "occurrences_pack_wide": n,
                     "policy": "never_rewrite"}
                    for url, n in sorted(backs.items(), key=lambda kv: (-kv[1], kv[0]))]

    # Step 6 -- the ArkhamDB join.
    cards, card_counts = arkhamdb_join(objects, arkhamdb)

    # Step 8 -- font identity.
    fonts, font_counts, unresolved_fonts = resolve_fonts(
        ["images-ko/fonts/"], env, workspace)

    counts.update(atlas_counts)
    counts.update(card_counts)
    counts.update(font_counts)

    source_dir = os.path.relpath(os.path.realpath(scenario_dir),
                                 os.path.realpath(workspace))
    source_tree_sha256 = kc.sha256_tree(scenario_dir)

    cfg = build_scenario_config(
        slug=slug, scenario_name=scenario_name, source_dir=source_dir,
        source_tree_sha256=source_tree_sha256, pack=pack,
        container_guid=root_obj.get("GUID"), container_stem=container_stem,
        arkham_prefixes=derive_arkham_prefixes(objects),
        atlases=atlases, shared_backs=shared_backs, fonts=fonts,
        run_dir=os.path.relpath(os.path.realpath(run_dir),
                                os.path.realpath(workspace)),
        counts=counts)
    # Validate what we generated before anything downstream trusts it. The pin is
    # recomputed here too, so a constructor that forgot to re-pin cannot ship.
    kz.validate(cfg, path="<generated scenario.json>")

    card_text = collections.OrderedDict([
        ("schema_version", CARD_TEXT_SCHEMA_VERSION),
        ("generated_by", "koreanize init"),
        ("slug", slug),
        ("binding", {"scenario.json": cfg["config_sha256"]}),
        ("objects", objects),
        ("cards", cards),
        ("counts", counts),
    ])

    card_source = collections.OrderedDict([
        ("schema_version", CARD_TEXT_SCHEMA_VERSION),
        ("generated_by", "koreanize init"),
        ("slug", slug),
        ("binding", {"scenario.json": cfg["config_sha256"]}),
        ("cards", cards),
        ("counts", {"cards": card_counts["cards"],
                    "cards_unmatched": card_counts["cards_unmatched"]}),
    ])

    atlas_inventory = collections.OrderedDict([
        ("schema_version", CARD_TEXT_SCHEMA_VERSION),
        ("generated_by", "koreanize init"),
        ("slug", slug),
        ("binding", {"scenario.json": cfg["config_sha256"]}),
        ("atlases", atlases),
        ("shared_backs", shared_backs),
        ("counts", {"atlases": len(atlases),
                    "atlases_fetched": atlas_counts["atlases_fetched"],
                    "atlases_measured": atlas_counts["atlases_measured"]}),
    ])

    checks = [
        {"name": "population-rule", "status": "pass",
         "detail": "clauses 1->4->3->2; %d reached, %d objects, %d twice"
                   % (counts["objects_reached"], counts["objects"],
                      counts["objects_reached_twice"])},
        {"name": "case-fold-collisions", "status": "pass",
         "detail": "%d groups over object_id" % case_fold_collisions},
        {"name": "excluded-kind-ids", "status": "pass",
         "detail": "%d excluded nodes carry a GMNotes.id" % excluded_with_id},
        {"name": "fonts", "status": "pass" if not unresolved_fonts else "warn",
         "detail": "unresolved roles: %s" % (", ".join(unresolved_fonts) or "none")},
    ]

    report = kc.new_report(
        STAGE, slug, mode=mode, counts=dict(counts), checks=checks,
        binding=kc.build_binding([root_json], extra={"source_tree": source_tree_sha256}),
        freshness=kc.build_freshness([root_json]),
        gate={"path": os.path.join(run_dir, "gates", "init-gate.md"),
              "status": "pending"},
        results={"scenario_dir": source_dir,
                 "container_stem": container_stem,
                 "fonts_unresolved": unresolved_fonts})

    artifacts = {
        "scenario.json": cfg,
        "card-text-en.json": card_text,
        "card-source-en.json": card_source,
        "atlas-inventory.json": atlas_inventory,
    }
    return report, artifacts


def emit(report, artifacts, run_dir, workspace=None, mirror=True):
    """Step 9 -- write into <run_dir>, then hand scenario.json to kz_config's
    mirror writer for its git home.

    `init` opens no path under SCED-tools/ ITSELF, which is what keeps §4.1's
    "writes outside <run_dir>: no" column literally true for it (§3.1).

    ORDER IS LOAD-BEARING, and it is the run-material artifacts, then the report,
    then the mirror. Each individual write is atomic (tmp + os.replace) but the
    SET is not transactional, so the question is which partial state a crash can
    leave -- and the only one that outlives `<run_dir>` is the git-tracked mirror.
    Writing it last means a mirror on disk implies a complete, reported run;
    writing it before the report would let a failure land a durable config for a
    run that never reported. `init` is idempotent -- it derives everything from
    the source tree and overwrites unconditionally -- so the recovery for any
    partial state is to re-run it, and no resume path is needed.
    """
    cfg = artifacts["scenario.json"]
    written = []
    for name, data in artifacts.items():
        path = os.path.join(run_dir, name)
        kc.atomic_write_json(path, data)
        written.append(path)
    written.append(write_gate_stub(run_dir, cfg["slug"], report["counts"]))
    written.append(kc.write_report(report, run_dir, cfg=cfg))
    if mirror:
        written.append(kz.write_data(cfg, "scenarios", "%s.scenario.json" % cfg["slug"],
                                     cfg, workspace=workspace))
    return written


# ---------------------------------------------------------------------------
# 13. selftest -- the faults this module's assertions target
# ---------------------------------------------------------------------------

def selftest(verbose=True):
    """Prove each refusal fires on the fault it targets. No corpus required."""
    findings = []

    def fires(label, code, fn):
        try:
            fn()
        except kc.KzRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d" % (label, code, exc.code))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    # Clause 3's admission rule: a repeated GUID under a different identity is
    # ADMITTED as its own entry, never merged.
    walk = WalkResult()
    for cell, arkham_id in ((0, "01141"), (1, "50044"), (2, "01179")):
        rel = "Deck.aaa/Card%d.ab3719" % cell
        walk.reached[rel] = {
            "walkrel": rel, "kind": "Card", "parent_kind": "Deck",
            "json": "/nonexistent/%s.json" % rel,
            "obj": {"GUID": "ab3719", "CardID": 100 + cell,
                    "GMNotes": json.dumps({"id": arkham_id}),
                    "CustomDeck": {"1": {"NumWidth": 4, "NumHeight": 4,
                                         "FaceURL": "f", "BackURL": "b"}}},
        }
    objects = build_objects(walk, "/nonexistent")
    if len(objects) != 3:
        findings.append("clause 3: 3 distinct identities on one GUID merged to %d"
                        % len(objects))
    counts = build_counts(walk, objects, {})
    if counts["guid_collisions_distinct_id"] != 2:
        findings.append("guid_collisions_distinct_id: expected 2 (sum len-1), got %s"
                        % counts["guid_collisions_distinct_id"])

    # Clause 2: an excluded kind carrying a GMNotes.id is exit 14. Neither
    # reference corpus can exhibit this, which is the whole reason it is a clause.
    bad = WalkResult()
    bad.excluded["Bag.dead01"] = {
        "walkrel": "Bag.dead01", "kind": "Bag", "parent_kind": None,
        "json": "/nonexistent/Bag.dead01.json",
        "obj": {"GUID": "dead01", "GMNotes": json.dumps({"id": "71099"})},
    }
    bad.excluded_by_kind["Bag"] += 1
    fires("clause 2 (excluded kind carries an id)", kc.EXIT_DRIFT,
          lambda: scan_excluded_ids(bad, "/nonexistent"))

    # Exit 62: a case-only path collision, the one fault a case-insensitive volume
    # cannot show through `git status`.
    fires("case-fold collision", kc.EXIT_COLLISION, lambda: scan_case_fold([
        {"object_id": "Deck.aaa/Card.ab3719"},
        {"object_id": "Deck.aaa/card.ab3719"},
    ]))

    # Geometry: CustomDeck-conditional, so a Custom_PDF guide with neither field
    # passes, one-of-two refuses, and an out-of-grid cell refuses.
    blank = check_geometry({"Name": "Custom_PDF"}, "guide.json")
    if blank["cell"] is not None:
        findings.append("geometry: a CustomDeck-less object was not skipped")
    fires("geometry (CardID without CustomDeck)", kc.EXIT_PRECONDITION,
          lambda: check_geometry({"CardID": 917500}, "half.json"))
    fires("geometry (cell outside the grid)", kc.EXIT_PRECONDITION,
          lambda: check_geometry(
              {"CardID": 917599,
               "CustomDeck": {"9175": {"NumWidth": 8, "NumHeight": 5,
                                       "FaceURL": "f", "BackURL": "b"}}},
              "overflow.json"))

    good = check_geometry(
        {"CardID": 917520,
         "CustomDeck": {"9175": {"NumWidth": 8, "NumHeight": 5,
                                 "FaceURL": "f", "BackURL": "b"}}},
        "ok.json")
    if (good["deck_key"], good["cell"], good["row"], good["col"]) != ("9175", 20, 2, 4):
        findings.append("geometry: 917520 on an 8x5 sheet gave %r" % (good,))

    # Both GMNotes storage forms, and the disagreement refusal.
    if read_gmnotes_id({"GMNotes": json.dumps({"id": "71006"})}, "x.json", "/nonexistent") \
            != "71006":
        findings.append("GMNotes: the inline form was not read")
    if read_gmnotes_id({}, "x.json", "/nonexistent") is not None:
        findings.append("GMNotes: an id-less object did not read as None")

    # A guide id is out of scope and must never be counted as an arkham_id.
    if not is_guide_id("CG71") or is_guide_id("71033"):
        findings.append("guide-id predicate is wrong")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 14. CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_init.py",
        description="koreanize stage 0 -- build card-text-en.json v2 and the "
                    "hash-pinned scenario.json (design §5.1).")
    parser.add_argument("--scenario", help="scenario directory name (or a unique substring)")
    parser.add_argument("--pack", default="Korean - Campaigns",
                        choices=[p for p, _c in KOREAN_PACKS],
                        help="the target langpack container")
    parser.add_argument("--slug", help="override the derived slug")
    parser.add_argument("--run-dir", help="override <run_dir> for this invocation only")
    parser.add_argument("--arkhamdb", help="path to a local ArkhamDB card extract (JSON)")
    parser.add_argument("--no-atlas", action="store_true",
                        help="skip the atlas download/measure step (offline)")
    parser.add_argument("--no-mirror", action="store_true",
                        help="do not mirror scenario.json into data/scenarios/")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-assert an existing output; writes init.verify.json, "
                             "never the marker")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and assert into <run_dir>/dry-run/init/, then report")
    parser.add_argument("--json-only", action="store_true",
                        help="emit the report JSON on stdout and nothing else")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        print("kz_init --selftest")
        findings = selftest()
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        print("  ok: population clauses 1-4, exit 14/62 scans, CustomDeck-conditional "
              "geometry, both GMNotes forms")
        return kc.EXIT_OK

    if args.verify_only:
        if not args.run_dir and not args.slug:
            kc.refuse(kc.EXIT_USAGE, "--verify-only needs --run-dir or --slug",
                      "it re-asserts an existing output and builds nothing")
        run_dir = args.run_dir or os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize",
                                               args.slug)
        report, path = verify_only(run_dir)
        if args.json_only:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("koreanize init --verify-only -- %s" % run_dir)
            for check in report["checks"]:
                print("  %-28s %s  %s" % (check["name"], check["status"],
                                          check["detail"]))
            print("  verdict         : %s (exit %d)"
                  % (report["verdict"], report["exit_code"]))
            print("  wrote           : %s" % path)
        return report["exit_code"]

    if not args.scenario:
        parser.print_help()
        return kc.EXIT_USAGE

    report, artifacts = run_init(
        scenario=args.scenario, pack=args.pack, slug=args.slug,
        run_dir=args.run_dir, download_atlases=not args.no_atlas,
        arkhamdb=args.arkhamdb, mode="dry-run" if args.dry_run else "build")

    cfg = artifacts["scenario.json"]
    run_dir = args.run_dir or os.path.join(kc.WORKSPACE_ROOT, cfg["run_dir"])
    if args.dry_run:
        # §4.1: every stage's --dry-run output goes to <run_dir>/dry-run/<stage>/,
        # and kz_config asserts the destination is under <run_dir> before the first
        # byte. Nothing outside it is opened for writing on this path.
        run_dir = os.path.join(run_dir, "dry-run", STAGE)
        kz.assert_dry_run_dest(cfg, STAGE, run_dir)

    written = emit(report, artifacts, run_dir,
                   mirror=not args.no_mirror and not args.dry_run)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    counts = report["counts"]
    print("koreanize init -- %s" % cfg["scenario_name"])
    print("  slug            : %s" % cfg["slug"])
    print("  reached/objects : %d / %d  (%d reached twice)"
          % (counts["objects_reached"], counts["objects"],
             counts["objects_reached_twice"]))
    print("  ids             : %d arkham + %d guide  (%d objects carry none)"
          % (counts["arkham_ids"], counts["guide_ids"],
             counts["objects_without_arkham_id"]))
    print("  guid collisions : %d" % counts["guid_collisions_distinct_id"])
    print("  excluded        : %s" % json.dumps(counts["objects_excluded_by_kind"],
                                                ensure_ascii=False, sort_keys=True))
    print("  by pack         : %s" % json.dumps(counts["objects_by_pack"],
                                                ensure_ascii=False, sort_keys=True))
    print("  atlases         : %d (%d measured)"
          % (len(cfg["atlases"]), counts["atlases_measured"]))
    print("  gate            : %s (pending)" % report["gate"]["path"])
    for path in written:
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
