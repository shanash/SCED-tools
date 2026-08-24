#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `composite` -- build `cleared/`, then prove it from disk (design §5.4, §6 step 13).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER` -- it decodes PNGs with numpy, and `/usr/bin/python3`
(3.9.6) has PIL 10.4.0 and no numpy at all.

WHAT IT BUILDS, IN ONE LINE
---------------------------
    cleared/<face>.png = where(mask.alpha == 255, slice_rgb, delivered_rgb)

`slice` produced the English pixels, `mask` declared which of them the erase
stage was allowed to touch, and `erase` produced `delivered/`. This stage is the
arithmetic that puts the two back together, and then the assertion that the
arithmetic actually happened.

THIS IS NOT A DISGUISED `cp`, AND THE RECORD SAYS SO IN ITS OWN VOICE
---------------------------------------------------------------------
`composite-cleared.py:34-45` records that on the Midwinter corpus `cleared/` came
out **pixel-identical to `delivered/` on all 88 faces**, because `delivered/`
already preserved its unmasked pixels. That is the expected result and it is
REPORTED (`counts.pixel_identical_to_delivered`) rather than hidden. The
assertion's value is that it survives a re-erase by a tool that does NOT preserve
unmasked pixels -- an external image AI, a different LaMa checkpoint, a vendor
package that came back re-encoded. On such an input this stage is the only thing
between a silently altered card face and the atlas.

PNG re-encoding is not byte-preserving, so `counts.byte_identical_to_delivered`
is expected to be 0. The claim made everywhere is *"PIXEL-identical outside the
mask"*, never *"byte-identical"* (`composite-cleared.py:41-45`).

THE CENTRAL INVARIANT -- ASSERTED FROM DISK, NOT FROM THE ARRAY
----------------------------------------------------------------
After the write, every output file is **re-opened and re-decoded**, and the two
assertions are evaluated against those bytes:

    A1 (exit 20)  cleared[keep]  == slice[keep]       -- outside-mask identity
    A2 (exit 21)  cleared[clear] == delivered[clear]  -- inside-mask fidelity

Asserting the in-memory array would prove that `numpy.where` works, which nobody
doubts. Asserting the decoded file proves that the thing on disk -- after the
encoder, after the atomic replace, after whatever the filesystem did -- is what
the contract says. Those are different claims and only the second one is worth
making. `--verify-only` runs exactly the same two assertions and skips only the
write, so the stage can re-prove a tree it did not build.

B3 IS WHY A1 AND A2 TOGETHER LEAVE NO PIXEL UNCONSTRAINED
----------------------------------------------------------
`keep` and `clear` are derived from a mask alpha that B3 forces binary. On a
binary alpha `keep | clear` covers every pixel and `keep & clear` is empty, so
the two assertions PARTITION the output: there is no third population that
neither one examines. Drop B3 and an alpha of 128 belongs to neither set -- the
run still passes, and the pixels nobody checked are exactly the ones a bad
encoder would have moved. B8 restates the consequence as an integer identity
(`keep + clear == width * height`) so the property is checked rather than argued.

NO THRESHOLD, NO TOLERANCE, NO DOWNGRADE FLAG -- BY CONSTRUCTION
-----------------------------------------------------------------
§5.4: *"No threshold, no tolerance, no downgrade flag."* One differing pixel on
one face fails the whole run. `composite` therefore has **no row in
`kz_common.TOLERANCES`** and must not grow one: a `--accept-*` here would be an
acceptance of "the output is not the arithmetic it claims to be", which is not a
measurement anybody can rule on. The absence is mechanical, not editorial --
`kz_common.assert_tables_total()` would fail the day a `composite` row appeared
without a flag, and `koreanize.sh` refuses an unregistered `--accept-*` at 71.

MASK POLARITY IS DECLARED, NOT ASSUMED
---------------------------------------
`masks/manifest.json.alpha_convention` must say `0 = CLEAR` / `255 = KEEP`, mode
`RGBA`. The record's `masks-inverted/` tree is an **L-mode** tree with the
OPPOSITE polarity, produced solely as LaMa's white-is-repaint input
(`invert-masks.py`); compositing with it inverts every card -- English text kept,
artwork erased. `composite-cleared.py:14-19` refuses it three independent ways
and this module keeps all three: P5 (the convention must be declared), P2 (the
mask directory must not BE the inverted one) and B2 (each mask FILE must decode
`RGBA`). A2 is the assertion an inverted composite fails.

NO AI, ENFORCED (§5.4)
-----------------------
`composite` is one of the eleven forbidden-AI stages, so `kc.declare_ai(STAGE,
required=False)` runs in the module body: a `scenario.json` that does not list it
in `ai.forbidden_stages` refuses at exit 4 the moment a config is bound, and
`kc.write_report` refuses a build report carrying a non-null `ai` block. The
prohibition is code, not a comment.

EXIT CODES (§4.2)
   0  every face composited, A1 and A2 exact on every one of them
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin, or a planned write outside <run_dir>
  13  precondition -- a manifest, the declared polarity, an upstream report that
       is not consumable, a per-face input surprise (B1..B8), or a --verify-only
       run over an absent cleared/
  20  A1 -- a re-read KEEP pixel differs from the English slice
  21  A2 -- a re-read CLEAR pixel differs from delivered/
  67  the cleared/ tree itself is not the expected set of PNGs at the expected
       geometry (the record's A3, remapped: 22 belongs to the tolerance family and
       `composite` has no tolerance)

13 IS DELIBERATELY BELOW 20 AND 21 IN `EXIT_PRECEDENCE`, and the reasoning is
`composite-cleared.py:60-63` verbatim: *"such an input does not merely coexist
with an identity failure -- it causes it. Telling an operator 'outside-mask
identity failed' when the real answer is 'you handed it an L-mode inverted mask'
would send them to the wrong file."* koreanize gets that ordering from
`kc.pick_exit` rather than from a `min()`, because its table places 4 and 11
below 13 and 62/65/66/67 above 20/21 -- under `min()` the rationale would invert
on half the pairs (§4.2).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402

import numpy as np  # noqa: E402
import PIL  # noqa: E402
from PIL import Image  # noqa: E402

STAGE = "composite"

# Declared in the MODULE BODY (§3.2, §5.4). `composite` is one of the eleven
# forbidden-AI stages, so this is the registration AND the assertion: bind a
# scenario.json that does not name it in ai.forbidden_stages and the process
# refuses at exit 4 before it has read a pixel.
kc.declare_ai(STAGE, required=False)

FAULTS = ("a1", "a2", "b3", "polarity")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: The alpha values a mask may carry. Binary is not a convenience -- it is what
#: makes `keep | clear` total and `keep & clear` empty (B3, and the docstring).
KEEP_ALPHA = 255
CLEAR_ALPHA = 0


# ===========================================================================
# 1. The write guard -- re-asserted internally, exit 4 before any read
# ===========================================================================
#
# `guard.write_roots` names the SCED-downloads langpack destinations and nothing
# else (kz_init.py:1205-1231), so `kz.assert_write_paths` is the wrong instrument
# here: this stage declares NO write root outside <run_dir> and must not be able
# to reach one. The property to assert is therefore containment in <run_dir>,
# plus the same `guard.forbidden` prefix test the shared evaluator applies, and
# it runs BEFORE the first read so that "nothing was read" is literally true on a
# refusal (§4.2 code 4).

def assert_inside_run_dir(cfg, run_dir, paths, workspace=None):
    """Every planned write resolves inside <run_dir>. Refuses at exit 4.

    Symlink-safe by construction: both sides are `realpath`'d, so a `cleared`
    symlink pointing at SCED-downloads is refused rather than followed.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    root = os.path.realpath(str(run_dir))
    findings = []
    for path in paths:
        real = os.path.realpath(str(path))
        if real != root and not real.startswith(root + os.sep):
            findings.append("%s resolves to %s, which is outside <run_dir> %s"
                            % (path, real, root))
            continue
        rel = os.path.relpath(real, os.path.realpath(workspace)).replace(os.sep, "/")
        for bad in (cfg.get("guard") or {}).get("forbidden") or ():
            if rel.startswith(bad) or ("/" + rel).find("/" + bad) >= 0:
                findings.append("%s matches guard.forbidden %r" % (rel, bad))
                break
    if findings:
        kc.refuse(kc.EXIT_GUARD,
                  "%s planned a write outside <run_dir>" % STAGE,
                  "; ".join(findings[:kz.GUARD_FINDING_SAMPLE]))


# ===========================================================================
# 2. The inputs -- two manifests, one declared polarity
# ===========================================================================

def load_json(path, label):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is absent" % label, path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        kc.refuse(kc.EXIT_PRECONDITION, "%s is not valid JSON" % label,
                  "%s: %s" % (path, exc))


#: TWO MANIFEST SHAPES, ONE READER, AND THE REASON IS NOT TIDINESS.
#:
#: `kz_slice.py` / `kz_mask.py` emit `records[]` + `entries[]` with a per-entry
#: `alpha_convention` STRING; the Midwinter record -- which is the golden fixture
#: this stage's A1/A2 are checked against (§6 step 8) -- emits `records[]` +
#: `masks[]` with a top-level `alpha_convention` DICT. Both are legitimate inputs
#: to `composite`, and a reader that understood only the live one would make the
#: regression instrument unrunnable, which is the one thing §5.8 exists to
#: prevent. So the shape is normalised at the door and nothing below this section
#: knows there were ever two.

def mask_records(manifest):
    """The mask entries, under either key. Never both -- a manifest carrying both
    is malformed and is reported as such by `masks_polarity_findings`."""
    if isinstance(manifest.get("masks"), list):
        return manifest["masks"]
    if isinstance(manifest.get("entries"), list):
        return manifest["entries"]
    return []


def declared_mask_count(manifest):
    counts = manifest.get("counts") or {}
    for key in ("masks", "faces"):
        if isinstance(counts.get(key), int):
            return counts[key]
    return None


def slice_geometry(record):
    """(width, height) for one slice record, or None.

    `measured_width`/`measured_height` is the record's shape and is preferred
    because it is a MEASUREMENT of the emitted file; `cell_pixels` is
    `kz_slice.py`'s and is the geometry it derived from the atlas. Preferring the
    measurement is deliberate: on a `single_card` atlas the two agree, and where
    they would not, the file on disk is the thing this stage composites.
    """
    if record.get("measured_width") and record.get("measured_height"):
        return (int(record["measured_width"]), int(record["measured_height"]))
    cell = record.get("cell_pixels")
    if isinstance(cell, (list, tuple)) and len(cell) == 2 and all(cell):
        return (int(cell[0]), int(cell[1]))
    return None


def slices_self_consistent(manifest):
    """P4 -- `slices/manifest.json` agrees with itself.

    Ported from `composite-cleared.py:295-320`. A manifest that disagrees with
    its own counts is a manifest whose face set nobody can state, and every
    downstream population count is derived from it.
    """
    findings = []
    records = manifest.get("records") or []
    counts = manifest.get("counts") or {}
    face = int(counts.get("face_slices_emitted", -1))
    back = int(counts.get("back_slices_emitted", -1))
    if face >= 0 and back >= 0 and len(records) != face + back:
        findings.append("manifest records %d != face %d + back %d"
                        % (len(records), face, back))
    got_face = sum(1 for r in records if r.get("side") == "face")
    got_back = sum(1 for r in records if r.get("side") == "back")
    if face >= 0 and back >= 0 and (got_face, got_back) != (face, back):
        findings.append("side split %d face / %d back != counts %d / %d"
                        % (got_face, got_back, face, back))
    names = [r.get("filename") for r in records]
    if len(names) != len({(n or "").lower() for n in names}):
        findings.append("duplicate filenames when case-folded")
    ids = [(r.get("arkham_id"), r.get("side")) for r in records]
    if len(ids) != len(set(ids)):
        findings.append("duplicate (arkham_id, side) pairs")
    for rec in records:
        if not rec.get("filename"):
            findings.append("a record carries no filename")
            break
        if slice_geometry(rec) is None:
            # PER RECORD, not corpus-wide: a scenario may legitimately carry two
            # atlas cell sizes, and each face is checked against its OWN declared
            # geometry below. What is never legitimate is a record that declares
            # none, because then there is nothing to assert the mask and the
            # delivered face against.
            findings.append("%s declares neither measured_width/height nor "
                            "cell_pixels" % rec["filename"])
            break
    if not records:
        findings.append("no records -- refusing a vacuous pass over an empty face set")
    return findings


def _declares_clear_keep(text):
    """Does one prose `alpha_convention` string declare 0 = CLEAR / 255 = KEEP?

    A substring test over a lowered string, and not a regex: the two live
    producers word it differently around the same two claims
    (`kz_mask.py:1593-1595` writes "alpha 0 = clear (regenerate); alpha 255 =
    keep ..."), and what has to be asserted is the CLAIM, not the sentence.
    """
    low = " ".join(str(text or "").lower().split())
    return ("0 = clear" in low and "255 = keep" in low)


def masks_polarity_findings(manifest):
    """P5 -- the mask manifest DECLARES the polarity this module assumes.

    `masks-inverted/` has no manifest at all, *"which is itself the signal"*
    (`composite-cleared.py:335-336`), so this check is what stands in when a
    manifest IS present and says the wrong thing.

    Both declaration shapes are accepted (see the normaliser above): a top-level
    `alpha_convention` DICT, or a per-entry prose string plus a per-entry `mode`.
    Neither is preferred; what is refused is a manifest that declares NOTHING,
    because an undeclared polarity is one the compositor would be assuming.
    """
    findings = []
    records = mask_records(manifest)
    conv = manifest.get("alpha_convention")

    if isinstance(conv, dict):
        if not str(conv.get("0", "")).upper().startswith("CLEAR"):
            findings.append("alpha_convention['0'] does not declare CLEAR: %r"
                            % conv.get("0"))
        if not str(conv.get("255", "")).upper().startswith("KEEP"):
            findings.append("alpha_convention['255'] does not declare KEEP: %r"
                            % conv.get("255"))
        if conv.get("mode") != "RGBA":
            findings.append("alpha_convention['mode'] is %r, expected 'RGBA' -- "
                            "masks-inverted/ is L-mode with the OPPOSITE polarity"
                            % conv.get("mode"))
    else:
        undeclared = [m.get("file") for m in records
                      if not _declares_clear_keep(m.get("alpha_convention"))]
        if undeclared:
            findings.append("%d mask record(s) declare no 0=CLEAR / 255=KEEP "
                            "convention, e.g. %s -- and the manifest carries no "
                            "top-level alpha_convention either"
                            % (len(undeclared), undeclared[:3]))
        wrong_mode = [m.get("file") for m in records
                      if m.get("mode") and m["mode"] != "RGBA"]
        if wrong_mode:
            findings.append("%d mask record(s) declare mode != RGBA, e.g. %s -- "
                            "masks-inverted/ is L-mode with the OPPOSITE polarity"
                            % (len(wrong_mode), wrong_mode[:3]))

    declared = declared_mask_count(manifest)
    if isinstance(declared, int) and declared != len(records):
        findings.append("counts declares %d masks, the manifest carries %d records"
                        % (declared, len(records)))
    if isinstance(manifest.get("masks"), list) and isinstance(
            manifest.get("entries"), list):
        findings.append("the manifest carries BOTH `masks` and `entries` -- which "
                        "one is the face set is exactly what a compositor may not "
                        "guess")
    nameless = sum(1 for m in records if not m.get("file"))
    if nameless:
        findings.append("%d mask record(s) name no file" % nameless)
    if not records:
        findings.append("no mask records")
    return findings


def manifests_agree(slices_manifest, masks_manifest):
    """P6 -- the two manifests name the same face set, case-folded.

    Case-folded because the volume is case-INSENSITIVE: two records differing
    only in case are one file on disk, and a set comparison that missed that
    would report agreement on a tree that cannot hold both.
    """
    want = {(r.get("filename") or "").lower() for r in slices_manifest.get("records") or []}
    got = {(m.get("file") or "").lower() for m in mask_records(masks_manifest)}
    if want == got:
        return []
    missing = sorted(want - got)
    extra = sorted(got - want)
    return ["slices/ and masks/ manifests disagree on the file set: "
            "%d only in slices (%s), %d only in masks (%s)"
            % (len(missing), missing[:3], len(extra), extra[:3])]


# ===========================================================================
# 3. B3 -- the binary-alpha property, and its consequence
# ===========================================================================

def partition(alpha):
    """(keep, clear, findings) for one mask alpha plane.

    B3 is evaluated BEFORE the populations are used, because on a non-binary
    alpha the two populations do not partition the frame and A1/A2 would then
    both pass while leaving pixels nobody examined. That is the defect this
    function exists to make impossible rather than to document.
    """
    findings = []
    binary = bool(((alpha == CLEAR_ALPHA) | (alpha == KEEP_ALPHA)).all())
    if not binary:
        stray = sorted({int(v) for v in np.unique(alpha)}
                       - {CLEAR_ALPHA, KEEP_ALPHA})
        findings.append(
            "B3 mask alpha is not confined to {%d, %d}: %d stray value(s), e.g. %s "
            "-- keep|clear would not cover the frame and A1/A2 would leave those "
            "pixels unconstrained" % (CLEAR_ALPHA, KEEP_ALPHA, len(stray), stray[:5]))
    keep = alpha == KEEP_ALPHA
    clear = alpha == CLEAR_ALPHA
    return keep, clear, findings


def totality(keep, clear):
    """B8 -- B3's consequence stated as an integer identity, and B4's anti-vacuity.

    Both live here because they are the same claim from two directions: B8 says
    no pixel is outside the partition, B4 says neither half is empty. A mask that
    is all-KEEP passes A2 on zero pixels, which is a pass nobody should be able
    to buy by handing in an empty mask.
    """
    n_keep, n_clear = int(keep.sum()), int(clear.sum())
    total = int(keep.size)
    findings = []
    if n_keep + n_clear != total:
        findings.append("B8 keep %d + clear %d != %d pixels"
                        % (n_keep, n_clear, total))
    if bool((keep & clear).any()):
        findings.append("B8 keep and clear overlap -- the partition is not disjoint")
    if not (n_keep and n_clear):
        findings.append("B4 degenerate mask: keep %d px, clear %d px -- one "
                        "assertion would pass on zero pixels" % (n_keep, n_clear))
    return n_keep, n_clear, findings


# ===========================================================================
# 4. The arithmetic, and the assertion that reads it back off the disk
# ===========================================================================

def composite_face(slice_rgb, delivered_rgb, keep):
    """where(mask.alpha == 255, slice, delivered) -- the whole of §5.4's rule.

    `keep[..., None]` broadcasts (H, W, 1) over (H, W, 3), so the selection is
    PER PIXEL and not per channel: a mask can never keep the red channel of a
    pixel and clear its blue.
    """
    return np.where(keep[..., None], slice_rgb, delivered_rgb)


def read_rgb(path):
    with Image.open(path) as im:
        size, mode, fmt = im.size, im.mode, im.format
        arr = np.asarray(im.convert("RGB"))
    return arr, size, mode, fmt


def assert_from_disk(path, slice_rgb, delivered_rgb, keep, clear):
    """A1 and A2, evaluated against the DECODED FILE. Never against the array.

    Returns a dict; the caller maps `a1_bad` to exit 20 and `a2_bad` to exit 21.

    int16, not uint8, on both differences: uint8 subtraction wraps, so a
    255 -> 0 change would report as a delta of 1 and the worst possible
    corruption would read as the mildest (`composite-cleared.py:706-707`).
    """
    out = {"a1_bad": None, "a1_max_delta": None, "a1_first_xy": None,
           "a2_bad": None, "a2_max_delta": None, "a2_first_xy": None,
           "size": None, "mode": None, "format": None, "bytes": None,
           "sha256": None, "pixel_identical_to_delivered": None, "error": None}
    try:
        arr, size, mode, fmt = read_rgb(path)
        out["size"] = list(size)
        out["mode"] = mode
        out["format"] = fmt
        out["bytes"] = os.path.getsize(path)
        out["sha256"] = kc.sha256_file(path)
    except (OSError, ValueError) as exc:
        out["error"] = "re-read failed: %s" % exc
        return out
    if arr.shape != slice_rgb.shape:
        out["error"] = "re-read shape %r != %r" % (arr.shape, slice_rgb.shape)
        return out

    diff_s = np.abs(arr.astype(np.int16) - slice_rgb.astype(np.int16))
    bad_out = (diff_s.max(axis=-1) > 0) & keep
    n_out = int(bad_out.sum())
    out["a1_bad"] = n_out
    out["a1_max_delta"] = int(diff_s[bad_out].max()) if n_out else 0
    if n_out:
        ys, xs = np.nonzero(bad_out)
        out["a1_first_xy"] = [int(xs[0]), int(ys[0])]

    diff_d = np.abs(arr.astype(np.int16) - delivered_rgb.astype(np.int16))
    bad_in = (diff_d.max(axis=-1) > 0) & clear
    n_in = int(bad_in.sum())
    out["a2_bad"] = n_in
    out["a2_max_delta"] = int(diff_d[bad_in].max()) if n_in else 0
    if n_in:
        ys, xs = np.nonzero(bad_in)
        out["a2_first_xy"] = [int(xs[0]), int(ys[0])]

    out["pixel_identical_to_delivered"] = bool(np.array_equal(arr, delivered_rgb))
    return out


def write_png(path, array):
    """Atomic: a sibling temp file plus `os.replace`, matching `sced_io`'s
    convention. An interrupted run never leaves a truncated PNG that the next
    run's A3 would classify as `corrupt` and the operator would read as a
    compositing defect."""
    tmp = path + ".tmp"
    Image.fromarray(array, mode="RGB").save(tmp, format="PNG")
    os.replace(tmp, path)


# ===========================================================================
# 5. A3 -- the cleared/ tree itself (exit 67, deliberately not 22)
# ===========================================================================

def classify_entry(path, name):
    """Five steps, first failure wins. Ported verbatim in behaviour from
    `composite-cleared.py:238-262`, which took it from
    `verify-delivered-inventory.py`."""
    if not os.path.isfile(path) or os.path.islink(path):
        return "not a regular file"
    if not name.lower().endswith(".png"):
        return "extension"
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != PNG_MAGIC:
                return "magic"
    except OSError:
        return "unreadable"
    try:
        with Image.open(path) as im:
            if im.format != "PNG":
                return "container"
    except Exception:
        return "container"
    try:
        # verify() consumes the file object, so this must be a SECOND open.
        with Image.open(path) as im:
            im.verify()
    except Exception:
        return "corrupt"
    return None


def tree_check(cleared_dir, records, geometry_by_file):
    """The output directory AS IT IS, not as the loop believes it left it.

    A3 in the record exited 22. Here it is **67**: 22 is uniformly "a
    tolerance-bearing predicate fired and its --accept-<name> was not given"
    (§4.1), `composite` has no tolerance row, and reusing 22 for a structural
    failure would put a code in a stage report that `kc.row_by_flag` can find no
    row for -- an operator following §4.2 would go looking for a table entry that
    does not exist.
    """
    findings = []
    try:
        names = sorted(os.listdir(cleared_dir))
    except OSError:
        names = []
    not_png, png_names = [], []
    for name in names:
        if name.endswith(".tmp"):
            not_png.append({"name": name, "reason": "leftover temp file"})
            continue
        reason = classify_entry(os.path.join(cleared_dir, name), name)
        if reason is None:
            png_names.append(name)
        else:
            not_png.append({"name": name, "reason": reason})
    if not_png:
        findings.append("A3 %d entr(ies) under cleared/ are not well-formed PNGs: %s"
                        % (len(not_png), not_png[:5]))

    bad_geom = []
    for name in png_names:
        try:
            with Image.open(os.path.join(cleared_dir, name)) as im:
                size, mode, fmt = im.size, im.mode, im.format
        except Exception as exc:
            bad_geom.append({"name": name, "reason": "unreadable: %s" % exc})
            continue
        # Each file against ITS OWN record's declared geometry. An unexpected
        # file has no record, so it has no geometry to be right about -- the set
        # comparison below is what names it, and naming it twice for two reasons
        # would send the operator looking for two defects.
        want = geometry_by_file.get(name)
        if want is None:
            continue
        if tuple(size) != tuple(want) or mode != "RGB" or fmt != "PNG":
            bad_geom.append({"name": name, "size": list(size), "expected": list(want),
                             "mode": mode, "format": fmt})
    if bad_geom:
        findings.append("A3 %d file(s) are not their declared geometry in RGB PNG: %s"
                        % (len(bad_geom), bad_geom[:5]))

    # Set comparison in BOTH directions, case-folded -- the volume is
    # case-insensitive, so `71006.PNG` and `71006.png` are one file and a
    # one-directional check would call that agreement.
    expected = {}
    for rec in records:
        expected.setdefault((rec["filename"] or "").lower(), []).append(rec["filename"])
    found = {}
    for name in png_names:
        found.setdefault(name.lower(), []).append(name)
    missing = sorted(set(expected) - set(found))
    extra = sorted(set(found) - set(expected))
    case = sorted(k for k in set(expected) & set(found)
                  if expected[k][0] != found[k][0])
    dup = sorted(k for k, v in found.items() if len(v) > 1)
    if missing:
        findings.append("A3 %d expected face(s) absent from cleared/: %s"
                        % (len(missing), [expected[k][0] for k in missing[:5]]))
    if extra:
        findings.append("A3 %d unexpected file(s) under cleared/: %s"
                        % (len(extra), [found[k][0] for k in extra[:5]]))
    if case:
        findings.append("A3 %d file(s) differ from the manifest only in case: %s"
                        % (len(case), [(expected[k][0], found[k][0]) for k in case[:5]]))
    if dup:
        findings.append("A3 %d case-folded duplicate(s) under cleared/: %s"
                        % (len(dup), [found[k] for k in dup[:5]]))
    return findings


# ===========================================================================
# 6. The stage
# ===========================================================================

#: `composite` requires all three (kz_config.PREDECESSORS). The dispatcher
#: refuses at 72 before spawning anything; this is the module's own half, and
#: §4.2 states the relationship: 72 means NOTHING RAN, 13 means a stage ran far
#: enough to read its input and refused on it.
UPSTREAM = ("slice", "mask", "erase")


def upstream_findings(run_dir):
    findings = []
    for stage in UPSTREAM:
        state = kz.stage_state(run_dir, stage)
        if not state["present"]:
            findings.append("upstream %s has no report (%s)" % (stage, state["report"]))
        elif not state["consumable"]:
            findings.append("upstream %s is not consumable: %s"
                            % (stage, state["blocked_by"]))
    return findings


def run_composite(run_dir, mode="build", workspace=None, verify_only=False,
                  cleared_dir=None, quiet=False):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)            -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds -> exit 4 / 13
      3. every planned write resolves inside <run_dir>               -> exit 4
      4. slices/manifest.json is present and self-consistent         -> exit 13
      5. masks/manifest.json declares 0=CLEAR / 255=KEEP / RGBA      -> exit 13
      6. the two manifests agree on the face set                     -> exit 13
      7. masks/ is not the inverted tree, and cleared/ does not alias
         any input tree                                              -> exit 13
      8. slice, mask and erase reports are present and consumable    -> exit 13
      9. under --verify-only, cleared/ is neither absent nor empty    -> exit 13

    Steps 1-3 run before the first read, so a refusal at 4 leaves "nothing was
    read" literally true (§4.2).
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    slices_dir = os.path.join(run_dir, "slices")
    masks_dir = os.path.join(run_dir, "masks")
    delivered_dir = os.path.join(run_dir, "delivered")
    cleared_dir = cleared_dir or os.path.join(run_dir, "cleared")

    # 3 -- BEFORE any read.
    assert_inside_run_dir(cfg, run_dir, [cleared_dir], workspace=workspace)

    reasons = []
    slices_manifest = load_json(os.path.join(slices_dir, "manifest.json"),
                                "slices/manifest.json")
    reasons += slices_self_consistent(slices_manifest)
    masks_manifest = load_json(os.path.join(masks_dir, "manifest.json"),
                               "masks/manifest.json")
    reasons += masks_polarity_findings(masks_manifest)
    if not reasons:
        reasons += manifests_agree(slices_manifest, masks_manifest)

    # P2 / P3 -- the two path facts. `masks-inverted/` is L-mode with the
    # opposite polarity; `cleared/` must not alias, contain or nest inside an
    # input tree, or the run would write into its own corpus.
    real_cleared = os.path.realpath(cleared_dir)
    if os.path.realpath(masks_dir) == os.path.realpath(
            os.path.join(run_dir, "masks-inverted")):
        reasons.append("the masks directory IS masks-inverted/ -- that is LaMa's "
                       "L-mode input with the OPPOSITE polarity; compositing with "
                       "it inverts every card")
    for label, path in (("slices", slices_dir), ("masks", masks_dir),
                        ("delivered", delivered_dir)):
        real = os.path.realpath(path)
        if real_cleared == real or real_cleared.startswith(real + os.sep) \
                or real.startswith(real_cleared + os.sep):
            reasons.append("cleared/ aliases, contains or nests inside the %s tree "
                           "-- refusing to write into the corpus (%s)"
                           % (label, cleared_dir))
    reasons += upstream_findings(run_dir)

    if verify_only:
        present = os.path.isdir(cleared_dir) and bool(os.listdir(cleared_dir))
        if not present:
            reasons.append("--verify-only over an absent or empty cleared/ -- "
                           "refusing a vacuous pass (%s)" % cleared_dir)

    if reasons:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s cannot trust its inputs -- nothing composited" % STAGE,
                  "; ".join(reasons[:kz.GUARD_FINDING_SAMPLE]))

    records = sorted(slices_manifest["records"], key=lambda r: r["filename"])
    # CASE-FOLDED, matching `manifests_agree` above. The volume is
    # case-insensitive, so `71001.PNG` and `71001.png` are ONE file here; a
    # case-sensitive lookup would agree on the set and then KeyError on the
    # first face, which is the `Investigatortokens` shape in CLAUDE.md arriving
    # as a traceback instead of as a refusal.
    masks_by_file = {(m.get("file") or "").lower(): m
                     for m in mask_records(masks_manifest)}
    # PER FACE, from that face's own record: a scenario may carry two atlas
    # cell sizes, and asserting every face against the FIRST record's geometry
    # would refuse the second sheet on a corpus nothing is wrong with.
    geometry_by_file = {r["filename"]: slice_geometry(r) for r in records}

    if not verify_only:
        os.makedirs(cleared_dir, exist_ok=True)

    faces, input_faults, a1_hits, a2_hits, io_faults = [], [], [], [], []
    composited = verified = 0
    keep_total = clear_total = 0
    pixel_identical = byte_identical = 0

    for rec in records:
        name = rec["filename"]
        mrec = masks_by_file[name.lower()]
        face = {"file": name, "arkham_id": rec.get("arkham_id"),
                "side": rec.get("side"), "group": mrec.get("group"),
                "keep_pixels": None, "clear_pixels": None,
                "slice_sha256": None, "mask_sha256": None,
                "delivered_sha256": None, "cleared_sha256": None,
                "a1_bad": None, "a2_bad": None, "status": "OK"}
        dst = os.path.join(cleared_dir, name)

        def fault(status, detail):
            face["status"] = status
            input_faults.append("%s: %s" % (name, detail))
            faces.append(face)

        # ---- B1: all three inputs decode, all three at the declared geometry --
        try:
            slice_rgba, s_size, _s_mode, _s_fmt = _read_rgba(
                os.path.join(slices_dir, name))
            alpha, m_size, m_mode = _read_alpha(os.path.join(masks_dir, name))
            delivered, d_size, _d_mode, _d_fmt = _read_rgba(
                os.path.join(delivered_dir, name))
        except (OSError, ValueError) as exc:
            fault("input_io_error", "an input could not be decoded: %s" % exc)
            continue
        want = geometry_by_file[name]
        if (tuple(s_size), tuple(m_size), tuple(d_size)) != (want,) * 3:
            fault("input_size", "sizes slice %r mask %r delivered %r, expected %r"
                  % (s_size, m_size, d_size, want))
            continue
        slice_rgb = slice_rgba[..., :3]
        delivered_rgb = delivered[..., :3]

        # ---- B2: the mask FILE is RGBA -- the pixel-level masks-inverted guard --
        if m_mode != "RGBA":
            fault("mask_mode", "mask mode is %r, expected 'RGBA' -- masks-inverted/ "
                  "is L-mode with the OPPOSITE polarity" % m_mode)
            continue

        # ---- B3 + B4 + B8: the partition ------------------------------------
        keep, clear, b3 = partition(alpha)
        if b3:
            fault("mask_non_binary_alpha", "; ".join(b3))
            continue
        n_keep, n_clear, b8 = totality(keep, clear)
        if b8:
            fault("mask_partition", "; ".join(b8))
            continue

        # ---- B5: the exact integer cross-check against masks/manifest.json ---
        declared = mrec.get("cleared_pixels")
        if isinstance(declared, int) and n_clear != declared:
            fault("mask_pixel_count_mismatch",
                  "cleared pixels %d != masks/manifest.json %r" % (n_clear, declared))
            continue

        # ---- B7: the mask file matches its own manifest digest ---------------
        #
        # FATAL, and the record explains why it was promoted from record-only:
        # it is the only check that catches a COUNT-PRESERVING mask edit, which
        # B3, B4 and B5 all pass and which A1/A2 then pass BY CONSTRUCTION,
        # because both assertions are evaluated with the same tampered mask
        # (`composite-cleared.py:142-148`).
        mask_sha = kc.sha256_file(os.path.join(masks_dir, name))
        face["mask_sha256"] = mask_sha
        if mrec.get("sha256") and mask_sha != mrec["sha256"]:
            fault("mask_sha256_mismatch",
                  "mask digest %s != masks/manifest.json %s -- a count-preserving "
                  "mask edit no pixel predicate can see"
                  % (mask_sha[:16], (mrec["sha256"] or "")[:16]))
            continue

        face["keep_pixels"] = n_keep
        face["clear_pixels"] = n_clear
        face["slice_sha256"] = kc.sha256_file(os.path.join(slices_dir, name))
        face["delivered_sha256"] = kc.sha256_file(os.path.join(delivered_dir, name))
        composited += 1
        keep_total += n_keep
        clear_total += n_clear

        if not verify_only:
            try:
                write_png(dst, composite_face(slice_rgb, delivered_rgb, keep))
            except OSError as exc:
                face["status"] = "io_error"
                io_faults.append("%s: write failed: %s" % (name, exc))
                faces.append(face)
                continue

        result = assert_from_disk(dst, slice_rgb, delivered_rgb, keep, clear)
        if result["error"]:
            face["status"] = "io_error"
            io_faults.append("%s: %s" % (name, result["error"]))
            faces.append(face)
            continue

        verified += 1
        face["cleared_sha256"] = result["sha256"]
        face["a1_bad"] = result["a1_bad"]
        face["a2_bad"] = result["a2_bad"]
        if result["a1_bad"]:
            face["status"] = "A1_IDENTITY_VIOLATION"
            a1_hits.append({"file": name, "keep_pixels": n_keep,
                            "bad_pixels": result["a1_bad"],
                            "max_abs_delta": result["a1_max_delta"],
                            "first_bad_xy": result["a1_first_xy"]})
        if result["a2_bad"]:
            if face["status"] == "OK":
                face["status"] = "A2_INSIDE_VIOLATION"
            a2_hits.append({"file": name, "clear_pixels": n_clear,
                            "bad_pixels": result["a2_bad"],
                            "max_abs_delta": result["a2_max_delta"],
                            "first_bad_xy": result["a2_first_xy"]})
        if result["pixel_identical_to_delivered"]:
            pixel_identical += 1
        if face["cleared_sha256"] == face["delivered_sha256"]:
            byte_identical += 1
        faces.append(face)

    a3 = tree_check(cleared_dir, records, geometry_by_file)

    triggered, checks = [], []

    def record_check(cid, name, ok, exit_on_fail, detail):
        checks.append({"id": cid, "name": name,
                       "status": "pass" if ok else "fail",
                       "exit_on_fail": exit_on_fail, "detail": detail})
        if not ok:
            triggered.append(exit_on_fail)

    record_check("B0", "inputs_trusted", not (input_faults or io_faults),
                 kc.EXIT_PRECONDITION, input_faults[:10] + io_faults[:10])
    record_check("A1", "outside_mask_identity", not a1_hits, kc.EXIT_RULE_A,
                 ["%s: %d KEEP px differ from the English slice (max |delta| %d, "
                  "first at %s)" % (h["file"], h["bad_pixels"], h["max_abs_delta"],
                                    h["first_bad_xy"]) for h in a1_hits[:10]])
    record_check("A2", "inside_mask_fidelity", not a2_hits, kc.EXIT_RULE_B,
                 ["%s: %d CLEAR px differ from delivered/ (max |delta| %d, first "
                  "at %s)" % (h["file"], h["bad_pixels"], h["max_abs_delta"],
                              h["first_bad_xy"]) for h in a2_hits[:10]])
    record_check("A3", "cleared_tree_is_the_expected_set", not a3,
                 kc.EXIT_ARTIFACT, a3[:10])

    counts = {
        "faces": len(records),
        "composited": composited,
        "verified": verified,
        "skipped": len(input_faults),
        "keep_pixels": keep_total,
        "clear_pixels": clear_total,
        "a1_violations": len(a1_hits),
        "a2_violations": len(a2_hits),
        # Reported, never hidden: on a corpus whose erase preserved its unmasked
        # pixels this reads `faces`, and that is the expected result
        # (`composite-cleared.py:34-45`).
        "pixel_identical_to_delivered": pixel_identical,
        "byte_identical_to_delivered": byte_identical,
    }

    inputs = [os.path.join(slices_dir, "manifest.json"),
              os.path.join(masks_dir, "manifest.json")]
    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path] + inputs),
        freshness=kc.build_freshness(
            inputs, upstream_report_path=kc.report_path(run_dir, "erase", "build")),
        tool=kc.tool_block(pil=PIL.__version__, numpy=np.__version__),
        results={"faces": faces, "a1": a1_hits, "a2": a2_hits})
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    return report


def _read_rgba(path):
    with Image.open(path) as im:
        size, mode, fmt = im.size, im.mode, im.format
        arr = np.asarray(im.convert("RGBA"))
    return arr, size, mode, fmt


def _read_alpha(path):
    """The mask's alpha plane, plus the FILE's own size and mode.

    `im.mode` is read before `convert("RGBA")`, because the conversion is exactly
    what would erase the evidence B2 is looking for: an L-mode inverted mask
    converts to RGBA with alpha 255 everywhere and would then read as an
    all-KEEP mask rather than as the wrong tree.
    """
    with Image.open(path) as im:
        size, mode = im.size, im.mode
        arr = np.asarray(im.convert("RGBA"))[..., 3]
    return arr, size, mode


# ===========================================================================
# 7. --selftest -- one planted fault per named assertion
# ===========================================================================
#
# The standard is `atlas-prompt/build-package.py:456-459`: "Each case injects one
# fault and requires the named check to fail. A check that cannot fail is a
# defect in this project's history, not a nicety."
#
# The A1 and A2 cases plant their fault IN THE WRITTEN FILE, between the write
# and the assertion, and that is the only way to test what this stage claims: a
# fault planted in the in-memory array would be caught by arithmetic that never
# touched the disk, which is precisely the assertion §5.4 says is worthless.

def _synth(width=24, height=16, seed=7):
    """A deterministic (slice, delivered, alpha) triple with a real partition."""
    rng = np.random.RandomState(seed)
    slice_rgb = rng.randint(0, 256, (height, width, 3)).astype(np.uint8)
    delivered = rng.randint(0, 256, (height, width, 3)).astype(np.uint8)
    alpha = np.full((height, width), KEEP_ALPHA, dtype=np.uint8)
    alpha[4:12, 6:18] = CLEAR_ALPHA          # a non-empty CLEAR island
    return slice_rgb, delivered, alpha


def selftest(fault=None, verbose=True):
    """Prove each named assertion fires on the fault it targets."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    slice_rgb, delivered, alpha = _synth()
    keep, clear, b3 = partition(alpha)
    if b3:
        findings.append("setup: the clean synthetic mask did not read as binary")
    n_keep, n_clear, b8 = totality(keep, clear)
    if b8:
        findings.append("setup: the clean synthetic mask is not a partition: %s" % b8)

    tmp = tempfile.mkdtemp(prefix="kz-composite-selftest.")
    try:
        dst = os.path.join(tmp, "face.png")

        # The clean control, run first for every fault: an assertion that fires
        # on a correct input proves nothing about the fault that follows it.
        write_png(dst, composite_face(slice_rgb, delivered, keep))
        clean = assert_from_disk(dst, slice_rgb, delivered, keep, clear)
        if clean["a1_bad"] or clean["a2_bad"] or clean["error"]:
            findings.append("control: a correct composite failed A1/A2 from disk "
                            "(a1=%r a2=%r err=%r)"
                            % (clean["a1_bad"], clean["a2_bad"], clean["error"]))

        if "a1" in wanted:
            # ONE KEEP pixel moved in the file on disk. The in-memory array is
            # untouched, so only the re-read can see it.
            out = composite_face(slice_rgb, delivered, keep).copy()
            ys, xs = np.nonzero(keep)
            y, x = int(ys[0]), int(xs[0])
            out[y, x, 0] = np.uint8((int(out[y, x, 0]) + 128) % 256)
            write_png(dst, out)
            got = assert_from_disk(dst, slice_rgb, delivered, keep, clear)
            if not got["a1_bad"]:
                findings.append("a1: a moved KEEP pixel did not fire A1")
            if got["a2_bad"]:
                findings.append("a1: the KEEP fault also fired A2 -- the two "
                                "populations are not disjoint")
            if kc.pick_exit([kc.EXIT_RULE_A]) != 20:
                findings.append("a1: A1 does not map to exit 20")
            # int16 and not uint8: a wrapping subtraction would report the
            # LARGEST possible corruption as the smallest, so the worst input
            # would read as the mildest (`composite-cleared.py:706-707`).
            base = composite_face(slice_rgb, delivered, keep).copy()
            base[y, x, 0] = 255
            write_png(dst, base)
            wrap = assert_from_disk(dst, slice_rgb, delivered, keep, clear)
            expected = abs(255 - int(composite_face(slice_rgb, delivered,
                                                    keep)[y, x, 0]))
            if wrap["a1_max_delta"] != expected:
                findings.append("a1: max |delta| is %r, expected %d -- uint8 "
                                "subtraction wrapped" % (wrap["a1_max_delta"],
                                                         expected))

        if "a2" in wanted:
            out = composite_face(slice_rgb, delivered, keep).copy()
            ys, xs = np.nonzero(clear)
            y, x = int(ys[0]), int(xs[0])
            out[y, x, 1] = np.uint8((int(out[y, x, 1]) + 99) % 256)
            write_png(dst, out)
            got = assert_from_disk(dst, slice_rgb, delivered, keep, clear)
            if not got["a2_bad"]:
                findings.append("a2: a moved CLEAR pixel did not fire A2")
            if got["a1_bad"]:
                findings.append("a2: the CLEAR fault also fired A1 -- the two "
                                "populations are not disjoint")
            if kc.pick_exit([kc.EXIT_RULE_B]) != 21:
                findings.append("a2: A2 does not map to exit 21")
            # A1 before A2 when both fire: the outside-mask claim is the headline.
            if kc.pick_exit([kc.EXIT_RULE_A, kc.EXIT_RULE_B]) != kc.EXIT_RULE_A:
                findings.append("a2: pick_exit({20,21}) must name A1 first")

        if "b3" in wanted:
            grey = alpha.copy()
            grey[5, 7] = 128
            _keep2, _clear2, b3_findings = partition(grey)
            if not b3_findings:
                findings.append("b3: a mask alpha of 128 did not fire B3")
            # The consequence, demonstrated rather than asserted in prose: with a
            # stray alpha the two populations no longer cover the frame, so a run
            # that skipped B3 would leave that pixel unexamined by BOTH A1 and A2.
            n_k, n_c, b8_findings = totality(_keep2, _clear2)
            if n_k + n_c == grey.size:
                findings.append("b3: the stray-alpha mask still partitions the "
                                "frame -- the B3 fault is not observable")
            if not b8_findings:
                findings.append("b3: B8 did not fire on a non-covering partition")
            # And the anti-vacuity half: an all-KEEP mask buys a free A2 pass.
            _k3, _c3, _ = partition(np.full_like(alpha, KEEP_ALPHA))
            if not totality(_k3, _c3)[2]:
                findings.append("b3: an all-KEEP mask did not fire B4 -- A2 would "
                                "pass on zero pixels")

        if "polarity" in wanted:
            # P5: masks-inverted/ has no manifest at all, "which is itself the
            # signal". The declared-convention check is what stands in for that
            # when a manifest IS present but says the wrong thing.
            good = {"alpha_convention": {"0": "CLEAR - regenerate",
                                         "255": "KEEP - restore",
                                         "mode": "RGBA"},
                    "counts": {"masks": 1}, "masks": [{"file": "a.png"}]}
            if masks_polarity_findings(good):
                findings.append("polarity: a correctly declared manifest was refused")
            for key, value, label in (("0", "KEEP - restore", "inverted 0"),
                                      ("255", "CLEAR - regenerate", "inverted 255"),
                                      ("mode", "L", "L-mode")):
                bad = json.loads(json.dumps(good))
                bad["alpha_convention"][key] = value
                if not masks_polarity_findings(bad):
                    findings.append("polarity: %s was not refused" % label)
            # And the file-level half, B2: an L-mode mask converts to RGBA with
            # alpha 255 everywhere, so without reading im.mode FIRST it would
            # read as an all-KEEP mask rather than as the wrong tree.
            lmode = os.path.join(tmp, "inverted.png")
            Image.fromarray(np.where(alpha == CLEAR_ALPHA, 255, 0).astype(np.uint8),
                            mode="L").save(lmode, format="PNG")
            _a, _size, mode_read = _read_alpha(lmode)
            if mode_read != "L":
                findings.append("polarity: _read_alpha reported mode %r for an "
                                "L-mode mask -- B2 cannot see the inverted tree"
                                % mode_read)
            if not bool((_a == KEEP_ALPHA).all()):
                findings.append("polarity: the L-mode mask did not convert to an "
                                "all-opaque alpha, so the B2 hazard is not the one "
                                "documented")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ===========================================================================
# 8. CLI
# ===========================================================================

_SUMMARY = {
    "a1": "a KEEP pixel moved in the FILE fires A1 at exit 20, does not fire A2, "
          "and reports its delta in int16 so a 255->0 change reads 255 and not 1",
    "a2": "a CLEAR pixel moved in the FILE fires A2 at exit 21, does not fire A1, "
          "and pick_exit names A1 first when both fire",
    "b3": "a stray alpha of 128 fires B3, and the frame it leaves is demonstrably "
          "no longer covered by keep|clear -- so a run without B3 would leave "
          "pixels neither assertion examines; an all-KEEP mask fires B4",
    "polarity": "an inverted or L-mode alpha_convention is refused, and _read_alpha "
                "reports the FILE's mode so B2 can see masks-inverted/ before the "
                "RGBA conversion hides it",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_composite.py",
        description="koreanize stage `composite` -- where(mask.alpha == 255, "
                    "slice, delivered), then A1/A2 asserted from disk "
                    "(design §5.4, §6 step 13).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and assert into <run_dir>/dry-run/composite/")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-assert an existing cleared/ without writing it")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_composite --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-9s %s" % (name, _SUMMARY[name]))
        print("  note: composite has NO TOLERANCES row and must not grow one "
              "(§5.4) -- there is no --accept-* that downgrades A1 or A2.")
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

    dest = run_dir
    cleared_dir = None
    if args.dry_run:
        cfg = kz.load_scenario(os.path.join(run_dir, "scenario.json"))
        dest = kz.assert_dry_run_dest(cfg, STAGE,
                                      os.path.join(run_dir, "dry-run", STAGE))
        cleared_dir = os.path.join(dest, "cleared")

    report = run_composite(run_dir, mode=mode, verify_only=args.verify_only,
                           cleared_dir=cleared_dir, quiet=args.quiet)
    path = kc.write_report(report, dest)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize composite -- %s" % report["slug"])
        print("  faces           : %d (%d composited, %d verified, %d skipped)"
              % (counts["faces"], counts["composited"], counts["verified"],
                 counts["skipped"]))
        print("  partition       : %d keep + %d clear px"
              % (counts["keep_pixels"], counts["clear_pixels"]))
        print("  identical to delivered/: %d pixel, %d byte  (pixel-identical is "
              "the EXPECTED result; PNG re-encoding is not byte-preserving)"
              % (counts["pixel_identical_to_delivered"],
                 counts["byte_identical_to_delivered"]))
        for check in report["checks"]:
            print("  %-30s: %s  %s" % (check["name"], check["status"],
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
