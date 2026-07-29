#!/usr/bin/env python3
"""
Build per-card cropped PNGs + a browsable gallery for the Korean Player Cards.

This is *deliverable B* of the slug ``korean-player-card-image-atlas-inventory``.
Deliverable A (``build-korean-card-index.py``) already emitted the offline
metadata index ``output/korean-player-card-index/korean_card_index.json`` —
per-card records carrying each card's atlas URL, grid (``num_width``/
``num_height``) and cell position (``cell_index``/``x``/``y``) for face and back.
This tool consumes that index and produces the *visual* output:

  - downloads each distinct atlas image once into a persistent, URL-hash-keyed
    cache (``atlas-cache/<sha256(url)>.<ext>``);
  - slices the per-card cell out of each atlas with Pillow and writes one PNG per
    card face and per *unique* back (``cards/``);
  - deduplicates the shared single-image card backs to one PNG each (``backs/``);
  - emits a paginated, self-contained ``gallery.html`` linking the saved PNGs;
  - emits a ``manifest.json`` recording counts, dedup statistics, per-output
    sha256, the URL->cache-path map and the skipped anomalies.

Everything is confined to ``SCED-tools/scripts/``; no ``SCED/`` base code, no
``SCED-downloads/`` langpack data is touched.

Runtime dependencies (declared in ``requirements.txt`` alongside this script):
  - Pillow   (image decode + crop + PNG save)
  - requests (atlas download over HTTPS, TLS verification always on)
Both are imported lazily inside ``main()``; if either is missing the tool prints
an actionable ``pip install -r requirements.txt`` message and exits non-zero
rather than raising an ImportError traceback. ``pytest`` / ``playwright`` are
dev-only (the test suite) and are *not* runtime dependencies.

Usage:
  build-korean-card-crops.py
    [--index PATH] [--out-dir PATH] [--cache-dir PATH]
    [--concurrency N] [--offline] [--dry-run] [--limit N]
    [--page-size N] [--hash-images] [--force]
    [--timeout SECONDS] [--retries N]

Exit codes:
  0  OK: every needed atlas resolved and every sliceable card produced its PNG
     (only index-marked anomalies were skipped).
  1  warnings: one or more atlas downloads/decodes failed (offline miss, 404,
     oversized, corrupt image) so a sliceable card produced no PNG. Outputs are
     still written for everything that succeeded. Index-marked anomaly skips do
     NOT trigger this.
  2  --index not found / not readable.
  42 write-integrity failure: a gallery.html sha256 re-read mismatch, OR a
     written PNG that failed its always-on post-write Image.open(...).verify()
     / size>0 check.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sced_io import atomic_write_json, atomic_write_text

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
DEFAULT_INDEX = SCRIPTS_DIR / "output" / "korean-player-card-index" / "korean_card_index.json"
DEFAULT_OUT_DIR = SCRIPTS_DIR / "output" / "korean-player-card-crop-gallery"

SCHEMA_VERSION = "1.0.0"
GENERATOR_SCRIPT = "build-korean-card-crops.py"

# Soft drift cross-check only — never alters the exit code (design §3.5).
EXPECTED_ANOMALY_COUNT = 3

# Streamed-download size cap (design §4 NFRs). The largest real Korean atlas is
# well under this; the cap bounds memory and blocks an oversized-payload DoS.
MAX_ATLAS_BYTES = 64 * 1024 * 1024  # 64 MiB

# Exponential-backoff base in seconds. Injectable for fast tests via
# SCED_CROPS_BACKOFF_BASE env var or the hidden --_backoff-base flag (design §4).
DEFAULT_BACKOFF_BASE = 1.0
BACKOFF_FACTOR = 2.0
BACKOFF_JITTER = 0.25  # max additional random seconds per retry (anti-lockstep)

# A hostile/huge Retry-After value is clamped to this ceiling so it cannot stall
# the run unbounded (design §4 / §5.7 ceiling-cap test).
RETRY_AFTER_CEILING = 5.0  # seconds


# ---------------------------------------------------------------------------
# Typed cross-layer hand-offs (design §5)
# ---------------------------------------------------------------------------


@dataclass
class CropTask:
    """One unit of crop work routed from the plan layer to the crop layer.

    ``kind`` is one of "face" | "uback" | "shared_back" | "anomaly". For an
    anomaly task no cell math ever runs (x/y may be null). ``status`` is filled
    in by the crop layer ("ok" | "failed" | "integrity_failed" | "anomaly").
    A written PNG that fails its post-write verify() gets "integrity_failed"
    (distinct from "failed") so main() can route it to exit 42 (design §5.5).
    """

    kind: str
    source_file: str
    arkham_id: str
    card_id: object  # int or None
    name: str
    url: Optional[str]
    num_width: Optional[int]
    num_height: Optional[int]
    x: Optional[int]
    y: Optional[int]
    out_name: Optional[str]
    sideways: bool
    anomaly_code: str = ""
    status: str = "pending"


@dataclass
class AtlasResult:
    """Result of resolving/fetching one distinct atlas URL."""

    url: str
    cache_path: Optional[Path]
    status: str  # "downloaded" | "cached" | "failed"
    sha256: Optional[str] = None
    bytes: int = 0
    reason: str = ""


@dataclass
class Plan:
    """In-memory plan produced by build_plan (design §5.1)."""

    tasks: list = field(default_factory=list)
    distinct_urls: list = field(default_factory=list)
    shared_back_names: dict = field(default_factory=dict)  # url -> sharedback PNG name
    skipped_anomalies: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# CLI (design §4)
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build per-card cropped PNGs + a gallery for the Korean "
                    "Player Cards from the offline atlas index."
    )
    p.add_argument("--index", type=Path, default=DEFAULT_INDEX,
                   help="Input index (deliverable A korean_card_index.json).")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                   help="Root for cards/, backs/, gallery.html, manifest.json.")
    p.add_argument("--cache-dir", type=Path, default=None,
                   help="Persistent URL-hash-keyed atlas cache "
                        "(default: <out-dir>/atlas-cache).")
    p.add_argument("--concurrency", type=int, default=4,
                   help="Max parallel atlas downloads (default: 4).")
    p.add_argument("--offline", action="store_true",
                   help="Cache-only. No network. A cache miss for any needed "
                        "atlas is a per-atlas failure (counted), not a crash.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute the full plan and print a summary; write no "
                        "files. Returns the would-be exit code.")
    p.add_argument("--limit", type=int, default=0,
                   help="Process only the first N cards (sorted by source_file). "
                        "0 = all.")
    p.add_argument("--page-size", type=int, default=100,
                   help="Cards per gallery page (default: 100).")
    p.add_argument("--hash-images", action="store_true",
                   help="Also record per-PNG sha256 in manifest.outputs (slower).")
    p.add_argument("--force", action="store_true",
                   help="Re-slice and overwrite existing PNGs even if present.")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="Per-request connect+read timeout in seconds (default: 30).")
    p.add_argument("--retries", type=int, default=2,
                   help="Retries per atlas on a transient failure (default: 2).")
    # Hidden test hook: near-zero backoff base so retry-policy tests run fast.
    p.add_argument("--_backoff-base", type=float, default=None,
                   help=argparse.SUPPRESS)
    return p.parse_args(argv)


def resolve_backoff_base(args) -> float:
    """Resolve the injectable backoff base (design §4): flag > env > default."""
    if getattr(args, "_backoff_base", None) is not None:
        return float(args._backoff_base)
    env = os.environ.get("SCED_CROPS_BACKOFF_BASE")
    if env is not None:
        try:
            return float(env)
        except ValueError:
            pass
    return DEFAULT_BACKOFF_BASE


# ---------------------------------------------------------------------------
# Naming + key helpers (design §3.3 / §5.2) — pure, no I/O
# ---------------------------------------------------------------------------


def tie_hash(source_file: str) -> str:
    """Short, stable, per-row tiebreak hash (sha1 of source_file, first 8 hex)."""
    return hashlib.sha1(source_file.encode("utf-8")).hexdigest()[:8]


def face_name(arkham_id: str, card_id, source_file: str) -> str:
    """Collision-safe face PNG name: <arkham_id>_<card_id>_face_<tie>.png."""
    return f"{arkham_id}_{card_id}_face_{tie_hash(source_file)}.png"


def uback_name(arkham_id: str, card_id, source_file: str) -> str:
    """Collision-safe unique-back PNG name: <arkham_id>_<card_id>_back_<tie>.png."""
    return f"{arkham_id}_{card_id}_back_{tie_hash(source_file)}.png"


def shared_back_name(url: str) -> str:
    """Content-keyed shared-back PNG name (normalized to .png), in backs/."""
    return f"sharedback_{cache_key(url)[:12]}.png"


def cache_key(url: str) -> str:
    """sha256 hex of the full URL verbatim (incl. any ?v= query params)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def guess_ext(url: str, content_type: Optional[str]) -> str:
    """Resolve the cache file extension for an atlas URL.

    Prefer the URL path suffix (.jpg/.jpeg/.png). The 3 extension-less Steam
    shared backs end in a trailing slash with no suffix, so fall back to the
    HTTP Content-Type (image/jpeg -> .jpg, image/png -> .png; any ``;charset=``
    parameters are stripped first), defaulting to .jpg.
    """
    # Strip query string before inspecting the suffix.
    path = url.split("?", 1)[0].split("#", 1)[0]
    lower = path.lower()
    if lower.endswith(".png"):
        return ".png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return ".jpg"
    # No usable suffix — fall back to Content-Type.
    if content_type:
        ct = content_type.split(";", 1)[0].strip().lower()
        if ct == "image/png":
            return ".png"
        if ct in ("image/jpeg", "image/jpg"):
            return ".jpg"
    return ".jpg"


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents (chunked)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Index load + validate (design §6 step 2)
# ---------------------------------------------------------------------------


def load_index(index_path: Path) -> dict:
    """Load + minimally validate the index doc. Caller maps errors to exit 2."""
    with index_path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or "cards" not in doc or "counts" not in doc:
        raise ValueError("index missing required keys (cards, counts)")
    if not isinstance(doc["cards"], list):
        raise ValueError("index 'cards' is not a list")
    return doc


# ---------------------------------------------------------------------------
# Plan layer (design §5.1)
# ---------------------------------------------------------------------------


def build_plan(index_doc: dict, args) -> Plan:
    """Classify every (limited, source_file-sorted) card into crop tasks.

    Produces: the task list, the distinct demanded atlas URL set, the
    shared-back URL->name map, and the skipped-anomalies list. Anomaly rows are
    routed to the skip path *before* any cell math, so a null x/y never reaches
    arithmetic (design §5.1 / §8 risk #3).
    """
    cards = sorted(index_doc["cards"], key=lambda c: c["source_file"])
    if args.limit and args.limit > 0:
        cards = cards[: args.limit]

    plan = Plan()
    distinct: dict[str, None] = {}  # ordered set of demanded URLs

    for rec in cards:
        source_file = rec["source_file"]
        arkham_id = rec["arkham_id"]
        card_id = rec["card_id"]
        name = rec.get("name", "")
        sideways = bool(rec.get("sideways", False))
        anomaly = rec.get("anomaly", "") or ""

        # --- anomaly: skip BEFORE any cell math (x/y may be null) ----------
        if anomaly:
            plan.skipped_anomalies.append({
                "card_id": card_id,
                "arkham_id": arkham_id,
                "name": name,
                "source_file": source_file,
                "code": anomaly,
                "reason": "index marks row non-sliceable (x/y null)",
            })
            plan.tasks.append(CropTask(
                kind="anomaly", source_file=source_file, arkham_id=arkham_id,
                card_id=card_id, name=name, url=None, num_width=None,
                num_height=None, x=None, y=None, out_name=None,
                sideways=sideways, anomaly_code=anomaly, status="anomaly",
            ))
            continue

        # --- face crop (always, for non-anomaly rows) ---------------------
        face = rec["face"]
        face_url = face.get("url", "")
        if face_url:
            distinct.setdefault(face_url, None)
        plan.tasks.append(CropTask(
            kind="face", source_file=source_file, arkham_id=arkham_id,
            card_id=card_id, name=name, url=face_url,
            num_width=face.get("num_width"), num_height=face.get("num_height"),
            x=face.get("x"), y=face.get("y"),
            out_name=face_name(arkham_id, card_id, source_file),
            sideways=sideways,
        ))

        # --- back ----------------------------------------------------------
        back = rec.get("back")
        if back is None:
            # No back at all (must-tolerate branch, §3.1).
            continue
        back_url = back.get("url", "")
        if back.get("single_image"):
            # Shared single-image back: dedup by URL into backs/.
            if back_url:
                distinct.setdefault(back_url, None)
                sb_name = shared_back_name(back_url)
                plan.shared_back_names.setdefault(back_url, sb_name)
                plan.tasks.append(CropTask(
                    kind="shared_back", source_file=source_file,
                    arkham_id=arkham_id, card_id=card_id, name=name,
                    url=back_url,
                    num_width=back.get("num_width"),
                    num_height=back.get("num_height"),
                    x=back.get("x"), y=back.get("y"),
                    out_name=sb_name, sideways=sideways,
                ))
        else:
            # unique_back (gridded): slice the back's own cell.
            if back_url:
                distinct.setdefault(back_url, None)
            plan.tasks.append(CropTask(
                kind="uback", source_file=source_file, arkham_id=arkham_id,
                card_id=card_id, name=name, url=back_url,
                num_width=back.get("num_width"),
                num_height=back.get("num_height"),
                x=back.get("x"), y=back.get("y"),
                out_name=uback_name(arkham_id, card_id, source_file),
                sideways=sideways,
            ))

    plan.distinct_urls = list(distinct.keys())
    return plan


# ---------------------------------------------------------------------------
# Download + cache layer (design §5.2). resolve_cache_path is pure (no network);
# fetch_if_missing is the ONLY network-touching function.
# ---------------------------------------------------------------------------


def resolve_cache_path(url: str, cache_dir: Path) -> Optional[Path]:
    """Pure cache-resolve: return an existing cache file for url, else None.

    The extension is unknown for an extension-less URL until download time, so a
    cache hit is resolved by globbing ``<key>.*`` rather than guessing the ext.
    """
    key = cache_key(url)
    # Fast path: a URL with a known suffix resolves directly.
    for ext in (".jpg", ".png"):
        candidate = cache_dir / f"{key}{ext}"
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    # Fallback: any cached file with this key prefix.
    for candidate in sorted(cache_dir.glob(f"{key}.*")):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _is_transient(status_code: Optional[int]) -> bool:
    """A transient HTTP failure is a 5xx or a 429 (design §4)."""
    if status_code is None:
        return True  # network error / timeout
    if status_code == 429:
        return True
    return 500 <= status_code < 600


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header (delta-seconds form), capped to the ceiling."""
    if not value:
        return None
    try:
        secs = float(value.strip())
    except (ValueError, AttributeError):
        return None
    if secs < 0:
        return None
    return min(secs, RETRY_AFTER_CEILING)


def fetch_if_missing(url: str, cache_dir: Path, args, *, requests_mod,
                     backoff_base: float) -> AtlasResult:
    """Resolve url to a cached atlas, downloading it if absent (network fn).

    Cache hit -> status "cached". Offline cache miss -> status "failed" (never
    raises). Otherwise stream the body (TLS verify always on, redirects
    followed), aborting if cumulative bytes exceed MAX_ATLAS_BYTES, with up to
    args.retries retries on transient failures (network error / timeout / 5xx /
    429) using exponential backoff + jitter; 429 honors a capped Retry-After.
    A non-429 4xx / persistent error -> status "failed". The download is written
    to a same-directory temp file then os.replace'd into place (EXDEV-free,
    partial-download safe).
    """
    existing = resolve_cache_path(url, cache_dir)
    if existing is not None:
        return AtlasResult(url=url, cache_path=existing, status="cached",
                           sha256=compute_sha256(existing),
                           bytes=existing.stat().st_size)

    if args.offline:
        return AtlasResult(url=url, cache_path=None, status="failed",
                           reason="offline cache miss")

    attempts = args.retries + 1
    last_reason = ""
    for attempt in range(attempts):
        retry_after_hint: Optional[float] = None
        try:
            resp = requests_mod.get(
                url, timeout=args.timeout, stream=True, verify=True,
                allow_redirects=True,
            )
            try:
                status_code = resp.status_code
                if status_code >= 400:
                    if _is_transient(status_code):
                        last_reason = f"HTTP {status_code}"
                        if status_code == 429:
                            retry_after_hint = _parse_retry_after(
                                resp.headers.get("Retry-After"))
                        # fall through to retry/backoff below
                    else:
                        # Non-429 4xx: immediate failure, no retry.
                        return AtlasResult(
                            url=url, cache_path=None, status="failed",
                            reason=f"HTTP {status_code}")
                else:
                    # 2xx/3xx-resolved success: stream to a same-dir temp file.
                    content_type = resp.headers.get("Content-Type")
                    ext = guess_ext(url, content_type)
                    cache_path = cache_dir / f"{cache_key(url)}{ext}"
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(
                        prefix=cache_path.name + ".", suffix=".tmp",
                        dir=str(cache_dir))
                    total = 0
                    oversized = False
                    try:
                        with os.fdopen(fd, "wb") as fh:
                            for chunk in resp.iter_content(chunk_size=65536):
                                if not chunk:
                                    continue
                                total += len(chunk)
                                if total > MAX_ATLAS_BYTES:
                                    oversized = True
                                    break
                                fh.write(chunk)
                        if oversized:
                            os.unlink(tmp)
                            return AtlasResult(
                                url=url, cache_path=None, status="failed",
                                reason=f"exceeds {MAX_ATLAS_BYTES} bytes")
                        os.replace(tmp, cache_path)
                    except Exception:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
                        raise
                    return AtlasResult(
                        url=url, cache_path=cache_path, status="downloaded",
                        sha256=compute_sha256(cache_path),
                        bytes=cache_path.stat().st_size)
            finally:
                resp.close()
        except Exception as exc:  # noqa: BLE001 — network errors must not crash
            last_reason = f"{type(exc).__name__}: {exc}"
            # treated as transient (status_code None) — retried below

        # We only reach here on a transient failure that may be retried.
        if attempt < attempts - 1:
            backoff = backoff_base * (BACKOFF_FACTOR ** attempt)
            if retry_after_hint is not None:
                backoff = max(backoff, retry_after_hint)
            backoff = min(backoff, RETRY_AFTER_CEILING)
            backoff += random.uniform(0, BACKOFF_JITTER)
            time.sleep(backoff)

    return AtlasResult(url=url, cache_path=None, status="failed",
                       reason=last_reason or "transient failure exhausted")


def ensure_atlases(urls: list, cache_dir: Path, args, *, requests_mod,
                   backoff_base: float) -> dict:
    """Threaded driver: resolve/fetch every distinct URL once (design §5.2).

    Workers are stateless (each returns its own AtlasResult, writes only its own
    cache file) so concurrency needs no locks. Returns url -> AtlasResult.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, AtlasResult] = {}

    def work(u: str) -> AtlasResult:
        return fetch_if_missing(u, cache_dir, args, requests_mod=requests_mod,
                                backoff_base=backoff_base)

    use_pool = (not args.offline) and args.concurrency > 1 and len(urls) > 1
    if use_pool:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            future_to_url = {pool.submit(work, u): u for u in urls}
            for fut in as_completed(future_to_url):
                u = future_to_url[fut]
                results[u] = fut.result()
    else:
        for u in urls:
            results[u] = work(u)
    return results


# ---------------------------------------------------------------------------
# Crop layer (design §5.3)
# ---------------------------------------------------------------------------


def slice_cell(img, num_width: int, num_height: int, x: int, y: int):
    """Crop one grid cell out of an atlas image (the verified index contract).

    Integer floor-division pixel boxes; PIL crop box is (left, top, right,
    bottom) with right/bottom exclusive. The trailing edge pixels of a
    non-evenly-divisible grid are intentionally dropped (matches the index's
    integer cell-box math).
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


def _verify_png(path: Path, image_mod) -> bool:
    """Always-on post-write write-integrity check: verify() + size>0 (§5.3).

    ``image_mod`` is the PIL.Image module (``from PIL import Image``).
    """
    try:
        if path.stat().st_size <= 0:
            return False
        with image_mod.open(path) as probe:
            probe.verify()
        return True
    except Exception:  # noqa: BLE001 — any failure is a failed integrity check
        return False


def crop_task(task: CropTask, atlas_result: Optional[AtlasResult],
              out_dir: Path, args, *, image_mod) -> bool:
    """Execute one crop task; set task.status. Return True iff a PNG was verified.

    Decode safety: verify() (which consumes the handle) runs on a first open,
    then the image is reopened for the actual load/crop per Pillow's documented
    verify() contract. DecompressionBombError / UnidentifiedImageError / any PIL
    error are caught -> task "failed", never a crash. The save is atomic and is
    followed by the always-on per-PNG write-integrity check.
    """
    if task.kind == "anomaly":
        task.status = "anomaly"
        return False

    if atlas_result is None or atlas_result.cache_path is None:
        task.status = "failed"
        return False

    if task.kind == "shared_back":
        dest = out_dir / "backs" / task.out_name
    else:
        dest = out_dir / "cards" / task.out_name

    # Skip-existing (cheap re-runs). The atomic-write invariant guarantees an
    # existing target is complete, so skip can never resurrect a truncated file.
    if dest.exists() and not args.force:
        task.status = "ok"
        return False

    src = atlas_result.cache_path
    try:
        # Pre-decode structural verify (rejects non-image / bomb payloads).
        with image_mod.open(src) as probe:
            probe.verify()
        # Reopen for the real load/crop (verify() consumed the handle).
        with image_mod.open(src) as img:
            if task.kind == "shared_back":
                # single_image back: the whole image IS the back (1x1 copy).
                crop = img.crop((0, 0, img.width, img.height))
            else:
                nw = int(task.num_width)
                nh = int(task.num_height)
                cx = int(task.x)
                cy = int(task.y)
                crop = slice_cell(img, nw, nh, cx, cy)
            if crop.mode not in ("RGB", "RGBA"):
                crop = crop.convert("RGBA" if "A" in crop.getbands() else "RGB")
            _atomic_save_png(crop, dest)
    except Exception:  # noqa: BLE001 — per-image isolation, never crash the run
        task.status = "failed"
        return False

    if not _verify_png(dest, image_mod):
        # A written-but-corrupt PNG is a write-integrity failure (-> exit 42,
        # design §4/§5.5), NOT a generic unresolved-atlas "failed" (-> exit 1).
        task.status = "integrity_failed"
        return False

    task.status = "ok"
    return True


# ---------------------------------------------------------------------------
# Gallery emitter (design §5.4)
# ---------------------------------------------------------------------------


def _js_safe(text: str) -> str:
    """Make a json.dumps string safe to embed inside a <script> island.

    Three replacements (stronger than the precedent's single </ guard):
      - </      -> <\\/    (prevents a premature </script> close)
      - U+2028  -> \\u2028 (JS forbids LINE SEPARATOR raw in a string literal)
      - U+2029  -> \\u2029 (JS forbids PARAGRAPH SEPARATOR raw)
    """
    return (text.replace("</", "<\\/")
                .replace(" ", "\\u2028")
                .replace(" ", "\\u2029"))


def build_gallery_rows(plan: Plan, out_dir: Path) -> list:
    """Build one gallery row object per card (design §5.4 row schema).

    Groups the plan's tasks by source_file into per-card rows carrying the
    relative PNG paths and the back_kind / status discriminators.
    """
    by_source: dict[str, dict] = {}
    order: list = []
    for task in plan.tasks:
        sf = task.source_file
        if sf not in by_source:
            row = {
                "arkham_id": task.arkham_id,
                "card_id": task.card_id,
                "name": task.name,
                "sideways": bool(task.sideways),
                "face_img": None,
                "back_img": None,
                "back_kind": "none",
                "status": "ok",
            }
            by_source[sf] = row
            order.append(sf)
        row = by_source[sf]

        if task.kind == "anomaly":
            row["status"] = "anomaly"
            row["anomaly_code"] = task.anomaly_code
            continue

        if task.kind == "face":
            row["sideways"] = bool(task.sideways)
            if task.status == "ok":
                row["face_img"] = f"cards/{task.out_name}"
            else:
                row["status"] = "failed"
        elif task.kind == "uback":
            row["back_kind"] = "unique"
            if task.status == "ok":
                row["back_img"] = f"cards/{task.out_name}"
            else:
                row["status"] = "failed"
        elif task.kind == "shared_back":
            row["back_kind"] = "shared"
            if task.status == "ok":
                row["back_img"] = f"backs/{task.out_name}"
            else:
                row["status"] = "failed"

    return [by_source[sf] for sf in order]


def build_gallery_html(rows: list, page_size: int) -> str:
    """Render the self-contained paginated gallery HTML (design §5.4).

    Embeds the row JSON via json.dumps(..., ensure_ascii=False) through
    _js_safe; thumbnails point at relative saved-PNG paths; sideways rows get a
    CSS rotate(90deg); anomaly/failed rows render labeled placeholders;
    IntersectionObserver lazy-loads the <img data-src> tiles.
    """
    now = datetime.now(timezone.utc)
    generated = now.strftime("%Y-%m-%d %H:%M UTC")
    rows_json = _js_safe(json.dumps(rows, ensure_ascii=False))

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Korean Player Card Crops — {generated}</title>
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
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 16px; padding: 16px;
  }}
  .tile {{
    border: 1px solid #0f3460; border-radius: 8px; overflow: hidden;
    background: #0a0a1a; display: flex; flex-direction: column;
  }}
  .tile-imgs {{
    display: flex; gap: 4px; padding: 6px; align-items: center;
    justify-content: center; min-height: 120px;
  }}
  .crop-wrap {{
    flex: 1; display: flex; align-items: center; justify-content: center;
    overflow: hidden;
  }}
  .crop-wrap img {{ max-width: 100%; height: auto; display: block; }}
  /* sideways: atlas stores landscape; rotate the displayed tile upright. */
  .crop-wrap.sideways {{ position: relative; }}
  .crop-wrap.sideways img {{ transform: rotate(90deg); transform-origin: center; }}
  .placeholder {{
    flex: 1; min-height: 120px; display: flex; align-items: center;
    justify-content: center; text-align: center; font-size: 11px;
    padding: 8px; border-radius: 4px;
  }}
  .placeholder.anomaly {{ background: #3a2a0a; color: #ffd180; }}
  .placeholder.failed {{ background: #2a0a0a; color: #ffaaaa; }}
  .tile-meta {{ padding: 6px 8px; border-top: 1px solid #0f3460; }}
  .tile-meta .name {{ font-size: 12px; font-weight: bold; color: #eee; }}
  .tile-meta .ids {{ font-size: 10px; color: #888; }}
  .badge {{
    display: inline-block; font-size: 9px; padding: 1px 5px; border-radius: 8px;
    margin-left: 4px; background: #0f3460; color: #88ccff;
  }}
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
  <h1>Korean Player Card Crops</h1>
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
const PAGE_SIZE = {page_size};
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

function buildImg(src, sideways) {{
  const sw = sideways ? ' sideways' : '';
  return `<div class="crop-wrap${{sw}}"><img data-src="${{escHtml(src)}}" alt=""></div>`;
}}

function buildTile(row) {{
  let body = '';
  if (row.status === 'anomaly') {{
    const code = row.anomaly_code || 'cell_out_of_bounds';
    body = `<div class="placeholder anomaly">skipped (${{escHtml(code)}})</div>`;
  }} else if (row.status === 'failed') {{
    body = `<div class="placeholder failed">image unavailable</div>`;
  }} else {{
    let imgs = '';
    if (row.face_img) imgs += buildImg(row.face_img, row.sideways);
    if (row.back_kind !== 'none' && row.back_img) imgs += buildImg(row.back_img, row.sideways);
    if (!imgs) imgs = `<div class="placeholder failed">image unavailable</div>`;
    body = imgs;
  }}
  const swBadge = row.sideways ? '<span class="badge">sideways</span>' : '';
  const backBadge = row.back_kind && row.back_kind !== 'none'
    ? `<span class="badge">${{escHtml(row.back_kind)}} back</span>` : '';
  return `
    <div class="tile" data-status="${{escHtml(row.status)}}">
      <div class="tile-imgs">${{body}}</div>
      <div class="tile-meta">
        <div class="name">${{escHtml(row.name)}}${{swBadge}}${{backBadge}}</div>
        <div class="ids">${{escHtml(row.arkham_id)}} &middot; ${{escHtml(row.card_id)}}</div>
      </div>
    </div>`;
}}

function getPageRows() {{
  const start = currentPage * PAGE_SIZE;
  return ROWS.slice(start, start + PAGE_SIZE);
}}

function buildPage(page) {{
  // Disconnect lingering observers from the previous page before clearing DOM.
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
    `${{currentPage + 1}} / ${{totalPages}} pages (${{ROWS.length}} cards)`;
  document.getElementById('btn-prev').disabled = currentPage === 0;
  document.getElementById('btn-next').disabled = currentPage >= totalPages - 1;
  document.getElementById('stats').textContent = `${{ROWS.length}} cards`;
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
# Manifest emitter (design §5.5)
# ---------------------------------------------------------------------------


def build_manifest(index_path: Path, index_sha: str, plan: Plan,
                   atlas_results: dict, counts: dict, gallery_sha: str,
                   args, png_hashes: Optional[dict]) -> dict:
    """Build the §3.4 manifest dict."""
    atlas_cache = []
    for url in plan.distinct_urls:
        res = atlas_results.get(url)
        if res is None:
            continue
        cache_rel = (str(res.cache_path.relative_to(args.cache_dir))
                     if res.cache_path is not None
                     and _is_relative_to(res.cache_path, args.cache_dir)
                     else (str(res.cache_path) if res.cache_path else None))
        atlas_cache.append({
            "source_url": url,
            "cache_path": (f"atlas-cache/{cache_rel}" if cache_rel else None),
            "sha256": res.sha256,
            "bytes": res.bytes,
            "status": res.status,
        })

    outputs = {"gallery.html": {"sha256": gallery_sha}}
    if png_hashes:
        outputs["images"] = png_hashes

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"script": GENERATOR_SCRIPT, "version": SCHEMA_VERSION},
        "source_index": _index_display_path(index_path),
        "source_index_sha256": index_sha,
        "counts": counts,
        "atlas_cache": atlas_cache,
        "skipped_anomalies": plan.skipped_anomalies,
        "outputs": outputs,
    }
    return manifest


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _index_display_path(index_path: Path) -> str:
    """Display the index path relative to SCRIPTS_DIR when possible."""
    try:
        return str(index_path.resolve().relative_to(SCRIPTS_DIR))
    except ValueError:
        return str(index_path)


# ---------------------------------------------------------------------------
# Orchestration (design §5.6)
# ---------------------------------------------------------------------------


def _print_plan_summary(plan: Plan, args) -> None:
    faces = sum(1 for t in plan.tasks if t.kind == "face")
    ubacks = sum(1 for t in plan.tasks if t.kind == "uback")
    shared_refs = sum(1 for t in plan.tasks if t.kind == "shared_back")
    print(f"Plan: {len(plan.tasks)} tasks "
          f"(face {faces}, unique-back {ubacks}, shared-back refs {shared_refs}, "
          f"anomalies {len(plan.skipped_anomalies)})")
    print(f"Distinct atlas URLs to fetch: {len(plan.distinct_urls)}")
    print(f"Distinct shared backs: {len(plan.shared_back_names)}")
    if len(plan.skipped_anomalies) != EXPECTED_ANOMALY_COUNT:
        print(f"WARNING: anomaly-set drift: index marks "
              f"{len(plan.skipped_anomalies)}, allowlist expects "
              f"{EXPECTED_ANOMALY_COUNT}", file=sys.stderr)


def main(argv=None) -> int:
    args = parse_args(argv)

    # Late-import-to-degrade (design §4): PIL + requests imported inside main().
    try:
        from PIL import Image as image_mod  # the PIL.Image module
        import requests as requests_mod
    except ImportError:
        print(
            "ERROR: required dependencies missing (Pillow and/or requests). "
            "Install with: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    if args.cache_dir is None:
        args.cache_dir = args.out_dir / "atlas-cache"

    # --- load + sha256 index (exit 2 on missing/unreadable) ----------------
    if not args.index.exists():
        print(f"--index not found: {args.index}", file=sys.stderr)
        return 2
    try:
        index_doc = load_index(args.index)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"--index not readable / malformed: {exc}", file=sys.stderr)
        return 2
    index_sha = compute_sha256(args.index)

    # --- plan --------------------------------------------------------------
    plan = build_plan(index_doc, args)
    _print_plan_summary(plan, args)

    if args.dry_run:
        print("(dry-run mode — no files written)")
        # would-be exit: offline + any demanded URL means a guaranteed miss;
        # otherwise the plan alone cannot determine failures, so report 0.
        return 0

    # --- ensure output dirs ------------------------------------------------
    (args.out_dir / "cards").mkdir(parents=True, exist_ok=True)
    (args.out_dir / "backs").mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    backoff_base = resolve_backoff_base(args)

    # --- download/cache the distinct atlases (threaded) --------------------
    atlas_results = ensure_atlases(
        plan.distinct_urls, args.cache_dir, args,
        requests_mod=requests_mod, backoff_base=backoff_base)

    # --- crop every task ---------------------------------------------------
    written_pngs: list[Path] = []
    saved_shared: set[str] = set()
    for task in plan.tasks:
        if task.kind == "shared_back":
            # Save the shared back once per distinct URL (design §6 step 8).
            if task.url in saved_shared:
                # Already written this run; mark ok if the file exists.
                dest = args.out_dir / "backs" / task.out_name
                task.status = "ok" if dest.exists() else "failed"
                continue
        atlas_result = atlas_results.get(task.url) if task.url else None
        wrote = crop_task(task, atlas_result, args.out_dir, args,
                          image_mod=image_mod)
        if task.kind == "shared_back" and task.status == "ok":
            saved_shared.add(task.url)
        if wrote:
            if task.kind == "shared_back":
                written_pngs.append(args.out_dir / "backs" / task.out_name)
            else:
                written_pngs.append(args.out_dir / "cards" / task.out_name)

    failed_tasks = [t for t in plan.tasks if t.status == "failed"]

    # --- gallery -----------------------------------------------------------
    rows = build_gallery_rows(plan, args.out_dir)
    gallery_html = build_gallery_html(rows, args.page_size)
    gallery_path = args.out_dir / "gallery.html"
    atomic_write_text(gallery_path, gallery_html)
    gallery_sha = compute_sha256(gallery_path)

    # --- per-PNG sha256 (only with --hash-images) --------------------------
    png_hashes = None
    if args.hash_images:
        png_hashes = {}
        for png in written_pngs:
            if png.exists():
                rel = str(png.relative_to(args.out_dir))
                png_hashes[rel] = {"sha256": compute_sha256(png)}

    # --- counts ------------------------------------------------------------
    counts = {
        "cards_total": len(index_doc["cards"]),
        "face_crops_written": sum(
            1 for t in plan.tasks if t.kind == "face" and t.status == "ok"),
        "unique_back_crops_written": sum(
            1 for t in plan.tasks if t.kind == "uback" and t.status == "ok"),
        "shared_backs_written": len(saved_shared),
        "shared_back_references": sum(
            1 for t in plan.tasks if t.kind == "shared_back"),
        "anomalies_skipped": len(plan.skipped_anomalies),
        "atlases_distinct": len(plan.distinct_urls),
        "atlases_downloaded": sum(
            1 for r in atlas_results.values() if r.status == "downloaded"),
        "atlases_from_cache": sum(
            1 for r in atlas_results.values() if r.status == "cached"),
        "atlases_failed": sum(
            1 for r in atlas_results.values() if r.status == "failed"),
    }

    # --- manifest ----------------------------------------------------------
    manifest = build_manifest(
        args.index, index_sha, plan, atlas_results, counts, gallery_sha,
        args, png_hashes)
    manifest_path = args.out_dir / "manifest.json"
    atomic_write_json(manifest_path, manifest)

    # --- write-integrity: re-read gallery.html sha256 (exit 42) ------------
    actual_gallery_sha = compute_sha256(gallery_path)
    declared = manifest["outputs"]["gallery.html"]["sha256"]
    if actual_gallery_sha != declared:
        print(
            f"sha256 mismatch on re-read: gallery.html "
            f"declared={declared!r} actual={actual_gallery_sha!r}",
            file=sys.stderr,
        )
        return 42

    # --- write-integrity: any PNG that failed its always-on post-write
    # verify()/size>0 check is a write-integrity failure -> exit 42 (design
    # §4/§5.5), taking precedence over the exit-1 (failed_outputs) path below.
    integrity_failures = [t for t in plan.tasks if t.status == "integrity_failed"]
    if integrity_failures:
        print(
            f"write-integrity check failed for {len(integrity_failures)} "
            f"written PNG(s) (exit 42).",
            file=sys.stderr,
        )
        return 42

    # --- summary -----------------------------------------------------------
    print(
        f"Wrote {counts['face_crops_written']} face + "
        f"{counts['unique_back_crops_written']} unique-back + "
        f"{counts['shared_backs_written']} shared-back PNGs."
    )
    print(
        f"Atlases: {counts['atlases_downloaded']} downloaded, "
        f"{counts['atlases_from_cache']} cached, "
        f"{counts['atlases_failed']} failed."
    )
    print(f"Wrote {gallery_path}  sha256={gallery_sha[:16]}...")
    print(f"Wrote {manifest_path}")

    if failed_tasks:
        print(f"{len(failed_tasks)} crop task(s) failed (exit 1).",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
