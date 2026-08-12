"""手记训练日志：速记文本 → 规范化会话。

这是**没有训记的用户的一等公民路径**，不是降级方案。解析出来的数据
和训记同步来的走完全相同的分析流程。

分工很关键：
  · 自然语言 →速记文本  由**模型**做（模型擅长，且结果会展示给用户确认）
  · 速记文本 → 结构化数据  由**这个解析器**做（确定性、有单元测试）

模型不参与数据解析。这样「今天卧推六十公斤四组」变成 `60x10x4` 这一步出错时
用户一眼能看见，而不是悄悄写进数据库。

速记语法
--------
    # 2026-07-26 推日 19:05-20:20
    杠铃卧推 ~40x10 60x10 62.5x8 62.5x8@8
    上斜哑铃卧推 22.5x12x3          # 一个重量做三组
    双杠臂屈伸 BWx12 BW+10x8
    平板支撑 T:60s x3
    保加利亚分腿蹲 L:20x10 R:22.5x10
    > 睡眠不足，状态一般

    W x R        一组
    W x R x N    同样的 N 组
    ~            热身组，不计入有效容量
    @8 / @8.5    该组的 RPE
    BW / 自重     自重；BW+10 表示自重加 10kg
    T:60s        计时组
    L:.. R:..    单侧，分别记左右
    #            注释。标题行是**动作之前第一条带日期或起止时间的 #**（日期
                 名字 起止时间三样都可省，但只写名字的话仅限文件第一行）；
                 其余 # 无论整行还是行尾都只当注释
    >            整次训练的备注，可以写多行

哑铃动作按**单只手**的重量写（一手 12kg 的哑铃卧推写 `12x10`，不写 24）。

**解析器不会替你乘二，也不该乘。** 它只如实存下你写的数 ——
「一只手 12kg」到「这一组等效负荷多少」的换算属于分析层，因为那要看动作类型：
两个哑铃同时推是 ×2，单臂划船和酒杯深蹲是 ×1。见
`knowledge/movements/implement-loading.json`。

⚠️ 单侧动作要用 `L: R:` 写，才会被标成 `unilateral`。**只写一个重量的哑铃动作
现在拿不到 `×2`** —— 同一次训练手记进来和训记同步进来，吨位会差一倍。
这是已知缺口，别拿两条路径的吨位互相比。

可以直接跑的样例见 `examples/workout.txt`；
体重、围度、饮食这些不走这里，格式见 `examples/README.md`。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field

HEADER_RE = re.compile(
    r"^#\s*(\d{4}-\d{2}-\d{2})?\s*(.*?)\s*"
    r"(?:(\d{1,2}:\d{2})\s*[-–~]\s*(\d{1,2}:\d{2}))?\s*$")
SET_RE = re.compile(
    r"^(?P<warm>~)?"
    r"(?:(?P<side>[LR左右])[:：])?"
    r"(?P<weight>BW|自重|\d+(?:\.\d+)?)"
    r"(?:\+(?P<add>\d+(?:\.\d+)?))?"
    r"(?:[xX*×](?P<reps>\d+))?"
    r"(?:[xX*×](?P<count>\d+))?"
    r"(?:@(?P<rpe>\d+(?:\.\d+)?))?$")
TIME_RE = re.compile(
    r"^[Tt][:：](?P<sec>\d+(?:\.\d+)?)(?P<unit>s|秒|m|分)?"
    r"(?:\s*[xX*×](?P<count>\d+))?$")
# 独立的「x3」token：把上一组再重复到共 3 组。
# 「平板支撑 T:60s x3」这种写法很自然，不该报看不懂。
REPEAT_RE = re.compile(r"^[xX*×](\d+)$")


@dataclass
class ParseIssue:
    line_no: int
    severity: str      # error | warning
    text: str
    suggestion: str | None = None

    def __str__(self) -> str:
        s = f"第 {self.line_no} 行 [{self.severity}] {self.text}"
        return s + (f" —— {self.suggestion}" if self.suggestion else "")


@dataclass
class ParseResult:
    session: dict | None
    issues: list[ParseIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.session is not None and not any(
            i.severity == "error" for i in self.issues)


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _mk_set(*, weight=None, left=None, reps=None, time_s=None, rpe=None,
            self_weight=False, warmup=False) -> dict:
    return {
        "done": True,
        "kind": "warmup" if warmup else "work",
        "weight_kg": weight,
        "left_weight_kg": left,
        "reps": reps,
        "time_s": time_s,
        "rpe": rpe,
        "self_weight": self_weight,
    }


def _parse_movement_line(line: str, line_no: int,
                         issues: list[ParseIssue]) -> dict | None:
    """一行 = 一个动作。第一个 token 之前的部分是动作名。"""
    tokens = line.split()
    if not tokens:
        return None

    # 从后往前找出所有能解析成组的 token，剩下的拼成动作名
    first_set_idx = len(tokens)
    for i, tok in enumerate(tokens):
        if SET_RE.match(tok) or TIME_RE.match(tok):
            first_set_idx = i
            break

    name = " ".join(tokens[:first_set_idx]).strip()
    set_tokens = tokens[first_set_idx:]

    if not name:
        issues.append(ParseIssue(line_no, "error", f"这行找不到动作名：{line!r}",
                                 "格式是「动作名 重量x次数」，比如「杠铃卧推 60x10」"))
        return None
    if not set_tokens:
        issues.append(ParseIssue(line_no, "error", f"「{name}」后面没有组数",
                                 "比如「杠铃卧推 60x10 60x8」"))
        return None

    sets: list[dict] = []
    pending_side: dict[str, float] = {}
    self_weight_used = False

    for tok in set_tokens:
        rm = REPEAT_RE.match(tok)
        if rm and sets:
            target = int(rm.group(1))
            last = sets[-1]
            while len(sets) < target:
                sets.append(dict(last))
            continue

        tm = TIME_RE.match(tok)
        if tm:
            secs = _num(tm.group("sec")) or 0
            if tm.group("unit") in ("m", "分"):
                secs *= 60
            for _ in range(int(tm.group("count") or 1)):
                sets.append(_mk_set(time_s=secs, self_weight=True))
            self_weight_used = True
            continue

        m = SET_RE.match(tok)
        if not m:
            issues.append(ParseIssue(
                line_no, "warning", f"看不懂「{tok}」，已跳过",
                "组的写法是 重量x次数，比如 60x10；重复组用 60x10x3"))
            continue

        raw_w = m.group("weight")
        is_bw = raw_w in ("BW", "自重")
        weight = None if is_bw else _num(raw_w)
        add = _num(m.group("add"))
        if is_bw:
            self_weight_used = True
            weight = add           # 自重之外额外挂的重量
        elif add:
            weight = (weight or 0) + add

        reps = _num(m.group("reps"))
        rpe = _num(m.group("rpe"))
        side = m.group("side")

        if side:
            # L:20x10 R:22.5x10 —— 攒够一对再合成一组
            key = "L" if side in ("L", "左") else "R"
            pending_side[key] = weight or 0
            pending_side[f"{key}_reps"] = reps or 0
            pending_side[f"{key}_rpe"] = rpe
            if "L" in pending_side and "R" in pending_side:
                sets.append(_mk_set(
                    weight=pending_side["R"], left=pending_side["L"],
                    reps=pending_side.get("R_reps") or pending_side.get("L_reps"),
                    rpe=pending_side.get("R_rpe") or pending_side.get("L_rpe"),
                    self_weight=is_bw))
                pending_side = {}
            continue

        count = int(m.group("count") or 1)
        for _ in range(count):
            sets.append(_mk_set(weight=weight, reps=reps, rpe=rpe,
                                self_weight=is_bw,
                                warmup=bool(m.group("warm"))))

    if pending_side:
        issues.append(ParseIssue(
            line_no, "warning", f"「{name}」有单侧记录没有配对，已忽略",
            "左右都要写，比如 L:20x10 R:20x10"))

    if not sets:
        return None

    unilateral = any(s.get("left_weight_kg") is not None for s in sets)
    return {
        "index": None,
        "name": name,
        "raw_type": None,          # 手记没有肌群字段，交给分类器
        "exetype": "times" if self_weight_used else None,
        "unilateral": unilateral,
        "difficulty": None,
        "note": None,
        "truncated": False,
        "sets": sets,
    }


def _titled_header(line: str) -> re.Match | None:
    """一行 `#` 能不能算标题行 —— 判据是它**真的解析出了日期或起止时间**。

    为什么要这道门槛：`HEADER_RE` 每个分组都可选，几乎任何 `#` 开头的行都能匹配，
    单靠它分不出标题和注释。而「# 补记：忘了当天记」这种注释在速记里再常见不过，
    被当成标题的后果是日期悄悄变成今天 —— 训练落进错误那一天、出现在错误那一周的
    报告里，还没法和训记记录去重，而且一句提示都没有。
    """
    m = HEADER_RE.match(line)
    if not m:
        return None
    return m if (m.group(1) or (m.group(3) and m.group(4))) else None


def _pick_header(lines: list[str]) -> int | None:
    """定标题行的行号（1 起）。两条规则，顺序有意义：

    1. **动作之前**第一条带日期或起止时间的 `#` 行 —— 那是唯一能和注释区分开的信号
    2. 都没有的话，只有当文件第一个非空行本身就是 `#` 行，才把它当纯标题行
       （`# 推日` 这种只写名字的写法是合法的，不能丢）

    必须先扫一遍再解析：「首行注释 + 第二行真标题」要让后者赢，边走边定就只能
    让第一行赢。而「后一个标题行赢」也不行 —— 那会让文件末尾随手一句
    `# 换了家健身房` 顶掉用户填的日期和标题。
    """
    first_hash: int | None = None
    for no, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s:
            continue
        if not s.startswith("#"):
            break                      # 动作/备注开始了，标题行不可能在后面
        if first_hash is None:
            # 走到这里说明文件第一个非空行就是 # —— 规则 2 的入口
            first_hash = no
        if _titled_header(s):
            return no
    return first_hash


def parse(text: str, *, default_date: dt.date | None = None) -> ParseResult:
    issues: list[ParseIssue] = []
    date = default_date or dt.date.today()
    title = ""
    start_ms = end_ms = None
    notes: list[str] = []
    movements: list[dict] = []
    lines = text.splitlines()
    header_no = _pick_header(lines)

    for line_no, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else raw.strip()
        if not line:
            continue

        if line.startswith("#"):
            # 标题行只有一条，由 `_pick_header()` 事先定好；其余 # 都是注释。
            # 两个方向的静默都不能接受：注释顶掉用户填的日期和标题，
            # 或者真正的标题行因为前面有句注释而被无视。
            if line_no != header_no:
                if _titled_header(line):
                    # 这行看着像标题（带日期或时间）却没被采用 —— 必须说出来。
                    # 静默忽略和静默覆盖一样糟：用户会以为这个日期生效了。
                    where = (f"标题行是第 {header_no} 行" if header_no
                             else f"标题行只能写在动作之前，日期用的是 {date.isoformat()}")
                    issues.append(ParseIssue(
                        line_no, "warning",
                        f"这行也带日期或时间，但只当注释处理（{where}）",
                        "一次训练只有一个标题行；要改日期就改标题行"))
                continue
            m = HEADER_RE.match(line)
            if m:
                if m.group(1):
                    try:
                        date = dt.date.fromisoformat(m.group(1))
                    except ValueError:
                        issues.append(ParseIssue(line_no, "warning",
                                                 f"日期看不懂：{m.group(1)}"))
                title = (m.group(2) or "").strip()
                if m.group(3) and m.group(4):
                    try:
                        h1, m1 = map(int, m.group(3).split(":"))
                        h2, m2 = map(int, m.group(4).split(":"))
                        s = dt.datetime.combine(date, dt.time(h1, m1))
                        e = dt.datetime.combine(date, dt.time(h2, m2))
                        if e < s:
                            e += dt.timedelta(days=1)
                        start_ms = int(s.timestamp() * 1000)
                        end_ms = int(e.timestamp() * 1000)
                    except ValueError:
                        issues.append(ParseIssue(line_no, "warning", "时间格式看不懂"))
            continue

        if line.startswith(">"):
            notes.append(line[1:].strip())
            continue

        mv = _parse_movement_line(line, line_no, issues)
        if mv:
            mv["index"] = len(movements) + 1
            movements.append(mv)

    if not movements:
        issues.append(ParseIssue(0, "error", "没有解析出任何动作"))
        return ParseResult(None, issues)

    digest = hashlib.sha1(
        (date.isoformat() + "|" +
         "|".join(f"{m['name']}:{len(m['sets'])}" for m in movements)
         ).encode("utf-8")).hexdigest()[:8]

    duration_s = ((end_ms - start_ms) // 1000) if (start_ms and end_ms) else None

    session = {
        "schema": "ha.session/1",
        "id": f"manual:{date.isoformat()}:{digest}",
        "source": "manual",
        "date": date.isoformat(),
        "title": title,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_s": duration_s,
        "note": " ".join(notes),
        "kcal": None,
        "truncated": False,
        "movements": movements,
    }
    return ParseResult(session, issues)


def summarize(session: dict) -> str:
    """给用户确认用的摘要。写回前必须展示。"""
    lines = [f"日期：{session['date']}"
             + (f"　标题：{session['title']}" if session["title"] else "")]
    if session.get("duration_s"):
        lines.append(f"时长：{session['duration_s'] // 60} 分钟")
    lines.append("")
    for m in session["movements"]:
        work = [s for s in m["sets"] if s["kind"] != "warmup"]
        warm = [s for s in m["sets"] if s["kind"] == "warmup"]
        bits = []
        for s in work:
            if s.get("time_s"):
                bits.append(f"{s['time_s']:.0f}s")
            else:
                w = s.get("weight_kg")
                left = s.get("left_weight_kg")
                if s["self_weight"]:
                    # 自重加负重要显示成「自重+10」，只显示 10 会看不出是自重动作
                    wtxt = "自重" if w is None else f"自重+{w:g}"
                else:
                    wtxt = f"{w:g}" if w is not None else "?"
                if left is not None and left != w:
                    wtxt = f"左{left:g}/右{w:g}"
                bits.append(f"{wtxt}×{s['reps']:.0f}"
                            + (f"@{s['rpe']:g}" if s.get("rpe") else ""))
        uni = "（单侧）" if m["unilateral"] else ""
        extra = f"　热身 {len(warm)} 组" if warm else ""
        lines.append(f"  {m['name']}{uni}　{len(work)} 组：{' '.join(bits)}{extra}")
    if session.get("note"):
        lines.append(f"\n备注：{session['note']}")
    return "\n".join(lines)


def dedupe_against(session: dict, existing: list[dict]) -> dict | None:
    """检查是否和训记同步来的记录重复。

    重复判据：同一天，且动作名集合的 Jaccard 相似度 ≥ 0.5。
    训记的记录优先 —— 但**绝不删除**手记，只标记为被取代。
    """
    same_day = [s for s in existing
                if s["date"] == session["date"] and s.get("source") == "xunji"]
    if not same_day:
        return None
    mine = {m["name"] for m in session["movements"]}
    for other in same_day:
        theirs = {m["name"] for m in other.get("movements", [])}
        if not mine or not theirs:
            continue
        jaccard = len(mine & theirs) / len(mine | theirs)
        if jaccard >= 0.5:
            return other
    return None
