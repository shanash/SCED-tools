#!/usr/bin/env python3
"""
Build a self-contained HTML gallery of extracted crops for the assisted-manual
PDF -> card mapping flow (the only non-automated step of korean-pdf-card-atlases).

Part of the ``korean-pdf-card-atlases`` slug. Two modes (see
.am/korean-pdf-card-atlases/design.md §5.3/§5.5):

  - ``--mode pre`` (default) — render each extracted crop thumbnail beside the
    card index's Korean ``name`` -> ``arkham_id`` candidate list, filtered to the
    track (Taboo = arkham_ids ending ``-t`` plus the two ``-t-c`` upgrade sheets;
    Parallel = arkham_ids starting ``90``). Each crop carries a dropdown of
    candidate ids so a human can confirm the binding by reading the Korean title.
    Also emits ``mapping_seed.json`` next to the gallery, pre-filled best-effort
    by page order in the mapping.json schema (§3.3) for the human to edit into
    the authoritative ``mapping.json``.
  - ``--mode recrop`` — given ``--atlas-manifest`` + ``--mapping``, re-slice each
    FINAL atlas cell back out (the verified index cell-slice geometry) and render
    it beside the ``arkham_id`` / Korean name it is now bound to, for a second
    human confirmation that catches a transposed mapping (otherwise anomaly-free).

The gallery reproduces the ``build-korean-card-crops.py`` structure: a
self-contained paginated single-file HTML with an embedded ``const ROWS`` JSON
island (via ``js_safe``), IntersectionObserver lazy-loading ``<img data-src>``,
and prev/next pagination. Written via ``sced_io.atomic_write_text``.

Everything is confined to ``SCED-tools/scripts/``; no ``SCED/`` base code and no
``SCED-downloads/`` langpack data is read or written.

Runtime dependency:
  - Pillow (``from PIL import Image``) — ONLY in ``--mode recrop`` (to re-slice
    the final atlas cells). ``--mode pre`` needs no image library. Imported
    lazily inside ``main()`` for recrop; if missing the tool prints an actionable
    ``pip install -r requirements.txt`` message and exits non-zero.

Usage:
  # pre mode — confirm the mapping (emits mapping_seed.json beside the gallery)
  build-pdf-crop-gallery.py --crops-dir output/korean-pdf-atlases/track-a-taboo \
      --index output/korean-player-card-index/korean_card_index.json \
      --track taboo --output output/korean-pdf-atlases/track-a-taboo/gallery-pre.html

  # recrop mode — second confirmation against the FINAL atlas + mapping
  build-pdf-crop-gallery.py --crops-dir output/korean-pdf-atlases/track-a-taboo \
      --index output/korean-player-card-index/korean_card_index.json \
      --track taboo --mode recrop \
      --atlas-manifest output/korean-pdf-atlases/track-a-taboo/atlas_manifest.json \
      --mapping output/korean-pdf-atlases/track-a-taboo/mapping.json \
      --output output/korean-pdf-atlases/track-a-taboo/gallery-recrop.html

Exit codes:
  0  OK: gallery (and, in pre mode, mapping_seed.json) written.
  2  a required input is missing/unreadable (--index; --crops-dir; in recrop
     mode --atlas-manifest / --mapping; or Pillow missing for recrop).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sced_io import atomic_write_json, atomic_write_text, js_safe

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
DEFAULT_INDEX = SCRIPTS_DIR / "output" / "korean-player-card-index" / "korean_card_index.json"

SCHEMA_VERSION = "1.0.0"
MAPPING_SCHEMA_VERSION = "1.1.0"
GENERATOR_SCRIPT = "build-pdf-crop-gallery.py"


# ---------------------------------------------------------------------------
# CLI (design §4)
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build a self-contained crop gallery for the assisted-manual "
                    "PDF->card mapping (pre) or post-compose re-crop confirm."
    )
    p.add_argument("--crops-dir", type=Path, required=True,
                   help="Directory holding the extractor crops/ (pre) or beside "
                        "the final atlas PNG referenced by the manifest (recrop).")
    p.add_argument("--index", type=Path, default=DEFAULT_INDEX,
                   help="The korean_card_index.json (name->arkham_id candidates).")
    p.add_argument("--track", choices=["taboo", "parallel"], required=True,
                   help="Which track to filter the index candidate list to.")
    p.add_argument("--output", type=Path, required=True,
                   help="Output gallery HTML path.")
    p.add_argument("--page-size", type=int, default=50,
                   help="Crops per gallery page (default: 50).")
    p.add_argument("--mode", choices=["pre", "recrop"], default="pre",
                   help="pre = crop + candidate dropdown + mapping_seed.json; "
                        "recrop = re-slice final atlas cells for a 2nd confirm.")
    p.add_argument("--atlas-manifest", type=Path, default=None,
                   help="Final atlas_manifest.json (required for --mode recrop).")
    p.add_argument("--mapping", type=Path, default=None,
                   help="Final mapping.json (required for --mode recrop).")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute rows and print a summary; write no files.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Index load + track filtering (design §5.3)
# ---------------------------------------------------------------------------


def load_index(index_path: Path) -> dict:
    """Load + minimally validate the card index. Caller maps errors to exit 2."""
    with index_path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get("cards"), list):
        raise ValueError("index missing 'cards' list")
    return doc


def in_track(arkham_id: str, track: str) -> bool:
    """Track membership filter.

    Taboo = arkham_id ends '-t' (and the two '-t-c' customizable upgrade sheets).
    Parallel = arkham_id starts '90'.
    """
    if track == "taboo":
        return arkham_id.endswith("-t") or arkham_id.endswith("-t-c")
    return arkham_id.startswith("90")


def track_candidates(index_doc: dict, track: str) -> list:
    """Build the sorted candidate {arkham_id, name, source_file} list for a track."""
    out = []
    for rec in index_doc["cards"]:
        aid = rec.get("arkham_id", "")
        if in_track(aid, track):
            out.append({
                "arkham_id": aid,
                "name": rec.get("name", ""),
                "source_file": rec.get("source_file", ""),
            })
    out.sort(key=lambda c: (c["name"], c["arkham_id"]))
    return out


# ---------------------------------------------------------------------------
# Crop discovery (pre mode)
# ---------------------------------------------------------------------------


def discover_crops(crops_dir: Path) -> list:
    """Return the sorted list of crop PNG paths relative to crops_dir.

    Prefers a ``crops/`` subdir (the extractor layout) and falls back to PNGs in
    crops_dir itself. Names sort naturally (zero-padded page numbers).
    """
    sub = crops_dir / "crops"
    base = sub if sub.is_dir() else crops_dir
    rels = []
    for path in sorted(base.glob("*.png")):
        rel = path.relative_to(crops_dir) if base is sub else Path(path.name)
        rels.append(str(rel))
    return rels


# ---------------------------------------------------------------------------
# Re-crop slicing (recrop mode) — image_mod passed in (lazy import)
# ---------------------------------------------------------------------------


def slice_cell(img, num_width: int, num_height: int, x: int, y: int):
    """Crop one grid cell out of an atlas (the verified index cell contract).

    Integer floor-division pixel boxes; PIL crop box (left, top, right, bottom)
    with right/bottom exclusive. Identical to build-korean-card-crops.slice_cell.
    """
    cell_w = img.width // num_width
    cell_h = img.height // num_height
    left = x * cell_w
    top = y * cell_h
    box = (left, top, left + cell_w, top + cell_h)
    return img.crop(box)


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
# Row builders
# ---------------------------------------------------------------------------


def build_pre_rows(crops: list, candidates: list) -> list:
    """One gallery row per crop: thumbnail + a best-effort default candidate id.

    The full candidate list is emitted ONCE (as a shared ``CANDIDATES`` island in
    the gallery JS) instead of duplicated onto every row; each row carries only
    its default (the candidate at the crop's page-order position).
    """
    rows = []
    for i, crop in enumerate(crops):
        default = candidates[i]["arkham_id"] if i < len(candidates) else ""
        rows.append({
            "crop": crop,
            "default_arkham_id": default,
        })
    return rows


def build_seed(crops: list, candidates: list, track: str, source_pdf: str) -> dict:
    """Pre-fill a mapping_seed.json (schema §3.3) best-effort by page order.

    Each crop is paired with the candidate at the same ordinal; target_* fields
    are left as the matched card's CURRENT values so a native-reuse swap is the
    default, and the human edits placement for packed sheets. ``atlas`` is left
    blank for the human to assign.
    """
    entries = []
    for i, crop in enumerate(crops):
        cand = candidates[i] if i < len(candidates) else {"arkham_id": "", "source_file": ""}
        entries.append({
            "crop": crop,
            "arkham_id": cand["arkham_id"],
            "source_file": cand["source_file"],
            "atlas": "",
            "current_deck_key": "",
            "current_card_id": 0,
            "target_deck_key": "",
            "target_cell_index": i,
            "x": 0,
            "y": 0,
            "target_card_id": 0,
            "target_num_width": 0,
            "target_num_height": 0,
            "sideways": False,
            "playset_dupe_of": None,
        })
    return {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "track": track,
        "source_pdf": source_pdf,
        "entries": entries,
    }


def build_recrop_rows(manifest: dict, mapping: dict, crops_dir: Path, out_dir: Path,
                      dry_run: bool, *, image_mod) -> list:
    """Re-slice each FINAL atlas cell back out and pair it with its binding.

    For every atlas in the manifest, open its PNG (path is relative to the
    manifest's parent's parent, i.e. the output root) and slice each cell using
    the verified geometry; write the slice to ``recrop/`` and emit a row pairing
    the slice with the cell's arkham_id + the Korean name from the mapping.
    """
    # arkham_id -> mapping entry (for the Korean name / cross-check).
    name_by_id = {}
    for e in mapping.get("entries", []):
        name_by_id.setdefault(e.get("arkham_id", ""), e)

    output_root = crops_dir
    rows = []
    recrop_dir = out_dir / "recrop"
    for atlas in manifest.get("atlases", []):
        atlas_png_rel = atlas.get("atlas_png", "")
        atlas_path = (output_root / Path(atlas_png_rel).name)
        if not atlas_path.exists():
            # Try the recorded relative path against the output root one level up.
            alt = output_root / atlas_png_rel
            atlas_path = alt if alt.exists() else atlas_path
        nw = int(atlas.get("num_width", 1))
        nh = int(atlas.get("num_height", 1))
        if not atlas_path.exists():
            for cell in atlas.get("cells", []):
                rows.append({
                    "crop": None,
                    "arkham_id": cell.get("arkham_id", ""),
                    "name": name_by_id.get(cell.get("arkham_id", ""), {}).get("name", ""),
                    "status": "missing_atlas",
                })
            continue
        with image_mod.open(atlas_path) as img:
            rgb = img.convert("RGB")
            for cell in atlas.get("cells", []):
                x = int(cell.get("x", 0))
                y = int(cell.get("y", 0))
                aid = cell.get("arkham_id", "")
                slice_name = f"{atlas.get('atlas_id', 'atlas')}_c{cell.get('target_cell_index', 0)}.png"
                rel = f"recrop/{slice_name}"
                if not dry_run:
                    crop = slice_cell(rgb, nw, nh, x, y)
                    _atomic_save_png(crop, recrop_dir / slice_name)
                rows.append({
                    "crop": rel,
                    "arkham_id": aid,
                    "name": name_by_id.get(aid, {}).get("name", ""),
                    "status": "ok",
                })
    return rows


# ---------------------------------------------------------------------------
# Gallery HTML (reproduces build-korean-card-crops.py structure)
# ---------------------------------------------------------------------------


def build_gallery_html(rows: list, candidates: list, page_size: int, mode: str,
                       track: str) -> str:
    """Render the self-contained paginated gallery HTML.

    Embeds the row JSON and the shared candidate list via json.dumps(...,
    ensure_ascii=False) through js_safe; thumbnails point at relative crop paths;
    pre-mode tiles render a candidate-id <select> (options come from the shared
    CANDIDATES island); recrop-mode tiles render the re-sliced cell beside its
    bound arkham_id/name; IntersectionObserver lazy-loads the <img data-src>.
    """
    now = datetime.now(timezone.utc)
    generated = now.strftime("%Y-%m-%d %H:%M UTC")
    rows_json = js_safe(json.dumps(rows, ensure_ascii=False))
    candidates_json = js_safe(json.dumps(candidates, ensure_ascii=False))
    title = f"PDF Crop Gallery — {track} — {mode} — {generated}"

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, sans-serif; background: #1a1a2e; color: #eee; }}
  #toolbar {{
    position: sticky; top: 0; z-index: 100;
    background: #16213e; border-bottom: 2px solid #0f3460;
    padding: 10px 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
  }}
  #toolbar h1 {{ font-size: 15px; color: #e94560; white-space: nowrap; }}
  #stats {{ font-size: 13px; color: #aaa; white-space: nowrap; }}
  #gallery {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 16px; padding: 16px;
  }}
  .tile {{
    border: 1px solid #0f3460; border-radius: 8px; overflow: hidden;
    background: #0a0a1a; display: flex; flex-direction: column;
  }}
  .tile-imgs {{
    display: flex; gap: 4px; padding: 6px; align-items: center;
    justify-content: center; min-height: 160px;
  }}
  .crop-wrap {{
    flex: 1; display: flex; align-items: center; justify-content: center;
    overflow: hidden;
  }}
  .crop-wrap img {{ max-width: 100%; height: auto; display: block; }}
  .placeholder {{
    flex: 1; min-height: 160px; display: flex; align-items: center;
    justify-content: center; text-align: center; font-size: 11px;
    padding: 8px; border-radius: 4px; background: #2a0a0a; color: #ffaaaa;
  }}
  .tile-meta {{ padding: 6px 8px; border-top: 1px solid #0f3460; }}
  .tile-meta .name {{ font-size: 12px; font-weight: bold; color: #eee; }}
  .tile-meta .ids {{ font-size: 10px; color: #88ccff; }}
  .tile-meta select {{
    width: 100%; margin-top: 4px; font-size: 11px; background: #0a0a1a;
    color: #eee; border: 1px solid #0f3460; border-radius: 4px; padding: 3px;
  }}
  .crop-name {{ font-size: 9px; color: #777; word-break: break-all; }}
  #pagination {{
    display: flex; align-items: center; justify-content: center; gap: 12px;
    padding: 16px; font-size: 14px; border-top: 1px solid #0f3460;
  }}
  #pagination button {{ background: #0f3460; color: #eee; padding: 6px 16px; border: none; border-radius: 6px; cursor: pointer; }}
  #pagination button:disabled {{ opacity: 0.4; cursor: default; }}
  #page-info {{ color: #aaa; min-width: 130px; text-align: center; }}
</style>
</head>
<body>
<div id="toolbar">
  <h1>PDF Crop Gallery — {track} — {mode}</h1>
  <span id="stats"></span>
</div>
<div id="gallery"></div>
<div id="pagination">
  <button id="btn-prev" onclick="goPage(-1)">&#9664; Prev</button>
  <span id="page-info"></span>
  <button id="btn-next" onclick="goPage(1)">Next &#9654;</button>
</div>
<script>
const ROWS = {rows_json};
const CANDIDATES = {candidates_json};
const PAGE_SIZE = {page_size};
const MODE = {json.dumps(mode)};
let currentPage = 0;

const gallery = document.getElementById('gallery');

// IntersectionObserver: set img.src only when its tile scrolls into view.
const imgObserver = new IntersectionObserver((entries) => {{
  entries.forEach(entry => {{
    if (!entry.isIntersecting) return;
    const img = entry.target;
    if (img.dataset.src) {{
      img.src = img.dataset.src;
      delete img.dataset.src;
    }}
    imgObserver.unobserve(img);
  }});
}}, {{ rootMargin: '300px' }});

function escHtml(s) {{
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}}

function buildImg(src) {{
  if (!src) return '<div class="placeholder">image unavailable</div>';
  return `<div class="crop-wrap"><img data-src="${{escHtml(src)}}" alt=""></div>`;
}}

function buildPreTile(row) {{
  let opts = '';
  CANDIDATES.forEach(c => {{
    const sel = c.arkham_id === row.default_arkham_id ? ' selected' : '';
    opts += `<option value="${{escHtml(c.arkham_id)}}"${{sel}}>`
          + `${{escHtml(c.name)}} — ${{escHtml(c.arkham_id)}}</option>`;
  }});
  return `
    <div class="tile">
      <div class="tile-imgs">${{buildImg(row.crop)}}</div>
      <div class="tile-meta">
        <div class="crop-name">${{escHtml(row.crop)}}</div>
        <select>${{opts}}</select>
      </div>
    </div>`;
}}

function buildRecropTile(row) {{
  const body = row.status === 'ok' ? buildImg(row.crop)
    : '<div class="placeholder">atlas missing</div>';
  return `
    <div class="tile">
      <div class="tile-imgs">${{body}}</div>
      <div class="tile-meta">
        <div class="name">${{escHtml(row.name)}}</div>
        <div class="ids">${{escHtml(row.arkham_id)}}</div>
      </div>
    </div>`;
}}

function buildTile(row) {{
  return MODE === 'recrop' ? buildRecropTile(row) : buildPreTile(row);
}}

function getPageRows() {{
  const start = currentPage * PAGE_SIZE;
  return ROWS.slice(start, start + PAGE_SIZE);
}}

function buildPage(page) {{
  gallery.querySelectorAll('img[data-src]').forEach(img => imgObserver.unobserve(img));
  currentPage = page;
  gallery.innerHTML = '';
  getPageRows().forEach(row => {{
    gallery.insertAdjacentHTML('beforeend', buildTile(row));
  }});
  gallery.querySelectorAll('img[data-src]').forEach(img => imgObserver.observe(img));
  updatePagination();
}}

function updatePagination() {{
  const totalPages = Math.max(1, Math.ceil(ROWS.length / PAGE_SIZE));
  document.getElementById('page-info').textContent =
    `${{currentPage + 1}} / ${{totalPages}} pages (${{ROWS.length}} crops)`;
  document.getElementById('btn-prev').disabled = currentPage === 0;
  document.getElementById('btn-next').disabled = currentPage >= totalPages - 1;
  document.getElementById('stats').textContent = `${{ROWS.length}} crops`;
}}

function goPage(delta) {{
  const totalPages = Math.ceil(ROWS.length / PAGE_SIZE);
  const np = currentPage + delta;
  if (np < 0 || np >= totalPages) return;
  buildPage(np);
  window.scrollTo(0, 0);
}}

buildPage(0);
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.index.exists():
        print(f"--index not found: {args.index}", file=sys.stderr)
        return 2
    try:
        index_doc = load_index(args.index)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"--index not readable / malformed: {exc}", file=sys.stderr)
        return 2
    if not args.crops_dir.exists():
        print(f"--crops-dir not found: {args.crops_dir}", file=sys.stderr)
        return 2

    candidates = track_candidates(index_doc, args.track)

    if args.mode == "recrop":
        if args.atlas_manifest is None or args.mapping is None:
            print("--mode recrop requires --atlas-manifest and --mapping",
                  file=sys.stderr)
            return 2
        if not args.atlas_manifest.exists():
            print(f"--atlas-manifest not found: {args.atlas_manifest}",
                  file=sys.stderr)
            return 2
        if not args.mapping.exists():
            print(f"--mapping not found: {args.mapping}", file=sys.stderr)
            return 2
        try:
            from PIL import Image as image_mod
        except ImportError:
            print("ERROR: required dependency missing (Pillow) for --mode "
                  "recrop. Install with: pip install -r requirements.txt",
                  file=sys.stderr)
            return 2
        try:
            with args.atlas_manifest.open(encoding="utf-8") as fh:
                manifest = json.load(fh)
            with args.mapping.open(encoding="utf-8") as fh:
                mapping = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"recrop input not readable: {exc}", file=sys.stderr)
            return 2
        rows = build_recrop_rows(manifest, mapping, args.crops_dir,
                                 args.output.parent, args.dry_run,
                                 image_mod=image_mod)
        print(f"recrop: {len(rows)} atlas cell(s) re-sliced for confirmation.")
    else:
        crops = discover_crops(args.crops_dir)
        rows = build_pre_rows(crops, candidates)
        print(f"pre: {len(crops)} crop(s); {len(candidates)} {args.track} "
              f"candidate id(s).")
        if not args.dry_run:
            seed = build_seed(crops, candidates, args.track,
                              index_doc.get("source_dir", ""))
            seed_path = args.output.parent / "mapping_seed.json"
            atomic_write_json(seed_path, seed)
            print(f"Wrote {seed_path}")

    if args.dry_run:
        print("(dry-run mode — no files written)")
        return 0

    html = build_gallery_html(rows, candidates, args.page_size, args.mode, args.track)
    atomic_write_text(args.output, html)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
