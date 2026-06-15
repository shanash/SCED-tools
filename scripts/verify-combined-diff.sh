#!/usr/bin/env bash
# verify-combined-diff.sh
#
# Enforces design §5.5.3 diff-count equality on
# SCED-downloads/downloadable/korean_campaigns.json after Phase 5 rebuild:
#
#   URL_DIFF_COUNT == 2 * sheets_replaced + 2 * swap_applied
#
# Also runs assertion #9 from §5.4.4 (pinned-back URL existence in current
# combined JSON) via a small Python helper.
#
# Exit codes:
#   0  all checks pass
#   1  URL_DIFF_COUNT != expected
#   2  apply_diff_summary.json missing or invalid
#   3  assertion #9 failed (pinned back URL not in current combined JSON)
#   4  git invocation failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SUMMARY_PATH="${SUMMARY_PATH:-${SCRIPT_DIR}/output/korean-image-apply/apply_diff_summary.json}"
COMBINED_REL="downloadable/korean_campaigns.json"
COMBINED_PATH="${REPO_ROOT}/SCED-downloads/${COMBINED_REL}"
KNOWN_GOOD_BACKS="${KNOWN_GOOD_BACKS:-${REPO_ROOT}/SCED-tools/tests/fixtures/korean-image-apply/known_good_backs.json}"

if [[ ! -f "${SUMMARY_PATH}" ]]; then
  echo "ERROR: apply_diff_summary.json not found at ${SUMMARY_PATH}" >&2
  exit 2
fi

if [[ ! -f "${COMBINED_PATH}" ]]; then
  echo "ERROR: combined JSON not found at ${COMBINED_PATH}" >&2
  exit 2
fi

# Pull counts from apply_diff_summary.json via python (portable, avoids jq dep).
read -r SHEETS_REPLACED SWAP_APPLIED <<EOF_SUMMARY
$(python3 - "${SUMMARY_PATH}" <<'PY'
import json, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as fh:
    data = json.load(fh)
print(int(data.get("replaced_sheets", 0)), int(data.get("swap_applied", 0)))
PY
)
EOF_SUMMARY

EXPECTED=$(( 2 * SHEETS_REPLACED + 2 * SWAP_APPLIED ))

# Run git diff inside SCED-downloads repo.
cd "${REPO_ROOT}/SCED-downloads" || { echo "ERROR: SCED-downloads not found" >&2; exit 4; }

if ! URL_DIFF_COUNT=$(git diff -- "${COMBINED_REL}" 2>/dev/null \
    | grep -cE '"(FaceURL|BackURL)":') ; then
  # grep returns 1 on zero matches — that's legitimate.
  URL_DIFF_COUNT=0
fi

echo "replaced_sheets=${SHEETS_REPLACED}"
echo "swap_applied=${SWAP_APPLIED}"
echo "URL_DIFF_COUNT=${URL_DIFF_COUNT}"
echo "expected=${EXPECTED}"

if [[ "${URL_DIFF_COUNT}" -ne "${EXPECTED}" ]]; then
  echo "FAIL: URL_DIFF_COUNT (${URL_DIFF_COUNT}) != expected (${EXPECTED})" >&2
  echo "       If structural (non-URL) key diff is present, try rebuilding with" >&2
  echo "       TTSModManager (B-prime) instead of --pure-python." >&2
  exit 1
fi

# Assertion #9: pinned back URLs must exist in current combined JSON.
python3 - "${COMBINED_PATH}" "${KNOWN_GOOD_BACKS}" <<'PY'
import json, sys
combined_path, pinned_path = sys.argv[1], sys.argv[2]
with open(combined_path, encoding="utf-8") as fh:
    root = json.load(fh)
with open(pinned_path, encoding="utf-8") as fh:
    pinned = json.load(fh)

backs = set()
stack = [root]
while stack:
    n = stack.pop()
    if isinstance(n, dict):
        cd = n.get("CustomDeck") or {}
        if isinstance(cd, dict):
            for v in cd.values():
                if isinstance(v, dict):
                    b = v.get("BackURL")
                    if b:
                        backs.add(b)
        for v in n.values():
            if isinstance(v, (dict, list)):
                stack.append(v)
    elif isinstance(n, list):
        stack.extend(n)

failed = False
for key, url in pinned.items():
    if url not in backs:
        print(
            f"ValidationError: pinned known_good_back_url not found in current "
            f"korean_campaigns.json (pinned_key={key} url={url}) "
            f"— re-run Step 0 extractor",
            file=sys.stderr,
        )
        failed = True
sys.exit(3 if failed else 0)
PY

echo "PASS: URL diff count matches expected; assertion #9 passed."
exit 0
