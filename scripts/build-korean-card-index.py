#!/usr/bin/env python3
"""
Build an offline per-card image-atlas index for the Korean Player Cards langpack.

Reads the decomposed TTS card JSON directly from
``SCED-downloads/decomposed/language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI/``
(approach A2) and emits, for each of the ~1,860 player cards, which atlas image
(front + back) it uses and where the card sits within the atlas grid
(``cell_index``, ``x``, ``y``). Deliverable A only: no image cropping, no network
access, no atlas-dimension fetching.

Reading decomposed JSON is what makes the index correct: each file isolates
exactly one deck, so no wrong-deck grid can be joined — unlike the packed
catalog, which mis-joined grids for 3 promo cards.

Output under ``output/korean-player-card-index/``:
  - korean_card_index.csv   (1 row per card, 23 columns, RFC-4180 quoting)
  - korean_card_index.json  (structured record array + counts + anomalies)
  - manifest.json           (run metadata, counts, anomalies, sha256 of both)

Usage:
  build-korean-card-index.py
    [--source-dir PATH] [--out-dir PATH] [--pretty] [--dry-run]

Exit codes:
  0  OK: no anomalies AND no count-reconciliation drift
  1  warnings: one or more row-level anomalies, OR count drift
     (json_files_scanned != cards_written). Non-fatal — outputs still written.
  40 GMNotes invariant violation (missing / empty / non-JSON / missing 'id')
  41 grid invariant violation (NumWidth/NumHeight not losslessly int-coercible)
  42 sha256 mismatch on write-back verification
  43 missing source dir or a card with empty CustomDeck
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
SCED_DOWNLOADS_PATH = REPO_ROOT / "SCED-downloads"
DEFAULT_DECOMPOSED_ROOT = SCED_DOWNLOADS_PATH / "decomposed"

SOURCE_SUBPATH = (
    "language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI"
)
DEFAULT_SOURCE_DIR = DEFAULT_DECOMPOSED_ROOT / SOURCE_SUBPATH
DEFAULT_OUTPUT = SCRIPTS_DIR / "output" / "korean-player-card-index"

SCHEMA_VERSION = "1.0.0"
INDEXER_SCRIPT = "build-korean-card-index.py"
PACK = "playercards"

# Host fragments (copied verbatim from extract-korean-atlases.py).
R2_HOST_FRAGMENT = "pub-05b4fa32b44341d797f5c66d59384724.r2.dev"
STEAM_HOST_FRAGMENT = "steamusercontent-a.akamaihd.net"

# Fixed CSV column order (23 columns) — see design §3.
CSV_HEADER = [
    "name", "arkham_id", "card_id", "deck_key",
    "face_url", "face_source", "face_num_width", "face_num_height",
    "face_cell_index", "face_x", "face_y",
    "back_url", "back_source", "back_num_width", "back_num_height",
    "back_cell_index", "back_x", "back_y",
    "unique_back", "sideways", "pack",
    "source_file", "anomaly",
]

# Row-level anomaly codes (fixed set).
ANOMALY_CARD_ID_INVALID = "card_id_invalid"
ANOMALY_CELL_OUT_OF_BOUNDS = "cell_out_of_bounds"
ANOMALY_DECKKEY_MISMATCH = "deckkey_mismatch"
ANOMALY_MULTI_DECK = "multi_deck"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build an offline per-card image-atlas index for "
                    "the Korean Player Cards langpack."
    )
    p.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR,
                   help="Decomposed Korean Player Cards source directory.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT,
                   help="Output directory for the index + manifest.")
    p.add_argument("--pretty", action="store_true",
                   help="No-op alias retained for parity (JSON is already "
                        "indent=2). Does not affect CSV or manifest bytes.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute everything but write no files; return the "
                        "would-be 0/1 exit code.")
    return p.parse_args(argv)


def _parse_gmnotes_id(obj: dict, location: str) -> str:
    """Parse GMNotes (string or dict) and return the id value.

    Reuses the invariant pattern from build-candidates-index.py.
    """
    raw = obj.get("GMNotes", "")
    if isinstance(raw, dict):
        gm = raw
    elif isinstance(raw, str):
        if not raw.strip():
            print(
                f"GMNotes invariant violation: empty GMNotes at {location}",
                file=sys.stderr,
            )
            sys.exit(40)
        try:
            gm = json.loads(raw)
        except json.JSONDecodeError:
            print(
                f"GMNotes invariant violation: non-JSON GMNotes at {location}",
                file=sys.stderr,
            )
            sys.exit(40)
    else:
        print(
            f"GMNotes invariant violation: unexpected type at {location}",
            file=sys.stderr,
        )
        sys.exit(40)
    if "id" not in gm:
        print(
            f"GMNotes invariant violation: missing 'id' key at {location}",
            file=sys.stderr,
        )
        sys.exit(40)
    return str(gm["id"])


def _coerce_int(value, location: str, field: str) -> int:
    """Coerce CustomDeck NumWidth/NumHeight to int (R4 mitigation).

    TTSModManager normalization may emit floats — accept ints/floats convertible
    to int losslessly. Anything else is an invariant violation.
    """
    if isinstance(value, bool):
        # bools are ints in Python; treat as invariant violation explicitly.
        print(
            f"Grid invariant violation: bool value for {field} at {location}",
            file=sys.stderr,
        )
        sys.exit(41)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            # NaN / +-Infinity (json.load accepts these tokens) is not coercible
            # to int — fail cleanly with the grid invariant code, not a traceback.
            print(
                f"Grid invariant violation: non-finite {field}={value!r} at {location}",
                file=sys.stderr,
            )
            sys.exit(41)
        coerced = int(value)
        if float(coerced) != value:
            print(
                f"Grid invariant violation: non-integral {field}={value!r} at {location}",
                file=sys.stderr,
            )
            sys.exit(41)
        return coerced
    print(
        f"Grid invariant violation: non-numeric {field}={value!r} at {location}",
        file=sys.stderr,
    )
    sys.exit(41)


def _coerce_card_id(value) -> int | None:
    """Soft-coerce a CardID to int; return None on any invalid value.

    Unlike the grid _coerce_int (hard exit 41), this never exits: CardID is the
    row's own primary key and the inventory philosophy is keep-and-flag, not
    crash (design §5.3). A None result means anomaly=card_id_invalid: the cell
    math and the deckkey_mismatch check are both skipped, the row is kept, and
    the run contributes to exit 1. Invalid = missing / null / bool /
    non-integral float / non-numeric.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            # NaN / +-Infinity (json.load accepts these tokens): not a usable
            # CardID — keep-and-flag (return None), never crash on int(nan)/int(inf).
            return None
        coerced = int(value)
        if float(coerced) != value:
            return None
        return coerced
    return None


def classify_host(url: str) -> str:
    """Classify an atlas image URL host as a total enum: R2 | Steam | other.

    Applied independently to face_url and back_url (DRY within this script).

    # TODO(host-classifier-unification): single shared classify_host across
    # this script and extract-korean-atlases.py — see design §3/§5.9
    """
    if R2_HOST_FRAGMENT in url:
        return "R2"
    if STEAM_HOST_FRAGMENT in url:
        return "Steam"
    return "other"


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON via .tmp + os.replace for atomicity.

    Uses indent=2, ensure_ascii=False, sort_keys=False to preserve cards order.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    finally:
        # Defensive cleanup if replace failed.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def build_card_record(path: Path, source_dir: Path, obj: dict) -> dict:
    """Parse one decomposed card JSON into a per-card index record.

    ``obj`` is the already-parsed JSON for ``path``; the caller parses each file
    exactly once (and uses the same object to decide whether the file is in
    scope), so this function never re-reads the file.

    Hard exits: 40 (GMNotes), 41 (grid), 43 (empty CustomDeck).
    Row-level anomalies (kept + flagged): card_id_invalid, multi_deck,
    deckkey_mismatch, cell_out_of_bounds.
    """
    location = str(path)
    source_file = str(path.relative_to(source_dir))

    arkham_id = _parse_gmnotes_id(obj, location)
    name = obj.get("Nickname", "")
    sideways = bool(obj.get("SidewaysCard", False))

    custom_deck = obj.get("CustomDeck", {})
    if not custom_deck:
        print(
            f"Card invariant violation: empty CustomDeck at {path}",
            file=sys.stderr,
        )
        sys.exit(43)

    anomalies: list[str] = []

    # Validate CardID (soft — keep-and-flag, never sys.exit).
    card_id = _coerce_card_id(obj.get("CardID"))
    if card_id is None:
        anomalies.append(ANOMALY_CARD_ID_INVALID)

    # Select the single deck entry the SAME way the reuse source does, then
    # validate the CardID//100 relationship rather than using it as selector.
    deck_key = next(iter(custom_deck))
    if len(custom_deck) != 1:
        anomalies.append(ANOMALY_MULTI_DECK)
        # Disambiguate by re-selecting the entry whose key == str(CardID//100).
        if card_id is not None:
            expected_key = str(card_id // 100)
            if expected_key in custom_deck:
                deck_key = expected_key
    entry = custom_deck[deck_key]

    # Validate the CardID//100 invariant (skipped when card_id is invalid).
    if card_id is not None:
        expected_key = str(card_id // 100)
        if expected_key != deck_key:
            anomalies.append(ANOMALY_DECKKEY_MISMATCH)

    # FRONT derivation.
    face_url = entry.get("FaceURL", "")
    nw = _coerce_int(entry.get("NumWidth", 0), location, "NumWidth")
    nh = _coerce_int(entry.get("NumHeight", 0), location, "NumHeight")

    # x/y default to the typed sentinel (None). cell_index is None only when
    # card_id is invalid (no usable CardID).
    face_cell_index: int | None = None
    face_x: int | None = None
    face_y: int | None = None

    if card_id is not None:
        cell = card_id % 100
        face_cell_index = cell
        capacity = nw * nh
        if cell >= capacity:
            anomalies.append(ANOMALY_CELL_OUT_OF_BOUNDS)
            # Keep raw cell_index (informational); x/y stay sentinel.
        else:
            face_x = cell % nw
            face_y = cell // nw

    # BACK derivation: two-branch rule keyed off the authoritative UniqueBack.
    back_url = entry.get("BackURL", "")
    unique_back = bool(entry.get("UniqueBack", False))

    back: dict | None
    if back_url == "":
        # No back at all: all back_* columns empty, JSON back=null.
        back = None
    elif unique_back:
        # Gridded mirror back: mirrors the face grid + cell (incl. sentinel).
        back = {
            "url": back_url,
            "source": classify_host(back_url),
            "num_width": nw,
            "num_height": nh,
            "cell_index": face_cell_index,
            "x": face_x,
            "y": face_y,
            "single_image": False,
        }
    else:
        # Single shared cardback: one whole image, a 1x1 cell at origin.
        back = {
            "url": back_url,
            "source": classify_host(back_url),
            "num_width": 1,
            "num_height": 1,
            "cell_index": 0,
            "x": 0,
            "y": 0,
            "single_image": True,
        }

    record = {
        "name": name,
        "arkham_id": arkham_id,
        "card_id": card_id,
        "deck_key": deck_key,
        "face": {
            "url": face_url,
            "source": classify_host(face_url),
            "num_width": nw,
            "num_height": nh,
            "cell_index": face_cell_index,
            "x": face_x,
            "y": face_y,
        },
        "back": back,
        "unique_back": unique_back,
        "sideways": sideways,
        "pack": PACK,
        "source_file": source_file,
        "anomaly": ";".join(anomalies),
    }
    return record


def _csv_cell(value) -> str:
    """Render a JSON-side value as a CSV cell (None -> empty string).

    bool renders as "True"/"False" via str() (bool is an int subclass), so no
    special-casing is needed beyond the None -> "" rule.
    """
    if value is None:
        return ""
    return str(value)


def record_to_csv_row(rec: dict) -> list[str]:
    """Flatten a per-card record into the fixed 23-column CSV row order."""
    face = rec["face"]
    back = rec["back"]
    if back is None:
        back_url = back_source = ""
        back_nw = back_nh = back_cell = back_x = back_y = None
    else:
        back_url = back["url"]
        back_source = back["source"]
        back_nw = back["num_width"]
        back_nh = back["num_height"]
        back_cell = back["cell_index"]
        back_x = back["x"]
        back_y = back["y"]

    return [
        _csv_cell(rec["name"]),
        _csv_cell(rec["arkham_id"]),
        _csv_cell(rec["card_id"]),
        _csv_cell(rec["deck_key"]),
        _csv_cell(face["url"]),
        _csv_cell(face["source"]),
        _csv_cell(face["num_width"]),
        _csv_cell(face["num_height"]),
        _csv_cell(face["cell_index"]),
        _csv_cell(face["x"]),
        _csv_cell(face["y"]),
        _csv_cell(back_url),
        _csv_cell(back_source),
        _csv_cell(back_nw),
        _csv_cell(back_nh),
        _csv_cell(back_cell),
        _csv_cell(back_x),
        _csv_cell(back_y),
        _csv_cell(rec["unique_back"]),
        _csv_cell(rec["sideways"]),
        _csv_cell(rec["pack"]),
        _csv_cell(rec["source_file"]),
        _csv_cell(rec["anomaly"]),
    ]


def write_csv(path: Path, records: list[dict]) -> None:
    """Write the index CSV (RFC-4180 quoting, UTF-8, \\n line terminator)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(CSV_HEADER)
            for rec in records:
                writer.writerow(record_to_csv_row(rec))
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def build_anomaly_list(records: list[dict]) -> list[dict]:
    """Build the structured manifest/JSON anomaly list, sorted deterministically.

    Each kept row carrying one or more codes contributes one entry per code,
    with a human-readable detail string for the out-of-bounds case.
    """
    entries: list[dict] = []
    for rec in records:
        if not rec["anomaly"]:
            continue
        codes = rec["anomaly"].split(";")
        face = rec["face"]
        for code in codes:
            detail = ""
            if code == ANOMALY_CELL_OUT_OF_BOUNDS:
                nw = face["num_width"]
                nh = face["num_height"]
                detail = (
                    f"face_cell_index={face['cell_index']} >= "
                    f"capacity={nw * nh} ({nw}x{nh}) for deck_key={rec['deck_key']}"
                )
            elif code == ANOMALY_DECKKEY_MISMATCH:
                # deckkey_mismatch is only recorded when card_id is valid (§5.3),
                # so rec["card_id"] is always non-None here.
                expected = str(rec["card_id"] // 100)
                detail = f"deck_key={rec['deck_key']} != str(CardID//100)={expected}"
            elif code == ANOMALY_MULTI_DECK:
                detail = "more than one CustomDeck entry"
            elif code == ANOMALY_CARD_ID_INVALID:
                detail = "missing or non-integer CardID"
            entries.append({
                "card_id": rec["card_id"],
                "arkham_id": rec["arkham_id"],
                "name": rec["name"],
                "source_file": rec["source_file"],
                "code": code,
                "detail": detail,
            })
    entries.sort(key=lambda e: (e["source_file"], e["code"]))
    return entries


def build_counts(records: list[dict], json_files_scanned: int) -> dict:
    """Aggregate counts for the JSON + manifest counts block."""
    face_source = {"R2": 0, "Steam": 0, "other": 0}
    back_source = {"R2": 0, "Steam": 0, "other": 0}
    anomaly_count = 0
    for rec in records:
        face_source[rec["face"]["source"]] += 1
        if rec["back"] is not None:
            back_source[rec["back"]["source"]] += 1
        if rec["anomaly"]:
            anomaly_count += 1
    return {
        "json_files_scanned": json_files_scanned,
        "cards_written": len(records),
        "face_source": face_source,
        "back_source": back_source,
        "anomalies": anomaly_count,
    }


def main(argv=None) -> int:
    args = parse_args(argv)

    source_dir: Path = args.source_dir
    out_dir: Path = args.out_dir

    if not source_dir.exists():
        print(f"--source-dir does not exist: {source_dir}", file=sys.stderr)
        return 43

    paths = sorted(source_dir.rglob("*.json"))

    records: list[dict] = []
    json_files_scanned = 0
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            peek = json.load(fh)
        # Skip files lacking both CardID and CustomDeck (not counted).
        if "CardID" not in peek and "CustomDeck" not in peek:
            continue
        json_files_scanned += 1
        records.append(build_card_record(path, source_dir, peek))

    # Sort by (arkham_id, card_id, source_file) for deterministic output.
    # card_id can be None (card_id_invalid); sort those last via a typed key.
    records.sort(key=lambda r: (
        r["arkham_id"],
        (r["card_id"] is None, r["card_id"] or 0),
        r["source_file"],
    ))

    counts = build_counts(records, json_files_scanned)
    anomaly_entries = build_anomaly_list(records)

    drift = counts["json_files_scanned"] != counts["cards_written"]
    if drift:
        print(
            f"Count drift: json_files_scanned={counts['json_files_scanned']} "
            f"!= cards_written={counts['cards_written']}",
            file=sys.stderr,
        )

    warnings_emitted = bool(anomaly_entries) or drift

    print(
        f"Loaded {json_files_scanned} player card files from {source_dir}."
    )
    print(
        f"Built {len(records)} cards "
        f"(face: R2 {counts['face_source']['R2']} / Steam {counts['face_source']['Steam']} "
        f"/ other {counts['face_source']['other']}; "
        f"back: R2 {counts['back_source']['R2']} / Steam {counts['back_source']['Steam']} "
        f"/ other {counts['back_source']['other']}; anomalies {counts['anomalies']})."
    )
    for entry in anomaly_entries:
        print(
            f"ANOMALY {entry['code']}: card_id={entry['card_id']} "
            f"arkham_id={entry['arkham_id']} source_file={entry['source_file']} "
            f"{entry['detail']}",
            file=sys.stderr,
        )

    generated_at = datetime.now(timezone.utc).isoformat()

    index_doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source_dir": SOURCE_SUBPATH,
        "counts": counts,
        "anomalies": anomaly_entries,
        "cards": records,
    }

    if args.dry_run:
        print("(dry-run mode — no files written)")
        return 1 if warnings_emitted else 0

    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "korean_card_index.json"
    csv_path = out_dir / "korean_card_index.csv"
    manifest_path = out_dir / "manifest.json"

    atomic_write_json(json_path, index_doc)
    write_csv(csv_path, records)

    json_sha = compute_sha256(json_path)
    csv_sha = compute_sha256(csv_path)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "indexer": {
            "script": INDEXER_SCRIPT,
            "version": SCHEMA_VERSION,
        },
        "source_dir": SOURCE_SUBPATH,
        "counts": counts,
        "anomalies": anomaly_entries,
        "outputs": {
            "korean_card_index.json": {"sha256": json_sha},
            "korean_card_index.csv": {"sha256": csv_sha},
        },
    }
    atomic_write_json(manifest_path, manifest)

    # Re-read both data outputs and verify sha256 matches the manifest claim.
    declared = manifest["outputs"]
    for path in (json_path, csv_path):
        actual = compute_sha256(path)
        expected = declared[path.name]["sha256"]
        if actual != expected:
            print(
                f"sha256 mismatch on re-read: {path.name} "
                f"declared={expected!r} actual={actual!r}",
                file=sys.stderr,
            )
            return 42

    print(f"Wrote {json_path}  sha256={json_sha[:16]}...")
    print(f"Wrote {csv_path}  sha256={csv_sha[:16]}...")
    print(f"Wrote {manifest_path}")

    return 1 if warnings_emitted else 0


if __name__ == "__main__":
    sys.exit(main())
