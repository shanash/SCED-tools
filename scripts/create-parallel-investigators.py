#!/usr/bin/env python3
"""Create the 6 Korean parallel-investigator langpack objects (90088-90093).

The canonical parallel-investigator object family (Ведущая леди / Sam Blake
variants) is verified in Russian as exactly 6 ids, 90088-90093. The Korean
Player Cards langpack contains ZERO of them, so they must be CREATED (not
repointed). This tool clones the Russian template object's shape, injects the
Korean Nickname + the new atlas FaceURL + the template's working BackURL
verbatim (Q5 — the single pinned Steam back), allocates a fresh GUID, and
registers the new objects in the parent container.

Container registration: the new per-object JSONs are written into the directory
named by the parent's ``ContainedObjects_path`` STRING
("Korean-PlayerCards.KoreanI"), and each new stem is inserted into the parent's
``ContainedObjects_order`` LIST which is then re-sorted ASCENDING. This is a
deliberate departure from ``sync-contained-objects.py`` (see the [DISCREPANCY]
note below); ``ContainedObjects_path`` is never modified.

  [DISCREPANCY] The design says this tool "prefers to reuse
  sync-contained-objects.py" to regenerate ContainedObjects_order. It is NOT
  reused: that script (1) hardcodes a Windows path, (2) sorts the order
  reverse=True (DESCENDING — the real list is ASCENDING), and (3) rewrites the
  whole container with sort_keys=True (reordering every top-level key). Any of
  those would corrupt the container's minimal-diff shape. Instead we insert the
  6 new stems into the existing ContainedObjects_order list, re-sort ASCENDING
  (leaving the 1848 existing entries in place), leave every other container key
  untouched, and write via sced_io.atomic_write_json (2-space indent, trailing
  newline, no key reordering).

Usage:
  create-parallel-investigators.py
    --mapping mapping.json
    --atlas-manifest atlas_manifest.json
    --template-from "Russian - Player Cards/.../ВедущаяледиГероиня.25688.json"
    --decomposed-root .../Korean-PlayerCards.KoreanI
    --container-index .../Korean-PlayerCards.KoreanI.json
    [--output-dir DIR]
    [--dry-run]

Exit codes:
  0  OK
  2  unreadable / malformed input (missing file, bad JSON, bad template)
  3  post-apply validation errors (per-object checks or the atlas_patch gate)
  6  created count != 6 (the investigator subset is not exactly 90088-90093)
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

from atlas_patch import parse_gmnotes_id, validate_gate
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
DEFAULT_CONTAINER_INDEX = DEFAULT_DECOMPOSED_ROOT.with_suffix(".json")
DEFAULT_OUTPUT_DIR = SCRIPTS_DIR / "output" / "korean-pdf-atlases"

# The canonical parallel-investigator id family (verified against Russian data).
INVESTIGATOR_IDS = {"90088", "90089", "90090", "90091", "90092", "90093"}
EXPECTED_COUNT = 6

# A safe filename stem keeps only [A-Za-z0-9]; everything else collapses out so
# the on-disk name is ASCII (the existing Korean objects use transliterated /
# English stems, never the Korean Nickname — e.g. "ZoeySamarasParallelFront").
_STEM_SANITIZE = re.compile(r"[^A-Za-z0-9]+")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Create the 6 Korean parallel-investigator langpack objects "
                    "(90088-90093) from the Russian template + the new atlas."
    )
    p.add_argument("--mapping", type=Path, required=True,
                   help="Human-confirmed mapping.json (the investigator subset).")
    p.add_argument("--atlas-manifest", type=Path, required=True,
                   help="atlas_manifest.json (per-cell FaceURL for each id).")
    p.add_argument("--template-from", type=Path, required=True,
                   help="A Russian 90088-family object JSON to clone the shape "
                        "from (its BackURL is reused verbatim).")
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT,
                   help="Korean Player Cards decomposed object directory.")
    p.add_argument("--container-index", type=Path, default=DEFAULT_CONTAINER_INDEX,
                   help="Parent container JSON (Korean-PlayerCards.KoreanI.json).")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="Where to write created_objects_summary.json.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute and validate everything but write no files.")
    return p.parse_args(argv)


def load_json(path: Path):
    """Read + parse a JSON file, mapping any failure to a clean exit 2."""
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"InputError: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def collect_existing_guids(root: Path) -> set[str]:
    """Collect every GUID present in the decomposed directory.

    Used so a freshly-minted GUID cannot collide with any existing object,
    regardless of that object's GUID format (the tree mixes 6-char hex with a
    few 5/7-char and non-hex stems like 'tdc062').
    """
    guids: set[str] = set()
    for path in root.rglob("*.json"):
        try:
            with path.open(encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            g = obj.get("GUID")
            if isinstance(g, str) and g:
                guids.add(g)
    return guids


def fresh_guid(used: set[str]) -> str:
    """Mint a 6-lowercase-hex GUID not present in ``used`` (and reserve it)."""
    while True:
        candidate = secrets.token_hex(3)  # 3 bytes -> 6 lowercase hex chars
        if candidate not in used:
            used.add(candidate)
            return candidate


def face_url_by_id(manifest: dict) -> dict[str, str]:
    """Map each investigator arkham_id -> the atlas FaceURL of its cell.

    A given arkham_id should appear on exactly one atlas cell. If the manifest
    carries the same id on two cells with DIFFERENT FaceURLs that is a compose
    error (ambiguous source); warn to stderr rather than silently letting the
    last cell win.
    """
    out: dict[str, str] = {}
    for atlas in manifest.get("atlases", []):
        face_url = atlas.get("face_url", "")
        for cell in atlas.get("cells", []):
            arkham_id = str(cell.get("arkham_id", ""))
            if not arkham_id:
                continue
            if arkham_id in out and out[arkham_id] != face_url:
                print(
                    f"WARNING: arkham_id {arkham_id} appears on multiple atlas "
                    f"cells with different FaceURLs; using the last ({face_url})",
                    file=sys.stderr,
                )
            out[arkham_id] = face_url
    return out


def nickname_by_id(mapping: dict) -> dict[str, str]:
    """Map each arkham_id -> the Korean Nickname carried on its mapping entry.

    The mapping entry is the human-confirmed source of the Korean name. The
    investigator objects do not exist in Korean yet (no source_file to read a
    name from), so the Korean Nickname is taken from an optional ``nickname``
    (or ``name``) field on the entry; absent that, the caller falls back to the
    template's Nickname (and the run is still valid — only the display name is
    less specific).
    """
    out: dict[str, str] = {}
    for entry in mapping.get("entries", []):
        arkham_id = str(entry.get("arkham_id", ""))
        if not arkham_id:
            continue
        name = entry.get("nickname") or entry.get("name")
        if isinstance(name, str) and name.strip():
            out[arkham_id] = name
    return out


def build_object(template: dict, arkham_id: str, nickname: str,
                 face_url: str, guid: str) -> dict:
    """Clone the template into one new investigator object (alpha-ordered keys).

    Mutated vs the template: CardID = id*100; CustomDeck = {<id>: {...1x1
    UniqueBack...}} with the new FaceURL and the template's BackURL verbatim;
    GMNotes = the escaped-JSON-string form of {"id": <id>}; GUID = fresh;
    Nickname = the Korean name. Every other template field (Name, Transform,
    Description, etc.) is carried through. Keys are emitted alphabetically to
    match the existing Korean object files.
    """
    template_key = next(iter(template.get("CustomDeck", {})))
    template_deck = template["CustomDeck"][template_key]
    back_url = template_deck.get("BackURL", "")

    custom_deck = {
        arkham_id: {
            "BackIsHidden": True,
            "BackURL": back_url,
            "FaceURL": face_url,
            "NumHeight": 1,
            "NumWidth": 1,
            "Type": 0,
            "UniqueBack": True,
        }
    }

    # Start from a copy of the template so any extra fields (Description, Tags…)
    # are preserved, then overwrite the fields we own.
    obj = dict(template)
    obj["CardID"] = int(arkham_id) * 100
    obj["CustomDeck"] = custom_deck
    obj["GMNotes"] = json.dumps({"id": arkham_id}, indent=2, ensure_ascii=False)
    obj["GUID"] = guid
    obj["Nickname"] = nickname

    # Emit keys alphabetically (existing Korean objects are alpha-ordered).
    return {k: obj[k] for k in sorted(obj)}


def stem_for(nickname: str, arkham_id: str, guid: str) -> str:
    """Build an ASCII ``<Name>.<guid>`` stem for the on-disk file.

    Sanitises the Nickname to ASCII alphanumerics; if nothing survives (a purely
    Korean name), falls back to a stable transliteration-free token derived from
    the arkham_id so the stem is always non-empty and unique.
    """
    base = _STEM_SANITIZE.sub("", nickname)
    if not base:
        base = f"ParallelInvestigator{arkham_id}"
    return f"{base}.{guid}"


def run(args: argparse.Namespace) -> int:
    mapping = load_json(args.mapping)
    manifest = load_json(args.atlas_manifest)
    template = load_json(args.template_from)
    if not isinstance(template, dict) or not template.get("CustomDeck"):
        print("InputError: --template-from is not a valid card object "
              "(no CustomDeck)", file=sys.stderr)
        return 2

    container = load_json(args.container_index)
    contained_dir_name = container.get("ContainedObjects_path")
    order = container.get("ContainedObjects_order")
    if not isinstance(contained_dir_name, str) or not isinstance(order, list):
        print("InputError: container index missing ContainedObjects_path "
              "(string) / ContainedObjects_order (list)", file=sys.stderr)
        return 2

    # Select the investigator subset and assert it is EXACTLY the 6 canonical ids.
    mapping_ids = {
        str(e.get("arkham_id"))
        for e in mapping.get("entries", [])
        if str(e.get("arkham_id", "")) in INVESTIGATOR_IDS
    }
    if mapping_ids != INVESTIGATOR_IDS:
        missing = sorted(INVESTIGATOR_IDS - mapping_ids)
        extra = sorted(mapping_ids - INVESTIGATOR_IDS)
        print(
            f"CountError: investigator subset is not the 6 canonical ids "
            f"(have={sorted(mapping_ids)} missing={missing} extra={extra})",
            file=sys.stderr,
        )
        return 6

    faces = face_url_by_id(manifest)
    nicknames = nickname_by_id(mapping)
    template_nickname = template.get("Nickname", "Card")

    used_guids = collect_existing_guids(args.decomposed_root)
    # Reserve the template's own GUID too, in case the template lives elsewhere.
    tmpl_guid = template.get("GUID")
    if isinstance(tmpl_guid, str):
        used_guids.add(tmpl_guid)

    container_dir = args.container_index.parent / contained_dir_name

    new_files: dict[Path, dict] = {}
    new_objs_by_label: dict[str, dict] = {}  # for the validate_gate pass
    new_stems: list[str] = []
    created_records: list[dict] = []

    for arkham_id in sorted(INVESTIGATOR_IDS):
        face_url = faces.get(arkham_id)
        if not face_url:
            print(
                f"InputError: atlas manifest has no FaceURL for investigator "
                f"id {arkham_id}",
                file=sys.stderr,
            )
            return 2
        nickname = nicknames.get(arkham_id, template_nickname)
        guid = fresh_guid(used_guids)
        obj = build_object(template, arkham_id, nickname, face_url, guid)
        stem = stem_for(nickname, arkham_id, guid)
        dest = container_dir / f"{stem}.json"

        new_files[dest] = obj
        new_objs_by_label[stem] = obj
        new_stems.append(stem)
        created_records.append({
            "arkham_id": arkham_id,
            "card_id": obj["CardID"],
            "guid": guid,
            "stem": stem,
            "nickname": nickname,
            "face_url": face_url,
        })

    # Hard guard: exactly 6 objects built.
    if len(new_files) != EXPECTED_COUNT:
        print(
            f"CountError: built {len(new_files)} objects, expected "
            f"{EXPECTED_COUNT}",
            file=sys.stderr,
        )
        return 6

    # Post-apply validation: per-object invariants + the shared gate.
    val_errors: list[str] = []
    for stem, obj in new_objs_by_label.items():
        expected_id = next(iter(obj["CustomDeck"]))
        if obj["CardID"] != int(expected_id) * 100:
            val_errors.append(
                f"ValidationError: {stem} CardID {obj['CardID']} != "
                f"id*100 {int(expected_id) * 100}"
            )
        deck = obj["CustomDeck"][expected_id]
        if deck.get("UniqueBack") is not True:
            val_errors.append(f"ValidationError: {stem} UniqueBack is not True")
        try:
            gm_id = parse_gmnotes_id(obj, stem)
        except ValueError as exc:
            val_errors.append(f"ValidationError: {stem} {exc}")
            gm_id = None
        if gm_id is not None and gm_id != expected_id:
            val_errors.append(
                f"ValidationError: {stem} GMNotes id {gm_id!r} != CustomDeck "
                f"key {expected_id!r}"
            )
    val_errors.extend(validate_gate(new_objs_by_label))
    if val_errors:
        for err in val_errors:
            print(err, file=sys.stderr)
        return 3

    # Register: insert the new stems into ContainedObjects_order, re-sort ASC.
    # (See [DISCREPANCY] in the module docstring — do NOT use
    # sync-contained-objects.py.) Every other container key is left untouched.
    new_order = sorted(set(order) | set(new_stems))
    container["ContainedObjects_order"] = new_order

    summary = {
        "schema_version": "1.0.0",
        "created_count": len(created_records),
        "container_index": str(args.container_index),
        "container_dir": str(container_dir),
        "order_len_before": len(order),
        "order_len_after": len(new_order),
        "created": created_records,
    }

    if args.dry_run:
        print(
            f"(dry-run) would create {len(created_records)} investigator "
            f"object(s) in {container_dir}; ContainedObjects_order "
            f"{len(order)} -> {len(new_order)}; validation passed; "
            f"no files written."
        )
        return 0

    # Write the 6 new objects + the container index. The objects go via the batch
    # (all-or-nothing); the container is a single atomic write afterward.
    atomic_write_json_batch(new_files)
    atomic_write_json(args.container_index, container)

    summary_path = args.output_dir / "created_objects_summary.json"
    atomic_write_json(summary_path, summary)

    print(
        f"Created {len(created_records)} investigator object(s) in "
        f"{container_dir}."
    )
    print(
        f"ContainedObjects_order {len(order)} -> {len(new_order)} "
        f"(container: {args.container_index})."
    )
    print(f"created_objects_summary written: {summary_path}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
