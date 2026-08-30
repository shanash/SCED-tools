#!/usr/bin/env bash
# sced-run-now.sh -- run tonight's upstream rebase + redeploy by hand, now.
#
# Design: .am/manual-nightly-runner/design.md (approach (b), analyze.md section 7).
#
# WHAT THIS IS
#   One operator entry point that reproduces what the two launchd agents do at
#   02:17 and 02:47, on demand, for every repo in sync-schedule.json's
#   policy.order, sequentially, with a rehearsal default.
#
#   It ASSEMBLES ARGV AND NOTHING ELSE. It re-implements no pipeline stage, takes
#   no lock, writes nothing the driver owns, and edits nothing. Its whole value is
#   removing four things an operator would otherwise have to remember correctly
#   under pressure: the two invocations, the mandatory --force, the repo order, and
#   the fact that the two runs cannot overlap because the lock is workspace-wide.
#
#   Each repo is run as
#       <launchd.program> <launchd.wrapper> --repo <NAME> --force [flags...]
#   i.e. through the SAME boot-volume wrapper launchd calls, so a manual run keeps
#   the nightly's PATH pin, deps preflight, interpreter assertion, TCC grant
#   assertion and both TCC canaries. --no-wrapper drops to daily-sync-local.sh
#   directly and loses exactly those five; it exists because the wrapper is
#   unversioned and `git checkout` cannot restore it.
#
# WHAT A --live RUN AUTHORISES, PER REPO, IF ITS OVERLAP GATE IS CLEAN
#   (a) push a new remote branch auto/korean-backup-local-<YYYYMMDD>;
#   (b) force-push the PUBLIC `korean` branch (--force-with-lease);
#   (c) DELETE remote auto/korean-backup-* beyond the newest 14 -- irreversible;
#   (d) publish a release and move releases/latest -- every mod user's download;
#   (e) everything else the driver's own "WHAT A SINGLE launchctl bootstrap
#       DELEGATES" header lists, including the AI stage and the GHA failover when
#       their allowlists name the repo.
#   The default is a rehearsal (--dry-run); --live additionally requires a typed
#   confirmation unless --yes.
#
# INVARIANTS THIS FILE HOLDS, AND WHY EACH ONE MATTERS
#   * It takes NO lock. The lock is ${STATE_ROOT}/run/daily-sync.lock and it is
#     workspace-wide, mkdir-ed by the driver. If this tool held it, EVERY child
#     driver would exit 3 immediately. The only thing done with it here is an
#     advisory read (see lock_probe); the driver's own mkdir is the authority.
#   * A future --parallel must NEVER be added. That same workspace-wide lock means
#     the second repo would exit 3, or -- worse -- steal the lock from a wedged
#     first run once MAX_RUN_SECONDS (3600 s) elapsed. The 30-minute nightly
#     stagger exists for this reason. The loop below is sequential and blocks on
#     each child before starting the next.
#   * It never sets, unsets or rewrites XPC_SERVICE_NAME, SCED_SYNC_SCHED_* or
#     SCED_SYNC_LATEST_START_*. Setting a SCED_SYNC_SCHED_* would shadow the plist
#     -- the exact condition `sced-schedule.sh verify` exits 2 for -- and is
#     pointless, because --force already covers the window. Unsetting
#     XPC_SERVICE_NAME would disable the driver's exit-5 interlock. If this tool is
#     ever somehow launchd-parented under an agent's own label, exit 5 firing is
#     CORRECT.
#   * Every interpreter it runs is "${PYBIN}", pinned to a platform path, never a
#     bare python3 resolved from PATH. Same rule, same reason, as
#     daily-sync-local.sh and sced-failover.sh: Homebrew's python3 is an app bundle,
#     so TCC makes it its own responsible_path instead of inheriting /bin/bash's
#     grant on /Volumes/PRO-G40. Read-only assertion here, like sced-failover.sh:
#     warn and fall back, never abort an operator's run over a schedule read.
#   * No GNU coreutils. The local tier has no Homebrew dependency at all and must
#     not gain one; nothing here needs a wall clock, because every child is
#     foreground and the driver bounds its own stages.
#
#   The tool's own exit band {7,8,9} is disjoint from the driver's table
#   {0,1,2,3,4,5,10,11,20,21,30,40,50,60,62,63,64,65,66,67,70} and from the
#   wrapper's {2,6,12}. If the driver ever gains a 7, 8 or 9, THIS BAND MUST MOVE.
#
# Usage:
#   sced-run-now.sh [--live] [--repo NAME] [options] [-- driver args...]
#
#     (default)         a REHEARSAL: --dry-run is passed to every repo. The real
#                       gate, rebase, build and verify all run; nothing is pushed,
#                       pruned or released.
#     --live            omit --dry-run. Requires typing LIVE unless --yes. See
#                       "WHAT A --live RUN AUTHORISES" in this file's header.
#     --dry-run         accepted as an explicit no-op; --live --dry-run is a usage
#                       error rather than a silent preference.
#     --repo NAME       run just one repo instead of every repo in policy.order
#     --yes             skip the --live confirmation AND the run-band refusal
#     --status          print state and the argv that WOULD be used; run nothing
#     --notify          omit --no-notify, so the run posts to Discord like a nightly
#     --failover        omit --no-failover, so a failed run may dispatch the GHA
#                       fallback (only ever possible when SCED_SYNC_FAILOVER names
#                       the repo; a dispatch consumes that repo's per-UTC-day budget
#                       and suppresses the real watchdog, which is why it is off)
#     --no-ai           append --no-ai (force the claude-driven stage off)
#     --skip-build      append --skip-build
#     --keep-scratch    append --keep-scratch
#     --keep-backups N  append --keep-backups N
#     --no-wrapper      target daily-sync-local.sh directly instead of the launchd
#                       wrapper; keeps pipeline fidelity, loses the PATH pin, the
#                       deps preflight, the interpreter assertion, the TCC grant
#                       assertion and both canaries
#     --no-snapshot     skip the pre-run copy of <repo>.last-run.json (not advised)
#     --stop-on-fail    stop the loop at the first FAIL; a designed skip never stops it
#     --help, -h        this text, followed by the driver's live exit table
#     --                everything after is appended verbatim to EACH repo's argv.
#                       --repo is REFUSED there: both the driver and the wrapper take
#                       the LAST --repo on the line, so it would re-target the run
#                       while this tool still labels and snapshots the repo you asked
#                       for. Use this tool's own --repo.
#
#   THIS TOOL CANNOT REPRODUCE A TCC FAILURE. A terminal-parented run inherits the
#   terminal's own grant, so no consent dialog is possible and a green run here is
#   evidence about the build, never about TCC. The only faithful reproduction is
#     launchctl kickstart -k gui/$(id -u)/com.shanash.sced-daily-sync.<repo>
#   which re-runs the plist verbatim and therefore cannot be given --force, so
#   outside 02:17/02:47 it stale-skips at exit 4 by design. Use that to diagnose a
#   wedge; use this to run the night.
#
#   --force IS ALWAYS PASSED, AND IT BYPASSES THE NO-OP GUARD as well as the
#   staleness guard. On a night when upstream has not moved and the tip already
#   carries a +korean.N tag, a --live run still cuts a NEW release with identical
#   content and moves releases/latest. Run without --live first and read the
#   `guard: behind=N has_release_tag=M` line in the log before deciding. --force
#   never bypasses the overlap gate (exit 10); nothing does.
#
#   A WRAPPER-LEVEL ABORT (exit 2, 6 or 12) POSTS TO DISCORD. --no-notify covers the
#   driver, not the wrapper, which has its own notify() on its abort paths. It
#   cannot pollute <repo>.notify-signature (the wrapper keeps no throttle state),
#   so the cost is channel noise only. The escape hatch is not owned by this tool
#   and needs no flag: SCED_SYNC_ENV_FILE=/path/to/quiet-env sced-run-now.sh ...
#   is inherited by both wrapper and driver. Two traps if you use it: the file must
#   be mode 600 or both layers exit 2, and it must still carry a webhook when
#   --notify is given, because the driver exits 2 on an empty webhook.
#
#   A --dry-run of SCED-downloads runs the real build, up to 1800 s. Prefer
#   `caffeinate -i sced-run-now.sh ...` so a sleep cannot orphan a live driver.
#
# Exit codes:
#   0  every selected repo finished ok
#   1  usage error (also: a driver usage error propagated from a bad -- argument)
#   *  any other code is the exit code of the WORST repo, verbatim from the
#      driver's own table -- FAIL outranks SKIP outranks ok, ties broken by policy
#      order, where SKIP is the driver's own designed-skip set {4,10,11} and an
#      unknown code classifies as FAIL. Aggregation is severity-ranked and NOT
#      max(): numerically, a designed skip (10) outranks a wrapper abort (2).
#   7  --live refused: LIVE was not typed, or stdin is not a terminal and --yes
#      was not given
#   8  refused before anything ran: the workspace lock is live, or it is inside
#      the nightly run band and --yes was not given
#   9  refused before anything ran: preflight failed, or the repo order is unusable

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --------------------------------------------------------------------- options

LIVE=false
EXPLICIT_DRY_RUN=false
ONE_REPO=""
YES=false
MODE="run"
WANT_NOTIFY=false
WANT_FAILOVER=false
NO_AI=false
SKIP_BUILD=false
KEEP_SCRATCH=false
KEEP_BACKUPS=""
NO_WRAPPER=false
NO_SNAPSHOT=false
STOP_ON_FAIL=false
PASSTHRU=()

usage() { sed -n '/^# Usage:/,/^#   9  /p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --live)         LIVE=true; shift ;;
    --dry-run)      EXPLICIT_DRY_RUN=true; shift ;;
    --repo)         ONE_REPO="${2:-}"; shift 2 ;;
    --yes)          YES=true; shift ;;
    --status)       MODE="status"; shift ;;
    --notify)       WANT_NOTIFY=true; shift ;;
    --failover)     WANT_FAILOVER=true; shift ;;
    --no-ai)        NO_AI=true; shift ;;
    --skip-build)   SKIP_BUILD=true; shift ;;
    --keep-scratch) KEEP_SCRATCH=true; shift ;;
    --keep-backups) KEEP_BACKUPS="${2:-}"; shift 2 ;;
    --no-wrapper)   NO_WRAPPER=true; shift ;;
    --no-snapshot)  NO_SNAPSHOT=true; shift ;;
    --stop-on-fail) STOP_ON_FAIL=true; shift ;;
    --help|-h)      MODE="help"; shift ;;
    --)             shift; while [[ $# -gt 0 ]]; do PASSTHRU+=("$1"); shift; done ;;
    *)              echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "${LIVE}" == true && "${EXPLICIT_DRY_RUN}" == true ]]; then
  echo "ERROR: --live and --dry-run are mutually exclusive; say which one you mean" >&2
  exit 1
fi
if [[ -n "${KEEP_BACKUPS}" ]]; then
  case "${KEEP_BACKUPS}" in
    ''|*[!0-9]*) echo "ERROR: --keep-backups must be a non-negative integer" >&2; exit 1 ;;
  esac
fi
# `--repo` is the ONE flag that may not appear after `--`, and the asymmetry is
# deliberate. Pass-through goes last precisely so the driver's last-wins parse lets
# an operator override a value-taking flag (--keep-backups) -- but --repo does not
# TUNE the run, it RE-TARGETS it. Both the driver's own parse loop and the wrapper's
# canary (which walks "$@" overwriting REPO_ARG) take the LAST --repo on the line,
# while this tool's loop variable, its summary row, its snapshot filename and its
# FAIL-row log lookup all still name the repo the operator asked for. So
# `--repo A -- --repo B` runs B and reports A, defeating the snapshot's forensic
# guarantee and the summary's honesty -- two of the three reasons this tool exists.
if [[ "${#PASSTHRU[@]}" -gt 0 ]]; then
  for _p in "${PASSTHRU[@]}"; do
    if [[ "${_p}" == "--repo" ]]; then
      echo "ERROR: --repo cannot be passed after '--'; use this tool's own --repo instead." >&2
      echo "       Both the driver and the wrapper take the LAST --repo on the line, so it" >&2
      echo "       would silently re-target the run while the summary, the snapshot and the" >&2
      echo "       FAIL-row log path all still name the repo you asked for." >&2
      exit 1
    fi
  done
fi

# ------------------------------------------------------------------ parameters
#
# ~/.config/sced-sync/env is deliberately NOT read here. The wrapper loads it and
# the driver loads it; this tool never holds the webhook in its own environment and
# needs no mode-600 assertion of its own. Everything it does export is what the
# operator already had -- SCED_SYNC_ENV_FILE, SCED_SYNC_PROBE_TIMEOUT and the rest
# are inherited verbatim by the child, which is what makes those escape hatches
# work here with no code at all.

if [[ -d "${SCRIPT_DIR}/../.." && -f "${SCRIPT_DIR}/../config/sync-schedule.json" ]]; then
  WORKSPACE="${SCED_SYNC_WORKSPACE:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
else
  WORKSPACE="${SCED_SYNC_WORKSPACE:-/Volumes/PRO-G40/Projects/SCED}"
fi
STATE_ROOT="${SCED_SYNC_STATE_ROOT:-${WORKSPACE}/.local-sync}"
SCHEDULE_JSON="${WORKSPACE}/SCED-tools/config/sync-schedule.json"
DRIVER="${WORKSPACE}/SCED-tools/scripts/daily-sync-local.sh"
LOCK="${STATE_ROOT}/run/daily-sync.lock"
KEEP_LOGS="${SCED_SYNC_KEEP_LOGS:-30}"

# Same pin and the same read-only assertion as sced-failover.sh: warn and fall
# back, never abort. A schedule read that fails must degrade to the compiled-in
# literals below, not kill the operator's run.
PYBIN="${SCED_SYNC_PYTHON:-/usr/bin/python3}"
case "${PYBIN}" in
  /bin/*|/sbin/*|/usr/bin/*|/usr/sbin/*|/usr/libexec/*) ;;
  *) echo "WARNING: SCED_SYNC_PYTHON='${PYBIN}' is not a platform path; falling back to /usr/bin/python3" >&2
     PYBIN=/usr/bin/python3 ;;
esac

# The repo order, in ONE place, and the ONLY copy outside sync-schedule.json. Kept
# in step with sced-failover.sh's FAILOVER_REPOS, which carries it for the same
# reason: the config lives on /Volumes/PRO-G40 and may be unreachable.
RUN_NOW_REPOS_FALLBACK="SCED-downloads SCED"

STAMP_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
STAMP_LOCAL="$(date +%Y%m%d-%H%M%S)"
TODAY_LOCAL="$(date +%Y%m%d)"
SNAPSHOT_DIR=""
TRANSCRIPT=""

log() { printf '%s [run-now] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }
die_code() { local rc="$1"; shift; echo "ERROR: $*" >&2; exit "${rc}"; }

# ------------------------------------------------------------- schedule config
#
# ONE interpreter spawn reads everything the tool needs and emits a line-oriented
# block the shell parses with `case`. Pure shell was considered and rejected: it
# would be the only JSON reader in this codebase that does not parse JSON, and
# /bin/sed opening this file on /Volumes/PRO-G40 carries exactly the same TCC
# exposure as the pinned interpreter, so it buys nothing on the axis that motivated
# the question. The compiled-in fallback already covers the no-interpreter case.
config_read() {
  [[ -r "${SCHEDULE_JSON}" ]] || return 1
  "${PYBIN}" -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
L = d.get("launchd", {}) or {}
print("program=" + str(L.get("program", "")))
print("wrapper=" + str(L.get("wrapper", "")))
for r in (d.get("policy", {}) or {}).get("order", []) or []:
    print("order=" + str(r))
for name, r in (d.get("repos", {}) or {}).items():
    print("hhmm=%s %s %s %s" % (name, r.get("local_hhmm", ""),
                                r.get("latest_hhmm", ""), r.get("launchd_label", "")))
' "${SCHEDULE_JSON}" 2>/dev/null
}

CFG_PROGRAM=""
CFG_WRAPPER=""
CFG_ORDER=""
CFG_HHMM=""
_cfg_line=""
while IFS= read -r _cfg_line; do
  case "${_cfg_line}" in
    program=*) CFG_PROGRAM="${_cfg_line#program=}" ;;
    wrapper=*) CFG_WRAPPER="${_cfg_line#wrapper=}" ;;
    order=*)   CFG_ORDER="${CFG_ORDER}${CFG_ORDER:+ }${_cfg_line#order=}" ;;
    hhmm=*)    CFG_HHMM="${CFG_HHMM}${_cfg_line#hhmm=}
" ;;
  esac
done < <(config_read || true)

order_valid() {   # every element known, no duplicates, list non-empty
  local o="$1" r seen=""
  [[ -n "${o}" ]] || return 1
  for r in ${o}; do
    case "${r}" in SCED|SCED-downloads) ;; *) return 1 ;; esac
    case " ${seen} " in *" ${r} "*) return 1 ;; esac
    seen="${seen} ${r}"
  done
  return 0
}

if order_valid "${CFG_ORDER}"; then
  ORDER="${CFG_ORDER}"
  ORDER_SOURCE="config"
else
  ORDER="${RUN_NOW_REPOS_FALLBACK}"
  ORDER_SOURCE="fallback"
fi

fallback_hhmm() {
  case "$1/$2" in
    SCED/local)            echo "0247" ;;
    SCED/latest)           echo "0327" ;;
    SCED-downloads/local)  echo "0217" ;;
    SCED-downloads/latest) echo "0257" ;;
    *)                     echo "" ;;
  esac
}

cfg_repo_field() {   # $1 repo, $2 = local|latest|label
  local want_repo="$1" want_key="$2" n lo la lb
  while IFS=' ' read -r n lo la lb; do
    [[ "${n}" == "${want_repo}" ]] || continue
    case "${want_key}" in
      local)  printf '%s' "${lo}" ;;
      latest) printf '%s' "${la}" ;;
      label)  printf '%s' "${lb}" ;;
    esac
    return 0
  done <<EOF
${CFG_HHMM}
EOF
  return 1
}

# Shape guard, identical to sced-failover.sh's: hhmm_to_min() indexes this as four
# digits and a malformed value would abort the pass under `set -e` arithmetic.
sched_hhmm() {
  local v=""
  v="$(cfg_repo_field "$1" "$2" 2>/dev/null || true)"
  case "${v}" in [0-9][0-9][0-9][0-9]) ;; *) v="" ;; esac
  [[ -n "${v}" ]] || v="$(fallback_hhmm "$1" "$2")"
  printf '%s' "${v}"
}

sched_label() {
  local v=""
  v="$(cfg_repo_field "$1" label 2>/dev/null || true)"
  [[ -n "${v}" ]] || v="com.shanash.sced-daily-sync.$1"
  printf '%s' "${v}"
}

hhmm_to_min() { printf '%s' "$(( 10#${1:0:2} * 60 + 10#${1:2:2} ))"; }
min_to_hhmm() { printf '%02d:%02d' "$(( $1 / 60 ))" "$(( $1 % 60 ))"; }

# The nightly run band, [min(local_hhmm) - 10, max(latest_hhmm) + 30]: the identical
# formula and offsets as sced-schedule.sh's assert_not_in_run_band(), so the two
# tools cannot disagree about when it is unsafe to touch a run in flight. Computed
# over EVERY repo in the order, not just the selected one -- the lock is shared.
BAND_LO=0
BAND_HI=0
compute_band() {
  local r lo hi lomin himin first=1
  for r in ${ORDER}; do
    lo="$(sched_hhmm "${r}" local)"
    hi="$(sched_hhmm "${r}" latest)"
    [[ -n "${lo}" && -n "${hi}" ]] || continue
    lomin="$(hhmm_to_min "${lo}")"
    himin="$(hhmm_to_min "${hi}")"
    if [[ "${first}" -eq 1 ]]; then
      BAND_LO="${lomin}"; BAND_HI="${himin}"; first=0
    else
      if [[ "${lomin}" -lt "${BAND_LO}" ]]; then BAND_LO="${lomin}"; fi
      if [[ "${himin}" -gt "${BAND_HI}" ]]; then BAND_HI="${himin}"; fi
    fi
  done
  # RAW pre-offset literals -- min(local_hhmm)=02:17 and max(latest_hhmm)=03:27 --
  # so the -10/+30 below applies uniformly on both paths and yields 02:07-03:57,
  # the same band as the config path. This line read `BAND_LO=127` until 2026-08-19:
  # 127 is already 02:07, i.e. the POST-offset low, so the -10 was applied twice and
  # the fallback band began at 01:57. Fail-safe (a wider refusal window) and
  # unreachable while order_valid() and fallback_hhmm() agree on the same two repos,
  # but it contradicted the formula this function claims to share with
  # sced-schedule.sh's assert_not_in_run_band().
  if [[ "${first}" -eq 1 ]]; then BAND_LO=137; BAND_HI=207; fi   # 02:17-03:27, raw
  BAND_LO=$(( BAND_LO - 10 ))
  BAND_HI=$(( BAND_HI + 30 ))
  if [[ "${BAND_LO}" -lt 0 ]]; then BAND_LO=0; fi
  return 0
}
compute_band

now_min() { printf '%s' "$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))"; }
in_run_band() {
  local n; n="$(now_min)"
  [[ "${n}" -ge "${BAND_LO}" && "${n}" -le "${BAND_HI}" ]]
}

# ------------------------------------------------------------------- inspectors

# Reads one dotted field out of <repo>.last-run.json. Never sources the file.
state_get() {
  local repo="$1" path="$2"
  local f="${STATE_ROOT}/state/${repo}.last-run.json"
  [[ -r "${f}" ]] || return 1
  RN_PATH="${path}" "${PYBIN}" -c '
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
for k in os.environ["RN_PATH"].split("."):
    if not isinstance(d, dict) or k not in d:
        sys.exit(1)
    d = d[k]
print("" if d is None else d)
' "${f}" 2>/dev/null
}

# The CLAUDE.md wedge fingerprint: the state file's mtime far LATER than the
# finished_at inside it means the run decided at 02:17 and the write did not land
# until hours afterwards -- the 2026-08-09 / 2026-08-19 post-lock signature. BSD
# `date -j -f` and `date -r`, both platform binaries; best effort throughout.
STATE_MTIME_ISO=""
STATE_SKEW_S=""
state_mtime_skew() {
  local repo="$1" f="${STATE_ROOT}/state/${repo}.last-run.json" mt="" fin="" fe=""
  STATE_MTIME_ISO=""; STATE_SKEW_S=""
  [[ -r "${f}" ]] || return 1
  mt="$(stat -f %m "${f}" 2>/dev/null || true)"
  [[ -n "${mt}" ]] || return 1
  STATE_MTIME_ISO="$(date -r "${mt}" '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || true)"
  fin="$(state_get "${repo}" finished_at 2>/dev/null || true)"
  [[ -n "${fin}" ]] || return 0
  fe="$(date -j -f '%Y-%m-%dT%H:%M:%S' "${fin:0:19}" +%s 2>/dev/null || true)"
  [[ -n "${fe}" ]] || return 0
  STATE_SKEW_S="$(( mt - fe ))"
  return 0
}

# ADVISORY ONLY, and TOCTOU-racy by construction. The driver's own mkdir on
# daily-sync.lock is the authority and exits 3; this exists to save the 90 seconds
# a doomed run would spend in the wrapper's canaries and to say why. Nothing here
# creates, removes or waits on the lock.
LOCK_STATE="free"
LOCK_DETAIL=""
lock_probe() {
  LOCK_STATE="free"; LOCK_DETAIL=""
  [[ -d "${LOCK}" ]] || return 0
  local owner="${LOCK}/owner" pid=""
  if [[ -r "${owner}" ]]; then
    LOCK_DETAIL="$(head -1 "${owner}" 2>/dev/null || true)"
    # Two FIXED sed programs, keyed then positional, exactly as sced-failover.sh's
    # lock_is_live(): BSD sed has no `\|` in a BRE and would match it literally.
    pid="$(sed -n '1s/^pid=\([0-9][0-9]*\).*/\1/p' "${owner}" 2>/dev/null | head -1)"
    [[ -n "${pid}" ]] || pid="$(sed -n '1s/.*[^A-Za-z_]pid=\([0-9][0-9]*\).*/\1/p' "${owner}" 2>/dev/null | head -1)"
  fi
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    LOCK_STATE="held"
  else
    LOCK_STATE="stale"
  fi
  return 0
}

# The driver's header IS the authority for what a code means, so it is read live
# rather than copied -- a copy would rot, and this codebase already treats rotted
# counts as a defect class. The wrapper codes 6 and 12 are the compiled-in special
# cases: the driver has neither.
rc_meaning() {
  local rc="$1" t=""
  if [[ -r "${DRIVER}" ]]; then
    t="$(sed -n '/^# Exit codes:/,/^#  70/p' "${DRIVER}" \
         | sed -n "s/^#[[:space:]]*${rc}[[:space:]][[:space:]]*//p" | head -1)"
  fi
  if [[ -z "${t}" ]]; then
    case "${rc}" in
      6)   t="wrapper: probe timed out -- a TCC consent prompt is pending" ;;
      12)  t="wrapper: a TCC grant no longer covers git or gh -- see assert-tcc-grants.sh --print" ;;
      130) t="killed by SIGINT (Ctrl-C)" ;;
      143) t="killed by SIGTERM" ;;
      *)   t="(see ${DRIVER##*/} --help)" ;;
    esac
  fi
  printf '%s' "${t}"
}

# The SKIP set is the driver's own, from failover_should_run's `kind != fail`
# comment: 0, 4 (stale-skip), 10 (overlap gate) and 11 (AI-STOP), where 10 and 11
# are DESIGNED SKIPS a human is required for. 4 cannot occur here because --force
# is always passed; it is kept so the two classifications stay word-for-word the
# same. A future designed-skip code this tool does not know about classifies as
# FAIL -- the safe direction.
rc_class() {
  case "$1" in
    0)       printf 'ok' ;;
    4|10|11) printf 'SKIP' ;;
    *)       printf 'FAIL' ;;
  esac
}
class_rank() {
  case "$1" in
    ok)   printf '0' ;;
    SKIP) printf '1' ;;
    *)    printf '2' ;;
  esac
}

# ------------------------------------------------------------ target resolution

PROGRAM="${CFG_PROGRAM:-/bin/bash}"
WRAPPER="${CFG_WRAPPER:-~/scripts/sced-daily-sync-launch.sh}"
case "${WRAPPER}" in
  "~/"*) WRAPPER="${HOME}/${WRAPPER#\~/}" ;;
  "~")   WRAPPER="${HOME}" ;;
esac
if [[ "${NO_WRAPPER}" == true ]]; then
  TARGET="${DRIVER}"
  TARGET_KIND="driver (no wrapper)"
else
  TARGET="${WRAPPER}"
  TARGET_KIND="launchd wrapper"
fi

# --------------------------------------------------------------- argv assembly
#
# --repo first because the wrapper's canary discovers the repo by walking "$@" for
# the token after --repo, and because it matches the plists' own ProgramArguments
# byte for byte. --force second because it is the one flag that is never optional.
# Pass-through last so the driver's last-wins parse lets an operator override a
# value-taking flag -- while the booleans stay monotonic: every driver boolean is
# VAR=true with no negation, so NOTHING an operator puts after `--` can turn a
# rehearsal into a live run or turn --force off. The one value-taking flag exempted
# from that override is --repo, rejected at parse time: last-wins would re-target the
# run behind the summary, the snapshot and the FAIL-row log lookup, all of which are
# keyed to the loop variable below.
PLAN=()
plan_argv() {
  local repo="$1" live="${2:-${LIVE}}"
  PLAN=("${PROGRAM}" "${TARGET}" --repo "${repo}" --force)
  if [[ "${live}" != true ]]; then PLAN+=(--dry-run); fi
  if [[ "${WANT_FAILOVER}" != true ]]; then PLAN+=(--no-failover); fi
  if [[ "${WANT_NOTIFY}" != true ]]; then PLAN+=(--no-notify); fi
  if [[ "${NO_AI}" == true ]]; then PLAN+=(--no-ai); fi
  if [[ "${SKIP_BUILD}" == true ]]; then PLAN+=(--skip-build); fi
  if [[ "${KEEP_SCRATCH}" == true ]]; then PLAN+=(--keep-scratch); fi
  if [[ -n "${KEEP_BACKUPS}" ]]; then PLAN+=(--keep-backups "${KEEP_BACKUPS}"); fi
  if [[ "${#PASSTHRU[@]}" -gt 0 ]]; then PLAN+=("${PASSTHRU[@]}"); fi
  return 0
}
argv_str() {
  local s="" a
  for a in "${PLAN[@]}"; do s="${s}${s:+ }${a}"; done
  printf '%s' "${s}"
}

# ------------------------------------------------------------------ repo select

SELECTED="${ORDER}"
if [[ -n "${ONE_REPO}" ]]; then
  case " ${ORDER} " in
    *" ${ONE_REPO} "*) SELECTED="${ONE_REPO}" ;;
    *) die_code 9 "--repo '${ONE_REPO}' is not in the resolved order (${ORDER}) [source: ${ORDER_SOURCE}]" ;;
  esac
fi

# ------------------------------------------------------------------------ help

if [[ "${MODE}" == "help" ]]; then
  usage
  echo
  echo "Driver exit table (read live from ${DRIVER}):"
  echo
  if [[ -r "${DRIVER}" ]]; then
    sed -n '/^# Exit codes:/,/^#  70/p' "${DRIVER}" | sed 's/^# \{0,1\}//' | sed 's/^/  /'
  else
    echo "  (driver not readable at ${DRIVER} -- run its --help)"
  fi
  exit 0
fi

# ---------------------------------------------------------------------- status

repo_state_block() {
  local repo="$1" v=""
  echo "  --- ${repo}"
  printf '      schedule      : %s / %s    %s\n' \
    "$(sched_hhmm "${repo}" local)" "$(sched_hhmm "${repo}" latest)" "$(sched_label "${repo}")"
  if [[ -r "${STATE_ROOT}/state/${repo}.last-run.json" ]]; then
    printf '      decision      : %-12s exit_code: %s\n' \
      "$(state_get "${repo}" decision || echo '?')" "$(state_get "${repo}" exit_code || echo '?')"
    printf '      started_at    : %s\n'  "$(state_get "${repo}" started_at || echo '?')"
    printf '      finished_at   : %s   (duration %ss)\n' \
      "$(state_get "${repo}" finished_at || echo '?')" "$(state_get "${repo}" duration_s || echo '?')"
    if state_mtime_skew "${repo}"; then
      if [[ -n "${STATE_SKEW_S}" ]]; then
        if [[ "${STATE_SKEW_S}" -gt 300 ]]; then
          printf '      state mtime   : %s   [skew: %ss -- WEDGE FINGERPRINT: the write landed long after the run decided]\n' \
            "${STATE_MTIME_ISO}" "${STATE_SKEW_S}"
        else
          printf '      state mtime   : %s   [skew: %ss]\n' "${STATE_MTIME_ISO}" "${STATE_SKEW_S}"
        fi
      else
        printf '      state mtime   : %s\n' "${STATE_MTIME_ISO}"
      fi
    fi
    printf '      tag           : %-22s backup: %s\n' \
      "$(state_get "${repo}" tag || echo '-')" "$(state_get "${repo}" backup_branch || echo '-')"
    printf '      ai / failover : %s / %s\n' \
      "$(state_get "${repo}" ai.outcome || echo '?')" "$(state_get "${repo}" failover.outcome || echo '?')"
    v="$(state_get "${repo}" log || echo '')"
    printf '      log           : %s\n' "${v:--}"
  else
    echo "      last run      : (none)"
  fi
  plan_argv "${repo}" false
  printf '      would run     : %s\n' "$(argv_str)"
  plan_argv "${repo}" true
  printf '      would run     : %s   [--live]\n' "$(argv_str)"
  echo
}

run_status() {
  local cfg_state="unreadable" tgt_state="missing" drv_state="missing" band_state="OUTSIDE" r
  [[ -r "${SCHEDULE_JSON}" ]] && cfg_state="readable"
  [[ -r "${TARGET}" ]] && tgt_state="readable"
  [[ -x "${TARGET}" ]] && tgt_state="readable, +x"
  [[ -r "${DRIVER}" ]] && drv_state="readable"
  [[ -x "${DRIVER}" ]] && drv_state="readable, +x"
  in_run_band && band_state="INSIDE"
  lock_probe
  echo "sced-run-now --status"
  printf '  workspace     : %s\n' "${WORKSPACE}"
  printf '  state_root    : %s\n' "${STATE_ROOT}"
  printf '  config        : %s  (%s)\n' "${SCHEDULE_JSON}" "${cfg_state}"
  printf '  order         : %s        [source: %s]\n' "${ORDER// /, }" "${ORDER_SOURCE}"
  printf '  selected      : %s\n' "${SELECTED// /, }"
  printf '  program       : %s                   [platform: %s]\n' \
    "${PROGRAM}" "$(program_is_platform && echo ok || echo NO)"
  printf '  target        : %s  (%s) [%s]\n' "${TARGET}" "${TARGET_KIND}" "${tgt_state}"
  printf '  driver        : %s  [%s]\n' "${DRIVER}" "${drv_state}"
  printf '  now           : %s KST -- %s the nightly band %s-%s\n' \
    "$(date +%H:%M)" "${band_state}" "$(min_to_hhmm "${BAND_LO}")" "$(min_to_hhmm "${BAND_HI}")"
  if [[ "${LOCK_STATE}" == "free" ]]; then
    printf '  lock          : free\n'
  else
    printf '  lock          : %s  (%s)\n' "${LOCK_STATE}" "${LOCK_DETAIL:-no owner file}"
  fi
  printf '  in-flight     : %s\n' "$(pgrep -fl daily-sync-local 2>/dev/null | head -3 | tr '\n' ';' || true)"
  echo
  for r in ${SELECTED}; do repo_state_block "${r}"; done
  return 0
}

# ------------------------------------------------------------------ guard 1: preflight
#
# The platform rule sced_schedule.py's validate() already enforces for
# launchd.program, applied here because this tool is now argv[0]'s parent.
program_is_platform() {
  case "${PROGRAM}" in
    /bin/*|/sbin/*|/usr/bin/*|/usr/sbin/*|/usr/libexec/*) return 0 ;;
    *) return 1 ;;
  esac
}

preflight() {
  [[ -d "${WORKSPACE}" ]] || die_code 9 "workspace not found: ${WORKSPACE} (is /Volumes/PRO-G40 mounted?)"
  [[ -r "${DRIVER}" ]]    || die_code 9 "driver not readable: ${DRIVER}"
  program_is_platform     || die_code 9 "launchd.program '${PROGRAM}' is not a platform path (/bin, /sbin, /usr/bin, /usr/sbin, /usr/libexec)"
  # READABLE, not executable: both target files are invoked as an ARGUMENT to
  # ${PROGRAM}, exactly as the plists do, and the wrapper on disk is mode 0644.
  if [[ ! -r "${TARGET}" ]]; then
    if [[ "${NO_WRAPPER}" != true ]]; then
      die_code 9 "launchd wrapper not readable: ${TARGET}
       That file is unversioned and has no copy in any repository, so \`git checkout\` cannot
       restore it; ~/scripts/.sced-daily-sync-launch.backup/ is the only rollback path.
       To run anyway without it, at the cost of the PATH pin, the deps preflight, the
       interpreter assertion, the TCC grant assertion and both canaries: --no-wrapper"
    fi
    die_code 9 "target not readable: ${TARGET}"
  fi
  order_valid "${SELECTED}" || die_code 9 "unusable repo order: '${SELECTED}'"
  return 0
}

if [[ "${MODE}" == "status" ]]; then
  # Read-only mode: no transcript, no snapshot, no guard at all, so it stays safe
  # to run against a mid-incident workspace -- which is the whole point of it.
  #
  # It REPORTS first and refuses second, deliberately: on an unmounted volume the
  # useful output is exactly the part that still works (the compiled-in order, the
  # target paths, `[source: fallback]`), and dying before printing it would make
  # the one command an operator reaches for during an incident the one command that
  # says nothing. The refusal still lands, with the same exit 9 preflight uses.
  run_status
  [[ -d "${WORKSPACE}" ]] || die_code 9 "workspace not found: ${WORKSPACE} (is /Volumes/PRO-G40 mounted?)"
  exit 0
fi

preflight

# --------------------------------------------------- own transcript (after preflight)
#
# Installed here, after preflight, so an unmounted volume fails cleanly before
# anything tries to write to it. This closes a real gap: the wrapper's pre-lock
# output -- its log lines, the two canary verdicts, any abort -- goes to
# /tmp/sced-daily-sync-<repo>.log under launchd but to the terminal only under a
# manual run, which is why the 2026-08-19 SCED wrapper abort left nothing at all in
# .local-sync/. /usr/bin/tee is a platform binary and process substitution makes it
# a SIBLING of the wrapper, never an ancestor.
mkdir -p "${STATE_ROOT}/logs" || die_code 9 "cannot create ${STATE_ROOT}/logs"
TRANSCRIPT="${STATE_ROOT}/logs/run-now-${STAMP_LOCAL}.log"
exec > >(/usr/bin/tee -a "${TRANSCRIPT}") 2>&1

# The driver's own pruner globs ${STATE_ROOT}/logs/${REPO}-*.log and cannot match
# run-now-*, so this prunes its own transcripts with the same idiom.
#
# The trailing `|| true` is load-bearing HERE in a way it is not in the driver,
# and it was a real failure before it was a comment: `tee -a` creates the
# transcript lazily, on its first write, so on the very first run-now ever the glob
# can still be unmatched when this line runs. `ls` then fails, `set -o pipefail`
# propagates it, and `set -e` kills the script BETWEEN the transcript redirection
# and the first log line -- exit 1, with an empty transcript and not one word on
# the terminal explaining why. The driver's copy is safe only because its own
# LOG_FILE always exists by the time cleanup() runs.
# shellcheck disable=SC2012
ls -1t "${STATE_ROOT}/logs/run-now-"*.log 2>/dev/null \
  | tail -n +"$(( KEEP_LOGS + 1 ))" | while read -r old; do rm -f "${old}"; done || true

log "=== sced-run-now (live=${LIVE} target=${TARGET_KIND} repos=${SELECTED// /,}) ==="
log "transcript: ${TRANSCRIPT}"

# ------------------------------------------------- guard 2: the nightly run band

if in_run_band; then
  if [[ "${YES}" != true ]]; then
    echo "ERROR: it is $(date +%H:%M), inside the nightly run band $(min_to_hhmm "${BAND_LO}")-$(min_to_hhmm "${BAND_HI}")." >&2
    echo "       A manual run now either collides with the nightly's lock or races its" >&2
    echo "       --force-with-lease on korean. Re-run with --yes to override, or wait until" >&2
    echo "       after $(min_to_hhmm "${BAND_HI}"). Nothing was touched." >&2
    exit 8
  fi
  echo "  WARNING: inside the run band $(min_to_hhmm "${BAND_LO}")-$(min_to_hhmm "${BAND_HI}"); --yes given, proceeding" >&2
fi

# ------------------------------------------------------ guard 3: lock pre-check

lock_probe
if [[ "${LOCK_STATE}" == "held" ]]; then
  echo "ERROR: the workspace lock is held: ${LOCK_DETAIL:-${LOCK}}" >&2
  echo "       A run is in flight. This check is advisory -- the driver's own mkdir is the" >&2
  echo "       authority and would exit 3 -- but there is no point spending 90 s in the" >&2
  echo "       wrapper's canaries to find that out. Nothing was touched." >&2
  exit 8
fi
if [[ "${LOCK_STATE}" == "stale" ]]; then
  log "lock: stale directory present (${LOCK_DETAIL:-no live owner}); the driver will steal it -- continuing"
fi

# ------------------------------------------------- guard 4: the --live confirmation

live_banner() {
  local r
  echo "================================================================================"
  echo "  LIVE RUN -- this is not a rehearsal"
  echo "================================================================================"
  printf '  order         : %s          [source: %s]\n' "${SELECTED// /, }" "${ORDER_SOURCE}"
  printf '  target        : %s %s  (%s)\n' "${PROGRAM}" "${TARGET}" "${TARGET_KIND}"
  for r in ${SELECTED}; do
    plan_argv "${r}"
    printf '  argv          : %s\n' "$(argv_str)"
  done
  printf '  now           : %s KST -- %s the nightly band %s-%s\n' \
    "$(date +%H:%M)" "$(in_run_band && echo INSIDE || echo OUTSIDE)" \
    "$(min_to_hhmm "${BAND_LO}")" "$(min_to_hhmm "${BAND_HI}")"
  printf '  lock          : %s\n' "${LOCK_STATE}"
  printf '  snapshot      : %s\n' "$( [[ "${NO_SNAPSHOT}" == true ]] && echo '(skipped: --no-snapshot)' || echo "${STATE_ROOT}/backup/run-now-${STAMP_UTC}/" )"
  echo
  echo "  For EACH repo, if its overlap gate is clean, this will:"
  echo "    * push  auto/korean-backup-local-${TODAY_LOCAL}                 (new remote branch)"
  echo "    * force-push  korean  --force-with-lease                  (rewrites a PUBLIC branch)"
  echo "    * DELETE remote auto/korean-backup-* beyond the newest 14 (irreversible)"
  echo "    * publish a release and move releases/latest              (every mod user's download)"
  echo
  echo "  --force ALSO bypasses the no-op guard. On a night when upstream has not moved and"
  echo "  the tip already carries a +korean.N tag, each repo still cuts a NEW release with"
  echo "  identical content. Run without --live first and read the \`guard: behind=N"
  echo "  has_release_tag=M\` line before deciding."
  echo
  echo "  Recovery:"
  for r in ${SELECTED}; do
    echo "    SCED-tools/scripts/recover-korean-from-backup.sh --repo ${r} --branch auto/korean-backup-local-${TODAY_LOCAL}"
  done
  echo "    gh release delete <tag> -R shanash/<repo> --cleanup-tag --yes"
  echo
}

if [[ "${LIVE}" == true ]]; then
  live_banner
  if [[ "${YES}" == true ]]; then
    log "--yes given; proceeding without the typed confirmation"
  else
    # Never call read without a terminal: a LIVE sitting on a redirected stdin must
    # not be consumable, and read returning 1 at EOF must not abort mid-flight
    # under set -e.
    if [[ ! -t 0 ]]; then
      echo "ERROR: --live needs a terminal to confirm on, and stdin is not one." >&2
      echo "       Re-run interactively, or pass --yes if this is deliberate. Nothing was touched." >&2
      exit 7
    fi
    ANSWER=""
    printf 'Type LIVE to proceed (anything else aborts): '
    read -r ANSWER || ANSWER=""
    if [[ "${ANSWER}" != "LIVE" ]]; then
      echo "aborted -- nothing was touched." >&2
      exit 7
    fi
  fi
fi

# ----------------------------------------------------------- guard 5: snapshot
#
# UNCONDITIONAL, and that is the finding that made it so: write_state() has exactly
# one caller, finish(), and the DRY-RUN path reaches it. A rehearsal therefore
# overwrites <repo>.last-run.json -- the runbook's primary forensic artifact and the
# failover's audit record -- just as a live run does. .local-sync/state/ held the
# proof while this was being written: SCED's file was a 2026-08-18 08:48 dry-run
# record that had overwritten that night's 02:47 nightly.
snapshot_state() {
  local dir="${STATE_ROOT}/backup/run-now-${STAMP_UTC}" r f
  mkdir -p "${dir}" || die_code 9 "cannot create snapshot directory ${dir}"
  for r in ${SELECTED}; do
    for f in "${STATE_ROOT}/state/${r}.last-run.json" "${STATE_ROOT}/state/${r}.notify-signature"; do
      if [[ -r "${f}" ]]; then
        cp -p "${f}" "${dir}/" || log "snapshot: could not copy ${f} (continuing)"
      else
        log "snapshot: ${f##*/} absent -- nothing to preserve"
      fi
    done
  done
  : > "${dir}/argv.txt" || die_code 9 "cannot write ${dir}/argv.txt"
  for r in ${SELECTED}; do
    plan_argv "${r}"
    argv_str >> "${dir}/argv.txt"
    printf '\n' >> "${dir}/argv.txt"
  done
  SNAPSHOT_DIR="${dir}"
  log "snapshot: ${dir}"
  return 0
}

if [[ "${NO_SNAPSHOT}" == true ]]; then
  log "snapshot: SKIPPED (--no-snapshot); <repo>.last-run.json will be overwritten with no copy"
else
  snapshot_state
fi

# ------------------------------------------------------------------------ loop

# The trap SETS A FLAG and the loop breaks on it; a print-only trap was a real defect
# and not a cosmetic one. Bash defers a trapped signal until the current foreground
# command returns, runs the trap body, and then RESUMES NORMAL EXECUTION -- so a
# print-only handler let one Ctrl-C kill repo 1's child and then start repo 2's own
# --live run: force-push korean, prune remote backups, move releases/latest, with no
# re-confirmation, because the --live prompt is a single pre-loop guard.
# --stop-on-fail did not cover this: it is off by default, and a killed child
# classifies as an ordinary FAIL rather than as an interrupted state.
INTERRUPTED=false
trap 'INTERRUPTED=true; echo "interrupted -- the remaining repos will NOT be attempted; the driver'"'"'s own cleanup() releases the lock" >&2' INT TERM

RES_REPO=()
RES_RC=()
RES_CLASS=()
RES_SECS=()
RES_T0=()

RUN_T0="$(date +%s)"
for repo in ${SELECTED}; do
  plan_argv "${repo}"
  log "==> ${repo}: $(argv_str)"
  t0="$(date +%s)"
  # set +e around the call, rc on the NEXT line, never `if ! ...`: the `!` reserved
  # word REPLACES the status it negates, which is the trap the wrapper documents on
  # both of its probe call sites.
  set +e
  "${PLAN[@]}"
  rc=$?
  set -e
  t1="$(date +%s)"
  cls="$(rc_class "${rc}")"
  RES_REPO+=("${repo}")
  RES_RC+=("${rc}")
  RES_CLASS+=("${cls}")
  RES_SECS+=("$(( t1 - t0 ))")
  RES_T0+=("${t0}")
  log "<== ${repo}: rc ${rc} ${cls} -- $(rc_meaning "${rc}")  ($(( t1 - t0 ))s)"
  # Checked BEFORE --stop-on-fail, and unconditional: an interrupt is the operator
  # saying stop, which outranks every flag. The summary and the aggregate exit still
  # run, so a partial run reports what it did and the snapshot stays discoverable.
  if [[ "${INTERRUPTED}" == true ]]; then
    log "interrupted: stopping after ${repo}; the remaining repos were not attempted"
    break
  fi
  if [[ "${cls}" == "FAIL" && "${STOP_ON_FAIL}" == true ]]; then
    log "--stop-on-fail: stopping after ${repo}; the remaining repos were not attempted"
    break
  fi
done
RUN_T1="$(date +%s)"

# ------------------------------------------------------------------- summary
#
# Severity-ranked, NOT max(): numerically a designed skip (10) outranks a wrapper
# abort (2), so max() would report a clean-looking 10 for a night that actually
# failed. Overall rc is the rc of the FIRST repo, in policy order, holding the
# highest class -- which keeps it a REAL driver exit code an operator can look up.
fmt_dur() {
  local s="$1"
  if [[ "${s}" -ge 60 ]]; then printf '%dm%02ds' "$(( s / 60 ))" "$(( s % 60 ))"
  else printf '%ds' "${s}"; fi
}

n_ok=0; n_skip=0; n_fail=0
best_rank=-1
OVERALL_RC=0
echo
echo "================================================================================"
printf '  run-now summary -- %s -- %s repo(s), %s\n' \
  "$( [[ "${LIVE}" == true ]] && echo live || echo dry-run )" \
  "${#RES_REPO[@]}" "$(fmt_dur "$(( RUN_T1 - RUN_T0 ))")"
echo "================================================================================"
i=0
while [[ "${i}" -lt "${#RES_REPO[@]}" ]]; do
  r="${RES_REPO[$i]}"; rc="${RES_RC[$i]}"; cls="${RES_CLASS[$i]}"; secs="${RES_SECS[$i]}"; rt0="${RES_T0[$i]}"
  extra=""
  # A wrapper-level abort is labelled distinctly: --no-notify covers the driver, not
  # the wrapper, so these two codes DID post to Discord.
  if [[ "${NO_WRAPPER}" != true && ( "${rc}" == "2" || "${rc}" == "6" || "${rc}" == "12" ) ]]; then
    extra="  [wrapper-abort -- a Discord notice was sent]"
  fi
  printf '  %-16s rc %-3s %-5s %s%s  (%s)\n' \
    "${r}" "${rc}" "${cls}" "$(rc_meaning "${rc}")" "${extra}" "$(fmt_dur "${secs}")"
  # The runbook's entry point for a FAIL: the repo's own per-run log, read back from
  # <repo>.last-run.json's .log field.
  #
  # GATED ON THE STATE FILE HAVING BEEN WRITTEN BY *THIS* RUN. A run that dies
  # before finish() -- a usage error, the wrapper's own preflight, an abort at
  # either canary -- never calls write_state(), so the field still names LAST
  # NIGHT'S log. Printing it unguarded sends an operator who is already looking at a
  # failure to a file about a different run, which is strictly worse than printing
  # nothing. mtime older than this repo's start is exactly that case.
  if [[ "${cls}" == "FAIL" ]]; then
    sf="${STATE_ROOT}/state/${r}.last-run.json"
    smt="$( [[ -r "${sf}" ]] && stat -f %m "${sf}" 2>/dev/null || echo 0 )"
    if [[ "${smt}" -ge "${rt0}" ]]; then
      lg="$(state_get "${r}" log 2>/dev/null || true)"
      [[ -n "${lg}" ]] && printf '  %-16s log: %s\n' "" "${lg}"
    else
      printf '  %-16s log: (this run wrote no state -- it died before finish(); see the transcript above)\n' ""
    fi
  fi
  case "${cls}" in
    ok)   n_ok=$(( n_ok + 1 )) ;;
    SKIP) n_skip=$(( n_skip + 1 )) ;;
    *)    n_fail=$(( n_fail + 1 )) ;;
  esac
  rank="$(class_rank "${cls}")"
  if [[ "${rank}" -gt "${best_rank}" ]]; then best_rank="${rank}"; OVERALL_RC="${rc}"; fi
  i=$(( i + 1 ))
done
echo
[[ "${INTERRUPTED}" == true ]] && printf '  interrupted: yes -- stopped by a signal; any repo after the last row above was NOT attempted\n'
[[ -n "${SNAPSHOT_DIR}" ]] && printf '  snapshot   : %s\n' "${SNAPSHOT_DIR}/"
printf '  transcript : %s\n' "${TRANSCRIPT}"
printf '  result     : %s ok, %s designed-skip, %s failed  ->  exit %s\n' \
  "${n_ok}" "${n_skip}" "${n_fail}" "${OVERALL_RC}"
echo

exit "${OVERALL_RC}"
