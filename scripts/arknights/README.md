# arknights

A staged, resumable, config-driven CLI that converts the 22 folders / 91 Tabletop
Simulator save files under `docs/Arknights/TTS용 파일/` into **one** non-decomposed
`Custom_Model_Bag` at `SCED-downloads/downloadable/playercards/arknights.json`
plus **one** entry in `SCED-downloads/library.json`. 92 fan-made investigators,
their minicards, and their signature and weakness cards; Korean text only;
`author: "Samari"`.

Run this first, always:

```
SCED-tools/scripts/arknights/arknights.sh --status [--run ID]
```

It is read-only. It prints the nightly lock probe, the nightly window, whether
the nightly moved `origin/korean` under the run, the source tree hash and whether
it has moved, the per-stage table with `consumable` / stale, and the exact argv
the next stage would use.

---

## What ships today

**Ships means *runs end to end*, not *exists*.** All six stages run, and the
whole chain from `scan` to a passing `verify` is reproducible on this machine in
about four minutes. Measured on the 2026-08-30 corpus:

| | |
|---|---|
| source | 22 packs, 91 save files, 211 files / 67.2 MB hashed whole |
| objects | 531 (257 `CardCustom`, 181 `Card`, 93 `Deck`), 431 carrying metadata |
| defects repaired | 21 objects, 6 config rows, 37 fields |
| new ids | 424 — 92 investigators + 92 minicards + 240 other cards |
| output | 1 233 754 bytes (1204.8 KB), 554 GUIDs, 22 sub-bags, 918 urls normalised |
| library | 193 → 194 entries |

**The output is byte-stable.** Derived container GUIDs are
`sha256("arknights:v1:" + pack)[:6]`, so re-running `assemble` on unchanged
inputs produces an identical file and the committed artifact diffs cleanly
instead of churning 23 GUIDs a run. `test_corpus_output_is_byte_stable` asserts
it.

**`publish` is rehearsal-by-default and is the only stage that writes outside its
run directory.** Everything before it is read-only with respect to every
repository in the workspace.

**Two things this tool deliberately does not do**, both because they are safety
mechanisms rather than omissions: it never writes `cycle_code` into the library
entry (that key is the only writer of `addToPlayerCards`, i.e. the only thing
that would route the bag into the card index), and it never sets the entry's
author to `Fantasy Flight Games` (`DW_TAB_IDS` has `fanmade-playercards` and no
official `playercards` tab, so an FFG-authored entry is silently invisible in the
download window). `akn_config.validate` refuses both at load time even if a
config edit adds them.

---

## Stages

Every stage has exactly one named owner. A stage with no row here does not exist.

| stage | module | writes outside `<run_dir>` | owns |
|---|---|---|---|
| `scan` | `akn_scan.py` | no | the inventory, the source tree hash, the defect list |
| `renumber` | `akn_renumber.py` | `data/arknights.idmap.json` | the `akn` namespace, exit 83 |
| `repair` | `akn_repair.py` | no | the patch set (it applies nothing), exit 84 |
| `assemble` | `akn_assemble.py` | no | the bag, the `Deck` prohibition, exit 85 |
| `verify` | `akn_verify.py` | no | V1–V12, exit 86 |
| `publish` | `akn_publish.py` | **yes — the two `SCED-downloads` files** | the banner, the lock, the plan binding |

`renumber`'s exemption is `guard.data_root`, a git-tracked JSON audit record, and
it is deliberately **not** banner-bearing: a blast-radius prompt whose radius is
a receipt trains an operator to type LIVE without reading it.

### The DAG

Linear, and stated as such because unlike koreanize's there is nothing to draw:

```
scan -> renumber -> repair -> assemble -> verify -> publish
```

A stage may run when its predecessor's report is `consumable` and no `binding{}`
sha256 has moved. That is the whole resumability mechanism: re-running `scan`
invalidates everything downstream without any stage having to remember it.

---

## The walkthrough

```
arknights.sh --status                       # always first
arknights.sh scan                           # mints <YYYYMMDDTHHMMSSZ>
arknights.sh renumber --run <id>            # writes data/arknights.idmap.json
arknights.sh repair   --run <id>            # emits repairs.json; applies nothing
arknights.sh assemble --run <id>            # builds <run_dir>/arknights.json
arknights.sh verify   --run <id>            # V1-V12; V10's live half is skipped
arknights.sh publish  --run <id>            # REHEARSAL. Writes nothing outside <run_dir>
arknights.sh publish  --run <id> --live     # the two writes; type LIVE

# then, by hand -- arknights never runs a mutating git verb:
git -C SCED-downloads add downloadable/playercards/arknights.json library.json
git -C SCED-downloads commit -m 'feat(playercards): add the Arknights fan-made player card pack'
```

Between `renumber` and `assemble` there are two artifacts worth actually reading:
`<run_dir>/idmap.tsv` (`new_id / old_id / pack / role / nickname / file / path`)
and `<run_dir>/repairs.json`, the per-object patch set as a reviewable diff. The
point of the staging is that both exist *before* anything is built.

After a live publish, re-run `verify`: V10's live half runs against the written
tree and asserts the entry, the +1 count and the sorter's stability. **No check
in this tool can see a wrong card image** — load the pack in Tabletop Simulator
and look at it.

---

## Exit codes

**The band is `{80..89}` and it is verified free.** The union of every other
documented table in this workspace reaches 75 and no further:
`daily-sync-local.sh`'s, koreanize's stage codes and its `{71,72,73,75}`
dispatcher band, `sced-run-now.sh`'s `{7,8,9}`, and the wrapper's `{2,6}`.
`test_exit_band_is_disjoint_from_every_other_table` reads those tables from their
own sources rather than restating them.

| code | owner | meaning | first move |
|---|---|---|---|
| `0` | — | the stage completed and every check passed | — |
| `1` | — | unhandled exception — a defect in the tool | the traceback |
| `2` | — | usage, or guard P0a (launchd) / P0b (interpreter outside the platform set), or a mutating git verb reached the `GIT_READONLY` wrapper | argv / env |
| `80` | stage | **guard refusal. NOTHING WAS WRITTEN.** A write outside `guard.write_roots`, a plan over `max_files_written`, the nightly lock is held, the run is inside the nightly window, or a `--restore` record whose path, action or snapshot this run never wrote | `arknights.packs.json`, `.local-sync/run/daily-sync.lock/owner` |
| `81` | stage | precondition — a named input missing or malformed, an upstream report not `consumable`, a `--live` run's `publish.plan.json` missing or moved since the banner, **or the lock failing to release cleanly after a write that did land** (P6 bands 81 once anything is on disk, precisely so 80 keeps its promise) | the named file |
| `82` | stage | input drift — `docs/Arknights/` changed since `scan`, or a config row names an object that does not exist | the path named, then re-run `scan` |
| `83` | `renumber` | duplicate new id, ordinal collision with no E-row, broken many-to-one, unresolvable role | `renumber.json` → `checks[]` |
| `84` | `repair` | a defect with no config row, **or a config row matching no object** | `repair.json` → the failing `rows[]` entry |
| `85` | `assemble` | a `Deck` lost or gained an object, duplicate GUID, surviving `cloud-3.` reference | `assemble.json` → `checks[]` |
| `86` | `verify` | a V-check failed, or a declared remainder with no `--accept-declared-remainder` | `verify.json` → `checks[]`; every check names its objects |
| `87` | `arknights.sh` | refused before any stage ran — unknown stage, unregistered `--accept-*`, unreadable config | argv, then `arknights.packs.json` |
| `88` | `arknights.sh` | refused: the predecessor is not `consumable`, or is stale | `arknights.sh plan` |
| `89` | stage | `--live` (or `--restore`) declined at the banner | — |

**`88` and `81` are the same fact reported by two actors**, and the difference is
the first move. `88` is the dispatcher's: the table was evaluated before spawning
anything and **no stage process ever started**, so the move is `plan`. `81` is a
stage's own: it ran far enough to read its input, so the move is the named file.
Lifted from koreanize's `72`/`13` on purpose — an operator who has learned that
distinction once should not have to learn it twice.

Aggregation across several triggered codes is an explicit precedence list
(`akn_common.EXIT_PRECEDENCE = [2, 80, 81, 82, 83, 84, 85, 86]`), never `min()`.

---

## Tolerances — the closed list

| name | flag | stages | what it accepts |
|---|---|---|---|
| `declared-remainder` | `--accept-declared-remainder` | `verify`, `publish` | N objects carry no metadata and no repair row |
| `max-files-written` | — (**hard cap**) | every stage | nothing. `guard.max_files_written` has no acceptance path |

The `--accept-*` family is **closed**: `arknights.sh` refuses an unregistered one
at `87`, before any stage runs, so the table's totality is enforced from the one
place an operator actually types. `declared_remainders` is `0` for the shipped
corpus — both investigator restorations (Zima, and 스카디 대체물's stripped
investigator card) ship with real data — so the flag is not needed for this run.

---

## The library entry, and the cost it buys

The entry is a config row (`library_entry` in `data/arknights.packs.json`) and it
is sorted into place by the **imported** `SCED-downloads/misc/sort_library.py`,
never a reimplementation and never a shell-out. That module owns `KEY_ORDER` and
the sort; importing it is the only way to guarantee one definition of the
canonical order.

Its `get_sort_keys`/`reorder_item_keys` are applied **in memory**, and
`library.json` is then written exactly once, atomically, through `publish_write`.
Its file-level `sort_json_file()` is deliberately not called on the published
file: it does `open(JSON_FILE, "w")` + `json.dump` in place, so it is a
truncate-then-rewrite of the file the mod consumes, it sits outside the closed
target pair, and it swallows its own exceptions into a `print`. The claim that
our in-memory application equals what its file-level function produces is
asserted by the gates instead, against a temp workspace where the in-place write
can do no harm.

**Adding it arms the nightly overlap gate, permanently.** Measured 2026-08-30:
`library.json` appears in **0** of the SCED-downloads fork's 1458 changed paths
vs `upstream/main`, and `downloadable/` in 0 as well. This entry puts
`library.json` into that set for the first time. The gate is path-level, so *any*
upstream touch of it trips the nightly whether or not the JSON edits conflict —
11 touches in the last 90 days, roughly **one stall per eight days** — and stalls
are sticky. `SCED_SYNC_AI_RESOLVE` is empty, so there is no automated path.

The resolution is always the same and always cheap: take upstream's
`library.json`, append our entry, re-run `misc/sort_library.py`. That reduces the
cost per occurrence, not the frequency, and it is offered as exactly that. The
banner prints all of this, with the fork-changed numbers **derived live** rather
than quoted, immediately before the `LIVE` prompt.

The new file at `downloadable/playercards/arknights.json` carries none of this
cost: upstream will never touch that exact path.

---

## The nightly interlock

`publish` shares exactly one resource with the nightly driver: the **workspace
lock**. It takes `.local-sync/run/daily-sync.lock/` with the same `mkdir`
primitive and writes `owner` with `O_EXCL` in the driver's exact field format
with `repo=arknights` — matched rather than invented, because that string is the
operator-legible signature `sced-run-now.sh --status` prints and is the whole
diagnosis of a silent night.

The order is load-bearing and is not the order the steps read in:

```
probe the lock -> check the window -> banner -> take the lock -> write
```

The probe and the window check come **first** so nobody is asked to type LIVE
into a run that is about to refuse. The lock comes **last** because the driver
declares a lock stale at one hour, and an operator who steps away with a
typed-LIVE prompt open would otherwise hand the workspace-wide lock to the
nightly on a timer. The window is re-checked inside the lock, because a probe
closes nothing on its own — it is a TOCTOU race either way, and re-checking
narrows it to the width of one `mkdir`.

**`--restore` takes the lock too, so it takes the lock probe with it** — the
same first step, before its own banner. It is the window check, not the probe,
that `--restore` is exempt from; see its own section below.

Nothing that spawns a process runs while the lock is held. The `origin/korean`
base sha the stamp records is resolved at plan time on both paths, and every
`git` call in the package is bounded (`akn_common.GIT_TIMEOUT_SECONDS`, 30 s):
the driver declares this lock stale at one hour, so a wedged subprocess would not
merely delay — it would hand the lock to a takeover while this process still
believed it held it.

`publish` also refuses inside the nightly window **even when the lock is free**.
The window is the **union** over every `repos{}` entry in
`SCED-tools/config/sync-schedule.json` (today 02:02–04:27 KST), computed and
never hardcoded — the lock is workspace-wide, so a window derived from one repo's
bounds would refuse at the wrong times.

While the lock is held, `.local-sync/run/arknights.stamp` records the stage, the
planned paths and the `origin/korean` base sha. That file exists for one reason:
if this stage holds the lock when the driver fires, the driver hits a bare
`exit 3` before its `EXIT` trap and before its log tee, so it produces **no state,
no Discord message of any grade, and no log entry**, and `3` is not in the
failover dispatch allow-list either. `sced-run-now.sh --status` prints the owner
line; the stamp turns that into "which stage, which files, which base". It is
removed under compare-and-delete, like the lock.

**`--status` says the base moved.** The nightly force-pushes `korean` on every
successful night, in a detached scratch worktree — so an uncommitted publish is
invisible to it and survives untouched. Only the base moved, so the plan is
**stale, not wrong**: re-run the rehearsal. Do not revert.

---

## Tests, and the absence of CI

`SCED-tools/` has no `.github/` directory and this tool does not add one. The
reason is not koreanize's — arknights has no third-party dependency at all and
one interpreter tier (`/usr/bin/python3`, Apple 3.9.6) — it is that the corpus
is `docs/Arknights/`, which is gitignored and exists in exactly one copy on this
volume, and the collision oracles V3, V3b and V11 are sibling checkouts a
`SCED-tools` runner would not have. A hosted runner would skip every
corpus-level check and report green on a suite whose four most load-bearing
assertions it had not performed — worse than no CI, because it reads as
coverage.

The compensating control is one entry point:

```
SCED-tools/scripts/arknights/arknights.sh selftest
```

It runs every module's `--selftest` and then `tests/test_arknights_gates.py`
through the file's **own** runner, not pytest — pytest is on this machine's
Homebrew 3.14 only, and this package is pinned to the platform interpreter. The
suite has two tiers: a synthetic one that builds a three-file miniature source
tree in a tempdir (a `Deck` holding two investigators, a card printed twice, a
minicard with no GMNotes), and a corpus tier that **skips loudly** with a printed
line when the source tree or a sibling checkout is absent.

Three package-wide gates have no single module's `--selftest` behind them and are
worth naming: every `git` invocation must go through the `GIT_READONLY` wrapper;
every `open()` in the package must live in `akn_common.py` or `akn_config.py`
with a literal read mode (`os.open` is permitted in two shapes only, each
because its flags cannot destroy anything: `O_EXCL`, whose create fails rather
than truncating, and `O_RDONLY|O_NOFOLLOW|O_NONBLOCK`, which cannot write,
refuses a symlink in the same syscall that opens the file, and cannot block on a
FIFO); and every import must be stdlib or one of
the three names in `PACKAGE_IMPORTS` — the package's own two modules plus
`SCED-tools/scripts/sced_io.py`, the workspace's shared atomic writer, which the
package uses rather than reimplementing so one fix to it propagates.

`sced_io.py` is **compiled and imported** by the 3.9 gate along with the package,
though it stays out of `PACKAGE_MODULES` (which also drives the AST scans). It is
owned by ~30 other scripts, most of them on the Homebrew 3.14 tier, while
arknights is the only consumer pinned to Apple's 3.9.6 — and the two checks catch
different halves. Measured on 3.9.6: `py_compile` catches `match` and `except*`
and does **not** catch a PEP-604 `int | str` annotation, which parses fine and
raises `TypeError` when the `def` executes. That file already carries a PEP-585
generic, so annotations are exactly the direction it drifts.

---

## Runbook

| symptom | first move |
|---|---|
| `exit 88` | `arknights.sh plan --run <id>`. **Nothing ran.** The named predecessor is not `consumable`, or is stale |
| `exit 81` from a stage | the stage read its input. Go to the file the message names |
| `exit 80` naming the lock | read the owner line. `repo=SCED`/`repo=SCED-downloads` is the nightly driver — wait for it. `repo=koreanize` is a langpack run. `repo=arknights` is another publish |
| `exit 80` naming the window | you are inside 02:02–04:27 KST. Wait, or run tomorrow |
| `exit 80` naming `guard.write_roots` | a stage planned a path outside its roots. **Nothing was written** |
| `exit 82` | `docs/Arknights/` moved under the run. `verify`'s V8 names the changed path; re-run `scan` |
| `exit 84` | both directions are failures. A defect with no row means the corpus changed; a row matching no object means a repair has silently stopped matching, which is how a repair suite rots |
| `exit 86` | the check id is the diagnosis. `verify.json` → `checks[]`, and each check's `detail[]` names the objects |
| `exit 89` | you did not type `LIVE`. Nothing was written |
| `publish --live` says there is no plan | run the rehearsal first. The banner is built from it and the live write is asserted against it |
| `publish --live` says the planned bytes moved | something changed between the banner and the write. Re-run the rehearsal and read the new banner — it is describing a different write set |
| `--status` says the base moved | re-run the rehearsal. **Do not revert** — nothing is lost, the plan is merely stale |
| the nightly produced no message of any grade | a silent night. `sced-run-now.sh --status`, then `.local-sync/run/arknights.stamp` |
| V10 says `skipped` | expected before `publish`. The template half ran; the live half needs the entry |
| V12 says `skipped` | expected. It is opt-in (`--probe-urls N`) and never fatal — the r2.dev edge 403s python's urllib User-Agent while curl gets 200 |

**`--probe-urls N` is the one flag in the package that reaches the network.** It makes a ranged
1-byte `/usr/bin/curl` GET (the platform path, pinned for the same reason P0b pins the
interpreter) at up to N of the atlas urls found in the corpus, and those urls come from
third-party fan packs — so the hosts contacted are chosen by the pack, not by this tool, and the
operator's address is disclosed to them. The urls are matched against `SAFE_URL` first (so one can
never become a curl option or a second command) and `-L` is not passed (so a redirect is not
followed), but the **host is deliberately not scoped**: an allowlist would hide the one case worth
reporting, a pack pointing somewhere unexpected. V12's note names every host it contacted; read it.
Measured 2026-08-30: all 1161 url occurrences in the corpus are `steamusercontent-a.akamaihd.net`.

### Reverting a publish

```
arknights.sh publish --run <id> --restore
```

`publish --live` snapshots both targets into `<run_dir>/publish/pre/` **before**
touching either, together with the manifest `--restore` reads. A `modify` is
restored from its snapshot; a `create` is deleted **only** when the file's
sha256 still equals the bytes that run wrote — anything else is somebody's later
edit, and the tool names it and refuses rather than guessing.

The record is validated **before the banner** — path, action and snapshot name —
so nothing is written and nobody types `LIVE` into a run about to refuse. That
validation is path-shaped, and the guarantee is not: the loop re-joins the same
name after the prompt, and a hard link defeats a name check outright, since
`islink()` is false for one and `realpath()` reports it as that path. So each
snapshot is read through `O_RDONLY|O_NOFOLLOW|O_NONBLOCK` with `S_ISREG` and
`st_nlink` asserted on the open descriptor, and a link at a validated name
refuses at **81** rather than 80 — by then the first entry may already be back
on disk.

Aliasing and object *kind* are separate properties and each needs its own test.
`O_NOFOLLOW` and `st_nlink` both refuse an alias; neither says the thing is a
regular file, and a **FIFO** satisfies every predicate on both sides of the read
— `lexists`, `islink` false, `realpath` inside the root, `st_nlink` 1 — while
blocking the `open()` itself, inside the workspace lock, which is a silent night.
`O_NONBLOCK` and `S_ISREG` close that; the selftest plants a real FIFO under a
5 s clock so a revert of either **names itself** instead of hanging the suite.

The `create` branch's delete goes through the same reader for the same reason:
`sha256_file` follows a symlink and `os.unlink` removes one, so hashing by path
and deleting by path were not statements about the same object.

If the commit has already been made, `--restore` is the wrong tool: use git.

**`--restore` takes two of `publish`'s three interlocks and warns about the
third, deliberately.** It writes the same two published paths, so it takes the
same workspace lock (`repo=arknights`, the driver's owner format) and leaves the
same `.local-sync/run/arknights.stamp` while it holds it — a restore that
collided with the nightly would otherwise be invisible to the Stall runbook's
silent-night row, which diagnoses a collision from exactly those two files. The
nightly **window** only prints a warning: a restore is unplanned and
time-sensitive, and refusing one for up to 2h25m is a worse failure than
overlapping a nightly whose lock this process is already holding. So a restore
inside 02:02–04:27 KST proceeds, and a nightly run starting in that moment finds
the lock held and exits 3 — the silent night the stamp exists to explain.

---

## What arknights will not do

- **No mutating git verb.** `akn_common.GIT_READONLY = {rev-parse, diff, status,
  ls-files}` is the complete set any module may pass to `git`, every invocation
  goes through one wrapper that asserts membership and refuses at exit 2, and
  `test_arknights_gates.py` AST-scans the package for one that does not.
  `akn_publish.py` writes files and **prints** the commit command; you commit.
- **No write anywhere under `docs/Arknights/`.** `guard.write_roots` is a closed
  list that does not contain the source root, `read_source()` is the only
  function that opens a path under it and it opens `"rb"` only, and an AST scan
  asserts both properties over the syntax rather than over one run's behaviour.
  V8 is the fourth layer and the only empirical one: it re-hashes the whole tree
  after the run. 68 MB with exactly one copy on a volume that has no Time Machine
  destination deserves more than a convention.
- **No write to a third file.** `publish`'s allowlist is a two-element list of
  exact paths, not a root prefix, and both of those paths are still refused by
  the ordinary write guard — so `publish` is an exemption rather than a hole.
- **No launchd path, ever.** P0a refuses at exit 2 in every module. This is an
  operator tool; the nightly must never invoke it.
- **No `cycle_code`, and no FFG author.** See "What ships today".
- **No CI.** See "Tests, and the absence of CI".
