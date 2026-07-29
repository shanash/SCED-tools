#!/usr/bin/env python3
"""Build an HTML gallery comparing UPSTREAM (Steam-hosted) vs LOCAL (R2-hosted)
card images for the cards that conflicted during the 2026-06-27 rebase.

For each conflicted card it renders the exact card cell (CSS sprite crop from the
remote sheet, no download) of the FaceURL and BackURL on both sides, so the user
can visually confirm whether our R2 images match / improve on upstream's Steam ones.

The cell is derived the TTS way: cell = CardID % 100, on the CustomDeck's
NumWidth x NumHeight grid (col = cell % NumWidth, row = cell // NumWidth).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path("/Volumes/PRO-G40/Projects/SCED/SCED-downloads")
DIR = "decomposed/language-pack/Korean - Player Cards/Korean-PlayerCards.KoreanI"
OLD_MERGE_BASE = "9ce1a1d5c47429020f2a73274d103e0acf8639cf"   # merge-base(upstream, pre-rebase korean)
LOCAL_REF = "korean-rebase-backup-20260627"                   # our pre-rebase tip (== current korean content)
UPSTREAM_REF = "upstream/main"
OUT_DIR = REPO.parent / "SCED-tools/scripts/output/upstream-vs-local-compare"

DW, DH = 168, 235  # display cell size (portrait ~5:7)


def git_bytes(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True).stdout


def changed_set(a: str, b: str) -> set[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "-z", f"{a}..{b}"],
        cwd=REPO, capture_output=True,
    ).stdout
    return {p for p in out.decode("utf-8").split("\0") if p}


def modified_both(ref_a: str, ref_b: str) -> set[str]:
    """Files present in BOTH refs whose content differs (status M).

    This is the real 'upstream vs ours diverge' set — it includes cards upstream
    never touched since the old merge-base but that we localized (e.g. Lola), which
    the merge-base intersection misses.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=M", ref_a, ref_b],
        cwd=REPO, capture_output=True,
    ).stdout
    return {p for p in out.decode("utf-8").split("\0") if p}


def show_json(ref: str, path: str) -> dict | None:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO, capture_output=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def crop_params(card: dict) -> dict | None:
    """Return face/back URLs + grid + cell position for the card, TTS-style."""
    try:
        cid = int(card["CardID"])
        cd = card["CustomDeck"]
    except (KeyError, TypeError, ValueError):
        return None
    deck = str(cid // 100)
    if deck not in cd:
        deck = next(iter(cd), None)
        if deck is None:
            return None
    e = cd[deck]
    nw = int(e.get("NumWidth", 1) or 1)
    nh = int(e.get("NumHeight", 1) or 1)
    cell = cid % 100
    return {
        "face": e.get("FaceURL", ""),
        "back": e.get("BackURL", ""),
        "nw": nw, "nh": nh,
        "col": cell % nw, "row": cell // nw,
        "unique_back": bool(e.get("UniqueBack", False)),
    }


def host(url: str) -> str:
    if "steamusercontent" in url:
        return "steam"
    if "r2.dev" in url:
        return "r2"
    return "other" if url else "none"


def crop_div(url: str, nw: int, nh: int, col: int, row: int) -> str:
    """A div that shows just one cell of the remote sheet via background crop."""
    if not url:
        return '<div class="cell empty">(none)</div>'
    style = (
        f"width:{DW}px;height:{DH}px;"
        f"background-image:url('{url}');background-repeat:no-repeat;"
        f"background-size:{nw * DW}px {nh * DH}px;"
        f"background-position:-{col * DW}px -{row * DH}px;"
    )
    return f'<div class="cell" style="{style}" title="{url}"></div>'


def main() -> int:
    # The rebase CONFLICT set: KoreanI files BOTH sides changed since the old
    # merge-base (this is exactly the "충돌났던 카드들" analysed at resolution time).
    cands = sorted(
        p for p in (changed_set(OLD_MERGE_BASE, LOCAL_REF) & changed_set(OLD_MERGE_BASE, UPSTREAM_REF))
        if p.startswith(DIR + "/") and not p.endswith("KoreanI.json")
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    n_face_diff = n_back_diff = 0
    kept = []
    for path in cands:
        ours = show_json(LOCAL_REF, path)
        theirs = show_json(UPSTREAM_REF, path)
        if not ours or not theirs:
            continue
        po = crop_params(ours)
        pt = crop_params(theirs)
        if not po or not pt:
            continue
        # only show cards whose FACE or BACK image actually differs
        if po["face"] == pt["face"] and po["back"] == pt["back"]:
            continue
        kept.append(path)
        name = ours.get("Nickname") or Path(path).stem
        gm = ours.get("GMNotes", "")
        aid = ""
        try:
            aid = json.loads(gm).get("id", "")
        except (json.JSONDecodeError, AttributeError):
            pass

        face_diff = host(po["face"]) != host(pt["face"]) or po["face"] != pt["face"]
        back_diff = host(po["back"]) != host(pt["back"]) or po["back"] != pt["back"]
        n_face_diff += bool(face_diff)
        n_back_diff += bool(back_diff)

        # back crop: if UniqueBack, slice on the same grid/cell as the face; else whole image
        def back_cell(p):
            if p["unique_back"]:
                return p["nw"], p["nh"], p["col"], p["row"]
            return 1, 1, 0, 0

        ub_nw, ub_nh, ub_col, ub_row = back_cell(pt)
        ob_nw, ob_nh, ob_col, ob_row = back_cell(po)

        rows.append(f"""
        <div class="card">
          <div class="hd"><b>{name}</b> <span class="id">{aid}</span>
            <span class="badge {'diff' if face_diff else 'same'}">face: {host(pt['face'])}→{host(po['face'])}</span>
            <span class="badge {'diff' if back_diff else 'same'}">back: {host(pt['back'])}→{host(po['back'])}</span>
          </div>
          <div class="pairs">
            <div class="pair"><div class="lbl">FACE upstream (Steam)</div>{crop_div(pt['face'], pt['nw'], pt['nh'], pt['col'], pt['row'])}</div>
            <div class="pair"><div class="lbl">FACE local (R2)</div>{crop_div(po['face'], po['nw'], po['nh'], po['col'], po['row'])}</div>
            <div class="pair"><div class="lbl">BACK upstream (Steam)</div>{crop_div(pt['back'], ub_nw, ub_nh, ub_col, ub_row)}</div>
            <div class="pair"><div class="lbl">BACK local (R2)</div>{crop_div(po['back'], ob_nw, ob_nh, ob_col, ob_row)}</div>
          </div>
        </div>""")

    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>Upstream(Steam) vs Local(R2) — conflicted cards</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#1e1f23;color:#e8e8ea;margin:0;padding:16px}}
 h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#9aa0a6;font-size:13px;margin-bottom:14px;line-height:1.5}}
 .controls{{margin:10px 0 18px}} button{{background:#2d2f36;color:#e8e8ea;border:1px solid #44464d;border-radius:6px;padding:6px 12px;cursor:pointer;margin-right:8px}}
 button.active{{background:#3b6ea5;border-color:#3b6ea5}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(720px,1fr));gap:14px}}
 .card{{background:#26282e;border:1px solid #383a41;border-radius:10px;padding:10px}}
 .hd{{font-size:14px;margin-bottom:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
 .id{{color:#9aa0a6;font-size:12px}}
 .badge{{font-size:11px;padding:2px 7px;border-radius:10px}}
 .badge.same{{background:#243b24;color:#8fd18f}} .badge.diff{{background:#3b2a24;color:#e0a679}}
 .pairs{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
 .pair{{display:flex;flex-direction:column;align-items:center;gap:4px}}
 .lbl{{font-size:11px;color:#9aa0a6;text-align:center;height:28px}}
 .cell{{border:1px solid #44464d;border-radius:4px;background-color:#15161a}}
 .cell.empty{{width:{DW}px;height:{DH}px;display:flex;align-items:center;justify-content:center;color:#666;font-size:12px}}
</style></head><body>
<h1>충돌 카드 이미지 비교 — upstream(Steam) vs local(R2)</h1>
<div class="sub">
 rebase 시 충돌났던 <b>{len(rows)}</b>개 카드. 각 카드의 실제 셀만 잘라 표시(원격 시트를 CSS로 크롭, 다운로드 없음).<br>
 FaceURL 차이: <b>{n_face_diff}</b>개 · BackURL 차이: <b>{n_back_diff}</b>개. 좌=upstream(Steam, 채택 안 함), 우=local(R2, 채택함).<br>
 <span style="color:#e0a679">주황 badge</span>=양쪽 다름, <span style="color:#8fd18f">초록</span>=동일. 이미지가 많아 로딩에 시간이 걸릴 수 있습니다.
</div>
<div class="controls">
 <button class="active" onclick="flt(this,'all')">전체</button>
 <button onclick="flt(this,'diff')">차이 있는 것만</button>
</div>
<div class="grid" id="grid">{''.join(rows)}</div>
<script>
 function flt(btn,mode){{
   document.querySelectorAll('.controls button').forEach(b=>b.classList.remove('active'));
   btn.classList.add('active');
   document.querySelectorAll('.card').forEach(c=>{{
     const hasDiff = c.querySelector('.badge.diff')!==null;
     c.style.display = (mode==='all'||hasDiff)?'':'none';
   }});
 }}
</script>
</body></html>"""

    (OUT_DIR / "candidates.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")
    out = OUT_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"cards: {len(rows)}  | face-diff: {n_face_diff}  back-diff: {n_back_diff}")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
