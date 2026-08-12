"""指标计算的回归测试。

数据取自真实训练记录。几个数字是手算核对过的基准，改动算法后必须仍然成立。
"""

from __future__ import annotations

import pytest

from health_assistant.analytics.metrics import (e1rm, movement_stats, rolling_weight,
                                                session_stats, set_load_kg, set_volume_kg)


def mk_set(**kw):
    base = {"done": True, "weight_kg": None, "left_weight_kg": None, "reps": None,
            "rpe": None, "self_weight": False}
    base.update(kw)
    return base


class TestUnilateralVolume:
    """2026-07-06 哑铃弯举，singleSide=true，4 组：
       7.5×10 / 10×5 / 7.5×12 / 7.5×10
       单侧容量 = 75+50+90+75 = 290 kg，左右合计 = 580 kg。
    """

    MOVEMENT = {
        "name": "哑铃弯举", "raw_type": "二头", "exetype": None, "unilateral": True,
        "sets": [
            mk_set(weight_kg=7.5, left_weight_kg=7.5, reps=10),
            mk_set(weight_kg=10.0, left_weight_kg=10.0, reps=5),
            mk_set(weight_kg=7.5, left_weight_kg=7.5, reps=12),
            mk_set(weight_kg=7.5, left_weight_kg=7.5, reps=10),
        ],
    }

    def test_total_volume_is_580(self):
        ms = movement_stats(self.MOVEMENT, bodyweight_kg=80.0)
        assert ms.volume_kg == pytest.approx(580.0)

    def test_uses_real_left_right_not_double(self):
        """左右重量不同时，用真实值相加，而不是右侧 ×2。"""
        m = dict(self.MOVEMENT,
                 sets=[mk_set(weight_kg=10.0, left_weight_kg=8.0, reps=10)])
        ms = movement_stats(m, bodyweight_kg=80.0)
        assert ms.volume_kg == pytest.approx(180.0)   # (10+8)*10，不是 200

    def test_e1rm_uses_single_side(self):
        """单侧动作的 1RM 必须按单侧重量算，否则会虚高一倍。"""
        ms = movement_stats(self.MOVEMENT, bodyweight_kg=80.0)
        # 顶组是 10kg × 5 次 → Epley 10 × (1+5/30) = 11.67
        assert ms.best_e1rm == pytest.approx(11.67, abs=0.05)

    def test_imbalance_detected(self):
        m = dict(self.MOVEMENT,
                 sets=[mk_set(weight_kg=10.0, left_weight_kg=8.0, reps=10)])
        ms = movement_stats(m, bodyweight_kg=80.0)
        assert ms.imbalance_pct == pytest.approx(20.0)


class TestAssisted:
    """exetype='help' 是辅助器械：记录的重量是助力配重，越大越省力。

    2026-07-05 引体向上（辅助），助力 53kg，体重 80.3kg。
    真实负荷 = 80.3 × 1.0 − 53 = 27.3 kg。
    """

    MOVEMENT = {
        "name": "引体向上（辅助）", "raw_type": "背", "exetype": "help",
        "unilateral": False,
        "sets": [mk_set(weight_kg=53.0, reps=8)],
    }

    def test_assist_is_subtracted_not_added(self):
        load = set_load_kg(self.MOVEMENT["sets"][0], self.MOVEMENT, 80.3)
        assert load == pytest.approx(27.3, abs=0.1)

    def test_more_assist_means_less_load(self):
        """方向性测试：助力调大，等效负荷必须变小。"""
        light = set_load_kg(mk_set(weight_kg=30.0, reps=8), self.MOVEMENT, 80.3)
        heavy = set_load_kg(mk_set(weight_kg=60.0, reps=8), self.MOVEMENT, 80.3)
        assert light > heavy, "助力越大应当越省力"

    def test_never_negative(self):
        load = set_load_kg(mk_set(weight_kg=200.0, reps=8), self.MOVEMENT, 80.3)
        assert load == 0.0

    def test_no_bodyweight_means_no_number(self):
        assert set_load_kg(self.MOVEMENT["sets"][0], self.MOVEMENT, None) is None


class TestBodyweight:
    def test_plus_weight_uses_factor(self):
        """俯卧撑 exetype=plus_weight，weight 为空 → 按体重系数折算，不能算成 0。"""
        m = {"name": "俯卧撑", "exetype": "plus_weight", "unilateral": False,
             "sets": [mk_set(reps=8)]}
        load = set_load_kg(m["sets"][0], m, 80.0)
        assert load == pytest.approx(80.0 * 0.64)

    def test_added_weight_is_added(self):
        m = {"name": "俯卧撑", "exetype": "plus_weight", "unilateral": False,
             "sets": [mk_set(reps=8, weight_kg=10.0)]}
        assert set_load_kg(m["sets"][0], m, 80.0) == pytest.approx(80.0 * 0.64 + 10.0)

    def test_missing_bodyweight_yields_none_not_zero(self):
        """没有体重数据时容量是「算不出」，不是 0。报告会如实说明。"""
        m = {"name": "仰卧抬腿", "exetype": "times", "unilateral": False,
             "sets": [mk_set(reps=15)]}
        ms = movement_stats(m, bodyweight_kg=None)
        assert ms.volume_kg is None
        assert ms.volume_incomplete is True


class TestSets:
    def test_undone_sets_excluded_from_volume(self):
        m = {"name": "杠铃卧推", "exetype": None, "unilateral": False,
             "sets": [mk_set(weight_kg=60.0, reps=10),
                      mk_set(weight_kg=60.0, reps=10, done=False)]}
        ms = movement_stats(m, bodyweight_kg=80.0)
        assert ms.sets_done == 1
        assert ms.sets_planned == 2
        assert ms.volume_kg == pytest.approx(600.0)


class TestE1RM:
    def test_epley(self):
        v, method = e1rm(100.0, 5)
        assert v == pytest.approx(100 * (1 + 5 / 30))
        assert method == "Epley"

    def test_single_rep_is_measured(self):
        v, method = e1rm(100.0, 1)
        assert v == 100.0 and method == "实测"

    def test_high_reps_flagged_low_confidence(self):
        _, method = e1rm(60.0, 20)
        assert "低置信度" in method

    def test_none_inputs(self):
        assert e1rm(None, 5) == (None, "n/a")
        assert e1rm(100.0, None) == (None, "n/a")
        assert e1rm(100.0, 0) == (None, "n/a")


class TestRollingWeight:
    def test_damps_noise(self):
        """平稳的基线上插一个 +1.8kg 的单日尖峰 —— 这是体重日间噪声的真实量级，
        移动均线必须把它压回 0.6kg 以内，否则叙述会把一次水分波动读成长胖。
        数字是编的，形状不是：一天涨完、一天退回。
        """
        records = [{"type": "weight", "date": d, "value": v} for d, v in [
            ("2026-01-05", 75.0), ("2026-01-06", 75.0), ("2026-01-07", 75.0),
            ("2026-01-08", 76.8), ("2026-01-09", 75.0), ("2026-01-10", 75.0)]]
        trend = rolling_weight(records, window=7)
        smoothed = dict(trend)["2026-01-08"]
        assert 75.0 < smoothed < 75.6, "离群点应当被显著压制"

    def test_empty(self):
        assert rolling_weight([]) == []


class TestSessionStats:
    def test_stretching_excluded_from_groups(self):
        s = {"id": "x", "date": "2026-07-01", "source": "manual", "title": "",
             "duration_s": 3600, "movements": [
                 {"name": "杠铃卧推", "raw_type": "胸", "exetype": None,
                  "unilateral": False, "sets": [mk_set(weight_kg=60.0, reps=10)]},
                 {"name": "脊柱拉伸", "raw_type": None, "exetype": None,
                  "unilateral": False, "sets": [mk_set(reps=1)]}]}
        st = session_stats(s, bodyweight_kg=80.0)
        assert "拉伸" not in st.groups
        assert st.groups.get("胸") == 1

    def test_rpe_coverage(self):
        s = {"id": "x", "date": "2026-07-01", "source": "manual", "title": "",
             "duration_s": 3600, "movements": [
                 {"name": "杠铃卧推", "raw_type": "胸", "exetype": None,
                  "unilateral": False, "sets": [
                      mk_set(weight_kg=60.0, reps=10, rpe=8.0),
                      mk_set(weight_kg=60.0, reps=10)]}]}
        st = session_stats(s, bodyweight_kg=80.0)
        assert st.rpe_coverage == pytest.approx(0.5)


class TestTimedMovements:
    """2026-08-06 平板支撑：训记的 record 类动作**不需要打勾**，done 恒为 false，
    时长有时只落在 trainedSeconds 里（time=0）。真实数据是 40 / 41 / 42 秒。
    只认 done 会把三组全判成没做，秒数也跟着丢光。
    """

    MOVEMENT = {
        "name": "平板支撑", "raw_type": "腹部", "exetype": "record",
        "unilateral": False, "sets": [
            mk_set(done=False, time_s=40.0),
            mk_set(done=False, time_s=41.0),
            mk_set(done=False, time_s=42.0),
        ],
    }

    def test_timed_sets_count_as_done_without_checkmark(self):
        ms = movement_stats(self.MOVEMENT, bodyweight_kg=80.0)
        assert ms.sets_done == 3

    def test_durations_are_kept(self):
        ms = movement_stats(self.MOVEMENT, bodyweight_kg=80.0)
        assert ms.timed is True
        assert ms.best_time_s == pytest.approx(42.0)
        assert ms.time_s_total == pytest.approx(123.0)

    def test_no_duration_still_undone(self):
        """没打勾又没时长的组，仍然算没做 —— 不能凭 exetype 一律放行。"""
        m = dict(self.MOVEMENT, sets=[mk_set(done=False)])
        assert movement_stats(m, bodyweight_kg=80.0).sets_done == 0

    def test_not_flagged_volume_incomplete(self):
        """计时动作没有「次数×负荷」意义上的容量，缺的是单位不是数据。
        标成 incomplete 会让每次练平板支撑都误报一条容量警告。
        """
        ms = movement_stats(self.MOVEMENT, bodyweight_kg=80.0)
        assert ms.volume_incomplete is False

    def test_normal_movement_still_needs_checkmark(self):
        m = {"name": "杠铃卧推", "raw_type": "胸", "exetype": None,
             "unilateral": False, "sets": [mk_set(done=False, weight_kg=60.0, reps=10)]}
        assert movement_stats(m, bodyweight_kg=80.0).sets_done == 0


class TestDifficulty:
    """训记的动作级难度标签（简单/正常/困难）。这是这个 app 里唯一真实可得的
    强度信号 —— rpe 字段实际从不填。刻意不做 RPE 数值映射。
    """

    def mk_session(self, *movements):
        return {"id": "x", "date": "2026-08-05", "source": "xunji", "title": "",
                "duration_s": 2700, "movements": list(movements)}

    def mk_mv(self, name, difficulty=None):
        return {"name": name, "raw_type": "腿", "exetype": None, "unilateral": False,
                "difficulty": difficulty,
                "sets": [mk_set(weight_kg=60.0, reps=8)]}

    def test_label_is_chinese_not_a_number(self):
        ms = movement_stats(self.mk_mv("杠铃深蹲", "hard"), bodyweight_kg=80.0)
        assert ms.difficulty == "困难"
        assert ms.rpes == []          # 绝不伪装成 RPE

    def test_unknown_label_is_none(self):
        ms = movement_stats(self.mk_mv("杠铃深蹲", "brutal"), bodyweight_kg=80.0)
        assert ms.difficulty is None

    def test_coverage_denominator_is_movements_not_sets(self):
        st = session_stats(self.mk_session(
            self.mk_mv("杠铃深蹲", "normal"), self.mk_mv("哈克机深蹲")),
            bodyweight_kg=80.0)
        assert st.difficulty_coverage == pytest.approx(0.5)

    def test_difficulty_alone_satisfies_intensity_gate(self):
        """全员标了难度但一个 RPE 都没有 —— 强度结论应该照常给。"""
        st = session_stats(self.mk_session(
            self.mk_mv("杠铃深蹲", "normal"), self.mk_mv("哈克机深蹲", "hard")),
            bodyweight_kg=80.0)
        assert st.rpe_coverage == 0.0
        assert st.has_intensity_signal is True

    def test_no_signal_at_all_fails_gate(self):
        st = session_stats(self.mk_session(self.mk_mv("杠铃深蹲")), bodyweight_kg=80.0)
        assert st.has_intensity_signal is False
