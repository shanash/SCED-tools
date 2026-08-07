#!/usr/bin/env bash
# resolve-rebase-with-ai.sh
#
# The claude-driven rebase stage. Design: .am/claude-driven-rebase-deploy/design.md
# §4.2 (this CLI), §4.3 (the invocation and the grant), §5.4 (the shadow seed),
# §5.8 (blast radius and timeout containment).
#
# The driver (daily-sync-local.sh) calls this INSTEAD of its own `git rebase` when
# the overlap gate trips and the repo is listed in SCED_SYNC_AI_RESOLVE. It is a
# black box with an exit-code contract, exactly as check-upstream-overlap.sh is:
# the driver keeps its structural purity (no `git add`, no `git commit`, no
# `rebase --continue`, no `-X ours`, no `rerere`) because every one of those verbs
# lives here or in ai-rebase-verify.py instead.
#
# WHAT THIS DELEGATES, IN ONE SENTENCE
#   It sends this repository's conflicting content to Anthropic's API and lets an
#   agent holding Edit / Write / git rebase / git add / git checkout drive a real
#   rebase inside a detached, throwaway worktree under .local-sync/scratch/.
#
# WHAT IT WILL NEVER DO
#   Push. Fetch. Touch a local ref other than the run-scoped seed pin. Run
#   `git reset` or `git clean` -- not even to recover from a timeout: on any
#   non-zero rc it runs `rebase --abort` and stops. The shell owns every push, and
#   the only pushes in this workspace remain the two in daily-sync-local.sh.
#
#   The agent's grant is written out in full below rather than inherited.
#   `--setting-sources user` is what makes "written out" true: the workspace's
#   .claude/settings.local.json allow-lists `git reset`, `git clean` and
#   `git fetch`, and this stage denies all three. That file is therefore a
#   security-relevant file which the nightly deliberately does not load.
#
# Usage:
#   resolve-rebase-with-ai.sh --repo <SCED|SCED-downloads>
#                             --worktree DIR        (must be under .local-sync/scratch/)
#                             --merge-base SHA --fork-ref SHA --upstream-ref SHA
#                             --overlap FILE --fork-paths FILE --upstream-paths FILE
#                             --run-dir DIR
#                             [--claude-bin PATH]
#                             [--model M] [--fallback-model M] [--effort E]
#                             [--timeout 780] [--stage-budget 900] [--max-budget-usd 10]
#                             [--r2-floor-pct 98] [--attest-max-bytes 65536]
#                             [--replay DIR]   re-run decide+verify on a recorded run
#                             [--no-attest]    verify only, create no commit
#                             [--keep-seed]    leave the seed worktree for debugging
#                             [--quiet] [--help]
#
# Exit codes:
#    0  rebase driven, tree verified, attestation commit created
#    1  usage error
#   11  stop by policy (abstain / low confidence / coverage / rule failure)
#   62  case-only path collision, or a duplicate TTS object GUID
#   64  shadow seed or overlap classification failed
#   65  claude unavailable, credential failure, non-zero exit, or timeout
#   66  attestation manifest invalid (schema / coverage / binding)
#   67  the resolved tree failed verification

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXIT_USAGE=1
EXIT_STOP=11
EXIT_CASE=62
EXIT_SEED=64
EXIT_CLAUDE=65
EXIT_MANIFEST=66
EXIT_VERIFY=67

# --------------------------------------------------------------------- options

REPO=""; WT=""; MERGE_BASE=""; FORK_SHA=""; UPSTREAM_SHA=""
OVERLAP_IN=""; FORK_PATHS_IN=""; UPSTREAM_PATHS_IN=""; RUN_DIR=""
CLAUDE_BIN=""; REPLAY=""; NO_ATTEST=false; KEEP_SEED=false; QUIET=false

usage() { sed -n '/^# Usage:/,/^#   67/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)            REPO="${2:-}"; shift 2 ;;
    --worktree)        WT="${2:-}"; shift 2 ;;
    --merge-base)      MERGE_BASE="${2:-}"; shift 2 ;;
    --fork-ref)        FORK_SHA="${2:-}"; shift 2 ;;
    --upstream-ref)    UPSTREAM_SHA="${2:-}"; shift 2 ;;
    --overlap)         OVERLAP_IN="${2:-}"; shift 2 ;;
    --fork-paths)      FORK_PATHS_IN="${2:-}"; shift 2 ;;
    --upstream-paths)  UPSTREAM_PATHS_IN="${2:-}"; shift 2 ;;
    --run-dir)         RUN_DIR="${2:-}"; shift 2 ;;
    --claude-bin)      CLAUDE_BIN="${2:-}"; shift 2 ;;
    --model)           SCED_SYNC_AI_MODEL="${2:-}"; shift 2 ;;
    --fallback-model)  SCED_SYNC_AI_FALLBACK_MODEL="${2:-}"; shift 2 ;;
    --effort)          SCED_SYNC_AI_EFFORT="${2:-}"; shift 2 ;;
    --timeout)         SCED_SYNC_AI_TIMEOUT="${2:-}"; shift 2 ;;
    --stage-budget)    SCED_SYNC_AI_STAGE_BUDGET="${2:-}"; shift 2 ;;
    --max-budget-usd)  SCED_SYNC_AI_MAX_BUDGET_USD="${2:-}"; shift 2 ;;
    --r2-floor-pct)    SCED_SYNC_R2_FLOOR_PCT="${2:-}"; shift 2 ;;
    --attest-max-bytes) SCED_SYNC_AI_ATTEST_MAX_BYTES="${2:-}"; shift 2 ;;
    --replay)          REPLAY="${2:-}"; shift 2 ;;
    --no-attest)       NO_ATTEST=true; shift ;;
    --keep-seed)       KEEP_SEED=true; shift ;;
    --quiet)           QUIET=true; shift ;;
    --help|-h)         usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit "${EXIT_USAGE}" ;;
  esac
done

# ------------------------------------------------------------ environment file
#
# The driver already sourced this and exports its variables, so under the nightly
# this is a no-op. A standalone rehearsal run (design S16) reaches the same state
# by loading it here. The mode check is the driver's, verbatim and fatal: a
# secrets file the rest of the machine can read is not one to source.
ENV_FILE="${SCED_SYNC_ENV_FILE:-${HOME}/.config/sced-sync/env}"
if [[ -r "${ENV_FILE}" ]]; then
  env_perm="$(stat -f '%Lp' "${ENV_FILE}" 2>/dev/null || echo '???')"
  if [[ "${env_perm}" != "600" ]]; then
    echo "ERROR: ${ENV_FILE} must be mode 600, found ${env_perm}" >&2
    exit "${EXIT_USAGE}"
  fi
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

# ------------------------------------------------------------------ parameters

AI_MODEL="${SCED_SYNC_AI_MODEL:-claude-opus-5}"
AI_FALLBACK_MODEL="${SCED_SYNC_AI_FALLBACK_MODEL:-claude-opus-4-8}"
AI_EFFORT="${SCED_SYNC_AI_EFFORT:-high}"
AI_TIMEOUT="${SCED_SYNC_AI_TIMEOUT:-780}"
AI_STAGE_BUDGET="${SCED_SYNC_AI_STAGE_BUDGET:-900}"
AI_MAX_BUDGET_USD="${SCED_SYNC_AI_MAX_BUDGET_USD:-10}"
AI_ATTEST_MAX_BYTES="${SCED_SYNC_AI_ATTEST_MAX_BYTES:-65536}"
R2_FLOOR_PCT="${SCED_SYNC_R2_FLOOR_PCT:-98}"

STARTED_EPOCH="$(date +%s)"
RUN_STAMP="$(date +%Y%m%d-%H%M%S)"
SEED_WT=""
SEED_REF=""
CLAUDE_RC=0
SESSION_ID=""

log() { printf '%s ai: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }
say() { [[ "${QUIET}" == true ]] || log "$@"; }

# ------------------------------------------------------------------ validation

for req in REPO WT MERGE_BASE FORK_SHA UPSTREAM_SHA OVERLAP_IN RUN_DIR; do
  if [[ -z "${!req}" ]]; then
    echo "ERROR: missing required argument for ${req}" >&2
    usage >&2
    exit "${EXIT_USAGE}"
  fi
done
case "${REPO}" in
  SCED|SCED-downloads) ;;
  *) echo "ERROR: unknown repo '${REPO}'" >&2; exit "${EXIT_USAGE}" ;;
esac

# Re-asserted here rather than trusted from the caller (design §7.1). Every script
# in this pipeline that can write states the boundary itself, so none of them is
# relying on another to have done it.
case "${WT}" in
  */.local-sync/scratch/*) ;;
  *) echo "refusing to operate outside a scratch worktree: ${WT}" >&2
     exit "${EXIT_USAGE}" ;;
esac
if [[ ! -d "${WT}" ]]; then
  echo "ERROR: worktree does not exist: ${WT}" >&2
  exit "${EXIT_USAGE}"
fi
if [[ ! -f "${OVERLAP_IN}" ]]; then
  echo "ERROR: overlap file does not exist: ${OVERLAP_IN}" >&2
  exit "${EXIT_USAGE}"
fi

mkdir -p "${RUN_DIR}"

# ------------------------------------------------------------------- git + time

# Identical to daily-sync-local.sh's g() (`:222-231`), including the deliberate
# omission of core.ignorecase: forcing it false on this case-insensitive volume
# manufactures phantom changes, and the real exposure is caught by the verifier's
# case-collision detector instead.
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

# timeout(1) is not installed (no coreutils). Identical to the driver's
# with_timeout (`:236-251`), including the closed watchdog stdout: a background
# process that inherits a command substitution's pipe holds it open for the whole
# timeout even after the real command finished.
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

elapsed() { echo $(( $(date +%s) - STARTED_EPOCH )); }

# The stage is INERT on overrun, by design (§5.9): korean is untouched and the
# night degrades to today's stop. Better a wasted API call than a push that lands
# inside the GHA fallback's window.
budget_check() {
  local phase="$1" e; e="$(elapsed)"
  if [[ "${e}" -gt "${AI_STAGE_BUDGET}" ]]; then
    fail "${EXIT_CLAUDE}" "stage budget exceeded before ${phase}: ${e}s > ${AI_STAGE_BUDGET}s"
  fi
}

fail() {
  local code="$1"; shift
  log "STOP (exit ${code}): $*"
  printf '%s\n' "$*" > "${RUN_DIR}/stage-error.txt"
  exit "${code}"
}

cleanup() {
  if [[ "${KEEP_SEED}" != true && -n "${SEED_WT}" && -e "${SEED_WT}" ]]; then
    g "${WT}" worktree remove --force "${SEED_WT}" 2>/dev/null \
      || log "cleanup: could not remove the seed worktree ${SEED_WT}"
  fi
  if [[ -n "${SEED_REF}" ]]; then
    g "${WT}" update-ref -d "${SEED_REF}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ------------------------------------------------------------------- inputs

cp "${OVERLAP_IN}" "${RUN_DIR}/overlap.txt"
if [[ -n "${FORK_PATHS_IN}" && -f "${FORK_PATHS_IN}" ]]; then
  cp "${FORK_PATHS_IN}" "${RUN_DIR}/fork-changed.txt"
fi
if [[ -n "${UPSTREAM_PATHS_IN}" && -f "${UPSTREAM_PATHS_IN}" ]]; then
  cp "${UPSTREAM_PATHS_IN}" "${RUN_DIR}/upstream-changed.txt"
fi

OVERLAP_COUNT="$(wc -l < "${RUN_DIR}/overlap.txt" | tr -d ' ')"
say "repo=${REPO} overlap=${OVERLAP_COUNT} run-dir=${RUN_DIR}"

# The status digests of all three primary checkouts, taken BEFORE the agent runs.
#
# Deliberately outside RUN_DIR: the agent holds `--add-dir "${RUN_DIR}"` and could
# otherwise rewrite the very baseline check 9 compares against. ai-rebase-verify.py
# copies the before/after pair into RUN_DIR afterwards as the artifact.
WORKSPACE_ROOT="${SCED_SYNC_WORKSPACE:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
PRE_STATUS="$(mktemp "${TMPDIR:-/tmp}/sced-ai-prestatus.XXXXXX")"
: > "${PRE_STATUS}"
for r in SCED SCED-downloads SCED-tools; do
  if [[ -d "${WORKSPACE_ROOT}/${r}/.git" || -f "${WORKSPACE_ROOT}/${r}/.git" ]]; then
    printf '%s  %s\n' \
      "$(g "${WORKSPACE_ROOT}/${r}" status --porcelain | shasum -a 256 | cut -d' ' -f1)" \
      "${WORKSPACE_ROOT}/${r}" >> "${PRE_STATUS}"
  fi
done
trap 'cleanup; rm -f "${PRE_STATUS}"' EXIT

# ---------------------------------------------------------------- replay mode
#
# Re-run decide + verify against a recorded run's artifacts. No claude, no
# credential, no cost. This is how a stopped night is re-adjudicated after a rule
# is changed, and how S16's rehearsal is repeated deterministically.
if [[ -n "${REPLAY}" ]]; then
  if [[ ! -d "${REPLAY}" ]]; then
    echo "ERROR: --replay directory does not exist: ${REPLAY}" >&2
    exit "${EXIT_USAGE}"
  fi
  say "replay: reusing ${REPLAY}"
  for f in classes.json overlap.txt claude-envelope.json manifest.json; do
    if [[ -f "${REPLAY}/${f}" ]]; then cp "${REPLAY}/${f}" "${RUN_DIR}/${f}"; fi
  done
  if [[ -d "${REPLAY}/material" && ! -d "${RUN_DIR}/material" ]]; then
    cp -R "${REPLAY}/material" "${RUN_DIR}/material"
  fi
fi

# --------------------------------------------------------------- shadow seed
#
# A deterministic reference tree, produced by the shell, never pushed, never the
# result. It does three jobs (§5.4) and is worth adopting for any one of them: it
# makes the silently-auto-merged class enumerable BEFORE the agent runs, it bounds
# the review surface to `git diff <seed> <result>`, and it supports the hard
# containment assertion of check 2.
#
# `--strategy-option=ours` in REBASE semantics favours the new base -- upstream --
# which is the opposite of the 2026-06-27 blanket keep-ours that had to be undone,
# and it is the fail-safe direction: a seed failure loses the fork's change rather
# than producing garbage.
if [[ -z "${REPLAY}" ]]; then
  SEED_WT="${RUN_DIR}/seed"
  g "${WT}" worktree prune
  if [[ -e "${SEED_WT}" ]]; then
    g "${WT}" worktree remove --force "${SEED_WT}" 2>/dev/null || true
  fi
  if ! g "${WT}" worktree add --detach "${SEED_WT}" "${FORK_SHA}" >/dev/null 2>&1; then
    fail "${EXIT_SEED}" "could not create the seed worktree at ${SEED_WT}"
  fi
  if ! g "${SEED_WT}" rebase --strategy-option=ours --empty=drop "${UPSTREAM_SHA}" \
        > "${RUN_DIR}/seed-rebase.log" 2>&1; then
    g "${SEED_WT}" rebase --abort >/dev/null 2>&1 || true
    fail "${EXIT_SEED}" "the shadow seed did not complete: $(tail -5 "${RUN_DIR}/seed-rebase.log" | tr '\n' ' ')"
  fi
  SEED_COMMIT="$(g "${SEED_WT}" rev-parse HEAD)"
  SEED_TREE="$(g "${SEED_WT}" rev-parse 'HEAD^{tree}')"
  SEED_REPLAYED="$(g "${SEED_WT}" rev-list --count "${UPSTREAM_SHA}..HEAD")"
  say "seed: commit ${SEED_COMMIT:0:12} tree ${SEED_TREE:0:12} (${SEED_REPLAYED} commit(s) replayed)"
  if [[ "${SEED_REPLAYED}" -eq 0 ]]; then
    fail "${EXIT_SEED}" "the seed replayed 0 commits -- the fork's entire divergence vanished"
  fi

  # Pin the seed so its objects stay reachable once the worktree is gone. The
  # prompt tells the agent the seed worktree has already been deleted, and that
  # has to be true: it must not be able to cd into a tree it might mistake for
  # its own. The ref is run-scoped and cleanup() removes it.
  SEED_REF="refs/sced-ai/seed/${RUN_STAMP}"
  g "${WT}" update-ref "${SEED_REF}" "${SEED_COMMIT}"

  # ------------------------------------------------------------- classify
  budget_check "classify"
  if ! python3 "${SCRIPT_DIR}/ai-overlap-classify.py" \
        --repo "${REPO}" --worktree "${WT}" \
        --merge-base "${MERGE_BASE}" --fork-ref "${FORK_SHA}" --upstream-ref "${UPSTREAM_SHA}" \
        --overlap "${RUN_DIR}/overlap.txt" --run-dir "${RUN_DIR}" \
        --mode drive --result-ref "${SEED_COMMIT}" --seed-commit "${SEED_COMMIT}" \
        --fork-paths "${RUN_DIR}/fork-changed.txt" \
        --upstream-paths "${RUN_DIR}/upstream-changed.txt" \
        > "${RUN_DIR}/classify.log" 2>&1; then
    fail "${EXIT_SEED}" "classification failed: $(tail -5 "${RUN_DIR}/classify.log" | tr '\n' ' ')"
  fi
  [[ "${QUIET}" == true ]] || cat "${RUN_DIR}/classify.log"

  if [[ "${KEEP_SEED}" != true ]]; then
    g "${WT}" worktree remove --force "${SEED_WT}" 2>/dev/null || true
    SEED_WT=""
  fi
else
  SEED_COMMIT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("seed_commit") or "")' "${RUN_DIR}/classes.json")"
  SEED_TREE="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("seed_tree") or "")' "${RUN_DIR}/classes.json")"
  [[ -n "${SEED_TREE}" ]] || fail "${EXIT_SEED}" "--replay: ${RUN_DIR}/classes.json carries no seed_tree"
fi

# ----------------------------------------------------------------- the agent

# Runtime resolution, never a hardcoded nvm path. `sort -V` and not plain
# `tail -1`: lexical order ranks v9.x above v20.18.1.
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

# The whole blast radius, written out rather than described -- and rather than
# inherited. Three of the six write verbs .claude/settings.local.json allow-lists
# are ones (b) genuinely needs, which makes that file MORE dangerous to shrug at,
# not less. The three that must stay denied -- reset, clean, fetch -- are all in it.
AI_ALLOW="Read,Grep,Glob,Edit,Write,\
Bash(git status:*),Bash(git diff:*),Bash(git show:*),Bash(git log:*),\
Bash(git cat-file:*),Bash(git ls-tree:*),Bash(git ls-files:*),Bash(git rev-parse:*),\
Bash(git rev-list:*),Bash(git merge-base:*),Bash(git merge-file:*),Bash(git grep:*),\
Bash(git hash-object:*),Bash(git diff-tree:*),Bash(git blame:*),\
Bash(git rebase:*),Bash(git add:*),Bash(git checkout:*),Bash(git restore:*),\
Bash(git cherry-pick --continue),Bash(git cherry-pick --abort)"

AI_DENY="NotebookEdit,WebFetch,WebSearch,Task,\
Bash(git push:*),Bash(git remote:*),Bash(git fetch:*),Bash(git pull:*),\
Bash(git reset:*),Bash(git clean:*),Bash(git worktree:*),Bash(git config:*),\
Bash(git branch:*),Bash(git tag:*),Bash(git update-ref:*),Bash(git symbolic-ref:*),\
Bash(git filter-branch:*),Bash(git replace:*),Bash(git reflog:*),Bash(git stash:*),\
Bash(git submodule:*),Bash(git gc:*),Bash(git prune:*),Bash(git clone:*),\
Bash(gh:*),Bash(curl:*),Bash(wget:*),Bash(ssh:*),Bash(scp:*),Bash(rsync:*),\
Bash(sudo:*),Bash(launchctl:*),Bash(rm:*),Bash(mv:*),Bash(chmod:*),Bash(defaults:*),\
Bash(security:*),Bash(osascript:*)"

run_claude() {
  cd "${WT}" || return 1
  # GH_TOKEN/GITHUB_TOKEN are removed as belt-and-braces beside the Bash(gh:*)
  # denial: a grant mistake then still cannot authenticate a push. The webhook and
  # the mention go too -- the agent has no reason to reach the notification channel.
  # GIT_EDITOR/GIT_SEQUENCE_EDITOR: a `rebase --continue` that opens an editor
  # under launchd hangs until the watchdog kills it.
  # The author identity is pinned so the rebase's replay stays comparable, which
  # is what makes ai-rebase-verify.py check 1's author/subject sequence meaningful.
  env -u SCED_SYNC_DISCORD_WEBHOOK -u SCED_SYNC_MENTION \
      -u GH_TOKEN -u GITHUB_TOKEN \
      GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/true \
      GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true \
      GIT_AUTHOR_NAME='SCED daily sync' GIT_AUTHOR_EMAIL='sced-daily-sync@localhost' \
      GIT_COMMITTER_NAME='SCED daily sync' GIT_COMMITTER_EMAIL='sced-daily-sync@localhost' \
  "${CLAUDE_BIN}" \
      --print \
      --model            "${AI_MODEL}" \
      --fallback-model   "${AI_FALLBACK_MODEL}" \
      --effort           "${AI_EFFORT}" \
      --output-format    json \
      --json-schema      "${RUN_DIR}/manifest.schema.json" \
      --permission-mode  dontAsk \
      --setting-sources  user \
      --add-dir          "${RUN_DIR}" \
      --append-system-prompt "$(cat "${RUN_DIR}/policy.md")" \
      --allowedTools     "${AI_ALLOW}" \
      --disallowedTools  "${AI_DENY}" \
      --max-budget-usd   "${AI_MAX_BUDGET_USD}" \
      --no-chrome \
      --disable-slash-commands \
      < "${RUN_DIR}/prompt.md" \
      > "${RUN_DIR}/claude-envelope.json"
}

if [[ -z "${REPLAY}" ]]; then
  # `|| true` because resolve_claude returns 1 when it finds nothing, and under
  # `set -e` a bare failing assignment would exit 1 instead of the 65 this case
  # is contracted to produce.
  if [[ -z "${CLAUDE_BIN}" ]]; then
    CLAUDE_BIN="$(resolve_claude || true)"
  fi
  if [[ -z "${CLAUDE_BIN}" || ! -x "${CLAUDE_BIN}" ]]; then
    fail "${EXIT_CLAUDE}" "no usable claude binary (set SCED_SYNC_CLAUDE_BIN in ${ENV_FILE} or put claude on PATH)"
  fi
  # Watchdogged, and with stdin closed. Both matter: this probe runs before any
  # other bound in the stage, so a binary that blocks here -- because it is
  # waiting on a stdin it inherited, or because it is simply wedged -- would hang
  # the whole nightly with the workspace-wide lock held and nothing to kill it.
  # The version is informational; it is never worth blocking on.
  CLI_VERSION=""
  probe_version() {
    "${CLAUDE_BIN}" --version < /dev/null > "${RUN_DIR}/claude-version.txt" 2>/dev/null
  }
  set +e
  with_timeout 20 probe_version
  set -e
  if [[ -s "${RUN_DIR}/claude-version.txt" ]]; then
    CLI_VERSION="$(awk 'NR==1{print $1}' "${RUN_DIR}/claude-version.txt")"
  fi
  say "binary ${CLAUDE_BIN} (${CLI_VERSION:-version unknown}) model=${AI_MODEL} effort=${AI_EFFORT}"

  # Exactly one credential may survive into the child. An empty-but-set
  # ANTHROPIC_API_KEY still wins its precedence slot and authenticates with an
  # empty key, so the unused one is UNSET, never blanked.
  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    unset CLAUDE_CODE_OAUTH_TOKEN
  elif [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    unset ANTHROPIC_API_KEY
  else
    fail "${EXIT_CLAUDE}" "no credential: set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in ${ENV_FILE}"
  fi

  for f in prompt.md policy.md manifest.schema.json; do
    [[ -f "${RUN_DIR}/${f}" ]] || fail "${EXIT_SEED}" "classification produced no ${f}"
  done

  budget_check "the agent"
  CLAUDE_STARTED="$(date +%s)"
  set +e
  with_timeout "${AI_TIMEOUT}" run_claude
  CLAUDE_RC=$?
  set -e
  CLAUDE_ELAPSED=$(( $(date +%s) - CLAUDE_STARTED ))
  say "claude finished rc=${CLAUDE_RC} in ${CLAUDE_ELAPSED}s"

  if [[ "${CLAUDE_RC}" -ne 0 ]]; then
    # Never `git reset --hard`, never `git clean` -- not even here. If the abort
    # fails, the stage does nothing further: the worktree is disposable and the
    # driver's cleanup() removes it unconditionally on every exit path.
    #
    # with_timeout signals the subshell, not necessarily the grandchild, so an
    # agent that outlives its watchdog is possible. It is contained rather than
    # prevented: it can only write inside a worktree that is about to be deleted,
    # the driver never pushes on a non-zero stage rc, and `:601-604` recreates the
    # scratch worktree from scratch next run. A failed `worktree remove` is logged,
    # not fatal.
    g "${WT}" rebase --abort >/dev/null 2>&1 || true
    if [[ -e "$(g "${WT}" rev-parse --git-path rebase-merge)" || -e "$(g "${WT}" rev-parse --git-path rebase-apply)" ]]; then
      log "a rebase is still in progress in ${WT} after --abort; leaving it for cleanup() to destroy"
    fi
    reason="claude exited ${CLAUDE_RC}"
    if [[ "${CLAUDE_RC}" -eq 143 ]]; then
      reason="claude timed out after ${AI_TIMEOUT}s (rc 143)"
    fi
    fail "${EXIT_CLAUDE}" "${reason}"
  fi

  budget_check "decide"

  # Best-effort copy of the session transcript. Copied, never parsed: it is a
  # forensic convenience, and a parser here would be one more thing to keep in
  # step with an internal format.
  SESSION_ID="$(python3 -c 'import json,sys
try:    print(json.load(open(sys.argv[1])).get("session_id") or "")
except Exception: print("")' "${RUN_DIR}/claude-envelope.json" 2>/dev/null || echo '')"
  if [[ -n "${SESSION_ID}" && -d "${HOME}/.claude/projects" ]]; then
    src="$(find "${HOME}/.claude/projects" -name "${SESSION_ID}.jsonl" -maxdepth 2 2>/dev/null | head -1 || true)"
    [[ -n "${src}" ]] && cp "${src}" "${RUN_DIR}/session.jsonl" 2>/dev/null || true
  fi
fi

# ------------------------------------------------------------------- decide

set +e
python3 "${SCRIPT_DIR}/ai-overlap-decide.py" --run-dir "${RUN_DIR}" --mode drive \
  > "${RUN_DIR}/decide.log" 2>&1
DECIDE_RC=$?
set -e
[[ "${QUIET}" == true ]] || cat "${RUN_DIR}/decide.log"
case "${DECIDE_RC}" in
  0) ;;
  11) fail "${EXIT_STOP}"     "the rules stopped this run: $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("reason",""))' "${RUN_DIR}/decide.json" 2>/dev/null || echo 'see decide.json')" ;;
  66) fail "${EXIT_MANIFEST}" "the attestation manifest is invalid: $(tail -5 "${RUN_DIR}/decide.log" | tr '\n' ' ')" ;;
  *)  fail "${EXIT_MANIFEST}" "ai-overlap-decide.py failed (rc=${DECIDE_RC}): $(tail -5 "${RUN_DIR}/decide.log" | tr '\n' ' ')" ;;
esac

# ------------------------------------------------------------------- verify

VERIFY_ARGS=""
if [[ "${NO_ATTEST}" == true ]]; then VERIFY_ARGS="--no-commit"; fi

# Field-by-field extraction, never `eval` and never sourcing JSON -- the same rule
# the driver applies to its own state files (daily-sync-local.sh:571-576).
envelope_field() {
  python3 -c 'import json,sys
try:    v = json.load(open(sys.argv[1])).get(sys.argv[2])
except Exception: v = None
print("" if v is None else v)' "${RUN_DIR}/claude-envelope.json" "$1" 2>/dev/null || echo ''
}
COST="$(envelope_field total_cost_usd)"
TURNS="$(envelope_field num_turns)"
API_MS="$(envelope_field duration_api_ms)"
if [[ -z "${SESSION_ID}" ]]; then SESSION_ID="$(envelope_field session_id)"; fi

# Built as a word-split string rather than an array: /bin/bash here is 3.2.57 and
# expanding an empty array under `set -u` is an error there. Every value is
# numeric, so splitting on whitespace is safe.
OPT_ARGS=""
if [[ -n "${COST}" ]];   then OPT_ARGS="${OPT_ARGS} --total-cost-usd ${COST}"; fi
if [[ -n "${TURNS}" ]];  then OPT_ARGS="${OPT_ARGS} --num-turns ${TURNS}"; fi
if [[ -n "${API_MS}" ]]; then OPT_ARGS="${OPT_ARGS} --duration-api-ms ${API_MS}"; fi

set +e
# shellcheck disable=SC2086
python3 "${SCRIPT_DIR}/ai-rebase-verify.py" \
  --repo "${REPO}" --worktree "${WT}" --run-dir "${RUN_DIR}" \
  --merge-base "${MERGE_BASE}" --fork-ref "${FORK_SHA}" --upstream-ref "${UPSTREAM_SHA}" \
  --seed-tree "${SEED_TREE}" --seed-commit "${SEED_COMMIT}" \
  --pre-status "${PRE_STATUS}" \
  --run-stamp "${RUN_STAMP}" --model "${AI_MODEL}" --effort "${AI_EFFORT}" \
  --claude-bin "${CLAUDE_BIN}" --cli-version "${CLI_VERSION:-}" \
  --session-id "${SESSION_ID}" \
  ${OPT_ARGS} \
  --r2-floor-pct "${R2_FLOOR_PCT}" --attest-max-bytes "${AI_ATTEST_MAX_BYTES}" \
  ${VERIFY_ARGS} > "${RUN_DIR}/verify-stdout.log" 2>&1
VERIFY_RC=$?
set -e
[[ "${QUIET}" == true ]] || cat "${RUN_DIR}/verify-stdout.log"
case "${VERIFY_RC}" in
  0)  ;;
  62) fail "${EXIT_CASE}"   "case collision or duplicate TTS object GUID: $(tail -3 "${RUN_DIR}/verify-stdout.log" | tr '\n' ' ')" ;;
  *)  fail "${EXIT_VERIFY}" "the resolved tree failed verification: $(tail -3 "${RUN_DIR}/verify-stdout.log" | tr '\n' ' ')" ;;
esac

say "applied in $(elapsed)s -- $(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print("attest",(d.get("attest_commit") or "(none)")[:12],"verdicts",d.get("verdicts"))' "${RUN_DIR}/result.json" 2>/dev/null || echo 'result.json unreadable')"
exit 0
