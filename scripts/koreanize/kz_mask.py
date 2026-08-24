#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `mask` (S3) -- the layout catalogue, the masks, and W1/W2
(design §6 steps 12 and 16, §5.5, §5.7).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER` -- it decodes PNGs and runs a MaxFilter, so it needs PIL
and numpy, and `/usr/bin/python3` has PIL 10.4.0 and no numpy at all.

WHAT THIS STAGE PRODUCES, AND WHY THE ORDER OF TWO PAINTS IS LOAD-BEARING
-------------------------------------------------------------------------
A mask is an RGBA PNG whose alpha says, per pixel, `0 = CLEAR` (regenerate) and
`255 = KEEP` (restore from the English slice). It is derived exactly the way
`build-masks.py:631`'s `build_regions()` derived Midwinter's: a dark-on-light ink
detector runs *inside a hand-measured window*, the ink is segmented into lines,
and each line is padded by `PAD_X` / `PAD_Y` into a rect. **Only frame windows
are clamped to their window; body rects are not.**

Then protection is painted **last** -- and this is the A6b ordering invariant
§6 step 12 calls out by name:

    `frame_windows` are subtracted from `protect` BEFORE protection is painted.

The two halves have to be read together. Protection is still the last word
everywhere it applies (A6), which is why a region rect can never eat the plate
outline; but the frame-text windows are cut out of the protect set *first*
(`protect_rects()` below), so the type banner and the stage label -- which sit
inside a protected panel -- are still cleared. Get the order wrong in either
direction and the failure is silent: paint protection first and every region
survives it, or forget the subtraction and the frame text is repainted KEEP with
English still under it. `--selftest ordering` plants exactly that fault and
requires it to be caught.

THE LAYOUT CATALOGUE IS DATA, AND S3 IS WHAT PRODUCES IT FOR AN UNSEEN SET
--------------------------------------------------------------------------
`build-masks.py`'s 15 hand-measured groups (`:207-507`) are not code here. They
live in `data/layouts/<set>.json`, git-tracked in the durable tier (§3.1), and
this stage reads them. For a set nobody has measured, S3 proposes them -- one
ruling per **group**, never per card and never per window -- and the numbers it
proposes are bounded twice: the manifest schema bounds `window_margin_px` at
1..64 (exit 66), and `kz_config.check_window_margin()` bounds the assembled
layout file at `1 <= window_margin_px <= calibrated_from <= 64` (exit 4, naming
the group).

`calibrated_from` is **this stage's** number, not the model's: it is the tightest
post-suppression trailing clearance measured on a *defect-free* face in the
group, and it is what turns the negative half of the calibration from a recorded
number into a checked one. A group with no defect-free face -- `Act/front` today,
both of whose faces are the known defects -- records the literal `"COL_GAP"`,
only the numeric bound applies, and `--status` prints the group as
**uncalibrated** so the weaker claim stays visible instead of reading like a
measurement.

STEP 16 -- W1/W2 ARE ACTIVATED HERE, AND THEY ARE NOT IMPLEMENTED HERE
-----------------------------------------------------------------------
The predicates live in `kz_checkers.py` and that placement is the point
(`verify-a4.py:4-7`): a checker must not be authored in the same pass as the
thing it checks. This module *calls* them -- `kx.w1_window_margin()`,
`kx.w2_line_containment()`, `kx.mask_residual_check()` -- and does three things
with the result:

  * it passes them the layout **`windows`** (and `frame_windows`), never
    `regions_slice_coords`. Those are the mask's own padded output, so asserting
    containment against them is circular and is what §5.5 property 1 calls
    "detector attempt #4".
  * it scores the hit set as a **superset**, never an equality. Two known hits
    over "the 26 faces actually looked at" of 88 is a rate, not a bound: a hit on
    one of the other 62 is the detector working, and it goes to adjudication.
  * it refuses at **exit 23** only on a hit whose `(check, group, window_name,
    face)` tuple is not in `data/locks/<slug>.lock.json`'s
    `mask.residual_baseline[]`. A novel hit is escalated to `triage` (S6) the way
    `kz_source.py` escalates a donor ambiguity, and `--accept-mask-residual`
    extends the baseline only for a hit triage ruled `tolerance`. The flag cannot
    launder a hit triage called a **defect**: that fails at 67, which outranks 23
    in `kz_common.EXIT_PRECEDENCE`, so the report names the cause.

WHICH FRAME THE CHECKERS ARE CALLED IN
---------------------------------------
`kz_checkers`' section-3 header states the contract: W1 and W2 take the array
**and** the windows in the group's *working* orientation, because "trailing edges
-- right and bottom" is literal only in that frame. §5.5 property 1 states the
same fact from the other side, as windows "mapped into the emitted 750x1050 slice
frame by the same transform `to_slice()` applies". The two are one map, and this
module applies it to the **arrays** rather than to the rects: the slice is loaded
rotated by `rot`, the alpha is measured before it is rotated back, and the
catalogue's windows are used as written. Mapping the rects instead would hand a
rot-90 group's windows to a predicate that then measures the *leading* margin --
which §5.5 shows fires on 80 of 86 non-defect faces -- and nothing would error,
because a rotated array is a valid array.

Exit codes (§4.2):
   0  every group ruled, every mask written, every check passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin, the AI contract, or the
      layout catalogue's shape / `window_margin_px` bound
  11  stop by policy -- the ten rules stopped this run
  13  precondition -- no slices, no group with a layout, an empty universe, or a
      bundle input missing
  14  the re-derivation does not reproduce the recorded masks
  22  the escalated triage stopped on its own tolerance
  23  a mask-coverage residual outside the lock baseline and
      `--accept-mask-residual` was not given
  25  the stage stopped between batches on its budget; N of M are on disk
  65  claude unavailable / unauthenticated / timed out
  66  AI manifest invalid -- schema, coverage, or run binding
  67  a produced mask failed structural verification, or triage ruled a residual
      a defect

THERE IS NO 30 IN THAT TABLE, AND THAT IS THE POINT. `mask` carries one of the
four human gates (§5.7) -- the contact sheet -- and a pending gate is not this
stage's failure. It attaches a `pending` gate block and exits 0, exactly as
`kz_init.py` and `kz_terms.py` do; `compute_consumable` is what stops `erase` and
`composite` consuming pixels nobody has walked.
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402
import kz_ask as ka  # noqa: E402
import kz_decide as kd  # noqa: E402
import kz_checkers as kx  # noqa: E402
import kz_triage as kt  # noqa: E402

STAGE = "mask"
SID = "S3"

# Declared in the MODULE BODY (§3.2): a scenario.json that does not list `mask`
# in ai.required_stages refuses at exit 4 the moment it is bound, and
# `kz_common.write_report` refuses a build report with `ai: null` at exit 11.
kc.declare_ai(STAGE, required=True)

#: The one tolerance-bearing predicate this module owns
#: (`kz_common.TOLERANCES`, row `mask-residual`, exit 23).
TOLERANCE = "mask-residual"

FAULTS = ("mask-residual", "w2", "ordering", "layout", "gate")

#: `prompts/S3-layouts.md`, passed EXPLICITLY. `kz_ask.prompt_path_for()` derives
#: `<Sid>-<AI_STAGE_MAP[sid]>.md`, i.e. `S3-mask.md`, which does not exist: the
#: design names S3's and S4's prompts after their CONTENT (`layouts`, `icons`)
#: rather than after their stage. `_read_text` refuses at exit 13 on a missing
#: prompt, so the derived lookup would stop the stage before its first call.
PROMPT = os.path.join(ka.PROMPTS_DIR, "S3-layouts.md")

# --------------------------------------------------------------------------
# The geometry constants, every one of them build-masks.py's own.
# --------------------------------------------------------------------------

CELL_W, CELL_H = 750, 1050

PAD_X = 12              # build-masks.py:80
PAD_Y = 7               # build-masks.py:81
EDGE_GUARD = 4          # build-masks.py:86 -- the outermost ring, never cleared

# Frame text (type banner, stage label) is small, letter-spaced and sits a few
# pixels from the plate outline that must survive, so it gets its own tighter
# padding (build-masks.py:88-93). Everything else about the measurement is
# identical.
FRAME_PAD_X = 5
FRAME_PAD_Y = 3

#: The default column-grouping gap. Overridden per layout file by `col_gap`, and
#: it is also `window_margin_px`'s reference value -- one constant, two uses, and
#: §5.5's whole argument for 34 turns on their being the same number.
DEFAULT_COL_GAP = kx.COL_GAP

#: The region names a mask can carry, in the order build-masks.py measured them.
REGION_KEYS = ("title", "subtitle", "traits", "text", "flavor", "victory")

#: Layout keys this stage consumes. A key outside this set is REPORTED, never
#: fatal -- see `validate_layout`.
GROUP_KEYS = frozenset((
    "windows", "frame_windows", "protect", "protect_no_victory",
    "protect_with_victory", "rot", "size", "working_size", "measured_from",
    "window_margin_px", "calibrated_from", "flavor_before_text",
))

ROTATIONS = (0, 90)


# ---------------------------------------------------------------------------
# 1. The layout catalogue -- data, validated, never invented here
# ---------------------------------------------------------------------------

def layout_relpath(cfg):
    """`<layout_set>.json`, the name under `data/layouts/`."""
    name = cfg.get("layout_set") or cfg.get("slug")
    if not name:
        kc.refuse(kc.EXIT_GUARD, "scenario.json declares neither layout_set nor slug",
                  "the layout catalogue is named data/layouts/<set>.json")
    return "%s.json" % name


def layout_path(cfg, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    return os.path.join(workspace, cfg["guard"]["data_root"], "layouts",
                        layout_relpath(cfg))


def read_layout(path):
    """The catalogue as written, or an empty one. NEVER a crash on bad bytes.

    An unseen set legitimately has no catalogue -- that is what S3 is for -- so
    absence is not a refusal here. Unreadable BYTES are: a truncated or
    hand-broken JSON file is a named exit 4 rather than a traceback, because the
    operator's next move is to fix that file and a stack trace does not say so.
    """
    if not os.path.exists(path):
        return {"col_gap": DEFAULT_COL_GAP, "groups": {}}, False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except ValueError as exc:
        kc.refuse(kc.EXIT_GUARD, "the layout catalogue is not valid JSON",
                  "%s: %s" % (path, exc))
    except OSError as exc:
        kc.refuse(kc.EXIT_GUARD, "the layout catalogue could not be read",
                  "%s: %s" % (path, exc))
    if not isinstance(data, dict):
        kc.refuse(kc.EXIT_GUARD, "the layout catalogue is not a JSON object", path)
    return data, True


def _is_rect(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
            and value[2] > value[0] and value[3] > value[1])


def _check_window_map(findings, path, group, label, windows):
    if windows is None:
        return
    if not isinstance(windows, dict):
        findings.append("%s: %s.%s is %s, expected an object of "
                        "window_name -> [x0, y0, x1, y1]"
                        % (path, group, label, type(windows).__name__))
        return
    for name in sorted(windows):
        if not _is_rect(windows[name]):
            findings.append("%s: %s.%s['%s'] is %r, expected four ints with "
                            "x1 > x0 and y1 > y0"
                            % (path, group, label, name, windows[name]))


def validate_layout(layout, path="<layout>"):
    """Every property of `data/layouts/<set>.json` this stage relies on.

    Returns (findings, unconsumed_keys). A missing or malformed REQUIRED key is a
    finding, and the caller refuses at exit 4 with all of them at once; an
    UNKNOWN key is only reported. The asymmetry is deliberate: a catalogue is a
    hand-maintained review artifact, and refusing the whole art chain because a
    human wrote a `notes:` line beside a window would make the file harder to
    document than to use. What must never happen is a crash -- so every access
    below is guarded and every failure is named.
    """
    findings = []
    unconsumed = set()

    col_gap = layout.get("col_gap", DEFAULT_COL_GAP)
    if not isinstance(col_gap, int) or isinstance(col_gap, bool) or col_gap < 1:
        findings.append("%s: col_gap is %r, expected a positive integer "
                        "(build-masks.py:85's COL_GAP is %d)"
                        % (path, layout.get("col_gap"), DEFAULT_COL_GAP))

    groups = layout.get("groups")
    if groups is None:
        findings.append("%s: no groups{}; the catalogue names its groups "
                        "'<Type>/<side>', e.g. 'Act/front'" % path)
        return findings, unconsumed
    if not isinstance(groups, dict):
        findings.append("%s: groups is %s, expected an object"
                        % (path, type(groups).__name__))
        return findings, unconsumed

    for group in sorted(groups):
        spec = groups[group]
        if not isinstance(spec, dict):
            findings.append("%s: groups['%s'] is %s, expected an object"
                            % (path, group, type(spec).__name__))
            continue
        if "/" not in group:
            findings.append("%s: group name %r is not '<Type>/<side>'" % (path, group))
        unconsumed |= set(k for k in spec if k not in GROUP_KEYS)

        windows = spec.get("windows")
        if not isinstance(windows, dict) or not windows:
            findings.append("%s: %s declares no windows{}; a group that names no "
                            "window has proposed nothing" % (path, group))
        else:
            _check_window_map(findings, path, group, "windows", windows)
        _check_window_map(findings, path, group, "frame_windows",
                          spec.get("frame_windows"))

        rot = spec.get("rot", 0)
        if rot not in ROTATIONS:
            findings.append("%s: %s.rot is %r; this stage's to_slice() transform "
                            "is defined for %s only (build-masks.py:840)"
                            % (path, group, rot, list(ROTATIONS)))

        dims = spec.get("working_size", spec.get("size"))
        if dims is not None:
            if (not isinstance(dims, (list, tuple)) or len(dims) != 2
                    or not all(isinstance(v, int) and not isinstance(v, bool)
                               and v > 0 for v in dims)):
                findings.append("%s: %s working dims are %r, expected [w, h]"
                                % (path, group, dims))
            elif rot in ROTATIONS:
                expected = derived_size(rot)
                if list(dims) != list(expected):
                    findings.append("%s: %s declares working dims %r but rot %d of "
                                    "a %dx%d slice is %r -- the dims FOLLOW from "
                                    "the slice frame and rot, and a second "
                                    "declaration of one fact is a second thing "
                                    "that can drift"
                                    % (path, group, list(dims), rot, CELL_W, CELL_H,
                                       list(expected)))

        for key in ("protect", "protect_no_victory", "protect_with_victory"):
            rects = spec.get(key)
            if rects is None:
                continue
            if not isinstance(rects, (list, tuple)):
                findings.append("%s: %s.%s is %s, expected a list of rectangles"
                                % (path, group, key, type(rects).__name__))
                continue
            for index, rect in enumerate(rects):
                if not _is_rect(rect):
                    findings.append("%s: %s.%s[%d] is %r, expected four ints"
                                    % (path, group, key, index, rect))

        measured = spec.get("measured_from")
        if measured is not None and (not isinstance(measured, (list, tuple))
                                     or not all(isinstance(v, str) for v in measured)):
            findings.append("%s: %s.measured_from is %r, expected a list of the "
                            "slice filenames the windows were read off"
                            % (path, group, measured))

        margin = spec.get("window_margin_px")
        if margin is None:
            # ABSENT-BUT-DECLARED IS NOT ABSENT-BY-OMISSION, and W1's honesty on
            # this corpus turns on the distinction.
            #
            # design 5.5: "A group whose post-suppression median is still 0 is a
            # FINDING, not a threshold problem: it means the windows in that
            # group hug their content, which is W1's hypothesis, and it goes
            # through step 16's adjudication like any other hit." Eleven of
            # Midwinter's fifteen groups measure 0 -- verified 2026-08-24 to be
            # genuine rather than the merged-band artifact that once inflated
            # them: in every one the floor is set by a band that really does
            # reach its window's trailing edge.
            #
            # `kz_config.check_window_margin()` already SKIPS a group with no
            # `window_margin_px`, so refusing here made this module strictly
            # stricter than the validator that OWNS the bound, and made the
            # catalogue for the only corpus we have unusable -- `mask` refused at
            # exit 4 and the whole art chain below it was unreachable. That is
            # not a safe default; it is the check declining to report its own
            # subject.
            #
            # So a group may omit the threshold only by SAYING SO. The shape is
            # lifted from `kz_common.TOLERANCES`' hard_cap rule -- "exactly the
            # hard_cap rows lack a flag, so the absence is a declaration rather
            # than an omission" -- and it keeps the refusal for the case that
            # actually matters: a group that simply forgot the key.
            declared = spec.get("uncalibrated") is True
            reason = spec.get("uncalibrated_reason")
            if not declared or not (isinstance(reason, str) and reason.strip()):
                findings.append(
                    "%s: %s declares no window_margin_px; W1's premise has no "
                    "threshold in this group. If that is intended, the group "
                    "must say so with uncalibrated: true and a non-empty "
                    "uncalibrated_reason -- an omission is refused, a "
                    "declaration is reported as unasserted coverage"
                    % (path, group))
        elif not isinstance(margin, int) or isinstance(margin, bool):
            findings.append("%s: %s.window_margin_px is %r, expected an integer"
                            % (path, group, margin))

        calibrated = spec.get("calibrated_from")
        if calibrated is None:
            findings.append("%s: %s declares no calibrated_from; record the "
                            "tightest trailing clearance on a defect-free face, "
                            "or the literal \"COL_GAP\" where the group has none"
                            % (path, group))
        elif calibrated != "COL_GAP" and (not isinstance(calibrated, int)
                                          or isinstance(calibrated, bool)):
            findings.append("%s: %s.calibrated_from is %r, expected an integer or "
                            "\"COL_GAP\"" % (path, group, calibrated))

    return findings, unconsumed


def assert_layout(layout, path):
    """Shape first, then `kz_config`'s bound. Refuses at exit 4, all at once.

    The bound is NOT re-implemented here: `kz_config.check_window_margin()` owns
    `1 <= window_margin_px <= calibrated_from <= 64` and is the single place the
    S3-proposed threshold is policed, so a second copy of the inequality could
    disagree with it.
    """
    findings, unconsumed = validate_layout(layout, path)
    findings += kz.check_window_margin(layout, path)
    if findings:
        kc.refuse(kc.EXIT_GUARD, "the layout catalogue is not usable",
                  "; ".join(findings))
    return unconsumed


def derived_size(rot):
    """The working dims, DERIVED. Slices are emitted 750x1050; a rot-90 group is
    read in the resulting 1050x750 frame."""
    return (CELL_H, CELL_W) if rot in (90, 270) else (CELL_W, CELL_H)


def group_spec(layout, group):
    """The normalised spec for one group, with every optional key defaulted."""
    raw = (layout.get("groups") or {}).get(group)
    if not isinstance(raw, dict):
        return None
    rot = raw.get("rot", 0)
    return {
        "group": group,
        "windows": dict(raw.get("windows") or {}),
        "frame_windows": dict(raw.get("frame_windows") or {}),
        "protect": [tuple(r) for r in (raw.get("protect") or [])],
        "protect_no_victory": [tuple(r) for r in (raw.get("protect_no_victory") or [])],
        "protect_with_victory": [tuple(r) for r in
                                 (raw.get("protect_with_victory") or [])],
        "rot": rot,
        "size": tuple(raw.get("working_size") or raw.get("size") or derived_size(rot)),
        "measured_from": list(raw.get("measured_from") or []),
        "window_margin_px": raw.get("window_margin_px"),
        "calibrated_from": raw.get("calibrated_from"),
        "flavor_before_text": bool(raw.get("flavor_before_text", False)),
        "has_victory_variants": bool(raw.get("protect_no_victory")
                                     or raw.get("protect_with_victory")),
    }


def uncalibrated_groups(layout):
    """The groups whose `calibrated_from` is the literal "COL_GAP".

    §5.5: such a group has NO defect-free face, so its threshold is inherited
    from `COL_GAP` rather than measured. `--status` prints it as uncalibrated so
    the weaker claim stays visible instead of reading like a measurement.
    """
    out = []
    for group, spec in sorted((layout.get("groups") or {}).items()):
        if isinstance(spec, dict) and spec.get("calibrated_from") == "COL_GAP":
            out.append(group)
    return out


# ---------------------------------------------------------------------------
# 2. The mask geometry -- build-masks.py's, ported to numpy
# ---------------------------------------------------------------------------
#
# Ported rather than imported: `.am/` is gitignored (§3.1) and the original is
# one of the files this project has already lost a sibling of. Every constant is
# cited to its line in `build-masks.py` so the port stays checkable against it.

def unasserted_groups(layout):
    """Groups where W1 has NO threshold at all, so its premise goes unasserted.

    DISTINCT FROM `uncalibrated_groups()`, and the two must not be merged. A
    "COL_GAP" group HAS a threshold (34) and W1 runs in it; only the CALIBRATION
    is inherited rather than measured. A group in this list has no positive
    threshold that survives its own negative evidence -- its tightest trailing
    clearance on a defect-free face is 0 -- so `w1_window_margin` is inert there
    and the group's windows are simply not policed.

    That is a weaker claim than "W1 passed", and design 5.5 requires the weaker
    claim to stay visible rather than read like a measurement. Counting them is
    what stops eleven silently unpoliced groups from looking like eleven clean
    ones.
    """
    out = []
    for group, spec in sorted((layout.get("groups") or {}).items()):
        if not isinstance(spec, dict):
            continue
        if spec.get("window_margin_px") is None and spec.get("uncalibrated") is True:
            out.append(group)
    return out


def load_slice_gray(path, rot=0):
    """The slice as an "L" image in the group's WORKING orientation."""
    from PIL import Image
    try:
        image = Image.open(path).convert("L")
    except OSError as exc:
        kc.refuse(kc.EXIT_PRECONDITION, "a slice could not be decoded",
                  "%s: %s" % (path, exc))
    if image.size != (CELL_W, CELL_H):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "a slice is not %dx%d" % (CELL_W, CELL_H),
                  "%s is %dx%d" % (path, image.size[0], image.size[1]))
    return image.rotate(rot, expand=True) if rot else image


def line_groups(ink, window, pad_x=PAD_X, pad_y=PAD_Y, min_ink=kx.LINE_MIN_INK,
                col_gap=DEFAULT_COL_GAP):
    """build-masks.py:116 over a numpy bool array: ink inside `window`,
    segmented into lines, each line a list of padded rects.

    THE ROW SEGMENTATION IS `kz_checkers._row_runs`, not a second copy of it --
    but ONLY because that helper is build-masks.py:116's segmenter and this is
    build-masks.py's mask builder. It is deliberately NOT the segmenter the
    checker uses, and the earlier version of this comment had that backwards.

    It used to claim `LINE_GAP` "has to be the same number the checker's
    `measure_bands()` uses, or the mask and the check that polices it would be
    segmenting different text". That is false, and the belief is what produced a
    real defect: `measure_bands()` was written as a fork of `line_groups()` to
    honour it, when §5.5 requires a fork of `typeset-cards.py`'s
    `measure_en_lines()` (`:721`) precisely so the check does NOT inherit the
    producer's segmentation. The two want opposite things. Merging two printed
    lines into one rect is HARMLESS here -- the rect simply covers both -- and
    fatal there, because a merged band's height is the ruler W1's band-height
    filter is calibrated against. Sharing the segmenter drove `en_ink_h` to 98 px
    on `Act/front` against a 20-45 plausible range (`typeset-cards.py:3857`),
    inverting the filter so it discarded the text and kept the dark plate, and
    W1 measured the plate at 0 px clearance instead of the text at 27.

    The independence is the point, and it is the same rule `verify-a4.py:4-7`
    states for the text chain: a checker authored from the producer's own
    machinery cannot fail on the producer's own blind spot.

    Rects are returned in the group's working frame and are NOT clamped to the
    window: §5.5 is explicit that only frame rects are clamped, because a body
    rect's padding reaching past the window is how the line's last glyph gets
    into CLEAR at all.
    """
    import numpy as np
    x0, y0, x1, y1 = (int(v) for v in window)
    height, width = ink.shape
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 <= x0 or y1 <= y0:
        return []
    sub = ink[y0:y1, x0:x1]
    if not sub.any():
        return []
    profile = sub.sum(axis=1).astype(np.int64)
    out = []
    for top, bottom in kx._row_runs(profile >= min_ink):
        cols = np.nonzero(sub[top:bottom + 1].any(axis=0))[0]
        if cols.size == 0:
            continue
        # Split the line into column groups. A frame ornament that happens to
        # share rows with a text line sits far from the glyph run; grouping stops
        # one such speck from stretching the rect across artwork
        # (build-masks.py:145-152).
        groups, current = [], [int(cols[0]), int(cols[0])]
        for col in cols[1:]:
            col = int(col)
            if col - current[1] <= col_gap:
                current[1] = col
            else:
                groups.append(current)
                current = [col, col]
        groups.append(current)
        out.append([(max(0, x0 + c0 - pad_x), max(0, y0 + top - pad_y),
                     min(width, x0 + c1 + 1 + pad_x),
                     min(height, y0 + bottom + 1 + pad_y))
                    for c0, c1 in groups])
    return out


def line_rects(ink, window, **kwargs):
    """Flat list of rectangles for every line inside `window`."""
    return [rect for line in line_groups(ink, window, **kwargs) for rect in line]


def clamp_rect(rect, window):
    x0, y0, x1, y1 = rect
    wx0, wy0, wx1, wy1 = window
    return (max(x0, wx0), max(y0, wy0), min(x1, wx1), min(y1, wy1))


def union_box(rects):
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def _sub_one(rect, hole):
    """`rect` minus `hole`, as up to four disjoint rectangles covering the
    difference (build-masks.py:172-197)."""
    rx0, ry0, rx1, ry1 = rect
    hx0, hy0, hx1, hy1 = hole
    if hx1 <= rx0 or hx0 >= rx1 or hy1 <= ry0 or hy0 >= ry1:
        return [rect]
    parts = []
    if ry0 < hy0:
        parts.append((rx0, ry0, rx1, min(ry1, hy0)))
    if hy1 < ry1:
        parts.append((rx0, max(ry0, hy1), rx1, ry1))
    my0, my1 = max(ry0, hy0), min(ry1, hy1)
    if my0 < my1:
        if rx0 < hx0:
            parts.append((rx0, my0, min(rx1, hx0), my1))
        if hx1 < rx1:
            parts.append((max(rx0, hx1), my0, rx1, my1))
    return [p for p in parts if p[2] > p[0] and p[3] > p[1]]


def subtract_rects(rects, holes):
    """Every rectangle in `rects` with every rectangle in `holes` cut out.

    build-masks.py's own words at `:189-193`: "This is what keeps the A6 ordering
    invariant intact while still letting the frame text be cleared: protection is
    still painted last, it simply no longer claims the frame-text windows."
    """
    out = list(rects)
    for hole in holes:
        nxt = []
        for rect in out:
            nxt.extend(_sub_one(rect, hole))
        out = nxt
    return out


def present(fields, key):
    """`victory` 0 is a printed value ("Victory 0."), so it tests for None."""
    value = fields.get(key)
    if key == "victory":
        return value is not None
    return bool(value and str(value).strip())


def split_body(lines, fields, flavor_before):
    """Name the body line rectangles `flavor` and `text` (build-masks.py:594).

    The alpha channel does not depend on this -- both names are cleared. The
    split is a naming heuristic driven by the relative character counts of the
    two printed strings, snapped to the widest inter-line gap near that fraction.
    """
    flat = lambda groups: [rect for line in groups for rect in line]
    has_text = present(fields, "text")
    has_flavor = present(fields, "flavor")
    if not lines:
        return {}
    if has_text and not has_flavor:
        return {"text": flat(lines)}
    if has_flavor and not has_text:
        return {"flavor": flat(lines)}
    if not (has_text or has_flavor):
        return {}
    count = len(lines)
    if count < 2:
        return {"text": flat(lines)} if has_text else {"flavor": flat(lines)}
    flavor_len = len(str(fields["flavor"]))
    text_len = len(str(fields["text"]))
    frac = flavor_len / float(flavor_len + text_len)
    target = max(1, min(count - 1,
                        int(round(count * (frac if flavor_before else 1 - frac)))))
    best, best_gap = target, -1
    for k in range(max(1, target - 1), min(count - 1, target + 1) + 1):
        gap = min(r[1] for r in lines[k]) - max(r[3] for r in lines[k - 1])
        if gap > best_gap:
            best, best_gap = k, gap
    head, tail = flat(lines[:best]), flat(lines[best:])
    if flavor_before:
        return {"flavor": head, "text": tail}
    return {"text": head, "flavor": tail}


def build_regions(ink, spec, fields, col_gap=DEFAULT_COL_GAP):
    """(regions, warnings) -- build-masks.py:631 with the catalogue as data.

    `regions` maps a region name to a list of rects in the working frame.
    `warnings` names every field the card prints for which no ink was measured:
    that is the mask silently not covering printed text, and it is what the human
    gate is given to look at.
    """
    windows = spec["windows"]
    regions = {}

    for key in ("title", "subtitle", "traits"):
        if key in windows and present(fields, key):
            rects = line_rects(ink, windows[key], col_gap=col_gap)
            if rects:
                regions[key] = rects

    victory = []
    if "victory" in windows and present(fields, "victory"):
        victory = line_rects(ink, windows["victory"], col_gap=col_gap)
        if victory:
            regions["victory"] = victory

    if "body" in windows:
        body_window = list(windows["body"])
        if victory:
            # Keep the body window clear of the victory line for NAMING purposes;
            # both are cleared either way.
            body_window[3] = min(body_window[3], min(r[1] for r in victory) - 2)
        if body_window[3] > body_window[1]:
            body = line_groups(ink, tuple(body_window), col_gap=col_gap)
            regions.update(split_body(body, fields,
                                      spec.get("flavor_before_text", False)))

    # Frame text. NOT gated on a card_source field -- the type banner and the
    # stage label are printed by the frame itself, so the ink detector is the
    # only authority on whether this slice carries one. These rects ARE clamped
    # to their window, so the tighter padding can never reach the plate outline
    # the window was measured to exclude.
    for key, window in sorted(spec.get("frame_windows", {}).items()):
        rects = [clamp_rect(r, window) for r in
                 line_rects(ink, window, pad_x=FRAME_PAD_X, pad_y=FRAME_PAD_Y,
                            col_gap=col_gap)]
        rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
        if rects:
            regions[key] = rects

    warnings = []
    for key in REGION_KEYS:
        if present(fields, key) and key not in regions:
            warnings.append("printed %s but no ink measured" % key)
    for key in sorted(spec.get("frame_windows", {})):
        if key not in regions:
            warnings.append("group declares a %s window but no ink measured "
                            "inside it" % key)
    return regions, warnings


def protect_rects(spec, fields, subtract_frame=True):
    """THE A6b ORDERING INVARIANT, and the only place it is expressed.

    The rectangles actually repainted opaque, with the frame windows ALREADY
    REMOVED. A6's ordering is unchanged -- these are still painted AFTER every
    region -- but the frame windows are cut out *here* rather than re-cleared
    later, so protection remains the last word everywhere it still applies.

    `subtract_frame=False` exists for one caller only: `--selftest ordering`,
    which needs to build the faulted mask in order to prove the correct one
    differs from it. A checker that cannot produce the failure it forbids has not
    shown that it would catch it.
    """
    out = list(spec.get("protect", []))
    if spec.get("has_victory_variants"):
        out += (spec["protect_with_victory"] if present(fields, "victory")
                else spec["protect_no_victory"])
    frame = list((spec.get("frame_windows") or {}).values())
    if frame and subtract_frame:
        out = subtract_rects(out, frame)
    return out


def render_alpha(spec, regions, fields, subtract_frame=True):
    """The alpha channel in the group's WORKING orientation.

    Regions first (alpha 0 = CLEAR), protection last (alpha 255 = KEEP), edge
    guard last of all. `render_alpha` returns the working-orientation image
    because that is the frame W2 must measure containment in -- see the module
    docstring.
    """
    from PIL import Image, ImageDraw
    width, height = spec["size"]
    alpha = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(alpha)
    for rects in regions.values():
        for x0, y0, x1, y1 in rects:
            draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=0)
    guards = protect_rects(spec, fields, subtract_frame=subtract_frame) + [
        (0, 0, width, EDGE_GUARD), (0, height - EDGE_GUARD, width, height),
        (0, 0, EDGE_GUARD, height), (width - EDGE_GUARD, 0, width, height),
    ]
    for x0, y0, x1, y1 in guards:
        draw.rectangle([max(0, x0), max(0, y0),
                        min(width, x1) - 1, min(height, y1) - 1], fill=255)
    return alpha


def mask_image(alpha, rot):
    """The RGBA mask in the SLICE frame, rotated back out of the working one."""
    from PIL import Image
    width, height = alpha.size
    white = Image.new("L", (width, height), 255)
    mask = Image.merge("RGBA", (white, white, white, alpha))
    if rot:
        mask = mask.rotate(-rot, expand=True)
    if mask.size != (CELL_W, CELL_H):
        kc.refuse(kc.EXIT_ARTIFACT,
                  "a rendered mask is not %dx%d" % (CELL_W, CELL_H),
                  "got %dx%d after rot %s" % (mask.size[0], mask.size[1], rot))
    return mask


def to_slice(rect, rot):
    """build-masks.py:840 -- a working-frame rect in SLICE coordinates.

    The mask is rotated by -rot (clockwise) on the way out, so an upright
    (xu, yu) maps to (CELL_W - y1, x0, CELL_W - y0, x1).
    """
    if not rot:
        return list(rect)
    if rot != 90:
        kc.refuse(kc.EXIT_GUARD, "to_slice() is defined for rot 0 and 90 only",
                  "got rot %r; build-masks.py:840 derives the 90 case alone" % rot)
    x0, y0, x1, y1 = rect
    return [CELL_W - y1, x0, CELL_W - y0, x1]


# ---------------------------------------------------------------------------
# 3. Grouping -- which fields a slice actually prints, and under which group
# ---------------------------------------------------------------------------

def printed_side(record, card):
    """(group, type_name, fields) -- build-masks.py:518, ported verbatim.

    LOCATIONS ARE INVERTED WITH RESPECT TO EVERY OTHER TYPE, and this is the
    single fact §6 step 12 names: the *face* slice is the **UNREVEALED** side,
    which prints `back_name` / `back_text` / `back_flavor` (plus the traits,
    which appear on both sides), and the *back* slice is the REVEALED one.
    Verified on 71009: face reads "Ground-Floor Room" (= back_name), back reads
    "Art Gallery" (= name). Get it the other way round and every Location mask
    is measured against text that is printed on the other side of the card.
    """
    type_name = card.get("type_name") or "Unknown"
    side = record.get("side")
    if type_name == "Location":
        if side == "face":
            return "Location/front", "Location", {
                "title": card.get("back_name") or card.get("name"),
                "subtitle": None,
                "traits": card.get("traits"),
                "text": card.get("back_text"),
                "flavor": card.get("back_flavor"),
                "victory": None,
            }
        return "Location/back", "Location", {
            "title": card.get("name"), "subtitle": None,
            "traits": card.get("traits"), "text": card.get("text"),
            "flavor": card.get("flavor"), "victory": card.get("victory"),
        }
    if side == "face":
        return type_name + "/front", type_name, {
            "title": card.get("name"), "subtitle": card.get("subname"),
            "traits": card.get("traits"), "text": card.get("text"),
            "flavor": card.get("flavor"), "victory": card.get("victory"),
        }
    b_side = card.get("b_side")
    if b_side:
        name = b_side.get("type_name") or type_name
        return name + "/bside", name, {
            "title": b_side.get("name"), "subtitle": b_side.get("subname"),
            "traits": b_side.get("traits"), "text": b_side.get("text"),
            "flavor": b_side.get("flavor"), "victory": b_side.get("victory"),
        }
    # The printed reverse of a double-sided card described by back_* fields.
    return type_name + "/back", type_name, {
        "title": card.get("back_name") or card.get("name"), "subtitle": None,
        "traits": card.get("traits"), "text": card.get("back_text"),
        "flavor": card.get("back_flavor"), "victory": None,
    }


def read_slice_records(run_dir):
    """`<run_dir>/slices/manifest.json` -> the per-face records `slice` emitted.

    The contract with `kz_slice.py` is one key deep and stated here because this
    is the consumer: a `records[]` of objects carrying `filename`, `arkham_id`
    and `side`. A bare list is accepted for the same shape, because the legacy
    `slices/manifest.json` this fixture was built from is one.
    """
    path = os.path.join(run_dir, "slices", "manifest.json")
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "no slice manifest", "%s is absent; "
                  "`mask` consumes `slice`'s output (§1.2)" % path)
    doc, err = kd._read_json(path)
    if err:
        kc.refuse(kc.EXIT_PRECONDITION, "the slice manifest is unreadable", err)
    records = doc.get("records") if isinstance(doc, dict) else doc
    if not isinstance(records, list) or not records:
        kc.refuse(kc.EXIT_PRECONDITION, "the slice manifest declares no records[]",
                  path)
    for record in records:
        if not isinstance(record, dict) or not record.get("filename"):
            kc.refuse(kc.EXIT_PRECONDITION,
                      "a slice record carries no filename", repr(record)[:200])
    return records


def card_index(doc):
    """arkham_id -> card, from `card-source-en.json` or `card-text-en.json`."""
    cards = {}
    rows = (doc.get("cards") if isinstance(doc, dict) else doc) or []
    for card in rows:
        key = card.get("code") or card.get("arkham_id")
        if key:
            cards[str(key)] = card
    return cards


def group_universe(records, cards, sample=8):
    """(universe[], units{}) -- one unit per GROUP, mechanically derived.

    The universe is the corpus's, not the model's: S3 rules on a set of groups it
    did not get to choose, for the same reason `kz_terms.extract_terms` derives
    the terminology universe rather than asking for one. A stage that let the
    agent both propose and adjudicate its own universe has no coverage rule that
    means anything.
    """
    units = collections.OrderedDict()
    plan = []
    for record in records:
        arkham_id = str(record.get("arkham_id") or "")
        card = cards.get(arkham_id) or {}
        group, type_name, fields = printed_side(record, card)
        plan.append((record, group, type_name, fields))
        unit = units.setdefault(group, {"group": group, "type_name": type_name,
                                        "faces": [], "face_count": 0,
                                        "measured_from": []})
        unit["face_count"] += 1
        if len(unit["faces"]) < sample:
            unit["faces"].append(arkham_id or record["filename"])
        if len(unit["measured_from"]) < 2:
            unit["measured_from"].append("slices/%s" % record["filename"])
    universe = sorted(units)
    if not universe:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the slice manifest yields no layout group",
                  "check that card-source-en.json indexes the sliced ids")
    return universe, dict(units), plan


# ---------------------------------------------------------------------------
# 4. Step 16 -- W1/W2, activated. The predicates are kz_checkers', not this
#    module's, and the three properties below are what activation MEANS.
# ---------------------------------------------------------------------------

def checked_windows(spec):
    """The rectangles W1 and W2 are given: `windows` AND `frame_windows`.

    NOT `regions_slice_coords`. Those are the mask's own padded output rects, so
    asserting containment against them is circular -- §5.5 property 1 names that
    "detector attempt #4", the fourth failed detector after the three
    `mask-coverage-finding.md` records. The windows are the PREMISE; the rects
    are the conclusion; a premise is what you check a conclusion against.

    Both maps are passed in ONE call per face, deliberately: `ink_mask()` runs a
    MaxFilter(11) over the whole slice and is the most expensive operation in the
    checker, so the filter is paid once per face and amortised over every window
    in the group (kz_checkers section 3's cost contract).
    """
    windows = dict(spec.get("windows") or {})
    for name, rect in (spec.get("frame_windows") or {}).items():
        # Namespaced so a `title` window and a `title` frame window cannot
        # collide into one key -- the baseline is matched on window_name, and two
        # windows sharing a name would make one of them unrepresentable.
        windows["frame:%s" % name] = rect
    return windows


def coverage_hits(ink, gray, clear, spec, group, face):
    """W1 + W2 for one face, in the group's WORKING orientation.

    `ink`, `gray` and `clear` are all in that frame -- the slice loaded rotated
    by `rot`, and the alpha read BEFORE it is rotated back. See the module
    docstring for why the arrays are mapped rather than the rects.
    """
    import numpy as np
    windows = checked_windows(spec)
    margin = spec.get("window_margin_px")
    hits = []
    if windows and isinstance(margin, int):
        hits += kx.w1_window_margin(ink, windows, margin, group, face)
    if windows:
        hits += kx.w2_line_containment(np.asarray(gray), clear, windows, group, face)
    return hits


def trailing_clearance(ink, spec):
    """The tightest post-suppression trailing clearance over one face, or None.

    This is W1's own measurement with the threshold taken out: the same
    `measure_bands` / `text_bands` pair, the same band-height filter, the same
    right-and-bottom-only reading -- returning the NUMBER rather than a verdict.
    It is what `calibrated_from` is derived from, and deriving it from anything
    else would calibrate the check against a quantity it does not measure.
    """
    tightest = None
    for _name, window in sorted(checked_windows(spec).items()):
        x0, y0, x1, y1 = (int(v) for v in window)
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0:
            continue
        bands, en_ink_h = kx.measure_bands(ink, (x0, y0, x1, y1))
        for top, bottom, _bx0, bx1 in kx.text_bands(bands, en_ink_h):
            value = min((width - 1) - bx1, (height - 1) - bottom)
            tightest = value if tightest is None else min(tightest, value)
    return tightest


def calibrate(group_faces, defect_faces):
    """`calibrated_from` for one group: the tightest trailing clearance measured
    on a face with NO known defect, or the literal "COL_GAP".

    `group_faces` is {face: clearance-or-None}; `defect_faces` is the set of
    faces the lock's `mask.residual_baseline[]` already names for this group.

    A group with no defect-free face cannot calibrate itself -- Midwinter's
    `Act/front` is exactly this case, with two faces both of which are the known
    defects -- so the honest answer is the label, not a number. §5.5: "a weaker
    claim honestly labelled rather than a measurement that was never made."
    """
    clean = [value for face, value in sorted(group_faces.items())
             if face not in defect_faces and value is not None]
    if not clean:
        return "COL_GAP"
    return int(min(clean))


def baseline_faces(baseline):
    """group -> {face} from the lock's `mask.residual_baseline[]`."""
    out = {}
    for entry in baseline or ():
        if isinstance(entry, dict) and entry.get("group") and entry.get("face"):
            out.setdefault(entry["group"], set()).add(str(entry["face"]))
    return out


# ---------------------------------------------------------------------------
# 5. The lock's residual baseline -- read here, extended only after triage
# ---------------------------------------------------------------------------

def lock_path(cfg, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    return os.path.join(workspace, cfg["guard"]["data_root"], "locks",
                        "%s.lock.json" % cfg["slug"])


def read_lock(cfg, workspace=None):
    """(lock, baseline). An absent lock is an EMPTY baseline, not a refusal.

    Absent means "nothing has been ruled yet", which is the correct reading for a
    scenario whose first mask build this is -- and it makes every hit novel,
    which is the safe direction. A lock that exists and cannot be parsed is a
    named exit 13 rather than a silent empty baseline, because an unreadable
    baseline and an empty one are opposite facts about what a human has decided.
    """
    path = lock_path(cfg, workspace)
    if not os.path.exists(path):
        return {}, []
    lock, err = kd._read_json(path)
    if err:
        kc.refuse(kc.EXIT_PRECONDITION, "the lock receipt is unreadable",
                  "%s -- an unreadable residual baseline is not an empty one" % err)
    baseline = ((lock.get("mask") or {}).get("residual_baseline")) or []
    if not isinstance(baseline, list):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "mask.residual_baseline[] is not a list", path)
    return lock, baseline


def baseline_entry(hit, reviewer, when):
    """One `mask.residual_baseline[]` row, in §3.1's declared entry shape.

    `extent` is recorded FOR DIFFING and is deliberately not part of the match:
    matching is equality on `(check, group, window_name, face)` -- never on
    `group` alone, so a third face inside an already-baselined group still fires
    exit 23, and never on `face` alone, so a W1 hit (whose subject is the window)
    is representable at all.
    """
    band = hit.get("band") or [None, None, None, None]
    return collections.OrderedDict([
        ("check", hit.get("check")),
        ("group", hit.get("group")),
        ("window_name", hit.get("window_name")),
        ("face", hit.get("face")),
        ("extent", [band[2], band[3], band[0], band[1]]),
        ("accepted_by", reviewer),
        ("accepted_on", when),
    ])


def extend_baseline(lock, additions):
    """The lock with `mask.residual_baseline[]` extended, key-deduplicated.

    A READ-MERGE-WRITE over the whole receipt and never a rewrite of it: the
    `stages{}` block is `kz_langpack.py`'s and the atlas map is `upload`'s, so
    this touches one key and carries every other one through untouched.
    """
    merged = collections.OrderedDict(lock or {})
    mask_block = collections.OrderedDict(merged.get("mask") or {})
    existing = list(mask_block.get("residual_baseline") or [])
    known = set(kx.baseline_key(e) for e in existing)
    for entry in additions:
        if kx.baseline_key(entry) not in known:
            existing.append(entry)
            known.add(kx.baseline_key(entry))
    mask_block["residual_baseline"] = existing
    merged["mask"] = mask_block
    merged.setdefault("schema_version", kc.SCHEMA_VERSION)
    return merged


# ---------------------------------------------------------------------------
# 6. The S6 escalation -- the same path kz_source.py uses for a donor ambiguity
# ---------------------------------------------------------------------------

def escalation_detail(hit):
    """One structured `checks[].detail[]` entry `kz_triage` adjudicates.

    `key` is the baseline tuple itself, joined -- so the unit id triage mints is
    `mask:W0:W1|Act/front|body|71006` and is unique by construction.
    `universe_from_gate` refuses a duplicate unit id, and the baseline key is the
    only identifier of a hit that is guaranteed distinct.
    """
    key = "|".join(str(part) for part in kx.baseline_key(hit))
    return collections.OrderedDict([
        ("key", key),
        ("kind", "mask_residual"),
        ("check", hit.get("check")),
        ("group", hit.get("group")),
        ("window_name", hit.get("window_name")),
        ("face", hit.get("face")),
        ("band", hit.get("band")),
        ("detail", hit.get("detail")),
    ])


def escalation_gate(novel):
    """The gate report `kz_triage.escalate` adjudicates.

    `exit_on_fail` is 23 and not 11: 11 is the AI-policy code and routes an
    operator to `decide.json`, which has nothing to say about whether a residual
    is a defect or a ruled tolerance.
    """
    return collections.OrderedDict([
        ("schema_version", kc.SCHEMA_VERSION),
        ("stage", STAGE),
        ("verdict", "FAIL"),
        ("checks", [collections.OrderedDict([
            ("id", "W0"),
            ("name", "mask_coverage_residual"),
            ("status", "fail"),
            ("exit_on_fail", kc.EXIT_MASK_RESIDUAL),
            ("detail", [escalation_detail(hit) for hit in novel]),
        ])]),
    ])


def unit_id_for(hit):
    """The id `kz_triage.universe_from_gate` mints for this hit's finding."""
    return "%s:W0:%s" % (STAGE, "|".join(str(p) for p in kx.baseline_key(hit)))


def rulings_by_unit(triage_report):
    """unit_id -> ruling, from a triage stage report's results block."""
    out = {}
    for ruling in ((triage_report or {}).get("results") or {}).get("rulings") or []:
        out[ruling.get("unit_id")] = ruling
    return out


def partition_by_ruling(novel, triage_report):
    """(tolerance[], defect[], unruled[]) over the novel hits.

    The three-way split is what keeps `--accept-mask-residual` honest. §5.5 says
    the flag "records a human decision to extend the baseline AFTER the hit has
    been ruled through the S6 triage path", so a hit triage called a **defect**
    must not be absorbable by it, and a hit nobody ruled must not be either.
    """
    rulings = rulings_by_unit(triage_report)
    tolerance, defect, unruled = [], [], []
    for hit in novel:
        ruling = rulings.get(unit_id_for(hit))
        verdict = (ruling or {}).get("verdict")
        if verdict == "tolerance":
            tolerance.append(hit)
        elif verdict == "defect":
            defect.append(hit)
        else:
            unruled.append(hit)
    return tolerance, defect, unruled


# ---------------------------------------------------------------------------
# 7. Assembling the layout file from the rulings
# ---------------------------------------------------------------------------

def layout_from_rulings(existing, universe, effective, col_gap=DEFAULT_COL_GAP):
    """(layout, findings) -- the catalogue this run will build from.

    An `inherit` ruling keeps the catalogue's own entry, which is the whole point
    of the verdict: the numbers a human measured are not re-derived from a model
    that was shown the same plate. A `measure` ruling replaces it. Neither can
    write `calibrated_from` or the working dims -- both are the STAGE's, derived
    below and after the sweep respectively, because `VALUE_SCHEMA["S3"]` is
    closed over seven keys and carries neither.
    """
    findings = []
    groups = collections.OrderedDict()
    prior = (existing.get("groups") or {})
    for group in universe:
        ruling = (effective.get(group) or {}).get("ruling")
        verdict = (ruling or {}).get("verdict")
        value = (ruling or {}).get("value") or {}
        if verdict == "measure":
            rot = value.get("rot", 0)
            entry = collections.OrderedDict([
                ("windows", dict(value.get("windows") or {})),
                ("frame_windows", dict(value.get("frame_windows") or {})),
                ("rot", rot),
                ("working_size", list(derived_size(rot))),
                ("flavor_before_text", bool(value.get("flavor_before_text", False))),
                ("measured_from", list(value.get("measured_from") or [])),
                ("window_margin_px", value.get("window_margin_px")),
                # Filled in after the sweep; a `measure` that never got a
                # clearance reading records the honest label rather than a number.
                ("calibrated_from", "COL_GAP"),
            ])
            # `protect` in the manifest is a list of NAMES, not rectangles
            # (VALUE_SCHEMA["S3"]), so it cannot become a protect rect here. The
            # rects stay whatever the catalogue already carried for the group.
            for key in ("protect", "protect_no_victory", "protect_with_victory"):
                if isinstance(prior.get(group), dict) and prior[group].get(key):
                    entry[key] = [list(r) for r in prior[group][key]]
            groups[group] = entry
        elif verdict == "inherit" or (verdict is None and group in prior):
            if not isinstance(prior.get(group), dict):
                findings.append("%s: ruled `inherit` but the catalogue carries no "
                                "usable entry to inherit from" % group)
                continue
            groups[group] = collections.OrderedDict(prior[group])
        else:
            findings.append("%s: ruled %r, so no layout was produced for it"
                            % (group, verdict or "nothing"))
    # Groups the catalogue already carries that this corpus does not use are kept
    # verbatim: the file is the SET's catalogue, not this run's, and dropping a
    # group because one scenario did not print it would silently narrow it.
    for group in sorted(prior):
        if group not in groups and isinstance(prior[group], dict):
            groups[group] = collections.OrderedDict(prior[group])
    return collections.OrderedDict([
        ("schema_version", kc.SCHEMA_VERSION),
        ("generated_by", "koreanize mask"),
        ("generated_at", kc.utc_now()),
        ("col_gap", int(existing.get("col_gap", col_gap) or col_gap)),
        ("groups", groups),
    ]), findings


# ---------------------------------------------------------------------------
# 8. The human gate -- the contact sheet (§5.7, one of the four)
# ---------------------------------------------------------------------------

GATE_STUB = """# mask gate -- {slug}

status: pending
reviewer:
date:
gate_for: mask
bound_sha256: {bound}

`mask` derived {faces} masks across {groups} layout groups. Every pixel `erase`
regenerates and every pixel `composite` restores is decided by these rectangles,
and the failure this gate exists for is SILENT: a window narrower than the text
it bounds leaves English in KEEP, where no erase statistic can see it.

Set `status:` to `accepted` (or `rejected`), record who decided and when.
`erase` and `composite` refuse at exit 30 until this reads `accepted`.

- contact sheet: `{sheet}`
- mask manifest: `{manifest}`
- layout catalogue: `data/layouts/{layout}`

## Walk the contact sheet

An unticked item is an unperformed review, by the same rule that makes an
unnamed gate an unperformed one.

- [ ] every printed line of every face is inside a CLEAR rectangle, INCLUDING
      its last glyph -- the `71006` defect is a tail, not a missing line
- [ ] no CLEAR rectangle reaches the plate outline, the stat arc or the artwork
- [ ] the frame text (type banner, stage label) is cleared where it is printed
- [ ] `Location` faces show the UNREVEALED side's title on `/front`
- [ ] `Act` / `Agenda` masks are landscape-correct after the rot-90 round trip
- [ ] every warning below has been looked at on the actual face

## Uncalibrated groups

{uncalibrated}

## Coverage residual (W1 / W2)

{residual}

## Faces with a warning

{warnings}
"""


def parse_gate(path):
    """The gate file's front-matter, as a dict. Absent file -> a pending record."""
    record = {"status": "pending", "reviewer": None, "date": None,
              "bound_sha256": None}
    if not os.path.exists(path):
        return record
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for key in ("status", "reviewer", "date", "bound_sha256"):
                prefix = "%s:" % key
                if line.startswith(prefix):
                    record[key] = line[len(prefix):].strip() or None
            if line.startswith("## "):
                break
    return record


def write_gate_stub(out_dir, slug, gate, counts, entries, layout, residual_check,
                    sheet_rel, manifest_rel, layout_name):
    """The gate stub is GENERATED, never blank: an unnamed gate is an unperformed
    gate (§5.7).

    The three variable sections are the three things a reviewer cannot get from
    the images alone: which groups are calibrated only by inheritance, which
    residuals fired and whether they were already ruled, and which faces printed
    a field the detector found no ink for.
    """
    uncal = uncalibrated_groups(layout)
    if uncal:
        uncal_text = "\n".join(
            "- `%s` -- **uncalibrated**: no defect-free face, so "
            "`window_margin_px` is inherited from `COL_GAP` rather than measured"
            % group for group in uncal)
    else:
        uncal_text = ("_(none -- every group's `window_margin_px` is at or below "
                      "a clearance measured on one of its own defect-free faces)_")
    residual_lines = ["- reproduced from the lock baseline: %d"
                      % residual_check.get("reproduced_baseline", 0),
                      "- novel, requiring adjudication: %d"
                      % residual_check.get("novel", 0)]
    residual_lines += ["  - %s" % line for line in
                       (residual_check.get("detail") or [])[:20]]
    warn_lines = ["- `%s` (%s): %s" % (entry["file"], entry["group"],
                                       "; ".join(entry["warnings"]))
                  for entry in entries if entry.get("warnings")]
    path = os.path.join(out_dir, "gates", "mask-gate.md")
    kc.atomic_write_text(path, GATE_STUB.format(
        slug=slug, bound=gate.get("bound_sha256") or "",
        faces=counts.get("faces", 0), groups=counts.get("groups", 0),
        sheet=sheet_rel, manifest=manifest_rel, layout=layout_name,
        uncalibrated=uncal_text,
        residual="\n".join(residual_lines),
        warnings="\n".join(warn_lines[:40]) or "_(none)_"))
    return path


CONTACT_SHEET = """<!doctype html>
<meta charset="utf-8">
<title>koreanize mask contact sheet -- {slug}</title>
<style>
 body {{ font: 13px/1.4 -apple-system, sans-serif; background: #181818; color: #ddd;
        margin: 16px; }}
 h1 {{ font-size: 16px; }}
 .grid {{ display: flex; flex-wrap: wrap; gap: 12px; }}
 figure {{ margin: 0; width: 260px; background: #222; padding: 8px;
           border-radius: 4px; }}
 .pair {{ position: relative; width: 244px; height: 342px; background: #000; }}
 .pair img {{ position: absolute; left: 0; top: 0; width: 244px; height: 342px; }}
 .pair img.mask {{ opacity: .55; }}
 figcaption {{ font-size: 11px; margin-top: 6px; word-break: break-all; }}
 .warn {{ color: #ffb454; }}
 .hit {{ color: #ff6b6b; }}
</style>
<h1>koreanize <code>mask</code> -- {slug} ({faces} faces, {groups} groups)</h1>
<p>Each tile is the English slice with its mask overlaid: the pale areas are
KEEP (alpha 255, restored from the slice), the dark ones are CLEAR (alpha 0,
regenerated). Read the last glyph of every line.</p>
<div class="grid">
{tiles}
</div>
"""

TILE = """<figure>
 <div class="pair">
  <img src="{slice_src}" alt="{name} slice">
  <img class="mask" src="{mask_src}" alt="{name} mask">
 </div>
 <figcaption><b>{name}</b><br>{group} &middot; cleared {cleared:.1%}
 {warnings}{hits}</figcaption>
</figure>"""


def write_contact_sheet(out_dir, slug, entries, counts):
    """The artifact the human gate is a gate ON. Written inside <run_dir>."""
    tiles = []
    for entry in entries:
        warnings = ("<br><span class=\"warn\">%s</span>"
                    % "; ".join(entry["warnings"])) if entry["warnings"] else ""
        hits = ("<br><span class=\"hit\">%d coverage hit(s)</span>"
                % len(entry["hits"])) if entry["hits"] else ""
        tiles.append(TILE.format(
            slice_src=os.path.join("..", "slices", entry["slice_file"]),
            mask_src=os.path.join("..", "masks", entry["file"]),
            name=entry["file"][:-4], group=entry["group"],
            cleared=entry["cleared_fraction"], warnings=warnings, hits=hits))
    path = os.path.join(out_dir, "galleries", "mask-contact-sheet.html")
    kc.atomic_write_text(path, CONTACT_SHEET.format(
        slug=slug, faces=counts.get("faces", 0), groups=counts.get("groups", 0),
        tiles="\n".join(tiles)))
    return path


# ---------------------------------------------------------------------------
# 9. The re-derivation comparison (§6 step 12's last sentence)
# ---------------------------------------------------------------------------

def compare_manifests(reference, current):
    """Per-face sha256 differences between a recorded mask set and this one.

    "Re-derives Midwinter's masks from step 8's data and compares" is a claim
    about BYTES, so it is checked on bytes. Three outcomes are distinguished
    because they mean different things: a face whose mask MOVED is the
    interesting one; a face only the reference has is a face this run did not
    produce; a face only this run has is new material.
    """
    ref = {}
    for entry in (reference or {}).get("entries") or []:
        if entry.get("file"):
            ref[entry["file"]] = entry.get("sha256")
    now = dict((e["file"], e["sha256"]) for e in current)
    findings = []
    for name in sorted(set(ref) & set(now)):
        if ref[name] and ref[name] != now[name]:
            findings.append("%s: derived %s, recorded %s"
                            % (name, now[name][:16], (ref[name] or "")[:16]))
    for name in sorted(set(ref) - set(now)):
        findings.append("%s: recorded but not derived by this run" % name)
    for name in sorted(set(now) - set(ref)):
        findings.append("%s: derived but absent from the reference" % name)
    return findings


def read_manifest(path):
    if not path or not os.path.exists(path):
        return None
    doc, err = kd._read_json(path)
    if err:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the comparison manifest is unreadable", err)
    return doc


def assert_masks_from_disk(masks_dir, entries):
    """RE-READ EVERY WRITTEN FILE and assert what was claimed about it.

    The house rule (§7, "assert from disk, never measure"): the sha256 in the
    manifest is the one the file on disk hashes to, the alpha is BINARY -- so
    `composite`'s B3 (`keep | clear` covers every pixel, `keep & clear` is empty)
    holds by construction -- and the emitted frame is 750x1050 whatever the
    group's working orientation was.
    """
    from PIL import Image
    import numpy as np
    findings = []
    for entry in entries:
        path = os.path.join(masks_dir, entry["file"])
        if not os.path.exists(path):
            findings.append("%s: was not written" % entry["file"])
            continue
        if kc.sha256_file(path) != entry["sha256"]:
            findings.append("%s: sha256 on disk differs from the manifest's"
                            % entry["file"])
        with Image.open(path) as image:
            if image.mode != "RGBA":
                findings.append("%s: mode is %s, expected RGBA"
                                % (entry["file"], image.mode))
            if image.size != (CELL_W, CELL_H):
                findings.append("%s: is %dx%d, expected %dx%d"
                                % (entry["file"], image.size[0], image.size[1],
                                   CELL_W, CELL_H))
            alpha = np.asarray(image.getchannel("A"))
        stray = np.unique(alpha[(alpha != 0) & (alpha != 255)])
        if stray.size:
            findings.append("%s: alpha is not binary (%d stray values, first %d)"
                            % (entry["file"], int(stray.size), int(stray[0])))
    return findings


# ---------------------------------------------------------------------------
# 10. The stage
# ---------------------------------------------------------------------------

def _material_for(cfg, run_dir, layout_file, workspace=None):
    """Everything the agent is given, COPIED. It holds no repository path.

    The reference catalogue is included whenever one exists, because `inherit` is
    unstatable without it: a verdict that cites an entry the agent was never
    shown is a verdict rule 4 cannot check.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    material = {}
    if os.path.exists(layout_file):
        material["layouts-reference.json"] = layout_file
    prior_manifest = os.path.join(run_dir, "masks", "manifest.json")
    if os.path.exists(prior_manifest):
        material["masks-manifest.json"] = prior_manifest
    for name in ("card-text-en.json", "card-source-en.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            material[name] = path
    return material


def _load_corpus(run_dir):
    """(records, cards). `card-source-en.json` is preferred and `card-text-en.json`
    is the fallback: build-masks.py measured against the SOURCE index, whose
    `back_*` fields are what the Location inversion reads."""
    records = read_slice_records(run_dir)
    doc = None
    for name in ("card-source-en.json", "card-text-en.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            doc = kx._load_json(path, name)
            break
    if doc is None:
        kc.refuse(kc.EXIT_PRECONDITION, "no English card index",
                  "%s carries neither card-source-en.json nor card-text-en.json"
                  % run_dir)
    return records, card_index(doc)


def run_mask(run_dir, mode="build", replay=None, ask_dir=None, claude_bin=None,
             workspace=None, readonly_trees=(), quiet=False,
             accept_mask_residual=False, escalate=True, reviewer=None,
             compare_to=None, out_dir=None, write_data=True):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. <run_dir>/slices/manifest.json exists and carries records[]
                                                                   -> exit 13
      4. an English card index is on disk                          -> exit 13
      5. the derived group universe is non-empty                   -> exit 13
      6. the ask bundle's policy, prompt and schema are on disk     -> exit 13
      7. the assembled layout catalogue's shape and its
         `1 <= window_margin_px <= calibrated_from <= 64` bound     -> exit 4
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    out_dir = out_dir or run_dir
    kc.check_invocation_guards([run_dir, out_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)
    slices_dir = os.path.join(run_dir, "slices")
    manifest_path = os.path.join(run_dir, "slices", "manifest.json")

    records, cards = _load_corpus(run_dir)
    universe, units, plan = group_universe(records, cards)

    layout_file = layout_path(cfg, workspace)
    existing, on_disk = read_layout(layout_file)
    # Validated AS READ, before a single group is assembled from it: a catalogue
    # that cannot be relied on is a named exit 4 here rather than a traceback
    # three functions deeper, and the operator's next move is the same either way.
    unconsumed = assert_layout(existing, layout_file) if on_disk else set()
    lock, baseline = read_lock(cfg, workspace)

    # ---- S3. `mask` is a REQUIRED-AI stage: there is no no-AI path, and a
    # build report with `ai: null` is refused at exit 11 by write_report.
    material = _material_for(cfg, run_dir, layout_file, workspace)
    if ask_dir is None:
        ask_dir = ka.build_bundle(cfg, SID, universe, units, material=material,
                                  prompt=PROMPT, workspace=workspace,
                                  extra={"slices_dir": os.path.relpath(slices_dir,
                                                                       workspace)})
    if replay:
        ka.seed_from_replay(ask_dir, replay)
    ask = ka.run_bundle(ask_dir, invoke=not replay, claude_bin=claude_bin,
                        readonly_trees=readonly_trees, workspace=workspace,
                        quiet=quiet)

    triggered, checks = [], []
    decide_report, effective = None, {}
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
            for ruling in merged.get("rulings") or []:
                effective[ruling.get("unit_id")] = {"ruling": ruling}

    layout, layout_findings = layout_from_rulings(existing, universe, effective)
    # M1 -- every group in the corpus has a layout entry. Without one there is no
    # window, and without a window there is no measurement at all: the mask would
    # be an all-KEEP rectangle and `composite` would restore the English face.
    checks.append({"id": "M1", "name": "every_group_has_a_layout",
                   "status": "fail" if layout_findings else "pass",
                   "exit_on_fail": kc.EXIT_PRECONDITION, "detail": layout_findings})
    if layout_findings:
        triggered.append(kc.EXIT_PRECONDITION)

    # ---- The build. Two passes over each face, and the order is load-bearing:
    # the mask is derived first, then W1/W2 measure the WINDOWS against it. The
    # reverse would have nothing to measure containment in.
    col_gap = int(layout.get("col_gap") or DEFAULT_COL_GAP)
    defect_faces = baseline_faces(baseline)
    import numpy as np
    masks_dir = os.path.join(out_dir, "masks")
    entries, hits, clearances = [], [], {}
    specs = {}
    for record, group, type_name, fields in plan:
        spec = specs.get(group)
        if spec is None:
            spec = group_spec(layout, group)
            specs[group] = spec
        if spec is None or not spec["windows"]:
            continue
        name = record["filename"]
        if name.lower().endswith(".png"):
            name = name[:-4]
        face = str(record.get("arkham_id") or name)
        gray = load_slice_gray(os.path.join(slices_dir, record["filename"]),
                               spec["rot"])
        ink = kx.ink_mask(gray)
        regions, warnings = build_regions(ink, spec, fields, col_gap=col_gap)
        alpha = render_alpha(spec, regions, fields)
        mask = mask_image(alpha, spec["rot"])
        out_path = os.path.join(masks_dir, name + ".png")
        if not os.path.isdir(masks_dir):
            os.makedirs(masks_dir)
        mask.save(out_path)

        alpha_working = np.asarray(alpha)
        cleared = int((np.asarray(mask.getchannel("A")) == 0).sum())
        face_hits = coverage_hits(ink, gray, alpha_working == 0, spec, group, face)
        hits.extend(face_hits)
        clearances.setdefault(group, {})[face] = trailing_clearance(ink, spec)

        entries.append(collections.OrderedDict([
            ("arkham_id", record.get("arkham_id")),
            ("side", record.get("side")),
            ("face", face),
            ("group", group),
            ("type_name", type_name),
            ("file", name + ".png"),
            ("slice_file", record["filename"]),
            ("width", mask.width), ("height", mask.height), ("mode", mask.mode),
            ("alpha_convention",
             "alpha 0 = clear (regenerate); alpha 255 = keep (restore from the "
             "English slice)"),
            ("orientation", ("stored rotated 90 CW; regions measured upright"
                             if spec["rot"] else "upright")),
            ("fields_present", dict((k, present(fields, k)) for k in REGION_KEYS)),
            ("regions", dict((k, [list(r) for r in v])
                             for k, v in sorted(regions.items()))),
            ("regions_slice_coords",
             dict((k, [to_slice(r, spec["rot"]) for r in v])
                  for k, v in sorted(regions.items()))),
            ("region_bbox", dict((k, list(union_box(v)))
                                 for k, v in sorted(regions.items()))),
            ("protected", [list(p) for p in protect_rects(spec, fields)]),
            ("cleared_pixels", cleared),
            ("cleared_fraction", round(cleared / float(CELL_W * CELL_H), 5)),
            ("sha256", kc.sha256_file(out_path)),
            ("warnings", warnings),
            ("hits", [h["detail"] for h in face_hits]),
        ]))

    # ---- `calibrated_from` is derived AFTER the sweep, per group, and only for
    # the groups this run measured: an `inherit` keeps the catalogue's recorded
    # negative evidence rather than overwriting it with this corpus's.
    for group in universe:
        entry = (layout.get("groups") or {}).get(group)
        ruling = (effective.get(group) or {}).get("ruling") or {}
        if not isinstance(entry, dict) or ruling.get("verdict") != "measure":
            continue
        entry["calibrated_from"] = calibrate(clearances.get(group, {}),
                                             defect_faces.get(group, set()))
    # The bound, owned by kz_config, applied again to the file that will be
    # WRITTEN -- the assembled catalogue is not the one read, so re-checking it is
    # not a repetition.
    unconsumed |= assert_layout(layout, layout_file)

    # M2 -- no empty mask. A face that printed something and cleared nothing is
    # not a conservative mask, it is a mask that will restore the English face.
    empty = [e["file"] for e in entries
             if not e["regions"] and any(e["fields_present"].values())]
    checks.append({"id": "M2", "name": "no_empty_mask",
                   "status": "fail" if empty else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": ["%s: printed fields but no region was measured" % f
                              for f in empty]})
    if empty:
        triggered.append(kc.EXIT_ARTIFACT)

    # M3 -- assert from disk, never measure.
    disk = assert_masks_from_disk(masks_dir, entries)
    checks.append({"id": "M3", "name": "masks_reread_from_disk",
                   "status": "fail" if disk else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT, "detail": disk})
    if disk:
        triggered.append(kc.EXIT_ARTIFACT)

    # M4 -- the re-derivation comparison (§6 step 12). The reference is
    # `--compare-to` when given and otherwise the manifest this run is about to
    # replace, so a re-run of a finished Midwinter run compares byte for byte.
    reference = read_manifest(compare_to or os.path.join(run_dir, "masks",
                                                         "manifest.json"))
    drift = compare_manifests(reference, entries) if reference else []
    checks.append({"id": "M4", "name": "rederivation_matches_reference",
                   "status": "fail" if drift else "pass",
                   "exit_on_fail": kc.EXIT_DRIFT,
                   "detail": (drift[:40] if drift else
                              [] if reference else
                              ["no reference manifest; nothing to compare against"])})
    if drift:
        triggered.append(kc.EXIT_DRIFT)

    # ---- W0. STEP 16: the hit set reaches a process exit through kz_checkers.
    residual, residual_exit = residual_gate(hits, baseline,
                                           accepted=accept_mask_residual)
    novel = [h for h in hits if kx.baseline_key(h) not in
             set(kx.baseline_key(e) for e in baseline)]

    triage_report, escalation = None, None
    tolerance_hits, defect_hits, unruled_hits = [], [], list(novel)
    if novel and escalate:
        triage_report, _triage_ask = kt.escalate(
            cfg, escalation_gate(novel), run_dir, mode=mode, replay=replay,
            claude_bin=claude_bin, workspace=workspace, quiet=quiet)
        if triage_report["exit_code"] != kc.EXIT_OK:
            triggered.append(triage_report["exit_code"])
        tolerance_hits, defect_hits, unruled_hits = partition_by_ruling(
            novel, triage_report)
        escalation = {
            "delegated_to": kt.STAGE, "stage_id": kt.SID,
            "report": os.path.relpath(
                kc.report_path(run_dir, kt.STAGE, triage_report["mode"]), workspace),
            "escalated": len(novel),
            "ruled_tolerance": len(tolerance_hits),
            "ruled_defect": len(defect_hits),
            "unruled": len(unruled_hits),
            "decide_outcome": (triage_report.get("ai") or {}).get("decide_outcome"),
        }
    checks.append(residual)
    triggered.extend(residual_exit)

    # W3 -- the flag cannot launder a defect. `--accept-mask-residual` records a
    # decision to EXTEND THE BASELINE after triage; a hit triage ruled `defect`
    # is a window that has to be re-measured, and 67 outranks 23 in
    # EXIT_PRECEDENCE so the report names the cause rather than the symptom.
    checks.append({"id": "W3", "name": "no_residual_ruled_a_defect",
                   "status": "fail" if defect_hits else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": [h["detail"] for h in defect_hits[:20]]})
    if defect_hits:
        triggered.append(kc.EXIT_ARTIFACT)

    # ---- Counts, manifest, gate.
    by_group = {}
    for entry in entries:
        by_group[entry["group"]] = by_group.get(entry["group"], 0) + 1
    counts = {
        "faces": len(entries),
        "groups": len(by_group),
        "groups_in_universe": len(universe),
        "faces_by_group": dict(sorted(by_group.items())),
        "uncalibrated_groups": len(uncalibrated_groups(layout)),
        # W1 IS INERT IN THESE, so a clean W1 result says nothing about them.
        # Counted beside the hits for exactly that reason (design 5.5).
        "unasserted_groups": len(unasserted_groups(layout)),
        "unasserted_group_names": unasserted_groups(layout),
        "w1_hits": sum(1 for h in hits if h["check"] == "W1"),
        "w2_hits": sum(1 for h in hits if h["check"] == "W2"),
        "residual_reproduced": residual["reproduced_baseline"],
        "residual_novel": residual["novel"],
        "baseline_entries": len(baseline),
        "warnings": sum(1 for e in entries if e["warnings"]),
        "batches": ask["batch_of"],
        "batches_complete": sum(1 for b in ask["batches"] if b["complete"]),
    }

    mask_manifest = collections.OrderedDict([
        ("schema_version", kc.SCHEMA_VERSION),
        ("generated_by", "koreanize mask"),
        ("generated_at", kc.utc_now()),
        ("slug", cfg["slug"]),
        ("layout_set", cfg.get("layout_set") or cfg["slug"]),
        ("col_gap", col_gap),
        ("counts", counts),
        ("per_type", per_type_block(layout, by_group)),
        ("entries", entries),
        ("hits", hits),
    ])
    manifest_out = os.path.join(masks_dir, "manifest.json")
    if mode == "build":
        kc.atomic_write_json(manifest_out, mask_manifest)

    bound = kc.sha256_bytes(kc.json_bytes(mask_manifest))
    gate_path = os.path.join(out_dir, "gates", "mask-gate.md")
    prior_gate = parse_gate(gate_path)
    prev_report, _err = kd._read_json(kc.report_path(run_dir, STAGE, "build"))
    prev_review = ((prev_report or {}).get("gate") or {})
    sha_moved = bool(prior_gate.get("bound_sha256")
                     and prior_gate["bound_sha256"] != bound)
    gate = kc.carry_review(prior_gate, prev_review, sha_moved, kc.utc_now())
    gate["gate_for"] = STAGE
    gate["bound_sha256"] = bound

    sheet = write_contact_sheet(out_dir, cfg["slug"], entries, counts)
    write_gate_stub(out_dir, cfg["slug"], gate, counts, entries, layout, residual,
                    os.path.relpath(sheet, out_dir),
                    os.path.relpath(manifest_out, out_dir), layout_relpath(cfg))

    # ---- The two durable writes, both through kz_config's mirror writer. This
    # module declares no filesystem write root outside <run_dir> and cannot reach
    # one, which is what keeps §4.1's "writes outside <run_dir>: no" column
    # literally true for this stage.
    written = []
    if write_data and mode == "build" and not triggered:
        written.append(kz.write_data(cfg, "layouts", layout_relpath(cfg), layout,
                                     workspace=workspace))
    additions = []
    if (write_data and mode == "build" and accept_mask_residual and tolerance_hits):
        when = kc.utc_now()[:10]
        who = reviewer or os.environ.get("USER") or "unknown"
        additions = [baseline_entry(h, who, when) for h in tolerance_hits]
        written.append(kz.write_data(cfg, "locks", "%s.lock.json" % cfg["slug"],
                                     extend_baseline(lock, additions),
                                     workspace=workspace))

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
        "escalation": escalation,
    }

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path, manifest_path]
                                 + ([layout_file] if os.path.exists(layout_file)
                                    else [])),
        freshness=kc.build_freshness(
            [manifest_path],
            upstream_report_path=kc.report_path(run_dir, "slice", "build")),
        ai=ai_block, gate=gate,
        accepted={TOLERANCE: bool(accept_mask_residual)},
        results=({"layout": layout,
                  "residual_baseline_additions": additions,
                  "unconsumed_layout_keys": sorted(unconsumed),
                  "masks": [{"file": e["file"], "group": e["group"],
                             "sha256": e["sha256"]} for e in entries]}
                 if entries else None))
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    if written:
        report["write_set"] = [{"path": os.path.relpath(p, workspace),
                                "action": "create"} for p in written]
    return report, ask_dir


def per_type_block(layout, by_group):
    """The per-group summary `masks/manifest.json` carries -- the windows AS THE
    MASK STAGE CONSUMED THEM, which is what W1/W2 are handed on the next run."""
    out = collections.OrderedDict()
    for group in sorted(by_group):
        spec = group_spec(layout, group)
        if spec is None:
            continue
        out[group] = collections.OrderedDict([
            ("slices", by_group[group]),
            ("rot", spec["rot"]),
            ("working_size", list(spec["size"])),
            ("windows", dict((k, list(v)) for k, v in spec["windows"].items())),
            ("frame_windows", dict((k, list(v))
                                   for k, v in spec["frame_windows"].items())),
            ("window_margin_px", spec["window_margin_px"]),
            ("calibrated_from", spec["calibrated_from"]),
            ("measured_from", list(spec["measured_from"])),
        ])
    return out


def residual_gate(hits, baseline, accepted=False):
    """THE ACTIVATION, in one function: hits -> a report check -> a process exit.

    §6 step 16 is not "run the checkers", it is "put them in `mask`'s gate set",
    and this is what that means concretely. `kz_checkers.mask_residual_check`
    decides pass/fail as a SUPERSET test against the lock baseline; this attaches
    the exit code, and `kz_common.pick_exit` does the rest. Kept as one function
    so the selftest can assert the exit and not merely the check.
    """
    check = kx.mask_residual_check(hits, baseline, accepted=accepted)
    return check, ([] if check["status"] == "pass" else [kc.EXIT_MASK_RESIDUAL])


# ---------------------------------------------------------------------------
# 11. --status -- read-only, and it prints the weaker claims as weaker
# ---------------------------------------------------------------------------

def status_lines(run_dir, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    lines = []
    scenario_path = os.path.join(run_dir, "scenario.json")
    if not os.path.exists(scenario_path):
        return ["no scenario.json at %s" % run_dir]
    cfg = kz.load_scenario(scenario_path, check_pin=False)
    path = layout_path(cfg, workspace)
    layout, present_on_disk = read_layout(path)
    lines.append("layout catalogue : %s%s"
                 % (path, "" if present_on_disk else "  (absent -- S3 has not "
                                                     "measured this set yet)"))
    groups = layout.get("groups") or {}
    lines.append("groups           : %d" % len(groups))
    for group in sorted(groups):
        spec = groups[group] if isinstance(groups[group], dict) else {}
        calibrated = spec.get("calibrated_from")
        # THE WEAKER CLAIM STAYS VISIBLE. A group calibrated from COL_GAP has no
        # defect-free face and its threshold is inherited, not measured; printing
        # the literal 34 beside it would read like a measurement nobody made.
        if spec.get("window_margin_px") is None and spec.get("uncalibrated") is True:
            # A THIRD STATE, and the one most easily mistaken for a pass: there
            # is no threshold at all, so W1 never runs here and this group's
            # windows are unpoliced. Saying "calibrated_from 0" would be true and
            # thoroughly misleading.
            note = ("UNASSERTED -- tightest trailing clearance on a defect-free "
                    "face is 0, so no positive threshold survives it; W1 does "
                    "NOT run in this group (a finding for step 16, design 5.5)")
        elif calibrated == "COL_GAP":
            note = ("UNCALIBRATED (no defect-free face; window_margin_px "
                    "inherited from COL_GAP)")
        else:
            note = "calibrated_from %s" % calibrated
        lines.append("  %-18s window_margin_px %-4s rot %-4s %s"
                     % (group, spec.get("window_margin_px"), spec.get("rot", 0),
                        note))
    _lock, baseline = read_lock(cfg, workspace)
    lines.append("residual baseline: %d entry(ies) in %s"
                 % (len(baseline), os.path.relpath(lock_path(cfg, workspace),
                                                   workspace)))
    for entry in baseline[:20]:
        lines.append("  %s %s/%s %s" % (entry.get("check"), entry.get("group"),
                                        entry.get("window_name"), entry.get("face")))
    report, err = kd._read_json(kc.report_path(run_dir, STAGE, "build"))
    if err or not report:
        lines.append("last run         : none")
    else:
        lines.append("last run         : %s (exit %s), gate %s, consumable %s"
                     % (report.get("verdict"), report.get("exit_code"),
                        (report.get("gate") or {}).get("status"),
                        report.get("consumable")))
    lines.extend(kc.pending_summary())
    return lines


# ---------------------------------------------------------------------------
# 12. --selftest -- every fault, with NO CORPUS PRESENT
# ---------------------------------------------------------------------------
#
# §5.5's rule, in this module's terms: W1/W2 is the highest-value automation in
# the design and it must not be the one predicate whose only evidence is a 2.1 GB
# gitignored tree. Every case below runs on generated pixels or pure data, so a
# machine with no golden corpus still proves the tolerance fires on the fault it
# names.

def _named_fault_hits(faces=("71006", "71005")):
    """The `mask-residual` row's named fault, synthetically.

    `kz_checkers.w1_cases()["positive"]` is a band stopping **27 px** short of
    its window's trailing edge -- 71006's own measured clearance -- inside a 540
    px window declared as `Act/front` + `body`, which is the real group and the
    real window name. Re-labelling it onto the two known faces reproduces exactly
    the tuple the lock baselines, with no corpus anywhere.
    """
    template = kx.run_w1_case(kx.w1_cases()["positive"])
    out = []
    for face in faces:
        for hit in template:
            copy = dict(hit)
            copy["face"] = face
            out.append(copy)
    return out


def _baseline_for(hits):
    return [collections.OrderedDict([("check", h["check"]), ("group", h["group"]),
                                     ("window_name", h["window_name"]),
                                     ("face", h["face"])]) for h in hits]


def _executable_source(fn):
    """One function's source with its DOCSTRING removed.

    The `w2` circularity guard is an assertion about what the code reaches for,
    and every one of these functions explains in prose that it must not reach for
    `regions_slice_coords`. Scanning the raw source would therefore fail on the
    comment that documents the rule -- which is the check punishing the only
    place the rule is written down.
    """
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    body = tree.body[0].body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body.pop(0)
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


def _ordering_fixture():
    """A group whose frame window sits INSIDE a protect rectangle -- which is the
    only configuration in which the A6b ordering is observable at all."""
    spec = {
        "group": "Test/front",
        "windows": {"body": (20, 200, 400, 300)},
        "frame_windows": {"stage_label": (30, 30, 200, 80)},
        "protect": [(0, 0, 300, 120)],
        "protect_no_victory": [], "protect_with_victory": [],
        "has_victory_variants": False,
        "rot": 0, "size": (CELL_W, CELL_H), "measured_from": [],
        "window_margin_px": 34, "calibrated_from": 34,
        "flavor_before_text": False,
    }
    regions = {"stage_label": [(35, 40, 150, 70)]}
    return spec, regions, {}, (100, 55)


def selftest(fault=None, verbose=True):
    """Prove each refusal and the one tolerance fire on the fault they target."""
    import numpy as np
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
                findings.append("%s: expected exit %d, got %d" % (label, code, exc.code))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    if "mask-residual" in wanted:
        cases = kx.w1_cases()
        # HALF ONE OF THE NAMED FAULT: the 27 px band -- 71006's measurement --
        # fires at window_margin_px 34, and its sibling at 40 px does not.
        if not kx.run_w1_case(cases["positive"]):
            findings.append("mask-residual: the 27 px band did not fire at "
                            "window_margin_px %d" % kx.COL_GAP)
        if kx.run_w1_case(cases["negative"]):
            findings.append("mask-residual: the 40 px band fired; a checker with "
                            "no negative is a checker that cannot fail")
        # HALF TWO: the synthetically narrowed window. Same pixels, window 40 px
        # narrower -- so the predicate reads the WINDOW and not a constant.
        if not kx.run_w1_case(cases["sensitivity"]):
            findings.append("mask-residual: narrowing the declared window by 40 px "
                            "did not turn the negative into a positive")
        # And the band-height filter, which is vacuous on every one-band case.
        if kx.run_w1_case(cases["band-height"]):
            findings.append("mask-residual: the full-width plate at 3x text height "
                            "was not discarded by the band-height filter")
        if not kx.run_w1_case(cases["band-height"], filtered=False):
            findings.append("mask-residual: the unfiltered comparison did not fire "
                            "on the plate, so the filter's absence is undetectable")

        hits = _named_fault_hits()
        faces = sorted(set(h["face"] for h in hits))
        if faces != ["71005", "71006"]:
            findings.append("mask-residual: the synthetic fault is not keyed on "
                            "the two known faces (%s)" % faces)
        if any((h["group"], h["window_name"]) != ("Act/front", "body") for h in hits):
            findings.append("mask-residual: the synthetic fault is not keyed on "
                            "Act/front + body")
        baseline = _baseline_for(hits)
        check, triggered = residual_gate(hits, baseline)
        if check["status"] != "pass" or triggered:
            findings.append("mask-residual: the two BASELINED hits did not pass "
                            "(%s, triggered %s)" % (check["status"], triggered))
        if check["reproduced_baseline"] != len(hits):
            findings.append("mask-residual: the baselined hits were not counted as "
                            "reproduced (%d of %d)"
                            % (check["reproduced_baseline"], len(hits)))
        # A THIRD FACE INSIDE AN ALREADY-BASELINED GROUP STILL FIRES. This is the
        # whole reason the key is a four-tuple and not a group.
        third = dict(hits[0])
        third["face"] = "71099"
        check, triggered = residual_gate(hits + [third], baseline)
        if check["status"] != "fail" or triggered != [kc.EXIT_MASK_RESIDUAL]:
            findings.append("mask-residual: a third face in a baselined group did "
                            "not reach exit %d (%s, %s)"
                            % (kc.EXIT_MASK_RESIDUAL, check["status"], triggered))
        # ... and a W1 hit is not suppressed by a W2 baseline row on the same
        # face, which is the other half of "never on `face` alone".
        w2_row = dict(baseline[0])
        w2_row["check"] = "W2"
        check, _t = residual_gate([hits[0]], [w2_row])
        if check["status"] != "fail":
            findings.append("mask-residual: a W2 baseline row suppressed a W1 hit "
                            "on the same face")
        check, triggered = residual_gate(hits + [third], baseline, accepted=True)
        if check["status"] != "pass" or triggered:
            findings.append("mask-residual: --accept-mask-residual did not absorb "
                            "the novel hit")
        # The flag records a decision made AFTER triage, so a hit triage ruled a
        # defect must not be absorbable by it.
        report = {"results": {"rulings": [
            {"unit_id": unit_id_for(third), "verdict": "defect"}]}}
        tol, defect, unruled = partition_by_ruling([third], report)
        if defect != [third] or tol or unruled:
            findings.append("mask-residual: a triage `defect` ruling was not kept "
                            "out of the baseline-extension set")
        report["results"]["rulings"][0]["verdict"] = "tolerance"
        tol, defect, unruled = partition_by_ruling([third], report)
        if tol != [third]:
            findings.append("mask-residual: a triage `tolerance` ruling did not "
                            "reach the baseline-extension set")
        entry = baseline_entry(third, "shanash", "2026-08-18")
        if kx.baseline_key(entry) != kx.baseline_key(third):
            findings.append("mask-residual: the baseline entry does not key to the "
                            "hit it was minted from")
        merged = extend_baseline({"stages": {"scaffold": {}}}, [entry, entry])
        if len(merged["mask"]["residual_baseline"]) != 1:
            findings.append("mask-residual: extend_baseline did not de-duplicate "
                            "on the key")
        if "scaffold" not in (merged.get("stages") or {}):
            findings.append("mask-residual: extending the baseline dropped another "
                            "owner's block from the receipt")

    if "w2" in wanted:
        cases = kx.w2_cases()
        hits = kx.run_w2_case(cases["overhang"])
        if not hits:
            findings.append("w2: a line box overhanging CLEAR by 12 px did not fire")
        elif hits[0]["overhang_right"] != cases["overhang"]["overhang_right"]:
            findings.append("w2: the overhang was attributed as right %d, expected %d"
                            % (hits[0]["overhang_right"],
                               cases["overhang"]["overhang_right"]))
        if kx.run_w2_case(cases["contained"]):
            findings.append("w2: a box entirely inside CLEAR fired")
        # THE CIRCULARITY GUARD (§5.5 property 1). W2 is given the layout WINDOWS
        # and a DECLARED mask, never the padded region rects the mask itself
        # produced -- asserting containment against those would be detector
        # attempt #4. Checked on the source, because the defect is an argument
        # nobody passes rather than a value anybody can observe.
        for fn in (checked_windows, coverage_hits):
            if "regions_slice_coords" in _executable_source(fn):
                findings.append("w2: %s reaches for regions_slice_coords, which is "
                                "the mask's own output" % fn.__name__)
        spec, _regions, _fields, _probe = _ordering_fixture()
        windows = checked_windows(spec)
        if sorted(windows) != ["body", "frame:stage_label"]:
            findings.append("w2: the checked window set is %s, expected the "
                            "layout's windows AND frame_windows" % sorted(windows))

    if "ordering" in wanted:
        # THE A6b FAULT, PLANTED AND REQUIRED TO BE CAUGHT. `protect` covers the
        # frame window; with the subtraction the frame text is CLEAR, without it
        # protection repaints it KEEP and the English survives underneath.
        spec, regions, fields, (px, py) = _ordering_fixture()
        correct = np.asarray(render_alpha(spec, regions, fields))
        faulted = np.asarray(render_alpha(spec, regions, fields,
                                          subtract_frame=False))
        if correct[py][px] != 0:
            findings.append("ordering: with frame_windows subtracted from protect "
                            "the frame text is not CLEAR (alpha %d)"
                            % correct[py][px])
        if faulted[py][px] != 255:
            findings.append("ordering: the planted fault did not reproduce -- "
                            "painting protect without the subtraction left the "
                            "frame text CLEAR, so this selftest proves nothing")
        if int((correct != faulted).sum()) == 0:
            findings.append("ordering: the correct and faulted masks are identical")
        kept = protect_rects(spec, fields)
        if any(r[0] <= px < r[2] and r[1] <= py < r[3] for r in kept):
            findings.append("ordering: protect_rects still claims the frame window")
        if not any(r[0] <= px < r[2] and r[1] <= py < r[3]
                   for r in protect_rects(spec, fields, subtract_frame=False)):
            findings.append("ordering: the fixture's protect rect does not cover "
                            "the frame window, so the fault is unobservable")
        # Protection is still painted LAST everywhere it applies: a region rect
        # overlapping a protected area must NOT survive.
        spec2 = dict(spec)
        spec2["frame_windows"] = {}
        over = np.asarray(render_alpha(spec2, {"title": [(35, 40, 150, 70)]}, fields))
        if over[py][px] != 255:
            findings.append("ordering: a region rect survived protection, so A6's "
                            "ordering is inverted")

    if "layout" in wanted:
        # The bound is kz_config's and is exercised THROUGH it, not re-stated.
        for margin, calibrated, should_fire in ((0, 34, True), (1, 34, False),
                                                (34, 34, False), (64, 64, False),
                                                (65, 65, True), (40, 27, True)):
            layout = {"col_gap": 34, "groups": {"G": {
                "window_margin_px": margin, "calibrated_from": calibrated}}}
            fired = bool(kz.check_window_margin(layout, "<selftest>"))
            if fired != should_fire:
                findings.append("layout: window_margin_px %d against "
                                "calibrated_from %d %s"
                                % (margin, calibrated,
                                   "did not refuse" if should_fire else "refused"))
        # "COL_GAP" leaves only the numeric bound, and the group reads as
        # uncalibrated rather than as a measurement.
        layout = {"col_gap": 34, "groups": {"Act/front": {
            "window_margin_px": 34, "calibrated_from": "COL_GAP"}}}
        if kz.check_window_margin(layout, "<selftest>"):
            findings.append("layout: an uncalibrated group refused its own "
                            "inherited COL_GAP")
        if uncalibrated_groups(layout) != ["Act/front"]:
            findings.append("layout: an uncalibrated group is not reported as one")
        # Malformed catalogues are NAMED, never a traceback.
        for broken, why in (
                ({"groups": []}, "groups is a list"),
                ({"groups": {"Act/front": {"windows": {"body": [1, 2, 3]}}}},
                 "a window with three numbers"),
                ({"groups": {"Act/front": {"windows": {"body": [0, 0, 10, 10]},
                                           "rot": 45}}}, "an unsupported rot"),
                ({"groups": {"Act/front": {}}}, "a group with no windows"),
                ({"col_gap": "wide", "groups": {}}, "a non-integer col_gap"),
                ({"groups": {"Act": {"windows": {"body": [0, 0, 10, 10]},
                                     "window_margin_px": 34,
                                     "calibrated_from": 34}}},
                 "a group name that is not <Type>/<side>")):
            if not validate_layout(broken, "<selftest>")[0]:
                findings.append("layout: %s was accepted" % why)
        fires("layout (assert_layout refuses)", kc.EXIT_GUARD,
              lambda: assert_layout({"groups": []}, "<selftest>"))
        fires("layout (unreadable file)", kc.EXIT_GUARD,
              lambda: read_layout(os.path.join(kc.PACKAGE_DIR, "policy.md")))
        # The working dims FOLLOW from the slice frame and rot; a second
        # declaration is a second thing that can drift.
        if derived_size(90) != (CELL_H, CELL_W) or derived_size(0) != (CELL_W, CELL_H):
            findings.append("layout: the working dims are not derived from rot")
        if validate_layout({"groups": {"Act/front": {
                "windows": {"body": [0, 0, 10, 10]}, "rot": 90,
                "working_size": [750, 1050], "window_margin_px": 34,
                "calibrated_from": 34}}}, "<selftest>")[0] == []:
            findings.append("layout: working dims disagreeing with rot were "
                            "accepted")
        # An unknown key is REPORTED and not fatal.
        keys = validate_layout({"col_gap": 34, "groups": {"Act/front": {
            "windows": {"body": [0, 0, 10, 10]}, "rot": 0,
            "window_margin_px": 34, "calibrated_from": 34,
            "measured_notes": "prose"}}}, "<selftest>")
        if keys[0] or keys[1] != {"measured_notes"}:
            findings.append("layout: an unknown key was not reported as "
                            "unconsumed (%s / %s)" % (keys[0], sorted(keys[1])))
        # to_slice is build-masks.py:840's transform and nothing else.
        if to_slice((26, 192, 558, 666), 90) != [CELL_W - 666, 26, CELL_W - 192, 558]:
            findings.append("layout: to_slice does not reproduce "
                            "build-masks.py:840")
        fires("layout (to_slice rot 180)", kc.EXIT_GUARD,
              lambda: to_slice((0, 0, 1, 1), 180))
        # Location front is the UNREVEALED side.
        card = {"type_name": "Location", "name": "Art Gallery",
                "back_name": "Ground-Floor Room"}
        group, _t, fields = printed_side({"side": "face"}, card)
        if group != "Location/front" or fields["title"] != "Ground-Floor Room":
            findings.append("layout: Location/front is not the unrevealed side")
        group, _t, fields = printed_side({"side": "back"}, card)
        if group != "Location/back" or fields["title"] != "Art Gallery":
            findings.append("layout: Location/back is not the revealed side")

    if "gate" in wanted:
        import tempfile
        record = parse_gate(os.path.join(os.sep, "nonexistent", "mask-gate.md"))
        if record["status"] != "pending":
            findings.append("gate: an absent gate did not read as pending")
        carried = kc.carry_review({"status": "accepted", "reviewer": "me",
                                   "date": "2026-08-24"},
                                  {"status": "accepted"}, True, "now")
        if carried["status"] != "pending" or not carried.get("superseded"):
            findings.append("gate: a moved bound_sha256 did not supersede the "
                            "acceptance (%r)" % carried)
        if kc.carry_review({"status": "accepted"}, {"status": "accepted"},
                           False, "now")["status"] != "accepted":
            findings.append("gate: an unmoved acceptance was not held")
        # THE STUB IS GENERATED, NEVER BLANK, and it names what to look at.
        layout = {"col_gap": 34, "groups": {"Act/front": {
            "windows": {"body": [26, 192, 558, 666]}, "rot": 90,
            "window_margin_px": 34, "calibrated_from": "COL_GAP"}}}
        entry = {"file": "71006-front.png", "group": "Act/front",
                 "warnings": ["printed flavor but no ink measured"], "hits": []}
        check, _t = residual_gate(_named_fault_hits(), [])
        temp = tempfile.mkdtemp(prefix="kz-mask-selftest-")
        path = write_gate_stub(temp, "midwinter",
                              {"bound_sha256": "d" * 64}, {"faces": 88, "groups": 15},
                              [entry], layout, check, "galleries/x.html",
                              "masks/manifest.json", "midwinter.json")
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        for needle in ("status: pending", "gate_for: mask", "bound_sha256: " + "d" * 64,
                       "UNREVEALED", "71006-front.png"):
            if needle not in text:
                findings.append("gate: the stub does not carry %r" % needle)
        if text.count("- [ ]") < 6:
            findings.append("gate: the stub carries %d review items; an unnamed "
                            "gate is an unperformed gate" % text.count("- [ ]"))
        if "**uncalibrated**" not in text:
            findings.append("gate: the stub does not tell the reviewer which "
                            "groups are uncalibrated")
        if "novel, requiring adjudication: %d" % check["novel"] not in text:
            findings.append("gate: the stub does not carry the coverage residual")
        import shutil
        shutil.rmtree(temp, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 13. CLI
# ---------------------------------------------------------------------------

_SUMMARY = {
    "mask-residual": "the 27 px band of the 71006/71005 faces fires at "
                     "window_margin_px 34 and the 40 px sibling does not, a "
                     "synthetically narrowed window turns the negative into a "
                     "positive, the baselined pair passes while a third face in "
                     "the same group still reaches exit 23, and a hit triage "
                     "ruled `defect` cannot be absorbed by the flag",
    "w2": "a line box overhanging CLEAR by 12 px fires and is attributed to the "
          "right edge, one entirely inside does not, and the predicate is given "
          "the layout windows and a declared mask rather than the mask's own "
          "output rects",
    "ordering": "frame_windows subtracted from protect BEFORE protection is "
                "painted leaves the frame text CLEAR; the planted fault without "
                "the subtraction repaints it KEEP, and protection is still the "
                "last word everywhere else",
    "layout": "the 0/1/64/65 boundaries and window_margin_px > calibrated_from "
              "refuse through kz_config, an uncalibrated group reads as one, a "
              "malformed catalogue is named rather than a traceback, and "
              "Location/front is the unrevealed side",
    "gate": "an absent gate reads pending, a moved bound_sha256 supersedes a "
            "live acceptance, and the stub is generated with its review items, "
            "its uncalibrated groups and its coverage residual named",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_mask.py",
        description="koreanize stage S3 -- the layout catalogue, the masks, and "
                    "the W1/W2 coverage assertion (design §6 steps 12 and 16).",
        epilog="exit codes: 0 ok, 2 usage/guard, 4 config or layout refusal, "
               "11 policy, 13 precondition, 14 re-derivation drift, 23 mask "
               "residual outside the baseline, 25 AI budget, 65 claude "
               "unavailable, 66 manifest invalid, 67 artifact failed")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--ask-dir", help="re-use an existing ai/mask/<stamp>/")
    parser.add_argument("--replay", metavar="DIR",
                        help="adjudicate a recorded run offline: no claude, no "
                             "credential, no cost")
    parser.add_argument("--readonly-tree", action="append", default=[],
                        help="a tree the agent must not move; repeatable (rule 8c)")
    parser.add_argument("--claude-bin", help="override the shim's binary resolution")
    parser.add_argument("--compare-to", metavar="MANIFEST",
                        help="a masks/manifest.json to re-derive against; defaults "
                             "to the one already in <run_dir>")
    parser.add_argument("--accept-mask-residual", action="store_true",
                        help="record the human decision to extend "
                             "mask.residual_baseline[] with the hits triage(S6) "
                             "ruled `tolerance` (exit 23 otherwise)")
    parser.add_argument("--reviewer", help="who accepted; recorded in the baseline "
                                           "entry's accepted_by")
    parser.add_argument("--no-escalate", action="store_true",
                        help="do not invoke triage(S6); novel residuals stay "
                             "unadjudicated")
    parser.add_argument("--status", action="store_true",
                        help="read-only: the catalogue, its uncalibrated groups, "
                             "the lock baseline and the last run")
    parser.add_argument("--dry-run", action="store_true",
                        help="write into <run_dir>/dry-run/mask/")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_mask --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-14s %s" % (name, _SUMMARY[name]))
        for line in kc.pending_summary():
            print("  %s" % line)
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    if args.status:
        for line in status_lines(run_dir):
            print(line)
        return kc.EXIT_OK

    mode = "build"
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"
    elif args.replay:
        mode = "replay"

    out_dir = run_dir
    if args.dry_run:
        out_dir = os.path.join(run_dir, "dry-run", STAGE)
        kz.assert_dry_run_dest(kz.load_scenario(os.path.join(run_dir,
                                                             "scenario.json")),
                               STAGE, out_dir)

    report, _ask_dir = run_mask(
        run_dir, mode=mode, replay=args.replay, ask_dir=args.ask_dir,
        claude_bin=args.claude_bin, readonly_trees=args.readonly_tree,
        quiet=args.quiet, accept_mask_residual=args.accept_mask_residual,
        escalate=not args.no_escalate, reviewer=args.reviewer,
        compare_to=args.compare_to, out_dir=out_dir,
        write_data=(mode == "build"))

    path = kc.write_report(report, out_dir)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize mask -- %s" % report["slug"])
        print("  faces           : %d in %d group(s)"
              % (counts["faces"], counts["groups"]))
        print("  uncalibrated    : %d group(s)" % counts["uncalibrated_groups"])
        print("  coverage        : W1 %d, W2 %d (baseline %d, reproduced %d, "
              "novel %d)" % (counts["w1_hits"], counts["w2_hits"],
                             counts["baseline_entries"],
                             counts["residual_reproduced"], counts["residual_novel"]))
        print("  gate            : %s" % report["gate"]["status"])
        for check in report["checks"]:
            print("  %-24s: %s  %s" % (check["name"], check["status"],
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
