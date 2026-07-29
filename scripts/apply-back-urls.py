#!/usr/bin/env python3
"""Set BackURL on the four sideways Taboo investigators from a back atlas.

Part of the ``korean-pdf-card-atlases`` sideways-investigator fix
(.am/korean-pdf-card-atlases/design-investigator-fix.md §5.4). This is a small,
FaceURL-untouching driver, deliberately SEPARATE from ``apply-atlas-faceurls.py``:

  - ``apply-atlas-faceurls.py`` writes ``FaceURL`` for *every* cell of *every*
    atlas in its (face) manifest — so routing the back atlas through it would
    overwrite the four investigators' FaceURL with the back URL. The back atlas is
    therefore composed into its OWN ``back/atlas_manifest.json`` and consumed here.
  - Setting a ``BackURL`` is a different operation from the atlas-aware FaceURL
    repoint: no CardID / grid / deck move, just one URL on an existing entry. A
    ``UniqueBack`` 10x6 deck slices its BackURL on the SAME 10x6 grid at the SAME
    cell index as the face, so the only requirement is that the card is already on
    its packed face cell — which this tool asserts (exit 5), forcing it to run
    AFTER the FaceURL apply.

The deck is hardcoded ``"8001"`` (the packed Taboo face deck); the native-reuse
back manifest's per-cell ``target_deck_key`` matches it, so the cross-check is a
consistency assert rather than a false-fail.

Usage:
  apply-back-urls.py
    --back-manifest .../track-a-taboo/back/atlas_manifest.json
    --mapping       .../track-a-taboo/mapping.json
    --decomposed-root .../Korean-PlayerCards.KoreanI
    --backup-out    .../pre_apply_back_backup.json
    [--revert pre_apply_back_backup.json]
    [--dry-run]

Exit codes:
  0  OK
  2  unreadable / malformed input (missing file, bad JSON, missing source_file)
  3  post-apply validation errors (edited set / BackURL value gate)
  5  pre-assert failure (a card is not on its packed 10x6 face cell on deck 8001)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atlas_patch import single_deck_key
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

# The packed Taboo face deck. UniqueBack means TTS slices the BackURL on the same
# grid at the same cell index as the face, so the back must land on this deck.
PACKED_DECK_KEY = "8001"
EXPECTED_NW = 10
EXPECTED_NH = 6


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Set BackURL on the four sideways Taboo investigators from a "
                    "separate back atlas manifest (FaceURL untouched)."
    )
    p.add_argument("--back-manifest", type=Path, required=True,
                   help="back/atlas_manifest.json emitted by compose-card-atlas.py "
                        "for the taboo-A-back atlas.")
    p.add_argument("--mapping", type=Path, required=True,
                   help="Human-confirmed mapping.json (for the back source_file set).")
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT,
                   help="Korean Player Cards decomposed object directory.")
    p.add_argument("--backup-out", type=Path, required=True,
                   help="Where to write the pre-apply BackURL backup (input to "
                        "--revert).")
    p.add_argument("--revert", type=Path, default=None,
                   help="Restore BackURL for every source_file from this backup, "
                        "then exit.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate everything but write no card files (the backup "
                        "snapshot is also skipped).")
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

    TOP-LEVEL only (``glob``, not ``rglob``) — mirrors
    ``apply-atlas-faceurls.py.build_basename_index``: a nested container file can
    legitimately share a basename with a top-level card object, and the mapping's
    source_file set is the authoritative target list. A duplicate basename at the
    top level is an integrity error (exit 2).
    """
    index: dict[str, Path] = {}
    for path in sorted(root.glob("*.json")):
        name = path.name
        if name in index:
            print(f"InputError: duplicate object basename {name!r} under {root}",
                  file=sys.stderr)
            sys.exit(2)
        index[name] = path
    return index


def back_atlas(manifest: dict) -> dict:
    """Return the single atlas entry from a back manifest (exit 2 if not exactly 1)."""
    atlases = manifest.get("atlases", [])
    if len(atlases) != 1:
        print(f"InputError: back manifest must hold exactly one atlas, "
              f"found {len(atlases)}", file=sys.stderr)
        sys.exit(2)
    return atlases[0]


def resolve_deck_key(card: dict, source_file: str) -> str:
    """``single_deck_key`` with a clean exit 5 on an empty/unusable CustomDeck.

    A card with no CustomDeck cannot carry a packed face cell, which is the same
    failure class as the pre-assert below — so it maps to exit 5 rather than an
    unhandled ``ValueError`` traceback (outside the documented 0/2/3/5 contract).
    """
    try:
        return single_deck_key(card)
    except ValueError as exc:
        print(f"PreAssertError: {source_file}: {exc}", file=sys.stderr)
        sys.exit(5)


def run_revert(args: argparse.Namespace) -> int:
    """Restore every backed-up card's BackURL atomically."""
    backup = load_json(args.revert)
    snapshots = backup.get("cards", {})
    if not isinstance(snapshots, dict):
        print("InputError: backup 'cards' is not an object", file=sys.stderr)
        return 2

    index = build_basename_index(args.decomposed_root)
    modified: dict[Path, dict] = {}
    restored = 0
    for source_file, snap in snapshots.items():
        if not isinstance(snap, dict) or "deck_key" not in snap \
                or "back_url" not in snap:
            print(f"InputError: backup entry for {source_file!r} is malformed "
                  f"(needs 'deck_key' + 'back_url')", file=sys.stderr)
            return 2
        path = index.get(Path(source_file).name)
        if path is None:
            print(f"InputError: backup references missing source_file "
                  f"{source_file!r}", file=sys.stderr)
            return 2
        card = load_json(path)
        deck_key = snap["deck_key"]
        if deck_key not in card.get("CustomDeck", {}):
            print(f"InputError: deck_key {deck_key!r} absent in {source_file}",
                  file=sys.stderr)
            return 2
        # back_url may legitimately be None — the faithful original state of a
        # card that had no BackURL before the apply (serialised as JSON null).
        card["CustomDeck"][deck_key]["BackURL"] = snap["back_url"]
        modified[path] = card
        restored += 1

    if args.dry_run:
        print(f"(dry-run) would restore BackURL on {restored} card file(s).")
        return 0
    atomic_write_json_batch(modified)
    print(f"Reverted BackURL on {restored} card file(s) from {args.revert}.")
    return 0


def run_apply(args: argparse.Namespace) -> int:
    manifest = load_json(args.back_manifest)
    mapping = load_json(args.mapping)
    atlas = back_atlas(manifest)
    back_url = atlas.get("face_url", "")
    if not back_url:
        print("InputError: back manifest atlas has no face_url", file=sys.stderr)
        return 2

    cells = atlas.get("cells", [])
    manifest_sources = {Path(c.get("source_file", "")).name
                        for c in cells if c.get("source_file")}
    # The back source_file set per the mapping (entries whose atlas == this atlas).
    atlas_id = atlas.get("atlas_id")
    mapping_back_sources = {
        Path(e["source_file"]).name
        for e in mapping.get("entries", [])
        if e.get("source_file") and e.get("atlas") == atlas_id
    }
    if manifest_sources != mapping_back_sources:
        print(f"InputError: back manifest source set {sorted(manifest_sources)} "
              f"!= mapping {atlas_id} source set {sorted(mapping_back_sources)}",
              file=sys.stderr)
        return 2

    index = build_basename_index(args.decomposed_root)

    # First pass: load each card, snapshot, and pre-assert it sits on its packed
    # 10x6 face cell on deck 8001 (so the BackURL slices at the right cell). Writes
    # nothing. (exit 5)
    loaded: dict[str, dict] = {}      # basename -> card dict
    backup_cards: dict[str, dict] = {}  # source_file -> {deck_key, back_url}
    for cell in cells:
        source_file = cell.get("source_file", "")
        basename = Path(source_file).name if source_file else ""
        path = index.get(basename)
        if path is None:
            print(f"InputError: back manifest cell source_file not found: "
                  f"{source_file!r}", file=sys.stderr)
            return 2
        if basename not in loaded:
            card = load_json(path)
            loaded[basename] = card
            deck_key = resolve_deck_key(card, source_file)
            entry = card["CustomDeck"][deck_key]
            backup_cards[source_file] = {
                "deck_key": deck_key,
                "back_url": entry.get("BackURL"),
            }

        card = loaded[basename]
        deck_key = resolve_deck_key(card, source_file)
        entry = card.get("CustomDeck", {}).get(deck_key, {})
        cell_index = int(cell["target_cell_index"])
        expected_card_id = int(PACKED_DECK_KEY) * 100 + cell_index
        problems = []
        if deck_key != PACKED_DECK_KEY:
            problems.append(f"deck_key={deck_key} != {PACKED_DECK_KEY}")
        if str(cell.get("target_deck_key")) != PACKED_DECK_KEY:
            problems.append(
                f"manifest target_deck_key={cell.get('target_deck_key')} "
                f"!= {PACKED_DECK_KEY}")
        if entry.get("NumWidth") != EXPECTED_NW or entry.get("NumHeight") != EXPECTED_NH:
            problems.append(
                f"grid={entry.get('NumWidth')}x{entry.get('NumHeight')} "
                f"!= {EXPECTED_NW}x{EXPECTED_NH}")
        if card.get("CardID") != expected_card_id:
            problems.append(
                f"CardID={card.get('CardID')} != {PACKED_DECK_KEY}*100+"
                f"{cell_index}={expected_card_id}")
        if entry.get("UniqueBack") is not True:
            problems.append(f"UniqueBack={entry.get('UniqueBack')} != True")
        if problems:
            print(f"PreAssertError: {source_file} not on its packed face cell: "
                  f"{'; '.join(problems)}", file=sys.stderr)
            return 5

    # Snapshot to disk BEFORE any card write (skip on --dry-run).
    backup_doc = {
        "schema_version": "1.0.0",
        "decomposed_root": str(args.decomposed_root),
        "back_url": back_url,
        "cards": backup_cards,
    }
    if not args.dry_run:
        atomic_write_json(args.backup_out, backup_doc)

    # Second pass: set BackURL in memory.
    changed = 0
    for cell in cells:
        basename = Path(cell["source_file"]).name
        card = loaded[basename]
        deck_key = single_deck_key(card)
        entry = card["CustomDeck"][deck_key]
        if entry.get("BackURL") != back_url:
            entry["BackURL"] = back_url
            changed += 1

    # Post-gate (exit 3): edited set == back source set; every BackURL == back_url.
    edited = set(loaded.keys())
    if edited != manifest_sources:
        print(f"GateError: edited set {sorted(edited)} != back source set "
              f"{sorted(manifest_sources)}", file=sys.stderr)
        return 3
    for basename, card in loaded.items():
        deck_key = single_deck_key(card)
        if card["CustomDeck"][deck_key].get("BackURL") != back_url:
            print(f"GateError: {basename} BackURL not set to back atlas url",
                  file=sys.stderr)
            return 3

    if args.dry_run:
        print(f"(dry-run) {len(loaded)} card file(s) would have BackURL set "
              f"(changed={changed}); gate passed; no files written.")
        return 0

    # atomic_write_json_batch is a staged two-phase commit; each per-file replace
    # is atomic but the loop is not, so an OS-level crash mid-batch can leave a
    # partial edit — recoverable with --revert from the backup written above.
    atomic_write_json_batch({index[bn]: card for bn, card in loaded.items()})
    print(f"Applied BackURL to {len(loaded)} card file(s) (changed={changed}).")
    print(f"BackURL: {back_url}")
    print(f"Backup written: {args.backup_out}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.revert is not None:
        return run_revert(args)
    return run_apply(args)


if __name__ == "__main__":
    sys.exit(main())
