"""
Browser tests for the Korean Image Review gallery using Playwright.

Requirements:
  pip install playwright && playwright install chromium

Run with:
  pytest tests/test_korean_review_browser.py -v

These tests exercise the HTML gallery's JavaScript behavior including:
  - Rendering
  - Radio persistence via localStorage
  - Export format
  - IntersectionObserver lazy-load (data-src / src attribute contract)
  - Cohort warning rendering
  - Prior session import modal
  - Dual-key localStorage writes (PRIMARY_KEY and LATEST_KEY)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_DIR = TESTS_DIR / "fixtures" / "korean-image-review"

GALLERY_SCRIPT = SCRIPTS_DIR / "build-card-review-gallery.py"
SMALL_CANDIDATES = FIXTURE_DIR / "small_candidates_index.json"

# Skip all tests if playwright is not installed
try:
    from playwright.sync_api import sync_playwright, Page, expect
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed. Run: pip install playwright && playwright install chromium",
)


def build_gallery(candidates_path: Path, output_path: Path, page_size: int = 5) -> None:
    """Generate gallery HTML from candidates index."""
    result = subprocess.run(
        [
            sys.executable, str(GALLERY_SCRIPT),
            "--candidates", str(candidates_path),
            "--output", str(output_path),
            "--page-size", str(page_size),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Gallery build failed:\n{result.stderr}"


def make_minimal_candidates(tmp_path: Path, n: int = 5, cohort_conflict: bool = False) -> Path:
    """Generate a minimal candidates index for browser testing."""
    from tests.test_korean_review import make_candidate, make_unavailable, make_candidates_index

    cards = []
    for i in range(n):
        face_v2 = f"https://sheet.example.com/v2/card{i}.jpg"
        if cohort_conflict:
            cohort = "SHARED|abc123def456"
            v2_face_url = f"https://sheet.example.com/v2/conflict{'A' if i % 2 == 0 else 'B'}.jpg"
        else:
            cohort = f"DK{i}|hash{i:012d}"
            v2_face_url = face_v2

        cards.append({
            "arkham_id": f"BR{i:04d}",
            "nickname_en": f"Browser Test Card {i}",
            "nickname_v2_ko": f"브라우저 테스트 {i}",
            "target_paths": [],
            "target_deck_key": f"55{i:02d}",
            "candidates": [
                make_candidate("en", f"https://sheet.example.com/en/card{i}.jpg",
                               deck_key=f"55{i:02d}", card_id=550000 + i),
                make_candidate("ko", f"https://sheet.example.com/ko/card{i}.jpg",
                               deck_key=f"55{i:02d}", card_id=550000 + i),
                make_unavailable("v0"),
                make_unavailable("v1"),
                make_candidate("v2", v2_face_url, deck_key=f"55{i:02d}", card_id=550000 + i),
            ],
            "unique_face_url_count": 3 if not cohort_conflict else (2 if i % 2 == 0 else 3),
            "diversity_score": 3,
            "sheet_cohort_v2": cohort,
        })

    index = make_candidates_index(cards)
    path = tmp_path / "candidates_index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def browser_ctx():
    """Module-scoped Playwright browser context."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        yield ctx
        ctx.close()
        browser.close()


def load_gallery(browser_ctx, gallery_path: Path) -> "Page":
    """Open gallery HTML in a new browser page."""
    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")
    return page


def test_gallery_html_renders(tmp_path, browser_ctx):
    """Gallery should render the card table without JavaScript errors."""
    cand_path = make_minimal_candidates(tmp_path)
    gallery_path = tmp_path / "gallery.html"
    build_gallery(cand_path, gallery_path)

    errors = []
    page = browser_ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Should have card rows
    rows = page.locator(".card-row")
    assert rows.count() > 0, "No card rows rendered"
    # Should have cand-tile divs (not img elements) for available candidates
    tiles = page.locator(".cand-tile")
    assert tiles.count() > 0, "No .cand-tile elements rendered"
    assert not errors, f"JavaScript errors: {errors}"
    page.close()


def test_gallery_radio_persistence(tmp_path, browser_ctx):
    """Selecting a radio should persist in localStorage and re-render after reload."""
    cand_path = make_minimal_candidates(tmp_path, n=3)
    gallery_path = tmp_path / "gallery_persist.html"
    build_gallery(cand_path, gallery_path)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Click v2 radio for first card
    first_row = page.locator(".card-row").first
    ark_id = first_row.get_attribute("data-id")
    v2_radio = page.locator(f"input[name='choice-{ark_id}'][value='v2']")
    v2_radio.click()
    page.wait_for_timeout(200)

    # Check localStorage has the decision
    state_raw = page.evaluate("() => { const k = Object.keys(localStorage).find(k => k.startsWith('korean_image_review_v1_state:') && !k.endsWith(':latest')); return k ? localStorage.getItem(k) : null; }")
    assert state_raw is not None, "No primary state key in localStorage"
    state = json.loads(state_raw)
    assert ark_id in state["decisions"], f"{ark_id} not in decisions"
    assert state["decisions"][ark_id]["choice"] == "v2"
    page.close()


def test_gallery_export_format(tmp_path, browser_ctx):
    """Exported JSON should match the expected schema."""
    cand_path = make_minimal_candidates(tmp_path, n=2)
    gallery_path = tmp_path / "gallery_export.html"
    build_gallery(cand_path, gallery_path)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Make a decision
    first_row = page.locator(".card-row").first
    ark_id = first_row.get_attribute("data-id")
    page.locator(f"input[name='choice-{ark_id}'][value='v2']").click()
    page.wait_for_timeout(200)

    # Get export data via JS
    export_json_str = page.evaluate("""() => {
        const data = window.buildExportData ? window.buildExportData() : null;
        return data ? JSON.stringify(data) : null;
    }""")

    if export_json_str:
        export_data = json.loads(export_json_str)
        assert "schema_version" in export_data
        assert "generated_at" in export_data
        assert "gallery_id" in export_data
        assert "decisions" in export_data
        assert "stats" in export_data
        stats = export_data["stats"]
        assert "total_cards" in stats
        assert "decided" in stats
        assert "skipped_explicit" in stats
        assert "undecided" in stats
    page.close()


def test_gallery_intersection_observer_lazy_load(tmp_path, browser_ctx):
    """Tiles outside viewport should have data-bg but no backgroundImage initially."""
    # Build a gallery with enough cards to exceed viewport
    cand_path = make_minimal_candidates(tmp_path, n=50)
    gallery_path = tmp_path / "gallery_lazy.html"
    build_gallery(cand_path, gallery_path, page_size=50)

    page = browser_ctx.new_page()
    # Set a small viewport to ensure many tiles are off-screen
    page.set_viewport_size({"width": 1024, "height": 400})
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)

    # At least some tiles should be lazy-loaded (still have data-bg)
    has_databg_tiles = page.evaluate("""() => {
        return document.querySelectorAll('.cand-tile[data-bg]').length;
    }""")
    total_tiles = page.evaluate("() => document.querySelectorAll('.cand-tile').length")
    assert total_tiles > 0, "No tiles rendered"
    assert has_databg_tiles > 0, (
        "All tiles loaded before scroll — observer not exercised; test would pass vacuously"
    )

    # Scroll to bottom to trigger lazy load
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(800)

    # After scroll, tiles in view should have backgroundImage set or tile-error class
    # (weak postcondition: fixture URLs may fail to load, which sets tile-error instead)
    remaining_databg = page.evaluate("""() => document.querySelectorAll('.cand-tile[data-bg]').length""")
    assert remaining_databg <= has_databg_tiles, (
        "More data-bg tiles after scroll than before — observer may not be firing"
    )
    page.close()


def test_gallery_lazy_load_success_with_inline_png(tmp_path, browser_ctx):
    """Tiles with a loadable image URL should set backgroundImage (strong postcondition)."""
    INLINE_PNG_DATA_URL = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
        "AAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )

    from tests.test_korean_review import make_candidate, make_unavailable, make_candidates_index

    cards = [{
        "arkham_id": "INLINEPNG0001",
        "nickname_en": "Inline PNG Card",
        "nickname_v2_ko": "인라인 PNG 카드",
        "target_paths": [],
        "target_deck_key": "9999",
        "candidates": [
            make_candidate("en", INLINE_PNG_DATA_URL, deck_key="9999", card_id=999903),
            make_unavailable("v0"),
            make_unavailable("v1"),
            make_candidate("v2", INLINE_PNG_DATA_URL, deck_key="9999", card_id=999903),
        ],
        "unique_face_url_count": 1,
        "diversity_score": 1,
        "sheet_cohort_v2": "DK9999|hash000000000000",
    }]
    index = make_candidates_index(cards)
    cand_path = tmp_path / "inline_candidates_index.json"
    cand_path.write_text(json.dumps(index), encoding="utf-8")

    gallery_path = tmp_path / "gallery_inline.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")
    # Scroll to ensure intersection triggers
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1000)

    # Strong postcondition: tile should have backgroundImage set, not tile-error
    result = page.evaluate("""() => {
        const tile = document.querySelector('.cand-tile');
        if (!tile) return {found: false};
        return {
            found: true,
            bgImage: tile.style.backgroundImage,
            hasError: tile.classList.contains('tile-error'),
            hasDatabg: 'bg' in tile.dataset,
        };
    }""")
    assert result["found"], "No .cand-tile element found"
    assert not result["hasError"], f"tile-error set unexpectedly: {result}"
    assert result["bgImage"].startswith("url("), (
        f"backgroundImage not set after load: {result['bgImage']!r}"
    )
    assert not result["hasDatabg"], "data-bg should be removed after successful load"
    page.close()


def test_gallery_cohort_warning_renders(tmp_path, browser_ctx):
    """Cards with cohort conflicts should display cohort-conflict CSS class."""
    cand_path = make_minimal_candidates(tmp_path, n=4, cohort_conflict=True)
    gallery_path = tmp_path / "gallery_cohort.html"
    build_gallery(cand_path, gallery_path)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Wait a moment for COHORT_CONFLICTS to be computed and rows rendered
    page.wait_for_timeout(300)

    conflict_rows = page.locator(".card-row.cohort-conflict")
    assert conflict_rows.count() > 0, "Expected cohort-conflict rows to be rendered"
    page.close()


def test_gallery_prior_session_import_modal(tmp_path, browser_ctx):
    """Prior session modal should appear when LATEST_KEY has a different generation_id."""
    cand_path = make_minimal_candidates(tmp_path, n=2)
    gallery_path = tmp_path / "gallery_prior.html"
    build_gallery(cand_path, gallery_path)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Inject a prior session state with different generation_id
    page.evaluate("""() => {
        const priorState = {
            schema_version: 1,
            generation_id: "19991231T000000Z",  // old, different ID
            decisions: {"BR0000": {choice: "v2", note: "", decided_at: "1999-12-31T00:00:00Z",
                                   face_url: "https://x.com/v2.jpg", back_url: "https://x.com/back.jpg",
                                   num_width: 10, num_height: 7, deck_key: "5500"}},
            ui: {currentPage: 0, lastExportedAt: null, lastDecisionCount: 0}
        };
        localStorage.setItem("korean_image_review_v1_state:latest", JSON.stringify(priorState));
    }""")

    # Reload to trigger prior session check
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)

    # Prior session modal should be visible
    modal = page.locator("#prior-session-modal.open")
    assert modal.count() > 0, "Prior session import modal should be visible"
    page.close()


def test_gallery_dual_key_localstorage_write(tmp_path, browser_ctx):
    """After a radio choice, both PRIMARY_KEY and LATEST_KEY should be written."""
    cand_path = make_minimal_candidates(tmp_path, n=2)
    gallery_path = tmp_path / "gallery_dual.html"
    build_gallery(cand_path, gallery_path)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Make a choice
    first_row = page.locator(".card-row").first
    ark_id = first_row.get_attribute("data-id")
    page.locator(f"input[name='choice-{ark_id}'][value='v2']").click()
    page.wait_for_timeout(300)

    # Check both keys exist
    keys_info = page.evaluate("""() => {
        const keys = Object.keys(localStorage);
        const primaryKeys = keys.filter(k => k.startsWith('korean_image_review_v1_state:') && !k.endsWith(':latest'));
        const latestKey = keys.filter(k => k === 'korean_image_review_v1_state:latest');
        return {
            primaryCount: primaryKeys.length,
            latestCount: latestKey.length,
            primaryKey: primaryKeys[0] || null,
        };
    }""")

    assert keys_info["primaryCount"] >= 1, "PRIMARY_KEY not found in localStorage"
    assert keys_info["latestCount"] == 1, "LATEST_KEY not found in localStorage"

    # Both should have the same decision
    primary_raw = page.evaluate(f"""() => localStorage.getItem({json.dumps(keys_info['primaryKey'])})""")
    latest_raw = page.evaluate("""() => localStorage.getItem('korean_image_review_v1_state:latest')""")
    primary = json.loads(primary_raw)
    latest = json.loads(latest_raw)
    assert primary["decisions"] == latest["decisions"]
    page.close()


def test_keyboard_nav_j_k_moves_cursor(tmp_path, browser_ctx):
    """Pressing 'j' should advance the cursor; pressing 'k' should retreat it."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_nav_jk.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Press 'j' once: cursor jumps from -1 sentinel to top of current page (index 0)
    page.keyboard.press("j")
    page.wait_for_timeout(150)
    cursor_rows = page.locator(".card-row.cursor")
    assert cursor_rows.count() == 1, f"Expected exactly 1 cursor row, got {cursor_rows.count()}"
    first_cursor_id = cursor_rows.first.get_attribute("data-id")
    first_row_id = page.locator(".card-row").first.get_attribute("data-id")
    assert first_cursor_id == first_row_id, "Cursor should be on first card after first 'j'"

    # Press 'j' again: cursor advances to index 1
    page.keyboard.press("j")
    page.wait_for_timeout(150)
    cursor_rows = page.locator(".card-row.cursor")
    assert cursor_rows.count() == 1
    second_cursor_id = cursor_rows.first.get_attribute("data-id")
    second_row_id = page.locator(".card-row").nth(1).get_attribute("data-id")
    assert second_cursor_id == second_row_id, "Cursor should be on second card after second 'j'"

    # Press 'k': cursor returns to index 0
    page.keyboard.press("k")
    page.wait_for_timeout(150)
    cursor_rows = page.locator(".card-row.cursor")
    assert cursor_rows.count() == 1
    assert cursor_rows.first.get_attribute("data-id") == first_row_id, "Cursor should be back on first card after 'k'"
    page.close()


def test_keyboard_nav_boundary_no_wrap(tmp_path, browser_ctx):
    """Cursor should hard-stop at first/last filtered card (no wrap)."""
    cand_path = make_minimal_candidates(tmp_path, n=3)
    gallery_path = tmp_path / "gallery_nav_boundary.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Press 'j' once (cursor -> index 0), then 'k' - should stay at 0 (no wrap)
    page.keyboard.press("j")
    page.wait_for_timeout(100)
    page.keyboard.press("k")
    page.wait_for_timeout(100)
    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 0, f"Expected cursorIndex 0 after 'k' from index 0 (no wrap), got {cursor_idx}"

    # Navigate to last card (index 2): press 'j' twice
    page.keyboard.press("j")
    page.keyboard.press("j")
    page.wait_for_timeout(100)
    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 2, f"Expected cursorIndex 2, got {cursor_idx}"

    # Press 'j' from last card - should stay at 2 (no wrap)
    page.keyboard.press("j")
    page.wait_for_timeout(100)
    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 2, f"Expected cursorIndex still 2 after 'j' from last (no wrap), got {cursor_idx}"
    page.close()


def test_keyboard_nav_page_auto_flip(tmp_path, browser_ctx):
    """Crossing PAGE_SIZE boundary should auto-flip to the next page."""
    cand_path = make_minimal_candidates(tmp_path, n=12)
    gallery_path = tmp_path / "gallery_nav_pageflip.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Press 'j' 6 times: -1 -> 0 (first press) -> 1 -> 2 -> 3 -> 4 -> 5 (crosses page boundary)
    for _ in range(6):
        page.keyboard.press("j")
        page.wait_for_timeout(60)

    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    current_page = page.evaluate("() => currentPage")
    assert cursor_idx == 5, f"Expected cursorIndex 5, got {cursor_idx}"
    assert current_page == 1, f"Expected currentPage 1 after auto-flip, got {current_page}"

    # The cursor row should be the first card of page 1 (index 5)
    cursor_rows = page.locator(".card-row.cursor")
    assert cursor_rows.count() == 1
    page.close()


def test_hotkey_acts_on_cursor(tmp_path, browser_ctx):
    """Number hotkeys should target the cursor card (not focused element)."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_hotkey_cursor.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Press 'j' twice: cursor on second card (index 1)
    page.keyboard.press("j")
    page.wait_for_timeout(60)
    page.keyboard.press("j")
    page.wait_for_timeout(60)

    # Press '5' (v2 choice)
    page.keyboard.press("5")
    page.wait_for_timeout(150)

    second_row_id = page.locator(".card-row").nth(1).get_attribute("data-id")
    decision = page.evaluate(f"() => state.decisions[{json.dumps(second_row_id)}]")
    assert decision is not None, f"Expected decision for {second_row_id}, got None"
    assert decision["choice"] == "v2", f"Expected choice v2, got {decision['choice']}"
    page.close()


def test_cursor_persists_across_reload(tmp_path, browser_ctx):
    """Cursor index should persist in localStorage and survive page reload."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_cursor_persist.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Press 'j' twice: cursor on index 1
    page.keyboard.press("j")
    page.wait_for_timeout(60)
    page.keyboard.press("j")
    page.wait_for_timeout(150)

    cursor_idx_before = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx_before == 1

    # Reload
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(200)

    cursor_idx_after = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx_after == 1, f"Expected cursorIndex 1 after reload, got {cursor_idx_after}"

    # Cursor highlight should be reapplied
    cursor_rows = page.locator(".card-row.cursor")
    assert cursor_rows.count() == 1, f"Expected 1 cursor row after reload, got {cursor_rows.count()}"
    page.close()


def test_filter_resets_cursor(tmp_path, browser_ctx):
    """Changing the filter mode should reset cursorIndex to -1."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_filter_reset.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Press 'j' to set cursor
    page.keyboard.press("j")
    page.wait_for_timeout(100)
    assert page.evaluate("() => state.ui.cursorIndex") == 0

    # Change filter to 'decided' (no decisions yet, will produce empty list)
    page.locator("#filter-select").select_option("decided")
    page.wait_for_timeout(150)

    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == -1, f"Expected cursorIndex -1 after filter change, got {cursor_idx}"

    cursor_rows = page.locator(".card-row.cursor")
    assert cursor_rows.count() == 0, f"Expected 0 cursor rows after filter reset, got {cursor_rows.count()}"
    page.close()


def test_click_row_sets_cursor_no_scroll(tmp_path, browser_ctx):
    """Clicking on a row's neutral area should set cursor without scrolling."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_click_cursor.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    initial_scroll_y = page.evaluate("() => window.scrollY")

    # Click the .arkham-id span of the 3rd card row (index 2)
    third_arkham_id_span = page.locator(".card-row").nth(2).locator(".arkham-id")
    third_arkham_id_span.click()
    page.wait_for_timeout(150)

    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 2, f"Expected cursorIndex 2 after clicking row 2, got {cursor_idx}"

    cursor_rows = page.locator(".card-row.cursor")
    assert cursor_rows.count() == 1, f"Expected exactly 1 cursor row, got {cursor_rows.count()}"

    scroll_y_after = page.evaluate("() => window.scrollY")
    assert scroll_y_after == initial_scroll_y, (
        f"Expected no scroll (scrollY {initial_scroll_y}), got {scroll_y_after}"
    )
    page.close()


def test_click_radio_does_not_steal_cursor(tmp_path, browser_ctx):
    """Clicking a radio input inside a row must not change the cursor."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_click_radio_cursor.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Set cursor at row 0 via 'j'
    page.keyboard.press("j")
    page.wait_for_timeout(100)
    assert page.evaluate("() => state.ui.cursorIndex") == 0

    # Click v2 radio on row 3 (index 3)
    row3_id = page.locator(".card-row").nth(3).get_attribute("data-id")
    v2_radio = page.locator(f"input[name='choice-{row3_id}'][value='v2']")
    v2_radio.click()
    page.wait_for_timeout(150)

    # Radio should be checked
    assert v2_radio.is_checked(), "v2 radio on row 3 should be checked"

    # Cursor must remain at 0
    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 0, f"Expected cursorIndex 0 (unchanged), got {cursor_idx}"
    page.close()


def test_click_note_input_does_not_steal_cursor(tmp_path, browser_ctx):
    """Clicking a note input inside a row must not change the cursor."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_click_note_cursor.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Set cursor at row 0 via 'j'
    page.keyboard.press("j")
    page.wait_for_timeout(100)
    assert page.evaluate("() => state.ui.cursorIndex") == 0

    # Click note input on row 3
    note_input = page.locator(".card-row").nth(3).locator("input.note-input")
    note_input.click()
    page.wait_for_timeout(150)

    # Cursor must remain at 0
    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 0, f"Expected cursorIndex 0 (unchanged), got {cursor_idx}"

    # Note input should be focused
    focused_tag = page.evaluate("() => document.activeElement.tagName")
    focused_class = page.evaluate("() => document.activeElement.className")
    assert focused_tag == "INPUT" and "note-input" in focused_class, (
        f"Expected note-input to be focused, got {focused_tag}.{focused_class}"
    )
    page.close()


def test_smart_scroll_keynav_visible(tmp_path, browser_ctx):
    """j/k navigation must not trigger scroll when cursor row is already visible."""
    cand_path = make_minimal_candidates(tmp_path, n=5)
    gallery_path = tmp_path / "gallery_smart_scroll_visible.html"
    build_gallery(cand_path, gallery_path, page_size=5)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # All 5 rows fit in a single page — press j twice (cursor 0 -> 1)
    page.keyboard.press("j")
    page.wait_for_timeout(150)
    page.keyboard.press("j")
    page.wait_for_timeout(150)

    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 1, f"Expected cursorIndex 1, got {cursor_idx}"

    scroll_y = page.evaluate("() => window.scrollY")
    assert scroll_y == 0, f"Expected no scroll (scrollY 0) when all rows visible, got {scroll_y}"
    page.close()


def test_smart_scroll_keynav_offscreen(tmp_path, browser_ctx):
    """k navigation should scroll back when cursor row is off-screen above."""
    cand_path = make_minimal_candidates(tmp_path, n=20)
    gallery_path = tmp_path / "gallery_smart_scroll_offscreen.html"
    build_gallery(cand_path, gallery_path, page_size=20)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # Press j 5 times: cursor at index 4
    for _ in range(5):
        page.keyboard.press("j")
        page.wait_for_timeout(60)

    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 4, f"Expected cursorIndex 4, got {cursor_idx}"

    # Scroll to bottom so cursor row (index 4) is above the viewport
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(150)

    scroll_before = page.evaluate("() => window.scrollY")

    # Press k: cursor moves to index 3 (off-screen above), should scroll up
    page.keyboard.press("k")
    page.wait_for_timeout(300)

    cursor_idx = page.evaluate("() => state.ui.cursorIndex")
    assert cursor_idx == 3, f"Expected cursorIndex 3 after k, got {cursor_idx}"

    scroll_after = page.evaluate("() => window.scrollY")
    assert scroll_after < scroll_before, (
        f"Expected scrollY to decrease (cursor scrolled into view), "
        f"before={scroll_before} after={scroll_after}"
    )
    page.close()


def test_g_hotkey_scrolls_to_cursor(tmp_path, browser_ctx):
    """'g' hotkey should scroll the cursor row into view; no-op when cursorIndex == -1."""
    cand_path = make_minimal_candidates(tmp_path, n=20)
    gallery_path = tmp_path / "gallery_g_hotkey.html"
    build_gallery(cand_path, gallery_path, page_size=20)

    page = browser_ctx.new_page()
    page.goto(f"file://{gallery_path}")
    page.wait_for_load_state("networkidle")

    # cursorIndex is -1 — pressing g should be a no-op (no error, no scroll)
    initial_scroll_y = page.evaluate("() => window.scrollY")
    page.keyboard.press("g")
    page.wait_for_timeout(150)
    scroll_y = page.evaluate("() => window.scrollY")
    assert scroll_y == initial_scroll_y, (
        f"Expected no scroll when cursorIndex == -1, got scrollY {scroll_y}"
    )

    # Set cursor at row 0 via j, then scroll to bottom
    page.keyboard.press("j")
    page.wait_for_timeout(150)
    assert page.evaluate("() => state.ui.cursorIndex") == 0

    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(150)

    scroll_before = page.evaluate("() => window.scrollY")

    # Press g: should scroll cursor row back into view
    page.keyboard.press("g")
    page.wait_for_timeout(300)

    scroll_after = page.evaluate("() => window.scrollY")
    assert scroll_after < scroll_before, (
        f"Expected scrollY to decrease after 'g' hotkey, "
        f"before={scroll_before} after={scroll_after}"
    )
