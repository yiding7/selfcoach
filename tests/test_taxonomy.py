"""肌群分类器的验收测试。

**验收集全部来自真实数据里 `type` 字段为空的动作** —— 也就是兜底分类器
真正要负责的那些。这些动作在训记接口里拿不到肌群，只能靠动作名推断。
"""

from __future__ import annotations

import pytest

from health_assistant.taxonomy import UNKNOWN, classify, normalize_group


class TestApiTypeWins:
    """接口给了 type 就直接用，它比任何关键词规则都可信。"""

    @pytest.mark.parametrize("name,raw_type,expected", [
        ("哑铃弯举", "二头", "二头"),
        ("器械推胸（版本2）", "胸", "胸"),
        ("悍马机三头下压", "三头", "三头"),
        ("绳索直臂下压", "背", "背"),     # 接口说是背，别被「下压」带偏
        ("仰卧抬腿", "腹部", "腹部"),
        ("硬拉", "腿", "腿"),             # 训记自己归在腿，我们跟随
    ])
    def test_uses_api_type(self, name, raw_type, expected):
        c = classify(name, raw_type)
        assert c.group == expected
        assert c.source == "xunji_type"


class TestFallbackClassifier:
    """真实数据里 type 为空的动作 —— 兜底分类器的主战场。

    2026-07-12 那次训练 8 个动作有 5 个 type 为空；
    2026-07-05 有 2 个；2026-07-01 有 5 个；2026-06-30 有 3 个。
    """

    @pytest.mark.parametrize("name,expected", [
        # 2026-07-12 胸日
        ("器械胸部推举", "胸"),
        ("杠杆式上斜推胸", "胸"),
        ("器械推胸（版本4）", "胸"),
        ("把手式蝴蝶机飞鸟", "胸"),
        ("直杆绳索下压", "三头"),
        # 2026-07-05 背日
        ("杠铃划船", "背"),
        ("器械划船2", "背"),
        # 2026-07-01 腿日
        ("腿举", "腿"),
        ("坐姿腿弯举", "腿"),
        ("坐姿腿屈伸", "腿"),
        ("器械后蹬", "臀"),
        ("坐姿髋外展", "臀"),
        # 2026-06-30 胸日
        ("哑铃卧推", "胸"),
        ("哑铃飞鸟", "胸"),
        ("上斜哑铃三头伸展", "三头"),
        # 2026-07-07 腹部日
        ("平板蝴蝶收腹", "腹部"),
    ])
    def test_classifies_without_api_type(self, name, expected):
        c = classify(name, None)
        assert c.group == expected, f"{name} 应归入 {expected}，实际 {c.group}"
        assert c.source in ("taxonomy", "rule")


class TestOrderingTraps:
    """这些是排序错了就会分错的坑，每一个都真实存在于动作表里。"""

    @pytest.mark.parametrize("name,expected,trap", [
        ("平板蝴蝶收腹", "腹部", "含「蝴蝶」但不是胸"),
        ("绳索直臂下压", "背", "含「下压」但不是三头"),
        ("坐姿髋外展", "臀", "含「外展」但不是肩"),
        ("器械后蹬", "臀", "含「蹬」但不是腿"),
        ("上台阶俯卧撑", "胸", "含「上台阶」但不是腿"),
        ("坐姿腿弯举", "腿", "含「弯举」但不是二头"),
        ("负重颈弯举", "颈", "含「弯举」但不是二头"),
        ("直立划船", "肩", "含「划船」但不是背"),
        ("双杠臂屈伸", "胸", "训记把它归在胸部区段"),
        ("平板哑铃臂屈伸（单）", "三头", "普通「臂屈伸」是三头"),
    ])
    def test_trap(self, name, expected, trap):
        assert classify(name, None).group == expected, trap


class TestOverrides:
    def test_learned_override_wins(self, tmp_path, monkeypatch):
        import health_assistant.taxonomy as tax
        monkeypatch.setattr(tax, "OVERRIDES_PATH", tmp_path / "ov.json")
        tax._overrides.cache_clear()
        tax.learn("悍马机-鹦鹉螺", "背")
        c = tax.classify("悍马机-鹦鹉螺", None)
        assert c.group == "背"
        assert c.source == "override"
        tax._overrides.cache_clear()

    def test_override_beats_api_type(self, tmp_path, monkeypatch):
        """用户教的优先级最高 —— 连接口返回的 type 也能覆盖。"""
        import health_assistant.taxonomy as tax
        monkeypatch.setattr(tax, "OVERRIDES_PATH", tmp_path / "ov.json")
        tax._overrides.cache_clear()
        tax.learn("硬拉", "背")
        assert tax.classify("硬拉", "腿").group == "背"
        tax._overrides.cache_clear()


class TestCoverage:
    def test_catalog_coverage_is_high(self):
        """官方 1092 个动作名的覆盖率不应回退。"""
        from health_assistant.taxonomy import _taxonomy
        assert len(_taxonomy()) >= 1080

    def test_unknown_is_graceful(self):
        c = classify("某个完全没见过的自定义动作XYZ", None)
        assert c.group == UNKNOWN
        assert c.source == "unknown"
        assert not c.is_known


class TestXunjiTypeIsNotAlwaysAMuscleGroup:
    """训记的 type 字段里混着动作**格式**标签，不只是肌群。
    实测出现过：计时动作、功能性、核心稳定。

    早先原样收下，结果「平板支撑_计时」被判成「计时动作」这个伪肌群，
    跟同一个动作的「平板支撑」（腹部）不可比，也不计入腹部容量。
    """

    @pytest.mark.parametrize("raw_type", ["计时动作", "功能性"])
    def test_format_label_falls_through_to_taxonomy(self, raw_type):
        c = classify("平板支撑_计时", raw_type)
        assert c.group == "腹部"
        assert c.source != "xunji_type"

    def test_core_stability_is_aliased_to_abs(self):
        assert normalize_group("核心稳定") == "腹部"

    def test_real_group_still_wins(self):
        """认得出的肌群仍然优先于本地分类表 —— 这条不能被上面的改动破坏。"""
        c = classify("杠铃卧推", "胸")
        assert c.group == "胸"
        assert c.source == "xunji_type"

    def test_unknown_type_does_not_become_a_group(self):
        assert normalize_group("计时动作") is None
