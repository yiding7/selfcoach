"""多视角对比：动作级 / 模式级 / 肌群窗口 / 结构平衡。

**为什么需要多个视角**

每次训练的选材都在变。真实数据里，背部有 5 个垂直拉变体和 9 个水平拉变体；
一次背日可能全做划船，下一次全做下拉，第三次做宽距+窄距+反握三种下拉。
只按「本次训练 vs 上次同部位训练」比，动作对不上时就只能说「不可比」——
但这其实浪费了大量真实存在的可比信息。

四个视角各自回答不同的问题，各自带独立的置信度：

  1. 动作级纵向  这个动作和**它自己**上一次做比，怎么样了？
                 ← 最可靠。完全不受当次选材变化影响。宽距高位下拉哪怕
                   隔了三次训练才再做，也能干净地比出进步。
  2. 模式级      垂直拉/水平拉这类**发力模式**的量和强度，比上次怎么样？
                 ← 变体互换时仍然成立。宽距、窄距、反握下拉都是垂直拉。
  3. 肌群窗口    最近 N 天 vs 前 N 天，这个部位的总量和频率如何？
                 ← 单次波动大，滚动窗口才看得出趋势。
  4. 结构平衡    这个部位内部，各模式的比例合理吗？
                 ← 背 20 组全是下拉、0 组划船，总量达标但结构是歪的。

置信度分级（高 → 低）：
    exact    同一个动作名           重量直接可比
    variant  同族不同握法/版本       重量大致可比，需标注
    pattern  同模式不同动作          只有容量和组数可比，重量不可比
    group    只是同一个肌群          只有组数和频率可比
    none     数据不足                什么都不比，明说

**结论按置信度门控**：负荷/1RM 类结论只从 exact 出（variant 出但标注），
容量类结论可以到 pattern，频率类到 group。低于门槛就明确说「暂时不可比」，
而不是硬凑一个会误导人的数字。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from functools import lru_cache

from ..config import KNOWLEDGE_DIR
from ..patterns import family_of, pattern_note, pattern_of
from .compare import Delta
from .metrics import MovementStats, SessionStats

BALANCE_PATH = KNOWLEDGE_DIR / "movements" / "pattern-balance.json"

CONFIDENCE_ORDER = {"none": 0, "group": 1, "pattern": 2, "variant": 3, "exact": 4}
CONFIDENCE_LABEL = {
    "exact": "同一动作",
    "variant": "同族变体",
    "pattern": "同类发力模式",
    "group": "仅同部位",
    "none": "无可比数据",
}


def at_least(conf: str, floor: str) -> bool:
    return CONFIDENCE_ORDER.get(conf, 0) >= CONFIDENCE_ORDER.get(floor, 0)


def _days(a: str, b: str) -> int | None:
    try:
        return (dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days
    except ValueError:
        return None


# ── 1. 动作级纵向 ───────────────────────────────────────────────────────

@dataclass
class MovementProgress:
    name: str
    group: str
    pattern: str
    confidence: str                  # exact | variant | none
    matched_name: str | None = None  # 上次匹配到的动作（同族时可能不同名）
    last_date: str | None = None
    days_since: int | None = None
    occurrences: int = 0             # 本地历史里做过多少次
    sets: Delta = field(default_factory=lambda: Delta(None, None))
    reps: Delta = field(default_factory=lambda: Delta(None, None))
    top_load: Delta = field(default_factory=lambda: Delta(None, None))
    volume: Delta = field(default_factory=lambda: Delta(None, None))
    e1rm: Delta = field(default_factory=lambda: Delta(None, None))
    bodyweight: bool = False
    assisted: bool = False
    # 计时类动作：成绩看秒不看次。见 compare.MovementDelta 上同名字段的说明。
    timed: bool = False
    best_time: Delta = field(default_factory=lambda: Delta(None, None))
    time_total: Delta = field(default_factory=lambda: Delta(None, None))
    # 换馆且这个动作的负荷依赖场地 —— 次数和容量照常比，负荷两端置空。
    site_incomparable: bool = False
    gym_before: str | None = None
    gym_after: str | None = None
    # 为了找同一个馆，跳过了更近的那一次。跳过谁必须说出来 ——
    # 「上一次」突然不是字面上的上一次，读的人有权知道。
    skipped_date: str | None = None
    skipped_gym: str | None = None

    @property
    def is_new(self) -> bool:
        return self.confidence == "none"

    @property
    def loads_comparable(self) -> bool:
        return (at_least(self.confidence, "variant")
                and not self.site_incomparable)


def _movements_of(stats: SessionStats) -> list[MovementStats]:
    return [m for m in stats.movements if m.sets_done > 0]


def movement_progress(current: SessionStats, history: list[SessionStats],
                      *, max_lookback_days: int = 180) -> list[MovementProgress]:
    """对本次每个动作，在**全部历史**里找它自己上一次出现。

    这是相对上一版最重要的改进：原先只在「上一次同部位训练」里找配对，
    所以如果宽距高位下拉不在那一次里、而在再往前一次里，对比就整个丢了。
    现在跨越所有历史找，选材怎么变都不影响单个动作的纵向追踪。
    """
    past = sorted([s for s in history if s.date < current.date],
                  key=lambda s: s.date, reverse=True)

    # 本次训练里出现过的动作名。同族回退时必须避开这些 ——
    # 否则「悍马机划船」（本次新加）会去跟上次的「悍马机划船（版本2）」比，
    # 而版本2 本次也做了、有它自己的对比，结果就是同一条历史被用了两次，
    # 还会凭空造出一个「下降 67%」的假结论。
    current_names = {m.name for m in _movements_of(current)}

    out: list[MovementProgress] = []
    for cur in _movements_of(current):
        fam = family_of(cur.name)
        pat = pattern_of(cur.name, cur.group)

        exact_hit = variant_hit = None
        same_gym_hit = None          # 同馆的同名记录，可能比 exact_hit 更早
        occurrences = 0
        for s in past:
            if _days(current.date, s.date) and _days(current.date, s.date) > max_lookback_days:
                break
            for m in _movements_of(s):
                if m.name == cur.name:
                    occurrences += 1
                    if exact_hit is None:
                        exact_hit = (s, m)
                    # 器械动作跨馆比不了。与其两手一摊，不如往前多翻几次找同馆的
                    # 那一回 —— 用户 2026-08-23：「能比较的还是尽可能去比较，
                    # 策略太保守导致经常得不出结果」。代价是「上一次」不再是字面
                    # 上的上一次，所以下面必须把跳过了谁写出来。
                    if (same_gym_hit is None and current.gym and s.gym
                            and s.gym == current.gym):
                        same_gym_hit = (s, m)
                elif (variant_hit is None
                      and m.name not in current_names
                      and family_of(m.name) == fam
                      and m.group == cur.group):
                    variant_hit = (s, m)

        hit, conf = (exact_hit, "exact") if exact_hit else (
            (variant_hit, "variant") if variant_hit else (None, "none"))

        # 最近那次在别的馆、而这个动作又吃场地 —— 换成同馆的那次，
        # 换出来的是一个真结论，而不是一个空格。
        skipped = None
        if (exact_hit and same_gym_hit and same_gym_hit is not exact_hit
                and not cur.load_portable
                and current.gym and exact_hit[0].gym != current.gym):
            skipped = exact_hit[0]
            hit, conf = same_gym_hit, "exact"

        mp = MovementProgress(
            name=cur.name, group=cur.group, pattern=pat, confidence=conf,
            occurrences=occurrences, bodyweight=cur.bodyweight, assisted=cur.assisted,
            timed=cur.timed, gym_after=current.gym)

        if hit is not None:
            s_prev, m_prev = hit
            # 换馆 + 器械类 = 换了把尺。两边都标了场地才算数：
            # None 是「不知道」，不是「同一个馆」。
            blind = bool(current.gym and s_prev.gym and current.gym != s_prev.gym
                         and not (cur.load_portable and m_prev.load_portable))
            mp.site_incomparable = blind
            mp.gym_before = s_prev.gym
            if skipped is not None:
                mp.skipped_date, mp.skipped_gym = skipped.date, skipped.gym
            mp.matched_name = m_prev.name
            mp.last_date = s_prev.date
            mp.days_since = _days(current.date, s_prev.date)
            mp.sets = Delta(float(m_prev.sets_done), float(cur.sets_done))
            mp.reps = Delta(m_prev.reps_total, cur.reps_total)
            mp.top_load = (Delta(None, None) if blind
                           else Delta(m_prev.top_load_kg, cur.top_load_kg))
            mp.volume = Delta(m_prev.volume_kg, cur.volume_kg)
            mp.e1rm = (Delta(None, None) if blind
                       else Delta(m_prev.best_e1rm, cur.best_e1rm))
            mp.timed = cur.timed or m_prev.timed
            mp.best_time = Delta(m_prev.best_time_s, cur.best_time_s)
            mp.time_total = Delta(m_prev.time_s_total, cur.time_s_total)
        else:
            mp.sets = Delta(None, float(cur.sets_done))
            mp.volume = Delta(None, cur.volume_kg)
            mp.top_load = Delta(None, cur.top_load_kg)
            mp.e1rm = Delta(None, cur.best_e1rm)
            mp.best_time = Delta(None, cur.best_time_s)
            mp.time_total = Delta(None, cur.time_s_total)

        out.append(mp)
    return out


# ── 2. 模式级 ───────────────────────────────────────────────────────────

@dataclass
class PatternComparison:
    group: str
    pattern: str
    note: str | None
    last_date: str | None
    days_since: int | None
    sets: Delta
    volume: Delta
    top_e1rm: Delta
    load_confidence: str             # 模式内是否存在同名/同族动作
    movements_now: list[str] = field(default_factory=list)
    movements_then: list[str] = field(default_factory=list)
    # 换馆，且这个模式里至少有一个动作的负荷依赖场地。
    # 组数和容量照常比 —— 容量是「练了多少」，换馆不改变这件事；
    # top_e1rm 是「多强」，那个跨馆不成立。
    site_incomparable: bool = False
    gym_before: str | None = None
    gym_after: str | None = None

    @property
    def has_anchor(self) -> bool:
        return self.last_date is not None


def _pattern_slice(stats: SessionStats, group: str, pattern: str) -> list[MovementStats]:
    return [m for m in _movements_of(stats)
            if m.group == group and pattern_of(m.name, m.group) == pattern]


def _sum(vals):
    known = [v for v in vals if v is not None]
    return sum(known) if known else None


def _max(vals):
    known = [v for v in vals if v is not None]
    return max(known) if known else None


def pattern_comparisons(current: SessionStats, history: list[SessionStats],
                        *, max_lookback_days: int = 90,
                        min_group_share: float = 0.25) -> list[PatternComparison]:
    """对本次涉及的每个 (肌群, 模式) 找上一次做同模式的训练。

    只看**本次的主要部位**（默认占本次有效组数 25% 以上）。
    背日顺手做的三组弯举，拿去和专门的手臂日比容量，只会得出
    「二头容量掉了 74%」这种正确但毫无意义的结论。
    附带练到的部位靠动作级视角追踪就够了 —— 那一层本来就更可靠。
    """
    past = sorted([s for s in history if s.date < current.date],
                  key=lambda s: s.date, reverse=True)

    total_sets = sum(m.sets_done for m in _movements_of(current)) or 1
    primary = {g for g, n in current.groups.items()
               if n / total_sets >= min_group_share}

    combos: list[tuple[str, str]] = []
    for m in _movements_of(current):
        key = (m.group, pattern_of(m.name, m.group))
        if key not in combos and m.group in primary:
            combos.append(key)

    out = []
    for group, pattern in combos:
        cur_ms = _pattern_slice(current, group, pattern)
        prev_s = prev_ms = None
        for s in past:
            d = _days(current.date, s.date)
            if d is not None and d > max_lookback_days:
                break
            ms = _pattern_slice(s, group, pattern)
            if ms:
                prev_s, prev_ms = s, ms
                break

        cur_names = sorted({m.name for m in cur_ms})
        if prev_ms is None:
            out.append(PatternComparison(
                group=group, pattern=pattern, note=pattern_note(group, pattern),
                last_date=None, days_since=None,
                sets=Delta(None, float(sum(m.sets_done for m in cur_ms))),
                volume=Delta(None, _sum(m.volume_kg for m in cur_ms)),
                top_e1rm=Delta(None, _max(m.best_e1rm for m in cur_ms)),
                load_confidence="none", movements_now=cur_names))
            continue

        prev_names = sorted({m.name for m in prev_ms})
        cur_fams = {family_of(n) for n in cur_names}
        if set(cur_names) & set(prev_names):
            conf = "exact"
        elif cur_fams & {family_of(n) for n in prev_names}:
            conf = "variant"
        else:
            conf = "pattern"

        # 换馆：只要这个模式里有一个动作的负荷依赖场地，峰值 1RM 就不能跨馆比。
        # 模式层是把不同动作汇到一起的，所以判据取「有没有」而不是「是不是全部」——
        # 一个哈克机就足以让这个模式的峰值失真。
        blind = bool(current.gym and prev_s.gym and current.gym != prev_s.gym
                     and not all(m.load_portable for m in cur_ms + prev_ms))

        # 1RM 只在有同名/同族动作时才比，否则不同动作的峰值没有可比性
        if at_least(conf, "variant") and not blind:
            e1 = Delta(_max(m.best_e1rm for m in prev_ms),
                       _max(m.best_e1rm for m in cur_ms))
        else:
            e1 = Delta(None, None)

        out.append(PatternComparison(
            group=group, pattern=pattern, note=pattern_note(group, pattern),
            site_incomparable=blind, gym_before=prev_s.gym, gym_after=current.gym,
            last_date=prev_s.date, days_since=_days(current.date, prev_s.date),
            sets=Delta(float(sum(m.sets_done for m in prev_ms)),
                       float(sum(m.sets_done for m in cur_ms))),
            volume=Delta(_sum(m.volume_kg for m in prev_ms),
                         _sum(m.volume_kg for m in cur_ms)),
            top_e1rm=e1, load_confidence=conf,
            movements_now=cur_names, movements_then=prev_names))
    return out


# ── 3. 肌群滚动窗口 ─────────────────────────────────────────────────────

@dataclass
class GroupWindow:
    group: str
    window_days: int
    sessions: Delta
    sets: Delta
    volume: Delta
    pattern_sets: dict[str, float] = field(default_factory=dict)
    prev_pattern_sets: dict[str, float] = field(default_factory=dict)
    days_since_last: int | None = None


def group_window(all_stats: list[SessionStats], group: str, as_of: str,
                 *, window_days: int = 28) -> GroupWindow:
    """最近 N 天 vs 前 N 天。单次训练波动太大，趋势要靠窗口看。"""
    try:
        end = dt.date.fromisoformat(as_of)
    except ValueError:
        end = dt.date.today()
    cur_start = end - dt.timedelta(days=window_days - 1)
    prev_start = cur_start - dt.timedelta(days=window_days)

    def slice_(lo: dt.date, hi: dt.date):
        return [s for s in all_stats
                if lo.isoformat() <= s.date <= hi.isoformat()
                and s.groups.get(group, 0) > 0]

    cur = slice_(cur_start, end)
    prev = slice_(prev_start, cur_start - dt.timedelta(days=1))

    def agg(sessions):
        sets = vol = 0.0
        pats: dict[str, float] = {}
        for s in sessions:
            for m in _movements_of(s):
                if m.group != group:
                    continue
                sets += m.sets_done
                vol += m.volume_kg or 0
                p = pattern_of(m.name, m.group)
                pats[p] = pats.get(p, 0) + m.sets_done
        return sets, vol, pats

    cs, cv, cp = agg(cur)
    ps, pv, pp = agg(prev)

    last = max((s.date for s in all_stats if s.groups.get(group, 0) > 0), default=None)
    return GroupWindow(
        group=group, window_days=window_days,
        sessions=Delta(float(len(prev)), float(len(cur))),
        sets=Delta(ps or None, cs or None),
        volume=Delta(pv or None, cv or None),
        pattern_sets=cp, prev_pattern_sets=pp,
        days_since_last=_days(as_of, last) if last else None)


# ── 4. 结构平衡 ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _balance_config() -> dict:
    try:
        return json.loads(BALANCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@dataclass
class BalanceFinding:
    group: str
    kind: str                 # ratio | share | presence
    subject: str
    detail: str               # 具体数字
    why: str
    fix: str
    severity: int = 1


def balance_findings(all_stats: list[SessionStats], as_of: str) -> list[BalanceFinding]:
    """滚动窗口内的模式结构失衡。

    只看窗口不看单次 —— 单次训练有侧重完全正常，甚至是好事（专项化）。
    值得提示的是**持续几周的结构性缺失**。
    """
    cfg = _balance_config()
    window = int(cfg.get("window_days", 28))
    floor = int(cfg.get("min_sets_to_judge", 8))
    out: list[BalanceFinding] = []

    for rule in cfg.get("rules", []):
        group = rule["group"]
        win = group_window(all_stats, group, as_of, window_days=window)
        total = sum(win.pattern_sets.values())
        if total < floor:
            continue   # 数据不够就不判断，避免误导
        pats = win.pattern_sets

        if rule["kind"] == "ratio":
            a, b = pats.get(rule["a"], 0), pats.get(rule["b"], 0)
            lo, hi = rule["healthy_range"]
            if a + b < floor:
                continue
            if b == 0:
                ratio = float("inf")
            else:
                ratio = a / b
            if ratio > hi:
                out.append(BalanceFinding(
                    group, "ratio", f"{rule['a']} / {rule['b']}",
                    f"过去 {window} 天：{rule['a']} {a:.0f} 组，{rule['b']} {b:.0f} 组"
                    + ("（完全没练）" if b == 0 else f"，比例 {ratio:.1f}:1"),
                    rule["why"], rule["fix_a_high"], severity=2 if b == 0 else 1))
            elif ratio < lo:
                out.append(BalanceFinding(
                    group, "ratio", f"{rule['b']} / {rule['a']}",
                    f"过去 {window} 天：{rule['b']} {b:.0f} 组，{rule['a']} {a:.0f} 组"
                    + ("（完全没练）" if a == 0 else f"，比例 {1 / ratio:.1f}:1"),
                    rule["why"], rule["fix_b_high"], severity=2 if a == 0 else 1))

        elif rule["kind"] == "share":
            part = pats.get(rule["of"], 0)
            base = sum(pats.get(p, 0) for p in rule["against"])
            if base < floor:
                continue
            share = part / base if base else 0
            lo, hi = rule["healthy_range"]
            if share < lo:
                out.append(BalanceFinding(
                    group, "share", rule["of"],
                    f"过去 {window} 天：{rule['of']} {part:.0f} 组 / 推类共 {base:.0f} 组"
                    f"（{share * 100:.0f}%，建议 {lo * 100:.0f}% 以上）",
                    rule["why"], rule["fix_low"], severity=2 if part == 0 else 1))
            elif share > hi:
                out.append(BalanceFinding(
                    group, "share", rule["of"],
                    f"过去 {window} 天：{rule['of']} 占推类 {share * 100:.0f}%"
                    f"（建议 {hi * 100:.0f}% 以下）",
                    rule["why"], rule["fix_high"]))

        elif rule["kind"] == "presence":
            need = rule["require"]
            if pats.get(need, 0) > 0:
                continue
            others = sum(pats.get(p, 0) for p in rule["when_present"])
            if others < floor:
                continue
            out.append(BalanceFinding(
                group, "presence", need,
                f"过去 {window} 天：{'、'.join(rule['when_present'])} 共 {others:.0f} 组，"
                f"但 {need} 一组都没有",
                rule["why"], rule["fix"], severity=2))
    return out
