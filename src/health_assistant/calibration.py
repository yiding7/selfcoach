"""负荷口径归一化 —— 同一个动作，换台机器，数字不该断。

## 要解决的问题

绳索器械是滑轮组，配重片上的数字取决于传动比。面拉在 A 机位标 50 kg、
在 B 机位标 25 kg，手上受的力可能完全一样。脚本按动作名老实配对，
就会算出「估算 1RM ↓ 50%」—— 数字没算错，含义是假的。

（这里和下面的数都是**编的示例**，取整以便一眼看出不是谁的记录。纪律见
`CLAUDE.md` 的「示例数据一律用整数 placeholder」。）

机制和完整测法见 `knowledge/measurement/load-measurement.md`。

## 为什么不写进动作名

`面拉（龙门2:1）` 能解决对比问题，但它是**把口径问题伪装成动作问题**：
动作明明是同一个，却因为器械不同被拆成两条互不相干的曲线，
纵向进步就永远看不出来了。用户 2026-08-10 明确否掉了这个方案。

正确的做法是**归一化**：动作名保持不变，把负荷折算到同一个口径上。

## 三层，各管各的

    data/training/**.jsonl        原始记录。**永远不改**
    data/load-calibration.jsonl   口径规则。只追加，不修改
    读取时                         store.load_sessions() 现场折算

原始数据不动是硬要求。改原始数据会毁掉「这份记录换个模型也能复算」这个前提，
而且一旦发现折算错了就再也回不去了。规则文件只追加、可以被 supersede，
半年后回看能知道「当时为什么这么折算」。

## 前提：记录口径先统一

折算只在「记录本身有一条固定规矩」时才成立。用户 2026-08-25 定死的规矩是：

    自由重量（杠铃）   记 **杆 + 片**
    器械               只记 **片重**

于是每台机器和「真实的力」之间只差一个固定的变换，而那个变换是机器的属性。

## 两种变换，就这两种

| 变换 | action | 公式 | 用在哪 |
|---|---|---|---|
| 乘法 | `scale` | `实际 = 记录 × ratio` | 绳索 / 龙门的滑轮传动比 |
| 加法 | `offset` | `实际 = 记录 + offset_kg` | 哈克 / 腿举的滑车自重；引体的配重与助力 |

`offset_kg` **可以是负的**：引体向上挂 10kg 配重是 `+10`，套助力带减 15kg 是 `-15`。
同一个动作名，两种做法，一正一负，不用把它拆成两条曲线。

**两种都挂在「馆 + 动作」上，没有日期。** 传动比和滑车自重是那台机器的物理属性，
不随日期变化。用日期表达「在哪台机器上练的」，每换一次馆就要补一条规则，
补漏一条就静默错一次。用户 2026-08-23 拍板作用域，2026-08-25 砍掉日期维度。

## 三层，各管各的

    data/training/**.jsonl        原始记录。**永远不改**
    data/load-calibration.jsonl   口径规则。只追加，不修改
    读取时                         store.load_sessions() 现场折算

原始数据不动是硬要求。改原始数据会毁掉「这份记录换个模型也能复算」这个前提，
而且一旦发现折算错了就再也回不去了。规则文件只追加、可以被 supersede，
半年后回看能知道「当时为什么这么折算」。

## 用户看到预警时的三个选择

| 用户想做的 | 怎么做 |
|---|---|
| 改原始记录数据 | 不写规则。去训记改，然后 `hc sync train --since X --until X --force` |
| 这台机器就是和别台不一样 | `scale` 或 `offset`，挂在那个馆的那个动作上 |
| 确认这是真实变化 | `confirm`，只标那一天。**数据一个字不动** |

`confirm` 是要留痕的：「我看过了，这是真的」和「还没人看过」是两回事，
后者不该被静默当成前者。它不是变换，所以不算在上面那两种里。

> **原来还有第四个选项 `ignore`（这次该动作不参与对比），2026-08-25 删掉了。**
> 它存在的两条理由 —— 腿举和哈克的历史「只记片重 vs 记片重+start」—— 在记录
> 口径统一之后都变成了一条 `offset` 规则。而 `ignore` 是这张表里唯一会**藏数据**
> 的动作，能不留就不留。文件里的旧 `ignore` 行不删（只追加），
> 但它们不再生效，`hc calib list` 会把它们单独列出来说明。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from . import store
from .config import DATA_DIR

PATH = DATA_DIR / "load-calibration.jsonl"
SCHEMA = "ha.calib/1"

# 两种变换 + 一个「我看过了」。`ignore` 2026-08-25 删掉 —— 见模块顶部。
ACTIONS = ("scale", "offset", "confirm")
TRANSFORMS = ("scale", "offset")     # 真的改数字的那两种，必须挂在馆上
RETIRED_ACTIONS = ("ignore",)        # 旧文件里可能还有，读到就说出来，不静默跳过

# ── 预警阈值 ────────────────────────────────────────────────────────────
#
# 只有「负荷大幅跳变 **而次数基本没变**」才算可疑。这个组合是关键：
# 重量减半、次数翻倍是有意的训练安排（换了次数区间），不该预警；
# 重量减半、次数原地不动才是换了把尺。上面那个面拉的例子正是后者
# —— 50kg → 25kg，两次的总次数都是 30。
JUMP_HIGH = 1.7          # 涨到 1.7 倍以上
JUMP_LOW = 1.0 / 1.7     # 或掉到 0.59 倍以下
REPS_STABLE_LOW = 0.6    # 同时总次数变化在 ±40% 以内
REPS_STABLE_HIGH = 1 / REPS_STABLE_LOW

# 下面两条是**降噪**用的，不放会淹掉真信号。两条都是拿真实数据调出来的：
# 未加限制时 10 条命中里有 5 条是新手期的正常进步。
#
# 绝对重量下限：小重量下一个哑铃档位就是巨大的比值。2.5kg → 10kg 的俯身飞鸟
# 是 ×4，但那只是从最轻的哑铃换到了第三档，没有任何异常。
MIN_LOAD_KG = 10.0
# 间隔上限：隔了一个多月翻倍是进步，隔十天翻倍才是换了把尺。
# 取 35 天而不是 21 —— 用户的绳索动作间隔实测到过 25 天，卡太紧会漏掉真的。
MAX_GAP_DAYS = 35

# 滑轮组动作 —— 只用来给预警文案加一句「这类最可能是传动比」，
# **不参与判断**。名字里没有「绳索」的面拉、下压、夹胸也在其中，
# 所以不能只靠 equipment_patterns 那套正则。
PULLEY_HINT = re.compile("绳索|滑轮|龙门|拉力|面拉|下压|夹胸|下拉|坐姿划船|高位")


@dataclass(frozen=True)
class Rule:
    id: str
    ts: str
    movement: str
    action: str
    ratio: float | None = None
    # 只对这个馆生效。`scale` / `offset` **必须**有，`confirm` 必须没有。
    #
    # **这是比日期更对的作用域。** 传动比和滑车自重是那台机器的物理属性 ——
    # 某个馆的龙门今天是 2:1、明天还是 2:1，它不随日期变化。用日期区间去表达
    # 「在哪台机器上练的」，每换一次馆就要补一条规则，而且补漏一条就静默错一次。
    # 挂在馆上则是一次定义、永久生效。
    gym: str | None = None
    # 加法常数，kg。**可以是负的**：引体挂 10kg 配重是 +10，套助力带是 -15。
    offset_kg: float | None = None
    # 只有 `confirm` 用：确认的是哪一天那一次。变换类规则没有日期。
    date: str | None = None
    note: str = ""
    # 推翻了哪几条旧规则。**可以是多条** —— 一条 offset 规则同时取代
    # 「腿举的 ignore」和「更早那版 offset」是常有的事，逼人一条条拆开写，
    # 只会让人干脆不写，然后旧规则永远挂在那儿。
    supersedes: tuple[str, ...] = ()

    def covers(self, movement: str, gym: str | None = None) -> bool:
        """这条变换规则适不适用于「某个馆的某个动作」。

        `gym` 是 `None` 表示**那次不知道在哪练的** —— 一律不适用。
        「不知道」绝不能当成「就是那个馆」：没标场地的历史记录会被一条
        后来才定义的折算规则悄悄改掉，那是最难查的一种错。
        """
        return (movement == self.movement
                and self.action in TRANSFORMS
                and bool(self.gym) and gym == self.gym)

    def describe(self) -> str:
        what = {
            "scale": f"折算 ×{self.ratio:g}" if self.ratio else "折算",
            "offset": (f"{self.offset_kg:+g}kg" if self.offset_kg is not None
                       else "加常数"),
            "confirm": "已确认为真实数据",
        }.get(self.action, self.action)
        scope = f"@{self.gym}" if self.gym else (self.date or "—")
        return f"[{self.id}] {self.movement}　{scope}　{what}"


def _rules_raw() -> list[dict]:
    try:
        return store.read_jsonl(PATH)
    except OSError:
        return []


def _supersedes(value) -> tuple[str, ...]:
    """`"A"` / `"A,B"` / `["A","B"]` 都认。空的一律是空元组。"""
    if not value:
        return ()
    if isinstance(value, str):
        return tuple(x.strip() for x in value.split(",") if x.strip())
    return tuple(str(x).strip() for x in value if str(x).strip())


def _num(row: dict, *keys):
    """取第一个能转成 float 的字段。多个 key 是为了兼容改过名的旧文件。"""
    for k in keys:
        if row.get(k) is None:
            continue
        try:
            return float(row[k])
        except (TypeError, ValueError):
            return None
    return None


def load_rules() -> list[Rule]:
    """读全部生效规则。被 supersede 掉的不返回，但它们仍留在文件里。

    jsonl 没有注释语法，所以文件头上那行 `{"_comment": [...]}` 是靠
    「action 不在 ACTIONS 里就跳过」被忽略的。写规则的人打开文件第一眼
    就该看见怎么填 —— 把用法藏在文档里，等于赌他会去翻文档。
    """
    rows = _rules_raw()
    dead = {d for r in rows for d in _supersedes(r.get("supersedes"))}
    out = []
    for r in rows:
        if r.get("id") in dead or r.get("action") not in ACTIONS:
            continue
        gym = str(r.get("gym") or "").strip() or None
        # date_from/date_to 是 2026-08-25 之前的字段。变换类规则不再有日期区间，
        # 但旧行里可能有单日的 confirm 写成了 from == to。
        date = r.get("date") or r.get("date_to") or r.get("date_from")
        out.append(Rule(
            id=str(r.get("id") or ""), ts=str(r.get("ts") or ""),
            movement=str(r.get("movement") or ""), action=str(r["action"]),
            ratio=_num(r, "ratio"), gym=gym,
            # `start_kg` 是旧名字。语义没变（滑车自重也是个加法常数），
            # 只是新名字容得下负数 —— 助力带是负的 offset，叫「起始重量」讲不通。
            offset_kg=_num(r, "offset_kg", "start_kg"),
            date=str(date) if date else None,
            note=str(r.get("note") or ""),
            supersedes=_supersedes(r.get("supersedes"))))
    return out


def retired_rows() -> list[dict]:
    """文件里那些 action 已经不再支持的行。

    **不能静默跳过。** 一条写在文件里、看起来生效、实际不生效的规则，
    是这个项目最不能接受的那种错。`hc calib list` 会把它们单独列出来。
    """
    rows = _rules_raw()
    dead = {d for r in rows for d in _supersedes(r.get("supersedes"))}
    return [r for r in rows
            if r.get("id") not in dead and r.get("action") in RETIRED_ACTIONS]


def _next_id(today: dt.date) -> str:
    prefix = today.strftime("%Y%m%d")
    n = sum(1 for r in _rules_raw() if str(r.get("id", "")).startswith(prefix))
    return f"{prefix}-{n + 1:02d}"


def add_rule(movement: str, action: str, *, ratio: float | None = None,
             gym: str | None = None, offset_kg: float | None = None,
             date: str | None = None, note: str = "",
             supersedes: str | list[str] | None = None,
             today: dt.date | None = None) -> Rule:
    # 动作名为空的规则永远匹配不上（covers 先比 movement），但文件只追加，
    # 这行垃圾就永久留在那儿了。宁可在这里拒绝，也不要写一条死规则。
    movement = (movement or "").strip()
    if not movement:
        raise ValueError("必须给动作名 —— 空名字的规则永远匹配不到任何记录")
    if action not in ACTIONS:
        raise ValueError(f"action 只能是 {ACTIONS} 之一，收到 {action!r}")
    if action == "scale" and (not ratio or ratio <= 0):
        raise ValueError("scale 必须给一个正的 ratio")
    # 0 是「不用改」，写成规则毫无意义，但会让人以为这个动作已经标定过了。
    # 负数是**合法的**：助力带就是负的 offset。
    if action == "offset" and not offset_kg:
        raise ValueError("offset 要给一个非 0 的 kg 数（配重为正，助力为负）")
    gym = (gym or "").strip() or None

    # 变换类规则挂在「馆 + 动作」上，没有日期；confirm 反过来。
    # 两个作用域搅在一起，半年后没人说得清一条记录到底被哪一条改过。
    if action in TRANSFORMS:
        if not gym:
            raise ValueError(
                f"{action} 规则必须指定 --gym。传动比和滑车自重是那台机器的属性，"
                "不挂在馆上就没法说清它作用于哪些记录")
        if date:
            raise ValueError("变换类规则不要带日期 —— 机器的属性不随日期变化")
    else:
        if gym:
            raise ValueError("confirm 是对某一次的确认，不要带 --gym")
        if not date:
            raise ValueError("confirm 要指定 --date（确认的是哪一天那一次）")
    if date:
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise ValueError(f"--date 的格式不对：{date!r}，要 YYYY-MM-DD") from None

    today = today or dt.date.today()
    rule = Rule(id=_next_id(today),
                ts=dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
                movement=movement, action=action, ratio=ratio,
                gym=gym, offset_kg=offset_kg, date=date, note=note,
                supersedes=_supersedes(supersedes))
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with PATH.open("a", encoding="utf-8") as fh:
        fh.write(store.dumps({
            "schema": SCHEMA, "id": rule.id, "ts": rule.ts,
            "movement": rule.movement, "action": rule.action, "ratio": rule.ratio,
            "gym": rule.gym, "offset_kg": rule.offset_kg, "date": rule.date,
            "note": rule.note,
            "supersedes": list(rule.supersedes) or None}) + "\n")
    return rule


# ── 应用到会话 ──────────────────────────────────────────────────────────

def _scalable(m: dict) -> bool:
    """自重、辅助、计时类动作不能乘系数。

    自重动作的重量是按体重折算出来的，辅助器械记的是助力（方向相反），
    计时类根本没有重量。对这三类乘系数只会造出一个假数字。
    """
    return m.get("exetype") not in ("times", "plus_weight", "help", "record")


def apply_rules(sessions: list[dict], rules: list[Rule] | None = None) -> list[dict]:
    """把口径规则应用到会话上。**返回新对象，不改原始文件。**

    这是唯一的折算入口，挂在 `store.load_sessions()` 里，所以
    `hc summary` / `hc compare` / `hc next` / `hc report` 看到的都是折算后的值。
    写盘路径（sync / rebuild / log）走的是 `load_sessions_month`，不经过这里 ——
    这一点是刻意的：折算绝不能被烘进原始文件。
    """
    rules = load_rules() if rules is None else rules
    if not rules:
        return sessions

    confirmed = {(r.movement, r.date) for r in rules if r.action == "confirm"}
    out = []
    for s in sessions:
        date = s.get("date", "")
        gym = s.get("gym") or None
        new_movements, touched = [], False
        for m in s.get("movements") or []:
            name = m.get("name") or ""
            hits = [r for r in rules if r.covers(name, gym)]
            if not hits and (name, date) not in confirmed:
                new_movements.append(m)
                continue

            m2 = dict(m)
            marks: dict = {}
            # 后写的规则优先 —— 文件是追加的，所以取最后一条同类
            scale = next((r for r in reversed(hits) if r.action == "scale"), None)
            if scale and scale.ratio and _scalable(m):
                m2["sets"] = [
                    {**st,
                     "weight_kg": (st["weight_kg"] * scale.ratio
                                   if st.get("weight_kg") is not None else None),
                     "left_weight_kg": (st["left_weight_kg"] * scale.ratio
                                        if st.get("left_weight_kg") is not None else None)}
                    for st in (m.get("sets") or [])]
                marks["ratio"] = scale.ratio
                marks["rule_id"] = scale.id
            # offset 是**加法**常数，和传动比那个乘法常数是两回事。
            # 先乘后加：传动比作用在配重片读数上，滑车自重（或配重/助力）
            # 是读数之外另加的一块。
            # 两者同时命中同一个动作在现实里几乎不会发生，但顺序必须定死，
            # 否则同一份数据在两次运行里可能得出不同的结果。
            off = next((r for r in reversed(hits) if r.action == "offset"), None)
            if off and off.offset_kg and _scalable(m):
                base = m2.get("sets") or m.get("sets") or []
                m2["sets"] = [
                    {**st,
                     "weight_kg": (st["weight_kg"] + off.offset_kg
                                   if st.get("weight_kg") is not None else None),
                     "left_weight_kg": (st["left_weight_kg"] + off.offset_kg
                                        if st.get("left_weight_kg") is not None else None)}
                    for st in base]
                marks["offset_kg"] = off.offset_kg
                marks["rule_id"] = off.id
            if (name, date) in confirmed:
                marks["confirmed"] = True

            if marks:
                m2["_calib"] = marks
                touched = True
            new_movements.append(m2)

        out.append({**s, "movements": new_movements} if touched else s)
    return out


# ── 检测 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Jump:
    movement: str
    date: str
    prev_date: str
    load: float
    prev_load: float
    reps: float
    prev_reps: float
    gap_days: int = 0
    resolved_by: str | None = None    # 已被哪条规则覆盖；None = 待处理

    @property
    def ratio(self) -> float:
        return self.load / self.prev_load

    @property
    def pulley_suspect(self) -> bool:
        return bool(PULLEY_HINT.search(self.movement))

    def headline(self) -> str:
        arrow = "↑" if self.ratio > 1 else "↓"
        return (f"{self.movement}　{self.prev_date} {self.prev_load:g}kg "
                f"→ {self.date} {self.load:g}kg {arrow} ×{self.ratio:.2f}"
                f"　相隔 {self.gap_days} 天，次数 {self.prev_reps:g} → {self.reps:g}")


def explanation(pulley: bool) -> list[str]:
    """给用户看的解释。措辞可以由模型润色，但这几条事实由脚本给，保证不走样。

    刻意做成模块级函数而不是 Jump 的方法：一次预警里往往有好几条跳变，
    解释只该说一遍。每条都重复一遍相同的三段话，用户就不看了。
    """
    lines = [
        "负荷大幅跳变、而次数留在同一个量级 —— 这个组合通常不是力量变化，"
        "是**换了把尺**。真的变强会表现为「重量涨、次数掉回区间下沿」，"
        "而不是两个数各走各的。",
    ]
    if pulley:
        lines += [
            "滑轮组动作最可能的原因是**传动比不同**：配重片和你手之间有几段绳，"
            "力就被分掉几份。同一个动作在 1:1 和 2:1 的机位上，"
            "标称重量差一倍，而手上的力一模一样。",
            "怎么测（30 秒，不用工具）：把手拉一段距离，看配重片升了多高。"
            "传动比 = 手移动的距离 ÷ 配重片上升的距离。"
            "手拉 60cm、配重片只升 30cm 就是 2:1，手上的力是标称的一半。",
        ]
    lines.append(
        "其他常见原因：换了器械、史密斯杆自重不同、单侧动作记法变了"
        "（单手 vs 双手合计）、输入时点错了一档。")
    lines.append("完整说明见 knowledge/measurement/load-measurement.md。")
    return lines


def detect_jumps(stats, rules: list[Rule] | None = None) -> list[Jump]:
    """扫描同一动作相邻两次的负荷跳变。

    `stats` 是 SessionStats 列表（按日期升序）。只看同名动作 ——
    跨动作比负荷本来就没意义，那是 compare 层已经挡掉的事。
    """
    rules = load_rules() if rules is None else rules
    confirmed = {(r.movement, r.date) for r in rules if r.action == "confirm"}
    seen: dict[str, tuple[str, float, float, str | None]] = {}
    jumps: list[Jump] = []

    for s in stats:
        for m in s.movements:
            if m.bodyweight or m.assisted or m.timed:
                continue
            if not m.top_load_kg or m.sets_done == 0:
                continue
            prev = seen.get(m.name)
            seen[m.name] = (s.date, m.top_load_kg, m.reps_total, s.gym)
            if not prev:
                continue
            prev_date, prev_load, prev_reps, prev_gym = prev
            if not prev_load:
                continue
            # **换馆已经解释了这个跳变，不用再问一遍。**
            # 两边都标了场地、场地不同、而这个动作的负荷本来就不跨馆成立
            # （器械/绳索/史密斯…，判据表见 site-dependence.json）—— 那么
            # 「数字变了」是必然的，不是发现。hc compare 那一侧已经把这类
            # 对比的负荷置空了，这里再报一次只会把真信号淹掉：实测 6 条命中
            # 里有 5 条是这种。自由重量不在此列，64kg 在哪个馆都是 64kg。
            if (prev_gym and s.gym and prev_gym != s.gym
                    and not m.load_portable):
                continue
            if prev_load < MIN_LOAD_KG or m.top_load_kg < MIN_LOAD_KG:
                continue
            try:
                gap = (dt.date.fromisoformat(s.date)
                       - dt.date.fromisoformat(prev_date)).days
            except ValueError:
                continue
            if gap > MAX_GAP_DAYS:
                continue

            ratio = m.top_load_kg / prev_load
            if JUMP_LOW < ratio < JUMP_HIGH:
                continue
            if prev_reps and m.reps_total:
                rr = m.reps_total / prev_reps
                if not (REPS_STABLE_LOW <= rr <= REPS_STABLE_HIGH):
                    continue    # 次数也大幅变了 —— 更像有意换次数区间

            # ⚠️ 只认标了**后一天**的 confirm。
            #
            # 曾经写成 `覆盖后一天 or 覆盖前一天`，那是个静默漏报：
            # 每个日期都会既当某一对的「后一天」、又当下一对的「前一天」，
            # 所以给 D 写的 confirm 会连带把 (D → 下一次) 那个跳变也标成已处理。
            # 用户确认了一次真实涨幅，下个月换机位造成的假涨幅就再也不会预警。
            #
            # 只认后一天是安全的：CLI 给出的 confirm 命令用的就是后一天；
            # 而 scale / offset 会真的改掉负荷，跳变自然消失，不靠这里标记。
            hit = (m.name, s.date) in confirmed
            jumps.append(Jump(
                movement=m.name, date=s.date, prev_date=prev_date,
                load=m.top_load_kg, prev_load=prev_load,
                reps=m.reps_total, prev_reps=prev_reps, gap_days=gap,
                resolved_by=s.date if hit else None))
    return jumps


def unresolved(stats, rules: list[Rule] | None = None) -> list[Jump]:
    return [j for j in detect_jumps(stats, rules) if j.resolved_by is None]
