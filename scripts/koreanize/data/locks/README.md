# `data/locks/` — the receipt tier

`<slug>.lock.json`, one per scenario. Design §3.1's **receipt** tier: per-stage
`{verdict, exit_code, consumable, gate, binding{}}`, the accepted tolerance
baselines, `atlas-urls.json` (the R2 map) and `write_set[]` — paths plus
`pre_sha256`/`post_sha256`, **never bytes**, and no per-face arrays.

## Why two files earned a promotion out of run material

Both for the same reason: something outside `<run_dir>` depends on them **after
`<run_dir>` is gone**.

- **`atlas-urls.json`** is §1.3's golden-fixture assertion target and §8.4's sole
  authority for the N-7 orphan set. Left in run material it is gitignored and
  dies with `.am/`, taking the only record of which R2 objects this scenario owns.
- **`write_set[]`** is the input `revert` reads. §8.4 says outright that *"that
  list, not the directory, is the authority"*, so a lost `<run_dir>` would make an
  in-flight repository write unrevertible by the tool. It is at most
  `guard.max_files_written` = 200 rows, and it is the one array whose absence is
  unrecoverable.

## Who writes here

`kz_langpack.py` **produces** the receipt but does not write this path. Its own
guard re-asserts `guard.forbidden` internally and refuses at exit 4 *before any
read*, and preserving that absolute refusal-before-read property is the point.
The receipt is a *record about* a repository write, not a repository write, so it
goes through **`kz_config.write_data()`** like every other `data_root` artifact —
the one place the exemption lives, so no second module needs to know it exists.

`revert` **reads** `data_root` and never writes it, so the fallback path survives
a run directory that is gone without inheriting the exemption.

## `mask.residual_baseline[]`

Matching is equality on the tuple `(check, group, window_name, face)` — never on
`group` alone, so a **third** face inside an already-baselined group still fires
exit 23, and never on `face` alone, so a W1 hit (whose subject is the window) is
representable at all. `extent` is recorded for diffing, not matched on.

```jsonc
{"check": "W1"|"W2", "group": "Act/front", "window_name": "body",
 "face": "71006", "extent": [x0, x1, y0, y1],
 "accepted_by": "shanash", "accepted_on": "2026-08-18"}
```

## Retention

The lock binds to its artifacts' **sha256s, not their paths**, so `<run_dir>` can
be moved off the volume without invalidating the receipt.
