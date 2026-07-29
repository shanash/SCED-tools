#!/usr/bin/env python3
"""
Extract per-card PNGs from a Korean print-and-play PDF (the new atlas source).

Part of the ``korean-pdf-card-atlases`` slug. Two source PDFs feed two tracks
(see .am/korean-pdf-card-atlases/design.md §5.1/§5.2):

  - Track A (Taboo) — ``--mode grid``: the A4 sheets impose a 2x2 card grid per
    page with bleed + crop marks. Each page is rendered at ``--dpi`` and the card
    cells are sliced out using a *measured* per-cell crop box (the page grid line
    is NOT the card boundary — bleed must be removed). The partial final page
    yields a per-page-overridden cell count. Written as ``crops/p<page>_r<row>_c<col>.png``.
  - Track B (Parallel) — ``--mode page``: each page already carries one card. The
    single dominant (largest) embedded image is pulled via PyMuPDF image-xref
    extraction (best quality), falling back to a ``--dpi`` page render if a page
    has no dominant image. ``--trim-overlays`` drops the tiny icon-overlay xrefs.
    Written as ``crops/p<NNN>.png``.

Both modes emit ``extract_manifest.json`` recording, per crop: sha256, page,
cell, a perceptual hash (a pure-PIL average-hash, no extra deps), the aspect
ratio (w/h), and — under ``--dedupe`` — the playset-duplicate groupings (so the
crop gallery can surface dropped copies for human confirmation).

Dedupe threshold (grid mode, ``--dedupe``): two crops are treated as the same
print copy when their 64-bit average-hash Hamming distance is <= ``DEDUPE_HAMMING_MAX``
(default 4). This is deliberately CONSERVATIVE so that distinct multi-XP
same-frame variants (e.g. All In (5), Eon Chart (1)/(4), Ace in the Hole (3))
— which differ only in a small XP pip / a few glyphs — are NOT merged; only true
print duplicates of the identical card collapse. The dropped copies are recorded
as ``playset_dupe_of`` referencing their representative crop.

The AHLCG card aspect ratio is ~0.715 (w/h); in grid mode every crop must fall
within ``ASPECT_TOLERANCE`` of it or extraction fails (a wrong/clipped crop box
shows up as an out-of-tolerance ratio). This replaces an eyeball check with a
measurable gate.

Everything is confined to ``SCED-tools/scripts/``; no ``SCED/`` base code and no
``SCED-downloads/`` langpack data is read or written.

Runtime dependencies (declared in ``requirements.txt`` alongside this script):
  - PyMuPDF (imported as ``fitz``; page render + embedded-image extraction)
  - Pillow  (``from PIL import Image``; slice + perceptual hash + PNG save)
Both are imported lazily inside ``main()``; if either is missing the tool prints
an actionable ``pip install -r requirements.txt`` message and exits non-zero
rather than raising an ImportError traceback.

Usage:
  # Track A — grid de-imposition (2x2 A4 sheets), with measured cut box
  extract-pdf-cards.py --pdf Ahc_taboo_kr_2402_updated250317.pdf --mode grid \
      --out-dir output/korean-pdf-atlases/track-a-taboo \
      --grid-rows 2 --grid-cols 2 --crop-box 120,150,1160,1604 --dpi 300 \
      --dedupe --page-cell-override "19:1" --expect-page19-faces 1 --expect-sideways 4

  # Track B — per-page embedded JPEG, trim icon overlays
  extract-pdf-cards.py --pdf AHC_Parallel_Cards_Kor_251110.pdf --mode page \
      --out-dir output/korean-pdf-atlases/track-b-parallel --trim-overlays

Exit codes:
  0  OK: every crop produced and (grid mode) every crop within aspect tolerance.
  1  warnings: dedupe dropped playset copies and/or overlay xrefs were skipped.
  2  --pdf not found / not a readable PDF.
  3  assertion failure: page-19 face-crop count != --expect-page19-faces, OR
     sideways count != --expect-sideways, OR (grid mode) a crop's aspect ratio
     outside the AHLCG tolerance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

from sced_io import atomic_write_json

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent

SCHEMA_VERSION = "1.0.0"
GENERATOR_SCRIPT = "extract-pdf-cards.py"

# The partial final Taboo page (page 19) carries a single face crop (plus a back
# and an FFG copyright region, both excluded). Default named literal per §5.1.
PAGE19_FACE_COUNT = 1

# AHLCG card aspect ratio (width / height) and the grid-mode tolerance band.
AHLCG_ASPECT_RATIO = 0.715
ASPECT_TOLERANCE = 0.03  # +/-3%

# Average-hash side length: an 8x8 reduce -> a 64-bit perceptual hash.
AHASH_SIDE = 8

# Conservative playset-dedupe threshold: max Hamming distance (of the 64-bit
# average hash) for two crops to be treated as the same print copy. Small enough
# that distinct multi-XP same-frame variants are NOT collapsed (see module doc).
DEDUPE_HAMMING_MAX = 4

# A "dominant" embedded image (page mode) must cover at least this fraction of
# the largest image's pixel area to be accepted; anything smaller is an overlay.
OVERLAY_AREA_FRACTION = 0.5


# ---------------------------------------------------------------------------
# CLI (design §4)
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract per-card PNGs from a Korean print-and-play PDF "
                    "(grid de-imposition or per-page extraction)."
    )
    p.add_argument("--pdf", type=Path, required=True,
                   help="Source PDF (the Korean print-and-play file).")
    p.add_argument("--mode", choices=["grid", "page"], required=True,
                   help="grid = de-impose an N x M card grid per page (Track A); "
                        "page = pull the dominant embedded image per page (Track B).")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="Output root for crops/ and extract_manifest.json.")
    p.add_argument("--grid-rows", type=int, default=2,
                   help="Rows in the per-page card grid (mode=grid; default 2).")
    p.add_argument("--grid-cols", type=int, default=2,
                   help="Columns in the per-page card grid (mode=grid; default 2).")
    p.add_argument("--crop-box", type=str, default=None,
                   help="Measured per-cell cut box L,T,R,B in rendered pixels "
                        "(mode=grid). Removes bleed/crop-marks: the page grid "
                        "line is not the card boundary. Required for mode=grid. "
                        "With --pitch this is the ABSOLUTE box of the first "
                        "cell (r1c1); without it, it is relative to each "
                        "equal-region's top-left.")
    p.add_argument("--pitch", type=str, default=None,
                   help="Card-to-card pitch H,V in rendered pixels (mode=grid). "
                        "When given, --crop-box is the absolute box of the first "
                        "cell and each further cell steps by this pitch, instead "
                        "of dividing the page into equal regions. Use when the "
                        "cards are a centered block with margins+gaps rather than "
                        "an edge-to-edge grid (H = card_width + gap).")
    p.add_argument("--dpi", type=int, default=300,
                   help="Page render DPI (mode=grid always; mode=page fallback).")
    p.add_argument("--dedupe", action="store_true",
                   help="Collapse near-identical print copies to one "
                        "representative (mode=grid); record playset_dupe_of "
                        "groupings in the manifest.")
    p.add_argument("--trim-overlays", action="store_true",
                   help="Drop the tiny icon-overlay xrefs, keeping only the "
                        "dominant per-page image (mode=page).")
    p.add_argument("--render", action="store_true",
                   help="mode=page: RENDER the full page at --dpi instead of "
                        "pulling the embedded image. Required when the PDF lays "
                        "card text as a vector overlay on top of a background "
                        "image (pulling the image alone drops all text).")
    p.add_argument("--page-cell-override", action="append", default=[],
                   metavar="PAGE:COUNT",
                   help="Override the cell count for a page (mode=grid), e.g. "
                        "the partial page 19 -> '19:1'. Repeatable.")
    p.add_argument("--expect-page19-faces", type=int, default=None,
                   help="Assert page 19 yields exactly this many face crops "
                        "(mode=grid). Mismatch -> exit 3.")
    p.add_argument("--expect-sideways", type=int, default=None,
                   help="Assert exactly this many crops are flagged sideways "
                        "(landscape orientation). Mismatch -> exit 3.")
    p.add_argument("--dry-run", action="store_true",
                   help="Open the PDF and print the extraction plan; write no "
                        "files. Returns the would-be exit code.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def parse_crop_box(spec: str) -> tuple[int, int, int, int]:
    """Parse a 'L,T,R,B' crop box into an int 4-tuple. Raises ValueError."""
    parts = [s.strip() for s in spec.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop-box must be 'L,T,R,B' (four comma-separated ints)")
    left, top, right, bottom = (int(v) for v in parts)
    if right <= left or bottom <= top:
        raise ValueError("--crop-box requires R>L and B>T")
    return left, top, right, bottom


def parse_pitch(spec: str) -> tuple[int, int]:
    """Parse a 'H,V' card pitch into an int 2-tuple. Raises ValueError."""
    parts = [s.strip() for s in spec.split(",")]
    if len(parts) != 2:
        raise ValueError("--pitch must be 'H,V' (two comma-separated ints)")
    pitch_h, pitch_v = (int(v) for v in parts)
    if pitch_h <= 0 or pitch_v <= 0:
        raise ValueError("--pitch requires H>0 and V>0")
    return pitch_h, pitch_v


def parse_cell_overrides(specs: list) -> dict:
    """Parse repeated 'PAGE:COUNT' overrides into a {page:int -> count:int} map."""
    out: dict[int, int] = {}
    for spec in specs:
        if ":" not in spec:
            raise ValueError(f"--page-cell-override must be 'PAGE:COUNT', got {spec!r}")
        page_s, count_s = spec.split(":", 1)
        out[int(page_s.strip())] = int(count_s.strip())
    return out


def average_hash(img, side: int = AHASH_SIDE) -> str:
    """Compute a pure-PIL average-hash (perceptual hash) as a hex string.

    The image is reduced to a ``side`` x ``side`` greyscale, each pixel compared
    against the mean; the resulting bit string is packed big-endian into hex.
    ``side=8`` yields a 64-bit (16 hex char) hash. No extra dependencies.
    """
    small = img.convert("L").resize((side, side))
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for px in pixels:
        bits = (bits << 1) | (1 if px >= mean else 0)
    hexlen = (side * side + 3) // 4
    return f"{bits:0{hexlen}x}"


def hamming_distance(a_hex: str, b_hex: str) -> int:
    """Hamming distance between two equal-length hex perceptual hashes."""
    return bin(int(a_hex, 16) ^ int(b_hex, 16)).count("1")


def aspect_ratio(width: int, height: int) -> float:
    """Width / height ratio; 0.0 for a degenerate (zero-height) crop."""
    return (width / height) if height else 0.0


def aspect_in_tolerance(ratio: float) -> bool:
    """True iff ``ratio`` is within ASPECT_TOLERANCE of the AHLCG card ratio."""
    return abs(ratio - AHLCG_ASPECT_RATIO) <= AHLCG_ASPECT_RATIO * ASPECT_TOLERANCE


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents (chunked)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Atomic PNG save (same-dir temp + os.replace) — mirrors build-korean-card-crops
# ---------------------------------------------------------------------------


def _atomic_save_png(img, dest: Path) -> None:
    """Save a PIL image as PNG to dest atomically (same-dir temp + os.replace)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp",
                               dir=str(dest.parent))
    os.close(fd)
    try:
        img.save(tmp, format="PNG")
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Rendering helpers (PyMuPDF) — image_mod / fitz_mod are passed in (lazy import)
# ---------------------------------------------------------------------------


def render_page_image(page, dpi: int, *, image_mod, fitz_mod):
    """Render a fitz page to a PIL RGB Image at ``dpi``."""
    pix = page.get_pixmap(dpi=dpi)
    img = image_mod.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img


def extract_dominant_image(doc, page, dpi: int, *, image_mod, fitz_mod,
                           trim_overlays: bool):
    """Return (PIL RGB Image, used_overlay_trim, fell_back) for a page (mode=page).

    Pull every embedded image's bytes via ``doc.extract_image(xref)``, pick the
    one with the largest pixel area as the dominant card image. ``trim_overlays``
    only changes whether the smaller xrefs are *counted as skipped* (they are
    never chosen as the dominant image regardless). If the page has no embedded
    image at all, fall back to a full-page render at ``dpi``.
    """
    infos = page.get_images(full=True)
    best = None  # (area, PIL image)
    overlay_skipped = 0
    candidates = []
    for info in infos:
        xref = info[0]
        try:
            extracted = doc.extract_image(xref)
        except Exception:  # noqa: BLE001 — a bad xref must not crash the run
            continue
        raw = extracted.get("image")
        if not raw:
            continue
        try:
            with image_mod.open(_BytesIO(raw)) as probe:
                probe.load()
                rgb = probe.convert("RGB")
        except Exception:  # noqa: BLE001 — undecodable xref is skipped
            continue
        candidates.append(rgb)

    if not candidates:
        return render_page_image(page, dpi, image_mod=image_mod, fitz_mod=fitz_mod), False, True

    candidates.sort(key=lambda im: im.width * im.height, reverse=True)
    best = candidates[0]
    best_area = best.width * best.height
    for other in candidates[1:]:
        if other.width * other.height < best_area * OVERLAY_AREA_FRACTION:
            overlay_skipped += 1
    used_trim = trim_overlays and overlay_skipped > 0
    return best, used_trim, False


class _BytesIO:
    """Tiny io.BytesIO shim kept local so the heavy import stays inside main()."""

    def __new__(cls, data):
        import io
        return io.BytesIO(data)


# ---------------------------------------------------------------------------
# Grid de-imposition (mode=grid, design §5.1)
# ---------------------------------------------------------------------------


def slice_grid_cells(page_img, rows: int, cols: int, crop_box, cell_count: int,
                     pitch=None):
    """Yield (row, col, PIL crop) for up to ``cell_count`` cells of a page.

    Cell ``(r, c)`` is cut at ``(c*step_x + left, r*step_y + top, ...)`` where
    ``(left, top, right, bottom) = crop_box`` and the per-cell step is:

      - ``pitch`` (``H, V`` in pixels) when given — ``crop_box`` is the ABSOLUTE
        box of the first cell (r0,c0) and each further cell steps by the measured
        card pitch. Use when the cards are a centered block with margins+gaps
        (so the card pitch is NOT ``page // cols``).
      - otherwise the equal-region size ``page_width // cols`` x
        ``page_height // rows`` — ``crop_box`` is relative to each region's top
        left (the legacy edge-to-edge-grid model).

    Cells are emitted in reading order (row-major) and stop after ``cell_count``
    cells, so a partial page (e.g. page 19 -> 1) yields only its real cards.
    """
    if pitch is not None:
        step_x, step_y = pitch
    else:
        step_x = page_img.width // cols
        step_y = page_img.height // rows
    left, top, right, bottom = crop_box
    emitted = 0
    for r in range(rows):
        for c in range(cols):
            if emitted >= cell_count:
                return
            ox = c * step_x
            oy = r * step_y
            box = (ox + left, oy + top, ox + right, oy + bottom)
            yield r, c, page_img.crop(box)
            emitted += 1


def run_grid(doc, args, crop_box, overrides, pitch, *, image_mod, fitz_mod) -> dict:
    """Execute grid de-imposition over all pages. Returns the run result dict.

    Result keys: crops (list of crop records), page19_face_count, sideways_count,
    aspect_violations (list), dropped (playset_dupe groupings).
    """
    rows, cols = args.grid_rows, args.grid_cols
    default_cells = rows * cols
    crops: list[dict] = []
    aspect_violations: list[dict] = []
    page19_face_count = 0
    sideways_count = 0

    crops_dir = args.out_dir / "crops"

    for page_index in range(doc.page_count):
        page_no = page_index + 1  # 1-based for filenames + overrides
        cell_count = overrides.get(page_no, default_cells)
        if page_no == 19 and 19 not in overrides:
            cell_count = PAGE19_FACE_COUNT
        page_img = render_page_image(doc[page_index], args.dpi,
                                     image_mod=image_mod, fitz_mod=fitz_mod)
        for r, c, crop in slice_grid_cells(page_img, rows, cols, crop_box,
                                           cell_count, pitch):
            w, h = crop.width, crop.height
            sideways = w > h  # landscape crop = a sideways card
            ratio = aspect_ratio(w, h)
            # For a sideways crop the long edge is width; the card ratio compares
            # the SHORT/LONG edges, so normalize before the tolerance check.
            check_ratio = ratio if ratio <= 1.0 else (1.0 / ratio)
            name = f"p{page_no:02d}_r{r + 1}_c{c + 1}.png"
            rec = {
                "crop": f"crops/{name}",
                "page": page_no,
                "row": r + 1,
                "col": c + 1,
                "width": w,
                "height": h,
                "aspect_ratio": round(ratio, 4),
                "sideways": sideways,
                "ahash": average_hash(crop),
                "sha256": None,  # filled after write
                "playset_dupe_of": None,
            }
            if not aspect_in_tolerance(check_ratio):
                aspect_violations.append({
                    "crop": rec["crop"], "aspect_ratio": rec["aspect_ratio"]})
            if page_no == 19:
                page19_face_count += 1
            if sideways:
                sideways_count += 1
            if not args.dry_run:
                dest = crops_dir / name
                _atomic_save_png(crop, dest)
                rec["sha256"] = compute_sha256(dest)
            crops.append(rec)

    dropped = _dedupe_groups(crops) if args.dedupe else []

    return {
        "crops": crops,
        "page19_face_count": page19_face_count,
        "sideways_count": sideways_count,
        "aspect_violations": aspect_violations,
        "dropped": dropped,
    }


def _dedupe_groups(crops: list) -> list:
    """Mark near-identical print copies (conservative ahash Hamming threshold).

    The first crop of a group is the representative; later crops within
    ``DEDUPE_HAMMING_MAX`` get ``playset_dupe_of`` set to the representative's
    crop path. Returns the list of dropped (duplicate) crop records for the
    manifest's dedupe-groupings section.
    """
    reps: list[dict] = []
    dropped: list[dict] = []
    for rec in crops:
        matched = None
        matched_dist = None
        for rep in reps:
            dist = hamming_distance(rec["ahash"], rep["ahash"])
            if dist <= DEDUPE_HAMMING_MAX:
                matched = rep
                matched_dist = dist
                break
        if matched is None:
            reps.append(rec)
        else:
            rec["playset_dupe_of"] = matched["crop"]
            dropped.append({"crop": rec["crop"], "dupe_of": matched["crop"],
                            "hamming": matched_dist})
    return dropped


# ---------------------------------------------------------------------------
# Per-page extraction (mode=page, design §5.2)
# ---------------------------------------------------------------------------


def run_page(doc, args, *, image_mod, fitz_mod) -> dict:
    """Execute per-page dominant-image extraction over all pages."""
    crops: list[dict] = []
    overlay_skips = 0
    fallbacks = 0
    sideways_count = 0
    crops_dir = args.out_dir / "crops"

    for page_index in range(doc.page_count):
        page_no = page_index + 1
        if args.render:
            # Full-page render: rasterises the vector text overlay together with
            # the background image (mode=page extraction drops the text layer).
            img, used_trim, fell_back = (
                render_page_image(doc[page_index], args.dpi,
                                  image_mod=image_mod, fitz_mod=fitz_mod),
                False, False)
        else:
            img, used_trim, fell_back = extract_dominant_image(
                doc, doc[page_index], args.dpi, image_mod=image_mod,
                fitz_mod=fitz_mod, trim_overlays=args.trim_overlays)
        if fell_back:
            fallbacks += 1
        if used_trim:
            overlay_skips += 1
        w, h = img.width, img.height
        sideways = w > h
        if sideways:
            sideways_count += 1
        name = f"p{page_no:03d}.png"
        rec = {
            "crop": f"crops/{name}",
            "page": page_no,
            "cell": 0,
            "width": w,
            "height": h,
            "aspect_ratio": round(aspect_ratio(w, h), 4),
            "sideways": sideways,
            "ahash": average_hash(img),
            "sha256": None,
            "fell_back": fell_back,
        }
        if not args.dry_run:
            dest = crops_dir / name
            _atomic_save_png(img.convert("RGB"), dest)
            rec["sha256"] = compute_sha256(dest)
        crops.append(rec)

    return {
        "crops": crops,
        "overlay_skips": overlay_skips,
        "fallbacks": fallbacks,
        "sideways_count": sideways_count,
    }


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(args, pdf_sha: str, result: dict) -> dict:
    """Assemble the extract_manifest.json document for either mode."""
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"script": GENERATOR_SCRIPT, "version": SCHEMA_VERSION},
        "mode": args.mode,
        "source_pdf": args.pdf.name,
        "source_pdf_sha256": pdf_sha,
        "dpi": args.dpi,
        "crops": result["crops"],
    }
    if args.mode == "grid":
        manifest["grid"] = {"rows": args.grid_rows, "cols": args.grid_cols}
        manifest["crop_box"] = args.crop_box
        manifest["pitch"] = args.pitch
        manifest["page19_face_count"] = result["page19_face_count"]
        manifest["sideways_count"] = result["sideways_count"]
        manifest["aspect_violations"] = result["aspect_violations"]
        manifest["dedupe"] = {
            "enabled": bool(args.dedupe),
            "hamming_max": DEDUPE_HAMMING_MAX,
            "dropped": result["dropped"],
        }
    else:
        manifest["overlay_skips"] = result["overlay_skips"]
        manifest["fallbacks"] = result["fallbacks"]
        manifest["sideways_count"] = result["sideways_count"]
    return manifest


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    args = parse_args(argv)

    # Late-import-to-degrade: PyMuPDF + PIL imported inside main().
    try:
        import fitz as fitz_mod  # PyMuPDF
        from PIL import Image as image_mod
    except ImportError:
        print(
            "ERROR: required dependencies missing (PyMuPDF and/or Pillow). "
            "Install with: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    if not args.pdf.exists():
        print(f"--pdf not found: {args.pdf}", file=sys.stderr)
        return 2

    crop_box = None
    pitch = None
    overrides = {}
    if args.mode == "grid":
        if not args.crop_box:
            print("--crop-box is required for --mode grid", file=sys.stderr)
            return 2
        try:
            crop_box = parse_crop_box(args.crop_box)
            pitch = parse_pitch(args.pitch) if args.pitch else None
            overrides = parse_cell_overrides(args.page_cell_override)
        except ValueError as exc:
            print(f"argument error: {exc}", file=sys.stderr)
            return 2

    try:
        doc = fitz_mod.open(args.pdf)
    except Exception as exc:  # noqa: BLE001 — any open failure is "unreadable PDF"
        print(f"--pdf not a readable PDF: {exc}", file=sys.stderr)
        return 2

    try:
        page_count = doc.page_count
        if page_count == 0:
            print("--pdf has no pages", file=sys.stderr)
            return 2

        pdf_sha = compute_sha256(args.pdf)

        if args.mode == "grid":
            result = run_grid(doc, args, crop_box, overrides, pitch,
                              image_mod=image_mod, fitz_mod=fitz_mod)
        else:
            result = run_page(doc, args, image_mod=image_mod, fitz_mod=fitz_mod)
    finally:
        doc.close()

    # --- summary -----------------------------------------------------------
    print(f"Mode {args.mode}: extracted {len(result['crops'])} crop(s) "
          f"from {args.pdf.name} ({page_count} pages).")
    if args.mode == "grid":
        print(f"  page-19 face crops: {result['page19_face_count']}; "
              f"sideways: {result['sideways_count']}; "
              f"aspect violations: {len(result['aspect_violations'])}; "
              f"dedupe-dropped: {len(result['dropped'])}")
    else:
        print(f"  overlay-skips: {result['overlay_skips']}; "
              f"page-render fallbacks: {result['fallbacks']}; "
              f"sideways: {result['sideways_count']}")

    # --- manifest ----------------------------------------------------------
    manifest = build_manifest(args, pdf_sha, result)
    if not args.dry_run:
        atomic_write_json(args.out_dir / "extract_manifest.json", manifest)
        print(f"Wrote {args.out_dir / 'extract_manifest.json'}")
    else:
        print("(dry-run mode — no files written)")

    # --- assertion gates (exit 3) ------------------------------------------
    if args.mode == "grid" and result["aspect_violations"]:
        print(f"ASSERTION: {len(result['aspect_violations'])} crop(s) outside "
              f"the AHLCG aspect tolerance (~{AHLCG_ASPECT_RATIO} +/-"
              f"{int(ASPECT_TOLERANCE * 100)}%).", file=sys.stderr)
        return 3
    if args.expect_page19_faces is not None and args.mode == "grid":
        if result["page19_face_count"] != args.expect_page19_faces:
            print(f"ASSERTION: page-19 face count "
                  f"{result['page19_face_count']} != expected "
                  f"{args.expect_page19_faces}.", file=sys.stderr)
            return 3
    if args.expect_sideways is not None:
        if result["sideways_count"] != args.expect_sideways:
            print(f"ASSERTION: sideways count {result['sideways_count']} != "
                  f"expected {args.expect_sideways}.", file=sys.stderr)
            return 3

    # --- warnings (exit 1) -------------------------------------------------
    if args.mode == "grid" and result["dropped"]:
        print(f"{len(result['dropped'])} playset copy(ies) deduped (exit 1).",
              file=sys.stderr)
        return 1
    if args.mode == "page" and result["overlay_skips"]:
        print(f"{result['overlay_skips']} page(s) had overlay xrefs skipped "
              f"(exit 1).", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
