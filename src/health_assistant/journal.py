"""教练工作日志。

## 这是什么

一个真实的教练会随手记东西：学员今天说腿酸、我猜是后链空窗、他好像想改目标了。
这些笔记**天然是不可靠的** —— 上周的判断下周可能被推翻，随口一句可能根本不算数。

所以这个模块的设计目标不是「记准」，而是：

1. **零摩擦地记**（不需要触发词，聊到就写）
2. **诚实地标注可靠性**（观察 / 判断 / 待确认，三档分明）
3. **绝不假装自己是事实**（权威性排在优先级链最底下）
4. **永不删除**（append-only，推翻靠追加而不是修改）

事实在 `data/`、`knowledge/`、`profile/` 的其余文件里。日志只提供线索。

## 存储

`profile/coach-journal/YYYY-MM.jsonl`，一行一条记录，只追加。两种记录：

- `entry` —— 一条笔记。写下就不再改。
- `status` —— 对某条 entry 的状态变更（确认/否决）。也是追加。

状态不是存出来的，是**回放出来的**：读的时候把 status 记录叠到 entry 上。
这样「用户确认了目标转向」这件事本身也留下了时间戳和痕迹，
半年后回看能看到「他当时怎么想的、什么时候改的、为什么改」。

## 为什么状态是回放而不是原地改

原地改会丢历史，而这份日志的全部价值就在历史。
另外回放让读操作是纯的 —— `hc journal` 永远不写盘，
所以它可以安心地放进权限白名单，每次对话开头跑都没有副作用。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .config import JOURNAL_DIR

# ── 词表 ────────────────────────────────────────────────────────────────
#
# 三档可靠性。刻意只有三档 —— 再多分类我自己都会用错。

KINDS = ("观察", "判断", "待确认")

KIND_HELP = {
    "观察": "用户说的话、我看到的事实。可以引用，但要说明是「当时的说法」。",
    "判断": "我的推断。只当线索，下次必须重新验算，不能当结论用。",
    "待确认": "我认为重要、需要用户拍板的。闭合前每次对话都会浮出来。",
}

TOPICS = ("训练", "饮食", "体重", "身体状态", "目标", "医疗", "其他")

# 状态是派生的，不直接写在 entry 上
STATUS_OPEN = "open"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_SUPERSEDED = "superseded"
STATUS_STALE = "stale"

# ── 三个拍板过的常数 ────────────────────────────────────────────────────

WINDOW_DAYS = 14
"""默认读取窗口。

14 天 ≈ 2 个完整 PPL 轮次 ≈ 6 次训练，够覆盖「上次我们说了什么」。
再往前的纵向对比该由 `hc compare` / `hc report` 出，不该靠日志记忆。
"""

STALE_DAYS = 60
"""未闭合的待确认挂多久算过期。

过期后仍然显示（不删），但降级到底部并标注挂了多久 ——
提醒该问一句「这条还跟吗」，而不是让它无声无息地攒成一屏待办。
"""


def _today() -> dt.date:
    return dt.date.today()


# ── 读写原语 ────────────────────────────────────────────────────────────
#
# 有意不复用 store.py：那边的两条铁律（原始响应永不丢弃、字节稳定序列化）
# 是为同步来的机器数据服务的，日志是纯追加的人类笔记，约束不一样。


def _month_path(day: dt.date, root: Path | None = None) -> Path:
    return (root or JOURNAL_DIR) / f"{day:%Y-%m}.jsonl"


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _append_line(path: Path, obj: Any) -> None:
    """追加一行。先确保文件以换行结尾，避免上一次写到一半把两条粘一起。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(_dump(obj) + "\n")


def _read_records(root: Path | None = None) -> list[dict]:
    """读出全部月份的全部记录，按文件名（即时间）顺序。

    坏行直接跳过：一条写坏了不能让整个日志读不出来。
    """
    base = root or JOURNAL_DIR
    if not base.exists():
        return []
    out: list[dict] = []
    for path in sorted(base.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("rec") in ("entry", "status"):
                out.append(rec)
    return out


# ── 写入 ────────────────────────────────────────────────────────────────


def _next_id(day: dt.date, existing: Iterable[dict]) -> str:
    """当天序号。`20260807-03` —— 可读、可排序、不需要 uuid。"""
    prefix = f"{day:%Y%m%d}"
    used = 0
    for rec in existing:
        rid = rec.get("id") or ""
        if rec.get("rec") == "entry" and rid.startswith(prefix + "-"):
            try:
                used = max(used, int(rid.split("-")[1]))
            except (IndexError, ValueError):
                continue
    return f"{prefix}-{used + 1:02d}"


def add(
    kind: str,
    topic: str,
    text: str,
    *,
    evidence: list[str] | None = None,
    supersedes: str | None = None,
    today: dt.date | None = None,
    root: Path | None = None,
) -> str:
    """追加一条笔记，返回它的 id。"""
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {'/'.join(KINDS)} 之一，收到 {kind!r}")
    text = (text or "").strip()
    if not text:
        raise ValueError("text 不能为空")
    day = today or _today()
    records = _read_records(root)
    if supersedes and not any(
        r.get("rec") == "entry" and r.get("id") == supersedes for r in records
    ):
        raise ValueError(f"要推翻的条目 {supersedes} 不存在")

    entry = {
        "rec": "entry",
        "id": _next_id(day, records),
        "date": day.isoformat(),
        "kind": kind,
        "topic": topic if topic in TOPICS else "其他",
        "text": text,
        "evidence": list(evidence or []),
        "supersedes": supersedes,
    }
    _append_line(_month_path(day, root), entry)
    return entry["id"]


def set_status(
    entry_id: str,
    status: str,
    *,
    landed: str | None = None,
    why: str | None = None,
    today: dt.date | None = None,
    root: Path | None = None,
) -> None:
    """给某条笔记记一次状态变更。**不修改原记录**，追加一条 status。"""
    if status not in (STATUS_CONFIRMED, STATUS_REJECTED):
        raise ValueError("只能手工标 confirmed / rejected；其余状态是算出来的")
    records = _read_records(root)
    target = next(
        (r for r in records if r.get("rec") == "entry" and r.get("id") == entry_id),
        None,
    )
    if target is None:
        raise ValueError(f"没有这条笔记：{entry_id}")
    if status == STATUS_CONFIRMED and not landed:
        raise ValueError(
            "确认必须给 --landed，写清楚落到 profile 的哪个文件哪一节。"
            "落不了盘的东西就不该标确认。"
        )
    day = today or _today()
    _append_line(
        _month_path(day, root),
        {
            "rec": "status",
            "target": entry_id,
            "date": day.isoformat(),
            "status": status,
            "landed": landed,
            "why": why,
        },
    )


# ── 回放 ────────────────────────────────────────────────────────────────


def entries(
    *, today: dt.date | None = None, root: Path | None = None
) -> list[dict]:
    """把 entry 和 status 折叠成带状态的视图，按日期倒序（新的在前）。"""
    day = today or _today()
    records = _read_records(root)

    items: dict[str, dict] = {}
    for rec in records:
        if rec.get("rec") != "entry":
            continue
        rid = rec.get("id")
        if not rid or rid in items:
            continue  # 同 id 只认第一条，防止手工编辑写重
        item = dict(rec)
        item["status"] = STATUS_OPEN
        item["landed"] = None
        item["why"] = None
        item["superseded_by"] = None
        item["status_date"] = None
        items[rid] = item

    # 状态变更
    for rec in records:
        if rec.get("rec") != "status":
            continue
        item = items.get(rec.get("target") or "")
        if item is None:
            continue
        item["status"] = rec.get("status") or STATUS_OPEN
        item["landed"] = rec.get("landed")
        item["why"] = rec.get("why")
        item["status_date"] = rec.get("date")

    # 被推翻的：由后来者指认，而不是自己改自己
    for item in items.values():
        old_id = item.get("supersedes")
        if not old_id:
            continue
        old = items.get(old_id)
        if old is None:
            continue
        old["superseded_by"] = item["id"]
        if old["status"] == STATUS_OPEN:
            old["status"] = STATUS_SUPERSEDED

    # 挂太久的待确认降级。算出来的，不写盘 —— 读操作保持无副作用。
    for item in items.values():
        if item["kind"] == "待确认" and item["status"] == STATUS_OPEN:
            if _age_days(item["date"], day) > STALE_DAYS:
                item["status"] = STATUS_STALE

    out = list(items.values())
    out.sort(key=lambda r: (r.get("date") or "", r.get("id") or ""), reverse=True)
    return out


def _age_days(iso: str, day: dt.date) -> int:
    try:
        return (day - dt.date.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return 0


# ── 选择 ────────────────────────────────────────────────────────────────


def select(
    *,
    window_days: int = WINDOW_DAYS,
    today: dt.date | None = None,
    root: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """默认的引用策略：最近 N 天 + 全部未闭合的待确认（不受窗口限制）。

    待确认不受窗口限制是这套东西最值钱的部分 ——
    用户今天没回答的问题，不该因为过了两周就消失。
    教练手上挂着的 open loop 就该一直挂着。

    返回 (待确认, 窗口内的其余条目)。待确认不会在第二个列表里重复出现。
    """
    day = today or _today()
    all_items = entries(today=day, root=root)

    pending = [
        it
        for it in all_items
        if it["kind"] == "待确认" and it["status"] in (STATUS_OPEN, STATUS_STALE)
    ]
    # 挂久的沉到底部
    pending.sort(key=lambda it: (it["status"] == STATUS_STALE, -_age_days(it["date"], day)))

    pending_ids = {it["id"] for it in pending}
    recent = [
        it
        for it in all_items
        if it["id"] not in pending_ids and _age_days(it["date"], day) <= window_days
    ]
    return pending, recent


def search(
    pattern: str, *, today: dt.date | None = None, root: Path | None = None
) -> list[dict]:
    """按关键词检索全部历史。聊到某个旧话题时才用，不通读。"""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)
    out = []
    for it in entries(today=today, root=root):
        haystack = " ".join(
            [it.get("text") or "", it.get("topic") or "", *(it.get("evidence") or [])]
        )
        if rx.search(haystack):
            out.append(it)
    return out


def since(
    start: dt.date, *, today: dt.date | None = None, root: Path | None = None
) -> list[dict]:
    """某个日期之后的全部条目。出周报/月报时用。"""
    iso = start.isoformat()
    return [it for it in entries(today=today, root=root) if (it.get("date") or "") >= iso]


# ── 渲染 ────────────────────────────────────────────────────────────────

_KIND_MARK = {"观察": "·", "判断": "~", "待确认": "?"}

_STATUS_LABEL = {
    STATUS_CONFIRMED: "已确认",
    STATUS_REJECTED: "已否决",
    STATUS_SUPERSEDED: "已被推翻",
    STATUS_STALE: "挂太久",
}

DISCLAIMER = "这里是线索，不是结论。任何要用于建议的数字必须重跑 hc 命令，不要从这里抄。"


def fmt_line(it: dict, day: dt.date, *, indent: str = "     ") -> str:
    mark = _KIND_MARK.get(it["kind"], "·")
    line = f"{indent}{mark} [{it['id']}] {it['date']} {it['kind']}·{it['topic']}  {it['text']}"
    tail = []
    label = _STATUS_LABEL.get(it["status"])
    if label:
        age = _age_days(it["date"], day)
        if it["status"] == STATUS_STALE:
            tail.append(f"{label} {age} 天")
        elif it["status"] == STATUS_SUPERSEDED:
            tail.append(f"{label} → {it['superseded_by']}")
        else:
            tail.append(label)
    if it.get("landed"):
        tail.append(f"落盘 {it['landed']}")
    if it.get("evidence"):
        tail.append("依据 " + " / ".join(it["evidence"]))
    if it.get("supersedes"):
        tail.append(f"推翻 {it['supersedes']}")
    if tail:
        line += "\n" + indent + "    （" + "；".join(tail) + "）"
    return line


def render(
    *,
    window_days: int = WINDOW_DAYS,
    today: dt.date | None = None,
    root: Path | None = None,
) -> str:
    """人读的默认视图。"""
    day = today or _today()
    pending, recent = select(window_days=window_days, today=day, root=root)

    lines = [
        "教练工作日志（profile/coach-journal/）",
        "=" * 46,
        f"  ⚠️  {DISCLAIMER}",
        "",
    ]

    if pending:
        lines.append(f"  ❓ 未闭合的待确认（{len(pending)} 条，不受 {window_days} 天窗口限制）")
        for it in pending:
            lines.append(fmt_line(it, day))
        lines.append("")

    if recent:
        lines.append(f"  最近 {window_days} 天（{len(recent)} 条）")
        for it in recent:
            lines.append(fmt_line(it, day))
    else:
        lines.append(f"  最近 {window_days} 天没有新记录。")

    if not pending and not recent:
        lines.append("")
        lines.append("  日志还是空的。聊起来就会有了 —— 不需要你做任何事。")

    return "\n".join(lines)


BRIEF_OPEN = "<coach-journal"
BRIEF_CLOSE = "</coach-journal>"


def render_brief(
    *,
    window_days: int = WINDOW_DAYS,
    today: dt.date | None = None,
    root: Path | None = None,
) -> str:
    """给 `--append-system-prompt` 用的紧凑版。

    带 `<coach-journal>` 哨兵，这样助手能一眼看出日志已经在上下文里了，
    不必再花一次工具调用去读 —— 那正是「每次对话开头要 accept 好几次」的来源。
    """
    day = today or _today()
    pending, recent = select(window_days=window_days, today=day, root=root)
    if not pending and not recent:
        return ""

    out = [f'{BRIEF_OPEN} generated="{day.isoformat()}" window="{window_days}d">',
           DISCLAIMER]
    if pending:
        out.append(f"未闭合的待确认（{len(pending)}，需要在对话中找机会闭合）：")
        for it in pending:
            age = _age_days(it["date"], day)
            flag = "  ⏳挂了%d天" % age if it["status"] == STATUS_STALE else ""
            out.append(f"  [{it['id']}] {it['topic']} {it['text']}{flag}")
    if recent:
        out.append(f"最近 {window_days} 天：")
        for it in recent:
            out.append(f"  [{it['id']}] {it['date']} {it['kind']}·{it['topic']} {it['text']}")
    out.append(BRIEF_CLOSE)
    return "\n".join(out)
