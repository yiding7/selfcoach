"""手记速记解析器的测试。

这是没有训记的用户的主路径，所以解析必须可靠。
模型只负责把口述转成速记（结果会给用户确认），解析这一步是确定性的。
"""

from __future__ import annotations

import datetime as dt

import pytest

from health_assistant.manual import dedupe_against, parse, summarize


def one(text, **kw):
    r = parse(text, **kw)
    assert r.session is not None, [str(i) for i in r.issues]
    return r


class TestHeader:
    def test_date_title_and_time(self):
        r = one("# 2026-07-26 推日 19:05-20:20\n杠铃卧推 60x10")
        s = r.session
        assert s["date"] == "2026-07-26"
        assert s["title"] == "推日"
        assert s["duration_s"] == 75 * 60

    def test_defaults_to_today(self):
        r = one("杠铃卧推 60x10")
        assert r.session["date"] == dt.date.today().isoformat()

    def test_explicit_default_date(self):
        r = one("杠铃卧推 60x10", default_date=dt.date(2026, 1, 2))
        assert r.session["date"] == "2026-01-02"

    def test_overnight_session(self):
        r = one("# 2026-07-26 深夜 23:30-00:40\n杠铃卧推 60x10")
        assert r.session["duration_s"] == 70 * 60


class TestSets:
    def test_basic(self):
        r = one("杠铃卧推 60x10 62.5x8")
        sets = r.session["movements"][0]["sets"]
        assert [(s["weight_kg"], s["reps"]) for s in sets] == [(60.0, 10.0), (62.5, 8.0)]

    def test_repeat_inline(self):
        r = one("上斜哑铃卧推 22.5x12x3")
        assert len(r.session["movements"][0]["sets"]) == 3

    def test_repeat_standalone_token(self):
        """「平板支撑 T:60s x3」这种写法很自然，不该报看不懂。"""
        r = one("平板支撑 T:60s x3")
        sets = r.session["movements"][0]["sets"]
        assert len(sets) == 3
        assert all(s["time_s"] == 60 for s in sets)

    def test_warmup_marked_and_excluded(self):
        r = one("杠铃卧推 ~40x10 60x10")
        sets = r.session["movements"][0]["sets"]
        assert sets[0]["kind"] == "warmup"
        assert sets[1]["kind"] == "work"

    def test_rpe(self):
        r = one("杠铃卧推 62.5x8@8.5")
        assert r.session["movements"][0]["sets"][0]["rpe"] == 8.5

    def test_bodyweight(self):
        r = one("双杠臂屈伸 BWx12")
        s = r.session["movements"][0]["sets"][0]
        assert s["self_weight"] is True and s["weight_kg"] is None

    def test_bodyweight_plus_load(self):
        r = one("双杠臂屈伸 BW+10x8")
        s = r.session["movements"][0]["sets"][0]
        assert s["self_weight"] is True and s["weight_kg"] == 10.0

    def test_chinese_bodyweight_token(self):
        r = one("俯卧撑 自重x20")
        assert r.session["movements"][0]["sets"][0]["self_weight"] is True

    def test_timed_minutes(self):
        r = one("平板支撑 T:2分")
        assert r.session["movements"][0]["sets"][0]["time_s"] == 120

    def test_unilateral_pairs(self):
        r = one("保加利亚分腿蹲 L:20x10 R:22.5x10")
        m = r.session["movements"][0]
        assert m["unilateral"] is True
        s = m["sets"][0]
        assert s["left_weight_kg"] == 20.0 and s["weight_kg"] == 22.5

    def test_unpaired_side_warns(self):
        r = parse("保加利亚分腿蹲 L:20x10")
        assert any("没有配对" in i.text for i in r.issues)


class TestMovementNames:
    def test_multiword_name(self):
        r = one("上斜 哑铃 卧推 22.5x12")
        assert r.session["movements"][0]["name"] == "上斜 哑铃 卧推"

    def test_name_with_parens(self):
        r = one("引体向上（辅助） 53x8")
        assert r.session["movements"][0]["name"] == "引体向上（辅助）"

    def test_missing_sets_is_error(self):
        r = parse("杠铃卧推")
        assert r.session is None or any(i.severity == "error" for i in r.issues)

    def test_unparseable_token_warns_not_fails(self):
        r = parse("杠铃卧推 60x10 乱七八糟")
        assert r.session is not None
        assert len(r.session["movements"][0]["sets"]) == 1


class TestNotes:
    def test_session_note(self):
        r = one("杠铃卧推 60x10\n> 睡眠不足\n> 右肩有点紧")
        assert "睡眠不足" in r.session["note"]
        assert "右肩有点紧" in r.session["note"]


class TestIdentity:
    def test_id_is_stable(self):
        text = "# 2026-07-26\n杠铃卧推 60x10 60x8"
        assert parse(text).session["id"] == parse(text).session["id"]

    def test_id_changes_with_content(self):
        a = parse("# 2026-07-26\n杠铃卧推 60x10").session["id"]
        b = parse("# 2026-07-26\n杠铃卧推 60x10\n绳索夹胸 15x15").session["id"]
        assert a != b

    def test_source_is_manual(self):
        assert one("杠铃卧推 60x10").session["source"] == "manual"


class TestSummary:
    def test_shows_bodyweight_clearly(self):
        """自重加负重不能只显示 10 —— 看不出是自重动作。"""
        text = summarize(one("双杠臂屈伸 BW+10x8").session)
        assert "自重+10" in text

    def test_shows_unilateral(self):
        text = summarize(one("哑铃弯举 L:10x10 R:12x10").session)
        assert "单侧" in text and "左10/右12" in text

    def test_separates_warmup(self):
        text = summarize(one("杠铃卧推 ~40x10 60x10").session)
        assert "热身 1 组" in text


class TestDedupe:
    XUNJI = {
        "id": "xunji:2026-07-26:123", "date": "2026-07-26", "source": "xunji",
        "movements": [{"name": "杠铃卧推"}, {"name": "绳索夹胸"}, {"name": "上斜卧推"}],
    }

    def test_detects_overlap(self):
        mine = one("# 2026-07-26\n杠铃卧推 60x10\n绳索夹胸 15x15").session
        assert dedupe_against(mine, [self.XUNJI]) is not None

    def test_different_movements_not_duplicate(self):
        mine = one("# 2026-07-26\n杠铃深蹲 100x5\n腿举 200x10").session
        assert dedupe_against(mine, [self.XUNJI]) is None

    def test_different_day_not_duplicate(self):
        mine = one("# 2026-07-20\n杠铃卧推 60x10\n绳索夹胸 15x15").session
        assert dedupe_against(mine, [self.XUNJI]) is None

    def test_ignores_other_manual_records(self):
        other = dict(self.XUNJI, source="manual")
        mine = one("# 2026-07-26\n杠铃卧推 60x10\n绳索夹胸 15x15").session
        assert dedupe_against(mine, [other]) is None


class TestIntegrationWithAnalytics:
    def test_manual_session_flows_through_analysis(self):
        """手记必须和训记同步来的数据走完全相同的分析流程。"""
        from health_assistant.analytics.metrics import session_stats

        s = one("# 2026-07-26 推日\n"
                "杠铃卧推 60x10 60x10 62.5x8\n"
                "绳索夹胸 15x15x3\n"
                "双杠臂屈伸 BWx12").session
        st = session_stats(s, bodyweight_kg=80.0)
        assert st.groups.get("胸", 0) == 7
        assert st.volume_kg and st.volume_kg > 0
        # e1RM 取所有组里最大的一个：60×10 → 80.0 比 62.5×8 → 79.2 更高。
        # 顶组重量最大 ≠ 估算 1RM 最大，这正是要用公式而不是看重量的原因。
        assert st.movements[0].best_e1rm == pytest.approx(60 * (1 + 10 / 30), abs=0.1)
        assert st.movements[0].top_load_kg == 62.5
