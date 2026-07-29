#!/usr/bin/env python3
"""
Audit FACE-atlas utilization for the Korean Player Cards langpack.

Aggregates the per-card index (``korean_card_index.csv``, produced by
``build-korean-card-index.py``) into a per-FACE-atlas utilization report: for
each distinct R2-hosted FaceURL atlas, how many of its grid cells are actually
used by a game card versus left empty (wasted). Analysis-only — read-only, no
image download, no JSON mutation, no repacking (see
``.am/korean-atlas-utilization-audit/``).

Scope (phase 1 of 2): FACE atlases, R2-hosted only (``face_source == "R2"``).
Steam faces and ALL backs are out of scope; backs and Steam faces are counted
under ``excluded`` for transparency but never aggregated.

The distinct-cell / anomaly-exclusion / utilization rules mirror analyze.md §3:
  - a cell counts once no matter how many reprints/variants reference it
    (DISTINCT), so ``card_ref_count >= used_cells``;
  - a ref whose cell index is missing or out of ``[0, total)`` is excluded from
    the cell math and reported under ``anomaly_cells`` (keeps ``wasted >= 0``);
  - a 1x1 atlas is the card (no slicing) -> used 1 / wasted 0 / util 100%;
  - URL query params (``?v=2``) are preserved verbatim, so ``?v=2`` and ``?v=3``
    are two distinct atlases.

Output under ``output/korean-atlas-utilization-audit/``:
  - utilization_report.csv   (1 row per atlas, RFC-4180 quoting)
  - utilization_report.json  (full records + summary + excluded)
  - manifest.json            (run metadata, summary, sha256 of each output)
  - atlas_heatmap.html        (only with --html: empty-cell visual heatmap,
                               used cell = card thumbnail via CSS sprite,
                               empty cell = red; zero image downloads at build)
  - repack_candidates.json   (unless --no-candidates: atlases below the
                               utilization threshold, descriptive only)

Usage:
  audit-atlas-utilization.py
    [--input PATH] [--output-dir PATH] [--html]
    [--max-util FLOAT] [--sort-by {wasted,util,used,url}]
    [--no-candidates] [--candidate-threshold FLOAT]
    [--target-sheet WxH] [--dry-run]

Exit codes:
  0  OK: no cell anomalies, no malformed rows, no grid inconsistencies
  1  warnings: the input carries cell anomalies (cell_out_of_bounds /
     card_id_invalid), malformed rows (missing face_url), or per-atlas grid
     inconsistencies (grid_mismatch / grid_invalid). Non-fatal — outputs written.
  2  input CSV missing/unreadable, or missing required columns
  42 sha256 mismatch on write-back verification
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sced_io import atomic_write_json, atomic_write_text

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = (
    SCRIPTS_DIR / "output" / "korean-player-card-index" / "korean_card_index.csv"
)
DEFAULT_OUTPUT = SCRIPTS_DIR / "output" / "korean-atlas-utilization-audit"

SCHEMA_VERSION = "1.0.0"
AUDITOR_SCRIPT = "audit-atlas-utilization.py"

DEFAULT_TARGET_SHEET = "10x7"  # 70 cells / sheet
DEFAULT_CANDIDATE_THRESHOLD = 50.0
# Sanity cap on a grid's cell count. Real atlases are <= 70 cells; a corrupt
# face_num_width/height must never drive range(total) / set(range(total))
# allocation. Grids above this are demoted to grid_invalid (DoS guard).
MAX_GRID_CELLS = 10_000

ANOMALY_CELL_OUT_OF_BOUNDS = "cell_out_of_bounds"
ANOMALY_CARD_ID_INVALID = "card_id_invalid"
FLAG_GRID_INVALID = "grid_invalid"
FLAG_GRID_MISMATCH = "grid_mismatch"

# Columns a valid index CSV must carry. The audit reads the face_*/card fields
# directly; `anomaly` is required only as a shape check — its value is NOT
# trusted. Anomalies (out-of-bounds / invalid cell) are re-derived independently
# in aggregate() so a stale or mis-tagged index cannot skew the cell math.
REQUIRED_COLUMNS = [
    "name", "arkham_id", "card_id",
    "face_url", "face_source", "face_num_width", "face_num_height",
    "face_cell_index", "back_url", "anomaly",
]

# Fixed report CSV column order (14 columns). List fields are JSON-encoded so the
# row stays a single RFC-4180 record despite embedded commas.
REPORT_CSV_HEADER = [
    "face_url", "source", "num_width", "num_height", "total_cells",
    "used_cells", "wasted_cells", "utilization_pct", "single_image",
    "card_ref_count", "reprint_overlap_count", "anomaly_cell_count",
    "used_cell_indices", "empty_cell_indices",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AtlasRecord:
    face_url: str
    source: str
    num_width: int
    num_height: int
    total_cells: int
    used_cells: int
    wasted_cells: int
    utilization_pct: float
    single_image: bool
    used_cell_indices: list[int]
    empty_cell_indices: list[int]
    card_ref_count: int
    cards: list[dict]
    reprint_overlaps: list[dict]
    anomaly_cells: list[dict]
    flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_target_sheet(value: str) -> int:
    """Parse a 'WxH' sheet spec into a cell count (W*H)."""
    raw = value.lower().replace("X", "x")
    parts = raw.split("x")
    if len(parts) != 2:
        raise ValueError(f"--target-sheet must be WxH (e.g. 10x7), got {value!r}")
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"--target-sheet must be WxH integers, got {value!r}")
    if w <= 0 or h <= 0:
        raise ValueError(f"--target-sheet dimensions must be positive, got {value!r}")
    return w * h


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Audit FACE-atlas utilization (empty/wasted cells) for the "
                    "Korean Player Cards langpack. Read-only, R2 faces only."
    )
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                   help="Path to korean_card_index.csv (default: the indexer output).")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                   help="Output directory for the report + manifest.")
    p.add_argument("--html", action="store_true",
                   help="Also emit atlas_heatmap.html (empty-cell visual heatmap).")
    p.add_argument("--max-util", type=float, default=100.0,
                   help="Only emit atlases with utilization_pct <= this value in "
                        "the report/heatmap (default 100 = all). Summary totals "
                        "are always over the full FACE-R2 set.")
    p.add_argument("--sort-by", choices=["wasted", "util", "used", "url"],
                   default="wasted",
                   help="Order of atlases in the report/heatmap (default: wasted).")
    p.add_argument("--no-candidates", action="store_true",
                   help="Do not emit repack_candidates.json.")
    p.add_argument("--candidate-threshold", type=float,
                   default=DEFAULT_CANDIDATE_THRESHOLD,
                   help="repack_candidates.json includes atlases with "
                        "utilization_pct < this value (default 50).")
    p.add_argument("--target-sheet", default=DEFAULT_TARGET_SHEET,
                   help="Target sheet grid WxH for the consolidation estimate "
                        "(default 10x7 = 70 cells).")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute everything but write no files; return the "
                        "would-be 0/1 exit code.")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rows(path: Path) -> list[dict]:
    """Read the index CSV via DictReader (never positional split).

    Raises ValueError if required columns are absent.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
        if missing:
            raise ValueError(f"input CSV missing required columns: {missing}")
        return list(reader)


def _parse_int(value) -> int | None:
    """Coerce a CSV string cell to int; return None on empty/invalid.

    Accepts integers and integral floats (TTSModManager may emit floats);
    rejects non-finite, non-integral, and non-numeric values (keep-and-flag).
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    try:
        i = int(s)
        return i
    except ValueError:
        pass
    try:
        f = float(s)
    except ValueError:
        return None
    if not math.isfinite(f):
        return None
    coerced = int(f)
    if float(coerced) != f:
        return None
    return coerced


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def filter_face_r2(rows: list[dict]) -> tuple[list[dict], dict]:
    """Keep only R2 FACE rows; tally everything else under `excluded`.

    The R2 filter is applied BEFORE grouping so a Steam URL can never create an
    atlas record. `back_*` columns are never aggregated (FACE-only phase).
    """
    kept: list[dict] = []
    steam_urls: set[str] = set()
    back_rows = 0
    malformed = 0
    other = 0
    for row in rows:
        if (row.get("back_url") or "").strip():
            back_rows += 1
        face_url = (row.get("face_url") or "").strip()
        source = (row.get("face_source") or "").strip()
        if not face_url:
            malformed += 1
            continue
        if source == "R2":
            kept.append(row)
        elif source == "Steam":
            steam_urls.add(face_url)
        else:
            other += 1
    excluded = {
        "steam_face_atlases": len(steam_urls),
        "back_rows_ignored": back_rows,
        "malformed_rows": malformed,
        "other_face_rows": other,
    }
    return kept, excluded


def _card_ref(row: dict) -> dict:
    return {
        "arkham_id": row.get("arkham_id", ""),
        "name": row.get("name", ""),
        "card_id": _parse_int(row.get("card_id")),
    }


def aggregate(kept: list[dict]) -> list[AtlasRecord]:
    """Group R2 FACE rows by verbatim face_url and compute per-atlas utilization."""
    groups: dict[str, list[dict]] = {}
    for row in kept:
        groups.setdefault(row["face_url"].strip(), []).append(row)

    records: list[AtlasRecord] = []
    for face_url, rows in groups.items():
        flags: list[str] = []
        nw = _parse_int(rows[0].get("face_num_width"))
        nh = _parse_int(rows[0].get("face_num_height"))

        grid_invalid = nw is None or nh is None or nw <= 0 or nh <= 0
        grid_oversize = (not grid_invalid) and nw * nh > MAX_GRID_CELLS
        if grid_invalid or grid_oversize:
            flags.append(FLAG_GRID_INVALID)
            grid_invalid = True
            # Zero the dims so an invalid/absurd grid contributes nothing to the
            # cell totals and can never drive range(total) (DoS guard). An
            # oversize grid is fully zeroed; an otherwise-invalid grid zeroes
            # only its bad dimension(s), keeping a still-positive one (e.g. a
            # 10x0 grid stays 10x0 -> total 0) — preserving prior behavior.
            if grid_oversize:
                nw = nh = 0
            else:
                nw = nw if (nw and nw > 0) else 0
                nh = nh if (nh and nh > 0) else 0
        else:
            for r in rows[1:]:
                if (_parse_int(r.get("face_num_width")) != nw
                        or _parse_int(r.get("face_num_height")) != nh):
                    flags.append(FLAG_GRID_MISMATCH)
                    break

        total = nw * nh
        single_image = (not grid_invalid) and nw == 1 and nh == 1

        used_set: set[int] = set()
        cell_to_cards: dict[int, list[dict]] = {}
        cards: list[dict] = []
        anomaly_cells: list[dict] = []

        # Classify each card ref. Anomalies (grid_invalid / card_id_invalid /
        # cell_out_of_bounds) are excluded from the cell math and recorded with
        # `capacity` = the grid total_cells they were range-checked against
        # (0 when the grid itself is invalid), keeping wasted_cells >= 0.
        for r in rows:
            ref = _card_ref(r)
            cell = _parse_int(r.get("face_cell_index"))
            if grid_invalid:
                anomaly_cells.append({**ref, "raw_cell_index": cell,
                                      "capacity": total, "code": FLAG_GRID_INVALID})
                continue
            if single_image:
                used_set.add(0)
                cell_to_cards.setdefault(0, []).append(ref)
                cards.append({**ref, "cell_index": 0})
                continue
            if cell is None:
                anomaly_cells.append({**ref, "raw_cell_index": None,
                                      "capacity": total,
                                      "code": ANOMALY_CARD_ID_INVALID})
            elif cell < 0 or cell >= total:
                anomaly_cells.append({**ref, "raw_cell_index": cell,
                                      "capacity": total,
                                      "code": ANOMALY_CELL_OUT_OF_BOUNDS})
            else:
                used_set.add(cell)
                cell_to_cards.setdefault(cell, []).append(ref)
                cards.append({**ref, "cell_index": cell})

        if single_image:
            used_cells, wasted, util = 1, 0, 100.0
            used_indices, empty_indices = [0], []
        elif grid_invalid:
            used_cells, wasted, util = 0, 0, 0.0
            used_indices, empty_indices = [], []
        else:
            used_indices = sorted(used_set)
            used_cells = len(used_set)
            wasted = total - used_cells
            util = round(100 * used_cells / total, 2) if total else 0.0
            empty_indices = sorted(set(range(total)) - used_set)

        reprint_overlaps = [
            {"cell_index": c, "count": len(lst), "cards": lst}
            for c, lst in sorted(cell_to_cards.items()) if len(lst) > 1
        ]

        records.append(AtlasRecord(
            face_url=face_url,
            source="R2",
            num_width=nw,
            num_height=nh,
            total_cells=total,
            used_cells=used_cells,
            wasted_cells=wasted,
            utilization_pct=util,
            single_image=single_image,
            used_cell_indices=used_indices,
            empty_cell_indices=empty_indices,
            card_ref_count=len(cards),
            cards=cards,
            reprint_overlaps=reprint_overlaps,
            anomaly_cells=anomaly_cells,
            flags=flags,
        ))
    return records


def _sort_key(sort_by: str):
    if sort_by == "wasted":
        return lambda r: (-r.wasted_cells, r.utilization_pct, r.face_url)
    if sort_by == "util":
        return lambda r: (r.utilization_pct, -r.wasted_cells, r.face_url)
    if sort_by == "used":
        return lambda r: (-r.used_cells, r.face_url)
    return lambda r: (r.face_url,)


def build_summary(records: list[AtlasRecord], target_cells: int,
                  filtered_count: int) -> dict:
    multi = [r for r in records
             if not r.single_image and FLAG_GRID_INVALID not in r.flags]
    single_count = sum(1 for r in records if r.single_image)
    total_cells = sum(r.total_cells for r in records)
    used_cells = sum(r.used_cells for r in records)
    wasted_cells = sum(r.wasted_cells for r in records)
    card_ref_count = sum(r.card_ref_count for r in records)
    reprint_overlap_cells = sum(len(r.reprint_overlaps) for r in records)
    anomaly_cells_excluded = sum(len(r.anomaly_cells) for r in records)

    buckets = {"100": 0, "75-99": 0, "50-74": 0, "25-49": 0, "0-24": 0}
    for r in multi:
        u = r.utilization_pct
        if u >= 100.0:
            buckets["100"] += 1
        elif u >= 75.0:
            buckets["75-99"] += 1
        elif u >= 50.0:
            buckets["50-74"] += 1
        elif u >= 25.0:
            buckets["25-49"] += 1
        else:
            buckets["0-24"] += 1

    return {
        "atlas_count": len(records),
        "multi_cell_count": len(multi),
        "single_image_count": single_count,
        "filtered_atlas_count": filtered_count,
        "total_cells": total_cells,
        "used_cells": used_cells,
        "wasted_cells": wasted_cells,
        "overall_utilization_pct": (round(100 * used_cells / total_cells, 2)
                                    if total_cells else 0.0),
        "card_ref_count": card_ref_count,
        "reprint_overlap_cells": reprint_overlap_cells,
        "anomaly_cells_excluded": anomaly_cells_excluded,
        "consolidation_target_sheets": (math.ceil(used_cells / target_cells)
                                        if used_cells else 0),
        "target_cells_per_sheet": target_cells,
        "utilization_buckets": buckets,
    }


def build_candidates(records: list[AtlasRecord], threshold: float,
                     generated_at: str) -> dict:
    cands = sorted(
        (r for r in records
         if not r.single_image
         and FLAG_GRID_INVALID not in r.flags
         and r.utilization_pct < threshold),
        key=lambda r: (-r.wasted_cells, r.utilization_pct, r.face_url),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "criteria": {
            "role": "face", "source": "R2",
            "max_utilization_pct": threshold,
            "exclude_single_image": True,
        },
        "candidate_count": len(cands),
        "candidates": [
            {
                "face_url": r.face_url,
                "num_width": r.num_width,
                "num_height": r.num_height,
                "total_cells": r.total_cells,
                "used_cells": r.used_cells,
                "wasted_cells": r.wasted_cells,
                "utilization_pct": r.utilization_pct,
                "used_cell_indices": r.used_cell_indices,
            }
            for r in cands
        ],
    }


def collect_anomaly_rows(records: list[AtlasRecord]) -> list[dict]:
    rows: list[dict] = []
    for r in records:
        for a in r.anomaly_cells:
            rows.append({"face_url": r.face_url, **a})
    rows.sort(key=lambda a: (a["code"], str(a.get("card_id")), a["face_url"]))
    return rows


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_report_csv(path: Path, records: list[AtlasRecord]) -> None:
    """Render the per-atlas report CSV (RFC-4180, UTF-8, \\n terminator) and write
    it atomically through sced_io (same-dir mkstemp + os.replace), matching the
    JSON/HTML writers rather than a bespoke fixed-name temp file."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(REPORT_CSV_HEADER)
    for r in records:
        writer.writerow([
            r.face_url, r.source, r.num_width, r.num_height,
            r.total_cells, r.used_cells, r.wasted_cells,
            r.utilization_pct, r.single_image, r.card_ref_count,
            len(r.reprint_overlaps), len(r.anomaly_cells),
            json.dumps(r.used_cell_indices),
            json.dumps(r.empty_cell_indices),
        ])
    atomic_write_text(path, buf.getvalue())


def _js_safe(text: str) -> str:
    """Make a JSON string safe to embed inside a <script> block.

    Escapes ``</`` (so ``</script>`` cannot end the block early) and the
    JS-illegal line/paragraph separators U+2028 / U+2029.
    """
    return (text.replace("</", '<\\/')
                .replace('\u2028', '\\u2028')
                .replace('\u2029', '\\u2029'))


def build_heatmap_viewmodels(records: list[AtlasRecord]) -> list[dict]:
    vms: list[dict] = []
    for r in records:
        if FLAG_GRID_INVALID in r.flags:
            continue  # no renderable grid
        cardmap: dict[int, dict] = {}
        for c in r.cards:
            cardmap.setdefault(c["cell_index"], c)
        cells: list[dict] = []
        for i in range(r.total_cells):
            if i in cardmap:
                c = cardmap[i]
                cells.append({"i": i, "used": True,
                              "arkham_id": c.get("arkham_id", ""),
                              "card_id": c.get("card_id"),
                              "name": c.get("name", "")})
            else:
                cells.append({"i": i, "used": False})
        vms.append({
            "face_url": r.face_url,
            "num_width": r.num_width,
            "num_height": r.num_height,
            "total_cells": r.total_cells,
            "used_cells": r.used_cells,
            "wasted_cells": r.wasted_cells,
            "utilization_pct": r.utilization_pct,
            "single_image": r.single_image,
            "cells": cells,
        })
    return vms


HEATMAP_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Korean FACE-atlas utilization heatmap</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1e1e1e; color: #eee;
         margin: 0; padding: 16px; }
  #banner { position: sticky; top: 0; background: #111; padding: 12px 16px;
            border-bottom: 2px solid #444; margin: -16px -16px 16px;
            z-index: 10; font-size: 14px; line-height: 1.5; }
  #banner b { color: #ffd866; }
  .atlas { margin-bottom: 22px; border: 1px solid #333; padding: 10px;
           border-radius: 6px; background: #232323; }
  .atlas header { margin-bottom: 8px; font-size: 13px; color: #ccc; }
  .atlas header b { color: #fff; }
  .atlas a { color: #6cf; text-decoration: none; }
  .grid { display: grid; gap: 2px; max-width: 560px; }
  .cell { aspect-ratio: 5 / 7; background-color: #2a2a2a;
          background-repeat: no-repeat; border-radius: 2px; }
  .cell.empty { background-color: #c0392b; }
  .cell.used { background-color: #333; }
  .legend { font-size: 12px; color: #999; margin-bottom: 14px; }
  .swatch { display: inline-block; width: 11px; height: 11px; border-radius: 2px;
            vertical-align: middle; margin: 0 3px 0 10px; }
</style>
</head>
<body>
<div id="banner">__BANNER__</div>
<div class="legend">
  <span class="swatch" style="background:#333"></span>used cell (card art)
  <span class="swatch" style="background:#c0392b"></span>empty / wasted cell
</div>
<div id="root"></div>
<script>
const ATLASES = __DATA__;
const root = document.getElementById('root');
const io = new IntersectionObserver(function (entries) {
  entries.forEach(function (e) {
    if (!e.isIntersecting) return;
    e.target.querySelectorAll('.cell.used[data-bg]').forEach(function (c) {
      c.style.backgroundImage = "url('" + cssUrl(c.getAttribute('data-bg')) + "')";
      c.removeAttribute('data-bg');
    });
    io.unobserve(e.target);
  });
}, { rootMargin: '300px' });

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function cssUrl(s) {
  // url('...') is single-quoted; neutralize the backslash (CSS escape) and the
  // single quote (string terminator) so a hostile face_url cannot break out of
  // the url() and inject CSS. Real R2 URLs contain neither, so they are
  // unchanged. String.fromCharCode(92) is a backslash (kept literal-free here).
  return String(s == null ? '' : s)
    .split(String.fromCharCode(92)).join('%5C')
    .split("'").join('%27');
}

ATLASES.forEach(function (a) {
  const sec = document.createElement('section');
  sec.className = 'atlas';
  const head = document.createElement('header');
  head.innerHTML = '<b>util ' + a.utilization_pct + '%</b> &middot; '
    + a.num_width + '×' + a.num_height + ' &middot; used ' + a.used_cells
    + ' / wasted ' + a.wasted_cells
    + (a.single_image ? ' &middot; <em>1×1 single image (no waste)</em>' : '')
    + ' &middot; <a href="' + esc(a.face_url) + '" target="_blank" rel="noopener">open</a>';
  sec.appendChild(head);

  const grid = document.createElement('div');
  grid.className = 'grid';
  grid.style.gridTemplateColumns = 'repeat(' + a.num_width + ', 1fr)';
  const nw = a.num_width, nh = a.num_height;
  const bgSize = (nw * 100) + '% ' + (nh * 100) + '%';

  a.cells.forEach(function (cell) {
    const d = document.createElement('div');
    if (cell.used) {
      d.className = 'cell used';
      const col = cell.i % nw, rowi = Math.floor(cell.i / nw);
      const px = nw > 1 ? (col / (nw - 1) * 100) + '%' : '0%';
      const py = nh > 1 ? (rowi / (nh - 1) * 100) + '%' : '0%';
      d.style.backgroundSize = bgSize;
      d.style.backgroundPosition = px + ' ' + py;
      d.setAttribute('data-bg', a.face_url);
      d.title = (cell.name || '') + ' [' + (cell.arkham_id || '') + '] cell ' + cell.i;
    } else {
      d.className = 'cell empty';
      d.title = 'empty cell ' + cell.i;
    }
    grid.appendChild(d);
  });

  sec.appendChild(grid);
  root.appendChild(sec);
  io.observe(sec);
});
</script>
</body>
</html>
"""


def build_heatmap_html(viewmodels: list[dict], summary: dict) -> str:
    s = summary
    banner = (
        f"<b>FACE R2 atlas utilization</b> &middot; {s['atlas_count']} atlases "
        f"({s['multi_cell_count']} multi-cell + {s['single_image_count']} single) "
        f"&middot; {s['used_cells']}/{s['total_cells']} cells used "
        f"&middot; {s['wasted_cells']} wasted "
        f"({100 - s['overall_utilization_pct']:.1f}% empty) "
        f"&middot; consolidation target ~{s['consolidation_target_sheets']} sheets "
        f"@ {s['target_cells_per_sheet']}/sheet (vs {s['atlas_count']} now)"
    )
    data = _js_safe(json.dumps(viewmodels, ensure_ascii=False))
    return HEATMAP_TEMPLATE.replace("__BANNER__", banner).replace("__DATA__", data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        target_cells = parse_target_sheet(args.target_sheet)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    input_path: Path = args.input
    out_dir: Path = args.output_dir

    if not input_path.exists():
        print(f"--input does not exist: {input_path}", file=sys.stderr)
        return 2
    try:
        rows = load_rows(input_path)
    except ValueError as exc:
        print(f"input CSV error: {exc}", file=sys.stderr)
        return 2

    source_csv_sha = compute_sha256(input_path)

    kept, excluded_counts = filter_face_r2(rows)
    records = aggregate(kept)

    anomaly_rows = collect_anomaly_rows(records)
    grid_invalid = sum(1 for r in records if FLAG_GRID_INVALID in r.flags)
    grid_mismatch = sum(1 for r in records if FLAG_GRID_MISMATCH in r.flags)

    emitted = [r for r in records if r.utilization_pct <= args.max_util]
    emitted.sort(key=_sort_key(args.sort_by))

    summary = build_summary(records, target_cells, len(emitted))

    warnings_emitted = bool(anomaly_rows) or excluded_counts["malformed_rows"] > 0 \
        or grid_invalid > 0 or grid_mismatch > 0

    generated_at = datetime.now(timezone.utc).isoformat()

    excluded = {
        **excluded_counts,
        "grid_invalid_atlases": grid_invalid,
        "grid_mismatch_atlases": grid_mismatch,
        "anomaly_rows": anomaly_rows,
    }

    report_doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_csv": input_path.name,
        "source_csv_sha256": source_csv_sha,
        "scope": {"role": "face", "source": "R2"},
        "summary": summary,
        "atlases": [asdict(r) for r in emitted],
        "excluded": excluded,
    }

    # Console summary (always printed).
    print(f"Read {len(rows)} index rows from {input_path}.")
    print(
        f"FACE R2 atlases: {summary['atlas_count']} "
        f"({summary['multi_cell_count']} multi-cell + "
        f"{summary['single_image_count']} single-image); "
        f"cells used {summary['used_cells']}/{summary['total_cells']} "
        f"(wasted {summary['wasted_cells']}, "
        f"{100 - summary['overall_utilization_pct']:.1f}% empty); "
        f"consolidation target ~{summary['consolidation_target_sheets']} sheets."
    )
    print(
        f"Excluded: Steam faces {excluded_counts['steam_face_atlases']} atlases, "
        f"back rows {excluded_counts['back_rows_ignored']}, "
        f"malformed {excluded_counts['malformed_rows']}, "
        f"anomaly cells {len(anomaly_rows)}, "
        f"grid_invalid {grid_invalid}, grid_mismatch {grid_mismatch}."
    )

    if args.dry_run:
        print("(dry-run mode — no files written)")
        return 1 if warnings_emitted else 0

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "utilization_report.json"
    csv_path = out_dir / "utilization_report.csv"
    manifest_path = out_dir / "manifest.json"
    html_path = out_dir / "atlas_heatmap.html"
    candidates_path = out_dir / "repack_candidates.json"

    atomic_write_json(json_path, report_doc)
    write_report_csv(csv_path, emitted)

    outputs: dict[str, dict] = {
        "utilization_report.json": {"sha256": compute_sha256(json_path)},
        "utilization_report.csv": {"sha256": compute_sha256(csv_path)},
    }

    if args.html:
        html = build_heatmap_html(build_heatmap_viewmodels(emitted), summary)
        atomic_write_text(html_path, html)
        outputs["atlas_heatmap.html"] = {"sha256": compute_sha256(html_path)}

    if not args.no_candidates:
        candidates = build_candidates(records, args.candidate_threshold, generated_at)
        atomic_write_json(candidates_path, candidates)
        outputs["repack_candidates.json"] = {"sha256": compute_sha256(candidates_path)}

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auditor": {"script": AUDITOR_SCRIPT, "version": SCHEMA_VERSION},
        "source_csv": input_path.name,
        "source_csv_sha256": source_csv_sha,
        "scope": {"role": "face", "source": "R2"},
        "summary": summary,
        "outputs": outputs,
    }
    atomic_write_json(manifest_path, manifest)

    # Re-read every declared output and verify its sha256.
    written = {
        "utilization_report.json": json_path,
        "utilization_report.csv": csv_path,
        "atlas_heatmap.html": html_path,
        "repack_candidates.json": candidates_path,
    }
    for name, decl in outputs.items():
        actual = compute_sha256(written[name])
        if actual != decl["sha256"]:
            print(
                f"sha256 mismatch on re-read: {name} "
                f"declared={decl['sha256']!r} actual={actual!r}",
                file=sys.stderr,
            )
            return 42

    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    if args.html:
        print(f"Wrote {html_path}")
    if not args.no_candidates:
        print(f"Wrote {candidates_path}")
    print(f"Wrote {manifest_path}")

    return 1 if warnings_emitted else 0


if __name__ == "__main__":
    sys.exit(main())
