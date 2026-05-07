#!/usr/bin/env python3
"""
Extract fork-only Korean atlas/index manifest from SCED-downloads decomposed tree.

Walks the two fork-only commits' resulting decomposed paths
(Korean-PlayerCards.KoreanI + BarkhamHorror.kr_bark) and produces a 3-tier
deterministic snapshot under ``output/korean-atlas-extract/``:

  - atlas-data.cards.json   (per-card flattened lookup)
  - atlas-data.decks.json   (CustomDeck-grouped, atlas reorganization input)
  - manifest.json           (sha256 + git head + reproduction metadata)
  - review_decisions.snapshot.json  (deterministic copy if available)

Usage:
  extract-korean-atlases.py
    [--decomposed-root PATH] [--output PATH] [--dry-run]
    [--decisions PATH]

Exit codes:
  0  OK
  1  warnings (e.g. missing optional decisions snapshot, count drift)
  40 GMNotes invariant violation (missing/empty/malformed)
  41 grid mismatch within deck (CustomDeck NumWidth/NumHeight inconsistent)
  42 hash mismatch on atomic write verification
  43 missing decomposed file or empty CustomDeck
  44 unexpected category (neither player_cards nor campaigns_barkham)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
SCED_DOWNLOADS_PATH = REPO_ROOT / "SCED-downloads"
DEFAULT_DECOMPOSED_ROOT = SCED_DOWNLOADS_PATH / "decomposed"
DEFAULT_OUTPUT = SCRIPTS_DIR / "output" / "korean-atlas-extract"
DEFAULT_DECISIONS = SCRIPTS_DIR / "output" / "korean-image-review" / "review_decisions.json"

PLAYER_CARDS_SUBPATH = Path(
    "language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI"
)
BARKHAM_SUBPATH = Path(
    "language-pack/Korean - Campaigns/Korean-Campaigns.KoreanC/BarkhamHorror.kr_bark"
)

CATEGORY_PLAYER = "player_cards"
CATEGORY_BARKHAM = "campaigns_barkham"

FORK_ONLY_COMMITS = ["3e19617933", "a12115e96a"]
MERGE_BASE = "51c864c7c8a6fd421b0be09a7e1d6e2cad3e629a"
SCHEMA_VERSION = "1.0.0"
EXTRACTOR_VERSION = "1.0.0"
EXTRACTOR_SCRIPT = "extract-korean-atlases.py"

R2_HOST_FRAGMENT = "pub-05b4fa32b44341d797f5c66d59384724.r2.dev"
STEAM_HOST_FRAGMENT = "steamusercontent-a.akamaihd.net"

# Source-of-truth attribution: category-based mapping (R1 mitigation).
# Recorded explicitly in manifest as "commit_attribution" so future tooling can
# distinguish heuristic mapping vs git-blame attribution if introduced later.
COMMIT_ATTRIBUTION_VERSION = "category-based-1.0.0"
CATEGORY_TO_COMMIT = {
    CATEGORY_PLAYER: "3e19617933",
    CATEGORY_BARKHAM: "a12115e96a",
}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract fork-only Korean atlas/index snapshot from decomposed tree."
    )
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT,
                   help="Root of SCED-downloads/decomposed tree.")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help="Output directory for the 3-tier snapshot.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute everything but do not write any output files.")
    p.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS,
                   help="Optional review_decisions.json to copy as snapshot.")
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


def load_decomposed_paths(decomposed_root: Path) -> tuple[list[Path], list[Path]]:
    """Return (player_card_paths, barkham_card_paths) sorted deterministically.

    Player cards: rglob "*.json" under PLAYER_CARDS_SUBPATH (includes fan-out
    subdirectories for cards with main + signature/promo variants).
    Barkham: glob "Card.bk*.json" under BARKHAM_SUBPATH (excludes the parent
    metadata file BarkhamHorror.kr_bark.json which sits one level higher).
    """
    player_root = decomposed_root / PLAYER_CARDS_SUBPATH
    barkham_root = decomposed_root / BARKHAM_SUBPATH

    if not player_root.exists():
        print(
            f"Decomposed player cards root missing: {player_root}",
            file=sys.stderr,
        )
        sys.exit(43)
    if not barkham_root.exists():
        print(
            f"Decomposed Barkham root missing: {barkham_root}",
            file=sys.stderr,
        )
        sys.exit(43)

    player_paths = sorted(player_root.rglob("*.json"))
    barkham_paths = sorted(barkham_root.glob("Card.bk*.json"))
    return player_paths, barkham_paths


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


def parse_card_json(
    path: Path,
    category: str,
    decomposed_root: Path,
) -> dict:
    """Parse a single decomposed card JSON into a flattened cards[] entry.

    Exits with code 43 on missing CustomDeck, 40 on GMNotes violation,
    41 on non-coercible grid value.
    """
    with path.open(encoding="utf-8") as fh:
        obj = json.load(fh)

    arkham_id = _parse_gmnotes_id(obj, str(path))
    guid = obj.get("GUID", "")
    if not guid:
        print(
            f"Card invariant violation: missing GUID at {path}",
            file=sys.stderr,
        )
        sys.exit(43)

    custom_deck = obj.get("CustomDeck", {})
    if not custom_deck:
        print(
            f"Card invariant violation: empty CustomDeck at {path}",
            file=sys.stderr,
        )
        sys.exit(43)

    deck_id = list(custom_deck.keys())[0]
    deck_data = custom_deck[deck_id]

    face_url = deck_data.get("FaceURL", "")
    back_url = deck_data.get("BackURL", "")
    num_width = _coerce_int(deck_data.get("NumWidth", 0), str(path), "NumWidth")
    num_height = _coerce_int(deck_data.get("NumHeight", 0), str(path), "NumHeight")

    back_shared = STEAM_HOST_FRAGMENT in back_url

    decomposed_path_rel = str(path.relative_to(decomposed_root.parent))

    source_commit = CATEGORY_TO_COMMIT.get(category)
    if source_commit is None:
        print(
            f"Unexpected category {category!r} for {path}",
            file=sys.stderr,
        )
        sys.exit(44)

    return {
        "category": category,
        "arkham_id": arkham_id,
        "guid": str(guid),
        "deck_id": str(deck_id),
        "decomposed_path": decomposed_path_rel,
        "face_url": face_url,
        "back_url": back_url,
        "num_width": num_width,
        "num_height": num_height,
        "back_shared": back_shared,
        "source_commit": source_commit,
        # Composite atlas-grouping key: deck_id alone is not atlas-unique
        # (45 of 553 fork-only deck_ids map to multiple face_urls), so the
        # decks{} dict is keyed by f"{deck_id}|{sha1_prefix(face_url)}".
        "deck_composite": f"{deck_id}|{_hash_url(face_url)}",
    }


def build_cards_array(
    player_paths: list[Path],
    barkham_paths: list[Path],
    decomposed_root: Path,
) -> list[dict]:
    """Iterate paths in (category, arkham_id, guid) sorted order.

    Sort is applied AFTER parsing so card content drives ordering, not file
    name. This guarantees deterministic output independent of filesystem
    enumeration order.
    """
    cards: list[dict] = []
    for path in player_paths:
        cards.append(parse_card_json(path, CATEGORY_PLAYER, decomposed_root))
    for path in barkham_paths:
        cards.append(parse_card_json(path, CATEGORY_BARKHAM, decomposed_root))

    cards.sort(key=lambda c: (c["category"], c["arkham_id"], c["guid"]))
    return cards


def _hash_url(url: str) -> str:
    """16-char sha1 prefix used to disambiguate composite deck keys.

    16 hex chars = 64 bits. Birthday-collision probability for ~10k decks
    (future scale) is ~3e-12 — effectively zero. The prefix is identifier-only,
    not security-critical (sha1 is fine for non-adversarial disambiguation).
    """
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def build_decks_dict(cards: list[dict]) -> dict[str, dict]:
    """Group cards by composite key (deck_id, face_url); validate grid invariant.

    The TTS CustomDeck integer key (deck_id) is mod-local and NOT unique across
    atlases — survey of fork-only Korean Player Cards shows 45 of 553 deck_ids
    map to 2-3 distinct face_urls (different atlases reusing the same numeric
    deck_id). The atlas-grouping unit must therefore be (deck_id, face_url),
    not deck_id alone.

    Output dict key format: ``f"{deck_id}|{sha1_prefix(face_url)}"`` —
    deterministic, collision-resistant, and human-traceable. Each entry carries
    ``deck_id`` and ``face_url`` explicitly so downstream tooling does not need
    to parse the composite key.

    Within a composite group all members must share identical num_width/
    num_height (atlas grid is a property of the atlas itself). back_url is
    NOT enforced — UniqueBack cards on a shared face atlas legitimately have
    distinct back URLs (1 of 603 fork-only composite groups exhibits this).
    The deck entry's ``back_url`` field stores the first member's back, while
    ``back_urls_distinct`` exposes the count of unique backs in the group so
    downstream tooling can detect divergence without re-grouping.

    Host classification:
      - face_url contains R2 fragment → "r2.dev"
      - face_url contains Steam fragment → "steam-shared" if back_shared else "steamusercontent"
      - otherwise → "steamusercontent" (unknown host treated as steam-like)
    """
    grouped: dict[str, list[dict]] = {}
    for card in cards:
        composite = f"{card['deck_id']}|{_hash_url(card['face_url'])}"
        grouped.setdefault(composite, []).append(card)

    decks: dict[str, dict] = {}
    for composite in sorted(grouped.keys()):
        members = grouped[composite]
        first = members[0]
        for other in members[1:]:
            for field in ("face_url", "num_width", "num_height"):
                if other[field] != first[field]:
                    print(
                        f"Deck invariant violation: composite={composite} field={field} "
                        f"mismatch between guid={first['guid']} ({first[field]!r}) and "
                        f"guid={other['guid']} ({other[field]!r})",
                        file=sys.stderr,
                    )
                    sys.exit(41)

        face_url = first["face_url"]
        back_shared = first["back_shared"]
        if R2_HOST_FRAGMENT in face_url:
            host = "r2.dev"
        elif STEAM_HOST_FRAGMENT in face_url:
            host = "steam-shared" if back_shared else "steamusercontent"
        else:
            # Unknown host — default to steamusercontent (extracted data only,
            # no remediation in this script).
            host = "steamusercontent"

        distinct_backs = sorted({m["back_url"] for m in members})

        decks[composite] = {
            "deck_id": first["deck_id"],
            "face_url": face_url,
            "back_url": first["back_url"],
            "back_urls_distinct": len(distinct_backs),
            "num_width": first["num_width"],
            "num_height": first["num_height"],
            "card_count": len(members),
            "back_shared": back_shared,
            "card_ids": sorted({m["arkham_id"] for m in members}),
            "card_guids": sorted({m["guid"] for m in members}),
            "host": host,
        }

    return decks


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_git_head(repo_path: Path) -> tuple[str | None, bool]:
    """Return (head_sha, ok). On failure returns (None, False) with stderr warn.

    Uses subprocess with list args + shell=False (OWASP injection mitigation).
    Falls back to reading .git/HEAD when subprocess git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha, True
        print(
            f"git rev-parse HEAD failed for {repo_path}: rc={result.returncode} "
            f"stderr={result.stderr.strip()!r}",
            file=sys.stderr,
        )
    except FileNotFoundError:
        # git binary unavailable. Try .git/HEAD fallback.
        print(
            f"git binary unavailable; falling back to .git/HEAD for {repo_path}",
            file=sys.stderr,
        )

    head_file = repo_path / ".git" / "HEAD"
    if not head_file.exists():
        return None, False
    head_text = head_file.read_text(encoding="utf-8").strip()
    if head_text.startswith("ref: "):
        ref_path = repo_path / ".git" / head_text[5:].strip()
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip(), True
        return None, False
    return head_text, True


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


def copy_decisions_snapshot(decisions_path: Path, output_dir: Path) -> bool:
    """Copy review_decisions.json deterministically as snapshot.

    Returns True on success, False if the source is absent (warn → exit 1).
    """
    if not decisions_path.exists():
        print(
            f"review_decisions snapshot skipped: source not found at {decisions_path}",
            file=sys.stderr,
        )
        return False
    target = output_dir / "review_decisions.snapshot.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    # shutil.copyfile is byte-for-byte deterministic.
    shutil.copyfile(decisions_path, target)
    return True


def build_manifest(
    cards: list[dict],
    decks: dict[str, dict],
    output_dir: Path,
    output_files: list[tuple[Path, str, dict]],
    head_sha: str | None,
    head_ok: bool,
    decisions_snapshot_present: bool,
    args: argparse.Namespace,
) -> dict:
    """Assemble manifest.json content.

    output_files entries are (path, kind, extra_dict) where kind is "cards"
    or "decks" — sha256 is recomputed on the actual file post-write so the
    manifest reflects what was committed to disk.

    Note: ``totals.grid_changed_cards`` is intentionally an empty array on the
    first extraction. The 8-card grid-change list referenced in the analysis
    requires comparison against an upstream baseline that this snapshot does
    not load. Future revisions can populate it without breaking schema.
    """
    by_category: dict[str, int] = {}
    for c in cards:
        by_category[c["category"]] = by_category.get(c["category"], 0) + 1

    atlases_r2 = sum(1 for d in decks.values() if d["host"] == "r2.dev")
    atlases_steam_shared = sum(
        1 for d in decks.values() if d["host"] == "steam-shared"
    )
    atlases_steam_user = sum(
        1 for d in decks.values() if d["host"] == "steamusercontent"
    )

    outputs: dict[str, dict] = {}
    for path, kind, extra in output_files:
        sha = compute_sha256(path)
        entry: dict = {"sha256": sha}
        entry.update(extra)
        outputs[path.name] = entry

    # Reproduction command — relative to SCED-tools.
    cmd = (
        f"python3 scripts/{EXTRACTOR_SCRIPT} "
        f"--decomposed-root {DEFAULT_DECOMPOSED_ROOT.relative_to(REPO_ROOT.parent)} "
        f"--output {output_dir.relative_to(REPO_ROOT)}"
    )

    references = {
        "candidates_index": "../korean-image-review/candidates_index.json",
        "review_decisions_snapshot": (
            "./review_decisions.snapshot.json" if decisions_snapshot_present else None
        ),
    }

    manifest: dict = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "extractor": {
            "script": EXTRACTOR_SCRIPT,
            "version": EXTRACTOR_VERSION,
            "command": cmd,
            "commit_attribution": COMMIT_ATTRIBUTION_VERSION,
        },
        "source": {
            "repo": "shanash/SCED-downloads",
            "branch": "main",
            "head": head_sha,
            "head_resolved": head_ok,
            "merge_base_with_upstream": MERGE_BASE,
            "fork_only_commits": FORK_ONLY_COMMITS,
        },
        "outputs": outputs,
        "categories": by_category,
        "totals": {
            "atlases_r2": atlases_r2,
            "atlases_steam_shared": atlases_steam_shared,
            "atlases_steamusercontent": atlases_steam_user,
            "deck_count": len(decks),
            "card_count": len(cards),
            # First-run extraction cannot identify grid_changed cards without
            # an upstream baseline diff. Field present (empty) for schema
            # forward-compat (1.x.0).
            "grid_changed_cards": [],
        },
        "references": references,
    }
    return manifest


def main(argv=None) -> int:
    args = parse_args(argv)

    output_dir: Path = args.output
    decomposed_root: Path = args.decomposed_root

    if not decomposed_root.exists():
        print(
            f"--decomposed-root does not exist: {decomposed_root}",
            file=sys.stderr,
        )
        return 43

    player_paths, barkham_paths = load_decomposed_paths(decomposed_root)
    print(
        f"Loaded {len(player_paths)} player card files + "
        f"{len(barkham_paths)} Barkham files."
    )

    cards = build_cards_array(player_paths, barkham_paths, decomposed_root)
    decks = build_decks_dict(cards)

    by_category: dict[str, int] = {}
    for c in cards:
        by_category[c["category"]] = by_category.get(c["category"], 0) + 1

    print(
        f"Built {len(cards)} cards across {len(decks)} decks "
        f"(by category: {by_category})."
    )

    warnings_emitted = False

    if args.dry_run:
        print("(dry-run mode — no files written)")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    cards_doc = {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commits": FORK_ONLY_COMMITS,
            "merge_base": MERGE_BASE,
            "total_cards": len(cards),
            "categories": by_category,
        },
        "cards": cards,
    }
    decks_doc = {
        "schema_version": SCHEMA_VERSION,
        "decks": decks,
    }

    cards_path = output_dir / "atlas-data.cards.json"
    decks_path = output_dir / "atlas-data.decks.json"

    atomic_write_json(cards_path, cards_doc)
    atomic_write_json(decks_path, decks_doc)

    decisions_present = copy_decisions_snapshot(args.decisions, output_dir)
    if not decisions_present:
        warnings_emitted = True

    head_sha, head_ok = get_git_head(SCED_DOWNLOADS_PATH)
    if not head_ok:
        warnings_emitted = True

    output_files = [
        (cards_path, "cards", {"card_count": len(cards)}),
        (decks_path, "decks", {"deck_count": len(decks)}),
    ]
    manifest = build_manifest(
        cards, decks, output_dir, output_files,
        head_sha, head_ok, decisions_present, args,
    )
    manifest_path = output_dir / "manifest.json"
    atomic_write_json(manifest_path, manifest)

    # Re-read both data outputs and verify sha256 matches manifest claim.
    declared = manifest["outputs"]
    for path in (cards_path, decks_path):
        actual = compute_sha256(path)
        expected = declared[path.name]["sha256"]
        if actual != expected:
            print(
                f"sha256 mismatch on re-read: {path.name} "
                f"declared={expected!r} actual={actual!r}",
                file=sys.stderr,
            )
            return 42

    print(f"Wrote {cards_path}  sha256={declared[cards_path.name]['sha256'][:16]}...")
    print(f"Wrote {decks_path}  sha256={declared[decks_path.name]['sha256'][:16]}...")
    if decisions_present:
        print(f"Wrote {output_dir / 'review_decisions.snapshot.json'}")
    print(f"Wrote {manifest_path}")
    print(
        f"Summary: cards={len(cards)} decks={len(decks)} "
        f"r2={manifest['totals']['atlases_r2']} "
        f"steam_shared={manifest['totals']['atlases_steam_shared']} "
        f"steamuser={manifest['totals']['atlases_steamusercontent']}"
    )

    return 1 if warnings_emitted else 0


if __name__ == "__main__":
    sys.exit(main())
