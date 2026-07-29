#!/usr/bin/env python3
"""Selectively revert the 141 rebase-conflicted Korean Player Card image URLs
toward ``upstream/main`` per a reviewed decision manifest.

Part of the ``conflict-url-revert`` chore (.am/conflict-url-revert/design.md).
The 141 cards under ``Korean-PlayerCards.KoreanI/`` that conflicted during the
2026-06-27 rebase split into three groups, each with one action:

  - Group A (118, face already byte-identical to upstream) -> ``full_revert``:
    ``git checkout upstream/main -- <path>`` makes the card byte-identical to
    upstream (face was already identical; backs hold no Korean art).
  - Group B (5, face differs, 1x1 deck) -> a manual SAME/DIFF judgement:
      SAME -> ``full_revert`` (card becomes fully upstream)
      DIFF -> ``revert_back_only`` (keep our R2 FaceURL; set BackURL to upstream's
              1x1 value). Face and back slice independently on a 1x1 deck.
  - Group C (18, face differs, PACKED, UniqueBack=true) -> ``keep_as_is`` (no-op):
    reverting a packed card's BackURL to upstream's 1x1 URL would garbage-slice on
    the 10x6 / 6x6 grid (design decision 1). Untouched, recorded for audit.

Why NOT reuse ``apply-back-urls.py``: it pre-asserts the target is on its PACKED
deck with ``UniqueBack=true`` and exits 5 on a 1x1 target -- the exact opposite
shape of Group-B-DIFF (1x1, reverting TO a 1x1 back). We mirror its
``--backup-out`` / ``--revert`` / ``--dry-run`` conventions, not its code path.

Byte-fidelity: decomposed card files are 2-space-indented with NO trailing
newline. ``full_revert`` restores upstream's exact bytes via git. ``revert_back_only``
rewrites in place via the centralized ``sced_io.atomic_write_text`` (verbatim, no
trailing newline) fed a 2-space ``dumps_faithful`` string, so the diff is exactly
the one BackURL value -- NOT ``atomic_write_json[_batch]``, which would append a
spurious trailing newline (design §4.2 named the batch writer; the centralized
verbatim writer is the recorded refinement to avoid format churn).

Usage:
  # 1. Seed the manifest skeleton (A->full_revert, C->keep_as_is, B->pending):
  apply-revert-decisions.py --seed \
      --candidates output/upstream-vs-local-compare/candidates.txt \
      --repo-root  <abs path to SCED-downloads> \
      --decisions  output/upstream-vs-local-compare/revert-decisions.json

  # 2. (human) resolve each Group-B face_judgement SAME/DIFF in the manifest.

  # 3. Dry-run, then apply:
  apply-revert-decisions.py \
      --decisions  output/upstream-vs-local-compare/revert-decisions.json \
      --repo-root  <abs path to SCED-downloads> \
      --backup-out output/upstream-vs-local-compare/pre_revert_backup.json \
      [--dry-run]

  # 4. Undo:
  apply-revert-decisions.py \
      --decisions  output/upstream-vs-local-compare/revert-decisions.json \
      --repo-root  <abs path to SCED-downloads> \
      --revert     output/upstream-vs-local-compare/pre_revert_backup.json

Exit codes:
  0  OK
  2  unreadable / malformed / inconsistent manifest or input
  3  post-apply validation failure
  5  pre-assert failure (e.g. a revert_back_only target is not 1x1)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from atlas_patch import single_deck_key
from sced_io import atomic_write_text

BASE_REF = "upstream/main"
LANGPACK_PREFIX = (
    "decomposed/language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI/"
)
VALID_ACTIONS = {"full_revert", "revert_back_only", "keep_as_is"}
EXPECTED_COUNTS = {"A": 118, "B": 5, "C": 18}


# ----------------------------------------------------------------------------- io
def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"InputError: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def dumps_faithful(data) -> str:
    """Serialize matching the on-disk decomposed format: 2-space indent,
    ensure_ascii=False, NO trailing newline."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def is_safe_langpack_path(rel: str) -> bool:
    """True iff ``rel`` is inside the langpack dir with no traversal segment.

    Defense-in-depth on the manifest/backup trust boundary: the prefix check
    alone would still admit ``<prefix>/../../escape`` once joined to ``repo_root``
    or fed to ``git ... -- <path>``. Rejecting any ``..`` component closes that.
    """
    return rel.startswith(LANGPACK_PREFIX) and ".." not in Path(rel).parts


# ---------------------------------------------------------------------------- git
def git_show_json(repo_root: Path, rel_path: str):
    """Parse ``git show upstream/main:<rel_path>`` (exit 2 on failure)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{BASE_REF}:{rel_path}"],
            capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"InputError: git show {BASE_REF}:{rel_path} failed: {exc.stderr.strip()}",
              file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        print(f"InputError: upstream {rel_path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def git_checkout(repo_root: Path, rel_paths: list[str]) -> None:
    """``git checkout upstream/main -- <paths...>`` in one invocation."""
    if not rel_paths:
        return
    cmd = ["git", "-C", str(repo_root), "checkout", BASE_REF, "--", *rel_paths]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ApplyError: git checkout failed (rc={res.returncode}): "
              f"{res.stderr.strip()}", file=sys.stderr)
        sys.exit(3)


def git_unstage(repo_root: Path, rel_paths: list[str]) -> None:
    """Restore the index entries to HEAD (``git reset -q HEAD -- <paths>``).

    The forward full_revert stages files via ``git checkout``; on --revert we
    rewrite the working tree but must also unstage, else index and working tree
    disagree (``git status`` shows a phantom change; ``commit -a`` could drop it).
    """
    if not rel_paths:
        return
    res = subprocess.run(["git", "-C", str(repo_root), "reset", "-q", "HEAD", "--", *rel_paths],
                         capture_output=True, text=True)
    if res.returncode != 0:
        # Non-fatal: the working tree is already restored; only the index may stay
        # staged. Surface it (mirrors git_checkout's rc handling) rather than exit.
        print(f"Warning: git reset (unstage) failed (rc={res.returncode}): "
              f"{res.stderr.strip()} -- working tree restored but index may remain "
              f"staged; run 'git reset HEAD -- <paths>' manually.", file=sys.stderr)


def upstream_back_url(repo_root: Path, rel_path: str) -> str:
    up = git_show_json(repo_root, rel_path)
    dk = single_deck_key(up)
    return up["CustomDeck"][dk]["BackURL"]


# --------------------------------------------------------------------------- seed
def classify(repo_root: Path, rel_path: str):
    """Return (group, deck_key) for a card by comparing local vs upstream FaceURL
    and the local deck grid. A=face-same; B=face-diff & 1x1; C=face-diff & packed."""
    local = load_json(repo_root / rel_path)
    up = git_show_json(repo_root, rel_path)
    ldk = single_deck_key(local)
    lentry = local["CustomDeck"][ldk]
    uentry = up["CustomDeck"][single_deck_key(up)]
    face_same = lentry.get("FaceURL") == uentry.get("FaceURL")
    packed = (lentry.get("NumWidth") or 1) > 1 or (lentry.get("NumHeight") or 1) > 1
    if face_same:
        return "A", ldk
    return ("C" if packed else "B"), ldk


def run_seed(args) -> int:
    repo_root = args.repo_root.resolve()
    paths = [l.strip() for l in args.candidates.read_text().splitlines() if l.strip()]
    cards, counts = [], {"A": 0, "B": 0, "C": 0}
    for rel in paths:
        if not is_safe_langpack_path(rel):
            print(f"InputError: unsafe/outside-langpack candidate path: {rel}", file=sys.stderr)
            return 2
        group, deck_key = classify(repo_root, rel)
        counts[group] += 1
        entry = {"path": rel, "group": group}
        if group == "A":
            entry["action"] = "full_revert"
        elif group == "C":
            entry["action"] = "keep_as_is"
        else:  # B -> pending human judgement
            entry["action"] = None
            entry["face_judgement"] = None
            entry["deck_key"] = deck_key
        cards.append(entry)

    tip = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    doc = {
        "generated": args.date,
        "base_ref": BASE_REF,
        "korean_tip": tip,
        "cards": cards,
    }
    atomic_write_text(args.decisions, dumps_faithful(doc) + "\n")
    print(f"Seeded {len(cards)} cards -> {args.decisions}")
    print(f"  counts: A={counts['A']} B={counts['B']} C={counts['C']}")
    if counts != EXPECTED_COUNTS:
        print(f"  WARNING: counts differ from expected {EXPECTED_COUNTS}", file=sys.stderr)
    pending = [c["path"] for c in cards if c["group"] == "B"]
    print("  Group-B cards needing a SAME/DIFF judgement:")
    for p in pending:
        print(f"    - {Path(p).name}")
    return 0


# ----------------------------------------------------------------------- validate
def validate(doc) -> list[dict]:
    """Validate the manifest; return the card list or exit 2."""
    cards = doc.get("cards")
    if not isinstance(cards, list) or not cards:
        print("InputError: manifest 'cards' missing or empty", file=sys.stderr)
        sys.exit(2)
    counts = {"A": 0, "B": 0, "C": 0}
    seen = set()
    for c in cards:
        path = c.get("path", "")
        if not is_safe_langpack_path(path):
            print(f"InputError: unsafe/outside-langpack card path: {path!r}", file=sys.stderr)
            sys.exit(2)
        if path in seen:
            print(f"InputError: duplicate card path {path!r}", file=sys.stderr)
            sys.exit(2)
        seen.add(path)
        group = c.get("group")
        if group not in counts:
            print(f"InputError: bad group {group!r} for {path}", file=sys.stderr)
            sys.exit(2)
        counts[group] += 1
        action = c.get("action")
        if action not in VALID_ACTIONS:
            print(f"InputError: card {path} has unresolved/invalid action "
                  f"{action!r} (resolve Group-B judgements first)", file=sys.stderr)
            sys.exit(2)
        if group == "B":
            judgement = c.get("face_judgement")
            if judgement not in ("SAME", "DIFF"):
                print(f"InputError: Group-B {path} missing face_judgement SAME/DIFF",
                      file=sys.stderr)
                sys.exit(2)
            expect = "full_revert" if judgement == "SAME" else "revert_back_only"
            if action != expect:
                print(f"InputError: Group-B {path} judgement {judgement} requires "
                      f"action {expect}, got {action}", file=sys.stderr)
                sys.exit(2)
        if action == "revert_back_only" and not c.get("deck_key"):
            print(f"InputError: {path} revert_back_only needs deck_key", file=sys.stderr)
            sys.exit(2)
        if group == "C" and action != "keep_as_is":
            print(f"InputError: Group-C {path} must be keep_as_is", file=sys.stderr)
            sys.exit(2)
    total = sum(counts.values())
    print(f"Manifest OK: {total} cards (A={counts['A']} B={counts['B']} C={counts['C']})")
    if counts != EXPECTED_COUNTS:
        print(f"  WARNING: counts differ from expected {EXPECTED_COUNTS}", file=sys.stderr)
    return cards


# -------------------------------------------------------------------------- apply
def run_apply(args) -> int:
    repo_root = args.repo_root.resolve()
    doc = load_json(args.decisions)
    cards = validate(doc)

    full = [c for c in cards if c["action"] == "full_revert"]
    backonly = [c for c in cards if c["action"] == "revert_back_only"]
    keep = [c for c in cards if c["action"] == "keep_as_is"]

    # Pre-assert every back-only target is 1x1 (defense vs a mis-routed packed path)
    # and resolve the upstream BackURL. Writes nothing.
    backonly_plan = []  # (rel, path, deck_key, old_back, up_back, orig_text)
    for c in backonly:
        rel = c["path"]
        path = repo_root / rel
        local = load_json(path)
        deck_key = c["deck_key"]
        if deck_key not in local.get("CustomDeck", {}):
            print(f"PreAssertError: {rel}: deck_key {deck_key!r} absent", file=sys.stderr)
            return 5
        entry = local["CustomDeck"][deck_key]
        if entry.get("NumWidth") != 1 or entry.get("NumHeight") != 1:
            print(f"PreAssertError: {rel}: not 1x1 "
                  f"({entry.get('NumWidth')}x{entry.get('NumHeight')}) -- refusing "
                  f"back-only revert on a packed deck", file=sys.stderr)
            return 5
        up_back = upstream_back_url(repo_root, rel)
        backonly_plan.append(
            (rel, path, deck_key, entry.get("BackURL"), up_back, path.read_text(encoding="utf-8")))

    if args.dry_run:
        print(f"(dry-run) plan: {len(full)} full_revert, "
              f"{len(backonly)} revert_back_only, {len(keep)} keep_as_is")
        for rel, _p, dk, old, new, _t in backonly_plan:
            same = " (already upstream)" if old == new else ""
            print(f"  back-only {Path(rel).name} [{dk}]: {old} -> {new}{same}")
        print("(dry-run) no files written, no backup written.")
        return 0

    # ---- snapshot BEFORE any write (design §4.2 step 2 / apply-back-urls.py:263)
    backup_cards = {}
    for c in full:
        rel = c["path"]
        backup_cards[rel] = {
            "action": "full_revert",
            "content_text": (repo_root / rel).read_text(encoding="utf-8"),
        }
    for rel, _path, deck_key, old_back, _up, _orig in backonly_plan:
        backup_cards[rel] = {
            "action": "revert_back_only",
            "deck_key": deck_key,
            "back_url": old_back,
        }
    keep_snapshot = {}  # rel -> bytes-at-start (design §4.2 step 5: assert unchanged)
    for c in keep:
        rel = c["path"]
        keep_snapshot[rel] = (repo_root / rel).read_text(encoding="utf-8")
        backup_cards[rel] = {"action": "keep_as_is"}
    backup_doc = {
        "schema_version": "1.0.0",
        "base_ref": BASE_REF,
        "repo_root": str(repo_root),
        "cards": backup_cards,
    }
    atomic_write_text(args.backup_out, dumps_faithful(backup_doc) + "\n")
    print(f"Backup written: {args.backup_out}")

    # ---- full_revert: one batched git checkout
    git_checkout(repo_root, [c["path"] for c in full])
    print(f"full_revert: {len(full)} card(s) checked out from {BASE_REF}.")

    # ---- revert_back_only: byte-faithful in-place BackURL rewrite. Re-parse the
    # text captured during planning (no second disk read) so the only change is
    # the BackURL value.
    changed = 0
    for rel, path, deck_key, old_back, up_back, orig in backonly_plan:
        local = json.loads(orig)
        local["CustomDeck"][deck_key]["BackURL"] = up_back
        atomic_write_text(path, dumps_faithful(local))
        if old_back != up_back:
            changed += 1
    print(f"revert_back_only: {len(backonly_plan)} card(s) rewritten "
          f"(BackURL changed on {changed}).")

    # ---- keep_as_is: design §4.2 step 5 — assert untouched by this run (guards
    # against a mis-routed path that collided with a full/back-only write).
    for rel, before in keep_snapshot.items():
        if (repo_root / rel).read_text(encoding="utf-8") != before:
            print(f"GateError: keep_as_is card was modified during apply: {rel}",
                  file=sys.stderr)
            return 3
    print(f"keep_as_is: {len(keep)} card(s) untouched.")
    return 0


# ------------------------------------------------------------------------- revert
def run_revert(args) -> int:
    repo_root = args.repo_root.resolve()
    backup = load_json(args.revert)
    snapshots = backup.get("cards", {})
    if not isinstance(snapshots, dict):
        print("InputError: backup 'cards' is not an object", file=sys.stderr)
        return 2
    full_n = back_n = 0
    full_restored = []  # rel paths to unstage after restore (forward apply staged them)
    for rel, snap in snapshots.items():
        if not is_safe_langpack_path(rel):
            print(f"InputError: unsafe/outside-langpack backup path: {rel!r}", file=sys.stderr)
            return 2
        action = snap.get("action")
        path = repo_root / rel
        if action == "full_revert":
            content = snap.get("content_text")
            if content is None:
                print(f"InputError: backup entry for {rel!r} missing 'content_text'",
                      file=sys.stderr)
                return 2
            if args.dry_run:
                full_n += 1
                continue
            atomic_write_text(path, content)
            full_restored.append(rel)
            full_n += 1
        elif action == "revert_back_only":
            deck_key = snap.get("deck_key")
            if deck_key is None or "back_url" not in snap:
                print(f"InputError: backup entry for {rel!r} needs 'deck_key' + 'back_url'",
                      file=sys.stderr)
                return 2
            local = load_json(path)
            if deck_key not in local.get("CustomDeck", {}):
                print(f"InputError: deck_key {deck_key!r} absent in {rel}", file=sys.stderr)
                return 2
            if args.dry_run:
                back_n += 1
                continue
            local["CustomDeck"][deck_key]["BackURL"] = snap["back_url"]
            atomic_write_text(path, dumps_faithful(local))
            back_n += 1
        # keep_as_is: nothing to undo
    if not args.dry_run:
        git_unstage(repo_root, full_restored)
    prefix = "(dry-run) would restore" if args.dry_run else "Restored"
    print(f"{prefix}: {full_n} full_revert + {back_n} revert_back_only card(s).")
    return 0


# --------------------------------------------------------------------------- main
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-root", type=Path, required=True,
                   help="Absolute path to the SCED-downloads repo root.")
    p.add_argument("--decisions", type=Path, required=True,
                   help="revert-decisions.json manifest (seeded then human-resolved).")
    p.add_argument("--seed", action="store_true",
                   help="Generate the manifest skeleton from --candidates and exit.")
    p.add_argument("--candidates", type=Path,
                   help="candidates.txt (141 repo-relative paths); required with --seed.")
    p.add_argument("--date", default="2026-06-27",
                   help="'generated' date stamped into a seeded manifest.")
    p.add_argument("--backup-out", type=Path,
                   help="Where to write the pre-revert backup (required for apply).")
    p.add_argument("--revert", type=Path, default=None,
                   help="Undo using this backup, then exit.")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate + print the plan; write nothing.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.seed:
        if not args.candidates:
            print("InputError: --seed requires --candidates", file=sys.stderr)
            return 2
        return run_seed(args)
    if args.revert is not None:
        return run_revert(args)
    if not args.dry_run and not args.backup_out:
        print("InputError: apply requires --backup-out (or use --dry-run)", file=sys.stderr)
        return 2
    return run_apply(args)


if __name__ == "__main__":
    sys.exit(main())
