#!/usr/bin/env python3
"""
Idempotent FaceURL/BackURL swap fix for the 9 Scenario-reference cards.

Why this exists (.am/korean-image-apply/design.md §5.4.3):
  - An earlier commit (3f763bcae5) on SCED-downloads applied this swap to the
    *combined* korean_campaigns.json only. The decomposed tree never received
    the fix.
  - This script applies the swap to the decomposed tree so the combined
    rebuild reproduces the fix naturally.

Idempotency:
  - Primary signal: each swapped card JSON gets a top-level
    `"_swap_applied_v1": true` marker. If present on re-run, skip.
  - Fallback: pinned known_good_back URL (loaded from fixture at import
    time — not hard-coded). If FaceURL equals that back URL, swap is
    pending; if BackURL equals it, already OK.

Outputs are merged into the existing apply_diff_summary.json so the single
post-apply verifier can see both replace and swap counts in one place.
"""

import argparse
import json
import sys
from pathlib import Path

from sced_io import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DECOMPOSED_CAMPAIGNS = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Campaigns"
    / "Korean-Campaigns.KoreanC"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "korean-image-apply"


# --- Pinned-back fixture (loaded at module import time; no hard-coding) ----
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "korean-image-apply" / "known_good_backs.json"
)
try:
    with _FIXTURE_PATH.open("r", encoding="utf-8") as _fh:
        _KNOWN_GOOD_BACKS = json.load(_fh)
except OSError as _e:
    raise RuntimeError(
        f"known_good_backs.json not found at {_FIXTURE_PATH} "
        "— re-run Step 0 extractor (see .am/korean-image-apply/design.md §6 Step 0)"
    ) from _e


def known_good_back_url_for(pinned_key: str) -> str:
    try:
        return _KNOWN_GOOD_BACKS[pinned_key]
    except KeyError as e:
        raise RuntimeError(
            f"known_good_backs.json missing pinned_key={pinned_key!r}; "
            "re-run Step 0 extractor against "
            "SCED-downloads/downloadable/korean_campaigns.json"
        ) from e


# Each entry: (arkham_id, scenario_label, pinned_key)
SCENARIO_REF_SWAP_CARDS = [
    ("03043", "Path to Carcosa Scenario ref — Deck 2320", "carcosa_scenario_ref"),
    ("03061", "Path to Carcosa Scenario ref — Deck 2320", "carcosa_scenario_ref"),
    ("03120", "Path to Carcosa Scenario ref — Deck 2320", "carcosa_scenario_ref"),
    ("03159", "Path to Carcosa Scenario ref — Deck 2320", "carcosa_scenario_ref"),
    ("08501", "Edge of the Earth Scenario ref — Deck 4499/4501/4520", "eote_scenario_ref"),
    ("08549", "Edge of the Earth Scenario ref", "eote_scenario_ref"),
    ("08596", "Edge of the Earth Scenario ref", "eote_scenario_ref"),
    ("08621", "Edge of the Earth Scenario ref", "eote_scenario_ref"),
    ("08648", "Edge of the Earth Scenario ref", "eote_scenario_ref"),
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Idempotent FaceURL/BackURL swap for 9 Scenario-ref cards."
    )
    p.add_argument("--decomposed-campaigns", type=Path,
                   default=DEFAULT_DECOMPOSED_CAMPAIGNS)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                   help="Where apply_diff_summary.json lives (merge target).")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _resolve_card_arkham_id(path: Path, data: dict) -> str:
    """Campaigns use GMNotes_path → sibling .gmnotes file."""
    inline = data.get("GMNotes", "")
    if inline:
        try:
            return str(json.loads(inline).get("id", ""))
        except json.JSONDecodeError:
            pass
    gm_path = data.get("GMNotes_path")
    if gm_path:
        sibling = path.parent / Path(gm_path).name
        if sibling.exists():
            try:
                with sibling.open(encoding="utf-8") as fh:
                    return str(json.load(fh).get("id", ""))
            except (json.JSONDecodeError, OSError):
                return ""
    return ""


def _swap_deck_urls(data: dict) -> bool:
    """Swap FaceURL <-> BackURL on every CustomDeck block. Returns True if anything swapped."""
    cd = data.get("CustomDeck")
    if not isinstance(cd, dict):
        return False
    changed = False
    for k, v in cd.items():
        if not isinstance(v, dict):
            continue
        if "FaceURL" in v and "BackURL" in v:
            v["FaceURL"], v["BackURL"] = v["BackURL"], v["FaceURL"]
            changed = True
    return changed


def process_card(path: Path, arkham_id: str, pinned_key: str,
                 dry_run: bool) -> tuple[str, str]:
    """Process a single card JSON. Returns (status, message).

    status ∈ {"applied", "already_ok", "skipped_by_marker", "warning", "error"}
    """
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return "error", f"cannot read {path}: {exc}"

    if data.get("_swap_applied_v1") is True:
        return "skipped_by_marker", f"{arkham_id} {path.name}"

    known_back = known_good_back_url_for(pinned_key)

    cd = data.get("CustomDeck") or {}
    if not isinstance(cd, dict) or not cd:
        return "warning", f"{arkham_id} {path.name}: no CustomDeck"

    first_face = None
    first_back = None
    for _, v in cd.items():
        if isinstance(v, dict):
            first_face = v.get("FaceURL", "")
            first_back = v.get("BackURL", "")
            break

    if first_face == known_back:
        if dry_run:
            return "applied", f"{arkham_id} {path.name} (dry-run: would swap)"
        if not _swap_deck_urls(data):
            return "warning", f"{arkham_id} {path.name}: swap no-op"
        data["_swap_applied_v1"] = True
        atomic_write_json(path, data)
        return "applied", f"{arkham_id} {path.name}"

    if first_back == known_back and first_face != known_back:
        if dry_run:
            return "already_ok", f"{arkham_id} {path.name} (already ok)"
        # Stamp the marker so future runs hit the fast path.
        data["_swap_applied_v1"] = True
        atomic_write_json(path, data)
        return "already_ok", f"{arkham_id} {path.name}"

    return "warning", (
        f"{arkham_id} {path.name}: FaceURL/BackURL do not match pinned "
        f"{pinned_key} back — manual review required"
    )


def find_card_files(root: Path, arkham_ids: list[str]) -> dict[str, list[Path]]:
    """Map arkham_id -> list of matching card JSON paths."""
    found: dict[str, list[Path]] = {a: [] for a in arkham_ids}
    if not root.exists():
        return found
    for path in sorted(root.rglob("*.json")):
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or "CustomDeck" not in data:
            continue
        ark = _resolve_card_arkham_id(path, data)
        if ark in found:
            found[ark].append(path)
    return found


def run(args) -> int:
    arkham_ids = [a for (a, _, _) in SCENARIO_REF_SWAP_CARDS]
    found = find_card_files(args.decomposed_campaigns, arkham_ids)

    counts = {"applied": 0, "already_ok": 0, "skipped_by_marker": 0}
    warnings: list[str] = []

    for arkham_id, label, pinned_key in SCENARIO_REF_SWAP_CARDS:
        matches = found.get(arkham_id, [])
        if not matches:
            warnings.append(f"{arkham_id} ({label}): no decomposed card matched")
            continue
        for path in matches:
            status, msg = process_card(path, arkham_id, pinned_key, args.dry_run)
            if status in counts:
                counts[status] += 1
                print(f"  [{status}] {msg}")
            elif status == "warning":
                warnings.append(msg)
                print(f"  [warning] {msg}", file=sys.stderr)
            else:
                warnings.append(msg)
                print(f"  [error]   {msg}", file=sys.stderr)

    # Merge into apply_diff_summary.json.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "apply_diff_summary.json"
    if summary_path.exists():
        try:
            with summary_path.open(encoding="utf-8") as fh:
                summary = json.load(fh)
            if not isinstance(summary, dict):
                summary = {}
        except (json.JSONDecodeError, OSError):
            summary = {}
    else:
        summary = {}

    summary["swap_applied"] = counts["applied"]
    summary["swap_already_ok"] = counts["already_ok"]
    summary["swap_skipped_by_marker"] = counts["skipped_by_marker"]
    summary["swap_warnings"] = warnings
    atomic_write_json(summary_path, summary)
    print(
        f"summary merged → {summary_path}  "
        f"applied={counts['applied']} already_ok={counts['already_ok']} "
        f"skipped_by_marker={counts['skipped_by_marker']} warnings={len(warnings)}"
    )
    return 0


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
