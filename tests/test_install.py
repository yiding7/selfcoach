"""install.sh 铺 skill 软链这一段的回归测试。

为什么这一小段值得单独测：**它的失败症状出现在别人家里。**
一个 skill 都没链上时，agent 那边报「Unknown skill: health-coach」，
跟安装脚本看不出任何关系；而安装脚本这边如果打完「下一步：…」就 exit 0，
用户根本不会回头怀疑安装。

所以这里只盯一件事：**没链上必须是硬失败**，成功和「已经是真实目录」两条
路径照旧不报错。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from health_assistant.config import ROOT

SKILLS = ("health-coach", "workout-log")


@pytest.fixture()
def fake_repo(tmp_path):
    """一个刚好能让 install.sh 跑完的最小仓库。"""
    for name in SKILLS:
        (tmp_path / "skills" / name).mkdir(parents=True)
    shutil.copy(ROOT / "install.sh", tmp_path / "install.sh")
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    hc = scripts / "hc"
    hc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")   # doctor 不在测试范围内
    hc.chmod(0o755)
    return tmp_path


def _run(repo: Path, *, ln_fails: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if ln_fails:
        # 模拟不支持软链的卷（exFAT / SMB / 某些容器挂载）：ln 一律失败。
        # 这比真去找一个这样的文件系统靠谱，而且在 CI 里也跑得动。
        stub = repo / "stub-bin"
        stub.mkdir()
        fake_ln = stub / "ln"
        fake_ln.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_ln.chmod(0o755)
        env["PATH"] = f"{stub}{os.pathsep}{env['PATH']}"
    return subprocess.run(["bash", "install.sh", "auto"], cwd=repo, env=env,
                          capture_output=True, text=True)


class TestSkillLinking:
    def test_links_are_created_and_relative(self, fake_repo):
        proc = _run(fake_repo)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "链接了 2 个 skill" in proc.stdout
        for name in SKILLS:
            link = fake_repo / ".claude" / "skills" / name
            assert link.is_symlink() and link.exists()
            assert not Path(os.readlink(link)).is_absolute(), \
                "绝对软链项目一改名就全断，install.sh 只该铺相对的"

    def test_nothing_linked_is_a_hard_failure(self, fake_repo):
        """七次 ln 全失败却 exit 0，是这个脚本最糟的失败模式。"""
        proc = _run(fake_repo, ln_fails=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, f"一个都没链上却成功退出了：\n{out}"
        assert "❌" in out
        assert "软链" in out, "报了故障却不说可能的原因，用户没法自己往下走"
        assert "下一步" not in out, "失败了还打「下一步」，等于告诉用户装好了"

    def test_pre_existing_real_dirs_are_not_a_failure(self, fake_repo):
        """有人手工把 skills 拷进宿主目录了 —— 能用，只是不跟仓库更新。"""
        host = fake_repo / ".claude" / "skills"
        for name in SKILLS:
            (host / name).mkdir(parents=True)
        proc = _run(fake_repo)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "⚠️" in proc.stdout and "不会跟着仓库更新" in proc.stdout
        assert "❌" not in proc.stdout

    def test_a_dead_link_is_rebuilt_and_reported(self, fake_repo):
        host = fake_repo / ".claude" / "skills"
        host.mkdir(parents=True)
        (host / "health-coach").symlink_to("../../skills/nowhere")
        proc = _run(fake_repo)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "死链" in proc.stdout, "静默修好等于下次还会踩，用户永远不知道发生过什么"
        assert (host / "health-coach").exists()
