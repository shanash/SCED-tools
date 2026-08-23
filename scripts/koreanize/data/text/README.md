# `data/text/` — the durable-inputs tier

`<slug>-ko.py`, one per scenario: **the Korean card-text generator**, not the
Korean card text. Design §3.1 splits koreanize's artifacts by **durability**, and
this is the tier that has to survive, because everything below it is derived.

## The output is a generator, and that is the whole design

`translate` (S2) emits a Python file carrying a `KO[...]` table and the code that
renders it into `card-text-ko.json`. It does **not** emit the JSON. Three reasons,
and the third is the one that matters:

1. **Reproducible.** Re-running the generator produces byte-identical JSON, so the
   artifact's serialization — `indent=1`, `ensure_ascii=False`, one trailing
   newline — never passes through human hands.
2. **Reviewable as a diff.** A table of Korean strings is something a reviewer can
   read, and a change to one card is one hunk. Multi-line fields are emitted one
   **source** line per **text** line for exactly this reason.
3. **A defect is fixed in the thing that produces it.** This is not a preference.

## Why (3) is not a preference

The Midwinter record's `주요목적를` — a wrong grammatical particle — appeared
**five times**, and all five came from one construction in the generator. Repair
the JSON and the next run reproduces all five. `check-particles.py:30-36` states
the consequence in full:

> `--fix` NEVER writes `card-text-ko.json`. That artifact is produced only by
> re-running the generator, which is what keeps its serialization out of human
> hands. A consequence, and it is designed, not a bug: **`--fix` ALONE DOES NOT
> TURN THIS CHECKER GREEN.** It goes green after the generator has been re-run and
> the regenerated JSON moved into place.

`kz_checkers.py` therefore has no `--fix` at all, and `audit`(S7) exists to widen
a single found instance into the predicate that catches its siblings **in the
generator**.

## Running one

```
python3 <slug>-ko.py --source <run_dir>/card-source-en.json \
                     --out    <run_dir>/card-text-ko.json
koreanize.sh check --slug <slug>
```

`render()` is one-directional: `KO` supplies Korean text, the English source
supplies identity, and a card `KO` does not carry is emitted with its English
text. So the output is **total over the source** rather than silently short, and a
card nobody has translated is visible as English rather than absent.

## What is deliberately not in the table

A `passthrough` ruling writes **no** `KO` row. A passthrough is a decision that
the English stands, so writing it into the table would restate the source as
though it were a translation and make the two indistinguishable in a diff.
`render()`'s fallback already produces identical bytes, so the behaviour is the
same and the record is honest.

Identity fields — `guid`, `card_id`, `deck_key`, `cell`, `sheet`, `pack` — are
copied from the English source and never restated in `KO`, so a translation
cannot move one. `kz_checkers.py`'s `CH4` refuses a run that does.

## Who writes here

Nothing writes here directly. `kz_translate.py` hands the assembled source text to
**`kz_config.write_data()`** — the mirror writer, and the only code in the tool
that opens a path under `SCED-tools/` for writing. The generator is `compile()`d
before it is written: a durable-tier artifact that does not parse is one a restore
cannot execute, and checking is cheap.

Writes here never require `--live`, for the reason `data/scenarios/README.md`
gives.

## What checks it

`check` (`kz_checkers.py`) re-validates the **rendered** JSON with a
hand-written tokenizer that shares no code with the generator — the rule
`verify-a4.py:4-7` states:

> This is deliberately NOT a re-run of `translate-a3.py`'s own `check()`. A3
> validates with regex multisets; this file scans character by character with a
> hand-written tokenizer, so a bug in one regex cannot pass both.

It checks id coverage, field parity, markup multisets (**which** icon tokens, not
just how many), identity, the `card_id == deck_key * 100 + cell` arithmetic, the
을/를 jongseong audit with its false-positive lexicon, and — reported, never fatal
— residual English.
