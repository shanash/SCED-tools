#!/usr/bin/env python3
"""
Validate review_decisions.json against candidates_index.json.

Usage:
  validate-decisions.py
    [--decisions PATH] [--candidates PATH] [--output-dir PATH] [--strict]

Exit codes:
  0 OK
  1 warnings (URL fail, cohort conflict, undecided > 0 — non-strict mode)
  30 schema mismatch
  31 invalid choice or arkham_id
  32 cohort conflict (--strict) OR undecided > 0 (--strict)
  33 tampered face_url/back_url
  34 url_status not ok for chosen candidate
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_DECISIONS = SCRIPTS_DIR / "output" / "korean-image-review" / "review_decisions.json"
DEFAULT_CANDIDATES = SCRIPTS_DIR / "output" / "korean-image-review" / "candidates_index.json"
DEFAULT_OUTPUT_DIR = SCRIPTS_DIR / "output" / "korean-image-review"
VALID_CHOICES = {"en", "ko", "v0", "v1", "v2", "skip"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Validate review decisions against candidates index."
    )
    p.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    p.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--strict", action="store_true",
                   help="Treat cohort conflicts and undecided cards as fatal errors.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # Load both files
    with args.decisions.open(encoding="utf-8") as fh:
        decisions_data = json.load(fh)
    with args.candidates.open(encoding="utf-8") as fh:
        candidates_data = json.load(fh)

    # 1. Schema version match
    dec_schema = decisions_data.get("schema_version")
    cand_schema = candidates_data.get("schema_version")
    if dec_schema != cand_schema:
        print(
            f"Schema version mismatch: decisions={dec_schema}, candidates={cand_schema}",
            file=sys.stderr,
        )
        sys.exit(30)

    # Build candidates lookup
    cards_by_id: dict[str, dict] = {
        c["arkham_id"]: c for c in candidates_data.get("cards", [])
    }
    total_cards = len(cards_by_id)

    decisions_list = decisions_data.get("decisions", [])

    warnings = []
    errors: list[dict] = []
    tampered: list[dict] = []
    url_fail: list[dict] = []
    cohort_conflicts: list[dict] = []

    # Build cohort groups for conflict detection
    cohort_chosen_face: dict[str, dict] = {}  # cohort -> {face_url: [arkham_id]}

    decided_ids: set[str] = set()
    skipped_explicit: int = 0
    by_choice: dict[str, int] = {}

    for dec in decisions_list:
        arkham_id = dec.get("arkham_id", "")
        choice = dec.get("choice")

        # 2. Check arkham_id exists in candidates
        if arkham_id not in cards_by_id:
            errors.append({"arkham_id": arkham_id, "error": "not found in candidates_index"})
            continue

        # 3. Check valid choice
        if choice not in VALID_CHOICES:
            errors.append({"arkham_id": arkham_id, "error": f"invalid choice: {choice!r}"})
            continue

        card = cards_by_id[arkham_id]
        by_choice[choice] = by_choice.get(choice, 0) + 1

        if choice == "skip":
            skipped_explicit += 1
            decided_ids.add(arkham_id)
            continue

        decided_ids.add(arkham_id)

        # Find chosen candidate
        cand = next(
            (c for c in card.get("candidates", []) if c.get("version") == choice),
            None,
        )
        if cand is None or not cand.get("available"):
            errors.append({
                "arkham_id": arkham_id,
                "error": f"chosen version {choice!r} not available in candidates",
            })
            continue

        # 4. Verify face_url / back_url integrity
        dec_face = dec.get("face_url")
        dec_back = dec.get("back_url")
        cand_face = cand.get("face_url")
        cand_back = cand.get("back_url")

        if dec_face != cand_face or dec_back != cand_back:
            tampered.append({
                "arkham_id": arkham_id,
                "choice": choice,
                "expected_face": cand_face,
                "got_face": dec_face,
                "expected_back": cand_back,
                "got_back": dec_back,
            })

        # 5. url_status check
        url_status = cand.get("url_status")
        if url_status is not None:
            face_status = (url_status.get("face") or {}).get("status")
            back_status = (url_status.get("back") or {}).get("status")
            if face_status != "ok" or back_status != "ok":
                url_fail.append({
                    "arkham_id": arkham_id,
                    "choice": choice,
                    "face_status": face_status,
                    "back_status": back_status,
                })

        # Cohort conflict tracking
        cohort = card.get("sheet_cohort_v2")
        if cohort and choice in ("v0", "v1", "v2"):
            chosen_face = cand.get("face_url", "")
            if cohort not in cohort_chosen_face:
                cohort_chosen_face[cohort] = {}
            cohort_chosen_face[cohort].setdefault(chosen_face, []).append(arkham_id)

    # 5. Cohort conflict detection
    for cohort, face_groups in cohort_chosen_face.items():
        if len(face_groups) > 1:
            cohort_conflicts.append({
                "cohort": cohort,
                "conflicting_choices": {
                    face: ids for face, ids in face_groups.items()
                },
            })

    undecided_count = total_cards - len(decided_ids)
    undecided_ids = sorted(set(cards_by_id.keys()) - decided_ids)

    # Build report
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": dec_schema,
        "total_cards": total_cards,
        "decided_count": len(decided_ids),
        "skipped_explicit": skipped_explicit,
        "undecided_count": undecided_count,
        "by_choice": by_choice,
        "errors": errors,
        "tampered": tampered,
        "url_fail": url_fail,
        "cohort_conflicts": cohort_conflicts,
        "undecided_ids": undecided_ids[:50],  # cap to avoid huge output
        "warnings": warnings,
    }

    # Write JSON report
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_out = args.output_dir / "decisions_validation_report.json"
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write markdown report
    md_lines = [
        "# Decisions Validation Report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Total cards: {total_cards}",
        f"- Decided: {len(decided_ids)}",
        f"- Skipped (explicit choice=skip): {skipped_explicit}",
        f"- Undecided (absent from decisions): {undecided_count}",
        f"- By choice: {by_choice}",
        "",
    ]

    if errors:
        md_lines += ["## Errors", ""]
        for e in errors:
            md_lines.append(f"- `{e['arkham_id']}`: {e['error']}")
        md_lines.append("")

    if tampered:
        md_lines += ["## Tampered URL Warnings", ""]
        for t in tampered:
            md_lines.append(
                f"- `{t['arkham_id']}` ({t['choice']}): face_url mismatch"
            )
        md_lines.append("")

    if url_fail:
        md_lines += ["## URL Validation Failures", ""]
        for u in url_fail:
            md_lines.append(
                f"- `{u['arkham_id']}` ({u['choice']}): face={u['face_status']}, back={u['back_status']}"
            )
        md_lines.append("")

    if cohort_conflicts:
        md_lines += ["## Cohort Conflicts", ""]
        for cc in cohort_conflicts:
            md_lines.append(f"- Cohort `{cc['cohort']}`: {len(cc['conflicting_choices'])} distinct face URLs")
        md_lines.append("")

    if undecided_ids:
        md_lines += [f"## Undecided Cards (first 50 of {undecided_count})", ""]
        for uid in undecided_ids:
            md_lines.append(f"- {uid}")
        md_lines.append("")

    md_out = args.output_dir / "decisions_validation_report.md"
    md_out.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"Validation report written to {json_out} and {md_out}")

    # Determine exit code
    if errors:
        sys.exit(31)
    if tampered:
        sys.exit(33)
    if url_fail and args.strict:
        sys.exit(34)
    if url_fail:
        print(f"Warning: {len(url_fail)} URL validation failures (non-strict mode)", file=sys.stderr)

    if cohort_conflicts:
        if args.strict:
            sys.exit(32)
        print(f"Warning: {len(cohort_conflicts)} cohort conflicts (non-strict mode)", file=sys.stderr)

    if undecided_count > 0:
        if args.strict:
            sys.exit(32)
        print(f"Warning: {undecided_count} undecided cards (non-strict mode)", file=sys.stderr)

    if url_fail or cohort_conflicts or undecided_count > 0:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
