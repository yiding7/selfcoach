"""训练处方的回归测试。

重点是：用户自己标的难度必须能压过纯次数规则。
次数说「该加重了」而用户说「困难」时，以用户的主观感受为准 —— 这是 persona 里
「用户的身体感受永远优先于数字」在代码里的落点。
"""

from __future__ import annotations

import pytest

from health_assistant.analytics.metrics import session_stats
from health_assistant.analytics.prescribe import prescribe_group


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
    """杠铃深蹲 rep_range 是 5-8，increment 2.5kg。"""

    def test_hard_blocks_load_increase_at_top_of_range(self):
        p = prescribe("hard", reps=8)
        assert p.load_kg == pytest.approx(60.0)
        assert "困难" in p.change

    def test_easy_advances_load_early(self):
        """次数还没到区间上限，但标了简单 —— 不用再磨一轮。"""
        p = prescribe("easy", reps=6)
        assert p.load_kg == pytest.approx(62.5)
        assert "简单" in p.why

    def test_normal_follows_pure_rep_rule(self):
        assert prescribe("normal", reps=8).load_kg == pytest.approx(62.5)
        assert prescribe("normal", reps=6).load_kg == pytest.approx(60.0)

    def test_unlabelled_matches_normal(self):
        """没标难度时行为必须和以前一致，不能因为新字段改变既有结论。"""
        assert prescribe(None, reps=8).load_kg == pytest.approx(62.5)
        assert prescribe(None, reps=6).load_kg == pytest.approx(60.0)

    def test_hard_below_range_still_holds(self):
        """标困难且次数也不够 —— 走原本的「先把次数做上去」，不重复报困难。"""
        p = prescribe("hard", reps=4)
        assert p.load_kg == pytest.approx(60.0)


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
