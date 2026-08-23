#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `audit` (S7) -- widen one found defect into the predicate that
catches its siblings (design §6 step 10, §5.7).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER`.

WHAT THIS STAGE IS FOR, IN ONE RECORDED CASE
---------------------------------------------
Every mechanical check on the Midwinter corpus passed, and a whole separate task
(`fix-korean-grammar-defects`) was then needed to find `주요목적를` -- FIVE TIMES,
IN THE GENERATOR. That is the shape of the defect class this stage exists for:

  * a human finds ONE instance, by reading, at a gate;
  * the mechanical checks did not catch it, because none of them was looking;
  * and the instance is almost never alone, because it came from a GENERATOR that
    will reproduce it everywhere the same construction appears.

So the question `audit` asks is never "how do I fix this?". It is: **does this
finding generalize into a predicate, and if so, which checker owns it?** A
verdict of `widen` produces a predicate; `isolated` records that the instance is
genuinely alone and why.

THE OUTPUT IS DATA AND NEVER A CODE EDIT
-----------------------------------------
Policy rule 1. `value` is `{predicate, checker_filename}`: a description of the
check and the file it belongs in. A human writes the code, and
`kz_checkers.py`'s own rule -- that a checker is authored apart from what it
checks -- is exactly why the agent must not write it here either.

`widen` is S7's NOVEL verdict (`kz_decide.NOVEL_VERDICTS["S7"]`), and rightly:
it authors a new predicate that will refuse artifacts from then on, which is as
durable as a coined term. Rule 5 requires it to be `high` or `abstain`.

A GATE-INVOKED STAGE, LIKE `triage`
------------------------------------
It has no position in the DAG (§1.2) and is never a predecessor of anything. Its
`universe_size` is `null` in `scenario.json` because a gate-invoked stage cannot
know its universe until it is invoked, so `kz_ask.py` enforces the ceiling
against the MEASURED universe at exit 13 (§3.7). `ai.batch.S7` declares
`max_units_per_call: 1` and `max_calls: 1`: one seed defect per invocation, on
purpose -- widening two unrelated findings in one call invites one predicate that
covers neither.

Exit codes (§4.2):
   0  the seed was ruled and every rule passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal
  11  stop by policy -- the ten rules stopped this run
  13  precondition -- no seed defect, or a bundle input is missing
  25  the stage stopped between batches on its budget
  65  claude unavailable / unauthenticated / timed out
  66  AI manifest invalid
  67  the proposed predicate failed structural verification
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

STAGE = "audit"
SID = "S7"

kc.declare_ai(STAGE, required=True)

FAULTS = ("seed", "checker-target", "empty-universe")

#: The checkers a widened predicate may be assigned to. CLOSED, and asserted
#: against the package directory at ruling time: a predicate assigned to a file
#: that does not exist is a predicate nobody will ever write, and the S7 schema's
#: `checker_filename` pattern only constrains the SHAPE of the name.
CHECKER_TARGETS = ("kz_checkers.py", "kz_verify.py")


# ---------------------------------------------------------------------------
# 1. The universe -- one seed defect
# ---------------------------------------------------------------------------

def universe_from_seed(seed):
    """(universe[], units{}, identity{}) from one seed defect.

    A seed is what a human wrote down at a gate: what they saw, where, and in
    which artifact. It is deliberately NOT a checker finding -- if a checker had
    found it there would be nothing to widen.
    """
    if not isinstance(seed, dict):
        kc.refuse(kc.EXIT_PRECONDITION, "the seed defect is not a JSON object")
    finding = (seed.get("finding") or "").strip()
    if not finding:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the seed defect carries no `finding`",
                  "audit widens ONE observed defect; describe what was seen")
    stage = (seed.get("found_in_stage") or "").strip()
    if stage and stage not in kz.STAGES:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "found_in_stage %r is not a koreanize stage" % stage,
                  "one of %s" % list(kz.STAGES))
    unit_id = "seed:%s" % (seed.get("key") or kc.sha256_bytes(
        finding.encode("utf-8"))[:12])
    payload = {
        "finding": finding,
        "found_in_stage": stage or None,
        "found_in_artifact": seed.get("found_in_artifact"),
        "occurrences_known": seed.get("occurrences_known"),
        "examples": list(seed.get("examples") or [])[:8],
        "checker_targets": list(CHECKER_TARGETS),
    }
    identity = {}
    record = dict((k, seed[k]) for k in kd.IDENTITY_KEYS if k in seed)
    if record:
        identity[unit_id] = record
    return [unit_id], {unit_id: payload}, identity


def load_seed(path=None, finding=None, stage=None, artifact=None, examples=()):
    """A seed from a file, or assembled from the CLI flags."""
    if path:
        seed, err = kd._read_json(path)
        if err:
            kc.refuse(kc.EXIT_PRECONDITION, "the seed file is unreadable", err)
        return seed
    if not finding:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "audit needs a seed defect",
                  "give --seed <file> or --finding '<what you saw>'")
    return {"finding": finding, "found_in_stage": stage,
            "found_in_artifact": artifact, "examples": list(examples)}


# ---------------------------------------------------------------------------
# 2. The artifact check -- the proposed predicate has to be actionable
# ---------------------------------------------------------------------------

def predicate_checks(units, effective, package_dir=None):
    """The artifact half of containment for S7, reported as 67 (§4.4).

    Two obligations:
      * a `widen` must name a predicate long enough to implement -- the schema
        already bounds it at 3..800 characters, so what is checked here is that
        the ruling actually carries one rather than an empty string that passed
        `minLength` on a space; and
      * `checker_filename` must name a file that EXISTS. The S7 schema constrains
        the shape (`^[A-Za-z0-9_.-]+\\.py$`) and nothing else, so without this a
        perfectly valid manifest can assign a predicate to `kz_grammar.py` and
        the widening is quietly addressed to nobody.
    """
    package_dir = package_dir or kc.PACKAGE_DIR
    findings = []
    for unit_id in sorted(effective):
        ruling = effective[unit_id]["ruling"]
        if ruling.get("verdict") != "widen":
            continue
        value = ruling.get("value") or {}
        predicate = (value.get("predicate") or "").strip()
        target = (value.get("checker_filename") or "").strip()
        if not predicate:
            findings.append("%s: `widen` with no predicate to implement" % unit_id)
        if target not in CHECKER_TARGETS:
            findings.append("%s: checker_filename %r is not one of %s"
                            % (unit_id, target, list(CHECKER_TARGETS)))
        elif not os.path.exists(os.path.join(package_dir, target)):
            findings.append("%s: checker_filename %r does not exist in %s"
                            % (unit_id, target, package_dir))
    return findings


# ---------------------------------------------------------------------------
# 3. The stage
# ---------------------------------------------------------------------------

def _material_for(cfg, run_dir, workspace=None):
    """Everything the agent is given, COPIED. It holds no repository path.

    The GENERATOR is included whenever it exists, and it is the most important
    entry: the recorded defect class lives there, so a widening that cannot read
    the thing that produced the instances is guessing at how many there are.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    material = {}
    for name in ("card-text-ko.json", "card-text-en.json", "check.json"):
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            material[name] = path
    generator = os.path.join(workspace, cfg["guard"]["data_root"], "text",
                             "%s-ko.py" % cfg["slug"])
    if os.path.exists(generator):
        material["generator.py"] = generator
    terms = os.path.join(workspace, cfg["guard"]["data_root"], "terms",
                         "%s.json" % cfg["slug"])
    if os.path.exists(terms):
        material["terms.json"] = terms
    return material


def run_audit(run_dir, seed, mode="build", replay=None, ask_dir=None,
              claude_bin=None, workspace=None, readonly_trees=(), quiet=False):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. the seed carries a `finding`, and a real stage if it names one
                                                                   -> exit 13
      4. the measured universe is inside ai.batch.S7's ceiling      -> exit 13
      5. the ask bundle's policy, prompt and schema are on disk     -> exit 13
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)
    universe, units, identity = universe_from_seed(seed)

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

    predicate_findings = predicate_checks(units, effective)
    checks.append({"id": "A1", "name": "predicate_actionable",
                   "status": "fail" if predicate_findings else "pass",
                   "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": predicate_findings})
    if predicate_findings:
        triggered.append(kc.EXIT_ARTIFACT)

    by_verdict = {}
    for entry in effective.values():
        name = entry["ruling"].get("verdict")
        by_verdict[name] = by_verdict.get(name, 0) + 1
    counts = {
        "seeds": len(universe),
        "seeds_ruled": len(effective),
        "widen": by_verdict.get("widen", 0),
        "isolated": by_verdict.get("isolated", 0),
        "abstain": by_verdict.get("abstain", 0),
        "batches": ask["batch_of"],
        "batches_complete": sum(1 for b in ask["batches"] if b["complete"]),
    }

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

    results = None
    if effective:
        results = {"proposals": [{
            "unit_id": unit_id,
            "verdict": entry["ruling"].get("verdict"),
            "confidence": entry["ruling"].get("confidence"),
            "predicate": (entry["ruling"].get("value") or {}).get("predicate"),
            "checker_filename": (entry["ruling"].get("value") or {}).get(
                "checker_filename"),
            "rationale": entry["ruling"].get("rationale"),
        } for unit_id, entry in sorted(effective.items())],
            "seed": dict(units[universe[0]])}

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path]),
        freshness=kc.build_freshness([scenario_path]),
        ai=ai_block, results=results)
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
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

    if "seed" in wanted:
        # The recorded case: one instance seen by a human, in the GENERATOR.
        seed = {"finding": "주요목적를 should be 주요목적을 (jongseong)",
                "found_in_stage": "translate",
                "found_in_artifact": "midwinter-ko.py",
                "occurrences_known": 1,
                "examples": ["주요목적를 완료하십시오"]}
        universe, units, _identity = universe_from_seed(seed)
        if len(universe) != 1:
            findings.append("seed: %d units, expected exactly 1" % len(universe))
        if units[universe[0]]["found_in_stage"] != "translate":
            findings.append("seed: the owning stage was not carried")
        if units[universe[0]]["checker_targets"] != list(CHECKER_TARGETS):
            findings.append("seed: the closed target set was not handed over")
        # The unit id is derived from the finding, so the same seed is the same
        # unit -- a re-run resumes rather than re-adjudicating under a new id.
        if universe != universe_from_seed(dict(seed))[0]:
            findings.append("seed: the unit id is not stable for one finding")
        # An explicit key wins, so two genuinely different seeds can share text.
        if universe_from_seed(dict(seed, key="k"))[0] != ["seed:k"]:
            findings.append("seed: an explicit key was ignored")

    if "empty-universe" in wanted:
        fires("empty-universe (no finding)", kc.EXIT_PRECONDITION,
              lambda: universe_from_seed({"found_in_stage": "translate"}))
        fires("empty-universe (blank finding)", kc.EXIT_PRECONDITION,
              lambda: universe_from_seed({"finding": "   "}))
        fires("empty-universe (not an object)", kc.EXIT_PRECONDITION,
              lambda: universe_from_seed(["a finding"]))
        fires("empty-universe (unreal stage)", kc.EXIT_PRECONDITION,
              lambda: universe_from_seed({"finding": "x",
                                          "found_in_stage": "spellcheck"}))
        fires("no seed at all", kc.EXIT_PRECONDITION, lambda: load_seed())

    if "checker-target" in wanted:
        _u, units, _i = universe_from_seed({"finding": "x"})
        unit_id = list(units)[0]

        def ruling(verdict, predicate, target):
            return {unit_id: {"ruling": {"verdict": verdict, "value": {
                "predicate": predicate, "checker_filename": target}}}}

        # A predicate addressed to a file that does not exist is addressed to
        # nobody -- the schema's pattern cannot catch it.
        if not predicate_checks(units, ruling("widen", "p", "kz_grammar.py")):
            findings.append("checker-target: a non-existent checker was accepted")
        if not predicate_checks(units, ruling("widen", "   ", "kz_checkers.py")):
            findings.append("checker-target: a blank predicate was accepted")
        if predicate_checks(units, ruling("widen", "audit 을/를 after digits",
                                          "kz_checkers.py")):
            findings.append("checker-target: a well-formed widening was rejected")
        # `isolated` proposes no predicate and must not be asked for one.
        if predicate_checks(units, ruling("isolated", "", "")):
            findings.append("checker-target: `isolated` was asked for a predicate")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

_SUMMARY = {
    "seed": "one seed defect becomes exactly one unit, its owning stage and the "
            "closed checker set are carried, and the unit id is stable",
    "checker-target": "a predicate addressed to a non-existent checker is "
                      "refused, a blank one is refused, and `isolated` is not "
                      "asked for one",
    "empty-universe": "a seed with no finding, a blank one, a non-object and an "
                      "unreal stage all refuse at 13",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_audit.py",
        description="koreanize stage S7 -- widen one found defect into the "
                    "predicate that catches its siblings (design §6 step 10).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--seed", help="a JSON file describing the found defect")
    parser.add_argument("--finding", help="the defect, in one sentence")
    parser.add_argument("--found-in-stage", help="the stage that produced it")
    parser.add_argument("--found-in-artifact", help="the file it was seen in")
    parser.add_argument("--example", action="append", default=[],
                        help="a literal instance; repeatable")
    parser.add_argument("--ask-dir", help="re-use an existing ai/audit/<stamp>/")
    parser.add_argument("--replay", metavar="DIR",
                        help="adjudicate a recorded run offline")
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
        print("kz_audit --selftest%s" % (" %s" % fault if fault else ""))
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

    seed = load_seed(args.seed, args.finding, args.found_in_stage,
                     args.found_in_artifact, args.example)

    mode = "build"
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"
    elif args.replay:
        mode = "replay"

    report, ask_dir = run_audit(
        run_dir, seed, mode=mode, replay=args.replay, ask_dir=args.ask_dir,
        claude_bin=args.claude_bin, readonly_trees=args.readonly_tree,
        quiet=args.quiet)

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
        print("koreanize audit -- %s" % report["slug"])
        print("  seeds           : %d (%d ruled)"
              % (counts["seeds"], counts["seeds_ruled"]))
        print("  verdicts        : %d widen / %d isolated / %d abstain"
              % (counts["widen"], counts["isolated"], counts["abstain"]))
        for proposal in ((report.get("results") or {}).get("proposals") or []):
            if proposal["verdict"] == "widen":
                print("  -> %s: %s" % (proposal["checker_filename"],
                                       proposal["predicate"]))
        for check in report["checks"]:
            print("  %-22s: %s  %s" % (check["name"], check["status"],
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
