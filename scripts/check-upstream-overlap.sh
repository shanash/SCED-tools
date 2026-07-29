#!/usr/bin/env bash
# check-upstream-overlap.sh
#
# Human-side mirror of the daily-upstream-sync overlap gate (design
# .am/daily-upstream-rebase-redeploy/design.md §5.4). Reports the files that
# BOTH the fork and upstream changed since their merge base — the condition
# that makes an unattended rebase unsafe — so a stalled sync can be triaged
# locally with the exact same predicate CI used.
#
# Strictly read-only: it never writes a ref, never pushes, never touches a
# working tree. Without --fetch it performs no network access at all, so it is
# safe to run offline.
#
# Usage:
#   check-upstream-overlap.sh --repo <SCED|SCED-downloads|SCED-tools>
#                             [--fork-ref REF]      (default: origin/korean)
#                             [--upstream-ref REF]  (default: upstream/main)
#                             [--limit N]           (default: 50)
#                             [--fetch]
#
# --fetch updates the 'origin' and 'upstream' remotes of the selected repo
# before computing. It is opt-in because the default must never touch the
# network.
#
# Exit codes:
#   0  no overlap — an unattended rebase is safe
#   1  usage error (bad flag, unknown --repo, missing repo checkout)
#   2  a required ref is missing, has no common ancestor, or --fetch failed
#  10  overlap found — the gate skips and a human must adjudicate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

REPO=""
FORK_REF="origin/korean"
UPSTREAM_REF="upstream/main"
LIMIT=50
FETCH=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="$2"
      shift 2
      ;;
    --fork-ref)
      FORK_REF="$2"
      shift 2
      ;;
    --upstream-ref)
      UPSTREAM_REF="$2"
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    --fetch)
      FETCH=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${REPO}" ]]; then
  echo "ERROR: specify --repo <SCED|SCED-downloads|SCED-tools>" >&2
  exit 1
fi

case "${REPO}" in
  SCED|SCED-downloads|SCED-tools) ;;
  *)
    echo "ERROR: unknown repo '${REPO}' (expected SCED, SCED-downloads or SCED-tools)" >&2
    exit 1
    ;;
esac

case "${LIMIT}" in
  ''|*[!0-9]*)
    echo "ERROR: --limit must be a non-negative integer (got '${LIMIT}')" >&2
    exit 1
    ;;
esac

REPO_PATH="${REPO_ROOT}/${REPO}"

if ! git -C "${REPO_PATH}" rev-parse --git-dir &>/dev/null; then
  echo "ERROR: '${REPO_PATH}' is not a git repository" >&2
  exit 1
fi

# Opt-in network refresh. A failure here is fatal rather than a warning: a
# triage run against silently stale refs reports a wrong overlap set.
if [[ "${FETCH}" == "true" ]]; then
  echo "Fetching origin and upstream in ${REPO_PATH} ..."
  if ! git -C "${REPO_PATH}" fetch --quiet --tags --force origin; then
    echo "ERROR: 'git fetch origin' failed in ${REPO_PATH}" >&2
    exit 2
  fi
  if ! git -C "${REPO_PATH}" fetch --quiet --tags upstream; then
    echo "ERROR: 'git fetch upstream' failed in ${REPO_PATH}" >&2
    exit 2
  fi
fi

if ! FORK_SHA="$(git -C "${REPO_PATH}" rev-parse --verify -q "${FORK_REF}^{commit}")"; then
  echo "ERROR: ref '${FORK_REF}' does not exist in ${REPO_PATH}" >&2
  exit 2
fi

if ! UPSTREAM_SHA="$(git -C "${REPO_PATH}" rev-parse --verify -q "${UPSTREAM_REF}^{commit}")"; then
  echo "ERROR: ref '${UPSTREAM_REF}' does not exist in ${REPO_PATH}" >&2
  exit 2
fi

# The gate is merge-base arithmetic, NOT a dry-run rebase (design R3a): on
# 2026-06-27 git's own conflict signal flagged only 18 of the 142 files that
# both sides had touched, so 124 would have been rewritten silently.
if ! MERGE_BASE="$(git -C "${REPO_PATH}" merge-base "${FORK_SHA}" "${UPSTREAM_SHA}")"; then
  echo "ERROR: '${FORK_REF}' and '${UPSTREAM_REF}' have no common ancestor" >&2
  exit 2
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/check-upstream-overlap.XXXXXX")"
trap 'rm -rf "${TMP_DIR}"' EXIT

# Verbatim mirror of the CI gate predicate. Each flag is load-bearing:
#
#   --no-renames         git's rename detection reports only the NEW path, so an
#                        upstream rename of a fork-modified file would land on
#                        one side only and slip through the gate (design F2:
#                        6 paths with detection vs 12 with --no-renames on
#                        commit 8e4ca17a, the InvestigatorTokens case rename).
#   core.quotePath=false 5 of the fork's changed paths are non-ASCII
#                        ("Déjà Vu5", "Coup de Grâce", ...); the default C-quotes
#                        them into unreadable escapes.
#   LC_ALL=C sort -u     comm(1) compares byte-wise, so a locale-collated input
#                        silently yields a wrong intersection. Both sides must
#                        use the same C collation, and so must comm itself.
names() { # $1 = ref
  git -C "${REPO_PATH}" -c core.quotePath=false \
    diff --no-renames --name-only "${MERGE_BASE}" "$1" \
    | LC_ALL=C sort -u
}

names "${FORK_SHA}" > "${TMP_DIR}/fork.txt"
names "${UPSTREAM_SHA}" > "${TMP_DIR}/upstream.txt"
LC_ALL=C comm -12 "${TMP_DIR}/fork.txt" "${TMP_DIR}/upstream.txt" > "${TMP_DIR}/overlap.txt"

N_FORK="$(wc -l < "${TMP_DIR}/fork.txt" | tr -d ' ')"
N_UPSTREAM="$(wc -l < "${TMP_DIR}/upstream.txt" | tr -d ' ')"
N_OVERLAP="$(wc -l < "${TMP_DIR}/overlap.txt" | tr -d ' ')"

echo "Repository:       ${REPO} (${REPO_PATH})"
echo "Fork ref:         ${FORK_REF} -> ${FORK_SHA}"
echo "Upstream ref:     ${UPSTREAM_REF} -> ${UPSTREAM_SHA}"
echo "Merge base:       ${MERGE_BASE}"
echo
echo "Fork-changed:     ${N_FORK}"
echo "Upstream-changed: ${N_UPSTREAM}"
echo "Overlap:          ${N_OVERLAP}"

if [[ "${N_OVERLAP}" -eq 0 ]]; then
  echo
  echo "OK: no file was changed by both sides since the merge base — rebase is safe."
  exit 0
fi

echo
echo "Overlapping paths (both sides changed them since ${MERGE_BASE}):"
echo

# Cap first, then group, so the printed set is exactly the first ${LIMIT}
# paths in byte order — the same slice the CI Issue body carries.
head -n "${LIMIT}" "${TMP_DIR}/overlap.txt" \
  | awk -F/ '{
      if (NF == 1) { d = "."; b = $0 }
      else { b = $NF; d = substr($0, 1, length($0) - length($NF) - 1) }
      print d "\t" b
    }' \
  | LC_ALL=C sort \
  | awk -F'\t' '
      $1 != prev { if (NR > 1) print ""; printf "  %s/\n", $1; prev = $1 }
      { printf "    %s\n", $2 }
    '

if [[ "${N_OVERLAP}" -gt "${LIMIT}" ]]; then
  echo
  echo "  ... and $((N_OVERLAP - LIMIT)) more (raise --limit to see them)"
fi

echo
echo "OVERLAP: ${N_OVERLAP} file(s) changed by both sides — the daily sync skips."
echo "Adjudicate manually, rebase locally, then let the next cron resume."
exit 10
