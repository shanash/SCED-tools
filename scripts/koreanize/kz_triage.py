#!/usr/bin/env python3
"""koreanize `triage` (S6) -- real defect, or ruled tolerance? (design §5.2, §9.1)

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER`.

A stage in its own right, and callable by ANY failing gate -- which is what makes
it worth being a stage rather than a branch inside one. Its universe is the
failing gate's `checks[].detail[]`, one unit per finding; its verdict enum is
`defect | tolerance | abstain`; its `value` is `{owner_stage, suggested_action}`;
and its `universe_size` is `null`, because a gate-invoked stage cannot know its
universe until it is invoked (§3.7). The ceiling is therefore enforced by
`kz_ask.py` against the MEASURED universe, at exit 13.

It is what `kz_source.py` escalates an ambiguous donor to (§5.2 step 4): 0 donors
=> `manufacture`, 1 donor => `reuse`, and **>= 2 donors or a grid/back conflict**
=> here. At equal confidence across differing grids the choice is
tolerance-bearing -- `--accept-donor-choice` or exit **22**. It is deliberately
not 11: 11 is the AI-policy code and routes an operator to `decide.json`, a file
that has nothing to say about a tie between two donors.

The output is DATA and never a code edit (§9.5 rule 1): a verdict, a confidence,
an owning stage and one suggested action per finding. A human or a later stage
acts on it.

Exit codes (§4.2):
   0  every finding ruled, every rule passed
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin or its AI contract
  11  stop by policy -- the ten rules stopped this run
  13  precondition -- no gate report, no failing finding, a universe over the
      batch ceiling, or a bundle input that is missing
  22  a donor choice at differing grids and `--accept-donor-choice` was not given
  25  the stage stopped between batches on its budget; N of M are on disk
  65  claude unavailable / unauthenticated / timed out
  66  AI manifest invalid -- schema, coverage, or run binding
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402
import kz_ask as ka  # noqa: E402
import kz_decide as kd  # noqa: E402

STAGE = "triage"
SID = "S6"

# Declared in the MODULE BODY (§3.2). This is what makes the AI contract
# mechanized rather than documented: a scenario.json that does not list `triage`
# in ai.required_stages refuses at exit 4 the moment it is bound, and
# `kz_common.write_report` refuses a build report with `ai: null` at exit 11.
kc.declare_ai(STAGE, required=True)

TOLERANCE = "donor-choice"
FAULTS = ("donor-choice", "gate-universe", "empty-universe")


# ---------------------------------------------------------------------------
# 1. The universe -- the failing gate's checks[].detail[]
# ---------------------------------------------------------------------------

def universe_from_gate(gate_report):
    """(universe[], units{}, identity{}) from a stage report's failing checks.

    A detail entry is either a plain string -- what most gates emit -- or a dict,
    which is how a gate hands over structured escalation material (`kz_source.py`'s
    donor ambiguity is the v0 case). Both are accepted; a string simply carries no
    identity and no `kind`.

    A PASSING check contributes nothing. Triage adjudicates findings, and a check
    that did not fire produced none.
    """
    if not isinstance(gate_report, dict):
        kc.refuse(kc.EXIT_PRECONDITION, "the gate report is not a JSON object")
    stage = gate_report.get("stage")
    if not stage:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the gate report declares no stage",
                  "triage is invoked BY a stage; its unit ids are keyed on that name")
    universe, units, identity = [], {}, {}
    for check in gate_report.get("checks") or []:
        if not isinstance(check, dict) or check.get("status") == "pass":
            continue
        check_id = check.get("id") or check.get("name") or "?"
        for index, detail in enumerate(check.get("detail") or []):
            if isinstance(detail, dict):
                key = str(detail.get("key") or detail.get("face")
                          or detail.get("unit") or index)
                payload = dict(detail)
                record = dict((k, detail[k]) for k in kd.IDENTITY_KEYS if k in detail)
            else:
                key = str(index)
                payload = {"detail": str(detail)}
                record = {}
            payload.setdefault("check", check_id)
            payload.setdefault("check_name", check.get("name"))
            payload.setdefault("gate_stage", stage)
            unit_id = "%s:%s:%s" % (stage, check_id, key)
            if unit_id in units:
                kc.refuse(kc.EXIT_PRECONDITION,
                          "the gate report yields the unit id %r twice" % unit_id,
                          "a finding key must identify one finding; give the detail "
                          "entries distinct `key` fields")
            universe.append(unit_id)
            units[unit_id] = payload
            if record:
                identity[unit_id] = record
    if not universe:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s reports no failing finding" % stage,
                  "triage is gate-invoked; a gate that passed has nothing to triage")
    return universe, units, identity


# ---------------------------------------------------------------------------
# 2. The donor-choice tolerance (§5.2 step 4, kz_common.TOLERANCES)
# ---------------------------------------------------------------------------

def donor_choice_check(units, effective, accepted):
    """The one tolerance-bearing predicate this module owns.

    Two donors at two grids for one id is not a defect and not an AI-policy stop:
    §3.5 makes both renderings equally correct, so choosing between them is a
    human authorisation of a tie, and the code that says so is 22.
    """
    fired = []
    for unit_id, payload in sorted(units.items()):
        if payload.get("kind") != "donor_choice":
            continue
        if not payload.get("differing_grid"):
            continue
        ruling = (effective.get(unit_id) or {}).get("ruling") or {}
        fired.append("%s: %s chose %s across differing grids"
                     % (unit_id, ruling.get("verdict") or "(no ruling)",
                        (ruling.get("value") or {}).get("suggested_action")
                        or "(no action)"))
    ok = (not fired) or bool(accepted)
    return {
        "id": "TR1",
        "name": "donor_choice",
        "status": "pass" if ok else "fail",
        "tolerance": TOLERANCE,
        "exit_on_fail": kc.EXIT_TOLERANCE,
        "detail": fired,
    }


# ---------------------------------------------------------------------------
# 3. The stage
# ---------------------------------------------------------------------------

def _material_for(cfg, gate_path, run_dir, workspace=None):
    """Everything the agent is given, COPIED. It holds no repository path.

    The lock is included whenever it exists because it is the record of what a
    human has already ruled a tolerance -- the prompt's `tolerance` verdict is
    defined against it, and rule 4's provenance clause requires it to be cited.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    material = {os.path.basename(gate_path): gate_path}
    lock = os.path.join(workspace, cfg["guard"]["data_root"], "locks",
                        "%s.lock.json" % cfg["slug"])
    if os.path.exists(lock):
        material["lock.json"] = lock
    return material


def run_triage(run_dir, gate_report_path, mode="build", accept_donor_choice=False,
               replay=None, ask_dir=None, claude_bin=None, workspace=None,
               readonly_trees=(), quiet=False):
    """PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)          -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds
                                                                   -> exit 4 / 13
      3. the gate report exists and is a stage report               -> exit 13
      4. it carries at least one failing check with at least one detail
                                                                   -> exit 13
      5. the measured universe is inside ai.batch.S6's ceiling      -> exit 13
      6. the ask bundle's policy, prompt and schema are on disk     -> exit 13
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    scenario_path = os.path.join(run_dir, "scenario.json")
    cfg = kz.load_scenario(scenario_path)

    gate, err = kd._read_json(gate_report_path)
    if err:
        kc.refuse(kc.EXIT_PRECONDITION, "the gate report is unreadable", err)
    universe, units, identity = universe_from_gate(gate)

    material = _material_for(cfg, gate_report_path, run_dir, workspace)

    if ask_dir is None:
        ask_dir = ka.build_bundle(cfg, SID, universe, units, identity=identity,
                                  material=material, workspace=workspace)
    if replay:
        ka.seed_from_replay(ask_dir, replay)

    ask = ka.run_bundle(ask_dir, invoke=not replay, claude_bin=claude_bin,
                        readonly_trees=readonly_trees, workspace=workspace,
                        quiet=quiet)

    triggered = []
    checks = []
    decide_report = None
    decide_code = kc.EXIT_OK
    effective = {}

    if ask["exit_code"] != kc.EXIT_OK:
        # 25: stopped BETWEEN batches. The completed batches are on disk and
        # re-running the stage resumes at the first missing one (§3.7).
        triggered.append(ask["exit_code"])
        checks.append({"id": "AI0", "name": "batches_complete", "status": "fail",
                       "exit_on_fail": ask["exit_code"],
                       "detail": ["%s; %d of %d batches complete, resume at %s"
                                  % (ask["stopped_by"] or "incomplete",
                                     sum(1 for b in ask["batches"] if b["complete"]),
                                     ask["batch_of"], ask["resume_from"])]})
    else:
        checks.append({"id": "AI0", "name": "batches_complete", "status": "pass",
                       "exit_on_fail": kc.EXIT_AI_BUDGET,
                       "detail": []})
        decide_report, decide_code = kd.decide(ask_dir)
        if decide_code != kc.EXIT_OK:
            triggered.append(decide_code)
        checks.append({"id": "AI1", "name": "decide", "status":
                       "pass" if decide_code == kc.EXIT_OK else "fail",
                       "exit_on_fail": decide_code or kc.EXIT_POLICY,
                       "detail": ([] if decide_code == kc.EXIT_OK
                                  else [decide_report["reason"]])})
        merged, err = kd._read_json(os.path.join(ask_dir, "manifest.merged.json"))
        if not err:
            for ruling in merged.get("rulings") or []:
                effective[ruling.get("unit_id")] = {"ruling": ruling}

    tolerance_check = donor_choice_check(units, effective, accept_donor_choice)
    checks.append(tolerance_check)
    if tolerance_check["status"] == "fail":
        triggered.append(kc.EXIT_TOLERANCE)

    verdicts = {}
    for entry in effective.values():
        name = entry["ruling"].get("verdict")
        verdicts[name] = verdicts.get(name, 0) + 1
    counts = {
        "findings": len(universe),
        "findings_ruled": len(effective),
        "defect": verdicts.get("defect", 0),
        "tolerance": verdicts.get("tolerance", 0),
        "abstain": verdicts.get("abstain", 0),
        "donor_choices": sum(1 for p in units.values()
                             if p.get("kind") == "donor_choice"),
        "donor_choices_differing_grid": sum(
            1 for p in units.values()
            if p.get("kind") == "donor_choice" and p.get("differing_grid")),
        "batches": ask["batch_of"],
        "batches_complete": sum(1 for b in ask["batches"] if b["complete"]),
    }

    ai_block = {
        "used": True,
        "stage_id": SID,
        "batches": ask["batch_of"],
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
    if decide_report is not None and effective:
        results = {
            "rulings": [{
                "unit_id": unit_id,
                "verdict": entry["ruling"].get("verdict"),
                "confidence": entry["ruling"].get("confidence"),
                "owner_stage": (entry["ruling"].get("value") or {}).get("owner_stage"),
                "suggested_action": (entry["ruling"].get("value") or {}).get(
                    "suggested_action"),
                "rationale": entry["ruling"].get("rationale"),
            } for unit_id, entry in sorted(effective.items())],
            "by_owner_stage": _by_owner(effective),
        }

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding([scenario_path, gate_report_path],
                                 extra={"ask/inputs.json":
                                        kc.sha256_file(os.path.join(ask_dir,
                                                                    "inputs.json"))}),
        freshness=kc.build_freshness([scenario_path, gate_report_path]),
        ai=ai_block,
        accepted={TOLERANCE: bool(accept_donor_choice)},
        results=results)
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    # The house rule: on the PRECONDITION path every result section is written as
    # literal null, never {} or [].
    if report["verdict"] == "PRECONDITION":
        report["results"] = None
    return report, ask_dir


def _by_owner(effective):
    out = {}
    for entry in effective.values():
        owner = (entry["ruling"].get("value") or {}).get("owner_stage")
        out[str(owner)] = out.get(str(owner), 0) + 1
    return dict(sorted(out.items()))


def escalate(cfg, gate_report, run_dir, **kwargs):
    """The library entry point `kz_source.py` uses on an ambiguous donor.

    It writes the gate report into `<run_dir>/gates/` under its own stage name and
    then runs the stage, so an escalation leaves the same artefacts on disk as a
    hand-invoked one and `--status` can find it.
    """
    stage = gate_report.get("stage") or "source"
    path = os.path.join(run_dir, "gates", "%s-findings.json" % stage)
    kc.atomic_write_json(path, gate_report)
    return run_triage(run_dir, path, **kwargs)


# ---------------------------------------------------------------------------
# 4. --selftest
# ---------------------------------------------------------------------------

def _donor_gate():
    """The escalation `kz_source.py` produces: one id, two donors, two grids."""
    return {
        "schema_version": kc.SCHEMA_VERSION, "stage": "source", "verdict": "FAIL",
        "checks": [{"id": "D2", "name": "donor_ambiguity", "status": "fail",
                    "exit_on_fail": kc.EXIT_TOLERANCE,
                    "detail": [{
                        "key": "71033-a",
                        "kind": "donor_choice",
                        "differing_grid": True,
                        "equal_confidence": True,
                        "arkham_id": "71033",
                        "guid": "4a2568",
                        "detail": "two Korean donors for 71033: "
                                  "Korean-PlayerCards (8x5) and Korean-Campaigns (4x4)",
                        "donors": [
                            {"pack": "Korean - Player Cards", "num_width": 8,
                             "num_height": 5},
                            {"pack": "Korean - Campaigns", "num_width": 4,
                             "num_height": 4}]}]}],
    }


def selftest(fault=None, verbose=True):
    """Prove each refusal and the one tolerance fire on the fault they target."""
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

    if "gate-universe" in wanted:
        gate = {"stage": "typeset", "checks": [
            {"id": "T1", "name": "ok", "status": "pass", "detail": []},
            {"id": "T3", "name": "clear", "status": "fail",
             "detail": ["face A overflows", "face B overflows"]},
            {"id": "T4", "name": "structured", "status": "fail",
             "detail": [{"key": "71006-front", "arkham_id": "71006", "cell": 6,
                         "detail": "structured finding"}]}]}
        universe, units, identity = universe_from_gate(gate)
        if universe != ["typeset:T3:0", "typeset:T3:1", "typeset:T4:71006-front"]:
            findings.append("gate-universe: unexpected universe %s" % universe)
        if identity.get("typeset:T4:71006-front") != {"arkham_id": "71006", "cell": 6}:
            findings.append("gate-universe: the structured detail's identity was "
                            "not carried")
        if units["typeset:T3:0"]["gate_stage"] != "typeset":
            findings.append("gate-universe: the owning gate stage was not recorded")
        # A passing check contributes nothing, and a duplicate key is a refusal.
        fires("gate-universe (duplicate finding key)", kc.EXIT_PRECONDITION,
              lambda: universe_from_gate(
                  {"stage": "typeset",
                   "checks": [{"id": "T3", "status": "fail",
                               "detail": [{"key": "x"}, {"key": "x"}]}]}))

    if "empty-universe" in wanted:
        fires("empty-universe (every check passed)", kc.EXIT_PRECONDITION,
              lambda: universe_from_gate(
                  {"stage": "typeset",
                   "checks": [{"id": "T1", "status": "pass", "detail": []}]}))
        fires("empty-universe (no stage)", kc.EXIT_PRECONDITION,
              lambda: universe_from_gate({"checks": []}))

    if "donor-choice" in wanted:
        # The TOLERANCES row's named fault: "two donors at two grids for one id".
        universe, units, _identity = universe_from_gate(_donor_gate())
        effective = {universe[0]: {"ruling": {
            "unit_id": universe[0], "verdict": "defect", "confidence": "high",
            "value": {"owner_stage": "source",
                      "suggested_action": "adopt the Korean - Campaigns donor"}}}}
        check = donor_choice_check(units, effective, accepted=False)
        if check["status"] != "fail":
            findings.append("donor-choice: the tie did not fire")
        if check["exit_on_fail"] != kc.EXIT_TOLERANCE:
            findings.append("donor-choice: fired with exit %d, expected 22"
                            % check["exit_on_fail"])
        if kc.pick_exit([check["exit_on_fail"]]) != kc.EXIT_TOLERANCE:
            findings.append("donor-choice: pick_exit did not return 22")
        row = kc.row_by_flag("--accept-%s" % TOLERANCE)
        if row is None or row.exit_code != kc.EXIT_TOLERANCE:
            findings.append("donor-choice: kz_common.TOLERANCES has no matching row")
        if row is not None and row.module != "kz_triage.py":
            findings.append("donor-choice: the TOLERANCES row names %s as its owner"
                            % row.module)
        accepted_check = donor_choice_check(units, effective, accepted=True)
        if accepted_check["status"] != "pass":
            findings.append("donor-choice: --accept-donor-choice did not accept it")
        # A donor choice at the SAME grid is not tolerance-bearing at all.
        same = dict((k, dict(v, differing_grid=False)) for k, v in units.items())
        if donor_choice_check(same, effective, accepted=False)["status"] != "pass":
            findings.append("donor-choice: a same-grid choice fired")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_triage.py",
        description="koreanize stage S6 -- adjudicate a failing gate's findings as "
                    "defect or tolerance (design §5.2, §9.1).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--gate-report", help="the failing stage report to triage")
    parser.add_argument("--ask-dir", help="re-use an existing ai/triage/<stamp>/ "
                                          "instead of building one")
    parser.add_argument("--replay", metavar="DIR",
                        help="seed the bundle from a recorded run and adjudicate it "
                             "offline: no claude, no credential, no cost")
    parser.add_argument("--accept-donor-choice", action="store_true",
                        help="authorize a donor choice across differing grids "
                             "(kz_common.TOLERANCES; without it, exit 22)")
    parser.add_argument("--readonly-tree", action="append", default=[],
                        help="a tree the agent must not move; repeatable (rule 8c)")
    parser.add_argument("--claude-bin", help="override the shim's binary resolution")
    parser.add_argument("--dry-run", action="store_true",
                        help="write <run_dir>/dry-run/triage/triage.dry-run.json")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-adjudicate an existing --ask-dir; writes "
                             "triage.verify.json, never the marker")
    parser.add_argument("--json-only", action="store_true",
                        help="emit the report JSON on stdout and nothing else")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_triage --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        summary = {
            "gate-universe": "the universe is the failing checks' detail[], the "
                             "structured form carries identity, a duplicate key "
                             "refuses",
            "empty-universe": "a gate with nothing failing refuses at 13",
            "donor-choice": "two donors at two grids fire the tolerance at exit 22 "
                            "and --accept-donor-choice accepts it",
        }
        if fault:
            print("  ok: %s" % summary[fault])
        else:
            for name in FAULTS:
                print("  ok: %-15s %s" % (name, summary[name]))
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir or not args.gate_report:
        parser.print_help()
        return kc.EXIT_USAGE

    mode = "build"
    if args.verify_only:
        mode = "verify-only"
    elif args.dry_run:
        mode = "dry-run"
    elif args.replay:
        mode = "replay"

    report, ask_dir = run_triage(
        run_dir, args.gate_report, mode=mode,
        accept_donor_choice=args.accept_donor_choice, replay=args.replay,
        ask_dir=args.ask_dir, claude_bin=args.claude_bin,
        readonly_trees=args.readonly_tree, quiet=args.quiet)

    dest = run_dir
    if args.dry_run:
        # §4.1: every stage's --dry-run output goes to <run_dir>/dry-run/<stage>/,
        # and kz_config asserts the destination is under <run_dir> before the
        # first byte.
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
        print("koreanize triage -- %s" % report["slug"])
        print("  findings        : %d (%d ruled)"
              % (counts["findings"], counts["findings_ruled"]))
        print("  verdicts        : %d defect / %d tolerance / %d abstain"
              % (counts["defect"], counts["tolerance"], counts["abstain"]))
        print("  batches         : %d of %d complete"
              % (counts["batches_complete"], counts["batches"]))
        print("  ai              : %s (%.2f USD, %s)"
              % (report["ai"]["decide_outcome"], report["ai"]["cost_usd"],
                 report["ai"]["ask_dir"]))
        for check in report["checks"]:
            print("  %-16s: %s  %s" % (check["name"], check["status"],
                                       "; ".join(check["detail"][:3])))
        print("  verdict         : %s (exit %d)"
              % (report["verdict"], report["exit_code"]))
        print("  consumable      : %s%s"
              % (report["consumable"],
                 "" if report["consumable"]
                 else " (%s)" % report["consumable_blocked_by"]))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
