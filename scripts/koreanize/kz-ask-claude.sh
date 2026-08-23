#!/usr/bin/env bash
# kz-ask-claude.sh
#
# The koreanize `claude` shim. Design: .am/standalone-scenario-koreanizer/design.md
# §4.3 (this invocation and the grant), §3.7 (one invocation per batch), §4.4
# rule 8 (the write set and the three pre/post digests this script observes).
#
# Ported from SCED-tools/scripts/resolve-rebase-with-ai.sh, which already runs
# `claude` unattended inside the nightly: run_claude() `:395-428` with its
# `env -u` scrub `:404-409`, with_timeout() `:200-210`, budget_check() `:217-222`,
# the version probe `:446-455`, the credential precedence rule `:460-466`, and the
# session transcript copy `:508-514`. koreanize reuses that contract rather than
# inventing one.
#
# WHAT THIS DELEGATES, IN ONE SENTENCE
#   It sends one batch of copied material to Anthropic's API and lets an agent
#   holding Read / Grep / Glob / Write produce a manifest inside ONE ask
#   directory, which is its entire world.
#
# WHAT IT WILL NEVER DO
#   Run git. Run any Bash verb at all -- the grant contains none, which is the
#   whole difference from the nightly's 21 `Bash(git ...)` entries. Touch a
#   repository worktree: cwd is ASK_DIR, and everything the agent needs was
#   COPIED into ASK_DIR/material/. Reaching SCED-downloads therefore produces a
#   `permission_denials` entry, which rule 6 turns into a stage refusal -- a
#   permission-layer property, not a filesystem one, since the process holds the
#   operator's rights throughout.
#
# THE THREE DIGESTS (rule 8 clause (c)) are taken here, around the invocation,
# and written to ASK_DIR/observed.json for kz_decide.py to compare. The set is
# deliberately WIDER than the nightly's, which observes only its run dir and the
# three checkouts: under that set a write to images-ko/, the glossary tree, or
# <run_dir> outside ASK_DIR is caught by nothing -- rule 6 catches an escape only
# when the CLI DENIED it, and a path the grant happens to allow is not denied.
#
# Usage:
#   kz-ask-claude.sh --ask-dir DIR            (ai/<stage>/<stamp>/batch-NN/)
#                    --stage S1..S7
#                    [--run-dir DIR]          (the koreanize run dir; digest 2)
#                    [--workspace DIR]        (the SCED workspace root; digest 1)
#                    [--readonly-tree DIR]    (repeatable; digest 3)
#                    [--model M] [--fallback-model M] [--effort E]
#                    [--timeout 780]          (wall clock of THIS invocation)
#                    [--call-budget 900]      (assemble + invoke, for ONE batch)
#                    [--max-budget-usd 10]
#                    [--claude-bin PATH] [--quiet] [--help]
#
# Exit codes (design §4.2; this script returns a subset and nothing else):
#    0  the invocation completed and claude-envelope.json is on disk
#    2  usage, or the launchd guard P0a
#   13  precondition -- the ask bundle is incomplete (prompt/policy/schema/inputs)
#   65  claude unavailable, unauthenticated, non-zero exit, or timed out (rc 143)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXIT_OK=0
EXIT_USAGE=2
EXIT_PRECONDITION=13
EXIT_CLAUDE=65

# --------------------------------------------------------------------- options

ASK_DIR=""; STAGE=""; RUN_DIR=""; WORKSPACE=""; CLAUDE_BIN=""; QUIET=false
READONLY_TREES=()

usage() { sed -n '/^# Usage:/,/^#   65/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ask-dir)         ASK_DIR="${2:-}"; shift 2 ;;
    --stage)           STAGE="${2:-}"; shift 2 ;;
    --run-dir)         RUN_DIR="${2:-}"; shift 2 ;;
    --workspace)       WORKSPACE="${2:-}"; shift 2 ;;
    --readonly-tree)   READONLY_TREES+=("${2:-}"); shift 2 ;;
    --model)           KOREANIZE_AI_MODEL="${2:-}"; shift 2 ;;
    --fallback-model)  KOREANIZE_AI_FALLBACK_MODEL="${2:-}"; shift 2 ;;
    --effort)          KOREANIZE_AI_EFFORT="${2:-}"; shift 2 ;;
    --timeout)         KOREANIZE_AI_TIMEOUT="${2:-}"; shift 2 ;;
    --call-budget)     KOREANIZE_AI_CALL_BUDGET="${2:-}"; shift 2 ;;
    --max-budget-usd)  KOREANIZE_AI_MAX_BUDGET_USD="${2:-}"; shift 2 ;;
    --claude-bin)      CLAUDE_BIN="${2:-}"; shift 2 ;;
    --quiet)           QUIET=true; shift ;;
    --help|-h)         usage; exit "${EXIT_OK}" ;;
    *) echo "Unknown argument: $1" >&2; exit "${EXIT_USAGE}" ;;
  esac
done

# ------------------------------------------------------------ environment file
#
# The mode check is the nightly's, verbatim and fatal: a secrets file the rest of
# the machine can read is not one to source. Machine-local locations live here and
# NEVER in scenario.json, which is hash-pinned and shared (§3.2).
ENV_FILE="${KOREANIZE_ENV_FILE:-${HOME}/.config/koreanize/env}"
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

AI_MODEL="${KOREANIZE_AI_MODEL:-claude-opus-5}"
AI_FALLBACK_MODEL="${KOREANIZE_AI_FALLBACK_MODEL:-claude-opus-4-8}"
AI_EFFORT="${KOREANIZE_AI_EFFORT:-high}"
AI_TIMEOUT="${KOREANIZE_AI_TIMEOUT:-780}"
AI_CALL_BUDGET="${KOREANIZE_AI_CALL_BUDGET:-900}"
AI_MAX_BUDGET_USD="${KOREANIZE_AI_MAX_BUDGET_USD:-10}"

STARTED_EPOCH="$(date +%s)"

log() { printf '%s kz-ask: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }
say() { [[ "${QUIET}" == true ]] || log "$@"; }

fail() {
  local code="$1"; shift
  log "STOP (exit ${code}): $*"
  if [[ -n "${ASK_DIR}" && -d "${ASK_DIR}" ]]; then
    printf '%s\n' "$*" > "${ASK_DIR}/stage-error.txt"
  fi
  exit "${code}"
}

# Plain integer seconds only, validated ONCE, before any work.
#
# `set -e` is suspended inside an `if` condition, so a non-integer knob makes a
# `-gt` test raise an arithmetic error that reads as FALSE: the guard would then
# pass silently for the rest of the run and the bound would be gone -- it fails
# OPEN. The other direction is CLAUDE.md's: `with_timeout` runs `sleep`, and an
# unparseable value makes `sleep` fail instantly, COLLAPSING the bound onto the
# call it was meant to protect. Neither is a default worth falling back to, so a
# bad value refuses here and names the knob that carries it.
require_seconds() {
  local flag="$1" name="$2" value="$3"
  if ! [[ "${value}" =~ ^[1-9][0-9]*$ ]]; then
    fail "${EXIT_USAGE}" "${flag} (${name}) is '${value}', not a positive integer of seconds"
  fi
}

require_seconds "--timeout"      "KOREANIZE_AI_TIMEOUT"      "${AI_TIMEOUT}"
require_seconds "--call-budget"  "KOREANIZE_AI_CALL_BUDGET"  "${AI_CALL_BUDGET}"

# ------------------------------------------------------------------ validation

for req in ASK_DIR STAGE; do
  if [[ -z "${!req}" ]]; then
    echo "ERROR: missing required argument for ${req}" >&2
    usage >&2
    exit "${EXIT_USAGE}"
  fi
done
case "${STAGE}" in
  S1|S2|S3|S4|S5|S6|S7) ;;
  *) echo "ERROR: unknown stage id '${STAGE}' (kz_config.AI_STAGE_MAP owns S1..S7)" >&2
     exit "${EXIT_USAGE}" ;;
esac
if [[ ! -d "${ASK_DIR}" ]]; then
  echo "ERROR: --ask-dir does not exist: ${ASK_DIR}" >&2
  exit "${EXIT_USAGE}"
fi
ASK_DIR="$(cd "${ASK_DIR}" && pwd)"

# P0a, the same predicate every koreanize module carries (kz_common.check_p0a,
# recompose-atlases.py:437-442). Detected DIRECTLY and never by tty: under any
# agent or CI harness no fd is a tty, so a tty test would refuse every legitimate
# run and the override would become the standing idiom.
if [[ "$(ps -o ppid= -p $$ | tr -d ' ')" == "1" || "${XPC_SERVICE_NAME:-0}" != "0" ]]; then
  if [[ "${KOREANIZE_ALLOW_LAUNCHD:-}" != "1" ]]; then
    echo "refusing to run under launchd; koreanize is an operator tool. Set" \
         "KOREANIZE_ALLOW_LAUNCHD=1 to record a deliberate override." >&2
    exit "${EXIT_USAGE}"
  fi
fi

# P0b's shell half: koreanize operates on the PRIMARY checkouts, so a run started
# inside the nightly's disposable worktree would write into a tree the driver
# deletes on completion. No override, deliberately (§5.9).
case "${ASK_DIR}" in
  */.local-sync/scratch/*)
    echo "refusing to operate inside the nightly's scratch worktree: ${ASK_DIR}" >&2
    exit "${EXIT_USAGE}" ;;
esac

WORKSPACE="${WORKSPACE:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"
RUN_DIR="${RUN_DIR:-$(cd "${ASK_DIR}/../../.." && pwd)}"

SCHEMA="${ASK_DIR}/${STAGE}.schema.json"
for f in prompt.md policy.md inputs.json "${STAGE}.schema.json"; do
  [[ -f "${ASK_DIR}/${f}" ]] || \
    fail "${EXIT_PRECONDITION}" "the ask bundle has no ${f}; kz_ask.py builds it"
done
mkdir -p "${ASK_DIR}/out"

# ------------------------------------------------------------------- watchdog
#
# timeout(1) is NOT used and must not be re-added. It is pure bash -- sleep +
# kill + wait -- exactly as with_timeout does in the nightly (`:200-210`), and for
# the reason CLAUDE.md states as the durable rule of this area: inserting a
# non-platform binary (Homebrew's `gtimeout`) into the ancestry of anything that
# opens /Volumes/PRO-G40 makes IT the TCC-responsible process, and only
# /bin/bash's grant exists. The closed watchdog stdout is load-bearing: a
# background process that inherits a command substitution's pipe holds it open
# for the whole timeout even after the real command finished.
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

# The invocation is INERT on overrun by design: nothing is written, the batch
# stays incomplete on disk, and kz_ask.py resumes it. `call_budget_s` is
# "assemble + invoke + decide, for ONE batch" (§3.2); the STAGE budget and the
# stage wall clock are kz_ask.py's, checked BETWEEN batches at exit 25.
budget_check() {
  local phase="$1" e; e="$(elapsed)"
  # The shape is re-checked here rather than trusted from startup, because this
  # guard is the only thing standing between a batch and an unbounded run: a
  # `-gt` against a non-integer is an arithmetic error that reads as false, so
  # the guard would vanish silently instead of stopping the batch. The two
  # refusals are kept apart on purpose -- one sends the operator to the knob,
  # the other to the batch.
  if ! [[ "${AI_CALL_BUDGET}" =~ ^[1-9][0-9]*$ ]]; then
    fail "${EXIT_USAGE}" "call budget is '${AI_CALL_BUDGET}', not a positive integer of seconds (--call-budget / KOREANIZE_AI_CALL_BUDGET)"
  fi
  if [[ "${e}" -gt "${AI_CALL_BUDGET}" ]]; then
    fail "${EXIT_CLAUDE}" "call budget exceeded before ${phase}: ${e}s > ${AI_CALL_BUDGET}s"
  fi
}

# ---------------------------------------------------------------- the digests
#
# One digest over a set of files, path-stable and locale-stable. `shasum` prints
# "<hash>  <path>", so sorting the lines is a total order that does not move with
# a locale -- the same reason the overlap gate runs everything under LC_ALL=C.
tree_digest() {
  local root="$1"; shift
  if [[ ! -d "${root}" ]]; then printf '%s\n' "$(printf '' | shasum -a 256 | cut -d' ' -f1)"; return; fi
  find "${root}" -type f "$@" -exec shasum -a 256 {} + 2>/dev/null \
    | LC_ALL=C sort | shasum -a 256 | cut -d' ' -f1
}

checkout_digest() {
  local r out=""
  for r in SCED SCED-downloads SCED-tools; do
    if [[ -e "${WORKSPACE}/${r}/.git" ]]; then
      out+="$(git -C "${WORKSPACE}/${r}" status --porcelain 2>/dev/null | shasum -a 256 | cut -d' ' -f1)  ${r}"$'\n'
    fi
  done
  printf '%s' "${out}" | shasum -a 256 | cut -d' ' -f1
}

readonly_digest() {
  local t out=""
  for t in ${READONLY_TREES[@]+"${READONLY_TREES[@]}"}; do
    out+="$(tree_digest "${t}")  ${t}"$'\n'
  done
  printf '%s' "${out}" | shasum -a 256 | cut -d' ' -f1
}

# Clause (b): everything inside ASK_DIR that is neither under out/ nor written by
# this script. A new or changed entry here is the agent writing outside its
# declared set even though the grant allowed it.
ask_listing() {
  find "${ASK_DIR}" -type f \
       -not -path "${ASK_DIR}/out/*" \
       ! -name 'claude-envelope.json' ! -name 'observed.json' \
       ! -name 'claude-version.txt' ! -name 'session.jsonl' \
       ! -name 'stage-error.txt' ! -name 'shim.log' \
       -exec shasum -a 256 {} + 2>/dev/null | LC_ALL=C sort
}

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

# ----------------------------------------------------------------- the agent
#
# Runtime resolution, never a hardcoded nvm path. `sort -V` and not plain
# `tail -1`: lexical order ranks v9.x above v20.18.1.
resolve_claude() {
  local c=""
  if [[ -n "${KOREANIZE_CLAUDE_BIN:-}" ]]; then
    [[ -x "${KOREANIZE_CLAUDE_BIN}" ]] && printf '%s\n' "${KOREANIZE_CLAUDE_BIN}"
    return
  fi
  c="$(command -v claude 2>/dev/null || true)"
  if [[ -z "${c}" ]]; then
    c="$(ls -d "${HOME}"/.nvm/versions/node/*/bin/claude 2>/dev/null | LC_ALL=C sort -V | tail -1 || true)"
  fi
  [[ -n "${c}" && -x "${c}" ]] && printf '%s\n' "${c}"
}

# The whole blast radius, written out rather than described -- and rather than
# inherited. `--setting-sources user` is what makes "written out" true: the
# workspace's .claude/settings.local.json allow-lists git verbs this stage must
# not hold, and it is deliberately not loaded.
#
# NO `Bash` VERB AT ALL. Where a stage needs code executed (S2's generator, S7's
# checker), the shell runs it AFTER the agent exits -- which is also rule 1 of
# §9.5: Claude Code emits data, never behaviour.
AI_ALLOW="Read,Grep,Glob,Write"
AI_DENY="Bash,Edit,NotebookEdit,WebFetch,WebSearch,Task"

run_claude() {
  # cwd is ASK_DIR, not a repository worktree. The agent cannot reach
  # SCED-downloads without producing a permission_denials entry.
  cd "${ASK_DIR}" || return 1
  # GH_TOKEN/GITHUB_TOKEN go as belt-and-braces beside the absent Bash grant: a
  # grant mistake then still cannot authenticate a push. The R2 credentials go
  # too -- koreanize uploads atlases with them and this stage has no business
  # holding them. The webhook and the mention go because the agent has no reason
  # to reach the notification channel. GIT_EDITOR/GIT_SEQUENCE_EDITOR: an editor
  # opening under an unattended run hangs until the watchdog kills it.
  env -u SCED_SYNC_DISCORD_WEBHOOK -u SCED_SYNC_MENTION \
      -u GH_TOKEN -u GITHUB_TOKEN \
      -u R2_ACCESS_KEY_ID -u R2_SECRET_ACCESS_KEY -u R2_ACCOUNT_ID \
      GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/usr/bin/true \
      GIT_EDITOR=true GIT_SEQUENCE_EDITOR=true \
  "${CLAUDE_BIN}" \
      --print \
      --model            "${AI_MODEL}" \
      --fallback-model   "${AI_FALLBACK_MODEL}" \
      --effort           "${AI_EFFORT}" \
      --output-format    json \
      --json-schema      "${SCHEMA}" \
      --permission-mode  dontAsk \
      --setting-sources  user \
      --add-dir          "${ASK_DIR}" \
      --append-system-prompt "$(cat "${ASK_DIR}/policy.md")" \
      --allowedTools     "${AI_ALLOW}" \
      --disallowedTools  "${AI_DENY}" \
      --max-budget-usd   "${AI_MAX_BUDGET_USD}" \
      --no-chrome \
      --disable-slash-commands \
      < "${ASK_DIR}/prompt.md" \
      > "${ASK_DIR}/claude-envelope.json"
}

if [[ -z "${CLAUDE_BIN}" ]]; then
  # `|| true` because resolve_claude returns 1 when it finds nothing, and under
  # `set -e` a bare failing assignment would exit 1 instead of the 65 this case
  # is contracted to produce.
  CLAUDE_BIN="$(resolve_claude || true)"
fi
if [[ -z "${CLAUDE_BIN}" || ! -x "${CLAUDE_BIN}" ]]; then
  fail "${EXIT_CLAUDE}" "no usable claude binary (set KOREANIZE_CLAUDE_BIN in ${ENV_FILE} or put claude on PATH)"
fi

# Watchdogged, and with stdin closed. Both matter: this probe runs before any
# other bound, so a binary that blocks here -- because it is waiting on a stdin it
# inherited, or because it is simply wedged -- would hang the stage with nothing
# to kill it. The version is informational; it is never worth blocking on.
CLI_VERSION=""
probe_version() {
  "${CLAUDE_BIN}" --version < /dev/null > "${ASK_DIR}/claude-version.txt" 2>/dev/null
}
set +e
with_timeout 20 probe_version
set -e
if [[ -s "${ASK_DIR}/claude-version.txt" ]]; then
  CLI_VERSION="$(awk 'NR==1{print $1}' "${ASK_DIR}/claude-version.txt")"
fi
say "stage=${STAGE} ask-dir=${ASK_DIR} binary=${CLAUDE_BIN} (${CLI_VERSION:-version unknown}) model=${AI_MODEL} effort=${AI_EFFORT}"

# Exactly one credential may survive into the child. An empty-but-set
# ANTHROPIC_API_KEY still wins its precedence slot and authenticates with an empty
# key, so the unused one is UNSET, never blanked.
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  unset CLAUDE_CODE_OAUTH_TOKEN
elif [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  unset ANTHROPIC_API_KEY
else
  fail "${EXIT_CLAUDE}" "no credential: set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in ${ENV_FILE}"
fi

# ------------------------------------------------------------- pre digests

PRE_LISTING="$(mktemp "${TMPDIR:-/tmp}/kz-ask-pre.XXXXXX")"
POST_LISTING="$(mktemp "${TMPDIR:-/tmp}/kz-ask-post.XXXXXX")"
trap 'rm -f "${PRE_LISTING}" "${POST_LISTING}"' EXIT

PRE_CHECKOUTS="$(checkout_digest)"
PRE_RUNDIR="$(tree_digest "${RUN_DIR}" -not -path "${ASK_DIR}/*")"
PRE_READONLY="$(readonly_digest)"
ask_listing > "${PRE_LISTING}"

# ------------------------------------------------------------- the invocation

budget_check "the agent"
CLAUDE_STARTED="$(date +%s)"
set +e
with_timeout "${AI_TIMEOUT}" run_claude
CLAUDE_RC=$?
set -e
CLAUDE_ELAPSED=$(( $(date +%s) - CLAUDE_STARTED ))
say "claude finished rc=${CLAUDE_RC} in ${CLAUDE_ELAPSED}s"

# ------------------------------------------------------------ post digests
#
# Taken even on a non-zero rc: a timed-out agent may have written before it was
# killed, and observed.json is the only record of what it touched.

POST_CHECKOUTS="$(checkout_digest)"
POST_RUNDIR="$(tree_digest "${RUN_DIR}" -not -path "${ASK_DIR}/*")"
POST_READONLY="$(readonly_digest)"
ask_listing > "${POST_LISTING}"

OUTSIDE=""
while IFS= read -r line; do
  [[ -z "${line}" ]] && continue
  path="${line#*  }"
  rel="${path#"${ASK_DIR}/"}"
  [[ -n "${OUTSIDE}" ]] && OUTSIDE+=", "
  OUTSIDE+="\"$(json_escape "${rel}")\""
done < <(LC_ALL=C comm -3 "${PRE_LISTING}" "${POST_LISTING}" | sed 's/^\t//')

WROTE=""
while IFS= read -r path; do
  [[ -z "${path}" ]] && continue
  rel="${path#"${ASK_DIR}/"}"
  [[ -n "${WROTE}" ]] && WROTE+=", "
  WROTE+="\"$(json_escape "${rel}")\""
done < <(find "${ASK_DIR}/out" -type f 2>/dev/null | LC_ALL=C sort)

cat > "${ASK_DIR}/observed.json" <<JSON
{
  "schema": 1,
  "ask_dir": "$(json_escape "$(basename "${ASK_DIR}")")",
  "stage": "${STAGE}",
  "wrote": [${WROTE}],
  "outside_out": [${OUTSIDE}],
  "digests": {
    "checkouts": {"path": "SCED,SCED-downloads,SCED-tools", "pre": "${PRE_CHECKOUTS}", "post": "${POST_CHECKOUTS}"},
    "run_dir": {"path": "$(json_escape "${RUN_DIR}") excluding ASK_DIR", "pre": "${PRE_RUNDIR}", "post": "${POST_RUNDIR}"},
    "read_only": {"path": "$(json_escape "${READONLY_TREES[*]-}")", "pre": "${PRE_READONLY}", "post": "${POST_READONLY}"}
  },
  "cli_version": "$(json_escape "${CLI_VERSION}")",
  "claude_rc": ${CLAUDE_RC},
  "elapsed_s": ${CLAUDE_ELAPSED}
}
JSON

if [[ "${CLAUDE_RC}" -ne 0 ]]; then
  reason="claude exited ${CLAUDE_RC}"
  if [[ "${CLAUDE_RC}" -eq 143 ]]; then
    reason="claude timed out after ${AI_TIMEOUT}s (rc 143)"
  fi
  fail "${EXIT_CLAUDE}" "${reason}"
fi

# Best-effort copy of the session transcript. Copied, never parsed: it is a
# forensic convenience, and a parser here would be one more thing to keep in step
# with an internal format.
SESSION_ID="$(sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  "${ASK_DIR}/claude-envelope.json" 2>/dev/null | head -1 || true)"
if [[ -n "${SESSION_ID}" && -d "${HOME}/.claude/projects" ]]; then
  src="$(find "${HOME}/.claude/projects" -name "${SESSION_ID}.jsonl" -maxdepth 2 2>/dev/null | head -1 || true)"
  [[ -n "${src}" ]] && cp "${src}" "${ASK_DIR}/session.jsonl" 2>/dev/null || true
fi

if [[ ! -s "${ASK_DIR}/claude-envelope.json" ]]; then
  fail "${EXIT_CLAUDE}" "claude exited 0 but wrote no envelope"
fi

say "wrote ${ASK_DIR}/claude-envelope.json and observed.json in $(elapsed)s"
exit "${EXIT_OK}"
