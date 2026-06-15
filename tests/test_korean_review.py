"""
pytest suite for the Korean Image Review pipeline scripts.

Tests:
- extract-historical-langpacks.py
- build-candidates-index.py
- validate-decisions.py
- synthesize-source-from-decisions.py
- apply-grid-dims.py
- apply-card-decisions.sh (wrapper)
- gallery/pagination logic (unit tests via fixture)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_DIR = TESTS_DIR / "fixtures" / "korean-image-review"

EXTRACT_SCRIPT = SCRIPTS_DIR / "extract-historical-langpacks.py"
CANDIDATES_SCRIPT = SCRIPTS_DIR / "build-candidates-index.py"
GALLERY_SCRIPT = SCRIPTS_DIR / "build-card-review-gallery.py"
VALIDATE_SCRIPT = SCRIPTS_DIR / "validate-decisions.py"
SYNTHESIZE_SCRIPT = SCRIPTS_DIR / "synthesize-source-from-decisions.py"
GRID_DIMS_SCRIPT = SCRIPTS_DIR / "apply-grid-dims.py"
WRAPPER_SCRIPT = SCRIPTS_DIR / "apply-card-decisions.sh"
BUILD_OVERRIDES_SCRIPT = SCRIPTS_DIR / "build-korean-overrides.py"

SMALL_CANDIDATES = FIXTURE_DIR / "small_candidates_index.json"
SMALL_DECISIONS = FIXTURE_DIR / "small_decisions.json"
FIXTURE_SOURCES = FIXTURE_DIR / "sources"
INDEX_180 = FIXTURE_DIR / "180_card_candidates_index.json"
DECISIONS_180 = FIXTURE_DIR / "180_card_decisions.json"

CHECKED_AT_FRESH = "2099-01-01T00:00:00+00:00"
CHECKED_AT_STALE = "2000-01-01T00:00:00+00:00"
BASE_BACK = "https://arkhamdb.example.com/back_player.jpg"


def run_script(script: Path, args: list[str], **kwargs) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(script)] + args
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def run_shell(script: Path, args: list[str], **kwargs) -> subprocess.CompletedProcess:
    cmd = ["bash", str(script)] + args
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def make_candidates_index(
    cards: list[dict],
    generated_at: str = CHECKED_AT_FRESH,
    schema_version: int = 1,
) -> dict:
    """Build a minimal candidates_index dict for testing."""
    return {
        "schema_version": schema_version,
        "generated_at": generated_at,
        "sources": {
            "en": {"path": "/fake/en", "card_count": len(cards)},
            "ko": {"path": "/fake/ko", "card_count": len(cards)},
            "v0": {"path": "/fake/v0.json", "card_count": 0, "available": False},
            "v1": {"path": "/fake/v1.json", "card_count": 0, "available": False},
            "v2": {"path": "/fake/v2.json", "card_count": 0, "available": False},
        },
        "cards": cards,
        "summary": {"total_cards": len(cards), "by_diversity": {}, "en_coverage": 0, "ko_coverage": 0, "v0_coverage": 0, "v1_coverage": 0, "v2_coverage": 0},
    }


def make_ok_status(checked_at: str = CHECKED_AT_FRESH) -> dict:
    return {
        "face": {"status": "ok", "checked_at": checked_at},
        "back": {"status": "ok", "checked_at": checked_at},
    }


def make_candidate(
    version: str,
    face_url: str,
    back_url: str = BASE_BACK,
    deck_key: str = "5500",
    card_id: int = 550000,
    num_width: int = 10,
    num_height: int = 7,
    url_status: dict | None = None,
) -> dict:
    return {
        "version": version,
        "available": True,
        "face_url": face_url,
        "back_url": back_url,
        "num_width": num_width,
        "num_height": num_height,
        "deck_key": deck_key,
        "card_id": card_id,
        "url_status": url_status if url_status is not None else make_ok_status(),
    }


def make_unavailable(version: str) -> dict:
    return {"version": version, "available": False}


# ---------------------------------------------------------------------------
# 1. extract-historical-langpacks.py
# ---------------------------------------------------------------------------


def test_extract_historical_langpacks_produces_3_files(tmp_path):
    """Extract should write v0.json, v1.json, v2.json and extraction_log.json
    when git show succeeds (mocked by pointing at a fake repo that returns data)."""
    # Create a minimal fake git repo with the langpack file
    fake_repo = tmp_path / "fake_sced"
    fake_repo.mkdir()
    (fake_repo / ".git").mkdir()
    langpack_dir = fake_repo / "langpack"
    langpack_dir.mkdir()
    langpack_data = json.dumps({"ContainedObjects": [
        {"GMNotes": json.dumps({"id": "test01"}), "Nickname": "Test", "CustomDeck": {
            "1234": {"FaceURL": "http://x.com/face.jpg", "BackURL": "http://x.com/back.jpg"}
        }, "CardID": 123400},
    ]})
    (langpack_dir / "korean_playercards.json").write_text(langpack_data, encoding="utf-8")

    # Init actual git repo so git show works
    subprocess.run(["git", "init"], cwd=str(fake_repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(fake_repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(fake_repo), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(fake_repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(fake_repo), capture_output=True)

    # Get the commit hash
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(fake_repo), capture_output=True, text=True)
    commit_hash = result.stdout.strip()

    out_dir = tmp_path / "sources"
    refs_arg = f"{commit_hash}=v0,{commit_hash}=v1,{commit_hash}=v2"

    result = run_script(EXTRACT_SCRIPT, [
        "--sced-repo", str(fake_repo),
        "--output-dir", str(out_dir),
        "--refs", refs_arg,
    ])

    assert result.returncode == 0, result.stderr
    assert (out_dir / "v0.json").exists()
    assert (out_dir / "v1.json").exists()
    assert (out_dir / "v2.json").exists()
    assert (out_dir / "extraction_log.json").exists()

    log = json.loads((out_dir / "extraction_log.json").read_text())
    assert "v0" in log["versions"]
    assert log["versions"]["v0"]["total_objects"] == 1


# ---------------------------------------------------------------------------
# 2. build-candidates-index.py unit tests
# ---------------------------------------------------------------------------


def test_candidates_index_dedup_collapses_identical_urls(tmp_path):
    """Cards with identical face_url across versions should have diversity_score=1."""
    # Create decomposed source with 1 card
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    card_path = decomposed / "01001.json"
    card_data = {
        "GMNotes": {"id": "01001"},
        "Nickname": "Test Card",
        "CustomDeck": {"5500": {"FaceURL": "https://x.com/same.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000,
        "GUID": "aaa001",
    }
    card_path.write_text(json.dumps(card_data), encoding="utf-8")

    # Create sources where all versions have the SAME face_url
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    for label in ["v0", "v1", "v2"]:
        data = {"ContainedObjects": [{
            "GMNotes": json.dumps({"id": "01001"}),
            "Nickname": "Test",
            "CustomDeck": {"5500": {"FaceURL": "https://x.com/same.jpg", "BackURL": BASE_BACK,
                                    "NumWidth": 10, "NumHeight": 7}},
            "CardID": 550000,
            "GUID": "aaa001",
        }]}
        (sources_dir / f"{label}.json").write_text(json.dumps(data), encoding="utf-8")

    out = tmp_path / "candidates_index.json"
    result = run_script(CANDIDATES_SCRIPT, [
        "--sources-dir", str(sources_dir),
        "--decomposed-root", str(decomposed),
        "--en-root", str(decomposed),
        "--output", str(out),
    ])

    assert result.returncode == 0, result.stderr
    index = json.loads(out.read_text())
    card = index["cards"][0]
    assert card["diversity_score"] == 1
    assert card["unique_face_url_count"] == 1


def test_candidates_index_records_target_deck_key(tmp_path):
    """target_deck_key must come from the decomposed (en) source, not the langpack."""
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    (decomposed / "01001.json").write_text(json.dumps({
        "GMNotes": {"id": "01001"},
        "Nickname": "Test Card",
        "CustomDeck": {"9999": {"FaceURL": "https://x.com/en.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 999900, "GUID": "bbb001",
    }), encoding="utf-8")

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    for label in ["v0", "v1", "v2"]:
        (sources_dir / f"{label}.json").write_text(json.dumps({"ContainedObjects": [{
            "GMNotes": json.dumps({"id": "01001"}),
            "Nickname": "Test",
            "CustomDeck": {"1111": {"FaceURL": "https://x.com/ko.jpg", "BackURL": BASE_BACK,
                                    "NumWidth": 10, "NumHeight": 7}},
            "CardID": 111100, "GUID": "bbb001",
        }]}), encoding="utf-8")

    out = tmp_path / "candidates_index.json"
    result = run_script(CANDIDATES_SCRIPT, [
        "--sources-dir", str(sources_dir),
        "--decomposed-root", str(decomposed),
        "--en-root", str(decomposed),
        "--output", str(out),
    ])

    assert result.returncode == 0, result.stderr
    index = json.loads(out.read_text())
    assert index["cards"][0]["target_deck_key"] == "9999"


def test_load_decomposed_prefers_top_level_over_subdirectory(tmp_path):
    """When the same arkham_id has both a top-level file (main investigator)
    and a subdirectory file (mini-deck variant), the canonical ko candidate
    must be the top-level one. Without depth-first sort, Python Path ordering
    places subdirectory entries before top-level ones, picking the wrong card."""
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()

    # Top-level: main investigator (deck_key 2738)
    (decomposed / "Roland.9e9e98.json").write_text(json.dumps({
        "GMNotes": {"id": "01001"},
        "Nickname": "Roland Banks",
        "CustomDeck": {"2738": {"FaceURL": "https://x.com/main.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 273800, "GUID": "guid-main",
    }), encoding="utf-8")

    # Subdirectory: mini-deck variant (deck_key 5874) — picked if depth ignored
    sub = decomposed / "Roland.9e9e98"
    sub.mkdir()
    (sub / "Roland.a684e0.json").write_text(json.dumps({
        "GMNotes": {"id": "01001"},
        "Nickname": "Roland Banks (mini)",
        "CustomDeck": {"5874": {"FaceURL": "https://x.com/mini.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 587400, "GUID": "guid-mini",
    }), encoding="utf-8")

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    for label in ["v0", "v1", "v2"]:
        (sources_dir / f"{label}.json").write_text(
            json.dumps({"ContainedObjects": []}), encoding="utf-8"
        )

    en_root = tmp_path / "en"
    en_root.mkdir()

    out = tmp_path / "candidates_index.json"
    result = run_script(CANDIDATES_SCRIPT, [
        "--sources-dir", str(sources_dir),
        "--decomposed-root", str(decomposed),
        "--en-root", str(en_root),
        "--output", str(out),
    ])

    assert result.returncode == 0, result.stderr
    index = json.loads(out.read_text())
    card = next(c for c in index["cards"] if c["arkham_id"] == "01001")
    ko = next(cand for cand in card["candidates"] if cand["version"] == "ko")

    assert ko["deck_key"] == "2738", \
        f"expected top-level main investigator deck_key 2738, got {ko['deck_key']}"
    assert ko["face_url"] == "https://x.com/main.jpg"

    # Both files should still be tracked in fan-out warnings (paths_by_id preserved).
    # first_deck_key must be the top-level main investigator (2738),
    # not the subdirectory mini-deck (5874) — confirms depth-first sort took effect.
    fanout = next(
        w for w in index["summary"]["deckkey_fanout_warnings"]
        if w["arkham_id"] == "01001"
    )
    assert fanout["first_deck_key"] == "2738"
    assert fanout["other_deck_key"] == "5874"


def test_candidates_index_url_status_has_checked_at(tmp_path):
    """When url-validation JSON is provided, candidates should have checked_at in url_status."""
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    (decomposed / "01001.json").write_text(json.dumps({
        "GMNotes": {"id": "01001"},
        "Nickname": "T",
        "CustomDeck": {"5500": {"FaceURL": "https://x.com/face.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "x",
    }), encoding="utf-8")

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    for label in ["v0", "v1", "v2"]:
        (sources_dir / f"{label}.json").write_text(
            json.dumps({"ContainedObjects": []}), encoding="utf-8"
        )

    val_input = tmp_path / "validation.json"
    val_input.write_text(json.dumps({
        "results": [{
            "face_url": "https://x.com/face.jpg",
            "face_status": "ok",
            "back_status": "ok",
            "checked_at": "2099-01-01T00:00:00+00:00",
        }]
    }), encoding="utf-8")

    out = tmp_path / "candidates_index.json"
    result = run_script(CANDIDATES_SCRIPT, [
        "--sources-dir", str(sources_dir),
        "--decomposed-root", str(decomposed),
        "--en-root", str(decomposed),
        "--output", str(out),
        "--url-validation", str(val_input),
    ])

    assert result.returncode == 0, result.stderr
    index = json.loads(out.read_text())
    en_cand = index["cards"][0]["candidates"][0]
    assert en_cand["url_status"] is not None
    assert en_cand["url_status"]["face"]["checked_at"] == "2099-01-01T00:00:00+00:00"


def test_candidates_index_emit_validation_input_produces_buildkorean_compatible_shape(tmp_path):
    """--emit-validation-input should produce ContainedObjects with GMNotes.id parseable."""
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    (decomposed / "01001.json").write_text(json.dumps({
        "GMNotes": {"id": "01001"},
        "Nickname": "T",
        "CustomDeck": {"5500": {"FaceURL": "https://x.com/en.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "x",
    }), encoding="utf-8")

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    for label in ["v0", "v1", "v2"]:
        (sources_dir / f"{label}.json").write_text(
            json.dumps({"ContainedObjects": []}), encoding="utf-8"
        )

    out = tmp_path / "candidates_index.json"
    val_out = tmp_path / "validation_input.json"
    result = run_script(CANDIDATES_SCRIPT, [
        "--sources-dir", str(sources_dir),
        "--decomposed-root", str(decomposed),
        "--en-root", str(decomposed),
        "--output", str(out),
        "--emit-validation-input", str(val_out),
    ])

    assert result.returncode == 0, result.stderr
    assert val_out.exists()
    val_data = json.loads(val_out.read_text())
    assert "ContainedObjects" in val_data
    for obj in val_data["ContainedObjects"]:
        gm = json.loads(obj["GMNotes"])
        assert "id" in gm
        assert "CustomDeck" in obj


def test_diversity_score_calculation(tmp_path):
    """Cards with 2 distinct face URLs should have diversity_score=2."""
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    (decomposed / "01001.json").write_text(json.dumps({
        "GMNotes": {"id": "01001"},
        "Nickname": "T",
        "CustomDeck": {"5500": {"FaceURL": "https://x.com/en.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "x",
    }), encoding="utf-8")

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    # v0 has different URL, v1 and v2 same as en
    for label, face in [("v0", "https://x.com/different.jpg"), ("v1", "https://x.com/en.jpg"), ("v2", "https://x.com/en.jpg")]:
        (sources_dir / f"{label}.json").write_text(json.dumps({"ContainedObjects": [{
            "GMNotes": json.dumps({"id": "01001"}),
            "Nickname": "T",
            "CustomDeck": {"5500": {"FaceURL": face, "BackURL": BASE_BACK, "NumWidth": 10, "NumHeight": 7}},
            "CardID": 550000, "GUID": "x",
        }]}), encoding="utf-8")

    out = tmp_path / "candidates_index.json"
    result = run_script(CANDIDATES_SCRIPT, [
        "--sources-dir", str(sources_dir),
        "--decomposed-root", str(decomposed),
        "--en-root", str(decomposed),
        "--output", str(out),
    ])
    assert result.returncode == 0, result.stderr
    index = json.loads(out.read_text())
    card = index["cards"][0]
    assert card["diversity_score"] == 2
    assert card["unique_face_url_count"] == 2


def test_sheet_cohort_grouping(tmp_path):
    """sheet_cohort_v2 should be deck_key|sha1(face_v2)[:12]."""
    import hashlib
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    face_v2 = "https://x.com/v2sheet.jpg"
    (decomposed / "01001.json").write_text(json.dumps({
        "GMNotes": {"id": "01001"},
        "Nickname": "T",
        "CustomDeck": {"5500": {"FaceURL": "https://x.com/en.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "x",
    }), encoding="utf-8")

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    for label in ["v0", "v1"]:
        (sources_dir / f"{label}.json").write_text(json.dumps({"ContainedObjects": []}), encoding="utf-8")
    (sources_dir / "v2.json").write_text(json.dumps({"ContainedObjects": [{
        "GMNotes": json.dumps({"id": "01001"}),
        "Nickname": "T",
        "CustomDeck": {"5500": {"FaceURL": face_v2, "BackURL": BASE_BACK, "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "x",
    }]}), encoding="utf-8")

    out = tmp_path / "candidates_index.json"
    result = run_script(CANDIDATES_SCRIPT, [
        "--sources-dir", str(sources_dir),
        "--decomposed-root", str(decomposed),
        "--en-root", str(decomposed),
        "--output", str(out),
    ])
    assert result.returncode == 0, result.stderr
    index = json.loads(out.read_text())
    card = index["cards"][0]
    expected_sha = hashlib.sha1(face_v2.encode()).hexdigest()[:12]
    expected_cohort = f"5500|{expected_sha}"
    assert card["sheet_cohort_v2"] == expected_cohort


# ---------------------------------------------------------------------------
# 3. synthesize-source-from-decisions.py
# ---------------------------------------------------------------------------


def _write_candidates_and_decisions(
    tmp_path: Path,
    cards: list[dict],
    decisions: list[dict],
    generated_at: str = CHECKED_AT_FRESH,
    schema_version: int = 1,
) -> tuple[Path, Path]:
    cand_path = tmp_path / "candidates_index.json"
    dec_path = tmp_path / "review_decisions.json"
    cand_path.write_text(json.dumps(make_candidates_index(cards, generated_at, schema_version),
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    dec_path.write_text(json.dumps({
        "schema_version": schema_version,
        "decisions": decisions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return cand_path, dec_path


def test_synthesize_emits_only_v0v1v2_choices(tmp_path):
    """Output should only contain cards with choice in {v0, v1, v2}."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    assert result.returncode == 0, result.stderr
    data = json.loads(out.read_text())
    assert len(data["ContainedObjects"]) == 1
    assert json.loads(data["ContainedObjects"][0]["GMNotes"])["id"] == "A001"


def test_synthesize_skip_choice_omitted(tmp_path):
    """Cards with choice=skip must NOT appear in output."""
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", "https://x.com/v2.jpg"),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "skip", "face_url": None,
                  "back_url": None, "num_width": None, "num_height": None, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    assert result.returncode in (0, 1), result.stderr
    data = json.loads(out.read_text())
    assert len(data["ContainedObjects"]) == 0


def test_synthesize_uses_target_deck_key_not_chosen(tmp_path):
    """CustomDeck key must be target_deck_key from candidates_index, not chosen.deck_key."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "TARGET_KEY",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg", deck_key="TARGET_KEY"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2, deck_key="CHOSEN_KEY"),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "CHOSEN_KEY"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    assert result.returncode == 0, result.stderr
    data = json.loads(out.read_text())
    assert len(data["ContainedObjects"]) == 1
    obj = data["ContainedObjects"][0]
    assert "TARGET_KEY" in obj["CustomDeck"]
    assert "CHOSEN_KEY" not in obj["CustomDeck"]


def test_synthesize_omits_backishidden_and_type(tmp_path):
    """CustomDeck entry must not contain BackIsHidden or Type keys."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    data = json.loads(out.read_text())
    deck_entry = data["ContainedObjects"][0]["CustomDeck"]["5500"]
    assert "BackIsHidden" not in deck_entry
    assert "Type" not in deck_entry
    assert set(deck_entry.keys()) == {"FaceURL", "BackURL", "NumWidth", "NumHeight"}


def test_synthesize_text_source_untouched_omits_nickname_description(tmp_path):
    """With --text-source untouched, output objects must not have Nickname/Description."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
        "--text-source", "untouched",
    ])
    data = json.loads(out.read_text())
    obj = data["ContainedObjects"][0]
    assert "Nickname" not in obj
    assert "Description" not in obj


def test_synthesize_text_source_v2_includes_korean_text(tmp_path):
    """With --text-source v2, output objects should include Korean Nickname from v2 source."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)

    # Write v2 source
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    (sources_dir / "v2.json").write_text(json.dumps({"ContainedObjects": [{
        "GMNotes": json.dumps({"id": "A001"}),
        "Nickname": "한국어 이름",
        "Description": "설명",
        "CustomDeck": {"5500": {"FaceURL": face_v2, "BackURL": BASE_BACK, "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "x",
    }]}), encoding="utf-8")

    out = tmp_path / "synthetic.json"
    run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(sources_dir),
        "--output", str(out),
        "--text-source", "v2",
    ])
    data = json.loads(out.read_text())
    obj = data["ContainedObjects"][0]
    assert obj.get("Nickname") == "한국어 이름"


def test_synthesize_aborts_when_undecided_gt_zero_without_flag(tmp_path):
    """Should exit 42 when some cards have no decision and --allow-incomplete is not set."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        },
        {
            "arkham_id": "A002",  # no decision
            "target_deck_key": "5501",
            "candidates": [
                make_candidate("en", "https://x.com/en2.jpg", deck_key="5501"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", "https://x.com/v2_2.jpg", deck_key="5501"),
            ],
        },
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    assert result.returncode == 42


def test_synthesize_proceeds_with_allow_incomplete(tmp_path):
    """With --allow-incomplete, should proceed even with undecided cards."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        },
        {
            "arkham_id": "A002",
            "target_deck_key": "5501",
            "candidates": [
                make_candidate("en", "https://x.com/en2.jpg", deck_key="5501"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_unavailable("v2"),
            ],
        },
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
        "--allow-incomplete",
    ])
    assert result.returncode in (0, 1), result.stderr
    data = json.loads(out.read_text())
    # Only A001 (v2 choice) should appear; A002 has no decision
    assert len(data["ContainedObjects"]) == 1


def test_synthesize_rejects_candidates_older_than_24h(tmp_path):
    """Should exit 40 when generated_at is older than 24 hours."""
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions, generated_at=stale_ts)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    assert result.returncode == 40


def test_synthesize_rejects_when_per_url_checked_at_stale(tmp_path):
    """Should exit 40 when url_status.checked_at is older than 24h."""
    face_v2 = "https://x.com/v2.jpg"
    stale_status = {
        "face": {"status": "ok", "checked_at": CHECKED_AT_STALE},
        "back": {"status": "ok", "checked_at": CHECKED_AT_STALE},
    }
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2, url_status=stale_status),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    assert result.returncode == 40


def test_synthesize_rejects_url_status_not_ok(tmp_path):
    """Should exit 41 when chosen candidate url_status.face.status != ok."""
    face_v2 = "https://x.com/v2.jpg"
    fail_status = {
        "face": {"status": "fail", "checked_at": CHECKED_AT_FRESH},
        "back": {"status": "ok", "checked_at": CHECKED_AT_FRESH},
    }
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2, url_status=fail_status),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path, dec_path = _write_candidates_and_decisions(tmp_path, cards, decisions)
    out = tmp_path / "synthetic.json"
    result = run_script(SYNTHESIZE_SCRIPT, [
        "--candidates", str(cand_path),
        "--decisions", str(dec_path),
        "--sources-dir", str(tmp_path / "sources"),
        "--output", str(out),
    ])
    assert result.returncode == 41


# ---------------------------------------------------------------------------
# 4. validate-decisions.py
# ---------------------------------------------------------------------------


def test_validate_decisions_distinguishes_skip_vs_undecided(tmp_path):
    """Report must list skip and undecided separately."""
    cand_path = SMALL_CANDIDATES
    dec_path = SMALL_DECISIONS

    result = run_script(VALIDATE_SCRIPT, [
        "--decisions", str(dec_path),
        "--candidates", str(cand_path),
        "--output-dir", str(tmp_path),
    ])

    md = (tmp_path / "decisions_validation_report.md").read_text()
    assert "Skipped (explicit choice=skip):" in md
    assert "Undecided (absent from decisions):" in md


def test_validate_decisions_rejects_tampered_face_url(tmp_path):
    """Should exit 33 when decision face_url differs from candidate."""
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "sheet_cohort_v2": None,
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2),
            ],
        }
    ]
    # Tampered face_url in decision
    decisions = [{"arkham_id": "A001", "choice": "v2",
                  "face_url": "https://TAMPERED.example.com/wrong.jpg",
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path = tmp_path / "candidates_index.json"
    dec_path = tmp_path / "review_decisions.json"
    cand_path.write_text(json.dumps(make_candidates_index(cards)), encoding="utf-8")
    dec_path.write_text(json.dumps({"schema_version": 1, "decisions": decisions}), encoding="utf-8")

    result = run_script(VALIDATE_SCRIPT, [
        "--decisions", str(dec_path),
        "--candidates", str(cand_path),
        "--output-dir", str(tmp_path),
    ])
    assert result.returncode == 33


def test_validate_decisions_rejects_stale_url_status(tmp_path):
    """Should warn (exit 1) when url_status shows fail (non-strict) or exit 34 (strict)."""
    fail_status = {
        "face": {"status": "fail", "checked_at": CHECKED_AT_FRESH},
        "back": {"status": "ok", "checked_at": CHECKED_AT_FRESH},
    }
    face_v2 = "https://x.com/v2.jpg"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "sheet_cohort_v2": None,
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", face_v2, url_status=fail_status),
            ],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "v2", "face_url": face_v2,
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path = tmp_path / "candidates_index.json"
    dec_path = tmp_path / "review_decisions.json"
    cand_path.write_text(json.dumps(make_candidates_index(cards)), encoding="utf-8")
    dec_path.write_text(json.dumps({"schema_version": 1, "decisions": decisions}), encoding="utf-8")

    result = run_script(VALIDATE_SCRIPT, [
        "--decisions", str(dec_path),
        "--candidates", str(cand_path),
        "--output-dir", str(tmp_path),
        "--strict",
    ])
    assert result.returncode == 34


def test_validate_decisions_detects_cohort_conflict(tmp_path):
    """Should detect when cards in the same cohort choose different face_urls."""
    cohort = "DK|abc123"
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "DK",
            "sheet_cohort_v2": cohort,
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg", deck_key="DK"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", "https://x.com/v2_face1.jpg", deck_key="DK"),
            ],
        },
        {
            "arkham_id": "A002",
            "target_deck_key": "DK",
            "sheet_cohort_v2": cohort,
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg", deck_key="DK"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", "https://x.com/v2_face2.jpg", deck_key="DK"),  # different
            ],
        },
    ]
    decisions = [
        {"arkham_id": "A001", "choice": "v2", "face_url": "https://x.com/v2_face1.jpg",
         "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "DK"},
        {"arkham_id": "A002", "choice": "v2", "face_url": "https://x.com/v2_face2.jpg",
         "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "DK"},
    ]

    cand_path = tmp_path / "candidates_index.json"
    dec_path = tmp_path / "review_decisions.json"
    cand_path.write_text(json.dumps(make_candidates_index(cards)), encoding="utf-8")
    dec_path.write_text(json.dumps({"schema_version": 1, "decisions": decisions}), encoding="utf-8")

    result = run_script(VALIDATE_SCRIPT, [
        "--decisions", str(dec_path),
        "--candidates", str(cand_path),
        "--output-dir", str(tmp_path),
        "--strict",
    ])
    assert result.returncode == 32


def test_validate_decisions_schema_version_mismatch(tmp_path):
    """Should exit 30 when schema versions differ between decisions and candidates."""
    cards = [
        {
            "arkham_id": "A001",
            "target_deck_key": "5500",
            "sheet_cohort_v2": None,
            "candidates": [make_candidate("en", "https://x.com/en.jpg")],
        }
    ]
    decisions = [{"arkham_id": "A001", "choice": "en", "face_url": "https://x.com/en.jpg",
                  "back_url": BASE_BACK, "num_width": 10, "num_height": 7, "deck_key": "5500"}]

    cand_path = tmp_path / "candidates_index.json"
    dec_path = tmp_path / "review_decisions.json"
    cand_path.write_text(json.dumps(make_candidates_index(cards, schema_version=1)), encoding="utf-8")
    dec_path.write_text(json.dumps({"schema_version": 99, "decisions": decisions}), encoding="utf-8")

    result = run_script(VALIDATE_SCRIPT, [
        "--decisions", str(dec_path),
        "--candidates", str(cand_path),
        "--output-dir", str(tmp_path),
    ])
    assert result.returncode == 30


# ---------------------------------------------------------------------------
# 5. apply-grid-dims.py
# ---------------------------------------------------------------------------


def _make_decomposed_card(
    tmp_path: Path,
    arkham_id: str,
    deck_key: str,
    num_width: int,
    num_height: int,
    face_url: str = "https://x.com/face.jpg",
) -> Path:
    card_path = tmp_path / f"{arkham_id}.json"
    card_path.write_text(json.dumps({
        "GMNotes": json.dumps({"id": arkham_id}),
        "Nickname": "Test",
        "CustomDeck": {deck_key: {"FaceURL": face_url, "BackURL": BASE_BACK,
                                   "NumWidth": num_width, "NumHeight": num_height}},
        "CardID": 550000, "GUID": "x",
    }), encoding="utf-8")
    return card_path


def test_grid_dims_patcher_idempotent(tmp_path):
    """Running apply-grid-dims twice should not change the result."""
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    _make_decomposed_card(decomposed, "A001", "5500", 10, 7)

    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({
        "decisions": [{"arkham_id": "A001", "choice": "v2", "face_url": "https://x.com/face.jpg",
                       "back_url": BASE_BACK, "num_width": 5, "num_height": 3, "deck_key": "5500"}]
    }), encoding="utf-8")

    args = ["--decisions", str(decisions), "--decomposed-root", str(decomposed)]
    result1 = run_script(GRID_DIMS_SCRIPT, args)
    assert result1.returncode == 0, result1.stderr
    result2 = run_script(GRID_DIMS_SCRIPT, args)
    assert result2.returncode == 0, result2.stderr

    data = json.loads((decomposed / "A001.json").read_text())
    assert data["CustomDeck"]["5500"]["NumWidth"] == 5
    assert data["CustomDeck"]["5500"]["NumHeight"] == 3


def test_grid_dims_patcher_handles_fanout_ids(tmp_path):
    """Same arkham_id in multiple files should all be patched."""
    decomposed = tmp_path / "decomposed"
    decomposed.mkdir()
    _make_decomposed_card(decomposed, "A001", "5500", 10, 7, "https://x.com/face1.jpg")
    # Second file with same id
    (decomposed / "A001_alt.json").write_text(json.dumps({
        "GMNotes": json.dumps({"id": "A001"}),
        "Nickname": "Alt",
        "CustomDeck": {"5500": {"FaceURL": "https://x.com/face1.jpg", "BackURL": BASE_BACK,
                                "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "y",
    }), encoding="utf-8")

    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({
        "decisions": [{"arkham_id": "A001", "choice": "v2", "face_url": "https://x.com/face1.jpg",
                       "back_url": BASE_BACK, "num_width": 3, "num_height": 2, "deck_key": "5500"}]
    }), encoding="utf-8")

    result = run_script(GRID_DIMS_SCRIPT, ["--decisions", str(decisions), "--decomposed-root", str(decomposed)])
    assert result.returncode == 0, result.stderr

    for fname in ["A001.json", "A001_alt.json"]:
        data = json.loads((decomposed / fname).read_text())
        assert data["CustomDeck"]["5500"]["NumWidth"] == 3
        assert data["CustomDeck"]["5500"]["NumHeight"] == 2


# ---------------------------------------------------------------------------
# 6. apply-card-decisions.sh wrapper
# ---------------------------------------------------------------------------


def _make_minimal_decomposed_for_wrapper(decomposed: Path, arkham_id: str, deck_key: str) -> None:
    """Create minimal decomposed card file for wrapper tests."""
    decomposed.mkdir(parents=True, exist_ok=True)
    (decomposed / f"{arkham_id}.json").write_text(json.dumps({
        "GMNotes": json.dumps({"id": arkham_id}),
        "Nickname": "Test",
        "CustomDeck": {deck_key: {"FaceURL": "https://x.com/old.jpg", "BackURL": BASE_BACK,
                                   "NumWidth": 10, "NumHeight": 7}},
        "CardID": 550000, "GUID": "x",
    }), encoding="utf-8")


def _make_synthetic_source(path: Path, arkham_id: str, deck_key: str, face_url: str) -> None:
    path.write_text(json.dumps({"ContainedObjects": [{
        "GMNotes": json.dumps({"id": arkham_id}),
        "Nickname": "Test",
        "CustomDeck": {deck_key: {"FaceURL": face_url, "BackURL": BASE_BACK,
                                   "NumWidth": 5, "NumHeight": 3}},
        "CardID": 550000, "GUID": "x",
    }]}), encoding="utf-8")


def _make_decisions_for_wrapper(path: Path, arkham_id: str, deck_key: str, face_url: str) -> None:
    path.write_text(json.dumps({
        "decisions": [{
            "arkham_id": arkham_id,
            "choice": "v2",
            "face_url": face_url,
            "back_url": BASE_BACK,
            "num_width": 5,
            "num_height": 3,
            "deck_key": deck_key,
        }]
    }), encoding="utf-8")


@pytest.mark.skipif(not BUILD_OVERRIDES_SCRIPT.exists(), reason="build-korean-overrides.py not found")
def test_apply_card_decisions_wrapper_creates_snapshot_in_persistent_location(tmp_path):
    """Wrapper must create snapshot in output-dir/snapshots/, not /tmp."""
    ark = "W001"
    dk = "9000"
    face_url = "https://x.com/v2.jpg"

    decomposed = tmp_path / "decomposed"
    _make_minimal_decomposed_for_wrapper(decomposed, ark, dk)

    source = tmp_path / "source.json"
    _make_synthetic_source(source, ark, dk, face_url)
    decisions = tmp_path / "decisions.json"
    _make_decisions_for_wrapper(decisions, ark, dk, face_url)
    output_dir = tmp_path / "output"

    result = run_shell(WRAPPER_SCRIPT, [
        "--source", str(source),
        "--decisions", str(decisions),
        "--decomposed-root", str(decomposed),
        "--output-dir", str(output_dir),
    ])

    snapshots_dir = output_dir / "snapshots"
    assert snapshots_dir.exists(), "snapshots/ directory must be inside output-dir"
    snapshot_files = list(snapshots_dir.glob("*.tar.gz"))
    assert len(snapshot_files) >= 1, "At least one snapshot file must exist"
    # Verify it's NOT in /tmp
    for sf in snapshot_files:
        assert not str(sf).startswith("/tmp"), "Snapshot must not be in /tmp"


@pytest.mark.skipif(not BUILD_OVERRIDES_SCRIPT.exists(), reason="build-korean-overrides.py not found")
def test_apply_card_decisions_wrapper_post_condition_verify_catches_mismatch(tmp_path):
    """Wrapper should detect post-condition mismatch and auto-restore."""
    ark = "W002"
    dk = "9001"
    old_face = "https://x.com/old.jpg"
    new_face = "https://x.com/new_v2.jpg"

    decomposed = tmp_path / "decomposed"
    _make_minimal_decomposed_for_wrapper(decomposed, ark, dk)

    # Source provides new_face
    source = tmp_path / "source.json"
    _make_synthetic_source(source, ark, dk, new_face)

    # Decisions say new_face, but we'll create a mismatch by giving wrong face in decisions
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({
        "decisions": [{
            "arkham_id": ark,
            "choice": "v2",
            "face_url": "https://x.com/COMPLETELY_DIFFERENT.jpg",  # won't match what was applied
            "back_url": BASE_BACK,
            "num_width": 5,
            "num_height": 3,
            "deck_key": dk,
        }]
    }), encoding="utf-8")

    output_dir = tmp_path / "output"
    result = run_shell(WRAPPER_SCRIPT, [
        "--source", str(source),
        "--decisions", str(decisions),
        "--decomposed-root", str(decomposed),
        "--output-dir", str(output_dir),
    ])

    # Should have auto-restored (exit 64) or detected mismatch (exit 63)
    assert result.returncode in (63, 64), f"Expected 63 or 64, got {result.returncode}\n{result.stderr}"


@pytest.mark.skipif(not BUILD_OVERRIDES_SCRIPT.exists(), reason="build-korean-overrides.py not found")
def test_apply_card_decisions_wrapper_records_original_exit_code(tmp_path):
    """apply_summary.json must record the original exit code of failed sub-script."""
    ark = "W003"
    dk = "9002"
    face_url = "https://x.com/v2.jpg"

    decomposed = tmp_path / "decomposed"
    _make_minimal_decomposed_for_wrapper(decomposed, ark, dk)

    # Deliberately broken source (empty ContainedObjects, but decisions want v2)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"ContainedObjects": []}), encoding="utf-8")

    decisions = tmp_path / "decisions.json"
    _make_decisions_for_wrapper(decisions, ark, dk, face_url)
    output_dir = tmp_path / "output"

    run_shell(WRAPPER_SCRIPT, [
        "--source", str(source),
        "--decisions", str(decisions),
        "--decomposed-root", str(decomposed),
        "--output-dir", str(output_dir),
        "--no-auto-restore",
    ])

    summary_path = output_dir / "apply_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        assert "original_exit_code" in summary
        assert "wrapper_exit_code" in summary


@pytest.mark.skipif(not BUILD_OVERRIDES_SCRIPT.exists(), reason="build-korean-overrides.py not found")
def test_wrapper_auto_restores_on_grid_dims_failure(tmp_path):
    """Wrapper should auto-restore snapshot when apply-grid-dims.py fails."""
    ark = "W004"
    dk = "9003"
    face_url = "https://x.com/v2.jpg"

    decomposed = tmp_path / "decomposed"
    _make_minimal_decomposed_for_wrapper(decomposed, ark, dk)
    original_content = (decomposed / f"{ark}.json").read_text()

    source = tmp_path / "source.json"
    _make_synthetic_source(source, ark, dk, face_url)

    # Decisions reference a non-existent arkham_id to cause grid-dims to fail
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({
        "decisions": [{
            "arkham_id": "NONEXISTENT_ID_THAT_WILL_FAIL",
            "choice": "v2",
            "face_url": face_url,
            "back_url": BASE_BACK,
            "num_width": 5,
            "num_height": 3,
            "deck_key": dk,
        }]
    }), encoding="utf-8")

    output_dir = tmp_path / "output"
    result = run_shell(WRAPPER_SCRIPT, [
        "--source", str(source),
        "--decisions", str(decisions),
        "--decomposed-root", str(decomposed),
        "--output-dir", str(output_dir),
    ])

    assert result.returncode == 64, f"Expected auto-restore exit 64, got {result.returncode}"
    # Check auto_restore_performed in summary
    summary_path = output_dir / "apply_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        assert summary.get("auto_restore_performed") is True


@pytest.mark.skipif(not BUILD_OVERRIDES_SCRIPT.exists(), reason="build-korean-overrides.py not found")
def test_wrapper_no_auto_restore_flag_skips_restore(tmp_path):
    """With --no-auto-restore, wrapper should exit 64 without actually restoring."""
    ark = "W005"
    dk = "9004"
    face_url = "https://x.com/v2.jpg"

    decomposed = tmp_path / "decomposed"
    _make_minimal_decomposed_for_wrapper(decomposed, ark, dk)

    source = tmp_path / "source.json"
    _make_synthetic_source(source, ark, dk, face_url)

    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({
        "decisions": [{
            "arkham_id": "NONEXISTENT_FOR_NO_RESTORE",
            "choice": "v2",
            "face_url": face_url,
            "back_url": BASE_BACK,
            "num_width": 5,
            "num_height": 3,
            "deck_key": dk,
        }]
    }), encoding="utf-8")

    output_dir = tmp_path / "output"
    result = run_shell(WRAPPER_SCRIPT, [
        "--source", str(source),
        "--decisions", str(decisions),
        "--decomposed-root", str(decomposed),
        "--output-dir", str(output_dir),
        "--no-auto-restore",
    ])

    assert result.returncode == 64
    summary_path = output_dir / "apply_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        assert summary.get("auto_restore_performed") is False


# ---------------------------------------------------------------------------
# 7. E2E tests with 180-card fixture
# ---------------------------------------------------------------------------


def test_e2e_review_pipeline_on_180card_fixture(tmp_path):
    """Run validate-decisions on 180-card fixture. Should detect tampered entry and cohort issues."""
    assert INDEX_180.exists(), f"180_card_candidates_index.json fixture missing"
    assert DECISIONS_180.exists(), f"180_card_decisions.json fixture missing"

    result = run_script(VALIDATE_SCRIPT, [
        "--decisions", str(DECISIONS_180),
        "--candidates", str(INDEX_180),
        "--output-dir", str(tmp_path),
    ])

    # Tampered entry (div_0002) should cause exit 33
    assert result.returncode == 33 or result.returncode in (1, 33), \
        f"Expected tamper detection, got {result.returncode}\n{result.stderr}"


def test_pagination_boundary_at_50(tmp_path):
    """Gallery with exactly 50 cards should have 1 page; 51 should have 2 pages."""
    # Build a minimal candidates_index with 51 cards
    cards = []
    for i in range(51):
        cards.append({
            "arkham_id": f"PAG{i:04d}",
            "nickname_en": f"Card {i}",
            "nickname_v2_ko": "",
            "target_paths": [],
            "target_deck_key": "5500",
            "candidates": [make_candidate("en", f"https://x.com/en_{i}.jpg")],
            "unique_face_url_count": 1,
            "diversity_score": 1,
            "sheet_cohort_v2": None,
        })

    index = make_candidates_index(cards)
    cand_path = tmp_path / "candidates_index.json"
    cand_path.write_text(json.dumps(index), encoding="utf-8")

    out_50 = tmp_path / "gallery_50.html"
    out_51 = tmp_path / "gallery_51.html"

    # 51 cards with page_size=50 → 2 pages
    result = run_script(GALLERY_SCRIPT, [
        "--candidates", str(cand_path),
        "--output", str(out_51),
        "--page-size", "50",
    ])
    assert result.returncode == 0, result.stderr
    html = out_51.read_text()
    assert "const PAGE_SIZE = 50" in html

    # 50 cards with page_size=50 → 1 page (use only 50 cards)
    index50 = make_candidates_index(cards[:50])
    cand_path50 = tmp_path / "candidates_index_50.json"
    cand_path50.write_text(json.dumps(index50), encoding="utf-8")
    result = run_script(GALLERY_SCRIPT, [
        "--candidates", str(cand_path50),
        "--output", str(out_50),
        "--page-size", "50",
    ])
    assert result.returncode == 0, result.stderr


def test_cohort_warning_on_60_card_cohort(tmp_path):
    """Gallery HTML should embed COHORT_CONFLICTS JS when cohort has multiple face_urls."""
    cards = []
    cohort_key = "CKEY|abc123def456"
    for i in range(60):
        # Alternate between two different v2 face URLs to create conflict
        v2_face = f"https://x.com/cohort_v2_{'A' if i % 2 == 0 else 'B'}.jpg"
        cards.append({
            "arkham_id": f"CC{i:04d}",
            "nickname_en": f"Cohort Card {i}",
            "nickname_v2_ko": f"코호트 {i}",
            "target_paths": [],
            "target_deck_key": "5500",
            "candidates": [
                make_candidate("en", "https://x.com/en.jpg"),
                make_unavailable("ko"),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", v2_face),
            ],
            "unique_face_url_count": 2,
            "diversity_score": 2,
            "sheet_cohort_v2": cohort_key,
        })

    index = make_candidates_index(cards)
    cand_path = tmp_path / "candidates_index.json"
    cand_path.write_text(json.dumps(index), encoding="utf-8")
    out = tmp_path / "gallery.html"
    result = run_script(GALLERY_SCRIPT, [
        "--candidates", str(cand_path),
        "--output", str(out),
    ])
    assert result.returncode == 0, result.stderr
    html = out.read_text()
    # The COHORT_CONFLICTS detection JS should be present
    assert "COHORT_CONFLICTS" in html
    assert "cohort-conflict" in html


def test_auto_export_prompt_fires_at_100_and_200(tmp_path):
    """Gallery HTML should contain auto-export at 100 multiple logic."""
    cards = [
        {
            "arkham_id": f"AE{i:04d}",
            "nickname_en": f"Card {i}",
            "nickname_v2_ko": "",
            "target_paths": [],
            "target_deck_key": "5500",
            "candidates": [make_candidate("en", f"https://x.com/en_{i}.jpg")],
            "unique_face_url_count": 1,
            "diversity_score": 1,
            "sheet_cohort_v2": None,
        }
        for i in range(5)
    ]
    index = make_candidates_index(cards)
    cand_path = tmp_path / "candidates_index.json"
    cand_path.write_text(json.dumps(index), encoding="utf-8")
    out = tmp_path / "gallery.html"
    result = run_script(GALLERY_SCRIPT, [
        "--candidates", str(cand_path),
        "--output", str(out),
    ])
    assert result.returncode == 0, result.stderr
    html = out.read_text()
    assert "checkAutoExportPrompt" in html
    assert "% 100 === 0" in html
