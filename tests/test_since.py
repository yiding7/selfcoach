"""`--since` 的相对写法必须和等价的绝对日期完全等价。

起因是一个**静默失效**：`hc cardio --since 7d` 把字符串 `"7d"` 原样传给下游，
下游拿它和 ISO 日期串比大小 —— ASCII 里数字排在字母前，`"2026-08-12" < "7d"`，
于是全部记录被滤空。命令不报错、退出码是 0，只是改口说「本地没有有氧记录」，
还顺手建议用户去跑 `hc sync train`。

假阴性比崩溃危险得多：崩溃会被修，「你没数据」会被相信。`hc import-health
--since 30d` 是同一个洞（那边报的是「没有解析出可用数据」）。

所以这里锁一条不变式：

    每一个接受 `--since` 的命令，`Nd` 和等价的绝对日期
    必须给出**逐字相同**的输出。

再加一条针对 cardio 的：有有氧记录时，`--since 7d` 不许走进「没有记录」那个分支。

覆盖表 `COVERED` 由 `test_every_since_command_is_covered` 对着 argparse 树校验 ——
新加一个带 `--since` 的命令却不补测试，那条会红。

测试不碰 `data/`：训练、日志、骰子、健康导出四份数据全部现造在 tmp 里。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

import pytest

from health_assistant import calibration, cli, dice, journal, store
from health_assistant.analytics import cardio as C

# 相对写法是相对「今天」算的，所以固定日期没有意义，只能跟着真实今天走。
TODAY = dt.date.today()
ISO_7D = (TODAY - dt.timedelta(days=7)).isoformat()
RECENT = (TODAY - dt.timedelta(days=2)).isoformat()     # 落在 7 天窗口里
OLD = (TODAY - dt.timedelta(days=30)).isoformat()       # 落在窗口外


def _out(capsys, argv: list[str]) -> str:
    assert cli.main(argv) == 0, f"{argv} 退出码非 0"
    return capsys.readouterr().out


def _pair(capsys, argv_head: list[str], argv_tail: list[str] = []) -> tuple[str, str]:
    """同一个命令跑两遍：一遍 `7d`，一遍等价的绝对日期。"""
    rel = _out(capsys, argv_head + ["--since", "7d"] + argv_tail)
    absolute = _out(capsys, argv_head + ["--since", ISO_7D] + argv_tail)
    return rel, absolute


# ── 数据 fixture（全部在 tmp 里，不读 data/）──────────────────────────────


def _session(date: str, kcal: int) -> dict:
    return {
        "id": f"test-{date}", "date": date, "source": "test", "title": "有氧",
        "movements": [{
            "name": "爬楼梯", "exetype": "cardio", "raw_type": "cardio",
            "sets": [{"done": True, "time_s": 1800,
                      "hr": {"avg": 120, "max": 140},
                      "metrics": {"kcal": kcal}}],
        }],
    }


@pytest.fixture()
def training(tmp_path, monkeypatch):
    """两次训练：一次在 7 天窗口内，一次在窗口外。"""
    root = tmp_path / "training"
    root.mkdir()
    (root / "sessions.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n"
                for s in (_session(OLD, 200), _session(RECENT, 100))),
        encoding="utf-8")
    monkeypatch.setattr(store, "TRAINING_DIR", root)
    # 口径规则文件不存在 —— 折算不该影响日期过滤，但也不该把本地真实规则带进来
    monkeypatch.setattr(calibration, "PATH", tmp_path / "no-calibration.jsonl")
    return root


@pytest.fixture()
def hr_profile(tmp_path, monkeypatch):
    """固定生理参数，否则 cardio 会因为缺 birth_year 直接返回 1。"""
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"birth_year": 1993, "sex": "male"}), encoding="utf-8")
    monkeypatch.setattr(C, "PROFILE_PATH", p)
    C._profile.cache_clear()
    yield
    C._profile.cache_clear()


# ── 不变式一：cardio ────────────────────────────────────────────────────


class TestCardio:
    def test_relative_matches_absolute(self, capsys, training, hr_profile):
        rel, absolute = _pair(capsys, ["cardio"])
        assert rel == absolute

    def test_relative_is_not_silently_empty(self, capsys, training, hr_profile):
        """这就是那个缺陷本身：有记录，却报「本地没有有氧记录」。"""
        out = _out(capsys, ["cardio", "--since", "7d"])
        assert "本地没有有氧记录" not in out
        assert RECENT in out

    def test_window_still_filters(self, capsys, training, hr_profile):
        """别用「全部放行」换掉过滤 —— 窗口外那次必须被滤掉。"""
        out = _out(capsys, ["cardio", "--since", "7d"])
        assert OLD not in out


# ── 不变式二：其余每一个接受 --since 的命令 ──────────────────────────────


class TestSessions:
    def test_relative_matches_absolute(self, capsys, training):
        rel, absolute = _pair(capsys, ["sessions"])
        assert rel == absolute
        assert RECENT in rel and OLD not in rel


class TestJournal:
    @pytest.fixture(autouse=True)
    def _journal(self, tmp_path, monkeypatch):
        root = tmp_path / "coach-journal"
        monkeypatch.setattr(journal, "JOURNAL_DIR", root)
        journal.add("观察", "训练", "窗口内这条",
                    today=TODAY - dt.timedelta(days=2), root=root)
        journal.add("观察", "训练", "窗口外那条",
                    today=TODAY - dt.timedelta(days=30), root=root)

    def test_relative_matches_absolute(self, capsys):
        rel, absolute = _pair(capsys, ["journal"])
        assert rel == absolute
        assert "窗口内这条" in rel and "窗口外那条" not in rel


class TestDiceLog:
    @pytest.fixture(autouse=True)
    def _rolls(self, tmp_path, monkeypatch):
        p = tmp_path / "dice.jsonl"
        rows = [{"rec": "roll", "date": OLD, "slot": "午餐",
                 "dish": "窗口外那道", "tier": "绿"},
                {"rec": "roll", "date": RECENT, "slot": "午餐",
                 "dish": "窗口内那道", "tier": "绿"}]
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                     encoding="utf-8")
        monkeypatch.setattr(dice, "LOG_PATH", p)

    def test_relative_matches_absolute(self, capsys):
        rel, absolute = _pair(capsys, ["dice", "log"])
        assert rel == absolute
        assert "窗口内那道" in rel and "窗口外那道" not in rel


class TestImportHealth:
    @pytest.fixture()
    def export_xml(self, tmp_path):
        def rec(day: str, value: int) -> str:
            stamp = f"{day} 08:00:00 +0800"
            return (f' <Record type="HKQuantityTypeIdentifierBodyMass"'
                    f' startDate="{stamp}" endDate="{stamp}"'
                    f' value="{value}" unit="kg"/>')

        p = tmp_path / "导出.xml"
        p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="zh_CN">\n'
                     + rec(OLD, 80) + "\n" + rec(RECENT, 75) + "\n</HealthData>\n",
                     encoding="utf-8")
        return p

    def test_relative_matches_absolute(self, capsys, export_xml):
        rel, absolute = _pair(capsys, ["import-health", str(export_xml)], ["--dry-run"])
        assert rel == absolute
        # 只剩窗口内那条，起止日期都该是它
        assert f"{RECENT} ~ {RECENT}" in rel

    def test_relative_is_not_silently_empty(self, capsys, export_xml):
        out = _out(capsys, ["import-health", str(export_xml), "--since", "7d", "--dry-run"])
        assert "没有解析出可用数据" not in out


class TestSync:
    """sync 会联网，所以只拦住送进同步层的那个区间端点。"""

    @pytest.fixture()
    def calls(self, monkeypatch):
        from health_assistant.xunji import client as xclient
        from health_assistant.xunji import sync as xsync

        seen: list[tuple[str, object, object]] = []

        class _Res:
            errors: list[str] = []

            def summary(self) -> str:
                return "（测试桩）"

        monkeypatch.setattr(cli, "load_env", lambda *a, **k: None)
        monkeypatch.setattr(xclient, "XunjiClient", lambda **k: object())
        for name in ("sync_body", "sync_food"):
            monkeypatch.setattr(
                xsync, name,
                lambda client, start, end, _n=name, **k: seen.append((_n, start, end)))
        monkeypatch.setattr(
            xsync, "sync_training",
            lambda client, start, end, **k: (seen.append(("train", start, end)), _Res())[1])
        return seen

    def test_relative_matches_absolute(self, capsys, calls):
        _out(capsys, ["sync", "--since", "7d"])
        relative = list(calls)
        calls.clear()
        _out(capsys, ["sync", "--since", ISO_7D])
        assert relative == calls
        # 区间端点必须已经是日期对象，不能是没翻译的 "7d"
        assert all(start == TODAY - dt.timedelta(days=7) for _, start, _ in relative)


# ── 覆盖表：新加 --since 而不加测试，这条会红 ────────────────────────────


COVERED = {
    ("sync",), ("import-health",), ("cardio",),
    ("sessions",), ("journal",), ("dice", "log"),
}


def _commands_with_since(parser, prefix: tuple[str, ...] = ()):
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            for name, sub in act.choices.items():
                yield from _commands_with_since(sub, prefix + (name,))
        elif "--since" in (act.option_strings or ()):
            yield prefix


def test_every_since_command_is_covered():
    found = set(_commands_with_since(cli.build_parser()))
    assert found == COVERED, (
        "有命令新增或删除了 --since。每一个都必须锁住"
        "「相对写法 == 等价绝对日期」这条不变式：给它补一个用例，再更新 COVERED。"
    )


class TestParseSince:
    @pytest.mark.parametrize("value,expect", [
        ("7d", TODAY - dt.timedelta(days=7)),
        ("12w", TODAY - dt.timedelta(weeks=12)),
        ("6m", TODAY - dt.timedelta(days=180)),
        ("1y", TODAY - dt.timedelta(days=365)),
        ("today", TODAY),
        ("今天", TODAY),
        ("2026-07-01", dt.date(2026, 7, 1)),
    ])
    def test_forms(self, value, expect):
        assert cli._parse_since(value, TODAY) == expect

    def test_garbage_raises_instead_of_filtering_everything_out(self):
        """看不懂就要炸。悄悄退化成字符串比较正是这个缺陷的形状。"""
        with pytest.raises(ValueError):
            cli._parse_since("上周", TODAY)
