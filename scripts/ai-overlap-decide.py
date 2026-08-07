#!/usr/bin/env python3
"""Validate an AI decision manifest and decide proceed / stop.

Part of the ``ai-rebase-conflict-resolution`` feature
(.am/ai-rebase-conflict-resolution/design.md §3.1, §5.5, §6 S5). Stage 3 of

    classify (ai-overlap-classify.py)
      -> adjudicate (claude -p --output-format json)
      -> DECIDE (this script)
      -> apply (ai-overlap-apply.py)

This script is the only place that knows the manifest contract. It owns the
JSON Schema that is handed to ``claude --json-schema`` (``--emit-schema``) and
re-validates the returned document against that same literal, so the schema can
never drift between what the model was told and what is enforced.

What it does, in order:

  1. Extract the manifest from the ``--output-format json`` envelope (the
     ``result`` field, with ``````` fences stripped), or read a manifest
     directly with ``--manifest`` (rehearsal / test path).
  2. Validate it against the schema (stdlib-only draft-07 subset -- the
     ``jsonschema`` package is deliberately not a dependency of this workspace).
  3. Expand class rulings + path overrides into exactly one *effective verdict*
     per path in the gate's overlap set.
  4. Evaluate the aggregation rules -- **seven** under ``--mode audit``, **ten**
     under ``--mode drive``. All must pass.
  5. Write ``decide.json`` (per-rule pass/fail, counts, and the effective
     verdict table that ``ai-overlap-apply.py`` / ``ai-rebase-verify.py``
     consumes) and a normalised ``manifest.json``.

Two modes, two schemas, one owner:

  --mode audit  (default)  approach (a), .am/ai-rebase-conflict-resolution
      Manifest schema 1. The AI holds no write tool, emits an INSTRUCTION, and
      ``ai-overlap-apply.py`` executes it through a closed verb algebra. This
      path is preserved byte-for-byte -- it remains the only viable shape for
      SCED-downloads (claude-driven-rebase-deploy design §5.11).

  --mode drive             approach (b), .am/claude-driven-rebase-deploy
      Manifest schema 2. Claude drives a real ``git rebase``, so the tree is
      already resolved when the manifest arrives and the manifest is an
      ATTESTATION, not an instruction. ``ai-rebase-verify.py`` checks the tree
      against it.

**What this script can and cannot check.** It sees the manifest, ``classes.json``
and the envelope; it never opens a worktree. Rules that need the tree -- does
``HEAD:<path>`` really equal the declared ``result_blob``, is the seed->result
diff really confined to ``overlap.txt`` -- belong to ``ai-rebase-verify.py``, and
that is why design §5.5 gives rule 9 the verifier's exit code (67) while rules 8
and 10 exit 11 here.

Class membership is NOT expressible in the manifest: it comes from
``classes.json``, which was computed deterministically before the model ran.
A ruling may name a class, it may never redefine one.

Usage:
  ai-overlap-decide.py --emit-schema <FILE>

  ai-overlap-decide.py --run-dir <DIR>
      [--classes FILE]      (default: RUN_DIR/classes.json)
      [--overlap FILE]      (default: RUN_DIR/overlap.txt)
      [--envelope FILE]     (default: RUN_DIR/claude-envelope.json)
      [--manifest FILE]     (bypass the envelope; rehearsal / table-driven tests)
      [--out FILE]          (default: RUN_DIR/decide.json)
      [--manifest-out FILE] (default: RUN_DIR/manifest.json)

Exit codes:
   0  proceed -- every rule passed
   1  usage error
  11  stop by policy (abstain / low confidence / medium override / seed-decided
      keep_merge). The tree is untouched; a human adjudicates.
  66  manifest invalid (schema / coverage / digest / pointer whitelist)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from sced_io import atomic_write_text

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_STOP = 11
EXIT_INVALID = 66

# Design §3.1. Pointers the applier is allowed to swap. Anything else is a
# manifest that could mix a grid from one side with a URL from the other -- the
# exact 2026-06-27 failure -- so it is rejected rather than sanitised.
POINTER_WHITELIST = re.compile(
    r"^/(CardID|CustomDeck|Nickname|Description|GMNotes|States|AttachedDecals"
    r"|CustomDeck/[A-Za-z0-9_-]{1,16})$"
)

VERDICTS = ("keep_merge", "take_upstream", "take_fork", "patch_json_fields", "abstain")
CONFIDENCES = ("high", "medium", "low")

# Schema 2 / --mode drive (.am/claude-driven-rebase-deploy/design.md §3.1).
#
# `keep_merge` is renamed `keep_auto`, and the rename is not cosmetic. Under (a)
# `keep_merge` was the NO-OP verdict -- nothing had been written yet and the
# applier would write nothing -- which is why `medium` confidence was tolerated
# on it. Under (b) the tree is already resolved when the manifest arrives, so
# nothing is a no-op: every byte was produced by claude's rebase. The name has to
# say what is actually being asserted, and that assertion is now machine-checkable
# (rule 4 here, check 3 in ai-rebase-verify.py).
#
# `hand_merge` is the one verb that escapes the closed byte algebra: it writes
# content present in no single side. Rule 8 confines it to paths git actually
# conflicted on, and caps it.
VERDICTS_V2 = ("keep_auto", "take_upstream", "take_fork", "patch_json_fields",
               "hand_merge", "abstain")
BYTES_FROM = ("auto", "upstream", "fork", "auto+pointers", "hand")

# The provenance a verdict implies. A manifest that disagrees with itself here is
# not a judgement call, it is a contradiction, so it is rejected rather than
# reconciled.
VERDICT_BYTES = {
    "keep_auto": "auto",
    "take_upstream": "upstream",
    "take_fork": "fork",
    "patch_json_fields": "auto+pointers",
    "hand_merge": "hand",
}

# Default for SCED_SYNC_AI_MAX_HAND_MERGE. A night proposing more hand-merges
# than this is a night that needs a person: the review burden is the bound, not
# the machinery.
DEFAULT_MAX_HAND_MERGE = 5

# Design §3.1. Passed verbatim to `claude --json-schema` and re-applied here.
MANIFEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "repo", "merge_base", "fork_sha", "upstream_sha",
                 "overlap_sha256", "classes", "paths"],
    "properties": {
        "schema": {"const": 1},
        "repo": {"enum": ["SCED", "SCED-downloads"]},
        "merge_base": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "fork_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "upstream_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "overlap_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "summary": {"type": "string", "maxLength": 600},
        "classes": {"type": "array", "maxItems": 64, "items": {"$ref": "#/$defs/ruling"}},
        "paths": {"type": "array", "maxItems": 2000, "items": {"$ref": "#/$defs/ruling"}},
    },
    "$defs": {
        "verdict": {"enum": list(VERDICTS)},
        "ruling": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "confidence", "rationale"],
            "properties": {
                "class_id": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,64}$"},
                "path": {"type": "string", "minLength": 1, "maxLength": 512},
                "verdict": {"$ref": "#/$defs/verdict"},
                "confidence": {"enum": list(CONFIDENCES)},
                "rationale": {"type": "string", "minLength": 10, "maxLength": 400},
                "patches": {
                    "type": "array", "minItems": 1, "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["pointer", "from"],
                        "properties": {
                            "pointer": {"type": "string"},
                            "from": {"enum": ["base", "upstream", "fork"]},
                        },
                    },
                },
            },
        },
    },
}


def _manifest_schema_v2() -> dict:
    """Schema 2, derived from schema 1 so the shared parts cannot drift apart.

    Everything schema 1 requires, schema 2 still requires. What it adds is the
    attestation machinery: two tree OIDs the shell observed itself (run binding,
    rule 7), a declared commit-drop ledger (rule 10), and three per-ruling fields
    that bind the ruling to specific bytes rather than to a promise.
    """
    import copy
    s = copy.deepcopy(MANIFEST_SCHEMA)
    s["properties"]["schema"] = {"const": 2}
    s["properties"]["mode"] = {"const": "drive"}
    # 40-hex tree OIDs. The shell recomputes both and rule 7 compares.
    for key in ("seed_tree", "result_tree"):
        s["properties"][key] = {"type": "string", "pattern": "^[0-9a-f]{40}$"}
    # Declared, not observed. ai-rebase-verify.py check 1 counts the real thing;
    # having the model state it too is what lets rule 10 catch an UNDECLARED drop
    # here, at exit 11, instead of only at exit 67 after the tree is built.
    s["properties"]["commits_replayed"] = {"type": "integer", "minimum": 0,
                                           "maximum": 10000}
    s["properties"]["commits_dropped"] = {
        "type": "array", "maxItems": 64,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["subject", "reason"],
            "properties": {
                "subject": {"type": "string", "minLength": 1, "maxLength": 200},
                "reason": {"type": "string", "minLength": 10, "maxLength": 400},
            },
        },
    }
    s["required"] = s["required"] + ["mode", "seed_tree", "result_tree"]
    s["$defs"]["verdict"] = {"enum": list(VERDICTS_V2)}
    ruling = s["$defs"]["ruling"]["properties"]
    # The strongest single replacement for (a)'s byte-provenance check on
    # hand-merged paths: the manifest names the exact blob, and the shell
    # recomputes `git rev-parse HEAD:<path>` and compares.
    ruling["result_blob"] = {"type": "string", "pattern": "^[0-9a-f]{40}$"}
    # Claude's own declaration of what git raised. Cross-checked against the
    # deterministic merge-file oracle; the UNION is what must be `high`.
    ruling["git_conflicted"] = {"type": "boolean"}
    ruling["bytes_from"] = {"enum": list(BYTES_FROM)}
    # Schema 1's 400-char cap is too tight for (b) and was measured to be so: the
    # F-1 acceptance case requires a rationale that names BOTH the conflicting
    # hunk and the region git auto-merged away (design §5.3), and the design's own
    # worked example of exactly that manifest (§3.1) is 495 characters. A cap that
    # rejects the design's model answer is a cap that would push the model to
    # under-explain the one ruling that most needs explaining. Schema 1 keeps 400
    # untouched -- the (a) path stays byte-for-byte.
    ruling["rationale"] = {"type": "string", "minLength": 10, "maxLength": 800}
    return s


MANIFEST_SCHEMA_V2 = _manifest_schema_v2()

SCHEMA_FOR_VERSION = {1: MANIFEST_SCHEMA, 2: MANIFEST_SCHEMA_V2}
VERDICTS_FOR_VERSION = {1: VERDICTS, 2: VERDICTS_V2}
# The no-op-shaped verdict of each schema: the one a `medium` confidence may ride.
KEEP_VERDICT = {1: "keep_merge", 2: "keep_auto"}


# --------------------------------------------------------------- schema engine
def _resolve_ref(root: dict, ref: str):
    """Resolve a local ``#/a/b`` JSON pointer reference inside ``root``."""
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref: {ref}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate_schema(instance, schema, root=None, where="$") -> list[str]:
    """Validate ``instance`` against the draft-07 subset this design uses.

    Returns a list of human-readable violations (empty == valid). Only the
    keywords actually present in MANIFEST_SCHEMA are implemented; an unknown
    keyword is ignored rather than silently treated as a pass of something
    stricter, because the schema is a literal in this file and not user input.
    """
    root = schema if root is None else root
    errs: list[str] = []

    if "$ref" in schema:
        return validate_schema(instance, _resolve_ref(root, schema["$ref"]), root, where)

    if "const" in schema and instance != schema["const"]:
        errs.append(f"{where}: expected const {schema['const']!r}, got {instance!r}")
        return errs

    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{where}: {instance!r} not in {schema['enum']}")
        return errs

    typ = schema.get("type")
    if typ == "object":
        if not isinstance(instance, dict):
            errs.append(f"{where}: expected object, got {type(instance).__name__}")
            return errs
        for key in schema.get("required", []):
            if key not in instance:
                errs.append(f"{where}: missing required key {key!r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errs.append(f"{where}: unexpected key {key!r}")
        for key, sub in props.items():
            if key in instance:
                errs.extend(validate_schema(instance[key], sub, root, f"{where}.{key}"))
    elif typ == "array":
        if not isinstance(instance, list):
            errs.append(f"{where}: expected array, got {type(instance).__name__}")
            return errs
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{where}: {len(instance)} items < minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errs.append(f"{where}: {len(instance)} items > maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errs.extend(validate_schema(item, item_schema, root, f"{where}[{i}]"))
    elif typ == "string":
        if not isinstance(instance, str):
            errs.append(f"{where}: expected string, got {type(instance).__name__}")
            return errs
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errs.append(f"{where}: {instance!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errs.append(f"{where}: length {len(instance)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errs.append(f"{where}: length {len(instance)} > maxLength {schema['maxLength']}")

    return errs


# ------------------------------------------------------------------------- io
def die(code: int, msg: str) -> None:
    print(f"DecideError: {msg}", file=sys.stderr)
    sys.exit(code)


def load_json(path: Path, code: int):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        die(code, f"cannot read {path}: {exc}")


def dumps_faithful(data) -> str:
    """2-space indent, ensure_ascii=False, NO trailing newline (house format,
    apply-revert-decisions.py:90-93)."""
    return json.dumps(data, indent=2, ensure_ascii=False)


FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


def extract_manifest_text(result: str) -> str:
    """Strip an optional fenced code block from the envelope's ``result``.

    With ``--json-schema`` the CLI returns bare JSON, but a fallback-model run or
    a future CLI version may fence it. Anything still unparseable after this is
    rejected by the caller -- never repaired.
    """
    m = FENCE_RE.match(result)
    return m.group(1) if m else result.strip()


# ------------------------------------------------------------------ pointers
def check_pointers(rulings: list[dict], label: str) -> list[str]:
    errs: list[str] = []
    for i, r in enumerate(rulings):
        verdict = r.get("verdict")
        patches = r.get("patches")
        ident = r.get("class_id") or r.get("path") or f"#{i}"
        if verdict == "patch_json_fields":
            if not patches:
                errs.append(f"{label}[{ident}]: patch_json_fields without 'patches'")
                continue
            for p in patches:
                ptr = p.get("pointer", "")
                if not POINTER_WHITELIST.match(ptr):
                    errs.append(f"{label}[{ident}]: pointer {ptr!r} is not whitelisted")
        elif patches:
            errs.append(f"{label}[{ident}]: 'patches' present on verdict {verdict!r}")
    return errs


# ------------------------------------------------------------------- decision
def build_effective(overlap: list[str], classes_doc: dict, manifest: dict):
    """Expand class rulings + path overrides into one verdict per overlap path.

    A path belongs to a provenance class (``C0``..``C8``) and, for langpack
    cards, additionally to a shape sub-class (``L-a``/``L-b``/``L-c``). Both are
    rulable, so resolution is by fixed precedence -- **path override > shape >
    class** -- because the shape is the strictly more specific membership. The
    precedence is stated in the prompt; a redundant double match is recorded in
    ``ambiguous`` for audit but is not a failure.

    Returns (effective, errors, ambiguous). ``effective`` is ordered like
    overlap.txt.
    """
    errs: list[str] = []
    ambiguous: list[str] = []
    known_classes = set(classes_doc.get("classes", {}).keys())
    path_class = {p["path"]: p.get("class") for p in classes_doc.get("paths", [])}
    path_shape = {p["path"]: p.get("shape") for p in classes_doc.get("paths", [])}
    seed_decided = set(classes_doc.get("seed_decided", []))
    overlap_set = set(overlap)

    by_class: dict[str, dict] = {}
    for r in manifest.get("classes", []):
        cid = r.get("class_id")
        if not cid:
            errs.append(f"classes[]: ruling without 'class_id' (verdict {r.get('verdict')!r})")
            continue
        if cid not in known_classes:
            errs.append(f"classes[]: unknown class_id {cid!r} -- classes are computed, "
                        f"not declarable")
            continue
        if cid in by_class:
            errs.append(f"classes[]: duplicate class_id {cid!r}")
            continue
        by_class[cid] = r

    by_path: dict[str, dict] = {}
    for r in manifest.get("paths", []):
        p = r.get("path")
        if not p:
            errs.append(f"paths[]: ruling without 'path' (verdict {r.get('verdict')!r})")
            continue
        if p not in overlap_set:
            errs.append(f"paths[]: {p!r} is not in the gate's overlap set")
            continue
        if p in by_path:
            errs.append(f"paths[]: duplicate path {p!r}")
            continue
        by_path[p] = r

    effective = []
    for p in overlap:
        cls = path_class.get(p)
        shape = path_shape.get(p)
        shape_ruling = by_class.get(shape) if shape else None
        class_ruling = by_class.get(cls) if cls else None
        if by_path.get(p) is not None:
            ruling, source = by_path[p], "path"
        elif shape_ruling is not None:
            ruling, source = shape_ruling, "shape"
        else:
            ruling, source = class_ruling, "class"
        if ruling is None:
            errs.append(f"coverage: {p!r} (class {cls!r}, shape {shape!r}) has no "
                        f"class ruling and no path override")
            continue
        if (source == "shape" and class_ruling is not None
                and class_ruling.get("verdict") != shape_ruling.get("verdict")):
            ambiguous.append(f"{p}: shape {shape} says {shape_ruling.get('verdict')}, "
                             f"class {cls} says {class_ruling.get('verdict')} "
                             f"-- shape wins")
        effective.append({
            "path": p,
            "class": cls,
            "shape": shape,
            "class_id": ruling.get("class_id") if source != "path" else None,
            "source": source,
            "verdict": ruling.get("verdict"),
            "confidence": ruling.get("confidence"),
            "rationale": ruling.get("rationale"),
            "patches": ruling.get("patches"),
            "seed_decided": p in seed_decided,
            # Schema-2 only; absent (None) under schema 1, where no rule reads
            # them. Carried on the effective record so ai-rebase-verify.py's
            # blob binding can consume decide.json rather than re-deriving the
            # class/shape/path precedence a second time.
            "result_blob": ruling.get("result_blob"),
            "git_conflicted": ruling.get("git_conflicted"),
            "bytes_from": ruling.get("bytes_from"),
        })
    return effective, errs, ambiguous


def evaluate_rules(overlap, classes_doc, manifest, effective, cov_errs,
                   schema_errs, envelope, envelope_path, overlap_sha256,
                   mode="audit", max_hand_merge=DEFAULT_MAX_HAND_MERGE):
    """Design §5.5. Returns a list of rule records, each with pass/fail + exit.

    ``mode="audit"`` evaluates exactly the seven rules of the (a) design, with
    identical names, order and semantics -- that is the regression guard on the
    approach-(a) path, which stays alive for SCED-downloads.

    ``mode="drive"`` evaluates ten. Rules 1/2/3/6/7 keep their names; 4 and 5 are
    re-founded; 8/9/10 are new.

    **Division of labour.** This script sees the manifest, ``classes.json`` and
    the envelope -- it never sees a worktree. So it evaluates every rule's
    manifest-internal half, and ``ai-rebase-verify.py`` evaluates the half that
    can only be settled by looking at the tree (does ``HEAD:<path>`` really equal
    ``result_blob``; is the seed->result diff really confined to ``overlap.txt``).
    That split is why the design gives rule 9 exit **67**, the verifier's code,
    while rules 8 and 10 exit **11** here.
    """
    rules = []
    drive = mode == "drive"
    keep = KEEP_VERDICT[2 if drive else 1]

    def rule(n, name, ok, exit_code, detail=None, skipped=False):
        rules.append({"rule": n, "name": name,
                      "status": "skip" if skipped else ("pass" if ok else "fail"),
                      "exit_on_fail": exit_code, "detail": detail or []})

    # 1 -- coverage. Verbatim across (a) and (b), and under (b) it is promoted
    # from "one of seven" to THE load-bearing rule: it is what drags the agent
    # from git's conflict list (18) to the gate's overlap set (142). An agent that
    # quietly adjudicates only the conflicting subset fails here and never reaches
    # the push.
    rule(1, "coverage", not cov_errs, EXIT_INVALID, cov_errs[:50])

    # 2 -- no abstain. Same form; different meaning under (b): the tree is already
    # resolved, so abstain means "I resolved it and will not stand behind it", and
    # the resolution is DISCARDED with the scratch worktree.
    abst = [e["path"] for e in effective if e["verdict"] == "abstain"]
    rule(2, "no_abstain", not abst, EXIT_STOP,
         [f"{p}: abstain" for p in abst[:50]])

    # 3 -- no low confidence
    low = [e["path"] for e in effective if e["confidence"] == "low"]
    rule(3, "no_low_confidence", not low, EXIT_STOP,
         [f"{p}: low confidence" for p in low[:50]])

    # 4 -- medium is permitted only on the keep verdict.
    #
    # Under (a) that was a TRUST argument: keep_merge was the no-op, so "I accept
    # git's merge but I'm not certain" cost nothing. Under (b) there is no no-op,
    # so the tolerance is re-founded on something checkable: keep_auto now asserts
    # "these bytes equal the deterministic seed's bytes", which ai-rebase-verify.py
    # check 3 verifies against seed_tree. Here we enforce the manifest half -- the
    # declared provenance must actually say `auto`, and a keep_auto ruling must not
    # also claim invented bytes.
    med = [e for e in effective
           if e["confidence"] == "medium" and e["verdict"] != keep]
    detail4 = [f"{e['path']}: {e['verdict']} at medium" for e in med[:50]]
    if drive:
        for e in effective:
            bf = e.get("bytes_from")
            want = VERDICT_BYTES.get(e["verdict"])
            if e["verdict"] == "abstain":
                continue
            if bf is not None and want is not None and bf != want:
                detail4.append(f"{e['path']}: verdict {e['verdict']} but "
                               f"bytes_from {bf!r} (expected {want!r})")
    rule(4, f"medium_only_on_{keep}", not detail4, EXIT_STOP, detail4[:50])

    # 5 -- a path git itself could not merge must be ruled on explicitly, high.
    #
    # Under (a) the conflict set was one prediction: `seed_decided`, from a
    # per-blob merge-file. Under (b) it is the UNION of three signals, because
    # none of the three is complete -- merge-file cannot model a multi-commit
    # replay, claude's git_conflicted is self-reported, and the seed->result diff
    # is silent wherever the seed and claude agreed. Taking the union only widens
    # the `high` requirement, and each signal covers a different blind spot. The
    # third signal is observed by ai-rebase-verify.py; the first two are here.
    if drive:
        oracle = set(classes_doc.get("conflict_oracle")
                     or classes_doc.get("seed_decided", []))
        declared = {e["path"] for e in effective if e.get("git_conflicted") is True}
        union = oracle | declared
        seed_bad = [e for e in effective
                    if e["path"] in union
                    and (e["verdict"] == keep or e["confidence"] != "high")]
        detail5 = [f"{e['path']}: in the conflict union but {e['verdict']}/"
                   f"{e['confidence']}" for e in seed_bad[:50]]
        # A mismatch between the oracle and claude's declaration is RECORDED, not
        # fatal: per-blob merge-file is an approximation of a multi-commit replay,
        # so the two legitimately disagree.
        for p in sorted(oracle ^ declared):
            detail5.append(f"(note) {p}: oracle={p in oracle} declared={p in declared}")
        ok5 = not seed_bad
    else:
        seed_bad = [e for e in effective
                    if e["seed_decided"] and (e["verdict"] == keep
                                              or e["confidence"] != "high")]
        detail5 = [f"{e['path']}: seed-decided but {e['verdict']}/{e['confidence']}"
                   for e in seed_bad[:50]]
        ok5 = not seed_bad
    rule(5, "conflict_union_ruled_high" if drive else "seed_decided_ruled_high",
         ok5, EXIT_STOP, detail5[:80])

    # 6 -- envelope integrity + schema + pointer whitelist
    detail6: list[str] = list(schema_errs[:50])
    detail6 += check_pointers(manifest.get("classes", []), "classes")
    detail6 += check_pointers(manifest.get("paths", []), "paths")
    if envelope is None:
        rule(6, "envelope_and_schema", not detail6, EXIT_INVALID,
             detail6 + [f"(envelope integrity skipped: no envelope at {envelope_path})"])
    else:
        if not envelope.get("session_id"):
            detail6.append("envelope: session_id is empty or absent")
        if envelope.get("total_cost_usd") is None:
            detail6.append("envelope: total_cost_usd is absent")
        if drive:
            # `--permission-mode dontAsk` DENIES rather than grants, and the run
            # still exits 0, so the exit code cannot detect a stage that did
            # nothing. A denial is the only in-band signal that the grant was
            # mis-specified.
            denials = envelope.get("permission_denials")
            if denials:
                detail6.append(f"envelope: {len(denials)} permission denial(s) -- the "
                               f"tool grant is mis-specified: {denials[:5]}")
        rule(6, "envelope_and_schema", not detail6, EXIT_INVALID, detail6)

    # 7 -- run binding
    detail7 = []
    if manifest.get("overlap_sha256") != overlap_sha256:
        detail7.append(f"overlap_sha256 {manifest.get('overlap_sha256')!r} != "
                       f"sha256(overlap.txt) {overlap_sha256!r}")
    for key in ("merge_base", "fork_sha", "upstream_sha", "repo"):
        want = classes_doc.get(key)
        got = manifest.get(key)
        if want != got:
            detail7.append(f"{key} {got!r} != run value {want!r}")
    if drive:
        # seed_tree is an OID the shell observed itself. A manifest from another
        # run, or one describing a different reference tree, cannot pass.
        want_seed = classes_doc.get("seed_tree")
        got_seed = manifest.get("seed_tree")
        if want_seed and got_seed != want_seed:
            detail7.append(f"seed_tree {got_seed!r} != run value {want_seed!r}")
        if not manifest.get("result_tree"):
            detail7.append("result_tree is absent; ai-rebase-verify.py has nothing "
                           "to compare the worktree against")
    rule(7, "run_binding", not detail7, EXIT_INVALID, detail7)

    if not drive:
        return rules

    # 8 -- NEW. The hand-merge boundary: the bound this design owes.
    #
    # (b)'s whole point is that claude can write bytes present in no side. How far
    # does that open? Answer: NOT on a path git merged cleanly. There, both sides
    # agree textually, so any change is a semantic judgement about code neither
    # side flagged -- precisely the 124-class, precisely where unattended byte
    # invention is least defensible, and precisely where (a)'s closed algebra
    # still works. This is NOT a ban on repairing the 124: take_upstream and
    # take_fork stay legal there and are what a human does.
    oracle = set(classes_doc.get("conflict_oracle") or classes_doc.get("seed_decided", []))
    declared = {e["path"] for e in effective if e.get("git_conflicted") is True}
    union = oracle | declared
    hand = [e for e in effective if e["verdict"] == "hand_merge"]
    detail8 = [f"{e['path']}: hand_merge on a path git did not conflict on"
               for e in hand if e["path"] not in union][:50]
    if len(hand) > max_hand_merge:
        detail8.append(f"{len(hand)} hand_merge paths exceeds the cap of "
                       f"{max_hand_merge}; this night needs a person")
    rule(8, "hand_merge_boundary", not detail8, EXIT_STOP, detail8)

    # 9 -- NEW. Seed containment, manifest half. The TREE half (every path in
    # `git diff --name-only <seed_tree> <result_tree>` is in overlap.txt and is
    # not keep_auto) is ai-rebase-verify.py check 2, which is why the design gives
    # this rule exit 67. What is checkable here is the converse self-consistency:
    # a ruling that claims non-`auto` provenance must name the bytes it produced,
    # or there is nothing for check 3 to bind against.
    detail9 = []
    for e in effective:
        if e["verdict"] in ("abstain", keep):
            continue
        if not e.get("result_blob"):
            detail9.append(f"{e['path']}: {e['verdict']} without a result_blob -- "
                           f"the ruling is not bound to any bytes")
    rule(9, "seed_containment", not detail9, EXIT_INVALID, detail9[:50])

    # 10 -- NEW. Rebase integrity. Every difference between the fork's commit
    # count and what replayed must be DECLARED, with a reason. An undeclared drop
    # is how the SOURCE_REPO commit disappears silently -- which is exactly the
    # R-1 bug this feature shipped alongside.
    detail10 = []
    dropped = manifest.get("commits_dropped") or []
    expected = classes_doc.get("commits_expected")
    # NOTE: classes_doc's `commits_replayed` describes the SEED, not claude's
    # rebase, so it is deliberately NOT used here. The manifest's own declaration
    # is what closes the ledger; ai-rebase-verify.py check 1 then compares that
    # declaration against the tree it can actually count.
    declared = manifest.get("commits_replayed")
    if expected is not None:
        if len(dropped) > expected:
            detail10.append(f"commits_dropped declares {len(dropped)} drops but only "
                            f"{expected} commits were expected to replay")
        if declared is not None and declared + len(dropped) != expected:
            detail10.append(
                f"ledger does not close: {declared} replayed + {len(dropped)} declared "
                f"drops != {expected} expected. Every commit that did not replay must "
                f"be declared in commits_dropped[] with a reason -- an undeclared drop "
                f"is how the SOURCE_REPO commit disappears silently.")
    for i, d in enumerate(dropped):
        if not (d.get("reason") or "").strip():
            detail10.append(f"commits_dropped[{i}]: no reason given")
    rule(10, "rebase_integrity", not detail10, EXIT_STOP, detail10[:50])

    return rules


def histogram(items, key):
    out: dict[str, int] = {}
    for it in items:
        out[str(it.get(key))] = out.get(str(it.get(key)), 0) + 1
    return dict(sorted(out.items()))


# ----------------------------------------------------------------------- main
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--emit-schema", type=Path,
                   help="Write the manifest JSON Schema to this path and exit 0.")
    p.add_argument("--schema-version", type=int, choices=[1, 2], default=None,
                   help="Which schema to emit; defaults to the one --mode implies.")
    p.add_argument("--mode", choices=["audit", "drive"], default="audit",
                   help="audit: the seven rules of approach (a), schema 1, "
                        "unchanged. drive: the ten rules of approach (b), "
                        "schema 2.")
    p.add_argument("--run-dir", type=Path,
                   help="Run artifact directory (design §3.5).")
    p.add_argument("--classes", type=Path, help="default: RUN_DIR/classes.json")
    p.add_argument("--overlap", type=Path, help="default: RUN_DIR/overlap.txt")
    p.add_argument("--envelope", type=Path, help="default: RUN_DIR/claude-envelope.json")
    p.add_argument("--manifest", type=Path,
                   help="Read the manifest directly instead of from the envelope.")
    p.add_argument("--out", type=Path, help="default: RUN_DIR/decide.json")
    p.add_argument("--manifest-out", type=Path, help="default: RUN_DIR/manifest.json")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    version = args.schema_version or (2 if args.mode == "drive" else 1)
    schema = SCHEMA_FOR_VERSION[version]

    if args.emit_schema:
        atomic_write_text(args.emit_schema, dumps_faithful(schema) + "\n")
        if not args.quiet:
            print(f"Wrote manifest schema v{version} -> {args.emit_schema}")
        return EXIT_OK

    if not args.run_dir:
        die(EXIT_USAGE, "--run-dir is required (or use --emit-schema)")
    run_dir: Path = args.run_dir
    classes_path = args.classes or run_dir / "classes.json"
    overlap_path = args.overlap or run_dir / "overlap.txt"
    envelope_path = args.envelope or run_dir / "claude-envelope.json"
    out_path = args.out or run_dir / "decide.json"
    manifest_out = args.manifest_out or run_dir / "manifest.json"

    classes_doc = load_json(classes_path, EXIT_INVALID)
    try:
        overlap_bytes = overlap_path.read_bytes()
    except OSError as exc:
        die(EXIT_INVALID, f"cannot read {overlap_path}: {exc}")
    overlap_sha256 = hashlib.sha256(overlap_bytes).hexdigest()
    overlap = [ln for ln in overlap_bytes.decode("utf-8").split("\n") if ln]

    # ---- manifest source: explicit file, else the claude envelope
    envelope = None
    manifest_source = "envelope"
    if args.manifest:
        manifest_text = args.manifest.read_text(encoding="utf-8")
        manifest_source = "manifest-arg"
        if envelope_path.exists():
            envelope = load_json(envelope_path, EXIT_INVALID)
    else:
        envelope = load_json(envelope_path, EXIT_INVALID)
        result = envelope.get("result")
        manifest_text = extract_manifest_text(result) if isinstance(result, str) else None
        # A multi-turn session that ends on a tool error can produce prose instead
        # of JSON; --json-schema was only ever validated on single-turn read-only
        # runs (design B-4). The agent is therefore REQUIRED to also write
        # manifest.json to the run dir, giving two independent paths. Falling back
        # is not laxity -- both paths are validated identically below.
        fallback = run_dir / "manifest.json"
        if manifest_text is not None:
            try:
                json.loads(manifest_text)
            except json.JSONDecodeError:
                manifest_text = None
        if manifest_text is None:
            if not fallback.is_file():
                die(EXIT_INVALID,
                    f"{envelope_path}: 'result' did not parse as JSON and there is no "
                    f"fallback manifest at {fallback}")
            manifest_text = fallback.read_text(encoding="utf-8")
            manifest_source = "run-dir-fallback"

    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        die(EXIT_INVALID, f"manifest is not valid JSON: {exc}")
    if not isinstance(manifest, dict):
        die(EXIT_INVALID, f"manifest is a {type(manifest).__name__}, expected object")

    max_hand_merge = DEFAULT_MAX_HAND_MERGE
    raw = os.environ.get("SCED_SYNC_AI_MAX_HAND_MERGE", "").strip()
    if raw.isdigit():
        max_hand_merge = int(raw)

    schema_errs = validate_schema(manifest, schema)
    effective, cov_errs, ambiguous = build_effective(overlap, classes_doc, manifest)
    rules = evaluate_rules(overlap, classes_doc, manifest, effective, cov_errs,
                           schema_errs, envelope, envelope_path, overlap_sha256,
                           mode=args.mode, max_hand_merge=max_hand_merge)

    failed = [r for r in rules if r["status"] == "fail"]
    if any(r["exit_on_fail"] == EXIT_INVALID for r in failed):
        outcome, exit_code = "invalid", EXIT_INVALID
    elif failed:
        outcome, exit_code = "stop", EXIT_STOP
    else:
        outcome, exit_code = "proceed", EXIT_OK

    reason = "; ".join(f"rule {r['rule']} ({r['name']})" for r in failed)
    report = {
        # decide.json's own schema tracks the manifest schema it evaluated, so a
        # reader can tell the seven-rule report from the ten-rule one without
        # counting rules.
        "schema": version,
        "mode": args.mode,
        "manifest_source": manifest_source,
        "outcome": outcome,
        "exit_code": exit_code,
        "reason": reason,
        "repo": classes_doc.get("repo"),
        "merge_base": classes_doc.get("merge_base"),
        "fork_sha": classes_doc.get("fork_sha"),
        "upstream_sha": classes_doc.get("upstream_sha"),
        "overlap_count": len(overlap),
        "overlap_sha256": overlap_sha256,
        "manifest_sha256": hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
        "summary": manifest.get("summary", ""),
        "rules": rules,
        "ambiguous_rulings": ambiguous[:50],
        "verdicts": histogram(effective, "verdict"),
        "confidence": histogram(effective, "confidence"),
        "classes": histogram(effective, "class"),
        "seed_decided_count": sum(1 for e in effective if e["seed_decided"]),
        "covered": len(effective),
        "session_id": (envelope or {}).get("session_id", ""),
        "total_cost_usd": (envelope or {}).get("total_cost_usd"),
        "num_turns": (envelope or {}).get("num_turns"),
        "effective": effective,
    }
    if args.mode == "drive":
        report.update({
            "seed_tree": manifest.get("seed_tree"),
            "result_tree": manifest.get("result_tree"),
            "commits_expected": classes_doc.get("commits_expected"),
            "commits_dropped": manifest.get("commits_dropped") or [],
            "hand_merge_paths": [e["path"] for e in effective
                                 if e["verdict"] == "hand_merge"],
            "max_hand_merge": max_hand_merge,
            "conflicts_oracle": len(classes_doc.get("conflict_oracle")
                                    or classes_doc.get("seed_decided", [])),
            "conflicts_declared": sum(1 for e in effective
                                      if e.get("git_conflicted") is True),
            "permission_denials": (envelope or {}).get("permission_denials") or [],
            "duration_api_ms": (envelope or {}).get("duration_api_ms"),
        })
    atomic_write_text(out_path, dumps_faithful(report) + "\n")
    atomic_write_text(manifest_out, dumps_faithful(manifest) + "\n")

    if not args.quiet:
        print(f"decide: {outcome} (exit {exit_code}) -- "
              f"{len(effective)}/{len(overlap)} paths covered")
        for r in rules:
            mark = {"pass": "ok  ", "fail": "FAIL", "skip": "skip"}[r["status"]]
            print(f"  {mark} rule {r['rule']} {r['name']}")
            if r["status"] == "fail":
                for d in r["detail"][:10]:
                    print(f"         - {d}")
        print(f"  verdicts: {report['verdicts']}")
        print(f"  report -> {out_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
