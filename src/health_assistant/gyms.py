"""训练场地 —— 「这次是在哪个馆练的」。

## 为什么单独开一个字段

到 2026-08-21 为止，本项目所有的负荷口径事故追下去都是同一件事：**换了台机器**。
`hc calib` 扫出的 5 处跳变全部横跨 2026-07-23（用户换馆那天），哈克机深蹲和
腿举的两条起算线也是。场地是那个能**预测**机器变化的变量。

标注量是 `训练次数 × 1`，一次到位；而手写 calib 规则的工作量是
`动作数 × 换馆次数`，而且全靠人当场想起来查。

## 为什么不写进训记的「心得」

训记**没有独立的热量字段** —— 每次的 kcal 就塞在心得里（`"calorie:228"`，
见 `xunji/normalize.py` 的 `_CALORIE_RE`）。往心得里写一个字，那次的热量就没了。
实测：2026-08-16/18/20/21 四次写了馆名，四次都丢了 kcal。

所以场地存在本地：`data/gyms.jsonl`，和原始记录彻底分开。
**原始文件永远不改**，和 `calibration.py` 是同一条纪律。

## 空是答案，不是缺失

没标场地的训练，`gym_of()` 返回 `None`，对比行为**和加这个字段之前完全一样**。
绝不猜 —— 用户 2026-08-21 明确说过「工作日一个馆、周末一个馆」这条规律
**不一定准**，不许拿它去自动填任何一天。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from dataclasses import dataclass
from functools import lru_cache

from . import store
from .config import DATA_DIR, KNOWLEDGE_DIR

# ⚠️ **不能放在 data/training/ 下面。** `store.load_sessions()` 是
# `TRAINING_DIR.rglob("*.jsonl")` —— 那个目录里任何一个 jsonl 都会被当成训练记录
# 读进去。第一版放在那儿，四行场地标注就变成了四次「0 个动作」的幽灵训练，
# `hc compare` 直接对着空训练报「无有效动作」。和 load-calibration.jsonl 同级才对。
PATH = DATA_DIR / "gyms.jsonl"
SITE_PATH = KNOWLEDGE_DIR / "movements" / "site-dependence.json"

# 认不出器械类型时的兜底：当作不可跨馆比。
# 不对称的代价 —— 漏报一次进步只是「这次没结论」，错报一次假进步会让他
# 照着一个不存在的数字去加重。
FALLBACK_PORTABLE = False


# ── 场地标注 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    id: str
    ts: str
    date: str
    gym: str
    note: str = ""

    def describe(self) -> str:
        tail = f"　{self.note}" if self.note else ""
        return f"[{self.id}] {self.date}　{self.gym}{tail}"


def _raw() -> list[dict]:
    try:
        return store.read_jsonl(PATH)
    except OSError:
        return []


def load_entries() -> list[Entry]:
    """读全部标注。同一天有多条时**后写的赢** —— 文件只追加，改名字就再写一条。"""
    out: dict[str, Entry] = {}
    for r in _raw():
        date = str(r.get("date") or "")
        gym = str(r.get("gym") or "").strip()
        if not _valid_date(date):
            continue
        if not gym:                      # 空 = 撤销这天的标注
            out.pop(date, None)
            continue
        out[date] = Entry(id=str(r.get("id") or ""), ts=str(r.get("ts") or ""),
                          date=date, gym=gym, note=str(r.get("note") or ""))
    return [out[k] for k in sorted(out)]


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    return {e.date: e.gym for e in load_entries()}


def gym_of(date: str) -> str | None:
    """那天在哪个馆。没标过就是 None —— **不猜**。"""
    return _index().get(date)


def names() -> list[str]:
    """标注里出现过的所有馆名，按出现次数从多到少。"""
    counts: dict[str, int] = {}
    for e in load_entries():
        counts[e.gym] = counts.get(e.gym, 0) + 1
    return sorted(counts, key=lambda n: (-counts[n], n))


def _valid_date(d: str) -> bool:
    try:
        dt.date.fromisoformat(d)
        return True
    except (TypeError, ValueError):
        return False


def _next_id(today: dt.date, taken: int = 0) -> str:
    prefix = today.strftime("%Y%m%d")
    n = sum(1 for r in _raw() if str(r.get("id", "")).startswith(prefix))
    return f"{prefix}-{n + taken + 1:02d}"


def set_gym(date: str, gym: str, *, note: str = "",
            today: dt.date | None = None, _seq: int = 0) -> Entry:
    """标一天的场地。传空字符串 = 撤销这天的标注（仍然是追加一行）。"""
    if not _valid_date(date):
        raise ValueError(f"日期要写成 YYYY-MM-DD，收到 {date!r}")
    gym = (gym or "").strip()
    today = today or dt.date.today()
    rec = {"id": _next_id(today, _seq), "ts": dt.datetime.now().isoformat(timespec="seconds"),
           "date": date, "gym": gym, "note": (note or "").strip()}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with PATH.open("a", encoding="utf-8") as fh:
        fh.write(store.dumps(rec) + "\n")
    _index.cache_clear()
    return Entry(rec["id"], rec["ts"], date, gym, rec["note"])


def set_many(pairs, *, today: dt.date | None = None) -> int:
    """批量标注。返回**实际写入**的条数 —— 和现有值相同的会跳过。

    跳过相同值不只是省事：文件是追加的，把 87 行没有信息量的重复写进去，
    半年后回看就再也分不清哪一次是真的改了主意。
    """
    cur = dict(_index())
    n = 0
    for date, gym, note in pairs:
        gym = (gym or "").strip()
        if cur.get(date, "") == gym:
            continue
        set_gym(date, gym, note=note or "", today=today, _seq=n)
        cur[date] = gym
        n += 1
    return n


def apply_to(sessions: list[dict]) -> list[dict]:
    """给会话挂上 `gym` 字段。没标过的不挂 —— 缺字段和空字符串是两回事。"""
    idx = _index()
    if not idx:
        return sessions
    out = []
    for s in sessions:
        g = idx.get(s.get("date", ""))
        out.append({**s, "gym": g} if g else s)
    return out


# ── 场地依赖性 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SiteDependence:
    portable: bool           # True = 负荷跨馆成立
    matched: str | None      # 命中的关键词；None = 走了默认值
    why: str

    @property
    def is_default(self) -> bool:
        return self.matched is None


@lru_cache(maxsize=1)
def _site_table() -> dict:
    try:
        data = json.loads(SITE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=512)
def site_dependence(name: str) -> SiteDependence:
    """动作名 → 换馆之后这个重量还成不成立。

    按 `rules` 的顺序从上到下匹配，命中即停。器械类排在自由重量之前 ——
    `史密斯机深蹲` 里既有「史密斯」也有「深蹲」，谁在前谁说了算。
    """
    t = _site_table()
    n = (name or "").strip()
    for rule in t.get("rules") or []:
        if not isinstance(rule, dict) or not isinstance(rule.get("portable"), bool):
            continue
        for kw in rule.get("match") or []:
            if kw and kw.lower() in n.lower():
                return SiteDependence(rule["portable"], kw, rule.get("why", ""))
    d = t.get("default")
    if isinstance(d, dict) and isinstance(d.get("portable"), bool):
        return SiteDependence(d["portable"], None, d.get("why", ""))
    return SiteDependence(FALLBACK_PORTABLE, None,
                          "site-dependence.json 缺失或坏掉，退回最保守的一档")


def warnings() -> list[str]:
    """表本身的问题。`hc doctor` 会打出来 —— 静默失效是这个项目最不能接受的。"""
    t = _site_table()
    if not t:
        return [f"缺少 {SITE_PATH.name} —— 所有动作退回「不可跨馆比」，"
                f"换馆之后连深蹲的进步也会被藏起来"]
    out = []
    for i, rule in enumerate(t.get("rules") or []):
        if not isinstance(rule, dict):
            out.append(f"site-dependence.json rules[{i}] 不是对象")
        elif not isinstance(rule.get("portable"), bool):
            out.append(f"site-dependence.json rules[{i}] 的 portable 必须是 true/false")
        elif not rule.get("match"):
            out.append(f"site-dependence.json rules[{i}] 没有 match 关键词，永远命中不到")
    return out


def unclassified(movement_names) -> list[str]:
    """走了默认值的动作 —— 值得补进表里。去重后按名字排序。"""
    return sorted({n for n in movement_names if site_dependence(n).is_default})


# ── 导出 / 导入（补录一整年用）────────────────────────────────────────────

COLUMNS = ("date", "weekday", "title", "movements", "gym")
_WEEKDAY = "一二三四五六日"


def export_rows(sessions: list[dict], *, only_missing: bool = False) -> list[list[str]]:
    """一天一行，`gym` 列留给用户填。已标过的把现值填进去，改它就是改标注。

    合并同一天的多次训练：用户想的是「那天在哪练的」，不是「那次」。
    真出现同一天跨两个馆的情况再拆，现在拆只会让 87 行变成 91 行。
    """
    idx = _index()
    by_date: dict[str, list[dict]] = {}
    for s in sessions:
        by_date.setdefault(s.get("date", ""), []).append(s)

    rows = []
    for date in sorted(by_date):
        if not _valid_date(date):
            continue
        cur = idx.get(date, "")
        if only_missing and cur:
            continue
        day = by_date[date]
        titles = "／".join(dict.fromkeys(s.get("title") or "(无标题)" for s in day))
        moves: list[str] = []
        for s in day:
            for m in s.get("movements") or []:
                if m.get("name") and m["name"] not in moves:
                    moves.append(m["name"])
        summary = "、".join(moves[:4]) + ("…" if len(moves) > 4 else "")
        wd = _WEEKDAY[dt.date.fromisoformat(date).weekday()]
        rows.append([date, wd, titles, summary or "—", cur])
    return rows


def to_tsv(rows: list[list[str]]) -> str:
    body = "\n".join("\t".join(c.replace("\t", " ") for c in r) for r in rows)
    return "\t".join(COLUMNS) + "\n" + body + ("\n" if body else "")


class ImportError_(ValueError):
    """导入失败。带行号，让用户知道去改哪一行。"""


def _rows_of(text: str):
    """按行切成单元格。制表符、逗号、多空格三种都认。

    导出的是 TSV，但人拿 Numbers / Excel 打开再存，出来的是 **CSV** ——
    这是实际发生过的事，不是假想。逗号分隔必须走 `csv` 模块而不是 split：
    动作摘要里的顿号不是逗号，但标题里可能有，裸 split 会把一行切错位。
    """
    if "\t" in text:
        for lineno, line in enumerate(text.splitlines(), 1):
            yield lineno, [c.strip() for c in re.split(r"\t|\s{2,}", line.rstrip())]
        return
    for lineno, cells in enumerate(csv.reader(io.StringIO(text)), 1):
        yield lineno, [c.strip() for c in cells]


def parse_table(text: str) -> list[tuple[str, str, str]]:
    """解析回 [(date, gym, note)]。

    宽容的地方：表头可选、列可以多可以少、制表符/逗号/多空格都认、空行跳过。
    **不宽容的地方**：日期解析不出来就报错并给行号 —— 静默跳过几行会让他
    以为一整年都录进去了，而实际上缺了一块。
    """
    out, bad = [], []
    for lineno, cells in _rows_of(text):
        if not cells or not any(cells):
            continue
        if cells[0].lower() in ("date", "日期"):     # 表头
            continue
        date = cells[0]
        if not _valid_date(date):
            bad.append(f"  第 {lineno} 行：{'|'.join(cells)[:60]}")
            continue
        gym = cells[-1] if len(cells) >= 2 else ""
        # 只有日期一列（或最后一列还是日期本身）= 这天没填，跳过而不是清空
        if gym == date:
            continue
        out.append((date, gym, ""))
    if bad:
        raise ImportError_(
            "有几行的第一列不是 YYYY-MM-DD 日期，没有导入任何东西：\n"
            + "\n".join(bad[:10])
            + (f"\n  …另有 {len(bad) - 10} 行" if len(bad) > 10 else ""))
    return out
