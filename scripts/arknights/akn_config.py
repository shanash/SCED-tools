#!/usr/bin/python3
"""arknights: the config loader and the write guard (design section 2 row 3).

THIS MODULE OWNS THE READ-ONLY GUARANTEE. docs/Arknights/ is 68 MB, gitignored,
with exactly one copy on a volume that has no Time Machine destination, so "the
converter did not touch the source" is made a measured fact three ways rather
than a property of how the code happens to be written (design section 5.1):

  1. guard.write_roots is a CLOSED list that does not contain the source root,
     and every write in the tool goes through write_guarded() below.
  2. read_source() is the only function that opens a path under the source root,
     and it opens "rb" only.
  3. akn_common.scan_open_modes() AST-scans the package: every open() must live
     in this file or akn_common.py and carry a literal read mode.

V8 is the fourth layer and the only one that is empirical -- it re-hashes the
whole tree after the run and compares. Layers 1-3 make a write impossible to
express; layer 4 catches one arriving from outside the tool.

It also owns the three things the nightly shares with this tool: the schedule
WINDOW (the union over every repos{} entry in sync-schedule.json, never
hardcoded), the read-only LOCK PROBE, and NightlyLock -- the acquisition itself.
The lock lives here beside the probe and the owner format so one file owns the
whole protocol, but `publish` is the only stage that ever instantiates it; every
other stage writes inside its run directory and has nothing to collide with.
"""

import argparse
import collections
import errno
import json
import os
import re
import socket
import sys
import time

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

import akn_common as kc  # noqa: E402  (path is set immediately above)

SCHEMA_VERSION = "1.0.0"

CONFIG_PATH = os.path.join(PACKAGE_DIR, "data", "arknights.packs.json")
IDMAP_PATH = os.path.join(PACKAGE_DIR, "data", "arknights.idmap.json")

SCHEDULE_JSON = os.path.join(kc.WORKSPACE_ROOT, "SCED-tools", "config",
                             "sync-schedule.json")
LOCK_DIR = os.path.join(kc.WORKSPACE_ROOT, ".local-sync", "run", "daily-sync.lock")
LOCK_OWNER = os.path.join(LOCK_DIR, "owner")

WINDOW_PRE_MIN = 15   # before the earliest local start
WINDOW_POST_MIN = 60  # after the latest CI start

# ---------------------------------------------------------------------------
# 1. The stage DAG (design section 4.1)
# ---------------------------------------------------------------------------
#
# Linear, and stated as such because unlike koreanize's there is nothing to draw.
# `publish` is the terminal stage, and the only one that writes outside its run
# directory.

STAGES = ("scan", "renumber", "repair", "assemble", "verify", "publish")

PREDECESSORS = {
    "scan": (),
    "renumber": ("scan",),
    "repair": ("renumber",),
    "assemble": ("repair",),
    "verify": ("assemble",),
    "publish": ("verify",),
}

RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(-[a-z0-9]+)?$")


# ---------------------------------------------------------------------------
# 2. Loading and validating the one config file
# ---------------------------------------------------------------------------

_REQUIRED_TOP = ("schema", "guard", "run_root", "id_scheme", "packs",
                 "role_overrides", "exceptions", "repairs", "declared_remainders",
                 "many_to_one", "url_normalisation", "bag", "library_entry",
                 "library_rules", "oracles")
_REQUIRED_GUARD = ("source_root", "source_containment_root", "packs_subdir",
                   "write_roots", "forbidden", "data_root", "max_files_written")

PACK_CODE_RE = re.compile(r"^(0[0-9]|1[0-4]|a[1-5]|r[12])$")

#: The closed set of patch rules akn_repair implements. A row naming anything
#: else is refused here rather than at the stage, so the config cannot describe a
#: repair the tool has no code for.
REPAIR_RULES = ("set_fields", "minicard_from_investigator")

#: The closed set of roles a role_override may assert.
ROLES = ("investigator", "minicard", "card")

#: The patchable object fields. Anything else is refused: a config row that could
#: write an arbitrary key would let the table reshape a TTS object rather than
#: repair one.
PATCH_FIELDS = ("Tags", "Nickname", "Description", "GMNotes")


def compute_config_sha256(cfg):
    """The pin. Computed over the canonical byte form so a reordering of keys in
    the file does not move it, and an edit to any VALUE does."""
    return kc.sha256_bytes(json.dumps(cfg, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8"))


def validate(cfg, path=CONFIG_PATH):
    """Refuses at 87 -- the dispatcher's code, because a config this tool cannot
    read means no stage can run and none did."""
    for key in _REQUIRED_TOP:
        if key not in cfg:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED, "config is missing %r" % key, path)
    guard = cfg["guard"]
    for key in _REQUIRED_GUARD:
        if key not in guard:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED, "config guard is missing %r" % key,
                      path)

    if guard["source_root"] in guard["write_roots"]:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "guard.source_root is listed in guard.write_roots",
                  "the write roots are a closed list that must not contain the "
                  "source; this is layer 1 of the read-only guarantee")
    for root in guard["write_roots"]:
        if root.startswith(guard["source_containment_root"]):
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "guard.write_roots entry %r is under the source containment "
                      "root" % root, path)
    if not guard["data_root"].startswith("SCED-tools/scripts/arknights/"):
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "guard.data_root %r does not resolve inside the package"
                  % guard["data_root"], path)

    packs = cfg["packs"]
    if len(packs) != cfg["oracles"]["packs"]:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "the pack table has %d rows, oracles.packs says %d"
                  % (len(packs), cfg["oracles"]["packs"]), path)
    codes, folders = set(), set()
    for row in packs:
        for key in ("code", "folder", "nickname"):
            if not row.get(key):
                kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                          "pack row %r is missing %r" % (row, key), path)
        if not PACK_CODE_RE.match(row["code"]):
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "pack code %r is outside the id_scheme regex's pack set"
                      % row["code"], path)
        if row["code"] in codes:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED, "duplicate pack code %r"
                      % row["code"], path)
        folder = kc.nfc(row["folder"])
        if folder in folders:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED, "duplicate pack folder %r" % folder,
                      path)
        codes.add(row["code"])
        folders.add(folder)

    entry = cfg["library_entry"]
    rules = cfg["library_rules"]
    for key in rules["forbidden_keys"]:
        if key in entry:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "library_entry carries the forbidden key %r" % key,
                      "cycle_code is the safety mechanism of this whole change; "
                      "the tool refuses to write it even if a config edit adds it")
    if entry.get("author") == rules["forbidden_author"]:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "library_entry.author is %r" % rules["forbidden_author"],
                  "DW_TAB_IDS has no official playercards tab; such an entry is "
                  "silently invisible")
    if entry.get("boxsize") not in rules["boxsize_values"]:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "library_entry.boxsize %r is not one of %s"
                  % (entry.get("boxsize"), rules["boxsize_values"]), path)
    if entry.get("filename") != "arknights":
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "library_entry.filename must be 'arknights' -- Global.ttslua:2497 "
                  "builds SOURCE_REPO .. filename .. '.json' and release assets are "
                  "uploaded flat", path)
    if not str(entry.get("boxart", "")).startswith("https://"):
        kc.refuse(kc.EXIT_DISPATCH_REFUSED, "library_entry.boxart is not https://",
                  path)

    _validate_rows(cfg, codes, path)
    return cfg


def _validate_rows(cfg, codes, path):
    """Schema of the role_override, exception and repair tables.

    Structure only -- whether a row RESOLVES is a fact about the inventory and is
    checked by the stage that reads it, in both directions. What is settled here
    is that a row cannot name a rule with no code behind it, a pack that does not
    exist, or a field the patcher will not write.
    """
    seen = set()
    for row in cfg["role_overrides"]:
        _require(row, ("id", "pack", "file", "path", "role"), "role_override", path)
        if row["role"] not in ROLES:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "role_override %s asserts role %r, not one of %s"
                      % (row["id"], row["role"], list(ROLES)), path)
        _unique(row["id"], seen, "role_override", path)
        _known_pack(row["pack"], codes, row["id"], path)

    seen = set()
    for row in cfg["exceptions"]:
        _require(row, ("id", "pack", "whole_pack", "assign", "evidence"),
                 "exception", path)
        _unique(row["id"], seen, "exception", path)
        _known_pack(row["pack"], codes, row["id"], path)
        if not row["assign"]:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "exception %s assigns nothing" % row["id"], path)
        for item in row["assign"]:
            by_path = "file" in item and "path" in item
            by_id = "source_id" in item
            if by_path == by_id:
                kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                          "exception %s: an assign row needs exactly one selector "
                          "form -- {file,path} or {source_id} -- got %r"
                          % (row["id"], sorted(item)), path)
            if not isinstance(item.get("ordinal"), int) or item["ordinal"] < 0:
                kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                          "exception %s: ordinal %r is not a non-negative int"
                          % (row["id"], item.get("ordinal")), path)

    seen = set()
    for row in cfg["repairs"]:
        _require(row, ("id", "rule", "targets", "evidence"), "repair", path)
        _unique(row["id"], seen, "repair", path)
        if row["rule"] not in REPAIR_RULES:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "repair %s names rule %r, which no code implements (%s)"
                      % (row["id"], row["rule"], list(REPAIR_RULES)), path)
        if not row["targets"]:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "repair %s targets nothing -- a row matching no object is "
                      "exit 84, and an EMPTY row would never even get there"
                      % row["id"], path)
        for target in row["targets"]:
            _require(target, ("pack", "file", "path"),
                     "repair %s target" % row["id"], path)
            _known_pack(target["pack"], codes, row["id"], path)
            fields = target.get("fields", row.get("fields"))
            if row["rule"] == "set_fields" and not fields:
                kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                          "repair %s: set_fields with no fields on %s %s"
                          % (row["id"], target["file"], target["path"]), path)
            for key in (fields or {}):
                if key not in PATCH_FIELDS:
                    kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                              "repair %s patches %r, which is not one of %s"
                              % (row["id"], key, list(PATCH_FIELDS)), path)

    remainders = cfg["declared_remainders"]
    if not isinstance(remainders, int) or remainders < 0:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "declared_remainders %r is not a non-negative int" % remainders,
                  path)


def _require(row, keys, what, path):
    for key in keys:
        if key not in row:
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "%s row is missing %r" % (what, key), "%s: %r" % (path, row))


def _unique(value, seen, what, path):
    if value in seen:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED, "duplicate %s id %r" % (what, value),
                  path)
    seen.add(value)


def _known_pack(code, codes, owner, path):
    if code not in codes:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                  "%s names pack %r, which has no row in the pack table"
                  % (owner, code), path)


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_DISPATCH_REFUSED, "config not found", path)
    try:
        cfg = json.loads(kc.read_text(path))
    except ValueError as exc:
        kc.refuse(kc.EXIT_DISPATCH_REFUSED, "config does not parse: %s" % exc, path)
    validate(cfg, path)
    cfg["_path"] = path
    cfg["_sha256"] = kc.sha256_file(path)
    return cfg


def pack_by_folder(cfg):
    return dict((kc.nfc(row["folder"]), row) for row in cfg["packs"])


def pack_by_code(cfg):
    return dict((row["code"], row) for row in cfg["packs"])


# ---------------------------------------------------------------------------
# 3. Paths: the source root, the run directory, and the containment rule
# ---------------------------------------------------------------------------

def source_root(cfg, override=None, workspace=None):
    """Resolve the source root, honouring `--source` but never leaving the
    containment root.

    The containment check is a realpath prefix test and not a string test, so a
    `--source ../../etc` or a symlink out of the tree is exit 80 rather than a
    read of somewhere else entirely.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    contain = os.path.realpath(os.path.join(
        workspace, cfg["guard"]["source_containment_root"]))
    cand = override or os.path.join(workspace, cfg["guard"]["source_root"])
    real = os.path.realpath(cand)
    if real != contain and not real.startswith(contain + os.sep):
        kc.refuse(kc.EXIT_GUARD,
                  "--source %s resolves outside the containment root" % cand,
                  "%s is not under %s. Nothing was read." % (real, contain))
    if not os.path.isdir(real):
        kc.refuse(kc.EXIT_PRECONDITION, "the source root is not a directory", real)
    return real


def packs_root(cfg, root):
    """The 22 pack folders live one level below the hashed source root."""
    return os.path.join(root, cfg["guard"]["packs_subdir"])


def is_pack_file(name):
    """A pack file is a `.json` that is not an AppleDouble sidecar.

    macOS writes `._Name.json` beside the real file on a non-native copy, and it
    holds resource-fork bytes. A plain suffix test admits it, so the parse below
    would fail as a tool defect instead of as input drift.
    """
    return name.endswith(".json") and not name.startswith("._")


def read_tts_save(cfg, relpath, root=None, workspace=None):
    """`read_source` plus the JSON parse, with a named refusal on either half.

    Exit 82 rather than an unhandled exception: a file under the pack folders
    that is not a readable TTS save is input drift -- the same class as the
    `ObjectStates` check its callers make on the very next line.
    """
    blob = read_source(cfg, relpath, root=root, workspace=workspace)
    try:
        return json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        kc.refuse(kc.EXIT_DRIFT, "%s is not a readable TTS save" % relpath,
                  "%s: %s" % (type(exc).__name__, exc))


def read_source(cfg, relpath, root=None, workspace=None):
    """THE ONLY READ PATH INTO docs/Arknights/. Binary, containment-checked.

    Returns bytes. Callers that want JSON decode it themselves -- keeping this
    function byte-level is what lets the AST scan assert one literal "rb" here
    rather than reasoning about a family of modes.
    """
    root = root or source_root(cfg, workspace=workspace)
    real = os.path.realpath(os.path.join(root, relpath))
    if not real.startswith(os.path.realpath(root) + os.sep):
        kc.refuse(kc.EXIT_GUARD, "read_source escaped the source root",
                  "%s is not under %s" % (real, root))
    with open(real, "rb") as handle:
        return handle.read()


def run_root(cfg, workspace=None):
    return os.path.join(workspace or kc.WORKSPACE_ROOT, cfg["run_root"])


def mint_run_id(now=None):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now else time.time()))


def resolve_run_dir(cfg, run_id=None, workspace=None, must_exist=False):
    """Run directory for `run_id`, or the newest one when none is given.

    The run id is matched against RUN_ID_RE before it is joined to anything: it
    arrives from argv, and a `--run ../../SCED/objects` would otherwise name a
    directory the write guard is then asked to bless.
    """
    root = run_root(cfg, workspace)
    if run_id:
        if not RUN_ID_RE.match(run_id):
            kc.refuse(kc.EXIT_DISPATCH_REFUSED,
                      "run id %r is not a <YYYYMMDDTHHMMSSZ> stamp" % run_id,
                      "the id is joined to a path; a traversing value is refused "
                      "before it is used")
        path = os.path.join(root, run_id)
    else:
        if not os.path.isdir(root):
            return None
        candidates = sorted(name for name in os.listdir(root)
                            if RUN_ID_RE.match(name)
                            and os.path.isdir(os.path.join(root, name)))
        if not candidates:
            return None
        path = os.path.join(root, candidates[-1])
    if must_exist and not os.path.isdir(path):
        kc.refuse(kc.EXIT_PRECONDITION, "run directory does not exist", path)
    return path


# ---------------------------------------------------------------------------
# 4. The write guard (design section 4.3, exit 80)
# ---------------------------------------------------------------------------

GUARD_FINDING_SAMPLE = 12


def _rel_to_workspace(path, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    real = os.path.realpath(str(path))
    root = os.path.realpath(workspace)
    if real == root:
        return ""
    if not real.startswith(root + os.sep):
        return None
    return real[len(root) + 1:]


def check_write_paths(cfg, paths, workspace=None):
    """Evaluate write_roots FIRST, then forbidden. Returns a list of findings.

    Order matters: guard.forbidden lists "docs/", which is also the source root,
    so a path evaluated against forbidden first would report the wrong reason for
    the right refusal -- and the reason is what an operator acts on.
    """
    guard = cfg["guard"]
    write_roots = [r.rstrip("/") for r in guard["write_roots"]]
    findings = []
    for path in paths:
        rel = _rel_to_workspace(path, workspace)
        if rel is None:
            findings.append("%s resolves outside the workspace" % path)
            continue
        rel_posix = rel.replace(os.sep, "/")
        if not any(rel_posix == r or rel_posix.startswith(r + "/")
                   for r in write_roots):
            findings.append("%s is outside guard.write_roots %s"
                            % (rel_posix, write_roots))
            continue
        for bad in guard["forbidden"]:
            if rel_posix.startswith(bad):
                findings.append("%s matches guard.forbidden %r" % (rel_posix, bad))
                break
    return findings


def assert_write_paths(cfg, paths, workspace=None):
    paths = list(paths)
    findings = check_write_paths(cfg, paths, workspace=workspace)
    if findings:
        detail = "; ".join(findings[:GUARD_FINDING_SAMPLE])
        if len(findings) > GUARD_FINDING_SAMPLE:
            detail += "; ... and %d more" % (len(findings) - GUARD_FINDING_SAMPLE)
        kc.refuse(kc.EXIT_GUARD, "planned write set violates the path guard", detail)
    cap = cfg["guard"]["max_files_written"]
    if len(paths) > cap:
        kc.refuse(kc.EXIT_GUARD,
                  "planned write set of %d exceeds guard.max_files_written %d"
                  % (len(paths), cap),
                  "this is a hard cap with no acceptance path (TOLERANCES, hard_cap)")
    return paths


def write_guarded(cfg, path, data, workspace=None):
    """The one writer. Every stage's output goes through here, which is what makes
    layer 1 of the read-only guarantee a structure rather than a convention."""
    assert_write_paths(cfg, [path], workspace=workspace)
    if isinstance(data, (bytes, str)):
        kc.atomic_write_text(path, data.decode("utf-8") if isinstance(data, bytes)
                             else data)
    else:
        kc.atomic_write_json(path, data)
    return path


# ---------------------------------------------------------------------------
# 5. The nightly schedule window -- the UNION over every repo
# ---------------------------------------------------------------------------

def _hhmm_to_min(hhmm):
    text = str(hhmm).zfill(4)
    return int(text[:2]) * 60 + int(text[2:])


def _min_to_hhmm(minutes):
    minutes %= 24 * 60
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def schedule_window(schedule_path=None):
    """[min(local_hhmm) - 15, max(latest_hhmm) + 60] over EVERY repos{} entry.

    The lock is workspace-wide, so a window derived from one repo's bounds refuses
    at the wrong times. Never hardcoded: CLAUDE.md's own rule is that the schedule
    lives in one place and everything downstream renders from it.
    """
    path = schedule_path or SCHEDULE_JSON
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "sync-schedule.json not found", path)
    sched = json.loads(kc.read_text(path))
    repos = sched.get("repos") or {}
    if not repos:
        kc.refuse(kc.EXIT_PRECONDITION, "sync-schedule.json declares no repos{}",
                  path)
    starts = [(_hhmm_to_min(repos[n]["local_hhmm"]), n) for n in sorted(repos)]
    latests = [(_hhmm_to_min(repos[n]["latest_hhmm"]), n) for n in sorted(repos)]
    lo_min, lo_repo = min(starts)
    hi_min, hi_repo = max(latests)
    return {
        "start_min": (lo_min - WINDOW_PRE_MIN) % (24 * 60),
        "end_min": (hi_min + WINDOW_POST_MIN) % (24 * 60),
        "start": _min_to_hhmm(lo_min - WINDOW_PRE_MIN),
        "end": _min_to_hhmm(hi_min + WINDOW_POST_MIN),
        "start_from": lo_repo,
        "end_from": hi_repo,
        "timezone": sched.get("timezone", "Asia/Seoul"),
        "source": path,
    }


def in_schedule_window(now_min=None, schedule_path=None):
    win = schedule_window(schedule_path)
    if now_min is None:
        local = time.localtime()
        now_min = local.tm_hour * 60 + local.tm_min
    start, end = win["start_min"], win["end_min"]
    if start <= end:
        inside = start <= now_min < end
    else:  # a window that wraps midnight
        inside = now_min >= start or now_min < end
    return inside, win


def assert_outside_window(now_min=None, schedule_path=None):
    """Refuse inside the nightly's window even when the lock is free.

    A probe closes nothing: it is a TOCTOU race, and nothing stops a write that
    begins at 02:16 from still writing when the driver takes the lock at 02:17.
    Only `publish` calls this -- the read-only stages have nothing to collide.
    """
    inside, win = in_schedule_window(now_min=now_min, schedule_path=schedule_path)
    if inside:
        kc.refuse(kc.EXIT_GUARD,
                  "inside the nightly's schedule window %s-%s %s"
                  % (win["start"], win["end"], win["timezone"]),
                  "start from %s.local_hhmm, end from %s.latest_hhmm, both read "
                  "from %s" % (win["start_from"], win["end_from"], win["source"]))
    return win


# ---------------------------------------------------------------------------
# 6. The nightly lock -- the probe, the owner format, and the acquisition
# ---------------------------------------------------------------------------
#
# `publish` is the only caller of NightlyLock; every other stage only probes. The
# probe exists because --status must be able to say "the driver is running"
# before an operator plans anything, and because the silent-night diagnosis in
# CLAUDE.md's Rollback table is read off exactly this owner line.

def parse_owner(text):
    out = {}
    for token in (text or "").strip().split():
        if "=" in token:
            key, _sep, val = token.partition("=")
            out[key] = val
    return out


def owner_text(pid=None, started=None):
    """The driver's exact field format, with repo=arknights.

    Matched rather than invented: this string is what `sced-run-now.sh --status`
    prints, and it is the whole diagnosis of a silent night.
    """
    return "pid=%d repo=arknights started=%d host=%s\n" % (
        pid or os.getpid(), int(started or time.time()), socket.gethostname())


def read_owner(path=None):
    """Returns (state, fields). state is absent_dir, absent_owner, or present."""
    path = path or LOCK_OWNER
    if not os.path.isdir(os.path.dirname(path)):
        return "absent_dir", {}
    try:
        return "present", parse_owner(kc.read_text(path))
    except IOError as exc:
        if exc.errno == errno.ENOENT:
            return "absent_owner", {}
        raise


def probe_lock(path=None):
    """Read-only. Shown by --status so nobody plans a run that is about to refuse."""
    state, fields = read_owner(path)
    if state == "absent_dir":
        return {"held": False, "state": state, "owner": None}
    if state == "absent_owner":
        # Not a corrupted lock: the driver's stale-takeover rm -f's owner at
        # daily-sync-local.sh:1311 and does not write its own until :1319.
        return {"held": True, "state": state, "owner": None,
                "note": "lock directory exists with no owner -- a takeover is in "
                        "progress. Touch nothing."}
    return {"held": True, "state": state, "owner": fields}


class NightlyLock(object):
    """Take the lock the driver takes, and make the OWNER FILE the atom.

    `mkdir` succeeding is not the acquisition; the acquisition completes when
    `owner` exists. In between, a driver arriving reads a directory with no owner
    in it and takes the unconditional stale branch at daily-sync-local.sh:
    1306-1307 -- so O_EXCL on the owner file is what protects the DRIVER from us,
    reducing acquisition to a single test-and-set on the file the other side
    actually reads. Matched from koreanize's kz_config.NightlyLock rather than
    invented: three writers sharing one lock should share one protocol.

    No heartbeat, unlike koreanize's. The whole write is two files and finishes
    in well under a second, so there is nothing to keep alive inside the driver's
    one-hour staleness bound; koreanize needs one because its stages write
    hundreds of images.
    """

    def __init__(self, lock_dir=None):
        self.lock_dir = lock_dir or LOCK_DIR
        self.owner_path = os.path.join(self.lock_dir, "owner")
        self.pid = os.getpid()
        self.started = None
        self.held = False
        self.released_ok = False
        self.release_state = None
        self.release_message = None

    def acquire(self):
        """Three refusals, all exit 80, and nothing has been written at any of them."""
        state, fields = read_owner(self.owner_path)
        if state == "present":
            kc.refuse(kc.EXIT_GUARD, "the nightly lock is held",
                      "owner: pid=%s repo=%s started=%s host=%s"
                      % (fields.get("pid"), fields.get("repo"),
                         fields.get("started"), fields.get("host")))
        try:
            os.makedirs(self.lock_dir)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
        self.started = int(time.time())
        try:
            # os.open, not open(): O_EXCL is the test-and-set, and akn_common's
            # open-mode scan permits it here precisely because an exclusive
            # create cannot truncate an existing file.
            handle = os.open(self.owner_path,
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                _state, other = read_owner(self.owner_path)
                kc.refuse(kc.EXIT_GUARD, "lock acquire lost (EEXIST)",
                          "another process completed its acquisition inside the "
                          "mkdir/write gap: pid=%s repo=%s"
                          % (other.get("pid"), other.get("repo")))
            raise
        with os.fdopen(handle, "w") as fh:
            fh.write(owner_text(self.pid, self.started))

        _state, mine = read_owner(self.owner_path)
        if mine.get("pid") != str(self.pid):
            kc.refuse(kc.EXIT_GUARD, "lock was taken over immediately after "
                      "acquisition",
                      "owner is now pid=%s repo=%s" % (mine.get("pid"),
                                                       mine.get("repo")))
        self.held = True
        return self

    def release(self):
        """Compare-and-delete over four owner states. Only the last one unlinks."""
        state, fields = read_owner(self.owner_path)
        self.release_state = state
        if state == "absent_dir":
            # Reaching this means this process ran to completion believing it
            # held a lock something else had already recycled -- a fact about the
            # interlock, not about the write, and swallowing it would be
            # indistinguishable from a clean release.
            self.release_message = ("lock directory absent at release; another "
                                    "process completed a takeover and released")
        elif state == "absent_owner":
            # NOT an orphan: the driver's stale-takeover rm -f's the owner at
            # :1311 and does not write its own until :1319. Removing it here
            # would destroy a lock the driver is MID-ACQUISITION of.
            self.release_message = "lock owner file absent; assuming takeover"
        elif fields.get("pid") != str(self.pid):
            self.release_message = ("lock was taken over by pid=%s repo=%s"
                                    % (fields.get("pid"), fields.get("repo")))
        else:
            os.unlink(self.owner_path)
            try:
                os.rmdir(self.lock_dir)
            except OSError:
                # The owner file is the atom, so a directory that will not go --
                # already removed, or holding somebody's scratch -- is not a
                # failed release and must not be reported as one.
                pass
            self.held = False
            self.released_ok = True
            self.release_message = "released"
        return self.released_ok, self.release_state, self.release_message

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        # Reported, never raised: by the time release runs the two files are on
        # disk, and an exception here would report exit 80 -- "nothing was
        # written" -- over a run that wrote everything. The caller folds
        # release_message into check P6 instead.
        ok, _state, message = self.release()
        if not ok:
            sys.stderr.write("arknights: %s\n" % message)
        return False


# ---------------------------------------------------------------------------
# 7. The DAG -- what `plan` and `--status` are computed from
# ---------------------------------------------------------------------------

def _read_json_quietly(path):
    try:
        return json.loads(kc.read_text(path))
    except (OSError, IOError, ValueError):
        return None


def stage_state(run_dir, stage, workspace=None):
    """What --status prints for one stage. Never raises: a missing or corrupt
    report is a STATE, and a status command that refused on one would be useless
    at exactly the moment it is needed."""
    workspace = workspace or kc.WORKSPACE_ROOT
    path = kc.report_path(run_dir, stage, "build")
    state = {"stage": stage, "report": path, "present": os.path.exists(path),
             "verdict": None, "exit_code": None, "consumable": False,
             "blocked_by": None, "stale": False, "stale_paths": []}
    report = _read_json_quietly(path) if state["present"] else None
    if report is None:
        state["blocked_by"] = ("no report" if not state["present"]
                               else "report is unreadable")
        return state
    state["verdict"] = report.get("verdict")
    state["exit_code"] = report.get("exit_code")
    state["consumable"] = bool(report.get("consumable"))
    state["blocked_by"] = report.get("consumable_blocked_by")

    # STALE is the whole resumability mechanism: a stage is stale when any
    # binding{} sha256 no longer matches disk, which implements "a rebuild
    # invalidates everything downstream" without any stage having to remember it.
    for name, recorded in sorted((report.get("binding") or {}).items()):
        candidate = name if os.path.isabs(name) else os.path.join(run_dir, name)
        if not os.path.exists(candidate):
            candidate = os.path.join(workspace, name)
        if not isinstance(recorded, str) or len(recorded) != 64:
            # An unusable RECORD, not an unusable input: nothing can be compared,
            # so there is nothing to call stale.
            continue
        if not os.path.exists(candidate):
            # GONE is stale, not fresh. This used to `continue`, which made the
            # rule "changed" rather than "no longer matches disk" and let a
            # deleted input read as up to date. No harmful path was constructible
            # -- every bound artifact is either a stage report covered above or a
            # file whose consumer refuses on its own -- but the exemption was
            # silent, and silence is what makes the next one a defect.
            state["stale"] = True
            state["stale_paths"].append(name)
            continue
        if kc.sha256_file(candidate) != recorded:
            state["stale"] = True
            state["stale_paths"].append(name)
    return state


def plan(run_dir, workspace=None):
    """The DAG with each stage's state, plus which stage runs next."""
    states = collections.OrderedDict()
    for stage in STAGES:
        states[stage] = stage_state(run_dir, stage, workspace)
    rows, nxt, blocked = [], None, []
    for stage in STAGES:
        state = dict(states[stage])
        preds = PREDECESSORS[stage]
        unmet = [p for p in preds
                 if not states[p]["consumable"] or states[p]["stale"]]
        state["predecessors"] = list(preds)
        state["unmet"] = unmet
        state["runnable"] = not unmet
        rows.append(state)
        if unmet:
            blocked.append(stage)
        elif nxt is None and (not state["consumable"] or state["stale"]):
            nxt = stage
    return {"stages": rows, "next": nxt, "blocked": blocked}


def assert_predecessors(run_dir, stage, workspace=None):
    """Exit 81 -- a STAGE's own precondition refusal, as distinct from the
    dispatcher's 88. The difference is the first move: 88 means no stage process
    started and the move is `plan`; 81 means the stage read its input and the
    move is the named file."""
    unmet = []
    for pred in PREDECESSORS.get(stage, ()):
        state = stage_state(run_dir, pred, workspace)
        if not state["consumable"]:
            unmet.append("%s (%s)" % (pred, state["blocked_by"] or "not consumable"))
        elif state["stale"]:
            unmet.append("%s (stale: %s)" % (pred, ", ".join(state["stale_paths"][:4])))
    if unmet:
        kc.refuse(kc.EXIT_PRECONDITION, "%s cannot run yet" % stage,
                  "; ".join(unmet))


# ---------------------------------------------------------------------------
# 8. --selftest
# ---------------------------------------------------------------------------

FAULTS = ("config", "guard", "source-containment", "run-id", "window", "dag",
          "owner")


def selftest(fault=None, verbose=True):
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    def fires(label, code, fn):
        try:
            fn()
        except kc.AknRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d (%s)"
                                % (label, code, exc.code, exc.message))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    cfg = load_config()

    if "config" in wanted:
        if compute_config_sha256(cfg) != compute_config_sha256(dict(cfg)):
            findings.append("config: the pin is not stable across a copy")
        by_folder = pack_by_folder(cfg)
        if len(by_folder) != len(cfg["packs"]):
            findings.append("config: pack folders collide after NFC folding")
        # The measured 2026-08-30 property: the disk is NFD, the config is NFC.
        for row in cfg["packs"]:
            if kc.nfc(row["folder"]) != row["folder"]:
                findings.append("config: pack folder %r is not NFC" % row["folder"])
        fires("config (cycle_code present)", kc.EXIT_DISPATCH_REFUSED,
              lambda: validate(_with_library_key(cfg, "cycle_code", "aknx")))
        fires("config (FFG author)", kc.EXIT_DISPATCH_REFUSED,
              lambda: validate(_with_library_key(cfg, "author",
                                                 "Fantasy Flight Games")))

    if "guard" in wanted:
        ok = os.path.join(kc.WORKSPACE_ROOT, cfg["run_root"], "20260830T000000Z",
                          "scan.json")
        if check_write_paths(cfg, [ok]):
            findings.append("guard: a legitimate run-dir write was refused")
        data_ok = os.path.join(kc.WORKSPACE_ROOT, cfg["guard"]["data_root"],
                               "arknights.idmap.json")
        if check_write_paths(cfg, [data_ok]):
            findings.append("guard: a legitimate data_root write was refused")
        for bad in ("docs/Arknights/TTS용 파일/x.json",
                    "SCED-downloads/library.json",
                    "SCED/objects/AllPlayerCards.15bb07/x.json",
                    "SCED-tools/scripts/koreanize/kz_common.py"):
            if not check_write_paths(cfg, [os.path.join(kc.WORKSPACE_ROOT, bad)]):
                findings.append("guard: %s was NOT refused" % bad)
        fires("guard (write under the source root)", kc.EXIT_GUARD,
              lambda: assert_write_paths(cfg, [os.path.join(
                  kc.WORKSPACE_ROOT, "docs/Arknights/anything.json")]))
        fires("guard (over max_files_written)", kc.EXIT_GUARD,
              lambda: assert_write_paths(cfg, [
                  os.path.join(kc.WORKSPACE_ROOT, cfg["run_root"], "r", "%d.json" % i)
                  for i in range(cfg["guard"]["max_files_written"] + 1)]))

    if "source-containment" in wanted:
        fires("source (--source outside the containment root)", kc.EXIT_GUARD,
              lambda: source_root(cfg, override=os.path.join(kc.WORKSPACE_ROOT,
                                                             "SCED-downloads")))
        fires("source (--source traversal)", kc.EXIT_GUARD,
              lambda: source_root(cfg, override=os.path.join(
                  kc.WORKSPACE_ROOT, "docs", "Arknights", "..", "..", "SCED")))

    if "run-id" in wanted:
        for bad in ("../../SCED/objects", "latest", "20260830T000000Z/../..",
                    "; rm -rf /"):
            fires("run-id %r" % bad, kc.EXIT_DISPATCH_REFUSED,
                  lambda b=bad: resolve_run_dir(cfg, run_id=b))
        if not RUN_ID_RE.match("20260830T041500Z"):
            findings.append("run-id: a legitimate stamp was rejected")

    if "window" in wanted:
        win = schedule_window()
        if win["start"] != "02:02" or win["end"] != "04:27":
            # Not a hardcode: the union of today's sync-schedule.json. A CHANGE
            # here is a real finding, because the same union bounds the driver.
            findings.append("window: %s-%s -- the union over sync-schedule.json "
                            "moved; confirm against sced-schedule.sh show"
                            % (win["start"], win["end"]))
        inside, _win = in_schedule_window(now_min=_hhmm_to_min("0230"))
        if not inside:
            findings.append("window: 02:30 should be inside")
        inside, _win = in_schedule_window(now_min=_hhmm_to_min("1200"))
        if inside:
            findings.append("window: 12:00 should be outside")
        fires("window (inside)", kc.EXIT_GUARD,
              lambda: assert_outside_window(now_min=_hhmm_to_min("0230")))

    if "dag" in wanted:
        if set(PREDECESSORS) != set(STAGES):
            findings.append("dag: the predecessor table is not total")
        if PREDECESSORS["scan"] != ():
            findings.append("dag: scan must have no predecessor")
        for i in range(1, len(STAGES)):
            if PREDECESSORS[STAGES[i]] != (STAGES[i - 1],):
                findings.append("dag: %s does not follow %s" % (STAGES[i],
                                                                STAGES[i - 1]))

    if "owner" in wanted:
        fields = parse_owner(owner_text(pid=4242, started=17))
        if fields.get("repo") != "arknights":
            findings.append("owner: repo= is not arknights")
        for key in ("pid", "repo", "started", "host"):
            if key not in fields:
                findings.append("owner: the driver's field %r is missing" % key)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


def _with_library_key(cfg, key, value):
    mutated = json.loads(json.dumps(cfg))
    mutated["library_entry"][key] = value
    return mutated


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_config.py",
        description="arknights config, write guard, nightly window and lock probe.")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    parser.add_argument("--validate", action="store_true",
                        help="validate the config and print its computed pin")
    parser.add_argument("--window", action="store_true")
    parser.add_argument("--lock-status", action="store_true")
    parser.add_argument("--plan", metavar="RUN_DIR")
    parser.add_argument("--plan-json", action="store_true")
    args = parser.parse_args(argv)

    # P0a and P0b, before anything reads or writes. Exit 2 for both: they are
    # facts about HOW the tool was invoked, not about its inputs.
    kc.check_invocation_guards()

    if args.plan:
        result = plan(args.plan)
        if args.plan_json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return kc.EXIT_OK
        print("%-9s %-14s %-11s %-6s %s"
              % ("stage", "report", "consumable", "stale", "binding"))
        for row in result["stages"]:
            note = "-"
            if row["unmet"]:
                note = "waiting on %s" % ", ".join(row["unmet"])
            elif row["stale"]:
                note = "stale: %s" % ", ".join(
                    os.path.basename(p) for p in row["stale_paths"][:3])
            elif row["blocked_by"]:
                note = row["blocked_by"]
            print("%-9s %-14s %-11s %-6s %s"
                  % (row["stage"],
                     os.path.basename(row["report"]) if row["present"] else "-",
                     "yes" if row["consumable"] else ("no" if row["present"] else "-"),
                     "YES" if row["stale"] else "no", note))
        print("next    : %s" % (result["next"] or "-- nothing runnable --"))
        return kc.EXIT_OK

    if args.window:
        inside, win = in_schedule_window()
        print("window  : %s-%s %s (union over %s)"
              % (win["start"], win["end"], win["timezone"],
                 os.path.basename(win["source"])))
        print("bounds  : start from %s.local_hhmm, end from %s.latest_hhmm"
              % (win["start_from"], win["end_from"]))
        print("now     : %s" % ("INSIDE -- publish refuses at exit 80" if inside
                                else "outside, ok"))
        return kc.EXIT_OK

    if args.lock_status:
        probe = probe_lock()
        print("lock    : %s" % ("held" if probe["held"] else "free"))
        print("state   : %s" % probe["state"])
        if probe.get("owner"):
            print("owner   : pid=%s repo=%s started=%s host=%s"
                  % (probe["owner"].get("pid"), probe["owner"].get("repo"),
                     probe["owner"].get("started"), probe["owner"].get("host")))
        if probe.get("note"):
            print("note    : %s" % probe["note"])
        return kc.EXIT_OK

    if args.validate:
        cfg = load_config()
        print("ok: %s" % cfg["_path"])
        print("config_sha256 : %s" % compute_config_sha256(cfg))
        print("file_sha256   : %s" % cfg["_sha256"])
        print("packs         : %d" % len(cfg["packs"]))
        print("exceptions    : %d" % len(cfg["exceptions"]))
        print("repairs       : %d rows, %d targets"
              % (len(cfg["repairs"]),
                 sum(len(r.get("targets") or []) for r in cfg["repairs"])))
        return kc.EXIT_OK

    if args.selftest is not None:
        fault = args.selftest or None
        print("akn_config --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_VERIFY
        print("  ok: config schema and library rules, the write guard in both "
              "directions, source containment, run-id traversal, the union "
              "window, the DAG's totality, the owner format")
        return kc.EXIT_OK

    parser.print_help()
    return kc.EXIT_USAGE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.AknRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
