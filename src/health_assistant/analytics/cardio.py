"""有氧与心率区间分析。

训记会把苹果健康的运动记录同步过来，带心率摘要（avg/max/min）和分桶趋势。
`exetype == "cardio"` 是可靠的标记。

这个模块回答两个问题：
  1. 这次有氧练的是哪个强度档？
  2. 周维度上，有氧的量和强度配比合理吗？

**关于 Z2 的一个澄清**：常见说法是「Z2 燃脂比例最高所以适合减脂」，
这个说法本身有误导性 —— 决定减脂的是总热量缺口，不是脂肪供能占比。
Z2 真正的优势是**恢复代价低**：同样一周，能做 3 次 40 分钟 Z2，
但做不了 3 次 40 分钟 Z4。而且高强度有氧会挤占力量训练的恢复额度，
在减脂期这个冲突会直接体现在力量下滑上。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from functools import lru_cache

from ..config import DATA_DIR, KNOWLEDGE_DIR
from .metrics import SessionStats

ZONES_PATH = KNOWLEDGE_DIR / "training" / "hr-zones.json"
PROFILE_PATH = DATA_DIR / "profile.json"


@lru_cache(maxsize=1)
def _zones_config() -> dict:
    try:
        return json.loads(ZONES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _profile() -> dict:
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def age_now(today: dt.date | None = None) -> int | None:
    # 年龄的真相源是 age.py —— 这里曾经和 nutrition.py 各写一份同样的减法。
    # 档案由本模块提供，理由同 nutrition.age_years。
    from ..age import age
    return age(today, _profile())


def hr_max() -> tuple[float | None, str]:
    """返回 (最大心率, 来源说明)。实测优先于公式。"""
    override = _profile().get("hr_max_override")
    if override:
        return float(override), "实测"
    age = age_now()
    if age is None:
        return None, "缺少出生年份"
    # Tanaka：对成年人比 220−年龄 更准
    return round(208 - 0.7 * age), f"Tanaka 公式估算（208 − 0.7 × {age} 岁）"


def zone_of(bpm: float | None) -> tuple[str, float] | None:
    """心率 → (区间名, 占最大心率的比例)。"""
    if bpm is None:
        return None
    hm, _ = hr_max()
    if not hm:
        return None
    pct = bpm / hm
    for z in _zones_config().get("zones", []):
        if z["lo"] <= pct < z["hi"]:
            return z["name"], pct
    return ("Z5 最大" if pct >= 1 else "Z1 恢复"), pct


def zone_table() -> list[tuple[str, int, int, str]]:
    hm, _ = hr_max()
    if not hm:
        return []
    return [(z["name"], round(hm * z["lo"]), round(hm * min(z["hi"], 1.0)),
             z.get("use", "")) for z in _zones_config().get("zones", [])]


@dataclass
class CardioBout:
    date: str
    name: str
    minutes: float | None
    avg_hr: float | None
    max_hr: float | None
    kcal: float | None
    zone: str | None
    pct_hrmax: float | None

    @property
    def is_high_intensity(self) -> bool:
        return bool(self.pct_hrmax and self.pct_hrmax >= 0.80)


def extract_bouts(sessions: list[SessionStats], raw_sessions: list[dict],
                  *, start: str | None = None) -> list[CardioBout]:
    """从原始会话里抽出有氧记录（心率在 set 上，SessionStats 不带）。"""
    out: list[CardioBout] = []
    for s in raw_sessions:
        if start and s["date"] < start:
            continue
        for m in s.get("movements") or []:
            if m.get("exetype") != "cardio":
                continue
            for st in m.get("sets") or []:
                hr = st.get("hr") or {}
                met = st.get("metrics") or {}
                dur = st.get("time_s") or met.get("workout_s")
                if not dur and hr.get("step_s") and hr.get("values"):
                    dur = hr["step_s"] * len(hr["values"])
                avg = hr.get("avg") or met.get("avg_hr")
                z = zone_of(avg)
                out.append(CardioBout(
                    date=s["date"], name=m.get("name") or "有氧",
                    minutes=(dur / 60.0) if dur else None,
                    avg_hr=avg, max_hr=hr.get("max") or met.get("max_hr"),
                    kcal=met.get("kcal"),
                    zone=z[0] if z else None,
                    pct_hrmax=z[1] if z else None))
    out.sort(key=lambda b: b.date)
    return out


@dataclass
class CardioWeek:
    window_days: int
    total_minutes: float
    high_intensity_minutes: float
    bouts: int
    by_zone: dict[str, float] = field(default_factory=dict)
    total_kcal: float = 0.0


def summarize(bouts: list[CardioBout], as_of: str, *, window_days: int = 7) -> CardioWeek:
    try:
        end = dt.date.fromisoformat(as_of)
    except ValueError:
        end = dt.date.today()
    lo = (end - dt.timedelta(days=window_days - 1)).isoformat()
    sel = [b for b in bouts if lo <= b.date <= end.isoformat()]

    by_zone: dict[str, float] = {}
    for b in sel:
        if b.zone and b.minutes:
            by_zone[b.zone] = by_zone.get(b.zone, 0) + b.minutes
    return CardioWeek(
        window_days=window_days,
        total_minutes=sum(b.minutes or 0 for b in sel),
        high_intensity_minutes=sum(b.minutes or 0 for b in sel if b.is_high_intensity),
        bouts=len(sel), by_zone=by_zone,
        total_kcal=sum(b.kcal or 0 for b in sel))


def evaluate(week: CardioWeek, bouts: list[CardioBout]) -> list[dict]:
    """产出结构化建议。每条 {kind, text, fix}。"""
    g = _zones_config().get("guidance", {})
    # 配置里的目标是「每周」，窗口不是 7 天时要按比例缩放，
    # 否则 14 天窗口拿周目标去比会得出「量严重不足」的错误结论
    scale = week.window_days / 7.0
    lo, hi = (v * scale for v in g.get("weekly_target_minutes", [60, 150]))
    cap = g.get("high_intensity_cap_minutes", 30) * scale
    out: list[dict] = []

    if week.bouts == 0:
        return [{"kind": "info", "text": f"过去 {week.window_days} 天没有有氧记录。",
                 "fix": ""}]

    if week.total_minutes < lo:
        out.append({
            "kind": "action",
            "text": f"过去 {week.window_days} 天有氧共 {week.total_minutes:.0f} 分钟，"
                    f"低于建议区间（按每周 60–150 分钟折算到 {week.window_days} 天 = {lo:.0f}–{hi:.0f} 分钟）。",
            "fix": f"再加 {lo - week.total_minutes:.0f} 分钟低强度有氧就够了，"
                   f"通勤骑行、快走都算。"})
    elif week.total_minutes > hi:
        out.append({
            "kind": "info",
            "text": f"过去 {week.window_days} 天有氧 {week.total_minutes:.0f} 分钟，"
                    f"高于建议区间上沿（{hi:.0f} 分钟）。",
            "fix": "量本身不是问题，但要留意它是否开始影响力量训练的表现。"})

    if week.high_intensity_minutes > cap:
        share = week.high_intensity_minutes / max(week.total_minutes, 1) * 100
        out.append({
            "kind": "warn",
            "text": f"其中 {week.high_intensity_minutes:.0f} 分钟在 Z4 以上"
                    f"（占 {share:.0f}%），超过常见上限（折算 {cap:.0f} 分钟）。",
            "fix": "高强度有氧的恢复代价接近一次力量训练。减脂期恢复能力本来就打折，"
                   "把大部分有氧降到 Z2（心率带在有氧基础区间），"
                   "总时长反而能拉长，周热量缺口更大，也不会拖累力量。"})

    z2 = sum(v for k, v in week.by_zone.items() if k.startswith("Z2"))
    z2_share = z2 / max(week.total_minutes, 1)
    if week.total_minutes >= 20 and z2_share < 0.4:
        out.append({
            "kind": "action",
            "text": f"Z2（有氧基础区）只占 {z2_share * 100:.0f}%，"
                    f"有氧强度整体偏高。",
            "fix": "把主力有氧的配速降下来，让平均心率落在有氧基础区间，"
                   "单次拉到 25–40 分钟。判断标准很简单："
                   "**还能正常说完整句子，但唱歌费劲**。"})

    return out
