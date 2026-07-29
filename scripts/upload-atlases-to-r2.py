#!/usr/bin/env python3
"""Upload the composed PDF-atlas PNGs to R2 at the EXACT key their FaceURL needs.

Why a dedicated tool (not upload-korean-images-to-r2.py): that tool PUTs to key
``images/sha256/<ab>/<hash>.png`` while the langpack FaceURLs (and atlas_manifest
face_url) are ``…r2.dev/langpack/images/sha256/<ab>/<hash>.png`` — i.e. the real
object key carries a ``langpack/`` prefix (the bucket ``tts-ahcg-assets`` stores
Korean assets under that path; existing card images live at ``ugc/…``). For an
R2 public bucket the object key == the URL path after ``…r2.dev/``. This tool
therefore derives each key directly from the manifest's ``face_url`` so the
uploaded key is guaranteed to match what TTS will request.

Credentials: env only — R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY,
R2_BUCKET (default tts-ahcg-assets). Idempotent: skips an object already present
(HEAD 200) with the same key. Verifies each upload via head_object + a public GET.

Usage:
  upload-atlases-to-r2.py --manifest <atlas_manifest.json> --staging-dir <dir> [--dry-run]

Exit: 0 ok · 2 input error · 3 credential/access error · 4 upload/verify failure
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, urllib.request
from pathlib import Path

DEFAULT_BUCKET = "tts-ahcg-assets"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key_from_url(url: str) -> str:
    if ".r2.dev/" in url:
        return url.split(".r2.dev/", 1)[1]
    raise ValueError(f"face_url not an r2.dev URL: {url}")


def public_get_ok(url: str) -> tuple[bool, int]:
    # Cloudflare's r2.dev edge bot-management returns 403 to the default
    # ``Python-urllib`` User-Agent even for public objects; send a browser-like
    # UA so the public-reachability probe reflects real client behaviour.
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/126.0.0.0 Safari/537.36"}
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200, int(r.headers.get("Content-Length", 0) or 0)
    except Exception as e:  # noqa: BLE001
        return False, 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--staging-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, default=None, help="upload_map.json to write.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.manifest.exists():
        print(f"InputError: manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    atlases = manifest.get("atlases", [])
    if not atlases:
        print("InputError: manifest has no atlases", file=sys.stderr)
        return 2

    # index staged PNGs by sha256
    staged = {sha256_file(p): p for p in sorted(args.staging_dir.glob("*.png"))}

    plan = []
    for a in atlases:
        sha = a["atlas_sha256"]
        url = a["face_url"]
        key = key_from_url(url)
        local = staged.get(sha)
        if local is None:
            print(f"InputError: no staged PNG with sha256={sha[:12]} for atlas {a['atlas_id']} "
                  f"(staged: {[p.name for p in staged.values()]})", file=sys.stderr)
            return 2
        plan.append({"atlas_id": a["atlas_id"], "sha256": sha, "key": key, "url": url,
                     "local": local, "size": local.stat().st_size})

    print(f"{'DRY-RUN — would upload' if args.dry_run else 'Uploading'} {len(plan)} atlas object(s) "
          f"to bucket {os.environ.get('R2_BUCKET', DEFAULT_BUCKET)}:")
    for p in plan:
        print(f"  {p['atlas_id']:14} {p['local'].name}  ({p['size']/1048576:.1f} MiB)")
        print(f"     key: {p['key']}")
        print(f"     url: {p['url']}")
    if args.dry_run:
        print("\n(dry-run: no network calls)")
        return 0

    # real upload
    try:
        import boto3  # noqa: PLC0415
        from botocore.exceptions import ClientError  # noqa: PLC0415
    except ImportError:
        print("InputError: boto3 not installed", file=sys.stderr)
        return 2
    acct = os.environ.get("R2_ACCOUNT_ID")
    ak = os.environ.get("R2_ACCESS_KEY_ID")
    sk = os.environ.get("R2_SECRET_ACCESS_KEY")
    bucket = os.environ.get("R2_BUCKET", DEFAULT_BUCKET)
    if not (acct and ak and sk):
        print("CredError: R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY must be set", file=sys.stderr)
        return 3
    s3 = boto3.client("s3", endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
                      aws_access_key_id=ak, aws_secret_access_key=sk, region_name="auto")

    uploads = []
    for p in plan:
        # idempotency: skip if key already present with matching size
        try:
            h = s3.head_object(Bucket=bucket, Key=p["key"])
            if int(h.get("ContentLength", -1)) == p["size"]:
                print(f"  [skip] {p['key']} already present ({p['size']} bytes)")
                uploads.append({**{k: p[k] for k in ('atlas_id','sha256','key','url','size')}, "skipped": True})
                continue
        except ClientError:
            pass  # not present -> upload
        try:
            with p["local"].open("rb") as fh:
                s3.put_object(Bucket=bucket, Key=p["key"], Body=fh, ContentType="image/png")
        except ClientError as e:
            print(f"UploadError: PUT {p['key']}: {e.response['Error'].get('Code')} "
                  f"({e.response['ResponseMetadata'].get('HTTPStatusCode')})", file=sys.stderr)
            return 4
        # verify
        try:
            h = s3.head_object(Bucket=bucket, Key=p["key"])
            remote = int(h.get("ContentLength", -1))
        except ClientError as e:
            print(f"VerifyError: head {p['key']}: {e.response['Error'].get('Code')}", file=sys.stderr)
            return 4
        ok_get, get_size = public_get_ok(p["url"])
        status = "OK" if (remote == p["size"] and ok_get) else "MISMATCH"
        print(f"  [{status}] {p['atlas_id']}: PUT {remote} bytes; public GET {'200' if ok_get else 'FAIL'} ({get_size})")
        if status != "OK":
            return 4
        uploads.append({**{k: p[k] for k in ('atlas_id','sha256','key','url','size')}, "skipped": False})

    if args.output:
        args.output.write_text(json.dumps({"bucket": bucket, "uploads": uploads}, indent=2), encoding="utf-8")
        print(f"Wrote {args.output}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
