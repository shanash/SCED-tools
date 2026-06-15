#!/usr/bin/env python3
"""
Generate a single-file, self-contained HTML gallery for Korean card image review.

Usage:
  build-card-review-gallery.py
    [--candidates PATH] [--output PATH]
    [--page-size N] [--sort-by FIELD]

Default --candidates: output/korean-image-review/candidates_index.json
Default --output: output/korean-image-review/cards_review.html
Default --page-size: 50
Default --sort-by: diversity_desc

Exit codes:
  0 success
  1 warnings
  20 candidates_index missing required fields
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_CANDIDATES = SCRIPTS_DIR / "output" / "korean-image-review" / "candidates_index.json"
DEFAULT_OUTPUT = SCRIPTS_DIR / "output" / "korean-image-review" / "cards_review.html"
DEFAULT_PAGE_SIZE = 50


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate HTML gallery for Korean card image review."
    )
    p.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    p.add_argument("--sort-by", type=str, default="diversity_desc",
                   choices=["diversity_desc", "arkham_id", "diversity_asc"])
    return p.parse_args(argv)


def sort_cards(cards: list[dict], sort_by: str) -> list[dict]:
    if sort_by == "diversity_desc":
        return sorted(cards, key=lambda c: (-c.get("diversity_score", 0), c["arkham_id"]))
    elif sort_by == "diversity_asc":
        return sorted(cards, key=lambda c: (c.get("diversity_score", 0), c["arkham_id"]))
    else:  # arkham_id
        return sorted(cards, key=lambda c: c["arkham_id"])


def build_html(cards: list[dict], candidates_data: dict, page_size: int) -> str:
    now = datetime.now(timezone.utc)
    generated = now.strftime("%Y-%m-%d %H:%M UTC")
    generation_timestamp = now.strftime("%Y%m%dT%H%M%SZ")

    cards_json = json.dumps(cards, ensure_ascii=False).replace("</", "<\\/")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Korean Card Image Review — {generated}</title>
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
  #filter-select {{
    padding: 6px 10px; border: 1px solid #0f3460; border-radius: 6px;
    background: #0f3460; color: #eee; font-size: 13px; cursor: pointer;
  }}
  #stats {{ font-size: 13px; color: #aaa; white-space: nowrap; }}
  button {{
    padding: 6px 14px; border: none; border-radius: 6px;
    cursor: pointer; font-size: 13px; font-weight: bold;
  }}
  #btn-export {{ background: #e94560; color: #fff; }}
  #btn-import {{ background: #0f3460; color: #eee; }}
  #btn-skip-undecided {{ background: #444; color: #eee; }}
  #btn-auto-v2 {{ background: #2d6a4f; color: #eee; }}
  #btn-export:hover {{ background: #c73652; }}
  #btn-import:hover {{ background: #1a4a6e; }}
  #btn-skip-undecided:hover {{ background: #555; }}
  #btn-auto-v2:hover {{ background: #3a8a65; }}

  #cards-table {{ width: 100%; border-collapse: collapse; }}
  #cards-table th {{
    background: #0f3460; color: #aaa; font-size: 12px; padding: 8px 6px;
    text-align: left; border-bottom: 2px solid #0f3460; position: sticky; top: 58px; z-index: 50;
  }}
  .card-row {{
    border-bottom: 1px solid #0f3460;
    transition: background .1s;
  }}
  .card-row:hover {{ background: #1d2a4a; }}
  .card-row:not(.cursor):hover {{ cursor: pointer; }}
  .card-row.decided {{ background: #1a2e1a; }}
  .card-row.cohort-conflict {{ border-left: 4px solid #e94560; }}
  .card-row.cursor {{
    outline: 2px solid #ffd166;
    outline-offset: -2px;
    background: #1d2f4f;
  }}
  .card-row.cursor.decided {{ background: #1f3a25; }}
  .card-info {{ padding: 8px 6px; vertical-align: top; min-width: 120px; }}
  .arkham-id {{ font-size: 11px; color: #888; }}
  .nickname {{ font-size: 13px; font-weight: bold; color: #eee; }}
  .diversity-badge {{
    display: inline-block; font-size: 10px; padding: 1px 5px;
    border-radius: 8px; margin-top: 3px;
    background: #0f3460; color: #88ccff;
  }}
  .candidate-cell {{ padding: 6px 4px; vertical-align: top; min-width: 150px; max-width: 200px; }}
  .candidate-wrap {{
    border: 1px solid #0f3460; border-radius: 6px; overflow: hidden;
    background: #0a0a1a;
  }}
  .candidate-wrap.selected {{ border-color: #e94560; background: #2a1020; }}
  .candidate-wrap.unavailable {{ opacity: 0.35; }}
  .cand-img-wrap {{
    position: relative; min-height: 80px;
    display: flex; align-items: center; justify-content: center;
  }}
  .cand-tile {{
    width: 100%; aspect-ratio: 5 / 7;
    background-repeat: no-repeat;
    background-color: #0a0a1a;
    display: block;
  }}
  .cand-tile.tile-error {{
    background-color: #2a0a0a !important;
  }}
  .cand-tile.tile-error::before {{
    content: '⚠ load failed';
    display: block; padding: 4px 6px;
    font-size: 10px; color: #ffaaaa;
  }}
  .url-badge {{
    position: absolute; top: 4px; right: 4px; font-size: 9px; font-weight: bold;
    padding: 1px 5px; border-radius: 8px;
  }}
  .url-badge.ok {{ background: #2d6a4f; color: #a8d5b5; }}
  .url-badge.fail {{ background: #7b2d2d; color: #ffaaaa; }}
  .url-badge.unknown {{ background: #444; color: #aaa; }}
  .open-link {{
    position: absolute; bottom: 4px; right: 4px;
    background: rgba(0,0,0,.6); color: #88ccff;
    font-size: 10px; padding: 1px 5px; border-radius: 8px; text-decoration: none;
  }}
  .open-link:hover {{ background: rgba(0,0,0,.9); }}
  .cand-footer {{ padding: 6px 8px; display: flex; align-items: center; gap: 6px; }}
  .cand-footer label {{
    display: flex; align-items: center; gap: 4px;
    cursor: pointer; font-size: 12px; font-weight: bold; color: #e94560;
    user-select: none; flex: 1;
  }}
  .cand-footer input[type=radio] {{ accent-color: #e94560; cursor: pointer; }}
  .version-label {{ font-size: 11px; color: #888; }}
  .skip-cell {{ padding: 6px 4px; vertical-align: top; min-width: 80px; }}
  .note-cell {{ padding: 6px 4px; vertical-align: top; min-width: 120px; }}
  .note-input {{
    width: 100%; padding: 4px 6px; font-size: 11px;
    border: 1px solid #0f3460; border-radius: 4px;
    background: #0f3460; color: #eee;
  }}
  .cohort-badge {{
    display: inline-block; font-size: 9px; padding: 1px 5px; border-radius: 8px;
    background: #e94560; color: #fff; margin-top: 3px;
  }}

  #pagination {{
    display: flex; align-items: center; justify-content: center; gap: 12px;
    padding: 16px; font-size: 14px; border-top: 1px solid #0f3460;
  }}
  #pagination button {{ background: #0f3460; color: #eee; padding: 6px 16px; border-radius: 6px; }}
  #pagination button:disabled {{ opacity: 0.4; cursor: default; }}
  #page-info {{ color: #aaa; min-width: 110px; text-align: center; }}

  .modal-overlay {{
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.8);
    z-index: 200; align-items: center; justify-content: center;
  }}
  .modal-overlay.open {{ display: flex; }}
  .modal-box {{
    background: #16213e; border: 2px solid #e94560; border-radius: 12px;
    padding: 20px; max-width: 700px; width: 90%; max-height: 80vh;
    display: flex; flex-direction: column; gap: 12px;
  }}
  .modal-box h2 {{ color: #e94560; font-size: 16px; }}
  .modal-box p {{ font-size: 13px; color: #aaa; }}
  .modal-box textarea {{
    flex: 1; min-height: 200px; background: #0a0a1a; color: #eee;
    border: 1px solid #0f3460; border-radius: 6px; padding: 10px;
    font-family: monospace; font-size: 12px; resize: vertical;
  }}
  .modal-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .modal-actions button {{ font-size: 13px; }}
  .btn-primary {{ background: #e94560; color: #fff; }}
  .btn-secondary {{ background: #0f3460; color: #eee; }}
  .btn-neutral {{ background: #333; color: #eee; }}
</style>
</head>
<body>

<div id="toolbar">
  <h1>&#x1F0CF; Korean Image Review</h1>
  <input id="search" type="search" placeholder="ArkhamID or nickname..." oninput="onSearch()">
  <select id="filter-select" onchange="onFilterChange()">
    <option value="all">All cards</option>
    <option value="undecided">Undecided only</option>
    <option value="decided">Decided only</option>
    <option value="cohort-conflict">Cohort conflicts</option>
    <option value="single-candidate">Single candidate</option>
  </select>
  <span id="stats"></span>
  <button id="btn-export" onclick="openExportModal()">Export JSON</button>
  <button id="btn-import" onclick="openImportModal()">Import JSON</button>
  <button id="btn-skip-undecided" onclick="skipAllUndecided()">Skip all undecided</button>
  <button id="btn-auto-v2" onclick="autoSetV2Single()">Auto-set v2 (single)</button>
</div>

<table id="cards-table">
  <thead>
    <tr>
      <th>Card</th>
      <th>EN</th>
      <th>KO</th>
      <th>v0</th>
      <th>v1</th>
      <th>v2</th>
      <th>Skip</th>
      <th>Note</th>
    </tr>
  </thead>
  <tbody id="cards-tbody"></tbody>
</table>

<div id="pagination">
  <button id="btn-prev" onclick="goPage(-1)">&#9664; Prev</button>
  <span id="page-info"></span>
  <button id="btn-next" onclick="goPage(1)">Next &#9654;</button>
</div>

<!-- Export modal -->
<div id="export-modal" class="modal-overlay">
  <div class="modal-box">
    <h2>Export Decisions (JSON)</h2>
    <textarea id="export-text" readonly></textarea>
    <div class="modal-actions">
      <button class="btn-secondary" onclick="copyExport()">Copy</button>
      <button class="btn-primary" onclick="downloadExport()">Download</button>
      <button class="btn-neutral" onclick="closeModal('export-modal')">Close</button>
    </div>
  </div>
</div>

<!-- Import modal -->
<div id="import-modal" class="modal-overlay">
  <div class="modal-box">
    <h2>Import Decisions (JSON)</h2>
    <p>Paste a previously exported review_decisions.json. Existing decisions will be merged.</p>
    <textarea id="import-text" placeholder='{{"schema_version":1, "decisions":[...]}}'></textarea>
    <div class="modal-actions">
      <button class="btn-primary" onclick="applyImport()">Import</button>
      <button class="btn-neutral" onclick="closeModal('import-modal')">Cancel</button>
    </div>
  </div>
</div>

<!-- Prior session import modal -->
<div id="prior-session-modal" class="modal-overlay">
  <div class="modal-box">
    <h2>Previous Session Detected</h2>
    <p id="prior-session-msg"></p>
    <div class="modal-actions">
      <button class="btn-primary" onclick="importPriorSession()">Import</button>
      <button class="btn-neutral" onclick="discardPriorSession()">Discard</button>
    </div>
  </div>
</div>

<!-- Auto-export prompt modal -->
<div id="autoexport-modal" class="modal-overlay">
  <div class="modal-box">
    <h2>Backup Reminder</h2>
    <p id="autoexport-msg"></p>
    <div class="modal-actions">
      <button class="btn-primary" onclick="doAutoExport()">Backup now</button>
      <button class="btn-neutral" onclick="closeModal('autoexport-modal')">Not now</button>
      <button class="btn-neutral" onclick="suppressAutoExport()">Don't ask again</button>
    </div>
  </div>
</div>

<script>
const CARDS = {cards_json};
const PAGE_SIZE = {page_size};
const GENERATION_TIMESTAMP = "{generation_timestamp}";
const PRIMARY_KEY = "korean_image_review_v1_state:" + GENERATION_TIMESTAMP;
const LATEST_KEY = "korean_image_review_v1_state:latest";

// Build card lookup by arkham_id
const CARD_BY_ID = {{}};
CARDS.forEach(c => CARD_BY_ID[c.arkham_id] = c);

// Detect cohort conflicts across ALL cards (not just page)
const COHORT_CONFLICTS = (() => {{
  const cohortFaceUrls = {{}};
  CARDS.forEach(c => {{
    if (!c.sheet_cohort_v2) return;
    if (!cohortFaceUrls[c.sheet_cohort_v2]) cohortFaceUrls[c.sheet_cohort_v2] = new Set();
    const v2cand = c.candidates.find(x => x.version === 'v2' && x.available);
    if (v2cand) cohortFaceUrls[c.sheet_cohort_v2].add(v2cand.face_url);
  }});
  const conflictSet = new Set();
  Object.entries(cohortFaceUrls).forEach(([cohort, urls]) => {{
    if (urls.size > 1) conflictSet.add(cohort);
  }});
  return conflictSet;
}})();

// State
let state = {{
  schema_version: 1,
  generation_id: GENERATION_TIMESTAMP,
  decisions: {{}},
  ui: {{ currentPage: 0, lastExportedAt: null, lastDecisionCount: 0, autoExportSuppressed: false, cursorIndex: -1 }}
}};

let currentPage = 0;
let filteredCards = CARDS.slice();
let searchTerm = '';
let filterMode = 'all';
let noteDebounceTimers = {{}};

// IntersectionObserver for lazy-load images
let imgObserver = null;

function createImgObserver() {{
  if (imgObserver) imgObserver.disconnect();
  imgObserver = new IntersectionObserver((entries) => {{
    entries.forEach(entry => {{
      if (!entry.isIntersecting) return;
      const tile = entry.target;
      const url = tile.dataset.bg;
      if (!url) return;
      const probe = new Image();
      probe.onload = () => {{
        tile.style.backgroundImage = `url("${{url.replace(/"/g, '%22')}}")`;
        delete tile.dataset.bg;
        imgObserver.unobserve(tile);
      }};
      probe.onerror = () => {{
        tile.classList.add('tile-error');
        console.warn('Gallery image load failed:', url);
        imgObserver.unobserve(tile);
        // data-bg intentionally retained for retry on next page entry
      }};
      probe.src = url;
      // STALE CLOSURE: if createImgObserver() is called again (page change),
      // in-flight probe callbacks reference the old observer instance.
      // unobserve() on a disconnected observer is a no-op (W3C spec) — no leak.
    }});
  }}, {{ rootMargin: '300px' }});
}}

function escHtml(s) {{
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function computeTileStyle(cand) {{
  const nw = cand.num_width  || 1;
  const nh = cand.num_height || 1;
  const tileIndex = (cand.card_id || 0) % 100;
  const row = Math.floor(tileIndex / nw);
  const col = tileIndex % nw;
  return {{
    bgSize: `${{nw * 100}}% ${{nh * 100}}%`,
    bgPosX: nw > 1 ? `${{(col / (nw - 1)) * 100}}%` : '0%',
    bgPosY: nh > 1 ? `${{(row / (nh - 1)) * 100}}%` : '0%',
  }};
}}

function saveState() {{
  const data = JSON.stringify(state);
  try {{ localStorage.setItem(PRIMARY_KEY, data); }} catch(e) {{ console.warn('saveState PRIMARY_KEY failed:', e); }}
  try {{ localStorage.setItem(LATEST_KEY, data); }} catch(e) {{ console.warn('saveState LATEST_KEY failed:', e); }}
}}

function loadState() {{
  try {{
    const raw = localStorage.getItem(PRIMARY_KEY);
    if (raw) {{
      const parsed = JSON.parse(raw);
      state = Object.assign(state, parsed);
      if (!state.ui) state.ui = {{}};
      if (!Number.isInteger(state.ui.cursorIndex)) state.ui.cursorIndex = -1;
    }}
  }} catch(e) {{ console.warn('loadState failed:', e); }}
}}

function checkPriorSession() {{
  try {{
    const raw = localStorage.getItem(LATEST_KEY);
    if (!raw) return;
    const prior = JSON.parse(raw);
    if (prior.generation_id === GENERATION_TIMESTAMP) return;
    const count = Object.keys(prior.decisions || {{}}).length;
    if (count === 0) return;
    const msg = document.getElementById('prior-session-msg');
    msg.textContent = `Previous session detected (${{count}} decisions from gallery ${{prior.generation_id}}). Import or discard?`;
    window._priorSessionData = prior;
    openModal('prior-session-modal');
  }} catch(e) {{}}
}}

function importPriorSession() {{
  const prior = window._priorSessionData;
  if (!prior) return;
  const priorDec = prior.decisions || {{}};
  let imported = 0;
  Object.entries(priorDec).forEach(([id, dec]) => {{
    if (CARD_BY_ID[id]) {{
      state.decisions[id] = dec;
      imported++;
    }}
  }});
  saveState();
  closeModal('prior-session-modal');
  buildPage(currentPage);
  updateStats();
  alert(`Imported ${{imported}} decisions.`);
}}

function discardPriorSession() {{
  closeModal('prior-session-modal');
  saveState();
}}

function openModal(id) {{ document.getElementById(id).classList.add('open'); }}
function closeModal(id) {{ document.getElementById(id).classList.remove('open'); }}

function getUrlBadgeHtml(urlStatus, side) {{
  if (!urlStatus || !urlStatus[side]) return '<span class="url-badge unknown">?</span>';
  const s = urlStatus[side].status;
  if (s === 'ok') return '<span class="url-badge ok">OK</span>';
  if (s === 'fail') return '<span class="url-badge fail">FAIL</span>';
  return '<span class="url-badge unknown">?</span>';
}}

function buildCandidateCell(card, cand) {{
  const dec = state.decisions[card.arkham_id];
  const chosen = dec && dec.choice === cand.version;
  if (!cand.available) {{
    return `<td class="candidate-cell">
      <div class="candidate-wrap unavailable">
        <div class="cand-img-wrap" style="height:80px;"><span style="color:#555;font-size:11px;">N/A</span></div>
        <div class="cand-footer"><span class="version-label">${{escHtml(cand.version)}}</span></div>
      </div>
    </td>`;
  }}
  const selectedClass = chosen ? ' selected' : '';
  const urlBadge = getUrlBadgeHtml(cand.url_status, 'face');
  const radioId = `radio-${{card.arkham_id}}-${{cand.version}}`;
  const {{ bgSize, bgPosX, bgPosY }} = computeTileStyle(cand);
  return `<td class="candidate-cell">
    <div class="candidate-wrap${{selectedClass}}" id="wrap-${{card.arkham_id}}-${{cand.version}}">
      <div class="cand-img-wrap">
        <div class="cand-tile"
             data-bg="${{escHtml(cand.face_url)}}"
             style="background-size:${{bgSize}};background-position:${{bgPosX}} ${{bgPosY}};"></div>
        ${{urlBadge}}
        <a class="open-link" href="${{escHtml(cand.face_url)}}" target="_blank" onclick="event.stopPropagation()">&#8599;</a>
      </div>
      <div class="cand-footer">
        <label for="${{radioId}}">
          <input type="radio" id="${{radioId}}" name="choice-${{escHtml(card.arkham_id)}}"
                 value="${{escHtml(cand.version)}}"
                 ${{chosen ? 'checked' : ''}}
                 onchange="onChoice('${{escHtml(card.arkham_id)}}', '${{escHtml(cand.version)}}')">
          ${{escHtml(cand.version)}}
        </label>
      </div>
    </div>
  </td>`;
}}

function buildRow(card) {{
  const dec = state.decisions[card.arkham_id];
  const decided = !!dec;
  const isConflict = card.sheet_cohort_v2 && COHORT_CONFLICTS.has(card.sheet_cohort_v2);
  const rowClasses = [
    'card-row',
    decided ? 'decided' : '',
    isConflict ? 'cohort-conflict' : ''
  ].filter(Boolean).join(' ');

  const versionOrder = ['en', 'ko', 'v0', 'v1', 'v2'];
  const candCells = versionOrder.map(v => {{
    const cand = card.candidates.find(c => c.version === v);
    if (!cand) return `<td class="candidate-cell"><div class="candidate-wrap unavailable"><div class="cand-img-wrap" style="height:80px;"></div><div class="cand-footer"><span class="version-label">${{v}}</span></div></div></td>`;
    return buildCandidateCell(card, cand);
  }}).join('');

  const skipChecked = dec && dec.choice === 'skip' ? 'checked' : '';
  const noteVal = escHtml(dec && dec.note ? dec.note : '');
  const cohortBadge = isConflict ? '<span class="cohort-badge">cohort!</span>' : '';

  return `<tr class="${{rowClasses}}" id="row-${{escHtml(card.arkham_id)}}" data-id="${{escHtml(card.arkham_id)}}">
    <td class="card-info">
      <div class="arkham-id">${{escHtml(card.arkham_id)}}</div>
      <div class="nickname">${{escHtml(card.nickname_v2_ko || card.nickname_en || '')}}</div>
      <span class="diversity-badge">div: ${{card.diversity_score}}</span>
      ${{cohortBadge}}
    </td>
    ${{candCells}}
    <td class="skip-cell">
      <label style="font-size:12px;cursor:pointer;">
        <input type="radio" name="choice-${{escHtml(card.arkham_id)}}" value="skip"
               ${{skipChecked}}
               onchange="onChoice('${{escHtml(card.arkham_id)}}', 'skip')">
        Skip
      </label>
    </td>
    <td class="note-cell">
      <input class="note-input" type="text" placeholder="note..."
             id="note-${{escHtml(card.arkham_id)}}"
             value="${{noteVal}}"
             oninput="onNoteInput('${{escHtml(card.arkham_id)}}', this.value)">
    </td>
  </tr>`;
}}

function getPageCards() {{
  const start = currentPage * PAGE_SIZE;
  return filteredCards.slice(start, start + PAGE_SIZE);
}}

function buildPage(page) {{
  // Disconnect observers
  if (imgObserver) imgObserver.disconnect();
  createImgObserver();

  currentPage = page;
  const tbody = document.getElementById('cards-tbody');
  tbody.innerHTML = '';

  const pageCards = getPageCards();
  pageCards.forEach(card => {{
    tbody.insertAdjacentHTML('beforeend', buildRow(card));
  }});

  // Register images for lazy load
  tbody.querySelectorAll('.cand-tile[data-bg]').forEach(el => imgObserver.observe(el));

  updatePagination();
  updateStats();
  state.ui.currentPage = page;
  renderCursor({{ scroll: false }});  // re-apply .cursor class without scrolling
  saveState();
}}

function updatePagination() {{
  const totalPages = Math.ceil(filteredCards.length / PAGE_SIZE);
  document.getElementById('page-info').textContent =
    `${{currentPage + 1}} / ${{Math.max(1, totalPages)}} pages (${{filteredCards.length}} cards)`;
  document.getElementById('btn-prev').disabled = currentPage === 0;
  document.getElementById('btn-next').disabled = currentPage >= totalPages - 1;
}}

function updateStats() {{
  const total = CARDS.length;
  const decided = Object.keys(state.decisions).length;
  const pct = total ? Math.round(decided / total * 100) : 0;
  document.getElementById('stats').textContent =
    `${{decided}}/${{total}} decided (${{pct}}%)`;
}}

function isCursorRowVisible(row) {{
  const rect = row.getBoundingClientRect();
  const vh = window.innerHeight;
  if (rect.top >= 0 && rect.bottom <= vh) return true;
  const visibleTop = Math.max(rect.top, 0);
  const visibleBottom = Math.min(rect.bottom, vh);
  const visibleHeight = visibleBottom - visibleTop;
  return visibleHeight >= rect.height * 0.5;
}}

function renderCursor(opts) {{
  const scroll = !(opts && opts.scroll === false);
  const force = !!(opts && opts.force);
  // Clear stale cursor highlight (only one .cursor at a time)
  document.querySelectorAll('.card-row.cursor').forEach(r => r.classList.remove('cursor'));
  const idx = state.ui.cursorIndex;
  if (idx < 0 || idx >= filteredCards.length) return;
  const card = filteredCards[idx];
  const row = document.getElementById('row-' + card.arkham_id);
  if (!row) return;  // cursor's card not on current page
  row.classList.add('cursor');
  if (scroll && (force || !isCursorRowVisible(row))) {{
    row.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
  }}
}}

function setCursor(index, opts) {{
  state.ui.cursorIndex = index;
  // Page auto-flip: if cursor crossed into a different page, rebuild that page
  const targetPage = Math.floor(index / PAGE_SIZE);
  if (index >= 0 && targetPage !== currentPage) {{
    buildPage(targetPage);  // buildPage already calls saveState() at the end
    // renderCursor runs after buildPage rewrites tbody
  }}
  renderCursor(opts);
  saveState();
}}

function moveCursor(delta) {{
  if (filteredCards.length === 0) return;
  const cur = state.ui.cursorIndex;
  // First navigation from -1 sentinel: jump to top of current page
  if (cur < 0) {{
    setCursor(currentPage * PAGE_SIZE);
    return;
  }}
  const next = cur + delta;
  // Hard stops at the absolute first/last of the filtered list (user decision #1)
  if (next < 0 || next >= filteredCards.length) return;
  setCursor(next);
}}

function setCursorByClick(event) {{
  if (event.target.closest('input, textarea, select, button, a, label')) return;
  const row = event.target.closest('tr.card-row');
  if (!row) return;
  const arkham_id = row.dataset.id;
  if (!arkham_id) return;
  const idx = filteredCards.findIndex(c => c.arkham_id === arkham_id);
  if (idx < 0) return;
  setCursor(idx, {{ scroll: false }});
}}

function goPage(delta) {{
  const totalPages = Math.ceil(filteredCards.length / PAGE_SIZE);
  const np = currentPage + delta;
  if (np < 0 || np >= totalPages) return;
  buildPage(np);
  window.scrollTo(0, 0);
}}

function applyFilters() {{
  let result = CARDS.slice();
  if (searchTerm) {{
    const q = searchTerm.toLowerCase();
    result = result.filter(c =>
      c.arkham_id.toLowerCase().includes(q) ||
      (c.nickname_en || '').toLowerCase().includes(q) ||
      (c.nickname_v2_ko || '').toLowerCase().includes(q)
    );
  }}
  switch (filterMode) {{
    case 'undecided':
      result = result.filter(c => !state.decisions[c.arkham_id]);
      break;
    case 'decided':
      result = result.filter(c => !!state.decisions[c.arkham_id]);
      break;
    case 'cohort-conflict':
      result = result.filter(c => c.sheet_cohort_v2 && COHORT_CONFLICTS.has(c.sheet_cohort_v2));
      break;
    case 'single-candidate':
      result = result.filter(c => c.diversity_score === 1);
      break;
  }}
  filteredCards = result;
  state.ui.cursorIndex = -1;  // reset cursor — old index is no longer meaningful
  buildPage(0);
}}

function onSearch() {{
  searchTerm = document.getElementById('search').value.trim().toLowerCase();
  applyFilters();
}}

function onFilterChange() {{
  filterMode = document.getElementById('filter-select').value;
  applyFilters();
}}

function onChoice(arkham_id, choice) {{
  const card = CARD_BY_ID[arkham_id];
  if (!card) return;
  const now = new Date().toISOString();

  // Get URL data from chosen candidate
  const cand = card.candidates.find(c => c.version === choice) || null;
  const dec = {{
    choice,
    note: (state.decisions[arkham_id] && state.decisions[arkham_id].note) || '',
    decided_at: now,
    face_url: cand && cand.available ? cand.face_url : null,
    back_url: cand && cand.available ? cand.back_url : null,
    num_width: cand && cand.available ? cand.num_width : null,
    num_height: cand && cand.available ? cand.num_height : null,
    deck_key: card.target_deck_key,
  }};
  state.decisions[arkham_id] = dec;

  // Update row class
  const row = document.getElementById('row-' + arkham_id);
  if (row) {{
    row.classList.add('decided');
    // Update candidate wrap selected state
    ['en','ko','v0','v1','v2'].forEach(v => {{
      const wrap = document.getElementById('wrap-' + arkham_id + '-' + v);
      if (wrap) wrap.classList.toggle('selected', v === choice);
    }});
  }}

  updateStats();
  saveState();
  checkAutoExportPrompt();
}}

function onNoteInput(arkham_id, value) {{
  clearTimeout(noteDebounceTimers[arkham_id]);
  noteDebounceTimers[arkham_id] = setTimeout(() => {{
    if (!state.decisions[arkham_id]) {{
      state.decisions[arkham_id] = {{ choice: null, note: value, decided_at: new Date().toISOString() }};
    }} else {{
      state.decisions[arkham_id].note = value;
    }}
    saveState();
  }}, 300);
}}

function skipAllUndecided() {{
  const now = new Date().toISOString();
  let count = 0;
  CARDS.forEach(card => {{
    if (!state.decisions[card.arkham_id]) {{
      state.decisions[card.arkham_id] = {{
        choice: 'skip', note: '', decided_at: now,
        face_url: null, back_url: null, num_width: null, num_height: null, deck_key: card.target_deck_key
      }};
      count++;
    }}
  }});
  saveState();
  buildPage(currentPage);
  alert(`Skipped ${{count}} undecided cards.`);
}}

function autoSetV2Single() {{
  const now = new Date().toISOString();
  let count = 0;
  CARDS.forEach(card => {{
    if (card.diversity_score !== 1) return;
    if (state.decisions[card.arkham_id]) return;
    const v2cand = card.candidates.find(c => c.version === 'v2' && c.available);
    if (!v2cand) return;
    state.decisions[card.arkham_id] = {{
      choice: 'v2', note: '', decided_at: now,
      face_url: v2cand.face_url, back_url: v2cand.back_url,
      num_width: v2cand.num_width, num_height: v2cand.num_height,
      deck_key: card.target_deck_key
    }};
    count++;
  }});
  saveState();
  buildPage(currentPage);
  alert(`Auto-set v2 for ${{count}} single-candidate cards.`);
}}

function buildExportData() {{
  const total = CARDS.length;
  const byChoice = {{ en: 0, ko: 0, v0: 0, v1: 0, v2: 0, skip: 0 }};
  const decisionsArr = [];

  Object.entries(state.decisions).forEach(([arkham_id, dec]) => {{
    if (!dec.choice) return;
    byChoice[dec.choice] = (byChoice[dec.choice] || 0) + 1;
    decisionsArr.push({{
      arkham_id,
      choice: dec.choice,
      face_url: dec.face_url,
      back_url: dec.back_url,
      num_width: dec.num_width,
      num_height: dec.num_height,
      deck_key: dec.deck_key,
      note: dec.note || '',
      decided_at: dec.decided_at,
    }});
  }});

  const decided = decisionsArr.length;
  return {{
    schema_version: 1,
    generated_at: new Date().toISOString(),
    gallery_id: GENERATION_TIMESTAMP,
    decisions: decisionsArr,
    stats: {{
      total_cards: total,
      decided,
      by_choice: byChoice,
      skipped_explicit: byChoice.skip || 0,
      undecided: total - decided,
    }},
  }};
}}

function openExportModal() {{
  const data = buildExportData();
  document.getElementById('export-text').value = JSON.stringify(data, null, 2);
  openModal('export-modal');
}}

function copyExport() {{
  const ta = document.getElementById('export-text');
  ta.select();
  document.execCommand('copy');
  alert('Copied!');
}}

function downloadExport() {{
  const data = buildExportData();
  const decided = data.stats.decided;
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const filename = `review_decisions_${{ts}}_${{decided}}cards.json`;
  const blob = new Blob([JSON.stringify(data, null, 2)], {{ type: 'application/json' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
  state.ui.lastExportedAt = new Date().toISOString();
  state.ui.lastDecisionCount = decided;
  saveState();
  closeModal('export-modal');
}}

function openImportModal() {{ openModal('import-modal'); }}

function applyImport() {{
  const text = document.getElementById('import-text').value.trim();
  if (!text) {{ alert('No data to import.'); return; }}
  try {{
    const data = JSON.parse(text);
    const decisions = data.decisions || [];
    let imported = 0;
    decisions.forEach(d => {{
      if (CARD_BY_ID[d.arkham_id] && d.choice) {{
        state.decisions[d.arkham_id] = {{
          choice: d.choice,
          note: d.note || '',
          decided_at: d.decided_at || new Date().toISOString(),
          face_url: d.face_url || null,
          back_url: d.back_url || null,
          num_width: d.num_width || null,
          num_height: d.num_height || null,
          deck_key: d.deck_key || null,
        }};
        imported++;
      }}
    }});
    saveState();
    closeModal('import-modal');
    buildPage(currentPage);
    alert(`Imported ${{imported}} decisions.`);
  }} catch(e) {{
    alert('JSON parse error: ' + e.message);
  }}
}}

function checkAutoExportPrompt() {{
  if (state.ui.autoExportSuppressed) return;
  const decided = Object.values(state.decisions).filter(d => d.choice).length;
  const last = state.ui.lastDecisionCount || 0;
  if (decided > 0 && decided % 100 === 0 && decided !== last) {{
    document.getElementById('autoexport-msg').textContent =
      `You have ${{decided}} decisions. Would you like to backup now?`;
    openModal('autoexport-modal');
  }}
}}

function doAutoExport() {{
  closeModal('autoexport-modal');
  downloadExport();
}}

function suppressAutoExport() {{
  state.ui.autoExportSuppressed = true;
  saveState();
  closeModal('autoexport-modal');
}}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {{
  const focused = document.activeElement;
  const isInput = focused && (focused.tagName === 'INPUT' || focused.tagName === 'TEXTAREA' || focused.tagName === 'SELECT');

  // Pagination
  if (e.key === 'ArrowLeft' && !isInput) {{ goPage(-1); return; }}
  if (e.key === 'ArrowRight' && !isInput) {{ goPage(1); return; }}

  // Cursor navigation (j/k or ArrowDown/ArrowUp)
  if (!isInput && (e.key === 'j' || e.key === 'ArrowDown')) {{
    e.preventDefault();
    moveCursor(1);
    return;
  }}
  if (!isInput && (e.key === 'k' || e.key === 'ArrowUp')) {{
    e.preventDefault();
    moveCursor(-1);
    return;
  }}
  if (!isInput && e.key === 'g') {{
    e.preventDefault();
    if (state.ui.cursorIndex >= 0) renderCursor({{ scroll: true, force: true }});
    return;
  }}

  // Card-targeted hotkeys: read cursor instead of activeElement.closest()
  if (!['1','2','3','4','5','0','n'].includes(e.key)) return;
  if (isInput) return;
  const idx = state.ui.cursorIndex;
  if (idx < 0 || idx >= filteredCards.length) return;
  const arkham_id = filteredCards[idx].arkham_id;
  e.preventDefault();
  if (e.key === '1') {{ document.getElementById(`radio-${{arkham_id}}-en`)?.click(); }}
  else if (e.key === '2') {{ document.getElementById(`radio-${{arkham_id}}-ko`)?.click(); }}
  else if (e.key === '3') {{ document.getElementById(`radio-${{arkham_id}}-v0`)?.click(); }}
  else if (e.key === '4') {{ document.getElementById(`radio-${{arkham_id}}-v1`)?.click(); }}
  else if (e.key === '5') {{ document.getElementById(`radio-${{arkham_id}}-v2`)?.click(); }}
  else if (e.key === '0') {{
    const row = document.getElementById('row-' + arkham_id);
    const skip = row && row.querySelector('input[type=radio][value=skip]');
    if (skip) {{ skip.checked = true; onChoice(arkham_id, 'skip'); }}
  }}
  else if (e.key === 'n') {{ document.getElementById(`note-${{arkham_id}}`)?.focus(); }}
}});

// Init
loadState();
createImgObserver();
document.getElementById('cards-tbody').addEventListener('click', setCursorByClick);
buildPage(state.ui.currentPage || 0);
// After restore, if cursor exists, scroll it into view
if (state.ui.cursorIndex >= 0) renderCursor();
checkPriorSession();
</script>
</body>
</html>"""
    return html


def main(argv=None):
    args = parse_args(argv)

    if not args.candidates.exists():
        print(f"candidates_index.json not found: {args.candidates}", file=sys.stderr)
        sys.exit(20)

    with args.candidates.open(encoding="utf-8") as fh:
        data = json.load(fh)

    if "cards" not in data or "schema_version" not in data:
        print("candidates_index.json missing required fields (cards, schema_version)", file=sys.stderr)
        sys.exit(20)

    cards = sort_cards(data["cards"], args.sort_by)

    html = build_html(cards, data, args.page_size)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(
        f"Gallery written to {args.output} "
        f"({len(cards)} cards, page_size={args.page_size}, sort={args.sort_by})"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
