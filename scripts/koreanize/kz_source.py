#!/usr/bin/env python3
"""koreanize `source` -- the reuse resolver, which is the whole of v0's value.

Design §5.2. Three outcomes, three predicates, one escalation, plus a fourth
OUTCOME (`out_of_scope`) that is decided on the id's prefix before any donor is
looked up and is therefore not a fourth predicate (§3.4).

    0 donors                        -> manufacture
    1 donor                         -> reuse, confidence high, ai false
    >= 2 donors / grid conflict     -> triage (S6), then reuse or unresolved
    no arkham_id                    -> defer
    a CG* campaign-guide id         -> out_of_scope, BEFORE the donor lookup

WHAT THIS MODULE DELIBERATELY DOES NOT DO

`parent_kind` decides NOTHING. `init` records it and this stage reports it; that
is the whole of its role. The unconditional `parent_kind == "Deck"` -> `defer`
predicate an earlier draft carried takes the eight Challenge Scenarios from 222
resolved ids to 61 (§5.2 step 5) -- it does not shave v0's value, it removes it.
The shipped Midwinter pack settles the question the predicate was guessing at:
the Korean container is FLAT (64 files, no subdirectory), and 40 of 40
Deck-parented Midwinter objects carry an override whose FaceURL is genuinely
Korean. `test_koreanize_source.py` pins the deletion with a Deck-parented object
that must resolve to `reuse`.

`harvest` and `pdf` are §1.4 non-goals and have no branch here. Do not ship a
schema whose enum is wider than its producer.

THE KOREAN-ART PREDICATE IS A HARD FILTER

A donor whose Korean `FaceURL` equals the English source object's `FaceURL` for
the same id is a TEXT-ONLY override and buys no art. It is counted in BOTH
`donor_rejected_same_url_objects` and `donor_rejected_same_url_ids` -- two
counters and not one, because over the 16 target scenarios they measure 20 and
17 and §3.4 forbids reporting one as the other -- and it does not resolve.

THIS STAGE IS ON v0'S AI CRITICAL PATH

16 of the 226 reuse objects over the eight Challenge Scenarios escalate to S6 and
14 of those are at donors of DIFFERING GRIDS, each of which is
`--accept-donor-choice` or exit 22. So a missing CLAUDE_CODE_OAUTH_TOKEN stops
v0, not only v1 (§1.3). `--no-escalate` exists for an offline smoke run and is
never a degradation path: the ambiguous objects are recorded `unresolved`, the D2
check fails at exit 13, and the report is therefore not consumable, so nothing
downstream can mistake it for an adjudicated run.

WHERE `resolution[]` LIVES, AND WHY IT IS NOT A TOP-LEVEL KEY

§3.4 draws `source.json` with `stage`, `binding`, `resolution[]` and `counts{}`
as peers. Three of those four are §3.6 ENVELOPE fields, and `report_path()`
resolves a build-mode `source` report to `<run_dir>/source.json` -- the very path
§3.4 names. So §3.4's drawing is the envelope, abbreviated, and `source.json` is
the stage report rather than a second file beside it. Writing both is not an
option: the second write silently destroys the first, which is what an earlier
draft of this module did.

`resolution[]` therefore lands in the envelope's declared `results` section
rather than as an undeclared top-level key. That is the same rule §3.6 states for
`registration{added[], removed[]}` -- "part of the envelope rather than an
undeclared field, because §8.4's revert step 3 operates on registration.added[]
and a rollback path cannot read an input the contract does not define" -- applied
to the input `scaffold`, `reuse`, `objtext` and `register` all read. Consumers
use `resolution_of(report)` below rather than indexing the path by hand.

One consequence is deliberate and is the house rule, not an omission: on the
PRECONDITION path `results` is literal null, so a `--no-escalate` run publishes
no resolution at all. That is correct -- an unadjudicated resolution is exactly
the artifact nothing downstream should be able to read -- and the failing D2
check names the objects instead.

INTERPRETER TIER: art (`#!/usr/bin/env python3`). This module is not in
`kz_common.STDLIB_TIER` -- it imports `kz_triage`, which is art tier.
"""

import argparse
import collections
import json
import os
import re
import sys

import kz_common as kc
import kz_config as kz
import kz_init as ki
import kz_triage as kt

STAGE = "source"

# NOT declare_ai(). `source` is one of the four NEUTRAL stages (§4.1): it neither
# requires AI nor is forbidden it -- it ESCALATES to `triage`, which owns S6 and
# declares the contract. declare_ai only expresses required/forbidden, so calling
# it here would assert `source` into a partition scenario.json does not put it in
# and refuse at exit 4 the moment a config is bound.

SCHEMA_VERSION = "1.1.0"

DECISIONS = ("reuse", "manufacture", "defer", "unresolved", "out_of_scope")

#: The donor keys an S6 ruling names. S6's `value` is closed to
#: `{owner_stage, suggested_action}` (kz_decide.STAGE_VALUE_SCHEMA) and cannot be
#: widened from here -- that schema is one owner's, and drift between what the
#: model was told and what is enforced is exactly what the single-owner rule
#: prevents. So the choice is carried in the free-text field under a token
#: contract stated in the unit payload itself: each candidate is labelled D0, D1,
#: ... and `suggested_action` must name EXACTLY ONE of them. Zero or two matches
#: is not a choice, and an unparseable ruling resolves `unresolved` rather than
#: silently picking the first donor.
DONOR_TOKEN_RE = re.compile(r"\bD(\d{1,2})\b")

#: The verdicts a donor choice may arrive under. `abstain` is excluded by
#: construction -- it is the model saying it could not tell, which is the correct
#: outcome when the material is insufficient and must not become a pick.
CHOICE_VERDICTS = ("tolerance", "defect")


# ---------------------------------------------------------------------------
# 1. korean-pack-index.json -- the donor index (§5.2 step 1)
# ---------------------------------------------------------------------------
#
# `kz_init.build_korean_pack_index` is the LIGHT half: id -> set of packs, which
# is all the human gate needs to see the reuse set before deciding scope. This is
# the full one -- id -> ordered donor records with geometry -- and it is a
# separate function rather than a widening of that one because the two have
# different costs and different consumers, and `init` is re-run repeatedly while
# an operator works its gate.


def _donor_record(pack, container, relfile, obj):
    """One donor, or None when the override carries no usable art.

    An override with no CustomDeck is a text-only override in the strongest
    sense -- it has no atlas at all -- and is skipped here rather than being
    admitted and rejected later, because `donor_rejected_same_url_*` is a
    URL-equality counter and this is not that fault.
    """
    decks = obj.get("CustomDeck")
    card_id = obj.get("CardID")
    if not isinstance(decks, dict) or len(decks) != 1 or not isinstance(card_id, int):
        return None
    deck_key, deck = next(iter(decks.items()))
    face_url = deck.get("FaceURL")
    if not face_url:
        return None
    return collections.OrderedDict([
        ("pack", pack),
        ("container", container),
        ("file", relfile),
        ("guid", obj.get("GUID")),
        ("card_id", card_id),
        ("deck_key", str(deck_key)),
        ("cell", card_id % 100),
        ("num_width", deck.get("NumWidth")),
        ("num_height", deck.get("NumHeight")),
        ("face_url", face_url),
        ("back_url", deck.get("BackURL")),
        ("back_is_hidden", deck.get("BackIsHidden")),
        ("type", deck.get("Type")),
        ("nickname", obj.get("Nickname")),
        ("description", obj.get("Description")),
    ])


def donor_index_cache_path(workspace, digest):
    return os.path.join(workspace, ".am", "koreanize", "_cache",
                        "donor-index.%s.json" % digest[:16])


DONOR_CACHE_KEEP = 3

#: (workspace, digest) -> index. One live entry per distinct pack state, so a
#: single-workspace process holds exactly one -- but a process that walks several
#: workspaces (a test run) would hold one per workspace per state, each ~4 MB of
#: donor records. Capped rather than left to grow: the entry that matters is the
#: one just used, and a miss costs a rebuild, never a wrong answer.
_INDEX_MEMO = collections.OrderedDict()
_INDEX_MEMO_MAX = 4


def _memo_put(key, value):
    _INDEX_MEMO[key] = value
    _INDEX_MEMO.move_to_end(key)
    while len(_INDEX_MEMO) > _INDEX_MEMO_MAX:
        _INDEX_MEMO.popitem(last=False)
    return value


def build_donor_index(workspace=None, use_cache=True, digest=None):
    """id -> [donor, ...] over all three Korean packs, in KOREAN_PACKS order.

    Ordered, not a set: the order is the pack precedence `init` already uses, so
    a one-donor id and a >=2-donor id are read the same way and the escalation's
    D0/D1 labels are stable across runs.

    BOUND AND CACHED BY THE PACK TREE DIGEST, which §5.2 step 1 requires --
    "bind by a tree digest so it is rebuilt when the packs move" -- and which
    `kz_init.build_korean_pack_index` already implements for its lighter
    membership-only walk. This one does strictly more per file (a donor record
    with geometry, not a set membership) over the same ~6,008 ids and 2,138+
    files, so it is the one that most needed it.

    The digest is over the packs as INPUT, not over this function's own output,
    and that distinction is the operator-visible half of the requirement: a
    binding taken over the emitted `korean-pack-index.json` is compared by
    `--status` against the very file `source` just wrote, so it can never go
    stale and the "rebuilt when the packs move" guarantee becomes undetectable.
    `run_source` therefore puts THIS value in the report's `binding{}`.

    A cache miss simply rebuilds, so a stale or corrupt entry costs a rebuild and
    never a wrong answer.
    """
    workspace = workspace or kc.WORKSPACE_ROOT

    # An in-process memo keyed on the DIGEST, which makes it correct by
    # construction: if the packs move the key moves and the entry is rebuilt, so
    # this can never serve a stale index. It exists because the on-disk cache is
    # a wash when the page cache is warm -- measured on this volume, a cache HIT
    # (0.847 s: parse 4.4 MB and rebuild ~6,000 records) costs exactly what the
    # full walk costs (0.847 s), and the disk cache only wins COLD (10.57 s ->
    # 1.58 s). One `source` run per process is the operator's case and neither
    # matters there; the test suite drives 24 scenarios in one process, where
    # this turns 24 rebuilds into one.
    memo_key = (os.path.realpath(workspace), digest)
    if use_cache and digest is not None and memo_key in _INDEX_MEMO:
        _INDEX_MEMO.move_to_end(memo_key)
        return _INDEX_MEMO[memo_key]

    cache_path = None
    if use_cache:
        # The digest is passed in by `run_source`, which needs the same value for
        # the report's binding{}. Computing it here as well stats all ~6,000 pack
        # files a second time -- measured at 1.98 s a call on this volume, i.e.
        # more than the 1.58 s a cache HIT costs in total, so the redundant walk
        # was the larger half of the very cost the cache exists to remove.
        cache_path = donor_index_cache_path(
            workspace, digest if digest is not None else pack_tree_digest(workspace))
        if os.path.exists(cache_path):
            try:
                # json.load already yields dicts in file order and 3.7+ dicts
                # preserve it, so rebuilding each of ~6,000 records as an
                # OrderedDict was pure overhead on the hot path.
                cached = ki.read_json(cache_path)
                if digest is not None:
                    _memo_put(memo_key, cached)
                return cached
            except (ValueError, OSError, AttributeError, TypeError):
                pass   # a bad cache entry is a rebuild, never a wrong answer

    index = collections.defaultdict(list)
    for pack, container in ki.KOREAN_PACKS:
        base = os.path.join(workspace, ki.LANGPACK_ROOT, pack, container)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for fname in sorted(filenames):
                if not fname.endswith(".json"):
                    continue
                path = os.path.join(dirpath, fname)
                try:
                    obj = ki.read_json(path)
                except (ValueError, OSError):
                    continue
                if not isinstance(obj, dict):
                    continue
                inline = obj.get("GMNotes")
                if not isinstance(inline, str) or not inline.strip():
                    continue
                try:
                    arkham_id = json.loads(inline).get("id")
                except ValueError:
                    continue
                if not arkham_id:
                    continue
                rel = os.path.relpath(path, base)
                top = rel.split(os.sep)[0]
                donor = _donor_record(pack, top if os.sep in rel else None,
                                      rel.replace(os.sep, "/"), obj)
                if donor is not None:
                    index[str(arkham_id)].append(donor)

    index = dict(index)
    if digest is not None:
        _memo_put(memo_key, index)
    if cache_path:
        try:
            kc.atomic_write_json(cache_path,
                                 collections.OrderedDict(sorted(index.items())))
            _evict_donor_cache(os.path.dirname(cache_path))
        except OSError:
            pass   # the cache is an optimisation; failing to write one is not an error
    return index


def pack_tree_digest(workspace=None):
    """The binding §5.2 step 1 names, shared with `kz_init`'s lighter probe.

    Delegated rather than reimplemented: two digests over the same three
    directories that disagreed would mean `init` and `source` could each believe
    the packs were unchanged while the other rebuilt.
    """
    return ki._pack_tree_digest(workspace or kc.WORKSPACE_ROOT)


def _evict_donor_cache(cache_dir, keep=DONOR_CACHE_KEEP):
    """Every langpack edit moves the digest and mints a new filename, so without
    eviction this directory grows once per pack change, forever. The entries are
    pure derived data -- deleting a live one costs one rebuild."""
    try:
        entries = [f for f in os.listdir(cache_dir)
                   if f.startswith("donor-index.") and f.endswith(".json")]
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
        try:
            os.unlink(path)
        except OSError:
            pass


def index_document(index, binding=None):
    """`korean-pack-index.json` as it lands in <run_dir>."""
    return collections.OrderedDict([
        ("schema_version", SCHEMA_VERSION),
        ("generated_by", "koreanize source"),
        ("binding", binding or {}),
        ("donors", collections.OrderedDict(
            (k, index[k]) for k in sorted(index))),
        ("counts", {"ids": len(index),
                    "donors": sum(len(v) for v in index.values())}),
    ])


# ---------------------------------------------------------------------------
# 2. The Korean-art predicate (§5.2 step 3, §3.4)
# ---------------------------------------------------------------------------


def english_face_url(entry):
    """The English source object's FaceURL for this object.

    `card-text-en.json`'s `atlas_id` IS the FaceURL -- `kz_init.build_objects`
    stores it under that name because downstream stages key atlases by URL.
    """
    return entry.get("atlas_id")


def partition_donors(entry, donors):
    """(kept, rejected_same_url) for one object.

    The rejection is by URL EQUALITY against this object's own English atlas, not
    against a global set: two objects of one id may sit on different English
    atlases, and a donor is text-only relative to the object it would override.
    """
    english = english_face_url(entry)
    kept, rejected = [], []
    for donor in donors:
        if english and donor.get("face_url") == english:
            rejected.append(donor)
        else:
            kept.append(donor)
    return kept, rejected


def _annotate(donor, entry):
    out = collections.OrderedDict(donor)
    english = english_face_url(entry)
    out["english_face_url"] = english
    out["face_url_differs_from_english"] = bool(
        english and donor.get("face_url") != english)
    return out


def differing_grid(donors):
    grids = {(d.get("num_width"), d.get("num_height")) for d in donors}
    return len(grids) > 1


# ---------------------------------------------------------------------------
# 3. The escalation payload and the ruling parser (§5.2 step 4)
# ---------------------------------------------------------------------------


def escalation_detail(entry, donors):
    """One `checks[].detail[]` entry -- the structured form `kz_triage` accepts.

    `key` is the object id and not the arkham id: two objects can carry one id
    (71033 -> 4a2568, ccce29) and `universe_from_gate` refuses a duplicate unit
    id, so keying on the arkham id would refuse at exit 13 on the very corpus
    §5.8 C2 pins.
    """
    labelled = []
    for pos, donor in enumerate(donors):
        labelled.append("D%d = %s %s (%sx%s, cell %s)"
                        % (pos, donor.get("pack"), donor.get("file"),
                           donor.get("num_width"), donor.get("num_height"),
                           donor.get("cell")))
    return collections.OrderedDict([
        ("key", entry["object_id"]),
        ("kind", "donor_choice"),
        ("differing_grid", differing_grid(donors)),
        ("equal_confidence", True),
        ("arkham_id", entry.get("arkham_id")),
        ("guid", entry.get("guid")),
        ("card_id", entry.get("card_id")),
        ("cell", entry.get("cell")),
        ("num_width", entry.get("num_width")),
        ("num_height", entry.get("num_height")),
        ("donor_keys", ["D%d" % pos for pos in range(len(donors))]),
        ("donors", donors),
        ("detail",
         "%d Korean donors for %s; name EXACTLY ONE of %s in suggested_action. %s"
         % (len(donors), entry.get("arkham_id"),
            ", ".join("D%d" % p for p in range(len(donors))),
            "; ".join(labelled))),
    ])


def escalation_gate(details):
    """The gate report `kz_triage.escalate` adjudicates.

    exit_on_fail is 22 and not 11: 11 is the AI-policy code and routes an
    operator to decide.json, a file that has nothing to say about a tie between
    two donors (§5.2 step 4).
    """
    return collections.OrderedDict([
        ("schema_version", kc.SCHEMA_VERSION),
        ("stage", STAGE),
        ("verdict", "FAIL"),
        ("checks", [collections.OrderedDict([
            ("id", "D2"),
            ("name", "donor_ambiguity"),
            ("status", "fail"),
            ("exit_on_fail", kc.EXIT_TOLERANCE),
            ("detail", details),
        ])]),
    ])


def parse_donor_choice(ruling, donor_count):
    """(index, reason). `index` is None when the ruling names no single donor."""
    if not isinstance(ruling, dict):
        return None, "no ruling"
    verdict = ruling.get("verdict")
    if verdict not in CHOICE_VERDICTS:
        return None, "verdict %r is not a choice" % verdict
    if ruling.get("confidence") != "high":
        return None, "confidence %r is not high" % ruling.get("confidence")
    action = ((ruling.get("value") or {}).get("suggested_action") or "")
    found = {int(m) for m in DONOR_TOKEN_RE.findall(action)}
    found = {i for i in found if 0 <= i < donor_count}
    if len(found) != 1:
        return None, ("suggested_action names %d of the %d donor keys"
                      % (len(found), donor_count))
    return sorted(found)[0], None


def rulings_by_unit(triage_report):
    """unit_id -> ruling, from a triage stage report's results block."""
    out = {}
    for ruling in ((triage_report or {}).get("results") or {}).get("rulings") or []:
        out[ruling.get("unit_id")] = ruling
    return out


def unit_id_for(entry):
    """The id `kz_triage.universe_from_gate` mints for this object's finding."""
    return "%s:D2:%s" % (STAGE, entry["object_id"])


def resolution_of(report):
    """The per-object decisions, from a `source` stage report.

    The one accessor every downstream stage uses, so the placement decision
    argued in the module docstring lives in exactly one place. A report on the
    PRECONDITION path carries `results: null` and yields the empty list, which is
    the correct reading: nothing was adjudicated.
    """
    return ((report or {}).get("results") or {}).get("resolution") or []


def decisions_by_object(report):
    """object_id -> decision, for §1.2's per-object predecessor disjunct."""
    return dict((e["object_id"], e["decision"]) for e in resolution_of(report))


# ---------------------------------------------------------------------------
# 4. The selector (§5.2 steps 2-6)
# ---------------------------------------------------------------------------


class Resolution(object):
    """The pass-1 result: decided entries plus the ambiguity that needs S6."""

    def __init__(self):
        self.entries = []                       # source.json resolution[]
        self.by_object = {}                     # object_id -> entry
        self.ambiguous = collections.OrderedDict()   # object_id -> (obj, donors)
        self.rejected_objects = 0
        self.rejected_ids = set()


def select(objects, index):
    """Pass 1: everything but the escalation. Pure -- no AI, no I/O."""
    res = Resolution()
    for obj in objects:
        arkham_id = obj.get("arkham_id")
        base = collections.OrderedDict([
            ("object_id", obj["object_id"]),
            ("guid", obj.get("guid")),
            ("arkham_id", arkham_id),
            ("parent_kind", obj.get("parent_kind")),
        ])

        # Step 2 -- the campaign-guide prefix, BEFORE any donor lookup. Without
        # this branch the guide falls through to step 4, finds no donor and is
        # filed as `manufacture`, i.e. queued for a manufacturing path §1.4 says
        # will never be built -- and `counts.out_of_scope` is absent from the
        # totality identity §3.4 asserts.
        if ki.is_guide_id(arkham_id):
            base["decision"] = "out_of_scope"
            base["reason"] = "campaign-guide id %s; §1.4 puts the guide PDF out of scope" % arkham_id
            res.entries.append(base)
            res.by_object[obj["object_id"]] = base
            continue

        # Step 6 -- no id at all. This is a Card / Custom_PDF / CardCustom that
        # reached objects[] and carries no GMNotes id; the Custom_Tile class is
        # excluded by KIND before the loop and never arrives here.
        if not arkham_id:
            base["decision"] = "defer"
            base["reason"] = "no GMNotes id"
            res.entries.append(base)
            res.by_object[obj["object_id"]] = base
            continue

        donors = index.get(str(arkham_id)) or []
        kept, rejected = partition_donors(obj, donors)
        if rejected:
            res.rejected_objects += 1
            res.rejected_ids.add(str(arkham_id))

        if not kept:
            base["decision"] = "manufacture"
            base["reason"] = ("no donor with Korean art"
                              if not rejected
                              else "%d donor(s) rejected: FaceURL equals the "
                                   "English source (text-only override)" % len(rejected))
            base["donor_candidates"] = []
            res.entries.append(base)
            res.by_object[obj["object_id"]] = base
            continue

        annotated = [_annotate(d, obj) for d in kept]
        if len(annotated) == 1:
            base["decision"] = "reuse"
            base["donor"] = annotated[0]
            base["donor_candidates"] = annotated
            base["confidence"] = "high"
            base["source"] = "id_exact"
            base["ai"] = False
            res.entries.append(base)
            res.by_object[obj["object_id"]] = base
            continue

        # >= 2 donors: the S6 escalation. The entry is appended now so
        # resolution[] keeps card-text-en.json's object order; pass 2 fills it.
        base["decision"] = "unresolved"
        base["reason"] = "%d Korean donors; awaiting triage(S6)" % len(annotated)
        base["donor_candidates"] = annotated
        base["ai"] = True
        base["ai_stage"] = kt.SID
        res.entries.append(base)
        res.by_object[obj["object_id"]] = base
        res.ambiguous[obj["object_id"]] = (obj, annotated)
    return res


def apply_rulings(res, triage_report):
    """Pass 2: fold S6's rulings back into the entries it was asked about."""
    rulings = rulings_by_unit(triage_report)
    applied = 0
    for object_id, (obj, donors) in res.ambiguous.items():
        entry = res.by_object[object_id]
        ruling = rulings.get(unit_id_for(obj))
        pos, why = parse_donor_choice(ruling, len(donors))
        if pos is None:
            entry["decision"] = "unresolved"
            entry["reason"] = "triage(S6) named no single donor: %s" % why
            continue
        entry["decision"] = "reuse"
        entry["donor"] = donors[pos]
        entry["confidence"] = ruling.get("confidence")
        entry["source"] = "triage"
        entry["rationale"] = ruling.get("rationale")
        entry.pop("reason", None)
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# 5. Counts and the totality identity (§3.4)
# ---------------------------------------------------------------------------


def build_counts(res, objects, card_counts=None):
    counts = collections.Counter()
    for entry in res.entries:
        counts[entry["decision"]] += 1
    resolved_ids = {str(e["arkham_id"]) for e in res.entries
                    if e["decision"] == "reuse" and e.get("arkham_id")}
    out = collections.OrderedDict()
    for name in DECISIONS:
        out[name] = counts.get(name, 0)
    out["objects"] = len(res.entries)
    # A reason counter, NOT an outcome: an object whose only donor was rejected
    # is counted here AND resolved to `manufacture`, so the two sets overlap by
    # construction and the totality identity above still holds.
    out["donor_rejected_same_url_objects"] = res.rejected_objects
    out["donor_rejected_same_url_ids"] = len(res.rejected_ids)
    # The id-level companion. len(distinct ids), never an object count -- the
    # project's headline figure is an id figure and 222 of 274 names THIS field.
    out["arkham_ids_resolved"] = len(resolved_ids)
    out["escalations"] = len(res.ambiguous)
    out["escalations_differing_grid"] = sum(
        1 for _obj, donors in res.ambiguous.values() if differing_grid(donors))
    out["objects_inside_deck"] = sum(
        1 for obj in objects if obj.get("parent_kind") == "Deck")
    if card_counts:
        # Copied from card-text-en.json so the denominator is unambiguous:
        # counts.arkham_ids EXCLUDES the CG* guide ids that counts.guide_ids
        # carries, and 274 + 7 = 281 is the reconciliation over the eight.
        out["arkham_ids"] = card_counts.get("arkham_ids")
        out["guide_ids"] = card_counts.get("guide_ids")
    return out


def assert_totality(counts):
    """reuse + manufacture + defer + unresolved + out_of_scope == objects.

    Asserted, not reported (§1.3). 67 and not 13: the artifact was produced and
    then failed structural verification, which is a different first move from a
    missing input.
    """
    total = sum(counts[name] for name in DECISIONS)
    if total != counts["objects"]:
        kc.refuse(kc.EXIT_ARTIFACT,
                  "the §3.4 totality identity does not hold: %d != %d"
                  % (total, counts["objects"]),
                  "reuse %d + manufacture %d + defer %d + unresolved %d + "
                  "out_of_scope %d against counts.objects %d"
                  % tuple([counts[n] for n in DECISIONS] + [counts["objects"]]))
    return True


# ---------------------------------------------------------------------------
# 6. The stage
# ---------------------------------------------------------------------------


def run_source(run_dir, mode="build", escalate=True, accept_donor_choice=False,
               replay=None, claude_bin=None, workspace=None, quiet=False):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. <run_dir>/card-text-en.json exists and binds to that pin   -> exit 13
      4. `init`'s report is consumable                             -> exit 13

    Returns (report, artifacts, triage_report).
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    card_text_path = os.path.join(run_dir, "card-text-en.json")
    if not os.path.exists(card_text_path):
        kc.refuse(kc.EXIT_PRECONDITION, "card-text-en.json is missing",
                  "run `koreanize init` first; %s" % card_text_path)
    card_text = ki.read_json(card_text_path)
    pinned = (card_text.get("binding") or {}).get("scenario.json")
    if pinned and pinned != cfg["config_sha256"]:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "card-text-en.json was built against a different scenario.json",
                  "it binds %s; this config pins %s"
                  % (pinned[:16], cfg["config_sha256"][:16]))

    objects = card_text.get("objects") or []
    card_counts = card_text.get("counts") or {}

    packs_digest = pack_tree_digest(workspace)
    index = build_donor_index(workspace, digest=packs_digest)
    index_doc = index_document(index, binding={"scenario.json": cfg["config_sha256"],
                                               "korean-packs@tree": packs_digest})

    res = select(objects, index)

    triggered = []
    checks = []
    triage_report = None
    ai_summary = None

    if res.ambiguous and escalate:
        gate = escalation_gate([escalation_detail(obj, donors)
                                for obj, donors in res.ambiguous.values()])
        triage_report, _ask_dir = kt.escalate(
            cfg, gate, run_dir, mode=mode,
            accept_donor_choice=accept_donor_choice, replay=replay,
            claude_bin=claude_bin, workspace=workspace, quiet=quiet)
        if triage_report["exit_code"] != kc.EXIT_OK:
            triggered.append(triage_report["exit_code"])
        applied = apply_rulings(res, triage_report)
        ai_summary = {
            "delegated_to": kt.STAGE,
            "stage_id": kt.SID,
            "report": os.path.relpath(
                kc.report_path(run_dir, kt.STAGE, triage_report["mode"]), workspace),
            "escalations": len(res.ambiguous),
            "choices_applied": applied,
            "decide_outcome": (triage_report.get("ai") or {}).get("decide_outcome"),
        }
        checks.append({
            "id": "D2", "name": "donor_ambiguity",
            "status": "pass" if applied == len(res.ambiguous) else "fail",
            "exit_on_fail": kc.EXIT_TOLERANCE,
            "detail": ["%s: %s" % (oid, res.by_object[oid].get("reason"))
                       for oid in res.ambiguous
                       if res.by_object[oid]["decision"] != "reuse"],
        })
        if applied != len(res.ambiguous):
            triggered.append(kc.EXIT_TOLERANCE)
    elif res.ambiguous:
        # --no-escalate. Not a degradation path: the check FAILS at 13, so the
        # report can never be consumable and `scaffold` refuses at the dispatcher
        # rather than planning against an unadjudicated resolution.
        checks.append({
            "id": "D2", "name": "donor_ambiguity", "status": "fail",
            "exit_on_fail": kc.EXIT_PRECONDITION,
            # Named here rather than in `results`, because the PRECONDITION path
            # nulls `results` and this is then the only record of WHICH objects
            # were left unadjudicated.
            "detail": ["%d object(s) have >= 2 Korean donors and --no-escalate "
                       "was given; triage(S6) is required to resolve them"
                       % len(res.ambiguous)]
                      + ["%s (%s): %d donors"
                         % (oid, obj.get("arkham_id"), len(donors))
                         for oid, (obj, donors) in list(res.ambiguous.items())[:20]],
        })
        triggered.append(kc.EXIT_PRECONDITION)
    else:
        checks.append({"id": "D2", "name": "donor_ambiguity", "status": "pass",
                       "exit_on_fail": kc.EXIT_TOLERANCE, "detail": []})

    counts = build_counts(res, objects, card_counts)
    assert_totality(counts)
    checks.insert(0, {
        "id": "D1", "name": "totality", "status": "pass",
        "exit_on_fail": kc.EXIT_ARTIFACT,
        "detail": ["reuse %d + manufacture %d + defer %d + unresolved %d + "
                   "out_of_scope %d == objects %d"
                   % tuple([counts[n] for n in DECISIONS] + [counts["objects"]])],
    })
    checks.append({
        "id": "D3", "name": "donor_rejected_same_url",
        "status": "pass", "exit_on_fail": kc.EXIT_ARTIFACT,
        "detail": ["%d object(s) over %d id(s); the two are counted separately "
                   "because they are not equal (§3.4)"
                   % (counts["donor_rejected_same_url_objects"],
                      counts["donor_rejected_same_url_ids"])],
    })

    results = collections.OrderedDict([
        ("resolution", res.entries),
        ("escalation", ai_summary),
    ])

    # The bytes `emit` will actually write, not a compact re-serialisation:
    # a binding computed differently from the writer is never equal to the file,
    # so the stage would read STALE on the very next --status.
    index_sha = kc.sha256_bytes(kc.json_bytes(index_doc))
    # `korean-packs@tree` is the one that can actually go stale, and it is the
    # point. The other two entries bind files this stage WROTE, so comparing them
    # against disk can only ever confirm that nothing overwrote them; the donor
    # packs are an INPUT this stage does not own, and §5.2 step 1's "rebuilt when
    # the packs move" is unobservable without a binding over them. `--status`
    # skips it as a staleness candidate (it is a digest, not a path) and it is
    # carried so `source --verify-only` and an operator can compare it.
    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=dict(counts), checks=checks,
        binding=kc.build_binding([scenario_path, card_text_path],
                                 extra={"korean-pack-index.json": index_sha,
                                        "korean-packs@tree": packs_digest}),
        freshness=kc.build_freshness([scenario_path, card_text_path],
                                     upstream_report_path=kc.report_path(
                                         run_dir, "init", "build")),
        accepted={kt.TOLERANCE: bool(accept_donor_choice)},
        results=results)
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    # The house rule: on the PRECONDITION path every result section is written as
    # literal null, never {} or []. Here that means an UNADJUDICATED resolution is
    # never published, which is the point (see the module docstring).
    if report["verdict"] == "PRECONDITION":
        report["results"] = None

    artifacts = collections.OrderedDict([
        ("korean-pack-index.json", index_doc),
    ])
    return report, artifacts, triage_report


def emit(report, artifacts, run_dir, cfg=None):
    """Run material only -- `source` writes nothing outside <run_dir> (§4.1).

    Order is the run-material artifacts, then the report, for the same reason
    `init`'s is: a report on disk implies its inputs are on disk beside it.
    """
    written = []
    for name, data in artifacts.items():
        path = os.path.join(run_dir, name)
        kc.atomic_write_json(path, data)
        written.append(path)
    written.append(kc.write_report(report, run_dir, cfg=cfg))
    return written


def verify_only(run_dir, workspace=None):
    """Re-assert an existing source.json; write source.verify.json, never the
    marker (§4.1)."""
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])
    cfg = kz.load_scenario(os.path.join(run_dir, "scenario.json"))
    path = kc.report_path(run_dir, STAGE, "build")
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "source.json is missing", path)
    doc = ki.read_json(path)
    counts = doc.get("counts") or {}
    checks, triggered = [], []

    total = sum(counts.get(name, 0) for name in DECISIONS)
    ok = total == counts.get("objects")
    checks.append({"id": "D1", "name": "totality",
                   "status": "pass" if ok else "fail",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": [] if ok else ["%d != %d" % (total, counts.get("objects"))]})
    if not ok:
        triggered.append(kc.EXIT_ARTIFACT)

    declared = counts.get("arkham_ids_resolved")
    measured = len({str(e.get("arkham_id")) for e in resolution_of(doc)
                    if e.get("decision") == "reuse" and e.get("arkham_id")})
    ok = declared == measured
    checks.append({"id": "D4", "name": "arkham_ids_resolved",
                   "status": "pass" if ok else "fail",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": [] if ok else
                             ["declared %s, measured %d" % (declared, measured)]})
    if not ok:
        triggered.append(kc.EXIT_ARTIFACT)

    # The pack-drift check, which is what makes §5.2 step 1's binding useful to a
    # human rather than merely present in a file. A moved digest does not mean
    # the resolution is WRONG -- it means it was computed against a langpack that
    # has since changed, so re-running `source` may decide differently. Exit 14
    # (input drift), not 13: the input exists and is readable, it moved.
    recorded = (doc.get("binding") or {}).get("korean-packs@tree")
    measured = pack_tree_digest(workspace)
    moved = bool(recorded) and recorded != measured
    checks.append({"id": "D5", "name": "korean_packs_unchanged",
                   "status": "fail" if moved else "pass",
                   "exit_on_fail": kc.EXIT_DRIFT,
                   "detail": ([] if not moved else
                              ["the Korean packs moved since this resolution was "
                               "computed (%s -> %s); re-run `source`"
                               % (recorded[:12], measured[:12])])})
    if moved:
        triggered.append(kc.EXIT_DRIFT)

    unresolved = [e["object_id"] for e in resolution_of(doc)
                  if e.get("decision") == "unresolved"]
    checks.append({"id": "D2", "name": "donor_ambiguity",
                   "status": "pass" if not unresolved else "fail",
                   "exit_on_fail": kc.EXIT_TOLERANCE,
                   "detail": unresolved[:20]})
    if unresolved:
        triggered.append(kc.EXIT_TOLERANCE)

    report = kc.new_report(STAGE, cfg["slug"], mode="verify-only",
                           counts=dict(counts), checks=checks,
                           binding=kc.build_binding([path]))
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    return report, kc.write_report(report, run_dir, cfg=cfg)


# ---------------------------------------------------------------------------
# 7. --selftest
# ---------------------------------------------------------------------------

FAULTS = ("totality", "same-url", "deck-parent", "no-id", "guide-id",
          "donor-choice")


def _obj(object_id, arkham_id, atlas_id="EN", parent_kind=None, guid="aaa111"):
    return {"object_id": object_id, "arkham_id": arkham_id, "guid": guid,
            "atlas_id": atlas_id, "parent_kind": parent_kind,
            "card_id": 100, "cell": 0, "num_width": 10, "num_height": 7}


def _donor(pack, face_url, num_width=10, num_height=7, card_id=4000):
    return {"pack": pack, "container": "C.1", "file": "C.1/x.json",
            "guid": "bbb222", "card_id": card_id, "deck_key": str(card_id // 100),
            "cell": card_id % 100, "num_width": num_width, "num_height": num_height,
            "face_url": face_url, "back_url": "B", "back_is_hidden": True,
            "type": 0, "nickname": None, "description": None}


def selftest(fault=None, verbose=True):
    """Prove each predicate fires on the fault it targets. No corpus required."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    if "totality" in wanted:
        try:
            assert_totality({"reuse": 1, "manufacture": 0, "defer": 0,
                             "unresolved": 0, "out_of_scope": 0, "objects": 2})
            findings.append("totality: a 1 != 2 identity did not refuse")
        except kc.KzRefusal as exc:
            if exc.code != kc.EXIT_ARTIFACT:
                findings.append("totality: expected exit %d, got %d"
                                % (kc.EXIT_ARTIFACT, exc.code))

    if "same-url" in wanted:
        # The hard filter, and the two counters that are not equal. Two objects,
        # ONE id: the object counter must read 2 and the id counter 1.
        objects = [_obj("A.1", "82022", atlas_id="EN"),
                   _obj("A.2", "82022", atlas_id="EN")]
        index = {"82022": [_donor("Korean - Campaigns", "EN")]}
        res = select(objects, index)
        counts = build_counts(res, objects)
        if counts["donor_rejected_same_url_objects"] != 2:
            findings.append("same-url: object counter read %d, expected 2"
                            % counts["donor_rejected_same_url_objects"])
        if counts["donor_rejected_same_url_ids"] != 1:
            findings.append("same-url: id counter read %d, expected 1"
                            % counts["donor_rejected_same_url_ids"])
        if counts["manufacture"] != 2 or counts["reuse"] != 0:
            findings.append("same-url: a text-only donor resolved to reuse")

    if "deck-parent" in wanted:
        # §5.2 step 5. A Deck-parented object WITH a donor resolves to `reuse`.
        # Re-introducing parent_kind == "Deck" -> defer takes the eight Challenge
        # Scenarios from 222 resolved ids to 61.
        objects = [_obj("Deck.a/Card.1", "82037", atlas_id="EN", parent_kind="Deck")]
        index = {"82037": [_donor("Korean - Campaigns", "KO")]}
        res = select(objects, index)
        if res.entries[0]["decision"] != "reuse":
            findings.append("deck-parent: a Deck-parented object with a donor "
                            "resolved to %r, not reuse"
                            % res.entries[0]["decision"])
        counts = build_counts(res, objects)
        if counts["objects_inside_deck"] != 1:
            findings.append("deck-parent: objects_inside_deck was not reported")

    if "no-id" in wanted:
        objects = [_obj("A.1", None)]
        res = select(objects, {})
        if res.entries[0]["decision"] != "defer":
            findings.append("no-id: expected defer, got %r"
                            % res.entries[0]["decision"])

    if "guide-id" in wanted:
        # Routed BEFORE the donor lookup: CG71 has a Korean - Campaigns override,
        # so a lookup-first implementation would resolve it to `reuse`.
        objects = [_obj("Guide.1", "CG71", atlas_id="EN")]
        index = {"CG71": [_donor("Korean - Campaigns", "KO")]}
        res = select(objects, index)
        if res.entries[0]["decision"] != "out_of_scope":
            findings.append("guide-id: expected out_of_scope, got %r"
                            % res.entries[0]["decision"])

    if "donor-choice" in wanted:
        objects = [_obj("A.1", "01129", atlas_id="EN")]
        index = {"01129": [_donor("Korean - Player Cards", "KO1", 1, 1),
                           _donor("Korean - Campaigns", "KO2", 10, 7)]}
        res = select(objects, index)
        if len(res.ambiguous) != 1:
            findings.append("donor-choice: %d escalations, expected 1"
                            % len(res.ambiguous))
        counts = build_counts(res, objects)
        if counts["escalations_differing_grid"] != 1:
            findings.append("donor-choice: differing_grid was not detected")
        detail = escalation_detail(objects[0], res.ambiguous["A.1"][1])
        if detail["donor_keys"] != ["D0", "D1"]:
            findings.append("donor-choice: donor keys were not labelled")

        # The ruling parser: exactly one token, high confidence, a choice verdict.
        good = {"verdict": "tolerance", "confidence": "high",
                "value": {"owner_stage": "source",
                          "suggested_action": "adopt D1, the 10x7 donor"}}
        pos, why = parse_donor_choice(good, 2)
        if pos != 1:
            findings.append("donor-choice: a single-token ruling parsed as %r (%s)"
                            % (pos, why))
        for label, ruling in (
                ("two tokens", {"verdict": "tolerance", "confidence": "high",
                                "value": {"suggested_action": "D0 or D1"}}),
                ("no token", {"verdict": "tolerance", "confidence": "high",
                              "value": {"suggested_action": "pick the wider one"}}),
                ("abstain", {"verdict": "abstain", "confidence": "high",
                             "value": {"suggested_action": "D1"}}),
                ("medium", {"verdict": "tolerance", "confidence": "medium",
                            "value": {"suggested_action": "D1"}}),
                ("out of range", {"verdict": "tolerance", "confidence": "high",
                                  "value": {"suggested_action": "D7"}})):
            pos, _why = parse_donor_choice(ruling, 2)
            if pos is not None:
                findings.append("donor-choice: %s parsed as a choice" % label)

        # And the fold-back: an unparseable ruling leaves the entry `unresolved`.
        res2 = select(objects, index)
        apply_rulings(res2, {"results": {"rulings": [
            {"unit_id": unit_id_for(objects[0]), "verdict": "abstain",
             "confidence": "high", "value": {"suggested_action": "D0"}}]}})
        if res2.entries[0]["decision"] != "unresolved":
            findings.append("donor-choice: an abstain resolved the object anyway")
        res3 = select(objects, index)
        applied = apply_rulings(res3, {"results": {"rulings": [
            {"unit_id": unit_id_for(objects[0]), "verdict": "tolerance",
             "confidence": "high", "rationale": "the 10x7 sheet renders",
             "value": {"suggested_action": "adopt D1"}}]}})
        if applied != 1 or res3.entries[0]["decision"] != "reuse":
            findings.append("donor-choice: a high-confidence choice was not applied")
        if res3.entries[0].get("donor", {}).get("num_width") != 10:
            findings.append("donor-choice: the chosen donor is not D1")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 8. CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_source.py",
        description="koreanize `source` -- the 3-way art-source selector "
                    "(design §5.2).")
    parser.add_argument("--slug", help="resolve <run_dir> from the default layout")
    parser.add_argument("--run-dir", help="override <run_dir> for this invocation only")
    parser.add_argument("--accept-donor-choice", action="store_true",
                        help="acknowledge a triage(S6) choice between donors at "
                             "differing grids (TOLERANCES; exit 22 without it)")
    parser.add_argument("--no-escalate", action="store_true",
                        help="do not invoke triage(S6); ambiguous objects are "
                             "recorded unresolved and the report is NOT consumable")
    parser.add_argument("--replay", help="re-run decide + apply on a recorded AI "
                                         "run -- no claude, no credential, no cost")
    parser.add_argument("--claude-bin", help="override the claude executable")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_source --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        print("  ok: totality, the same-URL hard filter and its two counters, the "
              "deleted Deck predicate, no-id defer, the CG* out_of_scope branch, "
              "and the S6 donor-choice contract")
        return kc.EXIT_OK

    if not args.run_dir and not args.slug:
        parser.print_help()
        return kc.EXIT_USAGE
    run_dir = args.run_dir or os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize",
                                           args.slug)

    if args.verify_only:
        report, path = verify_only(run_dir)
        if args.json_only:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("koreanize source --verify-only -- %s" % run_dir)
            for check in report["checks"]:
                print("  %-24s %s  %s" % (check["name"], check["status"],
                                          "; ".join(check["detail"])))
            print("  verdict         : %s (exit %d)"
                  % (report["verdict"], report["exit_code"]))
            print("  wrote           : %s" % path)
        return report["exit_code"]

    report, artifacts, _triage = run_source(
        run_dir, mode="dry-run" if args.dry_run else "build",
        escalate=not args.no_escalate,
        accept_donor_choice=args.accept_donor_choice,
        replay=args.replay, claude_bin=args.claude_bin, quiet=args.quiet)

    cfg = kc.bound_scenario()
    out_dir = run_dir
    if args.dry_run:
        out_dir = os.path.join(run_dir, "dry-run", STAGE)
        kz.assert_dry_run_dest(cfg, STAGE, out_dir)
    written = emit(report, artifacts, out_dir, cfg=cfg)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    counts = report["counts"]
    print("koreanize source -- %s" % cfg["scenario_name"])
    print("  objects         : %d" % counts["objects"])
    print("  decisions       : reuse %d / manufacture %d / defer %d / "
          "unresolved %d / out_of_scope %d"
          % tuple(counts[n] for n in DECISIONS))
    print("  ids resolved    : %s of %s" % (counts["arkham_ids_resolved"],
                                            counts.get("arkham_ids")))
    print("  guide ids       : %s" % counts.get("guide_ids"))
    print("  same-url reject : %d object(s) / %d id(s)"
          % (counts["donor_rejected_same_url_objects"],
             counts["donor_rejected_same_url_ids"]))
    print("  escalations     : %d (%d at differing grids)"
          % (counts["escalations"], counts["escalations_differing_grid"]))
    print("  inside a Deck   : %d  (reported, never a decision input)"
          % counts["objects_inside_deck"])
    print("  verdict         : %s (exit %d, consumable %s)"
          % (report["verdict"], report["exit_code"], report["consumable"]))
    for path in written:
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
