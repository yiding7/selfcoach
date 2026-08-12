"""动作名 → 器械计量口径（「记录里那个重量代表什么」）。

和 `taxonomy.py`（动作名 → 肌群）是并列的两件事，故意分开：一个动作的肌群
和它的计量口径没有关系，混在一张表里会让两边都难改。

## 为什么需要它

训记只给一个 `unilateral` 布尔值，而它是**记录格式标记**不是解剖学标记 ——
为 true 只表示「这条记录带了左右两个重量」。同一对字段于是承载了两种意思：

    哑铃卧推        右=10    左=10     两只手各一个哑铃，同时推
    哑铃保加利亚蹲   右=10    左=15     不可能是两只手（一手 10 一手 15 会把人拽歪）

混成一类的后果是数字直接错：`哑铃划船 15kg` 被算成顶组 30kg。

规则表在 `knowledge/movements/implement-loading.json`，改表不用改代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .config import KNOWLEDGE_DIR

LOADING_PATH = KNOWLEDGE_DIR / "movements" / "implement-loading.json"

IMPLEMENTS = ("single", "pair")
SIDES = ("both", "per_side")

# 表文件缺失或坏掉时的兜底。**取最保守的一档** ——
# single/both 不放大任何数字，宁可少算也不要凭空把吨位翻倍。
FALLBACK = ("single", "both")


@dataclass(frozen=True)
class Loading:
    implements: str          # single = 一个器械；pair = 每手一个
    sides: str               # both = 两侧同时；per_side = 左右分别各做一组
    matched: str | None      # 命中的关键词；None = 走了 default
    why: str = ""
    needs_confirmation: bool = False

    @property
    def is_default(self) -> bool:
        """没命中任何关键词。**调用方必须能把这件事说出来** ——
        静默按默认值算是这个项目最不能接受的失败模式。"""
        return self.matched is None

    @property
    def factor(self) -> int:
        """单侧重量 → 单次提举等效负荷的倍数。"""
        return 2 if self.implements == "pair" else 1

    @property
    def per_side_sets(self) -> bool:
        """一「组」只覆盖一侧。为真时右/左两个重量是**两组各自的重量**，
        绝不能相加当成同一次提举的负荷。"""
        return self.sides == "per_side"


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        data = json.loads(LOADING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _valid(rule: object) -> bool:
    return (isinstance(rule, dict)
            and rule.get("implements") in IMPLEMENTS
            and rule.get("sides") in SIDES)


@lru_cache(maxsize=512)
def classify(name: str) -> Loading:
    """按表里的顺序**从上到下**匹配，命中即停。

    顺序有意义：更具体的关键词必须排在前面（`单臂哑铃划船` 在 `哑铃划船` 之前），
    否则永远命中不到。`tests/test_loading.py` 里有一条守卫盯着这个。
    """
    t = _table()
    n = (name or "").strip()

    for rule in t.get("rules") or []:
        if not _valid(rule):
            continue
        for kw in rule.get("match") or []:
            if kw and kw in n:
                return Loading(rule["implements"], rule["sides"], kw,
                               rule.get("why", ""),
                               bool(rule.get("needs_confirmation")))

    d = t.get("default")
    if _valid(d):
        return Loading(d["implements"], d["sides"], None, d.get("why", ""))
    return Loading(*FALLBACK, None,
                   "implement-loading.json 缺失或坏掉，退回最保守的口径")


def warnings() -> list[str]:
    """表本身的问题。给 `hc doctor` 用 —— 它要能说清「为什么口径没生效」。

    表坏掉的后果是所有哑铃动作静默退回 single/both，吨位差一倍而屏幕上
    一个字的错都没有。这正是忌口那一层立下的规矩要防的事。
    """
    out: list[str] = []
    if not LOADING_PATH.exists():
        return [f"缺少 {LOADING_PATH.name} —— 所有动作退回「一个器械、两侧同时」，"
                f"双哑铃动作的吨位会少算一半"]
    t = _table()
    if not t:
        return [f"{LOADING_PATH.name} 解析失败 —— 口径规则**没有生效**，"
                f"全部退回最保守的一档"]
    if not _valid(t.get("default")):
        out.append(f"{LOADING_PATH.name} 的 default 不合法，认不出的动作会走内置兜底")
    for i, rule in enumerate(t.get("rules") or []):
        if not _valid(rule):
            out.append(f"{LOADING_PATH.name} 第 {i + 1} 条规则不合法，已跳过："
                       f"implements 只能是 {'/'.join(IMPLEMENTS)}，"
                       f"sides 只能是 {'/'.join(SIDES)}")
    unconfirmed = [kw for rule in (t.get("rules") or []) if _valid(rule)
                   and rule.get("needs_confirmation")
                   for kw in (rule.get("match") or [])]
    if unconfirmed:
        out.append(f"这些动作的口径**还没确认**（{'、'.join(unconfirmed[:4])}…）——"
                   f"讲它们的顶组和估算 1RM 时要带上口径说明。"
                   f"见 implement-loading.json 的 _open_question")
    return out
