"""教练工作日志的回归测试。

这份日志的价值全在「历史不被篡改」和「不可靠的东西不冒充事实」上，
所以测试重点不是格式好不好看，而是四条不变量：

1. **追加即不可变** —— 确认/否决/推翻都不能改动或删除原始行
2. **待确认不受时间窗限制** —— 挂 200 天也要浮出来
3. **推翻链正确** —— 旧条目被标 superseded，但内容原样保留
4. **读操作无副作用** —— 所以它才能安心进权限白名单
"""

from __future__ import annotations

import datetime as dt

import pytest

from health_assistant import journal


@pytest.fixture()
def root(tmp_path):
    return tmp_path / "coach-journal"


D = dt.date(2026, 8, 7)


class TestAppendOnly:
    def test_confirm_does_not_touch_the_original_line(self, root):
        eid = journal.add("待确认", "目标", "用户表示减脂完成，拟转增肌",
                          today=D, root=root)
        before = (root / "2026-08.jsonl").read_text(encoding="utf-8")

        journal.set_status(eid, journal.STATUS_CONFIRMED,
                           landed="profile/personal-context.md#3-目标",
                           today=dt.date(2026, 8, 9), root=root)
        after = (root / "2026-08.jsonl").read_text(encoding="utf-8")

        # 原文完整保留在开头，只是后面多了一行
        assert after.startswith(before)
        assert len(after.splitlines()) == len(before.splitlines()) + 1

    def test_status_is_replayed_not_stored(self, root):
        eid = journal.add("待确认", "目标", "转增肌", today=D, root=root)
        journal.set_status(eid, journal.STATUS_CONFIRMED, landed="x.md#y",
                           today=D, root=root)
        [item] = journal.entries(today=D, root=root)
        assert item["status"] == journal.STATUS_CONFIRMED
        assert item["landed"] == "x.md#y"
        # entry 记录本身没有 status 字段
        raw = [ln for ln in (root / "2026-08.jsonl").read_text(encoding="utf-8").splitlines()]
        assert '"rec":"entry"' in raw[0].replace(" ", "")
        assert "status" not in raw[0]

    def test_confirm_requires_a_landing_place(self, root):
        eid = journal.add("待确认", "目标", "转增肌", today=D, root=root)
        with pytest.raises(ValueError, match="landed"):
            journal.set_status(eid, journal.STATUS_CONFIRMED, today=D, root=root)

    def test_reads_never_write(self, root):
        journal.add("观察", "训练", "腿酸", today=D, root=root)
        before = (root / "2026-08.jsonl").read_bytes()
        journal.render(today=D, root=root)
        journal.render_brief(today=D, root=root)
        journal.search("腿", today=D, root=root)
        journal.select(today=D, root=root)
        journal.since(dt.date(2026, 1, 1), today=D, root=root)
        assert (root / "2026-08.jsonl").read_bytes() == before


class TestPendingNeverExpiresOutOfTheWindow:
    def test_old_pending_still_surfaces(self, root):
        old = dt.date(2026, 1, 5)
        journal.add("待确认", "目标", "很久以前挂的问题", today=old, root=root)
        journal.add("观察", "训练", "今天的观察", today=D, root=root)

        pending, recent = journal.select(today=D, root=root)
        assert [p["text"] for p in pending] == ["很久以前挂的问题"]
        assert [r["text"] for r in recent] == ["今天的观察"]

    def test_old_observation_falls_out_of_the_window(self, root):
        journal.add("观察", "训练", "两个月前的观察", today=dt.date(2026, 6, 1), root=root)
        pending, recent = journal.select(today=D, root=root)
        assert pending == []
        assert recent == []

    def test_pending_goes_stale_after_60_days_but_is_kept(self, root):
        journal.add("待确认", "饮食", "挂太久的", today=dt.date(2026, 5, 1), root=root)
        pending, _ = journal.select(today=D, root=root)
        assert pending[0]["status"] == journal.STATUS_STALE
        assert "挂太久" in journal.render(today=D, root=root)

    def test_closed_pending_stops_surfacing(self, root):
        eid = journal.add("待确认", "目标", "已经拍板的", today=dt.date(2026, 5, 1), root=root)
        journal.set_status(eid, journal.STATUS_CONFIRMED, landed="x.md#y",
                           today=dt.date(2026, 5, 2), root=root)
        pending, recent = journal.select(today=D, root=root)
        assert pending == []
        assert recent == []  # 也早就出了 14 天窗口

    def test_stale_boundary_is_exactly_60_days(self, root):
        journal.add("待确认", "训练", "边界上", today=D - dt.timedelta(days=60), root=root)
        pending, _ = journal.select(today=D, root=root)
        assert pending[0]["status"] == journal.STATUS_OPEN
        journal.add("待确认", "训练", "过一天", today=D - dt.timedelta(days=61), root=root)
        pending, _ = journal.select(today=D, root=root)
        stale = [p for p in pending if p["status"] == journal.STATUS_STALE]
        assert [s["text"] for s in stale] == ["过一天"]


class TestSupersede:
    def test_old_entry_is_marked_but_preserved(self, root):
        old = journal.add("判断", "训练", "前侧酸是因为后链断档",
                          today=dt.date(2026, 8, 1), root=root)
        new = journal.add("判断", "训练", "前侧酸其实来自哈克机创新高",
                          supersedes=old, today=D, root=root)

        by_id = {i["id"]: i for i in journal.entries(today=D, root=root)}
        assert by_id[old]["status"] == journal.STATUS_SUPERSEDED
        assert by_id[old]["superseded_by"] == new
        # 内容一个字都没变
        assert by_id[old]["text"] == "前侧酸是因为后链断档"

    def test_cannot_supersede_something_that_does_not_exist(self, root):
        with pytest.raises(ValueError, match="不存在"):
            journal.add("判断", "训练", "x", supersedes="19700101-01",
                        today=D, root=root)

    def test_a_week_can_contradict_the_week_before(self, root):
        """用户明确要的行为：后一周推翻前一周，两条都留着。"""
        a = journal.add("判断", "饮食", "停滞是因为饮酒", today=dt.date(2026, 7, 1), root=root)
        b = journal.add("判断", "饮食", "停滞其实是蛋白缺口", supersedes=a,
                        today=dt.date(2026, 7, 8), root=root)
        journal.add("判断", "饮食", "两者都有", supersedes=b, today=D, root=root)
        items = journal.entries(today=D, root=root)
        assert len(items) == 3
        assert sum(1 for i in items if i["status"] == journal.STATUS_SUPERSEDED) == 2


class TestIdsAndStorage:
    def test_ids_are_sequential_within_a_day(self, root):
        ids = [journal.add("观察", "训练", f"第{i}条", today=D, root=root) for i in range(3)]
        assert ids == ["20260807-01", "20260807-02", "20260807-03"]

    def test_sharded_by_month(self, root):
        journal.add("观察", "训练", "七月", today=dt.date(2026, 7, 30), root=root)
        journal.add("观察", "训练", "八月", today=D, root=root)
        assert (root / "2026-07.jsonl").exists()
        assert (root / "2026-08.jsonl").exists()
        assert len(journal.entries(today=D, root=root)) == 2

    def test_corrupt_line_does_not_kill_the_whole_log(self, root):
        journal.add("观察", "训练", "好行", today=D, root=root)
        with open(root / "2026-08.jsonl", "a", encoding="utf-8") as f:
            f.write("{这不是 json\n")
        journal.add("观察", "训练", "后面的行", today=D, root=root)
        texts = [i["text"] for i in journal.entries(today=D, root=root)]
        assert set(texts) == {"好行", "后面的行"}

    def test_missing_trailing_newline_is_repaired(self, root):
        root.mkdir(parents=True)
        (root / "2026-08.jsonl").write_text(
            '{"rec":"entry","id":"20260807-01","date":"2026-08-07","kind":"观察",'
            '"topic":"训练","text":"半截","evidence":[],"supersedes":null}',
            encoding="utf-8")
        journal.add("观察", "训练", "接着写", today=D, root=root)
        assert len(journal.entries(today=D, root=root)) == 2

    def test_rejects_unknown_kind(self, root):
        with pytest.raises(ValueError, match="kind"):
            journal.add("结论", "训练", "x", today=D, root=root)

    def test_rejects_empty_text(self, root):
        with pytest.raises(ValueError, match="不能为空"):
            journal.add("观察", "训练", "   ", today=D, root=root)

    def test_unknown_topic_falls_back_instead_of_crashing(self, root):
        eid = journal.add("观察", "占星", "x", today=D, root=root)
        [item] = journal.entries(today=D, root=root)
        assert item["id"] == eid
        assert item["topic"] == "其他"


class TestRendering:
    def test_disclaimer_is_always_present(self, root):
        journal.add("判断", "训练", "某个推断", today=D, root=root)
        assert journal.DISCLAIMER in journal.render(today=D, root=root)
        assert journal.DISCLAIMER in journal.render_brief(today=D, root=root)

    def test_brief_is_empty_when_nothing_to_say(self, root):
        assert journal.render_brief(today=D, root=root) == ""

    def test_brief_carries_the_sentinel(self, root):
        journal.add("观察", "训练", "x", today=D, root=root)
        text = journal.render_brief(today=D, root=root)
        assert text.startswith(journal.BRIEF_OPEN)
        assert text.endswith(journal.BRIEF_CLOSE)

    def test_search_spans_full_history(self, root):
        journal.add("观察", "身体状态", "嗓子有点哑", today=dt.date(2026, 2, 1), root=root)
        hits = journal.search("嗓", today=D, root=root)
        assert len(hits) == 1
        # 而默认视图里它不该出现
        assert "嗓" not in journal.render(today=D, root=root)

    def test_search_survives_a_bad_regex(self, root):
        journal.add("观察", "训练", "深蹲 60kg (高杠)", today=D, root=root)
        assert len(journal.search("(高杠", today=D, root=root)) == 1

    def test_evidence_is_shown_so_numbers_can_be_traced(self, root):
        journal.add("判断", "训练", "前侧来自负荷创新高",
                    evidence=["hc compare --date 2026-08-05"], today=D, root=root)
        assert "hc compare --date 2026-08-05" in journal.render(today=D, root=root)
