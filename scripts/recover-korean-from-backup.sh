#!/usr/bin/env bash
# recover-korean-from-backup.sh
#
# Force-push a backup tag or branch onto the korean branch of one of the
# workspace repos, allowing recovery from a bad rebuild or a bad automated
# rebase (daily-upstream-sync force-pushes korean on SCED and SCED-downloads).
#
# Usage:
#   recover-korean-from-backup.sh [--repo NAME] [--tag NAME|--branch NAME]
#                                 [--target NAME] [--remote NAME] [--yes]
#
#   --repo    SCED | SCED-downloads | SCED-tools  (default: SCED-downloads)
#   --tag     "latest" selects the newest 'archive/korean-pre-rebuild-*' tag
#   --branch  "latest" selects the newest 'auto/korean-backup-*' branch, local
#             or remote-tracking, by creatordate
#
# Exit codes:
#   0  success
#   1  usage error (unknown --repo; neither or both of --tag and --branch)
#   2  selected ref doesn't exist
#   3  user rejected confirmation
#   4  remote query or push failed (including lease conflict)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

REPO="SCED-downloads"
TAG=""
BRANCH=""
TARGET="korean"
REMOTE="origin"
YES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="$2"
      shift 2
      ;;
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

# Resolve the target repo.
case "${REPO}" in
  SCED|SCED-downloads|SCED-tools) ;;
  *)
    echo "ERROR: unknown repo '${REPO}' (expected SCED, SCED-downloads or SCED-tools)" >&2
    exit 1
    ;;
esac
REPO_PATH="${REPO_ROOT}/${REPO}"

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
      git -C "${REPO_PATH}" for-each-ref \
        --sort=-creatordate \
        --format='%(refname:short)' \
        'refs/tags/archive/korean-pre-rebuild-*' \
      | head -1
    )
    if [[ -z "${SELECTED_TAG}" ]]; then
      echo "ERROR: no 'archive/korean-pre-rebuild-*' tags found in ${REPO_PATH}" >&2
      exit 2
    fi
    SELECTED_REF="${SELECTED_TAG}"
  else
    SELECTED_REF="${TAG}"
  fi
  REF_TYPE="tag"
else
  if [[ "${BRANCH}" == "latest" ]]; then
    # Newest automated backup branch, considering local branches and the
    # remote-tracking refs of ${REMOTE} (the automation pushes the backup to
    # the remote, so a fresh clone only ever has the remote-tracking copy).
    # Scoped to the 'auto/' prefix on purpose: the hand-made
    # 'korean-rebase-backup-<YYYYMMDD>' branches are permanent forensic
    # artifacts and must never be selected — or pruned — by automation.
    SELECTED_BRANCH=$(
      git -C "${REPO_PATH}" for-each-ref \
        --sort=-creatordate \
        --format='%(refname:short)' \
        'refs/heads/auto/korean-backup-*' \
        "refs/remotes/${REMOTE}/auto/korean-backup-*" \
      | head -1
    )
    if [[ -z "${SELECTED_BRANCH}" ]]; then
      echo "ERROR: no 'auto/korean-backup-*' branches found in ${REPO_PATH}" >&2
      exit 2
    fi
    SELECTED_REF="${SELECTED_BRANCH}"
  else
    SELECTED_REF="${BRANCH}"
  fi
  REF_TYPE="branch"
fi

# Verify the ref exists.
if ! git -C "${REPO_PATH}" rev-parse --verify "${SELECTED_REF}^{commit}" &>/dev/null; then
  echo "ERROR: ref '${SELECTED_REF}' does not exist in ${REPO_PATH}" >&2
  exit 2
fi

REF_SHA=$(git -C "${REPO_PATH}" rev-parse "${SELECTED_REF}^{commit}")

echo "Repo: ${REPO} (${REPO_PATH})"
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

# Capture the actual remote tip of TARGET so the lease is based on the real
# remote state, not a possibly-stale local remote-tracking ref (design §8c,
# Issue #10 / N4). Empty result => TARGET does not exist on the remote yet.
if ! REMOTE_LS="$(git -C "${REPO_PATH}" ls-remote "${REMOTE}" "refs/heads/${TARGET}")"; then
  echo "ERROR: failed to query ${REMOTE} for refs/heads/${TARGET} (network error)" >&2
  exit 4
fi
EXPECTED_REMOTE_SHA="$(printf '%s\n' "${REMOTE_LS}" | awk 'NR==1 {print $1}')"

echo "Remote tip of ${REMOTE}/${TARGET}: ${EXPECTED_REMOTE_SHA:-<absent>}"

# Explicit lease: push only if the remote tip is still what we just observed.
# An empty expected value asserts the ref must not yet exist (safe create).
if ! git -C "${REPO_PATH}" push \
    "--force-with-lease=refs/heads/${TARGET}:${EXPECTED_REMOTE_SHA}" \
    "${REMOTE}" "${REF_SHA}:refs/heads/${TARGET}"; then
  echo "ERROR: push failed (lease conflict or network error)" >&2
  exit 4
fi

echo "SUCCESS: ${REMOTE}/${TARGET} restored from ${SELECTED_REF} (${REF_SHA})"
