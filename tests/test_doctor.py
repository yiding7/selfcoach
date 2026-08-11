"""skill 软链体检的回归测试。

为什么这一小块值得单独测：断链的**症状出现在别人家里** ——
agent 那边报「Unknown skill: health-coach」，跟这个仓库看不出任何关系。
2026-08-11 就是这么坏的：项目从 health-assistant 改名成 selfcoach，
install.sh 铺的绝对软链全部指瞎，每次启动都报错，而 hc doctor 一片绿。

所以这里盯三件事：

1. 死链要报 ❌，并且说清「跑 ./install.sh」
2. 绝对路径软链**还没坏也要警告** —— 它是下一次改名的定时炸弹
3. 没链过不算问题（手记模式和 hc 命令都不依赖 skill）
"""

from __future__ import annotations

import pytest

from health_assistant import doctor


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """一个只有 skills/ 和 .claude/skills/ 的假仓库。"""
    (tmp_path / "skills" / "health-coach").mkdir(parents=True)
    (tmp_path / "skills" / "workout-log").mkdir(parents=True)
    host = tmp_path / ".claude" / "skills"
    host.mkdir(parents=True)
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    return {"root": tmp_path, "host": host}


def _link(host, name, target):
    (host / name).symlink_to(target)


class TestSkillLinks:
    def test_silent_when_nothing_was_ever_linked(self, repo):
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
        (repo["host"] / "health-coach").mkdir()
        _link(repo["host"], "workout-log", "../../skills/workout-log")
        out = "\n".join(doctor._check_skill_links())
        assert "❌" not in out, "拷贝进去的目录不是死链"
        assert "1/2" in out
