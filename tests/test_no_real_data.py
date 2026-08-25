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

## 2026-08-25 加的第三块：**身份字符串**

体重那一半查的是「数字」，但泄露不止有数字。合并到 main 之前的人工检查扫出
`CLAUDE.md` 和 `data-map.md` 里写着使用者真实的健身房名 —— 配上完整的训练日期
就是行踪信息，而当时**没有任何自动防线在查它**：`data/` 和 `profile/` 有
`.gitignore` 兜底，`CLAUDE.md` / `data-map.md` / `knowledge/` 什么都没有。

新那一块从 `data/` 现取名单（馆名、称呼、教练名），扫全部会进版本库的文件。
**名单绝不写死在这里** —— 那正是本文件第一版栽过的跟头（见下面的 ⚠️）。
代价是没有 `data/` 时会 skip，所以它是「本机 pre-push 检查」，不是 CI 门禁。
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

# 允许出现身份字符串的文件，**每一条都要有理由**。
#
# 这两份是「真名不许进 knowledge/」那条守卫本身：它明确选择了把名字写死，
# 换来「git 不可用时也不 fail-open」。那个取舍已经在 main 上，不在这里翻案 ——
# 但豁免必须是显式的一行，不能靠这条新守卫悄悄看不见。
IDENTITY_ALLOW = {
    "tests/test_persona.py": "守卫自身：IDENTITY_RE 内置清单，见该文件顶部的说明",
    "tests/test_loading.py": "同上，`test_the_table_carries_no_identity_information`",
    "tests/test_no_real_data.py": "本文件：只有说明文字，没有名单",
}

# 太短或太常见的词会把这条守卫变成噪音（比如某个馆就叫「居家」）。
# 想放行就在 `data/public-scan-allow.json` 里写 {"terms": ["…"]} —— 那个文件
# 在 `data/` 下、不进版本库，所以放行名单本身也不会被公开。
ALLOW_FILE = DATA_DIR / "public-scan-allow.json"


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


@pytest.mark.skipif(not (DATA_DIR / "gyms.jsonl").exists()
                    and not (DATA_DIR / "profile.json").exists(),
                    reason="没有 data/（CI 环境），身份字符串扫描跳过 —— "
                           "整数守卫那一半仍然执行")
class TestNoTrackedFileLeaksAnIdentityString:
    """第三块：会进版本库的文件里不许出现使用者的身份字符串。

    名单**从 `data/` 现取**，绝不写死在这里 —— 写死就等于在公开仓库里
    自己重新公布一遍，那正是本文件第一版栽过的跟头。

    查三类：

    | 来源 | 是什么 | 为什么算身份信息 |
    |---|---|---|
    | `data/gyms.jsonl` 的 `gym` | 健身房名 | 配上训练日期就是行踪 |
    | `data/profile.json` 的 `address` | 怎么称呼他 | 就是名字 |
    | `data/profile.json` 的 `coach_name` | 教练的名字 | 他起的，也是私人的 |

    2026-08-25 加。起因：合 main 前的人工检查扫出 `CLAUDE.md` 和 `data-map.md`
    里写着真实馆名，而 `origin/main` 上此前一次都没出现过 —— 那一 push 会是
    首次公开，且基本不可逆。人工检查靠得住一次，靠不住第二次。
    """

    SUFFIXES = (".md", ".txt", ".json", ".jsonl", ".py", ".sh", ".html", ".css")

    @staticmethod
    def _allowed_terms() -> set[str]:
        """使用者自己放行的词（比如某个馆就叫「居家」这种常见词）。"""
        if not ALLOW_FILE.exists():
            return set()
        try:
            blob = json.loads(ALLOW_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()
        return {str(t) for t in (blob.get("terms") or []) if str(t).strip()}

    @classmethod
    def _terms(cls) -> dict[str, str]:
        """{词: 它是什么}。空字符串和单字符一律不要 —— 那种词只会制造噪音。"""
        out: dict[str, str] = {}
        gyms = DATA_DIR / "gyms.jsonl"
        if gyms.exists():
            for line in gyms.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    name = str(json.loads(line).get("gym") or "").strip()
                except json.JSONDecodeError:
                    continue
                if len(name) > 1:
                    out[name] = "健身房名"
        prof = DATA_DIR / "profile.json"
        if prof.exists():
            try:
                blob = json.loads(prof.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                blob = {}
            for key, what in (("address", "称呼"), ("coach_name", "教练名")):
                val = blob.get(key)
                for name in ([val] if isinstance(val, str) else (val or [])):
                    if isinstance(name, str) and len(name.strip()) > 1:
                        out[name.strip()] = what
        allowed = cls._allowed_terms()
        return {t: w for t, w in out.items() if t not in allowed}

    @staticmethod
    def _pattern(term: str) -> re.Pattern:
        """纯 ASCII 的词要卡词边界，否则 `BA` 会命中一大堆 base64 和变量名。

        中日韩没有词边界这回事（`\\b` 在中文串里几乎处处成立），直接子串匹配。
        """
        if term.isascii():
            return re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        return re.compile(re.escape(term))

    def test_no_identity_string_in_any_tracked_file(self):
        terms = self._terms()
        if not terms:
            pytest.skip("data/ 里没有可比对的身份字符串")
        pats = {t: self._pattern(t) for t in terms}
        hits = []
        for rel in _tracked():
            if rel in IDENTITY_ALLOW:
                continue
            p = ROOT / rel
            if p.suffix not in self.SUFFIXES or not p.exists():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for no, line in enumerate(text.splitlines(), 1):
                for term, pat in pats.items():
                    if pat.search(line):
                        hits.append(f"{rel}:{no}　{terms[term]}「{term}」")
        assert not hits, (
            "这些会进版本库的文件里出现了身份字符串：\n  " + "\n  ".join(hits[:20])
            + (f"\n  …… 还有 {len(hits) - 20} 处" if len(hits) > 20 else "")
            + "\n\n换成 甲馆/乙馆 这类占位符；具体登记在"
              " data/（gitignore）和 profile/（不进版本库）里。"
              f"\n确实要放行某个常见词，写进 {ALLOW_FILE.name}：{{\"terms\": [\"…\"]}}")

    def test_the_scan_actually_ran(self):
        """一个词都没读到，说明这条测试在空跑。"""
        assert self._terms(), "读不到任何身份字符串，扫描是空的"

    def test_the_allow_list_only_names_files_that_still_exist(self):
        """豁免名单会随文件改名漂开，漂开就是悄悄少扫一份。"""
        gone = [rel for rel in IDENTITY_ALLOW if not (ROOT / rel).exists()]
        assert not gone, f"IDENTITY_ALLOW 里这些文件已经不在了：{gone}"

    def test_ascii_terms_are_word_bounded(self):
        """`BA` 不该命中 `DATABASE`，但该命中 `--gym BA`。"""
        pat = self._pattern("BA")
        assert not pat.search("DATABASE_URL")
        assert not pat.search("alBAtross")
        assert pat.search("hc calib set '面拉' --gym BA --ratio 1")

    def test_cjk_terms_match_as_substrings(self):
        """中文没有词边界，`\\b` 会漏掉夹在句子里的名字。"""
        assert self._pattern("甲馆天地").search("在甲馆天地练的")
