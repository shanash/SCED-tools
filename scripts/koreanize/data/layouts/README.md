# `data/layouts/` — the durable-inputs tier

`<set>.json`, one per **card-frame set** — not per scenario. Design §3.1 splits
koreanize's artifacts by **durability**, not by convenience, and this directory
holds the one the art chain cannot re-derive: a hand-measured window catalogue is
a person with a pixel ruler, and losing it costs that person's afternoon again.

`scenario.json`'s `layout_set` names which file a run reads; it defaults to the
slug, so a scenario printed on frames nobody has measured gets its own file and a
scenario sharing another set's frames points at that set instead.

## What one file is

The catalogue `build-masks.py:207-507` carried as fifteen module-level dicts,
moved here as data. Per group:

| field | what it is |
|---|---|
| `rot` | the rotation applied before anything is measured. Act and Agenda are stored rotated 90 CW and measured **upright at 1050x750**; everything else is `0` |
| `working_size` | the dimensions the rectangles below are expressed in, **after** `rot` |
| `windows` | the region search windows, `(x0, y0, x1, y1)`, half-open on `x1`/`y1`, in the group's working orientation |
| `frame_windows` | windows for text printed on the **frame** (type banner, stage label). Measured the same way, but subtracted from `protect` before protection is painted (the A6b ordering invariant), and always searched — frame text is a property of the frame, not of a `card_source` field |
| `protect` | rectangles repainted opaque after the regions are painted |
| `flavor_before_text` | true where the flavour box precedes the rules text |
| `measured_from[]` | **the exact slice filenames the windows were read off.** Not a citation — the reproduction instruction |
| `measured_notes_key` | which `measured_notes{}` entry documents this group's measurement, verbatim from `build-masks.py:220` |
| `window_margin_px` | the W1 threshold for this group (below) |
| `calibrated_from` | the negative half of that threshold (below) |
| `calibration{}` | the sweep that produced both, per window, with the band that drove each minimum |

`col_gap` is top-level and is `34` — `build-masks.py:85`, *"columns of blank that
still count as one glyph run"*. It is the reference `window_margin_px` and the
literal `calibrated_from` value `"COL_GAP"` resolves to it.

## `window_margin_px` and `calibrated_from` — the pair, not the number

W1 (§5.5, `kz_checkers.w1_window_margin`) asserts that every located text band
inside a window leaves at least `window_margin_px` of non-ink between its extent
and the window's **trailing** edges — right and bottom in the group's working
orientation. If a band does not, **the window itself is the defect**: it is not
wider than the content it is supposed to bound, so the padded rects derived from
it cannot reach the line ends and W2 is vacuous.

`window_margin_px` alone is half a threshold. A threshold earns its number by
firing on the defects **and not on the rest**, so `calibrated_from` sits beside it
and records the tightest post-suppression trailing clearance measured on a
**defect-free** face in that group — that is, the largest threshold at which the
group's own negative evidence still passes. `kz_config.check_window_margin()`
asserts `1 <= window_margin_px <= calibrated_from <= 64` and refuses at **exit 4**
naming the group and both values. Recording the negative half and never comparing
against it is the same defect as a checker with no negative test.

`calibrated_from` may also be the literal string `"COL_GAP"` — the honest label
for a group with **no defect-free face**. Only the numeric bound applies then, and
`--status` prints such a group as **uncalibrated** so the weaker claim stays
visible instead of reading like a measurement.

## The Midwinter sweep, 2026-08-24

Run with `kz_checkers.measure_bands()` / `text_bands()` / `w1_window_margin()` —
i.e. **post-`rule_stroke_mask()` suppression** and behind `measure_en_optics()`'s
ink-weighted band-height filter, which the 2026-08-20 reconnaissance in §5.5 was
not. 88 faces, 15 groups, 971 text bands, on the art tier (3.14.7 / PIL 12.0.0 /
numpy 2.4.2).

| group | `window_margin_px` | `calibrated_from` | defect-free faces |
|---|---|---|---|
| `Act/back` | *(absent)* | 0 | 2 / 2 |
| `Act/front` | **34** | `"COL_GAP"` | 0 / 2 |
| `Agenda/back` | *(absent)* | 0 | 3 / 3 |
| `Agenda/front` | *(absent)* | 0 | 3 / 3 |
| `Asset/bside` | *(absent)* | 0 | 1 / 1 |
| `Asset/front` | *(absent)* | 0 | 22 / 22 |
| `Enemy/bside` | *(absent)* | 0 | 6 / 6 |
| `Enemy/front` | *(absent)* | 0 | 7 / 7 |
| `Location/back` | *(absent)* | 0 | 8 / 8 |
| `Location/front` | **2** | 2 | 8 / 8 |
| `Scenario/back` | **11** | 11 | 1 / 1 |
| `Scenario/front` | **10** | 10 | 1 / 1 |
| `Story/bside` | *(absent)* | 0 | 5 / 5 |
| `Story/front` | *(absent)* | 0 | 5 / 5 |
| `Treachery/front` | *(absent)* | 0 | 14 / 14 |

**`Act/front` reproduces the reference anchors.** Its two faces are both the known
`mask-coverage-finding.md` defects, so it has no negative example at all and its
threshold is inherited from `COL_GAP` by §5.5's argument rather than measured.
Post-suppression, on the `body` window `(26, 192, 558, 666)` at `rot: 90` /
`1050x750`, the tightest trailing clearance is **27 px** on `71006` (band
`y[181,278] x[1,504]` — `531 - 504 = 27`) and **15 px** on `71005` (band
`y[234,296] x[3,516]` — `531 - 516 = 15`). Both are right margins and both fire at
`window_margin_px = 34`, which is what the threshold was chosen to do.

The band **extents** differ from §5.5's prose (`y[215,249]` and `y[234,263]`)
because this segmentation merges adjacent lines that §5.5's did — see the next
section — but the quantities W1 thresholds on, the `x1` values `504` and `516`,
are identical, as are the whole-window profiles §5.5 records and discards
(`x[0,531]` on `71006`, `x[3,531]` on `71005`).

## The zero finding

**Eleven of the fifteen groups measure a tightest trailing clearance of 0 px on
their own defect-free faces.** §5.5 anticipates this outcome and names it: *"A
group whose post-suppression median is still 0 is a **finding**, not a threshold
problem: it means the windows in that group hug their content, which is W1's
hypothesis, and it goes through step 16's adjudication like any other hit."*

Two mechanisms produce it, and both are visible in `calibration.per_window`:

1. **The bottom edge is structurally tight.** A window measured to bound its
   content ends a few pixels below the last line it contains, so the last band's
   bottom clearance is small by construction. `frame_windows` make this
   unavoidable rather than merely likely: `Act/front`'s `stage_label` window is
   33 px tall and holds a 25 px line, so **no** threshold above 8 can pass it, and
   `Asset/front`'s `type_banner` is 19 px tall. A group containing a frame window
   cannot be calibrated above that window's own height.
2. **~~Full-width non-text bands survive the band-height filter.~~ FIXED
   2026-08-24 — kept here because it is the more instructive half.** The filter
   keeps bands whose height is within `[0.5, 2.0] * en_ink_h`, where `en_ink_h` is
   the ink-weighted median band height *of the bands themselves*. Where
   segmentation merged several printed lines into one band — which
   `build-masks.py`'s `LINE_MIN_INK = 2` / `LINE_GAP = 4` does wherever 5–10 px of
   ornament ink bridges the gap between lines — `en_ink_h` was dragged **up** to
   the merged height, and the plate the filter exists to reject fell inside the
   window. On `Act/front`'s `71006` body `en_ink_h` measured **98**, not the
   20–45 px `typeset-cards.py:3857` pins as plausible: the `y[301,473]` merged
   band was kept at `right = 0`, while the real text bands at `h = 33` and
   `h = 30` were **discarded**. W1 was measuring the plate.

   The cause was that `kz_checkers.measure_bands()` had been written as a fork of
   `build-masks.py`'s `line_groups()`, when §5.5 requires a fork of
   `typeset-cards.py`'s `measure_en_lines()` (`:721`) — whose own docstring warns
   that merged rects are *"unusable as a ruler"*. The repair restored the
   core-splitting step (`ROW_CORE_FRAC`, cut at the profile argmin between
   cores). After it: 10 and 11 bands, `en_ink_h` 33 and 30, plate dropped, and
   the two known hits fire from the **text** at 27 px and 15 px — exactly §5.5's
   recorded figures.

**Mechanism 2 turned out not to be what produced the zeros, and that is why it is
still written down.** Repairing it took the corpus from 564 resolved text bands to
971, yet only 10 of 88 faces changed their tightest clearance at all and the zero
count moved just 12 → 11. In every remaining zero group the floor is set by
mechanism 1 — a band that genuinely reaches its window's trailing edge, usually in
a different window from the one the plate lives in. Mechanism 2 was decisive only
on `Act/front`, the one group that contributes no calibration because both its
faces are the defects. The obvious suspect was real, was repaired, and was not the
cause.


### Why the eleven carry no `window_margin_px` at all

`kz_config.check_window_margin()` requires `1 <= window_margin_px <=
calibrated_from`. At `calibrated_from == 0` **no** value satisfies it, so any
number written there would refuse at exit 4 — including the reference 34, and
including 1. The validator skips a group that declares no `window_margin_px`, so
the eleven declare `calibrated_from: 0`, `uncalibrated: true` and an
`uncalibrated_reason`, and the file passes `check_window_margin()` with an empty
finding list.

That is a deliberately weaker claim, honestly labelled: **W1 is inert on those
eleven groups**, and the segmentation finding above is now settled without moving
any of them -- so this is the corpus's own geometry rather than a repairable
measurement defect, and it belongs in step 16's adjudication queue. `kz_mask`
accepts such a group only when it SAYS SO, with `uncalibrated: true` and a
non-empty `uncalibrated_reason`; it then reports the group as **unasserted
coverage** in the stage report and prints it as UNASSERTED in `--status`. An
omission is still refused at exit 4, so the distinction the file carries is
between a declaration and a forgotten key. W1 is live on
`Act/front` — the group that carries the two defects the check was built to catch
— plus `Location/front`, `Scenario/front` and `Scenario/back`. Writing 34
everywhere would have been the alternative and is exactly the corpus-absorbing
failure §5.5 rejects the four-sided formulation for: at a flat 34 the sweep fires
on 86 of 86 non-defect faces, `mask.residual_baseline[]` would have to absorb the
whole corpus, and step 16's adjudication queue would stop being a queue.

## Who writes here

Nothing writes here directly. `kz_mask.py` (S3) hands the proposed catalogue to
**`kz_config.write_data()`** — the mirror writer, and the only code in the tool
that opens a path under `SCED-tools/` for writing. That is what keeps §4.1's
*"writes outside `<run_dir>`: no"* column literally true for `mask`: it declares
no filesystem write root outside `<run_dir>` and it cannot reach one. Writes here
never require `--live`, for the reason `data/scenarios/README.md` gives.

`midwinter.json` is the exception that proves the rule and says so in its own
`provenance{}`: it was **imported** by §6 step 8 from a pre-koreanize script, not
produced by `mask`, because it is the corpus every later step is checked against
and it had to exist before the stage that would otherwise have written it.

## Because S3 is an AI stage

`window_margin_px` is proposed by a model for an unseen set, so it is bounded
rather than trusted: `1 <= window_margin_px <= 64`, refused at exit 4 naming the
group. `64` is the ceiling because a margin wider than one `COL_GAP` on each side
of a 532 px window starts refusing legitimate full-width layouts.
`test_koreanize_config.py` carries **0 / 1 / 64 / 65** as four cases — a bound
with no boundary case is a bound nobody has run.
