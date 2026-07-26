#!/usr/bin/env python3
"""把训记官方 1092 个动作名预分类成 knowledge/movement-taxonomy.json。

    python3 scripts/build_taxonomy.py            # 从 GitHub 拉取动作名表并生成
    python3 scripts/build_taxonomy.py --review   # 只打印分类结果，供人工过目

生成物随仓库发布，运行时不依赖 GitHub。动作名表只有中文名、没有肌群列，
所以分类完全由 knowledge/movement-rules.json 的关键词规则推导 —— **需要人工复核**。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from health_assistant.taxonomy import (  # noqa: E402
    GROUP_ORDER, UNKNOWN, _rules, sort_groups)

CATALOG_URL = "https://raw.githubusercontent.com/Foveluy/Xunji-movements/HEAD/README.md"
CACHE = ROOT / "knowledge" / ".movement-names.txt"
OUT = ROOT / "knowledge" / "movement-taxonomy.json"

ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|$")


def fetch_names(*, use_cache: bool = True) -> list[str]:
    if use_cache and CACHE.exists():
        return [n for n in CACHE.read_text(encoding="utf-8").splitlines() if n.strip()]
    req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "health-assistant"})
    with urllib.request.urlopen(req, timeout=30) as r:
        md = r.read().decode("utf-8")
    names = []
    for line in md.splitlines():
        m = ROW_RE.match(line.strip())
        if m and m.group(2) not in ("动作中文名", "---"):
            names.append(m.group(2).strip())
    CACHE.write_text("\n".join(names) + "\n", encoding="utf-8")
    return names


def classify_by_rules(name: str) -> tuple[str, str | None]:
    for pattern, group in _rules():
        if pattern.search(name):
            return group, pattern.pattern[:24]
    return UNKNOWN, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true", help="只打印，不写文件")
    ap.add_argument("--refresh", action="store_true", help="忽略本地缓存重新拉取")
    ap.add_argument("--show", help="只显示某个肌群的分类结果")
    args = ap.parse_args()

    names = fetch_names(use_cache=not args.refresh)
    print(f"动作名表：{len(names)} 个\n")

    mapping: dict[str, str] = {}
    by_group: dict[str, list[str]] = defaultdict(list)
    for name in names:
        group, _ = classify_by_rules(name)
        if group != UNKNOWN:
            mapping[name] = group
        by_group[group].append(name)

    counts = Counter({g: len(v) for g, v in by_group.items()})
    for g in sort_groups(by_group):
        print(f"  {g:<5} {counts[g]:>5}")
    covered = len(names) - counts[UNKNOWN]
    print(f"\n  覆盖率 {covered}/{len(names)} = {covered / len(names) * 100:.1f}%")

    if args.show:
        print(f"\n── {args.show} ──")
        for n in by_group.get(args.show, []):
            print(f"  {n}")

    if counts[UNKNOWN]:
        print(f"\n未分类 {counts[UNKNOWN]} 个（需要补规则或人工指定）：")
        for n in by_group[UNKNOWN][:60]:
            print(f"  {n}")
        if counts[UNKNOWN] > 60:
            print(f"  … 还有 {counts[UNKNOWN] - 60} 个")

    if not args.review:
        OUT.write_text(json.dumps({
            "_comment": (
                "由 scripts/build_taxonomy.py 从训记官方动作名表 + "
                "knowledge/movement-rules.json 生成。动作名表本身没有肌群列，"
                "分类由关键词规则推导，已人工复核。"
                "接口返回的 movements[].type 优先级高于本表。"
            ),
            "_source": CATALOG_URL,
            "_count": len(mapping),
            "movements": dict(sorted(mapping.items())),
        }, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        print(f"\n已写入 {OUT.relative_to(ROOT)}（{len(mapping)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
