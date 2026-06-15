#!/usr/bin/env python3
"""
Patch NumWidth and NumHeight in decomposed target JSONs to match chosen version's grid.

Run AFTER build-korean-overrides.py (orchestrated by apply-card-decisions.sh).

Usage:
  apply-grid-dims.py
    [--decisions PATH] [--decomposed-root PATH] [--dry-run]

Exit codes:
  0 OK
  1 warnings
  50 GMNotes invariant violation
  51 target file missing for arkham_id
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
DEFAULT_DECISIONS = SCRIPTS_DIR / "output" / "korean-image-review" / "review_decisions.json"
DEFAULT_DECOMPOSED_ROOT = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Player Cards"
    / "Korean-PlayerCards.KoreanI"
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Patch NumWidth/NumHeight in decomposed card JSONs."
    )
    p.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT)
    p.add_argument("--dry-run", action="store_true",
                   help="Compute changes but write nothing.")
    return p.parse_args(argv)


def _parse_gmnotes_id(obj: dict, location: str) -> str:
    raw = obj.get("GMNotes", "")
    if isinstance(raw, dict):
        gm = raw
    elif isinstance(raw, str):
        if not raw.strip():
            print(f"GMNotes invariant violation: empty GMNotes at {location}", file=sys.stderr)
            sys.exit(50)
        try:
            gm = json.loads(raw)
        except json.JSONDecodeError:
            print(f"GMNotes invariant violation: non-JSON GMNotes at {location}", file=sys.stderr)
            sys.exit(50)
    else:
        print(f"GMNotes invariant violation: unexpected type at {location}", file=sys.stderr)
        sys.exit(50)
    if "id" not in gm:
        print(f"GMNotes invariant violation: missing 'id' key at {location}", file=sys.stderr)
        sys.exit(50)
    return str(gm["id"])


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main(argv=None):
    args = parse_args(argv)

    with args.decisions.open(encoding="utf-8") as fh:
        decisions_data = json.load(fh)

    decisions_list = decisions_data.get("decisions", [])

    # Filter to only v0/v1/v2 choices (those with potentially different grid dims)
    relevant = [
        d for d in decisions_list
        if d.get("choice") in ("v0", "v1", "v2")
    ]

    # Build index of decomposed files by arkham_id
    files_by_id: dict[str, list[Path]] = {}
    for path in sorted(args.decomposed_root.rglob("*.json")):
        with path.open(encoding="utf-8") as fh:
            try:
                obj = json.load(fh)
            except json.JSONDecodeError:
                print(f"Invalid JSON in decomposed file: {path}", file=sys.stderr)
                continue
        arkham_id = _parse_gmnotes_id(obj, str(path))
        files_by_id.setdefault(arkham_id, []).append(path)

    summary: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "patched": 0,
        "no_change": 0,
        "missing": [],
        "warnings": [],
    }

    warnings = False

    for dec in relevant:
        arkham_id = dec.get("arkham_id", "")
        num_width = dec.get("num_width")
        num_height = dec.get("num_height")

        if num_width is None or num_height is None:
            summary["warnings"].append(
                f"arkham_id={arkham_id}: num_width/num_height missing in decision"
            )
            warnings = True
            continue

        target_paths = files_by_id.get(arkham_id)
        if not target_paths:
            print(f"Target file missing for arkham_id={arkham_id}", file=sys.stderr)
            summary["missing"].append(arkham_id)
            sys.exit(51)

        for path in target_paths:
            with path.open(encoding="utf-8") as fh:
                tgt = json.load(fh)

            custom_deck = tgt.get("CustomDeck", {})
            if not custom_deck:
                summary["warnings"].append(f"{path}: no CustomDeck")
                warnings = True
                continue

            deck_key = list(custom_deck.keys())[0]
            deck_data = custom_deck[deck_key]

            changed = False
            if deck_data.get("NumWidth") != num_width:
                deck_data["NumWidth"] = num_width
                changed = True
            if deck_data.get("NumHeight") != num_height:
                deck_data["NumHeight"] = num_height
                changed = True

            if changed:
                if not args.dry_run:
                    _atomic_write_json(path, tgt)
                summary["patched"] += 1
            else:
                summary["no_change"] += 1

    # Write grid_apply_summary.json
    out_path = args.decisions.parent / "grid_apply_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Grid dims applied: patched={summary['patched']}, no_change={summary['no_change']}, "
        f"missing={len(summary['missing'])}"
    )
    if args.dry_run:
        print("(dry-run mode — no files written)")

    if warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
