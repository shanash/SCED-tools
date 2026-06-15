#!/usr/bin/env python3
"""
Apply Korean langpack overrides (Nickname, Description, FaceURL, BackURL) to
decomposed player card JSONs in SCED-downloads.

Inputs:
  --source              path to source-langpack.json (ContainedObjects array)
  --decomposed-root     root of the decomposed player card tree (*.json files)
  --skip-cards          comma-separated GMNotes.id values to skip
  --source-dedup        how to handle duplicate ids in source: first | last | error
  --allow-multi-deck    allow source/target cards with >1 CustomDeck entry
  --dry-run             compute changes but write nothing
  --allow-guid-mismatch (unused flag kept for API compatibility; mismatches are
                         always logged and never block execution)
  --output-dir          where to write apply_summary.json
  --skipped-source-only-out  path for the one-id-per-line skipped list

Outputs:
  - In-place edits of decomposed card JSONs (unless --dry-run)
  - <output-dir>/apply_summary.json

Exit codes:
  0  success
  2  GMNotes invariant violation (missing or non-JSON GMNotes, or missing 'id')
  4  source duplicate ids with --source-dedup=error
  5  CustomDeck cardinality violation (0 entries, or >1 without --allow-multi-deck)
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCE = Path(__file__).resolve().parent / "output" / "korean-image-apply" / "source-langpack.json"
DEFAULT_DECOMPOSED_ROOT = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Player Cards"
    / "Korean-PlayerCards.KoreanI"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "korean-image-apply"
DEFAULT_SKIPPED_OUT = REPO_ROOT / ".am" / "korean-image-apply" / "skipped-source-only.txt"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Apply Korean langpack overrides to decomposed player card JSONs."
    )
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT)
    p.add_argument("--skip-cards", type=str, default="",
                   help="Comma-separated GMNotes.id values to skip.")
    p.add_argument("--source-dedup", choices=["first", "last", "error"], default="error",
                   help="How to handle duplicate ids in source.")
    p.add_argument("--allow-multi-deck", action="store_true",
                   help="Allow cards with >1 CustomDeck entry.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute changes but write nothing.")
    p.add_argument("--allow-guid-mismatch", action="store_true",
                   help="(No-op: GUID mismatches are always logged and never block.)")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--skipped-source-only-out", type=Path, default=DEFAULT_SKIPPED_OUT)
    return p.parse_args(argv)


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # Write to a unique temp file in the same directory (not a shared
    # "<stem>.tmp"), so two concurrent runs on the same tree cannot clobber each
    # other's temp file; os.replace is atomic on the same filesystem.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.chmod(tmp_name, 0o644)  # mkstemp creates 0600; keep data files readable
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _check_gmnotes(obj: dict, location: str) -> str:
    """Return the 'id' value from GMNotes, or exit with code 2 on violation."""
    raw = obj.get("GMNotes", "")
    if not raw:
        print(f"GMNotes invariant violation: missing GMNotes at {location}", file=sys.stderr)
        sys.exit(2)
    try:
        gm = json.loads(raw)
    except json.JSONDecodeError:
        print(f"GMNotes invariant violation: non-JSON GMNotes at {location}", file=sys.stderr)
        sys.exit(2)
    if "id" not in gm:
        print(f"GMNotes invariant violation: missing 'id' key at {location}", file=sys.stderr)
        sys.exit(2)
    return gm["id"]


def main(argv=None):
    args = parse_args(argv)

    skip_cards = set(
        s.strip() for s in args.skip_cards.split(",") if s.strip()
    ) if args.skip_cards else set()

    # Step 1: Load source JSON and validate GMNotes.
    with args.source.open(encoding="utf-8") as fh:
        source_data = json.load(fh)

    contained = source_data.get("ContainedObjects", [])

    # Count appearances per id to detect duplicates.
    id_count: dict[str, int] = {}
    for i, obj in enumerate(contained):
        card_id = _check_gmnotes(obj, f"{args.source}[{i}]")
        id_count[card_id] = id_count.get(card_id, 0) + 1

    source_duplicates = [cid for cid, cnt in id_count.items() if cnt > 1]

    if args.source_dedup == "error" and source_duplicates:
        print(
            f"Source duplicate ids with --source-dedup=error: {source_duplicates}",
            file=sys.stderr,
        )
        sys.exit(4)

    # Build src_by_id selecting first or last occurrence.
    src_by_id: dict[str, dict] = {}
    for obj in contained:
        raw = obj.get("GMNotes", "")
        card_id = json.loads(raw)["id"]
        if args.source_dedup == "first":
            if card_id not in src_by_id:
                src_by_id[card_id] = obj
        else:
            # "last" or "error" (if we got here, no duplicates in error mode)
            src_by_id[card_id] = obj

    # Step 2: Walk decomposed target directory.
    tgt_by_id_to_paths: dict[str, list[Path]] = {}
    for path in sorted(args.decomposed_root.rglob("*.json")):
        with path.open(encoding="utf-8") as fh:
            try:
                tgt = json.load(fh)
            except json.JSONDecodeError:
                print(f"Invalid JSON in target file: {path}", file=sys.stderr)
                sys.exit(2)
        card_id = _check_gmnotes(tgt, str(path))
        tgt_by_id_to_paths.setdefault(card_id, []).append(path)

    # Step 3: CustomDeck cardinality check.
    # 0 entries can never be applied (no deck key to index) -> always abort.
    # >1 entries -> abort unless --allow-multi-deck.
    for i, obj in enumerate(contained):
        n = len(obj.get("CustomDeck", {}))
        if n == 0 or (n > 1 and not args.allow_multi_deck):
            print(
                f"Source card index {i} has {n} CustomDeck entries (expected exactly 1). "
                "Use --allow-multi-deck to proceed with >1.",
                file=sys.stderr,
            )
            sys.exit(5)
    for paths in tgt_by_id_to_paths.values():
        for path in paths:
            with path.open(encoding="utf-8") as fh:
                tgt = json.load(fh)
            n = len(tgt.get("CustomDeck", {}))
            if n == 0 or (n > 1 and not args.allow_multi_deck):
                print(
                    f"Target file {path} has {n} CustomDeck entries (expected exactly 1). "
                    "Use --allow-multi-deck to proceed with >1.",
                    file=sys.stderr,
                )
                sys.exit(5)

    # Step 4: Initialize summary.
    summary: dict = {
        "applied": 0,
        "applied_unique_ids": 0,
        "skipped_source_only": [],
        "skipped_target_only_files": 0,
        "skipped_target_only_ids": [],
        "skipped_user_request": [],
        "guid_mismatches": [],
        "cardid_mismatches": [],
        "deckkey_mismatches": [],
        "source_duplicates": source_duplicates,
        "description_added": 0,
        "description_skipped_empty": 0,
        "no_change_files": 0,
    }

    # Step 5: Apply overrides.
    for target_id, paths in tgt_by_id_to_paths.items():
        if target_id not in src_by_id:
            summary["skipped_target_only_files"] += len(paths)
            summary["skipped_target_only_ids"].append(target_id)
            continue

        if target_id in skip_cards:
            summary["skipped_user_request"].append(target_id)
            continue

        src = src_by_id[target_id]
        any_path_changed_for_this_id = False

        for path in paths:
            with path.open(encoding="utf-8") as fh:
                tgt = json.load(fh)

            # Record mismatches (apply anyway, just log).
            if src.get("GUID") != tgt.get("GUID"):
                summary["guid_mismatches"].append({
                    "id": target_id,
                    "path": str(path),
                    "src_guid": src.get("GUID"),
                    "tgt_guid": tgt.get("GUID"),
                })
            if src.get("CardID") != tgt.get("CardID"):
                summary["cardid_mismatches"].append({
                    "id": target_id,
                    "path": str(path),
                    "src_cardid": src.get("CardID"),
                    "tgt_cardid": tgt.get("CardID"),
                })

            src_deck_key = list(src.get("CustomDeck", {}).keys())[0]
            tgt_deck_key = list(tgt.get("CustomDeck", {}).keys())[0]
            if src_deck_key != tgt_deck_key:
                summary["deckkey_mismatches"].append({
                    "id": target_id,
                    "path": str(path),
                    "src_key": src_deck_key,
                    "tgt_key": tgt_deck_key,
                })

            changed = False

            # Nickname (always overwrite).
            if src.get("Nickname", "") != tgt.get("Nickname", ""):
                tgt["Nickname"] = src.get("Nickname", "")
                changed = True

            # Description (whitespace-only = empty).
            src_desc = (src.get("Description") or "").strip()
            if src_desc:
                tgt_desc = (tgt.get("Description") or "").strip()
                if src_desc != tgt_desc:
                    if "Description" not in tgt:
                        summary["description_added"] += 1
                    tgt["Description"] = src.get("Description")  # original, not stripped
                    changed = True
            else:
                summary["description_skipped_empty"] += 1

            # FaceURL / BackURL into TARGET deck key.
            # Source field absent -> skip (don't overwrite target with null),
            # mirroring the Description policy (§5.6); also guards against a
            # malformed source deck missing these keys.
            src_deck = src["CustomDeck"][src_deck_key]
            src_face = src_deck.get("FaceURL")
            src_back = src_deck.get("BackURL")
            if src_face is not None and src_face != tgt["CustomDeck"][tgt_deck_key].get("FaceURL"):
                tgt["CustomDeck"][tgt_deck_key]["FaceURL"] = src_face
                changed = True
            if src_back is not None and src_back != tgt["CustomDeck"][tgt_deck_key].get("BackURL"):
                tgt["CustomDeck"][tgt_deck_key]["BackURL"] = src_back
                changed = True

            if changed:
                summary["applied"] += 1
                any_path_changed_for_this_id = True
                if not args.dry_run:
                    _atomic_write_json(path, tgt)
            else:
                summary["no_change_files"] += 1

        if any_path_changed_for_this_id:
            summary["applied_unique_ids"] += 1

    # Step 6: Collect source-only ids.
    for card_id in src_by_id:
        if card_id not in tgt_by_id_to_paths:
            summary["skipped_source_only"].append(card_id)

    # Step 7: Write summary.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(args.output_dir / "apply_summary.json", summary)

    # Step 8: Write skipped-source-only list.
    skipped_out = args.skipped_source_only_out
    skipped_out.parent.mkdir(parents=True, exist_ok=True)
    skipped_out.write_text(
        "\n".join(summary["skipped_source_only"]) + ("\n" if summary["skipped_source_only"] else ""),
        encoding="utf-8",
    )

    # Step 9: Print summary.
    n_src_only = len(summary["skipped_source_only"])
    n_usr_req = len(summary["skipped_user_request"])
    n_src_dup = len(summary["source_duplicates"])
    n_guid = len(summary["guid_mismatches"])
    n_cardid = len(summary["cardid_mismatches"])
    n_deckkey = len(summary["deckkey_mismatches"])
    n_tgt_only_ids = len(summary["skipped_target_only_ids"])

    print(f"Applied: {summary['applied']} files ({summary['applied_unique_ids']} unique ids)")
    print(f"No change: {summary['no_change_files']}")
    print(f"Skipped target-only: {summary['skipped_target_only_files']} files ({n_tgt_only_ids} unique ids)")
    print(f"Skipped (source-only): {n_src_only}")
    print(f"Skipped (user request): {n_usr_req}")
    print(f"Source duplicates: {n_src_dup}")
    print(f"GUID mismatches: {n_guid}")
    print(f"CardID mismatches: {n_cardid}")
    print(f"DeckKey mismatches: {n_deckkey}")
    print(f"Description added: {summary['description_added']}")
    print(f"Description skipped (empty source): {summary['description_skipped_empty']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
