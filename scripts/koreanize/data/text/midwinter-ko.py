#!/usr/bin/env python3
"""The Midwinter Gala -- Korean card text, as a GENERATOR (design 3.1, 5.2).

This file is the durable artifact and `card-text-ko.json` is derived from it. It
is NOT the other way round, and that is the whole design rather than a
preference. `check-particles.py:30-36` states the consequence in full:

    `--fix` NEVER writes `card-text-ko.json`. That artifact is produced only by
    re-running the generator, which is what keeps its serialization out of human
    hands. A consequence, and it is designed, not a bug: **`--fix` ALONE DOES NOT
    TURN THIS CHECKER GREEN.** It goes green after the generator has been re-run
    and the regenerated JSON moved into place.

The Midwinter record's `주요목적를` -- a wrong grammatical particle -- appeared
**five** times, and all five came from one construction in this table. Repair the
JSON and the next run reproduces all five. So `kz_checkers.py` has no `--fix` at
all, and `audit` (S7) exists to widen a single found instance into the predicate
that catches its siblings IN THE GENERATOR.

    python3 midwinter-ko.py --source <run_dir>/card-source-en.json \
                            --out    <run_dir>/card-text-ko.json
    koreanize.sh check --slug midwinter

PROVENANCE. The `KO[...]` table below and the four structural helpers under it
are ported VERBATIM from `.am/midwinter-gala-korean/translate-a3.py:36-1202` by
design 6 step 8. Only the header, `render()` and `main()` are new, and they are
new for one reason: the record's generator hardcoded its own input and output
paths, and `golden --fixture midwinter` has to drive it into a scratch run dir
(design 5.8). Nothing about a Korean string changed in the port.

WHAT IS DELIBERATELY NOT IN THE TABLE. A `passthrough` ruling writes NO `KO` row.
A passthrough is a decision that the English stands, so writing it into the table
would restate the source as though it were a translation and make the two
indistinguishable in a diff. `render()`'s fallback already produces identical
bytes, so the behaviour is the same and the record is honest.

Identity fields -- `guid`, `card_id`, `deck_key`, `cell`, `sheet`, `pack` -- are
copied from the English source and never restated in `KO`, so a translation
cannot move one. `kz_checkers.py`'s CH4 refuses a run that does.

KNOWN DIVERGENCE, MEASURED 2026-08-24, AND IT IS THIS FILE'S OWN CASE IN POINT.
Rendering this table against the record's `card-source-en.json` reproduces the
shipped `.am/midwinter-gala-korean/card-text-ko.json` on **61 of 62** cards. Card
`71001` -- the chaos-token reference card -- differs: the shipped JSON has the
four chaos-token `[icon]` prefixes (`[skull]: `, `[cultist]: `, `[tablet]: `,
`[elder_thing]: `) stripped from `text` and `back_text`, and this table still
carries them. `card-text-ko.json.bak-20260818T214404Z` matches THIS table, the
live file does not, and the live file's `counts.translated_chars` is still 18004
-- unchanged from before the edit, which is the proof it was edited in place
rather than regenerated.

That is exactly the defect the paragraph above exists to prevent, caught by the
first re-run after the record closed. It is RECORDED here rather than repaired
here: stripping four icon prefixes from a card's Korean text is a translation
decision behind the `terms` gate, not a data import, and 6 step 10
(`kz_translate.py` + S7 `audit`) owns this class. Two consequences a reader must
know:

  * the SHIPPED PIXELS follow the hand-edited JSON, not this table --
    `typeset-cards.json` is dated after the edit -- so `data/golden/`'s fixture
    binds to the shipped `card-text-ko.json` and `golden` never re-runs this
    generator; and
  * re-running this generator over the record's source and moving the result into
    place WOULD change `71001` back and invalidate the shipped atlas. Do not do
    it as a housekeeping step.

ONE MORE PORT GAP, RECORDED RATHER THAN BRIDGED. This table's `render()` reads
the RECORD's `card-source-en.json` shape -- `code`, `_atlas{}`, `b_side{}` --
because that is the document the table was written against. koreanize's own
`init` emits a different shape for the same file: `arkham_id`, no `_atlas`, no
`b_side` (compare `.am/koreanize/midwinter/card-source-en.json`). The two are not
interchangeable, and this file is deliberately NOT adapted to the second: 6 step
10 (`kz_translate.py`) is what decides which shape a generator is emitted
against, and a step-8 import that guessed would have to be undone. It costs
nothing today because `golden --fixture midwinter` SEEDS `translate` rather than
driving it (5.8), so this generator is never executed inside the regression
instrument.

SERIALIZATION IS PART OF THE CONTRACT (data/text/README.md): `indent=1`,
`ensure_ascii=False`, one trailing newline, and multi-line fields written one
SOURCE line per TEXT line so a change to one card is one hunk.

Structural rules the table obeys, enforced by `check()` below and re-tested
independently by `kz_checkers.py` with a hand-written tokenizer that shares no
code with this file:

  * every [icon] token is preserved verbatim -- they render as glyphs from the
    card font, not as text (data/terms/midwinter.json, kind `icon_token`).
  * every [[Trait]] reference keeps its markers, but the trait NAME inside is
    translated -- the terminology supplies Korean for Weapon/Tome/Tool precisely
    because they appear only as refs. Marker count is therefore preserved.
  * <b>/<i>/<u> tags are preserved verbatim, as are <blockquote>/<br>/<hr>.
  * (->R1) and similar resolution pointers are never translated.
  * newline structure matches the English line for line.
"""

import argparse
import json
import re
import sys

# Fields translated on the front/primary face and on b_side.
FRONT_FIELDS = ["name", "subname", "traits", "text", "flavor",
                "back_name", "back_text", "back_flavor"]
B_FIELDS = ["name", "subname", "traits", "text", "flavor"]

KO = {}

# --- Korean translations, keyed by arkham_id -------------------------------
# nick/desc are the TTS object's printed Nickname/Description (used by Block D).

KO = {}

KO["71001"] = dict(
    nick="시나리오",
    desc="한겨울 축제",
    name="한겨울 축제",
    text=(
        "쉬움 / 보통\n"
        "[skull]: -X. X는 현재 주요사건 번호입니다.\n"
        "[cultist]: -X. X는 당신의 장소에 있는 [[손님]] 자산의 수입니다(최대 5).\n"
        "[tablet]: -2. (당신이 [[개인]] 장소에 있다면 대신 -4).\n"
        "[elder_thing]: -3. 실패하거나 피 없는 사내가 당신의 장소에 있다면, 공포 1을 받습니다."
    ),
    back_text=(
        "어려움 / 전문가\n"
        "[skull]: -X. X는 현재 주요사건 번호에 1을 더한 값입니다.\n"
        "[cultist]: -X. X는 당신의 장소에 있는 이야기 자산의 수입니다.\n"
        "[tablet]: -3 (당신이 [[개인]] 장소에 있다면 대신 -5).\n"
        "[elder_thing]: -4. 실패하거나 피 없는 사내가 당신의 장소에 있다면, 공포 1을 받습니다."
    ),
)

_AGENDA_FORCED = (
    "카드 효과로는 이 주요사건에서 파멸을 제거할 수 없습니다.\n"
    "<b>강제</b> - 단면 [[손님]] 자산이 플레이에서 벗어나려 할 때: 대신 그 자산은 홀린 "
    "상태가 됩니다 <i>(뒷면이 보이게 뒤집고, 그 자산에 대한 조종을 잃고, 그 자산의 피해와 "
    "공포를 모두 회복합니다)</i>."
)

KO["71002"] = dict(
    nick="가면 쓴 연회객",
    desc="주요사건 1",
    name="가면 쓴 연회객",
    text="각 [[랜턴 클럽]] 적에게는 피해를 입힐 수 없습니다.\n" + _AGENDA_FORCED,
    flavor=(
        "음모와 경이의 밤을 약속하는 이 축제는, 술과 음악만큼이나 여흥의 일부인 비밀과 "
        "속삭임으로 가득할 것이다."
    ),
    back_name="누가 구그를 풀어놨지?",
    back_text=(
        "따로 빼둔 각 [[괴물]] 적과 잔혹한 매복 음모 2장, 그리고 조우 버린 카드 더미를 "
        "조우 덱에 섞어 넣습니다."
    ),
    back_flavor=(
        "저택 안쪽 어딘가에서 무언가 부서지는 소리가 울려 퍼지고, 뒤이어 비명이 터진다. "
        "데클란 피어스가 나타나 무도회장의 사람들을 진정시키지만, 가면을 쓴 종업원들이 "
        "저택의 수많은 출구마다 앞을 가로막고 서 있다는 사실이 눈에 들어오는 것을 어쩔 수 없다."
    ),
)

KO["71003"] = dict(
    nick="불청객",
    desc="주요사건 2",
    name="불청객",
    text="각 [[랜턴 클럽]] 적의 협상 기능은 격발할 수 없습니다.\n" + _AGENDA_FORCED,
    flavor=(
        "여러 손님이 종업원에게 내보내 달라고 요구하면서 저택 안쪽에서 소란이 인다. "
        "다른 손님들은 죽은 듯한 눈과 텅 빈 시선으로 저택 안을 헤매고 다닌다."
    ),
    back_name="아수라장",
    back_text=(
        "<b>피 없는 사내 <i>(말 없는 잠복자)</i>가 플레이 중이라면:</b>\n"
        "<blockquote><i>돌아서자 피 없는 사내가 살기를 띠고 당신을 향해 성큼성큼 다가오는 "
        "것이 보인다. 반응할 새도 없이, 그 위압적인 형체가 비틀거리더니 붉은 융단 위로 "
        "얼굴부터 고꾸라진다.<br><br>잠시 후, 천이 찢기는 날카로운 소리가 정적을 깨뜨린다. "
        "번들거리는 부속지들이 피 없는 사내의 찢어진 정장과 사지에서 터져 나와, 살덩이와 "
        "꿈틀대는 촉수가 뒤엉킨 기괴한 혼합체로 피어난다. 악몽 같은 팔다리와 맞부딪히는 "
        "송곳니의 아가리 한가운데 박힌 그 생물의 뼛빛 가면이 당신을 내려다볼 때, 속이 "
        "뒤틀린다.</i></blockquote>\n"
        "피 없는 사내 <i>(말 없는 잠복자)</i>를 <i>(풀려난 자)</i> 면으로 뒤집습니다.\n"
        "<hr>\n"
        "<b>그 외의 경우:</b>\n"
        "<blockquote><i>주변의 소란이 잠시 잦아드는 듯하다.</i></blockquote>\n"
        "각 조사자는 자신의 장소에 있는 조사자나 [[손님]] 자산의 공포 1을 회복할 수 있습니다."
    ),
)

KO["71004"] = dict(
    nick="죽여주는 파티",
    desc="주요사건 3",
    name="죽여주는 파티",
    text="각 [[랜턴 클럽]] 적의 협상 기능은 격발할 수 없습니다.\n" + _AGENDA_FORCED,
    flavor=(
        "파티는 완전한 아수라장으로 치닫는다. 손님들은 이세계의 괴물들을 피해 유리문과 "
        "창문 밖으로 몸을 던진다."
    ),
    back_name="킹스포트식 작별 인사",
    back_text="각 조사자는 쓰러지고 정신적 트라우마 1을 입습니다.",
    back_flavor=(
        "한때 위풍당당했던 저택에 역겨운 악취가 배어든다. 악몽 같은 생물들이 상처 입거나 "
        "넋이 나간 손님들을 하나둘 덮치는 사이, 당신은 숨어 있던 곳에서 뛰쳐나와 출구를 향해 "
        "곧장 내달린다. 악몽에서 튀어나온 듯한 굶주린 생물이 당신의 냄새를 맡고 출구까지 "
        "집요하게 쫓아온다. 두꺼운 유리창을 향해 몸을 던지는 순간 목덜미에 그 발톱이 닿는 "
        "것이 느껴지고, 당신은 옅게 내리는 눈에 뒤덮인 처참한 시체 더미 위로 떨어진다."
    ),
)

_ACT_PARLEY = (
    "[[손님]] 자산은 조력자 슬롯을 차지하지 않습니다.\n"
    "[action] 당신의 장소에 있는 [[손님]] 자산 하나와 그 자산의 능력 아이콘 하나를 "
    "고릅니다: <b>협상.</b> 그 능력을 테스트합니다(3). 성공하면, 그 자산을 조종합니다."
)

KO["71005"] = dict(
    nick="만남의 자리",
    desc="주요목적 1",
    name="만남의 자리",
    text=(
        _ACT_PARLEY + "\n"
        "[action] 당신이 [[손님]] 자산을 조종하고 있다면: 그 자산의 조종권을 당신의 장소에 "
        "있는 다른 조사자에게 넘깁니다.\n"
        "<b>목표</b> - 쓰러지지 않은 각 조사자가 [[손님]] 자산을 조종하고 있고 현재 "
        "주요사건이 2a 또는 3a라면, 조사자들은 그룹으로서 필요한 만큼의 단서를 소비해 "
        "진행할 수 있습니다."
    ),
    flavor="보석이 더 이상 무도회장에 없다. 어느 손님이 누군가 그것을 옮기는 것을 보았을까?",
    back_name="연줄 확보",
    back_text=(
        "따로 빼둔 [[2층]] 장소 중 하나를 무작위로 골라 로비 바로 위에 플레이합니다. 남은 "
        "게임 동안 그 장소는 로비와 이어지며, 그 반대도 마찬가지입니다. 따로 빼둔 나머지 "
        "장소 2개를 앞서 놓은 [[2층]] 장소의 오른쪽에 한 줄로 플레이합니다.\n"
        "따로 빼둔 라이벌 이야기 카드를 [[라이벌]] 면이 보이게 주요사건 옆에 플레이하고, "
        "그 카드의 <b>준비</b> 지문을 따릅니다.\n"
        "<b>막간: 전설의 보석</b> <i>(13쪽)</i>을 읽습니다."
    ),
    back_flavor=(
        "당신과 당신의 연줄은 여러 손님을 만나고, 그들은 하나같이 데클란 피어스가 "
        "무도회장의 전시함에서 보석을 가져갔다고 확인해 준다. 게다가 저택 2층에서 기이한 "
        "소리가 새어 나오기 시작했다..."
    ),
)

KO["71006"] = dict(
    nick="보석 찾기",
    desc="주요목적 2",
    name="보석 찾기",
    text=(
        _ACT_PARLEY + "\n"
        "<b>목표</b> - 당신의 진영 이야기 카드에 적힌 목표를 완수하고, 가능한 한 많은 과제를 "
        "달성하려 노력합니다 <i>(규칙서 24쪽 참조)</i>."
    ),
    flavor="무슨 대가를 치르더라도 당신은 연줄과 그 동료들을 도와야 한다.",
    back_name="파티는 끝났다",
    back_text="<b>(→R1)</b>.",
    back_flavor=(
        "차가운 밤공기 속으로 비틀거리며 나서면서 당신은 깊게 숨을 들이쉰다. 한때 티 없이 "
        "깨끗했던 잔디밭 위로 눈이 소복이 내리고, 그 위는 이제 피로 붉게 물들어 있다. 등 뒤의 "
        "저택은 죽은 듯 고요하다. 사르나스의 보석이 손바닥 안에서 이 세상의 것이 아닌 빛으로 "
        "고동치고, 한순간 당신은 포근한 바람과 함께 익숙하면서도 꿈결 같은 다른 어딘가의 "
        "내음을 느낀다. 이 모든 참극을 치를 만한 가치가 그 보석에 있었을까?"
    ),
)

KO["71007"] = dict(
    nick="로비",
    desc="저택. 1층.",
    name="로비",
    traits="저택. 1층.",
    text=(
        "[fast] 현재 주요사건이 2a 또는 3a라면, 자원 1 [per_investigator]를 소비합니다: "
        "누군가 멋지게 늦게 도착합니다. 손님 덱의 맨 위 카드를 공개해 이 장소에 "
        "플레이합니다. (그룹 제한: 라운드당 1회.)"
    ),
    flavor="겨울의 한기가 뼛속에 남아 있다. 무언가 잘못되었다는 느낌이 든다.",
    back_flavor=(
        "저택의 정문은 예술품과 참석자들, 그리고 아름다운 샹들리에로 가득한 방으로 이어진다. "
        "한 잔 챙기도록. 긴 밤이 될지도 모르니."
    ),
)

KO["71008"] = dict(
    nick="랜턴의 방",
    desc="저택. 개인. 지하실.",
    name="랜턴의 방",
    traits="저택. 개인. 지하실.",
    text=(
        "[fast] 당신의 턴 동안, 카드 하나를 지목합니다: 조우 덱의 맨 위 카드를 뽑습니다. 그 "
        "카드가 지목한 카드라면, 그 효과를 취소하고 버립니다. 이 장소에서 단서 1을 "
        "발견합니다. (제한: 턴당 1회.)\n"
        "[action] 이 장소에 단서가 없다면: [willpower]를 테스트합니다(4). 성공하면, 홀린 "
        "카드 아무거나 하나를 뒤집습니다."
    ),
    back_text="이 장소에 들어가는 추가 비용으로, 공포 1을 받아야 합니다.",
    back_flavor="고요한 성역의 손잡이에 손을 얹자 얼음장 같은 한기가 느껴진다.",
)

_GROUND_BACK_FLAVOR = "위풍당당한 복도는 음악과 웃음소리로 가득하다."
_SECOND_BACK_FLAVOR = "2층은 으스스할 만큼 고요하다."

KO["71009"] = dict(
    nick="1층 방",
    desc="저택. 1층.",
    name="화랑",
    traits="저택. 1층.",
    text=(
        "[action]: 기이한 예술 작품으로 가득한 이 방에는 더 발견할 것이 있습니다. 이 장소에 "
        "단서를 최대 2개까지 놓습니다 <i>(토큰 보관함에서)</i>. 자원 2를 얻습니다. "
        "(그룹 제한: 라운드당 1회.)"
    ),
    flavor="여기 전시된 작품들에 견줄 만한 것은 없다.",
    back_name="1층 방",
    back_flavor=_GROUND_BACK_FLAVOR,
)

KO["71010"] = dict(
    nick="1층 방",
    desc="저택. 1층.",
    name="무도회장",
    traits="저택. 1층.",
    text=(
        "[action]: <b>협상.</b> 손님 둘을 설득해 춤을 추게 합니다. 이 장소에 있는 [[손님]] "
        "자산 2개를 소진합니다. 그런 다음, 각각의 공포 1을 회복합니다."
    ),
    flavor='잘 알려지지 않은 작곡가 E. 잔의 "라단조 꿈과 광기"가 연주되고 있다.',
    back_name="1층 방",
    back_flavor=_GROUND_BACK_FLAVOR,
)

KO["71011"] = dict(
    nick="1층 방",
    desc="저택. 1층.",
    name="주점",
    traits="저택. 1층.",
    text=(
        "[fast] 자원 1을 소비합니다: 한 잔 삽니다! 이 장소에 있는 조사자나 [[손님]] 자산의 "
        "피해 1 또는 공포 1을 회복합니다. (그룹 제한: 라운드당 1회.)"
    ),
    flavor='"이 밀주, 킥이 아주 좋은데!"',
    back_name="1층 방",
    back_flavor=_GROUND_BACK_FLAVOR,
)

KO["71012"] = dict(
    nick="2층 방",
    desc="저택. 개인. 2층.",
    name="침실",
    traits="저택. 개인. 2층.",
    text=(
        "[action] [[2층]] 장소 아무 곳에나 있는 적 하나를 고릅니다: <b>전투.</b> 이 공격에는 "
        "[combat] 대신 [agility]를 사용할 수 있습니다. 성공하면, 단서 1을 소비해 이 공격이 "
        "피해 +2를 입히게 할 수 있습니다. (제한: 턴당 1회.)"
    ),
    back_name="2층 방",
    back_flavor=_SECOND_BACK_FLAVOR,
)

KO["71013"] = dict(
    nick="2층 방",
    desc="저택. 개인. 2층.",
    name="서재",
    traits="저택. 개인. 2층.",
    text=(
        "이 장소에서 협상하는 동안 당신은 능력값 +1을 얻습니다.\n"
        "[fast] 사르나스의 보석이 이 장소에 있다면: [intellect]를 테스트합니다(3). 성공하면, "
        "사르나스의 보석에 피해 1을 놓거나 그 위의 토큰 1개를 제거합니다. "
        "(그룹 제한: 게임당 1회.)"
    ),
    back_name="2층 방",
    back_flavor=_SECOND_BACK_FLAVOR,
)

KO["71014"] = dict(
    nick="2층 방",
    desc="저택. 개인. 2층.",
    name="응접실",
    traits="저택. 개인. 2층.",
    text=(
        "이 장소는 이곳에 있는 [[손님]] 자산 하나당 장막값 -1을 얻습니다(최소 장막값 1).\n"
        "[fast] 당신이 사르나스의 보석을 조종하고 있다면: 아무 장소에나 있는 적 하나를 "
        "소진합니다. (그룹 제한: 게임당 1회.)"
    ),
    flavor="병 속의 달짐승이라니? 참으로 기묘하군.",
    back_name="2층 방",
    back_flavor=_SECOND_BACK_FLAVOR,
)

KO["71015"] = dict(
    nick="재단",
    desc="",
    name="재단 [guardian]",
    traits="협력.",
    text=(
        "<b>강제</b> - 피 없는 사내 <i>(풀려난 자)</i>가 쓰러졌을 때: 사르나스의 보석이 "
        "그에게 부착되어 있다면, 그것을 그의 장소에 부착합니다.\n"
        "[action] 사르나스의 보석이 당신의 장소에 있고 피 없는 사내가 플레이 중이 "
        "아니라면: 사르나스의 보석을 조종하고 그 위에 피해 1을 놓습니다.\n"
        "<b>목표</b> - 라운드 종료 시, 조사자들이 사르나스의 보석을 조종하고 있다면, 그들은 "
        "그룹으로서 단서 2 [per_investigator]를 소비해 주요목적을 진행할 수 있습니다.\n"
        "<b>게임 종료 시 점수 계산:</b>\n"
        "약점이 아닌 적이 플레이 중에 없다면 4점(약점이 아닌 적이 정확히 하나만 플레이 "
        "중이라면 대신 2점)."
    ),
    flavor=(
        "'당국'은 당신이 힘으로 보석을 손에 넣기를 바라는 모양이다. 피 없는 사내에게서 "
        "그것을 빼앗을 수 있기를."
    ),
    b=dict(
        name="재단 [guardian]",
        traits="라이벌.",
        text=(
            "<b>준비:</b> 따로 빼둔 신참 경찰 적을 조우 덱에 섞어 넣습니다. 따로 빼둔 "
            "발레리야 안토노바 카드를 적 면이 보이게 주도 조사자의 장소에 플레이합니다.\n"
            "[action] 발레리야 안토노바가 당신의 장소에 있다면: <b>협상.</b> [combat]을 "
            "테스트합니다(3). 성공하면, 당신의 단서 1개를 이 이야기 카드에 놓습니다.\n"
            "<b>강제</b> - 이 이야기 카드에 단서가 1에 1 [per_investigator]를 더한 만큼 "
            "놓였을 때: 이 카드와 발레리야 안토노바를 승리 표시 영역에 추가합니다. 조우 덱, "
            "조우 버린 카드 더미, 그리고 모든 플레이 영역에서 신참 경찰을 찾아 게임에서 "
            "제거합니다. 조우 덱을 찾았다면, 조우 덱을 섞습니다."
        ),
        flavor=(
            "재단은 보석에 대한 당신의 관심을 곱게 보지 않는다. 힘을 과시한다면, 물러서도록 "
            "설득할 수 있을지도 모른다."
        ),
    ),
)

KO["71016"] = dict(
    nick="발레리야 안토노바",
    desc="",
    name="발레리야 안토노바",
    subname="탈출을 바라는 자",
    traits="지도자. 손님. 연방 요원.",
    text=(
        "당신은 [combat] +1을 얻습니다.\n"
        "[action] 발레리야 안토노바를 소진합니다: 당신의 장소에 있는 다른 [[손님]] 자산 "
        "하나를 준비 상태로 바꿉니다. 당신의 장소에 있는 [[손님]] 자산 하나당, 이번 라운드의 "
        "다음 능력 테스트에 +1을 얻습니다.\n"
        "<b>강제</b> - 발레리야 안토노바가 플레이에서 벗어났을 때: 그녀를 게임에서 "
        "제거합니다."
    ),
    b=dict(
        name="발레리야 안토노바",
        subname="건드리면 다친다",
        traits="인간형. 지도자. 라이벌. 정예.",
        text=(
            "냉담한. 사냥꾼.\n"
            "발레리야 안토노바는 피해를 입지 않습니다.\n"
            "이 장소에 있는 각 조사자는 소진된 [[손님]] 자산을 조종하고 있다면 자신의 각 "
            "능력에 -1을 얻습니다.\n"
            "<b>강제</b> - 라운드가 끝났을 때: 이 장소에 있는 각 [[손님]] 자산을 소진합니다."
        ),
    ),
)

KO["71017"] = dict(
    nick="아치볼드 허드슨",
    desc="기민한 요원",
    name="아치볼드 허드슨",
    subname="기민한 요원",
    traits="조력자. 손님. 연방 요원.",
    text=(
        "[fast] 당신의 턴 동안, 아치볼드 허드슨을 소진합니다: 조우 덱의 맨 위 9장에서 적 "
        "하나를 찾아 뽑고, 조우 덱을 섞습니다. 그런 다음, 그 적을 소진하고 자원 3을 "
        "얻습니다.\n"
        "[action] 자원 1을 소비합니다: 당신의 장소에 있는 자산에서 피해 1을 당신의 장소에 "
        "있는 [[정예]]가 아닌 적에게 옮깁니다."
    ),
)

KO["71018"] = dict(
    nick="캘러핸 특별 요원",
    desc="무기 전문가",
    name="캘러핸 특별 요원",
    subname="무기 전문가",
    traits="조력자. 손님. 연방 요원.",
    text=(
        "[action]: <b>전투.</b> 이 테스트의 난이도를 1 감소시킵니다.\n"
        "[reaction] 당신이 [[조력자]] 또는 [[무기]] 자산의 [action] 기능을 격발한 후, "
        "캘러핸 특별 요원을 소진합니다: 당신의 장소에 있는 적 하나에게 피해 1을 입힙니다."
    ),
    flavor='"내 작은 친구 토미한테 인사나 하시지!"',
)

KO["71019"] = dict(
    nick="오라시오 마르티네스",
    desc="저돌적인 경호원",
    name="오라시오 마르티네스",
    subname="저돌적인 경호원",
    traits="조력자. 손님. 연방 요원.",
    text=(
        "당신이 오라시오 마르티네스를 조종하고 있는 동안, 당신의 장소에 있는 다른 조사자에게 "
        "입히는 피해를 그에게 할당할 수 있습니다.\n"
        "[reaction] [[정예]]가 아닌 적이 당신을 공격한 후, 오라시오 마르티네스를 "
        "소진합니다: 그 적을 소진하고, 그 적은 이번 라운드에 준비 상태로 바뀔 수 없습니다 "
        "<i>(그 적과의 교전은 해제하지 않습니다)</i>."
    ),
)

KO["71020"] = dict(
    nick="신참 경찰",
    desc="인간형. 경찰. 라이벌.",
    name="신참 경찰",
    traits="인간형. 경찰. 라이벌.",
    text=(
        "급증. 냉담한. 사냥꾼.\n"
        "<b>강제</b> - 이 장소에 있는 조사자가 다른 적을 공격한 후: 그 조사자에게 피해 1을 "
        "입힙니다."
    ),
)

KO["71021"] = dict(
    nick="미스캐토닉 대학교",
    desc="",
    name="미스캐토닉 대학교 [seeker]",
    traits="협력.",
    text=(
        "사르나스의 보석이 데클란 피어스에게 부착되어 있는 동안 그는 피해를 입지 않으며 "
        "플레이에서 벗어나지 않습니다.\n"
        "[action] 데클란 피어스가 당신의 장소에 있다면: <b>협상.</b> 당신의 단서를 최대 "
        "2개까지 데클란 피어스에게 놓습니다. 그런 다음, 그에게 단서가 3 [per_investigator] "
        "이상 놓여 있다면, 사르나스의 보석을 조종하고 데클란 피어스를 승리 표시 영역에 "
        "추가합니다.\n"
        "<b>목표</b> - 라운드 종료 시, 조사자들이 사르나스의 보석을 조종하고 있다면, 그들은 "
        "주요목적을 진행할 수 있습니다.\n"
        "<b>게임 종료 시 점수 계산:</b>\n"
        "모든 조사자의 손에 있는 카드 총합 2 [per_investigator]장당 1점(최대 4점)."
    ),
    flavor=(
        '당신은 콜드웰 학장이 보석을 "확보"하는 것을 돕고 있지만, 먼저 데클란 피어스를 '
        "설득해 그것을 내놓게 해야 한다."
    ),
    b=dict(
        name="미스캐토닉 대학교 [seeker]",
        traits="라이벌.",
        text=(
            "<b>준비:</b> 따로 빼둔 혼란 음모를 조우 덱에 섞어 넣습니다. 따로 빼둔 콜드웰 "
            "필립스 카드를 적 면이 보이게 주도 조사자의 장소에 플레이합니다.\n"
            "[action] 콜드웰 필립스가 당신의 장소에 있다면: <b>협상.</b> [intellect]를 "
            "테스트합니다(3). 성공하면, 당신의 단서 1개를 이 이야기 카드에 놓습니다.\n"
            "<b>강제</b> - 이 이야기 카드에 단서가 1에 1 [per_investigator]를 더한 만큼 "
            "놓였을 때: 이 카드와 콜드웰 필립스를 승리 표시 영역에 추가합니다. 조우 덱과 "
            "버린 카드 더미에서 혼란을 찾아 게임에서 제거합니다. 조우 덱을 섞습니다."
        ),
        flavor=(
            "미스캐토닉 대학교의 먹물들은 이 보석이 자기네 박물관에 있어야 한다고 여기는 "
            "모양이다. 어쩌면 그들이 이치를 깨닫도록 도울 수 있을지도 모른다."
        ),
    ),
)

KO["71022"] = dict(
    nick="콜드웰 필립스",
    desc="",
    name="콜드웰 필립스",
    subname="전설에 매혹된 자",
    traits="지도자. 손님. 미스캐토닉.",
    text=(
        "당신은 [intellect] +1을 얻습니다.\n"
        "[reaction] 당신이 단서를 1개 이상 발견한 후, 콜드웰 필립스를 소진합니다: 그 수만큼 "
        "카드를 뽑습니다(최대 3장).\n"
        "<b>강제</b> - 콜드웰 필립스가 플레이에서 벗어났을 때: 그를 게임에서 제거합니다."
    ),
    b=dict(
        name="콜드웰 필립스",
        subname="꿈에 사로잡힌 자",
        traits="인간형. 지도자. 라이벌. 정예.",
        text=(
            "냉담한. 사냥꾼.\n"
            "콜드웰 필립스는 피해를 입지 않습니다.\n"
            "<b>강제</b> - 당신이 이 장소에서 턴을 끝냈을 때: 당신의 손에서 약점이 아닌 카드 "
            "1장을 골라 버립니다."
        ),
    ),
)

KO["71023"] = dict(
    nick="마야 배드리 박사",
    desc="검시관",
    name="마야 배드리 박사",
    subname="검시관",
    traits="조력자. 손님. 미스캐토닉.",
    text=(
        "당신의 최대 손 카드 수가 2 증가합니다.\n"
        "[action] 마야 배드리 박사를 소진합니다: <b>조사.</b> 이 조사에서 당신의 기본 "
        "[intellect] 값은 당신의 손에 있는 카드 수와 같습니다."
    ),
    flavor='"섬뜩할 만큼 살아 있는 것 같지 않나요?"',
)

KO["71024"] = dict(
    nick="루카스 테틀로",
    desc="학예사",
    name="루카스 테틀로",
    subname="학예사",
    traits="조력자. 손님. 미스캐토닉.",
    text=(
        "[reaction] 당신의 장소에서 마지막 단서가 발견된 후, 루카스 테틀로를 소진합니다: "
        "당신의 덱 맨 위 9장에서 [[물품]] 자산 하나를 찾아 뽑습니다. 그것이 [[유물]]이라면, "
        "자원 2를 얻습니다. 그것이 [[고서]]라면, 그것을 플레이할 수 있습니다 "
        "<i>(비용을 지불하고)</i>. 그것이 [[도구]]라면, 이어진 장소에서 단서 1을 "
        "발견합니다. 당신의 덱을 섞습니다."
    ),
)

KO["71025"] = dict(
    nick="엘리자베스 콘래드",
    desc="곤드레만드레 취한",
    name="엘리자베스 콘래드",
    subname="곤드레만드레 취한",
    traits="조력자. 손님. 미스캐토닉.",
    text=(
        "[reaction] 당신의 턴 동안 카드를 뽑은 후, 엘리자베스 콘래드를 소진합니다: 당신의 "
        "장소에 있는 조사자 하나를 골라 이어진 장소로 이동시킵니다."
    ),
    flavor='"이 술 마셔본 사람 또 있어요? 끝내주는데!"',
)

KO["71026"] = dict(
    nick="혼란",
    desc="실책. 라이벌.",
    name="혼란",
    traits="실책. 라이벌.",
    text=(
        "급증. 혼란은 취소될 수 없습니다.\n"
        "<b>폭로</b> - 당신의 단서 2개를 당신의 장소에 놓습니다. 놓을 수 없는 단서 하나당, "
        "공포 1을 받습니다."
    ),
    flavor="이게 대체 뭐지???",
)

KO["71027"] = dict(
    nick="조직",
    desc="",
    name="조직 [rogue]",
    traits="협력.",
    text=(
        "[action] 단서 1 [per_investigator]를 소비합니다: 당신의 장소 아래에 있는 미스터리 "
        "<i>(뒷면이 보이는 카드)</i>를 확인합니다.\n"
        "[fast] 당신의 턴 동안, 미스터리가 있는 장소에 단서가 1 [per_investigator] 이하로 "
        "있다면: 그 미스터리를 뽑습니다.\n"
        "<b>목표</b> - 사르나스의 보석을 찾아 탈출합니다. 라운드 종료 시, 로비에 있는 "
        "조사자가 사르나스의 보석을 조종하고 있다면, 조사자들은 그룹으로서 단서 "
        "4 [per_investigator]를 소비해 주요목적을 진행할 수 있습니다.\n"
        "<b>게임 종료 시 점수 계산:</b>\n"
        "모든 조사자의 자원 총합 3 [per_investigator]당 1점(최대 4점)."
    ),
    flavor="이건 일일 뿐, 그 이상은 아니다. 보석을 들고 튈 수만 있다면 두둑한 보수가 따라올 것이다.",
    b=dict(
        name="조직 [rogue]",
        traits="라이벌.",
        text=(
            "<b>준비:</b> 따로 빼둔 불운의 연속 음모를 조우 덱에 섞어 넣습니다. 따로 빼둔 "
            "조니 발론 카드를 적 면이 보이게 주도 조사자의 장소에 플레이합니다.\n"
            "[action] 조니 발론이 당신의 장소에 있다면: <b>협상.</b> [agility]를 "
            "테스트합니다(3). 성공하면, 당신의 단서 1개를 이 이야기 카드에 놓습니다.\n"
            "<b>강제</b> - 이 이야기 카드에 단서가 1에 1 [per_investigator]를 더한 만큼 "
            "놓였을 때: 이 카드와 조니 발론을 승리 표시 영역에 추가합니다. 조우 덱과 버린 "
            "카드 더미에서 불운의 연속을 찾아 게임에서 제거합니다. 조우 덱을 섞습니다."
        ),
        flavor=(
            "조직은 쉽게 성을 내지만, 보석을 넘기는 편이 자신에게 이롭다고 조니 발론을 "
            "설득할 수 있을지도 모른다..."
        ),
    ),
)

KO["71028"] = dict(
    nick="조니 발론",
    desc="",
    name="조니 발론",
    subname="거래할 준비가 된 자",
    traits="지도자. 손님. 조직.",
    text=(
        "당신은 [agility] +1을 얻습니다.\n"
        "[reaction] 카드 효과로 자원을 1 이상 얻은 후, 조니 발론을 소진합니다: 카드 1장을 "
        "뽑고 자원 1을 얻습니다.\n"
        "<b>강제</b> - 조니 발론이 플레이에서 벗어났을 때: 그를 게임에서 제거합니다."
    ),
    flavor='"일 끝나면 50달러."',
    b=dict(
        name="조니 발론",
        subname="빚을 받으러 온 자",
        traits="인간형. 지도자. 라이벌. 정예.",
        text=(
            "냉담한. 사냥꾼.\n"
            "조니 발론은 피해를 입지 않습니다.\n"
            "<b>강제</b> - 당신이 조니 발론의 장소에서 턴을 끝냈을 때: 자원 2를 잃습니다."
        ),
        flavor='"빚은 갚아야지!"',
    ),
)

KO["71029"] = dict(
    nick="미란다 키퍼",
    desc='골동품 "거래상"',
    name="미란다 키퍼",
    subname='골동품 "거래상"',
    traits="조력자. 손님. 조직.",
    text=(
        '사용 ("물자" 3).\n'
        "[fast] 물자 1을 소비합니다: 당신의 장소에 자원 1을 골동품으로서 놓습니다. 이번 "
        "라운드의 다음 능력 테스트에 +2를 얻습니다.\n"
        "[reaction] 당신이 능력 테스트에 2 이상 차이로 성공한 후, 당신의 장소에서 골동품 "
        "하나를 제거합니다: 자원 2를 얻습니다."
    ),
    flavor='"당신 정말 웃겨 죽겠네."',
)

KO["71030"] = dict(
    nick="아르센 르나르",
    desc="괴도 신사",
    name="아르센 르나르",
    subname="괴도 신사",
    traits="조력자. 손님. 조직.",
    text=(
        "[fast]: 각 장소에 자원 1을 골동품으로서 놓습니다. (그룹 제한: 게임당 1회.)\n"
        "[fast]: 당신의 장소에 소진된 적이 있다면, 당신의 장소에서 골동품 하나를 "
        "제거합니다: 카드 1장을 뽑거나 자원 2를 얻습니다."
    ),
)

KO["71031"] = dict(
    nick="노바 말론",
    desc="위압적인 갱단원",
    name="노바 말론",
    subname="위압적인 갱단원",
    traits="조력자. 손님. 조직.",
    text=(
        "[action] 노바 말론을 소진합니다: <b>전투.</b> 이 전투에서 당신의 기본 [combat] 값은 "
        "당신이 보유한 자원의 수와 같습니다(최대 7). 이 공격은 피해 +1을 입힙니다.\n"
        "[reaction] 당신이 적을 쓰러뜨린 후: 자원 1을 얻습니다. (제한: 라운드당 1회.)"
    ),
)

KO["71032"] = dict(
    nick="불운의 연속",
    desc="불운. 라이벌.",
    name="불운의 연속",
    traits="불운. 라이벌.",
    text=(
        "급증. 불운의 연속은 취소될 수 없습니다.\n"
        "<b>폭로</b> - 자원 4를 잃습니다. 잃을 수 없는 자원 하나당, 피해 1 또는 공포 1을 "
        "받습니다."
    ),
    flavor="오늘 밤은 도무지 운이 따르지 않는다.",
)

KO["71033"] = dict(
    nick="은빛 황혼회",
    desc="",
    name="은빛 황혼회 [mystic]",
    traits="협력.",
    text=(
        "[action]: 보석의 기척을 감지하려 시도합니다. 조우 덱의 맨 위 카드를 버립니다. 그 "
        "카드를 뽑을 수 있습니다. (그룹 제한: 라운드당 2회.)\n"
        "<b>목표</b> - 사르나스의 보석을 찾습니다. 라운드 종료 시, 조사자들이 사르나스의 "
        "보석을 조종하고 있다면, 그들은 그룹으로서 단서 5 [per_investigator]를 소비해 "
        "주요목적을 진행할 수 있습니다.\n"
        "<b>게임 종료 시 점수 계산:</b>\n"
        "승리 표시 영역에 있는 승점 없는 음모 하나당 1점(최대 4점)."
    ),
    flavor="칼 샌포드는 보석을 손에 넣으면 후한 보상을 주겠다고 약속했다. 하지만 그를 정말 믿어도 될까?",
    b=dict(
        name="은빛 황혼회 [mystic]",
        traits="라이벌.",
        text=(
            "<b>준비:</b> 따로 빼둔 보존의 수호부 음모를 조우 덱에 섞어 넣습니다. 따로 빼둔 "
            "칼 샌포드 카드를 적 면이 보이게 주도 조사자의 장소에 플레이합니다.\n"
            "[action] 칼 샌포드가 당신의 장소에 있다면: <b>협상.</b> [willpower]를 "
            "테스트합니다(3). 성공하면, 당신의 단서 1개를 이 이야기 카드에 놓습니다.\n"
            "<b>강제</b> - 이 이야기 카드에 단서가 1에 1 [per_investigator]를 더한 만큼 "
            "놓였을 때: 이 카드와 칼 샌포드를 승리 표시 영역에 추가합니다. 조우 덱, 조우 "
            "버린 카드 더미, 그리고 모든 플레이 영역에서 보존의 수호부를 찾아 게임에서 "
            "제거합니다. 조우 덱을 섞습니다."
        ),
        flavor="어쩌면 샌포드와 황혼회를 부드럽게 설득해 이 일에서 손을 떼게 할 수 있을지도 모른다.",
    ),
)

KO["71034"] = dict(
    nick="칼 샌포드",
    desc="",
    name="칼 샌포드",
    subname="권능을 갈망하는 자",
    traits="지도자. 손님. 은빛 황혼회.",
    text=(
        "당신은 [willpower] +1을 얻습니다. 당신은 비술 슬롯을 하나 더 가집니다.\n"
        "[reaction] 약점이 아닌 음모의 폭로 효과가 취소된 후, 칼 샌포드를 소진합니다: 그 "
        "음모를 승리 표시 영역에 추가합니다. 카드 1장을 뽑습니다.\n"
        "<b>강제</b> - 칼 샌포드가 플레이에서 벗어났을 때: 그를 게임에서 제거합니다."
    ),
    b=dict(
        name="칼 샌포드",
        subname="위압적인 존재감",
        traits="인간형. 지도자. 라이벌. 정예.",
        text=(
            "냉담한. 사냥꾼.\n"
            "칼 샌포드는 피해를 입지 않습니다.\n"
            "이 장소에 있는 각 조사자는 [willpower] -1을 얻습니다."
        ),
        flavor='"보석은 내 것이다."',
    ),
)

KO["71035"] = dict(
    nick="프루던스 더글러스",
    desc="실용주의 신비학자",
    name="프루던스 더글러스",
    subname="실용주의 신비학자",
    traits="조력자. 손님. 주술사. 은빛 황혼회.",
    text=(
        '사용 ("징조" 3).\n'
        "[fast] 당신의 턴 동안, 징조 1을 소비하고 프루던스 더글러스를 소진합니다: 조우 덱의 "
        "맨 위 4장을 확인합니다. 그중 [[정예]]가 아닌 카드를 최대 2장까지 버리고, 나머지를 "
        "원하는 순서로 맨 위에 되돌려 놓습니다."
    ),
)

KO["71036"] = dict(
    nick="사라 밴 쇼",
    desc="회관 관리인",
    name="사라 밴 쇼",
    subname="회관 관리인",
    traits="조력자. 손님. 은빛 황혼회.",
    text=(
        "[action] 자원을 최대 2까지 소비하고 사라 밴 쇼를 소진합니다: <b>전투.</b> 이 "
        "공격에서 당신의 [willpower] 값을 능력값에 더합니다. 소비한 자원 하나당, 이 공격은 "
        "피해 +1을 입힙니다."
    ),
    flavor='"아레스, 하데스, 이리 와. 착하지!"',
)

KO["71037"] = dict(
    nick="레이먼드 로긴스",
    desc="수수께끼의 후원자",
    name="레이먼드 로긴스",
    subname="수수께끼의 후원자",
    traits="조력자. 손님. 주술사. 은빛 황혼회.",
    text=(
        '사용 ("진실" 4).\n'
        "[reaction] 당신이 약점이 아닌 음모를 뽑았을 때, 진실 1을 소비합니다: 그 카드의 폭로 "
        "효과를 취소하고 그 카드를 버립니다. 그런 다음, 공포 1을 받습니다."
    ),
)

KO["71038"] = dict(
    nick="보존의 수호부",
    desc="마법. 라이벌.",
    name="보존의 수호부",
    traits="마법. 라이벌.",
    text=(
        "급증. 보존의 수호부는 취소될 수 없습니다.\n"
        "<b>폭로</b> - 보존의 수호부를 당신의 위협 영역에 플레이합니다.\n"
        "<b>강제</b> - 당신이 사건을 플레이했을 때: 그 효과를 취소하고 공포 1을 받습니다. 그런 "
        "다음, 보존의 수호부를 버립니다."
    ),
)

KO["71039"] = dict(
    nick="킹스포트 주민들",
    desc="",
    name="킹스포트 주민들 [survivor]",
    traits="협력.",
    text=(
        "<b>강제</b> - 데클란 피어스가 쓰러졌을 때: 사르나스의 보석을 그의 장소에 "
        "부착합니다.\n"
        "[action] 사르나스의 보석이 당신의 장소에 부착되어 있다면: 보석에서 오염을 정화하려 "
        "시도합니다. [combat] 또는 [agility]를 테스트합니다(3). 성공하면, 그 위의 토큰 1개를 "
        "제거합니다. 그것을 조종할 수 있습니다.\n"
        "<b>목표</b> - 라운드 종료 시, 조사자들이 사르나스의 보석을 조종하고 있고 그 위에 "
        "토큰이 없다면, 그들은 그룹으로서 단서 3 [per_investigator]를 소비해 주요목적을 "
        "진행할 수 있습니다.\n"
        "<b>게임 종료 시 점수 계산:</b>\n"
        "4점에서 손님 덱에 있는 카드 하나당 1점씩 뺍니다(최소 0점)."
    ),
    flavor=(
        "베인은 보석에 깃든 위험한 힘을 넌지시 내비쳤다. 데클란 피어스에게서 그것을 빼앗을 "
        "수 있다면, 어쩌면 이 악몽은 끝날지도 모른다."
    ),
    b=dict(
        name="킹스포트 주민들 [survivor]",
        traits="라이벌.",
        text=(
            "<b>준비:</b> 따로 빼둔 운수 사나운 날 음모를 조우 덱에 섞어 넣습니다. 따로 빼둔 "
            "윌리엄 베인 카드를 적 면이 보이게 주도 조사자의 장소에 플레이합니다.\n"
            "[action] 윌리엄 베인이 당신의 장소에 있다면: <b>협상.</b> 아무 능력이나 "
            "테스트합니다(4). 성공하면, 당신의 단서 1개를 이 이야기 카드에 놓습니다.\n"
            "<b>강제</b> - 이 이야기 카드에 단서가 1에 1 [per_investigator]를 더한 만큼 "
            "놓였을 때: 이 카드와 윌리엄 베인을 승리 표시 영역에 추가합니다. 조우 덱, 조우 "
            "버린 카드 더미, 그리고 모든 플레이 영역에서 운수 사나운 날을 찾아 게임에서 "
            "제거합니다. 조우 덱을 섞습니다."
        ),
        flavor=(
            "주민들은 당신이 끼어든 것을 곱게 보지 않는 듯하다. 어쩌면 해를 끼칠 뜻이 없음을 "
            "보여줄 수 있을지도 모른다."
        ),
    ),
)

KO["71040"] = dict(
    nick="윌리엄 베인",
    desc="",
    name="윌리엄 베인",
    subname="실종자를 찾는 자",
    traits="지도자. 손님. 킹스포트.",
    text=(
        "당신이 수행하는 능력 테스트에 카드가 1장 이상 투입되어 있는 동안, 당신은 자신의 각 "
        "능력에 +1을 얻습니다.\n"
        "[reaction] 당신이 능력 테스트에 카드를 1장 이상 투입한 후, 자원 1 "
        "[per_investigator]를 소비하고 윌리엄 베인을 소진합니다: 손님 덱의 맨 위 카드를 "
        "공개해 로비에 플레이합니다.\n"
        "<b>강제</b> - 윌리엄 베인이 플레이에서 벗어났을 때: 그를 게임에서 제거합니다."
    ),
    b=dict(
        name="윌리엄 베인",
        subname="끝까지 굴하지 않는 자",
        traits="인간형. 지도자. 라이벌. 정예.",
        text=(
            "냉담한. 사냥꾼.\n"
            "윌리엄 베인은 피해를 입지 않습니다.\n"
            "이 장소에 있는 각 조사자는 능력 테스트에 카드를 투입할 수 없습니다."
        ),
        flavor='"허튼소리 말게. 자네가 그놈들 편인 걸 알아."',
    ),
)

KO["71041"] = dict(
    nick="딜로리스 개들링",
    desc="랜턴 클럽 잠입자",
    name="딜로리스 개들링",
    subname="랜턴 클럽 잠입자",
    traits="조력자. 손님. 킹스포트.",
    text=(
        "조사하거나 협상하는 동안, 당신의 장소에 있는 [[인간형]] 적 하나당 [intellect] +1을 "
        "얻습니다.\n"
        "[[인간형]]이면서 [[정예]]가 아닌 적이 당신에게 가하는 각 기회 공격을 무시할 수 "
        "있습니다."
    ),
    flavor='"그 클럽은 나에게 끔찍한 짓을 시켰어요. 그들을 막아야 해요."',
)

KO["71042"] = dict(
    nick="토머스 올니",
    desc="호기심 많은 모험가",
    name="토머스 올니",
    subname="호기심 많은 모험가",
    traits="조력자. 손님. 여행자.",
    text=(
        "[fast] [[특성]] 하나를 지목하고 토머스 올니를 소진합니다: 조우 덱의 맨 위 카드를 "
        "버립니다. 그 카드가 지목한 [[특성]]을 가지고 있다면, 자원 2를 얻습니다.\n"
        "[fast] 당신의 장소에서 능력 테스트가 진행되는 동안, 토머스 올니를 소진합니다: 당신의 "
        "덱 맨 위 카드를 버립니다. 가능하다면, 그 카드를 이 능력 테스트에 투입할 수 있습니다."
    ),
)

KO["71043"] = dict(
    nick="클레어 윌슨",
    desc="전혀 감흥 없는",
    name="클레어 윌슨",
    subname="전혀 감흥 없는",
    traits="조력자. 손님. 킹스포트.",
    text=(
        "[reaction] 당신이 수행하는 능력 테스트에 카드를 1장 이상 투입한 후, 클레어 윌슨을 "
        "소진합니다: 이 테스트에서 능력값 +1을 얻습니다."
    ),
    flavor='"그냥 집에 있을 걸 그랬어."',
)

KO["71044"] = dict(
    nick="운수 사나운 날",
    desc="불운. 라이벌.",
    name="운수 사나운 날",
    traits="불운. 라이벌.",
    text=(
        "급증. 운수 사나운 날은 취소될 수 없습니다.\n"
        "<b>폭로</b> - 운수 사나운 날을 당신의 위협 영역에 플레이합니다.\n"
        "당신은 자신의 각 능력에 -2를 얻습니다.\n"
        "<b>강제</b> - 라운드가 끝났을 때: 운수 사나운 날을 버립니다."
    ),
)

KO["71045"] = dict(
    nick="피 없는 사내",
    desc="",
    name="피 없는 사내",
    subname="말 없는 잠복자",
    traits="인간형. 랜턴 클럽. 정예.",
    text=(
        "냉담한. 순찰 ([[손님]]이 있는 가장 가까운 장소).\n"
        "<b>강제</b> - 이 적이 쓰러졌을 때: 대신 그를 뒤집고, 소진하고, 피해 3 "
        "[per_investigator]만 남기고 모두 회복합니다.\n"
        "<b>강제</b> - 조사 단계가 끝났을 때, 이 적이 준비 상태라면: 이 장소에 있는 각 "
        "조사자와 [[손님]] 자산에게 공포 1을 입힙니다. 창백한 랜턴이 이 적에게 부착되어 "
        "있다면, 이 장소에 있는 각 [[손님]] 자산은 홀린 상태가 됩니다."
    ),
    b=dict(
        name="피 없는 사내",
        subname="풀려난 자",
        traits="괴물. 흉물. 정예.",
        text=(
            "사냥꾼. 거대한.\n"
            "<b>강제</b> - 이 적이 당신을 공격한 후: 당신이 조종하는 [[손님]] 자산 하나를 "
            "주요사건의 <b>강제</b> 효과를 무시하고 게임에서 제거합니다."
        ),
    ),
)

KO["71046"] = dict(
    nick="창백한 랜턴",
    desc="물품. 유물.",
    name="창백한 랜턴",
    subname="최면의 빛",
    traits="물품. 유물.",
    text=(
        "[action] 피 없는 사내에게 피해가 1 이상 있거나 그가 승리 표시 영역에 있다면: "
        "[combat] 또는 [agility]를 테스트합니다(3). 성공하면, 창백한 랜턴을 조종하고 그것을 "
        "뒤집습니다. 이 장소에 있는 조사자라면 누구나 이 기능을 격발할 수 있습니다.\n"
        "<b>강제</b> - 창백한 랜턴이 플레이에서 벗어나려 할 때: 대신 그것을 그 장소에 "
        "부착합니다."
    ),
    b=dict(
        name="창백한 랜턴",
        subname="현혹하는 기운",
        traits="물품. 유물.",
        text=(
            "[action]: 당신의 장소에 있는 홀린 카드 하나를 뒤집습니다.\n"
            "[action] 창백한 랜턴을 소진합니다: [combat] 또는 [agility]를 테스트합니다(2). "
            "성공하면, 그 위에 피해 1을 놓습니다. 그런 다음, 그 위에 피해가 4 있다면, 당신은 "
            "그것을 부숩니다. 그것을 (그 <b>강제</b> 효과를 무시하고) 승리 표시 영역에 "
            "추가하고 각 홀린 카드를 뒤집습니다.\n"
            "<b>강제</b> - 창백한 랜턴이 플레이에서 벗어나려 할 때: 대신 그것을 뒤집어 그 "
            "장소에 부착합니다."
        ),
    ),
)

KO["71047"] = dict(
    nick="격노한 구그",
    desc="괴물. 드림랜드. 구그.",
    name="격노한 구그",
    traits="괴물. 드림랜드. 구그.",
    text=(
        "경계. 사냥꾼.\n"
        "이 적이 입히는 공포는 가능하다면 당신이 조종하는 [[손님]] 자산에 할당해야 합니다."
    ),
    flavor='"이런 여흥은 기대하지 않았는데!" - 엘리자베스 콘래드',
)

KO["71048"] = dict(
    nick="혐오스러운 달짐승",
    desc="괴물. 드림랜드. 시종.",
    name="혐오스러운 달짐승",
    traits="괴물. 드림랜드. 시종.",
    text=(
        "사냥꾼. 보복.\n"
        "이 적이 입히는 피해는 가능하다면 당신이 조종하는 [[손님]] 자산에 할당해야 합니다."
    ),
    flavor='"삶이 예술을 모방하는 일이 예술이 삶을 모방하는 일보다 훨씬 많다." - 오스카 와일드',
)

KO["71049"] = dict(
    nick="사나운 샨타크",
    desc="괴물. 드림랜드. 샨타크.",
    name="사나운 샨타크",
    traits="괴물. 드림랜드. 샨타크.",
    text=(
        "사냥꾼.\n"
        "사나운 샨타크는 자신의 장소에 있는 [[손님]] 자산 하나당 전투값 +1과 회피값 +1을 "
        "얻습니다."
    ),
    flavor=(
        "그리고 그들은 소문으로 떠도는 샨타크 새들이 결코 온전한 것이 아니라고도 속삭였다... "
        "- H.P. 러브크래프트, <u>미지의 카다스를 향한 몽환의 추적</u>"
    ),
)

KO["71050"] = dict(
    nick="잔혹한 매복",
    desc="책략.",
    name="잔혹한 매복",
    traits="책략.",
    text=(
        "<b>폭로</b> - [[괴물]] 적이 플레이 중에 없다면, 잔혹한 매복은 급증을 얻습니다. 그 "
        "외의 경우, 가장 가까운 [[괴물]] 적이 준비 상태가 되고, (한 번에 한 장소씩) 당신의 "
        "장소에 도달할 때까지 이동해, 당신과 교전하고 즉시 공격합니다."
    ),
)

KO["71051"] = dict(
    nick="데클란 피어스",
    desc="랜턴 클럽 수장",
    name="데클란 피어스",
    subname="랜턴 클럽 수장",
    traits="인간형. 랜턴 클럽. 정예.",
    text=(
        "사냥꾼.\n"
        "데클란 피어스는 체력 +2 [per_investigator]를 얻습니다.\n"
        "데클란 피어스가 당신과 교전 중인 동안, 당신은 자신의 턴에 행동을 1 적게 가집니다.\n"
        "<b>강제</b> - 적 단계 종료 시, 데클란 피어스가 준비 상태라면: 플레이 중인 각 적의 "
        "피해 1을 회복합니다."
    ),
)

KO["71052"] = dict(
    nick="사르나스의 보석",
    desc="꿈을 현실로 바꾸는",
    name="사르나스의 보석",
    subname="꿈을 현실로 바꾸는",
    traits="물품. 유물.",
    text=(
        "<b>폭로</b> - 사르나스의 보석에 피해 3과 파멸 1을 놓은 채 당신의 조종하에 "
        "플레이합니다.\n"
        "<b>강제</b> - 사르나스의 보석이 플레이에서 벗어났을 때: 그것을 조우 덱에 섞어 "
        "넣습니다.\n"
        "[fast] 사르나스의 보석을 소진합니다: [willpower] 또는 [agility]를 테스트합니다(1). "
        "성공하면, 그 위의 토큰 1개를 당신의 장소에 있는 적에게 옮깁니다."
    ),
)

KO["71053"] = dict(
    nick="랜턴 클럽 회원",
    desc="인간형. 랜턴 클럽.",
    name="랜턴 클럽 회원",
    traits="인간형. 랜턴 클럽.",
    text=(
        "[action] 자원 1을 소비합니다: <b>협상.</b> 이 적을 자동으로 회피합니다(자원 2를 "
        "추가로 소비해 대신 이 적을 버릴 수 있습니다)."
    ),
    flavor='"피어스 수장께서 대단한 밤을 준비했다더군."',
)

KO["71054"] = dict(
    nick="피 흘리는 벽",
    desc="두려움.",
    name="피 흘리는 벽",
    traits="두려움.",
    text=(
        "<b>폭로</b> - [willpower]를 테스트합니다(X). X는 당신의 장소의 장막값입니다. "
        "실패하면, 행동 1을 잃고 공포 1을 받습니다."
    ),
    flavor="어차피 새로 단장할 때가 되긴 했다.",
)

KO["71055"] = dict(
    nick="덫",
    desc="위험.",
    name="덫",
    traits="위험.",
    text=(
        "<b>폭로</b> - [agility]를 테스트합니다(3). 실패했고...\n"
        "- ...피 없는 사내가 당신의 장소에 있다면, 당신이 조종하는 [[손님]] 자산 하나가 홀린 "
        "상태가 됩니다.\n"
        "- ...피 없는 사내가 다른 장소에 있다면, 그가 준비 상태가 되고, (한 번에 한 장소씩) "
        "당신의 장소에 도달할 때까지 이동해, 당신과 교전합니다.\n"
        "- ...피 없는 사내가 플레이 중이 아니라면, 덫은 급증을 얻습니다."
    ),
)

KO["71056"] = dict(
    nick="설명할 수 없는 한기",
    desc="위험.",
    name="설명할 수 없는 한기",
    traits="위험.",
    text=(
        "<b>폭로</b> - [agility]를 테스트합니다(4). 실패한 차이 1당, 다음 중 하나를 골라야 "
        "합니다:\n"
        "- 당신의 플레이 영역에서 카드 1장을 골라 버립니다.\n"
        "- 당신의 손에서 카드 1장을 골라 버립니다.\n"
        "- 피해 1을 받습니다."
    ),
)

KO["71057"] = dict(
    nick="정신 추출",
    desc="권능.",
    name="정신 추출",
    traits="권능.",
    text=(
        "<b>폭로</b> - [willpower]를 테스트합니다(X). X는 현재 주요사건 번호에 1을 더한 "
        "값입니다. 실패하면, 공포 2를 받습니다. 그런 다음, 피 없는 사내가 플레이 중이고 어느 "
        "조사자가 창백한 랜턴을 조종하고 있다면, 그것을 뒤집어 피 없는 사내에게 부착합니다 "
        "<i>(그 위의 토큰은 모두 그대로 둡니다)</i>."
    ),
)

KO["71058"] = dict(
    nick="유독 가스",
    desc="위험.",
    name="유독 가스",
    traits="위험.",
    text=(
        "<b>폭로</b> - 플레이어 순서대로, 당신의 장소에 있는 각 조사자는 다음 중 하나를 "
        "골라야 합니다:\n"
        "- 가스가 방을 채우기 전에 달아나려 시도하며 [agility]를 테스트합니다(3). 성공하면, "
        "이어진 장소로 이동합니다. 실패하면, 피해 2를 받습니다.\n"
        "- 숨을 참으려 시도하며 [combat]을 테스트합니다(3). 실패한 차이 1당, 피해 1을 "
        "받습니다."
    ),
)

KO["71059"] = dict(
    nick="너머로 떠밀리다",
    desc="주술.",
    name="너머로 떠밀리다",
    traits="주술.",
    text=(
        "<b>폭로</b> - 당신이 조종하는 이야기가 아닌 자산 하나를 골라 그 소유자의 덱에 섞어 "
        "넣습니다. 그 덱의 맨 위 3장을 버립니다. 그 자산과 같은 이름의 카드가 버려졌다면, "
        "공포 2를 받습니다."
    ),
    flavor="밀어내라.",
)

KO["71060"] = dict(
    nick="비밀 문",
    desc="장애물.",
    name="비밀 문",
    traits="장애물.",
    text=(
        "<b>폭로</b> - 조사자가 가장 많고 비밀 문이 부착되어 있지 않은 장소에 부착합니다.\n"
        "조사자들은 이 장소를 떠날 수 없습니다.\n"
        "[action]: 통로를 찾기 위해 [willpower]를 테스트하거나(3) 빠져나갈 길을 알아내기 위해 "
        "[intellect]를 테스트합니다(3). 성공하면, 비밀 문을 버립니다."
    ),
)

KO["71061"] = dict(
    nick="두려움의 문",
    desc="두려움.",
    name="두려움의 문",
    traits="두려움.",
    text=(
        "<b>폭로</b> - [willpower]를 테스트합니다(2). [[라이벌]] 적이 플레이 중이라면 이 "
        "테스트는 난이도 +2를 얻습니다. 실패했고 당신이 협력한 진영이...\n"
        "- ...[guardian] 진영이라면, 피해 2를 받습니다.\n"
        "- ...[seeker] 진영이라면, 손에서 카드 2장을 버립니다.\n"
        "- ...[rogue] 진영이라면, 자원 3을 잃습니다.\n"
        "- ...[mystic] 진영이라면, 공포 2를 받습니다.\n"
        "- ...[survivor] 진영이라면, 행동 1을 잃습니다."
    ),
)

KO["71062"] = dict(
    nick="폭력적인 명령",
    desc="두려움.",
    name="폭력적인 명령",
    traits="두려움.",
    text=(
        "<b>폭로</b> - 폭력적인 명령을 당신의 위협 영역에 플레이합니다.\n"
        "[action]: 당신의 장소에 있는 조사자 하나에게 피해 2를 입히고 폭력적인 명령을 "
        "버립니다.\n"
        "<b>강제</b> - 당신의 턴 종료 시: [willpower]를 테스트합니다(3). 실패하면, 공포 1을 "
        "받습니다."
    ),
)


# --- structural checks and emit ---------------------------------------------

ICON_RE = re.compile(r"\[([a-z_]+)\]")          # [action], [per_investigator], ...
TRAIT_RE = re.compile(r"\[\[([^\]]+)\]\]")      # [[Guest]] / [[손님]]
TAG_RE = re.compile(r"</?([a-z]+)>")            # <b> <i> <u> <blockquote> <br> <hr>


def icons(s):
    return sorted(ICON_RE.findall(s or ""))


def trait_marker_count(s):
    return len(TRAIT_RE.findall(s or ""))


def tags(s):
    return sorted(TAG_RE.findall(s or ""))


def check(cards_en):
    """Every structural problem the table can carry, as a list of strings.

    Kept as the generator's OWN check and never as the only one: design 5.2's
    rule is `verify-a4.py:4-7`'s -- "This is deliberately NOT a re-run of
    translate-a3.py's own check(). A3 validates with regex multisets; this file
    scans character by character with a hand-written tokenizer, so a bug in one
    regex cannot pass both."
    """
    problems = []
    en_codes = [c["code"] for c in cards_en]
    extra = [c for c in KO if c not in en_codes]
    if extra:
        problems.append("Korean entry for unknown code: %s" % sorted(extra))

    def compare(code, field, en_val, ko_val, where):
        en_val = en_val or ""
        ko_val = ko_val or ""
        if bool(en_val) != bool(ko_val):
            problems.append("%s %s.%s: presence mismatch (en=%s, ko=%s)"
                            % (code, where, field,
                               "set" if en_val else "empty",
                               "set" if ko_val else "empty"))
            return
        if not en_val:
            return
        if icons(en_val) != icons(ko_val):
            problems.append("%s %s.%s: icon tokens differ %s vs %s"
                            % (code, where, field, icons(en_val), icons(ko_val)))
        if trait_marker_count(en_val) != trait_marker_count(ko_val):
            problems.append("%s %s.%s: [[Trait]] ref count %d vs %d"
                            % (code, where, field, trait_marker_count(en_val),
                               trait_marker_count(ko_val)))
        if tags(en_val) != tags(ko_val):
            problems.append("%s %s.%s: html tags differ %s vs %s"
                            % (code, where, field, tags(en_val), tags(ko_val)))
        if en_val.count("\n") != ko_val.count("\n"):
            problems.append("%s %s.%s: line count %d vs %d"
                            % (code, where, field, en_val.count("\n"),
                               ko_val.count("\n")))

    for c in cards_en:
        code = c["code"]
        ko = KO.get(code)
        if ko is None:
            continue
        for f in FRONT_FIELDS:
            compare(code, f, c.get(f), ko.get(f), "front")
        if bool((c.get("_atlas") or {}).get("description")) != bool(ko.get("desc", "")):
            problems.append("%s _atlas.description: presence mismatch" % code)
        b_en, b_ko = c.get("b_side"), ko.get("b")
        if bool(b_en) != bool(b_ko):
            problems.append("%s: b_side presence mismatch" % code)
        elif b_en:
            for f in B_FIELDS:
                compare(code, f, b_en.get(f), b_ko.get(f), "b_side")
    return problems


def render(source):
    """The English source in, the Korean document out. ONE-DIRECTIONAL.

    `KO` supplies Korean text, the English source supplies identity, and a card
    `KO` does not carry is emitted WITH ITS ENGLISH TEXT. So the output is TOTAL
    over the source rather than silently short, and a card nobody has translated
    is visible as English rather than absent. `counts.untranslated` is the
    declared remainder.
    """
    cards_en = source["cards"]
    out_cards = []
    untranslated = []
    for c in cards_en:
        code = c["code"]
        ko = KO.get(code) or {}
        if code not in KO:
            untranslated.append(code)
        atlas = c["_atlas"]
        rec = {
            "code": code,
            "arkham_id": code,
            "guid": atlas["guid"],
            "card_id": atlas["card_id"],
            "deck_key": atlas["deck_key"],
            "sheet": atlas["sheet"],
            "cell": atlas["cell"],
            "pack": atlas["pack"],
            "type_name": c["type_name"],
            "nickname_en": atlas["nickname"],
            "description_en": atlas["description"],
            "nickname_ko": ko.get("nick", atlas["nickname"] or ""),
            "description_ko": ko.get("desc", atlas["description"] or ""),
        }
        for f in FRONT_FIELDS:
            value = ko.get(f) or c.get(f)
            if value:
                rec[f] = value
        b_en, b_ko = c.get("b_side"), ko.get("b") or {}
        if b_en:
            b_rec = {}
            for f in B_FIELDS:
                value = b_ko.get(f) or b_en.get(f)
                if value:
                    b_rec[f] = value
            rec["b_side"] = b_rec
        out_cards.append(rec)

    ko_chars = 0
    for rec in out_cards:
        for f in FRONT_FIELDS:
            ko_chars += len(rec.get(f, ""))
        for f in B_FIELDS:
            ko_chars += len(rec.get("b_side", {}).get(f, ""))

    return {
        "schema_version": "1.0.0",
        "generated_by": "midwinter-ko.py",
        "slug": "midwinter",
        "source": "card-source-en.json",
        "terminology": "data/terms/midwinter.json",
        "note": (
            "Korean card text for the 62 Midwinter Gala cards, keyed by "
            "arkham_id. [icon] tokens are verbatim English (they render as font "
            "glyphs); [[Trait]] markers are preserved with the trait name "
            "translated; html tags and line structure match the English exactly. "
            "nickname_ko/description_ko are the printed TTS object text."),
        "counts": {
            "cards": len(out_cards),
            "with_b_side": sum(1 for r in out_cards if "b_side" in r),
            "empty_descriptions": sum(1 for r in out_cards
                                      if not r["description_ko"]),
            "translated_chars": ko_chars,
            "untranslated": len(untranslated),
        },
        "untranslated": untranslated,
        "cards": out_cards,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render the Midwinter Korean card text from the KO table.")
    parser.add_argument("--source", required=True,
                        help="<run_dir>/card-source-en.json")
    parser.add_argument("--out", required=True,
                        help="<run_dir>/card-text-ko.json")
    parser.add_argument("--check-only", action="store_true",
                        help="run the structural checks and write nothing")
    args = parser.parse_args(argv)

    with open(args.source, "r", encoding="utf-8") as handle:
        source = json.load(handle)

    problems = check(source["cards"])
    if problems:
        print("FAIL -- %d structural problem(s):" % len(problems), file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1
    if args.check_only:
        print("OK -- %d cards, no structural problems" % len(source["cards"]))
        return 0

    doc = render(source)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    counts = doc["counts"]
    print("OK -- wrote %s (%d cards, %d Korean chars, %d b_side, "
          "%d empty descriptions, %d untranslated)"
          % (args.out, counts["cards"], counts["translated_chars"],
             counts["with_b_side"], counts["empty_descriptions"],
             counts["untranslated"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
