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
    for fname, desc in (
        ("persona.md", "教练人格"),
        ("safety-boundaries.md", "医疗安全边界"),
        ("movement-taxonomy.json", "动作肌群表"),
        ("movement-patterns.json", "动作模式表"),
        ("pattern-balance.json", "结构平衡规则"),
        ("training-landmarks.json", "训练量参考区间"),
        ("load-measurement.md", "负荷计量规程（绳索传动比）"),
    ):
        p = KNOWLEDGE_DIR / fname
        mark = OK if p.exists() else WARN
        lines.append(f"  {mark.rstrip()} {desc}{'' if p.exists() else '  ← 缺失'}")

    # 语气层单独查。核心在、语气文件丢了，教练还能用但会变得干巴 ——
    # 这种「降级但没坏」的状态必须说出来，否则用户只会觉得「换了没反应」。
    from . import persona as _persona
    missing_tones = [s for s in _persona.TONES if not _persona.tone_path(s).exists()]
    tone_mark = OK if not missing_tones else WARN
    lines.append(f"  {tone_mark.rstrip()} 语气层 {len(_persona.TONES) - len(missing_tones)}"
                 f"/{len(_persona.TONES)} 份"
                 + (f"　← 缺 {'、'.join(missing_tones)}" if missing_tones else "")
                 + f"　当前：{_persona.label(_persona.current())}")

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
