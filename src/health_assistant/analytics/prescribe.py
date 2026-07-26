"""下次同部位训练的处方。

基础规则是**双重递进**（先加次数，达到区间上限再加重量），
外面套一层护栏。护栏按顺序生效，每条都会记下 rationale，因为
「为什么这次不让我加重」比「加多少」更需要解释清楚。

其中 DEFICIT_HOLD 是最要紧的一条：处在明显减重期时，力量能维持住就是成功。
一个在减脂的人每周被工具催着加重，既不符合生理事实，也很打击人。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..config import KNOWLEDGE_DIR
from .compare import GroupComparison
from .metrics import MovementStats, SessionStats

LANDMARKS_PATH = KNOWLEDGE_DIR / "training-landmarks.json"

# 体重周降幅超过这个百分比，就认为处在明显缺口期，策略转为保强度
FAST_LOSS_PCT_PER_WEEK = 1.0


@lru_cache(maxsize=1)
def _config() -> dict:
    try:
        return json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def landmarks(group: str) -> dict | None:
    return _config().get("landmarks", {}).get(group)


def equipment_of(name: str) -> str:
    for equip, pattern in _config().get("equipment_patterns", {}).items():
        if re.search(pattern, name):
            return equip
    return "default"


def increment_for(name: str) -> float:
    return _config().get("increments", {}).get(equipment_of(name),
                                               _config().get("increments", {}).get("default", 2.5))


def rep_range(name: str) -> tuple[int, int]:
    cfg = _config()
    ranges = cfg.get("rep_ranges", {})
    if re.search(cfg.get("compound_patterns", "$^"), name):
        lo, hi = ranges.get("compound", [5, 8])
    elif re.search("卷腹|收腹|平板支撑|抬腿|核心", name):
        lo, hi = ranges.get("core", [10, 20])
    else:
        lo, hi = ranges.get("isolation", [8, 12])
    return int(lo), int(hi)


def volume_status(group: str, weekly_sets: float) -> tuple[str, dict | None]:
    lm = landmarks(group)
    if not lm:
        return "unknown", None
    if weekly_sets < lm["mev"]:
        return "under_mev", lm
    if weekly_sets > lm["mrv"]:
        return "over_mrv", lm
    lo, hi = lm["mav"]
    if weekly_sets < lo:
        return "mev_mav", lm
    if weekly_sets > hi:
        return "mav_mrv", lm
    return "in_mav", lm


@dataclass
class MovementPrescription:
    name: str
    sets: int
    load_kg: float | None
    rep_target: str
    change: str
    why: str


@dataclass
class Prescription:
    group: str
    based_on_date: str | None
    rationale: list[str] = field(default_factory=list)
    movements: list[MovementPrescription] = field(default_factory=list)
    total_sets: int = 0
    rpe_cap: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.movements)


def weight_trend_pct_per_week(body_trend: list[tuple[str, float]],
                              days: int = 14) -> float | None:
    """从体重移动均线估算周变化百分比。数据不足返回 None。"""
    if len(body_trend) < 4:
        return None
    import datetime as dt
    try:
        end_date = dt.date.fromisoformat(body_trend[-1][0])
        window = [(dt.date.fromisoformat(d), v) for d, v in body_trend
                  if (end_date - dt.date.fromisoformat(d)).days <= days]
    except ValueError:
        return None
    if len(window) < 4:
        return None
    (d0, v0), (d1, v1) = window[0], window[-1]
    span_days = (d1 - d0).days
    if span_days <= 0 or v0 <= 0:
        return None
    return ((v1 - v0) / v0) * 100.0 * (7.0 / span_days)


def prescribe_group(group: str, current: SessionStats,
                    comparison: GroupComparison | None,
                    *, weekly_sets: float | None = None,
                    body_trend: list[tuple[str, float]] | None = None,
                    ) -> Prescription:
    rx = Prescription(group=group, based_on_date=current.date)
    ms_list = [m for m in current.movements if m.group == group and m.sets_done > 0]
    if not ms_list:
        return rx

    # ── 护栏 ──────────────────────────────────────────────────────────
    hold_load = False

    trend_pct = weight_trend_pct_per_week(body_trend or [])
    if trend_pct is not None and trend_pct <= -FAST_LOSS_PCT_PER_WEEK:
        hold_load = True
        rx.rationale.append("DEFICIT_HOLD")
        rx.notes.append(
            f"你目前体重以每周 {trend_pct:.1f}% 的速度在下降，属于明显的热量缺口期。"
            f"这个阶段肌肉的恢复能力是打折的，**力量维持住就已经是成功**。"
            f"所以下面的建议以稳住负荷、保住强度为主，不追加重量 —— 等体重稳下来再冲。")

    status, lm = ("unknown", None)
    if weekly_sets is not None:
        status, lm = volume_status(group, weekly_sets)
        if status == "over_mrv" and lm:
            hold_load = True
            rx.rationale.append("OVER_MRV_HOLD")
            rx.notes.append(
                f"本周 {group} 已经练到 {weekly_sets:.0f} 组，"
                f"超过了常见的可恢复上限（约 {lm['mrv']} 组）。"
                f"先别加量也别加重，把这些组练扎实比堆更多组有用。")
        elif status == "under_mev" and lm:
            rx.rationale.append("UNDER_MEV")
            rx.notes.append(
                f"本周 {group} 只练了 {weekly_sets:.0f} 组，"
                f"低于常见的最小有效容量（约 {lm['mev']} 组）。"
                f"下次可以多加 2 组，或者本周再安排一次 {group}。")

    if current.rpe_coverage < 0.3:
        rx.notes.append(
            "顺带一提：因为这次几乎没有 RPE 记录，下面的重量建议只能靠次数推断。"
            "如果你能给每个动作的最后一组记一个 RPE，我下次能给得准得多。")

    # ── 逐动作双重递进 ────────────────────────────────────────────────
    by_name = {}
    if comparison:
        by_name = {md.name: md for md in comparison.movements}

    for m in ms_list:
        lo, hi = rep_range(m.name)
        step = increment_for(m.name)
        avg_reps = (m.reps_total / m.sets_done) if m.sets_done else 0
        load = m.top_load_kg
        if m.unilateral and load is not None:
            load = load / 2.0    # 展示单侧重量，和用户在器械上看到的一致

        # 自重类动作不显示折算出来的公斤数 —— 那不是器械上能设置的数字，
        # 显示出来只会误导。辅助类动作更要小心：配重越大越轻松，
        # 说「加重」方向是反的。
        if m.bodyweight:
            if m.assisted:
                change = "减少辅助配重" if avg_reps >= hi else "保持配重，先把次数做上去"
                why = (f"上次平均每组 {avg_reps:.0f} 次。"
                       + (f"已经到 {hi} 次了，把辅助配重调低一档（通常 5kg），"
                          f"次数会掉回 {lo} 次左右，这是对的。"
                          if avg_reps >= hi else
                          f"先在当前配重下做到 {lo} 次以上，再减配重。")
                       + "注意：辅助配重越大越省力，所以变强的方向是**往下调**。")
            else:
                change = "增加次数" if avg_reps < hi else "加负重或换更难的变式"
                why = (f"上次平均每组 {avg_reps:.0f} 次。这是自重动作，"
                       + (f"先把次数堆到 {hi} 次。"
                          if avg_reps < hi else
                          f"已经能做 {avg_reps:.0f} 次了，可以开始负重（配重带 2.5~5kg），"
                          f"或者换个更难的变式。"))
            rx.movements.append(MovementPrescription(
                name=m.name, sets=m.sets_done, load_kg=None,
                rep_target=f"{lo}-{hi}", change=change, why=why))
            continue

        if hold_load:
            rx.movements.append(MovementPrescription(
                name=m.name, sets=m.sets_done, load_kg=load,
                rep_target=f"{lo}-{hi}",
                change="保持重量",
                why=f"上次平均每组 {avg_reps:.0f} 次。当前阶段先稳住。"))
            continue

        if avg_reps >= hi:
            new_load = (load + step) if load is not None else None
            rx.movements.append(MovementPrescription(
                name=m.name, sets=m.sets_done, load_kg=new_load,
                rep_target=f"{lo}-{hi}",
                change=f"加重 +{step:g}kg" + ("（每只手）" if m.unilateral else ""),
                why=f"上次平均每组做到 {avg_reps:.0f} 次，已经到了 {hi} 次的区间上限，"
                    f"可以加重了。加重后次数会掉回 {lo} 次左右，这是正常的。"))
        elif avg_reps < lo:
            rx.movements.append(MovementPrescription(
                name=m.name, sets=m.sets_done, load_kg=load,
                rep_target=f"{lo}-{hi}",
                change="保持重量，先把次数做上去",
                why=f"上次平均每组 {avg_reps:.0f} 次，还没到 {lo} 次。"
                    f"先在这个重量上做到 {lo} 次以上，再考虑加重。"))
        else:
            rx.movements.append(MovementPrescription(
                name=m.name, sets=m.sets_done, load_kg=load,
                rep_target=f"{lo}-{hi}",
                change=f"保持重量，目标 +1 次",
                why=f"上次平均每组 {avg_reps:.0f} 次，在 {lo}-{hi} 的区间内。"
                    f"这次每组多做 1 次，累积到 {hi} 次就可以加重。"))

    # 掉队的动作提醒补回来
    if comparison:
        for name in comparison.dropped[:2]:
            rx.movements.append(MovementPrescription(
                name=name, sets=3, load_kg=None, rep_target="按上次",
                change="建议补回",
                why="上一次练这个部位时做了，这次没做。如果是有意换掉就忽略。"))

    rx.total_sets = sum(p.sets for p in rx.movements)
    return rx
