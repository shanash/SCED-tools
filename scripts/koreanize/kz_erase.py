#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `erase` -- inpaint the English text out, or assemble the package that will
(design §5.11, §5.9's inpaint tier, §6 step 13).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER` -- it decodes PNGs with numpy and PIL, and
`/usr/bin/python3` (3.9.6) has PIL 10.4.0 and no numpy at all.

WHAT THIS STAGE IS
------------------
`mask` said which pixels of each 750x1050 slice may be repainted; this stage
repaints them and produces `delivered/`. `composite` then puts the two back
together and asserts, from disk, that nothing outside the mask moved.

There are two engines and they are not two implementations of one thing:

  --engine lama      run the inpainter locally, in its own venv, as a SUBPROCESS
  --engine external  assemble a package for a third-party image AI, assert five
                     things about it, disclose it, and only then ask (§5.11)

`--engine external` is the one with the interesting control, and the ordering of
its five steps is the whole of it.

THE INPAINTER IS A SUBPROCESS, NEVER AN IMPORT (§5.9's third tier)
-------------------------------------------------------------------
LaMa lives in `.venv-lama/bin/python` (3.11); koreanize runs on 3.14. **This
module imports nothing from it** -- no torch, no LaMa package, not even lazily
inside a function -- and `--selftest` AST-scans this file's own source to prove
it, because a lazy import inside a rarely-taken branch is exactly the shape that
would survive review and then fail at 02:47 on somebody else's machine.

The venv path comes from `~/.config/koreanize/env` (`KOREANIZE_LAMA_PYTHON`),
never from `scenario.json`: `scenario.json` is hash-pinned and git-tracked, so a
machine-local absolute path in it could not be used on a second machine, and the
fix would be a regeneration that invalidates the `init` gate's `bound_sha256`
(§3.2). The interpreter's version is ASSERTED before use and refuses at exit 13,
because a 3.9 or a 3.13 in that slot fails somewhere deep inside a checkpoint
load with a traceback nobody can route.

THE GRAIN TOLERANCE, AND WHY IT IS A TOLERANCE AND NOT A GATE
--------------------------------------------------------------
`verify-delivered.py:16-19` defines the measurement: *"grain -- highpass sigma
inside the mask / highpass sigma in a ring around it. 1.0 = the patch carries the
same film grain as the parchment touching it. Near 0 = a smooth, grainless patch
-- the failure mode this project already rejected twice."* All 88 Midwinter faces
measured **0.213-0.630, median 0.399** (`build-erase-review.py:124-129`).

That band is the recorded evidence, and this stage checks against it. A face
outside it is not proof of a defect -- `build-erase-review.py:56-60` is explicit
that grain *"is reported, not a gate"* in the REVIEW GALLERY, whose job is to
shortlist for a human. Here it is the other half of the same fact: the review
gallery cannot fail a run, so nothing in the record could stop a re-erase that
came back glassy-smooth. §4.1 gives it the standard treatment for a measurement
that is real but not conclusive -- it fires at **exit 22** and
`--accept-grain` is the named human acknowledgement. `kz_common.TOLERANCES`
carries the row (`grain` / `erase` / `--accept-grain` / 22) and its named
`--selftest` fault is *"a delivered face with grain ratio driven past the band"*,
which is what `--selftest grain` below actually drives.

Do NOT reach for a fourth brightness detector on this corpus.
`inpaint-artifact-finding.md:26-40` records two that failed and
`task-queue.md:313` records three more; the grain ratio is a **ratio against the
face's own neighbourhood**, which is why it survives where an absolute threshold
on tone does not.

§5.11 -- ASSEMBLE, ASSERT, DISCLOSE, THEN ASK, IN THAT ORDER AND NO OTHER
--------------------------------------------------------------------------
The record's control was four assertions, not one flag
(`atlas-prompt/build-package.py`'s `run_assertions()` at `:325`, checks 04
`:361-363`, 05 `:365-379`, 06 `:381-386`, 07 `:388-391`). *A consent flag
authorizes the transfer; it does not inspect the payload.* And fonts are
precisely what must never leave this machine: the title face is commercial and
machine-local, and the glossary subsets carry a "Do not use!" notice.

So `--engine external` runs:

  1. ASSEMBLE into `<run_dir>/external/<stamp>/`.
  2. ASSERT five clauses over the assembled tree (a..e below).
  3. REFUSE AT EXIT 4 on any of them, before the package is written out of
     `<run_dir>`. Nothing is disclosed and no path is printed: step 4 comes
     after step 3, and a package that failed an assertion is one whose inventory
     nobody should be reading as an offer.
  4. DISCLOSE the full inventory -- count by extension, byte total, top-level
     tree -- to stdout.
  5. THEN require `--consent-third-party-transfer`, and only then print the
     package path. Without it: **exit 26**, with the step-4 disclosure already
     printed, the package left inside `<run_dir>`, and **the path not printed**.

The five clauses:

  (a) 0 Hangul codepoints in any text file
  (b) 0 files with a font extension
  (c) no file whose sha256 equals the sha256 of any `fonts.<role>` whose
      `redistributable` is false
  (d) 0 deny-listed basenames
  (e) every included file present in `slices/manifest.json` or
      `masks/manifest.json` with a matching sha256

**Clause (c) is a HASH comparison and not a path rule, and that is the load-
bearing sentence of this whole module.** The obvious formulation -- "0 paths
under any resolved font parent directory" -- is *vacuous* over a tree assembled
into `<run_dir>/external/<stamp>/`: every path in it is under `<run_dir>` by
construction, so the clause would pass on a package containing every font on the
machine. Hashing survives a rename, catches a font copied in under an innocuous
name, and is what check 04 in the record was reaching for. It is also
**selective**: a `redistributable: true` font -- the Hakgyoansim body face -- is
allowed through, which is the entire point of carrying the flag on the role.
`--selftest external` plants a font under a `.png` name and requires (c) to
catch it, plants the redistributable one the same way and requires (c) NOT to,
and asserts in the same case that the planted file really is under the package
root, so the vacuous formulation is shown to be vacuous rather than argued to be.

`erase` IS A NEUTRAL STAGE, SO THIS MODULE DELIBERATELY DOES NOT `declare_ai()`
-------------------------------------------------------------------------------
`kz_config.STAGES` puts `erase` in the four-member NEUTRAL class alongside
`init`, `source` and `check`: it neither requires AI nor is forbidden it. §5.1's
`erase` row says why -- *"it runs LaMa or assembles a package. The only judgement
-- transfer FFG art to a third party -- is a human consent flag, and the record is
explicit that it is 'the user's decision, not an agent's'"*.
`kc.declare_ai()` can only express `required=True` or `required=False`, and
`required=False` asserts the stage into `ai.forbidden_stages`. Calling it here
would therefore refuse at **exit 4** the moment a `scenario.json` is bound,
because `erase` is in `ai.neutral_stages` and in neither of the other two lists.
`kz_checkers.py:69` and `kz_source.py:89-92` carry the same note for the same
reason; `--selftest` below asserts the class mechanically against
`kz_config.STAGES` so this cannot rot into a comment.

EXIT CODES (§4.2)
   0  every face delivered and inside the grain band, or the package asserted,
      disclosed and consented
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin, a planned write outside
      <run_dir>, or an `--engine external` package assertion (§5.11 step 3)
  13  precondition -- a manifest, an upstream report that is not consumable, or
      the LaMa venv interpreter absent / unrunnable / at the wrong version
  22  a delivered face's grain ratio is outside the recorded band and
      --accept-grain was not given (kz_common.TOLERANCES row `grain`)
  26  the five §5.11 assertions all passed and
      --consent-third-party-transfer was not given (kz_common.CONSENTS row
      `third-party-transfer`)
  67  delivered/ is not the expected set of PNGs at the expected geometry, or the
      inpainter returned rc 0 and produced nothing
  74  reserved by §4.2 for R2/network; THIS MODULE NEVER PERFORMS NETWORK I/O and
      therefore never returns it. koreanize transmits nothing itself -- the
      external package is handed to a person, who decides.
"""

import argparse
import ast
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402

import numpy as np  # noqa: E402
import PIL  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

STAGE = "erase"

# NOT declare_ai(). See the module docstring: `erase` is one of the four NEUTRAL
# stages (kz_config.STAGES), and declare_ai can only say required/forbidden --
# so calling it would assert this stage into a partition scenario.json does not
# put it in and refuse at exit 4 the moment a config is bound.

FAULTS = ("grain", "external", "third-party-transfer", "engine")

ENGINES = ("lama", "external")

#: The TOLERANCES / CONSENTS rows this module owns. Named here so `--selftest`
#: can assert against the tables rather than against two copies of a string.
TOLERANCE = "grain"
CONSENT = "third-party-transfer"


# ===========================================================================
# 1. The write guard -- re-asserted internally, exit 4 before any read
# ===========================================================================
#
# `guard.write_roots` names the SCED-downloads langpack destinations and nothing
# else (kz_init.py:1205-1231), so `kz.assert_write_paths` is the wrong instrument
# for a stage whose every write is inside <run_dir>. The property to assert is
# containment, plus the same `guard.forbidden` prefix test the shared evaluator
# applies. It runs BEFORE the first read, so "nothing was read" is literally true
# on a refusal (§4.2 code 4).
#
# `kz_composite.py` carries the twin of this function. It belongs in `kz_common`
# and is deliberately NOT put there by this change set: `kz_common.py` is a §6
# step 1 file and this step may not edit it. The duplication is two files, both
# art-tier, both saying so.

def assert_inside_run_dir(cfg, run_dir, paths, workspace=None):
    """Every planned write resolves inside <run_dir>. Refuses at exit 4.

    Symlink-safe by construction: both sides are `realpath`'d, so an `external`
    symlink pointing at somebody's Dropbox is refused rather than followed --
    which for THIS stage is not a hypothetical, because the whole point of
    `--engine external` is that the package is destined to leave the machine.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    root = os.path.realpath(str(run_dir))
    findings = []
    for path in paths:
        real = os.path.realpath(str(path))
        if real != root and not real.startswith(root + os.sep):
            findings.append("%s resolves to %s, which is outside <run_dir> %s"
                            % (path, real, root))
            continue
        rel = os.path.relpath(real, os.path.realpath(workspace)).replace(os.sep, "/")
        for bad in (cfg.get("guard") or {}).get("forbidden") or ():
            if rel.startswith(bad) or ("/" + rel).find("/" + bad) >= 0:
                findings.append("%s matches guard.forbidden %r" % (rel, bad))
                break
    if findings:
        kc.refuse(kc.EXIT_GUARD,
                  "%s planned a write outside <run_dir>" % STAGE,
                  "; ".join(findings[:kz.GUARD_FINDING_SAMPLE]))


def load_json(path, label):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is absent" % label, path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        kc.refuse(kc.EXIT_PRECONDITION, "%s is not valid JSON" % label,
                  "%s: %s" % (path, exc))


#: TWO MANIFEST SHAPES, ONE READER. `kz_slice.py` / `kz_mask.py` emit `records[]`
#: + `entries[]`; the Midwinter record -- which is the golden fixture the whole
#: art chain is regression-checked against (§6 step 8) -- emits `records[]` +
#: `masks[]`, with `measured_width`/`measured_height` where the live producer
#: writes `cell_pixels`. Both are legitimate inputs to `erase`, and a reader that
#: understood only the live one would make the regression instrument unrunnable,
#: which is the one thing §5.8 exists to prevent. `kz_composite.py` carries the
#: twin of this section for the same reason and with the same caveat as the write
#: guard above: it belongs in `kz_common`, and `kz_common.py` is a §6 step 1 file
#: this step may not edit.

def mask_records(manifest):
    """The mask entries, under either key."""
    if isinstance(manifest.get("masks"), list):
        return manifest["masks"]
    if isinstance(manifest.get("entries"), list):
        return manifest["entries"]
    return []


def slice_geometry(record):
    """(width, height) for one slice record, or None.

    The MEASUREMENT is preferred over the derived cell size: on a `single_card`
    atlas the two agree, and where they would not, the file on disk is the thing
    the inpainter is handed.
    """
    if record.get("measured_width") and record.get("measured_height"):
        return (int(record["measured_width"]), int(record["measured_height"]))
    cell = record.get("cell_pixels")
    if isinstance(cell, (list, tuple)) and len(cell) == 2 and all(cell):
        return (int(cell[0]), int(cell[1]))
    return None


# ===========================================================================
# 2. The inpaint tier -- resolved, version-asserted, and run as a subprocess
# ===========================================================================

LAMA_PYTHON_ENV = "KOREANIZE_LAMA_PYTHON"
LAMA_RUNNER_ENV = "KOREANIZE_LAMA_RUNNER"
LAMA_TIMEOUT_ENV = "KOREANIZE_LAMA_TIMEOUT_S"

#: The venv the design pins (§5.9's inpaint tier row). Asserted on the MAJOR and
#: MINOR only: a patch bump inside 3.11 does not change the checkpoint loader,
#: and pinning the patch would make the stage refuse on a routine venv upgrade.
LAMA_PYTHON_VERSION = (3, 11)

#: Wall clocks, in PLAIN INTEGER SECONDS. The convention and the reason are the
#: nightly driver's (CLAUDE.md): an unparseable value makes the bound collapse
#: rather than widen, so a bad knob is logged and REPLACED BY THE DEFAULT here
#: rather than forwarded.
LAMA_PROBE_TIMEOUT_S = 30
LAMA_TIMEOUT_DEFAULT_S = 3600


def _positive_int_seconds(raw, default, name, log=None):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = None
    if value is None or value <= 0:
        if raw is not None:
            (log if log is not None else []).append(
                "config: %s is not a positive integer of seconds (%r); using %d"
                % (name, raw, default))
        return default, (raw is not None)
    return value, False


def resolve_lama(env=None, notes=None):
    """Resolve, assert and return the inpaint tier. Refuses at exit 13.

    Four separate refusals, because they are four different operator moves:
    the key is unset (edit the env file), the file is absent (the venv moved),
    it is not runnable (a mode or an architecture problem), and it is the wrong
    Python (the venv was rebuilt on the wrong base).

    NOTE the `~/.config/koreanize/env` file is read through `kz.read_env_file`,
    which refuses a file the group or the world can read: a config that names an
    interpreter this tool will execute is not a file to leave at 0644.
    """
    notes = notes if notes is not None else []
    env = env if env is not None else kz.read_env_file()
    raw = env.get(LAMA_PYTHON_ENV) or os.environ.get(LAMA_PYTHON_ENV)
    if not raw:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the inpaint tier is not configured",
                  "set %s in %s to the venv interpreter (design §5.9); it is "
                  "deliberately NOT pinned in scenario.json, which is hash-pinned "
                  "and git-tracked" % (LAMA_PYTHON_ENV, kz.ENV_FILE))
    python = os.path.expanduser(raw)
    if not os.path.exists(python):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the inpaint interpreter does not exist",
                  "%s=%s" % (LAMA_PYTHON_ENV, raw))
    if not os.access(python, os.X_OK):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the inpaint interpreter is not executable",
                  "%s=%s" % (LAMA_PYTHON_ENV, python))

    # The version is asserted BEFORE use, and by asking the interpreter itself
    # rather than by parsing the path: `.venv-lama/bin/python` says nothing about
    # what is behind it, and a rebuilt venv keeps its directory name.
    try:
        proc = subprocess.run(
            [python, "-c",
             "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=LAMA_PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the inpaint interpreter could not be probed",
                  "%s: %s" % (python, exc))
    if proc.returncode != 0:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the inpaint interpreter returned rc %d to a version probe"
                  % proc.returncode,
                  proc.stderr.decode("utf-8", "replace").strip()[:400])
    version = proc.stdout.decode("ascii", "replace").strip()
    parts = version.split(".")
    try:
        pair = (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        pair = None
    if pair != LAMA_PYTHON_VERSION:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the inpaint interpreter is Python %s, expected %d.%d"
                  % (version, LAMA_PYTHON_VERSION[0], LAMA_PYTHON_VERSION[1]),
                  "%s=%s -- the venv was rebuilt on the wrong base; koreanize "
                  "imports nothing from it, so this is the ONLY place the "
                  "mismatch can be caught before a checkpoint load fails deep "
                  "inside it" % (LAMA_PYTHON_ENV, python))

    runner = env.get(LAMA_RUNNER_ENV) or os.environ.get(LAMA_RUNNER_ENV)
    if not runner:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the inpaint runner is not configured",
                  "set %s in %s to the script the venv interpreter runs"
                  % (LAMA_RUNNER_ENV, kz.ENV_FILE))
    runner = os.path.expanduser(runner)
    if not os.path.exists(runner):
        kc.refuse(kc.EXIT_PRECONDITION, "the inpaint runner does not exist",
                  "%s=%s" % (LAMA_RUNNER_ENV, runner))

    timeout, bad = _positive_int_seconds(
        env.get(LAMA_TIMEOUT_ENV) or os.environ.get(LAMA_TIMEOUT_ENV),
        LAMA_TIMEOUT_DEFAULT_S, LAMA_TIMEOUT_ENV, notes)
    return {"python": python, "runner": runner, "version": version,
            "timeout_s": timeout, "timeout_knob_rejected": bad}


def write_inverted_masks(masks_dir, dest_dir, names):
    """The inpainter's input polarity, which is the INVERSE of the mask's.

    `masks/` is RGBA with alpha 0 = CLEAR / 255 = KEEP. LaMa wants an L-mode
    image where WHITE is the region to repaint. `invert-masks.py` is the record's
    six-line version of this and the polarity comment on its line 8 is the whole
    contract: alpha 0 (transparent) is the region to erase, so it becomes 255.

    Emitted into a SEPARATE tree, never over `masks/`: `composite` refuses an
    L-mode mask three independent ways precisely because handing this tree to the
    compositor inverts every card (`composite-cleared.py:14-19`).
    """
    os.makedirs(dest_dir, exist_ok=True)
    written = []
    for name in names:
        with Image.open(os.path.join(masks_dir, name)) as im:
            alpha = np.asarray(im.convert("RGBA"))[..., 3]
        out = np.where(alpha == 0, 255, 0).astype(np.uint8)
        tmp = os.path.join(dest_dir, name + ".tmp")
        Image.fromarray(out, mode="L").save(tmp, format="PNG")
        os.replace(tmp, os.path.join(dest_dir, name))
        written.append(name)
    return written


def run_inpainter(tier, job_path, log_path):
    """Invoke the venv interpreter on the runner. Returns (rc, detail).

    A wall clock, and rc 143 is the watchdog's SIGTERM rather than a runner
    error -- the same reading the nightly's log gives it (CLAUDE.md). The job is
    handed over as a FILE and not on argv, because 176 absolute paths do not fit
    in one and a truncated argv would silently erase fewer faces than asked.
    """
    cmd = [tier["python"], tier["runner"], "--job", job_path]
    try:
        with open(log_path, "wb") as log:
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=tier["timeout_s"])
        return proc.returncode, "rc %d" % proc.returncode
    except subprocess.TimeoutExpired:
        return 143, ("the inpainter exceeded %s=%d s and was terminated (rc 143 "
                     "is the watchdog's SIGTERM, not a runner error)"
                     % (LAMA_TIMEOUT_ENV, tier["timeout_s"]))
    except OSError as exc:
        return 1, "could not be started: %s" % exc


# ===========================================================================
# 3. The grain ratio, and the recorded band it is checked against
# ===========================================================================
#
# The estimator is `verify-delivered.py:38-61` verbatim in mechanism: a highpass
# (the image minus its own Gaussian blur) whose standard deviation is taken
# inside the CLEAR patch and inside a ring of KEEP pixels around it, then
# divided. Luminance only -- a colour-only discontinuity at constant luminance is
# invisible to it, which is not observed in this corpus and is one loop away if
# it ever is (`build-erase-review.py:52-55`).

ANNULUS = 12    # px ring of KEEP around CLEAR -- the grain REFERENCE
HP_RADIUS = 2   # highpass radius
MIN_SAMPLE = 50  # px; below this a sigma is noise about noise, so it is None

#: The band the Midwinter corpus measured: `build-erase-review.py:124-129`
#: records "All 88 faces measure 0.213-0.630 (median 0.399)". This is EVIDENCE,
#: not a preference -- which is why it is quoted from the file that recorded it
#: and why a per-scenario override lives in `data/locks/<slug>.lock.json` rather
#: than in a constant somebody edits.
GRAIN_BAND = (0.213, 0.630)


def _blur(plane, radius):
    return np.asarray(
        Image.fromarray(plane.astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(radius)),
        dtype=np.float64)


def _dilate(mask, radius):
    im = Image.fromarray((mask * 255).astype(np.uint8))
    return np.asarray(im.filter(ImageFilter.MaxFilter(2 * radius + 1))) > 127


def highpass_sigma(plane, selection):
    """None below MIN_SAMPLE px: a sigma over 12 pixels is not a measurement."""
    if int(selection.sum()) < MIN_SAMPLE:
        return None
    high = plane - _blur(plane, HP_RADIUS)
    return float(high[selection].std())


def grain_ratio(delivered_rgb, keep, clear):
    """(ratio, inside_sigma, ring_sigma). None when either side is unmeasurable.

    A RATIO and not an absolute, and that is why it works where five brightness
    detectors did not: the denominator is the same face's own parchment, 12 px
    away, so a night scene and a cream plate are compared against themselves
    rather than against a corpus-wide constant
    (`inpaint-artifact-finding.md:26-40`).
    """
    luma = delivered_rgb.mean(axis=2)
    ring = _dilate(clear, ANNULUS) & keep
    inside = highpass_sigma(luma, clear)
    outside = highpass_sigma(luma, ring)
    if not inside or not outside:
        return None, inside, outside
    return round(inside / outside, 3), inside, outside


def resolve_grain_band(cfg, workspace=None):
    """The band, and where it came from. `data/locks/<slug>.lock.json` wins.

    The lock is the §3.1 receipt tier: `mask` records its residual baseline there
    and `erase` records its band beside it, so a scenario whose art genuinely
    grains differently records the fact ONCE, in a git-tracked file with an
    author, rather than by an operator passing --accept-grain on every run. A
    standing acceptance flag is an acceptance nobody re-reads.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    path = os.path.join(workspace, (cfg.get("guard") or {}).get("data_root", ""),
                        "locks", "%s.lock.json" % cfg.get("slug"))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lock = json.load(handle)
        except (OSError, ValueError):
            lock = {}
        band = ((lock.get("erase") or {}).get("grain_band"))
        if isinstance(band, (list, tuple)) and len(band) == 2:
            try:
                lo, hi = float(band[0]), float(band[1])
            except (TypeError, ValueError):
                lo = hi = None
            if lo is not None and 0 < lo < hi:
                return (lo, hi), os.path.relpath(path, workspace)
    return GRAIN_BAND, ("build-erase-review.py:124-129 (the Midwinter corpus, "
                        "88 of 88 faces)")


def grain_check(faces, band, accepted):
    """The stage-report check the grain ratio reaches a process exit through.

    `tolerance` is the key `kc.compute_consumable` reads: a fired check whose
    flag was not given blocks consumability as well as returning 22, so a run
    that shipped a grainless `delivered/` cannot be consumed by `composite`
    merely because somebody ignored the exit code.
    """
    lo, hi = band
    outside = [f for f in faces
               if f.get("grain_ratio") is not None
               and not (lo <= f["grain_ratio"] <= hi)]
    unmeasured = [f for f in faces if f.get("grain_ratio") is None]
    ok = (not outside) or bool(accepted)
    detail = ["%s: grain %.3f outside [%.3f, %.3f]"
              % (f["file"], f["grain_ratio"], lo, hi) for f in outside[:10]]
    if unmeasured:
        # Recorded, never fatal: a mask whose CLEAR island is under MIN_SAMPLE px
        # has no grain to measure, and calling that a defect would fire on every
        # frame-text-only face.
        detail.append("%d face(s) carry too few CLEAR or ring pixels to measure "
                      "(< %d px); recorded, not a hit" % (len(unmeasured), MIN_SAMPLE))
    return {"id": "G1", "name": "inpaint_grain_within_band",
            "status": "pass" if ok else "fail",
            "tolerance": TOLERANCE, "exit_on_fail": kc.EXIT_TOLERANCE,
            "band": [lo, hi], "outside": len(outside),
            "unmeasured": len(unmeasured), "detail": detail}


# ===========================================================================
# 4. --engine external -- §5.11's assemble / assert / disclose / ask
# ===========================================================================

#: `atlas-prompt/build-package.py:55-56`, kept as-is. The extension net is (b);
#: it is the cheap one and it is NOT the one that matters -- see clause (c).
FONT_EXT = (".ttf", ".otf", ".ttc", ".woff", ".woff2", ".fon", ".pfb", ".pfm",
            ".bdf", ".dfont", ".pfa", ".otc")
TEXT_EXT = (".md", ".json", ".py", ".txt", ".csv", ".html", ".htm", ".yml",
            ".yaml", ".po", ".ttslua", ".lua")

#: Hangul syllables, Jamo, compatibility Jamo, Jamo Extended-A/B and halfwidth
#: Jamo -- `atlas-prompt/build-package.py:59-66`.
HANGUL_RANGES = ((0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F),
                 (0xA960, 0xA97F), (0xD7B0, 0xD7FF), (0xFFA0, 0xFFDC))

#: `atlas-prompt/build-package.py:73-79`, widened by the koreanize artifacts that
#: did not exist when it was written. Clause (e) would already refuse every one
#: of these -- none is named by either manifest -- so (d) is deliberately
#: redundant: it is the net that still holds if the whitelist is ever widened.
FORBIDDEN_BASENAMES = frozenset({
    "typeset-spec.md", "terminology-ko.md", "card-text-ko.json",
    "manifest.json", "contact-sheet.html", "build-package.py",
    "package-manifest.json",
    # koreanize's own run material (§3.1's "run material" row)
    "scenario.json", "source.json", "card-text-en.json", "card-source-en.json",
    "atlas-inventory.json", "korean-pack-index.json", "pack-membership.json",
    "atlas-urls.json", "upload-map.json", "verify.json",
})

#: The single file the assembler AUTHORS rather than copies. Clause (e) is
#: stated over the manifests, and this is the one carve-out -- verified against
#: the bytes the assembler just wrote, so the carve-out is a digest and not a
#: name. Without it (e) is unsatisfiable on any package carrying instructions,
#: and a package with no instructions is one the vendor cannot use.
README_NAME = "README.md"


def hangul_hits(text):
    out = []
    for ch in text:
        cp = ord(ch)
        for lo, hi in HANGUL_RANGES:
            if lo <= cp <= hi:
                out.append(ch)
                break
    return out


PACKAGE_README = """# English card faces and erase masks -- inpainting request

This package contains {slices} card face images and {masks} matching masks. It
contains no Korean text, no fonts and no metadata about the cards.

## What to do

For every file in `slices/`, there is a mask of the same name in `masks/`.
Reconstruct the image underneath the masked region: remove the printed English
text and continue the surrounding artwork and background across the area it
occupied. Do not add any new text, ornament or signature.

## The mask convention

Masks are RGBA PNGs at the same size as the slice they name.

* `alpha == 0`   -- CLEAR. Repaint this pixel.
* `alpha == 255` -- KEEP.  Leave this pixel exactly as it is.

Every returned image must be the same pixel dimensions as its input and must be
returned as a PNG under the same filename. Pixels outside the masked region are
compared byte for byte against the original on return; an image that has been
resized, re-cropped or globally re-toned will be rejected by that comparison
rather than reviewed.

## What is not here, and will not be

No fonts, no translated text, no card identifiers, no set or product metadata.
The package is asserted against that claim before it is disclosed, and the
assertion is a digest comparison rather than a directory rule.
"""


def _stamp():
    return datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def assemble_package(run_dir, slices_manifest, masks_manifest, stamp=None):
    """§5.11 step 1 -- assemble into `<run_dir>/external/<stamp>/`.

    Copies ONLY the files the two manifests name, and authors exactly one
    README. Nothing is walked: a directory copy is how a `.DS_Store`, a
    `__pycache__` or a stray `manifest.json` reaches a vendor, and the record
    carries a check (12) for exactly that because it happened.
    """
    stamp = stamp or _stamp()
    pkg = os.path.join(run_dir, "external", stamp)
    for sub in ("slices", "masks"):
        os.makedirs(os.path.join(pkg, sub), exist_ok=True)
    copied = 0
    for rec in slices_manifest.get("records") or []:
        name = rec["filename"]
        shutil.copy2(os.path.join(run_dir, "slices", name),
                     os.path.join(pkg, "slices", name))
        copied += 1
    for mrec in mask_records(masks_manifest):
        name = mrec["file"]
        shutil.copy2(os.path.join(run_dir, "masks", name),
                     os.path.join(pkg, "masks", name))
        copied += 1
    readme = PACKAGE_README.format(
        slices=len(slices_manifest.get("records") or []),
        masks=len(mask_records(masks_manifest)))
    readme_path = os.path.join(pkg, README_NAME)
    with open(readme_path, "w", encoding="utf-8") as handle:
        handle.write(readme)
    return pkg, {"stamp": stamp, "copied": copied,
                 "readme_sha256": kc.sha256_bytes(readme.encode("utf-8"))}


def newest_package(run_dir):
    """The most recently stamped package under `<run_dir>/external/`.

    Stamps sort lexicographically because they are `%Y%m%dT%H%M%SZ`, so "newest"
    is `max()` on the name and needs no mtime -- which matters, because a `cp -r`
    of a run directory rewrites every mtime and would otherwise silently
    re-target `--verify-only` at whichever package the copy happened to touch
    last.

    Refuses at exit 13 rather than assembling one: `--verify-only` that quietly
    built its own subject is a verification of nothing.
    """
    parent = os.path.join(run_dir, "external")
    stamps = ([d for d in sorted(os.listdir(parent))
               if os.path.isdir(os.path.join(parent, d))]
              if os.path.isdir(parent) else [])
    if not stamps:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "--verify-only found no assembled package to re-assert",
                  "%s holds no stamped directory; run --engine external without "
                  "--verify-only to assemble one" % parent)
    pkg = os.path.join(parent, stamps[-1])
    readme = os.path.join(pkg, README_NAME)
    if not os.path.exists(readme):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the assembled package carries no %s" % README_NAME,
                  "%s -- clause (e)'s one authored carve-out is matched against "
                  "the bytes the assembler wrote, and there are none" % pkg)
    return pkg, {"stamp": stamps[-1], "copied": None,
                 "readme_sha256": kc.sha256_file(readme)}


def package_files(pkg):
    """(abs, rel) for every file under the package, sorted by rel."""
    out = []
    for dirpath, dirnames, filenames in os.walk(pkg):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            out.append((full, os.path.relpath(full, pkg).replace(os.sep, "/")))
    return sorted(out, key=lambda pair: pair[1])


def non_redistributable_digests(cfg):
    """{sha256: role} for every `fonts.<role>` whose `redistributable` is false.

    THIS IS CLAUSE (c)'s SUBJECT SET, and it is read from `scenario.json` rather
    than re-resolved, because `init` already resolved and hashed every role
    (§5.1 step 8, `kz_init.resolve_fonts`) and re-resolving here would make the
    clause depend on whether a font happens to be installed TODAY rather than on
    what the pinned config recorded.

    Returns (digests, roles_without_digest). The second half is disclosed rather
    than swallowed: `init` records an ABSENT role with `sha256: null`, so a role
    in that state is one clause (c) cannot speak for, and a clause whose coverage
    is partial has to say so or it is a check that reads stronger than it is.
    """
    digests, blind = {}, []
    fonts = cfg.get("fonts") or {}
    for role, spec in sorted(fonts.items()):
        if not isinstance(spec, dict):
            continue           # `search_roots` is a list and is not a role
        if spec.get("redistributable"):
            continue           # SELECTIVE: the Hakgyoansim body face is allowed
        digest = spec.get("sha256")
        if digest:
            digests[digest] = role
        else:
            blind.append(role)
    return digests, blind


def assert_package(pkg, cfg, slices_manifest, masks_manifest, readme_sha256):
    """§5.11 step 2 -- the five clauses, evaluated over the ASSEMBLED TREE.

    Returns a list of clause dicts, each with `clause`, `name`, `ok`, `detail`.
    Every clause is evaluated to completion; the caller refuses at exit 4 if any
    is false. All of them and not the first, for `kz_config.validate`'s reason:
    an operator fixing a package one refusal at a time re-assembles on every pass.
    """
    files = package_files(pkg)
    clauses = []

    def clause(cid, name, ok, detail):
        clauses.append({"clause": cid, "name": name, "ok": bool(ok),
                        "detail": detail})

    # ---- (a) 0 Hangul codepoints in any text file -------------------------
    scanned, hangul = 0, []
    for full, rel in files:
        if not rel.lower().endswith(TEXT_EXT):
            continue
        scanned += 1
        try:
            with open(full, "r", encoding="utf-8") as handle:
                hits = hangul_hits(handle.read())
        except (OSError, UnicodeDecodeError):
            hits = []
        if hits:
            hangul.append("%s: %d codepoint(s), e.g. %s"
                          % (rel, len(hits), "".join(hits[:8])))
    clause("a", "no_hangul_in_text_files", not hangul,
           hangul[:5] if hangul else ["%d text file(s) scanned, 0 hits" % scanned])

    # ---- (b) 0 files with a font extension --------------------------------
    fonts = [rel for _full, rel in files if rel.lower().endswith(FONT_EXT)]
    clause("b", "no_font_extension", not fonts,
           fonts[:5] if fonts else ["0 of %d files carry a font extension"
                                    % len(files)])

    # ---- (c) no file whose sha256 equals a non-redistributable font's ------
    #
    # The clause that reads the `redistributable` flag, and a HASH comparison
    # rather than a path rule. "0 paths under any resolved font parent
    # directory" is VACUOUS here: the package is assembled into
    # <run_dir>/external/<stamp>/, so every path in it is under <run_dir> by
    # construction and the clause would pass on a package containing every font
    # on the machine. Hashing survives a rename and catches a font copied in
    # under an innocuous name; it is also SELECTIVE, so the redistributable body
    # face is allowed through, which is the whole point of carrying the flag.
    digests, blind = non_redistributable_digests(cfg)
    leaks = []
    for full, rel in files:
        role = digests.get(kc.sha256_file(full))
        if role:
            leaks.append("%s IS the %r font face by sha256 -- a rename does not "
                         "change a digest" % (rel, role))
    detail = leaks[:5] if leaks else [
        "%d file(s) hashed against %d non-redistributable font digest(s)"
        % (len(files), len(digests))]
    if blind:
        detail.append("COVERAGE: role(s) %s carry no sha256 in scenario.json "
                      "(init records an absent role with a null digest), so this "
                      "clause cannot speak for them; clause (e)'s whitelist is "
                      "what bounds the package in their absence" % sorted(blind))
    clause("c", "no_non_redistributable_font_by_digest", not leaks, detail)

    # ---- (d) 0 deny-listed basenames --------------------------------------
    bad = [rel for _full, rel in files
           if os.path.basename(rel) in FORBIDDEN_BASENAMES]
    clause("d", "no_denylisted_basename", not bad,
           bad[:5] if bad else ["0 of %d basenames are deny-listed" % len(files)])

    # ---- (e) every included file is manifest-named, with a matching sha256 --
    known = {}
    for rec in slices_manifest.get("records") or []:
        known["slices/%s" % rec["filename"]] = rec.get("sha256")
    for mrec in mask_records(masks_manifest):
        known["masks/%s" % mrec["file"]] = mrec.get("sha256")
    known[README_NAME] = readme_sha256
    unknown, drifted = [], []
    for full, rel in files:
        if rel not in known:
            unknown.append(rel)
            continue
        want = known[rel]
        if want and kc.sha256_file(full) != want:
            drifted.append(rel)
    missing = sorted(set(known) - {rel for _full, rel in files})
    problems = (["not named by either manifest: %s" % r for r in unknown[:5]]
                + ["digest does not match its manifest: %s" % r for r in drifted[:5]]
                + (["%d manifest-named file(s) absent from the package: %s"
                    % (len(missing), missing[:5])] if missing else []))
    clause("e", "every_file_manifest_named_and_matching", not problems,
           problems[:8] if problems else
           ["%d file(s), all manifest-named and digest-matched (the one authored "
            "file, %s, is matched against the bytes just written)"
            % (len(files), README_NAME)])
    return clauses


def disclosure_of(pkg):
    """§5.11 step 4's payload -- count by extension, byte total, top-level tree."""
    files = package_files(pkg)
    by_ext, total = {}, 0
    for full, rel in files:
        ext = os.path.splitext(rel)[1].lower() or "(none)"
        size = os.path.getsize(full)
        by_ext[ext] = by_ext.get(ext, 0) + 1
        total += size
    tree = []
    for entry in sorted(os.listdir(pkg)):
        full = os.path.join(pkg, entry)
        if os.path.isdir(full):
            members = [(f, r) for f, r in files if r.startswith(entry + "/")]
            tree.append({"name": entry + "/", "kind": "dir",
                         "files": len(members),
                         "bytes": sum(os.path.getsize(f) for f, _r in members)})
        else:
            tree.append({"name": entry, "kind": "file", "files": 1,
                         "bytes": os.path.getsize(full)})
    # Sibling stamps are counted, because a package is stamped and every run
    # leaves one: on a real corpus that is ~130 MiB per invocation, and an
    # operator who does not know they are accumulating will find out from the
    # volume rather than from here.
    parent = os.path.dirname(os.path.abspath(pkg))
    siblings = [d for d in sorted(os.listdir(parent))
                if d != os.path.basename(pkg)
                and os.path.isdir(os.path.join(parent, d))]
    return {"files": len(files), "bytes": total,
            "by_extension": dict(sorted(by_ext.items())), "tree": tree,
            "prior_packages": len(siblings)}


def _bytes(n):
    """Adaptive, because the disclosure is read by a person deciding whether to
    transfer this. A 130 MiB package and a 20 KiB one are different decisions and
    "0.0 MiB" for the second is a number that hides which one it is."""
    for unit, scale in (("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if n >= scale:
            return "%.1f %s" % (n / float(scale), unit)
    return "%d B" % n


def disclosure_lines(disclosure, clauses):
    """The stdout form of step 4. NAMES NO PATH -- step 5 owns that."""
    lines = ["", "--engine external -- DISCLOSURE (design §5.11 step 4)",
             "  This is the complete inventory of what a transfer would hand to a",
             "  third party. Read it before authorizing it.",
             "  files          : %d" % disclosure["files"],
             "  bytes          : %d (%s)" % (disclosure["bytes"],
                                             _bytes(disclosure["bytes"])),
             "  by extension   : %s"
             % ", ".join("%s %d" % (k, v)
                         for k, v in disclosure["by_extension"].items()),
             "  top-level tree :"]
    for entry in disclosure["tree"]:
        lines.append("    %-14s %5d file(s)  %10s"
                     % (entry["name"], entry["files"], _bytes(entry["bytes"])))
    if disclosure.get("prior_packages"):
        lines.append("  NOTE           : %d earlier package(s) are still under "
                     "<run_dir>/external/. Each run stamps a new one; nothing "
                     "removes them." % disclosure["prior_packages"])
    lines.append("  assertions     : all %d passed" % len(clauses))
    for cl in clauses:
        lines.append("    (%s) %-42s %s" % (cl["clause"], cl["name"],
                                            "pass" if cl["ok"] else "FAIL"))
        # A COVERAGE line is not a failure and must not be shown as one -- but it
        # is the difference between "clause (c) found nothing" and "clause (c)
        # had nothing to look for", and the person authorizing the transfer is
        # exactly the person who needs to know which. A partial-coverage claim
        # printed only into a JSON report is a claim nobody reads at the moment
        # it matters.
        for detail in cl["detail"]:
            if str(detail).startswith("COVERAGE:"):
                lines.append("        %s" % detail)
    return lines


def transfer_decision(pkg, consented):
    """§5.11 step 5 -- ask, and only then name the path.

    Returns (lines, exit_code, path_printed). A FUNCTION and not an inline
    branch, so `--selftest` can assert on the exact lines: the claim being tested
    is a NEGATIVE -- that the package path does not appear in what the operator
    is shown -- and a negative asserted by reading a print statement is a
    negative nobody has tested.
    """
    row = kc.row_by_flag("--consent-%s" % CONSENT)
    if consented:
        return ([
            "  transfer       : AUTHORIZED by --consent-%s" % CONSENT,
            "  package        : %s" % os.path.abspath(pkg),
            "  koreanize transmits nothing itself. Copying this directory to a",
            "  third party is a manual step and is the operator's own action.",
        ], kc.EXIT_OK, True)
    return ([
        "  transfer       : NOT AUTHORIZED -- exit %d" % kc.EXIT_CONSENT,
        "  The five assertions above all passed. Nothing is wrong; a person must",
        "  authorize this. The package stays inside <run_dir> and its path is",
        "  deliberately NOT printed -- printing it IS the authorization.",
        "  Re-run with --consent-%s to receive it." % CONSENT,
        "  (kz_common.CONSENTS row %r, exit %d)"
        % (row.name if row else CONSENT, kc.EXIT_CONSENT),
    ], kc.EXIT_CONSENT, False)


# ===========================================================================
# 5. The stage
# ===========================================================================

#: `erase` requires `mask` (kz_config.PREDECESSORS). The dispatcher refuses at 72
#: before spawning anything; this is the module's own half, and §4.2 states the
#: relationship: 72 means NOTHING RAN, 13 means a stage ran far enough to read
#: its input and refused on it.
UPSTREAM = ("mask",)


def upstream_findings(run_dir):
    findings = []
    for stage in UPSTREAM:
        state = kz.stage_state(run_dir, stage)
        if not state["present"]:
            findings.append("upstream %s has no report (%s)" % (stage, state["report"]))
        elif not state["consumable"]:
            findings.append("upstream %s is not consumable: %s"
                            % (stage, state["blocked_by"]))
    return findings


def run_erase(run_dir, engine="lama", mode="build", accept_grain=False,
              consent_transfer=False, workspace=None, out=None, quiet=False,
              verify_only=False):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)            -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds -> exit 4 / 13
      3. every planned write resolves inside <run_dir>               -> exit 4
      4. slices/manifest.json and masks/manifest.json are readable    -> exit 13
      5. the two manifests agree on the face set                     -> exit 13
      6. the mask report is present and consumable                   -> exit 13
      7. (lama only) the inpaint interpreter resolves, is executable
         and is Python 3.11                                          -> exit 13

    `out` is the sink the §5.11 disclosure is written to. It defaults to stdout
    and `--quiet` does NOT suppress it: a disclosure an operator can silence is
    not a disclosure, and the flag exists to quiet a progress summary.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    out = out if out is not None else sys.stdout
    if engine not in ENGINES:
        kc.refuse(kc.EXIT_USAGE, "unknown engine %r" % engine,
                  "the engines are %s" % list(ENGINES))
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    slices_dir = os.path.join(run_dir, "slices")
    masks_dir = os.path.join(run_dir, "masks")
    delivered_dir = os.path.join(run_dir, "delivered")
    inverted_dir = os.path.join(run_dir, "masks-inverted")
    external_dir = os.path.join(run_dir, "external")

    # 3 -- BEFORE any read.
    assert_inside_run_dir(cfg, run_dir,
                          [delivered_dir, inverted_dir, external_dir],
                          workspace=workspace)

    slices_manifest = load_json(os.path.join(slices_dir, "manifest.json"),
                                "slices/manifest.json")
    masks_manifest = load_json(os.path.join(masks_dir, "manifest.json"),
                               "masks/manifest.json")
    records = sorted(slices_manifest.get("records") or [],
                     key=lambda r: r["filename"])
    mrecords = mask_records(masks_manifest)
    reasons = []
    if not records:
        reasons.append("slices/manifest.json names no faces -- refusing a vacuous run")
    want = {(r.get("filename") or "").lower() for r in records}
    got = {(m.get("file") or "").lower() for m in mrecords}
    if want != got:
        reasons.append("slices/ and masks/ manifests disagree on the face set: "
                       "%d only in slices, %d only in masks"
                       % (len(want - got), len(got - want)))
    nameless = sum(1 for m in mrecords if not m.get("file"))
    if nameless:
        reasons.append("%d mask record(s) name no file" % nameless)
    reasons += upstream_findings(run_dir)
    if reasons:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s cannot trust its inputs -- nothing erased" % STAGE,
                  "; ".join(reasons[:kz.GUARD_FINDING_SAMPLE]))

    names = [r["filename"] for r in records]
    # PER FACE, from that face's own record: a scenario may carry two atlas cell
    # sizes, and asserting every delivered face against the FIRST record's
    # geometry would refuse the second sheet on a corpus nothing is wrong with.
    geometry_by_file = {r["filename"]: slice_geometry(r) for r in records}
    missing_geometry = sorted(n for n, g in geometry_by_file.items() if g is None)
    if missing_geometry:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%d slice record(s) declare no geometry" % len(missing_geometry),
                  "neither measured_width/height nor cell_pixels: %s"
                  % missing_geometry[:5])

    triggered, checks, notes = [], [], []
    counts = {"faces": len(names), "engine": engine}
    results = None
    accepted = {}

    if engine == "external":
        # ---- §5.11, in this order and no other --------------------------
        #
        # --verify-only re-asserts an EXISTING package and assembles nothing, per
        # the house contract (§4.1). It still discloses and still asks: the
        # consent flag authorizes a TRANSFER, and re-reading a package a previous
        # run assembled is a transfer decision on exactly the same bytes. A
        # verify-only path that handed out the path for free would be the way
        # around the one control this stage has.
        if verify_only:
            pkg, meta = newest_package(run_dir)
        else:
            pkg, meta = assemble_package(run_dir, slices_manifest, masks_manifest)
        clauses = assert_package(pkg, cfg, slices_manifest, masks_manifest,
                                 meta["readme_sha256"])
        failed = [c for c in clauses if not c["ok"]]
        counts.update({"package_files": len(package_files(pkg)),
                       "package_stamp": meta["stamp"],
                       "assertions_failed": len(failed)})
        checks.append({"id": "X1", "name": "package_assertions",
                       "status": "fail" if failed else "pass",
                       "exit_on_fail": kc.EXIT_GUARD,
                       "detail": ["(%s) %s: %s" % (c["clause"], c["name"],
                                                   "; ".join(c["detail"][:2]))
                                  for c in failed] or
                                 ["all %d clauses passed" % len(clauses)]})
        results = {"clauses": clauses,
                   "package_relpath": os.path.relpath(pkg, run_dir)}

        if failed:
            # STEP 3: refuse before disclosing and before naming a path. Step 4
            # is DOWNSTREAM of this refusal by design -- an inventory printed for
            # a package that failed an assertion reads as an offer, and it is not
            # one. The package is left where it is, inside <run_dir>: the bytes
            # are the evidence.
            triggered.append(kc.EXIT_GUARD)
            lines = ["", "--engine external -- REFUSED at exit %d (design §5.11 "
                         "step 3)" % kc.EXIT_GUARD]
            for c in failed:
                lines.append("  (%s) %s" % (c["clause"], c["name"]))
                for d in c["detail"][:4]:
                    lines.append("      %s" % d)
            lines.append("  The package was assembled in place and was NOT written "
                         "out of <run_dir>.")
            lines.append("  It is at external/%s, relative to <run_dir>."
                         % meta["stamp"])
            out.write("\n".join(lines) + "\n")
        else:
            # STEP 4: disclose. STEP 5: then ask.
            disclosure = disclosure_of(pkg)
            results["disclosure"] = disclosure
            out.write("\n".join(disclosure_lines(disclosure, clauses)) + "\n")
            lines, code, path_printed = transfer_decision(pkg, consent_transfer)
            out.write("\n".join(lines) + "\n")
            accepted["--consent-%s" % CONSENT] = bool(consent_transfer)
            accepted[CONSENT] = bool(consent_transfer)
            results["package_path_printed"] = path_printed
            results["package_path"] = os.path.abspath(pkg) if path_printed else None
            checks.append({"id": "X2", "name": "third_party_transfer_consent",
                           "status": "pass" if consent_transfer else "fail",
                           "consent": CONSENT, "exit_on_fail": kc.EXIT_CONSENT,
                           "detail": [] if consent_transfer else
                                     ["--consent-%s was not given; the package "
                                      "stays in <run_dir> and its path was not "
                                      "printed" % CONSENT]})
            if code != kc.EXIT_OK:
                triggered.append(code)
        inputs = [os.path.join(slices_dir, "manifest.json"),
                  os.path.join(masks_dir, "manifest.json")]
    else:
        # ---- --engine lama ----------------------------------------------
        # --verify-only re-measures an existing delivered/ and runs NEITHER the
        # mask inversion nor the inpainter: re-running a non-deterministic
        # generator is not a verification of what it produced last time, and
        # `delivered/` is precisely the tree §3.1 records as "not
        # deterministically reproducible".
        if verify_only:
            if not (os.path.isdir(delivered_dir) and
                    [n for n in os.listdir(delivered_dir) if n.endswith(".png")]):
                kc.refuse(kc.EXIT_PRECONDITION,
                          "--verify-only over an absent or empty delivered/ -- "
                          "refusing a vacuous pass", delivered_dir)
            tier = {"python": None, "runner": None, "version": None,
                    "timeout_s": None, "timeout_knob_rejected": False}
            rc, detail = 0, "not run (--verify-only)"
        else:
            tier = resolve_lama(notes=notes)
            os.makedirs(delivered_dir, exist_ok=True)
            write_inverted_masks(masks_dir, inverted_dir, names)
        job = {"schema_version": kc.SCHEMA_VERSION,
               "generated_by": "koreanize erase",
               "generated_at": kc.utc_now(),
               "convention": "mask is L-mode; WHITE (255) is the region to repaint",
               "faces": [{"image": os.path.abspath(os.path.join(slices_dir, n)),
                          "mask": os.path.abspath(os.path.join(inverted_dir, n)),
                          "out": os.path.abspath(os.path.join(delivered_dir, n))}
                         for n in names]}
        log_path = os.path.join(run_dir, "erase-inpaint.log")
        if not verify_only:
            job_path = os.path.join(run_dir, "erase-job.json")
            kc.atomic_write_json(job_path, job)
            rc, detail = run_inpainter(tier, job_path, log_path)
        checks.append({"id": "L1", "name": "inpainter_exit_code",
                       "status": "pass" if rc == 0 else "fail",
                       "exit_on_fail": kc.EXIT_ARTIFACT,
                       "detail": [] if rc == 0 else
                                 ["%s; the log is %s" % (detail, log_path)]})
        if rc != 0:
            triggered.append(kc.EXIT_ARTIFACT)

        faces, structural = [], []
        # CASE-FOLDED, matching the face-set comparison above: the volume is
        # case-insensitive, so a case-sensitive lookup would agree on the set
        # and then KeyError on the first face.
        masks_by_file = {(m.get("file") or "").lower(): m for m in mrecords}
        for name in names:
            path = os.path.join(delivered_dir, name)
            row = {"file": name, "sha256": None, "size": None,
                   "grain_ratio": None, "inside_sigma": None, "ring_sigma": None}
            if not os.path.exists(path):
                structural.append("%s: the inpainter produced no output" % name)
                faces.append(row)
                continue
            try:
                with Image.open(path) as im:
                    size = im.size
                    delivered_rgb = np.asarray(im.convert("RGB"))
                with Image.open(os.path.join(masks_dir, name)) as im:
                    alpha = np.asarray(im.convert("RGBA"))[..., 3]
            except (OSError, ValueError) as exc:
                structural.append("%s: could not be decoded: %s" % (name, exc))
                faces.append(row)
                continue
            row["size"] = list(size)
            row["sha256"] = kc.sha256_file(path)
            if tuple(size) != geometry_by_file[name]:
                structural.append("%s: %r, expected %r"
                                  % (name, size, geometry_by_file[name]))
                faces.append(row)
                continue
            keep = alpha == 255
            clear = alpha == 0
            ratio, inside, outside = grain_ratio(delivered_rgb, keep, clear)
            row["grain_ratio"] = ratio
            row["inside_sigma"] = None if inside is None else round(inside, 4)
            row["ring_sigma"] = None if outside is None else round(outside, 4)
            row["clear_pixels"] = int(clear.sum())
            row["declared_clear_pixels"] = masks_by_file.get(
                name.lower(), {}).get("cleared_pixels")
            faces.append(row)

        checks.append({"id": "D1", "name": "delivered_is_the_expected_set",
                       "status": "fail" if structural else "pass",
                       "exit_on_fail": kc.EXIT_ARTIFACT,
                       "detail": structural[:10]})
        if structural:
            triggered.append(kc.EXIT_ARTIFACT)

        band, band_source = resolve_grain_band(cfg, workspace)
        check = grain_check(faces, band, accept_grain)
        checks.append(check)
        if check["status"] == "fail":
            triggered.append(kc.EXIT_TOLERANCE)
        accepted["--accept-%s" % TOLERANCE] = bool(accept_grain)
        accepted[TOLERANCE] = bool(accept_grain)

        measured = [f["grain_ratio"] for f in faces if f["grain_ratio"] is not None]
        counts.update({
            "delivered": sum(1 for f in faces if f["sha256"]),
            "grain_measured": len(measured),
            "grain_outside_band": check["outside"],
            "grain_min": min(measured) if measured else None,
            "grain_max": max(measured) if measured else None,
            "inpainter_rc": rc,
        })
        manifest = {"schema_version": kc.SCHEMA_VERSION,
                    "generated_by": "koreanize erase",
                    "generated_at": kc.utc_now(),
                    "engine": "lama",
                    "interpreter": tier["python"], "python": tier["version"],
                    "grain_band": list(band), "grain_band_source": band_source,
                    "faces": faces}
        if mode == "build":
            kc.atomic_write_json(os.path.join(delivered_dir, "manifest.json"),
                                 manifest)
        results = {"faces": faces, "grain_band": list(band),
                   "grain_band_source": band_source, "notes": notes}
        inputs = [os.path.join(slices_dir, "manifest.json"),
                  os.path.join(masks_dir, "manifest.json")]

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path] + inputs),
        freshness=kc.build_freshness(
            inputs, upstream_report_path=kc.report_path(run_dir, "mask", "build")),
        accepted=accepted,
        tool=kc.tool_block(pil=PIL.__version__, numpy=np.__version__),
        results=results)
    # `ai` stays null and is never populated: `erase` is NEUTRAL, so
    # `assert_ai_contract` neither requires a block nor forbids one -- and this
    # module has no AI path to put in it (§5.1's `erase` row).
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    return report


# ===========================================================================
# 6. --selftest -- one planted fault per named row, plus the tier assertions
# ===========================================================================
#
# The standard is `atlas-prompt/build-package.py:456-459`: "Each case injects one
# fault and requires the named check to fail. A check that cannot fail is a
# defect in this project's history, not a nicety."

def _synth_face(width=96, height=96, clear_box=(24, 24, 72, 72), ring_amp=40.0,
                clear_amp=None, seed=11):
    """(delivered_rgb, keep, clear) with a controllable grain contrast.

    `clear_amp=None` means "the same noise amplitude as the ring", which is the
    grainless case's opposite. The band is a RATIO, so what matters is the
    quotient of the two amplitudes and not either one.
    """
    rng = np.random.RandomState(seed)
    base = np.full((height, width), 128.0)
    noise = rng.normal(0.0, 1.0, (height, width))
    x0, y0, x1, y1 = clear_box
    clear = np.zeros((height, width), dtype=bool)
    clear[y0:y1, x0:x1] = True
    keep = ~clear
    amp = np.full((height, width), ring_amp)
    amp[clear] = ring_amp if clear_amp is None else clear_amp
    plane = np.clip(base + noise * amp, 0, 255).astype(np.uint8)
    delivered = np.dstack([plane, plane, plane])
    return delivered, keep, clear


def _no_inpaint_import():
    """AST-scan THIS FILE for an import of the inpaint tier. §5.9: koreanize
    imports nothing from the venv -- including lazily, inside a branch."""
    forbidden = {"torch", "torchvision", "saicinpainting", "lama_cleaner",
                 "simple_lama_inpainting", "omegaconf", "iopaint"}
    findings = []
    with open(os.path.abspath(__file__), "rb") as handle:
        tree = ast.parse(handle.read(), filename=__file__)
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        for mod in mods:
            if mod.split(".")[0] in forbidden:
                findings.append("%s:%d imports %r from the inpaint tier -- §5.9 "
                                "says koreanize imports NOTHING from it"
                                % (os.path.basename(__file__), node.lineno, mod))
    if "torch" in sys.modules:
        findings.append("torch is in sys.modules after importing kz_erase")
    return findings


def _fake_package(root, cfg, slices_manifest, masks_manifest):
    """A three-file synthetic package plus the two manifests that whitelist it."""
    os.makedirs(os.path.join(root, "slices"), exist_ok=True)
    os.makedirs(os.path.join(root, "masks"), exist_ok=True)
    payload = {}
    for sub, key, listing in (("slices", "filename", slices_manifest["records"]),
                              ("masks", "file", mask_records(masks_manifest))):
        for rec in listing:
            name = rec[key]
            blob = ("%s/%s" % (sub, name)).encode("utf-8") * 4
            with open(os.path.join(root, sub, name), "wb") as handle:
                handle.write(blob)
            rec["sha256"] = kc.sha256_bytes(blob)
            payload["%s/%s" % (sub, name)] = blob
    readme = PACKAGE_README.format(slices=len(slices_manifest["records"]),
                                   masks=len(mask_records(masks_manifest)))
    with open(os.path.join(root, README_NAME), "w", encoding="utf-8") as handle:
        handle.write(readme)
    return kc.sha256_bytes(readme.encode("utf-8")), payload


def _fired(clauses, cid):
    return any((not c["ok"]) and c["clause"] == cid for c in clauses)


def selftest(fault=None, verbose=True):
    """Prove each refusal fires on the fault it targets."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    # ---- Standing assertions, run on every invocation -------------------
    #
    # These are not faults; they are the properties a fault case would be
    # meaningless without. `erase` being NEUTRAL is the reason this module does
    # not call declare_ai, and a comment saying so is not a check.
    findings += _no_inpaint_import()
    if STAGE not in kz.STAGES:
        findings.append("%r is not a declared stage" % STAGE)
    if STAGE in kz.AI_STAGE_MAP.values():
        findings.append("%r is mapped to an S-id; it must be neutral, and this "
                        "module's refusal to call declare_ai depends on it" % STAGE)
    if kc.AI_CONTRACT.get(STAGE) is not None:
        findings.append("kz_erase declared an AI contract for a NEUTRAL stage -- "
                        "declare_ai(required=False) asserts it into "
                        "ai.forbidden_stages and refuses at exit 4 on bind")
    trow = kc.row_by_flag("--accept-%s" % TOLERANCE)
    if trow is None or trow.module != "kz_erase.py" or trow.exit_code != kc.EXIT_TOLERANCE:
        findings.append("kz_common.TOLERANCES has no %r row owned by kz_erase.py "
                        "at exit 22" % TOLERANCE)
    crow = kc.row_by_flag("--consent-%s" % CONSENT)
    if crow is None or crow.module != "kz_erase.py" or crow.exit_code != kc.EXIT_CONSENT:
        findings.append("kz_common.CONSENTS has no %r row owned by kz_erase.py "
                        "at exit 26" % CONSENT)

    # ---- grain ----------------------------------------------------------
    if "grain" in wanted:
        band, _src = GRAIN_BAND, "constant"
        # In band: the patch carries 40% of the parchment's grain, which is the
        # corpus's own median (0.399, build-erase-review.py:126).
        delivered, keep, clear = _synth_face(clear_amp=40.0 * 0.40)
        ratio, _i, _o = grain_ratio(delivered, keep, clear)
        if ratio is None:
            findings.append("grain: the in-band control could not be measured")
        elif not (band[0] <= ratio <= band[1]):
            findings.append("grain: the in-band control measured %.3f, outside "
                            "[%.3f, %.3f] -- the control is wrong, not the check"
                            % (ratio, band[0], band[1]))
        ok_faces = [{"file": "in-band.png", "grain_ratio": ratio}]
        if grain_check(ok_faces, band, accepted=False)["status"] != "pass":
            findings.append("grain: an in-band face fired the tolerance")

        # THE FAULT: a delivered face with its grain ratio driven past the band
        # -- the smooth, grainless patch verify-delivered.py:18-19 names as "the
        # failure mode this project already rejected twice".
        smooth, keep2, clear2 = _synth_face(clear_amp=0.0)
        low, _i, _o = grain_ratio(smooth, keep2, clear2)
        if low is None or low >= band[0]:
            findings.append("grain: a grainless patch measured %r, which is not "
                            "past the band floor %.3f -- the fault is not the one "
                            "named" % (low, band[0]))
        bad_faces = [{"file": "grainless.png", "grain_ratio": low}]
        fired = grain_check(bad_faces, band, accepted=False)
        if fired["status"] != "fail":
            findings.append("grain: a grainless delivered face did not fire")
        if fired["exit_on_fail"] != kc.EXIT_TOLERANCE:
            findings.append("grain: fired with exit %d, expected 22"
                            % fired["exit_on_fail"])
        if kc.pick_exit([fired["exit_on_fail"]]) != kc.EXIT_TOLERANCE:
            findings.append("grain: pick_exit did not return 22")
        if fired.get("tolerance") != TOLERANCE:
            findings.append("grain: the check does not carry the tolerance key "
                            "compute_consumable reads")
        if grain_check(bad_faces, band, accepted=True)["status"] != "pass":
            findings.append("grain: --accept-grain did not accept the hit")
        # And the ceiling, which a floor-only test leaves untested: a patch
        # NOISIER than its parchment is the other end of the same band.
        rough, keep3, clear3 = _synth_face(clear_amp=40.0 * 3.0)
        high, _i, _o = grain_ratio(rough, keep3, clear3)
        if high is None or high <= band[1]:
            findings.append("grain: an over-grained patch measured %r, which is "
                            "not past the band ceiling %.3f" % (high, band[1]))
        if grain_check([{"file": "rough.png", "grain_ratio": high}], band,
                       accepted=False)["status"] != "fail":
            findings.append("grain: the band ceiling does not fire")
        # An unmeasurable face is RECORDED, never a hit.
        if grain_check([{"file": "tiny.png", "grain_ratio": None}], band,
                       accepted=False)["status"] != "pass":
            findings.append("grain: an unmeasurable face was scored as a hit")

    # ---- the §5.11 package ------------------------------------------------
    if "external" in wanted or "third-party-transfer" in wanted:
        cfg = {"slug": "selftest", "guard": {"data_root": "x/", "forbidden": []},
               "fonts": {
                   "search_roots": ["images-ko/fonts/"],
                   "title": {"postscript_name": "Sandoll", "sha256": None,
                             "redistributable": False},
                   "icons": {"postscript_name": "ArkhamIcons",
                             "sha256": None, "redistributable": False},
                   "body": {"postscript_name": "HakgyoansimBareondotumB",
                            "sha256": None, "redistributable": True}}}
        # A synthetic "commercial" face and a synthetic redistributable one. The
        # bytes never have to be a real font: clause (c) is a DIGEST comparison,
        # which is the property under test.
        commercial = b"OTTO-not-a-real-font-commercial" * 8
        allowed = b"OTTO-not-a-real-font-hakgyoansim" * 8
        cfg["fonts"]["title"]["sha256"] = kc.sha256_bytes(commercial)
        cfg["fonts"]["body"]["sha256"] = kc.sha256_bytes(allowed)

        tmp = tempfile.mkdtemp(prefix="kz-erase-selftest.")
        try:
            def fresh():
                slices_manifest = {"records": [{"filename": "71001.png"},
                                               {"filename": "71002.png"}]}
                masks_manifest = {"masks": [{"file": "71001.png"},
                                            {"file": "71002.png"}]}
                root = tempfile.mkdtemp(dir=tmp)
                readme_sha, _payload = _fake_package(root, cfg, slices_manifest,
                                                     masks_manifest)
                return root, slices_manifest, masks_manifest, readme_sha

            # --- the clean control. A clause that fires on a correct package
            # --- proves nothing about the fault that follows it.
            root, sm, mm, readme_sha = fresh()
            clean = assert_package(root, cfg, sm, mm, readme_sha)
            if len(clean) != 5:
                findings.append("external: %d clauses evaluated, expected the "
                                "five of §5.11" % len(clean))
            for c in clean:
                if not c["ok"]:
                    findings.append("external: clause (%s) %s failed on a CLEAN "
                                    "package: %s" % (c["clause"], c["name"],
                                                     c["detail"][:1]))

            if "external" in wanted:
                # --- FAULT 1: one font file. Planted under a `.png` name inside
                # --- `slices/`, so the EXTENSION net (b) cannot see it and the
                # --- path formulation cannot either -- it is under the package
                # --- root, which is under <run_dir>, which is what makes the
                # --- path rule vacuous. Only the DIGEST comparison catches it.
                root, sm, mm, readme_sha = fresh()
                planted = os.path.join(root, "slices", "71001.png")
                with open(planted, "wb") as handle:
                    handle.write(commercial)
                got = assert_package(root, cfg, sm, mm, readme_sha)
                if not _fired(got, "c"):
                    findings.append("external: a commercial font planted under a "
                                    ".png name did not fire clause (c)")
                if _fired(got, "b"):
                    findings.append("external: clause (b) claimed to catch a font "
                                    "with no font extension -- that is not what "
                                    "an extension net can do")
                if not os.path.realpath(planted).startswith(
                        os.path.realpath(root) + os.sep):
                    findings.append("external: the planted font is not under the "
                                    "package root, so this case does not "
                                    "demonstrate the path rule's vacuity")

                # (c) is SELECTIVE: the redistributable body face is allowed.
                root, sm, mm, readme_sha = fresh()
                with open(os.path.join(root, "slices", "71001.png"), "wb") as handle:
                    handle.write(allowed)
                got = assert_package(root, cfg, sm, mm, readme_sha)
                if _fired(got, "c"):
                    findings.append("external: clause (c) refused a "
                                    "redistributable: true font -- it must be "
                                    "selective, which is the point of the flag")

                # The extension net (b) still has to fire on the easy case.
                root, sm, mm, readme_sha = fresh()
                open(os.path.join(root, "leak.ttf"), "wb").close()
                got = assert_package(root, cfg, sm, mm, readme_sha)
                if not _fired(got, "b"):
                    findings.append("external: a .ttf did not fire clause (b)")
                if not _fired(got, "e"):
                    findings.append("external: a file named by neither manifest "
                                    "did not fire clause (e)")

                # --- FAULT 2: one Hangul string. Built from CODEPOINTS and not
                # --- written as a literal, for `build-package.py:490-492`'s
                # --- reason: this file's own text is scanned by the same class
                # --- of check, and a literal here would make that claim false.
                root, sm, mm, readme_sha = fresh()
                with open(os.path.join(root, README_NAME), "a",
                          encoding="utf-8") as handle:
                    handle.write("\n" + chr(0xD55C) + chr(0xAE00) + "\n")
                got = assert_package(root, cfg, sm, mm, readme_sha)
                if not _fired(got, "a"):
                    findings.append("external: Hangul in a text file did not fire "
                                    "clause (a)")
                if not _fired(got, "e"):
                    findings.append("external: the edited README did not fire "
                                    "clause (e) -- its digest carve-out is "
                                    "supposed to be the bytes just written, not "
                                    "its name")

                # (d): a deny-listed basename.
                root, sm, mm, readme_sha = fresh()
                with open(os.path.join(root, "slices", "manifest.json"), "w",
                          encoding="utf-8") as handle:
                    handle.write("{}\n")
                got = assert_package(root, cfg, sm, mm, readme_sha)
                if not _fired(got, "d"):
                    findings.append("external: a deny-listed basename did not "
                                    "fire clause (d)")

                # (e): a manifest-named file whose bytes drifted.
                root, sm, mm, readme_sha = fresh()
                with open(os.path.join(root, "masks", "71002.png"), "ab") as handle:
                    handle.write(b"drift")
                got = assert_package(root, cfg, sm, mm, readme_sha)
                if not _fired(got, "e"):
                    findings.append("external: a drifted digest did not fire "
                                    "clause (e)")

                # Coverage is DISCLOSED, not swallowed: a non-redistributable
                # role with a null digest is one clause (c) cannot speak for.
                blind_cfg = json.loads(json.dumps(cfg))
                blind_cfg["fonts"]["title"]["sha256"] = None
                root, sm, mm, readme_sha = fresh()
                got = assert_package(root, blind_cfg, sm, mm, readme_sha)
                c_clause = [c for c in got if c["clause"] == "c"][0]
                if not any("COVERAGE" in d for d in c_clause["detail"]):
                    findings.append("external: a non-redistributable role with no "
                                    "digest was not disclosed as a coverage gap")

            # --- FAULT 3 (the CONSENTS row): all five assertions pass and the
            # --- flag is absent. Exit 26, and THE PACKAGE PATH MUST NOT BE
            # --- PRINTED -- which is a negative, so it is asserted against the
            # --- exact lines rather than against a print statement.
            root, sm, mm, readme_sha = fresh()
            passing = assert_package(root, cfg, sm, mm, readme_sha)
            if any(not c["ok"] for c in passing):
                findings.append("third-party-transfer: the consent case needs a "
                                "package on which all five assertions pass")
            lines, code, printed = transfer_decision(root, consented=False)
            blob = "\n".join(lines)
            if code != kc.EXIT_CONSENT:
                findings.append("third-party-transfer: exit %d, expected 26" % code)
            if printed:
                findings.append("third-party-transfer: the un-consented path "
                                "reported the path as printed")
            if os.path.abspath(root) in blob or root in blob:
                findings.append("third-party-transfer: THE PACKAGE PATH WAS "
                                "PRINTED without the consent flag")
            if os.path.basename(root) in blob:
                findings.append("third-party-transfer: the package directory name "
                                "leaked into the un-consented output")
            # The disclosure of step 4 must already have been produced, and it
            # must not name a path either.
            dlines = disclosure_lines(disclosure_of(root), passing)
            dblob = "\n".join(dlines)
            if os.path.basename(root) in dblob:
                findings.append("third-party-transfer: the step-4 disclosure "
                                "names the package directory")
            if "files" not in dblob or "by extension" not in dblob:
                findings.append("third-party-transfer: the step-4 disclosure does "
                                "not carry the inventory §5.11 requires")
            # And with the flag: the path IS printed, so the control is a control.
            lines2, code2, printed2 = transfer_decision(root, consented=True)
            if code2 != kc.EXIT_OK or not printed2:
                findings.append("third-party-transfer: the consented path did not "
                                "return 0 with the path printed")
            if os.path.abspath(root) not in "\n".join(lines2):
                findings.append("third-party-transfer: the consented path did not "
                                "print the package path")
            if kc.pick_exit([kc.EXIT_TOLERANCE, kc.EXIT_CONSENT]) != kc.EXIT_CONSENT:
                findings.append("third-party-transfer: pick_exit({22,26}) must be "
                                "26 -- the human question is named first")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- the inpaint tier -------------------------------------------------
    if "engine" in wanted:
        def fires(label, code, fn):
            try:
                fn()
            except kc.KzRefusal as exc:
                if exc.code != code:
                    findings.append("%s: expected exit %d, got %d"
                                    % (label, code, exc.code))
                return
            findings.append("%s: did not refuse (expected exit %d)" % (label, code))

        fires("engine (unset)", kc.EXIT_PRECONDITION, lambda: resolve_lama(env={}))
        fires("engine (absent)", kc.EXIT_PRECONDITION,
              lambda: resolve_lama(env={LAMA_PYTHON_ENV:
                                        "/nonexistent/.venv-lama/bin/python"}))
        # The version assertion, run against a REAL interpreter that is real and
        # is the wrong one: /usr/bin/python3 is 3.9.6, and a stage that accepted
        # it would fail deep inside a checkpoint load instead of here.
        if os.path.exists(kc.PLATFORM_PYTHON):
            fires("engine (wrong version)", kc.EXIT_PRECONDITION,
                  lambda: resolve_lama(env={LAMA_PYTHON_ENV: kc.PLATFORM_PYTHON}))
        # A bad wall-clock knob COLLAPSES rather than widens a bound, so it is
        # replaced by the default and logged (CLAUDE.md's rule for the nightly's
        # own knobs, kept here for the same reason).
        log = []
        value, rejected = _positive_int_seconds("5m", LAMA_TIMEOUT_DEFAULT_S,
                                                LAMA_TIMEOUT_ENV, log)
        if value != LAMA_TIMEOUT_DEFAULT_S or not rejected or not log:
            findings.append("engine: a non-integer timeout was not rejected, "
                            "defaulted and logged")
        if _positive_int_seconds("900", 1, LAMA_TIMEOUT_ENV, [])[0] != 900:
            findings.append("engine: a valid timeout was not honoured")
        if not ENGINES == ("lama", "external"):
            findings.append("engine: the engine list moved")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ===========================================================================
# 7. CLI
# ===========================================================================

_SUMMARY = {
    "grain": "a grainless delivered face fires the `grain` tolerance at exit 22, "
             "an over-grained one fires the band's ceiling, --accept-grain "
             "accepts both, an in-band face does not fire and an unmeasurable "
             "one is recorded rather than scored",
    "external": "a commercial font planted under a .png name is caught by the "
                "DIGEST clause (c) that the vacuous path formulation cannot "
                "catch, a redistributable font is let through, Hangul in a text "
                "file fires (a), and (b)/(d)/(e) each fire on their own fault",
    "third-party-transfer": "with all five assertions passing and the flag "
                            "absent, the run exits 26 with the inventory "
                            "disclosed and THE PACKAGE PATH NOT PRINTED; with "
                            "the flag it exits 0 and prints it",
    "engine": "the inpaint venv is resolved from ~/.config/koreanize/env, and an "
              "unset key, an absent file and a real interpreter at the WRONG "
              "version each refuse at exit 13",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_erase.py",
        description="koreanize stage `erase` -- inpaint the English text out via "
                    "the LaMa venv, or assemble the §5.11 external package "
                    "(design §6 step 13).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--engine", choices=list(ENGINES), default="lama",
                        help="lama runs the venv inpainter; external assembles "
                             "the package for a third party (§5.11)")
    parser.add_argument("--accept-grain", action="store_true",
                        help="acknowledge a delivered face outside the recorded "
                             "grain band (kz_common.TOLERANCES row `grain`, "
                             "exit 22)")
    parser.add_argument("--consent-third-party-transfer", action="store_true",
                        dest="consent_third_party_transfer",
                        help="authorize third-party transfer of an assembled "
                             "--engine external package (kz_common.CONSENTS row "
                             "`third-party-transfer`, exit 26). koreanize "
                             "transmits nothing itself; this flag is what makes "
                             "the package path printable.")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and assert into <run_dir>/dry-run/erase/")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-assert an existing delivered/ (or, with --engine "
                             "external, the newest assembled package) without "
                             "running the inpainter or assembling anything")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the progress summary. It does NOT suppress "
                             "the §5.11 disclosure.")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_erase --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-21s %s" % (name, _SUMMARY[name]))
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    mode = "build"
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"

    dest = run_dir
    if args.dry_run:
        cfg = kz.load_scenario(os.path.join(run_dir, "scenario.json"))
        dest = kz.assert_dry_run_dest(cfg, STAGE,
                                      os.path.join(run_dir, "dry-run", STAGE))

    report = run_erase(run_dir, engine=args.engine, mode=mode,
                       accept_grain=args.accept_grain,
                       consent_transfer=args.consent_third_party_transfer,
                       quiet=args.quiet, verify_only=args.verify_only)
    path = kc.write_report(report, dest)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize erase -- %s (engine %s)" % (report["slug"], args.engine))
        print("  faces           : %d" % counts["faces"])
        if args.engine == "lama":
            print("  delivered       : %d (inpainter rc %s)"
                  % (counts.get("delivered", 0), counts.get("inpainter_rc")))
            print("  grain           : %d measured, %d outside the band "
                  "(min %s, max %s)"
                  % (counts.get("grain_measured", 0),
                     counts.get("grain_outside_band", 0),
                     counts.get("grain_min"), counts.get("grain_max")))
        else:
            print("  package         : %d file(s), %d assertion(s) failed"
                  % (counts.get("package_files", 0),
                     counts.get("assertions_failed", 0)))
        for check in report["checks"]:
            print("  %-30s: %s  %s" % (check["name"], check["status"],
                                       "; ".join(check["detail"][:3])))
        print("  verdict         : %s (exit %d)"
              % (report["verdict"], report["exit_code"]))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
