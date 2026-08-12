"""守卫：进版本库的文件里不许有使用者的真实数据。

仓库是公开的，而 `knowledge/` 和 `skills/` 明确标注「任何人都能用、可公开分享」。
2026-08-11 的审计查出这两个区里有**带日期的真实体重读数**，最早的已经在公开
main 上待了两周多 —— 而当时唯一的守卫只 grep 名字（Tim/Timothy），
从来没查过健康数据。这份文件补的就是那个缺口。

## 为什么要两半

- **精确的一半**：拿 `data/` 里的真实记录逐条比对。最准，但 `data/` 不进版本库，
  CI 里没有 → 只能 skip。
- **永远执行的一半**：示例数值必须是整数（真实测量值几乎总带小数）。
  不依赖 `data/`，所以任何环境都真的在查。

只留精确那一半，在 CI 里就是 fail-open —— 那正是同一天在
`tests/test_persona.py` 里修掉的另一个缺陷，不能在这里重犯一次。
规矩和例外写在 `CLAUDE.md` 的「进版本库的文件里，示例数据一律取整」。
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from health_assistant.config import DATA_DIR, ROOT

# 示例性个人数据所在的位置。这些文件的数值必须是整数。
EXAMPLE_FILES = ("examples/body.jsonl", "examples/apple-health.jsonl",
                 "examples/meals.jsonl")

# 数值字段里允许非整数的键 —— 都不是个人测量值。
# `amount` 是分量、`value`/`kcal`/`protein_g` 这些才是要管的。
FRACTION_OK_KEYS: tuple[str, ...] = ()


def _tracked() -> list[str]:
    """会进版本库的文件 = 已跟踪的 + 还没 add 但也没被忽略的。

    `--others --exclude-standard` 这两个开关是必须的：只用 `git ls-files`
    会漏掉**新建但还没 git add** 的文件，而那恰恰是最危险的时刻 ——
    这个守卫自己第一版就漏检了自己，直到被 add 进索引才暴露出
    docstring 里抄了泄露样本。新文件正是最该在提交前被拦下的东西。
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return out.stdout.split()


def _rows(rel: str) -> list[dict]:
    p = ROOT / rel
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


class TestExampleValuesAreIntegers:
    """永远执行的那一半。不依赖 `data/`，所以在最小 CI 容器里也真的在查。"""

    @pytest.mark.parametrize("rel", EXAMPLE_FILES)
    def test_every_numeric_value_is_a_whole_number(self, rel):
        bad = []
        for row in _rows(rel):
            for key, val in row.items():
                if key in FRACTION_OK_KEYS or not isinstance(val, (int, float)):
                    continue
                if isinstance(val, bool):
                    continue
                if float(val) != int(val):
                    bad.append(f"{key}={val}")
        assert not bad, (
            f"{rel} 里这些值带小数：{bad}　—— 示例数据一律取整。"
            f"带小数是真实测量值的特征，见 CLAUDE.md")

    def test_the_workout_example_has_no_fractional_weights(self):
        """速记样例里的重量也取整。

        注意区别：`manual.py` docstring 里的 `62.5x8` 是**语法演示**（存在的目的
        就是说明小数能填），照留；而 `examples/workout.txt` 是**示例训练数据**，
        取整。CLAUDE.md 的例外表里写明了这条界线。
        """
        text = (ROOT / "examples" / "workout.txt").read_text(encoding="utf-8")
        body = [ln for ln in text.splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
        frac = [m for ln in body for m in re.findall(r"\d+\.\d*[1-9]", ln)]
        assert not frac, f"样例训练里出现带小数的重量：{frac}"

    def test_the_guard_would_catch_a_fractional_value(self, tmp_path):
        """反向验证：过滤器写反了的话，上面几条会变成永远通过的摆设。"""
        bad = [{"type": "weight", "value": 79.85}]
        offenders = [f"{k}={v}" for row in bad for k, v in row.items()
                     if isinstance(v, (int, float)) and float(v) != int(v)]
        assert offenders == ["value=79.85"]


@pytest.mark.skipif(not (DATA_DIR / "body").exists(),
                    reason="没有 data/（CI 环境），精确比对跳过 —— "
                           "整数守卫那一半仍然执行")
class TestNoTrackedFileMatchesARealRecord:
    """精确的一半：`(日期, 数值)` 同现就算命中。

    只在本机跑得起来。真实案例是某份语气文件里一句「低点从 MM-DD 的 XX.X 抬到
    MM-DD 的 YY.Y」—— 两个数都是真实体重读数，日期也是真的。

    ⚠️ **这段说明里不许写出那两个真实数字。** 第一版就是这么栽的：把泄露样本
    原样抄进 docstring 当例子，于是这个文件自己成了新的泄露点，而且被自己抓住。
    """

    DATE = re.compile(r"(?:(\d{4})-)?(\d{1,2})[-/](\d{1,2})")
    NUM = re.compile(r"\d{1,3}\.\d{1,2}")
    SUFFIXES = (".md", ".txt", ".json", ".jsonl", ".py", ".sh")

    @staticmethod
    def _real() -> dict[str, dict[str, float]]:
        real: dict[str, dict[str, float]] = {}
        for path in sorted((DATA_DIR / "body").glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                real.setdefault(r["type"], {})[r["date"]] = r["value"]
        return real

    def test_no_date_plus_real_value_co_occurrence(self):
        real = self._real()
        if not real:
            pytest.skip("data/body 里没有记录")
        hits = []
        for rel in _tracked():
            p = ROOT / rel
            if p.suffix not in self.SUFFIXES or not p.exists():
                continue
            for no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                nums = {float(x) for x in self.NUM.findall(line)}
                if not nums:
                    continue
                for dm in self.DATE.finditer(line):
                    year, month, day = dm.groups()
                    key = f"{int(year or 2026):04d}-{int(month):02d}-{int(day):02d}"
                    for kind, by_day in real.items():
                        if key in by_day and by_day[key] in nums:
                            hits.append(f"{rel}:{no} {key} {kind}={by_day[key]}")
        assert not hits, (
            "这些会进版本库的文件里出现了真实身体记录：\n  " + "\n  ".join(hits))

    def test_the_comparison_actually_ran(self):
        """真实记录一条都没读到，说明这条测试在空跑。"""
        assert self._real(), "读不到任何真实记录，比对是空的"
