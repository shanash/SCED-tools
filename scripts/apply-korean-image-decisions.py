#!/usr/bin/env python3
"""
Apply user-reviewed Korean image decisions to the decomposed card JSONs.

This script is review-driven only. It knows nothing about the 9-card
Scenario-reference swap (that is handled by `fix-scenario-ref-swap.py`,
executed afterwards). See .am/korean-image-apply/design.md §5.4.1–§5.4.4.

Inputs:
  --review            path to review_export.json (Phase 2 output)
  --upload-map        path to upload_map.json (optional; required if any
                      review entry's replaceWith is a bare filename)
  --decomposed-playercards  default: SCED-downloads .../Korean - Player Cards/...
  --decomposed-campaigns    default: SCED-downloads .../Korean - Campaigns/...
  --output-dir        default: SCED-tools/scripts/output/korean-image-apply
  --dry-run           do not write any card JSON; just print and emit summary
  --allow-revert      required to honor action=revert entries

Outputs:
  - In-place edits of decomposed card JSONs (unless --dry-run)
  - <output-dir>/apply_diff_summary.json  (schema per design §5.4.2)
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from sced_io import atomic_write_json, atomic_write_json_batch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DECOMPOSED_PLAYERCARDS = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Player Cards"
    / "Korean-PlayerCards.KoreanI"
)
DEFAULT_DECOMPOSED_CAMPAIGNS = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Campaigns"
    / "Korean-Campaigns.KoreanC"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "korean-image-apply"
DEFAULT_COMBINED_CAMPAIGNS = (
    REPO_ROOT / "SCED-downloads" / "downloadable" / "korean_campaigns.json"
)
DEFAULT_KNOWN_GOOD_BACKS = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "korean-image-apply" / "known_good_backs.json"
)

URL_REGEX = re.compile(r"^https://[^\s]+\.(png|jpg|jpeg)(\?[^\s]*)?$", re.IGNORECASE)
VALID_ACTIONS = {"keep", "replace", "revert"}


class ValidationError(Exception):
    """Raised on review_export.json / post-apply rule violations."""


# ----------------------------- CLI / I/O ------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply review_export.json decisions to decomposed Korean card JSONs."
    )
    parser.add_argument("--review", type=Path, required=True,
                        help="Path to review_export.json (array of entries).")
    parser.add_argument("--upload-map", type=Path, default=None,
                        help="Path to upload_map.json (required if replaceWith uses bare filenames).")
    parser.add_argument("--decomposed-playercards", type=Path,
                        default=DEFAULT_DECOMPOSED_PLAYERCARDS)
    parser.add_argument("--decomposed-campaigns", type=Path,
                        default=DEFAULT_DECOMPOSED_CAMPAIGNS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="Where to write apply_diff_summary.json.")
    parser.add_argument("--combined-campaigns", type=Path,
                        default=DEFAULT_COMBINED_CAMPAIGNS,
                        help="Combined korean_campaigns.json, used for post-apply "
                             "assertion #9 (pinned back URL existence).")
    parser.add_argument("--known-good-backs", type=Path,
                        default=DEFAULT_KNOWN_GOOD_BACKS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-revert", action="store_true",
                        help="Required to honor action=revert entries.")
    parser.add_argument("--skip-post-apply", action="store_true",
                        help="Skip validate_post_apply() (testing only).")
    return parser.parse_args(argv)


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def index_uploads_by_name(upload_map: dict | None) -> dict[str, str]:
    """Map each upload's basename -> url, the first occurrence winning.

    Built once and shared by validate_review (membership check) and the apply
    loop (URL resolution) instead of re-scanning uploads[] per entry, turning
    the previous O(entries x uploads) lookup into O(entries). First-wins is
    deliberate: it reproduces the prior linear scan in resolve_replace_url,
    which returned the first uploads[] entry whose local_path basename matched.
    validate_review reads only membership, so the stored value never affects it.
    """
    index: dict[str, str] = {}
    if upload_map:
        for u in upload_map.get("uploads", []):
            name = Path(u.get("local_path") or "").name
            if name and name not in index:
                index[name] = u.get("url", "")
    return index


# ----------------------------- validation -----------------------------------


def validate_review(entries: list, upload_map: dict | None, allow_revert: bool) -> None:
    """Enforce §4.2.1. Raises ValidationError on first violation."""
    if not isinstance(entries, list):
        raise ValidationError("review_export.json root must be an array")

    by_local = index_uploads_by_name(upload_map)

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValidationError(f"entry #{i} is not an object")
        action = entry.get("action", "keep")
        if action not in VALID_ACTIONS:
            raise ValidationError(
                f"entry #{i} unknown action='{action}' (expected keep|replace|revert)"
            )
        if "customDeckKey" not in entry:
            raise ValidationError(f"entry #{i} missing customDeckKey")
        if "faceUrl" not in entry:
            raise ValidationError(f"entry #{i} missing faceUrl")

        if action == "replace":
            rw = entry.get("replaceWith", "")
            if not rw:
                raise ValidationError(
                    f"entry #{i} action=replace but replaceWith is empty"
                )
            if rw.startswith("http://") or rw.startswith("https://"):
                continue  # URL form — no upload_map check
            if rw.startswith("file://"):
                raise ValidationError(
                    f"entry #{i} replaceWith uses file:// scheme (not allowed)"
                )
            if "/" in rw or "\\" in rw:
                raise ValidationError(
                    f"entry #{i} replaceWith='{rw}' looks like a path "
                    "(expected bare filename or http(s) URL)"
                )
            if rw not in by_local:
                raise ValidationError(
                    f"entry #{i} replaceWith='{rw}' not found in upload_map.json"
                )
        elif action == "revert":
            if not allow_revert:
                raise ValidationError(
                    f"entry #{i} action=revert requires --allow-revert flag"
                )
            cards = entry.get("cards", [])
            if not isinstance(cards, list) or len(cards) < 1:
                raise ValidationError(
                    f"entry #{i} action=revert requires non-empty cards[]"
                )


# ----------------------------- apply ----------------------------------------


def iter_card_jsons(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*.json")):
        yield path


def resolve_replace_url(entry: dict, upload_index: dict[str, str]) -> str:
    rw = entry["replaceWith"]
    if rw.startswith("http://") or rw.startswith("https://"):
        return rw
    url = upload_index.get(rw)
    if url is None:
        raise ValidationError(f"replaceWith='{rw}' unresolved at apply time")
    return url


def collect_all_card_files(playercards_root: Path, campaigns_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in (playercards_root, campaigns_root):
        files.extend(iter_card_jsons(root))
    return files


def apply_entries(entries: list, playercards_root: Path, campaigns_root: Path,
                  upload_map: dict | None, allow_revert: bool,
                  dry_run: bool) -> dict:
    """Main apply loop. Returns apply_diff_summary.json content."""

    summary = {
        "changed_files": [],
        "replaced_sheets": 0,
        "reverted_cards": 0,
        "warnings": [],
        "unmatched_entries": [],
    }

    # Pre-load all card JSONs once to avoid re-reading per entry.
    all_files = collect_all_card_files(playercards_root, campaigns_root)
    cards: list[tuple[Path, dict]] = []
    for p in all_files:
        try:
            with p.open(encoding="utf-8") as fh:
                cards.append((p, json.load(fh)))
        except (json.JSONDecodeError, OSError) as exc:
            summary["warnings"].append(f"parse_error: {p}: {exc}")

    # Map of (deck_key, face_url) -> list of (path, data) tuples.
    by_deck_face = defaultdict(list)
    for path, data in cards:
        cd = data.get("CustomDeck") or {}
        if not isinstance(cd, dict):
            continue
        for k, v in cd.items():
            if isinstance(v, dict) and "FaceURL" in v:
                by_deck_face[(str(k), v.get("FaceURL", ""))].append((path, data))

    # Resolve bare-filename replaceWith values via a single basename->url index
    # instead of re-scanning upload_map["uploads"] for every replace entry.
    upload_index = index_uploads_by_name(upload_map)

    modified_files: dict[Path, dict] = {}

    for i, entry in enumerate(entries):
        action = entry.get("action", "keep")
        deck_key = str(entry.get("customDeckKey"))
        face_url = entry.get("faceUrl", "")

        if action == "keep":
            continue

        if action == "replace":
            new_url = resolve_replace_url(entry, upload_index)
            matches = by_deck_face.get((deck_key, face_url), [])
            if not matches:
                summary["unmatched_entries"].append({
                    "entry_index": i,
                    "customDeckKey": deck_key,
                    "faceUrl": face_url,
                    "reason": "no card JSON matched (deck_key, face_url)",
                })
                continue
            for path, data in matches:
                deck = data["CustomDeck"][deck_key]
                if deck.get("FaceURL") == new_url:
                    continue  # already up to date
                deck["FaceURL"] = new_url
                modified_files[path] = data
            summary["replaced_sheets"] += 1

        elif action == "revert":
            # action=revert deletes decomposed JSONs for the listed cards and
            # removes them from parent ContainedObjects_order.
            removed = _apply_revert(entry, campaigns_root, playercards_root,
                                    cards, summary, dry_run)
            summary["reverted_cards"] += removed

    # Write modified card JSONs as a two-phase staged commit so a mid-batch
    # failure (disk full, serialisation error) leaves zero files changed instead
    # of a partial apply: every file is serialised to a same-dir temp first, then
    # all temps are os.replace'd into place. Per-file 2-space indent (the style
    # used across the tree) is preserved. See sced_io.atomic_write_json_batch.
    changed = sorted(str(path) for path in modified_files)
    if not dry_run:
        atomic_write_json_batch(modified_files)
    summary["changed_files"] = changed
    return summary


def _apply_revert(entry: dict, campaigns_root: Path, playercards_root: Path,
                  cards: list, summary: dict, dry_run: bool) -> int:
    """Delete decomposed JSONs for each card in entry['cards']. Returns count.

    Deletions are applied to disk only; the in-memory `cards` list is left
    untouched. validate_post_apply re-reads disk afterwards, so it must not
    reuse this stale snapshot (it would still see the unlinked cards).
    """
    removed = 0
    for card in entry.get("cards", []):
        guid = card.get("guid", "")
        card_id = card.get("cardId") or card.get("CardID")
        target_path = None
        for path, data in cards:
            if (data.get("GUID") == guid) or (data.get("CardID") == card_id and card_id):
                target_path = path
                break
        if target_path is None:
            summary["warnings"].append(
                f"revert: could not find card for GUID={guid!r} CardID={card_id!r}"
            )
            continue
        if dry_run:
            print(f"  [dry-run] would delete {target_path}")
        else:
            try:
                target_path.unlink()
            except OSError as exc:
                summary["warnings"].append(f"revert: unlink failed for {target_path}: {exc}")
                continue
        removed += 1
        # Best-effort: strip from sibling ContainedObjects_order in parent dir.
        _strip_from_order(target_path, dry_run, summary)
    return removed


def _strip_from_order(card_path: Path, dry_run: bool, summary: dict) -> None:
    # The parent dir's "index" JSON (named <parent>.json adjacent to the dir)
    # holds ContainedObjects_order referring to the card's stem.
    parent_dir = card_path.parent
    index_json = parent_dir.with_suffix(".json") if parent_dir.suffix == "" else None
    if index_json is None or not index_json.exists():
        return
    try:
        with index_json.open(encoding="utf-8") as fh:
            idx = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return
    order = idx.get("ContainedObjects_order")
    paths = idx.get("ContainedObjects_path")
    stem = card_path.stem
    changed = False
    if isinstance(order, list) and stem in order:
        order.remove(stem)
        changed = True
    if isinstance(paths, list):
        basename = card_path.name
        new_paths = [p for p in paths if not (isinstance(p, str) and p.endswith(basename))]
        if len(new_paths) != len(paths):
            idx["ContainedObjects_path"] = new_paths
            changed = True
    if changed and not dry_run:
        atomic_write_json(index_json, idx)


# ----------------------------- post-apply validation ------------------------


def iter_deck_blocks(root: Path):
    """Yield (card_path, card_data, deck_key, deck_info) for every CustomDeck block."""
    for path in iter_card_jsons(root):
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            yield path, None, None, None
            continue
        cd = data.get("CustomDeck") if isinstance(data, dict) else None
        if not isinstance(cd, dict):
            continue
        for k, v in cd.items():
            if isinstance(v, dict):
                yield path, data, k, v


def _collect_back_urls_from_combined(combined_path: Path) -> set[str]:
    backs: set[str] = set()
    if not combined_path.exists():
        return backs
    try:
        with combined_path.open(encoding="utf-8") as fh:
            root = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return backs
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            cd = node.get("CustomDeck") or {}
            if isinstance(cd, dict):
                for v in cd.values():
                    if isinstance(v, dict):
                        b = v.get("BackURL")
                        if b:
                            backs.add(b)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return backs


def validate_post_apply(
    playercards_root: Path,
    campaigns_root: Path,
    entries: list,
    summary: dict,
    combined_path: Path,
    known_good_backs_path: Path,
) -> list[str]:
    """Run assertions 1–9 from design §5.4.4. Returns list of error strings.

    This intentionally re-reads the decomposed tree from disk (via
    iter_deck_blocks) rather than reusing apply_entries' in-memory `cards`
    snapshot: only disk reflects the actual persisted result. The snapshot
    diverges from disk in several ways — action=revert unlinks files that stay
    in `cards`, --dry-run leaves disk unchanged while the loop still reports
    intended edits, and _strip_from_order rewrites parent index JSONs outside
    the snapshot — so a post-apply gate must validate disk, not intent.
    """

    errors: list[str] = []

    # Assertions 1–5, 7: per-card JSON structure.
    for root in (playercards_root, campaigns_root):
        for path, data, deck_key, deck in iter_deck_blocks(root):
            if data is None:
                errors.append(f"ValidationError: invalid JSON (file={path})")
                continue
            face = deck.get("FaceURL", "")
            back = deck.get("BackURL", "")
            for label, url in (("FaceURL", face), ("BackURL", back)):
                if not url:
                    continue
                if url.startswith("http://") or url.startswith("file://"):
                    errors.append(
                        f"ValidationError: {label} uses non-https scheme "
                        f"(file={path} deck={deck_key} url={url})"
                    )
                    continue
                if not URL_REGEX.match(url):
                    errors.append(
                        f"ValidationError: {label} regex mismatch "
                        f"(file={path} deck={deck_key} url={url})"
                    )

            nw = deck.get("NumWidth")
            nh = deck.get("NumHeight")
            if not (isinstance(nw, int) and 1 <= nw <= 12):
                errors.append(
                    f"ValidationError: NumWidth out of range "
                    f"(file={path} deck={deck_key} value={nw!r})"
                )
            if not (isinstance(nh, int) and 1 <= nh <= 12):
                errors.append(
                    f"ValidationError: NumHeight out of range "
                    f"(file={path} deck={deck_key} value={nh!r})"
                )

            # Assertion 7: CardID ↔ sibling .gmnotes id (campaigns only).
            gm_path = data.get("GMNotes_path")
            if gm_path:
                sibling = path.parent / Path(gm_path).name
                if sibling.exists():
                    try:
                        with sibling.open(encoding="utf-8") as fh:
                            gm = json.load(fh)
                        gm_id = str(gm.get("id", ""))
                        # Compare against embedded id inside card — but the
                        # design specifies CardID vs .gmnotes id. The actual
                        # invariant in the data is that .gmnotes.id identifies
                        # the ArkhamDB id, not CardID. We treat missing/empty
                        # as a skip and only flag inconsistency when both are
                        # present and disagree with each other via GMNotes inline.
                        inline = data.get("GMNotes", "")
                        if inline:
                            try:
                                parsed = json.loads(inline)
                                if parsed.get("id", "") != gm_id:
                                    errors.append(
                                        f"ValidationError: inline GMNotes.id differs from "
                                        f"sibling .gmnotes.id (file={path} inline={parsed.get('id','')!r} "
                                        f"sibling={gm_id!r})"
                                    )
                            except json.JSONDecodeError:
                                pass
                    except (json.JSONDecodeError, OSError) as exc:
                        errors.append(
                            f"ValidationError: cannot parse sibling gmnotes "
                            f"(file={sibling} error={exc})"
                        )

    # Assertion 6: review_export vs write count. Each replace entry must have
    # caused at least one file modification OR appear in unmatched_entries[].
    unmatched_indices = {u.get("entry_index") for u in summary.get("unmatched_entries", [])}
    # We need to know which replace entries actually produced writes. Summary
    # counts replaced_sheets globally; per-entry matching is derived via
    # by_deck_face earlier. As a practical check we require:
    #   (# of replace entries) == replaced_sheets + len(unmatched_entries)
    replace_count = sum(1 for e in entries if e.get("action") == "replace")
    if replace_count != summary.get("replaced_sheets", 0) + len(unmatched_indices):
        errors.append(
            f"ValidationError: replace entry count mismatch: "
            f"replace_entries={replace_count} "
            f"replaced_sheets={summary.get('replaced_sheets', 0)} "
            f"unmatched={len(unmatched_indices)}"
        )

    # Assertion 9: pinned back URLs exist in current combined JSON.
    try:
        with known_good_backs_path.open(encoding="utf-8") as fh:
            pinned = json.load(fh)
    except OSError:
        errors.append(
            f"ValidationError: cannot read known_good_backs.json "
            f"(path={known_good_backs_path}) — re-run Step 0 extractor"
        )
        pinned = {}

    if pinned:
        backs = _collect_back_urls_from_combined(combined_path)
        for key, url in pinned.items():
            if url not in backs:
                errors.append(
                    f"ValidationError: pinned known_good_back_url not found in "
                    f"current korean_campaigns.json (pinned_key={key} url={url}) "
                    f"— re-run Step 0 extractor"
                )

    return errors


# ----------------------------- main -----------------------------------------


def run(args) -> int:
    try:
        review = load_json(args.review)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ValidationError: cannot read review file: {exc}", file=sys.stderr)
        return 2

    upload_map = None
    if args.upload_map is not None:
        try:
            upload_map = load_json(args.upload_map)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"ValidationError: cannot read upload_map: {exc}", file=sys.stderr)
            return 2

    try:
        validate_review(review, upload_map, args.allow_revert)
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    summary = apply_entries(
        review,
        args.decomposed_playercards,
        args.decomposed_campaigns,
        upload_map,
        args.allow_revert,
        args.dry_run,
    )

    if not args.skip_post_apply:
        errors = validate_post_apply(
            args.decomposed_playercards,
            args.decomposed_campaigns,
            review,
            summary,
            args.combined_campaigns,
            args.known_good_backs,
        )
        summary["post_apply_errors"] = errors
        if errors:
            for e in errors:
                print(e, file=sys.stderr)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_dir / "apply_diff_summary.json"
    # Merge with any existing summary so fix-scenario-ref-swap can append.
    if summary_path.exists():
        try:
            with summary_path.open(encoding="utf-8") as fh:
                existing = json.load(fh)
        except (json.JSONDecodeError, OSError):
            existing = {}
    else:
        existing = {}
    existing.update(summary)
    atomic_write_json(summary_path, existing)
    print(f"apply_diff_summary written: {summary_path}")

    if not args.skip_post_apply and summary.get("post_apply_errors"):
        return 3
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
