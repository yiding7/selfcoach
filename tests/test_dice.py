"""食物骰子的回归测试。

骰子这东西「看起来能跑」和「可以信」差得很远 —— 它每次只给一个答案，
错了也不容易被发现。所以测试盯的是六条不变量，全都是「错了会伤人」那一类：

1. **忌口是硬墙** —— 不管摇多少次、权重多高，忌口的东西不能出现
2. **医学禁忌是另一堵硬墙，且和忌口分开** —— 指标恢复能解除，忌口不能
3. **破戒额度跟着阶段走** —— 不许有一个写死的 1/月
4. **额度按「已定下的那次」算** —— 重摇作废的那次要退回额度
5. **只追加** —— 重摇不能删掉旧记录
6. **偏好只调概率，不做过滤** —— 软的东西不许悄悄掏空池子

另外守两条数据约束：池子里不许出现热量/克数字段（那是 nutrition-coach
明确禁止的编数据行为），以及嘌呤分档必须和 purine-reference.json 对得上。
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from health_assistant import dice

D = dt.date(2026, 8, 8)


def _pool(*dishes: dict) -> dict:
    return {"schema": "ha.dishpool/2", "dishes": list(dishes)}


def _dish(name, tier="绿", purine="低", protein="高", **kw) -> dict:
    return {
        "name": name, "tier": tier, "purine": purine, "protein": protein,
        "cuisine": kw.get("cuisine", "中餐"),
        "effort": kw.get("effort", "中等"),
        "scenes": kw.get("scenes", list(dice.SCENES)),
        "slots": kw.get("slots", ["午", "晚"]),
        "contains": kw.get("contains", []),
        "flags": kw.get("flags", []),
        "fix": kw.get("fix", []),
        "note": kw.get("note", ""),
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把所有读写重定向到 tmp_path。返回一个小 helper。"""
    paths = {
        "pool": tmp_path / "dish-pool.json",
        "local": tmp_path / "dish-pool.local.json",
        "log": tmp_path / "dice.jsonl",
        "constraints": tmp_path / "health-constraints.md",
        "profile": tmp_path / "profile.json",
    }
    monkeypatch.setattr(dice, "POOL_PATH", paths["pool"])
    monkeypatch.setattr(dice, "LOCAL_POOL_PATH", paths["local"])
    monkeypatch.setattr(dice, "LOG_PATH", paths["log"])
    monkeypatch.setattr(dice, "CONSTRAINTS_PATH", paths["constraints"])
    monkeypatch.setattr(dice, "PROFILE_PATH", paths["profile"])

    class Env:
        def __init__(self):
            self.paths = paths

        def write_pool(self, *dishes):
            paths["pool"].write_text(json.dumps(_pool(*dishes), ensure_ascii=False),
                                     encoding="utf-8")

        def write_avoid(self, *items):
            paths["constraints"].write_text(
                "# 约束\n\n## 用药\n\n无。\n\n## 忌口\n\n"
                f"不吃：{'、'.join(items)}。无过敏。\n",
                encoding="utf-8")

        def write_diet(self, **kw):
            paths["profile"].write_text(json.dumps({"diet": kw}, ensure_ascii=False),
                                        encoding="utf-8")

        def log_lines(self):
            if not paths["log"].exists():
                return []
            return [json.loads(x) for x in
                    paths["log"].read_text(encoding="utf-8").splitlines() if x.strip()]

    return Env()


# ── 1. 忌口是硬墙 ───────────────────────────────────────────────────────


class TestAvoidIsAHardWall:
    def test_never_rolls_an_avoided_dish_across_many_seeds(self, env):
        # 刻意让忌口那道菜拿到最高权重（绿灯 + 高蛋白 + 低嘌呤），
        # 唯一能拦住它的只有忌口本身。
        env.write_pool(_dish("凉拌木耳", contains=["木耳"]), _dish("鸡胸沙拉"))
        env.write_avoid("木耳")

        for seed in range(200):
            r = dice.roll(slot="午", today=D, seed=seed)
            assert r["dish"]["name"] == "鸡胸沙拉", f"seed {seed} 漏出了忌口"

    def test_matches_avoid_in_the_dish_name_too(self, env):
        """contains 没标全也要拦住 —— 手写池子时漏标 contains 太容易了。"""
        env.write_pool(_dish("鱼香茄子盖饭"), _dish("鸡胸沙拉"))
        env.write_avoid("茄子")
        assert dice.roll(slot="午", today=D, seed=1)["dish"]["name"] == "鸡胸沙拉"

    def test_parses_avoid_from_the_constraints_markdown(self, env):
        env.write_avoid("青椒", "茄子", "海带", "木耳")
        walls = dice.load_avoid()
        assert walls.avoid == ["青椒", "茄子", "海带", "木耳"]
        assert walls.allergy == [] and walls.warn is None

    def test_warns_loudly_when_there_is_no_constraint_file(self, env):
        env.write_pool(_dish("鸡胸沙拉"))
        walls = dice.load_avoid()
        assert walls.avoid == [] and walls.warn and "忌口过滤没有生效" in walls.warn
        assert "忌口过滤没有生效" in dice.render(dice.roll(slot="午", today=D, seed=1))

    def test_profile_json_can_extend_but_not_replace_the_markdown(self, env):
        env.write_avoid("木耳")
        env.write_diet(avoid=["香菜"])
        assert dice.load_avoid().avoid == ["木耳", "香菜"]


# ── 2. 医学禁忌是另一堵墙 ───────────────────────────────────────────────


class TestMedicalBlocks:
    def test_blocks_by_flag_not_by_dish_name(self, env):
        """池子存中性事实，profile 决定拦什么 —— 这样池子才能分享。"""
        env.write_pool(_dish("爆炒腰花", tier="红", purine="极高", flags=["内脏为主"]),
                       _dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(medical_blocks=["内脏为主"])

        for seed in range(200):
            assert dice.roll(slot="午", today=D, seed=seed)["dish"]["name"] == "鸡胸沙拉"

    def test_is_separate_from_the_breakable_quota(self, env):
        """医学禁忌压根不进池子，所以它不消耗额度 —— 这是它和红灯的根本区别。"""
        env.write_pool(_dish("毛血旺", tier="红", purine="极高", flags=["内脏为主"]))
        env.write_avoid()
        env.write_diet(medical_blocks=["内脏为主"], phase="维持")

        r = dice.roll(slot="午", today=D, seed=1)
        assert r["dish"] is None, "被医学禁忌拦掉的菜不该还能摇出来"
        assert r["quota_left"] == 2, "医学禁忌不该消耗破戒额度"
        assert r["dropped"]["医学禁忌"] == 1

    def test_allow_red_does_not_override_a_medical_block(self, env):
        """--allow-red 放开的是目标层，不是医学层。这两层的硬度不一样。"""
        env.write_pool(_dish("爆炒腰花", tier="红", purine="极高", flags=["内脏为主"]))
        env.write_avoid()
        env.write_diet(medical_blocks=["内脏为主"])
        assert dice.roll(slot="午", today=D, seed=1, allow_red=True)["dish"] is None

    def test_lifting_the_block_puts_the_dish_back(self, env):
        """指标恢复就该解除 —— 这是医学禁忌和忌口最大的区别。"""
        env.write_pool(_dish("爆炒腰花", tier="黄", purine="中", flags=["内脏为主"]))
        env.write_avoid()

        env.write_diet(medical_blocks=["内脏为主"])
        assert dice.roll(slot="午", today=D, seed=1)["dish"] is None

        env.write_diet(medical_blocks=[])       # 尿酸回到正常区间
        assert dice.roll(slot="午", today=D, seed=1)["dish"]["name"] == "爆炒腰花"

    def test_an_unblocked_flag_is_harmless(self, env):
        """标了 flag 但没人拦它，就该照常摇 —— 标签本身不代表禁止。"""
        env.write_pool(_dish("烧烤配啤酒", flags=["酒精"]))
        env.write_avoid()
        env.write_diet(medical_blocks=["内脏为主"])
        assert dice.roll(slot="午", today=D, seed=1)["dish"]["name"] == "烧烤配啤酒"


# ── 3. 破戒额度跟着阶段走 ───────────────────────────────────────────────


class TestPhaseDrivenQuota:
    def test_each_phase_has_its_own_quota(self, env):
        for phase, expected in [("减脂", 1), ("维持", 2), ("增肌", 4)]:
            env.write_diet(phase=phase)
            assert dice.load_phase() == (phase, False)
            assert dice.red_quota(phase) == expected, f"{phase} 的额度不对"

    def test_no_hardcoded_single_quota_anywhere(self):
        """这条是给未来的自己看的：三个阶段的额度必须真的不同。"""
        quotas = {p: c["quota"] for p, c in dice.PHASES.items()}
        assert len(set(quotas.values())) == 3, f"阶段之间没有区分度：{quotas}"
        assert quotas["减脂"] < quotas["维持"] < quotas["增肌"]

    def test_phases_differ_in_how_much_they_punish_yellow(self, env):
        """增肌期要吃够，把黄灯压得和减脂期一样低是帮倒忙。"""
        w = {p: c["tier_w"]["黄"] for p, c in dice.PHASES.items()}
        assert w["减脂"] < w["维持"] < w["增肌"]

    def test_protein_stays_the_top_weight_in_every_phase(self):
        """无论哪个阶段，高蛋白都该是最大的加权项 —— 这是骰子存在的理由。"""
        for phase, cfg in dice.PHASES.items():
            assert cfg["protein_w"]["高"] > max(cfg["tier_w"].values()), phase

    def test_explicit_quota_overrides_the_phase_default(self, env):
        env.write_diet(phase="减脂", red_quota_per_month=3)
        assert dice.red_quota("减脂") == 3

    def test_missing_phase_falls_back_visibly(self, env):
        """兜底必须能被看见 —— 阶段错了，额度和权重全错，而且是无声的。"""
        env.write_pool(_dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet()
        phase, defaulted = dice.load_phase()
        assert phase == dice.DEFAULT_PHASE and defaulted
        assert "没设阶段" in dice.render(dice.roll(slot="午", today=D, seed=1))

    def test_a_bogus_phase_does_not_crash(self, env):
        env.write_pool(_dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(phase="魔法阶段")
        assert dice.load_phase() == (dice.DEFAULT_PHASE, True)

    def test_quota_is_per_calendar_month(self, env):
        env.write_pool(_dish("火锅", tier="红", purine="高"), _dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(phase="减脂")
        dice.append_roll({"rec": "roll", "date": "2026-07-30", "slot": "晚",
                          "dish": "火锅", "tier": "红", "breakable": True})
        assert dice.roll(slot="晚", today=D, seed=1)["quota_left"] == 1


# ── 破戒池本身 ──────────────────────────────────────────────────────────


class TestBreakable:
    def test_red_and_high_purine_are_the_same_risk_class(self):
        assert dice._breakable(_dish("火锅", tier="红", purine="高"))
        assert dice._breakable(_dish("浓汤", tier="绿", purine="高"))
        assert dice._breakable(_dish("猪肝", tier="绿", purine="极高"))
        assert not dice._breakable(_dish("牛肉面", tier="黄", purine="中"))

    def test_locked_out_once_the_quota_is_spent(self, env):
        env.write_pool(_dish("火锅", tier="红", purine="高"), _dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(phase="减脂")
        dice.append_roll({"rec": "roll", "date": "2026-08-02", "slot": "晚",
                          "dish": "火锅", "tier": "红", "breakable": True})

        for seed in range(200):
            r = dice.roll(slot="晚", today=D, seed=seed)
            assert r["dish"]["name"] == "鸡胸沙拉", f"seed {seed} 越过了额度"
            assert r["quota_left"] == 0

    def test_reachable_while_the_quota_still_has_room(self, env):
        env.write_pool(_dish("火锅", tier="红", purine="高"), _dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(phase="维持")
        names = {dice.roll(slot="晚", today=D, seed=s)["dish"]["name"] for s in range(200)}
        assert names == {"火锅", "鸡胸沙拉"}, "留了额度却摇不出来，等于没留"

    def test_allow_red_overrides_an_exhausted_quota_and_says_so(self, env):
        env.write_pool(_dish("火锅", tier="红", purine="高"))
        env.write_avoid()
        env.write_diet(phase="减脂")
        dice.append_roll({"rec": "roll", "date": "2026-08-02", "slot": "晚",
                          "dish": "火锅", "tier": "红", "breakable": True})

        out = dice.render(dice.roll(slot="晚", today=D, seed=1, allow_red=True))
        assert "已用完" in out and "超出的第 1 次" in out


# ── 4. 额度按「已定下的那次」算 ─────────────────────────────────────────


class TestSettledReplay:
    def test_a_rerolled_meal_refunds_the_quota(self, env):
        """摇到火锅又换掉了，不该算破戒 —— 摇过不等于吃过。"""
        env.write_pool(_dish("火锅", tier="红", purine="高"), _dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(phase="减脂")
        dice.append_roll({"rec": "roll", "date": D.isoformat(), "slot": "晚",
                          "dish": "火锅", "tier": "红", "breakable": True,
                          "rolled_at": "2026-08-08T18:00:00"})
        dice.append_roll({"rec": "roll", "date": D.isoformat(), "slot": "晚",
                          "dish": "鸡胸沙拉", "tier": "绿", "breakable": False,
                          "rolled_at": "2026-08-08T18:01:00"})

        assert dice.red_used("2026-08") == []
        assert dice.roll(slot="晚", today=D, seed=1)["quota_left"] == 1

    def test_only_the_last_roll_of_a_slot_counts(self, env):
        for i, name in enumerate(["A", "B", "C"]):
            dice.append_roll({"rec": "roll", "date": D.isoformat(), "slot": "午",
                              "dish": name, "tier": "绿", "breakable": False,
                              "rolled_at": f"2026-08-08T12:0{i}:00"})
        assert [r["dish"] for r in dice.settled_rolls()] == ["C"]
        assert dice.current_roll(D.isoformat(), "午")["dish"] == "C"

    def test_different_slots_do_not_shadow_each_other(self, env):
        dice.append_roll({"rec": "roll", "date": D.isoformat(), "slot": "午",
                          "dish": "A", "breakable": False, "rolled_at": "t1"})
        dice.append_roll({"rec": "roll", "date": D.isoformat(), "slot": "晚",
                          "dish": "B", "breakable": False, "rolled_at": "t2"})
        assert {r["dish"] for r in dice.settled_rolls()} == {"A", "B"}


# ── 5. 只追加 ───────────────────────────────────────────────────────────


class TestAppendOnly:
    def test_rerolling_does_not_remove_the_old_row(self, env):
        env.write_pool(_dish("A"), _dish("B"), _dish("C"))
        env.write_avoid()

        dice.commit(dice.roll(slot="午", today=D, seed=1))
        before = env.paths["log"].read_text(encoding="utf-8")
        dice.commit(dice.roll(slot="午", today=D, seed=2))
        after = env.paths["log"].read_text(encoding="utf-8")

        assert after.startswith(before), "旧行被改动了"
        assert len(env.log_lines()) == 2

    def test_roll_itself_never_writes(self, env):
        """只读路径必须真的只读 —— 否则 --dry-run 和测试都会污染额度。"""
        env.write_pool(_dish("A"))
        env.write_avoid()
        for _ in range(5):
            dice.roll(slot="午", today=D, seed=1)
        assert not env.paths["log"].exists()


# ── 6. 偏好只调概率 ────────────────────────────────────────────────────


class TestPreferences:
    def test_likes_shift_the_odds_without_filtering(self, env):
        env.write_pool(_dish("牛肉面", tier="黄", protein="中"),
                       _dish("拉面", tier="黄", protein="中"))
        env.write_avoid()
        env.write_diet(likes=["牛肉面"])

        names = [dice.roll(slot="午", today=D, seed=s)["dish"]["name"] for s in range(300)]
        liked = names.count("牛肉面")
        assert liked > 150, f"偏好没起作用：{liked}/300"
        assert "拉面" in names, "偏好把没被偏好的菜从池子里挤没了 —— 那是过滤，不是加权"

    def test_dislikes_are_soft_not_a_filter(self, env):
        env.write_pool(_dish("A"), _dish("B"))
        env.write_avoid()
        env.write_diet(dislikes=["A"])
        names = {dice.roll(slot="午", today=D, seed=s)["dish"]["name"] for s in range(300)}
        assert names == {"A", "B"}, "不爱吃被做成了硬过滤，池子会被悄悄掏空"

    def test_likes_can_target_a_whole_cuisine(self, env):
        env.write_pool(_dish("番茄意面", cuisine="意大利", tier="黄", protein="中"),
                       _dish("牛肉面", cuisine="中餐", tier="黄", protein="中"))
        env.write_avoid()
        env.write_diet(likes=["意大利"])
        n = sum(dice.roll(slot="午", today=D, seed=s)["dish"]["cuisine"] == "意大利"
                for s in range(300))
        assert n > 150

    def test_preference_never_outranks_a_hard_wall(self, env):
        """爱吃 ≠ 能吃。偏好加权不许把忌口或医学禁忌顶开。"""
        env.write_pool(_dish("凉拌木耳", contains=["木耳"]),
                       _dish("爆炒腰花", flags=["内脏为主"]), _dish("鸡胸沙拉"))
        env.write_avoid("木耳")
        env.write_diet(likes=["凉拌木耳", "爆炒腰花"], medical_blocks=["内脏为主"])
        for seed in range(200):
            assert dice.roll(slot="午", today=D, seed=seed)["dish"]["name"] == "鸡胸沙拉"


# ── 分层顺序本身 ────────────────────────────────────────────────────────


class TestLayerOrder:
    def test_the_chain_is_reported_layer_by_layer(self, env):
        """筛选链是这个骰子值得信任的理由，不能悄悄消失。"""
        env.write_pool(_dish("早餐的", slots=["早"]),
                       _dish("忌口的", contains=["木耳"]),
                       _dish("内脏的", flags=["内脏为主"]),
                       _dish("鸡胸沙拉"))
        env.write_avoid("木耳")
        env.write_diet(medical_blocks=["内脏为主"], phase="维持")

        r = dice.roll(slot="午", today=D, seed=1)
        assert r["dropped"]["场景"] == 1
        assert r["dropped"]["忌口"] == 1
        assert r["dropped"]["医学禁忌"] == 1
        assert r["candidates"] == 1

        out = dice.render(r)
        for layer in dice.LAYERS:
            assert layer in out, f"筛选链里少了「{layer}」"

    def test_hard_layers_run_before_soft_ones(self):
        """顺序写死在 LAYERS 里。软的层跑到硬的层前面 = 统计会骗人。"""
        assert dice.LAYERS.index("忌口") < dice.LAYERS.index("医学禁忌")
        assert dice.LAYERS.index("医学禁忌") < dice.LAYERS.index("目标层")
        assert dice.LAYERS.index("目标层") < dice.LAYERS.index("偏好层")


# ── 可复现 ──────────────────────────────────────────────────────────────


class TestReproducible:
    def test_same_seed_same_result(self, env):
        env.write_pool(*[_dish(f"菜{i}", tier="黄", protein="中") for i in range(12)])
        env.write_avoid()
        a = dice.roll(slot="午", today=D, seed=777)
        b = dice.roll(slot="午", today=D, seed=777)
        assert a["dish"]["name"] == b["dish"]["name"]
        assert [x["name"] for x in a["alternates"]] == [x["name"] for x in b["alternates"]]

    def test_the_seed_is_recorded_alongside_the_result(self, env):
        env.write_pool(*[_dish(f"菜{i}") for i in range(5)])
        env.write_avoid()
        r = dice.roll(slot="午", today=D)
        rec = dice.commit(r)
        assert rec["seed"] == r["seed"] and rec["dish"] == r["dish"]["name"]

    def test_the_seed_alone_does_not_promise_replay(self, env):
        """把这条边界钉死，免得 --seed 的说明词又飘回「复现某一次」。

        种子只在**同一份池子 + 同一段历史**下可复现。摇完写了日志，
        历史就变了，同一个种子给出不同结果 —— 这是对的。
        想知道某天摇到了什么，看 `hc dice log`，不要靠重放种子。
        """
        env.write_pool(*[_dish(f"菜{i}") for i in range(5)])
        env.write_avoid()
        first = dice.roll(slot="午", today=D, seed=99)
        dice.commit(first)
        again = dice.roll(slot="午", today=D, seed=99)
        assert again["dish"]["name"] != first["dish"]["name"]
        assert dice.current_roll(D.isoformat(), "午")["dish"] == first["dish"]["name"]


# ── 加权与过滤的其余行为 ────────────────────────────────────────────────


class TestWeighting:
    def test_protein_density_actually_biases_the_outcome(self, env):
        env.write_pool(_dish("高蛋白", protein="高"), _dish("低蛋白", protein="低"))
        env.write_avoid()
        hi = sum(dice.roll(slot="午", today=D, seed=s)["dish"]["name"] == "高蛋白"
                 for s in range(400))
        assert hi > 300, f"高蛋白只占 {hi}/400，权重没起作用"

    def test_recently_rolled_dishes_drop_out(self, env):
        env.write_pool(_dish("A"), _dish("B"))
        env.write_avoid()
        dice.append_roll({"rec": "roll", "date": D.isoformat(), "slot": "晚",
                          "dish": "A", "breakable": False, "rolled_at": "t"})
        for seed in range(50):
            assert dice.roll(slot="午", today=D, seed=seed)["dish"]["name"] == "B"

    def test_a_dish_from_three_weeks_ago_is_back_in_play(self, env):
        env.write_pool(_dish("A"))
        env.write_avoid()
        dice.append_roll({"rec": "roll", "date": "2026-07-15", "slot": "晚",
                          "dish": "A", "breakable": False, "rolled_at": "t"})
        assert dice.roll(slot="午", today=D, seed=1)["dish"]["name"] == "A"

    def test_slot_scene_cuisine_effort_are_hard_filters(self, env):
        env.write_pool(
            _dish("早餐的", slots=["早"]),
            _dish("聚餐的", scenes=["聚餐"]),
            _dish("意面", cuisine="意大利"),
            _dish("费事的", effort="费事"),
            _dish("目标", cuisine="中餐", effort="快手", slots=["午"], scenes=["外卖"]),
        )
        env.write_avoid()
        for seed in range(50):
            r = dice.roll(slot="午", scene="外卖", cuisine="中餐", effort="快手",
                          today=D, seed=seed)
            assert r["dish"]["name"] == "目标"

    def test_no_candidates_returns_a_usable_empty_result(self, env):
        env.write_pool(_dish("早餐的", slots=["早"]))
        env.write_avoid()
        r = dice.roll(slot="晚", today=D, seed=1)
        assert r["dish"] is None
        out = dice.render(r)
        assert "没有符合条件的菜" in out
        assert "筛选链" in out, "空结果更需要说清是被哪一层筛掉的"


class TestLocalPool:
    def test_local_entry_overrides_the_shared_one(self, env):
        env.write_pool(_dish("牛肉面", tier="黄", protein="中"))
        env.write_avoid()
        dice.add_dish(_dish("牛肉面", tier="绿", protein="高", note="楼下那家肉给得多"))
        pool = dice.load_pool()
        assert len(pool) == 1
        assert pool[0]["tier"] == "绿" and pool[0]["source"] == "local"

    def test_local_can_drop_a_shared_dish(self, env):
        env.write_pool(_dish("火锅", tier="红"), _dish("鸡胸沙拉"))
        env.paths["local"].write_text(
            json.dumps({"dishes": [{"name": "火锅", "drop": True}]}, ensure_ascii=False),
            encoding="utf-8")
        assert [d["name"] for d in dice.load_pool()] == ["鸡胸沙拉"]

    def test_rejects_bad_values(self, env):
        with pytest.raises(ValueError):
            dice.add_dish(_dish("某菜", tier="橙"))
        with pytest.raises(ValueError):
            dice.add_dish(_dish("某菜", purine="巨高"))
        with pytest.raises(ValueError):
            dice.add_dish({"name": "", "tier": "绿"})

    def test_rejects_unknown_flags(self, env):
        """flag 打错字会静默地让医学禁忌层失效，必须当场报错。"""
        with pytest.raises(ValueError, match="未知 flag"):
            dice.add_dish(_dish("某菜", flags=["内脏"]))


# ── 随仓库发布的数据本身 ────────────────────────────────────────────────


def _shipped(name: str) -> dict:
    from health_assistant.config import KNOWLEDGE_DIR
    return json.loads((KNOWLEDGE_DIR / "nutrition" / name).read_text(encoding="utf-8"))


class TestShippedPool:
    def test_carries_no_fabricated_nutrition_numbers(self):
        """分档可以，克数和热量不行 —— 那是编数据，nutrition-coach 明确禁止。"""
        banned = {"kcal", "calories", "protein_g", "fat_g", "carb_g", "amount", "grams"}
        for d in _shipped("dish-pool.json")["dishes"]:
            assert not (banned & set(d)), f"{d['name']} 带了不该有的营养数字"

    def test_every_dish_is_well_formed(self):
        raw = _shipped("dish-pool.json")
        assert len(raw["dishes"]) >= 100, "池子太小，骰子会很快开始重复"
        names = [d["name"] for d in raw["dishes"]]
        assert len(names) == len(set(names)), "有重名的菜，个人池覆盖会出意外"
        for d in raw["dishes"]:
            assert d["tier"] in dice.TIERS, d["name"]
            assert d["purine"] in dice.PURINES, d["name"]
            assert d["protein"] in dice.PROTEINS, d["name"]
            assert d["effort"] in dice.EFFORTS, d["name"]
            assert set(d["slots"]) <= set(dice.SLOTS), d["name"]
            assert set(d["scenes"]) <= set(dice.SCENES), d["name"]
            assert set(d["flags"]) <= set(dice.FLAGS), f"{d['name']} 有未知 flag"

    def test_covers_every_slot_scene_and_several_cuisines(self):
        """任一餐次/场景摇不出东西，用户第一次用就会撞上空结果。"""
        pool = dice.load_pool()
        for slot in dice.SLOTS:
            assert any(slot in d["slots"] for d in pool), f"{slot} 没有候选"
        for scene in dice.SCENES:
            assert any(scene in d["scenes"] for d in pool), f"{scene} 没有候选"
        assert len(dice.cuisines()) >= 8, "菜系太少，用户会一直吃到同一类"
        assert sum(d["effort"] == "快手" for d in pool) >= 15, "快手菜太少"

    def test_organ_and_broth_dishes_are_flagged(self):
        """没标 flag 的内脏菜 = 医学禁忌层漏掉它。这是安全相关的。"""
        for d in _shipped("dish-pool.json")["dishes"]:
            if d["purine"] == "极高":
                assert d["flags"], f"{d['name']} 是极高嘌呤却没有任何 flag"


class TestShippedPurineReference:
    def test_thresholds_are_ordered_and_documented(self):
        t = _shipped("purine-reference.json")["tiers"]
        assert t["低"]["max"] == 100
        assert t["中"]["min"] == 100 and t["中"]["max"] == 200
        assert t["高"]["min"] == 200 and t["高"]["max"] == 300
        assert t["极高"]["min"] == 300

    def test_every_food_tier_matches_its_measured_value(self):
        """分档和数值对不上，整张表就没有意义了。"""
        ref = _shipped("purine-reference.json")
        tiers = ref["tiers"]
        for f in ref["foods"]:
            lo, hi = f["mg_per_100g"]
            assert lo <= hi, f["name"]
            mid = (lo + hi) / 2
            t = tiers[f["tier"]]
            assert t.get("min", 0) <= mid < t.get("max", 10**9), \
                f"{f['name']} 标了「{f['tier']}」但实测均值 {mid}"

    def test_it_carries_a_citable_source(self):
        src = _shipped("purine-reference.json")["source"]
        assert src["url"].startswith("https://") and "usda" in src["url"].lower()

    def test_beer_is_not_labelled_high_purine(self):
        """这是一条真的被改正过的事实，钉住免得又飘回去。

        啤酒实测约 12 mg/100g，属低嘌呤。它伤尿酸靠的是酒精抑制排泄 + 果糖，
        不是嘌呤含量。说成「啤酒嘌呤极高」会让人以为换白酒就安全 —— 恰恰相反。
        """
        ref = _shipped("purine-reference.json")
        beer = next(f for f in ref["foods"] if f["name"] == "啤酒")
        assert beer["tier"] == "低" and max(beer["mg_per_100g"]) < 100
        assert "酒精" in beer["caveat"] and "排泄" in beer["caveat"]


class TestDefaultSlot:
    @pytest.mark.parametrize("hour,expected", [
        (7, "早"), (10, "早"), (11, "午"), (13, "午"),
        (15, "晚"), (19, "晚"), (22, "加餐"),
    ])
    def test_slot_follows_the_clock(self, hour, expected):
        assert dice.default_slot(dt.datetime(2026, 8, 8, hour, 0)) == expected


# ── 评审修复的回归测试 ──────────────────────────────────────────────────
#
# 下面每个类对应 2026-08-09 那轮 code review 里确认的一个缺陷。
# 之前 420 个测试全绿的情况下这些洞都在 —— 因为老测试测的是「我实现的行为」，
# 不是「我承诺的行为」。所以这里一律从**承诺**出发写。


class TestAvoidMatchesComposition:
    """缺陷：忌口只查 contains 和菜名，而 135 道菜里 125 道 contains 是空的。

    菜品池是公开的、不区分用户的，真实食材在 dish-composition.json 里。
    只靠 contains，这堵墙大部分时候形同虚设。
    """

    @pytest.fixture(autouse=True)
    def _comp(self, monkeypatch):
        from health_assistant import nutrition
        monkeypatch.setattr(nutrition, "load_compositions", lambda: {
            "回锅肉": {"猪肉普通部位": 45, "青椒彩椒": 25, "食用油": 30},
            "西班牙海鲜饭": {"米饭": 55, "虾": 15, "鱿鱼": 12, "食用油": 10, "青椒彩椒": 8},
            "沙爹鸡肉串": {"鸡腿肉去皮": 70, "麻酱花生酱": 20, "食用油": 10},
            "鸡胸沙拉": {"鸡胸肉": 60, "绿叶菜": 40},
        })

    def test_blocks_a_dish_whose_composition_carries_the_avoided_ingredient(self, env):
        """回锅肉 contains 是空的、菜名不含「青椒」，但配比里有 25%。"""
        env.write_pool(_dish("回锅肉"), _dish("鸡胸沙拉"))
        env.write_avoid("青椒")
        for seed in range(100):
            assert dice.roll(slot="午", today=D, seed=seed)["dish"]["name"] == "鸡胸沙拉"

    def test_a_trace_amount_still_passes(self, env):
        """8% 的彩椒不该让整道海鲜饭出局 —— 用户明确要求的行为。"""
        env.write_pool(_dish("西班牙海鲜饭"))
        env.write_avoid("青椒")
        assert dice.roll(slot="午", today=D, seed=1)["dish"]["name"] == "西班牙海鲜饭"

    def test_the_threshold_sits_between_those_two_cases(self):
        assert 8 < dice.AVOID_SHARE_THRESHOLD <= 10

    def test_blocked_by_reports_which_term_and_how_much(self, env):
        env.write_avoid("青椒")
        walls = dice.load_avoid()
        term, why = dice.blocked_by(_dish("回锅肉"), walls)
        # 显式标注和实测占比是两种依据，不能都说成「占比」
        _, tagged = dice.blocked_by(_dish("某道盖饭", contains=["青椒"]), walls)
        assert "已标注" in tagged and "占比" not in tagged
        assert term == "青椒" and "占比 25%" in why and "忌口" in why
        assert dice.blocked_by(_dish("西班牙海鲜饭"), walls) is None


class TestAllergyIsZeroTolerance:
    """缺陷：过敏和忌口被当成同一件事。过敏是医学事实，微量也可能出事。"""

    @pytest.fixture(autouse=True)
    def _comp(self, monkeypatch):
        from health_assistant import nutrition
        monkeypatch.setattr(nutrition, "load_compositions", lambda: {
            "沙爹鸡肉串": {"鸡腿肉去皮": 97, "麻酱花生酱": 3},
            "鸡胸沙拉": {"鸡胸肉": 60, "绿叶菜": 40},
        })

    def _write(self, env, avoid, allergy):
        env.paths["constraints"].write_text(
            f"# 约束\n\n## 忌口\n\n不吃：{'、'.join(avoid) or '（无）'}。"
            f"过敏：{'、'.join(allergy) or '（无）'}。\n", encoding="utf-8")

    def test_a_3_percent_trace_is_blocked_when_it_is_an_allergy(self, env):
        env.write_pool(_dish("沙爹鸡肉串"), _dish("鸡胸沙拉"))
        self._write(env, [], ["花生"])
        for seed in range(100):
            assert dice.roll(slot="午", today=D, seed=seed)["dish"]["name"] == "鸡胸沙拉"

    def test_the_same_3_percent_passes_as_a_mere_preference(self, env):
        """同一道菜、同一个词，放在忌口里就该放行 —— 两者硬度不同。"""
        env.write_pool(_dish("沙爹鸡肉串"))
        self._write(env, ["花生"], [])
        assert dice.roll(slot="午", today=D, seed=1)["dish"]["name"] == "沙爹鸡肉串"

    def test_allergies_parse_and_render_separately(self, env):
        self._write(env, ["青椒"], ["虾", "花生"])
        walls = dice.load_avoid()
        assert walls.allergy == ["虾", "花生"] and walls.avoid == ["青椒"]
        env.write_pool(_dish("鸡胸沙拉"))
        assert "⛔虾" in dice.render(dice.roll(slot="午", today=D, seed=1))


class TestSilentFilterFailureIsLoud:
    """缺陷：文件在、但忌口行解析不出来时，静默返回空清单。

    这是这一层最危险的失败模式：看起来一切正常，而过滤已经关了。
    """

    def test_warns_when_the_section_is_missing(self, env):
        env.paths["constraints"].write_text("# 约束\n\n## 用药\n\n无。\n", encoding="utf-8")
        assert "没有「## 忌口」小节" in (dice.load_avoid().warn or "")

    def test_warns_when_the_clause_is_reformatted(self, env):
        env.paths["constraints"].write_text(
            "# 约束\n\n## 忌口\n\n- 青椒\n- 茄子\n", encoding="utf-8")
        walls = dice.load_avoid()
        assert walls.avoid == [] and walls.warn and "没有生效" in walls.warn

    def test_an_explicitly_empty_list_is_not_a_failure(self, env):
        """「确实什么都不忌口」和「解析坏了」必须能区分开。"""
        env.paths["constraints"].write_text(
            "# 约束\n\n## 忌口\n\n不吃：（无）。\n", encoding="utf-8")
        walls = dice.load_avoid()
        assert walls.avoid == [] and walls.warn is None


class TestRerollQuotaAccounting:
    """缺陷：--again 时，即将被顶替的那次仍占额度，红灯菜被悄悄清出池子。"""

    def _seed_red(self, env, date="2026-08-08", slot="晚"):
        dice.append_roll({"rec": "roll", "date": date, "slot": slot,
                          "dish": "火锅", "tier": "红", "breakable": True,
                          "rolled_at": f"{date}T18:00:00"})

    def test_again_does_not_count_the_roll_it_replaces(self, env):
        env.write_pool(_dish("火锅", tier="红", purine="高"), _dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(phase="减脂")          # 额度 1
        self._seed_red(env)

        without = dice.roll(slot="晚", today=D, seed=1)
        assert without["quota_left"] == 0, "不重摇时它当然占着额度"

        again = dice.roll(slot="晚", today=D, seed=1, again=True)
        assert again["quota_left"] == 1, "重摇时那一次不该还占着额度"
        assert again["dropped"]["目标层"] == 0, "池子不该被悄悄收窄"

    def test_again_only_excuses_its_own_slot(self, env):
        """别的餐次摇到的破戒仍然算数 —— 只豁免自己那一格。"""
        env.write_pool(_dish("火锅", tier="红", purine="高"), _dish("鸡胸沙拉"))
        env.write_avoid()
        env.write_diet(phase="减脂")
        self._seed_red(env, slot="午")
        assert dice.roll(slot="晚", today=D, seed=1, again=True)["quota_left"] == 0

    def test_settled_rolls_exclude_is_scoped_to_one_key(self, env):
        for slot in ("午", "晚"):
            dice.append_roll({"rec": "roll", "date": D.isoformat(), "slot": slot,
                              "dish": slot, "breakable": False, "rolled_at": "t"})
        kept = dice.settled_rolls(exclude=(D.isoformat(), "午"))
        assert [r["dish"] for r in kept] == ["晚"]


class TestPoolValidation:
    """缺陷：个人池里一个错值就抛裸 KeyError，整个命令不可用。"""

    def test_a_bad_tier_is_dropped_and_reported(self, env):
        env.write_pool(_dish("好菜"))
        env.paths["local"].write_text(
            json.dumps({"dishes": [_dish("坏菜", tier="青")]}, ensure_ascii=False),
            encoding="utf-8")
        env.write_avoid()

        pool, issues = dice.load_pool_with_issues()
        assert [d["name"] for d in pool] == ["好菜"]
        assert any("坏菜" in i and "tier" in i for i in issues)

    def test_the_command_still_works_and_surfaces_the_problem(self, env):
        env.write_pool(_dish("好菜"))
        env.paths["local"].write_text(
            json.dumps({"dishes": [_dish("坏菜", purine="巨高")]}, ensure_ascii=False),
            encoding="utf-8")
        env.write_avoid()

        r = dice.roll(slot="午", today=D, seed=1)   # 不该抛异常
        assert r["dish"]["name"] == "好菜"
        assert "候选池" in dice.render(r) and "坏菜" in dice.render(r)

    @pytest.mark.parametrize("bad", [
        {"tier": "青"}, {"purine": "巨高"}, {"protein": "超"},
        {"effort": "闪电"}, {"flags": ["内脏"]},
    ])
    def test_every_enumerated_field_is_validated(self, env, bad):
        assert dice._validate_dish(dice._normalize_dish(_dish("x", **bad))) is not None

    def test_a_good_dish_passes(self, env):
        assert dice._validate_dish(dice._normalize_dish(_dish("x"))) is None
