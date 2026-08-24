#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `typeset` (S4) -- Korean type onto the cleared faces (design §6 step 14).

ART TIER (design §5.9): `#!/usr/bin/env python3`, i.e. Homebrew 3.14 with PIL and
numpy. Not a member of `kz_common.STDLIB_TIER`, and it may not become one: the
containment proof is a numpy comparison over a decoded PNG and the anti-swap
invariant re-measures font outlines, neither of which `/usr/bin/python3` can do.

WHAT THIS STAGE OWNS, AND WHY EACH HALF EXISTS
----------------------------------------------
Seven things, and they are one list because §6 step 14 states them as one:

  1. THE SHRINK LADDER, with the `deep-shrink` tolerance. The ladder steps DOWN
     one pixel at a time to `MIN_SIZE_ABS` and never steps up. The floor is a
     LEGIBILITY floor and not a percentage: the record's spec says "reduce size
     for that card only, and log it. Never truncate", so no number of shrunk
     faces is a failure. Only "not even MIN_SIZE_ABS fits" is, and its answer is
     to widen that mask window -- not to shrink further and not to draw outside.
  2. BAND ALLOCATION WITH AN EXPLICIT `allocation_mode` (§5.6 item 4). See the
     next section; this is the structural half of the greedy-flow fix.
  3. PARAGRAPH PINNING, with `para_gaps: 0` recorded as BY CONSTRUCTION.
  4. THE PUA MAP WITH THE DECLARED MEASURED PAIR, and the anti-swap invariant
     evaluated by `kz_checkers` (§5.6 items 1-3).
  5. T1-T5, the typeset checks, each with a negative in `--selftest`.
  6. THE REVIEW GALLERY.
  7. THE GATE STUB, carrying PER-FACE PROSE ITEMS beside the visual ones (§5.7).

THE GREEDY-FLOW DEFECT IS FIXED STRUCTURALLY, NOT BY A TOLERANCE
-----------------------------------------------------------------
`typeset-cards.py:3483-3489` records the failure in its own comment:

    "A SEPARATE list, and it has to be: a pinned face keeps allocation_mode
     "single-band" with ONE declared group, so the filter just above structurally
     cannot contain it. That is precisely how the 71001 mis-attribution passed a
     human gate on 2026-08-18 -- the gate's section 3 never listed the face."

A pinned face and an unpinned single-block face reported the SAME
`allocation_mode`, so the review gate's own filter could not tell them apart and
the face a human most needed to look at was the one face the checklist omitted.
The fix is not a wider filter -- a filter is only ever as good as the field it
reads. `allocation_mode` becomes an EXPLICIT per-face declaration with three
values, `paragraph-pinned` among them, and `check_allocation_declared()` refuses
a face that carries `paragraph_bands` under any other mode. The gate's section
then partitions the faces by a field that cannot lie, and `para_gaps: 0` on a
pinned face is recorded as by-construction rather than surfacing as a defect: a
paragraph pinned to its own band pays no paragraph gap, because a fresh band
already separates it.

THE T-CHECKS, AND THE ONE EXIT CODE THAT COULD NOT BE CARRIED OVER
--------------------------------------------------------------------
`typeset-cards.py:44-64` numbers them; koreanize's exit table (§4.2) is not that
script's, so three of the five are re-homed and the re-homing is stated here
rather than left to be rediscovered:

    | check | asserts                                            | legacy | here |
    |-------|----------------------------------------------------|--------|------|
    | T1    | no drawn pixel landed where the mask is not clear  | 20     | 20   |
    | T2    | a field's ink stayed inside its own region union   | 21     | 21   |
    | T3    | typeset/ is exactly the expected face set          | 22     | 67   |
    | T4    | a body fitted at or above MIN_SIZE_ABS             | 23     | 22   |
    | T5    | region <-> string coverage, and no unknown token   | 14     | 14   |

T3 moves to 67 because koreanize's 22 is `EXIT_TOLERANCE` and belongs to T4: an
inventory that does not match is a produced artifact that failed structural
verification, which is exactly what 67 means. T4 moves to 22 because it IS the
`deep-shrink` tolerance row -- it has an `--accept-deep-shrink` flag and a named
human decision behind it, which no other T-check has. T5 keeps 14 because §4.2
names it in that row's own definition: "input drift (T5 class)".

Exit codes (§4.2):
   0  every face typeset, T1-T5 clean, the icon map re-measured and separated
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin, its AI contract, or a
      data_root write this stage may not make
  11  stop by policy -- the ten rules stopped this run
  13  precondition -- no composite/check predecessor, no Korean text, an empty
      icon universe, an unresolved font role, or a bundle input missing
  14  T5 -- content coverage: a region with no string, a string with no region,
      or a markup/[icon] token outside the PUA map. An INPUT-DRIFT class,
      evaluated BEFORE any pixel is drawn
  20  T1 -- a drawn pixel landed where the mask is not clear
  21  T2 -- a field's ink left its own region union
  22  a tolerance-bearing predicate fired without its flag: T4 `deep-shrink`, or
      `alignment` displacement beyond its axis tolerance
  25  the stage stopped between batches on its budget; N of M are on disk
  30  NOT IN THIS TABLE, deliberately -- see below
  65  claude unavailable / unauthenticated / timed out
  66  AI manifest invalid -- schema, coverage, or run binding
  67  the produced artifact failed structural verification: T3, the anti-swap
      declaration check, the pairwise separation check, or an allocation_mode
      that contradicts its own paragraph pin

THERE IS NO 30 IN THAT TABLE, AND THAT IS THE POINT. A pending gate is not this
stage's failure: it attaches a `pending` gate block to its report and exits 0,
exactly as `kz_init.py` and `kz_terms.py` do, and `compute_consumable` is what
stops `recompose` consuming pixels nobody has walked. Exiting 30 here would make
the stage FAIL on every first successful run, before any human could possibly
have accepted a gallery that did not exist until this run wrote it.

THE INPUT IS A JOIN, AND THAT IS WHY `typeset` HAS TWO PREDECESSORS
--------------------------------------------------------------------
`kz_config.PREDECESSORS["typeset"] == ("composite", "check")`, and neither edge is
decorative:

  * `<run_dir>/composite.json`'s `results.faces[]` is the authoritative face SET --
    which faces exist, their cleared digests, and which of them `composite`
    refused. It carries no geometry, deliberately: rectangles are the mask stage's
    measurement, not the compositor's.
  * `<run_dir>/masks/manifest.json`'s `entries[]` is the GEOMETRY -- the per-field
    rectangles in both frames -- and its `per_type{}` block carries each group's
    `rot` and its layout `windows`.
  * `<run_dir>/check.json` is the text edge: `check` is what proves the Korean
    strings this stage sets are the strings `translate` produced.

The rectangles are read in SLICE coordinates (`regions_slice_coords`), because the
mask PNG this stage derives `clear` from is stored in the slice frame -- the
manifest's own `orientation` field says so. Handing a rot-90 group's upright rects
to its slice would not error; it would silently address the wrong axis, which is
the same frame mistake §5.5 spends a paragraph warning W1/W2 about.

A missing half, a missing entry or a missing file is exit 13 NAMING IT, never a
silently empty face set -- a stage that typesets zero faces and reports PASS is
the failure mode this whole tool exists to make impossible.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402
import kz_ask as ka  # noqa: E402
import kz_decide as kd  # noqa: E402
import kz_checkers as kx  # noqa: E402

STAGE = "typeset"
SID = "S4"

# VERIFIED AGAINST kz_config BEFORE THE DECLARATION, not after. `declare_ai`
# raises on a contract that disagrees with scenario.json, but it cannot catch a
# stage NAME that is not in the universe at all -- AI_CONTRACT is a plain dict and
# would happily register `typset`, and the mismatch would then surface as a
# `required_stages` complaint about a stage nobody can run.
if STAGE not in kz.STAGES:
    kc.refuse(kc.EXIT_GUARD, "%r is not a koreanize stage" % STAGE,
              "kz_config.STAGES is the universe")
if kz.AI_STAGE_MAP.get(SID) != STAGE:
    kc.refuse(kc.EXIT_GUARD,
              "kz_config.AI_STAGE_MAP[%r] is %r, not %r"
              % (SID, kz.AI_STAGE_MAP.get(SID), STAGE),
              "the S-id and the stage name are one fact and have one owner")
if kz.PREDECESSORS.get(STAGE) != ("composite", "check"):
    kc.refuse(kc.EXIT_GUARD,
              "kz_config.PREDECESSORS[%r] is %r" % (STAGE, kz.PREDECESSORS.get(STAGE)),
              "typeset consumes cleared pixels AND checked Korean text; both edges "
              "are load-bearing and this module reads both artifacts")

# Declared in the MODULE BODY (§3.2): a scenario.json that does not list
# `typeset` in ai.required_stages refuses at exit 4 the moment it is bound, and
# `kz_common.write_report` refuses a build report with `ai: null` at exit 11.
kc.declare_ai(STAGE, required=True)

FAULTS = ("deep-shrink", "alignment", "anti-swap", "allocation", "content",
          "inventory", "containment", "draw", "gate")

#: PASSED EXPLICITLY, and it has to be. `kz_ask.prompt_path_for` derives
#: `prompts/<Sid>-<AI_STAGE_MAP[sid]>.md`, which for S4 is `S4-typeset.md` -- a
#: file that does not exist and never will, because the design pins this prompt's
#: filename as `S4-icons.md` (it is named for the UNIVERSE, tokens, not for the
#: stage that consumes it). `_read_text` refuses at exit 13 on a missing prompt,
#: so the derived lookup fails on every run. `prompt=` is a first-class parameter
#: of `build_bundle` and exists for exactly this; renaming the prompt or editing
#: `kz_ask.py` would be the wrong fix in both directions.
PROMPT_PATH = os.path.join(kc.PACKAGE_DIR, "prompts", "S4-icons.md")


# ---------------------------------------------------------------------------
# 1. The constants, lifted from typeset-cards.py with their reasons
# ---------------------------------------------------------------------------
#
# Every number here is MEASURED, and the measurement is what the comment carries.
# A constant whose rationale is absent is a constant the next person tunes.

#: `fonts.<role>` in scenario.json carries three roles (kz_init.FONT_ROLES);
#: typeset-cards.py distinguished five FACES. The mapping is one-way and lives
#: here rather than in the config, because which face a field is set in is a
#: TYPESETTING decision and the config's job is identity, not design.
ROLE_FONT = {
    "title": "title", "subtitle": "title", "type_banner": "title",
    "stage_label": "title", "traits": "body", "text": "body",
    "flavor": "body", "victory": "body",
}

#: The legibility floor, per field. Exit 22 (T4) means only "not even this fits".
MIN_SIZE_ABS = {
    "title": 15, "subtitle": 15, "traits": 16, "text": 16,
    "flavor": 16, "victory": 16, "type_banner": 15, "stage_label": 15,
}

#: A REPORTING threshold, not a stop: below it the field is `deep` and the run is
#: non-consumable until `--accept-deep-shrink` acknowledges it. A field that
#: shrank to 0.85 of natural is shrunk, reported, and not a decision.
MIN_SIZE_RATIO = 0.80
HEAVY_SHRINK_RATIO = 0.92

#: Displacement tolerated between a block's laid-out ink and its alignment
#: reference axis, per axis kind. Two numbers and not one: a centred field is
#: judged on its centre and a left-aligned one on its left edge, and the left
#: edge is the tighter measurement because it has no averaging in it.
ALIGNMENT_TOL_PX = {"center": 8, "left": 6}

#: The three allocation modes, and the whole point of §5.6 item 4 is that this
#: tuple has THREE members rather than two.
ALLOCATION_MODES = ("per-band", "single-band", "paragraph-pinned")

#: Horizontal guard, measured: the maximum ink overhang beyond the advance width
#: at size 64 is 1.1 px, so 2 covers it at every size this stage uses.
X_GUARD = 2

#: masks/manifest.json's own identity -- "every line becomes a rectangle padded by
#: 12px HORIZONTALLY and 7px vertically". Measured text ink can therefore never
#: legitimately begin left of `rect_x0 + A6_PAD_X`, and the alignment reference is
#: clamped by it: on faces whose body rect reaches into the dark frame scrollwork
#: the raw leftmost-ink column is that ornament, and left-aligning Korean to it
#: sets the first line on top of the scroll.
A6_PAD_X = 12

#: The flavour double rule is a pair of VERTICAL strokes, `||` not `=`.
RULE_STROKE_W = 1
RULE_STROKE_GAP = 7

#: Paragraph gap and blockquote indent, both as a ratio of the type size so they
#: follow the ladder down instead of becoming proportionally larger at every rung.
PARA_GAP_RATIO = 0.45
BLOCKQUOTE_INDENT_RATIO = 0.9
LEADING_RATIO = 1.32

#: The counted inline tags, shared with `kz_checkers.COUNTED_TAGS` rather than
#: re-listed, so the tokenizer this stage draws from and the tokenizer `check`
#: audits with cannot disagree about what a tag is.
COUNTED_TAGS = kx.COUNTED_TAGS

#: Fields that are laid out as a single line and never wrap.
SINGLE_LINE_ROLES = ("subtitle", "traits", "victory", "type_banner", "stage_label")
#: Fields that flow as paragraphs, in the order the plate prints them.
BODY_FIELDS = ("text", "flavor")
ALL_FIELDS = ("title", "subtitle", "traits", "text", "flavor", "victory",
              "type_banner", "stage_label")

#: `data/icons/core.json` is the SHARED core symbol set. It is human-curated data
#: imported by §6 step 8, it is cited by this stage as `material/icons-reference`,
#: and it is never written by a stage run: a scenario that could overwrite the
#: shared reference with its own rulings would make the reference cite itself.
CORE_ICON_SET = "core"


class Overflow(Exception):
    """A layout that does not fit at the size it was tried at.

    Carries `extra_px` -- how many more cleared pixels the mask would have had to
    offer -- because that is the number the dry-run reports and the only one that
    tells an operator how much to widen a window by.
    """

    def __init__(self, extra_px, detail):
        Exception.__init__(self, detail)
        self.extra_px = int(extra_px or 0)
        self.detail = detail


class MarkupError(Exception):
    """An unknown `[token]` or tag. A T5 (exit 14), never printed literally."""


# ---------------------------------------------------------------------------
# 2. The PUA icon map and the anti-swap invariant (§5.6)
# ---------------------------------------------------------------------------
#
# THE INVARIANT IS NOT REIMPLEMENTED HERE. `kz_checkers` owns it -- both halves,
# their margins, and the `swap/` fixture -- and this module CALLS it. That is not
# tidiness: the checker was authored before its producers (§6 step 9) precisely so
# the thing being checked cannot also define the check, and a second copy of
# `ICON_SEPARATION_MIN` living beside the map it polices is how a margin gets
# widened by whoever is inconvenienced by it.
#
# What this module owns is the three steps around the call:
#   (a) turn the S4 rulings into a DECLARED {token: (ink_fill, advance_em)} map,
#   (b) RE-MEASURE each assigned codepoint from the resolved icon font, and
#   (c) report both halves as checks that exit 67.

def codepoint_char(codepoint):
    """`"U+F25E"` -> the character. Refuses anything else.

    Codepoints, never literals: a PUA literal is invisible in a diff and silently
    corruptible by any tool in the chain -- which is exactly how a map gets a
    character replaced by a look-alike with nobody able to see it in review.
    """
    text = str(codepoint or "")
    if not text.startswith("U+"):
        raise MarkupError("codepoint %r is not in U+XXXX form" % codepoint)
    try:
        value = int(text[2:], 16)
    except ValueError:
        raise MarkupError("codepoint %r has no hex body" % codepoint)
    if not 0 < value <= 0x10FFFF:
        raise MarkupError("codepoint %r is outside Unicode" % codepoint)
    return chr(value)


def declared_map(rulings):
    """The S4 rulings, as ({token: (ink_fill, advance_em)}, {token: codepoint}).

    `abstain` rulings are EXCLUDED from both, not defaulted to anything: an
    abstention is the agent saying it could not tell two candidates apart, and
    inventing a pair for it is the one response that destroys the information.
    Rule 5 already stops the stage on an abstained novel verdict; this just makes
    sure the abstention cannot leak into the map on the way there.
    """
    declared, codepoints = {}, {}
    for ruling in rulings or []:
        if ruling.get("verdict") != "map":
            continue
        token = ruling.get("unit_id")
        value = ruling.get("value") or {}
        if token is None:
            continue
        declared[token] = (float(value.get("ink_fill") or 0.0),
                           float(value.get("advance_em") or 0.0))
        codepoints[token] = value.get("codepoint")
    return declared, codepoints


def measure_map(font_path, codepoints):
    """Re-measure every assigned codepoint from the ACTUAL font outlines.

    Delegates to `kz_checkers.measure_icon_metrics`, which is where the probe
    size, the coverage threshold and the advance size are declared. A token whose
    codepoint will not parse is left out of the measured map, which
    `check_icon_declarations` then reports as "declared but the font carries no
    measurement" -- the same finding a missing glyph produces, and correctly so:
    from the map's point of view both are "this declaration names nothing".
    """
    measured = {}
    for token in sorted(codepoints):
        try:
            char = codepoint_char(codepoints[token])
        except MarkupError:
            continue
        measured[token] = kx.measure_icon_metrics(font_path, char)
    return measured


def icon_map_checks(declared, measured):
    """The two halves, as report `checks[]` rows. Both exit 67.

    (a) catches a swap of two well-separated glyphs -- a swap makes BOTH
        declarations wrong at once, in BOTH dimensions, which is the only reason
        it is detectable when every stage downstream of the map is
        value-transparent.
    (b) refuses a map containing a pair (a) could not have caught, at
        MAP-AUTHORING time rather than at typeset time when the pixels are shipped.
    """
    decl_findings = kx.check_icon_declarations(declared, measured)
    sep_findings = kx.check_icon_separation(declared)
    return [
        {"id": "X1", "name": "icon_declarations",
         "status": "fail" if decl_findings else "pass",
         "exit_on_fail": kc.EXIT_ARTIFACT, "detail": decl_findings},
        {"id": "X2", "name": "icon_separation",
         "status": "fail" if sep_findings else "pass",
         "exit_on_fail": kc.EXIT_ARTIFACT, "detail": sep_findings},
    ]


#: The container key of a `data/icons/<set>.json`, and the three keys every entry
#: carries FLAT -- exactly `kz_decide.VALUE_SCHEMA["S4"]`'s closed key set, which
#: is `additionalProperties: False` over the same three and makes all three
#: mandatory through `VALUE_REQUIRED["S4"]`. Named here so the reader, the writer
#: and the manifest schema are one fact rather than three.
ICON_SET_CONTAINER = "icons"
ICON_SET_KEYS = ("codepoint", "ink_fill", "advance_em")


def load_icon_set(path):
    """Read a `data/icons/<set>.json` into {token: {codepoint, ink_fill, advance_em}}.

    STRICT ABOUT THE CONTAINER AND STRICT ABOUT THE TRIPLE, and the first half of
    that was a deliberate reversal. A reader that also accepted `tokens{}` or
    `map{}`, or an entry nested under `value{}`, would let the two writers of this
    file -- the step-8 backfill for `core.json` and this stage for a scenario's own
    set -- diverge in SILENCE and still load. That divergence does not stay
    invisible: it surfaces later as a wrong `ink_fill`, which is the exact failure
    class the anti-swap invariant exists to catch, arriving through the reader
    instead of through the map. It is the same shape as a budget guard that failed
    open on a bad knob and a verify check that passed vacuously on an empty
    subject; both were closed, and so is this.

    Refuses at EXIT_ARTIFACT (67) rather than 13, and NAMES THE CONTAINER KEY IT
    ACTUALLY FOUND, so a divergence is recorded as a structural fault in a durable
    artifact rather than absorbed as a missing input.
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        try:
            doc = json.load(handle)
        except ValueError as exc:
            kc.refuse(kc.EXIT_ARTIFACT, "%s is not valid JSON" % path, str(exc))
    container = doc.get(ICON_SET_CONTAINER)
    if not isinstance(container, dict):
        found = sorted(k for k, v in doc.items() if isinstance(v, dict))
        kc.refuse(kc.EXIT_ARTIFACT,
                  "%s declares no %s{} container" % (path, ICON_SET_CONTAINER),
                  "object-valued keys present: %s -- the container key is pinned "
                  "to %r so the two writers of this file cannot diverge silently"
                  % (found or ["(none)"], ICON_SET_CONTAINER))
    out = {}
    for token in sorted(container):
        entry = container[token]
        if not isinstance(entry, dict):
            kc.refuse(kc.EXIT_ARTIFACT,
                      "%s: entry %r is not an object" % (path, token))
        if "value" in entry:
            kc.refuse(kc.EXIT_ARTIFACT,
                      "%s: entry %r nests its fields under value{}" % (path, token),
                      "the entry is FLAT, mirroring kz_terms' terms{} and "
                      "kz_decide.VALUE_SCHEMA['S4']'s closed key set")
        missing = [k for k in ICON_SET_KEYS if entry.get(k) is None]
        if missing:
            kc.refuse(kc.EXIT_ARTIFACT,
                      "%s: entry %r is missing %s" % (path, token, ", ".join(missing)),
                      "the measured pair is the anti-swap mechanism (§5.6) and is "
                      "mandatory in the set file exactly as it is in the manifest")
        out[token] = {"codepoint": entry["codepoint"],
                      "ink_fill": float(entry["ink_fill"]),
                      "advance_em": float(entry["advance_em"])}
    return out


def build_icon_set(set_name, slug, units, rulings):
    """`data/icons/<set>.json` -- the durable half of this stage's judgement.

    KEY-SORTED BEFORE IT IS HANDED OVER, because `sced_io.py:99` passes no
    `sort_keys` (§5.3): a mapping whose key order depends on insertion produces a
    different byte stream on every run and the receipt stops being a diff anybody
    can read.
    """
    icons = {}
    for ruling in sorted(rulings or [], key=lambda r: str(r.get("unit_id"))):
        if ruling.get("verdict") != "map":
            continue
        token = ruling.get("unit_id")
        value = ruling.get("value") or {}
        unit = (units or {}).get(token) or {}
        icons[token] = kc.sorted_mapping({
            "codepoint": value.get("codepoint"),
            "ink_fill": value.get("ink_fill"),
            "advance_em": value.get("advance_em"),
            "source": ruling.get("verdict"),
            "confidence": ruling.get("confidence"),
            "rationale": ruling.get("rationale"),
            "occurrences": unit.get("occurrences"),
        })
    return kc.sorted_mapping({
        "schema_version": kc.SCHEMA_VERSION,
        "generated_by": "koreanize typeset",
        "generated_at": kc.utc_now(),
        # `set` is the one field this envelope adds over kz_terms': that file is
        # per-scenario and keyed by slug, this one is per SET and a scenario's set
        # is not necessarily its slug.
        "set": set_name,
        "slug": slug,
        ICON_SET_CONTAINER: kc.sorted_mapping(icons),
    })


def icon_set_name(cfg):
    """Which `data/icons/<set>.json` this run writes.

    Never `core`. `core.json` is the shared symbol set, imported as data and cited
    by every scenario as `material/icons-reference.json`; a stage that could
    overwrite it would let one scenario's rulings redefine the authority every
    other scenario is checked against, and the citation would then be circular.
    """
    name = (cfg.get("icon_set") or cfg["slug"]).strip()
    if name == CORE_ICON_SET:
        kc.refuse(kc.EXIT_GUARD,
                  "typeset may not write data/icons/%s.json" % CORE_ICON_SET,
                  "core.json is the shared, human-curated symbol set imported by "
                  "the backfill; this stage cites it and writes its own set")
    return name


# ---------------------------------------------------------------------------
# 3. The universe -- every DISTINCT [icon] token, never an occurrence
# ---------------------------------------------------------------------------

def extract_icons(ko_doc, en_doc=None):
    """(universe[], units{}) -- the distinct `[icon]` tokens in scope.

    §3.7: "every *distinct* `[icon]` name in scope, never an occurrence. Midwinter's
    `card-source-en.json` carries 132 occurrences of 17 distinct tokens, and 17 is
    the universe." The occurrence count travels in `units{}` because it tells the
    reviewer what a mistake would COST, not because it changes the unit.

    The Korean text is the primary source and the English is folded in when given:
    a token the translation dropped is still a token the plate has to set, and a
    universe derived from the Korean alone would silently shrink with the defect.
    """
    seen = {}

    def note(doc, language):
        cards = (doc.get("cards") if isinstance(doc, dict) else doc) or []
        for card in cards:
            ident = str(card.get("arkham_id") or card.get("code") or "")
            for field in kx.TEXT_FIELDS:
                text = card.get(field)
                if not text:
                    continue
                icons, _refs, _tags = kx.scan(text)
                for token in icons:
                    entry = seen.setdefault(token, {
                        "token": token, "occurrences": 0, "cards": [],
                        "fields": [], "languages": []})
                    entry["occurrences"] += 1
                    if ident and ident not in entry["cards"] and len(entry["cards"]) < 8:
                        entry["cards"].append(ident)
                    if field not in entry["fields"]:
                        entry["fields"].append(field)
                    if language not in entry["languages"]:
                        entry["languages"].append(language)

    note(ko_doc, "ko")
    if en_doc is not None:
        note(en_doc, "en")

    units = {}
    for token in sorted(seen):
        entry = seen[token]
        entry["fields"] = sorted(entry["fields"])
        entry["languages"] = sorted(entry["languages"])
        units[token] = entry
    universe = sorted(units)
    if not universe:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the Korean corpus yields no [icon] tokens",
                  "S4's universe is the distinct token set; an empty universe "
                  "cannot be adjudicated and must not be invented")
    return universe, units


# ---------------------------------------------------------------------------
# 4. Text measurement -- injectable, so the layout core needs no font
# ---------------------------------------------------------------------------
#
# §5.5's rule for W1/W2 applies here for the same reason: the predicate that
# matters must be exercisable on a machine with no corpus and no licensed font.
# Every layout function below takes a `metrics` object and asks it three
# questions; `SyntheticMetrics` answers them arithmetically and `FontMetrics`
# answers them from the outlines. The layout code cannot tell which it has, which
# is what makes the synthetic selftest evidence about the real path.

class SyntheticMetrics(object):
    """Arithmetic metrics: one em per character, ink height == size.

    Ink height EQUALS the size deliberately. It makes every arithmetic assertion
    in `--selftest` legible -- "a 40 px band cannot host a 41 px line" is then a
    statement about the numbers in the test rather than about a font's internal
    leading -- and it is the same simplification `kz_checkers.synth_slice` makes
    for the mask predicates.
    """

    def __init__(self, width_em=0.5):
        self.width_em = float(width_em)

    def advance(self, role, text, size):
        return len(text or "") * size * self.width_em

    def line_box(self, role, size):
        return (-int(round(0.78 * size)), int(round(0.22 * size)))

    def natural_size(self, role, en_ink_h):
        return max(1, int(round(en_ink_h)))

    def _font(self, role, size):
        """PIL's own bundled face, so the DRAW layer is exercisable with no font
        installed. `draw_face` asks `metrics` for this and for nothing else, which
        is what lets the containment proof be tested on a bare machine -- the same
        rule §5.5 applies to W1/W2."""
        from PIL import ImageFont
        return ImageFont.load_default(size)


class FontMetrics(object):
    """PIL-backed metrics over the resolved `fonts.<role>` faces.

    ART TIER ONLY: PIL is imported in the constructor rather than at module scope,
    so importing this module -- which `koreanize.sh --status` and the test suite
    both do -- costs nothing on a machine where the fonts are not installed.
    """

    def __init__(self, paths):
        from PIL import ImageFont  # noqa: F401  (asserted present here, used below)
        self._paths = dict(paths)
        self._cache = {}

    def _font(self, role, size):
        from PIL import ImageFont
        key = (role, int(size))
        if key not in self._cache:
            path = self._paths.get(role)
            if not path:
                kc.refuse(kc.EXIT_PRECONDITION,
                          "no font resolved for role %r" % role,
                          "fonts.%s in scenario.json, or its env var" % role)
            self._cache[key] = ImageFont.truetype(path, int(size))
        return self._cache[key]

    def advance(self, role, text, size):
        return float(self._font(role, size).getlength(text or ""))

    def line_box(self, role, size):
        # GUARD_SAMPLE, not the optical probe sample. typeset-cards.py:262-271
        # records why: at size 64 the probe sample's body box is top -48 / bottom
        # +9 while the corpus's true extremes are -50 / +12, so placing a line's
        # sample-box top on a band's top row spilled ink ONE ROW above the band on
        # 74 of 88 faces -- a T1 and a T2 on nearly every card.
        font = self._font(role, size)
        box = font.getbbox(GUARD_SAMPLE, anchor="ls")
        return (int(box[1]), int(box[3]))

    def natural_size(self, role, en_ink_h):
        """The size whose ink height matches the English ink height, by search.

        A search and not a ratio: the ink-height-to-size ratio is a property of the
        face, and hardcoding one makes the Korean type quietly wrong the day a
        face is replaced by one with different metrics.
        """
        target = max(1, int(round(en_ink_h)))
        low, high = 4, 240
        best = low
        while low <= high:
            mid = (low + high) // 2
            top, bot = self.line_box(role, mid)
            if (bot - top) <= target:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best


#: The measured extremal characters of this corpus -- they cover the true top and
#: bottom of all four Korean faces exactly, verified over all 598 distinct chars.
GUARD_SAMPLE = "()\"'흥후혹원큰q_pgy,"


# ---------------------------------------------------------------------------
# 5. Geometry -- bands and cleared runs
# ---------------------------------------------------------------------------
#
# THE CENTRAL INVARIANT, restated because every function below depends on it:
# `clear` is the ONLY authority on where ink may land. It already subsumes the
# region rectangles, the protected list and the edge guard. Every line is packed
# against the longest FULLY CLEAR column run spanning that line's OWN rows --
# never against a band median, never against the region rectangle. That is what
# makes T1 a proof rather than a hope, and it is why a Korean line may run LONGER
# than the English line above it where the mask allows and is refused where it
# does not.

def clear_cumsum(clear):
    """Column-wise prefix sums, so a "fully clear across these rows" question is
    two array reads instead of a slice-and-reduce per candidate line."""
    import numpy as np
    rows = np.zeros((clear.shape[0] + 1, clear.shape[1]), dtype="int32")
    rows[1:] = np.cumsum(clear.astype("int32"), axis=0)
    return rows


def usable_run(cs, y0, y1, x_lo, x_hi):
    """The longest run of columns fully clear across rows y0..y1, inside [x_lo, x_hi].

    Returns (a, b) inclusive, or None. `cs` is `clear_cumsum(clear)`.
    """
    import numpy as np
    height = y1 - y0 + 1
    if height <= 0 or y1 + 1 >= cs.shape[0]:
        return None
    x_lo = max(0, int(x_lo))
    x_hi = min(cs.shape[1] - 1, int(x_hi))
    if x_hi < x_lo:
        return None
    ok = (cs[y1 + 1, x_lo:x_hi + 1] - cs[y0, x_lo:x_hi + 1]) == height
    best = None
    start = None
    for index in range(ok.shape[0] + 1):
        inside = bool(ok[index]) if index < ok.shape[0] else False
        if inside and start is None:
            start = index
        elif not inside and start is not None:
            span = (start, index - 1)
            if best is None or (span[1] - span[0]) > (best[1] - best[0]):
                best = span
            start = None
    if best is None:
        return None
    return (x_lo + best[0], x_lo + best[1])


def bands_for(clear, rect):
    """Row runs inside `rect` that carry any clear pixel at all.

    A band is a contiguous stretch of rows with somewhere to put ink; the gaps
    between bands are the plate's own furniture -- a medallion, a rule, a
    scrollwork inset. Paragraph pinning is exactly the observation that those gaps
    are sometimes the layout the plate INTENDED, not obstacles to flow around.
    """
    import numpy as np
    x0, y0, x1, y1 = [int(v) for v in rect]
    y0 = max(0, y0)
    y1 = min(clear.shape[0] - 1, y1)
    x0 = max(0, x0)
    x1 = min(clear.shape[1] - 1, x1)
    if y1 < y0 or x1 < x0:
        return []
    rows = clear[y0:y1 + 1, x0:x1 + 1].any(axis=1)
    bands = []
    start = None
    for index in range(rows.shape[0] + 1):
        inside = bool(rows[index]) if index < rows.shape[0] else False
        if inside and start is None:
            start = index
        elif not inside and start is not None:
            bands.append((y0 + start, y0 + index - 1))
            start = None
    return bands


def rect_union(rects):
    return [min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects)]


# ---------------------------------------------------------------------------
# 6. Tokenising -- paragraphs, words, and the runs inside a word
# ---------------------------------------------------------------------------

def tokenise(text, base_role, icon_map, where=""):
    """The Korean string, as paragraphs of runs. Unknown token -> MarkupError (T5).

    NO REGEX, the same rule and the same reason as `kz_checkers.scan`: the corpus
    carries `[[Trait]]` beside `[icon]`, and a regex that gets the nesting subtly
    wrong produces a token named `[Trait` that then fails the PUA lookup with a
    message about the wrong thing. A character walk is longer and is checkable by
    reading it.
    """
    paras = []
    current = {"hr": False, "blockquote": False, "runs": []}

    def flush():
        if current["runs"] or current["hr"]:
            paras.append({"hr": current["hr"],
                          "blockquote": current["blockquote"],
                          "runs": list(current["runs"])})
        current["runs"] = []
        current["hr"] = False
        current["blockquote"] = False

    role_stack = [base_role]
    index = 0
    length = len(text or "")
    buf = []

    def emit():
        if buf:
            current["runs"].append((role_stack[-1], "".join(buf)))
            del buf[:]

    while index < length:
        ch = text[index]
        if ch == "\n":
            emit()
            # A blank line ends the paragraph; a single newline is a soft break
            # and is treated as a space, which is what the plate does.
            if text[index:index + 2] == "\n\n":
                flush()
                index += 2
                while index < length and text[index] == "\n":
                    index += 1
                if text[index:index + 2] == "> ":
                    current["blockquote"] = True
                    index += 2
                continue
            buf.append(" ")
            index += 1
            continue
        if text[index:index + 2] == "[[":
            close = text.find("]]", index + 2)
            if close != -1:
                buf.append(text[index + 2:close])
                index = close + 2
                continue
        if ch == "[":
            close = text.find("]", index + 1)
            if close != -1:
                name = text[index + 1:close]
                if name and all(c.islower() or c == "_" for c in name):
                    char = icon_map.get(name)
                    if char is None:
                        raise MarkupError("%s: [%s] is outside the PUA map"
                                          % (where, name))
                    emit()
                    current["runs"].append(("icons", char))
                    index = close + 1
                    continue
        if ch == "<":
            close = text.find(">", index + 1)
            if close != -1:
                tag = text[index + 1:close].strip().lower()
                bare = tag[1:] if tag.startswith("/") else tag
                if bare == "hr":
                    emit()
                    flush()
                    current["hr"] = True
                    flush()
                    index = close + 1
                    continue
                if bare in COUNTED_TAGS:
                    emit()
                    if tag.startswith("/"):
                        if len(role_stack) > 1:
                            role_stack.pop()
                    else:
                        role_stack.append(base_role)
                    index = close + 1
                    continue
                raise MarkupError("%s: <%s> is not a known tag" % (where, tag))
        buf.append(ch)
        index += 1
    emit()
    flush()
    return paras


def para_words(para):
    """A paragraph's runs, regrouped into words.

    A word is a list of (role, text) runs, because a word may legitimately mix
    faces -- `[skull]:` is an icon run and a body run with no space between them,
    and splitting it across a line break would put the colon on the next line.
    """
    words = []
    current = []
    for role, text in para["runs"]:
        if role == "icons":
            current.append((role, text))
            continue
        parts = (text or "").split(" ")
        for position, part in enumerate(parts):
            if position:
                if current:
                    words.append(current)
                current = []
            if part:
                current.append((role, part))
    if current:
        words.append(current)
    return words


def word_advance(word, size, metrics):
    return sum(metrics.advance(role, text, size) for role, text in word)


def word_text(word):
    return "".join(text for _role, text in word)


# ---------------------------------------------------------------------------
# 7. Allocation -- the explicit per-face declaration (§5.6 item 4)
# ---------------------------------------------------------------------------

def choose_allocation(groups, bands, ink_h):
    """(mode, pin_bands) -- the EXPLICIT declaration, decided once and recorded.

    Three modes, and the third is the fix:

      `paragraph-pinned` -- one group, no rule ornament, and exactly as many
          hostable bands as paragraphs. The plate sets one paragraph beside one
          medallion, and the bands ARE the intended layout rather than obstacles
          to flow around. Each paragraph is bound to its own band, so it can
          neither spill into the next nor be joined to the previous, and
          `para_gaps` is 0 BY CONSTRUCTION -- a fresh band already separates them,
          so no paragraph pays a gap.
      `per-band`        -- more than one group, each owning its own band slice.
          The rules block can then never spill into the flavour block's band and
          silently look plausible.
      `single-band`     -- everything else: one flow over every band.

    THE PREDICATE REFUSES UNLESS THE GEOMETRY SUPPORTS IT, which is the same shape
    the per-band decision already had. `hostable` is recomputed at every rung of
    the ladder, because the ink height changes at every rung and a band that could
    not host a line at the natural size can host one two sizes down. Computing it
    once at the natural size mis-maps a medallion-only band that must be SKIPPED.
    """
    hostable = [index for index, (b0, b1) in enumerate(bands)
                if (b1 - b0 + 1) >= ink_h]
    if (len(groups) == 1
            and groups[0].get("bands_slice") is None
            and len(hostable) >= 2
            and len(groups[0]["paras"]) == len(hostable)
            and not any(p["hr"] for p in groups[0]["paras"])):
        return "paragraph-pinned", hostable
    if len(groups) > 1 and all(g.get("bands_slice") is not None for g in groups):
        return "per-band", None
    return "single-band", None


def check_allocation_declared(faces):
    """The 71001 defect, made mechanical. Exit 67.

    A face carrying `paragraph_bands` under any mode other than `paragraph-pinned`
    is the exact state that let a pinned face pass a human gate on 2026-08-18: the
    gate's own filter partitioned on `allocation_mode`, the pinned face declared
    `single-band`, and the section that should have listed it structurally could
    not. The inverse is a defect too -- a face declaring the pin with no bands to
    pin to has a mode its geometry does not support -- and so is `para_gaps != 0`
    on a pinned face, because that is the pin not having been applied.
    """
    findings = []
    for face in faces or []:
        body = face.get("body")
        if not body:
            continue
        mode = body.get("allocation_mode")
        pinned = body.get("paragraph_bands")
        name = face.get("file") or face.get("arkham_id") or "?"
        if mode not in ALLOCATION_MODES:
            findings.append("%s: allocation_mode %r is not one of %s"
                            % (name, mode, list(ALLOCATION_MODES)))
            continue
        if pinned and mode != "paragraph-pinned":
            findings.append("%s: carries paragraph_bands %s under allocation_mode "
                            "%r -- a pinned face that does not declare the pin is "
                            "invisible to every filter that reads the mode"
                            % (name, pinned, mode))
        if mode == "paragraph-pinned":
            if not pinned:
                findings.append("%s: declares paragraph-pinned with no "
                                "paragraph_bands" % name)
            if body.get("para_gaps"):
                findings.append("%s: paragraph-pinned with para_gaps %s -- a pinned "
                                "paragraph pays no gap, so the pin was not applied"
                                % (name, body.get("para_gaps")))
    return findings


# ---------------------------------------------------------------------------
# 8. Layout and the shrink ladder
# ---------------------------------------------------------------------------

def alignment_for(group, field):
    """A DECLARED table, not a derivation.

    Ornament fragments poison a naive derivation: on the Story b-side the title
    scored 222 px of "centre deviation" purely from a corner pip. A table can be
    read and disagreed with; a derivation over plate art cannot.
    """
    if field in ("title", "subtitle", "traits", "victory", "type_banner",
                 "stage_label"):
        return "center"
    if field == "text":
        return "left"
    return "left" if str(group).split("/")[0] in ("Act", "Agenda") else "center"


def build_group(field, ko_text, rects, group, icon_map, bands_slice=None,
                ref=None, window=None):
    """One layout group. `x_range` is CONTAINMENT, `ref_*` is ALIGNMENT.

    They are different questions and conflating them is what put a face's flavour
    rule outside the mask: containment comes from the rectangles the mask actually
    cleared, alignment comes from where the English ink was printed. The reference
    is clamped into `union + A6_PAD_X` because the padding is an IDENTITY of the
    mask method, so measured ink can never legitimately begin left of it -- and on
    faces whose rects reach into the frame scrollwork the raw leftmost-ink column
    is that ornament.
    """
    union = rect_union(rects)
    rx0 = ref[0] if ref and ref[0] is not None else union[0]
    rx1 = ref[1] if ref and ref[1] is not None else union[2]
    rx0 = max(rx0, union[0] + A6_PAD_X)
    rx1 = min(max(rx1, rx0 + 1), max(union[2] - A6_PAD_X, rx0 + 1))
    align = alignment_for(group, field)
    ref_src = "measured-ink"
    if align == "center" and window:
        # A centred field is centred on its GROUP's window, not on this card's
        # ink; a left-aligned one is not, because the window's left edge is the
        # panel edge and sits tens of px left of the text margin. Measured
        # 2026-08-18: centring on the measured English ink instead took alignment
        # violations 20 -> 24, adding six Asset/front titles at up to 59 px,
        # because on those faces the largest-ink title rect is a plate edge rather
        # than the glyph line. The window is deliberate. Do not "fix" it.
        rx0, rx1 = window[0], window[2]
        ref_src = "per-type-window"
    return {
        "field": field,
        "role": ROLE_FONT[field],
        "align": align,
        "ref_src": ref_src,
        "paras": tokenise(ko_text, ROLE_FONT[field], icon_map,
                          "%s/%s" % (group, field)),
        "x_range": (union[0], union[2]),
        "ref_cx": (rx0 + rx1) / 2.0,
        "ref_x0": rx0,
        "ref_box": [rx0, rx1],
        "bands_slice": bands_slice,
        "union": union,
    }


def lay_out(groups, bands, cs, size, leading, ink_h, top_off, metrics,
            allocation=None):
    """Flow the groups into the bands at one size. Raises Overflow if it will not fit.

    Returns a layout dict carrying its own `allocation_mode` and `paragraph_bands`,
    so the declaration travels WITH the pixels rather than being re-derived by
    whoever reports on them.
    """
    mode, pin_bands = (allocation if allocation is not None
                       else choose_allocation(groups, bands, ink_h))
    lines = []
    gaps = 0
    para_gap = int(round(PARA_GAP_RATIO * size))
    worst_extra = 0
    band_index = 0
    band_hi = len(bands) - 1
    y = bands[0][0] if bands else 0

    def advance_to_fit():
        """Move y to the first row in some band that can host a line of ink_h."""
        nonlocal y, band_index
        while band_index <= band_hi:
            b0, b1 = bands[band_index]
            if y < b0:
                y = b0
            if y + ink_h - 1 <= b1:
                return True
            band_index += 1
            if band_index <= band_hi:
                y = bands[band_index][0]
        return False

    for group_index, group in enumerate(groups):
        own = group.get("bands_slice")
        if own is not None:
            # per-band: this group owns bands[own[0] .. own[1]] and nothing else.
            # A fresh band already separates it from the previous group, so it
            # pays no paragraph gap.
            band_index, band_hi = own
            y = bands[band_index][0]
        else:
            band_hi = len(bands) - 1
            if group_index > 0:
                y += para_gap
                gaps += 1
        x_lo, x_hi = group["x_range"]
        for para_index, para in enumerate(group["paras"]):
            if pin_bands is not None:
                # THE PIN. `band_hi` is bound to this paragraph's OWN band, so a
                # pinned paragraph can never spill: `advance_to_fit()` runs off
                # the end and raises Overflow, which the ladder handles by
                # stepping down one size. And no gap is paid -- a fresh band
                # already separates it, which is why `para_gaps: 0` on a pinned
                # face is by construction and not a defect.
                band_index = band_hi = pin_bands[para_index]
                y = bands[band_index][0]
            elif para_index > 0:
                y += para_gap
                gaps += 1
            indent = (int(round(BLOCKQUOTE_INDENT_RATIO * size))
                      if para["blockquote"] else 0)
            if para["hr"]:
                if not advance_to_fit():
                    raise Overflow(0, "no band for <hr>")
                # The rule occupies TWO bars RULE_STROKE_GAP apart, so the
                # containment test must span every row it will touch: checking a
                # third of the line box cleared the first bar and left the second
                # unverified, which was 3 stray px per rule on one face.
                rows = RULE_STROKE_GAP + RULE_STROKE_W
                run = usable_run(cs, y, y + rows, x_lo + indent, x_hi - indent)
                if run is None:
                    raise Overflow(0, "no clear run for <hr>")
                lines.append({"kind": "hr", "y": y, "band": band_index,
                              "group": group_index, "para": para_index,
                              "run": run, "size": size, "ink_h": ink_h,
                              "top_off": top_off, "indent": indent,
                              "align": group["align"], "width": run[1] - run[0],
                              "ref_cx": group["ref_cx"], "ref_x0": group["ref_x0"],
                              "ref_box": group["ref_box"], "words": []})
                y += leading
                continue
            for words in [para_words(para)]:
                index = 0
                guard = 0
                while index < len(words):
                    guard += 1
                    if guard > 4000:
                        raise Overflow(0, "layout guard tripped")
                    if not advance_to_fit():
                        raise Overflow(worst_extra, "ran out of band")
                    run = usable_run(cs, y, y + ink_h - 1,
                                     x_lo + indent, x_hi - indent)
                    if run is None:
                        y += 1
                        if y + ink_h - 1 > bands[band_index][1]:
                            band_index += 1
                            if band_index > band_hi:
                                raise Overflow(worst_extra, "no clear run for a line")
                            y = bands[band_index][0]
                        continue
                    available = (run[1] - run[0] + 1) - 2 * X_GUARD
                    taken = []
                    width = 0.0
                    while index < len(words):
                        candidate = word_advance(words[index], size, metrics)
                        gap = metrics.advance(group["role"], " ", size) if taken else 0.0
                        if taken and width + gap + candidate > available:
                            break
                        width += gap + candidate
                        taken.append(words[index])
                        index += 1
                    if not taken:
                        # Not even ONE word fits this run. That is a real overflow
                        # and the deficit is what the dry run reports: how many
                        # more cleared pixels the mask would have had to offer.
                        need = int(round(word_advance(words[index], size, metrics)
                                         - available))
                        worst_extra = max(worst_extra, need)
                        raise Overflow(worst_extra,
                                       "a single word needs %d more clear px" % need)
                    lines.append({"kind": "text", "y": y, "band": band_index,
                                  "group": group_index, "para": para_index,
                                  "run": run, "words": taken, "width": width,
                                  "size": size, "ink_h": ink_h, "top_off": top_off,
                                  "indent": indent, "align": group["align"],
                                  "ref_cx": group["ref_cx"],
                                  "ref_x0": group["ref_x0"],
                                  "ref_box": group["ref_box"]})
                    y += leading

    used = sorted(set(line["band"] for line in lines))
    return {
        "lines": lines,
        "size": size,
        "leading": leading,
        "ink_h": ink_h,
        "top_off": top_off,
        "gaps": gaps,
        "allocation_mode": mode,
        "paragraph_bands": pin_bands,
        "bands_used": len(used),
        "declared_order": [g["field"] for g in groups],
        "height_needed": sum(line["ink_h"] for line in lines),
        "height_available": sum(b1 - b0 + 1 for b0, b1 in bands),
    }


def fit_block(groups, bands, cs, size_natural, floor, metrics):
    """THE LADDER. Steps DOWN one px at a time to `floor` and NEVER steps up.

    The floor is a LEGIBILITY floor, not a percentage: the record's spec says
    "reduce size for that card only, and log it. Never truncate", and nothing
    authorises refusing to draw at 79%. So T4 means only "not even MIN_SIZE_ABS
    fits" -- the genuinely unrecoverable case, whose answer is to widen that mask
    window, not to shrink further and not to draw outside.

    Every trial is recorded, the overflowing ones included, and THAT is the
    load-bearing half: it is what proves the fit ran against real per-line cleared
    runs rather than against an idealised `ceil(advance / median_run)`, which
    silently assumes the very over-wide wrap that T2 exists to refuse.
    """
    trials = []
    last = None
    for size in range(int(size_natural), int(floor) - 1, -1):
        top_off, bot_off = metrics.line_box(groups[0]["role"], size)
        ink_h = bot_off - top_off
        if ink_h <= 0:
            continue
        leading = max(ink_h + 1, int(round(LEADING_RATIO * size)))
        try:
            layout = lay_out(groups, bands, cs, size, leading, ink_h, top_off,
                             metrics)
        except Overflow as exc:
            trials.append({"size": size, "fit": False, "reason": exc.detail})
            last = exc
            continue
        layout["trials"] = trials + [{"size": size, "fit": True}]
        return layout
    raise Overflow(last.extra_px if last else 0,
                   "%s (at floor %d)" % (last.detail if last else "no size fits",
                                         floor))


def shrink_row(face, field, layout, size_natural):
    """One row of the shrink list. `deep` is the tolerance-bearing flag."""
    used = layout["size"]
    return {
        "file": face.get("file"), "arkham_id": face.get("arkham_id"),
        "side": face.get("side"), "field": field,
        "size_natural": size_natural, "size_used": used,
        "delta_px": size_natural - used,
        "delta_pct": round(100.0 * used / max(1, size_natural), 1),
        "lines_ko": len([l for l in layout["lines"] if l["kind"] == "text"]),
        "allocation_mode": layout["allocation_mode"],
        "heavy": used < HEAVY_SHRINK_RATIO * size_natural,
        "deep": used < int(round(MIN_SIZE_RATIO * size_natural)),
    }


# ---------------------------------------------------------------------------
# 9. Alignment
# ---------------------------------------------------------------------------

def line_x(line):
    """The drawn x of a line. ONE definition, used by the draw layer, both
    ornament anchors and the alignment report alike -- three copies of this
    arithmetic is how an ornament silently drifts from the text it sits beside."""
    x_lo, x_hi = line["run"]
    if line["align"] == "center":
        x = int(round(line["ref_cx"] - line["width"] / 2.0))
    else:
        x = int(round(line["ref_x0"] + line["indent"]))
    return max(x_lo, min(x, x_hi - int(round(line["width"])) - 2 * X_GUARD))


def group_ink_box(layout, group_index):
    """The laid-out ink box of ONE group, from its own lines.

    A merged body lays `text` and `flavor` as one flow, so the changed-pixel bbox
    of that draw covers BOTH. Reporting alignment from it charges the left-aligned
    rules block with the flavour block's centred left edge -- which is how a clean
    run once reported 94 alignment violations that were entirely an artifact of
    the report.
    """
    mine = [l for l in layout["lines"]
            if l["kind"] == "text" and l["group"] == group_index]
    if not mine:
        return None
    x0 = min(line_x(l) for l in mine)
    x1 = max(line_x(l) + int(round(l["width"])) for l in mine)
    y0 = min(l["y"] for l in mine)
    y1 = max(l["y"] + l["ink_h"] for l in mine)
    return [x0, y0, x1, y1]


def alignment_deviation(own_box, ref_box, align):
    """Displacement of a laid-out block from its reference axis, in px.

    Centred fields are judged on the centre and left-aligned ones on the left
    edge, because those are the axes a reader's eye actually registers.
    """
    if not own_box or not ref_box:
        return None
    if align == "center":
        return abs((own_box[0] + own_box[2]) / 2.0 - (ref_box[0] + ref_box[1]) / 2.0)
    return abs(own_box[0] - ref_box[0])


def alignment_violations(field_records):
    """Every field whose displacement exceeds its axis tolerance.

    THESE ARE REAL DISPLACEMENT, NOT A REPORTING ARTIFACT, and the record is
    explicit about why they cannot simply be tuned away: `line_x` clamps every
    line into its cleared run, so when the reference axis is not centred on that
    run the whole block is pushed to one edge and the deviation records the push.
    Moving the reference to the card's measured ink was tried and took violations
    20 -> 24. So the displacement is acknowledged by a human at the gate --
    `--accept-alignment` -- rather than silently tuned away.
    """
    findings = []
    for record in field_records or []:
        deviation = record.get("alignment_check_px")
        align = record.get("alignment")
        tolerance = ALIGNMENT_TOL_PX.get(align, ALIGNMENT_TOL_PX["left"])
        if deviation is not None and deviation > tolerance:
            findings.append({"file": record.get("file"),
                             "field": record.get("name"),
                             "alignment": align,
                             "deviation_px": deviation,
                             "tolerance_px": tolerance})
    return findings


# ---------------------------------------------------------------------------
# 10. T1-T5
# ---------------------------------------------------------------------------

def t1_containment(before, after, clear):
    """T1 -- no drawn pixel landed where the mask is not clear. Returns a count.

    Measured from the ARRAYS, before and after, rather than from the layout: the
    layout is what the typesetter INTENDED and the arrays are what it did. The two
    ornaments (the unique star and the flavour double rule) are the only places in
    the draw layer that can violate containment at all, and they are the reason
    this is measured rather than proved.
    """
    import numpy as np
    changed = (before != after).any(axis=2) if before.ndim == 3 else (before != after)
    return int((changed & ~clear).sum()), int(changed.sum())


def t2_field_regions(before, after, region_union):
    """T2 -- a field's ink stayed inside its own region union. Returns a count."""
    import numpy as np
    changed = (before != after).any(axis=2) if before.ndim == 3 else (before != after)
    return int((changed & ~region_union).sum())


def t3_inventory(expected, actual):
    """T3 -- the produced face set is EXACTLY the expected one, case-folded, BOTH
    directions.

    Case-folded because the volume is case-insensitive and a case-only difference
    would otherwise read as two files where the atlas builder will find one. Both
    directions because a stale file from a previous run is as fatal as a missing
    one: a partial tree is indistinguishable from a complete one to the stage that
    addresses these files by name.
    """
    want = {}
    for name in expected or ():
        want.setdefault(name.lower(), []).append(name)
    have = {}
    for name in actual or ():
        have.setdefault(name.lower(), []).append(name)
    findings = []
    for key in sorted(set(want) - set(have)):
        findings.append("expected but absent: %s" % ", ".join(sorted(want[key])))
    for key in sorted(set(have) - set(want)):
        findings.append("present but not expected: %s" % ", ".join(sorted(have[key])))
    for key in sorted(set(want) & set(have)):
        if len(have[key]) > 1:
            findings.append("case-only collision: %s" % ", ".join(sorted(have[key])))
    return findings


def t4_deep_shrink(shrunk):
    """T4 -- the tolerance-bearing rows: a body that fitted only below the ratio,
    or not at all. `deep` is set by `shrink_row`; a row with `size_used` None is a
    field that never fitted even at MIN_SIZE_ABS."""
    return [row for row in shrunk or [] if row.get("deep") or row.get("size_used") is None]


def t5_content_coverage(faces, known_tokens):
    """T5 -- (region present) == (Korean string present), and no unknown token.

    Evaluated BEFORE any pixel is drawn, and it is an INPUT-DRIFT class: it fires
    when the Korean text or the mask manifest moved under us, so §4.2 puts it at
    14 and `EXIT_PRECEDENCE` surfaces it ahead of every pixel gate. Naming a
    containment failure when the real fault is that a region lost its string sends
    the operator to the wrong file.
    """
    findings = []
    for face in faces or []:
        name = face.get("file") or face.get("arkham_id") or "?"
        regions = face.get("regions") or {}
        strings = face.get("strings") or {}
        for field in sorted(set(regions) | set(strings)):
            has_region = bool(regions.get(field))
            has_string = bool((strings.get(field) or "").strip())
            if has_region and not has_string:
                findings.append("%s/%s: a region with no Korean string" % (name, field))
            elif has_string and not has_region:
                findings.append("%s/%s: a Korean string with no region" % (name, field))
            if not has_string:
                continue
            icons, _refs, _tags = kx.scan(strings[field])
            for token in icons:
                if token not in known_tokens:
                    findings.append("%s/%s: [%s] is outside the PUA map"
                                    % (name, field, token))
    return findings


# ---------------------------------------------------------------------------
# 11. The human gate -- `typeset` is one of the four (§1.2, §5.7)
# ---------------------------------------------------------------------------
#
# THE TYPESET GATE IS ALSO THE PROSE REVIEW, and the stub says so per face.
# `check` counts markup tokens and audits 을/를 jongseong; both COUNT tokens, and
# neither reads meaning. The Midwinter record needed a whole separate task after
# the mechanical checks passed, and that task found `주요목적를` FIVE TIMES in the
# generator. So the stub enumerates, per face, prose items alongside the visual
# ones -- and the gate cannot be `accepted` with an unticked item, by the same
# rule that makes an unnamed gate an unperformed one.

GATE_HEADER = """---
status: {status}
reviewer: {reviewer}
date: {date}
gate_for: typeset
bound_sha256: {bound}
---

# typeset gate -- {slug}

T1-T5 are mechanical: they prove containment and coverage. THEY ARE BLIND TO
CORRECTNESS OF APPEARANCE AND TO MEANING. A run in which the flavour was drawn
into the rules band, the fallback stars came out undersized, and every composed
victory line said the wrong thing would still be `verdict: PASS, exit_code: 0`.
So `consumable` is false until a human sets `status: accepted` above.

Gallery: `{gallery}`  |  Report: `typeset.json`

Faces: {faces} typeset, {pinned} paragraph-pinned, {single} single-band,
{per_band} per-band. Deep-shrink rows: {deep}. Alignment violations: {align}.

## How to read this file

Every item below is a checkbox and **an unticked item is an unperformed review**.
The stage refuses to record this gate as `accepted` while any box is empty --
not as a formality, but because the two defect classes it lists are exactly the
two that every mechanical check in this tool is structurally blind to.

## 1. Allocation -- read the pinned faces FIRST

`allocation_mode` is an EXPLICIT declaration, which is the whole fix: a pinned
face used to declare `single-band` and was therefore invisible to this section's
own filter, which is how a mis-attributed body passed a gate on 2026-08-18.
`para_gaps: 0` on a pinned face is BY CONSTRUCTION -- a paragraph pinned to its
own band pays no gap -- and is never a defect.

{allocation_items}

## 2. Icons -- the swap a human cannot see

Two Arkham icons swapped for each other look like two Arkham icons, which is why
the map declares a measured `(ink_fill, advance_em)` pair per token and
`kz_checkers` re-measures it. Read the MEANINGS, not the shapes:

{icon_items}

## 3. Per-face review -- visual AND prose

The prose items are not a courtesy. `check` counts markup tokens and audits
을/를 jongseong; both count, neither reads. Read each field for MEANING, NOT FOR
CONTAINMENT -- containment is already proved above.

{face_items}

A prose defect found here is routed to `audit` (S7), whose whole job is to widen
a single finding into the predicate that catches its siblings IN THE GENERATOR.
"""

#: The per-face items. The four prose rows are §5.7 verbatim in intent: "name,
#: traits, rules text, flavour, each phrased 'read for meaning, not for
#: containment'".
FACE_PROSE_ITEMS = (
    ("name", "the NAME reads as the card it is -- read for meaning, not for containment"),
    ("traits", "the TRAITS read as the printed traits -- read for meaning, not for containment"),
    ("text", "the RULES TEXT says what the English says -- read for meaning, not for containment"),
    ("flavour", "the FLAVOUR reads as prose a player would enjoy -- read for meaning, not for containment"),
)

FACE_VISUAL_ITEMS = (
    ("band", "no field was drawn into another field's band"),
    ("size", "nothing is illegibly small, and no ornament is undersized"),
    ("axis", "each block sits on the axis the English sat on"),
)


def parse_gate(path):
    """The gate file's front-matter plus its checklist state.

    Returns the four front-matter fields and `unticked[]`. Reading the checkboxes
    is not decoration: §5.7 makes an unticked item a blocker, and a gate parser
    that read only `status:` would let a reviewer write `accepted` over an empty
    list -- which is exactly the "unnamed gate is an unperformed gate" failure
    wearing a checklist.
    """
    record = {"status": "pending", "reviewer": None, "date": None,
              "bound_sha256": None, "unticked": []}
    if not os.path.exists(path):
        return record
    in_front = False
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line == "---":
                in_front = not in_front
                continue
            if in_front:
                for key in ("status", "reviewer", "date", "bound_sha256"):
                    prefix = "%s:" % key
                    if line.startswith(prefix):
                        record[key] = line[len(prefix):].strip() or None
                continue
            if line.startswith("- [ ]"):
                record["unticked"].append(line[5:].strip())
    return record


def write_gate_stub(run_dir, slug, gate, counts, faces, icons, gallery):
    """The stub is GENERATED, never blank: an unnamed gate is an unperformed gate.

    Every face gets its own block with three visual items and four prose ones, so
    the checklist grows with the corpus rather than staying a fixed page that
    describes a corpus it has not seen.
    """
    allocation = []
    for mode in ALLOCATION_MODES:
        named = [f["file"] for f in faces
                 if ((f.get("body") or {}).get("allocation_mode")) == mode]
        allocation.append("- [ ] `%s` (%d): %s"
                          % (mode, len(named),
                             ", ".join("`%s`" % n for n in named[:12]) or "_(none)_"))
    icon_items = []
    for token in sorted(icons):
        entry = icons[token]
        icon_items.append(
            "- [ ] `[%s]` -> `%s`  (ink_fill %.3f, advance %.3f em) -- is this the "
            "icon the English card means?"
            % (token, entry.get("codepoint"), float(entry.get("ink_fill") or 0.0),
               float(entry.get("advance_em") or 0.0)))
    face_items = []
    for face in faces:
        name = face.get("file") or face.get("arkham_id")
        body = face.get("body") or {}
        face_items.append("### `%s` -- %s, %s" % (
            name, face.get("group") or "?",
            body.get("allocation_mode") or "no body"))
        face_items.append("")
        for _key, prompt in FACE_VISUAL_ITEMS:
            face_items.append("- [ ] %s" % prompt)
        for _key, prompt in FACE_PROSE_ITEMS:
            face_items.append("- [ ] %s" % prompt)
        face_items.append("")

    text = GATE_HEADER.format(
        status=gate.get("status") or "pending",
        reviewer=gate.get("reviewer") or "",
        date=gate.get("date") or "",
        bound=gate.get("bound_sha256") or "",
        slug=slug, gallery=gallery,
        faces=counts.get("faces_typeset", 0),
        pinned=counts.get("by_allocation", {}).get("paragraph-pinned", 0),
        single=counts.get("by_allocation", {}).get("single-band", 0),
        per_band=counts.get("by_allocation", {}).get("per-band", 0),
        deep=counts.get("deep_shrink", 0),
        align=counts.get("alignment_violations", 0),
        allocation_items="\n".join(allocation),
        icon_items="\n".join(icon_items) or "_(none)_",
        face_items="\n".join(face_items) or "_(none)_")
    path = os.path.join(run_dir, "gates", "typeset-gate.md")
    kc.atomic_write_text(path, text)
    return path


def apply_gate_items(gate, prior):
    """An `accepted` gate with unticked items is DEMOTED to pending, with the
    reason recorded.

    Not an exit code and deliberately not: the blocking is done by
    `compute_consumable`, exactly as a plain `pending` gate blocks. What this adds
    is that the demotion is VISIBLE -- `gate.unticked[]` in the report names the
    items -- so a reviewer who wrote `accepted` over a half-read checklist is told
    which half, instead of finding a `consumable: false` with no cause.
    """
    unticked = list(prior.get("unticked") or [])
    gate["unticked"] = unticked
    if gate.get("status") == "accepted" and unticked:
        gate["status"] = "pending"
        gate["blocked_by_unticked"] = len(unticked)
    else:
        gate["blocked_by_unticked"] = 0
    return gate


# ---------------------------------------------------------------------------
# 12. The review gallery
# ---------------------------------------------------------------------------

GALLERY_CSS = """
body{font:13px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;padding:20px;
background:#141414;color:#e8e8e8}
h1{font-size:20px} table{border-collapse:collapse;width:100%}
td,th{border:1px solid #333;padding:6px;vertical-align:top}
img{max-width:250px;height:auto;display:block;background:#222}
.chip{display:inline-block;padding:1px 6px;border-radius:9px;font-size:11px;
margin-right:4px;background:#2a4;color:#041}
.chip.bad{background:#a33;color:#fee} .chip.warn{background:#a83;color:#fee}
.ko{white-space:pre-wrap;font-size:12px;color:#cfe}
.meta{color:#999;font-size:11px}
"""


def _esc(value):
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_gallery_html(slug, faces, counts, deep_rows, align_rows):
    """English slice || cleared input || Korean result, per face.

    Three columns and not one, because the question a reviewer is answering is
    comparative: "does the Korean say and sit where the English said and sat".
    A gallery of results alone can only be checked against a memory of the plate.
    """
    deep_by_file = {}
    for row in deep_rows or []:
        deep_by_file.setdefault(row.get("file"), []).append(row)
    align_by_file = {}
    for row in align_rows or []:
        align_by_file.setdefault(row.get("file"), []).append(row)

    out = ["<!doctype html><meta charset='utf-8'>",
           "<title>typeset review -- %s</title>" % _esc(slug),
           "<style>%s</style>" % GALLERY_CSS,
           "<h1>typeset review -- %s</h1>" % _esc(slug),
           "<p class='meta'>%d faces; allocation %s; %d deep-shrink rows; "
           "%d alignment violations. T1-T5 are mechanical and blind to meaning "
           "-- read the strings.</p>"
           % (counts.get("faces_typeset", 0),
              _esc(counts.get("by_allocation")), len(deep_rows or []),
              len(align_rows or [])),
           "<table><tr><th>face</th><th>English slice</th><th>cleared</th>"
           "<th>Korean</th><th>strings and lines</th></tr>"]
    for face in faces:
        name = face.get("file") or face.get("arkham_id") or "?"
        body = face.get("body") or {}
        chips = ["<span class='chip'>%s</span>"
                 % _esc(body.get("allocation_mode") or "no body")]
        if deep_by_file.get(name):
            chips.append("<span class='chip bad'>T4 deep-shrink</span>")
        if align_by_file.get(name):
            chips.append("<span class='chip warn'>alignment</span>")
        if face.get("t1_outside"):
            chips.append("<span class='chip bad'>T1</span>")
        if face.get("t2_outside"):
            chips.append("<span class='chip bad'>T2</span>")
        strings = []
        for field in ALL_FIELDS:
            value = (face.get("strings") or {}).get(field)
            if not value:
                continue
            lines = (face.get("lines_ko") or {}).get(field) or []
            strings.append("<b>%s</b>: %s<br><span class='meta'>%s</span>"
                           % (_esc(field), _esc(value),
                              _esc(" / ".join(lines))))
        out.append(
            "<tr><td>%s<br>%s<br><span class='meta'>%s</span></td>"
            "<td>%s</td><td>%s</td><td>%s</td><td class='ko'>%s</td></tr>"
            % (_esc(name), "".join(chips), _esc(face.get("group") or ""),
               _img(face.get("slice_path")), _img(face.get("composite_path")),
               _img(face.get("typeset_path")), "<hr>".join(strings)))
    out.append("</table>")
    return "\n".join(out) + "\n"


def _img(path):
    if not path:
        return "<span class='meta'>(absent)</span>"
    return "<img src='%s' loading='lazy'>" % _esc(path)


# ---------------------------------------------------------------------------
# 13. The stage
# ---------------------------------------------------------------------------

def _read_json(path, label):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is missing" % label, path)
    with open(path, "r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            kc.refuse(kc.EXIT_PRECONDITION, "%s is not valid JSON" % label,
                      "%s: %s" % (path, exc))


#: `masks/manifest.json`'s own padding identity, restated from `kz_mask.PAD_X` /
#: `PAD_Y` rather than imported, because importing `kz_mask` here would make the
#: typesetter depend on the mask stage's module for two integers -- and `PAD_X`
#: already has a second name in this file (`A6_PAD_X`) for the alignment clamp
#: that leans on the same identity. Asserted equal in `--selftest`, so a drift is
#: caught rather than merely commented about.
MASK_PAD_X = 12
MASK_PAD_Y = 7


def en_optics_from_rects(rects):
    """The English ink a set of line rectangles was measured from.

    `masks/manifest.json`'s method is an IDENTITY, not an estimate -- "every line
    becomes a rectangle padded by 12px HORIZONTALLY and 7px vertically" -- so the
    inverse is exact, and it is what lets this stage size Korean type against the
    English optics without re-measuring pixels the mask stage already measured.
    The ink height is the MEDIAN over the lines rather than the maximum, for the
    same reason `kz_checkers.text_bands` filters by band height: one rect that
    swallowed a rule or a plate edge would otherwise set the type for the whole
    field.
    """
    if not rects:
        return {}
    heights = sorted(max(1, int(r[3]) - int(r[1]) + 1 - 2 * MASK_PAD_Y)
                     for r in rects)
    median = heights[len(heights) // 2]
    return {"en_ink_h": median,
            "en_ink_x0": min(int(r[0]) for r in rects) + MASK_PAD_X,
            "en_ink_x1": max(int(r[2]) for r in rects) - MASK_PAD_X,
            "en_lines": len(rects)}


def read_faces(run_dir):
    """The face set, JOINED from the two predecessors that each own half of it.

    `composite.json`'s `results.faces[]` is the authoritative face SET -- which
    faces exist, which cleared bytes they carry, and which of them the composite
    stage refused. `masks/manifest.json`'s `entries[]` is the GEOMETRY -- the
    per-field rectangles, the slice file each face came from -- and its
    `per_type{}` block carries the group's `rot` and its layout `windows`.

    Neither half is derivable from the other and neither is optional here, which
    is why `kz_config.PREDECESSORS["typeset"]` has two edges. The rectangles are
    read in SLICE COORDINATES (`regions_slice_coords`), because the mask PNG this
    stage derives `clear` from is stored in the slice frame -- `masks/manifest`'s
    own `orientation` field says so ("stored rotated 90 CW; regions measured
    upright"), and handing upright rects to a rot-90 group's slice would silently
    address the wrong axis, exactly the frame mistake §5.5 warns W1/W2 about.

    A missing half is exit 13 NAMING IT, never an empty face set: a stage that
    typesets zero faces and reports PASS is the failure mode this tool exists to
    make impossible.
    """
    report = _read_json(os.path.join(run_dir, "composite.json"), "composite.json")
    faces = ((report.get("results") or {}).get("faces")
             if isinstance(report.get("results"), dict) else None)
    if not faces:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "composite.json carries no results.faces[]",
                  "typeset reads the cleared face set from its predecessor; an "
                  "absent set is a precondition, never an empty run")

    manifest = _read_json(os.path.join(run_dir, "masks", "manifest.json"),
                          "masks/manifest.json")
    entries = {}
    for entry in manifest.get("entries") or manifest.get("masks") or []:
        if entry.get("file"):
            entries[entry["file"]] = entry
    if not entries:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "masks/manifest.json carries no entries[]",
                  "the per-field rectangles are the geometry this stage sets type "
                  "into; composite.json carries the face set and not the layout")
    per_type = manifest.get("per_type") or {}

    joined = []
    for face in faces:
        name = face.get("file")
        if not name:
            kc.refuse(kc.EXIT_PRECONDITION, "composite.json carries a face with "
                      "no file", repr(face))
        entry = entries.get(name)
        if entry is None:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "masks/manifest.json has no entry for %s" % name,
                      "every composited face must carry the rectangles it is to "
                      "be typeset into")
        regions = entry.get("regions_slice_coords") or entry.get("regions")
        if not regions:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "masks/manifest.json entry %s declares no regions" % name)
        group = entry.get("group") or face.get("group")
        spec = per_type.get(group) or {}
        windows = spec.get("windows") or {}
        measured = {}
        for field, rects in sorted(regions.items()):
            optics = en_optics_from_rects(rects)
            optics["window"] = windows.get(field) or windows.get("body")
            measured[field] = optics
        record = dict(face)
        record.update({
            "file": name,
            "group": group,
            "type_name": str(group or "").split("/")[0],
            "rot": spec.get("rot", 0),
            "slice_file": entry.get("slice_file"),
            "regions": dict((k, [list(r) for r in v]) for k, v in regions.items()),
            "measured": measured,
            "mask_path": os.path.join("masks", name),
            "composite_path": os.path.join("cleared", name),
            "slice_path": (os.path.join("slices", entry["slice_file"])
                           if entry.get("slice_file") else None),
            "typeset_path": os.path.join("typeset", name),
        })
        joined.append(record)
    return report, joined


def resolve_faces_fonts(cfg, workspace=None):
    """Resolve every role this stage will actually set type in. Exit 13 on any.

    `init` records an ABSENT role rather than refusing, and its docstring says why:
    "The v1 stages that actually consume a face assert it themselves at the point
    of use, which is where an absent font is genuinely a precondition failure."
    This is that point of use, and `icons` is not optional here -- the anti-swap
    invariant re-measures from the outlines, so a run without the icon face cannot
    evaluate its own central check and must not pretend to.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    env = kz.read_env_file()
    fonts = cfg.get("fonts") or {}
    roots = fonts.get("search_roots") or []
    resolved = {}
    for role in sorted(set(ROLE_FONT.values()) | {"icons"}):
        spec = fonts.get(role)
        if not isinstance(spec, dict):
            kc.refuse(kc.EXIT_PRECONDITION,
                      "scenario.json declares no fonts.%s" % role,
                      "typeset sets type in every role of ROLE_FONT plus `icons`")
        resolved[role] = kz.resolve_font(role, spec, env=env, search_roots=roots,
                                         workspace=workspace)
    return resolved


def _material_for(cfg, run_dir, set_path, workspace=None):
    """Everything the agent is given, COPIED. It holds no repository path."""
    workspace = workspace or kc.WORKSPACE_ROOT
    material = {}
    for name in ("card-text-ko.json", "card-source-en.json", "card-text-en.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            material[name] = path
    core = os.path.join(workspace, cfg["guard"]["data_root"], "icons",
                        "%s.json" % CORE_ICON_SET)
    if os.path.exists(core):
        material["icons-reference.json"] = core
    if os.path.exists(set_path):
        material["icons-prior.json"] = set_path
    coverage = os.path.join(run_dir, "font-coverage.json")
    if os.path.exists(coverage):
        material["font-coverage.json"] = coverage
    return material


def typeset_faces(faces, ko_index, icon_map, metrics, clear_reader=None,
                  layouts_out=None):
    """Lay out every face. Returns (records[], shrunk[], field_records[], t4[]).

    `layouts_out`, when given, collects `{file: [(label, layout), ...]}` for the
    draw layer. It is an OUT-PARAMETER and not part of the return tuple because a
    layout carries tokenised runs and band tuples and is therefore not
    JSON-serialisable -- keeping it out of `records[]` is what keeps the stage
    report writable, and the separation is load-bearing rather than stylistic.

    `clear_reader(face) -> (clear, region_union)` is injected so the layout core is
    exercisable with synthetic arrays -- the same rule §5.5 applies to W1/W2, and
    the same reason: the predicate that matters must be testable on a machine with
    no corpus. When it is None the faces are laid out from whatever `clear` each
    face record already carries, which is what `--selftest` supplies.
    """
    records, shrunk, field_records, t4 = [], [], [], []
    for face in faces:
        clear, region_union = (clear_reader(face) if clear_reader
                               else (face.get("clear"), face.get("region_union")))
        cs = clear_cumsum(clear)
        strings = face.get("strings") or {}
        measured = face.get("measured") or {}
        record = dict(face)
        record.pop("clear", None)
        record.pop("region_union", None)
        record["fields"] = []
        record["lines_ko"] = {}
        face_layouts = []
        body_layout = None

        body_present = [f for f in BODY_FIELDS
                        if face["regions"].get(f) and (strings.get(f) or "").strip()]
        singles = [f for f in ALL_FIELDS if f not in BODY_FIELDS
                   and face["regions"].get(f) and (strings.get(f) or "").strip()]

        for field in singles + (["_body"] if body_present else []):
            if field == "_body":
                rects = []
                for name in body_present:
                    rects.extend(face["regions"][name])
                groups = [build_group(name, strings[name], face["regions"][name],
                                      face["group"], icon_map,
                                      ref=_ref_of(measured.get(name)),
                                      window=(measured.get(name) or {}).get("window"))
                          for name in body_present]
                floor = min(MIN_SIZE_ABS[n] for n in body_present)
                en_ink_h = max((measured.get(n) or {}).get("en_ink_h") or 0
                               for n in body_present)
                label = "+".join(body_present)
                role = ROLE_FONT[body_present[0]]
            else:
                rects = face["regions"][field]
                groups = [build_group(field, strings[field], rects, face["group"],
                                      icon_map,
                                      ref=_ref_of(measured.get(field)),
                                      window=(measured.get(field) or {}).get("window"))]
                floor = MIN_SIZE_ABS[field]
                en_ink_h = (measured.get(field) or {}).get("en_ink_h") or 0
                label = field
                role = ROLE_FONT[field]

            bands = bands_for(clear, rect_union(rects))
            natural = max(floor, metrics.natural_size(role, en_ink_h or floor))
            if not bands:
                t4.append({"file": face["file"], "field": label,
                           "reason": "no cleared band",
                           "extra_clear_px_needed": 0})
                continue
            try:
                layout = fit_block(groups, bands, cs, natural, floor, metrics)
            except Overflow as exc:
                # T4: the ladder bottomed out. THAT FIELD IS NOT DRAWN, so its
                # T1/T2 are null rather than a fabricated pass -- drawing a face
                # whose fit failed and then reporting its containment would report
                # a REAL violation for a face the contract says was never made.
                t4.append({"file": face["file"], "field": label,
                           "reason": exc.detail,
                           "extra_clear_px_needed": exc.extra_px})
                shrunk.append({"file": face["file"],
                               "arkham_id": face.get("arkham_id"),
                               "side": face.get("side"), "field": label,
                               "size_natural": natural, "size_used": None,
                               "delta_px": None, "delta_pct": None,
                               "lines_ko": 0, "allocation_mode": None,
                               "heavy": True, "deep": True})
                continue

            face_layouts.append((label, layout))
            if layout["size"] < natural:
                shrunk.append(shrink_row(face, label, layout, natural))
            for group_index, group in enumerate(groups):
                own_box = group_ink_box(layout, group_index)
                deviation = alignment_deviation(own_box, group["ref_box"],
                                                group["align"])
                lines = [" ".join(word_text(w) for w in l["words"])
                         for l in layout["lines"]
                         if l["kind"] == "text" and l["group"] == group_index]
                record["lines_ko"][group["field"]] = lines
                field_record = {
                    "file": face["file"], "name": group["field"],
                    "role": group["role"], "align_src": group["ref_src"],
                    "alignment": group["align"],
                    "alignment_check_px": (round(deviation, 1)
                                           if deviation is not None else None),
                    "size_natural": natural, "size_used": layout["size"],
                    "shrunk": layout["size"] < natural,
                    "deep": layout["size"] < int(round(MIN_SIZE_RATIO * natural)),
                    "lines": len(lines), "ink_box": own_box,
                    "ref_box": group["ref_box"],
                }
                record["fields"].append(field_record)
                field_records.append(field_record)
            if field == "_body":
                body_layout = {
                    "allocation_mode": layout["allocation_mode"],
                    "paragraph_bands": layout["paragraph_bands"],
                    "para_gaps": layout["gaps"],
                    # §5.6 item 4, recorded rather than inferred: on a pinned face
                    # zero gaps is a PROPERTY of the pin, not an absence of one.
                    "para_gaps_by_construction":
                        layout["allocation_mode"] == "paragraph-pinned",
                    "bands_used": layout["bands_used"],
                    "declared_order": layout["declared_order"],
                    "size": layout["size"], "trials": layout["trials"],
                    "height_needed": layout["height_needed"],
                    "height_available": layout["height_available"],
                }
        record["body"] = body_layout
        if layouts_out is not None:
            layouts_out[record["file"]] = face_layouts
        records.append(record)
    return records, shrunk, field_records, t4


def _ref_of(measured):
    if not measured:
        return None
    return (measured.get("en_ink_x0"), measured.get("en_ink_x1"))


# ---------------------------------------------------------------------------
# 13b. The draw layer -- clipped by construction, then MEASURED anyway
# ---------------------------------------------------------------------------

def draw_face(run_dir, record, layouts, metrics, ink=(0, 0, 0), write=True):
    """Draw one face's layouts onto its cleared image, then measure T1 and T2.

    CLIPPED BY CONSTRUCTION AND MEASURED ANYWAY, and the redundancy is the point.
    Everything is rendered onto a transparent overlay whose alpha is then ANDed
    with `allow = clear & region_union`, so text cannot land outside CLEAR even if
    the layout asked it to -- that is what makes T2 a guarantee rather than an
    afterthought, exactly as the all-clear run test makes T1 one. T1 and T2 are
    then re-derived from the arrays BEFORE and AFTER the composite, because the
    layout is what the typesetter intended and the arrays are what it did, and a
    proof that reads only its own intent proves nothing.

    `clipped_px` is recorded separately and is the number that survives the clip:
    a nonzero value means the layout WANTED to put ink outside its allowance. T1
    and T2 cannot see it -- the clip already removed it -- so it is reported on its
    own rather than folded into either.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    src = os.path.join(run_dir, record["composite_path"])
    if not os.path.exists(src):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "cleared face for %s is missing" % record["file"], src)
    base = Image.open(src).convert("RGB")
    before = np.asarray(base).copy()
    clear, union = _clear_for(run_dir, record)
    allow = clear & union

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(overlay)
    for _label, layout in layouts:
        for line in layout["lines"]:
            baseline = line["y"] - line["top_off"]
            if line["kind"] == "hr":
                # The flavour rule is a pair of VERTICAL strokes, `||` not `=`,
                # and the containment test spanned every row it touches (lay_out
                # measured RULE_STROKE_GAP + RULE_STROKE_W rows) -- checking a
                # third of the line box once cleared the first bar and left the
                # second unverified.
                x0, _x1 = line["run"]
                for offset in (0, RULE_STROKE_GAP):
                    pen.rectangle([x0 + offset, line["y"],
                                   x0 + offset + RULE_STROKE_W - 1,
                                   line["y"] + line["ink_h"] - 1],
                                  fill=tuple(ink) + (255,))
                continue
            cursor = float(line_x(line))
            space = metrics.advance("body", " ", line["size"])
            for index, word in enumerate(line["words"]):
                if index:
                    cursor += space
                for role, text in word:
                    pen.text((int(round(cursor)), baseline), text,
                             font=metrics._font(role, line["size"]),
                             fill=tuple(ink) + (255,), anchor="ls")
                    cursor += metrics.advance(role, text, line["size"])

    alpha = np.asarray(overlay)[:, :, 3] > 0
    clipped = int((alpha & ~allow).sum())
    kept = Image.fromarray(
        np.dstack([np.asarray(overlay)[:, :, :3],
                   (np.asarray(overlay)[:, :, 3] * allow).astype("uint8")]),
        "RGBA")
    base = Image.alpha_composite(base.convert("RGBA"), kept).convert("RGB")
    after = np.asarray(base)

    outside, changed = t1_containment(before, after, clear)
    record["t1_outside"] = outside
    record["t2_outside"] = t2_field_regions(before, after, union)
    record["changed_pixels"] = changed
    record["clipped_px"] = clipped
    record["ink_rgb"] = list(ink)

    if write:
        dest = os.path.join(run_dir, record["typeset_path"])
        parent = os.path.dirname(dest)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        base.save(dest, "PNG")
        record["typeset_sha256"] = kc.sha256_file(dest)
    else:
        record["typeset_sha256"] = None
    return record


def run_typeset(run_dir, mode="build", accept_deep_shrink=False,
                accept_alignment=False, replay=None, ask_dir=None,
                claude_bin=None, workspace=None, readonly_trees=(), quiet=False,
                write_data=True):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. <run_dir>/composite.json carries results.faces[]           -> exit 13
      4. <run_dir>/check.json exists                                -> exit 13
      5. <run_dir>/card-text-ko.json is readable JSON               -> exit 13
      6. the extracted icon universe is non-empty                   -> exit 13
      7. every font role this stage sets type in resolves           -> exit 13
      8. the ask bundle's policy, prompt and schema are on disk     -> exit 13
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)
    set_name = icon_set_name(cfg)

    composite_report, faces = read_faces(run_dir)
    check_path = os.path.join(run_dir, "check.json")
    if not os.path.exists(check_path):
        kc.refuse(kc.EXIT_PRECONDITION, "check.json is missing", check_path)

    ko_path = os.path.join(run_dir, "card-text-ko.json")
    ko_doc = _read_json(ko_path, "card-text-ko.json")
    en_path = os.path.join(run_dir, "card-source-en.json")
    en_doc = _read_json(en_path, "card-source-en.json") if os.path.exists(en_path) else None

    universe, units = extract_icons(ko_doc, en_doc)
    fonts = resolve_faces_fonts(cfg, workspace=workspace)

    set_path = os.path.join(workspace, cfg["guard"]["data_root"], "icons",
                            "%s.json" % set_name)
    material = _material_for(cfg, run_dir, set_path, workspace=workspace)
    if ask_dir is None:
        ask_dir = ka.build_bundle(cfg, SID, universe, units, material=material,
                                  prompt=PROMPT_PATH, workspace=workspace)
    if replay:
        ka.seed_from_replay(ask_dir, replay)
    ask = ka.run_bundle(ask_dir, invoke=not replay, claude_bin=claude_bin,
                        readonly_trees=readonly_trees, workspace=workspace,
                        quiet=quiet)

    triggered, checks = [], []
    decide_report, rulings = None, []
    if ask["exit_code"] != kc.EXIT_OK:
        triggered.append(ask["exit_code"])
        checks.append({"id": "AI0", "name": "batches_complete", "status": "fail",
                       "exit_on_fail": ask["exit_code"],
                       "detail": ["%s; %d of %d batches complete, resume at %s"
                                  % (ask["stopped_by"] or "incomplete",
                                     sum(1 for b in ask["batches"] if b["complete"]),
                                     ask["batch_of"], ask["resume_from"])]})
    else:
        checks.append({"id": "AI0", "name": "batches_complete", "status": "pass",
                       "exit_on_fail": kc.EXIT_AI_BUDGET, "detail": []})
        decide_report, decide_code = kd.decide(ask_dir)
        if decide_code != kc.EXIT_OK:
            triggered.append(decide_code)
        checks.append({"id": "AI1", "name": "decide",
                       "status": "pass" if decide_code == kc.EXIT_OK else "fail",
                       "exit_on_fail": decide_code or kc.EXIT_POLICY,
                       "detail": ([] if decide_code == kc.EXIT_OK
                                  else [decide_report["reason"]])})
        merged, err = kd._read_json(os.path.join(ask_dir, "manifest.merged.json"))
        if not err:
            rulings = merged.get("rulings") or []

    # ---- the icon map, and the anti-swap invariant (exit 67)
    declared, codepoints = declared_map(rulings)
    measured = measure_map(fonts["icons"]["path"], codepoints)
    icon_checks = icon_map_checks(declared, measured)
    checks.extend(icon_checks)
    for check in icon_checks:
        if check["status"] == "fail":
            triggered.append(check["exit_on_fail"])

    icon_map = {}
    for token, codepoint in sorted(codepoints.items()):
        try:
            icon_map[token] = codepoint_char(codepoint)
        except MarkupError as exc:
            checks.append({"id": "X0", "name": "icon_codepoints", "status": "fail",
                           "exit_on_fail": kc.EXIT_ARTIFACT, "detail": [str(exc)]})
            triggered.append(kc.EXIT_ARTIFACT)

    # ---- T5, BEFORE any pixel work (exit 14)
    ko_index = {}
    for card in (ko_doc.get("cards") if isinstance(ko_doc, dict) else ko_doc) or []:
        ko_index[str(card.get("arkham_id") or card.get("code") or "")] = card
    for face in faces:
        face.setdefault("strings", _strings_for(face, ko_index))
    t5 = t5_content_coverage(faces, set(icon_map))
    checks.append({"id": "T5", "name": "content_coverage",
                   "status": "fail" if t5 else "pass",
                   "exit_on_fail": kc.EXIT_DRIFT, "detail": t5})
    if t5:
        triggered.append(kc.EXIT_DRIFT)

    # ---- layout (T4 / the shrink ladder), then draw (T1/T2), then T3
    metrics = FontMetrics({role: spec["path"] for role, spec in fonts.items()})
    records, shrunk, field_records, t4_rows = ([], [], [], [])
    layouts_by_file = {}
    if not t5:
        records, shrunk, field_records, t4_rows = typeset_faces(
            faces, ko_index, icon_map, metrics,
            clear_reader=lambda face: _clear_for(run_dir, face),
            layouts_out=layouts_by_file)
        # THE DRAW. A dry run performs the ENTIRE fit and the entire measurement
        # and writes nothing: "--dry-run runs the ENTIRE fit including T4/T5,
        # writes NOTHING under typeset/". A verify-only run re-derives T1/T2 from
        # what is already there and likewise writes no pixels.
        for record in records:
            layouts = layouts_by_file.get(record["file"]) or []
            if not layouts:
                # Every field was a T4. THAT FACE IS NEVER DRAWN, so its T1/T2
                # are null rather than a fabricated pass -- drawing a face whose
                # fit failed and then reporting its containment would report a
                # REAL violation for a face the contract says was never made.
                record["t1_outside"] = record["t2_outside"] = None
                record["typeset_path"] = None
                continue
            draw_face(run_dir, record, layouts, metrics,
                      ink=tuple(record.get("ink_rgb") or (0, 0, 0)),
                      write=(mode == "build"))

    deep_rows = t4_deep_shrink(shrunk) + t4_rows
    checks.append({"id": "T4", "name": "deep_shrink",
                   "status": "pass" if not deep_rows else "fail",
                   "tolerance": "deep-shrink",
                   "exit_on_fail": kc.EXIT_TOLERANCE,
                   "detail": ["%s/%s: %s" % (r.get("file"), r.get("field"),
                                             r.get("reason") or "below the ratio")
                              for r in deep_rows]})
    if deep_rows and not accept_deep_shrink:
        triggered.append(kc.EXIT_TOLERANCE)

    align_rows = alignment_violations(field_records)
    checks.append({"id": "A1", "name": "alignment",
                   "status": "pass" if not align_rows else "fail",
                   "tolerance": "alignment",
                   "exit_on_fail": kc.EXIT_TOLERANCE,
                   "detail": ["%s/%s: %s px off its %s axis (tolerance %s)"
                              % (r["file"], r["field"], r["deviation_px"],
                                 r["alignment"], r["tolerance_px"])
                              for r in align_rows]})
    if align_rows and not accept_alignment:
        triggered.append(kc.EXIT_TOLERANCE)

    allocation_findings = check_allocation_declared(records)
    checks.append({"id": "AL", "name": "allocation_declared",
                   "status": "fail" if allocation_findings else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": allocation_findings})
    if allocation_findings:
        triggered.append(kc.EXIT_ARTIFACT)

    # The clip is what makes T1/T2 guarantees, so it is reported on its OWN.
    # A nonzero value means the layout wanted to put ink outside its allowance and
    # the clip removed it -- which T1 and T2 structurally cannot see, because by
    # the time they measure, the ink is gone. A `warn` and not a failure: the
    # pixels are correct, and what is wrong is upstream of them.
    clipped = [r for r in records if r.get("clipped_px")]
    checks.append({"id": "C0", "name": "clipped_ink",
                   "status": "warn" if clipped else "pass",
                   "detail": ["%s: %d px of ink were clipped away by CLEAR"
                              % (r["file"], r["clipped_px"]) for r in clipped]})

    out_dir = os.path.join(run_dir, "typeset")
    t1_total = sum(r.get("t1_outside") or 0 for r in records)
    t2_total = sum(r.get("t2_outside") or 0 for r in records)
    checks.append({"id": "T1", "name": "clear_containment",
                   "status": "fail" if t1_total else "pass",
                   "exit_on_fail": kc.EXIT_RULE_A,
                   "detail": ["%s: %d px outside CLEAR" % (r["file"], r["t1_outside"])
                              for r in records if r.get("t1_outside")]})
    if t1_total:
        triggered.append(kc.EXIT_RULE_A)
    checks.append({"id": "T2", "name": "region_containment",
                   "status": "fail" if t2_total else "pass",
                   "exit_on_fail": kc.EXIT_RULE_B,
                   "detail": ["%s: %d px outside its regions"
                              % (r["file"], r["t2_outside"])
                              for r in records if r.get("t2_outside")]})
    if t2_total:
        triggered.append(kc.EXIT_RULE_B)

    expected = [r["file"] for r in records if r.get("typeset_path")]
    actual = (sorted(n for n in os.listdir(out_dir) if n.lower().endswith(".png"))
              if os.path.isdir(out_dir) else [])
    t3 = t3_inventory(expected, actual) if mode == "build" else []
    checks.append({"id": "T3", "name": "inventory",
                   "status": "fail" if t3 else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT, "detail": t3})
    if t3:
        triggered.append(kc.EXIT_ARTIFACT)

    by_allocation = {}
    for record in records:
        mode_name = (record.get("body") or {}).get("allocation_mode")
        if mode_name:
            by_allocation[mode_name] = by_allocation.get(mode_name, 0) + 1
    counts = {
        "faces": len(faces),
        "faces_typeset": len(records),
        "icons": len(universe),
        "icons_ruled": len(declared),
        "shrunk": len(shrunk),
        "deep_shrink": len(deep_rows),
        "alignment_violations": len(align_rows),
        "by_allocation": dict(sorted(by_allocation.items())),
        "batches": ask["batch_of"],
        "batches_complete": sum(1 for b in ask["batches"] if b["complete"]),
    }

    icon_set = build_icon_set(set_name, cfg["slug"], units, rulings)
    bound = kc.sha256_bytes(kc.json_bytes(icon_set))
    gallery_name = "typeset-review-gallery.html"
    gallery = build_gallery_html(cfg["slug"], records, counts, deep_rows, align_rows)

    gate_path = os.path.join(run_dir, "gates", "typeset-gate.md")
    prior_gate = parse_gate(gate_path)
    prev_report, _err = kd._read_json(os.path.join(run_dir, "typeset.json"))
    prev_review = ((prev_report or {}).get("gate") or {})
    sha_moved = bool(prior_gate.get("bound_sha256")
                     and prior_gate["bound_sha256"] != bound)
    gate = kc.carry_review(prior_gate, prev_review, sha_moved, kc.utc_now())
    gate["gate_for"] = STAGE
    gate["bound_sha256"] = bound
    gate.pop("unticked", None)
    gate = apply_gate_items(gate, prior_gate)

    written = []
    if write_data and mode == "build" and declared and not triggered:
        # THE MIRROR WRITER is the only code in the tool that opens a path under
        # SCED-tools/ (§3.1). This module declares no write root outside
        # <run_dir> and cannot reach one. The write is gated on the adjudication:
        # a run whose rules failed must not leave an icon set behind, or the NEXT
        # run's `icons-prior.json` material is something nothing ever approved.
        written.append(kz.write_data(cfg, "icons", "%s.json" % set_name, icon_set,
                                     workspace=workspace))

    if mode == "build":
        kc.atomic_write_text(os.path.join(run_dir, gallery_name), gallery)
        write_gate_stub(run_dir, cfg["slug"], gate, counts, records,
                        icon_set["icons"], gallery_name)

    ai_block = {
        "used": True, "stage_id": SID, "batches": ask["batch_of"],
        "session_ids": (decide_report or {}).get("session_ids")
                       or [b["session_id"] for b in ask["batches"]],
        "envelope_sha256": (decide_report or {}).get("envelope_sha256") or [],
        "merged_manifest_sha256": (decide_report or {}).get("merged_manifest_sha256"),
        "decide_sha256": (decide_report or {}).get("decide_sha256"),
        "decide_outcome": (decide_report or {}).get("outcome") or "incomplete",
        "cost_usd": ask["cost_usd"],
        "model": (cfg.get("ai") or {}).get("model"),
        "replay_of": replay,
        "ask_dir": os.path.relpath(ask_dir, workspace),
    }

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path, ko_path,
                                  os.path.join(run_dir, "composite.json"),
                                  check_path]),
        freshness=kc.build_freshness([ko_path],
                                     upstream_report_path=os.path.join(
                                         run_dir, "composite.json")),
        ai=ai_block, gate=gate, tool=_tool_block(),
        accepted={"deep-shrink": bool(accept_deep_shrink),
                  "alignment": bool(accept_alignment)},
        results={"faces": records, "shrunk": shrunk, "deep_shrink": deep_rows,
                 "alignment": align_rows, "icons": icon_set["icons"]})
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    if written:
        report["write_set"] = [{"path": os.path.relpath(p, workspace),
                                "action": "create"} for p in written]
    return report, ask_dir


def _strings_for(face, ko_index):
    """The Korean strings this face's regions are to be filled with."""
    card = ko_index.get(str(face.get("arkham_id") or "")) or {}
    out = {}
    for field in ALL_FIELDS:
        value = card.get(field)
        if value:
            out[field] = value
    return out


def _clear_for(run_dir, face):
    """(clear, region_union) for one face, from the mask PNG the manifest names.

    `clear = (mask alpha == 0)` is the ONLY authority on where ink may land -- it
    already subsumes the region rectangles, the protected list and the edge guard.
    """
    import numpy as np
    from PIL import Image
    mask_path = os.path.join(run_dir, face["mask_path"])
    if not os.path.exists(mask_path):
        kc.refuse(kc.EXIT_PRECONDITION, "mask for %s is missing" % face["file"],
                  mask_path)
    mask = np.asarray(Image.open(mask_path).convert("RGBA"))
    clear = mask[:, :, 3] == 0
    union = np.zeros(clear.shape, dtype=bool)
    for rects in (face.get("regions") or {}).values():
        for x0, y0, x1, y1 in rects:
            union[int(y0):int(y1) + 1, int(x0):int(x1) + 1] = True
    return clear, union


def _tool_block():
    """The art tier DECLARES its PIL and numpy versions; the stdlib tier cannot.

    `golden` compares `tool{}` by report and treats a null as "this tier declares
    no opinion" (§5.8), so filling these is what makes the fixture's interpreter
    comparison meaningful for this module at all.
    """
    pil = numpy = None
    try:
        import PIL
        pil = PIL.__version__
    except Exception:                                    # pragma: no cover
        pass
    try:
        import numpy as _np
        numpy = _np.__version__
    except Exception:                                    # pragma: no cover
        pass
    return kc.tool_block(pil=pil, numpy=numpy)


# ---------------------------------------------------------------------------
# 14. --selftest
# ---------------------------------------------------------------------------
#
# ALL SYNTHETIC, NO CORPUS. Every case builds its own boolean `clear` array and
# uses `SyntheticMetrics`, so the two named TOLERANCES faults and the anti-swap
# fault are exercisable on a machine with no fonts, no images and no scenario --
# which is the same rule §5.5 imposes on W1/W2 and for the same reason: the art
# chain's most valuable predicates must survive an interpreter change and a
# machine move.

def synth_clear(size, runs):
    """A boolean clear array with `runs` = [(y0, y1, x0, x1), ...] cleared."""
    import numpy as np
    clear = np.zeros((size[1], size[0]), dtype=bool)
    for y0, y1, x0, x1 in runs:
        clear[y0:y1 + 1, x0:x1 + 1] = True
    return clear


def _synth_face(name, runs, strings, size=(400, 300), regions=None, measured=None):
    clear = synth_clear(size, runs)
    import numpy as np
    union = np.zeros((size[1], size[0]), dtype=bool)
    regions = regions or {"text": [[0, 0, size[0] - 1, size[1] - 1]]}
    for rects in regions.values():
        for x0, y0, x1, y1 in rects:
            union[y0:y1 + 1, x0:x1 + 1] = True
    return {"file": name, "arkham_id": name.split(".")[0], "side": "front",
            "group": "Asset/front", "regions": regions, "strings": strings,
            "measured": measured or {}, "clear": clear, "region_union": union}


def selftest(fault=None, verbose=True):
    """Prove each refusal fires on the fault it targets AND stays quiet on its
    negative. Returns a list of findings; empty means every case held."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))
    metrics = SyntheticMetrics()
    icons = {"skull": ""}

    # ------------------------------------------------------------------ T4
    if "deep-shrink" in wanted:
        # THE NAMED FAULT: "a synthetic face whose body cannot fit above
        # MIN_SIZE_ABS". One 8 px band and a string that needs a whole line, so
        # the ladder walks all the way to the floor and still overflows.
        narrow = _synth_face("deep.png", [(10, 17, 10, 60)],
                             {"text": "가나다라마바사아자차카타파하"},
                             regions={"text": [[10, 10, 60, 17]]},
                             measured={"text": {"en_ink_h": 40}})
        _records, shrunk, _fields, t4 = typeset_faces([narrow], {}, icons, metrics)
        if not t4:
            findings.append("deep-shrink: a face whose body cannot fit above "
                            "MIN_SIZE_ABS produced no T4 row")
        if not t4_deep_shrink(shrunk):
            findings.append("deep-shrink: the shrink list carries no deep row for "
                            "a field that never fitted")
        # ... and the negative: a wide band fits at the natural size and is NOT a
        # deep row. Shrinking is designed behaviour, reported, never a gate.
        roomy = _synth_face("ok.png", [(10, 200, 10, 390)],
                            {"text": "가나다라"},
                            regions={"text": [[10, 10, 390, 200]]},
                            measured={"text": {"en_ink_h": 20}})
        records, shrunk2, _f, t4b = typeset_faces([roomy], {}, icons, metrics)
        if t4b or t4_deep_shrink(shrunk2):
            findings.append("deep-shrink: a face with ample cleared band was "
                            "reported as deep (%r / %r)" % (t4b, shrunk2))
        if not records or not records[0]["fields"]:
            findings.append("deep-shrink: the negative face produced no fields")
        # The tolerance row itself must exist, name this module, and carry 22.
        row = kc.row_by_flag("--accept-deep-shrink")
        if row is None or row.module != "kz_typeset.py" or row.exit_code != kc.EXIT_TOLERANCE:
            findings.append("deep-shrink: the TOLERANCES row is %r" % (row,))

    # ------------------------------------------------------------ alignment
    if "alignment" in wanted:
        # THE NAMED FAULT: "a face whose reference axis is off its cleared run".
        # The cleared run sits far right of the reference box, so `line_x` clamps
        # the block into the run and the whole block is pushed off its axis --
        # REAL displacement, which is why it is acknowledged rather than tuned.
        off = _synth_face("off.png", [(10, 120, 240, 390)],
                          {"text": "가나다"},
                          regions={"text": [[10, 10, 390, 120]]},
                          measured={"text": {"en_ink_h": 20,
                                             "en_ink_x0": 20, "en_ink_x1": 120}})
        _r, _s, fields, _t4 = typeset_faces([off], {}, icons, metrics)
        violations = alignment_violations(fields)
        if not violations:
            findings.append("alignment: a block clamped %s px off its reference "
                            "axis produced no violation (%r)"
                            % ("far", [f.get("alignment_check_px") for f in fields]))
        # ... and the negative: the run straddles the reference, so the block sits
        # where the English sat and nothing is reported.
        on = _synth_face("on.png", [(10, 120, 10, 390)],
                         {"text": "가나다"},
                         regions={"text": [[10, 10, 390, 120]]},
                         measured={"text": {"en_ink_h": 20,
                                            "en_ink_x0": 30, "en_ink_x1": 200}})
        _r2, _s2, fields2, _t42 = typeset_faces([on], {}, icons, metrics)
        if alignment_violations(fields2):
            findings.append("alignment: a block sitting on its reference axis was "
                            "reported as displaced (%r)"
                            % alignment_violations(fields2))
        # Both axes have their own tolerance, and the left one is the tighter.
        if ALIGNMENT_TOL_PX["left"] >= ALIGNMENT_TOL_PX["center"]:
            findings.append("alignment: the left tolerance is not tighter than "
                            "the centre one")
        if alignment_deviation([0, 0, 100, 10], [10, 110], "center") != 10.0:
            findings.append("alignment: the centre deviation is not measured on "
                            "the centre")
        if alignment_deviation([20, 0, 100, 10], [10, 110], "left") != 10.0:
            findings.append("alignment: the left deviation is not measured on the "
                            "left edge")
        row = kc.row_by_flag("--accept-alignment")
        if row is None or row.module != "kz_typeset.py" or row.exit_code != kc.EXIT_TOLERANCE:
            findings.append("alignment: the TOLERANCES row is %r" % (row,))

    # ------------------------------------------------------------- anti-swap
    if "anti-swap" in wanted:
        # THE PLANTED FAULT: `tablet` and `elder_thing` exchanged. Both
        # declarations are then wrong in both dimensions at once, which is the
        # property that makes a swap detectable when every stage downstream of the
        # map is value-transparent. The NAMED CHECK must fail, and it must fail
        # with 67 -- the anti-swap invariant is not one of the ten rules.
        reference = dict(kx.ICON_REFERENCE)
        swapped = {"elder_thing": reference["tablet"],
                   "tablet": reference["elder_thing"]}
        checks = icon_map_checks(swapped, reference)
        named = [c for c in checks if c["name"] == "icon_declarations"]
        if not named:
            findings.append("anti-swap: no `icon_declarations` check was produced")
        elif named[0]["status"] != "fail":
            findings.append("anti-swap: a tablet/elder_thing swap did not fail "
                            "`icon_declarations`")
        elif named[0]["exit_on_fail"] != kc.EXIT_ARTIFACT:
            findings.append("anti-swap: `icon_declarations` reports exit %s, "
                            "expected %d (the invariant is an ARTIFACT failure, "
                            "not one of the ten rules)"
                            % (named[0]["exit_on_fail"], kc.EXIT_ARTIFACT))
        elif len(named[0]["detail"]) != 4:
            findings.append("anti-swap: a swap produced %d findings, expected 4 "
                            "(both declarations wrong in both dimensions)"
                            % len(named[0]["detail"]))
        # The negative: the recorded reference pair passes both halves.
        clean = icon_map_checks(reference, reference)
        if any(c["status"] != "pass" for c in clean):
            findings.append("anti-swap: the recorded reference pair did not pass "
                            "(%r)" % clean)
        # Half (b): a map containing an indistinguishable pair is refused at
        # MAP-AUTHORING time, not at typeset time.
        close = {"elder_thing": (0.47, 0.916), "tablet": (0.48, 0.918)}
        sep = [c for c in icon_map_checks(close, close)
               if c["name"] == "icon_separation"]
        if not sep or sep[0]["status"] != "fail" or sep[0]["exit_on_fail"] != kc.EXIT_ARTIFACT:
            findings.append("anti-swap: an indistinguishable pair was not refused "
                            "at 67 (%r)" % sep)
        # The two reference points are the ones §5.6 records.
        if reference["elder_thing"] != (0.47, 0.916) or reference["tablet"] != (0.66, 0.826):
            findings.append("anti-swap: kz_checkers.ICON_REFERENCE moved off the "
                            "recorded F25E/F260 pair (%r)" % (reference,))
        # A codepoint that is not `U+XXXX` is refused rather than guessed at.
        for bad in ("F25E", "U+", "U+ZZZZ", ""):
            try:
                codepoint_char(bad)
            except MarkupError:
                continue
            findings.append("anti-swap: codepoint %r was accepted" % bad)
        # THE READER IS THE OTHER WAY A WRONG PAIR GETS IN. `data/icons/<set>.json`
        # has two writers -- step 8's backfill for `core.json` and this stage for a
        # scenario's own set -- so a reader that absorbed a container-key or entry
        # -shape divergence would deliver a wrong `ink_fill` to the very check that
        # exists to catch one. Strict, at 67, naming what it found.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "good.json")
            kc.atomic_write_json(good, {"icons": {
                "skull": {"codepoint": "U+F25B", "ink_fill": 0.4,
                          "advance_em": 0.9}}})
            loaded = load_icon_set(good)
            if loaded.get("skull", {}).get("ink_fill") != 0.4:
                findings.append("anti-swap: the pinned flat shape did not load "
                                "(%r)" % loaded)
            if load_icon_set(os.path.join(tmp, "absent.json")) != {}:
                findings.append("anti-swap: an absent set file did not read empty")
            for name, doc in (
                    ("tokens", {"tokens": {"skull": {"codepoint": "U+F25B",
                                                     "ink_fill": 0.4,
                                                     "advance_em": 0.9}}}),
                    ("nested", {"icons": {"skull": {"value": {
                        "codepoint": "U+F25B", "ink_fill": 0.4,
                        "advance_em": 0.9}}}}),
                    ("partial", {"icons": {"skull": {"codepoint": "U+F25B"}}})):
                path = os.path.join(tmp, "%s.json" % name)
                kc.atomic_write_json(path, doc)
                try:
                    load_icon_set(path)
                except kc.KzRefusal as exc:
                    if exc.code != kc.EXIT_ARTIFACT:
                        findings.append("anti-swap: the %s set refused at %d, "
                                        "expected %d" % (name, exc.code,
                                                         kc.EXIT_ARTIFACT))
                    continue
                findings.append("anti-swap: the %s set loaded instead of "
                                "refusing -- a silent divergence between the two "
                                "writers of this file" % name)
        # The S4 prompt is named for its UNIVERSE, not for its stage, so the
        # bundle's derived lookup would refuse at 13 on every run. Assert the
        # file this module passes explicitly is the one on disk.
        if not os.path.exists(PROMPT_PATH):
            findings.append("anti-swap: %s is missing -- the S4 bundle would "
                            "refuse at 13" % PROMPT_PATH)
        if os.path.exists(ka.prompt_path_for(SID)):
            findings.append("anti-swap: %s now exists, so there are two S4 "
                            "prompts and the explicit pass is silently redundant"
                            % ka.prompt_path_for(SID))

    # ------------------------------------------------------------ allocation
    if "allocation" in wanted:
        # THE 71001 DEFECT, made mechanical: a pinned face declaring any other
        # mode is invisible to every filter that reads the mode.
        lying = [{"file": "71001.png",
                  "body": {"allocation_mode": "single-band",
                           "paragraph_bands": [0, 1], "para_gaps": 0}}]
        if not check_allocation_declared(lying):
            findings.append("allocation: a face carrying paragraph_bands under "
                            "`single-band` was not caught -- that is exactly the "
                            "2026-08-18 gate miss")
        honest = [{"file": "71001.png",
                   "body": {"allocation_mode": "paragraph-pinned",
                            "paragraph_bands": [0, 1], "para_gaps": 0}}]
        if check_allocation_declared(honest):
            findings.append("allocation: an honestly declared pin was flagged")
        if check_allocation_declared([{"file": "a", "body": {
                "allocation_mode": "single-band", "paragraph_bands": None,
                "para_gaps": 2}}]):
            findings.append("allocation: an ordinary single-band face was flagged")
        for bad in ({"allocation_mode": "paragraph-pinned", "paragraph_bands": None,
                     "para_gaps": 0},
                    {"allocation_mode": "paragraph-pinned",
                     "paragraph_bands": [0, 1], "para_gaps": 3},
                    {"allocation_mode": "greedy", "paragraph_bands": None,
                     "para_gaps": 0}):
            if not check_allocation_declared([{"file": "b", "body": bad}]):
                findings.append("allocation: %r was not caught" % (bad,))
        # The pin is CHOSEN by the geometry, not asserted: two hostable bands and
        # two paragraphs pin; one band does not.
        two_bands = [(0, 30), (60, 90)]
        group = {"paras": [{"hr": False, "blockquote": False, "runs": []},
                           {"hr": False, "blockquote": False, "runs": []}],
                 "bands_slice": None}
        mode, pinned = choose_allocation([group], two_bands, 20)
        if mode != "paragraph-pinned" or pinned != [0, 1]:
            findings.append("allocation: one paragraph per hostable band did not "
                            "pin (%r / %r)" % (mode, pinned))
        mode2, pinned2 = choose_allocation([group], [(0, 200)], 20)
        if mode2 != "single-band" or pinned2 is not None:
            findings.append("allocation: a single band pinned anyway (%r)" % mode2)
        # ... and the pin refuses when the geometry does not support it: three
        # paragraphs against two bands is a flow, not a pin.
        group3 = dict(group, paras=group["paras"] + [{"hr": False,
                                                      "blockquote": False,
                                                      "runs": []}])
        if choose_allocation([group3], two_bands, 20)[0] == "paragraph-pinned":
            findings.append("allocation: three paragraphs pinned into two bands")
        # A band too short for the ink height is not hostable, so a medallion-only
        # band cannot capture a paragraph.
        if choose_allocation([group], [(0, 5), (60, 90)], 20)[0] == "paragraph-pinned":
            findings.append("allocation: a medallion-only band was treated as "
                            "hostable")
        # END TO END: a pinned face records para_gaps 0 AS BY-CONSTRUCTION.
        pinned_face = _synth_face(
            "pin.png", [(10, 40, 10, 390), (100, 130, 10, 390)],
            {"text": "가나다\n\n라마바"},
            regions={"text": [[10, 10, 390, 130]]},
            measured={"text": {"en_ink_h": 20}})
        records, _s, _f, _t = typeset_faces([pinned_face], {}, icons, metrics)
        body = (records[0].get("body") or {}) if records else {}
        if body.get("allocation_mode") != "paragraph-pinned":
            findings.append("allocation: two paragraphs over two bands did not "
                            "declare the pin (%r)" % body.get("allocation_mode"))
        elif body.get("para_gaps") or not body.get("para_gaps_by_construction"):
            findings.append("allocation: a pinned face did not record para_gaps 0 "
                            "as by-construction (%r)" % body)
        if check_allocation_declared(records):
            findings.append("allocation: the end-to-end pinned face failed its own "
                            "declaration check (%r)"
                            % check_allocation_declared(records))

    # --------------------------------------------------------------- T5, T3
    if "content" in wanted:
        good = [{"file": "a.png", "regions": {"text": [[0, 0, 10, 10]]},
                 "strings": {"text": "[skull] 가"}}]
        if t5_content_coverage(good, {"skull"}):
            findings.append("content: a matched region/string pair was flagged")
        if not t5_content_coverage(good, set()):
            findings.append("content: an [icon] outside the PUA map was not caught")
        if not t5_content_coverage([{"file": "b.png",
                                     "regions": {"text": [[0, 0, 1, 1]]},
                                     "strings": {}}], {"skull"}):
            findings.append("content: a region with no string was not caught")
        if not t5_content_coverage([{"file": "c.png", "regions": {},
                                     "strings": {"text": "가"}}], {"skull"}):
            findings.append("content: a string with no region was not caught")
        # The tokenizer refuses an unknown token rather than printing it literally.
        try:
            tokenise("[nosuch] 가", "body", {}, "x/text")
        except MarkupError:
            pass
        else:
            findings.append("content: an unknown [token] was tokenised anyway")
        try:
            tokenise("<blink>가</blink>", "body", {}, "x/text")
        except MarkupError:
            pass
        else:
            findings.append("content: an unknown tag was tokenised anyway")
        # `[[Trait]]` must not tokenize as an icon named `[Trait`, the same
        # nesting trap kz_checkers.scan documents.
        paras = tokenise("[[Guest]] 가", "body", {}, "x/text")
        if "".join(t for _r, t in paras[0]["runs"]) != "Guest 가":
            findings.append("content: [[Trait]] did not unwrap (%r)" % paras)
        # A blank line ends a paragraph; a single newline is a soft break.
        if len(tokenise("가\n\n나", "body", {}, "x")) != 2:
            findings.append("content: a blank line did not split a paragraph")
        if len(tokenise("가\n나", "body", {}, "x")) != 1:
            findings.append("content: a soft break split a paragraph")

    if "inventory" in wanted:
        if t3_inventory(["a.png", "b.png"], ["a.png", "b.png"]):
            findings.append("inventory: an exact match was flagged")
        if not t3_inventory(["a.png", "b.png"], ["a.png"]):
            findings.append("inventory: a missing face was not caught")
        if not t3_inventory(["a.png"], ["a.png", "stale.png"]):
            findings.append("inventory: a stale file was not caught")
        # Case-folded, both directions: a case-only difference is one file on this
        # volume and two in the report, which is the failure `git status` cannot
        # show either.
        if t3_inventory(["A.png"], ["a.png"]):
            findings.append("inventory: the comparison is not case-folded")
        if not t3_inventory(["a.png"], ["a.png", "A.png"]):
            findings.append("inventory: a case-only collision was not caught")

    if "containment" in wanted:
        import numpy as np
        clear = np.zeros((10, 10), dtype=bool)
        clear[2:6, 2:6] = True
        before = np.full((10, 10), 255, dtype="uint8")
        after = before.copy()
        after[3, 3] = 0
        outside, changed = t1_containment(before, after, clear)
        if outside or changed != 1:
            findings.append("containment: ink inside CLEAR was reported outside "
                            "(%r/%r)" % (outside, changed))
        after2 = before.copy()
        after2[8, 8] = 0
        if t1_containment(before, after2, clear)[0] != 1:
            findings.append("containment: ink outside CLEAR was not caught")
        union = np.zeros((10, 10), dtype=bool)
        union[2:4, 2:4] = True
        if t2_field_regions(before, after, union) != 0:
            findings.append("containment: ink inside its region was flagged")
        if t2_field_regions(before, after2, union) != 1:
            findings.append("containment: ink outside its region was not caught")
        # The cleared-run primitive is what makes T1 a proof rather than a hope.
        cs = clear_cumsum(clear)
        if usable_run(cs, 2, 5, 0, 9) != (2, 5):
            findings.append("containment: usable_run did not find the fully clear "
                            "column run (%r)" % (usable_run(cs, 2, 5, 0, 9),))
        if usable_run(cs, 1, 5, 0, 9) is not None:
            findings.append("containment: usable_run accepted a row band that is "
                            "not fully clear")
        # `masks/manifest.json`'s padding is an IDENTITY, and this file leans on
        # it twice under two names -- `A6_PAD_X` clamps the alignment reference,
        # `MASK_PAD_X` inverts the rects into English optics. They are the same
        # number and must stay so.
        if MASK_PAD_X != A6_PAD_X:
            findings.append("containment: MASK_PAD_X %d != A6_PAD_X %d -- two "
                            "names for one identity have drifted"
                            % (MASK_PAD_X, A6_PAD_X))
        optics = en_optics_from_rects([[10, 20, 110, 40], [10, 50, 90, 70]])
        if optics["en_ink_h"] != 21 - 2 * MASK_PAD_Y:
            findings.append("containment: the rect->ink-height inverse is wrong "
                            "(%r)" % optics)
        if optics["en_ink_x0"] != 10 + MASK_PAD_X or optics["en_ink_x1"] != 110 - MASK_PAD_X:
            findings.append("containment: the rect->ink-x inverse is wrong (%r)"
                            % optics)
        if en_optics_from_rects([]) != {}:
            findings.append("containment: an empty rect set invented optics")

    if "draw" in wanted:
        # THE CLIP IS A GUARANTEE, AND THIS IS WHERE THAT IS PROVED. The layouts
        # are computed against a WIDE clear and then drawn against a NARROW mask,
        # so the draw layer is deliberately asked to put ink where it may not go.
        # T1 must still come back 0 -- that is what "clipped by construction"
        # means -- and `clipped_px` must be nonzero, because a clip that fired and
        # reported nothing is a defect T1 structurally cannot see.
        import numpy as np
        import tempfile
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            size = (240, 120)
            wide = _synth_face("d.png", [(10, 100, 10, 230)],
                               {"text": "AAA BBB CCC"},
                               size=size,
                               regions={"text": [[10, 10, 230, 100]]},
                               measured={"text": {"en_ink_h": 16}})
            layouts = {}
            records, _s, _f, _t = typeset_faces([wide], {}, icons, metrics,
                                                layouts_out=layouts)
            record = records[0]
            record["composite_path"] = os.path.join("cleared", "d.png")
            record["mask_path"] = os.path.join("masks", "d.png")
            record["typeset_path"] = os.path.join("typeset", "d.png")
            os.makedirs(os.path.join(tmp, "cleared"))
            os.makedirs(os.path.join(tmp, "masks"))
            Image.new("RGB", size, (255, 255, 255)).save(
                os.path.join(tmp, "cleared", "d.png"))
            # The NARROW mask: only the left half of the band is clear, so every
            # line the layout placed across the full width overruns it.
            alpha = np.full((size[1], size[0]), 255, dtype="uint8")
            alpha[10:101, 10:100] = 0
            mask = np.dstack([np.zeros((size[1], size[0], 3), dtype="uint8"), alpha])
            Image.fromarray(mask, "RGBA").save(os.path.join(tmp, "masks", "d.png"))

            draw_face(tmp, record, layouts["d.png"], metrics, write=True)
            if record["t1_outside"]:
                findings.append("draw: %d px landed outside CLEAR -- the clip is "
                                "not a guarantee" % record["t1_outside"])
            if record["t2_outside"]:
                findings.append("draw: %d px landed outside the region union"
                                % record["t2_outside"])
            if not record["clipped_px"]:
                findings.append("draw: a layout drawn against a narrower mask "
                                "reported no clipped ink, so a clip that fires is "
                                "invisible")
            if not record["changed_pixels"]:
                findings.append("draw: nothing was drawn at all -- a silently "
                                "no-op draw layer passes T1 and T2 trivially")
            if not os.path.exists(os.path.join(tmp, "typeset", "d.png")):
                findings.append("draw: no PNG was written on the build path")
            if not record["typeset_sha256"]:
                findings.append("draw: the written PNG was not digested")
            # ... and a non-build run performs the whole measurement and writes
            # nothing: "--dry-run runs the ENTIRE fit, writes NOTHING".
            os.remove(os.path.join(tmp, "typeset", "d.png"))
            draw_face(tmp, record, layouts["d.png"], metrics, write=False)
            if os.path.exists(os.path.join(tmp, "typeset", "d.png")):
                findings.append("draw: a non-build run wrote a PNG")
            if record["typeset_sha256"] is not None:
                findings.append("draw: a non-build run recorded a digest for a "
                                "file it did not write")
            if not record["changed_pixels"]:
                findings.append("draw: a non-build run skipped the measurement")

    if "gate" in wanted:
        record = parse_gate(os.path.join(os.sep, "nonexistent", "typeset-gate.md"))
        if record["status"] != "pending":
            findings.append("gate: an absent gate did not read as pending")
        carried = kc.carry_review({"status": "accepted", "reviewer": "me",
                                   "date": "2026-08-24"},
                                  {"status": "accepted"}, True, "now")
        if carried["status"] != "pending" or not carried.get("superseded"):
            findings.append("gate: a moved bound_sha256 did not supersede the "
                            "acceptance (%r)" % carried)
        held = kc.carry_review({"status": "accepted"}, {"status": "accepted"},
                               False, "now")
        if held["status"] != "accepted":
            findings.append("gate: an unmoved acceptance was not held")
        # §5.7: the gate cannot be `accepted` with an unticked item.
        demoted = apply_gate_items({"status": "accepted"},
                                   {"unticked": ["the RULES TEXT says what the "
                                                 "English says"]})
        if demoted["status"] != "pending" or demoted["blocked_by_unticked"] != 1:
            findings.append("gate: an `accepted` gate with an unticked item was "
                            "not demoted (%r)" % demoted)
        kept = apply_gate_items({"status": "accepted"}, {"unticked": []})
        if kept["status"] != "accepted":
            findings.append("gate: a fully ticked acceptance was demoted")
        # The stub is GENERATED and enumerates PER-FACE PROSE items beside the
        # visual ones -- name, traits, rules text, flavour.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            faces = [{"file": "71001.png", "group": "Asset/front",
                      "body": {"allocation_mode": "paragraph-pinned"}}]
            path = write_gate_stub(
                tmp, "midwinter", {"status": "pending", "bound_sha256": "x"},
                {"faces_typeset": 1, "by_allocation": {"paragraph-pinned": 1},
                 "deep_shrink": 0, "alignment_violations": 0},
                faces, {"skull": {"codepoint": "U+F25B", "ink_fill": 0.4,
                                  "advance_em": 0.9}},
                "typeset-review-gallery.html")
            with open(path, "r", encoding="utf-8") as handle:
                stub = handle.read()
        for needle in ("read for meaning, not for containment", "NAME", "TRAITS",
                       "RULES TEXT", "FLAVOUR", "paragraph-pinned", "71001.png"):
            if needle not in stub:
                findings.append("gate: the stub does not carry %r" % needle)
        if stub.count("- [ ]") < len(FACE_PROSE_ITEMS) + len(FACE_VISUAL_ITEMS):
            findings.append("gate: the stub carries fewer items than one face's "
                            "own visual and prose set")
        reparsed = _parse_gate_text(stub)
        if len(reparsed) != stub.count("- [ ]"):
            findings.append("gate: the parser did not see every unticked item")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


def _parse_gate_text(text):
    """The checkbox half of `parse_gate`, over a string. Used by --selftest so the
    stub and its parser are proved to agree without a file on disk."""
    return [line.strip()[5:].strip() for line in text.splitlines()
            if line.strip().startswith("- [ ]")]


# ---------------------------------------------------------------------------
# 15. CLI
# ---------------------------------------------------------------------------

_SUMMARY = {
    "deep-shrink": "the ladder walks to MIN_SIZE_ABS and a body that still will "
                   "not fit is a T4 row that blocks `consumable` until "
                   "--accept-deep-shrink; a face with room is not a row",
    "alignment": "a block clamped off its reference axis is reported with its "
                 "measured displacement and blocks `consumable` until "
                 "--accept-alignment; a block on its axis is not reported",
    "anti-swap": "a tablet/elder_thing swap fails `icon_declarations` with four "
                 "findings at exit 67, an indistinguishable pair fails "
                 "`icon_separation` at 67, and the reference pair passes both",
    "allocation": "allocation_mode is an explicit three-valued declaration; a "
                  "pinned face declaring anything else is refused at 67, and a "
                  "pinned face records para_gaps 0 as by-construction",
    "content": "T5 catches a region with no string, a string with no region and "
               "an [icon] outside the PUA map, before any pixel is drawn",
    "inventory": "T3 compares the produced face set case-folded in both "
                 "directions, so a stale file is as fatal as a missing one",
    "containment": "T1 and T2 are measured from the arrays before and after the "
                   "draw, and usable_run packs each line against its own rows",
    "draw": "the overlay is clipped by `clear & regions`, so a layout drawn "
            "against a narrower mask still reports T1 0 -- and reports the "
            "clipped pixels separately, because T1 cannot see ink the clip "
            "already removed",
    "gate": "an absent gate reads pending, a moved bound_sha256 supersedes a live "
            "acceptance, and an `accepted` gate with an unticked item is demoted "
            "-- the stub enumerates per-face PROSE items beside the visual ones",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_typeset.py",
        description="koreanize stage S4 -- Korean type onto the cleared faces "
                    "(design §6 step 14).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--ask-dir", help="re-use an existing ai/typeset/<stamp>/")
    parser.add_argument("--replay", metavar="DIR",
                        help="adjudicate a recorded run offline: no claude, no "
                             "credential, no cost")
    parser.add_argument("--readonly-tree", action="append", default=[],
                        help="a tree the agent must not move; repeatable (rule 8c)")
    parser.add_argument("--claude-bin", help="override the shim's binary resolution")
    parser.add_argument("--accept-deep-shrink", action="store_true",
                        help="acknowledge every T4 row: a body that could not be "
                             "fitted above MIN_SIZE_ABS")
    parser.add_argument("--accept-alignment", action="store_true",
                        help="acknowledge every block displaced beyond its axis "
                             "tolerance")
    parser.add_argument("--dry-run", action="store_true",
                        help="write <run_dir>/dry-run/typeset/typeset.dry-run.json")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_typeset --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-13s %s" % (name, _SUMMARY[name]))
        for line in kc.pending_summary():
            print("  %s" % line)
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    mode = "build"
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"
    elif args.replay:
        mode = "replay"

    report, _ask_dir = run_typeset(
        run_dir, mode=mode, accept_deep_shrink=args.accept_deep_shrink,
        accept_alignment=args.accept_alignment, replay=args.replay,
        ask_dir=args.ask_dir, claude_bin=args.claude_bin,
        readonly_trees=args.readonly_tree, quiet=args.quiet,
        write_data=(mode == "build"))

    dest = run_dir
    if args.dry_run:
        dest = os.path.join(run_dir, "dry-run", STAGE)
        kz.assert_dry_run_dest(kz.load_scenario(os.path.join(run_dir,
                                                             "scenario.json")),
                               STAGE, dest)
    path = kc.write_report(report, dest)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize typeset -- %s" % report["slug"])
        print("  faces           : %d (%d typeset)"
              % (counts["faces"], counts["faces_typeset"]))
        print("  icons           : %d (%d ruled)"
              % (counts["icons"], counts["icons_ruled"]))
        print("  allocation      : %s" % counts["by_allocation"])
        print("  shrunk / deep   : %d / %d" % (counts["shrunk"],
                                               counts["deep_shrink"]))
        print("  alignment       : %d" % counts["alignment_violations"])
        print("  batches         : %d of %d complete"
              % (counts["batches_complete"], counts["batches"]))
        print("  gate            : %s%s"
              % (report["gate"]["status"],
                 (" (%d unticked)" % report["gate"]["blocked_by_unticked"])
                 if report["gate"].get("blocked_by_unticked") else ""))
        for check in report["checks"]:
            print("  %-20s: %s  %s" % (check["name"], check["status"],
                                       "; ".join(check["detail"][:3])))
        print("  verdict         : %s (exit %d)"
              % (report["verdict"], report["exit_code"]))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
