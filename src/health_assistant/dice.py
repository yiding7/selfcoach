"""食物骰子 —— 替你做「今天吃什么」这个决定。

## 为什么不是随机

真·随机的骰子第二周就没人用了：它会摇出忌口的东西、会连着三天摇出面食、
会在尿酸偏高的时候摇出爆炒腰花。那不叫帮你决策，那叫多一个要否决的提议。

所以这里的骰子是**约束在先、随机在后**。五层，顺序不能换 ——
越靠前的越硬，越靠后的越可调：

| 层 | 管什么 | 硬度 | 来源 |
|---|---|---|---|
| 0 场景 | 现在这一餐、这个场合能吃到什么 | 硬 | `--slot` / `--scene` |
| 1 忌口 | 不吃和过敏。**个人喜好，不需要理由** | 硬，永不放行 | `health-constraints.md` 的「## 忌口」 |
| 2 医学禁忌 | 由**实际异常指标**推出的完全不能吃 | 硬，指标恢复才解除 | `profile.json` 的 `diet.medical_blocks` |
| 3 目标层 | 阶段（减脂/维持/增肌）决定破戒额度和灯的权重 | 软，有额度 | `profile.json` 的 `diet.phase` |
| 4 偏好层 | 爱吃的加权、不爱吃的降权 | 软，只调概率 | `profile.json` 的 `diet.likes` / `dislikes` |
| 5 加权摇 | 蛋白密度、灯、嘌呤、最近摇过 | 概率 | 本模块的权重表 |

**第 1 层和第 2 层的区别很重要**，不要合并：

- 忌口是**偏好**。他不吃木耳，不需要理由，也永远不会因为指标好转而改变。
- 医学禁忌是**指标推出来的**。尿酸 458 所以内脏不能碰 —— 等尿酸回到正常区间，
  这条就该解除。把它和忌口混在一起，等于让一个临时状态变成永久规则。

这也是为什么医学禁忌用 **flag** 而不是菜名：菜品池里标的是中性事实
（这道菜「以内脏为主」「以浓肉汤为主」），哪些 flag 该拦是**个人**的事，
写在 `profile.json` 里。这样池子可以分享，禁忌不外泄。

## 关于破戒额度不写死

额度**跟着当下目标走**，不是一个常数：

- **减脂** 每月 1 次 —— 有缺口要维持，一次高热量聚餐能吃掉小半周的缺口
- **维持** 每月 2 次 —— 没有缺口压力，可持续比严格更重要
- **增肌** 每月 4 次 —— 要吃够，这时候把黄灯压得很低反而是帮倒忙

同一个人在不同阶段本来就该有不同尺度。把 1 次/月 焊死在代码里，
等于假装他永远在减脂。阶段从 `data/profile.json` 的 `diet.phase` 读，
输出里会明说这次按哪个阶段算 —— 阶段错了要能一眼看出来。

红灯直接一刀切掉是这类工具最常见的死法：一个永远不摇火锅的骰子，
第二周就被关掉了。额度是**自己给自己定的上限**，不是外部戒律，超了只陈述不说教。

## 关于「已经摇过了」

同一餐次当天再跑 `hc dice`，默认**直接回放上次的结果**，不重摇。
决策疲劳的解药是「已经定了」，不是「再来一次」。想换用 `--again` ——
它会作废旧的那次并重摇，旧记录原样留在日志里。

## 存储

- 候选池：`knowledge/dish-pool.json`（通用，进版本库）
           + `profile/dish-pool.local.json`（个人增删改，不进版本库，同名覆盖）
- 嘌呤分档：`knowledge/purine-reference.json`（USDA/ODS 实测值，别凭印象改）
- 摇过什么：`data/dice.jsonl`，只追加。额度和「最近摇过」都是从这里回放出来的。

种子只在**同一份池子 + 同一段历史**下可复现：摇完写了日志，历史就变了，
同一个种子会给出不同结果。这是对的 —— 骰子该按最新状态摇。
查历史看 `hc dice log`，别靠重放种子。
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
from typing import Iterable, NamedTuple

from . import store
from .config import DATA_DIR, KNOWLEDGE_DIR, PROFILE_DIR, PROFILE_PATH

# ── 词表 ────────────────────────────────────────────────────────────────

TIERS = ("绿", "黄", "红")
# 「极高」是 2026-08 新增的一档。只有内脏和沙丁鱼/凤尾鱼那一类到得了，
# 见 knowledge/purine-reference.json —— 200 和 550 mg/100g 的实际代价差太远，
# 混在同一档里骰子就没法只拦真正危险的那一类。
PURINES = ("低", "中", "高", "极高")
PROTEINS = ("高", "中", "低")
SLOTS = ("早", "午", "晚", "加餐")
SCENES = ("外卖", "店里", "家里", "聚餐")
EFFORTS = ("快手", "中等", "费事")
PHASE_NAMES = ("减脂", "维持", "增肌")

# 菜品上的中性事实标签。**标签本身不代表禁止** —— 哪些该拦是个人的事，
# 写在 data/profile.json 的 diet.medical_blocks 里。池子因此可以分享。
FLAGS = (
    "内脏为主",      # 尿酸 / 胆固醇
    "浓肉汤为主",    # 嘌呤溶于汤，这道菜的主体就是那锅汤
    "高嘌呤海产",    # 沙丁鱼、凤尾鱼、鱼籽
    "酒精",          # 尿酸排泄、甘油三酯、脂肪肝
    "高果糖",        # 甘油三酯、脂肪肝
    "油炸",
    "加工肉",        # 香肠、培根、火腿
    "高盐腌制",
    "生食",          # 免疫低下 / 孕期
)

TIER_LABEL = {"绿": "绿灯", "黄": "黄灯", "红": "红灯"}

POOL_PATH = KNOWLEDGE_DIR / "dish-pool.json"
PURINE_PATH = KNOWLEDGE_DIR / "purine-reference.json"
LOCAL_POOL_PATH = PROFILE_DIR / "dish-pool.local.json"
LOG_PATH = DATA_DIR / "dice.jsonl"
CONSTRAINTS_PATH = PROFILE_DIR / "health-constraints.md"

# ── 第 3 层：目标阶段 ───────────────────────────────────────────────────
#
# 这张表是「跟着当下目标走」的全部实现。三个阶段的差别不是风格，是热量方向：
# 减脂要缺口、维持要持平、增肌要盈余，所以对黄灯的容忍度本来就该不同。

PHASES = {
    "减脂": {
        "quota": 1,
        "tier_w": {"绿": 2.5, "黄": 0.9, "红": 0.12},
        "protein_w": {"高": 3.5, "中": 1.4, "低": 0.4},
        "why": "有缺口要守：绿灯拉开差距，蛋白权重顶到最高，破戒额度最紧",
    },
    "维持": {
        "quota": 2,
        "tier_w": {"绿": 2.0, "黄": 1.2, "红": 0.30},
        "protein_w": {"高": 3.0, "中": 1.5, "低": 0.6},
        "why": "可持续优先：黄灯不再压得很低，额度放到每月 2 次",
    },
    "增肌": {
        "quota": 4,
        "tier_w": {"绿": 1.8, "黄": 1.5, "红": 0.50},
        "protein_w": {"高": 3.0, "中": 1.8, "低": 0.7},
        "why": "要吃够：黄灯几乎不惩罚，蛋白仍是第一权重，额度最宽",
    },
}
DEFAULT_PHASE = "维持"

# ── 第 5 层：权重 ───────────────────────────────────────────────────────
#
# tier_w 和 protein_w 归阶段管（上面那张表）。这里只放和阶段无关的部分。

# 嘌呤。分档依据 knowledge/purine-reference.json 的实测值，不是印象。
PURINE_W = {"低": 1.0, "中": 0.75, "高": 0.35, "极高": 0.15}

# 「最近摇过」的降权。骰子最讨人厌的失败模式是连着三天给同一个答案。
RECENCY_W = ((3, 0.05), (7, 0.2), (14, 0.6))

# food-traffic-light.md 的实操原则第 2 条是「黄灯叠加效应」。
# 它讲的是一顿饭里的叠加，这里落成一周之内的叠加。
YELLOW_WEEK_CAP = 3
YELLOW_STREAK_W = 0.4

# 第 4 层：偏好。刻意做得比其他权重弱 —— 偏好该调概率，不该压过蛋白和灯。
LIKE_W = 1.8
DISLIKE_W = 0.35


# ── 候选池 ──────────────────────────────────────────────────────────────


def _normalize_dish(raw: dict) -> dict:
    """补全缺省字段。手写 local pool 时少写几个键不该炸。"""
    return {
        "name": (raw.get("name") or "").strip(),
        "tier": raw.get("tier") or "黄",
        "purine": raw.get("purine") or "中",
        "protein": raw.get("protein") or "中",
        "cuisine": raw.get("cuisine") or "其他",
        "effort": raw.get("effort") or "中等",
        "scenes": list(raw.get("scenes") or SCENES),
        "slots": list(raw.get("slots") or ["午", "晚"]),
        "contains": list(raw.get("contains") or []),
        "flags": list(raw.get("flags") or []),
        "fix": list(raw.get("fix") or []),
        "note": raw.get("note") or "",
        "source": raw.get("source") or "knowledge",
    }


def _validate_dish(d: dict) -> str | None:
    """返回第一个说不通的地方。个人池是手写的，写错一个字不该炸整个命令。"""
    if d["tier"] not in TIERS:
        return f"tier「{d['tier']}」不在 {'/'.join(TIERS)} 里"
    if d["purine"] not in PURINES:
        return f"purine「{d['purine']}」不在 {'/'.join(PURINES)} 里"
    if d["protein"] not in PROTEINS:
        return f"protein「{d['protein']}」不在 {'/'.join(PROTEINS)} 里"
    if d["effort"] not in EFFORTS:
        return f"effort「{d['effort']}」不在 {'/'.join(EFFORTS)} 里"
    bad = [f for f in d["flags"] if f not in FLAGS]
    if bad:
        return f"未知 flag {bad}"
    return None


def load_pool_with_issues() -> tuple[list[dict], list[str]]:
    """通用池 + 个人池，同名以个人池为准；返回 (可用的菜, 被丢掉的原因)。

    同名覆盖而不是合并字段：个人池里写了一条就是完整的一条，
    不用去猜哪几个键继承了通用池。

    校验放在这里而不是只放在 `add_dish`，是因为个人池是允许手写的 ——
    手写就会写错，而一个错字不该让整个命令抛 KeyError。坏行丢掉并报出来。
    """
    dishes: dict[str, dict] = {}
    issues: list[str] = []

    def take(raw: dict, source: str) -> None:
        d = _normalize_dish({**raw, "source": source})
        if not d["name"]:
            issues.append(f"{source} 池里有一条没有名字，已跳过")
            return
        why = _validate_dish(d)
        if why:
            issues.append(f"「{d['name']}」{why}，已跳过")
            return
        dishes[d["name"]] = d

    for raw in (store.read_json(POOL_PATH, default={}) or {}).get("dishes", []):
        take(raw, "knowledge")

    for raw in (store.read_json(LOCAL_POOL_PATH, default={}) or {}).get("dishes", []):
        if raw.get("drop"):  # 个人池里可以删掉通用池的某一条
            dishes.pop((raw.get("name") or "").strip(), None)
        else:
            take(raw, "local")

    return sorted(dishes.values(), key=lambda d: d["name"]), issues


def load_pool() -> list[dict]:
    return load_pool_with_issues()[0]


def cuisines() -> list[str]:
    return sorted({d["cuisine"] for d in load_pool()})


def add_dish(dish: dict) -> None:
    """往个人池追加/覆盖一条。"""
    d = _normalize_dish({**dish, "source": "local"})
    if not d["name"]:
        raise ValueError("菜名不能为空")
    if d["tier"] not in TIERS:
        raise ValueError(f"tier 只能是 {'/'.join(TIERS)}")
    if d["purine"] not in PURINES:
        raise ValueError(f"purine 只能是 {'/'.join(PURINES)}")
    if d["protein"] not in PROTEINS:
        raise ValueError(f"protein 只能是 {'/'.join(PROTEINS)}")
    bad = [f for f in d["flags"] if f not in FLAGS]
    if bad:
        raise ValueError(f"未知 flag {bad}，可用的是 {'/'.join(FLAGS)}")

    local = store.read_json(LOCAL_POOL_PATH, default=None) or {
        "schema": "ha.dishpool/1",
        "_comment": "个人候选池。同名覆盖 knowledge/dish-pool.json。不进版本库。",
        "dishes": [],
    }
    kept = [x for x in local.get("dishes", []) if x.get("name") != d["name"]]
    kept.append({k: v for k, v in d.items() if k != "source"})
    local["dishes"] = kept
    LOCAL_POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    store.write_atomic(LOCAL_POOL_PATH, store.dumps_pretty(local))


# ── 第 1 层：忌口 ───────────────────────────────────────────────────────


_SPLIT_RE = re.compile(r"[、,，/\s]+")

# 忌口按**配比占比**判断：某样忌口食材占到这个百分比才拦。
#
# 为什么不是「含一点就拦」：菜品池是公开的、不区分用户的，配比里写着真实食材，
# 于是几乎每道复合菜都会蹭到某个人的忌口。西班牙海鲜饭里 8% 的彩椒不该让整道菜
# 出局，而回锅肉里 25% 的青椒该。阈值定在 10 是因为数据本身在这里有个空档：
# 全池最低的青椒占比就是海鲜饭的 8%，往上直接跳到 10%。
AVOID_SHARE_THRESHOLD = 10

# 过敏**不走阈值**。忌口是偏好，少一点无所谓；过敏是医学事实，微量也可能出事。
# 这就是把两者分开存、分开判的全部理由。


class Walls(NamedTuple):
    """第 1 层的两堵墙。硬度不同，所以分开。"""
    avoid: list[str]       # 忌口：偏好，按占比阈值拦
    allergy: list[str]     # 过敏：医学事实，零容忍
    warn: str | None       # 非空表示这一层没能正常加载，要显眼地说出来

    def all_terms(self) -> list[str]:
        return _dedupe([*self.allergy, *self.avoid])


def _avoid_section(text: str) -> tuple[int, int] | None:
    """「## 忌口」小节正文在全文里的 (起, 止)。setup 改写时也用它定位。

    只认这一个小节，是为了让读和写用同一个契约 —— 用全文正则找第一行
    「不吃：」会改到别的小节里的历史行（那些按约定只追加不删除）。
    """
    for m in re.finditer(r"^##\s+(.+)$", text, flags=re.MULTILINE):
        if not m.group(1).strip().startswith("忌口"):
            continue
        start = m.end()
        nxt = re.search(r"^##\s+", text[start:], flags=re.MULTILINE)
        return start, (start + nxt.start()) if nxt else len(text)
    return None


def _parse_clause(section: str, keyword: str) -> list[str] | None:
    """读「不吃：X、Y。」这样的一句。返回 None 表示这一句根本不存在。"""
    m = re.search(rf"{keyword}[：:]\s*([^\n。]*)", section)
    if not m:
        return None
    body = m.group(1).strip()
    if body in ("", "（无）", "(无)", "无"):
        return []
    return [w for w in _SPLIT_RE.split(body) if w]


def load_avoid() -> Walls:
    """忌口与过敏。唯一真相源是 profile/health-constraints.md 的「## 忌口」小节。

    契约窄且写在那份 md 里：
        不吃：青椒、茄子。      → 偏好，按占比阈值拦
        过敏：虾、花生。        → 医学事实，零容忍（可选，没有就不写）

    **解析不出来必须吵。** 这一层静默失效正是它最危险的失败模式 ——
    文件还在、看起来一切正常，而骰子已经不再过滤了。
    """
    avoid: list[str] = []
    allergy: list[str] = []
    warn: str | None = None

    if not CONSTRAINTS_PATH.exists():
        warn = (f"没找到 {CONSTRAINTS_PATH.name}，忌口过滤没有生效 —— "
                f"摇出来的东西请自己再过一眼。")
    else:
        text = CONSTRAINTS_PATH.read_text(encoding="utf-8")
        span = _avoid_section(text)
        if span is None:
            warn = (f"{CONSTRAINTS_PATH.name} 里没有「## 忌口」小节，忌口过滤没有生效。"
                    f"跑 `hc setup` 补一份。")
        else:
            section = text[span[0]:span[1]]
            parsed = _parse_clause(section, "不吃")
            allergy += _parse_clause(section, "过敏") or []
            if parsed is None:
                warn = (f"{CONSTRAINTS_PATH.name} 的「## 忌口」小节里没找到"
                        f"「不吃：…」那一行，忌口过滤没有生效。格式契约见该文件，"
                        f"或跑 `hc setup` 重写。")
            else:
                avoid += parsed

    extra = _diet().get("avoid") or []
    avoid += [str(x) for x in extra]
    return Walls(_dedupe(avoid), _dedupe(allergy), warn)


def _ingredients(name: str) -> dict[str, float]:
    """这道菜的配比。菜品池不带食材，食材在 dish-composition.json 里。"""
    from . import nutrition
    return nutrition.load_compositions().get(name) or {}


def blocked_by(dish: dict, walls: Walls) -> tuple[str, str] | None:
    """这道菜是否撞墙。返回 (触发的词, 原因)，没撞返回 None。

    三条依据，从强到弱：菜名、池子里显式标的 contains、配比里的食材。
    加上配比这一条是关键 —— 池子里 135 道菜只有 10 道填了 contains，
    只靠 contains 等于这堵墙大部分时候形同虚设。
    """
    comp = _ingredients(dish["name"])
    total = sum(comp.values()) or 100

    def hits(term: str) -> tuple[float, str] | None:
        """(用于比阈值的占比, 依据说明)。菜名/contains 是显式标注，直接算命中。

        显式标注不报「占比 100%」—— 它根本不是一个占比，说成占比是在
        编一个没测过的数字。这两种依据要能分开看。
        """
        if term in dish["name"]:
            return 100.0, "菜名"
        if any(term in c for c in dish["contains"]):
            return 100.0, "已标注"
        share = sum(sh for ing, sh in comp.items() if term in ing)
        return (share * 100 / total, "占比 {:.0f}%".format(share * 100 / total)) \
            if share else None

    for term in walls.allergy:          # 零容忍，先判
        hit = hits(term)
        if hit:
            return term, f"过敏（{term}，{hit[1]}）"
    for term in walls.avoid:
        hit = hits(term)
        if hit and hit[0] >= AVOID_SHARE_THRESHOLD:
            return term, f"忌口（{term}，{hit[1]}）"
    return None


def _dedupe(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _diet() -> dict:
    profile = store.read_json(PROFILE_PATH, default={}) or {}
    return profile.get("diet") or {}


# ── 第 2 层：医学禁忌 ───────────────────────────────────────────────────


def load_medical_blocks() -> list[str]:
    """由**实际异常指标**推出的、完全不能吃的那一类。

    存的是 flag 不是菜名：菜品池标中性事实（「以内脏为主」），
    哪些该拦是个人的事。指标恢复了就该把对应的 flag 删掉 ——
    这是它和忌口最大的区别，忌口永远不会因为体检好转而改变。
    """
    return _dedupe(str(x) for x in (_diet().get("medical_blocks") or []))


# ── 第 3 层：目标阶段 ───────────────────────────────────────────────────


def load_phase() -> tuple[str, bool]:
    """返回 (阶段, 是否用了兜底默认值)。

    兜底要能被看见 —— 阶段错了，额度和权重全错，而这两样都是无声的。
    所以输出里会明说这次按哪个阶段算。
    """
    phase = _diet().get("phase")
    if phase in PHASES:
        return phase, False
    return DEFAULT_PHASE, True


def red_quota(phase: str | None = None) -> int:
    """破戒额度。显式配置优先，否则按阶段取。"""
    diet = _diet()
    explicit = diet.get("red_quota_per_month")
    if explicit is not None:
        return int(explicit)
    if phase is None:
        phase, _ = load_phase()
    return PHASES[phase]["quota"]


# ── 第 4 层：偏好 ───────────────────────────────────────────────────────


def load_prefs() -> tuple[list[str], list[str]]:
    """(爱吃, 不爱吃)。都是软的 —— 只调概率，不做过滤。

    和忌口的区别：忌口是「不吃」，这里是「不太想吃」。
    把「不太想吃」做成硬过滤，池子会被悄悄掏空。
    """
    diet = _diet()
    return (_dedupe(str(x) for x in (diet.get("likes") or [])),
            _dedupe(str(x) for x in (diet.get("dislikes") or [])))


# ── 摇过什么 ────────────────────────────────────────────────────────────


def _breakable(dish: dict) -> bool:
    """要消耗破戒额度的那一类：红灯，或嘌呤高/极高。

    嘌呤和灯合并进同一个额度，是因为对高尿酸的人，
    一顿高嘌呤的「绿灯」和一顿红灯的实际代价是一个量级的。
    分成两套额度只会让人记不住。

    注意这和**第 2 层医学禁忌**不是一回事：医学禁忌是硬墙，不消耗额度，
    因为它压根不进池子。
    """
    return dish["tier"] == "红" or dish["purine"] in ("高", "极高")


def load_rolls() -> list[dict]:
    return store.read_jsonl(LOG_PATH)


def settled_rolls(rolls: Iterable[dict] | None = None, *,
                  exclude: tuple[str, str] | None = None) -> list[dict]:
    """回放：同一 (日期, 餐次) 只有最后一次算数，之前的都是被 --again 作废的。

    和教练工作日志一样只追加不修改 —— 「我摇了三次才接受」这件事本身
    是有信息量的，不该被覆盖掉。

    `exclude` 排除某个 (日期, 餐次)。两种情况必须用它，否则同一顿会被数两遍：
      · `--again` 重摇 —— 即将被顶替的那次不该占额度
      · 回放已摇过的那餐 —— 它已经在账上了，再减一次就成了「超额」
    """
    rolls = load_rolls() if rolls is None else rolls
    latest: dict[tuple[str, str], dict] = {}
    for r in rolls:
        if r.get("rec") != "roll":
            continue
        key = (r.get("date", ""), r.get("slot", ""))
        if exclude and key == exclude:
            continue
        latest[key] = r
    return sorted(latest.values(),
                  key=lambda r: (r.get("date", ""), r.get("rolled_at", "")))


def current_roll(date: str, slot: str) -> dict | None:
    for r in settled_rolls():
        if r.get("date") == date and r.get("slot") == slot:
            return r
    return None


def red_used(month: str, rolls: list[dict] | None = None) -> list[dict]:
    """本月已消耗的破戒额度。`breakable` 存在记录里，池子改了也不影响历史。"""
    return [r for r in (rolls if rolls is not None else settled_rolls())
            if r.get("date", "").startswith(month) and r.get("breakable")]


def append_roll(rec: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(store.dumps(rec) + "\n")


# ── 摇 ──────────────────────────────────────────────────────────────────


def default_slot(now: dt.datetime) -> str:
    h = now.hour + now.minute / 60
    if h < 10.5:
        return "早"
    if h < 15:
        return "午"
    if h < 21:
        return "晚"
    return "加餐"


# 筛选链上每一层的名字。顺序就是执行顺序，输出里照这个顺序打印。
LAYERS = ("场景", "忌口", "医学禁忌", "目标层", "偏好层", "近期重复")


def _recency_factor(name: str, today: dt.date, settled: list[dict]) -> float:
    last: dt.date | None = None
    for r in settled:
        if r.get("dish") != name:
            continue
        try:
            d = dt.date.fromisoformat(r.get("date", ""))
        except ValueError:
            continue
        if last is None or d > last:
            last = d
    if last is None:
        return 1.0
    days = (today - last).days
    if days < 0:
        return 1.0
    for limit, factor in RECENCY_W:
        if days <= limit:
            return factor
    return 1.0


def roll(
    *,
    slot: str,
    scene: str | None = None,
    cuisine: str | None = None,
    effort: str | None = None,
    today: dt.date | None = None,
    seed: int | None = None,
    allow_red: bool = False,
    min_protein: str | None = None,
    again: bool = False,
) -> dict:
    """摇一次。返回结果字典，**不写日志** —— 写盘由调用方决定。

    分开的理由和 `hc journal` 一样：只读的路径要真的只读，
    这样 `--dry-run`、测试、和「看看池子里都有啥」都不会污染额度。
    """
    import random

    today = today or dt.date.today()
    seed = secrets.randbelow(2**32) if seed is None else seed
    rng = random.Random(seed)

    pool, pool_issues = load_pool_with_issues()
    walls = load_avoid()
    blocks = load_medical_blocks()
    phase, phase_defaulted = load_phase()
    cfg = PHASES[phase]
    likes, dislikes = load_prefs()

    # 重摇时，即将被顶替的那一次不该继续占额度、也不该继续压自己的近期权重。
    supersedes = (today.isoformat(), slot) if again else None
    settled = settled_rolls(exclude=supersedes)
    month = today.strftime("%Y-%m")
    quota = red_quota(phase)
    used = red_used(month, settled)
    quota_left = max(0, quota - len(used))

    week_start = today - dt.timedelta(days=today.weekday())
    yellow_this_week = sum(
        1 for r in settled
        if r.get("tier") == "黄"
        and week_start.isoformat() <= r.get("date", "") <= today.isoformat()
    )

    # 每一层筛掉了几道。这是输出里那条筛选链的数据来源 ——
    # 骰子只给一个答案，不摊开过程就没法被信任。
    dropped = {k: 0 for k in LAYERS}
    survivors = {k: 0 for k in LAYERS}
    candidates: list[tuple[dict, float]] = []
    remaining = list(pool)

    # ── 第 0 层：场景 ──
    kept = []
    for d in remaining:
        if (slot not in d["slots"]
                or (scene and scene not in d["scenes"])
                or (cuisine and d["cuisine"] != cuisine)
                or (effort and d["effort"] != effort)
                or (min_protein
                    and PROTEINS.index(d["protein"]) > PROTEINS.index(min_protein))):
            dropped["场景"] += 1
        else:
            kept.append(d)
    remaining, survivors["场景"] = kept, len(kept)

    # ── 第 1 层：忌口 / 过敏（硬，永不放行）──
    kept = []
    for d in remaining:
        if blocked_by(d, walls):
            dropped["忌口"] += 1
        else:
            kept.append(d)
    remaining, survivors["忌口"] = kept, len(kept)

    # ── 第 2 层：医学禁忌（硬，指标恢复才解除）──
    kept = []
    for d in remaining:
        if any(f in blocks for f in d["flags"]):
            dropped["医学禁忌"] += 1
        else:
            kept.append(d)
    remaining, survivors["医学禁忌"] = kept, len(kept)

    # ── 第 3 层：目标层（软，有额度）──
    kept = []
    for d in remaining:
        if _breakable(d) and not allow_red and quota_left <= 0:
            dropped["目标层"] += 1
        else:
            kept.append(d)
    remaining, survivors["目标层"] = kept, len(kept)

    # ── 第 4/5 层：偏好 + 加权 ──
    for d in remaining:
        w = (cfg["tier_w"][d["tier"]]
             * cfg["protein_w"][d["protein"]]
             * PURINE_W[d["purine"]])
        if _breakable(d) and allow_red:
            w = max(w, 1.0)  # 明确要求破戒时，别让权重把它压得摇不出来
        if d["tier"] == "黄" and yellow_this_week >= YELLOW_WEEK_CAP:
            w *= YELLOW_STREAK_W
        if any(x in d["name"] or x == d["cuisine"] for x in likes):
            w *= LIKE_W
        if any(x in d["name"] or x == d["cuisine"] for x in dislikes):
            w *= DISLIKE_W

        rf = _recency_factor(d["name"], today, settled)
        if rf <= 0.05:
            # 「摇过」不等于「吃过」—— 日志记的是决定，不是入口的东西。
            # 这个区别要在措辞上守住，不然骰子会开始假装自己知道你吃了什么。
            dropped["近期重复"] += 1
            continue
        w *= rf
        if w > 0:
            candidates.append((d, w))
    survivors["偏好层"] = len(remaining)
    survivors["近期重复"] = len(candidates)

    from . import nutrition

    result = {
        "date": today.isoformat(),
        "slot": slot,
        "scene": scene,
        "cuisine": cuisine,
        "effort": effort,
        "seed": seed,
        "targets": nutrition.targets(today),
        "pool_total": len(pool),
        "candidates": len(candidates),
        "dropped": dropped,
        "survivors": survivors,
        "phase": phase,
        "phase_defaulted": phase_defaulted,
        "phase_why": cfg["why"],
        "quota": quota,
        "quota_left": quota_left,
        "quota_used": used,
        "yellow_this_week": yellow_this_week,
        "avoid": walls.all_terms(),
        "allergy": walls.allergy,
        "blocks": blocks,
        "likes": likes,
        "warn": walls.warn,
        "pool_issues": pool_issues,
        "dish": None,
        "alternates": [],
    }
    if not candidates:
        return result

    picks = _weighted_sample(rng, candidates, 3)
    result["dish"] = picks[0]
    result["alternates"] = picks[1:]
    return result


def _weighted_sample(rng, candidates: list[tuple[dict, float]], k: int) -> list[dict]:
    """按权重不放回抽 k 个。第一个是结果，其余是备选。

    备选也按权重抽，是有意的 —— 备选是「你可以直接换的那两个」，
    不是「池子里剩下的随便两个」，所以它们得同样过得了筛选和加权。
    """
    pool = list(candidates)
    out: list[dict] = []
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        if total <= 0:
            break
        x = rng.random() * total
        acc = 0.0
        for i, (d, w) in enumerate(pool):
            acc += w
            if x <= acc:
                out.append(d)
                pool.pop(i)
                break
        else:
            out.append(pool.pop()[0])
    return out


def commit(result: dict) -> dict:
    """把一次摇的结果写进日志。返回写进去的那条记录。"""
    d = result["dish"]
    rec = {
        "rec": "roll",
        "date": result["date"],
        "slot": result["slot"],
        "scene": result.get("scene"),
        "dish": d["name"],
        "cuisine": d.get("cuisine"),
        "tier": d["tier"],
        "purine": d["purine"],
        "protein": d["protein"],
        "breakable": _breakable(d),
        "phase": result.get("phase"),
        "seed": result["seed"],
        "rolled_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    append_roll(rec)
    return rec


# ── 输出 ────────────────────────────────────────────────────────────────


def _width(s: str) -> int:
    """终端显示宽度。中文占两列，用 len() 对齐会歪掉。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _width(s))


def _brief(items: list[str], limit: int = 4) -> str:
    """长清单截断。筛选链是给人扫一眼的，不是给人读完的。"""
    if not items:
        return "无"
    if len(items) <= limit:
        return " ".join(items)
    return " ".join(items[:limit]) + f" …{len(items)} 项"


def _chain_lines(result: dict) -> list[str]:
    """筛选链。骰子只给一个答案，不摊开过程就没法被信任。"""
    labels = {
        "场景": " ".join(
            x for x in [result["slot"], result.get("scene"), result.get("cuisine"),
                        result.get("effort")] if x),
        # 过敏单独标出来 —— 它和忌口的硬度不一样，混在一起看不出区别
        "忌口": (_brief([f"⛔{a}" for a in result.get("allergy") or []]
                       + [a for a in result["avoid"]
                          if a not in (result.get("allergy") or [])])),
        "医学禁忌": _brief(result["blocks"]),
        "目标层": f"{result['phase']} · 额度 {result['quota_left']}/{result['quota']}",
        "偏好层": _brief(result["likes"], 3),
        "近期重复": "14 天内摇过的降权",
    }
    lines = [f"  筛选链  池子 {result['pool_total']} 道"]
    items = list(LAYERS)
    for i, layer in enumerate(items):
        stem = "└" if i == len(items) - 1 else "├"
        drop = result["dropped"][layer]
        left = result["survivors"][layer]
        drop_s = f"-{drop}" if drop else ""
        lines.append(f"    {stem} {_pad(layer, 10)}{_pad(labels[layer], 30)}"
                     f"{drop_s:>5} → {left}")
    return lines


def _nutrition_lines(name: str, targets: dict | None) -> list[str]:
    """每 100 g 估算 + 今天的目标摄入量。

    刻意**不算「这顿吃了多少」** —— 那需要知道份量，而份量是这里唯一
    完全无从得知的变量。给每 100 g 和今日目标，让用户自己对着看，
    比编一个份量再乘出来诚实得多。
    """
    from . import nutrition

    n = nutrition.dish_per100g(name)
    if not n and not (targets and targets.get("ok")):
        return []

    lines: list[str] = []
    if n:
        share = nutrition.protein_share(n)
        lines.append(f"  每 100 g  {n['kcal']} kcal · 蛋白 {n['protein_g']} g "
                     f"· 脂肪 {n['fat_g']} g · 碳水 {n['carb_g']} g"
                     f"   蛋白供能 {share:.0%}")
    if targets and targets.get("ok"):
        t = targets
        lines.append(f"  今日目标  {t['kcal']} kcal · 蛋白 {t['protein_g']} g "
                     f"· 脂肪 {t['fat_g']} g · 碳水 {t['carb_g']} g"
                     f"   （{t['phase']}期 · {t['weight']} kg）")
    if n:
        lines.append("  ⚠️  每 100 g 是按典型做法的配比估算的，不同店油量差异大，"
                     "按 ±30% 理解；份量未知，所以不替你算这顿吃了多少")
    lines.append("")
    return lines


def render(result: dict, *, replayed: bool = False) -> str:
    """摇出来的样子。措辞刻意保持中性 —— 解读和劝说是模型的事，不是脚本的事。"""
    lines: list[str] = []
    head = f"🎲  {result['date']} {result['slot']}饭"
    if result.get("scene"):
        head += f" · {result['scene']}"
    lines.append(head)
    lines.append("")

    if result.get("warn"):
        lines.append(f"  ⚠️  {result['warn']}")
        lines.append("")
    for issue in result.get("pool_issues") or []:
        lines.append(f"  ⚠️  候选池：{issue}")
    if result.get("pool_issues"):
        lines.append("")

    d = result.get("dish")
    if not d:
        lines.append("  池子里没有符合条件的菜。")
        lines.append("")
        lines += _chain_lines(result)
        lines.append("")
        lines.append("  放宽条件：去掉 --scene / --cuisine，或 hc dice --allow-red；")
        lines.append("  池子太窄就往里加菜：hc dice add --name 〇〇 ...")
        return "\n".join(lines)

    lines.append(f"  {d['name']}")
    meta = f"  {TIER_LABEL[d['tier']]} · 嘌呤{d['purine']} · 蛋白{d['protein']}"
    if d.get("cuisine") and d["cuisine"] != "其他":
        meta += f" · {d['cuisine']}"
    if d.get("effort") == "快手":
        meta += " · 快手"
    lines.append(meta)
    if d.get("note"):
        lines.append(f"  {d['note']}")
    lines.append("")

    lines += _nutrition_lines(d["name"], result.get("targets"))

    if d.get("fix"):
        lines.append("  怎么点")
        for f in d["fix"]:
            lines.append(f"    · {f}")
    if d["protein"] in ("中", "低"):
        lines.append(f"    · 蛋白密度只有「{d['protein']}」，"
                     f"这顿之外补一份（蛋 / 乳清 / 无糖酸奶）")
    lines.append("")

    if result["alternates"]:
        alts = " / ".join(a["name"] for a in result["alternates"])
        lines.append(f"  备选   {alts}")
    again = "  换一个 hc dice --again"
    if result.get("slot"):
        again += f" --slot {result['slot']}"
    if result.get("scene"):
        again += f" --scene {result['scene']}"
    lines.append(again)
    lines.append("")

    if not replayed:
        lines += _chain_lines(result)

    if _breakable(d):
        if result["quota_left"] <= 0:
            # 只有 --allow-red 能走到这儿。额度是用户自己定的上限，
            # 所以这里只陈述事实，不劝阻 —— 他自己定的上限，他自己有权超。
            over = len(result["quota_used"]) - result["quota"] + 1
            lines.append(f"  ⚠️  本月破戒额度（{result['phase']} {result['quota']} 次）已用完，"
                         f"这顿是超出的第 {over} 次")
        else:
            lines.append(f"  ⚠️  这顿消耗本月破戒额度，"
                         f"用掉后剩 {result['quota_left'] - 1}/{result['quota']}")

    if result["yellow_this_week"] >= YELLOW_WEEK_CAP:
        lines.append(f"  本周黄灯已 {result['yellow_this_week']} 次，黄灯这次降权")

    if result.get("phase_defaulted"):
        lines.append(f"  ⚠️  没设阶段，按「{DEFAULT_PHASE}」算。"
                     f"设一下：data/profile.json 的 diet.phase（{'/'.join(PHASE_NAMES)}）")

    lines.append(f"  seed {result['seed']}")
    if replayed:
        lines.insert(1, "  （今天这一餐已经摇过了，下面是原来那次。想换用 --again）")
    return "\n".join(lines)
