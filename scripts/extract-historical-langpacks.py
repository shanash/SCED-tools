#!/usr/bin/env python3
"""
Extract historical Korean langpack versions from SCED git history.

Usage:
  extract-historical-langpacks.py [--sced-repo PATH] [--output-dir PATH] [--refs REF1,REF2,...]

Default --sced-repo: ../../SCED (relative to scripts/)
Default --output-dir: scripts/output/korean-image-review/sources/
Default --refs: 0772e28c=v0,c17769a4=v1,24a7587a=v2

Outputs:
  output/korean-image-review/sources/v0.json
  output/korean-image-review/sources/v1.json
  output/korean-image-review/sources/v2.json
  output/korean-image-review/sources/extraction_log.json

Exit codes:
  0 success
  1 warnings
  2 git show failure
  3 ContainedObjects missing or empty
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
DEFAULT_SCED_REPO = REPO_ROOT / "SCED"
DEFAULT_OUTPUT_DIR = SCRIPTS_DIR / "output" / "korean-image-review" / "sources"
LANGPACK_PATH = "langpack/korean_playercards.json"
DEFAULT_REFS = "0772e28c=v0,c17769a4=v1,24a7587a=v2"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Extract historical Korean langpack versions from SCED git history."
    )
    p.add_argument("--sced-repo", type=Path, default=DEFAULT_SCED_REPO,
                   help="Path to SCED git repository.")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="Directory to write extracted JSONs.")
    p.add_argument("--refs", type=str, default=DEFAULT_REFS,
                   help="Comma-separated ref=label pairs (e.g. abc123=v0,def456=v1).")
    return p.parse_args(argv)


def parse_refs(refs_str: str) -> list[tuple[str, str]]:
    """Parse 'ref=label,ref=label,...' into list of (ref, label) tuples."""
    result = []
    for part in refs_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            print(f"Invalid ref spec (expected ref=label): {part!r}", file=sys.stderr)
            sys.exit(1)
        ref, label = part.split("=", 1)
        result.append((ref.strip(), label.strip()))
    return result


def git_show(sced_repo: Path, ref: str, path: str) -> bytes | None:
    """Run git show <ref>:<path> and return stdout bytes, or None on failure."""
    cmd = ["git", "-C", str(sced_repo), "show", f"{ref}:{path}"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(
            f"git show failed for {ref}:{path}\n{result.stderr.decode(errors='replace')}",
            file=sys.stderr,
        )
        return None
    return result.stdout


def validate_langpack(data: dict, label: str, log: dict) -> bool:
    """Validate ContainedObjects array; record warnings in log. Returns False on fatal."""
    contained = data.get("ContainedObjects")
    if not contained:
        print(f"[{label}] ContainedObjects missing or empty", file=sys.stderr)
        return False

    parse_errors = []
    for i, obj in enumerate(contained):
        raw = obj.get("GMNotes", "")
        if not raw:
            parse_errors.append({"index": i, "error": "missing GMNotes"})
            continue
        try:
            gm = json.loads(raw)
        except json.JSONDecodeError as exc:
            parse_errors.append({"index": i, "error": f"non-JSON GMNotes: {exc}"})
            continue
        if "id" not in gm:
            parse_errors.append({"index": i, "error": "missing 'id' key in GMNotes"})

    total = len(contained)
    error_count = len(parse_errors)
    log["versions"][label] = {
        "total_objects": total,
        "parse_errors": parse_errors,
        "parse_error_count": error_count,
        "parse_success_rate": round((total - error_count) / total, 4) if total else 0.0,
    }

    if parse_errors:
        print(
            f"[{label}] {error_count}/{total} objects have GMNotes parse errors (warning only)",
            file=sys.stderr,
        )

    return True


def main(argv=None):
    args = parse_args(argv)

    refs = parse_refs(args.refs)
    if not refs:
        print("No refs specified.", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    log: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sced_repo": str(args.sced_repo),
        "langpack_path": LANGPACK_PATH,
        "refs": [{"ref": r, "label": l} for r, l in refs],
        "versions": {},
    }

    warnings = False
    fatal = False

    for ref, label in refs:
        raw_bytes = git_show(args.sced_repo, ref, LANGPACK_PATH)
        if raw_bytes is None:
            log["versions"][label] = {"error": f"git show failed for {ref}:{LANGPACK_PATH}"}
            fatal = True
            continue

        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[{label}] JSON decode error: {exc}", file=sys.stderr)
            log["versions"][label] = {"error": f"JSON decode error: {exc}"}
            fatal = True
            continue

        if not validate_langpack(data, label, log):
            fatal = True
            continue

        if log["versions"][label].get("parse_error_count", 0) > 0:
            warnings = True

        out_path = args.output_dir / f"{label}.json"
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{label}] Written to {out_path} ({log['versions'][label]['total_objects']} objects)")

    log_path = args.output_dir / "extraction_log.json"
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Extraction log written to {log_path}")

    if fatal:
        sys.exit(2)
    if warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
