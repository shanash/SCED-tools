"""
pytest suite for build-korean-overrides.py (v3 design).

All tests use subprocess.run to exercise exit codes and sys.exit() paths.
Fixtures are copied into tmp_path for isolation.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_DIR = TESTS_DIR / "fixtures" / "korean-image-apply"

APPLY_SCRIPT = SCRIPT_DIR / "build-korean-overrides.py"
SOURCE_FIXTURE = FIXTURE_DIR / "v3-source-fixture.json"
TARGET_FIXTURE_DIR = FIXTURE_DIR / "v3-target-fixture"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Copy fixtures to tmp_path and return (source, decomposed_root, output_dir, skipped_out)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "source-langpack.json"
    shutil.copy(SOURCE_FIXTURE, src)

    decomposed = tmp_path / "decomposed"
    shutil.copytree(TARGET_FIXTURE_DIR, decomposed)

    out_dir = tmp_path / "output"
    skipped_out = tmp_path / "skipped-source-only.txt"
    return src, decomposed, out_dir, skipped_out


def _run(tmp_path: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    return _run_with(src, decomposed, out_dir, skipped_out, extra_args or [])


def _run_with(
    source: Path,
    decomposed: Path,
    out_dir: Path,
    skipped_out: Path,
    extra_args: list[str],
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, str(APPLY_SCRIPT),
        "--source", str(source),
        "--decomposed-root", str(decomposed),
        "--output-dir", str(out_dir),
        "--skipped-source-only-out", str(skipped_out),
    ] + extra_args
    return subprocess.run(cmd, capture_output=True, text=True)


def _read_summary(out_dir: Path) -> dict:
    return json.loads((out_dir / "apply_summary.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_apply_dry_run_produces_expected_summary(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--dry-run", "--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    # Source fixture has id=06275 twice → 1 duplicate id.
    assert len(summary["source_duplicates"]) == 1
    assert "06275" in summary["source_duplicates"]

    # id=99999 is source-only.
    assert "99999" in summary["skipped_source_only"]

    # applied > 0 (at least the normal card 60505 changes).
    assert summary["applied"] > 0

    # dry-run: no files should have been written other than summary.
    # Target files should be unchanged.
    normal_card = decomposed / "NormalCard.101a41.json"
    data = json.loads(normal_card.read_text(encoding="utf-8"))
    assert data["Nickname"] == "18-Caliber Derringer"  # unchanged


def test_apply_writes_4_fields(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    normal_card = decomposed / "NormalCard.101a41.json"
    data = json.loads(normal_card.read_text(encoding="utf-8"))

    assert data["Nickname"] == "18구경 데린저"
    assert data["Description"] == "사용(탄약 2발).\n[action] 탄약을 소비합니다"
    assert data["CustomDeck"]["5508"]["FaceURL"] == (
        "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/images/face_60505.png"
    )
    assert data["CustomDeck"]["5508"]["BackURL"] == (
        "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/images/back_player.png"
    )


def test_skip_source_only_cards(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    assert "99999" in summary["skipped_source_only"]

    # No target file for 99999 exists, so nothing to assert on file side.


def test_skip_target_only_cards(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    assert "80001" in summary["skipped_target_only_ids"]

    # Target-only file must be unchanged.
    tgt_file = decomposed / "TargetOnlyCard.t00001.json"
    data = json.loads(tgt_file.read_text(encoding="utf-8"))
    assert data["Nickname"] == "Target Only Card"


def test_description_added_when_missing(tmp_path):
    # The NormalCard target has no Description key; source provides one.
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    normal_before = json.loads((decomposed / "NormalCard.101a41.json").read_text())
    assert "Description" not in normal_before

    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    assert summary["description_added"] >= 1

    normal_after = json.loads((decomposed / "NormalCard.101a41.json").read_text())
    assert "Description" in normal_after
    assert normal_after["Description"] == "사용(탄약 2발).\n[action] 탄약을 소비합니다"


def test_description_empty_source_preserves_target_value(tmp_path):
    # id=55090 has Description="" in source; target has "Original Target Description".
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    assert summary["description_skipped_empty"] >= 1

    data = json.loads((decomposed / "EmptyDescCard.ffffff.json").read_text())
    assert data["Description"] == "Original Target Description"


def test_description_whitespace_only_source_treated_as_empty(tmp_path):
    # id=43051 has Description="   \n  " in source (whitespace-only).
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    assert summary["description_skipped_empty"] >= 1

    data = json.loads((decomposed / "WhitespaceDescCard.bbbbbb.json").read_text())
    assert data["Description"] == "Original English Description"


def test_target_metadata_preserved(tmp_path):
    # id=02002-p: source has CardID=999901, target has CardID=20020.
    # After apply, target CardID must remain 20020.
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    data = json.loads((decomposed / "CardIdMismatch.cccccc.json").read_text())
    assert data["CardID"] == 20020
    assert data["GUID"] == "cccccc"


def test_skip_cards_argument(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(
        src, decomposed, out_dir, skipped_out,
        ["--source-dedup=first", "--skip-cards=60505"],
    )
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    assert "60505" in summary["skipped_user_request"]

    data = json.loads((decomposed / "NormalCard.101a41.json").read_text())
    assert data["Nickname"] == "18-Caliber Derringer"  # unchanged


def test_guid_mismatch_recorded(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    # id=06275 should have a guid_mismatch entry (src_guid_1 vs tgt_guid).
    guid_ids = [e["id"] for e in summary["guid_mismatches"]]
    assert "06275" in guid_ids


def test_one_source_id_to_multiple_target_files(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)

    # id=01001 maps to 3 target files — all should be applied.
    fanout_files = [
        decomposed / "FanoutCard.eeeeee.json",
        decomposed / "FanoutCard-Promo.promo01.json",
        decomposed / "FanoutCard-AltArt.alt001.json",
    ]
    applied_fanout = sum(
        1 for f in fanout_files
        if json.loads(f.read_text())["Nickname"] == "팬아웃 카드 (기본)"
    )
    assert applied_fanout == 3

    # applied_unique_ids must count id=01001 as just 1 unique id, not 3.
    assert summary["applied_unique_ids"] >= 1
    # Total applied files for 01001 = 3.
    # (We can't isolate exactly without more introspection, but we know
    # applied_unique_ids < applied when fan-out exists.)
    assert summary["applied"] > summary["applied_unique_ids"]


def test_source_duplicate_id_handling(tmp_path):
    # Default (error mode) should exit 4 because id=06275 appears twice.
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, [])
    assert result.returncode == 4, result.stderr

    # first mode: should succeed and use the first 06275 entry (GUID=src_guid_1).
    src2, decomposed2, out_dir2, skipped_out2 = _setup(tmp_path / "first")
    result2 = _run_with(src2, decomposed2, out_dir2, skipped_out2, ["--source-dedup=first"])
    assert result2.returncode == 0, result2.stderr

    data_first = json.loads((decomposed2 / "GuidMismatchCard.tgt_guid.json").read_text())
    # First occurrence has FaceURL face_06275.png (not face_06275_v2.png).
    assert "face_06275.png" in data_first["CustomDeck"]["627"]["FaceURL"]
    assert "v2" not in data_first["CustomDeck"]["627"]["FaceURL"]

    # last mode: should succeed and use the last 06275 entry (GUID=src_guid_2).
    src3, decomposed3, out_dir3, skipped_out3 = _setup(tmp_path / "last")
    result3 = _run_with(src3, decomposed3, out_dir3, skipped_out3, ["--source-dedup=last"])
    assert result3.returncode == 0, result3.stderr

    data_last = json.loads((decomposed3 / "GuidMismatchCard.tgt_guid.json").read_text())
    assert "face_06275_v2.png" in data_last["CustomDeck"]["627"]["FaceURL"]


def test_multi_deck_source_aborts(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)

    # Inject a card with 2 CustomDeck entries into the source.
    source_data = json.loads(src.read_text(encoding="utf-8"))
    source_data["ContainedObjects"].append({
        "CardID": 123456,
        "GMNotes": "{\"id\": \"99990\"}",
        "GUID": "multi01",
        "Nickname": "Multi Deck Card",
        "Description": "",
        "CustomDeck": {
            "1234": {"FaceURL": "https://example.com/face.png", "BackURL": "https://example.com/back.png"},
            "5678": {"FaceURL": "https://example.com/face2.png", "BackURL": "https://example.com/back2.png"},
        },
    })
    src.write_text(json.dumps(source_data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 5, result.stderr


def test_multi_deck_with_allow_flag(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)

    # Same multi-deck source as above.
    source_data = json.loads(src.read_text(encoding="utf-8"))
    source_data["ContainedObjects"].append({
        "CardID": 123456,
        "GMNotes": "{\"id\": \"99990\"}",
        "GUID": "multi01",
        "Nickname": "Multi Deck Card",
        "Description": "",
        "CustomDeck": {
            "1234": {"FaceURL": "https://example.com/face.png", "BackURL": "https://example.com/back.png"},
            "5678": {"FaceURL": "https://example.com/face2.png", "BackURL": "https://example.com/back2.png"},
        },
    })
    src.write_text(json.dumps(source_data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_with(
        src, decomposed, out_dir, skipped_out,
        ["--source-dedup=first", "--allow-multi-deck"],
    )
    assert result.returncode == 0, result.stderr


def test_gmnotes_invariant_violation(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)

    # Break GMNotes on the source fixture.
    source_data = json.loads(src.read_text(encoding="utf-8"))
    source_data["ContainedObjects"][0]["GMNotes"] = "NOT_VALID_JSON"
    src.write_text(json.dumps(source_data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 2, result.stderr


def test_skipped_source_only_txt_written_one_id_per_line(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)
    expected_ids = set(summary["skipped_source_only"])
    assert len(expected_ids) > 0, "Fixture should have at least one source-only card"

    assert skipped_out.exists(), "skipped-source-only.txt not written"
    lines = skipped_out.read_text(encoding="utf-8").splitlines()

    # One id per line, no extra whitespace.
    assert len(lines) == len(expected_ids), (
        f"Expected {len(expected_ids)} lines, got {len(lines)}: {lines}"
    )
    for line in lines:
        assert line == line.strip(), f"Unexpected whitespace on line: {line!r}"
    assert set(lines) == expected_ids


def test_apply_then_reconstruct_parity(tmp_path):
    src, decomposed, out_dir, skipped_out = _setup(tmp_path)
    result = _run_with(src, decomposed, out_dir, skipped_out, ["--source-dedup=first"])
    assert result.returncode == 0, result.stderr

    summary = _read_summary(out_dir)

    # Sanity: applied + no_change_files + skipped counts add up correctly.
    total_tgt_files = sum(
        1 for _ in decomposed.rglob("*.json")
    )
    accounted = (
        summary["applied"]
        + summary["no_change_files"]
        + summary["skipped_target_only_files"]
        + sum(
            len(paths)
            for paths in [
                [f for f in decomposed.rglob("*.json")
                 if json.loads(f.read_text()).get("GMNotes", "")
                 and json.loads(json.loads(f.read_text()).get("GMNotes", "{}")).get("id", "")
                 in summary.get("skipped_user_request", [])]
            ]
        )
    )
    assert accounted == total_tgt_files, (
        f"accounted={accounted} total_tgt_files={total_tgt_files} summary={summary}"
    )

    # Verify that all applied files now match the source values.
    source_data = json.loads(src.read_text(encoding="utf-8"))
    src_by_id: dict[str, dict] = {}
    for obj in source_data["ContainedObjects"]:
        gm = json.loads(obj["GMNotes"])
        card_id = gm["id"]
        if card_id not in src_by_id:  # first-wins
            src_by_id[card_id] = obj

    mismatches = []
    for path in decomposed.rglob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("GMNotes", "")
        if not raw:
            continue
        try:
            card_id = json.loads(raw).get("id")
        except json.JSONDecodeError:
            continue
        if card_id not in src_by_id:
            continue
        if card_id in summary.get("skipped_source_only", []):
            continue

        src_obj = src_by_id[card_id]
        src_deck_key = list(src_obj["CustomDeck"].keys())[0]
        tgt_deck_key = list(data["CustomDeck"].keys())[0]
        src_face = src_obj["CustomDeck"][src_deck_key]["FaceURL"]
        src_back = src_obj["CustomDeck"][src_deck_key]["BackURL"]
        tgt_face = data["CustomDeck"][tgt_deck_key]["FaceURL"]
        tgt_back = data["CustomDeck"][tgt_deck_key]["BackURL"]

        if tgt_face != src_face:
            mismatches.append(f"{path}: FaceURL mismatch")
        if tgt_back != src_back:
            mismatches.append(f"{path}: BackURL mismatch")
        if data.get("Nickname") != src_obj.get("Nickname"):
            mismatches.append(f"{path}: Nickname mismatch")

    assert mismatches == [], "\n".join(mismatches)
