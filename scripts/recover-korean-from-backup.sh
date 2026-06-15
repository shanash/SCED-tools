#!/usr/bin/env bash
# recover-korean-from-backup.sh
#
# Force-push a backup tag or branch onto the korean branch in SCED-downloads,
# allowing recovery from a bad rebuild.
#
# Usage:
#   recover-korean-from-backup.sh [--tag NAME|--branch NAME] [--target NAME]
#                                 [--remote NAME] [--yes]
#
# Exit codes:
#   0  success
#   1  neither --tag nor --branch specified
#   2  selected ref doesn't exist
#   3  user rejected confirmation
#   4  push failed (including lease conflict)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCED_DOWNLOADS="${REPO_ROOT}/SCED-downloads"

TAG=""
BRANCH=""
TARGET="korean"
REMOTE="origin"
YES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      TAG="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --target)
      TARGET="$2"
      shift 2
      ;;
    --remote)
      REMOTE="$2"
      shift 2
      ;;
    --yes)
      YES=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# Require exactly one of --tag or --branch.
if [[ -z "${TAG}" && -z "${BRANCH}" ]]; then
  echo "ERROR: specify --tag NAME or --branch NAME" >&2
  exit 1
fi

if [[ -n "${TAG}" && -n "${BRANCH}" ]]; then
  echo "ERROR: --tag and --branch are mutually exclusive" >&2
  exit 1
fi

# Determine selected ref.
if [[ -n "${TAG}" ]]; then
  if [[ "${TAG}" == "latest" ]]; then
    SELECTED_TAG=$(
      git -C "${SCED_DOWNLOADS}" for-each-ref \
        --sort=-creatordate \
        --format='%(refname:short)' \
        'refs/tags/archive/korean-pre-rebuild-*' \
      | head -1
    )
    if [[ -z "${SELECTED_TAG}" ]]; then
      echo "ERROR: no 'archive/korean-pre-rebuild-*' tags found in ${SCED_DOWNLOADS}" >&2
      exit 2
    fi
    SELECTED_REF="${SELECTED_TAG}"
  else
    SELECTED_REF="${TAG}"
  fi
  REF_TYPE="tag"
else
  SELECTED_REF="${BRANCH}"
  REF_TYPE="branch"
fi

# Verify the ref exists.
if ! git -C "${SCED_DOWNLOADS}" rev-parse --verify "${SELECTED_REF}^{commit}" &>/dev/null; then
  echo "ERROR: ref '${SELECTED_REF}' does not exist in ${SCED_DOWNLOADS}" >&2
  exit 2
fi

REF_SHA=$(git -C "${SCED_DOWNLOADS}" rev-parse "${SELECTED_REF}^{commit}")

echo "Selected ${REF_TYPE}: ${SELECTED_REF}"
echo "SHA: ${REF_SHA}"
echo "Push target: ${REMOTE}/${TARGET}"

if [[ "${YES}" != "true" ]]; then
  read -r -p "Proceed with force-push? This will overwrite ${REMOTE}/${TARGET}. [y/N] " CONFIRM
  case "${CONFIRM}" in
    [yY]|[yY][eE][sS])
      ;;
    *)
      echo "Aborted by user." >&2
      exit 3
      ;;
  esac
fi

# Push with force-with-lease to avoid clobbering concurrent pushes.
if ! git -C "${SCED_DOWNLOADS}" push --force-with-lease \
    "${REMOTE}" "${REF_SHA}:refs/heads/${TARGET}"; then
  echo "ERROR: push failed (lease conflict or network error)" >&2
  exit 4
fi

echo "SUCCESS: ${REMOTE}/${TARGET} restored from ${SELECTED_REF} (${REF_SHA})"
