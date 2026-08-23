#!/bin/bash
#
# koreanize -- the operator dispatcher (design §4.1), in the shape of
# sced-run-now.sh: it assembles argv and evaluates the DAG, and it owns no state
# any stage owns and re-implements no stage.
#
# WHAT THIS SCRIPT IS THE ONLY OWNER OF
#
#   * §1.2's predecessor gate, and therefore exit 72
#   * the --live blast-radius banner, its persisted <stage>.plan.json, and 73
#   * the closed --accept-* / --consent-* flag families, and 71
#   * which INTERPRETER each stage runs under (§5.9)
#   * `golden`, which is a COMMAND that drives five stages and not a stage
#     with a report of its own (§5.8), and therefore 75
#
# ITS EXIT BAND IS {71,72,73,75} AND IS DISJOINT FROM EVERY OTHER TABLE IN THIS
# WORKSPACE -- from the stage codes it passes through verbatim, from
# sced-run-now.sh's {7,8,9}, and from daily-sync-local.sh's. Any other code you
# see from this script is the stage's own.
#
#   71  refused before any stage ran (unknown stage, missing --slug, an
#       unregistered --accept-* / --consent-* flag)
#   72  the requested stage's upstream dependency is not consumable, or is
#       stale. `revert` can NEVER return it -- it is exempt from the
#       predecessor table, because its predecessor is by construction the
#       stage that just failed, and a `revert` with nothing to invert is the
#       module's own exit 13 naming the snapshot directory instead
#   73  --live declined at the banner
#   75  `golden` skipped on an interpreter mismatch
#
# 72 AND 13 ARE THE SAME FACT REPORTED BY TWO DIFFERENT ACTORS, and the
# difference is what the operator does next. 72 is THIS script's refusal:
# the table was evaluated before spawning anything and NO STAGE PROCESS EVER
# STARTED, so the move is `plan`. 13 is a stage's own: it ran far enough to read
# its input, so the move is the named file. They are deliberately not merged.
#
# NO CI RUNS ANY OF THIS. SCED-tools/ has no .github/ directory, and this change
# set does not add one: the art tier is pinned to this machine's Homebrew
# 3.14.7 / PIL 12.0.0 / numpy 2.4.2 triple by the golden fixture and the stdlib
# tier to Apple's /usr/bin/python3 3.9.6, which exists only on macOS. A hosted
# runner reproduces neither, so it would report green on a suite whose two most
# load-bearing checks it had not performed -- worse than no CI, because it reads
# as coverage. `koreanize.sh selftest` is the compensating control.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$(cd "${HERE}/../.." && pwd)"
WORKSPACE="$(cd "${TOOLS}/.." && pwd)"
TESTS="${TOOLS}/tests"

# The two interpreter tiers of §5.9, resolved once. The stdlib tier is Apple's
# xcode_select shim and is PINNED to a platform path -- it is the interpreter the
# py_compile check has to actually run, and a Homebrew python3 is an app bundle
# and therefore its own TCC responsible_path (see CLAUDE.md).
ART_PY="${KOREANIZE_ART_PYTHON:-python3}"
STDLIB_PY="${KOREANIZE_STDLIB_PYTHON:-/usr/bin/python3}"

EXIT_REFUSED=71
EXIT_UPSTREAM=72
EXIT_LIVE_DECLINED=73
EXIT_GOLDEN_SKIPPED=75

# stage -> module. A stage with no row here does not exist (§4.1).
stage_module() {
  case "$1" in
    init)                                   echo "kz_init.py:art" ;;
    source)                                 echo "kz_source.py:art" ;;
    triage)                                 echo "kz_triage.py:art" ;;
    scaffold|reuse|objtext|register|revert|repoint)
                                            echo "kz_langpack.py:stdlib" ;;
    verify)                                 echo "kz_verify.py:stdlib" ;;
    terms)                                  echo "kz_terms.py:art" ;;
    translate)                              echo "kz_translate.py:art" ;;
    check)                                  echo "kz_checkers.py:art" ;;
    slice)                                  echo "kz_slice.py:art" ;;
    mask)                                   echo "kz_mask.py:art" ;;
    erase)                                  echo "kz_erase.py:art" ;;
    composite)                              echo "kz_composite.py:art" ;;
    typeset)                                echo "kz_typeset.py:art" ;;
    recompose)                              echo "kz_recompose.py:art" ;;
    upload)                                 echo "kz_upload.py:art" ;;
    audit)                                  echo "kz_audit.py:art" ;;
    ocr)                                    echo "kz_ocr.py:art" ;;
    *)                                      return 1 ;;
  esac
}

# The langpack submodes take their stage name as argv[1] rather than as the
# module's own name.
is_submode() {
  case "$1" in
    scaffold|reuse|objtext|register|revert|repoint) return 0 ;;
    *) return 1 ;;
  esac
}

# The submodes that PLAN A WRITE SET, which is a strictly smaller set and is what
# the --live banner is derived from. `revert` is a submode and is deliberately
# NOT here: it is the inverse stage, so it has no plan to rehearse and no
# <stage>.plan.json to bind against -- its input is the record of a write that
# already happened. Routing it through the banner path makes it rehearse itself,
# which reports the write it is about to undo as a failure (exit 24) and refuses
# to run the very restore it was invoked for.
plans_a_write_set() {
  case "$1" in
    scaffold|reuse|objtext|register|repoint) return 0 ;;
    *) return 1 ;;
  esac
}

die() { printf '%s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Import-time assertions -- the ones §1.2 and §4.1 require of the DISPATCHER
# ---------------------------------------------------------------------------
#
# The exemption set is asserted rather than special-cased inline, because a
# widening of it is exactly the change that would make `revert` unreachable in
# the situation §8.4 invokes it for, and a comment saying "only revert" would not
# stop one.
assert_contracts() {
  "${STDLIB_PY}" - "${HERE}" <<'PY' || return 1
import sys
sys.path.insert(0, sys.argv[1])
import kz_config as kz
assert kz.PREDECESSOR_EXEMPT == frozenset({"revert"}), \
    "the predecessor exemption set must be exactly {'revert'} (%s)" % (
        sorted(kz.PREDECESSOR_EXEMPT),)
assert set(kz.PREDECESSORS) == set(kz.STAGES), "the predecessor table is not total"
PY
}

registered_flags() {
  "${STDLIB_PY}" - "${HERE}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import kz_common as kc
print(" ".join(sorted(kc.all_flags())))
PY
}

# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

usage() {
  cat <<'EOF'
koreanize -- staged, resumable, config-driven Korean scenario localizer

  koreanize.sh --status [--slug SLUG]              read-only; run this FIRST
  koreanize.sh init --scenario "<name>" [--pack Campaigns|FanCampaigns] [--slug S]
  koreanize.sh <stage> --slug SLUG [options]
  koreanize.sh plan --slug SLUG                    the DAG + what is stale
  koreanize.sh golden [--fixture midwinter]        the byte-exact regression
  koreanize.sh selftest [--pytest-only]            the whole suite + every --selftest
  koreanize.sh --help

global options (dispatcher, not stage):
  --run-dir DIR   override <run_dir> for THIS INVOCATION ONLY. Argv, never a
                  scenario.json field, so config_sha256 is untouched and a
                  scratch run cannot invalidate the init gate's bound_sha256
  --yes           skip the typed LIVE confirmation; the banner is still printed

stages (22): init source scaffold reuse terms translate check slice mask erase
             composite typeset recompose upload repoint objtext register verify
             revert triage audit ocr

v0 ships: init source scaffold reuse objtext register revert verify + triage(S6)

exit codes
  0            the stage completed and every check passed
  71 72 73 75  THIS SCRIPT's band (refused / upstream / live declined / golden
               skipped). Everything else is the stage's own -- see
               `<stage> --help` or README.md's exit table.

  NOTE 20, 21 and 30 collide numerically with daily-sync-local.sh's "fetch
  failed", "overlap gate script failed" and "rebase failed". That is accepted
  and stated rather than worked around: they are STAGE-LOCAL codes that only
  ever appear in a koreanize report and never in a Discord notification.
EOF
}

# ---------------------------------------------------------------------------
# --status / plan
# ---------------------------------------------------------------------------

golden_line() {
  # A silent skip and a pass look identical once --status prose scrolls away, and
  # on a Homebrew interpreter a `brew upgrade` is enough to produce one. So the
  # skip is printed on EVERY status, loudly, whether or not anyone asked.
  "${ART_PY}" - "${HERE}" <<'PY' 2>/dev/null || true
import json, os, sys
sys.path.insert(0, sys.argv[1])
manifest = os.path.join(sys.argv[1], "data", "golden", "midwinter.manifest.json")
if not os.path.exists(manifest):
    print("golden : SKIPPED -- art chain UNTESTED on this interpreter "
          "(no data/golden/midwinter.manifest.json; it lands in §6 step 8)")
    raise SystemExit(0)
recorded = (json.load(open(manifest)).get("tool") or {})
found = {"python": "%d.%d.%d" % sys.version_info[:3]}
for name, mod in (("pil", "PIL"), ("numpy", "numpy")):
    try:
        found[name] = __import__(mod).__version__
    except ImportError:
        found[name] = None
drift = [k for k in ("python", "pil", "numpy")
         if recorded.get(k) and recorded.get(k) != found.get(k)]
if drift:
    print("golden : SKIPPED -- art chain UNTESTED on this interpreter (%s)"
          % ", ".join("%s %s != recorded %s" % (k, found.get(k), recorded.get(k))
                      for k in drift))
PY
}

base_moved_line() {
  # §5.10 hazard A. The nightly FORCE-PUSHES korean every successful night; the
  # rebase happens in a detached scratch worktree, so uncommitted koreanize
  # writes are invisible to it and survive untouched. What moves is the BASE, so
  # a write set planned against the old one is merely STALE -- re-running is
  # right and `revert` is wrong, which is why this is a line and not a refusal.
  local head base
  head="$(git -C "${WORKSPACE}/SCED-downloads" rev-parse HEAD 2>/dev/null)" || return 0
  base="$(git -C "${WORKSPACE}/SCED-downloads" rev-parse origin/korean 2>/dev/null)" || return 0
  if [[ "${head}" != "${base}" ]]; then
    printf 'base   : origin/korean is %s, HEAD is %s -- the nightly moved the base under this run.\n' \
      "${base:0:12}" "${head:0:12}"
    printf '         Nothing is lost: re-run the planning stage. Do NOT revert.\n'
  fi
}

do_status() {
  local run_dir="$1"
  printf 'koreanize --status\n'
  "${STDLIB_PY}" "${HERE}/kz_config.py" --lock-status | sed 's/^/  /'
  base_moved_line
  golden_line
  if [[ -z "${run_dir}" || ! -d "${run_dir}" ]]; then
    printf 'run    : no run directory (%s)\n' "${run_dir:-<none given>}"
    printf 'next   : koreanize.sh init --scenario "<name>"\n'
    return 0
  fi
  printf 'run    : %s\n' "${run_dir}"
  # ONE invocation, captured. `plan()` calls `stage_state()` for all 22 stages
  # and each sha256s every path in that stage's binding{}, so a second call to
  # scrape one already-printed line doubled the hashing of the command whose
  # whole purpose is to be cheap enough to run constantly.
  local plan_out next
  plan_out="$("${STDLIB_PY}" "${HERE}/kz_config.py" --plan "${run_dir}")"
  printf '%s\n' "${plan_out}"
  next="$(printf '%s\n' "${plan_out}" | awk '/^next   :/ {print $3}')"
  if [[ -n "${next}" && "${next}" != "--" ]]; then
    printf 'argv   : koreanize.sh %s --run-dir %s\n' "${next}" "${run_dir}"
  fi
}

# ---------------------------------------------------------------------------
# The predecessor gate -- exit 72, taken BEFORE anything is spawned
# ---------------------------------------------------------------------------

check_predecessors() {
  local stage="$1" run_dir="$2"
  "${STDLIB_PY}" - "${HERE}" "${stage}" "${run_dir}" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import kz_config as kz
stage, run_dir = sys.argv[2], sys.argv[3]
if stage in kz.PREDECESSOR_EXEMPT:
    # `revert` is exempt and HAS to be: its predecessor is by construction the
    # stage that just failed, and a failed stage is never consumable.
    raise SystemExit(0)
unmet = []
for pred in kz.predecessors_of(stage, run_dir):
    state = kz.stage_state(run_dir, pred)
    if not state["consumable"]:
        unmet.append("%s (%s)" % (pred, state["blocked_by"] or "not consumable"))
    elif state["stale"]:
        unmet.append("%s (stale: %s)" % (pred, ", ".join(state["stale_paths"][:4])))
if unmet:
    sys.stderr.write("exit 72: %s cannot run yet\n" % stage)
    for line in unmet:
        sys.stderr.write("  waiting on %s\n" % line)
    sys.stderr.write("  nothing was started. Run `koreanize.sh plan` to see the DAG.\n")
    raise SystemExit(1)
PY
}

# ---------------------------------------------------------------------------
# The --live banner, and the plan it is built from
# ---------------------------------------------------------------------------

run_live() {
  # §4.1: the dispatcher asks the module for the planned write set with a
  # rehearsal, the module PERSISTS it to <run_dir>/<stage>.plan.json, the banner
  # is printed from that file, and the live invocation carries its sha256 in its
  # own binding{} and asserts equality against it. Without the persisted plan the
  # two invocations share nothing and "the executed write set equals the plan the
  # banner printed" is a statement one process makes about itself.
  local stage="$1" run_dir="$2" assume_yes="$3"; shift 3
  printf 'koreanize %s -- rehearsing to build the plan...\n' "${stage}"
  "${STDLIB_PY}" "${HERE}/kz_langpack.py" "${stage}" --run-dir "${run_dir}" "$@" >/dev/null
  local rc=$?
  if [[ ${rc} -ne 0 ]]; then
    die "the rehearsal failed (exit ${rc}); nothing was written"
    return ${rc}
  fi

  local plan="${run_dir}/${stage}.plan.json"
  "${STDLIB_PY}" - "${plan}" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
print("")
print("=" * 72)
print("  LIVE WRITE -- koreanize %s -- %s" % (doc["stage"], doc["slug"]))
print("=" * 72)
print("  files planned : %d" % doc["count"])
create = sum(1 for e in doc["entries"] if e["action"] == "create")
print("  create/modify : %d / %d" % (create, doc["count"] - create))
print("  roots         :")
for root in doc["roots"]:
    print("      %s" % root)
print("  plan          : %s" % sys.argv[1])
print("=" * 72)
PY

  if [[ "${assume_yes}" != "yes" ]]; then
    if [[ ! -t 0 ]]; then
      die "exit ${EXIT_LIVE_DECLINED}: --live needs a tty to confirm, or --yes"
      return ${EXIT_LIVE_DECLINED}
    fi
    printf 'Type LIVE to proceed: '
    local answer=""
    read -r answer
    if [[ "${answer}" != "LIVE" ]]; then
      die "exit ${EXIT_LIVE_DECLINED}: --live declined at the banner; nothing was written"
      return ${EXIT_LIVE_DECLINED}
    fi
  fi

  "${STDLIB_PY}" "${HERE}/kz_langpack.py" "${stage}" --run-dir "${run_dir}" \
    --live --yes "$@"
}

# ---------------------------------------------------------------------------
# selftest -- the compensating control for the absence of CI (§5.9)
# ---------------------------------------------------------------------------

do_selftest() {
  local pytest_only="${1:-no}"
  local failures=0

  printf '=== pytest (art interpreter) ===\n'
  ( cd "${TOOLS}" && "${ART_PY}" -m pytest tests/ -q ) || failures=$((failures + 1))

  if [[ "${pytest_only}" == "yes" ]]; then
    [[ ${failures} -eq 0 ]] && printf '\nselftest: PASS (pytest only)\n' \
      || printf '\nselftest: FAIL (%d)\n' "${failures}"
    return ${failures}
  fi

  printf '\n=== module --selftest ===\n'
  local module tier name
  for name in kz_common kz_config kz_init kz_ask kz_decide kz_triage kz_source; do
    [[ -f "${HERE}/${name}.py" ]] || continue
    printf -- '--- %s\n' "${name}"
    "${ART_PY}" "${HERE}/${name}.py" --selftest || failures=$((failures + 1))
  done
  for name in kz_langpack kz_verify; do
    [[ -f "${HERE}/${name}.py" ]] || continue
    printf -- '--- %s (stdlib tier)\n' "${name}"
    "${STDLIB_PY}" "${HERE}/${name}.py" --selftest || failures=$((failures + 1))
  done

  # GREEN IS PHASE-SCOPED (§4.1). A TOLERANCES or CONSENTS row whose owning
  # module does not yet exist is COUNTED AND PRINTED as `pending`, never skipped
  # -- so the completion predicate is satisfiable at each step without any step
  # being able to hide a gap. kz_common --selftest above prints those lines.
  printf '\n'
  if [[ ${failures} -eq 0 ]]; then
    printf 'selftest: PASS -- pytest green, every module --selftest green, and\n'
    printf '          the pending TOLERANCES/CONSENTS rows are counted above.\n'
  else
    printf 'selftest: FAIL (%d component(s))\n' "${failures}"
  fi
  return ${failures}
}

# ---------------------------------------------------------------------------
# golden -- a COMMAND, not a stage (§5.8). Lands in §6 step 15.
# ---------------------------------------------------------------------------

do_golden() {
  local fixture="${1:-midwinter}"
  local manifest="${HERE}/data/golden/${fixture}.manifest.json"
  if [[ ! -f "${manifest}" ]]; then
    die "exit 13: golden inputs missing -- ${manifest}"
    die "  The fixture manifest is written by §6 step 8 (the Midwinter backfill),"
    die "  and the driven stages by step 15. Neither has landed."
    die "  This is a RESTORE JOB, not an environment difference: a missing corpus"
    die "  is exit 13 and an interpreter mismatch is exit ${EXIT_GOLDEN_SKIPPED}, and merging"
    die "  the two is what §5.8's split exists to prevent."
    return 13
  fi
  die "exit 13: \`golden\` drives slice/mask/composite/typeset/recompose, none of"
  die "  which exists yet (§6 step 15). The manifest is present; the stages are not."
  return 13
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

main() {
  if ! assert_contracts; then
    die "exit ${EXIT_REFUSED}: the dispatcher's own contract assertions failed"
    return ${EXIT_REFUSED}
  fi

  local stage="" slug="" run_dir="" assume_yes="no" want_status="no"
  local pytest_only="no" fixture="midwinter"
  local -a passthrough=()

  if [[ $# -eq 0 ]]; then usage; return ${EXIT_REFUSED}; fi

  local registered
  registered=" $(registered_flags) "

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --help|-h) usage; return 0 ;;
      --status)  want_status="yes"; shift ;;
      --slug)    slug="${2:-}"; shift 2 ;;
      --run-dir) run_dir="${2:-}"; shift 2 ;;
      --yes)     assume_yes="yes"; shift ;;
      --pytest-only) pytest_only="yes"; shift ;;
      --fixture) fixture="${2:-midwinter}"; shift 2 ;;
      --accept-*|--consent-*)
        # The two CLOSED families. An unregistered flag in either is exit 71,
        # refused before any stage ran -- a flag family that silently accepted
        # anything would make the TOLERANCES table's totality unenforceable from
        # the one place an operator actually types.
        if [[ "${registered}" != *" $1 "* ]]; then
          die "exit ${EXIT_REFUSED}: $1 is not a registered --accept-* / --consent-* flag"
          die "  registered: ${registered}"
          die "  Both families are closed lists (kz_common.TOLERANCES / CONSENTS)."
          return ${EXIT_REFUSED}
        fi
        passthrough+=("$1"); shift ;;
      --*)       passthrough+=("$1"); shift ;;
      *)
        if [[ -z "${stage}" ]]; then stage="$1"; else passthrough+=("$1"); fi
        shift ;;
    esac
  done

  if [[ -z "${run_dir}" && -n "${slug}" ]]; then
    run_dir="${WORKSPACE}/.am/koreanize/${slug}"
  fi

  if [[ "${want_status}" == "yes" || "${stage}" == "status" ]]; then
    do_status "${run_dir}"; return 0
  fi

  case "${stage}" in
    plan)
      if [[ -z "${run_dir}" ]]; then
        die "exit ${EXIT_REFUSED}: plan needs --slug or --run-dir"; return ${EXIT_REFUSED}
      fi
      base_moved_line; golden_line
      "${STDLIB_PY}" "${HERE}/kz_config.py" --plan "${run_dir}"
      return 0 ;;
    selftest) do_selftest "${pytest_only}"; return $? ;;
    golden)   do_golden "${fixture}"; return $? ;;
  esac

  local row
  if ! row="$(stage_module "${stage}")"; then
    die "exit ${EXIT_REFUSED}: unknown stage ${stage:-<none>}"
    die "  stages: init source scaffold reuse terms translate check slice mask"
    die "          erase composite typeset recompose upload repoint objtext"
    die "          register verify revert triage audit ocr"
    return ${EXIT_REFUSED}
  fi
  local module="${row%%:*}" tier="${row##*:}"
  if [[ ! -f "${HERE}/${module}" ]]; then
    die "exit ${EXIT_REFUSED}: ${stage} is owned by ${module}, which has not been built yet"
    die "  v0 ships: init source scaffold reuse objtext register revert verify + triage"
    return ${EXIT_REFUSED}
  fi

  local py="${ART_PY}"
  [[ "${tier}" == "stdlib" ]] && py="${STDLIB_PY}"

  if [[ "${stage}" != "init" && -z "${run_dir}" ]]; then
    die "exit ${EXIT_REFUSED}: ${stage} needs --slug or --run-dir"
    return ${EXIT_REFUSED}
  fi

  # The predecessor gate, evaluated BEFORE anything is spawned -- which is the
  # whole difference between 72 and a stage's own 13.
  if [[ -n "${run_dir}" && -d "${run_dir}" ]]; then
    if ! check_predecessors "${stage}" "${run_dir}"; then
      return ${EXIT_UPSTREAM}
    fi
  fi

  # Every submode that leaves <run_dir> goes through the banner. Rehearsal is the
  # DEFAULT: an operator who omits --live gets a rehearsal and a NOTE, which is
  # the safe outcome.
  local wants_live="no"
  local arg
  for arg in "${passthrough[@]+"${passthrough[@]}"}"; do
    [[ "${arg}" == "--live" ]] && wants_live="yes"
  done

  if plans_a_write_set "${stage}" && [[ "${wants_live}" == "yes" ]]; then
    local -a rest=()
    for arg in "${passthrough[@]+"${passthrough[@]}"}"; do
      [[ "${arg}" == "--live" || "${arg}" == "--yes" ]] && continue
      rest+=("${arg}")
    done
    run_live "${stage}" "${run_dir}" "${assume_yes}" "${rest[@]+"${rest[@]}"}"
    return $?
  fi

  local -a argv=()
  is_submode "${stage}" && argv+=("${stage}")
  [[ -n "${run_dir}" ]] && argv+=(--run-dir "${run_dir}")
  [[ "${stage}" == "init" && -n "${slug}" ]] && argv+=(--slug "${slug}")
  # `revert` prints its OWN banner inside the module (it has no plan.json for the
  # write-set banner to be built from), so --yes has to reach it. The dispatcher
  # consumes --yes into assume_yes for the write-set path, which would otherwise
  # leave a `revert --live --yes` demanding a tty it was explicitly told to skip.
  [[ "${stage}" == "revert" && "${assume_yes}" == "yes" ]] && argv+=(--yes)
  argv+=("${passthrough[@]+"${passthrough[@]}"}")

  "${py}" "${HERE}/${module}" "${argv[@]+"${argv[@]}"}"
}

main "$@"
