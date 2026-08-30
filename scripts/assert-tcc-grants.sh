#!/bin/bash
# assert-tcc-grants.sh -- pre-lock assertion that the nightly's self-responsible
# leaves still hold the macOS TCC grant they will need on /Volumes/PRO-G40.
#
# WHAT THIS IS FOR. A TCC grant is keyed to path + cdhash. `brew upgrade` moves
# the Cellar path, so the grant stops matching and the binary is refused -- or,
# under launchd where nobody can answer, prompts and wedges. On 2026-08-27 one
# upgrade took git 2.53.0 -> 2.55.0 and gh 2.87.3 -> 2.98.0 and cost two nights:
# 08-28 both agents died at the wrapper's 60 s git probe (exit 6, nothing ran),
# 08-29 SCED-downloads reached the release stage and hung 120 s on the one gh
# call that reads a file off the volume, exiting 70 AFTER korean was force-pushed.
# This is the SCED_SYNC_TTSMM_SHA256 pattern moved one stage earlier: assert the
# identity of a grant-holding leaf before it runs, so a grant-invalidating
# replacement becomes a named refusal instead of a wedge.
#
# WHY THE LEAVES AND NOT THE ANCESTRY. The durable rule is "never insert a
# non-platform binary into the ancestry of anything that opens the volume", and
# the wrapper and driver both used to assert that `gh`, `curl` and `git-lfs`
# inherit /bin/bash's grant because they are plain Mach-O. Measured, that is true
# of exactly one of the three:
#
#   git      /opt/homebrew/Cellar/git/2.55.0/bin/git        adhoc, no TeamIdentifier
#   gh       /opt/homebrew/Cellar/gh/2.98.0/bin/gh          adhoc,linker-signed, no TeamIdentifier
#   git-lfs  /opt/homebrew/Cellar/git-lfs/3.7.1/bin/git-lfs adhoc,linker-signed, no TeamIdentifier
#   curl     /usr/bin/curl                                  Platform identifier=26  <- the one that inherits
#
# `gh` has TTSModManager's signature shape exactly, which is already documented as
# sufficient to make a bare Mach-O its own responsible_path. git and gh have held
# their own kTCCServiceSystemPolicyRemovableVolumes rows since March and August.
#
# ONE STORE, ONE SERVICE, AND IT IS THE STORE THE REMEDIATION WRITES. The query is
# pinned to kTCCServiceSystemPolicyRemovableVolumes rows in the USER database
# (~/Library/Application Support/com.apple.TCC/TCC.db). That is the only row a
# `brew upgrade` destroys and the only row answering the prompt recreates -- all
# ten path-keyed rows there carry auth_reason=2, user consent. "Full Disk Access"
# writes kTCCServiceSystemPolicyAllFiles into the SYSTEM database instead, and on
# this machine the two are strictly disjoint: 0 RemovableVolumes rows in the system
# db, 0 AllFiles rows in the user db. Widening the read to the system db would
# report a row no operator action on this machine has ever created, and a refusal
# an operator cannot clear is worse than no check.
#
# WHY THIS READ CANNOT ITSELF BECOME THE WEDGE -- the constraint that decides
# whether the check is viable at all:
#   * /usr/bin/sqlite3, /usr/bin/codesign and /usr/bin/readlink are platform
#     binaries (Platform identifier=26), so nothing is inserted into the
#     attribution chain and the durable rule survives.
#   * Reading the user TCC.db needs kTCCServiceSystemPolicyAllFiles, which
#     /bin/bash holds. AllFiles is NOT promptable -- it is granted in System
#     Settings and a denial returns immediately. The 08-29 kernel log shows it:
#     AllFiles refused in 9 ms, and it was the RemovableVolumes request that
#     prompted. So the worst case here is a fast EPERM -> INCONCLUSIVE -> continue.
#   * The caller runs this whole script inside `run_bounded 15`, and a bound trip
#     is non-fatal by the contract below.
#
# THE ONLY FATAL RC IS 10, and that polarity is the whole safety argument: a bug
# in this helper can never cost a night, and the wrapper's existing exit-6 canary
# is still right behind it. Fail-open on every ambiguity; fail-closed on exactly
# two positively-detected signatures (a DENIED row, or STALE).
#
# STALE is conditional, not a property of the binary: it means there is no row for
# the path that will actually run and at least one row for a SIBLING version under
# the same /opt/homebrew/Cellar/<formula>/ directory. macOS never prunes the
# superseded row -- three python@3.14 rows are sitting there right now, one per
# upgrade -- so that pair is the observed signature of an invalidated grant. It
# clears two ways: grant the new path (a row with auth_value=2 wins outright), or
# delete the superseded siblings (the branch stops applying and the verdict falls
# to UNKNOWN, which warns and continues).
#
# WHY git-lfs IS ADVISORY AND NEVER ENFORCED. It has the same signature shape and
# NO row at all, yet it demonstrably runs every night (the global core.hooksPath
# post-checkout hook shells `git lfs post-checkout` on the driver's `git worktree
# add`) and has never wedged -- most likely because its responsible process is the
# `git` that spawned it, which is itself granted. Enforcing on a client whose
# absence-of-row is the NORMAL state would refuse valid nights, which is the exact
# failure the warn-and-continue rule exists to prevent. It is reported every night;
# the day its line flips to GRANTED or STALE is the day someone moves it into the
# enforced set deliberately.
#
# `--client` AND SCED_SYNC_TCC_EXTRA_CLIENT SHARE ONE PATH DOOR, AND BOTH BYPASS
# THE codesign CLASSIFY GATE. A client named as an absolute path is checked as
# given: it is not resolved through PATH and it is never classified, because the
# whole point of both is to name a path that does not exist on disk (a future or
# past Cellar version) and reproduce the STALE signature against the real
# database. Neither can remove or disable an enforced client -- SCED_SYNC_TCC_EXTRA_CLIENT
# is additive only, so it is not a kill switch.
#
# Usage:
#   assert-tcc-grants.sh                 check the enforced set; exit 0 or 10
#   assert-tcc-grants.sh --print         check everything, always exit 0 (read-only view)
#   assert-tcc-grants.sh --client PATH   check one absolute path and exit; test hook
#   assert-tcc-grants.sh --help
#
# Exit codes:
#   0  every enforced client has a matching grant, or is platform, or is inconclusive
#  10  at least one enforced client is STALE or DENIED -- the ONLY rc the wrapper
#      treats as fatal (it maps 10 -> its own exit 12)
#   2  this script could not run its own preconditions (no sqlite3, no readable db)
#   1  usage
#
# Strictly read-only. It opens the database `-readonly`, writes no state file --
# deliberately: a cached-cdhash file would let the check drift from the machine and
# has no cleanup path anyone owns -- and touches nothing on the volume but itself.

# NOT `set -e`. Every rc below is decided explicitly, and an unexpected non-zero
# from a probe must never abort the scan mid-way: a partial scan that exits with a
# stray status is exactly the fail-closed-by-accident this check must not do.
set -uo pipefail

SQLITE=/usr/bin/sqlite3
CODESIGN=/usr/bin/codesign
READLINK=/usr/bin/readlink
TCC_DB="${HOME}/Library/Application Support/com.apple.TCC/TCC.db"
SERVICE=kTCCServiceSystemPolicyRemovableVolumes

# The enforced set is {git, gh}: both are self-responsible, both hold their own row
# today, and both broke the nightly on 2026-08-27. Adding to it is a deliberate act
# -- the same default-deny discipline as the driver's failover dispatch allowlist.
ENFORCED="git gh"
ADVISORY="git-lfs"
REPORTED="curl"

MODE=default
CLIENT_ARG=""

usage() {
    sed -n '/^# Usage:/,/^#   1  usage/p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

--client and SCED_SYNC_TCC_EXTRA_CLIENT both name an absolute path directly. They
bypass the codesign classification gate on purpose, so a path that does not exist
on disk can still be checked against the real database. SCED_SYNC_TCC_EXTRA_CLIENT
only ADDS an enforced client; it can never remove or disable one.
EOF
}

# MODE would otherwise be last-write-wins, and BOTH orderings break the contract
# documented above: `--client PATH --print` reports on the enforced set instead of
# the path that was asked about, and `--print --client PATH` can exit 10 out of the
# mode documented as always 0. An operator reaches for this tool mid-incident; an
# answer about the wrong binary is worse than a refusal.
one_mode() { [ "$MODE" = default ] || { printf 'usage error: give only one of --print / --client\n' >&2; exit 1; }; }

while [ $# -gt 0 ]; do
    case "$1" in
        --print)  one_mode; MODE=print; shift ;;
        # `shift 2` shifts NOTHING when the value is missing ($# == 1), so the
        # loop re-enters this branch forever -- silently, with no output, which is
        # the failure class this whole file exists to end. The empty CLIENT_ARG is
        # rejected by the absolute-path check below, which owns that message.
        --client) one_mode; MODE=client; CLIENT_ARG="${2:-}"; shift $(( $# > 1 ? 2 : 1 )) ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'usage error: unknown argument %s\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
done

if [ "$MODE" = client ]; then
    case "$CLIENT_ARG" in
        /*) ;;
        *) printf 'usage error: --client needs an absolute path\n' >&2; exit 1 ;;
    esac
fi

# ------------------------------------------------------------------ SQL quoting
# `.param set` is a sqlite3 DOT-COMMAND, and its tokenizer is shell-like: it has
# no '' doubling escape, so this does NOT make a quote-bearing value safe. What is
# safe is bash's own expansion -- ${c} is expanded once and the result is never
# re-scanned, so $(...) and backticks are inert. The doubling only keeps a
# well-formed value inside its single-quoted token.
# Measured against /usr/bin/sqlite3 3.51.0: a value containing ' leaves the
# dot-command unparseable, sqlite3 prints `.parameter` help to STDOUT (not stderr,
# so the caller's 2>/dev/null misses it) and exits 0, and the client is reported
# multi-line INCONCLUSIVE. Latent, and the polarity is safe -- INCONCLUSIVE is
# never rc 10 -- because no path on this machine carries a quote. Widening the
# input surface beyond this machine's own PATH means binding on argv instead.
sql_quote() { local s=$1; printf '%s' "${s//\'/\'\'}"; }

# LIKE metacharacters in the formula name, under ESCAPE '\'. Backslash first, or
# the escapes introduced below would themselves be escaped.
like_escape() {
    local s=$1
    s=${s//\\/\\\\}
    s=${s//%/\\%}
    s=${s//_/\\_}
    printf '%s' "$s"
}

# ------------------------------------------------------------- preconditions
if [ ! -x "$SQLITE" ]; then
    printf 'tcc: preconditions INCONCLUSIVE -- %s is not executable; no client was checked\n' "$SQLITE"
    exit 2
fi
if [ ! -r "$TCC_DB" ]; then
    printf 'tcc: preconditions INCONCLUSIVE -- cannot read %s; no client was checked\n' "$TCC_DB"
    exit 2
fi
# One probe query. If the schema or the read privilege has gone, say so once here
# rather than reporting every client as inconclusive for the same reason.
if ! "$SQLITE" -readonly "$TCC_DB" \
        "select count(*) from access where service='${SERVICE}';" >/dev/null 2>&1; then
    printf 'tcc: preconditions INCONCLUSIVE -- the access table could not be queried; no client was checked\n'
    exit 2
fi

# --------------------------------------------------------------- the lookups
# auth_value for an exact path-keyed row, or empty when there is none.
# client_type=1 is the path-keyed kind; a bundle-id row can never equal an
# absolute path, so this only makes the intent explicit.
grant_value() {
    local c; c="$(sql_quote "$1")"
    "$SQLITE" -readonly "$TCC_DB" <<SQL 2>/dev/null
.param set :c '${c}'
select auth_value from access
 where service = '${SERVICE}' and client_type = 1 and client = :c
 limit 1;
SQL
}

# Count of rows for OTHER versions under the same Cellar formula directory.
sibling_count() {
    local resolved=$1 prefix c p
    case "$resolved" in
        /opt/homebrew/Cellar/*/*) ;;
        *) printf '0'; return 0 ;;
    esac
    # /opt/homebrew/Cellar/<formula>/<version>/... -> /opt/homebrew/Cellar/<formula>/
    prefix="${resolved#/opt/homebrew/Cellar/}"
    prefix="${prefix%%/*}"
    [ -n "$prefix" ] || { printf '0'; return 0; }
    c="$(sql_quote "$resolved")"
    p="$(sql_quote "$(like_escape "/opt/homebrew/Cellar/${prefix}/")")"
    "$SQLITE" -readonly "$TCC_DB" <<SQL 2>/dev/null
.param set :c '${c}'
.param set :p '${p}%'
select count(*) from access
 where service = '${SERVICE}' and client_type = 1
   and client LIKE :p ESCAPE '\' and client <> :c;
SQL
}

# ------------------------------------------------------------- classification
# CLAUDE.md's own rule, encoded rather than reinvented: check a suspect with
# `codesign -dvvv <path>`, not by where it lives. A `Platform identifier=` line is
# an Apple platform binary and genuinely inherits bash; adhoc with no
# TeamIdentifier is its own responsible_path. Used only to decide WHETHER to look,
# never to decide the outcome.
classify() {
    local path=$1 out
    out="$("$CODESIGN" -dvvv "$path" 2>&1)" || { printf 'unclassified'; return 0; }
    case "$out" in
        *"Platform identifier="*) printf 'platform'; return 0 ;;
    esac
    case "$out" in
        *"TeamIdentifier=not set"*)
            case "$out" in
                *adhoc*) printf 'self-responsible'; return 0 ;;
            esac ;;
    esac
    printf 'team-signed'
}

FATAL=0

report() { printf 'tcc: %-8s %-50s %-16s %s\n' "$1" "$2" "$3" "$4"; }

# tier: enforced | advisory | reported.  A DENIED or STALE verdict sets FATAL only
# for the enforced tier; everything else is reported and continues.
check_name() {
    local name=$1 tier=$2 p r class av sib verdict downgraded=0

    p="$(command -v "$name" 2>/dev/null)"
    if [ -z "$p" ]; then
        report "$name" "(not on PATH)" "-" "INCONCLUSIVE"
        return 0
    fi
    # TCC keys on the real path, not the symlink. A resolution failure is never a
    # reason to refuse -- it downgrades the verdict instead.
    r="$("$READLINK" -f "$p" 2>/dev/null)"
    if [ -z "$r" ]; then r="$p"; downgraded=1; fi

    class="$(classify "$r")"
    if [ "$class" != "self-responsible" ]; then
        report "$name" "$r" "$class" "SKIPPED"
        return 0
    fi

    # A hit on either the resolved path or the symlink counts -- the fail-open
    # direction.
    av="$(grant_value "$r")"
    [ -n "$av" ] || av="$(grant_value "$p")"

    if [ "$av" = "2" ]; then
        verdict=GRANTED
    elif [ "$av" = "0" ]; then
        verdict=DENIED
    elif [ -n "$av" ]; then
        verdict="INCONCLUSIVE (auth_value=${av})"
    elif [ "$downgraded" = "1" ]; then
        verdict="UNKNOWN (path unresolved)"
    else
        sib="$(sibling_count "$r")"
        case "$sib" in
            ''|*[!0-9]*) verdict="INCONCLUSIVE (sibling query failed)" ;;
            0) verdict=UNKNOWN ;;
            *) verdict="STALE (${sib} sibling row(s) under the same formula)" ;;
        esac
    fi

    case "$tier" in
        advisory) [ "$verdict" = UNKNOWN ] && verdict="UNKNOWN (advisory)" ;;
    esac

    report "$name" "$r" "$class" "$verdict"

    if [ "$tier" = enforced ]; then
        case "$verdict" in
            DENIED|STALE*) FATAL=1 ;;
        esac
    fi
    return 0
}

# An absolute path is checked as given: no PATH resolution, no classification.
# This is the door --client and SCED_SYNC_TCC_EXTRA_CLIENT share.
check_path() {
    local c=$1 tier=$2 av sib verdict

    av="$(grant_value "$c")"
    if [ "$av" = "2" ]; then
        verdict=GRANTED
    elif [ "$av" = "0" ]; then
        verdict=DENIED
    elif [ -n "$av" ]; then
        verdict="INCONCLUSIVE (auth_value=${av})"
    else
        sib="$(sibling_count "$c")"
        case "$sib" in
            ''|*[!0-9]*) verdict="INCONCLUSIVE (sibling query failed)" ;;
            0) verdict=UNKNOWN ;;
            *) verdict="STALE (${sib} sibling row(s) under the same formula)" ;;
        esac
    fi

    # Pure bash, not `basename`: it was the one external this file resolved through
    # PATH while pinning every other to an absolute platform path.
    report "${c##*/}" "$c" "explicit" "$verdict"

    if [ "$tier" = enforced ]; then
        case "$verdict" in
            DENIED|STALE*) FATAL=1 ;;
        esac
    fi
    return 0
}

if [ "$MODE" = client ]; then
    check_path "$CLIENT_ARG" enforced
else
    for n in $ENFORCED; do check_name "$n" enforced; done
    for n in $ADVISORY; do check_name "$n" advisory; done
    for n in $REPORTED; do check_name "$n" reported; done
    # Additive only, and enforced: step 9 of the design uses it to prove the
    # refusal path end to end under real launchd attribution.
    if [ -n "${SCED_SYNC_TCC_EXTRA_CLIENT:-}" ]; then
        case "${SCED_SYNC_TCC_EXTRA_CLIENT}" in
            /*) check_path "${SCED_SYNC_TCC_EXTRA_CLIENT}" enforced ;;
            *) printf 'tcc: SCED_SYNC_TCC_EXTRA_CLIENT is not an absolute path, ignoring: %s\n' \
                      "${SCED_SYNC_TCC_EXTRA_CLIENT}" ;;
        esac
    fi
fi

# --print is the operator's read-only view and always exits 0, matching
# `sced-schedule.sh show` and `sced-failover.sh --status`.
if [ "$MODE" = print ]; then exit 0; fi
[ "$FATAL" -eq 0 ] || exit 10
exit 0
