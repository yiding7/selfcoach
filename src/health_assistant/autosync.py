"""后台自动同步。

**要解决的问题**：训记训练接口 30 秒/天限频。如果每次打开助手都要现场同步，
一等十几分钟，这个工具就没法用了 —— 助手应该是随叫随到的。

**解法**：把同步挪到后台，按固定间隔悄悄跑。对话开始时数据本来就是新的，
助手只需要跑一个**零网络的** `hc status` 确认新鲜度，毫秒级返回。

分工：
  · `hc status`   本地读水位，不联网。助手每次对话开头跑这个
  · `hc autosync` 装一个 launchd 定时任务（macOS）或打印 cron 配置（Linux）
  · 定时任务跑 `hc sync train --since 7d` + 一点回填预算

为什么增量同步很快：抓过的日期永远不重抓，所以稳定运行后每次只有
0~2 个新日期要抓，几十秒就结束了。慢的只有第一次回填。
"""

from __future__ import annotations

import datetime as dt
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import store
from .config import DATA_DIR, ROOT

LABEL = "com.selfcoach.autosync"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = DATA_DIR / "autosync.log"

# 默认每 3 小时跑一次。训练接口限频 30s/天，稳态下每次只有 0~2 天要抓。
DEFAULT_INTERVAL_HOURS = 3
# 每次顺带回填多少分钟的历史（补早期数据用，补完自动就没活干了）
DEFAULT_BACKFILL_MINUTES = 10


# ── 新鲜度 ──────────────────────────────────────────────────────────────

def freshness(today: dt.date | None = None) -> dict:
    """本地水位快照。**不联网**，毫秒级返回。"""
    today = today or dt.date.today()
    idx: dict[str, dict] = {}
    for year in range(today.year - 2, today.year + 1):
        idx.update(store.load_index(year))

    fetched = sorted(idx)
    sessions = store.load_sessions()
    body = store.load_body(types=["weight"])

    # 从今天往回数，第一个没抓过的日期就是缺口起点
    missing: list[str] = []
    d = today
    while len(missing) < 60:
        s = d.isoformat()
        if s not in idx:
            missing.append(s)
        elif missing:
            break
        d -= dt.timedelta(days=1)
        if (today - d).days > 60:
            break

    last_body = body[-1]["date"] if body else None
    return {
        "today": today.isoformat(),
        "train_fetched_days": len(fetched),
        "train_first": fetched[0] if fetched else None,
        "train_last": fetched[-1] if fetched else None,
        "train_sessions": len(sessions),
        "missing_recent": sorted(missing),
        "missing_count": len(missing),
        "body_last": last_body,
        "body_stale_days": ((today - dt.date.fromisoformat(last_body)).days
                            if last_body else None),
        "autosync_installed": PLIST_PATH.exists(),
        "last_autosync": _last_autosync(),
    }


def _last_autosync() -> str | None:
    try:
        return dt.datetime.fromtimestamp(
            LOG_PATH.stat().st_mtime).replace(microsecond=0).isoformat()
    except OSError:
        return None


def estimate_minutes(n_days: int) -> float:
    return n_days * 31 / 60.0


def status_report(verbose: bool = False) -> str:
    f = freshness()
    lines = ["数据新鲜度（本地读取，未联网）", "=" * 46]

    if f["train_last"]:
        lines.append(f"  训练数据已抓取 {f['train_first']} ~ {f['train_last']}"
                     f"（{f['train_fetched_days']} 天 / {f['train_sessions']} 次训练）")
    else:
        lines.append("  训练数据：本地为空")

    n = f["missing_count"]
    if n == 0:
        lines.append("  ✅ 最近的日期都已同步，可以直接分析")
    else:
        days = f["missing_recent"]
        lines.append(f"  ⚠️  最近 {n} 天未同步（{days[0]} ~ {days[-1]}），"
                     f"补齐约需 {estimate_minutes(n):.0f} 分钟")

    if f["body_stale_days"] is not None:
        mark = "✅" if f["body_stale_days"] <= 3 else "⚠️ "
        lines.append(f"  {mark} 体重数据最新到 {f['body_last']}"
                     f"（{f['body_stale_days']} 天前，同步很快）")
    else:
        lines.append("  · 体重数据：本地为空")

    lines.append("")
    if f["autosync_installed"]:
        last = f["last_autosync"] or "尚未运行"
        lines.append(f"  ✅ 后台自动同步已启用，上次运行 {last}")
        lines.append(f"     日志 {LOG_PATH}")
    else:
        lines.append("  · 后台自动同步：未启用")
        lines.append("     装上之后数据会在后台保持新鲜，对话时不用等：")
        lines.append("       hc autosync install")
    return "\n".join(lines)


# ── launchd ────────────────────────────────────────────────────────────

def _plist(interval_hours: int, backfill_minutes: int) -> dict:
    hc = ROOT / "scripts" / "hc"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            "/bin/bash", str(ROOT / "scripts" / "autosync.sh"),
            str(backfill_minutes),
        ],
        "StartInterval": interval_hours * 3600,
        # 开机/加载时先跑一次，避免装完还要等一个周期
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
            "HC_AUTOSYNC": "1",
        },
        "_hc_program": str(hc),
    }


def _write_runner(backfill_minutes: int) -> Path:
    """把同步逻辑写成一个小脚本，launchd 调它。

    单独一个脚本的好处：改同步策略不用重装 launchd 任务。
    """
    path = ROOT / "scripts" / "autosync.sh"
    path.write_text(f"""#!/usr/bin/env bash
# 由 launchd/cron 调用。改这个文件即可调整同步策略，不需要重装定时任务。
set -uo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
BUDGET="${{1:-{backfill_minutes}}}"

echo "───── $(date '+%Y-%m-%d %H:%M:%S') 开始 ─────"

# 1) 最近 7 天：日常增量，绝大多数日期已缓存，通常几十秒结束
"$HERE/scripts/hc" sync train --since 7d 2>&1

# 2) 身体数据：范围查询，一次调用，很快
"$HERE/scripts/hc" sync body --since 90d 2>&1

# 3) 顺带回填更早的历史，限时。补完之后这步自然就没活干了。
#    起点可以按需往前调；补到头会显示「全部已缓存」。
"$HERE/scripts/hc" sync train --since 2026-03-01 --until "$(date -v-8d '+%Y-%m-%d' 2>/dev/null || date -d '8 days ago' '+%Y-%m-%d')" \\
    --budget-minutes "$BUDGET" 2>&1

echo "───── $(date '+%Y-%m-%d %H:%M:%S') 结束 ─────"
""", encoding="utf-8")
    path.chmod(0o755)
    return path


def install(interval_hours: int = DEFAULT_INTERVAL_HOURS,
            backfill_minutes: int = DEFAULT_BACKFILL_MINUTES) -> int:
    if sys.platform != "darwin":
        print(_cron_instructions(interval_hours, backfill_minutes))
        return 0

    runner = _write_runner(backfill_minutes)
    data = _plist(interval_hours, backfill_minutes)
    data.pop("_hc_program", None)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(data))

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                   capture_output=True, check=False)
    r = subprocess.run(["launchctl", "load", str(PLIST_PATH)],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"launchctl load 失败：{r.stderr.strip()}")
        print(f"你可以手动跑：launchctl load {PLIST_PATH}")
        return 1

    print("✅ 后台自动同步已启用")
    print(f"   每 {interval_hours} 小时跑一次，每次顺带回填 {backfill_minutes} 分钟历史")
    print(f"   任务  {PLIST_PATH}")
    print(f"   脚本  {runner}（改这个文件即可调整策略，不用重装）")
    print(f"   日志  {LOG_PATH}")
    print()
    print("   刚装好会立刻跑一次。之后对话前用 `hc status` 看新鲜度（不联网，秒回）。")
    return 0


def uninstall() -> int:
    if not PLIST_PATH.exists():
        print("后台自动同步本来就没装。")
        return 0
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                   capture_output=True, check=False)
    PLIST_PATH.unlink(missing_ok=True)
    print(f"已停用并删除 {PLIST_PATH}")
    print("（同步脚本 scripts/autosync.sh 保留，可以手动跑或换用 cron）")
    return 0


def _cron_instructions(interval_hours: int, backfill_minutes: int) -> str:
    _write_runner(backfill_minutes)
    return f"""当前系统不是 macOS，已生成同步脚本，请自行加进 cron：

  scripts/autosync.sh

crontab -e 里加一行（每 {interval_hours} 小时）：

  0 */{interval_hours} * * * {ROOT}/scripts/autosync.sh {backfill_minutes} >> {LOG_PATH} 2>&1

之后用 `hc status` 查看新鲜度。"""


def tail_log(n: int = 40) -> int:
    if not LOG_PATH.exists():
        print(f"还没有日志。装了自动同步吗？（hc autosync install）")
        return 0
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    print("\n".join(lines[-n:]))
    return 0
