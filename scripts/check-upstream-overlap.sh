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
#                             [--paths-out FILE]          (the overlap set)
#                             [--fork-paths-out FILE]     (fork-changed set)
#                             [--upstream-paths-out FILE] (upstream-changed set)
#                             [--probe]             (repo-readability probe only)
#
# --paths-out writes the exact intersection (newline-delimited, LC_ALL=C byte
# order, no header) to FILE. It is written on BOTH exit 0 (empty file) and exit
# 10, so a caller can rely on its existence; nothing is written on exit 1 or 2.
# Human-readable stdout is unaffected.
#
# --fork-paths-out and --upstream-paths-out write the two INPUTS of that
# intersection, with the same idiom and the same guarantees. They exist so a
# caller can bound a change's blast radius -- a path that appears in neither set
# was touched by neither side and has no business differing after a rebase. They
# are inputs to the predicate, never the predicate: the `comm -12` below is the
# only thing that decides whether the gate trips.
#
# --fetch updates the 'origin' and 'upstream' remotes of the selected repo
# before computing. It is opt-in because the default must never touch the
# network.
#
# --probe stops right after the repository-readability guard and exits 0. It
# exists for the launchd wrapper's TCC canary, which needs to know whether git
# can read the volume at all — no network, no ref resolution, no diff. --fetch
# and all three --*-paths-out flags are ignored under --probe.
#
# Exit codes:
#   0  no overlap — an unattended rebase is safe  (or: --probe succeeded)
#   1  usage error (bad flag, unknown --repo, repo checkout unreadable)
#   2  a required ref is missing, has no common ancestor, or --fetch failed
#  10  overlap found — the gate skips and a human must adjudicate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

REPO=""
FORK_REF="origin/korean"
UPSTREAM_REF="upstream/main"
LIMIT=50
PATHS_OUT=""
# The two INPUTS of the intersection. --paths-out gives the predicate's result;
# these give the sets it was computed from, which a caller needs to bound a
# change's blast radius ("did anything outside what either side touched move?").
# They are not the predicate and nothing below reads them back.
FORK_PATHS_OUT=""
UPSTREAM_PATHS_OUT=""
FETCH=false
PROBE=false

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
    --paths-out)
      PATHS_OUT="$2"
      shift 2
      ;;
    --fork-paths-out)
      FORK_PATHS_OUT="$2"
      shift 2
      ;;
    --upstream-paths-out)
      UPSTREAM_PATHS_OUT="$2"
      shift 2
      ;;
    --fetch)
      FETCH=true
      shift
      ;;
    --probe)
      PROBE=true
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

# `&>/dev/null` used to destroy BOTH git's stderr and its exit code, so the
# macOS System Policy denial of 2026-08-01 (rc 128, "Operation not permitted")
# was reported as "not a git repository" and cost a multi-hour diagnosis.
# Distinct causes that share this guard: genuine non-repo (128), sandbox EPERM
# (128), dubious ownership (128), unreadable directory (128), malformed config
# (128), git missing from PATH (127), git not executable (126), signal (>128).
# `|| rc=$?` is required: under `set -e` the bare assignment would abort, and
# `if ! cmd` would leave $? holding the INVERTED status, not git's.
GIT_PROBE_RC=0
GIT_PROBE_ERR="$(git -C "${REPO_PATH}" rev-parse --git-dir 2>&1)" || GIT_PROBE_RC=$?
if [[ "${GIT_PROBE_RC}" -ne 0 ]]; then
  printf "ERROR: git could not read '%s' (rc=%d): %s\n" \
    "${REPO_PATH}" "${GIT_PROBE_RC}" "${GIT_PROBE_ERR}" >&2
  exit 1
fi

# --probe: stop right after the repository-readability guard. That guard is the
# only thing the launchd wrapper's TCC canary needs, and running the full
# predicate there would cost a 1377-path diff on SCED-downloads every night for
# nothing. No network, no ref resolution, no diff. --fetch and --paths-out are
# ignored under --probe.
if [[ "${PROBE}" == "true" ]]; then
  echo "PROBE OK: git can read ${REPO_PATH}"
  exit 0
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

# Machine-readable copy of the intersection, before the counts, so it lands on
# exit 0 (empty) and exit 10 alike. Written via a temp + rename so a reader can
# never observe a half-written file.
if [[ -n "${PATHS_OUT}" ]]; then
  cp "${TMP_DIR}/overlap.txt" "${PATHS_OUT}.tmp" \
    && mv "${PATHS_OUT}.tmp" "${PATHS_OUT}" \
    || { echo "ERROR: could not write --paths-out ${PATHS_OUT}" >&2; exit 2; }
fi

# Same idiom, same guarantees, for the two INPUTS of the comm above. Written here
# -- after the comm, before the counts -- so all three land on exit 0 and exit 10
# alike, and none of them on exit 1 or 2.
if [[ -n "${FORK_PATHS_OUT}" ]]; then
  cp "${TMP_DIR}/fork.txt" "${FORK_PATHS_OUT}.tmp" \
    && mv "${FORK_PATHS_OUT}.tmp" "${FORK_PATHS_OUT}" \
    || { echo "ERROR: could not write --fork-paths-out ${FORK_PATHS_OUT}" >&2; exit 2; }
fi

if [[ -n "${UPSTREAM_PATHS_OUT}" ]]; then
  cp "${TMP_DIR}/upstream.txt" "${UPSTREAM_PATHS_OUT}.tmp" \
    && mv "${UPSTREAM_PATHS_OUT}.tmp" "${UPSTREAM_PATHS_OUT}" \
    || { echo "ERROR: could not write --upstream-paths-out ${UPSTREAM_PATHS_OUT}" >&2; exit 2; }
fi

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
