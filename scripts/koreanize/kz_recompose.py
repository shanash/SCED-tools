#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `recompose` -- paste the typeset tiles back into the atlas (design §5.4, §6 step 15).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER` -- it decodes multi-megapixel PNGs with numpy, and
`/usr/bin/python3` (3.9.6) has PIL 10.4.0 and no numpy at all.

WHAT IT BUILDS, IN ONE LINE
---------------------------
    atlases/<sheet>.png = the DECODED ENGLISH ATLAS, with one typeset tile
                          pasted over every cell this scenario owns

THE BASE CANVAS IS THE ENGLISH ATLAS, AND THAT IS THE WHOLE DESIGN
-------------------------------------------------------------------
§5.4: *"composite typeset tiles onto the **decoded English atlas as base
canvas**, so unused cells keep English pixels by construction"*. The alternative
-- start from a blank canvas and fill the unused cells afterwards -- produces the
same pixels on a good day and a transparent hole on the day somebody forgets the
fill step, reorders the loop, or adds a `continue`. Midwinter's three sheets have
100 cells and 88 tiles: **12 cells are never written by this stage at all**, and
A2 below asserts that they came out identical to the base. A fill step would make
A2 a test of the fill step; starting from the base makes A2 a test of the
encoder, the atomic replace and the filesystem, which is the only part that can
still surprise anybody.

THE TOTALITY PROOF -- WHY IT IS AN INTEGER IDENTITY AND NOT A COMMENT
----------------------------------------------------------------------
    used + unused == num_width * num_height

Per sheet, and again over the run. A1 constrains every used cell and A2 every
unused one, so the identity is what turns *"A1 and A2 both passed"* into *"no
output pixel is unconstrained"*. Without it a sheet whose grid was misread as
6x4 would have A1 and A2 both pass over 24 of its 30 cells and say nothing at all
about the other six -- a green run over a canvas nobody examined. It is the same
argument `kz_composite.totality()` makes for `keep + clear == width * height`,
one dimension up, and it is checked rather than argued for the same reason.

The cells tile the canvas with no gap and no overlap because P4 asserts
`width == num_width * cell_width` and `height == num_height * cell_height`
EXACTLY. Drop that and the identity is still true while a strip of pixels down
the right edge belongs to no cell.

A1 AND A2 ARE RE-ASSERTED FROM DISK, NEVER AGAINST THE IN-MEMORY ARRAY
-----------------------------------------------------------------------
After the write, every output file is **re-opened and re-decoded**, and the two
assertions are evaluated against those bytes:

    A1 (exit 20)  a USED cell   == typeset/<filename> decoded to RGB
    A2 (exit 21)  an UNUSED cell == the English base cell at that (row, col)

Asserting the array would prove that `Image.paste` works, which nobody doubts.
`--verify-only` runs exactly the same two assertions and skips only the write, so
the stage can re-prove a tree it did not build.

The claim is PIXEL identity, never byte identity. For A1 the output is a
6000x5250 canvas and the input a 750x1050 file, so byte identity is not merely
absent, it is undefined (`recompose-atlases.py:39-42`).

`MAX_ATLAS_BYTES` IS IMPORTED. IT IS NEVER REDEFINED
-----------------------------------------------------
§5.4 and §7's R2-hosting row: the cap comes from
`SCED-tools/scripts/compose-card-atlas.py:81` and so does the content-addressed
URL scheme (`R2_PUBLIC_BASE` `:84`, `r2_key_for_sha` `:101`, `face_url_for_sha`
`:116`). The reason is stated as a consequence rather than as a style rule: the
size cap and the URL are two halves of one publication contract, and a local copy
of either lets them drift apart silently -- an atlas this stage called acceptable
uploaded to a key nothing requests. `recompose-atlases.py:78` made the same
choice and says so in the same words. The module is loaded through
`importlib.util` because its filename is hyphenated, and it is loaded from
`main()` rather than at module scope so that P0a/P0b still run before any file is
opened (`recompose-atlases.py:375-386`).

THE ONE TOLERANCE, AND THE ONE HARD CAP -- THEY ARE DIFFERENT THINGS
----------------------------------------------------------------------
`kz_common.TOLERANCES` carries exactly one row for this stage:

    atlas-size | atlas above 90% of MAX_ATLAS_BYTES | --accept-atlas-size | 22

That is a WARNING BAND below the cap, not the cap. An atlas at 61 MiB against a
64 MiB cap is publishable and an operator may say so; an atlas ABOVE the cap is
not publishable at all, so it has no `--accept-*` and refuses at **67** with the
produced file named. Giving the hard cap an acceptance flag would be offering to
accept a file R2 and TTS will not serve, which is not a measurement anybody can
rule on. `recompose-atlases.py` drew the same line and its docstring at `:63-66`
is explicit that the cap VALUE is imported while the exit CODE is local: *"That
divergence is a decision, not a discrepancy -- do not 'fix' it."*

NO AI, ENFORCED (§5.4)
-----------------------
`recompose` is one of the eleven forbidden-AI stages, so `kc.declare_ai(STAGE,
required=False)` runs in the module body: a `scenario.json` that does not list it
in `ai.forbidden_stages` refuses at exit 4 the moment a config is bound, and
`kc.write_report` refuses a build report carrying a non-null `ai` block. The
prohibition is code, not a comment.

WHAT IT READS, AND WHO OWNS EACH INPUT
---------------------------------------
    <run_dir>/scenario.json          the pinned config
    <run_dir>/atlas-inventory.json   `init` -- per sheet: grid, measured pixels,
                                     cell_pixels, the cached English atlas
    <run_dir>/slices/manifest.json   `slice` -- the URL<->file mapping, one record
                                     per cell: sheet, cell, row, col, filename
    <run_dir>/typeset/<filename>     `typeset` -- the finished Korean tile

`slice` owns the mapping and this stage consumes it; §5.4 assigns the
normalisation and the measured-dims confirmation to `slice` precisely so that
`recompose` never has to guess which file belongs in which cell. A record missing
`sheet`, `cell`, `row` or `col` refuses at 13 naming the field, rather than being
defaulted into a plausible-looking cell.

WHAT IT WRITES -- ALL INSIDE <run_dir>
---------------------------------------
    <run_dir>/atlases/<sheet>.png              the recomposed atlas
    <run_dir>/atlases/upload-manifest.json     `kz_upload`'s input, and exactly
                                               the shape `upload-atlases-to-r2.py
                                               --manifest` consumes
    <run_dir>/recompose.json                   the stage report

The manifest is the publication boundary and is written **only on exit 0**: a
manifest naming an atlas that failed A1 is a loaded gun pointed at `upload`.

EXIT CODES (§4.2)
   0  every sheet recomposed and re-read; totality holds; A1, A2 exact
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin, or a planned write outside <run_dir>
  13  precondition -- a manifest, an unresolvable sheet, a grid that does not
       tile its canvas, the TOTALITY identity, a per-cell input surprise, an
       upstream report that is not consumable, or a --verify-only run over an
       absent atlases/
  20  A1 -- a re-read USED cell differs from its typeset tile
  21  A2 -- a re-read UNUSED cell differs from the English base
  22  the atlas-size tolerance fired and --accept-atlas-size was not given
  67  the atlases/ tree is not the expected set of PNGs at the expected geometry,
       or a produced atlas is ABOVE the imported cap (no acceptance path)

13 is deliberately below 20 and 21 in `kc.EXIT_PRECEDENCE`, and 22 deliberately
last: a size warning can never mask a pixel failure, so a run that is both
oversize-in-band and A1-violating reports 20 (`recompose-atlases.py:58-60`).
"""

import argparse
import collections
import importlib.util
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402

import numpy as np  # noqa: E402
import PIL  # noqa: E402
from PIL import Image  # noqa: E402

STAGE = "recompose"

# Declared in the MODULE BODY (§3.2, §5.4). `recompose` is one of the eleven
# forbidden-AI stages, so this is the registration AND the assertion: bind a
# scenario.json that does not name it in ai.forbidden_stages and the process
# refuses at exit 4 before it has read a pixel.
kc.declare_ai(STAGE, required=False)

FAULTS = ("cap-import", "totality", "atlas-size", "a1", "a2", "grid")

#: The `kz_common.TOLERANCES` row this module owns. Named once, so the report,
#: the flag and the table can never drift apart.
TOLERANCE = "atlas-size"

#: 90% of the imported cap. Not a taste and not a round number chosen here: the
#: row's own predicate text is "atlas above 90% of MAX_ATLAS_BYTES", and the band
#: exists because an atlas that close to the cap will cross it on the next
#: re-typeset -- which is a fact an operator wants BEFORE the upload, not after.
ATLAS_SIZE_BAND = 0.90

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: `recompose` requires both (kz_config.PREDECESSORS). The dispatcher refuses at
#: 72 before spawning anything; this is the module's own half, and §4.2 states
#: the relationship: 72 means NOTHING RAN, 13 means a stage ran far enough to
#: read its input and refused on it.
UPSTREAM = ("slice", "typeset")

#: The fields a `slices/manifest.json` record must carry for this stage. Listed
#: rather than accessed with `.get(..., 0)`, because a defaulted cell is a tile
#: pasted somewhere plausible and A1 would then compare the wrong two things.
REQUIRED_RECORD_FIELDS = ("cell", "row", "col", "filename")

#: How a record names its sheet, in preference order. `kz_slice` writes
#: `atlas_id` and `english_url` and no `sheet` at all; the Midwinter record's
#: `slice-atlases.py` wrote a shape id (`8x5-face`). All three are accepted and
#: resolved against the inventory, which keeps the naming a `slice` decision
#: rather than a convention `recompose` imposes on it (§5.4).
SHEET_KEY_FIELDS = ("sheet", "atlas_id", "english_url")


# ===========================================================================
# 1. The imported cap and URL scheme (§5.4, §7)
# ===========================================================================
#
# Loaded from main()/run_recompose(), never at module scope: importing at module
# scope would open and execute compose-card-atlas.py before P0a/P0b had run,
# falsifying "nothing was read" on an invocation-guard refusal, and it would turn
# a missing file into a traceback at exit 1 instead of a named precondition.

CCA_PATH = os.path.join(kc.SCRIPTS_DIR, "compose-card-atlas.py")

#: The names this module refuses to run without. Asserted rather than assumed so
#: that a rename upstream is a named refusal here instead of an AttributeError
#: three functions later (`recompose-atlases.py:403-408`).
CCA_REQUIRED = ("MAX_ATLAS_BYTES", "R2_PUBLIC_BASE", "r2_key_for_sha",
                "public_url_for_key", "face_url_for_sha")

_CCA = {"mod": None, "path": None}


def _load_module(name, path):
    """importlib by path, because the filename is hyphenated and cannot be
    `import`ed. Registered in sys.modules BEFORE exec_module, which is the
    documented-correct order (`recompose-atlases.py:367-372`)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("no loader for %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_compose_card_atlas(path=None):
    """The single source of the size cap and the content-addressed URL scheme.

    Refuses at exit 13 rather than falling back to a local constant. A fallback
    is the one behaviour that must not exist here: it would let the cap and the
    URL diverge quietly, which is the exact failure §5.4 imports them to prevent.
    """
    path = path or CCA_PATH
    if _CCA["mod"] is not None and _CCA["path"] == path:
        return _CCA["mod"]
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "compose-card-atlas.py is absent, so MAX_ATLAS_BYTES cannot be "
                  "imported", "%s -- the cap and the content-addressed URL are one "
                  "contract and this module defines neither locally" % path)
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True          # never drop a .pyc into a git repo
    try:
        mod = _load_module("sced_compose_card_atlas", path)
    except Exception as exc:                # noqa: BLE001 -- reported, not swallowed
        kc.refuse(kc.EXIT_PRECONDITION,
                  "could not import compose-card-atlas.py",
                  "%s: %s: %s" % (path, type(exc).__name__, exc))
    finally:
        sys.dont_write_bytecode = prev
    missing = [name for name in CCA_REQUIRED if not hasattr(mod, name)]
    if missing:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "compose-card-atlas.py lacks %s" % ", ".join(missing),
                  "the size cap and the R2 URL scheme must be single-sourced, "
                  "never re-derived (§5.4, §7)")
    _CCA["mod"] = mod
    _CCA["path"] = path
    return mod


def max_atlas_bytes(path=None):
    """THE cap. There is no local literal anywhere in this file to compare it to."""
    return int(load_compose_card_atlas(path).MAX_ATLAS_BYTES)


def face_url_for_sha(digest, path=None):
    return load_compose_card_atlas(path).face_url_for_sha(digest)


def r2_key_for_sha(digest, path=None):
    return load_compose_card_atlas(path).r2_key_for_sha(digest)


# ===========================================================================
# 2. The atlas-size band -- pure, so its boundary is runnable (§2 item 39)
# ===========================================================================

def atlas_size_band(nbytes, cap):
    """Classify one produced atlas against the imported cap.

    Returns {"bytes", "cap", "threshold", "ratio", "in_band", "over_cap"}.

    PURE, and deliberately so: "a bound with no boundary case is a bound nobody
    has run" (§6 step 15), and a boundary case needs a predicate it can call with
    an integer rather than a 61 MiB file it has to write. `--selftest atlas-size`
    and `test_koreanize_recompose.py` both exercise it at
    threshold-1 / threshold / cap / cap+1.

    `in_band` is `bytes >= threshold`, i.e. the boundary is INCLUSIVE and the
    row's "above 90%" is read as "at or above". The inclusive form is the one
    that cannot round a value into silence: with `>` a file sitting exactly on
    61,847,529 bytes reports clean, and the next byte of PNG entropy makes it
    fire -- an operator would read that as a flapping check rather than as a file
    on the line.
    """
    cap = int(cap)
    nbytes = int(nbytes)
    threshold = int(cap * ATLAS_SIZE_BAND)
    return {
        "bytes": nbytes,
        "cap": cap,
        "threshold": threshold,
        "ratio": (float(nbytes) / cap) if cap else None,
        "in_band": nbytes >= threshold,
        "over_cap": nbytes > cap,
    }


def atlas_size_check(sizes, cap, accepted):
    """The `atlas-size` TOLERANCES row, and the hard cap beside it.

    Returns (check, over_cap_findings). `check` carries `"tolerance":
    "atlas-size"` so `kc.compute_consumable` can evaluate §3.6's tolerance
    conjunct on it, and the caller records `accepted["atlas-size"] = True` when
    the flag was given. The check therefore reports what the MEASUREMENT did and
    the `accepted{}` block reports what the HUMAN did, which is what makes the
    two separable in the receipt -- an accepted run and a clean run are not the
    same fact and must not read the same.

    The over-cap population is returned separately because it is not a tolerance
    at all: it has no flag, and its code is 67.
    """
    fired, over = [], []
    for name, nbytes in sizes:
        band = atlas_size_band(nbytes, cap)
        if band["over_cap"]:
            over.append("%s is %d bytes, ABOVE the imported cap of %d "
                        "(compose-card-atlas.py MAX_ATLAS_BYTES) -- there is no "
                        "--accept-* for this: R2 and TTS will not serve it"
                        % (name, band["bytes"], band["cap"]))
        if band["in_band"]:
            fired.append("%s is %d bytes, %.1f%% of the %d-byte cap (band starts "
                         "at %d)" % (name, band["bytes"], 100.0 * band["ratio"],
                                     band["cap"], band["threshold"]))
    check = {
        "id": "R4",
        "name": "atlas_size_within_band",
        # The MEASUREMENT, never the decision. `accepted` is recorded beside it
        # and `report["accepted"]["atlas-size"]` is what `kc.compute_consumable`
        # reads, so an accepted run and a clean run stay distinguishable in the
        # receipt. Folding the flag into `status` would make them read the same,
        # and the lock file would then have no record that a human ruled at all.
        "status": "pass" if not fired else "fail",
        "accepted": bool(accepted),
        "tolerance": TOLERANCE,
        "exit_on_fail": kc.EXIT_TOLERANCE,
        "detail": fired[:10],
    }
    return check, over


# ===========================================================================
# 3. The write guard -- re-asserted internally, exit 4 before any read
# ===========================================================================
#
# `guard.write_roots` names the SCED-downloads langpack destinations and nothing
# else, so `kz.assert_write_paths` is the wrong instrument here: this stage
# declares NO write root outside <run_dir> and must not be able to reach one.

def assert_inside_run_dir(cfg, run_dir, paths, workspace=None):
    """Every planned write resolves inside <run_dir>. Refuses at exit 4.

    Symlink-safe by construction: both sides are `realpath`'d, so an `atlases`
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
# 4. The inputs -- the inventory, the mapping, and the sheet plan
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


def safe_sheet_name(sheet_id):
    """A sheet id is used as a FILENAME, so it is validated as one.

    An id carrying a separator would write outside `atlases/` while every guard
    above it was looking at the directory rather than at the file, which is the
    one place a path check is easy to place too early.
    """
    if sheet_id is None:
        return None
    text = str(sheet_id)
    if not text or text in (".", "..") or "/" in text or "\\" in text \
            or text.startswith("."):
        return None
    return text


def local_asset_path(record, run_dir):
    """`atlas-inventory.json` records `local` as `<run_dir>/assets/<sha>.png`.

    The literal `<run_dir>` is `kz_init`'s own placeholder (kz_init.py:810) --
    the inventory is written workspace-relative so it survives the run directory
    moving, exactly as every other path in `scenario.json` does (§3.2).
    """
    local = record.get("local")
    if not local:
        return None
    parts = str(local).replace("\\", "/").split("/")
    if parts and parts[0] == "<run_dir>":
        return os.path.join(run_dir, *parts[1:])
    if os.path.isabs(local):
        return local
    return os.path.join(run_dir, local)


def index_inventory(inventory):
    """Every key a `slices/manifest.json` record might name a sheet by.

    Three keys and not one, because the record's own producer used the sheet
    SHAPE (`8x5-face`) while `init` names sheets by content digest and by URL.
    Resolving all three here is what keeps `slice`'s naming a `slice` decision.
    """
    index = {}
    for record in inventory.get("atlases") or []:
        for key in (record.get("atlas_id"), record.get("sheet"),
                    record.get("english_url")):
            if key:
                index.setdefault(str(key), record)
    return index


def load_typeset_index(run_dir):
    """{slice filename: typeset tile path} from `<run_dir>/typeset.json`.

    `kz_typeset` names its output from the MASK manifest (`file`) and records the
    slice it came from separately (`slice_file`), so the two names are not
    guaranteed equal and `typeset/<slice filename>` is a convention rather than a
    contract. The report carries the join explicitly, so this reads it and falls
    back to the convention only when there is no report to read -- which is the
    case a synthetic fixture is in, never a real run.
    """
    path = kc.report_path(run_dir, "typeset", "build")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except ValueError:
        return {}
    index = {}
    for face in ((report.get("results") or {}).get("faces") or []):
        slice_file = face.get("slice_file")
        tile = face.get("typeset_path")
        if slice_file and tile:
            index[slice_file] = tile
    return index


def plan_sheets(inventory, records, run_dir, typeset_index=None):
    """(sheets, findings) -- one plan entry per sheet named by the mapping.

    Every arithmetic property this stage rests on is asserted HERE, before a
    single pixel is decoded, so that a refusal names the geometry rather than a
    numpy shape mismatch:

      P1  every record carries cell / row / col / filename, and names a sheet
          through one of SHEET_KEY_FIELDS
      P2  that sheet resolves to an `atlas-inventory.json` record
      P3  that record declares a grid, measured pixels and cell_pixels
      P4  width == num_width * cell_width and height == num_height * cell_height
          EXACTLY -- without it the cells do not tile the canvas and the totality
          identity below is true of a canvas with an unexamined strip down its edge
      P5  cell == row * num_width + col, and 0 <= cell < capacity
      P6  no two records claim the same (sheet, cell)
      P7  the cached English atlas is on disk
    """
    index = index_inventory(inventory)
    typeset_index = typeset_index or {}
    typeset_dir = os.path.join(run_dir, "typeset")
    findings = []
    sheets = collections.OrderedDict()

    for record in records:
        missing = [f for f in REQUIRED_RECORD_FIELDS if record.get(f) is None]
        if missing:                                                     # P1
            findings.append("slices/manifest.json record %r declares no %s"
                            % (record.get("filename") or record, ", ".join(missing)))
            continue
        key = next((record[f] for f in SHEET_KEY_FIELDS if record.get(f)), None)
        if key is None:                                                 # P1
            findings.append("slices/manifest.json record %r names no sheet -- one "
                            "of %s is required"
                            % (record.get("filename"), list(SHEET_KEY_FIELDS)))
            continue
        sheet_id = str(key)
        entry = sheets.get(sheet_id)
        if entry is None:
            inv = index.get(sheet_id)
            if inv is None:                                             # P2
                findings.append("sheet %r is named by slices/manifest.json but by "
                                "no atlas-inventory.json record (known: %s)"
                                % (sheet_id, sorted(index)[:6]))
                sheets[sheet_id] = False
                continue
            # The OUTPUT filename comes from the inventory's `atlas_id` -- the
            # stable identity `init` derives from the sheet's content -- and only
            # falls back to the key the record used. A record that names its
            # sheet by URL is legitimate and a URL is not a filename, so deriving
            # the name from the key would refuse a correct run.
            name = (safe_sheet_name(inv.get("atlas_id"))
                    or safe_sheet_name(sheet_id))
            if name is None:
                findings.append("sheet %r resolves to no usable filename "
                                "component (atlas_id=%r)"
                                % (sheet_id, inv.get("atlas_id")))
                sheets[sheet_id] = False
                continue
            grid = inv.get("grid") or {}
            pixels = inv.get("pixels")
            cell_pixels = inv.get("cell_pixels")
            if not grid.get("num_width") or not grid.get("num_height") \
                    or not pixels or not cell_pixels:                   # P3
                findings.append("sheet %r: atlas-inventory.json declares grid=%r "
                                "pixels=%r cell_pixels=%r -- the atlas was never "
                                "measured, so recompose cannot place a cell"
                                % (sheet_id, grid, pixels, cell_pixels))
                sheets[sheet_id] = False
                continue
            num_width = int(grid["num_width"])
            num_height = int(grid["num_height"])
            width, height = int(pixels[0]), int(pixels[1])
            cell_w, cell_h = int(cell_pixels[0]), int(cell_pixels[1])
            if width != num_width * cell_w or height != num_height * cell_h:  # P4
                findings.append("sheet %r: %dx%d px does not tile as %dx%d cells of "
                                "%dx%d -- the cells would not cover the canvas"
                                % (sheet_id, width, height, num_width, num_height,
                                   cell_w, cell_h))
                sheets[sheet_id] = False
                continue
            base = local_asset_path(inv, run_dir)
            if not base or not os.path.exists(base):                    # P7
                findings.append("sheet %r: the cached English atlas is absent (%s) "
                                "-- the base canvas is not optional, it IS the "
                                "unused cells" % (sheet_id, base))
                sheets[sheet_id] = False
                continue
            entry = {
                "sheet": sheet_id,
                "name": name,
                "atlas_id": inv.get("atlas_id"),
                "english_url": inv.get("english_url"),
                "packs": inv.get("packs") or [],
                "single_card": bool(inv.get("single_card")),
                "base": base,
                "base_sha256": inv.get("sha256"),
                "num_width": num_width,
                "num_height": num_height,
                "cell_width": cell_w,
                "cell_height": cell_h,
                "width": width,
                "height": height,
                "cells_total": num_width * num_height,
                "tiles": collections.OrderedDict(),
            }
            sheets[sheet_id] = entry
        if entry is False:
            continue

        cell = int(record["cell"])
        row = int(record["row"])
        col = int(record["col"])
        capacity = entry["cells_total"]
        if not (0 <= cell < capacity) or cell != row * entry["num_width"] + col \
                or not (0 <= col < entry["num_width"]) \
                or not (0 <= row < entry["num_height"]):                 # P5
            findings.append("sheet %r: cell=%d row=%d col=%d is not consistent with "
                            "a %dx%d grid (cell must equal row*num_width+col and lie "
                            "in 0..%d)" % (sheet_id, cell, row, col,
                                           entry["num_width"], entry["num_height"],
                                           capacity - 1))
            continue
        if cell in entry["tiles"]:                                       # P6
            findings.append("sheet %r: cell %d is claimed by both %s and %s"
                            % (sheet_id, cell, entry["tiles"][cell]["filename"],
                               record["filename"]))
            continue
        rel = typeset_index.get(record["filename"])
        tile_path = (os.path.join(run_dir, rel) if rel
                     else os.path.join(typeset_dir, record["filename"]))
        entry["tiles"][cell] = {"filename": record["filename"],
                                "tile_path": tile_path,
                                "cell": cell, "row": row, "col": col,
                                "arkham_id": record.get("arkham_id"),
                                "side": record.get("side")}

    live = [s for s in sheets.values() if s]
    for entry in live:
        used, unused, totality_findings = totality(sorted(entry["tiles"]),
                                                   entry["num_width"],
                                                   entry["num_height"],
                                                   entry["sheet"])
        entry["used_cells"] = used
        entry["unused_cells"] = unused
        findings += totality_findings
    return live, findings


# ===========================================================================
# 5. The totality proof (§5.4)
# ===========================================================================

def totality(used_cells, num_width, num_height, label="sheet"):
    """(used, unused, findings) with `used + unused == num_width * num_height`.

    The unused set is DERIVED as the complement rather than declared, so the
    identity cannot be satisfied by a declaration that agrees with itself. That
    is the whole difference between this and a comment: a derived complement can
    only fail if the used set is out of range or has a duplicate, and both of
    those are exactly the defects that would leave a cell unconstrained.

    The identity is re-checked here even though `plan_sheets` already rejected
    out-of-range and duplicate cells, because this function is also the run-level
    one and is called by `--selftest totality` on inputs no planner produced. A
    proof that only holds when its caller was careful is not a proof.
    """
    capacity = int(num_width) * int(num_height)
    used = sorted(set(int(c) for c in used_cells))
    findings = []
    if len(used) != len(list(used_cells)):
        findings.append("%s: the used-cell set has a duplicate (%d entries, %d "
                        "distinct)" % (label, len(list(used_cells)), len(used)))
    out_of_range = [c for c in used if not (0 <= c < capacity)]
    if out_of_range:
        findings.append("%s: used cells outside 0..%d: %s"
                        % (label, capacity - 1, out_of_range[:8]))
    unused = sorted(set(range(capacity)) - set(used))
    if len(used) + len(unused) != capacity:
        findings.append(
            "%s: TOTALITY used %d + unused %d != num_width*num_height %d -- some "
            "cell of the output would be constrained by neither A1 nor A2"
            % (label, len(used), len(unused), capacity))
    return used, unused, findings


# ===========================================================================
# 6. The arithmetic, and the assertion that reads it back off the disk
# ===========================================================================

def cell_box(row, col, cell_w, cell_h):
    """(left, upper, right, lower) for one cell. No rotation, no resize."""
    return (col * cell_w, row * cell_h, col * cell_w + cell_w, row * cell_h + cell_h)


def read_rgb(path):
    with Image.open(path) as im:
        size, mode, fmt = im.size, im.mode, im.format
        arr = np.asarray(im.convert("RGB"))
    return arr, size, mode, fmt


def write_png(path, image):
    """Atomic: a sibling temp file plus `os.replace`, matching `sced_io`'s
    convention. An interrupted run never leaves a truncated PNG that the next
    run's R3 would classify as `corrupt` and the operator would read as a
    compositing defect."""
    tmp = path + ".tmp"
    image.save(tmp, format="PNG")
    os.replace(tmp, path)


def paste_sheet(sheet):
    """Decode the English base, paste every used tile onto it, return
    (image, base_cells, findings).

    `base_cells` is the RGB array of every UNUSED cell, captured BEFORE the first
    paste. It is what A2 compares the re-read output against, and capturing it
    here rather than re-opening the base afterwards is what makes A2 a statement
    about the file that was written rather than about a file re-read twice.

    Only the unused cells are captured. A used cell's expected pixels come from
    its typeset tile at assertion time, so hashing all of them here would be work
    whose result is immediately overwritten (`recompose-atlases.py:1184-1186`).
    """
    findings = []
    try:
        with Image.open(sheet["base"]) as src:
            base = src.convert("RGB")       # any RGBA buffer released at with-exit
    except Exception as exc:                # noqa: BLE001 -- reported, not swallowed
        return None, {}, ["sheet %r: the English base failed to decode: %s"
                          % (sheet["sheet"], exc)]
    if base.size != (sheet["width"], sheet["height"]):
        return None, {}, ["sheet %r: the English base is %dx%d, but "
                          "atlas-inventory.json measured %dx%d"
                          % (sheet["sheet"], base.size[0], base.size[1],
                             sheet["width"], sheet["height"])]

    base_cells = {}
    for cell in sheet["unused_cells"]:
        row, col = divmod(cell, sheet["num_width"])
        box = cell_box(row, col, sheet["cell_width"], sheet["cell_height"])
        base_cells[cell] = np.asarray(base.crop(box))

    for cell in sheet["used_cells"]:
        tile_rec = sheet["tiles"][cell]
        path = tile_rec["tile_path"]
        try:
            with Image.open(path) as tim:
                tsize, tmode = tim.size, tim.mode
                tile = tim.convert("RGB")
        except Exception as exc:            # noqa: BLE001 -- reported, not swallowed
            findings.append("sheet %r cell %d: typeset/%s is unreadable: %s"
                            % (sheet["sheet"], cell, tile_rec["filename"], exc))
            continue
        if tsize != (sheet["cell_width"], sheet["cell_height"]):
            # NO rotation and NO resize anywhere in this module: a tile handed in
            # at the transposed size is a `mask` rot=90 group that was never
            # rotated back, and silently rotating it here would make the defect
            # invisible on every downstream check.
            findings.append("sheet %r cell %d: typeset/%s is %dx%d, expected %dx%d "
                            "-- this module neither rotates nor resizes"
                            % (sheet["sheet"], cell, tile_rec["filename"],
                               tsize[0], tsize[1], sheet["cell_width"],
                               sheet["cell_height"]))
            tile.close()
            continue
        if tmode not in ("RGB", "RGBA", "L", "P"):
            findings.append("sheet %r cell %d: typeset/%s has mode %r"
                            % (sheet["sheet"], cell, tile_rec["filename"], tmode))
            tile.close()
            continue
        box = cell_box(tile_rec["row"], tile_rec["col"],
                       sheet["cell_width"], sheet["cell_height"])
        base.paste(tile, (box[0], box[1]))          # no rotation, no resize, no mask
        tile.close()

    if findings:
        base.close()
        return None, {}, findings
    return base, base_cells, findings


def assert_from_disk(path, sheet, base_cells):
    """A1 and A2, evaluated against the DECODED FILE. Never against the array.

    Returns a dict; the caller maps `a1` to exit 20 and `a2` to exit 21.

    int16, not uint8, on both differences: uint8 subtraction wraps, so a
    255 -> 0 change would report as a delta of 1 and the worst possible
    corruption would read as the mildest (`composite-cleared.py:706-707`).
    """
    out = {"a1": [], "a2": [], "cells_asserted": 0, "pixels_asserted": 0,
           "size": None, "mode": None, "format": None, "bytes": None,
           "sha256": None, "error": None}
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
    if tuple(size) != (sheet["width"], sheet["height"]):
        out["error"] = ("re-read %dx%d != the planned %dx%d"
                        % (size[0], size[1], sheet["width"], sheet["height"]))
        return out

    per_cell = sheet["cell_width"] * sheet["cell_height"]

    for cell in sheet["used_cells"]:                                    # A1
        rec = sheet["tiles"][cell]
        box = cell_box(rec["row"], rec["col"], sheet["cell_width"],
                       sheet["cell_height"])
        got = arr[box[1]:box[3], box[0]:box[2]]
        try:
            want, _s, _m, _f = read_rgb(rec["tile_path"])
        except (OSError, ValueError) as exc:
            out["a1"].append({"cell": cell, "file": rec["filename"],
                              "reason": "typeset tile unreadable at assert time: %s"
                                        % exc})
            continue
        diff = np.abs(got.astype(np.int16) - want.astype(np.int16))
        bad = int((diff.max(axis=-1) > 0).sum())
        out["cells_asserted"] += 1
        out["pixels_asserted"] += per_cell
        if bad:
            ys, xs = np.nonzero(diff.max(axis=-1) > 0)
            out["a1"].append({"cell": cell, "file": rec["filename"],
                              "bad_pixels": bad, "max_abs_delta": int(diff.max()),
                              "first_bad_xy": [int(xs[0]) + box[0],
                                               int(ys[0]) + box[1]]})

    for cell in sheet["unused_cells"]:                                  # A2
        row, col = divmod(cell, sheet["num_width"])
        box = cell_box(row, col, sheet["cell_width"], sheet["cell_height"])
        got = arr[box[1]:box[3], box[0]:box[2]]
        want = base_cells.get(cell)
        if want is None:
            out["a2"].append({"cell": cell,
                              "reason": "no pre-paste base capture for this cell"})
            continue
        diff = np.abs(got.astype(np.int16) - want.astype(np.int16))
        bad = int((diff.max(axis=-1) > 0).sum())
        out["cells_asserted"] += 1
        out["pixels_asserted"] += per_cell
        if bad:
            ys, xs = np.nonzero(diff.max(axis=-1) > 0)
            out["a2"].append({"cell": cell, "bad_pixels": bad,
                              "max_abs_delta": int(diff.max()),
                              "first_bad_xy": [int(xs[0]) + box[0],
                                               int(ys[0]) + box[1]]})
    return out


def capture_base_cells(sheet):
    """The A2 reference for a `--verify-only` run, which did no paste.

    Deliberately a SECOND function rather than a flag on `paste_sheet`: the build
    path's capture must happen before the first paste and the verify path's has
    no paste to be before, and folding the two into one function with a branch is
    how a future edit ends up capturing a base that has already been written over.
    """
    try:
        with Image.open(sheet["base"]) as src:
            base = src.convert("RGB")
    except Exception as exc:                # noqa: BLE001 -- reported, not swallowed
        return {}, ["sheet %r: the English base failed to decode: %s"
                    % (sheet["sheet"], exc)]
    cells = {}
    for cell in sheet["unused_cells"]:
        row, col = divmod(cell, sheet["num_width"])
        box = cell_box(row, col, sheet["cell_width"], sheet["cell_height"])
        cells[cell] = np.asarray(base.crop(box))
    base.close()
    return cells, []


# ===========================================================================
# 7. R3 -- the atlases/ tree itself (exit 67)
# ===========================================================================

def classify_entry(path, name):
    """Five steps, first failure wins. Ported in behaviour from
    `kz_composite.classify_entry`, which took it from
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
    except Exception:                       # noqa: BLE001
        return "container"
    try:
        # verify() consumes the file object, so this must be a SECOND open.
        with Image.open(path) as im:
            im.verify()
    except Exception:                       # noqa: BLE001
        return "corrupt"
    return None


def tree_check(atlases_dir, sheets):
    """The output directory AS IT IS, not as the loop believes it left it.

    Case-folded in BOTH directions, because the volume is case-INSENSITIVE:
    `8x5-face.PNG` and `8x5-face.png` are one file on disk and a one-directional
    check would call that agreement.
    """
    findings = []
    try:
        names = sorted(os.listdir(atlases_dir))
    except OSError:
        names = []
    not_png, png_names = [], []
    for name in names:
        if name.endswith(".json"):
            continue                        # the upload manifest lives here too
        if name.endswith(".tmp"):
            not_png.append({"name": name, "reason": "leftover temp file"})
            continue
        reason = classify_entry(os.path.join(atlases_dir, name), name)
        if reason is None:
            png_names.append(name)
        else:
            not_png.append({"name": name, "reason": reason})
    if not_png:
        findings.append("R3 %d entr(ies) under atlases/ are not well-formed PNGs: %s"
                        % (len(not_png), not_png[:5]))

    expected = {}
    for sheet in sheets:
        expected.setdefault(("%s.png" % sheet["name"]).lower(),
                            []).append("%s.png" % sheet["name"])
    found = {}
    for name in png_names:
        found.setdefault(name.lower(), []).append(name)
    missing = sorted(set(expected) - set(found))
    extra = sorted(set(found) - set(expected))
    case = sorted(k for k in set(expected) & set(found)
                  if expected[k][0] != found[k][0])
    if missing:
        findings.append("R3 %d expected atlas(es) absent from atlases/: %s"
                        % (len(missing), [expected[k][0] for k in missing[:5]]))
    if extra:
        findings.append("R3 %d unexpected file(s) under atlases/: %s"
                        % (len(extra), [found[k][0] for k in extra[:5]]))
    if case:
        findings.append("R3 %d file(s) differ from the plan only in case: %s"
                        % (len(case), [(expected[k][0], found[k][0]) for k in case[:5]]))

    by_name = {("%s.png" % s["name"]): s for s in sheets}
    bad_geom = []
    for name in png_names:
        sheet = by_name.get(name)
        if sheet is None:
            continue
        try:
            with Image.open(os.path.join(atlases_dir, name)) as im:
                size, mode, fmt = im.size, im.mode, im.format
        except Exception as exc:            # noqa: BLE001
            bad_geom.append({"name": name, "reason": "unreadable: %s" % exc})
            continue
        if tuple(size) != (sheet["width"], sheet["height"]) or mode != "RGB" \
                or fmt != "PNG":
            bad_geom.append({"name": name, "size": list(size), "mode": mode,
                             "format": fmt, "expected": [sheet["width"],
                                                         sheet["height"]]})
    if bad_geom:
        findings.append("R3 %d file(s) are not the planned RGB PNG geometry: %s"
                        % (len(bad_geom), bad_geom[:5]))
    return findings


# ===========================================================================
# 8. The upload manifest -- the publication boundary
# ===========================================================================

def build_upload_manifest(cfg, sheets, results, cap, cca_path=None):
    """Exactly the shape `upload-atlases-to-r2.py --manifest` consumes.

    That tool reads `atlases[].atlas_sha256`, `.face_url` and `.atlas_id` and
    derives the R2 object key from the URL (`key_from_url`), so the key is a
    function of the bytes and of `compose-card-atlas.py`'s scheme -- never of
    anything this module decided. `r2_key` is recorded beside the URL for the
    receipt and for `upload`'s own self-check, not as a second authority.
    """
    entries = []
    for sheet in sheets:
        res = results[sheet["sheet"]]
        digest = res["sha256"]
        entries.append(collections.OrderedDict([
            ("atlas_id", sheet["name"]),
            ("sheet", sheet["sheet"]),
            ("file", "%s.png" % sheet["name"]),
            ("path", res["path"]),
            ("atlas_sha256", digest),
            ("bytes", res["bytes"]),
            ("face_url", face_url_for_sha(digest, cca_path)),
            ("r2_key", r2_key_for_sha(digest, cca_path)),
            ("english_url", sheet["english_url"]),
            ("num_width", sheet["num_width"]),
            ("num_height", sheet["num_height"]),
            ("cells_total", sheet["cells_total"]),
            ("cells_used", len(sheet["used_cells"])),
            ("cells_unused", len(sheet["unused_cells"])),
            ("unused_cells", list(sheet["unused_cells"])),
            ("single_card", sheet["single_card"]),
            ("packs", list(sheet["packs"])),
        ]))
    return collections.OrderedDict([
        ("schema_version", kc.SCHEMA_VERSION),
        ("generated_by", "koreanize recompose"),
        ("generated_at", kc.utc_now()),
        ("slug", cfg["slug"]),
        ("max_atlas_bytes", cap),
        ("max_atlas_bytes_source",
         "SCED-tools/scripts/compose-card-atlas.py MAX_ATLAS_BYTES (imported)"),
        ("url_scheme_source",
         "SCED-tools/scripts/compose-card-atlas.py face_url_for_sha (imported)"),
        ("atlases", entries),
        ("shared_backs", cfg.get("shared_backs") or []),
    ])


# ===========================================================================
# 9. The stage
# ===========================================================================

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


def run_recompose(run_dir, mode="build", workspace=None, verify_only=False,
                  accept_atlas_size=False, atlases_dir=None, cca_path=None,
                  quiet=False):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)             -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds  -> exit 4 / 13
      3. every planned write resolves inside <run_dir>                 -> exit 4
      4. compose-card-atlas.py imports and carries the cap and scheme  -> exit 13
      5. atlas-inventory.json and slices/manifest.json are present     -> exit 13
      6. P1..P7 in plan_sheets, including the TOTALITY identity        -> exit 13
      7. slice and typeset reports are present and consumable          -> exit 13
      8. under --verify-only, atlases/ is neither absent nor empty      -> exit 13

    Steps 1-3 run before the first read, so a refusal at 4 leaves "nothing was
    read" literally true (§4.2).
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    typeset_dir = os.path.join(run_dir, "typeset")
    atlases_dir = atlases_dir or os.path.join(run_dir, "atlases")

    # 3 -- BEFORE any read.
    assert_inside_run_dir(cfg, run_dir, [atlases_dir], workspace=workspace)

    # 4 -- the imported cap, resolved before the pixels so that a missing
    # compose-card-atlas.py is a named precondition rather than a late crash.
    cap = max_atlas_bytes(cca_path)

    inventory_path = os.path.join(run_dir, "atlas-inventory.json")
    slices_manifest_path = os.path.join(run_dir, "slices", "manifest.json")
    inventory = load_json(inventory_path, "atlas-inventory.json")
    slices_manifest = load_json(slices_manifest_path, "slices/manifest.json")
    typeset_index = load_typeset_index(run_dir)

    reasons = []
    records = slices_manifest.get("records") or []
    if not records:
        reasons.append("slices/manifest.json carries no records -- refusing a "
                       "vacuous pass over an empty sheet set")
    sheets, plan_findings = plan_sheets(inventory, records, run_dir,
                                        typeset_index)
    reasons += plan_findings
    if not sheets and not reasons:
        reasons.append("no sheet resolved from slices/manifest.json")

    # The output tree must not alias an input tree, or the run writes into its
    # own corpus and A1 would then compare a tile against itself.
    real_out = os.path.realpath(atlases_dir)
    for label, path in (("typeset", typeset_dir),
                        ("assets", os.path.join(run_dir, "assets")),
                        ("slices", os.path.join(run_dir, "slices"))):
        real = os.path.realpath(path)
        if real_out == real or real_out.startswith(real + os.sep) \
                or real.startswith(real_out + os.sep):
            reasons.append("atlases/ aliases, contains or nests inside the %s tree "
                           "-- refusing to write into the corpus (%s)"
                           % (label, atlases_dir))

    reasons += upstream_findings(run_dir)

    if verify_only:
        present = os.path.isdir(atlases_dir) and bool(
            [n for n in os.listdir(atlases_dir) if n.lower().endswith(".png")])
        if not present:
            reasons.append("--verify-only over an absent or empty atlases/ -- "
                           "refusing a vacuous pass (%s)" % atlases_dir)

    if reasons:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s cannot trust its inputs -- nothing recomposed" % STAGE,
                  "; ".join(reasons[:kz.GUARD_FINDING_SAMPLE]))

    if not verify_only:
        os.makedirs(atlases_dir, exist_ok=True)

    results = {}
    input_faults, a1_hits, a2_hits, io_faults = [], [], [], []
    cells_asserted = pixels_asserted = 0
    recomposed = verified = 0

    for sheet in sheets:
        dst = os.path.join(atlases_dir, "%s.png" % sheet["name"])
        record = collections.OrderedDict([
            ("sheet", sheet["sheet"]),
            ("file", "%s.png" % sheet["name"]),
            ("path", os.path.relpath(dst, workspace)),
            ("english_url", sheet["english_url"]),
            ("base", os.path.relpath(sheet["base"], workspace)),
            ("base_sha256", sheet["base_sha256"]),
            ("num_width", sheet["num_width"]),
            ("num_height", sheet["num_height"]),
            ("cell_width", sheet["cell_width"]),
            ("cell_height", sheet["cell_height"]),
            ("cells_total", sheet["cells_total"]),
            ("cells_used", len(sheet["used_cells"])),
            ("cells_unused", len(sheet["unused_cells"])),
            ("unused_cells", list(sheet["unused_cells"])),
            ("bytes", None), ("sha256", None), ("status", "OK"),
        ])
        results[sheet["sheet"]] = record

        if verify_only:
            base_cells, faults = capture_base_cells(sheet)
        else:
            image, base_cells, faults = paste_sheet(sheet)
            if not faults:
                try:
                    write_png(dst, image)
                    recomposed += 1
                except OSError as exc:
                    faults = ["sheet %r: write failed: %s" % (sheet["sheet"], exc)]
                finally:
                    image.close()
        if faults:
            input_faults += faults
            record["status"] = "skipped"
            continue

        assertion = assert_from_disk(dst, sheet, base_cells)
        record["bytes"] = assertion["bytes"]
        record["sha256"] = assertion["sha256"]
        if assertion["error"]:
            io_faults.append("%s: %s" % (record["file"], assertion["error"]))
            record["status"] = "io_error"
            continue
        verified += 1
        cells_asserted += assertion["cells_asserted"]
        pixels_asserted += assertion["pixels_asserted"]
        for hit in assertion["a1"]:
            hit["sheet"] = sheet["sheet"]
            a1_hits.append(hit)
        for hit in assertion["a2"]:
            hit["sheet"] = sheet["sheet"]
            a2_hits.append(hit)

    triggered, checks = [], []

    def record_check(cid, name, ok, code, detail, tolerance=None):
        entry = {"id": cid, "name": name, "status": "pass" if ok else "fail",
                 "exit_on_fail": code, "detail": detail}
        if tolerance:
            entry["tolerance"] = tolerance
        checks.append(entry)
        if not ok and code not in triggered:
            triggered.append(code)

    # R0 -- the run-level totality, scaled to what was actually evaluated. This
    # is the identity §5.4 asks for, restated over the run: a sheet that was
    # skipped contributes nothing and is EXCLUDED from both sides rather than
    # counted as satisfied, so a skipped sheet can never buy a totality pass.
    evaluated = [s for s in sheets if results[s["sheet"]]["status"] == "OK"]
    expected_cells = sum(s["cells_total"] for s in evaluated)
    expected_pixels = sum(s["cells_total"] * s["cell_width"] * s["cell_height"]
                          for s in evaluated)
    totality_ok = (cells_asserted == expected_cells
                   and pixels_asserted == expected_pixels)
    record_check("R0", "totality_used_plus_unused_equals_capacity", totality_ok,
                 kc.EXIT_PRECONDITION,
                 [] if totality_ok else
                 ["cells asserted %d != %d evaluated, or pixels asserted %d != %d "
                  "-- some output cell is constrained by neither A1 nor A2"
                  % (cells_asserted, expected_cells, pixels_asserted,
                     expected_pixels)])

    record_check("R1", "a1_used_cell_equals_typeset_tile", not a1_hits,
                 kc.EXIT_RULE_A,
                 ["%s cell %s: %s" % (h["sheet"], h["cell"],
                                      h.get("reason") or
                                      "%d px differ (max delta %s) at %s"
                                      % (h.get("bad_pixels", 0),
                                         h.get("max_abs_delta"),
                                         h.get("first_bad_xy")))
                  for h in a1_hits[:10]])
    record_check("R2", "a2_unused_cell_equals_english_base", not a2_hits,
                 kc.EXIT_RULE_B,
                 ["%s cell %s: %s" % (h["sheet"], h["cell"],
                                      h.get("reason") or
                                      "%d px differ (max delta %s) at %s"
                                      % (h.get("bad_pixels", 0),
                                         h.get("max_abs_delta"),
                                         h.get("first_bad_xy")))
                  for h in a2_hits[:10]])

    tree = tree_check(atlases_dir, [s for s in sheets
                                    if results[s["sheet"]]["status"] != "skipped"])
    record_check("R3", "atlases_tree_is_the_expected_set", not tree,
                 kc.EXIT_ARTIFACT, tree[:10])

    sizes = [(results[s["sheet"]]["file"], results[s["sheet"]]["bytes"])
             for s in sheets if results[s["sheet"]]["bytes"] is not None]
    size_check, over_cap = atlas_size_check(sizes, cap, accept_atlas_size)
    checks.append(size_check)
    if size_check["status"] == "fail" and not accept_atlas_size:
        triggered.append(kc.EXIT_TOLERANCE)
    record_check("R5", "atlas_within_imported_cap", not over_cap,
                 kc.EXIT_ARTIFACT, over_cap[:10])

    if input_faults or io_faults:
        record_check("R6", "every_planned_sheet_was_produced", False,
                     kc.EXIT_PRECONDITION, (input_faults + io_faults)[:10])
    else:
        record_check("R6", "every_planned_sheet_was_produced", True,
                     kc.EXIT_PRECONDITION, [])

    counts = collections.OrderedDict([
        ("sheets", len(sheets)),
        ("recomposed", recomposed),
        ("verified", verified),
        ("skipped", len(sheets) - verified),
        ("cells_total", sum(s["cells_total"] for s in sheets)),
        ("cells_used", sum(len(s["used_cells"]) for s in sheets)),
        ("cells_unused", sum(len(s["unused_cells"]) for s in sheets)),
        ("cells_asserted", cells_asserted),
        ("pixels_asserted", pixels_asserted),
        ("a1_violations", len(a1_hits)),
        ("a2_violations", len(a2_hits)),
        ("atlases_in_size_band", len(size_check["detail"])),
        ("atlases_over_cap", len(over_cap)),
        ("max_atlas_bytes", cap),
    ])

    inputs = [inventory_path, slices_manifest_path]
    typeset_report = kc.report_path(run_dir, "typeset", "build")
    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding(
            [scenario_path] + inputs
            + ([typeset_report] if os.path.exists(typeset_report) else []),
            extra={"compose-card-atlas.py": kc.sha256_file(cca_path or CCA_PATH)}),
        freshness=kc.build_freshness(inputs, upstream_report_path=typeset_report),
        # §5.8: `golden` compares the interpreter triple BY REPORT, never by
        # import, so an art-tier stage must fill both fields it can fill.
        tool=kc.tool_block(pil=PIL.__version__, numpy=np.__version__),
        accepted={TOLERANCE: True} if accept_atlas_size else {},
        results={"sheets": list(results.values()), "a1": a1_hits, "a2": a2_hits})
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None

    # THE PUBLICATION BOUNDARY. The manifest names the atlases `upload` will PUT
    # to R2, so it is written only when every assertion above passed: a manifest
    # naming an atlas that failed A1 would hand `upload` a file this stage has
    # already refused (`recompose-atlases.py:1510-1511`).
    manifest_path = os.path.join(atlases_dir, "upload-manifest.json")
    if mode == "build" and report["exit_code"] == kc.EXIT_OK:
        manifest = build_upload_manifest(cfg, sheets, results, cap, cca_path)
        assert_inside_run_dir(cfg, run_dir, [manifest_path], workspace=workspace)
        kc.atomic_write_json(manifest_path, manifest)
        report["results"]["upload_manifest"] = os.path.relpath(manifest_path,
                                                               workspace)
    return report


# ===========================================================================
# 10. --selftest -- one planted fault per named assertion
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

def _synth_sheet(tmp, num_width=3, num_height=2, cell_w=8, cell_h=6, used=(0, 1, 3),
                 seed=11):
    """A small synthetic sheet: a base atlas, a typeset tile per used cell, and
    the plan record `paste_sheet`/`assert_from_disk` take."""
    rng = np.random.RandomState(seed)
    width, height = num_width * cell_w, num_height * cell_h
    base = rng.randint(0, 256, (height, width, 3)).astype(np.uint8)
    base_path = os.path.join(tmp, "base.png")
    Image.fromarray(base, mode="RGB").save(base_path, format="PNG")

    typeset_dir = os.path.join(tmp, "typeset")
    os.makedirs(typeset_dir, exist_ok=True)
    tiles = collections.OrderedDict()
    for cell in used:
        row, col = divmod(cell, num_width)
        name = "cell%02d.png" % cell
        arr = rng.randint(0, 256, (cell_h, cell_w, 3)).astype(np.uint8)
        Image.fromarray(arr, mode="RGB").save(os.path.join(typeset_dir, name),
                                              format="PNG")
        tiles[cell] = {"filename": name,
                       "tile_path": os.path.join(typeset_dir, name),
                       "cell": cell, "row": row, "col": col}

    used_cells, unused_cells, findings = totality(list(used), num_width, num_height)
    sheet = {"sheet": "synthetic", "name": "synthetic", "atlas_id": "synthetic",
             "english_url": "https://example.invalid/en.png", "packs": [],
             "single_card": False, "base": base_path, "base_sha256": None,
             "num_width": num_width, "num_height": num_height,
             "cell_width": cell_w, "cell_height": cell_h,
             "width": width, "height": height,
             "cells_total": num_width * num_height, "tiles": tiles,
             "used_cells": used_cells, "unused_cells": unused_cells}
    return sheet, typeset_dir, findings


def selftest(fault=None, verbose=True):
    """Prove each named assertion fires on the fault it targets."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    if "cap-import" in wanted:
        # The cap is IMPORTED. The assertion is that this file contains no
        # literal to compare it to -- checked by reading the source, because a
        # value test would pass on a local copy that happened to agree today and
        # is exactly the drift §5.4 imports the constant to prevent.
        cap = max_atlas_bytes()
        if cap != load_compose_card_atlas().MAX_ATLAS_BYTES:
            findings.append("cap-import: max_atlas_bytes() is not the imported value")
        if cap <= 0:
            findings.append("cap-import: the imported cap is not positive (%r)" % cap)
        with open(os.path.abspath(__file__), "r", encoding="utf-8") as handle:
            src = handle.read()
        # The needles are ASSEMBLED rather than written out, so that this check
        # does not match its own source -- which is not a trick, it is the only
        # way a source scan for a literal can live in the file it scans.
        needles = ["%d * 1024 * 1024" % (cap // (1024 * 1024)), str(cap)]
        if any(needle in src for needle in needles):
            findings.append("cap-import: this module carries a literal copy of the "
                            "cap -- §5.4 requires it to be imported and never "
                            "redefined")
        # A missing compose-card-atlas.py is a NAMED precondition, never a
        # fallback to a local default.
        _CCA["mod"], _CCA["path"] = None, None
        try:
            max_atlas_bytes(os.path.join(os.sep, "nonexistent", "cca.py"))
        except kc.KzRefusal as exc:
            if exc.code != kc.EXIT_PRECONDITION:
                findings.append("cap-import: an absent compose-card-atlas.py "
                                "refused with %d, expected 13" % exc.code)
        else:
            findings.append("cap-import: an absent compose-card-atlas.py did not "
                            "refuse -- something fell back to a local constant")
        _CCA["mod"], _CCA["path"] = None, None

    if "totality" in wanted:
        used, unused, ok = totality([0, 1, 3], 3, 2)
        if ok or used != [0, 1, 3] or unused != [2, 4, 5]:
            findings.append("totality: the clean case did not derive the complement "
                            "(used=%r unused=%r findings=%r)" % (used, unused, ok))
        if len(used) + len(unused) != 3 * 2:
            findings.append("totality: the identity does not hold on the clean case")
        # A cell outside the grid: the identity must FIRE rather than absorb it.
        _u, _n, out_of_range = totality([0, 1, 99], 3, 2)
        if not out_of_range:
            findings.append("totality: a used cell outside 0..5 did not fire")
        # A duplicate used cell: two tiles claiming one cell means one cell of
        # the output is asserted twice and another never.
        _u, _n, dup = totality([0, 1, 1], 3, 2)
        if not dup:
            findings.append("totality: a duplicated used cell did not fire")
        # And the identity is reachable at all -- a predicate that cannot fail is
        # not a predicate.
        if 5 + 1 == 3 * 2 and not out_of_range and not dup:
            findings.append("totality: neither fault fired, so the identity is inert")

    if "atlas-size" in wanted:
        cap = max_atlas_bytes()
        threshold = int(cap * ATLAS_SIZE_BAND)
        # The named fault of the TOLERANCES row: "a synthetic atlas at 61 MiB".
        # Synthetic in the size, not in the bytes -- the predicate is pure over
        # the byte count, so writing 61 MiB to prove it would test the filesystem.
        sixty_one = 61 * 1024 * 1024
        band = atlas_size_band(sixty_one, cap)
        if not band["in_band"]:
            findings.append("atlas-size: a synthetic atlas at 61 MiB did not enter "
                            "the band (threshold %d of cap %d)" % (threshold, cap))
        if band["over_cap"]:
            findings.append("atlas-size: 61 MiB read as ABOVE the cap -- the band "
                            "and the cap are being confused")
        check, over = atlas_size_check([("synthetic.png", sixty_one)], cap, False)
        if check["status"] != "fail" or check.get("tolerance") != TOLERANCE:
            findings.append("atlas-size: the tolerance check did not fire on 61 MiB "
                            "(%r)" % check)
        if over:
            findings.append("atlas-size: 61 MiB was reported as over the cap")
        accepted, _o = atlas_size_check([("synthetic.png", sixty_one)], cap, True)
        if accepted["status"] != "fail" or not accepted["accepted"]:
            findings.append("atlas-size: --accept-atlas-size changed the "
                            "MEASUREMENT rather than recording the decision")
        cleared, _blocked = kc.compute_consumable(
            {"stage": STAGE, "mode": "build", "exit_code": kc.EXIT_OK,
             "checks": [accepted], "accepted": {TOLERANCE: True}},
            cfg={"ai": {"required_stages": [], "forbidden_stages": [STAGE]}})
        if not cleared:
            findings.append("atlas-size: an accepted band did not clear "
                            "compute_consumable's tolerance conjunct")
        blocked, _why = kc.compute_consumable(
            {"stage": STAGE, "mode": "build", "exit_code": kc.EXIT_OK,
             "checks": [check], "accepted": {}},
            cfg={"ai": {"required_stages": [], "forbidden_stages": [STAGE]}})
        if blocked:
            findings.append("atlas-size: an UNaccepted band was still consumable")
        # The boundary, both sides. A bound with no boundary case is a bound
        # nobody has run.
        if atlas_size_band(threshold - 1, cap)["in_band"]:
            findings.append("atlas-size: threshold-1 entered the band")
        if not atlas_size_band(threshold, cap)["in_band"]:
            findings.append("atlas-size: the threshold itself did not enter the band")
        if atlas_size_band(cap, cap)["over_cap"]:
            findings.append("atlas-size: exactly the cap read as over it")
        if not atlas_size_band(cap + 1, cap)["over_cap"]:
            findings.append("atlas-size: cap+1 did not read as over the cap")
        # The hard cap has NO acceptance path, and that is the point of the split.
        _c, over_flagged = atlas_size_check([("big.png", cap + 1)], cap, True)
        if not over_flagged:
            findings.append("atlas-size: --accept-atlas-size suppressed an OVER-CAP "
                            "atlas -- the flag is for the band, never for the cap")

    if "grid" in wanted:
        # A real run dir with a real cached base, because P7 ("the English atlas
        # is on disk") is evaluated BEFORE the per-cell rules and would otherwise
        # absorb every case below into one uninformative finding.
        gtmp = tempfile.mkdtemp(prefix="kz-recompose-grid.")
        try:
            os.makedirs(os.path.join(gtmp, "assets"))
            Image.fromarray(np.zeros((12, 24, 3), dtype=np.uint8), mode="RGB").save(
                os.path.join(gtmp, "assets", "x.png"), format="PNG")

            def inventory(pixels):
                return {"atlases": [{"atlas_id": "s", "english_url": "u",
                                     "local": "<run_dir>/assets/x.png",
                                     "grid": {"num_width": 3, "num_height": 2},
                                     "pixels": pixels, "cell_pixels": [8, 6]}]}

            # P4: a grid whose cells do not tile the canvas is refused before any
            # pixel is read, because the totality identity would otherwise be
            # true of a canvas with an unexamined strip down its edge.
            _s, f = plan_sheets(inventory([25, 12]),
                                [{"sheet": "s", "cell": 0, "row": 0, "col": 0,
                                  "filename": "a.png"}], gtmp)
            if not any("does not tile" in x for x in f):
                findings.append("grid: a 25x12 canvas over 3x2 cells of 8x6 was "
                                "accepted")
            # P3: an atlas the inventory never measured.
            unmeasured = inventory([24, 12])
            unmeasured["atlases"][0]["cell_pixels"] = None
            _s, f = plan_sheets(unmeasured, [{"sheet": "s", "cell": 0, "row": 0,
                                              "col": 0, "filename": "a.png"}], gtmp)
            if not any("never measured" in x for x in f):
                findings.append("grid: an unmeasured atlas was accepted")
            # P5: cell must equal row*num_width+col.
            _s, f = plan_sheets(inventory([24, 12]),
                                [{"sheet": "s", "cell": 4, "row": 0, "col": 1,
                                  "filename": "a.png"}], gtmp)
            if not any("not consistent" in x for x in f):
                findings.append("grid: cell=4 at row=0 col=1 on a 3-wide grid was "
                                "accepted")
            # P6: two records claiming one cell -- one cell asserted twice and
            # another never, which is the totality identity's other failure mode.
            _s, f = plan_sheets(inventory([24, 12]),
                                [{"sheet": "s", "cell": 0, "row": 0, "col": 0,
                                  "filename": "a.png"},
                                 {"sheet": "s", "cell": 0, "row": 0, "col": 0,
                                  "filename": "b.png"}], gtmp)
            if not any("is claimed by both" in x for x in f):
                findings.append("grid: two tiles claiming one cell was accepted")
            # P1: a record with no cell is refused rather than defaulted.
            _s, f = plan_sheets(inventory([24, 12]),
                                [{"sheet": "s", "row": 0, "col": 0,
                                  "filename": "a.png"}], gtmp)
            if not any("declares no cell" in x for x in f):
                findings.append("grid: a record with no cell was defaulted rather "
                                "than refused")
            # P2: a sheet nothing in the inventory knows about.
            _s, f = plan_sheets(inventory([24, 12]),
                                [{"sheet": "ghost", "cell": 0, "row": 0, "col": 0,
                                  "filename": "a.png"}], gtmp)
            if not any("no atlas-inventory.json record" in x for x in f):
                findings.append("grid: an unknown sheet id was accepted")
            # P7: the cached English atlas is not optional -- it IS the unused
            # cells, so an absent base is a refusal and never a blank canvas.
            gone = inventory([24, 12])
            gone["atlases"][0]["local"] = "<run_dir>/assets/absent.png"
            _s, f = plan_sheets(gone, [{"sheet": "s", "cell": 0, "row": 0, "col": 0,
                                        "filename": "a.png"}], gtmp)
            if not any("base canvas is not optional" in x for x in f):
                findings.append("grid: an absent English base was accepted")
            # The clean control: the same inventory with a well-formed record
            # must produce exactly one sheet and no findings at all.
            good, f = plan_sheets(inventory([24, 12]),
                                  [{"sheet": "s", "cell": 4, "row": 1, "col": 1,
                                    "filename": "a.png"}], gtmp)
            if f or len(good) != 1 or good[0]["used_cells"] != [4] \
                    or good[0]["unused_cells"] != [0, 1, 2, 3, 5]:
                findings.append("grid: the clean control did not plan cleanly "
                                "(%r / %r)" % (f, good))
        finally:
            import shutil
            shutil.rmtree(gtmp, ignore_errors=True)

    if "a1" in wanted or "a2" in wanted:
        tmp = tempfile.mkdtemp(prefix="kz-recompose-selftest.")
        try:
            sheet, typeset_dir, setup = _synth_sheet(tmp)
            if setup:
                findings.append("setup: the synthetic sheet is not total: %s" % setup)
            dst = os.path.join(tmp, "out.png")
            image, base_cells, faults = paste_sheet(sheet)
            if faults:
                findings.append("setup: the clean synthetic paste failed: %s" % faults)
            else:
                write_png(dst, image)
                image.close()
                clean = assert_from_disk(dst, sheet, base_cells)
                if clean["a1"] or clean["a2"] or clean["error"]:
                    findings.append("setup: the clean control did not pass "
                                    "(a1=%r a2=%r err=%r)"
                                    % (clean["a1"], clean["a2"], clean["error"]))
                if clean["cells_asserted"] != sheet["cells_total"]:
                    findings.append("setup: the clean control asserted %d of %d cells"
                                    % (clean["cells_asserted"], sheet["cells_total"]))

                if "a1" in wanted:
                    # Plant the fault IN THE FILE, in a USED cell, after the write.
                    arr, _s, _m, _f = read_rgb(dst)
                    arr = arr.copy()
                    arr[1, 1] = (arr[1, 1].astype(np.int16) ^ 0xFF).astype(np.uint8)
                    write_png(dst, Image.fromarray(arr, mode="RGB"))
                    hit = assert_from_disk(dst, sheet, base_cells)
                    if not hit["a1"]:
                        findings.append("a1: a corrupted USED cell on disk did not "
                                        "fire A1")
                    if hit["a2"]:
                        findings.append("a1: corrupting a used cell also fired A2 -- "
                                        "the two populations are not disjoint")

                if "a2" in wanted:
                    # Cell 2 is unused on the synthetic sheet: (0,1) in cell 2 is
                    # at x = 2*8 + 1, y = 1.
                    image2, base_cells2, _f2 = paste_sheet(sheet)
                    write_png(dst, image2)
                    image2.close()
                    arr, _s, _m, _f = read_rgb(dst)
                    arr = arr.copy()
                    row, col = divmod(sheet["unused_cells"][0], sheet["num_width"])
                    box = cell_box(row, col, sheet["cell_width"], sheet["cell_height"])
                    arr[box[1] + 1, box[0] + 1] = (
                        arr[box[1] + 1, box[0] + 1].astype(np.int16) ^ 0xFF
                    ).astype(np.uint8)
                    write_png(dst, Image.fromarray(arr, mode="RGB"))
                    hit = assert_from_disk(dst, sheet, base_cells2)
                    if not hit["a2"]:
                        findings.append("a2: a corrupted UNUSED cell on disk did not "
                                        "fire A2 -- the English pixels are not being "
                                        "asserted")
                    if hit["a1"]:
                        findings.append("a2: corrupting an unused cell also fired A1")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ===========================================================================
# 11. CLI
# ===========================================================================

_SUMMARY = {
    "cap-import": "MAX_ATLAS_BYTES is imported from compose-card-atlas.py, this "
                  "file carries no literal copy of it, and an absent source is a "
                  "named precondition rather than a fallback",
    "totality": "used + unused == num_width * num_height, with the complement "
                "DERIVED; an out-of-range cell and a duplicated one both fire",
    "atlas-size": "a synthetic atlas at 61 MiB fires the band at 22, "
                  "--accept-atlas-size records the decision without moving the "
                  "measurement and clears compute_consumable, "
                  "threshold-1/threshold/cap/cap+1 all read correctly, and the "
                  "flag never clears an over-cap atlas",
    "a1": "a used cell corrupted IN THE WRITTEN FILE fires A1 at 20 and does not "
          "fire A2",
    "a2": "an unused cell corrupted IN THE WRITTEN FILE fires A2 at 21, so the "
          "English pixels are asserted and not merely inherited",
    "grid": "a canvas the cells do not tile, an inconsistent cell/row/col, a "
            "record with no cell and an unknown sheet id are each refused",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_recompose.py",
        description="koreanize stage `recompose` -- paste the typeset tiles onto "
                    "the decoded English atlas (design §5.4, §6 step 15).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--accept-atlas-size", action="store_true",
                        help="accept an atlas at or above %s of the imported "
                             "MAX_ATLAS_BYTES (TOLERANCES row %s). It never "
                             "accepts an atlas ABOVE the cap."
                             % ("%d%%%%" % int(ATLAS_SIZE_BAND * 100), TOLERANCE))
    parser.add_argument("--verify-only", action="store_true",
                        help="re-assert A1/A2 over an existing atlases/ tree")
    parser.add_argument("--dry-run", action="store_true",
                        help="write <run_dir>/dry-run/recompose/ only")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_recompose --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-11s %s" % (name, _SUMMARY[name]))
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    mode = "build"
    atlases_dir = None
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"
        # The rehearsal root is NAMED, not a sibling of atlases/ (§4.1): a
        # sibling would land inside <run_dir> where the guard, which refuses
        # writes outside it, could never fire on it.
        atlases_dir = os.path.join(run_dir, "dry-run", STAGE, "atlases")

    report = run_recompose(run_dir, mode=mode, verify_only=args.verify_only,
                           accept_atlas_size=args.accept_atlas_size,
                           atlases_dir=atlases_dir, quiet=args.quiet)

    dest = run_dir
    if args.dry_run:
        dest = os.path.join(run_dir, "dry-run", STAGE)
        kz.assert_dry_run_dest(
            kz.load_scenario(os.path.join(run_dir, "scenario.json")), STAGE, dest)
        os.makedirs(dest, exist_ok=True)
    path = kc.write_report(report, dest)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize recompose -- %s" % report["slug"])
        print("  sheets          : %d (%d recomposed, %d verified)"
              % (counts["sheets"], counts["recomposed"], counts["verified"]))
        print("  cells           : %d used + %d unused = %d"
              % (counts["cells_used"], counts["cells_unused"], counts["cells_total"]))
        print("  asserted        : %d cells / %d px"
              % (counts["cells_asserted"], counts["pixels_asserted"]))
        print("  cap             : %d bytes (imported from compose-card-atlas.py)"
              % counts["max_atlas_bytes"])
        for check in report["checks"]:
            print("  %-34s: %-4s  %s"
                  % (check["name"], check["status"], "; ".join(check["detail"][:2])))
        print("  verdict         : %s (exit %d, consumable %s)"
              % (report["verdict"], report["exit_code"], report["consumable"]))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
