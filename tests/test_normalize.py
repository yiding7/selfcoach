"""归一化层的回归测试。

所有断言都来自对真实账号的实测响应，不是构造的假数据。
"""

from __future__ import annotations

import pytest

from health_assistant.xunji.normalize import (normalize_movement, normalize_set,
                                              normalize_train, num, parse_note, to_kg)


class TestNum:
    """训记把所有数值存成字符串。区分「没记录」和「记录为 0」是承重的。"""

    @pytest.mark.parametrize("raw,expected", [
        ("7.5", 7.5), ("10", 10.0), (" 12 ", 12.0),
        (7.5, 7.5), (10, 10.0),
        ("", None), ("   ", None), (None, None), ("abc", None), ([], None),
        ("0", 0.0),          # 显式的 0 要保留
        (True, None),        # bool 是 int 的子类，必须挡掉
    ])
    def test_num(self, raw, expected):
        assert num(raw) == expected

    def test_empty_is_not_zero(self):
        """空字符串绝不能变成 0 —— 否则 RPE 统计和容量统计会同时失真。"""
        assert num("") is None
        assert num("") != 0


class TestUnits:
    def test_kg_passthrough(self):
        assert to_kg(60.0, "kg") == 60.0

    def test_lb_converts(self):
        assert to_kg(100.0, "lb") == pytest.approx(45.359, abs=0.01)

    def test_none_stays_none(self):
        assert to_kg(None, "kg") is None


class TestParseNote:
    """实测 note 是 "calorie:228" 这种字符串 —— 是训记塞的元数据，不是用户备注。"""

    def test_calorie_string(self):
        text, kcal, _ = parse_note("calorie:228")
        assert kcal == 228.0
        assert text == "", "热量哨兵不能作为备注正文展示给用户"

    def test_empty_calorie_sentinel(self):
        text, kcal, _ = parse_note("calorie:")
        assert kcal is None
        assert text == ""

    def test_real_note_preserved(self):
        text, kcal, _ = parse_note("今天状态不错 calorie:300")
        assert kcal == 300.0
        assert text == "今天状态不错"

    def test_dict_note(self):
        text, kcal, meta = parse_note({"text": "腰有点酸", "trainColor": "#FF7A00"})
        assert text == "腰有点酸"
        assert meta.get("trainColor") == "#FF7A00"

    def test_json_string_note(self):
        text, _, meta = parse_note('{"text":"嵌套","trainColor":"#000"}')
        assert text == "嵌套"
        assert meta.get("trainColor") == "#000"

    def test_none(self):
        assert parse_note(None) == ("", None, {})


class TestSets:
    def test_empty_rpe_is_none(self):
        """RPE 空字符串 → None。写成 0 会被服务端拒绝，也会污染强度分析。"""
        s = normalize_set({"weight": "60", "reps": "10", "rpe": "", "done": True})
        assert s["rpe"] is None

    def test_string_numerics(self):
        s = normalize_set({"weight": "7.5", "reps": "10", "done": True})
        assert s["weight_kg"] == 7.5
        assert s["reps"] == 10.0

    def test_unilateral_left_weight(self):
        s = normalize_set({"weight": "7.5", "leftWeight": "7.5", "reps": "10", "done": True})
        assert s["weight_kg"] == 7.5
        assert s["left_weight_kg"] == 7.5

    def test_undone_set_kept(self):
        """未完成组必须保留 —— 厂商文档明确警告不要擅自删除。"""
        s = normalize_set({"weight": "60", "reps": "8", "done": False})
        assert s["done"] is False
        assert s["weight_kg"] == 60.0


class TestMovement:
    def test_type_carries_muscle_group(self):
        """movements[].type 是肌群中文名 —— 厂商文档没写，但真实响应里有。"""
        m = normalize_movement({"name": "哑铃弯举", "type": "二头", "sets": []})
        assert m["raw_type"] == "二头"

    def test_empty_type_becomes_none(self):
        """实测约 60% 的动作 type 为空，这是兜底分类器存在的理由。"""
        m = normalize_movement({"name": "杠铃划船", "type": "", "sets": []})
        assert m["raw_type"] is None

    def test_exetype_preserved(self):
        for raw, expected in [("", None), ("times", "times"), ("plus_weight", "plus_weight")]:
            m = normalize_movement({"name": "仰卧抬腿", "exetype": raw, "sets": []})
            assert m["exetype"] == expected

    def test_single_side(self):
        m = normalize_movement({"name": "哑铃弯举", "singleSide": True, "sets": []})
        assert m["unilateral"] is True
        # 字段缺失时不能报错，默认为双侧
        assert normalize_movement({"name": "杠铃卧推", "sets": []})["unilateral"] is False


class TestTrain:
    """基于 2026-07-06 的真实响应。"""

    RAW = {
        "localid": 1783347801054,
        "datestr": "2026-07-06",
        "title": "",
        "note": "calorie:",
        "start": 1783347884549,
        "end": 1783350266630,
        "movements": [{
            "index": 1, "name": "哑铃弯举", "type": "二头", "exetype": "",
            "singleSide": True,
            "sets": [
                {"index": 1, "done": True, "weight": "7.5", "unit": "kg", "reps": "10",
                 "rpe": "", "leftWeight": "7.5"},
                {"index": 2, "done": True, "weight": "10", "unit": "kg", "reps": "5",
                 "rpe": "", "leftWeight": "10"},
                {"index": 3, "done": True, "weight": "7.5", "unit": "kg", "reps": "12",
                 "rpe": "", "leftWeight": "7.5"},
                {"index": 4, "done": True, "weight": "7.5", "unit": "kg", "reps": "10",
                 "rpe": "", "leftWeight": "7.5"},
            ],
        }],
    }

    def test_identity(self):
        s = normalize_train(self.RAW, datestr="2026-07-06")
        assert s["id"] == "xunji:2026-07-06:1783347801054"
        assert s["date"] == "2026-07-06"
        assert s["source"] == "xunji"

    def test_duration(self):
        s = normalize_train(self.RAW, datestr="2026-07-06")
        assert s["duration_s"] == (1783350266630 - 1783347884549) // 1000
        assert 39 <= s["duration_s"] / 60 <= 40

    def test_calorie_sentinel_not_shown_as_note(self):
        s = normalize_train(self.RAW, datestr="2026-07-06")
        assert s["note"] == ""
        assert s["kcal"] is None

    def test_runaway_timer_rejected(self):
        """忘记停计时器是真实存在的。超过 6 小时的时长不采信。"""
        raw = dict(self.RAW, start=0, end=10 * 3600 * 1000)
        assert normalize_train(raw, datestr="2026-07-06")["duration_s"] is None


class TestTimedSetDuration:
    """训记对计时类动作有两个时长字段。实测 2026-08-06 平板支撑三组：
    前两组 time=40/41 且 trainedSeconds 相同，第三组 time=0 而 trainedSeconds=42。
    159 天原始缓存里共 10 组是「只有 trainedSeconds」，且两者都有值时从不冲突。
    """

    def test_falls_back_to_trained_seconds(self):
        s = normalize_set({"index": 3, "done": False, "time": 0,
                           "trainedSeconds": 42, "unit": "kg"})
        assert s["time_s"] == 42.0

    def test_time_wins_when_both_present(self):
        s = normalize_set({"index": 1, "done": False, "time": 40,
                           "trainedSeconds": 40, "unit": "kg"})
        assert s["time_s"] == 40.0

    def test_no_duration_stays_absent(self):
        s = normalize_set({"index": 1, "done": True, "reps": "10",
                           "weight": "60", "unit": "kg"})
        assert s.get("time_s") is None

    def test_done_flag_is_preserved_verbatim(self):
        """归一化层不改 done 的含义 —— 计时动作算不算完成是 analytics 层的判断。"""
        s = normalize_set({"index": 1, "done": False, "trainedSeconds": 42})
        assert s["done"] is False


class TestMovementDifficulty:
    """动作级难度标签。厂商文档没写，但真实响应里有（159 天里 37 个动作有值）。"""

    def test_difficulty_kept(self):
        m = normalize_movement({"name": "哈克机深蹲", "difficulty": "hard", "sets": []})
        assert m["difficulty"] == "hard"

    def test_empty_difficulty_is_none(self):
        m = normalize_movement({"name": "杠铃深蹲", "difficulty": "", "sets": []})
        assert m["difficulty"] is None
