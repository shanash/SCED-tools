#!/bin/bash
#
# arknights -- the operator dispatcher (design section 4.1), in the shape of
# koreanize.sh: it assembles argv and evaluates the DAG, owns no state a stage
# owns, and re-implements no stage.
#
# WHAT THIS SCRIPT IS THE ONLY OWNER OF
#
#   * the predecessor gate, and therefore exit 88
#   * the closed --accept-* flag family, and therefore part of 87
#   * the --live blast-radius banner and 89
#   * which INTERPRETER every stage runs under -- one tier, and it is PINNED
#
# ITS EXIT BAND IS {87,88,89} AND IS DISJOINT FROM EVERY OTHER TABLE IN THIS
# WORKSPACE -- from the stage codes it passes through verbatim, from koreanize's
# {71,72,73,75}, from sced-run-now.sh's {7,8,9} and from daily-sync-local.sh's.
# Any other code you see from this script is the stage's own.
#
# Usage:
#   arknights.sh --status [--run ID]        read-only; run this FIRST, always
#   arknights.sh plan [--run ID]            the DAG and what is stale
#   arknights.sh scan [--source PATH]       mint a run; walk docs/Arknights/
#   arknights.sh renumber [--run ID]        assign the akn namespace
#   arknights.sh repair [--run ID]          emit the patch set (applies nothing)
#   arknights.sh assemble [--run ID]        build the bag into <run_dir>/
#   arknights.sh verify [--run ID] [--probe-urls N] [--accept-declared-remainder]
#   arknights.sh publish [--run ID]         REHEARSAL -- writes nothing outside <run_dir>
#   arknights.sh publish --live [--yes]     the two SCED-downloads writes
#   arknights.sh publish --restore [--yes]  put both files back from the snapshots
#   arknights.sh selftest                   every module --selftest + the gates
#   arknights.sh --help
#
# Options:
#   --run ID       run directory SCED-tools/scripts/output/arknights/<ID>.
#                  Defaults to the newest; `scan` mints a fresh stamp
#   --source PATH  scan only. Must still resolve inside docs/Arknights/
#   --live         publish only. REHEARSAL IS THE DEFAULT. Prints a blast-radius
#                  banner and requires typing LIVE
#   --restore      publish only. Restore both targets from <run_dir>/publish/pre/
#   --json         machine-readable report on stdout instead of the human table
#   --yes          skip a typed confirmation; never skips a plan binding
#
# Exit codes:
#    0  the stage completed and every check passed
#    1  unhandled exception -- a defect in the tool
#    2  usage, or guard P0a (launchd) / P0b (interpreter outside the platform
#       set), or a mutating git verb reached the GIT_READONLY wrapper
#   80  guard refusal. NOTHING WAS WRITTEN
#   81  precondition -- a named input is missing, malformed, or not consumable
#   82  input drift -- docs/Arknights/ changed, or a config row names no object
#   83  renumber: a renumbering rule violation
#   84  repair: a defect with no config row, or a config row matching no object
#   85  assemble: an assembly rule violation
#   86  verify: a V-check failed, or an unaccepted declared remainder
#   87  THIS SCRIPT refused before any stage ran
#   88  THIS SCRIPT refused: the predecessor is not consumable, or is stale
#   89  THIS SCRIPT refused: --live declined at the banner
#
# 88 AND 81 ARE THE SAME FACT REPORTED BY TWO ACTORS, and the difference is what
# you do next. 88 is this script's: the table was evaluated before spawning
# anything and NO STAGE PROCESS EVER STARTED, so the move is `plan`. 81 is a
# stage's own: it ran far enough to read its input, so the move is the named
# file. Lifted deliberately from koreanize's 72/13 -- an operator who has learned
# that distinction once should not have to learn it twice.
#
# NO CI RUNS ANY OF THIS, and none should be added. SCED-tools/ has no .github/
# directory. This tool's corpus is docs/Arknights/, which is gitignored and
# exists in exactly one copy on this volume, and its collision oracles (V3, V3b,
# V11) are sibling checkouts a SCED-tools runner would not have. A hosted runner
# would skip every corpus-level check and report green on a suite whose four
# most load-bearing assertions it had not performed -- worse than no CI, because
# it reads as coverage. `arknights.sh selftest` is the compensating control.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$(cd "${HERE}/../.." && pwd)"
WORKSPACE="$(cd "${TOOLS}/.." && pwd)"

# ONE interpreter tier, and it is a PLATFORM path. akn_common.check_p0b refuses
# anything else at exit 2, so overriding this only moves where the refusal lands.
PY="${ARKNIGHTS_PYTHON:-/usr/bin/python3}"

EXIT_REFUSED=87
EXIT_UPSTREAM=88
EXIT_LIVE_DECLINED=89

# stage -> module. A stage with no row here does not exist.
stage_module() {
  case "$1" in
    scan)     echo "akn_scan.py" ;;
    renumber) echo "akn_renumber.py" ;;
    repair)   echo "akn_repair.py" ;;
    assemble) echo "akn_assemble.py" ;;
    verify)   echo "akn_verify.py" ;;
    publish)  echo "akn_publish.py" ;;
    *)        return 1 ;;
  esac
}

die() { printf '%s\n' "$*" >&2; }

usage() { sed -n '/^# Usage:/,/^#   89 /p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# ---------------------------------------------------------------------------
# Import-time assertions the dispatcher requires of itself
# ---------------------------------------------------------------------------
#
# The stage list is asserted against the module's own table rather than restated,
# because a stage added to one and not the other is a stage `plan` shows and the
# dispatcher cannot spawn -- which reads as a dispatcher bug from the outside.
assert_contracts() {
  "${PY}" - "${HERE}" <<'PY' || return 1
import sys
sys.path.insert(0, sys.argv[1])
import akn_common as kc
import akn_config as kz
assert set(kz.PREDECESSORS) == set(kz.STAGES), "the predecessor table is not total"
assert kz.STAGES[0] == "scan" and kz.STAGES[-1] == "publish", \
    "the linear DAG's endpoints moved: %s" % (kz.STAGES,)
assert not (set(kc.EXIT_PRECEDENCE) & set(kc.DISPATCHER_BAND)), \
    "EXIT_PRECEDENCE overlaps the dispatcher band"
PY
}

registered_flags() {
  "${PY}" - "${HERE}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import akn_common as kc
print(" ".join(sorted(kc.all_flags())))
PY
}

stage_list() {
  "${PY}" - "${HERE}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import akn_config as kz
print(" ".join(kz.STAGES))
PY
}

# Stderr is CAPTURED, not discarded: akn_config.resolve_run_dir refuses a
# traversing --run at 87 naming the id and the stamp shape it failed, and
# dropping that left the operator with "needs a run directory. Run `scan`
# first." -- the same exit code pointing at the opposite first move. The
# resolver's own message wins whenever it produced one.
RUN_DIR_ERR=""

resolve_run_dir() {
  local err out rc
  err="$(mktemp)"
  out="$("${PY}" - "${HERE}" "${1:-}" 2>"${err}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import akn_common as kc
import akn_config as kz
try:
    cfg = kz.load_config()
    path = kz.resolve_run_dir(cfg, run_id=(sys.argv[2] or None))
except kc.AknRefusal as refusal:
    # AknRefusal.__str__ is already the operator-facing form; without this the
    # dispatcher showed a traceback where the module has a precise message.
    sys.stderr.write("%s\n" % refusal)
    sys.exit(refusal.code)
print(path or "")
PY
  )"
  rc=$?
  RUN_DIR_ERR="$(cat "${err}")"
  rm -f "${err}"
  if [[ ${rc} -ne 0 && -n "${RUN_DIR_ERR}" ]]; then
    die "${RUN_DIR_ERR}"
    return ${rc}
  fi
  printf '%s\n' "${out}"
}

# ---------------------------------------------------------------------------
# --status / plan
# ---------------------------------------------------------------------------

source_line() {
  # The source tree hash, and whether it has MOVED since scan recorded it. This
  # is V8's question asked cheaply and read-only, so an operator sees a drifted
  # source before spending a stage on it rather than at exit 82 afterwards.
  "${PY}" - "${HERE}" "${1:-}" <<'PY' 2>/dev/null || true
import json, os, sys
sys.path.insert(0, sys.argv[1])
import akn_common as kc
import akn_config as kz
cfg = kz.load_config()
root = kz.source_root(cfg)
run_dir = sys.argv[2]
recorded = None
tree_path = os.path.join(run_dir, "source.tree.json") if run_dir else None
if tree_path and os.path.exists(tree_path):
    recorded = json.loads(kc.read_text(tree_path))
if recorded is None:
    print("source  : %s -- not yet hashed (run `scan`)"
          % os.path.relpath(root, kc.WORKSPACE_ROOT))
    raise SystemExit(0)
digest, count, total = kc.sha256_tree(root)
moved = digest != recorded["sha256"]
print("source  : %s  %.1f MB  %d files  sha256 %s  %s"
      % (os.path.relpath(root, kc.WORKSPACE_ROOT), total / 1048576.0, count,
         digest[:12], "MOVED since scan -- re-run scan" if moved
         else "UNCHANGED since scan"))
PY
}

base_line() {
  # The nightly force-pushes `korean` every successful night. The rebase happens
  # in a detached scratch worktree, so uncommitted work is invisible to it and
  # survives untouched: what moves is the BASE, so a plan built against the old
  # one is merely STALE. Re-run the planning stage; do NOT revert.
  local head base
  head="$(git -C "${WORKSPACE}/SCED-downloads" rev-parse HEAD 2>/dev/null)" || return 0
  base="$(git -C "${WORKSPACE}/SCED-downloads" rev-parse origin/korean 2>/dev/null)" || return 0
  if [[ "${head}" != "${base}" ]]; then
    printf 'base    : origin/korean is %s, HEAD is %s -- the nightly moved the base under this run.\n' \
      "${base:0:12}" "${head:0:12}"
    printf '          Nothing is lost: re-run the planning stage. Do NOT revert.\n'
  else
    printf 'base    : origin/korean is %s, HEAD is %s -- unmoved\n' "${base:0:12}" "${head:0:12}"
  fi
}

do_status() {
  local run_dir="$1"
  printf 'arknights --status\n'
  "${PY}" "${HERE}/akn_config.py" --lock-status
  "${PY}" "${HERE}/akn_config.py" --window
  base_line
  source_line "${run_dir}"
  if [[ -z "${run_dir}" || ! -d "${run_dir}" ]]; then
    printf 'run     : no run directory (%s)\n' "${run_dir:-<none yet>}"
    printf 'next    : arknights.sh scan\n'
    return 0
  fi
  printf 'run     : %s\n' "${run_dir#${WORKSPACE}/}"
  local plan_out next
  plan_out="$("${PY}" "${HERE}/akn_config.py" --plan "${run_dir}")"
  printf '%s\n' "${plan_out}"
  next="$(printf '%s\n' "${plan_out}" | awk '/^next    :/ {print $3}')"
  if [[ -n "${next}" && "${next}" != "--" ]]; then
    printf 'argv    : arknights.sh %s --run %s\n' "${next}" "$(basename "${run_dir}")"
  fi
}

# ---------------------------------------------------------------------------
# The predecessor gate -- exit 88, taken BEFORE anything is spawned
# ---------------------------------------------------------------------------

check_predecessors() {
  local stage="$1" run_dir="$2"
  "${PY}" - "${HERE}" "${stage}" "${run_dir}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import akn_config as kz
stage, run_dir = sys.argv[2], sys.argv[3]
unmet = []
for pred in kz.PREDECESSORS.get(stage, ()):
    state = kz.stage_state(run_dir, pred)
    if not state["consumable"]:
        unmet.append("%s (%s)" % (pred, state["blocked_by"] or "not consumable"))
    elif state["stale"]:
        unmet.append("%s (stale: %s)" % (pred, ", ".join(state["stale_paths"][:4])))
if unmet:
    sys.stderr.write("exit 88: %s cannot run yet\n" % stage)
    for line in unmet:
        sys.stderr.write("  waiting on %s\n" % line)
    sys.stderr.write("  nothing was started. Run `arknights.sh plan` to see the DAG.\n")
    raise SystemExit(1)
PY
}

# ---------------------------------------------------------------------------
# selftest -- the compensating control for the absence of CI
# ---------------------------------------------------------------------------

do_selftest() {
  local failures=0 name

  printf '=== module --selftest (%s) ===\n' "${PY}"
  for name in akn_common akn_config akn_scan akn_renumber akn_repair \
              akn_assemble akn_verify akn_publish; do
    if [[ ! -f "${HERE}/${name}.py" ]]; then
      printf -- '--- %s  PENDING (not built yet)\n' "${name}"
      continue
    fi
    printf -- '--- %s\n' "${name}"
    "${PY}" "${HERE}/${name}.py" --selftest || failures=$((failures + 1))
  done

  printf '\n=== gates (%s) ===\n' "${HERE}/tests/test_arknights_gates.py"
  if [[ -f "${HERE}/tests/test_arknights_gates.py" ]]; then
    # Run through the file's OWN runner, not pytest: pytest is installed on the
    # Homebrew art tier only, and this package is pinned to the platform
    # interpreter. The file is importable by pytest too, for anyone who has it.
    "${PY}" "${HERE}/tests/test_arknights_gates.py" || failures=$((failures + 1))
  else
    printf 'PENDING (not built yet)\n'
  fi

  printf '\n'
  if [[ ${failures} -eq 0 ]]; then
    printf 'selftest: PASS -- every built module --selftest green, the gates green,\n'
    printf '          and any pending module is named above rather than skipped.\n'
  else
    printf 'selftest: FAIL (%d component(s))\n' "${failures}"
  fi
  return ${failures}
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

main() {
  if ! assert_contracts; then
    die "exit ${EXIT_REFUSED}: the dispatcher's own contract assertions failed"
    return ${EXIT_REFUSED}
  fi

  local stage="" run_id="" want_status="no"
  local -a passthrough=()

  if [[ $# -eq 0 ]]; then usage; return ${EXIT_REFUSED}; fi

  local registered
  registered=" $(registered_flags) "

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h) usage; return 0 ;;
      --status)  want_status="yes"; shift ;;
      --run)
        # `shift 2` with $# -lt 2 shifts NOTHING and returns non-zero, and there
        # is no `set -e` to catch it -- so a trailing `--run` looped forever with
        # no output. Refused rather than defaulted: an empty id resolves to the
        # latest run, which is not what an operator who named the flag meant.
        if [[ $# -lt 2 ]]; then
          die "exit ${EXIT_REFUSED}: --run needs a <YYYYMMDDTHHMMSSZ> run id"
          return ${EXIT_REFUSED}
        fi
        run_id="$2"; shift 2 ;;
      --accept-*)
        # A CLOSED family. An unregistered --accept-* is exit 87, refused before
        # any stage ran: a family that silently accepted anything would make the
        # TOLERANCES table's totality unenforceable from the one place an
        # operator actually types.
        if [[ "${registered}" != *" $1 "* ]]; then
          die "exit ${EXIT_REFUSED}: $1 is not a registered --accept-* flag"
          die "  registered:${registered}"
          die "  The family is a closed list (akn_common.TOLERANCES)."
          return ${EXIT_REFUSED}
        fi
        passthrough+=("$1"); shift ;;
      --*)       passthrough+=("$1"); shift ;;
      *)
        if [[ -z "${stage}" ]]; then stage="$1"; else passthrough+=("$1"); fi
        shift ;;
    esac
  done

  local run_dir
  local resolve_rc
  run_dir="$(resolve_run_dir "${run_id}")"
  resolve_rc=$?
  # The resolver already printed its own message. Stop here rather than fall
  # through to the generic "needs a run directory" one, which names the same exit
  # code and the opposite first move.
  if [[ ${resolve_rc} -ne 0 ]]; then return ${resolve_rc}; fi

  if [[ "${want_status}" == "yes" || "${stage}" == "status" ]]; then
    do_status "${run_dir}"; return 0
  fi

  case "${stage}" in
    plan)
      if [[ -z "${run_dir}" ]]; then
        die "exit ${EXIT_REFUSED}: no run directory yet. Run \`arknights.sh scan\`."
        return ${EXIT_REFUSED}
      fi
      base_line
      "${PY}" "${HERE}/akn_config.py" --plan "${run_dir}"
      return 0 ;;
    selftest) do_selftest; return $? ;;
  esac

  local module
  if ! module="$(stage_module "${stage}")"; then
    die "exit ${EXIT_REFUSED}: unknown stage ${stage:-<none>}"
    die "  stages: $(stage_list)"
    return ${EXIT_REFUSED}
  fi
  if [[ ! -f "${HERE}/${module}" ]]; then
    die "exit ${EXIT_REFUSED}: ${stage} is owned by ${module}, which is not on disk"
    die "  Every stage in the DAG has a module; one missing means the package is"
    die "  incomplete, not that the stage is unbuilt. \`arknights.sh selftest\` names it."
    return ${EXIT_REFUSED}
  fi

  if [[ "${stage}" != "scan" && -z "${run_dir}" ]]; then
    die "exit ${EXIT_REFUSED}: ${stage} needs a run directory. Run \`arknights.sh scan\` first."
    return ${EXIT_REFUSED}
  fi

  # The predecessor gate, evaluated BEFORE anything is spawned -- which is the
  # whole difference between 88 and a stage's own 81. `publish --restore` is
  # exempt on purpose: it is the recovery path, and gating a recovery on the
  # chain still being fresh makes it unreachable exactly when it is needed.
  local restoring="no"
  for arg in "${passthrough[@]+"${passthrough[@]}"}"; do
    [[ "${arg}" == "--restore" ]] && restoring="yes"
  done
  if [[ "${stage}" != "scan" && "${restoring}" == "no" \
        && -n "${run_dir}" && -d "${run_dir}" ]]; then
    if ! check_predecessors "${stage}" "${run_dir}"; then
      return ${EXIT_UPSTREAM}
    fi
  fi

  local -a argv=()
  [[ -n "${run_dir}" && "${stage}" != "scan" ]] && argv+=(--run-dir "${run_dir}")
  [[ -n "${run_id}" && "${stage}" == "scan" ]] && argv+=(--run-id "${run_id}")
  argv+=("${passthrough[@]+"${passthrough[@]}"}")

  "${PY}" "${HERE}/${module}" "${argv[@]+"${argv[@]}"}"
}

main "$@"
