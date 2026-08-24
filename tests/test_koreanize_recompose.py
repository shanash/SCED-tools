"""kz_recompose.py + kz_upload.py -- the art chain's publication end (design §2 item 39).

WHAT §2 ITEM 39 ASKS FOR, AND WHY EACH HALF IS HERE
    "the totality proof; the imported MAX_ATLAS_BYTES boundary -- including the
    boundary case, since a bound with no boundary case is a bound nobody has run."

    The TOTALITY PROOF is what turns "A1 and A2 both passed" into "no output
    pixel is unconstrained". It is tested three ways: as arithmetic on a list of
    cell indices, as a planner refusal on a grid that does not tile its canvas,
    and end to end on a real recomposed PNG where the count of asserted cells is
    compared against the sheet's capacity.

    The BOUNDARY is tested at threshold-1 / threshold / cap / cap+1, and the cap
    itself is asserted to BE `compose-card-atlas.py`'s attribute rather than to
    equal a number written here -- a test that pinned 67108864 would pass on the
    local copy §5.4 forbids and would be the very drift the import prevents.

NO CORPUS AND NO NETWORK, ANYWHERE IN THIS FILE
    Every image is generated: the largest is 24x12 px. `kz_upload` is driven
    entirely through its two injection points -- a stub uploader that returns an
    argv-derived `upload-map.json`, and a `StubVerifier` built from dicts -- so
    the four-way verification is exercised at both outcomes on every check
    without a bucket, a credential or a socket.

    That is not a convenience. §5.9 skips corpus-dependent cases when the golden
    manifest's inputs are absent, and N-4 states outright that the per-module
    suites "are what remain green underneath" a golden skip. A suite that needed
    the 2.1 GB tree would have zero passing evidence on the machine where it
    matters most.
"""

import json
import os
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

import kz_common as kc
import kz_config as kz
import kz_recompose as kr
import kz_upload as ku

from tests.koreanize_sandbox import default_sandbox

RECOMPOSE_PY = os.path.join(kc.PACKAGE_DIR, "kz_recompose.py")
UPLOAD_PY = os.path.join(kc.PACKAGE_DIR, "kz_upload.py")

#: The synthetic sheet every end-to-end case uses: 3x2 cells of 8x6 px, four of
#: the six cells used. Four and not six, because a sheet whose used set IS its
#: capacity makes A2 vacuous -- there would be no unused cell to assert the
#: English pixels of, which is exactly the population the base-canvas design
#: exists to protect.
NUM_WIDTH, NUM_HEIGHT = 3, 2
CELL_W, CELL_H = 8, 6
USED = (0, 1, 3, 5)


# ---------------------------------------------------------------------------
# 1. Every --selftest fault as a pytest case
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fault", kr.FAULTS)
def test_recompose_selftest_fault_passes(fault):
    assert kr.selftest(fault=fault, verbose=False) == []


@pytest.mark.parametrize("fault", ku.FAULTS)
def test_upload_selftest_fault_passes(fault):
    assert ku.selftest(fault=fault, verbose=False) == []


def test_selftest_faults_are_total_over_the_predicates():
    """Asserted LITERALLY rather than derived, for the reason §4.4 gives the
    rule->fixture map: a set discovered from the code under test grows and
    shrinks silently with it."""
    assert kr.FAULTS == ("cap-import", "totality", "atlas-size", "a1", "a2", "grid")
    assert ku.FAULTS == ("delegation", "four-way", "urls-from-map", "cross-pack",
                         "nickname-pin", "png-only", "live")


@pytest.mark.parametrize("script", [RECOMPOSE_PY, UPLOAD_PY])
def test_selftest_runs_as_a_subprocess_and_exits_zero(script):
    """`koreanize.sh selftest` invokes the CLI, so the CLI path is exercised and
    not only the function."""
    proc = subprocess.run([sys.executable, script, "--selftest"],
                          capture_output=True, text=True)
    assert proc.returncode == kc.EXIT_OK, proc.stdout + proc.stderr


@pytest.mark.parametrize("module", [kr, ku])
def test_unknown_fault_refuses_at_usage(module):
    with pytest.raises(kc.KzRefusal) as excinfo:
        module.selftest(fault="no-such-fault", verbose=False)
    assert excinfo.value.code == kc.EXIT_USAGE


# ---------------------------------------------------------------------------
# 2. The AI contract -- both stages are forbidden, and it is mechanical
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", ["recompose", "upload"])
def test_stage_is_a_forbidden_ai_stage(stage):
    assert stage in kz.STAGES
    assert kc.AI_CONTRACT.get(stage) is False


@pytest.mark.parametrize("stage,preds", [("recompose", ("slice", "typeset")),
                                         ("upload", ("recompose",))])
def test_predecessors_match_the_design(stage, preds):
    assert kz.PREDECESSORS[stage] == preds


@pytest.mark.parametrize("stage", ["recompose", "upload"])
def test_a_build_report_carrying_an_ai_block_is_refused(stage):
    """§3.2's enforcement half: `kc.write_report` refuses BEFORE the write, so a
    violating report never reaches disk."""
    cfg = {"ai": {"required_stages": [], "forbidden_stages": [stage]}}
    report = kc.new_report(stage, "s", mode="build", ai={"used": True})
    with pytest.raises(kc.KzRefusal) as excinfo:
        kc.assert_ai_contract(report, cfg=cfg)
    assert excinfo.value.code == kc.EXIT_GUARD


# ---------------------------------------------------------------------------
# 3. THE TOTALITY PROOF (§5.4)
# ---------------------------------------------------------------------------

def test_totality_derives_the_complement_rather_than_trusting_a_declaration():
    """The unused set is DERIVED, which is the whole difference between this and
    a comment: a declared complement can agree with itself while leaving a cell
    out of both populations."""
    used, unused, findings = kr.totality([0, 1, 3], 3, 2)
    assert findings == []
    assert used == [0, 1, 3]
    assert unused == [2, 4, 5]
    assert len(used) + len(unused) == 3 * 2


@pytest.mark.parametrize("num_width,num_height,used", [
    (1, 1, [0]),          # the single_card sheet: recompose is a no-op, A2 vacuous
    (3, 2, []),           # nothing used: every cell must be asserted by A2
    (3, 2, [0, 1, 2, 3, 4, 5]),   # everything used: every cell asserted by A1
    (8, 5, [0, 17, 39]),
    (12, 12, list(range(0, 144, 7))),
])
def test_totality_identity_holds_on_every_shape(num_width, num_height, used):
    got_used, unused, findings = kr.totality(used, num_width, num_height)
    assert findings == []
    assert len(got_used) + len(unused) == num_width * num_height
    assert set(got_used).isdisjoint(unused)


def test_totality_fires_on_a_cell_outside_the_grid():
    """An out-of-range cell means a tile was placed somewhere the canvas does not
    have, and the identity is what notices."""
    _used, _unused, findings = kr.totality([0, 1, 99], 3, 2)
    assert any("outside 0..5" in f for f in findings)


def test_totality_fires_on_a_duplicated_cell():
    """Two tiles claiming one cell: one cell of the output would be asserted
    twice and another never."""
    _used, _unused, findings = kr.totality([0, 1, 1], 3, 2)
    assert any("duplicate" in f for f in findings)


def test_totality_is_reachable_as_an_identity_failure():
    """A predicate that cannot fail is not a predicate. `totality` refuses to
    report a complement that would not close, even when handed one directly."""
    _used, _unused, findings = kr.totality([0, 1, 2, 3, 4, 5, 6], 3, 2)
    assert findings, "a 7-cell used set on a 6-cell grid produced no finding"


def test_a_grid_that_does_not_tile_its_canvas_is_refused_before_any_pixel(tmp_path):
    """P4. Without it the identity is TRUE of a canvas with an unexamined strip
    down its right edge -- the cells would still count to capacity."""
    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (25, 12)).save(str(assets / "x.png"))
    inventory = {"atlases": [{"atlas_id": "s", "english_url": "u",
                              "local": "<run_dir>/assets/x.png",
                              "grid": {"num_width": 3, "num_height": 2},
                              "pixels": [25, 12], "cell_pixels": [8, 6]}]}
    sheets, findings = kr.plan_sheets(
        inventory, [{"sheet": "s", "cell": 0, "row": 0, "col": 0,
                     "filename": "a.png"}], str(tmp_path))
    assert sheets == []
    assert any("does not tile" in f for f in findings)


# ---------------------------------------------------------------------------
# 4. THE IMPORTED CAP, AND ITS BOUNDARY (§5.4, §7)
# ---------------------------------------------------------------------------

def test_the_cap_is_the_imported_attribute_and_not_a_number_written_here():
    """Identity with `compose-card-atlas.py`'s attribute, never equality with a
    literal. A test pinning the value would pass on exactly the local copy §5.4
    forbids, which is the drift the import exists to prevent."""
    cca = kr.load_compose_card_atlas()
    assert kr.max_atlas_bytes() == cca.MAX_ATLAS_BYTES
    # And there is no module-level constant of that name here to shadow it.
    assert not hasattr(kr, "MAX_ATLAS_BYTES")


def test_the_module_carries_no_literal_copy_of_the_cap():
    """The other half of the same claim, and the one a value test cannot make."""
    cap = kr.max_atlas_bytes()
    with open(RECOMPOSE_PY, "r", encoding="utf-8") as handle:
        src = handle.read()
    assert ("%d * 1024 * 1024" % (cap // (1024 * 1024))) not in src
    assert str(cap) not in src


def test_an_absent_compose_card_atlas_is_a_named_precondition_not_a_fallback():
    """A fallback is the one behaviour that must not exist here: it would let the
    cap and the content-addressed URL diverge quietly."""
    kr._CCA["mod"], kr._CCA["path"] = None, None
    try:
        with pytest.raises(kc.KzRefusal) as excinfo:
            kr.max_atlas_bytes(os.path.join(os.sep, "nonexistent", "cca.py"))
        assert excinfo.value.code == kc.EXIT_PRECONDITION
    finally:
        kr._CCA["mod"], kr._CCA["path"] = None, None


def test_the_url_scheme_comes_from_the_same_import():
    """§7's R2-hosting row: the cap and the URL are two halves of ONE publication
    contract, so they must come from one place or they can disagree."""
    cca = kr.load_compose_card_atlas()
    digest = "a" * 64
    assert kr.face_url_for_sha(digest) == cca.face_url_for_sha(digest)
    assert kr.r2_key_for_sha(digest) == cca.r2_key_for_sha(digest)


def _cap_and_threshold():
    cap = kr.max_atlas_bytes()
    return cap, int(cap * kr.ATLAS_SIZE_BAND)


@pytest.mark.parametrize("offset,in_band,over_cap", [
    ("threshold-1", False, False),
    ("threshold", True, False),
    ("cap-1", True, False),
    ("cap", True, False),
    ("cap+1", True, True),
])
def test_atlas_size_boundary(offset, in_band, over_cap):
    """THE BOUNDARY CASE §2 item 39 asks for, on both bounds.

    `threshold` is INCLUSIVE: with a strict `>` a file sitting exactly on the
    threshold reports clean and the next byte of PNG entropy makes it fire, which
    an operator reads as a flapping check rather than as a file on the line.
    `cap` is EXCLUSIVE: an atlas of exactly MAX_ATLAS_BYTES is publishable.
    """
    cap, threshold = _cap_and_threshold()
    value = {"threshold-1": threshold - 1, "threshold": threshold,
             "cap-1": cap - 1, "cap": cap, "cap+1": cap + 1}[offset]
    band = kr.atlas_size_band(value, cap)
    assert band["in_band"] is in_band
    assert band["over_cap"] is over_cap


def test_the_named_selftest_fault_is_a_synthetic_atlas_at_61_mib():
    """`kc.TOLERANCES`'s `atlas-size` row names its fault; this is that fault,
    run as an ordinary case so the row's evidence survives without the CLI."""
    row = [r for r in kc.TOLERANCES if r.name == kr.TOLERANCE][0]
    assert row.stage == "recompose"
    assert row.module == "kz_recompose.py"
    assert row.flag == "--accept-atlas-size"
    assert row.exit_code == kc.EXIT_TOLERANCE
    assert "61 MiB" in row.selftest_fault

    cap, _threshold = _cap_and_threshold()
    band = kr.atlas_size_band(61 * 1024 * 1024, cap)
    assert band["in_band"] and not band["over_cap"]


def test_the_flag_records_the_decision_and_never_moves_the_measurement():
    """An accepted run and a clean run are different facts and must not read the
    same in the receipt, so `status` reports the MEASUREMENT and `accepted`
    reports the HUMAN."""
    cap, _t = _cap_and_threshold()
    fired, over = kr.atlas_size_check([("a.png", 61 * 1024 * 1024)], cap, False)
    assert fired["status"] == "fail" and fired["accepted"] is False and not over
    accepted, _o = kr.atlas_size_check([("a.png", 61 * 1024 * 1024)], cap, True)
    assert accepted["status"] == "fail" and accepted["accepted"] is True


def test_the_accepted_block_is_what_clears_compute_consumable():
    """§3.6's tolerance conjunct, evaluated on this stage's own check dict."""
    cap, _t = _cap_and_threshold()
    check, _o = kr.atlas_size_check([("a.png", 61 * 1024 * 1024)], cap, True)
    cfg = {"ai": {"required_stages": [], "forbidden_stages": ["recompose"]}}
    base = {"stage": "recompose", "mode": "build", "exit_code": kc.EXIT_OK,
            "checks": [check]}
    ok, _why = kc.compute_consumable(dict(base, accepted={kr.TOLERANCE: True}),
                                     cfg=cfg)
    assert ok is True
    blocked, why = kc.compute_consumable(dict(base, accepted={}), cfg=cfg)
    assert blocked is False and kr.TOLERANCE in why


def test_the_flag_never_clears_an_atlas_above_the_cap():
    """The band has an acceptance path; the cap does not. Offering one would be
    offering to accept a file R2 and TTS will not serve."""
    cap, _t = _cap_and_threshold()
    _check, over = kr.atlas_size_check([("big.png", cap + 1)], cap, True)
    assert over and "no --accept-*" in over[0]


def test_the_atlas_size_row_is_no_longer_pending():
    """The phase axis of §4.1: a row whose owning module does not exist is
    COUNTED as pending. This module is that module."""
    pending = kc.pending_rows()["TOLERANCES"]
    assert kr.TOLERANCE not in [r.name for r in pending]


# ---------------------------------------------------------------------------
# 5. The end-to-end synthetic run
# ---------------------------------------------------------------------------

def _seed_run_dir(box, used=USED, seed=3):
    """Write the artifacts `recompose` reads, all synthetic and all tiny.

    `slice` and `typeset` do not exist yet (§6 steps 11 and 14), so their
    outputs are constructed here to the contract `kz_recompose`'s docstring
    declares. That contract is the test's subject as much as the pixels are: if
    a later `kz_slice.py` emits a different record shape, the P1 refusal names
    the field rather than letting a tile land in a plausible cell.
    """
    run_dir = box.run_dir
    rng = np.random.RandomState(seed)
    width, height = NUM_WIDTH * CELL_W, NUM_HEIGHT * CELL_H

    assets = os.path.join(run_dir, "assets")
    os.makedirs(assets, exist_ok=True)
    base = rng.randint(0, 256, (height, width, 3)).astype(np.uint8)
    base_path = os.path.join(assets, "base.png")
    Image.fromarray(base, mode="RGB").save(base_path, format="PNG")

    typeset = os.path.join(run_dir, "typeset")
    os.makedirs(typeset, exist_ok=True)
    records = []
    for cell in used:
        row, col = divmod(cell, NUM_WIDTH)
        name = "cell%02d.png" % cell
        tile = rng.randint(0, 256, (CELL_H, CELL_W, 3)).astype(np.uint8)
        Image.fromarray(tile, mode="RGB").save(os.path.join(typeset, name),
                                               format="PNG")
        # THE SHAPE `kz_slice.py` ACTUALLY EMITS: `atlas_id` and `english_url`,
        # and no `sheet` field at all. The Midwinter record's slice-atlases.py
        # wrote a shape id instead, so `recompose` resolves all three rather than
        # imposing one -- the naming stays a `slice` decision (§5.4).
        records.append({"atlas_id": "3x2-face", "cell": cell, "row": row,
                        "col": col, "filename": name, "side": "face",
                        "english_url": "https://example.invalid/en/atlas.png",
                        "arkham_id": "710%02d" % cell,
                        "num_width": NUM_WIDTH, "num_height": NUM_HEIGHT,
                        "cell_pixels": [CELL_W, CELL_H]})

    with open(os.path.join(run_dir, "atlas-inventory.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"atlases": [{
            "atlas_id": "3x2-face", "side": "face",
            "english_url": "https://example.invalid/en/atlas.png",
            "local": "<run_dir>/assets/base.png",
            "grid": {"num_width": NUM_WIDTH, "num_height": NUM_HEIGHT},
            "pixels": [width, height], "cell_pixels": [CELL_W, CELL_H],
            "bytes": os.path.getsize(base_path),
            "sha256": kc.sha256_file(base_path),
            "cells_used": len(used), "packs": ["Korean - Campaigns"],
            "single_card": False}]}, handle)

    os.makedirs(os.path.join(run_dir, "slices"), exist_ok=True)
    with open(os.path.join(run_dir, "slices", "manifest.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"records": records,
                   "counts": {"face_slices_emitted": len(records),
                              "back_slices_emitted": 0}}, handle)

    # The two upstream reports. `recompose` refuses at 13 on a predecessor that
    # is not consumable, so a suite that omitted these would be testing the
    # refusal rather than the stage.
    for stage in kr.UPSTREAM:
        with open(kc.report_path(run_dir, stage, "build"), "w",
                  encoding="utf-8") as handle:
            json.dump({"schema_version": kc.SCHEMA_VERSION, "stage": stage,
                       "slug": box.cfg["slug"], "mode": "build", "verdict": "PASS",
                       "exit_code": 0, "consumable": True,
                       "consumable_blocked_by": None, "gate": None, "seed": None},
                      handle)
    return run_dir


@pytest.fixture
def seeded(tmp_path):
    box = default_sandbox(tmp_path)
    _seed_run_dir(box)
    return box


def test_end_to_end_recompose_asserts_every_cell_of_the_canvas(seeded):
    """The totality proof AS THE STAGE REPORTS IT: `cells_asserted` equals the
    sheet's capacity, so A1 and A2 between them examined every cell."""
    report = kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    assert report["exit_code"] == kc.EXIT_OK, report["checks"]
    counts = report["counts"]
    assert counts["cells_used"] == len(USED)
    assert counts["cells_unused"] == NUM_WIDTH * NUM_HEIGHT - len(USED)
    assert counts["cells_used"] + counts["cells_unused"] == counts["cells_total"]
    assert counts["cells_asserted"] == counts["cells_total"]
    assert counts["pixels_asserted"] == counts["cells_total"] * CELL_W * CELL_H
    assert counts["a1_violations"] == 0 and counts["a2_violations"] == 0
    totality = [c for c in report["checks"] if c["id"] == "R0"][0]
    assert totality["status"] == "pass"


def test_the_unused_cells_keep_english_pixels_by_construction(seeded):
    """The point of the base canvas. Read off the WRITTEN FILE: every unused cell
    is byte-for-byte the English atlas's, and at least one used cell is not."""
    kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    out = os.path.join(seeded.run_dir, "atlases", "3x2-face.png")
    got, _s, _m, _f = kr.read_rgb(out)
    base, _s, _m, _f = kr.read_rgb(os.path.join(seeded.run_dir, "assets",
                                                "base.png"))
    for cell in range(NUM_WIDTH * NUM_HEIGHT):
        row, col = divmod(cell, NUM_WIDTH)
        box = kr.cell_box(row, col, CELL_W, CELL_H)
        same = np.array_equal(got[box[1]:box[3], box[0]:box[2]],
                              base[box[1]:box[3], box[0]:box[2]])
        assert same is (cell not in USED), "cell %d" % cell


def test_the_report_carries_the_tool_triple_golden_compares(seeded):
    """§5.8 step (4): `golden` reads each stage report's own tool{python, pil,
    numpy} block and compares it to the fixture's, BY REPORT and never by import.
    An art-tier stage that left pil/numpy null would be read as "this tier
    declares no opinion" and the comparison would silently pass."""
    report = kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    tool = report["tool"]
    assert tool["python"] == "%d.%d.%d" % sys.version_info[:3]
    assert tool["pil"] and tool["numpy"]


def test_verify_only_re_proves_a_tree_it_did_not_build(seeded):
    kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    report = kr.run_recompose(seeded.run_dir, mode="verify-only", verify_only=True,
                              workspace=seeded.root)
    assert report["exit_code"] == kc.EXIT_OK
    assert report["counts"]["cells_asserted"] == NUM_WIDTH * NUM_HEIGHT


def test_a_corrupted_used_cell_on_disk_fires_a1_at_20(seeded):
    """A1 is asserted FROM DISK, so a fault planted in the written file after the
    build is what the re-assertion is for. Planted in the array it would prove
    only that `Image.paste` works."""
    kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    out = os.path.join(seeded.run_dir, "atlases", "3x2-face.png")
    arr, _s, _m, _f = kr.read_rgb(out)
    arr = arr.copy()
    arr[1, 1] = (arr[1, 1].astype(np.int16) ^ 0xFF).astype(np.uint8)   # cell 0
    kr.write_png(out, Image.fromarray(arr, mode="RGB"))
    report = kr.run_recompose(seeded.run_dir, mode="verify-only", verify_only=True,
                              workspace=seeded.root)
    assert report["exit_code"] == kc.EXIT_RULE_A
    assert report["counts"]["a1_violations"] == 1
    assert report["counts"]["a2_violations"] == 0


def test_a_corrupted_unused_cell_on_disk_fires_a2_at_21(seeded):
    """The English pixels are ASSERTED, not merely inherited."""
    kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    out = os.path.join(seeded.run_dir, "atlases", "3x2-face.png")
    arr, _s, _m, _f = kr.read_rgb(out)
    arr = arr.copy()
    unused = sorted(set(range(NUM_WIDTH * NUM_HEIGHT)) - set(USED))[0]
    row, col = divmod(unused, NUM_WIDTH)
    box = kr.cell_box(row, col, CELL_W, CELL_H)
    arr[box[1] + 1, box[0] + 1] = (
        arr[box[1] + 1, box[0] + 1].astype(np.int16) ^ 0xFF).astype(np.uint8)
    kr.write_png(out, Image.fromarray(arr, mode="RGB"))
    report = kr.run_recompose(seeded.run_dir, mode="verify-only", verify_only=True,
                              workspace=seeded.root)
    assert report["exit_code"] == kc.EXIT_RULE_B
    assert report["counts"]["a2_violations"] == 1
    assert report["counts"]["a1_violations"] == 0


def test_the_upload_manifest_is_the_publication_boundary(seeded):
    """Written only on exit 0, and carrying the IMPORTED cap and the imported URL
    scheme. A manifest naming an atlas that failed A1 would hand `upload` a file
    this stage has already refused."""
    report = kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    path = os.path.join(seeded.run_dir, "atlases", "upload-manifest.json")
    assert report["exit_code"] == kc.EXIT_OK and os.path.exists(path)
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["max_atlas_bytes"] == kr.max_atlas_bytes()
    atlas = manifest["atlases"][0]
    assert atlas["face_url"] == kr.face_url_for_sha(atlas["atlas_sha256"])
    assert atlas["r2_key"] == kr.r2_key_for_sha(atlas["atlas_sha256"])
    # The uploader's own consumption contract: it reads these three by name.
    for field in ("atlas_id", "atlas_sha256", "face_url"):
        assert field in atlas


def test_no_manifest_is_written_when_an_assertion_failed(seeded):
    kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    path = os.path.join(seeded.run_dir, "atlases", "upload-manifest.json")
    os.unlink(path)
    out = os.path.join(seeded.run_dir, "atlases", "3x2-face.png")
    arr, _s, _m, _f = kr.read_rgb(out)
    arr = arr.copy()
    arr[1, 1] = (arr[1, 1].astype(np.int16) ^ 0xFF).astype(np.uint8)
    kr.write_png(out, Image.fromarray(arr, mode="RGB"))
    report = kr.run_recompose(seeded.run_dir, workspace=seeded.root,
                              mode="verify-only", verify_only=True)
    assert report["exit_code"] != kc.EXIT_OK
    assert not os.path.exists(path)


def test_an_upstream_that_is_not_consumable_refuses_at_13(seeded):
    """72 means NOTHING RAN; 13 means a stage ran far enough to read its input and
    refused on it (§4.2). This is the module's own half of the predecessor rule."""
    path = kc.report_path(seeded.run_dir, "typeset", "build")
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    report["consumable"] = False
    report["consumable_blocked_by"] = "gate.status == pending"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)
    with pytest.raises(kc.KzRefusal) as excinfo:
        kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    assert excinfo.value.code == kc.EXIT_PRECONDITION
    assert "typeset" in str(excinfo.value)


def test_a_write_outside_the_run_dir_refuses_at_4_before_any_read(tmp_path):
    """Exit 4 and "nothing was read" is literally true, because the guard runs
    before the first open."""
    box = default_sandbox(tmp_path)
    outside = os.path.join(box.root, "SCED-downloads", "atlases")
    with pytest.raises(kc.KzRefusal) as excinfo:
        kr.run_recompose(box.run_dir, workspace=box.root, atlases_dir=outside)
    assert excinfo.value.code == kc.EXIT_GUARD


# ---------------------------------------------------------------------------
# 6. `upload` -- delegation, the four-way verification, and the map
# ---------------------------------------------------------------------------

def _stub_uploader(bucket="tts-ahcg-assets", mutate=None):
    """A stand-in for `upload-atlases-to-r2.py` that PUTs nothing.

    It reads the very manifest the real tool reads and writes the very
    `upload-map.json` the real tool writes, so the delegation contract is
    exercised in both directions. `mutate` lets a case change what the "uploader"
    recorded -- which is how a prediction can be driven to disagree with an
    observation without inventing a second code path.
    """
    def run(argv):
        manifest_path = argv[argv.index("--manifest") + 1]
        output_path = argv[argv.index("--output") + 1]
        staging = argv[argv.index("--staging-dir") + 1]
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        uploads = []
        for atlas in manifest["atlases"]:
            url = atlas["face_url"]
            uploads.append({"atlas_id": atlas["atlas_id"],
                            "sha256": atlas["atlas_sha256"],
                            "key": ku._key_from_url(url), "url": url,
                            "size": os.path.getsize(os.path.join(staging,
                                                                 atlas["file"])),
                            "skipped": False})
        document = {"bucket": bucket, "uploads": uploads}
        if mutate:
            mutate(document)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        return 0, "stub uploaded %d object(s)" % len(uploads)
    return run


def _stub_verifier_for(run_dir, bucket="tts-ahcg-assets", corrupt=None):
    """A `StubVerifier` built from the atlases actually on disk."""
    manifest_path = os.path.join(run_dir, "atlases", "upload-manifest.json")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    heads, bodies = {}, {}
    for atlas in manifest["atlases"]:
        path = os.path.join(run_dir, "atlases", atlas["file"])
        with open(path, "rb") as handle:
            body = handle.read()
        if corrupt:
            body = corrupt(body)
        heads[ku._key_from_url(atlas["face_url"])] = os.path.getsize(path)
        bodies[atlas["face_url"]] = body
    return ku.StubVerifier(heads=heads, bodies=bodies, bucket=bucket)


@pytest.fixture
def uploadable(seeded):
    report = kr.run_recompose(seeded.run_dir, workspace=seeded.root)
    assert report["exit_code"] == kc.EXIT_OK
    kc.write_report(report, seeded.run_dir)
    return seeded


def test_the_rehearsal_is_the_default_and_touches_nothing(uploadable):
    """§4.1: rehearsal is the default for every stage that writes outside
    <run_dir>, so forgetting --live is a rehearsal rather than a refusal."""
    report, urls = ku.run_upload(uploadable.run_dir, workspace=uploadable.root,
                                 mirror=False)
    assert report["mode"] == "dry-run"
    assert report["exit_code"] == kc.EXIT_OK
    assert urls is None
    assert os.path.exists(ku.plan_path(uploadable.run_dir))


def test_a_live_run_with_no_persisted_plan_refuses_at_13(uploadable):
    """Without the artifact, "the executed set equals the plan the banner
    printed" is a statement one process makes about itself (§4.1)."""
    with pytest.raises(kc.KzRefusal) as excinfo:
        ku.run_upload(uploadable.run_dir, live=True, assume_yes=True,
                      workspace=uploadable.root, mirror=False,
                      uploader=_stub_uploader(),
                      verifier=ku.StubVerifier())
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_the_live_run_delegates_verifies_and_writes_the_map(uploadable):
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(),
        verifier=_stub_verifier_for(uploadable.run_dir))
    assert report["exit_code"] == kc.EXIT_OK, report["checks"]

    # The delegation actually happened, through the shipped tool's argv.
    argv = report["results"]["delegation"]["argv"]
    assert any(a.endswith("upload-atlases-to-r2.py") for a in argv)
    for flag in ("--manifest", "--staging-dir", "--output"):
        assert flag in argv

    # All four checks, all green, on every object.
    assert report["results"]["verified"] == {
        "head_object": True, "public_get": True, "png_magic": True,
        "sha256_roundtrip": True, "objects": 1}

    # And the map is a map.
    assert urls["source"] == "upload-map.json"
    assert urls["substitutions"] == {
        "https://example.invalid/en/atlas.png": urls["atlases"][0]["korean_url"]}


def test_atlas_urls_takes_the_url_the_uploader_recorded_not_the_prediction(
        uploadable):
    """THE defect §6 step 15 names. Every URL here is derivable before the
    upload, so a predicted map looks correct right up until the two differ."""
    other = ("https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/"
             "images/sha256/ff/" + "f" * 64 + ".png")

    def mutate(document):
        document["uploads"][0]["url"] = other
        document["uploads"][0]["key"] = ku._key_from_url(other)

    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    report, _urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(mutate=mutate),
        verifier=_stub_verifier_for(uploadable.run_dir))
    # The disagreement is REPORTED (U3) and fails the run -- the observation
    # wins, and a predicted map would have shipped silently instead.
    u3 = [c for c in report["checks"] if c["id"] == "U3"][0]
    assert u3["status"] == "fail"
    assert report["exit_code"] == kc.EXIT_NETWORK
    assert any(other in d for d in u3["detail"])


def test_an_atlas_with_no_row_in_the_map_is_refused_rather_than_derived(uploadable):
    def mutate(document):
        document["uploads"] = []

    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(mutate=mutate),
        verifier=ku.StubVerifier())
    u2 = [c for c in report["checks"] if c["id"] == "U2"][0]
    assert u2["status"] == "fail"
    assert report["exit_code"] == kc.EXIT_NETWORK
    assert urls is None


def test_a_stale_object_at_a_content_addressed_key_fails_the_round_trip(uploadable):
    """V4. Under content addressing a wrong digest means the key was derived from
    different bytes, which is a different first move from "not a PNG at all"."""
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    report, _urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(),
        verifier=_stub_verifier_for(uploadable.run_dir,
                                    corrupt=lambda body: body + b"\x00"))
    u4 = [c for c in report["checks"] if c["id"] == "U4"][0]
    assert u4["status"] == "fail"
    assert any("sha256 round-trip" in d for d in u4["detail"])
    assert report["exit_code"] == kc.EXIT_NETWORK
    assert report["results"]["verified"]["sha256_roundtrip"] is False


def test_an_uploader_failure_is_74_and_never_70(uploadable):
    """N-6: `daily-sync-local.sh:109` owns 70, and an operator reading a 70 beside
    the nightly's would derive the wrong first move."""
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=lambda argv: (4, "UploadError: PUT failed"),
        verifier=ku.StubVerifier())
    assert report["exit_code"] == kc.EXIT_NETWORK
    assert report["exit_code"] != 70
    assert urls is None


def test_the_banner_is_derived_from_the_r2_destination_set(uploadable, capsys):
    """`kz_langpack.requires_live()` tests membership in `guard.write_roots`,
    which is a set of FILESYSTEM prefixes; R2 is not a path, so `upload` would
    otherwise be the one write-outside stage with no banner (§1.1(b))."""
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    ku.run_upload(uploadable.run_dir, live=True, assume_yes=True,
                  workspace=uploadable.root, mirror=False,
                  uploader=_stub_uploader(),
                  verifier=_stub_verifier_for(uploadable.run_dir))
    err = capsys.readouterr().err
    assert "LIVE WRITE -- koreanize upload" in err
    assert "tts-ahcg-assets" in err
    assert "CONTENT-ADDRESSED" in err


def test_the_map_declares_the_scenario_nickname_pin(uploadable):
    """C11's authority. The rule existed in prose and had no verifier before
    step 15; `verify` reads it from this artifact."""
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    _report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(),
        verifier=_stub_verifier_for(uploadable.run_dir))
    pin = urls["nickname_pin"]
    assert pin["literal"] == "Scenario"
    assert pin["source"] == "SCED/src/mythos/MythosArea.ttslua:209"
    assert pin["korean_name_goes_in"] == "Description"
    assert pin["policy"] == "never_localize"


def test_the_map_declares_the_cross_pack_duplication_rule_and_the_orphan_policy(
        uploadable):
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    _report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(),
        verifier=_stub_verifier_for(uploadable.run_dir))
    cross = urls["cross_pack"]
    assert cross["rule"] == "one korean_url per english_url across every pack"
    assert "DUPLICATE" in cross["duplication"]
    assert urls["orphans"]["policy"] == "never_deleted_by_koreanize"


def test_the_scenario_nickname_pin_is_still_load_bearing_in_the_mod(workspace_root):
    """The citation is checked against the tree, not trusted. If `MythosArea`
    stops branching on the literal, the pin is a rule with no reason and this
    test is where that is noticed -- read-only: the mod repo is the one tree
    koreanize never writes to (§7)."""
    path = os.path.join(workspace_root, "SCED", "src", "mythos",
                        "MythosArea.ttslua")
    if not os.path.exists(path):
        pytest.skip("SCED is not checked out at %s" % path)
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert '"%s"' % ku.SCENARIO_NICKNAME in source


def test_a_non_png_asset_is_refused_at_13_with_the_gap_named(tmp_path):
    """N-8: both uploaders glob `*.png` and hardcode `ContentType: image/png`
    (`upload-atlases-to-r2.py:76,131`), so the campaign-guide PDF path is out of
    scope and says so rather than uploading a PDF as an image."""
    (tmp_path / "guide.pdf").write_bytes(b"%PDF-1.4\n")
    _entries, findings = ku.build_plan(
        {"atlases": [{"file": "guide.pdf", "atlas_sha256": "d",
                      "face_url": "https://x.r2.dev/k.pdf"}]}, str(tmp_path))
    assert any("out of scope" in f and "N-8" in f for f in findings)


@pytest.mark.parametrize("case,expect_ok", [
    ("clean", True),
    ("absent-from-bucket", False),
    ("wrong-size", False),
    ("edge-refused", False),
    ("html-served-200", False),
    ("stale-bytes", False),
])
def test_four_way_verification_both_directions(case, expect_ok):
    """Each of the four checks driven to both outcomes, with no socket.

    V3 and V4 are separate although V4 subsumes V3 arithmetically, because their
    failures are DIAGNOSTIC of different layers: a body that is not a PNG is an
    edge or bucket problem; a PNG with the wrong digest is a stale object at a
    content-addressed key.
    """
    body = ku.PNG_MAGIC + b"atlas-bytes"
    digest = ku.sha256_bytes(body)
    entry = ku._stub_entry(digest, size=len(body))
    heads = {entry["key"]: len(body)}
    bodies = {entry["url"]: body}
    if case == "absent-from-bucket":
        heads = {}
    elif case == "wrong-size":
        heads = {entry["key"]: len(body) + 1}
    elif case == "edge-refused":
        bodies = {}
    elif case == "html-served-200":
        bodies = {entry["url"]: b"<html><body>404</body></html>"}
    elif case == "stale-bytes":
        bodies = {entry["url"]: ku.PNG_MAGIC + b"other-bytes"}

    checks, findings = ku.verify_four_ways(
        entry, ku.StubVerifier(heads=heads, bodies=bodies))
    assert list(checks) == ["head_object", "public_get", "png_magic",
                            "sha256_roundtrip"]
    assert all(checks.values()) is expect_ok
    assert (findings == []) is expect_ok


def test_the_cross_pack_rule_refuses_two_korean_urls_for_one_english_one():
    """A card in two packs carries the SAME FaceURL in both (N-10), and C3
    asserts byte identity of it everywhere it appears (§5.8) -- so a map offering
    two would make C3 unsatisfiable by construction."""
    shared = {"atlas_id": "a", "english_url": "https://en/x.png",
              "korean_url": "https://ko/1.png", "packs": ["Campaigns", "PlayerCards"]}
    assert ku.cross_pack_findings([shared]) == []
    split = dict(shared, atlas_id="b", korean_url="https://ko/2.png")
    assert any("DUPLICATED" in f for f in ku.cross_pack_findings([shared, split]))


@pytest.mark.parametrize("field", ["sheet", "atlas_id", "english_url"])
def test_a_record_may_name_its_sheet_by_any_of_the_three_keys(tmp_path, field):
    """`kz_slice.py` writes `atlas_id` and `english_url` and no `sheet`; the
    Midwinter record's `slice-atlases.py` wrote a shape id. All three resolve, so
    the naming stays a `slice` decision rather than one `recompose` imposes."""
    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (24, 12)).save(str(assets / "x.png"))
    url = "https://example.invalid/en/atlas.png"
    inventory = {"atlases": [{"atlas_id": "3x2-face", "english_url": url,
                              "local": "<run_dir>/assets/x.png",
                              "grid": {"num_width": 3, "num_height": 2},
                              "pixels": [24, 12], "cell_pixels": [8, 6]}]}
    value = {"sheet": "3x2-face", "atlas_id": "3x2-face", "english_url": url}[field]
    record = {field: value, "cell": 0, "row": 0, "col": 0, "filename": "a.png"}
    sheets, findings = kr.plan_sheets(inventory, [record], str(tmp_path))
    assert findings == []
    assert len(sheets) == 1 and sheets[0]["used_cells"] == [0]


def test_a_record_naming_no_sheet_at_all_is_refused(tmp_path):
    sheets, findings = kr.plan_sheets(
        {"atlases": []}, [{"cell": 0, "row": 0, "col": 0, "filename": "a.png"}],
        str(tmp_path))
    assert sheets == []
    assert any("names no sheet" in f for f in findings)


def test_the_typeset_tile_path_comes_from_the_typeset_report_when_it_has_one(
        seeded):
    """`kz_typeset` names its output from the MASK manifest and records the slice
    it came from separately, so `typeset/<slice filename>` is a convention rather
    than a contract. The report carries the join and `recompose` reads it."""
    run_dir = seeded.run_dir
    renamed = os.path.join(run_dir, "typeset", "renamed-cell00.png")
    os.rename(os.path.join(run_dir, "typeset", "cell00.png"), renamed)
    path = kc.report_path(run_dir, "typeset", "build")
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    report["results"] = {"faces": [{"slice_file": "cell00.png",
                                    "typeset_path": "typeset/renamed-cell00.png"}]}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle)

    index = kr.load_typeset_index(run_dir)
    assert index == {"cell00.png": "typeset/renamed-cell00.png"}
    built = kr.run_recompose(run_dir, workspace=seeded.root)
    assert built["exit_code"] == kc.EXIT_OK, built["checks"]


def test_a_sheet_whose_identity_is_no_usable_filename_is_refused(tmp_path):
    """The output name is a FILENAME, so it is validated as one. An id carrying a
    separator would write outside `atlases/` while every guard above it was
    looking at the directory rather than at the file."""
    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (24, 12)).save(str(assets / "x.png"))
    inventory = {"atlases": [{"atlas_id": "../escape", "english_url": "u",
                              "local": "<run_dir>/assets/x.png",
                              "grid": {"num_width": 3, "num_height": 2},
                              "pixels": [24, 12], "cell_pixels": [8, 6]}]}
    sheets, findings = kr.plan_sheets(
        inventory, [{"atlas_id": "../escape", "cell": 0, "row": 0, "col": 0,
                     "filename": "a.png"}], str(tmp_path))
    assert sheets == []
    assert any("no usable filename" in f for f in findings)
    assert kr.safe_sheet_name("a/b") is None
    assert kr.safe_sheet_name(".hidden") is None
    assert kr.safe_sheet_name("8x5-face") == "8x5-face"


def test_the_map_is_written_where_kz_verify_opens_it(uploadable):
    """`kz_verify` is stdlib-tier and opens `<run_dir>/atlas-urls.json` directly
    (`kz_verify.py:121`), and `golden` compares atlas sha256s against it inside a
    scratch run dir that has no lock at all (§5.8 step 5). §3.1 promotes the file
    to the receipt tier so it SURVIVES the gitignored run dir -- an argument for
    the receipt existing, not against the run-dir copy."""
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(),
        verifier=_stub_verifier_for(uploadable.run_dir))
    path = os.path.join(uploadable.run_dir, "atlas-urls.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as handle:
        on_disk = json.load(handle)
    assert on_disk["urls"] == urls["urls"]
    assert [w["path"] for w in report["write_set"]] == [
        os.path.relpath(path, uploadable.root)]


def test_urls_and_substitutions_are_one_mapping_under_two_names(uploadable):
    """`repoint` and the record's shipped map say `substitutions`; `kz_verify`
    opens `urls` (`kz_verify.py:137,187`). Emitting only one leaves the other
    consumer with no subject -- C1's cross-scope clause would stay inert in v1,
    which is the "reports a pass it cannot fail" shape §5.8 rewrote C3 to avoid."""
    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    _report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(),
        verifier=_stub_verifier_for(uploadable.run_dir))
    assert urls["urls"] == urls["substitutions"]
    assert urls["urls"], "the mapping is empty, so both consumers have no subject"


def test_kz_verify_can_read_the_map_this_stage_writes(uploadable):
    """The producer and its consumer, joined in one case rather than in prose.

    `owned_urls()` is what C1's cross-scope clause ranges over and what §3.1 calls
    "the sole authority for the N-7 orphan set"; it returns the KOREAN urls, so a
    map keyed the other way round would give it the English ones and the clause
    would fire on every donor.
    """
    import kz_verify as kv

    ku.run_upload(uploadable.run_dir, workspace=uploadable.root, mirror=False)
    _report, urls = ku.run_upload(
        uploadable.run_dir, live=True, assume_yes=True,
        workspace=uploadable.root, mirror=False,
        uploader=_stub_uploader(),
        verifier=_stub_verifier_for(uploadable.run_dir))
    korean = set(urls["substitutions"].values())

    subject = kv.Subject.__new__(kv.Subject)
    subject.atlas_urls = urls
    assert subject.owned_urls() == korean
    assert korean.isdisjoint(urls["substitutions"])
