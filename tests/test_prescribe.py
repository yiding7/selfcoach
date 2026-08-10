"""训练处方的回归测试。

重点是：用户自己标的难度必须能压过纯次数规则。
次数说「该加重了」而用户说「困难」时，以用户的主观感受为准 —— 这是 persona 里
「用户的身体感受永远优先于数字」在代码里的落点。
"""

from __future__ import annotations

import pytest

from health_assistant.analytics.metrics import session_stats
from health_assistant.analytics.prescribe import prescribe_group, rep_range

# 区间边界从配置里取，不写死。knowledge/training/training-landmarks.json 改过一次
# （compound 5-8 → 6-10），写死的话这些用例会跟着配置一起漂。
LO, HI = rep_range("杠铃深蹲")


def mk_movement(name: str, difficulty: str | None, reps: float, sets: int = 3):
    return {"name": name, "raw_type": "腿", "exetype": None, "unilateral": False,
            "difficulty": difficulty,
            "sets": [{"done": True, "weight_kg": 60.0, "reps": reps, "rpe": None,
                      "self_weight": False, "left_weight_kg": None}
                     for _ in range(sets)]}


def prescribe(difficulty: str | None, reps: float):
    s = {"id": "x", "date": "2026-08-05", "source": "xunji", "title": "",
         "duration_s": 2700, "movements": [mk_movement("杠铃深蹲", difficulty, reps)]}
    st = session_stats(s, bodyweight_kg=80.0)
    return prescribe_group("腿", st, None).movements[0]


class TestDifficultyOverridesReps:
    """杠铃深蹲走 compound 区间（当前 6-10），increment 2.5kg。"""

    def test_hard_blocks_load_increase_at_top_of_range(self):
        p = prescribe("hard", reps=HI)
        assert p.load_kg == pytest.approx(60.0)
        assert "困难" in p.change

    def test_easy_advances_load_early(self):
        """次数还没到区间上限，但标了简单 —— 不用再磨一轮。"""
        p = prescribe("easy", reps=LO)
        assert p.load_kg == pytest.approx(62.5)
        assert "简单" in p.why

    def test_normal_follows_pure_rep_rule(self):
        assert prescribe("normal", reps=HI).load_kg == pytest.approx(62.5)
        assert prescribe("normal", reps=LO).load_kg == pytest.approx(60.0)

    def test_unlabelled_matches_normal(self):
        """没标难度时行为必须和以前一致，不能因为新字段改变既有结论。"""
        assert prescribe(None, reps=HI).load_kg == pytest.approx(62.5)
        assert prescribe(None, reps=LO).load_kg == pytest.approx(60.0)

    def test_hard_below_range_still_holds(self):
        """标困难且次数也不够 —— 走原本的「先把次数做上去」，不重复报困难。"""
        p = prescribe("hard", reps=LO - 2)
        assert p.load_kg == pytest.approx(60.0)

    def test_machine_row_is_not_compound(self):
        """2026-08-10 的口径变更：器械划船/下拉不再落进 compound 的低次数区间。

        这条是回归锁 —— 旧口径下器械下拉判 5-8，用户按 8-12 练，
        于是每次都判「可以加重」，形成闭不上的环。
        """
        assert rep_range("器械下拉") == (8, 12)
        assert rep_range("悍马机划船（版本2）") == (8, 12)
        assert rep_range("腿举") == rep_range("器械倒蹬")
        # 自由重量复合仍走低次数区间
        assert rep_range("杠铃深蹲") == (6, 10)
        assert rep_range("杠铃卧推") == (6, 10)


class TestBackfillIsNotVolume:
    """2026-08-10 修掉的棘轮：换个器械 → 判「掉队」→ 下次要求补回 3 组，
    而那 3 组还计进 total_sets。于是建议量只增不减。

    修法不是删掉提示（「上次做了这次没做」确实值得提一句），
    是把它标成可选、不计进合计。这几条锁的就是「不计进」。
    """

    def _rx(self, dropped: list[str]):
        from health_assistant.analytics.compare import Delta, GroupComparison

        s = {"id": "x", "date": "2026-08-05", "source": "xunji", "title": "",
             "duration_s": 2700,
             "movements": [mk_movement("杠铃深蹲", "normal", 8),
                           mk_movement("哈克机深蹲", "normal", 8)]}
        blank = Delta(None, None)
        cmp = GroupComparison(
            group="腿", current_date="2026-08-05", anchor_date="2026-07-29",
            anchor_reason="", days_between=7, sets=blank, volume=blank,
            top_load=blank, best_e1rm=blank, dropped=dropped)
        return prescribe_group("腿", session_stats(s, bodyweight_kg=80.0), cmp)

    def test_total_sets_ignores_backfill(self):
        without = self._rx([])
        with_dropped = self._rx(["腿举", "坐姿腿屈伸"])
        assert with_dropped.total_sets == without.total_sets == 6
        assert with_dropped.optional_sets == 6

    def test_backfill_rows_are_flagged_optional(self):
        rx = self._rx(["腿举"])
        backfill = [m for m in rx.movements if m.optional]
        assert [m.name for m in backfill] == ["腿举"]
        assert all(not m.optional for m in rx.movements if m.name != "腿举")

    def test_prescription_never_exceeds_what_was_done(self):
        """核心不变式：处方的组数不会超过上次实做的组数。

        没有这一条，任何「掉队 → 补回」形态的规则都能把建议量推上去。
        """
        for dropped in ([], ["腿举"], ["腿举", "坐姿腿屈伸", "器械后蹬"]):
            assert self._rx(dropped).total_sets <= 6


class TestIntensityNote:
    def test_note_absent_when_difficulty_covers(self):
        s = {"id": "x", "date": "2026-08-05", "source": "xunji", "title": "",
             "duration_s": 2700,
             "movements": [mk_movement("杠铃深蹲", "normal", 8),
                           mk_movement("哈克机深蹲", "hard", 7)]}
        rx = prescribe_group("腿", session_stats(s, bodyweight_kg=80.0), None)
        assert not any("强度标注" in n for n in rx.notes)

    def test_note_present_when_nothing_recorded(self):
        s = {"id": "x", "date": "2026-08-05", "source": "xunji", "title": "",
             "duration_s": 2700, "movements": [mk_movement("杠铃深蹲", None, 8)]}
        rx = prescribe_group("腿", session_stats(s, bodyweight_kg=80.0), None)
        assert any("强度标注" in n for n in rx.notes)


class TestFreeWeightVsMachine:
    """2026-08-10 用户拍板的口径：自由重量复合 6-10，器械/绳索/孤立 8-12。

    第一版只把「划船」从 compound_patterns 里删掉，结果杠铃划船也掉进了
    8-12 —— 和拍板的口径不符。code review 抓出来的。
    """

    @pytest.mark.parametrize("name", ["杠铃划船", "哑铃划船", "杠铃臀冲",
                                      "史密斯臀冲", "杠铃深蹲", "杠铃卧推"])
    def test_free_weight_compounds_get_the_low_range(self, name):
        assert rep_range(name) == (6, 10), f"{name} 应该是自由重量复合"

    @pytest.mark.parametrize("name", ["器械划船2", "悍马机划船", "器械下拉",
                                      "绳索直背坐姿划船", "腿举", "器械倒蹬",
                                      "面拉", "蝴蝶机飞鸟"])
    def test_machine_and_cable_get_the_high_range(self, name):
        assert rep_range(name) == (8, 12), f"{name} 应该按器械/绳索算"

    def test_the_same_movement_under_two_names_agrees(self):
        assert rep_range("腿举") == rep_range("器械倒蹬")

    def test_config_fallback_matches_the_shipped_config(self):
        """`ranges.get('compound', ...)` 的兜底值必须和配置一致，
        否则配置读不到时会静默退回早就废弃的 5-8。
        """
        import json

        from health_assistant.analytics.prescribe import LANDMARKS_PATH
        cfg = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
        assert cfg["rep_ranges"]["compound"] == [6, 10]
