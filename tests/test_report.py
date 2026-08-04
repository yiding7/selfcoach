"""报表引擎的测试。

最重要的一条：**生成的 HTML 必须完全自包含。** 没有任何外链。
断网能看、存档十年后能看、打印正常 —— 这是「即启即用」的一部分。
"""

from __future__ import annotations

import re

import pytest

from health_assistant.report.render import render, rich
from health_assistant.report.svg import (bar_chart, calendar_heatmap, fmt_num,
                                         line_chart, nice_ticks, Series,
                                         sparkline, stacked_bar)


MINIMAL_MODEL = {
    "schema": "ha.report/1", "kind": "weekly",
    "generated_at": "2026-07-26T22:00:00+08:00",
    "period": {"start": "2026-07-06", "end": "2026-07-12",
               "label": "2026 年第 28 周", "days": 7},
    "kpis": {"sessions": 3, "sessions_prev": 3, "volume_kg": 13809.0,
             "volume_kg_prev": 20900.0, "sets": 50, "sets_prev": 65,
             "duration_min": 111, "kcal": 328},
    "sessions": [{
        "date": "2026-07-12", "label": "胸 + 三头", "title": "",
        "duration_min": 48, "volume_kg": 7555.0, "sets_done": 24,
        "sets_planned": 25, "kcal": 228, "groups": {"胸": 18, "三头": 6},
        "source": "xunji", "movements": [],
    }],
    "groups": {"胸": {"sets": 18, "sets_per_week": 18, "volume_kg": 4310.0,
                      "status": "in_mav",
                      "landmarks": {"mev": 8, "mav": [12, 20], "mrv": 22}}},
    "comparisons": [], "prescriptions": [],
    "findings": {"优点": [], "缺点": [], "改进点": [], "信息": []},
    "body": {"raw": [], "trend": [], "latest_trend_kg": None,
             "change_kg": None, "rate_pct_per_week": None, "note": "体重用 7 日均线。"},
    "nutrition": {"meals": 0, "note": "本期没有饮食记录。"},
    "data_quality": {"days_in_period": 7, "days_synced": 7, "coverage_pct": 100.0,
                     "rpe_coverage": 0.0, "unclassified_movements": [],
                     "volume_incomplete": False, "meals_logged": 0},
    "narrative": {}, "narrative_slots": [],
}


class TestSelfContained:
    """整个项目最硬的一条约束。"""

    @pytest.fixture
    def html(self):
        return render(MINIMAL_MODEL)

    @pytest.mark.parametrize("pattern,why", [
        (r'src\s*=\s*["\']https?:', "外部资源引用"),
        (r'<link[^>]+href\s*=\s*["\']https?:', "外部样式表"),
        (r'@import', "CSS @import"),
        (r'<script', "任何脚本标签"),
        (r'url\(\s*["\']?https?:', "CSS 里的远程 url()"),
        (r'<iframe', "内嵌框架"),
    ])
    def test_no_external_reference(self, html, pattern, why):
        assert not re.search(pattern, html, re.I), f"报告里不该出现{why}"

    def test_no_http_at_all(self, html):
        urls = re.findall(r'https?://[^\s"\'<>)]+', html)
        assert urls == [], f"发现外链: {urls[:3]}"

    def test_is_complete_document(self, html):
        assert html.startswith("<!doctype html>")
        assert html.rstrip().endswith("</html>")
        assert '<meta charset="utf-8">' in html

    def test_charts_are_inline_svg(self, html):
        assert "<svg" in html
        assert "<img" not in html, "图表必须是内联 SVG，不能是图片文件"

    def test_theme_and_print_styles_present(self, html):
        assert "prefers-color-scheme: dark" in html
        assert 'data-theme="dark"' in html, "要能被手动主题切换覆盖"
        assert "@media print" in html
        # 没有这行，Chrome 打印时会把图表填色全去掉
        assert "print-color-adjust: exact" in html


class TestDataOnlyMode:
    def test_renders_fully_without_model(self):
        html = render(MINIMAL_MODEL)
        assert "纯数据模式" in html
        assert "2026 年第 28 周" in html
        assert "13.8k kg" in html, "没有模型时数据依然完整呈现"

    def test_narrative_injected_when_present(self):
        model = dict(MINIMAL_MODEL, narrative={"opening": "这周练得不错。"})
        html = render(model)
        assert "这周练得不错。" in html
        assert "纯数据模式" not in html

    def test_empty_narrative_slot_omitted(self):
        model = dict(MINIMAL_MODEL, narrative={"opening": "", "closing": "收尾。"})
        html = render(model)
        assert "收尾。" in html


class TestDisclaimer:
    def test_always_present(self):
        html = render(MINIMAL_MODEL)
        assert "免责声明" in html
        assert "不构成医疗建议" in html
        assert "就医" in html

    def test_e1rm_labelled_as_estimate(self):
        html = render(MINIMAL_MODEL)
        assert "估算" in html and "不是实测值" in html


class TestRichText:
    def test_escapes_first(self):
        assert "&lt;script&gt;" in rich("<script>")

    def test_bold(self):
        assert rich("请**从弱侧先开始**做") == "请<strong>从弱侧先开始</strong>做"

    def test_code(self):
        assert "<code>hc sync</code>" in rich("跑 `hc sync` 补齐")

    def test_no_injection_through_emphasis(self):
        out = rich("**<img src=x onerror=alert(1)>**")
        assert "<img" not in out
        assert "&lt;img" in out


class TestSvgPrimitives:
    def test_nice_ticks_monotonic(self):
        ticks = nice_ticks(0, 97)
        assert ticks == sorted(ticks)
        assert len(ticks) >= 2

    def test_nice_ticks_degenerate_range(self):
        assert len(nice_ticks(5, 5)) >= 2

    def test_charts_produce_valid_svg(self):
        charts = [
            line_chart([Series("a", [(0, 1), (1, 2)])], ["一", "二"]),
            bar_chart(["胸", "背"], [18, 12]),
            stacked_bar(["07-06"], [{"胸": 4}], ["胸"], lambda k: "s1"),
            sparkline([1, 2, 3]),
            calendar_heatmap({"2026-07-06": 3}, "2026-07-01", "2026-07-31"),
        ]
        for svg in charts:
            assert svg.startswith("<svg") and svg.endswith("</svg>")
            assert svg.count("<svg") == svg.count("</svg>")

    def test_charts_use_css_vars_not_hardcoded_colors(self):
        """图元只带 class，颜色交给 CSS —— 这样深浅色才能自动跟随。"""
        svg = bar_chart(["胸"], [18], css_by_index=lambda i: "s1")
        assert not re.search(r'fill="#[0-9a-f]{3,6}"', svg, re.I)

    def test_empty_data_does_not_crash(self):
        assert "<svg" in line_chart([], [])
        assert "<svg" in bar_chart([], [])
        assert "<svg" in sparkline([])

    def test_per_bar_bands_are_independent(self):
        """各肌群的目标区间不同，必须每根柱子画自己的。"""
        svg = bar_chart(["胸", "腹部"], [18, 9],
                        per_bar_bands=[(12, 20, "胸"), (8, 16, "腹部")])
        assert svg.count('class="band ok"') == 2

    @pytest.mark.parametrize("v,expected", [
        (12500, "12.5k"), (250, "250"), (12.5, "12.5"), (None, "—"),
    ])
    def test_fmt_num(self, v, expected):
        assert fmt_num(v) == expected


class TestBodyMap:
    def test_legend_swatches_have_background(self):
        """.heat-N 设的是 SVG fill，对 HTML <i> 无效，色块必须用 inline background。"""
        from health_assistant.report.body_map import body_heatmap
        html = body_heatmap({"胸": 18, "三头": 6})
        assert "background:var(--heat-0)" in html

    def test_untrained_groups_listed(self):
        from health_assistant.report.body_map import body_heatmap
        html = body_heatmap({"胸": 18})
        assert "本周没练到" in html

    def test_handles_empty(self):
        from health_assistant.report.body_map import body_heatmap
        assert "<svg" in body_heatmap({})


class TestBodyBlockIsPeriodScoped:
    """月报的体重块必须只描述**本期**，不能泄漏「今天」的数字。

    回归：`build()` 曾把全量均线传给 `weight_trend_pct_per_week()`，
    又用 `trend[-1]` 当期末体重。结果六份月报的 latest_trend_kg 全是同一个数，
    3 月那份实际减了 7.23kg 却显示 +0.03%/周 —— 因为它算的是 7 月。
    """

    @pytest.fixture
    def patched(self, monkeypatch):
        """1 月匀速下降 100→90，2–3 月完全持平在 90。

        用 3 月而不是 2 月做「持平」断言：7 日均线会跨月边界带入 1 月的下降，
        所以 2 月头几天的均线仍在往下走（实测 -0.2%/周）—— 那是移动均线的正确行为，
        不是缺陷。3 月已经完全脱离 1 月的影响，可以断言精确的 0。
        """
        import datetime as dt
        import importlib
        # 不能写 `from health_assistant.report import build` ——
        # 包的 __init__ 把同名函数导出了，拿到的会是函数不是模块。
        build_mod = importlib.import_module("health_assistant.report.build")

        body = []
        for i in range(31):                      # 1 月：100 → 90
            d = (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()
            body.append({"id": f"w{d}", "date": d, "type": "weight",
                         "value": 100.0 - i * (10.0 / 30)})
        for i in range(59):                      # 2–3 月：持平 90
            d = (dt.date(2026, 2, 1) + dt.timedelta(days=i)).isoformat()
            body.append({"id": f"w{d}", "date": d, "type": "weight", "value": 90.0})

        monkeypatch.setattr(build_mod.store, "load_body", lambda: body)
        monkeypatch.setattr(build_mod.store, "load_sessions", lambda: [])
        monkeypatch.setattr(build_mod.store, "load_meals", lambda **kw: [])
        monkeypatch.setattr(build_mod.store, "load_index", lambda year: {})
        return build_mod

    def test_january_reports_january(self, patched):
        import datetime as dt
        b = patched.build("monthly", dt.date(2026, 1, 1), dt.date(2026, 1, 31))["body"]
        assert b["latest_trend_kg"] < 95, "期末体重必须是本期最后一天，不是今天"
        assert b["change_kg"] < 0
        assert b["rate_pct_per_week"] < 0, "1 月在减重，速率必须为负"

    def test_march_reports_march(self, patched):
        import datetime as dt
        b = patched.build("monthly", dt.date(2026, 3, 1), dt.date(2026, 3, 31))["body"]
        assert b["rate_pct_per_week"] == 0.0, "3 月完全持平，速率必须是 0"
        assert b["change_kg"] == 0.0

    def test_two_periods_do_not_share_a_number(self, patched):
        """核心断言：不同周期的体重块不能得出同一个结论。

        缺陷版本下这两组数字完全相同 —— 因为两份报告算的都是「今天」。
        """
        import datetime as dt
        jan = patched.build("monthly", dt.date(2026, 1, 1), dt.date(2026, 1, 31))["body"]
        mar = patched.build("monthly", dt.date(2026, 3, 1), dt.date(2026, 3, 31))["body"]
        assert jan["latest_trend_kg"] != mar["latest_trend_kg"]
        assert jan["rate_pct_per_week"] != mar["rate_pct_per_week"]
        assert jan["rate_pct_per_week"] < -1.0, "1 月在快速减重"

    def test_no_body_data_in_period_does_not_crash(self, patched):
        import datetime as dt
        b = patched.build("monthly", dt.date(2026, 6, 1), dt.date(2026, 6, 30))["body"]
        assert b["latest_trend_kg"] is None
        assert b["rate_pct_per_week"] is None
