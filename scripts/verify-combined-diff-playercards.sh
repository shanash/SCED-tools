#!/usr/bin/env bash
# verify-combined-diff-playercards.sh
#
# Full-coverage diff verification for the Korean Player Cards combined JSON.
#
# For ALL decomposed player card files, verifies that the four override fields
# (Nickname, Description, FaceURL, BackURL) match the corresponding entry in
# the combined JSON, matched by GMNotes.id.
#
# Exit codes:
#   0  all checks pass
#   1  one or more field mismatches found

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

COMBINED_REL=".build/korean_playercards.json"
COMBINED_PATH="${REPO_ROOT}/SCED-downloads/${COMBINED_REL}"
DECOMPOSED_ROOT="${REPO_ROOT}/SCED-downloads/decomposed/language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI"

# Parse optional overrides.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --decomposed-root)
      DECOMPOSED_ROOT="$2"
      shift 2
      ;;
    --combined)
      COMBINED_PATH="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "${COMBINED_PATH}" ]]; then
  echo "ERROR: combined JSON not found at ${COMBINED_PATH}" >&2
  exit 1
fi

if [[ ! -d "${DECOMPOSED_ROOT}" ]]; then
  echo "ERROR: decomposed root not found at ${DECOMPOSED_ROOT}" >&2
  exit 1
fi

python3 - "${DECOMPOSED_ROOT}" "${COMBINED_PATH}" <<'PY'
import json
import sys
from pathlib import Path

decomposed_root = Path(sys.argv[1])
combined_path = Path(sys.argv[2])

with combined_path.open(encoding="utf-8") as fh:
    combined = json.load(fh)

# Build id → card mapping from combined ContainedObjects.
combined_by_id: dict[str, dict] = {}
for card in combined.get("ContainedObjects", []):
    raw = card.get("GMNotes", "")
    if not raw:
        continue
    try:
        gm = json.loads(raw)
    except json.JSONDecodeError:
        continue
    card_id = gm.get("id")
    if card_id:
        combined_by_id[card_id] = card

all_files = sorted(decomposed_root.rglob("*.json"))
file_count = 0
id_set: set[str] = set()
fail = False

for path in all_files:
    with path.open(encoding="utf-8") as fh:
        try:
            decomp = json.load(fh)
        except json.JSONDecodeError:
            print(f"Invalid JSON: {path}", file=sys.stderr)
            fail = True
            continue

    raw = decomp.get("GMNotes", "")
    if not raw:
        continue
    try:
        gm = json.loads(raw)
    except json.JSONDecodeError:
        print(f"Non-JSON GMNotes: {path}", file=sys.stderr)
        fail = True
        continue

    card_id = gm.get("id")
    if not card_id:
        continue

    file_count += 1
    id_set.add(card_id)

    combined_card = combined_by_id.get(card_id)
    if combined_card is None:
        print(
            f"id={card_id}, path={path}, field=combined_missing, "
            f"decomposed=present, combined=absent",
            file=sys.stderr,
        )
        fail = True
        continue

    # Extract FaceURL / BackURL from decomposed (first CustomDeck entry).
    decomp_deck = decomp.get("CustomDeck", {})
    decomp_deck_val = next(iter(decomp_deck.values()), {}) if decomp_deck else {}
    decomp_face = decomp_deck_val.get("FaceURL", "")
    decomp_back = decomp_deck_val.get("BackURL", "")

    # Extract FaceURL / BackURL from combined (first CustomDeck entry).
    comb_deck = combined_card.get("CustomDeck", {})
    comb_deck_val = next(iter(comb_deck.values()), {}) if comb_deck else {}
    comb_face = comb_deck_val.get("FaceURL", "")
    comb_back = comb_deck_val.get("BackURL", "")

    fields_to_check = [
        ("Nickname", decomp.get("Nickname", ""), combined_card.get("Nickname", "")),
        ("Description", decomp.get("Description", ""), combined_card.get("Description", "")),
        ("FaceURL", decomp_face, comb_face),
        ("BackURL", decomp_back, comb_back),
    ]

    for field, decomp_val, comb_val in fields_to_check:
        if decomp_val != comb_val:
            print(
                f"id={card_id}, path={path}, field={field}, "
                f"decomposed={decomp_val!r}, combined={comb_val!r}",
                file=sys.stderr,
            )
            fail = True

print(f"PASS: {file_count} file checks across {len(id_set)} unique ids verified")
sys.exit(1 if fail else 0)
PY
