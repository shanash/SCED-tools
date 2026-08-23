#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `translate` (S2) -- card text into Korean (design §6 step 10).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER`.

THE OUTPUT IS A GENERATOR, NOT FREE TEXT, AND THAT IS THE WHOLE DESIGN
----------------------------------------------------------------------
This stage emits `data/text/<slug>-ko.py` -- a Python file carrying a `KO[...]`
table and the code that renders it into `card-text-ko.json`. It does NOT emit the
JSON directly, and the reason is recorded rather than aesthetic:

  * the output is REPRODUCIBLE: re-running the generator produces byte-identical
    JSON, so `card-text-ko.json`'s serialization (indent, `ensure_ascii`, the
    single trailing newline) never passes through human hands;
  * the output is REVIEWABLE AS A DIFF: a table of Korean strings is something a
    reviewer can read, and a change to one card is one hunk;
  * and above all, A DEFECT IS FIXED IN THE THING THAT PRODUCES IT. The Midwinter
    record's `주요목적를` appeared FIVE TIMES and the fix belonged in the
    generator; `check-particles.py:30-36` states the consequence in full --
    "`--fix` NEVER writes card-text-ko.json. That artifact is produced only by
    re-running the generator ... A consequence, and it is designed, not a bug:
    --fix ALONE DOES NOT TURN THIS CHECKER GREEN."

`kz_checkers.py` then re-validates the rendered JSON with an independent
tokenizer, and `audit`(S7) widens any prose defect found at the gate into a
predicate that catches its siblings IN THE GENERATOR.

WHY THIS STAGE HAS NO HUMAN GATE OF ITS OWN
--------------------------------------------
`scenario.json`'s `gates` block carries exactly four -- `init`, `terms`, `mask`,
`typeset` -- and `translate` is not one of them. Its correctness gate is `check`,
which is mechanical, plus the `typeset` gate, which §5.7 makes the PROSE review
per face. A term decided wrong is wrong once per occurrence and is caught at the
`terms` gate one stage earlier; a card translated wrong is wrong once.

S2 HAS NO NOVEL VERDICT (`kz_decide.NOVEL_VERDICTS["S2"] == ()`), because
`translate` restates a card that already exists. `passthrough` is the verdict for
a field that must stay as it is -- a proper noun, an empty field, a value the
terminology pins.

Exit codes (§4.2):
   0  every card ruled, every rule passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal
  11  stop by policy -- the ten rules stopped this run
  13  precondition -- no init/terms artifact, or the terms gate is missing
  25  the stage stopped between batches on its budget
  30  the terms gate is present but not accepted
  65  claude unavailable / unauthenticated / timed out
  66  AI manifest invalid
  67  the emitted generator failed structural verification
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402
import kz_ask as ka  # noqa: E402
import kz_decide as kd  # noqa: E402
import kz_checkers as kx  # noqa: E402

# NOT `import kz_terms`. The terminology reaches this stage as DATA -- the
# `terms.json` report's results block and the copied material -- never as a
# module. Importing it would register `terms`'s own AI contract as a side
# effect of importing `translate` (kz_terms calls declare_ai in its module
# body), which asserts a second stage into the partition on an import that
# has nothing to do with it.

STAGE = "translate"
SID = "S2"

kc.declare_ai(STAGE, required=True)

FAULTS = ("universe", "generator", "markup", "terms-gate")

#: The fields the S2 schema's `value.fields` may carry
#: (`kz_decide._FIELDS_SCHEMA`), in the order the generator emits them.
GENERATED_FIELDS = ("name", "subname", "traits", "text", "flavor", "b_side")


# ---------------------------------------------------------------------------
# 1. The universe -- one unit per card
# ---------------------------------------------------------------------------

def build_universe(en_doc):
    """(universe[], units{}, identity{}) -- one unit per distinct card id.

    THE ART UNIT IS THE CARD, NOT THE OBJECT (§3.3): `cards[]` carries one entry
    per distinct `arkham_id` while `objects[]` carries one per TTS object, and
    two objects can share an id (`71033` at `4a2568` and `ccce29`). Translating
    per object would ask the agent to render one card twice and would then have
    two renderings to reconcile; `objtext` resolves the object-level fan-out
    afterwards, from this one ruling.
    """
    cards = (en_doc.get("cards") if isinstance(en_doc, dict) else en_doc) or []
    units, identity = {}, {}
    for card in cards:
        ident = str(card.get("arkham_id") or card.get("code") or "").strip()
        if not ident:
            continue
        payload = {"arkham_id": ident}
        for field in kx.TEXT_FIELDS:
            value = card.get(field)
            if value:
                payload[field] = value
        units[ident] = payload
        record = dict((k, card[k]) for k in kd.IDENTITY_KEYS if k in card)
        if record:
            identity[ident] = record
    if not units:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "card-text-en.json carries no card ids",
                  "translate's universe is cards[]; run `init` first")
    return sorted(units), units, identity


# ---------------------------------------------------------------------------
# 2. The generator -- data/text/<slug>-ko.py
# ---------------------------------------------------------------------------

GENERATOR_HEADER = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{slug}-ko.py -- the Korean card-text GENERATOR for `{slug}`.

GENERATED BY `koreanize translate` (S2) on {when}. Do not hand-edit the JSON this
produces: edit the KO table below and re-run this file. That asymmetry is the
point -- `card-text-ko.json` is an OUTPUT, and a defect repaired only in the
output is a defect the next run reproduces.

    python3 {slug}-ko.py --source card-source-en.json --out card-text-ko.json

The identity fields (guid, card_id, deck_key, cell, sheet, pack) are copied from
the English source and never restated here, so a translation cannot move one.
`kz_checkers.py` re-validates the result with an independent tokenizer.
"""

import argparse
import json
import sys

SLUG = "{slug}"
SCHEMA_VERSION = "{schema}"

#: Identity fields copied verbatim from the English source. Never translated,
#: never restated in KO -- CH4 in `kz_checkers.py` refuses a run that moves one.
IDENTITY_FIELDS = {identity!r}

KO = {{}}

'''

GENERATOR_FOOTER = '''

def render(source):
    """Merge KO into the English source's identity, in id order.

    The merge is one-directional: KO supplies Korean text, the source supplies
    identity, and a card KO does not carry is emitted with its English text so
    the output is total over the source rather than silently short.
    """
    out = []
    for card in source.get("cards") or []:
        ident = str(card.get("arkham_id") or card.get("code") or "")
        merged = {"code": ident, "arkham_id": ident}
        for field in IDENTITY_FIELDS:
            if field in card:
                merged[field] = card[field]
        for field, value in sorted((KO.get(ident) or {}).items()):
            merged[field] = value
        for field in ("name", "subname", "traits", "text", "flavor",
                      "back_name", "back_text", "back_flavor"):
            if field not in merged and card.get(field):
                merged[field] = card[field]
        out.append(merged)
    out.sort(key=lambda c: c["arkham_id"])
    return {"schema_version": SCHEMA_VERSION, "slug": SLUG,
            "generated_by": "%s-ko.py" % SLUG,
            "counts": {"cards": len(out), "translated": len(KO)},
            "cards": out}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="%s-ko.py" % SLUG)
    parser.add_argument("--source", required=True,
                        help="card-source-en.json or card-text-en.json")
    parser.add_argument("--out", required=True, help="card-text-ko.json")
    args = parser.parse_args(argv)
    with open(args.source, "r", encoding="utf-8") as handle:
        source = json.load(handle)
    doc = render(source)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\\n")
    print("%s: %d cards, %d translated" % (args.out, doc["counts"]["cards"],
                                           doc["counts"]["translated"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _py_string(value):
    """A Korean string as a Python literal, one source line per text line.

    Line-per-line rather than one long escaped literal, because the review
    surface for this table IS the diff: a one-line change to a five-line card
    should be a one-line hunk.
    """
    lines = (value or "").split("\n")
    if len(lines) == 1:
        return json.dumps(lines[0], ensure_ascii=False)
    parts = [json.dumps(line + "\n", ensure_ascii=False) for line in lines[:-1]]
    parts.append(json.dumps(lines[-1], ensure_ascii=False))
    return "(\n" + "".join("        %s\n" % p for p in parts) + "    )"


#: A card id may contain only these characters. A POSITIVE ALLOWLIST, and it is
#: load-bearing rather than defensive: `unit_id` reaches this module from the
#: MERGED MANIFEST, which is model-authored, and it is interpolated into a Python
#: file a human is then instructed to RUN. Escaping alone would be enough to keep
#: the literal well-formed, but an allowlist is what makes "this is a card id"
#: checkable instead of assumed. Rule 1 rejects an id outside the universe, but
#: `decide`'s verdict is a separate conjunct from this one and neither may rely
#: on the other.
_ID_OK = frozenset("abcdefghijklmnopqrstuvwxyz"
                   "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


def _assert_emittable_id(unit_id):
    if not unit_id or not set(str(unit_id)) <= _ID_OK:
        kc.refuse(kc.EXIT_ARTIFACT,
                  "a ruling's unit_id is not a usable card id: %r" % (unit_id,),
                  "the generator is executable Python and its keys come from a "
                  "model-authored manifest; ids are [A-Za-z0-9_.-] only")
    return str(unit_id)


def build_generator(slug, units, effective):
    """The emitted `data/text/<slug>-ko.py`. Deterministic: id order, field order.

    Only `translate` rulings contribute a KO row. A `passthrough` is a decision
    that the English stands, so writing it into the table would restate the
    source as though it were a translation and make the two indistinguishable in
    a diff -- and `render()` already falls back to the source for any id the
    table omits, so the behaviour is identical and the record is honest.
    """
    body = []
    translated = 0
    for unit_id in sorted(effective):
        ruling = effective[unit_id]["ruling"]
        if ruling.get("verdict") != "translate":
            continue
        fields = (ruling.get("value") or {}).get("fields") or {}
        rows = [(f, fields[f]) for f in GENERATED_FIELDS
                if (fields.get(f) or "").strip()]
        if not rows:
            continue
        translated += 1
        # json.dumps, never %s: the key is model-authored (see _ID_OK above),
        # and the allowlist and the escaping are two independent guards.
        body.append("KO[%s] = dict(" % json.dumps(_assert_emittable_id(unit_id)))
        for field, value in rows:
            body.append("    %s=%s," % (field, _py_string(value)))
        body.append(")\n")
    header = GENERATOR_HEADER.format(
        slug=slug, when=kc.utc_now(), schema=kc.SCHEMA_VERSION,
        identity=tuple(kx.IDENTITY_FIELDS))
    return header + "\n".join(body) + GENERATOR_FOOTER, translated


def verify_generator(text):
    """The emitted generator must COMPILE, and it must compile on the stdlib
    interpreter too.

    `data/text/` is a durable-tier artifact that outlives this run, and a
    generator that only parses under the art interpreter is one a restore cannot
    execute. Compiling it here is cheap and is the difference between shipping a
    file and shipping a file that works.
    """
    findings = []
    try:
        compile(text, "<generated>", "exec")
    except SyntaxError as exc:
        findings.append("the emitted generator does not compile: %s" % exc)
    return findings


# ---------------------------------------------------------------------------
# 3. The stage
# ---------------------------------------------------------------------------

def require_terms(run_dir):
    """The terminology, and the gate that authorizes it. Returns the terms dict.

    TWO DIFFERENT REFUSALS, because they send an operator to different places:
    no `terms.json` at all means the stage has never run (exit 13, "run
    `terms`"); a `terms.json` whose gate is not `accepted` means it ran and
    nobody has walked the vocabulary (exit 30, "read gates/terms-gate.md"). The
    dispatcher's predecessor rule would catch both as one, which is why this is
    asserted here as well: a direct module invocation must not be a way around
    the gate.
    """
    report, err = kd._read_json(os.path.join(run_dir, "terms.json"))
    if err or not report:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "translate has no terminology to work from",
                  "run `koreanize.sh terms` first; %s"
                  % (err or "terms.json is absent"))
    gate = report.get("gate") or {}
    if gate.get("status") != "accepted":
        kc.refuse(kc.EXIT_GATE,
                  "the terms gate is %r, not accepted" % gate.get("status"),
                  "a term is applied once per occurrence downstream; read "
                  "%s/gates/terms-gate.md and set status: accepted" % run_dir)
    return ((report.get("results") or {}).get("terms")) or {}


def _material_for(cfg, run_dir, workspace=None):
    """Everything the agent is given, COPIED. It holds no repository path."""
    workspace = workspace or kc.WORKSPACE_ROOT
    material = {}
    for name in ("card-text-en.json", "card-source-en.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            material[name] = path
    for label, rel in (("terms.json", "%s.json" % cfg["slug"]),
                       ("terminology-ko.md",
                        "%s-terminology-ko.md" % cfg["slug"])):
        path = os.path.join(workspace, cfg["guard"]["data_root"], "terms", rel)
        if os.path.exists(path):
            material[label] = path
    return material


def markup_checks(units, effective):
    """The artifact half of containment for S2, reported as 67 (§4.4).

    Two obligations, and they are separate:
      * the RENDERED Korean must carry the same markup multiset as the English
        -- WHICH icon tokens, not just how many, because a translation that
        turns [combat] into [agility] keeps the count and changes the card; and
      * the agent's DECLARED `markup_tokens`/`icon_tokens` must agree with what
        it actually wrote, so a declaration cannot be a claim about a different
        string than the one shipped.
    The second is what makes the first non-circular: without it the agent could
    declare whatever the checker wanted to see.
    """
    rendered, declared = [], []
    for unit_id in sorted(effective):
        ruling = effective[unit_id]["ruling"]
        if ruling.get("verdict") != "translate":
            continue
        value = ruling.get("value") or {}
        fields = value.get("fields") or {}
        english = units.get(unit_id) or {}
        for field in sorted(fields):
            for finding in kx.markup_delta(english.get(field), fields[field]):
                rendered.append("%s.%s: %s" % (unit_id, field, finding))
        icons = []
        for field in sorted(fields):
            icons.extend(kx.scan(fields[field])[0])
        if value.get("icon_tokens") is not None:
            if sorted(value["icon_tokens"]) != sorted(icons):
                declared.append("%s: declared icon_tokens %s, wrote %s"
                                % (unit_id, sorted(value["icon_tokens"]),
                                   sorted(icons)))
    return rendered, declared


def run_translate(run_dir, mode="build", replay=None, ask_dir=None,
                  claude_bin=None, workspace=None, readonly_trees=(),
                  quiet=False, write_data=True):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. <run_dir>/card-text-en.json exists and carries card ids    -> exit 13
      4. <run_dir>/terms.json exists                                -> exit 13
      5. its gate reads `accepted`                                  -> exit 30
      6. the universe is inside ai.batch.S2's ceiling               -> exit 13
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    en_path = os.path.join(run_dir, "card-text-en.json")
    en_doc = kx._load_json(en_path, "card-text-en.json")
    terms = require_terms(run_dir)
    universe, units, identity = build_universe(en_doc)

    material = _material_for(cfg, run_dir, workspace)
    if ask_dir is None:
        ask_dir = ka.build_bundle(cfg, SID, universe, units, identity=identity,
                                  material=material, workspace=workspace)
    if replay:
        ka.seed_from_replay(ask_dir, replay)

    ask = ka.run_bundle(ask_dir, invoke=not replay, claude_bin=claude_bin,
                        readonly_trees=readonly_trees, workspace=workspace,
                        quiet=quiet)

    triggered, checks = [], []
    decide_report, effective = None, {}
    if ask["exit_code"] != kc.EXIT_OK:
        triggered.append(ask["exit_code"])
        checks.append({"id": "AI0", "name": "batches_complete", "status": "fail",
                       "exit_on_fail": ask["exit_code"],
                       "detail": ["%s; %d of %d batches complete, resume at %s"
                                  % (ask["stopped_by"] or "incomplete",
                                     sum(1 for b in ask["batches"] if b["complete"]),
                                     ask["batch_of"], ask["resume_from"])]})
    else:
        checks.append({"id": "AI0", "name": "batches_complete", "status": "pass",
                       "exit_on_fail": kc.EXIT_AI_BUDGET, "detail": []})
        decide_report, decide_code = kd.decide(ask_dir)
        if decide_code != kc.EXIT_OK:
            triggered.append(decide_code)
        checks.append({"id": "AI1", "name": "decide",
                       "status": "pass" if decide_code == kc.EXIT_OK else "fail",
                       "exit_on_fail": decide_code or kc.EXIT_POLICY,
                       "detail": ([] if decide_code == kc.EXIT_OK
                                  else [decide_report["reason"]])})
        merged, err = kd._read_json(os.path.join(ask_dir, "manifest.merged.json"))
        if not err:
            for ruling in merged.get("rulings") or []:
                effective[ruling.get("unit_id")] = {"ruling": ruling}

    rendered, declared = markup_checks(units, effective)
    checks.append({"id": "X1", "name": "markup_preserved",
                   "status": "fail" if rendered else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT, "detail": rendered})
    checks.append({"id": "X2", "name": "declared_tokens_match_written",
                   "status": "fail" if declared else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT, "detail": declared})
    if rendered:
        triggered.append(kc.EXIT_ARTIFACT)
    if declared:
        triggered.append(kc.EXIT_ARTIFACT)

    generator, translated = build_generator(cfg["slug"], units, effective)
    compile_findings = verify_generator(generator)
    checks.append({"id": "X3", "name": "generator_compiles",
                   "status": "fail" if compile_findings else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT, "detail": compile_findings})
    if compile_findings:
        triggered.append(kc.EXIT_ARTIFACT)

    by_verdict = {}
    for entry in effective.values():
        name = entry["ruling"].get("verdict")
        by_verdict[name] = by_verdict.get(name, 0) + 1
    counts = {
        "cards": len(universe),
        "cards_ruled": len(effective),
        "cards_translated": translated,
        "by_verdict": dict(sorted(by_verdict.items())),
        "terms_available": len(terms),
        "batches": ask["batch_of"],
        "batches_complete": sum(1 for b in ask["batches"] if b["complete"]),
    }

    # THE DURABLE-TIER WRITE, GATED ON EVERY TRIGGERED CODE BEING ABSENT.
    #
    # `data/text/<slug>-ko.py` is a durable-tier artifact (§3.1) AND it is
    # executable Python a human is instructed to run, so the bar is the whole of
    # `triggered` and not merely "it compiles". A run whose ten rules failed --
    # an abstain, a `low`, a coverage gap -- produced no translation this project
    # is willing to keep, and a compiling file is not the same claim as an
    # adjudicated one. `translate` has no gate of its own (§1.2's four are init,
    # terms, mask, typeset), so unlike `terms` there is no carve-out here.
    written = []
    if (write_data and mode == "build" and translated and not triggered):
        written.append(kz.write_data(cfg, "text", "%s-ko.py" % cfg["slug"],
                                     generator, workspace=workspace))

    ai_block = {
        "used": True, "stage_id": SID, "batches": ask["batch_of"],
        "session_ids": (decide_report or {}).get("session_ids")
                       or [b["session_id"] for b in ask["batches"]],
        "envelope_sha256": (decide_report or {}).get("envelope_sha256") or [],
        "merged_manifest_sha256": (decide_report or {}).get("merged_manifest_sha256"),
        "decide_sha256": (decide_report or {}).get("decide_sha256"),
        "decide_outcome": (decide_report or {}).get("outcome") or "incomplete",
        "cost_usd": ask["cost_usd"],
        "model": (cfg.get("ai") or {}).get("model"),
        "replay_of": replay,
        "ask_dir": os.path.relpath(ask_dir, workspace),
    }

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path, en_path]),
        freshness=kc.build_freshness([en_path],
                                     upstream_report_path=os.path.join(run_dir,
                                                                       "terms.json")),
        ai=ai_block,
        results=({"generator_sha256": kc.sha256_bytes(generator.encode("utf-8")),
                  "generator": "%s-ko.py" % cfg["slug"]} if translated else None))
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    if written:
        report["write_set"] = [{"path": os.path.relpath(p, workspace),
                                "action": "create"} for p in written]
    return report, ask_dir


# ---------------------------------------------------------------------------
# 4. --selftest
# ---------------------------------------------------------------------------

def selftest(fault=None, verbose=True):
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    def fires(label, code, fn):
        try:
            fn()
        except kc.KzRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d" % (label, code, exc.code))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    if "universe" in wanted:
        doc = {"cards": [{"arkham_id": "71001", "name": "A", "text": "x",
                          "guid": "5d96e5", "card_id": 918000},
                         {"code": "71002", "name": "B"}]}
        universe, units, identity = build_universe(doc)
        if universe != ["71001", "71002"]:
            findings.append("universe: %r" % universe)
        if identity.get("71001", {}).get("guid") != "5d96e5":
            findings.append("universe: identity was not carried for rule 10")
        if "text" in units["71002"]:
            findings.append("universe: an absent field was materialized")
        fires("universe (no ids)", kc.EXIT_PRECONDITION,
              lambda: build_universe({"cards": [{"name": "no id"}]}))

    if "generator" in wanted:
        effective = {"71001": {"ruling": {"verdict": "translate", "value": {
            "fields": {"name": "한겨울 축제", "text": "쉬움\n[skull]: -X."}}}},
            "71002": {"ruling": {"verdict": "passthrough", "value": {}}}}
        text, translated = build_generator("midwinter", {}, effective)
        if translated != 1:
            findings.append("generator: %d translated, expected 1" % translated)
        if verify_generator(text):
            findings.append("generator: the emitted file does not compile")
        if '71002' in text.split("def render")[0]:
            findings.append("generator: a `passthrough` was written into KO -- it "
                            "would restate the English as though translated")
        # DETERMINISM: the table is what a reviewer diffs, so two runs over the
        # same rulings must differ only in the generated-at line.
        again, _n = build_generator("midwinter", {}, effective)
        strip = lambda s: "\n".join(l for l in s.split("\n") if "S2) on " not in l)
        if strip(text) != strip(again):
            findings.append("generator: two runs over the same rulings differ")
        # Multi-line strings are emitted one source line per text line, so a
        # one-line change is a one-line hunk.
        if '"쉬움\\n"' not in text:
            findings.append("generator: a multi-line field was not split per line")
        # A generated file that does not compile must be CAUGHT, not shipped.
        if not verify_generator("KO['x'] = dict(name=)"):
            findings.append("generator: a syntactically broken generator passed")
        # CODE INJECTION THROUGH THE KEY. `unit_id` arrives from the MERGED
        # MANIFEST, which is model-authored, and it is interpolated into a Python
        # file a human is instructed to RUN. A crafted id can close the string
        # literal and open a statement, and the compile check does NOT catch it
        # -- a well-formed payload compiles by construction. The allowlist is
        # what refuses it.
        hostile = '71001"] = dict(name="a")\nimport os\nKO["y'
        fires("generator (unit_id code injection)", kc.EXIT_ARTIFACT,
              lambda: build_generator("s", {}, {hostile: {"ruling": {
                  "verdict": "translate",
                  "value": {"fields": {"name": "x"}}}}}))
        # ... and a value carrying quotes and backslashes must be escaped rather
        # than refused: the allowlist is on the KEY, the escaping on the VALUE.
        nasty = {"71001": {"ruling": {"verdict": "translate", "value": {
            "fields": {"name": 'a"b\\c\nd'}}}}}
        text, _n = build_generator("s", {}, nasty)
        if verify_generator(text):
            findings.append("generator: a field carrying quotes and backslashes "
                            "was not escaped into a valid literal")

    if "markup" in wanted:
        units = {"71001": {"text": "[combat] hit [[Guest]] <b>x</b>"}}
        # A token SUBSTITUTION keeps every count and changes the card.
        swapped = {"71001": {"ruling": {"verdict": "translate", "value": {
            "fields": {"text": "[agility] 타격 [[손님]] <b>x</b>"}}}}}
        rendered, _declared = markup_checks(units, swapped)
        if not rendered:
            findings.append("markup: [combat] -> [agility] was not caught")
        good = {"71001": {"ruling": {"verdict": "translate", "value": {
            "fields": {"text": "[combat] 타격 [[손님]] <b>x</b>"},
            "icon_tokens": ["combat"]}}}}
        rendered, declared = markup_checks(units, good)
        if rendered or declared:
            findings.append("markup: a correct translation was flagged (%s / %s)"
                            % (rendered, declared))
        # The declaration must describe the string actually written.
        lying = {"71001": {"ruling": {"verdict": "translate", "value": {
            "fields": {"text": "[combat] 타격 [[손님]] <b>x</b>"},
            "icon_tokens": ["agility"]}}}}
        _rendered, declared = markup_checks(units, lying)
        if not declared:
            findings.append("markup: a declaration disagreeing with the written "
                            "string was not caught, so the rendered check is "
                            "circular")

    if "terms-gate" in wanted:
        import tempfile
        holder = tempfile.mkdtemp(prefix="kz-translate-selftest.")
        try:
            fires("terms-gate (no terms.json)", kc.EXIT_PRECONDITION,
                  lambda: require_terms(holder))
            kc.atomic_write_json(os.path.join(holder, "terms.json"),
                                 {"gate": {"status": "pending"},
                                  "results": {"terms": {}}})
            fires("terms-gate (pending)", kc.EXIT_GATE,
                  lambda: require_terms(holder))
            kc.atomic_write_json(os.path.join(holder, "terms.json"),
                                 {"gate": {"status": "accepted"},
                                  "results": {"terms": {"Guest": {"ko": "손님"}}}})
            if require_terms(holder) != {"Guest": {"ko": "손님"}}:
                findings.append("terms-gate: an accepted gate did not yield the "
                                "terminology")
        finally:
            import shutil
            shutil.rmtree(holder, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

_SUMMARY = {
    "universe": "one unit per distinct card id, identity carried for rule 10, "
                "and a corpus with no ids refuses at 13",
    "generator": "the emitted KO table compiles, is deterministic, splits "
                 "multi-line fields per source line, omits `passthrough`, "
                 "refuses a model-authored unit_id that would inject code into "
                 "the generated file, escapes hostile field values, and catches "
                 "a broken generator rather than shipping it",
    "markup": "a [combat] -> [agility] substitution is caught, a correct "
              "translation is not, and a declaration that disagrees with the "
              "written string is caught -- which is what stops the first check "
              "being circular",
    "terms-gate": "no terms.json refuses at 13, a pending gate at 30, and an "
                  "accepted one yields the terminology",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_translate.py",
        description="koreanize stage S2 -- card text into Korean, emitted as a "
                    "generator (design §6 step 10).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--ask-dir", help="re-use an existing ai/translate/<stamp>/")
    parser.add_argument("--replay", metavar="DIR",
                        help="adjudicate a recorded run offline: no claude, no "
                             "credential, no cost")
    parser.add_argument("--readonly-tree", action="append", default=[])
    parser.add_argument("--claude-bin", help="override the shim's binary resolution")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_translate --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-12s %s" % (name, _SUMMARY[name]))
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    mode = "build"
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"
    elif args.replay:
        mode = "replay"

    report, ask_dir = run_translate(
        run_dir, mode=mode, replay=args.replay, ask_dir=args.ask_dir,
        claude_bin=args.claude_bin, readonly_trees=args.readonly_tree,
        quiet=args.quiet, write_data=(mode == "build"))

    dest = run_dir
    if args.dry_run:
        dest = os.path.join(run_dir, "dry-run", STAGE)
        kz.assert_dry_run_dest(kz.load_scenario(os.path.join(run_dir,
                                                             "scenario.json")),
                               STAGE, dest)
    path = kc.write_report(report, dest)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize translate -- %s" % report["slug"])
        print("  cards           : %d (%d ruled, %d translated)"
              % (counts["cards"], counts["cards_ruled"],
                 counts["cards_translated"]))
        print("  by verdict      : %s" % counts["by_verdict"])
        print("  terminology     : %d terms" % counts["terms_available"])
        print("  batches         : %d of %d complete"
              % (counts["batches_complete"], counts["batches"]))
        for check in report["checks"]:
            print("  %-26s: %s  %s" % (check["name"], check["status"],
                                       "; ".join(check["detail"][:3])))
        print("  verdict         : %s (exit %d)"
              % (report["verdict"], report["exit_code"]))
        print("  wrote           : %s" % path)
        print("\n  next: run the generator, then `koreanize.sh check`")
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
