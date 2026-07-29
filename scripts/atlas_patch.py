#!/usr/bin/env python3
"""Shared repoint primitives for the Korean PDF-atlas tooling.

This is an importable module (underscore name on purpose) holding the handful of
in-memory primitives that ``apply-atlas-faceurls.py`` and
``create-parallel-investigators.py`` both need, so the FaceURL-set / grid +
CardID/deck_key rewrite / post-apply validation logic lives in one place instead
of in three divergent copies (design §4, §5.4).

Everything here is pure / in-memory: callers do the disk I/O via
``sced_io.atomic_write_json_batch``. Functions raise ``ValueError`` on a
programmer error (an input that violates a precondition the caller is supposed
to have established) and return value lists for the validation gate; nothing in
this module ever calls ``sys.exit`` — mapping results to process exit codes is
the CLI scripts' job.

The TTS CardID invariant this module enforces is universal and load-bearing
(verified): for every card ``cell_index = CardID % 100`` and
``deck_key = CardID // 100``, where ``deck_key`` is the single ``CustomDeck``
key — i.e. ``CardID == int(deck_key) * 100 + cell_index``. Moving a card onto a
new packed atlas is therefore never a FaceURL/dims edit alone: it rewrites
``CardID`` and the ``CustomDeck`` key in lockstep.

Exit codes: none — this module never exits (see module docstring).
"""
from __future__ import annotations

import json
import re
import sys

# Atlas/back image URL shape (copied verbatim from
# apply-korean-image-decisions.py so the two gates agree byte-for-byte).
URL_REGEX = re.compile(r"^https://[^\s]+\.(png|jpg|jpeg)(\?[^\s]*)?$", re.IGNORECASE)

# Steam UGC content URLs carry no file extension (they end in a content hash and
# a trailing slash) and therefore do NOT match URL_REGEX, yet they are the
# canonical, working image host across this codebase (build-korean-card-index.py
# classifies them as the "Steam" source) and are exactly the BackURLs Q5
# preserves verbatim. The gate must accept them so preserving a working Steam
# back is not flagged as an error. The atlas FaceURLs the repoint tools WRITE are
# always R2 .png URLs, so URL_REGEX stays load-bearing for the written side.
STEAM_HOST_FRAGMENT = "steamusercontent-a.akamaihd.net"


def _url_is_acceptable(url: str) -> bool:
    """True if `url` is a valid image URL the gate should accept.

    Accepts either the R2/extension form (URL_REGEX — the only shape the repoint
    tools ever WRITE) or a recognized https Steam UGC content URL (the
    extension-less working host this codebase already treats as valid and which
    Q5 preserves verbatim for card backs).
    """
    if URL_REGEX.match(url):
        return True
    return url.startswith("https://") and STEAM_HOST_FRAGMENT in url

# Row-level anomaly codes — must mirror build-korean-card-index.py exactly so an
# anomaly-set comparison between this gate and the regenerated index is valid.
ANOMALY_CARD_ID_INVALID = "card_id_invalid"
ANOMALY_CELL_OUT_OF_BOUNDS = "cell_out_of_bounds"
ANOMALY_DECKKEY_MISMATCH = "deckkey_mismatch"
ANOMALY_MULTI_DECK = "multi_deck"


def parse_gmnotes_id(obj: dict, location: str) -> str:
    """Parse GMNotes (dict or escaped-JSON string) and return the id value.

    Cards carry the arkham_id inside ``GMNotes``, which is EITHER a dict OR an
    escaped-JSON string. Both forms are accepted; an ``"id"`` key is required and
    its value is returned coerced to ``str``. Reproduces the invariant pattern
    from build-korean-card-index.py / build-candidates-index.py.

    Raises ValueError on a missing/empty/non-JSON GMNotes or a missing ``id``;
    ``location`` is woven into the message so the caller can map it to its own
    exit code rather than crashing inside the module.
    """
    raw = obj.get("GMNotes", "")
    if isinstance(raw, dict):
        gm = raw
    elif isinstance(raw, str):
        if not raw.strip():
            raise ValueError(f"GMNotes invariant violation: empty GMNotes at {location}")
        try:
            gm = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"GMNotes invariant violation: non-JSON GMNotes at {location}"
            ) from exc
    else:
        raise ValueError(
            f"GMNotes invariant violation: unexpected type at {location}"
        )
    if not isinstance(gm, dict) or "id" not in gm:
        raise ValueError(
            f"GMNotes invariant violation: missing 'id' key at {location}"
        )
    return str(gm["id"])


def single_deck_key(card_obj: dict) -> str:
    """Return THIS card's authoritative CustomDeck key.

    The decomposed model gives every card its own ``CustomDeck``; the normal case
    is exactly one entry. If several are present (an anomaly), prefer the entry
    whose key == ``str(CardID // 100)`` (the same disambiguation
    build-korean-card-index.py uses), else fall back to the first key.

    Raises ValueError on an empty CustomDeck — a card with no deck cannot be
    repointed and the caller should never have selected it.
    """
    custom_deck = card_obj.get("CustomDeck", {})
    if not custom_deck:
        raise ValueError("card has an empty CustomDeck")
    keys = list(custom_deck)
    if len(keys) > 1:
        card_id = card_obj.get("CardID")
        if isinstance(card_id, int) and not isinstance(card_id, bool):
            expected = str(card_id // 100)
            if expected in custom_deck:
                return expected
    return keys[0]


def set_face_url(card_obj: dict, deck_key: str, face_url: str) -> None:
    """Set ``CustomDeck[deck_key]["FaceURL"]`` on this card (in place).

    Raises ValueError if ``deck_key`` is absent — the caller is expected to pass
    the card's current deck key (see ``single_deck_key``).
    """
    custom_deck = card_obj.get("CustomDeck", {})
    if deck_key not in custom_deck:
        raise ValueError(f"deck_key {deck_key!r} not present in CustomDeck")
    custom_deck[deck_key]["FaceURL"] = face_url


def repoint_to_packed_cell(
    card_obj: dict,
    target_deck_key: str,
    target_cell_index: int,
    target_num_width: int,
    target_num_height: int,
    face_url: str,
) -> bool:
    """Move this card's single CustomDeck entry onto a packed atlas cell.

    Takes the card's existing single CustomDeck entry (whatever its current key),
    sets its FaceURL + NumWidth/NumHeight, moves it under ``target_deck_key``
    (copy the dict to the new key, delete the stale key if it differs), and sets
    ``card_obj["CardID"] = int(target_deck_key) * 100 + target_cell_index``.

    Idempotent: if the card is already at the target (deck_key, CardID, grid AND
    FaceURL) nothing is written. Returns ``True`` if the object was modified,
    ``False`` if it was already at the target.

    Because each decomposed file carries its OWN copy of its CustomDeck, deleting
    the stale key touches only THIS card's file — co-tenant cards living in other
    files are unaffected (design §3.6). A deck is never renamed globally.
    """
    target_card_id = int(target_deck_key) * 100 + target_cell_index
    current_key = single_deck_key(card_obj)
    entry = card_obj["CustomDeck"][current_key]

    already_at_target = (
        current_key == target_deck_key
        and card_obj.get("CardID") == target_card_id
        and entry.get("NumWidth") == target_num_width
        and entry.get("NumHeight") == target_num_height
        and entry.get("FaceURL") == face_url
    )
    if already_at_target:
        return False

    entry["FaceURL"] = face_url
    entry["NumWidth"] = target_num_width
    entry["NumHeight"] = target_num_height

    if current_key != target_deck_key:
        # Re-home the entry under the new packed key, dropping the stale key.
        card_obj["CustomDeck"][target_deck_key] = entry
        del card_obj["CustomDeck"][current_key]

    card_obj["CardID"] = target_card_id
    return True


def _coerce_int_or_none(value):
    """Soft-coerce to int the way build-korean-card-index.py treats CardID/dims.

    bool / None / non-integral float / non-numeric all return None (keep-and-flag),
    so the gate flags an anomaly rather than crashing. int / integral-float pass.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        coerced = int(value)
        return coerced if float(coerced) == value else None
    return None


def derive_anomalies(card_obj: dict) -> set[str]:
    """Return the set of anomaly codes for ONE card, mirroring the index.

    Mirrors build-korean-card-index.py EXACTLY:
      cell = CardID % 100;  capacity = NumWidth * NumHeight;
      expected_key = str(CardID // 100).
    Codes: card_id_invalid, multi_deck, deckkey_mismatch, cell_out_of_bounds.

    Used by callers to assert that an edit introduces no NEW anomaly versus the
    pre-edit baseline. Returns a set (the index emits one entry per code).
    """
    codes: set[str] = set()
    custom_deck = card_obj.get("CustomDeck", {})
    if not custom_deck:
        # Mirrors the index's hard exit 43 case; surface as an explicit error
        # instead so the caller decides. (A repoint target should never be here.)
        raise ValueError("card has an empty CustomDeck")

    card_id = _coerce_int_or_none(card_obj.get("CardID"))
    if card_id is None:
        codes.add(ANOMALY_CARD_ID_INVALID)

    deck_key = next(iter(custom_deck))
    if len(custom_deck) != 1:
        codes.add(ANOMALY_MULTI_DECK)
        if card_id is not None:
            expected_key = str(card_id // 100)
            if expected_key in custom_deck:
                deck_key = expected_key
    entry = custom_deck[deck_key]

    if card_id is not None:
        if str(card_id // 100) != deck_key:
            codes.add(ANOMALY_DECKKEY_MISMATCH)
        nw = _coerce_int_or_none(entry.get("NumWidth", 0)) or 0
        nh = _coerce_int_or_none(entry.get("NumHeight", 0)) or 0
        if (card_id % 100) >= nw * nh:
            codes.add(ANOMALY_CELL_OUT_OF_BOUNDS)

    return codes


def validate_gate(card_objs_by_path) -> list[str]:
    """Post-apply validation gate over a set of edited cards (design §4).

    ``card_objs_by_path`` maps a label (e.g. a Path or stem) -> card dict.

    Checks, returning a list of human-readable error strings (empty == pass):
      1. URL + dims: every present FaceURL/BackURL is https and matches
         URL_REGEX; NumWidth/NumHeight are ints in [1, 12].
      2. CardID <-> cell integrity: for every card,
         ``CardID == int(deck_key) * 100 + (CardID % 100)`` and
         ``cell_index < NumWidth * NumHeight``. ``deck_key`` is the single
         CustomDeck key, or — when several are present — the one
         == ``str(CardID // 100)``.

    This never raises and never exits; it returns the findings so the caller can
    map a non-empty list to its own non-zero exit code.
    """
    errors: list[str] = []
    for label, card in card_objs_by_path.items():
        custom_deck = card.get("CustomDeck", {})
        if not custom_deck:
            errors.append(f"ValidationError: empty CustomDeck (file={label})")
            continue

        # Pick the authoritative deck key (single, or the CardID-matching one).
        card_id = card.get("CardID")
        deck_key = next(iter(custom_deck))
        if len(custom_deck) > 1:
            if isinstance(card_id, int) and not isinstance(card_id, bool):
                cand = str(card_id // 100)
                if cand in custom_deck:
                    deck_key = cand

        # (1) URL + dims for EVERY deck entry on the card.
        for dk, deck in custom_deck.items():
            for field in ("FaceURL", "BackURL"):
                url = deck.get(field, "")
                if not url:
                    continue
                if url.startswith("http://") or url.startswith("file://"):
                    errors.append(
                        f"ValidationError: {field} uses non-https scheme "
                        f"(file={label} deck={dk} url={url})"
                    )
                    continue
                if not _url_is_acceptable(url):
                    errors.append(
                        f"ValidationError: {field} regex mismatch "
                        f"(file={label} deck={dk} url={url})"
                    )
            nw = deck.get("NumWidth")
            nh = deck.get("NumHeight")
            if not (isinstance(nw, int) and not isinstance(nw, bool) and 1 <= nw <= 12):
                errors.append(
                    f"ValidationError: NumWidth out of range "
                    f"(file={label} deck={dk} value={nw!r})"
                )
            if not (isinstance(nh, int) and not isinstance(nh, bool) and 1 <= nh <= 12):
                errors.append(
                    f"ValidationError: NumHeight out of range "
                    f"(file={label} deck={dk} value={nh!r})"
                )

        # (2) CardID <-> cell integrity on the authoritative deck entry.
        if not (isinstance(card_id, int) and not isinstance(card_id, bool)):
            errors.append(
                f"ValidationError: CardID not an int "
                f"(file={label} value={card_id!r})"
            )
            continue
        cell_index = card_id % 100
        expected_card_id = int(deck_key) * 100 + cell_index
        if card_id != expected_card_id:
            errors.append(
                f"ValidationError: CardID {card_id} != "
                f"int(deck_key)*100+cell {expected_card_id} "
                f"(file={label} deck={deck_key} cell={cell_index})"
            )
        entry = custom_deck[deck_key]
        nw = entry.get("NumWidth")
        nh = entry.get("NumHeight")
        if isinstance(nw, int) and not isinstance(nw, bool) \
                and isinstance(nh, int) and not isinstance(nh, bool):
            if cell_index >= nw * nh:
                errors.append(
                    f"ValidationError: cell_index {cell_index} >= "
                    f"capacity {nw * nh} ({nw}x{nh}) "
                    f"(file={label} deck={deck_key})"
                )

    return errors


# This module is import-only; running it directly is a usage error. Emit a clear
# message and a non-zero status rather than silently doing nothing.
if __name__ == "__main__":
    print(
        "atlas_patch.py is an importable module, not a CLI; "
        "import its primitives from apply-atlas-faceurls.py / "
        "create-parallel-investigators.py.",
        file=sys.stderr,
    )
    sys.exit(2)
