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
这次就抓到一个：`profile.json` 里的身高和最近一次体检实测差了 1 cm，
而两处谁都不知道对方存在。

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
from . import plan as _plan
from .config import PROFILE_DIR, PROFILE_PATH

CONSTRAINTS_PATH = PROFILE_DIR / "health-constraints.md"

# 「谁负责什么」的完整清单。doctor 和 setup 共用，避免两处各写一份。
DATA_MAP = [
    ("性别 / 出生年月 / 身高", "data/profile.json", "hc setup",
     "算基础代谢和心率区间。出生年月到**月**，只填年份年龄全年可能偏大 1 岁"),
    ("体重", "data/body/YYYY.jsonl（不进 profile.json）", "自动同步 / hc setup",
     "它是随时间变的数据，不是固定参数。setup 里近 7 天有数据就直接用"),
    ("饮食阶段（减脂/维持/增肌）", "data/profile.json → diet.phase", "hc setup",
     "决定目标热量、破戒额度、红黄绿灯权重"),
    ("教练语气（四选一）", "data/profile.json → persona", "hc setup / hc persona --set",
     "只换措辞，不换规则；核心人格在 knowledge/coach/persona.md，选不了"),
    ("称呼（怎么叫你，可多个，可留空）", "data/profile.json → address", "hc setup",
     "唯一真相源。knowledge/ 会进版本库，名字绝不能写那边；留空就是不要称呼"),
    ("训练计划（频率/单次时长/侧重）", "data/profile.json → training", "hc setup",
     "hc next 拿单次时长提示超没超；建议值按目标推，以你填的为准"),
    ("周起始日（周一/周日）", "data/profile.json → training.week_start", "hc setup",
     "全局的「一周」口径：周报分桶、骰子每周黄灯计数。改了旧周报不会重写"),
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


def _ask_choice(prompt: str, options: list[tuple[str, str]],
                default: str | None = None) -> str:
    """固定选项 —— 打编号就行，不用打字。

    `options` 是 [(值, 一句话说明)]。返回选中的**值**，不是编号。
    仍然接受直接输入值本身 —— 有人就是想打字，不该拦着。
    """
    by_num = {str(i): val for i, (val, _) in enumerate(options, 1)}
    by_val = {val: val for val, _ in options}
    default_num = next((n for n, v in by_num.items() if v == default), None)

    print(f"  {prompt}")
    for i, (val, desc) in enumerate(options, 1):
        mark = "●" if val == default else "○"
        print(f"    {mark} {i}. {val}" + (f"　{desc}" if desc else ""))

    hint = f"[{default_num}. {default}]" if default_num else "[跳过]"
    while True:
        raw = input(f"    选一个（打编号）{hint}: ").strip()
        if not raw:
            return default or ""
        if raw in by_num:
            return by_num[raw]
        if raw in by_val:
            return raw
        print(f"    请输入 1~{len(options)} 之间的编号")


# 分隔符尽量放宽 —— 用户脑子里想的是「把几样东西列出来」，
# 不该让他还要记住这个工具认哪个符号。中英文标点、斜杠、下划线、
# 连字符、空格、分号、竖线全认。
SEPARATORS = r"[、，,；;/\\|_\-\s]+"


def _split_items(raw: str) -> list[str]:
    return [w for w in re.split(SEPARATORS, raw.strip()) if w]


def _ask_list(prompt: str, current: list[str]) -> list[str]:
    shown = "、".join(current) if current else "空"
    raw = input(f"  {prompt}\n    当前：{shown}\n"
                f"    新值（空格 / 顿号 / 逗号 / 斜杠 / 下划线 都能分隔，"
                f"回车保持不变，填「清空」两个字或单个 - 清空）: ").strip()
    if not raw:
        return current
    if raw in ("-", "清空", "无", "none", "NONE"):
        return []
    return _split_items(raw)


def _ask_address(current: list[str]) -> list[str]:
    """⑧ 称呼。**留空是一个合法答案，不是跳过一道必答题** —— 有人不喜欢被叫名字。

    提示语必须分「第一次填」和「改已有值」两种情况说。一句通用的
    「直接回车 = 不要称呼」在已经设过 address 的人身上是**假的**：`_ask_list`
    把空输入一律当「保持不变」，于是他按提示回车，值原样留着，
    `hc persona` 和 `scripts/coach` 继续注入那个他不想再听见的名字。

    修的方向是让提示语说真话，而不是让回车去删值 —— 回车在整个向导里到处都表示
    「这项不改」，只在这一步变成删除操作才是真正危险的不一致。所以已经有值时，
    把「我不想被叫名字了」这个意图明确指向 `-` / 「清空」。
    """
    print("\n⑧ 称呼（教练怎么叫你。可以填多个，第一个是默认）")
    if current:
        print("   直接回车 = 保持现在这几个不变。")
        print("   以后不想被叫名字了：填单个 - 或「清空」，教练就开门见山说事。")
    else:
        print("   直接回车 = 不要称呼，教练开门见山说事 —— 这是个正经答案，不是跳过。")
    print("   多个的用法：正式一点的场合用一个，平时用另一个。")
    return _ask_list("怎么称呼你？", current)


def _ask_multi(prompt: str, options: tuple[str, ...], current: list[str]) -> list[str]:
    """固定选项的多选 —— 打编号，编号之间随便用什么分隔。

    编号必须有分隔符：选项一旦超过 9 个，`112` 到底是「1,12」还是「11,2」
    就没法判断了。所以这里**不接受**连写，宁可报错也不猜。
    """
    print(f"  {prompt}")
    width = len(str(len(options)))
    for i, val in enumerate(options, 1):
        mark = "●" if val in current else "○"
        print(f"    {mark} {i:>{width}}. {val}")
    shown = "、".join(current) if current else "空"
    while True:
        raw = input(f"    当前：{shown}\n"
                    f"    选哪几个（编号，用空格或逗号隔开，"
                    f"回车保持不变，填「清空」清空）: ").strip()
        if not raw:
            return current
        if raw in ("-", "清空", "无", "none", "NONE"):
            return []
        picks = _split_items(raw)
        bad = [t for t in picks if not (t.isdigit() and 1 <= int(t) <= len(options))]
        if bad:
            print(f"    认不出来：{'、'.join(bad)}　"
                  f"—— 只填 1~{len(options)} 的编号，中间用空格或逗号隔开")
            continue
        # 去重但保持用户给的顺序
        seen, out = set(), []
        for t in picks:
            val = options[int(t) - 1]
            if val not in seen:
                seen.add(val)
                out.append(val)
        return out


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


def _ask_birth(p: dict, today: dt.date) -> tuple[int, int | None] | None:
    """出生年月。**到月的精度**，不是只到年。

    只有年份时，「今年 − 出生年」全年都可能偏大一岁。基础代谢和最大心率
    都吃年龄，差一岁不致命，但既然填一次就管一辈子，不如填准。
    """
    from . import age as age_mod

    cur = age_mod.format_ym(p)
    if cur:
        n, how = age_mod.age_at(today, p)
        print(f"  当前：{cur}　→ {n} 岁（{how}）")
    while True:
        raw = _ask("出生年月 YYYY-MM（只填年份也行，但会差一岁）", cur)
        if not raw:
            return None
        parsed = age_mod.parse_ym(raw)
        if parsed:
            year, month = parsed
            n, how = age_mod.age_at(today, {"birth_year": year, "birth_month": month})
            print(f"    → {n} 岁（{how}）")
            return parsed
        print("    认不出来。写成 1993-11 或 1993 这样")


def _ask_height(p: dict) -> float | None:
    """身高。**单位只支持 cm**，不做单位换算 —— 猜单位是造假数据最快的路。"""
    cur = p.get("height_cm")
    while True:
        raw = _ask("身高 cm（只支持厘米）", str(cur) if cur else "")
        if not raw:
            return None
        cleaned = re.sub(r"(?i)\s*(cm|厘米|公分)\s*$", "", raw).strip()
        try:
            val = float(cleaned)
        except ValueError:
            print("    请填一个数字，单位固定是 cm。比如 175")
            continue
        if not (100 <= val <= 250):
            print(f"    {val} cm 看着不像身高。单位是**厘米**，不是米也不是英寸")
            continue
        return val


def _ask_weight(today: dt.date, *, fresh_days: int = 7) -> dict | None:
    """体重。存在 `data/body/`，不存 profile.json —— 它是随时间变的数据。

    近 7 天有读数就直接用，回车跳过。这一条是刻意的：体重每天都在自动同步，
    在 setup 里手填一个反而可能比自动同步的更旧。
    """
    records = [r for r in store.load_body(types=["weight"])]
    records.sort(key=lambda r: r.get("date", ""))
    latest = records[-1] if records else None
    same_day = [r for r in records if r.get("date") == today.isoformat()]

    if latest:
        stale = (today - dt.date.fromisoformat(latest["date"])).days
        src = latest.get("source") or "未标来源"
        state = "最新" if stale <= fresh_days else f"⚠️ {stale} 天前，已经不新鲜"
        print(f"  当前体重：{latest['value']:g} kg"
              f"（{latest['date']}，来源 {src} —— {state}）")
        print("    注：体重记录只精确到日期，没有具体时刻。"
              "自动同步取的是当天最早一条（晨起）。")
        if stale <= fresh_days:
            print("    近 7 天内有数据，直接回车用它就行。")
    else:
        print("  当前体重：data/ 里还没有任何体重记录。")

    if same_day:
        # 趋势体重是按**记录条数**开窗的（rolling_weight 取最近 7 条），
        # 同一天两条会让今天在窗口里占两格、窗口实际只覆盖 6 天，
        # 目标热量和自重动作的容量都会跟着偏。所以这里直接不再写。
        srcs = "、".join(sorted({r.get("source") or "未标来源" for r in same_day}))
        vals = "、".join(f"{r['value']:g}" for r in same_day)
        print(f"  ⚠️  今天（{today}）已经有 {len(same_day)} 条体重记录了"
              f"（{vals} kg，来源 {srcs}）。")
        print("      同一天两条会让 7 日趋势把今天算两次，所以这一项**跳过不写**。")
        print("      要改今天的数值，直接编辑 data/body/"
              f"{today.year}.jsonl 里那一条。")
        return None

    while True:
        raw = _ask("体重 kg（只支持公斤。回车 = 用 data/ 里的）", "")
        if not raw:
            return None
        if raw in ("-", "清空"):
            print("    体重是时间序列，不在这里清 —— "
                  "要删某条记录直接编辑 data/body/YYYY.jsonl。这次跳过。")
            return None
        cleaned = re.sub(r"(?i)\s*(kg|公斤|千克)\s*$", "", raw).strip()
        try:
            val = float(cleaned)
        except ValueError:
            print("    请填一个数字，单位固定是 kg。比如 75，小数也行")
            continue
        if not (20 <= val <= 400):
            print(f"    {val} kg 看着不像体重。单位是**公斤**，不是斤也不是磅")
            continue
        return {"schema": "ha.body/1",
                "id": f"self:weight:{today.isoformat()}",
                "source": "self", "date": today.isoformat(),
                "type": "weight", "value": val, "unit": "kg", "label": "体重"}


def actual_days_per_week(*, weeks: int = 8, today: dt.date | None = None
                         ) -> tuple[float, float] | None:
    """近 N 周实际的训练频率。返回 (次/周, 实际覆盖的周数)；没数据返回 None。

    ⚠️ **分母是实际有记录的跨度，不是固定的 N。** 一度写死除以 8：
    只同步了 3 周历史、每周练 4 次（12 天）的人会算出 12/8 = 1.5 次/周，
    然后被提醒「你练太少了，要不要把目标改小」—— 而他其实每周都达标。
    刚开始用这个工具的人恰好最容易撞上，也最经不起这种误报。

    返回的周数要一起给出去：调用方得知道这个比例是拿几周算的，
    才能决定值不值得据此说话。
    """
    today = today or dt.date.today()
    start = (today - dt.timedelta(weeks=weeks)).isoformat()
    try:
        sessions = store.load_sessions(start=start, end=today.isoformat())
    except Exception:
        return None
    days = sorted({s["date"] for s in sessions if s.get("date")})
    if not days:
        return None
    try:
        first = dt.date.fromisoformat(days[0])
    except ValueError:
        return None
    # 至少算一周，否则只有一两天记录时会除出一个荒唐的大数
    span_weeks = max((today - first).days + 1, 7) / 7.0
    return round(len(days) / span_weeks, 1), round(span_weeks, 1)


def _ask_training(p: dict, phase: str) -> dict:
    """⑨ 训练计划。

    顺序是刻意的：**先按目标算出一份建议并说明理由，再让用户填**。
    反过来（先问、再评价他填的）会变成脚本在考用户；而只给建议不让改，
    就成了处方。这两个都不对 —— 建议是默认值，用户填的是结论。
    """
    old = dict(p.get("training") or {})
    goal_metric = ((p.get("goal") or {}).get("metric")) or None
    rec, why = _plan.suggest(goal_metric=goal_metric, phase=phase,
                             current_days=actual_days_per_week())

    print("\n⑨ 训练计划")
    print("   下面这份是按你当前目标推的**建议值**，不是处方 —— 直接回车就采用它，")
    print("   想按自己的安排来就改，以你填的为准。")
    for line in why:
        print(f"   · {line}")

    print("\n   一周几练是**排计划用的参考值，不是考核指标**：")
    print("     · 让教练出计划时，它按你填的次数排（填 4 就排 4 练的分化）")
    print("     · 某一周没练够不会有任何提示 —— 状态不好优先补觉，这是原则")
    print("     · 只有连续多周明显低于它，才会问一句「要不要把这个数改小」")
    days = _ask("一周练几次（参考值）",
                str(old.get("days_per_week") or rec["days_per_week"]))
    minutes = _ask("单次训练多少分钟（不含热身/通勤）",
                   str(old.get("session_minutes") or rec["session_minutes"]))
    focus = _ask_choice("侧重", [
        ("力量为主", "有氧只做必要的量"),
        ("有氧为主", "力量维持即可"),
        ("力量有氧并重", "两边都排进计划"),
    ], old.get("focus") or rec["focus"])

    print("\n   周起始日 —— 只影响「一周」这个桶怎么切（周报分桶、骰子的每周黄灯计数），")
    print("   不影响任何一次训练怎么练。改它会让新旧周报的分桶口径不一致，旧报告不会重写。")
    week_start = _ask_choice("每周从哪天开始", [
        ("周一", "ISO 8601 与国内日历习惯"),
        ("周日", "不少健康类 App（含苹果健康）的默认"),
    ], _plan.normalize_week_start(old.get("week_start")) or _plan.DEFAULT_WEEK_START)

    out = dict(old)
    out["_comment"] = _plan.PROFILE_FIELD_COMMENT
    out["week_start"] = _plan.normalize_week_start(week_start) or _plan.DEFAULT_WEEK_START
    out["focus"] = focus if focus in _plan.FOCUS_CHOICES else _plan.DEFAULT_FOCUS
    for key, raw in (("days_per_week", days), ("session_minutes", minutes)):
        try:
            n = int(str(raw).strip())
        except ValueError:
            n = 0
        out[key] = n if n > 0 else None

    cap = _plan.TrainingPlan(**{k: v for k, v in out.items()
                                if k in {"week_start", "days_per_week",
                                         "session_minutes", "focus"}}).session_set_cap
    if cap:
        print(f"\n   → 单次 {out['session_minutes']} 分钟 ≈ {cap} 组（所有部位合计，"
              f"按实测密度 {_plan.SETS_PER_MINUTE:g} 组/分钟推）。")
        print("     hc next 会拿它提示超没超，但**不会替你砍组** —— 砍哪几组是你的判断。")
    return out


def run(*, today: dt.date | None = None, dry_run: bool = False) -> int:
    """`dry_run=True` 走完全部问答但一个字都不写盘。

    加这个开关是有原因的：`hc setup` 会改档案、还会往 `data/body/` 写体重，
    而它又是纯交互的 —— 想验证它「问得对不对」就只能真跑一遍。
    2026-08-10 就这么把真实档案写坏过一次（语气、周起始日、医学禁忌被测试值覆盖，
    还多出一条编造的体重）。一个会改数据又没法排练的命令，早晚会出这个事。
    """
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
    sex = _ask_choice("性别", [("male", "男"), ("female", "女")], p.get("sex"))
    birth_ym = _ask_birth(p, today)
    height = _ask_height(p)
    weight_record = _ask_weight(today)

    print("\n② 当前饮食阶段（决定目标热量和破戒额度）")
    phase = _ask_choice("阶段", [
        ("减脂", "有缺口，破戒额度 1 次/月"),
        ("维持", "持平，2 次/月"),
        ("增肌", "盈余，4 次/月"),
    ], diet.get("phase") or dice.DEFAULT_PHASE)

    print("\n③ 忌口（个人喜好，按配比占比拦 —— 微量配料不算）")
    if legacy_avoid:
        print(f"   ℹ️  检测到 data/profile.json 里还有一份 diet.avoid（{'、'.join(legacy_avoid)}），"
              "\n      这次会合并进 health-constraints.md 并清空那一份，避免两处不同步。")
    avoid = _ask_list("不吃什么？", walls.avoid)

    print("\n④ 过敏（医学事实，零容忍 —— 微量也拦，不走占比阈值）")
    allergies = _ask_list("对什么过敏？", walls.allergy)

    print("\n⑤ 医学禁忌（由体检异常指标推出来的，指标恢复了要删）")
    current_blocks = [str(b) for b in (diet.get("medical_blocks") or [])]
    # 不在标准标签里的条目**不能默默丢掉** —— 这是医学硬墙，助手也可能写过
    # 非标准标签进来。改成编号多选之后，如果只把已知项喂进去，用户按回车
    # 「保持不变」就会把未知项永久删掉，而且一句提示都没有。
    unknown = [b for b in current_blocks if b not in dice.FLAGS]
    if unknown:
        print(f"   ⚠️  现有 {len(unknown)} 项不在标准标签里：{'、'.join(unknown)}")
        print("      它们不在下面的编号里。**回车保持不变会连它们一起保留；"
              "重新选择则会丢弃它们。**")
    blocks = _ask_multi("完全不能吃的类别？", dice.FLAGS, current_blocks)
    dropped_unknown = [b for b in unknown if b not in blocks]
    if dropped_unknown:
        print(f"   ⚠️  这次会丢弃：{'、'.join(dropped_unknown)}"
              f"　（要留着就 Ctrl-C 重来，按回车那一步保持不变）")

    print("\n⑥ 口味偏好（只调概率，不过滤。菜名或菜系都行）")
    likes = _ask_list("爱吃什么？", list(diet.get("likes") or []))
    dislikes = _ask_list("不太想吃什么？", list(diet.get("dislikes") or []))

    # ⑦ 语气只换措辞，不换规则 —— 核心人格（数字纪律、安全边界、
    #    「优点必须有数据支撑」）在 knowledge/coach/persona.md 里，选不了。
    print("\n⑦ 教练语气（只改怎么说，不改说什么）")
    tone_label = _ask_choice(
        "语气", [(name, desc) for name, desc in _persona.TONES.values()],
        _persona.label(_persona.current()))
    tone = _persona.normalize(tone_label) or _persona.DEFAULT_TONE

    address = _ask_address(_persona.addresses())

    training = _ask_training(p, phase)

    diet.update({"phase": phase, "medical_blocks": blocks,
                 "likes": likes, "dislikes": dislikes,
                 # 忌口的唯一真相源是 md。这里清空，杜绝第二份拷贝。
                 "avoid": []})
    p["diet"] = diet
    p["persona"] = tone
    p["address"] = address
    p["training"] = training
    p.setdefault("_persona_comment", _persona.PROFILE_FIELD_COMMENT)
    p.setdefault("_address_comment", _persona.ADDRESS_FIELD_COMMENT)
    if sex:
        p["sex"] = sex
    if birth_ym:
        year, month = birth_ym
        p["birth_year"] = year
        if month:
            p["birth_month"] = month
        else:
            # 从「有月份」退回「只有年份」是有意的降级，要真的删掉那个字段，
            # 否则会留下一个和用户刚填的值矛盾的旧月份。
            p.pop("birth_month", None)
        p["_birth_comment"] = (
            "出生年月。年龄的唯一真相源是 src/health_assistant/age.py，"
            "约定生日按当月 1 号计（只到月的精度，再细就是假精确）。"
            "birth_month 缺失时退回「今年 − 出生年」，全年可能偏大一岁。")
    if height:
        p["height_cm"] = int(height) if float(height).is_integer() else height

    print("\n" + "=" * 52)
    if dry_run:
        print("🧪 --dry-run：下面是**会**发生的事，实际一个字都没写。\n")
        print(f"  会写 {PROFILE_PATH}")
        for k in ("sex", "birth_year", "birth_month", "height_cm", "persona"):
            if k in p:
                print(f"    {k} = {p[k]}")
        print(f"    address = {address or '[]（不要称呼）'}")
        print(f"    diet.phase = {diet['phase']}")
        print(f"    diet.medical_blocks = {diet['medical_blocks']}")
        print(f"    diet.likes = {diet['likes']}")
        print(f"    diet.dislikes = {diet['dislikes']}")
        print(f"    training = {{days_per_week: {training['days_per_week']}, "
              f"session_minutes: {training['session_minutes']}, "
              f"focus: {training['focus']}, week_start: {training['week_start']}}}")
        if weight_record:
            print(f"  会写 data/body/{weight_record['date'][:4]}.jsonl"
                  f"    体重 {weight_record['value']:g} kg @ {weight_record['date']}")
        print(f"  会改 {CONSTRAINTS_PATH} 的「## 忌口」小节")
        print(f"    不吃：{'、'.join(avoid) or '（空）'}")
        print(f"    过敏：{'、'.join(allergies) or '（无）'}")
        print("\n去掉 --dry-run 才会真的写。")
        return 0

    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store.write_atomic(PROFILE_PATH, store.dumps_pretty(p))
    print(f"✅ 已写入 {PROFILE_PATH}")

    # 体重不进 profile.json —— 它是随时间变的数据，归 data/body/。
    # 这是 README「你要自己填什么」里「同一个值只有一个真相源」那条规矩的直接后果。
    if weight_record:
        store.upsert_body([weight_record])
        print(f"✅ 体重 {weight_record['value']:g} kg 已写入 "
              f"data/body/{weight_record['date'][:4]}.jsonl"
              f"（{weight_record['date']}，source=self）")

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

    from . import age as _age
    n, how = _age.age_at()
    birth_txt = _age.format_ym(p) or "未设"
    lines.append(f"  {mark(p.get('sex') in ('male', 'female'))} 性别"
                 f"　{mark(bool(p.get('birth_year')))} 出生年月 {birth_txt}"
                 f"　{mark(bool(p.get('height_cm')))} 身高"
                 "　← data/profile.json")
    if n is not None:
        # 只有年份时年龄全年可能偏大一岁 —— 打出口径，别让人以为这是精确值
        flag = "⚠️ " if "偏大" in how else ""
        lines.append(f"      {flag}当前 {n} 岁（{how}）")
    lines.append(f"  {mark(not phase_defaulted)} 饮食阶段：{phase}"
                 + ("（默认值，没设）" if phase_defaulted else "")
                 + "　← diet.phase")
    tone = _persona.current()
    lines.append(f"  {mark(p.get('persona') is not None)} 教练语气："
                 f"{_persona.label(tone)}"
                 + ("" if p.get("persona") is not None else "（默认值，没设）")
                 + "　← persona")
    # 称呼：**空不算缺**，所以判据是「问过没有」（字段在不在），不是「有没有值」。
    # 拿 ⬜ 去催一个已经明确说「不要称呼」的人，是这个项目最不该有的那种噪音。
    #
    # 但「字段在不在」不足以下结论：手改成 {"formal": "王先生"} 时字段在、
    # 解析结果是空，照这个判据会打出「✅ 称呼：不要称呼（已确认）」——
    # 用户填了两个称呼，工具却说系统已确认他不要称呼。忌口那一层的纪律
    # （解析失败要红着脸报，不许打绿勾）在这里同样适用。
    asked_address = "address" in p
    addrs = _persona.addresses()
    addr_warn = _persona.address_warning()
    if addr_warn:
        lines.append(f"  ❌ 称呼：**没有生效** —— {addr_warn}")
    else:
        lines.append(f"  {mark(asked_address)} 称呼："
                     + ("、".join(addrs) if addrs
                        else ("不要称呼（已确认）" if asked_address else "没问过"))
                     + "　← address")
    for w in _persona.warnings():
        if w == addr_warn:
            continue          # 上面已经红着脸报过了，别再打一遍
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

    tp = _plan.current()
    configured = bool(p.get("training"))
    drift = _plan.frequency_drift(actual_days_per_week(), plan=tp)
    freq = f"{tp.days_per_week} 次/周" if tp.days_per_week else "未设"
    dur = f"{tp.session_minutes} 分钟/次" if tp.session_minutes else "未设"
    cap = f"（≈ {tp.session_set_cap} 组）" if tp.session_set_cap else ""
    lines.append(f"  {mark(bool(tp.days_per_week and tp.session_minutes))} "
                 f"训练计划：{freq}、{dur}{cap}、{tp.focus}　← training")
    # 周起始日是全局口径，改了会让新旧周报对不上，所以永远打出当前值 ——
    # 这不是「缺了什么」，是「你现在按哪个口径在算」。
    if drift:
        lines.append(f"      ℹ️  {drift}")
    lines.append(f"  {mark(configured)} 周起始日：{tp.week_start}"
                 + ("" if configured else "（默认值，没设）")
                 + "　← training.week_start　周报分桶 / 骰子每周黄灯都按它切")

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

    if (not p.get("sex") or phase_defaulted or walls.warn or diet.get("avoid")
            or not p.get("training")):
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
