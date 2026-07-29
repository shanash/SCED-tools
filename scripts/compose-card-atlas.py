#!/usr/bin/env python3
"""
Compose a packed face atlas PNG from a track mapping + extracted crops.

Part of the ``korean-pdf-card-atlases`` slug; generalizes the trivial
``sheet-maker.py`` into a parameterized, manifest-emitting compositor (see
.am/korean-pdf-card-atlases/design.md §3.1/§3.6/§4). Given a ``mapping.json``
(the human-confirmed crop -> arkham_id contract, schema §3.3), this tool:

  - filters the mapping ``entries`` to those whose ``atlas`` == ``--atlas-id``
    and drops any ``playset_dupe_of != null`` print copy;
  - pastes each crop at its ``(x, y)`` cell into a packed RGB grid PNG of
    ``--num-width`` x ``--num-height`` cells (uniform cell size from
    ``--cell-size`` or the max crop dimensions);
  - computes the composed PNG's sha256 and the deterministic R2 FaceURL
    (content-hash scheme, identical to what the deferred upload will serve);
  - allocates deck_keys (§3.6): with ``--deck-key-base`` it is a *packed* sheet
    (one shared deck_key, ``target_card_id = base*100 + cell``) and the base is
    VERIFIED FREE by scanning every existing Korean CustomDeck key; without it,
    this is the native-reuse A-main sheet and each cell's target_* fields are
    copied verbatim from the mapping (a FaceURL-only downstream swap);
  - validates cell uniqueness / bounds, crop presence, and the CardID<->cell
    invariant before writing;
  - upserts its atlas entry (keyed by ``atlas_id``) into the per-track
    ``atlas_manifest.json`` that lives beside the atlas PNG directory.

``compose-card-atlas.py`` is invoked once per atlas (one ``--out-atlas``); each
run replaces or inserts only its own atlas in the shared per-track manifest, so a
track spanning multiple sheets is built by repeated invocations.

Everything is confined to ``SCED-tools/scripts/``: the only ``SCED-downloads/``
access is a READ-ONLY scan of the decomposed Korean Player Cards CustomDeck keys
to verify a packed deck_key is free; no langpack file is modified.

Runtime dependency (declared in ``requirements.txt``):
  - Pillow (``from PIL import Image``; paste + PNG save + optional overlay draw)
Imported lazily inside ``main()``; if missing the tool prints an actionable
``pip install -r requirements.txt`` message and exits non-zero.

Usage:
  # native-reuse A-main (56 cards at their existing 10x6 cells, FaceURL-only)
  compose-card-atlas.py --mapping output/korean-pdf-atlases/track-a-taboo/mapping.json \
      --crops-dir output/korean-pdf-atlases/track-a-taboo \
      --atlas-id atlas-A-main --num-width 10 --num-height 6 \
      --out-atlas output/korean-pdf-atlases/track-a-taboo/atlas-A-main.png

  # packed A-extra (17 outliers onto a fresh 5x4 sheet, base deck_key 8001)
  compose-card-atlas.py --mapping output/korean-pdf-atlases/track-a-taboo/mapping.json \
      --crops-dir output/korean-pdf-atlases/track-a-taboo \
      --atlas-id atlas-A-extra --num-width 5 --num-height 4 --deck-key-base 8001 \
      --out-atlas output/korean-pdf-atlases/track-a-taboo/atlas-A-extra.png \
      --dedupe --expect-distinct 71

Exit codes:
  0  OK: atlas composed, validated, and manifest upserted.
  1  warning: composed atlas exceeds 64 MiB.
  2  missing crop referenced by the mapping, OR a cell out of bounds.
  3  dedupe-validation failure: distinct-face count != --expect-distinct.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sced_io import atomic_write_json

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent

SCHEMA_VERSION = "1.0.0"
MANIFEST_SCHEMA_VERSION = "1.0.0"
GENERATOR_SCRIPT = "compose-card-atlas.py"

# Atlas size cap (matches build-korean-card-crops.py / the prior tooling).
MAX_ATLAS_BYTES = 64 * 1024 * 1024  # 64 MiB

# R2 public base (mirrors upload-korean-images-to-r2.py DEFAULT_PUBLIC_BASE).
R2_PUBLIC_BASE = (
    "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/images/"
)

# Default READ-ONLY decomposed dir scanned to verify a packed deck_key is free.
DEFAULT_DECOMPOSED_ROOT = (
    REPO_ROOT / "SCED-downloads" / "decomposed" / "language-pack"
    / "Korean - Player Cards" / "Korean-PlayerCards.KoreanI"
)


# ---------------------------------------------------------------------------
# Content-hash R2 URL scheme — mirrors upload-korean-images-to-r2.py
# r2_key_for_sha / public_url_for_key.
# ---------------------------------------------------------------------------


def r2_key_for_sha(digest: str) -> str:
    return f"images/sha256/{digest[:2]}/{digest.lower()}.png"


def public_url_for_key(base: str, key: str) -> str:
    # Join base + key, collapsing the shared "images/" prefix (so it is not
    # doubled) — identical to the upload tool's public_url_for_key.
    if not base.endswith("/"):
        base = base + "/"
    key_stripped = key
    if base.endswith("/images/") and key.startswith("images/"):
        key_stripped = key[len("images/"):]
    return base + key_stripped


def face_url_for_sha(digest: str) -> str:
    """Deterministic FaceURL for a composed atlas, from its PNG bytes' sha256."""
    return public_url_for_key(R2_PUBLIC_BASE, r2_key_for_sha(digest))


# ---------------------------------------------------------------------------
# CLI (design §4)
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compose a packed face atlas PNG from a track mapping + "
                    "extracted crops, emitting an atlas manifest."
    )
    p.add_argument("--mapping", type=Path, required=True,
                   help="The human-confirmed mapping.json for the track.")
    p.add_argument("--crops-dir", type=Path, required=True,
                   help="Directory the mapping 'crop' paths are relative to.")
    p.add_argument("--atlas-id", type=str, required=True,
                   help="Atlas id; only mapping entries with this 'atlas' are "
                        "composed. Also the manifest upsert key.")
    p.add_argument("--num-width", type=int, required=True,
                   help="Atlas grid columns (cells per row).")
    p.add_argument("--num-height", type=int, required=True,
                   help="Atlas grid rows.")
    p.add_argument("--out-atlas", type=Path, required=True,
                   help="Output atlas PNG path. Its stem is the atlas_id; the "
                        "per-track atlas_manifest.json is written beside it.")
    p.add_argument("--cell-size", type=str, default=None, metavar="WxH",
                   help="Uniform cell size in pixels (default: max crop dims).")
    p.add_argument("--deck-key-base", type=str, default=None,
                   help="Packed-sheet base deck_key (§3.6). When given, all "
                        "cells share this deck_key; verified free by a full "
                        "scan of existing Korean CustomDeck keys. Omit for the "
                        "native-reuse sheet (target_* copied from mapping).")
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT,
                   help="READ-ONLY decomposed dir scanned for existing "
                        "CustomDeck keys when verifying --deck-key-base.")
    p.add_argument("--dedupe", action="store_true",
                   help="Enable the dedupe-validation gate (with --expect-distinct).")
    p.add_argument("--expect-distinct", type=int, default=None,
                   help="Assert the distinct-face count equals this (e.g. 71 "
                        "for Track A's 73 objects). Mismatch -> exit 3.")
    p.add_argument("--calibration-overlay", type=Path, default=None,
                   help="Write a sign-off PNG of the crop box drawn on the "
                        "source page (requires --source-page).")
    p.add_argument("--source-page", type=Path, default=None,
                   help="Source page PNG for the calibration overlay.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate + compute placement and print a summary; "
                        "write no files. Returns the would-be exit code.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def parse_cell_size(spec: str) -> tuple[int, int]:
    """Parse 'WxH' into an (int, int) cell size. Raises ValueError."""
    lowered = spec.lower().replace(" ", "")
    if "x" not in lowered:
        raise ValueError("--cell-size must be 'WxH'")
    w_s, h_s = lowered.split("x", 1)
    w, h = int(w_s), int(h_s)
    if w <= 0 or h <= 0:
        raise ValueError("--cell-size dimensions must be positive")
    return w, h


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents (chunked)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def select_entries(mapping: dict, atlas_id: str) -> list:
    """Filter mapping entries to this atlas, dropping playset duplicates."""
    out = []
    for e in mapping.get("entries", []):
        if e.get("atlas") != atlas_id:
            continue
        if e.get("playset_dupe_of") is not None:
            continue
        out.append(e)
    return out


def collect_existing_deck_keys(decomposed_root: Path) -> set:
    """Scan every *.json under the decomposed dir, collecting CustomDeck keys.

    READ-ONLY. A card JSON carries ``CustomDeck`` as an object whose keys are the
    deck_keys; missing/malformed files are skipped silently (the scan is a guard,
    not a validator). Returns the set of all keys seen (as strings).
    """
    keys: set[str] = set()
    if not decomposed_root.exists():
        return keys
    for path in sorted(decomposed_root.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        custom = doc.get("CustomDeck")
        if isinstance(custom, dict):
            keys.update(str(k) for k in custom.keys())
    return keys


def allocate_deck_key_base(requested: str, existing_keys: set) -> str:
    """Return a verified-free deck_key base near ``requested`` (§3.6).

    If ``requested`` is free, use it. Otherwise pick the next free integer above
    it (the rule is "verified-free key by full key scan", never derived from a
    maximum). Returns the chosen base as a string.
    """
    base = int(requested)
    while str(base) in existing_keys:
        base += 1
    return str(base)


def target_for_entry(entry: dict, deck_key_base, num_width: int,
                     num_height: int) -> dict:
    """Compute the target placement fields for one entry.

    Packed sheet (deck_key_base given): cell from the entry, deck_key = base,
    card_id = base*100 + cell, grid = the atlas grid. Native-reuse (base None):
    target_* are copied verbatim from the mapping entry (target == current).
    """
    if deck_key_base is None:
        return {
            "target_deck_key": str(entry["target_deck_key"]),
            "target_cell_index": int(entry["target_cell_index"]),
            "x": int(entry["x"]),
            "y": int(entry["y"]),
            "target_card_id": int(entry["target_card_id"]),
            "target_num_width": int(entry["target_num_width"]),
            "target_num_height": int(entry["target_num_height"]),
        }
    cell = int(entry["target_cell_index"])
    base_i = int(deck_key_base)
    return {
        "target_deck_key": str(deck_key_base),
        "target_cell_index": cell,
        "x": cell % num_width,
        "y": cell // num_width,
        "target_card_id": base_i * 100 + cell,
        "target_num_width": num_width,
        "target_num_height": num_height,
    }


def validate_placement(entries: list, targets: list, crops_dir: Path,
                       num_width: int, num_height: int) -> tuple[int, str]:
    """Validate cells/crops/CardID before any write. Returns (exit_code, msg).

    Exit 2: a referenced crop is missing, OR a cell is out of bounds, OR a cell
    collides. Exit 0 with "" when all checks pass. The CardID<->cell invariant
    (target_card_id == deck_key*100 + cell) is asserted here too (exit 2).
    """
    capacity = num_width * num_height
    seen_cells: dict[int, str] = {}
    for entry, tgt in zip(entries, targets):
        crop_path = crops_dir / entry["crop"]
        if not crop_path.exists():
            return 2, f"missing crop: {crop_path}"
        cell = tgt["target_cell_index"]
        if not (0 <= cell < capacity):
            return 2, (f"cell_out_of_bounds: cell {cell} >= capacity {capacity} "
                       f"({num_width}x{num_height}) for {entry['crop']}")
        if cell in seen_cells:
            return 2, (f"duplicate target cell {cell}: {entry['crop']} and "
                       f"{seen_cells[cell]}")
        seen_cells[cell] = entry["crop"]
        expected = int(tgt["target_deck_key"]) * 100 + cell
        if tgt["target_card_id"] != expected:
            return 2, (f"CardID<->cell mismatch for {entry['crop']}: "
                       f"target_card_id {tgt['target_card_id']} != "
                       f"{tgt['target_deck_key']}*100+{cell}={expected}")
    return 0, ""


# ---------------------------------------------------------------------------
# Composition (PIL) — image_mod passed in (lazy import)
# ---------------------------------------------------------------------------


def load_tiles(entries: list, crops_dir: Path, *, image_mod) -> list:
    """Open each entry's crop ONCE as an RGB image (aligned to ``entries``).

    Loading here (instead of separately in cell-size detection and compose) means
    every crop PNG is decoded a single time. The caller owns the returned images
    and must release them (see ``close_tiles``).
    """
    tiles = []
    for entry in entries:
        with image_mod.open(crops_dir / entry["crop"]) as img:
            tiles.append(img.convert("RGB"))
    return tiles


def close_tiles(tiles: list) -> None:
    """Release the PIL images opened by ``load_tiles``."""
    for tile in tiles:
        try:
            tile.close()
        except Exception:  # noqa: BLE001 — a close error must not mask a real one
            pass


def determine_cell_size(tiles: list, explicit):
    """Resolve the uniform cell size: explicit --cell-size or the max tile dims."""
    if explicit is not None:
        return explicit
    max_w, max_h = 0, 0
    for tile in tiles:
        max_w = max(max_w, tile.width)
        max_h = max(max_h, tile.height)
    return max_w, max_h


def compose_atlas(tiles: list, targets: list, num_width: int, num_height: int,
                  cell_size, *, image_mod):
    """Paste every preloaded tile at its (x, y) cell into a packed RGB atlas.

    Reuses the sheet-maker.py paste idiom, parameterized: a tile larger than the
    cell is resized down to the cell; the canvas is sized num_width*cell_w by
    num_height*cell_h. ``tiles`` are the RGB images from ``load_tiles`` (decoded
    once, upstream). Returns the composed PIL Image.
    """
    cell_w, cell_h = cell_size
    canvas = image_mod.new("RGB", (num_width * cell_w, num_height * cell_h),
                           (255, 255, 255))
    for tile, tgt in zip(tiles, targets):
        placed = tile if tile.size == (cell_w, cell_h) else tile.resize((cell_w, cell_h))
        canvas.paste(placed, (tgt["x"] * cell_w, tgt["y"] * cell_h))
    return canvas


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


def render_calibration_overlay(source_page: Path, crop_box_spec, dest: Path,
                               *, image_mod):
    """Draw the per-cell crop box on the source page for human sign-off.

    ``crop_box_spec`` is the mapping's recorded crop box string 'L,T,R,B' (the
    measured cut box); this renders it as a rectangle outline on the page render
    so a human can confirm the calibration. A best-effort aid: if the spec is
    absent/malformed the page is copied through unmodified.
    """
    from PIL import ImageDraw
    with image_mod.open(source_page) as page:
        canvas = page.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    if crop_box_spec:
        try:
            left, top, right, bottom = (int(v.strip())
                                        for v in str(crop_box_spec).split(","))
            draw.rectangle((left, top, right, bottom), outline=(255, 0, 0), width=4)
        except (ValueError, AttributeError):
            pass
    _atomic_save_png(canvas, dest)


# ---------------------------------------------------------------------------
# Manifest upsert
# ---------------------------------------------------------------------------


def manifest_path_for(out_atlas: Path) -> Path:
    """The per-track atlas_manifest.json lives beside the atlas PNG."""
    return out_atlas.parent / "atlas_manifest.json"


def recorded_deck_key_base(manifest_file: Path, atlas_id: str):
    """Return the deck_key_base previously recorded for ``atlas_id``, or None.

    On a RE-compose the packed base must be reused from the prior
    atlas_manifest.json (design §3.6: "reuse the recorded key — reproducible")
    rather than re-derived from a key scan: the earlier apply has since placed
    the in-scope cards ON that base, so a fresh scan would see it as taken and
    bump to a different key, producing a manifest that disagrees with disk.
    Returns the recorded base as a string, or None when there is no prior
    manifest / no entry for this atlas / no base recorded (the first compose).
    """
    if not manifest_file.exists():
        return None
    try:
        with manifest_file.open(encoding="utf-8") as fh:
            doc = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(doc, dict):
        return None
    for atlas in doc.get("atlases", []):
        if isinstance(atlas, dict) and atlas.get("atlas_id") == atlas_id:
            base = atlas.get("deck_key_base")
            return str(base) if base is not None else None
    return None


def build_atlas_entry(atlas_id: str, atlas_png: Path, atlas_sha: str,
                      face_url: str, num_width: int, num_height: int,
                      deck_key_base, entries: list, targets: list) -> dict:
    """Build the per-atlas manifest entry (shared atlas_manifest.json contract)."""
    cells = []
    for entry, tgt in zip(entries, targets):
        cells.append({
            "arkham_id": entry["arkham_id"],
            "source_file": entry["source_file"],
            "current_deck_key": str(entry.get("current_deck_key", "")),
            "current_card_id": int(entry.get("current_card_id", 0) or 0),
            "target_deck_key": tgt["target_deck_key"],
            "target_cell_index": tgt["target_cell_index"],
            "x": tgt["x"],
            "y": tgt["y"],
            "target_card_id": tgt["target_card_id"],
            "target_num_width": tgt["target_num_width"],
            "target_num_height": tgt["target_num_height"],
            "sideways": bool(entry.get("sideways", False)),
        })
    return {
        "atlas_id": atlas_id,
        "atlas_png": atlas_png,
        "atlas_sha256": atlas_sha,
        "face_url": face_url,
        "num_width": num_width,
        "num_height": num_height,
        "deck_key_base": (str(deck_key_base) if deck_key_base is not None else None),
        "cells": cells,
    }


def upsert_manifest(manifest_file: Path, track: str, atlas_entry: dict) -> dict:
    """Read the per-track manifest (if present), replace/insert this atlas by id.

    The atlas is keyed by ``atlas_id``: an existing entry with the same id is
    replaced in place; otherwise the new entry is appended. Returns the full
    manifest doc (the caller writes it atomically).
    """
    doc = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "track": track,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "atlases": [],
    }
    if manifest_file.exists():
        try:
            with manifest_file.open(encoding="utf-8") as fh:
                existing = json.load(fh)
            if isinstance(existing, dict) and isinstance(existing.get("atlases"), list):
                doc = existing
        except (json.JSONDecodeError, OSError):
            pass
    doc["schema_version"] = MANIFEST_SCHEMA_VERSION
    doc["track"] = track
    doc["generated_at"] = datetime.now(timezone.utc).isoformat()
    atlases = doc["atlases"]
    for i, existing_atlas in enumerate(atlases):
        if existing_atlas.get("atlas_id") == atlas_entry["atlas_id"]:
            atlases[i] = atlas_entry
            break
    else:
        atlases.append(atlas_entry)
    return doc


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def load_mapping(path: Path) -> dict:
    """Load + minimally validate a mapping.json. Caller maps errors to exit 2."""
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise ValueError("mapping.json missing 'entries' list")
    return doc


def main(argv=None) -> int:
    args = parse_args(argv)

    # Late-import-to-degrade: PIL imported inside main().
    try:
        from PIL import Image as image_mod
    except ImportError:
        print(
            "ERROR: required dependency missing (Pillow). "
            "Install with: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2

    if not args.mapping.exists():
        print(f"--mapping not found: {args.mapping}", file=sys.stderr)
        return 2
    try:
        mapping = load_mapping(args.mapping)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"--mapping not readable / malformed: {exc}", file=sys.stderr)
        return 2

    cell_size_explicit = None
    if args.cell_size:
        try:
            cell_size_explicit = parse_cell_size(args.cell_size)
        except ValueError as exc:
            print(f"argument error: {exc}", file=sys.stderr)
            return 2

    track = mapping.get("track", "")
    entries = select_entries(mapping, args.atlas_id)
    if not entries:
        print(f"no mapping entries for atlas {args.atlas_id!r}", file=sys.stderr)
        return 2

    # --- deck_key allocation (§3.6) ----------------------------------------
    # Reproducibility: if this atlas was composed before, its packed base is
    # recorded in atlas_manifest.json — reuse it verbatim rather than re-deriving
    # (a re-run after apply would otherwise see the base as "taken" by our own
    # repointed cards and bump to a different key). Only derive a fresh base on
    # the first compose of this atlas_id.
    deck_key_base = None
    if args.deck_key_base is not None:
        recorded = recorded_deck_key_base(manifest_path_for(args.out_atlas),
                                          args.atlas_id)
        if recorded is not None:
            deck_key_base = recorded
            if recorded != str(args.deck_key_base):
                print(f"reusing recorded deck_key base {recorded} for atlas "
                      f"{args.atlas_id} (requested {args.deck_key_base})",
                      file=sys.stderr)
        else:
            existing_keys = collect_existing_deck_keys(args.decomposed_root)
            deck_key_base = allocate_deck_key_base(args.deck_key_base, existing_keys)
            if deck_key_base != str(args.deck_key_base):
                print(f"requested deck_key base {args.deck_key_base} is taken; "
                      f"using next free {deck_key_base}", file=sys.stderr)

    targets = [target_for_entry(e, deck_key_base, args.num_width, args.num_height)
               for e in entries]

    # --- validate cells / crops / CardID before any write (exit 2) ---------
    code, msg = validate_placement(entries, targets, args.crops_dir,
                                   args.num_width, args.num_height)
    if code != 0:
        print(f"VALIDATION: {msg}", file=sys.stderr)
        return code

    # --- dedupe-validation gate (exit 3) -----------------------------------
    distinct = len(entries)
    if args.dedupe and args.expect_distinct is not None:
        if distinct != args.expect_distinct:
            print(f"ASSERTION: distinct-face count {distinct} != expected "
                  f"{args.expect_distinct}.", file=sys.stderr)
            return 3

    tiles = load_tiles(entries, args.crops_dir, image_mod=image_mod)
    try:
        cell_size = determine_cell_size(tiles, cell_size_explicit)

        print(f"Atlas {args.atlas_id}: {len(entries)} cell(s) on a "
              f"{args.num_width}x{args.num_height} grid; cell size "
              f"{cell_size[0]}x{cell_size[1]}; deck_key_base="
              f"{deck_key_base if deck_key_base is not None else '(native-reuse)'}.")

        if args.dry_run:
            print("(dry-run mode — no files written)")
            # Composition would still need to size the PNG to check the 64 MiB
            # cap; we cannot know the exact byte size without writing, report 0.
            return 0

        # --- compose + save ------------------------------------------------
        canvas = compose_atlas(tiles, targets, args.num_width, args.num_height,
                               cell_size, image_mod=image_mod)
    finally:
        close_tiles(tiles)
    _atomic_save_png(canvas, args.out_atlas)
    atlas_sha = compute_sha256(args.out_atlas)
    face_url = face_url_for_sha(atlas_sha)
    atlas_bytes = args.out_atlas.stat().st_size

    # --- optional calibration overlay --------------------------------------
    if args.calibration_overlay is not None and args.source_page is not None:
        render_calibration_overlay(args.source_page, mapping.get("crop_box"),
                                   args.calibration_overlay, image_mod=image_mod)
        print(f"Wrote calibration overlay {args.calibration_overlay}")

    # --- upsert the per-track manifest -------------------------------------
    manifest_file = manifest_path_for(args.out_atlas)
    try:
        atlas_png_rel = str(args.out_atlas.relative_to(manifest_file.parent.parent))
    except ValueError:
        atlas_png_rel = str(args.out_atlas)
    atlas_entry = build_atlas_entry(
        args.atlas_id, atlas_png_rel, atlas_sha, face_url,
        args.num_width, args.num_height, deck_key_base, entries, targets)
    doc = upsert_manifest(manifest_file, track, atlas_entry)
    atomic_write_json(manifest_file, doc)

    print(f"Wrote {args.out_atlas} ({atlas_bytes} bytes), sha256="
          f"{atlas_sha[:16]}...")
    print(f"FaceURL: {face_url}")
    print(f"Upserted atlas {args.atlas_id} into {manifest_file}")

    # --- size cap warning (exit 1) -----------------------------------------
    if atlas_bytes > MAX_ATLAS_BYTES:
        print(f"WARNING: composed atlas {atlas_bytes} bytes exceeds "
              f"{MAX_ATLAS_BYTES} (exit 1).", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
