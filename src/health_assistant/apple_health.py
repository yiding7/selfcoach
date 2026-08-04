"""苹果健康导出文件的导入器。

**为什么需要**：训记只同步了一部分数据。实测里体重停在 2026-07-19，
而用户在苹果健康里还有更新的记录；饮酒、睡眠、静息心率、步数训记完全没有。
这些对减脂期的判断很有价值 —— 尤其静息心率和睡眠，是恢复状态最直接的指标。

**为什么不能直接读**：苹果健康没有对外的读取接口，数据在 iOS 的加密容器里。
唯一的官方途径是用户手动导出：

    iPhone 健康 App → 右上角头像 → 导出所有健康数据 → 得到「导出.zip」

然后把 zip（或解压出的 xml）交给这个导入器。**不需要任何授权配置**，
因为文件已经在你手里了。

**内存**：导出文件常常几百 MB，几百万条记录。所以用 iterparse 流式解析，
处理完一个元素就 clear 掉，内存占用是常数级。
"""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

from . import store
from .config import DATA_DIR

HEALTH_DIR = DATA_DIR / "apple-health"

# 关心的记录类型 → (内部名, 聚合方式)
#   first 当天取**最早**一条 —— 体重要用晨起空腹值，晚上称会高 1kg 以上
#   last  当天取最后一条
#   sum   当天求和（步数、饮酒、饮水）
#   mean  当天求平均（静息心率）
WANTED = {
    "HKQuantityTypeIdentifierBodyMass": ("weight", "first", "kg"),
    "HKQuantityTypeIdentifierBodyFatPercentage": ("bodyfat", "first", "%"),
    "HKQuantityTypeIdentifierWaistCircumference": ("weist", "last", "cm"),
    "HKQuantityTypeIdentifierLeanBodyMass": ("lean_mass", "last", "kg"),
    "HKQuantityTypeIdentifierRestingHeartRate": ("resting_hr", "mean", "bpm"),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("hrv", "mean", "ms"),
    "HKQuantityTypeIdentifierStepCount": ("steps", "sum", "步"),
    "HKQuantityTypeIdentifierNumberOfAlcoholicBeverages": ("alcohol", "sum", "标准杯"),
    "HKQuantityTypeIdentifierDietaryWater": ("water", "sum", "ml"),
    "HKQuantityTypeIdentifierAppleExerciseTime": ("exercise_min", "sum", "分钟"),
    "HKQuantityTypeIdentifierBodyMassIndex": ("bmi", "last", ""),
}

# 睡眠是分类型记录，单独处理：把「实际睡着」的区间时长按天累加
SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleep",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
}

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _date_of(value: str | None) -> str | None:
    if not value:
        return None
    m = _DATE_RE.match(value)
    return m.group(1) if m else None


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    # 形如 "2026-08-02 08:13:22 +0800"
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def _open_xml(path: Path):
    """支持直接给 zip，也支持给解压出来的 xml。"""
    if path.is_dir():
        for name in ("导出.xml", "export.xml"):
            for cand in (path / name, path / "apple_health_export" / name):
                if cand.exists():
                    return cand.open("rb"), cand.name
        raise FileNotFoundError(f"{path} 里没找到 导出.xml / export.xml")

    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        for member in zf.namelist():
            base = member.rsplit("/", 1)[-1]
            if base in ("导出.xml", "export.xml"):
                return zf.open(member), member
        raise FileNotFoundError(f"{path} 里没找到 导出.xml / export.xml")

    return path.open("rb"), path.name


def parse(path: Path, *, since: str | None = None, log=print) -> dict[str, dict[str, float]]:
    """流式解析。返回 {指标: {日期: 数值}}。"""
    fh, name = _open_xml(path)
    log(f"  解析 {name} …")

    buckets: dict[str, dict[str, list[tuple[dt.datetime | None, float]]]] = defaultdict(
        lambda: defaultdict(list))
    sleep: dict[str, float] = defaultdict(float)
    seen = 0

    try:
        for event, elem in ET.iterparse(fh, events=("end",)):
            if elem.tag != "Record":
                continue
            seen += 1
            rtype = elem.get("type")

            if rtype == SLEEP_TYPE:
                if elem.get("value") in ASLEEP_VALUES:
                    a, b = _parse_ts(elem.get("startDate")), _parse_ts(elem.get("endDate"))
                    if a and b and b > a:
                        # 归到「起床那天」：跨夜的睡眠算到 endDate 那天更符合直觉
                        day = b.date().isoformat()
                        if not since or day >= since:
                            sleep[day] += (b - a).total_seconds() / 3600.0
            elif rtype in WANTED:
                key, _, _ = WANTED[rtype]
                day = _date_of(elem.get("startDate"))
                if day and (not since or day >= since):
                    try:
                        val = float(elem.get("value"))
                    except (TypeError, ValueError):
                        val = None
                    if val is not None:
                        buckets[key][day].append((_parse_ts(elem.get("startDate")), val))

            elem.clear()
    finally:
        fh.close()

    log(f"  扫描了 {seen:,} 条记录")

    out: dict[str, dict[str, float]] = {}
    for rtype, (key, how, _unit) in WANTED.items():
        if key not in buckets:
            continue
        daily: dict[str, float] = {}
        for day, items in buckets[key].items():
            vals = [v for _, v in items]
            if how == "sum":
                daily[day] = sum(vals)
            elif how == "mean":
                daily[day] = sum(vals) / len(vals)
            else:  # first / last —— 按时间排序后取一端
                items.sort(key=lambda t: (t[0] is None, t[0]))
                daily[day] = items[0][1] if how == "first" else items[-1][1]
        if daily:
            out[key] = daily
    if sleep:
        out["sleep_h"] = dict(sleep)
    return out


def persist(data: dict[str, dict[str, float]], *, log=print) -> dict[str, int]:
    """写进本地存储。

    体重/体脂/围度合并进身体数据主存储（和训记来的数据同源使用），
    其余指标单独存 data/apple-health/metrics.jsonl。
    """
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    # 体重、体脂、围度 → 身体数据主存储
    body_records = []
    for key, unit in (("weight", "kg"), ("bodyfat", "%"), ("weist", "cm")):
        for day, val in (data.get(key) or {}).items():
            body_records.append({
                "schema": "ha.body/1",
                "id": f"apple:{key}:{day}",
                "source": "apple_health",
                "date": day, "type": key,
                "value": round(val, 2), "unit": unit,
                "label": {"weight": "体重", "bodyfat": "体脂率", "weist": "腰围"}[key],
            })
    if body_records:
        counts["body"] = store.upsert_body(body_records)

    # 其余指标 → 单独文件
    rows = []
    for key, daily in data.items():
        if key in ("weight", "bodyfat", "weist"):
            continue
        for day, val in daily.items():
            rows.append({"date": day, "metric": key, "value": round(val, 3)})
    if rows:
        path = HEALTH_DIR / "metrics.jsonl"
        existing = {(r["date"], r["metric"]): r for r in store.read_jsonl(path)}
        for r in rows:
            existing[(r["date"], r["metric"])] = r
        store.write_jsonl(path, list(existing.values()),
                          sort_key=lambda r: (r["date"], r["metric"]))
        counts["metrics"] = len(rows)

    return counts


def load_metrics(metric: str | None = None, start: str | None = None,
                 end: str | None = None) -> list[dict]:
    rows = store.read_jsonl(HEALTH_DIR / "metrics.jsonl")
    if metric:
        rows = [r for r in rows if r["metric"] == metric]
    if start:
        rows = [r for r in rows if r["date"] >= start]
    if end:
        rows = [r for r in rows if r["date"] <= end]
    rows.sort(key=lambda r: r["date"])
    return rows


def available_metrics() -> list[str]:
    return sorted({r["metric"] for r in store.read_jsonl(HEALTH_DIR / "metrics.jsonl")})
