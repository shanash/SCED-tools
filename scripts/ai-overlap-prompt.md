<!--
  ai-overlap-prompt.md -- prompt template for the nightly overlap audit.
  Rendered by ai-overlap-classify.py into <RUN_DIR>/prompt.md and fed to
  `claude --print` on stdin (design .am/ai-rebase-conflict-resolution/design.md
  §4.3, §5.4). Prose lives here, never inside the Python.

  Placeholders are literal double-brace tokens (see the list in
  ai-overlap-classify.py's `replacements` table) replaced by the classifier. They
  are substituted with str.replace, NOT str.format, because this file contains
  JSON braces that must survive verbatim. The classifier refuses to emit a prompt
  that still carries an unsubstituted token, so no placeholder may appear in prose
  here -- not even as an example.
-->

# Rebase overlap audit — {{REPO}}

## 1. Task and authority

You are auditing a rebase result that will be **force-pushed to a public fork's
default branch and published as `releases/latest` tonight, unattended**. No human
will look at it before it ships.

Your output is a **decision manifest and nothing else**. You have no write tools:
no `Edit`, no `Write`, no `Bash` verb that mutates anything. You cannot edit the
repository, and you are not being asked to. A deterministic applier
(`ai-overlap-apply.py`) executes your manifest, and it can only produce bytes that
already exist in one of the four trees described below.

Run identity — copy these values into your manifest verbatim:

| field | value |
|---|---|
| `repo` | `{{REPO}}` |
| `merge_base` | `{{MERGE_BASE}}` |
| `fork_sha` | `{{FORK_SHA}}` |
| `upstream_sha` | `{{UPSTREAM_SHA}}` |
| `overlap_sha256` | `{{OVERLAP_SHA256}}` |
| `schema` | `1` |

The post-rebase result commit is `{{RESULT_SHA}}`. The run's artifact directory is
`{{RUN_DIR}}`; you may read anything inside it.

## 2. The four-blob model

The overlap set is **fork-changed ∩ upstream-changed since the merge base** —
{{OVERLAP_COUNT}} path(s). It is *not* git's conflict list, and that distinction is
the whole reason this audit exists.

For every overlap path *p* there are four blobs:

| symbol | ref | meaning |
|---|---|---|
| `B` | `{{MERGE_BASE}}` | merge base — what both sides started from |
| `U` | `{{UPSTREAM_SHA}}` | upstream's current content |
| `F` | `{{FORK_SHA}}` | the fork's content before the rebase |
| `R` | `{{RESULT_SHA}}` (`HEAD`) | the post-rebase result — **the bytes that would ship** |

Class membership below is **computed**, not asserted. You may rule on a class; you
may never redefine one, and a `class_id` that is not in the table is rejected.

{{CLASS_TABLE}}

**`C3.blend` is the dangerous class.** On 2026-06-27 this repository's sibling had
142 files touched by both sides. Git flagged **18** of them as conflicts and
silently three-way merged the other **124** — and the 124 were the semantically
broken ones. A resolution that only looks at what git complained about is strictly
weaker than the gate it replaces.

`seed_decided` marks the paths a plain three-way merge could not do on its own
(this run: **{{SEED_DECIDED_COUNT}}**). They are recorded as *context*, never as
the predicate for what you must examine.

## 3. The verb algebra

Your manifest assigns exactly one verdict per path. The verdict set is closed:

| verdict | what the applier does | resulting bytes |
|---|---|---|
| `keep_merge` | nothing | `R` |
| `take_upstream` | `git checkout {{UPSTREAM_SHA}} -- <path>` | `U` |
| `take_fork` | `git checkout {{FORK_SHA}} -- <path>` | `F` |
| `patch_json_fields` | parse `R` as JSON and replace each declared pointer with the value at that pointer on the declared side (`base` / `upstream` / `fork`); delete the key if it is absent there | `R` with only those pointers swapped |
| `abstain` | nothing — **stops the run** | — |

`patch_json_fields` pointers are whitelisted. Anything outside this pattern is a
hard rejection of the whole manifest:

```
^/(CardID|CustomDeck|Nickname|Description|GMNotes|States|AttachedDecals|CustomDeck/[A-Za-z0-9_-]{1,16})$
```

Two levels of ruling exist:

- **`classes[]`** — one ruling covering every path in a computed class. This is the
  only way hundreds of mechanically identical paths fit a bounded run. A
  `class_id` may name either a provenance class (`kind: class`, `C0`..`C8`) or a
  langpack shape sub-class (`kind: shape`, `L-a`/`L-b`/`L-c`) from the table above.
- **`paths[]`** — a per-path override.

A path can belong to both a provenance class and a shape. Resolution is by fixed
precedence, **path override > shape > provenance class**, because the shape is the
strictly more specific membership. Every path must end up with a ruling by that
rule, so a `classes[]` entry for a shape does not relieve you of covering the
non-langpack paths.

## 4. Aggregation rules — all of these must pass, or the run stops

1. **Coverage.** Every one of the {{OVERLAP_COUNT}} overlap paths must resolve to
   exactly one effective verdict, and no ruling may name a path outside the
   overlap set. A partial manifest is rejected outright.
2. **No `abstain` anywhere**, or the run stops and a human adjudicates.
3. **No `low` confidence anywhere**, or the run stops.
4. **`medium` confidence is permitted only on `keep_merge`.** Any
   `take_upstream` / `take_fork` / `patch_json_fields` at `medium` stops the run.
5. **A `seed_decided` path may never be `keep_merge`**, at any confidence, and must
   be ruled `high`. For every hunk git could not merge you must say which side wins
   and why.
6. Schema, pointer whitelist and envelope integrity must hold.
7. `merge_base`, `fork_sha`, `upstream_sha`, `repo` and `overlap_sha256` must equal
   the run values in §1.

**Abstaining stops the run. That is a legitimate and expected outcome, not a
failure.** A stopped night costs one day of staleness. A wrong resolution costs a
broken public release that nobody reviewed. Do not manufacture confidence you do
not have; rule 4 exists so that "I accept git's merge but I am not certain" has a
truthful way to be expressed.

## 5. Policy

{{POLICY}}

## 6. Precedent — read this before ruling on any langpack card

{{PRECEDENT}}

## 7. Computed classification

`classes.json` (also on disk at `{{RUN_DIR}}/classes.json`):

```json
{{CLASSES_JSON}}
```

## 8. Material

Extracted blobs live under `{{RUN_DIR}}/material/<n>/` as `base`, `upstream`,
`fork`, `merged` and `merge-file.diff3`. Read them with your `Read` / `Grep`
tools; you may also use read-only git verbs (`git show`, `git diff`, `git log`,
`git cat-file`, `git ls-tree`, `git rev-parse`, `git merge-base`,
`git merge-file`) inside the worktree.

{{MATERIAL_INDEX}}

{{INLINE_DIFF3}}

## 9. Output

Emit **one JSON object** conforming to this schema, and nothing else — no prose,
no explanation outside the `rationale` fields.

```json
{{SCHEMA}}
```

Write a `summary` that a human reading the Discord notification at 02:20 could act
on: what the bulk class was, what you did with it, and how many exceptions there
were.
