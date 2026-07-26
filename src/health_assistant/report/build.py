"""把本地数据算成一份 ReportModel（也就是 facts.json）。

这是整套设计里最关键的解耦点：

    确定性引擎  →  facts.json  →  任意模型写叙述  →  注入 HTML

facts.json 里全是算好的事实和结论，模型只负责措辞。所以：
  · 没接模型时，报告依然完整可用（数据、图表、对比、处方全在）
  · 接了任何模型，数字都完全一致，只有文风不同

模型被明确要求：不得引入 metrics 里没有的数字。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, is_dataclass

from .. import store
from ..analytics.compare import compare_session
from ..analytics.findings import evaluate, split
from ..analytics.metrics import rolling_weight, session_stats, weight_at
from ..analytics.prescribe import (prescribe_group, volume_status,
                                   weight_trend_pct_per_week)
from ..taxonomy import sort_groups

SCHEMA = "ha.report/1"

NARRATIVE_SLOTS = ["opening", "training", "body", "nutrition", "closing"]


def week_bounds(anchor: dt.date) -> tuple[dt.date, dt.date]:
    start = anchor - dt.timedelta(days=anchor.weekday())
    return start, start + dt.timedelta(days=6)


def month_bounds(anchor: dt.date) -> tuple[dt.date, dt.date]:
    start = anchor.replace(day=1)
    nxt = (start + dt.timedelta(days=32)).replace(day=1)
    return start, nxt - dt.timedelta(days=1)


def year_bounds(anchor: dt.date) -> tuple[dt.date, dt.date]:
    return dt.date(anchor.year, 1, 1), dt.date(anchor.year, 12, 31)


def _d(obj):
    """dataclass → dict，递归。"""
    if is_dataclass(obj):
        return {k: _d(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _d(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_d(v) for v in obj]
    return obj


def _delta(d, unit: str = "") -> dict:
    # text 里就把单位放好，渲染层不要再拼 —— 否则会出现
    # 「3000 → 4300 ↑ +43% kg」这种单位跑到百分比后面的写法。
    return {"before": d.before, "after": d.after, "abs": d.abs_change,
            "pct": d.pct_change, "direction": d.direction,
            "unit": unit, "text": d.fmt(unit)}


def build(kind: str, start: dt.date, end: dt.date) -> dict:
    """kind ∈ weekly | monthly | yearly"""
    all_sessions = store.load_sessions()
    body = store.load_body()
    meals = store.load_meals(start=start.isoformat(), end=end.isoformat())
    trend = rolling_weight(body)

    stats_all = [session_stats(s, weight_at(body, s["date"])) for s in all_sessions]
    period = [s for s in stats_all if start.isoformat() <= s.date <= end.isoformat()]

    # ── 上一期，用于同比 ──
    span = (end - start).days + 1
    prev_start, prev_end = start - dt.timedelta(days=span), start - dt.timedelta(days=1)
    prev = [s for s in stats_all
            if prev_start.isoformat() <= s.date <= prev_end.isoformat()]

    def total_vol(items):
        vals = [s.volume_kg for s in items if s.volume_kg is not None]
        return sum(vals) if vals else None

    # ── 肌群容量 ──
    group_sets: dict[str, float] = {}
    group_vol: dict[str, float] = {}
    for s in period:
        for ms in s.movements:
            # 拉伸不计入训练容量；一组都没完成的动作不该让某个部位凭空出现在统计里
            # （7/12 那次的「绳索直臂下压」计划了但没打勾，否则会让「背」显示 0 组）
            if ms.group == "拉伸" or ms.sets_done == 0:
                continue
            group_sets[ms.group] = group_sets.get(ms.group, 0) + ms.sets_done
            if ms.volume_kg:
                group_vol[ms.group] = group_vol.get(ms.group, 0) + ms.volume_kg

    weeks = max(span / 7.0, 1.0)
    groups_block = {}
    for g in sort_groups(group_sets):
        per_week = group_sets[g] / weeks
        status, lm = volume_status(g, per_week)
        groups_block[g] = {
            "sets": group_sets[g],
            "sets_per_week": round(per_week, 1),
            "volume_kg": round(group_vol.get(g, 0), 1) or None,
            "status": status,
            "landmarks": lm,
        }

    # ── 同部位对比：对本期最后一次训练做 ──
    comparisons, prescriptions = [], []
    if period:
        latest = period[-1]
        history = [s for s in stats_all if s.id != latest.id]
        for cmp in compare_session(latest, history):
            comparisons.append({
                "group": cmp.group,
                "current_date": cmp.current_date,
                "anchor_date": cmp.anchor_date,
                "anchor_reason": cmp.anchor_reason,
                "days_between": cmp.days_between,
                "loads_comparable": cmp.loads_comparable,
                "paired_count": cmp.paired_count,
                "sets": _delta(cmp.sets, " 组"),
                "volume": _delta(cmp.volume, " kg"),
                "top_load": _delta(cmp.top_load, " kg"),
                "best_e1rm": _delta(cmp.best_e1rm, " kg"),
                "movements": [{
                    "name": md.name, "status": md.status,
                    "sets": _delta(md.sets, " 组"), "reps": _delta(md.reps, " 次"),
                    "top_load": _delta(md.top_load, " kg"), "volume": _delta(md.volume, " kg"),
                    "e1rm": _delta(md.e1rm, " kg"),
                } for md in cmp.movements],
                "added": cmp.added, "dropped": cmp.dropped,
            })

        # 处方：对本期练过的每个主要肌群
        cmp_by_group = {c.group: c for c in compare_session(latest, history)}
        for g in sort_groups(group_sets):
            if group_sets[g] < 2:
                continue
            src = next((s for s in reversed(period) if s.groups.get(g, 0) >= 2), None)
            if src is None:
                continue
            rx = prescribe_group(g, src, cmp_by_group.get(g),
                                 weekly_sets=group_sets[g] / weeks, body_trend=trend)
            if rx.has_content:
                prescriptions.append(_d(rx))

    # ── 结论 ──
    findings_raw = []
    if period:
        latest = period[-1]
        history = [s for s in stats_all if s.id != latest.id]
        findings_raw = evaluate(latest, compare_session(latest, history))
    buckets = split(findings_raw)
    findings_block = {k: [_d(f) for f in v] for k, v in buckets.items()}

    # ── 体重 ──
    raw_w = [(r["date"], r["value"]) for r in body
             if r["type"] == "weight" and start.isoformat() <= r["date"] <= end.isoformat()]
    trend_in = [(d, v) for d, v in trend if start.isoformat() <= d <= end.isoformat()]
    rate = weight_trend_pct_per_week(trend, days=max(span, 14))
    body_block = {
        "raw": [{"date": d, "kg": v} for d, v in raw_w],
        "trend": [{"date": d, "kg": round(v, 2)} for d, v in trend_in],
        "latest_trend_kg": round(trend[-1][1], 2) if trend else None,
        "change_kg": (round(trend_in[-1][1] - trend_in[0][1], 2)
                      if len(trend_in) >= 2 else None),
        "rate_pct_per_week": round(rate, 2) if rate is not None else None,
        "note": ("体重用 7 日移动均线。日间波动可达 ±1.8kg，看单日读数会得出错误结论。"),
    }

    # ── 数据质量：报告必须对自己的盲区诚实 ──
    total_sets = sum(s.sets_done for s in period)
    rpe_sets = sum(len(ms.rpes) for s in period for ms in s.movements)
    fetched_days, empty_days = 0, 0
    for year in {start.year, end.year}:
        for date, entry in store.load_index(year).items():
            if start.isoformat() <= date <= end.isoformat():
                fetched_days += 1
                if entry.get("status") == "empty":
                    empty_days += 1
    unknown = sorted({ms.name for s in period for ms in s.movements
                      if ms.group == "未分类"})

    quality = {
        "days_in_period": span,
        "days_synced": fetched_days,
        "coverage_pct": round(fetched_days / span * 100, 1) if span else 0,
        "rpe_coverage": round(rpe_sets / total_sets, 3) if total_sets else 0,
        "unclassified_movements": unknown,
        "volume_incomplete": any(s.volume_incomplete for s in period),
        "meals_logged": len(meals),
    }

    return {
        "schema": SCHEMA,
        "kind": kind,
        "generated_at": dt.datetime.now().astimezone().replace(
            microsecond=0).isoformat(),
        "period": {
            "start": start.isoformat(), "end": end.isoformat(),
            "label": _period_label(kind, start, end), "days": span,
        },
        "kpis": {
            "sessions": len(period),
            "sessions_prev": len(prev),
            "volume_kg": round(total_vol(period) or 0, 1),
            "volume_kg_prev": round(total_vol(prev) or 0, 1),
            "sets": total_sets,
            "sets_prev": sum(s.sets_done for s in prev),
            "duration_min": round(sum(s.duration_min or 0 for s in period), 0),
            "kcal": round(sum(s.kcal or 0 for s in period), 0) or None,
        },
        "sessions": [{
            "date": s.date, "label": s.label, "title": s.title,
            "duration_min": round(s.duration_min, 0) if s.duration_min else None,
            "volume_kg": round(s.volume_kg, 1) if s.volume_kg is not None else None,
            "sets_done": s.sets_done, "sets_planned": s.sets_planned,
            "kcal": s.kcal, "groups": s.groups, "source": s.source,
            "movements": [{
                "name": ms.name, "group": ms.group, "group_source": ms.group_source,
                "sets": ms.sets_done, "reps": ms.reps_total,
                "top_load_kg": round(ms.top_load_kg, 1) if ms.top_load_kg else None,
                "volume_kg": round(ms.volume_kg, 1) if ms.volume_kg is not None else None,
                "e1rm": round(ms.best_e1rm, 1) if ms.best_e1rm else None,
                "e1rm_method": ms.e1rm_method,
                "bodyweight": ms.bodyweight, "assisted": ms.assisted,
                "unilateral": ms.unilateral,
                "imbalance_pct": round(ms.imbalance_pct, 1) if ms.imbalance_pct else None,
            } for ms in s.movements],
        } for s in period],
        "groups": groups_block,
        "comparisons": comparisons,
        "prescriptions": prescriptions,
        "findings": findings_block,
        "body": body_block,
        "nutrition": {
            "meals": len(meals),
            "note": ("本期没有饮食记录。可以直接口述今天吃了什么，助手会帮你记下来。"
                     if not meals else None),
        },
        "data_quality": quality,
        "narrative": {},          # 由模型填，见 NARRATIVE_SLOTS
        "narrative_slots": NARRATIVE_SLOTS,
        "instructions_for_model": (
            "你拿到的是已经算好的事实。请只用这里出现过的数字，"
            "不要引入任何新数字，也不要发明新的结论。"
            "你的任务是把 findings 里的内容用教练的口吻讲出来："
            "先肯定做到的，再指出可以更好的地方，最后给出具体的下一步。"
            "语气谦和、专业、鼓励，不夸张、不说教。"
        ),
    }


def _period_label(kind: str, start: dt.date, end: dt.date) -> str:
    if kind == "weekly":
        y, w, _ = start.isocalendar()
        return f"{y} 年第 {w} 周"
    if kind == "monthly":
        return f"{start.year} 年 {start.month} 月"
    if kind == "yearly":
        return f"{start.year} 年"
    return f"{start} ~ {end}"


def slug(kind: str, start: dt.date) -> str:
    if kind == "weekly":
        y, w, _ = start.isocalendar()
        return f"{y}-W{w:02d}"
    if kind == "monthly":
        return f"{start.year}-{start.month:02d}"
    return str(start.year)
