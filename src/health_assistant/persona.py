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
from pathlib import Path

from .config import KNOWLEDGE_DIR, PROFILE_PATH, ROOT

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

COACH_NAME_FIELD_COMMENT = (
    "你怎么称呼教练。可以有多个（本名一个、简称一个），第一个是教练自称时用的。"
    "和 address 是同一件事的另一半：address 管教练怎么叫你，这里管你怎么叫教练。"
    "**还有一个用处是让教练认出自己被叫到了** —— 一句话以这里的任何一个名字开头，"
    "就是在叫它。空表示不起名字，教练就是「教练」。"
    "改这个字段用 hc setup 或 hc persona --set-coach-name。")

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


def _name_list(field: str) -> list[str]:
    """`address` / `coach_name` 这类「名字列表」字段的统一读法。

    容错和 `current()` 一致：字段类型不对就当没设，不抛异常 ——
    一个称呼字段填坏了不该让整个教练用不了。
    """
    raw = _profile().get(field)
    if isinstance(raw, str):          # 单个字符串也认，用户手改 json 时很容易这么写
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [s.strip() for s in raw if isinstance(s, str) and s.strip()]


def _name_list_warning(field: str, fallback: str, example: str) -> str | None:
    """名字列表字段填坏了的话，一句人话说清「没有生效」。没问题返回 None。

    为什么单独开一个函数而不是只在 `warnings()` 里塞一行：调用方要能**区分**
    「字段是空的（用户明确选择不要）」和「字段填坏了被丢掉了」。
    这两种情况读函数都返回 `[]`，而 `hc doctor` 原来只看字段在不在，
    于是手改成 `{"formal": "王先生"}` 会被报成「✅ 称呼：不要称呼（已确认）」——
    用户明明填了两个称呼，工具却说系统已确认他不要称呼。

    这和忌口那一层的纪律是同一条：解析失败要红着脸报出来，不许打绿勾糊弄。
    读函数本身继续容错返回 `[]`，但**不能静默**。
    """
    raw = _profile().get(field)
    if raw is None or isinstance(raw, str):
        return None
    if not isinstance(raw, list):
        return (f"data/profile.json 的 {field} 是 {type(raw).__name__} 类型，"
                f"这里只认字符串或字符串数组（比如 {example}）—— "
                f"**这个字段没有生效**，教练当成{fallback}。用 hc setup 重填")
    bad = [x for x in raw if not isinstance(x, str)]
    if bad:
        return (f"data/profile.json 的 {field} 里有 {len(bad)} 项不是文字，已忽略 —— "
                f"这几项**没有生效**。用 hc setup 重填")
    return None


def addresses() -> list[str]:
    """怎么称呼用户。空列表 = 没设，或者用户明确选择不要称呼。

    **空是一个合法答案，不是缺失。** 有人不喜欢被叫名字，教练直接说事就行 ——
    所以这里不给任何默认值，也不去别处猜（猜出来的称呼比没有称呼更冒犯）。
    """
    return _name_list("address")


def address_warning() -> str | None:
    return _name_list_warning("address", "没有称呼", '["老王", "王先生"]')


def coach_names() -> list[str]:
    """用户怎么叫教练。空列表 = 没起过名字，或者明确选择不起。

    这个字段有两个用处，第二个容易被忽略：

    1. 教练**自称**时用第一个（「阿尺觉得…」比「我觉得…」少见，但签名、
       落款、日志署名要用）
    2. **认出自己被叫到了** —— 用户一句话以这里的任何一个名字开头，
       就是在叫教练。多个别名全都算叫它，所以这里返回的是**全部**别名，
       调用方不要只取第一个去做匹配

    和 `addresses()` 同一条纪律：空是答案不是缺失，**绝不自己编一个**。
    没起名字的教练就叫「教练」，那完全够用。
    """
    return _name_list("coach_name")


def coach_name_warning() -> str | None:
    return _name_list_warning("coach_name", "没起名字（就叫「教练」）",
                              '["阿尺", "Chi"]')


def warnings() -> list[str]:
    """配置层面的问题。给 doctor 用 —— 它要能说清「为什么没生效」。"""
    out = []
    for w in (address_warning(), coach_name_warning()):
        if w:
            out.append(w)
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


def set_coach_names(names: list[str] | str | None) -> list[str]:
    """写回 data/profile.json 的 coach_name。返回落盘后的列表。

    传空列表是**合法的**，意思是「不给教练起名字」—— 和 `hc setup` 里
    「直接回车 = 不要称呼」是同一个语义。所以这里不把空当成错误，
    但会把字段真的写成 `[]`，让 `coach_name_set` 能区分「明确不要」和「没问过」。
    """
    if names is None:
        names = []
    if isinstance(names, str):
        names = [names]
    clean, seen = [], set()
    for n in names:
        if not isinstance(n, str):
            raise ValueError(f"名字必须是文字，收到 {type(n).__name__}")
        s = n.strip()
        if s and s not in seen:      # 去重但保持顺序 —— 第一个是教练自称用的那个
            seen.add(s)
            clean.append(s)
    data = _profile()
    data["coach_name"] = clean
    data.setdefault("_coach_name_comment", COACH_NAME_FIELD_COMMENT)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return clean


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


def as_dict() -> dict:
    """当前人格状态的**机器可读**快照。`hc persona --json` 打的就是它。

    为什么必须有这个：`render_list()` 是给人看的清单，措辞随时会调。
    `scripts/coach` 原来用 sed 从那份展示文本里抠「当前：」和「称呼：」，
    一改措辞就抠不到 —— 而 sed 抠不到只会安静地给出空串，注入 system prompt 的
    语气和称呼整段消失，屏幕上一个字的错都没有。agent 于是退回
    「不知道该怎么称呼」甚至自己编一个，正是那段代码想防的事。

    **这里的键名是接口**，改名等于悄悄弄坏调用方。`address_set` 单独给一个键，
    是因为「明确不要称呼」和「还没问过」对 agent 是两种不同的指示。
    """
    cur = current()
    return {
        "tone": cur,
        "tone_label": label(cur),
        "tone_file": _rel(tone_path(cur)),
        "core_file": _rel(CORE_PATH),
        "addresses": addresses(),
        "address_set": "address" in _profile(),
        "address_ok": address_warning() is None,
        "coach_names": coach_names(),
        "coach_name_set": "coach_name" in _profile(),
        "coach_name_ok": coach_name_warning() is None,
        "warnings": warnings(),
    }


def _rel(p) -> str:
    """路径一律相对仓库根 —— 这些字符串会进 system prompt，绝对路径既长又泄露用户名。"""
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


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
    if not addrs and address_warning():
        # 「空」和「填坏了」必须分开说。混成一句「不用称呼」，
        # 用户会以为工具收下了他填的东西。
        shown = "**没有生效**（address 格式不对，见下面的告警）"
    else:
        shown = "、".join(addrs) if addrs else "不用称呼（address 为空）"
    lines.append(f"  称呼：{shown}"
                 f"　←　data/profile.json 的 address"
                 + ("　第一个是默认" if len(addrs) > 1 else ""))
    # 教练的名字紧跟在称呼后面 —— 它俩是同一件事的两半，分开显示用户会漏掉一半。
    names = coach_names()
    if not names and coach_name_warning():
        shown_name = "**没有生效**（coach_name 格式不对，见下面的告警）"
    else:
        shown_name = ("、".join(names) if names
                      else "没起名字（就叫「教练」）")
    lines.append(f"  教练叫：{shown_name}"
                 f"　←　data/profile.json 的 coach_name"
                 + ("　第一个是自称用的" if len(names) > 1 else ""))
    lines.append("  切换：hc persona --set 严厉严肃    或    hc setup")
    lines.append("  改名：hc persona --set-coach-name 阿尺,Chi"
                 "    （填「清空」= 不起名字）")
    for w in warnings():
        lines.append(f"  ⚠️  {w}")
    return "\n".join(lines)
