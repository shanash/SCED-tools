#!/usr/bin/env python3
"""Repoint the Korean Player Cards langpack onto the composed PDF atlases.

This is a NEW, atlas-aware repoint driver (not a wrapper of the stock CLIs):
``apply-korean-image-decisions.py`` is driven by ``review_export.json`` and
matches on ``(deck_key, FaceURL)``; ``apply-grid-dims.py`` is driven by
``review_decisions.json`` and selects ``CustomDeck.keys()[0]`` — and neither can
rewrite ``CardID`` / the ``CustomDeck`` key. This tool consumes
``atlas_manifest.json`` + ``mapping.json``, matches each target card by
``source_file`` (NOT by old FaceURL — the dead-atlas cards share one FaceURL and
would match ambiguously), and performs the FaceURL set + grid patch +
``CardID``/``deck_key`` rewrite as one atomic, atlas-aware pass via the shared
``atlas_patch`` primitives. Adds ``--revert`` (mirrors
``apply-korean-image-decisions.py --allow-revert``).

The TTS CardID invariant is load-bearing: ``CardID == int(deck_key) * 100 +
cell_index``. Moving a card onto a packed atlas rewrites ``CardID`` and the
``CustomDeck`` key in lockstep; the post-apply gate (in ``atlas_patch``) asserts
the invariant holds for every edited card before the batch is considered done.

Usage:
  apply-atlas-faceurls.py
    --atlas-manifest atlas_manifest.json
    --mapping mapping.json
    --decomposed-root .../Korean-PlayerCards.KoreanI
    --backup-out .../pre_apply_backup.json
    [--output-dir DIR]
    [--revert pre_apply_backup.json]
    [--dry-run]

Normal mode: build a basename->Path index of the decomposed root; for each
manifest cell (across all atlases) locate the card JSON by source_file; assert
the on-disk current deck_key/CardID equals the manifest's current_* values OR
already equals the target_* values (idempotent re-run). Snapshot every in-scope
card's (FaceURL, CardID, deck_key, NumWidth, NumHeight) into --backup-out FIRST,
then apply via atlas_patch and commit with atomic_write_json_batch (all-or-
nothing). Run the post-apply validation gate over the edited set. The set of
files edited MUST equal exactly the mapping.json source_file set.

Revert mode (--revert): restore FaceURL/CardID/deck_key/grid for every
source_file from the backup, atomically.

Exit codes:
  0  OK
  2  unreadable / malformed input (missing file, bad JSON, missing source_file)
  3  post-apply validation errors (the atlas_patch gate)
  4  file-set mismatch (edited file set != mapping.json source_file set)
  5  source-value assertion failure (on-disk current deck_key/CardID != manifest
     current AND != manifest target)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atlas_patch import (
    repoint_to_packed_cell,
    set_face_url,
    single_deck_key,
    validate_gate,
)
from sced_io import atomic_write_json, atomic_write_json_batch

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
DEFAULT_DECOMPOSED_ROOT = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Player Cards"
    / "Korean-PlayerCards.KoreanI"
)
DEFAULT_OUTPUT_DIR = SCRIPTS_DIR / "output" / "korean-pdf-atlases"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Repoint the Korean Player Cards langpack onto the composed "
                    "PDF atlases (FaceURL + grid + CardID/deck_key rewrite)."
    )
    p.add_argument("--atlas-manifest", type=Path, required=True,
                   help="atlas_manifest.json emitted by compose-card-atlas.py.")
    p.add_argument("--mapping", type=Path, required=True,
                   help="Human-confirmed mapping.json (the source_file set).")
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT,
                   help="Korean Player Cards decomposed object directory.")
    p.add_argument("--backup-out", type=Path, required=True,
                   help="Where to write the pre-apply backup snapshot "
                        "(input to --revert).")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="Where to write apply_diff_summary.json.")
    p.add_argument("--revert", type=Path, default=None,
                   help="Restore FaceURL/CardID/deck_key/grid for every "
                        "source_file from this backup file, then exit.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute and validate everything but write no card files "
                        "(the backup snapshot is also skipped).")
    return p.parse_args(argv)


def load_json(path: Path):
    """Read + parse a JSON file, mapping any failure to a clean exit 2."""
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"InputError: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def build_basename_index(root: Path) -> dict[str, Path]:
    """Map each ``<stem>.json`` basename -> its Path directly under ``root``.

    Matching is by source_file basename (not by FaceURL): the dead-atlas cards
    all share one FaceURL and would match ambiguously. The scan is TOP-LEVEL only
    (``glob``, not ``rglob``): the langpack bag has nested container subdirs
    (per-investigator deck containers, e.g. ``TheGreatWork.tdc068/``) whose
    contents are not the top-level card objects this tool repoints — and a nested
    file can legitimately share a basename with a top-level one (e.g.
    ``LostHomunculus.tdc068b.json``). The mapping's ``source_file`` set is the
    authoritative target list and the post-scan file-set guard (exit 4) catches
    any target that is not found at the top level. A duplicate basename at the top
    level remains an integrity error (exit 2).
    """
    index: dict[str, Path] = {}
    for path in sorted(root.glob("*.json")):
        name = path.name
        if name in index:
            print(
                f"InputError: duplicate object basename {name!r} under {root}",
                file=sys.stderr,
            )
            sys.exit(2)
        index[name] = path
    return index


def snapshot_card(card_obj: dict) -> dict:
    """Capture the revert-relevant fields of one card's current state."""
    deck_key = single_deck_key(card_obj)
    entry = card_obj["CustomDeck"][deck_key]
    return {
        "deck_key": deck_key,
        "card_id": card_obj.get("CardID"),
        "face_url": entry.get("FaceURL"),
        "num_width": entry.get("NumWidth"),
        "num_height": entry.get("NumHeight"),
    }


def iter_manifest_cells(manifest: dict):
    """Yield each per-cell dict across all atlases in the manifest.

    Each yielded cell is paired with the FaceURL of the atlas it belongs to (the
    atlas-level ``face_url``), which is the URL written onto that card.
    """
    for atlas in manifest.get("atlases", []):
        face_url = atlas.get("face_url", "")
        for cell in atlas.get("cells", []):
            yield face_url, cell


def run_revert(args: argparse.Namespace) -> int:
    """Restore every backed-up card's FaceURL/CardID/deck_key/grid atomically."""
    backup = load_json(args.revert)
    snapshots = backup.get("cards", {})
    if not isinstance(snapshots, dict):
        print("InputError: backup 'cards' is not an object", file=sys.stderr)
        return 2

    index = build_basename_index(args.decomposed_root)
    modified: dict[Path, dict] = {}
    restored = 0
    for source_file, snap in snapshots.items():
        basename = Path(source_file).name
        path = index.get(basename)
        if path is None:
            print(
                f"InputError: backup references missing source_file "
                f"{source_file!r}",
                file=sys.stderr,
            )
            return 2
        card = load_json(path)
        old_key = single_deck_key(card)
        target_key = snap["deck_key"]
        entry = card["CustomDeck"][old_key]
        # Re-home under the original key if the current key differs (the repoint
        # may have moved the entry to a packed key).
        if old_key != target_key:
            card["CustomDeck"][target_key] = entry
            del card["CustomDeck"][old_key]
        entry["FaceURL"] = snap["face_url"]
        entry["NumWidth"] = snap["num_width"]
        entry["NumHeight"] = snap["num_height"]
        card["CardID"] = snap["card_id"]
        modified[path] = card
        restored += 1

    if args.dry_run:
        print(f"(dry-run) would restore {restored} card file(s) from backup.")
        return 0

    atomic_write_json_batch(modified)
    print(f"Reverted {restored} card file(s) from {args.revert}.")
    return 0


def run_apply(args: argparse.Namespace) -> int:
    manifest = load_json(args.atlas_manifest)
    mapping = load_json(args.mapping)

    mapping_source_files = {
        Path(e["source_file"]).name
        for e in mapping.get("entries", [])
        if e.get("source_file")
    }
    if not mapping_source_files:
        print("InputError: mapping.json has no source_file entries",
              file=sys.stderr)
        return 2

    index = build_basename_index(args.decomposed_root)

    # First pass: load each in-scope card once, snapshot it, and assert its
    # on-disk current state matches the manifest source values (or already equals
    # the target — an idempotent re-run). Nothing is written in this pass.
    loaded: dict[str, dict] = {}        # basename -> card dict (in-memory)
    backup_cards: dict[str, dict] = {}  # source_file -> snapshot
    summary = {
        "changed_files": 0,
        "replaced_faces": 0,
        "grid_patched": 0,
        "cardid_rewritten": 0,
        "unmatched": [],
    }

    for face_url, cell in iter_manifest_cells(manifest):
        source_file = cell.get("source_file", "")
        basename = Path(source_file).name if source_file else ""
        path = index.get(basename)
        if path is None:
            summary["unmatched"].append(source_file)
            continue

        if basename not in loaded:
            card = load_json(path)
            loaded[basename] = card
            backup_cards[source_file] = snapshot_card(card)

        card = loaded[basename]
        cur_key = single_deck_key(card)
        cur_card_id = card.get("CardID")
        cur_deck_key = str(cell.get("current_deck_key"))
        cur_target_deck_key = str(cell.get("target_deck_key"))
        manifest_cur_card_id = cell.get("current_card_id")
        manifest_target_card_id = cell.get("target_card_id")

        matches_current = (
            cur_key == cur_deck_key and cur_card_id == manifest_cur_card_id
        )
        matches_target = (
            cur_key == cur_target_deck_key and cur_card_id == manifest_target_card_id
        )
        if not (matches_current or matches_target):
            print(
                f"AssertionError: on-disk state for {source_file} "
                f"(deck_key={cur_key} CardID={cur_card_id}) matches neither "
                f"manifest current (deck_key={cur_deck_key} "
                f"CardID={manifest_cur_card_id}) nor target "
                f"(deck_key={cur_target_deck_key} "
                f"CardID={manifest_target_card_id})",
                file=sys.stderr,
            )
            return 5

    if summary["unmatched"]:
        for sf in summary["unmatched"]:
            print(f"InputError: manifest cell source_file not found: {sf!r}",
                  file=sys.stderr)
        return 2

    # Snapshot to disk BEFORE any card write (skip on --dry-run). The backup is
    # the input to --revert and must exist before the first mutation.
    backup_doc = {
        "schema_version": "1.0.0",
        "decomposed_root": str(args.decomposed_root),
        "cards": backup_cards,
    }
    if not args.dry_run:
        atomic_write_json(args.backup_out, backup_doc)

    # Second pass: apply the edits in memory.
    for face_url, cell in iter_manifest_cells(manifest):
        source_file = cell.get("source_file", "")
        basename = Path(source_file).name
        card = loaded[basename]

        target_deck_key = str(cell["target_deck_key"])
        target_cell_index = int(cell["target_cell_index"])
        target_nw = int(cell["target_num_width"])
        target_nh = int(cell["target_num_height"])

        cur_key = single_deck_key(card)
        entry = card["CustomDeck"][cur_key]
        is_target = (
            cur_key == target_deck_key
            and card.get("CardID") == int(target_deck_key) * 100 + target_cell_index
            and entry.get("NumWidth") == target_nw
            and entry.get("NumHeight") == target_nh
        )
        if is_target:
            # Target placement already correct: FaceURL-only (idempotent on the
            # grid/CardID). This is the A1 path (56 cards) and any re-run.
            face_changed = entry.get("FaceURL") != face_url
            set_face_url(card, cur_key, face_url)
            if face_changed:
                summary["replaced_faces"] += 1
        else:
            # Repoint onto the packed cell: FaceURL + grid + CardID/deck_key.
            repoint_to_packed_cell(
                card, target_deck_key, target_cell_index,
                target_nw, target_nh, face_url,
            )
            summary["replaced_faces"] += 1
            summary["grid_patched"] += 1
            summary["cardid_rewritten"] += 1

    # File-set guard: the set of files we touched MUST equal exactly the
    # mapping.json source_file set (no extra, no missing).
    edited_basenames = set(loaded.keys())
    if edited_basenames != mapping_source_files:
        missing = sorted(mapping_source_files - edited_basenames)
        extra = sorted(edited_basenames - mapping_source_files)
        print(
            f"FileSetError: edited file set != mapping source_file set "
            f"(missing={missing} extra={extra})",
            file=sys.stderr,
        )
        return 4

    # Post-apply validation gate over the edited set (URL/dims + CardID<->cell).
    gate_errors = validate_gate(
        {index[bn]: card for bn, card in loaded.items()}
    )
    if gate_errors:
        for err in gate_errors:
            print(err, file=sys.stderr)
        return 3

    summary["changed_files"] = len(loaded)

    if args.dry_run:
        print(
            f"(dry-run) {summary['changed_files']} file(s) would change "
            f"(replaced_faces={summary['replaced_faces']}, "
            f"grid_patched={summary['grid_patched']}, "
            f"cardid_rewritten={summary['cardid_rewritten']}); "
            f"validation gate passed; no files written."
        )
        return 0

    atomic_write_json_batch({index[bn]: card for bn, card in loaded.items()})

    summary_path = args.output_dir / "apply_diff_summary.json"
    atomic_write_json(summary_path, summary)

    print(
        f"Applied: changed_files={summary['changed_files']} "
        f"replaced_faces={summary['replaced_faces']} "
        f"grid_patched={summary['grid_patched']} "
        f"cardid_rewritten={summary['cardid_rewritten']}."
    )
    print(f"Backup written: {args.backup_out}")
    print(f"apply_diff_summary written: {summary_path}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.revert is not None:
        return run_revert(args)
    return run_apply(args)


if __name__ == "__main__":
    sys.exit(main())
