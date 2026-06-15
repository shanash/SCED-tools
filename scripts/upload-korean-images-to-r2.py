#!/usr/bin/env python3
"""
Upload local Korean card PNGs to Cloudflare R2 and emit/merge an upload_map.json.

Workflow context (see .am/korean-image-apply/design.md §5.3):
  - Called between Phase 2 (user review) and Phase 4 (apply).
  - Hashes each PNG (SHA-256), uploads to `images/sha256/<first2>/<hash>.png`,
    records the canonical public URL in upload_map.json.
  - Idempotent: pre-existing entries with matching SHA-256 + HEAD 200 on the
    R2 key are re-used.

Credentials are read from environment variables only (never hard-coded):
  R2_ACCOUNT_ID          (required unless --no-upload / --dry-run)
  R2_ACCESS_KEY_ID       (required unless --no-upload / --dry-run)
  R2_SECRET_ACCESS_KEY   (required unless --no-upload / --dry-run)
  R2_BUCKET              default: "langpack"
  R2_PUBLIC_BASE         default: "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/images/"

Usage:
  # Actual upload
  python3 upload-korean-images-to-r2.py \
      --input-dir path/to/local/pngs \
      --output output/korean-image-apply/upload_map.json

  # Build/extend the map without any network calls (CI / offline audit)
  python3 upload-korean-images-to-r2.py \
      --input-dir path/to/local/pngs \
      --output output/korean-image-apply/upload_map.json \
      --no-upload

  # Rebuild the map from scratch (disaster recovery — prefer append/merge)
  python3 upload-korean-images-to-r2.py --rebuild-map ...
"""

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from sced_io import atomic_write_json

DEFAULT_PUBLIC_BASE = (
    "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/images/"
)
DEFAULT_BUCKET = "langpack"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Upload Korean card PNGs to R2 and produce upload_map.json."
    )
    parser.add_argument(
        "--input-dir", type=Path, required=True,
        help="Directory containing local PNGs (recursed).",
    )
    parser.add_argument(
        "--output", type=Path, required=True,
        help="Path to upload_map.json (read for append/merge, written atomically).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Do everything except the actual PUT/HEAD network calls. Map is "
             "still computed locally; existing entries are preserved.",
    )
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="Max parallel uploads (default: 4).",
    )
    parser.add_argument(
        "--skip-existing", action="store_true",
        help="Skip PNGs whose SHA-256 is already in upload_map AND s3.head_object "
             "succeeds. Default behaviour merges already: this flag only shortens "
             "the HEAD check to trust local map (faster, slightly less safe).",
    )
    parser.add_argument(
        "--no-upload", action="store_true",
        help="Do not import boto3 or touch the network. Useful for building an "
             "initial local map offline or on restricted CI.",
    )
    parser.add_argument(
        "--rebuild-map", action="store_true",
        help="Discard any existing upload_map.json and write a fresh one. Disaster "
             "recovery only — default is append/merge.",
    )
    return parser.parse_args(argv)


# ----------------------------- helpers -------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def r2_key_for_sha(digest: str) -> str:
    return f"images/sha256/{digest[:2]}/{digest.lower()}.png"


def public_url_for_key(base: str, key: str) -> str:
    # Join base + key, preserving exactly one slash between them.
    if not base.endswith("/"):
        base = base + "/"
    # base already ends in .../langpack/images/ by default. The key starts with
    # "images/sha256/..." so strip the shared "images/" prefix so we don't
    # double it.
    key_stripped = key
    if base.endswith("/images/") and key.startswith("images/"):
        key_stripped = key[len("images/"):]
    return base + key_stripped


def load_existing_map(output_path: Path, rebuild: bool) -> dict:
    if rebuild or not output_path.exists():
        return {"generated": None, "uploads": []}
    try:
        with output_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "uploads" not in data:
            return {"generated": None, "uploads": []}
        return data
    except (json.JSONDecodeError, OSError):
        return {"generated": None, "uploads": []}


def index_by_sha(upload_map: dict) -> dict:
    return {e["sha256"]: e for e in upload_map.get("uploads", []) if e.get("sha256")}


def iter_local_pngs(root: Path):
    for p in sorted(root.rglob("*.png")):
        if p.is_file():
            yield p


# ----------------------------- core ----------------------------------------


def _process_one(path: Path, digest: str, existing_entry, *,
                 args, s3, bucket: str, public_base: str) -> dict:
    """Resolve one unique PNG (by sha256) to its upload_map entry.

    Pure per-item work: reads only its own `existing_entry`, never mutates
    shared state, so it is safe to run concurrently. Returns the entry dict.
    """
    key = r2_key_for_sha(digest)
    url = public_url_for_key(public_base, key)

    reuse = False
    if existing_entry is not None:
        if args.skip_existing or args.dry_run or args.no_upload:
            reuse = True
        elif s3 is not None and _head_exists(s3, bucket, key):
            reuse = True

    if reuse and existing_entry is not None:
        # Copy so we never mutate the shared `by_sha` object from a worker.
        entry = dict(existing_entry)
        entry["r2_key"] = key
        entry["url"] = url
        entry.setdefault("local_path", str(path.relative_to(args.input_dir)))
        return entry

    size_bytes = path.stat().st_size
    uploaded_at = None
    if s3 is not None and not args.dry_run:
        _put_object(s3, bucket, key, path)
        uploaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif args.dry_run:
        print(f"  [dry-run] would PUT {path} -> s3://{bucket}/{key}")
    elif args.no_upload:
        # Record the entry so downstream tools see the intended URL.
        print(f"  [no-upload] recording {path.name} (sha={digest[:8]}..)")

    return {
        "local_path": str(path.relative_to(args.input_dir)),
        "sha256": digest,
        "r2_key": key,
        "url": url,
        "size_bytes": size_bytes,
        "uploaded_at": uploaded_at,
    }


def build_upload_map(args) -> dict:
    """Core routine. Returns the final upload_map dict.

    On success the returned map fully replaces the previous one. On a mid-run
    failure the entries resolved so far are discarded (they live only in this
    function's locals) and the caller's finally re-writes the last good map
    atomically via os.replace, so the on-disk map is never left truncated or
    partially written — a re-run resumes from the last fully-successful run.
    """
    if not args.input_dir.exists():
        print(f"ERROR: input dir not found: {args.input_dir}", file=sys.stderr)
        sys.exit(2)

    public_base = os.environ.get("R2_PUBLIC_BASE", DEFAULT_PUBLIC_BASE)
    bucket = os.environ.get("R2_BUCKET", DEFAULT_BUCKET)

    existing = load_existing_map(args.output, rebuild=args.rebuild_map)
    by_sha = index_by_sha(existing)

    s3 = None
    if not args.no_upload and not args.dry_run:
        s3 = _make_s3_client()
        if s3 is None:
            print(
                "ERROR: boto3 client could not be initialized. "
                "Set R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY or "
                "re-run with --no-upload / --dry-run.",
                file=sys.stderr,
            )
            sys.exit(3)

    local_files = list(iter_local_pngs(args.input_dir))
    print(f"Found {len(local_files)} local PNG(s) under {args.input_dir}")

    # Phase 1: hash sequentially and de-dupe by sha256 (first path wins, in the
    # sorted order produced by iter_local_pngs). Hashing is CPU-bound under the
    # GIL so threading it buys little; the network round-trips in phase 2 are
    # what benefit from concurrency.
    digest_to_path: dict[str, Path] = {}
    for path in local_files:
        try:
            digest = sha256_file(path)
        except OSError as exc:
            print(f"  skip {path}: cannot hash ({exc})", file=sys.stderr)
            continue
        digest_to_path.setdefault(digest, path)

    # Phase 2: resolve each unique digest to an upload_map entry. The R2 HEAD
    # (reuse check) and PUT are I/O-bound and release the GIL, so we fan them
    # out across `--concurrency` worker threads when there is real upload work.
    # Workers never mutate shared state: each returns its entry and the main
    # thread collects them, so no locking is required (the boto3 client is
    # itself safe to call from multiple threads).
    def work(digest, path):
        return _process_one(
            path, digest, by_sha.get(digest),
            args=args, s3=s3, bucket=bucket, public_base=public_base,
        )

    results: dict[str, dict] = {}
    use_pool = s3 is not None and args.concurrency > 1 and len(digest_to_path) > 1
    if use_pool:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            future_to_digest = {
                pool.submit(work, digest, path): digest
                for digest, path in digest_to_path.items()
            }
            for fut in as_completed(future_to_digest):
                digest = future_to_digest[fut]
                results[digest] = fut.result()
    else:
        for digest, path in digest_to_path.items():
            results[digest] = work(digest, path)

    # Merge resolved entries over any pre-existing ones; entries for digests not
    # present locally are preserved untouched.
    by_sha.update(results)

    # Sort by local_path for deterministic output.
    merged = sorted(by_sha.values(), key=lambda e: e.get("local_path", ""))
    result = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uploads": merged,
    }
    return result


def _make_s3_client():
    """Late import of boto3 so --no-upload works without it."""
    try:
        import boto3  # noqa: F401
    except ImportError:
        print(
            "ERROR: boto3 not installed. Install with `pip install boto3` or "
            "pass --no-upload / --dry-run.",
            file=sys.stderr,
        )
        return None

    account = os.environ.get("R2_ACCOUNT_ID")
    access = os.environ.get("R2_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not all([account, access, secret]):
        return None

    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="auto",
    )


def _head_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def _put_object(s3, bucket: str, key: str, path: Path) -> None:
    with path.open("rb") as fh:
        s3.put_object(Bucket=bucket, Key=key, Body=fh, ContentType="image/png")


# ----------------------------- main ----------------------------------------


def main(argv=None) -> int:
    args = parse_args(argv)

    # Load whatever we already have so a crash mid-run doesn't lose state.
    existing = load_existing_map(args.output, rebuild=args.rebuild_map)
    final_map = existing
    try:
        final_map = build_upload_map(args)
    finally:
        atomic_write_json(args.output, final_map)
        print(
            f"upload_map written: {args.output}  "
            f"({len(final_map.get('uploads', []))} entries)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
