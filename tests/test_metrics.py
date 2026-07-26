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
        """平稳基线上插一个 +1.8kg 的单日尖峰。数字是编的，形状不是。"""
        records = [{"type": "weight", "date": d, "value": v} for d, v in [
            ("2026-01-05", 75.0), ("2026-01-06", 75.0), ("2026-01-07", 75.0),
            ("2026-01-08", 76.8), ("2026-01-09", 75.0), ("2026-01-10", 75.0)]]
        trend = rolling_weight(records, window=7)
        smoothed = dict(trend)["2026-07-16"]
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
