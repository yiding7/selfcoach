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

    problems = check_invariants(findings)
    if problems:
        print("\n⚠️  结论结构自检未通过（这是工具的 bug，请反馈）：")
        for p in problems:
            print(f"     {p}")
        return 1
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

    # 本周该肌群的总组数
    import datetime as dt
    last = dt.date.fromisoformat(target.date)
    week_start = (last - dt.timedelta(days=last.weekday())).isoformat()
    weekly = sum(s.groups.get(group, 0) for s in stats if s.date >= week_start)

    rx = prescribe_group(group, target, cmp, weekly_sets=weekly, body_trend=trend)

    print(f"\n{'═' * 62}")
    print(f"下次「{group}」训练建议")
    print("═" * 62)
    print(f"  基于 {target.date} 那次训练（{target.label}）")
    print(f"  本周 {group} 已练 {weekly:.0f} 组")
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
    print(f"合计 {rx.total_sets} 组\n")
    for p in rx.movements:
        print(f"  {p.name}：{p.why}")
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
