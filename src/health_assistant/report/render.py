"""ReportModel → 自包含单文件 HTML。

硬性约束：**输出里不允许出现任何外链。** 没有 CDN、没有外部字体、没有远程图片、
没有 script src。图表是内联 SVG，样式是内联 CSS。断网能看，存档十年后还能看。
有测试断言这一点。

叙述段落是可选的：没有模型时整份报告依然完整，只是少了教练的话。
"""

from __future__ import annotations

import re

from .body_map import body_heatmap
from .svg import (bar_chart, calendar_heatmap, donut, esc, fmt_num, line_chart,
                  Series, stacked_bar)
from .theme import CSS

GROUP_CSS = {
    "胸": "s1", "背": "s2", "肩": "s3", "腿": "s5", "臀": "s6",
    "二头": "s4", "三头": "s7", "腹部": "s3", "小腿": "s2",
    "前臂": "s4", "颈": "s7", "有氧": "s5", "全身": "s6", "未分类": "s7",
}


def group_css(g: str) -> str:
    return GROUP_CSS.get(g, "s7")


def rich(text) -> str:
    """转义后再处理行内强调。

    结论文本里会用 **强调** 标出关键点（比如「**从弱侧先开始**」）。
    终端里星号本身就是通行写法，HTML 里则要变成 <strong>，
    否则用户会看到一堆字面星号。

    先 esc 再替换是安全的：esc 不会产生 * 或 `，所以不存在被注入的可能。
    """
    out = esc(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+?)`", r"<code>\1</code>", out)
    return out


def _kpi(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return (f'<div class="kpi"><div class="k">{esc(label)}</div>'
            f'<div class="v {tone}">{esc(value)}</div>'
            + (f'<div class="s">{esc(sub)}</div>' if sub else "") + "</div>")


def _delta_sub(cur: float, prev: float, unit: str = "") -> tuple[str, str]:
    if not prev:
        return ("", "")
    pct = (cur - prev) / prev * 100
    tone = "up" if pct > 0 else ("down" if pct < 0 else "")
    return (f"上期 {fmt_num(prev)}{unit}（{pct:+.0f}%）", tone)


def _findings_list(items: list[dict], css: str) -> str:
    if not items:
        return ""
    rows = "".join(f'<li class="{css}">{rich(f["text"])}</li>' for f in items)
    return f'<ul class="findings">{rows}</ul>'


def _narrative(model: dict, slot: str) -> str:
    text = (model.get("narrative") or {}).get(slot)
    if not text:
        return ""
    paras = "".join(f"<p>{rich(p.strip())}</p>"
                    for p in str(text).split("\n\n") if p.strip())
    return f'<div class="narrative">{paras}</div>'


def _aggregate_by_month(sessions: list[dict]) -> tuple[list[str], list[dict], list[dict]]:
    """年报按月汇总。

    一年可能有 150+ 次训练，逐次画柱子会挤成一团、表格也长得没法看。
    按月聚合才是年报该有的粒度。
    """
    buckets: dict[str, dict] = {}
    for s in sessions:
        ym = s["date"][:7]
        b = buckets.setdefault(ym, {"groups": {}, "sessions": 0, "sets": 0,
                                    "volume": 0.0, "minutes": 0.0, "days": set()})
        b["sessions"] += 1
        b["sets"] += s["sets_done"]
        b["volume"] += s["volume_kg"] or 0
        b["minutes"] += s["duration_min"] or 0
        b["days"].add(s["date"])
        for g, n in s["groups"].items():
            b["groups"][g] = b["groups"].get(g, 0) + n

    months = sorted(buckets)
    labels = [f"{int(m[5:7])}月" for m in months]
    stacks = [buckets[m]["groups"] for m in months]
    rows = [{"label": labels[i], **buckets[months[i]]} for i in range(len(months))]
    return labels, stacks, rows


def _section_sessions(model: dict) -> str:
    sessions = model["sessions"]
    if not sessions:
        return ('<section class="card"><h2>训练</h2>'
                '<p class="muted">本期没有训练记录。'
                '如果练了但没同步，跑 <code>hc sync</code>；'
                '想手动补记，直接告诉助手就行。</p></section>')

    yearly = model["kind"] == "yearly"

    if yearly:
        labels, stacks, agg = _aggregate_by_month(sessions)
        rows = "".join(
            f'<tr><td>{esc(r["label"])}</td>'
            f'<td class="num">{r["sessions"]}</td>'
            f'<td class="num">{len(r["days"])}</td>'
            f'<td class="num">{r["sets"]}</td>'
            f'<td class="num">{fmt_num(r["volume"]) if r["volume"] else "—"}</td>'
            f'<td class="num">{fmt_num(r["minutes"]) if r["minutes"] else "—"}</td></tr>'
            for r in agg)
        head = ('<thead><tr><th>月份</th><th class="num">训练次数</th>'
                '<th class="num">训练天数</th><th class="num">组数</th>'
                '<th class="num">容量 kg</th><th class="num">分钟</th></tr></thead>')
        chart_title = "每月的部位组数分布"
    else:
        labels = [s["date"][5:] for s in sessions]
        stacks = [dict(s["groups"]) for s in sessions]
        rows = "".join(
            f'<tr><td>{esc(s["date"][5:])}</td><td>{esc(s["label"])}</td>'
            f'<td class="num">{s["sets_done"]}/{s["sets_planned"]}</td>'
            f'<td class="num">{fmt_num(s["volume_kg"]) if s["volume_kg"] else "—"}</td>'
            f'<td class="num">{fmt_num(s["duration_min"]) if s["duration_min"] else "—"}</td>'
            f'<td class="num">{fmt_num(s["kcal"]) if s.get("kcal") else "—"}</td></tr>'
            for s in sessions)
        head = ('<thead><tr><th>日期</th><th>部位</th><th class="num">组数</th>'
                '<th class="num">容量 kg</th><th class="num">分钟</th>'
                '<th class="num">kcal</th></tr></thead>')
        chart_title = "每次训练的部位组数分布"

    keys: list[str] = []
    for st in stacks:
        for g in st:
            if g not in keys:
                keys.append(g)

    legend = "".join(
        f'<span><i style="background:var(--{group_css(k)})"></i>{esc(k)}</span>'
        for k in keys)

    return f"""<section class="card">
<h2>训练分布</h2>
{stacked_bar(labels, stacks, keys, group_css, height=230, y_unit=" 组",
             title=chart_title)}
<div class="legend">{legend}</div>
<div class="scroll-x"><table>{head}<tbody>{rows}</tbody></table></div>
</section>"""


def _section_groups(model: dict) -> str:
    groups = model["groups"]
    if not groups:
        return ""
    names = list(groups)
    per_week = [groups[g]["sets_per_week"] for g in names]

    # 每个肌群画**自己的**最佳区间，而不是一条横贯全图的参考带。
    # 各肌群的地标差很多（胸 12–20 组，腹部 8–16 组），
    # 画一条通用带会让人拿胸的标准去看腹部。
    per_bar = []
    for g in names:
        lm = groups[g]["landmarks"]
        per_bar.append((lm["mav"][0], lm["mav"][1],
                        f"{g} 的最佳区间 {lm['mav'][0]}–{lm['mav'][1]} 组/周")
                       if lm else None)

    status_text = {
        "under_mev": ('<span class="pill bad">偏少</span>', "低于最小有效容量"),
        "mev_mav": ('<span class="pill">偏低</span>', "在有效区间下沿"),
        "in_mav": ('<span class="pill good">合适</span>', "在最佳区间内"),
        "mav_mrv": ('<span class="pill">偏高</span>', "接近可恢复上限"),
        "over_mrv": ('<span class="pill bad">过量</span>', "超过可恢复上限"),
        "unknown": ('<span class="pill">—</span>', "没有参考区间"),
    }
    rows = "".join(
        f'<tr><td>{esc(g)}</td><td class="num">{fmt_num(groups[g]["sets"])}</td>'
        f'<td class="num">{fmt_num(groups[g]["sets_per_week"])}</td>'
        f'<td class="num">{fmt_num(groups[g]["volume_kg"]) if groups[g]["volume_kg"] else "—"}</td>'
        f'<td>{status_text.get(groups[g]["status"], status_text["unknown"])[0]}</td></tr>'
        for g in names)

    return f"""<section class="card">
<h2>各部位训练量</h2>
{bar_chart(names, per_week, height=230, y_unit=" 组/周", per_bar_bands=per_bar,
           css_by_index=lambda i: group_css(names[i]),
           title="每个部位的周均有效组数")}
<p class="muted small">柱子背后的浅色区间是<strong>该部位自己</strong>的最佳训练量区间 ——
各部位的区间不同，所以不画统一的参考线。</p>
<div class="scroll-x"><table>
<thead><tr><th>部位</th><th class="num">总组数</th><th class="num">组/周</th>
<th class="num">容量 kg</th><th>参考</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="muted small">参考区间是人群统计值，不是处方。个体差异很大，
减脂期的可恢复上限还会更低。这里只用来提示「可能偏少 / 偏多」，
你自己的感受永远优先。</p>
</section>"""


def _section_calendar(model: dict) -> str:
    """月报/年报的训练日历。周报时间跨度太短，画日历没意义。"""
    if model["kind"] == "weekly":
        return ""
    sessions = model["sessions"]
    if not sessions:
        return ""
    p = model["period"]
    day_values: dict[str, float] = {}
    for s in sessions:
        # 有容量用容量，自重训练没容量就用组数兜底，免得整天显示成没练
        v = s["volume_kg"] or (s["sets_done"] * 100)
        day_values[s["date"]] = day_values.get(s["date"], 0) + v

    trained = len(day_values)
    days = p["days"]
    return f"""<section class="card">
<h2>训练日历</h2>
<div class="scroll-x">{calendar_heatmap(day_values, p["start"], p["end"])}</div>
<p class="muted small">颜色深浅表示当天的训练容量。
{p["days"]} 天里有 {trained} 天训练，约每周 {trained / max(days / 7, 1):.1f} 次。</p>
</section>"""


def _section_bodymap(model: dict) -> str:
    groups = {g: v["sets"] for g, v in model["groups"].items()}
    if not groups:
        return ""
    return (f'<section class="card"><h2>本期肌群覆盖</h2>'
            f'{body_heatmap(groups)}</section>')


def _section_body(model: dict) -> str:
    b = model["body"]
    if not b["trend"]:
        return ('<section class="card"><h2>体重</h2>'
                '<p class="muted">本期没有体重记录。</p></section>')

    trend = b["trend"]
    labels = [d["date"][5:] for d in trend]
    trend_pts = [(i, d["kg"]) for i, d in enumerate(trend)]

    raw_by_date = {d["date"]: d["kg"] for d in b["raw"]}
    raw_pts = [(i, raw_by_date[d["date"]]) for i, d in enumerate(trend)
               if d["date"] in raw_by_date]

    series = [
        Series("每日读数", raw_pts, css="s7", show_dots=True, dashed=True),
        Series("7 日均线", trend_pts, css="s1", show_dots=False),
    ]
    chart = line_chart(series, labels, height=240, y_unit=" kg",
                       title="体重趋势（7 日移动均线）")

    change = b.get("change_kg")
    rate = b.get("rate_pct_per_week")
    bits = []
    if change is not None:
        bits.append(f"本期变化 {change:+.2f} kg")
    if rate is not None:
        bits.append(f"约 {rate:+.2f}%/周")
    summary = "，".join(bits)

    caution = ""
    if rate is not None and rate <= -1.5:
        caution = ('<p class="muted small">当前下降速度偏快。减脂期速度过快时，'
                   '流失的瘦体重比例会上升，力量也更难维持。'
                   '如果这不是刻意为之，可以考虑把速度放缓一些。'
                   '涉及用药和体检指标的部分请咨询医生。</p>')

    return f"""<section class="card">
<h2>体重</h2>
{chart}
<div class="legend">
<span><i style="background:var(--s1)"></i>7 日均线</span>
<span><i style="background:var(--s7)"></i>每日读数</span></div>
<p class="small">{esc(summary)}</p>
<p class="muted small">{esc(b["note"])}</p>
{caution}
</section>"""


def _section_comparison(model: dict) -> str:
    comps = model.get("comparisons") or []
    if not comps:
        return ""
    blocks = []
    for c in comps:
        head = (f'<h3>「{esc(c["group"])}」本次 vs 上次</h3>'
                f'<p class="muted small">{esc(c["anchor_reason"])}</p>')
        if not c["anchor_date"]:
            blocks.append(head)
            continue

        cells = [
            ("有效组数", c["sets"]["text"]),
            ("总容量", c["volume"]["text"]),
        ]
        if c["loads_comparable"]:
            cells.append(("顶组负荷（仅共同动作）", c["top_load"]["text"]))
            cells.append(("最强估算 1RM（仅共同动作）", c["best_e1rm"]["text"]))
        else:
            cells.append(("顶组负荷", "两次没有共同动作，负荷不可比"))

        kpi_html = "".join(
            f'<div class="kpi"><div class="k">{esc(k)}</div>'
            f'<div class="v" style="font-size:15px">{esc(v)}</div></div>'
            for k, v in cells)

        paired = [m for m in c["movements"] if m["status"] == "paired"]
        table = ""
        if paired:
            rows = "".join(
                f'<tr><td>{esc(m["name"])}</td>'
                f'<td class="num">{esc(m["top_load"]["text"])}</td>'
                f'<td class="num">{esc(m["reps"]["text"])}</td>'
                f'<td class="num">{esc(m["e1rm"]["text"])}</td></tr>'
                for m in paired)
            table = (f'<div class="scroll-x"><table><thead><tr><th>共同动作</th>'
                     f'<th class="num">顶组</th><th class="num">总次数</th>'
                     f'<th class="num">估算 1RM</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table></div>')

        extra = []
        if c["added"]:
            extra.append(f'本次新增：{esc("、".join(c["added"]))}')
        if c["dropped"]:
            extra.append(f'本次没做：{esc("、".join(c["dropped"]))}')
        extra_html = (f'<p class="muted small">{" ｜ ".join(extra)}</p>'
                      if extra else "")

        blocks.append(head + f'<div class="kpis">{kpi_html}</div>'
                      + table + extra_html)

    return f'<section class="card"><h2>同部位对比</h2>{"".join(blocks)}</section>'


def _section_findings(model: dict) -> str:
    f = model["findings"]
    if not any(f.values()):
        return ""
    parts = []
    for label, key, css in (("做得好的", "优点", "f-good"),
                            ("可以更好的", "缺点", "f-bad"),
                            ("下一步", "改进点", "f-act"),
                            ("说明", "信息", "f-info")):
        if f.get(key):
            parts.append(f"<h3>{label}</h3>{_findings_list(f[key], css)}")
    return f'<section class="card"><h2>本期小结</h2>{"".join(parts)}</section>'


def _section_prescriptions(model: dict) -> str:
    rxs = model.get("prescriptions") or []
    if not rxs:
        return ""
    blocks = []
    for rx in rxs:
        notes = "".join(f'<p class="small">{rich(n)}</p>' for n in rx.get("notes", []))
        rows = "".join(
            f'<tr><td>{esc(p["name"])}</td><td class="num">{p["sets"]}</td>'
            f'<td class="num">{fmt_num(p["load_kg"]) + " kg" if p["load_kg"] else "—"}</td>'
            f'<td class="num">{esc(p["rep_target"])}</td>'
            f'<td>{esc(p["change"])}</td></tr>'
            for p in rx["movements"])
        why = "".join(f'<li class="f-info">{esc(p["name"])}：{rich(p["why"])}</li>'
                      for p in rx["movements"])
        blocks.append(
            f'<h3>下次「{esc(rx["group"])}」</h3>{notes}'
            f'<div class="scroll-x"><table><thead><tr><th>动作</th>'
            f'<th class="num">组</th><th class="num">重量</th>'
            f'<th class="num">目标次数</th><th>调整</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
            f'<ul class="findings">{why}</ul>')
    return f'<section class="card"><h2>下次训练建议</h2>{"".join(blocks)}</section>'


def _section_quality(model: dict) -> str:
    q = model["data_quality"]
    items = []
    cov = q.get("coverage_pct")
    if q.get("sync_applicable") and cov is not None and cov < 100:
        items.append(f'本期 {q["days_in_period"]} 天里，已同步 {q["days_synced"]} 天'
                     f'（{cov:.0f}%）。未同步的日期不会出现在统计里 —— '
                     f'跑 <code>hc sync</code> 补齐。')
    if q["rpe_coverage"] < 0.3:
        items.append(f'RPE 覆盖率 {q["rpe_coverage"] * 100:.0f}%。'
                     f'数据不足，所有依赖主观强度的判断本期都没有输出。')
    if q["unclassified_movements"]:
        items.append(f'有 {len(q["unclassified_movements"])} 个动作未能归类：'
                     f'{esc("、".join(q["unclassified_movements"]))}。')
    if q["volume_incomplete"]:
        items.append('部分自重动作因缺少当日体重数据，未计入总吨位。')
    if q["meals_logged"] == 0:
        items.append('本期没有饮食记录，营养部分暂时无法分析。')
    if not items:
        return ""
    lis = "".join(f'<li class="f-info">{i}</li>' for i in items)
    return (f'<section class="card"><h2>数据说明</h2>'
            f'<ul class="findings">{lis}</ul>'
            f'<p class="muted small">把盲区说清楚，是为了让上面的结论可以被放心使用。</p>'
            f'</section>')


def render(model: dict) -> str:
    k = model["kpis"]
    p = model["period"]

    vol_sub, vol_tone = _delta_sub(k["volume_kg"], k["volume_kg_prev"], " kg")
    set_sub, set_tone = _delta_sub(k["sets"], k["sets_prev"], " 组")
    ses_sub, _ = _delta_sub(k["sessions"], k["sessions_prev"], " 次")

    kpis = "".join([
        _kpi("训练次数", str(k["sessions"]), ses_sub),
        _kpi("总容量", f'{fmt_num(k["volume_kg"])} kg', vol_sub, vol_tone),
        _kpi("有效组数", str(k["sets"]), set_sub, set_tone),
        _kpi("训练时长", f'{fmt_num(k["duration_min"])} 分钟'
                     if k.get("duration_min") else "未记录"),
    ] + ([_kpi("消耗", f'{fmt_num(k["kcal"])} kcal')] if k.get("kcal") else []))

    has_narrative = bool(model.get("narrative"))
    data_only = "" if has_narrative else (
        '<div class="data-only no-print">纯数据模式：本报告没有接入模型，'
        '所以没有教练的文字讲解。上面的数据、图表、对比和建议都是完整的 —— '
        '它们由确定性脚本算出，不依赖任何模型。'
        '想要文字讲解的话，在 Claude Code / Codex 之类的宿主里让助手读一下 '
        '<code>.facts.json</code> 再生成即可。</div>')

    body = "".join([
        f'<header class="report-head">'
        f'<p class="eyebrow">{esc({"weekly": "周报", "monthly": "月报", "yearly": "年报"}.get(model["kind"], "报告"))}</p>'
        f'<h1>{esc(p["label"])}</h1>'
        f'<p class="muted small">{esc(p["start"])} ~ {esc(p["end"])}'
        f' · 生成于 {esc(model["generated_at"][:16].replace("T", " "))}</p></header>',
        _narrative(model, "opening"),
        data_only,
        f'<div class="kpis">{kpis}</div>',
        _narrative(model, "training"),
        _section_sessions(model),
        _section_calendar(model),
        _section_groups(model),
        _section_bodymap(model),
        _section_comparison(model),
        _section_findings(model),
        _section_prescriptions(model),
        _narrative(model, "body"),
        _section_body(model),
        _narrative(model, "nutrition"),
        _section_quality(model),
        _narrative(model, "closing"),
        '<footer class="disclaimer">'
        '<p><strong>免责声明</strong>　本报告由个人健康助手自动生成，'
        '提供的是一般性健身与营养信息，仅供参考。'
        '它不构成医疗建议，不诊断或治疗任何疾病，也不能替代医生。</p>'
        '<p>涉及用药调整、既往病史、体检异常指标，或出现胸痛、晕厥、'
        '持续呕吐、剧烈腹痛、关节红肿热痛、血尿等症状时，请及时就医。</p>'
        '<p class="small">所有估算 1RM 均为公式推算（Epley），不是实测值；'
        '训练量参考区间为人群统计值，非个人处方。</p></footer>',
    ])

    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(p["label"])} · 健康报告</title>'
            f'<style>{CSS}</style></head><body>'
            f'<div class="wrap">{body}</div></body></html>')
