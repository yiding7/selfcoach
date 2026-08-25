"""计时类动作（平板支撑、静态保持）的成绩在**秒**里，不在次数里。

起因和 `test_since.py` 是同一类缺陷：**不报错，报一个看起来正常的错结论**。

训记里计时类动作（`exetype == "record"`）的 `reps` 恒为 `null`，秒数在
`time_s` 里。四个消费侧各自独立地把它当成了「0 次」：

    hc next      目标 10-20 次，「先在这个重量上做到 10 次以上」
                 —— 对平板支撑毫无意义，而且永远不会翻篇
    hc compare   「总次数 0 → 0 次 =」
                 —— 把一次真实的 35s → 42s（+20%）报成「没变化」
    findings     一条优点/缺点都不产出
                 —— 静默省略：报告看起来正常，只是那个动作凭空消失了
    report       表格里是「— / 0 → 0 次 / —」，看起来像没练

真实数据触发过：2026-03-31 三组 35/35/34 秒 → 2026-08-06 三组 40/41/42 秒。

所以锁的不变式是**跨这四个消费侧**的，不是四个各自的症状：

    一个计时类动作的秒数变化，必须在每一个消费侧都以秒的形式出现；
    任何一处都不许把它退化成 0 次。

再加一条反向锁：计次动作不许被这套逻辑波及。

测试不碰 `data/`：会话和知识库配置都现造在内存/tmp 里。
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from health_assistant.analytics import prescribe
from health_assistant.analytics.compare import compare_group
from health_assistant.analytics.findings import evaluate
from health_assistant.analytics.metrics import session_stats
from health_assistant.analytics.progress import movement_progress

# 「上次 / 这次 / 下次」是正常措辞；要抓的是把秒数说成次数，也就是「10 次」这种。
_REP_COUNT = re.compile(r"\d+\s*次")

TODAY = dt.date.today()
D_PREV = (TODAY - dt.timedelta(days=14)).isoformat()
D_CUR = (TODAY - dt.timedelta(days=1)).isoformat()


def _plank(date: str, seconds: list[int]) -> dict:
    """计时类动作：`done` 恒为 false（app 记下时长就代表做过），reps 为 null。

    这两条都是训记的真实行为，不能为了让测试好写而简化掉 ——
    简化掉就测不到 `set_done` 那一层。
    """
    return {
        "id": f"t-{date}", "date": date, "source": "test", "title": "腹部",
        "movements": [{
            "name": "平板支撑", "exetype": "record", "raw_type": "腹部",
            "sets": [{"done": False, "index": i + 1, "kind": "work",
                      "reps": None, "time_s": float(s), "weight_kg": None}
                     for i, s in enumerate(seconds)],
        }],
    }


def _situp(date: str, reps: list[int], kg: float) -> dict:
    """对照组：普通计次动作，不该被计时逻辑碰到。"""
    return {
        "id": f"r-{date}", "date": date, "source": "test", "title": "腹部",
        "movements": [{
            "name": "负重仰卧起坐", "exetype": "weight", "raw_type": "腹部",
            "sets": [{"done": True, "index": i + 1, "kind": "work",
                      "reps": float(r), "weight_kg": kg}
                     for i, r in enumerate(reps)],
        }],
    }


def _stats(session: dict):
    return session_stats(session, 75.0)


def _findings(prev, cur):
    """跑满 findings 的两条相关路径：肌群对比 + 动作级纵向。

    两条都要跑 —— 缺陷当初就是「两处各自独立地把秒当成 0 次」，
    只测其中一条会让另一条悄悄退化回去。
    """
    return evaluate(cur, [compare_group(cur, [prev], "腹部")],
                    movement_progress=movement_progress(cur, [prev]))


@pytest.fixture()
def improved():
    """35/35/34 秒 → 40/41/42 秒：最长一组 +20%，总时长 +18.3%。"""
    prev = _stats(_plank(D_PREV, [35, 35, 34]))
    cur = _stats(_plank(D_CUR, [40, 41, 42]))
    return prev, cur


# ── 前提：指标层确实把秒数取出来了 ────────────────────────────────────────


class TestMetrics:
    def test_timed_flag_and_seconds(self, improved):
        _, cur = improved
        m = cur.movements[0]
        assert m.timed is True
        assert m.sets_done == 3, "done=false 不该让计时类动作变成「一组没做」"
        assert m.best_time_s == 42
        assert m.time_s_total == 123
        assert m.reps_total == 0, "前提：reps 确实是 0 —— 下游必须自己识别 timed"


# ── 消费侧一：hc next ──────────────────────────────────────────────────


class TestPrescribe:
    def test_target_is_seconds_not_reps(self, improved):
        _, cur = improved
        rx = prescribe.prescribe_group("腹部", cur, None)
        p = next(p for p in rx.movements if p.name == "平板支撑")
        assert p.timed is True
        assert p.rep_target.endswith("s"), "目标必须是秒数区间"
        # 「上次 / 这次 / 下次」是正常措辞，不能整词禁「次」。
        # 要禁的是**带数字的次数**，也就是把秒数说成了几次。
        assert not _REP_COUNT.search(p.why), f"建议里出现了次数：{p.why}"
        assert "秒" in p.change

    def test_does_not_advise_more_reps(self, improved):
        """缺陷本身：曾经输出「先在这个重量上做到 10 次以上」。"""
        _, cur = improved
        rx = prescribe.prescribe_group("腹部", cur, None)
        p = next(p for p in rx.movements if p.name == "平板支撑")
        assert "做到 10 次" not in p.why
        assert p.change != "保持重量，先把次数做上去"

    def test_above_ceiling_switches_to_load(self):
        """到了时长上限就该加难度，而不是无限拉长时间。"""
        cur = _stats(_plank(D_CUR, [90, 95, 92]))
        rx = prescribe.prescribe_group("腹部", cur, None)
        p = next(p for p in rx.movements if p.name == "平板支撑")
        assert "变式" in p.change or "负重" in p.change

    def test_missing_seconds_says_so(self):
        """标成计时类却没记秒数 —— 不许猜，也不许退回计次逻辑。"""
        s = _plank(D_CUR, [])
        s["movements"][0]["sets"] = [
            {"done": True, "index": 1, "kind": "work", "reps": None,
             "time_s": None, "weight_kg": None}]
        rx = prescribe.prescribe_group("腹部", _stats(s), None)
        p = next((p for p in rx.movements if p.name == "平板支撑"), None)
        if p is not None:                      # 没有秒数时也可能整条不产出
            assert not _REP_COUNT.search(p.why)


# ── 消费侧二：hc compare ───────────────────────────────────────────────


class TestCompare:
    def test_delta_carries_seconds(self, improved):
        prev, cur = improved
        c = compare_group(cur, [prev], "腹部")
        md = next(m for m in c.movements if m.name == "平板支撑")
        assert md.timed is True
        assert (md.best_time.before, md.best_time.after) == (35, 42)
        assert (md.time_total.before, md.time_total.after) == (104, 123)

    def test_progress_is_not_reported_as_flat(self, improved):
        """缺陷本身：真实的 +20% 被报成「总次数 0 → 0 次 =」。"""
        prev, cur = improved
        c = compare_group(cur, [prev], "腹部")
        md = next(m for m in c.movements if m.name == "平板支撑")
        assert md.best_time.direction == "up"
        assert md.reps.direction == "flat", "前提：次数确实是平的"
        assert round(md.best_time.pct_change, 1) == 20.0

    def test_movement_progress_carries_seconds(self, improved):
        prev, cur = improved
        mp = next(m for m in movement_progress(cur, [prev]) if m.name == "平板支撑")
        assert mp.timed is True
        assert (mp.best_time.before, mp.best_time.after) == (35, 42)


# ── 消费侧三：findings（优点/缺点） ──────────────────────────────────────


class TestFindings:
    def test_improvement_is_not_silently_dropped(self, improved):
        """一次 +20% 的真实进步，必须至少在一条 finding 里出现。

        这一条是**静默省略**的守卫：其余三处会输出错的东西，
        这里是什么都不输出 —— 而「什么都没说」在报告里读起来像「没什么可说」。
        """
        prev, cur = improved
        fs = _findings(prev, cur)
        hits = [f for f in fs if f.subject == "平板支撑" and f.polarity == "优点"]
        assert hits, "平板支撑 35s→42s 没有产出任何优点"
        assert any("s" in f.text for f in hits)

    def test_no_fabricated_1rm(self, improved):
        """计时类动作没有 1RM，任何一条 finding 都不许提它。"""
        prev, cur = improved
        for f in _findings(prev, cur):
            if f.subject == "平板支撑":
                assert "1RM" not in f.text


# ── 反向锁：计次动作不受波及 ────────────────────────────────────────────


class TestRepBasedUntouched:
    def test_normal_movement_still_uses_reps(self):
        prev = _stats(_situp(D_PREV, [10, 10, 10], 5))
        cur = _stats(_situp(D_CUR, [12, 12, 12], 5))
        c = compare_group(cur, [prev], "腹部")
        md = next(m for m in c.movements if m.name == "负重仰卧起坐")
        assert md.timed is False
        assert md.reps.direction == "up"
        assert md.best_time.before is None and md.best_time.after is None

        rx = prescribe.prescribe_group("腹部", cur, c)
        p = next(p for p in rx.movements if p.name == "负重仰卧起坐")
        assert p.timed is False
        assert not p.rep_target.endswith("s")


# ── 配置守卫 ──────────────────────────────────────────────────────────


class TestConfig:
    def test_time_range_is_configured(self):
        """秒数区间必须来自 knowledge/，不许在代码里写死。

        写死会绕开「数字归脚本、参考值归 knowledge」这条分工 ——
        rep_ranges 就是因为配置和档案长期不一致才出过 2026-08-10 那个 bug。
        """
        import json

        cfg = json.loads(prescribe.LANDMARKS_PATH.read_text(encoding="utf-8"))
        assert "time_ranges" in cfg, "knowledge/training/training-landmarks.json 缺 time_ranges"
        lo, hi = prescribe.time_range("平板支撑")
        assert 0 < lo < hi
        assert prescribe.time_increment_s() > 0
