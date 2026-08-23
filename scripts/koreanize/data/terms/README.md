# `data/terms/` — the durable-inputs tier

`<slug>.json` and `<slug>-terminology-ko.md`, one pair per scenario. Design §3.1
splits koreanize's artifacts by **durability**, not by convenience, and this
directory is part of the tier whose loss costs the most: these are
human-reviewed judgement artifacts, and re-deriving them means re-reviewing them.

## Why this file gates the whole text chain

A term is decided **once** and applied **everywhere**. The Midwinter record puts
the asymmetry plainly:

> This file is the **only** term authority for A3 (card text), E2 (campaign
> guide) and D1 (object `Nickname`/`Description`). If a term is wrong here it is
> wrong 62 times downstream — that asymmetry is why the queue stops at this file.

So `scenario.json`'s `gates` block carries `terms: required`, and every stage
below `terms` refuses at exit **30** until a human writes `accepted` into
`<run_dir>/gates/terms-gate.md`. `translate` re-asserts the same gate itself
(exit 30), so invoking the module directly is not a way around it.

## The two files, and why there are two

| file | who reads it | what it is |
|---|---|---|
| `<slug>.json` | `translate`, `objtext`, the guide chain | the machine-readable authority: one entry per term with `ko`, `source`, `confidence`, `rationale`, `kind` and `occurrences` |
| `<slug>-terminology-ko.md` | a human | the review surface. `coined` rows are listed **first and separately**, because they are the only ones that need a decision rather than a check |

The JSON is what the pipeline reads; the Markdown is what a person reads. Correct
a rendering in **both**, or re-run the stage — a reviewer who edits only the
Markdown has changed nothing that ships.

### The review sheet carries the digest of the JSON it was built from

One is JSON and one is Markdown, so there is no batch writer that covers both and
the pair **cannot be written as one transaction**. A crash between the two writes,
or a hand-edit of one alone, can therefore leave the sheet describing a
terminology that is no longer the one the gate is bound to.

Rather than pretend that window does not exist, the sheet's header stamps
`bound_sha256` — the digest of `<slug>.json` as the sheet was written from it, and
the same value the gate records. **If the sheet's digest disagrees with the one in
`<run_dir>/gates/terms-gate.md`, the sheet is stale**: re-run the stage before
accepting anything. A weaker claim honestly labelled beats a silent
inconsistency.

## Who writes here

Nothing writes here directly. `kz_terms.py` hands the built mapping to
**`kz_config.write_data()`** — the mirror writer, and the only code in the tool
that opens a path under `SCED-tools/` for writing. That is what keeps §4.1's
*"writes outside `<run_dir>`: no"* column literally true for `terms`: it declares
no filesystem write root outside `<run_dir>` and it cannot reach one.

`guard.data_root` is the single named exemption from `guard.forbidden`'s
`"SCED-tools/"` entry, and `kz_config` evaluates it **first**. Writes here never
require `--live`: §4.1 derives the blast-radius banner from `guard.write_roots`
— the `SCED-downloads` destinations — and a banner whose radius is a git-tracked
JSON receipt trains the operator to type `LIVE` without reading it.

## The verdicts, and what each one obliges

| `source` | meaning | reviewable? |
|---|---|---|
| `glossary` | the canonical Korean glossary carries it; the rendering is adopted unchanged | no |
| `rules_ref` | the official rules reference or FAQ carries it, and the glossary does not | no |
| `coined` | no authority carries it; it was invented here | **yes** |

`coined` is S1's **novel** verdict (`kz_decide.NOVEL_VERDICTS`), so rule 5
requires it to be ruled `high` or the stage stops at exit 11. Those rows are the
ones to read.

## Two consistency properties the stage enforces

Both are checked in **both directions** and both refuse at exit **67**:

- one English term may not be rendered two ways — that fragments a term across
  the corpus;
- two English terms may not share one Korean rendering — that merges them.

Neither is visible to any per-ruling rule, because each ruling is individually
valid. They are properties of the set.

## Generating one

```
koreanize.sh terms --slug <slug>
```

Then read `<slug>-terminology-ko.md`, correct what needs correcting, and set
`status: accepted` in `<run_dir>/gates/terms-gate.md`.

## The bound sha256

The gate records `bound_sha256` — the digest of the terminology it was given to.
If the terminology moves, `kz_common.carry_review()` supersedes the acceptance and
resets the gate to `pending`, recording the superseded verdict rather than
discarding it. *A verdict can never outlive the artifact it was given to.*

## Re-running

A re-run is given the **prior** terminology as material and is told to agree with
it unless it can say why the prior rendering is wrong. A stage that silently
re-coined a settled term would make the acceptance that settled it meaningless
one run later.
