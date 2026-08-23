# -*- coding: utf-8 -*-
"""kz_checkers.py -- the independent verifiers (design §5.5, §5.6, §9).

WHAT THIS SUITE IS FOR, AND WHY IT USES NO CORPUS
    W1/W2 is the single highest-value automation in the design, and as previously
    specified it was the ONLY tolerance-bearing predicate in §4.1 whose evidence
    was a 2.1 GB gitignored tree. §5.9 marks corpus-dependent tests `needs_golden`
    and skips them when the manifest's inputs are absent -- so on any machine
    without the corpus (CI, a second machine, this one after a disk loss) the
    checker had ZERO passing evidence while every other threshold in the document
    had a generated fault.

    Every case below except the last generates its own pixels. The corpus case is
    a `needs_golden` EXTRA and is deliberately demoted from primary evidence to
    corroboration: it is the only one of the set that can silently vanish.

THE FOURTH W1 CASE IS THE ONE THAT EARNS ITS PLACE
    The first three each declare ONE band, and on a one-band selection the
    ink-weighted median band height IS that band's height -- so the band-height
    filter is satisfied trivially and is VACUOUS in all three. Delete the filter
    and all three stay green. Only `band-height`, which declares two, notices.

THE swap/ FIXTURE LIVES HERE, NOT IN THE kz_decide.py TREE
    §4.4: "The previously-listed `swap/` class maps to no rule: the anti-swap
    invariant is a `kz_checkers.py` artifact check reported as 67 (§5.6), not one
    of the ten. It moves to `test_koreanize_checkers.py`, where it belongs."
    It is `icon_cases()["swap"]`, exercised by
    `test_icon_swap_makes_both_declarations_wrong_at_once`.
"""

import json
import os
import subprocess
import sys

import pytest

import kz_common as kc
import kz_checkers as kx

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKERS_PY = os.path.join(kc.PACKAGE_DIR, "kz_checkers.py")


# ---------------------------------------------------------------------------
# 1. Every --selftest fault as a pytest case (§2 item 40)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fault", kx.FAULTS)
def test_selftest_fault_passes(fault):
    """Each named fault, in-process, so a failure names the predicate."""
    assert kx.selftest(fault=fault, verbose=False) == []


def test_selftest_faults_are_total_over_the_predicates():
    """A predicate with no named fault is a predicate nobody runs.

    The list is asserted LITERALLY rather than derived, for the reason §4.4 gives
    the rule->fixture map: a set discovered from the code under test grows and
    shrinks silently with it.
    """
    assert kx.FAULTS == ("tokenizer", "jongseong", "icons", "w1", "w2")


def test_selftest_runs_as_a_subprocess_and_exits_zero():
    """`kz_checkers.py --selftest` is what `koreanize.sh selftest` invokes, so the
    CLI path is exercised and not only the function."""
    proc = subprocess.run([sys.executable, CHECKERS_PY, "--selftest"],
                          capture_output=True, text=True)
    assert proc.returncode == kc.EXIT_OK, proc.stdout + proc.stderr
    for fault in kx.FAULTS:
        assert fault in proc.stdout


def test_unknown_fault_refuses_at_usage():
    with pytest.raises(kc.KzRefusal) as excinfo:
        kx.selftest(fault="no-such-fault", verbose=False)
    assert excinfo.value.code == kc.EXIT_USAGE


# ---------------------------------------------------------------------------
# 2. W1 -- the four synthetic cases (§5.5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(kx.w1_cases()))
def test_w1_case(name):
    """27 px fires, 40 px does not, a narrowed window turns the negative into a
    positive, and the full-width plate is discarded."""
    case = kx.w1_cases()[name]
    hits = kx.run_w1_case(case)
    assert bool(hits) == case["fires"], case["why"]


def test_w1_positive_is_the_recorded_27px_clearance():
    """The reference figure, not an arbitrary one: 71006's tightest text band
    leaves 27 px, and `window_margin_px` is build-masks.py's own COL_GAP."""
    hits = kx.run_w1_case(kx.w1_cases()["positive"])
    assert len(hits) == 1
    hit = hits[0]
    assert hit["right_margin"] == 27
    assert hit["window_margin_px"] == kx.COL_GAP == 34
    # The hit must be keyable against the lock's residual baseline.
    assert kx.baseline_key(hit) == ("W1", kx.SYNTH_GROUP, "body", kx.SYNTH_FACE)
    assert "27 px" in hit["detail"]


def test_w1_negative_and_sensitivity_share_one_slice():
    """The sensitivity case must differ from the negative ONLY in the declared
    window -- otherwise it proves nothing about whether the predicate reads it."""
    cases = kx.w1_cases()
    assert cases["negative"]["bands"] == cases["sensitivity"]["bands"]
    assert cases["negative"]["window"] != cases["sensitivity"]["window"]
    assert kx.run_w1_case(cases["negative"]) == []
    assert kx.run_w1_case(cases["sensitivity"]) != []


def test_w1_band_height_filter_is_not_vacuous():
    """THE case that catches the filter's omission with no corpus present.

    With the filter the two-band slice must not fire; without it, the full-width
    plate at 3x text height fires with zero trailing clearance. If this ever
    reports the same answer both ways, the filter has stopped doing anything and
    W1 is measuring the plate instead of the text.
    """
    case = kx.w1_cases()["band-height"]
    assert kx.run_w1_case(case, filtered=True) == []
    unfiltered = kx.run_w1_case(case, filtered=False)
    assert unfiltered, "the case cannot catch an omitted band-height filter"
    # It fires on the PLATE -- the tall band -- and not on the text band.
    assert any((b[1] - b[0] + 1) == 90 for b in unfiltered)


def test_w1_band_height_filter_selects_the_text_band():
    """The filter's mechanism, stated directly: the ink-weighted median lands on
    the TEXT band's height because the sparse plate carries less ink, which is
    the same relation that holds on the real corpus where nine text bands
    outweigh one plate."""
    case = kx.w1_cases()["band-height"]
    ink = kx.ink_mask(kx.synth_slice(kx.SYNTH_SIZE, case["bands"]))
    bands, en_ink_h = kx.measure_bands(ink, case["window"])
    assert len(bands) == 2
    assert en_ink_h == 30
    kept = kx.text_bands(bands, en_ink_h)
    assert len(kept) == 1 and (kept[0][1] - kept[0][0] + 1) == 30


def test_w1_measures_trailing_edges_only():
    """"All four sides" is unsatisfiable by construction (§5.5): body text is
    left-aligned against the window, so the LEADING margin is a property of the
    typesetting. A band flush against the window's LEFT edge with ample trailing
    clearance must not fire."""
    left = kx.SYNTH_WINDOW[0]
    width = kx.SYNTH_WINDOW[2] - kx.SYNTH_WINDOW[0]
    flush_left = dict(top=60, height=30, left=left, right=left + width - 1 - 60,
                      block=(6, 30), period=(8, 30))
    ink = kx.ink_mask(kx.synth_slice(kx.SYNTH_SIZE, [flush_left]))
    bands, en_ink_h = kx.measure_bands(ink, kx.SYNTH_WINDOW)
    assert bands[0][2] == 0, "the band is not actually flush against the left edge"
    assert kx.w1_window_margin(ink, {"body": kx.SYNTH_WINDOW}, kx.COL_GAP,
                               kx.SYNTH_GROUP, kx.SYNTH_FACE) == []


# ---------------------------------------------------------------------------
# 3. W2 -- the two synthetic cases (§5.5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(kx.w2_cases()))
def test_w2_case(name):
    case = kx.w2_cases()[name]
    hits = kx.run_w2_case(case)
    assert bool(hits) == case["fires"], case["why"]


def test_w2_names_the_band_and_the_overhang():
    case = kx.w2_cases()["overhang"]
    hits = kx.run_w2_case(case)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["overhang_right"] == 12
    assert hit["overhang_px"] > 0
    assert hit["band"] == [60, 89, 20, 519]
    assert kx.baseline_key(hit) == ("W2", kx.SYNTH_GROUP, "body", kx.SYNTH_FACE)


def test_w2_attributes_a_bottom_overhang_to_the_bottom():
    """The two projections are not independent: a right-edge tail makes every row
    impure and a bottom overhang makes every column impure. Attributing to the
    tighter axis is what stops a 12 px tail being reported as "bottom 30"."""
    import numpy as np
    case = dict(kx.w2_cases()["overhang"])
    gray = np.asarray(kx.synth_slice(kx.SYNTH_SIZE, case["bands"]))
    clear = np.zeros((kx.SYNTH_SIZE[1], kx.SYNTH_SIZE[0]), dtype=bool)
    clear[50:82, 10:541] = True          # CLEAR stops 8 rows above the band's foot
    hits = kx.w2_line_containment(gray, clear, {"body": case["window"]},
                                  kx.SYNTH_GROUP, kx.SYNTH_FACE)
    assert len(hits) == 1
    assert hits[0]["overhang_bottom"] == 8
    assert hits[0]["overhang_right"] == 0


def test_w2_is_given_a_declared_mask_never_a_derived_one():
    """§5.5 property 1: asserting containment against `regions_slice_coords` --
    the mask's own padded output rects -- would be circular and would be detector
    attempt #4. The signature is the evidence: `clear` is a parameter."""
    import inspect
    params = list(inspect.signature(kx.w2_line_containment).parameters)
    assert params[:3] == ["gray", "clear", "windows"]


# ---------------------------------------------------------------------------
# 4. The residual baseline (§5.5) -- containment, never equality
# ---------------------------------------------------------------------------

def _hit(check="W1", group="Act/front", window="body", face="71006"):
    return {"check": check, "group": group, "window_name": window,
            "face": face, "detail": "%s %s %s %s" % (check, group, window, face)}


def test_baselined_hit_passes_and_a_novel_one_fails():
    baseline = [{"check": "W1", "group": "Act/front", "window_name": "body",
                 "face": "71006"}]
    assert kx.mask_residual_check([_hit()], baseline)["status"] == "pass"
    novel = kx.mask_residual_check([_hit(face="71099")], baseline)
    assert novel["status"] == "fail"
    assert novel["exit_on_fail"] == kc.EXIT_MASK_RESIDUAL == 23
    assert novel["tolerance"] == "mask-residual"


def test_a_superset_of_the_baseline_is_the_detector_working():
    """mask-coverage-finding.md:26 records the two known hits as a RATE over the
    26 faces looked at, of 88 -- "which is a rate, not a bound". So the baselined
    hits must still pass when new ones appear beside them, and only the new ones
    fail: a design that scored a successful first sweep as a defect would be
    scoring the detector working as the checker broken."""
    baseline = [{"check": "W1", "group": "Act/front", "window_name": "body",
                 "face": f} for f in ("71006", "71005")]
    check = kx.mask_residual_check(
        [_hit(face="71006"), _hit(face="71005"), _hit(face="71099")], baseline)
    assert check["reproduced_baseline"] == 2
    assert check["novel"] == 1
    assert check["status"] == "fail"
    assert len(check["detail"]) == 1


def test_accept_mask_residual_accepts_a_novel_hit():
    baseline = []
    assert kx.mask_residual_check([_hit()], baseline, accepted=True)["status"] == "pass"


def test_the_tolerance_row_names_this_predicate_and_its_exit():
    row = kc.row_by_flag("--accept-mask-residual")
    assert row is not None
    assert row.exit_code == kc.EXIT_MASK_RESIDUAL
    # OWNED BY kz_mask.py, activated at step 16 -- built here, which is why the
    # row is `pending` until that module exists (§4.1's phase axis).
    assert row.module == "kz_mask.py"
    assert row.phase == "v1.x"


# ---------------------------------------------------------------------------
# 5. The S4 anti-swap invariant -- the swap/ fixture's new home (§5.6, §4.4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(kx.icon_cases()))
def test_icon_case(name):
    case = kx.icon_cases()[name]
    assert len(kx.check_icon_declarations(
        case["declared"], case["measured"])) == case["declaration_findings"]
    assert len(kx.check_icon_separation(
        case["declared"])) == case["separation_findings"]


def test_icon_swap_makes_both_declarations_wrong_at_once():
    """THE `swap/` FIXTURE. The record's verdict is why this check exists:

        "the preflight's `miss_icons` probe asserts INK, not identity, and
         `tokenise`/`para_words`/`seg_advance`/`draw_layout` are all
         value-transparent, so a human `accepted` verdict on 2026-08-18 could not
         have caught it."

    Four findings and not two: both tokens are wrong in BOTH dimensions, which is
    the property that makes a swap detectable when every downstream stage is
    value-transparent.
    """
    case = kx.icon_cases()["swap"]
    findings = kx.check_icon_declarations(case["declared"], case["measured"])
    assert len(findings) == 4
    assert sum(1 for f in findings if "ink_fill" in f) == 2
    assert sum(1 for f in findings if "advance_em" in f) == 2
    for token in ("tablet", "elder_thing"):
        assert any(f.startswith(token + ":") for f in findings)


def test_the_reference_pair_clears_the_separation_margin_with_room():
    """The margin is not tuned to the one pair the project has measured: a bound
    set AT the reference distance would pass that pair and nothing else."""
    fill_a, adv_a = kx.ICON_REFERENCE["elder_thing"]
    fill_b, adv_b = kx.ICON_REFERENCE["tablet"]
    distance = ((fill_a - fill_b) ** 2 + (adv_a - adv_b) ** 2) ** 0.5
    assert distance == pytest.approx(0.2102, abs=5e-4)
    assert distance > kx.ICON_SEPARATION_MIN * 4


def test_an_undeclared_or_unmeasured_token_is_caught_in_both_directions():
    assert kx.check_icon_declarations({"tablet": (0.66, 0.826)}, {})
    assert kx.check_icon_declarations({}, {"tablet": (0.66, 0.826)})


# ---------------------------------------------------------------------------
# 6. The independent tokenizer (§9)
# ---------------------------------------------------------------------------

def test_tokenizer_uses_no_regex():
    """`verify-a4.py:4-7`: "A3 validates with regex multisets; this file scans
    character by character with a hand-written tokenizer, so a bug in one regex
    cannot pass both." A checker that shares an implementation with the thing it
    checks establishes nothing, so the absence of `re` is the property."""
    with open(CHECKERS_PY, "r", encoding="utf-8") as handle:
        source = handle.read()
    head = source.split("# 4. W1 / W2")[0]
    assert "import re" not in head
    assert "re.compile" not in source


@pytest.mark.parametrize("text,icons,refs,tags", [
    ("[skull]: -X", ["skull"], 0, {}),
    ("[[Guest]] asset", [], 1, {}),
    ("<b>Forced</b> - do it", [], 0, {"b": 2}),
    ("[combat][agility] <i>x</i> [[A]] [[B]]", ["agility", "combat"], 2, {"i": 2}),
    # `[[Trait]]` must not tokenize as an icon named `[Trait`.
    ("[[Elite]]", [], 1, {}),
    # A mixed-case body is not an icon token.
    ("[Skull]", [], 0, {}),
    ("", [], 0, {}),
])
def test_scan(text, icons, refs, tags):
    got_icons, got_refs, got_tags = kx.scan(text)
    assert got_icons == icons
    assert got_refs == refs
    assert {k: v for k, v in got_tags.items() if v} == tags


def test_markup_delta_catches_a_substitution_a_count_cannot_see():
    assert kx.markup_delta("[combat] x", "[agility] x")
    assert kx.markup_delta("[[Guest]] x", "x")
    assert kx.markup_delta("<b>x</b>", "x")
    assert kx.markup_delta("[combat] x", "[combat] 가") == []


def test_residual_english_ignores_constructs_that_stay_latin():
    """Reported, never fatal (verify-a4.py:20): proper nouns like "E. Zann" and
    cross-references are legitimately Latin."""
    assert kx.latin_runs("[skull] <b>x</b> [[Guest]] 한글") == []
    assert kx.latin_runs("남은 English text") == ["English", "text"]


# ---------------------------------------------------------------------------
# 7. The jongseong audit (§5.7)
# ---------------------------------------------------------------------------

def test_the_recorded_defect_flags_and_its_repair_does_not():
    """`주요목적를` shipped five times, in the generator. 적 = U+C801, jongseong 1
    (ㄱ), so the object particle takes its consonant form 을."""
    sites, _skipped = kx.scan_particles("주요목적를 완료하십시오")
    flags = [s for s in sites if s["status"] == "flag"]
    assert len(flags) == 1
    assert flags[0]["pair"] == "object"
    assert flags[0]["expected"] == "cons"
    assert [s for s in kx.scan_particles("주요목적을 완료하십시오")[0]
            if s["status"] == "flag"] == []


def test_jongseong_arithmetic_is_exact():
    assert kx.jongseong("적") == 1
    assert kx.jongseong("가") == 0
    assert kx.jongseong("칼") == 8
    assert kx.expected_form("object", "가", "cons") == "vowel"
    assert kx.expected_form("object", "적", "cons") == "cons"


def test_the_rieul_carve_out_is_per_pair_and_never_global():
    """After ㄹ the 으로/로서 family takes the vowel form. It does NOT apply to
    을/를, 은/는, 이/가 or 과/와, and a global rule would corrupt all four."""
    assert kx.expected_form("instrumental", "칼", "vowel") == "vowel"
    assert kx.expected_form("object", "칼", "cons") == "cons"
    assert kx.expected_form("subject", "칼", "cons") == "cons"


@pytest.mark.parametrize("entry", kx.LEXICON)
def test_every_lexicon_entry_suppresses_its_own_word(entry):
    """Without the lexicon the bare jamo rule flags well over a hundred sites that
    are almost entirely correct Korean, and any fixer that trusted the raw rule
    would corrupt every one of them."""
    text = "%s 조사자" % entry["word"]
    with_lexicon = kx.scan_particles(text, use_lexicon=True)[0]
    without = kx.scan_particles(text, use_lexicon=False)[0]
    assert [s for s in with_lexicon if s["status"] == "flag"] == []
    assert [s for s in without if s["status"] == "flag"], (
        "%r is in the lexicon but the bare rule does not flag it -- the entry is "
        "dead weight and `lexicon_unused` would pin it forever" % entry["word"])


def test_the_lexicon_cannot_mask_a_different_pairs_defect():
    """A site is suppressed only if the FULL word occupies the text ending at the
    particle AND the entry's pair equals the site's pair."""
    sites = kx.scan_particles("있는를", use_lexicon=True)[0]
    assert any(s["status"] == "flag" for s in sites)


def test_the_eojeol_boundary_removes_a_candidate_before_it_is_a_site():
    """Unlike the lexicon this can never appear in the raw flag count -- it is
    what stops 어딘가에서 / 사이에 / 폭로하다 being counted at all."""
    sites, skipped = kx.scan_particles("어딘가에서 왔습니다")
    assert [s for s in sites if s["status"] == "flag"] == []
    assert skipped > 0


def test_lexicon_excluded_is_reported_so_an_omission_is_a_decision():
    audit = kx.audit_particles({"f": "한글"})
    assert audit["lexicon_excluded"]
    words = [e["word"] for e in audit["lexicon_excluded"]]
    assert "어딘가" in words


def test_audit_reports_hits_unused_and_boundary_suppressions():
    audit = kx.audit_particles({"a": "주요목적를 하고", "b": "있는 조사자"})
    assert len(audit["flags"]) == 1
    assert "있는" in audit["lexicon_hits"]
    assert "플레이" in audit["lexicon_unused"]


def test_the_module_has_no_fix_path():
    """`--fix` NEVER writes the artifact: it is produced only by re-running the
    generator. This module therefore only ever REPORTS -- routing a finding to a
    fix is `audit`(S7)'s job, and the fix lands in the generator."""
    proc = subprocess.run([sys.executable, CHECKERS_PY, "--help"],
                          capture_output=True, text=True)
    assert "--fix" not in proc.stdout


# ---------------------------------------------------------------------------
# 8. The `check` stage's seven checks
# ---------------------------------------------------------------------------

def _en(**over):
    card = {"code": "71001", "name": "The Gala", "text": "[skull] a [[Guest]]",
            "guid": "5d96e5", "card_id": 918000, "deck_key": "9180", "cell": 0}
    card.update(over)
    return {"cards": [card]}


def _ko(**over):
    card = {"arkham_id": "71001", "name": "축제", "text": "[skull] 어느 [[손님]]",
            "guid": "5d96e5", "card_id": 918000, "deck_key": "9180", "cell": 0}
    card.update(over)
    return {"cards": [card]}


def _by_id(checks):
    return {c["id"]: c for c in checks}


def test_a_faithful_translation_passes_every_check():
    checks, _en_index, _ko_index, _audit = kx.run_checks(_en(), _ko())
    for check in checks:
        assert check["status"] == "pass", check


@pytest.mark.parametrize("check_id,en,ko", [
    # CH1 -- an id present in English and absent from Korean. THE recorded
    # defect: "every arkham_id-driven loop silently drops one, and a
    # 'verify all 62' gate PASSES while an English card sits in an otherwise
    # Korean scenario."
    ("CH1", _en(), {"cards": []}),
    # CH2 -- a field non-empty in English and empty in Korean.
    ("CH2", _en(flavor="Once upon a time"), _ko()),
    # CH3 -- a token SUBSTITUTION, which keeps every count.
    ("CH3", _en(), _ko(text="[cultist] 어느 [[손님]]")),
    # CH4 -- an identity field moved by translation.
    ("CH4", _en(), _ko(guid="000000")),
    # CH5 -- the card_id arithmetic broken.
    ("CH5", _en(), _ko(card_id=918001)),
    # CH7 -- the jongseong defect.
    ("CH7", _en(), _ko(text="[skull] 주요목적를 [[손님]]")),
])
def test_each_check_fires_on_its_own_fault(check_id, en, ko):
    checks, _e, _k, _a = kx.run_checks(en, ko)
    by_id = _by_id(checks)
    assert by_id[check_id]["status"] == "fail", by_id[check_id]
    assert by_id[check_id]["detail"]
    assert by_id[check_id]["exit_on_fail"] == kc.EXIT_ARTIFACT


def test_ch2_fires_in_the_other_direction_too():
    """Empty in English and non-empty in Korean is a different defect from its
    converse -- an invented field rather than a dropped one."""
    checks, _e, _k, _a = kx.run_checks(_en(), _ko(flavor="옛날 옛적에"))
    assert _by_id(checks)["CH2"]["status"] == "fail"


def test_ch1_catches_an_invented_id_and_a_duplicated_one():
    checks, _e, _k, _a = kx.run_checks(_en(), {"cards": [
        _ko()["cards"][0], dict(_ko()["cards"][0], arkham_id="99999")]})
    detail = " ".join(_by_id(checks)["CH1"]["detail"])
    assert "99999" in detail
    doubled = {"cards": [_ko()["cards"][0], _ko()["cards"][0]]}
    checks, _e, _k, _a = kx.run_checks(_en(), doubled)
    assert "twice" in " ".join(_by_id(checks)["CH1"]["detail"])


def test_ch6_residual_english_is_reported_and_never_fatal():
    """The one informational check. A hard rule here would refuse correct
    translations, because proper nouns are legitimately Latin."""
    checks, _e, _k, _a = kx.run_checks(_en(), _ko(text="[skull] E. Zann 어느 [[손님]]"))
    ch6 = _by_id(checks)["CH6"]
    assert ch6["status"] == "pass"
    assert ch6["exit_on_fail"] is None
    assert ch6["informational"] is True
    assert ch6["detail"]


def test_run_checks_is_pure():
    """Being pure is what lets every case above run on a two-card synthetic corpus
    rather than on the 2.1 GB tree."""
    en, ko = _en(), _ko()
    before = (json.dumps(en, sort_keys=True), json.dumps(ko, sort_keys=True))
    kx.run_checks(en, ko)
    assert (json.dumps(en, sort_keys=True), json.dumps(ko, sort_keys=True)) == before


def test_a_missing_artifact_refuses_at_precondition():
    with pytest.raises(kc.KzRefusal) as excinfo:
        kx._load_json(os.path.join(TESTS_DIR, "no-such-file.json"), "card-text-ko.json")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


def test_unreadable_json_refuses_at_precondition(tmp_path):
    bad = tmp_path / "card-text-ko.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(kc.KzRefusal) as excinfo:
        kx._load_json(str(bad), "card-text-ko.json")
    assert excinfo.value.code == kc.EXIT_PRECONDITION


# ---------------------------------------------------------------------------
# 9. Tier discipline (§5.9, §4.4)
# ---------------------------------------------------------------------------

def test_kz_checkers_is_art_tier_and_not_in_the_stdlib_set():
    """§4.4 is explicit that the artifact half of containment lives here and NOT
    in the stdlib-tier `kz_verify.py`: mask-CLEAR containment is a numpy
    comparison over a decoded PNG and the anti-swap invariant re-measures font
    outlines, while `/usr/bin/python3` has PIL 10.4.0 and no numpy at all."""
    assert "kz_checkers.py" not in kc.STDLIB_TIER
    assert "kz_verify.py" in kc.STDLIB_TIER
    with open(CHECKERS_PY, "r", encoding="utf-8") as handle:
        first = handle.readline().strip()
    assert first == "#!/usr/bin/env python3"


def test_numpy_and_pil_are_imported_lazily():
    """The tokenizer and jongseong halves must stay importable on any
    interpreter, so the pixel imports live inside the functions that need them."""
    with open(CHECKERS_PY, "r", encoding="utf-8") as handle:
        source = handle.read()
    module_body = source.split("# 1. The independent tokenizer")[0]
    assert "import numpy" not in module_body
    assert "from PIL" not in module_body


def test_check_is_a_neutral_stage_and_declares_no_ai_contract():
    """`check` is one of the four NEUTRAL stages (§4.1): calling declare_ai here
    would assert it into a partition scenario.json does not put it in and refuse
    at exit 4 the moment a config is bound."""
    import kz_config as kz
    assert kx.STAGE == "check"
    assert kx.STAGE not in kz.AI_STAGE_MAP.values()
    assert kc.AI_CONTRACT.get("check") is None


# ---------------------------------------------------------------------------
# 10. The corpus corroboration -- an EXTRA, demoted on purpose (§5.5)
# ---------------------------------------------------------------------------

@pytest.mark.needs_golden
def test_corpus_containment_reproduces_the_two_known_hits(golden):
    """CORROBORATION, not the primary evidence. On the Midwinter fixture the
    reported hit set must CONTAIN the 71006 and 71005 hits recorded in
    `mask-coverage-finding.md`. It is demoted because it is the only case in this
    file that can silently vanish, and it is a superset test because
    mask-coverage-finding.md:26 records those two as a rate over the 26 faces
    looked at, of 88 -- so hits among the other 62 are the detector working."""
    manifest_path = os.path.join(kc.PACKAGE_DIR, "data", "golden",
                                 "midwinter.manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    baseline = ((manifest.get("mask") or {}).get("residual_baseline")) or []
    keys = {(e.get("check"), e.get("group"), e.get("window_name"), e.get("face"))
            for e in baseline}
    for face in ("71006", "71005"):
        assert any(k[3] == face for k in keys), (
            "the golden manifest's residual baseline does not carry %s" % face)
