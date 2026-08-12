"""`examples/` 的契约测试。

样例文件最常见的下场是**慢慢烂掉**：格式改了、字段名换了，样例还停在旧版，
照着抄的人得到一份跑不通的东西 —— 那比没有样例更浪费时间。
所以这里把样例当代码测。

还有一条更要命的：**样例绝不能待在 `data/` 里。**
`store` 是按 `data/**/*.jsonl` 通配读的，样例放进去会被当成真实体重、
真实训练算进趋势 —— 一个凭空多出来的体重点会污染目标摄入量的 7 日均值。

反方向的漏也一样致命：`examples/` **进版本库**，而 `data/` / `profile/` 永不进。
真实记录被抄进样例就等于把健康数据公开推出去了，而且从此看不出来 ——
所以下面钉住「样例里的数字必须齐整到一眼假」。
"""

from __future__ import annotations

import json

import pytest

from health_assistant import manual
from health_assistant.config import ROOT

EXAMPLES = ROOT / "examples"

# 和 data-map.md §2 那几张表一致。样例用到表外的取值就是样例写错了。
BODY_TYPES = {"weight", "bodyfat", "lean_mass", "bmi", "weist", "bot", "neck",
              "chest", "hip", "torso_narrowest", "upper_arm", "wrist", "thigh",
              "knee", "calf", "head"}
METRICS = {"steps", "sleep_h", "resting_hr", "hrv", "alcohol", "water",
           "exercise_min", "bmi"}
MEALS = {"breakfast", "lunch", "dinner", "snack", "other"}

# 样例只用这两个虚构日期。真实记录带着真实日期，钉住日期就等于钉住
# 「这几行不是从 data/ 里抄来的」—— 换成别的日期得先改这里，改的人自然会想一下。
EXAMPLE_DATES = {"2026-01-01", "2026-01-02"}


def _rows(name: str) -> list[dict]:
    path = EXAMPLES / name
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


class TestExamplesStayOutOfData:
    def test_no_example_jsonl_hides_under_data(self):
        """在 data/ 里放样例 = 把假数据喂进分析。这条是硬的。"""
        strays = [p for p in (ROOT / "data").rglob("*.jsonl")
                  if "example" in p.name.lower() or "sample" in p.name.lower()]
        assert not strays, (
            f"这些文件会被 store 当成真实数据读进去：{[str(p) for p in strays]}　"
            f"样例请放 examples/")


class TestExamplesCarryNoRealRecords:
    """`examples/` 进版本库，`data/` 和 `profile/` 永不进 —— 方向别搞反。

    这里没法直接比对 `data/`（那些文件在 CI 里根本不存在），所以退一步钉日期。
    """

    @pytest.mark.parametrize("name", ["body.jsonl", "apple-health.jsonl",
                                      "meals.jsonl"])
    def test_dates_are_the_fabricated_ones(self, name):
        for r in _rows(name):
            assert r["date"] in EXAMPLE_DATES, (
                f"{name} 里出现了 {r['date']} —— 样例日期只能是 {EXAMPLE_DATES}。"
                f"这条像是从真实记录里抄的，抄进 examples/ 就是公开推出去了")


class TestWorkoutShorthand:
    def test_the_example_parses_cleanly(self):
        text = (EXAMPLES / "workout.txt").read_text(encoding="utf-8")
        r = manual.parse(text)
        errors = [i for i in r.issues if i.severity == "error"]
        assert not errors, f"样例自己都解析不了：{[str(e) for e in errors]}"
        assert r.ok and r.session

    def test_the_example_shows_off_the_syntax_it_documents(self):
        """样例的价值在于覆盖那几个容易写错的写法，掉了就补回来。"""
        r = manual.parse((EXAMPLES / "workout.txt").read_text(encoding="utf-8"))
        s = r.session
        assert s["title"], "标题行没解析出来 —— 说明 # 那一行被后面的注释顶掉了"
        assert s["date"] == "2026-01-01"
        assert len(s["movements"]) >= 5
        assert any(st.get("kind") == "warmup"
                   for mv in s["movements"] for st in mv["sets"]), "没演示热身组 ~"
        assert any(st.get("rpe") for mv in s["movements"] for st in mv["sets"]), \
            "没演示 @RPE"

    def test_trailing_comments_do_not_hijack_the_header(self):
        """样例末尾那段说明是整行 #，它不该改掉日期或标题。"""
        r = manual.parse((EXAMPLES / "workout.txt").read_text(encoding="utf-8"))
        assert r.session["title"] == "胸+三头"


class TestJsonlExamples:
    @pytest.mark.parametrize("name", ["body.jsonl", "apple-health.jsonl",
                                      "meals.jsonl"])
    def test_every_line_is_valid_json(self, name):
        assert _rows(name), f"{name} 是空的"

    def test_body_uses_known_types_and_the_historical_spellings(self):
        rows = _rows("body.jsonl")
        assert {r["type"] for r in rows} <= BODY_TYPES
        # weist / bot 是训记的历史拼写。样例里"改正"成 waist/hip 会教坏人，
        # 而且写回训记时对不上字段。
        assert any(r["type"] == "weist" for r in rows), "样例得演示腰围的拼写"
        for r in rows:
            assert r["type"] not in ("waist",), "腰围是 weist，不是 waist"
            assert {"schema", "id", "date", "type", "value", "unit"} <= set(r)

    def test_body_ids_are_unique(self):
        """id 是去重键。样例里撞了，照抄的人会莫名其妙丢数据。"""
        ids = [r["id"] for r in _rows("body.jsonl")]
        assert len(ids) == len(set(ids))

    def test_metrics_use_known_names(self):
        rows = _rows("apple-health.jsonl")
        assert {r["metric"] for r in rows} <= METRICS
        assert len({(r["date"], r["metric"]) for r in rows}) == len(rows), \
            "(date, metric) 是去重键，样例里不该有重复"

    def test_meals_use_known_slots_and_honest_confidence(self):
        rows = _rows("meals.jsonl")
        assert {r["meal"] for r in rows} <= MEALS
        conf = {r["confidence"] for r in rows}
        assert conf <= {"measured", "estimated"}
        # 两种都要出现：样例的作用之一就是让人看见「目测的要老实标 estimated」
        assert conf == {"measured", "estimated"}


class TestDiscoverability:
    def test_readme_exists_and_points_back_at_the_data_map(self):
        text = (EXAMPLES / "README.md").read_text(encoding="utf-8")
        assert "data-map.md" in text
        assert "hc log" in text

    def test_the_readme_warns_against_copying_into_data(self):
        """这是这个目录最重要的一句话，掉了就等于埋雷。"""
        text = (EXAMPLES / "README.md").read_text(encoding="utf-8")
        assert "data/" in text and "样例" in text
