"""可选的 LLM 适配器 —— 只为无人值守场景存在。

平时在 Claude Code / Codex / Cursor 里用时**不需要这个**：宿主自带模型，
而且可以追问，效果更好。这里是给 cron 定时出周报用的。

支持任何 OpenAI 兼容端点（DeepSeek / Kimi / 通义 / 智谱 / Ollama / vLLM …）
和 Anthropic 原生端点。纯标准库，没有 SDK 依赖。

两条铁律：

1. **只发聚合后的 findings，不发原始数据。** 餐食记录、用药信息、体检数值
   都不出本机。见 knowledge/safety-boundaries.md。

2. **数字幻觉拦截。** 模型返回的文本里出现的每个数字，都必须能在 facts 里找到。
   找不到就丢弃这段叙述，退回纯数据模式。报告宁可少一段话，
   也不能出现一个编造的体重或重量。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .config import KNOWLEDGE_DIR, load_env

PERSONA_PATH = KNOWLEDGE_DIR / "persona.md"  # 保留给外部引用；组装走 persona.load()

# 允许出现在叙述里、但不需要在 facts 里找到的数字：
# 年份、月份、周数、小数点后的位数、常见序数
_ALLOWED_BARE = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                 "12", "24", "30", "60", "100", "2025", "2026", "2027"}


class LLMNotConfigured(RuntimeError):
    pass


def config() -> dict:
    load_env()
    provider = (os.environ.get("HC_LLM_PROVIDER") or "").strip().lower()
    api_key = (os.environ.get("HC_LLM_API_KEY") or "").strip()
    if not provider or not api_key:
        raise LLMNotConfigured(
            "没有配置 LLM 适配器。这不是错误 —— 报告在纯数据模式下依然完整。\n"
            "  想让 cron 自动生成带教练叙述的周报，在 .env 里设置：\n"
            "    HC_LLM_PROVIDER=openai-compatible   # 或 anthropic\n"
            "    HC_LLM_BASE_URL=https://api.deepseek.com/v1\n"
            "    HC_LLM_MODEL=deepseek-chat\n"
            "    HC_LLM_API_KEY=...\n"
            "  在 Claude Code / Codex 里用的话不需要配这个，宿主自带模型。")
    return {
        "provider": provider,
        "base_url": (os.environ.get("HC_LLM_BASE_URL") or "").rstrip("/"),
        "model": os.environ.get("HC_LLM_MODEL") or "",
        "api_key": api_key,
    }


def _post(url: str, headers: dict, payload: dict, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"LLM 接口 HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM 接口不可达: {e.reason}") from None


def complete(system: str, user: str, *, max_tokens: int = 1200) -> str:
    cfg = config()
    if cfg["provider"] == "anthropic":
        base = cfg["base_url"] or "https://api.anthropic.com"
        data = _post(f"{base}/v1/messages",
                     {"x-api-key": cfg["api_key"],
                      "anthropic-version": "2023-06-01"},
                     {"model": cfg["model"] or "claude-sonnet-5",
                      "max_tokens": max_tokens,
                      "system": system,
                      "messages": [{"role": "user", "content": user}]})
        blocks = data.get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    base = cfg["base_url"] or "https://api.openai.com/v1"
    data = _post(f"{base}/chat/completions",
                 {"Authorization": f"Bearer {cfg['api_key']}"},
                 {"model": cfg["model"],
                  "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]})
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM 没有返回内容")
    return choices[0].get("message", {}).get("content", "")


# ── 数字幻觉拦截 ────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers_in(obj) -> set[str]:
    """把 facts 里出现过的所有数字收集起来，含常见的四舍五入形式。"""
    out: set[str] = set()

    def norm(v: float) -> None:
        out.add(f"{v:g}")
        out.add(f"{round(v):g}")
        out.add(f"{round(v, 1):g}")
        out.add(f"{abs(v):g}")
        out.add(f"{round(abs(v)):g}")
        out.add(f"{round(abs(v), 1):g}")

    def walk(o):
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            norm(float(o))
        elif isinstance(o, str):
            for m in _NUM_RE.findall(o):
                try:
                    norm(float(m))
                except ValueError:
                    pass
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return out


def check_numbers(text: str, facts: dict) -> list[str]:
    """返回叙述里出现、但 facts 里找不到的数字。空列表 = 通过。"""
    known = _numbers_in(facts) | _ALLOWED_BARE
    bad = []
    for token in _NUM_RE.findall(text):
        try:
            v = float(token)
        except ValueError:
            continue
        if f"{v:g}" in known or f"{abs(v):g}" in known:
            continue
        bad.append(token)
    return bad


# ── 叙述生成 ────────────────────────────────────────────────────────────

def _slim(facts: dict) -> dict:
    """给模型的精简版事实。刻意不含原始餐食、用药、体检数值。"""
    return {
        "period": facts.get("period"),
        "kpis": facts.get("kpis"),
        "groups": facts.get("groups"),
        "findings": facts.get("findings"),
        "comparisons": [{
            "group": c["group"], "anchor_date": c["anchor_date"],
            "anchor_reason": c["anchor_reason"],
            "loads_comparable": c["loads_comparable"],
            "sets": c["sets"]["text"], "volume": c["volume"]["text"],
            "top_load": c["top_load"]["text"], "best_e1rm": c["best_e1rm"]["text"],
            "added": c["added"], "dropped": c["dropped"],
        } for c in facts.get("comparisons", [])],
        "prescriptions": facts.get("prescriptions"),
        "body": {k: facts.get("body", {}).get(k)
                 for k in ("change_kg", "rate_pct_per_week", "latest_trend_kg", "note")},
        "data_quality": facts.get("data_quality"),
    }


def narrate(facts: dict, slots: list[str] | None = None) -> dict[str, str]:
    """给报告写叙述。任何异常都返回已有结果，绝不让报告因为模型而失败。"""
    slots = slots or ["opening", "training", "closing"]
    # 没配适配器和「调了但失败了」是两回事，要让调用方能区分：
    # 前者需要告诉用户怎么配，后者只需静默退回纯数据模式。
    config()
    # 核心 + 用户选的语气层，拼装逻辑在 persona.py，缺文件也不会抛
    from . import persona as _persona
    system = _persona.load() + (
        "\n\n---\n\n# 当前任务\n"
        "你在为一份已经生成好的健康报告写叙述段落。\n\n"
        "**硬性要求：**\n"
        "1. 只能使用给定 JSON 里出现过的数字。不得引入任何新数字。\n"
        "2. 不得发明新的结论。findings 里已有优点/缺点/改进点，"
        "你的工作是把它们讲得好懂、有温度。\n"
        "3. 不要复述表格。表格已经在报告里了，你写的是串起来的话。\n"
        "4. 涉及用药、疾病、指标解读一律不碰。\n"
        "5. 每段 2-4 句，简洁。\n\n"
        "按下面的 JSON 格式返回，只返回 JSON，不要有别的内容：\n"
        '{"opening": "...", "training": "...", "closing": "..."}\n')

    user = ("这是本期的事实数据：\n\n"
            + json.dumps(_slim(facts), ensure_ascii=False, indent=1)
            + f"\n\n请为这些槽位写叙述：{', '.join(slots)}")

    try:
        raw = complete(system, user)
    except (LLMNotConfigured, RuntimeError):
        return {}

    # 模型可能用 ``` 包起来
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}

    out: dict[str, str] = {}
    for slot, text in parsed.items():
        if slot not in slots or not isinstance(text, str) or not text.strip():
            continue
        bad = check_numbers(text, facts)
        if bad:
            # 宁可少一段话，也不能出现编造的数字
            continue
        out[slot] = text.strip()
    return out
