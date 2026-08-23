"""The miniature workspace both langpack suites drive.

WHY A WHOLE WORKSPACE AND NOT A DICT

`kz_langpack`'s output is FILES IN A GIT REPOSITORY and `kz_verify`'s subject is
those files, so the properties that matter are properties of the filesystem after
the fact: snapshots taken before the write, an intentions log persisted before
phase 2, a `create` that `revert` deletes only when its bytes are the ones this
run planned, and a set equality between a directory listing and a manifest. None
of those is observable from a plan.

So this assembles a real workspace -- a scenario tree, a Korean pack with donors,
a run directory -- and drives the actual v0 chain
(`init` -> `source` -> `scaffold` -> `reuse` -> `objtext` -> `register`) through
it with `--live`. That is the only way to exercise the live path at all: the real
one writes into SCED-downloads, which no test may do.

THE ONE THING IT CANNOT COVER is `kz_config.write_data`'s `data_root` mirror,
because `data_root` is asserted to name the koreanize package's canonical
workspace-relative location and a sandbox workspace has no package there.
`mirror=False` is passed throughout and the lock receipt's shape is covered
separately, against a stubbed writer.

It lives in its own module rather than in `conftest.py` so that the fixture stays
small and readable, and rather than inside one suite so the other does not import
a test module to get at it.
"""

import json
import os

import pytest

import kz_init as ki
import kz_langpack as kl
import kz_source as ks

PACK = "Korean - Campaigns"
PACK_CONTAINER = "Korean-Campaigns.KoreanC"
SCENARIO = "Test Scenario"
STEM = "TestScenario.aaaaaa"

EN_FACE = "https://example.invalid/en/atlas.png"
KO_FACE = "https://example.invalid/ko/atlas.png"
BACK = "https://example.invalid/back.png"


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------


def _write(path, data):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def _card(guid, arkham_id, card_id, face=EN_FACE, nickname=None, num_width=7,
          num_height=4):
    return {
        "Name": "Card", "GUID": guid, "CardID": card_id,
        "Nickname": nickname or ("Card %s" % arkham_id),
        "Description": "Creature.",
        "Tags": ["dropped"],
        "GMNotes": json.dumps({"id": arkham_id}, indent=2),
        "CustomDeck": {str(card_id // 100): {
            "FaceURL": face, "BackURL": BACK, "NumWidth": num_width,
            "NumHeight": num_height, "BackIsHidden": True, "Type": 0}},
        "Transform": {"posX": 3.0, "rotY": 90, "scaleX": 1, "scaleY": 1,
                      "scaleZ": 1},
    }


def _donor_override(guid, arkham_id, card_id, nickname):
    obj = _card(guid, arkham_id, card_id, face=KO_FACE, nickname=nickname,
                num_width=10, num_height=7)
    obj.pop("Tags", None)
    return obj


class Sandbox(object):
    def __init__(self, root):
        self.root = str(root)
        self.run_dir = os.path.join(self.root, ".am", "koreanize", "test-scenario")
        self.cfg = None

    # -- paths -------------------------------------------------------------
    @property
    def scenario_dir(self):
        return os.path.join(self.root, "SCED-downloads", "decomposed", "scenario",
                            SCENARIO)

    @property
    def langpack(self):
        return os.path.join(self.root, "SCED-downloads", "decomposed",
                            "language-pack")

    @property
    def container_dir(self):
        return os.path.join(self.langpack, PACK, PACK_CONTAINER, STEM)

    @property
    def container_json(self):
        return self.container_dir + ".json"

    @property
    def pack_json(self):
        return os.path.join(self.langpack, PACK, "%s.json" % PACK_CONTAINER)

    # -- construction ------------------------------------------------------
    def build(self, cards, donors, deck_parented=()):
        """`cards` is [(guid, arkham_id, card_id)]; `donors` maps arkham_id ->
        (guid, card_id, nickname). `deck_parented` names ids that sit under a
        Deck rather than directly under the root container."""
        order, deck_order = [], []
        for guid, arkham_id, card_id in cards:
            obj = _card(guid, arkham_id, card_id)
            stem = "Card%s.%s" % (arkham_id, guid)
            if arkham_id in deck_parented:
                deck_order.append(stem)
                _write(os.path.join(self.scenario_dir, STEM, "Deck.ddd111",
                                    "%s.json" % stem), obj)
            else:
                order.append(stem)
                _write(os.path.join(self.scenario_dir, STEM, "%s.json" % stem), obj)
        if deck_order:
            order.append("Deck.ddd111")
            _write(os.path.join(self.scenario_dir, STEM, "Deck.ddd111.json"), {
                "Name": "Deck", "GUID": "ddd111",
                "ContainedObjects_order": deck_order,
                "ContainedObjects_path": "%s/Deck.ddd111" % STEM})

        _write(os.path.join(self.scenario_dir, "%s.json" % STEM), {
            "Name": "Bag", "GUID": "aaaaaa", "Nickname": SCENARIO,
            "Description": "the english blurb",
            "ColorDiffuse": {"r": 1, "g": 1, "b": 1},
            "Hands": False, "HideWhenFaceDown": False,
            "Transform": {"posX": 1, "scaleX": 2, "scaleY": 2, "scaleZ": 2},
            "LuaScript": "-- dropped",
            "ContainedObjects_order": order,
            "ContainedObjects_path": STEM})

        for arkham_id, (guid, card_id, nickname) in donors.items():
            _write(os.path.join(self.langpack, PACK, PACK_CONTAINER,
                                "SomeOtherScenario.bbbbbb",
                                "Donor%s.%s.json" % (arkham_id, guid)),
                   _donor_override(guid, arkham_id, card_id, nickname))
        _write(os.path.join(self.langpack, PACK, "SomeOtherScenario.bbbbbb.json"),
               {"Name": "Bag", "GUID": "bbbbbb"})
        _write(self.pack_json, {"Name": "Bag", "GUID": "cccccc",
                                "ContainedObjects_order": ["SomeOtherScenario.bbbbbb"],
                                "ContainedObjects_path": PACK_CONTAINER})
        return self

    # -- the chain ---------------------------------------------------------
    def init(self):
        report, artifacts = ki.run_init(
            scenario=SCENARIO, pack=PACK, slug="test-scenario",
            run_dir=self.run_dir, workspace=self.root, download_atlases=False)
        ki.emit(report, artifacts, self.run_dir, workspace=self.root, mirror=False)
        self.cfg = artifacts["scenario.json"]
        return report

    def source(self, escalate=False):
        report, artifacts, _t = ks.run_source(
            self.run_dir, escalate=escalate, workspace=self.root)
        ks.emit(report, artifacts, self.run_dir, cfg=self.cfg)
        return report

    def run(self, stage, live=False, **kwargs):
        # interlock=False: the sandbox writes inside its own tmp_path and can
        # collide with nothing, and taking the WORKSPACE-WIDE nightly lock from a
        # test would make the suite serialise against the real 02:17 driver.
        return kl.run_langpack(stage, self.run_dir, live=live, assume_yes=True,
                               workspace=self.root, mirror=False, confirm=False,
                               interlock=False, **kwargs)

    def chain(self, stages=("scaffold", "reuse", "objtext", "register")):
        """Rehearse then commit each stage, which is the shipped order: the live
        invocation binds the plan the rehearsal persisted."""
        out = {}
        for stage in stages:
            self.run(stage)                    # rehearsal -> <stage>.plan.json
            out[stage] = self.run(stage, live=True)[0]
        return out




def default_sandbox(tmp_path):
    """The shape both suites use: 3 objects, 2 with donors, 1 under a Deck.

    Three and not two, because the third is what makes the SCOPING observable --
    an object with no donor must get no override file at all, and a suite whose
    population equals its reuse subset cannot tell a scoped plan from an
    unscoped one.
    """
    box = Sandbox(tmp_path).build(
        cards=[("111111", "01001", 10001),
               ("222222", "01002", 10002),
               ("333333", "01003", 10003)],
        donors={"01001": ("d11111", 231701, "Donor One"),
                "01002": ("d22222", 231702, "Donor Two")},
        deck_parented=("01003",))
    box.init()
    box.source()
    return box
