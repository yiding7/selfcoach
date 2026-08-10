"""训练计划配置 —— 周起始日、训练频率、单次时长、力量/有氧侧重。

## 为什么单独一个模块

这里放的是两类东西，它们凑在一起是因为都来自「用户对自己训练的安排」，
而不是因为它们相似：

1. **周起始日** —— 纯口径。它不影响任何一次训练怎么练，只影响「一周」这个
   桶怎么切。但它必须**全局唯一**：报告按周一切、骰子的黄灯额度按周日切，
   两个「本周」对不上，用户就再也不能信任任何一个带「本周」的数字。
   所以 `week_start_of()` 是这个仓库里唯一允许做周切分的地方。

2. **训练计划** —— 频率、单次时长、力量还是有氧为主。这些是**用户的选择**，
   不是脚本的推导结果。`suggest()` 会按目标给一份建议，但建议永远只是默认值：
   用户填什么就是什么，脚本不许拿建议去否定他填的值。

## 周起始日只给两个选项

周日和周一，没有第三个。ISO 8601 和中国大陆的日历习惯是周一，
而不少健康类 App（含苹果健康）默认周日。给两个够了 ——
再多的自由度只会让「本周」这个词更不可信。

⚠️ **改这个值会让历史周报的分桶跟着变。** 旧报告文件不会被重写，
所以同一个仓库里可能同时存在两种口径的周报。改之前想清楚，
`hc doctor` 会把当前口径打出来。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from . import store
from .config import PROFILE_PATH

# 显示名 → Python weekday() 的序号（周一=0 … 周日=6）
WEEK_STARTS: dict[str, int] = {"周一": 0, "周日": 6}
DEFAULT_WEEK_START = "周一"

# 用户直接填英文时也认
_WEEK_ALIASES = {
    "monday": "周一", "mon": "周一", "周一": "周一", "1": "周一",
    "sunday": "周日", "sun": "周日", "周日": "周日", "周天": "周日", "0": "周日",
}

FOCUS_CHOICES = ("力量为主", "有氧为主", "力量有氧并重")
DEFAULT_FOCUS = "力量为主"

# 单次训练组数上限由时长推算时用的密度（组/分钟）。
# 这个数是从用户实际记录里量出来的，不是拍的：2026-08-10 的 28 组 / 53 分钟
# = 0.53。取 0.5 略保守一点，宁可估出来的上限偏小。
SETS_PER_MINUTE = 0.5

PROFILE_FIELD_COMMENT = (
    "训练计划。week_start 是全局的「一周」口径（周一/周日），"
    "影响周报分桶和骰子的每周黄灯计数，不影响任何一次训练怎么练。"
    "days_per_week / session_minutes / focus 是用户自己的安排，"
    "hc setup 会按目标给一份建议作默认值，但**以用户填的为准**，"
    "脚本不许拿建议去否定它。改这一块用 hc setup。")


@dataclass(frozen=True)
class TrainingPlan:
    week_start: str = DEFAULT_WEEK_START
    days_per_week: int | None = None
    session_minutes: int | None = None
    focus: str = DEFAULT_FOCUS

    @property
    def week_start_index(self) -> int:
        return WEEK_STARTS.get(self.week_start, 0)

    @property
    def session_set_cap(self) -> int | None:
        """单次训练的组数上限。没填时长就返回 None —— 不猜。"""
        if not self.session_minutes:
            return None
        return int(self.session_minutes * SETS_PER_MINUTE)


def normalize_week_start(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _WEEK_ALIASES.get(str(raw).strip().lower())


def current() -> TrainingPlan:
    """读 data/profile.json 的 training 块。读不到就用默认值，绝不抛异常。"""
    p = store.read_json(PROFILE_PATH, default={}) or {}
    t = p.get("training") or {}
    if not isinstance(t, dict):
        t = {}

    ws = normalize_week_start(t.get("week_start")) or DEFAULT_WEEK_START

    def _int(key: str) -> int | None:
        v = t.get(key)
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    focus = t.get("focus")
    focus = focus if focus in FOCUS_CHOICES else DEFAULT_FOCUS

    return TrainingPlan(week_start=ws, days_per_week=_int("days_per_week"),
                        session_minutes=_int("session_minutes"), focus=focus)


def week_start_of(day: dt.date, *, plan: TrainingPlan | None = None) -> dt.date:
    """把日期落到它所属那一周的第一天。

    **整个仓库只有这一个地方做周切分。** 别的模块要按周聚合就调它，
    不要再写 `d - timedelta(days=d.weekday())` —— 那行代码写死了周一。
    """
    idx = (plan or current()).week_start_index
    return day - dt.timedelta(days=(day.weekday() - idx) % 7)


def week_bounds(day: dt.date, *, plan: TrainingPlan | None = None
                ) -> tuple[dt.date, dt.date]:
    start = week_start_of(day, plan=plan)
    return start, start + dt.timedelta(days=6)


def week_label_anchor(start: dt.date, *, plan: TrainingPlan | None = None) -> dt.date:
    """给周报算「第几周」用的锚点。

    `isocalendar()` 按定义是周一起算的，周日起算时直接拿 start 去问会把
    周日那一天算进上一周，编号差 1。取这一周里的那个周一去问，编号就稳定了。
    """
    idx = (plan or current()).week_start_index
    return start if idx == 0 else start + dt.timedelta(days=(7 - idx) % 7)


def weekday_labels(*, plan: TrainingPlan | None = None) -> list[str]:
    """日历热力图的行标，按当前周起始日轮转。"""
    base = ["一", "二", "三", "四", "五", "六", "日"]
    idx = (plan or current()).week_start_index
    return base[idx:] + base[:idx]


# 实际频率低于设定值多少才值得提一句。
# 0.7 = 设定 4 练、实际不到 2.8 练。留这么宽是刻意的：
# 「不勉强训练，状态不好优先补觉」是用户自己定的原则，
# 一个天天催你达标的工具会先被关掉，然后才轮到训练计划失败。
UNDERSHOOT_RATIO = 0.7
UNDERSHOOT_MIN_WEEKS = 6


def frequency_drift(actual: tuple[float, float] | None, *,
                    plan: TrainingPlan | None = None) -> str | None:
    """长期低于设定频率时给一句提醒。够不着阈值就返回 None。

    `actual` 是 `(次/周, 实际覆盖的周数)`，由 `setup.actual_days_per_week()` 给。
    **周数必须是实测的**，不能用调用方的默认值 —— 否则历史只有三周的人
    也会被当成「已经观察了八周，确实练得少」。

    措辞上刻意问「要不要把这个数改小」而不是「你练少了」—— 设定值是排计划用的
    参考，不是考核指标。一个长期够不着的目标，问题多半在目标不在人。
    """
    p = plan or current()
    if not p.days_per_week or not actual:
        return None
    actual_days, weeks = actual
    if weeks < UNDERSHOOT_MIN_WEEKS:
        return None
    if actual_days >= p.days_per_week * UNDERSHOOT_RATIO:
        return None
    return (f"近 {weeks:g} 周实际 {actual_days:.1f} 次/周，设定是 {p.days_per_week} 次。"
            f"要不要把设定值改成 {max(1, round(actual_days))} 次？"
            f"—— 这个数只是排计划用的参考，改小了计划会更贴合，"
            f"不改也没关系，不影响任何判断。")


def suggest(*, goal_metric: str | None = None, phase: str | None = None,
            current_days: tuple[float, float] | None = None) -> tuple[dict, list[str]]:
    """按目标给一份训练计划建议。返回 (建议值, 理由若干行)。

    **这是默认值，不是处方。** 调用方必须让用户能改，改了就以用户的为准。
    理由要一起给出去 —— 一个不说为什么的默认值等于没有默认值。
    """
    why: list[str] = []

    if phase == "增肌":
        days, focus = 4, "力量为主"
        why.append("增肌期：力量为主，频率 4 次/周 —— 每块肌肉一周碰到两次，"
                   "比一周一次的分化更容易累积容量。")
    elif phase == "减脂":
        days, focus = 3, "力量有氧并重"
        why.append("减脂期：热量缺口下恢复能力打折，力量训练的作用是**保住肌肉**、"
                   "不是冲新高；有氧负责扩大缺口。所以是并重，不是有氧为主。")
        why.append("频率 3 次/周是保底值 —— 缺口期加频率不如把每次练扎实。")
    else:
        days, focus = 3, "力量为主"
        why.append("维持期：力量为主，3 次/周即可覆盖主要肌群。")

    if goal_metric == "腰围":
        why.append("主指标是腰围：腰围要降得靠热量缺口，训练这一侧的任务是"
                   "**别掉肌肉**。所以频率和时长都不必堆高。")

    if current_days is not None:
        rate, weeks = current_days
        # 把「拿几周算的」一起说出来。只有两周记录时那个比例参考价值很低，
        # 不写清楚的话它看起来和八周的统计一样权威。
        why.append(f"参考：你近 {weeks:g} 周实际平均 {rate:.1f} 次/周。"
                   + ("" if weeks >= UNDERSHOOT_MIN_WEEKS
                      else f"（记录只有 {weeks:g} 周，这个数还不够稳，仅供参考）")
                   + "建议值只是默认值，按你的实际时间填。")

    return ({"days_per_week": days, "session_minutes": 50, "focus": focus}, why)
