#!/usr/bin/env python3
"""Verify a claude-driven rebase and commit one empty attestation.

Part of the ``claude-driven-rebase-deploy`` feature
(.am/claude-driven-rebase-deploy/design.md §5.6 the nine checks, §5.7 the repo
invariants, §3.6 the attestation commit, §4.7 this CLI). Stage 5 of

    seed      (git rebase --strategy-option=ours, in a throwaway worktree)
      -> classify (ai-overlap-classify.py --mode drive)
      -> drive    (claude -p, holding Edit/Write/git rebase in the scratch worktree)
      -> decide   (ai-overlap-decide.py --mode drive)
      -> VERIFY   (this script)

**What this script exists to replace.** Under approach (a) the AI held no write
tool and every byte it caused to be written already existed in one of four trees
-- a closed algebra the applier could re-prove after the fact. Under (b) the
agent drives a real ``git rebase`` and may write bytes present in no side, so
that proof is forfeited on hand-merged paths. These nine checks are what stands
in its place, and they run **before** the attestation commit and therefore long
before the driver's force-push.

    1  rebase shape            ancestry, no merges, count, clean tree, no rebase
                               in progress, and the author/subject sequence --
                               the check that catches a stage which did NOTHING,
                               which the exit code provably cannot (analyze §515)
    2  containment, two-sided  seed->result diff within overlap.txt (rule 9), and
                               upstream->HEAD diff within fork_changed | overlap
    3  blob binding            every non-keep_auto path equals its declared
                               result_blob; every keep_auto path equals the SEED
    4  closed algebra          where it still applies: take_upstream / take_fork /
                               patch_json_fields, via ai-overlap-apply's own
                               check_provenance(), reused verbatim
    5  conflict markers        WHOLE TREE, with the upstream|fork baseline
                               subtracted (a 7-char setext underline is legal)
    6  JSON validity           every touched *.json
    7  case collision (index)  exit 62
    8  repo invariants         SOURCE_REPO, the seven build-mod.yml literals,
                               the duplicate TTS-object GUID detector (exit 62)
    9  primary checkouts       status digests before/after -- the agent holds
                               Write and its cwd is inside the workspace tree

Checks 4/5/6/7/8 reuse ``ai-overlap-apply.py`` by importing it rather than by
copying: one implementation cannot drift from itself. Check 5 is the exception
and is written here, because under (b) it is promoted from a path-scoped
belt-and-braces scan to a whole-tree primary check with a baseline subtracted.

**The attestation commit** (§3.6) is created last, on the detached HEAD, only
after all nine pass. It changes no path -- so the tag base, the build, the asset
inventory and the 95 % floor are provably unaffected -- and it carries the seed
tree OID, the git version, both source SHAs, the manifest digest and, under
``--attest-max-bytes``, the full unified diff of seed -> result. That diff is
what replaces approach (a)'s "one labelled commit is the AI's whole change"
forensic property, which driving a real rebase destroys.

Usage:
  ai-rebase-verify.py --repo <SCED|SCED-downloads>
                      --worktree DIR --run-dir DIR
                      --merge-base SHA --fork-ref SHA --upstream-ref SHA
                      --seed-tree OID [--seed-commit OID]
                      [--decide FILE] [--manifest FILE] [--classes FILE]
                      [--overlap FILE]
                      [--pre-status FILE]
                      [--run-stamp S] [--model M] [--effort E] [--claude-bin P]
                      [--cli-version V] [--session-id S] [--total-cost-usd F]
                      [--duration-api-ms N] [--num-turns N]
                      [--r2-floor-pct 98] [--attest-max-bytes 65536]
                      [--no-commit] [--quiet]

Exit codes:
   0  every check passed (and, unless --no-commit, the attestation is committed)
   1  usage error
  62  case-only path collision, or a duplicate TTS object GUID, in the resolution
  67  any other check failed
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sced_io import atomic_write_text  # noqa: E402


def _load_apply_module():
    """Import ``ai-overlap-apply.py`` -- the hyphen makes it unimportable by name.

    Design §4.7 requires this script to reuse that module's ``git``, ``chunks``,
    ``check_markers``, ``check_json``, ``check_case``, ``langpack_counts``,
    ``check_invariants``, ``check_provenance``, ``Verifier`` and ``ApplyError``
    **verbatim**. Importing is the strongest available reading of "verbatim":
    a copy would be a second implementation to keep in sync, and the whole point
    of check 4 is that provenance is recomputed by the code that already knows how.
    """
    path = SCRIPT_DIR / "ai-overlap-apply.py"
    spec = importlib.util.spec_from_file_location("ai_overlap_apply", path)
    if spec is None or spec.loader is None:          # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_overlap_apply"] = mod
    spec.loader.exec_module(mod)
    return mod


A = _load_apply_module()

git = A.git
chunks = A.chunks
read_blobs = A.read_blobs
check_json = A.check_json
check_case = A.check_case
check_provenance = A.check_provenance
check_invariants = A.check_invariants
dumps_faithful = A.dumps_faithful
Verifier = A.Verifier
ApplyError = A.ApplyError

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CASE = 62
EXIT_VERIFY = 67

# Design §5.6 check 5. The `|{7}` alternative is present and the applier's
# MARKER_RE does not carry it: `git merge-file --diff3` and `merge.conflictStyle
# = diff3` both emit a `|||||||` base marker, and under (b) a half-edited file
# that got `git add`ed is a live risk rather than a structural impossibility.
MARKER_RE_TREE = r"^(<{7}|\|{7}|>{7}) |^={7}$"

BUILD_MOD_YML = ".github/workflows/build-mod.yml"

# Design §5.7. Each is a literal grep, cheap, present-or-absent, no judgement.
# Every REQUIRED entry is a fork-only property upstream has none of; every one
# would be silently lost by a wrong resolution of the one conflicting hunk in
# this file, or by a careless take_upstream.
BUILD_MOD_REQUIRED = [
    ("i",   "marker guard",            "sced-local-sync: assets-already-attached"),
    ("ii",  "concurrency group",       "'korean-release-assets'"),
    ("iii", "workflow_dispatch tag",   "INPUT_TAG: ${{ inputs.tag }}"),
    ("iv",  "injection fix",           '-modfile="./$filename"'),
]
# Scoped to the -modfile ARGUMENT, never to the string in general: `${{
# env.filename }}` inside a `with:` block is an action input, not a shell script
# body, and a blanket grep would be wrong.
BUILD_MOD_FORBIDDEN = [
    ("v", "no template expansion inside a run: body",
     '-modfile="./${{ env.filename }}"'),
]
# The two consistency literals the auto-merged region makes necessary. Upstream
# deleted the wget download step and the chmod of the Linux binary; a tree that
# still names TTSModManager-Linux is the "runs a binary it never fetched" failure,
# which is syntactically valid YAML and would not be noticed until a release
# built nothing.
BUILD_MOD_COUNTED = [
    ("vi",  ".vscode/bin/TTSModManager", 2),
    ("vii", "TTSModManager-Linux",       0),
    ("vii", "wget",                      0),
]

# Two top-level objects/*.json ending in the same 6-hex GUID are the same TTS
# object present twice. The driver's post-push case detector compares CASE-FOLDED
# paths and therefore cannot see `InvestigatorTokens.0203af.json` next to
# `InvestigatorTokens1.0203af.json` -- they differ by more than case.
OBJECT_GUID_RE = re.compile(r"^objects/[^/]*\.([0-9a-f]{6})\.json$")

ATTEST_SUBJECT_PREFIX = "chore(sync): claude-driven rebase attestation"


# --------------------------------------------------------------------- helpers
def _lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln]


def _detail_of(inner: Verifier) -> list[str]:
    """The detail lines of a single-record Verifier, unindented."""
    return [ln.strip() for ln in inner.lines[1:]]


def _fold(inner: Verifier, n: int, v: Verifier, name: str | None = None) -> None:
    """Re-record an imported check under **this** design's numbering.

    ``ai-overlap-apply.py``'s helpers hardcode approach (a)'s indices -- markers
    2, JSON 3, provenance 4, case 5, invariants 6. Under (b) §5.6 re-ranks the
    order by what actually protects, so each imported check runs into a private
    Verifier and is folded back in here under its (b) number. The alternative --
    editing the numbers in the applier -- would change the (a) path, which has to
    stay byte-for-byte alive for SCED-downloads (§5.11).
    """
    header = inner.lines[0] if inner.lines else ""
    label = name or re.sub(r"^\[(?:PASS|FAIL)\] check \d+ ", "", header)
    v.record(n, label, not inner.failed, _detail_of(inner))


def _read_lines(path: Path | None) -> list[str]:
    if not path or not path.is_file():
        return []
    return _lines(path.read_text(encoding="utf-8"))


def rev_parse(wt: Path, spec: str) -> str | None:
    rc, out, _err = git(wt, "rev-parse", "--verify", "-q", spec, check=False)
    return out.strip() if rc == 0 and out.strip() else None


def status_digest(repo_path: Path) -> str:
    """`git status --porcelain | shasum -a 256` for one primary checkout.

    Digests, not emptiness: SCED-tools is routinely dirty, and the property the
    check defends is "nothing MOVED while the agent held Write", not "clean".
    """
    res = subprocess.run(
        ["git", "-C", str(repo_path),
         "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0",
         "-c", "core.quotePath=false", "status", "--porcelain"],
        capture_output=True)
    if res.returncode != 0:
        return "ERROR:" + res.stderr.decode(errors="replace").strip()[:120]
    return hashlib.sha256(res.stdout).hexdigest()


def parse_status_file(path: Path) -> dict[str, str]:
    """Read `<digest>  <abs-path>` lines written by resolve-rebase-with-ai.sh."""
    out: dict[str, str] = {}
    for ln in _read_lines(path):
        parts = ln.split(None, 1)
        if len(parts) == 2:
            out[parts[1].strip()] = parts[0].strip()
    return out


# ---------------------------------------------------------------------- checks
def check_rebase_shape(wt: Path, upstream_sha: str, merge_base: str, fork_sha: str,
                       commits_expected: int | None, dropped: list[dict],
                       v: Verifier) -> dict:
    """Design §5.6 check 1. The check (a) never needed, because (a) drove the rebase.

    The author/subject sequence comparison is the load-bearing half: it catches a
    squash, a reorder, an authorship rewrite, a merge disguised as a rebase, an
    undeclared drop, and -- most importantly -- **a stage that did nothing**,
    which `--permission-mode dontAsk` makes invisible to the exit code because it
    denies silently and still exits 0.
    """
    detail: list[str] = []
    ok = True

    rc, _out, _err = git(wt, "merge-base", "--is-ancestor", upstream_sha, "HEAD",
                         check=False)
    if rc != 0:
        detail.append(f"HEAD is not a descendant of {upstream_sha[:12]} "
                      f"(merge-base --is-ancestor rc={rc})")
        ok = False

    _rc, merges, _e = git(wt, "rev-list", "--merges", f"{upstream_sha}..HEAD")
    if merges.strip():
        detail.append(f"{len(_lines(merges))} merge commit(s) in "
                      f"{upstream_sha[:12]}..HEAD -- a rebase produces none")
        ok = False

    _rc, out, _e = git(wt, "rev-list", "--count", f"{upstream_sha}..HEAD")
    observed_count = int(out.strip() or 0)

    _rc, porcelain, _e = git(wt, "status", "--porcelain")
    if porcelain.strip():
        detail.append(f"worktree is dirty: {_lines(porcelain)[:5]}")
        ok = False

    # NOT fatal, deliberately -- this deviates from design §5.6 check 1's literal
    # `rev-parse -q --verify REBASE_HEAD  # must be empty`, and it has to.
    # Measured on git 2.53.0: a rebase that hit a conflict and was carried to
    # completion with `--continue` LEAVES REBASE_HEAD behind, while correctly
    # removing rebase-merge/. So REBASE_HEAD is a "the last rebase conflicted"
    # residue, not a "a rebase is in progress" signal -- and it is present on
    # exactly the nights the stage did its job. Treating it as fatal would exit 67
    # on every successful hand-merge, i.e. on the whole reason approach (b) exists.
    # The authoritative test is the state directory below, plus the clean-tree
    # assertion above.
    stale_rebase_head = rev_parse(wt, "REBASE_HEAD")
    if stale_rebase_head:
        detail.append(f"(note) REBASE_HEAD -> {stale_rebase_head[:12]} is a leftover "
                      f"from a conflicted-then-continued rebase; not a rebase in "
                      f"progress (rebase-merge/ is absent)")
    for name in ("rebase-merge", "rebase-apply"):
        _rc, p, _e = git(wt, "rev-parse", "--git-path", name)
        state = Path(p.strip())
        if not state.is_absolute():
            state = wt / state
        if state.exists():
            detail.append(f"{name} exists at {state} -- a rebase is still in progress")
            ok = False

    # ---- author/subject sequence
    _rc, obs_raw, _e = git(wt, "log", "--format=%an|%ae|%s", f"{upstream_sha}..HEAD")
    observed = _lines(obs_raw)
    # A --replay run verifies a tree whose tip is already an attestation. Those
    # commits are empty and this script created them, so they are stripped rather
    # than counted against the sequence.
    attest_seen = 0
    while observed and observed[0].split("|", 2)[-1].startswith(ATTEST_SUBJECT_PREFIX):
        observed.pop(0)
        attest_seen += 1
    if attest_seen:
        detail.append(f"(note) {attest_seen} pre-existing attestation commit(s) "
                      f"excluded from the sequence and the count")
        observed_count -= attest_seen

    _rc, exp_raw, _e = git(wt, "log", "--format=%an|%ae|%s", f"{merge_base}..{fork_sha}")
    expected = _lines(exp_raw)
    for d in dropped:
        subject = (d.get("subject") or "").strip()
        hit = next((i for i, e in enumerate(expected)
                    if e.split("|", 2)[-1] == subject), None)
        if hit is None:
            detail.append(f"commits_dropped declares {subject!r}, which is not a "
                          f"subject in {merge_base[:12]}..{fork_sha[:12]}")
            ok = False
        else:
            expected.pop(hit)

    if observed != expected:
        ok = False
        detail.append(f"author/subject sequence differs: {len(observed)} replayed vs "
                      f"{len(expected)} expected after {len(dropped)} declared drop(s)")
        for line in [f"  - only in HEAD:     {x}" for x in observed
                     if x not in expected][:10]:
            detail.append(line)
        for line in [f"  - only in the fork: {x}" for x in expected
                     if x not in observed][:10]:
            detail.append(line)

    if observed_count < 1:
        detail.append("0 commits replayed -- the fork's entire divergence vanished")
        ok = False
    if commits_expected is not None and observed_count > commits_expected:
        detail.append(f"{observed_count} commits replayed but only {commits_expected} "
                      f"were expected")
        ok = False

    detail.append(f"replayed {observed_count} commit(s) of {commits_expected} expected, "
                  f"{len(dropped)} declared drop(s)")
    v.record(1, "rebase shape", ok, detail)
    return {"commits_replayed": observed_count}


def check_containment(wt: Path, seed_tree: str, upstream_sha: str, overlap: list[str],
                      fork_changed: list[str] | None, by_path: dict[str, dict],
                      v: Verifier) -> list[str]:
    """Design §5.6 check 2, both sides. Returns the seed->result path list.

    Half one is rule 9 observed on the tree: the seed is a deterministic
    reference, so this diff is *exactly* the bytes claude decided differently,
    and every one of them must be a path the gate already flagged.

    Half two is the blast-radius bound (a) got for free from having no write
    tools: a change smuggled into a path neither side had touched appears here
    and nowhere else.
    """
    detail: list[str] = []
    ok = True
    overlap_set = set(overlap)

    _rc, out, _e = git(wt, "diff", "--name-only", seed_tree, "HEAD^{tree}")
    seed_diff = _lines(out)
    outside = [p for p in seed_diff if p not in overlap_set]
    if outside:
        ok = False
        detail += [f"seed->result changed {p}, which is not in overlap.txt"
                   for p in outside[:20]]
    for p in seed_diff:
        if p not in overlap_set:
            continue
        ruling = by_path.get(p)
        if ruling is None:
            ok = False
            detail.append(f"seed->result changed {p} with no effective verdict")
        elif ruling.get("verdict") == "keep_auto":
            ok = False
            detail.append(f"{p}: declared keep_auto but its bytes differ from the seed")

    if fork_changed is None:
        ok = False
        detail.append("fork_changed is absent from classes.json; the upstream->HEAD "
                      "containment half cannot run (pass --fork-paths to the classifier)")
    else:
        allowed = set(fork_changed) | overlap_set
        _rc, out2, _e = git(wt, "diff", "--no-renames", "--name-only", upstream_sha, "HEAD")
        tree_diff = _lines(out2)
        smuggled = [p for p in tree_diff if p not in allowed]
        if smuggled:
            ok = False
            detail += [f"HEAD differs from upstream at {p}, which neither the fork "
                       f"nor the overlap set touched" for p in smuggled[:20]]
        detail.append(f"upstream->HEAD touches {len(tree_diff)} path(s), all within "
                      f"fork_changed({len(fork_changed)}) | overlap({len(overlap_set)})"
                      if not smuggled else
                      f"upstream->HEAD touches {len(tree_diff)} path(s), "
                      f"{len(smuggled)} outside the allowed set")

    detail.append(f"seed->result differs at {len(seed_diff)} path(s): {seed_diff[:10]}")
    v.record(2, "containment (two-sided)", ok, detail)
    return seed_diff


def check_blob_binding(wt: Path, seed_tree: str, effective: list[dict],
                       v: Verifier) -> None:
    """Design §5.6 check 3. The machine-checkable core of the attestation.

    A `keep_auto` ruling asserts "these bytes equal the deterministic reference's
    bytes" and is verified against the seed tree, which is what re-founds decide
    rule 4 on a check instead of on trust. Everything else names its blob, and
    the shell recomputes it.
    """
    detail: list[str] = []
    ok = True
    checked = 0
    for e in effective:
        p, verdict = e["path"], e.get("verdict")
        if verdict == "abstain":          # decide rule 2 already stopped the run
            continue
        head_oid = rev_parse(wt, f"HEAD:{p}")
        if verdict == "keep_auto":
            seed_oid = rev_parse(wt, f"{seed_tree}:{p}")
            checked += 1
            if head_oid != seed_oid:
                ok = False
                detail.append(f"{p}: keep_auto but HEAD:{p}={head_oid or 'absent'} != "
                              f"seed:{p}={seed_oid or 'absent'}")
            continue
        want = e.get("result_blob")
        checked += 1
        if not want:
            ok = False
            detail.append(f"{p}: {verdict} without a result_blob -- nothing to bind to")
        elif head_oid is None:
            ok = False
            detail.append(f"{p}: {verdict} declares result_blob {want[:12]} but the "
                          f"path does not exist at HEAD")
        elif head_oid != want:
            ok = False
            detail.append(f"{p}: HEAD blob {head_oid[:12]} != declared result_blob "
                          f"{want[:12]}")
    detail.append(f"{checked} path(s) bound")
    v.record(3, "blob binding", ok, detail)


def check_markers_tree(wt: Path, upstream_sha: str, fork_sha: str, v: Verifier) -> None:
    """Design §5.6 check 5, promoted to primary and widened to the whole tree.

    `^={7}$` legitimately matches a 7-character Markdown setext underline, so the
    scan subtracts a baseline: the same grep against upstream and fork, keyed on
    (path, line text) because line numbers shift under a rebase. Both baselines
    were empty on SCED when this was designed, so any hit today is a true
    positive -- but the subtraction is what keeps that true after upstream lands
    a README with an underline in it.

    `git grep` exit 1 (no match) is the pass, 0 is a hit, anything else is an error.
    """
    def scan(rev: str | None) -> set[tuple[str, str]]:
        args = ["grep", "-I", "-n", "-E", MARKER_RE_TREE]
        if rev:
            args.append(rev)
        args += ["--", "."]
        rc, out, err = git(wt, *args, check=False)
        if rc == 1:
            return set()
        if rc != 0:
            raise ApplyError(EXIT_VERIFY,
                             f"git grep for conflict markers failed at "
                             f"{rev or 'the working tree'} (rc={rc}): {err.strip()}")
        hits: set[tuple[str, str]] = set()
        for ln in _lines(out):
            body = ln.split(":", 1)[1] if rev else ln          # strip `rev:`
            parts = body.split(":", 2)                          # path:lineno:text
            if len(parts) == 3:
                hits.add((parts[0], parts[2]))
        return hits

    head_hits = scan(None)
    baseline = scan(upstream_sha) | scan(fork_sha)
    new = sorted(head_hits - baseline)
    detail = [f"{p}: {text[:120]}" for p, text in new[:40]]
    detail.append(f"{len(head_hits)} raw hit(s), {len(baseline)} in the "
                  f"upstream|fork baseline, {len(new)} new")
    v.record(5, "conflict-marker scan (whole tree)", not new, detail)


def check_build_mod_literals(wt: Path, detail: list[str]) -> bool:
    """Design §5.7's seven literals. SCED only, and only if the file is present."""
    f = wt / BUILD_MOD_YML
    if not f.is_file():
        detail.append(f"{BUILD_MOD_YML}: absent -- the fork's build workflow is gone")
        return False
    try:
        text = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        detail.append(f"{BUILD_MOD_YML}: unreadable ({exc})")
        return False
    ok = True
    for num, name, literal in BUILD_MOD_REQUIRED:
        n = text.count(literal)
        if n < 1:
            ok = False
            detail.append(f"{BUILD_MOD_YML} ({num}) {name}: REQUIRED literal absent: "
                          f"{literal!r}")
        else:
            detail.append(f"{BUILD_MOD_YML} ({num}) {name}: present ({n})")
    for num, name, literal in BUILD_MOD_FORBIDDEN:
        n = text.count(literal)
        if n:
            ok = False
            detail.append(f"{BUILD_MOD_YML} ({num}) {name}: FORBIDDEN literal present "
                          f"{n} time(s): {literal!r} -- this re-opens the script "
                          f"injection the fork closed")
        else:
            detail.append(f"{BUILD_MOD_YML} ({num}) {name}: absent, as required")
    for num, literal, want in BUILD_MOD_COUNTED:
        n = text.count(literal)
        if n != want:
            ok = False
            detail.append(f"{BUILD_MOD_YML} ({num}) {literal!r}: {n} occurrence(s), "
                          f"expected {want}")
        else:
            detail.append(f"{BUILD_MOD_YML} ({num}) {literal!r}: {n}, as required")
    return ok


def check_duplicate_object_guid(wt: Path, detail: list[str]) -> list[str]:
    """Design §5.7. Two top-level objects/*.json with the same 6-hex GUID."""
    _rc, out, _e = git(wt, "ls-tree", "--name-only", "HEAD", "objects/")
    seen: dict[str, list[str]] = {}
    for name in _lines(out):
        m = OBJECT_GUID_RE.match(name)
        if m:
            seen.setdefault(m.group(1), []).append(name)
    dupes = [g for g, names in sorted(seen.items()) if len(names) > 1]
    for g in dupes:
        detail.append(f"objects/: GUID {g} appears {len(seen[g])} times: {seen[g]} "
                      f"-- the same TTS object would be present twice")
    if not dupes:
        detail.append(f"objects/: {len(seen)} top-level GUID(s), none duplicated")
    return dupes


def check_invariants_extended(wt: Path, repo: str, fork_sha: str, upstream_sha: str,
                              floor_pct: int, v: Verifier) -> dict:
    """Design §5.6 check 8 = §5.7.

    ``ai-overlap-apply.check_invariants`` is *extended, not replaced*: it is run
    verbatim into a private Verifier (where it records as its own check 6) and
    its verdict, detail and stats are folded into check 8 here alongside the
    build-mod.yml literals and the duplicate-GUID detector.
    """
    inner = Verifier()
    # `pre_sha` only feeds SCED-downloads' pre-apply langpack counts, which are
    # informational; under (b) there is no pre-apply tree, so HEAD stands in.
    stats = check_invariants(wt, repo, fork_sha, upstream_sha, "HEAD", floor_pct, inner)
    detail = _detail_of(inner)
    ok = not inner.failed

    if repo == "SCED":
        if not check_build_mod_literals(wt, detail):
            ok = False
        dupes = check_duplicate_object_guid(wt, detail)
    else:
        dupes = []

    # Recorded as a FAILURE before it is raised, mirroring check_case()
    # (ai-overlap-apply.py:365-369): verify.log is the forensic record, and a
    # log that says PASS next to a fatal detail line is a log that will be
    # misread at 03:00.
    v.record(8, f"repo invariants ({repo})", ok and not dupes, detail)
    if dupes:
        raise ApplyError(EXIT_CASE,
                         f"duplicate TTS object GUID introduced by the resolution: "
                         f"{dupes[:5]}")
    return stats


def check_primary_checkouts(pre: dict[str, str], v: Verifier) -> dict:
    """Design §5.6 check 9. New under (b), and specific to it.

    The agent holds Write and its cwd is inside the workspace tree, so "the
    driver never touches the primary checkouts" stops being a structural property
    of the shell and becomes a claim that has to be measured. It costs
    milliseconds and it is the only check that looks outside the worktree.
    """
    detail: list[str] = []
    ok = True
    after: dict[str, str] = {}
    if not pre:
        ok = False
        detail.append("no pre-run status digests were supplied (--pre-status); "
                      "the primary checkouts cannot be shown to be unchanged")
    for path, before in sorted(pre.items()):
        now = status_digest(Path(path))
        after[path] = now
        if now != before:
            ok = False
            detail.append(f"{path}: git status digest changed {before[:12]} -> "
                          f"{now[:12]} while the agent held Write")
        else:
            detail.append(f"{path}: unchanged ({before[:12]})")
    v.record(9, "primary-checkout containment", ok, detail)
    return after


# --------------------------------------------------------------- attestation
def build_attestation(args, wt: Path, run_dir: Path, classes_doc: dict,
                      decide: dict, seed_tree: str, result_tree: str,
                      counts: dict) -> str:
    """Design §3.6. The message, including the seed->result diff when it fits."""
    stamp = args.run_stamp or time.strftime("%Y%m%d-%H%M%S")
    _rc, stat, _e = git(wt, "diff", "--stat", seed_tree, result_tree)
    _rc, patch, _e = git(wt, "diff", seed_tree, result_tree)
    atomic_write_text(run_dir / "seed-diff.patch", patch)
    atomic_write_text(run_dir / "seed-diff.stat", stat)

    verdicts = decide.get("verdicts", {})
    confidence = decide.get("confidence", {})
    dropped = decide.get("commits_dropped") or []
    git_version = classes_doc.get("git_version", "")
    seed_cmd = (f"`git rebase --strategy-option=ours --empty=drop "
                f"{args.upstream_ref}`")

    head = [
        f"{ATTEST_SUBJECT_PREFIX} {stamp}",
        "",
        f"repo:    {args.repo}",
        f"overlap: {decide.get('overlap_count')} path(s), "
        f"sha256 {(decide.get('overlap_sha256') or '')[:12]}",
        f"base:    merge-base {args.merge_base}",
        f"fork:    {args.fork_ref}",
        f"upstream:{args.upstream_ref}",
        f"seed:    tree {seed_tree} from {seed_cmd} (git {git_version})",
        f"result:  tree {result_tree}",
        f"manifest: sha256 {(decide.get('manifest_sha256') or '')[:12]}   "
        f"model: {args.model}   effort: {args.effort}",
        f"session: {args.session_id or decide.get('session_id', '')}      "
        f"cost: ${decide.get('total_cost_usd')}            "
        f"turns: {decide.get('num_turns')}",
        f"commits: {counts.get('commits_replayed')} replayed of "
        f"{classes_doc.get('commits_expected')} expected, {len(dropped)} dropped",
        "verdicts: " + " ".join(
            f"{k}={verdicts.get(k, 0)}" for k in
            ("keep_auto", "take_upstream", "take_fork", "patch_json_fields",
             "hand_merge")),
        "confidence: " + " ".join(
            f"{k}={confidence.get(k, 0)}" for k in ("high", "medium", "low")),
    ]
    for d in dropped:
        head.append(f"dropped: {d.get('subject')} -- {d.get('reason')}")
    head += ["", "--- bytes decided differently from the deterministic seed ---",
             stat.rstrip("\n") or " (none)"]
    if len(patch.encode("utf-8")) <= args.attest_max_bytes:
        head += ["", patch.rstrip("\n")]
    else:
        head += ["", f"truncated: the seed->result diff is "
                     f"{len(patch.encode('utf-8'))} bytes, over the "
                     f"{args.attest_max_bytes}-byte budget; regenerate the seed "
                     f"from fork/upstream above (git {git_version}) and re-diff "
                     f"against tree {result_tree}."]
    return "\n".join(head) + "\n"


# ----------------------------------------------------------------------- main
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, choices=["SCED", "SCED-downloads"])
    p.add_argument("--worktree", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--merge-base", required=True)
    p.add_argument("--fork-ref", required=True)
    p.add_argument("--upstream-ref", required=True)
    p.add_argument("--seed-tree", required=True)
    p.add_argument("--seed-commit", default="")
    p.add_argument("--decide", type=Path, help="default: RUN_DIR/decide.json")
    p.add_argument("--manifest", type=Path, help="default: RUN_DIR/manifest.json")
    p.add_argument("--classes", type=Path, help="default: RUN_DIR/classes.json")
    p.add_argument("--overlap", type=Path, help="default: RUN_DIR/overlap.txt")
    p.add_argument("--pre-status", type=Path,
                   help="`<sha256>  <abs-path>` per primary checkout, written "
                        "before claude ran. Deliberately NOT inside RUN_DIR: the "
                        "agent holds --add-dir on that directory.")
    p.add_argument("--run-stamp", default="")
    p.add_argument("--model", default="")
    p.add_argument("--effort", default="")
    p.add_argument("--claude-bin", default="")
    p.add_argument("--cli-version", default="")
    p.add_argument("--session-id", default="")
    p.add_argument("--total-cost-usd", type=float, default=None)
    p.add_argument("--duration-api-ms", type=int, default=None)
    p.add_argument("--num-turns", type=int, default=None)
    p.add_argument("--r2-floor-pct", type=int, default=98)
    p.add_argument("--attest-max-bytes", type=int, default=65536)
    p.add_argument("--no-commit", action="store_true",
                   help="run the nine checks and write every artifact, but create "
                        "no attestation commit (rehearsal).")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def run(args) -> int:  # noqa: C901 - one linear pipeline, kept in reading order
    wt: Path = args.worktree
    run_dir: Path = args.run_dir

    # Re-asserted independently of the caller (design §7.1): every script that can
    # write in this pipeline states the boundary itself, so none of them is
    # trusting another to have done it.
    if "/.local-sync/scratch/" not in f"{wt.resolve()}/":
        print(f"refusing to operate outside a scratch worktree: {wt}", file=sys.stderr)
        return EXIT_USAGE
    if not run_dir.is_dir():
        print(f"run dir does not exist: {run_dir}", file=sys.stderr)
        return EXIT_USAGE

    decide_path = args.decide or run_dir / "decide.json"
    classes_path = args.classes or run_dir / "classes.json"
    overlap_path = args.overlap or run_dir / "overlap.txt"
    for f in (decide_path, classes_path, overlap_path):
        if not f.is_file():
            print(f"missing input: {f}", file=sys.stderr)
            return EXIT_USAGE

    decide = json.loads(decide_path.read_text(encoding="utf-8"))
    classes_doc = json.loads(classes_path.read_text(encoding="utf-8"))
    overlap = _read_lines(overlap_path)

    if decide.get("outcome") != "proceed":
        raise ApplyError(EXIT_VERIFY, f"decide.json outcome is "
                                      f"{decide.get('outcome')!r}, refusing to verify "
                                      f"a tree the rules already stopped")
    if decide.get("mode") != "drive":
        raise ApplyError(EXIT_VERIFY, f"decide.json mode is {decide.get('mode')!r}; "
                                      f"this verifier only understands 'drive'")

    seed_tree = args.seed_tree
    if rev_parse(wt, f"{seed_tree}^{{tree}}") is None:
        raise ApplyError(EXIT_VERIFY, f"seed tree {seed_tree} is not present in "
                                      f"{wt} -- the shadow seed's objects must be "
                                      f"reachable from the scratch worktree")
    declared_seed = classes_doc.get("seed_tree")
    if declared_seed and declared_seed != seed_tree:
        raise ApplyError(EXIT_VERIFY, f"--seed-tree {seed_tree[:12]} disagrees with "
                                      f"classes.json {declared_seed[:12]}")

    effective = decide.get("effective") or []
    by_path = {e["path"]: e for e in effective}
    dropped = decide.get("commits_dropped") or []
    commits_expected = classes_doc.get("commits_expected")
    fork_changed = classes_doc.get("fork_changed")
    pre_status = parse_status_file(args.pre_status) if args.pre_status else {}

    v = Verifier()
    counts = check_rebase_shape(wt, args.upstream_ref, args.merge_base, args.fork_ref,
                                commits_expected, dropped, v)
    seed_diff = check_containment(wt, seed_tree, args.upstream_ref, overlap,
                                  fork_changed, by_path, v)
    check_blob_binding(wt, seed_tree, effective, v)

    # Check 4 -- the closed algebra, where it still applies. `R` is the SEED tree:
    # under (b) the deterministic auto-merge is the reference, and it is what
    # `patch_json_fields` patches. `hand_merge` is deliberately absent from the
    # plan: it is the one verb that escapes the algebra, bounded by decide rule 8
    # (conflicting paths only) and pinned by check 3 instead.
    algebra = [e for e in effective
               if e.get("verdict") in ("take_upstream", "take_fork", "patch_json_fields")]
    if algebra:
        algebra_paths = [e["path"] for e in algebra]
        blobs = {
            "B": read_blobs(wt, args.merge_base, algebra_paths),
            "U": read_blobs(wt, args.upstream_ref, algebra_paths),
            "F": read_blobs(wt, args.fork_ref, algebra_paths),
            "R": read_blobs(wt, seed_tree, algebra_paths),
        }
        inner = Verifier()
        check_provenance(wt, algebra, blobs, inner)
        _fold(inner, 4, v)
    else:
        v.record(4, "closed algebra (0 applicable path(s))", True,
                 ["no take_upstream / take_fork / patch_json_fields ruling in this run"])

    check_markers_tree(wt, args.upstream_ref, args.fork_ref, v)

    touched = [e["path"] for e in effective if e.get("verdict") != "keep_auto"]
    inner = Verifier()
    check_json(wt, args.repo, touched, inner)
    _fold(inner, 6, v)

    post_status: dict[str, str] = {}
    try:
        inner = Verifier()
        try:
            check_case(wt, inner)               # raises EXIT_CASE on a collision
        finally:
            _fold(inner, 7, v)
        stats = check_invariants_extended(wt, args.repo, args.fork_ref,
                                          args.upstream_ref, args.r2_floor_pct, v)
        post_status = check_primary_checkouts(pre_status, v)
    finally:
        atomic_write_text(run_dir / "verify.log", v.text())
        if pre_status:
            atomic_write_text(run_dir / "preflight-status.sha256", "".join(
                [f"before {d}  {p}\n" for p, d in sorted(pre_status.items())]
                + [f"after  {d}  {p}\n" for p, d in sorted(post_status.items())]))

    if v.failed:
        raise ApplyError(EXIT_VERIFY,
                         "post-rebase verification failed: " + ", ".join(v.failed))

    _rc, out, _e = git(wt, "rev-parse", "HEAD^{tree}")
    result_tree = out.strip()
    declared_result = decide.get("result_tree")
    if declared_result and declared_result != result_tree:
        raise ApplyError(EXIT_VERIFY,
                         f"the manifest declares result_tree {declared_result[:12]} but "
                         f"HEAD's tree is {result_tree[:12]}")

    # ---- attestation, last, and only once every check has passed
    message = build_attestation(args, wt, run_dir, classes_doc, decide,
                                seed_tree, result_tree, counts)
    atomic_write_text(run_dir / "attest.txt", message)
    attest_commit = ""
    if not args.no_commit:
        git(wt,
            "-c", "user.name=SCED daily sync",
            "-c", "user.email=sced-daily-sync@localhost",
            "commit", "--allow-empty", "--no-verify", "--cleanup=verbatim",
            "-F", "-", stdin=message.encode("utf-8"))
        _rc, out, _e = git(wt, "rev-parse", "HEAD")
        attest_commit = out.strip()
        _rc, out, _e = git(wt, "rev-parse", "HEAD^{tree}")
        if out.strip() != result_tree:
            raise ApplyError(EXIT_VERIFY,
                             "the attestation commit changed the tree; it must be empty")

    result = {
        "enabled": True,
        "mode": "drive",
        "outcome": "proceed",
        "reason": "",
        "binary": args.claude_bin,
        "cli_version": args.cli_version,
        "model": args.model,
        "effort": args.effort,
        "session_id": args.session_id or decide.get("session_id", ""),
        "total_cost_usd": (args.total_cost_usd if args.total_cost_usd is not None
                           else decide.get("total_cost_usd")),
        "duration_api_ms": (args.duration_api_ms if args.duration_api_ms is not None
                            else decide.get("duration_api_ms")),
        "num_turns": (args.num_turns if args.num_turns is not None
                      else decide.get("num_turns")),
        "permission_denials": decide.get("permission_denials") or [],
        "git_version": classes_doc.get("git_version", ""),
        "overlap_sha256": decide.get("overlap_sha256", ""),
        "manifest_sha256": decide.get("manifest_sha256", ""),
        "seed_commit": args.seed_commit or classes_doc.get("seed_commit", ""),
        "seed_tree": seed_tree,
        "result_tree": result_tree,
        "attest_commit": attest_commit,
        "commits_expected": commits_expected,
        "commits_replayed": counts.get("commits_replayed"),
        "commits_dropped": dropped,
        "conflicts_declared": decide.get("conflicts_declared"),
        "conflicts_oracle": decide.get("conflicts_oracle"),
        "seed_diff_paths": seed_diff[:200],
        "verdicts": decide.get("verdicts", {}),
        "confidence": decide.get("confidence", {}),
        "hand_merge_paths": decide.get("hand_merge_paths", []),
        "invariants": stats,
        "artifact_dir": str(run_dir),
    }
    atomic_write_text(run_dir / "result.json", dumps_faithful(result) + "\n")

    if not args.quiet:
        print(v.text().rstrip())
        print(f"  seed->result: {len(seed_diff)} path(s)")
        print(f"  attestation:  {attest_commit or '(none: --no-commit)'}")
    return EXIT_OK


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except ApplyError as exc:
        print(f"VerifyError: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
