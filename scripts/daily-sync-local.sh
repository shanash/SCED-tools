#!/usr/bin/env bash
# daily-sync-local.sh
#
# Local (on-Mac) primary path for the daily upstream rebase + redeploy, running
# an hour ahead of the GitHub Actions `daily-upstream-sync.yml` that remains in
# place as the fallback. Design: .am/local-daily-rebase-deploy/design.md §5.
#
# The sequence mirrors the GHA workflow step for step. Repo-specific behaviour is
# confined to the three functions marked `TAG-BASE` / `BUILD` / `VERIFY`, named
# after the corresponding hunks in that workflow so the two can be diffed by eye.
#
# WHAT A SINGLE `launchctl bootstrap` DELEGATES
#   Loading the LaunchAgent authorises this script to do all of the following
#   unattended, once per night, without asking again:
#     (a) force-push the `korean` branch of a public fork, leased to the SHA it
#         observed at the start of the run;
#     (b) create a public release that becomes `releases/latest` -- the thing every
#         mod user downloads;
#     (c) create and delete `auto/korean-backup-*` branches on that fork;
#     (d) send paths and SHAs outbound to a Discord webhook;
#     (e) execute upstream code (`git rebase upstream/main`) and a locally built,
#         unreleased Mach-O binary (TTSModManager-Darwin) on this machine;
#     (f) when -- and only when -- SCED_SYNC_AI_RESOLVE names this repo: send this
#         repository's conflicting content to Anthropic's API and invoke an LLM
#         agent holding file-write and `git rebase` capability inside a throwaway
#         detached worktree, to resolve a tripped overlap gate.
#         This is the single largest thing a `bootstrap` authorises, and it is off
#         by default: SCED_SYNC_AI_RESOLVE is empty in ~/.config/sced-sync/env
#         until someone puts a repo name in it, and blanking that one line is the
#         kill switch.
#     (g) when -- and only when -- SCED_SYNC_FAILOVER names this repo: on a run
#         that FAILS, dispatch the GitHub Actions fallback directly rather than
#         waiting for its schedule. Delegated to sced-failover.sh; this file only
#         decides whether dispatching is safe. Off by default and revoked by
#         blanking that one line, exactly like (f).
#   `launchctl bootout gui/$(id -u)/com.shanash.sced-daily-sync.<repo>` revokes it
#   in seconds, and the GHA fallback keeps running either way.
#
# WHAT IT WILL NEVER DO
#   Resolve a rebase conflict ITSELF. That claim is still literally true of this
#   file: there is no `-X ours`, no `--strategy`, no `rebase --continue` and no
#   `rerere` anywhere in it, and the grep in .am/claude-driven-rebase-deploy/
#   design.md S13 is the standing proof.
#
#   What changed is what a TRIPPED gate hands off to. With the stage disabled --
#   the default, and the only state until SCED_SYNC_AI_RESOLVE names a repo -- the
#   run stops and a human decides, exactly as before. With it enabled for a repo,
#   the tripped gate hands off to resolve-rebase-with-ai.sh, which drives a real
#   rebase in a throwaway worktree, verifies the result against nine checks, and
#   commits one empty attestation; this script then pushes it or does not.
#
#   The never-list is therefore: never push an unverified tree; never resolve when
#   the stage is disabled; never `rerere`; never touch the primary checkouts (now
#   asserted at runtime, not merely by this file's structure); and never let
#   anything but this script push.
#
#   The overlap gate below remains a stricter predicate than git's own conflict
#   signal -- on 2026-06-27 both sides had touched 142 files and git reported only
#   18 -- and it is still not bypassable: --force never reaches it.
#
# Usage:
#   daily-sync-local.sh --repo <SCED|SCED-downloads> [options]
#
#     --repo NAME       required; a flag, not a positional, so a bare invocation
#                       can never start a live run
#     --dry-run         do everything through build+verify, but push nothing,
#                       prune nothing, and create no release
#     --skip-build      stop after the force-push; no build, verify or release
#     --force           ignore the no-op guard and the staleness guard.
#                       NEVER ignores the overlap gate.
#     --no-ai           force the claude-driven resolution stage off for this run.
#                       The stage is opt-in per repo via SCED_SYNC_AI_RESOLVE and
#                       is off entirely when that is empty; --no-ai is how a single
#                       manual run opts out while it is on. Note that --dry-run
#                       DOES run the stage -- it is the rehearsal path -- and simply
#                       never pushes.
#     --no-failover     do not dispatch the GHA fallback if this run fails. The
#                       failover is opt-in per repo via SCED_SYNC_FAILOVER and is
#                       off entirely when that is empty; --no-failover is how a
#                       single manual run opts out while it is on. --dry-run never
#                       dispatches regardless.
#     --no-notify       do not POST to Discord (the payload is still logged)
#     --keep-scratch    leave the scratch worktree in place for debugging
#     --keep-backups N  backup branches to retain (default 14)
#     --help
#
# Exit codes:
#   0  released, or nothing to do (no-op guard)
#   1  usage error
#   2  preflight failed (volume / deps / env-file perms / disk)
#   3  another run holds the lock
#   4  skipped: staleness guard -- the GHA fallback owns tonight
#   5  schedule interlock missing: launchd fired but the plist carried no
#      SCED_SYNC_SCHED_* bound -- the trigger and the guard have diverged
#  10  skipped: overlap gate tripped -- human adjudication required
#  11  skipped: AI resolution stopped by policy -- human adjudication required
#  20  fetch failed
#  21  overlap gate script failed (the gate itself errored; the gate did NOT trip)
#  30  rebase failed after a clean gate (unexpected)
#  40  backup push, or korean force-push rejected (lease conflict)
#  50  tag derivation failed / tag already exists
#  60  build failed
#  62  Darwin build-equivalence preflight failed (checksum / case / duplicate object)
#  63  asset verification failed
#  64  AI stage: shadow seed or overlap classification failed
#  65  AI stage: claude unavailable, credential failure, or timeout
#  66  AI stage: attestation manifest invalid (schema / coverage / binding)
#  67  AI stage: the resolved tree failed verification
#  70  draft release creation, asset upload, or go-live failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# --------------------------------------------------------------------- options

REPO=""
DRY_RUN=false
SKIP_BUILD=false
FORCE=false
NO_AI=false
NO_FAILOVER=false
NO_NOTIFY=false
KEEP_SCRATCH=false
KEEP_BACKUPS=""

usage() { sed -n '/^# Usage:/,/^#  70/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)         REPO="${2:-}"; shift 2 ;;
    --dry-run)      DRY_RUN=true; shift ;;
    --skip-build)   SKIP_BUILD=true; shift ;;
    --force)        FORCE=true; shift ;;
    --no-ai)        NO_AI=true; shift ;;
    --no-failover)  NO_FAILOVER=true; shift ;;
    --no-notify)    NO_NOTIFY=true; shift ;;
    --keep-scratch) KEEP_SCRATCH=true; shift ;;
    --keep-backups) KEEP_BACKUPS="${2:-}"; shift 2 ;;
    --help|-h)      usage; exit 0 ;;
    *)              echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${REPO}" ]]; then
  echo "ERROR: specify --repo <SCED|SCED-downloads>" >&2
  exit 1
fi

case "${REPO}" in
  SCED|SCED-downloads) ;;
  *) echo "ERROR: unknown repo '${REPO}' (expected SCED or SCED-downloads)" >&2; exit 1 ;;
esac

if [[ -n "${KEEP_BACKUPS}" ]]; then
  case "${KEEP_BACKUPS}" in
    ''|*[!0-9]*) echo "ERROR: --keep-backups must be a non-negative integer" >&2; exit 1 ;;
  esac
fi

# ------------------------------------------------------------ environment file

# The launchd wrapper normally loads this and then execs us, but a manual run --
# a --dry-run, or triage after a stall -- calls this script directly. Loading it
# here as well makes both paths behave identically, and it has to happen before
# the parameters below so an override in the file actually takes effect.
#
# The mode check comes first and is fatal: a secrets file the rest of the machine
# can read is not one to source.
ENV_FILE="${SCED_SYNC_ENV_FILE:-${HOME}/.config/sced-sync/env}"
if [[ -r "${ENV_FILE}" ]]; then
  env_perm="$(stat -f '%Lp' "${ENV_FILE}" 2>/dev/null || echo '???')"
  if [[ "${env_perm}" != "600" ]]; then
    echo "ERROR: ${ENV_FILE} must be mode 600, found ${env_perm}" >&2
    exit 2
  fi
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
elif [[ "${NO_NOTIFY}" != true ]]; then
  echo "ERROR: env file missing or unreadable: ${ENV_FILE}" >&2
  exit 2
fi

# ------------------------------------------------------------------ parameters

WORKSPACE="${SCED_SYNC_WORKSPACE:-${REPO_ROOT}}"
STATE_ROOT="${SCED_SYNC_STATE_ROOT:-${WORKSPACE}/.local-sync}"
TTSMM="${SCED_SYNC_TTSMM:-${WORKSPACE}/TTSModManager-Darwin}"
TTSMM_SHA256="${SCED_SYNC_TTSMM_SHA256:-d40df046b928a224295c2b8be2cd6543bd27eb0d739e8346afed33ff1f26f1ff}"
KEEP_BACKUPS="${KEEP_BACKUPS:-${SCED_SYNC_KEEP_BACKUPS:-14}}"
KEEP_LOGS="${SCED_SYNC_KEEP_LOGS:-30}"
NET_TIMEOUT="${SCED_SYNC_NET_TIMEOUT:-120}"
BUILD_TIMEOUT="${SCED_SYNC_BUILD_TIMEOUT:-1800}"
# Bounds every SHORT-LIVED python3 helper: the state write, the notify payload, the
# two JSON readers, and the four JSON checks in the verify stage (see repo_verify).
# NOT build.py / minify.py -- those are real work and run under BUILD_TIMEOUT, so
# raising this knob will not move them.
# The costliest bounded call is the SCED mod-JSON parse: ~0.1 s for an 11.7 MB
# asset, measured 2026-08-09, so 30 s is ~200x it and the rest are far cheaper.
# A trip therefore means something is genuinely wedged rather than slow. Added
# 2026-08-09: a macOS TCC consent prompt for the Homebrew python3
# (kTCCServiceSystemPolicyRemovableVolumes on /Volumes/PRO-G40) that nobody could
# answer at 02:17 turned write_state() into a 6h15m block that held the workspace
# lock and could not report itself. Every site below already had a failure branch;
# all a bound does is reach it.
PY_TIMEOUT="${SCED_SYNC_PY_TIMEOUT:-30}"
MAX_RUN_SECONDS="${SCED_SYNC_MAX_RUN_SECONDS:-3600}"
ASSET_FLOOR_PCT=95
OWNER="shanash"
MARKER="sced-local-sync: assets-already-attached"

# --- claude-driven rebase resolution (.am/claude-driven-rebase-deploy/design.md)
#
# SCED_SYNC_AI_RESOLVE is a comma-separated repo ALLOWLIST and it is the kill
# switch: empty or unset means the stage never runs and this script behaves
# exactly as it did before the feature existed. Everything below only matters once
# a repo is named in it.
AI_MAX_OVERLAP="${SCED_SYNC_AI_MAX_OVERLAP:-50}"
AI_CI_OFFSET_MIN="${SCED_SYNC_AI_CI_OFFSET_MIN:-60}"
KEEP_AI_RUNS="${SCED_SYNC_KEEP_AI_RUNS:-30}"

# --- GHA failover (.am/local-failure-gha-failover/design.md)
#
# SCED_SYNC_FAILOVER is a comma-separated repo ALLOWLIST and it is the kill
# switch, exactly like SCED_SYNC_AI_RESOLVE above: empty or unset means the
# dispatch never happens and this script behaves exactly as it did before the
# feature existed. 60 s rather than NET_TIMEOUT's 120: the measured dispatch
# completed end to end in 11 s, and this call sits inside finish(), which holds
# the workspace lock until it returns. It bounds the `gh workflow run` call ONLY,
# never sced-failover.sh as a whole -- the COST note in finish() has the real sum.
FAILOVER_TIMEOUT="${SCED_SYNC_FAILOVER_TIMEOUT:-60}"

# Under launchd both bounds arrive from the plist, which carries them alongside
# the StartCalendarInterval trigger so the two cannot drift apart. The literals
# below are MANUAL-RUN FALLBACKS ONLY; the source of truth is
# SCED-tools/config/sync-schedule.json, applied by
# SCED-tools/scripts/sced-schedule.sh (`verify` reports it when they diverge).
case "${REPO}" in
  SCED)           SCHED_HHMM="${SCED_SYNC_SCHED_SCED:-0247}";           LATEST_HHMM="${SCED_SYNC_LATEST_START_SCED:-0327}" ;;
  SCED-downloads) SCHED_HHMM="${SCED_SYNC_SCHED_SCED_DOWNLOADS:-0217}"; LATEST_HHMM="${SCED_SYNC_LATEST_START_SCED_DOWNLOADS:-0257}" ;;
esac

REPO_PATH="${REPO_ROOT}/${REPO}"
WT="${STATE_ROOT}/scratch/${REPO}"
LOCK="${STATE_ROOT}/run/daily-sync.lock"
SIGFILE="${STATE_ROOT}/state/${REPO}.notify-signature"
STATEFILE="${STATE_ROOT}/state/${REPO}.last-run.json"

# No prompt may ever block this run: launchd has no terminal to answer one.
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/usr/bin/true
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
export GH_PROMPT_DISABLED=1
export GH_NO_UPDATE_NOTIFIER=1

STARTED_EPOCH="$(date +%s)"
STARTED_AT="$(date +%Y-%m-%dT%H:%M:%S%z)"
RUN_STAMP="$(date +%Y%m%d-%H%M%S)"

# Run-scoped state, filled in as the run proceeds and reported at the end.
DECISION="unknown"
FORK_SHA=""
KOREAN_SHA=""
UPSTREAM_SHA=""
UPSTREAM_DATE=""
MERGE_BASE=""
OVERLAP_COUNT=0
# Digest of the overlap SET, not its size. The notification signature is keyed on
# this so a stall that swaps one path for another breaks through the throttle
# instead of looking "unchanged" at a constant count.
OVERLAP_SHA256=""
BACKUP=""
BASE=""
TAG=""
PREV_TAG=""
N_NEW=0
N_PREV=0
RELEASE_ID=""
RELEASE_URL=""
PROVENANCE=""
FILENAME=""
LOG_FILE=""
LOCK_HELD=false
NOTIFIED=false
EXTRA=""

# AI stage, run-scoped. AI_ACTIVE is the ONE flag that branches the rebase; the
# rest is reporting. AI_RESULT_JSON is the file write_state() embeds verbatim.
AI_ACTIVE=false
AI_BIN=""
AI_REASON=""
AI_OUTCOME="skipped"
AI_RUN_DIR=""
AI_RESULT_JSON=""

# GHA failover, run-scoped.
#
# PUSHED is the flag the dispatch predicate keys on, and it exists because the
# EXIT CODE ALONE IS NOT ENOUGH: exit 40 is reached from two places on opposite
# sides of the force-push -- "could not push the backup branch" (:1328) and
# "korean force-push rejected (lease conflict)" (:1440). The second of those means
# something else is writing korean right now -- the worst possible moment to hand
# CI a run that will force-push it.
#
# Quoted as well as cited (verify round 5, N14). The two numbers that stood here
# were wrong from the commit that introduced them: written against the pre-edit
# file, then shifted by that same commit's own insertions, and shifted a further
# +30 by the R10 fix below. A citation that carries its target's text drifts
# loudly instead of silently.
PUSHED=false
FAILOVER_ATTEMPTED=false
FAILOVER_OUTCOME="off"
FAILOVER_REASON=""
FAILOVER_SIG="fo-off"
# Which CLASS of refusal failover_should_run() produced, and the ONLY thing that
# decides `off` from `refused`. "off" = the failover does not apply to this run at
# all (gates 1-4: --dry-run, --no-failover, repo not in the allowlist, or the run
# did not fail). "refused" = it applies and the predicate deliberately declined
# (gates 5-8). The 03:00 operator needs those apart: the first is a config answer,
# the second is a ruling with its three inputs recorded beside it.
FAILOVER_CLASS="off"
FAILOVER_RUN_ID=""
FAILOVER_RUN_URL=""
FAILOVER_LINE=""
FAILOVER_FIELD=""

# --------------------------------------------------------------------- logging

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }

# Every git call goes through this.
#   core.hooksPath=/dev/null  the global pre-push hook exits 2 when git-lfs is not
#                             on PATH and the pre-commit hook is irrelevant here --
#                             this script creates no commits at all. The repos hold
#                             zero LFS objects, so bypassing it changes nothing.
#   rerere.enabled=false      no stored conflict resolution is ever replayed.
#   core.ignorecase           deliberately NOT touched: forcing it false on a
#                             case-insensitive volume produces phantom changes. The
#                             real exposure is caught by the case-collision
#                             preflight instead.
g() {
  local dir="$1"; shift
  git -C "${dir}" \
      -c core.hooksPath=/dev/null \
      -c gc.auto=0 \
      -c rerere.enabled=false \
      -c rebase.autoStash=false \
      -c core.quotePath=false \
      "$@"
}

# A watchdog subshell stands in for timeout(1). Homebrew coreutils DOES provide
# timeout(1) on this machine and the launchd wrapper now requires it, but this
# helper predates that and every caller below is a direct child, which is the case
# it handles correctly -- so it stays. Know its ONE limitation before reusing it:
# it kills the child only, so a caller whose child spawns a GRANDCHILD inside a
# command substitution still hangs, because the surviving grandchild holds the
# substitution's pipe open. That is why the wrapper's `bash -> git` probe uses
# GNU timeout, which signals the whole process group, and not this.
# `|| rc=$?` is required: under `set -e` a non-zero `wait` would kill the script
# before the caller can decide what the failure means.
with_timeout() {
  local secs="$1"; shift
  local rc=0 pid wd
  # `<&0` is LOAD-BEARING. With job control off -- every non-interactive run --
  # bash redirects an async command's stdin from /dev/null unless the command
  # carries an explicit redirection, so `with_timeout N python3 <<'PY' ... PY`
  # silently feeds python an EMPTY script. Measured 2026-08-09: without this the
  # notify payload build returns "" and the state file is written empty, with no
  # error anywhere.
  #
  # It is a no-op under launchd, where this script's own stdin is /dev/null -- but
  # NOT unconditionally, so do not restate it that way. On a manual terminal run
  # the wrapped `git`/`gh` children now inherit the tty. That is harmless today for
  # two checked reasons and both must stay true: GIT_TERMINAL_PROMPT=0, GIT_ASKPASS
  # and ssh BatchMode=yes are exported above so git cannot prompt, and no
  # with_timeout call sits inside a `while read` loop, so no wrapped child can eat
  # a loop's input. Verify both before adding a caller that reads stdin.
  "$@" <&0 & pid=$!
  # The watchdog's own stdout MUST be closed. Most calls here sit inside a command
  # substitution, and a background process that inherits the substitution's pipe
  # keeps it open: the substitution then blocks for the whole timeout even though
  # the real command finished in a second. Measured as a flat 120 s added to every
  # `gh` call before this redirect existed.
  { sleep "${secs}"; kill -TERM "${pid}" 2>/dev/null; sleep 5; kill -KILL "${pid}" 2>/dev/null; } >/dev/null 2>&1 &
  wd=$!
  wait "${pid}" || rc=$?
  kill "${wd}" 2>/dev/null || true
  wait "${wd}" 2>/dev/null || true
  return "${rc}"
}

# ------------------------------------------------------- AI resolution stage
#
# This script still drives NO rebase resolution of its own: when the stage is
# enabled the tripped gate hands off to resolve-rebase-with-ai.sh, which is
# treated as a black box with an exit-code contract exactly as
# check-upstream-overlap.sh already is. That is what keeps this file free of
# `git add`, `git commit`, `rebase --continue`, `-X ours` and `rerere`.

# Runtime resolution, never a hardcoded nvm path: node upgrades move it, and
# `claude` is NOT on the launchd PATH. `sort -V` rather than plain `tail -1`,
# because lexical order ranks v9.x above v20.18.1.
resolve_claude() {
  local c=""
  if [[ -n "${SCED_SYNC_CLAUDE_BIN:-}" ]]; then
    [[ -x "${SCED_SYNC_CLAUDE_BIN}" ]] && printf '%s\n' "${SCED_SYNC_CLAUDE_BIN}"
    return
  fi
  c="$(command -v claude 2>/dev/null || true)"
  if [[ -z "${c}" ]]; then
    c="$(ls -d "${HOME}"/.nvm/versions/node/*/bin/claude 2>/dev/null | LC_ALL=C sort -V | tail -1 || true)"
  fi
  [[ -n "${c}" && -x "${c}" ]] && printf '%s\n' "${c}"
}

# Six gates, every failure logged and NON-FATAL: a night that cannot run the stage
# degrades to today's behaviour -- a stop at the gate -- rather than failing.
# Always called from an `if` condition, which is what makes the `&& { ...; return 1; }`
# form safe under `set -e`.
ai_should_run() {
  if [[ "${NO_AI}" == true ]]; then
    AI_REASON="--no-ai"; log "ai: skipped (--no-ai)"; return 1
  fi
  case ",${SCED_SYNC_AI_RESOLVE:-}," in
    *",${REPO},"*) ;;
    *) AI_REASON="repo not enabled"
       log "ai: skipped (${REPO} not in SCED_SYNC_AI_RESOLVE)"; return 1 ;;
  esac
  AI_BIN="$(resolve_claude)"
  if [[ -z "${AI_BIN}" ]]; then
    AI_REASON="no binary"; log "ai: skipped (no claude binary found)"; return 1
  fi
  if [[ -z "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    AI_REASON="no credential"
    log "ai: skipped (no credential in ${ENV_FILE})"; return 1
  fi
  # 50, not the audit path's 2000. Approach (b) holds the whole resolution in one
  # session inside one wall-clock bound; the ceiling is the scoping decision made
  # explicit, not a tuning knob.
  if [[ "${OVERLAP_COUNT}" -gt "${AI_MAX_OVERLAP}" ]]; then
    AI_REASON="overlap too large (${OVERLAP_COUNT} > ${AI_MAX_OVERLAP})"
    log "ai: skipped (${AI_REASON})"; return 1
  fi
  if [[ $(( $(date +%s) - STARTED_EPOCH )) -ge 300 ]]; then
    AI_REASON="late start"
    log "ai: skipped (already $(( $(date +%s) - STARTED_EPOCH ))s into the run)"; return 1
  fi
  return 0
}

# ------------------------------------------------------------------- failover

# R12 / design Decision 3. The "already fired tonight" record. It lives in
# last-run.json, is keyed by the UTC date, and therefore SELF-EXPIRES with no
# cleanup step at all -- the exact property Alt-7 lacked when it was rejected for
# state leakage. It never touches a remote, a ref or a lock.
#
# The whole nightly band (02:17-03:35 KST) maps to 17:17-18:35 UTC of the previous
# day, so one UTC date groups one night. Same convention as the backup branch name.
# THE BOOT-VOLUME STAMP IS AN OR HERE TOO (verify R10). This is the driver's own
# copy of sced-failover.sh's gate 5, and it was missing gate 5's second store.
#
# design 5.1(e) ruled the omission acceptable, but its argument only covers one of
# the two consumers: the gate at the dispatch site is backstopped by the sub-script
# refusing with exit 5, because that site actually calls the sub-script. The AI
# push-window guard does NOT -- it reads this predicate and pushes. So on a night
# where last-run.json is lost or corrupted between two runs, the AI stage saw no
# live dispatch and could push into a dispatched CI run's window: the R14 race, by
# the one path R14's mitigations do not cover.
#
# Same two fixed `sed` programs, same UTC date key and the same `error`-is-not-fired
# rule as the sub-script, so the two predicates cannot disagree. The read is an OR,
# so it can only ever make this MORE conservative -- it can never cause a dispatch.
failover_already_fired() {
  local wd_dir="${SCED_SYNC_FAILOVER_STATE_DIR:-${HOME}/.local/state/sced-failover}"
  local stamp="${wd_dir}/${REPO}.dispatch" sdate="" soutcome="" fo_rc=0
  if [[ -r "${STATEFILE}" ]]; then
    # `env` rather than a bare assignment prefix: with_timeout runs "$@", which
    # cannot carry one. A timeout here reads as "not recorded as fired", which is
    # the same answer an unreadable state file already gives, so the boot-volume
    # stamp below still gets its say -- the OR can only make this more conservative.
    with_timeout "${PY_TIMEOUT}" env FO_TODAY="$(date -u +%Y%m%d)" python3 -c '
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
f = d.get("failover") or {}
sys.exit(0 if f.get("date") == os.environ["FO_TODAY"] and f.get("outcome") == "dispatched" else 1)
' "${STATEFILE}" 2>/dev/null || fo_rc=$?
    [[ "${fo_rc}" -eq 0 ]] && return 0
    # 1 is the designed "not recorded as fired" and is silent on purpose -- it is
    # the common case. Anything else means the predicate did not actually RUN:
    # 143 is the bound's SIGTERM, 2 a python crash. The answer is the same either
    # way (fall through to the stamp), but a disputed dispatch is audited from this
    # log, and "the state file said no" and "we never got to ask it" are different
    # claims. Not logging that was the one place silence survived this change.
    if [[ "${fo_rc}" -ne 1 ]]; then
      log "failover: the state-file predicate did not run (rc ${fo_rc}); the boot-volume stamp is now the only evidence"
    fi
  fi
  [[ -r "${stamp}" ]] || return 1
  sdate="$(sed -n 's/^date: *//p' "${stamp}" 2>/dev/null | head -1)"
  soutcome="$(sed -n 's/^outcome: *//p' "${stamp}" 2>/dev/null | head -1)"
  # An unparseable stamp FAILS OPEN, exactly as stamp_says_fired() does: a stamp
  # read as fired is only ever rewritten by a dispatch, so failing closed on a
  # corrupt one would suppress this predicate with no self-healing path.
  [[ "${sdate}" == "$(date -u +%Y%m%d)" ]] || return 1
  case "${soutcome}" in
    dispatched|attempting)
      log "failover: the boot-volume dispatch stamp records ${soutcome} for today"
      return 0 ;;
  esac
  return 1
}

# Eight gates, every refusal logged. Always called from an `if`, which is what
# makes the form safe under `set -e` -- same contract as ai_should_run() above.
#
# The allow-list is DEFAULT DENY on purpose: a future exit code cannot dispatch by
# accident, it has to be added here deliberately.
failover_should_run() {
  local code="$1" kind="$2"

  if [[ "${DRY_RUN}" == true ]]; then
    FAILOVER_CLASS="off"; FAILOVER_REASON="--dry-run"; log "failover: skipped (--dry-run)"; return 1
  fi
  if [[ "${NO_FAILOVER}" == true ]]; then
    FAILOVER_CLASS="off"; FAILOVER_REASON="--no-failover"; log "failover: skipped (--no-failover)"; return 1
  fi
  case ",${SCED_SYNC_FAILOVER:-}," in
    *",${REPO},"*) ;;
    *) FAILOVER_CLASS="off"; FAILOVER_REASON="repo not enabled"
       log "failover: skipped (${REPO} not in SCED_SYNC_FAILOVER)"; return 1 ;;
  esac
  # kind != fail covers exits 0 (noop / skip-build / dry-run / released),
  # 4 (stale-skip: the GHA fallback already owns tonight), 10 (overlap gate) and
  # 11 (AI-STOP). 10 and 11 are DESIGNED SKIPS where a human is required: CI has no
  # AI stage, so dispatching there trips the same gate, opens an Issue an hour
  # early, and -- because stalls are sticky -- would do so every night until someone
  # acts, while a completed adjudication sits unread in .local-sync/ai/.
  if [[ "${kind}" != fail ]]; then
    FAILOVER_CLASS="off"; FAILOVER_REASON="kind=${kind} is a designed outcome, not a failure"
    log "failover: skipped (${FAILOVER_REASON})"; return 1
  fi
  # ---- CLASS BOUNDARY. Gates 1-4 above are the "does not apply" class; 5-8 below
  # are the predicate proper and are reachable only when kind == fail. A new gate
  # must declare FAILOVER_CLASS explicitly and must go on the correct side of this
  # line -- failover_dispatch() reads nothing else to tell `off` from `refused`.
  case "${code}" in
    2|5|20|21|30|40|50|60|62|63|70) ;;
    *) FAILOVER_CLASS="refused"; FAILOVER_REASON="exit ${code} is not in the dispatch set"
       log "failover: skipped (${FAILOVER_REASON})"; return 1 ;;
  esac
  # One structural refusal that generalises the 10/11 rule to every AI exit code.
  # Before the push korean is still at the pre-rebase tip, so CI would compute the
  # same merge base and trip the same gate.
  if [[ "${AI_ACTIVE}" == true && "${PUSHED}" != true ]]; then
    FAILOVER_CLASS="refused"
    FAILOVER_REASON="AI stage stopped before the push -- CI has no AI stage and would trip the same gate"
    log "failover: skipped (${FAILOVER_REASON})"; return 1
  fi
  # R4. Exit 40 from the push site means the lease was broken, i.e. an unknown
  # writer holds korean. This is why the predicate cannot key on the code alone.
  if [[ "${code}" == 40 && "${DECISION}" == push ]]; then
    FAILOVER_CLASS="refused"; FAILOVER_REASON="lease conflict -- another writer holds korean"
    log "failover: skipped (${FAILOVER_REASON})"; return 1
  fi
  if failover_already_fired; then
    FAILOVER_CLASS="refused"; FAILOVER_REASON="already dispatched tonight"
    log "failover: skipped (${FAILOVER_REASON})"; return 1
  fi
  return 0
}

# The Discord `failover` field and the one-line EXTRA prefix. Deliberately
# apostrophe- and backtick-free: both strings end up inside the notify() heredoc,
# which bash scans for quote characters (see the WATCH OUT block there).
failover_render_field() {
  local kind="${1:-}"
  case "${FAILOVER_OUTCOME}" in
    dispatched)
      if [[ -n "${FAILOVER_RUN_URL}" ]]; then
        FAILOVER_FIELD="dispatched"$'\n'"${FAILOVER_RUN_URL}"
        FAILOVER_LINE="GHA failover dispatched: ${FAILOVER_RUN_URL}"
      else
        FAILOVER_FIELD="dispatched (${FAILOVER_REASON:-run url unavailable})"
        FAILOVER_LINE="GHA failover dispatched (run url unavailable)."
      fi ;;
    refused)
      FAILOVER_FIELD="refused: ${FAILOVER_REASON}"
      FAILOVER_LINE="GHA failover not dispatched: ${FAILOVER_REASON}." ;;
    error)
      FAILOVER_FIELD="error: ${FAILOVER_REASON} -- CI was NOT armed"
      FAILOVER_LINE="GHA failover FAILED to dispatch: ${FAILOVER_REASON}. CI was NOT armed." ;;
    off)
      # Design 5.7(b) specifies this field and it was never implemented. Rendered
      # on a FAILURE only: a green night must not carry a failover field at all,
      # which is what "renders only when non-empty" there means. No EXTRA line --
      # 5.7(c)'s prepend exists to keep a dispatched run URL out of the 960-char
      # tail truncation, and an `off` record has neither a URL nor an action.
      if [[ "${kind}" == fail ]]; then
        FAILOVER_FIELD="off (${FAILOVER_REASON})"
      else
        FAILOVER_FIELD=""
      fi
      FAILOVER_LINE="" ;;
    *)
      FAILOVER_FIELD=""
      FAILOVER_LINE="" ;;
  esac
}

# Never returns non-zero and never changes the exit code the run reports. The
# dispatch is delegated to sced-failover.sh, which owns the gh probes, the
# orphan-draft precondition and the call itself; this function owns only the
# predicate and the reporting.
failover_dispatch() {
  local code="$1" kind="$2" rc=0 out=""
  # In-memory reentrancy guard, mirroring notify()'s NOTIFIED.
  [[ "${FAILOVER_ATTEMPTED}" == true ]] && return 0
  FAILOVER_ATTEMPTED=true
  FAILOVER_OUTCOME="off"; FAILOVER_SIG="fo-off"; FAILOVER_CLASS="off"

  # The refusing gate classified itself; nothing else is consulted. `kind` is NOT
  # re-tested here: gate 4 is the only gate a non-failing run can reach and it
  # declares class=off itself, so the old `kind == fail` test was doing this job
  # by proxy -- and getting the three config gates wrong (verify F11).
  if ! failover_should_run "${code}" "${kind}"; then
    if [[ "${FAILOVER_CLASS}" == refused ]]; then
      FAILOVER_OUTCOME="refused"; FAILOVER_SIG="fo-no"
    fi
    failover_render_field "${kind}"
    return 0
  fi

  # Interpreter pinned for the same reason as the gate and the AI stage: a
  # `#!/usr/bin/env bash` shebang makes the kernel exec /usr/bin/env first, and
  # that image swap is what macOS System Policy denied on 2026-08-01.
  set +e
  out="$("${BASH:-/bin/bash}" "${SCRIPT_DIR}/sced-failover.sh" --dispatch \
          --repo "${REPO}" --tag "${TAG}" --release-id "${RELEASE_ID}" \
          --reason "exit ${code} ${DECISION}" --timeout "${FAILOVER_TIMEOUT}" 2>&1)"
  rc=$?
  set -e
  printf '%s\n' "${out}" | sed 's/^/failover: /'

  FAILOVER_RUN_URL="$(printf '%s\n' "${out}" | sed -n 's/^run_url: *//p' | head -1)"
  FAILOVER_RUN_ID="$(printf '%s\n' "${out}"  | sed -n 's/^run_id: *//p'  | head -1)"
  FAILOVER_REASON="$(printf '%s\n' "${out}"  | sed -n 's/^reason: *//p'  | head -1)"

  case "${rc}" in
    0) FAILOVER_OUTCOME="dispatched"; FAILOVER_SIG="fo-ok" ;;
    3|5) FAILOVER_OUTCOME="refused";  FAILOVER_SIG="fo-no"
         : "${FAILOVER_REASON:=refused by sced-failover.sh (rc ${rc})}" ;;
    *) FAILOVER_OUTCOME="error";      FAILOVER_SIG="fo-err"
       : "${FAILOVER_REASON:=sced-failover.sh exited ${rc}}" ;;
  esac
  failover_render_field "${kind}"
  return 0
}

# The Discord `detail` for an AI stop. Reads decide.json field by field -- never
# sources it -- and points at the artifact directory, because on an ai-stop the
# agent has already ruled on everything and its rulings are readable.
#
# Every log() in here MUST carry `>&2`. This function's stdout IS its return value
# (`EXTRA="$(ai_stop_extra)"`), so an ordinary log line would be captured into the
# Discord detail field instead of the run log. stderr is safe: the script-level
# `exec … 2>&1` at the tee sends it to the log file, and a command substitution
# captures only stdout.
ai_stop_extra() {
  local reason="" rules="" rc=0
  if [[ -f "${AI_RUN_DIR}/decide.json" ]]; then
    reason="$(with_timeout "${PY_TIMEOUT}" python3 -c 'import json,sys
d = json.load(open(sys.argv[1]))
print(d.get("reason") or d.get("outcome") or "")' "${AI_RUN_DIR}/decide.json" 2>/dev/null)" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
      reason=""
      log "ai: could not read the reason from decide.json (rc ${rc})" >&2
    fi
    # No f-string here on purpose: this whole program is inside a single-quoted
    # -c argument, so an escaped double quote reaches python as a backslash and
    # `f"{r[\"rule\"]}"` is a SyntaxError -- one that fails silently through the
    # `|| echo ''` and empties the field this notification exists to carry.
    rc=0
    rules="$(with_timeout "${PY_TIMEOUT}" python3 -c 'import json,sys
d = json.load(open(sys.argv[1]))
for r in d.get("rules", []):
    if r.get("status") == "fail":
        for line in (r.get("detail") or [])[:4]:
            print("  rule %s %s: %s" % (r.get("rule"), r.get("name"), line))' "${AI_RUN_DIR}/decide.json" 2>/dev/null)" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
      rules=""
      log "ai: could not read the failing rules from decide.json (rc ${rc})" >&2
    fi
  fi
  if [[ -z "${reason}" && -f "${AI_RUN_DIR}/stage-error.txt" ]]; then
    reason="$(head -3 "${AI_RUN_DIR}/stage-error.txt")"
  fi
  printf '```\n%s\n%s\n```\nArtifacts: `%s`\nRead `decide.json`, `verify.log`, `seed-diff.patch` and `manifest.json` there: the agent has already ruled on every overlap path and its rulings are readable.' \
    "${reason:-no reason recorded}" "${rules}" "${AI_RUN_DIR#"${WORKSPACE}"/}"
}

# --------------------------------------------------------------- notification

# Colours: released green, noop/stale grey, stop orange (a person is needed, not a
# breakage), ai-stop violet, fail red.
#
# ai-stop gets its own colour rather than reusing orange because the two mean
# different things to whoever reads the channel: `stop` means nobody has looked at
# the overlap yet, `ai-stop` means the agent looked at all of it, resolved it, and
# the rules refused to ship the result -- so there is a manifest, a verify log and
# a seed diff already waiting to be read.
notify_color() {
  case "$1" in
    released)        echo 3066993 ;;
    noop|stale-skip) echo 9807270 ;;
    stop)            echo 15105570 ;;
    ai-stop)         echo 10181046 ;;
    *)               echo 15158332 ;;
  esac
}

# Throttle state, one value per line: kind, signature, first epoch, last sent
# epoch, streak. A CHANGE in signature always breaks through, ahead of any
# repetition limit -- the failure mode being defended against is a real change
# hiding behind a quiet streak, not the streak itself.
should_notify() {
  local kind="$1" sig="$2" now="$3"
  local p_kind="" p_sig="" p_first=0 p_last=0 p_streak=0
  if [[ -f "${SIGFILE}" ]]; then
    { read -r p_kind; read -r p_sig; read -r p_first; read -r p_last; read -r p_streak; } < "${SIGFILE}" 2>/dev/null || true
  fi
  : "${p_first:=0}" "${p_last:=0}" "${p_streak:=0}"

  local send=0 streak=1 first="${now}"
  if [[ "${kind}" == "released" ]]; then
    send=1
  elif [[ "${kind}" != "${p_kind}" || "${sig}" != "${p_sig}" ]]; then
    send=1
  else
    streak=$((p_streak + 1))
    first="${p_first}"
    if [[ "${kind}" == "noop" ]]; then
      send=0
    elif [[ "${streak}" -le 3 ]]; then
      send=1
    elif [[ "${streak}" -ge "${SCED_SYNC_STALL_ESCALATE:-7}" ]]; then
      # Escalation. Decaying to weekly after three nights is exactly how a stall
      # becomes invisible: 2026-08-06 the SCED overlap stall reached streak 4 and
      # the next scheduled Discord message was six nights away. A stall that has
      # survived this many runs is MORE worth saying, not less, so it breaks
      # through regardless of when the last send was.
      send=1
    elif [[ $((now - p_last)) -ge 604800 ]]; then
      send=1
    fi
  fi

  local last="${p_last}"
  [[ "${send}" -eq 1 ]] && last="${now}"
  printf '%s\n%s\n%s\n%s\n%s\n' "${kind}" "${sig}" "${first}" "${last}" "${streak}" > "${SIGFILE}.tmp"
  mv "${SIGFILE}.tmp" "${SIGFILE}"
  return $((1 - send))
}

# Read-only companion to should_notify(): what streak WOULD this run record?
# should_notify() is called after the payload is built (so that --no-notify can
# print a payload without mutating throttle state), so the streak has to be
# peeked rather than returned. Never writes SIGFILE.
peek_streak() {
  local kind="$1" sig="$2"
  local p_kind="" p_sig="" p_first=0 p_last=0 p_streak=0
  if [[ -f "${SIGFILE}" ]]; then
    { read -r p_kind; read -r p_sig; read -r p_first; read -r p_last; read -r p_streak; } < "${SIGFILE}" 2>/dev/null || true
  fi
  : "${p_streak:=0}"
  if [[ "${kind}" == "${p_kind}" && "${sig}" == "${p_sig}" ]]; then
    echo $((p_streak + 1))
  else
    echo 1
  fi
}

notify() {
  local kind="$1" title="$2" code="$3"
  [[ "${NOTIFIED}" == true ]] && return 0
  NOTIFIED=true

  local now sig
  now="$(date +%s)"
  case "${kind}" in
    # Keyed on the overlap SET's digest, not its COUNT. A stall that swaps one
    # path for another holds the count constant, so a count-keyed signature
    # reported "unchanged" and the change was throttled into invisibility.
    stop) sig="overlap:${MERGE_BASE}:${OVERLAP_SHA256:0:12}" ;;
    # Keyed on the INPUT, not the outcome. A sticky stall produces a stable
    # signature, which is the assumption the whole throttle rests on; keying an
    # ai-stop on which rule failed would make a night that stops for a different
    # reason on the same unchanged overlap look like news, and a night that stops
    # for the same reason on a CHANGED overlap look like more of the same. The
    # second of those is the one that matters.
    ai-stop) sig="ai:${MERGE_BASE}:${OVERLAP_SHA256:0:12}" ;;
    # DECISION is in the signature because one code can be reached from several
    # places (20 = no upstream remote / fetch origin / fetch upstream / worktree
    # add). Without it, a change of failure MODE at a constant code is invisible
    # to should_notify() and gets throttled as "unchanged".
    # FAILOVER_SIG is a FOUR-VALUE token (fo-off/fo-no/fo-ok/fo-err), never the run
    # URL and never the refusal text. Cardinality is the whole point: a signature
    # that changed every night would break should_notify() open permanently and
    # destroy the throttle. Four tokens distinguishes exactly the case that matters
    # -- two identical exit-60 nights where the first armed CI and the second could
    # not reach gh -- while a sticky stall still produces a stable signature.
    fail) sig="fail:${code}:${DECISION}:${FAILOVER_SIG}" ;;
    *)    sig="${kind}:${code}" ;;
  esac

  local payload
  # `with_timeout ... env` and not a bare assignment prefix -- see write_state().
  # The heredoc below is why with_timeout carries `<&0`: without it python would be
  # handed an empty script here and this would post an EMPTY payload, silently.
  #
  # The bound covers the python, NOT the assignments' own substitutions: bash
  # evaluates $(notify_color ...) and $(peek_streak ...) before with_timeout is
  # ever entered, and peek_streak opens SIGFILE on the same volume. Accepted rather
  # than fixed -- it is a builtin read in a process that already holds the volume
  # grant, whereas the 2026-08-09 wedge was per-binary against the Homebrew
  # python3 -- but it is a real residual and belongs on the record here.
  payload="$(
    with_timeout "${PY_TIMEOUT}" env \
    KIND="${kind}" TITLE="${title}" CODE="${code}" COLOR="$(notify_color "${kind}")" \
    STREAK="$(peek_streak "${kind}" "${sig}")" \
    ESCALATE="${SCED_SYNC_STALL_ESCALATE:-7}" \
    REPO="${REPO}" OWNER="${OWNER}" MENTION="${SCED_SYNC_MENTION:-}" \
    DURATION="$(( $(date +%s) - STARTED_EPOCH ))" \
    FORK_SHA="${FORK_SHA}" KOREAN_SHA="${KOREAN_SHA}" UPSTREAM_SHA="${UPSTREAM_SHA}" \
    UPSTREAM_DATE="${UPSTREAM_DATE}" MERGE_BASE="${MERGE_BASE}" TAG="${TAG}" \
    RELEASE_URL="${RELEASE_URL}" ASSETS="${N_NEW}" BACKUP="${BACKUP}" \
    EXTRA="${EXTRA}" LOGF="${LOG_FILE#"${WORKSPACE}"/}" TS="$(date +%Y-%m-%dT%H:%M:%S%z)" \
    FAILOVER="${FAILOVER_FIELD}" \
    python3 <<'PY'
import json, os

e = os.environ.get
kind, code = e("KIND", ""), e("CODE", "")
mention = e("MENTION", "")
grade = {"released": "released", "noop": "no-op", "stale-skip": "skipped",
         "stop": "STOP", "ai-stop": "AI-STOP", "fail": "FAIL"}.get(kind, kind)
# The three kinds that mean "a person is needed". Kept as one tuple so a new kind
# cannot be added to the colour map and silently forgotten here.
NEEDS_A_PERSON = ("stop", "ai-stop", "fail")

# A stall that has survived ESCALATE runs says so in the title. Without this the
# only signal of a deepening stall was its absence from the channel.
streak, escalate = int(e("STREAK", "1") or 1), int(e("ESCALATE", "7") or 7)
title = e("TITLE", "")
if kind in NEEDS_A_PERSON and streak >= escalate:
    title = f"{title} — unchanged for {streak} runs"

head = f'[{e("REPO")}] {grade} · {title}'
content = (mention + " " + head).strip() if kind in NEEDS_A_PERSON and mention else head

def short(s):
    return f"`{s[:8]}`" if s else "-"

fields = [
    {"name": "repo", "value": f'{e("OWNER")}/{e("REPO")}', "inline": True},
    {"name": "exit", "value": str(code), "inline": True},
    {"name": "duration", "value": f'{e("DURATION")}s', "inline": True},
]
if e("FORK_SHA"):
    tip = short(e("KOREAN_SHA")) if e("KOREAN_SHA") else short(e("FORK_SHA")) + " (unchanged)"
    fields.append({"name": "korean tip", "value": tip, "inline": True})
if e("UPSTREAM_SHA"):
    fields.append({"name": "upstream tip",
                   "value": f'{short(e("UPSTREAM_SHA"))} {e("UPSTREAM_DATE")}'.strip(), "inline": True})
if e("MERGE_BASE"):
    fields.append({"name": "merge-base", "value": short(e("MERGE_BASE")), "inline": True})
if e("TAG"):
    fields.append({"name": "tag", "value": f'`{e("TAG")}`', "inline": True})
if e("ASSETS") and e("ASSETS") != "0":
    fields.append({"name": "assets", "value": e("ASSETS"), "inline": True})
if e("BACKUP"):
    fields.append({"name": "backup", "value": f'`{e("BACKUP")}`', "inline": True})
# Whether CI was armed. This is the field that lets the reader tell a night that
# recovered itself from one that is still waiting for a person.
if e("FAILOVER"):
    fields.append({"name": "failover", "value": e("FAILOVER")[:1000], "inline": False})
if e("EXTRA"):
    # Fence-aware truncation. Two ways the detail field can render as a broken code
    # block, and both land on the exit-21 gate text -- the one payload this
    # notification exists to carry faithfully: a value over the 1024-char Discord
    # field cap, cut before its closing fence; and an odd fence marker inside the
    # git output itself. The parity test fixes both, and the truncation line points
    # at the log, which always holds the full text.
    #
    # Truncating HERE and not in the producers is deliberate: git speaks Korean on
    # this box, so a byte-level head -c in bash could split a multibyte character
    # and leave os.environ holding a surrogate that json.dumps cannot encode. By
    # this point the value is a decoded str.
    #
    # WATCH OUT when editing anything below, comments included. This heredoc sits
    # inside a double-quoted command substitution, so bash scans the body for
    # backticks and quote characters even though the heredoc delimiter is quoted.
    # A literal fence, or an apostrophe in a word like doesn t, silently swallows
    # the following lines and breaks the whole script. Hence chr 96, and hence the
    # deliberately apostrophe-free prose in this block.
    FENCE = chr(96) * 3
    detail = e("EXTRA")
    if len(detail) > 960:
        detail = detail[:960].rstrip() + "\n… truncated; full text in the log."
    if detail.count(FENCE) % 2:
        detail += "\n" + FENCE
    fields.append({"name": "detail", "value": detail, "inline": False})

embed = {
    "title": title,
    "color": int(e("COLOR")),
    "timestamp": e("TS"),
    "fields": fields,
    "footer": {"text": f'log: {e("LOGF")}'},
}
if e("RELEASE_URL"):
    embed["url"] = e("RELEASE_URL")

print(json.dumps({"username": "SCED daily sync", "content": content, "embeds": [embed]}))
PY
  )" || {
    # `return 0` used to live here, and the bound above is what made that
    # unacceptable: notify() is the ONLY channel by which an unattended failure
    # reaches a human, so a wedge that trips the bound would have closed the very
    # channel that exists to report the wedge. Fall back to a payload the SHELL
    # builds -- no interpreter, no heredoc, so it cannot fail the same way.
    #
    # There is no json.dumps() here, so every interpolated value is first stripped
    # to an ASCII class that contains no `"`, no `\` and no newline. tr BEFORE cut,
    # so the length cap can never split a multi-byte character into invalid bytes;
    # `-` is last in the tr set so it is a literal and not a range.
    local nrc=$? safe_title safe_kind
    safe_title="$(printf '%s' "${title}" | LC_ALL=C tr -cd 'A-Za-z0-9 ._:+@()/-' | cut -c1-200)"
    safe_kind="$(printf '%s' "${kind}" | LC_ALL=C tr -cd 'A-Za-z0-9-' | cut -c1-32)"
    log "notify: payload build failed (rc ${nrc}) -- falling back to a minimal payload"
    payload="$(printf '{"username":"SCED daily sync","content":"[%s] %s %s (exit %s) — notify payload build failed rc %s, detail is in the run log"}' \
      "${safe_kind}" "${REPO}" "${safe_title}" "${code}" "${nrc}")"
  }

  if [[ "${NO_NOTIFY}" == true ]]; then
    log "notify (suppressed by --no-notify): ${payload}"
    return 0
  fi
  if ! should_notify "${kind}" "${sig}" "${now}"; then
    log "notify (throttled, signature unchanged): ${kind} ${sig}"
    return 0
  fi
  if [[ -z "${SCED_SYNC_DISCORD_WEBHOOK:-}" ]]; then
    log "notify: no webhook configured"
    return 0
  fi

  # The URL goes in on stdin, never in argv: `ps` is world-readable.
  printf 'url = "%s"\n' "${SCED_SYNC_DISCORD_WEBHOOK}" \
    | curl --config - -sS -X POST -H 'Content-Type: application/json' \
           --data-binary "${payload}" --max-time 20 --retry 2 --retry-delay 5 \
           >/dev/null || log "notify: webhook POST failed"
}

write_state() {
  local code="$1"
  mkdir -p "${STATE_ROOT}/state"
  # FO_ENABLED's two case patterns below carry a LEADING "(" and must keep it. The
  # shebang is /usr/bin/env bash, which on macOS is bash 3.2, whose $( ) parser
  # takes an unparenthesised pattern's own ")" as the closing paren of the
  # substitution. Without the leading "(" this printed a `syntax error near
  # unexpected token' on EVERY run and assigned FO_ENABLED the literal tail of the
  # case body -- so failover.enabled was ALWAYS false, whatever SCED_SYNC_FAILOVER
  # said. That voids the `enabled == false implies outcome == off` invariant and
  # the CLAUDE.md rollback row that sends a 03:00 operator to read .failover.enabled.
  #
  # `env` carries the assignments because with_timeout runs "$@", which cannot take
  # a bash assignment prefix. THIS is the call that hung for 6h15m on 2026-08-09 --
  # the state file's 08:32 mtime against its 02:17 content pins it here, not to
  # notify() -- so the bound is the point of the whole change. Writing is atomic
  # (OUT + ".tmp" then os.replace below), so a killed python can never truncate the
  # real file; the worst case is a stray .tmp, which cleanup() sweeps.
  with_timeout "${PY_TIMEOUT}" env \
  REPO="${REPO}" DECISION="${DECISION}" CODE="${code}" \
  STARTED_AT="${STARTED_AT}" FINISHED_AT="$(date +%Y-%m-%dT%H:%M:%S%z)" \
  DURATION="$(( $(date +%s) - STARTED_EPOCH ))" \
  FORK_SHA="${FORK_SHA}" KOREAN_SHA="${KOREAN_SHA}" UPSTREAM_SHA="${UPSTREAM_SHA}" \
  MERGE_BASE="${MERGE_BASE}" OVERLAP="${OVERLAP_COUNT}" BACKUP="${BACKUP}" \
  TAG="${TAG}" PREV_TAG="${PREV_TAG}" ASSETS="${N_NEW}" PREV_ASSETS="${N_PREV}" \
  RELEASE_URL="${RELEASE_URL}" PROVENANCE="${PROVENANCE}" LOGF="${LOG_FILE}" \
  AI_ACTIVE="${AI_ACTIVE}" AI_OUTCOME="${AI_OUTCOME}" AI_REASON="${AI_REASON}" \
  AI_BIN="${AI_BIN}" AI_RUN_DIR="${AI_RUN_DIR}" AI_RESULT="${AI_RESULT_JSON}" \
  FO_OUTCOME="${FAILOVER_OUTCOME}" FO_REASON="${FAILOVER_REASON}" \
  FO_RUN_ID="${FAILOVER_RUN_ID}" FO_RUN_URL="${FAILOVER_RUN_URL}" \
  FO_PUSHED="${PUSHED}" FO_DATE="$(date -u +%Y%m%d)" \
  FO_ENABLED="$(case ",${SCED_SYNC_FAILOVER:-}," in (*",${REPO},"*) echo true ;; (*) echo false ;; esac)" \
  OUT="${STATEFILE}" \
  python3 <<'PY' || log "state: write failed (rc $?)"
import json, os
e = os.environ.get
def num(k):
    try:    return int(e(k, "0") or 0)
    except ValueError: return 0
doc = {
    # Schema 3 is ADDITIVE ONLY. Every schema-1 and schema-2 top-level key keeps
    # its name, its position and its type, because the offline asset-count baseline
    # below reads this file back and any external reader must keep working. All new
    # material is nested under "ai" and "failover".
    "schema": 3,
    "repo": e("REPO"),
    "started_at": e("STARTED_AT"),
    "finished_at": e("FINISHED_AT"),
    "duration_s": num("DURATION"),
    "decision": e("DECISION"),
    "exit_code": num("CODE"),
    "fork_sha_before": e("FORK_SHA"),
    "korean_sha_after": e("KOREAN_SHA"),
    "upstream_sha": e("UPSTREAM_SHA"),
    "merge_base": e("MERGE_BASE"),
    "overlap_count": num("OVERLAP"),
    "backup_branch": e("BACKUP"),
    "tag": e("TAG"),
    "prev_tag": e("PREV_TAG"),
    "asset_count": num("ASSETS"),
    "prev_asset_count": num("PREV_ASSETS"),
    "release_url": e("RELEASE_URL"),
    "build_provenance": e("PROVENANCE"),
    "log": e("LOGF"),
}

# The stage writes the full `ai` object (design §3.3) as result.json once it has
# verified the tree, so on a successful night this is a straight embed rather than
# a second, drifting transcription. On every other path -- skipped, stopped,
# errored -- result.json does not exist and the minimum is recorded instead.
ai = {
    "enabled": e("AI_ACTIVE") == "true",
    "mode": "drive",
    "outcome": e("AI_OUTCOME") or "skipped",
    "reason": e("AI_REASON") or "",
    "binary": e("AI_BIN") or "",
    "artifact_dir": e("AI_RUN_DIR") or "",
}
result_path = e("AI_RESULT") or ""
if result_path and os.path.isfile(result_path):
    try:
        with open(result_path) as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            loaded.update({k: v for k, v in ai.items()
                           if k in ("enabled", "outcome", "reason") and v not in (None, "")})
            ai = loaded
    except (OSError, ValueError):
        ai["reason"] = (ai["reason"] + "; result.json unreadable").strip("; ")
doc["ai"] = ai

# The failover record. `date` is the UTC night key and is what makes the "already
# fired tonight" test self-expiring: tomorrow reads a date that is not today's and
# treats the record as absent, so no cleanup step exists or is needed. The three
# predicate INPUTS are recorded alongside the outcome so a disputed refusal or
# dispatch is auditable after the fact.
fo = {
    "enabled": e("FO_ENABLED") == "true",
    "tier": "driver",
    "date": e("FO_DATE", ""),
    "outcome": e("FO_OUTCOME") or "off",
    "reason": e("FO_REASON") or "",
    "exit_code": num("CODE"),
    "decision": e("DECISION"),
    "pushed": e("FO_PUSHED") == "true",
    "run_id": e("FO_RUN_ID") or "",
    "run_url": e("FO_RUN_URL") or "",
}

# THE ONE FIELD IN THIS DOCUMENT THAT DOES NOT BELONG TO THIS RUN (verify F3).
# Everything above and below is a from-scratch projection of this run, and that is
# deliberate -- it is what lets this function repair a corrupted state file. The
# (date, outcome) pair is different: it is read back by a LATER process, three of
# them -- failover_already_fired() above, sced-failover.sh's gate 5, and the AI
# push-window guard, which is R14 mitigation (3). Rebuilding it from this run
# erased a live `dispatched` record on the second run of a night, so a third run --
# or the AI stage -- saw no live dispatch and could push into the CI run's window.
# Read the prior value and keep it.
#
# ONLY a `dispatched` record for TONIGHT is protected, and only from a NON-dispatch.
# `error` stays overwritable on purpose: design 3.1 makes an errored dispatch a
# legitimate retry. A later successful dispatch wins, since its URL is the useful
# one. Do NOT generalise this read to other keys -- a partial merge would resurrect
# yesterday's release_url on a run that failed before the release.
prev_fo = {}
try:
    with open(e("OUT")) as fh:
        loaded = json.load(fh)
    if isinstance(loaded, dict) and isinstance(loaded.get("failover"), dict):
        prev_fo = loaded["failover"]
except (OSError, ValueError):
    prev_fo = {}

if (fo["date"]
        and prev_fo.get("date") == fo["date"]
        and prev_fo.get("outcome") == "dispatched"
        and fo["outcome"] != "dispatched"):
    prev_fo = dict(prev_fo)
    prev_fo["superseded_by"] = (
        fo["outcome"] + " (exit " + str(fo["exit_code"]) + " " + fo["decision"] + "): "
        + (fo["reason"] or "no reason recorded"))[:200]
    fo = prev_fo

doc["failover"] = fo

out = e("OUT")
with open(out + ".tmp", "w") as fh:
    json.dump(doc, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(out + ".tmp", out)
PY
}

# Sole exit path once the lock is held. Notification happens HERE, never in the
# EXIT trap: a hung POST inside a trap would hold the lock forever.
finish() {
  local code="$1" kind="$2" title="$3"
  log "finish: decision=${DECISION} kind=${kind} exit=${code} -- ${title}"
  # BEFORE write_state and notify, because both report its outcome. Never in the
  # trap, for the same reason notify() is not: cleanup() is the only thing that
  # releases the lock and it runs only on exit.
  #
  # COST. FAILOVER_TIMEOUT bounds ONE call inside sced-failover.sh -- the
  # `gh workflow run` in do_dispatch() -- and nothing else. Its probes carry their
  # own fixed bounds, so the serial worst case of this one line is
  #     auth 20 + draft query 30 + draft DELETE 30 + re-query 30 + dispatch 60
  #   = 170 s, or 195 s if every one of them has to be SIGKILLed after
  # with_timeout's 5 s grace. The cheapest path -- no TAG yet, so no draft probe
  # at all -- is still 20 + 60 = 80 s, which already exceeds the notify POST's own
  # worst case of 70 s (--max-time 20 --retry 2 --retry-delay 5). So this IS the
  # longest pole in finish(), and every second of it is spent holding the
  # workspace lock.
  # Accepted, not fixed: 195 s against the 1800 s stagger between the two repos'
  # agents, finish() is terminal so it can never eat the AI stage's reserve, and
  # the measured dispatch is 11 s end to end. If you add or retime a network call
  # in sced-failover.sh, this arithmetic moves with it -- and so do the COST block
  # in that file's header and design.feature-v1.md 5.4.
  failover_dispatch "${code}" "${kind}" || true
  # PREPENDED, not appended: the detail field truncates its TAIL at 960 chars, so
  # an appended line would be the first thing lost on exactly the failures with the
  # longest detail (exit 21 git text, exit 62 collision lists).
  if [[ -n "${FAILOVER_LINE}" ]]; then
    if [[ -n "${EXTRA}" ]]; then EXTRA="${FAILOVER_LINE}"$'\n'"${EXTRA}"; else EXTRA="${FAILOVER_LINE}"; fi
  fi
  write_state "${code}"
  notify "${kind}" "${title}" "${code}"
  exit "${code}"
}

cleanup() {
  # write_state()'s python writes OUT + ".tmp" and os.replace()s it, so the real
  # state file is never truncated -- but a python killed by the PY_TIMEOUT bound
  # leaves the .tmp behind. Sweep it: the next run reads the state file back for
  # the offline asset-count baseline and for the failover "already fired tonight"
  # record, and a stray sibling is the kind of debris that outlives its cause.
  rm -f "${STATEFILE}.tmp"
  if [[ "${KEEP_SCRATCH}" != true && -n "${WT}" && -e "${WT}" ]]; then
    g "${REPO_PATH}" worktree remove --force "${WT}" 2>/dev/null \
      || log "cleanup: could not remove worktree ${WT}"
  fi
  if [[ -n "${LOG_FILE}" && -d "${STATE_ROOT}/logs" ]]; then
    # shellcheck disable=SC2012
    ls -1t "${STATE_ROOT}/logs/${REPO}-"*.log 2>/dev/null \
      | tail -n +"$((KEEP_LOGS + 1))" | while read -r old; do rm -f "${old}"; done
  fi
  # Same idiom as the log pruner above, one directory deeper. Names are timestamps
  # written by this script, and the `-n` guard means a blank line from an empty
  # listing can never expand into the parent directory.
  if [[ -d "${STATE_ROOT}/ai/${REPO}" ]]; then
    # shellcheck disable=SC2012
    ls -1t "${STATE_ROOT}/ai/${REPO}" 2>/dev/null \
      | tail -n +"$((KEEP_AI_RUNS + 1))" \
      | while read -r old; do
          [[ -n "${old}" && -d "${STATE_ROOT}/ai/${REPO}/${old}" ]] \
            && rm -rf "${STATE_ROOT:?}/ai/${REPO}/${old}"
        done
  fi
  if [[ "${LOCK_HELD}" == true ]]; then
    rm -f "${LOCK}/owner"
    rmdir "${LOCK}" 2>/dev/null || true
  fi
}

# ------------------------------------------------------------------- preflight

# Redirecting git's stderr away used to collapse at least eight distinct causes
# into "is not a git repository" -- among them the macOS System Policy denial of
# 2026-08-01 (rc 128, "Operation not permitted"), which cost a multi-hour
# diagnosis. Keep both the exit code and the message. `|| rc=$?` is required:
# under `set -e` the bare assignment would abort, and `if ! cmd` would leave $?
# holding the INVERTED status, not git's.
PREFLIGHT_RC=0
PREFLIGHT_ERR="$(g "${REPO_PATH}" rev-parse --git-dir 2>&1)" || PREFLIGHT_RC=$?
if [[ "${PREFLIGHT_RC}" -ne 0 ]]; then
  printf "ERROR: git could not read '%s' (rc=%d): %s\n" \
    "${REPO_PATH}" "${PREFLIGHT_RC}" "${PREFLIGHT_ERR}" >&2
  exit 2
fi

if [[ "${NO_NOTIFY}" != true && -z "${SCED_SYNC_DISCORD_WEBHOOK:-}" ]]; then
  echo "ERROR: SCED_SYNC_DISCORD_WEBHOOK is not set in ${ENV_FILE}" >&2
  exit 2
fi

mkdir -p "${STATE_ROOT}/run" "${STATE_ROOT}/state" "${STATE_ROOT}/logs" "${STATE_ROOT}/scratch"

# mktemp would otherwise land in /var/folders on the boot volume, which has far
# less headroom than the external volume everything else uses.
export TMPDIR="${STATE_ROOT}/run"

AVAIL_KB="$(df -k "${WORKSPACE}" | awk 'NR==2 {print $4}')"
if [[ "${AVAIL_KB}" -lt 5242880 ]]; then
  echo "ERROR: less than 5 GiB free on ${WORKSPACE} (${AVAIL_KB} KiB)" >&2
  exit 2
fi

# mkdir is atomic; flock(1) does not exist here. The lock is workspace-wide, not
# per-repo: its purpose is to stop the 02:17 run from overlapping the 02:47 one.
if ! mkdir "${LOCK}" 2>/dev/null; then
  stale=false
  if [[ -f "${LOCK}/owner" ]]; then
    # Field-by-field extraction rather than `eval`: the owner file is ours, but a
    # shell-evaluated state file is an injection primitive waiting for the day it
    # is not.
    LOCK_pid="$(sed -n 's/.*[^a-z]pid=\([0-9]*\).*/\1/p;s/^pid=\([0-9]*\).*/\1/p' "${LOCK}/owner" | head -1)"
    LOCK_repo="$(sed -n 's/.*repo=\([A-Za-z][A-Za-z-]*\).*/\1/p' "${LOCK}/owner" | head -1)"
    LOCK_started="$(sed -n 's/.*started=\([0-9]*\).*/\1/p' "${LOCK}/owner" | head -1)"
    if [[ -n "${LOCK_pid:-}" ]] && ! kill -0 "${LOCK_pid}" 2>/dev/null; then
      stale=true
    elif [[ -n "${LOCK_started:-}" ]] && [[ $(( $(date +%s) - LOCK_started )) -gt "${MAX_RUN_SECONDS}" ]]; then
      stale=true
    fi
  else
    stale=true
  fi
  if [[ "${stale}" == true ]]; then
    echo "WARNING: taking over a stale lock held by pid=${LOCK_pid:-?} repo=${LOCK_repo:-?}" >&2
    rm -f "${LOCK}/owner"
  else
    echo "another run holds the lock (pid=${LOCK_pid:-?} repo=${LOCK_repo:-?}); exiting" >&2
    exit 3
  fi
fi
LOCK_HELD=true
trap cleanup EXIT
printf 'pid=%s repo=%s started=%s host=%s\n' "$$" "${REPO}" "${STARTED_EPOCH}" "$(hostname)" > "${LOCK}/owner"

LOG_FILE="${STATE_ROOT}/logs/${REPO}-${RUN_STAMP}.log"
exec > >(tee -a "${LOG_FILE}") 2>&1

log "=== daily-sync-local ${REPO} (dry_run=${DRY_RUN} skip_build=${SKIP_BUILD} force=${FORCE}) ==="

# An unparseable PY_TIMEOUT is not a wider bound, it is a COLLAPSED one, and it
# fails in the one direction these bounds exist to close. with_timeout's watchdog
# runs `sleep "${secs}"`; sleep rejects a non-integer instantly, so the SIGTERM
# lands within milliseconds of the python3 starting. Measured 2026-08-09 with
# SCED_SYNC_PY_TIMEOUT=abc: write_state() AND notify() both fail rc 143, so the
# night keeps the PREVIOUS run's state file and sends no Discord at all -- silence,
# and the failover watchdog then reads a stale finished_at. Falling back keeps the
# night correct; the log line is the only way the operator learns the value in the
# env file was ignored. Plain integer seconds only: `30s` happens to work with this
# machine's sleep and `5m` does not, and depending on which is undocumented luck.
#
# Validated here rather than at the assignment because log() needs the tee at
# :1203; nothing reads PY_TIMEOUT before this point (every consumer is reached via
# finish() or the verify stage). NET_TIMEOUT, BUILD_TIMEOUT and FAILOVER_TIMEOUT
# share the shape but NOT the consequence -- a collapsed bound there kills the
# fetch, the build or the dispatch, which reports loudly through a notify path
# whose own knob is still valid. Deliberately left; see verify.md M2.
case "${PY_TIMEOUT}" in
  ''|*[!0-9]*)
    log "config: SCED_SYNC_PY_TIMEOUT='${PY_TIMEOUT}' is not a positive integer of seconds -- using 30"
    PY_TIMEOUT=30 ;;
  *)
    if [[ "${PY_TIMEOUT}" -lt 1 ]]; then
      log "config: SCED_SYNC_PY_TIMEOUT=${PY_TIMEOUT} must be at least 1 second -- using 30"
      PY_TIMEOUT=30
    fi ;;
esac

# ---------------------------------------------------------- schedule interlock

# Under launchd the plist is the single source of the schedule: it carries the
# StartCalendarInterval trigger and the two guard bounds in the same file,
# rendered together by SCED-tools/scripts/sced-schedule.sh. If the trigger fired
# but the bounds did not arrive, that file was hand-edited and the two have
# diverged -- historically a silent grey `stale-skip` at exit 4 that the notify
# throttle then suppressed. Fail loudly instead.
#
# The test is XPC_SERVICE_NAME == the agent's own label, NOT merely "set":
# measured on this box, an ordinary login shell already carries
# XPC_SERVICE_NAME=0, so a non-empty check would trip on every manual run. Only a
# launchd job gets its label as the value.
SCHED_ENV_SEEN="${SCED_SYNC_SCHED_SCED:-}${SCED_SYNC_SCHED_SCED_DOWNLOADS:-}"
if [[ "${XPC_SERVICE_NAME:-}" == "com.shanash.sced-daily-sync.${REPO}" && -z "${SCHED_ENV_SEEN}" ]]; then
  DECISION="schedule-interlock"
  EXTRA="launchd (${XPC_SERVICE_NAME}) started this run but the plist carries no SCED_SYNC_SCHED_* bound; the trigger and the staleness guard have diverged. Run: SCED-tools/scripts/sced-schedule.sh verify"
  finish 5 fail "schedule interlock missing"
fi

# -------------------------------------------------------------- staleness guard

# launchd coalesces missed firings: a machine asleep at 02:17 runs the job on
# wake, possibly after the GHA slot. The guard asks only "may this still START",
# not "can it finish" -- a late run is better skipped than raced.
NOW_MIN=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
SCHED_MIN=$(( 10#${SCHED_HHMM:0:2} * 60 + 10#${SCHED_HHMM:2:2} ))
LATEST_MIN=$(( 10#${LATEST_HHMM:0:2} * 60 + 10#${LATEST_HHMM:2:2} ))
if [[ "${FORCE}" != true ]] && { [[ "${NOW_MIN}" -gt "${LATEST_MIN}" ]] || [[ "${NOW_MIN}" -lt $((SCHED_MIN - 5)) ]]; }; then
  DECISION="stale-skip"
  EXTRA="now $(date +%H:%M) is outside the window ${SCHED_HHMM}-${LATEST_HHMM}; the GHA fallback owns tonight."
  finish 4 "stale-skip" "outside the start window"
fi

# ------------------------------------------------------------------ worktree

case "${WT}" in
  "${STATE_ROOT}/scratch/"*) ;;
  *) log "refusing to touch a scratch path outside ${STATE_ROOT}/scratch"; DECISION="preflight"; finish 2 fail "scratch path assertion failed" ;;
esac
g "${REPO_PATH}" worktree prune
if [[ -e "${WT}" ]]; then
  g "${REPO_PATH}" worktree remove --force "${WT}" 2>/dev/null || rm -rf "${WT}"
fi

# --------------------------------------------------------------------- fetch

if ! g "${REPO_PATH}" remote get-url upstream >/dev/null 2>&1; then
  DECISION="fetch"; finish 20 fail "no 'upstream' remote in ${REPO_PATH}"
fi
if ! with_timeout "${NET_TIMEOUT}" g "${REPO_PATH}" fetch --prune --tags --force origin; then
  DECISION="fetch"; finish 20 fail "git fetch origin failed"
fi
if ! with_timeout "${NET_TIMEOUT}" g "${REPO_PATH}" fetch --tags upstream main; then
  DECISION="fetch"; finish 20 fail "git fetch upstream failed"
fi

# Assigned exactly once. This single value is the worktree base, the no-op
# guard's tip, the backup source and the --force-with-lease pin. Re-reading the
# remote later (as the GHA workflow does) degenerates the lease into a plain
# --force, so there is deliberately no `ls-remote` anywhere in this file.
FORK_SHA="$(g "${REPO_PATH}" rev-parse origin/korean)"
UPSTREAM_SHA="$(g "${REPO_PATH}" rev-parse upstream/main)"
UPSTREAM_DATE="$(g "${REPO_PATH}" log -1 --format=%cI upstream/main)"
log "fork tip     ${FORK_SHA}"
log "upstream tip ${UPSTREAM_SHA} (${UPSTREAM_DATE})"

if ! g "${REPO_PATH}" worktree add --detach "${WT}" "${FORK_SHA}" >/dev/null; then
  DECISION="fetch"; finish 20 fail "could not create the scratch worktree"
fi

# ------------------------------------------------------------------ no-op guard

BEHIND="$(g "${REPO_PATH}" rev-list --count "${FORK_SHA}..${UPSTREAM_SHA}")"
HAS_REL=0
if g "${REPO_PATH}" tag --points-at "${FORK_SHA}" | grep -q '+korean\.'; then HAS_REL=1; fi
NEEDS_REBASE=0;  [[ "${BEHIND}" -gt 0 ]]  && NEEDS_REBASE=1
NEEDS_RELEASE=0; [[ "${HAS_REL}" -eq 0 ]] && NEEDS_RELEASE=1
log "guard: behind=${BEHIND} has_release_tag=${HAS_REL}"

if [[ "${NEEDS_REBASE}" -eq 0 && "${NEEDS_RELEASE}" -eq 0 && "${FORCE}" != true ]]; then
  DECISION="noop"
  EXTRA="already on upstream tip and the tip already carries a +korean tag."
  finish 0 "noop" "nothing to do"
fi

# ----------------------------------------------------------------- overlap gate

# The gate predicate lives in one place. This script calls it; it never
# reimplements it.
set +e
# The interpreter is named EXPLICITLY, not left to the shebang.
#
# `check-upstream-overlap.sh` starts with `#!/usr/bin/env bash`, so executing it
# directly makes the kernel exec /usr/bin/env (com.apple.env, a *different*
# platform binary) before it ever reaches /bin/bash. That image swap is the ONLY
# structural difference between this git process and every other git process in
# the run -- the plist runs `/bin/bash <wrapper>`, the wrapper runs
# `exec /bin/bash <driver>` -- and it is exactly the process macOS System Policy
# denied file-read-data on /Volumes/PRO-G40 on 2026-08-01 at 02:17:20.160 and
# 02:47:10.572 (see .am/daily-sync-rebase-failure/). Naming ${BASH} keeps the
# whole pipeline on one image and out of TCC's responsible-process reattribution.
# A manual run is unaffected: ${BASH} is whatever bash is already running us.
"${BASH:-/bin/bash}" "${SCRIPT_DIR}/check-upstream-overlap.sh" --repo "${REPO}" \
  --fork-ref "${FORK_SHA}" --upstream-ref "${UPSTREAM_SHA}" --limit 200 \
  --paths-out "${TMPDIR}/overlap-${REPO}.txt" \
  --fork-paths-out "${TMPDIR}/fork-changed-${REPO}.txt" \
  --upstream-paths-out "${TMPDIR}/upstream-changed-${REPO}.txt" \
  > "${TMPDIR}/gate-${REPO}.txt" 2>&1
GATE_RC=$?
set -e
cat "${TMPDIR}/gate-${REPO}.txt"
MERGE_BASE="$(sed -n 's/^Merge base: *//p' "${TMPDIR}/gate-${REPO}.txt" | head -1)"
OVERLAP_COUNT="$(sed -n 's/^Overlap: *//p' "${TMPDIR}/gate-${REPO}.txt" | head -1)"
: "${OVERLAP_COUNT:=0}"
# The gate guarantees --paths-out on exit 0 and 10 and writes nothing on 1/2, so
# an absent file here means the gate errored and the digest stays empty.
if [[ -f "${TMPDIR}/overlap-${REPO}.txt" ]]; then
  OVERLAP_SHA256="$(shasum -a 256 < "${TMPDIR}/overlap-${REPO}.txt" | cut -d' ' -f1)"
fi

case "${GATE_RC}" in
  0) DECISION="proceed"; AI_REASON="gate clean -- nothing to resolve" ;;
  10)
    # The gate is still NOT bypassable: --force never reaches here, the predicate
    # still ran, and it still returned 10. What changes is only what a TRIPPED gate
    # hands off to. With the stage disabled -- which is the default, and the only
    # state until SCED_SYNC_AI_RESOLVE names a repo -- this branch is byte-identical
    # to what it has always done.
    if ai_should_run; then
      AI_ACTIVE=true
      AI_OUTCOME="proceed"
      AI_RUN_DIR="${STATE_ROOT}/ai/${REPO}/${RUN_STAMP}"
      mkdir -p "${AI_RUN_DIR}"
      DECISION="proceed-ai"
      log "ai: enabled for ${REPO} -- ${OVERLAP_COUNT} overlap path(s) will be adjudicated"
      log "ai: binary ${AI_BIN}, artifacts ${AI_RUN_DIR}"
    else
      DECISION="stop-overlap"
      EXTRA="$(printf '```\n%s\n```\nNext: `SCED-tools/scripts/check-upstream-overlap.sh --repo %s`\nThe GHA fallback fires at %s KST and stops at the same gate.' \
               "$(sed -n '/^Overlapping paths/,$p' "${TMPDIR}/gate-${REPO}.txt" | head -20)" "${REPO}" \
               "$([[ "${REPO}" == SCED ]] && echo 03:47 || echo 03:17)")"
      finish 10 stop "overlap gate tripped (${OVERLAP_COUNT} files)"
    fi
    ;;
  *)
    # 21, not 20: a gate-script ERROR is not a fetch failure, and sharing 20
    # with fetch also made them share the `fail:${code}` throttle bucket -- a
    # real fetch failure the night after a gate error would have been silently
    # throttled as "signature unchanged".
    DECISION="gate-error"
    EXTRA="$(printf '```\n%s\n```\nThe gate could not run. This is NOT an overlap trip; `korean` is untouched.' \
             "$(tail -20 "${TMPDIR}/gate-${REPO}.txt")")"
    finish 21 fail "overlap gate script failed (rc=${GATE_RC})"
    ;;
esac

# --------------------------------------------------------------------- backup

BACKUP="auto/korean-backup-local-$(date -u +%Y%m%d)"
if [[ "${DRY_RUN}" == true ]]; then
  log "dry-run: would push ${FORK_SHA} to ${BACKUP}"
else
  # Non-force on purpose: if the name already points somewhere else the push is
  # refused and a timestamped second branch is made. An existing backup is never
  # overwritten.
  if ! with_timeout "${NET_TIMEOUT}" g "${WT}" push origin "${FORK_SHA}:refs/heads/${BACKUP}"; then
    BACKUP="${BACKUP}-$(date -u +%H%M%SZ)"
    if ! with_timeout "${NET_TIMEOUT}" g "${WT}" push origin "${FORK_SHA}:refs/heads/${BACKUP}"; then
      DECISION="backup"; finish 40 fail "could not push the backup branch"
    fi
  fi
  log "backup pushed: ${BACKUP}"
fi

# --------------------------------------------------------------------- rebase

# Two explicit call sites, never one flag-array invocation: /bin/bash here is
# 3.2.57 and expanding an empty array under `set -u` is an error there.
#
# The AI branch performs NO rebase of its own. It hands the tripped gate to the
# stage, which owns the shadow seed, the agent, the ten rules, the nine checks and
# the attestation commit. This script's structural claim survives intact: there is
# still no `git add`, no `git commit`, no `--strategy`, no `rebase --continue` and
# no `rerere` in it.
if [[ "${AI_ACTIVE}" == true ]]; then
  set +e
  # ${BASH} for the same reason as the gate call above: keeping the whole pipeline
  # on one image keeps it out of TCC's responsible-process reattribution.
  "${BASH:-/bin/bash}" "${SCRIPT_DIR}/resolve-rebase-with-ai.sh" \
    --repo "${REPO}" --worktree "${WT}" \
    --merge-base "${MERGE_BASE}" --fork-ref "${FORK_SHA}" --upstream-ref "${UPSTREAM_SHA}" \
    --overlap "${TMPDIR}/overlap-${REPO}.txt" \
    --fork-paths "${TMPDIR}/fork-changed-${REPO}.txt" \
    --upstream-paths "${TMPDIR}/upstream-changed-${REPO}.txt" \
    --run-dir "${AI_RUN_DIR}" --claude-bin "${AI_BIN}"
  AI_RC=$?
  set -e
  AI_RESULT_JSON="${AI_RUN_DIR}/result.json"
  case "${AI_RC}" in
    0)  log "ai: applied" ;;
    11) DECISION="ai-stop"; AI_OUTCOME="stop"
        AI_REASON="stopped by policy"
        EXTRA="$(ai_stop_extra)"
        finish 11 ai-stop "AI resolution stopped by policy -- korean untouched" ;;
    62) DECISION="ai-verify"; AI_OUTCOME="error"
        AI_REASON="case collision or duplicate TTS object GUID"
        EXTRA="$(ai_stop_extra)"
        finish 62 fail "AI resolution introduced a case collision or a duplicate object" ;;
    64) DECISION="ai-seed"; AI_OUTCOME="error"; AI_REASON="seed or classification failed"
        EXTRA="$(ai_stop_extra)"
        finish 64 fail "AI stage: shadow seed or classification failed" ;;
    65) DECISION="ai-claude"; AI_OUTCOME="error"; AI_REASON="claude unavailable or timed out"
        EXTRA="$(ai_stop_extra)"
        finish 65 fail "AI stage: claude unavailable, credential failure, or timeout" ;;
    66) DECISION="ai-manifest"; AI_OUTCOME="error"; AI_REASON="manifest invalid"
        EXTRA="$(ai_stop_extra)"
        finish 66 fail "AI stage: the attestation manifest is invalid" ;;
    *)  DECISION="ai-verify"; AI_OUTCOME="error"; AI_REASON="tree failed verification"
        EXTRA="$(ai_stop_extra)"
        finish 67 fail "AI stage: the resolved tree failed verification (rc=${AI_RC})" ;;
  esac
else
  # Detached rebase. `checkout -B korean` (what the GHA workflow does) is not
  # available here: the primary checkout already holds that branch and git refuses.
  # The result is identical and no local ref moves.
  if ! g "${WT}" rebase "${UPSTREAM_SHA}"; then
    g "${WT}" rebase --abort || true
    DECISION="rebase"
    EXTRA="The overlap gate was clean, so this is unexpected. No resolution was attempted."
    finish 30 fail "rebase failed after a clean gate"
  fi
fi
KOREAN_SHA="$(g "${WT}" rev-parse HEAD)"
if [[ -n "$(g "${WT}" status --porcelain)" ]]; then
  DECISION="rebase"; finish 30 fail "worktree is dirty after the rebase"
fi
log "rebased: ${FORK_SHA} -> ${KOREAN_SHA}"

# ------------------------------------------------------------ push-window guard
#
# AI branch only. Absolute as well as elapsed, and the absolute half is the one
# that matters: the staleness guard permits a start as late as LATEST_HHMM, so an
# elapsed-only budget would still allow a push AFTER the GHA fallback has begun
# its own run against the pre-push merge base. Two tiers writing the same branch
# minutes apart is the one race this whole design is arranged to avoid.
if [[ "${AI_ACTIVE}" == true ]]; then
  CI_MIN=$(( SCHED_MIN + AI_CI_OFFSET_MIN ))
  NOW_MIN=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
  # A DISPATCHED CI run falsifies the CI_MIN arithmetic below: the fallback is no
  # longer starting at its cron, it is already running. Without this the guard
  # would compute "plenty of time" and push straight into it -- reintroducing the
  # exact two-tier race the rest of this block exists to prevent.
  if failover_already_fired; then
    DECISION="ai-stop"
    AI_OUTCOME="stop"
    AI_REASON="a failover dispatch is already live tonight"
    EXTRA="$(printf 'The AI resolution completed and verified, but a GHA failover run was already dispatched for tonight. Refusing to push into its window; korean is untouched and the resolution is in %s.' \
             "${AI_RUN_DIR#"${WORKSPACE}"/}")"
    finish 11 ai-stop "AI resolution completed, but a failover run is already live"
  fi
  if [[ "${NOW_MIN}" -ge $((CI_MIN - 10)) ]] || [[ $(( $(date +%s) - STARTED_EPOCH )) -gt 1500 ]]; then
    DECISION="ai-stop"
    AI_OUTCOME="stop"
    AI_REASON="completed too late to push"
    EXTRA="$(printf 'The AI resolution completed and verified, but it is now %s and the GHA fallback starts at %02d:%02d. Refusing to push into its window; `korean` is untouched and the resolution is in `%s`.' \
             "$(date +%H:%M)" "$((CI_MIN / 60))" "$((CI_MIN % 60))" "${AI_RUN_DIR#"${WORKSPACE}"/}")"
    finish 11 ai-stop "AI resolution completed too late to push"
  fi
fi

# ----------------------------------------------------------------- force-push

if [[ "${DRY_RUN}" == true ]]; then
  log "dry-run: would push ${KOREAN_SHA} to korean, leased on ${FORK_SHA}"
else
  if ! with_timeout "${NET_TIMEOUT}" g "${WT}" push \
        "--force-with-lease=refs/heads/korean:${FORK_SHA}" \
        origin "${KOREAN_SHA}:refs/heads/korean"; then
    DECISION="push"
    EXTRA="The lease on ${FORK_SHA} was broken, so something else moved korean during this run. Nothing was retried."
    finish 40 fail "korean force-push rejected (lease conflict)"
  fi
  log "pushed korean -> ${KOREAN_SHA}"
  # From here on korean is on the upstream tip with no +korean.N tag -- the state
  # CI's own no-op guard reads as NEEDS_RELEASE=1. This flag is what tells the
  # failover predicate that a dispatch is now both safe and useful.
  PUSHED=true
fi

# ------------------------------------------------------- prune + Darwin preflight

# Sorted by the date embedded in the branch NAME, not by committer date: on a
# night where upstream had not moved the rebase rewrites nothing, so a
# date-sorted list would rank today's backup by an old commit and prune the
# newest first. `korean-rebase-backup-*` (hand-made, permanent) cannot match.
prune_backups() {
  local branches
  branches="$(g "${REPO_PATH}" for-each-ref --format='%(refname:short)' 'refs/remotes/origin/auto/korean-backup-*' \
    | sed 's|^origin/||' \
    | sed -E 's|^auto/korean-backup-(local-)?([0-9]{8})(.*)$|\2\t&|' \
    | LC_ALL=C sort -r \
    | cut -f2 \
    | tail -n +"$((KEEP_BACKUPS + 1))")"
  [[ -z "${branches}" ]] && { log "prune: nothing to remove (keep=${KEEP_BACKUPS})"; return 0; }
  while read -r b; do
    [[ -z "${b}" ]] && continue
    if [[ "${DRY_RUN}" == true ]]; then
      log "dry-run: would delete backup ${b}"
    else
      g "${REPO_PATH}" push origin --delete "${b}" || log "prune: could not delete ${b}"
    fi
  done <<< "${branches}"
}
prune_backups

if [[ "${SKIP_BUILD}" == true ]]; then
  DECISION="pushed-no-build"
  finish 0 released "pushed; build skipped by --skip-build"
fi

# Local-only failure mode: this volume is case-insensitive APFS. A pair of paths
# differing only in case survives the gate (neither side "changed both") and then
# silently loses one file on checkout. Linux CI cannot see this.
g "${WT}" ls-tree -r --name-only HEAD | LC_ALL=C tr 'A-Z' 'a-z' | LC_ALL=C sort | uniq -d > "${TMPDIR}/case-${REPO}.txt"
if [[ -s "${TMPDIR}/case-${REPO}.txt" ]]; then
  DECISION="case-collision"
  EXTRA="$(head -20 "${TMPDIR}/case-${REPO}.txt")"
  finish 62 fail "case-only path collision on a case-insensitive volume"
fi

# TTSModManager has never shipped a Darwin asset in any release, so there is no
# upstream artefact to pin against. The checksum only asserts "this is the binary
# whose output was proved equivalent to the CI build" -- see design N2.
if [[ ! -x "${TTSMM}" ]]; then
  DECISION="build"; finish 62 fail "TTSModManager-Darwin missing or not executable: ${TTSMM}"
fi
ACTUAL_SHA="$(shasum -a 256 "${TTSMM}" | awk '{print $1}')"
if [[ "${ACTUAL_SHA}" != "${TTSMM_SHA256}" ]]; then
  DECISION="build"
  EXTRA="expected ${TTSMM_SHA256}, found ${ACTUAL_SHA}"
  finish 62 fail "TTSModManager-Darwin checksum mismatch"
fi

# ------------------------------------------------------------------ tag

repo_tag_base() {
  case "${REPO}" in
    SCED)
      # --- TAG-BASE (SCED) ---
      # The base is MOD_VERSION from the REBASED tree, not a git tag. The nearest
      # upstream tag is computed only as a drift warning.
      local mod_version uptag
      mod_version="$(grep -Eo '^MOD_VERSION[[:space:]]*=[[:space:]]*"[^"]+"' "${WT}/src/core/Constants.ttslua" \
                     | sed -E 's/.*"([^"]+)".*/\1/' || true)"
      [[ -n "${mod_version}" ]] || return 1
      BASE="v${mod_version}"
      uptag="$(g "${WT}" describe --tags --abbrev=0 --match 'v[0-9]*' --exclude '*korean*' HEAD 2>/dev/null || true)"
      [[ "${uptag}" == "${BASE}" ]] || log "WARN: MOD_VERSION (${BASE}) != nearest upstream tag (${uptag:-none})"
      # --- end TAG-BASE ---
      ;;
    SCED-downloads)
      # --- TAG-BASE (SCED-downloads) ---
      # `--exclude '*korean*'` is mandatory: without it describe returns the fork's
      # own tag and the scheme collapses. `--match 'v[0-9]*'` rejects `v1.0.0-korean`.
      BASE="$(g "${WT}" describe --tags --abbrev=0 --match 'v[0-9]*' --exclude '*korean*' HEAD 2>/dev/null)" || return 1
      # --- end TAG-BASE ---
      ;;
  esac
  [[ -n "${BASE}" ]]
}

if ! repo_tag_base; then
  DECISION="tag"; finish 50 fail "could not derive the tag base"
fi
LATEST="$(g "${REPO_PATH}" tag -l "${BASE}+korean.*" | sort -V | tail -1)"
if [[ -z "${LATEST}" ]]; then
  N=1
else
  SUFFIX="${LATEST##*.}"
  case "${SUFFIX}" in
    ''|*[!0-9]*) DECISION="tag"; finish 50 fail "unparsable existing tag ${LATEST}" ;;
  esac
  N=$((SUFFIX + 1))
fi
TAG="${BASE}+korean.${N}"
if g "${REPO_PATH}" rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  DECISION="tag"; finish 50 fail "tag ${TAG} already exists"
fi
PREV_TAG="${LATEST}"
log "next tag ${TAG} (base ${BASE}, previous ${PREV_TAG:-none})"

# ------------------------------------------------------------------- build

repo_build() {
  cp "${TTSMM}" "${WT}/TTSModManager-Darwin"
  chmod +x "${WT}/TTSModManager-Darwin"

  case "${REPO}" in
    SCED)
      # --- BUILD (SCED) ---
      # TTSModManager bundles the mod into a single JSON. The filename must match
      # the release-event naming in build-mod.yml.
      FILENAME="Arkham SCE ${TAG}.json"
      ( cd "${WT}" && with_timeout "${BUILD_TIMEOUT}" ./TTSModManager-Darwin -moddir="./" -modfile="./${FILENAME}" ) \
        > "${TMPDIR}/build-${REPO}.log" 2>&1 || return 1
      PROVENANCE="TTSModManager-Darwin sha256:${TTSMM_SHA256:0:16}"
      # --- end BUILD ---
      ;;
    SCED-downloads)
      # --- BUILD (SCED-downloads) ---
      # `make init/build/minify` cannot be used here: `make init` hardcodes the
      # TTSModManager-Linux filename, and `make build`/`make minify` invoke
      # `python`, which does not exist on this machine (only python3). The
      # equivalent commands are issued directly instead, so the Makefile stays
      # byte-identical to upstream.
      local argonui_sha
      ( cd "${WT}" && with_timeout "${NET_TIMEOUT}" git clone --depth 1 --branch main \
          https://github.com/argonui/SCED SCED ) >> "${TMPDIR}/build-${REPO}.log" 2>&1 || return 1
      argonui_sha="$(git -C "${WT}/SCED" rev-parse HEAD)"
      cp -R "${WT}"/src/* "${WT}/SCED/src/"
      cp -R "${WT}"/xml/* "${WT}/SCED/xml/"

      # Runtime-only patch, inside a scratch worktree that is deleted at the end of
      # the run. It cannot reach a commit, and the reason is worth restating now
      # that the workspace does contain a script which commits: the only commit
      # ever made on this tip is the empty attestation, created by
      # ai-rebase-verify.py BEFORE the force-push and therefore before this hunk
      # runs. By the time control gets here the push has already happened, this
      # script still contains no `git add` and no `git commit`, and nothing
      # downstream of the push can reach one either.
      sed -i '' 's|"modexec": "\./TTSModManager-Linux"|"modexec": "./TTSModManager-Darwin"|' "${WT}/build.py"
      grep -q '"modexec": "./TTSModManager-Darwin"' "${WT}/build.py" || return 1
      [[ "$(g "${WT}" status --porcelain -- build.py)" == " M build.py" ]] || return 1

      ( cd "${WT}" && with_timeout "${BUILD_TIMEOUT}" python3 build.py ) \
        >> "${TMPDIR}/build-${REPO}.log" 2>&1 || return 1
      ( cd "${WT}" && with_timeout "${BUILD_TIMEOUT}" python3 minify.py ) \
        >> "${TMPDIR}/build-${REPO}.log" 2>&1 || return 1
      PROVENANCE="TTSModManager-Darwin sha256:${TTSMM_SHA256:0:16}, argonui/SCED@main=${argonui_sha}"
      # --- end BUILD ---
      ;;
  esac
}

if ! repo_build; then
  DECISION="build"
  EXTRA="$(tail -20 "${TMPDIR}/build-${REPO}.log" 2>/dev/null || echo 'no build log')"
  finish 60 fail "build failed"
fi
log "build ok (${PROVENANCE})"

# ------------------------------------------------------------------ verify

FILES=()

# Every python3 below is bounded for the same reason as the four in write_state(),
# notify(), failover_already_fired() and ai_stop_extra() -- but the exposure here is
# WORSE, not milder, and that is why they are not left out. These run AFTER the lock
# is taken (:1194) and after the rebase, so a python3 wedged on the same TCC consent
# prompt holds the workspace lock, never reaches finish(), and writes no state and no
# Discord: the 2026-08-09 outage reproduced one stage later. MAX_RUN_SECONDS does not
# cap a wedged run -- it only lets a LATER run steal the lock. All are direct-child,
# no-stdin shapes, which is the case with_timeout handles correctly.
repo_verify() {
  case "${REPO}" in
    SCED)
      # --- VERIFY (SCED) ---
      # A single mod JSON. The 8 MB floor is calibrated on the real asset of
      # v4.8.0+korean.5 (11,738,304 B) and is the detector for TTSModManager
      # silently producing short or empty output.
      local size
      [[ -s "${WT}/${FILENAME}" ]] || { log "verify: ${FILENAME} missing or empty"; return 1; }
      # The rc separates the two causes this one branch now carries: a real parse
      # failure exits 1, a bound that tripped exits 143.
      with_timeout "${PY_TIMEOUT}" python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${WT}/${FILENAME}" \
        || { log "verify: ${FILENAME} is not valid JSON (rc $?)"; return 1; }
      size="$(stat -f%z "${WT}/${FILENAME}")"     # BSD stat; the CI runner uses -c%s
      [[ "${size}" -ge 8000000 ]] || { log "verify: mod json is ${size} B, floor 8 MB"; return 1; }
      N_NEW=1
      FILES=("${WT}/${FILENAME}")
      # --- end VERIFY ---
      ;;
    SCED-downloads)
      # --- VERIFY (SCED-downloads) ---
      # 82 `.build/*` + 118 `downloadable/**/*.json` + library.json + modversion.json = 202.
      local n_build n_expect n_dl f
      [[ -d "${WT}/.build" ]]       || { log "verify: .build is missing"; return 1; }
      [[ -d "${WT}/downloadable" ]] || { log "verify: downloadable/ is missing"; return 1; }

      # Exact equality, not the CI comment's "expect 82": build.py prints
      # "Warning: No .json file found" and CONTINUES when an input directory is
      # missing, so a silently short build is otherwise invisible. Deriving the
      # expected count from library.json makes the check self-calibrating.
      # `|| return 1` alone was the one site here with no log line at all, so a
      # tripped bound would have produced a bare exit 63 with nothing naming it.
      n_expect="$(cd "${WT}" && with_timeout "${PY_TIMEOUT}" python3 -c 'import json;print(sum(1 for i in json.load(open("library.json"))["content"] if i.get("decomposed")))')" \
        || { log "verify: could not derive the expected count from library.json (rc $?)"; return 1; }
      n_build="$(find "${WT}/.build" -maxdepth 1 -type f | wc -l | tr -d ' ')"
      if [[ "${n_build}" -ne "${n_expect}" ]]; then
        log "verify: .build has ${n_build} files, library.json declares ${n_expect} decomposed entries"
        return 1
      fi
      n_dl="$(find "${WT}/downloadable" -name '*.json' -type f | wc -l | tr -d ' ')"
      N_NEW=$((n_build + n_dl + 2))
      for f in library.json modversion.json; do
        with_timeout "${PY_TIMEOUT}" python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${WT}/${f}" \
          || { log "verify: ${f} is not valid JSON (rc $?)"; return 1; }
      done
      if grep -q 'No .json file found' "${TMPDIR}/build-${REPO}.log" 2>/dev/null; then
        log "verify: build.py reported a missing input directory"
        return 1
      fi
      while IFS= read -r f; do FILES+=("${f}"); done < <(find "${WT}/.build" -maxdepth 1 -type f)
      while IFS= read -r f; do FILES+=("${f}"); done < <(find "${WT}/downloadable" -name '*.json' -type f)
      FILES+=("${WT}/library.json" "${WT}/modversion.json")
      log "verify: inventory ${N_NEW} (.build=${n_build}, downloadable=${n_dl}, +2)"
      # --- end VERIFY ---
      ;;
  esac
}

if ! repo_verify; then
  DECISION="verify"; finish 63 fail "asset verification failed"
fi

# Regression floor. The CI version drops N_PREV to 0 when `gh` fails, which
# disarms the floor exactly when the network is flaky. Here a local baseline from
# the previous run stands in, and with the GHA fallback an hour away, failing
# closed costs little.
PREV_REF="${PREV_TAG}"
if [[ -z "${PREV_REF}" ]]; then
  PREV_REF="$(with_timeout "${NET_TIMEOUT}" gh release view -R "${OWNER}/${REPO}" --json tagName --jq .tagName 2>/dev/null || true)"
fi
N_PREV=0
if [[ -n "${PREV_REF}" ]]; then
  for attempt in 1 2 3; do
    if N_PREV="$(with_timeout "${NET_TIMEOUT}" gh release view "${PREV_REF}" -R "${OWNER}/${REPO}" --json assets --jq '.assets | length' 2>/dev/null)"; then
      break
    fi
    N_PREV=""
    log "verify: could not read assets of ${PREV_REF} (attempt ${attempt}/3)"
    sleep 5
  done
  if [[ -z "${N_PREV}" ]]; then
    # Reads ${STATEFILE} -- the same path on the same volume as the call that
    # blocked for 6h15m on 2026-08-09, and the last unbounded one on this path.
    # A tripped bound yields "", which the fail-closed branch below already
    # handles: no baseline means exit 63, never a silently disarmed floor.
    N_PREV="$(with_timeout "${PY_TIMEOUT}" python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("asset_count",0))' "${STATEFILE}" 2>/dev/null || echo "")"
    if [[ -z "${N_PREV}" ]]; then
      DECISION="verify"; finish 63 fail "no asset baseline available (fail closed)"
    fi
    log "verify: using the offline baseline ${N_PREV} from ${STATEFILE}"
  fi
fi
if [[ "${N_PREV}" -gt 0 ]] && [[ $((N_NEW * 100)) -lt $((N_PREV * ASSET_FLOOR_PCT)) ]]; then
  DECISION="verify"
  EXTRA="${N_NEW} assets is below ${ASSET_FLOOR_PCT}% of ${N_PREV} (${PREV_REF})"
  finish 63 fail "asset count regression"
fi
log "verify ok: ${N_NEW} assets, baseline ${PREV_REF:-none}=${N_PREV}"

if [[ "${DRY_RUN}" == true ]]; then
  DECISION="dry-run"
  EXTRA="would release ${TAG} with ${N_NEW} assets; nothing was pushed."
  finish 0 noop "dry run complete"
fi

# ------------------------------------------------------------- draft release

NOTES="${TMPDIR}/release-notes-${REPO}.md"
{
  # FIRST LINE, byte-identical to the `if:` guard in build-mod.yml / build.yml.
  # Its absence is what lets a hand-made release still trigger a CI build.
  echo "<!-- ${MARKER} -->"
  echo "Automated Korean sync release — rebased \`korean\` onto \`upstream/main\` on the local runner."
  echo
  echo "| item | value |"
  echo "|---|---|"
  echo "| tag | \`${TAG}\` |"
  echo "| korean tip (rebased) | \`${KOREAN_SHA}\` |"
  echo "| korean tip (before) | \`${FORK_SHA}\` |"
  echo "| upstream tip | \`${UPSTREAM_SHA}\` (${UPSTREAM_DATE}) |"
  echo "| backup branch | \`${BACKUP}\` |"
  echo "| build provenance | ${PROVENANCE} |"
  echo "| runner | local ($(hostname)), daily-sync-local.sh |"
} > "${NOTES}"

delete_draft() {
  [[ -z "${RELEASE_ID}" ]] && return 0
  with_timeout "${NET_TIMEOUT}" gh api "repos/${OWNER}/${REPO}/releases/${RELEASE_ID}" --method DELETE >/dev/null 2>&1 \
    || log "cleanup: could not delete draft ${RELEASE_ID}"
  RELEASE_ID=""
}

# The POST API rather than `gh release create`, because the numeric release id is
# needed three more times below and a draft cannot be looked up by tag.
if ! RELEASE_ID="$(with_timeout "${NET_TIMEOUT}" gh api "repos/${OWNER}/${REPO}/releases" --method POST \
      -f tag_name="${TAG}" -f target_commitish="${KOREAN_SHA}" -f name="${TAG}" \
      -F draft=true --field body=@"${NOTES}" --jq .id)"; then
  DECISION="release"; finish 70 fail "could not create the draft release"
fi
if [[ -z "${RELEASE_ID}" ]]; then
  DECISION="release"; finish 70 fail "draft release returned no id"
fi
log "draft release ${RELEASE_ID} created for ${TAG}"

uploaded=false
for attempt in 1 2 3; do
  if with_timeout "${BUILD_TIMEOUT}" gh release upload "${TAG}" "${FILES[@]}" -R "${OWNER}/${REPO}" --clobber; then
    uploaded=true
    break
  fi
  log "release: asset upload attempt ${attempt}/3 failed"
  sleep 15
done
if [[ "${uploaded}" != true ]]; then
  DECISION="release"; delete_draft
  finish 70 fail "asset upload failed"
fi

# Server-side count, not a glob match: a partial upload interrupted by a
# secondary rate limit (seen on run 25382544736) otherwise passes silently.
ATTACHED="$(with_timeout "${NET_TIMEOUT}" gh api "repos/${OWNER}/${REPO}/releases/${RELEASE_ID}" --jq '.assets | length' 2>/dev/null || echo 0)"
if [[ "${ATTACHED}" -ne "${N_NEW}" ]]; then
  DECISION="release"
  EXTRA="server reports ${ATTACHED} assets, expected ${N_NEW}"
  delete_draft
  finish 70 fail "attached asset count mismatch"
fi
log "release: ${ATTACHED} assets attached and confirmed server-side"

# --------------------------------------------------------------------- go-live

# One PATCH creates the tag, publishes the assets and moves releases/latest. Up to
# this point nothing was visible to any user. The tag it creates is the contract
# with the GHA fallback: an hour later its no-op guard sees a +korean tag on the
# tip and stops.
if ! RELEASE_URL="$(with_timeout "${NET_TIMEOUT}" gh api --method PATCH \
      "repos/${OWNER}/${REPO}/releases/${RELEASE_ID}" \
      -F draft=false -f make_latest=true --jq .html_url)"; then
  DECISION="golive"; delete_draft
  finish 70 fail "could not flip the draft live"
fi

DECISION="released"
log "released ${TAG} -> ${RELEASE_URL}"
finish 0 released "released ${TAG} (${N_NEW} assets)"
