#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `check` + the independent verifiers (design §5.5, §5.6, §9).

ART TIER (design §5.9): `#!/usr/bin/env python3`, NOT a member of
`kz_common.STDLIB_TIER`. It decodes PNGs with numpy and measures font outlines,
neither of which `/usr/bin/python3` can do -- that interpreter has PIL 10.4.0 and
no numpy at all. §4.4 is explicit that the artifact half of containment lives
here and emphatically not in the stdlib-tier `kz_verify.py`; naming the wrong
module there would make the tier boundary a comment rather than a constraint.

AUTHORED APART FROM EVERY PRODUCER, AND THAT PLACEMENT IS THE POINT
------------------------------------------------------------------
`verify-a4.py:4-7` states the rule this module exists to keep:

    "This is deliberately NOT a re-run of translate-a3.py's own check(). A3
     validates with regex multisets; this file scans character by character with
     a hand-written tokenizer, so a bug in one regex cannot pass both."

So the tokenizer below is hand-written and uses no regex, the jongseong
arithmetic is exact and uses no `unicodedata`, and the mask predicates are given
the DECLARED layout windows rather than the mask's own output rects. A checker
that shares an implementation with the thing it checks establishes nothing.

WHAT IS IN HERE, AND WHICH SECTION OF THE DESIGN OWNS IT
--------------------------------------------------------
  1. the independent tokenizer                       §9,   forked from verify-a4.py
  2. the 을/를 jongseong audit + false-positive lexicon §5.7, from check-particles.py
  3. the S4 anti-swap invariant                      §5.6
  4. W1/W2, the mask-coverage assertion              §5.5  (built here, ACTIVATED
                                                            in kz_mask.py at step 16)
  5. the `check` stage                               §1.2

`check` is one of the four NEUTRAL stages (§4.1): it neither requires AI nor is
forbidden it, so this module deliberately does NOT call `kc.declare_ai()` --
doing so would assert `check` into a partition `scenario.json` does not put it in
and refuse at exit 4 the moment a config is bound.

W1/W2 ARE BUILT HERE AND ACTIVATED LATER, WHICH IS NOT THE SAME AS UNTESTED
---------------------------------------------------------------------------
§5.5 makes the ordering explicit: the predicates are authored in step 9, and step
16 adds them to `kz_mask.py`'s gate set after the first full 88-face sweep has
been adjudicated. Every one of the six synthetic cases below runs NOW, on
generated images, with no corpus -- because the corpus is the one piece of
evidence that can silently vanish, and W1 was previously resting entirely on it.

EXIT CODES (§4.2)
   0  every check passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal
  13  precondition -- a missing input, an unreadable artifact
  23  a mask-coverage residual above the lock baseline (W1/W2)
  67  a produced artifact failed structural verification
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402

STAGE = "check"

# NOT declare_ai(). See the module docstring: `check` is neutral (§4.1).

FAULTS = ("tokenizer", "jongseong", "icons", "w1", "w2")


# ===========================================================================
# 1. The independent tokenizer -- hand-written, NO REGEX (§9, verify-a4.py:39)
# ===========================================================================
#
# A regex multiset and a character walk fail differently, which is the entire
# reason there are two. `translate-a3.py`'s own check() is the regex half; this
# is the other one, and it must never be refactored to share code with it.

COUNTED_TAGS = ("b", "i", "u")


def scan(text):
    """(icons, trait_refs, tag_counts) from one field, by character walk.

    icons      : sorted [token] names whose body is all lowercase/underscore ASCII
    trait_refs : count of [[...]] constructs
    tag_counts : {tag name: number of <tag> and </tag> occurrences}

    Lifted from `verify-a4.py:39`. The `[[` test precedes the `[` test because
    `[[Trait]]` would otherwise tokenize as an icon named `[Trait`.
    """
    text = text or ""
    icons = []
    trait_refs = 0
    tag_counts = {}
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "[":
            if i + 1 < n and text[i + 1] == "[":
                close = text.find("]]", i + 2)
                if close != -1:
                    trait_refs += 1
                    i = close + 2
                    continue
            close = text.find("]", i + 1)
            if close != -1:
                body = text[i + 1:close]
                if body and all(c.islower() or c == "_" for c in body) and body.isascii():
                    icons.append(body)
                i = close + 1
                continue
            i += 1
            continue
        if ch == "<":
            close = text.find(">", i + 1)
            if close != -1:
                body = text[i + 1:close]
                name = body[1:] if body.startswith("/") else body
                if name and name.isascii() and name.isalpha():
                    tag_counts[name.lower()] = tag_counts.get(name.lower(), 0) + 1
                i = close + 1
                continue
            i += 1
            continue
        i += 1
    return sorted(icons), trait_refs, tag_counts


def strip_constructs(text):
    """Remove the parts of a string allowed to stay Latin, for the residual scan."""
    text = text or ""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "[":
            close = (text.find("]]", i + 2) if text[i:i + 2] == "[["
                     else text.find("]", i + 1))
            if close != -1:
                i = close + (2 if text[i:i + 2] == "[[" else 1)
                continue
        if text[i] == "<":
            close = text.find(">", i + 1)
            if close != -1:
                i = close + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def latin_runs(text, minimum=3):
    """Runs of `minimum`+ Latin letters left in a Korean field.

    REPORTED, never fatal (verify-a4.py:20): proper nouns like "E. Zann" and
    cross-references like "(->R1)" are legitimately Latin, so a hard rule here
    would refuse correct translations.
    """
    runs = []
    current = ""
    for ch in strip_constructs(text):
        if ch.isascii() and ch.isalpha():
            current += ch
        else:
            if len(current) >= minimum:
                runs.append(current)
            current = ""
    if len(current) >= minimum:
        runs.append(current)
    return runs


def markup_delta(en_text, ko_text):
    """The multiset difference between one English field and its Korean pair.

    Returns [] when they agree. WHICH icon tokens, not just how many
    (verify-a4.py:22): a translation that turns [combat] into [agility] keeps the
    count and changes the card.
    """
    en_icons, en_refs, en_tags = scan(en_text)
    ko_icons, ko_refs, ko_tags = scan(ko_text)
    findings = []
    if en_icons != ko_icons:
        findings.append("icon tokens %r != %r" % (en_icons, ko_icons))
    if en_refs != ko_refs:
        findings.append("[[trait]] refs %d != %d" % (en_refs, ko_refs))
    for tag in COUNTED_TAGS:
        if en_tags.get(tag, 0) != ko_tags.get(tag, 0):
            findings.append("<%s> count %d != %d"
                            % (tag, en_tags.get(tag, 0), ko_tags.get(tag, 0)))
    return findings


# ===========================================================================
# 2. The 을/를 jongseong audit (§5.7, from check-particles.py)
# ===========================================================================
#
# Two things that must stay together or neither is trustworthy
# (check-particles.py:8-20):
#
#   1. the jongseong rule -- exact arithmetic deciding whether a Korean
#      object/topic/subject particle takes its consonant or its vowel form from
#      the final jamo of the preceding syllable; and
#   2. an EXPLICIT false-positive lexicon -- the Korean words whose own final
#      syllable happens to BE a particle character. Without it the bare jamo rule
#      flags well over a hundred sites that are almost entirely correct Korean,
#      and any fixer that trusted the raw rule would corrupt every one of them.
#
# THE DEFECT THIS EXISTS FOR IS IN THE GENERATOR, NOT IN THE JSON.
# `주요목적를` appeared five times, and the record's whole point (§5.6, §5.7) is
# that the fix belongs in `data/text/<slug>-ko.py` -- the thing that PRODUCES the
# JSON -- because fixing the JSON leaves the generator able to reproduce it. This
# module therefore only ever REPORTS; it has no --fix, by design.

HANGUL_START = 0xAC00
HANGUL_END = 0xD7A3

# name, consonant form, vowel form, what jongseong 8 (ㄹ) takes
PAIRS = (
    ("object",          "을",     "를",   "cons"),
    ("topic",           "은",     "는",   "cons"),
    ("subject",         "이",     "가",   "cons"),
    ("conjunction",     "과",     "와",   "cons"),
    ("instrumental_as", "으로서", "로서", "vowel"),   # longest first
    ("instrumental",    "으로",   "로",   "vowel"),
    ("alternative",     "이나",   "나",   "cons"),
    ("copular_and",     "이며",   "며",   "cons"),
    ("copular_excl",    "이야",   "야",   "cons"),
    ("comitative",      "이랑",   "랑",   "cons"),
    ("quotative_top",   "이란",   "란",   "cons"),
    ("quotative",       "이라",   "라",   "cons"),
    ("vocative_lit",    "이여",   "여",   "cons"),
)

# A site is suppressed only if the FULL word occupies the text ending at the
# particle AND the entry's pair equals the site's pair. It therefore cannot mask
# a defect elsewhere, and it cannot mask a defect of a DIFFERENT pair.
LEXICON = (
    {"word": "있는",   "pair": "topic",         "reason": "verb adnominal -는 (있다)"},
    {"word": "없는",   "pair": "topic",         "reason": "verb adnominal -는 (없다)"},
    {"word": "않는",   "pair": "topic",         "reason": "verb adnominal -는 (않다)"},
    {"word": "닿는",   "pair": "topic",         "reason": "verb adnominal -는 (닿다)"},
    {"word": "찾는",   "pair": "topic",         "reason": "verb adnominal -는 (찾다)"},
    {"word": "플레이", "pair": "subject",       "reason": "loanword ending in 이"},
    {"word": "전문가", "pair": "subject",       "reason": "Sino-Korean -가 agent suffix"},
    {"word": "모험가", "pair": "subject",       "reason": "Sino-Korean -가 agent suffix"},
    {"word": "작곡가", "pair": "subject",       "reason": "Sino-Korean -가 agent suffix"},
    {"word": "누군가", "pair": "subject",       "reason": "indefinite pronoun"},
    {"word": "무언가", "pair": "subject",       "reason": "indefinite pronoun"},
    {"word": "차이",   "pair": "subject",       "reason": "noun ending in 이"},
    {"word": "사이",   "pair": "subject",       "reason": "noun ending in 이"},
    {"word": "폭로",   "pair": "instrumental",  "reason": "noun ending in 로"},
    {"word": "골라",   "pair": "quotative",     "reason": "verb form 고르-+-아"},
    {"word": "데클란", "pair": "quotative_top", "reason": "proper noun (Declan) ending in 란"},
    {"word": "혼란",   "pair": "quotative_top", "reason": "noun ending in 란"},
    {"word": "놓여",   "pair": "vocative_lit",  "reason": "verb form 놓이-+-어"},
)

# Entries deliberately NOT in LEXICON, recorded so the omission is a decision
# rather than an oversight. Reported on every run (check-particles.py:219).
LEXICON_EXCLUDED = (
    {"word": "어딘가", "pair": "subject",
     "reason": "measured 2026-08-18: both corpus occurrences are 어딘가에서 / "
               "어딘가의, so the 가 is followed by a Hangul syllable and the "
               "eojeol-boundary rule means it is never a SITE, let alone a flag. "
               "Including it would make lexicon_unused permanently 1."},
)

# Transcribed from terminology-ko.md §3 "Icon tokens". The report records that
# file's sha256, so a §3 edit that moves a reading is visible as an input change.
ICON_READINGS = {
    "willpower": "의지", "combat": "힘", "agility": "민첩", "intellect": "지식",
    "per_investigator": "조사자당", "action": "행동", "fast": "신속",
    "reaction": "반응", "skull": "해골", "cultist": "광신도", "tablet": "석판",
    "elder_thing": "옛것", "guardian": "수호자", "seeker": "탐구자",
    "rogue": "무법자", "mystic": "신비주의자", "survivor": "생존자",
}

# The one icon whose particle attaches to the preceding QUANTITY rather than to
# the icon. Untouchable by every code path.
PER_INVESTIGATOR = "per_investigator"

SINO_DIGITS = {"0": "영", "1": "일", "2": "이", "3": "삼", "4": "사",
               "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구"}
SINO_PLACES = {1: "십", 2: "백", 3: "천"}
LATIN_READINGS = {"X": "엑스", "x": "엑스"}


def is_hangul(ch):
    return bool(ch) and HANGUL_START <= ord(ch) <= HANGUL_END


def jongseong(ch):
    """0 = open (no final consonant); 1..27 = the final consonant's index.

    Exact arithmetic on the Hangul syllable block. No `unicodedata`: the
    decomposition tables are a second implementation with its own version skew,
    and this arithmetic is total over the block by construction.
    """
    return (ord(ch) - HANGUL_START) % 28


def expected_form(pair_name, syllable, rieul_rule):
    """'cons' or 'vowel' -- which form of the pair `syllable` requires."""
    final = jongseong(syllable)
    if final == 0:
        return "vowel"
    if final == 8 and rieul_rule == "vowel":
        # After ㄹ the 으로/로서 family takes the vowel form. Stored PER PAIR and
        # never globally: it does not apply to 을/를, 은/는, 이/가 or 과/와.
        return "vowel"
    return "cons"


def sino_reading(digits):
    """Sino-Korean reading of a digit run: the FINAL digit, or the place word for
    a trailing-zero run (20 -> 이십 -> ㅂ; 100 -> 백 -> ㄱ)."""
    stripped = digits.rstrip("0")
    if stripped == "":
        return SINO_DIGITS["0"]
    zeros = len(digits) - len(stripped)
    if zeros:
        return SINO_PLACES.get(zeros, SINO_PLACES[3])
    return SINO_DIGITS[digits[-1]]


def resolve_reading(text, index):
    """Walk left from a particle's start index; return (kind, syllable) or None.

    kind is `hangul`, `trait`, `icon`, `digit` or `latin` -- the five things that
    can precede a particle in this corpus and be READ ALOUD, because it is the
    reading, not the glyph, that decides the particle's form.
    """
    i = index - 1
    # Skip closing brackets and quotes that carry no reading.
    while i >= 0 and text[i] in ")}」』”’":
        i -= 1
    if i < 0:
        return None
    ch = text[i]
    if ch == "]":
        # A [[Trait]] reference or an [icon] token.
        open_double = text.rfind("[[", 0, i)
        close_double = text.rfind("]]", 0, i + 1)
        if close_double == i - 1 and open_double != -1:
            body = text[open_double + 2:i - 1]
            for c in reversed(body):
                if is_hangul(c):
                    return ("trait", c)
            return None
        open_single = text.rfind("[", 0, i)
        if open_single == -1:
            return None
        token = text[open_single + 1:i]
        reading = ICON_READINGS.get(token)
        if reading:
            return ("icon", reading[-1])
        return None
    if is_hangul(ch):
        return ("hangul", ch)
    if ch.isdigit():
        start = i
        while start >= 0 and text[start].isdigit():
            start -= 1
        return ("digit", sino_reading(text[start + 1:i + 1])[-1])
    if ch in LATIN_READINGS:
        return ("latin", LATIN_READINGS[ch][-1])
    return None


def _forms():
    """(form string, pair name, which) longest-first, so 으로서 beats 으로."""
    out = []
    for name, cons, vowel, _rieul in PAIRS:
        out.append((cons, name, "cons"))
        out.append((vowel, name, "vowel"))
    out.sort(key=lambda row: -len(row[0]))
    return tuple(out)


FORMS = _forms()
_RIEUL = dict((name, rieul) for name, _c, _v, rieul in PAIRS)


def scan_particles(text, use_lexicon=True):
    """Every particle SITE in one field, with its verdict.

    A site is a particle form that ENDS AN EOJEOL -- if the character immediately
    after it is itself a Hangul syllable the match is inside a word, not a
    particle. That rule is what stops 어딘가에서 / 사이에 / 폭로하다 being counted
    at all, and it removes a candidate BEFORE it becomes a site, so unlike the
    lexicon it can never appear in the raw flag count.
    """
    text = text or ""
    sites = []
    boundary_skipped = 0
    i = 0
    n = len(text)
    while i < n:
        matched = None
        for form, pair_name, which in FORMS:
            if text.startswith(form, i):
                matched = (form, pair_name, which)
                break
        if matched is None:
            i += 1
            continue
        form, pair_name, which = matched
        end = i + len(form)
        # The eojeol-boundary rule.
        if end < n and is_hangul(text[end]):
            boundary_skipped += 1
            i += 1
            continue
        reading = resolve_reading(text, i)
        if reading is None:
            i = end
            continue
        kind, syllable = reading
        if not is_hangul(syllable):
            i = end
            continue
        want = expected_form(pair_name, syllable, _RIEUL[pair_name])
        site = {"pair": pair_name, "form": form, "which": which,
                "expected": want, "reading_kind": kind, "syllable": syllable,
                "index": i,
                "context": text[max(0, i - 12):min(n, end + 4)]}
        if want != which:
            site["status"] = "flag"
            suppressed = _lexicon_hit(text, i, pair_name) if use_lexicon else None
            if suppressed is not None:
                site["status"] = "suppressed"
                site["lexicon"] = suppressed
        else:
            site["status"] = "ok"
        sites.append(site)
        i = end
    return sites, boundary_skipped


def _lexicon_hit(text, index, pair_name):
    """The lexicon entry suppressing the site at `index`, or None.

    The FULL word must occupy the text ending at the particle AND the entry's
    pair must equal the site's pair -- so an entry can neither mask a defect
    elsewhere in the string nor mask a defect of a different pair.
    """
    for entry in LEXICON:
        if entry["pair"] != pair_name:
            continue
        word = entry["word"]
        start = index + 1 - len(word)
        if start < 0:
            continue
        if text[start:index + 1] != word:
            continue
        # The word must start an eojeol too, or it is a suffix of a longer word.
        if start > 0 and is_hangul(text[start - 1]):
            continue
        return entry
    return None


def audit_particles(fields, use_lexicon=True):
    """The jongseong audit over a mapping of {field label: text}.

    Returns a dict carrying the flags that SURVIVED the lexicon, the ones it
    suppressed, and the entries it never used. `lexicon_unused` is reported
    because a lexicon entry that stops matching is a silent widening of the
    checker -- exactly the drift check-particles.py:129 gives its own exit code.
    """
    flags, suppressed, boundary = [], [], 0
    used = set()
    for label in sorted(fields):
        sites, skipped = scan_particles(fields[label], use_lexicon=use_lexicon)
        boundary += skipped
        for site in sites:
            if site["status"] == "flag":
                flags.append(dict(site, field=label))
            elif site["status"] == "suppressed":
                suppressed.append(dict(site, field=label))
                used.add(site["lexicon"]["word"])
    return {
        "flags": flags,
        "suppressed": suppressed,
        "suppressed_eojeol_boundary": boundary,
        "lexicon_hits": sorted(used),
        "lexicon_unused": sorted(e["word"] for e in LEXICON if e["word"] not in used),
        "lexicon_excluded": [dict(e) for e in LEXICON_EXCLUDED],
    }


# ===========================================================================
# 3. The S4 anti-swap invariant (§5.6)
# ===========================================================================
#
# ICON_MAP had `tablet` and `elder_thing` swapped, and the record's verdict is
# the reason this check exists at all:
#
#   "the preflight's `miss_icons` probe asserts INK, not identity, and
#    `tokenise`/`para_words`/`seg_advance`/`draw_layout` are all
#    value-transparent, so a human `accepted` verdict on 2026-08-18 could not
#    have caught it."
#
# Every stage downstream of the map is value-transparent, so the map is the ONLY
# place a swap can be caught, and a human reading a gallery cannot catch it. The
# generalizable fix is mechanical and has two halves:
#
#   (a) the S4 manifest MUST declare, per token, the measured `ink_fill` and
#       `advance_em` of the codepoint it assigns (kz_decide.VALUE_REQUIRED["S4"]
#       makes both mandatory); this module re-measures from the actual font
#       outlines and asserts the declared pair. A SWAP MAKES BOTH DECLARATIONS
#       WRONG AT ONCE, which is what makes it detectable at all.
#   (b) for every PAIR in the map, the two (ink_fill, advance_em) points must be
#       separated by more than a declared margin -- so two glyphs that could be
#       interchanged undetectably are refused at MAP-AUTHORING time rather than
#       at typeset time, when the pixels are already shipped.
#
# The two halves are separately testable and separately necessary: (a) catches a
# swap of two well-separated glyphs, (b) refuses a map that contains a pair (a)
# could not have caught.

#: Euclidean separation required between any two icons' (ink_fill, advance_em)
#: points. The recorded reference pair -- F25E elder_thing (0.47, 0.916) and F260
#: tablet (0.66, 0.826) -- sits at distance sqrt(0.19^2 + 0.09^2) = 0.210, so
#: this bound is roughly 4x inside the one pair the project has actually measured
#: and is deliberately not tuned to it: a margin set AT the reference distance
#: would pass that pair and nothing else.
ICON_SEPARATION_MIN = 0.05

#: How far a re-measured value may sit from the declared one. Tight, because the
#: measurement is deterministic given (font, size) -- this absorbs float
#: formatting in the manifest, not disagreement.
ICON_DECLARATION_TOL = 0.02

#: The size the reference figures were measured at, and therefore the size a
#: declaration is compared at. `advance_em` is size-invariant by construction
#: (it is divided by the size); `ink_fill` is a ratio of bbox areas and is stable
#: from ~32 px up, which is why the reference used 200.
ICON_PROBE_SIZE = 200
ICON_ADVANCE_SIZE = 32
ICON_COVERAGE = 128     # a probe pixel darker than this is ink


def measure_icon_metrics(font_path, char, probe_size=ICON_PROBE_SIZE,
                         advance_size=ICON_ADVANCE_SIZE):
    """(ink_fill, advance_em) re-measured from the font's own outlines.

    `ink_fill` is the fraction of the glyph's own bounding box that is inked --
    a solid slab and a winged silhouette differ in it by a wide margin. It is
    measured on a large probe canvas so anti-aliasing is a rounding error rather
    than a term.

    ART TIER ONLY: PIL is imported here rather than at module scope so the
    tokenizer and jongseong halves stay importable on any interpreter.
    """
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    big = ImageFont.truetype(font_path, probe_size)
    canvas = Image.new("L", (probe_size * 3, probe_size * 3), 255)
    ImageDraw.Draw(canvas).text((probe_size, probe_size * 2), char, font=big,
                                fill=0, anchor="ls")
    ink = np.asarray(canvas) < ICON_COVERAGE
    ys, xs = np.nonzero(ink)
    if ys.size == 0:
        return 0.0, 0.0
    box = (ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)
    fill = float(ink.sum()) / float(box)
    small = ImageFont.truetype(font_path, advance_size)
    advance = float(small.getlength(char)) / float(advance_size)
    return fill, advance


def check_icon_declarations(declared, measured, tol=ICON_DECLARATION_TOL):
    """Half (a): every declared (ink_fill, advance_em) equals the re-measured one.

    PURE -- takes two mappings of {token: (ink_fill, advance_em)} and returns a
    list of findings. Keeping the measurement out of it is what lets the swap
    case be a synthetic test with no font on the machine, which is the same rule
    §5.5 applies to W1/W2.
    """
    findings = []
    for token in sorted(declared):
        if token not in measured:
            findings.append("%s: declared but the font carries no measurement" % token)
            continue
        d_fill, d_adv = declared[token]
        m_fill, m_adv = measured[token]
        if abs(d_fill - m_fill) > tol:
            findings.append("%s: declared ink_fill %.3f, measured %.3f"
                            % (token, d_fill, m_fill))
        if abs(d_adv - m_adv) > tol:
            findings.append("%s: declared advance_em %.3f, measured %.3f"
                            % (token, d_adv, m_adv))
    for token in sorted(measured):
        if token not in declared:
            findings.append("%s: measured but not declared" % token)
    return findings


def check_icon_separation(declared, margin=ICON_SEPARATION_MIN):
    """Half (b): no two icons sit within `margin` of each other in the plane.

    PURE. A pair that fails this is a pair half (a) could not have caught, so
    the map is refused at authoring time rather than shipped and trusted.
    """
    findings = []
    tokens = sorted(declared)
    for i, left in enumerate(tokens):
        for right in tokens[i + 1:]:
            lf, la = declared[left]
            rf, ra = declared[right]
            distance = ((lf - rf) ** 2 + (la - ra) ** 2) ** 0.5
            if distance <= margin:
                findings.append(
                    "%s and %s are separated by %.4f in (ink_fill, advance_em), "
                    "at or under the %.4f margin -- they could be interchanged "
                    "undetectably" % (left, right, distance, margin))
    return findings


# ===========================================================================
# 4. W1 / W2 -- the mask-coverage assertion (§5.5)
# ===========================================================================
#
# BUILT HERE, ACTIVATED IN kz_mask.py's GATE SET AT STEP 16. The predicates are
# complete and fully tested now; what step 16 adds is the 88-face sweep and the
# adjudication of whatever it surfaces beyond the two known hits.
#
# WHY THIS SUCCEEDS WHERE THREE PREVIOUS ATTEMPTS FAILED.
# `mask-coverage-finding.md` records that English survives in KEEP because CLEAR
# stops short of line ends (71006: 1,070 of 1,102 residual dark px in KEEP), and
# that three detector attempts failed because every erase statistic measures
# INSIDE CLEAR ONLY. Attempts 1-3 asked a brightness STATISTIC to separate glyph
# ink from dark plate art, which it cannot do. W1/W2 ask a GEOMETRY question --
# a line box either is or is not inside CLEAR; a window either does or does not
# leave a margin -- and use brightness only to find rows and columns of text,
# downstream of rule-stroke suppression. That is the difference, and analysis
# §7's claim that this check "does not rely on a brightness threshold" is wrong
# as stated: it does, and it is corrected here and in the README.
#
# TRAILING EDGES ONLY, AND THAT IS NOT A SIMPLIFICATION.
# "All four sides" was the earlier formulation and is unsatisfiable by
# construction. Body text is left-aligned against the window, so the LEADING
# margin is a property of the typesetting rather than of the window: measured
# across all 88 Midwinter faces on 2026-08-20 the tightest left clearance has a
# per-group median of 0-12 px in fourteen of the fifteen groups. At
# window_margin_px = 34 a four-sided rule fires on 80 of 86 non-defect faces --
# every left-aligned band on every face -- and step 16's adjudication queue stops
# being a queue.

# THE COORDINATE FRAME, STATED ONCE, BECAUSE BOTH PREDICATES DEPEND ON IT AND
# NEITHER CAN CHECK IT.
#
# W1 and W2 are given the ink/gray array and the layout `windows` IN THE GROUP'S
# WORKING ORIENTATION -- the frame `build-masks.py`'s own `load_slice(name, rot)`
# and `ink_mask(name, rot)` produce, i.e. the slice already rotated by the
# group's `rot` (Act/Agenda are rot 90). The caller -- `kz_mask.py`, which owns
# the layout catalogue and therefore owns `rot` -- is what applies it.
#
# This matters because W1's rule is "trailing edges only, RIGHT AND BOTTOM IN THE
# GROUP'S WORKING ORIENTATION" (§5.5). Right and bottom are literal here, and
# they are literal only in that frame: hand these functions a rot-90 group's
# slice in the unrotated 750x1050 frame and the predicate silently asserts the
# margin on the wrong two edges -- it will not error, it will just be measuring
# the leading side, which §5.5 shows fires on 80 of 86 non-defect faces.
#
# Nothing here can detect the mistake, because a rotated array is a valid array.
# It is a contract, and this comment is where it is written down.
#
# THE SECOND HALF OF THE CONTRACT IS COST, AND THE SIGNATURES ENCODE IT.
# `ink_mask()` runs a PIL `MaxFilter(11)` over the whole slice and is by a wide
# margin the most expensive operation in this module. Both predicates therefore
# take the ALREADY-COMPUTED array plus a `windows` MAPPING -- plural, deliberately
# -- so the filter is paid once per face and amortised over every window in the
# group. Call them once per face with all of that face's windows; calling them
# once per (group, window) pays the MaxFilter again for every window and turns
# the 88-face sweep into a multiple of the work it needs to be.

INK_DARK = 120          # build-masks.py:76 -- darker than this is a candidate
INK_LIGHT = 165         # build-masks.py:77 -- ... only if something this light is near
INK_RADIUS = 11         # build-masks.py:78
LINE_MIN_INK = 2        # build-masks.py:83
LINE_GAP = 4            # build-masks.py:84
COL_GAP = 34            # build-masks.py:85 -- and the reference window_margin_px

SLICE_INK = 96          # typeset-cards.py:188 -- W2's threshold
RULE_MIN_LEN = 40       # typeset-cards.py:198
RULE_ASPECT = 12        # typeset-cards.py:199
RULE_DILATE = 2         # typeset-cards.py:200

#: The band-height window. §5.5: "W1 evaluates only bands whose height is
#: plausibly a line, 0.5 * en_ink_h <= h <= 2.0 * en_ink_h". Without it W1 fires
#: on every Act face on the dark plate below the printed body -- a 100 px band
#: the dark-on-light detector legitimately picks up -- and the check is noise.
BAND_H_LO = 0.5
BAND_H_HI = 2.0

#: The row segmentation's two constants, both cited to the function this module
#: forks. `measure_en_lines` takes `has = profile > 0` (typeset-cards.py:747) --
#: NOT `>= LINE_MIN_INK` -- and cuts a merged run at the profile minimum between
#: consecutive "cores", rows carrying at least ROW_CORE_FRAC of the peak.
ROW_MIN_INK = 1         # typeset-cards.py:747 -- `has = profile > 0`
ROW_CORE_FRAC = 0.12    # typeset-cards.py:202


def _run_lengths(flags, vertical):
    """For every True pixel, the length of its contiguous run along one axis.

    Lifted from typeset-cards.py; O(W) numpy passes.
    """
    import numpy as np
    arr = flags.T if vertical else flags
    height, width = arr.shape
    fwd = np.zeros((height, width), dtype=np.int32)
    counter = np.zeros(height, dtype=np.int32)
    for x in range(width):
        counter = np.where(arr[:, x], counter + 1, 0)
        fwd[:, x] = counter
    out = np.zeros((height, width), dtype=np.int32)
    prev = np.zeros(height, dtype=np.int32)
    prev_set = np.zeros(height, dtype=bool)
    for x in range(width - 1, -1, -1):
        cur = np.where(arr[:, x] & prev_set, prev, fwd[:, x])
        cur = np.where(arr[:, x], cur, 0)
        out[:, x] = cur
        prev, prev_set = cur, arr[:, x]
    return out.T if vertical else out


def rule_stroke_mask(ink):
    """Long-thin strokes are RULES, not glyphs, and must not be measured as text.

    Symmetric by construction, and it has to be: the Story/Act/Agenda flavour
    ornament is a pair of VERTICAL strokes while the traits divider and the <hr>
    are HORIZONTAL ones. This is the function `measure_en_lines` already calls
    and reports as `rule_cols_excluded` -- naming it here is what makes
    "rule-stroke suppression" a definition rather than a phrase.
    """
    import numpy as np
    if not ink.any():
        return np.zeros_like(ink)
    horizontal = _run_lengths(ink, vertical=False)
    vertical = _run_lengths(ink, vertical=True)
    longer = np.maximum(horizontal, vertical)
    shorter = np.maximum(np.minimum(horizontal, vertical), 1)
    rule = ink & (longer >= RULE_MIN_LEN) & (longer >= RULE_ASPECT * shorter)
    if rule.any() and RULE_DILATE > 0:
        acc = rule.copy()
        for dy in range(-RULE_DILATE, RULE_DILATE + 1):
            for dx in range(-RULE_DILATE, RULE_DILATE + 1):
                acc |= np.roll(np.roll(rule, dy, axis=0), dx, axis=1)
        rule = acc & ink
    return rule


def ink_mask(gray_image):
    """build-masks.py:104-113's dark-on-light detector, as a bool array.

    This -- not `gray < SLICE_INK` -- is what W1 measures with, because it is the
    detector the MASK ITSELF was built from and the one that already discounts
    dark-on-dark plate art. Using a different detector to check a mask would be
    measuring a different thing and calling the difference a defect.
    """
    from PIL import ImageChops, ImageFilter
    import numpy as np
    near_light = gray_image.filter(ImageFilter.MaxFilter(INK_RADIUS))
    dark = gray_image.point(lambda v: 255 if v < INK_DARK else 0)
    light = near_light.point(lambda v: 255 if v > INK_LIGHT else 0)
    return np.asarray(ImageChops.multiply(dark, light)) > 0


def _row_runs(flags):
    """Maximal contiguous True runs as inclusive (a, b) pairs, with LINE_GAP
    tolerance -- build-masks.py:116's segmentation, not typeset-cards'."""
    runs, current = [], None
    for i, value in enumerate(flags):
        if value:
            current = [i, i] if current is None else [current[0], i]
        elif current is not None and i - current[1] > LINE_GAP:
            runs.append(tuple(current))
            current = None
    if current is not None:
        runs.append(tuple(current))
    return runs


def _ink_runs(flags):
    """Maximal contiguous True runs as inclusive (a, b) pairs -- NO gap tolerance.

    typeset-cards.py:706-718's `_runs`, and the difference from `_row_runs`
    above is the whole of this module's segmentation defect. `_row_runs` is
    build-masks.py:116's: it forgives LINE_GAP blank rows inside one band and it
    only counts a row as ink at LINE_MIN_INK. On the Midwinter body windows the
    flavour ornament lays 5-10 px of ink across every inter-line gap, so every
    row clears LINE_MIN_INK, NO gap is ever detected, and consecutive printed
    lines merge into one band -- measured on `Act/front` `body`, 5 bands on
    71006 where there are 10 and 6 on 71005 where there are 11. This one counts
    a row as ink at ROW_MIN_INK and forgives nothing, which is what
    `measure_en_lines` does and why its docstring is an explicit warning against
    the substitution: "rects are padded 7px and sometimes merge two printed
    lines, so rect heights are unusable as a ruler".
    """
    out, start = [], None
    for i, value in enumerate(flags):
        if value and start is None:
            start = i
        elif not value and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def _core_split(profile, has, core_frac=ROW_CORE_FRAC):
    """typeset-cards.py:750-781 -- cut a merged run at the profile minima.

    `peak = profile.max()`, `core = profile >= core_frac * peak`, and where there
    is MORE THAN ONE core the span is cut between consecutive cores at the argmin
    of the profile between them; each segment is then RE-EXPANDED to the full ink
    extent inside it, so `en_ink_h` stays the ascender-to-descender band it has
    always meant rather than the x-height (typeset-cards.py:775-777).

    Returns None when there is nothing to split (0 or 1 core), which is the
    source's own `if len(cores) <= 1: bands = raw`.
    """
    import numpy as np
    peak = int(profile.max())
    cores = _ink_runs(profile >= max(1.0, core_frac * peak))
    if len(cores) <= 1:
        return None
    cuts = []
    for i in range(len(cores) - 1):
        a, b = cores[i][1], cores[i + 1][0]
        if b > a + 1:
            cuts.append(a + 1 + int(np.argmin(profile[a + 1:b])))
        else:
            cuts.append(b)
    lo = int(np.argmax(has))
    hi = len(has) - 1 - int(np.argmax(has[::-1]))
    bounds = [lo] + cuts + [hi + 1]
    bands = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        if e <= s:
            continue
        rows = np.nonzero(has[s:e])[0]
        if rows.size:
            bands.append((s + int(rows[0]), s + int(rows[-1])))
    return bands


def measure_bands(ink, window, min_ink=ROW_MIN_INK):
    """Per-band (y0, y1, x0, x1) inside `window`, WINDOW-RELATIVE, rules removed.

    THE KOREANIZE FORK OF `measure_en_lines` (typeset-cards.py:721), and the fork
    is the point (§5.5 property 2). `measure_en_lines` as shipped returns row
    bands plus ONE ink_x0/ink_x1 pair for the whole selection and one
    line_x0/line_x1 for the DOMINANT band only -- deliberately, because its
    caller wants a single alignment reference. But 71006/71005 are a HORIZONTAL
    tail on ONE band among ten and eleven respectively, and the dominant band is
    not that band. The per-band margins the design measures (27 px and 15 px) are
    invisible to a single whole-selection extent, which reports 0 on both faces
    for a reason unrelated to the defect.

    IT IS `measure_en_lines`' SEGMENTATION AND NOT `line_groups`', AND THAT IS
    NOT A DETAIL. This function forked build-masks.py:116 until 2026-08-24. Its
    LINE_MIN_INK = 2 / LINE_GAP = 4 pair is calibrated for a MASK -- it wants a
    rect that swallows the ornament -- while W1 needs a RULER, and the two are
    not interchangeable. Measured on `Act/front` `body`, rot 90:

        face    line_groups()            measure_en_lines()
        71006   5 bands, en_ink_h 98     10 bands, en_ink_h 33
        71005   6 bands, en_ink_h 63     11 bands, en_ink_h 30

    and because `en_ink_h` is an INK-WEIGHTED median over a band set that still
    contained the ornament plate, the plate dragged the median up and
    `text_bands`' filter INVERTED: on 71006 the real text bands y[97,129] (right
    margin 41) and y[143,172] (57) were dropped while the full-width plate
    y[301,473] (0) was kept, so W1's tightest clearance was 0 -- from the plate,
    not the 27 from the text. Both known hits fired for the wrong reason and
    twelve of fifteen groups calibrated to 0. §5.5: "Without that filter W1 fires
    on every Act face on a band that is not text, and the check is noise."

    BLANK ROWS SEPARATE FIRST; THE CORE-SPLIT IS THE FALLBACK. `measure_en_lines`
    applies its core-split to the whole selection unconditionally, because its
    selection is ONE FIELD'S padded rects -- a few lines of one paragraph. W1's
    selection is a whole 532x474 body window, and applied at that scale the
    unconditional cut fragments the flourish under the last two printed lines
    into text-height full-width pieces: measured on 71006 it returns 14 bands and
    puts y[409,446] x[2,531] back INSIDE the height filter, so the tightest kept
    clearance is 0 again and the fix undoes itself. So the run structure is used
    where it exists -- a blank row is an unambiguous separator that no threshold
    has to be chosen for -- and the core-split is applied where it does not, i.e.
    to a selection whose ink is ONE contiguous run. That is precisely the case
    the ornament creates and the case `line_groups` could not see; it is what
    `test_w1_ornament_bridged_gaps_do_not_merge_the_lines` exercises.

    Returns (bands, en_ink_h) with bands window-relative and en_ink_h the
    ink-weighted median band height over ALL bands, before any filtering.
    """
    import numpy as np
    x0, y0, x1, y1 = window
    height, width = ink.shape
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(width, int(x1)), min(height, int(y1))
    if x1 <= x0 or y1 <= y0:
        return [], None
    sub = ink[y0:y1, x0:x1]
    if not sub.any():
        return [], None
    text_ink = sub & ~rule_stroke_mask(sub)
    if not text_ink.any():
        return [], None

    profile = text_ink.sum(axis=1).astype(np.int64)
    has = profile >= min_ink
    runs = _ink_runs(has)
    if len(runs) == 1:
        runs = _core_split(profile, has) or runs
    bands = []
    for top, bottom in runs:
        cols = np.nonzero(text_ink[top:bottom + 1].any(axis=0))[0]
        if cols.size == 0:
            continue
        bands.append((int(top), int(bottom), int(cols[0]), int(cols[-1])))
    if not bands:
        return [], None
    return bands, _ink_weighted_median_height(bands, profile)


def _ink_weighted_median_height(bands, profile):
    """en_ink_h -- the INK-WEIGHTED median band height, not the plain median.

    Lifted from typeset-cards.py:819 `measure_en_optics`, whose docstring gives
    the reason: "A plain median is destroyed by 2-3px ornament fragments:
    71005-back's title bands are [71,2,2,1] and its plain median is 2. Under this
    pipeline they are [9,2,1,2,3,38] and the answer is 38 -- the true
    ascender-to-descender extent of 'Contacts Acquired'."
    """
    heights = [bottom - top + 1 for top, bottom, _a, _b in bands]
    weights = [int(profile[top:bottom + 1].sum()) for top, bottom, _a, _b in bands]
    order = sorted(range(len(bands)), key=lambda i: heights[i])
    total = sum(weights) or 1
    acc = 0
    ink_h = heights[order[-1]]
    for i in order:
        acc += weights[i]
        if acc * 2 >= total:
            ink_h = heights[i]
            break
    return int(ink_h)


def text_bands(bands, en_ink_h):
    """The bands that are plausibly TEXT LINES, per the band-height filter.

    A one-band selection makes the filter vacuous by construction -- the
    ink-weighted median of one band IS that band's height -- which is exactly why
    the synthetic W1 suite carries a two-band case. Without that case the filter
    can be deleted and all the other cases stay green.
    """
    if en_ink_h is None:
        return []
    lo, hi = BAND_H_LO * en_ink_h, BAND_H_HI * en_ink_h
    return [b for b in bands if lo <= (b[1] - b[0] + 1) <= hi]


def w1_window_margin(ink, windows, window_margin_px, group, face):
    """W1 -- the window-margin premise, on the TRAILING edges only.

    For every (group, window) and every slice in that group, every located text
    band inside the window must leave at least `window_margin_px` of non-ink
    between its extent and the window's TRAILING edges -- right and bottom in the
    group's working orientation. If any band does not, THE WINDOW ITSELF IS THE
    DEFECT: it is not wider than the content it is supposed to bound, so the
    padded rects derived from it cannot reach the line ends and W2 is vacuous.

    `window_margin_px`'s reference value is 34, and it is not a taste: it is
    build-masks.py's own COL_GAP -- "columns of blank that still count as one
    glyph run". If the gap between a band's last detected ink and the window edge
    is narrower than COL_GAP, the segmenter would have joined a further glyph run
    into that same band had one been printed there, so the window cannot be shown
    to bound the content. That is exactly W1's claim, stated in the same units,
    from the same file, as the segmenter it constrains.
    """
    hits = []
    for window_name in sorted(windows):
        x0, y0, x1, y1 = windows[window_name]
        width, height = int(x1) - int(x0), int(y1) - int(y0)
        if width <= 0 or height <= 0:
            continue
        bands, en_ink_h = measure_bands(ink, (x0, y0, x1, y1))
        for top, bottom, bx0, bx1 in text_bands(bands, en_ink_h):
            right = (width - 1) - bx1
            below = (height - 1) - bottom
            tightest = min(right, below)
            if tightest < window_margin_px:
                hits.append({
                    "check": "W1", "group": group, "window_name": window_name,
                    "face": face,
                    "band": [top, bottom, bx0, bx1],
                    "right_margin": int(right), "bottom_margin": int(below),
                    "window_margin_px": int(window_margin_px),
                    "en_ink_h": en_ink_h,
                    "detail": "%s/%s %s: band y[%d,%d] x[%d,%d] leaves %d px of "
                              "trailing clearance, under window_margin_px %d"
                              % (group, window_name, face, top, bottom, bx0, bx1,
                                 tightest, window_margin_px),
                })
    return hits


def w2_line_containment(gray, clear, windows, group, face):
    """W2 -- line containment. Every located line box fully inside mask CLEAR.

    W2 catches the OTHER mechanism: a window wide enough, but the `protect`
    repaint or a merged band leaving a tail in KEEP.

    `clear` is a bool array, True where the mask is CLEAR (alpha == 0). It is a
    DECLARED mask, never one this function derives -- asserting containment
    against `regions_slice_coords`, the mask's own padded output rects, would be
    circular and would be detector attempt #4 (§5.5 property 1).
    """
    import numpy as np
    hits = []
    ink = gray < SLICE_INK
    for window_name in sorted(windows):
        x0, y0, x1, y1 = (int(v) for v in windows[window_name])
        bands, en_ink_h = measure_bands(ink, (x0, y0, x1, y1))
        for top, bottom, bx0, bx1 in text_bands(bands, en_ink_h):
            # Back to SLICE coordinates -- measure_bands works window-relative.
            ay0, ay1 = y0 + top, y0 + bottom
            ax0, ax1 = x0 + bx0, x0 + bx1
            box = clear[ay0:ay1 + 1, ax0:ax1 + 1]
            if box.size == 0 or box.all():
                continue
            right, below = _attribute_overhang(box)
            hits.append({
                "check": "W2", "group": group, "window_name": window_name,
                "face": face,
                "band": [ay0, ay1, ax0, ax1],
                "overhang_px": int((~box).sum()),
                "overhang_right": int(right), "overhang_bottom": int(below),
                "detail": "%s/%s %s: line box y[%d,%d] x[%d,%d] leaves %d px "
                          "outside CLEAR (right %d, bottom %d)"
                          % (group, window_name, face, ay0, ay1, ax0, ax1,
                             int((~box).sum()), right, below),
            })
    return hits


def _attribute_overhang(box):
    """(right, bottom) linear overhang of a line box outside CLEAR.

    ATTRIBUTED TO THE TIGHTER AXIS FIRST, and the naive alternative is actively
    misleading. The two projections are not independent: a 12 px tail on the
    right makes EVERY row of the box impure, so an independent row projection
    reports the band's full height as a bottom overhang on a defect that is
    purely horizontal -- measured on the synthetic case, "bottom 30" for a 12 px
    tail. The reverse is equally wrong: an 8-row overhang at the bottom makes
    every column impure and reads as a 500 px right overhang.

    So the axis with the SMALLER trailing run is the more specific explanation
    and is attributed first; the other is then re-measured on the remainder. A
    box that genuinely overhangs on BOTH axes saturates both projections and is
    reported on the tighter one with the remainder on the other -- `overhang_px`
    always carries the true total, which is why it is reported beside these.
    """
    cols = _trailing_false(box.all(axis=0))
    rows = _trailing_false(box.all(axis=1))
    if cols and (not rows or cols <= rows):
        inner = box[:, :box.shape[1] - cols]
        below = _trailing_false(inner.all(axis=1)) if inner.size else 0
        return cols, below
    if rows:
        inner = box[:box.shape[0] - rows, :]
        right = _trailing_false(inner.all(axis=0)) if inner.size else 0
        return right, rows
    return 0, 0


def _trailing_false(flags):
    """How many trailing entries of `flags` are False -- the linear overhang."""
    count = 0
    for value in reversed(list(flags)):
        if value:
            break
        count += 1
    return count


def baseline_key(hit):
    """The tuple a residual is keyed on: (check, group, window_name, face).

    §5.5: the check refuses at exit 23 only on a hit whose key is NOT in
    `data/locks/<slug>.lock.json`'s `mask.residual_baseline[]`, and
    `--accept-mask-residual` records a human decision to extend the baseline
    AFTER the hit has been ruled through the S6 `triage` path.
    """
    return (hit.get("check"), hit.get("group"), hit.get("window_name"),
            hit.get("face"))


def mask_residual_check(hits, baseline, accepted=False):
    """The stage-report check W1/W2 findings reach a process exit through.

    CONTAINMENT RATHER THAN EQUALITY, because the two-hit baseline is a SAMPLE
    and the finding says so: mask-coverage-finding.md:26 records "Two confirmed
    instances out of the 26 faces actually looked at, which is a rate, not a
    bound -- the corpus was never swept" -- of 88 faces. Running W1/W2 across all
    88 for the first time will plausibly surface hits among the 62 never
    examined. THAT IS THE DETECTOR WORKING. A design that scored it as "exactly
    two or the checker is wrong" would be scoring a successful sweep as a defect.
    """
    known = set()
    for entry in baseline or ():
        known.add((entry.get("check"), entry.get("group"),
                   entry.get("window_name"), entry.get("face")))
    novel = [h for h in hits if baseline_key(h) not in known]
    reproduced = [h for h in hits if baseline_key(h) in known]
    ok = (not novel) or bool(accepted)
    return {
        "id": "W0",
        "name": "mask_coverage_residual",
        "status": "pass" if ok else "fail",
        "tolerance": "mask-residual",
        "exit_on_fail": kc.EXIT_MASK_RESIDUAL,
        "detail": [h["detail"] for h in novel],
        "reproduced_baseline": len(reproduced),
        "novel": len(novel),
    }


# ===========================================================================
# 5. The `check` stage (§1.2 -- gates `translate`)
# ===========================================================================
#
# `check` consumes the Korean text `translate` produced and re-validates it
# against the English `init` indexed, with the independent tokenizer above. It
# writes nothing outside `<run_dir>` and it has no --fix: the defect class it
# exists for (`주요목적를` x5) lives in the GENERATOR, `data/text/<slug>-ko.py`,
# and a checker that repaired the JSON would leave the generator able to
# reproduce it on the next run. Routing a finding to a fix is `audit`(S7)'s job.

#: The text-bearing fields compared field-by-field. Lifted from verify-a4.py:31.
TEXT_FIELDS = ("name", "subname", "traits", "text", "flavor",
               "back_name", "back_text", "back_flavor")

#: The identity fields a translation may never move (verify-a4.py check 5).
IDENTITY_FIELDS = ("guid", "card_id", "deck_key", "cell", "sheet", "pack")

#: The Korean object-text fields `objtext` will carry into the override.
OBJECT_TEXT_FIELDS = ("nickname_ko", "description_ko")


def _card_index(doc, key_names=("arkham_id", "code")):
    """{arkham_id: card} plus the ids seen more than once.

    Keyed by identity rather than by position, for the reason §5.8 gives C1: an
    id-driven loop over a list silently drops the second of a colliding pair,
    and "verify all 62" then PASSES while an English card sits in an otherwise
    Korean scenario.
    """
    cards = doc.get("cards") if isinstance(doc, dict) else doc
    index, duplicates = {}, []
    for card in cards or []:
        ident = None
        for key in key_names:
            if card.get(key):
                ident = str(card[key])
                break
        if ident is None:
            continue
        if ident in index:
            duplicates.append(ident)
            continue
        index[ident] = card
    return index, sorted(set(duplicates))


def _fail(check_id, name, detail):
    return {"id": check_id, "name": name,
            "status": "fail" if detail else "pass",
            "exit_on_fail": kc.EXIT_ARTIFACT, "detail": detail}


def run_checks(en_doc, ko_doc):
    """The seven checks, as a `checks[]` list. PURE -- takes two dicts.

    Being pure is what lets `test_koreanize_checkers.py` exercise every one of
    them on a two-card synthetic corpus rather than on the 2.1 GB tree.
    """
    en_index, en_dupes = _card_index(en_doc)
    ko_index, ko_dupes = _card_index(ko_doc)
    checks = []

    # CH1 -- every English id present in Korean, exactly once, and none invented.
    missing = sorted(set(en_index) - set(ko_index))
    extra = sorted(set(ko_index) - set(en_index))
    detail = []
    detail += ["%s: in English, absent from Korean" % i for i in missing]
    detail += ["%s: in Korean, absent from English" % i for i in extra]
    detail += ["%s: listed twice in the Korean file" % i for i in ko_dupes]
    detail += ["%s: listed twice in the English file" % i for i in en_dupes]
    checks.append(_fail("CH1", "id_coverage", detail))

    # CH2 -- field non-emptiness parity, BOTH directions.
    detail = []
    for ident in sorted(set(en_index) & set(ko_index)):
        en_card, ko_card = en_index[ident], ko_index[ident]
        for field in TEXT_FIELDS:
            en_value = (en_card.get(field) or "").strip()
            ko_value = (ko_card.get(field) or "").strip()
            if en_value and not ko_value:
                detail.append("%s.%s: non-empty in English, empty in Korean"
                              % (ident, field))
            elif ko_value and not en_value:
                detail.append("%s.%s: empty in English, non-empty in Korean"
                              % (ident, field))
    checks.append(_fail("CH2", "field_parity", detail))

    # CH3 -- markup multisets, WHICH tokens and not just how many.
    detail = []
    for ident in sorted(set(en_index) & set(ko_index)):
        en_card, ko_card = en_index[ident], ko_index[ident]
        for field in TEXT_FIELDS:
            for finding in markup_delta(en_card.get(field), ko_card.get(field)):
                detail.append("%s.%s: %s" % (ident, field, finding))
    checks.append(_fail("CH3", "markup_multiset", detail))

    # CH4 -- identity fields unchanged by translation.
    detail = []
    for ident in sorted(set(en_index) & set(ko_index)):
        en_card, ko_card = en_index[ident], ko_index[ident]
        for field in IDENTITY_FIELDS:
            if field not in en_card or field not in ko_card:
                continue
            if en_card[field] != ko_card[field]:
                detail.append("%s.%s: %r became %r"
                              % (ident, field, en_card[field], ko_card[field]))
    checks.append(_fail("CH4", "identity_unchanged", detail))

    # CH5 -- card_id == deck_key * 100 + cell, still, after translation.
    detail = []
    for ident in sorted(ko_index):
        card = ko_index[ident]
        if not all(k in card for k in ("card_id", "deck_key", "cell")):
            continue
        try:
            want = int(card["deck_key"]) * 100 + int(card["cell"])
        except (TypeError, ValueError):
            detail.append("%s: deck_key/cell are not integral" % ident)
            continue
        if int(card["card_id"]) != want:
            detail.append("%s: card_id %s != deck_key %s * 100 + cell %s = %d"
                          % (ident, card["card_id"], card["deck_key"],
                             card["cell"], want))
    checks.append(_fail("CH5", "card_id_arithmetic", detail))

    # CH6 -- residual English. REPORTED, NEVER FATAL (verify-a4.py:20): proper
    # nouns like "E. Zann" and cross-references like "(->R1)" are legitimately
    # Latin, so a hard rule here would refuse correct translations.
    residual = []
    for ident in sorted(ko_index):
        card = ko_index[ident]
        for field in TEXT_FIELDS + OBJECT_TEXT_FIELDS:
            for run in latin_runs(card.get(field)):
                residual.append("%s.%s: %s" % (ident, field, run))
    checks.append({"id": "CH6", "name": "residual_english", "status": "pass",
                   "exit_on_fail": None, "informational": True,
                   "detail": residual})

    # CH7 -- the jongseong particle audit.
    fields = {}
    for ident in sorted(ko_index):
        card = ko_index[ident]
        for field in TEXT_FIELDS + OBJECT_TEXT_FIELDS:
            value = card.get(field)
            if value:
                fields["%s.%s" % (ident, field)] = value
    audit = audit_particles(fields)
    checks.append({
        "id": "CH7", "name": "particle_jongseong",
        "status": "fail" if audit["flags"] else "pass",
        "exit_on_fail": kc.EXIT_ARTIFACT,
        "detail": ["%s: %r takes the %s form after %r (%s)"
                   % (f["field"], f["form"], f["expected"], f["syllable"],
                      f["context"])
                   for f in audit["flags"]],
        "lexicon_hits": audit["lexicon_hits"],
        "lexicon_unused": audit["lexicon_unused"],
        "suppressed": len(audit["suppressed"]),
        "suppressed_eojeol_boundary": audit["suppressed_eojeol_boundary"],
    })
    return checks, en_index, ko_index, audit


def run_check(run_dir, mode="build", en_path=None, ko_path=None, workspace=None):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. card-text-en.json and card-text-ko.json are readable JSON  -> exit 13
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    en_path = en_path or os.path.join(run_dir, "card-text-en.json")
    ko_path = ko_path or os.path.join(run_dir, "card-text-ko.json")
    en_doc = _load_json(en_path, "card-text-en.json")
    ko_doc = _load_json(ko_path, "card-text-ko.json")

    checks, en_index, ko_index, audit = run_checks(en_doc, ko_doc)
    triggered = [c["exit_on_fail"] for c in checks
                 if c["status"] == "fail" and c.get("exit_on_fail")]

    counts = {
        "cards_en": len(en_index),
        "cards_ko": len(ko_index),
        "cards_checked": len(set(en_index) & set(ko_index)),
        "markup_findings": len([c for c in checks if c["id"] == "CH3"][0]["detail"]),
        "residual_english": len([c for c in checks if c["id"] == "CH6"][0]["detail"]),
        "particle_flags": len(audit["flags"]),
        "particle_suppressed": len(audit["suppressed"]),
        "particle_suppressed_eojeol_boundary": audit["suppressed_eojeol_boundary"],
    }

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path, en_path, ko_path]),
        freshness=kc.build_freshness([en_path, ko_path],
                                     upstream_report_path=os.path.join(
                                         run_dir, "translate.json")),
        results={"lexicon_excluded": audit["lexicon_excluded"],
                 "lexicon_unused": audit["lexicon_unused"]})
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    return report


def _load_json(path, label):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is missing" % label, path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        kc.refuse(kc.EXIT_PRECONDITION, "%s is not readable JSON" % label,
                  "%s: %s" % (path, exc))


# ===========================================================================
# 6. Synthetic fixtures for W1 / W2 -- REQUIRED, and required to be corpus-free
# ===========================================================================
#
# §5.5 is emphatic about why these exist. W1/W2 is the single highest-value
# automation in this design and, as previously specified, it was the ONLY
# tolerance-bearing predicate in §4.1 whose evidence was a 2.1 GB gitignored
# tree. §5.9 marks corpus-dependent tests `needs_golden` and skips them when the
# manifest's inputs are absent -- so on any machine without the corpus (CI, a
# second machine, this one after a disk loss) the checker had ZERO passing
# evidence while every other threshold in the document had a generated fault.
#
# The corpus case is demoted to a corroboration precisely because it is the only
# one of the set that can silently vanish.

DEFAULT_INK = 40        # < INK_DARK and < SLICE_INK: ink under both detectors
DEFAULT_GROUND = 255


def synth_slice(size, bands, ground=DEFAULT_GROUND, ink=DEFAULT_INK):
    """A generated slice carrying hatched bands. Returns a PIL "L" image.

    Each band is a dict of window-ABSOLUTE `top`/`height`/`left`/`right` plus a
    `block` (bw, bh) and `period` (px, py) lattice. HATCHED RATHER THAN SOLID,
    and that is not decoration -- three separate mechanisms require it:

      * `ink_mask()` is dark-on-LIGHT: a pixel counts only if something lighter
        than INK_LIGHT sits within INK_RADIUS. A solid band's interior has no
        light pixel within 5 px in any direction, so a solid band registers only
        its own rim -- which is not what printed text does, because printed text
        has white between its glyphs.
      * `rule_stroke_mask()` suppresses long-thin strokes. A band drawn as
        full-height vertical strips is a RULE by that definition (run >= 40 at
        aspect >= 12) and is suppressed entirely.
      * the row segmentation counts a row as ink at ROW_MIN_INK and forgives
        NO blank row (typeset-cards.py:747), so a lattice with ANY vertical gap
        fragments into as many short bands as it has ink runs, instead of the
        one tall band the plate case needs. Until 2026-08-24 this read "gaps
        wider than LINE_GAP"; the plate lattice was `(3, 4)/(15, 6)` and survived
        only on that 4-row tolerance, so fixing `measure_bands` shattered it into
        fifteen 4 px bands. A band meant to be read as ONE must now be drawn
        vertically SOLID (`bh == py`) and wide enough that `rule_stroke_mask`
        does not claim it: at height h the block must be wider than h / 12.


    `density` therefore has to be controllable independently of height, which is
    what the two-band case turns on: see `w1_cases()`.
    """
    from PIL import Image, ImageDraw
    image = Image.new("L", size, ground)
    draw = ImageDraw.Draw(image)
    for band in bands:
        top, height = int(band["top"]), int(band["height"])
        left, right = int(band["left"]), int(band["right"])
        bw, bh = band.get("block", (6, 6))
        px, py = band.get("period", (8, 8))
        ys = list(range(top, top + height, py))
        xs = list(range(left, right + 1, px))
        for y in ys:
            for x in xs:
                draw.rectangle([x, y, min(x + bw - 1, right),
                                min(y + bh - 1, top + height - 1)], fill=ink)
        # Force ink flush at the trailing edges so the band's measured extent is
        # exactly (left, right) and (top, top+height-1) rather than wherever the
        # lattice happened to stop. A case that means to leave 27 px must leave
        # 27 px, not 27 plus a lattice remainder.
        draw.rectangle([max(left, right - bw + 1), top, right,
                        min(top + bh - 1, top + height - 1)], fill=ink)
        draw.rectangle([max(left, right - bw + 1),
                        max(top, top + height - bh), right,
                        top + height - 1], fill=ink)
        draw.rectangle([left, max(top, top + height - bh),
                        min(left + bw - 1, right), top + height - 1], fill=ink)
    return image


def band_density(band):
    """The lattice fill ratio of one `synth_slice` band -- the quantity the
    two-band case has to control, since the ink-weighted median is a weighted
    median of HEIGHTS and the weights are ink counts."""
    bw, bh = band.get("block", (6, 6))
    px, py = band.get("period", (8, 8))
    return (min(bw, px) / float(px)) * (min(bh, py) / float(py))


#: The window every synthetic case declares: 540 x 360 inside a 600 x 400 slice.
#: 540 is deliberately close to the Act/front `body` window's real 532 px width
#: (build-masks.py:436, (26, 192, 558, 666) at rot 90) so the reference figures
#: below are read at the scale they were measured at.
SYNTH_SIZE = (600, 400)
SYNTH_WINDOW = (20, 20, 560, 380)
SYNTH_GROUP = "Act/front"
SYNTH_FACE = "synthetic"


def w1_cases():
    """W1's FIVE synthetic cases (§5.5 plus the one §5.5 did not think to ask for).

    Case 4 is the one §5.5 enumerates and it earns its place. The first three
    each declare ONE band -- and on a one-band selection the ink-weighted median
    band height IS that band's height, so `BAND_H_LO <= h <= BAND_H_HI` is
    satisfied trivially and the filter that makes W1 signal rather than noise is
    VACUOUS in all three. Delete the filter and all three stay green; only the
    corpus case would notice, and the corpus case is the one that can be absent.

    Case 4 therefore declares TWO bands: a text-height band leaving 40 px of
    trailing clearance, and a full-width band at 3x that height leaving 0 -- the
    synthetic form of the plate the Act faces actually carry. With the filter it
    must not fire; without it, it fires on the plate.

    CASE 5 IS THE ONE THAT WAS MISSING, AND ITS ABSENCE IS WHY THE DEFECT SHIPPED.
    Cases 1-4 all declare bands separated by GENUINELY BLANK rows, which is the
    one thing the Midwinter body windows never are: the flavour ornament lays a
    few pixels of ink across every inter-line gap, so `line_groups`' LINE_MIN_INK
    /LINE_GAP pair never finds a gap and merges ten printed lines into five. Case
    4 could not see that, because a segmenter that merges nothing on blank rows
    merges nothing on case 4 either -- both segmenters agree on it, so it stayed
    green through the whole defect. Case 5 declares four text bands whose gaps
    are BRIDGED by a low-ink ornament, plus a plate that carries MORE ink than
    any single text band, which is the relation that actually holds on the
    corpus. Under `line_groups` it is one 234 px band whose x extent is the
    plate's, so it fires at right margin 0; under `measure_en_lines`' cut it is
    four text bands plus a plate, the plate is dropped by the height filter, and
    the text bands leave their declared 40 px. It fires before the fix and is
    silent after it, which is what makes it a check rather than a decoration.
    """
    width = SYNTH_WINDOW[2] - SYNTH_WINDOW[0]
    left = SYNTH_WINDOW[0]
    dense = {"block": (6, 30), "period": (8, 30)}       # density 0.75
    # The plate must read as ONE band, so it is vertically SOLID (bh == py) --
    # a lattice with vertical gaps now fragments (see `synth_slice`). 9 px wide
    # keeps `rule_stroke_mask` off it (90 < RULE_ASPECT * 9) while the 45 px
    # period holds its density at 0.2: 0.2 * 540 * 90 = 9,720 ink px against the
    # text band's 0.75 * 500 * 30 = 11,250, which is what puts the ink-weighted
    # median on the TEXT height in this case. That relation is deliberately the
    # OPPOSITE of `ornament-bridge`'s, and having both is the point -- see
    # `test_w1_band_height_filter_selects_the_text_band`.
    plate_lattice = {"block": (9, 6), "period": (45, 6)}    # density 0.2
    text_short = dict(top=60, height=30, left=left,
                      right=left + width - 1 - 27, **dense)
    text_clear = dict(top=60, height=30, left=left,
                      right=left + width - 1 - 40, **dense)
    plate = dict(top=240, height=90, left=left,
                 right=left + width - 1, **plate_lattice)
    return {
        # The 27 px is 71006's own measured trailing clearance (§5.5).
        "positive": {"bands": [text_short], "window": SYNTH_WINDOW,
                     "margin": COL_GAP, "fires": True,
                     "why": "a band stopping 27 px short of the window's "
                            "trailing edge fires at window_margin_px 34"},
        "negative": {"bands": [text_clear], "window": SYNTH_WINDOW,
                     "margin": COL_GAP, "fires": False,
                     "why": "the same band stopping 40 px short must not fire -- "
                            "a checker with no negative is a checker that cannot "
                            "fail"},
        "sensitivity": {"bands": [text_clear],
                        "window": (SYNTH_WINDOW[0], SYNTH_WINDOW[1],
                                   SYNTH_WINDOW[2] - 40, SYNTH_WINDOW[3]),
                        "margin": COL_GAP, "fires": True,
                        "why": "narrowing the declared window by 40 px turns the "
                               "negative into a positive, proving the predicate "
                               "reads the window rather than a constant"},
        "band-height": {"bands": [text_clear, plate], "window": SYNTH_WINDOW,
                        "margin": COL_GAP, "fires": False,
                        "why": "the full-width plate at 3x text height must be "
                               "discarded by the band-height filter; without the "
                               "filter W1 fires on it",
                        "unfiltered_fires": True},
        "ornament-bridge": {"bands": ornament_bridge_bands(),
                            "window": SYNTH_WINDOW,
                            "margin": COL_GAP, "fires": False,
                            "why": "four text lines whose gaps are bridged by "
                                   "ornament ink, plus a plate carrying more ink "
                                   "than any one of them: build-masks.py:116's "
                                   "segmenter merges all five into one full-width "
                                   "band and fires at 0, typeset-cards.py:721's "
                                   "cut separates them and the plate is dropped",
                            "merged_by_line_groups": True},
    }


def ornament_bridge_bands():
    """Case 5's geometry: bridged gaps and a plate heavier than any text band.

    THE BRIDGE IS TWO INTERLEAVED LATTICES AND HAS TO BE. It must put ink in
    EVERY row between the first text line and the plate's foot -- that is the
    whole point, since a single blank row hands `_ink_runs` a separator and the
    case stops testing the cut. But a column that is vertically continuous over
    234 rows is a RULE by `rule_stroke_mask`'s definition unless it is ~20 px
    wide, and a 20 px solid column has no light pixel within INK_RADIUS of its
    interior so `ink_mask` keeps only its 5 px rims -- which are rules again. Two
    6 px lattices in ANTIPHASE (4 rows on, 4 off, offset by 4) and at disjoint x
    solve all three at once: 4 px vertical runs are under RULE_MIN_LEN, the 2 px
    horizontal gaps keep the dark-on-light detector satisfied, and their union
    leaves no blank row.

    The plate is the same vertically-solid 1/3-density lattice `w1_cases()` uses,
    at 90 rows and full window width. It carries ~16.7k ink px against ~10.7k for
    any single text band -- the corpus relation, and the one the earlier
    `band-height` case had backwards.
    """
    left = SYNTH_WINDOW[0]
    right = SYNTH_WINDOW[2] - 1
    text_right = right - 40                      # the declared 40 px clearance
    tops = (40, 76, 112, 148)                    # 30 px lines, 6 px gaps
    bands = [dict(top=t, height=30, left=left + 30, right=text_right,
                  block=(6, 30), period=(8, 30)) for t in tops]
    bands.append(dict(top=184, height=90, left=left, right=right,
                      block=(10, 6), period=(30, 6)))
    bands.append(dict(top=40, height=234, left=left, right=left + 5,
                      block=(6, 4), period=(16, 8)))
    bands.append(dict(top=44, height=230, left=left + 12, right=left + 17,
                      block=(6, 4), period=(16, 8)))
    return bands


def legacy_line_group_bands(ink, window, min_ink=LINE_MIN_INK):
    """build-masks.py:116's row segmentation -- THE NEGATIVE CONTROL, nothing else.

    `measure_bands` forked this until 2026-08-24 and it is kept here for one
    reason: a fixture that reproduces the defect is worthless unless something
    asserts that it still reproduces it. `w1_cases()["ornament-bridge"]` is
    checked BOTH ways, so the day this returns the same answer as `measure_bands`
    on that case is the day the case has stopped discriminating and says so.
    It is not called by W1, by W2 or by `kz_mask`.
    """
    import numpy as np
    x0, y0, x1, y1 = window
    height, width = ink.shape
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(width, int(x1)), min(height, int(y1))
    sub = ink[y0:y1, x0:x1]
    if not sub.any():
        return [], None
    text_ink = sub & ~rule_stroke_mask(sub)
    if not text_ink.any():
        return [], None
    profile = text_ink.sum(axis=1).astype(np.int64)
    bands = []
    for top, bottom in _row_runs(profile >= min_ink):
        cols = np.nonzero(text_ink[top:bottom + 1].any(axis=0))[0]
        if cols.size:
            bands.append((int(top), int(bottom), int(cols[0]), int(cols[-1])))
    if not bands:
        return [], None
    return bands, _ink_weighted_median_height(bands, profile)


def w2_cases():
    """W2's TWO synthetic cases (§5.5). W2 had no enumerated synthetic case at
    all and was resting entirely on the corpus -- the position W1 was rescued
    from. Both are built against a DECLARED mask, never one the checker derives,
    so the assertion stays clear of the circularity §5.5 property 1 forbids.
    """
    left = SYNTH_WINDOW[0]
    band = dict(top=60, height=30, left=left, right=519,
                block=(6, 30), period=(8, 30))
    return {
        "overhang": {"bands": [band], "window": SYNTH_WINDOW,
                     "clear": (20, 50, 507, 100), "fires": True,
                     "overhang_right": 12,
                     "why": "a located line box overhanging CLEAR by 12 px fires "
                            "and names the band and the overhang"},
        "contained": {"bands": [band], "window": SYNTH_WINDOW,
                      "clear": (10, 50, 540, 110), "fires": False,
                      "why": "the same box entirely inside CLEAR must not fire"},
    }


def run_w1_case(case, filtered=True):
    """Evaluate one `w1_cases()` entry; returns the hit list."""
    image = synth_slice(SYNTH_SIZE, case["bands"])
    ink = ink_mask(image)
    if filtered:
        return w1_window_margin(ink, {"body": case["window"]}, case["margin"],
                                SYNTH_GROUP, SYNTH_FACE)
    # The unfiltered comparison the band-height case turns on: identical except
    # that every band is evaluated, which is what deleting the filter would do.
    bands, _en_ink_h = measure_bands(ink, case["window"])
    x0, _y0, x1, y1 = case["window"]
    width, height = x1 - x0, y1 - _y0
    return [b for b in bands
            if min((width - 1) - b[3], (height - 1) - b[1]) < case["margin"]]


def legacy_case_fires(case):
    """Would `case` fire if `measure_bands` relapsed to `line_groups`' segmentation?

    The negative control for the SEGMENTATION, exactly as `filtered=False` is the
    negative control for the band-height filter. `ornament-bridge` must answer
    True here and False through `run_w1_case`, and a fixture that answers the
    same both ways is a fixture that has stopped testing the fix.
    """
    image = synth_slice(SYNTH_SIZE, case["bands"])
    ink = ink_mask(image)
    x0, y0, x1, y1 = case["window"]
    width, height = x1 - x0, y1 - y0
    bands, en_ink_h = legacy_line_group_bands(ink, case["window"])
    return any(min((width - 1) - b[3], (height - 1) - b[1]) < case["margin"]
               for b in text_bands(bands, en_ink_h))


def run_w2_case(case):
    """Evaluate one `w2_cases()` entry; returns the hit list."""
    import numpy as np
    image = synth_slice(SYNTH_SIZE, case["bands"])
    gray = np.asarray(image)
    cx0, cy0, cx1, cy1 = case["clear"]
    clear = np.zeros((SYNTH_SIZE[1], SYNTH_SIZE[0]), dtype=bool)
    clear[cy0:cy1 + 1, cx0:cx1 + 1] = True
    return w2_line_containment(gray, clear, {"body": case["window"]},
                               SYNTH_GROUP, SYNTH_FACE)


#: The reference (ink_fill, advance_em) pair recorded in §5.6, and the ONLY pair
#: the project has actually measured. `swap` below is the fixture that moved here
#: from the kz_decide.py tree: the anti-swap invariant reports 67 and is not one
#: of the ten adjudication rules (§4.4), so it never belonged there.
ICON_REFERENCE = {
    "elder_thing": (0.47, 0.916),   # F25E -- a winged, tentacled silhouette
    "tablet": (0.66, 0.826),        # F260 -- a solid slab
}


def icon_cases():
    """The S4 anti-swap cases, as {name: case}. PURE -- no font, no corpus.

    The font-borne measurement is a `needs_golden` extra for the same reason the
    corpus containment case is: it is the half that can silently vanish. What
    must hold on every machine is that a SWAPPED declaration is caught, and that
    a map containing an indistinguishable pair is refused at authoring time.
    """
    swapped = {"elder_thing": ICON_REFERENCE["tablet"],
               "tablet": ICON_REFERENCE["elder_thing"]}
    close = {"elder_thing": (0.47, 0.916), "tablet": (0.48, 0.918)}
    return {
        "good": {"declared": dict(ICON_REFERENCE), "measured": dict(ICON_REFERENCE),
                 "declaration_findings": 0, "separation_findings": 0,
                 "why": "the recorded reference pair passes both halves"},
        # THE `swap/` FIXTURE. Four findings, not two: a swap makes BOTH
        # declarations wrong at once, in BOTH dimensions, which is the property
        # that makes it detectable at all when every downstream stage is
        # value-transparent.
        "swap": {"declared": swapped, "measured": dict(ICON_REFERENCE),
                 "declaration_findings": 4, "separation_findings": 0,
                 "why": "tablet and elder_thing exchanged: both declarations are "
                        "wrong in both dimensions at once"},
        "indistinguishable": {"declared": close, "measured": close,
                              "declaration_findings": 0,
                              "separation_findings": 1,
                              "why": "a pair 0.014 apart could be interchanged "
                                     "undetectably and is refused at map-"
                                     "authoring time"},
    }


def selftest(fault=None, verbose=True):
    """Prove each predicate fires on the fault it targets AND stays quiet on its
    negative. Returns a list of findings; empty means every case held."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    if "tokenizer" in wanted:
        icons, refs, tags = scan("[skull]: -X [[Guest]] <b>bold</b> [combat]")
        if icons != ["combat", "skull"]:
            findings.append("tokenizer: icons %r" % (icons,))
        if refs != 1:
            findings.append("tokenizer: trait refs %d != 1" % refs)
        if tags.get("b") != 2:
            findings.append("tokenizer: <b> count %r != 2" % tags.get("b"))
        # WHICH tokens, not just how many -- the substitution a count cannot see.
        if not markup_delta("[combat] x", "[agility] x"):
            findings.append("tokenizer: [combat] -> [agility] was not caught")
        if markup_delta("[combat] x", "[combat] 가"):
            findings.append("tokenizer: a correct translation was flagged")
        # `[[Trait]]` must not tokenize as an icon named `[Trait`.
        if scan("[[Guest]]")[0]:
            findings.append("tokenizer: [[Trait]] tokenized as an icon")
        if latin_runs("<b>E</b> 한글 [[Guest]]"):
            findings.append("tokenizer: a construct-only field reported residual "
                            "English")

    if "jongseong" in wanted:
        # The recorded defect, and the one the generator produced five times.
        sites, _skipped = scan_particles("주요목적를 완료하십시오")
        flags = [s for s in sites if s["status"] == "flag"]
        if len(flags) != 1 or flags[0]["expected"] != "cons":
            findings.append("jongseong: 주요목적를 did not flag (%r)" % (sites,))
        if scan_particles("주요목적을 완료하십시오")[0][0]["status"] != "ok":
            findings.append("jongseong: the CORRECTED form was flagged")
        # The lexicon: the bare jamo rule flags these and must not survive it.
        for word, tail in (("있는", "조사자"), ("전문가", "가"), ("사이", "가")):
            text = "%s %s" % (word, tail)
            statuses = [s["status"] for s in scan_particles(text)[0]]
            if "flag" in statuses:
                findings.append("jongseong: %r was not suppressed by the lexicon"
                                % text)
        # ... and the lexicon must not suppress a DIFFERENT pair's defect.
        with_lex = scan_particles("있는를", use_lexicon=True)[0]
        if not any(s["status"] == "flag" for s in with_lex):
            findings.append("jongseong: the lexicon masked an object-particle "
                            "defect on a topic-particle word")
        # The eojeol-boundary rule removes a candidate BEFORE it is a site.
        if any(s["status"] == "flag"
               for s in scan_particles("어딘가에서 왔습니다")[0]):
            findings.append("jongseong: 어딘가에서 became a site")
        # The ㄹ carve-out is PER PAIR, never global.
        if expected_form("instrumental", "칼", "vowel") != "vowel":
            findings.append("jongseong: 으로/로 lost its rieul carve-out")
        if expected_form("object", "칼", "cons") != "cons":
            findings.append("jongseong: the rieul carve-out leaked into 을/를")

    if "icons" in wanted:
        for name, case in sorted(icon_cases().items()):
            got = check_icon_declarations(case["declared"], case["measured"])
            if len(got) != case["declaration_findings"]:
                findings.append("icons/%s: %d declaration findings, expected %d "
                                "(%s)" % (name, len(got),
                                          case["declaration_findings"], got))
            sep = check_icon_separation(case["declared"])
            if len(sep) != case["separation_findings"]:
                findings.append("icons/%s: %d separation findings, expected %d "
                                "(%s)" % (name, len(sep),
                                          case["separation_findings"], sep))

    if "w1" in wanted:
        for name, case in sorted(w1_cases().items()):
            hits = run_w1_case(case)
            if bool(hits) != case["fires"]:
                findings.append("w1/%s: fired=%s, expected %s -- %s"
                                % (name, bool(hits), case["fires"], case["why"]))
            if case["fires"] and hits:
                hit = hits[0]
                if hit["group"] != SYNTH_GROUP or hit["window_name"] != "body":
                    findings.append("w1/%s: the hit did not name its group and "
                                    "window" % name)
            if case.get("unfiltered_fires"):
                # The band-height filter's own negative: without it, the plate
                # fires. A filter nothing exercises is a filter that can be
                # deleted silently.
                if not run_w1_case(case, filtered=False):
                    findings.append("w1/%s: the case does not fire WITHOUT the "
                                    "band-height filter, so it cannot catch the "
                                    "filter's omission" % name)
            if case.get("merged_by_line_groups"):
                # The SEGMENTATION's own negative, and the one whose absence let
                # the defect ship: the case must still be one merged, firing band
                # under build-masks.py:116's segmenter. The day it is not, the
                # fixture has stopped discriminating between the two and the
                # green light it gives is worth nothing.
                if not legacy_case_fires(case):
                    findings.append("w1/%s: the case does NOT fire under "
                                    "build-masks.py:116's segmentation, so it "
                                    "cannot catch a relapse to it" % name)

    if "w2" in wanted:
        for name, case in sorted(w2_cases().items()):
            hits = run_w2_case(case)
            if bool(hits) != case["fires"]:
                findings.append("w2/%s: fired=%s, expected %s -- %s"
                                % (name, bool(hits), case["fires"], case["why"]))
            if case.get("overhang_right") is not None and hits:
                if hits[0]["overhang_right"] != case["overhang_right"]:
                    findings.append("w2/%s: overhang_right %d, expected %d"
                                    % (name, hits[0]["overhang_right"],
                                       case["overhang_right"]))

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ===========================================================================
# 7. CLI
# ===========================================================================

_SUMMARY = {
    "tokenizer": "the hand-written tokenizer counts icons, [[trait]] refs and "
                 "tags, and catches a token SUBSTITUTION a count cannot see",
    "jongseong": "주요목적를 flags, 주요목적을 does not, the lexicon suppresses "
                 "its 18 words without masking a different pair's defect, and "
                 "the rieul carve-out stays per-pair",
    "icons": "a tablet/elder_thing swap produces four findings at once, and an "
             "indistinguishable pair is refused at map-authoring time",
    "w1": "27 px fires, 40 px does not, narrowing the window turns the negative "
          "into a positive, the full-width plate is discarded by the "
          "band-height filter (and fires without it), and four lines whose gaps "
          "are bridged by ornament ink stay four lines (and merge into one "
          "firing band under build-masks.py:116's segmentation)",
    "w2": "a line box overhanging CLEAR by 12 px fires and names the overhang; "
          "the same box inside CLEAR does not",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_checkers.py",
        description="koreanize `check` + the independent verifiers "
                    "(design §5.5, §5.6, §9).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--en", help="override card-text-en.json")
    parser.add_argument("--ko", help="override card-text-ko.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="write <run_dir>/dry-run/check/check.dry-run.json")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-check an existing artifact; writes "
                             "check.verify.json, never the marker")
    parser.add_argument("--json-only", action="store_true",
                        help="emit the report JSON on stdout and nothing else")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_checkers --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-10s %s" % (name, _SUMMARY[name]))
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    mode = "build"
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"

    report = run_check(run_dir, mode=mode, en_path=args.en, ko_path=args.ko)

    dest = run_dir
    if args.dry_run:
        # §4.1: every stage's --dry-run output goes to <run_dir>/dry-run/<stage>/,
        # and kz_config asserts the destination is under <run_dir> before the
        # first byte.
        dest = os.path.join(run_dir, "dry-run", STAGE)
        kz.assert_dry_run_dest(kz.load_scenario(os.path.join(run_dir,
                                                             "scenario.json")),
                               STAGE, dest)
    path = kc.write_report(report, dest)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize check -- %s" % report["slug"])
        print("  cards           : %d English / %d Korean (%d checked)"
              % (counts["cards_en"], counts["cards_ko"], counts["cards_checked"]))
        print("  particles       : %d flagged, %d suppressed by the lexicon, "
              "%d by the eojeol boundary"
              % (counts["particle_flags"], counts["particle_suppressed"],
                 counts["particle_suppressed_eojeol_boundary"]))
        print("  residual English: %d (reported, never fatal)"
              % counts["residual_english"])
        for check in report["checks"]:
            print("  %-18s: %s  %s"
                  % (check["name"], check["status"],
                     "; ".join(check["detail"][:3])))
        print("  verdict         : %s (exit %d)"
              % (report["verdict"], report["exit_code"]))
        print("  consumable      : %s%s"
              % (report["consumable"],
                 "" if report["consumable"]
                 else " (%s)" % report["consumable_blocked_by"]))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
