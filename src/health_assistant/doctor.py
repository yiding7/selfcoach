"""环境体检。

原则：**缺少可选能力不是错误。** 退出码非零只留给真正的故障
（数据目录不可写、凭证无效、存储损坏）。没配训记 key、没接模型、
没有生图能力，都是正常状态，打印升级路径，退出 0。
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import store
from .config import (CREDENTIALS, DATA_DIR, KNOWLEDGE_DIR, ROOT, ensure_dirs,
                     get_key, load_env)

OK, WARN, BAD, INFO = "✅", "⚠️ ", "❌", "· "


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:6]}…{key[-4:]}"


# agent 宿主目录 → 人话名字。install.sh 往这些位置铺软链，skills/ 是唯一真相源。
SKILL_HOSTS = ((".claude/skills", "Claude Code 项目级"),
               (".codex/skills", "Codex"))


def _check_skill_links() -> list[str]:
    """查 agent 宿主目录里的 skill 软链：断没断、缺没缺、有没有留下孤儿。

    为什么值得单独查：断链的报错出现在 **agent 那一侧**
    （「Unknown skill: health-coach」），跟这个仓库看不出关系。
    而成因往往是仓库改了名或搬了家 —— 绝对软链把旧路径烧死在里面了。

    **遍历宿主目录里实际存在的软链，不是 skills/ 里现存的名字。**
    只按 skills/ 的名字去看的话，改名留下的孤儿死链永远碰不到：把
    skills/xunji-sync 改成 sync-xunji 再跑 install.sh，新链建好了，宿主里那条
    旧链还指着已经不存在的目录，而 doctor 会一边打「7/7 已链接」一边让 agent
    读不到那份 skill —— 拿着一片绿的体检报告去查故障是最费时间的一种。

    缺链和死链一样致命，所以「只链上一部分」和「目录在但一个都没链」都要如实报，
    不能打绿勾。但宿主目录**不存在**时保持沉默：没链过不是问题，
    手记模式和 hc 命令都不依赖 skill，在体检里制造噪音是负价值。
    """
    out: list[str] = []
    src_names = {p.name for p in (ROOT / "skills").glob("*/")}
    total = len(src_names)
    for rel, human in SKILL_HOSTS:
        host = ROOT / rel
        if not host.is_dir():
            continue
        links = sorted(p.name for p in host.iterdir() if p.is_symlink())
        # 手工拷进去的真实目录：能用，但不会跟着仓库更新。不算死链。
        copies = sorted(n for n in src_names
                        if (host / n).exists() and not (host / n).is_symlink())
        if not links and not copies:
            out.append(f"  {BAD} {rel}（{human}）：目录在，但一个 skill 都没链上"
                       f"（0/{total}）")
            out.append("      多半是 ./install.sh 没跑完，或这个卷不支持软链"
                       "（exFAT / SMB / 某些容器挂载）。"
                       "agent 那边会报「Unknown skill: health-coach」")
            continue

        dead = [n for n in links if not (host / n).exists()]
        good = [n for n in links if n in src_names and (host / n).exists()]
        absolute = [n for n in good if Path((host / n).readlink()).is_absolute()]
        missing = sorted(src_names - set(links) - set(copies))

        notes: list[str] = []
        # 死链分两种，修法不同：名字还在 skills/ 里的重建，已经没了的直接删。
        dead_known = [n for n in dead if n in src_names]
        dead_gone = [n for n in dead if n not in src_names]
        if dead_known:
            notes.append(f"      {BAD} {len(dead_known)} 个死链"
                         f"（{'、'.join(dead_known)}）—— 指向的目标不存在，"
                         f"多半是项目改过名或搬过家。跑 `./install.sh` 重建")
        if dead_gone:
            notes.append(f"      {BAD} {len(dead_gone)} 个孤儿死链"
                         f"（{'、'.join(dead_gone)}）—— skills/ 里已经没有这个名字，"
                         f"是改名或删除留下的。删掉它：rm {rel}/{dead_gone[0]}")
        live_orphan = [n for n in links if n not in src_names and n not in dead]
        if live_orphan:
            notes.append(f"      {WARN.rstrip()} {len(live_orphan)} 个链不属于本仓库的 "
                         f"skills/（{'、'.join(live_orphan)}）—— 还能读到，"
                         f"但已经不是这个项目在维护的了")
        if missing:
            notes.append(f"      {WARN.rstrip()} 缺 {len(missing)} 个"
                         f"（{'、'.join(missing)}）—— agent 用不到这几份，"
                         f"跑 `./install.sh` 补齐")
        if absolute:
            # 还没坏，但下一次改名就会坏。说出来，不打红叉。
            notes.append(f"      {WARN.rstrip()} {len(absolute)}/{len(good)} 个是绝对路径软链，"
                         f"项目一改名就会断。跑 `./install.sh` 换成相对路径")
        if copies:
            notes.append(f"      {WARN.rstrip()} {len(copies)} 个是手工拷进去的目录"
                         f"（{'、'.join(copies)}）—— 能用，但不会跟着仓库更新")

        mark = BAD if dead else (OK if not notes else WARN.rstrip())
        out.append(f"  {mark} {rel}（{human}）：{len(good)}/{total} 个 skill 已链接")
        out.extend(notes)
    return out


def check(*, verbose: bool = False) -> int:
    load_env()
    lines: list[str] = []
    fatal = 0        # 工具跑不起来 —— 退出码非 0
    # 工具能跑，但有等着你拍板的事。**不能被「✅ 一切正常」盖过去** ——
    # 那正是这个项目在忌口那一层明令禁止的「打绿勾糊弄过去」。
    # 但也不该让退出码非 0：待处理不是故障。
    todo: list[str] = []

    lines.append("训记健康助手 · 环境体检")
    lines.append("=" * 52)

    # ── 运行时 ──
    v = sys.version_info
    py_ok = v >= (3, 11)
    lines.append(f"\n{'运行时':─<20}")
    lines.append(f"  {OK if py_ok else BAD} Python {v.major}.{v.minor}.{v.micro}"
                 f"{'' if py_ok else '  ← 需要 3.11+'}")
    if not py_ok:
        fatal += 1
    lines.append(f"  {OK} 第三方依赖：0 个（全部使用标准库）")

    # ── 路径 ──
    lines.append(f"\n{'路径':─<20}")
    lines.append(f"  {INFO}仓库根   {ROOT}")
    lines.append(f"  {INFO}数据目录 {DATA_DIR}")
    try:
        ensure_dirs()
        store.init()
        probe = DATA_DIR / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        lines.append(f"  {OK} 数据目录可写")
    except OSError as e:
        lines.append(f"  {BAD} 数据目录不可写：{e}")
        fatal += 1

    # jsonl 是人真的会去手改的格式：一行一条、没有缩进、逗号引号全靠自己数。
    # 而 `read_jsonl()` 遇到坏行是**跳过**的 —— 手滑打错一个引号，那条规则
    # 就悄悄消失了：没有报错，只是折算不再发生。这里把它变成一句人话。
    broken = []
    for path in sorted(DATA_DIR.rglob("*.jsonl")) + sorted(
            (ROOT / "profile" / "coach-journal").glob("*.jsonl")):
        for lineno, snippet in store.bad_jsonl_lines(path):
            broken.append((path, lineno, snippet))
    if broken:
        for path, lineno, snippet in broken[:8]:
            rel = path.relative_to(ROOT) if ROOT in path.parents else path
            msg = f"{rel} 第 {lineno} 行不是合法 JSON，**这一行被静默跳过了**"
            lines.append(f"  {BAD} {msg}")
            lines.append(f"      {snippet}")
            # 算 fatal 而不是 todo：这不是「等你拍板」，是数据正在被丢掉。
            # 退出码非 0，脚本和 hook 都能拦住。
            fatal += 1
        if len(broken) > 8:
            lines.append(f"      …… 还有 {len(broken) - 8} 行")
    else:
        lines.append(f"  {OK} 全部 jsonl 逐行可解析")

    # ── 凭证 ──
    lines.append(f"\n{'训记凭证':─<20}")
    present = 0
    for name, (env_var, desc) in CREDENTIALS.items():
        key = get_key(name)
        if key:
            present += 1
            lines.append(f"  {OK} {desc}")
            if verbose:
                lines.append(f"       {env_var} = {mask(key)}")
        else:
            lines.append(f"  {WARN}{desc} —— 未配置（{env_var}）")

    if present == 0:
        lines.append("")
        lines.append("  ℹ️  没有配置任何训记凭证 —— 这完全没问题。")
        lines.append("     工具会以「手记模式」运行，功能完全一样：")
        lines.append("       hc log add          记录一次训练")
        lines.append("       hc report weekly    出周报")
        lines.append("     想接入训记：在 App 里申请 key（需会员），填进 .env")
        lines.append("     参考 .env.example")
    elif present < len(CREDENTIALS):
        lines.append(f"\n  ℹ️  已配置 {present}/{len(CREDENTIALS)} 个凭证。"
                     f"缺失的那部分功能不可用，其余不受影响。")

    # ── 本地数据 ──
    lines.append(f"\n{'本地数据':─<20}")
    sessions = store.load_sessions()
    body = store.load_body()
    meals = store.load_meals()
    raw_dates = store.raw_training_dates()

    if sessions:
        dates = sorted({s["date"] for s in sessions})
        n_moves = sum(len(s.get("movements") or []) for s in sessions)
        lines.append(f"  {OK} 训练 {len(sessions)} 次 / {n_moves} 个动作"
                     f"（{dates[0]} ~ {dates[-1]}）")
    else:
        lines.append(f"  {INFO}训练记录：0 —— 跑 `hc sync` 或 `hc log add` 开始")
    lines.append(f"  {INFO}原始缓存：{len(raw_dates)} 天"
                 f"（可用 `hc rebuild` 离线重算，不消耗任何请求）")

    # 负荷口径。⚠️ 这一条要报「待处理」而不是打绿勾 —— 一个没人看过的
    # 负荷跳变会让 hc compare 输出「1RM ↓50%」这种既错误又打击人的结论。
    if sessions:
        from . import calibration
        from .analytics.metrics import session_stats, weight_at
        stats = [session_stats(s, weight_at(body, s["date"])) for s in sessions]
        rules = calibration.load_rules()
        pending = calibration.unresolved(stats, rules)
        if pending:
            names = "、".join(sorted({j.movement for j in pending}))
            lines.append(f"  {WARN} 负荷口径：{len(pending)} 处跳变待处理"
                         f"（{names}）—— 跑 `hc calib` 逐条处置")
            todo.append(f"{len(pending)} 处负荷跳变待处置 —— `hc calib`")
        elif rules:
            lines.append(f"  {OK} 负荷口径：{len(rules)} 条规则生效，无待处理跳变")
        else:
            lines.append(f"  {OK} 负荷口径：无可疑跳变")

    if body:
        w = [r for r in body if r["type"] == "weight"]
        if w:
            lines.append(f"  {OK} 身体数据 {len(body)} 条，最新体重 "
                         f"{w[-1]['value']} kg（{w[-1]['date']}）")
        else:
            lines.append(f"  {OK} 身体数据 {len(body)} 条")
    else:
        lines.append(f"  {INFO}身体数据：0")

    lines.append(f"  {INFO}饮食记录：{len(meals)} 条"
                 + ("" if meals else " —— 可以直接口述，助手会帮你记"))

    # ── 知识库 ──
    lines.append(f"\n{'知识库（通用，可分享）':─<16}")
    for rel, desc in (
        ("coach/persona.md", "教练人格"),
        ("coach/safety-boundaries.md", "医疗安全边界"),
        ("movements/movement-taxonomy.json", "动作肌群表"),
        ("movements/movement-patterns.json", "动作模式表"),
        ("movements/pattern-balance.json", "结构平衡规则"),
        ("training/training-landmarks.json", "训练量参考区间"),
        ("measurement/load-measurement.md", "负荷计量规程（绳索传动比）"),
        ("movements/implement-loading.json", "器械计量口径表"),
        ("movements/site-dependence.json", "场地依赖性表（换馆能不能比）"),
    ):
        p = KNOWLEDGE_DIR / rel
        mark = OK if p.exists() else WARN
        lines.append(f"  {mark.rstrip()} {desc}{'' if p.exists() else '  ← 缺失'}")

    # 这两张表**坏掉的样子是没有样子**：口径表坏了，双哑铃动作的吨位静默少算
    # 一半；场地表坏了，换馆之后连深蹲的进步也被藏起来。屏幕上一个字的错都没有。
    #
    # `loading.warnings()` 的 docstring 从第一天就写着「给 hc doctor 用」，
    # 但一直没人调它 —— 一个为了防静默失效而写的函数，自己静默失效了。
    # 2026-08-23 接上。
    from . import gyms as _gyms
    from . import loading as _loading
    for w in _loading.warnings() + _gyms.warnings():
        lines.append(f"  {BAD} {w}")
        todo.append(w)

    # 语气层单独查。核心在、语气文件丢了，教练还能用但会变得干巴 ——
    # 这种「降级但没坏」的状态必须说出来，否则用户只会觉得「换了没反应」。
    from . import persona as _persona
    missing_tones = [s for s in _persona.TONES if not _persona.tone_path(s).exists()]
    tone_mark = OK if not missing_tones else WARN
    lines.append(f"  {tone_mark.rstrip()} 语气层 {len(_persona.TONES) - len(missing_tones)}"
                 f"/{len(_persona.TONES)} 份"
                 + (f"　← 缺 {'、'.join(missing_tones)}" if missing_tones else "")
                 + f"　当前：{_persona.label(_persona.current())}")

    # ── skill 软链 ──
    # 断链的症状出现在 agent 那一侧（「Unknown skill: health-coach」），
    # 跟这个仓库看不出任何关系，所以必须在这里主动查一次。
    # 最常见的成因是项目改名或搬家把绝对软链指瞎了 —— 2026-08-11 踩过。
    skill_lines = _check_skill_links()
    if skill_lines:
        lines.append(f"\n{'技能软链（agent 宿主）':─<17}")
        lines += skill_lines
        if any(BAD in ln for ln in skill_lines):
            todo.append("skill 软链断了 —— 跑一次 `./install.sh` 重建")
        elif any("install.sh" in ln for ln in skill_lines):
            # 缺链和死链一样致命：agent 那边同样是「Unknown skill」。
            # 只在报告里写一行、不进 todo，等于被后面的「一切正常」盖过去。
            todo.append("skill 软链不全 —— 跑一次 `./install.sh` 补齐")

    # profile/ 是个人隐私，不进版本库。缺了不是错误，只是助手会少一些上下文。
    lines.append(f"\n{'个人档案（私密，不进版本库）':─<14}")
    profile_dir = ROOT / "profile"
    for fname, desc in (
        ("personal-context.md", "个人档案（病史/用药/偏好）"),
        ("food-traffic-light.md", "个人化红黄绿灯"),
    ):
        p = profile_dir / fname
        if p.exists():
            lines.append(f"  {OK} {desc}")
        else:
            lines.append(f"  {INFO}{desc} —— 未填写")
    if not (profile_dir / "personal-context.md").exists():
        lines.append("     照着 profile/personal-context.example.md 填一份，"
                     "助手的建议会贴合得多")

    # ── 需要用户自己填的部分 ──
    # 单独一节，因为这些是「工具帮不了你、只能你自己给」的东西。
    # 它们散在几个文件里各有道理，但散落本身就是个问题，所以这里集中体检一次。
    lines.append(f"\n{'该你填的（hc setup 可一次填完）':─<12}")
    from . import setup as setup_mod
    lines += setup_mod.render_checklist()

    lines.append("")
    lines.append("=" * 52)
    if fatal:
        lines.append(f"{BAD} {fatal} 个问题需要处理")
    elif todo:
        lines.append(f"{OK} 环境正常，但有 {len(todo)} 件事等你拍板：")
        for t in todo:
            lines.append(f"   · {t}")
    else:
        lines.append(f"{OK} 一切正常")

    print("\n".join(lines))
    return 1 if fatal else 0
