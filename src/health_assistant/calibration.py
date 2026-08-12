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

## 四种处置

用户看到预警时有四个选择，对应关系：

| 用户想做的 | action | 效果 |
|---|---|---|
| 改原始记录数据 | —— | 不写规则。去训记改，然后 `hc sync train --date X --force` |
| 只改项目内的数（不动数据源）| `scale` | 读取时乘一个系数，原始文件不动 |
| 忽略本次该动作的对比 | `ignore` | 该动作该次不参与配对，但组数/容量照常计入 |
| 确认是真实变化 | `confirm` | 不再预警。数据一个字不动 |

`confirm` 是要留痕的：「我看过了，这是真的」和「还没人看过」是两回事，
后者不该被静默当成前者。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from . import store
from .config import DATA_DIR

PATH = DATA_DIR / "load-calibration.jsonl"
SCHEMA = "ha.calib/1"

ACTIONS = ("scale", "ignore", "confirm")

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
    date_from: str | None = None      # 闭区间，None = 不限
    date_to: str | None = None
    note: str = ""
    supersedes: str | None = None

    def covers(self, movement: str, date: str) -> bool:
        if movement != self.movement:
            return False
        if self.date_from and date < self.date_from:
            return False
        if self.date_to and date > self.date_to:
            return False
        return True

    @property
    def span(self) -> str:
        if self.date_from and self.date_from == self.date_to:
            return self.date_from
        lo = self.date_from or "最早"
        hi = self.date_to or "至今"
        return f"{lo} ~ {hi}"

    def describe(self) -> str:
        what = {
            "scale": f"折算 ×{self.ratio:g}" if self.ratio else "折算",
            "ignore": "不参与对比",
            "confirm": "已确认为真实数据",
        }.get(self.action, self.action)
        return f"[{self.id}] {self.movement}　{self.span}　{what}"


def _rules_raw() -> list[dict]:
    try:
        return store.read_jsonl(PATH)
    except OSError:
        return []


def load_rules() -> list[Rule]:
    """读全部生效规则。被 supersede 掉的不返回，但它们仍留在文件里。"""
    rows = _rules_raw()
    dead = {r.get("supersedes") for r in rows if r.get("supersedes")}
    out = []
    for r in rows:
        if r.get("id") in dead or r.get("action") not in ACTIONS:
            continue
        try:
            ratio = float(r["ratio"]) if r.get("ratio") is not None else None
        except (TypeError, ValueError):
            ratio = None
        out.append(Rule(
            id=str(r.get("id") or ""), ts=str(r.get("ts") or ""),
            movement=str(r.get("movement") or ""), action=str(r["action"]),
            ratio=ratio, date_from=r.get("date_from"), date_to=r.get("date_to"),
            note=str(r.get("note") or ""), supersedes=r.get("supersedes")))
    return out


def _next_id(today: dt.date) -> str:
    prefix = today.strftime("%Y%m%d")
    n = sum(1 for r in _rules_raw() if str(r.get("id", "")).startswith(prefix))
    return f"{prefix}-{n + 1:02d}"


def add_rule(movement: str, action: str, *, ratio: float | None = None,
             date_from: str | None = None, date_to: str | None = None,
             note: str = "", supersedes: str | None = None,
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
    for label, d in (("--from/--date", date_from), ("--to/--date", date_to)):
        if d:
            try:
                dt.date.fromisoformat(d)
            except ValueError:
                raise ValueError(f"{label} 的日期格式不对：{d!r}，要 YYYY-MM-DD") from None
    if date_from and date_to and date_from > date_to:
        raise ValueError(f"起始日期 {date_from} 晚于结束日期 {date_to}")

    today = today or dt.date.today()
    rule = Rule(id=_next_id(today),
                ts=dt.datetime.now().astimezone().replace(microsecond=0).isoformat(),
                movement=movement, action=action, ratio=ratio,
                date_from=date_from, date_to=date_to, note=note,
                supersedes=supersedes)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with PATH.open("a", encoding="utf-8") as fh:
        fh.write(store.dumps({
            "schema": SCHEMA, "id": rule.id, "ts": rule.ts,
            "movement": rule.movement, "action": rule.action, "ratio": rule.ratio,
            "date_from": rule.date_from, "date_to": rule.date_to,
            "note": rule.note, "supersedes": rule.supersedes}) + "\n")
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

    out = []
    for s in sessions:
        date = s.get("date", "")
        new_movements, touched = [], False
        for m in s.get("movements") or []:
            name = m.get("name") or ""
            hits = [r for r in rules if r.covers(name, date)]
            if not hits:
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
            if any(r.action == "ignore" for r in hits):
                marks["exclude_compare"] = True
            if any(r.action == "confirm" for r in hits):
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
    seen: dict[str, tuple[str, float, float]] = {}
    jumps: list[Jump] = []

    for s in stats:
        for m in s.movements:
            if m.bodyweight or m.assisted or m.timed:
                continue
            if not m.top_load_kg or m.sets_done == 0:
                continue
            prev = seen.get(m.name)
            seen[m.name] = (s.date, m.top_load_kg, m.reps_total)
            if not prev:
                continue
            prev_date, prev_load, prev_reps = prev
            if not prev_load:
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

            # ⚠️ 只认覆盖**后一天**的规则。
            #
            # 曾经写成 `covers(后一天) or covers(前一天)`，那是个静默漏报：
            # 每个日期都会既当某一对的「后一天」、又当下一对的「前一天」，
            # 所以给 D 写的 confirm 会连带把 (D → 下一次) 那个跳变也标成已处理。
            # 用户确认了一次真实涨幅，下个月换机位造成的假涨幅就再也不会预警。
            #
            # 只认后一天是安全的：CLI 给出的 ignore/confirm 命令用的就是后一天；
            # 而 scale 规则会真的改掉负荷，跳变自然消失，不需要靠这里标记。
            # 跨越两天的区间规则同时覆盖两端，也照样能命中。
            hit = next((r for r in rules if r.covers(m.name, s.date)), None)
            jumps.append(Jump(
                movement=m.name, date=s.date, prev_date=prev_date,
                load=m.top_load_kg, prev_load=prev_load,
                reps=m.reps_total, prev_reps=prev_reps, gap_days=gap,
                resolved_by=hit.id if hit else None))
    return jumps


def unresolved(stats, rules: list[Rule] | None = None) -> list[Jump]:
    return [j for j in detect_jumps(stats, rules) if j.resolved_by is None]
