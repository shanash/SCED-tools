#!/usr/bin/python3
"""koreanize `verify` -- the C1-C11 cross-pack gate plus the TTSMM build gate.

INTERPRETER TIER: stdlib (`#!/usr/bin/python3`, Apple's 3.9.6 xcode_select shim).
This module is in `kz_common.STDLIB_TIER` and is enforced twice (§5.9). It
therefore contains NO PIXEL CHECK: the artifact half of containment decodes PNGs
with numpy and measures font outlines, which is `kz_checkers.py`'s job on the art
tier (§4.4). It is also NOT the golden fixture -- `golden` has to drive five
art-tier stages and compare the interpreter triple they ran under, which a
stdlib-tier module can neither import nor spawn coherently, so it is a
`koreanize.sh` COMMAND (§5.8).

WHY C1-C11 ARE ENUMERATED IN CODE AND NOT SUMMARISED

"C1-C11 all pass" is a v0 acceptance predicate, and a predicate whose terms are
written down nowhere cannot be implemented, tested or refused. C1-C9 are the
checks `.am/midwinter-gala-korean/verify-both-packs.md:43-51` recorded, restated
over koreanize's own frozen artifacts instead of that task's hardcoded target
set; C10 and C11 are koreanize's own two, and each already existed elsewhere in
the design as a commitment with no verifier named.

WHAT IS THE SUBJECT AND WHAT ARE THE REFERENCES

The SUBJECT is the written langpack -- a pack cannot be verified without reading
it. What §5.8 forbids is deriving the EXPECTATIONS from the pack, which is how
the record's own first-draft C3 came to be structurally incapable of failing: it
selected by URL, so a drifted URL silently left the selected set. So selection
and every reference value come from frozen artifacts instead:

  card-text-en.json   identity -- the id and English nickname of every object
  source.json         the donor geometry and urls `reuse` was supposed to adopt
  scenario.json       shared_backs[].policy, and the measured atlas grids
  <stage>.json        write_set[].pre_sha256, which is C8's before-state
  atlas-urls.json     C3's reference map WHEN IT EXISTS -- it is `upload`'s
                      output and therefore v1; in v0 the reference is the donor
                      face url `source` recorded, which is the same fact one
                      producer earlier

IN v0 ALL ELEVEN ARE EVALUATED OVER THE REUSE SUBSET, with
`counts.objects_without_korean_text` as the declared remainder (§1.2). That is a
narrower claim than v1's and it is the one v0 can discharge.
"""

import argparse
import collections
import json
import os
import signal
import subprocess
import sys

import kz_common as kc
import kz_config as kz

STAGE = "verify"
kc.declare_ai(STAGE, required=False)

SCHEMA_VERSION = "1.1.0"

LANGPACK_ROOT = "SCED-downloads/decomposed/language-pack"
DOWNLOADS_ROOT = "SCED-downloads"

#: The four properties the nightly's copy of this same binary already has, and
#: which "the TTSMM build gate rc 0" names none of on its own (§5.8).
TTSMM_ENV = "KOREANIZE_TTSMM"
TTSMM_SHA_ENV = "KOREANIZE_TTSMM_SHA256"
TTSMM_TIMEOUT_ENV = "KOREANIZE_TTSMM_TIMEOUT_S"
TTSMM_TIMEOUT_DEFAULT = 1800

#: The load-bearing Lua pin of §7. `MythosArea.ttslua:209` selects the scenario
#: card by its literal English Nickname, so translating it breaks the mod.
SCENARIO_NICKNAME = "Scenario"

CHECK_IDS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11")


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# 1. The frozen artifacts
# ---------------------------------------------------------------------------


class Subject(object):
    """Everything the eleven checks are evaluated over, gathered once."""

    def __init__(self, run_dir, cfg, workspace):
        self.run_dir = run_dir
        self.cfg = cfg
        self.workspace = workspace

        self.card_text = read_json(os.path.join(run_dir, "card-text-en.json"))
        self.objects = dict((o["object_id"], o)
                            for o in self.card_text.get("objects") or [])

        source_path = kc.report_path(run_dir, "source", "build")
        if not os.path.exists(source_path):
            kc.refuse(kc.EXIT_PRECONDITION, "source.json is missing", source_path)
        self.source = read_json(source_path)
        self.resolution = ((self.source.get("results") or {}).get("resolution")
                           or [])
        self.subset = [e for e in self.resolution if e.get("decision") == "reuse"]

        self.container_dir = os.path.join(
            workspace, LANGPACK_ROOT, cfg["pack"], cfg["pack_container"],
            cfg["container_stem"])
        self.container_json = self.container_dir + ".json"
        self.pack_json = os.path.join(workspace, LANGPACK_ROOT, cfg["pack"],
                                      "%s.json" % cfg["pack_container"])

        self.written = {}
        if os.path.isdir(self.container_dir):
            for name in sorted(os.listdir(self.container_dir)):
                if name.endswith(".json"):
                    self.written[name[:-5]] = read_json(
                        os.path.join(self.container_dir, name))

        atlas_urls = os.path.join(run_dir, "atlas-urls.json")
        self.atlas_urls = read_json(atlas_urls) if os.path.exists(atlas_urls) \
            else None

    def stem(self, object_id):
        return object_id.replace("\\", "/").rsplit("/", 1)[-1]

    def owned_urls(self):
        """The atlas urls this scenario OWNS -- `upload`'s output, v1.

        Empty in v0 by construction: every atlas a v0 run installs belongs to the
        donor it was adopted from, so no url is uniquely this scenario's and the
        cross-scope clause it feeds has nothing to range over.
        """
        if not self.atlas_urls:
            return set()
        urls = self.atlas_urls.get("urls")
        values = urls.values() if isinstance(urls, dict) else (urls or [])
        return {u for u in values if isinstance(u, str)}

    def foreign_overrides(self):
        """(stem, override) for every override in this PACK that is not this
        scenario's -- C1's cross-scope subject.

        Scoped to the target pack rather than to all three: the check is "no
        object outside scope carries one of THIS scenario's urls", and an atlas
        this scenario owns can only have been installed by this tool, which
        writes into one pack. Walked lazily and only by C1, because it is the one
        check whose subject is not the reuse subset.
        """
        pack_dir = os.path.dirname(self.container_dir)
        mine = os.path.realpath(self.container_dir)
        out = []
        if not os.path.isdir(pack_dir):
            return out
        for dirpath, dirnames, filenames in os.walk(pack_dir):
            dirnames.sort()
            if os.path.realpath(dirpath) == mine:
                dirnames[:] = []
                continue
            for name in sorted(filenames):
                if not name.endswith(".json"):
                    continue
                try:
                    obj = read_json(os.path.join(dirpath, name))
                except (ValueError, OSError):
                    continue
                if isinstance(obj, dict) and obj.get("CustomDeck"):
                    out.append((name[:-5], obj))
        return out

    def expected_stems(self):
        return [self.stem(e["object_id"]) for e in self.subset]

    def face_of(self, override):
        decks = override.get("CustomDeck") or {}
        if len(decks) != 1:
            return None
        return next(iter(decks.values())).get("FaceURL")

    def reference_face(self, entry):
        """C3's reference: `atlas-urls.json` when it exists (v1), else the donor
        face url `source` recorded (v0). The same fact, one producer earlier."""
        donor_face = (entry.get("donor") or {}).get("face_url")
        if not self.atlas_urls:
            return donor_face
        for url in (self.atlas_urls.get("urls") or {}).values():
            if url == donor_face:
                return url
        return donor_face


# ---------------------------------------------------------------------------
# 2. C1-C11
# ---------------------------------------------------------------------------


def _check(cid, name, detail, exit_on_fail=kc.EXIT_ARTIFACT, note=None,
           subject_size=None):
    """One check result.

    `subject_size` is not decoration. Nine of these eleven iterate the reuse
    subset, so an EMPTY subset makes every one of them report `pass` with an
    empty `detail[]` -- and "C1-C11 all pass" is the v0 acceptance predicate
    §1.3 gates `register` and `verify` on. Nothing in the result distinguished
    "verified 226 objects" from "verified nothing", which is the same class of
    defect as N-9's "11 unregistered files were dropped silently with rc 0": a
    green result whose subject was empty. C8 and C9 already said so in prose;
    the other nine now do too, mechanically, from one place.
    """
    if note is None and subject_size is not None:
        note = "%d object(s) in scope" % subject_size
    return {"id": cid, "name": name,
            "status": "pass" if not detail else "fail",
            "exit_on_fail": exit_on_fail,
            "detail": detail[:40], "note": note}


def c1_inventory(subject):
    """Present, on a Korean atlas, and no object OUTSIDE scope carries one of
    this scenario's urls.

    SELECTION IS BY IDENTITY, never by url: the record's own first draft of this
    check selected by url and was therefore structurally incapable of failing --
    a drifted url silently left the selected set, so the check passed by looking
    at fewer things.
    """
    detail = []
    for entry in subject.subset:
        stem = subject.stem(entry["object_id"])
        override = subject.written.get(stem)
        if override is None:
            detail.append("%s: no override was written" % stem)
            continue
        face = subject.face_of(override)
        english = subject.objects.get(entry["object_id"], {}).get("atlas_id")
        if not face:
            detail.append("%s: the override carries no single CustomDeck" % stem)
            continue
        if face == english:
            detail.append("%s: still on the ENGLISH atlas %s" % (stem, face[-24:]))
            continue

    # Strays, sense 1 -- an override in THIS container that is not in the reuse
    # subset. The objects with no donor must have NO override file at all: an
    # English FaceURL inside a container the mod advertises as Korean is the
    # silent defect the v0 scoping exists to prevent (§1.2).
    expected = set(subject.expected_stems())
    for stem in sorted(set(subject.written) - expected):
        detail.append("%s: a stray override outside the reuse subset" % stem)

    # Strays, sense 2 -- "no object OUTSIDE scope carries one of this scenario's
    # urls". Two senses catching different faults: the set difference above finds
    # a file in the wrong PLACE, this finds this scenario's ART on an object that
    # is not ours -- the cross-pack duplication N-7 records, which leaves no
    # trace in the container listing at all.
    #
    # ITS SUBJECT IS THE ATLASES THIS SCENARIO *OWNS*, WHICH IN v0 IS NONE, and
    # getting that wrong is instructive rather than incidental. Evaluated over
    # every url the overrides carry, the check fires on the DONORS -- correctly
    # by its own logic and wrongly by intent, because `reuse` adopts a donor's
    # atlas and sharing it is the entire mechanism (§3.5). The clause is
    # meaningful only for an atlas nothing else can legitimately be on, i.e. one
    # this scenario uploaded: `atlas-urls.json`, which §3.1 makes "the sole
    # authority for the N-7 orphan set" and which `upload` produces in v1. Absent
    # it there is no subject, and the note says so rather than reporting a pass.
    owned = subject.owned_urls()
    if owned:
        for stem, override in sorted(subject.foreign_overrides()):
            if subject.face_of(override) in owned:
                detail.append("%s: an object outside this scenario carries one "
                              "of its OWN atlas urls" % stem)
    note = ("%d object(s) in scope; %d owned atlas url(s) for the cross-scope "
            "clause" % (len(subject.subset), len(owned)))
    if not owned:
        note += " -- none, so that clause has no subject until `upload` (v1)"
    return _check("C1", "inventory_residue_strays", detail, note=note)


def c2_id_set(subject):
    """The id set equals card-text-en.json's IN BOTH DIRECTIONS, and an id
    carried by two objects expands to BOTH (71033 -> 4a2568, ccce29).

    Object counts, never id counts -- that expansion is exactly what an id-keyed
    comparison silently collapses.
    """
    detail = []
    for entry in subject.subset:
        stem = subject.stem(entry["object_id"])
        override = subject.written.get(stem)
        if override is None:
            continue    # C1 owns absence
        try:
            written_id = json.loads(override.get("GMNotes") or "{}").get("id")
        except ValueError:
            detail.append("%s: GMNotes is unparseable" % stem)
            continue
        if written_id != entry.get("arkham_id"):
            detail.append("%s: carries id %r, card-text-en.json says %r"
                          % (stem, written_id, entry.get("arkham_id")))
    # The other direction, over OBJECTS: every written override must be one of
    # the subset's objects.
    expected = set(subject.expected_stems())
    for stem in sorted(set(subject.written) - expected):
        detail.append("%s: written but not in card-text-en.json's subset" % stem)
    return _check("C2", "id_set_both_directions", detail, subject_size=len(subject.subset))


def c3_atlas_bytes(subject):
    """Each atlas FaceURL is BYTE-identical everywhere it appears, across packs,
    and equal to the reference. Bytes, not normalised strings."""
    detail = []
    by_id = collections.defaultdict(set)
    for entry in subject.subset:
        stem = subject.stem(entry["object_id"])
        override = subject.written.get(stem)
        if override is None:
            continue
        face = subject.face_of(override)
        reference = subject.reference_face(entry)
        if reference and face != reference:
            detail.append("%s: FaceURL %r != the recorded %r"
                          % (stem, face, reference))
        by_id[entry.get("arkham_id")].add(face)
    for arkham_id, faces in sorted(by_id.items()):
        if len(faces) > 1:
            detail.append("%s: carried on %d different atlas urls"
                          % (arkham_id, len(faces)))
    return _check("C3", "atlas_url_byte_identical", detail, subject_size=len(subject.subset))


def c4_card_arithmetic(subject):
    """card_id == deck_key * 100 + cell, with cell INSIDE the sheet's capacity."""
    detail = []
    for stem, override in sorted(subject.written.items()):
        card_id = override.get("CardID")
        decks = override.get("CustomDeck") or {}
        if not isinstance(card_id, int) or len(decks) != 1:
            detail.append("%s: CardID %r against %d CustomDeck key(s)"
                          % (stem, card_id, len(decks)))
            continue
        deck_key, deck = next(iter(decks.items()))
        cell = card_id % 100
        if card_id != int(deck_key) * 100 + cell:
            detail.append("%s: CardID %d != deck_key %s * 100 + cell %d"
                          % (stem, card_id, deck_key, cell))
        capacity = (deck.get("NumWidth") or 0) * (deck.get("NumHeight") or 0)
        if capacity and cell >= capacity:
            detail.append("%s: cell %d is outside the sheet's capacity %d"
                          % (stem, cell, capacity))
    return _check("C4", "card_id_arithmetic", detail, subject_size=len(subject.subset))


def c5_grid(subject):
    """NumWidth / NumHeight integral, in 1..12, and equal to the sheet's
    MEASURED grid -- `init`'s, never a constant table."""
    detail = []
    for entry in subject.subset:
        stem = subject.stem(entry["object_id"])
        override = subject.written.get(stem)
        if override is None:
            continue
        decks = override.get("CustomDeck") or {}
        if len(decks) != 1:
            continue    # C4 owns that
        deck = next(iter(decks.values()))
        for field in ("NumWidth", "NumHeight"):
            value = deck.get(field)
            if not isinstance(value, int) or not 1 <= value <= 12:
                detail.append("%s: %s is %r, expected an integer in 1..12"
                              % (stem, field, value))
        donor = entry.get("donor") or {}
        if (deck.get("NumWidth"), deck.get("NumHeight")) != \
                (donor.get("num_width"), donor.get("num_height")):
            detail.append("%s: grid %sx%s != the donor's measured %sx%s"
                          % (stem, deck.get("NumWidth"), deck.get("NumHeight"),
                             donor.get("num_width"), donor.get("num_height")))
    return _check("C5", "grid_matches_measured", detail, subject_size=len(subject.subset))


def c6_guids(subject):
    """Target GUIDs distinct, none colliding with another object IN ITS OWN PACK,
    and the filename stem agrees with the GUID.

    SCOPED TO THE TARGET SET, never pack-wide: both packs carry INHERITED
    duplicate-GUID groups (47 of 167 in Campaigns, 4 of 12 in Player Cards) that
    koreanize did not write and cannot fix, so a pack-wide assertion fails on
    somebody else's data forever.
    """
    detail = []
    seen = {}
    for stem, override in sorted(subject.written.items()):
        guid = override.get("GUID")
        if not guid:
            detail.append("%s: no GUID" % stem)
            continue
        if not stem.endswith("." + guid):
            detail.append("%s: the filename stem does not end in its GUID %s"
                          % (stem, guid))
        if guid in seen:
            detail.append("%s: GUID %s collides with %s" % (stem, guid, seen[guid]))
        seen[guid] = stem
    return _check("C6", "guids_distinct_and_named", detail, subject_size=len(subject.subset))


def case_fold_collisions(paths):
    """A PURE FUNCTION OVER PATHS, which is what makes it exercisable on a
    case-insensitive volume -- the one class `git status` there cannot show."""
    groups = collections.defaultdict(list)
    for path in paths:
        groups[path.lower()].append(path)
    return [sorted(group) for group in groups.values() if len(group) > 1]


def c7_case_collisions(subject):
    paths = [os.path.join(subject.cfg["container_stem"], "%s.json" % stem)
             for stem in sorted(subject.written)]
    paths.append(os.path.basename(subject.container_json))
    detail = ["case-only collision: %s" % ", ".join(group)
              for group in case_fold_collisions(paths)]
    return _check("C7", "no_case_only_collisions", detail, kc.EXIT_COLLISION, subject_size=len(subject.subset))


def c8_unchanged_identity(subject):
    """GUID / CardID / deck_key / grid unchanged against the PRE-WRITE state.

    `write_set[].pre_sha256` and the §5.3 snapshots are the reference, so this
    check has a subject only where something was modified rather than created --
    which on a first v0 run is nothing, and is reported as such rather than
    silently passing over an empty set.
    """
    detail = []
    compared = 0
    for stage in ("reuse", "objtext", "register"):
        path = kc.report_path(subject.run_dir, stage, "build")
        if not os.path.exists(path):
            continue
        for record in read_json(path).get("write_set") or []:
            if record.get("action") != "modify" or not record.get("snapshot"):
                continue
            snapshot = os.path.join(subject.workspace, record["snapshot"])
            target = os.path.join(subject.workspace, record["path"])
            if not (os.path.exists(snapshot) and os.path.exists(target)):
                continue
            before, after = read_json(snapshot), read_json(target)
            compared += 1
            if before.get("GUID") != after.get("GUID"):
                detail.append("%s: GUID moved %s -> %s"
                              % (record["path"], before.get("GUID"),
                                 after.get("GUID")))
    return _check("C8", "identity_unchanged_vs_pre_write", detail,
                  note="%d modified file(s) had a pre-write snapshot to compare"
                       % compared)


def c9_shared_backs(subject):
    """Shared backs keep their ENGLISH generics; only the backs this scenario
    OWNS become Korean."""
    detail = []
    never = {b["url"] for b in (subject.cfg.get("shared_backs") or [])
             if b.get("policy") == "never_rewrite" and b.get("url")}
    if not never:
        return _check("C9", "shared_backs_never_rewritten", [],
                      note="this scenario declares no never_rewrite back")
    for entry in subject.subset:
        stem = subject.stem(entry["object_id"])
        override = subject.written.get(stem)
        english = subject.objects.get(entry["object_id"], {})
        if override is None:
            continue
        decks = override.get("CustomDeck") or {}
        if len(decks) != 1:
            continue
        back = next(iter(decks.values())).get("BackURL")
        if english.get("back_atlas_id") in never and back != english["back_atlas_id"]:
            detail.append("%s: a shared English back was rewritten to %r"
                          % (stem, back))
    return _check("C9", "shared_backs_never_rewritten", detail,
                  subject_size=len(subject.subset))


def c10_container_children(subject):
    """The container-children count derived from objects[] equals
    `ContainedObjects_order`'s length AND the built spawn count.

    N-10's spawn-path asymmetry, which had an assertion promised in prose and no
    check named. The three counts are deliberately compared as three and not two:
    the manifest and the directory can agree while both disagree with what the
    resolution says should be there.
    """
    detail = []
    expected = subject.expected_stems()
    if not os.path.exists(subject.container_json):
        return _check("C10", "container_children_reconcile",
                      ["the scenario container %s does not exist"
                       % os.path.basename(subject.container_json)])
    order = read_json(subject.container_json).get("ContainedObjects_order") or []
    spawned = sorted(subject.written)
    if len(order) != len(expected):
        detail.append("ContainedObjects_order has %d entries, the resolution "
                      "gives %d" % (len(order), len(expected)))
    if len(spawned) != len(expected):
        detail.append("%d override file(s) on disk, the resolution gives %d"
                      % (len(spawned), len(expected)))
    if sorted(order) != spawned:
        detail.append("the manifest and the directory disagree: %d only in the "
                      "manifest, %d only on disk"
                      % (len(set(order) - set(spawned)),
                         len(set(spawned) - set(order))))
    return _check("C10", "container_children_reconcile", detail, subject_size=len(subject.subset))


def c11_scenario_nickname(subject):
    """Every object whose English Nickname is the literal "Scenario" still
    carries it after `objtext`, with the Korean name in Description.

    `SCED/src/mythos/MythosArea.ttslua:209` selects the scenario card by that
    exact string, so translating it does not mistranslate a card -- it breaks the
    mod. The rule existed in the design with no verifier named; this is it.
    """
    detail = []
    for entry in subject.subset:
        english = subject.objects.get(entry["object_id"], {})
        if english.get("nickname") != SCENARIO_NICKNAME:
            continue
        stem = subject.stem(entry["object_id"])
        override = subject.written.get(stem)
        if override is None:
            continue
        if override.get("Nickname") != SCENARIO_NICKNAME:
            detail.append("%s: Nickname is %r, MythosArea.ttslua:209 pins %r"
                          % (stem, override.get("Nickname"), SCENARIO_NICKNAME))
        elif not override.get("Description"):
            detail.append("%s: the pin is intact but the Korean name is not in "
                          "Description" % stem)
    return _check("C11", "scenario_nickname_pin", detail, subject_size=len(subject.subset))


CHECKS = (c1_inventory, c2_id_set, c3_atlas_bytes, c4_card_arithmetic, c5_grid,
          c6_guids, c7_case_collisions, c8_unchanged_identity, c9_shared_backs,
          c10_container_children, c11_scenario_nickname)


def run_checks(subject):
    results = [fn(subject) for fn in CHECKS]
    ids = [r["id"] for r in results]
    if ids != list(CHECK_IDS):
        kc.refuse(kc.EXIT_ARTIFACT,
                  "the check table is not C1-C11", "produced %s" % ids)
    return results


# ---------------------------------------------------------------------------
# 3. The TTSMM build gate -- a GATE, with all four properties
# ---------------------------------------------------------------------------
#
# "the TTSMM build gate rc 0" as a phrase names no binary, asserts no checksum,
# sets no bound and defines no failure code -- against a driver that does all
# four for that same executable (daily-sync-local.sh:191-192, :1688-1695, :1758).
# koreanize's gate therefore declares all four, and it may NOT borrow the
# driver's copy: :1749 copies the binary into the nightly's disposable worktree
# under .local-sync/scratch/, P0b refuses any koreanize run that resolves a path
# there, and the tree is deleted when the driver finishes -- a gate pointed at it
# is a gate that vanishes.
#
# Nor may the path be moved casually once chosen. TTSModManager-Darwin is
# adhoc/linker-signed with Identifier=a.out and no TeamIdentifier, so macOS makes
# it its OWN TCC responsible_path and any Full Disk Access grant is keyed to
# path + cdhash. The asserted sha256 is what turns a grant-invalidating
# replacement into a NAMED REFUSAL instead of a wedge -- which is the 2026-08-19
# failure, six hours of one.


def resolve_ttsmm(env=None, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    env = env if env is not None else kz.read_env_file()
    path = env.get(TTSMM_ENV) or os.environ.get(TTSMM_ENV) \
        or os.path.join(workspace, "TTSModManager-Darwin")
    return os.path.expanduser(path)


def ttsmm_timeout(env=None):
    env = env if env is not None else kz.read_env_file()
    raw = env.get(TTSMM_TIMEOUT_ENV) or os.environ.get(TTSMM_TIMEOUT_ENV)
    return kz._positive_int(raw, TTSMM_TIMEOUT_DEFAULT, TTSMM_TIMEOUT_ENV)


def _kill_group(proc):
    """SIGKILL the whole process group, then reap. Best effort by construction:
    the group may already be gone, and a failure to kill must not mask the
    timeout that is the actual finding."""
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def build_gate(run_dir, env=None, workspace=None, run=True):
    """Returns a check dict. Four declared properties, four distinct failures."""
    workspace = workspace or kc.WORKSPACE_ROOT
    env = env if env is not None else kz.read_env_file()
    path = resolve_ttsmm(env, workspace)
    log = os.path.join(run_dir, "verify-stdout.log")

    if kc.SCRATCH_MARKER.replace(os.sep, "/") in path.replace(os.sep, "/"):
        kc.refuse(kc.EXIT_USAGE,
                  "the TTSMM gate is pointed at the nightly's scratch copy",
                  "%s is deleted when the driver finishes; koreanize needs a "
                  "stable client path of its own (§5.8)" % path)
    if not os.path.exists(path) or not os.access(path, os.X_OK):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the TTSMM binary is missing or not executable",
                  "%s -- set %s in ~/.config/koreanize/env" % (path, TTSMM_ENV))

    expected = env.get(TTSMM_SHA_ENV) or os.environ.get(TTSMM_SHA_ENV)
    found = kc.sha256_file(path)
    if not expected:
        # MANDATORY, not "checked when someone happened to record one". §5.8
        # declares the digest "asserted before EVERY invocation", and the
        # nightly's own copy of this same binary is unconditional
        # (daily-sync-local.sh:1688-1695). An optional integrity check on a
        # binary this function then EXECUTES is not a weaker guarantee, it is
        # none: the operator who most needs it is exactly the one who never set
        # the variable. The refusal names the digest to record, so it is one
        # copy-paste to satisfy rather than an obstacle.
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s is not set and the TTSMM gate will not run an unpinned "
                  "binary" % TTSMM_SHA_ENV,
                  "record it in ~/.config/koreanize/env:\n"
                  "  %s=%s\n"
                  "(%s)" % (TTSMM_SHA_ENV, found, path))
    if expected != found:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the TTSMM binary's sha256 does not match %s" % TTSMM_SHA_ENV,
                  "on disk %s, recorded %s -- a replaced binary also invalidates "
                  "its Full Disk Access grant, so this refusal is what stops a "
                  "wedge" % (found[:16], expected[:16]))

    if not run:
        return {"id": "B1", "name": "ttsmm_build", "status": "pass",
                "exit_on_fail": kc.EXIT_ARTIFACT,
                "detail": [], "note": "--no-build: path and sha256 asserted, "
                                      "the build itself was not run",
                "binary": path, "sha256": found, "rc": None}

    bound = ttsmm_timeout(env)
    downloads = os.path.join(workspace, DOWNLOADS_ROOT)
    detail, rc = [], None
    # start_new_session puts the build in its own PROCESS GROUP so the timeout
    # can reach a GRANDCHILD. This is not defensive programming, it is the exact
    # defect CLAUDE.md records for this binary: on 2026-08-18 `with_timeout`
    # SIGTERMed its direct child and returned, but TTSMM was a grandchild -- it
    # survived, held an unanswered TCC prompt for another 2h16m, blocked the
    # other repo's agent and kept the scratch worktree unremovable. Python's
    # default `subprocess.run(timeout=...)` kills only the direct child and would
    # reproduce it exactly.
    proc = None
    try:
        proc = subprocess.Popen([path, "-mode", "build", "-moddir", downloads],
                                cwd=downloads, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        stdout, _ = proc.communicate(timeout=bound)
        rc = proc.returncode
        with open(log, "wb") as handle:
            handle.write(stdout or b"")
        if rc != 0:
            detail.append("rc %d; the build log is %s" % (rc, log))
    except subprocess.TimeoutExpired:
        rc = 143
        _kill_group(proc)
        detail.append(
            "timed out after %ds. A wedge and a broken build look nothing alike: "
            "a real failure takes seconds and leaves a traceback, a wedge leaves "
            "the log ending at the last thing that worked. Check for an "
            "unanswered TCC prompt naming %s." % (bound, os.path.basename(path)))
    return {"id": "B1", "name": "ttsmm_build",
            "status": "pass" if rc == 0 else "fail",
            "exit_on_fail": kc.EXIT_ARTIFACT,
            "detail": detail, "note": "bound %ds" % bound,
            "binary": path, "sha256": found, "rc": rc}


# ---------------------------------------------------------------------------
# 4. The stage
# ---------------------------------------------------------------------------


def run_verify(run_dir, mode="build", build=True, workspace=None, env=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])
    cfg = kz.load_scenario(os.path.join(run_dir, "scenario.json"))

    register = kc.report_path(run_dir, "register", "build")
    if not os.path.exists(register):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the `register` report is missing",
                  "verify's predecessor is register (§1.2); %s" % register)
    if not read_json(register).get("consumable"):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the `register` report is not consumable",
                  read_json(register).get("consumable_blocked_by") or register)

    subject = Subject(run_dir, cfg, workspace)

    # THE SCOPE GUARD, and it runs before the eleven rather than beside them.
    # Nine of C1-C11 iterate the reuse subset, so an EMPTY subset makes all nine
    # report `pass` over nothing -- and "C1-C11 all pass" is the acceptance
    # predicate §1.3 gates `register` and `verify` on. `source.json` already
    # states how many objects it resolved to `reuse`; if the resolution[] this
    # stage actually loaded does not carry that many, the two disagree about what
    # is being verified and no result computed from the smaller one means
    # anything. Exit 14 (input drift): both inputs exist and are readable, they
    # disagree.
    scounts = subject.source.get("counts") or {}
    declared = scounts.get("reuse")
    if declared is not None and len(subject.subset) != declared:
        kc.refuse(kc.EXIT_DRIFT,
                  "source.json declares counts.reuse %d but its resolution[] "
                  "carries %d `reuse` entries" % (declared, len(subject.subset)),
                  "verify would otherwise report C1-C11 all pass over %d "
                  "object(s), which is the acceptance predicate passing on a "
                  "subject that is not the one `source` resolved"
                  % len(subject.subset))

    checks = run_checks(subject)
    checks.append(build_gate(run_dir, env, workspace, run=build))

    triggered = [c["exit_on_fail"] for c in checks if c["status"] == "fail"]

    counts = collections.OrderedDict([
        ("objects", scounts.get("objects")),
        ("reuse", scounts.get("reuse")),
        ("verified", len(subject.subset)),
        ("overrides_on_disk", len(subject.written)),
        # The DECLARED remainder -- v0's coverage is partial and says so (§1.2).
        ("objects_without_korean_text",
         (scounts.get("manufacture") or 0) + (scounts.get("defer") or 0)
         + (scounts.get("unresolved") or 0)),
        ("checks_passed", sum(1 for c in checks if c["status"] == "pass")),
        ("checks_failed", sum(1 for c in checks if c["status"] == "fail")),
    ])

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=dict(counts), checks=checks,
        binding=kc.build_binding([os.path.join(run_dir, "scenario.json"),
                                  os.path.join(run_dir, "card-text-en.json"),
                                  kc.report_path(run_dir, "source", "build"),
                                  register]),
        freshness=kc.build_freshness([register], upstream_report_path=register),
        results={"container": os.path.relpath(subject.container_dir, workspace),
                 "scope": "reuse subset (%d of %d objects)"
                          % (len(subject.subset), scounts.get("objects") or 0)})
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    return report


# ---------------------------------------------------------------------------
# 5. --selftest
# ---------------------------------------------------------------------------

FAULTS = ("check-table", "case-fold", "ttsmm-path", "ttsmm-sha", "scenario-pin")


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

    if "check-table" in wanted:
        # "C1-C11 all pass" is an acceptance predicate, so the TABLE being total
        # is itself checked: a check quietly dropped would make the predicate
        # easier to satisfy every time it was evaluated.
        if len(CHECKS) != 11 or tuple(CHECK_IDS) != tuple(
                "C%d" % n for n in range(1, 12)):
            findings.append("check-table: C1-C11 is not eleven checks")

    if "case-fold" in wanted:
        groups = case_fold_collisions(["Dir/Card.aa11.json", "Dir/card.aa11.json",
                                       "Dir/Other.bb22.json"])
        if len(groups) != 1 or len(groups[0]) != 2:
            findings.append("case-fold: the collision was not detected (%r)"
                            % (groups,))
        if case_fold_collisions(["a.json", "b.json"]):
            findings.append("case-fold: a false positive on distinct names")

    if "ttsmm-path" in wanted:
        fires("ttsmm (nightly scratch copy)", kc.EXIT_USAGE,
              lambda: build_gate("/nonexistent", env={
                  TTSMM_ENV: os.path.join(kc.WORKSPACE_ROOT, ".local-sync",
                                          "scratch", "SCED", "TTSModManager-Darwin")},
                  run=False))
        fires("ttsmm (absent binary)", kc.EXIT_PRECONDITION,
              lambda: build_gate("/nonexistent",
                                 env={TTSMM_ENV: "/nonexistent/TTSMM"}, run=False))

    if "ttsmm-sha" in wanted:
        path = resolve_ttsmm(env={})
        if os.path.exists(path):
            fires("ttsmm (sha256 mismatch)", kc.EXIT_PRECONDITION,
                  lambda: build_gate("/nonexistent",
                                     env={TTSMM_ENV: path,
                                          TTSMM_SHA_ENV: "0" * 64}, run=False))
            gate = build_gate("/nonexistent", env={TTSMM_ENV: path,
                                                   TTSMM_SHA_ENV: kc.sha256_file(path)},
                              run=False)
            if gate["status"] != "pass" or gate["rc"] is not None:
                findings.append("ttsmm: --no-build did not assert-and-stop")
        else:
            findings.append("ttsmm: %s is absent, so the sha256 assertion could "
                            "not be exercised" % path)

    if "scenario-pin" in wanted:
        # The load-bearing Lua pin. A translated "Scenario" does not mistranslate
        # a card, it breaks MythosArea.ttslua:209.
        if SCENARIO_NICKNAME != "Scenario":
            findings.append("scenario-pin: the pinned literal moved")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_verify.py",
        description="koreanize `verify` -- C1-C11 plus the TTSMM build gate "
                    "(design §5.8).")
    parser.add_argument("--slug")
    parser.add_argument("--run-dir")
    parser.add_argument("--no-build", action="store_true",
                        help="assert the TTSMM binary's path and sha256 but do "
                             "not run the build")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_verify --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_ARTIFACT
        print("  ok: C1-C11 is total, the case-fold predicate, the TTSMM gate's "
              "path and sha256 refusals, and the MythosArea Nickname pin")
        return kc.EXIT_OK

    if not args.run_dir and not args.slug:
        parser.print_help()
        return kc.EXIT_USAGE
    run_dir = args.run_dir or os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize",
                                           args.slug)

    report = run_verify(run_dir, mode="verify-only" if args.verify_only else "build",
                        build=not args.no_build)
    kc.write_report(report, run_dir, cfg=kc.bound_scenario())

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    print("koreanize verify -- %s" % report["slug"])
    print("  scope           : %s" % (report["results"] or {}).get("scope"))
    for check in report["checks"]:
        print("  %-4s %-32s %s" % (check["id"], check["name"], check["status"]))
        for line in check["detail"][:5]:
            print("       - %s" % line)
        if check.get("note"):
            print("       (%s)" % check["note"])
    print("  remainder       : %d object(s) with no Korean text (declared)"
          % report["counts"]["objects_without_korean_text"])
    print("  verdict         : %s (exit %d, consumable %s)"
          % (report["verdict"], report["exit_code"], report["consumable"]))
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
