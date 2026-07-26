"""训记原始响应 → 规范化会话。纯函数，不碰网络、不碰磁盘。

这个模块承载了所有"脏数据"知识。下游的分析引擎只看规范化后的结构，
不需要知道训记把数字存成字符串、RPE 空字符串代表没记、note 里藏着热量。

刻意**不**在这里做肌群分类。分类放在 analytics 层按需计算，
这样以后改进分类规则立刻对全部历史生效，不用重新 rebuild。
"""

from __future__ import annotations

import re
from typing import Any

LB_TO_KG = 0.45359237

# note 里的哨兵值。实测 note 常常是 "calorie:228"，也见过空的 "calorie:"。
_CALORIE_RE = re.compile(r"calorie\s*:\s*(\d+(?:\.\d+)?)?", re.I)


def num(value: Any) -> float | None:
    """字符串数值 → float。空串/None/垃圾 → None。

    绝不返回 0 兜底 —— "没记录"和"记录为 0"是两回事，
    混淆这两者会让容量统计和 RPE 分析全部失真。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def to_kg(weight: float | None, unit: str | None) -> float | None:
    if weight is None:
        return None
    if (unit or "").strip().lower() in ("lb", "lbs", "磅"):
        return round(weight * LB_TO_KG, 4)
    return weight


def parse_note(note: Any) -> tuple[str, float | None, dict]:
    """note 可能是字符串，也可能是对象。返回 (正文, 热量kcal, 其余元数据)。

    实测形态是 `"calorie:228"` 这种字符串 —— 它不是用户写的备注，
    是训记塞进去的元数据。直接当备注展示会让报告里出现莫名其妙的 "calorie:"。
    """
    meta: dict = {}
    if note is None:
        return "", None, meta

    if isinstance(note, str):
        s = note.strip()
        if not s:
            return "", None, meta
        # 可能是 JSON 字符串
        if s.startswith("{"):
            try:
                import json
                return parse_note(json.loads(s))
            except Exception:
                pass
        kcal = None
        m = _CALORIE_RE.search(s)
        if m:
            kcal = num(m.group(1))
            s = _CALORIE_RE.sub("", s).strip()
        return s, kcal, meta

    if isinstance(note, dict):
        meta = {k: v for k, v in note.items()
                if k not in ("text", "calorie", "heartRate", "trainColor")}
        text = str(note.get("text") or "").strip()
        kcal = num(note.get("calorie"))
        if not kcal and text:
            m = _CALORIE_RE.search(text)
            if m:
                kcal = num(m.group(1))
                text = _CALORIE_RE.sub("", text).strip()
        if note.get("trainColor"):
            meta["trainColor"] = note["trainColor"]
        if note.get("heartRate") is not None:
            meta["heartRate"] = note["heartRate"]
        return text, kcal, meta

    return "", None, meta


def _metrics(raw_set: dict) -> dict:
    """有氧 / Tabata / 苹果健康动作的摘要指标。"""
    m = raw_set.get("metrics")
    if not isinstance(m, dict):
        return {}
    out = {}
    mapping = {
        "distance": "distance_m", "kcal": "kcal", "calories": "kcal",
        "workoutTime": "workout_s", "avgHeartRate": "avg_hr",
        "maxHeartRate": "max_hr", "minHeartRate": "min_hr",
    }
    for src, dst in mapping.items():
        v = num(m.get(src))
        if v is not None:
            out.setdefault(dst, v)
    return out


def _hr_trend(raw_set: dict) -> dict | None:
    hr = raw_set.get("heartRate")
    if not isinstance(hr, dict):
        return None
    values = hr.get("values")
    if not isinstance(values, list):
        values = None
    return {
        "avg": num(hr.get("avg")), "max": num(hr.get("max")), "min": num(hr.get("min")),
        "step_s": num(hr.get("step")), "peak": num(hr.get("peak")),
        "values": values,
    }


def normalize_set(raw: dict, *, unit_hint: str | None = None, kind: str = "work") -> dict:
    unit = raw.get("unit") or unit_hint
    weight = to_kg(num(raw.get("weight")), unit)
    left = to_kg(num(raw.get("leftWeight")), unit)

    out = {
        "index": raw.get("index"),
        "done": bool(raw.get("done")),
        "kind": kind,
        "weight_kg": weight,
        "left_weight_kg": left,
        "reps": num(raw.get("reps")),
        "time_s": num(raw.get("time")) or None,
        "self_weight": bool(raw.get("selfWeight")),
        "rpe": num(raw.get("rpe")),          # "" → None，绝不当 0
        "set_type": (raw.get("setType") or "") or None,
        "note": (raw.get("note") or raw.get("comment") or "") or None,
    }
    metrics = _metrics(raw)
    if metrics:
        out["metrics"] = metrics
    hr = _hr_trend(raw)
    if hr:
        out["hr"] = hr

    # 超级组 / 递减组：子动作在 items[] 里
    items = raw.get("items")
    if isinstance(items, list) and items:
        children = []
        for it in items:
            child_set = it.get("set") if isinstance(it, dict) else None
            if isinstance(child_set, dict):
                child = normalize_set(child_set, unit_hint=unit, kind="superset_child")
                child["name"] = (it.get("name") or "").strip() or None
                children.append(child)
        if children:
            out["items"] = children
    return {k: v for k, v in out.items() if v is not None or k in ("weight_kg", "reps", "rpe")}


def normalize_movement(raw: dict) -> dict:
    exetype = (raw.get("exetype") or "").strip()
    return {
        "index": raw.get("index"),
        "name": (raw.get("name") or "").strip(),
        # 训记返回的肌群中文名。厂商文档没写这个字段，但真实响应里有。
        # 经常为空 —— 所以 analytics 层必须有关键词兜底分类器。
        "raw_type": (raw.get("type") or "").strip() or None,
        # "" = 常规负重；"times" = 纯次数自重；"plus_weight" = 自重加负重
        "exetype": exetype or None,
        "unilateral": bool(raw.get("singleSide")),
        "difficulty": (raw.get("difficulty") or "").strip() or None,
        "note": (raw.get("note") or "").strip() or None,
        "truncated": bool(raw.get("truncated")),
        "sets": [normalize_set(s) for s in (raw.get("sets") or []) if isinstance(s, dict)],
    }


def normalize_train(raw: dict, *, datestr: str) -> dict:
    localid = raw.get("localid")
    start = raw.get("start") or raw.get("started_at")
    end = raw.get("end") or raw.get("ended_at")
    start_ms = int(start) if isinstance(start, (int, float)) else None
    end_ms = int(end) if isinstance(end, (int, float)) else None

    duration_s = None
    if start_ms and end_ms and end_ms > start_ms:
        duration_s = (end_ms - start_ms) // 1000
        # 忘记停计时器是真实存在的：超过 6 小时的一律不采信
        if duration_s > 6 * 3600:
            duration_s = None

    note_text, kcal, note_meta = parse_note(raw.get("note"))

    session = {
        "schema": "ha.session/1",
        "id": f"xunji:{datestr}:{localid}",
        "source": "xunji",
        "date": datestr,
        "title": (raw.get("title") or "").strip(),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_s": duration_s,
        "note": note_text,
        "kcal": kcal,
        "truncated": bool(raw.get("truncated")),
        "movements": [normalize_movement(m) for m in (raw.get("movements") or [])
                      if isinstance(m, dict)],
    }
    hr = raw.get("heartRate")
    if hr is not None:
        session["heart_rate"] = hr
    elif note_meta.get("heartRate") is not None:
        session["heart_rate"] = note_meta["heartRate"]
    if note_meta.get("trainColor"):
        session["color"] = note_meta["trainColor"]
    return session


def normalize_train_day(res: dict, *, datestr: str) -> list[dict]:
    """`res` 是训练接口 unwrap 之后的对象。"""
    trains = res.get("trains")
    if not isinstance(trains, list):
        return []
    return [normalize_train(t, datestr=res.get("datestr") or datestr)
            for t in trains if isinstance(t, dict)]


# ── 身体数据 ────────────────────────────────────────────────────────────

def normalize_body(res: dict) -> list[dict]:
    records = res.get("records")
    if not isinstance(records, list):
        return []
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        datestr = r.get("datestr")
        rtype = r.get("type")
        if not datestr or not rtype:
            continue
        value = num(r.get("value"))
        if value is None:
            value = num(r.get(rtype))
        if value is None:
            continue
        rid = r.get("id")
        out.append({
            "schema": "ha.body/1",
            # 没有 id 时用 date+type 兜底，保证 upsert 幂等
            "id": f"xunji:body:{rid}" if rid else f"xunji:body:{datestr}:{rtype}",
            "source": "xunji",
            "date": datestr,
            # 保留训记的原始类型名，包括历史拼写 weist（腰围）。
            # 改成 waist 会导致写回时对不上字段。
            "type": rtype,
            "value": value,
            "unit": r.get("unit") or "",
            "label": r.get("label") or "",
        })
    return out


# ── 饮食 ────────────────────────────────────────────────────────────────

def normalize_food(res: dict) -> list[dict]:
    days = res.get("days")
    if not isinstance(days, list):
        return []
    out = []
    for day in days:
        if not isinstance(day, dict):
            continue
        date = day.get("date") or day.get("datestr")
        if not date:
            continue
        for meal_key in ("breakfast", "lunch", "dinner", "snack", "meals", "items"):
            entries = day.get(meal_key)
            if not isinstance(entries, list):
                continue
            for i, e in enumerate(entries):
                if not isinstance(e, dict):
                    continue
                ntr = e.get("ntr") if isinstance(e.get("ntr"), dict) else {}
                meal = e.get("meal_type") or e.get("meal") or (
                    meal_key if meal_key in ("breakfast", "lunch", "dinner", "snack") else "other")
                eid = e.get("id") or f"{date}:{meal}:{i}"
                out.append({
                    "schema": "ha.meal/1",
                    "id": f"xunji:food:{eid}",
                    "source": "xunji",
                    "date": date,
                    "meal": meal,
                    "name": (e.get("name") or "").strip(),
                    "amount": num(e.get("amount")),
                    "unit": e.get("unit") or "g",
                    "kcal": num(e.get("cal") or ntr.get("cal")),
                    "protein_g": num(e.get("protein") or ntr.get("protein")),
                    "fat_g": num(e.get("fat") or ntr.get("fat")),
                    "carb_g": num(e.get("carb") or ntr.get("carb")),
                    "uniquekey": e.get("uniquekey"),
                    "confidence": "measured",
                })
    return out
