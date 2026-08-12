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
bad() { printf "  ❌ %s\n" "$*"; }

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
#
# 仓库**内部**的宿主目录（.claude/ .codex/）一律用**相对**软链。
#
# 为什么：绝对软链会把仓库的绝对路径烧进链接里，项目一改名、一搬家，
# 七个链接同时变成死链 —— 而症状是 agent 那边报「Unknown skill: health-coach」，
# 跟目录改名看不出任何关系，极难定位。2026-08-11 就这么踩过一次：
# 项目从 health-assistant 改名成 selfcoach，每次启动都报错。
# 相对链接跟着仓库一起走，改名搬家都不影响。
#
# 全局目录（~/.claude/skills）在仓库外面，相对路径没有意义，只能用绝对路径 ——
# 那种情况下搬仓库确实需要重跑一次 install.sh，这是无法避免的。
link_skills() {
  local target="$1" label="$2" relative="${3:-}"
  mkdir -p "$target"
  local n=0 fixed=0 kept=0 total=0
  for d in "$HERE"/skills/*/; do
    [ -d "$d" ] || continue          # skills/ 空时通配符会原样留下，别去链一个 `*`
    total=$((total+1))
    local name; name="$(basename "$d")"
    local dest="$target/$name"
    local src="$d"
    # $target 是 $HERE/.claude/skills，回到仓库根要退两级
    [ -n "$relative" ] && src="../../skills/$name"

    if [ -L "$dest" ]; then
      # 已经是软链：断了或指错了都重建。**断链要单独计数并说出来** ——
      # 静默修好等于下次还会踩，用户永远不知道发生过什么。
      [ -e "$dest" ] || fixed=$((fixed+1))
      rm -f "$dest"
    elif [ -e "$dest" ]; then
      warn "$label/$name 已存在且不是软链，跳过"; kept=$((kept+1)); continue
    fi
    ln -s "$src" "$dest" && n=$((n+1))
  done

  # n=0 是「软链一个都没建上」唯一的信号，不能吞掉。
  #
  # 之前这里是 `[ "$n" -gt 0 ] && ok …` 后面补一句 `return 0` —— 那句 return
  # 是为了让假值不触发 set -e，代价是把失败也一起抹平了：仓库放在不支持软链的
  # 卷上（exFAT / SMB / 某些容器挂载）时七次 ln 全失败，两行提示都不打印，
  # 脚本继续跑完还打出「下一步：…」并 exit 0。用户以为装好了，
  # 而报错出现在 agent 那边（Unknown skill: health-coach），跟安装看不出关系。
  # 改成显式 if：成功路径照旧，失败路径重新变成硬失败。
  if [ "$n" -gt 0 ]; then
    ok "${label}：链接了 $n 个 skill"
    [ "$fixed" -gt 0 ] && warn "其中 $fixed 个原来是**死链**（多半是项目改过名或搬过家），已重建"
    return 0
  fi
  if [ "$total" -gt 0 ] && [ "$kept" -eq "$total" ]; then
    # 每个位置都已经有同名的真实目录（有人手工拷过去的）—— skill 能用，
    # 只是不会跟着仓库更新。这不是失败，但要说出来。
    warn "${label}：$kept 个位置本来就是真实目录，没建任何软链 —— 它们不会跟着仓库更新"
    return 0
  fi
  bad "${label}：一个 skill 都没链上（skills/ 下有 $total 个）"
  [ "$fixed" -gt 0 ] && bad "原来那 $fixed 个死链已经删掉，也没能重建 —— 现在比之前更空"
  say "     可能的原因：这个卷不支持软链（exFAT / SMB / 某些容器挂载），"
  say "     或者 $target 没有写权限。"
  say "     绕过办法：把仓库放到 APFS / ext4 上，或手工把 skills/ 下各目录拷进"
  say "     $target —— 拷贝能用，但以后不会跟着仓库更新。"
  return 1
}

case "${1:-auto}" in
  auto)
    link_skills "$HERE/.claude/skills" ".claude/skills（Claude Code 项目级）" relative
    ;;
  global)
    link_skills "$HOME/.claude/skills" "~/.claude/skills（Claude Code 全局）"
    ;;
  codex)
    link_skills "$HERE/.codex/skills" ".codex/skills" relative
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
say "          语法和样例      ./scripts/hc log --syntax   ·  examples/README.md"
say "  出报告：                ./scripts/hc report weekly"
say ""
