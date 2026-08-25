"""训练场地（`hc gym`）的回归测试。

这个功能存在的理由只有一句：**换馆等于换尺**。所以测试盯的是四件事：

1. **没标注时，行为和加这个功能之前一模一样。** 场地是可选的补充事实，
   不是新的必填项 —— 一个让历史数据全部降级的"改进"不是改进
2. `None` 是「不知道」，**不是「同一个馆」**。只有两边都标了且不同才算换馆
3. 换馆时**只降器械类**。杠铃 64kg 在哪个馆都是 64kg，把它一起埋掉
   是另一种错，而且表现为「什么都没说」，比报错难发现
4. 存放位置不能污染 `store.load_sessions()` —— 第一版放进 `data/training/`
   就变成了四次「0 个动作」的幽灵训练

夹具里的重量、次数、日期**全是编的，且一律取整**，纪律见 `CLAUDE.md`。
"""

from __future__ import annotations

import pytest

from health_assistant import gyms, store
from health_assistant.analytics.compare import compare_group
from health_assistant.analytics.metrics import session_stats
from health_assistant.analytics.progress import movement_progress


@pytest.fixture()
def gym_file(tmp_path, monkeypatch):
    monkeypatch.setattr(gyms, "PATH", tmp_path / "gyms.jsonl")
    gyms._index.cache_clear()
    yield tmp_path / "gyms.jsonl"
    gyms._index.cache_clear()


def sess(date: str, movements: list[tuple[str, float, float]], *, group: str = "腿") -> dict:
    """movements = [(动作名, 重量kg, 次数)]，每个动作固定 3 组。"""
    return {
        "id": f"{date}-s", "date": date, "source": "x", "title": "", "duration_s": 3000,
        "movements": [{
            "name": name, "raw_type": group, "exetype": None,
            "unilateral": False, "difficulty": None,
            "sets": [{"done": True, "weight_kg": w, "reps": r, "rpe": None,
                      "self_weight": False, "left_weight_kg": None} for _ in range(3)],
        } for name, w, r in movements],
    }


def stats_of(sessions):
    return [session_stats(s, 80.0) for s in gyms.apply_to(sessions)]


# ── 1. 场地依赖性分类 ───────────────────────────────────────────────────

class TestSiteDependence:
    @pytest.mark.parametrize("name", [
        "杠铃深蹲", "哑铃卧推", "杠铃罗马尼亚硬拉", "俯卧撑", "侧平举", "俯身飞鸟",
    ])
    def test_free_weights_travel(self, name):
        assert gyms.site_dependence(name).portable is True

    @pytest.mark.parametrize("name", [
        "哈克机深蹲", "腿举", "器械倒蹬", "直杆绳索下压", "面拉",
        "把手式蝴蝶机飞鸟", "史密斯机深蹲", "悍马机划船", "宽距高位下拉",
        "坐姿腿屈伸", "引体向上（辅助）",
    ])
    def test_machines_do_not(self, name):
        assert gyms.site_dependence(name).portable is False

    def test_machine_keywords_win_over_movement_keywords(self):
        """`史密斯机深蹲` 里既有「史密斯」也有「深蹲」—— 器械必须排在前面。"""
        assert gyms.site_dependence("史密斯机深蹲").matched == "史密斯"
        assert gyms.site_dependence("哈克机深蹲").matched == "哈克"

    def test_butterfly_machine_does_not_swallow_bodyweight_ab_work(self):
        """关键词是「蝴蝶机」不是「蝴蝶」—— 平板蝴蝶收腹是自重动作。"""
        assert gyms.site_dependence("平板蝴蝶收腹").portable is True
        assert gyms.site_dependence("把手式蝴蝶机飞鸟").portable is False

    def test_unknown_falls_back_to_conservative(self):
        d = gyms.site_dependence("某种没见过的动作")
        assert d.portable is False and d.is_default is True

    def test_table_itself_is_healthy(self):
        assert gyms.warnings() == []


# ── 2. 存储 ────────────────────────────────────────────────────────────

class TestStorage:
    def test_unset_date_is_none_not_empty_string(self, gym_file):
        assert gyms.gym_of("2026-08-20") is None

    def test_last_write_wins(self, gym_file):
        gyms.set_gym("2026-08-20", "甲馆")
        gyms.set_gym("2026-08-20", "乙馆")
        assert gyms.gym_of("2026-08-20") == "乙馆"

    def test_history_is_append_only(self, gym_file):
        gyms.set_gym("2026-08-20", "甲馆")
        gyms.set_gym("2026-08-20", "乙馆")
        assert len(store.read_jsonl(gym_file)) == 2

    def test_empty_value_clears_the_tag(self, gym_file):
        gyms.set_gym("2026-08-20", "甲馆")
        gyms.set_gym("2026-08-20", "")
        assert gyms.gym_of("2026-08-20") is None

    def test_bad_date_is_refused(self, gym_file):
        with pytest.raises(ValueError):
            gyms.set_gym("08/20/2026", "甲馆")

    def test_set_many_skips_unchanged(self, gym_file):
        gyms.set_gym("2026-08-20", "甲馆")
        n = gyms.set_many([("2026-08-20", "甲馆", ""), ("2026-08-21", "乙馆", "")])
        assert n == 1

    def test_apply_to_does_not_mutate_input(self, gym_file):
        gyms.set_gym("2026-08-20", "甲馆")
        raw = [sess("2026-08-20", [("杠铃深蹲", 60, 10)])]
        gyms.apply_to(raw)
        assert "gym" not in raw[0]


# ── 3. 存放位置 ─────────────────────────────────────────────────────────

class TestFileLocation:
    def test_not_under_training_dir(self):
        """`load_sessions()` 会 rglob 整个 data/training/ —— 场地文件掉进去
        就会被当成训练记录读出来，变成「0 个动作」的幽灵训练。"""
        assert store.TRAINING_DIR not in gyms.PATH.parents

    def test_load_sessions_ignores_rows_without_movements(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "TRAINING_DIR", tmp_path)
        (tmp_path / "2026").mkdir()
        (tmp_path / "2026" / "2026-08.jsonl").write_text(
            store.dumps(sess("2026-08-20", [("杠铃深蹲", 60, 10)])) + "\n", encoding="utf-8")
        (tmp_path / "intruder.jsonl").write_text(
            store.dumps({"date": "2026-08-20", "gym": "甲馆"}) + "\n", encoding="utf-8")
        assert len(store.load_sessions()) == 1


# ── 4. 对比行为 ─────────────────────────────────────────────────────────

BOTH = [("杠铃深蹲", 60, 10), ("哈克机深蹲", 100, 10)]


class TestComparison:
    def test_no_tags_behaves_exactly_as_before(self, gym_file):
        prev, cur = stats_of([sess("2026-08-14", BOTH), sess("2026-08-20", BOTH)])
        c = compare_group(cur, [prev], "腿")
        assert c.gym_changed is False
        assert c.site_incomparable == []
        assert c.top_load.before is not None

    def test_one_side_tagged_is_not_a_gym_change(self, gym_file):
        """`None` 是「不知道」，不是「同一个馆」，更不是「换馆」。"""
        gyms.set_gym("2026-08-20", "甲馆")
        prev, cur = stats_of([sess("2026-08-14", BOTH), sess("2026-08-20", BOTH)])
        c = compare_group(cur, [prev], "腿")
        assert c.gym_changed is False
        assert c.site_incomparable == []

    def test_same_gym_compares_everything(self, gym_file):
        gyms.set_gym("2026-08-14", "甲馆")
        gyms.set_gym("2026-08-20", "甲馆")
        prev, cur = stats_of([sess("2026-08-14", BOTH), sess("2026-08-20", BOTH)])
        c = compare_group(cur, [prev], "腿")
        assert c.gym_changed is False
        assert c.site_incomparable == []

    def test_gym_change_blinds_machines_only(self, gym_file):
        gyms.set_gym("2026-08-14", "甲馆")
        gyms.set_gym("2026-08-20", "乙馆")
        prev, cur = stats_of([sess("2026-08-14", BOTH), sess("2026-08-20", BOTH)])
        c = compare_group(cur, [prev], "腿")

        assert c.gym_changed is True
        assert c.site_incomparable == ["哈克机深蹲"]

        by_name = {m.name: m for m in c.movements}
        squat, hack = by_name["杠铃深蹲"], by_name["哈克机深蹲"]
        # 自由重量照常比 —— 这条是这个设计的全部意义
        assert squat.site_incomparable is False
        assert squat.top_load.before == 60 and squat.top_load.after == 60
        # 器械的负荷两端置空，不是「留着数字再加一句提醒」
        assert hack.site_incomparable is True
        assert hack.top_load.before is None and hack.top_load.after is None
        assert hack.e1rm.before is None and hack.e1rm.after is None
        # 但组数、次数、容量照常计入 —— 那些组是真的练了
        assert hack.sets.after == 3
        assert hack.volume.before is not None and hack.volume.after is not None

    def test_group_level_peak_excludes_the_machine(self, gym_file):
        """逐动作那层挡住了、汇总层漏过去，就是另一个静默失效。"""
        gyms.set_gym("2026-08-14", "甲馆")
        gyms.set_gym("2026-08-20", "乙馆")
        prev, cur = stats_of([sess("2026-08-14", BOTH), sess("2026-08-20", BOTH)])
        c = compare_group(cur, [prev], "腿")
        # 哈克的 100kg 不该出现在顶组汇总里，只剩深蹲的 60kg
        assert c.top_load.after == 60

    def test_all_machines_means_loads_not_comparable(self, gym_file):
        gyms.set_gym("2026-08-14", "甲馆")
        gyms.set_gym("2026-08-20", "乙馆")
        only_machine = [("哈克机深蹲", 100, 10)]
        prev, cur = stats_of([sess("2026-08-14", only_machine),
                              sess("2026-08-20", only_machine)])
        c = compare_group(cur, [prev], "腿")
        assert c.loads_comparable is False


class TestLongitudinalView:
    """`hc compare` 的第二层。2026-08-21 之前这一层完全不读口径标记 ——
    「本次 vs 上次」正确剔除了腿举，这里却照报 ↑+53%。"""

    def test_gym_change_blinds_machines_here_too(self, gym_file):
        gyms.set_gym("2026-08-14", "甲馆")
        gyms.set_gym("2026-08-20", "乙馆")
        prev, cur = stats_of([sess("2026-08-14", BOTH), sess("2026-08-20", BOTH)])
        by_name = {p.name: p for p in movement_progress(cur, [prev])}

        assert by_name["杠铃深蹲"].site_incomparable is False
        assert by_name["杠铃深蹲"].top_load.after == 60

        hack = by_name["哈克机深蹲"]
        assert hack.site_incomparable is True
        assert hack.top_load.after is None and hack.e1rm.after is None
        assert hack.loads_comparable is False
        # 次数照常比
        assert hack.reps.after == 30


class TestSameGymFallback:
    """用户 2026-08-23：「能比较的还是尽可能去比较，策略太保守导致经常得不出结果」。

    所以器械动作碰上换馆时，先往前翻找同馆的那一次，翻不到才认输。
    代价是「上一次」不再是字面上的上一次 —— 那就必须把跳过了谁说出来。
    """

    def test_reaches_further_back_for_the_same_gym(self, gym_file):
        gyms.set_gym("2026-08-01", "甲馆")     # 同馆，更早
        gyms.set_gym("2026-08-14", "乙馆")     # 别的馆，更近
        gyms.set_gym("2026-08-20", "甲馆")     # 本次
        old, recent, cur = stats_of([
            sess("2026-08-01", [("哈克机深蹲", 90, 10)]),
            sess("2026-08-14", [("哈克机深蹲", 100, 10)]),
            sess("2026-08-20", [("哈克机深蹲", 95, 10)]),
        ])
        hack = movement_progress(cur, [old, recent])[0]

        assert hack.last_date == "2026-08-01"        # 跳过了 08-14
        assert hack.site_incomparable is False       # 同馆，真的能比
        assert hack.top_load.before == 90 and hack.top_load.after == 95
        # 跳过谁必须留痕，否则「上一次」这三个字就是在骗人
        assert hack.skipped_date == "2026-08-14"
        assert hack.skipped_gym == "乙馆"

    def test_free_weights_never_reach_back(self, gym_file):
        """杠铃在哪个馆都一样，没有任何理由跳过更近的那一次。"""
        gyms.set_gym("2026-08-01", "甲馆")
        gyms.set_gym("2026-08-14", "乙馆")
        gyms.set_gym("2026-08-20", "甲馆")
        old, recent, cur = stats_of([
            sess("2026-08-01", [("杠铃深蹲", 50, 10)]),
            sess("2026-08-14", [("杠铃深蹲", 60, 10)]),
            sess("2026-08-20", [("杠铃深蹲", 64, 10)]),
        ])
        squat = movement_progress(cur, [old, recent])[0]
        assert squat.last_date == "2026-08-14"
        assert squat.skipped_date is None

    def test_falls_back_to_blanking_when_no_same_gym_history(self, gym_file):
        gyms.set_gym("2026-08-14", "乙馆")
        gyms.set_gym("2026-08-20", "甲馆")
        prev, cur = stats_of([sess("2026-08-14", [("哈克机深蹲", 100, 10)]),
                              sess("2026-08-20", [("哈克机深蹲", 95, 10)])])
        hack = movement_progress(cur, [prev])[0]
        assert hack.site_incomparable is True
        assert hack.top_load.after is None
        assert hack.skipped_date is None



# ── 6. 换馆已经解释了跳变，不用再问一遍 ──────────────────────────────────

class TestJumpDetectionIsGymAware:
    """`hc calib` 的预警在有场地之后应该安静得多。

    实测：6 条命中里有 5 条是「换了个馆的绳索动作」—— 那不是发现，那是必然。
    `hc compare` 那一侧早就把这类对比的负荷置空了，预警再报一次只会把
    唯一那条真信号（同一个馆里重量腰斩）淹掉。
    """

    def _jumps(self, sessions):
        from health_assistant import calibration
        return calibration.detect_jumps(stats_of(sessions), rules=[])

    def test_quiet_when_the_gym_changed(self, gym_file):
        gyms.set_gym("2026-07-17", "乙馆")
        gyms.set_gym("2026-07-27", "甲馆")
        assert self._jumps([sess("2026-07-17", [("绳索臂屈伸", 50, 10)], group="三头"),
                            sess("2026-07-27", [("绳索臂屈伸", 16, 10)], group="三头")]) == []

    def test_still_reports_a_jump_inside_one_gym(self, gym_file):
        """同一个馆、同一台机器上重量腰斩 —— 这才是要问的那种。"""
        gyms.set_gym("2026-07-17", "甲馆")
        gyms.set_gym("2026-07-27", "甲馆")
        jumps = self._jumps([sess("2026-07-17", [("绳索臂屈伸", 50, 10)], group="三头"),
                             sess("2026-07-27", [("绳索臂屈伸", 16, 10)], group="三头")])
        assert [j.date for j in jumps] == ["2026-07-27"]

    def test_free_weights_are_still_compared_across_gyms(self, gym_file):
        """64kg 的杠铃在哪个馆都是 64kg —— 换馆解释不了杠铃的跳变。"""
        gyms.set_gym("2026-07-17", "乙馆")
        gyms.set_gym("2026-07-27", "甲馆")
        jumps = self._jumps([sess("2026-07-17", [("杠铃深蹲", 60, 10)]),
                             sess("2026-07-27", [("杠铃深蹲", 20, 10)])])
        assert [j.date for j in jumps] == ["2026-07-27"]

    def test_unknown_gym_is_not_treated_as_a_gym_change(self, gym_file):
        """没标场地 = 不知道。不知道时行为和加这个字段之前完全一样。"""
        jumps = self._jumps([sess("2026-07-17", [("绳索臂屈伸", 50, 10)], group="三头"),
                             sess("2026-07-27", [("绳索臂屈伸", 16, 10)], group="三头")])
        assert [j.date for j in jumps] == ["2026-07-27"]
