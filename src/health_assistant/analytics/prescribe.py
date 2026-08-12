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

LANDMARKS_PATH = KNOWLEDGE_DIR / "training" / "training-landmarks.json"

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


FREE_WEIGHT_EQUIPMENT = ("barbell", "dumbbell")


def is_compound(name: str) -> bool:
    """复合动作判定。

    两条规则：
      1. 深蹲/硬拉/卧推/推举/引体 —— 什么器械都算复合
      2. 划船/臀冲/上凳这类 —— **只在自由重量上**算复合

    第二条不能省。用户的口径是「自由重量复合 6-10，器械/绳索/孤立 8-12」，
    而「划船」器械和自由重量都在用：杠铃划船是自由重量复合、悍马机划船是器械。
    只把「划船」从 compound_patterns 里删掉会让杠铃划船掉进 8-12 ——
    2026-08-10 就这么错过一次，是 code review 抓出来的。
    """
    cfg = _config()
    if re.search(cfg.get("compound_patterns", "$^"), name):
        return True
    return bool(re.search(cfg.get("free_weight_compound_patterns", "$^"), name)
                and equipment_of(name) in FREE_WEIGHT_EQUIPMENT)


def rep_range(name: str) -> tuple[int, int]:
    cfg = _config()
    ranges = cfg.get("rep_ranges", {})
    if is_compound(name):
        lo, hi = ranges.get("compound", [6, 10])
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
    # 「建议补回」的动作是**提示**，不是处方的一部分。它的组数不计进 total_sets ——
    # 见 prescribe_group 末尾那段注释，这是 2026-08-10 修掉的棘轮。
    optional: bool = False


@dataclass
class Prescription:
    group: str
    based_on_date: str | None
    rationale: list[str] = field(default_factory=list)
    movements: list[MovementPrescription] = field(default_factory=list)
    total_sets: int = 0          # 只数处方里的组，不含「可选：补回」
    optional_sets: int = 0       # 可选提示的组数，单独报，别混进合计
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
                    window_label: str = "最近 7 天",
                    body_trend: list[tuple[str, float]] | None = None,
                    ) -> Prescription:
    """`weekly_sets` 是拿来和周容量地标比的组数，口径由调用方决定：

    - `hc next` 传**滚动 7 天**的实际组数（恢复状态与日历怎么切无关）
    - 周报传**本期周均**（它本来就在按周聚合）

    `window_label` 只影响 note 的措辞，不影响任何判断 —— 但它必须传对，
    否则输出里会出现「本周练了 12 组」而那个 12 其实是周均。
    """
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
                f"{window_label} {group} 已经练到 {weekly_sets:.0f} 组，"
                f"超过了常见的可恢复上限（约 {lm['mrv']} 组/周）。"
                f"先别加量也别加重，把这些组练扎实比堆更多组有用。")
        elif status == "under_mev" and lm:
            rx.rationale.append("UNDER_MEV")
            rx.notes.append(
                f"{window_label} {group} 只练了 {weekly_sets:.0f} 组，"
                f"低于常见的最小有效容量（约 {lm['mev']} 组/周）。"
                f"下次可以多加 2 组，或者这周再安排一次 {group}。")

    if not current.has_intensity_signal:
        rx.notes.append(
            "顺带一提：因为这次几乎没有强度标注，下面的重量建议只能靠次数推断。"
            "在训记里给每个动作点一下难度（简单/正常/困难），我下次能给得准得多。")

    # ── 逐动作双重递进 ────────────────────────────────────────────────
    by_name = {}
    if comparison:
        by_name = {md.name: md for md in comparison.movements}

    for m in ms_list:
        lo, hi = rep_range(m.name)
        step = increment_for(m.name)
        avg_reps = (m.reps_total / m.sets_done) if m.sets_done else 0
        # top_load 本身已经是**单侧/单器械**口径（和用户在器械上看到的一致），
        # 这里不再折算。此前这里有一句 `load / 2.0` —— 那是在给
        # 「顶组用双侧合计、1RM 用单侧」这个矛盾打补丁。矛盾在
        # metrics.per_side_load_kg 里修掉了，补丁必须同时撤掉，否则会除两次。
        load = m.top_load_kg

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

        # 难度标注是用户自己的主观强度反馈，优先于纯次数规则：
        # 标「困难」还硬加重是最容易受伤的路径；标「简单」则不必再等满一轮。
        if m.difficulty == "困难" and avg_reps >= hi:
            rx.movements.append(MovementPrescription(
                name=m.name, sets=m.sets_done, load_kg=load,
                rep_target=f"{lo}-{hi}",
                change="保持重量（你标了困难）",
                why=f"次数上看已经到 {avg_reps:.0f} 次、够加重了，"
                    f"但你把这个动作标成了「困难」。以你的主观感受为准 —— "
                    f"先在这个重量上再练一次，标到「正常」了再加 {step:g}kg。"))
            continue

        if avg_reps >= hi or (m.difficulty == "简单" and avg_reps >= lo):
            new_load = (load + step) if load is not None else None
            by_easy = m.difficulty == "简单" and avg_reps < hi
            rx.movements.append(MovementPrescription(
                name=m.name, sets=m.sets_done, load_kg=new_load,
                rep_target=f"{lo}-{hi}",
                change=f"加重 +{step:g}kg" + ("（每只手）" if m.unilateral else ""),
                why=(f"上次平均每组 {avg_reps:.0f} 次，还没到 {hi} 次，"
                     f"但你把这个动作标成了「简单」—— 不用再磨一轮次数，直接加重。"
                     if by_easy else
                     f"上次平均每组做到 {avg_reps:.0f} 次，已经到了 {hi} 次的区间上限，"
                     f"可以加重了。加重后次数会掉回 {lo} 次左右，这是正常的。")))
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

    # ── 掉队的动作：提示，不是加量 ──────────────────────────────────────
    #
    # 这一段曾经是个**棘轮**：换个器械就被判成「掉队」，下次按 3 组要求补回，
    # 而那 3 组还计进 total_sets。于是建议量只增不减 —— 拿 27 次历史背日
    # 逐一重跑，建议组数 27/27 次 ≥ 实做组数，其中 17 次严格高出，
    # 超出的部分**全部**来自这里（每次 +3 或 +6 组）。
    #
    # 修法不是删掉它 —— 「上次做了这次没做」确实值得提一句，可能是忘了。
    # 修的是它的身份：标 optional，不计进 total_sets。
    # 想练就练，不练也不欠着。
    if comparison:
        for name in comparison.dropped[:2]:
            rx.movements.append(MovementPrescription(
                name=name, sets=3, load_kg=None, rep_target="按上次",
                change="可选：补回",
                why="上一次练这个部位时做了，这次没做。如果是有意换掉就忽略 ——"
                    "这一条不计进合计组数。",
                optional=True))

    rx.total_sets = sum(p.sets for p in rx.movements if not p.optional)
    rx.optional_sets = sum(p.sets for p in rx.movements if p.optional)
    return rx
