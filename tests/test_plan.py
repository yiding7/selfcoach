"""训练计划配置的回归测试。

这个模块的价值全在**一致性**上，所以测试盯的是两件事：

1. 「一周」只有一个切法。周报、骰子、热力图必须用同一个 `week_start_of`，
   否则用户会看到两个对不上的「本周」，然后再也不信任何一个。
2. 建议永远只是默认值。`suggest()` 必须给出理由，且不能有任何路径让它
   反过来否定用户填的值。
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from health_assistant import plan as plan_mod


@pytest.fixture()
def profile(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    monkeypatch.setattr(plan_mod, "PROFILE_PATH", path)

    def write(training: dict | None):
        payload = {} if training is None else {"training": training}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return plan_mod.current()

    return write


# 2026-08-10 是周一，2026-08-09 是周日 —— 跨周边界的两天，故意挑的
MON = dt.date(2026, 8, 10)
SUN = dt.date(2026, 8, 9)


class TestWeekStart:
    def test_monday_start(self, profile):
        p = profile({"week_start": "周一"})
        assert plan_mod.week_start_of(MON, plan=p) == MON
        assert plan_mod.week_start_of(SUN, plan=p) == dt.date(2026, 8, 3)

    def test_sunday_start(self, profile):
        p = profile({"week_start": "周日"})
        assert plan_mod.week_start_of(SUN, plan=p) == SUN
        assert plan_mod.week_start_of(MON, plan=p) == SUN

    def test_bounds_span_exactly_seven_days(self, profile):
        for ws in ("周一", "周日"):
            p = profile({"week_start": ws})
            start, end = plan_mod.week_bounds(MON, plan=p)
            assert (end - start).days == 6
            assert start <= MON <= end

    def test_every_day_maps_into_its_own_week(self, profile):
        """任何一天都必须落在包含它自己的那个七天桶里 —— 两种口径都是。"""
        for ws in ("周一", "周日"):
            p = profile({"week_start": ws})
            for offset in range(60):
                d = MON + dt.timedelta(days=offset)
                start = plan_mod.week_start_of(d, plan=p)
                assert 0 <= (d - start).days <= 6

    def test_english_aliases_accepted(self):
        assert plan_mod.normalize_week_start("Sunday") == "周日"
        assert plan_mod.normalize_week_start(" MON ") == "周一"
        assert plan_mod.normalize_week_start("周三") is None

    def test_garbage_falls_back_to_monday(self, profile):
        """配置坏掉不能让工具不能用 —— 退回默认值，不抛异常。"""
        assert profile({"week_start": "星期八"}).week_start == "周一"
        assert profile(None).week_start == "周一"
        assert profile({}).week_start == "周一"


class TestWeekLabel:
    def test_monday_start_matches_isocalendar(self, profile):
        p = profile({"week_start": "周一"})
        start = plan_mod.week_start_of(MON, plan=p)
        assert plan_mod.week_label_anchor(start, plan=p) == start

    def test_sunday_start_labels_by_the_monday_inside(self, profile):
        """周日起算时，那个周日和它后面的周一必须落进同一个周编号。

        直接拿周日去问 isocalendar() 会把它算进上一周，编号差 1 ——
        周报的文件名就会出现两个 W32。
        """
        p = profile({"week_start": "周日"})
        start = plan_mod.week_start_of(SUN, plan=p)     # = 2026-08-09 周日
        assert start == SUN
        anchor = plan_mod.week_label_anchor(start, plan=p)
        assert anchor == MON                            # 这一周里的周一
        assert anchor.isocalendar()[1] == MON.isocalendar()[1]


class TestWeekdayLabels:
    def test_rotates_with_week_start(self, profile):
        assert plan_mod.weekday_labels(plan=profile({"week_start": "周一"})) == \
            ["一", "二", "三", "四", "五", "六", "日"]
        assert plan_mod.weekday_labels(plan=profile({"week_start": "周日"})) == \
            ["日", "一", "二", "三", "四", "五", "六"]

    def test_row_index_matches_label_order(self, profile):
        """热力图的行号算法必须和行标顺序一致，否则图上标错行。"""
        for ws in ("周一", "周日"):
            p = profile({"week_start": ws})
            labels = plan_mod.weekday_labels(plan=p)
            for offset in range(7):
                d = MON + dt.timedelta(days=offset)
                row = (d.weekday() - p.week_start_index) % 7
                assert labels[row] == ["一", "二", "三", "四", "五", "六", "日"][d.weekday()]


class TestSessionBudget:
    def test_cap_derives_from_minutes(self, profile):
        assert profile({"session_minutes": 50}).session_set_cap == 25
        assert profile({"session_minutes": 90}).session_set_cap == 45

    def test_no_minutes_means_no_cap(self, profile):
        """没填时长就不猜上限 —— 空值不能变成一个凭空冒出来的数字。"""
        assert profile({}).session_set_cap is None
        assert profile({"session_minutes": 0}).session_set_cap is None
        assert profile({"session_minutes": "唔"}).session_set_cap is None

    def test_bad_values_do_not_crash(self, profile):
        p = profile({"days_per_week": -3, "session_minutes": None, "focus": "随便"})
        assert p.days_per_week is None
        assert p.focus == plan_mod.DEFAULT_FOCUS


class TestSuggest:
    def test_always_explains_itself(self):
        """一个不说为什么的默认值等于没有默认值。"""
        for phase in ("减脂", "维持", "增肌"):
            rec, why = plan_mod.suggest(phase=phase)
            assert why, f"{phase} 没给理由"
            assert rec["days_per_week"] > 0
            assert rec["focus"] in plan_mod.FOCUS_CHOICES

    def test_cut_phase_is_not_cardio_first(self):
        """减脂期力量训练的任务是保住肌肉，不该被降级成配角。"""
        rec, _ = plan_mod.suggest(phase="减脂")
        assert rec["focus"] != "有氧为主"

    def test_bulk_suggests_more_frequency_than_cut(self):
        assert (plan_mod.suggest(phase="增肌")[0]["days_per_week"]
                > plan_mod.suggest(phase="减脂")[0]["days_per_week"])

    def test_actual_frequency_is_reference_not_override(self):
        """实际频率只进理由行，不改建议值 —— 它是参考，不是结论。"""
        base, _ = plan_mod.suggest(phase="减脂")
        with_actual, why = plan_mod.suggest(phase="减脂", current_days=(1.0, 8.0))
        assert with_actual == base
        assert any("1.0" in line for line in why)

    def test_short_history_is_labelled_as_unstable(self):
        """只有两周记录时那个比例参考价值很低，不能看起来和八周的一样权威。"""
        _, why = plan_mod.suggest(phase="减脂", current_days=(1.5, 2.0))
        assert any("还不够稳" in line for line in why)
        _, why8 = plan_mod.suggest(phase="减脂", current_days=(1.5, 8.0))
        assert not any("还不够稳" in line for line in why8)


class TestFrequencyDrift:
    """设定值是排计划用的参考，不是考核指标 —— 提醒要够克制。"""

    PLAN = plan_mod.TrainingPlan(days_per_week=4, session_minutes=45)

    def test_quiet_when_history_is_too_short(self):
        """只同步了 3 周、每周练满 4 次的人，绝不该被提醒「你练少了」。

        这条锁的是一个真实缺陷：分母一度写死成 8 周，12 天 / 8 = 1.5 次/周，
        于是刚开始用这个工具、又完全达标的人第一次就被数落。
        """
        assert plan_mod.frequency_drift((4.0, 3.0), plan=self.PLAN) is None

    def test_quiet_when_on_target(self):
        assert plan_mod.frequency_drift((4.0, 8.0), plan=self.PLAN) is None
        assert plan_mod.frequency_drift((3.0, 8.0), plan=self.PLAN) is None

    def test_speaks_up_after_enough_weeks_well_below_target(self):
        msg = plan_mod.frequency_drift((1.5, 8.0), plan=self.PLAN)
        assert msg and "要不要把设定值改成 2 次" in msg

    def test_asks_rather_than_scolds(self):
        """一个长期够不着的目标，问题多半在目标不在人。"""
        msg = plan_mod.frequency_drift((1.5, 8.0), plan=self.PLAN)
        assert "不改也没关系" in msg

    def test_no_plan_no_message(self):
        assert plan_mod.frequency_drift((1.0, 8.0),
                                        plan=plan_mod.TrainingPlan()) is None
        assert plan_mod.frequency_drift(None, plan=self.PLAN) is None
