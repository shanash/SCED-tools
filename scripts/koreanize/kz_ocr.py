#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `ocr` (S5) -- the declared out-of-scope refusal (design §1.3, §4.1).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER`.

THIS STAGE REFUSES. THAT IS ITS WHOLE BEHAVIOUR, AND IT IS A DECISION
---------------------------------------------------------------------
§1.3's phasing table puts `(1,1)`-only and fan-id scenarios in the `out of scope`
row, with the reason stated in the same line: they

    "need an OCR front-end and a PDF-capable uploader that do not exist"

So `run_ocr()` raises `EXIT_PRECONDITION` (13) on every invocation, naming the
reason and the two scenario classes it covers, and no bundle is ever built, no
`claude` is ever invoked, and no report is ever written.

WHY A REFUSING MODULE RATHER THAN NO MODULE, AND WHY NOT A STUB
----------------------------------------------------------------
Three properties would break if this file did not exist, and all three are
asserted somewhere else in the tree rather than here:

  * `kz_config.AI_STAGE_MAP` maps `S5 -> ocr` and asserts itself total over
    `S1..S7` at import, so `ocr` is a real stage name whether or not it runs.
  * `koreanize.sh` dispatches `ocr` to `kz_ocr.py:art`. A dispatched name with no
    module is a shell-level "no such file", which is an accident's diagnostic, not
    a decision's.
  * `test_koreanize_gates.py` names `kz_ocr` in the art-tier set and requires
    `PREDECESSORS["ocr"] == ()` with nothing waiting on it.

And a STUB is the one thing it must not be. A stage that returned zero rulings, or
an empty report with `exit_code: 0`, would be `consumable` by §3.6's formula and
would read downstream as "the OCR pass ran and found nothing to do" -- which is
precisely the silent-English defect class this design refuses everywhere else. A
refusal is louder than an empty success and it is the honest one: exit 13 sends an
operator to a named precondition, and `--status` shows the stage as never having
run rather than as having passed.

THE AI CONTRACT IS STILL DECLARED, AND DELIBERATELY SO
-------------------------------------------------------
`ocr` is one of the seven required-AI stages in `kz_config.STAGES`, so this module
carries `kc.declare_ai("ocr", required=True)` in its MODULE BODY exactly as the
six live ones do (§3.2). Registering it is what makes a `scenario.json` that
demotes `ocr` out of `ai.required_stages` refuse at exit 4 the moment it is bound.
Dropping the declaration "because the stage does not run" would quietly re-open
the degradation the HARD CONSTRAINT forbids, for the one stage nobody is watching.

THE SCHEMA IS PINNED ELSEWHERE AND IS ASSERTED HERE
-----------------------------------------------------
`kz_decide.py` already owns S5's verdict enum (`read | illegible | abstain`), its
`value` sub-schema (`{fields{}, per_field_confidence{}}`, both required), its
evidence rule and its novel verdict. Whoever builds the OCR front-end satisfies
that contract rather than choosing one. `--selftest` asserts the pin so that a
later edit to `kz_decide.py` that quietly re-specifies S5 is caught by the module
that would have had to implement it, and `prompts/S5-ocr.md` states the same
contract in the form the agent would receive.

Exit codes (§4.2):
   0  --selftest passed (the only way this module returns 0)
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
  13  precondition -- OUT OF SCOPE: no OCR front-end exists (every real invocation)
  67  --selftest found a finding
"""

import argparse
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402
import kz_decide as kd  # noqa: E402

STAGE = "ocr"
SID = "S5"

# Declared in the MODULE BODY (§3.2), exactly as the six live required-AI stages
# do. See the header: a stage that refuses is still a stage, and demoting it out
# of ai.required_stages must still refuse at exit 4.
kc.declare_ai(STAGE, required=True)

#: The refusal's one-line reason. A literal rather than an f-string at the raise
#: site, because --selftest asserts the message NAMES the reason -- and a test
#: that re-derives the string it is checking is a test of nothing.
REASON = ("the `ocr` stage is out of scope for v1: no OCR front-end exists")

#: The detail. It names both scenario classes §1.3 puts in the row, so an operator
#: who reached this refusal learns which of their scenarios triggered it and what
#: would have to be built, not merely that something is unimplemented.
DETAIL = (
    "`(1,1)`-only atlases and fan-id scenarios need an OCR front-end and a "
    "PDF-capable uploader that do not exist (design §1.3, `out of scope`). "
    "This is a DECLARED refusal, not an unimplemented branch: the stage is "
    "registered in kz_config.AI_STAGE_MAP (S5 -> ocr), its manifest schema is "
    "pinned in kz_decide.py, and its prompt is prompts/S5-ocr.md -- so the S-id "
    "is documented rather than being a hole. Nothing was read and nothing was "
    "written. `ocr` is never a predecessor of any stage, so no other stage is "
    "blocked by this."
)

FAULTS = ("refusal", "contract", "schema-pin", "cli")


# ---------------------------------------------------------------------------
# 1. The refusal -- the whole of the stage
# ---------------------------------------------------------------------------

def run_ocr(run_dir, mode="build", **_ignored):
    """Refuse at exit 13, naming the reason. Never returns.

    The invocation guards run FIRST and the refusal second, so the exit codes
    stay in the order they describe: 2 is a fact about how the tool was invoked
    and 13 is a fact about what it was asked to do. A run started under launchd,
    or inside the nightly's scratch worktree, is wrong for reasons that have
    nothing to do with OCR and must say so.

    `**_ignored` absorbs the standard stage keywords (`replay`, `ask_dir`,
    `claude_bin`, `readonly_trees`, `quiet`, ...) so a caller written against the
    other stages' signature reaches THIS refusal rather than a TypeError, which
    would be exit 1 -- "a defect in the tool" -- for a stage that is behaving
    exactly as designed.
    """
    kc.check_invocation_guards([run_dir] if run_dir else [])
    kc.refuse(kc.EXIT_PRECONDITION, REASON, DETAIL)


# ---------------------------------------------------------------------------
# 2. The pinned contract, restated as assertions rather than as prose
# ---------------------------------------------------------------------------

def declared_contract():
    """What `kz_decide.py` will enforce for S5, read from `kz_decide.py`.

    Read rather than duplicated: a second literal here would be a second source
    of truth for the one schema §3.7 says has exactly one owner, and the day the
    two disagree the prompt would be describing a contract nothing enforces.
    """
    return {
        "stage": kz.AI_STAGE_MAP[SID],
        "unit": "object",
        "verdicts": kd.VERDICTS[SID],
        "novel_verdicts": kd.NOVEL_VERDICTS[SID],
        "value_required": kd.VALUE_REQUIRED[SID],
        "value_properties": tuple(sorted(kd.VALUE_SCHEMA[SID]["properties"])),
        "evidence_rules": kd.EVIDENCE_RULES.get(SID, {}),
    }


def prompt_path():
    """`prompts/S5-ocr.md` -- the prompt that exists so the S-id is not a hole."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "prompts", "%s-%s.md" % (SID, STAGE))


# ---------------------------------------------------------------------------
# 3. --selftest -- a refusal with no test is a refusal nobody has run
# ---------------------------------------------------------------------------

def selftest(fault=None, verbose=True):
    """Prove the refusal fires, carries exit 13, and names its reason."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    if "refusal" in wanted:
        # The refusal itself: it must FIRE, it must carry 13, and it must NAME
        # the reason. All three, because a refusal at the wrong code sends an
        # operator to the wrong table and a refusal with an empty message sends
        # them nowhere at all.
        try:
            run_ocr(os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", "selftest"))
            findings.append("refusal: run_ocr returned instead of refusing")
        except kc.KzRefusal as exc:
            if exc.code != kc.EXIT_PRECONDITION:
                findings.append("refusal: expected exit %d, got %d"
                                % (kc.EXIT_PRECONDITION, exc.code))
            if "out of scope" not in exc.message:
                findings.append("refusal: the message does not name the reason "
                                "(%r)" % exc.message)
            for phrase in ("OCR front-end", "(1,1)", "fan-id"):
                if phrase not in (exc.detail or ""):
                    findings.append("refusal: the detail does not name %r, so an "
                                    "operator cannot tell which scenario class "
                                    "tripped it" % phrase)
            if "§1.3" not in (exc.detail or ""):
                findings.append("refusal: the detail cites no design section")
        # A caller written against the other stages' keyword signature must reach
        # the refusal, not a TypeError -- which would surface as exit 1, "a defect
        # in the tool", for a module doing exactly what it was designed to do.
        try:
            run_ocr(None, mode="dry-run", replay=None, ask_dir=None,
                    claude_bin=None, readonly_trees=[], quiet=True)
            findings.append("refusal: the standard stage signature returned")
        except kc.KzRefusal as exc:
            if exc.code != kc.EXIT_PRECONDITION:
                findings.append("refusal: the standard stage signature refused at "
                                "%d, not %d" % (exc.code, kc.EXIT_PRECONDITION))
        except TypeError as exc:
            findings.append("refusal: the standard stage signature raised "
                            "TypeError (%s), which would be exit 1" % exc)

    if "contract" in wanted:
        # `ocr` is a real, required-AI, positionless stage. Each of these is
        # asserted somewhere else too; asserted HERE they are what stops the
        # module from being deleted as dead code without the map noticing.
        if STAGE not in kz.STAGES:
            findings.append("contract: %r is not in kz_config.STAGES" % STAGE)
        if kz.AI_STAGE_MAP.get(SID) != STAGE:
            findings.append("contract: AI_STAGE_MAP[%r] is %r, not %r"
                            % (SID, kz.AI_STAGE_MAP.get(SID), STAGE))
        if kc.AI_CONTRACT.get(STAGE) is not True:
            findings.append("contract: declare_ai did not register %r as a "
                            "required-AI stage (%r)"
                            % (STAGE, kc.AI_CONTRACT.get(STAGE)))
        if kz.PREDECESSORS.get(STAGE) != ():
            findings.append("contract: %r has predecessors (%r); it is "
                            "positionless" % (STAGE, kz.PREDECESSORS.get(STAGE)))
        waiters = sorted(s for s, preds in kz.PREDECESSORS.items()
                         if STAGE in preds)
        if waiters:
            findings.append("contract: %s wait on %r, so this refusal would "
                            "block them" % (waiters, STAGE))

    if "schema-pin" in wanted:
        # The schema is pinned for whoever builds the front-end. It is read from
        # kz_decide.py rather than restated, so this asserts the PIN and not a
        # copy of it -- an edit there that re-specifies S5 fails here.
        contract = declared_contract()
        if contract["verdicts"] != ("read", "illegible", "abstain"):
            findings.append("schema-pin: the S5 verdict enum moved (%r)"
                            % (contract["verdicts"],))
        if contract["novel_verdicts"] != ("read",):
            findings.append("schema-pin: S5's novel verdict moved (%r) -- rule 5 "
                            "is what holds an OCR reading to `high`"
                            % (contract["novel_verdicts"],))
        if set(contract["value_required"]) != {"fields", "per_field_confidence"}:
            findings.append("schema-pin: S5's required value keys moved (%r); "
                            "per_field_confidence is the only place a partial "
                            "reading can be stated honestly"
                            % (contract["value_required"],))
        if set(contract["value_required"]) - set(contract["value_properties"]):
            findings.append("schema-pin: a required value key is not in the value "
                            "schema, so it can never be satisfied")
        if not contract["evidence_rules"].get("read", {}).get("min"):
            findings.append("schema-pin: a `read` no longer has to cite the slice "
                            "it read")
        path = prompt_path()
        if not os.path.exists(path):
            findings.append("schema-pin: %s is missing, so S5 is an undocumented "
                            "hole in AI_STAGE_MAP" % path)
        else:
            with open(path, "r", encoding="utf-8") as handle:
                head = handle.read(2048)
            if "OUT OF SCOPE" not in head:
                findings.append("schema-pin: %s does not say at the top that the "
                                "stage is out of scope" % path)
            if "13" not in head:
                findings.append("schema-pin: %s does not name the exit code the "
                                "stage refuses with" % path)

    if "cli" in wanted:
        # The CLI surface reaches the refusal rather than a usage error, because
        # `--slug midwinter` is a well-formed request and the answer to it is 13,
        # not 2. An invocation with neither --run-dir nor --slug is a usage error.
        try:
            main(["--slug", "selftest"])
            findings.append("cli: --slug returned instead of refusing")
        except kc.KzRefusal as exc:
            if exc.code != kc.EXIT_PRECONDITION:
                findings.append("cli: --slug refused at %d, not %d"
                                % (exc.code, kc.EXIT_PRECONDITION))
        try:
            # argparse prints its help to stdout; captured so the selftest's own
            # output stays readable rather than burying four `ok:` lines under a
            # usage block.
            with contextlib.redirect_stdout(io.StringIO()):
                code = main([])
            if code != kc.EXIT_USAGE:
                findings.append("cli: a bare invocation returned %r, not the "
                                "usage code %d" % (code, kc.EXIT_USAGE))
        except kc.KzRefusal as exc:
            findings.append("cli: a bare invocation refused at %d instead of "
                            "printing help" % exc.code)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 4. CLI
# ---------------------------------------------------------------------------

_SUMMARY = {
    "refusal": "run_ocr refuses at exit 13, the message names the reason and the "
               "detail names both scenario classes and the design section; the "
               "standard stage signature reaches the refusal rather than a "
               "TypeError",
    "contract": "`ocr` is a real stage, declare_ai registered it as required-AI, "
                "and it is positionless -- nothing waits on this refusal",
    "schema-pin": "S5's verdict enum, novel verdict, required value keys and "
                  "evidence rule are read from kz_decide.py and still say what "
                  "prompts/S5-ocr.md documents",
    "cli": "--slug reaches the refusal at 13; a bare invocation prints help and "
           "returns the usage code",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_ocr.py",
        description="koreanize stage S5 -- OUT OF SCOPE for v1. Refuses at exit "
                    "13: (1,1)-only and fan-id scenarios need an OCR front-end "
                    "that does not exist (design §1.3).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--ask-dir", help="re-use an existing ai/ocr/<stamp>/")
    parser.add_argument("--replay", metavar="DIR",
                        help="adjudicate a recorded run offline: no claude, no "
                             "credential, no cost")
    parser.add_argument("--readonly-tree", action="append", default=[],
                        help="a tree the agent must not move; repeatable (rule 8c)")
    parser.add_argument("--claude-bin", help="override the shim's binary resolution")
    parser.add_argument("--dry-run", action="store_true",
                        help="write <run_dir>/dry-run/ocr/ocr.dry-run.json")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--contract", action="store_true",
                        help="print the S5 manifest contract kz_decide.py pins, "
                             "and exit 0 -- the one read-only thing this module "
                             "can answer")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_ocr --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-11s %s" % (name, _SUMMARY[name]))
        return kc.EXIT_OK

    if args.contract:
        contract = declared_contract()
        print("koreanize %s (%s) -- OUT OF SCOPE for v1; every run refuses at "
              "exit %d" % (SID, STAGE, kc.EXIT_PRECONDITION))
        print("  unit            : %s" % contract["unit"])
        print("  verdicts        : %s" % (contract["verdicts"],))
        print("  novel verdicts  : %s" % (contract["novel_verdicts"],))
        print("  value required  : %s" % (contract["value_required"],))
        print("  value properties: %s" % (contract["value_properties"],))
        print("  prompt          : %s" % prompt_path())
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

    # Never returns.
    run_ocr(run_dir, mode=mode, replay=args.replay, ask_dir=args.ask_dir,
            claude_bin=args.claude_bin, readonly_trees=args.readonly_tree,
            quiet=args.quiet)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
