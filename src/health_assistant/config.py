"""路径解析与 .env 加载。

刻意不用 python-dotenv —— 整个项目的承诺是零依赖。
.env 的语法我们只支持最朴素的那部分：KEY=VALUE、# 注释、空行。
"""

from __future__ import annotations

import os
from pathlib import Path

# src/health_assistant/config.py -> 仓库根
ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
TRAINING_DIR = DATA_DIR / "training"
BODY_DIR = DATA_DIR / "body"
MEALS_DIR = DATA_DIR / "meals"
PROFILE_PATH = DATA_DIR / "profile.json"

KNOWLEDGE_DIR = ROOT / "knowledge"
REPORTS_DIR = ROOT / "reports"
SKILLS_DIR = ROOT / "skills"

ENV_PATH = ROOT / ".env"

_ENV_LOADED = False


def load_env(path: Path | None = None, *, override: bool = False) -> None:
    """把 .env 读进 os.environ。已存在的真实环境变量优先，除非 override。

    真实环境变量优先是有意的：CI 或 cron 里用注入的变量覆盖本地 .env 才顺手。
    """
    global _ENV_LOADED
    env_path = path or ENV_PATH
    if _ENV_LOADED and path is None and not override:
        return
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
    if path is None:
        _ENV_LOADED = True


# ── 训记凭证 ────────────────────────────────────────────────────────────
# 名字 -> (环境变量, 人类可读的用途)
CREDENTIALS = {
    "train": ("XUNJI_TRAIN_KEY", "训练记录读写 + 官方计划"),
    "food": ("XUNJI_FOOD_KEY", "饮食记录读写 + 自定义食物 + 模板"),
    "food_search": ("XUNJI_FOOD_SEARCH_KEY", "官方食物库搜索"),
    "body": ("XUNJI_BODY_KEY", "身体数据读写"),
}

# .env.example 里的占位值，不能当成真 key
_PLACEHOLDER_MARKERS = ("xxxx", "your", "changeme", "<", "placeholder")


def get_key(name: str) -> str | None:
    """取一把训记 key。没配、或还是占位值，都返回 None。"""
    load_env()
    env_var, _ = CREDENTIALS[name]
    value = (os.environ.get(env_var) or "").strip()
    if not value:
        return None
    low = value.lower()
    if any(marker in low for marker in _PLACEHOLDER_MARKERS):
        return None
    return value


def ensure_dirs() -> None:
    for d in (DATA_DIR, TRAINING_DIR, BODY_DIR, MEALS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def redact(text: str) -> str:
    """把任何形似训记 key 的串抹掉。所有面向用户/日志的输出都要过这一道。"""
    import re

    text = re.sub(r"\b(xj(?:llm|food|body)_)[A-Za-z0-9]{8,}", r"\1***", text)
    for name in CREDENTIALS:
        key = get_key(name)
        if key:
            text = text.replace(key, "***")
    return text
