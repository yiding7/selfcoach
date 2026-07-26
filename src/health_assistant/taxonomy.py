"""动作名 → 肌群。三级回退。

  1. 训记接口返回的 `movements[].type`      —— 最可信，但实测约 60% 为空
  2. 预分类表 knowledge/movement-taxonomy.json —— 覆盖官方 1092 个动作名
  3. 关键词规则 knowledge/movement-rules.json  —— 兜底，也覆盖自定义动作名
  4. 用户覆盖 data/movement-overrides.json     —— 优先级最高，教一次记一辈子

刻意放在 analytics 层而不是 normalize 层：这样改进分类规则会立刻对全部历史生效，
不需要 rebuild，也不需要重新同步。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from .config import DATA_DIR, KNOWLEDGE_DIR

RULES_PATH = KNOWLEDGE_DIR / "movement-rules.json"
TAXONOMY_PATH = KNOWLEDGE_DIR / "movement-taxonomy.json"
OVERRIDES_PATH = DATA_DIR / "movement-overrides.json"

UNKNOWN = "未分类"

# 报告里的展示顺序（推 → 拉 → 腿 → 核心 → 其它）
GROUP_ORDER = ["胸", "背", "肩", "二头", "三头", "前臂", "腿", "臀", "小腿",
               "腹部", "颈", "有氧", "全身", UNKNOWN]


@dataclass(frozen=True)
class Classification:
    group: str
    source: str   # override | xunji_type | taxonomy | rule | unknown

    @property
    def is_known(self) -> bool:
        return self.group != UNKNOWN


@lru_cache(maxsize=1)
def _rules() -> list[tuple[re.Pattern, str]]:
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [(re.compile(r["pattern"]), r["group"]) for r in data.get("rules", [])]


@lru_cache(maxsize=1)
def _taxonomy() -> dict[str, str]:
    try:
        data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("movements", {})


@lru_cache(maxsize=1)
def _overrides() -> dict[str, str]:
    try:
        return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _valid_groups() -> set[str]:
    try:
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        return set(data.get("groups", []))
    except (OSError, json.JSONDecodeError):
        return set()


def normalize_group(raw: str | None) -> str | None:
    """把训记返回的 type 规整到我们的词表。"""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if s in _valid_groups():
        return s
    # 训记偶尔用别名
    alias = {
        "肱二头": "二头", "肱三头": "三头", "肱二头肌": "二头", "肱三头肌": "三头",
        "腹肌": "腹部", "核心": "腹部", "腹": "腹部",
        "股四头": "腿", "腘绳": "腿", "大腿": "腿", "腿部": "腿",
        "臀部": "臀", "肩部": "肩", "背部": "背", "胸部": "胸",
        "有氧运动": "有氧", "心肺": "有氧",
    }
    return alias.get(s, s)


def classify(name: str, raw_type: str | None = None) -> Classification:
    name = (name or "").strip()

    ov = _overrides().get(name)
    if ov:
        return Classification(ov, "override")

    g = normalize_group(raw_type)
    if g:
        return Classification(g, "xunji_type")

    tx = _taxonomy().get(name)
    if tx:
        return Classification(tx, "taxonomy")

    for pattern, group in _rules():
        if pattern.search(name):
            return Classification(group, "rule")

    return Classification(UNKNOWN, "unknown")


def classify_movement(movement: dict) -> Classification:
    return classify(movement.get("name", ""), movement.get("raw_type"))


def learn(name: str, group: str) -> None:
    """把一个动作的归属教给工具，写入用户覆盖表。"""
    ov = dict(_overrides())
    ov[name.strip()] = group.strip()
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(
        json.dumps(ov, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    _overrides.cache_clear()


def sort_groups(groups) -> list[str]:
    index = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(groups, key=lambda g: (index.get(g, 999), g))
