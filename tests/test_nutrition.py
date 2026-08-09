"""营养估算与每日目标摄入量的回归测试。

这个模块最大的风险不是算错，是**看起来很准**。它输出的是一串具体数字，
用户没有办法判断哪一位是可信的。所以测试盯的是四件事：

1. **公式对** —— Mifflin-St Jeor 用文献值逐项核对
2. **缺数据时不猜** —— 宁可说「算不出来，缺 X」，也不填一个默认体重
3. **数字能追溯** —— 菜品热量必须由配比算出，任何地方都不许手写
4. **口径不许漂** —— 熟重 vs 生重、7 日均值 vs 单日，错了就是系统性偏差
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from health_assistant import nutrition

D = dt.date(2026, 8, 9)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 profile / body / 步数 / 训练都重定向到 tmp_path。"""
    from health_assistant import dice, store

    profile = tmp_path / "profile.json"
    metrics = tmp_path / "metrics.jsonl"

    monkeypatch.setattr(nutrition, "PROFILE_PATH", profile)
    monkeypatch.setattr(nutrition, "METRICS_PATH", metrics)
    monkeypatch.setattr(dice, "PROFILE_PATH", profile)

    state = {"body": [], "sessions": []}
    monkeypatch.setattr(store, "load_body",
                        lambda *a, **k: list(state["body"]))
    monkeypatch.setattr(store, "load_sessions",
                        lambda *a, **k: list(state["sessions"]))

    class Env:
        paths = {"profile": profile, "metrics": metrics}

        def write_profile(self, **kw):
            profile.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")

        def weights(self, *pairs):
            state["body"] = [{"date": d, "type": "weight", "value": v} for d, v in pairs]

        def sessions(self, *dates):
            state["sessions"] = [{"date": d} for d in dates]

        def steps(self, *pairs):
            metrics.write_text(
                "\n".join(json.dumps({"date": d, "metric": "steps", "value": v})
                          for d, v in pairs),
                encoding="utf-8")

    return Env()


# ── 1. 公式 ─────────────────────────────────────────────────────────────


class TestMifflinStJeor:
    def test_male_matches_the_published_formula(self, env):
        """BMR = 10W + 6.25H - 5A + 5。手算一遍钉住。"""
        env.write_profile(sex="male", birth_year=1993, height_cm=178,
                          diet={"phase": "维持", "activity_factor": 1.5})
        env.weights(("2026-08-09", 80.0))

        t = nutrition.targets(D)
        expected = 10 * 80.0 + 6.25 * 178 - 5 * 33 + 5      # = 1752.5
        assert t["bmr"] == round(expected)
        assert t["age"] == 33

    def test_female_uses_the_minus_161_constant(self, env):
        env.write_profile(sex="female", birth_year=1993, height_cm=165,
                          diet={"phase": "维持", "activity_factor": 1.5})
        env.weights(("2026-08-09", 60.0))
        expected = 10 * 60.0 + 6.25 * 165 - 5 * 33 - 161
        assert nutrition.targets(D)["bmr"] == round(expected)

    def test_tdee_is_bmr_times_activity(self, env):
        env.write_profile(sex="male", birth_year=1993, height_cm=178,
                          diet={"phase": "维持", "activity_factor": 1.5})
        env.weights(("2026-08-09", 80.0))
        t = nutrition.targets(D)
        # 容 1 kcal：TDEE 由**未取整**的 BMR 乘出来（1752.5 × 1.5），
        # 而展示的 BMR 已经取整了。先乘后round 是对的，链式取整会积累误差。
        assert abs(t["tdee"] - t["bmr"] * 1.5) <= 1

    def test_maintenance_has_no_adjustment(self, env):
        env.write_profile(sex="male", birth_year=1993, height_cm=178,
                          diet={"phase": "维持", "activity_factor": 1.5})
        env.weights(("2026-08-09", 80.0))
        t = nutrition.targets(D)
        assert t["delta"] == 0 and t["kcal"] == t["tdee"]


class TestPhaseAdjustment:
    @pytest.mark.parametrize("phase,sign", [("减脂", -1), ("维持", 0), ("增肌", 1)])
    def test_direction_follows_the_phase(self, env, phase, sign):
        env.write_profile(sex="male", birth_year=1993, height_cm=178,
                          diet={"phase": phase, "activity_factor": 1.5})
        env.weights(("2026-08-09", 80.0))
        delta = nutrition.targets(D)["delta"]
        assert (delta < 0) if sign < 0 else (delta > 0) if sign > 0 else delta == 0

    def test_deficit_is_clamped_into_a_sane_band(self, env):
        """只用百分比，TDEE 高的人缺口会大到危险；只用固定值，低的人又太狠。"""
        for weight in (55.0, 80.0, 130.0):
            env.write_profile(sex="male", birth_year=1993, height_cm=178,
                              diet={"phase": "减脂", "activity_factor": 1.6})
            env.weights(("2026-08-09", weight))
            d = nutrition.targets(D)["delta"]
            assert -600 <= d <= -300, f"{weight} kg 的缺口 {d} 跑出区间了"

    def test_protein_is_highest_in_a_deficit(self, env):
        """缺口下要保瘦体重，蛋白反而该更高 —— 这条最容易被写反。"""
        assert nutrition.PROTEIN_PER_KG["减脂"] > nutrition.PROTEIN_PER_KG["维持"]

    def test_macros_add_back_up_to_the_calorie_target(self, env):
        env.write_profile(sex="male", birth_year=1993, height_cm=178,
                          diet={"phase": "减脂", "activity_factor": 1.6})
        env.weights(("2026-08-09", 80.0))
        t = nutrition.targets(D)
        total = t["protein_g"] * 4 + t["fat_g"] * 9 + t["carb_g"] * 4
        assert abs(total - t["kcal"]) <= 12, f"宏量加起来 {total} 对不上 {t['kcal']}"


# ── 2. 缺数据时不猜 ─────────────────────────────────────────────────────


class TestRefusesToGuess:
    def test_no_weight_means_no_answer(self, env):
        env.write_profile(sex="male", birth_year=1993, height_cm=178)
        t = nutrition.targets(D)
        assert not t["ok"]
        assert any("体重" in m for m in t["missing"])

    def test_missing_fields_are_named_individually(self, env):
        env.write_profile()
        t = nutrition.targets(D)
        assert not t["ok"] and len(t["missing"]) == 4
        assert "算不出来" in nutrition.render_targets(t)

    def test_render_points_at_the_fix(self, env):
        env.write_profile()
        assert "hc setup" in nutrition.render_targets(nutrition.targets(D))


# ── 3. 体重口径 ─────────────────────────────────────────────────────────


class TestWeightBasis:
    def test_uses_a_seven_day_mean_not_the_latest_reading(self, env):
        """日间波动可以到 ±1.8 kg。用单日读数，目标每天都在跳。"""
        env.write_profile(sex="male", birth_year=1993, height_cm=178,
                          diet={"activity_factor": 1.5})
        env.weights(("2026-08-05", 80.0), ("2026-08-07", 81.0), ("2026-08-09", 82.0))
        w, n, basis = nutrition.recent_weight(D)
        assert w == 81.0 and n == 3 and "均值" in basis

    def test_falls_back_to_the_latest_reading_when_the_window_is_empty(self, env):
        env.write_profile(sex="male", birth_year=1993, height_cm=178)
        env.weights(("2026-06-01", 85.0))
        w, n, basis = nutrition.recent_weight(D)
        assert (w, n) == (85.0, 1)
        # 口径变了就必须说出来 —— 不能把单次读数说成 7 日均值
        assert "单次读数" in basis and "不是" in basis

    def test_ignores_readings_after_today(self, env):
        """按历史日期回算时，不能把「未来」的体重算进去。"""
        env.write_profile(sex="male", birth_year=1993, height_cm=178)
        env.weights(("2026-08-09", 80.0), ("2026-08-20", 70.0))
        w, n, _ = nutrition.recent_weight(D)
        assert w == 80.0 and n == 1


# ── 活动系数 ────────────────────────────────────────────────────────────


class TestActivityFactor:
    def test_derived_from_measured_steps(self, env):
        env.write_profile()
        env.steps(*[(f"2026-08-{d:02d}", 13000) for d in range(1, 10)])
        a = nutrition.activity_factor(D)
        assert a["source"] == "由数据推算"
        assert a["value"] >= 1.55 and "13,000 步" in a["detail"]

    def test_uses_the_median_so_one_hike_cannot_skew_it(self, env):
        env.write_profile()
        env.steps(("2026-08-01", 3000), ("2026-08-02", 3000),
                  ("2026-08-03", 3000), ("2026-08-04", 40000))
        assert nutrition.activity_factor(D)["steps"] == 3000

    def test_training_adds_a_capped_bonus(self, env):
        """力量训练的净消耗被高估得厉害，不该把系数顶到 1.9。"""
        env.write_profile()
        env.steps(("2026-08-01", 3000))
        env.sessions(*[f"2026-07-{d:02d}" for d in range(10, 31)])
        a = nutrition.activity_factor(D)
        assert a["value"] <= 1.20 + nutrition.TRAIN_BONUS_CAP + 1e-9

    def test_explicit_setting_wins_and_says_so(self, env):
        env.write_profile(diet={"activity_factor": 1.9})
        a = nutrition.activity_factor(D)
        assert a["value"] == 1.9 and a["source"] == "手动设定"

    def test_falls_back_visibly_without_step_data(self, env):
        env.write_profile()
        a = nutrition.activity_factor(D)
        assert a["value"] == pytest.approx(nutrition.DEFAULT_ACTIVITY, abs=0.16)
        assert "无步数数据" in a["detail"]

    def test_never_exceeds_the_cap(self, env):
        env.write_profile()
        env.steps(("2026-08-01", 99999))
        env.sessions(*[f"2026-07-{d:02d}" for d in range(1, 29)])
        assert nutrition.activity_factor(D)["value"] <= nutrition.ACTIVITY_CAP


# ── 4. 菜品营养必须算出来 ───────────────────────────────────────────────


class TestDishNutrition:
    def test_computed_from_composition_not_hand_written(self):
        """手算一道两成分的菜，验证是加权平均而不是别的什么。"""
        foods = nutrition.load_foods()
        comp = nutrition.load_compositions()["蒸红薯 + 水煮蛋"]
        n = nutrition.dish_per100g("蒸红薯 + 水煮蛋")
        expected = sum(foods[ing][0] * share for ing, share in comp.items()) / 100
        assert n["kcal"] == round(expected)

    def test_returns_none_for_an_unknown_dish(self):
        """没有配比就不给数字。编一个比不给更糟。"""
        assert nutrition.dish_per100g("根本不存在的菜") is None

    def test_pure_oil_dishes_are_not_low_calorie(self):
        """回归护栏：油如果没被算进去，油炸菜会显得很健康。"""
        assert nutrition.dish_per100g("炸鸡")["kcal"] > nutrition.dish_per100g("白灼虾")["kcal"]

    def test_protein_share_is_a_fraction(self):
        n = nutrition.dish_per100g("白灼虾")
        assert 0.8 < nutrition.protein_share(n) <= 1.0, "白灼虾几乎全是蛋白"
        assert nutrition.protein_share({"kcal": 0, "protein_g": 0}) == 0.0

    def test_high_protein_dishes_really_do_score_higher(self):
        """分档和算出来的数字如果打架，其中一个是错的。"""
        lean = nutrition.protein_share(nutrition.dish_per100g("水煮鸡胸 + 西兰花 + 糙米"))
        carby = nutrition.protein_share(nutrition.dish_per100g("葱油拌面"))
        assert lean > carby * 2


class TestShippedNutritionData:
    @staticmethod
    def _load(name):
        from health_assistant.config import KNOWLEDGE_DIR
        return json.loads((KNOWLEDGE_DIR / name).read_text(encoding="utf-8"))

    def test_every_composition_ingredient_exists(self):
        """配比里写错一个食材名，那道菜就会静默地少算一块。"""
        foods = set(self._load("nutrition-reference.json")["foods"])
        for dish, comp in self._load("dish-composition.json")["compositions"].items():
            unknown = set(comp) - foods
            assert not unknown, f"{dish} 用了参考表里没有的食材：{unknown}"

    def test_every_composition_sums_to_100(self):
        for dish, comp in self._load("dish-composition.json")["compositions"].items():
            assert sum(comp.values()) == 100, f"{dish} 配比合计 {sum(comp.values())}"

    def test_every_dish_in_the_pool_has_a_composition(self):
        """漏一道就是摇到它时没有营养信息，用户不会知道为什么。"""
        pool = {d["name"] for d in self._load("dish-pool.json")["dishes"]}
        comps = set(self._load("dish-composition.json")["compositions"])
        assert not (pool - comps), f"缺配比：{pool - comps}"
        assert not (comps - pool), f"配比里有池子里没有的菜：{comps - pool}"

    def test_macros_are_physically_possible(self):
        """蛋白+脂肪+碳水 的供能不该显著超过标称热量。"""
        for name, v in self._load("nutrition-reference.json")["foods"].items():
            kcal, p, f, c = v
            derived = p * 4 + f * 9 + c * 4
            assert derived <= kcal * 1.35 + 25, f"{name} 宏量({derived}) 远超热量({kcal})"

    def test_it_declares_a_cooked_weight_basis(self):
        """生重当熟重用会系统性低估 25% —— 口径必须写在文件里。"""
        src = self._load("nutrition-reference.json")["source"]
        assert "熟重" in src["basis"]
        assert src["url"].startswith("https://")
