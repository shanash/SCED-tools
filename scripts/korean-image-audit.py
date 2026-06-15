#!/usr/bin/env python3
"""
Korean Player Card Image Audit Tool

Walks the SCED-downloads Korean Player Cards decomposed JSON tree and produces
a catalog (XLSX + CSV) listing every card's identity, image URL, and auto-detected
flags, plus empty user-markup columns (Status / Issue / Note).

Workflow:
  1. Run this script to produce korean_playercards_catalog.xlsx
  2. Open in Excel, filter by Flag_* columns or spot-check FaceURL previews
  3. Mark Status=Fix, Issue=<category>, Note=<detail> on problem rows
  4. Save marked file under a new name (e.g., korean_playercards_catalog_MARKED.xlsx)
     before re-running — re-runs overwrite the original output path
  5. Future reduce step reads marked file, filters Status==Fix, groups by CustomDeck_Key,
     feeds into image-path-replacer.py (column contract: CardID, GUID, FaceURL, Status)

Re-run after SCED-downloads main updates:
  Run again to get a fresh catalog; merge prior user markings by CardID+GUID composite key.

Usage:
  python3 korean-image-audit.py [--source <path>] [--output <path>]
          [--format both|csv|xlsx] [--by-sheet] [--html] [--check-urls] [--include-campaigns]
"""

import argparse
import csv
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCE = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Player Cards"
    / "Korean-PlayerCards.KoreanI"
)
DEFAULT_CAMPAIGN_SOURCE = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Campaigns"
    / "Korean-Campaigns.KoreanC"
)
DEFAULT_CAMPAIGN_COMBINED = (
    REPO_ROOT
    / "SCED-downloads"
    / "downloadable"
    / "korean_campaigns.json"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "output" / "korean-image-audit"
# Campaigns mode writes to a sub-directory of this task's own output dir
# (avoids hardcoding another task's output path; override with --output).
DEFAULT_OUTPUT_CAMPAIGNS = DEFAULT_OUTPUT / "with-campaigns"

STEAM_HOST = "steamusercontent-a.akamaihd.net"

STATUS_OPTIONS = ["", "OK", "Fix", "Skip", "Recheck"]
ISSUE_OPTIONS = ["", "Wrong Image", "Low Quality", "Outdated", "Duplicate", "Other"]

COLUMNS = [
    "CardID", "GUID", "Nickname", "Name", "ArkhamID", "Pack",
    "SourceFile", "ParentDir", "CustomDeck_Key",
    "FaceURL", "BackURL", "NumWidth", "NumHeight", "BackIsHidden",
    "Preview_URL", "Duplicate_Sheet_Count",
    "Flag_Meta", "Flag_Host", "Flag_Shared_Sheet", "Flag_Back_Nonstandard", "Flag_URL_Status",
    "Status", "Issue", "Note",
]

# XLSX column widths (approximate)
COL_WIDTHS = {
    "CardID": 10, "GUID": 10, "Nickname": 28, "Name": 8, "ArkhamID": 12, "Pack": 12,
    "SourceFile": 40, "ParentDir": 22, "CustomDeck_Key": 16,
    "FaceURL": 80, "BackURL": 80, "NumWidth": 10, "NumHeight": 10, "BackIsHidden": 12,
    "Preview_URL": 10, "Duplicate_Sheet_Count": 20,
    "Flag_Meta": 10, "Flag_Host": 10, "Flag_Shared_Sheet": 16, "Flag_Back_Nonstandard": 20,
    "Flag_URL_Status": 14,
    "Status": 12, "Issue": 20, "Note": 40,
}

FLAG_COLS = {"Flag_Meta", "Flag_Host", "Flag_Shared_Sheet", "Flag_Back_Nonstandard", "Flag_URL_Status"}
MARKUP_COLS = {"Status", "Issue", "Note"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate Korean card image audit catalog from SCED-downloads decomposed data."
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE,
        help="Root directory to walk (default: Korean-PlayerCards.KoreanI under SCED-downloads)",
    )
    parser.add_argument(
        "--source-campaigns", type=Path, default=DEFAULT_CAMPAIGN_SOURCE,
        help="Campaigns decomposed root (default: Korean-Campaigns.KoreanC under SCED-downloads)",
    )
    parser.add_argument(
        "--campaigns-from-combined", action="store_true",
        help="Fallback: read campaigns from downloadable/korean_campaigns.json "
             "instead of decomposed tree. Requires --include-campaigns.",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output directory (created if missing). "
             "Defaults to output/korean-image-audit; switches to output/korean-image-apply "
             "automatically when --include-campaigns is set.",
    )
    parser.add_argument(
        "--format", choices=["both", "csv", "xlsx"], default="both",
        help="Output format (default: both)",
    )
    parser.add_argument(
        "--by-sheet", action="store_true",
        help="Also emit korean_playercards_by_sheet.csv grouping cards by CustomDeck_Key+FaceURL",
    )
    parser.add_argument(
        "--check-urls", action="store_true",
        help="HEAD-request every unique FaceURL to detect 404s (slow; populates Flag_URL_Status)",
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Generate HTML gallery (korean_playercards_gallery.html) for visual image review",
    )
    parser.add_argument(
        "--include-campaigns", action="store_true",
        help="Include Korean - Campaigns data in the audit (switches output prefix to "
             "korean_images_*).",
    )
    return parser.parse_args()


def iter_card_jsons(root: Path):
    """Yield all .json files under root recursively, sorted for determinism."""
    for path in sorted(root.rglob("*.json")):
        yield path


def parse_arkham_id(gm_notes_str: str) -> str:
    if not gm_notes_str:
        return ""
    try:
        data = json.loads(gm_notes_str)
        val = data.get("id", "")
        return str(val) if val is not None else ""
    except (json.JSONDecodeError, TypeError, AttributeError):
        return ""


def resolve_arkham_id(card_json: dict, card_path: Path, root: Path) -> str:
    """Resolve arkham_id for a card JSON.

    Player Cards embed GMNotes inline as a JSON string; Campaigns use
    GMNotes_path pointing to a sibling .gmnotes file. This helper normalizes
    both layouts and returns "" on miss.
    """
    inline = card_json.get("GMNotes", "")
    if inline:
        ark = parse_arkham_id(inline)
        if ark:
            return ark

    gm_path = card_json.get("GMNotes_path")
    if gm_path:
        # GMNotes_path is relative to the decomposed tree root (parents[1]
        # of the decomposed/language-pack/X subdir — i.e. the decomposed pack
        # root like Korean-Campaigns.KoreanC). We resolve by trying the
        # sibling file first, then the given path anchored at root.parent.
        candidates = [
            card_path.parent / Path(gm_path).name,
            root.parent / gm_path,
            root / Path(gm_path).relative_to(root.name)
            if str(gm_path).startswith(root.name + "/") else None,
        ]
        for cand in candidates:
            if cand is None:
                continue
            try:
                if cand.exists():
                    with cand.open(encoding="utf-8") as fh:
                        text = fh.read()
                    return parse_arkham_id(text)
            except OSError:
                continue
    return ""


def host_flag(face_url: str) -> str:
    """Return 'Y' if FaceURL host is not the standard Steam host (or unparseable)."""
    if not face_url:
        return ""
    try:
        host = urlparse(face_url).hostname or ""
    except ValueError as exc:
        logging.debug("FaceURL parse failed %s: %s", face_url, exc)
        return "Y"
    return "Y" if host != STEAM_HOST else ""


def extract_rows(path: Path, root: Path, pack: str = "playercards") -> list[dict]:
    """Parse one JSON file and return a list of row dicts (one per CustomDeck key).
    Returns [] if the file is not a card object.
    Returns a single error row (with Flag_Meta=Y) on parse failure.
    """
    source_rel = str(path.relative_to(root))
    parent_dir = path.parent.name if path.parent != root else "."

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        logging.warning("Parse error %s: %s", path, exc)
        return [{col: "" for col in COLUMNS} | {
            "SourceFile": source_rel,
            "ParentDir": parent_dir,
            "Pack": pack,
            "Flag_Meta": "Y",
        }]

    if not isinstance(data, dict) or "CustomDeck" not in data:
        return []

    custom_deck: dict = data.get("CustomDeck", {})
    if not custom_deck:
        return []

    card_id = data.get("CardID", "")
    guid = data.get("GUID", "")
    nickname = data.get("Nickname", "")
    name = data.get("Name", "")
    arkham_id = resolve_arkham_id(data, path, root)

    has_meta_issue = not card_id or not guid or not nickname

    rows = []
    for deck_key, deck_info in custom_deck.items():
        face_url = deck_info.get("FaceURL", "")
        back_url = deck_info.get("BackURL", "")

        row = {
            "CardID": card_id,
            "GUID": guid,
            "Nickname": nickname,
            "Name": name,
            "ArkhamID": arkham_id,
            "Pack": pack,
            "SourceFile": source_rel,
            "ParentDir": parent_dir,
            "CustomDeck_Key": deck_key,
            "FaceURL": face_url,
            "BackURL": back_url,
            "NumWidth": deck_info.get("NumWidth", ""),
            "NumHeight": deck_info.get("NumHeight", ""),
            "BackIsHidden": deck_info.get("BackIsHidden", ""),
            "Preview_URL": face_url,
            # computed later
            "Duplicate_Sheet_Count": 0,
            "Flag_Meta": "Y" if has_meta_issue else "",
            "Flag_Host": "",
            "Flag_Shared_Sheet": "",
            "Flag_Back_Nonstandard": "",
            "Flag_URL_Status": "",
            "Status": "",
            "Issue": "",
            "Note": "",
        }

        # Immediate flags derivable per-row
        row["Flag_Host"] = host_flag(face_url)

        rows.append(row)

    return rows


def extract_rows_from_combined(combined_path: Path, pack: str = "campaigns") -> list[dict]:
    """Fallback parser: walk a combined JSON (downloadable/korean_campaigns.json)
    and yield rows in the same shape as extract_rows().
    """
    try:
        with combined_path.open(encoding="utf-8") as fh:
            root_obj = json.load(fh)
    except (json.JSONDecodeError, IOError) as exc:
        logging.warning("Parse error %s: %s", combined_path, exc)
        return []

    rows: list[dict] = []
    stack = [root_obj]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue

        if "CustomDeck" in node and isinstance(node["CustomDeck"], dict):
            card_id = node.get("CardID", "")
            guid = node.get("GUID", "")
            nickname = node.get("Nickname", "")
            name = node.get("Name", "")
            # In combined JSON, GMNotes is inlined as a string.
            arkham_id = parse_arkham_id(node.get("GMNotes", ""))
            has_meta_issue = not card_id or not guid or not nickname

            for deck_key, deck_info in node["CustomDeck"].items():
                face_url = deck_info.get("FaceURL", "")
                back_url = deck_info.get("BackURL", "")
                row = {
                    "CardID": card_id,
                    "GUID": guid,
                    "Nickname": nickname,
                    "Name": name,
                    "ArkhamID": arkham_id,
                    "Pack": pack,
                    "SourceFile": f"<combined:{combined_path.name}>",
                    "ParentDir": "<combined>",
                    "CustomDeck_Key": deck_key,
                    "FaceURL": face_url,
                    "BackURL": back_url,
                    "NumWidth": deck_info.get("NumWidth", ""),
                    "NumHeight": deck_info.get("NumHeight", ""),
                    "BackIsHidden": deck_info.get("BackIsHidden", ""),
                    "Preview_URL": face_url,
                    "Duplicate_Sheet_Count": 0,
                    "Flag_Meta": "Y" if has_meta_issue else "",
                    "Flag_Host": "",
                    "Flag_Shared_Sheet": "",
                    "Flag_Back_Nonstandard": "",
                    "Flag_URL_Status": "",
                    "Status": "",
                    "Issue": "",
                    "Note": "",
                }
                row["Flag_Host"] = host_flag(face_url)
                rows.append(row)

        for v in node.values():
            if isinstance(v, (dict, list)):
                stack.append(v)

    return rows


def compute_duplicate_counts(rows: list[dict]) -> Counter:
    """Count how many rows share the same FaceURL."""
    return Counter(r["FaceURL"] for r in rows if r.get("FaceURL"))


def compute_mode_back(rows: list[dict]) -> str:
    """Return the most common BackURL across all cards (the "standard" back)."""
    backs = [r["BackURL"] for r in rows if r.get("BackURL")]
    return Counter(backs).most_common(1)[0][0] if backs else ""


def apply_flags(rows: list[dict], dup_counts: Counter, mode_back: str) -> None:
    for row in rows:
        face_url = row.get("FaceURL", "")
        count = dup_counts.get(face_url, 1)
        row["Duplicate_Sheet_Count"] = count
        if count >= 2:
            row["Flag_Shared_Sheet"] = "Y"
        if mode_back and row.get("BackURL", "") != mode_back:
            row["Flag_Back_Nonstandard"] = "Y"


def annotate_url_status(rows: list[dict]) -> None:
    """HEAD-request every unique FaceURL and store HTTP status in Flag_URL_Status."""
    try:
        import requests
        from concurrent.futures import ThreadPoolExecutor, as_completed
    except ImportError:
        print("WARNING: 'requests' not installed; skipping --check-urls. Run: pip install requests")
        return

    unique_urls = {r["FaceURL"] for r in rows if r.get("FaceURL")}
    print(f"Checking {len(unique_urls)} unique FaceURLs (this may take several minutes)...")

    url_status: dict[str, str] = {}

    def check(url: str) -> tuple[str, str]:
        if urlparse(url).scheme not in ("http", "https"):
            return url, "SKIP_SCHEME"
        for attempt in range(2):
            try:
                # allow_redirects=False: report 3xx status as-is and avoid
                # following redirects to internal hosts (SSRF hardening).
                resp = requests.head(url, timeout=10, allow_redirects=False)
                return url, str(resp.status_code)
            except Exception as exc:
                if attempt == 1:
                    logging.debug("URL check failed %s: %s", url, exc)
                    return url, "ERR"
        return url, "ERR"

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(check, url): url for url in unique_urls}
        done = 0
        for future in as_completed(futures):
            url, status = future.result()
            url_status[url] = status
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(unique_urls)} checked...")

    for row in rows:
        row["Flag_URL_Status"] = url_status.get(row["FaceURL"], "")


def sort_key(row: dict):
    ark = str(row.get("ArkhamID", ""))
    try:
        card_id = int(row.get("CardID"))
    except (TypeError, ValueError):
        card_id = 999999999
    guid = str(row.get("GUID", ""))
    # Empty ArkhamID sorts last
    ark_sort = (1, ark) if not ark else (0, ark)
    return (ark_sort, card_id, guid)


def sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=sort_key)


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig = UTF-8 with BOM so Excel opens Korean text correctly
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV written: {out_path}")


def write_xlsx(rows: list[dict], out_path: Path) -> None:
    if not HAS_OPENPYXL:
        print("WARNING: openpyxl not installed; skipping XLSX. Run: pip install openpyxl")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cards"

    # --- Header row ---
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    flag_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    markup_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

    for col_idx, col_name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = Font(bold=True, color="FFFFFF")
        if col_name in FLAG_COLS:
            cell.fill = flag_fill
            cell.font = Font(bold=True, color="000000")
        elif col_name in MARKUP_COLS:
            cell.fill = markup_fill
            cell.font = Font(bold=True, color="000000")
        else:
            cell.fill = header_fill

    # --- Data validation dropdowns on Status and Issue ---
    status_formula = '"' + ",".join(o for o in STATUS_OPTIONS if o) + '"'
    issue_formula = '"' + ",".join(o for o in ISSUE_OPTIONS if o) + '"'

    status_col_idx = COLUMNS.index("Status") + 1
    issue_col_idx = COLUMNS.index("Issue") + 1

    status_letter = get_column_letter(status_col_idx)
    issue_letter = get_column_letter(issue_col_idx)

    dv_status = DataValidation(
        type="list", formula1=status_formula, allow_blank=True,
        showDropDown=False,
        error="Choose from: " + ", ".join(o for o in STATUS_OPTIONS if o),
        errorTitle="Invalid Status",
        showErrorMessage=True,
    )
    dv_issue = DataValidation(
        type="list", formula1=issue_formula, allow_blank=True,
        showDropDown=False,
        error="Choose from: " + ", ".join(o for o in ISSUE_OPTIONS if o),
        errorTitle="Invalid Issue",
        showErrorMessage=True,
    )
    ws.add_data_validation(dv_status)
    ws.add_data_validation(dv_issue)
    n_rows = len(rows)
    dv_status.add(f"{status_letter}2:{status_letter}{n_rows + 1}")
    dv_issue.add(f"{issue_letter}2:{issue_letter}{n_rows + 1}")

    # --- Data rows ---
    face_col_idx = COLUMNS.index("FaceURL") + 1
    preview_col_idx = COLUMNS.index("Preview_URL") + 1

    for row_idx, row in enumerate(rows, start=2):
        for col_idx, col_name in enumerate(COLUMNS, start=1):
            value = row.get(col_name, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=value)

            if col_name == "Preview_URL" and value:
                cell.value = "open"
                cell.hyperlink = value
                cell.font = Font(color="0563C1", underline="single")

            if col_name in FLAG_COLS and value == "Y":
                cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
                cell.font = Font(bold=True)

    # --- Column widths ---
    for col_idx, col_name in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = COL_WIDTHS.get(col_name, 15)

    # --- Freeze header row + autofilter ---
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # --- Metadata sheet ---
    ws_meta = wb.create_sheet("Metadata")
    ws_meta["A1"] = "Generated"
    ws_meta["B1"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ws_meta["A2"] = "Total cards"
    ws_meta["B2"] = len(rows)
    ws_meta["A3"] = "Column contract (future reduce step)"
    ws_meta["B3"] = "CardID, GUID, FaceURL, Status == Fix"
    ws_meta["A4"] = "Re-run merge key"
    ws_meta["B4"] = "CardID + GUID composite"
    ws_meta["A5"] = "Status options"
    ws_meta["B5"] = ", ".join(o for o in STATUS_OPTIONS if o)
    ws_meta["A6"] = "Issue options"
    ws_meta["B6"] = ", ".join(o for o in ISSUE_OPTIONS if o)

    wb.save(out_path)
    print(f"XLSX written: {out_path}")


def write_by_sheet_csv(rows: list[dict], out_path: Path) -> None:
    """Group cards by (CustomDeck_Key, FaceURL) and emit a summary CSV."""
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["CustomDeck_Key"], row["FaceURL"])
        groups.setdefault(key, []).append(row)

    sheet_rows = []
    for (dk, furl), cards in sorted(groups.items()):
        sheet_rows.append({
            "CustomDeck_Key": dk,
            "FaceURL": furl,
            "Card_Count": len(cards),
            "Sample_Nicknames": " | ".join(c["Nickname"] for c in cards[:5]),
            "CardIDs": " | ".join(str(c["CardID"]) for c in cards[:10]),
            "ArkhamIDs": " | ".join(c["ArkhamID"] for c in cards[:10]),
            "Note": "",
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_rows.sort(key=lambda r: -r["Card_Count"])
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["CustomDeck_Key", "FaceURL", "Card_Count",
                                                "Sample_Nicknames", "CardIDs", "ArkhamIDs", "Note"])
        writer.writeheader()
        writer.writerows(sheet_rows)
    print(f"By-sheet CSV written: {out_path}")


def write_html_gallery(rows: list[dict], out_path: Path) -> None:
    """Generate a self-contained HTML gallery grouped by unique sheet (FaceURL).
    Each tile shows the sprite sheet image, card count, card names, and a Fix checkbox.
    Checked items can be exported as JSON via the Export button.
    """
    # Group cards by (CustomDeck_Key, FaceURL), sorted by card count desc
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["CustomDeck_Key"], row["FaceURL"])
        groups.setdefault(key, []).append(row)

    sheets = sorted(groups.items(), key=lambda x: -len(x[1]))

    # Build JS data: array of {key, faceUrl, cardCount, cards:[{cardId,guid,arkhamId,nickname}]}
    js_sheets = []
    for (dk, furl), cards in sheets:
        js_sheets.append({
            "key": dk,
            "faceUrl": furl,
            "cardCount": len(cards),
            "cards": [
                {"cardId": c["CardID"], "guid": c["GUID"],
                 "arkhamId": c["ArkhamID"], "nickname": c["Nickname"]}
                for c in cards
            ],
        })

    js_data = json.dumps(js_sheets, ensure_ascii=False)
    now = datetime.now(timezone.utc)
    generated = now.strftime("%Y-%m-%d %H:%M UTC")
    # Stable machine timestamp used for the localStorage key so a new gallery
    # run starts a fresh state bucket and doesn't inherit stale check marks.
    generation_timestamp = now.strftime("%Y%m%dT%H%M%SZ")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Korean Card Image Audit — {generated}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, sans-serif; background: #1a1a2e; color: #eee; }}

  #toolbar {{
    position: sticky; top: 0; z-index: 100;
    background: #16213e; border-bottom: 2px solid #0f3460;
    padding: 10px 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
  }}
  #toolbar h1 {{ font-size: 15px; color: #e94560; white-space: nowrap; }}
  #search {{
    flex: 1; min-width: 180px; padding: 6px 10px;
    border: 1px solid #0f3460; border-radius: 6px;
    background: #0f3460; color: #eee; font-size: 14px;
  }}
  #stats {{ font-size: 13px; color: #aaa; white-space: nowrap; }}
  button {{
    padding: 6px 14px; border: none; border-radius: 6px;
    cursor: pointer; font-size: 13px; font-weight: bold;
  }}
  #btn-export {{ background: #e94560; color: #fff; }}
  #btn-clear  {{ background: #444; color: #eee; }}
  #btn-export:hover {{ background: #c73652; }}
  #btn-clear:hover  {{ background: #555; }}

  #gallery {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 16px; padding: 16px;
  }}

  .tile {{
    background: #16213e; border: 2px solid #0f3460; border-radius: 10px;
    overflow: hidden; display: flex; flex-direction: column;
    transition: border-color .15s;
  }}
  .tile.checked {{ border-color: #e94560; background: #2a1020; }}
  .tile.hidden  {{ display: none; }}

  .img-wrap {{
    position: relative; background: #0a0a1a; cursor: pointer;
    min-height: 120px; display: flex; align-items: center; justify-content: center;
  }}
  .img-wrap img {{
    width: 100%; height: 180px; object-fit: cover; object-position: top left;
    display: block;
  }}
  .img-wrap .badge {{
    position: absolute; top: 6px; right: 6px;
    background: rgba(0,0,0,.75); color: #fff;
    font-size: 11px; font-weight: bold; padding: 2px 7px; border-radius: 10px;
  }}
  .img-wrap .open-link {{
    position: absolute; bottom: 6px; right: 6px;
    background: rgba(0,0,0,.6); color: #88ccff;
    font-size: 11px; padding: 2px 7px; border-radius: 10px; text-decoration: none;
  }}
  .img-wrap .open-link:hover {{ background: rgba(0,0,0,.9); }}

  .tile-body {{ padding: 10px; flex: 1; }}
  .deck-key {{ font-size: 11px; color: #888; margin-bottom: 4px; }}
  .card-list {{ font-size: 12px; color: #ccc; line-height: 1.6; }}
  .card-list .more {{ color: #888; font-style: italic; }}

  .tile-footer {{
    padding: 8px 10px; border-top: 1px solid #0f3460;
    display: flex; align-items: center; gap: 8px;
  }}
  .tile-footer label {{
    display: flex; align-items: center; gap: 6px;
    cursor: pointer; font-size: 13px; font-weight: bold; color: #e94560;
    user-select: none;
  }}
  .tile-footer input[type=checkbox] {{ width: 16px; height: 16px; accent-color: #e94560; cursor: pointer; }}
  .tile-footer .note-input {{
    flex: 1; padding: 4px 8px; font-size: 12px;
    border: 1px solid #0f3460; border-radius: 4px;
    background: #0f3460; color: #eee;
  }}

  #pagination {{
    display: flex; align-items: center; justify-content: center; gap: 12px;
    padding: 16px; font-size: 14px; border-top: 1px solid #0f3460;
  }}
  #pagination button {{ background: #0f3460; color: #eee; padding: 6px 16px; border-radius: 6px; }}
  #pagination button:disabled {{ opacity: 0.4; cursor: default; }}
  #page-info {{ color: #aaa; min-width: 110px; text-align: center; }}

  #export-modal, #import-modal {{
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.8);
    z-index: 200; align-items: center; justify-content: center;
  }}
  #export-modal.open, #import-modal.open {{ display: flex; }}
  .modal-box {{
    background: #16213e; border: 2px solid #e94560; border-radius: 12px;
    padding: 20px; max-width: 700px; width: 90%; max-height: 80vh;
    display: flex; flex-direction: column; gap: 12px;
  }}
  .modal-box h2 {{ color: #e94560; font-size: 16px; }}
  .modal-box textarea {{
    flex: 1; min-height: 300px; background: #0a0a1a; color: #eee;
    border: 1px solid #0f3460; border-radius: 6px; padding: 10px;
    font-family: monospace; font-size: 12px; resize: vertical;
  }}
  .modal-actions {{ display: flex; gap: 8px; }}
  #btn-copy {{ background: #0f3460; color: #eee; }}
  #btn-close {{ background: #333; color: #eee; }}
</style>
</head>
<body>

<div id="toolbar">
  <h1>🃏 Korean Image Audit</h1>
  <input id="search" type="search" placeholder="카드 이름 또는 ArkhamID 검색..." oninput="filterGallery()">
  <span id="stats"></span>
  <button id="btn-export" onclick="openExport()">Export Fix List</button>
  <button id="btn-import" onclick="openImport()">Import from JSON</button>
  <button id="btn-clear" onclick="clearAll()">전체 해제</button>
</div>

<div id="gallery"></div>

<div id="pagination">
  <button id="btn-prev" onclick="goPage(-1)">◀ 이전</button>
  <span id="page-info"></span>
  <button id="btn-next" onclick="goPage(1)">다음 ▶</button>
</div>

<div id="export-modal">
  <div class="modal-box">
    <h2>Fix List (JSON)</h2>
    <textarea id="export-text" readonly></textarea>
    <div class="modal-actions">
      <button id="btn-copy" onclick="copyExport()">📋 복사</button>
      <button id="btn-close" onclick="closeExport()">닫기</button>
    </div>
  </div>
</div>

<div id="import-modal">
  <div class="modal-box">
    <h2>Import from JSON</h2>
    <p style="font-size: 12px; color: #aaa;">
      이전 Export JSON을 아래에 붙여넣은 뒤 "가져오기"를 누르세요. 체크 상태와 메모가 복원됩니다.
    </p>
    <textarea id="import-text" placeholder='[{{"customDeckKey": "...", "faceUrl": "...", "note": "...", "cards": [...]}}]'></textarea>
    <div class="modal-actions">
      <button id="btn-import-apply" onclick="applyImport()" style="background: #e94560; color: #fff;">가져오기</button>
      <button id="btn-import-close" onclick="closeImport()" style="background: #333; color: #eee;">취소</button>
    </div>
  </div>
</div>

<script>
const SHEETS = {js_data};
const GENERATION_TIMESTAMP = "{generation_timestamp}";
const STATE_KEY = "korean_image_audit_v1_state:" + GENERATION_TIMESTAMP;
const PAGE_SIZE = 25;

// (key+"|"+faceUrl) → globalIdx  (for Import-from-JSON matching)
const SHEET_LOOKUP = (() => {{
  const m = new Map();
  SHEETS.forEach((s, i) => m.set(s.key + "|" + s.faceUrl, i));
  return m;
}})();

// Full state persisted in localStorage — spans all pages.
let galleryState = {{ checks: {{}}, notes: {{}} }};

function saveFullState() {{
  try {{ localStorage.setItem(STATE_KEY, JSON.stringify(galleryState)); }}
  catch (e) {{ console.warn("saveFullState failed:", e); }}
}}

function loadFullState() {{
  try {{
    const raw = localStorage.getItem(STATE_KEY);
    if (raw) galleryState = JSON.parse(raw);
  }} catch (e) {{ console.warn("loadFullState failed:", e); }}
}}

const gallery = document.getElementById('gallery');
let tiles = [];          // page-local DOM elements (for iteration)
let tileByIdx = new Map(); // globalIdx → DOM element
let currentPage = 0;
const totalPages = Math.ceil(SHEETS.length / PAGE_SIZE);
let searchFilter = '';

// IntersectionObserver: set img.src only when tile scrolls into view
const imgObserver = new IntersectionObserver((entries) => {{
  entries.forEach(entry => {{
    if (!entry.isIntersecting) return;
    const img = entry.target;
    if (img.dataset.src) {{
      img.src = img.dataset.src;
      delete img.dataset.src;
    }}
    imgObserver.unobserve(img);
  }});
}}, {{ rootMargin: '300px' }});

function escHtml(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}}

function safeUrl(u) {{
  u = String(u);
  var lo = u.toLowerCase();
  return (lo.indexOf('http://') === 0 || lo.indexOf('https://') === 0) ? u : '';
}}

function buildPage(page) {{
  // Disconnect lingering observers from previous page before clearing DOM
  tiles.forEach(t => {{
    const img = t.querySelector('img[data-src]');
    if (img) imgObserver.unobserve(img);
  }});
  gallery.innerHTML = '';
  tiles = [];
  tileByIdx.clear();
  const start = page * PAGE_SIZE;
  const end = Math.min(start + PAGE_SIZE, SHEETS.length);

  for (let idx = start; idx < end; idx++) {{
    const sheet = SHEETS[idx];
    const tile = document.createElement('div');
    tile.className = 'tile';
    tile.dataset.idx = idx;
    tile.dataset.search = [
      sheet.cards.map(c => c.nickname).join(' '),
      sheet.cards.map(c => c.arkhamId).join(' '),
      sheet.key
    ].join(' ').toLowerCase();

    if (searchFilter && !tile.dataset.search.includes(searchFilter))
      tile.classList.add('hidden');

    const namesToShow = sheet.cards.slice(0, 8).map(c => c.nickname || c.arkhamId || c.cardId);
    const extra = sheet.cardCount - namesToShow.length;
    const namesHtml = namesToShow.map(n =>
      `<span>${{escHtml(n)}}</span>`
    ).join('<br>') + (extra > 0 ? `<br><span class="more">+${{extra}}장 더...</span>` : '');

    tile.innerHTML = `
      <div class="img-wrap" onclick="toggleTile(${{idx}})">
        <img data-src="${{escHtml(safeUrl(sheet.faceUrl))}}" src="" alt="sheet ${{escHtml(sheet.key)}}"
             onerror="this.style.display='none';this.parentNode.style.minHeight='80px'">
        <span class="badge">${{sheet.cardCount}}장</span>
        <a class="open-link" href="${{escHtml(safeUrl(sheet.faceUrl)) || '#'}}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">원본 ↗</a>
      </div>
      <div class="tile-body">
        <div class="deck-key">Key: ${{escHtml(sheet.key)}}</div>
        <div class="card-list">${{namesHtml}}</div>
      </div>
      <div class="tile-footer">
        <label>
          <input type="checkbox" id="chk-${{idx}}" onchange="onCheck(${{idx}})">
          Fix
        </label>
        <input class="note-input" type="text" placeholder="메모..." id="note-${{idx}}"
               oninput="onNote(${{idx}}, this.value)">
      </div>
    `;
    gallery.appendChild(tile);
    tiles.push(tile);
    tileByIdx.set(idx, tile);

    // Register image for deferred loading
    const img = tile.querySelector('img[data-src]');
    if (img) imgObserver.observe(img);

    // Restore persisted state for this tile
    if (galleryState.checks[idx]) {{
      tile.classList.add('checked');
      const chk = document.getElementById('chk-' + idx);
      if (chk) chk.checked = true;
    }}
    if (galleryState.notes[idx]) {{
      const note = document.getElementById('note-' + idx);
      if (note) note.value = galleryState.notes[idx];
    }}
  }}

  currentPage = page;
  updatePagination();
  updateStats();
}}

function updatePagination() {{
  document.getElementById('page-info').textContent =
    (currentPage + 1) + ' / ' + totalPages + ' 페이지';
  document.getElementById('btn-prev').disabled = currentPage === 0;
  document.getElementById('btn-next').disabled = currentPage === totalPages - 1;
}}

function goPage(delta) {{
  const np = currentPage + delta;
  if (np < 0 || np >= totalPages) return;
  buildPage(np);
  window.scrollTo(0, 0);
}}

function toggleTile(idx) {{
  const chk = document.getElementById('chk-' + idx);
  if (!chk) return;
  chk.checked = !chk.checked;
  onCheck(idx);
}}

function onCheck(idx) {{
  const chk = document.getElementById('chk-' + idx);
  const checked = chk && chk.checked;
  const tile = tileByIdx.get(idx);
  if (tile) tile.classList.toggle('checked', checked);
  if (checked) galleryState.checks[idx] = true;
  else delete galleryState.checks[idx];
  saveFullState();
  updateStats();
}}

function onNote(idx, val) {{
  if (val) galleryState.notes[idx] = val;
  else delete galleryState.notes[idx];
  saveFullState();
}}

function updateStats() {{
  const visible = tiles.filter(t => !t.classList.contains('hidden')).length;
  const totalChecked = Object.keys(galleryState.checks).length;
  document.getElementById('stats').textContent =
    visible + ' / {len(sheets)} 시트  |  Fix: ' + totalChecked + '개 (전체 페이지)';
}}

function filterGallery() {{
  searchFilter = document.getElementById('search').value.toLowerCase().trim();
  tiles.forEach(tile => {{
    const match = !searchFilter || tile.dataset.search.includes(searchFilter);
    tile.classList.toggle('hidden', !match);
  }});
  updateStats();
}}

function clearAll() {{
  galleryState = {{ checks: {{}}, notes: {{}} }};
  saveFullState();
  tiles.forEach(t => {{
    const idx = +t.dataset.idx;
    const chk = document.getElementById('chk-' + idx);
    if (chk) chk.checked = false;
    t.classList.remove('checked');
    const note = document.getElementById('note-' + idx);
    if (note) note.value = '';
  }});
  updateStats();
}}

function openImport() {{
  document.getElementById('import-text').value = '';
  document.getElementById('import-modal').classList.add('open');
}}

function closeImport() {{
  document.getElementById('import-modal').classList.remove('open');
}}

function applyImport() {{
  const raw = document.getElementById('import-text').value.trim();
  if (!raw) {{ closeImport(); return; }}
  let parsed;
  try {{ parsed = JSON.parse(raw); }}
  catch (e) {{ alert("JSON 파싱 실패: " + e.message); return; }}
  if (!Array.isArray(parsed)) {{ alert("JSON 루트는 배열이어야 합니다."); return; }}
  let matched = 0, missed = 0;
  parsed.forEach(entry => {{
    const key = (entry.customDeckKey || '') + "|" + (entry.faceUrl || '');
    const idx = SHEET_LOOKUP.get(key);
    if (idx === undefined) {{ missed++; return; }}
    galleryState.checks[idx] = true;
    if (entry.note) galleryState.notes[idx] = entry.note;
    matched++;
  }});
  saveFullState();
  buildPage(currentPage);
  closeImport();
  alert('가져오기 완료: 매칭 ' + matched + '건, 미매칭 ' + missed + '건');
}}

// Export reads from galleryState so all pages' checked items are included.
function openExport() {{
  const result = [];
  Object.keys(galleryState.checks).map(Number).sort((a, b) => a - b).forEach(idx => {{
    if (!galleryState.checks[idx]) return;
    result.push({{
      customDeckKey: SHEETS[idx].key,
      faceUrl: SHEETS[idx].faceUrl,
      cardCount: SHEETS[idx].cardCount,
      note: galleryState.notes[idx] || '',
      cards: SHEETS[idx].cards,
    }});
  }});
  document.getElementById('export-text').value = JSON.stringify(result, null, 2);
  document.getElementById('export-modal').classList.add('open');
}}

function closeExport() {{
  document.getElementById('export-modal').classList.remove('open');
}}

function copyExport() {{
  const ta = document.getElementById('export-text');
  ta.select();
  document.execCommand('copy');
  document.getElementById('btn-copy').textContent = '✓ 복사됨';
  setTimeout(() => document.getElementById('btn-copy').textContent = '📋 복사', 1500);
}}

document.getElementById('export-modal').addEventListener('click', e => {{
  if (e.target === e.currentTarget) closeExport();
}});
document.getElementById('import-modal').addEventListener('click', e => {{
  if (e.target === e.currentTarget) closeImport();
}});

loadFullState();
buildPage(0);
</script>
</body>
</html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"HTML gallery written: {out_path}")


def print_summary(rows: list[dict]) -> None:
    total = len(rows)
    flags = {
        "Flag_Meta": sum(1 for r in rows if r.get("Flag_Meta") == "Y"),
        "Flag_Host": sum(1 for r in rows if r.get("Flag_Host") == "Y"),
        "Flag_Shared_Sheet": sum(1 for r in rows if r.get("Flag_Shared_Sheet") == "Y"),
        "Flag_Back_Nonstandard": sum(1 for r in rows if r.get("Flag_Back_Nonstandard") == "Y"),
    }
    url_checked = sum(1 for r in rows if r.get("Flag_URL_Status"))
    url_404 = sum(1 for r in rows if r.get("Flag_URL_Status") == "404")
    unique_sheets = len({r["FaceURL"] for r in rows if r.get("FaceURL")})

    print()
    print("=" * 50)
    print(f"  Total cards: {total}")
    print(f"  Unique image sheets: {unique_sheets}")
    print(f"  Flag_Meta (missing fields):     {flags['Flag_Meta']}")
    print(f"  Flag_Host (non-Steam URL):      {flags['Flag_Host']}")
    print(f"  Flag_Shared_Sheet (shared img): {flags['Flag_Shared_Sheet']}")
    print(f"  Flag_Back_Nonstandard:          {flags['Flag_Back_Nonstandard']}")
    if url_checked:
        print(f"  URL checked: {url_checked}, 404s: {url_404}")
    print("=" * 50)


def main():
    args = parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    # Resolve output directory + filename prefix based on mode.
    if args.include_campaigns:
        out_prefix = "korean_images"
        default_output = DEFAULT_OUTPUT_CAMPAIGNS
    else:
        out_prefix = "korean_playercards"
        default_output = DEFAULT_OUTPUT
    output_dir = args.output if args.output is not None else default_output

    if not args.source.exists():
        print(f"ERROR: Source directory not found: {args.source}")
        sys.exit(1)

    print(f"Walking: {args.source}")
    rows: list[dict] = []
    json_count = 0
    for path in iter_card_jsons(args.source):
        json_count += 1
        rows.extend(extract_rows(path, args.source, pack="playercards"))

    if args.include_campaigns:
        if args.campaigns_from_combined:
            combined_path = DEFAULT_CAMPAIGN_COMBINED
            if not combined_path.exists():
                print(f"ERROR: Combined JSON not found: {combined_path}")
                sys.exit(1)
            print(f"Reading campaigns from combined: {combined_path}")
            campaign_rows = extract_rows_from_combined(combined_path, pack="campaigns")
            rows.extend(campaign_rows)
            print(f"  campaign card rows extracted (combined): {len(campaign_rows)}")
        else:
            if not args.source_campaigns.exists():
                print(
                    f"ERROR: Campaigns decomposed root not found: {args.source_campaigns}. "
                    "Use --campaigns-from-combined to fall back to downloadable JSON."
                )
                sys.exit(1)
            print(f"Walking campaigns: {args.source_campaigns}")
            campaign_count = 0
            start_len = len(rows)
            for path in iter_card_jsons(args.source_campaigns):
                campaign_count += 1
                rows.extend(extract_rows(path, args.source_campaigns, pack="campaigns"))
            print(
                f"  campaign JSON files: {campaign_count}, rows added: "
                f"{len(rows) - start_len}"
            )
            json_count += campaign_count

    print(f"JSON files found: {json_count}, card rows extracted: {len(rows)}")

    dup_counts = compute_duplicate_counts(rows)
    mode_back = compute_mode_back(rows)
    apply_flags(rows, dup_counts, mode_back)

    if args.check_urls:
        annotate_url_status(rows)

    rows = sort_rows(rows)

    output_dir.mkdir(parents=True, exist_ok=True)

    if args.format in ("both", "csv"):
        write_csv(rows, output_dir / f"{out_prefix}_catalog.csv")

    if args.format in ("both", "xlsx"):
        write_xlsx(rows, output_dir / f"{out_prefix}_catalog.xlsx")

    if args.by_sheet:
        write_by_sheet_csv(rows, output_dir / f"{out_prefix}_by_sheet.csv")

    if args.html:
        write_html_gallery(rows, output_dir / f"{out_prefix}_gallery.html")

    print_summary(rows)


if __name__ == "__main__":
    main()
