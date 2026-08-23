#!/usr/bin/python3
"""koreanize: the shared foundation every stage is built on.

STDLIB TIER (design §5.9). This module is imported by both the art tier
(/opt/homebrew/bin/python3 3.14) and the stdlib tier (/usr/bin/python3 3.9.6),
so it may import **stdlib only** -- never PIL, never numpy -- and must stay
3.9-compatible. `--selftest` enforces both properties mechanically, over a
declared subject set, and `kz_config` is the only other koreanize module it is
allowed to name.

It deliberately does NOT import kz_config. kz_config imports this module for its
exit codes; an edge back the other way would be a module-scope cycle resolved by
whichever import ran first (§3.2). The AI contract that needs `scenario.json` is
therefore split: `declare_ai()` registers at import, `bind_scenario()` validates
when a config arrives, and either order gives the same answer.

Contents, in the order §6 step 1 lists them:
  - the exit table, EXIT_PRECEDENCE and pick_exit()
  - P0a (launchd) and P0b (nightly scratch worktree)
  - GIT_READONLY, the whole of §1.4's git rule
  - TOLERANCES and CONSENTS with their phase axis and totality selftests
  - declare_ai() / assert_ai_contract(), the enforcement half of §3.2
  - carry_review(), lifted verbatim from typeset-cards.py:2594-2642
  - the report envelope, compute_consumable() and write_report()
  - the atomic-write wrappers over sced_io
  - STDLIB_TIER and the two tier checks it is the subject set of
"""

import argparse
import ast
import errno
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(PACKAGE_DIR)
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import sced_io  # noqa: E402  (path is set immediately above)

SCHEMA_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# 1. The exit table (§4.2)
# ---------------------------------------------------------------------------
#
# Codes 11, 62, 65, 66 and 67 are deliberately identical in meaning to
# resolve-rebase-with-ai.sh:47-56 so CLAUDE.md's runbook (B) transfers verbatim.
# 70 is deliberately NOT used -- daily-sync-local.sh:109 owns it and an operator
# reading a 70 beside the nightly's would derive the wrong first move; R2 and
# network failures are 74. The stage codes 20, 21 and 30 DO collide numerically
# with the driver's `20 fetch failed` / `21 overlap gate script failed` /
# `30 rebase failed`; that is accepted, because they are stage-local codes that
# only ever appear in a koreanize report and never in a Discord notification.

EXIT_OK = 0
EXIT_BUG = 1                 # unhandled exception -- a defect in the tool
EXIT_USAGE = 2               # usage, or invocation guard P0a / P0b
EXIT_GUARD = 4               # config guard refusal. NOTHING WAS WRITTEN.
EXIT_POLICY = 11             # AI abstain / low confidence / a rule failure
EXIT_PRECONDITION = 13       # manifest, upstream report, freshness, font, ceiling
EXIT_DRIFT = 14              # input drift (T5 class)
EXIT_RULE_A = 20             # per-stage hard rule code
EXIT_RULE_B = 21             # per-stage hard rule code
EXIT_TOLERANCE = 22          # a tolerance-bearing predicate fired, flag not given
EXIT_MASK_RESIDUAL = 23      # mask-coverage residual above the accepted baseline
EXIT_REVERT_INCOMPLETE = 24  # a recorded write did not restore
EXIT_AI_BUDGET = 25          # an AI stage stopped BETWEEN batches
EXIT_CONSENT = 26            # a CONSENTS row's flag was not given
EXIT_GATE = 30               # human gate present but not accepted
EXIT_COLLISION = 62          # case-only path collision
EXIT_AI_UNAVAILABLE = 65     # claude unavailable / unauthenticated / timed out
EXIT_AI_MANIFEST = 66        # AI manifest invalid
EXIT_ARTIFACT = 67           # produced artifact failed structural verification
EXIT_NETWORK = 74            # R2 / network failure

# The dispatcher's own band, disjoint from every stage code above and from
# sced-run-now.sh's {7,8,9}. koreanize.sh returns one of these INSTEAD of a
# stage's code, never aggregated with one, which is why they are excluded from
# EXIT_PRECEDENCE below.
EXIT_DISPATCH_REFUSED = 71
EXIT_DISPATCH_UPSTREAM = 72
EXIT_DISPATCH_LIVE_DECLINED = 73
EXIT_DISPATCH_GOLDEN_SKIPPED = 75
DISPATCHER_BAND = (71, 72, 73, 75)

EXIT_MEANING = {
    0: "stage complete, every check passed",
    1: "unhandled exception -- a defect in the tool",
    2: "usage, or invocation guard P0a (launchd) / P0b (nightly scratch worktree)",
    4: "config guard refusal. Nothing was written.",
    11: "stop by policy -- AI abstain / low confidence / a rule failure",
    13: "precondition -- a named input is missing, malformed, stale or unbound",
    14: "input drift (T5 class)",
    20: "per-stage hard rule code (semantics in the stage docstring)",
    21: "per-stage hard rule code (semantics in the stage docstring)",
    22: "a tolerance-bearing predicate fired and its --accept-<name> was not given",
    23: "mask-coverage residual above the accepted baseline",
    24: "revert incomplete -- a recorded write did not restore",
    25: "an AI stage stopped between batches on its budget or wall clock",
    26: "a CONSENTS row's flag was not given -- a human authorization",
    30: "human gate present but not accepted",
    62: "case-only path collision",
    65: "claude unavailable / unauthenticated / timed out",
    66: "AI manifest invalid -- schema, coverage, or run binding",
    67: "the produced artifact failed structural verification",
    74: "R2 / network failure",
    71: "koreanize.sh refused before any stage ran",
    72: "koreanize.sh refused: upstream dependency is not consumable, or is stale",
    73: "koreanize.sh refused: --live declined at the banner",
    75: "golden skipped on an interpreter mismatch",
}

# Aggregation is an explicit precedence list, NOT min() (§4.2). The rationale of
# composite-cleared.py:45-63 is kept -- name the cause, not the symptom -- but its
# mechanism does not survive the move: that script works only because every rule
# code it has sits above its precondition code. koreanize places 4 and 11 BELOW 13
# and 62/65/66/67 ABOVE 20/21/22, so under min() a run tripping both rule 2 (11)
# and rule 7 (66) would exit 11, and one tripping both its own rule (20) and an
# invalid manifest (66) would exit 20 -- naming the symptom in both cases.
# 1 is never a member: it is what __main__ returns on an unhandled exception.
EXIT_PRECEDENCE = [2, 4, 13, 14, 66, 67, 62, 65, 25, 74, 11, 30, 24, 23, 20, 21, 26, 22]


def pick_exit(triggered):
    """First member of EXIT_PRECEDENCE present in `triggered`, else 0."""
    fired = set(triggered)
    unknown = fired - set(EXIT_PRECEDENCE) - {0}
    if unknown:
        raise ValueError(
            "pick_exit given code(s) outside EXIT_PRECEDENCE: %s. The dispatcher band "
            "%s is returned instead of a stage code, never aggregated with one."
            % (sorted(unknown), list(DISPATCHER_BAND)))
    for code in EXIT_PRECEDENCE:
        if code in fired:
            return code
    return EXIT_OK


class KzRefusal(Exception):
    """A refusal carrying the exit code the process should return.

    Every guard in this tool raises one of these rather than calling sys.exit,
    so a caller can catch it, and so `__main__` is the single place that maps a
    refusal onto a process exit status.
    """

    def __init__(self, code, message, detail=None):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.detail = detail

    def __str__(self):
        base = "exit %d: %s" % (self.code, self.message)
        if self.detail:
            base += "\n  %s" % self.detail
        return base


def refuse(code, message, detail=None):
    raise KzRefusal(code, message, detail)


# ---------------------------------------------------------------------------
# 2. Interpreter tiers (§5.9)
# ---------------------------------------------------------------------------
#
# The subject set is a literal tuple rather than prose, because a scan whose
# subject set is implied by prose is a scan whose coverage nobody can state --
# and two checks disagreeing about WHICH files they police is the shape that let
# AI_STAGE_MAP sit in an unpoliced art-tier module while a policed stdlib-tier
# one imported it at module scope (§3.2).
STDLIB_TIER = ("kz_common.py", "kz_config.py", "kz_langpack.py", "kz_verify.py", "sced_io.py")

# Closed in BOTH directions: an import outside this allowlist fails, and so does
# an import of any koreanize module outside the triple below -- which is what
# makes the one-way kz_decide -> kz_config dependency a checked property rather
# than a convention.
KOREANIZE_STDLIB_IMPORTS = frozenset({"kz_common", "kz_config", "sced_io"})

# Explicit rather than sys.stdlib_module_names, which does not exist on 3.9.6 --
# and an explicit list is the stronger form anyway: it is closed and reviewable.
STDLIB_ALLOWLIST = frozenset({
    "argparse", "ast", "base64", "binascii", "bisect", "calendar", "collections",
    "contextlib", "copy", "csv", "dataclasses", "datetime", "difflib", "enum",
    "errno", "fcntl", "fnmatch", "functools", "getpass", "glob", "gzip", "hashlib",
    "heapq", "hmac", "html", "http", "io", "itertools", "json", "logging", "math",
    "mimetypes", "operator", "os", "pathlib", "platform", "posixpath", "pprint",
    "py_compile", "random", "re", "shlex", "shutil", "signal", "socket", "sqlite3",
    "stat", "string", "struct", "subprocess", "sys", "tempfile", "textwrap",
    "threading", "time", "traceback", "types", "typing", "unicodedata", "urllib",
    "uuid", "warnings", "zipfile", "zlib",
})

PLATFORM_PYTHON = "/usr/bin/python3"


def _stdlib_tier_paths():
    out = []
    for name in STDLIB_TIER:
        # sced_io.py lives one level up, beside the other shared helpers.
        cand = os.path.join(PACKAGE_DIR, name)
        if not os.path.exists(cand):
            cand = os.path.join(SCRIPTS_DIR, name)
        out.append((name, cand))
    return out


def stdlib_tier_pending():
    """Declared subject-set members that have not been built yet.

    Counted and printed rather than failed, for the same reason the TOLERANCES
    phase axis exists: kz_langpack.py lands in §6 step 6 and kz_verify.py in
    step 7, so between step 1 and step 6 an "absent" reading of this set would
    make the tier scan permanently red and train an operator to ignore it. A
    member that IS on disk is scanned and can still fail; only absence is
    pending, and it is never silent.
    """
    return [name for name, path in _stdlib_tier_paths() if not os.path.exists(path)]


def scan_stdlib_imports():
    """AST-scan the declared stdlib-tier subject set. Returns a list of findings.

    An import-only scan cannot catch `match`, a runtime-evaluated PEP-604
    `X | Y` annotation, or a 3.10+ builtin -- all of which are syntax or name
    errors on 3.9.6 and all of which would ship green under this check alone.
    That is what compile_stdlib_tier() below is for; the two are not redundant.
    """
    findings = []
    for name, path in _stdlib_tier_paths():
        if not os.path.exists(path):
            continue  # reported by stdlib_tier_pending(), never skipped silently
        with open(path, "rb") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src, filename=path)
        except SyntaxError as exc:
            findings.append("%s: does not parse: %s" % (name, exc))
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    findings.append("%s:%d: relative import" % (name, node.lineno))
                    continue
                mods = [node.module or ""]
            for mod in mods:
                top = mod.split(".")[0]
                if not top:
                    continue
                if top in STDLIB_ALLOWLIST or top in KOREANIZE_STDLIB_IMPORTS:
                    continue
                findings.append(
                    "%s:%d: imports %r, which is outside the stdlib allowlist and "
                    "outside %s" % (name, node.lineno, top, sorted(KOREANIZE_STDLIB_IMPORTS)))
    return findings


def compile_stdlib_tier():
    """Run the platform interpreter's py_compile over STDLIB_TIER as a subprocess.

    A subprocess and not an import, because the point is to exercise 3.9.6's own
    parser -- which is not the interpreter running this scan when pytest is on
    the art tier (§5.9).
    """
    findings = []
    if not os.path.exists(PLATFORM_PYTHON):
        return ["%s is absent; the 3.9 compatibility check cannot run" % PLATFORM_PYTHON]
    paths = [p for _n, p in _stdlib_tier_paths() if os.path.exists(p)]
    proc = subprocess.run(
        [PLATFORM_PYTHON, "-m", "py_compile"] + paths,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        findings.append("%s -m py_compile rc %d:\n%s"
                        % (PLATFORM_PYTHON, proc.returncode,
                           proc.stdout.decode("utf-8", "replace").strip()))
    return findings


# ---------------------------------------------------------------------------
# 3. Invocation guards P0a and P0b (§5.9)
# ---------------------------------------------------------------------------

P0A_OVERRIDE_ENV = "KOREANIZE_ALLOW_LAUNCHD"
SCRATCH_MARKER = os.path.join(".local-sync", "scratch")


def check_p0a():
    """Refuse under launchd, detected DIRECTLY -- never by tty.

    recompose-atlases.py:437-442 verbatim in mechanism and in reasoning. A tty
    test would refuse every legitimate agent or CI run and the override would
    become the standing idiom, leaving the guard inert while reading as enforced.

    Homebrew's python3 is an app bundle, so TCC makes it its own responsible_path;
    under launchd it raises a consent prompt nothing can answer (2026-08-18: one
    prompt, three bounds, two agents -- CLAUDE.md).
    """
    ppid = os.getppid()
    xpc = os.environ.get("XPC_SERVICE_NAME", "0")
    under_launchd = (ppid == 1) or (xpc != "0")
    if under_launchd and os.environ.get(P0A_OVERRIDE_ENV) != "1":
        refuse(EXIT_USAGE,
               "refusing to run under launchd (ppid=%d, XPC_SERVICE_NAME=%r)" % (ppid, xpc),
               "koreanize is an operator tool. Set %s=1 to record a deliberate "
               "override." % P0A_OVERRIDE_ENV)
    return under_launchd and os.environ.get(P0A_OVERRIDE_ENV) == "1"


def check_p0b(paths=()):
    """Refuse when cwd or any declared write root resolves under the nightly's scratch.

    The inverse of resolve-rebase-with-ai.sh:163-167, which refuses any worktree
    NOT under */.local-sync/scratch/* because that script may only operate inside
    the nightly's disposable worktree. koreanize operates on the PRIMARY checkouts,
    so a run started in the scratch tree would be writing into a directory the
    driver deletes on completion and the writes would vanish with no diagnostic.

    NO OVERRIDE, deliberately: unlike P0a there is no legitimate reason to want
    one, so an escape hatch would only ever be used to defeat it.
    """
    candidates = [os.getcwd()] + list(paths)
    for cand in candidates:
        real = os.path.realpath(cand)
        if SCRATCH_MARKER in real:
            refuse(EXIT_USAGE,
                   "refusing to operate inside the nightly's scratch worktree",
                   "%s resolves to %s, which is under %s/. The driver deletes that "
                   "tree on completion; writes there vanish with no diagnostic."
                   % (cand, real, SCRATCH_MARKER))


def check_invocation_guards(paths=()):
    """P0a then P0b. Exit 2 for both: these are facts about HOW the tool was
    invoked, not about its inputs. Returns True when P0a was overridden, which
    compute_consumable() reads as a test override."""
    overridden = check_p0a()
    check_p0b(paths)
    return overridden


def test_overrides_present():
    """True when this run overrode a guard or set a test hook.

    A run that overrode a guard must never publish a consumable report: that is
    the conjunct compute_consumable() spends this on.
    """
    if os.environ.get(P0A_OVERRIDE_ENV) == "1":
        return True
    for key in os.environ:
        if key.startswith("KOREANIZE_TEST_"):
            return True
    return False


# ---------------------------------------------------------------------------
# 4. GIT_READONLY -- the whole of §1.4's git rule
# ---------------------------------------------------------------------------
#
# "No git operations" was the earlier formulation and it is false as stated: four
# requirements in this design run git, one of them a v0 acceptance predicate.
# The rule is therefore an allowlist, so that it is grep-checkable rather than
# aspirational. add/commit/checkout/clean/reset/fetch/push/stash appear in this
# tool only as text it PRINTS (§5.10) or as an operator's own fallback (§8.4).

GIT_READONLY = frozenset({"rev-parse", "diff", "status", "ls-files"})


def git(verb, args=(), cwd=None, check=False):
    """The one wrapper every git invocation in the tool goes through.

    A verb outside GIT_READONLY refuses at exit 2 -- a fact about how the tool
    was invoked, not about its inputs. test_koreanize_gates.py greps the change
    set for a `git` invocation that does not come through here.
    """
    if verb not in GIT_READONLY:
        refuse(EXIT_USAGE,
               "git verb %r is not read-only" % verb,
               "GIT_READONLY = %s (§1.4). koreanize runs no mutating git verb; it "
               "prints the commit line and the operator commits."
               % sorted(GIT_READONLY))
    cmd = ["git"]
    if cwd:
        cmd += ["-C", str(cwd)]
    cmd += [verb] + [str(a) for a in args]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        refuse(EXIT_PRECONDITION,
               "git %s failed (rc %d)" % (verb, proc.returncode),
               proc.stderr.decode("utf-8", "replace").strip())
    return proc


def git_stdout(verb, args=(), cwd=None, check=True):
    return git(verb, args, cwd=cwd, check=check).stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 5. TOLERANCES and CONSENTS -- two closed, total tables (§4.1)
# ---------------------------------------------------------------------------
#
# The totality assertion is stated in the direction it can actually hold:
# every registered --accept-* flag has exactly one row, and every row carries an
# exit code and a named --selftest fault. The flag column is deliberately NOT
# asserted non-empty, because one row has no flag by design -- guard.max_files_written
# is a hard cap with no acceptance path at all -- and a rule reading "every row
# has a registered flag" fails on its own table the day it is written.
#
# PHASE AXIS (2026-08-21, finding K23). The flag axis is not the only one on
# which this table can fail to be total. Five of the seven rows are owned by
# modules that do not exist when v0 ships, so a selftest demanding an EXERCISED
# fault from every row is unsatisfiable at the very step §6 makes it the
# completion predicate of. Rows therefore declare an owning phase and module;
# the assertion runs over rows whose module exists and COUNTS the rest as
# pending. A row whose module IS present but whose fault does not fire still
# fails, so the phase attribute can never excuse a real gap.

PHASES = ("v0", "v1", "v1.x")


class _Row(object):
    __slots__ = ("name", "predicate", "stage", "phase", "module", "flag",
                 "exit_code", "selftest_fault", "hard_cap")

    def __init__(self, name, predicate, stage, phase, module, flag,
                 exit_code, selftest_fault, hard_cap=False):
        self.name = name
        self.predicate = predicate
        self.stage = stage
        self.phase = phase
        self.module = module
        self.flag = flag
        self.exit_code = exit_code
        self.selftest_fault = selftest_fault
        self.hard_cap = hard_cap

    def __repr__(self):
        return "<%s %s phase=%s module=%s>" % (
            type(self).__name__, self.name, self.phase, self.module)


class Tolerance(_Row):
    pass


class Consent(_Row):
    pass


TOLERANCES = (
    Tolerance("deep-shrink", "shrink ladder bottomed out on a face",
              "typeset", "v1", "kz_typeset.py", "--accept-deep-shrink", EXIT_TOLERANCE,
              "a synthetic face whose body cannot fit above MIN_SIZE_ABS"),
    Tolerance("alignment", "alignment displacement beyond alignment_check_px",
              "typeset", "v1", "kz_typeset.py", "--accept-alignment", EXIT_TOLERANCE,
              "a face whose reference axis is off its cleared run"),
    Tolerance("mask-residual",
              "mask-coverage residual above the lock baseline, keyed "
              "(check, group, window_name, face)",
              "mask", "v1.x", "kz_mask.py", "--accept-mask-residual", EXIT_MASK_RESIDUAL,
              "the 71006/71005 faces of Act/front+body, and a synthetically narrowed window"),
    Tolerance("atlas-size", "atlas above 90% of MAX_ATLAS_BYTES",
              "recompose", "v1", "kz_recompose.py", "--accept-atlas-size", EXIT_TOLERANCE,
              "a synthetic atlas at 61 MiB"),
    Tolerance("grain", "inpaint grain ratio outside the recorded band",
              "erase", "v1", "kz_erase.py", "--accept-grain", EXIT_TOLERANCE,
              "a delivered face with grain ratio driven past the band"),
    Tolerance("donor-choice", "two donors at equal confidence and different grids",
              "triage", "v0", "kz_triage.py", "--accept-donor-choice", EXIT_TOLERANCE,
              "two donors at two grids for one id"),
    Tolerance("max-files-written", "planned write set at guard.max_files_written",
              "kz_langpack.py", "v0", "kz_langpack.py", None, EXIT_GUARD,
              "199 / 200 / 201 planned files", hard_cap=True),
)

# A third category, registered as one. §5.11's transfer authorization is not a
# tolerance -- nothing fired, no predicate was exceeded, there is no baseline to
# extend -- but it is also not an ordinary flag, because it is the single control
# between FFG art and a third party and an unregistered control is one nobody can
# enumerate. Exit 26 is distinct from 22 for the same reason the tables are:
# 22 says "a measurement exceeded a bound, decide whether to accept it";
# 26 says "nothing is wrong; a person must authorize this".
CONSENTS = (
    Consent("third-party-transfer",
            "third-party transfer of an assembled --engine external package",
            "erase", "v1", "kz_erase.py", "--consent-third-party-transfer", EXIT_CONSENT,
            "the five §5.11 assertions all pass and the flag is absent -- the package "
            "must stay in <run_dir> and the path must not be printed"),
)


def tolerance_flags():
    return frozenset(r.flag for r in TOLERANCES if r.flag)


def consent_flags():
    return frozenset(r.flag for r in CONSENTS if r.flag)


def all_flags():
    return tolerance_flags() | consent_flags()


def row_by_flag(flag):
    for row in TOLERANCES + CONSENTS:
        if row.flag == flag:
            return row
    return None


def _module_exists(module):
    return os.path.exists(os.path.join(PACKAGE_DIR, module))


def pending_rows():
    """Rows whose owning module has not been built yet, by table."""
    return {
        "TOLERANCES": [r for r in TOLERANCES if not _module_exists(r.module)],
        "CONSENTS": [r for r in CONSENTS if not _module_exists(r.module)],
    }


def pending_summary():
    """The line printed on every selftest run and repeated by --status.

    Counted and printed, never skipped: a row that quietly dropped out of the
    assertion because its module was missing is precisely the coverage nobody
    would notice was gone.
    """
    lines = []
    tier_pending = stdlib_tier_pending()
    if tier_pending:
        lines.append("STDLIB_TIER: %d of %d members pending (%s) -- not yet built"
                     % (len(tier_pending), len(STDLIB_TIER), ", ".join(tier_pending)))
    for table, rows in sorted(pending_rows().items()):
        if not rows:
            continue
        by_phase = {}
        for row in rows:
            by_phase.setdefault(row.phase, []).append(row.stage)
        parts = ["%s: %s" % (ph, ", ".join(sorted(by_phase[ph])))
                 for ph in sorted(by_phase)]
        lines.append("%s: %d rows pending (%s) -- owning modules not yet built"
                     % (table, len(rows), "; ".join(parts)))
    return lines


def assert_tables_total():
    """Every totality property of §4.1, in the direction each can hold.

    Returns a list of findings; empty means the tables are total.
    """
    findings = []
    for table_name, rows in (("TOLERANCES", TOLERANCES), ("CONSENTS", CONSENTS)):
        seen = {}
        for row in rows:
            if row.phase not in PHASES:
                findings.append("%s/%s: phase %r not in %s"
                                % (table_name, row.name, row.phase, list(PHASES)))
            if not row.module:
                findings.append("%s/%s: no owning module" % (table_name, row.name))
            if not row.exit_code:
                findings.append("%s/%s: no exit code" % (table_name, row.name))
            if not row.selftest_fault:
                findings.append("%s/%s: no named --selftest fault" % (table_name, row.name))
            # Exactly the hard_cap rows lack a flag, so the absence is a
            # declaration rather than an omission.
            if row.hard_cap and row.flag is not None:
                findings.append("%s/%s: hard_cap row carries a flag %r"
                                % (table_name, row.name, row.flag))
            if not row.hard_cap and row.flag is None:
                findings.append("%s/%s: no flag and hard_cap is not declared"
                                % (table_name, row.name))
            if row.flag:
                if row.flag in seen:
                    findings.append("%s: flag %r has two rows (%s, %s)"
                                    % (table_name, row.flag, seen[row.flag], row.name))
                seen[row.flag] = row.name
    # A flag in both tables would mean a human decision was being recorded as a
    # threshold.
    both = tolerance_flags() & consent_flags()
    if both:
        findings.append("TOLERANCES and CONSENTS are not disjoint: %s" % sorted(both))
    return findings


# ---------------------------------------------------------------------------
# 6. The AI contract -- declared at import, asserted at the build path (§3.2)
# ---------------------------------------------------------------------------
#
# Two calls, because one cannot do both. A module body runs before there is a
# run, a ctx or an envelope, so an import-time raise cannot express "this run
# reached the build path without an AI envelope".

AI_CONTRACT = {}
_BOUND_SCENARIO = {"cfg": None}


def declare_ai(stage, required):
    """Register a stage's AI contract from the module body.

    This is what makes the eleven forbidden stages MECHANIZED rather than merely
    documented. The assertion against scenario.json runs here when a config is
    already bound and in bind_scenario() otherwise, so either import order gives
    the same answer -- and neither needs kz_common to import kz_config.
    """
    prior = AI_CONTRACT.get(stage)
    if prior is not None and prior != bool(required):
        refuse(EXIT_GUARD,
               "stage %r declares conflicting AI contracts" % stage,
               "declared required=%s and required=%s" % (prior, bool(required)))
    AI_CONTRACT[stage] = bool(required)
    cfg = _BOUND_SCENARIO["cfg"]
    if cfg is not None:
        findings = _check_contracts(cfg)
        if findings:
            refuse(EXIT_GUARD, "AI contract does not match scenario.json",
                   "; ".join(findings))


def _check_contracts(cfg):
    ai = (cfg or {}).get("ai") or {}
    required = set(ai.get("required_stages") or ())
    forbidden = set(ai.get("forbidden_stages") or ())
    findings = []
    for stage, is_required in sorted(AI_CONTRACT.items()):
        if is_required and stage not in required:
            findings.append("%s declares required=True but is not in ai.required_stages" % stage)
        if not is_required and stage not in forbidden:
            findings.append("%s declares required=False but is not in ai.forbidden_stages" % stage)
    return findings


def bind_scenario(cfg):
    """Called by kz_config once scenario.json is loaded and validated.

    Every contract registered before the config arrived is checked now; every
    contract registered afterwards is checked in declare_ai(). The split is what
    lets this module stay free of a kz_config import (§3.2).
    """
    _BOUND_SCENARIO["cfg"] = cfg
    findings = _check_contracts(cfg)
    if findings:
        refuse(EXIT_GUARD, "AI contract does not match scenario.json", "; ".join(findings))


def bound_scenario():
    return _BOUND_SCENARIO["cfg"]


def assert_ai_contract(report, cfg=None):
    """Called by write_report on the build path -- i.e. by every stage, without
    each stage remembering to.

    kz_slice.py therefore imports cleanly, which it must, and still cannot ship
    an AI-influenced artifact. §3.6's compute_consumable conjunct is the
    REPORTING half of the same rule and the two are deliberately redundant: this
    one stops the report being written at all.
    """
    cfg = cfg if cfg is not None else _BOUND_SCENARIO["cfg"]
    stage = report.get("stage")
    if report.get("mode") != "build":
        return
    ai = (cfg or {}).get("ai") or {}
    required = set(ai.get("required_stages") or ())
    forbidden = set(ai.get("forbidden_stages") or ())
    block = report.get("ai")
    if stage in required and block is None:
        refuse(EXIT_POLICY,
               "%s is a required-AI stage and is about to write a build report with "
               "ai: null" % stage,
               "the HARD CONSTRAINT (§3.2) forbids degrading to a no-AI path")
    if stage in forbidden and block is not None:
        refuse(EXIT_GUARD,
               "%s is a forbidden-AI stage and is about to write a build report "
               "carrying an ai block" % stage,
               "ai.forbidden_stages names it; an AI-influenced artifact from this "
               "stage cannot be shipped")


# ---------------------------------------------------------------------------
# 7. carry_review -- lifted VERBATIM from typeset-cards.py:2594-2642
# ---------------------------------------------------------------------------

def carry_review(prior_gate, prev_review, sha_moved, when):
    """The verdict a rebuild leaves behind. PURE -- takes dicts, returns a dict, touches no file.

    Three branches, three behaviours:
      1. the shas moved and a LIVE verdict exists  -> record it and reset to pending
      2. a record already exists and nobody has accepted since -> CARRY IT FORWARD
      3. a human has written `accepted`            -> the record retires

    Branch 2 is why this is a function. The original inline form required the PREVIOUS report's
    status to be non-pending, so it was a ONE-SHOT: the first rebuild recorded the superseded
    verdict and left the report `pending`, and the SECOND rebuild -- reading that pending status
    -- silently dropped the record, taking the `## Superseded` section of the gate file with it
    and erasing the audit trail of a human acceptance with no diagnostic anywhere.

    THE LIVE VERDICT IS NOT ALWAYS THE REPORT'S (fixed 2026-08-19, verify §3). A human accepts by
    editing the GATE FILE, and write_gate_stub() rewrites that file from the report -- so for
    exactly one build after an acceptance the gate says `accepted` while the report still says
    `pending`. Deciding branch 1 from `prev_status` alone missed that build entirely: branch 1 did
    not fire (report pending), branch 2 did not fire (gate accepted), and the else-branch retired
    the record while `new["status"]` stayed `accepted` -- inherited from prior_gate. The build then
    published `consumable: true` for pixels NOBODY HAS WALKED (review["status"] is the only open
    conjunct once --accept-deep-shrink/--accept-alignment are passed) and recompose-atlases.py's P6
    let the atlas build proceed on them. If a supersede record was already on file it was erased in
    the same call, which is branch 2's own bug reached by the other entry.
    So: the live verdict is the report's when it is non-pending and the GATE'S otherwise, and
    `sha_moved` is a veto on retiring -- a verdict can never outlive the pixels it was given to.
    """
    new = dict(prior_gate)
    prev_review = prev_review or {}
    prev_status = prev_review.get("status")
    prev_sup = prev_review.get("superseded")
    from_report = prev_status not in (None, "pending")
    live = prev_review if from_report else prior_gate
    live_status = live.get("status")
    if sha_moved and live_status not in (None, "pending"):
        sup = {"status": live_status, "reviewer": live.get("reviewer"),
               "date": live.get("date"),
               "superseded_at": when, "carried_forward": False}
        new["status"] = "pending"
        new["reviewer"] = None
        new["date"] = None
    elif prev_sup and (sha_moved or new.get("status") != "accepted"):
        sup = dict(prev_sup)
        sup.setdefault("superseded_at", None)
        sup["carried_forward"] = True
    else:
        sup = None
    new["superseded"] = sup
    return new


# ---------------------------------------------------------------------------
# 8. Hashes, time, and the binding/freshness blocks (§3.6)
# ---------------------------------------------------------------------------

def utc_now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root):
    """Digest over sorted (relpath, bytes) of every file below `root`.

    Sorted with the byte value of the relative path, so the digest does not move
    with a locale -- the same reason the overlap gate runs everything under
    LC_ALL=C.
    """
    digest = hashlib.sha256()
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            entries.append((os.path.relpath(full, root), full))
    for rel, full in sorted(entries, key=lambda pair: pair[0].encode("utf-8")):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(full).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_binding(paths, extra=None):
    """{relpath: sha256} over the inputs a stage read, plus any declared extras.

    `SCED-downloads@origin/korean` is the extra that answers Hazard A of §5.10:
    the nightly force-pushes `korean`, so a write set planned against the old base
    is stale even though nothing was lost. --status compares this to the live ref.
    """
    binding = {}
    for path in paths:
        binding[str(path)] = sha256_file(str(path))
    if extra:
        binding.update(extra)
    return binding


def downloads_base_binding(downloads_root):
    """The `SCED-downloads@origin/korean` entry of §5.10. None when unresolvable."""
    proc = git("rev-parse", ["origin/korean"], cwd=downloads_root)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("ascii", "replace").strip()


def build_freshness(input_paths, upstream_report_path=None):
    """max(mtime(inputs)) <= mtime(upstream report) -- recorded, and checked by
    the caller so the refusal names the offending file."""
    newest = None
    for path in input_paths:
        if not os.path.exists(str(path)):
            continue
        mtime = os.path.getmtime(str(path))
        if newest is None or mtime > newest:
            newest = mtime
    return {
        "newest_input_mtime": (
            None if newest is None
            else datetime.utcfromtimestamp(newest).strftime("%Y-%m-%dT%H:%M:%SZ")),
        "rule": "max(mtime(inputs)) <= mtime(upstream report)",
        "upstream_report": str(upstream_report_path) if upstream_report_path else None,
    }


# ---------------------------------------------------------------------------
# 9. The report envelope (§3.6)
# ---------------------------------------------------------------------------

MODES = ("build", "verify-only", "dry-run", "selftest", "replay")

# Only a build run may write the marker (.am/recompose-korean-atlases/design.md:581).
# kz_common enforces the path choice rather than each stage repeating it.
_MODE_SUFFIX = {
    "build": ".json",
    "verify-only": ".verify.json",
    "dry-run": ".dry-run.json",
    "selftest": ".selftest.json",
    "replay": ".replay.json",
}


def report_path(run_dir, stage, mode):
    if mode not in MODES:
        refuse(EXIT_USAGE, "unknown mode %r" % mode, "modes are %s" % list(MODES))
    return os.path.join(str(run_dir), stage + _MODE_SUFFIX[mode])


def tool_block(interpreter=None, pil=None, numpy=None):
    """pil and numpy are NULL, never absent and never "", in a stdlib-tier report.

    kz_langpack.py and kz_verify.py run on /usr/bin/python3, which has PIL 10.4.0
    and no numpy, and must not import either to fill a field. `golden` compares
    tool{} by report and treats a null as "this tier declares no opinion", never
    as a mismatch (§5.8).
    """
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    return {
        "path": os.path.abspath(path) if path else None,
        "sha256": sha256_file(path) if path and os.path.exists(path) else None,
        "interpreter": interpreter or sys.executable,
        "python": "%d.%d.%d" % sys.version_info[:3],
        "pil": pil,
        "numpy": numpy,
    }


def new_report(stage, slug, mode="build", counts=None, checks=None,
               binding=None, freshness=None, ai=None, gate=None,
               accepted=None, results=None, tool=None, seed=None):
    """The envelope, with write_set / registration / seed null BY DEFAULT.

    Null and not absent: §3.6 declares all three as fields, and a rollback path
    cannot read an input the contract does not define (§8.4 step 3 operates on
    registration.added[]).
    """
    if mode not in MODES:
        refuse(EXIT_USAGE, "unknown mode %r" % mode, "modes are %s" % list(MODES))
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "slug": slug,
        "generated_by": "koreanize %s" % stage,
        "generated_at": utc_now(),
        "mode": mode,
        "tool": tool if tool is not None else tool_block(),
        "seed": seed,
        "binding": binding if binding is not None else {},
        "freshness": freshness if freshness is not None else build_freshness([]),
        "ai": ai,
        "gate": gate,
        "write_set": None,
        "registration": None,
        "counts": counts if counts is not None else {},
        "checks": checks if checks is not None else [],
        "accepted": accepted if accepted is not None else {},
        "verdict": "PASS",
        "exit_code": EXIT_OK,
        "consumable": False,
        "consumable_blocked_by": None,
        "results": results,
    }


def compute_consumable(report, cfg=None, golden_run_id=None):
    """The one place `consumable` is computed. Returns (bool, blocked_by).

    Conjunctive, and every conjunct is here rather than spread across the stages
    that would each have to remember it.
    """
    cfg = cfg if cfg is not None else _BOUND_SCENARIO["cfg"]
    stage = report.get("stage")

    if report.get("mode") != "build":
        return False, "mode == %r" % report.get("mode")
    if report.get("exit_code") != EXIT_OK:
        return False, "exit_code == %s" % report.get("exit_code")
    if test_overrides_present():
        return False, "test overrides present"

    gate = report.get("gate")
    if gate is not None and gate.get("status") != "accepted":
        return False, "gate.status == %s" % gate.get("status")

    # Every tolerance-bearing check that fired must have had its flag given.
    accepted = report.get("accepted") or {}
    for check in report.get("checks") or []:
        flag_name = check.get("tolerance")
        if not flag_name:
            continue
        if check.get("status") == "pass":
            continue
        if not accepted.get(flag_name):
            return False, "tolerance %r fired and --accept-%s was not given" % (
                flag_name, flag_name)

    ai_cfg = (cfg or {}).get("ai") or {}
    required = set(ai_cfg.get("required_stages") or ())
    block = report.get("ai")
    if stage in required:
        # The previous form -- (ai is None or ai.decide_outcome == "proceed") --
        # made a stage that SKIPPED its required AI call fully consumable, i.e. it
        # rewarded exactly the degradation the HARD CONSTRAINT forbids.
        if block is None:
            return False, "required-AI stage with ai: null"
        if not block.get("used"):
            return False, "required-AI stage with ai.used false"
        if block.get("decide_outcome") != "proceed":
            return False, "ai.decide_outcome == %s" % block.get("decide_outcome")

    seed = report.get("seed")
    if seed is not None:
        # A seeded report is consumable only to a stage running inside the SAME
        # golden run id. Without this a seeded terms report carrying ai.used true
        # and decide_outcome proceed is indistinguishable from a real one, which
        # is a laundered AI acceptance (§5.8). A null id must not compare equal to
        # every run, so an unset ctx id blocks rather than passes.
        if golden_run_id is None or seed.get("golden_run_id") != golden_run_id:
            return False, "seeded by golden run %s, not consumable here" % seed.get(
                "golden_run_id")

    return True, None


def finalize_report(report, cfg=None, golden_run_id=None, triggered=()):
    """Set exit_code from pick_exit, then verdict, then consumable. In that order,
    because consumable reads exit_code."""
    code = pick_exit(triggered)
    report["exit_code"] = code
    if code == EXIT_OK:
        report["verdict"] = "PASS"
    elif code in (EXIT_PRECONDITION, EXIT_DRIFT, EXIT_GUARD, EXIT_USAGE):
        report["verdict"] = "PRECONDITION"
    else:
        report["verdict"] = "FAIL"
    ok, blocked = compute_consumable(report, cfg=cfg, golden_run_id=golden_run_id)
    report["consumable"] = ok
    report["consumable_blocked_by"] = blocked
    return report


def write_report(report, run_dir, cfg=None, golden_run_id=None):
    """The only writer of a stage report.

    assert_ai_contract runs HERE so that every stage inherits it without
    remembering to, and it runs BEFORE the write so a violating report never
    reaches disk.
    """
    assert_ai_contract(report, cfg=cfg)
    if report.get("consumable_blocked_by") is None and not report.get("consumable"):
        # finalize_report was not called; do it now so the two fields cannot
        # disagree with the rest of the envelope.
        finalize_report(report, cfg=cfg, golden_run_id=golden_run_id,
                        triggered=() if report.get("exit_code") in (None, 0)
                        else (report["exit_code"],))
    path = report_path(run_dir, report["stage"], report["mode"])
    atomic_write_json(path, report)
    return path


# ---------------------------------------------------------------------------
# 10. Atomic-write wrappers over sced_io
# ---------------------------------------------------------------------------
#
# Thin on purpose: sced_io is the shared writer and centralising it there means a
# single fix propagates to every caller. What these add is the path-type coercion
# (sced_io takes Path, koreanize passes str freely) and one property sced_io
# cannot give -- sced_io.py:99 passes NO sort_keys, so the A1 byte convention has
# to be a property of the mapping handed in, never of the writer (§5.3).

def _as_path(path):
    from pathlib import Path
    return path if hasattr(path, "parent") else Path(str(path))


def atomic_write_json(path, data):
    sced_io.atomic_write_json(_as_path(path), data)


def atomic_write_text(path, text):
    sced_io.atomic_write_text(_as_path(path), text)


def atomic_write_json_batch(items):
    """Staged two-phase commit over many destinations.

    Callers that need key order must sort the mapping BEFORE handing it over:
    sced_io.atomic_write_json_batch passes no sort_keys, so key order is whatever
    the caller built (§5.3).
    """
    return sced_io.atomic_write_json_batch({_as_path(k): v for k, v in items.items()})


def sorted_mapping(data):
    """Recursively rebuild dicts in sorted key order.

    The A1 byte convention is a property of the mapping, not of the writer -- so
    this is what a langpack caller runs before atomic_write_json_batch.
    """
    if isinstance(data, dict):
        return dict((k, sorted_mapping(data[k])) for k in sorted(data))
    if isinstance(data, list):
        return [sorted_mapping(v) for v in data]
    return data


# ---------------------------------------------------------------------------
# 11. --selftest
# ---------------------------------------------------------------------------

def selftest(verbose=True):
    """Every mechanical property this module owns. Returns a findings list."""
    findings = []

    findings += assert_tables_total()
    findings += scan_stdlib_imports()
    findings += compile_stdlib_tier()

    # pick_exit: the two pairs test_koreanize_gates.py pins, stated here too so a
    # bare `--selftest` run without pytest still exercises the precedence list.
    if pick_exit([]) != EXIT_OK:
        findings.append("pick_exit([]) != 0")
    if pick_exit([EXIT_POLICY, EXIT_AI_MANIFEST]) != EXIT_AI_MANIFEST:
        findings.append("pick_exit({11,66}) must be 66 -- the cause, not the symptom")
    if pick_exit([EXIT_RULE_A, EXIT_AI_MANIFEST]) != EXIT_AI_MANIFEST:
        findings.append("pick_exit({20,66}) must be 66 -- the cause, not the symptom")
    if pick_exit([EXIT_TOLERANCE, EXIT_CONSENT]) != EXIT_CONSENT:
        findings.append("pick_exit({22,26}) must be 26 -- the human question is named first")
    if set(EXIT_PRECEDENCE) & set(DISPATCHER_BAND):
        findings.append("EXIT_PRECEDENCE overlaps the dispatcher band")
    if len(set(EXIT_PRECEDENCE)) != len(EXIT_PRECEDENCE):
        findings.append("EXIT_PRECEDENCE has a duplicate")
    if EXIT_BUG in EXIT_PRECEDENCE:
        findings.append("1 must never be in EXIT_PRECEDENCE")

    # GIT_READONLY refuses a mutating verb at exit 2.
    for verb in ("add", "commit", "checkout", "clean", "reset", "fetch", "push", "stash"):
        try:
            git(verb, [])
        except KzRefusal as exc:
            if exc.code != EXIT_USAGE:
                findings.append("git(%r) refused with %d, expected 2" % (verb, exc.code))
        else:
            findings.append("git(%r) was not refused" % verb)

    if verbose:
        for line in pending_summary():
            print("  %s" % line)
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_common.py",
        description="koreanize shared foundation (stdlib tier). "
                    "Run --selftest to exercise every property this module owns.")
    parser.add_argument("--selftest", action="store_true",
                        help="tier checks, table totality, pick_exit and GIT_READONLY")
    parser.add_argument("--exit-table", action="store_true",
                        help="print the exit table and the precedence list")
    args = parser.parse_args(argv)

    if args.exit_table:
        for code in sorted(EXIT_MEANING):
            print("%3d  %s" % (code, EXIT_MEANING[code]))
        print("\nEXIT_PRECEDENCE = %s" % EXIT_PRECEDENCE)
        print("dispatcher band = %s (returned instead of a stage code, never with one)"
              % list(DISPATCHER_BAND))
        return EXIT_OK

    if args.selftest:
        print("kz_common --selftest")
        findings = selftest()
        if findings:
            print("\nFAIL (%d):" % len(findings))
            for line in findings:
                print("  - %s" % line)
            return EXIT_ARTIFACT
        print("  ok: tables total, stdlib tier clean, pick_exit and GIT_READONLY hold")
        return EXIT_OK

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
