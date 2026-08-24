# `data/icons/` — the durable-inputs tier

`<set>.json`, one per **icon set** — not per scenario, which is why step 8's file
is `core.json`. `scenario.json`'s `icon_set` names it and defaults to `"core"`
(`kz_init.build_scenario_config(..., icon_set="core")`). Design §3.1 splits
koreanize's artifacts by **durability**; this one is here because the map it
carries is a set of *identity* claims about PUA codepoints, and identity is
exactly what no downstream check can re-derive.

## What one file is

An envelope in the house shape — `{schema_version, generated_by, generated_at,
set, icons{}}`, key-sorted throughout (`kz_common.sorted_mapping()`) — plus a
`font{}` block naming the face the figures were measured from and a
`provenance{}` block. `icons{}` is keyed by **token name** and every entry is
flat:

| field | what it is |
|---|---|
| `codepoint` | `"U+XXXX"`, uppercase hex. A **codepoint, never a literal**: a PUA literal is invisible in a diff and silently corruptible by any tool in the chain |
| `ink_fill` | the fraction of the glyph's own bounding box that is inked, `0..1` |
| `advance_em` | the glyph's advance divided by the point size, `0..8` |
| `ko` | the Korean *reference name* — what the campaign guide and the prose around a rules line call the icon. **Never a substitution**: the token renders as a font glyph and `translate` preserves it verbatim |
| `source` / `confidence` / `rationale` | the S1 verdict vocabulary, as in `data/terms/` |
| `occurrences` | how many times this **token** appears across the English cards. The S4 unit is a token, never an occurrence |

The value triple is fixed in code, not chosen here: `kz_decide.VALUE_SCHEMA["S4"]`
is `additionalProperties: False` over exactly `codepoint`, `ink_fill`,
`advance_em`, and `VALUE_REQUIRED["S4"]` makes all three mandatory. The reader
refuses at **exit 67** on anything else; the tolerant reader that would have
absorbed a divergence was deliberately removed, because absorbing it is how a
wrong `ink_fill` ships silently — the exact failure the invariant below exists to
catch.

## Why `(ink_fill, advance_em)` is declared at all — the anti-swap invariant

`ICON_MAP` shipped with **`tablet` and `elder_thing` swapped**, and nothing caught
it. The record's verdict:

> the preflight's `miss_icons` probe asserts INK, not identity, and
> `tokenise`/`para_words`/`seg_advance`/`draw_layout` are all value-transparent,
> so a human `accepted` verdict on 2026-08-18 could not have caught it.

The identity of a PUA codepoint is not mechanically decidable in general — but a
**swap of two specific glyphs** is, when the glyphs differ measurably. `tablet`
(U+F260) is a solid slab: narrower advance, denser ink. `elder_thing` (U+F25E) is
a winged silhouette: wider advance, sparser ink. Declaring the measured pair per
token turns "which glyph is this" from an unfalsifiable claim into two numbers
`kz_checkers` re-measures from the font outlines. The invariant has two halves and
both are necessary:

- **(a) `check_icon_declarations()`** — every declared pair equals the re-measured
  one, within `ICON_DECLARATION_TOL = 0.02`. This is what catches a swap.
- **(b) `check_icon_separation()`** — no two icons sit within
  `ICON_SEPARATION_MIN = 0.05` of each other in the `(ink_fill, advance_em)`
  plane. This refuses, **at map-authoring time**, a map containing a pair that (a)
  could not have caught — rather than shipping it and trusting it.

`core.json` carries the **corrected** pairing, and its `provenance.corrected_swap`
says so in the file itself.

## The reference pair

U+F25E `elder_thing` → `ink_fill 0.470`, `advance_em 0.916`.
U+F260 `tablet` → `ink_fill 0.659`, `advance_em 0.826`.

Both re-measured on 2026-08-24 from `_font_ArkhamIcons.ttf` by
`kz_checkers.measure_icon_metrics()` at probe size 200 / advance size 32, and both
reproduce the recorded reference to three decimals. They sit at distance
`sqrt(0.19^2 + 0.09^2) = 0.210`, which is why `ICON_SEPARATION_MIN` is set at
`0.05` — roughly 4x inside the one pair the project has actually measured, and
deliberately not tuned to it: a margin set **at** the reference distance would
pass that pair and nothing else.

## `[fast]` is 2.27 em and is not a defect

`typeset-cards.py`'s `NOTES[]` pins it, and an empty `notes[]` is how a fact like
this gets silently "corrected" by the next reader:

> `[fast]` (U+F25A) has an advance of 2.27 em — roughly **twice** every other
> icon. That is the glyph's true shape (a wide double zigzag), verified against
> `slices/71017.png`, not a defect and not a font-loading error. It is measured by
> advance like every other run.

The `advance_em` bound is `0..8` for exactly this reason.

## The separation finding

Half (b) **does not pass on this font today.** Measured 2026-08-24 over all 19
declared tokens, four pairs sit at or under the 0.05 margin:

| pair | separation |
|---|---|
| `cultist` / `per_investigator` | 0.005 |
| `mystic` / `reaction` | 0.035 |
| `auto_fail` / `guardian` | 0.042 |
| `skull` / `willpower` | 0.048 |

This is recorded rather than tuned away, and lowering `ICON_SEPARATION_MIN` to
make it pass would delete the check. What it says is precise and worth stating
plainly: **for those four pairs, half (a) cannot distinguish a swap**, because the
two glyphs' `(ink_fill, advance_em)` points are closer together than the
measurement's own tolerance can resolve. Half (a) still protects the other 15
tokens, and it still protects `tablet`/`elder_thing` — the pair the actual defect
occurred on, which sits at 0.210, forty times the closest of these four.

Two follow-ups belong to §6 step 14 (`kz_typeset.py` + S4), not to step 8, which
is a data import with no code dependency:

1. Decide whether half (b) is a **refusal** or a **declared residual**. As
   specified it refuses at authoring time, which on this font means `core.json`
   cannot be authored at all — so either the margin is a per-set field, or those
   four pairs are baselined the way `mask.residual_baseline[]` baselines a W1 hit.
2. If a third discriminating dimension is wanted, it has to be one a swap cannot
   preserve. Bounding-box aspect ratio is the obvious candidate and is untested.

## Who writes here

Nothing writes here directly. `kz_typeset.py` (S4) hands the proposed map to
**`kz_config.write_data()`** — the mirror writer, and the only code in the tool
that opens a path under `SCED-tools/` for writing. Writes here never require
`--live`, for the reason `data/scenarios/README.md` gives.

`core.json` was **imported** by §6 step 8 from `typeset-cards.py:300-307` rather
than produced by `typeset`, because §6 step 15's `golden --fixture midwinter`
consumes it and it had to exist before the stage that would otherwise have written
it.
