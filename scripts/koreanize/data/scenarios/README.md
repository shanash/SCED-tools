# `data/scenarios/` — the durable-inputs tier

`<slug>.scenario.json`, one per scenario. Design §3.1 splits koreanize's artifacts
by **durability**, not by convenience, and this directory is the tier whose loss
costs the whole project: these are the human-reviewed judgement artifacts.

## Who writes here

Nothing writes here directly. `kz_init.py` hands the generated config to
**`kz_config.write_data()`** — the mirror writer, and the only code in the tool
that opens a path under `SCED-tools/` for writing. That is what keeps §4.1's
*"writes outside `<run_dir>`: no"* column literally true for `init`: it declares
no filesystem write root outside `<run_dir>` and it cannot reach one.

`guard.data_root` is the single named exemption from `guard.forbidden`'s
`"SCED-tools/"` entry, and `kz_config` evaluates it **first** — a `data_root`
write checked against `forbidden` first would be refused by the very rule its
exemption exists to carve out. Three properties are asserted at exit 4 on every
call: the root resolves inside the koreanize package directory, it is a strict
prefix of every planned write under `SCED-tools/`, and no planned write under it
lands outside `{scenarios,terms,text,layouts,icons,locks,golden}/`.

Writes here **never require `--live`**. §4.1 derives the blast-radius banner from
`guard.write_roots` — the `SCED-downloads` destinations — and `data_root` is
excluded by name, because a banner whose radius is a git-tracked JSON receipt
trains the operator to type `LIVE` without reading it.

## Generating one

```
koreanize.sh init --scenario "The Midwinter Gala" --pack "Korean - Campaigns"
```

`init` writes `scenario.json` into `<run_dir>` **and** mirrors it here. Then read
`<run_dir>/gates/init-gate.md`, check the counts against the scenario, and set
`status: accepted`. Every downstream stage refuses at exit 30 until you do;
`init` itself exits 0 and prints the gate path.

## The pin

`config_sha256` is a digest of the document with that field set to `""`,
serialised with sorted keys and a fixed separator so it is a property of the
**content** rather than of whichever writer last touched the file. Every stage
recomputes it and refuses at exit 4 on mismatch, so a hand-edit after `init`
cannot silently retarget the tool. If you must change a value here, change it and
re-pin with:

```
python3 kz_config.py --validate data/scenarios/<slug>.scenario.json
```

which prints the computed digest — or simply re-run `init`, which is the
supported path.

Machine-local locations (font paths, the atlas cache, the golden corpus root)
belong in `~/.config/koreanize/env` and are deliberately **not** covered by
`config_sha256`: a pinned, git-tracked config that embedded `~/Library/Fonts/…`
or `/Volumes/PRO-G40/…` could not be used on another machine or after the volume
moved.
