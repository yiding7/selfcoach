"""器械计量口径表的测试。

这张表存在的理由是一个具体的错数：训记的 `unilateral` 是记录格式标记，
不是解剖学标记，于是 `哑铃卧推`（两只手各一个哑铃）和 `哑铃保加利亚蹲`
（两手各拎一个，但左右腿分别各做一组）被算成同一类，
`单臂哑铃划船 15kg` 被报成顶组 30kg。

所以测试盯四件事：

1. **关键词顺序**。更具体的必须排在前面，否则永远命中不到 —— 这是这类
   表最容易悄悄坏掉的地方，加一条新规则时很容易插错位置
2. **认不出来要能说出来**（`is_default`）。静默按默认值算，比报错危险
3. **表坏掉退回最保守的一档**，并且要在 `warnings()` 里出声
4. 本地真实存在的动作名都落在预期那一类
"""

from __future__ import annotations

import json

import pytest

from health_assistant import loading


@pytest.fixture(autouse=True)
def _clear_caches():
    loading._table.cache_clear()
    loading.classify.cache_clear()
    yield
    loading._table.cache_clear()
    loading.classify.cache_clear()


class TestTheFourClasses:
    """四种组合都真实存在，例子取自本地记录。"""

    @pytest.mark.parametrize("name,implements,sides", [
        # pair + both —— 每手一个哑铃，同时做。等效负荷 = 两只相加
        ("哑铃卧推", "pair", "both"),
        ("上斜哑铃卧推", "pair", "both"),
        ("哑铃推肩", "pair", "both"),
        ("哑铃飞鸟", "pair", "both"),
        ("仰卧哑铃臂屈伸", "pair", "both"),
        ("哑铃锤式交替弯举", "pair", "both"),
        # pair + per_side —— 两手各拎一个，但左右分别各做一组
        ("哑铃保加利亚蹲", "pair", "per_side"),
        ("哑铃箭步蹲", "pair", "per_side"),
        ("原地箭步蹲", "pair", "per_side"),
        # single + both —— 一个器械双手持
        ("哑铃酒杯深蹲", "single", "both"),
        ("哑铃臀冲", "single", "both"),
        ("哑铃相扑蹲", "single", "both"),
        ("哑铃杠杆握法相扑深蹲", "single", "both"),
        # single + per_side —— 一个器械，左右轮流
        ("单臂哑铃划船", "single", "per_side"),
        # 非哑铃：全部走默认
        ("杠铃卧推", "single", "both"),
        ("直杆绳索下压", "single", "both"),
        ("哈克机深蹲", "single", "both"),
    ])
    def test_known_movements_land_in_the_right_class(self, name, implements, sides):
        got = loading.classify(name)
        assert (got.implements, got.sides) == (implements, sides), got.matched


class TestKeywordOrder:
    """这类表最容易坏的地方：新规则插错位置，具体的被笼统的吃掉。"""

    def test_single_arm_row_beats_the_generic_dumbbell_rule(self):
        """`单臂哑铃划船` 里同时含「单臂哑铃」和「哑铃」。

        如果笼统的「哑铃」规则排在前面，它会被判成 pair —— 顶组直接翻倍，
        15kg 报成 30kg。这正是这张表要修的那个错。
        """
        got = loading.classify("单臂哑铃划船")
        assert got.implements == "single", \
            f"被「{got.matched}」抢先命中了 —— 具体关键词必须排在笼统的前面"

    def test_goblet_squat_beats_the_generic_dumbbell_rule(self):
        got = loading.classify("哑铃酒杯深蹲")
        assert got.implements == "single", got.matched

    def test_bulgarian_beats_the_generic_dumbbell_rule(self):
        got = loading.classify("哑铃保加利亚蹲")
        assert got.sides == "per_side", got.matched

    def test_every_rule_is_reachable(self):
        """有规则永远命中不到就是死规则 —— 它给人一种「已经处理了」的错觉。

        判据：拿每条规则自己的关键词去分类，必须命中它自己那一条。
        """
        table = json.loads(loading.LOADING_PATH.read_text(encoding="utf-8"))
        for i, rule in enumerate(table["rules"], 1):
            for kw in rule["match"]:
                got = loading.classify(kw)
                assert (got.implements, got.sides) == (rule["implements"], rule["sides"]), \
                    (f"第 {i} 条规则的关键词「{kw}」被「{got.matched}」抢先命中 —— "
                     f"把它挪到那一条之前")


class TestDefaultIsNotSilent:
    def test_an_unknown_movement_is_flagged_as_default(self):
        got = loading.classify("某某新奇器械推举")
        assert got.is_default, "认不出来必须能说出来，静默按默认值算比报错危险"
        assert got.factor == 1, "认不出来时不放大任何数字"

    def test_a_known_movement_is_not_flagged(self):
        assert not loading.classify("哑铃卧推").is_default


class TestFactorAndSets:
    def test_pair_doubles_and_single_does_not(self):
        assert loading.classify("哑铃卧推").factor == 2
        assert loading.classify("哑铃酒杯深蹲").factor == 1
        assert loading.classify("杠铃卧推").factor == 1

    def test_per_side_sets_are_marked(self):
        """标出来才能避免把「右腿那组」和「左腿那组」相加当成一次提举负荷。"""
        assert loading.classify("哑铃保加利亚蹲").per_side_sets
        assert loading.classify("单臂哑铃划船").per_side_sets
        assert not loading.classify("哑铃卧推").per_side_sets


class TestBrokenTableFailsLoudAndSafe:
    def test_a_missing_table_falls_back_to_the_safest_reading(self, monkeypatch, tmp_path):
        monkeypatch.setattr(loading, "LOADING_PATH", tmp_path / "nope.json")
        loading._table.cache_clear()
        loading.classify.cache_clear()
        got = loading.classify("哑铃卧推")
        assert (got.implements, got.sides) == loading.FALLBACK
        assert got.factor == 1, "表没了就不该再给任何动作翻倍"
        assert any("少算一半" in w or "缺少" in w for w in loading.warnings()), \
            "表缺失必须出声 —— 悄悄少算一半吨位是最坏的失败模式"

    def test_a_corrupt_table_says_it_did_not_take_effect(self, monkeypatch, tmp_path):
        bad = tmp_path / "implement-loading.json"
        bad.write_text("{ 这不是 json", encoding="utf-8")
        monkeypatch.setattr(loading, "LOADING_PATH", bad)
        loading._table.cache_clear()
        loading.classify.cache_clear()
        assert loading.classify("哑铃卧推").factor == 1
        assert any("没有生效" in w for w in loading.warnings())

    def test_an_invalid_rule_is_skipped_and_reported(self, monkeypatch, tmp_path):
        bad = tmp_path / "implement-loading.json"
        bad.write_text(json.dumps({
            "default": {"implements": "single", "sides": "both"},
            "rules": [{"match": ["哑铃"], "implements": "双手", "sides": "both"}],
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(loading, "LOADING_PATH", bad)
        loading._table.cache_clear()
        loading.classify.cache_clear()
        assert loading.classify("哑铃卧推").is_default, "不合法的规则要跳过，不能半信半疑地用"
        assert any("不合法" in w for w in loading.warnings())


class TestUnconfirmedClassesAreAdvertised:
    """推断当成结论用，会让一个差一倍的顶组被当成事实引用。

    所以只要表里还有 `needs_confirmation`，`hc doctor` 就得说出来。
    测机制而不是测当前状态 —— 当前状态会随着口径被逐个确认而变，
    而「有待确认项必须出声」这条永远成立。
    """

    def test_the_shipped_table_has_nothing_left_unconfirmed(self):
        """三个口径问题在 2026-08-11 都确认了，表里不该再有推断项。"""
        table = json.loads(loading.LOADING_PATH.read_text(encoding="utf-8"))
        pending = [r["match"] for r in table["rules"] if r.get("needs_confirmation")]
        assert not pending, f"这些还挂着推断标记：{pending}"
        assert not any("还没确认" in w for w in loading.warnings())

    def test_an_unconfirmed_rule_would_be_advertised(self, monkeypatch, tmp_path):
        """机制本身：一旦有人加了推断项，必须能在 warnings 里看到。"""
        t = tmp_path / "implement-loading.json"
        t.write_text(json.dumps({
            "default": {"implements": "single", "sides": "both", "why": "兜底"},
            "rules": [{"match": ["某某新动作"], "implements": "pair", "sides": "both",
                       "needs_confirmation": True, "why": "推断的"}],
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(loading, "LOADING_PATH", t)
        loading._table.cache_clear()
        loading.classify.cache_clear()
        assert any("还没确认" in w and "某某新动作" in w for w in loading.warnings())


class TestTableContract:
    def test_the_table_documents_why_for_every_rule(self):
        """`why` 不是装饰：下一个人（或下一个模型）要能看懂为什么这么分。"""
        table = json.loads(loading.LOADING_PATH.read_text(encoding="utf-8"))
        assert table["default"].get("why")
        for rule in table["rules"]:
            assert rule.get("why"), rule["match"]

    def test_the_table_carries_no_identity_information(self):
        """这张表在 knowledge/ 下，会进公开版本库。"""
        text = loading.LOADING_PATH.read_text(encoding="utf-8")
        for banned in ("Tim", "Timothy"):
            assert banned not in text
