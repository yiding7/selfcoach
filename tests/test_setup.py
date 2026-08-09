"""`hc setup` 的回归测试。

这个模块会**写别人的健康档案**，所以测试盯的是「不该动的别动」：

1. 改忌口时只动那一行，文件其余部分逐字节不变
2. 找不到目标行时宁可不改并说清楚，不要瞎猜位置
3. 数值冲突（同一个值出现在两个文件且对不上）要能被抓出来
"""

from __future__ import annotations

import json

import pytest

from health_assistant import setup as setup_mod

ORIGINAL = """# 个人健康约束（饮食与训练）

> 私密文件，不进版本库。

## 代谢相关

| 约束 | 含义 |
|---|---|
| 高尿酸 | 高嘌呤食物需控制 |

## 忌口

不吃：青椒、茄子。无过敏。

> ⚠️ 上面这一行是 hc dice 读的，格式别改。

## 饮酒

每月最多一次。
"""


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    constraints = tmp_path / "health-constraints.md"
    profile_dir = tmp_path
    monkeypatch.setattr(setup_mod, "CONSTRAINTS_PATH", constraints)
    monkeypatch.setattr(setup_mod, "PROFILE_DIR", profile_dir)
    return {"constraints": constraints, "dir": profile_dir}


class TestAvoidLineRewrite:
    def test_only_the_avoid_line_changes(self, paths):
        paths["constraints"].write_text(ORIGINAL, encoding="utf-8")
        changed, msg = setup_mod._update_avoid_line(["青椒", "茄子", "海带", "木耳"])
        after = paths["constraints"].read_text(encoding="utf-8")

        assert changed
        assert "不吃：青椒、茄子、海带、木耳。无过敏。" in after
        # 其余每一行都必须原样保留 —— 包括那条格式契约的说明
        for line in ORIGINAL.splitlines():
            if line.startswith("不吃"):
                continue
            assert line in after, f"这一行被动了：{line!r}"

    def test_refuses_to_guess_when_the_line_is_missing(self, paths):
        text = "# 约束\n\n## 忌口\n\n- 青椒\n- 茄子\n"
        paths["constraints"].write_text(text, encoding="utf-8")
        changed, msg = setup_mod._update_avoid_line(["海带"])
        assert not changed
        assert paths["constraints"].read_text(encoding="utf-8") == text
        assert "没有改动" in msg and "海带" in msg

    def test_no_write_when_nothing_changed(self, paths):
        paths["constraints"].write_text(ORIGINAL, encoding="utf-8")
        changed, msg = setup_mod._update_avoid_line(["青椒", "茄子"])
        assert not changed and "没有变化" in msg

    def test_creates_a_minimal_file_with_the_format_contract(self, paths):
        changed, _ = setup_mod._update_avoid_line(["木耳"])
        text = paths["constraints"].read_text(encoding="utf-8")
        assert changed
        assert "## 忌口" in text and "不吃：木耳。" in text
        # 新建的文件必须自带格式契约，否则下一个人一改就静默失效
        assert "格式别改" in text

    def test_empty_list_is_representable(self, paths):
        paths["constraints"].write_text(ORIGINAL, encoding="utf-8")
        setup_mod._update_avoid_line([])
        assert "不吃：（无）。无过敏。" in paths["constraints"].read_text(encoding="utf-8")

    def test_the_written_line_round_trips_through_the_dice_parser(self, paths):
        """写进去的格式必须是骰子能读回来的 —— 否则等于悄悄关掉忌口过滤。"""
        from health_assistant import dice

        paths["constraints"].write_text(ORIGINAL, encoding="utf-8")
        items = ["青椒", "茄子", "海带", "木耳"]
        setup_mod._update_avoid_line(items)
        text = paths["constraints"].read_text(encoding="utf-8")
        start, end = dice._avoid_section(text)
        assert dice._parse_clause(text[start:end], "不吃") == items


class TestConflictDetection:
    def test_flags_a_height_mismatch(self, paths):
        (paths["dir"] / "personal-context.md").write_text(
            "## 1. 基本信息\n\n- 男，33 岁，身高 **179 cm**（早期自述）\n", encoding="utf-8")
        out = setup_mod._conflicts({"height_cm": 178})
        assert len(out) == 1 and "179" in out[0] and "178" in out[0]

    def test_silent_when_they_agree(self, paths):
        (paths["dir"] / "personal-context.md").write_text(
            "- 男，33 岁，身高 **178 cm**（体检实测）\n", encoding="utf-8")
        assert setup_mod._conflicts({"height_cm": 178}) == []

    def test_silent_when_there_is_nothing_to_compare(self, paths):
        assert setup_mod._conflicts({"height_cm": 178}) == []
        (paths["dir"] / "personal-context.md").write_text("没有身高", encoding="utf-8")
        assert setup_mod._conflicts({"height_cm": 178}) == []
        assert setup_mod._conflicts({}) == []


class TestDataMap:
    def test_covers_every_thing_a_user_must_provide(self):
        """这张表是 doctor 和 README 共用的清单，漏一项就等于没人告诉用户。"""
        labels = " ".join(x[0] for x in setup_mod.DATA_MAP)
        for must in ("身高", "阶段", "忌口", "医学禁忌", "体重", "体检报告", "步数"):
            assert must in labels, f"清单里漏了「{must}」"

    def test_every_entry_is_fully_specified(self):
        for label, where, how, why in setup_mod.DATA_MAP:
            assert label and where and how and why, label

    def test_paths_point_at_real_locations(self):
        """写错路径的清单比没有清单更浪费时间。"""
        from health_assistant.config import ROOT
        for _label, where, _how, _why in setup_mod.DATA_MAP:
            base = where.split("→")[0].split("（")[0].strip()
            top = base.split("/")[0]
            assert (ROOT / top).exists(), f"{where} 指向不存在的 {top}"


# ── 评审修复的回归测试 ──────────────────────────────────────────────────


WITH_ALLERGY = """# 个人健康约束

## 历史

不吃：香菜。（2025 年的旧记录，已作废，按约定只降格不删除）

## 忌口

不吃：青椒、茄子。过敏：虾（2026-09 确诊）。

## 饮酒

每月最多一次。
"""


class TestAllergyIsPreserved:
    """缺陷：整行替换 + 硬编码「无过敏。」会抹掉助手记下的过敏并断言相反。"""

    def test_editing_avoid_does_not_touch_the_allergy_clause(self, paths):
        paths["constraints"].write_text(WITH_ALLERGY, encoding="utf-8")
        changed, _ = setup_mod._update_avoid_line(["青椒", "茄子", "木耳"],
                                                  ["虾（2026-09 确诊）"])
        after = paths["constraints"].read_text(encoding="utf-8")
        assert changed
        assert "过敏：虾（2026-09 确诊）。" in after, "过敏被抹掉了"
        assert "不吃：青椒、茄子、木耳。" in after

    def test_never_asserts_no_allergy_on_its_own(self, paths):
        """用户没提过敏时，脚本不许替他断言「无过敏」。"""
        paths["constraints"].write_text(WITH_ALLERGY, encoding="utf-8")
        setup_mod._update_avoid_line(["青椒"], ["虾（2026-09 确诊）"])
        assert "无过敏" not in paths["constraints"].read_text(encoding="utf-8")

    def test_an_allergy_can_be_added_where_there_was_no_clause(self, paths):
        paths["constraints"].write_text(ORIGINAL, encoding="utf-8")
        setup_mod._update_avoid_line(["青椒", "茄子"], ["花生"])
        assert "过敏：花生。" in paths["constraints"].read_text(encoding="utf-8")

    def test_round_trips_through_the_dice_parser(self, paths):
        from health_assistant import dice
        paths["constraints"].write_text(WITH_ALLERGY, encoding="utf-8")
        setup_mod._update_avoid_line(["青椒", "木耳"], ["虾", "花生"])
        text = paths["constraints"].read_text(encoding="utf-8")
        start, end = dice._avoid_section(text)
        section = text[start:end]
        assert dice._parse_clause(section, "不吃") == ["青椒", "木耳"]
        assert dice._parse_clause(section, "过敏") == ["虾", "花生"]


class TestOnlyTheAvoidSectionIsTouched:
    """缺陷：全文正则找第一行「不吃：」，会改到别的小节里的历史行。"""

    def test_a_history_line_in_another_section_is_left_alone(self, paths):
        paths["constraints"].write_text(WITH_ALLERGY, encoding="utf-8")
        setup_mod._update_avoid_line(["木耳"], ["虾（2026-09 确诊）"])
        after = paths["constraints"].read_text(encoding="utf-8")

        assert "不吃：香菜。（2025 年的旧记录，已作废，按约定只降格不删除）" in after, \
            "改到「## 历史」小节里的行了"
        assert "## 忌口\n\n不吃：木耳。" in after

    def test_setup_and_dice_agree_on_where_the_section_is(self, paths):
        """两边必须用同一个定位逻辑，否则一个写、另一个读不到。"""
        from health_assistant import dice
        assert setup_mod.dice._avoid_section is dice._avoid_section

    def test_refuses_when_there_is_no_section_at_all(self, paths):
        text = "# 约束\n\n## 用药\n\n无。\n"
        paths["constraints"].write_text(text, encoding="utf-8")
        changed, msg = setup_mod._update_avoid_line(["木耳"])
        assert not changed
        assert paths["constraints"].read_text(encoding="utf-8") == text
        assert "没找到「## 忌口」小节" in msg
