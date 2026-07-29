#!/usr/bin/env python3
"""Create Korean langpack Taboo objects for reprint printings (Track A).

Five Track-A crops are SEPARATE printings (reprints) of a base Taboo card, each
with its own art + arkham_id, grouped to a base via SCED REPRINT_GROUPS. The
Korean Player Cards langpack has no Taboo object for any of the 5, so they must
be CREATED (not repointed). Each new object clones its base Taboo object's shape
and overrides only GMNotes id + GUID; CustomDeck/CardID stay as placeholders
(apply-atlas-faceurls.py repoints FaceURL + packs the deck key/CardID later).

Container registration mirrors create-parallel-investigators.py: the new stems
are inserted into the parent container's ContainedObjects_order LIST (re-sorted
ASCENDING); ContainedObjects_path and all other keys are untouched. Atomic write
via sced_io. No network, no SCED/ change — only the langpack bag.

See .am/korean-pdf-card-atlases/reprint-objects-design.md.

Usage:
  create-reprint-objects.py
    --decomposed-root ".../Korean - Player Cards/Korean-PlayerCards.KoreanI"
    --container-index ".../Korean - Player Cards/Korean-PlayerCards.KoreanI.json"
    [--mapping mapping.json]   # if given, its 5 reprint entries' source_file is rewritten
    [--dry-run]

Exit codes: 0 ok · 2 input error · 3 write/IO error · 4 base stem missing · 6 container malformed
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sced_io import atomic_write_json  # noqa: E402

# reprint id -> base Taboo stem to clone (verified against the langpack + ReprintGroups.ttslua)
REPRINT_SPEC = {
    "01573-t": "ScavengingTaboo.1b76c8",
    "60405-t": "RitualCandlesTaboo.7dc746",
    "60414-t": "HypnoticGazeTaboo.47d782",
    "60417-t": "DarkProphecyTaboo.448db7",
    "01692-t": "Eucatastrophe3Taboo.8be540",
}

_STEM_SANITIZE = re.compile(r"[^A-Za-z0-9]+")


def collect_existing_guids(root: Path) -> set[str]:
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


def existing_ids(root: Path) -> dict[str, str]:
    """Map GMNotes id -> stem for every object already in the dir (idempotency)."""
    out: dict[str, str] = {}
    for path in root.glob("*.json"):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            gm = json.loads(obj.get("GMNotes", "") or "{}")
        except (OSError, json.JSONDecodeError):
            continue
        cid = gm.get("id")
        if isinstance(cid, str):
            out[cid] = path.stem
    return out


def fresh_guid(used: set[str]) -> str:
    while True:
        candidate = secrets.token_hex(3)
        if candidate not in used:
            used.add(candidate)
            return candidate


def build_reprint(base_obj: dict, reprint_id: str, guid: str) -> dict:
    """Clone the base Taboo object; override only GMNotes id + GUID. Keys alpha-sorted."""
    obj = dict(base_obj)
    obj["GMNotes"] = json.dumps({"id": reprint_id}, indent=2, ensure_ascii=False)
    obj["GUID"] = guid
    return {k: obj[k] for k in sorted(obj)}


def stem_for(nickname: str, reprint_id: str, guid: str) -> str:
    base = _STEM_SANITIZE.sub("", nickname or "")
    if not base:
        base = f"Reprint{reprint_id.replace('-', '')}"
    return f"{base}.{guid}"


def run(args: argparse.Namespace) -> int:
    root = Path(args.decomposed_root)
    container_index = Path(args.container_index)
    if not root.is_dir():
        print(f"InputError: --decomposed-root not a dir: {root}", file=sys.stderr)
        return 2
    try:
        container = json.loads(container_index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"InputError: cannot read container index: {e}", file=sys.stderr)
        return 6
    order = container.get("ContainedObjects_order")
    if not isinstance(order, list):
        print("InputError: container ContainedObjects_order missing/not a list", file=sys.stderr)
        return 6

    used_guids = collect_existing_guids(root)
    present = existing_ids(root)

    planned: list[dict] = []
    new_stems: list[str] = []
    for reprint_id, base_stem in REPRINT_SPEC.items():
        if reprint_id in present:
            print(f"  SKIP {reprint_id}: already exists as {present[reprint_id]}.json")
            continue
        base_path = root / f"{base_stem}.json"
        if not base_path.is_file():
            print(f"BaseMissing: {base_path}", file=sys.stderr)
            return 4
        base_obj = json.loads(base_path.read_text(encoding="utf-8"))
        nickname = base_obj.get("Nickname", "Card")
        guid = fresh_guid(used_guids)
        obj = build_reprint(base_obj, reprint_id, guid)
        stem = stem_for(nickname, reprint_id, guid)
        planned.append({"reprint_id": reprint_id, "stem": stem, "nickname": nickname,
                        "base_stem": base_stem, "obj": obj})
        new_stems.append(stem)

    if not planned:
        print("Nothing to do (all reprint ids already present).")
        return 0

    print(f"\n{'DRY-RUN — would create' if args.dry_run else 'Creating'} {len(planned)} reprint object(s):")
    for p in planned:
        print(f"  {p['reprint_id']:9} <- clone {p['base_stem']:28} -> {p['stem']}.json  ({p['nickname']})")

    if args.dry_run:
        print("\n(dry-run: no files written, container untouched)")
        return 0

    try:
        for p in planned:
            atomic_write_json(root / f"{p['stem']}.json", p["obj"])
        merged = sorted(set(order) | set(new_stems))
        container["ContainedObjects_order"] = merged
        atomic_write_json(container_index, container)
    except OSError as e:
        print(f"WriteError: {e}", file=sys.stderr)
        return 3

    print(f"\nWrote {len(planned)} object(s); ContainedObjects_order {len(order)} -> {len(merged)}.")

    # Optionally rewrite the 5 reprint entries' source_file in mapping.json
    if args.mapping:
        mp_path = Path(args.mapping)
        try:
            mp = json.loads(mp_path.read_text(encoding="utf-8"))
            stem_by_id = {p["reprint_id"]: p["stem"] for p in planned}
            n = 0
            for e in mp.get("entries", []):
                stem = stem_by_id.get(e.get("arkham_id"))
                if stem:
                    e["source_file"] = f"{stem}.json"
                    n += 1
            atomic_write_json(mp_path, mp)
            print(f"Updated {n} reprint source_file(s) in {mp_path.name}.")
        except (OSError, json.JSONDecodeError) as e:
            print(f"WriteError (mapping): {e}", file=sys.stderr)
            return 3
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decomposed-root", required=True)
    ap.add_argument("--container-index", required=True)
    ap.add_argument("--mapping", default=None)
    ap.add_argument("--dry-run", action="store_true")
    return run(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
