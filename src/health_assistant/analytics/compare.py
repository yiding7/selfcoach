"""同肌群训练对比。

**这是整个工具的承重件。**

一个关键事实：一次训练里通常混着好几个部位。2026-07-12 那次名义上是胸日，
但 8 个动作里混了三头和背。所以对比必须在**肌群粒度**做，不能按整次训练比。

对每个肌群 G：
  1. 在历史里找到上一次真正练了 G 的训练（G 的有效组数 ≥ 阈值）
  2. 只取两次训练里属于 G 的动作来比
  3. 逐个动作配对，算出负荷/次数/容量/估算 1RM 的变化
  4. 产出带证据的结构化结论

「带证据」是硬要求：每条结论都要能追溯到具体动作和具体数字。
模型负责把它讲得好听，不负责发明结论。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .. import loading
from .metrics import MovementStats, SessionStats

# 一次训练里某肌群至少练到这么多组，才算「练了这个部位」
MIN_SETS_FOR_GROUP = 2
# 往前找多久
MAX_LOOKBACK_DAYS = 90
# 动作名模糊匹配阈值
FUZZY_THRESHOLD = 0.82


@dataclass(frozen=True)
class Delta:
    before: float | None
    after: float | None

    @property
    def abs_change(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before

    @property
    def pct_change(self) -> float | None:
        if self.before is None or self.after is None or self.before == 0:
            return None
        return (self.after - self.before) / self.before * 100.0

    @property
    def direction(self) -> str:
        c = self.abs_change
        if c is None:
            return "n/a"
        if abs(c) < 1e-9:
            return "flat"
        return "up" if c > 0 else "down"

    def fmt(self, unit: str = "", digits: int = 1) -> str:
        if self.before is None and self.after is None:
            return "—"
        if self.before is None:
            return f"新增 {self.after:.{digits}f}{unit}"
        if self.after is None:
            return f"本次未做（上次 {self.before:.{digits}f}{unit}）"
        pct = self.pct_change
        arrow = {"up": "↑", "down": "↓", "flat": "="}[self.direction]
        base = f"{self.before:.{digits}f} → {self.after:.{digits}f}{unit} {arrow}"
        if pct is not None and abs(pct) >= 0.05:
            base += f" {pct:+.1f}%"
        return base


@dataclass
class MovementDelta:
    name: str
    status: str            # paired | added | dropped
    sets: Delta
    reps: Delta
    top_load: Delta
    volume: Delta
    e1rm: Delta
    avg_rpe: Delta
    # 计时类动作（平板支撑等）：成绩在秒里，`reps` 恒为 0。
    # 不带这两个字段的话，35s→42s 这种真实进步会被报成「总次数 0 → 0 次 =」，
    # 也就是把一次 +20% 的进步报成「没变化」。
    timed: bool = False
    best_time: Delta = field(default_factory=lambda: Delta(None, None))
    time_total: Delta = field(default_factory=lambda: Delta(None, None))
    # 两次不在同一个馆，且这个动作的负荷依赖场地（配重片/绳索/史密斯/哈克）。
    # 组数、次数、容量照常比 —— 那些是真的练了；只有 top_load 和 e1rm 作废。
    site_incomparable: bool = False


@dataclass
class GroupComparison:
    group: str
    current_date: str
    anchor_date: str | None
    anchor_reason: str
    days_between: int | None
    # 肌群粒度的汇总
    sets: Delta
    volume: Delta
    # ⚠️ 顶组负荷和估算 1RM 只在**两次都做过的动作**之间比较。
    # 跨动作比峰值负荷是没有意义的：器械推胸 40kg 和俯卧撑折算的 51kg
    # 完全不是一回事，硬比会得出「力量下降 19.5%」这种既错误又打击人的结论。
    top_load: Delta
    best_e1rm: Delta
    movements: list[MovementDelta] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    rpe_coverage: float = 0.0
    paired_count: int = 0
    # 两次各自在哪个馆。None = 没标过场地 —— **不是「同一个馆」，是「不知道」**。
    # 只有两边都标了且不同，才算换馆。不知道时行为和加这个字段之前完全一致。
    gym_before: str | None = None
    gym_after: str | None = None
    # 因换馆而负荷不可比的动作（组数/容量照常计入）
    site_incomparable: list[str] = field(default_factory=list)

    @property
    def gym_changed(self) -> bool:
        return bool(self.gym_before and self.gym_after
                    and self.gym_before != self.gym_after)

    @property
    def has_anchor(self) -> bool:
        return self.anchor_date is not None

    @property
    def loads_comparable(self) -> bool:
        """有共同动作时，负荷才有可比性。"""
        return self.paired_count > 0


def _same_loading(a: str, b: str) -> bool:
    """两个动作名的计量口径一不一样（一个器械 vs 每手一个、两侧 vs 单侧）。

    口径不同的两个动作，负荷数字根本不是同一个量 —— 模糊匹配再像也不能配对。
    """
    x, y = loading.classify(a), loading.classify(b)
    return (x.implements, x.sides) == (y.implements, y.sides)


def _group_movements(stats: SessionStats, group: str) -> list[MovementStats]:
    return [m for m in stats.movements if m.group == group and m.sets_done > 0]


def _sum(values) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _max(values) -> float | None:
    known = [v for v in values if v is not None]
    return max(known) if known else None


def find_anchor(current: SessionStats, history: list[SessionStats], group: str,
                *, max_lookback_days: int = MAX_LOOKBACK_DAYS
                ) -> tuple[SessionStats | None, str]:
    """找到上一次练了同一肌群的训练。

    返回 (会话, 选中理由)。理由要展示给用户 —— 让人看得见工具为什么拿这次比，
    是建立信任的关键。
    """
    import datetime as dt

    try:
        cur_date = dt.date.fromisoformat(current.date)
    except ValueError:
        return None, "当前训练日期无效"

    candidates = []
    for s in history:
        if s.id == current.id or s.date >= current.date:
            continue
        n = s.groups.get(group, 0)
        if n < MIN_SETS_FOR_GROUP:
            continue
        try:
            d = dt.date.fromisoformat(s.date)
        except ValueError:
            continue
        if (cur_date - d).days > max_lookback_days:
            continue
        candidates.append((s.date, s, n))

    if not candidates:
        return None, f"最近 {max_lookback_days} 天内没有另一次练到「{group}」的记录"

    candidates.sort(key=lambda t: t[0], reverse=True)
    _, anchor, n = candidates[0]
    cur_n = current.groups.get(group, 0)
    gap = (cur_date - dt.date.fromisoformat(anchor.date)).days
    return anchor, (f"上一次练「{group}」是 {anchor.date}（{gap} 天前，"
                    f"{group} {n:.0f} 组 vs 本次 {cur_n:.0f} 组）")


def _pair(current: list[MovementStats], anchor: list[MovementStats]
          ) -> tuple[list[tuple[MovementStats, MovementStats]],
                     list[MovementStats], list[MovementStats]]:
    """按动作名配对：精确 → 模糊。"""
    remaining = {m.name: m for m in anchor}
    paired, added = [], []

    for cur in current:
        if cur.name in remaining:
            paired.append((cur, remaining.pop(cur.name)))
            continue
        # 模糊匹配只在**同一个计量口径**里找。
        #
        # 「上斜杠铃卧推」和「上斜哑铃卧推」只差一个字，difflib 相似度 0.83，
        # 稳稳越过 0.82 的门槛 —— 于是 2026-08-23 那次真的报出了
        # 「顶组 14.0 → 35.0kg ↑ +150.0%」：拿一对 14kg 哑铃去比一根 35kg 杠铃。
        # 一个动作名差一个字、负荷口径差一倍，这正是 implement-loading.json
        # 存在的理由，所以让它来当这道门槛。
        pool = [n for n in remaining if _same_loading(cur.name, n)]
        match = difflib.get_close_matches(cur.name, pool, n=1,
                                          cutoff=FUZZY_THRESHOLD)
        if match:
            paired.append((cur, remaining.pop(match[0])))
        else:
            added.append(cur)

    return paired, added, list(remaining.values())


def compare_group(current: SessionStats, history: list[SessionStats],
                  group: str) -> GroupComparison:
    import datetime as dt

    cur_ms = _group_movements(current, group)
    anchor, reason = find_anchor(current, history, group)

    rpe_sets = sum(len(m.rpes) for m in cur_ms)
    total_sets = sum(m.sets_done for m in cur_ms)
    coverage = (rpe_sets / total_sets) if total_sets else 0.0

    if anchor is None:
        return GroupComparison(
            group=group, current_date=current.date, anchor_date=None,
            anchor_reason=reason, days_between=None,
            sets=Delta(None, float(total_sets)),
            volume=Delta(None, _sum(m.volume_kg for m in cur_ms)),
            top_load=Delta(None, _max(m.top_load_kg for m in cur_ms)),
            best_e1rm=Delta(None, _max(m.best_e1rm for m in cur_ms)),
            added=[m.name for m in cur_ms],
            rpe_coverage=coverage,
        )

    anc_ms = _group_movements(anchor, group)

    paired, added, dropped = _pair(cur_ms, anc_ms)

    # 换馆：**只降器械类，自由重量照常比。** 杠铃 64kg 在哪个馆都是 64kg；
    # 哈克机 107kg 换台机器就不是同一个量（滑车自重 + 轨道角度各厂不同）。
    #
    # 为什么不把整次对比作废：那会把「深蹲涨了 4kg」这种真进步一起埋掉，
    # 而且表现为「什么都没说」—— 比报错更难发现。用户 2026-08-21 选的就是这一档。
    #
    # 判据要两边都标了场地才成立。`None` 是「不知道」，不是「同一个馆」——
    # 混淆这两者会让没标注的历史突然全部变成「换馆」。
    gym_changed = bool(current.gym and anchor.gym and current.gym != anchor.gym)
    site_hit = sorted({c.name for c, a in paired
                       if not (c.load_portable and a.load_portable)}) if gym_changed else []

    deltas = []
    for cur, anc in paired:
        blind = cur.name in site_hit
        deltas.append(MovementDelta(
            name=cur.name, status="paired",
            sets=Delta(float(anc.sets_done), float(cur.sets_done)),
            reps=Delta(anc.reps_total, cur.reps_total),
            # 换馆 + 器械类：负荷两端都置空，不是「保留数字再加一句提醒」。
            # 留着数字，读的人（和下一个模型）迟早会去比它。
            top_load=Delta(None, None) if blind else Delta(anc.top_load_kg, cur.top_load_kg),
            volume=Delta(anc.volume_kg, cur.volume_kg),
            e1rm=Delta(None, None) if blind else Delta(anc.best_e1rm, cur.best_e1rm),
            avg_rpe=Delta(anc.avg_rpe, cur.avg_rpe),
            site_incomparable=blind,
            # 两次里任意一次是计时类就按计时展示 —— 同一个动作不该
            # 因为某一次没记秒数就退回「0 次 → 0 次」。
            timed=cur.timed or anc.timed,
            best_time=Delta(anc.best_time_s, cur.best_time_s),
            time_total=Delta(anc.time_s_total, cur.time_s_total),
        ))
    for m in added:
        deltas.append(MovementDelta(
            name=m.name, status="added",
            sets=Delta(None, float(m.sets_done)), reps=Delta(None, m.reps_total),
            top_load=Delta(None, m.top_load_kg), volume=Delta(None, m.volume_kg),
            e1rm=Delta(None, m.best_e1rm), avg_rpe=Delta(None, m.avg_rpe),
            timed=m.timed,
            best_time=Delta(None, m.best_time_s),
            time_total=Delta(None, m.time_s_total)))
    for m in dropped:
        deltas.append(MovementDelta(
            name=m.name, status="dropped",
            sets=Delta(float(m.sets_done), None), reps=Delta(m.reps_total, None),
            top_load=Delta(m.top_load_kg, None), volume=Delta(m.volume_kg, None),
            e1rm=Delta(m.best_e1rm, None), avg_rpe=Delta(m.avg_rpe, None),
            timed=m.timed,
            best_time=Delta(m.best_time_s, None),
            time_total=Delta(m.time_s_total, None)))

    days = None
    try:
        days = (dt.date.fromisoformat(current.date)
                - dt.date.fromisoformat(anchor.date)).days
    except ValueError:
        pass

    # 负荷类指标只在共同动作之间比。没有共同动作时留空，
    # 由 findings 层给出「动作换了，负荷不可比」的说明，而不是硬比出一个假结论。
    # 汇总的顶组和最强 1RM 同样要把换馆的器械动作排除掉，否则「腿」这一层
    # 的顶组还是会拿哈克机的 107kg 去比 —— 逐动作那层挡住了，汇总层漏过去，
    # 就是另一个静默失效。
    comparable = [(c, a) for c, a in paired if c.name not in site_hit]
    if comparable:
        cur_paired = [c for c, _ in comparable]
        anc_paired = [a for _, a in comparable]
        top_load = Delta(_max(m.top_load_kg for m in anc_paired),
                         _max(m.top_load_kg for m in cur_paired))
        best_e1rm = Delta(_max(m.best_e1rm for m in anc_paired),
                          _max(m.best_e1rm for m in cur_paired))
    else:
        top_load = Delta(None, None)
        best_e1rm = Delta(None, None)

    return GroupComparison(
        group=group, current_date=current.date, anchor_date=anchor.date,
        anchor_reason=reason, days_between=days,
        sets=Delta(float(sum(m.sets_done for m in anc_ms)), float(total_sets)),
        volume=Delta(_sum(m.volume_kg for m in anc_ms), _sum(m.volume_kg for m in cur_ms)),
        top_load=top_load,
        best_e1rm=best_e1rm,
        movements=deltas,
        added=[m.name for m in added],
        dropped=[m.name for m in dropped],
        rpe_coverage=coverage,
        paired_count=len(comparable),
        gym_before=anchor.gym,
        gym_after=current.gym,
        site_incomparable=site_hit,
    )


def compare_session(current: SessionStats, history: list[SessionStats],
                    *, min_sets: int = MIN_SETS_FOR_GROUP) -> list[GroupComparison]:
    """对本次训练涉及的每个主要肌群各做一次对比。"""
    groups = [g for g, n in current.groups.items() if n >= min_sets]
    groups.sort(key=lambda g: (-current.groups[g], g))
    return [compare_group(current, history, g) for g in groups]
