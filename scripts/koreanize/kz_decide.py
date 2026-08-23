#!/usr/bin/env python3
"""koreanize `decide` -- the AI manifest schema and the ten adjudication rules.

ART TIER (design §5.9): `#!/usr/bin/env python3`, Homebrew 3.14.x. It imports
nothing outside the stdlib in practice, but it is deliberately NOT a member of
`kz_common.STDLIB_TIER`: the tier is a declared subject set, and adding a module
to it is a promise that `/usr/bin/python3 -m py_compile` polices it.

SOLE OWNER OF THE MANIFEST SCHEMA (§3.7). All seven per-stage schemas live here,
`--emit-schema --stage <Sn>` emits the same literal this module re-validates
against, and nothing else in the tool may build one -- the "one owner" property
of `ai-overlap-decide.py`: *"so the schema can never drift between what the model
was told and what is enforced."*

`AI_STAGE_MAP` is **not** rebuilt here. It is `kz_config`'s (§3.2) and the
dependency is one-way -- `kz_decide` imports `kz_config`, never the reverse.
A cycle between two module bodies resolves by whichever import ran first, and it
would drag this unpoliced art-tier module into the stdlib tier's import graph.

WHAT THIS MODULE OWNS
  - the seven stage schemas: universe == batch unit, per-field payloads inside
    `value` (§3.7). A universe finer than the batch unit is unsatisfiable by
    construction, so the schema states the identity once.
  - the ten rules of §4.4, with their scope: rules 1, 7 and 10 evaluate over the
    UNION of every batch against the whole `inputs.json.universe[]`; rules 2, 3,
    4, 5, 6, 8 and 9 evaluate PER BATCH and any batch failing fails the stage.
  - the cross-batch merge into `manifest.merged.json`. A `unit_id` appearing in
    two batches is a rule 1 failure (66), never a silent last-write-wins.
  - the multi-turn fallback: `out/manifest.json` is accepted when the envelope's
    `result` does not parse, and BOTH paths are validated identically, per batch
    (`ai-overlap-decide.py:761-765`).
  - the two-tier aggregation of `ai-overlap-decide.py:799-805`: any 66-rule
    failing => 66; else any failure => 11; else 0. There is no 67 tier here --
    67 belongs to the artifact verifier (`kz_checkers.py`), which evaluates a
    rule this module does not.
  - `--replay DIR`, which re-runs merge + decide from a recorded run with no
    `claude`, no credential, no network and no cost.

Exit codes (§4.2). This module's DECISION is one of {0, 11, 66} and nothing else:
   0  proceed -- every rule passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a (launchd) / P0b (nightly scratch worktree)
  13  precondition -- the recorded run itself is missing: no `inputs.json`, no
      `batch-NN/` directory. A manifest that is PRESENT but unparseable is not a
      precondition, it is an invalid manifest, and it is rule 6 at 66. §4.2 lists
      "manifest missing/malformed" under 13; the split is drawn at "is there a
      run to decide about", so that the decision itself can never leave {0,11,66}
      as §4.4 requires.
  11  stop by policy -- abstain / low confidence / a rule 2,3,4,5,8,10 failure
  66  AI manifest invalid -- schema, coverage, or run binding (rules 1,6,7,9)
"""

import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402

MANIFEST_SCHEMA_VERSION = 1

# One-way, and never rebuilt: kz_config owns the map and asserts it total at
# import (§3.2). Reading it here is what makes "every S-id has a schema" a
# checkable property rather than two lists that agree by luck.
STAGE_NAMES = dict(kz.AI_STAGE_MAP)
STAGE_IDS = tuple(sorted(STAGE_NAMES))

CONFIDENCES = ("high", "medium", "low")
ABSTAIN = "abstain"

# The seven identity keys of rule 10, in the order §4.4 lists them.
IDENTITY_KEYS = ("arkham_id", "guid", "card_id", "deck_key", "cell",
                 "num_width", "num_height")

# Files the shim owns inside ASK_DIR. Rule 8 clause (b) asks whether the AGENT
# wrote outside `out/`; the shell's own artifacts are not the agent's writes.
SHIM_OWNED = ("claude-envelope.json", "observed.json", "claude-version.txt",
              "session.jsonl", "stage-error.txt", "shim.log")

BATCH_DIR_RE = re.compile(r"^batch-(\d{2,3})$")


# ---------------------------------------------------------------------------
# 1. The per-stage schemas (§3.7)
# ---------------------------------------------------------------------------
#
# universe == batch unit, and every per-unit payload lives inside `value`. The
# `unit` name itself is NOT declared here: it is `scenario.json`'s
# `ai.batch.<Sn>.unit`, copied into `inputs.json` by kz_ask, and a second
# declaration would be a second source of truth for one fact.

VERDICTS = {
    "S1": ("glossary", "rules_ref", "coined", ABSTAIN),
    "S2": ("translate", "passthrough", ABSTAIN),
    "S3": ("measure", "inherit", ABSTAIN),
    "S4": ("map", ABSTAIN),
    "S5": ("read", "illegible", ABSTAIN),
    "S6": ("defect", "tolerance", ABSTAIN),
    "S7": ("widen", "isolated", ABSTAIN),
}

# Rule 5's subject. §4.4 names four: "every `coined` term, every new icon pair,
# every new layout group, every `read` OCR field must be `high`". S6 and S7 are
# not named there and are decided here on the same principle -- a verdict that
# CREATES something durable that no prior record contains. `tolerance` writes a
# new `mask.residual_baseline[]` row that suppresses a gate finding for good, and
# `widen` authors a new checker; both are exactly as novel as a coined term.
# S2 has no novel verdict: `translate` restates a card that already exists.
NOVEL_VERDICTS = {
    "S1": ("coined",),
    "S2": (),
    "S3": ("measure",),
    "S4": ("map",),
    "S5": ("read",),
    "S6": ("tolerance",),
    "S7": ("widen",),
}

# Rule 4 clause 2: "a ruling's declared provenance must match its verdict
# (S1 `coined` => no glossary `evidence[]`, etc.)". Stated as a table so the
# "etc." is enumerated rather than implied. A verdict absent from a stage's map
# carries no provenance requirement; `abstain` never does.
EVIDENCE_RULES = {
    "S1": {"glossary": {"min": 1, "require": "glossary"},
           "rules_ref": {"min": 1, "forbid": "glossary"},
           "coined": {"min": 0, "forbid": "glossary"}},
    "S2": {"translate": {"min": 1}},
    "S3": {"measure": {"min": 1}},
    "S4": {"map": {"min": 1}},
    "S5": {"read": {"min": 1}},
    # A `tolerance` extends the lock's residual baseline, so it must cite the
    # baseline it extends; a `defect` must cite the gate, and may NOT justify
    # itself by the baseline that would have excused it.
    "S6": {"defect": {"min": 1, "forbid": "lock"},
           "tolerance": {"min": 1, "require": "lock"}},
    "S7": {"widen": {"min": 1}},
}

_FIELDS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "maxLength": 400},
        "subname": {"type": "string", "maxLength": 400},
        "traits": {"type": "string", "maxLength": 400},
        "text": {"type": "string", "maxLength": 8000},
        "flavor": {"type": "string", "maxLength": 4000},
        "b_side": {"type": "string", "maxLength": 8000},
    },
}

_CONFIDENCE_MAP = {"type": "object", "additionalProperties": {"enum": list(CONFIDENCES)}}

VALUE_SCHEMA = {
    "S1": {"type": "object", "additionalProperties": False, "properties": {
        "ko": {"type": "string", "minLength": 1, "maxLength": 200},
        "romanization": {"type": "string", "maxLength": 200},
        "note": {"type": "string", "maxLength": 800}}},
    "S2": {"type": "object", "additionalProperties": False, "properties": {
        "fields": _FIELDS_SCHEMA,
        "markup_tokens": {"type": "array", "maxItems": 256,
                          "items": {"type": "string", "maxLength": 64}},
        "icon_tokens": {"type": "array", "maxItems": 256,
                        "items": {"type": "string", "maxLength": 64}}}},
    "S3": {"type": "object", "additionalProperties": False, "properties": {
        "windows": {"type": "object"},
        # Bounded here as well as in kz_config.check_window_margin: the manifest
        # is where the number first arrives, and 1..64 is the same bound §5.5
        # gives the layout file it will be written into.
        "window_margin_px": {"type": "integer", "minimum": 1, "maximum": 64},
        "frame_windows": {"type": "object"},
        "protect": {"type": "array", "maxItems": 64, "items": {"type": "string"}},
        "rot": {"enum": [0, 90, 180, 270]},
        "flavor_before_text": {"type": "boolean"},
        "measured_from": {"type": "array", "maxItems": 64,
                          "items": {"type": "string", "maxLength": 256}}}},
    "S4": {"type": "object", "additionalProperties": False, "properties": {
        "codepoint": {"type": "string", "pattern": "^U\\+[0-9A-F]{4,6}$"},
        # The anti-swap pair (§5.6), mandatory on a `map` by VALUE_REQUIRED below.
        "ink_fill": {"type": "number", "minimum": 0, "maximum": 1},
        "advance_em": {"type": "number", "minimum": 0, "maximum": 8}}},
    "S5": {"type": "object", "additionalProperties": False, "properties": {
        "fields": _FIELDS_SCHEMA,
        "per_field_confidence": _CONFIDENCE_MAP}},
    "S6": {"type": "object", "additionalProperties": False, "properties": {
        "owner_stage": {"enum": list(kz.STAGES)},
        "suggested_action": {"type": "string", "minLength": 3, "maxLength": 400}}},
    "S7": {"type": "object", "additionalProperties": False, "properties": {
        "predicate": {"type": "string", "minLength": 3, "maxLength": 800},
        "checker_filename": {"type": "string",
                             "pattern": "^[A-Za-z0-9_.-]+\\.py$"}}},
}

# Rule 9's manifest half: a non-`abstain` ruling that produces bytes must NAME
# them. These are the keys without which the ruling is not bound to anything.
VALUE_REQUIRED = {
    "S1": ("ko",),
    "S2": ("fields",),
    "S3": ("windows", "window_margin_px"),
    "S4": ("codepoint", "ink_fill", "advance_em"),
    "S5": ("fields", "per_field_confidence"),
    "S6": ("owner_stage", "suggested_action"),
    "S7": ("predicate", "checker_filename"),
}

EVIDENCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["file", "sha256"],
    "properties": {
        "file": {"type": "string", "minLength": 1, "maxLength": 256},
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "locator": {"type": "string", "maxLength": 256},
    },
}

# The identity echo rule 10 binds against. OPTIONAL on every ruling and closed to
# exactly the seven keys of §4.4 -- so a stage whose `value` has no room for an
# id (S6's is `{owner_stage, suggested_action}`) still has one place where an id
# can be stated, and therefore one place where it can be caught disagreeing with
# `card-text-en.json`. Without it rule 10 has nothing to bind against on any
# stage and the `identity-drift/` fixture class of §4.4 cannot exist.
IDENTITY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "arkham_id": {"type": "string", "minLength": 1, "maxLength": 32},
        "guid": {"type": "string", "pattern": "^[0-9a-fA-F]{6}$"},
        "card_id": {"type": "integer", "minimum": 0},
        "deck_key": {"type": "string", "minLength": 1, "maxLength": 16},
        "cell": {"type": "integer", "minimum": 0},
        "num_width": {"type": "integer", "minimum": 1, "maximum": 32},
        "num_height": {"type": "integer", "minimum": 1, "maximum": 32},
    },
}

COMMON_REQUIRED = ("schema", "stage", "slug", "batch", "batch_of",
                   "scenario_sha256", "inputs_sha256", "summary", "rulings",
                   "wrote", "declared_input_sha256")


def ruling_schema(sid):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["unit_id", "verdict", "confidence", "rationale", "value",
                     "reaches_pixels"],
        "properties": {
            # Drawn from THIS batch's inputs.json.universe[]; rule 1 enforces it.
            "unit_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "verdict": {"enum": list(VERDICTS[sid])},
            "confidence": {"enum": list(CONFIDENCES)},
            "rationale": {"type": "string", "minLength": 10, "maxLength": 800},
            "value": VALUE_SCHEMA[sid],
            "reaches_pixels": {"type": "boolean"},
            "identity": IDENTITY_SCHEMA,
            "evidence": {"type": "array", "maxItems": 64, "items": EVIDENCE_SCHEMA},
        },
    }


def stage_schema(sid):
    """The one literal handed to `claude --json-schema` AND re-validated here."""
    if sid not in STAGE_NAMES:
        kc.refuse(kc.EXIT_USAGE, "unknown stage id %r" % sid,
                  "the S-ids are %s (kz_config.AI_STAGE_MAP)" % list(STAGE_IDS))
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "koreanize AI manifest -- %s (%s)" % (sid, STAGE_NAMES[sid]),
        "type": "object", "additionalProperties": False,
        "required": list(COMMON_REQUIRED),
        "properties": {
            "schema": {"const": MANIFEST_SCHEMA_VERSION},
            "stage": {"const": sid},
            "slug": {"type": "string", "minLength": 1, "maxLength": 128},
            "batch": {"type": "integer", "minimum": 1, "maximum": 64},
            "batch_of": {"type": "integer", "minimum": 1, "maximum": 64},
            "scenario_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "inputs_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "summary": {"type": "string", "minLength": 10, "maxLength": 800},
            "rulings": {"type": "array", "minItems": 1, "maxItems": 512,
                        "items": ruling_schema(sid)},
            "wrote": {"type": "array", "maxItems": 64,
                      "items": {"type": "string", "minLength": 1, "maxLength": 256}},
            "declared_input_sha256": {
                "type": "object",
                "additionalProperties": {"type": "string",
                                         "pattern": "^[0-9a-f]{64}$"}},
        },
    }


# Totality, asserted at import: every S-id kz_config owns has a schema here, and
# nothing here names an S-id kz_config does not own. The three tables are the
# ones a new stage would be added to and forgotten in.
assert set(VERDICTS) == set(STAGE_NAMES), "VERDICTS is not total over AI_STAGE_MAP"
assert set(VALUE_SCHEMA) == set(STAGE_NAMES), "VALUE_SCHEMA is not total"
assert set(VALUE_REQUIRED) == set(STAGE_NAMES), "VALUE_REQUIRED is not total"
assert set(NOVEL_VERDICTS) == set(STAGE_NAMES), "NOVEL_VERDICTS is not total"
for _sid in STAGE_NAMES:
    assert ABSTAIN in VERDICTS[_sid], "%s has no abstain verdict" % _sid
    assert set(NOVEL_VERDICTS[_sid]) <= set(VERDICTS[_sid])
    assert set(EVIDENCE_RULES.get(_sid, {})) <= set(VERDICTS[_sid])
    assert set(VALUE_REQUIRED[_sid]) <= set(VALUE_SCHEMA[_sid]["properties"])


# ---------------------------------------------------------------------------
# 2. The draft-07 subset validator (ported from ai-overlap-decide.py:257-320)
# ---------------------------------------------------------------------------
#
# The `jsonschema` package is deliberately not a dependency of this workspace.
# Only the keywords the schemas above actually use are implemented; an unknown
# keyword is ignored rather than silently treated as a pass of something
# stricter, because the schema is a literal in this file and not user input.

def validate_schema(instance, schema, root=None, where="$"):
    root = schema if root is None else root
    errs = []

    if "$ref" in schema:
        node = root
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        return validate_schema(instance, node, root, where)

    if "const" in schema and instance != schema["const"]:
        return ["%s: expected const %r, got %r" % (where, schema["const"], instance)]
    if "enum" in schema and instance not in schema["enum"]:
        return ["%s: %r not in %s" % (where, instance, schema["enum"])]

    typ = schema.get("type")
    if typ == "object":
        if not isinstance(instance, dict):
            return ["%s: expected object, got %s" % (where, type(instance).__name__)]
        for key in schema.get("required", []):
            if key not in instance:
                errs.append("%s: missing required key %r" % (where, key))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for key in instance:
            if key in props:
                continue
            if extra is False:
                errs.append("%s: unexpected key %r" % (where, key))
            elif isinstance(extra, dict):
                errs.extend(validate_schema(instance[key], extra, root,
                                            "%s.%s" % (where, key)))
        for key, sub in props.items():
            if key in instance:
                errs.extend(validate_schema(instance[key], sub, root,
                                            "%s.%s" % (where, key)))
    elif typ == "array":
        if not isinstance(instance, list):
            return ["%s: expected array, got %s" % (where, type(instance).__name__)]
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append("%s: %d items < minItems %d"
                        % (where, len(instance), schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errs.append("%s: %d items > maxItems %d"
                        % (where, len(instance), schema["maxItems"]))
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errs.extend(validate_schema(item, item_schema, root,
                                            "%s[%d]" % (where, i)))
    elif typ == "string":
        if not isinstance(instance, str):
            return ["%s: expected string, got %s" % (where, type(instance).__name__)]
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errs.append("%s: %r does not match %s" % (where, instance, schema["pattern"]))
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errs.append("%s: length %d < minLength %d"
                        % (where, len(instance), schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errs.append("%s: length %d > maxLength %d"
                        % (where, len(instance), schema["maxLength"]))
    elif typ == "integer":
        # bool is a subclass of int in Python; a boolean where an integer is
        # declared is a type error, not a 0/1.
        if isinstance(instance, bool) or not isinstance(instance, int):
            return ["%s: expected integer, got %s" % (where, type(instance).__name__)]
        errs.extend(_bounds(instance, schema, where))
    elif typ == "number":
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            return ["%s: expected number, got %s" % (where, type(instance).__name__)]
        errs.extend(_bounds(instance, schema, where))
    elif typ == "boolean":
        if not isinstance(instance, bool):
            return ["%s: expected boolean, got %s" % (where, type(instance).__name__)]

    return errs


def _bounds(value, schema, where):
    errs = []
    if "minimum" in schema and value < schema["minimum"]:
        errs.append("%s: %r < minimum %r" % (where, value, schema["minimum"]))
    if "maximum" in schema and value > schema["maximum"]:
        errs.append("%s: %r > maximum %r" % (where, value, schema["maximum"]))
    return errs


# ---------------------------------------------------------------------------
# 3. Reading a recorded run
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)


def _read_json(path):
    """(document, error). Never raises on a malformed file -- a bad manifest is a
    rule 6 finding at 66, not a traceback."""
    if not os.path.exists(path):
        return None, "%s does not exist" % path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except ValueError as exc:
        return None, "%s is not valid JSON: %s" % (path, exc)
    except OSError as exc:
        return None, "%s is unreadable: %s" % (path, exc)


def extract_manifest_text(result):
    """Strip an optional fenced code block from the envelope's `result`.

    With `--json-schema` the CLI returns bare JSON, but a fallback-model run or a
    future CLI version may fence it. Anything still unparseable after this is
    rejected by the caller -- never repaired.
    """
    match = FENCE_RE.match(result)
    return match.group(1) if match else result.strip()


def batch_dirs(run_dir):
    """`batch-NN/` directories, in numeric order."""
    found = []
    for name in sorted(os.listdir(run_dir)):
        match = BATCH_DIR_RE.match(name)
        if match and os.path.isdir(os.path.join(run_dir, name)):
            found.append((int(match.group(1)), os.path.join(run_dir, name)))
    return sorted(found)


def observed_writes(batch_dir):
    """Every file under `ASK_DIR/out/`, as ASK_DIR-relative paths.

    Re-derived from disk at decide time rather than trusted from the shell, so
    `--replay` checks the same thing the live run did.
    """
    out_root = os.path.join(batch_dir, "out")
    found = []
    for dirpath, dirnames, filenames in os.walk(out_root):
        dirnames.sort()
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            found.append(os.path.relpath(full, batch_dir))
    return sorted(found)


def load_batch(index, batch_dir, sid):
    """Everything one batch contributes, with its manifest resolved by BOTH paths.

    `ai-overlap-decide.py:761-765`: a multi-turn session that ends on a tool error
    can produce prose instead of JSON, and `--json-schema` was only ever validated
    on single-turn read-only runs. The agent is therefore REQUIRED to also write
    `out/manifest.json`. Falling back is not laxity -- both paths are validated
    identically below.
    """
    record = {
        "index": index, "dir": batch_dir, "name": os.path.basename(batch_dir),
        "manifest": None, "manifest_source": None, "rulings": [],
        "schema_errors": [], "read_errors": [], "fallback_reason": None,
        "envelope": None,
        "observed": None, "inputs": None, "inputs_sha256": None,
        "out_files": observed_writes(batch_dir),
    }

    inputs_path = os.path.join(batch_dir, "inputs.json")
    doc, err = _read_json(inputs_path)
    if err:
        record["read_errors"].append(err)
    else:
        record["inputs"] = doc
        record["inputs_sha256"] = kc.sha256_file(inputs_path)

    envelope, err = _read_json(os.path.join(batch_dir, "claude-envelope.json"))
    if err:
        record["read_errors"].append(err)
    else:
        record["envelope"] = envelope

    observed, err = _read_json(os.path.join(batch_dir, "observed.json"))
    if err:
        record["read_errors"].append(
            "%s (the shim records rule 8's pre/post digests there)" % err)
    else:
        record["observed"] = observed

    text = None
    if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
        candidate = extract_manifest_text(envelope["result"])
        try:
            json.loads(candidate)
            text, record["manifest_source"] = candidate, "envelope"
        except ValueError:
            # NOT a read error: taking the second path is the DESIGNED behaviour
            # (§4.4, `ai-overlap-decide.py:761-765`), and the two paths are
            # validated identically below. Recording it as a read error made rule
            # 6 fail at 66 on exactly the multi-turn session the fallback exists
            # for, i.e. the fallback could never be taken successfully. It is
            # recorded as a reason so the report still says which path was used
            # and why; when the fallback ALSO fails, the branch below appends a
            # real read error and rule 6 fails on that.
            record["fallback_reason"] = (
                "claude-envelope.json: 'result' did not parse as JSON; "
                "fell back to the required second path out/manifest.json")
    if text is None:
        fallback = os.path.join(batch_dir, "out", "manifest.json")
        doc, err = _read_json(fallback)
        if err:
            record["read_errors"].append(
                "%s -- neither the envelope nor the required second path yielded a "
                "manifest" % err)
        else:
            text = json.dumps(doc)
            record["manifest_source"] = "out-manifest"

    if text is not None:
        manifest = json.loads(text)
        if not isinstance(manifest, dict):
            record["read_errors"].append(
                "manifest is a %s, expected object" % type(manifest).__name__)
        else:
            record["manifest"] = manifest
            record["rulings"] = [r for r in (manifest.get("rulings") or [])
                                 if isinstance(r, dict)]
            record["schema_errors"] = validate_schema(manifest, stage_schema(sid))
    return record


# ---------------------------------------------------------------------------
# 4. The ten rules (§4.4)
# ---------------------------------------------------------------------------

def _label(batch, unit_id):
    return "batch-%02d/%s" % (batch["index"], unit_id)


def _evidence_findings(sid, ruling):
    """Rule 4 clause 2 and rule 9's evidence half, from one table."""
    spec = EVIDENCE_RULES.get(sid, {}).get(ruling.get("verdict"))
    if spec is None:
        return []
    evidence = ruling.get("evidence") or []
    files = [str(e.get("file") or "") for e in evidence if isinstance(e, dict)]
    findings = []
    if len(evidence) < spec.get("min", 0):
        findings.append("verdict %r needs >= %d evidence entries, has %d"
                        % (ruling.get("verdict"), spec["min"], len(evidence)))
    need = spec.get("require")
    if need and not any(need in f for f in files):
        findings.append("verdict %r must cite %r; evidence names %s"
                        % (ruling.get("verdict"), need, files or "nothing"))
    forbid = spec.get("forbid")
    if forbid and any(forbid in f for f in files):
        findings.append("verdict %r may not cite %r; evidence names %s"
                        % (ruling.get("verdict"), forbid, files))
    return findings


def evaluate_rules(inputs, batches):
    """The ten rules, each recorded with its scope and its exit code.

    Rules 1, 7 and 10 evaluate over the UNION against the whole
    `inputs.json.universe[]`; rules 2, 3, 4, 5, 6, 8 and 9 evaluate PER BATCH and
    any batch failing fails the stage (§3.7).
    """
    sid = inputs.get("stage")
    universe = list(inputs.get("universe") or [])
    universe_set = set(universe)
    identity_table = inputs.get("identity") or {}
    material_sha = inputs.get("material_sha256") or {}
    novel = set(NOVEL_VERDICTS.get(sid, ()))
    required_value_keys = VALUE_REQUIRED.get(sid, ())

    rules = []

    def rule(number, name, scope, ok, exit_code, detail):
        rules.append({"rule": number, "name": name, "scope": scope,
                      "status": "pass" if ok else "fail",
                      "exit_on_fail": exit_code, "detail": list(detail)[:80]})

    # -- the union view, built once and shared by rules 1, 7 and 10 ------------
    effective = {}
    duplicates = []
    outside = []
    for batch in batches:
        for ruling in batch["rulings"]:
            unit_id = ruling.get("unit_id")
            if unit_id in effective:
                duplicates.append(
                    "%s: ruled in batch-%02d and batch-%02d -- a unit_id in two "
                    "batches is a coverage failure, never last-write-wins"
                    % (unit_id, effective[unit_id]["batch"], batch["index"]))
                continue
            effective[unit_id] = {"batch": batch["index"], "ruling": ruling}
            if unit_id not in universe_set:
                outside.append("%s: not in inputs.json.universe[]"
                               % _label(batch, unit_id))

    # 1 -- coverage (66, union). Exactly one effective ruling per unit.
    missing = ["%s: no ruling in any batch" % u for u in universe if u not in effective]
    rule(1, "coverage", "union", not (missing or outside or duplicates),
         kc.EXIT_AI_MANIFEST, missing + outside + duplicates)

    # 2 -- no_abstain (11, per batch)
    detail = [_label(b, r.get("unit_id")) + ": abstain"
              for b in batches for r in b["rulings"] if r.get("verdict") == ABSTAIN]
    rule(2, "no_abstain", "batch", not detail, kc.EXIT_POLICY, detail)

    # 3 -- no_low_confidence (11, per batch)
    detail = [_label(b, r.get("unit_id")) + ": low confidence"
              for b in batches for r in b["rulings"] if r.get("confidence") == "low"]
    rule(3, "no_low_confidence", "batch", not detail, kc.EXIT_POLICY, detail)

    # 4 -- medium_only_off_pixels (11, per batch). Two clauses: `medium` is
    #      permitted only where the ruling does not reach shipped pixels, and the
    #      declared provenance must match the verdict.
    detail = []
    for batch in batches:
        for ruling in batch["rulings"]:
            tag = _label(batch, ruling.get("unit_id"))
            if ruling.get("confidence") == "medium" and ruling.get("reaches_pixels"):
                detail.append("%s: medium confidence on a ruling that reaches pixels"
                              % tag)
            if ruling.get("verdict") != ABSTAIN:
                for finding in _evidence_findings(sid, ruling):
                    detail.append("%s: %s" % (tag, finding))
    rule(4, "medium_only_off_pixels", "batch", not detail, kc.EXIT_POLICY, detail)

    # 5 -- novel_ruled_high (11, per batch)
    detail = []
    for batch in batches:
        for ruling in batch["rulings"]:
            if ruling.get("verdict") in novel and ruling.get("confidence") != "high":
                detail.append("%s: %s at %s -- a novel ruling must be high"
                              % (_label(batch, ruling.get("unit_id")),
                                 ruling.get("verdict"), ruling.get("confidence")))
    rule(5, "novel_ruled_high", "batch", not detail, kc.EXIT_POLICY, detail)

    # 6 -- envelope_and_schema (66, per batch). `dontAsk` DENIES and still exits
    #      0, so a denial is the only in-band signal the grant is mis-specified.
    detail = []
    for batch in batches:
        prefix = "batch-%02d" % batch["index"]
        for err in batch["read_errors"]:
            detail.append("%s: %s" % (prefix, err))
        for err in batch["schema_errors"]:
            detail.append("%s: %s" % (prefix, err))
        envelope = batch["envelope"]
        if not isinstance(envelope, dict):
            detail.append("%s: no claude-envelope.json to check" % prefix)
            continue
        if not envelope.get("session_id"):
            detail.append("%s: envelope session_id is empty or absent" % prefix)
        if envelope.get("total_cost_usd") is None:
            detail.append("%s: envelope total_cost_usd is absent" % prefix)
        denials = envelope.get("permission_denials")
        if denials:
            detail.append("%s: %d permission denial(s) -- the tool grant is "
                          "mis-specified: %s" % (prefix, len(denials), denials[:5]))
    rule(6, "envelope_and_schema", "batch", not detail, kc.EXIT_AI_MANIFEST, detail)

    # 7 -- run_binding (66, union)
    detail = []
    seen_batches = {}
    for batch in batches:
        prefix = "batch-%02d" % batch["index"]
        manifest = batch["manifest"] or {}
        if batch["inputs_sha256"] is None:
            detail.append("%s: no inputs.json to bind against" % prefix)
        elif manifest.get("inputs_sha256") != batch["inputs_sha256"]:
            detail.append("%s: inputs_sha256 %r != sha256(inputs.json) %r"
                          % (prefix, manifest.get("inputs_sha256"),
                             batch["inputs_sha256"]))
        for key, want in (("slug", inputs.get("slug")),
                          ("stage", inputs.get("stage")),
                          ("scenario_sha256", inputs.get("scenario_sha256")),
                          ("batch_of", inputs.get("batch_of"))):
            if manifest.get(key) != want:
                detail.append("%s: %s %r != run value %r"
                              % (prefix, key, manifest.get(key), want))
        number = manifest.get("batch")
        if number != batch["index"]:
            detail.append("%s: manifest declares batch %r" % (prefix, number))
        elif number in seen_batches:
            detail.append("%s: batch %r already claimed by %s"
                          % (prefix, number, seen_batches[number]))
        else:
            seen_batches[number] = prefix
        declared = manifest.get("declared_input_sha256") or {}
        batch_material = (batch["inputs"] or {}).get("material_sha256") or material_sha
        for name in sorted(declared):
            if name not in batch_material:
                detail.append("%s: declared_input_sha256 names %r, which the shell "
                              "did not stage" % (prefix, name))
            elif declared[name] != batch_material[name]:
                detail.append("%s: declared_input_sha256[%r] %s != recorded %s"
                              % (prefix, name, declared[name][:16],
                                 batch_material[name][:16]))
    expected_batches = inputs.get("batch_of")
    if isinstance(expected_batches, int):
        got = sorted(seen_batches)
        if got != list(range(1, expected_batches + 1)):
            detail.append("batch set %s != 1..%d" % (got, expected_batches))
    rule(7, "run_binding", "union", not detail, kc.EXIT_AI_MANIFEST, detail)

    # 8 -- write_set (11, per batch). Three clauses; (c) is deliberately WIDER
    #      than the nightly's, which observes only ASK_DIR/out and the three
    #      checkouts -- a write to images-ko/, the glossary tree, or <run_dir>
    #      outside ASK_DIR would be caught by nothing there.
    detail = []
    for batch in batches:
        prefix = "batch-%02d" % batch["index"]
        manifest = batch["manifest"] or {}
        declared_writes = sorted(manifest.get("wrote") or [])
        observed = sorted(batch["out_files"])
        if declared_writes != observed:
            for path in sorted(set(observed) - set(declared_writes)):
                detail.append("%s: %s was written but is not in manifest.wrote[]"
                              % (prefix, path))
            for path in sorted(set(declared_writes) - set(observed)):
                detail.append("%s: manifest.wrote[] names %s, which is not under out/"
                              % (prefix, path))
        observation = batch["observed"]
        if not isinstance(observation, dict):
            detail.append("%s: no observed.json -- clause (b) and the three clause "
                          "(c) digests cannot be evaluated" % prefix)
            continue
        outside_out = observation.get("outside_out")
        if outside_out is None:
            detail.append("%s: observed.json declares no outside_out list" % prefix)
        elif outside_out:
            detail.append("%s: the agent wrote outside out/: %s"
                          % (prefix, sorted(outside_out)[:10]))
        digests = observation.get("digests") or {}
        for label in ("checkouts", "run_dir", "read_only"):
            entry = digests.get(label)
            if not isinstance(entry, dict):
                detail.append("%s: observed.json has no %r digest -- clause (c) "
                              "needs all three" % (prefix, label))
                continue
            if entry.get("pre") != entry.get("post"):
                detail.append("%s: %s moved while the agent held Write (%s -> %s)"
                              % (prefix, label, str(entry.get("pre"))[:16],
                                 str(entry.get("post"))[:16]))
    rule(8, "write_set", "batch", not detail, kc.EXIT_POLICY, detail)

    # 9 -- containment, manifest half (66, per batch). The ARTIFACT half is
    #      kz_checkers.py's and reports 67; it is never listed in decide.json.
    detail = []
    for batch in batches:
        for ruling in batch["rulings"]:
            if ruling.get("verdict") == ABSTAIN:
                continue
            tag = _label(batch, ruling.get("unit_id"))
            value = ruling.get("value")
            if not isinstance(value, dict) or not value:
                detail.append("%s: %s with an empty value -- the ruling produces "
                              "bytes and names none" % (tag, ruling.get("verdict")))
                continue
            for key in required_value_keys:
                if key not in value:
                    detail.append("%s: value has no %r" % (tag, key))
            spec = EVIDENCE_RULES.get(sid, {}).get(ruling.get("verdict"))
            if spec and spec.get("min", 0) > 0 and not (ruling.get("evidence") or []):
                detail.append("%s: %s with no evidence[]"
                              % (tag, ruling.get("verdict")))
    rule(9, "containment", "batch", not detail, kc.EXIT_AI_MANIFEST, detail)

    # 10 -- identity_invariants (11, union). "None dropped" is rule 1's coverage
    #       and is deliberately not re-derived here; what this rule adds is that
    #       no identity the manifest STATES may differ from card-text-en.json, and
    #       that no id is invented.
    detail = []
    known_ids = set()
    for record in identity_table.values():
        if isinstance(record, dict) and record.get("arkham_id"):
            known_ids.add(record["arkham_id"])
    for unit_id in universe:
        entry = effective.get(unit_id)
        if entry is None:
            continue
        ruling = entry["ruling"]
        echo = ruling.get("identity")
        if not isinstance(echo, dict):
            continue
        record = identity_table.get(unit_id)
        if not isinstance(record, dict):
            detail.append("%s: states an identity for a unit inputs.json declares "
                          "none for" % unit_id)
            continue
        for key in IDENTITY_KEYS:
            if key in echo and key in record and echo[key] != record[key]:
                detail.append("%s: %s %r != card-text-en.json %r"
                              % (unit_id, key, echo[key], record[key]))
        arkham_id = echo.get("arkham_id")
        if arkham_id and known_ids and arkham_id not in known_ids:
            detail.append("%s: arkham_id %r appears in no input record -- invented"
                          % (unit_id, arkham_id))
    rule(10, "identity_invariants", "union", not detail, kc.EXIT_POLICY, detail)

    return rules, effective


# ---------------------------------------------------------------------------
# 5. Merge, aggregate, report
# ---------------------------------------------------------------------------

def merge_manifests(inputs, batches, effective):
    """`manifest.merged.json` -- the union, in universe order.

    A `unit_id` in two batches never reaches here as a survivor: the first is
    kept, the second is recorded by rule 1 and the stage stops at 66.
    """
    universe = list(inputs.get("universe") or [])
    ordered = [u for u in universe if u in effective]
    ordered += [u for u in sorted(effective) if u not in set(universe)]
    return {
        "schema": MANIFEST_SCHEMA_VERSION,
        "stage": inputs.get("stage"),
        "stage_name": STAGE_NAMES.get(inputs.get("stage")),
        "slug": inputs.get("slug"),
        "scenario_sha256": inputs.get("scenario_sha256"),
        "batch_of": inputs.get("batch_of"),
        "universe": len(universe),
        "batches": [{"batch": b["index"],
                     "inputs_sha256": b["inputs_sha256"],
                     "manifest_source": b["manifest_source"],
                     "summary": (b["manifest"] or {}).get("summary"),
                     "wrote": sorted((b["manifest"] or {}).get("wrote") or []),
                     "declared_input_sha256":
                         (b["manifest"] or {}).get("declared_input_sha256") or {}}
                    for b in batches],
        "ruling_batch": dict((u, effective[u]["batch"]) for u in ordered),
        "rulings": [effective[u]["ruling"] for u in ordered],
    }


def histogram(rulings, key):
    out = {}
    for ruling in rulings:
        name = str(ruling.get(key))
        out[name] = out.get(name, 0) + 1
    return dict(sorted(out.items()))


def aggregate(rules):
    """`ai-overlap-decide.py:799-805`, exactly: any 66-rule failing => 66; else any
    failure => 11; else 0. There is no 67 tier here."""
    failed = [r for r in rules if r["status"] == "fail"]
    if any(r["exit_on_fail"] == kc.EXIT_AI_MANIFEST for r in failed):
        return "invalid", kc.EXIT_AI_MANIFEST, failed
    if failed:
        return "stop", kc.EXIT_POLICY, failed
    return "proceed", kc.EXIT_OK, failed


def decide(run_dir, out_path=None, merged_path=None, write=True):
    """Read a recorded run, evaluate the ten rules, write decide.json. No network."""
    inputs_path = os.path.join(run_dir, "inputs.json")
    if not os.path.isdir(run_dir):
        kc.refuse(kc.EXIT_PRECONDITION, "ask directory does not exist", run_dir)
    inputs, err = _read_json(inputs_path)
    if err:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the recorded run has no stage inputs.json", err)
    sid = inputs.get("stage")
    if sid not in STAGE_NAMES:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s declares stage %r" % (inputs_path, sid),
                  "the S-ids are %s (kz_config.AI_STAGE_MAP)" % list(STAGE_IDS))
    found = batch_dirs(run_dir)
    if not found:
        kc.refuse(kc.EXIT_PRECONDITION, "no batch-NN/ directory under %s" % run_dir,
                  "kz_ask.py writes one per batch; there is nothing to decide")

    batches = [load_batch(index, path, sid) for index, path in found]
    rules, effective = evaluate_rules(inputs, batches)
    outcome, exit_code, failed = aggregate(rules)
    merged = merge_manifests(inputs, batches, effective)

    out_path = out_path or os.path.join(run_dir, "decide.json")
    merged_path = merged_path or os.path.join(run_dir, "manifest.merged.json")
    merged_blob = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"

    failed_by_batch = {}
    for record in rules:
        if record["status"] != "fail" or record["scope"] != "batch":
            continue
        for line in record["detail"]:
            match = re.match(r"^batch-(\d+)", str(line))
            if match:
                failed_by_batch.setdefault(int(match.group(1)), set()).add(record["rule"])

    report = {
        "schema": MANIFEST_SCHEMA_VERSION,
        "stage": sid,
        "stage_name": STAGE_NAMES[sid],
        "slug": inputs.get("slug"),
        "unit": inputs.get("unit"),
        "outcome": outcome,
        "exit_code": exit_code,
        "reason": "; ".join("rule %d (%s)" % (r["rule"], r["name"]) for r in failed),
        "scenario_sha256": inputs.get("scenario_sha256"),
        "universe": len(inputs.get("universe") or []),
        "covered": len(effective),
        "batch_of": inputs.get("batch_of"),
        # Every batch's outcome, so an operator reading decide.json can see WHICH
        # invocation failed rather than only that the stage did (§3.7).
        "batches": [{
            "batch": b["index"],
            "dir": b["name"],
            "manifest_source": b["manifest_source"],
            "fallback_reason": b["fallback_reason"],
            "inputs_sha256": b["inputs_sha256"],
            "rulings": len(b["rulings"]),
            "session_id": (b["envelope"] or {}).get("session_id"),
            "total_cost_usd": (b["envelope"] or {}).get("total_cost_usd"),
            "num_turns": (b["envelope"] or {}).get("num_turns"),
            "permission_denials": (b["envelope"] or {}).get("permission_denials") or [],
            "outcome": "fail" if b["index"] in failed_by_batch else "pass",
            "failed_rules": sorted(failed_by_batch.get(b["index"], ())),
        } for b in batches],
        "rules": rules,
        "verdicts": histogram([e["ruling"] for e in effective.values()], "verdict"),
        "confidence": histogram([e["ruling"] for e in effective.values()], "confidence"),
        "merged_manifest_sha256": kc.sha256_bytes(merged_blob.encode("utf-8")),
        "total_cost_usd": sum((b["envelope"] or {}).get("total_cost_usd") or 0
                              for b in batches),
        "envelope_sha256": [
            kc.sha256_file(os.path.join(b["dir"], "claude-envelope.json"))
            if os.path.exists(os.path.join(b["dir"], "claude-envelope.json")) else None
            for b in batches],
        "session_ids": [(b["envelope"] or {}).get("session_id") for b in batches],
    }
    if write:
        kc.atomic_write_text(merged_path, merged_blob)
        kc.atomic_write_json(out_path, report)
        report["decide_sha256"] = kc.sha256_file(out_path)
    return report, exit_code


# ---------------------------------------------------------------------------
# 6. --replay (§4.4)
# ---------------------------------------------------------------------------

REPLAY_BATCH_FILES = ("inputs.json", "claude-envelope.json", "observed.json",
                      "prompt.md", "policy.md")
REPLAY_BATCH_TREES = ("out", "material")


def replay(src, dst):
    """Copy a recorded run into `dst` and return it. No claude, no credential,
    no cost -- the shape of `resolve-rebase-with-ai.sh:277-289`."""
    if not os.path.isdir(src):
        kc.refuse(kc.EXIT_USAGE, "--replay directory does not exist", src)
    if os.path.realpath(src) == os.path.realpath(dst):
        kc.refuse(kc.EXIT_USAGE, "--replay source and --run-dir are the same path",
                  "replay copies a recorded run; it never decides in place")
    copied = []
    if not os.path.isdir(dst):
        os.makedirs(dst)
    for name in sorted(os.listdir(src)):
        full = os.path.join(src, name)
        if os.path.isfile(full) and (name == "inputs.json"
                                     or name.endswith(".schema.json")):
            shutil.copy2(full, os.path.join(dst, name))
            copied.append(name)
    for index, batch_dir in batch_dirs(src):
        target = os.path.join(dst, os.path.basename(batch_dir))
        if not os.path.isdir(target):
            os.makedirs(target)
        for name in sorted(os.listdir(batch_dir)):
            full = os.path.join(batch_dir, name)
            if os.path.isfile(full) and (name in REPLAY_BATCH_FILES
                                         or name.endswith(".schema.json")):
                shutil.copy2(full, os.path.join(target, name))
                copied.append(os.path.join(os.path.basename(batch_dir), name))
            elif os.path.isdir(full) and name in REPLAY_BATCH_TREES:
                dest_tree = os.path.join(target, name)
                if os.path.isdir(dest_tree):
                    shutil.rmtree(dest_tree)
                shutil.copytree(full, dest_tree)
                copied.append(os.path.join(os.path.basename(batch_dir), name) + "/")
    return copied


# ---------------------------------------------------------------------------
# 7. The reference run, and the eleven faults derived from it
# ---------------------------------------------------------------------------
#
# One canonical passing run, plus one named mutation per rule-failure class of
# §4.4. The tests' fixture tree is THIS function's output -- so a schema change
# that would invalidate the fixtures cannot pass this module's own --selftest.

FAULTS = ("abstain", "low", "coverage-gap", "novel-not-high", "medium-on-pixels",
          "provenance-mismatch", "empty-value", "binding-drift",
          "write-outside-set", "identity-drift", "permission-denied")

# fixture class -> (rule number, expected exit). The literal map §4.4 requires,
# stated here as well as in test_koreanize_decide.py so a bare --selftest run
# without pytest exercises it.
FAULT_RULE = {
    "coverage-gap": (1, kc.EXIT_AI_MANIFEST),
    "abstain": (2, kc.EXIT_POLICY),
    "low": (3, kc.EXIT_POLICY),
    "medium-on-pixels": (4, kc.EXIT_POLICY),
    "provenance-mismatch": (4, kc.EXIT_POLICY),
    "novel-not-high": (5, kc.EXIT_POLICY),
    "permission-denied": (6, kc.EXIT_AI_MANIFEST),
    "binding-drift": (7, kc.EXIT_AI_MANIFEST),
    "write-outside-set": (8, kc.EXIT_POLICY),
    "empty-value": (9, kc.EXIT_AI_MANIFEST),
    "identity-drift": (10, kc.EXIT_POLICY),
}

_REF_SLUG = "the-midwinter-gala"
_REF_SCENARIO_SHA = "b" * 64
_REF_GATE = "material/gate-typeset.json"
_REF_LOCK = "material/lock.json"

_REF_FINDINGS = [
    {
        "unit_id": "typeset:T3:71006-front",
        "detail": "body band 2 overflows the CLEAR window by 12 px on the "
                  "Act front face",
        "identity": {"arkham_id": "71006", "guid": "a26b6b", "card_id": 917506,
                     "deck_key": "9175", "cell": 6,
                     "num_width": 8, "num_height": 5},
        "verdict": "defect", "reaches_pixels": True,
        "owner_stage": "typeset",
        "suggested_action": "re-run typeset with the shrink ladder one step deeper "
                            "on this face",
        "rationale": "The overflow is 12 px of real ink past the CLEAR boundary on "
                     "a face whose window is not narrowed, so it is a typeset "
                     "defect and not a measurement artefact.",
        "evidence": [{"file": _REF_GATE, "locator": "$.checks[0].detail[0]"}],
    },
    {
        "unit_id": "typeset:T3:71005-front",
        "detail": "body band 1 overflows the CLEAR window by 3 px on the Act "
                  "front face",
        "identity": {"arkham_id": "71005", "guid": "0782c0", "card_id": 917505,
                     "deck_key": "9175", "cell": 5,
                     "num_width": 8, "num_height": 5},
        "verdict": "tolerance", "reaches_pixels": True,
        "owner_stage": "mask",
        "suggested_action": "add (T3, Act/front, body, 71005-front) to "
                            "mask.residual_baseline[] via --accept-mask-residual",
        "rationale": "3 px is inside the recorded residual baseline for this group "
                     "and window, and the lock already carries the sibling face, so "
                     "this reproduces a ruled tolerance rather than a new defect.",
        "evidence": [{"file": _REF_LOCK, "locator": "$.mask.residual_baseline[1]"}],
    },
    {
        "unit_id": "typeset:T1:71012-back",
        "detail": "flavor line box tail sits 4 px outside CLEAR on the back face",
        "identity": {"arkham_id": "71012", "guid": "1e7f1b", "card_id": 917512,
                     "deck_key": "9175", "cell": 12,
                     "num_width": 8, "num_height": 5},
        "verdict": "defect", "reaches_pixels": True,
        "owner_stage": "mask",
        "suggested_action": "widen the flavor window for Location/back by the "
                            "measured margin and re-run mask",
        "rationale": "The tail is outside CLEAR on a window the layout set measured "
                     "from a different face, so the window is wrong rather than the "
                     "typesetting, and the owner is mask.",
        "evidence": [{"file": _REF_GATE, "locator": "$.checks[1].detail[0]"}],
    },
]

_REF_MAX_UNITS = 2   # small on purpose: two batches, so the merge is exercised

_REF_CHECK_NAMES = {"T3": "clear_containment", "T1": "line_box_containment"}


def _ref_gate_detail(finding):
    """One `checks[].detail[]` entry, in the STRUCTURED form a gate emits when it
    has identity to hand over.

    The reference run is a faithful recording: running `kz_triage.py` against this
    gate report reproduces exactly this universe, these unit ids and these unit
    payloads. A fixture whose universe could not have come from its own material
    would be testing the decider against something no gate produces.
    """
    detail = {"key": finding["unit_id"].split(":")[2]}
    detail.update(finding["identity"])
    detail["detail"] = finding["detail"]
    return detail


def _ref_unit(finding):
    """The unit payload `kz_triage.universe_from_gate` derives from the entry
    above: the detail verbatim, then the three fields the walk adds."""
    unit = dict(_ref_gate_detail(finding))
    unit["check"] = finding["unit_id"].split(":")[1]
    unit["check_name"] = _REF_CHECK_NAMES[unit["check"]]
    unit["gate_stage"] = finding["unit_id"].split(":")[0]
    return unit


def _ref_material():
    """The copied material every batch of the reference run holds.

    IDENTICAL in every batch, and that is deliberate: `material_sha256` is a
    stage-level fact, so material that differed per batch would give one file two
    digests and rule 7 could never hold. The agent sees the WHOLE failing gate
    report; its universe is the slice, not its evidence.
    """
    return {
        "gate-typeset.json": {
            "schema_version": kc.SCHEMA_VERSION, "stage": "typeset",
            "slug": _REF_SLUG, "verdict": "FAIL", "exit_code": 22,
            "checks": [
                {"id": check_id, "name": _REF_CHECK_NAMES[check_id],
                 "status": "fail", "exit_on_fail": 20,
                 "detail": [_ref_gate_detail(f) for f in _REF_FINDINGS
                            if f["unit_id"].split(":")[1] == check_id]}
                for check_id in ("T3", "T1")
            ]},
        "lock.json": {
            "schema_version": kc.SCHEMA_VERSION, "slug": _REF_SLUG,
            "mask": {"residual_baseline": [
                {"check": "T3", "group": "Act/front", "window_name": "body",
                 "face": "71006-front"},
                {"check": "T3", "group": "Act/front", "window_name": "body",
                 "face": "71005-front"}]}},
    }


def _material_blob(doc):
    """The exact bytes kc.atomic_write_json puts on disk, so the digest recorded
    in inputs.json is the digest of the file the agent actually reads."""
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _ref_material_sha():
    return dict(("material/%s" % name, kc.sha256_bytes(_material_blob(doc)))
                for name, doc in sorted(_ref_material().items()))


def _ref_stage_inputs():
    universe = [f["unit_id"] for f in _REF_FINDINGS]
    return {
        "schema": 1,
        "stage": "S6",
        "stage_name": "triage",
        "slug": _REF_SLUG,
        "scenario_sha256": _REF_SCENARIO_SHA,
        "unit": "finding",
        "gate_stage": "typeset",
        "universe": universe,
        # null: S6 is gate-invoked, so its universe is not knowable until
        # invocation and the ceiling is enforced by kz_ask.py at exit 13 (§3.7).
        "universe_size": None,
        "max_units_per_call": _REF_MAX_UNITS,
        "max_calls": 3,
        "batch_of": 2,
        "budget": {"model": "claude-opus-5", "fallback_model": "claude-opus-4-8",
                   "effort": "high", "timeout_s": 780, "call_budget_s": 900,
                   "stage_wall_clock_s": 5400, "max_budget_usd_per_call": 10,
                   "max_budget_usd_per_stage": 60},
        "units": dict((f["unit_id"], _ref_unit(f)) for f in _REF_FINDINGS),
        "identity": dict((f["unit_id"], f["identity"]) for f in _REF_FINDINGS),
        "material_sha256": _ref_material_sha(),
    }


def batch_inputs(stage_inputs, index, unit_ids):
    """The per-batch bundle `batch-NN/inputs.json`, derived from the stage-level one.

    It lives in the SCHEMA owner rather than in kz_ask.py because rule 7 binds a
    manifest to `sha256(batch-NN/inputs.json)`: the bundle is part of the run
    binding, and two implementations of one format is exactly the drift the
    "one owner" property exists to prevent. kz_ask.py calls this; nothing else
    builds a batch bundle.

    It deliberately carries NO timestamp. The resume path binds a completed batch
    by that digest, so a batch bundle has to be a pure function of the plan or
    every resume would look like input drift (§3.7).
    """
    return {
        "schema": 1,
        "stage": stage_inputs["stage"],
        "stage_name": stage_inputs["stage_name"],
        "slug": stage_inputs["slug"],
        "scenario_sha256": stage_inputs["scenario_sha256"],
        "unit": stage_inputs["unit"],
        "batch": index,
        "batch_of": stage_inputs["batch_of"],
        "universe": list(unit_ids),
        "units": dict((u, stage_inputs["units"][u]) for u in unit_ids),
        "identity": dict((u, stage_inputs["identity"][u]) for u in unit_ids
                         if u in stage_inputs["identity"]),
        "material_sha256": dict(stage_inputs["material_sha256"]),
    }


def _ref_ruling(finding, material_sha):
    return {
        "unit_id": finding["unit_id"],
        "verdict": finding["verdict"],
        "confidence": "high",
        "rationale": finding["rationale"],
        "value": {"owner_stage": finding["owner_stage"],
                  "suggested_action": finding["suggested_action"]},
        "reaches_pixels": finding["reaches_pixels"],
        "identity": dict(finding["identity"]),
        "evidence": [{"file": e["file"], "sha256": material_sha[e["file"]],
                      "locator": e["locator"]} for e in finding["evidence"]],
    }


def build_reference_run(dest, fault=None):
    """Write one recorded run under `dest`, optionally carrying a named fault.

    This is the fixture generator AND the negative selftest, deliberately the
    same code: a schema change that would invalidate
    `tests/fixtures/koreanize/ai/S6/<class>/` cannot pass --selftest.
    """
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the classes are %s" % list(FAULTS))
    stage_inputs = _ref_stage_inputs()
    material_sha = dict(stage_inputs["material_sha256"])
    universe = list(stage_inputs["universe"])
    groups = [universe[i:i + _REF_MAX_UNITS]
              for i in range(0, len(universe), _REF_MAX_UNITS)]

    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    kc.atomic_write_json(os.path.join(dest, "inputs.json"), stage_inputs)
    kc.atomic_write_text(os.path.join(dest, "S6.schema.json"),
                         json.dumps(stage_schema("S6"), ensure_ascii=False,
                                    indent=2) + "\n")

    for index, unit_ids in enumerate(groups, start=1):
        batch_dir = os.path.join(dest, "batch-%02d" % index)
        os.makedirs(os.path.join(batch_dir, "out"))
        os.makedirs(os.path.join(batch_dir, "material"))

        batch_doc = batch_inputs(stage_inputs, index, unit_ids)
        inputs_path = os.path.join(batch_dir, "inputs.json")
        kc.atomic_write_json(inputs_path, batch_doc)

        for name, doc in sorted(_ref_material().items()):
            kc.atomic_write_json(os.path.join(batch_dir, "material", name), doc)
        kc.atomic_write_text(os.path.join(batch_dir, "policy.md"),
                             _read_package_text("policy.md"))
        kc.atomic_write_text(os.path.join(batch_dir, "prompt.md"),
                             _read_package_text(os.path.join("prompts",
                                                             "S6-triage.md")))
        kc.atomic_write_text(os.path.join(batch_dir, "S6.schema.json"),
                             json.dumps(stage_schema("S6"), ensure_ascii=False,
                                        indent=2) + "\n")

        rulings = [_ref_ruling(f, material_sha) for f in _REF_FINDINGS
                   if f["unit_id"] in unit_ids]

        # -- the faults, each landing on exactly one rule -----------------------
        if fault == "abstain" and index == 1:
            rulings[0]["verdict"] = ABSTAIN
            rulings[0]["value"] = {}
            rulings[0]["evidence"] = []
        if fault == "low" and index == 1:
            rulings[0]["confidence"] = "low"
        if fault == "coverage-gap" and index == 1:
            # ONE unit loses its ruling, in the batch that still returns another.
            # Emptying batch-02's `rulings[]` instead also trips rule 6 (the
            # schema's `minItems: 1`), and then a broken rule 1 would still fail
            # this fixture at the same exit code -- the very conflation §4.4
            # splits rule 4 and rule 5 apart to avoid.
            rulings = rulings[:1]
        if fault == "novel-not-high" and index == 1:
            # A `tolerance` -- S6's novel verdict -- at medium, with
            # reaches_pixels false so it trips rule 5 and NOT rule 4. Without that
            # separation a single medium fixture makes both rules red and neither
            # is actually tested (§4.4).
            for ruling in rulings:
                if ruling["verdict"] == "tolerance":
                    ruling["confidence"] = "medium"
                    ruling["reaches_pixels"] = False
        if fault == "medium-on-pixels" and index == 1:
            rulings[0]["confidence"] = "medium"
            rulings[0]["reaches_pixels"] = True
        if fault == "provenance-mismatch" and index == 1:
            for ruling in rulings:
                if ruling["verdict"] == "tolerance":
                    ruling["evidence"] = [{"file": _REF_GATE,
                                           "sha256": material_sha[_REF_GATE],
                                           "locator": "$.checks[0].detail[1]"}]
        if fault == "empty-value" and index == 1:
            rulings[0]["value"] = {}
            rulings[0]["evidence"] = []
        if fault == "identity-drift" and index == 1:
            rulings[0]["identity"]["cell"] = rulings[0]["identity"]["cell"] + 1

        manifest = {
            "schema": MANIFEST_SCHEMA_VERSION,
            "stage": "S6",
            "slug": _REF_SLUG,
            "batch": index,
            "batch_of": len(groups),
            "scenario_sha256": _REF_SCENARIO_SHA,
            "inputs_sha256": kc.sha256_file(inputs_path),
            "summary": "Adjudicated %d typeset gate finding(s) for batch %d."
                       % (len(rulings), index),
            "rulings": rulings,
            "wrote": ["out/manifest.json"],
            "declared_input_sha256": dict(material_sha),
        }
        if fault == "binding-drift" and index == 1:
            manifest["inputs_sha256"] = "0" * 64

        kc.atomic_write_json(os.path.join(batch_dir, "out", "manifest.json"), manifest)

        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(manifest, ensure_ascii=False),
            "session_id": "0000000%d-0000-4000-8000-00000000000%d" % (index, index),
            "total_cost_usd": 0.42,
            "num_turns": 3,
            "duration_ms": 41000,
            "duration_api_ms": 39500,
            "permission_denials": [],
        }
        if fault == "permission-denied" and index == 1:
            envelope["permission_denials"] = [
                {"tool_name": "Read",
                 "tool_input": {"file_path":
                                "SCED-downloads/decomposed/language-pack"}}]
        kc.atomic_write_json(os.path.join(batch_dir, "claude-envelope.json"), envelope)

        observed = {
            "schema": 1,
            "ask_dir": "batch-%02d" % index,
            "stage": "S6",
            "batch": index,
            "outside_out": [],
            "digests": {
                "checkouts": {"path": "SCED,SCED-downloads,SCED-tools",
                              "pre": "c" * 64, "post": "c" * 64},
                "run_dir": {"path": "<run_dir> excluding ASK_DIR",
                            "pre": "d" * 64, "post": "d" * 64},
                "read_only": {"path": "images-ko/", "pre": "e" * 64, "post": "e" * 64},
            },
            "cli_version": "2.0.0",
            "elapsed_s": 41,
        }
        if fault == "write-outside-set" and index == 1:
            observed["outside_out"] = ["notes.md"]
        kc.atomic_write_json(os.path.join(batch_dir, "observed.json"), observed)
    return dest


def _read_package_text(relpath):
    path = os.path.join(kc.PACKAGE_DIR, relpath)
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is missing from the package" % relpath,
                  path)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 8. --selftest
# ---------------------------------------------------------------------------

def selftest(verbose=True):
    """Prove the good run decides `proceed` and each fault fires on its own rule."""
    import tempfile

    findings = []

    # Every S-id's schema round-trips through the validator that enforces it.
    for sid in STAGE_IDS:
        schema = stage_schema(sid)
        if schema["properties"]["stage"]["const"] != sid:
            findings.append("%s: stage_schema does not pin its own S-id" % sid)
        if json.loads(json.dumps(schema)) != schema:
            findings.append("%s: schema is not JSON round-trippable" % sid)

    if set(FAULT_RULE) != set(FAULTS):
        findings.append("FAULT_RULE is not total over FAULTS")
    if sorted(set(r for r, _e in FAULT_RULE.values())) != list(range(1, 11)):
        findings.append("the fixture -> rule map does not cover rules 1..10")

    root = tempfile.mkdtemp(prefix="kz-decide-selftest.")
    try:
        good = build_reference_run(os.path.join(root, "good"))
        report, code = decide(good)
        if code != kc.EXIT_OK:
            findings.append("the reference run did not proceed: exit %d (%s)"
                            % (code, report["reason"]))
        if report["covered"] != report["universe"]:
            findings.append("the reference run covered %d of %d units"
                            % (report["covered"], report["universe"]))
        if report["batch_of"] != 2:
            findings.append("the reference run is not multi-batch; the cross-batch "
                            "merge is then untested")

        # A unit_id in two batches is rule 1, never last-write-wins.
        dup = build_reference_run(os.path.join(root, "duplicate"))
        dup_path = os.path.join(dup, "batch-02", "out", "manifest.json")
        with open(dup_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["rulings"][0]["unit_id"] = _REF_FINDINGS[0]["unit_id"]
        kc.atomic_write_json(dup_path, doc)
        os.remove(os.path.join(dup, "batch-02", "claude-envelope.json"))
        report, code = decide(dup)
        rule1 = [r for r in report["rules"] if r["rule"] == 1][0]
        if code != kc.EXIT_AI_MANIFEST or rule1["status"] != "fail":
            findings.append("a duplicated unit_id did not fail rule 1 at 66 "
                            "(got exit %d)" % code)

        for fault in FAULTS:
            want_rule, want_exit = FAULT_RULE[fault]
            path = build_reference_run(os.path.join(root, fault), fault=fault)
            report, code = decide(path)
            if code != want_exit:
                findings.append("%s: exit %d, expected %d (%s)"
                                % (fault, code, want_exit, report["reason"]))
            record = [r for r in report["rules"] if r["rule"] == want_rule][0]
            if record["status"] != "fail":
                findings.append("%s: rule %d (%s) did not fail"
                                % (fault, want_rule, record["name"]))

        # The multi-turn fallback: an envelope whose `result` is prose, with a
        # valid out/manifest.json beside it, must decide IDENTICALLY. Guarded
        # here as well as in pytest because no CI runs the pytest suite and
        # `koreanize.sh selftest` is the compensating control (conftest.py).
        prose = build_reference_run(os.path.join(root, "prose"))
        env_path = os.path.join(prose, "batch-01", "claude-envelope.json")
        with open(env_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["result"] = ("I ran out of turns after a tool error; the manifest is "
                         "in out/manifest.json.")
        kc.atomic_write_json(env_path, doc)
        report, code = decide(prose)
        if code != kc.EXIT_OK:
            findings.append("the out/manifest.json fallback did not decide proceed: "
                            "exit %d (%s)" % (code, report["reason"]))
        if report["batches"][0]["manifest_source"] != "out-manifest":
            findings.append("the fallback did not take the out/manifest.json path")

        # --replay is the offline path, and it must decide identically.
        target = os.path.join(root, "replayed")
        replay(good, target)
        report, code = decide(target)
        if code != kc.EXIT_OK:
            findings.append("--replay of the reference run did not proceed: exit %d"
                            % code)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 9. CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_decide.py",
        description="koreanize AI manifest schema + the ten adjudication rules "
                    "(design §3.7, §4.4).")
    parser.add_argument("--emit-schema", action="store_true",
                        help="print (or write with --out) the manifest schema for "
                             "--stage and exit 0")
    parser.add_argument("--stage", help="S-id (%s)" % ", ".join(STAGE_IDS))
    parser.add_argument("--run-dir", help="ai/<stage>/<stamp>/ -- the recorded run")
    parser.add_argument("--replay", metavar="DIR",
                        help="copy a recorded run into --run-dir and decide it "
                             "offline: no claude, no credential, no cost")
    parser.add_argument("--out", help="decide.json destination (or the schema path "
                                      "with --emit-schema)")
    parser.add_argument("--manifest-out", help="manifest.merged.json destination")
    parser.add_argument("--json-only", action="store_true",
                        help="emit the report JSON on stdout and nothing else")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        print("kz_decide --selftest")
        findings = selftest()
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        print("  ok: seven stage schemas, the reference run proceeds, %d fault "
              "classes each fire their own rule, --replay agrees" % len(FAULTS))
        return kc.EXIT_OK

    if args.emit_schema:
        if not args.stage:
            kc.refuse(kc.EXIT_USAGE, "--emit-schema needs --stage",
                      "the S-ids are %s" % list(STAGE_IDS))
        blob = json.dumps(stage_schema(args.stage), ensure_ascii=False,
                          indent=2) + "\n"
        if args.out:
            kc.atomic_write_text(args.out, blob)
            if not args.quiet:
                print("wrote %s schema -> %s" % (args.stage, args.out))
        else:
            sys.stdout.write(blob)
        return kc.EXIT_OK

    # P0a / P0b. Deliberately after --selftest and --emit-schema, which read and
    # write nothing outside the package, and before anything that opens a run dir.
    kc.check_invocation_guards([args.run_dir] if args.run_dir else [])

    if not args.run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    if args.replay:
        copied = replay(args.replay, args.run_dir)
        if not args.quiet and not args.json_only:
            print("replay: copied %d artefact(s) from %s" % (len(copied), args.replay))

    report, code = decide(args.run_dir, out_path=args.out,
                          merged_path=args.manifest_out)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code

    if not args.quiet:
        print("decide: %s (exit %d) -- %d/%d units covered over %d batch(es)"
              % (report["outcome"], code, report["covered"], report["universe"],
                 len(report["batches"])))
        for record in report["rules"]:
            mark = {"pass": "ok  ", "fail": "FAIL"}[record["status"]]
            print("  %s rule %-2d %-24s (%s)"
                  % (mark, record["rule"], record["name"], record["scope"]))
            if record["status"] == "fail":
                for line in record["detail"][:10]:
                    print("         - %s" % line)
        for batch in report["batches"]:
            print("  batch-%02d %-14s %2d ruling(s)  %s"
                  % (batch["batch"], batch["outcome"], batch["rulings"],
                     batch["manifest_source"]))
        print("  verdicts: %s" % report["verdicts"])
        print("  merged  : %s" % report["merged_manifest_sha256"][:16])
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
