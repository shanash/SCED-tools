# koreanize

A staged, resumable, config-driven CLI that localizes an Arkham Horror scenario
into Korean. It generalizes the Midwinter Gala chain — index → terms → translate
→ slice → mask → erase → composite → typeset → recompose → upload → repoint →
register → verify — into something that can be pointed at a scenario it has never
seen.

Run this first, always:

```
scripts/koreanize/koreanize.sh --status [--slug SLUG]
```

It is read-only. It prints the nightly lock probe, the per-stage table, what is
stale, whether the golden fixture would skip, whether the nightly moved the base
under you, and the exact argv the next stage would use.

---

## What ships today

**Ships means *runs end to end*, not *exists*.** Every stage in the table below
is owned by a module that is present, registered and self-testing; what
separates v0 from v1 here is that v0 completes and v1 stops inside `typeset`.

**v0 — the reuse resolver.** `init source scaffold reuse objtext register revert
verify` plus `triage`(S6). It writes no art and manufactures nothing: it finds
the cards that already have Korean art somewhere in the three Korean packs and
adopts it. Over the eight Challenge Scenarios that clears **222 of 274 ids
(81%)** across **226 objects**, out of **298** objects in the population.

**v0 is not AI-free.** `triage`(S6) is on its critical path, not at its margin:
`source` escalates to it on every id with two or more Korean-art donors, and that
is **16** of the 226 — with **14** at donors of *differing grids*, each of which
is `--accept-donor-choice` or exit 22. A missing `CLAUDE_CODE_OAUTH_TOKEN` stops
v0, not only v1.

**v0's coverage is partial and is *declared* rather than implied.** No
manufacture path completes today, so `scaffold`, `objtext`, `register` and
`verify` cover the objects `source` decided `reuse` and report the rest as
`counts.objects_without_korean_text`. The other 72 objects get **no override file
at all** and Tabletop Simulator resolves them from the English base object
exactly as it does today. That is deliberate: an override carrying the *English*
`FaceURL`, sitting inside a container the mod advertises as Korean, is worse than
no override — the pack would be larger and wrong instead of smaller and honest.

**v1 (the manufacturing chain) and v1.x (the mask-coverage assertion) are built
and committed; neither runs end to end yet.** Every v1 module in the stage
table below exists, is registered in `kz_config.PREDECESSORS`, answers
`--selftest`, and is exercised by `koreanize.sh selftest` — there is no stage
left that can refuse for want of a module.

**What stops a v1 run is check `X2`.** `typeset` measures the icon map and
refuses at **67** when two icons sit within `ICON_SEPARATION_MIN` (0.05) of
each other in the `(ink_fill, advance_em)` plane; four pairs of the shipped
icon font do, and are recorded with their distances in `data/icons/README.md`.
X2 is the one tolerance-bearing predicate in the design with **no row in the
tolerance table below** — no `--accept-<name>`, no baseline — so the refusal is
unconditional rather than a decision offered to the operator. The two
candidates, a third discriminating dimension or a reviewed exception list
carrying a `TOLERANCES` row, a flag and a baseline, are recorded there too;
choosing between them is its own task and this file does not choose.

**And `~/.config/koreanize/env` has to exist first.** Independently of X2, a
missing file or a missing `CLAUDE_CODE_OAUTH_TOKEN` stops every AI stage at
**65** — `triage`(S6) included, which is why it stops v0 and not only v1.

---

## Stages

Every stage has exactly one named owner. A stage with no row here does not exist.

| stage | module | phase | AI | writes outside `<run_dir>` |
|---|---|---|---|---|
| `init` | `kz_init.py` | v0 | — | no |
| `source` | `kz_source.py` | v0 | — (escalates to `triage`) | no |
| `scaffold` | `kz_langpack.py` | v0 | forbidden | **yes** |
| `reuse` | `kz_langpack.py` | v0 | forbidden | **yes** |
| `objtext` | `kz_langpack.py` | v0 | forbidden | **yes** |
| `register` | `kz_langpack.py` | v0 | forbidden | **yes** |
| `revert` | `kz_langpack.py` | v0 | forbidden | **yes** |
| `verify` | `kz_verify.py` | v0 | forbidden | no |
| `triage` | `kz_triage.py` | v0 | **S6 required** | no |
| `terms` | `kz_terms.py` | v1 | **S1 required** | no |
| `translate` | `kz_translate.py` | v1 | **S2 required** | no |
| `check` | `kz_checkers.py` | v1 | — | no |
| `slice` | `kz_slice.py` | v1 | forbidden | no |
| `mask` | `kz_mask.py` | v1 | **S3 required** | no |
| `erase` | `kz_erase.py` | v1 | — | no |
| `composite` | `kz_composite.py` | v1 | forbidden | no |
| `typeset` | `kz_typeset.py` | v1 | **S4 required** | no |
| `recompose` | `kz_recompose.py` | v1 | forbidden | no |
| `upload` | `kz_upload.py` | v1 | forbidden | **yes** — R2 |
| `repoint` | `kz_langpack.py` | v1 | forbidden | **yes** |
| `audit` | `kz_audit.py` | v1 | **S7 required** | no |
| `ocr` | `kz_ocr.py` | v1.x | **S5 required** | no |

The AI column is total and is the same partition `scenario.json` declares — 7
required, 11 forbidden, 4 neutral — and `kz_config` refuses at exit 4 if the two
ever disagree. Declaring the contract in each module's body is what makes the
prohibition mechanical: a forbidden stage that tried to write a report carrying
an `ai` block is refused before the write.

`golden` is deliberately absent from that table. It is a `koreanize.sh` **command**
that drives five stages, not a stage with a report of its own.

### The DAG

```
init --+-> source -> scaffold --+-> reuse -----------------------------------+
       |                        |                                           |
       |                        +-> objtext -----------------------------+   |
       |                                                                 |   |
       +-> terms(S1) -> translate(S2) -> check                           |   |
       |                                                                 |   |
       +-> slice -> mask(S3) -> erase -> composite -> typeset(S4)        |   |
                                     -> recompose -> upload -> repoint --+---+-> register -> verify
```

The drawing omits three edges for legibility. The authoritative version is
`kz_config.PREDECESSORS`, which is what `plan` implements; `koreanize.sh plan`
prints it evaluated against your run directory.

A stage may run when every predecessor's report is `consumable` **and** no
`binding{}` sha256 has moved. A stage is **stale** when one has — that is the
whole resumability mechanism, and it implements *"a rebuild invalidates
everything downstream"* without anyone having to remember it.

**Two edges are resolved per object, not per scenario**, and the difference is
not academic. `objtext` needs, for each object, whichever of `reuse` / `check`
produced *that object's* text; `register` needs whichever of `reuse` / `repoint`
produced *that object's* art. All eight Challenge Scenarios have both
`counts.manufacture > 0` and `counts.reuse > 0`, so a scenario-level rule selects
`check` — a v1 stage — on every scenario v0 targets, and `objtext` refuses at
exit 72 on all eight, taking `register` and `verify` with it. The disjunct is
therefore resolved over the stage's **write set** — the entries it will actually
write — never over its input.

**`revert` is exempt from the predecessor rule, and has to be.** Its predecessor
is by construction the stage that just *failed*, and a failed stage is never
`consumable`, so the universal rule would make `revert` refuse at exit 72 in
exactly the situation it exists for. Its precondition is instead the *existence
of something to undo*; absent all three records it refuses at **13** naming the
directory it looked in, which is a different fact from 72 and sends you somewhere
different. `koreanize.sh` asserts at startup that the exemption set is exactly
`{"revert"}`.

---

## The v0 walkthrough

```bash
K=scripts/koreanize/koreanize.sh

$K init --scenario "Challenge Scenario - By the Book"
# read gates/init-gate.md, decide the scope, set status: accepted

$K source --slug challenge-scenario-by-the-book
# escalates ambiguous donors to triage(S6); add --accept-donor-choice for
# donors at differing grids, or it exits 22

$K scaffold --slug ...        # rehearsal. Rehearsal is the DEFAULT.
$K scaffold --slug ... --live # prints the banner, asks you to type LIVE

$K reuse    --slug ... --live
$K objtext  --slug ... --live
$K register --slug ... --live
$K verify   --slug ...

# then, by hand -- koreanize never runs a mutating git verb:
git -C SCED-downloads add ... && git commit -m "..."
```

**Rehearsal is the default for every stage that writes outside `<run_dir>`.**
Forgetting `--live` gets you a rehearsal and a note, never a write. The rehearsal
writes into `<run_dir>/dry-run/<stage>/` — a *pinned* path, not a convention,
because a sibling of a langpack output directory lands *inside* the guarded tree,
where a guard that refuses writes *outside* the roots could never fire on it.

**The banner is not decoration and the plan behind it is not a memory.** The
rehearsal persists its plan to `<run_dir>/<stage>.plan.json`; the live invocation
carries that file's sha256 in its own `binding{}` and asserts the executed write
set equals it path-for-path and in order. Without the persisted plan, "the
executed write set equals the plan the banner printed" would be a statement one
process makes about itself. A live invocation with no matching plan, or one whose
plan has moved since the banner was answered, refuses at **13**.

---

## Exit codes

| code | meaning | first move |
|---|---|---|
| 0 | stage complete, every check passed | — |
| 1 | unhandled exception — a defect in the tool | the traceback |
| 2 | usage, or invocation guard P0a (launchd) / P0b (running from inside the nightly's scratch worktree) | argv / env |
| 4 | **config guard refusal** — `config_sha256` mismatch, a write target outside `guard.write_roots`, a forbidden prefix, a plan over `guard.max_files_written`, the nightly lock, or the nightly schedule window. **Nothing was written.** | `scenario.json`, `.local-sync/run/daily-sync.lock` |
| 11 | stop by policy — AI abstain / low confidence / a rule failure | `ai/<stage>/<stamp>/decide.json` → the `rules[]` entry with `"status":"fail"` |
| 13 | precondition — a manifest missing or malformed, an upstream report not `consumable`, mtime freshness, a sha256 mismatch, font identity, an AI universe over the batch ceiling, golden inputs missing or drifted, or a `--live` invocation whose `<stage>.plan.json` is missing or has moved | the named input |
| 14 | input drift — a unit with no record, a record with no unit, an unknown token, a face count that moved between stages, or a node excluded by kind that carries a `GMNotes.id` | the upstream data file |
| 20 / 21 | per-stage hard rule codes; the semantics are in each stage's docstring | the report's `checks[]` |
| 22 | a tolerance-bearing predicate fired and its `--accept-<name>` was not given | the tolerance table below |
| 23 | mask-coverage residual above the accepted baseline | `data/locks/<slug>.lock.json` → `mask.residual_baseline[]`, matched on `(check, group, window_name, face)` |
| 24 | `revert` incomplete — a recorded write did not restore, or a touched path is still modified | the `write_set[]` entry that failed, then `git -C SCED-downloads diff --name-only` |
| **25** | an AI stage stopped **between batches** on `max_budget_usd_per_stage` or `stage_wall_clock_s`. N of M batches are complete on disk | `--status` → `batches N of M`; re-run the stage to resume at batch N+1. **Never 65** — no `claude` invocation failed |
| **26** | a `CONSENTS` row's flag was not given — a human *authorization*, not a threshold | the consent table below |
| 30 | human gate present but not `accepted` | `gates/<stage>-gate.md` |
| 62 | case-only path collision | `verify.json` check C7 |
| 65 | `claude` unavailable / unauthenticated / timed out (`rc 143`) | `ai/<stage>/<stamp>/stage-error.txt` |
| 66 | AI manifest invalid — schema, coverage, or run binding | `ai/<stage>/<stamp>/decide.log` |
| 67 | the produced artifact failed structural verification | `verify-stdout.log` |
| 74 | R2 / network failure | `upload.json` |
| **75** | `golden` **skipped** — the interpreter triple does not match the fixture manifest's. Not a pass and not a failure | `data/golden/<fixture>.manifest.json` → `tool{}` |

**`koreanize.sh`'s own band is `{71, 72, 73, 75}`** and is disjoint from every
stage code, from `sced-run-now.sh`'s `{7,8,9}` and from the nightly driver's.

- `71` refused before any stage ran (unknown stage, missing `--slug`, an
  unregistered `--accept-*` / `--consent-*` flag)
- `72` the requested stage's upstream dependency is not `consumable`, or is
  stale. `revert` can never return it
- `73` `--live` declined at the banner
- `75` `golden` skipped on an interpreter mismatch

**72 and 13 are the same fact reported by two different actors, and the
difference is what you do next.** `72` is the *dispatcher's*: it evaluated the
predecessor table and **no stage process ever started**, so the move is `plan`.
`13` is a *stage's own*: it ran far enough to read its input, so the move is the
named file.

**20, 21 and 30 collide numerically with `daily-sync-local.sh`'s "fetch failed",
"overlap gate script failed" and "rebase failed".** That is accepted and stated
rather than worked around: they are stage-local codes that only ever appear in a
koreanize report and never in a Discord notification. **`70` is deliberately not
used** — the driver defines it as "draft release creation, asset upload, or
go-live failed", and an operator reading a `70` beside the nightly's would derive
the wrong first move. R2 and network failures are `74`.

Aggregation across several triggered codes is an explicit precedence list, not
`min()`. `min()` would report a stage rule (20) over an invalid manifest (66),
naming the symptom instead of the cause.

---

## Tolerances — the closed list

`--accept-<name>` is not an open family. Every row is registered in
`kz_common.TOLERANCES`, and `koreanize.sh` refuses an unregistered flag at 71.

| predicate | stage | phase | flag | exit if unaccepted |
|---|---|---|---|---|
| two donors at equal confidence and **different grids** | `triage`(S6), on `source`'s behalf | **v0** | `--accept-donor-choice` | 22 |
| planned write set over `guard.max_files_written` | `kz_langpack.py` | **v0** | *(none — a hard cap)* | 4 |
| shrink ladder bottomed out on a face | `typeset` | v1 | `--accept-deep-shrink` | 22 |
| alignment displacement beyond `alignment_check_px` | `typeset` | v1 | `--accept-alignment` | 22 |
| inpaint grain ratio outside the recorded band | `erase` | v1 | `--accept-grain` | 22 |
| atlas above 90% of `MAX_ATLAS_BYTES` | `recompose` | v1 | `--accept-atlas-size` | 22 |
| mask-coverage residual above the lock baseline | `mask` | v1.x | `--accept-mask-residual` | **23** |

The flag column is deliberately *not* asserted non-empty: one row has no flag by
design, and a rule reading "every row has a registered flag" would fail on its
own table the day it was written. A flagless row must instead declare
`hard_cap: true`, and the selftest asserts that **exactly** the `hard_cap` rows
lack a flag — so the absence is a declaration rather than an omission.

## Consents — the other closed list

| consent | stage | phase | flag | exit if not given |
|---|---|---|---|---|
| third-party transfer of an assembled `--engine external` package | `erase` | v1 | `--consent-third-party-transfer` | **26** |

The two tables are asserted **disjoint**: a flag in both would mean a human
*decision* was being recorded as a *threshold*. 22 says "a measurement exceeded a
bound, decide whether to accept it"; 26 says "nothing is wrong; a person must
authorize this".

**Green is phase-scoped.** A row whose owning module does not exist yet is
*counted and printed* as pending on every `selftest` run — never skipped — so the
completion predicate is satisfiable at each implementation step without any step
being able to hide a gap. A row whose module *is* present but whose named fault
does not fire still fails.

The mechanism is live and its count is currently **zero** — every module named
in `kz_common.TOLERANCES` and `CONSENTS` is present, so `kz_common --selftest`
prints no pending line at all. That silence is a count of zero, not a check
that stopped running.

---

## Machine-local configuration — `~/.config/koreanize/env`

Mode `0600`, refused otherwise. Every path in `scenario.json` is
**workspace-relative**; machine-local locations live here, and this file is
deliberately *not* covered by `config_sha256`, so editing it never invalidates a
gate.

| variable | what it names |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | the credential every AI stage needs, including v0's `triage` |
| `KOREANIZE_FONT_TITLE` / `_BODY` / `_ICONS` | the three font roles, resolved by PostScript name and hashed |
| `KOREANIZE_ATLAS_CACHE` | a content-addressed atlas cache shared across runs |
| `KOREANIZE_GOLDEN_ROOT` | where the golden corpus lives (default `~/SCED-golden/`) |
| `KOREANIZE_LAMA_PYTHON` | the inpainting venv's interpreter (v1) |
| `KOREANIZE_TTSMM` | the TTSModManager binary the build gate runs |
| `KOREANIZE_TTSMM_SHA256` | asserted before every invocation |
| `KOREANIZE_TTSMM_TIMEOUT_S` | the build gate's wall clock (default 1800; plain integer seconds) |
| `KOREANIZE_LOCK_MAX_S` | the heartbeat's total cap (default 1800) |

**`KOREANIZE_TTSMM` may not point at the nightly's copy.** `daily-sync-local.sh`
copies that binary into its disposable worktree under `.local-sync/scratch/`, P0b
refuses any koreanize run that resolves a path there, and the tree is deleted
when the driver finishes — a gate pointed at it is a gate that vanishes.
`TTSModManager-Darwin` is adhoc/linker-signed with `Identifier=a.out` and no
TeamIdentifier, so macOS makes it its own TCC `responsible_path` and any Full
Disk Access grant is keyed to **path + cdhash**. That is why the path must be
stable, and why the asserted sha256 matters: it turns a grant-invalidating
replacement into a named refusal instead of a six-hour wedge.

---

## Interpreter tiers

| tier | interpreter | modules | rule |
|---|---|---|---|
| **art** | `#!/usr/bin/env python3` → Homebrew 3.14.7, PIL 12.0.0, numpy 2.4.2 | everything except the two below | P0a mandatory |
| **stdlib** | `#!/usr/bin/python3` (Apple 3.9.6) | `kz_langpack.py`, `kz_verify.py` | stdlib only, 3.9-compatible |
| **inpaint** | `.venv-lama/bin/python` (3.11) | none — koreanize imports nothing from it | invoked as a subprocess by `kz_erase.py` (v1) |

`kz_common.py` and `kz_config.py` are importable by both the art and the stdlib
tier, so they may not import PIL or numpy at module level. That is what forces
`kz_config`'s font-identity check to parse the sfnt `name` table with stdlib
`struct` rather than with `fontTools`.

**Two assertions, because an import scan is not enough.** `kz_common --selftest`
AST-scans the declared subject set (`STDLIB_TIER`) against a closed allowlist,
*and* runs `/usr/bin/python3 -m py_compile` over it as a subprocess. The scan
alone cannot catch `match`, a runtime-evaluated `X | Y` annotation, or a 3.10+
builtin — all of which are errors on 3.9.6 and all of which would ship green.

---

## Tests, and the absence of CI

**No CI runs this suite, and that is stated rather than assumed.** `SCED-tools/`
has no `.github/` directory and this change set does not add one. The art tier is
pinned to this machine's Homebrew triple by the golden fixture, and the stdlib
tier to Apple's `/usr/bin/python3`, which exists only on macOS and is the
interpreter the `py_compile` check has to actually *run*. A hosted runner
reproduces neither, so it would report green on a suite whose two most
load-bearing checks it had not performed — worse than no CI, because it reads as
coverage.

The compensating control is one entry point:

```
scripts/koreanize/koreanize.sh selftest              # the whole suite + every --selftest
scripts/koreanize/koreanize.sh selftest --pytest-only
```

It returns non-zero on any failure, and a green run is the completion predicate
of every implementation step.

Corpus figures — the 222, the 20/17, the 16/14 — are checked by `slow`-marked
tests that walk the live `SCED-downloads` tree the nightly force-pushes nightly.
They are a **smoke run, not the coverage**; `-m "not slow"` skips them and the
synthetic cases beside them are what actually pin the behaviour.

---

## The nightly interlock

`kz_langpack.py` writes into `SCED-downloads`, the tree the nightly rebases.
There are two hazards and they need different answers.

**Hazard A — the base moves, and it is the one that bites.** The nightly
force-pushes `korean` on every successful night. The rebase happens in a detached
scratch worktree, so uncommitted koreanize writes are invisible to it and survive
untouched. What moves is the *base*: `origin/korean` advances beneath a write set
planned against the old one. Nothing is lost; the plan is simply stale.

Every report records the sha it planned against, and `--status` prints:

```
base   : origin/korean is 120bea9be…, HEAD is 3f9c1aa2b… -- the nightly moved the base under this run.
         Nothing is lost: re-run the planning stage. Do NOT revert.
```

**Hazard B — concurrency.** A *probe* closes nothing: it is a TOCTOU race, and
nothing stops a write that begins at 02:16 from still running when the driver
takes the lock at 02:17. So koreanize takes the same lock the driver takes —
`.local-sync/run/daily-sync.lock/` with `mkdir`, and `owner` written with
`O_EXCL` in the driver's exact field format, `repo=koreanize`. It is *probed*
read-only before the banner (so you are never asked to type `LIVE` into a run
that is about to refuse) and *taken* after the banner is answered, immediately
before the first write — never across it, because the driver declares a lock
stale when `started` is older than an hour, and an operator who steps away with a
prompt open would hand it over on a timer.

It also refuses inside the nightly's window even when the lock is free, and the
window is the **union** over every repo in `SCED-tools/config/sync-schedule.json`
— today 02:02–04:27 KST. Never hardcoded.

### The five lock cases

All five are exit **4**, and a run that hits any of them has written nothing
since its last committed batch and needs no recovery.

| case | what happened | what to do |
|---|---|---|
| **acquire lost (`EEXIST`)** | someone completed an acquisition inside the gap between `mkdir` and the `owner` write | nothing was written, nothing needs cleaning up. Re-run after the nightly |
| **lock is held** | the owner file names a live process | read the owner line; `repo=SCED*` is the driver, `repo=koreanize` is another koreanize |
| **lock was taken over** | the owner names another pid *mid-write* | the lock is **not** deleted — doing so would destroy the driver's owner file and leave two writers believing they hold it. Re-plan and re-run |
| **owner file absent** | the directory exists with no `owner` inside it | this is a takeover *in progress*, not an orphan: the driver `rm -f`s the owner before writing its own. Touch nothing |
| **lock directory absent at release** | something else completed a takeover and released | reported rather than swallowed, because reaching it means this process ran believing it held a lock that had already been recycled |

### The silent night

If koreanize holds the lock when the driver fires, the driver exits **3** at a
*bare* `exit 3` — before its `trap cleanup EXIT` is installed and before its log
tee. `finish()` never runs, so **no Discord message of any grade is sent, no log
is written, no `last-run.json` is updated, and no failover is dispatched** (`3`
is not in the driver's default-deny dispatch allow-list). The fork simply does
not rebase until the next night's cron.

A silent night is therefore structurally invisible to CLAUDE.md's runbook §(B),
which is reached *from* a Discord grade. The entry point that finds it is:

```
SCED-tools/scripts/sced-run-now.sh --status
```

Read the `lock :` line. Its first field is
`pid=… repo=koreanize started=… host=…`, and that is the whole diagnosis.
`.local-sync/run/koreanize-<slug>.stamp` is the corroboration — it carries the
stage, the write roots, the planned file count and the base sha.

---

## Runbook

| symptom | first move |
|---|---|
| `exit 72` | `koreanize.sh plan --slug <s>`. **Nothing ran.** The named predecessor is either not `consumable` or is stale |
| `exit 13` from a stage | a stage ran far enough to read its input. Go to the file the message names |
| `exit 4` naming the lock | one of the five cases above |
| `exit 4` naming the window | you are inside 02:02–04:27 KST. Wait, or run tomorrow |
| `exit 4` naming `guard.write_roots` | the plan wants a path outside the langpack. **Nothing was read** |
| `exit 22` from `source` | two donors at differing grids. Read `triage`'s rationale in the report, then re-run with `--accept-donor-choice` |
| `exit 11` from `triage` | the model abstained or was low-confidence. `ai/triage/<stamp>/decide.json` → the `rules[]` entry with `"status": "fail"` |
| `exit 25` | the AI stage stopped **between** batches on budget. N of M are on disk; re-run the stage to resume at N+1. This is not a failure |
| `exit 65` | `claude` was unavailable or the credential is gone. Regenerate with `claude setup-token` into `~/.config/koreanize/env` |
| `exit 24` from `revert` | a recorded write did not restore. The report names the entry; then `git -C SCED-downloads diff --name-only` |
| `verify` fails C1–C11 | the check id is the diagnosis. `verify.json` → `checks[]`, and each check's `detail[]` names the objects |
| the build gate times out | a *wedge*, not a broken build. A real failure takes seconds and leaves a traceback; a wedge leaves the log ending at the last thing that worked. Look for an unanswered TCC prompt naming `TTSModManager-Darwin` |
| `--status` says the base moved | re-plan. **Do not revert** — nothing is lost, the plan is merely stale |
| the nightly produced no message of any grade | a silent night. `sced-run-now.sh --status`, then the koreanize stamp |
| a stage says `golden: SKIPPED` | the art chain is untested on this interpreter. That is a warning printed on *every* run, because a silent skip and a pass look identical once prose scrolls away |

### Reverting a write

```
koreanize.sh revert --slug <s> --stage <reuse|scaffold|objtext|register> --live
```

`revert` reads whichever record it finds — the stage report's `write_set[]`, the
pre-phase-2 `intentions.json`, or `data/locks/<slug>.lock.json` when the run
directory is gone. A `modify` is restored from its snapshot; a `create` is
deleted **only** when the file's sha256 equals the bytes this run planned to
write. Anything it cannot classify is exit 24 naming the path, rather than a
guess.

The `intentions.json` exists because `write_set[]` cannot describe a
half-committed batch: `post_sha256` is re-read after the batch returns, and a
crash between two renames means it never does.

---

## What koreanize will not do

- **No mutating git verb.** `kz_common.GIT_READONLY = {rev-parse, diff, status,
  ls-files}` is the complete set any module may pass to `git`, every invocation
  goes through one wrapper that asserts membership and refuses at exit 2, and
  `test_koreanize_gates.py` greps the package for one that does not.
  `kz_langpack.py` writes files and *prints* the commit command; you commit.
- **No `harvest` or `pdf` art source.** They appear in no enum, because you do
  not ship a schema wider than its producer.
- **No PDF upload**, no `Custom_Tile` / no-GMNotes objects, and **no launchd
  path, ever** — P0a enforces the last one at runtime in every module.
- **No writes under `SCED-tools/` except through one exemption.**
  `guard.data_root` is fixed at `scripts/koreanize/data/`, and `kz_config`'s
  mirror writer is the only code in the tool that opens a path under it.
  `kz_langpack.py` *hands* it the lock receipt rather than writing it, which is
  what keeps that module's refusal-before-read absolute.
