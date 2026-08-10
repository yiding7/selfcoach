"""动作模式与变体族。

肌群回答「练的是哪块肉」，模式回答「用的是哪种发力方式」。
两者都需要，因为同一个部位的选材每次都在变：
真实数据里背部有 5 个垂直拉变体和 9 个水平拉变体，
两次背日按动作名可能一个都对不上，但按模式看得清清楚楚。

查表按肌群分域，先定肌群再在该肌群的列表里匹配 —— 这样
「上斜哑铃三头伸展」不会被胸部的「上斜推」规则抢走。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import KNOWLEDGE_DIR
from .taxonomy import classify_movement

PATTERNS_PATH = KNOWLEDGE_DIR / "movements" / "movement-patterns.json"


@lru_cache(maxsize=1)
def _config() -> dict:
    try:
        return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _compiled() -> dict[str, list[tuple[re.Pattern, str]]]:
    out: dict[str, list[tuple[re.Pattern, str]]] = {}
    for group, rules in (_config().get("patterns") or {}).items():
        out[group] = [(re.compile(r["pattern"]), r["name"]) for r in rules]
    return out


@lru_cache(maxsize=1)
def _family_re() -> re.Pattern | None:
    quals = _config().get("family_qualifiers") or []
    if not quals:
        return None
    return re.compile("|".join(quals))


@lru_cache(maxsize=512)
def pattern_of(name: str, group: str) -> str:
    """动作 → 模式。按肌群分域查表，最后一条 "." 兜底，永远有返回值。"""
    for rx, pname in _compiled().get(group, []):
        if rx.search(name or ""):
            return pname
    return group or "未分类"


def pattern_of_movement(movement: dict) -> tuple[str, str]:
    """返回 (肌群, 模式)。"""
    group = classify_movement(movement).group
    return group, pattern_of(movement.get("name", ""), group)


@lru_cache(maxsize=512)
def family_of(name: str) -> str:
    """剥掉握法/握距/版本号，得到基础动作名。

    「悍马机划船（版本2）」→「悍马机划船」
    「宽距高位下拉」→「高位下拉」

    刻意保守：不剥离器械品牌（器械划船和悍马机划船是两台机器，重量刻度不通用）、
    不剥离体位（俯卧哑铃划船 ≠ 哑铃划船）、不剥离任意括号
    （引体向上（辅助）和引体向上是完全不同的负荷）。

    ⚠️ 同族 ≠ 可以直接比重量，只是比「同模式不同动作」更接近。
    族内对比的置信度是 variant，低于精确同名的 exact。
    """
    rx = _family_re()
    base = (name or "").strip()
    if rx:
        base = rx.sub("", base)
    base = re.sub(r"[\s　]+", "", base)
    # 全剥没了说明这个名字整个都是修饰词，退回原名更安全
    return base or (name or "").strip()


def pattern_note(group: str, pattern: str) -> str | None:
    for r in (_config().get("patterns") or {}).get(group, []):
        if r["name"] == pattern:
            return r.get("note")
    return None


def patterns_for_group(group: str) -> list[str]:
    """某个肌群下所有可能的模式，按表里的顺序。"""
    seen, out = set(), []
    for r in (_config().get("patterns") or {}).get(group, []):
        if r["name"] not in seen:
            seen.add(r["name"])
            out.append(r["name"])
    return out
