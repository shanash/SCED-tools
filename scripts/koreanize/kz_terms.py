#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `terms` (S1) -- the terminology authority (design §6 step 10, §5.7).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER`.

WHY THIS STAGE HAS A HARD HUMAN GATE AND `translate` DOES NOT
-------------------------------------------------------------
The Midwinter record states the asymmetry exactly:

    "This file is the ONLY term authority for A3 (card text), E2 (campaign
     guide) and D1 (object Nickname/Description). If a term is wrong here it is
     wrong 62 times downstream -- that asymmetry is why the queue stops at this
     file."

A term is decided once and applied everywhere, so an error here is multiplied by
the corpus rather than isolated to a card. `scenario.json`'s `gates` block
therefore carries `terms: required`, and every downstream stage refuses at exit
30 until a human writes `accepted` into `gates/terms-gate.md`.

THE UNIVERSE IS MECHANICALLY DERIVED, NOT PROPOSED BY THE MODEL
---------------------------------------------------------------
`init` cannot size S1's universe -- the terminology set does not exist until this
stage measures it -- so `scenario.json` declares `ai.batch.S1.universe_size:
null` and `kz_ask.py` enforces the ceiling against the MEASURED universe at exit
13 (§3.7). What that measurement is, though, is this module's business and not
the agent's: the candidate set is extracted from the English corpus by
`extract_terms()` below, so the agent RULES on a universe it did not choose. An
agent that both proposes and adjudicates its own universe has no coverage rule
that means anything -- rule 1 would be satisfied by any set it felt like
returning.

THE VERDICTS ARE ABOUT PROVENANCE, WHICH IS WHY RULE 4 CAN CHECK THEM
----------------------------------------------------------------------
`glossary` / `rules_ref` / `coined` are not confidence levels; they are claims
about WHERE a rendering came from, and `kz_decide.EVIDENCE_RULES["S1"]` turns
each into a checkable obligation: `glossary` must cite the glossary, `rules_ref`
must cite something that is not the glossary, and `coined` must cite no glossary
at all -- because a coined term is precisely one no authority carries. A `coined`
term is also S1's NOVEL verdict (`kz_decide.NOVEL_VERDICTS`), so rule 5 requires
it to be `high` or `abstain`.

Exit codes (§4.2):
   0  every term ruled, every rule passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin or its AI contract
  11  stop by policy -- the ten rules stopped this run
  13  precondition -- no init artifact, an empty universe, or a bundle input missing
  25  the stage stopped between batches on its budget; N of M are on disk
  65  claude unavailable / unauthenticated / timed out
  66  AI manifest invalid -- schema, coverage, or run binding
  67  the terminology failed structural verification (T1/T2)

NOTE THERE IS NO 30 IN THAT TABLE, AND THAT IS THE POINT. A pending gate is not
this stage's failure: it attaches a `pending` gate block to its report and exits
0, exactly as `kz_init.py` does, and `compute_consumable` is what stops anything
downstream consuming an unreviewed terminology. `translate` is where the 30 comes
from, because `translate` is the consumer.
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

STAGE = "terms"
SID = "S1"

# Declared in the MODULE BODY (§3.2): a scenario.json that does not list `terms`
# in ai.required_stages refuses at exit 4 the moment it is bound, and
# `kz_common.write_report` refuses a build report with `ai: null` at exit 11.
kc.declare_ai(STAGE, required=True)

FAULTS = ("extract", "empty-universe", "gate")

#: The kinds of term the extractor distinguishes. `trait` and `icon` are closed
#: sets the corpus states literally; `name` and `subname` are per card; `title`
#: is the scenario's own name, which §1.1 of the Midwinter terminology file
#: singles out as gating everything downstream.
TERM_KINDS = ("title", "name", "subname", "trait", "icon")


# ---------------------------------------------------------------------------
# 1. The universe -- extracted from the English corpus, never proposed
# ---------------------------------------------------------------------------

def extract_terms(en_doc, scenario_name=None):
    """(universe[], units{}, identity{}) -- the candidate terminology set.

    MECHANICAL and total over the corpus: card names, subnames, every `[[Trait]]`
    reference and every `[icon]` token, plus the scenario title. Nothing here is
    a judgement -- the judgement is the agent's, and it is made against a
    universe it did not get to choose.

    Occurrences are counted and a bounded sample of contexts carried, because a
    term appearing once and a term appearing forty times are different decisions
    and the reviewer of the gate needs to see which is which.
    """
    cards = (en_doc.get("cards") if isinstance(en_doc, dict) else en_doc) or []
    seen = {}

    def note(term, kind, context=None, card=None):
        term = (term or "").strip()
        if not term:
            return
        entry = seen.setdefault(term, {"term": term, "kind": kind,
                                       "occurrences": 0, "examples": [],
                                       "cards": []})
        entry["occurrences"] += 1
        # The FIRST kind wins, and the order of the calls below is therefore the
        # precedence: a string that is both a card name and a trait is a name.
        if context and len(entry["examples"]) < 3:
            entry["examples"].append(context[:160])
        if card and card not in entry["cards"] and len(entry["cards"]) < 8:
            entry["cards"].append(card)

    if scenario_name:
        note(scenario_name, "title", context=scenario_name)

    for card in cards:
        ident = str(card.get("arkham_id") or card.get("code") or "")
        note(card.get("name"), "name", context=card.get("name"), card=ident)
        note(card.get("subname"), "subname", context=card.get("subname"),
             card=ident)
        for field in kx.TEXT_FIELDS:
            text = card.get(field)
            if not text:
                continue
            icons, _refs, _tags = kx.scan(text)
            for icon in icons:
                note(icon, "icon", context=text, card=ident)
            for trait in _trait_refs(text):
                note(trait, "trait", context=text, card=ident)

    units, identity = {}, {}
    for term in sorted(seen):
        entry = seen[term]
        unit_id = "%s:%s" % (entry["kind"], term)
        units[unit_id] = {"term": term, "kind": entry["kind"],
                          "occurrences": entry["occurrences"],
                          "examples": entry["examples"],
                          "cards": entry["cards"]}
    # Sorted on the UNIT ID, not on the term: the id is what `inputs.json`
    # carries, what the batch splitter partitions and what rule 1 checks
    # coverage over, so it is the ordering that has to be stable. Sorting on the
    # term leaves the emitted list unsorted the moment two kinds interleave.
    universe = sorted(units)
    if not universe:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the English corpus yields no terminology candidates",
                  "check that card-text-en.json carries a populated cards[]")
    return universe, units, identity


def _trait_refs(text):
    """Every `[[Trait]]` body, by character walk.

    The same no-regex rule as `kz_checkers.scan` and for the same reason -- but
    note this returns the BODIES while `scan` returns only a count, because the
    terminology universe needs the strings themselves.
    """
    out = []
    i = 0
    n = len(text or "")
    while i < n:
        if text[i:i + 2] == "[[":
            close = text.find("]]", i + 2)
            if close != -1:
                body = text[i + 2:close].strip()
                if body:
                    out.append(body)
                i = close + 2
                continue
        i += 1
    return out


# ---------------------------------------------------------------------------
# 2. The human gate -- `terms` is one of the four (§1.2, §5.7)
# ---------------------------------------------------------------------------

GATE_STUB = """# terms gate -- {slug}

status: pending
reviewer:
date:
gate_for: terms
bound_sha256: {bound}

`terms` ruled {ruled} of {total} candidate terms. This file is the ONLY term
authority for `translate`, for the campaign guide and for the object
`Nickname`/`Description` -- if a term is wrong here it is wrong once per
occurrence downstream, which is why the pipeline stops at this file.

Set `status:` to `accepted` (or `rejected`), record who decided and when. Every
downstream stage refuses at exit 30 until this reads `accepted`.

## Read for meaning, not for containment

An unticked item is an unperformed review, by the same rule that makes an
unnamed gate an unperformed one.

- [ ] every `coined` term reads as Korean a player would recognise
- [ ] no `coined` term duplicates an existing `glossary` rendering under a new name
- [ ] proper nouns follow one policy consistently (transliterate vs translate)
- [ ] icon readings match the rules reference
- [ ] trait renderings are identical everywhere the trait appears

## Rulings by verdict

{rows}

- terminology : `{terms_json}`
- review sheet: `{terms_md}`

A prose defect found here is routed to `audit`(S7), whose job is to widen a
single finding into the predicate that catches its siblings IN THE GENERATOR.
"""


def parse_gate(path):
    """The gate file's front-matter, as a dict. Absent file -> a pending record."""
    record = {"status": "pending", "reviewer": None, "date": None,
              "bound_sha256": None}
    if not os.path.exists(path):
        return record
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for key in ("status", "reviewer", "date", "bound_sha256"):
                prefix = "%s:" % key
                if line.startswith(prefix):
                    value = line[len(prefix):].strip() or None
                    record[key] = value
            if line.startswith("## "):
                break
    return record


def write_gate_stub(run_dir, slug, gate, counts, terms_json, terms_md):
    """The gate stub is GENERATED, never blank: an unnamed gate is an unperformed
    gate (§5.7)."""
    rows = "\n".join("| `%s` | %s |" % (k, v)
                     for k, v in sorted((counts.get("by_verdict") or {}).items()))
    rows = "| verdict | terms |\n|---|---|\n" + (rows or "| (none) | 0 |")
    path = os.path.join(run_dir, "gates", "terms-gate.md")
    kc.atomic_write_text(path, GATE_STUB.format(
        slug=slug, bound=gate.get("bound_sha256") or "",
        ruled=counts.get("terms_ruled", 0), total=counts.get("terms", 0),
        rows=rows, terms_json=terms_json, terms_md=terms_md))
    return path


# ---------------------------------------------------------------------------
# 3. The artifacts -- data/terms/<slug>.json and <slug>-terminology-ko.md
# ---------------------------------------------------------------------------

def build_terms_json(slug, units, effective):
    """`data/terms/<slug>.json` -- per-term source, confidence and rationale.

    A MAPPING BUILT KEY-SORTED before it is handed over, because `sced_io.py:99`
    passes no `sort_keys` (§5.3): a mapping whose key order depends on insertion
    produces a different byte stream on every run and the receipt stops being a
    diff anybody can read.
    """
    terms = {}
    for unit_id, entry in sorted(effective.items()):
        ruling = entry["ruling"]
        unit = units.get(unit_id) or {}
        value = ruling.get("value") or {}
        terms[unit.get("term") or unit_id] = kc.sorted_mapping({
            "ko": value.get("ko"),
            "romanization": value.get("romanization"),
            "note": value.get("note"),
            "kind": unit.get("kind"),
            "source": ruling.get("verdict"),
            "confidence": ruling.get("confidence"),
            "rationale": ruling.get("rationale"),
            "occurrences": unit.get("occurrences"),
        })
    return kc.sorted_mapping({
        "schema_version": kc.SCHEMA_VERSION,
        "generated_by": "koreanize terms",
        "generated_at": kc.utc_now(),
        "slug": slug,
        "terms": kc.sorted_mapping(terms),
    })


TERMS_MD_HEADER = """# Terminology -- {slug} (English to Korean)

Generated by `koreanize terms` (S1). This file is the review surface; the
machine-readable authority is `{slug}.json` beside it and `translate` reads that
one. Correct the **Korean** column here AND in the JSON, or re-run the stage.

    bound_sha256: {bound}

**That digest is this file's whole integrity claim, so check it.** It is the
sha256 of `{slug}.json` as this review sheet was written from it, and it is the
same value the gate records. The pair cannot be written as one transaction --
one is JSON and one is Markdown, so there is no batch writer that covers both --
so a crash between the two writes, or a hand-edit of one alone, can leave this
sheet describing a terminology that is no longer the one the gate is bound to.
Rather than pretend the window does not exist, the digest makes it VISIBLE: if
this line disagrees with the `bound_sha256` in `gates/terms-gate.md`, you are
reading a stale sheet and must re-run the stage before accepting anything.

| Value | Meaning | Reviewable? |
|---|---|---|
| `glossary` | the canonical first authority | no |
| `rules_ref` | the official Korean rules reference | no |
| `coined` | no authority carries it; invented here | **yes** |

A `coined` term is S1's novel verdict and must be ruled `high` or the stage stops
(rule 5). Those are the rows to read first.

"""


def build_terms_md(slug, units, effective, bound=None):
    """The human review surface. `coined` rows are listed FIRST and separately,
    because they are the only ones that need a decision rather than a check.

    `bound` is the digest of the terminology JSON this sheet was built from, and
    it is stamped into the header so a reviewer can tell a current sheet from a
    stale one without diffing two files.
    """
    rows = []
    for unit_id, entry in sorted(effective.items()):
        ruling = entry["ruling"]
        unit = units.get(unit_id) or {}
        value = ruling.get("value") or {}
        rows.append((ruling.get("verdict"), unit.get("kind"),
                     unit.get("term") or unit_id, value.get("ko"),
                     ruling.get("confidence"), unit.get("occurrences"),
                     ruling.get("rationale") or ""))
    out = [TERMS_MD_HEADER.format(slug=slug, bound=bound or "(not recorded)")]
    for heading, wanted in (("## Coined -- read these first", ("coined",)),
                            ("## Resolved from an authority",
                             ("glossary", "rules_ref"))):
        selected = [r for r in rows if r[0] in wanted]
        out.append("%s\n" % heading)
        if not selected:
            out.append("_(none)_\n")
            continue
        out.append("| English | Korean | kind | source | confidence | uses |")
        out.append("|---|---|---|---|---|---|")
        for verdict, kind, term, ko, confidence, uses, _why in sorted(
                selected, key=lambda r: (r[1] or "", r[2] or "")):
            out.append("| %s | **%s** | %s | `%s` | %s | %s |"
                       % (term, ko or "", kind or "", verdict, confidence or "",
                          uses if uses is not None else ""))
        out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 4. The stage
# ---------------------------------------------------------------------------

def _material_for(cfg, run_dir, workspace=None):
    """Everything the agent is given, COPIED. It holds no repository path.

    The prior terminology is included whenever it exists, because a re-run must
    be able to AGREE with what a human already accepted rather than re-coin it:
    a stage that silently re-derives a settled rendering makes the gate's
    acceptance meaningless one run later.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    material = {}
    for name in ("card-text-en.json", "card-source-en.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            material[name] = path
    prior = os.path.join(workspace, cfg["guard"]["data_root"], "terms",
                         "%s.json" % cfg["slug"])
    if os.path.exists(prior):
        material["terms-prior.json"] = prior
    return material


def run_terms(run_dir, mode="build", replay=None, ask_dir=None, claude_bin=None,
              workspace=None, readonly_trees=(), quiet=False, write_data=True):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. <run_dir>/card-text-en.json exists and is readable JSON     -> exit 13
      4. the extracted universe is non-empty                        -> exit 13
      5. it is inside ai.batch.S1's ceiling                         -> exit 13
      6. the ask bundle's policy, prompt and schema are on disk     -> exit 13
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    en_path = os.path.join(run_dir, "card-text-en.json")
    en_doc = kx._load_json(en_path, "card-text-en.json")
    universe, units, identity = extract_terms(en_doc, cfg.get("scenario_name"))

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

    # T1 -- every ruling carries a Korean rendering. `VALUE_REQUIRED["S1"]` makes
    # `ko` mandatory in the SCHEMA, so this is the artifact-side restatement: a
    # ruling that validated and still produced no usable string is caught here
    # rather than discovered by `translate` on the next stage.
    empty = sorted(uid for uid, entry in effective.items()
                   if not ((entry["ruling"].get("value") or {}).get("ko") or "").strip())
    checks.append({"id": "T1", "name": "every_term_rendered",
                   "status": "fail" if empty else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": ["%s: ruled but carries no `ko`" % u for u in empty]})
    if empty:
        triggered.append(kc.EXIT_ARTIFACT)

    # T2 -- one Korean rendering per English term, corpus-wide. Two spellings of
    # one trait is the defect class this whole stage exists to prevent, and it is
    # invisible to every per-ruling rule: each ruling is individually valid.
    collisions = _rendering_collisions(units, effective)
    checks.append({"id": "T2", "name": "one_rendering_per_term",
                   "status": "fail" if collisions else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT, "detail": collisions})
    if collisions:
        triggered.append(kc.EXIT_ARTIFACT)

    by_verdict = {}
    for entry in effective.values():
        name = entry["ruling"].get("verdict")
        by_verdict[name] = by_verdict.get(name, 0) + 1
    counts = {
        "terms": len(universe),
        "terms_ruled": len(effective),
        "by_verdict": dict(sorted(by_verdict.items())),
        "by_kind": _by_kind(units),
        "batches": ask["batch_of"],
        "batches_complete": sum(1 for b in ask["batches"] if b["complete"]),
    }

    terms_json = build_terms_json(cfg["slug"], units, effective)
    # The digest is computed BEFORE the review sheet, because the sheet carries
    # it: a reviewer must be able to tell that the sheet in front of them
    # describes the terminology the gate is bound to.
    bound = kc.sha256_bytes(kc.json_bytes(terms_json))
    terms_md = build_terms_md(cfg["slug"], units, effective, bound=bound)

    gate_path = os.path.join(run_dir, "gates", "terms-gate.md")
    prior_gate = parse_gate(gate_path)
    prev_report, _err = kd._read_json(os.path.join(run_dir, "terms.json"))
    prev_review = ((prev_report or {}).get("gate") or {})
    sha_moved = bool(prior_gate.get("bound_sha256")
                     and prior_gate["bound_sha256"] != bound)
    gate = kc.carry_review(prior_gate, prev_review, sha_moved, kc.utc_now())
    gate["gate_for"] = STAGE
    gate["bound_sha256"] = bound
    # A PENDING GATE IS NOT THIS STAGE'S FAILURE, AND EXIT_GATE IS DELIBERATELY
    # NOT APPENDED TO `triggered`.
    #
    # The gate blocks CONSUMERS, not the producer. `kz_init.py` is the established
    # precedent and the one this follows: it attaches a `pending` gate block to
    # its report and exits 0 -- "every downstream stage refuses until you accept
    # it; `init` itself exits 0 and prints the gate path". The blocking is done by
    # `compute_consumable`, which returns False for any report whose
    # `gate.status != "accepted"` (kz_common.py), so nothing downstream can
    # consume an unreviewed terminology.
    #
    # Doing it the other way round -- exiting 30 here -- would make a stage FAIL
    # on every first successful run, before any human could possibly have accepted
    # a file that did not exist until this run wrote it.
    #
    # THE DURABLE-TIER WRITE IS GATED ON THE ADJUDICATION. `data/terms/` outlives
    # this run (§3.1), so a run whose ten rules FAILED must not leave one behind:
    # a manifest that abstained, came back `low`, or lost coverage is not a
    # terminology, and writing it anyway would let the NEXT run's "prior
    # terminology" material be something nothing ever approved.
    written = []
    if write_data and mode == "build" and effective and not triggered:
        # The MIRROR WRITER is the only code in the tool that opens a path under
        # SCED-tools/ (§3.1). This module declares no write root outside
        # <run_dir> and cannot reach one.
        written.append(kz.write_data(cfg, "terms", "%s.json" % cfg["slug"],
                                     terms_json, workspace=workspace))
        written.append(kz.write_data(cfg, "terms",
                                     "%s-terminology-ko.md" % cfg["slug"],
                                     terms_md, workspace=workspace))

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
                                                                       "init.json")),
        ai=ai_block, gate=gate,
        results=({"terms": terms_json["terms"]} if effective else None))
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    if written:
        report["write_set"] = [{"path": os.path.relpath(p, workspace),
                                "action": "create"} for p in written]
    if mode == "build":
        write_gate_stub(run_dir, cfg["slug"], gate, counts,
                        "%s.json" % cfg["slug"],
                        "%s-terminology-ko.md" % cfg["slug"])
    return report, ask_dir


def _rendering_collisions(units, effective):
    """One English term rendered two ways, or one Korean string claimed by two
    English terms. BOTH directions, because they are different defects: the first
    fragments a term across the corpus, the second merges two.
    """
    forward, backward = {}, {}
    for unit_id, entry in sorted(effective.items()):
        term = (units.get(unit_id) or {}).get("term") or unit_id
        korean = ((entry["ruling"].get("value") or {}).get("ko") or "").strip()
        if not korean:
            continue
        forward.setdefault(term, set()).add(korean)
        backward.setdefault(korean, set()).add(term)
    findings = []
    for term in sorted(forward):
        if len(forward[term]) > 1:
            findings.append("%r was rendered %d ways: %s"
                            % (term, len(forward[term]), sorted(forward[term])))
    for korean in sorted(backward):
        if len(backward[korean]) > 1:
            findings.append("%r renders %d different English terms: %s"
                            % (korean, len(backward[korean]),
                               sorted(backward[korean])))
    return findings


def _by_kind(units):
    out = {}
    for unit in units.values():
        kind = unit.get("kind")
        out[kind] = out.get(kind, 0) + 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# 5. --selftest
# ---------------------------------------------------------------------------

def selftest(fault=None, verbose=True):
    """Prove each refusal fires on the fault it targets."""
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

    if "extract" in wanted:
        doc = {"cards": [
            {"code": "71001", "name": "The Bloodless Man", "subname": "Host",
             "text": "[skull]: at a [[Private]] location"},
            {"code": "71002", "name": "Guest", "text": "[[Guest]] [combat] [skull]"}]}
        universe, units, _identity = extract_terms(doc, "The Midwinter Gala")
        if "title:The Midwinter Gala" not in universe:
            findings.append("extract: the scenario title is not in the universe")
        if "trait:Private" not in universe:
            findings.append("extract: a [[Trait]] reference was not extracted")
        if "icon:skull" not in universe:
            findings.append("extract: an [icon] token was not extracted")
        if units["icon:skull"]["occurrences"] != 2:
            findings.append("extract: occurrences are not counted across cards")
        # A string that is both a card NAME and a trait is ONE term with one
        # rendering -- fragmenting it is the defect T2 exists to catch.
        if "name:Guest" not in universe or "trait:Guest" in universe:
            findings.append("extract: a name/trait homonym produced two units")
        if universe != sorted(universe):
            findings.append("extract: the universe is not deterministically ordered")
        # T2, both directions.
        units2 = {"a": {"term": "Guest"}, "b": {"term": "Host"}}
        same = {"a": {"ruling": {"value": {"ko": "손님"}}},
                "b": {"ruling": {"value": {"ko": "손님"}}}}
        if not _rendering_collisions(units2, same):
            findings.append("extract: two English terms sharing one Korean "
                            "rendering were not caught")

    if "empty-universe" in wanted:
        fires("empty-universe (no cards)", kc.EXIT_PRECONDITION,
              lambda: extract_terms({"cards": []}))
        fires("empty-universe (cards with nothing translatable)",
              kc.EXIT_PRECONDITION,
              lambda: extract_terms({"cards": [{"code": "1"}]}))

    if "gate" in wanted:
        # The gate stub is GENERATED, never blank, and a fresh one is `pending`
        # -- so a stage that has never been reviewed cannot be consumable.
        record = parse_gate(os.path.join(os.sep, "nonexistent", "terms-gate.md"))
        if record["status"] != "pending":
            findings.append("gate: an absent gate did not read as pending")
        # carry_review's branch 1: the bound sha moved under a live acceptance,
        # so the verdict is superseded and reset -- a verdict can never outlive
        # the artifact it was given to.
        carried = kc.carry_review({"status": "accepted", "reviewer": "me",
                                   "date": "2026-08-24"},
                                  {"status": "accepted"}, True, "now")
        if carried["status"] != "pending" or not carried.get("superseded"):
            findings.append("gate: a moved bound_sha256 did not supersede the "
                            "acceptance (%r)" % carried)
        held = kc.carry_review({"status": "accepted"}, {"status": "accepted"},
                               False, "now")
        if held["status"] != "accepted":
            findings.append("gate: an unmoved acceptance was not held")
        # The review sheet must carry the digest of the terminology it was built
        # from. The JSON and the Markdown cannot be written as one transaction,
        # so this is what makes a stale sheet VISIBLE to the reviewer instead of
        # silently accepted -- and the gate records the same value.
        terms_json = build_terms_json("s", {"name:A": {"term": "A"}},
                                      {"name:A": {"ruling": {
                                          "verdict": "coined",
                                          "value": {"ko": "가"}}}})
        digest = kc.sha256_bytes(kc.json_bytes(terms_json))
        sheet = build_terms_md("s", {"name:A": {"term": "A"}},
                               {"name:A": {"ruling": {"verdict": "coined",
                                                      "value": {"ko": "가"}}}},
                               bound=digest)
        if "bound_sha256: %s" % digest not in sheet:
            findings.append("gate: the review sheet does not carry the digest of "
                            "the terminology it was built from, so a stale sheet "
                            "is indistinguishable from a current one")
        if "(not recorded)" not in build_terms_md("s", {}, {}):
            findings.append("gate: a sheet built with no digest does not say so")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------

_SUMMARY = {
    "extract": "the universe is mechanically derived from the corpus, ordered, "
               "de-duplicated across kinds, and T2 catches a term rendered two "
               "ways in either direction",
    "empty-universe": "a corpus with nothing translatable refuses at 13 rather "
                      "than asking the agent to invent a universe",
    "gate": "an absent gate reads pending, a moved bound_sha256 supersedes a "
            "live acceptance, an unmoved one is held, and the review sheet "
            "carries the digest of the terminology it was built from so a stale "
            "sheet is visible rather than silently accepted",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_terms.py",
        description="koreanize stage S1 -- the terminology authority "
                    "(design §6 step 10).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--ask-dir", help="re-use an existing ai/terms/<stamp>/")
    parser.add_argument("--replay", metavar="DIR",
                        help="adjudicate a recorded run offline: no claude, no "
                             "credential, no cost")
    parser.add_argument("--readonly-tree", action="append", default=[],
                        help="a tree the agent must not move; repeatable (rule 8c)")
    parser.add_argument("--claude-bin", help="override the shim's binary resolution")
    parser.add_argument("--dry-run", action="store_true",
                        help="write <run_dir>/dry-run/terms/terms.dry-run.json")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_terms --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-15s %s" % (name, _SUMMARY[name]))
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

    report, ask_dir = run_terms(
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
        print("koreanize terms -- %s" % report["slug"])
        print("  terms           : %d (%d ruled)"
              % (counts["terms"], counts["terms_ruled"]))
        print("  by verdict      : %s" % counts["by_verdict"])
        print("  by kind         : %s" % counts["by_kind"])
        print("  batches         : %d of %d complete"
              % (counts["batches_complete"], counts["batches"]))
        print("  gate            : %s" % report["gate"]["status"])
        for check in report["checks"]:
            print("  %-18s: %s  %s" % (check["name"], check["status"],
                                       "; ".join(check["detail"][:3])))
        print("  verdict         : %s (exit %d)"
              % (report["verdict"], report["exit_code"]))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
