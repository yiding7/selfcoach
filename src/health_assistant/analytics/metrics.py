"""单次训练的指标计算。纯函数、确定性。

这里所有数字都不依赖模型。换任何模型跑，出来的吨位、组数、估算 1RM 完全一致。
模型只负责在报告里把这些数字讲得好听。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from functools import lru_cache

from .. import gyms, loading
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


def _recorded_sides(s: dict) -> list[float]:
    """这一行记录里出现的重量数值。一个或两个。

    两个的时候它们**是什么**取决于动作类型 —— 见 `loading.py`：
    `both` 类是两只手（同一次提举的两半），`per_side` 类是两组（左右各一组）。
    这个函数不做解释，只负责把数取出来。
    """
    w, left = s.get("weight_kg"), s.get("left_weight_kg")
    out = [] if w is None else [float(w)]
    if left is not None:
        out.append(float(left))
    return out


def _machine_load(s: dict, movement: dict, bodyweight_kg: float | None
                  ) -> tuple[bool, float | None]:
    """自重 / 辅助器械的等效负荷。这两类不存在「单侧口径」，算出来就是整体。

    返回 `(适用, 值)`。**这两件事必须分开** —— 用单个 `None` 同时表示
    「不是这一类」和「是这一类但缺体重算不出」，会让辅助器械在缺体重时
    漏到下面的原始重量分支，把**助力配重**当成负荷返回：助力调得越大
    （越轻松）显示的负荷反而越高，方向完全反了。
    这个坑在改口径时真的踩了一次，`tests/test_metrics.py::TestAssisted` 抓住了。
    """
    if is_assisted(movement):
        # 辅助器械：记录的是助力配重，方向和负重相反。
        # 真实负荷 = 体重折算 − 助力。
        if bodyweight_kg is None:
            return True, None
        base = bodyweight_kg * bodyweight_factor(movement.get("name", ""))
        return True, max(base - (s.get("weight_kg") or 0.0), 0.0)
    if is_bodyweight(movement, s):
        if bodyweight_kg is None:
            return True, None
        base = bodyweight_kg * bodyweight_factor(movement.get("name", ""))
        return True, base + (s.get("weight_kg") or 0.0)
    return False, None


def per_side_load_kg(s: dict, movement: dict, bodyweight_kg: float | None
                     ) -> float | None:
    """**单侧 / 单器械**负荷 —— 顶组和估算 1RM 的统一基准。

    也就是「你往一个哑铃上加了多少」「器械配重片停在哪一格」，
    和你在器械上看到的数字一致。

    为什么顶组和 1RM 必须同一个基准：此前顶组用双侧合计而 1RM 用单侧，
    同一个动作「一会儿乘 1 一会儿乘 2」，于是打出「顶组 20kg（双侧合计）/
    估算 1RM 13kg（单侧）」—— 1RM 低于顶组，数学上不可能。
    使用者 2026-08-11 拍板统一到单侧口径，接受历史数字变化。

    两侧都记了就取**较重**那一侧：顶组的定义是「那天真的举起来过的最重的东西」。
    此前这里只读 `weight_kg` 而丢掉 `left_weight_kg`，左边更重时 1RM 会低估
    （保加利亚蹲右 10 / 左 15，算的是 10）。
    """
    applies, machine = _machine_load(s, movement, bodyweight_kg)
    if applies:
        return machine
    sides = _recorded_sides(s)
    return max(sides) if sides else None


def set_load_kg(s: dict, movement: dict, bodyweight_kg: float | None,
                *, per_side: bool = False) -> float | None:
    """一次提举的等效负荷（kg）。返回 None 表示「无法计算」，绝不返回 0 兜底。

    `per_side=True` 直接转 `per_side_load_kg()`（顶组与 1RM 用它）。

    `per_side=False` 给的是**单次提举实际移动的总重**：
    双手各一个哑铃同时推 = 两只相加；单个哑铃 = 就那一个。
    口径来自 `knowledge/movements/implement-loading.json`，不再依赖训记那个
    `unilateral` 布尔值 —— 它其实是**记录格式标记**（只表示「这条记录带了左右
    两个重量」），把双手同推和左右分做混成了一类。
    """
    if per_side:
        return per_side_load_kg(s, movement, bodyweight_kg)

    applies, machine = _machine_load(s, movement, bodyweight_kg)
    if applies:
        return machine
    sides = _recorded_sides(s)
    if not sides:
        return None

    spec = loading.classify(movement.get("name", ""))
    if spec.per_side_sets:
        # 左右分别各做一组：两个数是两组各自的重量，**不是同一次提举的两半**。
        # 单次提举负荷取较重那一组，并按器械数折算（每侧都拎着两个哑铃）。
        return max(sides) * spec.factor
    # 两侧同时做：两个数就是两只手，直接相加（比 ×2 准，两只不一样重时也对）。
    return sum(sides) if len(sides) > 1 else sides[0] * spec.factor


def set_volume_kg(s: dict, movement: dict, bodyweight_kg: float | None) -> float | None:
    """这一行记录贡献的总功（kg·次）。

    `per_side` 类动作的一行记录**是两组**，两组各自的功要分别算再相加 ——
    而且 `reps` 是**每侧**的次数（使用者 2026-08-11 确认）。

    此前这里把右+左相加当成单次负荷再乘次数。对「双手同推」和「单臂轮流」
    代数上恰好正确，但对「两手各拎一个、左右腿分别做」**少算一半** ——
    漏掉了每侧都还拎着两个哑铃这一层。保加利亚蹲和箭步蹲的历史吨位
    一直是实际的一半。
    """
    if not set_done(s, movement):
        return None
    reps = s.get("reps")
    if not reps:
        return None

    applies, machine = _machine_load(s, movement, bodyweight_kg)
    if applies:
        return None if machine is None else machine * reps
    sides = _recorded_sides(s)
    if not sides:
        return None

    spec = loading.classify(movement.get("name", ""))
    if spec.per_side_sets:
        return sum(x * spec.factor * reps for x in sides)
    load = sum(sides) if len(sides) > 1 else sides[0] * spec.factor
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
    calib_offset_kg: float | None = None   # 同上，加法那一路（滑车自重 / 配重 / 助力）
    # 换馆之后这个负荷数字还成不成立。杠铃 64kg 在哪个馆都是 64kg；
    # 哈克机 107kg 换台机器就不是同一个量。表在
    # knowledge/movements/site-dependence.json，认不出来时保守取 False。
    load_portable: bool = False
    site_rule_default: bool = False    # 走了默认值 —— 值得补进表里

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
    # 这次在哪个馆练的。None = 没标过 —— **不是「同一个馆」**，是「不知道」。
    # 两者绝不能混：不知道时一切照旧，知道且不同才降级负荷对比。
    gym: str | None = None

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

    # 顶组和估算 1RM **必须同一个基准**，否则会打出「顶组 24.0kg /
    # 估算 1RM 13kg（单侧）」这种 1RM 低于顶组的组合。统一到单侧/单器械口径
    # （使用者 2026-08-11 拍板），也就是你在器械上看到的那个数。
    loads = [per_side_load_kg(s, m, bodyweight_kg) for s in done]
    known_loads = [x for x in loads if x is not None]
    top_load = max(known_loads) if known_loads else None

    best_e1rm, method = None, "n/a"
    for s in done:
        # 走同一个函数，避免自重/辅助/单侧的换算在两处各写一遍而走样。
        val, meth = e1rm(per_side_load_kg(s, m, bodyweight_kg), s.get("reps"))
        if val is not None and (best_e1rm is None or val > best_e1rm):
            best_e1rm, method = val, meth

    imbalance = None
    if m.get("unilateral"):
        pairs = [(s.get("weight_kg"), s.get("left_weight_kg")) for s in done]
        diffs = [abs(r - l) / max(r, l) * 100
                 for r, l in pairs if r and l and max(r, l) > 0]
        if diffs:
            imbalance = statistics.fmean(diffs)

    site = gyms.site_dependence(m.get("name", ""))

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
        calib_offset_kg=(m.get("_calib") or {}).get("offset_kg"),
        load_portable=site.portable,
        site_rule_default=site.is_default,
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
        gym=(session.get("gym") or None),
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

    日间噪声到 ±1.8kg 是常事（示意：75.0 → 76.8 → 75.0，一天涨完一天退回），
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
