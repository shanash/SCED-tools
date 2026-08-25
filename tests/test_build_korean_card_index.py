"""
pytest suite for build-korean-card-index.py (design korean-player-card-image-atlas-inventory).

All tests use subprocess.run to exercise exit codes and sys.exit() paths.
Fixtures are copied into tmp_path for isolation, mirroring
test_korean_overrides_v3.py.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_DIR = TESTS_DIR / "fixtures" / "korean-player-card-index"

INDEX_SCRIPT = SCRIPT_DIR / "build-korean-card-index.py"
CLEAN_FIXTURES = FIXTURE_DIR / "clean"
ANOMALY_FIXTURES = FIXTURE_DIR / "anomalies"
GMNOTES_FIXTURES = FIXTURE_DIR / "gmnotes"
EMPTYDECK_FIXTURES = FIXTURE_DIR / "emptydeck"
GRID_FIXTURES = FIXTURE_DIR / "grid"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(source_dir: Path, out_dir: Path, extra_args: list[str] | None = None):
    cmd = [
        sys.executable, str(INDEX_SCRIPT),
        "--source-dir", str(source_dir),
        "--out-dir", str(out_dir),
    ] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True)


def _make_source(tmp_path: Path, *fixture_files: Path) -> Path:
    """Build an isolated source dir in tmp_path from individual fixture files."""
    src = tmp_path / "source"
    src.mkdir(parents=True, exist_ok=True)
    for f in fixture_files:
        shutil.copy(f, src / f.name)
    return src


def _copy_clean(tmp_path: Path) -> Path:
    src = tmp_path / "clean-source"
    shutil.copytree(CLEAN_FIXTURES, src)
    return src


def _read_index(out_dir: Path) -> dict:
    return json.loads((out_dir / "korean_card_index.json").read_text(encoding="utf-8"))


def _read_manifest(out_dir: Path) -> dict:
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


def _read_csv_rows(out_dir: Path) -> list[dict]:
    with (out_dir / "korean_card_index.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _cards_by_source(index: dict) -> dict[str, dict]:
    return {c["source_file"]: c for c in index["cards"]}


def _csv_by_source(rows: list[dict]) -> dict[str, dict]:
    return {r["source_file"]: r for r in rows}


# ---------------------------------------------------------------------------
# Clean run + counts
# ---------------------------------------------------------------------------


def test_clean_run_exit_zero(tmp_path):
    src = _copy_clean(tmp_path)
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 0, result.stderr

    index = _read_index(out)
    assert index["counts"]["cards_written"] == 6
    assert index["counts"]["json_files_scanned"] == 6
    assert index["counts"]["anomalies"] == 0
    assert index["anomalies"] == []


def test_csv_header_is_23_columns(tmp_path):
    src = _copy_clean(tmp_path)
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 0, result.stderr

    with (out / "korean_card_index.csv").open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    expected = [
        "name", "arkham_id", "card_id", "deck_key",
        "face_url", "face_source", "face_num_width", "face_num_height",
        "face_cell_index", "face_x", "face_y",
        "back_url", "back_source", "back_num_width", "back_num_height",
        "back_cell_index", "back_x", "back_y",
        "unique_back", "sideways", "pack",
        "source_file", "anomaly",
    ]
    assert rows[0] == expected
    assert len(rows[0]) == 23
    for r in rows:
        assert len(r) == 23


# ---------------------------------------------------------------------------
# Back branches
# ---------------------------------------------------------------------------


def test_unique_back_mirrors_face_grid(tmp_path):
    src = _make_source(tmp_path, CLEAN_FIXTURES / "UniqueBackCard.aaa001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 0, result.stderr

    card = _cards_by_source(_read_index(out))["UniqueBackCard.aaa001.json"]
    assert card["unique_back"] is True
    # face: cell 4, x 4, y 0 (535704 % 100 = 4, 10-wide)
    assert card["face"]["cell_index"] == 4
    assert card["face"]["x"] == 4
    assert card["face"]["y"] == 0
    # back mirrors the face grid
    assert card["back"]["single_image"] is False
    assert card["back"]["num_width"] == 10
    assert card["back"]["num_height"] == 7
    assert card["back"]["cell_index"] == 4
    assert card["back"]["x"] == 4
    assert card["back"]["y"] == 0


def test_shared_back_is_single_1x1_image(tmp_path):
    src = _make_source(tmp_path, CLEAN_FIXTURES / "SharedBackCard.aaa002.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 0, result.stderr

    card = _cards_by_source(_read_index(out))["SharedBackCard.aaa002.json"]
    assert card["unique_back"] is False
    assert card["back"]["single_image"] is True
    assert card["back"]["num_width"] == 1
    assert card["back"]["num_height"] == 1
    assert card["back"]["cell_index"] == 0
    assert card["back"]["x"] == 0
    assert card["back"]["y"] == 0


def test_no_back_url_yields_null_back(tmp_path):
    src = _make_source(tmp_path, CLEAN_FIXTURES / "NoBackURLCard.aaa003.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 0, result.stderr

    card = _cards_by_source(_read_index(out))["NoBackURLCard.aaa003.json"]
    assert card["back"] is None

    row = _csv_by_source(_read_csv_rows(out))["NoBackURLCard.aaa003.json"]
    assert row["back_url"] == ""
    assert row["back_source"] == ""
    assert row["back_num_width"] == ""
    assert row["back_num_height"] == ""
    assert row["back_cell_index"] == ""
    assert row["back_x"] == ""
    assert row["back_y"] == ""


def test_back_coordinate_disambiguation(tmp_path):
    """A shared 1x1 back writes a real 0 origin; a no-BackURL back writes empty.

    The two are distinguishable only by whether the JSON `back` object exists
    (null vs object), and in CSV the empty value is overloaded — so this also
    confirms the anomaly column is the disambiguator (both rows here are clean).
    """
    src = _make_source(
        tmp_path,
        CLEAN_FIXTURES / "SharedBackCard.aaa002.json",
        CLEAN_FIXTURES / "NoBackURLCard.aaa003.json",
    )
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 0, result.stderr

    rows = _csv_by_source(_read_csv_rows(out))
    shared = rows["SharedBackCard.aaa002.json"]
    noback = rows["NoBackURLCard.aaa003.json"]

    # Shared single-image back -> real origin cell 0/0/0.
    assert shared["back_cell_index"] == "0"
    assert shared["back_x"] == "0"
    assert shared["back_y"] == "0"
    assert shared["anomaly"] == ""

    # No-BackURL back -> empty back coordinates.
    assert noback["back_x"] == ""
    assert noback["back_y"] == ""
    assert noback["anomaly"] == ""

    cards = _cards_by_source(_read_index(out))
    assert cards["SharedBackCard.aaa002.json"]["back"] is not None
    assert cards["NoBackURLCard.aaa003.json"]["back"] is None


# ---------------------------------------------------------------------------
# Anomaly branches
# ---------------------------------------------------------------------------


def test_card_id_invalid_missing(tmp_path):
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "MissingCardId.cid001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["MissingCardId.cid001.json"]
    assert card["anomaly"] == "card_id_invalid"
    assert card["card_id"] is None
    # sentinel coords incl. cell_index (no usable CardID).
    assert card["face"]["cell_index"] is None
    assert card["face"]["x"] is None
    assert card["face"]["y"] is None


def test_card_id_invalid_non_integer(tmp_path):
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "NonIntCardId.cid002.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["NonIntCardId.cid002.json"]
    assert card["anomaly"] == "card_id_invalid"
    assert card["card_id"] is None
    assert card["face"]["cell_index"] is None


def test_card_id_nan_is_invalid_not_crash(tmp_path):
    """A NaN CardID token must keep-and-flag (card_id_invalid), never crash."""
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "NanCardId.cid003.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["NanCardId.cid003.json"]
    assert card["anomaly"] == "card_id_invalid"
    assert card["card_id"] is None
    assert card["face"]["cell_index"] is None
    assert card["face"]["x"] is None


def test_card_id_infinity_is_invalid_not_crash(tmp_path):
    """An Infinity CardID token must keep-and-flag (card_id_invalid), never crash."""
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "InfCardId.cid004.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["InfCardId.cid004.json"]
    assert card["anomaly"] == "card_id_invalid"
    assert card["card_id"] is None


def test_multi_deck_disambiguates_to_cardid_key(tmp_path):
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "MultiDeck.md001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["MultiDeck.md001.json"]
    assert card["anomaly"] == "multi_deck"
    # 920103 // 100 = 9201 -> re-selected away from the first key "0000".
    assert card["deck_key"] == "9201"
    # grid of the 9201 deck (10x7), cell 3.
    assert card["face"]["num_width"] == 10
    assert card["face"]["cell_index"] == 3
    assert card["face"]["x"] == 3
    assert card["face"]["y"] == 0


def test_deckkey_mismatch(tmp_path):
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "DeckKeyMismatch.dk001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["DeckKeyMismatch.dk001.json"]
    assert card["anomaly"] == "deckkey_mismatch"
    assert card["deck_key"] == "9999"
    # cell math still runs (cell 4 of a 10x7 grid).
    assert card["face"]["cell_index"] == 4
    assert card["face"]["x"] == 4


def test_cell_out_of_bounds_keeps_row_with_sentinel(tmp_path):
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "OutOfBounds.oob001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["OutOfBounds.oob001.json"]
    assert card["anomaly"] == "cell_out_of_bounds"
    # cell 31 kept raw (informational), x/y are the typed sentinel (null).
    assert card["face"]["cell_index"] == 31
    assert card["face"]["x"] is None
    assert card["face"]["y"] is None
    # UniqueBack=true mirrors the empty x/y sentinel onto the back.
    assert card["back"]["cell_index"] == 31
    assert card["back"]["x"] is None
    assert card["back"]["y"] is None

    # manifest anomaly entry carries the detail.
    manifest = _read_manifest(out)
    oob = [e for e in manifest["anomalies"] if e["code"] == "cell_out_of_bounds"]
    assert len(oob) == 1
    assert "capacity=8" in oob[0]["detail"]
    assert "4x2" in oob[0]["detail"]


def test_multi_code_anomaly_semicolon_joined(tmp_path):
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "MultiCode.mc001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    card = _cards_by_source(_read_index(out))["MultiCode.mc001.json"]
    assert card["anomaly"] == "multi_deck;deckkey_mismatch"

    row = _csv_by_source(_read_csv_rows(out))["MultiCode.mc001.json"]
    # CSV joins with ';' not ',' so the row stays a single 23-field record.
    assert row["anomaly"] == "multi_deck;deckkey_mismatch"
    assert "," not in row["anomaly"]


# ---------------------------------------------------------------------------
# Hard exits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", [
    "NonJsonGmnotes.gm001.json",
    "EmptyGmnotes.gm002.json",
    "NoIdGmnotes.gm003.json",
])
def test_gmnotes_violation_hard_exit_40(tmp_path, fixture_name):
    src = _make_source(tmp_path, GMNOTES_FIXTURES / fixture_name)
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 40, result.stderr
    # No output written on a hard exit.
    assert not (out / "korean_card_index.json").exists()


def test_empty_custom_deck_hard_exit_43(tmp_path):
    src = _make_source(tmp_path, EMPTYDECK_FIXTURES / "EmptyDeck.ed001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 43, result.stderr


def test_non_finite_grid_hard_exit_41(tmp_path):
    """A non-finite NumWidth/NumHeight is a clean grid invariant exit 41, not a
    traceback (the cell math is never reached)."""
    src = _make_source(tmp_path, GRID_FIXTURES / "NonFiniteGridWidth.grid001.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 41, result.stderr
    assert not (out / "korean_card_index.json").exists()


def test_missing_source_dir_hard_exit_43(tmp_path):
    src = tmp_path / "does-not-exist"
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 43, result.stderr


# ---------------------------------------------------------------------------
# CSV safety / sentinel round-trip
# ---------------------------------------------------------------------------


def test_csv_quoting_keeps_23_fields(tmp_path):
    """A Nickname with comma + double-quote + newline stays one 23-field row."""
    src = _make_source(tmp_path, CLEAN_FIXTURES / "QuotedNameCard.aaa004.json")
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 0, result.stderr

    with (out / "korean_card_index.csv").open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    # header + exactly one data row, each 23 fields.
    assert len(rows) == 2
    assert all(len(r) == 23 for r in rows)
    assert rows[1][0] == 'Knife, "the" Sharp\nBlade'


def test_sentinel_round_trip_csv_and_json(tmp_path):
    """Every anomaly row: CSV x/y empty, JSON x/y null (never 0)."""
    src = _make_source(
        tmp_path,
        ANOMALY_FIXTURES / "OutOfBounds.oob001.json",
        ANOMALY_FIXTURES / "MissingCardId.cid001.json",
        CLEAN_FIXTURES / "UniqueBackCard.aaa001.json",
    )
    out = tmp_path / "out"
    result = _run(src, out)
    assert result.returncode == 1, result.stderr

    cards = _cards_by_source(_read_index(out))
    rows = _csv_by_source(_read_csv_rows(out))

    for sf, card in cards.items():
        if card["anomaly"]:
            assert card["face"]["x"] is None
            assert card["face"]["y"] is None
            assert rows[sf]["face_x"] == ""
            assert rows[sf]["face_y"] == ""
        else:
            # clean row keeps a numeric origin.
            assert isinstance(card["face"]["x"], int)
            assert rows[sf]["face_x"] != "" or card["face"]["x"] == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def _normalize_index_json(text: str) -> str:
    """Strip the volatile generated_at field for byte-comparison."""
    data = json.loads(text)
    data.pop("generated_at", None)
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def test_two_runs_byte_identical(tmp_path):
    """Two runs over the same fixtures produce byte-identical CSV and (after
    normalizing generated_at) byte-identical JSON, proving the
    (arkham_id, card_id, source_file) sort is a total order regardless of
    rglob walk order. The clean set includes a (arkham_id, card_id) collision
    pair (CollidePair bbb001/bbb002) so the source_file tiebreak is exercised.
    """
    src = _copy_clean(tmp_path)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    r1 = _run(src, out1)
    r2 = _run(src, out2)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr

    csv1 = (out1 / "korean_card_index.csv").read_bytes()
    csv2 = (out2 / "korean_card_index.csv").read_bytes()
    assert csv1 == csv2

    json1 = (out1 / "korean_card_index.json").read_text(encoding="utf-8")
    json2 = (out2 / "korean_card_index.json").read_text(encoding="utf-8")
    assert _normalize_index_json(json1) == _normalize_index_json(json2)

    # The two CollidePair rows collide on (arkham_id, card_id); the source_file
    # tiebreak must order them deterministically.
    index = _read_index(out1)
    collide = [c for c in index["cards"] if c["arkham_id"] == "06275"]
    assert len(collide) == 2
    source_files = [c["source_file"] for c in collide]
    assert source_files == sorted(source_files)


def test_manifest_stable_except_volatile_fields(tmp_path):
    """manifest.json carries two run-volatile fields (its own generated_at and
    the recorded JSON-file sha256, volatile because the JSON embeds
    generated_at). The CSV sha256, counts, and anomalies list are stable.
    """
    src = _copy_clean(tmp_path)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    assert _run(src, out1).returncode == 0
    assert _run(src, out2).returncode == 0

    m1 = _read_manifest(out1)
    m2 = _read_manifest(out2)

    # CSV sha is stable across runs (no embedded timestamp).
    assert (m1["outputs"]["korean_card_index.csv"]["sha256"]
            == m2["outputs"]["korean_card_index.csv"]["sha256"])
    # counts + anomalies stable.
    assert m1["counts"] == m2["counts"]
    assert m1["anomalies"] == m2["anomalies"]
    # generated_at is a real ISO-8601 UTC timestamp.
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", m1["generated_at"])


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_writes_no_files_exit_zero_on_clean(tmp_path):
    src = _copy_clean(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    before = sorted(p.name for p in out.iterdir())
    result = _run(src, out, ["--dry-run"])
    assert result.returncode == 0, result.stderr
    after = sorted(p.name for p in out.iterdir())
    assert before == after == []


def test_dry_run_returns_warning_exit_on_anomaly(tmp_path):
    src = _make_source(tmp_path, ANOMALY_FIXTURES / "OutOfBounds.oob001.json")
    out = tmp_path / "out"
    out.mkdir()
    result = _run(src, out, ["--dry-run"])
    assert result.returncode == 1, result.stderr
    # No files created.
    assert sorted(p.name for p in out.iterdir()) == []


def test_dry_run_no_modify_existing_outputs(tmp_path):
    """A dry-run after a real run must not create or modify any output file."""
    src = _copy_clean(tmp_path)
    out = tmp_path / "out"
    assert _run(src, out).returncode == 0

    snapshot = {
        p.name: (p.stat().st_size, p.stat().st_mtime_ns)
        for p in out.iterdir()
    }
    result = _run(src, out, ["--dry-run"])
    assert result.returncode == 0, result.stderr
    after = {
        p.name: (p.stat().st_size, p.stat().st_mtime_ns)
        for p in out.iterdir()
    }
    assert snapshot == after


# ---------------------------------------------------------------------------
# --pretty no-op (CSV byte-stable)
# ---------------------------------------------------------------------------


def test_pretty_does_not_change_csv(tmp_path):
    src = _copy_clean(tmp_path)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    assert _run(src, out1).returncode == 0
    assert _run(src, out2, ["--pretty"]).returncode == 0
    assert ((out1 / "korean_card_index.csv").read_bytes()
            == (out2 / "korean_card_index.csv").read_bytes())


# ---------------------------------------------------------------------------
# --coverage  (design korean-coverage-metric)
#
# Appended section. Nothing above this line is edited or deleted: the two hard
# exits stay hard in strict mode and every pre-existing assertion still holds.
# ---------------------------------------------------------------------------

COVERAGE_FIXTURES = FIXTURE_DIR / "coverage"

PACK_SUBPATH = {
    "playercards": "language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI",
    "campaigns": "language-pack/Korean - Campaigns/Korean-Campaigns.KoreanC",
    "fancampaigns": "language-pack/Korean - Fan Campaigns/Korean-FanCampaigns.KoreanFC",
}


def _make_decomposed(tmp_path: Path, **packs) -> Path:
    """Materialise a decomposed root in tmp_path at the REAL pack subpaths.

    Keyword args map a pack name to the directory whose contents are copied
    into that pack's root (None creates the pack root empty). Using the real
    subpaths is what keeps --packs resolution under test identical to
    production; only the root differs.
    """
    root = tmp_path / "decomposed"
    for name, src in packs.items():
        dst = root / PACK_SUBPATH[name]
        dst.mkdir(parents=True, exist_ok=True)
        if src is None:
            continue
        for item in sorted(Path(src).iterdir()):
            if item.is_dir():
                shutil.copytree(item, dst / item.name)
            else:
                shutil.copy(item, dst / item.name)
    return root


def _run_packs(root: Path, out_dir: Path, packs: str, extra_args=None):
    cmd = [
        sys.executable, str(INDEX_SCRIPT),
        "--packs", packs,
        "--decomposed-root", str(root),
        "--out-dir", str(out_dir),
    ] + (extra_args or [])
    return subprocess.run(cmd, capture_output=True, text=True)


def _demo_root(tmp_path: Path) -> Path:
    """The canonical two-pack fixture tree used by most coverage tests."""
    return _make_decomposed(
        tmp_path,
        playercards=COVERAGE_FIXTURES / "playercards",
        campaigns=COVERAGE_FIXTURES / "campaigns",
    )


def _read_coverage(out_dir: Path) -> dict:
    return json.loads(
        (out_dir / "korean_coverage_report.json").read_text(encoding="utf-8")
    )


def _coverage_units(report: dict) -> dict:
    return {u["unit_key"]: u for u in report["units"]}


def _coverage_cards(out_dir: Path) -> dict:
    return {c["source_file"]: c for c in _read_index(out_dir)["cards"]}


def test_coverage_verdicts_by_host(tmp_path):
    """R2 -> korean, Steam -> english, relative (no host) -> unknown."""
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    result = _run_packs(root, out, "playercards,campaigns", ["--coverage"])
    assert result.returncode == 1, result.stderr

    cards = _coverage_cards(out)
    assert cards["KoreanFace.cv001.json"]["image_language"] == "korean"
    assert cards["EnglishFace.cv002.json"]["image_language"] == "english"
    relative = cards["DemoCampaign.dc0001/RelativeUrl.cv003.json"]
    assert relative["image_language"] == "unknown"

    report = _read_coverage(out)
    assert report["schema_version"] == "1.0.0"
    assert report["report_kind"] == "korean_image_language_coverage"
    assert report["unknown_breakdown"] == {"(relative)": 1}


def test_unknown_excluded_from_denominator(tmp_path):
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    assert _run_packs(root, out, "playercards,campaigns", ["--coverage"]).returncode == 1

    totals = _read_coverage(out)["totals"]
    assert totals["objects"] == totals["korean"] + totals["english"] + totals["unknown"]
    assert totals["denominator"] == totals["korean"] + totals["english"]
    assert totals["unknown"] == 1
    assert totals["denominator"] == totals["objects"] - totals["unknown"]
    assert totals["korean_pct"] == round(
        totals["korean"] / totals["denominator"] * 100, 2
    )


def test_custom_pdf_judged_by_pdfurl(tmp_path):
    """A Custom_PDF enters scope under --coverage and is judged by PDFUrl.

    Also the regression guard for the empty-CustomDeck demotion: an unbound
    deck_key/entry there raises StopIteration or UnboundLocalError and writes
    NO output files, which asserting on the report alone could not tell apart
    from an assertion failure. So the file existence is asserted first.
    """
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    result = _run_packs(root, out, "playercards,campaigns", ["--coverage"])
    assert result.returncode == 1, result.stderr
    assert (out / "korean_card_index.json").exists()
    assert (out / "korean_card_index.csv").exists()
    assert (out / "korean_coverage_report.json").exists()
    assert (out / "korean_coverage_report.csv").exists()

    guide = _coverage_cards(out)["DemoCampaign.dc0001/ScenarioGuide.cv005.json"]
    assert guide["image_source_field"] == "CustomPDF.PDFUrl"
    assert guide["image_language"] == "english"
    assert "no_custom_deck" in guide["anomaly"].split(";")
    # Total-but-inert values, never unbound and never a fake 0x0 grid.
    assert guide["deck_key"] == ""
    assert guide["face"]["num_width"] is None
    assert guide["face"]["num_height"] is None
    assert guide["face"]["cell_index"] is None
    assert "cell_out_of_bounds" not in guide["anomaly"]

    # The object is counted, and the CSV keeps its 23-column width.
    report = _read_coverage(out)
    assert report["totals"]["objects"] == 6
    row = _csv_by_source(_read_csv_rows(out))[
        "DemoCampaign.dc0001/ScenarioGuide.cv005.json"
    ]
    assert row["deck_key"] == ""
    assert row["face_num_width"] == ""
    with (out / "korean_card_index.csv").open(encoding="utf-8") as fh:
        assert all(len(r) == 23 for r in csv.reader(fh))


def test_gmnotes_sidecar_resolved_under_coverage(tmp_path):
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    result = _run_packs(root, out, "campaigns", ["--coverage"])
    assert result.returncode == 1, result.stderr

    card = _coverage_cards(out)["DemoCampaign.dc0001/SidecarGmnotes.cv004.json"]
    assert card["arkham_id"] == "kr0004"
    assert card["anomaly"] == "gmnotes_sidecar_resolved"
    assert card["image_language"] == "korean"

    codes = {a["code"] for a in _read_coverage(out)["anomalies"]}
    assert "gmnotes_sidecar_resolved" in codes


def test_gmnotes_hard_exit_40_still_fires_without_coverage(tmp_path):
    """The demotion is mode-scoped: the same object is a hard 40 in strict mode."""
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    result = _run_packs(root, out, "campaigns")
    assert result.returncode == 40, result.stderr
    assert not (out / "korean_card_index.json").exists()
    assert not (out / "korean_coverage_report.json").exists()


def test_gmnotes_sidecar_outside_pack_root_refused(tmp_path):
    """A GMNotes_path escaping the pack root is refused, never read."""
    root = _make_decomposed(tmp_path, campaigns=None)
    pack_root = root / PACK_SUBPATH["campaigns"]
    escape_target = tmp_path / "escape.gmnotes"
    escape_target.write_text(json.dumps({"id": "ESCAPED"}), encoding="utf-8")
    (pack_root / "Escape.esc001.json").write_text(json.dumps({
        "Nickname": "Escaping Card",
        "CardID": 400,
        "GMNotes": "",
        "GMNotes_path": "../../../escape.gmnotes",
        "CustomDeck": {"4": {
            "FaceURL": "https://steamusercontent-a.akamaihd.net/ugc/esc/",
            "BackURL": "", "NumWidth": 1, "NumHeight": 1, "UniqueBack": False,
        }},
    }), encoding="utf-8")

    out = tmp_path / "out"
    result = _run_packs(root, out, "campaigns", ["--coverage"])
    assert result.returncode == 1, result.stderr
    assert "refused" in result.stderr

    card = _coverage_cards(out)["Escape.esc001.json"]
    assert card["arkham_id"] == ""
    assert card["anomaly"] == "gmnotes_unresolved"
    assert "ESCAPED" not in (out / "korean_card_index.json").read_text(
        encoding="utf-8"
    )


def test_unit_key_directory_and_id_prefix(tmp_path):
    """Campaigns roll up by directory; Player Cards by derived cycle-NN."""
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    assert _run_packs(root, out, "playercards,campaigns", ["--coverage"]).returncode == 1

    cards = _coverage_cards(out)
    assert cards["KoreanFace.cv001.json"]["unit_key"] == "playercards/cycle-02"
    assert cards["EnglishFace.cv002.json"]["unit_key"] == "playercards/cycle-09"
    assert (cards["DemoCampaign.dc0001/RelativeUrl.cv003.json"]["unit_key"]
            == "campaigns/DemoCampaign.dc0001")

    units = _coverage_units(_read_coverage(out))
    assert units["playercards/cycle-02"]["unit_key_source"] == "id_prefix"
    assert units["campaigns/DemoCampaign.dc0001"]["unit_key_source"] == "directory"
    assert units["campaigns/DemoCampaign.dc0001"]["objects"] == 4
    # Always '/', never '\\', in every emitted unit_key.
    assert all("\\" not in u for u in units)


def test_multi_owner_verdict_conflict_recorded(tmp_path):
    """The cv001/cv006 id pair is declared, and BOTH objects are counted."""
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    assert _run_packs(root, out, "playercards,campaigns", ["--coverage"]).returncode == 1

    report = _read_coverage(out)
    multi = report["multi_owner_ids"]
    assert multi["ids_in_multiple_units"] == 1
    assert multi["verdict_conflict_count"] == 1
    conflict = multi["verdict_conflicts"][0]
    assert conflict["id"] == "02040"
    assert conflict["units"] == [
        {"unit_key": "campaigns/DemoCampaign.dc0001", "image_language": "english"},
        {"unit_key": "playercards/cycle-02", "image_language": "korean"},
    ]

    # No id collapse: both objects survive into the index and the counts.
    dupes = [c for c in _read_index(out)["cards"] if c["arkham_id"] == "02040"]
    assert len(dupes) == 2
    units = _coverage_units(report)
    assert units["playercards/cycle-02"]["objects"] == 1
    assert units["campaigns/DemoCampaign.dc0001"]["objects"] == 4


def test_face_back_mismatch_counted(tmp_path):
    """A korean face over an english back is counted, and only such crossings."""
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    assert _run_packs(root, out, "playercards,campaigns", ["--coverage"]).returncode == 1

    report = _read_coverage(out)
    assert report["totals"]["face_back_mismatch"] == 1
    units = _coverage_units(report)
    assert units["playercards/cycle-02"]["face_back_mismatch"] == 1
    assert units["playercards/cycle-09"]["face_back_mismatch"] == 0
    # An unknown on either side is a non-measurement, not a disagreement.
    assert units["campaigns/DemoCampaign.dc0001"]["face_back_mismatch"] == 0
    # The back rollup is independent of the verdict and counts every object:
    # two Steam backs, four with no readable back host.
    assert report["totals"]["back"] == {"korean": 0, "english": 2, "unknown": 4}
    assert sum(report["totals"]["back"].values()) == report["totals"]["objects"]


def test_coverage_csv_is_15_columns(tmp_path):
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    assert _run_packs(root, out, "playercards,campaigns", ["--coverage"]).returncode == 1

    with (out / "korean_coverage_report.csv").open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    expected = [
        "unit_key", "pack", "unit", "unit_key_source",
        "objects", "korean", "english", "unknown",
        "denominator", "korean_pct",
        "back_korean", "back_english", "back_unknown",
        "face_back_mismatch", "anomalies",
    ]
    assert rows[0] == expected
    assert len(rows[0]) == 15
    for r in rows:
        assert len(r) == 15
    # header + one row per unit, sorted by unit_key.
    keys = [r[0] for r in rows[1:]]
    assert keys == sorted(keys)
    assert len(keys) == 3


COVERAGE_PCT_COL = 9


def test_all_unknown_unit_has_null_pct(tmp_path):
    """denominator == 0 yields null, never 0.0 and never a ZeroDivisionError."""
    root = _make_decomposed(tmp_path, campaigns=None)
    lonely = root / PACK_SUBPATH["campaigns"] / "LonelyUnit.dc0002"
    lonely.mkdir(parents=True)
    shutil.copy(
        COVERAGE_FIXTURES / "campaigns" / "DemoCampaign.dc0001"
        / "RelativeUrl.cv003.json",
        lonely / "RelativeUrl.cv003.json",
    )

    out = tmp_path / "out"
    result = _run_packs(root, out, "campaigns", ["--coverage"])
    assert result.returncode == 0, result.stderr
    assert "ZeroDivisionError" not in result.stderr
    assert "Traceback" not in result.stderr

    report = _read_coverage(out)
    unit = _coverage_units(report)["campaigns/LonelyUnit.dc0002"]
    assert unit["objects"] == 1
    assert unit["denominator"] == 0
    assert unit["korean_pct"] is None
    assert report["totals"]["denominator"] == 0
    assert report["totals"]["korean_pct"] is None

    with (out / "korean_coverage_report.csv").open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert all(len(r) == 15 for r in rows)
    assert rows[1][COVERAGE_PCT_COL] == ""


def test_coverage_two_runs_byte_identical(tmp_path):
    root = _demo_root(tmp_path)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    assert _run_packs(root, out1, "playercards,campaigns", ["--coverage"]).returncode == 1
    assert _run_packs(root, out2, "playercards,campaigns", ["--coverage"]).returncode == 1

    assert ((out1 / "korean_coverage_report.csv").read_bytes()
            == (out2 / "korean_coverage_report.csv").read_bytes())

    def _norm(p):
        data = json.loads(p.read_text(encoding="utf-8"))
        data.pop("generated_at", None)
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

    assert (_norm(out1 / "korean_coverage_report.json")
            == _norm(out2 / "korean_coverage_report.json"))


def test_source_dir_and_packs_are_mutually_exclusive(tmp_path):
    root = _demo_root(tmp_path)
    result = subprocess.run([
        sys.executable, str(INDEX_SCRIPT),
        "--source-dir", str(root / PACK_SUBPATH["playercards"]),
        "--packs", "playercards",
        "--out-dir", str(tmp_path / "out"),
    ], capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    assert "not allowed with" in result.stderr


def test_default_invocation_output_unchanged(tmp_path):
    """The step-1 byte-compat oracle, pinned as a test.

    The oracle itself (the real tree's CSV sha256) cannot be hardcoded — the
    nightly force-pushes `korean` and koreanize writes into these packs, so a
    pinned digest would rot. What IS tree-independent, and what the oracle
    actually established, is that the default single-pack invocation still
    produces the same 23-column CSV bytes as before the multi-pack walk
    existed, and that `--packs playercards` is byte-identical to it.
    """
    src = _copy_clean(tmp_path)
    out_legacy = tmp_path / "out-legacy"
    assert _run(src, out_legacy).returncode == 0

    root = _make_decomposed(tmp_path, playercards=src)
    out_packs = tmp_path / "out-packs"
    assert _run_packs(root, out_packs, "playercards").returncode == 0

    assert ((out_legacy / "korean_card_index.csv").read_bytes()
            == (out_packs / "korean_card_index.csv").read_bytes())

    legacy = _read_index(out_legacy)
    packs = _read_index(out_packs)
    assert legacy["counts"] == packs["counts"]
    assert legacy["anomalies"] == packs["anomalies"]
    # source_dir is kept verbatim for compatibility in single-pack mode.
    assert legacy["source_dir"] == PACK_SUBPATH["playercards"]
    assert packs["source_dir"] == PACK_SUBPATH["playercards"]
    # No coverage artifacts without --coverage.
    assert not (out_legacy / "korean_coverage_report.json").exists()
    assert not (out_legacy / "korean_coverage_report.csv").exists()
    assert _read_manifest(out_legacy)["mode"] == "index"


# ---------------------------------------------------------------------------
# --incumbent-report (read-only; never imports or runs the incumbent generator)
# ---------------------------------------------------------------------------


def _write_incumbent(path: Path, **overrides) -> Path:
    doc = {
        "metadata": {
            "last_updated": "2026-08-06 13:55:20",
            "language": "Korean",
            "overall_completion": "86.70 %",
            "total_found": 5253,
            "total_required": 6059,
        },
        "content": {
            "campaign\\The Dunwich Legacy": {
                "completion": "95.74 %", "stats": "90 / 94", "missing": [],
            },
        },
    }
    doc.update(overrides)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def test_incumbent_report_populates_comparison(tmp_path):
    root = _demo_root(tmp_path)
    incumbent = _write_incumbent(tmp_path / "Korean_report.json")
    out = tmp_path / "out"
    result = _run_packs(
        root, out, "playercards,campaigns",
        ["--coverage", "--incumbent-report", str(incumbent)],
    )
    assert result.returncode == 1, result.stderr

    report = _read_coverage(out)
    comp = report["incumbent_comparison"]
    assert comp is not None
    assert comp["read_only"] is True
    assert comp["incumbent_overall_completion"] == "86.70 %"
    assert comp["incumbent_last_updated"] == "2026-08-06 13:55:20"
    assert comp["incumbent_total"] == "5253 / 6059"
    assert comp["this_report_korean_pct"] == report["totals"]["korean_pct"]
    assert comp["per_unit_comparison"] == "not attempted"
    assert comp["per_unit_comparison_reason"]
    # The incumbent artifact is read, never rewritten.
    assert json.loads(incumbent.read_text(encoding="utf-8"))["metadata"][
        "total_found"] == 5253


def test_incumbent_content_keys_separator_normalised(tmp_path):
    root = _demo_root(tmp_path)
    incumbent = _write_incumbent(tmp_path / "Korean_report.json")
    out = tmp_path / "out"
    assert _run_packs(
        root, out, "playercards,campaigns",
        ["--coverage", "--incumbent-report", str(incumbent)],
    ).returncode == 1

    comp = _read_coverage(out)["incumbent_comparison"]
    assert comp["incumbent_content_keys"] == ["campaign/The Dunwich Legacy"]
    assert all("\\" not in k for k in comp["incumbent_content_keys"])


def test_incumbent_report_missing_file_warns_and_continues(tmp_path):
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    missing = tmp_path / "nonexistent" / "Korean_report.json"
    result = _run_packs(
        root, out, "playercards,campaigns",
        ["--coverage", "--incumbent-report", str(missing)],
    )
    assert result.returncode == 1, result.stderr
    assert str(missing) in result.stderr
    assert "Traceback" not in result.stderr
    # Asserting the outputs exist is what separates the handled path from an
    # unhandled traceback, since both would exit 1.
    assert (out / "korean_coverage_report.json").exists()
    assert (out / "korean_coverage_report.csv").exists()
    assert _read_coverage(out)["incumbent_comparison"] is None


def test_incumbent_report_malformed_json_warns_and_continues(tmp_path):
    root = _demo_root(tmp_path)
    out = tmp_path / "out"
    truncated = tmp_path / "Truncated_report.json"
    truncated.write_text('{"metadata": {"overall_com', encoding="utf-8")
    result = _run_packs(
        root, out, "playercards,campaigns",
        ["--coverage", "--incumbent-report", str(truncated)],
    )
    assert result.returncode == 1, result.stderr
    assert str(truncated) in result.stderr
    assert "Traceback" not in result.stderr
    assert (out / "korean_coverage_report.json").exists()
    assert (out / "korean_coverage_report.csv").exists()
    assert _read_coverage(out)["incumbent_comparison"] is None


def test_incumbent_report_requires_coverage(tmp_path):
    """Not an argparse-native relation — a post-parse check produces exit 2."""
    root = _demo_root(tmp_path)
    incumbent = _write_incumbent(tmp_path / "Korean_report.json")
    result = subprocess.run([
        sys.executable, str(INDEX_SCRIPT),
        "--packs", "playercards",
        "--decomposed-root", str(root),
        "--out-dir", str(tmp_path / "out"),
        "--incumbent-report", str(incumbent),
    ], capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    assert "--incumbent-report requires --coverage" in result.stderr
    assert not (tmp_path / "out").exists()
