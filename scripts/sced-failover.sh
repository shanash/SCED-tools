#!/usr/bin/env bash
# sced-failover.sh
#
# Force-dispatch the GitHub Actions fallback (`daily-upstream-sync.yml`) when the
# local nightly tier could not finish the night itself. Design:
# .am/local-failure-gha-failover/design.md.
#
# WHY THIS EXISTS
#   CLAUDE.md long claimed the GHA tier picks the night up "+60 minutes" after the
#   local one. Measured over the last 20 scheduled runs of both forks, that has
#   never once been true: delivery ran 71.6-359.9 minutes late, so the MINIMUM
#   observed lag already exceeded the contract. GitHub documents scheduled events
#   as best-effort and droppable under load, so the latency cannot be fixed -- only
#   routed around. `workflow_dispatch` is the only trigger with a bounded latency
#   (11 s, measured).
#
# THE ONE PROPERTY THAT MAKES THIS SAFE
#   This script changes WHEN CI runs. It never changes WHAT CI decides. The
#   dispatch carries force=false, so CI's own no-op guard and its unbypassable
#   overlap gate remain the sole authorities on whether any work happens. On a
#   night that needed nothing, the dispatched run reaches its no-op guard and
#   finishes green in seconds.
#
# WHAT IT WILL NEVER DO
#   Touch a working tree, a ref, or a release. It makes read-only `gh api` calls
#   plus exactly one `gh workflow run`, and (in --watchdog mode) one read-modify-
#   write of .local-sync/state/<repo>.last-run.json. There is no `git` anywhere in
#   this file.
#
# TWO TIERS, ONE IMPLEMENTATION
#   --dispatch   called by daily-sync-local.sh's finish() for any failure that
#                reaches it (analysis classes (b) and (c)). Latency: seconds.
#   --watchdog   called by com.shanash.sced-failover-watchdog at 03:05/03:35 KST
#                for the failures that never reach finish() at all -- the ten bare
#                `exit` sites, the six wrapper `die()` sites, an unmounted volume,
#                or a machine that was simply off. Latency: 8-38 minutes.
#
# COST, AND WHO PAYS IT
#   In --dispatch mode this script runs inside the driver's finish(), which holds
#   the workspace-wide lock until it returns. Every network call is bounded -- but
#   by its OWN number, never by --timeout:
#       gh auth status     with_timeout 20    gh_usable()
#       draft query        with_timeout 30    draft_holds_tag(), TWICE on the
#                                             delete path
#       draft DELETE       with_timeout 30    draft_precondition_ok()
#       gh workflow run    with_timeout N     do_dispatch(), N = --timeout = 60
#   Serial worst case 170 s, or 195 s if each one has to be SIGKILLed after
#   with_timeout's 5 s grace. The no-tag path is 20 + 60 = 80 s. The 4-6 local
#   `gh --version` spawns carry no timeout at all; GH_NO_UPDATE_NOTIFIER=1 keeps
#   them off the network, so they are ~0.05 s each.
#   Adding or retiming a network call here changes the driver's lock-hold worst
#   case, so three places move together: this block, the COST comment in
#   daily-sync-local.sh's finish(), and design.feature-v1.md 5.4.
#
#   The 170/195 s figures are the --dispatch tier and are UNCHANGED by the R3
#   retry, which is why the retry is tier-split rather than global: in --watchdog
#   mode the auth probe may run twice, and nothing there holds the lock. Costed in
#   the SAME convention as the 195 s above -- i.e. each with_timeout 20 bounded at
#   its SIGKILL worst case of 25 s, not at its nominal 20 -- that is
#   25 + 5 pause + 25 = 55 s, so +30 s over the single-shot worst case of 25 s.
#   (Nominally 45 s / +25 s; the two conventions must not be mixed inside one
#   block, which an earlier draft of this paragraph did.) The draft probes are not
#   retried in either tier -- draft_precondition_ok() is reachable only from
#   run_dispatch().
#
# Usage:
#   sced-failover.sh --dispatch --repo <SCED|SCED-downloads> [options]
#   sced-failover.sh --watchdog [--repo NAME] [--no-dispatch] [--grace-min N]
#   sced-failover.sh --status
#
#     --dispatch        dispatch for one repo. The caller has already decided this
#                       run failed; this script decides only whether dispatching is
#                       SAFE, never whether it is warranted.
#     --watchdog        evaluate every enabled repo and dispatch for those whose
#                       local tier did not report tonight.
#     --status          report what each repo's state looks like and what a
#                       watchdog pass would do. Never dispatches.
#     --repo NAME       required for --dispatch; optional filter for --watchdog
#     --tag TAG         the release tag this run was minting, if it got that far.
#                       When set, the orphan-draft precondition is enforced.
#     --release-id ID   the draft id the caller failed to clean up, if known
#     --reason TEXT     free text recorded in the log and the notification
#     --timeout N       seconds for the `gh workflow run` call ONLY (default 60).
#                       It does not bound this script -- the auth and draft probes
#                       carry their own fixed 20/30/30 s bounds. See COST above.
#     --grace-min N     minutes after a repo's latest legal start before the
#                       watchdog considers it (default 5)
#     --no-dispatch     --watchdog rehearsal: evaluate and report, dispatch nothing
#     --no-op           --dispatch rehearsal: run every probe and precondition,
#                       report the outcome that WOULD result, dispatch nothing
#     --no-notify       do not POST to Discord (the payload is still logged)
#     --help
#
# Environment (all optional). NOTE the ordering hazard: the mode-600 env file is
# sourced AFTER the flags are parsed, with `set -a`, so a value set there OVERRIDES
# an inline export on the command line. `SCED_SYNC_FAILOVER=SCED sced-failover.sh
# --watchdog` therefore does NOT arm anything while the kill switch is blank -- point
# SCED_SYNC_ENV_FILE at a scratch mode-600 file to rehearse.
#     SCED_SYNC_FAILOVER            comma-separated repo allowlist, and the kill switch
#     SCED_SYNC_ENV_FILE            overrides ~/.config/sced-sync/env
#     SCED_SYNC_STATE_ROOT          overrides <workspace>/.local-sync
#     SCED_SYNC_FAILOVER_STATE_DIR  where the per-night dispatch stamp lives
#                                   (default ~/.local/state/sced-failover)
#     SCED_SYNC_FAILOVER_TIMEOUT    default for --timeout
#     SCED_SYNC_FAILOVER_GRACE_MIN  default for --grace-min
#
# Exit codes:
#   0  dispatched (a run URL is on stdout when gh >= 2.87.0), or a clean --status
#   1  usage error
#   2  gh missing, unauthenticated, or the workflow is not reachable on the ref
#   3  refused: an orphan DRAFT holds --tag, or the draft state could not be verified
#   4  dispatch call failed (non-zero gh rc, or timeout)
#   5  refused: a failover was already dispatched for this repo tonight

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --------------------------------------------------------------------- options

MODE=""
REPO=""
TAG=""
RELEASE_ID=""
REASON=""
TIMEOUT=""
GRACE_MIN=""
NO_DISPATCH=false
NO_OP=false
NO_NOTIFY=false

usage() { sed -n '/^# Usage:/,/^#   5/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dispatch)    MODE="dispatch"; shift ;;
    --watchdog)    MODE="watchdog"; shift ;;
    --status)      MODE="status"; shift ;;
    --repo)        REPO="${2:-}"; shift 2 ;;
    --tag)         TAG="${2:-}"; shift 2 ;;
    --release-id)  RELEASE_ID="${2:-}"; shift 2 ;;
    --reason)      REASON="${2:-}"; shift 2 ;;
    --timeout)     TIMEOUT="${2:-}"; shift 2 ;;
    --grace-min)   GRACE_MIN="${2:-}"; shift 2 ;;
    --no-dispatch) NO_DISPATCH=true; shift ;;
    --no-op)       NO_OP=true; shift ;;
    --no-notify)   NO_NOTIFY=true; shift ;;
    --help|-h)     usage; exit 0 ;;
    *)             echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${MODE}" ]]; then
  echo "ERROR: specify --dispatch, --watchdog or --status" >&2
  usage >&2
  exit 1
fi
if [[ "${MODE}" == "dispatch" && -z "${REPO}" ]]; then
  echo "ERROR: --dispatch requires --repo <SCED|SCED-downloads>" >&2
  exit 1
fi
if [[ -n "${REPO}" && "${REPO}" != "SCED" && "${REPO}" != "SCED-downloads" ]]; then
  echo "ERROR: unknown --repo '${REPO}'" >&2
  exit 1
fi
# ------------------------------------------------------------------- env file
#
# Same contract as daily-sync-local.sh: the mode check is first and fatal. A
# secrets file the rest of the machine can read is not one to source. Unlike the
# driver, a missing env file is NOT fatal here -- the watchdog's whole job is to
# run when the normal path could not, and it can still dispatch without a webhook.
ENV_FILE="${SCED_SYNC_ENV_FILE:-${HOME}/.config/sced-sync/env}"
if [[ -r "${ENV_FILE}" ]]; then
  env_perm="$(stat -f '%Lp' "${ENV_FILE}" 2>/dev/null || echo '???')"
  if [[ "${env_perm}" != "600" ]]; then
    echo "ERROR: ${ENV_FILE} must be mode 600, found ${env_perm}" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

# Same pin, same reason, as daily-sync-local.sh: this script runs unattended under
# launchd (the watchdog at 03:05/03:35, and the driver's own dispatch on a failed
# run) and reads .local-sync/state/<repo>.last-run.json on /Volumes/PRO-G40.
# Homebrew's python3 is an app bundle, so TCC makes it its own responsible_path
# instead of inheriting /bin/bash's grant, and its adhoc-signed grant dies at the
# next `brew upgrade` -- 2026-08-18, where one such prompt cost the whole night.
# Read-only assertion here rather than the driver's two: this script must degrade
# to "the failover did not fire", never abort a night on its own.
PYBIN="${SCED_SYNC_PYTHON:-/usr/bin/python3}"
case "${PYBIN}" in
  /bin/*|/sbin/*|/usr/bin/*|/usr/sbin/*|/usr/libexec/*) ;;
  *) echo "WARNING: SCED_SYNC_PYTHON='${PYBIN}' is not a platform path; falling back to /usr/bin/python3" >&2
     PYBIN=/usr/bin/python3 ;;
esac

# PROVED NUMERIC AT THE BOUNDARY (verify R2). RELEASE_ID is interpolated into the
# PATH of a `gh api -X DELETE` (draft_precondition_ok) -- the only destructive call
# in either script -- so a value that was never checked must not be able to reach
# it.
#
# BELOW THE ENV FILE, NOT ABOVE IT (verify round 7, N20). RELEASE_ID has THREE
# writers, not one: the `RELEASE_ID=""` init neutralises an INHERITED value, the
# --release-id arm of the parse loop sets it, and `. "${ENV_FILE}"` under `set -a`
# can assign it like any other variable. This guard used to sit between the second
# and the third while claiming to cover every path. An env-file line would have
# reached the DELETE unchecked -- and, landing after the parse loop, would have
# silently overridden an explicit --release-id as well. Here it runs after the LAST
# writer, which is what makes that claim true rather than nearly true.
#
# `grep -n '^[^#]*RELEASE_ID=' sced-failover.sh` must show 3: the init, the parse
# arm, and this guard's own clear. The `^[^#]*` is what keeps this comment's own
# mentions out of its own count -- a first draft said "must show 3" against a plain
# grep that returned 5, which is the defect this rule exists to catch, committed
# inside the rule. A fourth hit means a writer was added and this block has to move
# again, or stop being the last word.
#
# CLEARING, not aborting, and that is deliberate twice over: an empty RELEASE_ID is
# an ALREADY-DESIGNED state (the orphan case has no id by construction, see the
# comment above draft_holds_tag), and this file deliberately invents no exit codes.
# `echo >&2` rather than log(), which is not defined until further down. The message
# does not name --release-id, because the env file can now be the source too.
case "${RELEASE_ID}" in
  '') ;;
  *[!0-9]*)
    echo "WARNING: ignoring non-numeric release id '${RELEASE_ID}'; no draft will be deleted by id" >&2
    RELEASE_ID="" ;;
esac

# ------------------------------------------------------------------ parameters

OWNER="shanash"
WORKFLOW="daily-upstream-sync.yml"
REF="korean"
GH_MIN_VERSION="2.87.0"

# The repo list, in ONE place (verify R9). Order is load-bearing in exactly one
# way -- SCED-downloads before SCED, matching the nightly stagger -- so the two
# consumers (enabled_repos and run_status) read it rather than each carrying a
# literal. `grep -n FAILOVER_REPOS` must show 4: this line, the assignment, and
# those two. fallback_hhmm()'s case labels are a keyed lookup, not a third copy.
FAILOVER_REPOS="SCED-downloads SCED"

# WORKSPACE is derived, not assumed: this script has an install copy on the boot
# volume (see design section 2 row 6) whose SCRIPT_DIR is NOT inside the workspace.
if [[ -d "${SCRIPT_DIR}/../.." && -f "${SCRIPT_DIR}/../config/sync-schedule.json" ]]; then
  WORKSPACE="${SCED_SYNC_WORKSPACE:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
else
  WORKSPACE="${SCED_SYNC_WORKSPACE:-/Volumes/PRO-G40/Projects/SCED}"
fi
STATE_ROOT="${SCED_SYNC_STATE_ROOT:-${WORKSPACE}/.local-sync}"
SCHEDULE_JSON="${WORKSPACE}/SCED-tools/config/sync-schedule.json"
LOCK="${STATE_ROOT}/run/daily-sync.lock"

TIMEOUT="${TIMEOUT:-${SCED_SYNC_FAILOVER_TIMEOUT:-60}}"
GRACE_MIN="${GRACE_MIN:-${SCED_SYNC_FAILOVER_GRACE_MIN:-5}}"

# Boot-volume state. It lives here, and not under STATE_ROOT, because the headline
# case this watchdog exists for is an unmounted /Volumes/PRO-G40 -- a record on that
# volume can be neither written nor read then. Today it holds exactly one file per
# repo, <repo>.dispatch, the per-night dispatch bound (design 3.4, verify F1).
#
# The five-line notification throttle design 3.4 also specifies is DEFERRED and
# deliberately NOT built here: it is a Discord-noise control rather than a
# correctness bound, and an unread file sitting next to the one that actually gates
# the dispatch is how "two throttle implementations" stops being a risk and becomes
# a fact. notify() below is unconditional.
WD_STATE_DIR="${SCED_SYNC_FAILOVER_STATE_DIR:-${HOME}/.local/state/sced-failover}"

# MANUAL-RUN FALLBACKS ONLY; the source of truth is
# SCED-tools/config/sync-schedule.json. Identical idiom and identical caveat to
# daily-sync-local.sh's own fallbacks, and for the same reason -- except that here
# the fallback is load-bearing rather than a convenience: when the volume is not
# mounted the config is unreadable and these are all the watchdog has.
#
# TWO BOUNDS, AND THEY ARE NOT INTERCHANGEABLE (verify F4):
#   local_hhmm   the SCHEDULED start. Gate 4 asks "did the driver report tonight",
#                and a report is a finished_at AT OR AFTER this.
#   latest_hhmm  the LAST LEGAL start. Gate 2 asks "could a local run still legally
#                begin", and waits until this plus the grace.
# Using latest_hhmm for gate 4 classifies every ordinary night as unreported --
# SCED-downloads reports at ~02:18, 39 minutes before its 02:57 latest -- so the
# watchdog would dispatch on a GREEN night, and on every exit 10 and exit 11 that
# gate 4 exists to protect.
fallback_hhmm() {
  case "$1/$2" in
    SCED/local_hhmm)            echo "0247" ;;
    SCED/latest_hhmm)           echo "0327" ;;
    SCED-downloads/local_hhmm)  echo "0217" ;;
    SCED-downloads/latest_hhmm) echo "0257" ;;
    *)                          echo "" ;;
  esac
}

export GH_PROMPT_DISABLED=1
export GH_NO_UPDATE_NOTIFIER=1

TODAY_UTC="$(date -u +%Y%m%d)"

# --------------------------------------------------------------------- logging

log() { printf '%s [failover] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >&2; }

# timeout(1) is not installed (no coreutils). Copied verbatim from
# daily-sync-local.sh, INCLUDING the watchdog subshell's stdout redirect: without
# it a background process inherits an enclosing command substitution's pipe and
# keeps it open, so the substitution blocks for the whole timeout even when the
# real command finished in a second.
with_timeout() {
  local secs="$1"; shift
  local rc=0 pid wd
  "$@" & pid=$!
  { sleep "${secs}"; kill -TERM "${pid}" 2>/dev/null; sleep 5; kill -KILL "${pid}" 2>/dev/null; } >/dev/null 2>&1 &
  wd=$!
  wait "${pid}" || rc=$?
  kill "${wd}" 2>/dev/null || true
  wait "${wd}" 2>/dev/null || true
  return "${rc}"
}

# Best-effort Discord notification, watchdog tier only. Never fatal: when this
# fails there is nothing further to escalate to. Same curl --config - idiom as the
# driver, so the webhook never appears in `ps`.
notify() {
  local title="$1" detail="$2" color="$3"
  [[ "${NO_NOTIFY}" == true ]] && { log "notify (suppressed): ${title}"; return 0; }
  [[ -n "${SCED_SYNC_DISCORD_WEBHOOK:-}" ]] || { log "no webhook configured, cannot notify"; return 0; }
  local payload
  payload="$(TITLE="${title}" DETAIL="${detail}" COLOR="${color}" \
             MENTION="${SCED_SYNC_MENTION:-}" "${PYBIN}" -c '
import json, os
title = os.environ["TITLE"]
detail = os.environ.get("DETAIL", "")
mention = os.environ.get("MENTION", "")
content = (mention + " " if mention else "") + "[sced-failover-watchdog]"
fields = []
if detail:
    fields.append({"name": "detail", "value": detail[:1000], "inline": False})
print(json.dumps({
    "username": "SCED failover watchdog",
    "content": content,
    "embeds": [{
        "title": title[:250],
        "color": int(os.environ.get("COLOR", "15158332")),
        "fields": fields,
    }],
}))
' 2>/dev/null)" || { log "payload build failed"; return 0; }

  printf 'url = "%s"\n' "${SCED_SYNC_DISCORD_WEBHOOK}" \
    | curl --config - -sS -X POST -H 'Content-Type: application/json' \
           --data-binary "${payload}" --max-time 20 --retry 2 --retry-delay 5 \
           >/dev/null 2>&1 || log "webhook POST failed"
}

# ------------------------------------------------------------------ gh probes

# R11. The long-standing "workflow_dispatch returns 204 No Content" constraint was
# lifted by the return_run_details parameter (GitHub Changelog 2026-02-19),
# supported in gh from v2.87.0 and defaulted on. Below that floor the dispatch
# still WORKS, it simply returns nothing -- so this probe is ADVISORY and must
# never block. `sort -V` is the established idiom here (daily-sync-local.sh :381,
# :1534); at exactly 2.87.0 the head is 2.87.0, so the comparison is inclusive.
GH_VERSION=""
gh_version() {
  [[ -n "${GH_VERSION}" ]] && { printf '%s' "${GH_VERSION}"; return 0; }
  GH_VERSION="$(gh --version 2>/dev/null | sed -n '1s/^gh version \([0-9][0-9.]*\).*/\1/p')"
  printf '%s' "${GH_VERSION}"
}

gh_run_url_supported() {
  local v; v="$(gh_version)"
  [[ -n "${v}" ]] || return 1
  [[ "$(printf '%s\n%s\n' "${GH_MIN_VERSION}" "${v}" | LC_ALL=C sort -V | head -1)" == "${GH_MIN_VERSION}" ]]
}

# Exit 2 conditions, checked once per invocation.
#
# ONE RETRY, WATCHDOG TIER ONLY (verify R3). A transient auth blip forfeiting the
# night is the exact failure this whole feature exists to prevent, so a second look
# is worth buying -- but only where it is free.
#
#   --watchdog   nothing holds the workspace lock (gate 3 refuses outright when a
#                run does), and the next firing is 30 minutes out. +30 s worst case
#                (45 s / +25 s nominal) -- the COST block above owns both figures
#                and says which convention is which. Stating the nominal number
#                under the word "worst case" was the D2 defect (verify round 5, N16).
#   --dispatch   this runs inside the driver's finish() while it holds the
#                workspace-wide LOCK, and the COST block above pins that path at
#                170/195 s -- a number three documents quote. Not retried there.
#
# The draft probe is NOT retried at all, in either tier, and that is not an
# oversight: draft_precondition_ok() is called only from run_dispatch(), i.e. only
# on the lock-holding tier. Adding a retry there would be dead code in the tier that
# could afford it and a lock-hold increase in the tier that cannot.
PROBE_REASON=""
# `if`, not `[[ ]] && PROBE_RETRY=1`. NOT because the && form would abort: it would
# not, and an earlier draft of this comment claimed it would. `set -e` exempts a
# command that is not the last in an && list, which is exactly the failing `[[ ]]`
# here -- measured under `set -euo pipefail` on bash 3.2.57, at file scope and
# inside a function, for MODE in dispatch/watchdog/status/empty: the list returns 1,
# the script continues, rc 0. The `if` is kept because it reads as what it is, a
# conditional assignment, and because its correctness does not depend on knowing
# that exemption rule at all.
PROBE_RETRY=0
if [[ "${MODE}" == "watchdog" ]]; then PROBE_RETRY=1; fi
gh_usable() {
  local attempt=0
  if ! command -v gh >/dev/null 2>&1; then
    PROBE_REASON="gh-missing"; return 1
  fi
  while :; do
    with_timeout 20 gh auth status >/dev/null 2>&1 && return 0
    [[ "${attempt}" -lt "${PROBE_RETRY}" ]] || break
    attempt=$(( attempt + 1 ))
    log "gh auth probe failed; retrying once in 5s (watchdog tier)"
    sleep 5
  done
  PROBE_REASON="gh-unauthenticated"
  return 1
}

# ------------------------------------------------------- orphan-draft guard (R5)
#
# R5's real shape is sharper than "call delete_draft() first". At the driver's two
# no-id sites -- daily-sync-local.sh's "could not create the draft release" (:1743)
# and "draft release returned no id" (:1746) -- RELEASE_ID is empty: the orphan case
# IS a POST that succeeded server-side while the client timed out or the --jq .id
# pipeline failed. Deletion by id is therefore unavailable, and delete_draft() is
# best-effort besides -- it clears RELEASE_ID unconditionally, so any post-check on
# that variable is vacuous.
#
# The precondition is therefore stated on the TAG and PROVED, never assumed. Only
# DRAFTS are counted: a published release holding the tag creates the git tag, so
# CI's own tag guard stops at its exit 50. It is precisely the draft -- invisible to
# that guard -- that lets softprops/action-gh-release UPDATE it and go-live flip it
# with a mixed asset set and the local driver's marker still in the body.
# per_page goes in the QUERY STRING, never through `-f`. `gh api -f k=v` switches
# the method to POST: with this URL that is the CREATE-A-RELEASE endpoint, and the
# only thing that stopped it from being a write was the missing tag_name (measured
# 2026-08-07: HTTP 422 "tag_name wasn't supplied"). An earlier draft of the design
# specified `-f per_page=100` for exactly this call.
#
# --method GET is belt-and-braces: it pins the verb even if this call ever grows a
# parameter flag.
#
# The tag reaches jq through the ENVIRONMENT, never the program text (verify R1).
# This call site was the file's one exception to the discipline that notify(),
# state_get() and sched_hhmm_for() already follow -- named rather than cited by
# line, because the two line numbers that stood here pointed at neither of them and
# one of them pointed four lines into THIS paragraph (verify round 4, N10). A tag
# carrying a `"` terminated the jq string literal. `env.FO_TAG` is
# supported by gh's embedded jq engine (verified against gh 2.87.3: a literal
# comparison and an env comparison return the same count).
#
# The assignment lives INSIDE the command substitution -- a subshell -- so it cannot
# leak past the call under any shell option.
#
# The plain `FO_TAG=x with_timeout ...` prefix form would ALSO be clean here today,
# and the first draft of this comment claimed otherwise; measured on bash 3.2.57 --
# the only bash on this machine, and what this script's shebang resolves to, so the
# "and 5.x" this sentence used to claim was never run and has been struck (D6) -- an
# assignment prefixing a shell-function call persists only under `set -o posix`,
# which this script does not set. The subshell form is kept because it does not
# depend on that: `set -o posix` appearing anywhere above would silently turn the
# prefix form into a leak, and this shape has no such precondition.
draft_holds_tag() {
  local repo="$1" tag="$2" n=""
  n="$(export FO_TAG="${tag}"
       with_timeout 30 gh api --method GET "repos/${OWNER}/${repo}/releases?per_page=100" \
         --jq '[.[] | select(.draft) | select(.tag_name==env.FO_TAG)] | length' 2>/dev/null)" || return 2
  [[ -n "${n}" ]] || return 2
  [[ "${n}" -gt 0 ]]
}

# Returns 0 when it is safe to dispatch, 3 when it is not. FAILS CLOSED: an
# unreachable API is never read as "the draft is gone".
draft_precondition_ok() {
  local repo="$1" tag="$2" rc=0
  [[ -n "${tag}" ]] || return 0                       # the run never got as far as a tag
  set +e; draft_holds_tag "${repo}" "${tag}"; rc=$?; set -e
  case "${rc}" in
    1) return 0 ;;                                    # no draft holds the tag
    2) PROBE_REASON="draft-unverifiable"; return 1 ;;  # fail closed
  esac
  if [[ -n "${RELEASE_ID}" ]]; then
    log "orphan draft holds ${tag}; deleting release id ${RELEASE_ID}"
    with_timeout 30 gh api -X DELETE "repos/${OWNER}/${repo}/releases/${RELEASE_ID}" >/dev/null 2>&1 \
      || log "could not delete draft ${RELEASE_ID} (best effort)"
    set +e; draft_holds_tag "${repo}" "${tag}"; rc=$?; set -e
    [[ "${rc}" -eq 1 ]] && return 0
  fi
  PROBE_REASON="orphan-draft"
  return 1
}

# ------------------------------------------------------------------- state I/O

# Reads one dotted field out of <repo>.last-run.json. Never sources the file.
state_get() {
  local repo="$1" path="$2"
  local f="${STATE_ROOT}/state/${repo}.last-run.json"
  [[ -r "${f}" ]] || return 1
  FO_PATH="${path}" "${PYBIN}" -c '
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
for k in os.environ["FO_PATH"].split("."):
    if not isinstance(d, dict) or k not in d:
        sys.exit(1)
    d = d[k]
print("" if d is None else d)
' "${f}" 2>/dev/null
}

# --------------------------------------------------------- boot-volume stamp
#
# The per-night dispatch bound, design 3.4 (verify F1). last-run.json is the
# authoritative record and stays so -- but it lives on /Volumes/PRO-G40, and the
# headline case this watchdog exists for is that volume not being mounted. On that
# night state_get() cannot read anything, gate 5 fails open, and 03:35 repeats
# 03:05. This file is the only record that survives that night.
#
# It is NOT a second source of truth: the read is an OR, so it can only ever make
# the watchdog refuse where it would otherwise fire. It carries the SAME outcome
# vocabulary as last-run.json, so the two can never disagree -- `error` is not
# fired in either store, because design 3.1 makes an errored dispatch a legitimate
# retry.
STAMP_DATE=""
STAMP_OUTCOME=""

stamp_read() {                       # 1 when there is no readable stamp
  local f="${WD_STATE_DIR}/$1.dispatch"
  STAMP_DATE=""; STAMP_OUTCOME=""
  [[ -r "${f}" ]] || return 1
  # Two FIXED sed programs. Nothing is interpolated into either: the key names are
  # literals in this file, never arguments (cf. verify F6, one function over).
  STAMP_DATE="$(sed -n 's/^date: *//p' "${f}" 2>/dev/null | head -1)"
  STAMP_OUTCOME="$(sed -n 's/^outcome: *//p' "${f}" 2>/dev/null | head -1)"
  return 0
}

stamp_says_fired() {
  local repo="$1"
  stamp_read "${repo}" || return 1
  if [[ -z "${STAMP_DATE}" ]]; then
    # FAIL OPEN, on purpose. A stamp read as "fired" is only ever rewritten by a
    # dispatch, so failing closed on an unparseable one would suppress this agent
    # permanently with no self-healing path. The cost of failing open is one
    # duplicate dispatch, which the korean-release-assets concurrency group
    # absorbs; the cost of failing closed is every future night.
    log "${repo}: dispatch stamp ${WD_STATE_DIR}/${repo}.dispatch carries no date line; ignoring it"
    return 1
  fi
  [[ "${STAMP_DATE}" == "${TODAY_UTC}" ]] || return 1
  case "${STAMP_OUTCOME}" in
    dispatched|attempting) return 0 ;;
    *)                     return 1 ;;
  esac
}

# NEVER fatal and NEVER blocks a dispatch: a local bookkeeping failure must not
# cost the night this whole feature exists to save. Same atomic idiom as
# should_notify() in daily-sync-local.sh -- write .tmp, then rename.
#
# The `$$` in the temp name separates the two writers that can race for one repo:
# a driver-tier `--dispatch`, exec'd as a child process by the driver's finish(),
# and a watchdog-tier firing, which launchd starts as its own process. They differ
# because they are separate INVOCATIONS, not because `$$` is per-write -- bash
# subshells inherit `$$`, so calling write_stamp twice inside one invocation would
# reuse the same temp name. Nothing does that today; a future retry loop must not.
# A hard kill between the write and the rename leaves a `.tmp` nobody reaps, but it
# is inert: every reader opens `<repo>.dispatch` by exact name, never a glob.
write_stamp() {
  local repo="$1" outcome="$2" run_id="$3" run_url="$4" tier="driver"
  local f="${WD_STATE_DIR}/${repo}.dispatch" tmp="${WD_STATE_DIR}/${repo}.dispatch.$$.tmp"
  [[ "${MODE}" == "watchdog" ]] && tier="watchdog"
  if ! mkdir -p "${WD_STATE_DIR}" 2>/dev/null; then
    log "${repo}: cannot create ${WD_STATE_DIR} -- the per-night dispatch bound is UNENFORCED"
    return 0
  fi
  if { printf 'date: %s\n'    "${TODAY_UTC}"
       printf 'outcome: %s\n' "${outcome}"
       printf 'tier: %s\n'    "${tier}"
       printf 'run_id: %s\n'  "${run_id}"
       printf 'run_url: %s\n' "${run_url}"
       printf 'at: %s\n'      "$(date +%Y-%m-%dT%H:%M:%S%z)"; } > "${tmp}" 2>/dev/null \
     && mv -f "${tmp}" "${f}" 2>/dev/null; then
    return 0
  fi
  rm -f "${tmp}" 2>/dev/null || true
  log "${repo}: cannot write ${f} -- the per-night dispatch bound is UNENFORCED"
  return 0
}

# R12 / Decision 3. The "already fired tonight" record. It lives in last-run.json,
# is keyed by the UTC date, and therefore SELF-EXPIRES with no cleanup step at all
# -- which is the exact property Alt-7 lacked when it was rejected for state
# leakage. It never touches a remote, a ref or a lock.
#
# outcome == "error" deliberately does NOT count as fired, so the watchdog remains
# a legitimate retry for a driver dispatch that hit a transient network failure.
# The boot-volume stamp is consulted as an OR, never as a condition: it can only
# make this predicate refuse where it would otherwise fire, and it uses the same
# `error`-is-not-fired rule, so the two stores cannot contradict each other.
already_fired_tonight() {
  local repo="$1" d="" o=""
  # `|| echo ''` and NOT `|| return 1`: state_get() returns 1 for "file
  # unreadable", "JSON corrupt" AND "key absent", and only the third of those
  # means no dispatch happened. Returning early on the first two is verify F1.
  d="$(state_get "${repo}" failover.date 2>/dev/null || echo '')"
  o="$(state_get "${repo}" failover.outcome 2>/dev/null || echo '')"
  [[ "${d}" == "${TODAY_UTC}" && "${o}" == "dispatched" ]] && return 0
  if stamp_says_fired "${repo}"; then
    log "${repo}: the boot-volume dispatch stamp records ${STAMP_OUTCOME} for ${TODAY_UTC}"
    return 0
  fi
  return 1
}

# Records the outcome under a NEW top-level key, preserving every other key
# verbatim -- the additive-schema contract write_state() states at its head. Used
# by the watchdog tier only; the driver writes its own record inside write_state().
#
# READ-MODIFY-WRITE, NEVER CREATE (verify F2). The earlier form did
# `except Exception: doc = {}` and then wrote, so any unreadable, truncated or
# unparseable <repo>.last-run.json was REPLACED by a one-key stub. That destroyed
# repo, finished_at, exit_code and asset_count -- and asset_count is the driver's
# offline asset baseline (daily-sync-local.sh, the N_PREV read), where a MISSING
# file fails closed at exit 63 and a stub with asset_count 0 silently skips the 95%
# floor check instead. Creating a stub is strictly worse than writing nothing, so
# this function now writes nothing unless it first read a JSON object.
#
# Losing the record is survivable because it is no longer the only night key: the
# boot-volume stamp carries the same date (design 3.4), and the driver's next
# write_state() rebuilds this file from scratch, which is what repairs it.
record_failover() {
  local repo="$1" outcome="$2" reason="$3" run_id="$4" run_url="$5" rc=0
  local f="${STATE_ROOT}/state/${repo}.last-run.json"
  if [[ ! -d "${STATE_ROOT}/state" ]]; then
    log "${repo}: state root unreachable; the outcome is recorded only in the boot-volume stamp"
    return 0
  fi
  if [[ ! -f "${f}" ]]; then
    log "${repo}: ${f} does not exist and will NOT be created; the boot-volume stamp holds the night key"
    return 0
  fi
  FO_OUTCOME="${outcome}" FO_REASON="${reason}" FO_RUN_ID="${run_id}" \
  FO_RUN_URL="${run_url}" FO_DATE="${TODAY_UTC}" FO_GH="$(gh_version)" \
  FO_URLOK="$(gh_run_url_supported && echo true || echo false)" \
  FO_AT="$(date +%Y-%m-%dT%H:%M:%S%z)" \
  "${PYBIN}" -c '
import json, os, sys
p = sys.argv[1]
try:
    with open(p) as fh:
        doc = json.load(fh)
except Exception:
    sys.exit(3)                      # readable-but-corrupt, or vanished since the -f
if not isinstance(doc, dict):
    sys.exit(3)
fo = {
    "enabled": True,
    "tier": "watchdog",
    "date": os.environ["FO_DATE"],
    "outcome": os.environ["FO_OUTCOME"],
    "reason": os.environ["FO_REASON"],
    "run_id": os.environ["FO_RUN_ID"],
    "run_url": os.environ["FO_RUN_URL"],
    "gh_version": os.environ["FO_GH"],
    "url_supported": os.environ["FO_URLOK"] == "true",
    "dispatched_at": os.environ["FO_AT"],
}
# The same latch rule write_state() gets (verify F3), so the invariant lives at
# BOTH writers instead of being an emergent property of gate ordering. Unreachable
# today -- gate 5 refuses before this function is called -- and that is exactly why
# it belongs here: it stops being true the moment a gate moves.
prev = doc.get("failover")
if (fo["date"] and isinstance(prev, dict)
        and prev.get("date") == fo["date"]
        and prev.get("outcome") == "dispatched"
        and fo["outcome"] != "dispatched"):
    prev = dict(prev)
    prev["superseded_by"] = ("watchdog " + fo["outcome"] + ": "
                             + (fo["reason"] or "no reason recorded"))[:200]
    fo = prev
doc["failover"] = fo
tmp = p + ".tmp"
with open(tmp, "w") as fh:
    json.dump(doc, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp, p)
' "${f}" 2>/dev/null || rc=$?
  case "${rc}" in
    0) ;;
    3) log "${repo}: ${f} is unreadable or is not a JSON object; NOT overwriting it -- the outcome is recorded only in the boot-volume stamp" ;;
    *) log "${repo}: could not record the failover outcome in ${f} (rc ${rc})" ;;
  esac
  return 0
}

# --------------------------------------------------------------- the dispatch

DISPATCH_RUN_ID=""
DISPATCH_RUN_URL=""

# Every element of this call is load-bearing; see design section 4.1.
#   -f dry_run=false     MANDATORY. dry_run DEFAULTS TO TRUE in the workflow, so a
#                        bare `gh workflow run` creates a run that does nothing.
#                        This is the single most likely implementation error.
#   -f force=false       explicit, not omitted. `force` bypasses the no-op guard
#                        ONLY -- it never reaches the overlap gate -- and passing it
#                        explicitly immunises this call against a future default
#                        flip while keeping CI's guard the authority.
#   -f skip_release=false  the release is the entire point of the failover.
do_dispatch() {
  local repo="$1" rc=0 out=""
  # The stamp goes down BEFORE the call, not after. The window that produces a
  # double fire is a POST that lands server-side while this process dies, and
  # `attempting` is the honest label for it -- read as fired (fail closed), because
  # we do not know. A COMPLETED failure is rewritten to `error` below and is read
  # as not fired, matching last-run.json exactly. This is also the single funnel
  # BOTH tiers pass through, which is what makes a driver-tier dispatch at 02:47
  # visible to a watchdog firing at 03:05 after the volume has gone away.
  write_stamp "${repo}" attempting "" ""
  set +e
  out="$(with_timeout "${TIMEOUT}" gh workflow run "${WORKFLOW}" \
           -R "${OWNER}/${repo}" --ref "${REF}" \
           -f dry_run=false -f skip_release=false -f force=false 2>&1)"
  rc=$?
  set -e
  [[ -n "${out}" ]] && log "gh: ${out}"
  if [[ "${rc}" -ne 0 ]]; then
    PROBE_REASON="dispatch-failed rc=${rc}"
    write_stamp "${repo}" error "" ""
    return 1
  fi
  DISPATCH_RUN_URL="$(printf '%s\n' "${out}" \
    | sed -n 's|.*\(https://github\.com/[^ ]*/actions/runs/[0-9][0-9]*\).*|\1|p' | head -1)"
  DISPATCH_RUN_ID="${DISPATCH_RUN_URL##*/}"
  write_stamp "${repo}" dispatched "${DISPATCH_RUN_ID}" "${DISPATCH_RUN_URL}"
  return 0
}

# ------------------------------------------------------------- drift self-check
#
# The watchdog LaunchAgent runs ~/scripts/sced-failover.sh, not the repo copy, so a
# pass that changes executable bytes does not exist in production until the mirror
# step runs. Nothing detected that drift; two passes in a row have now changed these
# bytes (verify R6).
#
# ADVISORY, and SILENT when the repo copy is unreachable. The entire reason the boot
# volume holds a copy is the night /Volumes/PRO-G40 is not mounted, so "cannot
# compare" is a NORMAL state here and can never be an error. This function cannot
# refuse, cannot exit, and returns 0 on every path.
#
# Returns its verdict on stdout for run_status: same | drift | unknown.
install_copy_state() {
  local repo_copy="${WORKSPACE}/SCED-tools/scripts/sced-failover.sh"
  local self="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
  # Only meaningful when THIS is the boot-volume copy; when the repo copy is the one
  # running, it is trivially identical to itself.
  if [[ ! -r "${repo_copy}" || ! -r "${self}" ]]; then printf 'unknown'; return 0; fi
  if [[ "${repo_copy}" -ef "${self}" ]]; then printf 'same'; return 0; fi
  if cmp -s "${repo_copy}" "${self}"; then printf 'same'; else printf 'drift'; fi
  return 0
}

emit() {
  local outcome="$1" repo="$2" reason="$3"
  printf 'outcome: %s\n' "${outcome}"
  printf 'repo: %s/%s\n' "${OWNER}" "${repo}"
  printf 'ref: %s\n' "${REF}"
  printf 'run_id: %s\n' "${DISPATCH_RUN_ID}"
  printf 'run_url: %s\n' "${DISPATCH_RUN_URL}"
  printf 'gh: %s\n' "$(gh_version)"
  printf 'url_supported: %s\n' "$(gh_run_url_supported && echo true || echo false)"
  printf 'reason: %s\n' "${reason}"
}

# ------------------------------------------------------------- mode: dispatch

run_dispatch() {
  local repo="${REPO}" reason=""

  if ! gh_usable; then
    emit refused "${repo}" "${PROBE_REASON}"
    log "refused: ${PROBE_REASON}"
    return 2
  fi
  if already_fired_tonight "${repo}"; then
    emit refused "${repo}" "already-dispatched-tonight"
    log "refused: a failover was already dispatched for ${repo} tonight"
    return 5
  fi
  if ! draft_precondition_ok "${repo}" "${TAG}"; then
    emit refused "${repo}" "${PROBE_REASON}"
    log "refused: ${PROBE_REASON} (tag ${TAG})"
    return 3
  fi

  reason="run-url-unavailable (gh $(gh_version) < ${GH_MIN_VERSION})"
  gh_run_url_supported && reason=""

  if [[ "${NO_OP}" == true ]]; then
    emit would-dispatch "${repo}" "${reason:+${reason}; }--no-op rehearsal, nothing dispatched"
    log "would dispatch ${OWNER}/${repo} (${REASON:-no reason given})"
    return 0
  fi

  log "dispatching ${OWNER}/${repo} ${WORKFLOW}@${REF} (${REASON:-no reason given})"
  if ! do_dispatch "${repo}"; then
    emit error "${repo}" "${PROBE_REASON}"
    return 4
  fi
  emit dispatched "${repo}" "${reason}"
  log "dispatched: ${DISPATCH_RUN_URL:-run url unavailable}"
  return 0
}

# ------------------------------------------------------------- mode: watchdog

# Repos the failover is enabled for. SCED_SYNC_FAILOVER is a comma-separated
# ALLOWLIST and it is the kill switch: empty or unset means neither tier ever
# fires and the nightly behaves exactly as it did before this feature existed.
enabled_repos() {
  local want="${REPO}"
  local r
  for r in ${FAILOVER_REPOS}; do
    [[ -n "${want}" && "${r}" != "${want}" ]] && continue
    case ",${SCED_SYNC_FAILOVER:-}," in
      *",${r},"*) printf '%s\n' "${r}" ;;
    esac
  done
}

# Config when the volume is mounted, compiled-in literals when it is not. The KEY
# is passed through the environment and never interpolated into the program, the
# same discipline state_get() uses.
sched_hhmm_for() {
  local repo="$1" key="$2" v=""
  if [[ -r "${SCHEDULE_JSON}" ]]; then
    v="$(FO_REPO="${repo}" FO_KEY="${key}" "${PYBIN}" -c '
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
r = d.get("repos", {}).get(os.environ["FO_REPO"], {})
v = r.get(os.environ["FO_KEY"])
if not v:
    sys.exit(1)
print(v)
' "${SCHEDULE_JSON}" 2>/dev/null)" || v=""
  fi
  # Shape guard: hhmm_to_min() and the gate-4 comparison both index this as four
  # digits, and a malformed value would abort the pass under `set -e` arithmetic.
  case "${v}" in
    [0-9][0-9][0-9][0-9]) ;;
    *) v="" ;;
  esac
  [[ -n "${v}" ]] || v="$(fallback_hhmm "${repo}" "${key}")"
  printf '%s' "${v}"
}

latest_hhmm_for() { sched_hhmm_for "$1" latest_hhmm; }
local_hhmm_for()  { sched_hhmm_for "$1" local_hhmm; }

hhmm_to_min() { printf '%s' "$(( 10#${1:0:2} * 60 + 10#${1:2:2} ))"; }

# Gate 3. A LIVE pid holding the workspace lock means a run is in flight and its
# own finish() owns the outcome -- and, crucially, that a dispatch can never be
# issued into an in-flight AI stage, which is the primary R14 mitigation and a hard
# invariant rather than a timing assumption. A STALE lock (dead pid) is the
# opposite signal: the driver died without running cleanup(), which is exactly a
# report failure, so it does NOT refuse.
#
# PARSED BY KEY, the way it is written (verify R7). The driver writes
#   pid=%s repo=%s started=%s host=%s        (daily-sync-local.sh:1152)
# and reads it back by key itself. This function used to take the first digit run on
# line 1 by POSITION, which is correct only for as long as `pid=` stays first. Get
# the wrong number and `kill -0` may find an unrelated LIVE pid -- and then gate 3
# refuses permanently, silently, on every future night.
#
# The positional form is kept as an explicit FALLBACK so an owner file written by an
# older driver still parses. It can only ever find more, never something different:
# it is consulted only when the keyed form found nothing at all.
lock_is_live() {
  local owner="${LOCK}/owner" pid=""
  [[ -r "${owner}" ]] || return 1
  # THREE FIXED programs, never one with alternation: BSD sed has no `\|` in a BRE
  # and would match it literally (the exact shape that made design.fix-f5-f11.md's
  # S5 harness pass vacuously under macOS sed). Leading `pid=` first, then `pid=`
  # anywhere on the line, then the legacy positional form.
  pid="$(sed -n '1s/^pid=\([0-9][0-9]*\).*/\1/p' "${owner}" 2>/dev/null | head -1)"
  [[ -n "${pid}" ]] || pid="$(sed -n '1s/.*[^A-Za-z_]pid=\([0-9][0-9]*\).*/\1/p' "${owner}" 2>/dev/null | head -1)"
  if [[ -z "${pid}" ]]; then
    pid="$(sed -n '1s/[^0-9]*\([0-9][0-9]*\).*/\1/p' "${owner}" 2>/dev/null | head -1)"
    [[ -n "${pid}" ]] && log "lock owner file carries no pid= key; falling back to the positional parse (pid ${pid})"
  fi
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" 2>/dev/null
}

# Gate 4. finished_at at or after tonight's SCHEDULED local start means the driver
# reported and its own predicate already ruled -- do NOT second-guess a deliberate
# refusal of exit 10 or 11. The one exception is a recorded dispatch ERROR, which
# is a transient-network retry and the single case where the watchdog overrides.
#
# The bound is local_hhmm, NOT latest_hhmm: see fallback_hhmm() above. And it is a
# LOWER bound on finished_at, not "some time today" -- a manual run at 00:30 is not
# tonight's scheduled run, and treating it as one is what let a 02:47 no-show fall
# through to GitHub's 72-360-minute-late cron (verify F4).
reported_tonight() {
  local repo="$1" fin="" fo_date="" fo_out="" hhmm=""
  fin="$(state_get "${repo}" finished_at 2>/dev/null)" || return 1
  [[ -n "${fin}" ]] || return 1
  fo_date="$(state_get "${repo}" failover.date 2>/dev/null || echo '')"
  fo_out="$(state_get "${repo}" failover.outcome 2>/dev/null || echo '')"
  if [[ "${fo_date}" == "${TODAY_UTC}" && "${fo_out}" == "error" ]]; then
    return 1
  fi
  hhmm="$(local_hhmm_for "${repo}")"
  if [[ -z "${hhmm}" ]]; then
    log "${repo}: no local_hhmm from config or fallback; treating the night as unreported"
    return 1
  fi
  # finished_at carries a local offset; both sides of the comparison are naive
  # local, which is the same assumption the driver's own NOW_MIN arithmetic makes.
  FO_FIN="${fin}" FO_HHMM="${hhmm}" "${PYBIN}" -c '
import datetime as dt, os, sys
try:
    t = dt.datetime.strptime(os.environ["FO_FIN"][:19], "%Y-%m-%dT%H:%M:%S")
except Exception:
    sys.exit(1)
hhmm = os.environ["FO_HHMM"]
try:
    start = dt.datetime.combine(dt.datetime.now().date(),
                                dt.time(int(hhmm[:2]), int(hhmm[2:4])))
except Exception:
    sys.exit(1)
sys.exit(0 if t >= start else 1)
' 2>/dev/null
}

run_watchdog() {
  local rc_any=0 repo="" latest="" now_min=0 latest_min=0 acted=0 copy_state=""
  now_min=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))

  if [[ -z "$(enabled_repos)" ]]; then
    log "no repo enabled (SCED_SYNC_FAILOVER is empty or does not name one)"
    return 0
  fi

  # Advisory only; never gates anything (verify R6).
  #
  # BELOW the short-circuit, not above it (verify round 4, N7). This is the first
  # thing in this function that touches /Volumes/PRO-G40, and neither the `[[ -r ]]`
  # nor the `cmp` inside it is bounded by with_timeout. Above the short-circuit a
  # mounted-but-HUNG volume would stall even a pass with nothing enabled to
  # dispatch; below it, a pass that has no work to do returns without touching the
  # volume at all. It buys nothing on the armed path -- latest_hhmm_for() and
  # lock_is_live() read the same volume just as unboundedly a few lines down, and
  # every notify() is further down still -- so a hung volume still costs that night
  # silently. That exposure is pre-existing and is NOT closed here; this only
  # refuses to widen it to the no-op case.
  copy_state="$(install_copy_state)"
  if [[ "${copy_state}" == "drift" ]]; then
    log "WARNING: this copy differs from ${WORKSPACE}/SCED-tools/scripts/sced-failover.sh -- the repo copy is the source of truth and this one is what runs at 03:05"
  fi

  while read -r repo; do
    [[ -n "${repo}" ]] || continue
    latest="$(latest_hhmm_for "${repo}")"
    if [[ -z "${latest}" ]]; then
      log "${repo}: cannot determine latest_hhmm, skipping"
      continue
    fi
    latest_min="$(hhmm_to_min "${latest}")"

    # Gate 2 -- too early. This is what makes ONE shared fire time correct for two
    # repos with different windows: at 03:05 SCED-downloads (latest 02:57) is
    # eligible and SCED (latest 03:27) is not.
    if [[ "${now_min}" -lt $(( latest_min + GRACE_MIN )) ]]; then
      log "${repo}: too early (now $(date +%H:%M) < ${latest} + ${GRACE_MIN}m)"
      continue
    fi
    # Gate 3 -- a run is in flight.
    if lock_is_live; then
      log "${repo}: a run holds the lock; its own finish() owns the outcome"
      continue
    fi
    # Gate 5 -- already fired tonight.
    if already_fired_tonight "${repo}"; then
      log "${repo}: a failover was already dispatched tonight"
      continue
    fi
    # Gate 4 -- the driver reported; do not second-guess its predicate.
    if reported_tonight "${repo}"; then
      log "${repo}: the local tier reported tonight; nothing to do"
      continue
    fi

    acted=1
    # `|| MODE == status` was here and was DEAD (verify R8): main dispatches
    # `status` to run_status(), so this function only ever runs with MODE=watchdog.
    if [[ "${NO_DISPATCH}" == true ]]; then
      log "${repo}: WOULD dispatch (local tier did not report tonight)"
      emit would-dispatch "${repo}" "watchdog rehearsal"
      continue
    fi
    if ! gh_usable; then
      log "${repo}: cannot dispatch -- ${PROBE_REASON}"
      record_failover "${repo}" error "${PROBE_REASON}" "" ""
      notify "failover watchdog could not dispatch ${repo}" \
             "The local tier did not report tonight and the watchdog could not reach GitHub: ${PROBE_REASON}. CI was NOT armed." 15158332
      rc_any=2
      continue
    fi
    REPO="${repo}"
    if do_dispatch "${repo}"; then
      record_failover "${repo}" dispatched "watchdog: local tier did not report tonight" \
                      "${DISPATCH_RUN_ID}" "${DISPATCH_RUN_URL}"
      emit dispatched "${repo}" "watchdog: local tier did not report tonight"
      notify "failover watchdog armed CI for ${repo}" \
             "The local tier did not report tonight, so the GitHub Actions fallback was dispatched directly instead of waiting for its schedule. ${DISPATCH_RUN_URL:-run url unavailable}" 15105570
    else
      record_failover "${repo}" error "${PROBE_REASON}" "" ""
      emit error "${repo}" "${PROBE_REASON}"
      notify "failover watchdog could not dispatch ${repo}" \
             "Dispatch failed: ${PROBE_REASON}. CI was NOT armed." 15158332
      rc_any=4
    fi
  done <<< "$(enabled_repos)"

  [[ "${acted}" -eq 0 ]] && log "watchdog pass complete: nothing to do"
  return "${rc_any}"
}

# --------------------------------------------------------------- mode: status

run_status() {
  local repo="" latest=""
  printf 'workspace: %s\n' "${WORKSPACE}"
  printf 'state_root: %s%s\n' "${STATE_ROOT}" "$([[ -d "${STATE_ROOT}" ]] || printf ' (UNREACHABLE)')"
  printf 'wd_state_dir: %s%s\n' "${WD_STATE_DIR}" "$([[ -d "${WD_STATE_DIR}" ]] || printf ' (absent)')"
  printf 'gh: %s\n' "$(gh_version)"
  printf 'url_supported: %s\n' "$(gh_run_url_supported && echo true || echo false)"
  printf 'enabled: %s\n' "${SCED_SYNC_FAILOVER:-(none)}"
  printf 'lock_live: %s\n' "$(lock_is_live && echo true || echo false)"
  printf 'install_copy: %s\n' "$(install_copy_state)"
  for repo in ${FAILOVER_REPOS}; do
    latest="$(latest_hhmm_for "${repo}")"
    printf -- '--- %s\n' "${repo}"
    printf '  latest_hhmm: %s\n' "${latest}"
    printf '  finished_at: %s\n' "$(state_get "${repo}" finished_at 2>/dev/null || echo '(none)')"
    printf '  exit_code: %s\n' "$(state_get "${repo}" exit_code 2>/dev/null || echo '(none)')"
    printf '  failover.date: %s\n' "$(state_get "${repo}" failover.date 2>/dev/null || echo '(none)')"
    printf '  failover.outcome: %s\n' "$(state_get "${repo}" failover.outcome 2>/dev/null || echo '(none)')"
    # Both stores side by side, so drift between them is a one-command diagnosis.
    if stamp_read "${repo}"; then
      printf '  stamp.date: %s\n'    "${STAMP_DATE:-(none)}"
      printf '  stamp.outcome: %s\n' "${STAMP_OUTCOME:-(none)}"
    else
      printf '  stamp: (none)\n'
    fi
    printf '  already_fired: %s\n' "$(already_fired_tonight "${repo}" && echo true || echo false)"
    printf '  reported_tonight: %s\n' "$(reported_tonight "${repo}" && echo true || echo false)"
  done
  return 0
}

# ---------------------------------------------------------------------- main

case "${MODE}" in
  dispatch) run_dispatch ;;
  watchdog) run_watchdog ;;
  status)   run_status ;;
esac
