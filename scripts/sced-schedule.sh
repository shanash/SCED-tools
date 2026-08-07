#!/usr/bin/env bash
# sced-schedule.sh
#
# Operator control for WHEN the nightly upstream rebase + redeploy runs. Design:
# .am/sync-schedule-control/design.md §4, §5.
#
# The schedule used to be six independent hardcodes across three systems -- two
# launchd plists, the driver's SCHED_HHMM/LATEST_HHMM constants, two GitHub
# Actions cron lines, and a documentation table -- bound by three ordering
# constraints that nothing enforced. This is the single entry point:
# SCED-tools/config/sync-schedule.json is the source of truth and everything
# else is rendered from it or reported as drift.
#
# WHAT IT WRITES, AND WHAT IT ONLY PRINTS
#   It writes artifacts it is the SOLE AUTHOR of -- the two LaunchAgent plists --
#   and single-purpose data cells whose structure it asserts before and after:
#   the one `- cron:` line of each workflow (only under `apply --ci --yes`) and
#   the four `When (KST)` cells of CLAUDE.md (only under `apply --docs`).
#   Everything else it reports, with the exact patch printed. In particular it
#   NEVER edits daily-sync-local.sh: a schedule tool must not hold write access
#   to the script that force-pushes two public branches unattended.
#
# THE INTERLOCK
#   The plist carries the StartCalendarInterval trigger AND the staleness-guard
#   bounds (SCED_SYNC_SCHED_* / SCED_SYNC_LATEST_START_*) in one file, rendered
#   together. Editing only the trigger used to leave the guard on its old bound:
#   launchd fired, the driver exited 4, and the resulting grey `stale-skip` was
#   throttled away. `verify` cross-checks the loaded trigger against the loaded
#   bound inside launchd's own state, which also catches a plist that was edited
#   but never reloaded -- invisible on disk.
#
# Usage:
#   sced-schedule.sh show    [--json] [--fetch] [--gh]
#   sced-schedule.sh set     --repo <SCED|SCED-downloads> --at HH:MM
#                            [--latest HH:MM] [--ci-offset MIN] [--days LIST]
#                            [--ack-collision TEXT] [--dry-run] [--strict]
#   sced-schedule.sh apply   [--local] [--ci] [--docs] [--all]
#                            [--dry-run] [--check] [--yes] [--no-reload] [--no-push]
#   sced-schedule.sh verify  [--json] [--fetch] [--gh] [--strict]
#                            [--sites LIST] [--no-launchd-probe]
#   sced-schedule.sh rollback --local [--label LABEL] [--stamp STAMP] [--dry-run]
#
#   Common: --config PATH --launch-agents-dir DIR --state-root DIR --env-file PATH
#           --ci-ref REF --home DIR --help
#
#   `apply` with no tier flag means --local: the machine-local, revocable tier.
#   `apply --ci` requires --yes; without it the plan is printed and nothing runs,
#   because a cron change is a commit and a push to a public fork's DEFAULT
#   branch.
#
# Exit codes:
#   0  success; for `verify`: every site consistent with the source of truth
#   1  usage error
#   2  precondition failed (config, missing tool, or a schedule key in the 0600
#      env file that would shadow the plist)
#   3  the workspace lock is held -- a sync is running; nothing was touched
#  10  drift found (`verify`), or `apply --check` would change something
#  11  the proposed schedule violates an invariant -- nothing was written
#  50  plist render / lint / semantic assertion failed -- NOTHING WAS SWAPPED
#  51  launchctl reload failed. THE AGENT MAY BE UNLOADED; see stderr
#  60  CI write failed (fetch, worktree, structure assertion, commit, or push)
#  61  CI post-flight failed (not on the default branch, or not registered)
#  70  docs rewrite failed (table structure assertion)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# --------------------------------------------------------------------- options

VERB=""
CONFIG="${SCRIPT_DIR}/../config/sync-schedule.json"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
STATE_ROOT="${SCED_SYNC_STATE_ROOT:-${REPO_ROOT}/.local-sync}"
ENV_FILE="${SCED_SYNC_ENV_FILE:-${HOME}/.config/sced-sync/env}"
HOME_DIR="${HOME}"
CI_REF=""
DRIVER="${SCRIPT_DIR}/daily-sync-local.sh"
DOCS="${REPO_ROOT}/CLAUDE.md"

JSON=false
FETCH=false
GH=false
STRICT=false
SITES=""
NO_LAUNCHD_PROBE=false
DRY_RUN=false
CHECK=false
YES=false
NO_RELOAD=false
NO_PUSH=false
DO_LOCAL=false
DO_CI=false
DO_DOCS=false
SET_REPO=""
SET_AT=""
SET_LATEST=""
SET_CI_OFFSET=""
SET_DAYS=""
SET_ACK=""
RB_LABEL=""
RB_STAMP=""

usage() { sed -n '/^# Usage:/,/^#  70/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

if [[ $# -eq 0 ]]; then usage; exit 1; fi
case "$1" in
  show|set|apply|verify|rollback) VERB="$1"; shift ;;
  --help|-h) usage; exit 0 ;;
  *) echo "ERROR: unknown verb '$1' (expected show, set, apply, verify or rollback)" >&2; exit 1 ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)            CONFIG="${2:-}"; shift 2 ;;
    --launch-agents-dir) LAUNCH_AGENTS_DIR="${2:-}"; shift 2 ;;
    --state-root)        STATE_ROOT="${2:-}"; shift 2 ;;
    --env-file)          ENV_FILE="${2:-}"; shift 2 ;;
    --home)              HOME_DIR="${2:-}"; shift 2 ;;
    --ci-ref)            CI_REF="${2:-}"; shift 2 ;;
    --json)              JSON=true; shift ;;
    --fetch)             FETCH=true; shift ;;
    --gh)                GH=true; shift ;;
    --strict)            STRICT=true; shift ;;
    --sites)             SITES="${2:-}"; shift 2 ;;
    --no-launchd-probe)  NO_LAUNCHD_PROBE=true; shift ;;
    --dry-run)           DRY_RUN=true; shift ;;
    --check)             CHECK=true; shift ;;
    --yes)               YES=true; shift ;;
    --no-reload)         NO_RELOAD=true; shift ;;
    --no-push)           NO_PUSH=true; shift ;;
    --local)             DO_LOCAL=true; shift ;;
    --ci)                DO_CI=true; shift ;;
    --docs)              DO_DOCS=true; shift ;;
    --all)               DO_LOCAL=true; DO_CI=true; DO_DOCS=true; shift ;;
    --repo)              SET_REPO="${2:-}"; shift 2 ;;
    --at)                SET_AT="${2:-}"; shift 2 ;;
    --latest)            SET_LATEST="${2:-}"; shift 2 ;;
    --ci-offset)         SET_CI_OFFSET="${2:-}"; shift 2 ;;
    --days)              SET_DAYS="${2:-}"; shift 2 ;;
    --ack-collision)     SET_ACK="${2:-}"; shift 2 ;;
    --label)             RB_LABEL="${2:-}"; shift 2 ;;
    --stamp)             RB_STAMP="${2:-}"; shift 2 ;;
    --help|-h)           usage; exit 0 ;;
    *)                   echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "${VERB}" == "apply" && "${DO_LOCAL}" != true && "${DO_CI}" != true && "${DO_DOCS}" != true ]]; then
  DO_LOCAL=true
fi

# ---------------------------------------------------------------- preconditions

for dep in python3 git; do
  command -v "${dep}" >/dev/null 2>&1 || { echo "ERROR: ${dep} is required" >&2; exit 2; }
done
if [[ ! -r "${CONFIG}" ]]; then
  echo "ERROR: config not readable: ${CONFIG}" >&2
  exit 2
fi

UID_NUM="$(id -u)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/sced-schedule.XXXXXX")"
RAW="${TMP}/raw"
mkdir -p "${RAW}"
LOCK="${STATE_ROOT}/run/daily-sync.lock"
LOCK_HELD=false

cleanup() {
  if [[ "${LOCK_HELD}" == true ]]; then
    rm -f "${LOCK}/owner"
    rmdir "${LOCK}" 2>/dev/null || true
  fi
  rm -rf "${TMP}"
}
trap cleanup EXIT

py() { python3 "${SCRIPT_DIR}/sced_schedule.py" --config "${CONFIG}" --home "${HOME_DIR}" "$@"; }

# A schedule key in the 0600 env file defeats the entire interlock: the driver
# sources that file with `set -a` AFTER launchd's environment
# (daily-sync-local.sh:127-130), so it silently wins over the plist. Installing
# or checking a plist while one exists is meaningless, so this is a hard
# precondition for `verify` and `apply` rather than a reported finding. Only key
# NAMES are read here -- the values in that file are secrets.
assert_no_env_shadow() {
  [[ -r "${ENV_FILE}" ]] || return 0
  local bad
  # `|| true` is load-bearing: no match is the HEALTHY case, but grep exits 1 for
  # it and `set -o pipefail` would propagate that out of the substitution and
  # abort the run.
  bad="$(grep -oE '^(SCED_SYNC_SCHED_|SCED_SYNC_LATEST_START_)[A-Z_]*' "${ENV_FILE}" 2>/dev/null \
         | LC_ALL=C sort -u | tr '\n' ' ' || true)"
  [[ -z "${bad}" ]] && return 0
  {
    echo "ERROR: ${ENV_FILE} defines schedule key(s): ${bad}"
    echo "       That file is sourced with \`set -a\` AFTER launchd's environment, so those keys"
    echo "       silently override the plist and the trigger/guard interlock stops meaning"
    echo "       anything. Remove them; the schedule belongs in ${CONFIG}."
  } >&2
  exit 2
}

# The workspace-wide lock the nightly driver uses. A stale lock is NEVER taken
# over here: only the driver may do that, and stealing one would let this tool
# push to origin/korean under a run whose --force-with-lease was pinned before
# the push (daily-sync-local.sh:557).
take_lock() {
  mkdir -p "${STATE_ROOT}/run"
  if ! mkdir "${LOCK}" 2>/dev/null; then
    local owner=""
    [[ -f "${LOCK}/owner" ]] && owner="$(cat "${LOCK}/owner")"
    echo "ERROR: the daily-sync lock is held (${owner:-no owner file}); a run is in progress." >&2
    echo "       Nothing was touched. Retry once it finishes." >&2
    exit 3
  fi
  LOCK_HELD=true
  printf 'pid=%s repo=%s started=%s host=%s\n' \
    "$$" "sced-schedule" "$(date +%s)" "$(hostname)" > "${LOCK}/owner"
}

json_get() { # $1 = file, $2.. = keys
  local f="$1"; shift
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2:]:
    d = d[k] if isinstance(d, dict) else d[int(k)]
print("" if d is None else d)' "${f}" "$@"
}

DERIVED="${TMP}/derived.json"
py derive --json > "${DERIVED}" || exit $?
REPOS="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))))' "${DERIVED}")"

dget() { json_get "${DERIVED}" "$1" "$2"; }

plist_path() { echo "${LAUNCH_AGENTS_DIR}/$(dget "$1" launchd_label).plist"; }

repo_field() { # $1 = repo, $2 = json path under repos.<repo>
  python3 -c '
import json, sys
print(json.load(open(sys.argv[1]))["repos"][sys.argv[2]].get(sys.argv[3], ""))' \
    "${CONFIG}" "$1" "$2"
}

# ------------------------------------------------------------------ observation

collect_raw() {
  local repo label pp ref wf
  for repo in ${REPOS}; do
    label="$(dget "${repo}" launchd_label)"
    pp="$(plist_path "${repo}")"
    if [[ -r "${pp}" ]]; then
      cp "${pp}" "${RAW}/plist-${repo}.xml"
      plutil -convert json -o "${RAW}/plist-${repo}.json" -- "${pp}" 2>/dev/null \
        || rm -f "${RAW}/plist-${repo}.json"
    fi

    if [[ "${NO_LAUNCHD_PROBE}" != true ]] && command -v launchctl >/dev/null 2>&1; then
      launchctl print "gui/${UID_NUM}/${label}" > "${RAW}/launchctl-${repo}.txt" 2>/dev/null \
        || rm -f "${RAW}/launchctl-${repo}.txt"
    fi

    ref="${CI_REF:-$(repo_field "${repo}" ci_ref)}"
    wf="$(repo_field "${repo}" workflow_path)"
    if [[ -d "${REPO_ROOT}/${repo}/.git" || -f "${REPO_ROOT}/${repo}/.git" ]]; then
      if [[ "${FETCH}" == true ]]; then
        git -C "${REPO_ROOT}/${repo}" fetch --quiet --tags --force origin 2>/dev/null \
          || echo "WARNING: fetch failed in ${repo}; the CI cron is compared against stale refs" >&2
      fi
      git -C "${REPO_ROOT}/${repo}" show "${ref}:${wf}" > "${RAW}/workflow-${repo}.yml" 2>/dev/null \
        || rm -f "${RAW}/workflow-${repo}.yml"
    fi

    if [[ "${GH}" == true ]] && command -v gh >/dev/null 2>&1; then
      GH_REPO="$(repo_field "${repo}" gh_repo)" WF="${wf}" \
      gh api "repos/$(repo_field "${repo}" gh_repo)/actions/workflows" \
        --jq "{state: (.workflows[]|select(.path==\"${wf}\")|.state)}" \
        > "${RAW}/gh-state-${repo}.json" 2>/dev/null || echo '{}' > "${RAW}/gh-state-${repo}.json"
      gh repo view "$(repo_field "${repo}" gh_repo)" \
        --json defaultBranchRef --jq '{default_branch: .defaultBranchRef.name}' \
        > "${RAW}/gh-branch-${repo}.json" 2>/dev/null || echo '{}' > "${RAW}/gh-branch-${repo}.json"
      python3 -c '
import json, sys
out = {}
for p in sys.argv[1:3]:
    try:
        out.update(json.load(open(p)))
    except Exception:
        pass
print(json.dumps(out))' "${RAW}/gh-state-${repo}.json" "${RAW}/gh-branch-${repo}.json" \
        > "${RAW}/gh-${repo}.json"
    fi
  done

  if [[ -r "${DRIVER}" ]]; then
    # Anchored on the case arms themselves, not on a line range: a hard-coded
    # window silently loses an arm the moment anything above it grows (adding
    # exit code 21 to the header on 2026-08-01 pushed SCED-downloads out of
    # `148,160p`, and the site degraded to "unknown" instead of failing loudly).
    # `|| true` for the same reason as driver-stop.txt below.
    grep -E '^[[:space:]]*[A-Za-z][A-Za-z-]*\)[[:space:]]*SCHED_HHMM=' \
      "${DRIVER}" > "${RAW}/driver-case.txt" 2>/dev/null || true
    [[ -s "${RAW}/driver-case.txt" ]] || rm -f "${RAW}/driver-case.txt"
    # `|| true`: a no-match grep exits 1, which under `set -e` would end the run
    # here -- and a driver that no longer quotes the CI times is a finding to
    # report, not a reason to abort the whole report.
    grep -n 'echo 03:47 || echo 03:17' "${DRIVER}" > "${RAW}/driver-stop.txt" 2>/dev/null || true
    [[ -s "${RAW}/driver-stop.txt" ]] || rm -f "${RAW}/driver-stop.txt"
  fi

  # Key names ONLY. The values in this file are secrets and are never read,
  # never logged, and never leave this line.
  if [[ -r "${ENV_FILE}" ]]; then
    grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "${ENV_FILE}" > "${RAW}/env-keys.txt" || true
    # The ONE exception, and it is deliberately narrow: SCED_SYNC_AI_TIMEOUT is
    # the inner `claude` wall clock in seconds, so it is a schedule number that
    # happens to live in the secrets file, and leaving it unvalidated is what
    # made reserve.ai_claude_min dead config in the first place. The pattern
    # accepts digits only -- a value that is not a bare integer is not extracted
    # at all, so nothing that could be a secret can be captured by a typo'd key.
    grep -oE '^SCED_SYNC_AI_TIMEOUT=[0-9]+$' "${ENV_FILE}" 2>/dev/null \
      | cut -d= -f2 > "${RAW}/env-ai-timeout.txt" || true
    [[ -s "${RAW}/env-ai-timeout.txt" ]] || rm -f "${RAW}/env-ai-timeout.txt"
  fi

  [[ -r "${DOCS}" ]] && cp "${DOCS}" "${RAW}/docs.md"
  return 0
}

build_meta() {
  local probe_launchd="true"
  [[ "${NO_LAUNCHD_PROBE}" == true ]] && probe_launchd="false"
  local paths="" repo
  for repo in ${REPOS}; do
    paths="${paths}${paths:+,}\"${repo}\": \"$(plist_path "${repo}")\""
  done
  cat > "${TMP}/meta.json" <<EOF
{
  "uid": "${UID_NUM}",
  "env_file": "${ENV_FILE}",
  "docs_path": "${DOCS}",
  "fetched_at": "$( [[ "${FETCH}" == true ]] && date +%Y-%m-%dT%H:%M:%S%z || echo offline )",
  "probes": { "launchd": ${probe_launchd}, "fetch": ${FETCH}, "gh": ${GH} },
  "plist_paths": { ${paths} }
}
EOF
}

observe() {
  collect_raw
  build_meta
  py collect --raw-dir "${RAW}" --meta "${TMP}/meta.json" --out "${TMP}/obs.json"
}

report() { # $1 = "show" | "verify"
  local rc=0
  # Captured before the `set --` below, which rewrites this function's own $1.
  local mode="$1"
  observe
  # `|| rc=$?` rather than toggling `set -e`: a function that flips the global
  # shell flag silently changes how its own caller behaves after it returns.
  set -- compare --observations "${TMP}/obs.json"
  [[ -n "${SITES}" ]] && set -- "$@" --sites "${SITES}"
  [[ "${STRICT}" == true ]] && set -- --strict "$@"
  py "$@" > "${TMP}/report.json" || rc=$?
  if [[ ${rc} -ne 0 && ${rc} -ne 10 ]]; then
    cat "${TMP}/report.json" >&2
    return ${rc}
  fi

  if [[ "${JSON}" == true ]]; then
    cat "${TMP}/report.json"
  else
    MODE="${mode}" python3 - "${TMP}/report.json" <<'PY'
import json, os, sys
doc = json.load(open(sys.argv[1]))
mode = os.environ.get("MODE", "verify")

print(f"Config:  {doc['config_path']}  (updated {doc.get('config_updated_at')})")
print()
print(f"  {'repo':<16} {'local':>6} {'window':>8} {'CI (KST)':>9}  cron (UTC)")
for repo, d in doc["repos"].items():
    print(f"  {repo:<16} {d['local']:>6} {'->' + d['latest']:>8} {d['ci_kst']:>9}  {d['cron_utc']}")

bad = [f for f in doc["invariants"] if f["status"] in ("fail", "warn", "ack")]
if bad:
    print()
    print("Invariants:")
    for f in bad:
        print(f"  [{f['status'].upper():<4}] {f['id']:<16} {f['detail']}")

print()
print("Sites:")
mark = {"ok": "ok  ", "drift": "DRIFT", "unknown": "?????", "advisory": "note"}
for s in doc["sites"]:
    if mode == "show" or s["status"] != "ok":
        who = f"{s['repo']}:" if s.get("repo") else ""
        cls = f" ({s['class']})" if s.get("class") else ""
        print(f"  [{mark.get(s['status'], '?'):<5}] {s['id']:<3} {s['kind']:<18} "
              f"{who}{s['target']}{cls}")
        if s["status"] != "ok":
            print(f"          want {s['expected']!r}, have {s['actual']!r}")
            if s.get("detail"):
                print(f"          {s['detail']}")
            if s.get("patch"):
                print(f"          patch: {s['patch']}")

c = doc["counts"]
print()
print(f"{c.get('ok', 0)} ok, {c.get('drift', 0)} drift, {c.get('unknown', 0)} unknown, "
      f"{c.get('advisory', 0)} advisory")
if doc["exit"] == 0:
    print("OK: every managed site matches the source of truth.")
else:
    print("DRIFT: run `sced-schedule.sh apply` (add --ci / --docs for those tiers).")
PY
  fi
  return ${rc}
}

# ----------------------------------------------------------------- apply: local

# The dangerous window is bootout -> bootstrap: between them the agent is
# unloaded. It is narrowed to milliseconds and made near-impossible to enter with
# a bad file, because the plist is linted AND semantically asserted BEFORE the
# swap, and the swap itself is an atomic same-directory rename.
apply_local_one() { # $1 = repo
  local repo="$1"
  local label pp tmp bak bak_boot stamp was_loaded=false
  label="$(dget "${repo}" launchd_label)"
  pp="$(plist_path "${repo}")"
  tmp="${LAUNCH_AGENTS_DIR}/.${label}.plist.tmp.$$"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"

  if ! py render-plist --repo "${repo}" > "${tmp}"; then
    rm -f "${tmp}"; echo "ERROR: render failed for ${repo}" >&2; return 50
  fi
  if ! plutil -lint "${tmp}" >/dev/null; then
    rm -f "${tmp}"; echo "ERROR: rendered plist for ${repo} is not a valid plist" >&2; return 50
  fi
  if ! plutil -convert json -o - -- "${tmp}" | py assert-plist --repo "${repo}" >/dev/null; then
    plutil -convert json -o - -- "${tmp}" | py assert-plist --repo "${repo}" >/dev/null || true
    rm -f "${tmp}"
    echo "ERROR: rendered plist for ${repo} failed its semantic assertions" >&2
    return 50
  fi
  chmod 644 "${tmp}"

  if [[ "${DRY_RUN}" == true || "${CHECK}" == true ]]; then
    if [[ -r "${pp}" ]] && diff -q "${pp}" "${tmp}" >/dev/null; then
      echo "  ${repo}: no change"
      rm -f "${tmp}"
      return 0
    fi
    echo "  ${repo}: would change ${pp}"
    diff -u "${pp}" "${tmp}" 2>/dev/null | sed 's/^/    /' || true
    rm -f "${tmp}"
    return 10
  fi

  bak="${STATE_ROOT}/backup/launchagents/${label}.${stamp}.plist"
  bak_boot="${LAUNCH_AGENTS_DIR}/.sced-schedule-backup/${label}.${stamp}.plist"
  if [[ -r "${pp}" ]]; then
    mkdir -p "$(dirname "${bak}")" "$(dirname "${bak_boot}")"
    # The boot-volume copy is what `rollback --local` uses when /Volumes/PRO-G40
    # is unmounted. launchd does not recurse into subdirectories, so a copy in
    # .sced-schedule-backup/ is never bootstrapped.
    cp "${pp}" "${bak}" && cp "${pp}" "${bak_boot}"
    prune_backups "${label}"
  fi

  # `|| true`: a not-loaded agent makes `launchctl print` exit non-zero, which is
  # a legitimate state (first install), not a failure of this run.
  if command -v launchctl >/dev/null 2>&1; then
    launchctl print "gui/${UID_NUM}/${label}" >/dev/null 2>&1 && was_loaded=true || true
  fi

  mv "${tmp}" "${pp}"
  echo "  ${repo}: wrote ${pp}"

  if [[ "${NO_RELOAD}" == true ]]; then
    echo "  ${repo}: --no-reload, launchd still holds the PREVIOUS schedule"
    return 0
  fi

  if [[ "${was_loaded}" == true ]]; then
    launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
    local i=0
    while [[ ${i} -lt 10 ]] && launchctl print "gui/${UID_NUM}/${label}" >/dev/null 2>&1; do
      sleep 0.5; i=$((i + 1))
    done
  fi

  if ! launchctl bootstrap "gui/${UID_NUM}" "${pp}" 2>/dev/null; then
    sleep 1
    if ! launchctl bootstrap "gui/${UID_NUM}" "${pp}" 2>/dev/null; then
      if [[ -r "${bak_boot}" ]]; then
        cp "${bak_boot}" "${pp}"
        if launchctl bootstrap "gui/${UID_NUM}" "${pp}" 2>/dev/null; then
          echo "ERROR: ${label} would not load; the PREVIOUS schedule was restored." >&2
          return 51
        fi
      fi
      {
        echo "!!! AGENT NOT LOADED: ${label}"
        echo "!!! Run:  launchctl bootstrap gui/${UID_NUM} ${pp}"
        echo "!!! Then: ${BASH_SOURCE[0]} verify"
        echo "!!! Until then the GitHub Actions fallback still runs at $(dget "${repo}" ci_kst) KST."
      } >&2
      return 51
    fi
  fi

  # Confirm against launchd's own loaded state, not the file we just wrote.
  local out="${TMP}/confirm-${repo}.txt"
  if ! launchctl print "gui/${UID_NUM}/${label}" > "${out}" 2>/dev/null; then
    echo "ERROR: ${label} bootstrapped but cannot be printed" >&2
    return 51
  fi
  if ! CONFIRM_REPO="${repo}" CONFIRM_PP="${pp}" SCED_SCHEDULE_DIR="${SCRIPT_DIR}" \
       python3 - "${out}" "${DERIVED}" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["SCED_SCHEDULE_DIR"])
import sced_schedule as ss
lc = ss.parse_launchctl(open(sys.argv[1]).read())
d = json.load(open(sys.argv[2]))[os.environ["CONFIRM_REPO"]]
problems = []
if not lc["parsed"]:
    problems.append("launchctl print could not be parsed")
want_h, want_m = d["local_min"] // 60, d["local_min"] % 60
if (lc["hour"], lc["minute"]) != (want_h, want_m):
    problems.append(f"loaded trigger {lc['hour']}:{lc['minute']}, expected {want_h}:{want_m}")
if lc["env"].get(d["sched_env_key"]) != d["local_hhmm"]:
    problems.append(f"loaded {d['sched_env_key']}={lc['env'].get(d['sched_env_key'])}, "
                    f"expected {d['local_hhmm']}")
if lc["env"].get(d["latest_env_key"]) != d["latest_hhmm"]:
    problems.append(f"loaded {d['latest_env_key']}={lc['env'].get(d['latest_env_key'])}, "
                    f"expected {d['latest_hhmm']}")
if lc["path"] and lc["path"] != os.environ["CONFIRM_PP"]:
    problems.append(f"loaded from {lc['path']}, not {os.environ['CONFIRM_PP']}")
for p in problems:
    print(f"  - {p}", file=sys.stderr)
sys.exit(1 if problems else 0)
PY
  then
    echo "ERROR: ${label} reloaded but launchd's state does not match the config" >&2
    return 51
  fi
  echo "  ${repo}: reloaded, launchd confirms $(dget "${repo}" local) with the guard bound"
  return 0
}

prune_backups() { # $1 = label; keep 14, like the driver's log pruner
  local label="$1" d
  for d in "${STATE_ROOT}/backup/launchagents" "${LAUNCH_AGENTS_DIR}/.sced-schedule-backup"; do
    [[ -d "${d}" ]] || continue
    # BSD head has no negative -n, so awk drops all but the newest 14. The names
    # carry a sortable UTC stamp, so byte order is chronological order.
    # shellcheck disable=SC2012
    ls -1 "${d}/${label}."*.plist 2>/dev/null | LC_ALL=C sort \
      | awk '{a[NR]=$0} END {for (i = 1; i <= NR - 14; i++) print a[i]}' \
      | while read -r old; do rm -f "${old}"; done
  done
}

# `launchctl bootout` SIGTERMs a running job, and this tool also takes the
# workspace lock the driver uses -- so applying inside the nightly window would
# either kill a run or be blocked by it. Refuse without an explicit --yes.
assert_not_in_run_band() {
  local band
  band="$(python3 -c '
import json, sys, time
d = json.load(open(sys.argv[1]))
lo = min(r["local_min"] for r in d.values()) - 10
hi = max(r["latest_min"] for r in d.values()) + 30
now = time.localtime()
cur = now.tm_hour * 60 + now.tm_min
print("in" if lo <= cur <= hi else "out",
      f"{lo // 60:02d}:{lo % 60:02d}", f"{hi // 60:02d}:{hi % 60:02d}")' "${DERIVED}")"
  local state lo hi
  state="${band%% *}"; lo="$(echo "${band}" | cut -d' ' -f2)"; hi="$(echo "${band}" | cut -d' ' -f3)"
  if [[ "${state}" == "in" && "${YES}" != true ]]; then
    echo "ERROR: it is $(date +%H:%M), inside the nightly run band ${lo}-${hi}." >&2
    echo "       Reloading now would SIGTERM a run in progress. Re-run with --yes to override," >&2
    echo "       or wait until after ${hi}. Nothing was touched." >&2
    exit 1
  fi
  [[ "${state}" == "in" ]] && echo "  WARNING: inside the run band ${lo}-${hi}; --yes given, proceeding" >&2
  return 0
}

apply_local() {
  local rc=0 repo r
  echo "apply --local:"
  if [[ "${DRY_RUN}" != true && "${CHECK}" != true ]]; then
    assert_not_in_run_band
    take_lock
  fi
  for repo in ${REPOS}; do
    r=0
    apply_local_one "${repo}" || r=$?
    if [[ ${r} -ne 0 && ${r} -ne 10 ]]; then
      echo "ERROR: stopping before the remaining agents so at most one is affected." >&2
      return ${r}
    fi
    [[ ${r} -eq 10 ]] && rc=10
  done
  return ${rc}
}

# -------------------------------------------------------------------- apply: ci

apply_ci_one() { # $1 = repo
  local repo="$1" ref wf gh_repo path wt obs cron tmpf
  ref="${CI_REF:-$(repo_field "${repo}" ci_ref)}"
  wf="$(repo_field "${repo}" workflow_path)"
  gh_repo="$(repo_field "${repo}" gh_repo)"
  path="${REPO_ROOT}/${repo}"
  cron="$(dget "${repo}" cron_utc)"

  if [[ "${DRY_RUN}" == true || "${CHECK}" == true || "${YES}" != true ]]; then
    git -C "${path}" show "${ref}:${wf}" > "${TMP}/wf-${repo}.yml" 2>/dev/null || {
      echo "  ${repo}: cannot read ${ref}:${wf}" >&2; return 60; }
    if ! py patch-workflow --repo "${repo}" --in "${TMP}/wf-${repo}.yml" \
         > "${TMP}/wf-${repo}.new.yml"; then
      return 60
    fi
    if diff -q "${TMP}/wf-${repo}.yml" "${TMP}/wf-${repo}.new.yml" >/dev/null; then
      echo "  ${repo}: no change (${cron})"
      return 0
    fi
    echo "  ${repo}: would commit and push to ${gh_repo} ${ref}"
    diff -u "${TMP}/wf-${repo}.yml" "${TMP}/wf-${repo}.new.yml" | sed 's/^/    /' || true
    [[ "${YES}" != true ]] && echo "    (add --yes to perform this push)"
    return 10
  fi

  # A workflow file that is not on the DEFAULT branch is not registered at all:
  # `gh workflow list` omits it and the cron never fires, silently.
  if command -v gh >/dev/null 2>&1; then
    local db
    db="$(gh repo view "${gh_repo}" --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || echo "")"
    if [[ "${db}" != "korean" ]]; then
      echo "ERROR: ${gh_repo}'s default branch is '${db:-unknown}', not 'korean'." >&2
      echo "       A scheduled workflow only runs from the default branch; pushing here" >&2
      echo "       would silently never fire. Refusing." >&2
      return 61
    fi
  else
    echo "WARNING: gh is not available; the default-branch precondition was NOT checked" >&2
  fi

  git -C "${path}" fetch --quiet --prune --tags --force origin || {
    echo "ERROR: fetch failed in ${repo}" >&2; return 60; }
  obs="$(git -C "${path}" rev-parse origin/korean)" || return 60

  wt="${STATE_ROOT}/scratch/sched-ci-${repo}"
  case "${wt}" in
    "${STATE_ROOT}/scratch/"*) ;;
    *) echo "ERROR: refusing a scratch path outside ${STATE_ROOT}/scratch" >&2; return 60 ;;
  esac
  git -C "${path}" worktree prune
  [[ -e "${wt}" ]] && { git -C "${path}" worktree remove --force "${wt}" 2>/dev/null || rm -rf "${wt}"; }
  git -C "${path}" worktree add --detach "${wt}" "${obs}" >/dev/null || return 60

  tmpf="${TMP}/wf-${repo}.patched"
  local rep="${TMP}/wf-${repo}.report.json"
  if ! py patch-workflow --repo "${repo}" --in "${wt}/${wf}" --report "${rep}" > "${tmpf}"; then
    git -C "${path}" worktree remove --force "${wt}" 2>/dev/null || true
    return 60
  fi
  if diff -q "${wt}/${wf}" "${tmpf}" >/dev/null; then
    echo "  ${repo}: no change (${cron})"
    git -C "${path}" worktree remove --force "${wt}" 2>/dev/null || true
    return 0
  fi
  mv "${tmpf}" "${wt}/${wf}"

  local dirty
  dirty="$(git -C "${wt}" status --porcelain)"
  if [[ "${dirty}" != " M ${wf}" && "${dirty}" != "M  ${wf}" ]]; then
    echo "ERROR: unexpected working-tree state in ${wt}:" >&2
    echo "${dirty}" >&2
    git -C "${path}" worktree remove --force "${wt}" 2>/dev/null || true
    return 60
  fi

  # Describe what actually changed. The subject used to assert a cron move
  # unconditionally, which was false whenever only the header was regenerated.
  local subject cron_ch hdr_ch
  cron_ch="$(json_get "${rep}" cron_changed)"
  hdr_ch="$(json_get "${rep}" header_changed)"
  if [[ "${cron_ch}" == "True" && "${hdr_ch}" == "True" ]]; then
    subject="ci(sync): move the cron to ${cron} UTC ($(dget "${repo}" ci_kst) KST) and correct the header"
  elif [[ "${cron_ch}" == "True" ]]; then
    subject="ci(sync): move the daily-upstream-sync cron to ${cron} UTC ($(dget "${repo}" ci_kst) KST)"
  else
    subject="ci(sync): state the branch this workflow must live on"
  fi

  git -C "${wt}" -c core.hooksPath=/dev/null commit -q -m \
    "${subject}

Rendered by SCED-tools/scripts/sced-schedule.sh from config/sync-schedule.json." \
    -- "${wf}" || { git -C "${path}" worktree remove --force "${wt}" 2>/dev/null || true; return 60; }

  if [[ "${NO_PUSH}" == true ]]; then
    echo "  ${repo}: committed in ${wt}, --no-push so nothing was pushed"
    return 0
  fi

  # A fast-forward, but leased: a concurrent nightly force-push makes this a
  # clean refusal instead of a silent overwrite.
  if ! git -C "${wt}" push --force-with-lease="refs/heads/korean:${obs}" \
       origin HEAD:refs/heads/korean; then
    echo "ERROR: push rejected for ${repo} (lease conflict or permissions)" >&2
    git -C "${path}" worktree remove --force "${wt}" 2>/dev/null || true
    return 60
  fi
  git -C "${path}" worktree remove --force "${wt}" 2>/dev/null || true
  echo "  ${repo}: pushed ${cron} to ${gh_repo} korean"

  if command -v gh >/dev/null 2>&1; then
    local state
    state="$(gh api "repos/${gh_repo}/actions/workflows" \
      --jq ".workflows[]|select(.path==\"${wf}\")|.state" 2>/dev/null || echo "")"
    case "${state}" in
      active) echo "  ${repo}: workflow is registered and active" ;;
      "")     echo "  ${repo}: WARNING - the workflow is not listed yet (may take a moment)" >&2 ;;
      *)      echo "ERROR: ${repo}: workflow state is '${state}'" >&2; return 61 ;;
    esac
  fi

  echo "  ${repo}: NOTE - this commit moves korean off its +korean.N tag, so tomorrow's"
  echo "         no-op guard sees an untagged tip and performs a full rebase/build/release"
  echo "         even if upstream has not moved. Expected, not a fault."
  return 0
}

apply_ci() {
  local rc=0 repo r
  echo "apply --ci:"
  if [[ "${YES}" != true && "${DRY_RUN}" != true && "${CHECK}" != true ]]; then
    echo "  (plan only -- --ci writes to two PUBLIC forks' default branch; add --yes)"
  fi
  [[ "${YES}" == true && "${DRY_RUN}" != true && "${CHECK}" != true ]] && take_lock
  for repo in ${REPOS}; do
    r=0
    apply_ci_one "${repo}" || r=$?
    if [[ ${r} -ne 0 && ${r} -ne 10 ]]; then return ${r}; fi
    [[ ${r} -eq 10 ]] && rc=10
  done
  return ${rc}
}

# ------------------------------------------------------------------ apply: docs

apply_docs() {
  echo "apply --docs:"
  if [[ ! -r "${DOCS}" ]]; then
    echo "ERROR: ${DOCS} is not readable" >&2
    return 70
  fi
  local out="${TMP}/docs.new.md"
  if ! py patch-doc --in "${DOCS}" > "${out}"; then
    return 70
  fi
  if diff -q "${DOCS}" "${out}" >/dev/null; then
    echo "  no change"
    return 0
  fi
  if [[ "${DRY_RUN}" == true || "${CHECK}" == true ]]; then
    echo "  would change ${DOCS}"
    diff -u "${DOCS}" "${out}" | sed 's/^/    /' || true
    return 10
  fi
  # CLAUDE.md sits at the workspace root, which is NOT a git repository, so a
  # bad rewrite has no `git checkout` undo -- only this copy.
  local bak="${STATE_ROOT}/backup/docs/CLAUDE.$(date -u +%Y%m%dT%H%M%SZ).md"
  mkdir -p "$(dirname "${bak}")"
  cp "${DOCS}" "${bak}"
  cp "${out}" "${DOCS}"
  echo "  rewrote the schedule table in ${DOCS} (backup: ${bak})"
  echo "  NOTE: the surrounding prose is never rewritten; \`verify\` reports it separately."
  return 0
}

# --------------------------------------------------------------------- rollback

rollback_local() {
  local dirs="${STATE_ROOT}/backup/launchagents ${LAUNCH_AGENTS_DIR}/.sced-schedule-backup"
  local repo label pp cand d
  for repo in ${REPOS}; do
    label="$(dget "${repo}" launchd_label)"
    [[ -n "${RB_LABEL}" && "${RB_LABEL}" != "${label}" ]] && continue
    pp="$(plist_path "${repo}")"
    cand=""
    for d in ${dirs}; do
      [[ -d "${d}" ]] || continue
      if [[ -n "${RB_STAMP}" ]]; then
        [[ -r "${d}/${label}.${RB_STAMP}.plist" ]] && cand="${d}/${label}.${RB_STAMP}.plist"
      else
        # shellcheck disable=SC2012
        cand="$(ls -1 "${d}/${label}."*.plist 2>/dev/null | sort | tail -1)"
      fi
      [[ -n "${cand}" ]] && break
    done
    if [[ -z "${cand}" ]]; then
      echo "  ${repo}: no backup found" >&2
      continue
    fi
    if [[ "${DRY_RUN}" == true ]]; then
      echo "  ${repo}: would restore ${cand}"
      diff -u "${pp}" "${cand}" 2>/dev/null | sed 's/^/    /' || true
      continue
    fi
    if ! plutil -lint "${cand}" >/dev/null; then
      echo "ERROR: ${cand} is not a valid plist; refusing to restore it" >&2
      return 50
    fi
    cp "${cand}" "${pp}"
    launchctl bootout "gui/${UID_NUM}/${label}" 2>/dev/null || true
    if ! launchctl bootstrap "gui/${UID_NUM}" "${pp}" 2>/dev/null; then
      echo "!!! AGENT NOT LOADED: ${label} -- run: launchctl bootstrap gui/${UID_NUM} ${pp}" >&2
      return 51
    fi
    echo "  ${repo}: restored ${cand} and reloaded"
  done
  return 0
}

# ------------------------------------------------------------------------- main

case "${VERB}" in
  show)
    # `show` is a report verb and never carries the 0/10 contract: scripts must
    # depend on `verify` for that, not accidentally on this.
    rc=0
    report show || rc=$?
    [[ ${rc} -eq 10 ]] && rc=0
    exit ${rc}
    ;;

  verify)
    assert_no_env_shadow
    rc=0
    report verify || rc=$?
    exit ${rc}
    ;;

  set)
    if [[ -z "${SET_REPO}" ]]; then
      echo "ERROR: set requires --repo" >&2
      exit 1
    fi
    # Built element by element rather than as one string: --ack-collision takes
    # free text and word-splitting an unquoted expansion would mangle it.
    set -- set --repo "${SET_REPO}" \
           --updated-at "$(date +%Y-%m-%dT%H:%M:%S%z)" --updated-by "sced-schedule.sh"
    [[ -n "${SET_AT}" ]]        && set -- "$@" --at "${SET_AT}"
    [[ -n "${SET_LATEST}" ]]    && set -- "$@" --latest "${SET_LATEST}"
    [[ -n "${SET_CI_OFFSET}" ]] && set -- "$@" --ci-offset "${SET_CI_OFFSET}"
    [[ -n "${SET_DAYS}" ]]      && set -- "$@" --days "${SET_DAYS}"
    [[ -n "${SET_ACK}" ]]       && set -- "$@" --ack-collision "${SET_ACK}"
    [[ "${DRY_RUN}" == true ]]  && set -- "$@" --dry-run
    [[ "${STRICT}" == true ]]   && set -- --strict "$@"
    rc=0
    py "$@" || rc=$?
    exit ${rc}
    ;;

  apply)
    assert_no_env_shadow
    rc=0
    if [[ "${DO_LOCAL}" == true ]]; then
      r=0; apply_local || r=$?
      [[ ${r} -eq 10 ]] && rc=10
      [[ ${r} -ne 0 && ${r} -ne 10 ]] && exit ${r}
    fi
    if [[ "${DO_CI}" == true ]]; then
      r=0; apply_ci || r=$?
      [[ ${r} -eq 10 ]] && rc=10
      [[ ${r} -ne 0 && ${r} -ne 10 ]] && exit ${r}
    fi
    if [[ "${DO_DOCS}" == true ]]; then
      r=0; apply_docs || r=$?
      [[ ${r} -eq 10 ]] && rc=10
      [[ ${r} -ne 0 && ${r} -ne 10 ]] && exit ${r}
    fi
    if [[ "${CHECK}" == true || "${DRY_RUN}" == true ]]; then
      exit ${rc}
    fi
    exit 0
    ;;

  rollback)
    if [[ "${DO_LOCAL}" != true ]]; then
      echo "ERROR: rollback requires --local (the CI tier rolls back with a normal commit)" >&2
      exit 1
    fi
    echo "rollback --local:"
    rc=0; rollback_local || rc=$?
    exit ${rc}
    ;;
esac
