#!/usr/bin/python3
"""arknights `publish` -- the one stage that writes outside <run_dir> (design section 5.5).

REHEARSAL IS THE DEFAULT. `--live` is opt-in, prints a blast-radius banner and
requires the operator to type LIVE. The write set is a CLOSED PAIR -- the payload
and library.json -- and the live invocation asserts it path-for-path against a
plan a PREVIOUS process wrote, so "the executed write set equals the plan the
banner printed" is something two processes say about each other rather than
something one says about itself.

WHY THIS STAGE CANNOT USE akn_config.write_guarded. guard.write_roots is the
closed list that makes docs/Arknights/ structurally unwritable, and it names
neither SCED-downloads path -- deliberately, because every OTHER stage must be
refused there. So `publish` carries its own allowlist, and it is a two-element
LIST OF EXACT PATHS rather than a root prefix: no argument, no config edit and no
traversal can add a third file to it.

THE ORDER OF THE INTERLOCK IS LOAD-BEARING, and it is not the order the steps
read in:

    probe the lock -> check the window -> banner -> take the lock -> write

The probe and the window check come first so nobody is asked to type LIVE into a
run that is about to refuse. The lock comes LAST because the driver declares a
lock stale at one hour, and an operator who steps away with a typed-LIVE prompt
open would otherwise hand the workspace-wide lock to the nightly on a timer. The
window is re-checked after the lock because a probe closes nothing -- it is a
TOCTOU race either way, and re-checking narrows it to the width of one mkdir.

IT RUNS NO MUTATING GIT VERB. It PRINTS the commit command and the operator
commits: akn_common.GIT_READONLY is the complete set any module may pass to git,
and tests/test_arknights_gates.py greps the package for an invocation that does
not go through the wrapper.
"""

import argparse
import collections
import json
import os
import sys

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_DIR not in sys.path:
    sys.path.insert(0, PACKAGE_DIR)

import akn_common as kc  # noqa: E402
import akn_config as kz  # noqa: E402

STAGE = "publish"

DOWNLOADS_ROOT = "SCED-downloads"
PAYLOAD_DIR = "SCED-downloads/downloadable/playercards"
LIBRARY_REL = "SCED-downloads/library.json"
SORT_LIBRARY_REL = "SCED-downloads/misc/sort_library.py"

#: The silent-night corroboration. Written while the lock is HELD, not after it
#: is released: a driver colliding with this stage hits a bare `exit 3` before
#: its EXIT trap and its log tee, so it produces no state, no Discord message of
#: any grade and no log entry. `sced-run-now.sh --status` prints the owner line;
#: this file is what turns that into "which stage, which files, which base".
STAMP_REL = ".local-sync/run/arknights.stamp"

#: Simulating the clock is the only way to exercise the nightly-window refusal
#: without waiting for 02:02. The knob is an ARKNIGHTS_TEST_* name, so
#: kc.test_overrides_present() already refuses to mark any report it produces
#: consumable -- the override cannot leak into the DAG.
NOW_MIN_ENV = "ARKNIGHTS_TEST_NOW_MIN"


# ---------------------------------------------------------------------------
# 1. The closed target pair
# ---------------------------------------------------------------------------

def payload_rel(cfg):
    """akn_config.validate pins library_entry.filename to "arknights", so this
    path is a config CONSTANT rather than operator input -- there is no argv
    value anywhere on it and therefore nothing to traverse with."""
    return "%s/%s.json" % (PAYLOAD_DIR, cfg["library_entry"]["filename"])


def publish_targets(cfg):
    return (payload_rel(cfg), LIBRARY_REL)


def assert_publish_targets(cfg, rels):
    """Exit 80. A LIST of exact paths, not a root prefix -- see the module docstring."""
    allowed = set(publish_targets(cfg))
    outside = [rel for rel in rels if rel not in allowed]
    if outside:
        kc.refuse(kc.EXIT_GUARD,
                  "publish was asked to write %d path(s) outside its target pair"
                  % len(outside),
                  "outside: %s; the pair is %s"
                  % (sorted(outside), sorted(allowed)))
    return list(rels)


def publish_write(cfg, rel, text, workspace=None):
    """The one writer outside <run_dir>. Every byte this stage lands goes here."""
    workspace = workspace or kc.WORKSPACE_ROOT
    assert_publish_targets(cfg, [rel])
    kc.atomic_write_text(os.path.join(workspace, rel), text)
    return rel


def sha_of(path):
    return kc.sha256_file(path) if os.path.exists(path) else None


# ---------------------------------------------------------------------------
# 2. The plan
# ---------------------------------------------------------------------------

def plan_path(run_dir):
    return os.path.join(run_dir, "publish.plan.json")


def snapshot_root(run_dir):
    return os.path.join(run_dir, "publish", "pre")


def snapshot_root_checked(run_dir, exit_code=kc.EXIT_GUARD):
    """snapshot_root(), with every component of the prefix asserted to be a real
    directory rather than a symlink. Use this wherever the result is opened.

    Anchored at dirname(run_dir) -- run_root in production -- so the run
    directory itself, `publish` and `pre` are all checked. That is the whole set
    an attacker with write access inside the run tree can plant, and the run
    tree is the trust boundary every other check on this path already assumes.
    """
    return kc.assert_unaliased_prefix(
        os.path.dirname(os.path.normpath(str(run_dir))),
        snapshot_root(run_dir), exit_code)


def rehearsal_root(run_dir):
    return os.path.join(run_dir, "dry-run", "publish")


class PlanEntry(object):
    __slots__ = ("path", "action", "pre_sha256", "plan_sha256", "size", "text")

    def __init__(self, path, action, pre_sha256, text):
        self.path = path
        self.action = action
        self.pre_sha256 = pre_sha256
        self.text = text
        self.size = len(text.encode("utf-8"))
        self.plan_sha256 = kc.sha256_bytes(text.encode("utf-8"))

    def record(self, committed=False, post_sha256=None):
        return collections.OrderedDict([
            ("path", self.path),
            ("action", self.action),
            ("pre_sha256", self.pre_sha256),
            ("plan_sha256", self.plan_sha256),
            ("bytes", self.size),
            ("committed", committed),
            ("post_sha256", post_sha256),
        ])


def load_sorter(workspace=None):
    """IMPORTED, never reimplemented and never shelled out.

    misc/sort_library.py owns KEY_ORDER and the sort. Importing it is the only
    way to guarantee exactly one definition of the canonical order -- a second
    copy here would diverge the first time upstream reorders a key, and the
    divergence would show up as a diff nobody could explain.
    """
    return kc.load_module_by_path(
        "akn_sort_library",
        os.path.join(workspace or kc.WORKSPACE_ROOT, SORT_LIBRARY_REL))


def planned_library(sorter, library, entry):
    """The document library.json must hold after the append and the sort.

    Returns (document, replaced). `replaced` is true on a RE-PUBLISH, where the
    entry already exists and the count delta is 0 rather than +1 -- which is why
    the delta is derived here and asserted, never assumed to be one.
    """
    content = list(library.get("content") or [])
    out, replaced = [], False
    for item in content:
        if item.get("filename") == entry["filename"]:
            out.append(dict(entry))
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(dict(entry))
    ordered = [sorter.reorder_item_keys(item) for _index, item
               in sorted(list(enumerate(out)), key=sorter.get_sort_keys)]
    doc = dict(library)
    doc["content"] = ordered
    return doc, replaced


class PublishPlan(object):
    """Everything the banner prints and the live run asserts against."""

    def __init__(self, cfg, run_dir, workspace=None):
        self.cfg = cfg
        self.run_dir = run_dir
        self.run_id = os.path.basename(run_dir)
        self.workspace = workspace or kc.WORKSPACE_ROOT

        self.built = os.path.join(run_dir, "arknights.json")
        if not os.path.exists(self.built):
            kc.refuse(kc.EXIT_PRECONDITION, "the built bag is missing",
                      "%s -- run `arknights.sh assemble`" % self.built)
        payload_text = kc.read_text(self.built)

        self.library_path = os.path.join(self.workspace, LIBRARY_REL)
        if not os.path.exists(self.library_path):
            kc.refuse(kc.EXIT_PRECONDITION, "library.json is missing",
                      self.library_path)
        library = json.loads(kc.read_text(self.library_path))
        if not isinstance(library.get("content"), list):
            kc.refuse(kc.EXIT_PRECONDITION,
                      "library.json is not {\"content\": [...]}",
                      "%s: top-level keys %s" % (LIBRARY_REL, sorted(library)))

        self.sorter = load_sorter(self.workspace)
        self.entry = dict(cfg["library_entry"])
        doc, replaced = planned_library(self.sorter, library, self.entry)
        self.replaces_existing = replaced
        self.pre_entries = len(library["content"])
        self.post_entries = len(doc["content"])
        self.delta = self.post_entries - self.pre_entries

        payload = payload_rel(cfg)
        payload_abs = os.path.join(self.workspace, payload)
        self.entries = [
            PlanEntry(payload,
                      "modify" if os.path.exists(payload_abs) else "create",
                      sha_of(payload_abs), payload_text),
            PlanEntry(LIBRARY_REL, "modify", sha_of(self.library_path),
                      kc.json_bytes(doc).decode("utf-8")),
        ]
        assert_publish_targets(cfg, [e.path for e in self.entries])
        # Resolved HERE, at plan time, because write_stamp() records it from
        # inside the lock and it is invariant from this point on: a git subprocess
        # is the only process spawn the lock-held window had, and it need not be
        # there. See base_sha()'s own note on why a moved base is recorded rather
        # than refused.
        self.base_sha = base_sha(self.workspace)

    def by_path(self, rel):
        for entry in self.entries:
            if entry.path == rel:
                return entry
        return None

    def document(self):
        return collections.OrderedDict([
            ("schema", 1),
            ("stage", STAGE),
            ("run_id", self.run_id),
            ("generated_by", "akn_publish.py"),
            ("generated_at", kc.utc_now()),
            ("config_sha256", kz.compute_config_sha256(self.cfg)),
            ("built_bag", os.path.relpath(self.built, self.workspace)),
            ("entry", self.entry),
            ("library", collections.OrderedDict([
                ("pre_entries", self.pre_entries),
                ("post_entries", self.post_entries),
                ("delta", self.delta),
                ("replaces_existing", self.replaces_existing),
            ])),
            ("entries", [e.record() for e in self.entries]),
        ])


def assert_plan_matches(plan, persisted):
    """Exit 81. Same paths, same ORDER, same length, same planned bytes.

    This is what makes the two-process claim real. A plan that moved since the
    banner is not a stale convenience -- it means the operator approved a
    different write set than the one about to land.
    """
    want = persisted.get("entries") or []
    got = plan.document()["entries"]
    if len(want) != len(got):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the persisted plan has %d entries, this run planned %d"
                  % (len(want), len(got)),
                  "re-run the rehearsal; the plan is stale")
    for index, pair in enumerate(zip(want, got)):
        was, now = pair
        if was.get("path") != now["path"]:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the persisted plan diverges at entry %d" % index,
                      "%s != %s" % (was.get("path"), now["path"]))
        if was.get("plan_sha256") != now["plan_sha256"]:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the planned bytes for %s have moved since the banner"
                      % now["path"],
                      "%s != %s" % ((was.get("plan_sha256") or "")[:16],
                                    now["plan_sha256"][:16]))
    return True


# ---------------------------------------------------------------------------
# 3. The blast-radius banner and the typed-LIVE prompt
# ---------------------------------------------------------------------------

def fork_changed(workspace=None):
    """The overlap gate's own input, read live rather than quoted.

    Returns (total, library_hits, downloadable_hits) or None when upstream/main
    is not fetched. Derived because the banner's whole claim is "this path is not
    in the fork-changed set TODAY" -- a number pasted from the design would go on
    saying that after it stopped being true.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    downloads = os.path.join(workspace, DOWNLOADS_ROOT)
    proc = kc.git("diff", ["--no-renames", "--name-only", "upstream/main...korean"],
                  cwd=downloads)
    if proc.returncode != 0:
        return None
    paths = [line for line in proc.stdout.decode("utf-8", "replace").splitlines()
             if line]
    return (len(paths),
            sum(1 for p in paths if p == "library.json"),
            sum(1 for p in paths if p.startswith("downloadable/")))


OVERLAP_WARNING = """\
  OVERLAP GATE -- design section 8 R-A. THIS IS THE COST, NOT A CAVEAT.
  Adding this entry puts library.json into the SCED-downloads fork-changed set
  for the first time. The nightly's overlap gate is PATH-LEVEL, so any upstream
  touch of that path trips it whether or not the JSON edits conflict. Upstream
  touched it 11 times in the last 90 days -- roughly ONE STALL PER EIGHT DAYS --
  and stalls are STICKY: a skipped rebase leaves the merge base where it was, so
  the same overlap recurs nightly until a human resolves it. SCED_SYNC_AI_RESOLVE
  is empty, so there is no automated path. The resolution is always cheap -- take
  upstream's library.json, append our entry, re-run misc/sort_library.py -- but
  that reduces the cost per occurrence, not the frequency."""


def banner(plan, live=True):
    rule = "=" * 78
    lines = ["", rule,
             "  %s -- arknights %s -- run %s"
             % ("LIVE PUBLISH" if live else "REHEARSAL", STAGE, plan.run_id),
             rule]
    for entry in plan.entries:
        lines.append("  %-6s %s" % (entry.action.upper(), entry.path))
        lines.append("         %8.1f KB   now %s   then %s"
                     % (entry.size / 1024.0,
                        (entry.pre_sha256 or "<absent>")[:12],
                        entry.plan_sha256[:12]))
    lines.append("  library entries : %d -> %d (%+d)"
                 % (plan.pre_entries, plan.post_entries, plan.delta))
    lines.append("  entry to insert :")
    for line in json.dumps(plan.entry, ensure_ascii=False,
                           indent=2).splitlines():
        lines.append("      %s" % line)
    lines.append("  snapshots       : %s"
                 % os.path.relpath(snapshot_root(plan.run_dir), plan.workspace))
    lines.append("  plan            : %s"
                 % os.path.relpath(plan_path(plan.run_dir), plan.workspace))

    changed = fork_changed(plan.workspace)
    lines.append("-" * 78)
    if changed is None:
        lines.append("  fork-changed set: NOT MEASURABLE -- upstream/main is not "
                     "fetched in SCED-downloads")
    else:
        total, lib, dl = changed
        lines.append("  fork-changed set: %d path(s) vs upstream/main; "
                     "library.json %d, downloadable/ %d" % (total, lib, dl))
    lines.append(OVERLAP_WARNING)
    lines.append(rule)
    return "\n".join(lines)


def restore_banner(plan_doc, run_dir, workspace=None):
    """`--restore`'s own banner. The generic one describes files about to be
    created, which is the opposite of what a restore is about to do."""
    workspace = workspace or kc.WORKSPACE_ROOT
    rule = "=" * 78
    lines = ["", rule,
             "  LIVE RESTORE -- arknights publish --restore",
             rule,
             "  snapshots : %s" % os.path.relpath(snapshot_root(run_dir),
                                                  workspace)]
    for entry in plan_doc.get("entries") or []:
        verb = "DELETE " if entry.get("action") == "create" else "RESTORE"
        lines.append("  %s %s" % (verb, entry.get("path")))
    lines.append(rule)
    return "\n".join(lines)


def confirm_live(text, assume_yes=False, stream=None):
    """Returns True to proceed. The typed-LIVE prompt, matching sced-run-now.sh
    and koreanize so an operator reads one gesture for one fact."""
    stream = stream or sys.stderr
    stream.write(text + "\n")
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        kc.refuse(kc.EXIT_USAGE, "--live needs a tty to confirm, or --yes",
                  "the banner above is the blast radius; nothing was written")
    stream.write("Type LIVE to proceed: ")
    stream.flush()
    return sys.stdin.readline().strip() == "LIVE"


# ---------------------------------------------------------------------------
# 4. The interlock -- the window, the lock, and the stamp
# ---------------------------------------------------------------------------

def now_min_override():
    raw = os.environ.get(NOW_MIN_ENV)
    if raw is None:
        return None
    if not raw.isdigit():
        kc.refuse(kc.EXIT_USAGE, "%s=%r is not minutes-past-midnight"
                  % (NOW_MIN_ENV, raw))
    return int(raw) % (24 * 60)


def probe_lock_before_banner(workspace=None):
    """Design section 5.5 step 1. Refuses at 80: nothing was written.

    Split out from probe_before_banner because `--restore` needs this half and
    NOT the window half -- it only warns on the window, deliberately. Both the
    live path and the restore path take the lock, so both owe the operator the
    refusal before the prompt rather than after it; without this, `--restore`
    asked for LIVE mid-incident and then refused at 80 from acquire().

    It probes the same scoped path workspace_lock() will take, so the probe and
    the take cannot disagree about which lock they mean. The owner line it names
    is the operator-legible signature `sced-run-now.sh --status` prints and the
    whole diagnosis of a collision.
    """
    probe = kz.probe_lock(lock_owner_for(workspace))
    if probe["held"]:
        owner = probe.get("owner") or {}
        kc.refuse(kc.EXIT_GUARD, "the nightly lock is held",
                  probe.get("note") or
                  "owner: pid=%s repo=%s started=%s host=%s"
                  % (owner.get("pid"), owner.get("repo"), owner.get("started"),
                     owner.get("host")))


def probe_before_banner(now_min=None, workspace=None):
    """Design section 5.5 steps 1 and 3, taken in that order and BEFORE the prompt.

    Both refuse at 80 and both mean nothing was written.
    """
    probe_lock_before_banner(workspace)
    return kz.assert_outside_window(now_min=now_min)


def base_sha(workspace=None):
    """The `origin/korean` sha this plan was built against.

    The nightly force-pushes `korean` on every successful night, and the rebase
    happens in a detached scratch worktree -- so an uncommitted publish survives
    untouched and only the BASE moves. Recorded rather than made a refusal, for
    the same reason: nothing is lost, the plan is merely stale.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    proc = kc.git("rev-parse", ["origin/korean"],
                  cwd=os.path.join(workspace, DOWNLOADS_ROOT))
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip() or None


def stamp_path(workspace=None):
    return os.path.join(workspace or kc.WORKSPACE_ROOT, STAMP_REL)


def write_stamp(plan):
    path = stamp_path(plan.workspace)
    kc.atomic_write_json(path, collections.OrderedDict([
        ("stage", STAGE),
        ("run_id", plan.run_id),
        ("pid", os.getpid()),
        ("started_at", kc.utc_now()),
        ("write_paths", [e.path for e in plan.entries]),
        ("planned_files", len(plan.entries)),
        # plan.base_sha, not a call: this runs inside the lock.
        ("base_sha", plan.base_sha),
    ]))
    return path


def remove_stamp(workspace=None):
    """Compare-and-delete, like the lock: a stamp this process did not write is
    another process's diagnosis, not litter."""
    path = stamp_path(workspace)
    if not os.path.exists(path):
        return False
    try:
        record = json.loads(kc.read_text(path))
    except ValueError:
        # An unparseable stamp is not ours to delete, and this runs in a finally:
        # raising here would replace the outcome of the write with a complaint
        # about a diagnostic file.
        return False
    if record.get("pid") != os.getpid():
        return False
    os.unlink(path)
    return True


def lock_dir_for(workspace=None):
    """kz.LOCK_DIR's path under `workspace` -- identical to it for a real run."""
    return os.path.join(workspace or kc.WORKSPACE_ROOT,
                        os.path.relpath(kz.LOCK_DIR, kc.WORKSPACE_ROOT))


def lock_owner_for(workspace=None):
    """The OWNER FILE, which is what kz.probe_lock takes -- not the directory.
    read_owner() reads it and infers absent_dir from its dirname, so handing it
    the directory reports a takeover in progress on a workspace with no lock."""
    return os.path.join(lock_dir_for(workspace),
                        os.path.basename(kz.LOCK_OWNER))


def workspace_lock(workspace=None):
    """The lock under `workspace` -- byte-identical to kz.LOCK_DIR for a real run.

    Scoped rather than global because both `--live` and `--restore` take the lock
    and the round-trip gates drive them against a temp workspace: an unscoped
    lock would have them take the REAL one, which the driver reads. Every taker
    and every prober of this lock goes through this scoping.
    """
    return kz.NightlyLock(lock_dir=lock_dir_for(workspace))


def write_restore_stamp(run_dir, entries, workspace, base):
    """`--restore`'s stamp, in write_stamp()'s shape and at its path.

    The silent-night runbook row reads one file and must not have to know which
    of the two operations wrote it; `stage` is what tells them apart.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.atomic_write_json(stamp_path(workspace), collections.OrderedDict([
        ("stage", STAGE + " --restore"),
        ("run_id", os.path.basename(run_dir.rstrip(os.sep))),
        ("pid", os.getpid()),
        ("started_at", kc.utc_now()),
        ("write_paths", [record["path"] for record in entries]),
        ("planned_files", len(entries)),
        # Recorded, never resolved here: `base` is the caller's, computed
        # BEFORE the lock for the reason PublishPlan.base_sha gives. The
        # `base if base is not None else base_sha(workspace)` fallback that used
        # to sit here spawned git from INSIDE the lock the moment a caller
        # stopped passing one, so the parameter is required instead. None stays
        # a legitimate value -- it is base_sha()'s own answer for "no repo".
        ("base_sha", base),
    ]))
    return stamp_path(workspace)


# ---------------------------------------------------------------------------
# 5. The write, and the post-conditions it is asserted against
# ---------------------------------------------------------------------------

def take_snapshots(cfg, plan):
    """Both targets copied into <run_dir>/publish/pre/ BEFORE either is touched,
    together with the manifest `--restore` reads. Written first so a crash
    between the two writes still leaves a complete restore record."""
    root = snapshot_root_checked(plan.run_dir)
    manifest = collections.OrderedDict([
        ("schema", 1),
        ("stage", STAGE),
        ("run_id", plan.run_id),
        ("generated_at", kc.utc_now()),
        ("entries", []),
    ])
    for entry in plan.entries:
        record = entry.record()
        record["snapshot"] = None
        if entry.action == "modify":
            name = entry.path.replace("/", "__")
            kz.write_guarded(cfg, os.path.join(root, name),
                             kc.read_text(os.path.join(plan.workspace,
                                                       entry.path)),
                             workspace=plan.workspace)
            record["snapshot"] = name
        manifest["entries"].append(record)
    kz.write_guarded(cfg, os.path.join(root, "manifest.json"), manifest,
                     workspace=plan.workspace)
    return manifest


def commit_writes(cfg, plan):
    """Payload first, then library.json -- each written ONCE, atomically.

    The library entry was appended and sorted in memory at plan time, so what
    goes down is plan.by_path(LIBRARY_REL).text and nothing touches the file
    afterwards. This used to append unsorted through publish_write and then call
    the imported sort_json_file(), which does open(JSON_FILE, "w") + json.dump
    IN PLACE -- a truncate-then-rewrite of the one file the mod consumes and the
    overlap gate reads, outside publish_write and therefore outside the closed
    target pair, swallowing its own exceptions into a print. A crash inside that
    dump left library.json truncated.

    Nothing is lost by dropping it. planned_library() applies the sorter's OWN
    get_sort_keys/reorder_item_keys, so there is still exactly one definition of
    the canonical order; kc.json_bytes is byte-identical to the sorter's
    json.dump(indent=2, ensure_ascii=False) + "\\n" (asserted by the gates, which
    run the real file-level function against a temp workspace); and the P3-P5
    checks re-read what landed and compare it to the planned bytes, which is the
    same verification the post-sort re-read used to provide.
    """
    written = []
    payload = plan.by_path(payload_rel(cfg))
    publish_write(cfg, payload.path, payload.text, plan.workspace)
    written.append(payload.record(committed=True,
                                  post_sha256=sha_of(os.path.join(
                                      plan.workspace, payload.path))))

    library = plan.by_path(LIBRARY_REL)
    publish_write(cfg, LIBRARY_REL, library.text, plan.workspace)
    written.append(library.record(committed=True,
                                  post_sha256=sha_of(plan.library_path)))
    return written


def post_write_checks(plan, written):
    """What actually landed, re-read from disk. Every failure here means the two
    files WERE written -- hence 81 and not 80 -- and `--restore` is the move."""
    detail_bytes, detail_payload = [], []
    for record in written:
        entry = plan.by_path(record["path"])
        if record["post_sha256"] != entry.plan_sha256:
            target = (detail_payload if record["path"] != LIBRARY_REL
                      else detail_bytes)
            target.append("%s on disk is %s, planned %s"
                          % (record["path"], (record["post_sha256"] or "")[:16],
                             entry.plan_sha256[:16]))
    library = json.loads(kc.read_text(plan.library_path))
    content = library.get("content") or []
    if len(content) != plan.post_entries:
        detail_bytes.append("library.json holds %d entries, the plan said %d"
                            % (len(content), plan.post_entries))
    hits = [i for i in content
            if i.get("filename") == plan.entry["filename"]]
    if len(hits) != 1:
        detail_bytes.append("%d entries carry filename %r"
                            % (len(hits), plan.entry["filename"]))
    return detail_payload, detail_bytes


def interlock_exit(written):
    """P6's band: 80 before a write, 81 after one.

    P6 is the only check that can fail on either side of the write, and `written`
    is the whole of the difference. A dirty lock RELEASE can only happen once
    both files are on disk -- akn_config.NightlyLock.__exit__ reports instead of
    raising for exactly that reason, and hands the band to this caller -- while
    80 contracts "nothing was written". Banding it 80 therefore told the operator
    not to run `--restore` in the one case where `--restore` is the move.
    run_restore's analogue already returns 81, so this is also what makes the two
    paths agree about the same fact.
    """
    return kc.EXIT_PRECONDITION if written else kc.EXIT_GUARD


def commit_command(cfg):
    """PRINTED, never run. See the module docstring's git rule."""
    paths = " ".join(rel[len(DOWNLOADS_ROOT) + 1:] for rel in publish_targets(cfg))
    return "\n".join([
        "  git -C SCED-downloads add %s" % paths,
        "  git -C SCED-downloads commit -m \\",
        "    'feat(playercards): add the Arknights fan-made player card pack'",
        "",
        "  arknights runs no mutating git verb -- run the two lines above yourself.",
        "  THAT COMMIT IS WHAT ARMS THE OVERLAP GATE (design section 8 R-A).",
    ])


# ---------------------------------------------------------------------------
# 6. --restore
# ---------------------------------------------------------------------------

RESTORE_ACTIONS = ("modify", "create")


def assert_restore_record(cfg, manifest, run_dir):
    """Every path, action AND snapshot in the manifest, checked before the first
    write. 80 promises nothing was written and the loop below writes as it goes,
    so a per-entry check would break that promise on entry 2.

    Three separate channels, because closing one leaves the other two open:

    * the PATH is the closed target pair, so neither the write target nor the
      derived snapshot name can traverse;
    * the ACTION selects between restoring and DELETING, and everything outside
      RESTORE_ACTIONS falls to the loop's delete branch -- so an unknown verb, or
      a `create` on library.json (a real plan always makes it `modify`), is a
      doctored record asking for a published file to be removed;
    * the SNAPSHOT is re-derived rather than merely contained, because
      containment alone would still let one entry read another's snapshot, and
      whatever is read is written into a file that gets published.

    Re-deriving the NAME is not enough on its own: a symlink AT that name is the
    derived name. So the resolved path is also required to be a real file inside
    snapshot_root -- the same realpath prefix test read_source() uses.

    BOTH of those are path-shaped, so neither is the guarantee. They state what
    the name looked like HERE, and the loop re-joins that name after an unbounded
    LIVE prompt; a hard link defeats them outright with no timing at all, since
    islink() is false for one and realpath() reports it as this path. The
    guarantee is the descriptor: the loop reads through kc.read_text_nofollow(),
    which refuses both in the same syscall that opens the file. These two checks
    stay because catching it here refuses at 80, before a byte is written, which
    is the better answer whenever the link was already in place.
    """
    entries = manifest.get("entries") or []
    assert_publish_targets(cfg, [record.get("path") for record in entries])
    root = snapshot_root_checked(run_dir)
    contain = root + os.sep
    for record in entries:
        action = record.get("action")
        if action not in RESTORE_ACTIONS:
            kc.refuse(kc.EXIT_GUARD,
                      "the restore record carries an action this run never wrote",
                      "%s: %r, but take_snapshots() writes one of %s. Anything "
                      "else reaches the delete branch."
                      % (record.get("path"), action, list(RESTORE_ACTIONS)))
        if action == "create" and record.get("path") == LIBRARY_REL:
            kc.refuse(kc.EXIT_GUARD,
                      "the restore record would DELETE %s" % LIBRARY_REL,
                      "a real plan always records it as `modify` -- it exists "
                      "upstream and is only ever appended to.")
        if action != "modify":
            continue
        expected = record["path"].replace("/", "__")
        if record.get("snapshot") != expected:
            kc.refuse(kc.EXIT_GUARD,
                      "the restore record names a snapshot this run did not write",
                      "%s: %r, but take_snapshots() writes %r"
                      % (record["path"], record.get("snapshot"), expected))
        snapshot = os.path.join(root, expected)
        # Absence is NOT refused here: the loop below reports a missing snapshot
        # as detail and carries on with the other entry, which is the right
        # answer for a half-deleted run directory.
        if os.path.islink(snapshot):
            kc.refuse(kc.EXIT_GUARD,
                      "the snapshot %s is a symlink" % expected,
                      "%s -> %s. take_snapshots() writes a regular file through "
                      "os.replace(); a link at that name reads somebody else's "
                      "file into a target that gets published."
                      % (expected, os.path.realpath(snapshot)))
        if not os.path.realpath(snapshot).startswith(contain):
            kc.refuse(kc.EXIT_GUARD,
                      "the snapshot %s resolves outside the run directory"
                      % expected,
                      "%s is not under %s" % (os.path.realpath(snapshot), contain))
    return entries


def run_restore(cfg, run_dir, assume_yes=False, workspace=None, confirm=True,
                now_min=None):
    """Put both files back from <run_dir>/publish/pre/.

    A `modify` is restored from its snapshot. A `create` is DELETED, and only
    when the file's sha256 still equals the bytes this run wrote -- anything else
    is somebody's later edit and refusing is the only safe answer.

    Two of `publish`'s three interlocks are enforced here and the third is not,
    which is a decision rather than an omission. The LOCK is what actually stops
    a concurrent write, so it is taken; the STAMP is what the silent-night
    runbook row reads, so it is written. The nightly WINDOW only warns: a restore
    is unplanned and time-sensitive, and refusing one for up to 2h25m is a worse
    failure than overlapping a nightly whose lock this process is holding.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    if now_min is None:
        now_min = now_min_override()
    # Checked once, here, and reused for every join below: deriving it twice
    # would give the second derivation a fresh chance to resolve a link planted
    # in between.
    root = snapshot_root_checked(run_dir)
    manifest_path = os.path.join(root, "manifest.json")
    if not os.path.exists(manifest_path):
        kc.refuse(kc.EXIT_PRECONDITION, "no restore record for this run",
                  "%s is absent -- `publish --live` writes it before the first "
                  "byte" % manifest_path)
    # Through the no-follow reader like every other read on this path. The old
    # plain read was argued safe because the manifest is validated before any
    # write, and that argument holds against a link at the FINAL component --
    # but the manifest chooses which entries the loop restores, so a prefix this
    # process did not verify chooses them instead. 80: nothing is written yet.
    manifest = json.loads(kc.read_text_nofollow(manifest_path, kc.EXIT_GUARD))
    # Both before the banner, for the reason step 1 gives: nobody is asked to
    # type LIVE into a run that is about to refuse. The lock probe is the half
    # this path used to omit -- it takes the lock like the live write does, so
    # it owes the same refusal at the same point. The window is deliberately NOT
    # asserted here; see the warning inside the lock below.
    entries = assert_restore_record(cfg, manifest, run_dir)
    probe_lock_before_banner(workspace)

    if confirm and not confirm_live(restore_banner(manifest, run_dir, workspace),
                                    assume_yes):
        kc.refuse(kc.EXIT_DISPATCH_LIVE_DECLINED, "--restore declined at the banner")

    restored, detail = [], []
    # Before the lock: the only process spawn this path had inside it.
    base = base_sha(workspace)
    lock = workspace_lock(workspace)
    with lock:
        inside, win = kz.in_schedule_window(now_min=now_min)
        if inside:
            print("WARNING: inside the nightly's window %s-%s %s. This process "
                  "holds the lock, so there is no concurrent write -- but a "
                  "nightly run starting now will find the lock held."
                  % (win["start"], win["end"], win["timezone"]))
        write_restore_stamp(run_dir, entries, workspace, base)
        for record in entries:
            rel = record["path"]
            target = os.path.join(workspace, rel)
            if record.get("action") == "modify":
                snapshot = os.path.join(root, record["snapshot"])
                # lexists, not exists: a DANGLING symlink at the derived
                # name is a planted link, not a missing snapshot, and it has
                # to reach the reader below to be named as one.
                if not os.path.lexists(snapshot):
                    detail.append("%s: the snapshot %s is gone"
                                  % (rel, record["snapshot"]))
                    continue
                # 81, not 80: entry 1 may already be on disk by now, and 80
                # contracts that nothing was written.
                text = kc.read_text_nofollow(snapshot, kc.EXIT_PRECONDITION)
                publish_write(cfg, rel, text, workspace)
                restored.append(rel)
                continue
            if not os.path.lexists(target):
                continue
            # Through the no-follow reader, not sha_of(): sha_of is
            # os.path.exists + open("rb"), both of which FOLLOW a symlink,
            # while os.unlink removes the link -- so a symlink at the target
            # had the guard hashing the pointee and the delete removing the
            # pointer. The reader refuses a link, a hard link and a
            # non-regular file in the open itself.
            current = kc.sha256_bytes(
                kc.read_text_nofollow(target, kc.EXIT_PRECONDITION)
                .encode("utf-8"))
            if current != record.get("plan_sha256"):
                detail.append("%s was created by this run but its bytes have "
                              "since changed (%s != %s) -- not deleting"
                              % (rel, current[:16],
                                 (record.get("plan_sha256") or "")[:16]))
                continue
            os.unlink(target)
            restored.append(rel)
        # Deleted only once the sequence COMPLETED. This used to be a finally,
        # which meant an OSError between the two publish_write() calls -- disk
        # full, permission -- both left a partial write AND destroyed the one
        # artifact the Stall runbook's silent-night row reads. A SIGKILL
        # preserved the stamp while an ordinary error erased it, which is
        # backwards. write_stamp() overwrites unconditionally, so a stamp left
        # behind by a failed run cannot block the next one.
        remove_stamp(workspace)
    if not lock.released_ok:
        detail.append("the lock was not released cleanly: %s. The restore itself "
                      "completed -- check the two files, then read the owner line"
                      % lock.release_message)
    return restored, detail


# ---------------------------------------------------------------------------
# 7. The stage
# ---------------------------------------------------------------------------

def run_publish(cfg, run_dir, live=False, assume_yes=False, workspace=None,
                confirm=True, now_min=None, accepted=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    kz.assert_predecessors(run_dir, STAGE, workspace)
    if now_min is None:
        now_min = now_min_override()

    plan = PublishPlan(cfg, run_dir, workspace)
    window = probe_before_banner(now_min, workspace)

    checks, written, lock_detail = [], [], []
    lock_note = "not taken (rehearsal)"

    if live:
        path = plan_path(run_dir)
        if not os.path.exists(path):
            kc.refuse(kc.EXIT_PRECONDITION, "there is no plan for this run",
                      "%s is absent. Run the rehearsal first -- the banner is "
                      "built from it and the live write is asserted against it."
                      % path)
        persisted = json.loads(kc.read_text(path))
        if confirm and not confirm_live(banner(plan, live=True), assume_yes):
            kc.refuse(kc.EXIT_DISPATCH_LIVE_DECLINED, "--live declined at the banner")
        assert_plan_matches(plan, persisted)

        # workspace_lock, not NightlyLock(): the stamp two lines down is scoped
        # to plan.workspace, so an unscoped lock put the lock and its own
        # corroborating stamp in two different roots -- and a gate driving this
        # against a temp workspace would take the REAL one the driver reads.
        lock = workspace_lock(workspace)
        with lock:
            # Re-checked INSIDE the lock: the probe before the banner closes
            # nothing on its own, and the operator may have taken minutes to type.
            kz.assert_outside_window(now_min=now_min)
            write_stamp(plan)
            take_snapshots(cfg, plan)
            written = commit_writes(cfg, plan)
            # Deleted only once the sequence COMPLETED. This used to be a finally,
            # which meant an OSError between the two publish_write() calls -- disk
            # full, permission -- both left a partial write AND destroyed the one
            # artifact the Stall runbook's silent-night row reads. A SIGKILL
            # preserved the stamp while an ordinary error erased it, which is
            # backwards. write_stamp() overwrites unconditionally, so a stamp left
            # behind by a failed run cannot block the next one.
            remove_stamp(workspace)
        lock_note = "taken and %s" % lock.release_message
        if not lock.released_ok:
            lock_detail.append("the lock was not released cleanly: %s. The two "
                               "files WERE written -- check them, then read the "
                               "owner line" % lock.release_message)
    else:
        kz.write_guarded(cfg, plan_path(run_dir), plan.document(),
                         workspace=workspace)
        for entry in plan.entries:
            kz.write_guarded(cfg,
                             os.path.join(rehearsal_root(run_dir),
                                          os.path.basename(entry.path)),
                             entry.text, workspace=workspace)

    # P1 is about the write GUARD, so it carries 80. P3-P5 are about the CONTENT
    # of a named file, so they carry 81. The split is the first move, not the
    # severity: 80 means look at the target pair and the lock, 81 means look at
    # the file the check names -- and after a live write that file is on disk, so
    # `--restore` is available where 80 would have promised nothing had been
    # written.
    # P6 is the one check that can fail on EITHER side of the write, so it is the
    # one that has to read `written` rather than pick a band once. See its own
    # comment below.
    checks.append(kc.check(
        "P1", "write set is the closed target pair",
        assert_target_findings(cfg, plan),
        exit_on_fail=kc.EXIT_GUARD, subject_size=len(plan.entries)))

    if live:
        p2_note = ("asserted path-for-path against %s"
                   % os.path.basename(plan_path(run_dir)))
    else:
        p2_note = "the rehearsal WRITES the plan; the live invocation asserts it"
    checks.append(kc.check(
        "P2", "plan binding", [], exit_on_fail=kc.EXIT_PRECONDITION,
        status=None if live else "skipped", note=p2_note))

    checks.append(kc.check(
        "P3", "library entry and count delta", library_findings(plan),
        exit_on_fail=kc.EXIT_PRECONDITION,
        note="%d -> %d (%+d), %s" % (plan.pre_entries, plan.post_entries,
                                     plan.delta,
                                     "replaces an existing entry"
                                     if plan.replaces_existing else "new entry")))

    payload_detail, library_detail = [], []
    if live:
        payload_detail, library_detail = post_write_checks(plan, written)

    checks.append(kc.check(
        "P4", "sorter stability", sorter_findings(plan) + library_detail,
        exit_on_fail=kc.EXIT_PRECONDITION,
        note="misc/sort_library.py is idempotent on the planned document"))

    checks.append(kc.check(
        "P5", "payload bytes", payload_findings(plan) + payload_detail,
        exit_on_fail=kc.EXIT_PRECONDITION,
        note="%.1f KB, sha256 %s"
             % (plan.entries[0].size / 1024.0,
                plan.entries[0].plan_sha256[:12])))

    checks.append(kc.check(
        "P6", "nightly interlock", lock_detail,
        exit_on_fail=interlock_exit(written),
        note="outside %s-%s %s; lock %s" % (window["start"], window["end"],
                                            window["timezone"], lock_note)))

    counts = collections.OrderedDict([
        ("files", len(plan.entries)),
        ("bytes", sum(e.size for e in plan.entries)),
        ("payload_bytes", plan.entries[0].size),
        ("library_entries_before", plan.pre_entries),
        ("library_entries_after", plan.post_entries),
        ("library_delta", plan.delta),
        ("committed", len(written)),
    ])
    report = kc.new_report(
        STAGE, plan.run_id, mode="build" if live else "dry-run",
        counts=dict(counts), checks=checks, accepted=accepted or {},
        binding=kc.build_binding([cfg["_path"], plan.built,
                                  plan_path(run_dir)]),
        results=collections.OrderedDict([
            ("live", live),
            ("targets", list(publish_targets(cfg))),
            ("entry", plan.entry),
            ("base_sha", plan.base_sha),
            ("commit_command", commit_command(cfg)),
        ]))
    if live:
        report["write_set"] = [r["path"] for r in written]
    else:
        report["write_set"] = [os.path.relpath(path, workspace) for path in
                               [plan_path(run_dir)] +
                               [os.path.join(rehearsal_root(run_dir),
                                             os.path.basename(e.path))
                                for e in plan.entries]]
    kc.finalize_report(report,
                       triggered=[c["exit_on_fail"] for c in checks
                                  if c["status"] == "fail"])
    return report, plan


def assert_target_findings(cfg, plan):
    """P1's detail, and it is provably always empty -- state that rather than
    read the pass as coverage. PublishPlan.__init__ runs assert_publish_targets
    over these same entries, so a violating plan refuses at 80 in the constructor
    and never reaches the check. Kept as defence in depth against a future plan
    built some other way; do NOT write a fixture for it, because one cannot exist
    while the constructor holds."""
    allowed = set(publish_targets(cfg))
    return ["%s is not one of %s" % (e.path, sorted(allowed))
            for e in plan.entries if e.path not in allowed]


def library_findings(plan):
    rules = plan.cfg["library_rules"]
    detail = []
    if plan.delta != (0 if plan.replaces_existing else 1):
        detail.append("the count moves by %+d; a first publish is +1 and a "
                      "re-publish is 0" % plan.delta)
    planned = [i for i in json.loads(plan.by_path(LIBRARY_REL).text)["content"]
               if i.get("filename") == plan.entry["filename"]]
    if len(planned) != 1:
        detail.append("the planned document carries %d entries with filename %r"
                      % (len(planned), plan.entry["filename"]))
    for key in rules["forbidden_keys"]:
        if key in plan.entry or any(key in i for i in planned):
            detail.append("the entry carries the forbidden key %r -- %s"
                          % (key, "cycle_code is the safety mechanism that keeps "
                             "the pack out of the card index"
                             if key == "cycle_code" else "it has no meaning here"))
    if plan.entry.get("author") == rules["forbidden_author"]:
        detail.append("the entry's author is the FFG sentinel, which lands it in "
                      "a bucket no download-window tab selects")
    if plan.post_entries < rules["expected_entries_min"]:
        detail.append("%d entries after the write, below the floor of %d -- "
                      "library.json looks truncated"
                      % (plan.post_entries, rules["expected_entries_min"]))
    return detail


def sorter_findings(plan):
    """The sorter must be a no-op on the document we plan to write."""
    doc = json.loads(plan.by_path(LIBRARY_REL).text)
    again, _replaced = planned_library(plan.sorter, doc, plan.entry)
    if kc.json_bytes(again) != kc.json_bytes(doc):
        return ["the planned library.json is not sorter-stable -- re-running "
                "misc/sort_library.py on it would produce a diff"]
    return []


def payload_findings(plan):
    entry = plan.entries[0]
    built = kc.sha256_file(plan.built)
    if entry.plan_sha256 != built:
        return ["the planned payload (%s) is not the built bag (%s)"
                % (entry.plan_sha256[:16], built[:16])]
    return []


# ---------------------------------------------------------------------------
# 8. --selftest
# ---------------------------------------------------------------------------

FAULTS = ("targets", "plan-binding", "library", "window", "restore-record",
          "restore-snapshot")


def selftest(fault=None, verbose=True):
    # Function-local so the module stays light for the stages; at the TOP of it
    # because two faults need them and either can be selected on its own.
    import shutil
    import tempfile

    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))
    cfg = kz.load_config()

    def fires(label, code, fn):
        try:
            fn()
        except kc.AknRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d (%s)"
                                % (label, code, exc.code, exc.message))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    def _bounded(secs, fn):
        """A wall clock, so a fault that WEDGES reports instead of hanging.

        The FIFO fault below exists because the open can block; a gate that hung
        to prove a hang would say nothing, and this suite is the compensating
        control for a package no CI runs. The timeout is raised as a refusal
        with the wrong code on purpose, so `fires` names it as a mismatch.
        """
        import signal

        def expired(_signum, _frame):
            raise kc.AknRefusal(kc.EXIT_BUG,
                                "the bounded call did not return within %ds"
                                % secs,
                                "it wedged, which is the failure this fault "
                                "exists to catch -- O_NONBLOCK.")
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(secs)
        try:
            return fn()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    if "targets" in wanted:
        pair = publish_targets(cfg)
        if pair != ("SCED-downloads/downloadable/playercards/arknights.json",
                    "SCED-downloads/library.json"):
            findings.append("targets: the pair moved -- %s" % (pair,))
        for bad in ("SCED-downloads/library.json.bak", "SCED/objects/x.json",
                    "SCED-downloads/downloadable/playercards/../../x.json",
                    "docs/Arknights/TTS용 파일/x.json", ".local-sync/run/x"):
            fires("targets %r" % bad, kc.EXIT_GUARD,
                  lambda b=bad: assert_publish_targets(cfg, [b]))
        # Every publish target must ALSO be refused by the ordinary write guard:
        # that is what makes `publish` the single exception rather than a hole.
        for rel in pair:
            if not kz.check_write_paths(cfg, [os.path.join(kc.WORKSPACE_ROOT, rel)]):
                findings.append("targets: %s is not refused by guard.write_roots, "
                                "so publish is not the only writer of it" % rel)

    if "plan-binding" in wanted:
        class _Plan(object):
            def document(self):
                return {"entries": [{"path": "a", "plan_sha256": "x"},
                                    {"path": "b", "plan_sha256": "y"}]}
        plan = _Plan()
        if not assert_plan_matches(plan, plan.document()):
            findings.append("plan-binding: an identical plan did not match")
        fires("plan-binding (short)", kc.EXIT_PRECONDITION,
              lambda: assert_plan_matches(plan, {"entries": [{"path": "a",
                                                              "plan_sha256": "x"}]}))
        fires("plan-binding (reordered)", kc.EXIT_PRECONDITION,
              lambda: assert_plan_matches(plan, {"entries": [
                  {"path": "b", "plan_sha256": "y"},
                  {"path": "a", "plan_sha256": "x"}]}))
        fires("plan-binding (bytes moved)", kc.EXIT_PRECONDITION,
              lambda: assert_plan_matches(plan, {"entries": [
                  {"path": "a", "plan_sha256": "x"},
                  {"path": "b", "plan_sha256": "MOVED"}]}))

    if "library" in wanted:
        sorter = load_sorter()
        entry = dict(cfg["library_entry"])
        base = {"content": [{"name": "Zzz", "type": "playercards",
                             "author": "Someone", "filename": "zzz"}]}
        doc, replaced = planned_library(sorter, base, entry)
        if replaced or len(doc["content"]) != 2:
            findings.append("library: a first publish must append, not replace")
        again, replaced2 = planned_library(sorter, doc, entry)
        if not replaced2 or len(again["content"]) != 2:
            findings.append("library: a re-publish must replace in place")
        if kc.json_bytes(again) != kc.json_bytes(doc):
            findings.append("library: planned_library is not idempotent")
        if list(doc["content"][0]) != [k for k in sorter.KEY_ORDER
                                       if k in doc["content"][0]]:
            findings.append("library: the written key order is not sort_library's")

    if "window" in wanted:
        fires("window (inside, before the banner)", kc.EXIT_GUARD,
              lambda: probe_before_banner(now_min=2 * 60 + 30))
        if os.environ.get(NOW_MIN_ENV) is None and not kz.probe_lock()["held"]:
            probe_before_banner(now_min=12 * 60)

    if "restore-record" in wanted:
        tmp = tempfile.mkdtemp(prefix="akn-publish-selftest.")
        fires("restore (no record)", kc.EXIT_PRECONDITION,
              lambda: run_restore(cfg, tmp, assume_yes=True, confirm=False))
        os.rmdir(tmp)

    if "restore-snapshot" in wanted:
        pair = publish_targets(cfg)
        run = tempfile.mkdtemp(prefix="akn-publish-restore.")
        root = snapshot_root(run)
        os.makedirs(root)

        def _record(path, snapshot, action="modify"):
            return {"entries": [{"path": path, "action": action,
                                 "snapshot": snapshot}]}

        good = _record(pair[1], pair[1].replace("/", "__"))
        if assert_restore_record(cfg, good, run) != good["entries"]:
            findings.append("restore-snapshot: a legitimate record was rejected")
        # The first is the traversal the reader used to accept; the second is the
        # subtler one, a name that IS a snapshot this run wrote -- just not this
        # entry's. Both end with foreign bytes inside a published file.
        for bad in ("../../../../.config/sced-sync/env",
                    pair[0].replace("/", "__"), "manifest.json", None):
            fires("restore-snapshot %r" % bad, kc.EXIT_GUARD,
                  lambda b=bad: assert_restore_record(cfg, _record(pair[1], b),
                                                      run))
        fires("restore-snapshot (path outside the pair)", kc.EXIT_GUARD,
              lambda: assert_restore_record(
                  cfg, _record("SCED/objects/x.json", "SCED__objects__x.json"),
                  run))
        # The name check passes and the read still escapes: a symlink AT the
        # derived name is the derived name. Planted for real, because the whole
        # point is that no string test can see it.
        link = os.path.join(root, pair[1].replace("/", "__"))
        os.symlink(os.path.join(run, "elsewhere"), link)
        fires("restore-snapshot (symlink at the derived name)", kc.EXIT_GUARD,
              lambda: assert_restore_record(cfg, good, run))
        os.unlink(link)
        # The two channels the path-shaped checks above cannot close, exercised
        # against the reader the LOOP uses -- because that, not the assertion, is
        # where the guarantee lives. 81 rather than 80: the loop writes as it
        # goes, so by the time it reads entry 2 entry 1 is already on disk.
        secret = os.path.join(run, "secret.env")
        kc.atomic_write_text(secret, "SCED_SYNC_DISCORD_WEBHOOK=xxx\n")
        os.link(secret, link)
        # Non-vacuity: BOTH path predicates pass on a hard link. If that ever
        # stops being true the fault below is proving nothing.
        if os.path.islink(link) or not os.path.realpath(link).startswith(
                os.path.realpath(root) + os.sep):
            findings.append("restore-snapshot: a hardlink no longer defeats the "
                            "path checks, so the reader fault proves nothing")
        fires("restore-snapshot (hardlink at the derived name)",
              kc.EXIT_PRECONDITION,
              lambda: kc.read_text_nofollow(link, kc.EXIT_PRECONDITION))
        os.unlink(link)
        os.symlink(secret, link)
        fires("restore-snapshot (symlink swapped after the assertion)",
              kc.EXIT_PRECONDITION,
              lambda: kc.read_text_nofollow(link, kc.EXIT_PRECONDITION))
        os.unlink(link)
        os.unlink(secret)
        # A third KIND of channel: both faults above are aliases, and neither
        # O_NOFOLLOW nor st_nlink says the object is a regular file. A FIFO
        # passes every predicate on both sides of the read and wedges the open
        # itself -- inside the workspace lock, so it is a silent night. Bounded
        # rather than left to hang: a revert of O_NONBLOCK must NAME itself here,
        # and a gate that wedges to prove a wedge reports nothing.
        os.mkfifo(link)
        if os.path.islink(link) or not os.path.realpath(link).startswith(
                os.path.realpath(root) + os.sep) or os.lstat(link).st_nlink != 1:
            findings.append("restore-snapshot: a FIFO no longer defeats the "
                            "path checks, so the reader fault proves nothing")
        fires("restore-snapshot (FIFO at the derived name)",
              kc.EXIT_PRECONDITION,
              lambda: _bounded(5, lambda: kc.read_text_nofollow(
                  link, kc.EXIT_PRECONDITION)))
        os.unlink(link)
        # A directory is the same class but a WEAKER fault, and saying so is the
        # point: st_nlink is 2 for one, so the hardlink check already refuses it
        # and it pins nothing about S_ISREG -- measured, after this comment first
        # claimed the opposite. It is kept because it pins the OUTCOME: without
        # some check ahead of the read, os.read() raises EISDIR uncaught, which
        # is exit 1, outside the documented band. The FIFO above is the only
        # fault here that fails when S_ISREG is removed.
        os.mkdir(link)
        fires("restore-snapshot (directory at the derived name)",
              kc.EXIT_PRECONDITION,
              lambda: kc.read_text_nofollow(link, kc.EXIT_PRECONDITION))
        os.rmdir(link)
        # The action field is what selects the delete branch, so every value
        # outside RESTORE_ACTIONS -- and a `create` on library.json, which a real
        # plan never writes -- has to refuse before the loop reaches os.unlink.
        for act in ("create", "typo", None):
            fires("restore-snapshot action %r" % act, kc.EXIT_GUARD,
                  lambda a=act: assert_restore_record(
                      cfg, _record(pair[1], pair[1].replace("/", "__"), a), run))
        payload_created = _record(pair[0], None, "create")
        if assert_restore_record(cfg, payload_created, run) != \
                payload_created["entries"]:
            findings.append("restore-snapshot: a legitimate `create` on the "
                            "payload was rejected")
        shutil.rmtree(run)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 9. main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="akn_publish.py",
        description="arknights `publish` -- rehearsal by default; --live writes "
                    "the two SCED-downloads files and prints the commit command.")
    parser.add_argument("--run-dir")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--accept-declared-remainder", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT")
    args = parser.parse_args(argv)

    kc.check_invocation_guards()

    if args.selftest is not None:
        fault = args.selftest or None
        print("akn_publish --selftest%s" % (" %s" % fault if fault else ""))
        found = selftest(fault)
        if found:
            print("\nFAIL (%d):" % len(found))
            return kc.EXIT_VERIFY
        print("  ok: the closed target pair and its refusals, the two-process "
              "plan binding, append-vs-replace and sorter key order, the "
              "pre-banner window refusal, and the restore record's absence, "
              "its actions, its snapshot names AND resolved paths, and the "
              "reader that refuses a hardlink, a swapped symlink, a FIFO or a "
              "directory at a name those checks already passed")
        return kc.EXIT_OK

    if not args.run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    cfg = kz.load_config()

    if args.restore:
        restored, detail = run_restore(cfg, args.run_dir, assume_yes=args.yes,
                                       now_min=now_min_override())
        for line in detail:
            print("  ! %s" % line)
        for rel in restored:
            print("  restored %s" % rel)
        return kc.EXIT_PRECONDITION if detail else kc.EXIT_OK

    report, plan = run_publish(
        cfg, args.run_dir, live=args.live, assume_yes=args.yes,
        accepted={"declared-remainder": bool(args.accept_declared_remainder)})
    kc.write_report(report, args.run_dir)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return report["exit_code"]

    if not args.live:
        print(banner(plan, live=False))
    print("arknights publish -- %s%s" % (report["run_id"],
                                         "" if args.live else "  (REHEARSAL)"))
    print("  targets     : %s" % ", ".join(report["results"]["targets"]))
    print("  library     : %d -> %d entries (%+d)"
          % (report["counts"]["library_entries_before"],
             report["counts"]["library_entries_after"],
             report["counts"]["library_delta"]))
    print("  payload     : %.1f KB" % (report["counts"]["payload_bytes"] / 1024.0))
    for entry in report["checks"]:
        dots = "." * max(1, 34 - len(entry["name"]))
        print("  %-4s %s %s %-8s %s" % (entry["id"], entry["name"], dots,
                                        entry["status"], entry.get("note") or ""))
        for line in entry["detail"][:6]:
            print("       - %s" % line)
    print("  verdict     : %s (exit %d, consumable %s)"
          % (report["verdict"], report["exit_code"], report["consumable"]))
    if args.live and report["exit_code"] == kc.EXIT_OK:
        print("\ncommit it yourself:")
        print(report["results"]["commit_command"])
    elif not args.live:
        print("\n  Nothing outside %s was touched. `--live` performs the write."
              % os.path.basename(args.run_dir))
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.AknRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
