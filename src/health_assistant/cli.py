"""hc —— 健康助手命令行。

设计约束：所有子命令都必须在**没有模型**的情况下产出完整、可用的结果。
模型只在 skill 层介入，负责把这里算出来的结论讲得好听。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import store
from .config import load_env


def _parse_since(value: str, today: dt.date) -> dt.date:
    """接受 '2026-07-01' 或 '30d' / '12w' / '6m' 这种相对写法。"""
    v = value.strip().lower()
    if v in ("today", "今天"):
        return today
    if v.endswith("d") and v[:-1].isdigit():
        return today - dt.timedelta(days=int(v[:-1]))
    if v.endswith("w") and v[:-1].isdigit():
        return today - dt.timedelta(weeks=int(v[:-1]))
    if v.endswith("m") and v[:-1].isdigit():
        return today - dt.timedelta(days=30 * int(v[:-1]))
    if v.endswith("y") and v[:-1].isdigit():
        return today - dt.timedelta(days=365 * int(v[:-1]))
    return dt.date.fromisoformat(value)


def cmd_doctor(args) -> int:
    from .doctor import check
    return check(verbose=args.verbose)


def cmd_sync(args) -> int:
    from .xunji.client import XunjiClient, XunjiError
    from .xunji import sync as sync_mod

    load_env()
    today = dt.date.today()
    end = dt.date.fromisoformat(args.until) if args.until else today
    start = _parse_since(args.since, today) if args.since else end - dt.timedelta(days=89)

    # 只在交互式终端里画等待指示器。重定向到文件或 cron 时保持日志干净。
    tty = sys.stderr.isatty()

    def on_wait(seconds: float) -> None:
        if tty and seconds >= 3:
            sys.stderr.write(f"\r  ⏳ 限频等待 {seconds:4.0f}s …")
            sys.stderr.flush()

    def log(msg: str) -> None:
        if tty:
            sys.stderr.write("\r" + " " * 32 + "\r")
        print(msg, flush=True)

    client = XunjiClient(on_wait=on_wait)
    what = args.what or "all"
    rc = 0

    if what in ("all", "body"):
        print(f"\n身体数据 {start} ~ {end}")
        try:
            sync_mod.sync_body(client, start, end, log=log)
        except XunjiError as e:
            print(f"  跳过：{e}")
            rc = rc or (1 if e.kind not in ("auth", "network") else 0)

    if what in ("all", "food"):
        print(f"\n饮食记录 {start} ~ {end}")
        try:
            sync_mod.sync_food(client, start, end, log=log)
        except XunjiError as e:
            print(f"  跳过：{e}")

    if what in ("all", "train"):
        print(f"\n训练记录 {start} ~ {end}")
        try:
            res = sync_mod.sync_training(
                client, start, end,
                full=not args.light, force=args.force,
                budget_s=args.budget_minutes * 60 if args.budget_minutes else None,
                max_requests=args.max_requests,
                stop_after_empty_streak=args.stop_after_empty_streak,
                log=log)
            print(f"\n{res.summary()}")
            for err in res.errors[:5]:
                print(f"  ✗ {err}")
        except KeyboardInterrupt:
            print("\n\n已中断。进度已保存，下次运行会从中断处继续。")
            return 130
        except XunjiError as e:
            print(f"  跳过：{e}")

    return rc


def cmd_import_health(args) -> int:
    """导入苹果健康导出文件。"""
    import pathlib as _p

    from . import apple_health as AH

    path = _p.Path(args.path).expanduser()
    if not path.exists():
        print(f"找不到 {path}")
        print()
        print("怎么拿到这个文件：")
        print("  iPhone 健康 App → 右上角头像 → 拉到最下面「导出所有健康数据」")
        print("  → 生成「导出.zip」→ 传到电脑上，把路径给我")
        print()
        print("  hc import-health ~/Downloads/导出.zip")
        return 1

    print(f"导入 {path}")
    try:
        data = AH.parse(path, since=args.since)
    except (FileNotFoundError, OSError) as e:
        print(f"  读取失败：{e}")
        return 1

    if not data:
        print("  没有解析出可用数据。")
        return 0

    print()
    print("  解析结果：")
    for key, daily in sorted(data.items()):
        days = sorted(daily)
        print(f"    {key:<14} {len(daily):>5} 天  {days[0]} ~ {days[-1]}")

    if args.dry_run:
        print("\n（dry-run，未写入）")
        return 0

    counts = AH.persist(data)
    print()
    for k, n in counts.items():
        print(f"  已写入 {k}: {n} 条")
    print("\n体重/体脂/腰围已合并进身体数据，`hc status` 和报告会自动用上。")
    return 0


def cmd_cardio(args) -> int:
    from .analytics import cardio as C

    hm, src = C.hr_max()
    if not hm:
        print("缺少出生年份，无法估算心率区间。填一下 data/profile.json 的 birth_year。")
        return 1

    print(f"\n最大心率 {hm:.0f} bpm（{src}）")
    print("─" * 58)
    for name, lo, hi, use in C.zone_table():
        print(f"  {name:<10} {lo:>3}–{hi:>3} bpm   {use}")

    raw = store.load_sessions(start=args.since)
    bouts = C.extract_bouts([], raw, start=args.since)
    if not bouts:
        print("\n本地没有有氧记录。")
        print("训记会把苹果健康的运动同步过来（骑行、跑步、爬楼梯等），"
              "跑 `hc sync train` 之后就能看到。")
        return 0

    print(f"\n{'实际记录':─<54}")
    for b in bouts:
        line = f"  {b.date}  {b.name:<12}"
        if b.minutes:  line += f"{b.minutes:>5.1f} 分钟"
        if b.avg_hr:   line += f"  平均 {b.avg_hr:>3.0f} bpm（{b.pct_hrmax*100:.0f}% → {b.zone}）"
        if b.max_hr:   line += f"  峰值 {b.max_hr:.0f}"
        if b.kcal:     line += f"  {b.kcal:.0f} kcal"
        print(line)

    import datetime as _dt
    week = C.summarize(bouts, _dt.date.today().isoformat(), window_days=args.window)
    print(f"\n{'过去 ' + str(args.window) + ' 天汇总':─<54}")
    print(f"  {week.bouts} 次，共 {week.total_minutes:.0f} 分钟"
          + (f"，{week.total_kcal:.0f} kcal" if week.total_kcal else ""))
    for z, mins in sorted(week.by_zone.items()):
        print(f"    {z:<10} {mins:>5.0f} 分钟")

    findings = C.evaluate(week, bouts)
    if findings:
        print(f"\n{'建议':─<54}")
        icon = {"warn": "△", "action": "→", "info": "·"}
        for f in findings:
            print(f"  {icon.get(f['kind'], '·')} {f['text']}")
            if f["fix"]:
                print(f"      {f['fix']}")
    return 0


def cmd_status(args) -> int:
    """数据新鲜度。**不联网**，助手每次对话开头跑这个。"""
    from .autosync import status_report
    print(status_report(verbose=args.verbose))
    return 0


def cmd_autosync(args) -> int:
    from . import autosync
    if args.action == "install":
        return autosync.install(interval_hours=args.interval,
                                backfill_minutes=args.backfill_minutes)
    if args.action == "uninstall":
        return autosync.uninstall()
    if args.action == "log":
        return autosync.tail_log()
    print(autosync.status_report())
    return 0


def cmd_rebuild(args) -> int:
    from .xunji.sync import rebuild
    rebuild()
    return 0


def cmd_sessions(args) -> int:
    """列出本地已有的训练，用于快速确认同步结果。"""
    today = dt.date.today()
    start = _parse_since(args.since, today).isoformat() if args.since else None
    sessions = store.load_sessions(start=start)
    if not sessions:
        print("本地还没有训练记录。跑 `hc sync` 或 `hc log add`。")
        return 0

    from .analytics.metrics import set_done

    for s in sessions:
        mins = f"{s['duration_s'] // 60} min" if s.get("duration_s") else "—"
        kcal = f" {s['kcal']:.0f} kcal" if s.get("kcal") else ""
        title = s.get("title") or "（无标题）"
        print(f"\n{s['date']}  {title}  {mins}{kcal}  [{s['source']}]")
        for m in s.get("movements") or []:
            done = [x for x in m["sets"] if set_done(x, m)]
            t = m.get("raw_type") or "?"
            uni = " 单侧" if m.get("unilateral") else ""
            ex = f" {m['exetype']}" if m.get("exetype") else ""
            print(f"    {m['name']}  [{t}]{uni}{ex}  {len(done)}/{len(m['sets'])} 组")
    print(f"\n共 {len(sessions)} 次训练。")
    return 0


def _calib_note(stats) -> None:
    """折算过的负荷必须说出来。

    `calibration` 会在读取时按口径规则改写重量，输出里的数字就和
    `data/training/` 以及训记 app 里的对不上了。**一个被悄悄改过的数字
    比一个明显错的数字危险得多** —— 用户会拿它去和 app 核对，然后
    不知道该信哪个。所以凡是展示负荷的地方，都要在末尾交代一句。
    """
    if isinstance(stats, (list, tuple)):
        sessions = stats
    else:
        sessions = [stats]
    folded = sorted({(m.name, m.calib_ratio) for s in sessions
                     for m in s.movements if m.calib_ratio})
    if not folded:
        return
    items = "、".join(f"{name} ×{ratio:g}" for name, ratio in folded)
    print(f"\n  ⚖️  上面这些重量按口径规则折算过：{items}")
    print(f"      与 data/training/ 和训记里的原始数字不同，"
          f"**原始记录未被修改**。规则见 hc calib list")


def _load_stats(since: str | None = None):
    """把本地会话和体重读出来，算成 SessionStats。"""
    from .analytics.metrics import rolling_weight, session_stats, weight_at

    sessions = store.load_sessions(start=since)
    body = store.load_body()
    trend = rolling_weight(body)
    stats = [session_stats(s, weight_at(body, s["date"])) for s in sessions]
    return stats, trend


def cmd_summary(args) -> int:
    stats, _ = _load_stats()
    if args.date:
        stats = [s for s in stats if s.date == args.date]
    else:
        stats = stats[-1:]
    if not stats:
        print("找不到对应日期的训练。用 `hc sessions` 看看本地有哪些。")
        return 0

    for st in stats:
        print(f"\n{'═' * 62}")
        print(f"{st.date}  {st.label}"
              + (f"  {st.duration_min:.0f} 分钟" if st.duration_min else "")
              + (f"  {st.kcal:.0f} kcal" if st.kcal else ""))
        print("═" * 62)
        print(f"{'动作':<22}{'部位':<6}{'组':>4}{'总次':>6}{'顶组':>9}{'容量':>10}"
              f"{'估算1RM':>10}{'难度':>6}")
        print("─" * 62)
        for m in st.movements:
            top = f"{m.top_load_kg:.1f}kg" if m.top_load_kg else "—"
            if m.sets_done == 0:
                vol = "未完成"
            elif m.volume_kg is None:
                vol = "自重"      # 自重动作且缺当日体重，无法折算
            else:
                vol = f"{m.volume_kg:.0f}kg"
            e = f"{m.best_e1rm:.1f}kg" if m.best_e1rm else "—"
            # 计时类动作的成绩是秒数：顶组=最长一组，容量位置放总时长
            if m.timed and m.best_time_s:
                top = f"{m.best_time_s:.0f}s"
                vol = f"{m.time_s_total:.0f}s"
            flag = "*" if m.group_source in ("rule", "taxonomy") else " "
            print(f"{m.name[:20]:<22}{m.group:<6}{m.sets_done:>4}"
                  f"{m.reps_total:>6.0f}{top:>9}{vol:>10}{e:>10}{flag}"
                  f"{m.difficulty or '—':>5}")
        print("─" * 62)
        vol = f"{st.volume_kg:,.0f} kg" if st.volume_kg is not None else "—"
        print(f"合计  {st.sets_done}/{st.sets_planned} 组   总容量 {vol}"
              + (f"   密度 {st.density_kg_per_min:.0f} kg/min"
                 if st.density_kg_per_min else ""))
        _calib_note(st)
        groups = "、".join(f"{g} {n:.0f}组" for g, n in
                           sorted(st.groups.items(), key=lambda kv: -kv[1]))
        print(f"部位分布  {groups}")
        print(f"强度覆盖率 {st.difficulty_coverage * 100:.0f}%"
              f"（难度标注）／ RPE {st.rpe_coverage * 100:.0f}%"
              + ("" if st.has_intensity_signal else "  ← 数据不足，强度相关结论会略过"))
        print("\n* = 部位由动作名推断（训记未返回该字段）")
    return 0


def cmd_compare(args) -> int:
    from .analytics.compare import compare_session
    from .analytics.findings import check_invariants, evaluate, split
    from .analytics.progress import (CONFIDENCE_LABEL, balance_findings,
                                     movement_progress, pattern_comparisons)

    stats, _ = _load_stats()
    if not stats:
        print("本地还没有训练记录。跑 `hc sync` 或 `hc log add`。")
        return 0

    target = next((s for s in stats if s.date == args.date), None) if args.date else stats[-1]
    if target is None:
        print(f"找不到 {args.date} 的训练。")
        return 1

    history = [s for s in stats if s.id != target.id]
    comparisons = compare_session(target, history)
    if args.group:
        comparisons = [c for c in comparisons if c.group == args.group]

    print(f"\n{target.date}  {target.label}")

    for c in comparisons:
        print(f"\n{'═' * 62}")
        print(f"「{c.group}」本次 vs 上次")
        print("═" * 62)
        print(f"  依据：{c.anchor_reason}")
        if not c.has_anchor:
            continue
        print()
        print(f"  有效组数   {c.sets.fmt('组', 0)}")
        print(f"  总容量     {c.volume.fmt('kg', 0)}")
        if c.loads_comparable:
            print(f"  顶组负荷   {c.top_load.fmt('kg')}   （仅比共同动作）")
            print(f"  最强估算1RM {c.best_e1rm.fmt('kg')}   （仅比共同动作）")
        else:
            print("  顶组负荷   —  两次没有共同动作，负荷不可比")
        paired = [m for m in c.movements if m.status == "paired"]
        if paired:
            print(f"\n  逐动作（{len(paired)} 个动作两次都做了）")
            for md in paired:
                print(f"    {md.name}")
                print(f"        顶组 {md.top_load.fmt('kg')}   "
                      f"总次数 {md.reps.fmt('次', 0)}   估算1RM {md.e1rm.fmt('kg')}")
        if c.added:
            print(f"\n  本次新增：{'、'.join(c.added)}")
        if c.dropped:
            print(f"  本次没做：{'、'.join(c.dropped)}")
        if c.excluded:
            print(f"  ⊘ 未参与对比（口径存疑）：{'、'.join(c.excluded)}"
                  f" —— 组数和容量仍照常计入")

    # 三个新视角
    mprog = movement_progress(target, history)
    pcmps = pattern_comparisons(target, history)
    if args.group:
        mprog = [m for m in mprog if m.group == args.group]
        pcmps = [p for p in pcmps if p.group == args.group]
    balance = balance_findings(stats, target.date)
    if args.group:
        balance = [b for b in balance if b.group == args.group]

    if mprog:
        print(f"\n{'═' * 62}")
        print("逐动作纵向（和这个动作自己上一次比）")
        print("═" * 62)
        for mp in sorted(mprog, key=lambda m: (m.group, m.pattern, m.name)):
            tag = CONFIDENCE_LABEL[mp.confidence]
            if mp.confidence == "none":
                print(f"  {mp.name}  [{mp.pattern}]  ← 本地历史里第一次做，暂无对比")
                continue
            src = f"{mp.last_date}"
            if mp.confidence == "variant":
                src += f" 的「{mp.matched_name}」"
            print(f"  {mp.name}  [{mp.pattern}]  vs {src}（{tag}，{mp.days_since} 天前）")
            print(f"      顶组 {mp.top_load.fmt('kg')}   总次数 {mp.reps.fmt('次', 0)}"
                  f"   估算1RM {mp.e1rm.fmt('kg')}")

    if pcmps:
        print(f"\n{'═' * 62}")
        print("按发力模式（动作换了也能比）")
        print("═" * 62)
        for pc in pcmps:
            if not pc.has_anchor:
                print(f"  {pc.group}·{pc.pattern}  ← 最近没有可比的同模式训练")
                continue
            print(f"  {pc.group}·{pc.pattern}  vs {pc.last_date}（{pc.days_since} 天前，"
                  f"{CONFIDENCE_LABEL[pc.load_confidence]}）")
            print(f"      组数 {pc.sets.fmt('组', 0)}   容量 {pc.volume.fmt('kg', 0)}")
            if pc.movements_then and set(pc.movements_then) != set(pc.movements_now):
                print(f"      上次：{'、'.join(pc.movements_then)}")
                print(f"      本次：{'、'.join(pc.movements_now)}")

    findings = evaluate(target, comparisons, movement_progress=mprog,
                        pattern_comparisons=pcmps, balance=balance)
    buckets = split(findings)

    print(f"\n{'═' * 62}")
    for label, icon in (("优点", "✓"), ("缺点", "△"), ("改进点", "→"), ("信息", "·")):
        items = buckets[label]
        if not items:
            continue
        print(f"\n{label}")
        for f in items:
            print(f"  {icon} {f.text}")

    _calib_note([target] + [s for s in history if s.date in
                            {c.anchor_date for c in comparisons}])
    _warn_load_jumps(stats, only_date=target.date)

    problems = check_invariants(findings)
    if problems:
        print("\n⚠️  结论结构自检未通过（这是工具的 bug，请反馈）：")
        for p in problems:
            print(f"     {p}")
        return 1
    return 0


def _warn_load_jumps(stats, *, only_date: str | None = None) -> int:
    """负荷跳变预警。返回待处理的条数。

    刻意放在结论之后：它不是训练结论，是**数据可信度**的问题。
    放在前面会让人以为工具在质疑他的训练，实际上工具是在质疑自己的数字。
    """
    from . import calibration

    jumps = [j for j in calibration.unresolved(stats)
             if only_date is None or j.date == only_date]
    if not jumps:
        return 0

    SHOW = 8
    print(f"\n{'═' * 62}")
    print(f"⚠️  负荷口径预警 —— {len(jumps)} 处跳变可能不是力量变化")
    print("═" * 62)

    print("\n为什么")
    for line in calibration.explanation(any(j.pulley_suspect for j in jumps)):
        print(f"  {line}")

    print("\n哪几处")
    for i, j in enumerate(jumps[:SHOW], 1):
        tag = "  [滑轮组]" if j.pulley_suspect else ""
        print(f"  {i}. {j.headline()}{tag}")
    if len(jumps) > SHOW:
        print(f"  …… 还有 {len(jumps) - SHOW} 处没列出来（`hc calib check` 看全部）")

    print("\n怎么处置 —— 四选一，每条各自决定")
    print("  1 改原始记录数据（数据源也改）")
    print("  2 只改项目内的数（原始文件不动，读取时折算）")
    print("  3 忽略这次该动作的对比（组数容量照常计入）")
    print("  4 确认是真实数据（留痕，不再预警）")
    print("\n  照抄就能跑：")
    for i, j in enumerate(jumps[:SHOW], 1):
        print(f"    # {i}. {j.movement}")
        print(f"    1: hc sync train --date {j.date} --force"
              f"      （先去训记改那天的记录）")
        print(f"    2: hc calib set '{j.movement}' --date {j.prev_date} "
              f"--ratio 0.5   （旧机位若是 2:1 就是 0.5）")
        print(f"    3: hc calib set '{j.movement}' --date {j.date} --ignore")
        print(f"    4: hc calib set '{j.movement}' --date {j.date} --confirm")
    print("\n  折算成哪个口径由你定：把**现在这台**当基准就折算旧的，"
          "反过来也行 —— 只要全序列统一。")
    return len(jumps)


def cmd_calib(args) -> int:
    """负荷口径归一化：查看跳变、写规则。"""
    from . import calibration

    if args.action == "list":
        rules = calibration.load_rules()
        if not rules:
            print("还没有任何口径规则。跑 `hc calib check` 看看有没有可疑跳变。")
            return 0
        print(f"\n生效中的口径规则（{len(rules)} 条，文件 {calibration.PATH}）")
        print("─" * 62)
        for r in rules:
            print(f"  {r.describe()}")
            if r.note:
                print(f"      {r.note}")
        print("\n规则文件只追加不修改。改主意就新写一条 --supersedes <旧ID>。")
        return 0

    if args.action == "set":
        if not (args.movement or "").strip():
            print("要指定动作名：hc calib set '<动作>' --date <日期> --ratio 0.5")
            print("看有哪些待处理：hc calib check")
            return 1
        chosen = [k for k in ("ratio", "ignore", "confirm")
                  if getattr(args, k, None)]
        if len(chosen) != 1:
            print("要且只要一个处置：--ratio <系数> / --ignore / --confirm")
            return 1
        action = "scale" if args.ratio else ("ignore" if args.ignore else "confirm")
        if args.date and (args.date_from or args.date_to):
            print("--date 和 --from/--to 不能一起用。--date 是单日的简写。")
            return 1
        lo = args.date or args.date_from
        hi = args.date or args.date_to
        try:
            rule = calibration.add_rule(
                args.movement, action, ratio=args.ratio, date_from=lo, date_to=hi,
                note=args.note or "", supersedes=args.supersedes)
        except ValueError as e:
            print(f"❌ {e}")
            return 1
        print(f"✅ 已记 {rule.describe()}")
        if action == "scale":
            print("   原始文件一个字没动 —— 折算是在读取时做的，"
                  "`hc rebuild` 也不会把它冲掉。")
        print("   核对：hc calib list　效果：hc compare")
        return 0

    # 默认：check
    stats, _ = _load_stats()
    if not stats:
        print("本地还没有训练记录。")
        return 0
    n = _warn_load_jumps(stats)
    rules = calibration.load_rules()
    if not n:
        print(f"✅ 没有待处理的负荷跳变"
              + (f"（已有 {len(rules)} 条口径规则在生效）" if rules else "") + "。")
    return 0


def cmd_next(args) -> int:
    from .analytics.compare import compare_group
    from .analytics.prescribe import prescribe_group

    stats, trend = _load_stats()
    if not stats:
        print("本地还没有训练记录。")
        return 0

    group = args.group
    target = next((s for s in reversed(stats) if s.groups.get(group, 0) >= 2), None)
    if target is None:
        print(f"本地没有练过「{group}」的记录。")
        print(f"可选部位：{'、'.join(sorted({g for s in stats for g in s.groups}))}")
        return 0

    history = [s for s in stats if s.id != target.id]
    cmp = compare_group(target, history, group)

    # 该肌群最近 7 天的总组数。
    #
    # 这里曾经取的是「target.date 所在自然周」的组数，有两个毛病：
    #   1. 算的是**上一次训练那一周**，而这条建议是给**下一次**训练的。
    #      背日周频不到 1 次时，下次训练多半落在新的一周，这个数没有意义。
    #   2. 它跟着周起始日走 —— 周日练完，按周一起算就是「本周 0 组」，
    #      恢复状态和日历怎么切没有半点关系。
    #
    # 改成滚动 7 天：问的是「这块肌肉最近七天吃了多少量」，
    # 这才是 MRV（最大可恢复容量）真正想问的问题，且与周起始日无关。
    #
    # ⚠️ 窗口必须以**今天**结尾，不是以上次训练那天结尾。
    # 一度写成 `target.date - 6 天 ~ target.date`，那等于把「上次训练那一周」
    # 换了个名字叫「最近 7 天」：上次背日在 20 天前练了 16 组的话，
    # 这个窗口照样数出 16 组、照样触发 OVER_MRV_HOLD，而那块肌肉其实
    # 已经休息了 20 天。恢复状态问的是当下，不是上次训练那会儿。
    import datetime as dt
    today = dt.date.today()
    window_start = (today - dt.timedelta(days=6)).isoformat()
    recent = sum(s.groups.get(group, 0) for s in stats
                 if window_start <= s.date <= today.isoformat())
    window_label = f"最近 7 天（{window_start} ~ {today.isoformat()}）"

    rx = prescribe_group(group, target, cmp, weekly_sets=recent,
                         window_label=window_label, body_trend=trend)

    print(f"\n{'═' * 62}")
    print(f"下次「{group}」训练建议")
    print("═" * 62)
    print(f"  基于 {target.date} 那次训练（{target.label}）")
    print(f"  {window_label}{group} 已练 {recent:.0f} 组")
    if rx.rationale:
        print(f"  生效护栏：{'、'.join(rx.rationale)}")

    for note in rx.notes:
        print(f"\n  {note}")

    print(f"\n{'动作':<24}{'组':>4}{'重量':>10}{'目标次数':>10}  {'调整'}")
    print("─" * 62)
    for p in rx.movements:
        load = f"{p.load_kg:.1f}kg" if p.load_kg is not None else "—"
        print(f"{p.name[:22]:<24}{p.sets:>4}{load:>10}{p.rep_target:>10}  {p.change}")
    print("─" * 62)
    line = f"合计 {rx.total_sets} 组"
    if rx.optional_sets:
        line += f"（另有 {rx.optional_sets} 组可选，不计入）"
    print(line)

    # 单次时长预算：只提示，不截断。
    # 用户 2026-08-10 明确选了「自己排训练内容」而不是「让脚本加硬上限」，
    # 所以这里把预算和差额摆出来，砍哪几组由他决定。
    from . import plan as _plan
    cap = _plan.current().session_set_cap
    if cap:
        mins = _plan.current().session_minutes
        if rx.total_sets > cap:
            print(f"⚠️  你的单次预算是 {mins} 分钟 ≈ {cap} 组（所有部位合计），"
                  f"光「{group}」就有 {rx.total_sets} 组 —— 会超。")
        else:
            print(f"   单次预算 {mins} 分钟 ≈ {cap} 组（所有部位合计），"
                  f"「{group}」占 {rx.total_sets} 组。")
    print()

    for p in rx.movements:
        print(f"  {p.name}：{p.why}")
    _calib_note(target)
    return 0


def cmd_log(args) -> int:
    """手记训练。解析 → 展示摘要 → 确认 → 落库。"""
    import pathlib

    from . import manual

    if args.file == "-":
        if sys.stdin.isatty():
            print("从标准输入读速记文本，Ctrl-D 结束：\n")
        text = sys.stdin.read()
    else:
        text = pathlib.Path(args.file).read_text(encoding="utf-8")

    date = dt.date.fromisoformat(args.date) if args.date else None
    result = manual.parse(text, default_date=date)

    for issue in result.issues:
        print(f"  {issue}")

    if result.session is None:
        print("\n没能解析出训练内容。速记格式示例：")
        print("  # 2026-07-26 推日\n  杠铃卧推 60x10 60x8 62.5x8@8\n  绳索夹胸 15x15x3")
        return 1

    print("\n" + "─" * 56)
    print(manual.summarize(result.session))
    print("─" * 56)

    existing = store.load_sessions(start=result.session["date"],
                                   end=result.session["date"])
    dup = manual.dedupe_against(result.session, existing)
    if dup:
        print(f"\n⚠️  这一天已经有训记同步来的记录（{dup['id']}），动作高度重合。")
        print("    训记的记录优先，这条手记会被标记为已被取代（不会删除）。")
        result.session["superseded_by"] = dup["id"]

    if args.dry_run:
        print("\n（dry-run，没有写入）")
        return 0
    if not args.yes:
        try:
            if input("\n确认写入？[y/N] ").strip().lower() not in ("y", "yes"):
                print("已取消。")
                return 0
        except EOFError:
            print("\n非交互环境，请加 --yes 确认写入。")
            return 1

    store.init()
    store.upsert_sessions([result.session])
    print(f"\n已写入 {result.session['id']}")
    print("接着可以跑：hc compare　或　hc report weekly")
    return 0


def cmd_classify(args) -> int:
    """查看/教会动作的肌群归属。"""
    from . import taxonomy

    if args.learn:
        name, _, group = args.learn.partition("=")
        if not group:
            print("用法：hc classify --learn '动作名=部位'")
            return 1
        taxonomy.learn(name.strip(), group.strip())
        print(f"记住了：{name.strip()} → {group.strip()}")
        return 0

    sessions = store.load_sessions()
    seen: dict[str, tuple[str, str]] = {}
    for s in sessions:
        for m in s.get("movements") or []:
            c = taxonomy.classify_movement(m)
            seen[m["name"]] = (c.group, c.source)

    unknown = {n: v for n, v in seen.items() if v[0] == taxonomy.UNKNOWN}
    total_seen = len(seen)
    if args.unknown_only:
        seen = unknown

    if not seen:
        # 「没有未分类动作」和「本地没有记录」是两回事，别混为一谈
        if total_seen:
            print(f"全部 {total_seen} 个动作都已归类，没有需要处理的。")
        else:
            print("本地还没有训练记录。跑 `hc sync` 或 `hc log`。")
        return 0

    src_label = {"override": "你教的", "xunji_type": "训记返回",
                 "taxonomy": "动作表", "rule": "关键词推断", "unknown": "未分类"}
    for name, (group, source) in sorted(seen.items(), key=lambda kv: kv[1][0]):
        print(f"  {group:<5} {name:<24} [{src_label.get(source, source)}]")

    print(f"\n共 {len(seen)} 个动作")
    if unknown and not args.unknown_only:
        print(f"其中 {len(unknown)} 个未分类。教给我：")
        for n in list(unknown)[:5]:
            print(f"  hc classify --learn '{n}=胸'")
    return 0


def cmd_report(args) -> int:
    import json

    from .config import REPORTS_DIR
    from .report import build, month_bounds, render, slug, week_bounds, year_bounds

    today = dt.date.today()
    anchor = dt.date.fromisoformat(args.date) if args.date else today
    bounds = {"weekly": week_bounds, "monthly": month_bounds, "yearly": year_bounds}
    start, end = bounds[args.kind](anchor)

    model = build(args.kind, start, end)
    name = slug(args.kind, start)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    facts_path = REPORTS_DIR / f"{name}.facts.json"
    facts_path.write_text(
        json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.auto:
        from .llm import LLMNotConfigured, narrate
        try:
            narrative = narrate(model)
            if narrative:
                model["narrative"] = narrative
                facts_path.write_text(
                    json.dumps(model, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                print(f"  已生成叙述：{'、'.join(narrative)}")
            else:
                print("  模型没有返回可用叙述（或数字校验未通过），保持纯数据模式")
        except LLMNotConfigured as e:
            print(f"\n{e}")

    html_path = REPORTS_DIR / f"{name}.html"
    html_path.write_text(render(model), encoding="utf-8")

    print(f"\n{model['period']['label']}（{start} ~ {end}）")
    k = model["kpis"]
    dur = f"{k['duration_min']:.0f} 分钟" if k.get("duration_min") else "时长未记录"
    print(f"  训练 {k['sessions']} 次 · {k['sets']} 组 · "
          f"{k['volume_kg']:,.0f} kg · {dur}")
    q = model["data_quality"]
    cov = q.get("coverage_pct")
    # 显式判 None —— 0.0 是 falsy，用 `or` 兜底会让「一天都没同步」变成「全同步了」
    if q.get("sync_applicable") and cov is not None and cov < 100:
        print(f"  ⚠ 本期只同步了 {q['days_synced']}/{q['days_in_period']} 天"
              f"（{cov:.0f}%），跑 `hc sync` 补齐")
    print(f"\n  报告  {html_path}")
    print(f"  事实  {facts_path}")
    print("\n  没有接模型时报告也是完整的。想要教练的文字讲解，"
          "让助手读 facts.json 后用 `hc report inject` 注入。")
    if args.open:
        import subprocess
        subprocess.run(["open", str(html_path)], check=False)
    return 0


def cmd_inject(args) -> int:
    """把模型写好的叙述注入报告。"""
    import json

    from .config import REPORTS_DIR
    from .report import render

    facts_path = REPORTS_DIR / f"{args.name}.facts.json"
    if not facts_path.exists():
        print(f"找不到 {facts_path}。先跑 `hc report weekly`。")
        return 1
    model = json.loads(facts_path.read_text(encoding="utf-8"))

    text = (sys.stdin.read() if args.file == "-"
            else __import__("pathlib").Path(args.file).read_text(encoding="utf-8"))
    model.setdefault("narrative", {})[args.slot] = text.strip()

    facts_path.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    html_path = REPORTS_DIR / f"{args.name}.html"
    html_path.write_text(render(model), encoding="utf-8")
    print(f"已注入 {args.slot} → {html_path}")
    return 0


def cmd_persona(args) -> int:
    """看/换教练语气。不带参数是纯读操作，放白名单安全。"""
    from . import persona

    if args.set:
        try:
            slug = persona.set_tone(args.set)
        except ValueError as e:
            print(f"❌ {e}")
            return 1
        print(f"✅ 教练语气 → {persona.label(slug)}（knowledge/coach/personas/{slug}.md）")
        print("   下次对话生效。核心人格不变 —— 换的只是措辞，不是规则。")
        return 0
    if args.show:
        print(persona.load())
        return 0
    print(persona.render_list())
    return 0


def cmd_journal(args) -> int:
    """教练工作日志。读操作永远不写盘，所以放进权限白名单是安全的。"""
    from . import journal

    action = getattr(args, "journal_action", None)
    today = dt.date.today()

    if action == "add":
        try:
            entry_id = journal.add(
                args.kind, args.topic, args.text,
                evidence=args.evidence or [],
                supersedes=args.supersedes,
            )
        except ValueError as e:
            print(f"写不进去：{e}")
            return 1
        print(f"已记 [{entry_id}] {args.kind}·{args.topic}  {args.text}")
        if args.kind == "待确认":
            print("  这条在闭合前每次对话都会浮出来。"
                  f"用户拍板后跑 `hc journal confirm {entry_id} --landed <文件#小节>`。")
        return 0

    if action in ("confirm", "reject"):
        status = (journal.STATUS_CONFIRMED if action == "confirm"
                  else journal.STATUS_REJECTED)
        try:
            journal.set_status(args.id, status,
                               landed=getattr(args, "landed", None),
                               why=getattr(args, "why", None))
        except ValueError as e:
            print(f"标不了：{e}")
            return 1
        if action == "confirm":
            print(f"[{args.id}] 已确认，落盘位置 {args.landed}")
            print("  记得 profile 里的旧结论要降格成历史行，不要删除。")
        else:
            print(f"[{args.id}] 已否决" + (f"：{args.why}" if args.why else ""))
        return 0

    # 以下都是只读视图
    if args.grep:
        hits = journal.search(args.grep, today=today)
        if not hits:
            print(f"日志里没有匹配「{args.grep}」的条目。")
            return 0
        print(f"匹配「{args.grep}」的 {len(hits)} 条（全量检索，不受窗口限制）")
        print(f"⚠️  {journal.DISCLAIMER}\n")
        for it in hits:
            print(journal.fmt_line(it, today, indent="  "))
        return 0

    if args.since:
        start = _parse_since(args.since, today)
        hits = journal.since(start, today=today)
        print(f"{start.isoformat()} 至今共 {len(hits)} 条")
        print(f"⚠️  {journal.DISCLAIMER}\n")
        for it in hits:
            print(journal.fmt_line(it, today, indent="  "))
        return 0

    if args.brief:
        text = journal.render_brief(window_days=args.window, today=today)
        if text:
            print(text)
        return 0

    print(journal.render(window_days=args.window, today=today))
    return 0


def cmd_dice(args) -> int:
    """食物骰子。约束在先、随机在后 —— 摊开筛掉了什么，比给一个答案重要。"""
    from . import dice

    action = getattr(args, "dice_action", None)
    now = dt.datetime.now()
    today = dt.date.fromisoformat(args.date) if getattr(args, "date", None) else now.date()

    if action == "list":
        pool, issues = dice.load_pool_with_issues()
        walls = dice.load_avoid()
        avoid = walls.all_terms()
        blocks = dice.load_medical_blocks()
        if walls.warn:
            print(f"⚠️  {walls.warn}\n")
        for issue in issues:
            print(f"⚠️  候选池：{issue}")
        shown = [d for d in pool
                 if (not args.tier or d["tier"] == args.tier)
                 and (not args.slot or args.slot in d["slots"])
                 and (not args.scene or args.scene in d["scenes"])
                 and (not args.cuisine or d["cuisine"] == args.cuisine)
                 and (not args.effort or d["effort"] == args.effort)]
        print(f"候选池 {len(pool)} 道，符合条件 {len(shown)} 道")
        if avoid:
            print(f"  忌口：{'、'.join(avoid)}")
        if blocks:
            print(f"  医学禁忌：{'、'.join(blocks)}")
        print()
        for d in sorted(shown, key=lambda x: (x["cuisine"], x["name"])):
            why = ""
            hit = dice.blocked_by(d, walls)
            if hit:
                why = f" ⛔{hit[1]}"
            elif any(f in blocks for f in d["flags"]):
                names = [f for f in d["flags"] if f in blocks]
                why = f" ⛔医学禁忌（{'、'.join(names)}）"
            local = " *" if d["source"] == "local" else ""
            fast = "⚡" if d["effort"] == "快手" else "　"
            print(f"  {fast}{dice.TIER_LABEL[d['tier']]} 嘌呤{d['purine']:<2} 蛋白{d['protein']}"
                  f"  {d['cuisine']:<5}  {d['name']}{local}{why}")
        print(f"\n  ⚡ = 快手菜   * = 你自己加的（{dice.LOCAL_POOL_PATH.name}）")
        print(f"  菜系：{'、'.join(dice.cuisines())}")
        return 0

    if action == "add":
        try:
            dice.add_dish({
                "name": args.name, "tier": args.tier, "purine": args.purine,
                "protein": args.protein, "scenes": args.scene or None,
                "slots": args.slot or None, "contains": args.contains or [],
                "cuisine": args.cuisine, "effort": args.effort,
                "flags": args.flag or [],
                "fix": args.fix or [], "note": args.note or "",
            })
        except ValueError as e:
            print(f"加不进去：{e}")
            return 1
        print(f"已加进个人池：{args.name}（{args.tier}灯 · 嘌呤{args.purine} · 蛋白{args.protein}）")
        print(f"  {dice.LOCAL_POOL_PATH}")
        return 0

    if action == "log":
        rolls = dice.settled_rolls()
        start = _parse_since(args.since, today) if args.since else today - dt.timedelta(days=14)
        hits = [r for r in rolls if r.get("date", "") >= start.isoformat()]
        if not hits:
            print(f"{start.isoformat()} 至今还没摇过。")
            return 0
        print(f"{start.isoformat()} 至今摇过 {len(hits)} 次"
              "（同一餐重摇过的以最后一次为准，旧的仍在 data/dice.jsonl 里）")
        for r in hits:
            mark = " ⚠️破戒" if r.get("breakable") else ""
            print(f"  {r['date']} {r['slot']}  {dice.TIER_LABEL.get(r.get('tier'), '?')}"
                  f"  {r['dish']}{mark}")
        month = today.strftime("%Y-%m")
        used = dice.red_used(month, rolls)
        phase, defaulted = dice.load_phase()
        print(f"\n  {month} 破戒额度 {len(used)}/{dice.red_quota(phase)}"
              f"（阶段：{phase}{' —— 默认值，没在 profile 里设' if defaulted else ''}）")
        return 0

    # 默认：摇一次
    slot = args.slot or dice.default_slot(now)

    existing = dice.current_roll(today.isoformat(), slot)
    if existing and not args.again:
        from . import nutrition
        pool = {d["name"]: d for d in dice.load_pool()}
        d = pool.get(existing["dish"])
        if d is None:  # 池子里删掉了，用日志里存的那份档位凑合显示
            d = {"name": existing["dish"], "tier": existing.get("tier", "黄"),
                 "purine": existing.get("purine", "中"),
                 "protein": existing.get("protein", "中"),
                 "cuisine": existing.get("cuisine", "其他"), "effort": "中等",
                 "contains": [], "flags": [], "fix": [], "note": ""}

        # 回放**必须重跑两堵硬墙**。约束是会变的：早上摇完，中午拿到复查结果
        # 把「内脏为主」加进医学禁忌 —— 这时候原样端出早上那道菜是不能接受的。
        walls = dice.load_avoid()
        blocks = dice.load_medical_blocks()
        hit = dice.blocked_by(d, walls)
        hit_flags = [f for f in d.get("flags", []) if f in blocks]
        if hit or hit_flags:
            why = hit[1] if hit else f"医学禁忌（{'、'.join(hit_flags)}）"
            print(f"⚠️  今天这一餐原本摇到「{d['name']}」，但它现在撞上了{why}。")
            print("    约束是在那次之后才生效的，所以这条结论作废，重摇一次：\n")
            args.again = True   # 落到下面的正常摇一次
        else:
            phase, defaulted = dice.load_phase()
            quota = dice.red_quota(phase)
            # 这一餐已经记在账上了，算剩余额度时要把它排除，否则会误报「已超额」
            used = dice.red_used(today.strftime("%Y-%m"),
                                 dice.settled_rolls(exclude=(existing["date"], slot)))
            print(dice.render({
                "date": existing["date"], "slot": existing["slot"],
                "scene": existing.get("scene"), "cuisine": None, "effort": None,
                "seed": existing.get("seed"), "targets": nutrition.targets(today),
                "pool_total": len(pool), "candidates": 0,
                "dropped": {}, "survivors": {},
                "phase": phase, "phase_defaulted": defaulted, "phase_why": "",
                "quota": quota, "quota_left": max(0, quota - len(used)),
                "quota_used": used, "yellow_this_week": 0,
                "avoid": walls.all_terms(), "allergy": walls.allergy,
                "blocks": blocks, "likes": [], "warn": walls.warn,
                "pool_issues": [],
                "dish": d, "alternates": [],
            }, replayed=True))
            return 0

    result = dice.roll(slot=slot, scene=args.scene, cuisine=args.cuisine,
                       effort=args.effort, today=today, seed=args.seed,
                       allow_red=args.allow_red, min_protein=args.min_protein,
                       again=args.again)
    print(dice.render(result))
    if result["dish"] and not args.dry_run:
        dice.commit(result)
    return 0 if result["dish"] else 1


def cmd_setup(args) -> int:
    """一次问完所有需要用户自己填的东西，写回各自的真相源。"""
    from . import setup as setup_mod

    if args.show:
        print("需要你自己提供的数据 —— 完整清单")
        print("=" * 60)
        for label, where, how, why in setup_mod.DATA_MAP:
            print(f"\n{label}")
            print(f"  位置    {where}")
            print(f"  怎么弄  {how}")
            print(f"  用来做  {why}")
        return 0
    try:
        return setup_mod.run(dry_run=args.dry_run)
    except (KeyboardInterrupt, EOFError):
        print("\n已取消，没有写盘。")
        return 130


def cmd_targets(args) -> int:
    """每日目标摄入量。数字全部算出来，且必须说明是怎么算的。"""
    from . import nutrition

    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    t = nutrition.targets(today)
    print(nutrition.render_targets(t))
    return 0 if t.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hc",
        description="健身教练 / 营养师 / 健康顾问 一体化工具 —— 模型无关，即启即用",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用流程:
  hc doctor                    体检：环境、凭证、本地数据
  hc sync --since 30d          同步最近 30 天
  hc sync train --since 90d    只同步训练（限频较严，会预告耗时）
  hc sessions --since 30d      看看本地都有什么
  hc rebuild                   改了解析逻辑后离线重算，零网络请求
""")
    sub = p.add_subparsers(dest="command", required=True)

    su = sub.add_parser("setup", help="引导式填写个人数据（一次问完，写回各自的真相源）")
    su.add_argument("--show", action="store_true",
                    help="只列出「什么数据填在哪」，不进入问答")
    su.add_argument("--dry-run", action="store_true",
                    help="走完全部问答但**一个字都不写盘**，只打印会改什么")
    su.set_defaults(func=cmd_setup)

    d = sub.add_parser("doctor", help="环境体检")
    d.add_argument("-v", "--verbose", action="store_true", help="显示脱敏后的凭证片段")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("sync", help="从训记同步数据")
    s.add_argument("what", nargs="?", choices=["all", "train", "body", "food"],
                   default="all", help="同步哪一部分（默认全部）")
    s.add_argument("--since", help="起始日期，支持 2026-07-01 或 30d/12w/6m/1y")
    s.add_argument("--until", help="结束日期，默认今天")
    s.add_argument("--light", action="store_true",
                   help="轻量模式（15s/次而非 30s），但不含 RPE、左右重量、未完成组")
    s.add_argument("--force", action="store_true", help="忽略本地缓存，强制重抓")
    s.add_argument("--budget-minutes", type=float,
                   help="本次最多跑多少分钟，适合放进 cron 分批补历史")
    s.add_argument("--max-requests", type=int, help="本次最多发多少个请求")
    s.add_argument("--stop-after-empty-streak", type=int, default=None,
                   help="连续 N 天无记录就停止，用于探到历史起点")
    s.set_defaults(func=cmd_sync)

    ih = sub.add_parser("import-health", help="导入苹果健康导出文件（导出.zip）")
    ih.add_argument("path", help="导出.zip 或解压后的 导出.xml / 目录")
    ih.add_argument("--since", help="只导入这个日期之后的，比如 2026-01-01")
    ih.add_argument("--dry-run", action="store_true", help="只解析看结果，不写入")
    ih.set_defaults(func=cmd_import_health)

    cd = sub.add_parser("cardio", help="有氧与心率区间分析")
    cd.add_argument("--since", default="2026-06-01", help="起始日期")
    cd.add_argument("--window", type=int, default=7, help="汇总窗口天数，默认 7")
    cd.set_defaults(func=cmd_cardio)

    stt = sub.add_parser("status", help="数据新鲜度（不联网，秒回）")
    stt.add_argument("-v", "--verbose", action="store_true")
    stt.set_defaults(func=cmd_status)

    aus = sub.add_parser("autosync", help="后台自动同步（装上就不用现场等）")
    aus.add_argument("action", nargs="?", default="status",
                     choices=["install", "uninstall", "status", "log"])
    aus.add_argument("--interval", type=int, default=3, help="每几小时跑一次，默认 3")
    aus.add_argument("--backfill-minutes", type=int, default=10,
                     help="每次顺带回填多少分钟历史，默认 10")
    aus.set_defaults(func=cmd_autosync)

    r = sub.add_parser("rebuild", help="用本地原始缓存离线重算（零网络请求）")
    r.set_defaults(func=cmd_rebuild)

    ls = sub.add_parser("sessions", help="列出本地训练记录")
    ls.add_argument("--since", help="起始日期，支持 2026-07-01 或 30d")
    ls.set_defaults(func=cmd_sessions)

    sm = sub.add_parser("summary", help="单次训练的详细指标")
    sm.add_argument("--date", help="日期，默认最近一次")
    sm.set_defaults(func=cmd_summary)

    cp = sub.add_parser("compare", help="本次 vs 上次同部位，出优点/缺点/改进点")
    cp.add_argument("--date", help="日期，默认最近一次")
    cp.add_argument("--group", help="只看某个部位，比如 胸")
    cp.set_defaults(func=cmd_compare)

    cb = sub.add_parser("calib", help="负荷口径：同一动作换机位导致的重量跳变")
    cb.add_argument("action", nargs="?", default="check", choices=("check", "set", "list"),
                    help="check=看有哪些可疑跳变（默认）  set=写规则  list=看现有规则")
    cb.add_argument("movement", nargs="?", help="动作名（set 时必填）")
    cb.add_argument("--date", help="只对这一天生效")
    cb.add_argument("--from", dest="date_from", help="起始日期（含）")
    cb.add_argument("--to", dest="date_to", help="结束日期（含）")
    cb.add_argument("--ratio", type=float,
                    help="折算系数。旧机位是 2:1 就填 0.5。原始文件不动")
    cb.add_argument("--ignore", action="store_true", help="该动作该次不参与对比")
    cb.add_argument("--confirm", action="store_true", help="确认是真实变化，不再预警")
    cb.add_argument("--note", help="为什么这么定，写给半年后的自己")
    cb.add_argument("--supersedes", help="推翻哪条旧规则（只追加，不改旧的）")
    cb.set_defaults(func=cmd_calib)

    nx = sub.add_parser("next", help="下次同部位训练的具体建议")
    nx.add_argument("group", help="部位，比如 胸 / 背 / 腿")
    nx.set_defaults(func=cmd_next)

    lg = sub.add_parser("log", help="手记一次训练（没有训记也能用）")
    lg.add_argument("--file", default="-", help="速记文本文件，- 表示从 stdin 读")
    lg.add_argument("--date", help="日期，默认取文本里的或今天")
    lg.add_argument("--dry-run", action="store_true", help="只解析并展示，不写入")
    lg.add_argument("--yes", action="store_true", help="跳过确认直接写入")
    lg.set_defaults(func=cmd_log)

    cf = sub.add_parser("classify", help="查看或教会动作的肌群归属")
    cf.add_argument("--learn", metavar="'动作名=部位'", help="教一个动作属于哪个部位")
    cf.add_argument("--unknown-only", action="store_true", help="只看未分类的")
    cf.set_defaults(func=cmd_classify)

    rp = sub.add_parser("report", help="生成自包含 HTML 报告（周/月/年）")
    rp.add_argument("kind", choices=["weekly", "monthly", "yearly"])
    rp.add_argument("--date", help="期间内的任意一天，默认今天")
    rp.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    rp.add_argument("--auto", action="store_true",
                    help="用 .env 里配的 LLM 适配器自动写叙述（cron 无人值守用；"
                         "在 agent 宿主里不需要）")
    rp.set_defaults(func=cmd_report)

    from . import persona as _pa

    pa = sub.add_parser(
        "persona", help="教练语气（四选一，只换措辞不换规则）",
        description="核心人格在 knowledge/coach/persona.md，不可选；"
                    "这里选的是语气层 knowledge/coach/personas/<语气>.md。")
    pa.add_argument("--set", metavar="语气",
                    help="切换语气，中文名或 slug 都行："
                         + " / ".join(f"{n}({s})" for s, (n, _) in _pa.TONES.items()))
    pa.add_argument("--show", action="store_true",
                    help="打印拼装后的完整人格（核心 + 当前语气）")
    pa.set_defaults(func=cmd_persona)

    # journal 模块零依赖、无副作用，可以在这里直接导入，省得把词表抄一遍
    from . import journal as _jr

    jr = sub.add_parser(
        "journal", help="教练工作日志（线索层，非事实）",
        description="教练随手记的笔记。权威性排在优先级链最底下，只提供线索和近期摘要。")
    jr.add_argument("--window", type=int, default=_jr.WINDOW_DAYS,
                    help=f"最近多少天（默认 {_jr.WINDOW_DAYS} 天）")
    jr.add_argument("--brief", action="store_true",
                    help="紧凑版，给 --append-system-prompt 注入用")
    jr.add_argument("--grep", metavar="关键词", help="全量检索（聊到旧话题时用）")
    jr.add_argument("--since", help="某日期至今，支持 2026-07-01 或 30d/12w")
    jr.set_defaults(func=cmd_journal, journal_action=None)

    jsub = jr.add_subparsers(dest="journal_action")

    ja = jsub.add_parser("add", help="记一条")
    ja.add_argument("--kind", required=True, choices=list(_jr.KINDS),
                    help="观察=事实 / 判断=我的推断 / 待确认=需要用户拍板")
    ja.add_argument("--topic", required=True, choices=list(_jr.TOPICS))
    ja.add_argument("--text", required=True, help="一句话")
    ja.add_argument("--evidence", action="append", metavar="来源",
                    help="数字的出处，如 'hc compare --date 2026-08-05'，可重复")
    ja.add_argument("--supersedes", metavar="ID", help="推翻之前的哪一条（旧条目不会被删）")
    ja.set_defaults(func=cmd_journal, journal_action="add")

    jc = jsub.add_parser("confirm", help="用户拍板了，标为已确认")
    jc.add_argument("id")
    jc.add_argument("--landed", required=True, metavar="文件#小节",
                    help="落到 profile 的哪里，如 profile/personal-context.md#3-目标")
    jc.set_defaults(func=cmd_journal, journal_action="confirm")

    jx = jsub.add_parser("reject", help="用户否了，标为已否决")
    jx.add_argument("id")
    jx.add_argument("--why", help="为什么否了")
    jx.set_defaults(func=cmd_journal, journal_action="reject")

    # dice 模块零依赖、只读路径无副作用，直接导入词表，省得抄一遍
    from . import dice as _dc

    dc = sub.add_parser(
        "dice", help="食物骰子：今天吃什么",
        description="约束在先、随机在后。忌口/嘌呤/红黄绿灯先筛，再按蛋白密度加权摇。")
    dc.add_argument("--slot", choices=list(_dc.SLOTS), help="餐次，默认按当前时间判断")
    dc.add_argument("--scene", choices=list(_dc.SCENES), help="外卖 / 店里 / 家里 / 聚餐")
    dc.add_argument("--cuisine", help="限定菜系，可选值见 hc dice list 末尾")
    dc.add_argument("--effort", choices=list(_dc.EFFORTS),
                    help="快手 / 中等 / 费事。自己做饭时用")
    dc.add_argument("--again", action="store_true",
                    help="重摇这一餐（旧的作废，但仍留在日志里）")
    dc.add_argument("--allow-red", action="store_true",
                    help="把红灯和高嘌呤放回池子，摇到就消耗本月破戒额度")
    dc.add_argument("--min-protein", choices=list(_dc.PROTEINS),
                    help="只摇蛋白密度不低于这一档的（练后 / 蛋白落后时用）")
    dc.add_argument("--seed", type=int,
                    help="固定随机种子。只在池子和历史都没变时才给出同一个结果 —— "
                         "想查某天摇到什么用 hc dice log")
    dc.add_argument("--date", help="按哪天算，默认今天")
    dc.add_argument("--dry-run", action="store_true", help="只看结果，不写日志、不消耗额度")
    dc.set_defaults(func=cmd_dice, dice_action=None)

    dsub = dc.add_subparsers(dest="dice_action")

    dl = dsub.add_parser("list", help="看池子里都有什么")
    dl.add_argument("--tier", choices=list(_dc.TIERS))
    dl.add_argument("--slot", choices=list(_dc.SLOTS))
    dl.add_argument("--scene", choices=list(_dc.SCENES))
    dl.add_argument("--cuisine")
    dl.add_argument("--effort", choices=list(_dc.EFFORTS))
    dl.set_defaults(func=cmd_dice, dice_action="list")

    da = dsub.add_parser("add", help="往个人池加一道菜（同名覆盖通用池）")
    da.add_argument("--name", required=True)
    da.add_argument("--tier", required=True, choices=list(_dc.TIERS))
    da.add_argument("--purine", required=True, choices=list(_dc.PURINES),
                    help="分档见 knowledge/nutrition/purine-reference.json，别凭印象填")
    da.add_argument("--protein", required=True, choices=list(_dc.PROTEINS))
    da.add_argument("--cuisine", help="中餐 / 意大利 / 墨西哥 ...")
    da.add_argument("--effort", choices=list(_dc.EFFORTS), default="中等")
    da.add_argument("--scene", action="append", choices=list(_dc.SCENES), help="可重复")
    da.add_argument("--slot", action="append", choices=list(_dc.SLOTS), help="可重复")
    da.add_argument("--contains", action="append", metavar="食材", help="忌口过滤用，可重复")
    da.add_argument("--flag", action="append", choices=list(_dc.FLAGS),
                    help="中性事实标签，医学禁忌层据此拦截。可重复")
    da.add_argument("--fix", action="append", metavar="怎么点", help="可重复")
    da.add_argument("--note", help="为什么是这一档，一句话")
    da.set_defaults(func=cmd_dice, dice_action="add")

    dg = dsub.add_parser("log", help="最近摇过什么 + 本月破戒额度")
    dg.add_argument("--since", help="默认最近 14 天")
    dg.set_defaults(func=cmd_dice, dice_action="log")

    tg = sub.add_parser("targets", help="每日目标摄入量（热量 / 蛋白 / 脂肪 / 碳水）",
                        description="Mifflin-St Jeor + 由实测步数和训练频率推的活动系数 + 阶段调整。")
    tg.add_argument("--date", help="按哪天算，默认今天")
    tg.set_defaults(func=cmd_targets)

    ij = sub.add_parser("inject", help="把模型写的叙述注入报告")
    ij.add_argument("name", help="报告名，比如 2026-W30")
    ij.add_argument("--slot", required=True,
                    choices=["opening", "training", "body", "nutrition", "closing"])
    ij.add_argument("--file", default="-", help="叙述文件路径，- 表示从 stdin 读")
    ij.set_defaults(func=cmd_inject)

    return p


def main(argv: list[str] | None = None) -> int:
    load_env()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
