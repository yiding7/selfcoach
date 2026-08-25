"""同肌群对比与结论生成的测试。

最重要的一条：**两次训练没有共同动作时，绝不比较峰值负荷。**
真实数据里 2026-06-30 是哑铃/自重胸日，2026-07-12 是纯器械胸日，
两次零重合。硬比会得出「估算 1RM 下降 19.5%」—— 那其实只是
把俯卧撑折算的 51kg 和器械推胸的 40kg 放在一起比，既错误又打击人。
"""

from __future__ import annotations

import pytest

from health_assistant.analytics.compare import (Delta, compare_group, find_anchor)
from health_assistant.analytics.findings import (check_invariants, evaluate,
                                                 evaluate_group, split)
from health_assistant.analytics.metrics import session_stats


def mk_session(date, movements, *, sid=None, duration_s=3600):
    return {
        "id": sid or f"test:{date}", "date": date, "source": "manual",
        "title": "", "duration_s": duration_s,
        "movements": [
            {"name": name, "raw_type": group, "exetype": None, "unilateral": False,
             "sets": [{"done": True, "weight_kg": w, "left_weight_kg": None,
                       "reps": r, "rpe": None, "self_weight": False}
                      for _ in range(sets)]}
            for name, group, w, r, sets in movements],
    }


def stats(session):
    return session_stats(session, bodyweight_kg=80.0)


class TestDelta:
    def test_pct_change(self):
        d = Delta(100.0, 110.0)
        assert d.pct_change == pytest.approx(10.0)
        assert d.direction == "up"

    def test_none_safe(self):
        d = Delta(None, 50.0)
        assert d.pct_change is None
        assert d.abs_change is None
        assert "新增" in d.fmt()

    def test_zero_before(self):
        assert Delta(0.0, 5.0).pct_change is None


class TestAnchor:
    def test_finds_most_recent_same_group(self):
        cur = stats(mk_session("2026-07-12", [("器械推胸", "胸", 40, 10, 4)]))
        hist = [
            stats(mk_session("2026-06-30", [("哑铃卧推", "胸", 30, 10, 4)])),
            stats(mk_session("2026-07-05", [("杠铃划船", "背", 40, 10, 4)])),
            stats(mk_session("2026-06-20", [("上斜卧推", "胸", 35, 10, 4)])),
        ]
        anchor, reason = find_anchor(cur, hist, "胸")
        assert anchor is not None and anchor.date == "2026-06-30"
        assert "胸" in reason and "2026-06-30" in reason

    def test_ignores_sessions_with_too_few_sets(self):
        cur = stats(mk_session("2026-07-12", [("器械推胸", "胸", 40, 10, 4)]))
        hist = [stats(mk_session("2026-07-01", [("飞鸟", "胸", 15, 12, 1)]))]
        anchor, _ = find_anchor(cur, hist, "胸")
        assert anchor is None, "只练了 1 组不算练过这个部位"

    def test_no_future_sessions(self):
        cur = stats(mk_session("2026-07-01", [("器械推胸", "胸", 40, 10, 4)]))
        hist = [stats(mk_session("2026-07-12", [("哑铃卧推", "胸", 30, 10, 4)]))]
        assert find_anchor(cur, hist, "胸")[0] is None

    def test_reason_is_human_readable(self):
        cur = stats(mk_session("2026-07-12", [("器械推胸", "胸", 40, 10, 4)]))
        hist = [stats(mk_session("2026-06-30", [("哑铃卧推", "胸", 30, 10, 4)]))]
        _, reason = find_anchor(cur, hist, "胸")
        assert "12 天前" in reason, "要让用户看得见工具为什么挑这次来比"


class TestLoadComparability:
    """核心正确性：动作完全不重合时不比负荷。"""

    CURRENT = mk_session("2026-07-12", [
        ("器械推胸（版本2）", "胸", 40, 10, 4),
        ("杠杆式上斜推胸", "胸", 35, 6, 5)])
    ANCHOR = mk_session("2026-06-30", [
        ("俯卧撑", "胸", 51, 8, 3),
        ("哑铃卧推", "胸", 30, 10, 4)])

    def test_no_overlap_suppresses_load_comparison(self):
        cmp = compare_group(stats(self.CURRENT), [stats(self.ANCHOR)], "胸")
        assert cmp.paired_count == 0
        assert cmp.loads_comparable is False
        assert cmp.top_load.before is None and cmp.top_load.after is None
        assert cmp.best_e1rm.before is None and cmp.best_e1rm.after is None

    def test_volume_still_compared(self):
        """容量是可加总的工作量，即使动作换了也仍然有可比性。"""
        cmp = compare_group(stats(self.CURRENT), [stats(self.ANCHOR)], "胸")
        assert cmp.volume.before is not None and cmp.volume.after is not None
        assert cmp.sets.before is not None

    def test_emits_explanation_not_silence(self):
        cmp = compare_group(stats(self.CURRENT), [stats(self.ANCHOR)], "胸")
        codes = {f.code for f in evaluate_group(cmp)}
        assert "MOVEMENT_SET_CHANGED" in codes
        assert "REGRESSED_E1RM" not in codes, "不能拿不可比的负荷得出力量下降的结论"

    def test_shared_movements_enable_comparison(self):
        cur = mk_session("2026-07-05", [("杠铃划船", "背", 50, 8, 4)])
        anc = mk_session("2026-06-28", [("杠铃划船", "背", 40, 8, 4)])
        cmp = compare_group(stats(cur), [stats(anc)], "背")
        assert cmp.paired_count == 1
        assert cmp.loads_comparable is True
        assert cmp.top_load.pct_change == pytest.approx(25.0)


class TestFindingInvariants:
    """两条结构性不变量 —— 「循循善诱」靠这个从形容词变成系统属性。"""

    def _all_findings(self):
        cur = stats(mk_session("2026-07-12", [
            ("器械推胸", "胸", 40, 10, 4), ("绳索夹胸", "胸", 15, 12, 2)]))
        anc = stats(mk_session("2026-06-30", [
            ("器械推胸", "胸", 45, 10, 6), ("哑铃飞鸟", "胸", 12, 12, 4)]))
        cmp = compare_group(cur, [anc], "胸")
        return evaluate(cur, [cmp])

    def test_every_weakness_has_a_linked_action(self):
        findings = self._all_findings()
        problems = check_invariants(findings)
        assert problems == [], f"不变量被破坏: {problems}"

    def test_praise_requires_numbers(self):
        from health_assistant.analytics.findings import Finding
        with pytest.raises(ValueError):
            Finding(code="X", polarity="优点", subject="胸", text="很棒", metrics={})

    def test_weakness_without_action_is_caught(self):
        from health_assistant.analytics.findings import Finding
        bad = [Finding(code="W", polarity="缺点", subject="胸", text="容量掉了 20%",
                       metrics={"pct": -20})]
        assert check_invariants(bad), "没挂改进点的缺点必须被检出"

    def test_split_buckets(self):
        buckets = split(self._all_findings())
        assert set(buckets) == {"优点", "缺点", "改进点", "信息"}


class TestNoAnchor:
    def test_first_time_training_group_is_informational(self):
        cur = stats(mk_session("2026-07-12", [("器械推胸", "胸", 40, 10, 4)]))
        cmp = compare_group(cur, [], "胸")
        assert not cmp.has_anchor
        findings = evaluate_group(cmp)
        assert [f.code for f in findings] == ["NO_ANCHOR"]
        assert all(f.polarity == "信息" for f in findings), "没数据时不该给出评价"


class TestRpeSuppression:
    def test_low_coverage_suppresses_intensity_claims(self):
        """RPE 覆盖率为 0 时，不能出任何依赖 RPE 的结论，而是提示补记录。"""
        cur = stats(mk_session("2026-07-12", [("器械推胸", "胸", 40, 10, 4)]))
        findings = evaluate(cur, [compare_group(cur, [], "胸")])
        assert cur.rpe_coverage == 0.0
        assert "MISSING_RPE" in {f.code for f in findings}
        assert not any(f.code in ("JUNK_VOLUME", "GRINDER") for f in findings)


class TestFuzzyMatchRespectsLoadingConvention:
    """模糊匹配不能跨计量口径配对。

    2026-08-23 真的发生过：「上斜杠铃卧推」和「上斜哑铃卧推」只差一个字，
    difflib 相似度 0.83 越过了 0.82 的门槛，于是 `hc compare` 报出
    「顶组 14.0 → 35.0kg ↑ +150.0%」—— 拿一对 14kg 哑铃比一根 35kg 杠铃。
    """

    @staticmethod
    def _sess(date: str, name: str, weight: float) -> dict:
        return {
            "id": date, "date": date, "source": "x", "title": "", "duration_s": 3000,
            "movements": [{
                "name": name, "raw_type": "胸", "exetype": None,
                "unilateral": False, "difficulty": None,
                "sets": [{"done": True, "weight_kg": weight, "reps": 10, "rpe": None,
                          "self_weight": False, "left_weight_kg": None} for _ in range(3)],
            }],
        }

    def test_dumbbell_and_barbell_never_pair(self):
        from health_assistant.analytics.compare import compare_group
        from health_assistant.analytics.metrics import session_stats
        prev = session_stats(self._sess("2026-08-18", "上斜哑铃卧推", 14), 80.0)
        cur = session_stats(self._sess("2026-08-23", "上斜杠铃卧推", 35), 80.0)

        c = compare_group(cur, [prev], "胸")
        assert [m.name for m in c.movements if m.status == "paired"] == []
        assert c.added == ["上斜杠铃卧推"]
        assert c.dropped == ["上斜哑铃卧推"]

    def test_same_convention_still_pairs_fuzzily(self):
        """门槛只挡口径不同的，同口径的近似名字照配 —— 别把功能一起关掉。"""
        from health_assistant.analytics.compare import compare_group
        from health_assistant.analytics.metrics import session_stats
        prev = session_stats(self._sess("2026-08-18", "器械推胸（版本2）", 40), 80.0)
        cur = session_stats(self._sess("2026-08-23", "器械推胸（版本3）", 45), 80.0)

        c = compare_group(cur, [prev], "胸")
        assert [m.name for m in c.movements if m.status == "paired"] == ["器械推胸（版本3）"]
