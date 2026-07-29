#!/usr/bin/env python3
"""Runnable acceptance gate for the sideways-investigator fix.

Part of the ``korean-pdf-card-atlases`` sideways-investigator fix
(.am/korean-pdf-card-atlases/design-investigator-fix.md §7). Replaces the
unmeasurable "~0.9 correlation" prose with a deterministic gate: fixed
thresholds, named reference assets, non-zero exit on any failure. Run AFTER the
recompose + both applies (§6 step 10); ``--r2-preflight`` is the pre-release
network gate (§6 step 11).

Checks (R = required for exit 0):
  R cell_isolation   only cells 18/32/51/56 of atlas-A-main changed pre->post
  R placement_main   each fixed face cell ~= the source crop placed there (>=0.95)
  R placement_back   each back cell ~= the source back crop placed there (>=0.95)
  R orientation      each face matches its EN reference upright, not flipped
  R extra_noop       the 12 taboo-A-extra FaceURLs are unchanged (idempotent)
  R cotenant_back    only the 4 investigators got a BackURL; backup holds 4
  R cardid           the 4 investigators satisfy CardID == 8001*100 + cell, UB
    r2_preflight     (only with --r2-preflight) both atlas shas resolve at R2 (200)

Exit codes:
  0  all required checks passed.
  1  one or more required checks failed.
  2  bad input (missing file / malformed manifest).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- fixed thresholds (design §7) -----------------------------------------
PLACEMENT_MIN = 0.95
ORIENT_ASIS_MIN = 0.50
ORIENT_MARGIN_MIN = 0.30

CORR_SIZE = (128, 182)  # grayscale resize for correlation (orientation-sensitive)
INVESTIGATOR_CELLS = (18, 32, 51, 56)

# Hard caps on untrusted remote reference images (decompression-bomb / OOM guard).
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024  # 256 MiB
MAX_IMAGE_PIXELS = 200_000_000          # 200 MP

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Named EN reference faces (design §7). cell index in the source EN sheet.
EN_FACE_REFS = {
    18: {  # Rex — EN deck-143, 1x1
        "name": "Rex",
        "url": "https://steamusercontent-a.akamaihd.net/ugc/2414565357243142219/"
               "EB53C43AB498B112B5EA2C488F0EB1543CD43DE5/",
        "grid": (1, 1), "cell": 0,
    },
    32: {  # Lola — EN deck-551, 2x2, cell 2
        "name": "Lola",
        "url": "https://steamusercontent-a.akamaihd.net/ugc/2450601300753083072/"
               "7500D69C546D9FD62750C45062986AE34060A8B1/",
        "grid": (2, 2), "cell": 2,
    },
    51: {  # Mandy — EN deck-551, 2x2, cell 1
        "name": "Mandy",
        "url": "https://steamusercontent-a.akamaihd.net/ugc/2450601300753083072/"
               "7500D69C546D9FD62750C45062986AE34060A8B1/",
        "grid": (2, 2), "cell": 1,
    },
    56: {  # Trish — EN deck-551, 2x2, cell 3
        "name": "Trish",
        "url": "https://steamusercontent-a.akamaihd.net/ugc/2450601300753083072/"
               "7500D69C546D9FD62750C45062986AE34060A8B1/",
        "grid": (2, 2), "cell": 3,
    },
}

R2_PUBLIC_BASE = (
    "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/images/"
)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Acceptance gate for the "
                                            "sideways-investigator fix.")
    p.add_argument("--track-dir", type=Path, required=True,
                   help="track-a-taboo output dir (holds atlas-A-main.png, "
                        "atlas_manifest.json, mapping.json, crops/, back/).")
    p.add_argument("--pre-atlas", type=Path, required=True,
                   help="Backup of the pre-fix atlas-A-main.png (cell isolation).")
    p.add_argument("--decomposed-root", type=Path, required=True,
                   help="Korean Player Cards decomposed object directory.")
    p.add_argument("--back-backup", type=Path, required=True,
                   help="pre_apply_back_backup.json written by apply-back-urls.py.")
    p.add_argument("--r2-preflight", action="store_true",
                   help="Also assert both atlas shas resolve at R2 (HTTP 200).")
    return p.parse_args(argv)


# --- helpers ---------------------------------------------------------------


def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"InputError: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def cell_box(width, height, num_width, num_height, cell):
    cw = width // num_width
    ch = height // num_height
    x = cell % num_width
    y = cell // num_width
    return (x * cw, y * ch, (x + 1) * cw, (y + 1) * ch)


def cell_image(atlas_img, num_width, num_height, cell):
    return atlas_img.crop(cell_box(atlas_img.width, atlas_img.height,
                                   num_width, num_height, cell))


def corr(img_a, img_b, *, np):
    a = np.asarray(img_a.convert("L").resize(CORR_SIZE), dtype="float64").ravel()
    b = np.asarray(img_b.convert("L").resize(CORR_SIZE), dtype="float64").ravel()
    a = a - a.mean()
    b = b - b.mean()
    da = (a * a).sum() ** 0.5
    db = (b * b).sum() ** 0.5
    if da == 0 or db == 0:
        return 0.0
    return float((a * b).sum() / (da * db))


def download_image(url, *, image_mod):
    import io
    import requests
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=30,
                        stream=True)
    try:
        resp.raise_for_status()
        declared = resp.headers.get("Content-Length")
        if declared is not None and int(declared) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Content-Length {declared} exceeds cap "
                             f"{MAX_DOWNLOAD_BYTES} bytes")
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=1 << 16):
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"body exceeds cap {MAX_DOWNLOAD_BYTES} bytes")
            chunks.append(chunk)
    finally:
        resp.close()
    return image_mod.open(io.BytesIO(b"".join(chunks))).convert("RGB")


def find_atlas(manifest, atlas_id):
    for a in manifest.get("atlases", []):
        if a.get("atlas_id") == atlas_id:
            return a
    return None


def crop_for(mapping, atlas_id, cell):
    """The mapping crop placed at (atlas_id, cell) — the source image for a cell."""
    for e in mapping.get("entries", []):
        if (e.get("atlas") == atlas_id
                and int(e.get("target_cell_index", -1)) == cell
                and e.get("playset_dupe_of") is None):
            return e.get("crop")
    return None


# --- checks ----------------------------------------------------------------


def check_cell_isolation(post, pre, manifest_main, *, image_mod, results):
    nw, nh = manifest_main["num_width"], manifest_main["num_height"]
    with image_mod.open(post) as a, image_mod.open(pre) as b:
        a = a.convert("RGB")
        b = b.convert("RGB")
        if a.size != b.size:
            results.append(("cell_isolation", False,
                            f"size changed {b.size}->{a.size}"))
            return
        changed = []
        for cell in range(nw * nh):
            box = cell_box(a.width, a.height, nw, nh, cell)
            if a.crop(box).tobytes() != b.crop(box).tobytes():
                changed.append(cell)
    ok = set(changed) == set(INVESTIGATOR_CELLS)
    results.append(("cell_isolation", ok,
                    f"changed cells {sorted(changed)} "
                    f"(expected {list(INVESTIGATOR_CELLS)})"))


def check_placement(atlas_png, manifest_atlas, mapping, atlas_id, crops_dir,
                    *, image_mod, np, results, label):
    nw, nh = manifest_atlas["num_width"], manifest_atlas["num_height"]
    with image_mod.open(atlas_png) as atlas:
        atlas = atlas.convert("RGB")
        worst = 1.0
        detail = []
        for cell in INVESTIGATOR_CELLS:
            crop_rel = crop_for(mapping, atlas_id, cell)
            if not crop_rel:
                results.append((label, False, f"no mapping crop for cell {cell}"))
                return
            crop_path = crops_dir / crop_rel
            if not crop_path.exists():
                results.append((label, False, f"missing crop {crop_path}"))
                return
            with image_mod.open(crop_path) as crop:
                c = corr(cell_image(atlas, nw, nh, cell), crop.convert("RGB"), np=np)
            worst = min(worst, c)
            detail.append(f"{cell}:{c:.3f}")
    ok = worst >= PLACEMENT_MIN
    results.append((label, ok, f"min corr {worst:.3f} >= {PLACEMENT_MIN} "
                               f"[{', '.join(detail)}]"))


def check_orientation(atlas_png, manifest_main, *, image_mod, np, results):
    nw, nh = manifest_main["num_width"], manifest_main["num_height"]
    detail = []
    ok = True
    sheet_cache: dict[str, object] = {}  # url -> Image; Lola/Mandy/Trish share one
    with image_mod.open(atlas_png) as atlas:
        atlas = atlas.convert("RGB")
        for cell in INVESTIGATOR_CELLS:
            ref = EN_FACE_REFS[cell]
            url = ref["url"]
            if url not in sheet_cache:
                try:
                    sheet_cache[url] = download_image(url, image_mod=image_mod)
                except Exception as exc:  # noqa: BLE001 — fail-closed on a gate
                    # Record this ref as failed but keep checking the rest, so the
                    # gate reports every problem rather than only the first.
                    ok = False
                    detail.append(f"{ref['name']}(download failed: {exc})")
                    continue
            sheet = sheet_cache[url]
            gw, gh = ref["grid"]
            ref_cell = sheet.crop(cell_box(sheet.width, sheet.height, gw, gh,
                                           ref["cell"]))
            face = cell_image(atlas, nw, nh, cell)
            asis = corr(face, ref_cell, np=np)
            rot = corr(face.rotate(180), ref_cell, np=np)
            margin = asis - rot
            this_ok = asis >= ORIENT_ASIS_MIN and margin >= ORIENT_MARGIN_MIN
            ok = ok and this_ok
            detail.append(f"{ref['name']}(asis={asis:.2f},mgn={margin:.2f})")
    results.append(("orientation", ok,
                    f"asis>={ORIENT_ASIS_MIN} & margin>={ORIENT_MARGIN_MIN}: "
                    f"{', '.join(detail)}"))


def check_extra_noop(manifest_main_doc, decomposed_root, *, results):
    extra = find_atlas(manifest_main_doc, "taboo-A-extra")
    if extra is None:
        results.append(("extra_noop", False, "no taboo-A-extra atlas in manifest"))
        return
    extra_url = extra.get("face_url", "")
    bad = []
    for c in extra.get("cells", []):
        path = decomposed_root / Path(c["source_file"]).name
        if not path.exists():
            bad.append(f"{c['source_file']} missing")
            continue
        card = load_json(path)
        cd = card.get("CustomDeck", {})
        key = str(c.get("target_deck_key"))
        face = cd.get(key, {}).get("FaceURL") if key in cd else None
        if face is None and cd:
            face = next(iter(cd.values())).get("FaceURL")
        if face != extra_url:
            bad.append(f"{c['source_file']} FaceURL drifted")
    results.append(("extra_noop", not bad,
                    f"{len(extra.get('cells', []))} extra cards on atlas url"
                    + (f"; problems={bad}" if bad else "")))


def check_cotenant_back(back_manifest_doc, back_backup_doc, face_manifest_doc,
                        decomposed_root, *, results):
    atlas = (back_manifest_doc.get("atlases") or [{}])[0]
    back_url = atlas.get("face_url", "")
    cells = atlas.get("cells", [])
    sources = [Path(c["source_file"]).name for c in cells]
    investigator_set = set(sources)
    backup_cards = back_backup_doc.get("cards", {})
    problems = []
    if len(backup_cards) != 4:
        problems.append(f"backup has {len(backup_cards)} cards (expected 4)")
    if len(sources) != 4:
        problems.append(f"back manifest has {len(sources)} cells (expected 4)")
    for sf in sources:
        path = decomposed_root / sf
        if not path.exists():
            problems.append(f"{sf} missing")
            continue
        card = load_json(path)
        cd = card.get("CustomDeck", {})
        entry = cd.get("8001") or (next(iter(cd.values())) if cd else {})
        if entry.get("BackURL") != back_url:
            problems.append(f"{sf} BackURL != back atlas url")

    # Co-tenant guarantee (design §7): the non-investigator taboo-A-main cards
    # must NOT carry the back atlas URL. back_url is a fresh sha-derived URL unique
    # to this build, so the only way apply-back-urls.py could corrupt a co-tenant
    # is by writing back_url onto it — checking no other card holds back_url makes
    # the "56 unchanged" claim runtime-verified, not safe-by-construction only.
    main_atlas = find_atlas(face_manifest_doc, "taboo-A-main")
    breaches = []
    checked = 0
    if main_atlas is None:
        problems.append("no taboo-A-main atlas in face manifest")
    else:
        for c in main_atlas.get("cells", []):
            name = Path(c.get("source_file", "")).name
            if not name or name in investigator_set:
                continue
            path = decomposed_root / name
            if not path.exists():
                continue
            card = load_json(path)
            checked += 1
            if any(e.get("BackURL") == back_url
                   for e in card.get("CustomDeck", {}).values()):
                breaches.append(name)
    if breaches:
        problems.append(f"{len(breaches)} non-investigator card(s) carry the back "
                        f"url: {sorted(breaches)[:5]}")
    results.append(("cotenant_back", not problems,
                    f"4 investigators on back url; {checked} non-investigator "
                    f"taboo-A-main cards back-url-free"
                    + (f"; problems={problems}" if problems else "")))


def check_cardid(back_manifest_doc, decomposed_root, *, results):
    atlas = (back_manifest_doc.get("atlases") or [{}])[0]
    problems = []
    for c in atlas.get("cells", []):
        cell = int(c["target_cell_index"])
        path = decomposed_root / Path(c["source_file"]).name
        if not path.exists():
            problems.append(f"{c['source_file']} missing")
            continue
        card = load_json(path)
        cd = card.get("CustomDeck", {})
        entry = cd.get("8001")
        expected = 8001 * 100 + cell
        if "8001" not in cd:
            problems.append(f"{c['source_file']} not on deck 8001")
            continue
        if card.get("CardID") != expected:
            problems.append(f"{c['source_file']} CardID {card.get('CardID')} "
                            f"!= {expected}")
        if entry.get("NumWidth") != 10 or entry.get("NumHeight") != 6:
            problems.append(f"{c['source_file']} grid != 10x6")
        if entry.get("UniqueBack") is not True:
            problems.append(f"{c['source_file']} UniqueBack != True")
    results.append(("cardid", not problems,
                    "CardID==8001*100+cell, 10x6, UniqueBack"
                    + (f"; problems={problems}" if problems else "")))


def check_r2_preflight(main_atlas, back_atlas, *, results):
    import requests
    problems = []
    for label, atlas in (("face", main_atlas), ("back", back_atlas)):
        url = atlas.get("face_url", "")
        try:
            r = requests.get(url, headers={"User-Agent": BROWSER_UA},
                             stream=True, timeout=30)
            if r.status_code != 200:
                problems.append(f"{label} {url} -> HTTP {r.status_code}")
            r.close()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label} {url} -> {exc}")
    results.append(("r2_preflight", not problems,
                    "both atlas shas resolve at R2 (200)"
                    + (f"; problems={problems}" if problems else "")))


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        from PIL import Image as image_mod
        import numpy as np
    except ImportError as exc:
        print(f"ERROR: required dependency missing ({exc}). "
              "Install with: pip install -r requirements.txt", file=sys.stderr)
        return 2
    image_mod.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS  # explicit bomb guard

    track = args.track_dir
    main_png = track / "atlas-A-main.png"
    back_png = track / "back" / "atlas-A-back.png"
    face_manifest_doc = load_json(track / "atlas_manifest.json")
    back_manifest_doc = load_json(track / "back" / "atlas_manifest.json")
    mapping = load_json(track / "mapping.json")
    back_backup_doc = load_json(args.back_backup)

    main_atlas = find_atlas(face_manifest_doc, "taboo-A-main")
    back_atlas_entry = find_atlas(back_manifest_doc, "taboo-A-back")
    if main_atlas is None or back_atlas_entry is None:
        print("InputError: manifest missing taboo-A-main or taboo-A-back",
              file=sys.stderr)
        return 2

    results: list[tuple[str, bool, str]] = []

    check_cell_isolation(main_png, args.pre_atlas, main_atlas,
                         image_mod=image_mod, results=results)
    check_placement(main_png, main_atlas, mapping, "taboo-A-main", track,
                    image_mod=image_mod, np=np, results=results,
                    label="placement_main")
    check_placement(back_png, back_atlas_entry, mapping, "taboo-A-back", track,
                    image_mod=image_mod, np=np, results=results,
                    label="placement_back")
    check_orientation(main_png, main_atlas, image_mod=image_mod, np=np,
                      results=results)
    check_extra_noop(face_manifest_doc, args.decomposed_root, results=results)
    check_cotenant_back(back_manifest_doc, back_backup_doc, face_manifest_doc,
                        args.decomposed_root, results=results)
    check_cardid(back_manifest_doc, args.decomposed_root, results=results)
    if args.r2_preflight:
        check_r2_preflight(main_atlas, back_atlas_entry, results=results)

    print("\n=== verify-investigator-fix ===")
    all_ok = True
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        all_ok = all_ok and ok
    print(f"=== {'ALL PASS' if all_ok else 'FAILURES PRESENT'} ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
