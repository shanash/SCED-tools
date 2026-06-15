#!/usr/bin/env python3
"""
Synthesize a source langpack JSON from user review decisions.

Usage:
  synthesize-source-from-decisions.py
    [--decisions PATH] [--candidates PATH] [--sources-dir PATH]
    [--output PATH] [--text-source untouched|v2|v1|v0|en]
    [--allow-incomplete]

Exit codes:
  0 OK
  1 warnings
  40 candidates_index stale (>24h) OR per-URL checked_at stale (>24h)
  41 chosen candidate URL not validated
  42 undecided > 0 (without --allow-incomplete)
  43 candidate metadata mismatch (tampered)
  44 unsupported choice value
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_DECISIONS = SCRIPTS_DIR / "output" / "korean-image-review" / "review_decisions.json"
DEFAULT_CANDIDATES = SCRIPTS_DIR / "output" / "korean-image-review" / "candidates_index.json"
DEFAULT_SOURCES_DIR = SCRIPTS_DIR / "output" / "korean-image-review" / "sources"
DEFAULT_OUTPUT = SCRIPTS_DIR / "output" / "korean-image-review" / "synthetic-source.json"
MAX_AGE_HOURS = 24
VERSION_LABELS = ["v0", "v1", "v2"]
VALID_CHOICES = {"en", "ko", "v0", "v1", "v2", "skip"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Synthesize source langpack from review decisions."
    )
    p.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    p.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    p.add_argument("--sources-dir", type=Path, default=DEFAULT_SOURCES_DIR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument(
        "--text-source",
        choices=["untouched", "v2", "v1", "v0", "en"],
        default="untouched",
        help="Source for Nickname/Description text fields.",
    )
    p.add_argument("--allow-incomplete", action="store_true",
                   help="Proceed even if some cards have no decision.")
    return p.parse_args(argv)


def _parse_iso(ts: str) -> datetime:
    """Parse ISO8601 timestamp to UTC-aware datetime."""
    # Handle +00:00 suffix
    ts = ts.replace("+00:00", "Z").replace("Z", "+00:00")
    if ts.endswith("+00:00"):
        ts = ts[:-6]
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def _is_stale(ts_str: str | None, max_age_hours: int = MAX_AGE_HOURS) -> bool:
    if not ts_str:
        return True
    try:
        ts = _parse_iso(ts_str)
        return (datetime.now(timezone.utc) - ts) > timedelta(hours=max_age_hours)
    except Exception:
        return True


def load_version_text(sources_dir: Path, label: str) -> dict[str, dict]:
    """Load text fields (Nickname, Description) from a version source."""
    path = sources_dir / f"{label}.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    result: dict[str, dict] = {}
    for obj in data.get("ContainedObjects", []):
        raw = obj.get("GMNotes", "")
        if isinstance(raw, dict):
            gm = raw
        elif raw:
            try:
                gm = json.loads(raw)
            except json.JSONDecodeError:
                continue
        else:
            continue
        arkham_id = gm.get("id")
        if not arkham_id:
            continue
        result[str(arkham_id)] = {
            "Nickname": obj.get("Nickname", ""),
            "Description": obj.get("Description", ""),
        }
    return result


def main(argv=None):
    args = parse_args(argv)

    with args.decisions.open(encoding="utf-8") as fh:
        decisions_data = json.load(fh)
    with args.candidates.open(encoding="utf-8") as fh:
        candidates_data = json.load(fh)

    # Precondition 1: candidates_index freshness
    generated_at = candidates_data.get("generated_at", "")
    if _is_stale(generated_at):
        print(
            f"candidates_index is stale (generated_at={generated_at!r}, must be within {MAX_AGE_HOURS}h). "
            "Re-run build-candidates-index.py with fresh URL validation.",
            file=sys.stderr,
        )
        sys.exit(40)

    # Build candidates lookup
    cards_by_id: dict[str, dict] = {
        c["arkham_id"]: c for c in candidates_data.get("cards", [])
    }
    total_cards = len(cards_by_id)

    decisions_list = decisions_data.get("decisions", [])
    decisions_by_id: dict[str, dict] = {
        d["arkham_id"]: d for d in decisions_list if d.get("choice")
    }

    # Precondition 4: undecided check
    decided_ids = set(decisions_by_id.keys())
    all_ids = set(cards_by_id.keys())
    undecided = all_ids - decided_ids
    if undecided and not args.allow_incomplete:
        print(
            f"{len(undecided)} cards have no decision. Use --allow-incomplete to proceed.",
            file=sys.stderr,
        )
        sys.exit(42)

    # Load text sources if needed
    text_data: dict[str, dict] = {}
    if args.text_source != "untouched":
        # Try to load from specified version with fallback chain
        fallback_chain = []
        if args.text_source == "v2":
            fallback_chain = ["v2", "v1", "v0"]
        elif args.text_source == "v1":
            fallback_chain = ["v1", "v0"]
        elif args.text_source == "v0":
            fallback_chain = ["v0"]
        elif args.text_source == "en":
            fallback_chain = ["en"]

        # Load all needed versions for fallback
        loaded_texts: dict[str, dict[str, dict]] = {}
        for ver in fallback_chain:
            if ver == "en":
                # en text comes from decomposed; not loaded here
                loaded_texts["en"] = {}
            else:
                loaded_texts[ver] = load_version_text(args.sources_dir, ver)

        for arkham_id in all_ids:
            for ver in fallback_chain:
                ver_texts = loaded_texts.get(ver, {})
                if arkham_id in ver_texts and ver_texts[arkham_id].get("Nickname"):
                    text_data[arkham_id] = ver_texts[arkham_id]
                    break

    # Build ContainedObjects
    contained_objects = []
    warnings = []

    for arkham_id, dec in decisions_by_id.items():
        choice = dec.get("choice")

        if choice not in VALID_CHOICES:
            print(f"Unsupported choice value {choice!r} for arkham_id={arkham_id}", file=sys.stderr)
            sys.exit(44)

        if choice == "skip":
            # skip = exclude from output
            continue

        card = cards_by_id.get(arkham_id)
        if card is None:
            warnings.append(f"arkham_id={arkham_id} not found in candidates_index (skipping)")
            continue

        # Find chosen candidate
        cand = next(
            (c for c in card.get("candidates", []) if c.get("version") == choice),
            None,
        )
        if cand is None or not cand.get("available"):
            print(
                f"Chosen candidate {choice!r} not available for arkham_id={arkham_id}",
                file=sys.stderr,
            )
            sys.exit(43)

        # Precondition 2 & 3: URL validation check (only for v0/v1/v2/en non-skip)
        url_status = cand.get("url_status")
        if url_status is None:
            # Not validated at all
            warnings.append(
                f"arkham_id={arkham_id} ({choice}): url_status is null (not validated)"
            )
        else:
            face_st = (url_status.get("face") or {})
            back_st = (url_status.get("back") or {})

            if face_st.get("status") != "ok" or back_st.get("status") != "ok":
                print(
                    f"Chosen candidate for arkham_id={arkham_id} ({choice}) has "
                    f"url_status face={face_st.get('status')!r} back={back_st.get('status')!r}",
                    file=sys.stderr,
                )
                sys.exit(41)

            # Per-URL staleness check
            if _is_stale(face_st.get("checked_at")) or _is_stale(back_st.get("checked_at")):
                print(
                    f"URL validation for arkham_id={arkham_id} ({choice}) is stale "
                    f"(checked_at must be within {MAX_AGE_HOURS}h).",
                    file=sys.stderr,
                )
                sys.exit(40)

        # Verify metadata integrity
        dec_face = dec.get("face_url")
        dec_back = dec.get("back_url")
        cand_face = cand.get("face_url")
        cand_back = cand.get("back_url")

        if dec_face is not None and dec_face != cand_face:
            print(
                f"Candidate metadata mismatch for arkham_id={arkham_id} ({choice}): "
                f"decision face_url={dec_face!r} != candidates face_url={cand_face!r}",
                file=sys.stderr,
            )
            sys.exit(43)
        if dec_back is not None and dec_back != cand_back:
            print(
                f"Candidate metadata mismatch for arkham_id={arkham_id} ({choice}): "
                f"decision back_url={dec_back!r} != candidates back_url={cand_back!r}",
                file=sys.stderr,
            )
            sys.exit(43)

        # Use target_deck_key from candidates_index (NOT chosen.deck_key)
        target_deck_key = card["target_deck_key"]

        obj: dict = {
            "GUID": "000000",
            "CardID": cand.get("card_id"),
            "Name": "Card",
            "Transform": {"rotY": 270, "scaleX": 1, "scaleY": 1, "scaleZ": 1},
            "GMNotes": json.dumps({"id": arkham_id}),
            "CustomDeck": {
                target_deck_key: {
                    "FaceURL": cand_face,
                    "BackURL": cand_back,
                    "NumWidth": cand.get("num_width", dec.get("num_width")),
                    "NumHeight": cand.get("num_height", dec.get("num_height")),
                }
            },
        }

        # Add text fields if text_source != untouched
        if args.text_source != "untouched":
            text = text_data.get(arkham_id, {})
            if text.get("Nickname"):
                obj["Nickname"] = text["Nickname"]
            if text.get("Description"):
                obj["Description"] = text["Description"]

        contained_objects.append(obj)

    for w in warnings:
        print(f"Warning: {w}", file=sys.stderr)

    output = {"ContainedObjects": contained_objects}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(args.output)
    print(
        f"Synthetic source written to {args.output} ({len(contained_objects)} cards)"
    )

    if warnings:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
