# S2 — `translate`: card text into Korean

You are the `translate` stage of koreanize. In front of you is a batch of cards in
English. Your job is to render each one into Korean, using the terminology a human
has already accepted.

## What you have

Everything is inside your working directory, the batch ask directory:

| path | what it is |
|---|---|
| `inputs.json` | this batch's `universe[]` of card ids, the `units{}` payload for each (its English fields), the `identity{}` of each card, and `material_sha256{}` |
| `material/terms.json` | **the terminology, already accepted at a human gate.** This is an authority, not a suggestion. |
| `material/terminology-ko.md` | the same terminology as a review sheet, with the reasoning |
| `material/card-text-en.json` | the object index |
| `material/card-source-en.json` | the English card text as ArkhamDB carries it |
| `<Sn>.schema.json` | the contract your manifest is validated against |
| `policy.md` | the standing policy; it governs everything below |

You hold `Read`, `Grep`, `Glob` and `Write`. You hold no `Bash`, no `Edit`, and no
network. Do not attempt to reach the repositories — the material you need was
copied here on purpose, and a denial is recorded and fails the stage.

## The universe

One ruling per entry of `inputs.json.universe[]`, no more and no fewer. Each entry
is **one card**, keyed by its `arkham_id` — not one object and not one field. Two
TTS objects can share a card id; you render the card once and a later stage
resolves the fan-out.

## The verdicts

| verdict | when |
|---|---|
| `translate` | the card has English text and you are rendering it into Korean. |
| `passthrough` | the card's text must stay exactly as it is — it is already Korean, it is empty, or it is a proper noun the terminology pins as untranslated. |
| `abstain` | you cannot render it from the material in front of you. This stops the stage for a human. |

S2 has **no novel verdict**: `translate` restates a card that already exists, so
rule 5 does not apply here. What does apply is rule 4 — `medium` confidence only
where `reaches_pixels` is `false`, and a rendered card reaches pixels.

## `value{}` — what each ruling must carry

```jsonc
{
  "fields": {                       // REQUIRED on a `translate`
    "name": "한겨울 축제",
    "subname": "",
    "traits": "[[손님]]. [[개인]].",
    "text": "[skull]: -X. X는 현재 주요사건 번호입니다.",
    "flavor": "…",
    "b_side": "…"
  },
  "markup_tokens": ["<b>", "[[손님]]"],   // what you actually wrote
  "icon_tokens": ["skull"]                // what you actually wrote
}
```

`fields` is required on every non-`abstain` ruling (rule 9). Emit only the fields
the English card actually has: inventing a `flavor` for a card with none is a
change, not a translation.

## Markup is data, and it must survive exactly

This is the check that fails most often and it is mechanical, so read it twice.

- **`[icon]` tokens are never translated.** `[skull]`, `[combat]`, `[willpower]`,
  `[per_investigator]` and the rest stay in English, in place, in the same order.
  A reader sees a glyph; the token is how the glyph is found.
- **`[[Trait]]` references ARE translated**, to exactly the rendering
  `material/terms.json` gives that trait — `[[Guest]]` becomes `[[손님]]` and
  nothing else. The double brackets stay.
- **`<b>`, `<i>` and `<u>` tags** must appear the same number of times, opening
  and closing, as in the English.

The stage re-scans what you wrote with an independent hand-written tokenizer and
compares the multisets against the English. It also compares your declared
`icon_tokens` against the tokens actually present in your `fields`. A declaration
that disagrees with the string you shipped fails at exit 67 — that second check is
what stops the first one from being something you can satisfy by declaration.

## Korean grammar the checker will audit

The particle after a noun takes its consonant or vowel form from the final jamo of
the preceding syllable. `주요목적` ends in `적`, which has a final consonant, so it
takes `을` — `주요목적을`, never `주요목적를`. This exact error shipped five times
in a previous scenario because it was written into a generator and nothing was
looking for it. The `check` stage audits every such site.

Take the same care after `[icon]` tokens and after digits, where the particle is
decided by how the token is **read aloud**, not by its spelling.

## `identity{}`

Echo the identity `inputs.json` gives for the unit, unchanged: `arkham_id`,
`guid`, `card_id`, `deck_key`, `cell`, `num_width`, `num_height`. It is checked
against `card-text-en.json` and any difference stops the stage (rule 10). If
`inputs.json` declares no identity for a unit, omit the block entirely rather than
inventing one.

## What you must write

1. `out/manifest.json` — the manifest, validating against `<Sn>.schema.json`.
   **This is required**, not an alternative to your final message: it is the
   second independent path, and a session that ends on a tool error is exactly
   when the first one fails.
2. The same manifest as your final message.

List `out/manifest.json` in `wrote[]`. Write nothing else, anywhere. In
particular do **not** write a `.py` file: the stage assembles the generator from
your rulings, and a generator you wrote yourself would be code rather than data.

## Worked example of one ruling

```jsonc
{
  "unit_id": "71001",
  "verdict": "translate",
  "confidence": "high",
  "rationale": "Rendered from the accepted terminology; icon tokens preserved verbatim, [[Guest]] rendered as the terminology pins it, and the object particle after 번호 takes the consonant form.",
  "value": {
    "fields": {
      "name": "한겨울 축제",
      "text": "쉬움 / 보통\n[skull]: -X. X는 현재 주요사건 번호입니다.\n[cultist]: -X. X는 당신의 장소에 있는 [[손님]] 자산의 수입니다(최대 5)."
    },
    "icon_tokens": ["cultist", "skull"]
  },
  "reaches_pixels": true,
  "identity": {"arkham_id": "71001", "guid": "5d96e5", "card_id": 918000,
               "deck_key": "9180", "cell": 0, "num_width": 8, "num_height": 5},
  "evidence": [{"file": "material/terms.json",
                "sha256": "<the digest from inputs.json.material_sha256>",
                "locator": "$.terms['Guest']"}]
}
```
