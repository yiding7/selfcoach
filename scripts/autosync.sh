#!/usr/bin/env bash
# 由 launchd/cron 调用。改这个文件即可调整同步策略，不需要重装定时任务。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUDGET="${1:-10}"

echo "───── $(date '+%Y-%m-%d %H:%M:%S') 开始 ─────"

# 1) 最近 7 天：日常增量，绝大多数日期已缓存，通常几十秒结束
"$HERE/scripts/hc" sync train --since 7d 2>&1

# 2) 身体数据：范围查询，一次调用，很快
"$HERE/scripts/hc" sync body --since 90d 2>&1

# 3) 顺带回填更早的历史，限时。补完之后这步自然就没活干了。
#    起点可以按需往前调；补到头会显示「全部已缓存」。
"$HERE/scripts/hc" sync train --since 2026-03-01 --until "$(date -v-8d '+%Y-%m-%d' 2>/dev/null || date -d '8 days ago' '+%Y-%m-%d')" \
    --budget-minutes "$BUDGET" 2>&1

echo "───── $(date '+%Y-%m-%d %H:%M:%S') 结束 ─────"
