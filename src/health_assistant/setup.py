"""`hc setup` —— 一次问完所有需要你自己填的东西。

## 为什么需要这个

功能加多了以后，「用户要自己填什么」散在了五个地方：性别身高在
`data/profile.json`、忌口在 `health-constraints.md`、目标在 `personal-context.md`、
红黄绿灯在第四个文件、体检报告是往目录里丢 PDF。

每个位置都有它的道理（机器读的进 json，人读的进 md，原件进目录），
但**没人能记住五个位置**。所以这里做一件事：把散落的问题集中问一遍，
再写回各自该去的地方。

## 一条原则：每个值只有一个真相源

散是可以的，**重是不行的**。同一个数字出现在两个文件里，迟早有一份是旧的 ——
这次就抓到一个：`profile.json` 写身高 179，而 2026-04 体检实测 178。

所以分工是硬的：

| 类别 | 真相源 | 为什么 |
|---|---|---|
| 数值参数（性别/出生年/身高/阶段/偏好） | `data/profile.json` | 引擎要读，必须结构化 |
| 忌口与医学约束 | `profile/health-constraints.md` | 是健康事实，要留变更历史 |
| 病史/目标/沟通偏好 | `profile/personal-context.md` | 是叙述，表格装不下 |
| 体检原件 | `profile/medical/` | 原件就是原件 |

散文文件里**引用**数值，不复制数值。
`hc doctor` 会检查两边冲突并报出来。
"""

from __future__ import annotations

import datetime as dt
import re

from . import dice, store
from . import persona as _persona
from .config import PROFILE_DIR, PROFILE_PATH

CONSTRAINTS_PATH = PROFILE_DIR / "health-constraints.md"

# 「谁负责什么」的完整清单。doctor 和 setup 共用，避免两处各写一份。
DATA_MAP = [
    ("性别 / 出生年 / 身高", "data/profile.json", "hc setup", "算基础代谢和心率区间"),
    ("饮食阶段（减脂/维持/增肌）", "data/profile.json → diet.phase", "hc setup",
     "决定目标热量、破戒额度、红黄绿灯权重"),
    ("教练语气（四选一）", "data/profile.json → persona", "hc setup / hc persona --set",
     "只换措辞，不换规则；核心人格在 knowledge/persona.md，选不了"),
    ("忌口与过敏", "profile/health-constraints.md → ## 忌口", "hc setup", "骰子的硬墙，永不放行"),
    ("医学禁忌（由异常指标推）", "data/profile.json → diet.medical_blocks", "hc setup",
     "骰子的第二堵硬墙，指标恢复要删"),
    ("爱吃 / 不爱吃", "data/profile.json → diet.likes / dislikes", "hc setup", "只调概率，不过滤"),
    ("体重 / 体脂 / 围度", "data/body/YYYY.jsonl", "hc sync body / hc import-health / 跟助手说",
     "目标摄入量按 7 日均值算"),
    ("步数 / 睡眠 / 饮酒", "data/apple-health/metrics.jsonl", "hc import-health <导出.zip>",
     "活动系数从步数推"),
    ("训练记录", "data/training/", "hc sync / hc log", "活动系数也看训练频率"),
    ("体检报告原件", "profile/medical/（文件名带日期）", "拷进去，然后跟助手说一句", "助手解析并跨次对比"),
    ("病史 / 用药 / 目标 / 沟通偏好", "profile/personal-context.md", "跟助手说，它来写", "叙述性内容"),
    ("个人化红黄绿灯", "profile/food-traffic-light.md", "跟助手说", "饮食判断的框架"),
    ("自己常吃的菜", "profile/dish-pool.local.json", "hc dice add", "同名覆盖通用池"),
]


def _ask(prompt: str, default: str | None = None, *, choices: tuple = ()) -> str:
    suffix = f"（{'/'.join(choices)}）" if choices else ""
    shown = f"[{default}]" if default not in (None, "") else "[跳过]"
    while True:
        raw = input(f"  {prompt}{suffix} {shown}: ").strip()
        if not raw:
            return default or ""
        if choices and raw not in choices:
            print(f"    只能填 {'/'.join(choices)}")
            continue
        return raw


def _ask_list(prompt: str, current: list[str]) -> list[str]:
    shown = "、".join(current) if current else "空"
    raw = input(f"  {prompt}\n    当前：{shown}\n    新值（顿号或逗号分隔，"
                f"回车保持不变，填 - 清空）: ").strip()
    if not raw:
        return current
    if raw == "-":
        return []
    return [w for w in re.split(r"[、,，/\s]+", raw) if w]


FORMAT_CONTRACT = (
    "> ⚠️ 下面两行是 `hc dice` 读的，格式别改。契约：`## 忌口` 小节里，\n"
    "> 以 `不吃：` / `过敏：` 开头、到句号为止，顿号分隔。\n"
    "> 解析不出来的后果是骰子安静地把这一层关掉 —— `hc doctor` 会报警，别忽略。\n"
)


def _clause(keyword: str, items: list[str]) -> str:
    return f"{keyword}：{'、'.join(items) if items else '（无）'}。"


def _rewrite_clause(section: str, keyword: str, items: list[str]) -> tuple[str, str | None]:
    """在小节文本里替换（或追加）一句。返回 (新小节, 被替换掉的旧句)。

    **只替换那一句，不是整行。** 之前整行替换会把同一行上的其他内容
    （比如助手记的过敏说明）连带删掉 —— 那是在破坏一份健康档案。
    """
    new = _clause(keyword, items)
    m = re.search(rf"{keyword}[：:][^\n。]*。?", section)
    if not m:
        return section, None
    return section[:m.start()] + new + section[m.end():], m.group(0)


def _update_avoid_line(items: list[str], allergies: list[str] | None = None
                       ) -> tuple[bool, str]:
    """改写 health-constraints.md 的忌口/过敏两句。

    三条硬约束，都是评审抓出来的坑：

    1. **只在「## 忌口」小节里找**。用全文正则找第一行「不吃：」会改到别的
       小节里的历史行 —— 那些按 CLAUDE.md 的约定是只追加不删除的。
    2. **过敏单独维护**，绝不硬编码「无过敏」。医学事实不能被一次回车抹掉。
    3. **只替换那一句**，同一行上的其他文字原样保留。
    """
    allergies = allergies or []
    avoid_c, allergy_c = _clause("不吃", items), _clause("过敏", allergies)

    if not CONSTRAINTS_PATH.exists():
        CONSTRAINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONSTRAINTS_PATH.write_text(
            "# 个人健康约束（饮食与训练）\n\n"
            "> 私密文件，不进版本库。给饮食和训练建议前必读。\n\n"
            f"## 忌口\n\n{avoid_c}{allergy_c}\n\n{FORMAT_CONTRACT}",
            encoding="utf-8")
        return True, f"新建 {CONSTRAINTS_PATH}"

    text = CONSTRAINTS_PATH.read_text(encoding="utf-8")
    span = dice._avoid_section(text)   # 和骰子用同一个定位逻辑，契约不许分叉
    if span is None:
        return False, ("没找到「## 忌口」小节，没有改动。请手动加一节：\n"
                       f"    ## 忌口\n\n    {avoid_c}{allergy_c}")

    start, end = span
    section = text[start:end]
    before = section

    section, old_avoid = _rewrite_clause(section, "不吃", items)
    if old_avoid is None:
        return False, ("「## 忌口」小节里没有「不吃：…」那一句，没有改动"
                       f"（不敢猜位置）。请手动加一行：\n    {avoid_c}")

    section, old_allergy = _rewrite_clause(section, "过敏", allergies)
    if old_allergy is None:
        if allergies:
            # 原文没有过敏句，而用户填了 —— 补在不吃那句后面
            section = section.replace(_clause("不吃", items),
                                      _clause("不吃", items) + allergy_c, 1)
        # 用户没填过敏、原文也没有这一句：什么都不做，不替他断言「无过敏」

    if section == before:
        return False, "忌口/过敏没有变化"

    store.write_atomic(CONSTRAINTS_PATH, text[:start] + section + text[end:])
    changes = [f"不吃  {old_avoid.strip()} → {avoid_c}"]
    if old_allergy is not None and old_allergy.strip() != allergy_c:
        changes.append(f"过敏  {old_allergy.strip()} → {allergy_c}")
    elif old_allergy is None and allergies:
        changes.append(f"过敏  （原本没有这一句）→ {allergy_c}")
    return True, "忌口已更新\n    " + "\n    ".join(changes)


def run(*, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    p = store.read_json(PROFILE_PATH, default={}) or {}
    diet = dict(p.get("diet") or {})
    walls = dice.load_avoid()
    # profile.json 的 diet.avoid 是历史遗留的「补充位」。它和 md 里的忌口是
    # 同一件事的两份拷贝，正是设计里明令禁止的。setup 借这次机会把它并进 md
    # 再清空 —— 否则用户在这里删掉的条目下次又会被合并回来，永远删不掉。
    legacy_avoid = [str(x) for x in (diet.get("avoid") or [])]

    print("hc setup —— 一次填完需要你自己提供的东西")
    print("=" * 52)
    print("回车 = 保持当前值。随时 Ctrl-C 退出，已确认的部分不会写盘。\n")

    print("① 基础生理参数（算基础代谢要用）")
    sex = _ask("性别", p.get("sex"), choices=("male", "female"))
    birth = _ask("出生年份", str(p.get("birth_year") or ""))
    height = _ask("身高 cm", str(p.get("height_cm") or ""))

    print("\n② 当前饮食阶段（决定目标热量和破戒额度）")
    print("   减脂 = 有缺口，额度 1 次/月 ｜ 维持 = 持平，2 次 ｜ 增肌 = 盈余，4 次")
    phase = _ask("阶段", diet.get("phase") or dice.DEFAULT_PHASE,
                 choices=dice.PHASE_NAMES)

    print("\n③ 忌口（个人喜好，按配比占比拦 —— 微量配料不算）")
    if legacy_avoid:
        print(f"   ℹ️  检测到 data/profile.json 里还有一份 diet.avoid（{'、'.join(legacy_avoid)}），"
              "\n      这次会合并进 health-constraints.md 并清空那一份，避免两处不同步。")
    avoid = _ask_list("不吃什么？", walls.avoid)

    print("\n④ 过敏（医学事实，零容忍 —— 微量也拦，不走占比阈值）")
    allergies = _ask_list("对什么过敏？", walls.allergy)

    print("\n⑤ 医学禁忌（由体检异常指标推出来的，指标恢复了要删）")
    print(f"   可选：{' / '.join(dice.FLAGS)}")
    blocks = _ask_list("完全不能吃的类别？", list(diet.get("medical_blocks") or []))
    bad = [b for b in blocks if b not in dice.FLAGS]
    if bad:
        print(f"    ⚠️  忽略了未知标签 {bad}")
        blocks = [b for b in blocks if b in dice.FLAGS]

    print("\n⑥ 口味偏好（只调概率，不过滤。菜名或菜系都行）")
    likes = _ask_list("爱吃什么？", list(diet.get("likes") or []))
    dislikes = _ask_list("不太想吃什么？", list(diet.get("dislikes") or []))

    # ⑦ 语气只换措辞，不换规则 —— 核心人格（数字纪律、安全边界、
    #    「优点必须有数据支撑」）在 knowledge/persona.md 里，选不了。
    print("\n⑦ 教练语气（只改怎么说，不改说什么）")
    for slug, (name, desc) in _persona.TONES.items():
        print(f"   {name}　{desc}")
    tone_label = _ask("语气", _persona.label(_persona.current()),
                      choices=tuple(n for n, _ in _persona.TONES.values()))
    tone = _persona.normalize(tone_label) or _persona.DEFAULT_TONE

    diet.update({"phase": phase, "medical_blocks": blocks,
                 "likes": likes, "dislikes": dislikes,
                 # 忌口的唯一真相源是 md。这里清空，杜绝第二份拷贝。
                 "avoid": []})
    p["diet"] = diet
    p["persona"] = tone
    p.setdefault("_persona_comment", _persona.PROFILE_FIELD_COMMENT)
    if sex:
        p["sex"] = sex
    if birth.isdigit():
        p["birth_year"] = int(birth)
    if height.replace(".", "", 1).isdigit():
        p["height_cm"] = float(height) if "." in height else int(height)

    print("\n" + "=" * 52)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store.write_atomic(PROFILE_PATH, store.dumps_pretty(p))
    print(f"✅ 已写入 {PROFILE_PATH}")

    changed, msg = _update_avoid_line(avoid, allergies)
    print(f"{'✅' if changed else 'ℹ️ '} {msg}")
    if legacy_avoid:
        print("✅ 已清空 data/profile.json 的 diet.avoid（忌口现在只有 md 一份）")

    print("\n下面这些不在 setup 里，跟助手说一句就行：")
    for label, where, how, _why in DATA_MAP:
        if "profile.json" in where or "health-constraints" in where:
            continue
        print(f"  · {label}\n      位置 {where}\n      怎么弄 {how}")

    print("\n核对：hc doctor   算目标摄入：hc targets   试试骰子：hc dice")
    return 0


def render_checklist() -> list[str]:
    """给 hc doctor 用的「还缺什么」清单 + 冲突检查。"""
    from . import nutrition

    lines: list[str] = []
    p = store.read_json(PROFILE_PATH, default={}) or {}
    diet = p.get("diet") or {}
    walls = dice.load_avoid()
    phase, phase_defaulted = dice.load_phase()

    def mark(ok: bool) -> str:
        return "✅" if ok else "⬜"

    lines.append(f"  {mark(p.get('sex') in ('male', 'female'))} 性别"
                 f"　{mark(bool(p.get('birth_year')))} 出生年"
                 f"　{mark(bool(p.get('height_cm')))} 身高"
                 "　← data/profile.json")
    lines.append(f"  {mark(not phase_defaulted)} 饮食阶段：{phase}"
                 + ("（默认值，没设）" if phase_defaulted else "")
                 + "　← diet.phase")
    tone = _persona.current()
    lines.append(f"  {mark(p.get('persona') is not None)} 教练语气："
                 f"{_persona.label(tone)}"
                 + ("" if p.get("persona") is not None else "（默认值，没设）")
                 + "　← persona")
    for w in _persona.warnings():
        lines.append(f"  ⚠️  {w}")

    # ⚠️ 这一行**不能**只看文件在不在。忌口这一层最危险的失败模式是静默失效：
    # 文件还在、格式被改坏、解析返回空 —— 骰子照常跑，只是不再过滤了。
    # 所以解析失败要红着脸报出来，而不是打一个绿勾配一句「未设」。
    if walls.warn:
        lines.append(f"  ❌ 忌口：**没有生效** —— {walls.warn}")
    else:
        lines.append(f"  {mark(bool(walls.avoid))} 忌口："
                     f"{'、'.join(walls.avoid) or '无（已确认为空）'}"
                     "　← health-constraints.md 的「## 忌口」")
        lines.append(f"  {mark(True)} 过敏："
                     f"{'、'.join(walls.allergy) or '无（零容忍，填了会微量也拦）'}")
    if diet.get("avoid"):
        lines.append(f"  ⚠️  data/profile.json 里还有一份 diet.avoid"
                     f"（{'、'.join(str(x) for x in diet['avoid'])}）—— "
                     f"忌口有两份拷贝，跑 `hc setup` 合并掉")

    lines.append(f"  {mark(bool(diet.get('medical_blocks')))} 医学禁忌："
                 f"{'、'.join(diet.get('medical_blocks') or []) or '未设'}　← diet.medical_blocks")

    _, pool_issues = dice.load_pool_with_issues()
    for issue in pool_issues:
        lines.append(f"  ⚠️  候选池：{issue}")

    t = nutrition.targets()
    if t.get("ok"):
        lines.append(f"  ✅ 目标摄入量可算：{t['kcal']} kcal / 蛋白 {t['protein_g']} g")
    else:
        lines.append(f"  ⬜ 目标摄入量算不出，缺：{'；'.join(t['missing'])}")

    # 一致性检查：数值只该有一个真相源，散文里重复了迟早有一份是旧的
    conflicts = _conflicts(p)
    if conflicts:
        lines.append("")
        lines.append("  ⚠️  数值在两个地方对不上（散文文件里的数字应该引用而不是复制）：")
        lines += [f"      {c}" for c in conflicts]

    if not p.get("sex") or phase_defaulted or walls.warn or diet.get("avoid"):
        lines.append("")
        lines.append("  跑 `hc setup` 可以一次填完上面这些。")
    return lines


def _conflicts(p: dict) -> list[str]:
    """散文档案里写死的身高，和 profile.json 对不上就报出来。

    只查身高：它是唯一一个既写在散文里、又被引擎拿去做计算的数值。
    体重之类的以 data/ 为准，散文里本来就不该有。
    """
    out: list[str] = []
    ctx = PROFILE_DIR / "personal-context.md"
    if not ctx.exists() or not p.get("height_cm"):
        return out
    text = ctx.read_text(encoding="utf-8")
    m = re.search(r"身高\s*\**\s*(\d{3})\s*cm", text)
    if m and int(m.group(1)) != int(p["height_cm"]):
        out.append(f"身高：profile.json {p['height_cm']} cm vs "
                   f"personal-context.md {m.group(1)} cm")
    return out
