"""营养成分与每日目标摄入量。

两件事：

1. **一道菜每 100 g 大概是什么** —— 由 `dish-composition.json` 的配比
   × `nutrition-reference.json` 的食材值算出。菜品的热量**从不手写**，
   手写的数字半年后没人能追溯它是怎么来的。
2. **你今天该吃多少** —— Mifflin-St Jeor 算基础代谢，用**实测步数和训练频率**
   推活动系数，再按阶段（减脂/维持/增肌）调整。

## 精度：说清楚它有多准，比让它看起来很准重要

三层误差，一层比一层大：

| 环节 | 误差来源 | 量级 |
|---|---|---|
| 食材每 100 g | 品种、部位、做法 | ±10~20% |
| 菜品配比 | 不同店油量差异（**最大的一项**） | ±30% |
| 基础代谢公式 | Mifflin-St Jeor 本身 | 82% 的人落在 ±10% 内 |

所以这些数字回答的是**「这顿大概什么水平、蛋白够不够」**，
不回答「今天摄入了多少千卡」。后者需要称重和包装营养表，
而用户明确说过不做这件事（见 `personal-context.md` §7）。

输出里始终带着口径和误差说明 —— 一个不标注误差的估算值，
比没有这个估算值更糟。

## 为什么活动系数是算出来的不是填的

让用户在「久坐 / 轻度 / 中度 / 高度」里选一个，是所有热量计算器的通病：
没人知道自己算哪一档，而选错一档就是 ±15% 的偏差。

这里改成从**已经在采集的数据**推：日均步数（苹果健康）+ 每周训练次数
（训练记录）。数据本来就有，推导规则写在下面的表里，随时能核对。
用户想手动定死就填 `data/profile.json` 的 `diet.activity_factor`。
"""

from __future__ import annotations

import datetime as dt

from . import store
from .config import DATA_DIR, KNOWLEDGE_DIR, PROFILE_PATH

NUTRITION_PATH = KNOWLEDGE_DIR / "nutrition-reference.json"
COMPOSITION_PATH = KNOWLEDGE_DIR / "dish-composition.json"
METRICS_PATH = DATA_DIR / "apple-health" / "metrics.jsonl"

# 营养素顺序，和 nutrition-reference.json 的 _fields 一致
MACROS = ("kcal", "protein_g", "fat_g", "carb_g")

# 体重取 7 日均值 —— 和项目其余部分一个口径。
# 日间波动可以到 ±1.8 kg，用单日读数算出来的目标每天都在跳。
WEIGHT_WINDOW_DAYS = 7

# ── 活动系数 ────────────────────────────────────────────────────────────
#
# 从实测步数推。分档参考常见的活动量分级，但**这是启发式，不是公式** ——
# 所以输出里会写明用了哪一档、依据是多少步。

STEP_TIERS = (
    (5000, 1.20, "久坐"),
    (7500, 1.30, "轻度活动"),
    (10000, 1.40, "中度活动"),
    (12500, 1.50, "较高活动"),
    (10**9, 1.55, "高活动"),
)
# 每周每次力量训练加一点。封顶 0.15 —— 力量训练的净热量消耗被高估得很厉害，
# 一次 60 分钟大约 200–300 kcal，不该让它把系数推到 1.9。
TRAIN_BONUS_PER_SESSION = 0.03
TRAIN_BONUS_CAP = 0.15
ACTIVITY_CAP = 1.90
DEFAULT_ACTIVITY = 1.375  # 没有任何数据时的兜底，对应「轻度活动」

# ── 阶段调整 ────────────────────────────────────────────────────────────
#
# 缺口/盈余用 TDEE 的百分比算，再夹到一个绝对区间里。
# 只用百分比，TDEE 低的人缺口会小到没有意义；只用固定值，TDEE 高的人又太温和。

PHASE_ENERGY = {
    "减脂": {"pct": -0.18, "floor": -600, "ceil": -300},
    "维持": {"pct": 0.0, "floor": 0, "ceil": 0},
    "增肌": {"pct": 0.10, "floor": 200, "ceil": 400},
}

# 蛋白 g/kg 体重。抗阻训练人群在热量缺口下需要更高的蛋白来保住瘦体重，
# 所以减脂期反而最高。
PROTEIN_PER_KG = {"减脂": 2.0, "维持": 1.8, "增肌": 1.8}
# 脂肪下限，保证激素和脂溶性维生素。碳水吃剩下的。
FAT_PER_KG = {"减脂": 0.8, "维持": 0.9, "增肌": 1.0}

KCAL_PER_G = {"protein_g": 4, "fat_g": 9, "carb_g": 4}

ACCURACY_NOTE = "Mifflin-St Jeor 对 82% 的健康成年人误差在 ±10% 以内，把它当起点不是终点"


# ── 食材与菜品 ──────────────────────────────────────────────────────────


def load_foods() -> dict[str, list[float]]:
    return (store.read_json(NUTRITION_PATH, default={}) or {}).get("foods", {})


def load_compositions() -> dict[str, dict[str, float]]:
    return (store.read_json(COMPOSITION_PATH, default={}) or {}).get("compositions", {})


def dish_per100g(name: str) -> dict | None:
    """一道菜每 100 g 的估算值。没有配比就返回 None —— 不猜。"""
    comp = load_compositions().get(name)
    if not comp:
        return None
    foods = load_foods()
    total = sum(comp.values())
    if total <= 0:
        return None

    out = {k: 0.0 for k in MACROS}
    for ing, share in comp.items():
        vals = foods.get(ing)
        if vals is None:
            # 配比里写了参考表没有的食材。测试会拦住这种情况，
            # 运行期就当它不存在，别让一个错字把整道菜的估算变成 0。
            continue
        for k, v in zip(MACROS, vals):
            out[k] += v * share / total

    return {
        "kcal": round(out["kcal"]),
        "protein_g": round(out["protein_g"], 1),
        "fat_g": round(out["fat_g"], 1),
        "carb_g": round(out["carb_g"], 1),
        "basis": comp,
    }


def protein_share(per100g: dict) -> float:
    """蛋白供能占比。判断「这顿是不是碳水/脂肪堆出来的」比看绝对克数直观。"""
    kcal = per100g.get("kcal") or 0
    if kcal <= 0:
        return 0.0
    return per100g["protein_g"] * KCAL_PER_G["protein_g"] / kcal


# ── 个人参数 ────────────────────────────────────────────────────────────


def _profile() -> dict:
    return store.read_json(PROFILE_PATH, default={}) or {}


def recent_weight(today: dt.date, *, days: int = WEIGHT_WINDOW_DAYS
                  ) -> tuple[float, int, str] | None:
    """(体重, 用了几条读数, 口径说明)。

    ⚠️ **回退分支必须也用 today 设上界。** 按历史日期回算时（`hc targets --date`），
    没有上界就会拿到「未来」的体重，而输出还写着「最近 7 天的均值」——
    这个仓库已经栽过一次同类的坑（commit e334c46 修的报告体重块）。

    回退时口径变了（不再是 7 日均值，而是某天的单次读数），所以口径说明
    跟着返回，由调用方原样打印，不许再自己编一句。
    """
    body = [r for r in store.load_body(types=["weight"])
            if r["date"] <= today.isoformat()]
    if not body:
        return None
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    window = [r for r in body if r["date"] >= start]
    if window:
        mean = round(sum(r["value"] for r in window) / len(window), 2)
        return mean, len(window), f"最近 {days} 天 {len(window)} 条读数的均值"
    last = body[-1]
    stale = (today - dt.date.fromisoformat(last["date"])).days
    return (round(last["value"], 2), 1,
            f"⚠️ {last['date']} 的单次读数（{stale} 天前，不是 {days} 日均值）")


def age_years(today: dt.date) -> int | None:
    # 年龄的真相源是 age.py —— 这里曾经和 cardio.py 各写一份同样的减法。
    # **档案由本模块提供**：age.py 自己也能读盘，但那样就绕过了这里的缓存，
    # 也让测试没法通过替换本模块的 PROFILE_PATH 来注入。
    from .age import age
    return age(today, _profile())


def _daily_steps(today: dt.date, *, days: int = 30) -> float | None:
    """最近 N 天的日均步数。取中位数 —— 一次远足不该把系数顶上去。"""
    if not METRICS_PATH.exists():
        return None
    start = (today - dt.timedelta(days=days)).isoformat()
    vals = [r["value"] for r in store.read_jsonl(METRICS_PATH)
            if r.get("metric") == "steps" and start <= r.get("date", "") <= today.isoformat()]
    if not vals:
        return None
    vals.sort()
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _sessions_per_week(today: dt.date, *, days: int = 28) -> float:
    start = (today - dt.timedelta(days=days)).isoformat()
    dates = {s["date"] for s in store.load_sessions(start=start, end=today.isoformat())}
    return len(dates) / (days / 7)


def activity_factor(today: dt.date) -> dict:
    """活动系数 + 它是怎么来的。来源必须能看见，否则没法判断该不该信。"""
    explicit = (_profile().get("diet") or {}).get("activity_factor")
    if explicit:
        return {"value": float(explicit), "source": "手动设定",
                "detail": "data/profile.json 的 diet.activity_factor",
                "steps": None, "sessions": None}

    steps = _daily_steps(today)
    sessions = _sessions_per_week(today)

    if steps is None:
        base, label = DEFAULT_ACTIVITY, "无步数数据，按轻度活动兜底"
    else:
        base, label = next((f, lb) for limit, f, lb in STEP_TIERS if steps < limit)
        label = f"日均 {round(steps):,} 步 → {label}"

    bonus = min(sessions * TRAIN_BONUS_PER_SESSION, TRAIN_BONUS_CAP)
    return {
        "value": round(min(base + bonus, ACTIVITY_CAP), 3),
        "source": "由数据推算",
        "detail": f"{label}；力量训练 {sessions:.1f} 次/周 → +{bonus:.2f}",
        "steps": steps, "sessions": sessions,
    }


# ── 每日目标 ────────────────────────────────────────────────────────────


def targets(today: dt.date | None = None) -> dict:
    """今天该吃多少。缺数据时返回 missing 列表，**不猜**。"""
    from . import dice  # 阶段的真相源在 dice，避免两处各定义一套

    today = today or dt.date.today()
    p = _profile()
    phase, phase_defaulted = dice.load_phase()

    missing: list[str] = []
    sex = p.get("sex")
    height = p.get("height_cm")
    age = age_years(today)
    w = recent_weight(today)

    if sex not in ("male", "female"):
        missing.append("性别（data/profile.json 的 sex）")
    if not height:
        missing.append("身高（data/profile.json 的 height_cm）")
    if age is None:
        missing.append("出生年（data/profile.json 的 birth_year）")
    if w is None:
        missing.append("体重（跑 hc sync body 或直接告诉助手）")

    if missing:
        return {"ok": False, "missing": missing, "phase": phase}

    weight, n_readings, weight_basis = w
    # Mifflin-St Jeor (1990)
    bmr = 10 * weight + 6.25 * height - 5 * age + (5 if sex == "male" else -161)
    act = activity_factor(today)
    tdee = bmr * act["value"]

    # 缺口/盈余：先按 TDEE 百分比算，再夹进绝对区间。
    # 只用百分比，TDEE 低的人缺口小到没意义；只用固定值，TDEE 高的人又太温和。
    adj = PHASE_ENERGY[phase]
    delta = max(adj["floor"], min(adj["ceil"], tdee * adj["pct"]))
    kcal = tdee + delta

    protein_g = PROTEIN_PER_KG[phase] * weight
    fat_g = FAT_PER_KG[phase] * weight
    carb_kcal = kcal - protein_g * KCAL_PER_G["protein_g"] - fat_g * KCAL_PER_G["fat_g"]
    carb_g = max(0.0, carb_kcal / KCAL_PER_G["carb_g"])

    return {
        "ok": True,
        "date": today.isoformat(),
        "phase": phase,
        "phase_defaulted": phase_defaulted,
        "weight": weight,
        "weight_readings": n_readings,
        "weight_basis": weight_basis,
        "height_cm": height,
        "age": age,
        "sex": sex,
        "bmr": round(bmr),
        "activity": act,
        "tdee": round(tdee),
        "delta": round(delta),
        "kcal": round(kcal),
        "protein_g": round(protein_g),
        "fat_g": round(fat_g),
        "carb_g": round(carb_g),
        "protein_per_kg": PROTEIN_PER_KG[phase],
    }


def render_targets(t: dict) -> str:
    if not t.get("ok"):
        lines = ["今天的目标摄入量算不出来，还缺："]
        lines += [f"  · {m}" for m in t["missing"]]
        lines.append("")
        lines.append("  跑 `hc setup` 可以一次填完。")
        return "\n".join(lines)

    act = t["activity"]
    return "\n".join([
        f"每日目标摄入量  {t['date']} · {t['phase']}期",
        "",
        f"  热量    {t['kcal']} kcal"
        + (f"（维持约 {t['tdee']}，{'缺口' if t['delta'] < 0 else '盈余'} {abs(t['delta'])}）"
           if t["delta"] else f"（= 维持热量 {t['tdee']}）"),
        f"  蛋白    {t['protein_g']} g   （{t['protein_per_kg']} g/kg）",
        f"  脂肪    {t['fat_g']} g",
        f"  碳水    {t['carb_g']} g   （吃剩下的）",
        "",
        "  怎么来的",
        f"    体重    {t['weight']} kg（{t['weight_basis']}）",
        f"    基础代谢 {t['bmr']} kcal —— Mifflin-St Jeor（{t['sex'] == 'male' and '男' or '女'} "
        f"{t['age']} 岁 {t['height_cm']} cm）",
        f"    活动系数 ×{act['value']}（{act['source']}）",
        f"             {act['detail']}",
        "",
        f"  ⚠️  {ACCURACY_NOTE}。体重两周没动再调，别按单日读数改。",
    ])
