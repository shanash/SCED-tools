#!/usr/bin/env python3
"""
Build per-card candidates index from historical langpack versions + decomposed en source.

Usage:
  build-candidates-index.py
    [--sources-dir PATH] [--decomposed-root PATH] [--output PATH]
    [--url-validation PATH] [--emit-validation-input PATH]

Exit codes:
  0 success
  1 warnings
  10 GMNotes invariant violation
  11 target_deck_key mismatch within fan-out
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
DEFAULT_SOURCES_DIR = SCRIPTS_DIR / "output" / "korean-image-review" / "sources"
DEFAULT_DECOMPOSED_ROOT = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Player Cards"
    / "Korean-PlayerCards.KoreanI"
)
DEFAULT_OUTPUT = SCRIPTS_DIR / "output" / "korean-image-review" / "candidates_index.json"
DEFAULT_EN_ROOT = (
    REPO_ROOT
    / "SCED"
    / "objects"
    / "AllPlayerCards.15bb07"
)
VERSION_LABELS = ["v0", "v1", "v2"]


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Build per-card candidates index from langpack versions and decomposed source."
    )
    p.add_argument("--sources-dir", type=Path, default=DEFAULT_SOURCES_DIR)
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--en-root", type=Path, default=DEFAULT_EN_ROOT,
                   help="Path to AllPlayerCards.15bb07 English source directory.")
    p.add_argument("--url-validation", type=Path, default=None,
                   help="Path to URL validation JSON to annotate url_status fields.")
    p.add_argument("--emit-validation-input", type=Path, default=None,
                   help="Write validation input JSON for validate-korean-overrides.py.")
    return p.parse_args(argv)


def _sha1_prefix(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def _parse_gmnotes_id(obj: dict, location: str) -> str:
    """Parse GMNotes (string or dict) and return id value."""
    raw = obj.get("GMNotes", "")
    if isinstance(raw, dict):
        gm = raw
    elif isinstance(raw, str):
        if not raw.strip():
            print(f"GMNotes invariant violation: empty GMNotes at {location}", file=sys.stderr)
            sys.exit(10)
        try:
            gm = json.loads(raw)
        except json.JSONDecodeError:
            print(f"GMNotes invariant violation: non-JSON GMNotes at {location}", file=sys.stderr)
            sys.exit(10)
    else:
        print(f"GMNotes invariant violation: unexpected type at {location}", file=sys.stderr)
        sys.exit(10)
    if "id" not in gm:
        print(f"GMNotes invariant violation: missing 'id' key at {location}", file=sys.stderr)
        sys.exit(10)
    return str(gm["id"])


def _extract_card_from_langpack_obj(obj: dict, location: str) -> dict | None:
    """Extract card data from a langpack ContainedObject."""
    arkham_id = _parse_gmnotes_id(obj, location)
    custom_deck = obj.get("CustomDeck", {})
    if not custom_deck:
        return None
    deck_key = list(custom_deck.keys())[0]
    deck_data = custom_deck[deck_key]
    return {
        "arkham_id": arkham_id,
        "nickname": obj.get("Nickname", ""),
        "face_url": deck_data.get("FaceURL", ""),
        "back_url": deck_data.get("BackURL", ""),
        "num_width": deck_data.get("NumWidth", 0),
        "num_height": deck_data.get("NumHeight", 0),
        "deck_key": deck_key,
        "card_id": obj.get("CardID"),
        "guid": obj.get("GUID", ""),
    }


def load_version(sources_dir: Path, label: str) -> dict[str, dict]:
    """Load a langpack version and return dict[arkham_id, card_data]."""
    path = sources_dir / f"{label}.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    contained = data.get("ContainedObjects", [])
    result: dict[str, dict] = {}
    for i, obj in enumerate(contained):
        card = _extract_card_from_langpack_obj(obj, f"{path}[{i}]")
        if card is None:
            continue
        arkham_id = card["arkham_id"]
        # Keep last occurrence (consistent with "last" dedup strategy)
        result[arkham_id] = card
    return result


def load_decomposed(decomposed_root: Path) -> tuple[dict[str, dict], dict[str, list[Path]], list[dict]]:
    """
    Load all decomposed JSON files.
    Returns (cards_by_id, paths_by_id, deckkey_fanout_warnings).

    Fan-out (multiple target files per arkham_id) is allowed and expected:
    e.g., id=01004 has both AgnesBaker.25e2db.json (deck_key=5356, main investigator deck)
    and AgnesBaker.25e2db/AgnesBaker.6797bb.json (deck_key=5874, mini/signature variant).
    These are physically different cards on different sheets but share the arkham_id.

    When fan-out files have different deck_keys, this is recorded as a warning,
    not a fatal error, because:
    - build-korean-overrides.py walks each target file by id and uses each target's
      own deck_key for writing (build-korean-overrides.py:218-220, 250-251).
    - apply-grid-dims.py similarly reads each target's own deck_key (apply-grid-dims.py:142).
    - The cards_by_id["target_deck_key"] is used only for the synthetic source wrapper
      and gallery UI display; build-korean-overrides ignores the source deck_key for writes.
    """
    cards_by_id: dict[str, dict] = {}
    paths_by_id: dict[str, list[Path]] = {}
    deckkey_fanout_warnings: list[dict] = []

    # Sort top-level files first so first-wins dedup picks main investigator
    # card over mini-deck variants in fan-out subdirectories.
    for path in sorted(
        decomposed_root.rglob("*.json"),
        key=lambda p: (len(p.relative_to(decomposed_root).parts), p),
    ):
        with path.open(encoding="utf-8") as fh:
            try:
                obj = json.load(fh)
            except json.JSONDecodeError:
                print(f"Invalid JSON in decomposed file: {path}", file=sys.stderr)
                continue

        arkham_id = _parse_gmnotes_id(obj, str(path))
        custom_deck = obj.get("CustomDeck", {})
        if not custom_deck:
            continue
        deck_key = list(custom_deck.keys())[0]
        deck_data = custom_deck[deck_key]

        card = {
            "arkham_id": arkham_id,
            "nickname": obj.get("Nickname", ""),
            "face_url": deck_data.get("FaceURL", ""),
            "back_url": deck_data.get("BackURL", ""),
            "num_width": deck_data.get("NumWidth", 0),
            "num_height": deck_data.get("NumHeight", 0),
            "deck_key": deck_key,
            "card_id": obj.get("CardID"),
            "guid": obj.get("GUID", ""),
        }

        paths_by_id.setdefault(arkham_id, []).append(path)

        if arkham_id in cards_by_id:
            existing = cards_by_id[arkham_id]
            if existing["deck_key"] != deck_key:
                deckkey_fanout_warnings.append({
                    "arkham_id": arkham_id,
                    "first_deck_key": existing["deck_key"],
                    "first_path": str(paths_by_id[arkham_id][0]),
                    "other_deck_key": deck_key,
                    "other_path": str(path),
                })
        else:
            cards_by_id[arkham_id] = card

    return cards_by_id, paths_by_id, deckkey_fanout_warnings


def load_en_source(en_root: Path) -> dict[str, dict]:
    """
    Load AllPlayerCards.15bb07 English source.
    Returns dict[arkham_id → card_dict].

    Reads only top-level *.json (no rglob) to exclude investigator mini-deck
    subdirectory files that reference Korean ugc IDs.

    GMNotes resolution per file:
      1. obj["GMNotes"] not None → parse as string or use directly as dict
      2. else → read sibling .gmnotes sidecar file
    """
    if not en_root.exists():
        print(f"Warning: --en-root {en_root} does not exist; en candidates will be unavailable.",
              file=sys.stderr)
        return {}

    result: dict[str, dict] = {}
    for path in sorted(en_root.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as fh:
                obj = json.load(fh)
        except json.JSONDecodeError:
            print(f"Invalid JSON in en source: {path}", file=sys.stderr)
            continue

        gm_inline = obj.get("GMNotes")
        if gm_inline is not None:
            if isinstance(gm_inline, dict):
                gm = gm_inline
            else:
                try:
                    gm = json.loads(gm_inline)
                except json.JSONDecodeError:
                    continue
        else:
            sidecar = path.with_suffix(".gmnotes")
            if not sidecar.exists():
                continue
            try:
                with sidecar.open(encoding="utf-8") as fh:
                    gm = json.load(fh)
            except json.JSONDecodeError:
                continue

        arkham_id = gm.get("id")
        if not arkham_id:
            continue
        arkham_id = str(arkham_id)

        custom_deck = obj.get("CustomDeck", {})
        if not custom_deck:
            continue
        deck_key = list(custom_deck.keys())[0]
        deck_data = custom_deck[deck_key]

        result[arkham_id] = {
            "arkham_id": arkham_id,
            "nickname": obj.get("Nickname", ""),
            "face_url": deck_data.get("FaceURL", ""),
            "back_url": deck_data.get("BackURL", ""),
            "num_width": deck_data.get("NumWidth", 0),
            "num_height": deck_data.get("NumHeight", 0),
            "deck_key": deck_key,
            "card_id": obj.get("CardID"),
            "guid": obj.get("GUID", ""),
        }
    return result


def build_candidate_entry(version: str, card_data: dict | None) -> dict:
    """Build a candidate entry for a given version."""
    if card_data is None:
        return {"version": version, "available": False}
    return {
        "version": version,
        "available": True,
        "face_url": card_data["face_url"],
        "back_url": card_data["back_url"],
        "num_width": card_data["num_width"],
        "num_height": card_data["num_height"],
        "deck_key": card_data["deck_key"],
        "card_id": card_data["card_id"],
        "url_status": None,
    }


def annotate_url_status(cards: list[dict], validation_path: Path) -> None:
    """Annotate url_status fields from a URL validation JSON."""
    with validation_path.open(encoding="utf-8") as fh:
        validation = json.load(fh)

    # Build lookup: face_url -> {face: {status, checked_at}, back: {...}}
    url_map: dict[str, dict] = {}
    for entry in validation.get("results", []):
        face_url = entry.get("face_url", "")
        if face_url:
            url_map[face_url] = {
                "face": {
                    "status": entry.get("face_status", "unknown"),
                    "checked_at": entry.get("checked_at", ""),
                },
                "back": {
                    "status": entry.get("back_status", "unknown"),
                    "checked_at": entry.get("checked_at", ""),
                },
            }

    for card in cards:
        for cand in card.get("candidates", []):
            if not cand.get("available"):
                continue
            face_url = cand.get("face_url", "")
            if face_url in url_map:
                cand["url_status"] = url_map[face_url]


def main(argv=None):
    args = parse_args(argv)

    # Load all version data
    versions: dict[str, dict[str, dict]] = {}
    source_meta: dict[str, dict] = {}
    for label in VERSION_LABELS:
        path = args.sources_dir / f"{label}.json"
        versions[label] = load_version(args.sources_dir, label)
        source_meta[label] = {
            "path": str(path),
            "card_count": len(versions[label]),
            "available": path.exists(),
        }

    # Load decomposed (ko source)
    ko_cards, paths_by_id, deckkey_fanout_warnings = load_decomposed(args.decomposed_root)
    source_meta["ko"] = {
        "path": str(args.decomposed_root),
        "card_count": len(ko_cards),
    }

    # Load en source (AllPlayerCards)
    en_cards = load_en_source(args.en_root)
    source_meta["en"] = {
        "path": str(args.en_root),
        "card_count": len(en_cards),
    }

    warnings = False
    cards_output = []

    if deckkey_fanout_warnings:
        print(
            f"Note: {len(deckkey_fanout_warnings)} arkham_id(s) have fan-out target files "
            f"with differing deck_keys (recorded in summary.deckkey_fanout_warnings). "
            f"This is normal for cards with multiple physical variants (e.g., main + signature).",
            file=sys.stderr,
        )

    for arkham_id, ko_card in sorted(ko_cards.items()):
        en_card = en_cards.get(arkham_id)
        candidates = []

        # en candidate (AllPlayerCards — true English original)
        candidates.append(build_candidate_entry("en", en_card))

        # ko candidate (KoreanI decomposed — Korean mod base)
        candidates.append(build_candidate_entry("ko", ko_card))

        # version candidates (CDN)
        for label in VERSION_LABELS:
            ver_card = versions[label].get(arkham_id)
            candidates.append(build_candidate_entry(label, ver_card))

        # Compute unique face URLs
        face_urls = [
            c["face_url"]
            for c in candidates
            if c.get("available") and c.get("face_url")
        ]
        unique_face_urls = list(dict.fromkeys(face_urls))  # preserve order, deduplicate
        unique_face_url_count = len(unique_face_urls)
        diversity_score = unique_face_url_count

        # sheet_cohort_v2
        v2_card = versions["v2"].get(arkham_id)
        if v2_card and v2_card.get("face_url"):
            sheet_cohort_v2 = f"{ko_card['deck_key']}|{_sha1_prefix(v2_card['face_url'])}"
        else:
            sheet_cohort_v2 = None

        # target paths
        target_paths = [str(p) for p in paths_by_id.get(arkham_id, [])]

        # nickname from v2 if available, else en
        v2_card_data = versions["v2"].get(arkham_id)
        nickname_ko = v2_card_data["nickname"] if v2_card_data else ""

        card_entry = {
            "arkham_id": arkham_id,
            "nickname_en": en_card["nickname"] if en_card else ko_card["nickname"],
            "nickname_v2_ko": nickname_ko,
            "target_paths": target_paths,
            "target_deck_key": ko_card["deck_key"],
            "candidates": candidates,
            "unique_face_url_count": unique_face_url_count,
            "diversity_score": diversity_score,
            "sheet_cohort_v2": sheet_cohort_v2,
        }
        cards_output.append(card_entry)

    # Annotate URL status if provided
    if args.url_validation and args.url_validation.exists():
        annotate_url_status(cards_output, args.url_validation)

    # Emit validation input if requested
    if args.emit_validation_input:
        seen_pairs: set[tuple[str, str]] = set()
        validation_objects = []
        for card in cards_output:
            target_deck_key = card["target_deck_key"]
            en_cand = card["candidates"][0]  # en is always first
            card_id_source = en_cand if en_cand.get("available") else next(
                (c for c in card["candidates"] if c.get("available")), en_cand
            )
            for cand in card["candidates"]:
                if not cand.get("available"):
                    continue
                pair = (cand["face_url"], cand["back_url"])
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                validation_objects.append({
                    "GMNotes": json.dumps({"id": card["arkham_id"]}),
                    "CustomDeck": {
                        target_deck_key: {
                            "FaceURL": cand["face_url"],
                            "BackURL": cand["back_url"],
                            "NumWidth": cand["num_width"],
                            "NumHeight": cand["num_height"],
                        }
                    },
                    "CardID": card_id_source.get("card_id"),
                    "GUID": "000000",
                })

        args.emit_validation_input.parent.mkdir(parents=True, exist_ok=True)
        args.emit_validation_input.write_text(
            json.dumps({"ContainedObjects": validation_objects}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Validation input written to {args.emit_validation_input} ({len(validation_objects)} pairs)")

    # Build summary
    by_diversity: dict[str, int] = {}
    for card in cards_output:
        key = str(card["diversity_score"])
        by_diversity[key] = by_diversity.get(key, 0) + 1

    def _coverage(label: str) -> int:
        return sum(1 for c in cards_output if any(
            x["version"] == label and x.get("available") for x in c["candidates"]
        ))

    summary = {
        "total_cards": len(cards_output),
        "by_diversity": by_diversity,
        "en_coverage": _coverage("en"),
        "ko_coverage": _coverage("ko"),
        "v0_coverage": _coverage("v0"),
        "v1_coverage": _coverage("v1"),
        "v2_coverage": _coverage("v2"),
        "deckkey_fanout_warnings": deckkey_fanout_warnings,
    }

    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": source_meta,
        "cards": cards_output,
        "summary": summary,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(args.output)
    print(
        f"candidates_index.json written to {args.output} "
        f"({len(cards_output)} cards, diversity: {by_diversity})"
    )

    if warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
