"""单次训练的指标计算。纯函数、确定性。

这里所有数字都不依赖模型。换任何模型跑，出来的吨位、组数、估算 1RM 完全一致。
模型只负责在报告里把这些数字讲得好听。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from functools import lru_cache

from ..config import KNOWLEDGE_DIR
from ..taxonomy import Classification, classify_movement

BW_FACTORS_PATH = KNOWLEDGE_DIR / "training" / "bodyweight-factors.json"

# Epley 公式在高次数时会明显高估。超过这个次数只作低置信度参考，不参与 PR 判定。
E1RM_MAX_REPS = 12


@lru_cache(maxsize=1)
def _bw_factors() -> tuple[dict[str, float], float]:
    try:
        d = json.loads(BW_FACTORS_PATH.read_text(encoding="utf-8"))
        return d.get("factors", {}), float(d.get("default", 0.65))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}, 0.65


def bodyweight_factor(name: str) -> float:
    factors, default = _bw_factors()
    if name in factors:
        return factors[name]
    # 名字里含有已知动作的，用最长匹配
    best, best_len = default, 0
    for key, val in factors.items():
        if key in name and len(key) > best_len:
            best, best_len = val, len(key)
    return best


# 训记 exetype 的实测取值：
#   ""            常规负重
#   "times"       纯次数自重
#   "plus_weight" 自重 + 额外负重
#   "help"        辅助器械 —— 记录的重量是**配重助力**，越大越省力
BODYWEIGHT_EXETYPES = ("times", "plus_weight", "help")


def is_assisted(movement: dict) -> bool:
    return movement.get("exetype") == "help"


# 训记的动作级难度标签（简单/正常/困难）。这是**三档主观标注，不是 RPE**：
# 一个动作一个标，不是每组一个，而且档之间的间距没有定义。
# 所以不做数值映射 —— 把三个档位换算成 RPE 小数会让它看起来比实际精确。
DIFFICULTY_LABELS = {"easy": "简单", "normal": "正常", "hard": "困难"}


def difficulty_label(movement: dict) -> str | None:
    return DIFFICULTY_LABELS.get((movement.get("difficulty") or "").strip().lower())


def is_timed(movement: dict) -> bool:
    """计时类动作（平板支撑、静态保持）。成绩是秒数，不是次数。"""
    return movement.get("exetype") == "record"


def set_done(s: dict, movement: dict) -> bool:
    """这一组算不算做了。

    计时类动作在训记里**不需要打勾** —— app 记下时长就代表做过，`done` 恒为 false。
    只认 `done` 会把整个平板支撑判成「一组没做」，秒数也跟着被丢掉。
    """
    if s.get("done"):
        return True
    return is_timed(movement) and bool(s.get("time_s"))


def is_bodyweight(movement: dict, s: dict) -> bool:
    return bool(s.get("self_weight")) or movement.get("exetype") in BODYWEIGHT_EXETYPES


def set_load_kg(s: dict, movement: dict, bodyweight_kg: float | None,
                *, per_side: bool = False) -> float | None:
    """一组的等效负荷（kg）。返回 None 表示「无法计算」，绝不返回 0 兜底。

    per_side=True 时，单侧动作只返回单侧重量。算容量要用双侧合计，
    算估算 1RM 必须用单侧 —— 两者混用会让单侧动作的 1RM 虚高一倍。
    """
    w = s.get("weight_kg")

    if is_assisted(movement):
        # 辅助器械：记录的是助力配重，方向和负重相反。
        # 真实负荷 = 体重折算 − 助力。把助力当成负荷加进去会让方向完全反过来：
        # 助力调得越大（越轻松）反而显示容量越高。
        if bodyweight_kg is None:
            return None
        base = bodyweight_kg * bodyweight_factor(movement.get("name", ""))
        return max(base - (w or 0.0), 0.0)

    if is_bodyweight(movement, s):
        if bodyweight_kg is None:
            return None
        base = bodyweight_kg * bodyweight_factor(movement.get("name", ""))
        return base + (w or 0.0)

    if w is None:
        return None
    if movement.get("unilateral") and not per_side:
        # 单侧动作：weight 是单侧重量，左右各做一遍。
        # 用真实的左右重量相加而不是简单 ×2 —— 顺带就得到了不平衡度。
        left = s.get("left_weight_kg")
        return w + (left if left is not None else w)
    return w


def set_volume_kg(s: dict, movement: dict, bodyweight_kg: float | None) -> float | None:
    if not set_done(s, movement):
        return None
    reps = s.get("reps")
    if not reps:
        return None
    load = set_load_kg(s, movement, bodyweight_kg)
    if load is None:
        return None
    return load * reps


def e1rm(weight_kg: float | None, reps: float | None) -> tuple[float | None, str]:
    """估算 1RM。用 Epley：w × (1 + reps/30)。

    返回 (值, 方法标签)。方法标签会在报告里展示，因为「估算」和「实测」
    必须让用户一眼分得清。
    """
    if weight_kg is None or not reps or reps <= 0:
        return None, "n/a"
    if reps == 1:
        return weight_kg, "实测"
    value = weight_kg * (1 + reps / 30.0)
    if reps > E1RM_MAX_REPS:
        return value, "Epley（高次数，低置信度）"
    return value, "Epley"


@dataclass
class MovementStats:
    name: str
    group: str
    group_source: str
    unilateral: bool
    exetype: str | None
    sets_done: int
    sets_planned: int
    reps_total: float
    volume_kg: float | None
    top_load_kg: float | None
    best_e1rm: float | None
    e1rm_method: str
    rpes: list[float] = field(default_factory=list)
    imbalance_pct: float | None = None
    timed: bool = False               # 计时类动作，成绩看秒数不看吨位
    time_s_total: float | None = None
    best_time_s: float | None = None
    difficulty: str | None = None     # 训记动作级难度：简单/正常/困难。不是 RPE
    volume_incomplete: bool = False   # 有组因缺体重数据算不出容量
    bodyweight: bool = False          # 自重类动作，重量数字是折算出来的
    assisted: bool = False            # exetype=help，记录的是助力配重，越大越轻松
    # 负荷口径归一化的痕迹（calibration.py 打的标）。
    # calib_ratio 不是 None 就说明这里的重量已经被折算过，报告里要说出来 ——
    # 一个被悄悄改过的数字比一个明显错的数字危险得多。
    calib_ratio: float | None = None
    compare_excluded: bool = False     # 口径存疑，本次不参与配对（组数/容量照常计）

    @property
    def avg_rpe(self) -> float | None:
        return statistics.fmean(self.rpes) if self.rpes else None


@dataclass
class SessionStats:
    id: str
    date: str
    source: str
    title: str
    duration_min: float | None
    kcal: float | None
    movements: list[MovementStats]
    volume_kg: float | None
    sets_done: int
    sets_planned: int
    groups: dict[str, float]          # 肌群 → 有效组数
    rpe_coverage: float               # 记了 RPE 的组 / 有效组
    volume_incomplete: bool
    # 标了难度的动作 / 有效动作。和 rpe_coverage 是两个独立来源：
    # 训记的 rpe 字段实际从不填，难度标签才是这个 app 里真实可得的强度信号。
    difficulty_coverage: float = 0.0

    @property
    def has_intensity_signal(self) -> bool:
        """够不够做强度判断。RPE 和难度标注任一达标即可。"""
        return (self.rpe_coverage >= 0.30) or (self.difficulty_coverage >= 0.50)

    @property
    def density_kg_per_min(self) -> float | None:
        if self.volume_kg is None or not self.duration_min:
            return None
        return self.volume_kg / self.duration_min

    @property
    def top_group(self) -> str | None:
        if not self.groups:
            return None
        return max(self.groups.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @property
    def label(self) -> str:
        """人类可读的部位标签，比如「胸 + 三头」。"""
        if not self.groups:
            return "（无有效动作）"
        ranked = sorted(self.groups.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(self.groups.values()) or 1
        main = [g for g, n in ranked if n / total >= 0.2]
        return " + ".join(main[:3]) or ranked[0][0]


def movement_stats(m: dict, bodyweight_kg: float | None) -> MovementStats:
    cls: Classification = classify_movement(m)
    sets = m.get("sets") or []
    done = [s for s in sets if set_done(s, m)]
    timed = is_timed(m)

    volumes = [set_volume_kg(s, m, bodyweight_kg) for s in done]
    known = [v for v in volumes if v is not None]
    # 计时类动作本来就没有「次数 × 负荷」意义上的容量，缺的不是数据而是单位。
    # 不标成 incomplete，否则每次练平板支撑都会误报一条「容量不完整」。
    incomplete = (not timed) and any(v is None for v in volumes) and bool(done)
    volume = sum(known) if known else None

    times = [s["time_s"] for s in done if s.get("time_s")]

    loads = [set_load_kg(s, m, bodyweight_kg) for s in done]
    known_loads = [x for x in loads if x is not None]
    top_load = max(known_loads) if known_loads else None

    best_e1rm, method = None, "n/a"
    for s in done:
        # 走同一套负荷逻辑，避免自重/辅助/单侧的换算在两处各写一遍而走样。
        # per_side=True：单侧动作用单侧重量算 1RM，不能用双侧合计。
        val, meth = e1rm(set_load_kg(s, m, bodyweight_kg, per_side=True), s.get("reps"))
        if val is not None and (best_e1rm is None or val > best_e1rm):
            best_e1rm, method = val, meth

    imbalance = None
    if m.get("unilateral"):
        pairs = [(s.get("weight_kg"), s.get("left_weight_kg")) for s in done]
        diffs = [abs(r - l) / max(r, l) * 100
                 for r, l in pairs if r and l and max(r, l) > 0]
        if diffs:
            imbalance = statistics.fmean(diffs)

    return MovementStats(
        name=m.get("name", ""),
        group=cls.group,
        group_source=cls.source,
        unilateral=bool(m.get("unilateral")),
        exetype=m.get("exetype"),
        sets_done=len(done),
        sets_planned=len(sets),
        reps_total=sum(s.get("reps") or 0 for s in done),
        volume_kg=volume,
        top_load_kg=top_load,
        best_e1rm=best_e1rm,
        e1rm_method=method,
        rpes=[s["rpe"] for s in done if s.get("rpe") is not None],
        imbalance_pct=imbalance,
        timed=timed,
        time_s_total=sum(times) if times else None,
        best_time_s=max(times) if times else None,
        difficulty=difficulty_label(m),
        volume_incomplete=incomplete,
        bodyweight=any(is_bodyweight(m, s) for s in sets) if sets else False,
        assisted=is_assisted(m),
        calib_ratio=(m.get("_calib") or {}).get("ratio"),
        compare_excluded=bool((m.get("_calib") or {}).get("exclude_compare")),
    )


def session_stats(session: dict, bodyweight_kg: float | None = None) -> SessionStats:
    movements = [movement_stats(m, bodyweight_kg) for m in session.get("movements") or []]

    groups: dict[str, float] = {}
    for ms in movements:
        # 拉伸不计入训练容量
        if ms.group == "拉伸":
            continue
        groups[ms.group] = groups.get(ms.group, 0) + ms.sets_done

    known_vol = [ms.volume_kg for ms in movements if ms.volume_kg is not None]
    sets_done = sum(ms.sets_done for ms in movements)
    rpe_count = sum(len(ms.rpes) for ms in movements)

    # 难度是动作级标签，分母也必须是动作数 —— 用组数当分母会让多组动作被稀释。
    active = [ms for ms in movements if ms.sets_done > 0]
    tagged = [ms for ms in active if ms.difficulty]

    return SessionStats(
        id=session.get("id", ""),
        date=session.get("date", ""),
        source=session.get("source", ""),
        title=session.get("title") or "",
        duration_min=(session["duration_s"] / 60.0) if session.get("duration_s") else None,
        kcal=session.get("kcal"),
        movements=movements,
        volume_kg=sum(known_vol) if known_vol else None,
        sets_done=sets_done,
        sets_planned=sum(ms.sets_planned for ms in movements),
        groups={g: n for g, n in groups.items() if n > 0},
        rpe_coverage=(rpe_count / sets_done) if sets_done else 0.0,
        volume_incomplete=any(ms.volume_incomplete for ms in movements),
        difficulty_coverage=(len(tagged) / len(active)) if active else 0.0,
    )


# ── 体重趋势 ────────────────────────────────────────────────────────────

def rolling_weight(body_records: list[dict], window: int = 7) -> list[tuple[str, float]]:
    """体重 7 日移动均线。

    实测日间噪声可达 ±1.8kg（2026-07-14 79.8 → 07-16 81.6 → 07-18 80.0），
    直接用当天读数做任何判断都会得出垃圾结论。用户自己的原则也是「看周不看天」。
    """
    weights = sorted(((r["date"], r["value"]) for r in body_records
                      if r.get("type") == "weight" and r.get("value")),
                     key=lambda kv: kv[0])
    out = []
    for i, (date, _) in enumerate(weights):
        lo = max(0, i - window + 1)
        vals = [v for _, v in weights[lo:i + 1]]
        out.append((date, statistics.fmean(vals)))
    return out


def weight_at(body_records: list[dict], date: str, window: int = 7) -> float | None:
    """某个日期的趋势体重。找不到就用最近的一个值。"""
    trend = rolling_weight(body_records, window)
    if not trend:
        return None
    before = [v for d, v in trend if d <= date]
    return before[-1] if before else trend[0][1]
