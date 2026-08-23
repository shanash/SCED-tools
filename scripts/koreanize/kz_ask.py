#!/usr/bin/env python3
"""koreanize `ask` -- the batch machinery every AI stage is invoked through.

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER`; it imports `kz_config` and `kz_decide` one-way.

A stage is **one or more** `claude` invocations, never an unstated number (§3.7).
Without batching, rule 1 is unsatisfiable in practice: War of the Outer Gods' S2
universe is 58 cards and The Blob's is 80, and rule 1 would demand the entire
universe in one manifest inside a single 780 s wall clock -- a scenario that
exceeds context or time could then NEVER pass, with exit 65 as its only outcome
and no recovery path.

WHAT THIS MODULE OWNS
  - the splitter: `inputs.json.universe[]` into batches of at most
    `ai.batch.<Sn>.max_units_per_call`, written under
    `ai/<stage>/<stamp>/batch-NN/`, one shim invocation per batch.
  - the ceiling re-assertion at exit 13 -- naming the stage, the universe size
    and the ceiling, a legible "this scenario is too big for the configured
    budget" rather than a timeout. `kz_config` already refuses a mis-declared
    `universe_size` at load (exit 4); this is defence in depth for the stages
    whose universe is not knowable until invocation.
  - `len(universe) == universe_size` when the bundle is built (exit 13), with the
    `null` carve-out for S6 and S7 -- both gate-invoked, so a set-equality against
    `null` is unsatisfiable and would refuse every S6/S7 bundle before its first
    invocation.
  - `budget_check` BETWEEN batches, and the exit-**25** stop that leaves every
    completed batch on disk. 25 is not 65: 65 is scoped to a single `claude`
    invocation failing, and telling an operator "claude unavailable" when the
    truth is "the stage ran out of its own budget" sends them to the wrong file.
  - the resume path, so 25 is not a dead end: every completed `batch-NN/` is
    re-used, each bound by `inputs_sha256` and revalidated -- **never trusted on
    filename** -- and the run starts at the first missing batch.

WHY A BATCH BUNDLE CARRIES NO TIMESTAMP
  The resume path binds a completed batch by `sha256(batch-NN/inputs.json)`. A
  `generated_at` inside that file would make every re-plan look like input drift
  and would turn resume into re-invocation. The stamp lives in the stage-level
  `inputs.json` and in the directory name, never in a batch bundle.

Exit codes (§4.2):
   0  every batch complete
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
  13  precondition -- universe over the batch ceiling, a universe that disagrees
      with the declared `universe_size`, or a bundle input that is missing
  25  the stage stopped BETWEEN batches on `max_budget_usd_per_stage` or
      `stage_wall_clock_s`; N of M batches are complete on disk. Re-run to resume.
  65  `claude` unavailable / unauthenticated / timed out -- the shim's own code,
      passed through
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402
import kz_decide as kd  # noqa: E402

SHIM = os.path.join(kc.PACKAGE_DIR, "kz-ask-claude.sh")
POLICY = os.path.join(kc.PACKAGE_DIR, "policy.md")
PROMPTS_DIR = os.path.join(kc.PACKAGE_DIR, "prompts")

# The shim's exit codes koreanize passes through verbatim rather than flattening.
SHIM_PASSTHROUGH = (kc.EXIT_USAGE, kc.EXIT_PRECONDITION, kc.EXIT_AI_UNAVAILABLE)

# The budget knobs `invoke_shim` forwards, and the shim flag each becomes. A key
# absent here is dropped silently at the door of the invocation: the shim falls
# back to its own default and the scenario's declared number is never enforced.
# That is what happened to `call_budget_s` -- `kz_config` proves
# `max_calls * call_budget_s <= stage_wall_clock_s` at load, so a scenario that
# shortens it to leave headroom under a tight stage wall clock was getting a
# config-time proof with no runtime behaviour behind it. `selftest` re-derives the
# flags the shim actually parses from the shim itself, so the two cannot drift.
SHIM_BUDGET_FLAGS = (
    ("model", "--model"),
    ("fallback_model", "--fallback-model"),
    ("effort", "--effort"),
    ("timeout_s", "--timeout"),
    ("call_budget_s", "--call-budget"),
    ("max_budget_usd_per_call", "--max-budget-usd"),
)


# ---------------------------------------------------------------------------
# 1. The splitter and the ceiling
# ---------------------------------------------------------------------------

def plan_batches(universe, max_units_per_call):
    """Partition: total, disjoint, every batch at or under `max_units_per_call`."""
    if not isinstance(max_units_per_call, int) or max_units_per_call < 1:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "max_units_per_call must be a positive integer",
                  "got %r" % (max_units_per_call,))
    return [list(universe[i:i + max_units_per_call])
            for i in range(0, len(universe), max_units_per_call)]


def assert_universe(sid, entry, universe):
    """§3.7's invariant, with BOTH conjuncts carrying the `null` carve-out.

    `universe_size: null` means "not knowable until invocation" and is what S6 and
    S7 declare, because both are gate-invoked and their universe is the failing
    gate's `checks[].detail[]`. A set-equality against `null` is unsatisfiable, so
    under a one-sided form this function would refuse every S6 and S7 bundle at
    exit 13 on the way to its first invocation.
    """
    stage_name = kz.AI_STAGE_MAP.get(sid, sid)
    if not universe:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s (%s): the universe is empty" % (sid, stage_name),
                  "a stage with nothing to ask about must not reach an invocation")
    units = entry.get("max_units_per_call")
    calls = entry.get("max_calls")
    if not isinstance(units, int) or not isinstance(calls, int):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s: ai.batch.%s is malformed" % (stage_name, sid),
                  "max_units_per_call=%r max_calls=%r" % (units, calls))
    ceiling = units * calls
    if len(universe) > ceiling:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s (%s): universe of %d units is over the declared ceiling of %d"
                  % (sid, stage_name, len(universe), ceiling),
                  "ceiling = max_units_per_call (%d) * max_calls (%d). This "
                  "scenario is too big for the configured budget; widen "
                  "ai.batch.%s in scenario.json or narrow the scope."
                  % (units, calls, sid))
    if "universe_size" not in entry:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s: ai.batch.%s declares no universe_size" % (stage_name, sid),
                  "declare null to mean 'not knowable until invocation'")
    declared = entry.get("universe_size")
    if declared is not None and len(universe) != declared:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s (%s): measured universe %d != declared universe_size %d"
                  % (sid, stage_name, len(universe), declared),
                  "the corpus changed under a pinned config; re-run init rather "
                  "than asking a silently short universe")
    return ceiling


def assert_call_bounds(stage_name, ai):
    """`timeout_s` must fit inside `call_budget_s` (§3.7's defence in depth).

    The bound that actually governs an in-flight `claude` call is the shim's
    `with_timeout "${AI_TIMEOUT}"`; `call_budget_s` is only a PRE-call gate, since
    the shim's `budget_check` runs before an invocation and never inside one. So a
    `timeout_s` longer than `call_budget_s` lets one call run past the per-batch
    budget, and `kz_config`'s `max_calls * call_budget_s <= stage_wall_clock_s`
    proof stops being an end-to-end runtime guarantee -- the overrun surfaces only
    between batches, at exit 25. `kz_config` refuses this at load (exit 4); this is
    the same statement re-made where the stage is known, before any invocation.
    """
    timeout = ai.get("timeout_s")
    call_budget = ai.get("call_budget_s")
    for key, value in (("timeout_s", timeout), ("call_budget_s", call_budget)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "%s: ai.%s is %r, not a positive integer of seconds"
                      % (stage_name, key, value),
                      "scenario.json declares the per-call bounds; a knob that is "
                      "merely present bounds nothing")
    if timeout > call_budget:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s: ai.timeout_s %ds > ai.call_budget_s %ds"
                  % (stage_name, timeout, call_budget),
                  "call_budget_s gates a call only BEFORE it starts, so a longer "
                  "per-call wall clock overruns the batch budget it is meant to "
                  "close; lower timeout_s or raise call_budget_s in scenario.json")


# ---------------------------------------------------------------------------
# 2. Building the bundle
# ---------------------------------------------------------------------------

def default_ask_root(cfg, workspace=None):
    workspace = workspace or kc.WORKSPACE_ROOT
    run_dir = cfg.get("run_dir")
    if not run_dir:
        kc.refuse(kc.EXIT_PRECONDITION, "scenario.json declares no run_dir")
    return os.path.join(workspace, run_dir, "ai")


def prompt_path_for(sid):
    stage_name = kz.AI_STAGE_MAP.get(sid)
    return os.path.join(PROMPTS_DIR, "%s-%s.md" % (sid, stage_name))


def _read_text(path, what):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is missing" % what, path)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def build_bundle(cfg, sid, universe, units, identity=None, material=None,
                 ask_root=None, stamp=None, prompt=None, policy=None,
                 workspace=None, extra=None):
    """Assemble `ai/<stage>/<stamp>/` with one `batch-NN/` per invocation.

    Idempotent: it creates and overwrites the bundle inputs and never removes a
    completed batch's `claude-envelope.json`, `observed.json` or `out/`. That is
    what makes a resume run re-plan into the same directory safely.

    Returns the ask directory.
    """
    if sid not in kz.AI_STAGE_MAP:
        kc.refuse(kc.EXIT_USAGE, "unknown stage id %r" % sid,
                  "the S-ids are %s (kz_config.AI_STAGE_MAP)"
                  % sorted(kz.AI_STAGE_MAP))
    stage_name = kz.AI_STAGE_MAP[sid]
    ai = cfg.get("ai") or {}
    entry = (ai.get("batch") or {}).get(sid)
    if not isinstance(entry, dict):
        kc.refuse(kc.EXIT_PRECONDITION, "scenario.json has no ai.batch.%s" % sid)

    assert_call_bounds(stage_name, ai)
    universe = list(universe)
    assert_universe(sid, entry, universe)
    groups = plan_batches(universe, entry["max_units_per_call"])

    ask_root = ask_root or os.path.join(default_ask_root(cfg, workspace), stage_name)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    ask_dir = os.path.join(ask_root, stamp)

    policy_text = _read_text(policy or POLICY, "policy.md")
    prompt_text = _read_text(prompt or prompt_path_for(sid),
                             "prompts/%s-%s.md" % (sid, stage_name))
    schema_blob = json.dumps(kd.stage_schema(sid), ensure_ascii=False, indent=2) + "\n"
    schema_name = "%s.schema.json" % sid

    material = dict(material or {})
    material_sha = {}
    for name in sorted(material):
        src = material[name]
        if not os.path.exists(src):
            kc.refuse(kc.EXIT_PRECONDITION, "material %r does not exist" % name, src)
        material_sha["material/%s" % name] = kc.sha256_file(src)

    stage_inputs = {
        "schema": 1,
        "stage": sid,
        "stage_name": stage_name,
        "slug": cfg.get("slug"),
        # The pin, so a manifest carries the identity of the config it was asked
        # under and rule 7 can reject one from another run.
        "scenario_sha256": cfg.get("config_sha256"),
        "unit": entry.get("unit"),
        "generated_at": kc.utc_now(),
        "universe": universe,
        "universe_size": entry.get("universe_size"),
        "max_units_per_call": entry["max_units_per_call"],
        "max_calls": entry["max_calls"],
        "batch_of": len(groups),
        "budget": {
            "model": ai.get("model"),
            "fallback_model": ai.get("fallback_model"),
            "effort": ai.get("effort"),
            "timeout_s": ai.get("timeout_s"),
            "call_budget_s": ai.get("call_budget_s"),
            "stage_wall_clock_s": ai.get("stage_wall_clock_s"),
            "max_budget_usd_per_call": ai.get("max_budget_usd_per_call"),
            "max_budget_usd_per_stage": ai.get("max_budget_usd_per_stage"),
        },
        "units": dict(units or {}),
        "identity": dict(identity or {}),
        "material_sha256": material_sha,
    }
    if extra:
        stage_inputs.update(extra)

    if not os.path.isdir(ask_dir):
        os.makedirs(ask_dir)
    kc.atomic_write_json(os.path.join(ask_dir, "inputs.json"), stage_inputs)
    kc.atomic_write_text(os.path.join(ask_dir, schema_name), schema_blob)

    for index, unit_ids in enumerate(groups, start=1):
        batch_dir = os.path.join(ask_dir, "batch-%02d" % index)
        for sub in ("", "out", "material"):
            path = os.path.join(batch_dir, sub) if sub else batch_dir
            if not os.path.isdir(path):
                os.makedirs(path)
        kc.atomic_write_json(os.path.join(batch_dir, "inputs.json"),
                             batch_inputs(stage_inputs, index, unit_ids))
        kc.atomic_write_text(os.path.join(batch_dir, "policy.md"), policy_text)
        kc.atomic_write_text(os.path.join(batch_dir, "prompt.md"), prompt_text)
        kc.atomic_write_text(os.path.join(batch_dir, schema_name), schema_blob)
        for name in sorted(material):
            shutil.copyfile(material[name],
                            os.path.join(batch_dir, "material", name))
    return ask_dir


# The per-batch bundle shape is kz_decide's, not this module's: rule 7 binds a
# manifest to sha256(batch-NN/inputs.json), so the bundle is part of the run
# binding and the schema owner owns it. Two implementations of one format is
# exactly the drift the "one owner" property exists to prevent.
batch_inputs = kd.batch_inputs


# ---------------------------------------------------------------------------
# 3. Resume -- bound by inputs_sha256, revalidated, never trusted on filename
# ---------------------------------------------------------------------------

def batch_state(ask_dir, index, planned_sha=None):
    """Is `batch-NN/` a completed invocation this run may re-use?

    A filename is not evidence. The batch is re-used only when its own
    `inputs.json` hashes to the planned bundle's digest AND an envelope is on disk
    that parses and carries a session id. Anything else is re-invoked.
    """
    batch_dir = os.path.join(ask_dir, "batch-%02d" % index)
    state = {"batch": index, "dir": batch_dir, "complete": False, "reason": None,
             "inputs_sha256": None, "cost_usd": 0.0, "session_id": None,
             "reused": False}
    inputs_path = os.path.join(batch_dir, "inputs.json")
    if not os.path.exists(inputs_path):
        state["reason"] = "no inputs.json"
        return state
    state["inputs_sha256"] = kc.sha256_file(inputs_path)
    if planned_sha is not None and state["inputs_sha256"] != planned_sha:
        state["reason"] = ("inputs.json digest %s != planned %s -- the bundle "
                           "changed under the recorded batch"
                           % (state["inputs_sha256"][:16], planned_sha[:16]))
        return state
    envelope_path = os.path.join(batch_dir, "claude-envelope.json")
    if not os.path.exists(envelope_path):
        state["reason"] = "no claude-envelope.json"
        return state
    try:
        with open(envelope_path, "r", encoding="utf-8") as fh:
            envelope = json.load(fh)
    except ValueError as exc:
        state["reason"] = "claude-envelope.json is not valid JSON: %s" % exc
        return state
    if not isinstance(envelope, dict) or not envelope.get("session_id"):
        state["reason"] = "claude-envelope.json carries no session_id"
        return state
    state["complete"] = True
    state["session_id"] = envelope.get("session_id")
    state["cost_usd"] = envelope.get("total_cost_usd") or 0.0
    return state


# ---------------------------------------------------------------------------
# 4. budget_check -- between batches, never inside one
# ---------------------------------------------------------------------------

def budget_finding(budget, cost_usd, elapsed_s):
    """The predicate `budget_check` runs BETWEEN batches (§3.7). None means go on.

    The cost half is forward-looking on purpose: the next invocation may cost up
    to `max_budget_usd_per_call`, so stopping only once the cap is already
    exceeded would exceed it by construction.
    """
    clock = budget.get("stage_wall_clock_s")
    if isinstance(clock, int) and elapsed_s > clock:
        return ("stage_wall_clock_s: %ds elapsed > %ds" % (elapsed_s, clock))
    cap = budget.get("max_budget_usd_per_stage")
    per_call = budget.get("max_budget_usd_per_call") or 0
    if cap is not None and (cost_usd + per_call) > cap:
        return ("max_budget_usd_per_stage: %.2f spent + %.2f for the next call > %.2f"
                % (cost_usd, per_call, cap))
    return None


# ---------------------------------------------------------------------------
# 5. Invocation
# ---------------------------------------------------------------------------

def invoke_shim(ask_dir, batch_dir, sid, budget, run_dir=None, workspace=None,
                readonly_trees=(), claude_bin=None, shim=None, quiet=False):
    """One `claude` invocation, through the shim and nothing else.

    Returns the shim's rc. koreanize never builds a `claude` command line itself:
    the grant, the credential rule and the env scrub live in one file so that the
    blast radius is written out rather than described (§4.3).
    """
    shim = shim or SHIM
    if not os.path.exists(shim):
        kc.refuse(kc.EXIT_PRECONDITION, "the ask shim is missing", shim)
    cmd = [shim,
           "--ask-dir", batch_dir,
           "--stage", sid,
           "--run-dir", run_dir or os.path.dirname(os.path.dirname(ask_dir)),
           "--workspace", workspace or kc.WORKSPACE_ROOT]
    for key, flag in SHIM_BUDGET_FLAGS:
        value = budget.get(key)
        if value is not None:
            cmd += [flag, str(value)]
    for tree in readonly_trees:
        cmd += ["--readonly-tree", tree]
    if claude_bin:
        cmd += ["--claude-bin", claude_bin]
    if quiet:
        cmd += ["--quiet"]
    log = os.path.join(batch_dir, "shim.log")
    with open(log, "wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
    return proc.returncode


def run_bundle(ask_dir, invoke=True, claude_bin=None, shim=None,
               readonly_trees=(), workspace=None, quiet=False, started=None):
    """Walk the batches: re-use what is complete, invoke what is not, and stop at a
    batch boundary when the budget says so.

    `invoke=False` is the offline path: completed batches are still walked and
    budget-checked, and the first missing batch becomes `resume_from`. It is what
    makes the splitter, the ceiling, the merge, the exit-25 stop and the resume
    path all testable with no `claude`, no credential and no cost.
    """
    inputs_path = os.path.join(ask_dir, "inputs.json")
    if not os.path.exists(inputs_path):
        kc.refuse(kc.EXIT_PRECONDITION, "no stage inputs.json in the ask directory",
                  ask_dir)
    with open(inputs_path, "r", encoding="utf-8") as fh:
        stage_inputs = json.load(fh)
    sid = stage_inputs["stage"]
    budget = stage_inputs.get("budget") or {}
    universe = list(stage_inputs.get("universe") or [])
    groups = plan_batches(universe, stage_inputs["max_units_per_call"])
    started = started if started is not None else time.time()

    result = {
        "stage": sid,
        "stage_name": stage_inputs.get("stage_name"),
        "ask_dir": ask_dir,
        "batch_of": len(groups),
        "batches": [],
        "complete": False,
        "resume_from": None,
        "cost_usd": 0.0,
        "elapsed_s": 0,
        "stopped_by": None,
        "exit_code": kc.EXIT_OK,
        "invoked": 0,
        "reused": 0,
    }

    for index, unit_ids in enumerate(groups, start=1):
        planned = batch_inputs(stage_inputs, index, unit_ids)
        batch_dir = os.path.join(ask_dir, "batch-%02d" % index)
        if not os.path.isdir(batch_dir):
            os.makedirs(batch_dir)
        planned_path = os.path.join(batch_dir, "inputs.json")
        # The digest of the bundle THIS run planned, derived from the plan rather
        # than read back off disk. Hashing the file instead makes the comparison
        # in `batch_state` a tautology -- a recorded batch whose inputs.json no
        # longer matches the plan would hash to itself and be re-used, which is
        # precisely the "never trusted on filename" property of §3.7. The bytes
        # are `sced_io.atomic_write_json`'s, so the two are comparable.
        planned_sha = kc.sha256_bytes(
            (json.dumps(planned, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        if not os.path.exists(planned_path):
            kc.atomic_write_json(planned_path, planned)

        state = batch_state(ask_dir, index, planned_sha)
        if state["complete"]:
            state["reused"] = True
            result["reused"] += 1
        elif not invoke:
            result["resume_from"] = index
            result["batches"].append(state)
            break
        else:
            rc = invoke_shim(ask_dir, batch_dir, sid, budget,
                             workspace=workspace, readonly_trees=readonly_trees,
                             claude_bin=claude_bin, shim=shim, quiet=quiet)
            if rc != 0:
                detail = os.path.join(batch_dir, "stage-error.txt")
                message = ""
                if os.path.exists(detail):
                    with open(detail, "r", encoding="utf-8") as fh:
                        message = fh.read().strip()
                code = rc if rc in SHIM_PASSTHROUGH else kc.EXIT_AI_UNAVAILABLE
                kc.refuse(code,
                          "batch-%02d of %d: the ask shim exited %d"
                          % (index, len(groups), rc),
                          message or "see %s" % os.path.join(batch_dir, "shim.log"))
            state = batch_state(ask_dir, index, planned_sha)
            if not state["complete"]:
                kc.refuse(kc.EXIT_AI_UNAVAILABLE,
                          "batch-%02d of %d produced no usable envelope"
                          % (index, len(groups)),
                          state["reason"] or "")
            result["invoked"] += 1

        result["batches"].append(state)
        result["cost_usd"] += state["cost_usd"] or 0.0
        result["elapsed_s"] = int(time.time() - started)

        if index < len(groups):
            finding = budget_finding(budget, result["cost_usd"], result["elapsed_s"])
            if finding:
                result["stopped_by"] = finding
                result["resume_from"] = index + 1
                result["exit_code"] = kc.EXIT_AI_BUDGET
                return result

    result["complete"] = (len(result["batches"]) == len(groups)
                          and all(b["complete"] for b in result["batches"]))
    if result["complete"]:
        result["resume_from"] = None
    return result


def seed_from_replay(ask_dir, src):
    """Copy a recorded run's INVOCATION RESULTS into an already-built bundle.

    The bundle half (inputs, prompt, policy, schema, material) is re-planned by
    `build_bundle`, never copied -- so a replay that resumes is resuming against a
    freshly derived plan, and a plan that no longer matches the recording shows up
    as a digest mismatch rather than being papered over.
    """
    if not os.path.isdir(src):
        kc.refuse(kc.EXIT_USAGE, "--replay directory does not exist", src)
    copied = []
    for index, batch_dir in kd.batch_dirs(src):
        target = os.path.join(ask_dir, "batch-%02d" % index)
        if not os.path.isdir(target):
            os.makedirs(target)
        for name in ("claude-envelope.json", "observed.json"):
            full = os.path.join(batch_dir, name)
            if os.path.exists(full):
                shutil.copy2(full, os.path.join(target, name))
                copied.append(os.path.join(os.path.basename(batch_dir), name))
        out_src = os.path.join(batch_dir, "out")
        if os.path.isdir(out_src):
            out_dst = os.path.join(target, "out")
            if os.path.isdir(out_dst):
                shutil.rmtree(out_dst)
            shutil.copytree(out_src, out_dst)
            copied.append(os.path.join(os.path.basename(batch_dir), "out") + "/")
        if not os.path.exists(os.path.join(target, "inputs.json")):
            src_inputs = os.path.join(batch_dir, "inputs.json")
            if os.path.exists(src_inputs):
                shutil.copy2(src_inputs, os.path.join(target, "inputs.json"))
                copied.append(os.path.join(os.path.basename(batch_dir),
                                           "inputs.json"))
    return copied


# ---------------------------------------------------------------------------
# 6. --selftest
# ---------------------------------------------------------------------------

def _selftest_cfg(slug="selftest", scenario_sha256="b" * 64,
                  max_units=2, max_calls=3, universe_size=None,
                  stage_usd=60, call_usd=10, clock=5400):
    """A scenario-shaped dict with only what build_bundle reads. Deliberately not
    a generated scenario.json: this exercises the splitter, not kz_config.

    `slug` and `scenario_sha256` are taken from the RECORDING when one is being
    replayed, because rule 7 binds a manifest to both and a cfg that invented its
    own would make every replay fail as a run-binding drift.
    """
    return {
        "slug": slug,
        "run_dir": ".am/koreanize/%s" % slug,
        "config_sha256": scenario_sha256,
        "ai": {"model": "claude-opus-5", "fallback_model": "claude-opus-4-8",
               "effort": "high", "timeout_s": 780, "call_budget_s": 900,
               "stage_wall_clock_s": clock, "max_budget_usd_per_call": call_usd,
               "max_budget_usd_per_stage": stage_usd,
               "batch": {"S6": {"unit": "finding",
                                "max_units_per_call": max_units,
                                "max_calls": max_calls,
                                "universe_size": universe_size}}},
    }


def selftest(verbose=True):
    """Every property this module owns, offline and at no cost."""
    import tempfile

    findings = []

    def fires(label, code, fn):
        try:
            fn()
        except kc.KzRefusal as exc:
            if exc.code != code:
                findings.append("%s: expected exit %d, got %d" % (label, code, exc.code))
            return
        findings.append("%s: did not refuse (expected exit %d)" % (label, code))

    # -- the partition: total, disjoint, every batch at or under the bound -----
    universe = ["u%02d" % i for i in range(1, 10)]
    for bound in (1, 2, 4, 9, 20):
        groups = plan_batches(universe, bound)
        flat = [u for g in groups for u in g]
        if flat != universe:
            findings.append("plan_batches(%d): not total/in order" % bound)
        if len(set(flat)) != len(flat):
            findings.append("plan_batches(%d): batches are not disjoint" % bound)
        if any(len(g) > bound for g in groups):
            findings.append("plan_batches(%d): a batch exceeds the bound" % bound)
        if any(not g for g in groups):
            findings.append("plan_batches(%d): produced an empty batch" % bound)

    # -- the ceiling, and the universe_size equality with its null carve-out ---
    entry = {"max_units_per_call": 2, "max_calls": 3, "universe_size": None}
    assert_universe("S6", entry, universe[:6])          # 6 == ceiling, allowed
    fires("ceiling (7 units over a ceiling of 6)", kc.EXIT_PRECONDITION,
          lambda: assert_universe("S6", entry, universe[:7]))
    fires("empty universe", kc.EXIT_PRECONDITION,
          lambda: assert_universe("S6", entry, []))
    fires("absent universe_size", kc.EXIT_PRECONDITION,
          lambda: assert_universe("S6", {"max_units_per_call": 2, "max_calls": 3},
                                  universe[:2]))
    fires("universe_size disagrees with the measured universe", kc.EXIT_PRECONDITION,
          lambda: assert_universe("S6", {"max_units_per_call": 2, "max_calls": 3,
                                         "universe_size": 5}, universe[:4]))

    # -- the per-call clock must fit inside the pre-call gate (§3.7) -----------
    assert_call_bounds("triage", {"timeout_s": 780, "call_budget_s": 900})
    assert_call_bounds("triage", {"timeout_s": 420, "call_budget_s": 420})
    fires("timeout_s over call_budget_s", kc.EXIT_PRECONDITION,
          lambda: assert_call_bounds("triage", {"timeout_s": 780,
                                                "call_budget_s": 420}))
    fires("a non-integer timeout_s", kc.EXIT_PRECONDITION,
          lambda: assert_call_bounds("triage", {"timeout_s": "780s",
                                                "call_budget_s": 900}))
    fires("a zero call_budget_s", kc.EXIT_PRECONDITION,
          lambda: assert_call_bounds("triage", {"timeout_s": 780,
                                                "call_budget_s": 0}))

    # -- budget_finding: the two knobs, and the forward-looking cost half ------
    budget = {"stage_wall_clock_s": 100, "max_budget_usd_per_stage": 60,
              "max_budget_usd_per_call": 10}
    if budget_finding(budget, 40.0, 10) is not None:
        findings.append("budget_finding stopped a run with room left")
    if budget_finding(budget, 55.0, 10) is None:
        findings.append("budget_finding did not stop before an over-cap call")
    if budget_finding(budget, 0.0, 101) is None:
        findings.append("budget_finding did not stop on the stage wall clock")

    # -- the flag mapping, against the shim that has to parse it ---------------
    # A budget knob computed, config-validated and then not forwarded is a stated
    # invariant with nothing behind it at runtime. `call_budget_s` is the one that
    # was missing, so it is named rather than only covered by the loop.
    mapped = dict(SHIM_BUDGET_FLAGS)
    if mapped.get("call_budget_s") != "--call-budget":
        findings.append("call_budget_s is not mapped to --call-budget: the shim "
                        "would measure every batch against its own default")
    if os.path.exists(SHIM):
        with open(SHIM, "r", encoding="utf-8") as fh:
            shim_text = fh.read()
        for _key, flag in SHIM_BUDGET_FLAGS:
            if ("%s)" % flag) not in shim_text:
                findings.append("%s is forwarded but %s parses no such flag"
                                % (flag, os.path.basename(SHIM)))

        # The shim's per-call guard must fail CLOSED. `set -e` is suspended inside
        # an `if` condition, so a `-gt` against a non-integer raises an arithmetic
        # error that reads as FALSE and the budget check silently disappears for
        # the rest of the run. The shape has to be validated before it is compared.
        guard_body = shim_text.split("budget_check() {", 1)[-1].split("\n}", 1)[0]
        shape = guard_body.find('"${AI_CALL_BUDGET}" =~ ^[1-9][0-9]*$')
        compare = guard_body.find('"${e}" -gt "${AI_CALL_BUDGET}"')
        if shape < 0 or compare < 0 or shape > compare:
            findings.append("the shim's budget_check compares AI_CALL_BUDGET "
                            "before validating its shape: the guard fails open")

    root = tempfile.mkdtemp(prefix="kz-ask-selftest.")
    try:
        recorded = kd.build_reference_run(os.path.join(root, "recorded"))
        gate = os.path.join(recorded, "batch-01", "material", "gate-typeset.json")
        lock = os.path.join(recorded, "batch-01", "material", "lock.json")
        with open(os.path.join(recorded, "inputs.json"), "r", encoding="utf-8") as fh:
            reference = json.load(fh)
        material = {"gate-typeset.json": gate, "lock.json": lock}

        cfg = _selftest_cfg(slug=reference["slug"],
                            scenario_sha256=reference["scenario_sha256"])
        ask_dir = build_bundle(cfg, "S6", reference["universe"], reference["units"],
                               identity=reference["identity"], material=material,
                               ask_root=os.path.join(root, "ask"), stamp="00000000-000000")
        if len(kd.batch_dirs(ask_dir)) != 2:
            findings.append("build_bundle split 3 units at 2/call into %d batches"
                            % len(kd.batch_dirs(ask_dir)))

        # A batch bundle is a pure function of the plan: re-planning must not move
        # its digest, or every resume would look like drift.
        first = kc.sha256_file(os.path.join(ask_dir, "batch-01", "inputs.json"))
        build_bundle(cfg, "S6", reference["universe"], reference["units"],
                     identity=reference["identity"], material=material,
                     ask_root=os.path.join(root, "ask"), stamp="00000000-000000")
        if kc.sha256_file(os.path.join(ask_dir, "batch-01", "inputs.json")) != first:
            findings.append("re-planning moved batch-01/inputs.json's digest")

        # -- resume: nothing recorded yet -> the first missing batch is 1 -------
        result = run_bundle(ask_dir, invoke=False)
        if result["resume_from"] != 1 or result["complete"]:
            findings.append("an unrecorded bundle did not resume at batch 1 (got %r)"
                            % result["resume_from"])

        # -- resume: batch-01 recorded -> start at 2 ----------------------------
        for name in ("claude-envelope.json", "observed.json"):
            shutil.copy2(os.path.join(recorded, "batch-01", name),
                         os.path.join(ask_dir, "batch-01", name))
        shutil.copytree(os.path.join(recorded, "batch-01", "out"),
                        os.path.join(ask_dir, "batch-01", "out"),
                        dirs_exist_ok=True)
        result = run_bundle(ask_dir, invoke=False)
        if result["resume_from"] != 2:
            findings.append("a bundle with batch-01 recorded resumed at %r, not 2"
                            % result["resume_from"])
        if result["reused"] != 1:
            findings.append("batch-01 was not re-used (reused=%d)" % result["reused"])

        # -- a completed batch is NEVER trusted on filename --------------------
        tampered = os.path.join(ask_dir, "batch-01", "inputs.json")
        with open(tampered, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["universe"] = list(reversed(doc["universe"]))
        kc.atomic_write_json(tampered, doc)
        state = batch_state(ask_dir, 1, first)
        if state["complete"]:
            findings.append("a batch whose inputs.json moved was still re-used")
        # And through the walk, not only through the predicate: run_bundle derives
        # the planned digest from the PLAN, so a tampered recorded batch is
        # re-invoked rather than hashing to itself and being trusted.
        result = run_bundle(ask_dir, invoke=False)
        if result["resume_from"] != 1:
            findings.append("run_bundle re-used a batch whose inputs.json moved "
                            "(resume_from=%r)" % result["resume_from"])
        kc.atomic_write_json(tampered, batch_inputs(
            reference, 1, reference["universe"][:2]))

        # -- the full recorded run replays, resumes to completion, and decides --
        replayed = build_bundle(cfg, "S6", reference["universe"], reference["units"],
                                identity=reference["identity"], material=material,
                                ask_root=os.path.join(root, "ask2"),
                                stamp="00000000-000001")
        seed_from_replay(replayed, recorded)
        result = run_bundle(replayed, invoke=False)
        if not result["complete"] or result["resume_from"] is not None:
            findings.append("a fully recorded run did not complete offline: %r"
                            % result["stopped_by"])
        if result["invoked"] != 0:
            findings.append("--replay invoked the shim %d time(s)" % result["invoked"])
        report, code = kd.decide(replayed)
        if code != kc.EXIT_OK:
            findings.append("the replayed bundle did not decide proceed: exit %d (%s)"
                            % (code, report["reason"]))

        # -- the between-batches stop at exit 25, with batch-01 left on disk ----
        broke = build_bundle(_selftest_cfg(slug=reference["slug"],
                                           scenario_sha256=reference["scenario_sha256"],
                                           stage_usd=1), "S6",
                             reference["universe"], reference["units"],
                             identity=reference["identity"], material=material,
                             ask_root=os.path.join(root, "ask3"),
                             stamp="00000000-000002")
        seed_from_replay(broke, recorded)
        result = run_bundle(broke, invoke=False)
        if result["exit_code"] != kc.EXIT_AI_BUDGET:
            findings.append("an exhausted stage budget gave exit %d, expected 25"
                            % result["exit_code"])
        if result["resume_from"] != 2:
            findings.append("the exit-25 stop did not name batch 2 as the resume "
                            "point (got %r)" % result["resume_from"])
        if not os.path.exists(os.path.join(broke, "batch-01",
                                           "claude-envelope.json")):
            findings.append("the exit-25 stop did not leave batch-01 on disk")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_ask.py",
        description="koreanize batch machinery -- split a universe, invoke the ask "
                    "shim once per batch, budget-check between them (design §3.7).")
    parser.add_argument("--ask-dir", help="ai/<stage>/<stamp>/ built by a host stage")
    parser.add_argument("--replay", metavar="DIR",
                        help="seed --ask-dir from a recorded run and walk it with no "
                             "claude, no credential and no cost")
    parser.add_argument("--no-invoke", action="store_true",
                        help="walk the batches without invoking the shim; report the "
                             "first missing batch as resume_from")
    parser.add_argument("--claude-bin", help="override the shim's binary resolution")
    parser.add_argument("--readonly-tree", action="append", default=[],
                        help="a tree the agent must not move; repeatable (rule 8c)")
    parser.add_argument("--json-only", action="store_true",
                        help="emit the result JSON on stdout and nothing else")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        print("kz_ask --selftest")
        findings = selftest()
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        print("  ok: partition total/disjoint/bounded, ceiling and universe_size "
              "refusals, budget stop at 25, resume from the first missing batch, "
              "every budget knob mapped to a flag the shim parses")
        return kc.EXIT_OK

    kc.check_invocation_guards([args.ask_dir] if args.ask_dir else [])

    if not args.ask_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    if args.replay:
        seed_from_replay(args.ask_dir, args.replay)

    result = run_bundle(args.ask_dir,
                        invoke=not (args.no_invoke or args.replay),
                        claude_bin=args.claude_bin,
                        readonly_trees=args.readonly_tree,
                        quiet=args.quiet)

    if args.json_only:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result["exit_code"]

    if not args.quiet:
        print("ask: %s -- batches %d of %d complete"
              % (result["stage"], sum(1 for b in result["batches"] if b["complete"]),
                 result["batch_of"]))
        for batch in result["batches"]:
            print("  batch-%02d %-9s %s"
                  % (batch["batch"], "complete" if batch["complete"] else "pending",
                     batch["reason"] or (batch["session_id"] or "")))
        print("  cost   : %.2f USD over %ds" % (result["cost_usd"],
                                                result["elapsed_s"]))
        if result["stopped_by"]:
            print("  STOPPED between batches: %s" % result["stopped_by"])
            print("  re-run the stage to resume at batch %d" % result["resume_from"])
        elif result["resume_from"]:
            print("  resume_from: batch %d" % result["resume_from"])
    return result["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
