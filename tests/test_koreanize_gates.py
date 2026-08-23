"""The pure functions and the cross-cutting tables (design §2 item 37).

Three subjects, and they are together because each is a property of the TOOL
rather than of any one stage:

  * `carry_review()`  -- lifted verbatim from `typeset-cards.py:2594-2642`, and
    the one function here whose bugs were paid for in shipped pixels
  * `pick_exit()`     -- the explicit precedence list that replaced `min()`
  * `GIT_READONLY`    -- §1.4's git allowlist, greppable over the change set

plus the dispatcher's own contract assertions (§1.2's predecessor table, the
`{"revert"}` exemption, the per-object disjuncts), which have no other home:
they are read by `koreanize.sh`, by `--status` and by `plan`, and by no stage.
"""

import ast
import os
import re

import pytest

import kz_common as kc
import kz_config as kz

KOREANIZE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts", "koreanize")


# ---------------------------------------------------------------------------
# 1. pick_exit -- aggregation is a PRECEDENCE LIST, not min()
# ---------------------------------------------------------------------------
#
# `composite-cleared.py:45-63`'s RATIONALE is kept -- "such an input does not
# merely coexist with an identity failure, it CAUSES it", and "telling an
# operator 'outside-mask identity failed' when the real answer is 'you handed it
# an L-mode inverted mask' would send them to the wrong file" -- but its
# MECHANISM does not survive the move. That script works only because every rule
# code it has (20/21/22) is above its precondition code 13. koreanize places 4
# and 11 BELOW 13 and 62/65/66/67 ABOVE 20/21/22, so min() names the symptom.


def test_pick_exit_returns_zero_on_an_empty_set():
    assert kc.pick_exit([]) == kc.EXIT_OK
    assert kc.pick_exit(()) == kc.EXIT_OK


@pytest.mark.parametrize("triggered,expected,why", [
    ([kc.EXIT_POLICY, kc.EXIT_AI_MANIFEST], kc.EXIT_AI_MANIFEST,
     "rule 2 (11) and rule 7 (66) together: min() would say 11, naming the "
     "symptom of an invalid manifest instead of the manifest"),
    ([kc.EXIT_RULE_A, kc.EXIT_AI_MANIFEST], kc.EXIT_AI_MANIFEST,
     "a stage rule (20) and an invalid manifest (66): min() would say 20"),
    ([kc.EXIT_GUARD, kc.EXIT_PRECONDITION], kc.EXIT_GUARD,
     "the guard refused before anything was read"),
    ([kc.EXIT_USAGE, kc.EXIT_GUARD], kc.EXIT_USAGE,
     "an invocation guard outranks a config guard"),
    ([kc.EXIT_TOLERANCE, kc.EXIT_CONSENT], kc.EXIT_CONSENT,
     "26 sits ABOVE 22: they answer different questions and the human one is "
     "named first"),
    ([kc.EXIT_MASK_RESIDUAL, kc.EXIT_RULE_A], kc.EXIT_MASK_RESIDUAL,
     "23 sits ABOVE 20/21 because the runbook move differs: compare against "
     "data/locks/<slug>.lock.json's residual_baseline[], not against a re-run"),
    ([kc.EXIT_AI_BUDGET, kc.EXIT_POLICY], kc.EXIT_AI_BUDGET,
     "stopping between batches is not an AI-policy stop and must not read as one"),
    ([kc.EXIT_NETWORK], kc.EXIT_NETWORK, "a single code passes through"),
])
def test_pick_exit_precedence(triggered, expected, why):
    assert kc.pick_exit(triggered) == expected, why
    assert kc.pick_exit(list(reversed(triggered))) == expected, \
        "%s -- and order of arrival must not change the answer" % why


def test_the_precedence_list_is_total_over_the_stage_codes():
    """`1` is never in the set -- it is what __main__ returns on an unhandled
    exception -- and the dispatcher band is deliberately excluded, because the
    dispatcher returns those INSTEAD of a stage's code rather than aggregating
    them with one."""
    listed = set(kc.EXIT_PRECEDENCE)
    assert 1 not in listed
    assert listed.isdisjoint(kc.DISPATCHER_BAND)
    assert len(kc.EXIT_PRECEDENCE) == len(listed), "no code appears twice"
    documented = set(kc.EXIT_MEANING) - {0, 1} - set(kc.DISPATCHER_BAND)
    assert documented <= listed, \
        "every documented stage code must be aggregable: %s" % sorted(
            documented - listed)


def test_the_dispatcher_band_is_disjoint_from_every_neighbouring_table():
    """{71,72,73,75} against sced-run-now.sh's {7,8,9} and the driver's."""
    assert kc.DISPATCHER_BAND == (71, 72, 73, 75)
    assert set(kc.DISPATCHER_BAND).isdisjoint({7, 8, 9})
    # 70 is deliberately NOT used: daily-sync-local.sh:109 defines it as "draft
    # release creation, asset upload, or go-live failed", and an operator reading
    # a 70 beside the nightly's would derive the wrong first move.
    assert 70 not in set(kc.EXIT_PRECEDENCE) | set(kc.DISPATCHER_BAND)
    assert kc.EXIT_NETWORK == 74


# ---------------------------------------------------------------------------
# 2. carry_review -- three branches, and the build that published unwalked pixels
# ---------------------------------------------------------------------------

WHEN = "2026-08-23T00:00:00Z"


def gate(status="pending", reviewer=None, date=None, superseded=None):
    return {"status": status, "reviewer": reviewer, "date": date,
            "superseded": superseded}


def test_branch_one_records_a_live_verdict_and_resets_to_pending():
    out = kc.carry_review(gate("accepted", "shanash", "2026-08-01"),
                          gate("accepted", "shanash", "2026-08-01"),
                          sha_moved=True, when=WHEN)
    assert out["status"] == "pending"
    assert out["reviewer"] is None and out["date"] is None
    assert out["superseded"]["status"] == "accepted"
    assert out["superseded"]["reviewer"] == "shanash"
    assert out["superseded"]["superseded_at"] == WHEN
    assert out["superseded"]["carried_forward"] is False


def test_branch_two_carries_the_record_forward_on_a_second_rebuild():
    """THE bug this function exists to prevent. The original inline form required
    the PREVIOUS report's status to be non-pending, so it was a ONE-SHOT: the
    first rebuild recorded the superseded verdict and left the report `pending`,
    and the SECOND rebuild -- reading that pending status -- silently dropped the
    record, taking the gate file's `## Superseded` section with it and erasing
    the audit trail of a human acceptance with no diagnostic anywhere."""
    first = kc.carry_review(gate("accepted", "shanash", "2026-08-01"),
                            gate("accepted", "shanash", "2026-08-01"),
                            sha_moved=True, when=WHEN)
    second = kc.carry_review(gate("pending"), first, sha_moved=True, when=WHEN)
    assert second["superseded"] is not None, "the record must survive rebuild 2"
    assert second["superseded"]["carried_forward"] is True
    third = kc.carry_review(gate("pending"), second, sha_moved=True, when=WHEN)
    assert third["superseded"] is not None, "and rebuild 3, and every one after"


def test_branch_three_retires_the_record_once_a_human_accepts():
    prior = kc.carry_review(gate("accepted", "shanash", "2026-08-01"),
                            gate("accepted", "shanash", "2026-08-01"),
                            sha_moved=True, when=WHEN)
    out = kc.carry_review(gate("accepted", "shanash", "2026-08-20"), prior,
                          sha_moved=False, when=WHEN)
    assert out["superseded"] is None


def test_the_live_verdict_is_the_gates_when_the_report_still_says_pending():
    """Fixed 2026-08-19. A human accepts by editing the GATE FILE and
    write_gate_stub() rewrites that file from the report -- so for exactly ONE
    build after an acceptance the gate says `accepted` while the report still
    says `pending`. Deciding branch 1 from prev_status alone missed that build
    entirely: branch 1 did not fire (report pending), branch 2 did not fire (gate
    accepted), and the else-branch retired the record while new["status"] stayed
    `accepted` -- inherited from prior_gate. The build then published
    `consumable: true` FOR PIXELS NOBODY HAD WALKED."""
    out = kc.carry_review(gate("accepted", "shanash", "2026-08-19"),
                          gate("pending"), sha_moved=True, when=WHEN)
    assert out["status"] == "pending", \
        "an accepted gate over moved pixels must NOT stay accepted"
    assert out["superseded"] is not None
    assert out["superseded"]["status"] == "accepted"


def test_sha_moved_is_a_veto_on_retiring_a_record():
    """"A verdict can never outlive the pixels it was given to.\""""
    prior = kc.carry_review(gate("accepted", "shanash", "2026-08-01"),
                            gate("accepted", "shanash", "2026-08-01"),
                            sha_moved=True, when=WHEN)
    out = kc.carry_review(gate("accepted", "shanash", "2026-08-20"), prior,
                          sha_moved=True, when=WHEN)
    assert out["superseded"] is not None


def test_carry_review_is_pure():
    """"PURE -- takes dicts, returns a dict, touches no file." The arity is what
    this table test is parametrized on, so it is asserted rather than assumed."""
    prior, prev = gate("accepted", "a", "d"), gate("pending")
    before = (dict(prior), dict(prev))
    out = kc.carry_review(prior, prev, True, WHEN)
    assert (prior, prev) == before, "the inputs must not be mutated"
    assert out is not prior


@pytest.mark.parametrize("sha_moved", [True, False])
@pytest.mark.parametrize("prior_status", ["pending", "accepted"])
@pytest.mark.parametrize("prev_status", [None, "pending", "accepted"])
def test_carry_review_never_leaves_an_accepted_status_over_moved_shas(
        sha_moved, prior_status, prev_status):
    prev = None if prev_status is None else gate(prev_status)
    out = kc.carry_review(gate(prior_status, "shanash", "2026-08-01"), prev,
                          sha_moved, WHEN)
    if sha_moved and "accepted" in (prior_status, prev_status):
        assert out["status"] == "pending"


# ---------------------------------------------------------------------------
# 3. GIT_READONLY -- §1.4's allowlist, greppable over the change set
# ---------------------------------------------------------------------------
#
# "No git operations" was the earlier formulation of this rule and it is FALSE as
# stated: four requirements run git, one of them a v0 acceptance predicate. The
# rule is therefore an ALLOWLIST, so that it is grep-checkable rather than
# aspirational.

MUTATING_VERBS = ("add", "commit", "checkout", "clean", "reset", "fetch",
                  "push", "stash", "rebase", "merge", "tag", "cherry-pick")


def test_the_allowlist_is_exactly_the_four_read_only_verbs():
    assert kc.GIT_READONLY == frozenset({"rev-parse", "diff", "status",
                                         "ls-files"})
    assert set(MUTATING_VERBS).isdisjoint(kc.GIT_READONLY)


@pytest.mark.parametrize("verb", MUTATING_VERBS)
def test_the_wrapper_refuses_every_mutating_verb_at_exit_two(verb):
    with pytest.raises(kc.KzRefusal) as excinfo:
        kc.git(verb, ["--any"], cwd=KOREANIZE_DIR)
    assert excinfo.value.code == kc.EXIT_USAGE


def test_the_wrapper_admits_a_read_only_verb():
    proc = kc.git("rev-parse", ["--is-inside-work-tree"], cwd=KOREANIZE_DIR)
    assert proc.returncode in (0, 128)   # 128 = not a repo, still not a refusal


def _koreanize_sources():
    for name in sorted(os.listdir(KOREANIZE_DIR)):
        if name.endswith(".py"):
            yield name, os.path.join(KOREANIZE_DIR, name)


def test_no_module_invokes_git_outside_the_wrapper():
    """The grep §1.4 asks for, as an AST walk rather than a regex: every `git`
    invocation must go through `kz_common.git` / `kz_common.git_stdout`, which
    assert membership and refuse at exit 2 otherwise.

    An AST walk and not a regex because the thing being looked for is a CALL --
    `subprocess.run(["git", ...])` -- and a regex over the word `git` matches
    every docstring in the package, including this rule's own statement of
    itself.
    """
    offenders = []
    for name, path in _koreanize_sources():
        with open(path, "rb") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            args = list(node.args)
            for arg in args:
                literals = []
                if isinstance(arg, (ast.List, ast.Tuple)):
                    literals = [e for e in arg.elts
                                if isinstance(e, ast.Constant)
                                and isinstance(e.value, str)]
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    literals = [arg]
                for literal in literals:
                    value = literal.value
                    if value == "git" or value.endswith("/git"):
                        # kz_common's own wrapper is the one legal site.
                        if name == "kz_common.py":
                            continue
                        offenders.append("%s:%d" % (name, node.lineno))
    assert not offenders, (
        "git invoked outside kz_common.git: %s. §1.4 is an allowlist so that it "
        "is grep-checkable rather than aspirational." % offenders)


def test_no_module_names_a_mutating_verb_as_a_git_argument():
    """The complement: `kc.git("commit", ...)` would be caught at RUNTIME by the
    wrapper, but only on the path that runs it. This finds it statically."""
    offenders = []
    pattern = re.compile(r"""(?:kc|kz_common)\.git(?:_stdout)?\(\s*["'](\w[\w-]*)["']""")
    for name, path in _koreanize_sources():
        with open(path, "r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                for verb in pattern.findall(line):
                    if verb not in kc.GIT_READONLY:
                        offenders.append("%s:%d %s" % (name, lineno, verb))
    assert not offenders, offenders


def test_the_one_named_delegation_keeps_the_allowlist_total():
    """§1.4 names exactly one delegation -- `kz_verify.py` shelling out to
    `check-upstream-overlap.sh`, an existing read-only gate script that runs its
    own git. If a module ever spawns a second such script the allowlist stops
    being total, so the set of shelled-out `.sh` scripts is pinned here."""
    allowed = {"check-upstream-overlap.sh"}
    found = set()
    for _name, path in _koreanize_sources():
        with open(path, "rb") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            # An AST walk over CALL arguments, not a regex over the file: the
            # modules discuss daily-sync-local.sh, resolve-rebase-with-ai.sh and
            # sced-run-now.sh at length in prose, and a regex cannot tell a
            # citation from an invocation. Only a literal that reaches a call is
            # a delegation.
            if not isinstance(node, ast.Call):
                continue
            for arg in node.args:
                elements = arg.elts if isinstance(arg, (ast.List, ast.Tuple)) \
                    else [arg]
                for element in elements:
                    if isinstance(element, ast.Constant) \
                            and isinstance(element.value, str) \
                            and element.value.endswith(".sh"):
                        base = os.path.basename(element.value)
                        if base.startswith("kz-") or base == "koreanize.sh":
                            continue    # koreanize's own shims
                        found.add(base)
    assert found <= allowed, "an undeclared shell delegation: %s" % sorted(
        found - allowed)


# ---------------------------------------------------------------------------
# 4. The DAG -- §1.2's table, the exemption, and the per-object disjuncts
# ---------------------------------------------------------------------------


def test_the_predecessor_table_is_total_and_well_formed():
    assert set(kz.PREDECESSORS) == set(kz.STAGES)
    for stage, preds in kz.PREDECESSORS.items():
        assert set(preds) <= set(kz.STAGES), stage
        assert stage not in preds, "%s is its own predecessor" % stage


def test_the_exemption_set_is_exactly_revert():
    """A widening of this set is exactly the change that makes `revert`
    unreachable in the situation §8.4 invokes it for, so `koreanize.sh` asserts
    it at import rather than special-casing the name inline."""
    assert kz.PREDECESSOR_EXEMPT == frozenset({"revert"})
    assert kz.predecessors_of("revert") == ()
    assert kz.predecessors_of("verify") == ("register",)


def test_the_gate_invoked_stages_are_nobodys_predecessor():
    """`triage`(S6) and `audit`(S7) are stages in their own right with NO DAG
    position, invoked by any failing gate; `ocr` is v1.x and is never a
    predecessor of anything."""
    for positionless in ("triage", "audit", "ocr"):
        assert kz.PREDECESSORS[positionless] == ()
        for stage, preds in kz.PREDECESSORS.items():
            assert positionless not in preds, "%s waits on %s" % (stage,
                                                                  positionless)


def test_golden_is_not_a_stage():
    """It is a `koreanize.sh` COMMAND that drives five stages -- it produces no
    <stage>.json, has no gate and no binding{} (§5.8)."""
    assert "golden" not in kz.STAGES
    assert "golden" not in kz.PREDECESSORS


def test_the_dag_is_acyclic():
    seen, stack = set(), set()

    def visit(stage):
        if stage in stack:
            raise AssertionError("cycle through %s" % stage)
        if stage in seen:
            return
        stack.add(stage)
        for pred in kz.PREDECESSORS[stage]:
            visit(pred)
        stack.discard(stage)
        seen.add(stage)

    for stage in kz.STAGES:
        visit(stage)


def test_the_disjuncts_name_the_two_per_object_stages_and_nothing_else():
    assert set(kz.DISJUNCTS) == {"objtext", "register"}
    assert kz.DISJUNCTS["objtext"] == {"reuse": "reuse", "manufacture": "check"}
    assert kz.DISJUNCTS["register"] == {"reuse": "reuse",
                                        "manufacture": "repoint"}


def test_a_manufacture_entry_adds_no_predecessor(tmp_path):
    """The whole of the §1.2 fix. All eight Challenge Scenarios have BOTH
    counts.manufacture > 0 and counts.reuse > 0, so a rule reading "in its input"
    selects `check` -- a v1 stage -- on every scenario v0 targets, and `objtext`
    refuses at exit 72 on all eight, taking `register` and `verify` with it. The
    disjunct is resolved over the WRITE SET instead."""
    run_dir = str(tmp_path)
    kc.atomic_write_json(kc.report_path(run_dir, "source", "build"), {
        "stage": "source", "results": {"resolution": [
            {"object_id": "A.1", "decision": "reuse"},
            {"object_id": "A.2", "decision": "manufacture"},
            {"object_id": "A.3", "decision": "defer"}]}})
    assert kz.resolve_disjunct(run_dir, "objtext") == ("reuse",)
    assert kz.resolve_disjunct(run_dir, "register") == ("reuse",)
    assert kz.predecessors_of("objtext", run_dir) == ("scaffold", "reuse")
    assert "check" not in kz.predecessors_of("objtext", run_dir)


def test_a_precondition_source_report_yields_no_disjunct(tmp_path):
    """`results: null` on the PRECONDITION path means nothing was adjudicated,
    so nothing is in the write set and the stage simply has no extra
    predecessor -- never a crash and never a silently-satisfied one."""
    run_dir = str(tmp_path)
    kc.atomic_write_json(kc.report_path(run_dir, "source", "build"),
                         {"stage": "source", "results": None})
    assert kz.resolve_disjunct(run_dir, "objtext") == ()


def test_stage_state_never_raises_on_a_missing_or_corrupt_report(tmp_path):
    """A status command that refused on an unreadable report would be useless at
    exactly the moment it is needed."""
    run_dir = str(tmp_path)
    missing = kz.stage_state(run_dir, "init")
    assert missing["present"] is False and missing["consumable"] is False
    with open(kc.report_path(run_dir, "init", "build"), "w",
              encoding="utf-8") as handle:
        handle.write("{not json")
    corrupt = kz.stage_state(run_dir, "init")
    assert corrupt["present"] is True and corrupt["consumable"] is False
    assert "unreadable" in corrupt["blocked_by"]


def test_plan_reports_next_and_blocked(tmp_path):
    run_dir = str(tmp_path)
    result = kz.plan(run_dir)
    assert result["next"] == "init", "with nothing done, init is next"
    assert "source" in result["blocked"]


# ---------------------------------------------------------------------------
# 5. compute_consumable's conjuncts, which every stage inherits
# ---------------------------------------------------------------------------


def _cfg(required=("triage",), forbidden=("reuse",)):
    return {"ai": {"required_stages": list(required),
                   "forbidden_stages": list(forbidden)}}


def test_a_required_ai_stage_with_a_null_ai_block_is_never_consumable():
    """The previous form -- (ai is None or ai.decide_outcome == "proceed") --
    made a stage that SKIPPED its required AI call fully consumable, i.e. it
    rewarded exactly the degradation the HARD CONSTRAINT forbids."""
    report = kc.new_report("triage", "s", mode="build")
    report["exit_code"] = 0
    ok, blocked = kc.compute_consumable(report, cfg=_cfg())
    assert ok is False and "required-AI" in blocked


def test_a_seeded_report_is_consumable_only_inside_its_own_golden_run():
    report = kc.new_report("terms", "s", mode="build",
                           seed={"fixture": "midwinter", "manifest_sha256": "x",
                                 "golden_run_id": "G1"})
    report["exit_code"] = 0
    assert kc.compute_consumable(report, cfg=_cfg(required=()),
                                 golden_run_id="G1")[0] is True
    # Not to a real run in the same directory, not to a later golden, not to
    # `plan` -- and a null id must not compare equal to every run.
    assert kc.compute_consumable(report, cfg=_cfg(required=()),
                                 golden_run_id="G2")[0] is False
    assert kc.compute_consumable(report, cfg=_cfg(required=()),
                                 golden_run_id=None)[0] is False


def test_a_fired_tolerance_without_its_flag_blocks_consumability():
    report = kc.new_report("triage", "s", mode="build",
                           checks=[{"id": "TR1", "name": "donor_choice",
                                    "status": "fail", "tolerance": "donor-choice",
                                    "detail": []}],
                           accepted={"donor-choice": False},
                           ai={"used": True, "decide_outcome": "proceed"})
    report["exit_code"] = 0
    ok, blocked = kc.compute_consumable(report, cfg=_cfg())
    assert ok is False and "donor-choice" in blocked
    report["accepted"] = {"donor-choice": True}
    assert kc.compute_consumable(report, cfg=_cfg())[0] is True


def test_only_a_build_run_may_be_consumable():
    for mode in ("verify-only", "dry-run", "selftest", "replay"):
        report = kc.new_report("reuse", "s", mode=mode)
        report["exit_code"] = 0
        assert kc.compute_consumable(report, cfg=_cfg())[0] is False, mode


# ---------------------------------------------------------------------------
# 6. The TOLERANCES / CONSENTS tables (§4.1), asserted where they can hold
# ---------------------------------------------------------------------------


def test_the_two_tables_are_total_and_disjoint():
    """A flag in both would mean a human DECISION was being recorded as a
    THRESHOLD. 22 says "a measurement exceeded a bound, decide whether to accept
    it"; 26 says "nothing is wrong; a person must authorize this"."""
    assert not kc.tolerance_flags() & kc.consent_flags()
    assert kc.assert_tables_total() == []


def test_exactly_the_hard_cap_rows_lack_a_flag():
    """The flag column is deliberately NOT asserted non-empty, because one row
    has no flag BY DESIGN -- guard.max_files_written is a hard cap with no
    acceptance path at all -- and a rule reading "every row has a registered
    flag" fails on its own table the day it is written."""
    for row in kc.TOLERANCES:
        assert bool(row.flag) != bool(getattr(row, "hard_cap", False)), row.flag


def test_every_row_declares_a_phase_and_an_owning_module():
    for row in tuple(kc.TOLERANCES) + tuple(kc.CONSENTS):
        assert row.phase in kc.PHASES, row
        assert row.module, row


def test_the_pending_rows_are_counted_and_never_silently_skipped():
    """The count is the whole point: a row that quietly dropped out of the
    assertion because its module was missing is precisely the coverage nobody
    would notice was gone."""
    pending = kc.pending_rows()          # {table_name: [row, ...]}
    summary = kc.pending_summary()       # one printed line per non-empty table
    flat = [row for rows in pending.values() for row in rows]
    if flat:
        assert summary, "pending rows must produce a printed, counted line"
        assert len(summary) == len([k for k, v in pending.items() if v])
        for table, rows in pending.items():
            if rows:
                assert any(line.startswith(table) and str(len(rows)) in line
                           for line in summary), table
    for row in flat:
        assert row.phase in ("v1", "v1.x"), \
            "a v0 row cannot be pending at the step v0 ships: %s" % (row,)


def test_a_v0_row_whose_module_exists_but_whose_fault_does_not_fire_still_fails():
    """The phase attribute can NEVER excuse a real gap: a row whose module IS
    present but whose named fault does not fire is a failure, not a pending."""
    v0_modules = {row.module for row in kc.TOLERANCES if row.phase == "v0"}
    for module in v0_modules:
        assert os.path.exists(os.path.join(KOREANIZE_DIR, module)), module


def test_the_two_v0_rows_are_owned_by_modules_that_exist():
    """§6 step 7's green `selftest` is satisfiable only if both v0 rows'
    modules are present -- `triage`'s donor-choice row and kz_langpack's cap."""
    v0 = [r for r in kc.TOLERANCES if r.phase == "v0"]
    assert len(v0) == 2
    for row in v0:
        assert row not in kc.pending_rows(), row


def test_every_registered_flag_has_exactly_one_row():
    for flag in kc.all_flags():
        assert kc.row_by_flag(flag) is not None
    seen = [r.flag for r in tuple(kc.TOLERANCES) + tuple(kc.CONSENTS) if r.flag]
    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# 7. The interpreter tiers (§5.9), which no single stage owns either
# ---------------------------------------------------------------------------


def test_the_declared_stdlib_tier_is_complete_and_scans_clean():
    assert kc.stdlib_tier_pending() == [], \
        "every declared stdlib-tier member must exist by the step v0 ships"
    assert kc.scan_stdlib_imports() == []


def test_the_stdlib_tier_compiles_under_the_platform_interpreter():
    """An import-only AST scan cannot catch `match`, a runtime-evaluated PEP-604
    `X | Y` annotation, or a 3.10+ builtin -- all of which are syntax or name
    errors on 3.9.6 and all of which would ship green under the scan alone."""
    assert kc.compile_stdlib_tier() == []


def test_the_two_tier_checks_police_the_same_subject_set():
    """A scan whose subject set is implied by prose is a scan whose coverage
    nobody can state, and two checks disagreeing about WHICH files they police is
    the shape that let AI_STAGE_MAP sit in an unpoliced art-tier module while a
    policed stdlib-tier one imported it at module scope."""
    assert kc.STDLIB_TIER == ("kz_common.py", "kz_config.py", "kz_langpack.py",
                              "kz_verify.py", "sced_io.py")


def test_the_shebangs_match_the_declared_tier():
    """The tier is not only an import rule: `kz_langpack.py` and `kz_verify.py`
    run under Apple's 3.9.6 shim, and the shebang is what an operator invoking
    the file directly actually gets."""
    for name in ("kz_langpack.py", "kz_verify.py", "kz_common.py",
                 "kz_config.py"):
        path = os.path.join(KOREANIZE_DIR, name)
        with open(path, "r", encoding="utf-8") as handle:
            assert handle.readline().strip() == "#!/usr/bin/python3", name
    for name in ("kz_init.py", "kz_source.py", "kz_triage.py"):
        path = os.path.join(KOREANIZE_DIR, name)
        with open(path, "r", encoding="utf-8") as handle:
            assert handle.readline().strip() == "#!/usr/bin/env python3", name


def test_no_stdlib_tier_module_imports_an_art_tier_one():
    """Closed in BOTH directions: an import of any koreanize module outside the
    triple fails the scan, which is what makes the one-way kz_decide ->
    kz_config dependency a checked property rather than a convention."""
    art = {"kz_init", "kz_source", "kz_triage", "kz_decide", "kz_ask",
           "kz_checkers", "kz_terms", "kz_translate", "kz_slice", "kz_mask",
           "kz_erase", "kz_composite", "kz_typeset", "kz_recompose",
           "kz_upload", "kz_audit", "kz_ocr"}
    assert art.isdisjoint(kc.KOREANIZE_STDLIB_IMPORTS)
    for name in kc.STDLIB_TIER:
        path = os.path.join(KOREANIZE_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            assert art.isdisjoint(names), "%s imports %s" % (name, names)


# ---------------------------------------------------------------------------
# 8. P0a / P0b -- the invocation guards, mandatory in every module
# ---------------------------------------------------------------------------


def test_p0b_refuses_a_path_inside_the_nightly_scratch_worktree():
    """koreanize operates on the PRIMARY checkouts, so it refuses when cwd -- or
    any declared write root -- resolves under .local-sync/scratch/. A run started
    there would be writing into a tree the driver DELETES on completion, and the
    writes would vanish with no diagnostic. Exit 2 ("nothing was read"), and it
    has NO OVERRIDE: unlike P0a there is no legitimate reason to want one, so an
    escape hatch would only ever be used to defeat it."""
    scratch = os.path.join(kc.WORKSPACE_ROOT, ".local-sync", "scratch",
                           "SCED-downloads", "anything")
    with pytest.raises(kc.KzRefusal) as excinfo:
        kc.check_p0b([scratch])
    assert excinfo.value.code == kc.EXIT_USAGE


def test_p0b_admits_an_ordinary_run_dir():
    kc.check_p0b([os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", "x")])


def test_every_module_asserts_the_invocation_guards():
    """P0a and P0b are mandatory in EVERY module (§5.9), so their presence is
    checked statically rather than left to each module's own tests."""
    missing = []
    for name, path in _koreanize_sources():
        if name in ("kz_common.py", "kz_config.py", "kz_ask.py",
                    "kz_decide.py"):
            continue    # helpers and the guard's own home, not stage entry points
        with open(path, "r", encoding="utf-8") as handle:
            body = handle.read()
        if "check_invocation_guards" not in body and "check_p0b" not in body:
            missing.append(name)
    assert not missing, "no invocation guard in %s" % missing


# ---------------------------------------------------------------------------
# 9. The write-root guard's blast radius
# ---------------------------------------------------------------------------


def _roots_for(pack):
    import kz_init as ki
    cfg = ki.build_scenario_config(
        slug="s", scenario_name="S", source_dir="d", source_tree_sha256="x",
        pack=pack, container_guid="aaaaaa", container_stem="Stem.aaaaaa",
        arkham_prefixes=["01"], atlases=[], shared_backs=[], fonts={},
        run_dir=".am/koreanize/s", counts={"arkham_ids": 1})
    return cfg["guard"]["write_roots"]


def test_a_campaigns_scenario_does_not_open_the_player_cards_container():
    """Deny-by-default must not be widened for a stage that cannot reach it.
    Every v0 destination is derived from cfg["pack"] / pack_container /
    container_stem, so no v0 path can land under a foreign pack's tree -- and
    `repoint`, which legitimately needs it (§5.3's URL-predicate rule), is v1 and
    refuses at 13."""
    roots = _roots_for("Korean - Campaigns")
    assert not any("Korean-PlayerCards.KoreanI/" in r for r in roots), roots


def test_a_player_cards_scenario_still_covers_its_own_container():
    roots = _roots_for("Korean - Player Cards")
    assert any("Korean-PlayerCards.KoreanI" in r for r in roots)


def test_the_scenario_container_object_is_a_root_and_the_directory_is_too():
    """They are siblings, and a root ending in "/" is a prefix test -- so
    "<stem>.json" is not under "<stem>/" and needs its own row. Without it
    `register` cannot create the container that holds the overrides."""
    roots = _roots_for("Korean - Campaigns")
    assert any(r.endswith("/Stem.aaaaaa/") for r in roots)
    assert any(r.endswith("/Stem.aaaaaa.json") for r in roots)


def test_every_write_root_is_workspace_relative_and_inside_the_langpack():
    """§3.2: every path in a scenario.json is workspace-relative. A root that
    embedded an absolute machine path could not be used on another machine, and
    because the config is hash-pinned the fix would be a regeneration that
    invalidates the init gate."""
    for pack in ("Korean - Campaigns", "Korean - Player Cards",
                 "Korean - Fan Campaigns"):
        for root in _roots_for(pack):
            assert not os.path.isabs(root), root
            assert root.startswith("SCED-downloads/decomposed/language-pack/"), root
