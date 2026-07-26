"""优点 / 缺点 / 改进点 —— 由确定性规则产出，不由模型产出。

两条结构性不变量（有测试保证）：

  1. **每一条「缺点」都必须挂着至少一条「改进点」。**
     这是把「循循善诱」从形容词变成系统属性的办法 —— 批评永远不会孤零零出现，
     后面一定跟着一个带数字的、可执行的下一步。

  2. **每一条「优点」都必须有数字支撑。**
     夸奖要靠数据挣来。没有可夸的就诚实地说没有，不编。

模型拿到的是这些 Finding，任务只是把它们讲得好听。它不能发明新的 finding，
也不能引入 metrics 里没有的数字。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .compare import GroupComparison
from .metrics import SessionStats

Polarity = Literal["优点", "缺点", "改进点", "信息"]

# RPE 覆盖率低于这个值时，所有依赖 RPE 的判断一律不出，改为提示补记录。
RPE_MIN_COVERAGE = 0.30


@dataclass
class Finding:
    code: str
    polarity: Polarity
    subject: str                       # 动作名 / 肌群名 / 「本次训练」
    text: str                          # 给用户看的一句话，必须自带数字
    metrics: dict = field(default_factory=dict)   # 支撑数字，模型只能用这里的数
    links_to: list[str] = field(default_factory=list)  # 缺点 → 对应改进点的 code
    severity: int = 1                  # 1..3

    def __post_init__(self) -> None:
        if self.polarity == "优点" and not self.metrics:
            raise ValueError(f"优点必须有数字支撑: {self.code}")


def _fmt(v: float | None, unit: str = "", digits: int = 1) -> str:
    return "—" if v is None else f"{v:.{digits}f}{unit}"


def evaluate_group(cmp: GroupComparison) -> list[Finding]:
    """针对一个肌群的对比结果出结论。"""
    out: list[Finding] = []
    g = cmp.group

    if not cmp.has_anchor:
        out.append(Finding(
            code="NO_ANCHOR", polarity="信息", subject=g,
            text=f"这是最近一段时间里第一次记录到「{g}」的训练，暂时没有可对比的上一次。"
                 f"下次再练到 {g} 时就能给出对比了。",
            metrics={"sets": cmp.sets.after or 0}))
        return out

    # ── 动作完全换了 ──
    # 这种情况下比顶组负荷和估算 1RM 是没有意义的：器械推胸的 40kg 和
    # 俯卧撑折算出的 51kg 根本不是一回事。硬比会得出「力量下降 20%」这种
    # 既错误又打击人的结论。所以明说不比，并解释为什么。
    if not cmp.loads_comparable:
        out.append(Finding(
            code="MOVEMENT_SET_CHANGED", polarity="信息", subject=g,
            text=f"这次 {g} 的动作和上次完全没有重合"
                 f"（上次：{'、'.join(cmp.dropped[:3])}；"
                 f"这次：{'、'.join(cmp.added[:3])}），"
                 f"器械和自由重量的重量刻度本来就不通用，所以我不拿峰值负荷去对比 —— "
                 f"那样比出来的涨跌没有意义。下面只比总容量和组数这类可加总的量。"
                 f"想看力量进展的话，两次训练里保留 1~2 个相同的主项动作最有效。",
            metrics={"paired": 0, "added": cmp.added, "dropped": cmp.dropped}))

    # ── 容量与强度 ──
    if cmp.loads_comparable and cmp.best_e1rm.pct_change is not None:
        pct = cmp.best_e1rm.pct_change
        if pct >= 1.5:
            out.append(Finding(
                code="PROGRESSED_E1RM", polarity="优点", subject=g,
                text=f"{g} 的最强单组估算 1RM 从 {_fmt(cmp.best_e1rm.before, 'kg')} "
                     f"提升到 {_fmt(cmp.best_e1rm.after, 'kg')}（{pct:+.1f}%），力量在往上走。",
                metrics={"before": cmp.best_e1rm.before, "after": cmp.best_e1rm.after,
                         "pct": pct}))
        elif pct <= -3.0:
            out.append(Finding(
                code="REGRESSED_E1RM", polarity="缺点", subject=g, severity=2,
                text=f"{g} 的最强单组估算 1RM 从 {_fmt(cmp.best_e1rm.before, 'kg')} "
                     f"回落到 {_fmt(cmp.best_e1rm.after, 'kg')}（{pct:+.1f}%）。",
                metrics={"before": cmp.best_e1rm.before, "after": cmp.best_e1rm.after,
                         "pct": pct},
                links_to=["ACTION_RECOVER_LOAD"]))
            out.append(Finding(
                code="ACTION_RECOVER_LOAD", polarity="改进点", subject=g,
                text=f"下次 {g} 先回到上次的顶组重量 {_fmt(cmp.top_load.before, 'kg')} 试一组，"
                     f"如果动作质量没问题再往上加。单次回落很常见，可能只是睡眠或状态问题，不用急。",
                metrics={"target_load": cmp.top_load.before}))

    if cmp.volume.pct_change is not None:
        pct = cmp.volume.pct_change
        if pct <= -15.0:
            out.append(Finding(
                code="VOLUME_DROP", polarity="缺点", subject=g, severity=2,
                text=f"{g} 的总容量从 {_fmt(cmp.volume.before, 'kg', 0)} 降到 "
                     f"{_fmt(cmp.volume.after, 'kg', 0)}（{pct:+.1f}%），"
                     f"有效组数 {cmp.sets.fmt('组', 0)}。",
                metrics={"before": cmp.volume.before, "after": cmp.volume.after,
                         "pct": pct, "sets_before": cmp.sets.before,
                         "sets_after": cmp.sets.after},
                links_to=["ACTION_ADD_SET"]))
            need = max(1, int((cmp.sets.before or 0) - (cmp.sets.after or 0)))
            out.append(Finding(
                code="ACTION_ADD_SET", polarity="改进点", subject=g,
                text=f"下次 {g} 补回 {need} 组即可回到上次的水平。"
                     f"如果这次是刻意减量（比如状态不好或在减载周），那没问题，忽略这条。",
                metrics={"add_sets": need}))
        elif 5.0 <= pct <= 25.0:
            out.append(Finding(
                code="VOLUME_UP_CONTROLLED", polarity="优点", subject=g,
                text=f"{g} 的总容量从 {_fmt(cmp.volume.before, 'kg', 0)} 涨到 "
                     f"{_fmt(cmp.volume.after, 'kg', 0)}（{pct:+.1f}%），是健康的递进幅度。",
                metrics={"before": cmp.volume.before, "after": cmp.volume.after, "pct": pct}))
        elif pct > 40.0:
            out.append(Finding(
                code="VOLUME_SPIKE", polarity="缺点", subject=g, severity=1,
                text=f"{g} 的容量一次涨了 {pct:+.0f}%（{_fmt(cmp.volume.before, 'kg', 0)} → "
                     f"{_fmt(cmp.volume.after, 'kg', 0)}），涨幅偏陡，酸痛和恢复负担会明显增加。",
                metrics={"before": cmp.volume.before, "after": cmp.volume.after, "pct": pct},
                links_to=["ACTION_SMOOTH_RAMP"]))
            out.append(Finding(
                code="ACTION_SMOOTH_RAMP", polarity="改进点", subject=g,
                text=f"下次 {g} 保持在这个容量别再加，先让身体适应一到两次，"
                     f"之后每次加 5~10% 更稳。",
                metrics={"hold_volume": cmp.volume.after}))

    # ── 逐动作 ──
    for md in cmp.movements:
        if md.status == "paired":
            load_pct = md.top_load.pct_change
            reps_up = (md.reps.abs_change or 0) > 0
            if load_pct is not None and load_pct >= 2.0:
                out.append(Finding(
                    code="PROGRESSED_LOAD", polarity="优点", subject=md.name,
                    text=f"{md.name} 顶组重量 {md.top_load.fmt('kg')}，加重成功。",
                    metrics={"before": md.top_load.before, "after": md.top_load.after,
                             "pct": load_pct}))
            elif (load_pct is not None and abs(load_pct) < 2.0 and reps_up):
                out.append(Finding(
                    code="PROGRESSED_REPS", polarity="优点", subject=md.name,
                    text=f"{md.name} 同样的重量下总次数 {md.reps.fmt('次', 0)}，"
                         f"这是加重之前该走的一步。",
                    metrics={"reps_before": md.reps.before, "reps_after": md.reps.after,
                             "load": md.top_load.after}))
            if md.e1rm.pct_change is not None and md.e1rm.pct_change <= -5.0:
                out.append(Finding(
                    code="MOVEMENT_REGRESSED", polarity="缺点", subject=md.name, severity=1,
                    text=f"{md.name} 的估算 1RM 回落 {md.e1rm.pct_change:+.1f}%"
                         f"（{md.e1rm.fmt('kg')}）。",
                    metrics={"before": md.e1rm.before, "after": md.e1rm.after,
                             "pct": md.e1rm.pct_change},
                    links_to=["ACTION_PRIORITIZE"]))
                out.append(Finding(
                    code="ACTION_PRIORITIZE", polarity="改进点", subject=md.name,
                    text=f"下次把 {md.name} 排到 {cmp.group} 训练的最前面做，"
                         f"在最有力气的时候练它，通常一次就能恢复。",
                    metrics={"movement": md.name}))

    if cmp.dropped:
        names = "、".join(cmp.dropped[:3])
        out.append(Finding(
            code="DROPPED_MOVEMENT", polarity="缺点", subject=cmp.group, severity=1,
            text=f"上次练 {cmp.group} 做了但这次没做：{names}"
                 f"{'等' if len(cmp.dropped) > 3 else ''}。",
            metrics={"dropped": cmp.dropped},
            links_to=["ACTION_RESTORE_MOVEMENT"]))
        out.append(Finding(
            code="ACTION_RESTORE_MOVEMENT", polarity="改进点", subject=cmp.group,
            text=f"如果是有意换动作，那很好，换动作本身有价值；"
                 f"如果只是忘了，下次把 {cmp.dropped[0]} 加回来，保持刺激的连续性。",
            metrics={"restore": cmp.dropped[0]}))

    if cmp.added:
        out.append(Finding(
            code="ADDED_MOVEMENT", polarity="信息", subject=cmp.group,
            text=f"这次 {cmp.group} 新加了：{'、'.join(cmp.added[:3])}。"
                 f"新动作前两次先摸清重量，不用急着冲。",
            metrics={"added": cmp.added}))

    return out


def evaluate_session(stats: SessionStats, comparisons: list[GroupComparison],
                     *, weekly_sets: dict[str, float] | None = None) -> list[Finding]:
    """整次训练层面的结论（不针对单个肌群）。"""
    out: list[Finding] = []

    # ── 完成度 ──
    if stats.sets_planned > stats.sets_done:
        missed = stats.sets_planned - stats.sets_done
        out.append(Finding(
            code="INCOMPLETE_SETS", polarity="缺点", subject="本次训练", severity=1,
            text=f"有 {missed} 组计划了但没打勾完成"
                 f"（{stats.sets_done}/{stats.sets_planned} 组）。",
            metrics={"missed": missed, "done": stats.sets_done,
                     "planned": stats.sets_planned},
            links_to=["ACTION_TRIM_PLAN"]))
        out.append(Finding(
            code="ACTION_TRIM_PLAN", polarity="改进点", subject="本次训练",
            text=f"如果经常练不完，把计划裁到 {stats.sets_done} 组左右更实际 —— "
                 f"能稳定完成的计划比雄心勃勃的计划有用得多。",
            metrics={"suggested_sets": stats.sets_done}))
    elif stats.sets_done > 0 and stats.sets_planned == stats.sets_done:
        out.append(Finding(
            code="FULL_COMPLETION", polarity="优点", subject="本次训练",
            text=f"计划的 {stats.sets_done} 组全部完成，执行度满分。",
            metrics={"sets": stats.sets_done}))

    # ── RPE 数据质量 ──
    if stats.sets_done and stats.rpe_coverage < RPE_MIN_COVERAGE:
        out.append(Finding(
            code="MISSING_RPE", polarity="改进点", subject="本次训练",
            text=f"这次有 RPE 记录的组占 {stats.rpe_coverage * 100:.0f}%，"
                 f"数据不够做强度判断，所以本次报告里关于「练得够不够狠」的结论我先不下。"
                 f"其实只要每个动作的**最后一组**记一个 RPE 就够了，"
                 f"一次训练多花不到一分钟，但能让下次的重量建议准很多。",
            metrics={"coverage": stats.rpe_coverage}))

    # ── 时长与密度 ──
    if stats.duration_min and stats.duration_min > 90 and stats.density_kg_per_min:
        out.append(Finding(
            code="SESSION_LONG", polarity="缺点", subject="本次训练", severity=1,
            text=f"这次练了 {stats.duration_min:.0f} 分钟，密度 "
                 f"{stats.density_kg_per_min:.0f} kg/分钟。",
            metrics={"duration_min": stats.duration_min,
                     "density": stats.density_kg_per_min},
            links_to=["ACTION_TIGHTEN_REST"]))
        out.append(Finding(
            code="ACTION_TIGHTEN_REST", polarity="改进点", subject="本次训练",
            text="孤立动作的组间休息压到 60~90 秒，复合动作 2~3 分钟，"
                 "总时长控制在 75 分钟内，训练质量通常不降反升。",
            metrics={"target_duration_min": 75}))

    # ── 左右不平衡 ──
    for m in stats.movements:
        if m.imbalance_pct is not None and m.imbalance_pct > 10:
            out.append(Finding(
                code="IMBALANCE_LR", polarity="缺点", subject=m.name, severity=2,
                text=f"{m.name} 左右重量差 {m.imbalance_pct:.0f}%，超过 10% 了。",
                metrics={"imbalance_pct": m.imbalance_pct},
                links_to=["ACTION_FIX_IMBALANCE"]))
            out.append(Finding(
                code="ACTION_FIX_IMBALANCE", polarity="改进点", subject=m.name,
                text=f"下次做 {m.name} 时**从弱侧先开始**，强侧只做到弱侧完成的次数，"
                     f"几周就能明显拉平。",
                metrics={"movement": m.name}))

    # ── 分类覆盖 ──
    unknown = [m.name for m in stats.movements if m.group == "未分类"]
    if unknown:
        out.append(Finding(
            code="UNCLASSIFIED", polarity="信息", subject="本次训练",
            text=f"有 {len(unknown)} 个动作我还认不出属于哪个部位："
                 f"{'、'.join(unknown[:3])}。告诉我它们练的是哪里，我记下来，以后就认得了。",
            metrics={"movements": unknown}))

    # ── 容量不完整 ──
    if stats.volume_incomplete:
        out.append(Finding(
            code="VOLUME_INCOMPLETE", polarity="信息", subject="本次训练",
            text="本次有自重动作，但缺少当日体重数据，这部分容量没有计入总吨位。"
                 "记录一下体重就能补全。",
            metrics={}))

    return out


def evaluate(stats: SessionStats, comparisons: list[GroupComparison]) -> list[Finding]:
    out = evaluate_session(stats, comparisons)
    for c in comparisons:
        out.extend(evaluate_group(c))
    return out


def split(findings: list[Finding]) -> dict[str, list[Finding]]:
    """按 优点 / 缺点 / 改进点 / 信息 分组，报告直接用。"""
    buckets: dict[str, list[Finding]] = {"优点": [], "缺点": [], "改进点": [], "信息": []}
    for f in findings:
        buckets[f.polarity].append(f)
    for key in ("缺点",):
        buckets[key].sort(key=lambda f: -f.severity)
    return buckets


def check_invariants(findings: list[Finding]) -> list[str]:
    """校验两条结构性不变量。返回违规说明列表，空列表 = 通过。"""
    problems = []
    codes = {f.code for f in findings}
    for f in findings:
        if f.polarity == "缺点":
            if not f.links_to:
                problems.append(f"缺点 {f.code} 没有挂改进点")
            else:
                missing = [c for c in f.links_to if c not in codes]
                if missing:
                    problems.append(f"缺点 {f.code} 指向了不存在的改进点 {missing}")
        if f.polarity == "优点" and not f.metrics:
            problems.append(f"优点 {f.code} 没有数字支撑")
    return problems
