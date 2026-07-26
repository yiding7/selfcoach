#!/usr/bin/env bash
# 一条命令上手。不装任何依赖、不联网。
#
# 这个脚本刻意什么都不下载：整个项目只用 Python 标准库，
# clone 下来就能跑。这就是「即启即用」的字面意思。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

say() { printf "%s\n" "$*"; }
ok()  { printf "  ✅ %s\n" "$*"; }
warn(){ printf "  ⚠️  %s\n" "$*"; }

say ""
say "健康助手 · 安装"
say "════════════════════════════════════════"

# ── Python ──
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  printf "  ❌ 找不到 python3。请先安装 Python 3.11 或更新版本。\n"; exit 1
fi
PYV="$("$PY" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)'; then
  printf "  ❌ 需要 Python 3.11+，当前是 %s\n" "$PYV"; exit 1
fi
ok "Python $PYV"
ok "第三方依赖：0 个（全部标准库）"

# ── 目录 ──
mkdir -p data/training data/body data/meals reports
ok "数据目录已就绪"

# ── .env ──
if [ ! -f .env ]; then
  cp .env.example .env
  warn "已创建 .env —— 有训记的话把 4 个 key 填进去；没有就留空，手记模式功能一样完整"
else
  ok ".env 已存在，未改动"
fi

chmod +x scripts/hc 2>/dev/null || true

# ── 把 skills 链到 agent 宿主 ──
link_skills() {
  local target="$1" label="$2"
  mkdir -p "$target"
  local n=0
  for d in "$HERE"/skills/*/; do
    local name; name="$(basename "$d")"
    local dest="$target/$name"
    if [ -L "$dest" ]; then rm -f "$dest"
    elif [ -e "$dest" ]; then warn "$label/$name 已存在且不是软链，跳过"; continue; fi
    ln -s "$d" "$dest" && n=$((n+1))
  done
  [ "$n" -gt 0 ] && ok "${label}：链接了 $n 个 skill"
}

case "${1:-auto}" in
  auto)
    link_skills "$HERE/.claude/skills" ".claude/skills（Claude Code 项目级）"
    ;;
  global)
    link_skills "$HOME/.claude/skills" "~/.claude/skills（Claude Code 全局）"
    ;;
  codex)
    link_skills "$HERE/.codex/skills" ".codex/skills"
    ;;
  none) say "  跳过 skill 链接" ;;
esac

say ""
say "════════════════════════════════════════"
"$HERE/scripts/hc" doctor || true

say ""
say "下一步："
say "  有训记：填好 .env，然后  ./scripts/hc sync --since 30d"
say "  没训记：直接开始记      ./scripts/hc log"
say "  出报告：                ./scripts/hc report weekly"
say ""
