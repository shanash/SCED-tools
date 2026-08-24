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
  --keep-scratch  `golden` only: do NOT remove the scratch run dir on exit. The
                  seed is labelled seed{golden_run_id} and is consumable to that
                  run alone, so keeping it cannot launder anything -- it is how a
                  refusal is read afterwards (§5.8)

stages (22): init source scaffold reuse terms translate check slice mask erase
             composite typeset recompose upload repoint objtext register verify
             revert triage audit ocr

v0 ships: init source scaffold reuse objtext register revert verify + triage(S6)

exit codes
  0            the stage completed and every check passed
  71 72 73 75  THIS SCRIPT's band (refused / upstream / live declined / golden
               skipped). Everything else is the stage's own -- see
               `<stage> --help` or README.md's exit table.

  `golden` additionally returns 13 (inputs missing or DRIFTED, or the seeded
  closure is not covered -- a RESTORE job) and 14 (the chain ran and produced
  different bytes -- the regression it exists to catch). Its 75 is the SKIP,
  and the three are never merged: restore, re-derive, pin the interpreter.

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
  # Absent modules are skipped by the guard below, so a name may be added here
  # as soon as the design names its module -- which is what keeps the completion
  # predicate non-vacuous for the step that builds it. A step whose module is
  # missing from this list has a `--selftest` nothing ever runs.
  for name in kz_common kz_config kz_init kz_ask kz_decide kz_triage kz_source \
              kz_checkers kz_terms kz_translate kz_audit \
              kz_slice kz_mask kz_erase kz_composite kz_typeset kz_recompose \
              kz_upload kz_ocr; do
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
# golden -- a COMMAND, not a stage (§5.8). §6 step 15.
# ---------------------------------------------------------------------------
#
# `golden` writes no <stage>.json of its own, has no gate and no binding{}: it
# MINTS a run id, SEEDS a scratch run dir from data/golden/<fixture>.manifest.json
# and then drives five real stages through this very dispatcher. Its whole claim
# is byte-exactness of the ARITHMETIC chain -- slice -> mask -> composite ->
# typeset -> recompose -- and the design states the narrowness on purpose: the
# text chain (S1/S2) is seeded, not driven, so that the regression instrument
# does not depend on a model version and can run offline.
#
# THE THREE OUTCOMES ARE DELIBERATELY DISTINCT AND MUST NEVER BE MERGED (§3.1,
# §5.8):
#
#   13  inputs missing or DRIFTED, the closure is not covered, or the seed the
#       manifest describes is not consumable. NEVER a skip -- a missing corpus is
#       a RESTORE JOB, not an environment difference, and `delivered/` has no
#       second copy anywhere on earth.
#   75  the interpreter triple the stages actually ran under is not the fixture's.
#       A SKIP with a named reason, never a pass, and it has a code of its own
#       because otherwise nothing outside --status prose separates it from a 0.
#   14  the chain ran and produced DIFFERENT BYTES. That is the regression the
#       instrument exists to catch. 14 rather than 13 because 13 is scoped to
#       INPUTS; the precedent for byte re-derivation drift is kz_mask.py's M4
#       (`rederivation_matches_reference`, exit_on_fail EXIT_DRIFT).
#
# Anything else is the driven stage's own code, passed through verbatim.

# The scratch run dir of the CURRENT `golden`, and the knob its trap reads.
# Globals because bash has no `finally`: the removal §5.8 requires is a trap on
# EXIT/INT/TERM, and a trap body cannot take an argument.
GOLDEN_SCRATCH=""
GOLDEN_KEEP="no"

golden_cleanup() {
  local dir="${GOLDEN_SCRATCH}"
  GOLDEN_SCRATCH=""
  [[ -n "${dir}" ]] || return 0
  # One guard before an `rm -rf` on an assembled path. The scratch dir is minted
  # by do_golden alone and is always under .am/koreanize/.golden/, so any other
  # value is a defect in this file and is REFUSED rather than removed.
  case "${dir}" in
    */.am/koreanize/.golden/*) : ;;
    *) die "golden: NOT removing ${dir} -- it is not under .am/koreanize/.golden/"
       return 0 ;;
  esac
  if [[ "${GOLDEN_KEEP}" == "yes" ]]; then
    printf 'golden : scratch KEPT at %s (--keep-scratch)\n' "${dir}"
    printf '         Every artifact in it carries seed{golden_run_id}, so it is\n'
    printf '         consumable to NOTHING else -- not to a real run in the same\n'
    printf '         directory, not to a later `golden`, not to `plan` (§5.8).\n'
    return 0
  fi
  rm -rf "${dir}"
}

# ---- steps (1) and (2): bind the manifest, then seed the scratch run dir --------------------------------
#
# STDLIB tier on purpose (§5.9). The seeder touches JSON, hashes and file copies
# and nothing else, so running it on Apple's /usr/bin/python3 proves it needs
# neither PIL nor numpy -- and it is the interpreter CLAUDE.md's TCC rule wants
# in the ancestry of anything that opens /Volumes/PRO-G40 (Homebrew's python3 is
# an app bundle and therefore its own responsible_path).
golden_seed() {
  "${STDLIB_PY}" - "${HERE}" "$@" <<'PY'
import json, os, shutil, sys

sys.path.insert(0, sys.argv[1])
import kz_common as kc
import kz_config as kz

HERE, MANIFEST, SCRATCH, RUN_ID, FIXTURE = sys.argv[1:6]


def say(fmt, *args):
    print(fmt % args if args else fmt)


def placed_groups():
    """group -> how its files reach the scratch dir.

    A table rather than a loop over whatever happens to be there, because an
    input group with no destination is a group that is verified and then never
    used -- the shape that made tests/conftest.py report success while checking
    nothing. An unplaced group is exit 13, named.
    """
    return {
        # STRATEGY 1 of kz_slice.resolve_atlas_file (kz_slice.py:212-221): `init`
        # writes <run_dir>/assets/<sha256>.png and the inventory records that
        # digest, so the path is exact and norm() is never consulted.
        "atlases_en": "assets",
        # `erase` is the one seeded stage that is also REPLAYABLE: its output IS
        # the fixture's delivered/, which composite reads face by face.
        "delivered": "delivered",
    }


#: Groups `golden` itself consumes, by PATH, without placing them in the run dir.
#:
#: The placement guard's premise is "a verified-but-unplaced group is a group the
#: driven stages cannot see", and that is right for anything a stage reads out of
#: <run_dir>. It does not hold for a group this command reads itself: the masks
#: reference is handed to `mask` as `--compare-to <golden_root>/masks/manifest.json`,
#: an argv path resolved against the golden root, so copying it into the scratch
#: dir would place a file nothing opens there.
#:
#: Named rather than inferred, and kept a closed set, because the alternative --
#: "skip the guard when a group looks unused" -- is the guard deleting itself.
#: Adding a row here is a claim that `golden` reads that group directly, and it
#: should be as hard to add as it looks.
GOLDEN_CONSUMED = {
    "masks_reference": "--compare-to, resolved against the golden root (M4, §6 step 12)",
}


def main():
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    manifest_sha = kc.sha256_file(MANIFEST)
    slug = doc.get("slug") or FIXTURE
    say("golden : fixture %s, slug %s, manifest %s", FIXTURE, slug, manifest_sha[:16])
    say("golden : run id %s", RUN_ID)

    # ---- (1) BIND THE MANIFEST AGAINST THE DECLARED GOLDEN ROOT -----------
    env_name = doc.get("inputs_root_env") or "KOREANIZE_GOLDEN_ROOT"
    # Process env first, then ~/.config/koreanize/env, then the manifest's own
    # default. read_env_file() refuses a file that is not mode 0600 and returns
    # {} when there is none, so the default path needs no special case.
    root = (os.environ.get(env_name) or kz.read_env_file().get(env_name)
            or doc.get("inputs_root") or "")
    if not root:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the fixture declares no inputs_root and %s is unset" % env_name,
                  MANIFEST)
    root = os.path.abspath(os.path.expanduser(str(root)))

    declared = []
    for group in sorted(doc.get("inputs") or {}):
        value = (doc.get("inputs") or {})[group]
        if not isinstance(value, list):
            continue                    # `inputs.note` is prose, not a file
        for entry in value:
            if isinstance(entry, dict) and entry.get("path"):
                declared.append((group, entry))

    # AN EMPTY DECLARED SET IS ITSELF A REFUSAL, and this is not defensive
    # programming -- it is the exact defect just fixed in tests/conftest.py,
    # where the reader looked for a flat `files[]` key this manifest does not
    # have, got an empty list, checked nothing, and reported success. All three
    # outcomes the predicate exists to separate became unreachable at once.
    if not declared:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the fixture declares no inputs -- refusing a vacuous bind",
                  "%s: inputs{} holds no group whose entries carry `path`. A bind "
                  "over an empty set passes without reading a byte, which is the "
                  "one failure this refusal exists for." % MANIFEST)

    total_bytes = 0
    for group, entry in declared:
        rel = entry["path"]
        path = os.path.join(root, rel)
        # First offending path only (§3.1): the operator's move is the same for
        # all of them and it is a restore, so the list adds nothing.
        if not os.path.exists(path):
            kc.refuse(kc.EXIT_PRECONDITION,
                      "golden inputs missing or drifted",
                      "%s (declared in inputs.%s) is not on disk under %s -- this "
                      "is a RESTORE JOB, not an environment difference. Override "
                      "the root with %s in ~/.config/koreanize/env."
                      % (rel, group, root, env_name))
        size = os.path.getsize(path)
        if entry.get("bytes") is not None and size != entry["bytes"]:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "golden inputs missing or drifted",
                      "%s (inputs.%s) is %d bytes, the manifest records %d"
                      % (rel, group, size, entry["bytes"]))
        got = kc.sha256_file(path)
        if got != entry.get("sha256"):
            kc.refuse(kc.EXIT_PRECONDITION,
                      "golden inputs missing or drifted",
                      "%s (inputs.%s) hashes %s, the manifest records %s"
                      % (rel, group, got[:16], str(entry.get("sha256"))[:16]))
        total_bytes += size
    say("golden : bound %d declared inputs (%.1f MB) under %s",
        len(declared), total_bytes / 1048576.0, root)

    # ---- THE COVERAGE GUARD (part of (2), and taken BEFORE a byte is written) ------------------------------------------
    #
    # THE SEEDED SET IS A CLOSURE, NOT A LIST, because a list is exactly what
    # left `init` out: the previous guard was scoped to "every stage it seeds",
    # a tautology over its own list, and an unlisted `init` therefore surfaced as
    # a `slice` refusal at 72 rather than as a named 13. Computed from
    # kz_config.PREDECESSORS so that adding a stage to driven_stages recomputes
    # it -- never hardcoded here.
    driven = list(doc.get("driven_stages") or ())
    if not driven:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the fixture drives no stages", MANIFEST)
    unknown = [s for s in driven if s not in kz.PREDECESSORS]
    if unknown:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "driven_stages names a stage the predecessor table does not",
                  "%s -- kz_config.PREDECESSORS is asserted total against STAGES"
                  % unknown[0])
    seen, stack = set(), list(driven)
    while stack:
        for pred in kz.PREDECESSORS.get(stack.pop(), ()):
            if pred not in seen:
                seen.add(pred)
                stack.append(pred)
    closure = sorted(seen - set(driven))
    seeded_reports = (doc.get("seed") or {}).get("reports") or {}
    for stage in closure:
        if stage not in seeded_reports:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the fixture does not cover the seeded closure",
                      "`%s` is in the transitive predecessor closure of %s but "
                      "seed.reports has no entry for it. Extending driven_stages "
                      "without extending the seed leaves a stage both undriven "
                      "and unseeded, which surfaces one stage later as a 72."
                      % (stage, driven))
    say("golden : closure %s (driven %s)", closure, driven)

    # ---- the scenario, and the guard on the scratch dir -------------------
    #
    # Loaded from the DURABLE tier and not from the run dir, because the run dir
    # does not exist yet and this has to be asserted BEFORE the first write.
    # `scenario.json` is not one of init's three data files: it is git-tracked in
    # data/scenarios/, and the manifest binds its digest through init's binding{},
    # so a drifted config is a named 13 here rather than a pin failure two stages
    # later.
    scenario_src = os.path.join(HERE, "data", "scenarios",
                                "%s.scenario.json" % slug)
    init_binding = (seeded_reports.get("init") or {}).get("binding") or {}
    want = init_binding.get("scenario.json")
    if not os.path.exists(scenario_src):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the fixture's scenario.json is not in the durable tier",
                  scenario_src)
    got = kc.sha256_file(scenario_src)
    if want and got != want:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "golden inputs missing or drifted",
                  "%s hashes %s, seed.reports.init.binding records %s"
                  % (scenario_src, got[:16], want[:16]))
    cfg = kz.load_scenario(scenario_src)

    # P0a + P0b for the dir we are about to mint. check_invocation_guards refuses
    # at exit 2 for the nightly scratch worktree (no override, deliberately); the
    # write_roots / forbidden containment below is exit 4, the same code and the
    # same reasoning as kz_slice.assert_run_dir_writes (kz_slice.py:800-812).
    # --run-dir is ARGV and therefore unpinned by config_sha256 -- it is the one
    # input a typo can retarget -- so `golden`'s own scratch path is asserted
    # exactly as a stage asserts an operator's.
    kc.check_invocation_guards([SCRATCH])
    rel = kz._rel_to_workspace(SCRATCH, kc.WORKSPACE_ROOT)
    findings = []
    if rel is not None:
        rel_posix = rel.replace(os.sep, "/").rstrip("/") + "/"
        for write_root in cfg["guard"]["write_roots"]:
            if rel_posix.startswith(write_root) or write_root.startswith(rel_posix):
                findings.append("scratch <run_dir> %s lands inside "
                                "guard.write_roots %r" % (rel_posix, write_root))
        for bad in cfg["guard"]["forbidden"]:
            if rel_posix.startswith(bad):
                findings.append("scratch <run_dir> %s matches guard.forbidden %r"
                                % (rel_posix, bad))
    if findings:
        kc.refuse(kc.EXIT_GUARD, "the golden scratch run dir violates the path guard",
                  "; ".join(findings))

    # ---- (2) SEED ---------------------------------------------------------
    #
    # Confined to the scratch run dir and written by `golden` alone: no stage
    # gains a seeding path, data/locks/<slug>.lock.json is NEVER written here,
    # and scenario.json is never edited -- which is precisely why --run-dir is
    # argv rather than a config field (§5.8).
    os.makedirs(SCRATCH)
    shutil.copyfile(scenario_src, os.path.join(SCRATCH, "scenario.json"))

    seed_files = {}          # golden-tier relpath -> destination basename
    for stage, block in sorted(seeded_reports.items()):
        for spec in block.get("files") or []:
            seed_files[spec["from"]] = spec

    dests = placed_groups()
    by_group = {}
    for group, entry in declared:
        by_group.setdefault(group, []).append(entry)
    for group in sorted(by_group):
        if group in dests:
            continue
        if group in GOLDEN_CONSUMED:
            say("golden : inputs.%s consumed directly by golden (%s)",
                group, GOLDEN_CONSUMED[group])
            continue
        if all(e["path"] in seed_files for e in by_group[group]):
            continue
        kc.refuse(kc.EXIT_PRECONDITION,
                  "an input group has no destination in the scratch run dir",
                  "inputs.%s is verified by step (1) and then read by nothing. "
                  "Either name it in a seed.reports.<stage>.files[] entry or give "
                  "it a destination -- a verified-but-unplaced group is a group "
                  "the driven stages cannot see." % group)

    copied = 0
    for group, entry in declared:
        rel_in = entry["path"]
        src = os.path.join(root, rel_in)
        spec = seed_files.get(rel_in)
        if spec is not None:
            # `init`'s seed carries its three DATA FILES and not only its verdict:
            # every one of the five driven stages reads the files.
            dst = os.path.join(SCRATCH, spec["name"])
        elif dests.get(group) == "assets":
            dst = os.path.join(SCRATCH, "assets", "%s.png" % entry["sha256"])
        elif dests.get(group) == "delivered":
            dst = os.path.join(SCRATCH, "delivered", os.path.basename(rel_in))
        else:
            continue
        parent = os.path.dirname(dst)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        # COPIED, never symlinked. A symlink farm would leave the read-only
        # golden tier one errant open("w") away from being destroyed, and
        # `delivered/` has no second copy anywhere -- not in git, not on R2, not
        # in a release asset (§3.1).
        shutil.copyfile(src, dst)
        copied += 1
    say("golden : seeded %d input files into %s", copied, SCRATCH)

    # Reports. Every seeded artifact is bound to the FIXTURE'S OWN sha256s, so a
    # seed cannot be reused against a different tree; and every one carries
    # seed{fixture, manifest_sha256, golden_run_id}, which compute_consumable's
    # last conjunct turns into "consumable to the stages of THIS golden run and
    # to nothing else" -- not to a real run in the same directory, not to a later
    # `golden`, not to `plan`. Without the label a seeded `terms` report carrying
    # ai.used true is indistinguishable from one that really invoked claude.
    seeded_gates = (doc.get("seed") or {}).get("gates") or {}
    for stage in sorted(seeded_reports):
        block = seeded_reports[stage]
        seed = dict(block.get("seed") or {})
        seed["fixture"] = seed.get("fixture") or FIXTURE
        seed["manifest_sha256"] = manifest_sha
        seed["golden_run_id"] = RUN_ID
        gate = None
        if stage in seeded_gates:
            gate = dict(seeded_gates[stage])
            gate["seed"] = dict(seed)
            gate["gate_for"] = stage
        report = kc.new_report(
            stage, slug, mode=block.get("mode") or "build",
            counts={"seeded": True},
            checks=[],
            # VERBATIM from the manifest, never recomputed: the binding is what
            # `stage_state` re-hashes to decide stale, so a seed that hashed the
            # scratch copy instead would be a seed that can never go stale.
            binding=dict(block.get("binding") or {}),
            ai=block.get("ai"),
            gate=gate,
            seed=seed)
        report["generated_by"] = "koreanize golden --fixture %s (seed)" % FIXTURE
        report["results"] = {"why_seeded": block.get("why_seeded")}
        kc.finalize_report(report, cfg=cfg, golden_run_id=RUN_ID)
        if not report["consumable"]:
            # A seed that is not consumable is a broken seed, and it would
            # surface one stage later as a 72 naming a stage nobody seeded by
            # hand. Named here instead.
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the seeded %s report is not consumable" % stage,
                      "%s -- the manifest's seed block does not satisfy §3.6"
                      % report["consumable_blocked_by"])
        kc.write_report(report, SCRATCH, cfg=cfg, golden_run_id=RUN_ID)

    # Gates. A fresh scratch run writes `pending` stubs and §3.6 makes
    # gate.status == "accepted" a consumable conjunct, so without these the chain
    # stops at the first gated stage even with every predecessor satisfied.
    # The front matter is delimited with `---` AND written as bare `key: value`
    # lines because the two parsers in the tool differ: kz_typeset.parse_gate
    # (kz_typeset.py:1477) toggles on `---` and also reads `- [ ]` checkboxes,
    # kz_mask.parse_gate (kz_mask.py:1231) reads bare lines and stops at `## `.
    # No checkbox is written, so nothing is left unticked -- an accepted gate
    # with an unticked item is refused by design (§5.7).
    gates_dir = os.path.join(SCRATCH, "gates")
    if seeded_gates and not os.path.isdir(gates_dir):
        os.makedirs(gates_dir)
    for stage in sorted(seeded_gates):
        block = seeded_gates[stage]
        text = [
            "---",
            "status: %s" % (block.get("status") or "pending"),
            "reviewer: %s" % (block.get("accepted_by") or ""),
            "date: %s" % (block.get("accepted_on") or ""),
            "gate_for: %s" % stage,
            "bound_sha256: %s" % (block.get("bound_sha256") or ""),
            "---",
            "",
            "# %s gate -- %s (SEEDED)" % (stage, slug),
            "",
            "Written by `koreanize golden --fixture %s`, run id `%s`, from the"
            % (FIXTURE, RUN_ID),
            "verdict recorded in `data/golden/%s.manifest.json` (manifest sha256"
            % FIXTURE,
            "`%s`)." % manifest_sha,
            "",
            "This is NOT a human acceptance performed today: it replays one that",
            "was. `bound_sha256` is the fixture's, so `carry_review()` demotes it",
            "to `pending` the moment those bytes move -- a verdict can never",
            "outlive the pixels it was given to (§5.7).",
        ]
        path = os.path.join(gates_dir, "%s-gate.md" % stage)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(text) + "\n")
    say("golden : seeded %d reports and %d gates (%s / %s)",
        len(seeded_reports), len(seeded_gates),
        ", ".join(sorted(seeded_reports)), ", ".join(sorted(seeded_gates)))

    # The manifest's own reference for `mask`'s M4. Absent today, and the absence
    # is REPORTED rather than papered over: with no reference M4 passes with
    # "nothing to compare against", so step 12's re-derivation claim would go
    # unasserted and `golden` would report a byte-exactness it never checked.
    ref = os.path.join(root, "masks", "manifest.json")
    ok = False
    if os.path.exists(ref):
        try:
            with open(ref, "r", encoding="utf-8") as fh:
                ok = bool((json.load(fh) or {}).get("entries"))
        except ValueError:
            ok = False
    handoff = os.path.join(SCRATCH, ".golden-run")
    if not ok:
        say("golden : WARNING -- no usable masks/manifest.json under %s.", root)
        say("         M4 will pass with `nothing to compare against`, so this run "
            "does NOT")
        say("         assert mask re-derivation. compare_manifests reads "
            "reference[\"entries\"]")
        say("         (kz_mask.py:1360); a manifest without that key yields an "
            "empty reference.")
    # The handoff to the shell. A FILE inside the scratch dir rather than stdout,
    # so that the seeder's own output -- 94 hashes and a 294 MB copy -- streams to
    # the operator live instead of being swallowed by a command substitution and
    # replayed after the refusal it was supposed to explain.
    with open(handoff, "w", encoding="utf-8") as fh:
        fh.write("MASK_REFERENCE=%s\n" % (ref if ok else ""))
        fh.write("SLUG=%s\n" % slug)
        fh.write("DRIVEN=%s\n" % " ".join(driven))
    return 0


try:
    raise SystemExit(main())
except kc.KzRefusal as exc:
    sys.stderr.write("%s\n" % exc)
    raise SystemExit(exc.code)
PY
}

# ---- steps (4) and (5): the interpreter triple, then the atlas bytes ------
golden_compare() {
  "${ART_PY}" - "${HERE}" "$@" <<'PY'
import json, os, sys

sys.path.insert(0, sys.argv[1])
import kz_common as kc

HERE, MANIFEST, SCRATCH = sys.argv[1:4]
DRIVEN = sys.argv[4:]

SKIPPED = kc.EXIT_DISPATCH_GOLDEN_SKIPPED
FIELDS = ("python", "pil", "numpy")


def load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def compare_tool(doc):
    """(4) BY REPORT, NEVER BY IMPORT.

    The reference triple is FIXTURE METADATA and lives in the manifest, because
    running typeset-cards.py under 3.9.6 / Pillow 10.4.0 "would re-encode every
    PNG differently" and a golden fixture that quietly passed under a different
    Pillow would be worse than none.

    A null `pil` / `numpy` is "this tier declares no opinion", never a mismatch:
    a stdlib-tier report cannot fill those fields without importing what it is
    forbidden to import (kz_common.tool_block, kz_common.py:858). Reading it by
    IMPORT here would measure THIS process instead of the process that made the
    pixels, which is the whole distinction.
    """
    recorded = doc.get("tool") or {}
    drift, seen = [], 0
    for stage in DRIVEN:
        path = os.path.join(SCRATCH, "%s.json" % stage)
        if not os.path.exists(path):
            print("  %-10s report absent (the stage did not get that far)" % stage)
            continue
        tool = (load(path).get("tool") or {})
        seen += 1
        cells = []
        for field in FIELDS:
            want, got = recorded.get(field), tool.get(field)
            if want is None:
                cells.append("%s -" % field)
            elif got is None:
                cells.append("%s null (no opinion)" % field)
            elif got != want:
                cells.append("%s %s != %s" % (field, got, want))
                drift.append("%s: %s %s != recorded %s" % (stage, field, got, want))
            else:
                cells.append("%s %s" % (field, got))
        print("  %-10s %s" % (stage, ", ".join(cells)))
    return drift, seen


def reference_atlases(block):
    """The recorded Korean atlases, and WHICH SPELLING they came from.

    `kz_upload.build_atlas_urls` emits the same dict twice, as `substitutions`
    and as `urls` (kz_upload.py:730,739), because `repoint` opens one and
    `kz_verify` the other; `atlases` carries the rows, sha256 included. The
    backfilled Midwinter receipt predates that writer and carries `substitutions`
    as a LIST, so all three shapes are tolerated and the one used is printed --
    a comparison whose subject is ambiguous is a comparison nobody can audit.
    """
    rows = block.get("atlases")
    if isinstance(rows, list) and any(r.get("sha256") for r in rows if isinstance(r, dict)):
        return ([(r.get("sheet") or r.get("atlas_id"), r.get("sha256")) for r in rows
                 if isinstance(r, dict) and r.get("sha256")], "atlases[]")
    for name in ("urls", "substitutions"):
        value = block.get(name)
        pairs = []
        if isinstance(value, dict):
            pairs = [(None, url) for url in value.values()]
        elif isinstance(value, list):
            pairs = [(e.get("sheet"), e.get("korean_url")) for e in value
                     if isinstance(e, dict) and e.get("korean_url")]
        out = []
        for sheet, url in pairs:
            stem = str(url).rsplit("/", 1)[-1]
            if stem.endswith(".png"):
                stem = stem[:-4]
            if len(stem) == 64:
                out.append((sheet, stem))
        if out:
            return (out, "%s[] (sha256 read off the content-addressed R2 key)" % name)
    return ([], None)


def compare_atlases(doc):
    """(5) BYTE-FOR-BYTE against the receipt tier.

    atlas-urls.json was promoted OUT of run material precisely so it survives the
    run dir being gone (§3.1): left in `.am/` it is gitignored and dies with it,
    taking the only record of which R2 objects this scenario owns.
    """
    spec = (doc.get("atlas_urls_assertion") or {}).get("from") or ""
    rel, _, key = spec.partition("->")
    rel, key = rel.strip(), key.strip() or "atlas_urls"
    # The path is package-relative (`data/locks/<slug>.lock.json`), with the
    # workspace as the fallback so a future manifest may name either.
    lock_path = os.path.join(HERE, rel)
    if not os.path.exists(lock_path):
        lock_path = os.path.join(kc.WORKSPACE_ROOT, rel)
    if not os.path.exists(lock_path):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the receipt the atlas assertion names is not on disk", lock_path)
    block = (load(lock_path) or {}).get(key) or {}
    recorded, spelling = reference_atlases(block)
    if not recorded:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the receipt carries no recorded atlas sha256s",
                  "%s -> %s: neither atlases[] nor urls/substitutions yielded one. "
                  "A comparison over an empty reference passes without comparing."
                  % (lock_path, key))
    print("  reference : %s (%s -> %s)" % (spelling, os.path.basename(lock_path), key))

    produced = []
    report_path = os.path.join(SCRATCH, "recompose.json")
    if os.path.exists(report_path):
        for sheet in ((load(report_path).get("results") or {}).get("sheets") or []):
            if sheet.get("sha256"):
                produced.append((sheet.get("sheet"), sheet.get("sha256")))
    if not produced:
        atlases_dir = os.path.join(SCRATCH, "atlases")
        for name in sorted(os.listdir(atlases_dir) if os.path.isdir(atlases_dir) else []):
            if name.endswith(".png"):
                produced.append((os.path.splitext(name)[0],
                                 kc.sha256_file(os.path.join(atlases_dir, name))))

    by_sheet = dict((s, d) for s, d in produced if s)
    made = set(d for _s, d in produced)
    findings = []
    for sheet, digest in recorded:
        got = by_sheet.get(sheet)
        if got is None and digest in made:
            print("  %-14s %s  MATCH (by digest; sheet name not carried)"
                  % (sheet or "-", digest[:16]))
            continue
        if got is None:
            findings.append("%s: recorded %s, produced nothing under that sheet"
                            % (sheet, digest[:16]))
            print("  %-14s %s  MISSING" % (sheet or "-", digest[:16]))
        elif got != digest:
            findings.append("%s: produced %s, the receipt records %s"
                            % (sheet, got[:16], digest[:16]))
            print("  %-14s %s  DRIFT (produced %s)"
                  % (sheet, digest[:16], got[:16]))
        else:
            print("  %-14s %s  MATCH" % (sheet, digest[:16]))
    # Extra means a sheet the receipt does not name AT ALL. Keyed on the sheet
    # name first and the digest only as a fallback, because a DRIFTED sheet has a
    # digest the receipt does not carry -- scoring it on the digest alone reported
    # the same one sheet twice, once as drift and once as a surplus that does not
    # exist.
    known_sheets = set(s for s, _d in recorded if s)
    known_digests = set(d for _s, d in recorded)
    for sheet, digest in produced:
        if sheet in known_sheets or digest in known_digests:
            continue
        findings.append("%s: produced but not in the receipt" % sheet)
    return findings


def main():
    doc = load(MANIFEST)
    print("golden : (4) interpreter triple, BY REPORT")
    drift, seen = compare_tool(doc)
    if drift:
        print("")
        print("golden : SKIPPED (exit %d) -- the art chain is UNTESTED on this "
              "interpreter" % SKIPPED)
        for line in drift:
            print("         %s" % line)
        print("         This is a SKIP, never a pass, and never a 13: a missing "
              "corpus is a")
        print("         restore job, an interpreter mismatch is a pin job. The "
              "per-module")
        print("         pytest suites are what carry coverage while it fires.")
        return SKIPPED
    # (5) HAS A PRECONDITION AND IT IS NOT AN ASSUMPTION: the three atlases are
    # `recompose`'s output, so a chain that stopped earlier has no subject for
    # the comparison. Reporting "MISSING" for all three there would dress a
    # stage refusal up as a byte regression -- two different first moves, and
    # the louder one is wrong.
    if seen != len(DRIVEN):
        print("  (%d of %d driven stages reported; the chain did not complete, so"
              % (seen, len(DRIVEN)))
        print("   the atlas comparison has no subject and is NOT run)")
        return 0
    print("golden : (5) atlas sha256, BYTE-FOR-BYTE")
    findings = compare_atlases(doc)
    if findings:
        print("")
        print("golden : exit %d -- the chain ran and produced DIFFERENT BYTES"
              % kc.EXIT_DRIFT)
        for line in findings:
            print("         %s" % line)
        return kc.EXIT_DRIFT
    print("")
    print("golden : PASS -- the arithmetic chain is byte-stable against the "
          "fixture.")
    return 0


try:
    raise SystemExit(main())
except kc.KzRefusal as exc:
    sys.stderr.write("%s\n" % exc)
    raise SystemExit(exc.code)
PY
}

do_golden() {
  local fixture="${1:-midwinter}"
  GOLDEN_KEEP="${2:-no}"
  local manifest="${HERE}/data/golden/${fixture}.manifest.json"
  if [[ ! -f "${manifest}" ]]; then
    die "exit 13: golden inputs missing -- ${manifest}"
    die "  This is a RESTORE JOB, not an environment difference: a missing corpus"
    die "  is exit 13 and an interpreter mismatch is exit ${EXIT_GOLDEN_SKIPPED}, and merging"
    die "  the two is what §5.8's split exists to prevent."
    return 13
  fi

  # (0) MINT the run id and the scratch run dir. The id is what every seeded
  # artifact is labelled with and what compute_consumable compares against, so it
  # has to be unique per run -- stamp plus pid, because two `golden` runs in the
  # same second would otherwise share a seed.
  local stamp run_id scratch
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  run_id="golden-${fixture}-${stamp}-$$"
  scratch="${WORKSPACE}/.am/koreanize/.golden/${fixture}-${stamp}-$$"
  if [[ -e "${scratch}" ]]; then
    die "exit ${EXIT_REFUSED}: ${scratch} already exists"
    return ${EXIT_REFUSED}
  fi

  GOLDEN_SCRATCH="${scratch}"
  # bash has no `finally`; this is it. INT and TERM as well as EXIT, because a
  # ^C during a 174 MB copy would otherwise leave the seed behind -- and a seed
  # left behind next to a real run dir is the one thing §5.8 spends four
  # properties making harmless.
  trap golden_cleanup EXIT INT TERM

  local rc handoff
  golden_seed "${manifest}" "${scratch}" "${run_id}" "${fixture}"
  rc=$?
  if [[ ${rc} -ne 0 ]]; then
    die "golden: refused during bind/cover/seed (exit ${rc}); nothing was driven"
    return ${rc}
  fi

  handoff="${scratch}/.golden-run"
  if [[ ! -f "${handoff}" ]]; then
    die "exit ${EXIT_REFUSED}: the seeder returned 0 but wrote no ${handoff}"
    return ${EXIT_REFUSED}
  fi
  local mask_reference slug driven
  mask_reference="$(sed -n 's/^MASK_REFERENCE=//p' "${handoff}")"
  slug="$(sed -n 's/^SLUG=//p' "${handoff}")"
  driven="$(sed -n 's/^DRIVEN=//p' "${handoff}")"

  # (3) DRIVE the five stages IN ORDER, through this very dispatcher -- so each
  # one takes the predecessor gate, the interpreter tier and the flag families it
  # would take from an operator. --run-dir is argv, so config_sha256 is untouched
  # and the init gate's bound_sha256 is not invalidated; the real
  # .am/koreanize/<slug>/ is never opened.
  local stage
  for stage in ${driven}; do
    local -a extra=()
    if [[ "${stage}" == "mask" && -n "${mask_reference}" ]]; then
      # kz_mask's M4 defaults its reference to a manifest already in the run dir,
      # and a fresh scratch dir has none -- without this it passes with "nothing
      # to compare against" and the re-derivation claim goes unasserted.
      extra=(--compare-to "${mask_reference}")
    fi
    printf '\n=== golden: %s ===\n' "${stage}"
    "${HERE}/koreanize.sh" "${stage}" --slug "${slug}" --run-dir "${scratch}" \
      "${extra[@]+"${extra[@]}"}"
    rc=$?
    if [[ ${rc} -ne 0 ]]; then
      printf '\n'
      die "golden: \`${stage}\` exited ${rc} -- the chain stopped there."
      # The interpreter comparison still runs over the reports that DO exist: a
      # mismatch OUTRANKS a stage failure, because a failure under an interpreter
      # the fixture was never pinned to is not evidence about the code. When the
      # triple matches, the stage's own code is passed through verbatim.
      golden_compare "${manifest}" "${scratch}" ${driven}
      local crc=$?
      if [[ ${crc} -eq ${EXIT_GOLDEN_SKIPPED} ]]; then
        return ${EXIT_GOLDEN_SKIPPED}
      fi
      return ${rc}
    fi
  done

  printf '\n'
  golden_compare "${manifest}" "${scratch}" ${driven}
  return $?
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
  local pytest_only="no" fixture="midwinter" keep_scratch="no"
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
      --keep-scratch) keep_scratch="yes"; shift ;;
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
    golden)   do_golden "${fixture}" "${keep_scratch}"; return $? ;;
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
