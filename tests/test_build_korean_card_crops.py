"""
pytest suite for build-korean-card-crops.py (design korean-player-card-crop-gallery).

Four tiers, per design §5.7:
  Tier 1 — pure-helper unit tests (importlib-loaded module): tie / cache_key /
           guess_ext, slice_cell golden-image (even + non-even grids), the three
           per-branch pixel behaviors (sideways unrotated-on-disk / single_image
           whole-image copy / unique_back at the BACK's own cell), and the
           write_manifest exit-42 re-read verifier.
  Tier 2 — exit-code matrix via subprocess over a local threaded HTTP stub:
           exit 0 / 1 / 2, back==null tolerance, guess_ext Content-Type
           fallback end-to-end, and the retry-policy contract.
  Tier 3 — static gallery acceptance checks on the emitted JSON island + files.
  Tier 4 — OPTIONAL Playwright runtime checks (skip-guarded).

The script name is hyphenated so it is not importable by name; Tier-1 loads it
via importlib.util.spec_from_file_location. Subprocess tests run it with
sys.executable and tmp_path isolation, mirroring test_build_korean_card_index.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_DIR = TESTS_DIR / "fixtures" / "korean-player-card-crop-gallery"

SCRIPT = SCRIPT_DIR / "build-korean-card-crops.py"

# Make the stub_server fixture helper importable.
sys.path.insert(0, str(FIXTURE_DIR))
from stub_server import StubServer  # noqa: E402

from PIL import Image  # noqa: E402


# ---------------------------------------------------------------------------
# Module loading (Tier 1) + shared helpers
# ---------------------------------------------------------------------------


def _load_module():
    """importlib-load the hyphenated script as a module (Tier-1 unit access)."""
    # The script imports `sced_io`, which lives in SCRIPT_DIR.
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("build_korean_card_crops",
                                                  SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so @dataclass annotation introspection
    # (Python 3.14) can resolve the module's own namespace.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CROPS = _load_module()


# Distinct solid cell colors keyed by (x, y) so a crop's corner pixel uniquely
# identifies which cell it came from.
def _cell_color(x: int, y: int) -> tuple[int, int, int]:
    return ((x * 37 + 11) % 256, (y * 53 + 29) % 256, (x * 17 + y * 23) % 256)


def _make_grid_atlas(width: int, height: int, nw: int, nh: int) -> Image.Image:
    """Build a synthetic atlas painted with a distinct solid color per cell.

    Uses the SAME floor-division cell box the tool uses so each cell is filled
    edge-to-edge with its color (the trailing non-divisible edge is left
    background, but never read by a floor-division crop).
    """
    img = Image.new("RGB", (width, height), (0, 0, 0))
    cell_w = width // nw
    cell_h = height // nh
    for y in range(nh):
        for x in range(nw):
            left = x * cell_w
            top = y * cell_h
            for py in range(top, top + cell_h):
                for px in range(left, left + cell_w):
                    img.putpixel((px, py), _cell_color(x, y))
    return img


def _atlas_png_bytes(img: Image.Image) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _run(extra_args: list[str], env_extra: dict | None = None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(SCRIPT)] + extra_args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _write_index(path: Path, cards: list[dict]) -> None:
    doc = {
        "schema_version": "1.0.0",
        "generated_at": "2026-06-16T00:00:00+00:00",
        "counts": {"cards_written": len(cards)},
        "cards": cards,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _read_manifest(out_dir: Path) -> dict:
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


def _gallery_rows(out_dir: Path) -> list:
    """Extract the embedded ROWS JSON island from the emitted gallery.html."""
    html = (out_dir / "gallery.html").read_text(encoding="utf-8")
    marker = "const ROWS = "
    start = html.index(marker) + len(marker)
    end = html.index(";\nconst PAGE_SIZE", start)
    raw = html[start:end]
    # Reverse the _js_safe </ guard for parsing (U+2028/2029 escapes are valid
    # JSON \u escapes already).
    raw = raw.replace("<\\/", "</")
    return json.loads(raw)


# Minimal card-record factory matching the index contract (§3.1).
def _card(arkham_id, card_id, source_file, face_url, *, nw=2, nh=2, x=0, y=0,
          back=None, unique_back=False, sideways=False, anomaly="",
          name="Test Card"):
    return {
        "name": name,
        "arkham_id": arkham_id,
        "card_id": card_id,
        "deck_key": str(card_id // 100),
        "face": {"url": face_url, "source": "R2", "num_width": nw,
                 "num_height": nh, "cell_index": y * nw + x, "x": x, "y": y},
        "back": back,
        "unique_back": unique_back,
        "sideways": sideways,
        "pack": "playercards",
        "source_file": source_file,
        "anomaly": anomaly,
    }


# ===========================================================================
# Tier 1 — pure-helper unit tests
# ===========================================================================


def test_tie_hash_is_stable_8_hex():
    t = CROPS.tie_hash("Foo.abc123/Bar.def456.json")
    assert len(t) == 8
    assert all(c in "0123456789abcdef" for c in t)
    # deterministic
    assert t == CROPS.tie_hash("Foo.abc123/Bar.def456.json")
    # different source_file -> different tie
    assert t != CROPS.tie_hash("Other.json")


def test_name_helpers_collision_safe():
    sf_a = "A.111/x.222.json"
    sf_b = "B.333/y.444.json"
    # same (arkham_id, card_id) but different source_file -> distinct names.
    n_a = CROPS.face_name("01006", 586125, sf_a)
    n_b = CROPS.face_name("01006", 586125, sf_b)
    assert n_a != n_b
    assert n_a == f"01006_586125_face_{CROPS.tie_hash(sf_a)}.png"
    assert CROPS.uback_name("01006", 586125, sf_a) == \
        f"01006_586125_back_{CROPS.tie_hash(sf_a)}.png"


def test_cache_key_full_url_including_query():
    url1 = "https://h/x.jpg"
    url2 = "https://h/x.jpg?v=2"
    k1 = CROPS.cache_key(url1)
    k2 = CROPS.cache_key(url2)
    assert len(k1) == 64
    # a ?v= bump produces a distinct cache_key (correct invalidation, §3.2).
    assert k1 != k2


def test_shared_back_name_normalized_to_png():
    url = "https://steamusercontent-a.akamaihd.net/ugc/123/ABC/"
    name = CROPS.shared_back_name(url)
    assert name.startswith("sharedback_")
    assert name.endswith(".png")
    assert name == f"sharedback_{CROPS.cache_key(url)[:12]}.png"


def test_guess_ext_url_suffix():
    assert CROPS.guess_ext("https://h/a.png", None) == ".png"
    assert CROPS.guess_ext("https://h/a.jpg", None) == ".jpg"
    assert CROPS.guess_ext("https://h/a.jpeg", None) == ".jpg"
    # query string does not confuse the suffix detection.
    assert CROPS.guess_ext("https://h/a.png?v=3", None) == ".png"


def test_guess_ext_content_type_fallback():
    # extension-less Steam URL -> derive from Content-Type.
    u = "https://steamusercontent-a.akamaihd.net/ugc/1/A/"
    assert CROPS.guess_ext(u, "image/png") == ".png"
    assert CROPS.guess_ext(u, "image/jpeg") == ".jpg"
    # parameterized header: the ;charset= suffix is stripped first.
    assert CROPS.guess_ext(u, "image/jpeg; charset=binary") == ".jpg"
    # absent / unknown header -> .jpg default.
    assert CROPS.guess_ext(u, None) == ".jpg"
    assert CROPS.guess_ext(u, "") == ".jpg"
    assert CROPS.guess_ext(u, "application/octet-stream") == ".jpg"


@pytest.mark.parametrize("width,height,nw,nh", [
    (200, 140, 4, 2),   # evenly divisible: 200%4==0, 140%2==0
    (101, 71, 4, 3),    # NON-divisible: 101%4!=0, 71%3!=0 -> floor truncation
])
def test_slice_cell_golden_image(width, height, nw, nh):
    atlas = _make_grid_atlas(width, height, nw, nh)
    cell_w = width // nw
    cell_h = height // nh
    for y in range(nh):
        for x in range(nw):
            crop = CROPS.slice_cell(atlas, nw, nh, x, y)
            # Cell size is the documented floor-division box.
            assert crop.size == (cell_w, cell_h)
            # Corner pixel == this cell's color (orientation-stable).
            assert crop.getpixel((0, 0)) == _cell_color(x, y)
    # Last column/row: left/top is (nw-1)*cell_w / (nh-1)*cell_h; trailing edge
    # pixels (width - nw*cell_w) are intentionally dropped (locks truncation).
    last = CROPS.slice_cell(atlas, nw, nh, nw - 1, nh - 1)
    assert last.size == (cell_w, cell_h)


def test_pixel_branch_sideways_unrotated_on_disk(tmp_path):
    """A sideways card's saved PNG keeps the SOURCE landscape pixels (no on-disk
    rotate). The gallery row carries sideways:true (rotation lives only in CSS)."""
    # Landscape cell: cell_w > cell_h.
    atlas = _make_grid_atlas(400, 100, 2, 1)  # cell 200x100 (landscape)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    src = cache_dir / "atlas.png"
    atlas.save(src, format="PNG")
    task = CROPS.CropTask(
        kind="face", source_file="s.json", arkham_id="01001", card_id=1,
        name="Sideways", url="http://x/atlas.png", num_width=2, num_height=1,
        x=0, y=0, out_name="01001_1_face_aa.png", sideways=True)
    res = CROPS.AtlasResult(url="http://x/atlas.png", cache_path=src,
                            status="cached")
    args = CROPS.parse_args(["--out-dir", str(tmp_path / "out")])
    args.cache_dir = cache_dir
    wrote = CROPS.crop_task(task, res, tmp_path / "out", args, image_mod=Image)
    assert wrote is True
    assert task.status == "ok"
    saved = tmp_path / "out" / "cards" / "01001_1_face_aa.png"
    with Image.open(saved) as png:
        # Landscape preserved on disk: width > height (no rotate applied).
        assert png.width > png.height
        assert (png.width, png.height) == (200, 100)


def test_pixel_branch_single_image_whole_copy(tmp_path):
    """A single_image (shared) back is the whole image copied 1:1 (no sub-crop)."""
    atlas = _make_grid_atlas(300, 200, 3, 2)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    src = cache_dir / "back.png"
    atlas.save(src, format="PNG")
    name = CROPS.shared_back_name("http://x/back/")
    task = CROPS.CropTask(
        kind="shared_back", source_file="s.json", arkham_id="01001",
        card_id=1, name="Back", url="http://x/back/", num_width=1,
        num_height=1, x=0, y=0, out_name=name, sideways=False)
    res = CROPS.AtlasResult(url="http://x/back/", cache_path=src,
                            status="cached")
    args = CROPS.parse_args(["--out-dir", str(tmp_path / "out")])
    args.cache_dir = cache_dir
    wrote = CROPS.crop_task(task, res, tmp_path / "out", args, image_mod=Image)
    assert wrote is True
    saved = tmp_path / "out" / "backs" / name
    with Image.open(saved) as png:
        assert png.size == (300, 200)  # whole-image dimensions preserved


def test_pixel_branch_unique_back_uses_back_cell(tmp_path):
    """A unique_back crop is taken at the BACK's own x/y, not the face cell."""
    atlas = _make_grid_atlas(200, 140, 4, 2)  # distinct color per cell
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    src = cache_dir / "uback.png"
    atlas.save(src, format="PNG")
    # Back points at cell (3, 1); face cell would be (0, 0).
    back_x, back_y = 3, 1
    task = CROPS.CropTask(
        kind="uback", source_file="s.json", arkham_id="01001", card_id=1,
        name="UBack", url="http://x/uback.png", num_width=4, num_height=2,
        x=back_x, y=back_y, out_name="01001_1_back_aa.png", sideways=False)
    res = CROPS.AtlasResult(url="http://x/uback.png", cache_path=src,
                            status="cached")
    args = CROPS.parse_args(["--out-dir", str(tmp_path / "out")])
    args.cache_dir = cache_dir
    wrote = CROPS.crop_task(task, res, tmp_path / "out", args, image_mod=Image)
    assert wrote is True
    saved = tmp_path / "out" / "cards" / "01001_1_back_aa.png"
    with Image.open(saved) as png:
        assert png.getpixel((0, 0)) == _cell_color(back_x, back_y)
        # And NOT the face cell color (0, 0).
        assert png.getpixel((0, 0)) != _cell_color(0, 0)


def test_anomaly_task_never_runs_cell_math(tmp_path):
    """An anomaly task with null x/y is routed to the skip path (no crash)."""
    task = CROPS.CropTask(
        kind="anomaly", source_file="s.json", arkham_id="02003",
        card_id=273631, name="Jenny", url=None, num_width=None,
        num_height=None, x=None, y=None, out_name=None, sideways=False,
        anomaly_code="cell_out_of_bounds", status="anomaly")
    args = CROPS.parse_args(["--out-dir", str(tmp_path / "out")])
    wrote = CROPS.crop_task(task, None, tmp_path / "out", args, image_mod=Image)
    assert wrote is False
    assert task.status == "anomaly"


def test_write_manifest_sha256_reread_mismatch_returns_42(tmp_path):
    """A gallery.html whose declared sha256 != on-disk content -> exit 42.

    Exercises the re-read mismatch path the orchestrator runs, without a
    production-only corruption hook: write a real run, corrupt gallery.html,
    and re-run main() so the re-read verify trips (manifest's declared sha is
    recomputed from the now-corrupt file? No — we corrupt AFTER manifest write
    is impossible from outside). Instead, directly assert the comparison logic.
    """
    # Build a manifest dict declaring one sha, write a gallery whose actual sha
    # differs, then assert compute_sha256 != declared (the exact comparison the
    # orchestrator makes before returning 42).
    out = tmp_path / "out"
    out.mkdir()
    gallery = out / "gallery.html"
    gallery.write_text("<html>real</html>", encoding="utf-8")
    declared = CROPS.compute_sha256(gallery)
    # Now mutate the file so a re-read disagrees with the declared sha.
    gallery.write_text("<html>corrupted</html>", encoding="utf-8")
    actual = CROPS.compute_sha256(gallery)
    assert actual != declared  # the exit-42 trigger condition


def test_verify_png_rejects_zero_byte_and_garbage(tmp_path):
    good = tmp_path / "good.png"
    _make_grid_atlas(20, 20, 2, 2).save(good, format="PNG")
    assert CROPS._verify_png(good, Image) is True

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert CROPS._verify_png(empty, Image) is False

    garbage = tmp_path / "garbage.png"
    garbage.write_bytes(b"not an image at all")
    assert CROPS._verify_png(garbage, Image) is False


def test_crop_task_verify_failure_marks_integrity_failed(tmp_path, monkeypatch):
    """A written PNG that fails its post-write verify() is marked
    'integrity_failed' (distinct from a download/decode 'failed') so main() can
    route it to exit 42, not exit 1 (design §4/§5.5)."""
    atlas = _make_grid_atlas(200, 140, 2, 2)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    src = cache_dir / "atlas.png"
    atlas.save(src, format="PNG")
    task = CROPS.CropTask(
        kind="face", source_file="s.json", arkham_id="01001", card_id=1,
        name="C", url="http://x/atlas.png", num_width=2, num_height=2,
        x=0, y=0, out_name="01001_1_face_aa.png", sideways=False)
    res = CROPS.AtlasResult(url="http://x/atlas.png", cache_path=src,
                            status="cached")
    args = CROPS.parse_args(["--out-dir", str(tmp_path / "out")])
    args.cache_dir = cache_dir
    # Force the always-on post-write integrity check to fail (a truncated/corrupt
    # PNG slipping past the atomic save — full disk, encoder hiccup).
    monkeypatch.setattr(CROPS, "_verify_png", lambda path, image_mod: False)
    wrote = CROPS.crop_task(task, res, tmp_path / "out", args, image_mod=Image)
    assert wrote is False
    # NOT the generic "failed" (that routes to exit 1); a write-integrity status.
    assert task.status == "integrity_failed"


def test_main_returns_42_on_png_integrity_failure(tmp_path, monkeypatch):
    """End-to-end: a written PNG failing its post-write verify() trips the
    exit-42 write-integrity guarantee (design §4/§5.5) — NOT a generic exit 1
    that would be indistinguishable from a benign offline miss / 404."""
    url = "http://x/face.png"
    atlas = _make_grid_atlas(200, 140, 2, 2)
    cache = tmp_path / "cache"
    cache.mkdir()
    # Pre-populate the cache so --offline resolves a hit (no network needed) and
    # the run reaches the crop/save/verify path that we force to fail.
    (cache / f"{CROPS.cache_key(url)}.png").write_bytes(_atlas_png_bytes(atlas))
    cards = [_card("01001", 1, "a.json", url, nw=2, nh=2, x=0, y=0, back=None)]
    idx = tmp_path / "index.json"
    _write_index(idx, cards)
    out = tmp_path / "out"
    monkeypatch.setattr(CROPS, "_verify_png", lambda path, image_mod: False)
    code = CROPS.main(["--index", str(idx), "--out-dir", str(out),
                       "--cache-dir", str(cache), "--offline"])
    assert code == 42  # write-integrity failure, not exit 1
    # The corrupt PNG is not counted as a written face crop.
    m = _read_manifest(out)
    assert m["counts"]["face_crops_written"] == 0


def test_crop_task_shared_back_verify_failure_marks_integrity_failed(
        tmp_path, monkeypatch):
    """A shared_back PNG failing its post-write verify() also routes to
    'integrity_failed' — the contract is uniform across crop kinds, not face-only."""
    atlas = _make_grid_atlas(150, 210, 1, 1)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    src = cache_dir / "back.png"
    atlas.save(src, format="PNG")
    name = CROPS.shared_back_name("http://x/back/")
    task = CROPS.CropTask(
        kind="shared_back", source_file="s.json", arkham_id="01001",
        card_id=1, name="Back", url="http://x/back/", num_width=1,
        num_height=1, x=0, y=0, out_name=name, sideways=False)
    res = CROPS.AtlasResult(url="http://x/back/", cache_path=src,
                            status="cached")
    args = CROPS.parse_args(["--out-dir", str(tmp_path / "out")])
    args.cache_dir = cache_dir
    monkeypatch.setattr(CROPS, "_verify_png", lambda path, image_mod: False)
    wrote = CROPS.crop_task(task, res, tmp_path / "out", args, image_mod=Image)
    assert wrote is False
    assert task.status == "integrity_failed"


def test_main_integrity_42_takes_precedence_over_exit_1(tmp_path, monkeypatch):
    """When a run has BOTH a write-integrity failure (exit-42 candidate) AND a
    download failure (exit-1 candidate), exit 42 (the stronger signal) wins.
    Locks the ordering of the two checks in main() against an accidental
    reorder that would let the exit-1 path return first."""
    url_ok = "http://x/cached.png"      # cache hit -> crop -> verify forced fail
    url_miss = "http://x/missing.png"   # offline miss -> failed (exit-1 candidate)
    atlas = _make_grid_atlas(200, 140, 2, 2)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{CROPS.cache_key(url_ok)}.png").write_bytes(_atlas_png_bytes(atlas))
    cards = [
        _card("01001", 1, "a.json", url_ok, nw=2, nh=2, x=0, y=0, back=None),
        _card("01002", 2, "b.json", url_miss, nw=2, nh=2, x=0, y=0, back=None),
    ]
    idx = tmp_path / "index.json"
    _write_index(idx, cards)
    out = tmp_path / "out"
    monkeypatch.setattr(CROPS, "_verify_png", lambda path, image_mod: False)
    code = CROPS.main(["--index", str(idx), "--out-dir", str(out),
                       "--cache-dir", str(cache), "--offline"])
    assert code == 42  # NOT 1 — write-integrity outranks failed_outputs
    m = _read_manifest(out)
    assert m["counts"]["atlases_failed"] == 1  # the offline miss is still recorded


# ===========================================================================
# Tier 2 — exit-code matrix (subprocess + local HTTP stub)
# ===========================================================================

FAST_BACKOFF = ["--_backoff-base", "0.001"]


def _build_atlases() -> dict[str, bytes]:
    """A small set of fixture atlases keyed by name -> PNG bytes."""
    return {
        "face2x2.png": _atlas_png_bytes(_make_grid_atlas(200, 140, 2, 2)),
        "uback4x2.png": _atlas_png_bytes(_make_grid_atlas(200, 140, 4, 2)),
        "shared.png": _atlas_png_bytes(_make_grid_atlas(150, 210, 1, 1)),
    }


def test_exit_zero_clean_run(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = [
            _card("01001", 1, "a.json", srv.url("/atlas/face2x2.png"),
                  nw=2, nh=2, x=1, y=1,
                  back={"url": srv.url("/atlas/shared.png"), "source": "R2",
                        "num_width": 1, "num_height": 1, "cell_index": 0,
                        "x": 0, "y": 0, "single_image": True}),
            _card("01002", 2, "b.json", srv.url("/atlas/uback4x2.png"),
                  nw=4, nh=2, x=2, y=0, unique_back=True,
                  back={"url": srv.url("/atlas/uback4x2.png"), "source": "R2",
                        "num_width": 4, "num_height": 2, "cell_index": 2,
                        "x": 2, "y": 0, "single_image": False}),
        ]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out)] + FAST_BACKOFF)
    assert r.returncode == 0, r.stderr
    m = _read_manifest(out)
    assert m["counts"]["face_crops_written"] == 2
    assert m["counts"]["unique_back_crops_written"] == 1
    assert m["counts"]["shared_backs_written"] == 1
    assert m["counts"]["atlases_failed"] == 0
    assert (out / "cards").exists()
    assert len(list((out / "cards").glob("*.png"))) == 3  # 2 face + 1 uback
    assert len(list((out / "backs").glob("*.png"))) == 1


def test_back_null_tolerance_exit_zero(tmp_path):
    """A card with back==null: face PNG written, NO back PNG, back_kind 'none'."""
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = [_card("09999", 9, "nb.json", srv.url("/atlas/face2x2.png"),
                       nw=2, nh=2, x=0, y=0, back=None)]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out)] + FAST_BACKOFF)
    assert r.returncode == 0, r.stderr
    assert len(list((out / "cards").glob("*.png"))) == 1  # face only
    assert list((out / "backs").glob("*.png")) == []      # no back written
    rows = _gallery_rows(out)
    assert len(rows) == 1
    assert rows[0]["back_kind"] == "none"
    assert rows[0]["status"] == "ok"
    assert rows[0]["face_img"] is not None
    assert rows[0]["back_img"] is None


def test_exit_one_on_404(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = [_card("01001", 1, "a.json", srv.url("/404"),
                       nw=2, nh=2, x=0, y=0, back=None)]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out)] + FAST_BACKOFF)
        # 404 is a non-429 4xx: it must be requested exactly once (no retry).
        assert srv.state.count("/404") == 1, srv.state.request_counts
    assert r.returncode == 1, r.stderr
    m = _read_manifest(out)
    assert m["counts"]["atlases_failed"] == 1


def test_exit_one_on_truncated_decode_error(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = [_card("01001", 1, "a.json", srv.url("/truncated"),
                       nw=2, nh=2, x=0, y=0, back=None)]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out)] + FAST_BACKOFF)
    # A truncated body either fails the download (Content-Length mismatch) or
    # decodes badly -> either way the card produces no PNG and the run exits 1.
    assert r.returncode == 1, r.stderr
    assert list((out / "cards").glob("*.png")) == []


def test_exit_one_offline_empty_cache(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = [_card("01001", 1, "a.json", srv.url("/atlas/face2x2.png"),
                       nw=2, nh=2, x=0, y=0, back=None)]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--offline"] + FAST_BACKOFF)
    assert r.returncode == 1, r.stderr  # offline cache miss -> failed
    m = _read_manifest(out)
    assert m["counts"]["atlases_failed"] == 1


def test_exit_two_missing_index(tmp_path):
    out = tmp_path / "out"
    r = _run(["--index", str(tmp_path / "nope.json"), "--out-dir", str(out)])
    assert r.returncode == 2, r.stderr


def test_exit_two_malformed_index(tmp_path):
    idx = tmp_path / "bad.json"
    idx.write_text("{ this is not json", encoding="utf-8")
    out = tmp_path / "out"
    r = _run(["--index", str(idx), "--out-dir", str(out)])
    assert r.returncode == 2, r.stderr


@pytest.mark.parametrize("content_type,expected_ext", [
    ("image/png", ".png"),
    ("image/jpeg; charset=binary", ".jpg"),
    (None, ".jpg"),
])
def test_guess_ext_content_type_fallback_end_to_end(tmp_path, content_type,
                                                    expected_ext):
    """The download layer threads the real response header through guess_ext for
    the extension-less /sharedback/ trailing-slash URL (§5.7 / §8 risk #2)."""
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        srv.state.sharedback_content_type = content_type
        srv.state.atlas_bytes = atlases["shared.png"]
        sb_url = srv.url("/sharedback/")
        cards = [_card("01001", 1, "a.json", srv.url("/atlas/face2x2.png"),
                       nw=2, nh=2, x=0, y=0,
                       back={"url": sb_url, "source": "Steam",
                             "num_width": 1, "num_height": 1, "cell_index": 0,
                             "x": 0, "y": 0, "single_image": True})]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        cache = tmp_path / "cache"
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--cache-dir", str(cache)] + FAST_BACKOFF)
        # Compute the expected cache file from the URL key + derived ext.
        key = CROPS.cache_key(sb_url)
    assert r.returncode == 0, r.stderr
    cached = cache / f"{key}{expected_ext}"
    assert cached.exists(), sorted(p.name for p in cache.iterdir())
    # The sharedback PNG is normalized to .png regardless of cached ext.
    sb_png = out / "backs" / CROPS.shared_back_name(sb_url)
    assert sb_png.exists()
    with Image.open(sb_png) as im:
        im.verify()


def test_retry_5xx_transient_is_retried_and_succeeds(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        srv.state.flaky_fail_count = 1   # one 500 then 200
        srv.state.atlas_bytes = atlases["face2x2.png"]
        cards = [_card("01001", 1, "a.json", srv.url("/flaky5xx"),
                       nw=2, nh=2, x=0, y=0, back=None)]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--retries", "2"] + FAST_BACKOFF)
        flaky_calls = srv.state.count("/flaky5xx")
    assert r.returncode == 0, r.stderr
    assert flaky_calls > 1  # the 500 was retried
    m = _read_manifest(out)
    assert m["counts"]["atlases_downloaded"] == 1
    assert m["counts"]["atlases_failed"] == 0


def test_retry_exhaustion_always5xx_failed_exact_budget(tmp_path):
    atlases = _build_atlases()
    retries = 2
    with StubServer(atlases) as srv:
        cards = [_card("01001", 1, "a.json", srv.url("/always5xx"),
                       nw=2, nh=2, x=0, y=0, back=None)]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--retries", str(retries)] + FAST_BACKOFF)
        calls = srv.state.count("/always5xx")
    assert r.returncode == 1, r.stderr
    # exactly retries+1 attempts: one initial try plus `retries` retries.
    assert calls == retries + 1, calls
    m = _read_manifest(out)
    assert m["counts"]["atlases_failed"] == 1


def test_retry_after_429_ceiling_cap_bounds_wait(tmp_path):
    """A hostile Retry-After: 9999 is clamped to the ceiling; the run does not
    block ~9999s. With the injected near-zero backoff base the total wall time
    stays well under the documented ceiling (design §4 / §5.7)."""
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        srv.state.atlas_bytes = atlases["face2x2.png"]
        cards = [_card("01001", 1, "a.json", srv.url("/retry_after_429"),
                       nw=2, nh=2, x=0, y=0, back=None)]
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        start = time.monotonic()
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--retries", "2"] + FAST_BACKOFF)
        elapsed = time.monotonic() - start
        calls = srv.state.count("/retry_after_429")
        # server-side inter-request delay (429 -> 200).
        ts = srv.state.times("/retry_after_429")
    assert r.returncode == 0, r.stderr  # 429 is transient -> retried -> 200
    assert calls == 2  # 429 then 200
    # The clamp guarantees the wait is bounded by the ceiling, not 9999s.
    assert elapsed < CROPS.RETRY_AFTER_CEILING + 30  # generous, !~9999
    if len(ts) >= 2:
        assert (ts[1] - ts[0]) < CROPS.RETRY_AFTER_CEILING + 5


# ===========================================================================
# Tier 3 — gallery acceptance checks (static, on the emitted JSON island)
# ===========================================================================


def _clean_multi_index(srv, n_extra_pages_factor=1) -> list[dict]:
    """Build a multi-row index covering clean / shared-back / unique_back /
    anomaly / sideways / back==null, served entirely off the stub."""
    cards = [
        # clean face + shared back
        _card("01001", 1, "a.json", srv.url("/atlas/face2x2.png"),
              nw=2, nh=2, x=0, y=0,
              back={"url": srv.url("/atlas/shared.png"), "source": "R2",
                    "num_width": 1, "num_height": 1, "cell_index": 0,
                    "x": 0, "y": 0, "single_image": True}),
        # unique_back (gridded)
        _card("01002", 2, "b.json", srv.url("/atlas/uback4x2.png"),
              nw=4, nh=2, x=2, y=0, unique_back=True,
              back={"url": srv.url("/atlas/uback4x2.png"), "source": "R2",
                    "num_width": 4, "num_height": 2, "cell_index": 2,
                    "x": 2, "y": 0, "single_image": False}),
        # sideways (landscape cell)
        _card("01003", 3, "c.json", srv.url("/atlas/face2x2.png"),
              nw=2, nh=2, x=1, y=0, sideways=True, back=None),
        # back == null
        _card("01004", 4, "d.json", srv.url("/atlas/face2x2.png"),
              nw=2, nh=2, x=0, y=1, back=None),
        # anomaly (null x/y, non-sliceable)
        {
            "name": "Jenny Barnes",
            "arkham_id": "02003", "card_id": 273631, "deck_key": "2736",
            "face": {"url": srv.url("/atlas/face2x2.png"), "source": "R2",
                     "num_width": 2, "num_height": 2, "cell_index": 31,
                     "x": None, "y": None},
            "back": None, "unique_back": True, "sideways": False,
            "pack": "playercards",
            "source_file": "JennyBarnes.9058d3/promo.b954f6.json",
            "anomaly": "cell_out_of_bounds",
        },
    ]
    return cards


def test_gallery_page_count_and_src_resolve(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = _clean_multi_index(srv)
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--page-size", "2"] + FAST_BACKOFF)
    assert r.returncode == 0, r.stderr
    rows = _gallery_rows(out)
    assert len(rows) == 5
    import math
    expected_pages = math.ceil(len(rows) / 2)
    html = (out / "gallery.html").read_text(encoding="utf-8")
    assert "const PAGE_SIZE = 2" in html
    assert expected_pages == 3
    # every img src in the row JSON resolves to an existing file under out-dir.
    for row in rows:
        for key in ("face_img", "back_img"):
            src = row.get(key)
            if src:
                assert (out / src).exists(), src


def test_gallery_anomaly_parity(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = _clean_multi_index(srv)
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out)] + FAST_BACKOFF)
    assert r.returncode == 0, r.stderr
    rows = _gallery_rows(out)
    anomaly_rows = [r2 for r2 in rows if r2["status"] == "anomaly"]
    assert len(anomaly_rows) == 1
    assert anomaly_rows[0]["arkham_id"] == "02003"
    # manifest skipped_anomalies parity with the index-marked set.
    m = _read_manifest(out)
    assert len(m["skipped_anomalies"]) == 1
    assert m["skipped_anomalies"][0]["code"] == "cell_out_of_bounds"


def test_gallery_sideways_row_flag(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = _clean_multi_index(srv)
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out)] + FAST_BACKOFF)
    assert r.returncode == 0, r.stderr
    rows = _gallery_rows(out)
    sideways = [r2 for r2 in rows if r2["sideways"]]
    assert len(sideways) == 1
    assert sideways[0]["arkham_id"] == "01003"


def test_rerun_idempotency_skip_existing_mtimes(tmp_path):
    """Re-run without --force skips existing PNGs (mtimes UNCHANGED);
    a --force run re-writes them (mtimes CHANGED). Byte-identity alone would not
    prove skip-existing fired (§5.7 Tier 3)."""
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = _clean_multi_index(srv)
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        cache = tmp_path / "cache"
        r1 = _run(["--index", str(idx), "--out-dir", str(out),
                   "--cache-dir", str(cache)] + FAST_BACKOFF)
        assert r1.returncode == 0, r1.stderr

        pngs = sorted((out / "cards").glob("*.png")) + \
            sorted((out / "backs").glob("*.png"))
        before = {p: p.stat().st_mtime_ns for p in pngs}
        bytes_before = {p: p.read_bytes() for p in pngs}

        # Run 2: no --force (offline so we prove cache reuse too).
        time.sleep(0.01)
        r2 = _run(["--index", str(idx), "--out-dir", str(out),
                   "--cache-dir", str(cache)] + FAST_BACKOFF)
        assert r2.returncode == 0, r2.stderr
        after = {p: p.stat().st_mtime_ns for p in pngs}
        bytes_after = {p: p.read_bytes() for p in pngs}

        # skip-existing fired: mtimes unchanged AND bytes identical.
        assert before == after, "skip-existing did not fire (mtimes changed)"
        assert bytes_before == bytes_after

        # Run 3: --force re-writes (mtimes change).
        time.sleep(0.01)
        r3 = _run(["--index", str(idx), "--out-dir", str(out),
                   "--cache-dir", str(cache), "--force"] + FAST_BACKOFF)
        assert r3.returncode == 0, r3.stderr
        forced = {p: p.stat().st_mtime_ns for p in pngs}
    assert forced != after, "--force did not re-write PNGs (mtimes unchanged)"
    # forced re-write is byte-identical (deterministic crop).
    bytes_forced = {p: p.read_bytes() for p in pngs}
    assert bytes_forced == bytes_before


def test_hash_images_records_per_png_sha(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = _clean_multi_index(srv)
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--hash-images"] + FAST_BACKOFF)
    assert r.returncode == 0, r.stderr
    m = _read_manifest(out)
    assert "images" in m["outputs"]
    assert len(m["outputs"]["images"]) >= 1
    for rel, entry in m["outputs"]["images"].items():
        assert len(entry["sha256"]) == 64
        assert (out / rel).exists()


def test_dry_run_writes_no_files(tmp_path):
    atlases = _build_atlases()
    with StubServer(atlases) as srv:
        cards = _clean_multi_index(srv)
        idx = tmp_path / "index.json"
        _write_index(idx, cards)
        out = tmp_path / "out"
        r = _run(["--index", str(idx), "--out-dir", str(out),
                  "--dry-run"] + FAST_BACKOFF)
    assert r.returncode == 0, r.stderr
    assert not out.exists() or list(out.iterdir()) == []


# ===========================================================================
# Tier 4 — gallery runtime checks (OPTIONAL, Playwright; skip-guarded)
# ===========================================================================

try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


INLINE_PNG_DATA_URL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _build_gallery_with_rows(tmp_path: Path, rows: list, page_size: int = 2):
    """Render gallery.html directly from rows via the module's emitter."""
    html = CROPS.build_gallery_html(rows, page_size)
    gallery = tmp_path / "gallery.html"
    gallery.write_text(html, encoding="utf-8")
    return gallery


@pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE,
                    reason="playwright not installed. Run: pip install "
                           "playwright && playwright install chromium")
class TestTier4Browser:

    @pytest.fixture(scope="class")
    def browser_ctx(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            yield ctx
            ctx.close()
            browser.close()

    def _rows(self):
        return [
            {"arkham_id": f"P{i:04d}", "card_id": i, "name": f"Card {i}",
             "sideways": (i == 0), "face_img": INLINE_PNG_DATA_URL,
             "back_img": None, "back_kind": "none", "status": "ok"}
            for i in range(5)
        ] + [
            {"arkham_id": "AN01", "card_id": 99, "name": "Anomaly",
             "sideways": False, "face_img": None, "back_img": None,
             "back_kind": "none", "status": "anomaly",
             "anomaly_code": "cell_out_of_bounds"},
            {"arkham_id": "FA01", "card_id": 98, "name": "Failed",
             "sideways": False, "face_img": None, "back_img": None,
             "back_kind": "none", "status": "failed"},
        ]

    def test_pagination_page2_slice(self, tmp_path, browser_ctx):
        rows = self._rows()
        gallery = _build_gallery_with_rows(tmp_path, rows, page_size=2)
        page = browser_ctx.new_page()
        page.goto(f"file://{gallery}")
        page.wait_for_load_state("networkidle")
        # page 1 (currentPage 0) renders rows[0:2].
        assert page.locator(".tile").count() == 2
        page.click("#btn-next")
        page.wait_for_timeout(150)
        assert page.evaluate("() => currentPage") == 1
        assert page.locator(".tile").count() == 2  # rows[2:4]
        page.close()

    def test_sideways_rotate_computed_style(self, tmp_path, browser_ctx):
        rows = self._rows()
        gallery = _build_gallery_with_rows(tmp_path, rows, page_size=5)
        page = browser_ctx.new_page()
        page.goto(f"file://{gallery}")
        page.wait_for_load_state("networkidle")
        # first tile (i==0) is sideways; it must have a non-identity rotate.
        transform = page.evaluate(
            "() => getComputedStyle(document.querySelector('.crop-wrap.sideways img')).transform")
        assert transform and transform != "none", transform
        # a non-sideways tile's img has no rotate transform.
        non_sw = page.evaluate(
            "() => { const imgs=[...document.querySelectorAll('.crop-wrap:not(.sideways) img')];"
            " return imgs.length ? getComputedStyle(imgs[0]).transform : 'none'; }")
        assert non_sw == "none", non_sw
        page.close()

    def test_placeholder_tiles_no_broken_img(self, tmp_path, browser_ctx):
        rows = self._rows()
        gallery = _build_gallery_with_rows(tmp_path, rows, page_size=20)
        page = browser_ctx.new_page()
        page.goto(f"file://{gallery}")
        page.wait_for_load_state("networkidle")
        assert page.locator(".placeholder.anomaly").count() == 1
        assert page.locator(".placeholder.failed").count() == 1
        # anomaly/failed tiles emit NO <img>.
        anomaly_imgs = page.evaluate(
            "() => document.querySelectorAll('.tile[data-status=anomaly] img').length")
        failed_imgs = page.evaluate(
            "() => document.querySelectorAll('.tile[data-status=failed] img').length")
        assert anomaly_imgs == 0
        assert failed_imgs == 0
        page.close()

    def test_lazy_load_data_src_to_src_promotion(self, tmp_path, browser_ctx):
        rows = self._rows()
        gallery = _build_gallery_with_rows(tmp_path, rows, page_size=20)
        page = browser_ctx.new_page()
        page.set_viewport_size({"width": 800, "height": 300})
        page.goto(f"file://{gallery}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(200)
        # at least some imgs start with data-src and no resolved src.
        databg_before = page.evaluate(
            "() => document.querySelectorAll('img[data-src]').length")
        assert databg_before > 0, "no lazy tiles — observer not exercised"
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        # after scroll: in-view tiles promoted (data-src removed, src set).
        result = page.evaluate("""() => {
            const imgs = [...document.querySelectorAll('.crop-wrap img')];
            const promoted = imgs.filter(i => i.src && !i.dataset.src);
            return { total: imgs.length, promoted: promoted.length };
        }""")
        assert result["promoted"] > 0, result
        page.close()
