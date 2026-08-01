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
#         unreleased Mach-O binary (TTSModManager-Darwin) on this machine.
#   `launchctl bootout gui/$(id -u)/com.shanash.sced-daily-sync.<repo>` revokes it
#   in seconds, and the GHA fallback keeps running either way.
#
# WHAT IT WILL NEVER DO
#   Resolve a rebase conflict. There is no `-X ours`, no `--strategy`, no
#   `rebase --continue`, and no `rerere` anywhere in this file. The overlap gate
#   below is a stricter predicate than git's own conflict signal -- on 2026-06-27
#   both sides had touched 142 files and git reported only 18 -- so when it trips,
#   the run stops and a human decides.
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
#  20  fetch failed
#  21  overlap gate script failed (the gate itself errored; the gate did NOT trip)
#  30  rebase failed after a clean gate (unexpected)
#  40  backup push, or korean force-push rejected (lease conflict)
#  50  tag derivation failed / tag already exists
#  60  build failed
#  62  Darwin build-equivalence preflight failed (checksum / case collision)
#  63  asset verification failed
#  70  draft release creation, asset upload, or go-live failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# --------------------------------------------------------------------- options

REPO=""
DRY_RUN=false
SKIP_BUILD=false
FORCE=false
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
MAX_RUN_SECONDS="${SCED_SYNC_MAX_RUN_SECONDS:-3600}"
ASSET_FLOOR_PCT=95
OWNER="shanash"
MARKER="sced-local-sync: assets-already-attached"

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

# timeout(1) is not installed (no coreutils). A watchdog subshell stands in.
# `|| rc=$?` is required: under `set -e` a non-zero `wait` would kill the script
# before the caller can decide what the failure means.
with_timeout() {
  local secs="$1"; shift
  local rc=0 pid wd
  "$@" & pid=$!
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

# --------------------------------------------------------------- notification

# Colours: released green, noop/stale grey, stop orange (a person is needed, not a
# breakage), fail red.
notify_color() {
  case "$1" in
    released)        echo 3066993 ;;
    noop|stale-skip) echo 9807270 ;;
    stop)            echo 15105570 ;;
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

notify() {
  local kind="$1" title="$2" code="$3"
  [[ "${NOTIFIED}" == true ]] && return 0
  NOTIFIED=true

  local now sig
  now="$(date +%s)"
  case "${kind}" in
    stop) sig="overlap:${MERGE_BASE}:${OVERLAP_COUNT}" ;;
    # DECISION is in the signature because one code can be reached from several
    # places (20 = no upstream remote / fetch origin / fetch upstream / worktree
    # add). Without it, a change of failure MODE at a constant code is invisible
    # to should_notify() and gets throttled as "unchanged".
    fail) sig="fail:${code}:${DECISION}" ;;
    *)    sig="${kind}:${code}" ;;
  esac

  local payload
  payload="$(
    KIND="${kind}" TITLE="${title}" CODE="${code}" COLOR="$(notify_color "${kind}")" \
    REPO="${REPO}" OWNER="${OWNER}" MENTION="${SCED_SYNC_MENTION:-}" \
    DURATION="$(( $(date +%s) - STARTED_EPOCH ))" \
    FORK_SHA="${FORK_SHA}" KOREAN_SHA="${KOREAN_SHA}" UPSTREAM_SHA="${UPSTREAM_SHA}" \
    UPSTREAM_DATE="${UPSTREAM_DATE}" MERGE_BASE="${MERGE_BASE}" TAG="${TAG}" \
    RELEASE_URL="${RELEASE_URL}" ASSETS="${N_NEW}" BACKUP="${BACKUP}" \
    EXTRA="${EXTRA}" LOGF="${LOG_FILE#"${WORKSPACE}"/}" TS="$(date +%Y-%m-%dT%H:%M:%S%z)" \
    python3 <<'PY'
import json, os

e = os.environ.get
kind, code = e("KIND", ""), e("CODE", "")
mention = e("MENTION", "")
grade = {"released": "released", "noop": "no-op", "stale-skip": "skipped",
         "stop": "STOP", "fail": "FAIL"}.get(kind, kind)

head = f'[{e("REPO")}] {grade} · {e("TITLE")}'
content = (mention + " " + head).strip() if kind in ("stop", "fail") and mention else head

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
    "title": e("TITLE"),
    "color": int(e("COLOR")),
    "timestamp": e("TS"),
    "fields": fields,
    "footer": {"text": f'log: {e("LOGF")}'},
}
if e("RELEASE_URL"):
    embed["url"] = e("RELEASE_URL")

print(json.dumps({"username": "SCED daily sync", "content": content, "embeds": [embed]}))
PY
  )" || { log "notify: payload build failed"; return 0; }

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
  REPO="${REPO}" DECISION="${DECISION}" CODE="${code}" \
  STARTED_AT="${STARTED_AT}" FINISHED_AT="$(date +%Y-%m-%dT%H:%M:%S%z)" \
  DURATION="$(( $(date +%s) - STARTED_EPOCH ))" \
  FORK_SHA="${FORK_SHA}" KOREAN_SHA="${KOREAN_SHA}" UPSTREAM_SHA="${UPSTREAM_SHA}" \
  MERGE_BASE="${MERGE_BASE}" OVERLAP="${OVERLAP_COUNT}" BACKUP="${BACKUP}" \
  TAG="${TAG}" PREV_TAG="${PREV_TAG}" ASSETS="${N_NEW}" PREV_ASSETS="${N_PREV}" \
  RELEASE_URL="${RELEASE_URL}" PROVENANCE="${PROVENANCE}" LOGF="${LOG_FILE}" \
  OUT="${STATEFILE}" \
  python3 <<'PY' || log "state: write failed"
import json, os
e = os.environ.get
def num(k):
    try:    return int(e(k, "0") or 0)
    except ValueError: return 0
doc = {
    "schema": 1,
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
  write_state "${code}"
  notify "${kind}" "${title}" "${code}"
  exit "${code}"
}

cleanup() {
  if [[ "${KEEP_SCRATCH}" != true && -n "${WT}" && -e "${WT}" ]]; then
    g "${REPO_PATH}" worktree remove --force "${WT}" 2>/dev/null \
      || log "cleanup: could not remove worktree ${WT}"
  fi
  if [[ -n "${LOG_FILE}" && -d "${STATE_ROOT}/logs" ]]; then
    # shellcheck disable=SC2012
    ls -1t "${STATE_ROOT}/logs/${REPO}-"*.log 2>/dev/null \
      | tail -n +"$((KEEP_LOGS + 1))" | while read -r old; do rm -f "${old}"; done
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
  > "${TMPDIR}/gate-${REPO}.txt" 2>&1
GATE_RC=$?
set -e
cat "${TMPDIR}/gate-${REPO}.txt"
MERGE_BASE="$(sed -n 's/^Merge base: *//p' "${TMPDIR}/gate-${REPO}.txt" | head -1)"
OVERLAP_COUNT="$(sed -n 's/^Overlap: *//p' "${TMPDIR}/gate-${REPO}.txt" | head -1)"
: "${OVERLAP_COUNT:=0}"

case "${GATE_RC}" in
  0) DECISION="proceed" ;;
  10)
    DECISION="stop-overlap"
    EXTRA="$(printf '```\n%s\n```\nNext: `SCED-tools/scripts/check-upstream-overlap.sh --repo %s`\nThe GHA fallback fires at %s KST and stops at the same gate.' \
             "$(sed -n '/^Overlapping paths/,$p' "${TMPDIR}/gate-${REPO}.txt" | head -20)" "${REPO}" \
             "$([[ "${REPO}" == SCED ]] && echo 03:47 || echo 03:17)")"
    finish 10 stop "overlap gate tripped (${OVERLAP_COUNT} files)"
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

# Detached rebase. `checkout -B korean` (what the GHA workflow does) is not
# available here: the primary checkout already holds that branch and git refuses.
# The result is identical and no local ref moves.
if ! g "${WT}" rebase "${UPSTREAM_SHA}"; then
  g "${WT}" rebase --abort || true
  DECISION="rebase"
  EXTRA="The overlap gate was clean, so this is unexpected. No resolution was attempted."
  finish 30 fail "rebase failed after a clean gate"
fi
KOREAN_SHA="$(g "${WT}" rev-parse HEAD)"
if [[ -n "$(g "${WT}" status --porcelain)" ]]; then
  DECISION="rebase"; finish 30 fail "worktree is dirty after the rebase"
fi
log "rebased: ${FORK_SHA} -> ${KOREAN_SHA}"

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
      # the run. It cannot reach a commit: this script contains no `git add` and no
      # `git commit`, and the push already happened above.
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

repo_verify() {
  case "${REPO}" in
    SCED)
      # --- VERIFY (SCED) ---
      # A single mod JSON. The 8 MB floor is calibrated on the real asset of
      # v4.8.0+korean.5 (11,738,304 B) and is the detector for TTSModManager
      # silently producing short or empty output.
      local size
      [[ -s "${WT}/${FILENAME}" ]] || { log "verify: ${FILENAME} missing or empty"; return 1; }
      python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${WT}/${FILENAME}" \
        || { log "verify: ${FILENAME} is not valid JSON"; return 1; }
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
      n_expect="$(cd "${WT}" && python3 -c 'import json;print(sum(1 for i in json.load(open("library.json"))["content"] if i.get("decomposed")))')" || return 1
      n_build="$(find "${WT}/.build" -maxdepth 1 -type f | wc -l | tr -d ' ')"
      if [[ "${n_build}" -ne "${n_expect}" ]]; then
        log "verify: .build has ${n_build} files, library.json declares ${n_expect} decomposed entries"
        return 1
      fi
      n_dl="$(find "${WT}/downloadable" -name '*.json' -type f | wc -l | tr -d ' ')"
      N_NEW=$((n_build + n_dl + 2))
      for f in library.json modversion.json; do
        python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "${WT}/${f}" \
          || { log "verify: ${f} is not valid JSON"; return 1; }
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
    N_PREV="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("asset_count",0))' "${STATEFILE}" 2>/dev/null || echo "")"
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
