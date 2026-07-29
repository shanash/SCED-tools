#!/usr/bin/env python3
"""Download a remote sprite-sheet and slice one (grid, cell) out as a PNG.

Part of the ``korean-pdf-card-atlases`` sideways-investigator fix
(.am/korean-pdf-card-atlases/design-investigator-fix.md §5.5). Two kinds of fix
crop are produced this way, each from an image that already exists on a CDN:

  - the four investigators' Korean deckbuilding *backs* are individual cells of
    their NON-taboo investigator BackURL sheets on R2 (the deckbuilding back is
    identical across the taboo / non-taboo printings); and
  - the English Mandy Taboo *front* is a cell of the EN deck-551 FaceURL sheet on
    Steam (no Korean Taboo front exists for Mandy).

Fetching + slicing here (rather than hand-cutting) gives every fix crop a
recorded, reproducible ``(URL, grid, cell, sha256)`` provenance.

Cloudflare ``r2.dev`` (and some Steam CDNs) answer the default urllib / requests
User-Agent with HTTP 403, so the download always sends a browser User-Agent.

Usage:
  slice-remote-cell.py --url URL --grid WxH --cell N --out PATH [--rotate180]

Exit codes:
  0  OK: cell sliced and written; output sha256 printed to stdout.
  2  bad argument / unreadable response / cell out of bounds.
  3  download failed (network error or non-200 response).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import tempfile
from pathlib import Path

# A real browser UA — r2.dev / akamai 403 the default urllib/requests UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Hard caps on untrusted remote input. The download is streamed and aborted past
# MAX_DOWNLOAD_BYTES, and Pillow is told to refuse a decoded image larger than
# MAX_IMAGE_PIXELS, so a hostile / corrupt response cannot exhaust memory. The
# legitimate source sheets here are at most a few tens of MB / ~40 MP.
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024  # 256 MiB
MAX_IMAGE_PIXELS = 200_000_000          # 200 MP (decompression-bomb guard)


def parse_grid(spec: str) -> tuple[int, int]:
    """Parse 'WxH' into (cols, rows). Raises ValueError on a malformed spec."""
    lowered = spec.lower().replace(" ", "")
    if "x" not in lowered:
        raise ValueError("--grid must be 'WxH'")
    w_s, h_s = lowered.split("x", 1)
    w, h = int(w_s), int(h_s)
    if w <= 0 or h <= 0:
        raise ValueError("--grid dimensions must be positive")
    return w, h


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download a sprite-sheet and slice one (grid, cell) as a PNG."
    )
    p.add_argument("--url", required=True, help="Source sheet URL.")
    p.add_argument("--grid", required=True, metavar="WxH",
                   help="Source sheet grid (columns x rows), e.g. 10x7.")
    p.add_argument("--cell", type=int, required=True,
                   help="Zero-based cell index (row-major: cell = y*W + x).")
    p.add_argument("--out", type=Path, required=True, help="Output PNG path.")
    p.add_argument("--rotate180", action="store_true",
                   help="Rotate the sliced cell 180 degrees before saving.")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="Per-request timeout in seconds (default 30).")
    return p.parse_args(argv)


def download(url: str, timeout: float) -> bytes:
    """Fetch ``url`` with a browser UA; raise on network error / non-200.

    Streams the body and aborts once ``MAX_DOWNLOAD_BYTES`` is exceeded (also
    rejecting an over-large declared ``Content-Length``) so a hostile or corrupt
    response cannot be buffered to memory in full.
    """
    import requests

    resp = requests.get(url, headers={"User-Agent": BROWSER_UA},
                        timeout=timeout, stream=True)
    try:
        resp.raise_for_status()
        declared = resp.headers.get("Content-Length")
        if declared is not None and int(declared) > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"declared Content-Length {declared} exceeds cap "
                f"{MAX_DOWNLOAD_BYTES} bytes")
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=1 << 16):
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"response body exceeds cap {MAX_DOWNLOAD_BYTES} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        resp.close()


def slice_cell(sheet_bytes: bytes, grid: tuple[int, int], cell: int,
               rotate180: bool, *, image_mod):
    """Crop cell ``cell`` of a ``grid`` (W x H) sheet; optionally rotate 180."""
    num_width, num_height = grid
    capacity = num_width * num_height
    if not (0 <= cell < capacity):
        raise ValueError(
            f"cell {cell} out of bounds for grid {num_width}x{num_height} "
            f"(capacity {capacity})"
        )
    with image_mod.open(io.BytesIO(sheet_bytes)) as img:
        sheet = img.convert("RGB")
    cell_w = sheet.width // num_width
    cell_h = sheet.height // num_height
    x = cell % num_width
    y = cell // num_width
    box = (x * cell_w, y * cell_h, (x + 1) * cell_w, (y + 1) * cell_h)
    crop = sheet.crop(box)
    if rotate180:
        crop = crop.rotate(180)
    return crop


def atomic_save_png(img, dest: Path) -> None:
    """Save a PIL image as PNG to ``dest`` atomically (same-dir temp + replace)."""
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


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        from PIL import Image as image_mod
    except ImportError:
        print("ERROR: required dependency missing (Pillow). "
              "Install with: pip install -r requirements.txt", file=sys.stderr)
        return 2
    image_mod.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS  # explicit bomb guard

    try:
        grid = parse_grid(args.grid)
    except ValueError as exc:
        print(f"argument error: {exc}", file=sys.stderr)
        return 2

    try:
        sheet_bytes = download(args.url, args.timeout)
    except Exception as exc:  # noqa: BLE001 — any fetch failure is exit 3
        print(f"DownloadError: {args.url}: {exc}", file=sys.stderr)
        return 3

    try:
        crop = slice_cell(sheet_bytes, grid, args.cell, args.rotate180,
                          image_mod=image_mod)
    except (ValueError, OSError) as exc:
        print(f"SliceError: {exc}", file=sys.stderr)
        return 2

    try:
        atomic_save_png(crop, args.out)
    except OSError as exc:
        print(f"SaveError: {args.out}: {exc}", file=sys.stderr)
        return 2
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(f"Wrote {args.out} ({crop.width}x{crop.height}) "
          f"from {args.grid} cell {args.cell}"
          f"{' +rot180' if args.rotate180 else ''}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
