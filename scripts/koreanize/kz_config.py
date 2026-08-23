#!/usr/bin/python3
"""koreanize: scenario.json, the guards it carries, and the nightly interlock.

STDLIB TIER (design §5.9): stdlib only, 3.9-compatible, no PIL and no numpy.
That constraint is what forces the font-identity check below to parse the sfnt
`name` table with `struct` rather than with fontTools -- kz_common's AST
allowlist is the mechanism that would otherwise have to be relaxed for it, and
it is not relaxed. PIL's ImageFont.getname() is not a substitute: it returns
(family, style) and never the PostScript name, so two faces of one family share
its answer and differ exactly where identity matters.

This module owns, and is the only owner of:
  - STAGES and AI_STAGE_MAP, with their import-time assertions (§3.2)
  - the scenario.json schema, its four stage-partition cross-checks and the
    config_sha256 pin
  - the batch-budget closure and the universe/ceiling refusal
  - the guard.write_roots / guard.forbidden evaluator, and guard.data_root --
    the ONE exemption, whose mirror writer is the only code in the tool that
    opens a path under SCED-tools/ for writing (§3.1)
  - font identity, resolved through ~/.config/koreanize/env
  - the nightly lock ACQUIRE and the union schedule-window refusal (§5.10)

It imports kz_common and kz_common does not import it: an edge back the other
way would be a module-scope cycle resolved by whichever import ran first, and it
would drag whatever kz_common imported into the stdlib tier's import graph.
"""

import argparse
import errno
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402

SCHEMA_VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# 1. The stage universe and the S-id map (§3.2)
# ---------------------------------------------------------------------------
#
# AI_STAGE_MAP lives HERE and not in kz_decide.py. kz_config needs the map to
# prove ai.required_stages total; kz_decide needs kz_config.STAGES to prove every
# mapped name is a real stage. A cycle between two module BODIES resolves by
# whichever is imported first and breaks silently the day that order changes --
# but the sharper cost is the interpreter tier: kz_decide.py is art-tier and is
# policed by neither of §5.9's two checks, so a `match` statement written there
# would break kz_langpack.py under 3.9.6 and nothing would see it.
# The dependency is one-way: kz_decide imports kz_config, never the reverse.

STAGES = (
    # neutral (4)
    "init", "source", "check", "erase",
    # required-AI (7)
    "terms", "translate", "mask", "typeset", "ocr", "triage", "audit",
    # forbidden-AI (11)
    "scaffold", "reuse", "objtext", "register", "repoint", "revert", "verify",
    "slice", "composite", "recompose", "upload",
)

AI_STAGE_MAP = {"S1": "terms", "S2": "translate", "S3": "mask", "S4": "typeset",
                "S5": "ocr", "S6": "triage", "S7": "audit"}

assert set(AI_STAGE_MAP) == {"S1", "S2", "S3", "S4", "S5", "S6", "S7"}   # every S-id owned
assert len(set(AI_STAGE_MAP.values())) == 7                              # no stage owns two
assert set(AI_STAGE_MAP.values()) <= set(STAGES)                         # every name is real
assert len(set(STAGES)) == len(STAGES) == 22                             # 22 = 7 + 11 + 4

# `revert` is the only stage exempt from §1.2's universal predecessor rule, and
# the set is stated here so the dispatcher can assert it rather than special-case
# it inline. Its predecessor is by construction the stage that just failed, and a
# failed stage is never consumable -- so the universal form makes revert refuse
# at exit 72 in exactly the situation §8.4 invokes it for. The exemption is one
# named row and deliberately NOT a widening of compute_consumable: a second
# "consumable-for-revert-only" predicate would put a branch nobody else reads
# into the conjunct every stage evaluates (§3.6).
PREDECESSOR_EXEMPT = frozenset({"revert"})
assert PREDECESSOR_EXEMPT <= set(STAGES)

# The subdirectories guard.data_root may contain. Seven, because the exemption is
# named for the tier and not for one of its members: init writes scenarios/,
# terms writes terms/, translate writes text/, mask writes layouts/, typeset
# writes icons/, kz_langpack produces locks/, and §6 step 8 writes golden/.
DATA_SUBDIRS = ("scenarios", "terms", "text", "layouts", "icons", "locks", "golden")

ENV_FILE = os.path.expanduser("~/.config/koreanize/env")
SCHEDULE_JSON = os.path.join(kc.WORKSPACE_ROOT, "SCED-tools", "config", "sync-schedule.json")
LOCK_DIR = os.path.join(kc.WORKSPACE_ROOT, ".local-sync", "run", "daily-sync.lock")
LOCK_OWNER = os.path.join(LOCK_DIR, "owner")

# Half of the driver's MAX_RUN_SECONDS (daily-sync-local.sh:229, default 3600).
# Uncapped, a heartbeat is a defect rather than a fix: refreshing `started`
# neutralises the driver's second staleness test while a live pid already
# neutralises its first, so a koreanize process that is WEDGED BUT ALIVE would
# hold the workspace-wide lock indefinitely and every subsequent night would
# exit 3 with nothing able to reclaim it.
LOCK_MAX_S_DEFAULT = 1800
LOCK_HEARTBEAT_S = 60

WINDOW_PRE_MIN = 15   # before the earliest local start
WINDOW_POST_MIN = 60  # after the latest CI start


# ---------------------------------------------------------------------------
# 2. ~/.config/koreanize/env -- machine-local locations, never in git
# ---------------------------------------------------------------------------
#
# Every path in scenario.json is workspace-relative. A pinned, git-tracked config
# that embedded ~/Library/Fonts/... could not be used on another machine or after
# the volume moved, and because it is hash-pinned the fix would be a regeneration
# that invalidates the init gate's bound_sha256. This file is NOT covered by
# config_sha256, which is the point of it.

def read_env_file(path=None):
    """Parse ~/.config/koreanize/env. Refuses a file that is not mode 0600.

    The shape of ~/.config/sced-r2/env, and the refusal is the same one the
    nightly driver makes: a credentials file the group or the world can read is
    a credentials file, not a config.
    """
    path = path or ENV_FILE
    if not os.path.exists(path):
        return {}
    mode = os.stat(path).st_mode & 0o777
    if mode != 0o600:
        kc.refuse(kc.EXIT_GUARD,
                  "%s is mode %04o, expected 0600" % (path, mode),
                  "chmod 600 %s" % path)
    out = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _sep, val = line.partition("=")
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            out[key.strip()] = val
    return out


# ---------------------------------------------------------------------------
# 3. Font identity -- the sfnt `name` table, parsed with struct
# ---------------------------------------------------------------------------

def read_postscript_name(path):
    """Name ID 6 out of the sfnt `name` table.

    Roughly 40 lines, no dependency, and it keeps the check where the guard
    already is. Preference order is platform 3 / encoding 1 (UTF-16BE) then
    platform 1 / encoding 0 (Mac Roman) -- the two encodings this corpus's fonts
    actually use.

    A file whose first tag is `ttcf` is a font COLLECTION and is refused rather
    than guessed at: which face in the collection was meant is exactly the
    question identity is being asserted to answer.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 12:
        kc.refuse(kc.EXIT_PRECONDITION, "%s is too short to be a font" % path)
    tag = data[0:4]
    if tag == b"ttcf":
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s is a font collection (ttcf)" % path,
                  "a collection holds several faces; name the individual face "
                  "instead of guessing which one was meant")
    num_tables = struct.unpack(">H", data[4:6])[0]
    name_off = name_len = None
    for i in range(num_tables):
        rec = 12 + i * 16
        if rec + 16 > len(data):
            break
        entry_tag = data[rec:rec + 4]
        if entry_tag == b"name":
            name_off, name_len = struct.unpack(">II", data[rec + 8:rec + 16])
            break
    if name_off is None:
        kc.refuse(kc.EXIT_PRECONDITION, "%s has no `name` table" % path)
    if name_off + 6 > len(data):
        kc.refuse(kc.EXIT_PRECONDITION, "%s: `name` table offset is past EOF" % path)

    fmt, count, string_off = struct.unpack(">HHH", data[name_off:name_off + 6])
    storage = name_off + string_off
    best = None
    for i in range(count):
        rec = name_off + 6 + i * 12
        if rec + 12 > len(data):
            break
        plat, enc, _lang, name_id, length, offset = struct.unpack(
            ">HHHHHH", data[rec:rec + 12])
        if name_id != 6:
            continue
        raw = data[storage + offset:storage + offset + length]
        if plat == 3 and enc in (0, 1):
            try:
                return raw.decode("utf-16-be").strip("\x00").strip()
            except UnicodeDecodeError:
                continue
        if plat == 1 and enc == 0 and best is None:
            try:
                best = raw.decode("mac-roman").strip("\x00").strip()
            except UnicodeDecodeError:
                best = None
    if best is not None:
        return best
    kc.refuse(kc.EXIT_PRECONDITION,
              "%s: `name` table carries no readable name ID 6" % path,
              "format %d, %d records" % (fmt, count))


def resolve_font(role, spec, env=None, search_roots=(), workspace=None):
    """Resolve one fonts.<role> to an absolute path and assert BOTH its identity
    and its bytes.

    Identity, never a path: the env var comes first because a machine-local font
    is machine-local, and search_roots second so a checkout with images-ko/ works
    with no env file at all. Either way the PostScript name AND the sha256 must
    match, and a mismatch is exit 13 naming the ROLE -- the resolved absolute path
    is recorded in the run's report, never in scenario.json.
    """
    env = env or {}
    workspace = workspace or kc.WORKSPACE_ROOT
    candidates = []
    env_var = spec.get("env_var")
    if env_var:
        val = env.get(env_var) or os.environ.get(env_var)
        if val:
            candidates.append(os.path.expanduser(val))
    for root in search_roots:
        root_abs = root if os.path.isabs(root) else os.path.join(workspace, root)
        if not os.path.isdir(root_abs):
            continue
        for dirpath, _dirnames, filenames in os.walk(root_abs):
            for fname in sorted(filenames):
                if os.path.splitext(fname)[1].lower() in (".ttf", ".otf", ".ttc"):
                    candidates.append(os.path.join(dirpath, fname))

    want_name = spec.get("postscript_name")
    want_sha = spec.get("sha256")
    tried = []
    for cand in candidates:
        if not os.path.exists(cand):
            tried.append("%s (absent)" % cand)
            continue
        got_sha = kc.sha256_file(cand)
        if want_sha and got_sha != want_sha:
            tried.append("%s (sha256 %s)" % (cand, got_sha[:12]))
            continue
        got_name = read_postscript_name(cand)
        if want_name and got_name != want_name:
            tried.append("%s (postscript_name %r)" % (cand, got_name))
            continue
        return {"role": role, "path": os.path.abspath(cand),
                "postscript_name": got_name, "sha256": got_sha,
                "redistributable": bool(spec.get("redistributable"))}
    kc.refuse(kc.EXIT_PRECONDITION,
              "font role %r did not resolve" % role,
              "want postscript_name=%r sha256=%s; tried: %s"
              % (want_name, (want_sha or "")[:12], ", ".join(tried) or "nothing"))


# ---------------------------------------------------------------------------
# 4. scenario.json -- schema, the config_sha256 pin, and the cross-checks
# ---------------------------------------------------------------------------

_REQUIRED_TOP = ("schema_version", "slug", "scenario_name", "source_dir", "pack",
                 "arkham_prefixes", "guard", "run_dir", "ai", "gates", "fonts")
_REQUIRED_GUARD = ("write_roots", "forbidden", "data_root", "max_files_written")
_REQUIRED_AI = ("required_stages", "forbidden_stages", "neutral_stages", "batch",
                "call_budget_s", "stage_wall_clock_s",
                "max_budget_usd_per_call", "max_budget_usd_per_stage")


def compute_config_sha256(cfg):
    """sha256 of the document with config_sha256 set to "".

    Serialised with sorted keys and a fixed separator so the digest is a property
    of the CONTENT rather than of whichever writer last touched the file.
    """
    clone = dict(cfg)
    clone["config_sha256"] = ""
    blob = json.dumps(clone, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return kc.sha256_bytes(blob)


def _check_stage_partition(cfg):
    """The four cross-checks of §3.2. Every one refuses at exit 4."""
    ai = cfg["ai"]
    required = list(ai["required_stages"])
    forbidden = list(ai["forbidden_stages"])
    neutral = list(ai["neutral_stages"])
    findings = []

    # 1. the required list is exactly what AI_STAGE_MAP owns
    if sorted(required) != sorted(AI_STAGE_MAP.values()):
        findings.append("ai.required_stages %s != sorted(AI_STAGE_MAP.values()) %s"
                        % (sorted(required), sorted(AI_STAGE_MAP.values())))
    # 2. required and forbidden are disjoint
    both = set(required) & set(forbidden)
    if both:
        findings.append("stages in BOTH required and forbidden: %s" % sorted(both))
    # 3. every name in any of the three lists is a declared stage
    unknown = (set(required) | set(forbidden) | set(neutral)) - set(STAGES)
    if unknown:
        findings.append("not declared stages: %s" % sorted(unknown))
    # 4. the three lists PARTITION STAGES -- 22 = 7 + 11 + 4. This is the
    #    assertion the earlier "machine-checked in both directions" claim was
    #    missing: it held only for the required half, so a stage added later could
    #    silently land in no class at all and the §4.1 AI column would not be total.
    union = set(required) | set(forbidden) | set(neutral)
    if union != set(STAGES):
        findings.append("the three lists do not partition STAGES; missing %s, extra %s"
                        % (sorted(set(STAGES) - union), sorted(union - set(STAGES))))
    if len(required) + len(forbidden) + len(neutral) != len(STAGES):
        findings.append("a stage appears in two lists: %d + %d + %d != %d"
                        % (len(required), len(forbidden), len(neutral), len(STAGES)))
    return findings


def _check_batch_budgets(cfg):
    """Per S-id closure, and the universe/ceiling refusal with its null carve-out.

    Both inequalities bind max_calls <= 6, which is why every S-id declares at
    most 6 and why max_budget_usd_per_stage is 60 and not 40. The previous table
    declared max_calls 12 for S2 and S5 against a $40 stage budget and a 5,400 s
    stage clock -- budgets that stopped those stages after roughly four batches
    and half their declared wall clock, with the ceiling they advertised
    unreachable.
    """
    ai = cfg["ai"]
    findings = []
    call_budget = ai["call_budget_s"]
    stage_clock = ai["stage_wall_clock_s"]
    per_call_usd = ai["max_budget_usd_per_call"]
    per_stage_usd = ai["max_budget_usd_per_stage"]
    batch = ai.get("batch") or {}

    missing = set(AI_STAGE_MAP) - set(batch)
    if missing:
        findings.append("ai.batch has no entry for %s" % sorted(missing))
    extra = set(batch) - set(AI_STAGE_MAP)
    if extra:
        findings.append("ai.batch has entries for unknown S-ids %s" % sorted(extra))

    for sid in sorted(set(batch) & set(AI_STAGE_MAP)):
        entry = batch[sid]
        calls = entry.get("max_calls")
        units = entry.get("max_units_per_call")
        if not isinstance(calls, int) or calls < 1:
            findings.append("%s.max_calls is not a positive integer" % sid)
            continue
        if not isinstance(units, int) or units < 1:
            findings.append("%s.max_units_per_call is not a positive integer" % sid)
            continue
        if calls * call_budget > stage_clock:
            findings.append("%s: max_calls * call_budget_s = %d > stage_wall_clock_s %d"
                            % (sid, calls * call_budget, stage_clock))
        if calls * per_call_usd > per_stage_usd:
            findings.append("%s: max_calls * max_budget_usd_per_call = %s > "
                            "max_budget_usd_per_stage %s"
                            % (sid, calls * per_call_usd, per_stage_usd))
        universe = entry.get("universe_size", None)
        if universe is None:
            # "not knowable until invocation" -- S6 and S7 are gate-invoked, so the
            # ceiling is enforced by kz_ask.py at exit 13 instead. The carve-out is
            # explicit so a MISSING universe_size cannot pass as a declared null.
            if "universe_size" not in entry:
                findings.append("%s: universe_size absent; declare null to mean "
                                "'not knowable until invocation'" % sid)
            continue
        ceiling = units * calls
        if universe > ceiling:
            findings.append("%s: universe_size %d > ceiling %d (= %d * %d)"
                            % (sid, universe, ceiling, units, calls))
    return findings


def _check_gates(cfg):
    findings = []
    for stage, status in sorted((cfg.get("gates") or {}).items()):
        if stage not in STAGES:
            findings.append("gates names %r, which is not a stage" % stage)
        if status not in ("required", "optional"):
            findings.append("gates.%s is %r, expected 'required' or 'optional'"
                            % (stage, status))
    return findings


def check_window_margin(layout, path="<layout>"):
    """1 <= window_margin_px <= calibrated_from <= 64, per group (§5.5).

    This is what turns the negative half of the mask calibration from a recorded
    number into a checked one. `calibrated_from` is the tightest post-suppression
    trailing clearance on a defect-free face in that group, or the literal
    "COL_GAP" where the group has no defect-free face -- which today is Act/front,
    both of whose faces are the known defects.
    """
    findings = []
    for group, spec in sorted((layout.get("groups") or {}).items()):
        if "window_margin_px" not in spec:
            continue
        margin = spec.get("window_margin_px")
        calibrated = spec.get("calibrated_from")
        if not isinstance(margin, int):
            findings.append("%s: %s.window_margin_px is not an integer" % (path, group))
            continue
        if calibrated == "COL_GAP":
            calibrated = layout.get("col_gap", 34)
        if not isinstance(calibrated, int):
            findings.append("%s: %s.calibrated_from is %r, expected an integer or "
                            "\"COL_GAP\"" % (path, group, spec.get("calibrated_from")))
            continue
        if not (1 <= margin <= calibrated <= 64):
            findings.append("%s: %s violates 1 <= window_margin_px (%d) <= "
                            "calibrated_from (%d) <= 64" % (path, group, margin, calibrated))
    return findings


def validate(cfg, path="<scenario.json>", check_pin=True):
    """Every scenario.json property, refusing at exit 4 with all findings at once.

    All findings at once and not the first: an operator fixing a pinned config one
    refusal at a time re-runs the hash pin on every pass.
    """
    findings = []
    for key in _REQUIRED_TOP:
        if key not in cfg:
            findings.append("missing top-level key %r" % key)
    if findings:
        kc.refuse(kc.EXIT_GUARD, "%s is not a scenario.json" % path, "; ".join(findings))

    if cfg.get("schema_version") != SCHEMA_VERSION:
        findings.append("schema_version %r != %r" % (cfg.get("schema_version"), SCHEMA_VERSION))

    guard = cfg["guard"]
    for key in _REQUIRED_GUARD:
        if key not in guard:
            findings.append("guard is missing %r" % key)
    if "data_root" in guard:
        if not str(guard["data_root"]).endswith("/"):
            findings.append("guard.data_root must end in / so prefix tests are unambiguous")
    if isinstance(guard.get("max_files_written"), int):
        if guard["max_files_written"] < 1:
            findings.append("guard.max_files_written must be positive")
    else:
        findings.append("guard.max_files_written is not an integer")

    ai = cfg["ai"]
    for key in _REQUIRED_AI:
        if key not in ai:
            findings.append("ai is missing %r" % key)
    if not findings:
        findings += _check_stage_partition(cfg)
        findings += _check_batch_budgets(cfg)
    findings += _check_gates(cfg)

    if check_pin:
        pinned = cfg.get("config_sha256")
        actual = compute_config_sha256(cfg)
        if not pinned:
            findings.append("config_sha256 is empty; init writes it and every stage "
                            "recomputes it")
        elif pinned != actual:
            # The pin is what stops a hand-edit after init from silently
            # retargeting the tool.
            findings.append("config_sha256 mismatch: pinned %s, computed %s"
                            % (pinned[:16], actual[:16]))

    if findings:
        kc.refuse(kc.EXIT_GUARD, "%s failed validation" % path, "; ".join(findings))
    return cfg


def load_scenario(path, check_pin=True, bind=True):
    """Read, validate, and bind. Binding is what makes every declare_ai() call
    made at import time assert against this config (§3.2)."""
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "scenario.json not found", path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            cfg = json.load(fh)
        except ValueError as exc:
            kc.refuse(kc.EXIT_GUARD, "%s is not valid JSON" % path, str(exc))
    validate(cfg, path=path, check_pin=check_pin)
    if bind:
        kc.bind_scenario(cfg)
    return cfg


# ---------------------------------------------------------------------------
# 5. The path guard, and guard.data_root -- the one exemption (§3.1)
# ---------------------------------------------------------------------------

def _rel_to_workspace(path, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    real = os.path.realpath(str(path))
    root = os.path.realpath(workspace)
    if real == root:
        return ""
    if not real.startswith(root + os.sep):
        return None  # outside the workspace entirely
    return real[len(root) + 1:]


def check_write_paths(cfg, paths, workspace=None):
    """Evaluate guard.data_root FIRST, then write_roots, then forbidden.

    Order matters and is not cosmetic: guard.forbidden lists "SCED-tools/", so a
    data_root write evaluated against `forbidden` first would be refused by the
    rule its exemption exists to carve out of.

    Returns a list of findings; the caller refuses at exit 4 BEFORE any read.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    guard = cfg["guard"]
    data_root = guard["data_root"]
    write_roots = list(guard["write_roots"])
    forbidden = list(guard["forbidden"])
    findings = []

    # data_root's own three properties, asserted once per call.
    pkg_rel = _rel_to_workspace(kc.PACKAGE_DIR, workspace)
    if pkg_rel is None or not data_root.startswith(pkg_rel.rstrip("/") + "/"):
        findings.append("guard.data_root %r does not resolve inside the koreanize "
                        "package directory (%s)" % (data_root, pkg_rel))

    for path in paths:
        rel = _rel_to_workspace(path, workspace)
        if rel is None:
            findings.append("%s resolves outside the workspace" % path)
            continue
        rel_posix = rel.replace(os.sep, "/")

        if rel_posix.startswith(data_root):
            tail = rel_posix[len(data_root):]
            sub = tail.split("/", 1)[0]
            if sub not in DATA_SUBDIRS:
                findings.append("%s is under guard.data_root but its subdirectory %r "
                                "is not one of %s" % (rel_posix, sub, list(DATA_SUBDIRS)))
            continue

        # Every remaining write under SCED-tools/ is a data_root violation by
        # construction: data_root is asserted to be a strict prefix of every one.
        under_root = any(rel_posix.startswith(r) or rel_posix == r.rstrip("/")
                         for r in write_roots)
        if not under_root:
            findings.append("%s is outside guard.write_roots" % rel_posix)
            continue
        for bad in forbidden:
            if rel_posix.startswith(bad) or ("/" + rel_posix).find("/" + bad) >= 0:
                findings.append("%s matches guard.forbidden %r" % (rel_posix, bad))
                break
    return findings


def assert_write_paths(cfg, paths, workspace=None):
    findings = check_write_paths(cfg, paths, workspace=workspace)
    if findings:
        kc.refuse(kc.EXIT_GUARD, "planned write set violates the path guard",
                  "; ".join(findings))
    if len(list(paths)) > cfg["guard"]["max_files_written"]:
        # Exit 4, and "nothing was read" is still true, because the count is
        # derived from the PLAN and not from the writes.
        kc.refuse(kc.EXIT_GUARD,
                  "planned write set of %d exceeds guard.max_files_written %d"
                  % (len(list(paths)), cfg["guard"]["max_files_written"]),
                  "this is a hard cap with no acceptance path (TOLERANCES, hard_cap)")


def assert_dry_run_dest(cfg, stage, dest, workspace=None):
    """Every stage's --dry-run output goes to <run_dir>/dry-run/<stage>/<relpath>.

    A named path and not a convention, because kz_langpack.py's outputs are under
    guard.write_roots, so a SIBLING of one of them lands INSIDE the guarded tree --
    and the guard refuses writes outside the roots, not inside them, so exit 4
    could never fire on it. Rehearsal is also kz_langpack.py's default mode and it
    runs on the --live path too (the banner is built from a --json-only --dry-run
    invocation), so a rehearsal that wrote into SCED-downloads would be the
    most-executed write path in the tool.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    run_dir = cfg["run_dir"]
    root = os.path.realpath(os.path.join(workspace, run_dir, "dry-run", stage))
    real = os.path.realpath(str(dest))
    if real != root and not real.startswith(root + os.sep):
        kc.refuse(kc.EXIT_GUARD,
                  "dry-run destination is outside the scratch root",
                  "%s is not under %s" % (real, root))
    return real


def write_data(cfg, subdir, relpath, data, workspace=None):
    """The mirror writer -- the ONLY code in the tool that opens a path under
    SCED-tools/ for writing (§3.1).

    The producing stage hands over a (subdir, relpath, data) triple and this
    validates it against data_root's three properties and writes it. That is what
    keeps §4.1's "writes outside <run_dir>: no" column literally true for all five
    producing stages: none of them declares a filesystem write root outside
    <run_dir>, and none of them can reach one.

    data_root writes never require --live: §4.1 derives the banner from writes
    that leave <run_dir>, and a blast-radius prompt whose radius is a git-tracked
    JSON receipt trains the operator to type LIVE without reading it.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    if subdir not in DATA_SUBDIRS:
        kc.refuse(kc.EXIT_GUARD, "unknown data_root subdirectory %r" % subdir,
                  "one of %s" % list(DATA_SUBDIRS))
    if os.path.isabs(relpath) or ".." in relpath.split("/"):
        kc.refuse(kc.EXIT_GUARD, "data_root relpath %r must be relative and "
                  "must not traverse" % relpath)
    dest = os.path.join(workspace, cfg["guard"]["data_root"], subdir, relpath)
    findings = check_write_paths(cfg, [dest], workspace=workspace)
    if findings:
        kc.refuse(kc.EXIT_GUARD, "data_root write refused", "; ".join(findings))
    if isinstance(data, (bytes, str)):
        kc.atomic_write_text(dest, data.decode("utf-8") if isinstance(data, bytes) else data)
    else:
        kc.atomic_write_json(dest, data)
    return dest


# ---------------------------------------------------------------------------
# 6. The nightly schedule window -- the UNION over every repo (§5.10)
# ---------------------------------------------------------------------------

def _hhmm_to_min(hhmm):
    text = str(hhmm).zfill(4)
    return int(text[:2]) * 60 + int(text[2:])


def _min_to_hhmm(minutes):
    minutes %= 24 * 60
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def schedule_window(schedule_path=None):
    """[min(local_hhmm) - 15, max(latest_hhmm) + 60] over EVERY repos{} entry.

    The lock is workspace-wide (daily-sync-local.sh:1290-1291, "its purpose is to
    stop the 02:17 run from overlapping the 02:47 one"), so a window derived from
    one repo's bounds refuses at the wrong times. Never hardcoded: CLAUDE.md's own
    rule is that the schedule lives in one place and everything downstream renders
    from it.
    """
    path = schedule_path or SCHEDULE_JSON
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "sync-schedule.json not found", path)
    with open(path, "r", encoding="utf-8") as fh:
        sched = json.load(fh)
    repos = sched.get("repos") or {}
    if not repos:
        kc.refuse(kc.EXIT_PRECONDITION, "sync-schedule.json declares no repos{}", path)
    starts = []
    latests = []
    for name in sorted(repos):
        entry = repos[name]
        starts.append((_hhmm_to_min(entry["local_hhmm"]), name))
        latests.append((_hhmm_to_min(entry["latest_hhmm"]), name))
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

    A probe closes nothing: it is a TOCTOU race, and nothing stops a koreanize
    write that begins at 02:16 from still writing when the driver takes the lock
    at 02:17.
    """
    inside, win = in_schedule_window(now_min=now_min, schedule_path=schedule_path)
    if inside:
        kc.refuse(kc.EXIT_GUARD,
                  "inside the nightly's schedule window %s-%s %s"
                  % (win["start"], win["end"], win["timezone"]),
                  "start from %s.local_hhmm, end from %s.latest_hhmm, both read from %s"
                  % (win["start_from"], win["end_from"], win["source"]))
    return win


# ---------------------------------------------------------------------------
# 7. The nightly lock -- ACQUIRE, do not probe (§5.10)
# ---------------------------------------------------------------------------

def _owner_text(pid=None, started=None):
    return "pid=%d repo=koreanize started=%d host=%s\n" % (
        pid or os.getpid(), int(started or time.time()), socket.gethostname())


def parse_owner(text):
    out = {}
    for token in (text or "").strip().split():
        if "=" in token:
            key, _sep, val = token.partition("=")
            out[key] = val
    return out


def read_owner(path=None):
    """Returns (state, fields). state is one of: absent_dir, absent_owner, present."""
    path = path or LOCK_OWNER
    lock_dir = os.path.dirname(path)
    if not os.path.isdir(lock_dir):
        return "absent_dir", {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return "present", parse_owner(fh.read())
    except IOError as exc:
        if exc.errno == errno.ENOENT:
            return "absent_owner", {}
        raise


def probe_lock(path=None):
    """Read-only. Shown in the --live banner so an operator is never asked to type
    LIVE into a run that is about to refuse."""
    state, fields = read_owner(path)
    if state == "absent_dir":
        return {"held": False, "state": state, "owner": None}
    if state == "absent_owner":
        # Not a corrupted lock: the driver's stale-takeover does rm -f owner at
        # daily-sync-local.sh:1311 and does not write its own until :1319.
        return {"held": True, "state": state, "owner": None,
                "note": "lock directory exists with no owner -- a takeover is in progress"}
    return {"held": True, "state": state, "owner": fields}


class NightlyLock(object):
    """Take the lock the driver takes, take it AFTER the banner, and make the
    OWNER FILE the atom.

    mkdir succeeding is not the acquisition; the acquisition completes when
    `owner` exists. In between, a driver arriving reads a directory with no owner
    in it and takes the unconditional `else stale=true` at daily-sync-local.sh:
    1306-1307. R3-9 protected koreanize from the driver and left the driver
    unprotected from koreanize; O_EXCL on the owner file is what closes the other
    direction, so acquisition reduces to a single test-and-set on the file the
    other side actually reads.
    """

    def __init__(self, lock_dir=None, max_seconds=None, heartbeat_s=LOCK_HEARTBEAT_S):
        self.lock_dir = lock_dir or LOCK_DIR
        self.owner_path = os.path.join(self.lock_dir, "owner")
        env = read_env_file()
        raw = env.get("KOREANIZE_LOCK_MAX_S") or os.environ.get("KOREANIZE_LOCK_MAX_S")
        self.max_seconds = max_seconds if max_seconds is not None else _positive_int(
            raw, LOCK_MAX_S_DEFAULT, "KOREANIZE_LOCK_MAX_S")
        self.heartbeat_s = heartbeat_s
        self.pid = os.getpid()
        self.started = None
        self._stop = threading.Event()
        self._thread = None
        self.held = False

    def acquire(self):
        """Five outcomes, and four of them are refusals. Exit 4 on every refusal;
        nothing has been written at any of them."""
        state, fields = read_owner(self.owner_path)
        if state == "present":
            kc.refuse(kc.EXIT_GUARD,
                      "the nightly lock is held",
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
            fd = os.open(self.owner_path,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                # Someone else completed an acquisition inside the mkdir/write
                # gap. Nothing was written and nothing needs cleaning up.
                _st, other = read_owner(self.owner_path)
                kc.refuse(kc.EXIT_GUARD,
                          "lock acquire lost (EEXIST)",
                          "another process completed its acquisition first: "
                          "pid=%s repo=%s" % (other.get("pid"), other.get("repo")))
            raise
        with os.fdopen(fd, "w") as fh:
            fh.write(_owner_text(self.pid, self.started))

        # Compare AFTER acquire and before the first byte of work.
        _st, mine = read_owner(self.owner_path)
        if mine.get("pid") != str(self.pid):
            kc.refuse(kc.EXIT_GUARD,
                      "lock was taken over immediately after acquisition",
                      "owner is now pid=%s repo=%s" % (mine.get("pid"), mine.get("repo")))
        self.held = True
        self._start_heartbeat()
        return self

    def _start_heartbeat(self):
        if not self.heartbeat_s:
            return
        self._thread = threading.Thread(target=self._heartbeat, name="kz-lock-hb")
        self._thread.daemon = True
        self._thread.start()

    def _heartbeat(self):
        """Refresh `started` on a timer well inside MAX_RUN_SECONDS, so a
        legitimately long write can never age into staleness -- CAPPED, so a
        wedged-but-alive process cannot hold the workspace-wide lock forever, and
        OWNERSHIP-VERIFYING, so a heartbeat can never rewrite a lock that was
        taken over."""
        deadline = self.started + self.max_seconds
        while not self._stop.wait(self.heartbeat_s):
            if time.time() >= deadline:
                # Past the cap the refresh stops, the owner file keeps its last
                # `started`, and the lock becomes reclaimable through the driver's
                # own stale path on schedule.
                return
            state, fields = read_owner(self.owner_path)
            if state != "present" or fields.get("pid") != str(self.pid):
                return
            try:
                kc.atomic_write_text(self.owner_path, _owner_text(self.pid))
            except OSError:
                return

    def release(self):
        """Compare-and-delete, over FOUR owner states rather than two.

        Returns (ok, state, message). Only the first state touches the filesystem.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        state, fields = read_owner(self.owner_path)

        if state == "absent_dir":
            # Reaching this means this process ran to completion believing it held
            # a lock that something else had already recycled -- a fact about the
            # interlock, not about the write. A finally that swallowed the
            # FileNotFoundError would be indistinguishable from a clean release.
            return (False, state,
                    "lock directory absent at release; another process completed a "
                    "takeover and released")
        if state == "absent_owner":
            # NOT an orphan: the driver's stale-takeover rm -f's the owner at
            # :1311 and does not write its own until :1319. Treating "no owner" as
            # "not someone else's, therefore mine" would rmdir a lock the driver is
            # MID-ACQUISITION of -- the exact double-writer outcome the interlock
            # exists to prevent, reached through the branch written to prevent it.
            return (False, state, "lock owner file absent; assuming takeover")
        if fields.get("pid") != str(self.pid):
            # Deleting it would destroy the other side's owner file and leave two
            # writers believing they hold the lock.
            return (False, state,
                    "lock was taken over by pid=%s repo=%s"
                    % (fields.get("pid"), fields.get("repo")))

        os.unlink(self.owner_path)
        try:
            os.rmdir(self.lock_dir)
        except OSError:
            pass
        self.held = False
        return (True, state, "released")

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        ok, state, message = self.release()
        if not ok:
            sys.stderr.write("koreanize: %s\n" % message)
            if exc_type is None:
                raise kc.KzRefusal(kc.EXIT_GUARD, message, "lock state: %s" % state)
        return False


def _positive_int(raw, default, name):
    """Plain integer seconds only.

    Not pedantry, and the reason is CLAUDE.md's: an unvalidated bad value does not
    widen a bound, it COLLAPSES it. The nightly learned this when a bad
    SCED_SYNC_* value made `sleep` fail instantly and SIGTERMed the very calls the
    bound was meant to protect.
    """
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = int(str(raw).strip())
    except ValueError:
        val = 0
    if val <= 0:
        sys.stderr.write("config: %s is not a positive integer of seconds; using %d\n"
                         % (name, default))
        return default
    return val


# ---------------------------------------------------------------------------
# 8. --selftest
# ---------------------------------------------------------------------------

def selftest(verbose=True):
    findings = []

    # The three import-time assertions are already live by the time this runs;
    # restate the partition arithmetic so a bare --selftest names it explicitly.
    if len(STAGES) != 22:
        findings.append("STAGES is %d, expected 22" % len(STAGES))
    if set(AI_STAGE_MAP.values()) - set(STAGES):
        findings.append("AI_STAGE_MAP names a stage that does not exist")
    if not PREDECESSOR_EXEMPT <= set(STAGES):
        findings.append("PREDECESSOR_EXEMPT names a stage that does not exist")
    if PREDECESSOR_EXEMPT != frozenset({"revert"}):
        findings.append("PREDECESSOR_EXEMPT must be exactly {'revert'} (§1.2)")

    # The union window, computed from the live schedule rather than asserted at a
    # literal, so a schedule change is visible here rather than only at 02:17.
    try:
        win = schedule_window()
        if verbose:
            print("  window: %s-%s %s (start from %s, end from %s)"
                  % (win["start"], win["end"], win["timezone"],
                     win["start_from"], win["end_from"]))
        inside_at_0217, _w = in_schedule_window(now_min=2 * 60 + 17)
        inside_at_1200, _w = in_schedule_window(now_min=12 * 60)
        if not inside_at_0217:
            findings.append("02:17 must be inside the union window")
        if inside_at_1200:
            findings.append("12:00 must be outside the union window")
    except kc.KzRefusal as exc:
        findings.append("schedule window: %s" % exc.message)

    # Budget knob validation collapses rather than widens on a bad value.
    if _positive_int("60s", 30, "TEST") != 30:
        findings.append("_positive_int must reject '60s' and fall back to the default")
    if _positive_int("0", 30, "TEST") != 30:
        findings.append("_positive_int must reject '0' and fall back to the default")
    if _positive_int("45", 30, "TEST") != 45:
        findings.append("_positive_int must accept a plain integer")

    # window_margin_px bound, both directions.
    ok_layout = {"col_gap": 34, "groups": {"Act/front": {"window_margin_px": 34,
                                                         "calibrated_from": "COL_GAP"}}}
    bad_layout = {"col_gap": 34, "groups": {"Act/front": {"window_margin_px": 40,
                                                          "calibrated_from": 34}}}
    if check_window_margin(ok_layout):
        findings.append("check_window_margin rejected a legal margin")
    if not check_window_margin(bad_layout):
        findings.append("check_window_margin accepted margin > calibrated_from")

    # The lock's five outcomes are exercised by test_koreanize_config.py against a
    # temp directory; what is asserted here is that the owner format matches the
    # driver's exactly, because `repo=koreanize` is the operator-legible signature
    # sced-run-now.sh --status prints at :659 and is the whole diagnosis.
    fields = parse_owner(_owner_text())
    for key in ("pid", "repo", "started", "host"):
        if key not in fields:
            findings.append("owner line is missing %r" % key)
    if fields.get("repo") != "koreanize":
        findings.append("owner repo must be 'koreanize'")

    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_config.py",
        description="koreanize scenario.json, path guard, font identity and the "
                    "nightly interlock (stdlib tier).")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--validate", metavar="SCENARIO_JSON",
                        help="validate a scenario.json and print its computed pin")
    parser.add_argument("--no-pin", action="store_true",
                        help="with --validate: skip the config_sha256 check")
    parser.add_argument("--window", action="store_true",
                        help="print the union schedule window and whether now is inside it")
    parser.add_argument("--lock-status", action="store_true",
                        help="read-only probe of the nightly lock")
    args = parser.parse_args(argv)

    if args.window:
        inside, win = in_schedule_window()
        print("window : %s-%s %s" % (win["start"], win["end"], win["timezone"]))
        print("bounds : start from %s.local_hhmm, end from %s.latest_hhmm"
              % (win["start_from"], win["end_from"]))
        print("source : %s" % win["source"])
        print("now    : %s" % ("INSIDE -- writes refuse at exit 4" if inside
                               else "outside"))
        return kc.EXIT_OK

    if args.lock_status:
        probe = probe_lock()
        print("lock   : %s" % ("held" if probe["held"] else "free"))
        print("state  : %s" % probe["state"])
        if probe.get("owner"):
            print("owner  : pid=%s repo=%s started=%s host=%s"
                  % (probe["owner"].get("pid"), probe["owner"].get("repo"),
                     probe["owner"].get("started"), probe["owner"].get("host")))
        if probe.get("note"):
            print("note   : %s" % probe["note"])
        return kc.EXIT_OK

    if args.validate:
        cfg = load_scenario(args.validate, check_pin=not args.no_pin, bind=False)
        print("ok: %s" % args.validate)
        print("slug            : %s" % cfg["slug"])
        print("config_sha256   : %s" % compute_config_sha256(cfg))
        print("stage partition : %d required + %d forbidden + %d neutral = %d"
              % (len(cfg["ai"]["required_stages"]), len(cfg["ai"]["forbidden_stages"]),
                 len(cfg["ai"]["neutral_stages"]), len(STAGES)))
        return kc.EXIT_OK

    if args.selftest:
        print("kz_config --selftest")
        findings = selftest()
        if findings:
            print("\nFAIL (%d):" % len(findings))
            for line in findings:
                print("  - %s" % line)
            return kc.EXIT_ARTIFACT
        print("  ok: 22-stage partition, S-id map, union window, knob validation, "
              "margin bound, owner format")
        return kc.EXIT_OK

    parser.print_help()
    return kc.EXIT_USAGE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
