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
