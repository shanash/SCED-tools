#!/usr/bin/python3
"""arknights: the shared foundation every stage is built on (design section 2 row 2).

ONE INTERPRETER TIER, and it is a PLATFORM path. Everything here runs under
Apple's /usr/bin/python3 (3.9.6) and must stay stdlib-only and 3.9-compatible:
no f-string `=`, no `match`, no runtime `X | Y` unions. `--selftest` enforces
both properties mechanically over a declared subject set.

The pin is not style. macOS attributes a file request to the RESPONSIBLE
process, and Homebrew's python3 is an app bundle, so TCC makes it its own
responsible_path; /usr/bin/python3 is the xcode_select shim and inherits its
parent's grant (CLAUDE.md, 2026-08-18). P0b below refuses any interpreter that is
not Apple's -- see APPLE_PYTHON_ROOTS for why the test cannot be a path equality.

Contents, in the order design section 7 step S1 lists them:
  - the exit table, EXIT_PRECEDENCE and pick_exit()
  - P0a (launchd) and P0b (interpreter pin)
  - GIT_READONLY, and the one wrapper every git invocation goes through
  - TOLERANCES -- a closed, total table with exactly one acceptance flag
  - hashes, the binding{} block, and the read helpers
  - the report envelope, compute_consumable() and write_report()
  - get_mini_id(), transcribed verbatim from Global.ttslua:5418-5429
  - the package tier scans --selftest is the subject of

It deliberately does NOT import akn_config: akn_config imports this module for
its exit codes, and an edge back the other way would be a module-scope cycle
resolved by whichever import ran first.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from datetime import datetime

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(PACKAGE_DIR)
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(SCRIPTS_DIR))

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import sced_io  # noqa: E402  (path is set immediately above)

SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 1. The exit table (design section 4.3)
# ---------------------------------------------------------------------------
#
# THE BAND IS {80..89} AND IT IS VERIFIED FREE. The union of every documented
# table in this workspace reaches 75 and no further -- daily-sync-local.sh's,
# koreanize's stage codes and its {71,72,73,75} dispatcher band, sced-run-now.sh's
# {7,8,9}, and the wrapper's {2,6}. `grep -rnE '\bexit[ (]+8[0-9]\b'` over
# SCED-tools/scripts/ returns zero hits (re-verified 2026-08-30). 90..99 is
# reserved for a future stage so nothing here ever has to renumber, and the band
# is clear of the shell's reserved 126/127/128+N.

EXIT_OK = 0
EXIT_BUG = 1                 # unhandled exception -- a defect in the tool
EXIT_USAGE = 2               # usage, or guard P0a / P0b, or a mutating git verb
EXIT_GUARD = 80              # guard refusal. NOTHING WAS WRITTEN.
EXIT_PRECONDITION = 81       # a named input missing, malformed, or not consumable
EXIT_DRIFT = 82              # input drift -- the source tree or a config selector moved
EXIT_RENUMBER = 83           # renumbering rule violation
EXIT_REPAIR = 84             # repair rule violation
EXIT_ASSEMBLE = 85           # assembly rule violation
EXIT_VERIFY = 86             # a V-check failed, or an unaccepted declared remainder

# The dispatcher's own band. arknights.sh returns one of these INSTEAD of a
# stage's code, never aggregated with one, which is why they are excluded from
# EXIT_PRECEDENCE below.
EXIT_DISPATCH_REFUSED = 87
EXIT_DISPATCH_UPSTREAM = 88
EXIT_DISPATCH_LIVE_DECLINED = 89
DISPATCHER_BAND = (87, 88, 89)

EXIT_MEANING = {
    0: "stage complete, every check passed",
    1: "unhandled exception -- a defect in the tool",
    2: "usage, or guard P0a (launchd) / P0b (interpreter outside the platform set), "
       "or a mutating git verb reached the GIT_READONLY wrapper",
    80: "guard refusal. Nothing was written.",
    81: "precondition -- a named input is missing, malformed, or not consumable",
    82: "input drift -- docs/Arknights/ changed, or a config row names no object",
    83: "renumber: a renumbering rule violation",
    84: "repair: a defect with no config row, or a config row matching no object",
    85: "assemble: an assembly rule violation",
    86: "verify: a V-check failed, or a declared remainder without its --accept flag",
    87: "arknights.sh refused before any stage ran",
    88: "arknights.sh refused: the predecessor is not consumable, or is stale",
    89: "arknights.sh refused: --live declined at the banner",
}

# Aggregation is an explicit precedence list, NOT min() (section 4.3). Under min()
# a run tripping both its stage rule (83) and input drift (82) would report 82 --
# which happens to be right here, but a later row placed below its cause would
# not be, and the list makes the ordering reviewable instead of accidental.
# 1 is never a member: it is what __main__ returns on an unhandled exception.
EXIT_PRECEDENCE = [2, 80, 81, 82, 83, 84, 85, 86]


def pick_exit(triggered):
    """First member of EXIT_PRECEDENCE present in `triggered`, else 0."""
    fired = set(triggered)
    unknown = fired - set(EXIT_PRECEDENCE) - set([0])
    if unknown:
        raise ValueError(
            "pick_exit given code(s) outside EXIT_PRECEDENCE: %s. The dispatcher "
            "band %s is returned instead of a stage code, never aggregated with one."
            % (sorted(unknown), list(DISPATCHER_BAND)))
    for code in EXIT_PRECEDENCE:
        if code in fired:
            return code
    return EXIT_OK


class AknRefusal(Exception):
    """A refusal carrying the exit code the process should return.

    Every guard raises one of these rather than calling sys.exit, so a caller can
    catch it and so `__main__` is the single place that maps a refusal onto a
    process exit status.
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
    raise AknRefusal(code, message, detail)


# ---------------------------------------------------------------------------
# 2. Invocation guards P0a and P0b (design section 4.3, exit 2)
# ---------------------------------------------------------------------------

P0A_OVERRIDE_ENV = "ARKNIGHTS_ALLOW_LAUNCHD"

#: The platform prefixes sced_schedule.py's `program.platform` rule and
#: daily-sync-local.sh's PYBIN check already use. Kept identical on purpose: an
#: operator who has learned the rule once should not meet a second version of it.
PLATFORM_PREFIXES = ("/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/", "/usr/libexec/")
PLATFORM_PYTHON = "/usr/bin/python3"

#: MEASURED, and the reason P0b cannot simply compare against PLATFORM_PYTHON:
#: /usr/bin/python3 is Apple's xcode_select SHIM, so sys.executable never reports
#: it -- it reports wherever the shim exec'd to, today
#: /Applications/Xcode.app/Contents/Developer/usr/bin/python3. Both landing sites
#: are Apple-signed with a real TeamIdentifier; Homebrew's is adhoc-signed and an
#: app bundle, which is the whole difference TCC keys on. The allowlist is
#: therefore over the two Apple developer roots the shim can land in, plus the
#: platform prefixes for a future move.
APPLE_PYTHON_ROOTS = ("/Applications/Xcode.app/Contents/Developer/",
                      "/Library/Developer/CommandLineTools/",
                      "/System/Library/Frameworks/")


def check_p0a():
    """Refuse under launchd, detected DIRECTLY -- never by tty.

    A tty test would refuse every legitimate agent run and the override would
    become the standing idiom, leaving the guard inert while reading as enforced.
    """
    ppid = os.getppid()
    xpc = os.environ.get("XPC_SERVICE_NAME", "0")
    under_launchd = (ppid == 1) or (xpc != "0")
    if under_launchd and os.environ.get(P0A_OVERRIDE_ENV) != "1":
        refuse(EXIT_USAGE,
               "refusing to run under launchd (ppid=%d, XPC_SERVICE_NAME=%r)"
               % (ppid, xpc),
               "arknights is an operator tool. Set %s=1 to record a deliberate "
               "override." % P0A_OVERRIDE_ENV)
    return under_launchd and os.environ.get(P0A_OVERRIDE_ENV) == "1"


def interpreter_is_platform(executable=None):
    """True when `executable` is Apple's, false for Homebrew/pyenv/conda/anything."""
    real = os.path.realpath(executable or sys.executable)
    return any(real.startswith(prefix)
               for prefix in PLATFORM_PREFIXES + APPLE_PYTHON_ROOTS)


def check_p0b(executable=None):
    """Refuse when the running interpreter is not Apple's.

    NO OVERRIDE, deliberately. The whole point of the pin is that a Homebrew
    python3 is an app bundle and therefore its own TCC responsible_path, so an
    escape hatch here would only ever be used to re-arm the 2026-08-18 wedge.
    """
    if not interpreter_is_platform(executable):
        real = os.path.realpath(executable or sys.executable)
        refuse(EXIT_USAGE,
               "interpreter %s is not Apple's" % real,
               "the pin is %s (an xcode_select shim, so it reports itself as one "
               "of %s). Anything else -- Homebrew above all -- is an app bundle "
               "and therefore its own TCC responsible_path."
               % (PLATFORM_PYTHON, list(APPLE_PYTHON_ROOTS)))


def check_invocation_guards():
    """P0a then P0b. Exit 2 for both: these are facts about HOW the tool was
    invoked, not about its inputs. Returns True when P0a was overridden, which
    compute_consumable() reads as a test override."""
    overridden = check_p0a()
    check_p0b()
    return overridden


def test_overrides_present():
    """True when this run overrode a guard or set a test hook.

    A run that overrode a guard must never publish a consumable report.
    """
    if os.environ.get(P0A_OVERRIDE_ENV) == "1":
        return True
    for key in os.environ:
        if key.startswith("ARKNIGHTS_TEST_"):
            return True
    return False


# ---------------------------------------------------------------------------
# 3. GIT_READONLY -- the whole of the git rule (design section 5.5 step 7)
# ---------------------------------------------------------------------------
#
# An allowlist rather than "no git operations", so it is grep-checkable rather
# than aspirational. add/commit/checkout/clean/reset/fetch/push/stash appear in
# this tool only as text a stage PRINTS.

GIT_READONLY = frozenset(["rev-parse", "diff", "status", "ls-files"])


#: Every git call is bounded, because publish makes some of them while holding
#: the workspace lock and the driver declares that lock stale at one hour -- so
#: an unbounded wedge does not merely delay, it hands the lock to a takeover
#: while this process still believes it holds it. ~600x the measured cost of a
#: rev-parse on this volume, so a trip means wedged, not slow. The nightly's own
#: rule (CLAUDE.md, SCED_SYNC_PY_TIMEOUT) is the same one for the same reason.
GIT_TIMEOUT_SECONDS = 30


def git(verb, args=(), cwd=None, check=False):
    """The one wrapper every git invocation in the tool goes through.

    A verb outside GIT_READONLY refuses at exit 2 -- a fact about how the tool
    was invoked, not about its inputs. test_arknights_gates.py greps the package
    for a `git` invocation that does not come through here.
    """
    if verb not in GIT_READONLY:
        refuse(EXIT_USAGE,
               "git verb %r is not read-only" % verb,
               "GIT_READONLY = %s. arknights runs no mutating git verb; it prints "
               "the commit line and the operator commits." % sorted(GIT_READONLY))
    cmd = ["git"]
    if cwd:
        cmd += ["-C", str(cwd)]
    cmd += [verb] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              timeout=GIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        refuse(EXIT_PRECONDITION,
               "git %s did not return within %ds" % (verb, GIT_TIMEOUT_SECONDS),
               "it wedged rather than failed. On this volume that is usually a "
               "pending TCC consent prompt nothing under launchd can answer -- "
               "see CLAUDE.md's wrapper exit 6 and exit 12 rows.")
    if check and proc.returncode != 0:
        refuse(EXIT_PRECONDITION, "git %s failed (rc %d)" % (verb, proc.returncode),
               proc.stderr.decode("utf-8", "replace").strip())
    return proc


def git_stdout(verb, args=(), cwd=None, check=True):
    return git(verb, args, cwd=cwd, check=check).stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 4. TOLERANCES -- one closed, total table (design section 4.2)
# ---------------------------------------------------------------------------
#
# The flag column is deliberately NOT asserted non-empty: guard.max_files_written
# is a hard cap with no acceptance path at all, and a totality rule reading "every
# row has a registered flag" would fail on its own table the day it is written.
# arknights.sh refuses an unregistered --accept-* at 87, which is what keeps the
# family closed from the one place an operator actually types.


class Tolerance(object):
    __slots__ = ("name", "predicate", "stages", "flag", "exit_code",
                 "selftest_fault", "hard_cap")

    def __init__(self, name, predicate, stages, flag, exit_code, selftest_fault,
                 hard_cap=False):
        self.name = name
        self.predicate = predicate
        self.stages = stages
        self.flag = flag
        self.exit_code = exit_code
        self.selftest_fault = selftest_fault
        self.hard_cap = hard_cap

    def __repr__(self):
        return "<Tolerance %s flag=%s>" % (self.name, self.flag)


TOLERANCES = (
    Tolerance("declared-remainder",
              "N objects carry no metadata that could be repaired",
              ("verify",), "--accept-declared-remainder", EXIT_VERIFY,
              "a synthetic object with no GMNotes and no repair row"),
    Tolerance("max-files-written",
              "the planned write set exceeds guard.max_files_written",
              ("scan", "renumber", "repair", "assemble", "verify"), None, EXIT_GUARD,
              "a plan one entry over the cap", hard_cap=True),
)


def tolerance_flags():
    return set(t.flag for t in TOLERANCES if t.flag)


def all_flags():
    return tolerance_flags()


def row_by_flag(flag):
    for t in TOLERANCES:
        if t.flag == flag:
            return t
    return None


def assert_tables_total():
    """Every row carries an exit code and a named --selftest fault, and every
    registered flag maps back to exactly one row."""
    findings = []
    seen = set()
    for row in TOLERANCES:
        if row.exit_code not in EXIT_MEANING:
            findings.append("TOLERANCES %s: exit %r is not in the exit table"
                            % (row.name, row.exit_code))
        if not row.selftest_fault:
            findings.append("TOLERANCES %s: no named --selftest fault" % row.name)
        if row.flag is None and not row.hard_cap:
            findings.append("TOLERANCES %s: no flag and not declared a hard cap"
                            % row.name)
        if row.name in seen:
            findings.append("TOLERANCES: duplicate row %s" % row.name)
        seen.add(row.name)
    for flag in tolerance_flags():
        if row_by_flag(flag) is None:
            findings.append("flag %s maps to no row" % flag)
    return findings


# ---------------------------------------------------------------------------
# 5. Hashes, time, reads, and the binding{} block
# ---------------------------------------------------------------------------
#
# READS ARE FUNNELLED THROUGH THIS MODULE AND akn_config ON PURPOSE. The
# package's third read-only layer (design section 5.1) is an AST scan asserting
# that every `open()` in the package lives in one of two files and carries a
# literal "r"/"rb" mode; funnelling is what makes that scan's subject set small
# enough to state.

def utc_now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path):
    with open(str(path), "rb") as handle:
        return handle.read()


def read_text(path):
    return read_bytes(path).decode("utf-8")


def read_json(path):
    return json.loads(read_text(path))


def assert_unaliased_prefix(anchor, path, exit_code):
    """`path` must sit under `anchor` with no symlink at ANY component between.

    This is the class four earlier fixes on this path each missed one instance
    of. O_NOFOLLOW and realpath() are both FINAL-component properties -- the
    first refuses a link AT the file, the second RESOLVES the prefix rather than
    refusing it -- so a symlink at an intermediate component satisfies every
    check at once, with an ordinary single-link regular file at the leaf. A
    containment test derives its own prefix through the same link and compares
    the attacker's directory against itself, islink() is false because the last
    component really is a file, and st_nlink is 1 because it really was written
    normally. Measured: all four pass and the forged bytes are returned.

    Returns the anchored path, and the caller MUST use the return value for its
    joins -- resolving the anchor once here is what stops a later realpath()
    re-resolving a component this refused.

    The anchor is the trust boundary and cannot be checked by this function; the
    caller picks one that already bounds the attacker it is defending against.
    """
    rel = os.path.relpath(str(path), str(anchor))
    if os.path.isabs(rel) or rel == os.pardir or \
            rel.startswith(os.pardir + os.sep):
        refuse(exit_code, "%s is not under %s" % (path, anchor),
               "a path outside the anchor has no prefix to check, so this "
               "returns a refusal rather than a weaker guarantee.")
    current = os.path.realpath(str(anchor))
    for part in rel.split(os.sep):
        if part in ("", os.curdir):
            continue
        current = os.path.join(current, part)
        if os.path.islink(current):
            refuse(exit_code, "%s is a symlink" % current,
                   "a COMPONENT of the path, not the file at the end of it -- "
                   "which is why O_NOFOLLOW, islink() on the leaf, st_nlink and "
                   "a realpath containment test all pass anyway. -> %s"
                   % os.path.realpath(current))
    return current


def read_text_nofollow(path, exit_code):
    """Read a file whose properties are asserted on the DESCRIPTOR, for a caller
    that validated the path earlier.

    A path-shaped check states what a name looked like at check time. Anything
    that re-joins the name and open()s it reads whatever is there now, and the
    gap can be human-scale -- publish --restore validates before its LIVE prompt
    and reads after it. O_NOFOLLOW refuses a symlink at the final component in
    the same syscall that opens the file, so there is no gap to plant one in.

    st_nlink is the second half and the one no path test can reach: a hard link
    is invisible to islink() and realpath() reports it as the path itself, so a
    link into ~/.config is indistinguishable from a real snapshot by name alone.
    A file written through a same-dir temp + os.replace has exactly one link.

    S_ISREG is the third, and it is what the other two do not imply: both refuse
    an ALIAS, neither refuses an object of another KIND. A FIFO satisfies every
    predicate on either side of this call -- lexists, islink false, realpath
    inside the root, st_nlink 1 -- and wedges the open, holding the workspace
    lock. Aliasing and kind are separate properties and each needs its own test.

    `exit_code` is the caller's because the band depends on what the caller has
    already done: 80 contracts "nothing was written", which a reader inside a
    write loop cannot promise.
    """
    try:
        # O_NONBLOCK because O_NOFOLLOW says nothing about a FIFO, and open(2)
        # on one WITHOUT it blocks until a writer appears -- which never happens
        # under --restore, and the read is inside the workspace lock, so the
        # wedge is a silent night rather than a hang. It is a no-op for a
        # regular file, so the legitimate path is unchanged.
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        refuse(exit_code, "%s could not be opened as a regular file" % path,
               "%s. A link AT a validated name IS the validated name, which is "
               "why the refusal is the open() and not a check before it." % exc)
    try:
        info = os.fstat(fd)
        # Before st_nlink so the message names the real problem: a FIFO, a
        # device or a directory has st_nlink == 1 too, and a directory would
        # otherwise reach os.read() and raise EISDIR uncaught -- exit 1, outside
        # the documented band.
        if not stat.S_ISREG(info.st_mode):
            refuse(exit_code, "%s is not a regular file" % path,
                   "st_mode says %s. O_NOFOLLOW refuses a link, not an object "
                   "of another kind; take_snapshots() writes a regular file."
                   % stat.filemode(info.st_mode))
        links = info.st_nlink
        if links != 1:
            refuse(exit_code, "%s has %d hard links, not 1" % (path, links),
                   "a file written through a same-dir temp + os.replace has "
                   "exactly one. A second link is somebody else's file under "
                   "this name -- islink() cannot see it and realpath() reports "
                   "it as this path.")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks).decode("utf-8")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def nfc(text):
    """Every path string that crosses into a comparison is NFC-normalised.

    MEASURED 2026-08-30: all 22 pack directory names under docs/Arknights/ come
    back from os.listdir in NFD, while the config file (and every Korean string
    in this repository) is NFC. Comparing the two raw makes every pack row miss.
    """
    return unicodedata.normalize("NFC", text or "")


def sha256_tree_detail(root):
    """Per-file digests plus the folded whole-tree digest below `root`.

    Sorted by the BYTE value of the relative path so the digest does not move with
    a locale -- the same reason the overlap gate runs everything under LC_ALL=C.
    Relative paths are NFC-normalised before hashing: HFS+/APFS hand back NFD and
    a digest that moved with the filesystem's normalisation would make V8 report
    a modified source tree on a machine that had merely copied it.

    The per-file map is what lets V8 NAME the changed path. An aggregate alone
    can only say the 68 MB tree moved, which is the least useful half of the
    finding at exactly the moment it matters.

    Returns (sha256, {relpath: [sha256, size]}, byte_total).
    """
    digest = hashlib.sha256()
    entries = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames.sort()
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            entries.append((nfc(os.path.relpath(full, str(root))), full))
    files = {}
    for rel, full in sorted(entries, key=lambda pair: pair[0].encode("utf-8")):
        one = sha256_file(full)
        size = os.path.getsize(full)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(one.encode("ascii"))
        digest.update(b"\0")
        files[rel] = [one, size]
        total += size
    return digest.hexdigest(), files, total


def sha256_tree(root):
    """The aggregate form. Returns (sha256, file_count, byte_total)."""
    digest, files, total = sha256_tree_detail(root)
    return digest, len(files), total


def build_binding(paths, extra=None):
    """{path: sha256} over the inputs a stage read, plus any declared extras.

    A stage is STALE when any entry no longer matches disk. That is the whole
    resumability mechanism: a re-run of `scan` invalidates everything downstream
    without any stage having to remember it.
    """
    binding = {}
    for path in paths:
        if os.path.exists(str(path)):
            binding[str(path)] = sha256_file(str(path))
    if extra:
        binding.update(extra)
    return binding


# ---------------------------------------------------------------------------
# 6. The report envelope (design section 4.4)
# ---------------------------------------------------------------------------

MODES = ("build", "verify-only", "dry-run", "selftest")

_MODE_SUFFIX = {
    "build": ".json",
    "verify-only": ".verify.json",
    "dry-run": ".dry-run.json",
    "selftest": ".selftest.json",
}


def report_path(run_dir, stage, mode="build"):
    if mode not in MODES:
        refuse(EXIT_USAGE, "unknown mode %r" % mode, "modes are %s" % list(MODES))
    return os.path.join(str(run_dir), stage + _MODE_SUFFIX[mode])


def tool_block():
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    return {
        "path": os.path.abspath(path) if path else None,
        "sha256": sha256_file(path) if path and os.path.exists(path) else None,
        "interpreter": sys.executable,
        "python": "%d.%d.%d" % sys.version_info[:3],
    }


def check(cid, name, detail, exit_on_fail=EXIT_VERIFY, note=None,
          subject_size=None, status=None):
    """One check result. `status` overrides the detail-derived verdict, which is
    what an opt-in check (V12) and a not-yet-reachable one (V10 before publish)
    need in order to print `skipped` rather than a pass they did not earn.

    `subject_size` is not decoration: a check that iterates an EMPTY subject
    reports `pass` with an empty detail[], and nothing else in the result
    distinguishes "checked 424 ids" from "checked nothing".
    """
    if note is None and subject_size is not None:
        note = "%d item(s) in scope" % subject_size
    return {
        "id": cid, "name": name,
        "status": status or ("pass" if not detail else "fail"),
        "exit_on_fail": exit_on_fail,
        "detail": list(detail)[:40],
        "detail_total": len(detail),
        "note": note,
    }


def new_report(stage, run_id, mode="build", counts=None, checks=None,
               binding=None, accepted=None, results=None):
    if mode not in MODES:
        refuse(EXIT_USAGE, "unknown mode %r" % mode, "modes are %s" % list(MODES))
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "run_id": run_id,
        "generated_by": "arknights %s" % stage,
        "generated_at": utc_now(),
        "mode": mode,
        "tool": tool_block(),
        "binding": binding if binding is not None else {},
        "write_set": None,
        "counts": counts if counts is not None else {},
        "checks": checks if checks is not None else [],
        "accepted": accepted if accepted is not None else {},
        "verdict": "PASS",
        "exit_code": EXIT_OK,
        "consumable": False,
        "consumable_blocked_by": None,
        "results": results,
    }


def compute_consumable(report):
    """The one place `consumable` is computed. Returns (bool, blocked_by)."""
    if report.get("mode") != "build":
        return False, "mode == %r" % report.get("mode")
    if report.get("exit_code") != EXIT_OK:
        return False, "exit_code == %s" % report.get("exit_code")
    if test_overrides_present():
        return False, "test overrides present"

    accepted = report.get("accepted") or {}
    for entry in report.get("checks") or []:
        flag_name = entry.get("tolerance")
        if not flag_name or entry.get("status") == "pass":
            continue
        if not accepted.get(flag_name):
            return False, "tolerance %r fired and --accept-%s was not given" % (
                flag_name, flag_name)
    return True, None


def finalize_report(report, triggered=()):
    """Set exit_code from pick_exit, then verdict, then consumable. In that order,
    because consumable reads exit_code."""
    code = pick_exit(triggered)
    report["exit_code"] = code
    if code == EXIT_OK:
        report["verdict"] = "PASS"
    elif code in (EXIT_USAGE, EXIT_GUARD, EXIT_PRECONDITION, EXIT_DRIFT):
        report["verdict"] = "PRECONDITION"
    else:
        report["verdict"] = "FAIL"
    ok, blocked = compute_consumable(report)
    report["consumable"] = ok
    report["consumable_blocked_by"] = blocked
    return report


def write_report(report, run_dir):
    """The only writer of a stage report."""
    if report.get("consumable_blocked_by") is None and not report.get("consumable"):
        finalize_report(report,
                        triggered=() if report.get("exit_code") in (None, 0)
                        else (report["exit_code"],))
    path = report_path(run_dir, report["stage"], report["mode"])
    atomic_write_json(path, report)
    return path


# ---------------------------------------------------------------------------
# 7. Atomic-write wrappers over sced_io
# ---------------------------------------------------------------------------
#
# Thin on purpose: sced_io is the shared writer, so one fix propagates to every
# caller. What these add is path-type coercion and json_bytes(), which owns the
# exact byte shape a binding{} entry is computed against -- a binding computed
# with different bytes than the writer produces is never equal to the file, so
# the stage reads STALE on the very next --status.

def _as_path(path):
    from pathlib import Path
    return path if hasattr(path, "parent") else Path(str(path))


def json_bytes(data):
    """Exactly the bytes atomic_write_json will put on disk."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def atomic_write_json(path, data):
    sced_io.atomic_write_json(_as_path(path), data)


def atomic_write_text(path, text):
    sced_io.atomic_write_text(_as_path(path), text)


# ---------------------------------------------------------------------------
# 8. get_mini_id -- transcribed VERBATIM from Global.ttslua:5418-5429
# ---------------------------------------------------------------------------

#: The renumbering scheme's whole reason for being dash-free (design section 3.1).
#: A short id containing a dash takes the middle branch below and resolves its
#: minicard from only its first five characters, silently.
NEW_ID_RE = re.compile(r"^akn(0[0-9]|1[0-4]|a[1-5]|r[12])[0-9]{3}$")


def get_mini_id(base_id):
    """Global.ttslua:5418-5429, transcribed.

    -- constructs the mini id for a given investigator id
    function getMiniId(baseId)
      if not string.contains(baseId, "-") then return baseId .. "-m"
      elseif #baseId < 16 then return string.match(baseId, ".....") .. "-m"
      else return baseId .. "-m" end
    end
    """
    if "-" not in base_id:
        return base_id + "-m"
    elif len(base_id) < 16:
        return base_id[:5] + "-m"
    else:
        return base_id + "-m"


def mini_id_branch(base_id):
    """Which of getMiniId's three branches `base_id` takes. V4 asserts branch 1."""
    if "-" not in base_id:
        return 1
    elif len(base_id) < 16:
        return 2
    return 3


# ---------------------------------------------------------------------------
# 8b. Borrowing SCED-downloads/misc/ -- imported, never reimplemented
# ---------------------------------------------------------------------------
#
# Both modules this loads own a definition the tool must agree with exactly:
# memory-bag-updater.py owns the bundled MemoryBag Lua (so the updater is
# provably a no-op on our file), and sort_library.py owns KEY_ORDER and the sort
# (so there is exactly one canonical library.json order). Loading them BY PATH
# rather than by name is forced: `memory-bag-updater` is not an identifier, and
# adding SCED-downloads/misc to sys.path would put every module in that directory
# on the import path of a tool that wants exactly two of them.

def load_module_by_path(name, path):
    """Import a module from an explicit file path. Refuses at 81 when absent."""
    import importlib.util
    if not os.path.exists(str(path)):
        refuse(EXIT_PRECONDITION, "%s is missing" % name, str(path))
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def memory_bag_luascript(workspace=None, updater_rel=None):
    """The top bag's LuaScript, built by the updater's OWN construction.

    Instantiating TTSUpdater and taking `replacement_content` -- rather than
    pasting the bundle from an existing pack -- is what makes
    misc/memory-bag-updater.py provably a no-op on the file we ship: it is the
    same code path, reading the same two sources.
    """
    workspace = workspace or WORKSPACE_ROOT
    path = os.path.join(workspace,
                        updater_rel or "SCED-downloads/misc/memory-bag-updater.py")
    updater = load_module_by_path("akn_memory_bag_updater", path)
    built = updater.TTSUpdater(updater.SEARCH_TEXT, updater.SEARCH_TEXT_2,
                               updater.MB_SCRIPT_FILE, updater.MB_WRAPPER_FILE)
    return built.replacement_content


# ---------------------------------------------------------------------------
# 9. The package tier scans -- what --selftest is the subject of
# ---------------------------------------------------------------------------

#: The declared subject set. A module absent from disk is COUNTED as pending and
#: printed, never skipped silently: the stages land in dependency order (S1..S7)
#: and a scan that read absence as failure would be permanently red at the very
#: step it is the completion predicate of.
PACKAGE_MODULES = ("akn_common.py", "akn_config.py", "akn_scan.py",
                   "akn_renumber.py", "akn_repair.py", "akn_assemble.py",
                   "akn_verify.py", "akn_publish.py")

#: Explicit rather than sys.stdlib_module_names, which does not exist on 3.9.6 --
#: and an explicit list is the stronger form anyway: it is closed and reviewable.
STDLIB_ALLOWLIST = frozenset([
    "argparse", "ast", "base64", "binascii", "collections", "contextlib", "copy",
    "csv", "datetime", "difflib", "errno", "fnmatch", "functools", "glob",
    "hashlib", "importlib", "io", "itertools", "json", "math", "os", "pathlib",
    "posixpath", "pprint", "py_compile", "re", "shlex", "shutil", "signal",
    "socket", "stat", "string", "struct", "subprocess", "sys", "tempfile",
    "textwrap", "time", "traceback", "types", "typing", "unicodedata", "urllib",
    "uuid",
])

#: Closed in both directions: an import outside the stdlib allowlist fails, and
#: so does an import of any package module outside this triple.
PACKAGE_IMPORTS = frozenset(["akn_common", "akn_config", "sced_io"])

#: The ONLY two files allowed to call open(), and only with a read mode. Every
#: other module reads through akn_common.read_* / akn_config.read_source().
OPEN_ALLOWLIST = frozenset(["akn_common.py", "akn_config.py"])
READ_MODES = frozenset(["r", "rb", "rt"])


def _module_paths():
    return [(name, os.path.join(PACKAGE_DIR, name)) for name in PACKAGE_MODULES]


def modules_pending():
    return [name for name, path in _module_paths() if not os.path.exists(path)]


def scan_imports():
    """AST-scan the package for an import outside the two allowlists."""
    findings = []
    for name, path in _module_paths():
        if not os.path.exists(path):
            continue
        try:
            tree = ast.parse(read_bytes(path), filename=path)
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
                if not top or top in STDLIB_ALLOWLIST or top in PACKAGE_IMPORTS:
                    continue
                findings.append("%s:%d: imports %r, outside the stdlib allowlist "
                                "and outside %s"
                                % (name, node.lineno, top, sorted(PACKAGE_IMPORTS)))
    return findings


def scan_open_modes():
    """The third read-only layer (design section 5.1).

    68 MB with exactly one copy on a volume with no Time Machine destination
    deserves more than a convention, so "the converter never opens a source path
    for writing" is asserted over the SYNTAX rather than over the behaviour of
    one run: every open() in the package must live in OPEN_ALLOWLIST and carry a
    literal read mode. A write mode anywhere -- source path or not -- is a
    finding, because a scan that had to decide at parse time whether a path
    expression resolves under docs/Arknights/ could not be sound.
    """
    findings = []
    for name, path in _module_paths():
        if not os.path.exists(path):
            continue
        try:
            tree = ast.parse(read_bytes(path), filename=path)
        except SyntaxError:
            continue  # reported by scan_imports()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fname = getattr(func, "id", None) or getattr(func, "attr", None)
            if fname != "open":
                continue
            if getattr(getattr(func, "value", None), "id", None) == "os":
                # os.open takes FLAGS, not a mode string, so the READ_MODES rule
                # cannot be applied to it. Two shapes are permitted, each because
                # its flags make the call unable to destroy anything: O_EXCL,
                # whose create fails rather than truncating (the nightly lock's
                # test-and-set), and O_RDONLY|O_NOFOLLOW, which cannot write at
                # all and refuses a symlink at the final component in the same
                # syscall that opens it (read_text_nofollow). Anything else is a
                # finding -- a scan that had to decide at parse time what a flag
                # expression evaluates to could not be sound.
                flags = ast.dump(node.args[1]) if len(node.args) > 1 else ""
                permitted = ("O_EXCL" in flags
                             or ("O_RDONLY" in flags and "O_NOFOLLOW" in flags))
                if name not in OPEN_ALLOWLIST or not permitted:
                    findings.append("%s:%d: os.open outside %s, or without "
                                    "O_EXCL or O_RDONLY|O_NOFOLLOW -- only an "
                                    "exclusive create and a no-follow read are "
                                    "permitted"
                                    % (name, node.lineno, sorted(OPEN_ALLOWLIST)))
                continue
            if name not in OPEN_ALLOWLIST:
                findings.append("%s:%d: open() outside %s -- read through "
                                "akn_common.read_* or akn_config.read_source()"
                                % (name, node.lineno, sorted(OPEN_ALLOWLIST)))
                continue
            mode = "r"
            if len(node.args) > 1:
                arg = node.args[1]
                mode = arg.value if isinstance(arg, ast.Constant) else "<dynamic>"
            for kw in node.keywords:
                if kw.arg == "mode":
                    mode = (kw.value.value if isinstance(kw.value, ast.Constant)
                            else "<dynamic>")
            if mode not in READ_MODES:
                findings.append("%s:%d: open(mode=%r) -- only %s are permitted; "
                                "writes go through atomic_write_*"
                                % (name, node.lineno, mode, sorted(READ_MODES)))
    return findings


#: The one function permitted to build a git argv. AST-scoped, not text-scoped:
#: a text grep for "git" matches the scanner's own source and the prose around
#: it, which is how a check comes to be disabled by the comment explaining it.
GIT_WRAPPER = ("akn_common.py", "git")


def scan_git_calls():
    """Every git invocation must go through the GIT_READONLY wrapper.

    Also refuses os.system / os.popen outright: both take a SHELL STRING, so a
    path or a url interpolated into one is a command-injection site, and this
    tool interpolates operator-supplied paths (--run, --source) and source-supplied
    urls (--probe-urls). subprocess with a list argv has no such hazard.
    """
    findings = []
    shell_runners = ("system", "popen")
    for name, path in _module_paths():
        if not os.path.exists(path):
            continue
        try:
            tree = ast.parse(read_bytes(path), filename=path)
        except SyntaxError:
            continue  # reported by scan_imports()
        wrappers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and (name, node.name) == GIT_WRAPPER:
                wrappers.update(id(inner) for inner in ast.walk(node))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = getattr(func, "attr", None)
            owner = getattr(getattr(func, "value", None), "id", None)
            if owner == "os" and attr in shell_runners:
                findings.append("%s:%d: os.%s takes a shell string -- use "
                                "subprocess with a list argv"
                                % (name, node.lineno, attr))
                continue
            if owner != "subprocess" or id(node) in wrappers:
                continue
            if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
                continue
            head = node.args[0].elts[0] if node.args[0].elts else None
            if isinstance(head, ast.Constant) and head.value == "git":
                findings.append("%s:%d: a bare git argv -- every invocation goes "
                                "through akn_common.git()" % (name, node.lineno))
    return findings


def compile_package():
    """py_compile the package with the PLATFORM interpreter, as a subprocess.

    A subprocess and not an import, because the point is to exercise 3.9.6's own
    parser -- which is not the interpreter running this scan when pytest is on
    Homebrew's 3.14.

    sced_io.py is compiled with the package and NOT added to PACKAGE_MODULES,
    which also drives the AST scans and modules_pending. It is imported at
    runtime but owned by ~30 other scripts, most of them on the Homebrew 3.14
    tier, while arknights is the only consumer pinned to Apple's 3.9.6.

    It is also IMPORTED, not merely compiled, and the two catch different halves.
    Measured on this box's 3.9.6: py_compile catches `match` and `except*` and
    does NOT catch a PEP-604 `int | str` annotation, because an annotation is an
    expression -- it parses on 3.9 and raises TypeError when the def executes.
    sced_io.py already carries a PEP-585 generic, so annotations are exactly the
    direction that file drifts, and compiling alone would have missed it.
    """
    if not os.path.exists(PLATFORM_PYTHON):
        return ["%s is absent; the 3.9 compatibility check cannot run"
                % PLATFORM_PYTHON]
    paths = [p for _n, p in _module_paths() if os.path.exists(p)]
    shared = os.path.join(SCRIPTS_DIR, "sced_io.py")
    if os.path.exists(shared):
        paths.append(shared)
    if not paths:
        return []
    findings = []
    proc = subprocess.run([PLATFORM_PYTHON, "-m", "py_compile"] + paths,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        findings.append("%s -m py_compile rc %d:\n%s"
                        % (PLATFORM_PYTHON, proc.returncode,
                           proc.stdout.decode("utf-8", "replace").strip()))
    if os.path.exists(shared):
        proc = subprocess.run(
            [PLATFORM_PYTHON, "-c",
             "import sys; sys.path.insert(0, %r); import sced_io" % SCRIPTS_DIR],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            findings.append("%s could not import sced_io (rc %d):\n%s"
                            % (PLATFORM_PYTHON, proc.returncode,
                               proc.stdout.decode("utf-8", "replace").strip()))
    return findings


# ---------------------------------------------------------------------------
# 10. --selftest
# ---------------------------------------------------------------------------

def selftest(verbose=True):
    """Every mechanical property this module owns. Returns a findings list."""
    findings = []
    findings += assert_tables_total()
    findings += scan_imports()
    findings += scan_open_modes()
    findings += scan_git_calls()
    findings += compile_package()

    if pick_exit([]) != EXIT_OK:
        findings.append("pick_exit([]) != 0")
    if pick_exit([EXIT_VERIFY, EXIT_DRIFT]) != EXIT_DRIFT:
        findings.append("pick_exit({86,82}) must be 82 -- the cause, not the symptom")
    if pick_exit([EXIT_RENUMBER, EXIT_GUARD]) != EXIT_GUARD:
        findings.append("pick_exit({83,80}) must be 80 -- nothing was written")
    if set(EXIT_PRECEDENCE) & set(DISPATCHER_BAND):
        findings.append("EXIT_PRECEDENCE overlaps the dispatcher band")
    if len(set(EXIT_PRECEDENCE)) != len(EXIT_PRECEDENCE):
        findings.append("EXIT_PRECEDENCE has a duplicate")
    if EXIT_BUG in EXIT_PRECEDENCE:
        findings.append("1 must never be in EXIT_PRECEDENCE")
    for code in list(EXIT_MEANING) + list(EXIT_PRECEDENCE):
        if code not in (0, 1, 2) and not (80 <= code <= 89):
            findings.append("exit code %d is outside the {80..89} band" % code)

    for verb in ("add", "commit", "checkout", "clean", "reset", "fetch", "push",
                 "stash"):
        try:
            git(verb, [])
        except AknRefusal as exc:
            if exc.code != EXIT_USAGE:
                findings.append("git(%r) refused with %d, expected 2"
                                % (verb, exc.code))
        else:
            findings.append("git(%r) was not refused" % verb)

    # P0b: a Homebrew path must refuse, a platform path must not.
    try:
        check_p0b("/opt/homebrew/bin/python3")
    except AknRefusal as exc:
        if exc.code != EXIT_USAGE:
            findings.append("check_p0b(homebrew) refused with %d, expected 2"
                            % exc.code)
    else:
        findings.append("check_p0b(/opt/homebrew/bin/python3) was not refused")
    try:
        check_p0b()  # the interpreter actually running this selftest
    except AknRefusal as exc:
        findings.append("check_p0b(self) refused: %s" % exc)
    for hostile in ("/opt/homebrew/opt/python@3.14/bin/python3.14",
                    "/usr/local/bin/python3", os.path.expanduser("~/.pyenv/shims/python3")):
        if interpreter_is_platform(hostile):
            findings.append("check_p0b: %s was accepted as a platform interpreter"
                            % hostile)

    # get_mini_id: the three branches, and the dash-free property the scheme rests on.
    if get_mini_id("akn06005") != "akn06005-m":
        findings.append("get_mini_id: branch 1 (dash-free) is wrong")
    if get_mini_id("akn06-005") != "akn06-m":
        findings.append("get_mini_id: branch 2 (short, dashed) is wrong")
    if get_mini_id("a" * 16 + "-x") != "a" * 16 + "-x-m":
        findings.append("get_mini_id: branch 3 (>=16) is wrong")
    for good in ("akn00001", "akn14027", "akna5011", "aknr2004"):
        if not NEW_ID_RE.match(good) or len(good) != 8 or mini_id_branch(good) != 1:
            findings.append("NEW_ID_RE / branch: %r should be a valid branch-1 id"
                            % good)
    for bad in ("akn15001", "aknr3001", "akn0001", "akn00001-m", "AKN00001"):
        if NEW_ID_RE.match(bad):
            findings.append("NEW_ID_RE accepted %r" % bad)

    # NFC: the measured 2026-08-30 property the pack table depends on.
    if nfc(unicodedata.normalize("NFD", "대체물")) != "대체물":
        findings.append("nfc() does not fold NFD to NFC")

    if verbose:
        pending = modules_pending()
        if pending:
            print("  pending modules (not yet built): %s" % ", ".join(pending))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_common.py",
        description="arknights shared foundation. --selftest exercises every "
                    "property this module owns.")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--exit-table", action="store_true",
                        help="print the exit table and the precedence list")
    args = parser.parse_args(argv)

    if args.exit_table:
        for code in sorted(EXIT_MEANING):
            print("%3d  %s" % (code, EXIT_MEANING[code]))
        print("\nEXIT_PRECEDENCE = %s" % EXIT_PRECEDENCE)
        print("dispatcher band = %s (returned instead of a stage code, never "
              "with one)" % list(DISPATCHER_BAND))
        return EXIT_OK

    if args.selftest:
        print("akn_common --selftest")
        findings = selftest()
        if findings:
            print("\nFAIL (%d):" % len(findings))
            for line in findings:
                print("  - %s" % line)
            return EXIT_VERIFY
        print("  ok: tolerance table total, package scans clean (imports, open "
              "modes, git), 3.9 compile, pick_exit, GIT_READONLY, P0b, "
              "get_mini_id, NFC")
        return EXIT_OK

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AknRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
