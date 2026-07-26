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

    for s in sessions:
        mins = f"{s['duration_s'] // 60} min" if s.get("duration_s") else "—"
        kcal = f" {s['kcal']:.0f} kcal" if s.get("kcal") else ""
        title = s.get("title") or "（无标题）"
        print(f"\n{s['date']}  {title}  {mins}{kcal}  [{s['source']}]")
        for m in s.get("movements") or []:
            done = [x for x in m["sets"] if x.get("done")]
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
        print(f"{'动作':<22}{'部位':<6}{'组':>4}{'总次':>6}{'顶组':>9}{'容量':>10}{'估算1RM':>10}")
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
            flag = "*" if m.group_source in ("rule", "taxonomy") else " "
            print(f"{m.name[:20]:<22}{m.group:<6}{m.sets_done:>4}"
                  f"{m.reps_total:>6.0f}{top:>9}{vol:>10}{e:>10}{flag}")
        print("─" * 62)
        vol = f"{st.volume_kg:,.0f} kg" if st.volume_kg is not None else "—"
        print(f"合计  {st.sets_done}/{st.sets_planned} 组   总容量 {vol}"
              + (f"   密度 {st.density_kg_per_min:.0f} kg/min"
                 if st.density_kg_per_min else ""))
        groups = "、".join(f"{g} {n:.0f}组" for g, n in
                           sorted(st.groups.items(), key=lambda kv: -kv[1]))
        print(f"部位分布  {groups}")
        print(f"RPE 覆盖率 {st.rpe_coverage * 100:.0f}%"
              + ("（没有 RPE 数据，强度相关结论会略过）" if st.rpe_coverage < 0.3 else ""))
        print("\n* = 部位由动作名推断（训记未返回该字段）")
    return 0


def cmd_compare(args) -> int:
    from .analytics.compare import compare_session
    from .analytics.findings import check_invariants, evaluate, split

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

    findings = evaluate(target, comparisons)
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
