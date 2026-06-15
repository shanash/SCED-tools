#!/usr/bin/env python3
"""Helper script to generate 180_card_candidates_index.json and 180_card_decisions.json."""
import hashlib
import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent
CHECKED_AT_FRESH = "2099-01-01T00:00:00+00:00"
CHECKED_AT_STALE = "2000-01-01T00:00:00+00:00"
BASE_BACK = "https://arkhamdb.example.com/back_player.jpg"


def sha1_prefix(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def make_url_status(status="ok", checked_at=CHECKED_AT_FRESH):
    return {
        "face": {"status": status, "checked_at": checked_at},
        "back": {"status": status, "checked_at": checked_at}
    }


cards = []
decisions = []

# Cohort A: 60 cards on same sheet, mixed choices (30 v2 + 20 v0 + 10 v1)
COHORT_A_FACE_V2 = "https://sheet.example.com/cohort_A_v2.jpg"
COHORT_A_COHORT = f"cohortA|{sha1_prefix(COHORT_A_FACE_V2)}"

for i in range(60):
    ark_id = f"cohA_{i:04d}"
    dk = "8000"
    card_id = 800000 + i
    face_en = f"https://sheet.example.com/en/cohort_A.jpg"
    face_v0 = f"https://sheet.example.com/v0/cohort_A.jpg"
    face_v1 = f"https://sheet.example.com/v1/cohort_A.jpg"
    face_v2 = COHORT_A_FACE_V2

    card = {
        "arkham_id": ark_id,
        "nickname_en": f"Cohort A Card {i}",
        "nickname_v2_ko": f"코호트 A {i}",
        "target_paths": [f"/fake/decomposed/{ark_id}.json"],
        "target_deck_key": dk,
        "candidates": [
            {"version": "en", "available": True, "face_url": face_en, "back_url": BASE_BACK,
             "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
            {"version": "v0", "available": True, "face_url": face_v0, "back_url": BASE_BACK,
             "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
            {"version": "v1", "available": True, "face_url": face_v1, "back_url": BASE_BACK,
             "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
            {"version": "v2", "available": True, "face_url": face_v2, "back_url": BASE_BACK,
             "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
        ],
        "unique_face_url_count": 4,
        "diversity_score": 4,
        "sheet_cohort_v2": COHORT_A_COHORT,
    }
    cards.append(card)

    # 30 v2, 20 v0, 10 v1
    if i < 30:
        choice, face, back = "v2", face_v2, BASE_BACK
    elif i < 50:
        choice, face, back = "v0", face_v0, BASE_BACK
    else:
        choice, face, back = "v1", face_v1, BASE_BACK

    decisions.append({
        "arkham_id": ark_id,
        "choice": choice,
        "face_url": face,
        "back_url": back,
        "num_width": 10,
        "num_height": 7,
        "deck_key": dk,
        "note": "",
        "decided_at": CHECKED_AT_FRESH,
    })

# Cohort B: 30 cards, uniform v2
COHORT_B_FACE_V2 = "https://sheet.example.com/cohort_B_v2.jpg"
COHORT_B_COHORT = f"cohortB|{sha1_prefix(COHORT_B_FACE_V2)}"

for i in range(30):
    ark_id = f"cohB_{i:04d}"
    dk = "8001"
    card_id = 800100 + i
    face_en = "https://sheet.example.com/en/cohort_B.jpg"
    face_v2 = COHORT_B_FACE_V2

    card = {
        "arkham_id": ark_id,
        "nickname_en": f"Cohort B Card {i}",
        "nickname_v2_ko": f"코호트 B {i}",
        "target_paths": [f"/fake/decomposed/{ark_id}.json"],
        "target_deck_key": dk,
        "candidates": [
            {"version": "en", "available": True, "face_url": face_en, "back_url": BASE_BACK,
             "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
            {"version": "v0", "available": False},
            {"version": "v1", "available": False},
            {"version": "v2", "available": True, "face_url": face_v2, "back_url": BASE_BACK,
             "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
        ],
        "unique_face_url_count": 2,
        "diversity_score": 2,
        "sheet_cohort_v2": COHORT_B_COHORT,
    }
    cards.append(card)
    decisions.append({
        "arkham_id": ark_id,
        "choice": "v2",
        "face_url": face_v2,
        "back_url": BASE_BACK,
        "num_width": 10,
        "num_height": 7,
        "deck_key": dk,
        "note": "",
        "decided_at": CHECKED_AT_FRESH,
    })

# Singletons: 30 cards, diversity_score=1
for i in range(30):
    ark_id = f"sing_{i:04d}"
    dk = f"80{2 + i:02d}"
    card_id = 800200 + i
    face_url = f"https://sheet.example.com/singleton/card_{i}.jpg"

    card = {
        "arkham_id": ark_id,
        "nickname_en": f"Singleton Card {i}",
        "nickname_v2_ko": "",
        "target_paths": [f"/fake/decomposed/{ark_id}.json"],
        "target_deck_key": dk,
        "candidates": [
            {"version": "en", "available": True, "face_url": face_url, "back_url": BASE_BACK,
             "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
            {"version": "v0", "available": False},
            {"version": "v1", "available": False},
            {"version": "v2", "available": False},
        ],
        "unique_face_url_count": 1,
        "diversity_score": 1,
        "sheet_cohort_v2": None,
    }
    cards.append(card)
    decisions.append({
        "arkham_id": ark_id,
        "choice": "en",
        "face_url": face_url,
        "back_url": BASE_BACK,
        "num_width": 10,
        "num_height": 7,
        "deck_key": dk,
        "note": "",
        "decided_at": CHECKED_AT_FRESH,
    })

# Diversity variants: 60 cards
# 1 fan-out (two target paths), 1 tampered face_url marker, 1 stale, rest normal diversity combos
for i in range(60):
    ark_id = f"div_{i:04d}"
    dk = f"90{i:02d}"
    card_id = 900000 + i

    # Varied availability combinations
    has_v0 = i % 2 == 0
    has_v1 = i % 3 != 0
    has_v2 = True

    face_en = f"https://sheet.example.com/div/en_{i}.jpg"
    face_v0 = f"https://sheet.example.com/div/v0_{i}.jpg" if has_v0 else None
    face_v1 = f"https://sheet.example.com/div/v1_{i}.jpg" if has_v1 else None
    face_v2 = f"https://sheet.example.com/div/v2_{i}.jpg"

    cohort = f"div{i}|{sha1_prefix(face_v2)}"

    unique_faces = list(dict.fromkeys(
        f for f in [face_en, face_v0, face_v1, face_v2] if f
    ))
    diversity = len(unique_faces)

    cands = [
        {"version": "en", "available": True, "face_url": face_en, "back_url": BASE_BACK,
         "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()},
    ]
    if has_v0:
        cands.append({"version": "v0", "available": True, "face_url": face_v0, "back_url": BASE_BACK,
                      "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()})
    else:
        cands.append({"version": "v0", "available": False})

    if has_v1:
        cands.append({"version": "v1", "available": True, "face_url": face_v1, "back_url": BASE_BACK,
                      "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()})
    else:
        cands.append({"version": "v1", "available": False})

    # Card 1: stale url_status
    if i == 1:
        cands.append({"version": "v2", "available": True, "face_url": face_v2, "back_url": BASE_BACK,
                      "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id,
                      "url_status": make_url_status("ok", CHECKED_AT_STALE)})
    else:
        cands.append({"version": "v2", "available": True, "face_url": face_v2, "back_url": BASE_BACK,
                      "num_width": 10, "num_height": 7, "deck_key": dk, "card_id": card_id, "url_status": make_url_status()})

    # Card 0: fan-out (two target paths)
    target_paths = [f"/fake/decomposed/{ark_id}.json"]
    if i == 0:
        target_paths.append(f"/fake/decomposed/{ark_id}_alt.json")

    card = {
        "arkham_id": ark_id,
        "nickname_en": f"Diversity Card {i}",
        "nickname_v2_ko": f"다양성 카드 {i}" if has_v2 else "",
        "target_paths": target_paths,
        "target_deck_key": dk,
        "candidates": cands,
        "unique_face_url_count": diversity,
        "diversity_score": diversity,
        "sheet_cohort_v2": cohort,
    }
    cards.append(card)

    # Card 2: tampered face_url in decisions (wrong URL to trigger tamper detect)
    if i == 2:
        decisions.append({
            "arkham_id": ark_id,
            "choice": "v2",
            "face_url": "https://tampered.example.com/wrong.jpg",  # tampered
            "back_url": BASE_BACK,
            "num_width": 10,
            "num_height": 7,
            "deck_key": dk,
            "note": "tampered",
            "decided_at": CHECKED_AT_FRESH,
        })
    else:
        decisions.append({
            "arkham_id": ark_id,
            "choice": "v2",
            "face_url": face_v2,
            "back_url": BASE_BACK,
            "num_width": 10,
            "num_height": 7,
            "deck_key": dk,
            "note": "",
            "decided_at": CHECKED_AT_FRESH,
        })

assert len(cards) == 180, f"Expected 180 cards, got {len(cards)}"
assert len(decisions) == 180, f"Expected 180 decisions, got {len(decisions)}"

# Build candidates index
by_diversity = {}
for c in cards:
    k = str(c["diversity_score"])
    by_diversity[k] = by_diversity.get(k, 0) + 1

index = {
    "schema_version": 1,
    "generated_at": CHECKED_AT_FRESH,
    "sources": {
        "en": {"path": "/fake/decomposed", "card_count": 180},
        "v0": {"path": "/fake/sources/v0.json", "card_count": 90, "available": True},
        "v1": {"path": "/fake/sources/v1.json", "card_count": 110, "available": True},
        "v2": {"path": "/fake/sources/v2.json", "card_count": 180, "available": True},
    },
    "cards": cards,
    "summary": {
        "total_cards": 180,
        "by_diversity": by_diversity,
        "v0_coverage": sum(1 for c in cards if any(x["version"] == "v0" and x.get("available") for x in c["candidates"])),
        "v1_coverage": sum(1 for c in cards if any(x["version"] == "v1" and x.get("available") for x in c["candidates"])),
        "v2_coverage": sum(1 for c in cards if any(x["version"] == "v2" and x.get("available") for x in c["candidates"])),
    },
}

# Build decisions file
decisions_file = {
    "schema_version": 1,
    "generated_at": CHECKED_AT_FRESH,
    "gallery_id": "20990101T000000Z",
    "decisions": decisions,
    "stats": {
        "total_cards": 180,
        "decided": 180,
        "by_choice": {"en": 0, "v0": 0, "v1": 0, "v2": 0, "skip": 0},
        "skipped_explicit": 0,
        "undecided": 0,
    },
}

(OUT_DIR / "180_card_candidates_index.json").write_text(
    json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
)
(OUT_DIR / "180_card_decisions.json").write_text(
    json.dumps(decisions_file, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"Generated {len(cards)}-card fixture files in {OUT_DIR}")
