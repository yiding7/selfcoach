"""教练人格 —— 核心 + 可选语气层。

分两层是有原因的。`knowledge/coach/persona.md` 里那些「数字归脚本」「优点必须有数据
支撑」「身体感受优先于数字」是**行为约束**，换谁用都不能变；而打不打招呼、
用不用表情符号是**口味**，本来就该让用户挑。混在一份文件里的后果是：
用户想换个说话方式，就得去动那些不该动的规则。

所以：

    knowledge/coach/persona.md              核心，不可选
    knowledge/coach/personas/<slug>.md      语气，用户选一个
    data/profile.json 的 persona 字段  选了哪个

`load()` 把两层拼起来。**核心永远在前** —— 语气文件是补充措辞，
不是覆盖规则，顺序反了会让模型以为后面那份优先级更高。

坏掉的选择不能让教练不能用：语气 slug 认不出来、或者文件被删了，
一律退回默认语气并在 `warnings()` 里报出来，绝不抛异常。
"""

from __future__ import annotations

import json

from .config import KNOWLEDGE_DIR, PROFILE_PATH

CORE_PATH = KNOWLEDGE_DIR / "coach" / "persona.md"
PERSONAS_DIR = KNOWLEDGE_DIR / "coach" / "personas"

DEFAULT_TONE = "warm"

PROFILE_FIELD_COMMENT = (
    "教练语气。核心人格在 knowledge/coach/persona.md（不可选、永远生效），"
    "这里只挑语气层，对应 knowledge/coach/personas/<值>.md。"
    "换语气只改措辞，不该改变任何一个数字或结论。"
    "改这个字段用 hc setup 或 hc persona --set。")

ADDRESS_FIELD_COMMENT = (
    "教练怎么称呼你。可以有多个（正式场合一个、平时一个），第一个是默认。"
    "这里是唯一真相源 —— knowledge/ 会进版本库，名字绝不能写进那边；"
    "profile/ 的散文档案里也只引用不复制。空表示不要称呼，教练直接说事。"
    "改这个字段用 hc setup。")

# slug → (显示名, 一句话说明)。顺序就是 hc setup 和 hc persona 的展示顺序。
TONES: dict[str, tuple[str, str]] = {
    "warm":      ("亲切客观", "熟悉你、愿意把话说明白。会打招呼，表情符号少量且有所指"),
    "strict":    ("严厉严肃", "不寒暄，结论前置，短句。要求高，不因状态好而放宽标准"),
    "calm":      ("平和",     "把时间尺度拉长看。不渲染，单日波动默认当噪声"),
    "energetic": ("充满活力", "节奏快，能量高，把坏消息立刻转成这周能做的动作"),
}

# 兼容用户直接填中文显示名 —— 他们在 hc setup 里看到的就是中文。
_BY_LABEL = {label: slug for slug, (label, _) in TONES.items()}


def label(slug: str) -> str:
    """显示名。认不出来的 slug 原样返回，方便在告警里指出到底填了什么。"""
    entry = TONES.get(slug)
    return entry[0] if entry else slug


def normalize(raw: object) -> str | None:
    """把用户填的东西归一成 slug。认不出来返回 None（调用方决定怎么办）。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s in TONES:
        return s
    if s in _BY_LABEL:
        return _BY_LABEL[s]
    low = s.lower()
    return low if low in TONES else None


def _profile() -> dict:
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def current() -> str:
    """当前语气的 slug。没设过或者设错了都退回默认，不抛异常。"""
    return normalize(_profile().get("persona")) or DEFAULT_TONE


def tone_path(slug: str | None = None):
    return PERSONAS_DIR / f"{slug or current()}.md"


def addresses() -> list[str]:
    """怎么称呼用户。空列表 = 没设，或者用户明确选择不要称呼。

    **空是一个合法答案，不是缺失。** 有人不喜欢被叫名字，教练直接说事就行 ——
    所以这里不给任何默认值，也不去别处猜（猜出来的称呼比没有称呼更冒犯）。

    容错和 `current()` 一致：字段类型不对就当没设，不抛异常 ——
    一个称呼字段填坏了不该让整个教练用不了。
    """
    raw = _profile().get("address")
    if isinstance(raw, str):          # 单个字符串也认，用户手改 json 时很容易这么写
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [s.strip() for s in raw if isinstance(s, str) and s.strip()]


def warnings() -> list[str]:
    """配置层面的问题。给 doctor 用 —— 它要能说清「为什么没生效」。"""
    out = []
    raw = _profile().get("persona")
    if raw is not None and normalize(raw) is None:
        out.append(f"data/profile.json 的 persona 填的是「{raw}」，不认识，"
                   f"已退回默认语气「{label(DEFAULT_TONE)}」。"
                   f"可选：{'、'.join(label(s) for s in TONES)}")
    if not CORE_PATH.exists():
        out.append(f"缺少 {CORE_PATH.name} —— 人格核心没了，教练会退化成通用模型")
    slug = current()
    if not tone_path(slug).exists():
        out.append(f"缺少 knowledge/coach/personas/{slug}.md —— 语气层没生效，只剩核心")
    return out


def set_tone(slug: str) -> str:
    """写回 data/profile.json。返回落盘的 slug。"""
    norm = normalize(slug)
    if norm is None:
        raise ValueError(f"不认识的语气「{slug}」，可选：{'、'.join(TONES)}")
    data = _profile()
    data["persona"] = norm
    data.setdefault("_persona_comment", PROFILE_FIELD_COMMENT)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return norm


def load(slug: str | None = None) -> str:
    """核心 + 语气，拼成一份完整的 system prompt。

    核心在前。缺哪一块就少哪一块，但只要核心还在就能用 —— 语气没了顶多
    说话干巴，核心没了就不是这个教练了。
    """
    slug = normalize(slug) or current()
    parts = []
    try:
        parts.append(CORE_PATH.read_text(encoding="utf-8").rstrip())
    except OSError:
        parts.append("你是一位谦和、专业、鼓励式的健身教练。"
                     "所有数字必须来自确定性脚本，不要自己心算或估算。")
    try:
        parts.append(tone_path(slug).read_text(encoding="utf-8").rstrip())
    except OSError:
        pass
    return "\n\n---\n\n".join(parts) + "\n"


def render_list() -> str:
    """给 hc persona 用的清单。"""
    cur = current()
    lines = ["教练语气（核心人格不可选，见 knowledge/coach/persona.md）",
             "=" * 46]
    for slug, (name, desc) in TONES.items():
        mark = "●" if slug == cur else "○"
        missing = "" if tone_path(slug).exists() else "  ← 文件缺失"
        lines.append(f"  {mark} {name}（{slug}）{missing}")
        lines.append(f"      {desc}")
    lines.append("")
    lines.append(f"  当前：{label(cur)}　←　data/profile.json 的 persona")
    # 称呼跟语气一起显示：它俩是同一件事的两面（怎么开口），
    # 而且用户想核对「教练该怎么叫我」时不会想到去翻 json。
    addrs = addresses()
    lines.append(f"  称呼：{'、'.join(addrs) if addrs else '不用称呼（address 为空）'}"
                 f"　←　data/profile.json 的 address"
                 + ("　第一个是默认" if len(addrs) > 1 else ""))
    lines.append("  切换：hc persona --set 严厉严肃    或    hc setup")
    for w in warnings():
        lines.append(f"  ⚠️  {w}")
    return "\n".join(lines)
