"""负荷口径归一化的回归测试。

这个模块碰的是**别人的训练历史**，所以测试盯的是三件事：

1. 原始文件一个字节都不能改。折算只在读取时发生
2. `ignore` 只关掉对比，**不能顺手把训练量也抹掉** —— 那些组是真的练了
3. 检测不能吵。新手期的正常进步（小重量、长间隔）不该报警，
   否则用户三天就把这个功能关了

夹具里的重量、次数、日期**全是编的，且一律取整** —— 这个仓库是公开的，
一份带小数的负荷或一个真实日期就是一条别人的训练记录。纪律见 `CLAUDE.md`。
"""

from __future__ import annotations

import copy

import pytest

from health_assistant import calibration, store
from health_assistant.analytics.compare import compare_group
from health_assistant.analytics.metrics import session_stats


@pytest.fixture()
def rules_file(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "PATH", tmp_path / "load-calibration.jsonl")
    return tmp_path / "load-calibration.jsonl"


def sess(date: str, weight: float, reps: float, *, name: str = "面拉",
         sets: int = 3, exetype=None, group: str = "肩", gym: str = "甲馆") -> dict:
    """夹具默认带场地 —— 变换类规则挂在「馆 + 动作」上，没有馆就不可能命中。"""
    return {
        "id": f"{date}-{name}", "date": date, "source": "x", "title": "",
        "duration_s": 3000, "gym": gym,
        "movements": [{
            "name": name, "raw_type": group, "exetype": exetype,
            "unilateral": False, "difficulty": None,
            "sets": [{"done": True, "weight_kg": weight, "reps": reps, "rpe": None,
                      "self_weight": False, "left_weight_kg": None}
                     for _ in range(sets)],
        }],
    }


def stats_of(sessions):
    return [session_stats(s, 80.0) for s in calibration.apply_rules(sessions)]


class TestRawDataIsUntouched:
    """最重要的一条：原始记录永远不改。"""

    def test_apply_rules_does_not_mutate_input(self, rules_file):
        raw = [sess("2026-06-01", 50, 10)]
        before = copy.deepcopy(raw)
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="甲馆")
        calibration.apply_rules(raw)
        assert raw == before

    def test_scaling_shows_up_only_in_the_read_path(self, rules_file):
        raw = [sess("2026-06-01", 50, 10)]
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="甲馆")
        st = stats_of(raw)[0]
        assert st.movements[0].top_load_kg == pytest.approx(25.0)
        assert raw[0]["movements"][0]["sets"][0]["weight_kg"] == 50
        # 折算过的一定留痕 —— 一个被悄悄改过的数字比一个明显错的数字危险
        assert st.movements[0].calib_ratio == 0.5

    def test_rules_file_is_append_only(self, rules_file):
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="甲馆")
        first = rules_file.read_text(encoding="utf-8")
        calibration.add_rule("面拉", "confirm", date="2026-06-01")
        after = rules_file.read_text(encoding="utf-8")
        assert after.startswith(first)

    def test_supersede_hides_but_keeps_the_old_rule(self, rules_file):
        old = calibration.add_rule("面拉", "scale", ratio=0.5, gym="甲馆")
        calibration.add_rule("面拉", "scale", ratio=0.25, gym="甲馆", supersedes=old.id)
        live = calibration.load_rules()
        assert [r.ratio for r in live] == [0.25]
        assert old.id in rules_file.read_text(encoding="utf-8")


class TestScopeAndSafety:
    def test_transforms_must_be_bound_to_a_gym(self, rules_file):
        """2026-08-25：变换类规则的作用域只剩「馆 + 动作」，没有日期。

        传动比和滑车自重是那台机器的属性 —— 用日期表达「在哪台机器上练的」，
        每换一次馆都要补一条，补漏一条就静默错一次。
        """
        for kwargs in ({}, {"gym": "", "date": "2026-07-01"}):
            with pytest.raises(ValueError):
                calibration.add_rule("面拉", "scale", ratio=0.5, **kwargs)
        with pytest.raises(ValueError):
            calibration.add_rule("面拉", "scale", ratio=0.5, gym="甲馆",
                                 date="2026-07-01")
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="甲馆")
        rule = calibration.load_rules()[0]
        assert rule.covers("面拉", "甲馆")
        assert not rule.covers("面拉", "乙馆")
        assert not rule.covers("下压", "甲馆")

    @pytest.mark.parametrize("exetype", ["times", "plus_weight", "help", "record"])
    def test_derived_loads_are_never_scaled(self, rules_file, exetype):
        """自重折算、辅助配重（方向相反）、计时类都没有「标称重量」这回事。

        对它们乘系数只会造出一个假数字，所以规则命中了也不动。
        """
        raw = [sess("2026-06-01", 20, 10, name="引体向上（辅助）", exetype=exetype)]
        calibration.add_rule("引体向上（辅助）", "scale", ratio=0.5, gym="甲馆")
        out = calibration.apply_rules(raw)
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 20

    def test_scale_requires_a_positive_ratio(self, rules_file):
        for bad in (None, 0, -1):
            with pytest.raises(ValueError):
                calibration.add_rule("面拉", "scale", ratio=bad, gym="甲馆")

    def test_unknown_action_is_rejected(self, rules_file):
        for bad in ("删掉", "ignore"):        # ignore 2026-08-25 退役
            with pytest.raises(ValueError):
                calibration.add_rule("面拉", bad)


class TestIgnoreIsRetired:
    """`ignore` 2026-08-25 删掉了。

    它存在的两条理由 —— 腿举和哈克的历史「只记片重 vs 记片重+start」——
    在记录口径统一之后都变成了一条 `offset` 规则。而 `ignore` 是这张表里
    唯一会**藏数据**的动作，能不留就不留。

    但旧文件里的 `ignore` 行不能删（只追加），所以必须保证两件事：
    它不再生效，**而且这件事是说出来的**。一条看起来生效、实际不生效的规则，
    是这个项目最不能接受的那种错。
    """

    def test_old_ignore_rows_no_longer_take_effect(self, rules_file):
        rules_file.write_text(store.dumps({
            "id": "20260821-01", "movement": "面拉", "action": "ignore",
            "date_to": "2026-06-26"}) + "\n", encoding="utf-8")
        assert calibration.load_rules() == []

    def test_but_they_are_reported_not_silently_skipped(self, rules_file):
        rules_file.write_text(store.dumps({
            "id": "20260821-01", "movement": "面拉", "action": "ignore",
            "date_to": "2026-06-26"}) + "\n", encoding="utf-8")
        retired = calibration.retired_rows()
        assert [r["id"] for r in retired] == ["20260821-01"]

    def test_superseded_retired_rows_stay_quiet(self, rules_file):
        """已经被推翻的旧行不用再提 —— 那是噪音，不是遗漏。"""
        rules_file.write_text(
            store.dumps({"id": "A", "movement": "面拉", "action": "ignore"}) + "\n"
            + store.dumps({"id": "B", "movement": "面拉", "action": "scale",
                           "ratio": 0.5, "gym": "甲馆", "supersedes": "A"}) + "\n",
            encoding="utf-8")
        assert calibration.retired_rows() == []
        assert [r.id for r in calibration.load_rules()] == ["B"]


class TestDetection:
    def test_catches_the_pulley_case(self, rules_file):
        """要抓的形状：面拉 50kg → 25kg，总次数两次都是 30 —— 换了把尺。"""
        raw = [sess("2026-06-01", 50, 10), sess("2026-06-26", 25, 10)]
        jumps = calibration.detect_jumps(stats_of(raw))
        assert len(jumps) == 1
        assert jumps[0].movement == "面拉"
        assert jumps[0].pulley_suspect
        assert jumps[0].ratio == pytest.approx(0.5)

    def test_quiet_when_reps_moved_too(self, rules_file):
        """重量减半、次数翻倍是有意换次数区间，不是换尺。"""
        raw = [sess("2026-06-01", 50, 6), sess("2026-06-26", 25, 18)]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_quiet_for_small_absolute_loads(self, rules_file):
        """2.5kg → 10kg 的飞鸟是 ×4，但那只是换了两档哑铃。"""
        raw = [sess("2026-03-18", 2.5, 10, name="俯身飞鸟"),
               sess("2026-03-25", 10, 10, name="俯身飞鸟")]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_quiet_across_a_long_gap(self, rules_file):
        """隔一个多月翻倍是进步，隔十天翻倍才是换尺。"""
        raw = [sess("2026-03-10", 15, 10, name="哑铃直腿硬拉"),
               sess("2026-04-20", 30, 10, name="哑铃直腿硬拉")]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_normal_progression_is_quiet(self, rules_file):
        raw = [sess("2026-06-01", 50, 10), sess("2026-06-08", 55, 10)]
        assert calibration.detect_jumps(stats_of(raw)) == []

    def test_resolved_jumps_drop_out_of_unresolved(self, rules_file):
        raw = [sess("2026-06-01", 50, 10), sess("2026-06-26", 25, 10)]
        st = stats_of(raw)
        assert len(calibration.unresolved(st)) == 1
        calibration.add_rule("面拉", "confirm", date="2026-06-26")
        assert calibration.unresolved(stats_of(raw)) == []

    def test_confirm_changes_no_number(self, rules_file):
        """「我看过了，这是真的」只该改预警状态，不该碰任何数据。"""
        raw = [sess("2026-06-26", 25, 10)]
        plain = stats_of(raw)[0].movements[0].top_load_kg
        calibration.add_rule("面拉", "confirm", date="2026-06-26")
        assert stats_of(raw)[0].movements[0].top_load_kg == plain


class TestExplanation:
    def test_pulley_case_teaches_how_to_measure(self):
        text = " ".join(calibration.explanation(pulley=True))
        assert "传动比" in text and "÷" in text
        assert "load-measurement.md" in text

    def test_non_pulley_case_omits_the_pulley_lecture(self):
        text = " ".join(calibration.explanation(pulley=False))
        assert "传动比" not in text
        assert "load-measurement.md" in text


class TestResolutionDoesNotLeakForward:
    def test_a_rule_on_one_date_does_not_silence_the_next_jump(self, rules_file):
        """缺陷：`covers(后一天) or covers(前一天)` 会让给 D 写的规则
        连带把 (D → 下一次) 那个跳变也标成已处理。

        用户确认了一次真实涨幅，下个月换机位造成的假涨幅就再也不会预警。
        """
        raw = [sess("2026-06-01", 50, 10),
               sess("2026-06-26", 25, 10),
               sess("2026-07-11", 50, 10)]
        calibration.add_rule("面拉", "confirm", date="2026-06-26")
        st = stats_of(raw)
        pending = calibration.unresolved(st)
        assert [j.date for j in pending] == ["2026-07-11"], \
            "给 08-10 写的规则把 08-25 那个新跳变也吞掉了"

    def test_confirming_the_wrong_day_resolves_nothing(self, rules_file):
        """confirm 标的是「哪一次」，不是「哪一段」。标错日子就该照旧预警。"""
        raw = [sess("2026-06-01", 50, 10), sess("2026-06-26", 25, 10)]
        calibration.add_rule("面拉", "confirm", date="2026-06-01")
        assert [j.date for j in calibration.unresolved(stats_of(raw))] == ["2026-06-26"]


class TestRuleValidation:
    def test_empty_movement_is_refused(self, rules_file):
        """文件只追加 —— 一条永远匹配不上的规则会永久留在那儿。"""
        for bad in (None, "", "   "):
            with pytest.raises(ValueError):
                calibration.add_rule(bad, "confirm", date="2026-07-16")
        assert not rules_file.exists() or rules_file.read_text(encoding="utf-8") == ""

    def test_malformed_dates_are_refused(self, rules_file):
        with pytest.raises(ValueError):
            calibration.add_rule("面拉", "confirm", date="2026/07/16")

    def test_confirm_needs_a_date_and_no_gym(self, rules_file):
        with pytest.raises(ValueError):
            calibration.add_rule("面拉", "confirm")
        with pytest.raises(ValueError):
            calibration.add_rule("面拉", "confirm", date="2026-07-16", gym="甲馆")


class TestGymScope:
    """作用域挂在**馆**上，不挂日期。

    传动比是那台机器的物理属性 —— 艾克仕的龙门今天 2:1、明天还是 2:1。
    用日期区间去表达「在哪台机器上练的」，每换一次馆就要补一条规则，
    补漏一条就静默错一次。用户 2026-08-23 拍板改用这个作用域。
    """

    @staticmethod
    def _sess(date: str, gym: str | None, weight: float) -> dict:
        s = sess(date, weight, 10, name="面拉")
        if gym:
            s["gym"] = gym
        else:
            s.pop("gym", None)      # 没标过场地 —— 缺字段和空字符串是两回事
        return s

    def test_rule_applies_only_at_that_gym(self, rules_file):
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="艾克仕")
        out = calibration.apply_rules([self._sess("2026-08-16", "艾克仕", 40),
                                       self._sess("2026-08-10", "BA", 40)])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 20
        assert out[1]["movements"][0]["sets"][0]["weight_kg"] == 40

    def test_unknown_gym_is_never_assumed_to_match(self, rules_file):
        """没标场地的历史记录，绝不能被一条后来才定义的按馆规则悄悄改掉。"""
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="艾克仕")
        out = calibration.apply_rules([self._sess("2026-06-01", None, 40)])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 40
        assert "_calib" not in out[0]["movements"][0]

    def test_scope_survives_a_round_trip_through_the_file(self, rules_file):
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="艾克仕")
        loaded = calibration.load_rules()[0]
        assert loaded.gym == "艾克仕"
        assert loaded.covers("面拉", "艾克仕") is True
        assert loaded.covers("面拉", "BA") is False
        assert loaded.covers("面拉", None) is False

    def test_identity_ratio_is_still_a_real_rule(self, rules_file):
        """×1.0 不改数字，但它记录了「测过了，就是 1:1」——
        和「还没人测过」是两回事，不该被静默当成同一件事。"""
        calibration.add_rule("面拉", "scale", ratio=1.0, gym="BA")
        out = calibration.apply_rules([self._sess("2026-08-10", "BA", 40)])
        m = out[0]["movements"][0]
        assert m["sets"][0]["weight_kg"] == 40
        assert m["_calib"]["ratio"] == 1.0


class TestOffset:
    """加法常数，和传动比那个乘法常数是两回事。

    这正是 2026-08-21 那次没法用 ratio 修腿举历史的原因：同一天各组片重不同，
    一个系数对不上任何一组。

    2026-08-25 起 offset **可以是负的** —— 引体挂 10kg 配重是 +10，
    套助力带减 15kg 是 -15。同一个动作名，两种做法，一正一负，
    不用把它拆成两条曲线（那正是用户 2026-08-10 否掉的方案）。
    """

    @staticmethod
    def _sess(date: str, gym: str, weight: float, name: str = "腿举") -> dict:
        return sess(date, weight, 10, name=name, group="腿", gym=gym)

    def test_adds_the_sled_weight(self, rules_file):
        calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA")
        out = calibration.apply_rules([self._sess("2026-08-20", "BA", 100)])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 153

    def test_is_additive_not_multiplicative(self, rules_file):
        """不同片重加同一个常数 —— 这是 ratio 做不到的那件事。"""
        calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA")
        out = calibration.apply_rules([self._sess("2026-08-20", "BA", 100),
                                       self._sess("2026-08-14", "BA", 60)])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 153
        assert out[1]["movements"][0]["sets"][0]["weight_kg"] == 113

    def test_assistance_is_a_negative_offset(self, rules_file):
        """助力带引体：手上实际要出的力比体重少。"""
        calibration.add_rule("引体向上", "offset", offset_kg=-15, gym="BA")
        out = calibration.apply_rules(
            [self._sess("2026-08-20", "BA", 75, name="引体向上")])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 60

    def test_zero_is_refused(self, rules_file):
        """0 不改任何数字，却会让人以为这个动作已经标定过了。"""
        for bad in (None, 0):
            with pytest.raises(ValueError):
                calibration.add_rule("腿举", "offset", offset_kg=bad, gym="BA")

    def test_reads_the_old_start_kg_field(self, rules_file):
        """`start_kg` 是 2026-08-25 之前的字段名，旧文件必须照常生效。"""
        rules_file.write_text(store.dumps({
            "id": "X", "movement": "腿举", "action": "offset",
            "gym": "BA", "start_kg": 53}) + "\n", encoding="utf-8")
        out = calibration.apply_rules([self._sess("2026-08-20", "BA", 100)])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 153

    def test_only_at_that_gym(self, rules_file):
        calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA")
        out = calibration.apply_rules([self._sess("2026-08-20", "乙馆", 100)])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 100

    def test_raw_file_still_untouched(self, rules_file):
        calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA")
        raw = [self._sess("2026-08-20", "BA", 100)]
        before = copy.deepcopy(raw)
        calibration.apply_rules(raw)
        assert raw == before

    def test_scaling_happens_before_adding(self, rules_file):
        """顺序定死：传动比作用在配重片读数上，自重是读数之外另加的一块。"""
        calibration.add_rule("腿举", "scale", ratio=0.5, gym="BA")
        calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA")
        out = calibration.apply_rules([self._sess("2026-08-20", "BA", 100)])
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 103


class TestGymIsAttachedBeforeCalibration:
    """按馆的规则靠 `session["gym"]` 匹配 —— 场地必须先挂上。

    反过来的顺序会让每一条按馆的规则永远匹配不到，而且是**静默**的：
    没有报错，只是折算没发生。
    """

    def test_load_sessions_applies_gym_then_calibration(self, rules_file, tmp_path,
                                                        monkeypatch):
        from health_assistant import gyms, store as _store
        monkeypatch.setattr(_store, "TRAINING_DIR", tmp_path)
        monkeypatch.setattr(gyms, "PATH", tmp_path.parent / "gyms.jsonl")
        gyms._index.cache_clear()
        (tmp_path / "2026").mkdir()
        (tmp_path / "2026" / "2026-08.jsonl").write_text(
            _store.dumps(sess("2026-08-16", 40, 10, name="面拉")) + "\n", encoding="utf-8")
        gyms.set_gym("2026-08-16", "艾克仕")
        calibration.add_rule("面拉", "scale", ratio=0.5, gym="艾克仕")

        out = _store.load_sessions()
        gyms._index.cache_clear()
        assert out[0]["gym"] == "艾克仕"
        assert out[0]["movements"][0]["sets"][0]["weight_kg"] == 20


class TestSupersedesMany:
    """一条新规则可以同时推翻好几条旧的。

    这不是花哨功能：2026-08-25 那条「BA 腿举 +53」同时取代了「腿举的 ignore
    起算线」和更早那版 offset。逼人一条条拆开写，结果是人干脆不写，
    旧规则就永远挂在那儿 —— 而这张表里最危险的东西正是「看起来生效的死规则」。
    """

    def test_one_rule_can_retire_two(self, rules_file):
        a = calibration.add_rule("腿举", "offset", offset_kg=50, gym="BA")
        rules_file.write_text(
            rules_file.read_text(encoding="utf-8")
            + store.dumps({"id": "OLD", "movement": "腿举", "action": "ignore"}) + "\n",
            encoding="utf-8")
        calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA",
                             supersedes=f"{a.id},OLD")
        assert [r.offset_kg for r in calibration.load_rules()] == [53]
        assert calibration.retired_rows() == []          # 旧 ignore 不再报

    def test_single_id_still_works(self, rules_file):
        a = calibration.add_rule("腿举", "offset", offset_kg=50, gym="BA")
        calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA", supersedes=a.id)
        assert [r.offset_kg for r in calibration.load_rules()] == [53]

    def test_survives_a_round_trip_through_the_file(self, rules_file):
        a = calibration.add_rule("腿举", "offset", offset_kg=50, gym="BA")
        b = calibration.add_rule("腿举", "offset", offset_kg=53, gym="BA",
                                 supersedes=[a.id, "OLD"])
        loaded = calibration.load_rules()[0]
        assert loaded.id == b.id
        assert loaded.supersedes == (a.id, "OLD")


class TestCommentRow:
    """jsonl 没有注释语法，所以文件头上放一行 `{"_comment": [...]}`。

    写规则的人打开文件第一眼就该看见怎么填 —— 把用法藏在文档里，
    等于赌他会去翻文档。靠「action 不在白名单里就跳过」被忽略。
    """

    def test_comment_row_is_ignored(self, rules_file):
        rules_file.write_text(
            store.dumps({"_comment": ["怎么填", "hc calib set … --ratio 0.5"]}) + "\n"
            + store.dumps({"id": "A", "movement": "面拉", "action": "scale",
                           "ratio": 0.5, "gym": "甲馆"}) + "\n",
            encoding="utf-8")
        assert [r.id for r in calibration.load_rules()] == ["A"]
        assert calibration.retired_rows() == []      # 注释不是「失效的规则」

    def test_rules_can_still_be_appended_after_it(self, rules_file):
        rules_file.write_text(
            store.dumps({"_comment": ["怎么填"]}) + "\n", encoding="utf-8")
        r = calibration.add_rule("面拉", "scale", ratio=0.5, gym="甲馆")
        assert [x.id for x in calibration.load_rules()] == [r.id]
        assert "_comment" in rules_file.read_text(encoding="utf-8")
