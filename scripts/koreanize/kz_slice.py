#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `slice` -- the first arithmetic stage (design §6 step 11, §5.4).

ART TIER (design §5.9): `#!/usr/bin/env python3`. NOT a member of
`kz_common.STDLIB_TIER`, so it may import PIL -- and it does, lazily, inside the
functions that need pixels, so the arithmetic half stays importable and testable
on any interpreter.

WHAT THIS STAGE IS, IN ONE SENTENCE
-----------------------------------
It cuts each English atlas into per-card slices, writes a manifest that names
every emitted byte by sha256, and builds one contact sheet a human can read.

THE CONSTANT TABLE IS THE THING BEING DELETED (R-A)
----------------------------------------------------
`slice-atlases.py:48-53` carries `EXPECTED_SHEET_DIMS`, three hand-recorded
`(width, height, cols, rows)` tuples, and `CELL_W, CELL_H = 750, 1050` at `:46`.
That table is why the Midwinter script generalises to nothing: §5.4's whole claim
is that `cell_pixels` is *measured per atlas*, so Woods of the Black Goat's 88
`(1,1)` sheets and Labyrinths' `(10,7)` fall out of the same code path with no
new constants. `init` already did the measuring (`kz_init.measure_atlases`), so
this module's job is to CONFIRM that measurement against the file it is about to
cut and to derive the cell from it -- never to carry a table of its own. There is
deliberately no `CELL_W` in this file.

`norm()` IS KEPT; THE TABLE IT CONFIRMED AGAINST IS NOT
-------------------------------------------------------
The URL-to-file half of `slice-atlases.py` is sound and survives verbatim in
meaning: `norm()` at `:56-57` strips every non-alphanumeric character and
lowercases, because `images-ko/assets/` names each download after its source URL
with the punctuation removed. That normalisation is the ONLY way to get from a
`FaceURL` back to that file, and it is kept (`norm()` below, proven equal to the
regex form by `--selftest norm`).

What is *not* kept is what the mapping was then confirmed against. The legacy
confirmed the mapped file's measured pixels against `EXPECTED_SHEET_DIMS[sid]`,
so a correct mapping to an unlisted sheet was a `KeyError` and a new scenario was
a code edit. Here the confirmation is against `atlas-inventory.json`'s recorded
`pixels` -- the dims `init` measured off the file it downloaded -- so the
predicate is the same strength and needs no table.

RESOLUTION IS TWO STRATEGIES, AND CONTENT ADDRESSING COMES FIRST
-----------------------------------------------------------------
`init` writes its downloads to `<run_dir>/assets/<sha256>.png` (§5.1 step 5),
content-addressed precisely so a second scenario cannot overwrite the first's
sheet. That path is exact, so it is tried first and `norm()` is not consulted at
all. `norm()` is the fallback for the URL-stem-named read-only tier
(`images-ko/assets/`, §3.1) and for any root named with `--atlas-root`.

The fallback is also where the legacy had a latent defect worth naming: it took
`files[0]` in `os.listdir` order, so two files whose normalised names both prefix
one URL resolved nondeterministically. Here the candidate set is collected in
full and sorted; two candidates with two different digests is an ambiguity, not a
coin flip, and it refuses at exit 14.

`single_card` IS A COPY, AND THE RECORD SAYS SO
------------------------------------------------
A `(1,1)` atlas has `cell_pixels == pixels`, so the crop is the identity -- but
`crop()` + `save()` is a re-encode, and a re-encode is not byte-stable across
Pillow versions. §5.8 makes the golden fixture a BYTE comparison pinned to one
interpreter triple, so re-encoding an untouched sheet would put a Pillow upgrade
inside the regression instrument for no gain at all. `single_card` therefore
emits the source file's bytes verbatim and the manifest records
`"operation": "copy"` with `sha256 == source_sha256`, which is a checkable claim
rather than a comment (check SL6). A consumer that needs RGBA converts on open,
exactly as it must for a cropped slice whose source was a JPEG.

WHERE THE REFUSALS ARE, AND WHY THEY ARE THERE AND NOT IN `checks[]`
---------------------------------------------------------------------
Two bands, and the split is by whether anything could have been written yet.

  * PREFLIGHT -- atlas resolution, the measured-dims confirmation, the
    integrality assertion and the cell-arithmetic round-trip. These are facts
    about the INPUTS, they are evaluated before the first byte is written, and
    they `kc.refuse`. Nothing is written, so no report is written either: the
    same shape `kz_init.measure_atlases` uses for the identical fact, down to the
    message string `"atlas pixels do not divide by its grid"` so an operator
    greps one phrase and finds both. When the preflight PASSES its four
    assertions are still recorded as `checks[]` rows (SL1-SL5) carrying the code
    they would have refused with -- a report whose check list is total is one an
    operator can read without knowing which half of the module ran.

  * EMISSION -- the re-read of every written file, the `single_card` digest
    identity and the manifest's set equality with the directory. These are facts
    about the OUTPUT, they can only be evaluated after writing, and they
    accumulate into `triggered` and are reported (SL6-SL8).

EXIT CODES (§4.2)
   0  every slice written, every check passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- a planned write outside <run_dir>, or a <run_dir>
      that lands inside guard.write_roots or matches guard.forbidden
  13  precondition -- scenario.json / atlas-inventory.json / card-text-en.json
      missing, an atlas `init` never measured, no file for a referenced atlas,
      or ATLAS PIXELS THAT DO NOT DIVIDE BY THE DECLARED GRID
  14  input drift -- the file's measured dims moved off the recorded ones, its
      digest moved, two files claim one URL, or scenario.json's `cell_pixels` /
      `single_card` / grid disagree with the inventory or with card-text-en.json
  20  hard rule A -- CELL ARITHMETIC. A slice would have been cut from the wrong
      cell: the cell/row/col round-trip failed, the card_id round-trip failed,
      the crop box left the sheet, or two objects claimed one output filename at
      two different cells
  21  hard rule B -- EMITTED-ARTIFACT IDENTITY. A file re-read from disk is not
      the file that was written: wrong dims, a digest that moved, a `single_card`
      slice that is not its atlas byte for byte, or a manifest that does not
      describe exactly the set of files in slices/

THERE IS NO 30 IN THAT TABLE. `slice` is not one of §3.2's four gated stages
(`init`, `terms`, `mask`, `typeset`), so its report carries `gate: null` and
nothing downstream is blocked on a human. The contact sheet is still built, and
it is not decoration: `mask` IS gated, its gate is the one §1.2 calls "contact
sheet", and the pixels that gate adjudicates are these. Building it here, from
the manifest that names every byte, is what makes that later review possible at
all.

NO AI, AND THAT IS CODE (§3.2, §5.4)
-------------------------------------
`kc.declare_ai(STAGE, required=False)` runs in the module body below. It
registers the prohibition and asserts it against `ai.forbidden_stages` the moment
a scenario.json is bound -- and `kc.assert_ai_contract`, which `kc.write_report`
calls on the build path, refuses at exit 4 if this stage ever tries to write a
build report carrying a non-null `ai` block. So the module imports cleanly, which
it must, and still cannot ship an AI-influenced artifact.
"""

import argparse
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402

STAGE = "slice"

# Declared in the MODULE BODY (§3.2). A scenario.json that does not list `slice`
# in ai.forbidden_stages refuses at exit 4 the moment it is bound.
kc.declare_ai(STAGE, required=False)

FAULTS = ("norm", "resolve", "integrality", "geometry", "single-card",
          "reread", "guard")

#: Where the emitted slices, their manifest and the contact sheet land, relative
#: to the output root (`<run_dir>`, or `<run_dir>/dry-run/slice/` under
#: --dry-run). Named here because three functions and the §5.11 package
#: assertion -- "every included file present in `slices/manifest.json`" -- all
#: have to agree on them.
SLICES_DIR = "slices"
MANIFEST_NAME = "manifest.json"
GALLERY_DIR = "galleries"
CONTACT_SHEET_NAME = "slice-contact-sheet.html"

#: The read-only tier `norm()` exists for (§3.1). Never written by any stage.
DEFAULT_ATLAS_ROOTS = (os.path.join("images-ko", "assets"),)

#: A ceiling on the pixel count this stage will decode, in the same spirit as
#: `kz_init.MAX_ATLAS_FETCH_BYTES` and for the same reason: an atlas URL comes
#: out of the mod tree, so its dimensions are third-party input and Pillow's
#: decompression-bomb guard is the thing standing between that and the heap.
#: Pillow's own default is ~89 MP, which the 6000x5250 reference sheets are
#: comfortably under -- but a 10x7 sheet of 750x1050 cells is 55 MP and an
#: 88-atlas scenario is not the shape this figure was chosen against, so the cap
#: is raised DELIBERATELY and to a stated number rather than disabled.
MAX_ATLAS_PIXELS = 256 * 1024 * 1024


# ---------------------------------------------------------------------------
# 1. URL <-> file normalisation -- `slice-atlases.py:56-57`, kept
# ---------------------------------------------------------------------------

def norm(text):
    """Bare-alphanumeric-lowercase, exactly `slice-atlases.py:56-57`.

        def norm(s):
            return re.sub(r"[^a-zA-Z0-9]", "", s).lower()

    Written as a character walk rather than a regex for the same reason
    `kz_checkers.scan` is: this repository's checkers do not lean on a regex
    engine to establish a property. The two forms are asserted EQUAL over a
    corpus of real Steam URLs by `--selftest norm`, so "kept" is a checked claim
    and not a comment -- if this ever stops agreeing with the legacy form, every
    file in `images-ko/assets/` becomes unreachable at once and silently.
    """
    return "".join(ch for ch in (text or "") if ch.isalnum() and ch.isascii()).lower()


def candidate_files(root):
    """Sorted listing of `root`, or [] when it does not exist.

    SORTED, which the legacy's `os.listdir` order was not. Two files whose
    normalised names both prefix one URL are an ambiguity to be reported, not a
    coin flip to be taken -- see `resolve_atlas_file`.
    """
    if not root or not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root)
                  if os.path.isfile(os.path.join(root, name)))


def resolve_atlas_file(record, run_dir, search_roots=()):
    """Locate the decoded English sheet for one inventory record.

    Returns {"path", "how", "root"}. Refuses at 13 when nothing matches and at
    14 when two files with different bytes both claim the URL.

    STRATEGY 1 -- content address. `init` writes `<run_dir>/assets/<sha256>.png`
    and records that digest, so the path is exact and `norm()` is not consulted.
    STRATEGY 2 -- `norm()`, for the URL-stem-named read-only tier.
    """
    url = record.get("english_url")
    digest = record.get("sha256")
    if digest:
        exact = os.path.join(str(run_dir), "assets", "%s.png" % digest)
        if os.path.exists(exact):
            return {"path": exact, "how": "content-address", "root": "<run_dir>/assets"}

    prefix = norm(url)
    if not prefix:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "an atlas record carries no english_url to resolve",
                  "atlas_id=%r -- re-run `init`" % record.get("atlas_id"))

    hits = []
    for root in search_roots:
        for name in candidate_files(root):
            if norm(name).startswith(prefix):
                hits.append(os.path.join(root, name))
    if not hits:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "no file on disk matches a referenced atlas",
                  "%s -- searched %s. Re-run `init` with the network, or name a "
                  "root with --atlas-root."
                  % (url, [str(r) for r in search_roots] or "(no roots)"))

    by_digest = {}
    for path in hits:
        by_digest.setdefault(kc.sha256_file(path), []).append(path)
    if len(by_digest) > 1:
        # The legacy took files[0] in listdir order here and could not see this.
        kc.refuse(kc.EXIT_DRIFT,
                  "two files with different bytes both claim one atlas URL",
                  "%s -> %s" % (url, sorted(p for group in by_digest.values()
                                            for p in group)))
    return {"path": sorted(hits)[0], "how": "url-normalisation",
            "root": os.path.dirname(sorted(hits)[0])}


# ---------------------------------------------------------------------------
# 2. Grid arithmetic -- the measured cell, and the integrality refusal
# ---------------------------------------------------------------------------

def measure_png(path):
    """(width, height) of an image file, with the bomb guard raised to a STATED
    ceiling and restored afterwards.

    Restored because `Image.MAX_IMAGE_PIXELS` is process-global: a stage that
    raised it and walked away would leave every later decode in the same process
    -- including a test's, including `mask`'s under `golden` -- unguarded.
    """
    from PIL import Image
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_ATLAS_PIXELS
    try:
        with Image.open(path) as image:
            return image.size
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def cell_pixels_for(record, measured=None):
    """(cell_w, cell_h) for one atlas, DERIVED -- never looked up.

    `measured` is the dims read off the file that is about to be cut; when it is
    given it must equal the record's own `pixels`, because a file whose size no
    longer matches what `init` measured is a different file (exit 14).

    THE THREE REFUSALS, and the codes are not interchangeable:

      13  the grid is not a positive integer pair, the atlas was never measured,
          or W % num_width / H % num_height is non-zero. The last is §5.4's
          integrality assertion: a sheet whose pixels do not divide by its
          declared grid has NO cell size, so there is nothing to derive and the
          stage cannot proceed. It is a PRECONDITION -- the same class and the
          same code `kz_init.measure_atlases` refuses it with, and the same
          message, so one grep finds both sites.
      14  the record's stored `cell_pixels` or `single_card` disagrees with what
          the same record's own `pixels` and `grid` derive. Nothing is wrong with
          the atlas; two fields of one record have drifted apart, which means
          scenario.json was edited after `init` wrote it.
    """
    grid = record.get("grid") or {}
    num_width = grid.get("num_width")
    num_height = grid.get("num_height")
    url = record.get("english_url")

    if not _positive_int(num_width) or not _positive_int(num_height):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "atlas grid is not a positive integer pair",
                  "%s: num_width=%r num_height=%r" % (url, num_width, num_height))

    pixels = record.get("pixels")
    if not _pixel_pair(pixels):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "atlas was never measured, so its cell size cannot be derived",
                  "%s: pixels=%r. `init` records null pixels for a sheet it could "
                  "not fetch (§5.1 step 5); re-run it with the network."
                  % (url, pixels))
    width, height = int(pixels[0]), int(pixels[1])

    if measured is not None and (int(measured[0]), int(measured[1])) != (width, height):
        kc.refuse(kc.EXIT_DRIFT,
                  "atlas on disk does not match the dims `init` measured",
                  "%s: file is %dx%d, atlas-inventory.json records %dx%d"
                  % (url, measured[0], measured[1], width, height))

    # §5.4's integrality assertion. The message is `kz_init.py`'s, verbatim.
    if width % num_width or height % num_height:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "atlas pixels do not divide by its grid",
                  "%s: %dx%d over a %dx%d sheet -- %d %% %d = %d, %d %% %d = %d"
                  % (url, width, height, num_width, num_height,
                     width, num_width, width % num_width,
                     height, num_height, height % num_height))

    derived = [width // num_width, height // num_height]
    recorded = record.get("cell_pixels")
    if recorded is not None and [int(recorded[0]), int(recorded[1])] != derived:
        kc.refuse(kc.EXIT_DRIFT,
                  "recorded cell_pixels disagree with the measured sheet",
                  "%s: record says %s, %dx%d over %dx%d derives %s"
                  % (url, list(recorded), width, height, num_width, num_height,
                     derived))

    single = (num_width == 1 and num_height == 1)
    if record.get("single_card") is not None and bool(record["single_card"]) != single:
        kc.refuse(kc.EXIT_DRIFT,
                  "recorded single_card disagrees with the declared grid",
                  "%s: record says single_card=%r, grid is %dx%d"
                  % (url, record.get("single_card"), num_width, num_height))

    return derived[0], derived[1]


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _pixel_pair(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and _positive_int(value[0]) and _positive_int(value[1]))


# ---------------------------------------------------------------------------
# 3. The plan -- one record per slice, before a byte is written
# ---------------------------------------------------------------------------

def slice_stem(obj):
    """The output basename's stem.

    `<arkham_id>` when there is one, which is the naming
    `.am/midwinter-gala-korean/slices/` already uses and which `build-masks.py`
    and the golden fixture both read. An object with no ArkhamDB id still has
    pixels worth cutting, so it falls back to its GUID under an `obj-` prefix --
    a namespace an arkham id can never collide with, since arkham ids are digits.
    """
    arkham_id = obj.get("arkham_id")
    if arkham_id:
        return str(arkham_id)
    return "obj-%s" % (obj.get("guid") or "unknown")


def plan_slices(objects, atlas_by_url, cell_by_url):
    """One record per emittable slice, plus the objects that carry no cell.

    EVERY ARITHMETIC ASSERTION IS HERE, and every one of them refuses at exit 20,
    because every one of them means the same thing: a slice would have been cut
    from the wrong cell. `slice-atlases.py` calls this out in its own docstring --
    "fails loudly on any mismatch instead of silently slicing the wrong cell" --
    and it is the single defect this stage exists to make impossible, because a
    wrong-cell slice is *plausible*: it is a real card at the right size, and it
    survives every downstream stage to land in the langpack as somebody else's
    art.

      1. cell == row * num_width + col            (the grid round-trip)
      2. card_id == deck_key * 100 + cell         (TTS's own addressing)
      3. row < num_height                         (inside the declared sheet)
      4. the crop box lies inside the measured pixels
      5. no two objects claim one output filename at two different cells
    """
    records, without_cell, seen = [], [], {}

    for obj in objects:
        if obj.get("cell") is None:
            without_cell.append(obj)
            continue

        url = obj.get("atlas_id")
        atlas = atlas_by_url.get(url)
        if atlas is None:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "an object references an atlas the inventory does not carry",
                      "%s (%s): atlas_id=%r. Re-run `init`."
                      % (slice_stem(obj), obj.get("object_id"), url))

        grid = atlas["record"].get("grid") or {}
        num_width = grid["num_width"]
        num_height = grid["num_height"]
        cell = obj["cell"]
        row = obj.get("row")
        col = obj.get("col")

        # The object's OWN grid must agree with the inventory's. They come from
        # the same CustomDeck block, so a disagreement means one of the two
        # artifacts was edited after `init` wrote both -- exit 14, not 20: the
        # arithmetic is not wrong, the inputs no longer describe one sheet.
        if (obj.get("num_width"), obj.get("num_height")) != (num_width, num_height):
            kc.refuse(kc.EXIT_DRIFT,
                      "card-text-en.json and atlas-inventory.json declare "
                      "different grids for one atlas",
                      "%s: object says %rx%r, inventory says %dx%d (%s)"
                      % (slice_stem(obj), obj.get("num_width"),
                         obj.get("num_height"), num_width, num_height, url))

        if not isinstance(row, int) or not isinstance(col, int) \
                or cell != row * num_width + col:
            kc.refuse(kc.EXIT_RULE_A, "cell != row*num_width + col",
                      "%s: cell=%r row=%r col=%r num_width=%d (%s)"
                      % (slice_stem(obj), cell, row, col, num_width, url))
        if row >= num_height:
            kc.refuse(kc.EXIT_RULE_A, "cell is outside the declared grid",
                      "%s: cell=%d is row %d of a %dx%d sheet (%s)"
                      % (slice_stem(obj), cell, row, num_width, num_height, url))

        card_id, deck_key = obj.get("card_id"), obj.get("deck_key")
        if card_id is not None and deck_key is not None:
            if card_id != int(deck_key) * 100 + cell:
                kc.refuse(kc.EXIT_RULE_A, "card_id != deck_key*100 + cell",
                          "%s: card_id=%r deck_key=%r cell=%d"
                          % (slice_stem(obj), card_id, deck_key, cell))

        cell_w, cell_h = cell_by_url[url]
        width, height = atlas["record"]["pixels"]
        box = (col * cell_w, row * cell_h, (col + 1) * cell_w, (row + 1) * cell_h)
        if box[2] > width or box[3] > height:
            kc.refuse(kc.EXIT_RULE_A, "crop box leaves the sheet",
                      "%s: %s on a %dx%d sheet (%s)"
                      % (slice_stem(obj), list(box), width, height, url))

        name = "%s.png" % slice_stem(obj)
        key = (name.casefold(), url, cell)
        prior = seen.get(name.casefold())
        if prior is not None and prior != key:
            # Case-folded, because the volume this runs on is case-insensitive:
            # `71001.png` and `71001.PNG` are ONE file here and two everywhere
            # the langpack is later built, which is the shape of the
            # `Investigatortokens` rename in CLAUDE.md.
            kc.refuse(kc.EXIT_RULE_A,
                      "two objects claim one slice filename at different cells",
                      "%s: %s vs %s" % (name, list(prior[1:]), list(key[1:])))
        seen[name.casefold()] = key

        records.append({
            "arkham_id": obj.get("arkham_id"),
            "guid": obj.get("guid"),
            "object_id": obj.get("object_id"),
            "side": "face",
            "atlas_id": atlas["record"].get("atlas_id"),
            "english_url": url,
            "cell": cell, "row": row, "col": col,
            "num_width": num_width, "num_height": num_height,
            "card_id": card_id, "deck_key": deck_key,
            "pack": obj.get("pack"),
            "nickname": obj.get("nickname"),
            "description": obj.get("description"),
            "cell_pixels": [cell_w, cell_h],
            "box": list(box),
            "operation": "copy" if atlas["single_card"] else "crop",
            "source_sha256": atlas["record"].get("sha256"),
            "filename": name,
            "sha256": None,
            "bytes": None,
        })

    records.sort(key=lambda r: (r["english_url"], r["cell"], r["filename"]))
    return records, without_cell


# ---------------------------------------------------------------------------
# 4. Emission -- the crop, the copy, and the atomic binary write
# ---------------------------------------------------------------------------

def _atomic_write_bytes(path, data):
    """Same-directory temp + `os.replace`, mirroring `sced_io.atomic_write_json`.

    A LOCAL MIRROR AND NOT A CALL, because `sced_io` offers text and JSON writers
    only (`sced_io.py:16,38`) and §7 forbids editing it from this change set. The
    shape is copied deliberately -- temp file in the destination directory, unlink
    on any failure, `os.replace` to commit -- so a crash mid-write can never leave
    a half-written PNG that the re-read check would then hash and record.
    """
    directory = os.path.dirname(str(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    handle, tmp = tempfile.mkstemp(prefix=os.path.basename(str(path)) + ".",
                                   suffix=".tmp", dir=directory or ".")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(data)


def slice_bytes(atlas_path, record, single_card):
    """The bytes one slice is made of.

    `single_card` -> the atlas file's own bytes, verbatim (§5.4: "a copy, not a
    crop"). Everything else -> `crop()` on an RGBA-converted sheet, encoded PNG.

    RGBA and not the source mode, because `composite` is specified as
    `where(mask.alpha == 255, slice, delivered)` (§5.4) and a slice with no alpha
    channel makes that expression a type error one stage later, in a module that
    did not produce it.
    """
    if single_card:
        with open(atlas_path, "rb") as stream:
            return stream.read()
    from PIL import Image
    previous = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_ATLAS_PIXELS
    try:
        with Image.open(atlas_path) as sheet:
            tile = sheet.convert("RGBA").crop(tuple(record["box"]))
        buffer = io.BytesIO()
        tile.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        Image.MAX_IMAGE_PIXELS = previous


def emit_slices(records, atlas_by_url, out_root):
    """Write every planned slice. Returns the total byte count.

    Each atlas is opened once per slice rather than held open across the whole
    run: an 88-atlas scenario would otherwise hold 88 decoded sheets, and the
    88-atlas case is exactly the one §5.4 says this code path has to survive.
    """
    written = 0
    for record in records:
        atlas = atlas_by_url[record["english_url"]]
        data = slice_bytes(atlas["path"], record, atlas["single_card"])
        path = os.path.join(out_root, SLICES_DIR, record["filename"])
        written += _atomic_write_bytes(path, data)
        record["bytes"] = len(data)
        record["sha256"] = kc.sha256_bytes(data)
    return written


def verify_emitted(records, out_root):
    """Re-read every file from disk. Returns (checks, triggered).

    RE-READ AND NOT REMEMBER, which is `slice-atlases.py`'s own rule ("Every
    emitted file is reopened after writing and its dimensions re-asserted") and
    §5.4's for `composite` ("re-read every written file"). What it buys is
    narrow and real: it catches a truncated write, a destination that was a
    symlink into somewhere else, and a second writer racing this one -- none of
    which the in-memory bytes can see.

    THREE CHECKS, all exit 21, because all three say the same thing: the file on
    disk is not the file this run decided to write.
    """
    from_disk, missing, mismatched, not_a_copy = [], [], [], []

    for record in records:
        path = os.path.join(out_root, SLICES_DIR, record["filename"])
        if not os.path.exists(path):
            missing.append(record["filename"])
            continue
        from_disk.append(record["filename"])
        digest = kc.sha256_file(path)
        if record.get("sha256") and digest != record["sha256"]:
            mismatched.append("%s: on disk %s, manifest %s"
                              % (record["filename"], digest[:16],
                                 record["sha256"][:16]))
            continue
        size = measure_png(path)
        want = (record["cell_pixels"][0], record["cell_pixels"][1])
        if size != want:
            mismatched.append("%s: re-read %dx%d, cell_pixels %dx%d"
                              % (record["filename"], size[0], size[1],
                                 want[0], want[1]))
        if record["operation"] == "copy" and record.get("source_sha256") \
                and digest != record["source_sha256"]:
            not_a_copy.append("%s: single_card slice %s is not its atlas %s"
                              % (record["filename"], digest[:16],
                                 record["source_sha256"][:16]))
        if record["operation"] == "crop" and record.get("source_sha256") \
                and digest == record["source_sha256"]:
            not_a_copy.append("%s: a cropped slice is byte-identical to its "
                              "whole atlas" % record["filename"])

    # Set equality between the manifest and the directory, in both directions.
    # §5.11 clause (e) asserts an assembled package against this manifest, so a
    # file in slices/ that the manifest does not describe is a file that would
    # ship undeclared.
    on_disk = set()
    slices_root = os.path.join(out_root, SLICES_DIR)
    if os.path.isdir(slices_root):
        on_disk = set(name for name in os.listdir(slices_root)
                      if name != MANIFEST_NAME
                      and os.path.isfile(os.path.join(slices_root, name)))
    declared = set(r["filename"] for r in records)
    orphans = sorted(on_disk - declared)
    absent = sorted(declared - on_disk)

    checks = [
        {"id": "SL6", "name": "single_card_is_a_copy",
         "status": "fail" if not_a_copy else "pass",
         "exit_on_fail": kc.EXIT_RULE_B, "detail": not_a_copy},
        {"id": "SL7", "name": "reread_matches_what_was_written",
         "status": "fail" if (missing or mismatched) else "pass",
         "exit_on_fail": kc.EXIT_RULE_B,
         "detail": (["%s: declared but not on disk" % n for n in missing]
                    + mismatched)},
        {"id": "SL8", "name": "manifest_describes_the_directory",
         "status": "fail" if (orphans or absent) else "pass",
         "exit_on_fail": kc.EXIT_RULE_B,
         "detail": (["%s: on disk, not in the manifest" % n for n in orphans]
                    + ["%s: in the manifest, not on disk" % n for n in absent])},
    ]
    triggered = [c["exit_on_fail"] for c in checks if c["status"] == "fail"]
    return checks, triggered


# ---------------------------------------------------------------------------
# 5. The manifest and the contact sheet
# ---------------------------------------------------------------------------

def build_manifest(cfg, records, mapping, counts):
    """`slices/manifest.json` -- the per-file sha256 record §6 step 11 asks for.

    Key-sorted before it is handed over, because `sced_io.py:99` passes no
    `sort_keys` (§5.3): a manifest whose key order follows construction produces
    different bytes every time a constructor is reordered, in a file whose entire
    downstream use is a comparison.
    """
    return kc.sorted_mapping({
        "schema_version": kc.SCHEMA_VERSION,
        "generated_by": "koreanize slice",
        "generated_at": kc.utc_now(),
        "slug": cfg["slug"],
        "binding": {"scenario.json": cfg["config_sha256"]},
        "atlas_mapping": mapping,
        "records": records,
        "counts": counts,
    })


_CONTACT_SHEET = """<!DOCTYPE html>
<meta charset="utf-8">
<title>koreanize slice -- {slug}</title>
<style>
 body {{ font: 13px/1.45 -apple-system, system-ui, sans-serif; margin: 24px;
        background: #14161a; color: #e6e8eb; }}
 h1 {{ font-size: 18px; margin: 0 0 4px; }}
 p.sub {{ color: #9aa3ad; margin: 0 0 20px; }}
 h2 {{ font-size: 14px; margin: 28px 0 4px; }}
 p.meta {{ color: #9aa3ad; margin: 0 0 12px; font-family: ui-monospace, monospace; }}
 .grid {{ display: flex; flex-wrap: wrap; gap: 14px; }}
 figure {{ margin: 0; width: 160px; }}
 img {{ width: 150px; height: auto; background: #24272c; border: 1px solid #333; }}
 figcaption {{ font-size: 11px; }}
 .id {{ font-weight: 600; }}
 .cell {{ color: #9aa3ad; font-family: ui-monospace, monospace; }}
 .sha {{ color: #6b737d; font-family: ui-monospace, monospace; font-size: 10px; }}
 .copy {{ color: #d8a657; }}
</style>
<h1>koreanize <code>slice</code> &mdash; {slug}</h1>
<p class="sub">{total} slices from {atlases} atlas sheet(s). Read for the WRONG
CELL: the id under each tile must be the card in it. Every cell size below was
measured off its own sheet &mdash; there is no table of expected dimensions.</p>
{sections}
"""

_SECTION = """<h2>{url}</h2>
<p class="meta">{width}x{height} &middot; {cols}x{rows} grid &middot; cell
{cell_w}x{cell_h} &middot; {operation} &middot; {file}</p>
<div class="grid">
{tiles}
</div>
"""

_TILE = """  <figure>
    <img src="../{slices}/{file}" loading="lazy" alt="{ident}">
    <figcaption>
      <div class="id">{ident}</div>
      <div>{nickname}</div>
      <div class="cell">cell {cell} &middot; row {row}, col {col}</div>
      <div class="sha">{sha}</div>
    </figcaption>
  </figure>"""


def build_contact_sheet(cfg, records, mapping):
    """The human review surface, self-contained but for the sibling PNGs.

    Grouped by sheet and ordered by cell, with the id, nickname and (row, col)
    printed under each tile -- `build_contact_sheet.py:5-8`'s reasoning, kept
    verbatim in intent: a reviewer must be able to catch a card sliced from the
    wrong cell WITHOUT cross-referencing a separate legend, because a legend that
    has to be consulted is a legend that will not be.
    """
    by_url = {}
    for record in records:
        by_url.setdefault(record["english_url"], []).append(record)

    sections = []
    for entry in mapping:
        url = entry["english_url"]
        group = sorted(by_url.get(url, []), key=lambda r: r["cell"])
        tiles = "\n".join(
            _TILE.format(slices=SLICES_DIR, file=_esc(r["filename"]),
                         ident=_esc(r["arkham_id"] or r["guid"]),
                         nickname=_esc(r.get("nickname") or ""),
                         cell=r["cell"], row=r["row"], col=r["col"],
                         sha=_esc((r.get("sha256") or "")[:16]))
            for r in group)
        sections.append(_SECTION.format(
            url=_esc(url), width=entry["measured_width"],
            height=entry["measured_height"], cols=entry["grid_cols"],
            rows=entry["grid_rows"], cell_w=entry["cell_pixels"][0],
            cell_h=entry["cell_pixels"][1],
            operation="copied whole (single_card)" if entry["single_card"]
                      else "cropped",
            file=_esc(os.path.basename(entry["file"])),
            tiles=tiles or "  <p>(no card references this sheet)</p>"))

    return _CONTACT_SHEET.format(slug=_esc(cfg["slug"]), total=len(records),
                                 atlases=len(mapping),
                                 sections="\n".join(sections))


def _esc(value):
    import html
    return html.escape("" if value is None else str(value))


# ---------------------------------------------------------------------------
# 6. The write guard, re-asserted internally (§4.1, §1.1(b))
# ---------------------------------------------------------------------------

def assert_run_dir_writes(cfg, out_root, paths, workspace=None):
    """§4.1's `writes outside <run_dir>: no` column, as code.

    `kz_config.assert_write_paths` cannot be used here and the reason is worth
    stating rather than discovering: it evaluates paths against
    `guard.write_roots`, which are the SCED-downloads langpack destinations, so
    it would refuse EVERY slice -- the guard for a stage that writes only inside
    `<run_dir>` is a containment assertion, not a membership one.

    Three properties, all exit 4, all evaluated BEFORE the first byte:

      1. every planned path resolves under `out_root`
      2. `out_root` is not inside any `guard.write_roots` entry
      3. `out_root` does not match any `guard.forbidden` prefix

    2 and 3 are the ones that matter. `--run-dir` is argv (§3.1) and therefore
    unpinned by `config_sha256` -- it is the one input to this stage a typo can
    retarget -- so a `--run-dir` pointed at the langpack tree would otherwise let
    an ungated stage write PNGs into SCED-downloads.

    `guard.max_files_written` is deliberately NOT applied. That cap is a property
    of the langpack write set (§4.1, `TOLERANCES` hard_cap) and 200 is a
    plausible slice count for a single scenario -- Woods of the Black Goat is 88
    atlases -- so applying it here would refuse a legitimate run with a message
    about a rule that is not about this stage.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    root = os.path.realpath(str(out_root))
    findings = []

    for path in paths:
        # realpath on the PARENT, because the file does not exist yet and a
        # symlinked destination directory is exactly what property 1 is for.
        parent = os.path.realpath(os.path.dirname(str(path)))
        resolved = os.path.join(parent, os.path.basename(str(path)))
        if resolved != root and not resolved.startswith(root + os.sep):
            findings.append("%s is outside %s" % (resolved, root))

    rel = kz._rel_to_workspace(out_root, workspace)
    if rel is not None:
        rel_posix = rel.replace(os.sep, "/").rstrip("/") + "/"
        for write_root in cfg["guard"]["write_roots"]:
            if rel_posix.startswith(write_root) or write_root.startswith(rel_posix):
                findings.append("<run_dir> %s lands inside guard.write_roots %r; "
                                "`slice` writes no repository path"
                                % (rel_posix, write_root))
        for bad in cfg["guard"]["forbidden"]:
            if rel_posix.startswith(bad):
                findings.append("<run_dir> %s matches guard.forbidden %r"
                                % (rel_posix, bad))

    if findings:
        kc.refuse(kc.EXIT_GUARD, "planned write set violates the path guard",
                  "; ".join(findings[:kz.GUARD_FINDING_SAMPLE]))
    return root


# ---------------------------------------------------------------------------
# 7. The stage
# ---------------------------------------------------------------------------

def _load_json(path, label):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is missing" % label, path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        kc.refuse(kc.EXIT_PRECONDITION, "%s is not readable JSON" % label,
                  "%s: %s" % (path, exc))


def atlas_roots(run_dir, extra=(), workspace=None):
    """Where `resolve_atlas_file` looks, in order.

    `<run_dir>/assets` is not listed: it is reached by content address, not by
    listing. These are the URL-stem-named roots `norm()` exists for.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    roots = [os.path.join(workspace, rel) for rel in DEFAULT_ATLAS_ROOTS]
    roots += [os.path.abspath(os.path.expanduser(str(path))) for path in extra]
    return roots


def preflight(cfg, inventory, run_dir, referenced_urls, search_roots):
    """Resolve, confirm and derive -- every input assertion, before any write.

    Returns (atlas_by_url, cell_by_url, mapping, checks). Refuses at 13/14; the
    checks it returns are therefore all `pass` and exist so the report's check
    list is total over the stage's rules rather than over the half that happens
    to be evaluated after the write.
    """
    by_url = {}
    for record in inventory:
        if record.get("side") not in (None, "face"):
            continue
        by_url[record.get("english_url")] = record

    atlas_by_url, cell_by_url, mapping = {}, {}, []
    for url in sorted(referenced_urls):
        record = by_url.get(url)
        if record is None:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "a referenced atlas has no record in atlas-inventory.json",
                      "%s -- re-run `init`" % url)
        found = resolve_atlas_file(record, run_dir, search_roots)

        digest = kc.sha256_file(found["path"])
        if record.get("sha256") and digest != record["sha256"]:
            kc.refuse(kc.EXIT_DRIFT,
                      "the atlas on disk is not the one `init` measured",
                      "%s: %s has sha256 %s, atlas-inventory.json records %s"
                      % (url, found["path"], digest[:16], record["sha256"][:16]))

        measured = measure_png(found["path"])
        cell_w, cell_h = cell_pixels_for(record, measured=measured)
        grid = record["grid"]
        single = (grid["num_width"] == 1 and grid["num_height"] == 1)

        atlas_by_url[url] = {"record": record, "path": found["path"],
                             "single_card": single, "sha256": digest}
        cell_by_url[url] = (cell_w, cell_h)
        mapping.append({
            "atlas_id": record.get("atlas_id"),
            "english_url": url,
            "file": found["path"],
            "resolved_by": found["how"],
            "measured_width": measured[0], "measured_height": measured[1],
            "recorded_width": record["pixels"][0],
            "recorded_height": record["pixels"][1],
            "grid_cols": grid["num_width"], "grid_rows": grid["num_height"],
            "cell_pixels": [cell_w, cell_h],
            "single_card": single,
            "sha256": digest,
        })

    checks = [
        {"id": "SL1", "name": "every_referenced_atlas_resolved", "status": "pass",
         "exit_on_fail": kc.EXIT_PRECONDITION,
         "detail": ["%d atlas(es), %d by content address"
                    % (len(mapping),
                       sum(1 for m in mapping
                           if m["resolved_by"] == "content-address"))]},
        {"id": "SL2", "name": "measured_dims_confirm_the_mapping", "status": "pass",
         "exit_on_fail": kc.EXIT_DRIFT,
         "detail": ["%s %dx%d" % (m["atlas_id"], m["measured_width"],
                                  m["measured_height"]) for m in mapping]},
        {"id": "SL3", "name": "cell_pixels_divide_the_sheet", "status": "pass",
         "exit_on_fail": kc.EXIT_PRECONDITION,
         "detail": ["%s %dx%d over %dx%d -> %s"
                    % (m["atlas_id"], m["measured_width"], m["measured_height"],
                       m["grid_cols"], m["grid_rows"], m["cell_pixels"])
                    for m in mapping]},
        {"id": "SL4", "name": "recorded_geometry_agrees", "status": "pass",
         "exit_on_fail": kc.EXIT_DRIFT,
         "detail": ["%s single_card=%s" % (m["atlas_id"], m["single_card"])
                    for m in mapping]},
    ]
    return atlas_by_url, cell_by_url, mapping, checks


def run_slice(run_dir, mode="build", workspace=None, atlas_root=(), quiet=True):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. <run_dir>/atlas-inventory.json and card-text-en.json are readable JSON
                                                                   -> exit 13
      4. every referenced atlas resolves to a file whose bytes and dims are the
         ones `init` recorded                                      -> exit 13 / 14
      5. every atlas's pixels divide by its declared grid           -> exit 13
      6. every object's cell/row/col/card_id round-trips           -> exit 20
      7. every planned write lands inside <run_dir>                -> exit 4
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    inventory_path = os.path.join(run_dir, "atlas-inventory.json")
    en_path = os.path.join(run_dir, "card-text-en.json")
    inventory_doc = _load_json(inventory_path, "atlas-inventory.json")
    en_doc = _load_json(en_path, "card-text-en.json")

    out_root = run_dir
    if mode == "dry-run":
        out_root = kz.assert_dry_run_dest(
            cfg, STAGE, os.path.join(run_dir, "dry-run", STAGE), workspace=workspace)

    objects = en_doc.get("objects") or []
    inventory = inventory_doc.get("atlases") or []
    shared_backs = inventory_doc.get("shared_backs") or []
    referenced = set(obj.get("atlas_id") for obj in objects
                     if obj.get("cell") is not None and obj.get("atlas_id"))

    search_roots = atlas_roots(run_dir, atlas_root, workspace)
    atlas_by_url, cell_by_url, mapping, checks = preflight(
        cfg, inventory, run_dir, referenced, search_roots)

    records, without_cell = plan_slices(objects, atlas_by_url, cell_by_url)

    planned = [os.path.join(out_root, SLICES_DIR, r["filename"]) for r in records]
    planned += [os.path.join(out_root, SLICES_DIR, MANIFEST_NAME),
                os.path.join(out_root, GALLERY_DIR, CONTACT_SHEET_NAME)]
    assert_run_dir_writes(cfg, out_root, planned, workspace=workspace)

    written = 0
    if mode == "verify-only":
        # Re-assert an EXISTING output, so the records come from the manifest on
        # disk rather than from this run's plan: verifying the plan against the
        # plan would establish nothing about the files a previous run left.
        prior = _load_json(os.path.join(out_root, SLICES_DIR, MANIFEST_NAME),
                           "slices/manifest.json")
        records = prior.get("records") or []
    else:
        written = emit_slices(records, atlas_by_url, out_root)

    emitted_checks, triggered = verify_emitted(records, out_root)
    checks = checks + emitted_checks

    counts = {
        "objects": len(objects),
        "objects_without_cell": len(without_cell),
        "atlases_in_inventory": len(inventory),
        "atlases_referenced": len(mapping),
        "atlases_single_card": sum(1 for m in mapping if m["single_card"]),
        "shared_backs_not_sliced": len(shared_backs),
        "slices": len(records),
        "slices_copied": sum(1 for r in records if r["operation"] == "copy"),
        "slices_cropped": sum(1 for r in records if r["operation"] == "crop"),
        "bytes_written": written,
    }

    manifest = build_manifest(cfg, records, mapping, counts)
    if mode != "verify-only":
        kc.atomic_write_json(os.path.join(out_root, SLICES_DIR, MANIFEST_NAME),
                             manifest)
        kc.atomic_write_text(os.path.join(out_root, GALLERY_DIR, CONTACT_SHEET_NAME),
                             build_contact_sheet(cfg, records, mapping))

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path, inventory_path, en_path]),
        freshness=kc.build_freshness([inventory_path, en_path],
                                     upstream_report_path=os.path.join(run_dir,
                                                                       "init.json")),
        tool=kc.tool_block(pil=_pil_version(), numpy=_numpy_version()),
        # `slice` is NOT one of §3.2's four gated stages, so gate stays null and
        # `compute_consumable` does not block on a human who was never asked.
        gate=None,
        results={"slices_dir": _rel(os.path.join(out_root, SLICES_DIR), workspace),
                 "manifest": _rel(os.path.join(out_root, SLICES_DIR,
                                               MANIFEST_NAME), workspace),
                 "contact_sheet": _rel(os.path.join(out_root, GALLERY_DIR,
                                                    CONTACT_SHEET_NAME), workspace),
                 "cell_pixels": dict((m["atlas_id"], m["cell_pixels"])
                                     for m in mapping)})
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    return report, manifest


def _rel(path, workspace):
    """Workspace-relative when it IS inside the workspace, absolute otherwise.

    §3.2 makes every recorded path workspace-relative, but `--run-dir` is argv and
    can legitimately point outside -- `golden`'s scratch run and every test do
    exactly that. A bare `os.path.relpath` answers such a case with a ladder of
    `../..` that is neither readable nor stable, so the fallback is the absolute
    path, which is at least true.
    """
    rel = kz._rel_to_workspace(path, workspace)
    return rel if rel else os.path.abspath(str(path))


def _pil_version():
    try:
        import PIL
        return PIL.__version__
    except ImportError:                                   # pragma: no cover
        return None


def _numpy_version():
    """Reported as null when absent, never "" and never omitted (§3.6).

    `slice` does no numpy arithmetic, so this is a statement about the
    INTERPRETER the artifact was produced on, which is what `golden` compares.
    """
    try:
        import numpy
        return numpy.__version__
    except ImportError:                                   # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# 8. --selftest -- every predicate, with a GENERATED fault
# ---------------------------------------------------------------------------
#
# A check that cannot fail is a defect in this project's history, not a nicety:
# `carry_review`'s branch 2 shipped inert for months because no fault was ever
# planted against it (kz_common.py §7). Every predicate above therefore has a
# fault below that fires it, built on generated pixels -- no corpus, no network,
# no fixture that can silently vanish.

SYNTH_URL = ("https://steamusercontent-a.akamaihd.net/ugc/"
             "2407823725609449730/C570E67837928FB374DF50F4AA7FA2039DA9633B/")


def synth_atlas(path, cols, rows, cell_w, cell_h):
    """A sheet whose every cell is a DIFFERENT colour, keyed to (row, col).

    Different per cell and not merely different from the ground, because the
    defect this stage exists to prevent is a slice cut from the WRONG cell -- and
    a uniform sheet makes every crop identical, so a predicate that reads the
    pixels back cannot tell a correct slice from an off-by-one one.
    """
    from PIL import Image
    image = Image.new("RGB", (cols * cell_w, rows * cell_h), (0, 0, 0))
    for row in range(rows):
        for col in range(cols):
            index = row * cols + col
            colour = (20 + (index * 37) % 200, 20 + (index * 91) % 200,
                      20 + (index * 53) % 200)
            image.paste(Image.new("RGB", (cell_w, cell_h), colour),
                        (col * cell_w, row * cell_h))
    directory = os.path.dirname(str(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    image.save(str(path), format="PNG")
    return str(path)


def synth_record(path, cols, rows, cell_w, cell_h, url=SYNTH_URL, pixels=None):
    """An `atlas-inventory.json` record for a generated sheet."""
    digest = kc.sha256_file(path)
    return {
        "atlas_id": digest[:16], "side": "face", "english_url": url,
        "local": None,
        "grid": {"num_width": cols, "num_height": rows},
        "pixels": list(pixels) if pixels else [cols * cell_w, rows * cell_h],
        "cell_pixels": [cell_w, cell_h],
        "bytes": os.path.getsize(path), "sha256": digest,
        "cells_used": 1, "packs": [],
        "single_card": bool(cols == 1 and rows == 1),
    }


def synth_object(arkham_id, cell, cols, rows, guid=None, deck_key="100"):
    row, col = divmod(cell, cols)
    return {
        "guid": guid or ("g%s" % arkham_id), "object_id": "Root/%s" % arkham_id,
        "arkham_id": arkham_id, "kind": "Card",
        "card_id": int(deck_key) * 100 + cell, "deck_key": deck_key,
        "cell": cell, "row": row, "col": col,
        "atlas_id": SYNTH_URL, "back_atlas_id": None,
        "num_width": cols, "num_height": rows,
        "pack": "Korean - Campaigns", "nickname": "Card %s" % arkham_id,
        "description": "", "source_file": "x.json",
    }


def synth_cfg(run_dir):
    """The minimum `guard` block `assert_run_dir_writes` reads. Deliberately not
    a whole scenario.json: this fault is about the guard, and building a pinned
    config to exercise it would test `kz_config` instead."""
    return {"slug": "synth", "config_sha256": "0" * 64,
            "guard": {"write_roots": ["SCED-downloads/decomposed/language-pack/"
                                      "Korean - Campaigns/Korean-Campaigns.KoreanC/"
                                      "Synth.aaaaaa/"],
                      "forbidden": ["SCED/", ".git/", "decomposed/scenario/",
                                    "decomposed/campaign/", "library.json",
                                    "SCED-tools/"],
                      "data_root": "SCED-tools/scripts/koreanize/data/",
                      "max_files_written": 200}}


def selftest(fault=None, verbose=True):
    """Prove each refusal fires on the fault it targets."""
    import shutil

    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    def fires(label, code, call):
        try:
            call()
        except kc.KzRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d (%s)"
                                % (label, code, exc.code, exc.message))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    scratch = tempfile.mkdtemp(prefix="kz-slice-selftest.")
    try:
        if "norm" in wanted:
            # `norm()` is KEPT, so the claim is checked against the legacy form
            # rather than asserted. If these ever diverge, every file in
            # images-ko/assets/ becomes unreachable at once and silently.
            import re
            legacy = lambda s: re.sub(r"[^a-zA-Z0-9]", "", s).lower()  # noqa: E731
            for sample in (SYNTH_URL, "", "Ab_9-/.", "HTTPS://x.y/Z%20a",
                           "httpssteamusercontentaakamaihdnet.png"):
                if norm(sample) != legacy(sample):
                    findings.append("norm(%r) = %r, legacy form gives %r"
                                    % (sample, norm(sample), legacy(sample)))
            # The property the mapping actually rests on: the on-disk name is the
            # normalised URL plus an extension, so it is a PREFIX match.
            disk = norm(SYNTH_URL) + ".png"
            if not norm(disk).startswith(norm(SYNTH_URL)):
                findings.append("norm: a url-stem filename no longer prefix-matches "
                                "its URL, so images-ko/assets/ is unreachable")

            # Two files with different bytes claiming one URL is an ambiguity the
            # legacy resolved by listdir order.
            root = os.path.join(scratch, "ambiguous")
            os.makedirs(root)
            synth_atlas(os.path.join(root, norm(SYNTH_URL) + ".png"), 2, 1, 8, 8)
            synth_atlas(os.path.join(root, norm(SYNTH_URL) + "-2.png"), 3, 1, 8, 8)
            fires("norm (two files, one URL)", kc.EXIT_DRIFT,
                  lambda: resolve_atlas_file({"english_url": SYNTH_URL},
                                             scratch, [root]))
            # Same bytes twice is NOT an ambiguity: it is a cache hit.
            same = os.path.join(scratch, "duplicated")
            os.makedirs(same)
            first = synth_atlas(os.path.join(same, norm(SYNTH_URL) + ".png"),
                                2, 1, 8, 8)
            shutil.copyfile(first, os.path.join(same, norm(SYNTH_URL) + "-copy.png"))
            got = resolve_atlas_file({"english_url": SYNTH_URL}, scratch, [same])
            if got["how"] != "url-normalisation":
                findings.append("norm: two identical files were not treated as a "
                                "cache hit (%r)" % got)

        if "resolve" in wanted:
            root = os.path.join(scratch, "resolve")
            path = synth_atlas(os.path.join(root, norm(SYNTH_URL) + ".png"),
                               2, 2, 8, 8)
            record = synth_record(path, 2, 2, 8, 8)

            fires("resolve (nothing on disk)", kc.EXIT_PRECONDITION,
                  lambda: resolve_atlas_file({"english_url": SYNTH_URL},
                                             scratch, [os.path.join(scratch, "nope")]))
            # Content address wins, and it is what `init` actually writes.
            assets = os.path.join(scratch, "run", "assets")
            os.makedirs(assets)
            shutil.copyfile(path, os.path.join(assets, record["sha256"] + ".png"))
            got = resolve_atlas_file(record, os.path.join(scratch, "run"), [root])
            if got["how"] != "content-address":
                findings.append("resolve: the content-addressed path did not win "
                                "over the url-normalised one (%r)" % got)
            # A record with no URL at all cannot be resolved by either strategy.
            fires("resolve (no english_url)", kc.EXIT_PRECONDITION,
                  lambda: resolve_atlas_file({"english_url": None}, scratch, [root]))
            # The file moved under the recorded digest -> drift, not a bug.
            drifted = dict(record, pixels=[999, 999])
            fires("resolve (dims moved off the measurement)", kc.EXIT_DRIFT,
                  lambda: cell_pixels_for(drifted, measured=(16, 16)))

        if "integrality" in wanted:
            root = os.path.join(scratch, "integral")
            # (10,7) -- Labyrinths' shape, and the reference cell size.
            big = synth_atlas(os.path.join(root, "b.png"), 10, 7, 75, 105)
            got = cell_pixels_for(synth_record(big, 10, 7, 75, 105),
                                  measured=(750, 735))
            if got != (75, 105):
                findings.append("integrality: (10,7) derived %r, expected (75,105)"
                                % (got,))
            # (1,1) -- Woods of the Black Goat's shape. The whole sheet is a cell.
            one = synth_atlas(os.path.join(root, "o.png"), 1, 1, 64, 96)
            got = cell_pixels_for(synth_record(one, 1, 1, 64, 96), measured=(64, 96))
            if got != (64, 96):
                findings.append("integrality: (1,1) derived %r, expected (64,96)"
                                % (got,))
            # THE non-integral division. 100x100 over a 7x4 grid divides by
            # neither axis, so there is no cell to derive.
            odd = synth_atlas(os.path.join(root, "x.png"), 1, 1, 100, 100)
            bad = synth_record(odd, 7, 4, 0, 0, pixels=[100, 100])
            bad["cell_pixels"] = None
            fires("integrality (100x100 over 7x4)", kc.EXIT_PRECONDITION,
                  lambda: cell_pixels_for(bad, measured=(100, 100)))
            # One axis divides and the other does not -- still no cell.
            half = synth_record(odd, 2, 3, 0, 0, pixels=[100, 100])
            half["cell_pixels"] = None
            fires("integrality (one axis divides)", kc.EXIT_PRECONDITION,
                  lambda: cell_pixels_for(half, measured=(100, 100)))
            # A sheet `init` never measured has no cell either, and it is a
            # different first move: fetch it, do not re-grid it.
            never = synth_record(odd, 2, 2, 0, 0)
            never["pixels"] = None
            never["cell_pixels"] = None
            fires("integrality (never measured)", kc.EXIT_PRECONDITION,
                  lambda: cell_pixels_for(never))
            # cell_pixels that disagree with the sheet is DRIFT, not a
            # precondition: nothing is wrong with the atlas.
            lying = synth_record(big, 10, 7, 75, 105)
            lying["cell_pixels"] = [750, 1050]
            fires("integrality (cell_pixels drifted)", kc.EXIT_DRIFT,
                  lambda: cell_pixels_for(lying, measured=(750, 735)))
            # single_card that disagrees with the grid, likewise.
            claims = synth_record(big, 10, 7, 75, 105)
            claims["single_card"] = True
            fires("integrality (single_card drifted)", kc.EXIT_DRIFT,
                  lambda: cell_pixels_for(claims, measured=(750, 735)))

        if "geometry" in wanted:
            root = os.path.join(scratch, "geometry")
            path = synth_atlas(os.path.join(root, "g.png"), 4, 2, 10, 12)
            record = synth_record(path, 4, 2, 10, 12)
            atlas = {SYNTH_URL: {"record": record, "path": path,
                                 "single_card": False, "sha256": record["sha256"]}}
            cells = {SYNTH_URL: (10, 12)}

            good = [synth_object("01001", 5, 4, 2)]
            plan, _skipped = plan_slices(good, atlas, cells)
            if len(plan) != 1 or plan[0]["box"] != [10, 12, 20, 24]:
                findings.append("geometry: cell 5 of a 4x2 sheet planned %r"
                                % (plan and plan[0].get("box")))

            wrong = [dict(synth_object("01001", 5, 4, 2), row=0, col=5)]
            fires("geometry (cell != row*w+col)", kc.EXIT_RULE_A,
                  lambda: plan_slices(wrong, atlas, cells))
            outside = [dict(synth_object("01001", 5, 4, 2), cell=9, row=2, col=1)]
            fires("geometry (row outside the grid)", kc.EXIT_RULE_A,
                  lambda: plan_slices(outside, atlas, cells))
            mis_id = [dict(synth_object("01001", 5, 4, 2), card_id=99999)]
            fires("geometry (card_id != deck_key*100+cell)", kc.EXIT_RULE_A,
                  lambda: plan_slices(mis_id, atlas, cells))
            clash = [synth_object("01001", 1, 4, 2),
                     dict(synth_object("01001", 2, 4, 2), guid="g2")]
            fires("geometry (one filename, two cells)", kc.EXIT_RULE_A,
                  lambda: plan_slices(clash, atlas, cells))
            regrid = [dict(synth_object("01001", 1, 4, 2), num_width=8)]
            fires("geometry (grids disagree between artifacts)", kc.EXIT_DRIFT,
                  lambda: plan_slices(regrid, atlas, cells))
            # An object with no cell is SKIPPED AND COUNTED -- the scenario guide
            # is a Custom_PDF with no CustomDeck at all (kz_init.check_geometry),
            # so refusing on it would refuse both reference corpora.
            _plan, skipped = plan_slices(
                [dict(synth_object("01001", 1, 4, 2), cell=None)], atlas, cells)
            if len(skipped) != 1:
                findings.append("geometry: an object with no cell was not skipped "
                                "and counted")

        if "single-card" in wanted:
            root = os.path.join(scratch, "single")
            out = os.path.join(scratch, "single-out")
            one = synth_atlas(os.path.join(root, "one.png"), 1, 1, 24, 32)
            rec = synth_record(one, 1, 1, 24, 32)
            atlas = {SYNTH_URL: {"record": rec, "path": one,
                                 "single_card": True, "sha256": rec["sha256"]}}
            plan, _ = plan_slices([synth_object("01001", 0, 1, 1)], atlas,
                                  {SYNTH_URL: (24, 32)})
            if plan[0]["operation"] != "copy":
                findings.append("single-card: a (1,1) atlas was not recorded as a "
                                "copy (%r)" % plan[0]["operation"])
            emit_slices(plan, atlas, out)
            if plan[0]["sha256"] != rec["sha256"]:
                findings.append("single-card: the emitted slice is not the atlas "
                                "byte for byte")
            checks, triggered = verify_emitted(plan, out)
            if triggered:
                findings.append("single-card: a correct copy failed verification "
                                "(%r)" % [c for c in checks if c["status"] == "fail"])

            # THE FAULT: a `copy` record whose bytes are not the atlas's. Without
            # SL6 this is invisible -- the file is a valid PNG of the right size.
            faked = os.path.join(scratch, "single-fake")
            plan2, _ = plan_slices([synth_object("01001", 0, 1, 1)], atlas,
                                   {SYNTH_URL: (24, 32)})
            # Emitted through the CROP path -- a full-sheet crop of a (1,1)
            # atlas, which is the identity in pixels and a re-encode in bytes --
            # then labelled `copy`. That is precisely the shape a future
            # "simplify: crop always, it is the same thing" edit would produce.
            reencoded = slice_bytes(one, plan2[0], single_card=False)
            _atomic_write_bytes(
                os.path.join(faked, SLICES_DIR, plan2[0]["filename"]), reencoded)
            plan2[0]["sha256"] = kc.sha256_bytes(reencoded)
            if plan2[0]["sha256"] == rec["sha256"]:
                findings.append("single-card: the re-encoded fault produced the "
                                "source bytes, so SL6 cannot be exercised by it")
            checks, triggered = verify_emitted(plan2, faked)
            if kc.EXIT_RULE_B not in triggered:
                findings.append("single-card: a re-encoded `copy` was not caught "
                                "by SL6")

            # And the mirror: a `crop` that is the whole sheet is a copy wearing
            # the wrong label, which is what a (1,1) misclassification looks like.
            multi = synth_atlas(os.path.join(root, "two.png"), 2, 1, 24, 32)
            mrec = synth_record(multi, 2, 1, 24, 32)
            mismatch = [dict(plan[0], operation="crop",
                             source_sha256=plan[0]["sha256"])]
            checks, triggered = verify_emitted(mismatch, out)
            if kc.EXIT_RULE_B not in triggered:
                findings.append("single-card: a `crop` identical to its whole "
                                "atlas was not caught by SL6")
            if mrec["single_card"]:
                findings.append("single-card: a 2x1 sheet was recorded as "
                                "single_card")

        if "reread" in wanted:
            root = os.path.join(scratch, "reread")
            out = os.path.join(scratch, "reread-out")
            path = synth_atlas(os.path.join(root, "r.png"), 2, 2, 16, 20)
            rec = synth_record(path, 2, 2, 16, 20)
            atlas = {SYNTH_URL: {"record": rec, "path": path,
                                 "single_card": False, "sha256": rec["sha256"]}}
            plan, _ = plan_slices([synth_object("01001", 0, 2, 2),
                                   synth_object("01002", 3, 2, 2)],
                                  atlas, {SYNTH_URL: (16, 20)})
            emit_slices(plan, atlas, out)
            checks, triggered = verify_emitted(plan, out)
            if triggered:
                findings.append("reread: a clean emission failed verification (%r)"
                                % [c for c in checks if c["status"] == "fail"])
            # A wrong-cell slice is caught only because the synthetic sheet
            # colours every cell differently -- assert that the two slices differ,
            # or the fault below proves nothing about cell selection.
            first = kc.sha256_file(os.path.join(out, SLICES_DIR, plan[0]["filename"]))
            second = kc.sha256_file(os.path.join(out, SLICES_DIR, plan[1]["filename"]))
            if first == second:
                findings.append("reread: two different cells produced identical "
                                "bytes, so no predicate here can see a wrong cell")

            # THE FAULT: the file on disk is replaced after the write.
            victim = os.path.join(out, SLICES_DIR, plan[0]["filename"])
            _atomic_write_bytes(victim, b"\x89PNG not really")
            _checks, triggered = verify_emitted(plan, out)
            if kc.EXIT_RULE_B not in triggered:
                findings.append("reread: a file replaced after the write was not "
                                "caught by SL7")
            # THE SECOND FAULT: a file in slices/ the manifest does not declare.
            emit_slices(plan, atlas, out)
            _atomic_write_bytes(os.path.join(out, SLICES_DIR, "undeclared.png"),
                                b"stray")
            checks, triggered = verify_emitted(plan, out)
            sl8 = [c for c in checks if c["id"] == "SL8"][0]
            if sl8["status"] != "fail" or kc.EXIT_RULE_B not in triggered:
                findings.append("reread: an undeclared file in slices/ was not "
                                "caught by SL8")
            # And a declared file that is not there.
            os.unlink(os.path.join(out, SLICES_DIR, "undeclared.png"))
            os.unlink(victim)
            checks, triggered = verify_emitted(plan, out)
            if kc.EXIT_RULE_B not in triggered:
                findings.append("reread: a declared file missing from slices/ was "
                                "not caught")

        if "guard" in wanted:
            run_dir = os.path.join(scratch, "guarded-run")
            cfg = synth_cfg(run_dir)
            inside = [os.path.join(run_dir, SLICES_DIR, "01001.png")]
            assert_run_dir_writes(cfg, run_dir, inside, workspace=scratch)
            fires("guard (a write outside <run_dir>)", kc.EXIT_GUARD,
                  lambda: assert_run_dir_writes(
                      cfg, run_dir,
                      inside + [os.path.join(scratch, "elsewhere", "x.png")],
                      workspace=scratch))
            fires("guard (traversal out of <run_dir>)", kc.EXIT_GUARD,
                  lambda: assert_run_dir_writes(
                      cfg, run_dir,
                      [os.path.join(run_dir, "..", "escaped.png")],
                      workspace=scratch))
            # THE ONE THAT MATTERS: --run-dir is argv, so it is unpinned by
            # config_sha256 and a typo can retarget the whole stage.
            langpack = os.path.join(
                scratch, "SCED-downloads", "decomposed", "language-pack",
                "Korean - Campaigns", "Korean-Campaigns.KoreanC", "Synth.aaaaaa")
            fires("guard (<run_dir> inside guard.write_roots)", kc.EXIT_GUARD,
                  lambda: assert_run_dir_writes(
                      cfg, langpack,
                      [os.path.join(langpack, SLICES_DIR, "01001.png")],
                      workspace=scratch))
            forbidden = os.path.join(scratch, "SCED-tools", "scratch")
            fires("guard (<run_dir> matches guard.forbidden)", kc.EXIT_GUARD,
                  lambda: assert_run_dir_writes(
                      cfg, forbidden,
                      [os.path.join(forbidden, SLICES_DIR, "01001.png")],
                      workspace=scratch))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 9. CLI
# ---------------------------------------------------------------------------

_SUMMARY = {
    "norm": "norm() still agrees with slice-atlases.py:56-57 character for "
            "character, a url-stem filename prefix-matches its URL, and two "
            "files with different bytes claiming one URL refuse at 14 instead "
            "of resolving by listdir order",
    "resolve": "the content-addressed <run_dir>/assets path wins over the "
               "url-normalised one, nothing on disk refuses at 13, and a file "
               "whose dims moved off init's measurement refuses at 14",
    "integrality": "(10,7) and (1,1) both derive from the same code path with "
                   "no table, and 100x100 over a 7x4 grid refuses at 13 -- on "
                   "either axis, and separately from a sheet init never measured",
    "geometry": "cell != row*w+col, a row outside the grid, a broken card_id "
                "round-trip and two objects claiming one filename all refuse at "
                "20; an object with no cell is skipped and counted",
    "single-card": "a (1,1) atlas emits its source bytes verbatim and is "
                   "recorded as `copy`, a re-encoded copy is caught by SL6, and "
                   "so is a `crop` identical to its whole atlas",
    "reread": "a file replaced after the write, an undeclared file in slices/ "
              "and a declared file that is absent are each caught at 21 -- on a "
              "sheet whose cells differ, so the check can see a wrong cell",
    "guard": "a write outside <run_dir>, a traversal out of it, and a --run-dir "
             "landing in guard.write_roots or matching guard.forbidden all "
             "refuse at 4 before the first byte",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_slice.py",
        description="koreanize stage `slice` -- cut each English atlas into "
                    "per-card slices (design §6 step 11, §5.4).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--atlas-root", action="append", default=[],
                        metavar="DIR",
                        help="an extra directory of url-stem-named atlas PNGs to "
                             "search; repeatable. <run_dir>/assets is always "
                             "reached by content address and needs no flag")
    parser.add_argument("--dry-run", action="store_true",
                        help="full build into <run_dir>/dry-run/slice/")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-assert the existing slices/ against their "
                             "manifest; writes slice.verify.json, never the marker")
    parser.add_argument("--json-only", action="store_true",
                        help="emit the report JSON on stdout and nothing else")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_slice --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-12s %s" % (name, _SUMMARY[name]))
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

    report, _manifest = run_slice(run_dir, mode=mode, atlas_root=args.atlas_root,
                                  quiet=args.quiet)

    dest = run_dir
    if args.dry_run:
        dest = os.path.join(run_dir, "dry-run", STAGE)
    path = kc.write_report(report, dest)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize slice -- %s" % report["slug"])
        print("  atlases         : %d referenced of %d (%d single_card)"
              % (counts["atlases_referenced"], counts["atlases_in_inventory"],
                 counts["atlases_single_card"]))
        print("  cell_pixels     : %s" % (report["results"] or {}).get("cell_pixels"))
        print("  slices          : %d (%d cropped, %d copied), %d bytes"
              % (counts["slices"], counts["slices_cropped"],
                 counts["slices_copied"], counts["bytes_written"]))
        print("  objects skipped : %d with no cell; %d shared back(s) not sliced"
              % (counts["objects_without_cell"], counts["shared_backs_not_sliced"]))
        for check in report["checks"]:
            print("  %-34s: %s  %s"
                  % (check["name"], check["status"], "; ".join(check["detail"][:2])))
        print("  verdict         : %s (exit %d)"
              % (report["verdict"], report["exit_code"]))
        print("  consumable      : %s%s"
              % (report["consumable"],
                 "" if report["consumable"]
                 else " (%s)" % report["consumable_blocked_by"]))
        print("  contact sheet   : %s"
              % (report["results"] or {}).get("contact_sheet"))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
