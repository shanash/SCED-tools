#!/usr/bin/env python3
"""Pure core of the nightly-sync schedule control: validate, derive, render, compare.

Part of the ``sync-schedule-control`` feature
(.am/sync-schedule-control/design.md §3, §5). This module is the half with no
side effects on the live machine: every subcommand is a text-in/text-out
transform over ``SCED-tools/config/sync-schedule.json``, so the whole validator
and every renderer is exercisable offline under pytest. The operator CLI that
owns the side effects -- ``plutil``, ``launchctl``, ``git``, ``gh``, backups,
the workspace lock -- is ``sced-schedule.sh``.

The schedule used to live in six independent hardcodes across three systems
(two launchd plists, the driver's SCHED_HHMM/LATEST_HHMM constants, two GitHub
Actions cron lines, and a documentation table), bound by three ordering
constraints that nothing enforced. The worst failure was silent: editing only a
plist left ``daily-sync-local.sh``'s staleness guard on its old bound, so
launchd fired and the run died at exit 4 with a grey ``stale-skip`` that the
notify throttle then suppressed. This config is the single source; the plist
renderer emits the trigger and the guard bound into the *same file* so the two
cannot be produced out of step.

What it does, per subcommand:

  validate        load the config, run every rule in design §3.3, report each
                  by stable id. Fail-closed: any error means nothing is written.
  derive          emit the computed schedule (window, CI time, cron expression,
                  UTC day shift) as JSON for the shell half to consume.
  set             mutate one repo's schedule and re-validate the WHOLE document
                  before writing it back atomically.
  render-plist    emit the complete LaunchAgent plist for one repo. The renderer
                  is the sole author of that file.
  render-cron     emit the 5-field cron expression for one repo's CI fallback.
  assert-plist    read a `plutil -convert json` decode on stdin and assert it
                  semantically matches the config -- the gate that runs BEFORE
                  a rendered plist is allowed anywhere near launchd.
  patch-workflow  rewrite the single `- cron:` line of a workflow file, byte
                  faithful everywhere else.
  patch-doc       rewrite the four `When (KST)` cells of the CLAUDE.md schedule
                  table, byte faithful everywhere else.
  compare         given an observation document describing the live world,
                  classify every site as ok / drift / unknown / advisory.

KST has no DST, so the KST->UTC map is a constant -9 hours forever; that is why
a fixed cron expression is safe here and would not be in a DST zone. The
validator refuses to run if the machine's own UTC offset stops matching the
config.

Usage:
  sced_schedule.py validate     [--config FILE] [--strict] [--allow-round-minute] [--json]
  sced_schedule.py derive       [--config FILE] [--repo NAME] [--json]
  sced_schedule.py set          [--config FILE] --repo NAME [--at HH:MM] [--latest HH:MM]
                                [--ci-offset MIN] [--days LIST] [--ack-collision TEXT]
                                [--updated-at ISO8601] [--dry-run] [--strict]
  sced_schedule.py render-plist [--config FILE] --repo NAME [--home DIR]
  sced_schedule.py render-cron  [--config FILE] --repo NAME
  sced_schedule.py assert-plist [--config FILE] --repo NAME [--decoded FILE] [--home DIR]
  sced_schedule.py patch-workflow [--config FILE] --repo NAME --in FILE [--out FILE]
  sced_schedule.py patch-doc    [--config FILE] --in FILE [--out FILE]
  sced_schedule.py compare      [--config FILE] --observations FILE [--strict] [--sites LIST]

Exit codes:
   0  success; for `compare`: every site consistent with the config
   1  usage error
   2  precondition failed (config missing / bad JSON / wrong schema / tz drift)
  10  drift found (`compare`), or `set --dry-run` would change something
  11  the proposed schedule violates an invariant -- nothing was written
  50  render or semantic assertion failed
  70  patch failed (structure assertion on the target file)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from sced_io import atomic_write_json, atomic_write_text

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_PRECONDITION = 2
EXIT_DRIFT = 10
EXIT_INVARIANT = 11
EXIT_RENDER = 50
EXIT_PATCH = 70

SCHEMA = 1
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
QUARTER_MINUTES = (0, 15, 30, 45)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "sync-schedule.json"


class ConfigError(Exception):
    """The config could not be loaded or is structurally unusable."""


# --------------------------------------------------------------------- loading


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"config unreadable: {path}: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ConfigError("config root must be an object")
    if cfg.get("schema") != SCHEMA:
        raise ConfigError(f"unsupported config schema {cfg.get('schema')!r} (expected {SCHEMA})")
    for key in ("timezone", "utc_offset_min", "launchd", "policy", "repos"):
        if key not in cfg:
            raise ConfigError(f"config is missing required key {key!r}")
    if not cfg["repos"]:
        raise ConfigError("config defines no repos")
    return cfg


def repo_names(cfg: dict) -> list:
    """Repos in policy order, then any not named there (so nothing is skipped)."""
    order = [r for r in cfg["policy"].get("order", []) if r in cfg["repos"]]
    return order + [r for r in cfg["repos"] if r not in order]


def repo_cfg(cfg: dict, repo: str) -> dict:
    try:
        return cfg["repos"][repo]
    except KeyError:
        raise ConfigError(f"unknown repo {repo!r} (have: {', '.join(sorted(cfg['repos']))})") from None


# ------------------------------------------------------------------ derivation


def hhmm_to_min(hhmm: str) -> int:
    """`"0247"` -> 167. Raises ValueError on anything that is not HHMM."""
    if not isinstance(hhmm, str) or not re.fullmatch(r"\d{4}", hhmm):
        raise ValueError(f"expected a 4-digit HHMM string, got {hhmm!r}")
    hh, mm = int(hhmm[:2]), int(hhmm[2:])
    if hh > 23 or mm > 59:
        raise ValueError(f"{hhmm!r} is not a valid time of day")
    return hh * 60 + mm


def min_to_hhmm(total: int) -> str:
    total %= 1440
    return f"{total // 60:02d}{total % 60:02d}"


def min_to_colon(total: int) -> str:
    total %= 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_days(days) -> list:
    """`"*"` -> [] (daily). A list of day names -> sorted unique indices 0..6."""
    if days == "*":
        return []
    if not isinstance(days, list) or not days:
        raise ValueError(f"days must be \"*\" or a non-empty list of {'/'.join(DAY_NAMES)}")
    out = set()
    for name in days:
        if name not in DAY_NAMES:
            raise ValueError(f"unsupported day {name!r}; expected one of {', '.join(DAY_NAMES)}")
        out.add(DAY_NAMES.index(name))
    return sorted(out)


def derive(cfg: dict, repo: str) -> dict:
    """Compute everything downstream of the two stored times. See design §5.1-5.2."""
    rc = repo_cfg(cfg, repo)
    offset = int(cfg["utc_offset_min"])

    local_min = hhmm_to_min(rc["local_hhmm"])
    latest_min = hhmm_to_min(rc["latest_hhmm"])
    ci_offset = int(rc["ci_offset_min"])

    window_min = latest_min - local_min
    ci_min_kst = local_min + ci_offset

    # The CI time may roll into tomorrow KST; converting to UTC can then roll it
    # back. KST = UTC+9, so the UTC shift is only ever 0 or -1.
    ci_day_shift_kst, ci_hm_kst = divmod(ci_min_kst, 1440)
    utc_raw = ci_hm_kst - offset
    utc_day_shift = -1 if utc_raw < 0 else 0
    utc_min = utc_raw % 1440
    day_shift = ci_day_shift_kst + utc_day_shift

    day_idx = parse_days(rc.get("days", "*"))
    if day_idx:
        shifted = sorted({(idx + day_shift) % 7 for idx in day_idx})
        dow_field = ",".join(str(idx) for idx in shifted)
    else:
        dow_field = "*"

    return {
        "repo": repo,
        "local_hhmm": rc["local_hhmm"],
        "latest_hhmm": rc["latest_hhmm"],
        "local": min_to_colon(local_min),
        "latest": min_to_colon(latest_min),
        "local_min": local_min,
        "latest_min": latest_min,
        "window_min": window_min,
        "ci_offset_min": ci_offset,
        "ci_kst": min_to_colon(ci_hm_kst),
        "ci_utc": min_to_colon(utc_min),
        "cron_utc": f"{utc_min % 60} {utc_min // 60} * * {dow_field}",
        "cron_minute": utc_min % 60,
        "cron_hour": utc_min // 60,
        "utc_day_shift": utc_day_shift,
        "day_shift": day_shift,
        "days": rc.get("days", "*"),
        "launchd_label": rc["launchd_label"],
        "env_suffix": rc["env_suffix"],
        "sched_env_key": f"SCED_SYNC_SCHED_{rc['env_suffix']}",
        "latest_env_key": f"SCED_SYNC_LATEST_START_{rc['env_suffix']}",
    }


def derive_all(cfg: dict) -> dict:
    return {repo: derive(cfg, repo) for repo in repo_names(cfg)}


# ------------------------------------------------------------------ validation


def _finding(rule_id, status, detail, severity="error"):
    return {"id": rule_id, "status": status, "severity": severity, "detail": detail}


def machine_utc_offset_min() -> int:
    """The running machine's current UTC offset, in minutes east of UTC."""
    is_dst = time.localtime().tm_isdst > 0
    seconds_west = time.altzone if is_dst else time.timezone
    return -seconds_west // 60


def validate(cfg: dict, strict: bool = False, allow_round_minute: bool = False,
             check_tz: bool = True) -> list:
    """Run every rule of design §3.3. Returns findings; `error` means refuse."""
    findings = []
    policy = cfg["policy"]
    reserve = policy.get("reserve", {})
    ai_stage = int(reserve.get("ai_stage_min", 0))
    run_reserve = int(reserve.get("run_reserve_min", 0))
    ai_claude = int(reserve.get("ai_claude_min", 0))

    # tz.drift -- a machine moved to another zone must be caught, not silently
    # mis-scheduled: every cron expression here was computed for a fixed offset.
    if check_tz:
        actual = machine_utc_offset_min()
        if actual != int(cfg["utc_offset_min"]):
            findings.append(_finding(
                "tz.drift", "fail",
                f"config declares utc_offset_min={cfg['utc_offset_min']} "
                f"({cfg.get('timezone')}) but this machine is at {actual}"))
        else:
            findings.append(_finding("tz.drift", "ok", f"machine offset {actual} matches config"))

    derived = {}
    for repo in repo_names(cfg):
        rc = repo_cfg(cfg, repo)

        # hhmm.format / days.unsupported -- structural, so they gate everything
        # else for this repo.
        try:
            derived[repo] = derive(cfg, repo)
        except ValueError as exc:
            rule = "days.unsupported" if "day" in str(exc) else "hhmm.format"
            findings.append(_finding(rule, "fail", f"{repo}: {exc}"))
            continue
        findings.append(_finding("hhmm.format", "ok",
                                 f"{repo}: {rc['local_hhmm']}/{rc['latest_hhmm']}"))

        d = derived[repo]

        # env.suffix -- must match the key the driver already reads at
        # daily-sync-local.sh:151-154, or the override silently does nothing.
        expected_suffix = repo.upper().replace("-", "_")
        if rc.get("env_suffix") != expected_suffix:
            findings.append(_finding("env.suffix", "fail",
                                     f"{repo}: env_suffix={rc.get('env_suffix')!r}, "
                                     f"expected {expected_suffix!r}"))
        else:
            findings.append(_finding("env.suffix", "ok", f"{repo}: {expected_suffix}"))

        # window.order / window.no-wrap -- daily-sync-local.sh:521-524 is
        # `NOW > LATEST || NOW < SCHED-5` with no wraparound, so a window that
        # crosses midnight makes EVERY minute skip. Hard reject, not a warning.
        if d["window_min"] <= 0:
            findings.append(_finding(
                "window.no-wrap", "fail",
                f"{repo}: latest {d['latest']} is not after local {d['local']}; the staleness "
                f"guard has no midnight wraparound, so every minute would skip"))
        else:
            findings.append(_finding("window.order", "ok",
                                     f"{repo}: {d['local']} -> {d['latest']} ({d['window_min']}m)"))

            min_window = int(policy.get("min_window_min", 0))
            if d["window_min"] < min_window:
                findings.append(_finding("window.min", "fail",
                                         f"{repo}: window {d['window_min']}m < {min_window}m"))
            else:
                findings.append(_finding("window.min", "ok",
                                         f"{repo}: window {d['window_min']}m >= {min_window}m"))

        # window.floor -- below 00:05 the `SCHED_MIN - 5` at :524 goes negative
        # and the lower bound silently vanishes.
        if d["local_min"] < 5:
            findings.append(_finding("window.floor", "fail",
                                     f"{repo}: local {d['local']} is under 00:05, so the guard's "
                                     f"SCHED-5 lower bound goes negative and stops applying"))
        else:
            findings.append(_finding("window.floor", "ok", f"{repo}: local {d['local']} >= 00:05"))

        # ci.offset / ci.margin
        min_ci = int(policy.get("min_ci_offset_min", 0))
        if d["ci_offset_min"] < min_ci:
            findings.append(_finding("ci.offset", "fail",
                                     f"{repo}: ci_offset {d['ci_offset_min']}m < {min_ci}m"))
        else:
            findings.append(_finding("ci.offset", "ok",
                                     f"{repo}: ci_offset {d['ci_offset_min']}m >= {min_ci}m"))

        min_margin = int(policy.get("min_ci_margin_min", 0))
        needed = max(d["window_min"], 0) + min_margin
        if d["ci_offset_min"] < needed:
            findings.append(_finding("ci.margin", "fail",
                                     f"{repo}: ci_offset {d['ci_offset_min']}m < window "
                                     f"{d['window_min']}m + margin {min_margin}m"))
        else:
            findings.append(_finding("ci.margin", "ok",
                                     f"{repo}: ci_offset {d['ci_offset_min']}m >= {needed}m"))

        # minute.zero -- GitHub delays and sometimes drops schedules on the hour
        # (CLAUDE.md:79). Applies to both the local trigger and the cron.
        round_hits = []
        if d["local_min"] % 60 == 0:
            round_hits.append(f"local {d['local']}")
        if d["cron_minute"] == 0:
            round_hits.append(f"cron minute 0 ({d['cron_utc']})")
        if policy.get("forbid_zero_minute", True) and round_hits:
            findings.append(_finding(
                "minute.zero", "warn" if allow_round_minute else "fail",
                f"{repo}: {', '.join(round_hits)} -- the :00 slot is congested and GitHub "
                f"drops runs there",
                severity="warn" if allow_round_minute else "error"))
        else:
            findings.append(_finding("minute.zero", "ok", f"{repo}: off the hour"))

        if policy.get("warn_quarter_minute", True):
            quarters = [n for n in (d["local_min"] % 60, d["cron_minute"]) if n in QUARTER_MINUTES]
            if quarters and not round_hits:
                findings.append(_finding("minute.quarter", "warn",
                                         f"{repo}: minute {quarters[0]} is a congested slot",
                                         severity="warn"))
            else:
                findings.append(_finding("minute.quarter", "ok", f"{repo}: not a quarter slot"))

        # ai.ci-gap -- reserve room for the future AI overlap-resolution stage
        # (.am/ai-rebase-conflict-resolution) between the latest possible local
        # start and the CI fallback.
        if d["window_min"] > 0:
            need = d["window_min"] + ai_stage
            if d["ci_offset_min"] < need:
                findings.append(_finding("ai.ci-gap", "fail",
                                         f"{repo}: ci_offset {d['ci_offset_min']}m leaves no room for "
                                         f"the AI stage (window {d['window_min']}m + {ai_stage}m)"))
            else:
                slack = d["ci_offset_min"] - need
                findings.append(_finding(
                    "ai.ci-gap", "warn" if slack < 5 else "ok",
                    f"{repo}: ci_offset {d['ci_offset_min']}m vs {need}m needed (slack {slack}m)",
                    severity="warn" if slack < 5 else "error"))
                if slack < 5:
                    findings.append(_finding("ai.slack", "warn",
                                             f"{repo}: only {slack}m of AI-stage slack on the CI gap",
                                             severity="warn"))

        # collision -- the envelope a run can occupy once the AI stage exists,
        # against every other scheduled `claude` consumer on this machine.
        env_start = d["local_min"]
        env_end = d["latest_min"] + ai_stage
        for coll in policy.get("collisions", []):
            try:
                c_start = hhmm_to_min(coll["hhmm"])
            except (ValueError, KeyError) as exc:
                findings.append(_finding("collision", "fail", f"malformed collision entry: {exc}"))
                continue
            c_end = c_start + int(coll.get("budget_min", 0))
            overlap = min(env_end, c_end) - max(env_start, c_start)
            if overlap <= 0:
                findings.append(_finding("collision", "ok",
                                         f"{repo}: clear of {coll['label']}"))
                continue
            ack = rc.get("collision_ack") or {}
            ack_for = f"{rc['local_hhmm']}/{rc['latest_hhmm']}"
            detail = (f"{repo}: envelope [{d['local']}..{min_to_colon(env_end)}] overlaps "
                      f"{coll['label']} {coll['hhmm'][:2]}:{coll['hhmm'][2:]}"
                      f"+{coll.get('budget_min', 0)}m by {overlap}m")
            if strict:
                findings.append(_finding("collision", "fail", detail + " (--strict ignores acks)"))
            elif ack.get("ack_for") == ack_for:
                findings.append(_finding(
                    "collision", "ack",
                    detail + f" -- acknowledged {ack.get('at', '?')}: {ack.get('reason', '')}",
                    severity="ack"))
            else:
                findings.append(_finding(
                    "collision", "fail",
                    detail + (" -- the acknowledgement is stale (it covers "
                              f"{ack.get('ack_for')!r}, not {ack_for!r})"
                              if ack else " -- unacknowledged")))

    # stagger -- consecutive repos in policy order share a workspace-wide lock
    # (daily-sync-local.sh:480-509), so the second must not start inside the
    # first's run.
    min_stagger = int(policy.get("min_stagger_min", 0))
    order = [r for r in repo_names(cfg) if r in derived]
    for prev, cur in zip(order, order[1:]):
        gap = derived[cur]["local_min"] - derived[prev]["local_min"]
        if gap < min_stagger:
            findings.append(_finding("stagger", "fail",
                                     f"{prev} +{gap}m -> {cur}: needs at least {min_stagger}m"))
        else:
            findings.append(_finding("stagger", "ok",
                                     f"{prev} +{gap}m <= {cur} (slack {gap - min_stagger}m)"))

    # ai.stagger -- the same gap must also absorb a full AI stage plus the rest
    # of a run once the stage lands.
    if min_stagger < ai_stage + run_reserve:
        findings.append(_finding("ai.stagger", "fail",
                                 f"min_stagger_min {min_stagger}m cannot hold the AI stage "
                                 f"({ai_stage}m) plus the run reserve ({run_reserve}m)"))
    else:
        findings.append(_finding("ai.stagger", "ok",
                                 f"min_stagger_min {min_stagger}m >= {ai_stage + run_reserve}m"))

    # ai.claude-inner -- the inner `claude` wall clock is the DOMINANT term of the
    # AI stage under the claude-driven rebase (.am/claude-driven-rebase-deploy
    # design §5.9), so leaving it unvalidated is the wrong thing to leave
    # unvalidated. Two minutes is the measured fixed overhead of the stage's
    # deterministic phases: shadow seed, classification, decide, the nine checks
    # and the attestation commit. `sced-schedule.sh verify` separately checks that
    # SCED_SYNC_AI_TIMEOUT in ~/.config/sced-sync/env is <= ai_claude_min * 60.
    if ai_claude < 1:
        findings.append(_finding("ai.claude-inner", "fail",
                                 f"reserve.ai_claude_min is {ai_claude}; the inner `claude` "
                                 f"bound must be at least 1 minute"))
    elif ai_claude + 2 > ai_stage:
        findings.append(_finding("ai.claude-inner", "fail",
                                 f"ai_claude_min {ai_claude}m + 2m of deterministic overhead "
                                 f"exceeds ai_stage_min {ai_stage}m"))
    else:
        findings.append(_finding("ai.claude-inner", "ok",
                                 f"ai_claude_min {ai_claude}m + 2m <= ai_stage_min {ai_stage}m "
                                 f"(slack {ai_stage - ai_claude - 2}m)"))

    # failover.window -- the GHA failover watchdog
    # (.am/local-failure-gha-failover/design.md §3.3).
    #
    # Severity is `warn` throughout, deliberately: these times govern the LATENCY of
    # a recovery path, never whether a rebase or a release is safe, so `set` must
    # never refuse because of them. Same posture as minute.quarter.
    #
    # The bound is PER REPO, not global. An earlier draft of the design wrote it as
    # "every watchdog_hhmm < min(ci_kst)", which is wrong: min(ci_kst) is
    # SCED-downloads' 03:17, and the design's own 03:35 -- correct, because gate 2
    # makes it ineligible for SCED-downloads anyway -- violates it. A firing only has
    # to precede the CI cron of a repo it is actually eligible for.
    fo = policy.get("failover") or {}
    if not fo:
        findings.append(_finding("failover.window", "ok",
                                 "no policy.failover block; the failover is not configured",
                                 severity="warn"))
    else:
        grace = int(fo.get("watchdog_grace_min", 0))
        try:
            wd_mins = [(h, hhmm_to_min(h)) for h in fo.get("watchdog_hhmm", [])]
        except (ValueError, TypeError) as exc:
            wd_mins = []
            findings.append(_finding("failover.window", "fail",
                                     f"malformed watchdog_hhmm: {exc}"))
        for repo, d in derived.items():
            lo = d["latest_min"] + grace
            hi = d["local_min"] + d["ci_offset_min"]
            covering = [h for h, m in wd_mins if lo <= m < hi]
            if covering:
                findings.append(_finding(
                    "failover.window", "ok",
                    f"{repo}: {'/'.join(covering)} lands in "
                    f"[{min_to_colon(lo)},{min_to_colon(hi)})"))
            else:
                findings.append(_finding(
                    "failover.window", "warn",
                    f"{repo}: no watchdog firing in [{min_to_colon(lo)},{min_to_colon(hi)}) "
                    f"-- latest start {d['latest']} + grace {grace}m to CI cron "
                    f"{min_to_colon(hi)}; this repo has no bounded-latency recovery",
                    severity="warn"))

        # A firing must not sit on a local trigger or a latest-start boundary, and
        # must not be on the hour -- same reasoning as minute.zero.
        boundaries = {d["local_min"] for d in derived.values()} | {
            d["latest_min"] for d in derived.values()}
        for h, m in wd_mins:
            if m in boundaries:
                findings.append(_finding("failover.window", "warn",
                                         f"watchdog {h} coincides with a local trigger "
                                         f"or latest-start boundary", severity="warn"))
            if m % 60 == 0 and policy.get("forbid_zero_minute", True):
                findings.append(_finding("failover.window", "warn",
                                         f"watchdog {h} is on the hour", severity="warn"))

        # A firing inside a declared collision budget needs a pinned acknowledgement.
        # The pin is the (label, hhmm, watchdog_hhmm) triple, so moving either time
        # invalidates it -- identical semantics to the driver's own collision_ack.
        acks = fo.get("collision_ack") or []
        for coll in policy.get("collisions", []):
            try:
                c_start = hhmm_to_min(coll["hhmm"])
            except (ValueError, KeyError):
                continue
            c_end = c_start + int(coll.get("budget_min", 0))
            for h, m in wd_mins:
                if not (c_start <= m < c_end):
                    continue
                pinned = any(a.get("label") == coll["label"]
                             and a.get("hhmm") == coll["hhmm"]
                             and a.get("watchdog_hhmm") == h
                             for a in acks)
                detail = (f"watchdog {h} falls inside {coll['label']} "
                          f"{coll['hhmm'][:2]}:{coll['hhmm'][2:]}"
                          f"+{coll.get('budget_min', 0)}m")
                if strict:
                    findings.append(_finding("failover.window", "warn",
                                             detail + " (--strict ignores acks)",
                                             severity="warn"))
                elif pinned:
                    findings.append(_finding("failover.window", "ack", detail + " -- acknowledged",
                                             severity="ack"))
                else:
                    findings.append(_finding("failover.window", "warn",
                                             detail + " -- unacknowledged", severity="warn"))

    if strict:
        for f in findings:
            if f["status"] == "warn":
                f["status"] = "fail"
                f["severity"] = "error"

    return findings


def has_errors(findings: list) -> bool:
    return any(f["status"] == "fail" for f in findings)


# ------------------------------------------------------------------- rendering


def _expand_home(path: str, home: str) -> str:
    if path.startswith("~/"):
        return home.rstrip("/") + path[1:]
    return path


def _stagger_sentence(cfg: dict, repo: str) -> str:
    """Reproduce the rationale sentence the hand-written plists carried."""
    order = repo_names(cfg)
    idx = order.index(repo)
    if idx > 0:
        other = order[idx - 1]
        gap = derive(cfg, repo)["local_min"] - derive(cfg, other)["local_min"]
        return (f" and {gap} minutes after the {other} agent, so\n"
                f"\t     the two never contend for the workspace lock.")
    if len(order) > 1:
        other = order[idx + 1]
        gap = derive(cfg, other)["local_min"] - derive(cfg, repo)["local_min"]
        return f" and {gap} minutes ahead of the {other} agent."
    return "."


def render_plist(cfg: dict, repo: str, home: str) -> str:
    """The complete LaunchAgent plist. This renderer is the file's sole author.

    The trigger and the two staleness-guard bounds are emitted together, from one
    config record, in one pass: there is no code path here that writes a
    StartCalendarInterval without the matching SCED_SYNC_SCHED_* bound. That
    co-location is the interlock (design §1.3) -- it makes the historical silent
    divergence impossible to produce rather than merely detectable.
    """
    rc = repo_cfg(cfg, repo)
    d = derive(cfg, repo)
    ld = cfg["launchd"]
    label = rc["launchd_label"]
    wrapper = _expand_home(ld["wrapper"], home)
    log = f"{ld['log_dir'].rstrip('/')}/sced-daily-sync-{repo}.log"
    hour, minute = d["local_min"] // 60, d["local_min"] % 60

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        "<!-- GENERATED by SCED-tools/scripts/sced-schedule.sh from",
        f"     SCED-tools/config/sync-schedule.json (schema {SCHEMA}, source updated",
        f"     {cfg.get('updated_at', 'unknown')}). Hand edits are overwritten by",
        "     `sced-schedule.sh apply --local`; `sced-schedule.sh verify` detects drift. -->",
        '<plist version="1.0">',
        "<dict>",
        "\t<key>Label</key>",
        f"\t<string>{label}</string>",
        "",
        "\t<!-- One agent per repository rather than one agent with two calendar entries.",
        "\t     launchd does not tell the job which entry fired, so a shared agent would",
        "\t     have to infer the repository from the clock - and that inference is wrong",
        "\t     exactly when it matters, after a wake coalesces missed firings. -->",
        "\t<key>ProgramArguments</key>",
        "\t<array>",
        f"\t\t<string>{ld['program']}</string>",
        f"\t\t<string>{wrapper}</string>",
        "\t\t<string>--repo</string>",
        f"\t\t<string>{repo}</string>",
        "\t</array>",
        "",
        f"\t<!-- {d['local']} KST, {d['ci_offset_min']} minutes ahead of this repo's GitHub "
        f"Actions fallback",
        f"\t     ({d['ci_utc']} UTC = {d['ci_kst']} KST){_stagger_sentence(cfg, repo)} -->",
        "\t<key>StartCalendarInterval</key>",
        "\t<dict>",
        "\t\t<key>Hour</key>",
        f"\t\t<integer>{hour}</integer>",
        "\t\t<key>Minute</key>",
        f"\t\t<integer>{minute}</integer>",
        "\t</dict>",
        "",
        "\t<!-- On the boot volume on purpose: if /Volumes/PRO-G40 is not mounted,",
        "\t     launchd must still be able to open this file, or the wrapper's own",
        "\t     failure message goes nowhere. -->",
        "\t<key>StandardOutPath</key>",
        f"\t<string>{log}</string>",
        "\t<key>StandardErrorPath</key>",
        f"\t<string>{log}</string>",
        "",
        "\t<key>ProcessType</key>",
        f"\t<string>{ld['process_type']}</string>",
        "",
        "\t<!-- git-lfs must be on PATH: the global core.hooksPath pre-push hook exits 2",
        "\t     without it, which would block every push the driver makes. /usr/sbin and",
        "\t     /sbin are needed for df and mount. -->",
        "\t<key>EnvironmentVariables</key>",
        "\t<dict>",
        "\t\t<key>PATH</key>",
        f"\t\t<string>{ld['path_env']}</string>",
        "\t\t<!-- INTERLOCK: the staleness-guard bounds travel with the trigger above.",
        "\t\t     daily-sync-local.sh:151-154 reads these; if the trigger fires without",
        "\t\t     them the driver exits 5 (loud) rather than 4 (a grey stale-skip the",
        "\t\t     notify throttle then suppresses). -->",
        f"\t\t<key>{d['sched_env_key']}</key>",
        f"\t\t<string>{rc['local_hhmm']}</string>",
        f"\t\t<key>{d['latest_env_key']}</key>",
        f"\t\t<string>{rc['latest_hhmm']}</string>",
        "\t</dict>",
        "",
        "\t<!-- Deliberately absent: RunAtLoad (a login must not trigger a release) and",
        "\t     WorkingDirectory (it would point at the external volume, so launchd could",
        "\t     not spawn the job at all while that volume is unmounted). -->",
        "</dict>",
        "</plist>",
    ]
    return "\n".join(lines) + "\n"


def render_cron(cfg: dict, repo: str) -> str:
    return derive(cfg, repo)["cron_utc"]


def assert_plist(cfg: dict, repo: str, decoded: dict, home: str) -> list:
    """Semantic gate run on a `plutil -convert json` decode BEFORE the swap.

    A rendered file that passes `plutil -lint` can still be catastrophically
    wrong -- a dropped PATH breaks every push, a stray RunAtLoad publishes a
    public release at login. These assertions are the reason the dangerous
    bootout->bootstrap window can only ever be entered with a good file.
    """
    rc = repo_cfg(cfg, repo)
    d = derive(cfg, repo)
    ld = cfg["launchd"]
    errors = []

    expected_keys = {
        "Label", "ProgramArguments", "StartCalendarInterval",
        "StandardOutPath", "StandardErrorPath", "ProcessType", "EnvironmentVariables",
    }
    actual_keys = set(decoded)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        if missing:
            errors.append(f"missing key(s): {', '.join(missing)}")
        if extra:
            errors.append(f"unexpected key(s): {', '.join(extra)}")

    for forbidden in ld.get("forbidden_keys", []):
        if forbidden in decoded:
            errors.append(f"forbidden key present: {forbidden}")

    if decoded.get("Label") != rc["launchd_label"]:
        errors.append(f"Label is {decoded.get('Label')!r}, expected {rc['launchd_label']!r}")

    args = decoded.get("ProgramArguments")
    expected_args = [ld["program"], _expand_home(ld["wrapper"], home), "--repo", repo]
    if args != expected_args:
        errors.append(f"ProgramArguments is {args!r}, expected {expected_args!r}")

    sci = decoded.get("StartCalendarInterval") or {}
    if not isinstance(sci, dict):
        errors.append("StartCalendarInterval is not a dict (a list of entries is not supported)")
    else:
        want = {"Hour": d["local_min"] // 60, "Minute": d["local_min"] % 60}
        got = {k: sci.get(k) for k in ("Hour", "Minute")}
        if got != want:
            errors.append(f"StartCalendarInterval is {got}, expected {want}")

    env = decoded.get("EnvironmentVariables") or {}
    if env.get("PATH") != ld["path_env"]:
        errors.append("EnvironmentVariables.PATH does not match config.launchd.path_env")
    if env.get(d["sched_env_key"]) != rc["local_hhmm"]:
        errors.append(f"{d['sched_env_key']} is {env.get(d['sched_env_key'])!r}, "
                      f"expected {rc['local_hhmm']!r}")
    if env.get(d["latest_env_key"]) != rc["latest_hhmm"]:
        errors.append(f"{d['latest_env_key']} is {env.get(d['latest_env_key'])!r}, "
                      f"expected {rc['latest_hhmm']!r}")

    log = f"{ld['log_dir'].rstrip('/')}/sced-daily-sync-{repo}.log"
    for key in ("StandardOutPath", "StandardErrorPath"):
        if decoded.get(key) != log:
            errors.append(f"{key} is {decoded.get(key)!r}, expected {log!r}")
    if decoded.get("ProcessType") != ld["process_type"]:
        errors.append(f"ProcessType is {decoded.get('ProcessType')!r}, "
                      f"expected {ld['process_type']!r}")

    return errors


# --------------------------------------------------------------------- patching

CRON_LINE = re.compile(r'^(?P<pre>\s*-\s*cron:\s*")(?P<expr>[^"]*)(?P<post>")(?P<gap>[ \t]*)'
                       r'(?P<comment>#.*)?$')
UTC_KST_SPAN = re.compile(r'\d{2}:\d{2}\s+UTC\s*=\s*\d{2}:\d{2}\s+KST')


class PatchError(Exception):
    """The target file does not have the structure the patcher requires."""


# The workflow header states WHERE the file must live. That is derivable from
# `ci_ref`, so the block is rendered rather than hand-maintained -- the same
# sole-author model as the plist comments, and for the same reason: the claim
# went stale the moment the forks' default branch became `korean`, and a stale
# claim in a header is read as fact.
#
# HEADER_STALE is the recognised predecessor, kept only so the migration can run
# once per fork. It may be deleted once both forks carry the rendered form; the
# patcher refuses (rather than guesses) when it finds neither.
HEADER_STALE = (
    "# MUST live on the default branch (main): scheduled workflows only run from the\n"
    "# default branch. It is never merged into `korean`, so it cannot affect a rebase.\n"
)


def render_header(branch: str) -> str:
    return (
        f"# MUST live on the default branch ({branch}): scheduled workflows only ever run\n"
        f"# from the default branch, and a workflow file that is not on it is not\n"
        f"# registered at all -- `gh workflow list` omits it and the cron never fires,\n"
        f"# silently. It therefore rides the branch it rebases, which makes it one more\n"
        f"# path in the fork-changed set: an upstream file at this path would trip the\n"
        f"# overlap gate. Upstream has none.\n"
    )


def default_branch(ci_ref: str) -> str:
    """`origin/korean` -> `korean`."""
    return ci_ref.rsplit("/", 1)[-1]


def patch_workflow(cfg: dict, repo: str, text: str) -> str:
    """Rewrite the single `- cron:` line inside `on:` -> `schedule:`.

    Deliberately a line-scoped text edit, not a YAML round-trip: PyYAML is not a
    dependency of this workspace, and re-emitting the document would strip the
    comments and the column alignment that make the 470-line file readable. The
    same byte-fidelity discipline as apply-revert-decisions.py's dumps_faithful.
    """
    d = derive(cfg, repo)
    lines = text.split("\n")

    # Scope the search to the `schedule:` block under a top-level `on:`.
    in_on = False
    in_schedule = False
    hits = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"on:\s*", line) or re.fullmatch(r'"on":\s*', line):
            in_on, in_schedule = True, False
            continue
        if in_on and line and not line[0].isspace() and stripped:
            in_on, in_schedule = False, False
            continue
        if in_on and re.fullmatch(r"\s+schedule:\s*", line):
            in_schedule = True
            continue
        if in_schedule:
            if stripped and not stripped.startswith(("-", "#")):
                in_schedule = False
                continue
            if CRON_LINE.match(line):
                hits.append(i)

    if len(hits) != 1:
        raise PatchError(f"cron.ambiguous: expected exactly one `- cron:` line inside "
                         f"on:->schedule:, found {len(hits)}")

    i = hits[0]
    m = CRON_LINE.match(lines[i])
    old_expr = m.group("expr")
    new_expr = d["cron_utc"]
    comment = m.group("comment") or ""
    if comment:
        comment = UTC_KST_SPAN.sub(f"{d['ci_utc']} UTC = {d['ci_kst']} KST", comment)

    # Keep the comment in its original column: the expression's length changed,
    # so re-pad by the difference. Never collapse below two spaces.
    gap = m.group("gap")
    if comment:
        pad = len(gap) + len(old_expr) - len(new_expr)
        gap = " " * max(pad, 2)

    lines[i] = f'{m.group("pre")}{new_expr}{m.group("post")}{gap}{comment}'.rstrip() \
        if not comment else f'{m.group("pre")}{new_expr}{m.group("post")}{gap}{comment}'
    out = "\n".join(lines)

    # The header block is rendered, not edited in place: accept either the
    # recognised stale form or the already-correct one, and refuse anything else
    # rather than pattern-match its way through prose it does not understand.
    branch = default_branch(repo_cfg(cfg, repo)["ci_ref"])
    current = render_header(branch)
    if current in out:
        return out
    if HEADER_STALE in out:
        return out.replace(HEADER_STALE, current, 1)
    raise PatchError(
        "header.unrecognised: the 'MUST live on the default branch' block matches neither "
        "the rendered form nor the known stale one; refusing to rewrite prose it cannot verify")


DOC_TABLE_HEADER = "| Tier | Runner | Repo | When (KST) | Definition |"


def unmark(cell: str) -> str:
    """Strip the markdown code span the doc table wraps repo names in."""
    return cell.strip().strip("`").strip()


def patch_doc(cfg: dict, text: str) -> str:
    """Rewrite only column 4 of the CLAUDE.md schedule table.

    The table is located by its exact header and the row count is asserted, so a
    restructured document fails loudly instead of being silently mangled. Prose
    around the table is never touched -- a regex rewrite of an argument is how
    contract documents get quietly falsified.
    """
    lines = text.split("\n")
    try:
        head = next(i for i, line in enumerate(lines) if line.strip() == DOC_TABLE_HEADER)
    except StopIteration:
        raise PatchError("doc.table-missing: the schedule table header was not found") from None

    start = head + 2  # header, separator, then rows
    rows = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append(i)
        i += 1

    expected_rows = 2 * len(repo_names(cfg))
    if len(rows) != expected_rows:
        raise PatchError(f"doc.rows: expected {expected_rows} data rows after the header, "
                         f"found {len(rows)}")

    derived = derive_all(cfg)
    for idx in rows:
        cells = [c.strip() for c in lines[idx].strip().strip("|").split("|")]
        if len(cells) != 5:
            raise PatchError(f"doc.columns: row {idx + 1} has {len(cells)} columns, expected 5")
        tier, repo = unmark(cells[0]), unmark(cells[2])
        if tier not in ("primary", "fallback"):
            raise PatchError(f"doc.tier: row {idx + 1} has unknown tier {tier!r}")
        if repo not in derived:
            raise PatchError(f"doc.repo: row {idx + 1} names unknown repo {repo!r}")
        d = derived[repo]
        cells[3] = d["local"] if tier == "primary" else f"{d['ci_kst']} (`{d['cron_utc']}` UTC)"
        lines[idx] = "| " + " | ".join(cells) + " |"

    return "\n".join(lines)


# ------------------------------------------------------------------ collection

# `launchctl print` is an unversioned human-facing format, so every parse here
# is defensive: anything it cannot read becomes `parsed: false`, which the
# comparator classifies as `unknown` and counts as drift. A comparator that
# cannot see a site must never report that site as correct.
LC_PATH = re.compile(r"^\tpath = (.+)$", re.M)
LC_ENV_BLOCK = re.compile(r"^\tenvironment = \{\n(.*?)^\t\}$", re.M | re.S)
LC_ENV_ENTRY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*) => (.*)$", re.M)
LC_DESCRIPTOR = re.compile(r"descriptor = \{\n(.*?)\}", re.S)
LC_DESC_ENTRY = re.compile(r'"([A-Za-z]+)"\s*=>\s*(-?\d+)')


def parse_launchctl(text: str) -> dict:
    out = {"present": True, "parsed": True, "path": None,
           "hour": None, "minute": None, "env": {}}

    m = LC_PATH.search(text)
    if m:
        out["path"] = m.group(1).strip()

    # `environment` only -- NOT `inherited environment` or `default environment`,
    # which is why the pattern is anchored to a single leading tab.
    m = LC_ENV_BLOCK.search(text)
    if m:
        out["env"] = {k: v.strip() for k, v in LC_ENV_ENTRY.findall(m.group(1))}
    else:
        out["parsed"] = False

    pairs = set()
    for block in LC_DESCRIPTOR.findall(text):
        entries = dict(LC_DESC_ENTRY.findall(block))
        if "Hour" in entries and "Minute" in entries:
            pairs.add((int(entries["Hour"]), int(entries["Minute"])))
    if len(pairs) == 1:
        out["hour"], out["minute"] = pairs.pop()
    else:
        # Zero means the trigger could not be read; more than one means a shared
        # agent with several calendar entries, which the plist renderer never
        # produces and the comparator must not silently average.
        out["parsed"] = False
    return out


CRON_ANY = re.compile(r'^\s*-\s*cron:\s*"([^"]*)"')
DRIVER_CASE = re.compile(
    r'^\s*(?P<repo>[A-Za-z][A-Za-z-]*"?\)?)\s*'
    r'SCHED_HHMM=(?:"?\$\{SCED_SYNC_SCHED_[A-Z_]+:-)?(?P<sched>\d{4})',
    re.M)
DRIVER_LATEST = re.compile(r'LATEST_HHMM="\$\{SCED_SYNC_LATEST_START_[A-Z_]+:-(?P<latest>\d{4})\}"')
DOC_ROW = re.compile(r"^\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$")
PROSE_STAGGER = re.compile(r"The (\d+)-minute gap between the two local agents")
PROSE_CI = re.compile(r"the (\d+)-minute gap to the fallback")
PROSE_WINDOW = re.compile(r"outside the (\d{2}:\d{2}/\d{2}:\d{2}) window")


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def collect(cfg: dict, raw: Path, meta: dict) -> dict:
    """Turn the raw artifacts the shell gathered into an observation document.

    The shell runs the side-effecting commands (`plutil`, `launchctl`, `git
    show`, `gh api`) and drops their unmodified output here; all parsing lives
    on this side so it is fixture-testable without a live machine.
    """
    obs = {"probes": meta.get("probes", {}), "plist_disk": {}, "launchd": {},
           "ci_cron": {}, "ci_registration": {}, "driver_defaults": {},
           "driver_stop_msg": {}, "env_file": meta.get("env_file"),
           "docs_path": meta.get("docs_path")}

    for repo in repo_names(cfg):
        rc = repo_cfg(cfg, repo)
        label = rc["launchd_label"]
        plist_path = meta.get("plist_paths", {}).get(repo, f"~/Library/LaunchAgents/{label}.plist")

        xml = _read(raw / f"plist-{repo}.xml")
        decoded_text = _read(raw / f"plist-{repo}.json")
        decoded = None
        if decoded_text:
            try:
                decoded = json.loads(decoded_text)
            except ValueError:
                decoded = None
        obs["plist_disk"][repo] = {
            "exists": xml is not None, "path": plist_path,
            "raw": xml, "decoded": decoded,
        }

        if not meta.get("probes", {}).get("launchd", True):
            obs["launchd"][repo] = {"probed": False,
                                    "target": f"gui/{meta.get('uid', '?')}/{label}"}
        else:
            lc = _read(raw / f"launchctl-{repo}.txt")
            entry = {"target": f"gui/{meta.get('uid', '?')}/{label}"}
            if lc is None:
                entry["present"] = False
            else:
                entry.update(parse_launchctl(lc))
            obs["launchd"][repo] = entry

        wf = _read(raw / f"workflow-{repo}.yml")
        if wf is None:
            obs["ci_cron"][repo] = {"available": False,
                                    "detail": f"could not read {rc['ci_ref']}:{rc['workflow_path']}"}
        else:
            hits = [m.group(1) for m in (CRON_ANY.match(l) for l in wf.split("\n")) if m]
            obs["ci_cron"][repo] = ({"available": True, "expr": hits[0],
                                     "fetched_at": meta.get("fetched_at", "offline")}
                                    if len(hits) == 1 else
                                    {"available": False,
                                     "detail": f"expected exactly one cron line, found {len(hits)}"})

        gh_text = _read(raw / f"gh-{repo}.json")
        if gh_text is None:
            obs["ci_registration"][repo] = {"checked": False}
        else:
            try:
                gh = json.loads(gh_text)
            except ValueError:
                gh = {}
            obs["ci_registration"][repo] = {
                "checked": True,
                "default_branch": gh.get("default_branch"),
                "found": bool(gh.get("state")),
                "state": gh.get("state"),
            }

    driver = _read(raw / "driver-case.txt")
    if driver:
        for line in driver.split("\n"):
            m = DRIVER_CASE.search(line)
            if not m:
                continue
            repo = m.group("repo").rstrip(')"')
            if repo not in cfg["repos"]:
                continue
            lm = DRIVER_LATEST.search(line)
            obs["driver_defaults"][repo] = {"sched": m.group("sched"),
                                            "latest": lm.group("latest") if lm else None}

    stop = _read(raw / "driver-stop.txt")
    if stop:
        times = re.findall(r"echo (\d{2}:\d{2})", stop)
        names = re.findall(r'== (SCED[A-Za-z-]*) \]\]', stop)
        # `[[ "${REPO}" == SCED ]] && echo 03:47 || echo 03:17` -- the named repo
        # takes the first time, the other takes the second.
        if len(times) == 2 and len(names) == 1 and names[0] in cfg["repos"]:
            other = [r for r in repo_names(cfg) if r != names[0]]
            obs["driver_stop_msg"][names[0]] = times[0]
            if other:
                obs["driver_stop_msg"][other[0]] = times[1]

    keys = _read(raw / "env-keys.txt")
    obs["env_shadow_keys"] = ([k.strip().rstrip("=") for k in keys.split("\n") if k.strip()]
                              if keys is not None else None)

    # Absent file = the key is unset, which is legal (the stage falls back to its
    # own default). Only a SET value can drift.
    ai_timeout = _read(raw / "env-ai-timeout.txt")
    obs["env_ai_timeout_s"] = int(ai_timeout.strip()) if ai_timeout and ai_timeout.strip() else None

    docs = _read(raw / "docs.md")
    if docs is None:
        obs["docs_table"] = None
        obs["docs_prose"] = None
    else:
        rows = []
        lines = docs.split("\n")
        try:
            head = next(i for i, l in enumerate(lines) if l.strip() == DOC_TABLE_HEADER)
        except StopIteration:
            head = None
        if head is not None:
            i = head + 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                m = DOC_ROW.match(lines[i].strip())
                if m:
                    rows.append({"tier": m.group(1).strip(), "repo": m.group(3).strip(),
                                 "when": m.group(4).strip()})
                i += 1
        obs["docs_table"] = rows if rows else None
        s, c, w = PROSE_STAGGER.search(docs), PROSE_CI.search(docs), PROSE_WINDOW.search(docs)
        obs["docs_prose"] = {
            "stagger_min": int(s.group(1)) if s else None,
            "ci_offset_min": int(c.group(1)) if c else None,
            "window": w.group(1) if w else None,
        }

    return obs


# ------------------------------------------------------------------- comparison


def _site(site_id, site_no, kind, repo, target, expected, actual, status,
          managed, detail="", cls=None, patch=None):
    row = {"id": site_id, "site": site_no, "kind": kind, "repo": repo, "target": target,
           "expected": expected, "actual": actual, "status": status, "managed": managed,
           "detail": detail}
    if cls is not None:
        row["class"] = cls
    if patch is not None:
        row["patch"] = patch
    return row


def compare(cfg: dict, obs: dict, home: str, strict: bool = False) -> list:
    """Classify every write site against the config. See design §5.5.

    An observation that could not be parsed is `unknown`, which counts as drift.
    It is never silently reported as `ok`: a comparator that cannot see a site
    must not claim it is correct.
    """
    sites = []
    derived = derive_all(cfg)

    for repo, d in derived.items():
        rc = repo_cfg(cfg, repo)
        label = rc["launchd_label"]
        want = f"{rc['local_hhmm']}/{rc['local_hhmm']}-{rc['latest_hhmm']}"
        site_no = repo_names(cfg).index(repo) + 1

        # --- sites 1/2: the plist on disk
        p = (obs.get("plist_disk") or {}).get(repo) or {}
        target = p.get("path", f"~/Library/LaunchAgents/{label}.plist")
        if not p.get("exists"):
            sites.append(_site(str(site_no), site_no, "plist-disk", repo, target,
                               want, "(absent)", "drift", "write",
                               "the LaunchAgent file does not exist"))
        else:
            decoded = p.get("decoded")
            if not isinstance(decoded, dict):
                sites.append(_site(str(site_no), site_no, "plist-disk", repo, target,
                                   want, "(undecodable)", "unknown", "write",
                                   "plutil could not decode the file"))
            else:
                errs = assert_plist(cfg, repo, decoded, home)
                sci = decoded.get("StartCalendarInterval") or {}
                env = decoded.get("EnvironmentVariables") or {}
                got = (f"{sci.get('Hour', '?')}:{sci.get('Minute', '?')}"
                       f"/{env.get(d['sched_env_key'], '-')}-{env.get(d['latest_env_key'], '-')}")
                if errs:
                    sites.append(_site(str(site_no), site_no, "plist-disk", repo, target,
                                       want, got, "drift", "write", "; ".join(errs),
                                       cls="semantic"))
                elif p.get("raw") is not None and p["raw"] != render_plist(cfg, repo, home):
                    sites.append(_site(str(site_no), site_no, "plist-disk", repo, target,
                                       want, got, "drift", "write",
                                       "decodes correctly but the bytes differ from a fresh "
                                       "render (stale comment or banner)", cls="cosmetic"))
                else:
                    sites.append(_site(str(site_no), site_no, "plist-disk", repo, target,
                                       want, got, "ok", "write", "", cls=None))

        # --- sites 1L/2L: what launchd actually loaded
        lp = (obs.get("launchd") or {}).get(repo) or {}
        ltarget = lp.get("target", f"gui/<uid>/{label}")
        if lp.get("probed") is False:
            sites.append(_site(f"{site_no}L", site_no, "launchd-loaded", repo, ltarget,
                               want, "(not probed)", "advisory", "write",
                               "--no-launchd-probe was given"))
        elif not lp.get("present"):
            sites.append(_site(f"{site_no}L", site_no, "launchd-loaded", repo, ltarget,
                               want, "(not loaded)", "drift", "write",
                               "the agent is not loaded; `launchctl bootstrap` it"))
        elif lp.get("parsed") is False:
            sites.append(_site(f"{site_no}L", site_no, "launchd-loaded", repo, ltarget,
                               want, "(unparseable)", "unknown", "write",
                               "`launchctl print` output could not be parsed"))
        else:
            lenv = lp.get("env") or {}
            got = (f"{lp.get('hour', '?')}:{lp.get('minute', '?')}"
                   f"/{lenv.get(d['sched_env_key'], '-')}-{lenv.get(d['latest_env_key'], '-')}")
            problems = []
            # (a) the interlock check itself: loaded trigger vs loaded bound.
            # This needs no config at all, and it is the one comparison that
            # catches a hand-edited plist that was reloaded.
            loaded_sched = lenv.get(d["sched_env_key"])
            if loaded_sched is None:
                problems.append(f"INTERLOCK: no {d['sched_env_key']} in the loaded environment; "
                                f"the trigger and the staleness guard have diverged")
            elif lp.get("hour") is not None and lp.get("minute") is not None:
                try:
                    if hhmm_to_min(loaded_sched) != int(lp["hour"]) * 60 + int(lp["minute"]):
                        problems.append(
                            f"INTERLOCK: loaded trigger {lp['hour']:02d}:{lp['minute']:02d} does not "
                            f"match the loaded bound {loaded_sched}")
                except (ValueError, TypeError):
                    problems.append(f"INTERLOCK: loaded bound {loaded_sched!r} is not HHMM")
            # (b) both against the config.
            if lp.get("hour") != d["local_min"] // 60 or lp.get("minute") != d["local_min"] % 60:
                problems.append(f"loaded trigger is {lp.get('hour')}:{lp.get('minute')}, "
                                f"config says {d['local']}")
            if loaded_sched not in (None, rc["local_hhmm"]):
                problems.append(f"loaded {d['sched_env_key']}={loaded_sched}, "
                                f"config says {rc['local_hhmm']}")
            if lenv.get(d["latest_env_key"]) not in (None, rc["latest_hhmm"]):
                problems.append(f"loaded {d['latest_env_key']}={lenv.get(d['latest_env_key'])}, "
                                f"config says {rc['latest_hhmm']}")
            # (c) loaded from the file we manage.
            if lp.get("path") and p.get("path") and lp["path"] != p["path"]:
                problems.append(f"loaded from {lp['path']}, not {p['path']}")
            sites.append(_site(f"{site_no}L", site_no, "launchd-loaded", repo, ltarget, want, got,
                               "drift" if problems else "ok", "write",
                               "; ".join(problems) or "trigger and env agree"))

    # --- sites 3/4 and 3G/4G: the CI cron and its registration
    for repo, d in derived.items():
        rc = repo_cfg(cfg, repo)
        site_no = 3 + repo_names(cfg).index(repo)
        target = f"{rc['gh_repo']} {rc['ci_ref']}:{rc['workflow_path']}"
        c = (obs.get("ci_cron") or {}).get(repo) or {}
        if not c.get("available"):
            sites.append(_site(str(site_no), site_no, "ci-cron", repo, target,
                               d["cron_utc"], "(unavailable)", "unknown", "report",
                               c.get("detail", "the workflow file could not be read")))
        elif c.get("expr") != d["cron_utc"]:
            sites.append(_site(str(site_no), site_no, "ci-cron", repo, target,
                               d["cron_utc"], c.get("expr", ""), "drift", "report",
                               "run `sced-schedule.sh apply --ci --yes` to push the new cron"))
        else:
            sites.append(_site(str(site_no), site_no, "ci-cron", repo, target,
                               d["cron_utc"], c["expr"], "ok", "report",
                               f"fetched {c.get('fetched_at', 'offline')}"))

        g = (obs.get("ci_registration") or {}).get(repo) or {}
        gtarget = f"{rc['gh_repo']} actions/workflows"
        if not g.get("checked"):
            sites.append(_site(f"{site_no}G", site_no, "ci-registration", repo, gtarget,
                               "korean + active", "(not checked)", "advisory", "report",
                               "pass --gh to query GitHub"))
        else:
            problems = []
            if g.get("default_branch") != "korean":
                problems.append(f"default branch is {g.get('default_branch')!r}, not 'korean' -- a "
                                f"workflow off the default branch is NOT registered and the cron "
                                f"never fires")
            if not g.get("found"):
                problems.append("the workflow is not listed by the Actions API")
            elif g.get("state") != "active":
                problems.append(f"workflow state is {g.get('state')!r}")
            sites.append(_site(f"{site_no}G", site_no, "ci-registration", repo, gtarget,
                               "korean + active",
                               f"{g.get('default_branch')} + {g.get('state')}",
                               "drift" if problems else "ok", "report",
                               "; ".join(problems) or "registered and active"))

    # --- site 5D: the driver's literal fallbacks
    dd = obs.get("driver_defaults") or {}
    for repo, d in derived.items():
        rc = repo_cfg(cfg, repo)
        got = dd.get(repo) or {}
        target = "SCED-tools/scripts/daily-sync-local.sh:151-154"
        if not got:
            sites.append(_site("5D", 5, "driver-default", repo, target,
                               f"{rc['local_hhmm']}/{rc['latest_hhmm']}", "(unread)",
                               "unknown", "report", "the constants block could not be parsed"))
            continue
        actual = f"{got.get('sched', '-')}/{got.get('latest', '-')}"
        expected = f"{rc['local_hhmm']}/{rc['latest_hhmm']}"
        if actual != expected:
            patch = (f'  {repo}) SCHED_HHMM="${{{d["sched_env_key"]}:-{rc["local_hhmm"]}}}"; '
                     f'LATEST_HHMM="${{{d["latest_env_key"]}:-{rc["latest_hhmm"]}}}" ;;')
            sites.append(_site("5D", 5, "driver-default", repo, target, expected, actual,
                               "drift" if strict else "advisory", "report",
                               "manual-run fallback only -- launchd now gets the value from the "
                               "plist, and a missing bound exits 5 rather than skipping silently",
                               patch=patch))
        else:
            sites.append(_site("5D", 5, "driver-default", repo, target, expected, actual,
                               "ok", "report"))

    # --- site 5E: a schedule key in the 0600 env file would shadow the plist
    keys = obs.get("env_shadow_keys")
    etarget = obs.get("env_file", "~/.config/sced-sync/env")
    if keys is None:
        sites.append(_site("5E", 5, "env-shadow", None, etarget, "(no schedule keys)",
                           "(unread)", "advisory", "report", "the env file was not readable"))
    else:
        bad = [k for k in keys
               if k.startswith("SCED_SYNC_SCHED_") or k.startswith("SCED_SYNC_LATEST_START_")]
        if bad:
            sites.append(_site("5E", 5, "env-shadow", None, etarget, "(no schedule keys)",
                               ", ".join(sorted(bad)), "drift", "report",
                               "the env file is sourced with `set -a` AFTER launchd's environment "
                               "(daily-sync-local.sh:127-130), so these silently override the "
                               "plist. Remove them."))
        else:
            sites.append(_site("5E", 5, "env-shadow", None, etarget, "(no schedule keys)",
                               "(none)", "ok", "report"))

    # --- site 5F: the inner `claude` bound must fit reserve.ai_claude_min
    # This is the check that finally gives ai_claude_min a consumer (R-16). The
    # env file owns the runtime value; sync-schedule.json owns the bound that the
    # ai.claude-inner invariant validated against ai_stage_min. If the two
    # disagree, the stage can overrun the envelope the schedule was proved safe
    # for -- and the failure would only show up as a run that still held the
    # workspace lock when the sibling repo's job started.
    ai_claude_min = int((cfg["policy"].get("reserve") or {}).get("ai_claude_min", 0))
    cap_s = ai_claude_min * 60
    got_s = obs.get("env_ai_timeout_s")
    if got_s is None:
        sites.append(_site("5F", 5, "env-ai-timeout", None, etarget,
                           f"<= {cap_s}s", "(unset)", "ok", "report",
                           "unset: the stage uses its own default"))
    elif got_s > cap_s:
        sites.append(_site("5F", 5, "env-ai-timeout", None, etarget,
                           f"<= {cap_s}s", f"{got_s}s", "drift", "report",
                           f"SCED_SYNC_AI_TIMEOUT exceeds reserve.ai_claude_min "
                           f"({ai_claude_min}m). Either lower it or raise ai_claude_min "
                           f"in {cfg.get('_path', 'sync-schedule.json')} -- but raising it "
                           f"must keep ai_claude_min + 2 <= ai_stage_min."))
    else:
        sites.append(_site("5F", 5, "env-ai-timeout", None, etarget,
                           f"<= {cap_s}s", f"{got_s}s", "ok", "report"))

    # --- site 5S: the CI times quoted in the driver's STOP embed
    sm = obs.get("driver_stop_msg") or {}
    if sm:
        for repo, d in derived.items():
            got = sm.get(repo)
            if got is None:
                continue
            status = "ok" if got == d["ci_kst"] else ("drift" if strict else "advisory")
            sites.append(_site("5S", 5, "driver-stop-message", repo,
                               "SCED-tools/scripts/daily-sync-local.sh:604",
                               d["ci_kst"], got, status, "report",
                               "" if status == "ok" else
                               "wording of the overlap STOP Discord embed only; it can never "
                               "cause a wrong run"))

    # --- site 6: the CLAUDE.md schedule table
    table = obs.get("docs_table")
    dtarget = obs.get("docs_path", "CLAUDE.md:72-81")
    if table is None:
        sites.append(_site("6", 6, "docs-table", None, dtarget, "(4 cells)", "(unread)",
                           "unknown", "report", "the schedule table could not be read"))
    else:
        problems = []
        for row in table:
            repo = unmark(row.get("repo") or "")
            tier, when = unmark(row.get("tier") or ""), (row.get("when") or "").strip()
            if repo not in derived:
                problems.append(f"unknown repo {repo!r} in the table")
                continue
            d = derived[repo]
            want_cell = d["local"] if tier == "primary" else f"{d['ci_kst']} (`{d['cron_utc']}` UTC)"
            if when != want_cell:
                problems.append(f"{tier} {repo}: {when!r} != {want_cell!r}")
        sites.append(_site("6", 6, "docs-table", None, dtarget, "(4 cells)",
                           f"({len(table)} rows)", "drift" if problems else "ok", "report",
                           "; ".join(problems) or "table matches"))

    # --- site 6P: the prose that argues for the gaps
    prose = obs.get("docs_prose")
    if prose is not None:
        policy = cfg["policy"]
        problems = []
        if prose.get("stagger_min") is not None \
                and int(prose["stagger_min"]) != int(policy.get("min_stagger_min", 0)):
            problems.append(f"prose says a {prose['stagger_min']}-minute gap between the agents, "
                            f"config says {policy.get('min_stagger_min')}")
        if prose.get("ci_offset_min") is not None \
                and int(prose["ci_offset_min"]) != int(policy.get("min_ci_offset_min", 0)):
            problems.append(f"prose says a {prose['ci_offset_min']}-minute gap to the fallback, "
                            f"config says {policy.get('min_ci_offset_min')}")
        want_window = "/".join(derived[r]["local"] for r in repo_names(cfg))
        if prose.get("window") and prose["window"] != want_window:
            problems.append(f"prose quotes the window as {prose['window']!r}, now {want_window!r}")
        sites.append(_site("6P", 6, "docs-prose", None, dtarget, want_window,
                           prose.get("window", "?"),
                           ("drift" if strict else "advisory") if problems else "ok", "report",
                           "; ".join(problems) or "prose matches"))

    return sites


def compare_exit(sites: list) -> int:
    if any(s["status"] in ("drift", "unknown") for s in sites):
        return EXIT_DRIFT
    return EXIT_OK


# --------------------------------------------------------------------------- CLI


def _print_findings(findings: list, stream=sys.stdout) -> None:
    width = max((len(f["id"]) for f in findings), default=0)
    for f in findings:
        mark = {"ok": "ok  ", "warn": "WARN", "ack": "ACK ", "fail": "FAIL"}.get(f["status"], "?   ")
        print(f"  [{mark}] {f['id']:<{width}}  {f['detail']}", file=stream)


def _load_or_die(args) -> dict:
    try:
        return load_config(Path(args.config))
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(EXIT_PRECONDITION)


def cmd_validate(args) -> int:
    cfg = _load_or_die(args)
    findings = validate(cfg, strict=args.strict, allow_round_minute=args.allow_round_minute,
                        check_tz=not args.no_tz_check)
    bad = has_errors(findings)
    if args.json:
        print(json.dumps({"findings": findings, "ok": not bad}, indent=2, ensure_ascii=False))
    else:
        print(f"Config:  {args.config}")
        print(f"Repos:   {', '.join(repo_names(cfg))}")
        print()
        _print_findings(findings)
        print()
        print("INVALID: the schedule violates an invariant; nothing may be written."
              if bad else "OK: every invariant holds.")
    return EXIT_INVARIANT if bad else EXIT_OK


def cmd_derive(args) -> int:
    cfg = _load_or_die(args)
    repos = [args.repo] if args.repo else repo_names(cfg)
    try:
        out = {r: derive(cfg, r) for r in repos}
    except (ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    if args.json or args.repo is None:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        d = out[args.repo]
        print(f"local   {d['local']}  window -> {d['latest']} ({d['window_min']}m)")
        print(f"ci      {d['ci_kst']} KST = {d['ci_utc']} UTC  (day shift {d['utc_day_shift']})")
        print(f"cron    {d['cron_utc']}")
    return EXIT_OK


def cmd_set(args) -> int:
    cfg = _load_or_die(args)
    try:
        rc = repo_cfg(cfg, args.repo)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE

    before = json.dumps(cfg, sort_keys=True)

    def to_hhmm(value, flag):
        m = re.fullmatch(r"(\d{1,2}):?(\d{2})", value.strip())
        if not m:
            print(f"ERROR: {flag} expects HH:MM, got {value!r}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        return f"{int(m.group(1)):02d}{m.group(2)}"

    if args.at:
        rc["local_hhmm"] = to_hhmm(args.at, "--at")
    if args.latest:
        rc["latest_hhmm"] = to_hhmm(args.latest, "--latest")
    if args.ci_offset is not None:
        rc["ci_offset_min"] = int(args.ci_offset)
    if args.days:
        rc["days"] = "*" if args.days.strip() == "*" else [
            d.strip() for d in args.days.split(",") if d.strip()]
    if args.ack_collision:
        rc["collision_ack"] = {
            "ack_for": f"{rc['local_hhmm']}/{rc['latest_hhmm']}",
            "at": args.updated_at[:10] if args.updated_at else "unknown",
            "reason": args.ack_collision,
        }
    elif rc.get("collision_ack") and \
            rc["collision_ack"].get("ack_for") != f"{rc['local_hhmm']}/{rc['latest_hhmm']}":
        # A stale acknowledgement is worse than none: it reads as a decision that
        # was never made about the new time. Drop it and let validation re-ask.
        rc.pop("collision_ack")

    if args.updated_at:
        cfg["updated_at"] = args.updated_at
    cfg["updated_by"] = args.updated_by or "sced-schedule.sh"

    findings = validate(cfg, strict=args.strict, check_tz=not args.no_tz_check)
    if has_errors(findings):
        print("REFUSED: the proposed schedule violates an invariant. Nothing was written.\n",
              file=sys.stderr)
        _print_findings([f for f in findings if f["status"] != "ok"], sys.stderr)
        return EXIT_INVARIANT

    changed = json.dumps(cfg, sort_keys=True) != before
    d = derive(cfg, args.repo)
    print(f"{args.repo}: {d['local']} (window to {d['latest']}), "
          f"CI {d['ci_kst']} KST = cron `{d['cron_utc']}`")
    if not changed:
        print("no change")
        return EXIT_OK
    if args.dry_run:
        print("--dry-run: the config was NOT written")
        return EXIT_DRIFT
    atomic_write_json(Path(args.config), cfg)
    print(f"config updated: {args.config}")
    print("nothing is in effect yet -- run `sced-schedule.sh apply`")
    return EXIT_OK


def cmd_render_plist(args) -> int:
    cfg = _load_or_die(args)
    try:
        sys.stdout.write(render_plist(cfg, args.repo, args.home))
    except (ConfigError, ValueError, KeyError) as exc:
        print(f"ERROR: render failed: {exc}", file=sys.stderr)
        return EXIT_RENDER
    return EXIT_OK


def cmd_render_cron(args) -> int:
    cfg = _load_or_die(args)
    try:
        print(render_cron(cfg, args.repo))
    except (ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_RENDER
    return EXIT_OK


def cmd_assert_plist(args) -> int:
    cfg = _load_or_die(args)
    raw = Path(args.decoded).read_text(encoding="utf-8") if args.decoded else sys.stdin.read()
    try:
        decoded = json.loads(raw)
    except ValueError as exc:
        print(f"ERROR: the decoded plist is not JSON: {exc}", file=sys.stderr)
        return EXIT_RENDER
    errors = assert_plist(cfg, args.repo, decoded, args.home)
    if errors:
        print(f"ERROR: rendered plist for {args.repo} failed its semantic assertions:",
              file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return EXIT_RENDER
    print(f"ok: {args.repo} plist matches the config")
    return EXIT_OK


def cmd_patch_workflow(args) -> int:
    cfg = _load_or_die(args)
    src = Path(args.infile)
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        out = patch_workflow(cfg, args.repo, text)
    except (PatchError, ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PATCH

    # What actually changed, so the caller can describe the commit truthfully
    # instead of asserting a cron move that may not have happened.
    if args.report:
        def cron_of(t):
            for line in t.split("\n"):
                m = CRON_LINE.match(line)
                if m:
                    return m.group("expr")
            return None
        atomic_write_json(Path(args.report), {
            "changed": out != text,
            "cron_changed": cron_of(out) != cron_of(text),
            "header_changed": HEADER_STALE in text and HEADER_STALE not in out,
            "cron": cron_of(out),
        })

    if args.outfile:
        atomic_write_text(Path(args.outfile), out)
        print("no change" if out == text else f"patched -> {args.outfile}")
    else:
        sys.stdout.write(out)
    return EXIT_OK


def cmd_patch_doc(args) -> int:
    cfg = _load_or_die(args)
    src = Path(args.infile)
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        out = patch_doc(cfg, text)
    except (PatchError, ConfigError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PATCH
    if args.outfile:
        atomic_write_text(Path(args.outfile), out)
        print("no change" if out == text else f"patched -> {args.outfile}")
    else:
        sys.stdout.write(out)
    return EXIT_OK


def cmd_collect(args) -> int:
    cfg = _load_or_die(args)
    try:
        meta = json.loads(Path(args.meta).read_text(encoding="utf-8")) if args.meta else {}
    except (OSError, ValueError) as exc:
        print(f"ERROR: meta unreadable: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    obs = collect(cfg, Path(args.raw_dir), meta)
    if args.out:
        atomic_write_json(Path(args.out), obs)
    else:
        print(json.dumps(obs, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_compare(args) -> int:
    cfg = _load_or_die(args)
    try:
        obs = json.loads(Path(args.observations).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: observations unreadable: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION

    sites = compare(cfg, obs, args.home, strict=args.strict)
    if args.sites:
        wanted = {s.strip() for s in args.sites.split(",") if s.strip()}
        sites = [s for s in sites if s["id"] in wanted or str(s["site"]) in wanted]

    counts = {"ok": 0, "drift": 0, "unknown": 0, "advisory": 0}
    for s in sites:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    code = compare_exit(sites)

    findings = validate(cfg, strict=args.strict, check_tz=not args.no_tz_check)
    doc = {
        "schema": SCHEMA,
        "config_path": args.config,
        "config_updated_at": cfg.get("updated_at"),
        "strict": bool(args.strict),
        "probes": obs.get("probes", {}),
        "repos": derive_all(cfg),
        "invariants": findings,
        "sites": sites,
        "counts": counts,
        "drift_count": counts.get("drift", 0) + counts.get("unknown", 0),
        "exit": code,
    }
    print(json.dumps(doc, indent=2, ensure_ascii=False))
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sced_schedule.py", add_help=True,
                                description=__doc__.split("\n")[0])
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--home", default=str(Path.home()),
                   help="HOME used to expand ~ in launchd paths (tests override it)")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--no-tz-check", action="store_true",
                   help="skip the machine-offset check (fixtures are timezone-independent)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("validate")
    s.add_argument("--allow-round-minute", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("derive")
    s.add_argument("--repo")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_derive)

    s = sub.add_parser("set")
    s.add_argument("--repo", required=True)
    s.add_argument("--at")
    s.add_argument("--latest")
    s.add_argument("--ci-offset", type=int)
    s.add_argument("--days")
    s.add_argument("--ack-collision")
    s.add_argument("--updated-at")
    s.add_argument("--updated-by")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("render-plist")
    s.add_argument("--repo", required=True)
    s.set_defaults(func=cmd_render_plist)

    s = sub.add_parser("render-cron")
    s.add_argument("--repo", required=True)
    s.set_defaults(func=cmd_render_cron)

    s = sub.add_parser("assert-plist")
    s.add_argument("--repo", required=True)
    s.add_argument("--decoded", help="file holding the plutil JSON decode (default: stdin)")
    s.set_defaults(func=cmd_assert_plist)

    s = sub.add_parser("patch-workflow")
    s.add_argument("--repo", required=True)
    s.add_argument("--in", dest="infile", required=True)
    s.add_argument("--out", dest="outfile")
    s.add_argument("--report", help="write {changed, cron_changed, header_changed} JSON here")
    s.set_defaults(func=cmd_patch_workflow)

    s = sub.add_parser("patch-doc")
    s.add_argument("--in", dest="infile", required=True)
    s.add_argument("--out", dest="outfile")
    s.set_defaults(func=cmd_patch_doc)

    s = sub.add_parser("collect")
    s.add_argument("--raw-dir", required=True)
    s.add_argument("--meta")
    s.add_argument("--out")
    s.set_defaults(func=cmd_collect)

    s = sub.add_parser("compare")
    s.add_argument("--observations", required=True)
    s.add_argument("--sites")
    s.set_defaults(func=cmd_compare)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION


if __name__ == "__main__":
    sys.exit(main())
