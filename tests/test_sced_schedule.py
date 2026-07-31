"""pytest suite for sced_schedule.py (the sync-schedule-control pure core).

Design: .am/sync-schedule-control/design.md §6 S4. Everything here runs offline:
no launchd, no boot volume, no fork, no network. That is the point -- the
component whose entire job is refusing bad input must itself be testable without
a live machine to break.

Pure functions are imported directly (better failure messages); the CLI contract
-- exit codes, stdout/stderr split -- goes through subprocess, matching the house
style of the other suites here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TESTS_DIR.parent / "scripts"
FIXTURE_DIR = TESTS_DIR / "fixtures" / "sync-schedule"

SCRIPT = SCRIPT_DIR / "sced_schedule.py"
BASE_CONFIG = FIXTURE_DIR / "base-config.json"

sys.path.insert(0, str(SCRIPT_DIR))
import sced_schedule as ss  # noqa: E402

HOME = "/Users/fixture"


# --------------------------------------------------------------------- helpers


def run(*args, config=None):
    cmd = [sys.executable, str(SCRIPT), "--config", str(config or BASE_CONFIG),
           "--home", HOME, "--no-tz-check", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture
def cfg():
    return ss.load_config(BASE_CONFIG)


@pytest.fixture
def cfg_path(tmp_path):
    """A writable copy of the fixture config."""
    dst = tmp_path / "sync-schedule.json"
    dst.write_text(BASE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def mutate(tmp_path, name, **repo_overrides):
    """Write a variant of the base config with one repo's fields overridden."""
    doc = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    for repo, fields in repo_overrides.items():
        if repo == "policy":
            doc["policy"].update(fields)
        else:
            doc["repos"][repo].update(fields)
    dst = tmp_path / name
    dst.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return dst


def rule_status(findings, rule_id):
    return [f["status"] for f in findings if f["id"] == rule_id]


# ------------------------------------------------------------------- load/derive


def test_config_loads_and_orders_repos(cfg):
    assert ss.repo_names(cfg) == ["SCED-downloads", "SCED"]


def test_unsupported_schema_is_a_precondition_failure(tmp_path):
    doc = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    doc["schema"] = 99
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    r = run("validate", config=bad)
    assert r.returncode == ss.EXIT_PRECONDITION
    assert "schema" in r.stderr


def test_missing_config_is_a_precondition_failure(tmp_path):
    r = run("validate", config=tmp_path / "nope.json")
    assert r.returncode == ss.EXIT_PRECONDITION


@pytest.mark.parametrize("value", ["247", "02:47", "2470", "2461", "", None])
def test_hhmm_rejects_non_hhmm(value):
    with pytest.raises(ValueError):
        ss.hhmm_to_min(value)


# ---------------------------------------------------- the KST -> UTC day shift

@pytest.mark.parametrize("local,offset,cron,utc_shift,net_shift", [
    # The live SCED slot: 02:47 + 60 = 03:47 KST = 18:47 UTC on the PREVIOUS day.
    # The shift is -1 and irrelevant for a daily cron -- which is exactly why the
    # existing `47 18 * * *` is correct, and why the shift is reported rather
    # than left as folklore.
    ("0247", 60, "47 18 * * *", -1, -1),
    ("0217", 60, "17 18 * * *", -1, -1),
    # CI time rolls into tomorrow KST (+1), then the UTC conversion rolls it back
    # out (-1): the two legs cancel and the net shift is 0.
    ("2330", 60, "30 15 * * *", -1, 0),
    # Past 09:00 KST no roll-back is needed on either leg.
    ("1000", 60, "0 2 * * *", 0, 0),
])
def test_day_shift_arithmetic(tmp_path, local, offset, cron, utc_shift, net_shift):
    latest = ss.min_to_hhmm(ss.hhmm_to_min(local) + 40)
    path = mutate(tmp_path, f"d-{local}.json",
                  SCED={"local_hhmm": local, "latest_hhmm": latest, "ci_offset_min": offset})
    d = ss.derive(ss.load_config(path), "SCED")
    assert d["cron_utc"] == cron
    assert d["utc_day_shift"] == utc_shift
    assert d["day_shift"] == net_shift


def test_weekday_rotation_follows_the_day_shift(tmp_path):
    # 02:47 + 60 = 03:47 KST is 18:47 UTC the day BEFORE, so Mon,Wed in KST are
    # Sun,Tue in UTC.
    path = mutate(tmp_path, "days.json",
                  SCED={"local_hhmm": "0247", "latest_hhmm": "0327",
                        "days": ["Mon", "Wed"]})
    d = ss.derive(ss.load_config(path), "SCED")
    assert d["day_shift"] == -1
    assert d["cron_utc"] == "47 18 * * 0,2"


def test_day_of_month_is_rejected(tmp_path):
    path = mutate(tmp_path, "dom.json", SCED={"days": ["1st"]})
    r = run("validate", config=path)
    assert r.returncode == ss.EXIT_INVARIANT
    assert "days.unsupported" in r.stdout


# ---------------------------------------------------------------- validation

def test_base_fixture_is_clean(cfg):
    findings = ss.validate(cfg, check_tz=False)
    assert not ss.has_errors(findings), [f for f in findings if f["status"] == "fail"]


def test_latest_before_local_is_rejected_as_no_wrap(tmp_path, cfg):
    path = mutate(tmp_path, "wrap.json", SCED={"local_hhmm": "0230", "latest_hhmm": "0100"})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "window.no-wrap")
    assert ss.has_errors(findings)


def test_short_window_is_rejected(tmp_path):
    path = mutate(tmp_path, "short.json", SCED={"local_hhmm": "0147", "latest_hhmm": "0157"})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "window.min")


def test_window_floor_guards_the_negative_lower_bound(tmp_path):
    # Below 00:05 the driver's `SCHED_MIN - 5` goes negative and the guard's
    # lower bound silently stops applying.
    path = mutate(tmp_path, "floor.json",
                  SCED={"local_hhmm": "0002", "latest_hhmm": "0042"},
                  **{"SCED-downloads": {"local_hhmm": "0001", "latest_hhmm": "0041"}})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "window.floor")


def test_insufficient_stagger_is_rejected(tmp_path):
    path = mutate(tmp_path, "stagger.json", SCED={"local_hhmm": "0132", "latest_hhmm": "0212"})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "stagger")


def test_short_ci_offset_is_rejected(tmp_path):
    path = mutate(tmp_path, "ci.json", SCED={"ci_offset_min": 45})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "ci.offset")


def test_ci_margin_needs_room_beyond_the_window(tmp_path):
    # offset 60 with a 50-minute window leaves only 10 minutes of margin.
    path = mutate(tmp_path, "margin.json",
                  SCED={"local_hhmm": "0147", "latest_hhmm": "0237", "ci_offset_min": 60})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "ci.margin")


def test_round_minute_is_rejected_and_can_be_downgraded(tmp_path):
    path = mutate(tmp_path, "zero.json", SCED={"local_hhmm": "0200", "latest_hhmm": "0240"})
    cfg = ss.load_config(path)
    assert "fail" in rule_status(ss.validate(cfg, check_tz=False), "minute.zero")
    relaxed = ss.validate(cfg, allow_round_minute=True, check_tz=False)
    assert "warn" in rule_status(relaxed, "minute.zero")
    assert not ss.has_errors(relaxed)


def test_quarter_minute_warns_but_does_not_block(tmp_path):
    path = mutate(tmp_path, "quarter.json", SCED={"local_hhmm": "0145", "latest_hhmm": "0225"})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "warn" in rule_status(findings, "minute.quarter")
    assert not ss.has_errors(findings)


def test_strict_promotes_warnings_to_errors(tmp_path):
    path = mutate(tmp_path, "quarter2.json", SCED={"local_hhmm": "0145", "latest_hhmm": "0225"})
    findings = ss.validate(ss.load_config(path), strict=True, check_tz=False)
    assert ss.has_errors(findings)


def test_env_suffix_must_match_the_driver_key(tmp_path):
    path = mutate(tmp_path, "suffix.json", SCED={"env_suffix": "SCED_MAIN"})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "env.suffix")


def test_ai_stagger_needs_room_for_the_future_stage(tmp_path):
    path = mutate(tmp_path, "aistag.json", policy={"min_stagger_min": 20})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "ai.stagger")


# ---------------------------------------------------------------- collisions

# 02:15-02:45 clears SCED-downloads' envelope ([01:12 .. 01:52+15]) and lands
# inside SCED's ([01:47 .. 02:27+15]), so exactly one repo is contested.
COLLISION = {"label": "com.shanash.kod-auto-improve", "hhmm": "0215",
             "budget_min": 30, "why": "fixture"}


def _with_collision(tmp_path, name, ack=None):
    doc = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    doc["policy"]["collisions"] = [COLLISION]
    doc["repos"]["SCED"]["collision_ack"] = ack
    dst = tmp_path / name
    dst.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return dst


def test_unacknowledged_collision_is_rejected(tmp_path):
    # SCED's envelope is [01:47 .. 02:27+15] and the collision runs 02:00-02:30.
    path = _with_collision(tmp_path, "coll.json", ack=None)
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "collision")


def test_matching_acknowledgement_clears_the_collision(tmp_path):
    path = _with_collision(tmp_path, "coll-ack.json",
                           ack={"ack_for": "0147/0227", "at": "2026-07-31", "reason": "contained"})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "ack" in rule_status(findings, "collision")
    assert not ss.has_errors(findings)


def test_acknowledgement_is_invalidated_by_a_time_change(tmp_path):
    # The ack names the OLD pair, so it must not cover the new one.
    path = _with_collision(tmp_path, "coll-stale.json",
                           ack={"ack_for": "0247/0327", "at": "2026-07-31", "reason": "old"})
    findings = ss.validate(ss.load_config(path), check_tz=False)
    assert "fail" in rule_status(findings, "collision")
    assert any("stale" in f["detail"] for f in findings if f["id"] == "collision")


def test_strict_ignores_acknowledgements(tmp_path):
    path = _with_collision(tmp_path, "coll-strict.json",
                           ack={"ack_for": "0147/0227", "at": "2026-07-31", "reason": "contained"})
    findings = ss.validate(ss.load_config(path), strict=True, check_tz=False)
    assert "fail" in rule_status(findings, "collision")


# --------------------------------------------------------------- plist render

def test_rendered_plist_round_trips_through_assert(cfg):
    for repo in ss.repo_names(cfg):
        text = ss.render_plist(cfg, repo, HOME)
        # A hand decode of the same shape `plutil -convert json` produces.
        decoded = {
            "Label": cfg["repos"][repo]["launchd_label"],
            "ProgramArguments": ["/bin/bash", f"{HOME}/scripts/sced-daily-sync-launch.sh",
                                 "--repo", repo],
            "StartCalendarInterval": {
                "Hour": ss.hhmm_to_min(cfg["repos"][repo]["local_hhmm"]) // 60,
                "Minute": ss.hhmm_to_min(cfg["repos"][repo]["local_hhmm"]) % 60,
            },
            "StandardOutPath": f"/tmp/sced-daily-sync-{repo}.log",
            "StandardErrorPath": f"/tmp/sced-daily-sync-{repo}.log",
            "ProcessType": "Standard",
            "EnvironmentVariables": {
                "PATH": cfg["launchd"]["path_env"],
                f"SCED_SYNC_SCHED_{cfg['repos'][repo]['env_suffix']}":
                    cfg["repos"][repo]["local_hhmm"],
                f"SCED_SYNC_LATEST_START_{cfg['repos'][repo]['env_suffix']}":
                    cfg["repos"][repo]["latest_hhmm"],
            },
        }
        assert ss.assert_plist(cfg, repo, decoded, HOME) == []
        assert "<key>StartCalendarInterval</key>" in text
        assert f"SCED_SYNC_SCHED_{cfg['repos'][repo]['env_suffix']}" in text


def test_render_emits_trigger_and_bound_together(cfg):
    """The interlock: no render path produces one without the other."""
    for repo in ss.repo_names(cfg):
        text = ss.render_plist(cfg, repo, HOME)
        assert text.count("<key>StartCalendarInterval</key>") == 1
        assert text.count(f"<key>SCED_SYNC_SCHED_{cfg['repos'][repo]['env_suffix']}</key>") == 1
        assert text.count(
            f"<key>SCED_SYNC_LATEST_START_{cfg['repos'][repo]['env_suffix']}</key>") == 1


def test_render_never_emits_a_forbidden_key(cfg):
    for repo in ss.repo_names(cfg):
        text = ss.render_plist(cfg, repo, HOME)
        for forbidden in cfg["launchd"]["forbidden_keys"]:
            assert f"<key>{forbidden}</key>" not in text


@pytest.mark.parametrize("mangle,expect", [
    (lambda d: d.pop("EnvironmentVariables"), "missing key"),
    (lambda d: d["EnvironmentVariables"].pop("PATH"), "PATH"),
    (lambda d: d.__setitem__("RunAtLoad", True), "forbidden key"),
    (lambda d: d["StartCalendarInterval"].__setitem__("Hour", 9), "StartCalendarInterval"),
    (lambda d: d["EnvironmentVariables"].__setitem__("SCED_SYNC_SCHED_SCED", "0900"),
     "SCED_SYNC_SCHED_SCED"),
])
def test_assert_plist_catches_dangerous_decodes(cfg, mangle, expect):
    decoded = {
        "Label": "com.shanash.sced-daily-sync.SCED",
        "ProgramArguments": ["/bin/bash", f"{HOME}/scripts/sced-daily-sync-launch.sh",
                             "--repo", "SCED"],
        "StartCalendarInterval": {"Hour": 1, "Minute": 47},
        "StandardOutPath": "/tmp/sced-daily-sync-SCED.log",
        "StandardErrorPath": "/tmp/sced-daily-sync-SCED.log",
        "ProcessType": "Standard",
        "EnvironmentVariables": {
            "PATH": cfg["launchd"]["path_env"],
            "SCED_SYNC_SCHED_SCED": "0147",
            "SCED_SYNC_LATEST_START_SCED": "0227",
        },
    }
    mangle(decoded)
    errors = ss.assert_plist(cfg, "SCED", decoded, HOME)
    assert errors, "the mangled decode should not have passed"
    assert any(expect in e for e in errors), errors


def test_assert_plist_cli_exit_code(cfg, tmp_path):
    decoded = tmp_path / "decoded.json"
    decoded.write_text(json.dumps({"Label": "wrong"}), encoding="utf-8")
    r = run("assert-plist", "--repo", "SCED", "--decoded", str(decoded))
    assert r.returncode == ss.EXIT_RENDER


# ------------------------------------------------------------- workflow patch

def test_patch_workflow_is_a_byte_identical_noop_when_unchanged(cfg):
    src = (FIXTURE_DIR / "workflow-one-cron.yml").read_text(encoding="utf-8")
    # The fixture carries SCED's live cron; point the config at the same time.
    doc = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    doc["repos"]["SCED"].update({"local_hhmm": "0247", "latest_hhmm": "0327"})
    doc["repos"]["SCED-downloads"].update({"local_hhmm": "0217", "latest_hhmm": "0257"})
    assert ss.patch_workflow(doc, "SCED", src) == src


def test_patch_workflow_rewrites_expression_and_comment(cfg):
    src = (FIXTURE_DIR / "workflow-one-cron.yml").read_text(encoding="utf-8")
    out = ss.patch_workflow(cfg, "SCED", src)  # fixture config -> 02:47 KST CI
    line = next(l for l in out.split("\n") if "cron:" in l)
    assert '"47 17 * * *"' in line
    assert "17:47 UTC = 02:47 KST" in line
    assert "Off-the-hour per R12." in line, "the unrelated half of the comment must survive"


def test_patch_workflow_preserves_the_comment_column(cfg):
    src = (FIXTURE_DIR / "workflow-one-cron.yml").read_text(encoding="utf-8")
    before = next(l for l in src.split("\n") if "cron:" in l)
    after = next(l for l in ss.patch_workflow(cfg, "SCED", src).split("\n") if "cron:" in l)
    assert before.index("#") == after.index("#")


def test_patch_workflow_touches_exactly_one_line(cfg):
    src = (FIXTURE_DIR / "workflow-one-cron.yml").read_text(encoding="utf-8")
    out = ss.patch_workflow(cfg, "SCED", src)
    changed = [i for i, (a, b) in enumerate(zip(src.split("\n"), out.split("\n"))) if a != b]
    assert len(changed) == 1


@pytest.mark.parametrize("fixture", ["workflow-two-crons.yml", "workflow-no-cron.yml"])
def test_patch_workflow_refuses_ambiguous_files(cfg, fixture):
    src = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
    with pytest.raises(ss.PatchError) as exc:
        ss.patch_workflow(cfg, "SCED", src)
    assert "cron.ambiguous" in str(exc.value)


def test_patch_workflow_cli_exit_code(cfg):
    r = run("patch-workflow", "--repo", "SCED",
            "--in", str(FIXTURE_DIR / "workflow-two-crons.yml"))
    assert r.returncode == ss.EXIT_PATCH
    assert "cron.ambiguous" in r.stderr


# --------------------------------------------------------------- header block

def test_header_is_rendered_from_the_configured_ref(cfg):
    assert ss.default_branch("origin/korean") == "korean"
    assert "(korean)" in ss.render_header("korean")


def test_stale_header_is_migrated_and_the_false_sentence_goes(cfg):
    src = (FIXTURE_DIR / "workflow-header-stale.yml").read_text(encoding="utf-8")
    out = ss.patch_workflow(cfg, "SCED", src)
    assert "default branch (main)" not in out
    assert "It is never merged into `korean`" not in out, \
        "the claim is exactly backwards now that the file lives on korean"
    assert ss.render_header("korean") in out
    # The migration must not disturb the rest of the file.
    assert out.count("name: daily-upstream-sync") == 1
    assert "jobs:" in out


def test_header_migration_is_idempotent(cfg):
    src = (FIXTURE_DIR / "workflow-header-stale.yml").read_text(encoding="utf-8")
    once = ss.patch_workflow(cfg, "SCED", src)
    assert ss.patch_workflow(cfg, "SCED", once) == once


def test_unrecognised_header_is_refused_not_guessed(cfg):
    src = (FIXTURE_DIR / "workflow-header-unknown.yml").read_text(encoding="utf-8")
    with pytest.raises(ss.PatchError) as exc:
        ss.patch_workflow(cfg, "SCED", src)
    assert "header.unrecognised" in str(exc.value)


# ------------------------------------------------------------------ doc patch

def test_patch_doc_is_a_noop_when_the_table_matches(cfg):
    src = (FIXTURE_DIR / "docs-table.md").read_text(encoding="utf-8")
    assert ss.patch_doc(cfg, src) == src


def test_patch_doc_rewrites_only_column_four(tmp_path, cfg):
    src = (FIXTURE_DIR / "docs-table.md").read_text(encoding="utf-8")
    path = mutate(tmp_path, "doc.json", SCED={"local_hhmm": "0150", "latest_hhmm": "0230"})
    out = ss.patch_doc(ss.load_config(path), src)
    changed = [(a, b) for a, b in zip(src.split("\n"), out.split("\n")) if a != b]
    assert len(changed) == 2  # the primary row and the fallback row for SCED
    for _before, after in changed:
        assert "com.shanash.sced-daily-sync.SCED.plist" in after \
            or "daily-upstream-sync.yml" in after
    assert "| 01:50 |" in out
    assert "02:50 (`50 17 * * *` UTC)" in out


def test_patch_doc_refuses_a_restructured_table(cfg):
    src = (FIXTURE_DIR / "docs-table-3rows.md").read_text(encoding="utf-8")
    with pytest.raises(ss.PatchError) as exc:
        ss.patch_doc(cfg, src)
    assert "doc.rows" in str(exc.value)


def test_patch_doc_refuses_a_missing_table(cfg):
    with pytest.raises(ss.PatchError) as exc:
        ss.patch_doc(cfg, "no table here\n")
    assert "doc.table-missing" in str(exc.value)


# ------------------------------------------------------------------ comparator

def _clean_observations(cfg):
    obs = {"probes": {"launchd": True}, "plist_disk": {}, "launchd": {},
           "ci_cron": {}, "ci_registration": {}, "driver_defaults": {},
           "driver_stop_msg": {}, "env_shadow_keys": ["SCED_SYNC_DISCORD_WEBHOOK"],
           "docs_table": [], "docs_prose": None}
    for repo in ss.repo_names(cfg):
        rc = cfg["repos"][repo]
        d = ss.derive(cfg, repo)
        raw = ss.render_plist(cfg, repo, HOME)
        obs["plist_disk"][repo] = {
            "exists": True,
            "path": f"{HOME}/Library/LaunchAgents/{rc['launchd_label']}.plist",
            "raw": raw,
            "decoded": {
                "Label": rc["launchd_label"],
                "ProgramArguments": ["/bin/bash",
                                     f"{HOME}/scripts/sced-daily-sync-launch.sh", "--repo", repo],
                "StartCalendarInterval": {"Hour": d["local_min"] // 60,
                                          "Minute": d["local_min"] % 60},
                "StandardOutPath": f"/tmp/sced-daily-sync-{repo}.log",
                "StandardErrorPath": f"/tmp/sced-daily-sync-{repo}.log",
                "ProcessType": "Standard",
                "EnvironmentVariables": {
                    "PATH": cfg["launchd"]["path_env"],
                    d["sched_env_key"]: rc["local_hhmm"],
                    d["latest_env_key"]: rc["latest_hhmm"],
                },
            },
        }
        obs["launchd"][repo] = {
            "present": True, "parsed": True,
            "path": f"{HOME}/Library/LaunchAgents/{rc['launchd_label']}.plist",
            "hour": d["local_min"] // 60, "minute": d["local_min"] % 60,
            "env": {d["sched_env_key"]: rc["local_hhmm"],
                    d["latest_env_key"]: rc["latest_hhmm"]},
        }
        obs["ci_cron"][repo] = {"available": True, "expr": d["cron_utc"]}
        obs["ci_registration"][repo] = {"checked": True, "default_branch": "korean",
                                        "found": True, "state": "active"}
        obs["driver_defaults"][repo] = {"sched": rc["local_hhmm"], "latest": rc["latest_hhmm"]}
        obs["driver_stop_msg"][repo] = d["ci_kst"]
        obs["docs_table"].append({"tier": "primary", "repo": f"`{repo}`", "when": d["local"]})
        obs["docs_table"].append({"tier": "fallback", "repo": f"`{repo}`",
                                  "when": f"{d['ci_kst']} (`{d['cron_utc']}` UTC)"})
    return obs


def test_comparator_reports_a_clean_world_as_ok(cfg):
    sites = ss.compare(cfg, _clean_observations(cfg), HOME)
    bad = [s for s in sites if s["status"] not in ("ok", "advisory")]
    assert not bad, bad
    assert ss.compare_exit(sites) == ss.EXIT_OK


def test_comparator_catches_a_plist_edited_but_never_reloaded(cfg):
    """The failure that is invisible on disk: the file is right, launchd is stale."""
    obs = _clean_observations(cfg)
    obs["launchd"]["SCED"]["hour"] = 9
    obs["launchd"]["SCED"]["minute"] = 5
    sites = ss.compare(cfg, obs, HOME)
    loaded = next(s for s in sites if s["id"] == "2L")
    assert loaded["status"] == "drift"
    assert "INTERLOCK" in loaded["detail"]
    assert ss.compare_exit(sites) == ss.EXIT_DRIFT


def test_comparator_catches_a_missing_interlock_bound(cfg):
    obs = _clean_observations(cfg)
    d = ss.derive(cfg, "SCED")
    obs["launchd"]["SCED"]["env"].pop(d["sched_env_key"])
    sites = ss.compare(cfg, obs, HOME)
    loaded = next(s for s in sites if s["id"] == "2L")
    assert loaded["status"] == "drift"
    assert "INTERLOCK" in loaded["detail"]


def test_comparator_catches_an_env_file_shadow(cfg):
    obs = _clean_observations(cfg)
    obs["env_shadow_keys"] = ["SCED_SYNC_DISCORD_WEBHOOK", "SCED_SYNC_SCHED_SCED"]
    sites = ss.compare(cfg, obs, HOME)
    shadow = next(s for s in sites if s["id"] == "5E")
    assert shadow["status"] == "drift"
    assert "set -a" in shadow["detail"]


def test_comparator_catches_a_non_default_branch(cfg):
    obs = _clean_observations(cfg)
    obs["ci_registration"]["SCED"]["default_branch"] = "main"
    sites = ss.compare(cfg, obs, HOME)
    reg = next(s for s in sites if s["id"] == "4G")
    assert reg["status"] == "drift"
    assert "NOT registered" in reg["detail"]


def test_comparator_catches_an_inactive_workflow(cfg):
    obs = _clean_observations(cfg)
    obs["ci_registration"]["SCED"]["state"] = "disabled_inactivity"
    sites = ss.compare(cfg, obs, HOME)
    assert next(s for s in sites if s["id"] == "4G")["status"] == "drift"


def test_unparseable_observations_are_unknown_not_ok(cfg):
    obs = _clean_observations(cfg)
    obs["launchd"]["SCED"] = {"present": True, "parsed": False}
    obs["plist_disk"]["SCED"]["decoded"] = None
    sites = ss.compare(cfg, obs, HOME)
    assert {s["status"] for s in sites if s["repo"] == "SCED" and s["site"] == 2} == {"unknown"}
    assert ss.compare_exit(sites) == ss.EXIT_DRIFT


def test_driver_default_drift_is_advisory_but_strict_promotes_it(cfg):
    obs = _clean_observations(cfg)
    obs["driver_defaults"]["SCED"] = {"sched": "0247", "latest": "0327"}
    lenient = ss.compare(cfg, obs, HOME)
    row = next(s for s in lenient if s["id"] == "5D" and s["repo"] == "SCED")
    assert row["status"] == "advisory"
    assert row["patch"].startswith("  SCED)")
    assert ss.compare_exit(lenient) == ss.EXIT_OK

    strict = ss.compare(cfg, obs, HOME, strict=True)
    assert next(s for s in strict if s["id"] == "5D" and s["repo"] == "SCED")["status"] == "drift"


def test_cosmetic_plist_drift_is_still_drift_but_labelled(cfg):
    obs = _clean_observations(cfg)
    obs["plist_disk"]["SCED"]["raw"] = "<!-- an older banner -->\n" + \
        obs["plist_disk"]["SCED"]["raw"]
    sites = ss.compare(cfg, obs, HOME)
    row = next(s for s in sites if s["id"] == "2")
    assert row["status"] == "drift"
    assert row["class"] == "cosmetic"


# ------------------------------------------------------------------- CLI shape

def test_validate_cli_is_clean_on_the_fixture():
    r = run("validate")
    assert r.returncode == ss.EXIT_OK, r.stdout + r.stderr


def test_compare_cli_emits_one_json_document(tmp_path, cfg):
    obs = tmp_path / "obs.json"
    obs.write_text(json.dumps(_clean_observations(cfg)), encoding="utf-8")
    r = run("compare", "--observations", str(obs))
    assert r.returncode == ss.EXIT_OK, r.stderr
    doc = json.loads(r.stdout)
    assert doc["exit"] == 0
    assert doc["drift_count"] == 0
    assert set(doc["repos"]) == {"SCED", "SCED-downloads"}


def test_set_refuses_an_invalid_time_and_writes_nothing(cfg_path):
    before = cfg_path.read_text(encoding="utf-8")
    r = run("set", "--repo", "SCED", "--at", "01:20", config=cfg_path)  # stagger 8 min
    assert r.returncode == ss.EXIT_INVARIANT
    assert cfg_path.read_text(encoding="utf-8") == before
    assert "REFUSED" in r.stderr


def test_set_dry_run_writes_nothing(cfg_path):
    before = cfg_path.read_text(encoding="utf-8")
    r = run("set", "--repo", "SCED", "--at", "01:52", "--latest", "02:32",
            "--dry-run", config=cfg_path)
    assert r.returncode == ss.EXIT_DRIFT
    assert cfg_path.read_text(encoding="utf-8") == before


def test_set_writes_and_reports_that_nothing_is_in_effect(cfg_path):
    r = run("set", "--repo", "SCED", "--at", "01:52", "--latest", "02:32", config=cfg_path)
    assert r.returncode == ss.EXIT_OK, r.stderr
    assert "apply" in r.stdout
    doc = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert doc["repos"]["SCED"]["local_hhmm"] == "0152"
    assert doc["repos"]["SCED"]["latest_hhmm"] == "0232"


# --------------------------------------------------- shell-level precondition

SHELL = SCRIPT_DIR / "sced-schedule.sh"


def _run_shell(tmp_path, env_body, *args):
    env_file = tmp_path / "env"
    env_file.write_text(env_body, encoding="utf-8")
    env_file.chmod(0o600)
    return subprocess.run(
        ["/bin/bash", str(SHELL), *args,
         "--config", str(BASE_CONFIG),
         "--env-file", str(env_file),
         "--launch-agents-dir", str(tmp_path / "agents"),
         "--no-launchd-probe"],
        capture_output=True, text=True)


@pytest.mark.parametrize("verb", ["verify", "apply"])
def test_env_file_schedule_key_is_a_hard_precondition(tmp_path, verb):
    """A schedule key there is sourced AFTER launchd's env and silently wins."""
    r = _run_shell(tmp_path, "SCED_SYNC_DISCORD_WEBHOOK=x\nSCED_SYNC_SCHED_SCED=0900\n",
                   verb, "--dry-run")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "SCED_SYNC_SCHED_SCED" in r.stderr


def test_clean_env_file_does_not_trip_the_precondition(tmp_path):
    """Regression: `grep` exits 1 on no-match, and pipefail turned the HEALTHY
    case into an aborted run."""
    r = _run_shell(tmp_path, "SCED_SYNC_DISCORD_WEBHOOK=x\nSCED_SYNC_MENTION=y\n",
                   "verify")
    assert r.returncode != 2, r.stdout + r.stderr
    assert r.returncode != 1, f"the run aborted instead of reporting: {r.stderr}"


def test_set_drops_an_acknowledgement_the_new_time_invalidates(tmp_path):
    path = _with_collision(tmp_path, "coll-drop.json",
                           ack={"ack_for": "0147/0227", "at": "2026-07-31", "reason": "contained"})
    # Move SCED clear of the 02:00-02:30 collision: the ack no longer applies and
    # must not be carried forward as a decision nobody made.
    r = run("set", "--repo", "SCED", "--at", "03:12", "--latest", "03:52", config=path)
    assert r.returncode == ss.EXIT_OK, r.stderr
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert "collision_ack" not in doc["repos"]["SCED"]
