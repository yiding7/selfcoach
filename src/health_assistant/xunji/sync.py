"""训记增量同步。

训练接口一天一查、限频 30 秒。这不是可以优化掉的常数，它决定了整个同步层的形状：

  - 抓过的日期**永不重抓**（包括空日期 —— 不记录空日期的话，每次同步都要
    再花 30 秒去确认某天确实没练）
  - 每抓完一天立刻写水位 → Ctrl-C 安全、可续传
  - 原始响应原样落盘 → 以后改解析逻辑用 `hc rebuild` 离线重算，一次网络都不用
  - 支持时间预算 → 适合放进 cron 每小时跑 20 分钟，慢慢把历史补完

身体和饮食接口支持日期范围，一次调用搞定，不受这些约束。
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

from .. import store
from ..config import ensure_dirs
from . import normalize
from .client import (BODY_QUERY, FOOD_QUERY, PLAN_QUERY, TRAIN_READ,
                     TRAIN_READ_LIGHT, XunjiClient, XunjiError)

# 最近这些天的记录还可能被用户改动，每次同步都重抓
EDIT_HORIZON_DAYS = 3


def daterange(start: dt.date, end: dt.date):
    """从 end 往 start 倒着走 —— 最近的数据对教练建议最有价值。"""
    d = end
    while d >= start:
        yield d
        d -= dt.timedelta(days=1)


@dataclass
class SyncResult:
    fetched: int = 0
    skipped: int = 0
    sessions: int = 0
    errors: list[str] = field(default_factory=list)
    stopped_reason: str | None = None
    dates_with_data: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"新抓取 {self.fetched} 天", f"跳过（已缓存）{self.skipped} 天",
                f"落库 {self.sessions} 次训练"]
        if self.errors:
            bits.append(f"{len(self.errors)} 个错误")
        if self.stopped_reason:
            bits.append(f"提前停止：{self.stopped_reason}")
        return "，".join(bits)


def _needs_fetch(datestr: str, today: dt.date, *, force: bool) -> bool:
    if force:
        return True
    entry = store.index_entry(datestr)
    if entry is None:
        return True
    if entry.get("status") == "error":
        return True
    # 热窗口：最近几天可能还在改，重抓
    d = dt.date.fromisoformat(datestr)
    if (today - d).days <= EDIT_HORIZON_DAYS:
        return True
    # light 模式抓过的，遇到有数据的日子要用 full 补一次
    if entry.get("mode") == "light" and entry.get("trains", 0) > 0:
        return True
    return False


def estimate_minutes(n_days: int, *, full: bool = True) -> float:
    interval = TRAIN_READ.min_interval_s if full else TRAIN_READ_LIGHT.min_interval_s
    return n_days * interval / 60.0


def sync_training(client: XunjiClient, start: dt.date, end: dt.date, *,
                  full: bool = True, force: bool = False,
                  budget_s: float | None = None,
                  max_requests: int | None = None,
                  stop_after_empty_streak: int | None = None,
                  log=print) -> SyncResult:
    ensure_dirs()
    store.init()
    endpoint = TRAIN_READ if full else TRAIN_READ_LIGHT
    today = dt.date.today()
    result = SyncResult()
    started = time.monotonic()
    empty_streak = 0

    pending = [d for d in daterange(start, end)
               if _needs_fetch(d.isoformat(), today, force=force)]
    result.skipped = (end - start).days + 1 - len(pending)

    if pending:
        mins = estimate_minutes(len(pending), full=full)
        log(f"需要抓取 {len(pending)} 天（已缓存 {result.skipped} 天）。"
            f"按限频 {endpoint.min_interval_s:.0f} 秒/次，预计约 {mins:.0f} 分钟。")
        log("随时可以 Ctrl-C 中断，下次运行会从中断处继续。")
    else:
        log(f"全部 {result.skipped} 天都已在本地缓存，无需请求。")
        return result

    for d in pending:
        if budget_s is not None and time.monotonic() - started > budget_s:
            result.stopped_reason = "达到时间预算"
            break
        if max_requests is not None and result.fetched >= max_requests:
            result.stopped_reason = "达到请求数上限"
            break

        datestr = d.isoformat()
        try:
            data = client.post(endpoint, {
                "schema_version": "train_open_api_v2",
                "datestr": datestr,
                "include_full_data": bool(full),
            })
            res = client.unwrap(data, endpoint)
        except XunjiError as e:
            result.errors.append(f"{datestr}: {e}")
            store.mark_fetched(datestr, status="error", mode="full" if full else "light",
                               trains=0, fetched_at=_now())
            log(f"  {datestr}  ✗ {e}")
            if e.kind in ("auth", "vip", "network"):
                result.stopped_reason = str(e)
                break
            continue

        store.save_raw_training(datestr, res)
        sessions = normalize.normalize_train_day(res, datestr=datestr)
        n = len(sessions)
        result.fetched += 1

        if n:
            store.upsert_sessions(sessions)
            result.sessions += n
            result.dates_with_data.append(datestr)
            empty_streak = 0
            names = [m["name"] for s in sessions for m in s["movements"]]
            preview = "、".join(names[:4]) + ("…" if len(names) > 4 else "")
            log(f"  {datestr}  ✓ {n} 次训练：{preview}")
        else:
            empty_streak += 1
            log(f"  {datestr}  · 无训练")

        store.mark_fetched(datestr, status="ok" if n else "empty",
                           mode="full" if full else "light",
                           trains=n, fetched_at=_now())

        if stop_after_empty_streak and empty_streak >= stop_after_empty_streak:
            result.stopped_reason = f"连续 {empty_streak} 天无记录，已到历史起点"
            break

    return result


def sync_body(client: XunjiClient, start: dt.date, end: dt.date, *, log=print) -> int:
    """身体数据支持范围查询，一次调用拿完。"""
    ensure_dirs()
    data = client.post(BODY_QUERY, {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "include_latest": True,
        "include_records": True,
        "limit": 1000,
        "offset": 0,
    })
    res = client.unwrap(data, BODY_QUERY)
    records = normalize.normalize_body(res)
    n = store.upsert_body(records)
    if res.get("truncated"):
        log("  ⚠ 服务端提示结果被截断，可能需要分段查询更早的数据")
    log(f"  身体数据 {n} 条（{start} ~ {end}）")
    return n


def sync_food(client: XunjiClient, start: dt.date, end: dt.date, *, log=print) -> int:
    ensure_dirs()
    data = client.post(FOOD_QUERY, {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "include_detail": True,
    })
    res = client.unwrap(data, FOOD_QUERY)
    window = res.get("window") or {}
    records = normalize.normalize_food(res)
    n = store.upsert_meals(records)
    if n == 0:
        log(f"  饮食记录 0 条（{start} ~ {end}）"
            f"{'，服务端允许区间 ' + window.get('minDate', '?') + ' ~ ' + window.get('maxDate', '?') if window else ''}")
    else:
        log(f"  饮食记录 {n} 条（{start} ~ {end}）")
    return n


def fetch_plans(client: XunjiClient) -> list[dict]:
    data = client.post(PLAN_QUERY, {
        "schema_version": "plan_open_api_v1",
        "action": "list",
    })
    res = client.unwrap(data, PLAN_QUERY)
    plans = res.get("plans")
    return plans if isinstance(plans, list) else []


def rebuild(log=print) -> int:
    """用本地原始缓存离线重算全部会话。零网络请求。

    改了 normalize.py 之后跑这个，而不是重新同步 —— 重新同步一年要 3 小时。
    """
    store.init()
    dates = store.raw_training_dates()
    total = 0
    by_month: dict[str, list[dict]] = {}
    for datestr in dates:
        res = store.load_raw_training(datestr)
        if not isinstance(res, dict):
            continue
        for s in normalize.normalize_train_day(res, datestr=datestr):
            by_month.setdefault(s["date"][:7], []).append(s)
            total += 1
    for ym, sessions in by_month.items():
        existing = [r for r in store.load_sessions_month(ym) if r.get("source") != "xunji"]
        store.save_sessions_month(ym, existing + sessions)
    log(f"从 {len(dates)} 天原始缓存重算出 {total} 次训练，覆盖 {len(by_month)} 个月份。")
    return total


def _now() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
