"""年龄的回归测试。

原来 `nutrition.age_years()` 和 `cardio.age_now()` 各写了一份
`today.year - birth_year`。这个模块把它们合成一处，所以测试盯两件事：

1. 有月份时年龄按月份走，别再全年偏大一岁
2. 缺月份时**退回旧口径并说清楚**，不许猜一个月份出来
"""

from __future__ import annotations

import datetime as dt

import pytest

from health_assistant import age as age_mod


def prof(year=None, month=None) -> dict:
    out = {}
    if year:
        out["birth_year"] = year
    if month:
        out["birth_month"] = month
    return out


class TestAgeWithMonth:
    def test_before_birth_month_is_one_less(self):
        """1993-11 生的人，2026-03 是 32 岁，不是 33。"""
        n, how = age_mod.age_at(dt.date(2026, 3, 15), prof(1993, 11))
        assert n == 32
        assert "1993-11" in how

    def test_on_the_first_of_birth_month_it_ticks(self):
        """约定：生日按当月 1 号计。"""
        assert age_mod.age(dt.date(2026, 10, 31), prof(1993, 11)) == 32
        assert age_mod.age(dt.date(2026, 11, 1), prof(1993, 11)) == 33

    def test_january_birth_never_goes_negative_at_year_edge(self):
        assert age_mod.age(dt.date(2026, 1, 1), prof(1993, 1)) == 33
        assert age_mod.age(dt.date(2025, 12, 31), prof(1993, 1)) == 32


class TestAgeWithoutMonth:
    def test_falls_back_to_the_old_formula(self):
        assert age_mod.age(dt.date(2026, 3, 15), prof(1993)) == 33

    def test_says_the_number_may_be_high(self):
        """静默退化是不行的 —— 用户有权知道这个数可能偏大一岁。"""
        _, how = age_mod.age_at(dt.date(2026, 3, 15), prof(1993))
        assert "偏大" in how

    def test_no_birth_year_returns_none_not_a_guess(self):
        n, how = age_mod.age_at(dt.date(2026, 3, 15), {})
        assert n is None
        assert "缺" in how

    def test_bad_month_is_dropped_not_clamped(self):
        """月份是 0 或 13 时退回只有年份，**不要**夹到 1 或 12。

        夹一下会造出一个用户没填过的月份，而它会一直影响年龄。
        """
        for bad in (0, 13, "十一月", None):
            assert age_mod.birth_ym(prof(1993, bad))[1] is None


class TestParse:
    @pytest.mark.parametrize("raw,expect", [
        ("1993-11", (1993, 11)),
        ("1993/11", (1993, 11)),
        ("1993.11", (1993, 11)),
        ("1993 11", (1993, 11)),
        ("1993年11月", (1993, 11)),
        ("1993-1", (1993, 1)),
        ("1993", (1993, None)),
        ("  1993-11  ", (1993, 11)),
    ])
    def test_accepts_common_shapes(self, raw, expect):
        assert age_mod.parse_ym(raw) == expect

    @pytest.mark.parametrize("raw", ["", "93-11", "1993-13", "1993-00",
                                     "abc", "1800", "3000-01"])
    def test_rejects_rather_than_guesses(self, raw):
        assert age_mod.parse_ym(raw) is None


class TestSingleSourceOfTruth:
    def test_nutrition_and_cardio_agree(self, tmp_path, monkeypatch):
        """两个模块必须给出同一个年龄 —— 它们曾经各算各的。"""
        import json

        from health_assistant import nutrition
        from health_assistant.analytics import cardio

        p = tmp_path / "profile.json"
        p.write_text(json.dumps({"birth_year": 1993, "birth_month": 11}),
                     encoding="utf-8")
        monkeypatch.setattr(nutrition, "PROFILE_PATH", p)
        monkeypatch.setattr(cardio, "PROFILE_PATH", p)
        cardio._profile.cache_clear()      # cardio 那份带 lru_cache

        today = dt.date(2026, 3, 15)
        assert nutrition.age_years(today) == cardio.age_now(today) == 32

        cardio._profile.cache_clear()
