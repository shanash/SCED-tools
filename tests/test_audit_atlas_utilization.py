"""
pytest suite for audit-atlas-utilization.py (design korean-atlas-utilization-audit).

All tests drive the script via subprocess to exercise exit codes and the full
read -> aggregate -> emit -> sha256-verify path. Fixtures are single CSV index
files under tests/fixtures/korean-atlas-utilization-audit/, mirroring the
conventions in test_build_korean_card_index.py.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_DIR = TESTS_DIR / "fixtures" / "korean-atlas-utilization-audit"

AUDIT_SCRIPT = SCRIPT_DIR / "audit-atlas-utilization.py"

R2_FRAG = "pub-05b4fa32b44341d797f5c66d59384724.r2.dev"
STEAM_FRAG = "steamusercontent-a.akamaihd.net"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(input_csv: Path, out_dir: Path, extra_args: list[str] | None = None):
    cmd = [
        sys.executable, str(AUDIT_SCRIPT),
        "--input", str(input_csv),
        "--output-dir", str(out_dir),
    ] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True)


def _fixture(name: str) -> Path:
    return FIXTURE_DIR / name


def _report(out_dir: Path) -> dict:
    return json.loads((out_dir / "utilization_report.json").read_text(encoding="utf-8"))


def _manifest(out_dir: Path) -> dict:
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


def _candidates(out_dir: Path) -> dict:
    return json.loads((out_dir / "repack_candidates.json").read_text(encoding="utf-8"))


def _atlas_by_frag(report: dict, frag: str) -> dict:
    matches = [a for a in report["atlases"] if frag in a["face_url"]]
    assert len(matches) == 1, f"expected exactly one atlas matching {frag!r}, got {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# Clean run + summary
# ---------------------------------------------------------------------------


def test_clean_run_exit_zero(tmp_path):
    out = tmp_path / "out"
    result = _run(_fixture("clean_index.csv"), out)
    assert result.returncode == 0, result.stderr

    s = _report(out)["summary"]
    assert s["atlas_count"] == 4
    assert s["multi_cell_count"] == 3
    assert s["single_image_count"] == 1
    assert s["total_cells"] == 211
    assert s["used_cells"] == 5
    assert s["wasted_cells"] == 206
    assert s["card_ref_count"] == 6
    assert s["reprint_overlap_cells"] == 1
    assert s["consolidation_target_sheets"] == 1
    assert s["anomaly_cells_excluded"] == 0
    assert _report(out)["excluded"]["steam_face_atlases"] == 1


def test_util_and_consolidation_math(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    s = _report(out)["summary"]
    # 5 used / 211 total -> 2.37%; ceil(5/70) -> 1 sheet.
    assert s["overall_utilization_pct"] == round(100 * 5 / 211, 2) == 2.37
    assert s["consolidation_target_sheets"] == 1


def test_distinct_cell_counting_reprint(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    aaa = _atlas_by_frag(_report(out), "AAA.jpg")
    # cells {0, 30} -> distinct 2, but 3 card refs (587400 reprints cell 0).
    assert aaa["used_cells"] == 2
    assert aaa["used_cell_indices"] == [0, 30]
    assert aaa["card_ref_count"] == 3
    assert aaa["wasted_cells"] == 68
    assert len(aaa["reprint_overlaps"]) == 1
    assert aaa["reprint_overlaps"][0]["cell_index"] == 0
    assert aaa["reprint_overlaps"][0]["count"] == 2


def test_single_image_1x1_zero_waste(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    bbb = _atlas_by_frag(_report(out), "BBB.jpg")
    assert bbb["single_image"] is True
    assert bbb["total_cells"] == 1
    assert bbb["used_cells"] == 1
    assert bbb["wasted_cells"] == 0
    assert bbb["utilization_pct"] == 100.0
    assert bbb["empty_cell_indices"] == []
    # single-image atlases are excluded from the multi-cell buckets.
    s = _report(out)["summary"]
    assert sum(s["utilization_buckets"].values()) == s["multi_cell_count"] == 3


def test_steam_face_excluded(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    report = _report(out)
    assert all(STEAM_FRAG not in a["face_url"] for a in report["atlases"])
    assert report["excluded"]["steam_face_atlases"] == 1


def test_query_param_preserved_distinct_atlases(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    report = _report(out)
    urls = [a["face_url"] for a in report["atlases"] if "CCC.jpg" in a["face_url"]]
    assert sorted(urls) == [
        f"https://{R2_FRAG}/ugc/CCC.jpg?v=2",
        f"https://{R2_FRAG}/ugc/CCC.jpg?v=3",
    ]


def test_back_rows_ignored_counts_input(tmp_path):
    """FACE-only phase: back columns are never aggregated, only tallied.

    The clean fixture has empty back columns, so back_rows_ignored is 0 — this
    pins the FACE-only invariant (no atlas record is ever a back atlas).
    """
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    assert _report(out)["excluded"]["back_rows_ignored"] == 0
    assert _report(out)["scope"] == {"role": "face", "source": "R2"}


# ---------------------------------------------------------------------------
# Anomaly / malformed / grid handling
# ---------------------------------------------------------------------------


def test_anomaly_excluded_from_cell_math(tmp_path):
    out = tmp_path / "out"
    result = _run(_fixture("anomaly_index.csv"), out)
    assert result.returncode == 1, result.stderr  # warnings -> exit 1
    report = _report(out)
    ddd = _atlas_by_frag(report, "DDD.jpg")
    # only cell 0 is in-bounds; cell 31 (OOB) and the invalid row are excluded.
    assert ddd["used_cells"] == 1
    assert ddd["used_cell_indices"] == [0]
    assert ddd["wasted_cells"] == 7  # 8 - 1, never negative
    assert ddd["total_cells"] == 8
    assert len(ddd["anomaly_cells"]) == 2
    codes = {a["code"] for a in ddd["anomaly_cells"]}
    assert codes == {"cell_out_of_bounds", "card_id_invalid"}
    assert len(report["excluded"]["anomaly_rows"]) == 2


def test_malformed_row_handled(tmp_path):
    out = tmp_path / "out"
    result = _run(_fixture("malformed_index.csv"), out)
    assert result.returncode == 1, result.stderr
    report = _report(out)
    assert report["excluded"]["malformed_rows"] == 1
    # the one valid R2 atlas is still reported.
    assert _atlas_by_frag(report, "FFF.jpg")["used_cells"] == 1


def test_grid_mismatch_flagged(tmp_path):
    out = tmp_path / "out"
    result = _run(_fixture("gridmismatch_index.csv"), out)
    assert result.returncode == 1, result.stderr
    ggg = _atlas_by_frag(_report(out), "GGG.jpg")
    assert "grid_mismatch" in ggg["flags"]
    # first row's grid (10x7) is used for the math.
    assert ggg["num_width"] == 10
    assert ggg["num_height"] == 7
    assert _report(out)["excluded"]["grid_mismatch_atlases"] == 1


def test_oversize_grid_demoted_to_invalid(tmp_path):
    """A corrupt grid whose nw*nh exceeds MAX_GRID_CELLS is demoted to
    grid_invalid (dims zeroed) instead of materializing set(range(total)) — the
    run completes instead of hanging/OOMing on ~1e10 cells (W1 DoS guard).
    """
    out = tmp_path / "out"
    result = _run(_fixture("oversize_index.csv"), out)
    assert result.returncode == 1, result.stderr  # grid_invalid -> warning exit
    report = _report(out)
    assert report["excluded"]["grid_invalid_atlases"] == 1
    hhh = _atlas_by_frag(report, "HHH.jpg")
    assert "grid_invalid" in hhh["flags"]
    assert hhh["total_cells"] == 0   # dims zeroed; no range(total) blowup
    assert hhh["used_cells"] == 0
    assert hhh["empty_cell_indices"] == []
    # the lone ref is routed to anomaly_cells (not counted as a used card).
    assert hhh["card_ref_count"] == 0
    assert len(hhh["anomaly_cells"]) == 1
    assert hhh["anomaly_cells"][0]["code"] == "grid_invalid"
    assert hhh["anomaly_cells"][0]["capacity"] == 0


# ---------------------------------------------------------------------------
# Empty / zero-R2 edge cases
# ---------------------------------------------------------------------------


def test_empty_input_header_only(tmp_path):
    out = tmp_path / "out"
    result = _run(_fixture("empty_index.csv"), out)
    assert result.returncode == 0, result.stderr
    s = _report(out)["summary"]
    assert s["atlas_count"] == 0
    assert s["consolidation_target_sheets"] == 0
    assert s["overall_utilization_pct"] == 0.0


def test_zero_r2_faces_steamonly(tmp_path):
    out = tmp_path / "out"
    result = _run(_fixture("steamonly_index.csv"), out)
    assert result.returncode == 0, result.stderr
    report = _report(out)
    assert report["summary"]["atlas_count"] == 0
    assert report["summary"]["consolidation_target_sheets"] == 0
    assert report["excluded"]["steam_face_atlases"] == 1


def test_missing_input_exit_2(tmp_path):
    out = tmp_path / "out"
    result = _run(tmp_path / "does-not-exist.csv", out)
    assert result.returncode == 2, result.stderr
    assert not (out / "utilization_report.json").exists()


# ---------------------------------------------------------------------------
# CSV quoting (output safety)
# ---------------------------------------------------------------------------


def test_csv_quoting_keeps_single_row(tmp_path):
    """A used_cell_indices field with embedded commas (e.g. "[0, 3, 12]") must
    stay one RFC-4180 record; the nasty card name in the input is read via
    DictReader and round-trips into the JSON cards list verbatim.
    """
    out = tmp_path / "out"
    assert _run(_fixture("quoting_index.csv"), out).returncode == 0

    with (out / "utilization_report.csv").open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    # header + exactly one atlas row, each with 14 fields.
    assert len(rows) == 2
    assert all(len(r) == 14 for r in rows)
    # used_cell_indices column (index 12) is a JSON list with commas, one field.
    assert rows[1][12] == "[0, 3, 12]"

    eee = _atlas_by_frag(_report(out), "EEE.jpg")
    assert eee["used_cells"] == 3
    names = {c["name"] for c in eee["cards"]}
    assert 'Knife, "the" Sharp\nBlade' in names


# ---------------------------------------------------------------------------
# Heatmap / candidates / flags
# ---------------------------------------------------------------------------


def test_html_flag_emits_sprite_heatmap(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out, ["--html"]).returncode == 0
    html = (out / "atlas_heatmap.html").read_text(encoding="utf-8")
    # CSS-sprite cropping: position/size applied per cell + lazy bg via data-bg.
    assert "backgroundPosition" in html
    assert "backgroundSize" in html
    assert "data-bg" in html
    assert "AAA.jpg" in html              # atlas URL embedded for the browser
    assert "atlas-cache" not in html      # no pre-cropped PNG pipeline
    assert ".png" not in html             # sprite uses the .jpg atlas directly
    assert "atlas_heatmap.html" in _manifest(out)["outputs"]


def test_no_html_by_default(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    assert not (out / "atlas_heatmap.html").exists()
    assert "atlas_heatmap.html" not in _manifest(out)["outputs"]


def test_candidates_threshold(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out,
                ["--candidate-threshold", "50"]).returncode == 0
    cand = _candidates(out)
    assert cand["criteria"]["max_utilization_pct"] == 50.0
    assert cand["criteria"]["exclude_single_image"] is True
    # AAA (2.86%), CCC?v=2 (1.43%), CCC?v=3 (1.43%) qualify; BBB (single) excluded.
    assert cand["candidate_count"] == 3
    assert all(c["utilization_pct"] < 50 for c in cand["candidates"])
    assert all("BBB.jpg" not in c["face_url"] for c in cand["candidates"])


def test_no_candidates_flag(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out, ["--no-candidates"]).returncode == 0
    assert not (out / "repack_candidates.json").exists()
    assert "repack_candidates.json" not in _manifest(out)["outputs"]


def test_sort_by_wasted_default(tmp_path):
    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out).returncode == 0
    wasted = [a["wasted_cells"] for a in _report(out)["atlases"]]
    assert wasted == sorted(wasted, reverse=True)


# ---------------------------------------------------------------------------
# Manifest / determinism / dry-run
# ---------------------------------------------------------------------------


def test_manifest_sha256_matches_outputs(tmp_path):
    import hashlib

    out = tmp_path / "out"
    assert _run(_fixture("clean_index.csv"), out, ["--html"]).returncode == 0
    manifest = _manifest(out)
    for name, decl in manifest["outputs"].items():
        actual = hashlib.sha256((out / name).read_bytes()).hexdigest()
        assert actual == decl["sha256"], name


def test_two_runs_csv_byte_identical(tmp_path):
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    assert _run(_fixture("clean_index.csv"), out1).returncode == 0
    assert _run(_fixture("clean_index.csv"), out2).returncode == 0
    assert ((out1 / "utilization_report.csv").read_bytes()
            == (out2 / "utilization_report.csv").read_bytes())

    def _norm(path: Path) -> str:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("generated_at", None)
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

    assert (_norm(out1 / "utilization_report.json")
            == _norm(out2 / "utilization_report.json"))


def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = _run(_fixture("clean_index.csv"), out, ["--dry-run"])
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == []


def test_dry_run_returns_warning_exit_on_anomaly(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = _run(_fixture("anomaly_index.csv"), out, ["--dry-run"])
    assert result.returncode == 1, result.stderr
    assert sorted(p.name for p in out.iterdir()) == []
