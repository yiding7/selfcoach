"""负荷口径归一化的回归测试。

这个模块碰的是**别人的训练历史**，所以测试盯的是三件事：

1. 原始文件一个字节都不能改。折算只在读取时发生
2. `ignore` 只关掉对比，**不能顺手把训练量也抹掉** —— 那些组是真的练了
3. 检测不能吵。新手期的正常进步（小重量、长间隔）不该报警，
   否则用户三天就把这个功能关了
"""

from __future__ import annotations

import copy

import pytest

from health_assistant import calibration
from health_assistant.analytics.compare import compare_group
from health_assistant.analytics.metrics import session_stats


@pytest.fixture()
def rules_file(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "PATH", tmp_path / "load-calibration.jsonl")
    return tmp_path / "load-calibration.jsonl"


def sess(date: str, weight: float, reps: float, *, name: str = "面拉",
         sets: int = 3, exetype=None, group: str = "肩") -> dict:
    return {
        "id": f"{date}-{name}", "date": date, "source": "x", "title": "",
        "duration_s": 3000,
        "movements": [{
            "name": name, "raw_type": group, "exetype": exetype,
            "unilateral": False, "difficulty": None,
            "sets": [{"done": True, "weight_kg": weight, "reps": reps, "rpe": None,
                      "self_weight": False, "left_weight_kg": None}
                     for _ in range(sets)],
        }],
    }


def stats_of(sessions):
    return [session_stats(s, 80.0) for s in calibration.apply_rules(sessions)]


class TestRawDataIsUntouched:
    """最重要的一条：原始记录永远不改。"""

    def test_apply_rules_does_not_mutate_input(self, rules_file):
        raw = [sess("2026-07-16", 40, 10)]
        before = copy.deepcopy(raw)
        calibration.add_rule("面拉", "scale", ratio=0.5)
        calibration.apply_rules(raw)
        assert raw == before

    def test_scaling_shows_up_only_in_the_read_path(self, rules_file):
        raw = [sess("2026-07-16", 40, 10)]
        calibration.add_rule("面拉", "scale", ratio=0.5)
        st = stats_of(raw)[0]
        assert st.movements[0].top_load_kg == pytest.approx(20.0)
        assert raw[0]["movements"][0]["sets"][0]["weight_kg"] == 40
        # 折算过的一定留痕 —— 一个被悄悄改过的数字比一个明显错的数字危险
        assert st.movements[0].calib_ratio == 0.5

    def test_rules_file_is_append_only(self, rules_file):
        calibration.add_rule("面拉", "scale", ratio=0.5)
        first = rules_file.read_text(encoding="utf-8")
        calibration.add_rule("面拉", "confirm")
        after = rules_file.read_text(encoding="utf-8")
        assert after.startswith(first)

    def test_supersede_hides_but_keeps_the_old_rule(self, rules_file):
        old = calibration.add_rule("面拉", "scale", ratio=0.5)
        calibration.add_rule("面拉", "scale", ratio=0.25, supersedes=old.id)
        live = calibration.load_rules()
        assert [r.ratio for r in live] == [0.25]
        assert old.id in rules_file.read_text(encoding="utf-8")


class TestScopeAndSafety:
    def test_date_range_is_inclusive_and_bounded(self, rules_file):
        calibration.add_rule("面拉", "scale", ratio=0.5,
                             date_from="2026-07-01", date_to="2026-07-31")
        rule = calibration.load_rules()[0]
        assert rule.covers("面拉", "2026-07-01")
        assert rule.covers("面拉", "2026-07-31")
        assert not rule.covers("面拉", "2026-08-01")
        assert not rule.covers("下压", "2026-07-15")

    @pytest.mark.parametrize("exetype", ["times", "plus_weight", "help", "record"])
    def test_derived_loads_are_never_scaled(self, rules_file, exetype):
        """自重折算、辅助配重（方向相反）、计时类都没有「标称重量」这回事。

        对它们乘系数只会造出一个假数字，所以规则命中了也不动。
        """
        raw = [sess("2026-07-16", 20, 10, name="引体向上（辅助）", exetype=exetype)]
        calibration.add_rule("引体向上（辅助）", "scale", ratio=0.5)
        out = calibration.apply_rules(raw)
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 20

    def test_scale_requires_a_positive_ratio(self, rules_file):
        for bad in (None, 0, -1):
            with pytest.raises(ValueError):
                calibration.add_rule("面拉", "scale", ratio=bad)

    def test_unknown_action_is_rejected(self, rules_file):
        with pytest.raises(ValueError):
            calibration.add_rule("面拉", "删掉")


class TestIgnoreKeepsTheVolume:
    """`ignore` 的语义是「这个负荷数字不能跨两次比」，不是「这次没练」。"""

    def test_excluded_from_pairing_but_counted_in_volume(self, rules_file):
        raw = [sess("2026-07-16", 40, 10), sess("2026-08-10", 18, 10)]
        calibration.add_rule("面拉", "ignore", date_from="2026-08-10",
                             date_to="2026-08-10")
        st = stats_of(raw)
        c = compare_group(st[1], [st[0]], "肩")
        assert c.paired_count == 0
        assert c.excluded == ["面拉"]
        # 组数照常计入 —— 那 3 组是真的练了
        assert c.sets.before == 3.0 and c.sets.after == 3.0
        assert c.volume.after is not None

    def test_exclusion_is_visible_not_silent(self, rules_file):
        """静默排除等于伪造了一个「什么都没发生」的对比。"""
        raw = [sess("2026-07-16", 40, 10), sess("2026-08-10", 18, 10)]
        calibration.add_rule("面拉", "ignore")
        st = stats_of(raw)
        assert compare_group(st[1], [st[0]], "肩").excluded == ["面拉"]


class TestDetection:
    def test_catches_the_pulley_case(self, rules_file):
        """2026-08-10 那次真实的面拉：40kg → 18kg，两次都是 32 次。"""
        raw = [sess("2026-07-16", 40, 32 / 3), sess("2026-08-10", 18, 32 / 3)]
        jumps = calibration.detect_jumps(stats_of(raw))
        assert len(jumps) == 1
        assert jumps[0].movement == "面拉"
        assert jumps[0].pulley_suspect
        assert jumps[0].ratio == pytest.approx(0.5)

    def test_quiet_when_reps_moved_too(self, rules_file):
        """重量减半、次数翻倍是有意换次数区间，不是换尺。"""
        raw = [sess("2026-07-16", 40, 6), sess("2026-08-10", 18, 18)]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_quiet_for_small_absolute_loads(self, rules_file):
        """2.5kg → 10kg 的飞鸟是 ×4，但那只是换了两档哑铃。"""
        raw = [sess("2026-03-18", 2.5, 10, name="俯身飞鸟"),
               sess("2026-03-25", 10, 10, name="俯身飞鸟")]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_quiet_across_a_long_gap(self, rules_file):
        """隔一个多月翻倍是进步，隔十天翻倍才是换尺。"""
        raw = [sess("2026-03-10", 15, 10, name="哑铃直腿硬拉"),
               sess("2026-04-20", 30, 10, name="哑铃直腿硬拉")]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_normal_progression_is_quiet(self, rules_file):
        raw = [sess("2026-06-01", 50, 10), sess("2026-06-08", 55, 10)]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_resolved_jumps_drop_out_of_unresolved(self, rules_file):
        raw = [sess("2026-07-16", 40, 32 / 3), sess("2026-08-10", 18, 32 / 3)]
        st = stats_of(raw)
        assert len(calibration.unresolved(st)) == 1
        calibration.add_rule("面拉", "confirm", date_from="2026-08-10",
                             date_to="2026-08-10")
        assert calibration.unresolved(stats_of(raw)) == []

    def test_confirm_changes_no_number(self, rules_file):
        """「我看过了，这是真的」只该改预警状态，不该碰任何数据。"""
        raw = [sess("2026-08-10", 18, 10)]
        plain = stats_of(raw)[0].movements[0].top_load_kg
        calibration.add_rule("面拉", "confirm")
        assert stats_of(raw)[0].movements[0].top_load_kg == plain


class TestExplanation:
    def test_pulley_case_teaches_how_to_measure(self):
        text = " ".join(calibration.explanation(pulley=True))
        assert "传动比" in text and "÷" in text
        assert "load-measurement.md" in text

    def test_non_pulley_case_omits_the_pulley_lecture(self):
        text = " ".join(calibration.explanation(pulley=False))
        assert "传动比" not in text
        assert "load-measurement.md" in text


class TestExclusionIsSymmetric:
    """`--ignore` 通常只写在其中一天（CLI 建议的就是 `--date <本次>`）。
    两边必须按同一个名字集合过滤，否则锚点那次还留着这个动作。
    """

    def test_ignoring_only_the_current_date_does_not_report_it_as_dropped(self, rules_file):
        """缺陷：只过滤本次 → 锚点仍有该动作 → `_pair` 把它算成「本次没做」。

        后果是输出里同时出现「本次没做：面拉」和「未参与对比：面拉」，
        还会生成一条「建议补回」，让用户去补一个他当天明明做了的动作。
        """
        raw = [sess("2026-07-16", 40, 10), sess("2026-08-10", 18, 10)]
        calibration.add_rule("面拉", "ignore", date_from="2026-08-10",
                             date_to="2026-08-10")
        st = stats_of(raw)
        c = compare_group(st[1], [st[0]], "肩")
        assert c.excluded == ["面拉"]
        assert c.dropped == [], "被排除的动作被误报成「本次没做」了"
        assert c.added == []

    def test_ignoring_only_the_anchor_date_is_also_symmetric(self, rules_file):
        raw = [sess("2026-07-16", 40, 10), sess("2026-08-10", 18, 10)]
        calibration.add_rule("面拉", "ignore", date_from="2026-07-16",
                             date_to="2026-07-16")
        st = stats_of(raw)
        c = compare_group(st[1], [st[0]], "肩")
        assert c.excluded == ["面拉"]
        assert c.added == [] and c.dropped == []

    def test_no_backfill_prescription_for_an_excluded_movement(self, rules_file):
        """「建议补回」是从 comparison.dropped 来的，所以这条一起锁上。"""
        from health_assistant.analytics.prescribe import prescribe_group

        raw = [sess("2026-07-16", 40, 10), sess("2026-08-10", 18, 10)]
        calibration.add_rule("面拉", "ignore", date_from="2026-08-10",
                             date_to="2026-08-10")
        st = stats_of(raw)
        rx = prescribe_group("肩", st[1], compare_group(st[1], [st[0]], "肩"))
        assert not any("补回" in m.change for m in rx.movements)


class TestResolutionDoesNotLeakForward:
    def test_a_rule_on_one_date_does_not_silence_the_next_jump(self, rules_file):
        """缺陷：`covers(后一天) or covers(前一天)` 会让给 D 写的规则
        连带把 (D → 下一次) 那个跳变也标成已处理。

        用户确认了一次真实涨幅，下个月换机位造成的假涨幅就再也不会预警。
        """
        raw = [sess("2026-07-16", 40, 10),
               sess("2026-08-10", 18, 10),
               sess("2026-08-25", 40, 10)]
        calibration.add_rule("面拉", "confirm", date_from="2026-08-10",
                             date_to="2026-08-10")
        st = stats_of(raw)
        pending = calibration.unresolved(st)
        assert [j.date for j in pending] == ["2026-08-25"], \
            "给 08-10 写的规则把 08-25 那个新跳变也吞掉了"

    def test_a_range_rule_covering_both_ends_still_resolves(self, rules_file):
        raw = [sess("2026-07-16", 40, 10), sess("2026-08-10", 18, 10)]
        calibration.add_rule("面拉", "confirm", date_from="2026-07-01",
                             date_to="2026-08-31")
        assert calibration.unresolved(stats_of(raw)) == []


class TestRuleValidation:
    def test_empty_movement_is_refused(self, rules_file):
        """文件只追加 —— 一条永远匹配不上的规则会永久留在那儿。"""
        for bad in (None, "", "   "):
            with pytest.raises(ValueError):
                calibration.add_rule(bad, "confirm")
        assert not rules_file.exists() or rules_file.read_text(encoding="utf-8") == ""

    def test_malformed_dates_are_refused(self, rules_file):
        with pytest.raises(ValueError):
            calibration.add_rule("面拉", "confirm", date_from="2026/07/16")
        with pytest.raises(ValueError):
            calibration.add_rule("面拉", "confirm", date_from="2026-08-31",
                                 date_to="2026-08-01")
