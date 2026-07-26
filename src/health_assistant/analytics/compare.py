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

    @property
    def has_anchor(self) -> bool:
        return self.anchor_date is not None

    @property
    def loads_comparable(self) -> bool:
        """有共同动作时，负荷才有可比性。"""
        return self.paired_count > 0


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
        match = difflib.get_close_matches(cur.name, list(remaining), n=1,
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

    deltas = []
    for cur, anc in paired:
        deltas.append(MovementDelta(
            name=cur.name, status="paired",
            sets=Delta(float(anc.sets_done), float(cur.sets_done)),
            reps=Delta(anc.reps_total, cur.reps_total),
            top_load=Delta(anc.top_load_kg, cur.top_load_kg),
            volume=Delta(anc.volume_kg, cur.volume_kg),
            e1rm=Delta(anc.best_e1rm, cur.best_e1rm),
            avg_rpe=Delta(anc.avg_rpe, cur.avg_rpe),
        ))
    for m in added:
        deltas.append(MovementDelta(
            name=m.name, status="added",
            sets=Delta(None, float(m.sets_done)), reps=Delta(None, m.reps_total),
            top_load=Delta(None, m.top_load_kg), volume=Delta(None, m.volume_kg),
            e1rm=Delta(None, m.best_e1rm), avg_rpe=Delta(None, m.avg_rpe)))
    for m in dropped:
        deltas.append(MovementDelta(
            name=m.name, status="dropped",
            sets=Delta(float(m.sets_done), None), reps=Delta(m.reps_total, None),
            top_load=Delta(m.top_load_kg, None), volume=Delta(m.volume_kg, None),
            e1rm=Delta(m.best_e1rm, None), avg_rpe=Delta(m.avg_rpe, None)))

    days = None
    try:
        days = (dt.date.fromisoformat(current.date)
                - dt.date.fromisoformat(anchor.date)).days
    except ValueError:
        pass

    # 负荷类指标只在共同动作之间比。没有共同动作时留空，
    # 由 findings 层给出「动作换了，负荷不可比」的说明，而不是硬比出一个假结论。
    if paired:
        cur_paired = [c for c, _ in paired]
        anc_paired = [a for _, a in paired]
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
        paired_count=len(paired),
    )


def compare_session(current: SessionStats, history: list[SessionStats],
                    *, min_sets: int = MIN_SETS_FOR_GROUP) -> list[GroupComparison]:
    """对本次训练涉及的每个主要肌群各做一次对比。"""
    groups = [g for g, n in current.groups.items() if n >= min_sets]
    groups.sort(key=lambda g: (-current.groups[g], g))
    return [compare_group(current, history, g) for g in groups]
