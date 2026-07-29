#!/usr/bin/env python3
"""Auto-identify extracted PDF crops by perceptual-hash matching against known
reference card faces (assisted-manual mapping aid, decision Q3).

References are built from existing langpack objects: each in-scope card's
CustomDeck FaceURL (sheet) is downloaded once and the card's own cell is sliced
out (cell = CardID % 100 on a NumWidth x NumHeight grid). The 6 parallel
investigators are sourced from the Russian langpack (same art, different text).

Matching uses pHash (DCT) + dHash over BOTH the full card and its top art band
(robust to title/body-text language differences); the minimum Hamming distance
across hash kinds and regions is the score. Output is a ranked identification
table (Markdown + JSON) — a human confirms it into mapping.json.

No writes outside --out-dir. Network: read-only GET of reference sheets.
Exit: 0 ok · 2 input error · 3 network/decode error
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
INV_IDS = ("90088", "90089", "90090", "90091", "90092", "90093")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def gray_resize(im, size):
    return im.convert("L").resize((size, size))


def dhash(im, hs=8):
    from PIL import Image
    g = im.convert("L").resize((hs + 1, hs))
    px = list(g.getdata())
    bits = 0
    for r in range(hs):
        for c in range(hs):
            bits = (bits << 1) | (1 if px[r * (hs + 1) + c] < px[r * (hs + 1) + c + 1] else 0)
    return bits


def phash(im, hs=8):
    # DCT-II on a 32x32 grayscale, keep top-left hs x hs (excl DC), median threshold
    import math
    N = 32
    g = im.convert("L").resize((N, N))
    px = list(g.getdata())
    f = [[px[y * N + x] for x in range(N)] for y in range(N)]
    # separable DCT
    def dct1d(vec):
        n = len(vec)
        return [sum(vec[x] * math.cos(math.pi * (x + 0.5) * u / n) for x in range(n)) for u in range(n)]
    rows = [dct1d(r) for r in f]
    cols = [dct1d([rows[y][x] for y in range(N)]) for x in range(N)]
    # cols[x][u] -> dct[u][x]
    vals = []
    for u in range(hs):
        for v in range(hs):
            if u == 0 and v == 0:
                continue
            vals.append(cols[v][u])
    med = sorted(vals)[len(vals) // 2]
    bits = 0
    for val in vals:
        bits = (bits << 1) | (1 if val > med else 0)
    return bits


def ham(a, b):
    return bin(a ^ b).count("1")


def sigs(im):
    """Hash signatures for full image + top art band (top 48%)."""
    from PIL import Image
    w, h = im.size
    top = im.crop((0, 0, w, int(h * 0.48)))
    return {"d_full": dhash(im), "p_full": phash(im),
            "d_top": dhash(top), "p_top": phash(top)}


def best_dist(s1, s2):
    return min(ham(s1["d_full"], s2["d_full"]), ham(s1["p_full"], s2["p_full"]),
               ham(s1["d_top"], s2["d_top"]), ham(s1["p_top"], s2["p_top"]))


def parse_id(gm):
    if not isinstance(gm, str):
        return None
    m = re.search(r'"id"\s*:\s*"(90\d{3}[^"]*)"', gm)
    return m.group(1) if m else None


def build_refs(kor_dir: Path, rus_dir: Path, cache: Path):
    from PIL import Image
    cache.mkdir(parents=True, exist_ok=True)
    refs = []  # (arkham_id, nickname, source, PIL.Image face)
    sheet_cache = {}

    def get_sheet(url):
        if url in sheet_cache:
            return sheet_cache[url]
        h = re.sub(r"\W", "_", url)[-60:]
        p = cache / f"sheet_{h}.bin"
        if not p.exists():
            p.write_bytes(fetch(url))
        import io
        img = Image.open(io.BytesIO(p.read_bytes())).convert("RGB")
        sheet_cache[url] = img
        return img

    def add_from(d, only_inv=False):
        for f in sorted(d.glob("*.json")):
            try:
                dd = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            aid = parse_id(dd.get("GMNotes", ""))
            if not aid:
                continue
            if only_inv and aid not in INV_IDS:
                continue
            if not only_inv and aid in INV_IDS:
                continue  # investigators come from Russian only
            cd = dd.get("CustomDeck", {})
            k = next(iter(cd), None)
            if not k:
                continue
            v = cd[k]
            url = v.get("FaceURL", "")
            if not url:
                continue
            nw, nh = int(v.get("NumWidth", 1) or 1), int(v.get("NumHeight", 1) or 1)
            cell = int(dd.get("CardID", 0)) % 100
            try:
                sheet = get_sheet(url)
            except Exception as e:
                print(f"  [warn] fetch fail {aid}: {e}", file=sys.stderr)
                continue
            W, H = sheet.size
            cw, ch = W // nw, H // nh
            col, row = cell % nw, cell // nw
            face = sheet.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            refs.append((aid, dd.get("Nickname", ""), "inv-ru" if only_inv else "kor", face))
    add_from(kor_dir, only_inv=False)
    add_from(rus_dir, only_inv=True)
    return refs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crops-dir", type=Path, required=True)
    ap.add_argument("--kor-dir", type=Path, required=True)
    ap.add_argument("--rus-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        from PIL import Image
    except ImportError:
        print("InputError: Pillow required", file=sys.stderr)
        return 2

    cache = args.out_dir / "_ref_cache"
    print("Building reference faces (download + slice)...")
    refs = build_refs(args.kor_dir, args.rus_dir, cache)
    print(f"  built {len(refs)} reference faces "
          f"({sum(1 for r in refs if r[2]=='kor')} kor + {sum(1 for r in refs if r[2]=='inv-ru')} inv-ru)")
    ref_sigs = [(aid, nick, src, sigs(face)) for aid, nick, src, face in refs]

    crops = sorted(args.crops_dir.glob("p*.png"), key=lambda p: int(p.stem[1:4]))
    print(f"Matching {len(crops)} crops against {len(ref_sigs)} refs...")
    table = []
    for cp in crops:
        cs = sigs(Image.open(cp).convert("RGB"))
        scored = sorted(((best_dist(cs, rs), aid, nick, src) for aid, nick, src, rs in ref_sigs))
        d0, aid0, nick0, src0 = scored[0]
        d1 = scored[1][0] if len(scored) > 1 else 99
        table.append({"crop": cp.name, "page": int(cp.stem[1:4]),
                      "best_id": aid0, "best_nick": nick0, "best_src": src0,
                      "dist": d0, "margin": d1 - d0,
                      "runner_up": f"{scored[1][1]}({scored[1][0]})" if len(scored) > 1 else ""})
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "crop_identification.json").write_text(
        json.dumps({"refs": len(ref_sigs), "crops": len(crops), "table": table},
                   indent=2, ensure_ascii=False), encoding="utf-8")

    # confidence buckets
    strong = [t for t in table if t["dist"] <= 10 and t["margin"] >= 4]
    maybe = [t for t in table if t not in strong and t["dist"] <= 16]
    weak = [t for t in table if t not in strong and t not in maybe]
    # one strong per id (dedupe to most-confident crop per arkham_id)
    by_id = {}
    for t in sorted(strong, key=lambda x: x["dist"]):
        by_id.setdefault(t["best_id"], t)
    lines = ["# Crop auto-identification (perceptual-hash vs existing faces)\n",
             f"refs={len(ref_sigs)} crops={len(crops)}  |  "
             f"strong={len(strong)} maybe={len(maybe)} weak/out-of-scope={len(weak)}\n",
             f"\n## Strong matches deduped to one crop per arkham_id ({len(by_id)}/38 targets)\n",
             "| arkham_id | nick | src | crop(page) | dist | margin |",
             "|---|---|---|---|---|---|"]
    for aid, t in sorted(by_id.items()):
        lines.append(f"| {aid} | {t['best_nick'][:18]} | {t['best_src']} | {t['crop']} | {t['dist']} | {t['margin']} |")
    lines.append(f"\n## All 151 crops ranked\n")
    lines.append("| crop | best_id | nick | src | dist | margin | runner_up |")
    lines.append("|---|---|---|---|---|---|---|")
    for t in table:
        lines.append(f"| {t['crop']} | {t['best_id']} | {t['best_nick'][:16]} | {t['best_src']} | "
                     f"{t['dist']} | {t['margin']} | {t['runner_up']} |")
    (args.out_dir / "crop_identification.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nStrong matches: {len(strong)} crops -> {len(by_id)} distinct target ids")
    print(f"Targets matched: {sorted(by_id)}")
    missing = [i for i in ([f"{r[0]}" for r in refs]) if i not in by_id]
    print(f"Targets NOT strongly matched ({len(set(missing))}): {sorted(set(missing))}")
    print(f"Wrote {args.out_dir/'crop_identification.md'} and .json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
