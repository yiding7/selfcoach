"""本地数据层。

两条铁律：

1. **原始响应永不丢弃。** 训记训练接口一天一查、限频 30 秒，拉一年要 3 小时。
   所以接口返回的原始 JSON 原样落盘到 `data/training/raw/`，规范化结果是它的
   纯函数派生物。以后改了字段解析逻辑，`hc rebuild` 离线重算即可，一次网络都不用。

2. **字节稳定序列化。** 同样的输入必须产出逐字节相同的文件，否则 data/ 放进
   私有 git 仓库做跨设备同步时，每次同步都会产生一堆假 diff。
   所以：固定排序、固定分隔符、ensure_ascii=False、原子写。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .config import BODY_DIR, MEALS_DIR, TRAINING_DIR, ensure_dirs

RAW_DIR = TRAINING_DIR / "raw"


# ── 字节稳定的读写原语 ──────────────────────────────────────────────────

def dumps(obj: Any) -> str:
    """规范 JSON：排序键、紧凑分隔符、保留中文。同输入 ⇒ 同字节。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def dumps_pretty(obj: Any) -> str:
    """人读版本，同样排序，用于需要手改的文件（index、profile）。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_atomic(path: Path, text: str) -> None:
    """写临时文件再 os.replace，避免 Ctrl-C 留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def write_jsonl(path: Path, records: Iterable[dict], *, sort_key=None) -> None:
    rows = list(records)
    if sort_key is not None:
        rows.sort(key=sort_key)
    write_atomic(path, "".join(dumps(r) + "\n" for r in rows))


# ── 训练：原始缓存 ──────────────────────────────────────────────────────

def raw_training_path(datestr: str) -> Path:
    return RAW_DIR / datestr[:4] / f"{datestr}.json"


def save_raw_training(datestr: str, payload: dict) -> None:
    write_atomic(raw_training_path(datestr), dumps_pretty(payload))


def load_raw_training(datestr: str) -> dict | None:
    return read_json(raw_training_path(datestr))


def raw_training_dates() -> list[str]:
    if not RAW_DIR.exists():
        return []
    return sorted(p.stem for p in RAW_DIR.rglob("*.json"))


# ── 训练：抓取水位 ──────────────────────────────────────────────────────
#
# 空日期也必须记录。否则每次同步都要花 15~30 秒去重新确认一个本来就没练的日子，
# 一年下来是几个小时的纯浪费。

def index_path(year: int | str) -> Path:
    return TRAINING_DIR / str(year) / "index.json"


def load_index(year: int | str) -> dict[str, dict]:
    return read_json(index_path(year), default={}) or {}


def save_index(year: int | str, index: dict[str, dict]) -> None:
    write_atomic(index_path(year), dumps_pretty(index))


def index_entry(datestr: str) -> dict | None:
    return load_index(datestr[:4]).get(datestr)


def mark_fetched(datestr: str, *, status: str, mode: str, trains: int, fetched_at: str) -> None:
    year = datestr[:4]
    idx = load_index(year)
    idx[datestr] = {
        "status": status,          # ok | empty | error
        "mode": mode,              # full | light
        "trains": trains,
        "fetched_at": fetched_at,
    }
    save_index(year, idx)


# ── 训练：规范化后的会话 ────────────────────────────────────────────────

def sessions_path(year_month: str) -> Path:
    """year_month 形如 '2026-07'。按月分片，缩小 git 冲突面。"""
    return TRAINING_DIR / year_month[:4] / f"{year_month}.jsonl"


def load_sessions_month(year_month: str) -> list[dict]:
    return read_jsonl(sessions_path(year_month))


def save_sessions_month(year_month: str, sessions: list[dict]) -> None:
    write_jsonl(
        sessions_path(year_month),
        sessions,
        sort_key=lambda s: (s.get("date", ""), s.get("start_ms") or 0, s.get("id", "")),
    )


def upsert_sessions(sessions: list[dict]) -> int:
    """按 id 覆盖写入。同一天重复同步不会产生重复行，也不会产生 git diff。"""
    by_month: dict[str, list[dict]] = {}
    for s in sessions:
        by_month.setdefault(s["date"][:7], []).append(s)

    written = 0
    for ym, incoming in by_month.items():
        existing = {r["id"]: r for r in load_sessions_month(ym)}
        # 同一天的旧记录先清掉，再放入本次结果 —— 这样在训记里删掉的训练也会同步消失
        dates_touched = {s["date"] for s in incoming}
        existing = {
            rid: r for rid, r in existing.items()
            if r["date"] not in dates_touched or r.get("source") != "xunji"
        }
        for s in incoming:
            existing[s["id"]] = s
        save_sessions_month(ym, list(existing.values()))
        written += len(incoming)
    return written


def load_sessions(start: str | None = None, end: str | None = None) -> list[dict]:
    """读取日期区间内的全部会话（含手记）。区间为闭区间的 YYYY-MM-DD。"""
    if not TRAINING_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(TRAINING_DIR.rglob("*.jsonl")):
        out.extend(read_jsonl(p))
    if start:
        out = [s for s in out if s.get("date", "") >= start]
    if end:
        out = [s for s in out if s.get("date", "") <= end]
    out.sort(key=lambda s: (s.get("date", ""), s.get("start_ms") or 0, s.get("id", "")))
    return out


# ── 身体数据 ────────────────────────────────────────────────────────────

def body_path(year: int | str) -> Path:
    return BODY_DIR / f"{year}.jsonl"


def upsert_body(records: list[dict]) -> int:
    by_year: dict[str, list[dict]] = {}
    for r in records:
        by_year.setdefault(r["date"][:4], []).append(r)
    n = 0
    for year, incoming in by_year.items():
        path = body_path(year)
        existing = {r["id"]: r for r in read_jsonl(path)}
        for r in incoming:
            existing[r["id"]] = r
        write_jsonl(path, list(existing.values()),
                    sort_key=lambda r: (r["date"], r["type"], r["id"]))
        n += len(incoming)
    return n


def load_body(start: str | None = None, end: str | None = None,
              types: list[str] | None = None) -> list[dict]:
    if not BODY_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(BODY_DIR.glob("*.jsonl")):
        out.extend(read_jsonl(p))
    if start:
        out = [r for r in out if r["date"] >= start]
    if end:
        out = [r for r in out if r["date"] <= end]
    if types:
        out = [r for r in out if r["type"] in types]
    out.sort(key=lambda r: (r["date"], r["type"]))
    return out


# ── 饮食 ────────────────────────────────────────────────────────────────

def meals_path(year_month: str) -> Path:
    return MEALS_DIR / f"{year_month}.jsonl"


def upsert_meals(records: list[dict]) -> int:
    by_month: dict[str, list[dict]] = {}
    for r in records:
        by_month.setdefault(r["date"][:7], []).append(r)
    n = 0
    for ym, incoming in by_month.items():
        path = meals_path(ym)
        existing = {r["id"]: r for r in read_jsonl(path)}
        for r in incoming:
            existing[r["id"]] = r
        write_jsonl(path, list(existing.values()),
                    sort_key=lambda r: (r["date"], r.get("meal", ""), r["id"]))
        n += len(incoming)
    return n


def load_meals(start: str | None = None, end: str | None = None) -> list[dict]:
    if not MEALS_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(MEALS_DIR.glob("*.jsonl")):
        out.extend(read_jsonl(p))
    if start:
        out = [r for r in out if r["date"] >= start]
    if end:
        out = [r for r in out if r["date"] <= end]
    out.sort(key=lambda r: (r["date"], r.get("meal", "")))
    return out


def init() -> None:
    ensure_dirs()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
