#!/usr/bin/env python3
"""Apply a validated overlap decision manifest and verify the resulting tree.

Part of the ``ai-rebase-conflict-resolution`` feature
(.am/ai-rebase-conflict-resolution/design.md §3.1 verb table, §5.6, §5.7, §6 S6).
Stage 4 of

    classify (ai-overlap-classify.py)
      -> adjudicate (claude -p --output-format json)
      -> decide (ai-overlap-decide.py)
      -> APPLY (this script)

The verb algebra is closed: every byte this script writes already exists in one of
the four trees ``B`` (merge base), ``U`` (upstream), ``F`` (fork) or ``R`` (the
post-rebase result). That is the design's single most load-bearing safety property
and check 4 below re-proves it after the fact, from git, independently of the
write path.

    keep_merge         no write                                        -> R
    take_upstream      git checkout <upstream_sha> -- <path>           -> U
    take_fork          git checkout <fork_sha>     -- <path>           -> F
    patch_json_fields  R with the declared JSON pointers swapped for
                       the value at the same pointer on the declared
                       side (key deleted if absent there)              -> R'

Byte fidelity: decomposed card files are 2-space-indented with NO trailing
newline, so ``patch_json_fields`` writes ``dumps_faithful`` through
``sced_io.atomic_write_text`` -- never ``atomic_write_json``, which appends a
newline and would produce format churn across 1300 files.

Verification order (design §5.7, reconciled with §5.6). The path-scoped
``git add`` runs first because §5.7 check 5 is explicitly an index check at a
point where HEAD is still the pre-apply commit, and a case collision must surface
as its own exit code rather than be masked by check 1:

    -- git add -- <touched> --
    5  case-collision detector  -- git ls-files --stage, lowercased, uniq -d  (62)
    1  clean tree after write   -- status lists exactly the declared paths
    2  conflict-marker scan     -- over overlap union touched
    3  JSON validity            -- every touched *.json, plus SCED-downloads'
                                   library.json / modversion.json
    4  byte provenance          -- recomputed from git, not from the writer
    6  repo invariants          -- SOURCE_REPO / langpack + R2 floor / divergence

Nothing is committed until every check has passed. There is no snapshot and no
auto-restore, and that is a property of running after the rebase rather than an
omission: until the driver's force-push, the only thing outside ``.local-sync/``
that has moved is the pre-rebase backup branch, which is already the rollback
anchor. A failed apply exits before the commit and the scratch worktree is
destroyed by the caller.

Usage:
  ai-overlap-apply.py --repo <SCED|SCED-downloads>
                      --worktree DIR          (must be under .local-sync/scratch/)
                      --run-dir DIR
                      [--decide FILE] [--manifest FILE] [--classes FILE]
                      [--run-stamp STR] [--model STR] [--effort STR]
                      [--claude-bin PATH] [--cli-version STR]
                      [--session-id STR] [--total-cost-usd F]
                      [--duration-s N] [--num-turns N]
                      [--r2-floor-pct 98]
                      [--dry-run] [--no-commit]

Exit codes:
   0  applied, verified, committed (or --dry-run: would apply)
   1  usage error
  62  case-only path collision introduced by the resolution
  67  apply failed, or a post-apply invariant failed
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from sced_io import atomic_write_text

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CASE = 62
EXIT_APPLY = 67

LANGPACK_KOREAN_PREFIX = "decomposed/language-pack/Korean - "
R2_HOST = "pub-05b4fa32b44341d797f5c66d59384724.r2.dev"
SOURCE_REPO_RE = re.compile(
    r'^\s*SOURCE_REPO\s*=\s*"https://github\.com/shanash/SCED-downloads/'
    r'releases/latest/download/"\s*$', re.MULTILINE)
CONSTANTS_PATH = "src/core/Constants.ttslua"
MARKER_RE = r"^(<{7}|>{7}) |^={7}$"
CHUNK = 200
# The manifest names sides in words; the blob table is keyed by the four-blob
# symbols of design §1.2. One place to map them.
SIDE_BLOB = {"base": "B", "upstream": "U", "fork": "F"}


class ApplyError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------------ git
def git(wt: Path, *args: str, check: bool = True, binary: bool = False,
        stdin: bytes | None = None):
    """Read/write git with the driver's hardening flags (daily-sync-local.sh:200-219).

    core.ignorecase is deliberately not forced: on this case-insensitive APFS
    volume forcing it false manufactures phantom changes. Case exposure is caught
    by check 5 instead.
    """
    cmd = ["git", "-C", str(wt),
           "-c", "core.hooksPath=/dev/null",
           "-c", "gc.auto=0",
           "-c", "rerere.enabled=false",
           "-c", "core.quotePath=false",
           *args]
    res = subprocess.run(cmd, capture_output=True, input=stdin)
    if check and res.returncode != 0:
        raise ApplyError(EXIT_APPLY,
                         f"git {' '.join(args[:4])} failed (rc={res.returncode}): "
                         f"{res.stderr.decode(errors='replace').strip()}")
    return res if binary else (res.returncode,
                               res.stdout.decode("utf-8", errors="replace"),
                               res.stderr.decode("utf-8", errors="replace"))


def read_blobs(wt: Path, ref: str, paths: list[str]) -> dict[str, bytes | None]:
    """One ``git cat-file --batch`` per ref for the whole path list.

    Deliberately a second, self-contained implementation rather than a shared
    helper with ai-overlap-classify.py: check 4 is required to recompute
    provenance *independently*, and a single shared reader with a subtle bug
    would fail identically on both sides of the comparison.
    """
    if not paths:
        return {}
    proc = subprocess.run(
        ["git", "-C", str(wt), "-c", "core.quotePath=false",
         "cat-file", "--batch", "--buffer"],
        input="".join(f"{ref}:{p}\n" for p in paths).encode("utf-8"),
        capture_output=True)
    if proc.returncode != 0:
        raise ApplyError(EXIT_APPLY, f"git cat-file --batch failed at {ref}: "
                                     f"{proc.stderr.decode(errors='replace').strip()}")
    out, pos = proc.stdout, 0
    blobs: dict[str, bytes | None] = {}
    for p in paths:
        nl = out.find(b"\n", pos)
        if nl < 0:
            raise ApplyError(EXIT_APPLY, f"cat-file --batch truncated at {ref}:{p}")
        header, pos = out[pos:nl], nl + 1
        if header.endswith(b" missing") or header.endswith(b" ambiguous"):
            blobs[p] = None
            continue
        parts = header.rsplit(b" ", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            raise ApplyError(EXIT_APPLY, f"cat-file --batch: bad header {header!r}")
        size = int(parts[2])
        blobs[p] = out[pos:pos + size]
        pos += size + 1
    return blobs


def chunks(seq: list[str], n: int = CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ------------------------------------------------------------------- pointers
def unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ApplyError(EXIT_APPLY, f"pointer {pointer!r} must start with '/'")
    return [unescape(t) for t in pointer[1:].split("/")]


_MISSING = object()


def pointer_get(obj, pointer: str):
    node = obj
    for part in pointer_parts(pointer):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def pointer_set(obj, pointer: str, value) -> None:
    parts = pointer_parts(pointer)
    node = obj
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def pointer_delete(obj, pointer: str) -> None:
    parts = pointer_parts(pointer)
    node = obj
    for part in parts[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return
    node.pop(parts[-1], None)


def dumps_faithful(data) -> str:
    """2-space indent, ensure_ascii=False, NO trailing newline (house format,
    apply-revert-decisions.py:90-93)."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def patched_text(path: str, r_blob: bytes | None, patches: list[dict],
                 side_blobs: dict[str, dict[str, bytes | None]]) -> str:
    """The exact text patch_json_fields must produce for ``path``."""
    if r_blob is None:
        raise ApplyError(EXIT_APPLY, f"{path}: patch_json_fields but the result blob "
                                     f"does not exist")
    try:
        obj = json.loads(r_blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplyError(EXIT_APPLY, f"{path}: result blob is not JSON: {exc}") from exc
    for patch in patches:
        side = patch["from"]
        if side not in SIDE_BLOB:
            raise ApplyError(EXIT_APPLY, f"{path}: unknown patch side {side!r}")
        src_blob = side_blobs[SIDE_BLOB[side]].get(path)
        if src_blob is None:
            raise ApplyError(EXIT_APPLY,
                             f"{path}: patch pointer {patch['pointer']} reads from "
                             f"{side!r}, where the file does not exist")
        try:
            src = json.loads(src_blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApplyError(EXIT_APPLY,
                             f"{path}: {side} blob is not JSON: {exc}") from exc
        val = pointer_get(src, patch["pointer"])
        if val is _MISSING:
            pointer_delete(obj, patch["pointer"])
        else:
            pointer_set(obj, patch["pointer"], val)
    return dumps_faithful(obj)


# --------------------------------------------------------------------- checks
class Verifier:
    def __init__(self):
        self.lines: list[str] = []
        self.failed: list[str] = []

    def record(self, n: int, name: str, ok: bool, detail: list[str] | None = None):
        self.lines.append(f"[{'PASS' if ok else 'FAIL'}] check {n} {name}")
        for d in (detail or [])[:40]:
            self.lines.append(f"         {d}")
        if not ok:
            self.failed.append(f"check {n} {name}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def git_status_paths(wt: Path) -> set[str]:
    _rc, out, _err = git(wt, "status", "--porcelain", "-z")
    paths: set[str] = set()
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if not rec:
            continue
        xy, name = rec[:2], rec[3:]
        if "R" in xy or "C" in xy:      # rename/copy: the origin path follows
            i += 1
        paths.add(name)
    return paths


def check_markers(wt: Path, paths: list[str], v: Verifier) -> None:
    existing = [p for p in paths if (wt / p).is_file()]
    hits: list[str] = []
    for batch in chunks(existing):
        rc, out, err = git(wt, "grep", "-I", "-n", "-E", MARKER_RE, "--", *batch,
                           check=False)
        if rc == 0:
            hits.extend(out.strip().split("\n")[:40])
        elif rc != 1:
            raise ApplyError(EXIT_APPLY, f"git grep failed (rc={rc}): {err.strip()}")
    v.record(2, "conflict-marker scan", not hits, hits)


def check_json(wt: Path, repo: str, touched: list[str], v: Verifier) -> None:
    targets = [p for p in touched if p.lower().endswith(".json")]
    if repo == "SCED-downloads":
        for extra in ("library.json", "modversion.json"):
            if (wt / extra).is_file() and extra not in targets:
                targets.append(extra)
    bad: list[str] = []
    for p in targets:
        f = wt / p
        if not f.is_file():
            bad.append(f"{p}: missing on disk")
            continue
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            bad.append(f"{p}: {exc}")
    v.record(3, f"JSON validity ({len(targets)} file(s))", not bad, bad)


def check_provenance(wt: Path, plan: list[dict], blobs: dict, v: Verifier) -> list[str]:
    """Design §5.7 check 4, recomputed from git rather than from the writer."""
    bad: list[str] = []
    matched: list[str] = []
    for item in plan:
        p, verdict = item["path"], item["verdict"]
        f = wt / p
        try:
            actual = f.read_bytes()
        except OSError as exc:
            bad.append(f"{p}: unreadable after write ({exc})")
            continue
        if verdict == "take_upstream":
            want, label = blobs["U"].get(p), "U"
        elif verdict == "take_fork":
            want, label = blobs["F"].get(p), "F"
        elif verdict == "patch_json_fields":
            want = patched_text(p, blobs["R"].get(p), item["patches"], blobs).encode("utf-8")
            label = "R+patches"
        else:
            bad.append(f"{p}: verdict {verdict!r} should not have been written")
            continue
        if want is None:
            bad.append(f"{p}: the {label} blob does not exist")
        elif actual != want:
            bad.append(f"{p}: bytes do not equal {label} "
                       f"({len(actual)} vs {len(want)} bytes)")
        else:
            matched.append(f"{p} == {label}")
    v.record(4, f"byte provenance ({len(plan)} touched)", not bad, bad)
    return matched


def check_case(wt: Path, v: Verifier) -> None:
    _rc, out, _err = git(wt, "ls-files", "--stage")
    lower: dict[str, list[str]] = {}
    for line in out.split("\n"):
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            lower.setdefault(parts[1].lower(), []).append(parts[1])
    dupes = [f"{group}" for group in lower.values() if len(group) > 1]
    v.record(5, "case-collision detector (index)", not dupes, dupes)
    if dupes:
        raise ApplyError(EXIT_CASE,
                         f"case-only path collision introduced by the resolution: "
                         f"{dupes[:5]}")


def langpack_counts(wt: Path, ref: str | None) -> tuple[int, int]:
    """(files under 'Korean - *', of which reference the R2 host)."""
    if ref:
        _rc, listing, _e = git(wt, "ls-tree", "-r", "--name-only", ref,
                               "--", "decomposed/language-pack/")
        rc, hits, err = git(wt, "grep", "-l", "-I", "-F", R2_HOST, ref,
                            "--", "decomposed/language-pack/", check=False)
        hit_paths = [ln.split(":", 1)[1] for ln in hits.split("\n")
                     if ln and ":" in ln]
    else:
        _rc, listing, _e = git(wt, "ls-files", "--", "decomposed/language-pack/")
        rc, hits, err = git(wt, "grep", "-l", "-I", "-F", R2_HOST,
                            "--", "decomposed/language-pack/", check=False)
        hit_paths = [ln for ln in hits.split("\n") if ln]
    if rc not in (0, 1):
        raise ApplyError(EXIT_APPLY, f"git grep for the R2 host failed (rc={rc}): "
                                     f"{err.strip()}")
    n = sum(1 for ln in listing.split("\n") if ln.startswith(LANGPACK_KOREAN_PREFIX))
    r = sum(1 for ln in hit_paths if ln.startswith(LANGPACK_KOREAN_PREFIX))
    return n, r


def check_invariants(wt: Path, repo: str, fork_sha: str, upstream_sha: str,
                     pre_sha: str, floor_pct: int, v: Verifier) -> dict:
    detail: list[str] = []
    stats: dict = {}
    ok = True

    if repo == "SCED":
        f = wt / CONSTANTS_PATH
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as exc:
            detail.append(f"{CONSTANTS_PATH}: unreadable ({exc})")
            ok = False
        else:
            if SOURCE_REPO_RE.search(text):
                detail.append(f"{CONSTANTS_PATH}: SOURCE_REPO points at the shanash fork")
            else:
                detail.append(f"{CONSTANTS_PATH}: SOURCE_REPO does NOT point at "
                              f"github.com/shanash/SCED-downloads/releases/latest/download/ "
                              f"-- the fork's entire reason to exist")
                ok = False

    if repo == "SCED-downloads":
        # "before" is the FORK tree, not the pre-apply tree: the mass-deletion this
        # is meant to catch (a delete/modify resolved to upstream's delete) happens
        # in the seeded rebase, before this script runs. Comparing post-apply to
        # pre-apply could never see it. The pre-apply numbers are recorded too.
        n_fork, r_fork = langpack_counts(wt, fork_sha)
        n_pre, r_pre = langpack_counts(wt, pre_sha)
        n_post, r_post = langpack_counts(wt, None)
        stats = {"n_fork": n_fork, "r_fork": r_fork, "n_pre": n_pre, "r_pre": r_pre,
                 "n_post": n_post, "r_post": r_post, "floor_pct": floor_pct}
        detail.append(f"langpack files: fork={n_fork} pre-apply={n_pre} post={n_post}")
        detail.append(f"R2-referencing:  fork={r_fork} pre-apply={r_pre} post={r_post} "
                      f"(floor {floor_pct}%)")
        if n_post < n_fork:
            detail.append(f"langpack file count fell {n_fork} -> {n_post}")
            ok = False
        if r_post * 100 < r_fork * floor_pct:
            detail.append(f"R2 reference count {r_post} is below "
                          f"{floor_pct}% of {r_fork}")
            ok = False

    _rc, out, _e = git(wt, "rev-list", "--count", f"{upstream_sha}..HEAD")
    divergence = int(out.strip() or 0)
    stats["divergence"] = divergence
    detail.append(f"divergence: {divergence} commit(s) ahead of {upstream_sha[:12]}")
    if divergence < 1:
        detail.append("divergence is 0 -- the fork's entire history vanished")
        ok = False

    v.record(6, f"repo invariants ({repo})", ok, detail)
    return stats


# ----------------------------------------------------------------------- main
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, choices=["SCED", "SCED-downloads"])
    p.add_argument("--worktree", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--decide", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--classes", type=Path)
    p.add_argument("--run-stamp", default="")
    p.add_argument("--model", default="")
    p.add_argument("--effort", default="")
    p.add_argument("--claude-bin", default="")
    p.add_argument("--cli-version", default="")
    p.add_argument("--session-id", default="")
    p.add_argument("--total-cost-usd", type=float, default=None)
    p.add_argument("--duration-s", type=int, default=None)
    p.add_argument("--num-turns", type=int, default=None)
    p.add_argument("--r2-floor-pct", type=int, default=98)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-commit", action="store_true")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def run(args) -> int:  # noqa: C901 - one linear pipeline, kept in reading order
    wt: Path = args.worktree
    run_dir: Path = args.run_dir
    started = time.time()

    # Scratch-path assertion, re-asserted independently of the caller
    # (daily-sync-local.sh:532-535, resolve-overlap-with-ai.sh).
    if "/.local-sync/scratch/" not in f"{wt.resolve()}/":
        print(f"refusing to operate outside a scratch worktree: {wt}", file=sys.stderr)
        return EXIT_USAGE

    decide_path = args.decide or run_dir / "decide.json"
    classes_path = args.classes or run_dir / "classes.json"
    manifest_path = args.manifest or run_dir / "manifest.json"
    for f in (decide_path, classes_path):
        if not f.is_file():
            print(f"ApplyError: missing {f}", file=sys.stderr)
            return EXIT_USAGE
    decide = json.loads(decide_path.read_text(encoding="utf-8"))
    classes_doc = json.loads(classes_path.read_text(encoding="utf-8"))

    if decide.get("outcome") != "proceed":
        raise ApplyError(EXIT_APPLY, f"decide.json outcome is "
                                     f"{decide.get('outcome')!r}, refusing to apply")

    merge_base = classes_doc["merge_base"]
    fork_sha = classes_doc["fork_sha"]
    upstream_sha = classes_doc["upstream_sha"]
    result_sha = classes_doc["result_sha"]
    overlap = [r["path"] for r in classes_doc["paths"]]

    _rc, head, _e = git(wt, "rev-parse", "HEAD")
    pre_sha = head.strip()
    if pre_sha != result_sha:
        raise ApplyError(EXIT_APPLY, f"HEAD is {pre_sha[:12]} but classes.json was "
                                     f"computed against {result_sha[:12]}")
    dirty = git_status_paths(wt)
    if dirty:
        raise ApplyError(EXIT_APPLY, f"worktree is dirty before apply: "
                                     f"{sorted(dirty)[:5]}")

    effective = decide["effective"]
    plan = [e for e in effective if e["verdict"] != "keep_merge"]
    take_up = [e["path"] for e in plan if e["verdict"] == "take_upstream"]
    take_fk = [e["path"] for e in plan if e["verdict"] == "take_fork"]
    patches = [e for e in plan if e["verdict"] == "patch_json_fields"]
    touched = [e["path"] for e in plan]

    if args.dry_run:
        print(f"(dry-run) would apply: take_upstream={len(take_up)} "
              f"take_fork={len(take_fk)} patch_json_fields={len(patches)} "
              f"keep_merge={len(effective) - len(plan)}")
        for e in plan[:20]:
            print(f"  {e['verdict']:18s} {e['path']}")
        return EXIT_OK

    # ---- blobs, read before anything is written
    blobs = {
        "B": read_blobs(wt, merge_base, overlap),
        "U": read_blobs(wt, upstream_sha, overlap),
        "F": read_blobs(wt, fork_sha, overlap),
        "R": read_blobs(wt, result_sha, overlap),
    }
    pre_oids: dict[str, str] = {}
    for batch in chunks(touched):
        _rc, staged, _e = git(wt, "ls-files", "--stage", "--", *batch)
        for line in staged.split("\n"):
            if line and "\t" in line:
                meta, name = line.split("\t", 1)
                pre_oids[name] = meta.split(" ")[1]

    # ---- write phase
    actions: list[dict] = []
    for batch in chunks(take_up):
        git(wt, "checkout", upstream_sha, "--", *batch)
    for batch in chunks(take_fk):
        git(wt, "checkout", fork_sha, "--", *batch)
    for e in patches:
        text = patched_text(e["path"], blobs["R"].get(e["path"]), e["patches"], blobs)
        atomic_write_text(wt / e["path"], text)
    for e in plan:
        actions.append({"path": e["path"], "verdict": e["verdict"],
                        "class": e.get("class"), "source": e.get("source"),
                        "pre_oid": pre_oids.get(e["path"]),
                        "patches": e.get("patches")})

    # ---- verification
    #
    # Ordering note (deviation from §5.6's literal step order, forced by §5.7
    # check 5's own parenthetical): the path-scoped `git add` happens BEFORE the
    # checks, not after, because check 5 is an index check and a case collision
    # must surface as its own exit code (62) rather than being masked by check 1
    # reporting the same event as an undeclared working-tree change. Checks 1-4
    # and 6 read the working tree and are unaffected by staging. Nothing is
    # committed until every check has passed; a failure leaves a staged index in
    # a scratch worktree the caller destroys.
    v = Verifier()
    for batch in chunks(touched):
        git(wt, "add", "--", *batch)
    try:
        check_case(wt, v)
    except ApplyError:
        atomic_write_text(run_dir / "verify.log", v.text())
        raise

    declared = set(touched)
    observed = git_status_paths(wt)
    extra = sorted(observed - declared)
    missing = sorted(declared - observed)
    same_bytes = [p for p in missing
                  if blobs["R"].get(p) is not None
                  and (wt / p).is_file()
                  and (wt / p).read_bytes() == blobs["R"][p]]
    # A declared path whose new bytes happen to equal R is a no-op write, not a
    # failure: git reports no change and the provenance check still proves the
    # bytes. Only an UNdeclared change is a hard failure.
    v.record(1, "clean tree after write", not extra,
             ([f"undeclared change: {p}" for p in extra]
              + [f"declared but unchanged (bytes already equal R): {p}"
                 for p in same_bytes]))
    check_markers(wt, sorted(set(overlap) | declared), v)
    check_json(wt, args.repo, touched, v)
    provenance = check_provenance(wt, plan, blobs, v)
    stats = check_invariants(wt, args.repo, fork_sha, upstream_sha, pre_sha,
                             args.r2_floor_pct, v)

    atomic_write_text(run_dir / "verify.log", v.text())
    if v.failed:
        raise ApplyError(EXIT_APPLY, "post-apply verification failed: "
                                     + ", ".join(v.failed))

    commit_sha = pre_sha
    if not args.no_commit and touched:
        stamp = args.run_stamp or time.strftime("%Y%m%d-%H%M%S")
        counts = decide.get("verdicts", {})
        git(wt,
            "-c", "user.name=SCED daily sync",
            "-c", "user.email=sced-daily-sync@localhost",
            "commit",
            "-m", f"fix(sync): AI overlap resolution {stamp}",
            "-m", f"overlap: {len(overlap)} paths, sha256 {decide.get('overlap_sha256','')}",
            "-m", f"manifest: {decide.get('manifest_sha256','')}",
            "-m", f"model: {args.model}  session: {args.session_id}  "
                  f"cost: ${args.total_cost_usd}",
            "-m", f"verdicts: keep_merge={counts.get('keep_merge', 0)} "
                  f"take_upstream={counts.get('take_upstream', 0)} "
                  f"take_fork={counts.get('take_fork', 0)} "
                  f"patch={counts.get('patch_json_fields', 0)}")
        _rc, head, _e = git(wt, "rev-parse", "HEAD")
        commit_sha = head.strip()

    resolved = [e["path"] for e in plan]
    result = {
        "enabled": True,
        "outcome": "proceed",
        "reason": "",
        "binary": args.claude_bin,
        "cli_version": args.cli_version,
        "model": args.model,
        "effort": args.effort,
        "session_id": args.session_id or decide.get("session_id", ""),
        "total_cost_usd": (args.total_cost_usd if args.total_cost_usd is not None
                           else decide.get("total_cost_usd")),
        "duration_s": (args.duration_s if args.duration_s is not None
                       else int(time.time() - started)),
        "num_turns": (args.num_turns if args.num_turns is not None
                      else decide.get("num_turns")),
        "overlap_sha256": decide.get("overlap_sha256", ""),
        "manifest_sha256": decide.get("manifest_sha256", ""),
        "commit_sha": commit_sha,
        "commits_replayed": classes_doc.get("commits_replayed"),
        "classes": decide.get("classes", {}),
        "verdicts": decide.get("verdicts", {}),
        "confidence": decide.get("confidence", {}),
        "seed_decided_count": decide.get("seed_decided_count", 0),
        "resolved_paths": resolved[:200],
        "resolved_paths_truncated": max(0, len(resolved) - 200),
        "artifact_dir": str(run_dir),
        "invariants": stats,
    }
    atomic_write_text(run_dir / "result.json", dumps_faithful(result) + "\n")
    atomic_write_text(run_dir / "apply-summary.json", dumps_faithful({
        "schema": 1,
        "failed_step": None,
        "original_exit_code": 0,
        "wrapper_exit_code": 0,
        "auto_restore_performed": False,
        "snapshot_path": "",
        "manifest": str(manifest_path),
        "pre_sha": pre_sha,
        "commit_sha": commit_sha,
        "counts": {"take_upstream": len(take_up), "take_fork": len(take_fk),
                   "patch_json_fields": len(patches),
                   "keep_merge": len(effective) - len(plan)},
        "actions": actions,
        "provenance": provenance[:200],
    }) + "\n")

    if not args.quiet:
        print(f"apply: {len(plan)} write(s) "
              f"(take_upstream={len(take_up)} take_fork={len(take_fk)} "
              f"patch={len(patches)}), {len(effective) - len(plan)} keep_merge")
        print(v.text().rstrip())
        print(f"  commit: {commit_sha}")
    return EXIT_OK


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except ApplyError as exc:
        print(f"ApplyError: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
