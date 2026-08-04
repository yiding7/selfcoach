"""多视角对比的测试。

核心诉求：每次训练侧重不同、选材大幅变化时，仍然能给出**准确**的对比；
实在不可比时明确说不可比，而不是硬凑一个误导性结论。
"""

from __future__ import annotations

import pytest

from health_assistant.analytics.metrics import session_stats
from health_assistant.analytics.progress import (at_least, balance_findings,
                                                 group_window, movement_progress,
                                                 pattern_comparisons)
from health_assistant.patterns import family_of, pattern_of


def mk(date, movements, sid=None):
    return {
        "id": sid or f"t:{date}", "date": date, "source": "manual",
        "title": "", "duration_s": 3600,
        "movements": [
            {"name": name, "raw_type": group, "exetype": None, "unilateral": False,
             "sets": [{"done": True, "weight_kg": w, "left_weight_kg": None,
                       "reps": r, "rpe": None, "self_weight": False}
                      for _ in range(n)]}
            for name, group, w, r, n in movements],
    }


def st(session):
    return session_stats(session, bodyweight_kg=80.0)


class TestPatternTaxonomy:
    @pytest.mark.parametrize("name,group,expected", [
        ("宽距高位下拉", "背", "垂直拉"),
        ("反握器械高位下拉", "背", "垂直拉"),
        ("引体向上（辅助）", "背", "垂直拉"),
        ("V-bar下拉", "背", "垂直拉"),
        ("杠铃划船", "背", "水平拉"),
        ("悍马机划船（版本2）", "背", "水平拉"),
        ("拉杆坐姿划船(宽握)", "背", "水平拉"),
        ("上斜杠铃卧推", "胸", "上斜推"),
        ("杠铃卧推", "胸", "水平推"),
        ("蝴蝶机飞鸟", "胸", "胸部孤立"),
        ("下斜悍马机推胸", "胸", "下斜推"),
        ("杠铃深蹲", "腿", "膝主导"),
        ("硬拉", "腿", "髋铰链"),
        ("坐姿腿弯举", "腿", "腘绳孤立"),
        ("坐姿腿屈伸", "腿", "股四孤立"),
        ("侧平举", "肩", "侧束孤立"),
        ("俯身飞鸟", "肩", "后束孤立"),
        ("哑铃推肩", "肩", "垂直推"),
        ("坐姿哑铃侧外旋", "肩", "肩袖"),
        ("直杆绳索下压", "三头", "下压"),
        ("哑铃过头臂屈伸", "三头", "过头伸展"),
        ("碎颅者", "三头", "过头伸展"),
    ])
    def test_pattern(self, name, group, expected):
        assert pattern_of(name, group) == expected

    def test_incline_triceps_not_stolen_by_chest_rule(self):
        """「上斜哑铃三头伸展」含「上斜」，但按肌群分域查表，不会被胸的规则抢走。"""
        assert pattern_of("上斜哑铃三头伸展", "三头") == "过头伸展"
        assert pattern_of("上斜哑铃卧推", "胸") == "上斜推"


class TestFamily:
    def test_merges_version_variants(self):
        assert family_of("悍马机划船（版本2）") == family_of("悍马机划船")
        assert family_of("器械推胸（版本2）") == family_of("器械推胸（版本4）")

    def test_merges_grip_variants(self):
        assert family_of("宽距高位下拉") == family_of("高位下拉")

    @pytest.mark.parametrize("name", [
        "V-bar下拉",              # 剥 -.*$ 会切成 "V"
        "俯卧撑",                 # 剥「俯卧」会切成「撑」
        "引体向上（辅助）",         # 剥任意括号会和标准引体合并，负荷完全不同
        "俯卧哑铃划船",            # 体位不同是真实差异
    ])
    def test_conservative_no_destruction(self, name):
        assert family_of(name) == name

    def test_different_machines_not_merged(self):
        """器械划船和悍马机划船是两台机器，重量刻度不通用，不能合并。"""
        assert family_of("器械划船2") != family_of("悍马机划船")


class TestMovementProgress:
    def test_finds_movement_across_intervening_sessions(self):
        """核心改进：动作隔了几次训练才再做，也要能比。

        原先只在「上一次同部位训练」里找配对，这个动作不在那次里就整个丢了。
        """
        cur = st(mk("2026-07-30", [("宽距高位下拉", "背", 45, 10, 4)]))
        hist = [
            st(mk("2026-07-05", [("宽距高位下拉", "背", 40, 10, 4)])),
            st(mk("2026-07-23", [("悍马机正手下拉", "背", 40, 10, 4)])),  # 中间那次没做
        ]
        mp = movement_progress(cur, hist)[0]
        assert mp.confidence == "exact"
        assert mp.last_date == "2026-07-05"
        assert mp.top_load.pct_change == pytest.approx(12.5)

    def test_variant_fallback(self):
        cur = st(mk("2026-07-30", [("悍马机划船（版本2）", "背", 50, 10, 4)]))
        hist = [st(mk("2026-07-23", [("悍马机划船", "背", 45, 10, 4)]))]
        mp = movement_progress(cur, hist)[0]
        assert mp.confidence == "variant"
        assert mp.matched_name == "悍马机划船"

    def test_variant_never_consumes_movement_present_today(self):
        """本次同时做了 A 和 A（版本2）时，A 不能去跟上次的 A（版本2）比 ——
        那条历史属于 A（版本2）自己，借用会凭空造出一个假的大幅下降。"""
        cur = st(mk("2026-07-30", [
            ("悍马机划船", "背", 40, 8, 3),
            ("悍马机划船（版本2）", "背", 50, 10, 9)]))
        hist = [st(mk("2026-07-23", [("悍马机划船（版本2）", "背", 50, 10, 8)]))]
        by_name = {m.name: m for m in movement_progress(cur, hist)}
        assert by_name["悍马机划船"].confidence == "none", "应当识别为新动作"
        assert by_name["悍马机划船（版本2）"].confidence == "exact"

    def test_brand_new_movement(self):
        cur = st(mk("2026-07-30", [("某个新动作", "背", 40, 10, 3)]))
        mp = movement_progress(cur, [])[0]
        assert mp.is_new and mp.confidence == "none"
        assert mp.top_load.before is None


class TestPatternComparison:
    def test_compares_across_completely_different_movements(self):
        """用户的实际场景：一次全做哑铃划船，下一次全做器械划船。
        动作零重合，但都是水平拉，容量和组数仍然可比。"""
        cur = st(mk("2026-06-28", [
            ("V-bar划船", "背", 40, 10, 3), ("器械划船2", "背", 45, 10, 5)]))
        hist = [st(mk("2026-06-23", [
            ("俯卧哑铃划船", "背", 20, 10, 4), ("哑铃划船", "背", 22, 10, 3)]))]
        pcs = {p.pattern: p for p in pattern_comparisons(cur, hist)}
        hp = pcs["水平拉"]
        assert hp.has_anchor
        assert hp.load_confidence == "pattern"
        assert hp.sets.before == 7 and hp.sets.after == 8

    def test_no_1rm_comparison_without_shared_movement(self):
        """不同动作的峰值负荷没有可比性，必须留空。"""
        cur = st(mk("2026-06-28", [("器械划船2", "背", 45, 10, 5)]))
        hist = [st(mk("2026-06-23", [("哑铃划船", "背", 22, 10, 4)]))]
        hp = {p.pattern: p for p in pattern_comparisons(cur, hist)}["水平拉"]
        assert hp.top_e1rm.before is None and hp.top_e1rm.after is None

    def test_1rm_compared_when_movement_shared(self):
        cur = st(mk("2026-06-28", [("杠铃划船", "背", 50, 10, 4)]))
        hist = [st(mk("2026-06-23", [("杠铃划船", "背", 40, 10, 4)]))]
        hp = {p.pattern: p for p in pattern_comparisons(cur, hist)}["水平拉"]
        assert hp.load_confidence == "exact"
        assert hp.top_e1rm.pct_change == pytest.approx(25.0)

    def test_accessory_group_excluded(self):
        """背日顺手做的几组弯举，不该拿去和专门的手臂日比容量。"""
        cur = st(mk("2026-07-30", [
            ("杠铃划船", "背", 50, 10, 20), ("绳索单臂弯举", "二头", 9, 7, 3)]))
        hist = [st(mk("2026-07-23", [("绳索单臂弯举", "二头", 20, 10, 5)]))]
        groups = {p.group for p in pattern_comparisons(cur, hist)}
        assert "二头" not in groups, "附带部位不做模式级对比"
        assert "背" in groups


class TestBalance:
    def _back_sessions(self, vertical, horizontal):
        out = []
        for i in range(3):
            movements = []
            if vertical:
                movements.append(("宽距高位下拉", "背", 40, 10, vertical))
            if horizontal:
                movements.append(("杠铃划船", "背", 50, 10, horizontal))
            out.append(st(mk(f"2026-07-{10 + i * 5:02d}", movements, sid=f"s{i}")))
        return out

    def test_flags_vertical_pull_deficit(self):
        sessions = self._back_sessions(vertical=1, horizontal=6)
        found = balance_findings(sessions, "2026-07-20")
        back = [b for b in found if b.group == "背"]
        assert back, "水平拉远多于垂直拉时应当提示"
        assert "垂直拉" in back[0].fix

    def test_balanced_training_produces_no_finding(self):
        sessions = self._back_sessions(vertical=4, horizontal=5)
        assert not [b for b in balance_findings(sessions, "2026-07-20") if b.group == "背"]

    def test_silent_when_data_too_thin(self):
        """组数不够时不做任何判断，避免在稀疏数据上给误导结论。"""
        thin = [st(mk("2026-07-20", [("宽距高位下拉", "背", 40, 10, 2)]))]
        assert not [b for b in balance_findings(thin, "2026-07-20") if b.group == "背"]

    def test_missing_rear_delt_flagged(self):
        sessions = [st(mk("2026-07-20", [
            ("哑铃推肩", "肩", 20, 10, 5), ("侧平举", "肩", 10, 12, 5)]))]
        found = [b for b in balance_findings(sessions, "2026-07-20") if b.group == "肩"]
        assert found and "后束" in found[0].subject


class TestGroupWindow:
    def test_rolling_window(self):
        sessions = [
            st(mk("2026-07-01", [("杠铃划船", "背", 40, 10, 4)])),
            st(mk("2026-07-20", [("杠铃划船", "背", 50, 10, 6)])),
        ]
        w = group_window(sessions, "背", "2026-07-25", window_days=14)
        assert w.sessions.after == 1 and w.sets.after == 6
        assert w.pattern_sets.get("水平拉") == 6


class TestConfidenceGate:
    def test_ordering(self):
        assert at_least("exact", "variant")
        assert at_least("variant", "variant")
        assert not at_least("pattern", "variant")
        assert not at_least("none", "group")


class TestCardioClassification:
    def test_exetype_cardio_wins(self):
        """苹果健康记录的动作名可能是 Cycling、爬楼梯甚至占位符，
        exetype=cardio 是最可靠的标记。"""
        from health_assistant.taxonomy import classify_movement
        for name in ("Cycling", "爬楼梯", "AppleHealthWorkout", ""):
            c = classify_movement({"name": name, "exetype": "cardio", "raw_type": None})
            assert c.group == "有氧" and c.source == "exetype"

    def test_apple_placeholder_replaced_by_title(self):
        from health_assistant.xunji.normalize import normalize_movement
        m = normalize_movement({"name": "AppleHealthWorkout", "sets": []},
                               session_title="爬楼梯")
        assert m["name"] == "爬楼梯"

    def test_real_name_not_overwritten(self):
        from health_assistant.xunji.normalize import normalize_movement
        m = normalize_movement({"name": "杠铃卧推", "sets": []}, session_title="胸日")
        assert m["name"] == "杠铃卧推"
