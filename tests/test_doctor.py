"""skill 软链体检的回归测试。

为什么这一小块值得单独测：断链的**症状出现在别人家里** ——
agent 那边报「Unknown skill: health-coach」，跟这个仓库看不出任何关系。
2026-08-11 就是这么坏的：项目从 health-assistant 改名成 selfcoach，
install.sh 铺的绝对软链全部指瞎，每次启动都报错，而 hc doctor 一片绿。

所以这里盯五件事：

1. 死链要报 ❌，并且说清怎么修
2. 绝对路径软链**还没坏也要警告** —— 它是下一次改名的定时炸弹
3. 缺链、孤儿链和死链一样要查得到，而且不许打绿勾
4. 宿主目录在、却一个软链都没有，要报 ❌（install.sh 那头可能整段失败了）
5. 宿主目录**不存在**不算问题（手记模式和 hc 命令都不依赖 skill）
"""

from __future__ import annotations

import pytest

from health_assistant import doctor


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """一个只有 skills/ 的假仓库。宿主目录按需在各条用例里建。"""
    (tmp_path / "skills" / "health-coach").mkdir(parents=True)
    (tmp_path / "skills" / "workout-log").mkdir(parents=True)
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    return {"root": tmp_path, "host": tmp_path / ".claude" / "skills"}


def _link(host, name, target):
    host.mkdir(parents=True, exist_ok=True)
    (host / name).symlink_to(target)


class TestSkillLinks:
    def test_silent_when_the_host_dir_does_not_exist(self, repo):
        """没链过不是问题 —— 不该在体检里制造噪音。"""
        assert doctor._check_skill_links() == []

    def test_relative_links_report_ok(self, repo):
        for name in ("health-coach", "workout-log"):
            _link(repo["host"], name, f"../../skills/{name}")
        out = doctor._check_skill_links()
        assert len(out) == 1
        assert "✅" in out[0] and "2/2" in out[0]

    def test_broken_link_is_reported_with_the_fix(self, repo):
        _link(repo["host"], "health-coach", "../../skills/health-coach")
        _link(repo["host"], "workout-log", "/nowhere/skills/workout-log")
        out = "\n".join(doctor._check_skill_links())
        assert "❌" in out
        assert "workout-log" in out
        assert "install.sh" in out, "报了故障却不说怎么修，等于没报"

    def test_absolute_links_warn_before_they_break(self, repo):
        """还能用，但项目一改名就断。这种「将坏未坏」必须说出来。"""
        for name in ("health-coach", "workout-log"):
            _link(repo["host"], name, str(repo["root"] / "skills" / name))
        out = "\n".join(doctor._check_skill_links())
        assert "❌" not in out, "没坏就不该打红叉"
        assert "绝对路径" in out and "install.sh" in out

    def test_a_real_directory_is_not_mistaken_for_a_link(self, repo):
        """有人手动拷了一份进去 —— 那不是软链，不该被误报成死链。

        计数按软链算，所以这里是 1/2 而不是 2/2：拷贝那一份不归这里管，
        但「有一个没走软链」本身值得让用户看见。
        """
        _link(repo["host"], "workout-log", "../../skills/workout-log")
        (repo["host"] / "health-coach").mkdir()
        out = "\n".join(doctor._check_skill_links())
        assert "❌" not in out, "拷贝进去的目录不是死链"
        assert "1/2" in out

    # ── 评审修复：孤儿链 / 缺链 / 零链 ────────────────────────────────

    def test_an_orphan_dead_link_left_by_a_rename_is_found(self, repo):
        """缺陷：只遍历 skills/ 里现存的名字，改名留下的旧链永远碰不到。

        skills/xunji-sync 改名成 sync-xunji 后，宿主里那条旧链指着已经不存在的
        目录 —— agent 读不到，而 doctor 却打「全部已链接」。
        """
        (repo["root"] / "skills" / "sync-xunji").mkdir()
        for name in ("health-coach", "workout-log", "sync-xunji"):
            _link(repo["host"], name, f"../../skills/{name}")
        _link(repo["host"], "xunji-sync", "../../skills/xunji-sync")  # 改名前的残留
        out = "\n".join(doctor._check_skill_links())
        assert "❌" in out and "xunji-sync" in out
        assert "✅" not in out, "有一条死链却打绿勾"

    def test_a_live_orphan_link_is_reported(self, repo):
        """指向别处的活链：还能读到，但已经不是这个项目在维护的。"""
        elsewhere = repo["root"] / "elsewhere"
        elsewhere.mkdir()
        for name in ("health-coach", "workout-log"):
            _link(repo["host"], name, f"../../skills/{name}")
        _link(repo["host"], "other-skill", str(elsewhere))
        out = "\n".join(doctor._check_skill_links())
        assert "other-skill" in out
        assert "✅" not in out, "多出一条来路不明的链，不该打绿勾"

    def test_partial_linking_does_not_get_a_green_check(self, repo):
        """缺链和死链一样致命：agent 那边同样是「Unknown skill」。"""
        _link(repo["host"], "health-coach", "../../skills/health-coach")
        out = "\n".join(doctor._check_skill_links())
        assert "✅" not in out
        assert "1/2" in out and "workout-log" in out
        assert "install.sh" in out

    def test_an_empty_host_dir_is_a_red_flag(self, repo):
        """install.sh 建好目录后七次 ln 全失败（不支持软链的卷）就是这个样子。

        以前这里 `if not linked: continue` 完全沉默，和 install.sh 那边被
        `return 0` 抹掉的失败信号叠在一起 —— 两道网同时漏。
        """
        repo["host"].mkdir(parents=True)
        out = "\n".join(doctor._check_skill_links())
        assert "❌" in out and "0/2" in out
        assert "软链" in out, "不说可能的原因，用户没法自己往下走"

    def test_a_missing_link_shows_up_in_the_todo_list(self, repo, monkeypatch):
        """报告里写一行还不够 —— 不进 todo 就会被后面的「一切正常」盖过去。"""
        _link(repo["host"], "health-coach", "../../skills/health-coach")
        lines = doctor._check_skill_links()
        assert any("install.sh" in ln for ln in lines), \
            "check() 靠这个关键词把缺链推进 todo"
