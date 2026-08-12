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
            "## 1. 基本信息\n\n- 男，30 岁，身高 **175 cm**（早期自述）\n", encoding="utf-8")
        out = setup_mod._conflicts({"height_cm": 174})
        assert len(out) == 1 and "175" in out[0] and "174" in out[0]

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


class TestListSeparators:
    """自由输入项的分隔符要宽 —— 用户脑子里想的是「把几样东西列出来」，
    不该让他还要记住这个工具认哪个符号。
    """

    @pytest.mark.parametrize("raw", [
        "青椒 茄子 海带",
        "青椒、茄子、海带",
        "青椒，茄子，海带",
        "青椒,茄子,海带",
        "青椒/茄子/海带",
        "青椒\\茄子\\海带",
        "青椒_茄子_海带",
        "青椒-茄子-海带",
        "青椒;茄子;海带",
        "青椒；茄子；海带",
        "青椒|茄子|海带",
        "青椒 、 茄子,,海带",          # 混用 + 多余空格
        "  青椒 茄子 海带  ",          # 首尾空白
    ])
    def test_all_common_separators_work(self, raw):
        assert setup_mod._split_items(raw) == ["青椒", "茄子", "海带"]

    def test_empty_input_yields_empty_list(self):
        assert setup_mod._split_items("   ") == []


class TestNumberedChoice:
    OPTIONS = [("减脂", "有缺口"), ("维持", "持平"), ("增肌", "盈余")]

    def _pick(self, monkeypatch, typed, default="减脂"):
        answers = iter(typed)
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        return setup_mod._ask_choice("阶段", self.OPTIONS, default)

    def test_number_selects_the_value(self, monkeypatch):
        assert self._pick(monkeypatch, ["2"]) == "维持"

    def test_enter_keeps_the_default(self, monkeypatch):
        assert self._pick(monkeypatch, [""]) == "减脂"

    def test_typing_the_value_still_works(self, monkeypatch):
        """改成编号是为了省事，不是为了禁止打字。"""
        assert self._pick(monkeypatch, ["增肌"]) == "增肌"

    def test_out_of_range_reprompts_instead_of_guessing(self, monkeypatch):
        assert self._pick(monkeypatch, ["9", "0", "3"]) == "增肌"


class TestNumberedMulti:
    FLAGS = ("内脏为主", "浓肉汤为主", "高嘌呤海产", "酒精", "高果糖",
             "油炸", "加工肉", "高盐腌制", "生食", "第十项", "第十一项")

    def _pick(self, monkeypatch, typed, current=()):
        answers = iter(typed)
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        return setup_mod._ask_multi("禁忌", self.FLAGS, list(current))

    def test_picks_by_number_in_the_order_given(self, monkeypatch):
        assert self._pick(monkeypatch, ["3 1 5"]) == ["高嘌呤海产", "内脏为主", "高果糖"]

    def test_two_digit_numbers_need_a_separator(self, monkeypatch):
        """超过 9 项时 `112` 是「1,12」还是「11,2」没法判断 —— 宁可报错也不猜。"""
        assert self._pick(monkeypatch, ["11 2"]) == ["第十一项", "浓肉汤为主"]

    def test_enter_keeps_current(self, monkeypatch):
        assert self._pick(monkeypatch, [""], current=["酒精"]) == ["酒精"]

    def test_clear_empties_it(self, monkeypatch):
        assert self._pick(monkeypatch, ["清空"], current=["酒精"]) == []

    def test_duplicates_collapse(self, monkeypatch):
        assert self._pick(monkeypatch, ["1 1 1"]) == ["内脏为主"]

    def test_garbage_reprompts(self, monkeypatch):
        assert self._pick(monkeypatch, ["99", "内脏", "2"]) == ["浓肉汤为主"]


class TestUnitsAreNotGuessed:
    """单位只支持 cm / kg。猜单位是造假数据最快的一条路。"""

    def _height(self, monkeypatch, typed):
        answers = iter(typed)
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        return setup_mod._ask_height({})

    def test_accepts_a_plain_number(self, monkeypatch):
        assert self._height(monkeypatch, ["179"]) == 179.0

    def test_strips_a_trailing_unit_word(self, monkeypatch):
        assert self._height(monkeypatch, ["179cm"]) == 179.0
        assert self._height(monkeypatch, ["179 厘米"]) == 179.0

    def test_metres_are_rejected_not_converted(self, monkeypatch):
        """`1.75` 不该被悄悄当成 175 —— 那是替用户做决定。"""
        assert self._height(monkeypatch, ["1.75", "175"]) == 175.0

    def test_blank_skips(self, monkeypatch):
        assert self._height(monkeypatch, [""]) is None


class TestWeightDoesNotDoubleCount:
    """`rolling_weight` 是按**记录条数**开窗的（最近 7 条），不是按天。
    同一天两条会让那天在窗口里占两格，7 日趋势实际只覆盖 6 天 ——
    目标热量和自重动作的容量都会跟着偏。
    """

    def _ask(self, monkeypatch, existing, typed=("75.0",)):
        import datetime as dt
        monkeypatch.setattr(setup_mod.store, "load_body", lambda **kw: existing)
        answers = iter(typed)
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        return setup_mod._ask_weight(dt.date(2026, 1, 10))

    def rec(self, date, value, source="xunji"):
        return {"date": date, "value": value, "type": "weight",
                "source": source, "id": f"{source}:weight:{date}"}

    def test_refuses_to_add_a_second_record_for_today(self, monkeypatch):
        out = self._ask(monkeypatch, [self.rec("2026-01-10", 76.0)])
        assert out is None, "今天已经有记录了还要再写一条"

    def test_writes_when_today_has_none(self, monkeypatch):
        out = self._ask(monkeypatch, [self.rec("2026-01-09", 76.0)])
        assert out is not None
        assert out["date"] == "2026-01-10" and out["value"] == 75.0
        assert out["source"] == "self"

    def test_kg_suffix_is_stripped(self, monkeypatch):
        assert self._ask(monkeypatch, [], typed=("75.0kg",))["value"] == 75.0
        assert self._ask(monkeypatch, [], typed=("75.0 公斤",))["value"] == 75.0

    def test_out_of_range_reprompts_instead_of_converting(self, monkeypatch):
        """填了斤（150）或磅会落在合理区间里，工具没法分辨 —— 那是用户的责任。
        这里只锁明显不可能的值：重问，绝不替他换算。
        """
        assert self._ask(monkeypatch, [], typed=("8", "75.0"))["value"] == 75.0
        assert self._ask(monkeypatch, [], typed=("500", "75.0"))["value"] == 75.0


class TestMedicalBlocksAreNotSilentlyDropped:
    """医学禁忌是硬墙。改成编号多选后，非标准标签不能因为「回车保持不变」
    就被悄悄删掉 —— 旧的自由输入路径至少还会打一句「忽略了未知标签」。
    """

    def test_enter_preserves_entries_outside_the_standard_flags(self, monkeypatch):
        from health_assistant import dice
        monkeypatch.setattr("builtins.input", lambda _: "")
        current = ["酒精", "高胆固醇"]          # 第二项不在 dice.FLAGS 里
        assert "高胆固醇" not in dice.FLAGS
        out = setup_mod._ask_multi("禁忌", dice.FLAGS, current)
        assert out == current, "按回车「保持不变」把非标准标签删掉了"

    def test_reselecting_does_drop_them(self, monkeypatch):
        """重新选择时丢弃是预期行为 —— 但调用方必须把这件事说出来。"""
        from health_assistant import dice
        monkeypatch.setattr("builtins.input", lambda _: "1")
        out = setup_mod._ask_multi("禁忌", dice.FLAGS, ["酒精", "高胆固醇"])
        assert out == [dice.FLAGS[0]]


class TestAddressPromptTellsTheTruth:
    """缺陷：⑧ 打「直接回车 = 不要称呼」，而 `_ask_list` 把空输入当「保持不变」。

    两句互相矛盾的指示只隔三行，而唯一有效的做法（填 `-` 或「清空」）埋在
    `_ask_list` 自己的提示语里。已经设过 address 的人照提示回车，值原样保留，
    `hc persona` 和 `scripts/coach` 继续注入那个他不想再听见的名字。
    """

    def _ask(self, monkeypatch, current, typed):
        answers = iter(typed)
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        return setup_mod._ask_address(list(current))

    def test_enter_on_a_fresh_profile_means_no_address(self, monkeypatch, capsys):
        """留空是一个合法答案，不是跳过一道必答题 —— 这句提示要留着。"""
        assert self._ask(monkeypatch, [], [""]) == []
        assert "直接回车 = 不要称呼" in capsys.readouterr().out

    def test_enter_never_deletes_an_existing_address(self, monkeypatch, capsys):
        """回车在整个向导里到处都表示「这项不改」，不能只在这一步变成删除。"""
        assert self._ask(monkeypatch, ["Tim"], [""]) == ["Tim"]
        shown = capsys.readouterr().out
        assert "直接回车 = 不要称呼" not in shown, "提示和实际行为对不上"
        assert "保持现在这几个不变" in shown

    def test_dropping_the_address_is_reachable_and_advertised(self, monkeypatch, capsys):
        """「我不想被叫名字了」这个意图必须真的能表达出来，而且不用去猜。"""
        assert self._ask(monkeypatch, ["Tim"], ["-"]) == []
        assert "填单个 - 或「清空」" in capsys.readouterr().out
        assert self._ask(monkeypatch, ["Tim"], ["清空"]) == []

    def test_typing_new_names_replaces_them(self, monkeypatch):
        assert self._ask(monkeypatch, ["Tim"], ["老王 王先生"]) == ["老王", "王先生"]


class TestBrokenAddressIsNotReportedAsConfirmed:
    """缺陷：判据是「字段在不在」，于是手改坏的 address 被报成「已确认不要称呼」。

    和忌口那一层的纪律一致：解析失败要红着脸报出来，不许打绿勾糊弄。
    """

    def _address_lines(self, tmp_path, monkeypatch, address):
        from health_assistant import persona
        p = tmp_path / "profile.json"
        p.write_text(json.dumps({"address": address}, ensure_ascii=False),
                     encoding="utf-8")
        monkeypatch.setattr(setup_mod, "PROFILE_PATH", p)
        monkeypatch.setattr(persona, "PROFILE_PATH", p)
        return [ln for ln in setup_mod.render_checklist()
                if "称呼" in ln or "address" in ln]

    def test_a_hand_mangled_address_is_reported_as_not_taking_effect(
            self, tmp_path, monkeypatch):
        lines = self._address_lines(
            tmp_path, monkeypatch, {"formal": "王先生", "casual": "老王"})
        text = "\n".join(lines)
        assert "❌" in text and "没有生效" in text
        assert "已确认" not in text, "用户填了两个称呼，工具却说系统已确认他不要称呼"

    def test_it_is_said_exactly_once(self, tmp_path, monkeypatch):
        """同一件事报两遍，用户会以为是两个问题。"""
        lines = self._address_lines(tmp_path, monkeypatch, {"formal": "王先生"})
        assert sum("没有生效" in ln for ln in lines) == 1

    def test_an_explicit_empty_still_counts_as_confirmed(self, tmp_path, monkeypatch):
        """空是答案 —— 不能因为加了这道检查就回去催他填。"""
        text = "\n".join(self._address_lines(tmp_path, monkeypatch, []))
        assert "不要称呼（已确认）" in text and "❌" not in text

    def test_a_normal_address_is_shown_as_is(self, tmp_path, monkeypatch):
        text = "\n".join(self._address_lines(tmp_path, monkeypatch, ["老王"]))
        assert "老王" in text and "❌" not in text


class TestActualFrequencyDenominator:
    """分母必须是**实际有记录的跨度**，不是固定的 8 周。"""

    def _run(self, monkeypatch, dates):
        import datetime as dt
        monkeypatch.setattr(setup_mod.store, "load_sessions",
                            lambda **kw: [{"date": d} for d in dates])
        return setup_mod.actual_days_per_week(today=dt.date(2026, 8, 10))

    def test_three_weeks_of_four_per_week_reads_as_four(self, monkeypatch):
        """缺陷：12 天 / 固定 8 周 = 1.5 次/周 —— 一个完全达标的人被数落。"""
        import datetime as dt
        start = dt.date(2026, 7, 21)
        dates = []
        for w in range(3):
            for d in (0, 1, 3, 5):
                dates.append((start + dt.timedelta(weeks=w, days=d)).isoformat())
        rate, weeks = self._run(monkeypatch, dates)
        assert 3.5 <= rate <= 4.5, f"算出来 {rate} 次/周，实际是 4"
        assert weeks < 4

    def test_returns_none_without_data(self, monkeypatch):
        assert self._run(monkeypatch, []) is None

    def test_a_single_day_does_not_blow_up_the_rate(self, monkeypatch):
        """只有一天记录时分母至少按一周算，否则会除出一个荒唐的大数。"""
        rate, weeks = self._run(monkeypatch, ["2026-08-10"])
        assert rate <= 1.0 and weeks >= 1.0
