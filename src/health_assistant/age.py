"""年龄 —— 唯一真相源。

## 为什么单独一个模块

年龄本来在两个地方各算了一遍：`nutrition.age_years()` 和 `cardio.age_now()`，
两边都写着 `today.year - birth_year`。同一个公式抄两份，迟早有一份被改漏 ——
这正是这个项目在别处反复强调的「一个值只有一个真相源」。

## 精度：YYYY-MM，不是 YYYY

原来只存 `birth_year`，年龄按「今年 − 出生年」算，**全年都可能差一岁**：
1993-11 出生的人在 2026-03 实际 32 岁，那个减法给 33。
基础代谢和最大心率都吃年龄，差一岁不致命但没必要。

现在存 `birth_month`（可选）。**约定：把生日当成那个月的 1 号** ——
只到月的精度，再细就是假精确。所以 11 月生的人在 11 月 1 日涨一岁。

`birth_year` 保持原样不动，老档案照常能用，只是月份未知时退回旧口径
并在 `source` 里说明。
"""

from __future__ import annotations

import datetime as dt
import json

from .config import PROFILE_PATH


def _profile() -> dict:
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def birth_ym(profile: dict | None = None) -> tuple[int | None, int | None]:
    """返回 (年, 月)。月份缺失或非法时返回 None，不猜。"""
    p = _profile() if profile is None else profile
    try:
        year = int(p.get("birth_year")) if p.get("birth_year") else None
    except (TypeError, ValueError):
        year = None
    try:
        month = int(p.get("birth_month")) if p.get("birth_month") else None
    except (TypeError, ValueError):
        month = None
    if month is not None and not (1 <= month <= 12):
        month = None
    return year, month


def parse_ym(raw: str) -> tuple[int, int | None] | None:
    """解析 `YYYY-MM` 或 `YYYY`。分隔符宽松：- / . 空格 年月 都认。

    返回 (年, 月or None)；解析不出来返回 None，**不做任何猜测**。
    """
    import re
    s = (raw or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})\s*[-/.年\s]?\s*(\d{1,2})?\s*月?", s)
    if not m:
        return None
    year = int(m.group(1))
    if not (1900 <= year <= dt.date.today().year):
        return None
    month = int(m.group(2)) if m.group(2) else None
    if month is not None and not (1 <= month <= 12):
        return None
    return year, month


def format_ym(profile: dict | None = None) -> str:
    year, month = birth_ym(profile)
    if year is None:
        return ""
    return f"{year}-{month:02d}" if month else str(year)


def age_at(today: dt.date | None = None, profile: dict | None = None
           ) -> tuple[int | None, str]:
    """返回 (年龄, 口径说明)。

    说明要一起返回 —— 只有年份时算出来的年龄全年都可能偏大一岁，
    调用方有责任让用户知道这个数是怎么来的。
    """
    today = today or dt.date.today()
    year, month = birth_ym(profile)
    if year is None:
        return None, "缺出生年月"
    if month is None:
        return today.year - year, "只有出生年，未按月份校正（可能偏大 1 岁）"
    age = today.year - year - ((today.month, today.day) < (month, 1))
    return age, f"按出生年月 {year}-{month:02d}（生日按当月 1 号计）"


def age(today: dt.date | None = None, profile: dict | None = None) -> int | None:
    return age_at(today, profile)[0]
