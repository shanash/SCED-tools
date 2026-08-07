#!/usr/bin/env python3
"""Classify a rebase overlap set by blob provenance and build the prompt bundle.

Part of the ``ai-rebase-conflict-resolution`` feature
(.am/ai-rebase-conflict-resolution/design.md §3.2, §5.4, §6 S2/S3). Stage 1 of

    CLASSIFY (this script)
      -> adjudicate (claude -p --output-format json)
      -> decide (ai-overlap-decide.py)
      -> apply (ai-overlap-apply.py)

Everything this script emits is deterministic. It runs *after* a seeded rebase,
so for every path in the gate's overlap set it can compare four blobs:

    B = <merge-base>:p    what both sides started from
    U = <upstream>:p      upstream's content
    F = <fork>:p          the fork's content before the rebase
    R = <result-ref>:p    the post-rebase result

``--mode`` selects which pipeline is being served, and the difference is entirely
in what ``R`` *means*:

  audit (default, approach (a), .am/ai-rebase-conflict-resolution)
      the shell has already run the seeded rebase in the worktree that will ship,
      so ``R`` is HEAD and R IS the bytes that would ship unless the audit
      overrides them.

  drive (approach (b), .am/claude-driven-rebase-deploy)
      the shell ran the seeded rebase in a SHADOW worktree that is thrown away,
      and claude drives the real rebase afterwards. ``R`` is therefore the shadow
      seed's tree -- a deterministic REFERENCE, not the shipped bytes. This is
      what makes ``C3.blend`` enumerable *before* the agent touches anything, and
      it is the only reason approach (b) can make a coverage argument at all
      (design §5.2, §5.4).

In both modes the class computation, the four-blob comparison and the coverage
set are identical. Only the provenance of ``R`` and the prompt template differ.

The class ``C3.blend`` (``R`` is none of ``B``/``U``/``F``) is the set git merged
silently. Measured 2026-06-27 on SCED-downloads: 142 files touched by both sides,
git flagged 18, silently merged 124, and the 124 were the broken ones. Naming that
class rather than inferring it is the point of this stage.

``seed_decided`` is the true "git would have conflicted" set, computed offline with
``git merge-file -p --diff3`` on the three extracted blobs -- no second rebase, no
worktree mutation. It is recorded as context, never as the predicate. Under
``--mode drive`` the same value is ALSO surfaced under its own key
``conflict_oracle``, because there it is one of three conflict signals rather than
the only one (design §5.5 rule 5): the deterministic oracle, claude's own
``git_conflicted`` declaration, and membership in the seed->result diff. The union
is what must be ruled ``high``.

Outputs, all under ``--run-dir`` (design §3.5):

    overlap.txt           byte copy of the gate's --paths-out
    classes.json          the classification (design §3.2)
    material/<n>/         base, upstream, fork, merged, merge-file.diff3, index.json
    material/index.json   material dir -> path
    policy.md             workspace policy, also fed to --append-system-prompt
    precedent.md          the 2026-06-27 R2 precedent, verbatim
    prompt.md             the mode's template, rendered
    manifest.schema.json  emitted by ai-overlap-decide.py --emit-schema (one owner)

Usage:
  ai-overlap-classify.py --repo <SCED|SCED-downloads>
                         --worktree DIR
                         --merge-base SHA --fork-ref SHA --upstream-ref SHA
                         --overlap FILE --run-dir DIR
                         [--mode audit|drive]
                         [--result-ref HEAD]
                         [--fork-paths FILE] [--upstream-paths FILE]
                         [--seed-commit SHA] [--commits-expected N]
                         [--prompt-template FILE]
                         [--sample-per-class 5] [--material-all-max 64]
                         [--material-max-bytes 1000000]
                         [--inline-budget-bytes 200000]

Exit codes:
   0  OK
   2  bad input (missing repo/worktree/overlap file, unusable ref)
  64  classification failed: a C4.base path (both changes lost), an unreadable
      blob, or commits_replayed == 0 (the fork's entire divergence vanished)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from sced_io import atomic_write_text

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_CLASSIFY = 64

# Kept in sync with ai-overlap-decide.py's literal of the same name; the prompt
# and the rule that enforces it must never state different numbers.
DEFAULT_MAX_HAND_MERGE = 5

SCRIPT_DIR = Path(__file__).resolve().parent
# One template per mode. `audit` keeps the (a) prompt byte-for-byte so that path
# stays alive and tested for SCED-downloads (design §5.11).
TEMPLATE_FOR_MODE = {
    "audit": SCRIPT_DIR / "ai-overlap-prompt.md",
    "drive": SCRIPT_DIR / "ai-rebase-prompt.md",
}
DEFAULT_TEMPLATE = TEMPLATE_FOR_MODE["audit"]
DECIDER = SCRIPT_DIR / "ai-overlap-decide.py"

LANGPACK_PREFIX = "decomposed/language-pack/"
R2_HOST_SUFFIX = ".r2.dev"

CLASS_MEANING = {
    "C0.identical": "U == F",
    "C1.fork_wins": "R == F, F != U",
    "C2.upstream_wins": "R == U, F != U -- the fork's change was dropped",
    "C3.blend": "R not in {B,U,F} -- git three-way merged silently",
    "C4.base": "R == B -- both changes lost; HARD ERROR",
    "C8.tree_conflict": "add/delete/rename or case-sibling",
}

SHAPE_MEANING = {
    "L-a.image-identity-only": "fork moved only the image identity; upstream did not "
                               "contest it (or the fork's is R2-hosted and upstream's "
                               "is not)",
    "L-b.image-contested": "both sides moved the image identity and the host rule does "
                           "not separate them",
    "L-c.mixed": "the fork changed something outside the image identity",
}

SHAPE_PROPOSED = {
    "L-a.image-identity-only": "patch_json_fields /CardID + /CustomDeck from: fork",
    "L-b.image-contested": "abstain",
    "L-c.mixed": "per-path adjudication",
}


# --------------------------------------------------------------------- helpers
def die(code: int, msg: str) -> None:
    print(f"ClassifyError: {msg}", file=sys.stderr)
    sys.exit(code)


def dumps_faithful(data) -> str:
    """2-space indent, ensure_ascii=False, NO trailing newline (house format,
    apply-revert-decisions.py:90-93)."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def git(worktree: Path, *args: str, check: bool = True) -> str:
    """Read-only git, with the driver's hardening flags (daily-sync-local.sh:200-219).

    core.ignorecase is deliberately NOT forced: on this case-insensitive APFS
    volume forcing it false manufactures phantom changes. Case exposure is caught
    by the dedicated collision check instead.
    """
    cmd = ["git", "-C", str(worktree),
           "-c", "core.hooksPath=/dev/null",
           "-c", "gc.auto=0",
           "-c", "rerere.enabled=false",
           "-c", "core.quotePath=false",
           *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        die(EXIT_INPUT, f"git {' '.join(args[:3])} failed: {res.stderr.strip()}")
    return res.stdout


def batch_blobs(worktree: Path, ref: str, paths: list[str]) -> dict[str, bytes | None]:
    """Read one blob per path at ``ref`` in a single ``git cat-file --batch``.

    Returns path -> bytes, or None when the path does not exist at that ref (a
    delete/add/rename, i.e. a tree conflict). A protocol-level surprise is fatal
    (exit 64) rather than silently treated as absent -- "missing" and "unreadable"
    must never collapse into the same class.
    """
    if not paths:
        return {}
    specs = "".join(f"{ref}:{p}\n" for p in paths)
    proc = subprocess.run(
        ["git", "-C", str(worktree), "-c", "core.quotePath=false",
         "cat-file", "--batch", "--buffer"],
        input=specs.encode("utf-8"), capture_output=True)
    if proc.returncode != 0:
        die(EXIT_CLASSIFY,
            f"git cat-file --batch failed at {ref}: {proc.stderr.decode(errors='replace').strip()}")
    out = proc.stdout
    result: dict[str, bytes | None] = {}
    pos = 0
    for p in paths:
        nl = out.find(b"\n", pos)
        if nl < 0:
            die(EXIT_CLASSIFY, f"git cat-file --batch: truncated output at {ref}:{p}")
        header = out[pos:nl]
        pos = nl + 1
        if header.endswith(b" missing") or header.endswith(b" ambiguous"):
            result[p] = None
            continue
        parts = header.rsplit(b" ", 2)
        if len(parts) != 3:
            die(EXIT_CLASSIFY, f"git cat-file --batch: bad header {header!r} for {ref}:{p}")
        try:
            size = int(parts[2])
        except ValueError:
            die(EXIT_CLASSIFY, f"git cat-file --batch: bad size in {header!r}")
        result[p] = out[pos:pos + size]
        pos += size + 1  # trailing newline after the payload
    return result


def is_binary(blob: bytes | None) -> bool:
    return blob is not None and b"\x00" in blob[:8000]


def try_json(blob: bytes | None):
    if blob is None:
        return None
    try:
        return json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------- langpack
def card_pointers(obj) -> dict | None:
    """Flatten a decomposed card to the pointer granularity the manifest uses:
    every top-level key, with ``CustomDeck`` expanded one level."""
    if not isinstance(obj, dict):
        return None
    out: dict = {}
    for k, v in obj.items():
        if k == "CustomDeck" and isinstance(v, dict):
            for sk, sv in v.items():
                out[f"/CustomDeck/{sk}"] = sv
        else:
            out[f"/{k}"] = v
    return out


def keys_changed(x: dict, b: dict) -> set[str]:
    sentinel = object()
    return {k for k in set(x) | set(b) if x.get(k, sentinel) != b.get(k, sentinel)}


def is_image_identity(ptr: str) -> bool:
    return ptr == "/CardID" or ptr == "/CustomDeck" or ptr.startswith("/CustomDeck/")


def deck_urls(obj) -> list[str]:
    urls: list[str] = []
    deck = obj.get("CustomDeck") if isinstance(obj, dict) else None
    if not isinstance(deck, dict):
        return urls
    for entry in deck.values():
        if isinstance(entry, dict):
            for key in ("FaceURL", "BackURL"):
                val = entry.get(key)
                if isinstance(val, str):
                    urls.append(val)
    return urls


def host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_r2_hosted(obj) -> bool:
    return any(host(u).endswith(R2_HOST_SUFFIX) for u in deck_urls(obj))


def langpack_shape(path: str, b_blob, u_blob, f_blob) -> tuple[str | None, str]:
    """Design §3.2's L-a / L-b / L-c table. Returns (shape, reason)."""
    if not path.startswith(LANGPACK_PREFIX):
        return None, "not a langpack path"
    ob, ou, of = try_json(b_blob), try_json(u_blob), try_json(f_blob)
    pb, pu, pf = card_pointers(ob), card_pointers(ou), card_pointers(of)
    if pb is None or pu is None or pf is None:
        return None, "not a JSON object on all three of B/U/F"
    kc_f = keys_changed(pf, pb)
    kc_u = keys_changed(pu, pb)
    if not all(is_image_identity(k) for k in kc_f):
        outside = sorted(k for k in kc_f if not is_image_identity(k))
        return "L-c.mixed", f"fork changed {outside[:6]} outside the image identity"
    contested = {k for k in kc_u if is_image_identity(k)}
    if not contested:
        return "L-a.image-identity-only", "upstream did not touch the image identity"
    if is_r2_hosted(of) and not is_r2_hosted(ou):
        return "L-a.image-identity-only", "fork image identity is R2-hosted, upstream's is not"
    return "L-b.image-contested", (
        f"both sides moved {sorted(contested)[:4]} and the host rule does not separate them")


# ------------------------------------------------------------- classification
def classify_path(b, u, f, r, case_sibling: bool) -> str:
    if case_sibling or any(x is None for x in (b, u, f, r)):
        return "C8.tree_conflict"
    if u == f:
        return "C0.identical"
    if r == f:
        return "C1.fork_wins"
    if r == u:
        return "C2.upstream_wins"
    if r == b:
        return "C4.base"
    return "C3.blend"


def merge_file_conflicts(tmp: Path, b, u, f) -> tuple[bool, str]:
    """``git merge-file -p --diff3 F B U``: non-zero iff a plain three-way merge
    of these blobs conflicts. Returns (conflicted, diff3_text)."""
    if any(x is None for x in (b, u, f)):
        return True, ""
    if any(is_binary(x) for x in (b, u, f)):
        return True, "(binary blob: merge-file not run)\n"
    fp, bp, up = tmp / "fork", tmp / "base", tmp / "upstream"
    fp.write_bytes(f)
    bp.write_bytes(b)
    up.write_bytes(u)
    res = subprocess.run(
        ["git", "merge-file", "-p", "--diff3",
         "-L", "fork", "-L", "base", "-L", "upstream",
         str(fp), str(bp), str(up)],
        capture_output=True)
    text = res.stdout.decode("utf-8", errors="replace")
    if res.returncode == 0:
        return False, text
    if 0 < res.returncode < 128:
        return True, text
    # -1 / 255: merge-file itself failed. Treat as conflicted -- the fail-safe
    # direction, since a seed-decided path must then be ruled on explicitly.
    return True, f"(git merge-file error rc={res.returncode})\n"


# ----------------------------------------------------------- prompt fragments
def build_policy() -> str:
    """Curated restatement of the workspace policy, not a mirror of CLAUDE.md.

    Deliberately a literal rather than a runtime excerpt: at 02:17 unattended, a
    heading-based extractor that silently returns an empty section is a worse
    failure than prose that a human can diff against CLAUDE.md by hand.
    """
    return """\
These are the standing rules of the SCED workspace. They come from
`/Volumes/PRO-G40/Projects/SCED/CLAUDE.md` (sections "Current Work",
"Conventions" and "Automation") and from `.am/conflict-url-revert/design.md`
(the settled decisions of 2026-06-27). They bind your rulings.

**What the fork is for.** `shanash/SCED` and `shanash/SCED-downloads` track
upstream (`Chr1Z93`) with Korean-only changes. Divergence from `upstream/main` is
kept deliberately minimal.

- **Korean changes belong in langpack data, not base code.** A fork edit to base
  code is either one of the two sanctioned exceptions below or a mistake.
- `SCED/src/core/Constants.ttslua` holds the fork's single load-bearing code
  change: `SOURCE_REPO` must point at
  `github.com/shanash/SCED-downloads/releases/latest/download/`, never at
  upstream's Chr1Z93 URL. Losing that line ships a mod that downloads upstream's
  English content to every Korean user. It is the entire reason the fork exists.
- `SCED/objects/InvestigatorTokens.0203af*` is a case-only rename
  (`Investigatortokens` -> `InvestigatorTokens`) that fixes the build on
  case-sensitive Linux CI. This volume is case-insensitive APFS: a tree that
  contains both spellings loses one file silently on checkout. Never produce a
  tree carrying both.
- Korean langpack content lives under
  `SCED-downloads/decomposed/language-pack/Korean - Player Cards|Campaigns/`.
  Base data and `library.json` are untouched by the fork.

**The overlap gate.** The set you are auditing is the intersection of the paths
each side changed since the merge base. A non-empty intersection normally stops
the nightly sync and requires a human. Tonight it is being handed to you instead,
and the resulting tree is force-pushed and published unattended. Your rulings are
the only judgement in the loop.

**Settled decisions from the 2026-06-27 conflict-url-revert chore (LOCKED -- do
not re-litigate):**

1. A packed card (`UniqueBack=true`, grid larger than 1x1) must never have its
   `BackURL` pointed at a 1x1 URL, and vice versa. The back would garbage-slice
   against the wrong grid. `FaceURL`, `BackURL`, `NumWidth`, `NumHeight` and
   `UniqueBack` of a deck entry are one unit.
2. Face same/different is a **visual** judgement. Automated perceptual hashing was
   available for that job and was explicitly rejected. It is not delegated to you.
3. The langpack bag containers (`Korean-PlayerCards.KoreanI.json` and siblings)
   reference cards by GUID. Do not move a card between decks.

**`-X ours` is not a resolution.** The nightly driver seeds the rebase with
`--strategy-option=ours` purely to obtain a tree to audit; in rebase semantics
that means "upstream wins", i.e. the fork's change is the one at risk of having
been dropped. Assume nothing about the seed's choices: check them.
"""


def build_precedent() -> str:
    """Design §5.4 item 5, quoted verbatim -- the single most important
    instruction in the prompt."""
    return """\
On 2026-06-27, 141 Korean cards conflicted. The correct answer required
**rendering the card images and looking at them**: 118 needed a full revert, 5
needed a per-card human SAME/DIFF visual call, and 18 had to be kept because
reverting would garbage-slice a packed atlas grid. Automated perceptual hashing
was available and was explicitly rejected for this job.

**You cannot see images.** You cannot tell an R2-hosted Korean card from a
Steam-hosted English one by URL. You are never asked to. Two rules follow, and
they are absolute:

1. A card's `CardID` and its entire `CustomDeck` object move **together, from one
   side**. Never mix a grid from one side with a URL from the other -- that is
   exactly how a packed 10x6 back gets sliced against a 1x1 image.
2. If both sides changed the image identity and the deterministic host rule did
   not separate them, **abstain**. Do not guess. An abstain costs one stalled
   night; a guess costs a broken public release.
"""


def render_class_table(classes: dict) -> str:
    lines = ["| class_id | kind | n | meaning | proposed verdict |",
             "|---|---|---|---|---|"]
    for cid, info in classes.items():
        lines.append(f"| `{cid}` | {info['kind']} | {info['n']} | {info['meaning']} | "
                     f"{info.get('proposed_verdict', '-')} |")
    return "\n".join(lines)


# ----------------------------------------------------------------------- main
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, choices=["SCED", "SCED-downloads"])
    p.add_argument("--worktree", type=Path, required=True)
    p.add_argument("--merge-base", required=True)
    p.add_argument("--fork-ref", required=True)
    p.add_argument("--upstream-ref", required=True)
    p.add_argument("--overlap", type=Path, required=True)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--mode", choices=["audit", "drive"], default="audit",
                   help="audit: R is the shipped post-rebase tree (approach a). "
                        "drive: R is a throwaway shadow-seed tree used only as a "
                        "deterministic reference (approach b).")
    p.add_argument("--result-ref", default="HEAD",
                   help="what to read R from; under --mode drive this is the "
                        "shadow seed's commit, never the worktree HEAD")
    # The gate's two input sets. Recorded, never recomputed: check-upstream-overlap.sh
    # owns the predicate and both of its inputs, and a second implementation here
    # would be a third copy to keep in sync.
    p.add_argument("--fork-paths", type=Path,
                   help="check-upstream-overlap.sh --fork-paths-out")
    p.add_argument("--upstream-paths", type=Path,
                   help="check-upstream-overlap.sh --upstream-paths-out")
    p.add_argument("--seed-commit",
                   help="shadow-seed commit, recorded for reproducibility "
                        "(--mode drive)")
    p.add_argument("--commits-expected", type=int,
                   help="fork commits that SHOULD replay; defaults to "
                        "rev-list --count merge_base..fork_ref")
    p.add_argument("--prompt-template", type=Path, default=None,
                   help="overrides the per-mode default template")
    p.add_argument("--sample-per-class", type=int, default=5)
    p.add_argument("--material-all-max", type=int, default=64,
                   help="At or below this overlap size, extract material for every path.")
    p.add_argument("--material-max-bytes", type=int, default=1_000_000)
    p.add_argument("--inline-budget-bytes", type=int, default=200_000)
    p.add_argument("--full-list-max", type=int, default=25,
                   help="Classes at or below this size get a full path list in the prompt.")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:  # noqa: C901 - one linear pipeline, kept in reading order
    args = parse_args(argv)
    wt: Path = args.worktree
    run_dir: Path = args.run_dir
    if args.prompt_template is None:
        args.prompt_template = TEMPLATE_FOR_MODE[args.mode]

    if not (wt / ".git").exists() and not (wt / "HEAD").exists():
        if subprocess.run(["git", "-C", str(wt), "rev-parse", "--git-dir"],
                          capture_output=True).returncode != 0:
            die(EXIT_INPUT, f"{wt} is not a git worktree")
    if not args.overlap.is_file():
        die(EXIT_INPUT, f"overlap file not found: {args.overlap}")
    if not args.prompt_template.is_file():
        die(EXIT_INPUT, f"prompt template not found: {args.prompt_template}")
    for label, p in (("--fork-paths", args.fork_paths),
                     ("--upstream-paths", args.upstream_paths)):
        if p is not None and not p.is_file():
            die(EXIT_INPUT, f"{label} file not found: {p}")

    run_dir.mkdir(parents=True, exist_ok=True)
    overlap_bytes = args.overlap.read_bytes()
    overlap = [ln for ln in overlap_bytes.decode("utf-8").split("\n") if ln]
    overlap_sha256 = hashlib.sha256(overlap_bytes).hexdigest()
    atomic_write_text(run_dir / "overlap.txt", overlap_bytes.decode("utf-8"))

    merge_base = git(wt, "rev-parse", "--verify", f"{args.merge_base}^{{commit}}").strip()
    fork_sha = git(wt, "rev-parse", "--verify", f"{args.fork_ref}^{{commit}}").strip()
    upstream_sha = git(wt, "rev-parse", "--verify", f"{args.upstream_ref}^{{commit}}").strip()
    result_sha = git(wt, "rev-parse", "--verify", f"{args.result_ref}^{{commit}}").strip()

    commits_replayed = int(
        git(wt, "rev-list", "--count", f"{upstream_sha}..{result_sha}").strip() or 0)
    if commits_replayed == 0:
        die(EXIT_CLASSIFY,
            f"commits_replayed == 0: {upstream_sha[:12]}..{result_sha[:12]} is empty -- "
            f"the fork's entire divergence vanished in the rebase")

    # How many commits SHOULD have replayed. Under --mode drive the difference
    # between this and what claude's rebase actually produced is what decide-rule
    # 10 makes it declare, with a reason, in commits_dropped[].
    commits_expected = args.commits_expected
    if commits_expected is None:
        commits_expected = int(
            git(wt, "rev-list", "--count", f"{merge_base}..{fork_sha}").strip() or 0)

    # The shadow seed's reproducibility depends on git's merge-strategy defaults,
    # so the version is recorded. A future non-reproduction should be diagnosable
    # rather than mysterious (design §5.4).
    git_version = subprocess.run(["git", "--version"], capture_output=True,
                                 text=True).stdout.strip().replace("git version ", "")
    seed_commit = args.seed_commit or (result_sha if args.mode == "drive" else None)
    seed_tree = (git(wt, "rev-parse", "--verify", f"{seed_commit}^{{tree}}").strip()
                 if seed_commit else None)

    def _read_set(p: Path | None) -> list[str] | None:
        if p is None:
            return None
        return [ln for ln in p.read_text(encoding="utf-8").split("\n") if ln]

    fork_changed = _read_set(args.fork_paths)
    upstream_changed = _read_set(args.upstream_paths)

    # Read from the same env var ai-overlap-decide.py enforces the cap from. The
    # prompt must not be able to state a bound the decider does not apply.
    _raw = os.environ.get("SCED_SYNC_AI_MAX_HAND_MERGE", "").strip()
    max_hand_merge = int(_raw) if _raw.isdigit() else DEFAULT_MAX_HAND_MERGE

    # ---- case-sibling detection over the whole result tree (design §5.7 check 5's
    # hazard, surfaced here as a class rather than discovered at apply time).
    lower_map: dict[str, list[str]] = {}
    for line in git(wt, "ls-tree", "-r", "--name-only", result_sha).split("\n"):
        if line:
            lower_map.setdefault(line.lower(), []).append(line)
    case_siblings = {p for group in lower_map.values() if len(group) > 1 for p in group}

    # ---- four blobs per path, four batched cat-file calls
    blobs = {
        "B": batch_blobs(wt, merge_base, overlap),
        "U": batch_blobs(wt, upstream_sha, overlap),
        "F": batch_blobs(wt, fork_sha, overlap),
        "R": batch_blobs(wt, result_sha, overlap),
    }

    tmp = Path(tempfile.mkdtemp(prefix="ai-overlap-classify."))
    records: list[dict] = []
    diff3_texts: dict[str, str] = {}
    base_lost: list[str] = []
    try:
        for path in overlap:
            b, u, f, r = (blobs[k][path] for k in ("B", "U", "F", "R"))
            cls = classify_path(b, u, f, r, path in case_siblings)
            if cls == "C4.base":
                base_lost.append(path)
            conflicted, diff3 = merge_file_conflicts(tmp, b, u, f)
            diff3_texts[path] = diff3
            shape, shape_reason = langpack_shape(path, b, u, f)
            records.append({
                "path": path,
                "class": cls,
                "shape": shape,
                "shape_reason": shape_reason,
                "seed_decided": conflicted,
                "case_sibling": path in case_siblings,
                "size": {k: (len(blobs[k][path]) if blobs[k][path] is not None else None)
                         for k in ("B", "U", "F", "R")},
                "material": None,
            })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if base_lost:
        die(EXIT_CLASSIFY,
            f"C4.base: {len(base_lost)} path(s) came out of the rebase equal to the "
            f"merge base, i.e. BOTH sides' changes were lost. First: {base_lost[:5]}")

    # ---- class + shape aggregation
    classes: dict[str, dict] = {}
    for cid in CLASS_MEANING:
        members = [r["path"] for r in records if r["class"] == cid]
        if not members and cid not in ("C0.identical", "C3.blend"):
            continue
        classes[cid] = {"kind": "class", "n": len(members),
                        "meaning": CLASS_MEANING[cid]}
        if len(members) <= args.full_list_max:
            classes[cid]["paths"] = members
        else:
            classes[cid]["sample"] = members[:args.sample_per_class]
    for sid in SHAPE_MEANING:
        members = [r["path"] for r in records if r["shape"] == sid]
        if not members:
            continue
        classes[sid] = {"kind": "shape", "n": len(members),
                        "meaning": SHAPE_MEANING[sid],
                        "proposed_verdict": SHAPE_PROPOSED[sid]}
        if len(members) <= args.full_list_max:
            classes[sid]["paths"] = members
        else:
            classes[sid]["sample"] = members[:args.sample_per_class]

    seed_decided = [r["path"] for r in records if r["seed_decided"]]

    # ---- material selection (design §5.4 item 7)
    material_all = len(overlap) <= args.material_all_max
    wanted: set[str] = set()
    if material_all:
        wanted = set(overlap)
    else:
        for r in records:
            if (r["seed_decided"]
                    or r["class"] in ("C2.upstream_wins", "C8.tree_conflict")
                    or r["shape"] in ("L-b.image-contested", "L-c.mixed")):
                wanted.add(r["path"])
        per_class: dict[str, int] = {}
        for r in records:
            if r["path"] in wanted:
                continue
            key = f"{r['class']}|{r['shape']}"
            if per_class.get(key, 0) < args.sample_per_class:
                per_class[key] = per_class.get(key, 0) + 1
                wanted.add(r["path"])

    material_index: dict[str, str] = {}
    material_root = run_dir / "material"
    if material_root.exists():
        shutil.rmtree(material_root)
    for idx, r in enumerate(records):
        if r["path"] not in wanted:
            continue
        rel = f"material/{idx:05d}"
        d = run_dir / rel
        d.mkdir(parents=True, exist_ok=True)
        for key, name in (("B", "base"), ("U", "upstream"), ("F", "fork"), ("R", "merged")):
            blob = blobs[key][r["path"]]
            if blob is None:
                continue
            if len(blob) > args.material_max_bytes:
                (d / name).write_text(
                    f"(blob omitted: {len(blob)} bytes exceeds "
                    f"--material-max-bytes {args.material_max_bytes})\n", encoding="utf-8")
            else:
                (d / name).write_bytes(blob)
        (d / "merge-file.diff3").write_text(diff3_texts[r["path"]], encoding="utf-8")
        (d / "index.json").write_text(dumps_faithful({
            "path": r["path"], "class": r["class"], "shape": r["shape"],
            "shape_reason": r["shape_reason"], "seed_decided": r["seed_decided"],
            "size": r["size"],
            "present": {k: blobs[k][r["path"]] is not None for k in ("B", "U", "F", "R")},
        }) + "\n", encoding="utf-8")
        r["material"] = rel
        material_index[rel] = r["path"]
    atomic_write_text(material_root / "index.json", dumps_faithful(material_index) + "\n")

    classes_doc = {
        # Schema 2 is a strict superset of 1: every schema-1 key keeps its name,
        # position and type, and the drive-only keys are added after them.
        "schema": 2 if args.mode == "drive" else 1,
        "mode": args.mode,
        "repo": args.repo,
        "merge_base": merge_base,
        "fork_sha": fork_sha,
        "upstream_sha": upstream_sha,
        "result_sha": result_sha,
        "commits_replayed": commits_replayed,
        "overlap_count": len(overlap),
        "overlap_sha256": overlap_sha256,
        "classes": classes,
        "seed_decided": seed_decided,
        "paths": records,
    }
    if args.mode == "drive":
        classes_doc.update({
            "seed_commit": seed_commit,
            "seed_tree": seed_tree,
            "git_version": git_version,
            "commits_expected": commits_expected,
            "fork_changed": fork_changed,
            "upstream_changed": upstream_changed,
            # Same membership as seed_decided, under the name rule 5 reasons about.
            # Kept as its own key because under (b) it is ONE of three conflict
            # signals, and conflating it with the others is how the 18-of-142
            # blindness gets back in.
            "conflict_oracle": seed_decided,
        })
    atomic_write_text(run_dir / "classes.json", dumps_faithful(classes_doc) + "\n")

    # ---- prompt bundle
    policy = build_policy()
    precedent = build_precedent()
    atomic_write_text(run_dir / "policy.md", policy)
    atomic_write_text(run_dir / "precedent.md", precedent)

    # The schema has exactly one owner: ai-overlap-decide.py, which also enforces
    # it. Emitting it from there makes drift between "what the model was told" and
    # "what is checked" structurally impossible.
    schema_path = run_dir / "manifest.schema.json"
    res = subprocess.run([sys.executable, str(DECIDER), "--emit-schema", str(schema_path),
                          "--schema-version", "2" if args.mode == "drive" else "1",
                          "--quiet"], capture_output=True, text=True)
    if res.returncode != 0 or not schema_path.is_file():
        die(EXIT_CLASSIFY, f"could not emit manifest schema via {DECIDER}: "
                           f"{res.stderr.strip()}")

    inline_doc = {k: v for k, v in classes_doc.items() if k != "paths"}
    inline_doc["paths"] = f"(omitted here; full per-path table at {run_dir / 'classes.json'})"

    material_lines = [f"- `{rel}` -> `{p}`" for rel, p in sorted(material_index.items())]
    if len(material_lines) > 400:
        extra = len(material_lines) - 400
        material_lines = material_lines[:400] + [
            f"- ... and {extra} more; the full map is `{run_dir / 'material' / 'index.json'}`"]
    material_index_md = ("\n".join(material_lines) if material_lines
                         else "_(no material extracted: the overlap set is empty)_")

    inline_blocks: list[str] = []
    used = 0
    for r in records:
        if not r["seed_decided"] or not diff3_texts[r["path"]]:
            continue
        block = (f"### `{r['path']}` — {r['class']}"
                 f"{' / ' + r['shape'] if r['shape'] else ''}\n\n"
                 f"```\n{diff3_texts[r['path']]}\n```\n")
        if used + len(block) > args.inline_budget_bytes:
            inline_blocks.append(
                f"_(remaining seed-decided diff3 text omitted for size; read it under "
                f"`{run_dir / 'material'}`)_\n")
            break
        inline_blocks.append(block)
        used += len(block)
    inline_diff3 = ("## 8b. Conflicting hunks (`seed_decided`), diff3\n\n"
                    + "\n".join(inline_blocks)) if inline_blocks else ""

    # Drop the template's own authoring comment: it addresses whoever edits the
    # template, not the model that reads the rendered prompt.
    template = re.sub(r"\A<!--.*?-->\s*", "", args.prompt_template.read_text(encoding="utf-8"),
                      count=1, flags=re.DOTALL)
    replacements = {
        "{{REPO}}": args.repo,
        "{{MERGE_BASE}}": merge_base,
        "{{FORK_SHA}}": fork_sha,
        "{{UPSTREAM_SHA}}": upstream_sha,
        "{{RESULT_SHA}}": result_sha,
        "{{OVERLAP_COUNT}}": str(len(overlap)),
        "{{OVERLAP_SHA256}}": overlap_sha256,
        "{{RUN_DIR}}": str(run_dir),
        "{{SEED_DECIDED_COUNT}}": str(len(seed_decided)),
        "{{CLASS_TABLE}}": render_class_table(classes),
        "{{CLASSES_JSON}}": dumps_faithful(inline_doc),
        "{{MATERIAL_INDEX}}": material_index_md,
        "{{INLINE_DIFF3}}": inline_diff3,
        "{{POLICY}}": policy,
        "{{PRECEDENT}}": precedent,
        "{{SCHEMA}}": schema_path.read_text(encoding="utf-8").rstrip("\n"),
        # drive-only. Defined unconditionally so a stray token in either template
        # is caught by the leftover check below rather than shipped to the model.
        "{{MODE}}": args.mode,
        "{{SEED_COMMIT}}": seed_commit or "-",
        "{{SEED_TREE}}": seed_tree or "-",
        "{{GIT_VERSION}}": git_version,
        "{{COMMITS_EXPECTED}}": str(commits_expected),
        "{{WORKTREE}}": str(wt),
        "{{FORK_CHANGED_COUNT}}": str(len(fork_changed) if fork_changed is not None else 0),
        "{{UPSTREAM_CHANGED_COUNT}}": str(
            len(upstream_changed) if upstream_changed is not None else 0),
        "{{CONFLICT_ORACLE_LIST}}": (
            "\n".join(f"- `{p}`" for p in seed_decided) if seed_decided
            else "_(none: git's three-way merge conflicts on no path in this set)_"),
        # Same env var the decider enforces the cap from, so the number the model
        # is told and the number it is held to cannot drift.
        "{{MAX_HAND_MERGE_HINT}}": f"{max_hand_merge} such path(s) in one run",
        "{{OVERLAP_LIST}}": (
            "\n".join(f"- `{p}`" for p in overlap) if overlap
            else "_(empty)_"),
    }
    prompt = template
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", prompt)
    if leftover:
        die(EXIT_INPUT, f"prompt template has unsubstituted placeholders: {sorted(set(leftover))}")
    atomic_write_text(run_dir / "prompt.md", prompt)

    if not args.quiet:
        print(f"classify: {args.repo} mode={args.mode} overlap={len(overlap)} "
              f"sha256={overlap_sha256[:12]} commits_replayed={commits_replayed}"
              + (f" seed_tree={seed_tree[:12]}" if seed_tree else ""))
        for cid, info in classes.items():
            print(f"  {cid:28s} n={info['n']}")
        print(f"  seed_decided                 n={len(seed_decided)}")
        print(f"  material dirs                n={len(material_index)}")
        print(f"  prompt.md                    {len(prompt)} bytes -> {run_dir / 'prompt.md'}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
