#!/usr/bin/env python3
"""
Verify atlas-data.{cards,decks}.json against SCED-downloads decomposed tree.

Bidirectional verification:
  1. Each cards[] entry → re-open decomposed_path and assert
     face_url/back_url/num_width/num_height match.
  2. Walk decomposed_root and assert every card .json appears in cards[].
  3. Cross-reference decks against grouped cards.
  4. Optional sha256 verification of manifest.outputs vs actual files.

Usage:
  verify-korean-atlases.py
    [--input-dir PATH] [--decomposed-root PATH]
    [--check-hashes] [--no-check-hashes] [--strict]

Exit codes:
  0  OK (all matches)
  1  warnings (e.g. card_count drift)
  42 hash mismatch (manifest sha256 vs actual file)
  45 cards.json ↔ decomposed mismatch (face/back/grid)
  46 decks.json cross-reference failure (deck_id orphan, member mismatch)
  47 manifest.outputs missing or stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
SCED_DOWNLOADS_PATH = REPO_ROOT / "SCED-downloads"
DEFAULT_DECOMPOSED_ROOT = SCED_DOWNLOADS_PATH / "decomposed"
DEFAULT_INPUT_DIR = SCRIPTS_DIR / "output" / "korean-atlas-extract"

PLAYER_CARDS_SUBPATH = Path(
    "language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI"
)
BARKHAM_SUBPATH = Path(
    "language-pack/Korean - Campaigns/Korean-Campaigns.KoreanC/BarkhamHorror.kr_bark"
)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify atlas-data.{cards,decks}.json against decomposed tree."
    )
    p.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
                   help="Directory containing atlas-data.* + manifest.json.")
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT,
                   help="Root of SCED-downloads/decomposed tree.")
    p.add_argument("--check-hashes", dest="check_hashes",
                   action="store_true", default=True,
                   help="Verify manifest.outputs sha256 against actual files (default).")
    p.add_argument("--no-check-hashes", dest="check_hashes",
                   action="store_false",
                   help="Skip manifest sha256 verification.")
    p.add_argument("--strict", action="store_true",
                   help="Treat warnings (count drift, missing fields) as errors.")
    return p.parse_args(argv)


def _parse_gmnotes_id(obj: dict, location: str) -> str | None:
    """Best-effort GMNotes id parse for verification (returns None on failure)."""
    raw = obj.get("GMNotes", "")
    if isinstance(raw, dict):
        gm = raw
    elif isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            gm = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        return None
    val = gm.get("id")
    return str(val) if val is not None else None


def _coerce_int_strict(value):
    """Coerce CustomDeck NumWidth/NumHeight to int, rejecting truncation.

    Mirrors extract-korean-atlases._coerce_int but returns None on failure
    (verify accumulates errors rather than exiting). Distinguishing this
    from a plain int(value) prevents non-integral floats (e.g. 3.9) from
    silently passing comparison against an integer extracted value (e.g. 3).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        coerced = int(value)
        if float(coerced) != value:
            return None
        return coerced
    return None


# Duplicated from extract-korean-atlases.py — keep in sync.
def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_outputs(input_dir: Path) -> tuple[dict, dict, dict]:
    """Load (cards_doc, decks_doc, manifest_doc). Exits 47 if any missing."""
    cards_path = input_dir / "atlas-data.cards.json"
    decks_path = input_dir / "atlas-data.decks.json"
    manifest_path = input_dir / "manifest.json"

    for p in (cards_path, decks_path, manifest_path):
        if not p.exists():
            print(f"Missing output file: {p}", file=sys.stderr)
            sys.exit(47)

    with cards_path.open(encoding="utf-8") as fh:
        cards_doc = json.load(fh)
    with decks_path.open(encoding="utf-8") as fh:
        decks_doc = json.load(fh)
    with manifest_path.open(encoding="utf-8") as fh:
        manifest_doc = json.load(fh)

    if "cards" not in cards_doc:
        print(f"atlas-data.cards.json missing 'cards' field", file=sys.stderr)
        sys.exit(47)
    if "decks" not in decks_doc:
        print(f"atlas-data.decks.json missing 'decks' field", file=sys.stderr)
        sys.exit(47)
    if "outputs" not in manifest_doc:
        print(f"manifest.json missing 'outputs' field", file=sys.stderr)
        sys.exit(47)

    return cards_doc, decks_doc, manifest_doc


def verify_hashes(input_dir: Path, manifest: dict) -> list[str]:
    """Verify manifest.outputs.<name>.sha256 against actual file contents."""
    errors: list[str] = []
    for name, entry in manifest.get("outputs", {}).items():
        path = input_dir / name
        if not path.exists():
            errors.append(f"manifest declared output missing on disk: {name}")
            continue
        declared = entry.get("sha256")
        actual = compute_sha256(path)
        if declared != actual:
            errors.append(
                f"sha256 mismatch: {name} declared={declared!r} actual={actual!r}"
            )
    return errors


def verify_cards_vs_decomposed(
    cards: list[dict],
    decomposed_root: Path,
) -> list[str]:
    """Bidirectional check between cards[] and the decomposed filesystem."""
    errors: list[str] = []

    # Forward: each card -> decomposed file matches.
    seen_paths: set[Path] = set()
    repo_root_for_paths = decomposed_root.parent  # SCED-downloads
    for card in cards:
        rel = card.get("decomposed_path", "")
        path = (repo_root_for_paths / rel).resolve()
        if not path.exists():
            errors.append(
                f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                f"decomposed_path missing on disk: {rel}"
            )
            continue
        seen_paths.add(path)
        with path.open(encoding="utf-8") as fh:
            try:
                obj = json.load(fh)
            except json.JSONDecodeError:
                errors.append(
                    f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                    f"decomposed file is not valid JSON: {rel}"
                )
                continue

        gm_id = _parse_gmnotes_id(obj, str(path))
        if gm_id != card.get("arkham_id"):
            errors.append(
                f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                f"field=arkham_id expected={card.get('arkham_id')!r} actual={gm_id!r}"
            )

        actual_guid = str(obj.get("GUID", ""))
        if actual_guid != card.get("guid"):
            errors.append(
                f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                f"field=guid expected={card.get('guid')!r} actual={actual_guid!r}"
            )

        custom_deck = obj.get("CustomDeck", {})
        if not custom_deck:
            errors.append(
                f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                f"field=CustomDeck expected=non-empty actual=empty"
            )
            continue
        deck_id = list(custom_deck.keys())[0]
        if deck_id != card.get("deck_id"):
            errors.append(
                f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                f"field=deck_id expected={card.get('deck_id')!r} actual={deck_id!r}"
            )
        deck_data = custom_deck[deck_id]

        for field, expected, actual_raw in (
            ("face_url", card.get("face_url"), deck_data.get("FaceURL", "")),
            ("back_url", card.get("back_url"), deck_data.get("BackURL", "")),
            ("num_width", card.get("num_width"), deck_data.get("NumWidth", 0)),
            ("num_height", card.get("num_height"), deck_data.get("NumHeight", 0)),
        ):
            if field in ("num_width", "num_height"):
                # Strict coerce: reject non-integral floats (matches extract).
                actual_cmp = _coerce_int_strict(actual_raw)
                if actual_cmp != expected:
                    errors.append(
                        f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                        f"field={field} expected={expected!r} actual={actual_raw!r}"
                    )
            else:
                if actual_raw != expected:
                    errors.append(
                        f"card arkham_id={card.get('arkham_id')} guid={card.get('guid')} "
                        f"field={field} expected={expected!r} actual={actual_raw!r}"
                    )

    # Reverse: every decomposed *.json under the two subpaths must appear in cards[].
    player_root = decomposed_root / PLAYER_CARDS_SUBPATH
    barkham_root = decomposed_root / BARKHAM_SUBPATH

    expected_paths: set[Path] = set()
    if player_root.exists():
        for p in player_root.rglob("*.json"):
            expected_paths.add(p.resolve())
    if barkham_root.exists():
        for p in barkham_root.glob("Card.bk*.json"):
            expected_paths.add(p.resolve())

    missing_in_cards = expected_paths - seen_paths
    extra_in_cards = seen_paths - expected_paths

    for p in sorted(missing_in_cards):
        try:
            rel = p.relative_to(decomposed_root.parent)
        except ValueError:
            rel = p
        errors.append(
            f"decomposed file present on disk but missing from cards[]: {rel}"
        )
    for p in sorted(extra_in_cards):
        try:
            rel = p.relative_to(decomposed_root.parent)
        except ValueError:
            rel = p
        errors.append(
            f"cards[] references path not under expected fork-only subpaths: {rel}"
        )

    return errors


def verify_decks_cross_reference(
    cards: list[dict],
    decks: dict[str, dict],
) -> list[str]:
    """Validate cards.deck_composite ⊆ decks and membership consistency.

    Decks dict is keyed by composite (deck_id, face_url) — see
    extract-korean-atlases.build_decks_dict for rationale.
    """
    errors: list[str] = []

    grouped: dict[str, list[dict]] = {}
    for card in cards:
        composite = card.get("deck_composite")
        if not composite:
            errors.append(
                f"deck cross-ref: card arkham_id={card.get('arkham_id')} "
                f"guid={card.get('guid')} missing deck_composite field"
            )
            continue
        grouped.setdefault(composite, []).append(card)

    for composite, members in grouped.items():
        if composite not in decks:
            errors.append(
                f"deck cross-ref: composite={composite} present in cards[] but missing from decks{{}}"
            )
            continue
        deck = decks[composite]
        if deck.get("card_count") != len(members):
            errors.append(
                f"deck cross-ref: composite={composite} card_count "
                f"declared={deck.get('card_count')} actual={len(members)}"
            )
        expected_ids = sorted({m["arkham_id"] for m in members})
        expected_guids = sorted({m["guid"] for m in members})
        if list(deck.get("card_ids", [])) != expected_ids:
            errors.append(
                f"deck cross-ref: composite={composite} card_ids declared={deck.get('card_ids')!r} "
                f"actual={expected_ids!r}"
            )
        if list(deck.get("card_guids", [])) != expected_guids:
            errors.append(
                f"deck cross-ref: composite={composite} card_guids declared={deck.get('card_guids')!r} "
                f"actual={expected_guids!r}"
            )
        # Every card in this group must share the deck-level face/grid.
        # back_url is NOT enforced (UniqueBack cards on a shared face atlas
        # legitimately diverge — see extract-korean-atlases.build_decks_dict).
        for m in members:
            for field, deck_field in (
                ("face_url", "face_url"),
                ("num_width", "num_width"),
                ("num_height", "num_height"),
            ):
                if m[field] != deck[deck_field]:
                    errors.append(
                        f"deck cross-ref: composite={composite} member guid={m['guid']} "
                        f"field={field} card={m[field]!r} deck={deck[deck_field]!r}"
                    )
        # back_urls_distinct must match actual distinct backs in members.
        actual_distinct_backs = len({m["back_url"] for m in members})
        if deck.get("back_urls_distinct") != actual_distinct_backs:
            errors.append(
                f"deck cross-ref: composite={composite} back_urls_distinct "
                f"declared={deck.get('back_urls_distinct')} actual={actual_distinct_backs}"
            )
        # Member's deck_id must match the deck entry's deck_id (composite split sanity).
        for m in members:
            if m["deck_id"] != deck.get("deck_id"):
                errors.append(
                    f"deck cross-ref: composite={composite} member guid={m['guid']} "
                    f"field=deck_id card={m['deck_id']!r} deck={deck.get('deck_id')!r}"
                )

    for composite in decks:
        if composite not in grouped:
            errors.append(
                f"deck cross-ref: composite={composite} present in decks{{}} but no member cards"
            )

    return errors


def main(argv=None) -> int:
    args = parse_args(argv)

    cards_doc, decks_doc, manifest_doc = load_outputs(args.input_dir)
    cards: list[dict] = cards_doc.get("cards", [])
    decks: dict[str, dict] = decks_doc.get("decks", {})

    print(
        f"Loaded {len(cards)} cards across {len(decks)} decks from {args.input_dir}."
    )

    # Hash check first — catch corrupted writes before deep verification.
    if args.check_hashes:
        hash_errors = verify_hashes(args.input_dir, manifest_doc)
        if hash_errors:
            for e in hash_errors:
                print(f"[hash] {e}", file=sys.stderr)
            return 42

    cards_errors = verify_cards_vs_decomposed(cards, args.decomposed_root)
    if cards_errors:
        for e in cards_errors:
            print(f"[cards] {e}", file=sys.stderr)
        return 45

    decks_errors = verify_decks_cross_reference(cards, decks)
    if decks_errors:
        for e in decks_errors:
            print(f"[decks] {e}", file=sys.stderr)
        return 46

    # Optional drift warnings — manifest-declared totals vs actual.
    warnings: list[str] = []
    declared_card_count = (
        manifest_doc.get("outputs", {})
        .get("atlas-data.cards.json", {})
        .get("card_count")
    )
    if declared_card_count is not None and declared_card_count != len(cards):
        warnings.append(
            f"manifest card_count={declared_card_count} != actual cards={len(cards)}"
        )
    declared_deck_count = (
        manifest_doc.get("outputs", {})
        .get("atlas-data.decks.json", {})
        .get("deck_count")
    )
    if declared_deck_count is not None and declared_deck_count != len(decks):
        warnings.append(
            f"manifest deck_count={declared_deck_count} != actual decks={len(decks)}"
        )

    for w in warnings:
        print(f"[warn] {w}", file=sys.stderr)

    print(
        f"PASS: cards ↔ decomposed bidirectional, decks cross-ref, "
        f"sha256={'checked' if args.check_hashes else 'skipped'}."
    )

    # --strict promotes warnings (count drift, missing fields) to a non-zero
    # exit. Default mode treats them as informational only.
    if warnings and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
